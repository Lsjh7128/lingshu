# -*- coding: utf-8 -*-
"""S1 执行器：探针扩容（48 条 / 3 剧本）+ 机制级消融（9 臂），引擎侧、零模型。

依据（已冻结，跑前不可改）：
    - `paper/supplement/PLAN.md` §1（S1），sha256 记录在 `paper/supplement/FREEZE.json`
    - 剧本与探针库由 `tools/gen_s1_script.py` 生成并冻结在
      `paper/ablation/script-freeze-s1.json`

设计：
    scripts = sA(p0) / sB(p1) / sC(p2)   三个 100 轮剧本，各 16 探针（A/B/C/D 各 4）
    arms    = Baseline / Basic / Full / Full−ACT-R / Full−anchor / Full−gate /
              Full−variety / Full−ushape / Full−integral （减一臂用 harness 开关，
              **不改 engine/**）
    统计    = 每类 12 对精确符号检验，Bonferroni α = 0.05/4 = 0.0125；
              每剧本点估计 + 跨剧本极差；不做"重复取均值"（引擎确定性）

用法：
    python tools/run_s1.py [--out paper/ablation/results/abl-20261027-s1-engine.json]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

from engine import compose as CP                      # noqa: E402
from engine import config as CFGM                     # noqa: E402
from engine import control as CT                      # noqa: E402
from engine import emotion as EM                      # noqa: E402
from engine import memory as MEM                      # noqa: E402
from engine import metacog as MC                      # noqa: E402
from engine import pipeline as PIPE                   # noqa: E402
from engine import replay as RP                       # noqa: E402

import gen_ablation_script as G0                      # noqa: E402
import gen_s1_script as G1                            # noqa: E402
import run_ablation as RA                             # noqa: E402

SCHEMA = "lingshu/ablation-result/v1"
DESIGN_VERSION = "ablation-supplement-s1-v0.1"
WINDOW = 24000
ALPHA_BONF = 0.05 / 4.0        # 四类 → 0.0125（PLAN §1.2）
SIGN_MDD = 7                   # 2^-7 = 0.0078 <= 0.0125 < 2^-6（PLAN §1.2 的 MDD）
CLASSES = ("A", "B", "C", "D")
SCRIPT_KEYS = ("sA", "sB", "sC")

# ── 9 臂（PLAN §1.2 表格逐字） ─────────────────────────────
FULL_FEATURES = {"u_shape": True, "phase": True, "anchor": True,
                 "variety": True, "gate": True, "integral": True}

ARMS = (
    {"id": "baseline", "label": "Baseline (sliding window W=3)", "kind": "baseline"},
    {"id": "basic", "label": "Basic (layered memory, no control plane)", "kind": "basic"},
    {"id": "full", "label": "Full", "kind": "full"},
    {"id": "full_minus_actr", "label": "Full −ACT-R (actrWeight=0)", "kind": "full",
     "cfg": {"memory.actrWeight": 0.0}},
    {"id": "full_minus_anchor", "label": "Full −anchor", "kind": "full",
     "features": {"anchor": False}},
    {"id": "full_minus_gate", "label": "Full −gate", "kind": "full",
     "features": {"gate": False}},
    {"id": "full_minus_variety", "label": "Full −variety", "kind": "full",
     "features": {"variety": False}},
    {"id": "full_minus_ushape", "label": "Full −ushape", "kind": "full",
     "features": {"u_shape": False}},
    {"id": "full_minus_integral", "label": "Full −integral", "kind": "full",
     "features": {"integral": False}},
)
ARM_IDS = tuple(a["id"] for a in ARMS)
MECH_ARMS = tuple(a["id"] for a in ARMS if a["id"].startswith("full_minus_"))


# ══════════════════════════════════════════════════════════
# 配置与特征
# ══════════════════════════════════════════════════════════

def arm_cfg(arm: dict) -> dict:
    cfg = CFGM.default()
    if arm["kind"] in ("baseline", "basic"):
        for key in ("boostFact", "boostOrganization", "boostStyle"):
            cfg = CFGM.apply_path(cfg, "compose.weights." + key, 0.0)
    if arm["kind"] == "basic":
        cfg = CFGM.apply_path(cfg, "memory.actrWeight", 0.0)
    for path, val in (arm.get("cfg") or {}).items():
        cfg = CFGM.apply_path(cfg, path, val)
    return cfg


def arm_features(arm: dict) -> dict:
    f = dict(FULL_FEATURES) if arm["kind"] == "full" else {}
    f.update(arm.get("features") or {})
    return f


def callset(arm: dict) -> dict:
    f = arm_features(arm)
    return {
        "recall": "sliding_window" if arm["kind"] == "baseline" else "memory.recall",
        "metacog": arm["kind"] != "baseline",
        "compose": "static" if arm["kind"] != "full" else "dynamic_weights",
        "u_shape": bool(f.get("u_shape")),
        "phase": bool(f.get("phase")),
        "anchor": bool(f.get("anchor")),
        "variety": bool(f.get("variety")),
        "integral": bool(f.get("integral")),
        "gate": bool(f.get("gate")),
        "actr": bool(arm["kind"] == "full" and (arm.get("cfg") or {}).get(
            "memory.actrWeight", 1.0) != 0.0),
        "baseline_window_turns": RA.BASELINE_W if arm["kind"] == "baseline" else None,
        "mechanism_disabled": [k for k, v in f.items() if not v] or None,
    }


def cfg_diff(arm: dict) -> dict:
    base = CFGM.default()
    d = CFGM.diff(base, arm_cfg(arm))
    return {k: [d[k][0], d[k][1]] for k in sorted(d)}


# ══════════════════════════════════════════════════════════
# 跑一个臂 × 一个剧本
# ══════════════════════════════════════════════════════════

def run_arm(arm: dict, rows: list[dict]) -> dict:
    cfg = arm_cfg(arm)
    feats = arm_features(arm)
    now_last = RP.BASE_TS + (len(rows) - 1) * RP.TURN_STEP
    store = RA.new_store(now_last)
    setmap = RA.setting_turn_map(rows)
    turns, probes, nohit = [], [], []
    uce_uns, uce_tot = 0, 0
    cum = 0

    if arm["kind"] == "baseline":
        for i, row in enumerate(rows):
            cum += len(row["text"])
            out = RA.baseline_turn(cfg, rows, i, row, setmap)
            uce_tot += out["injected_items"]
            if out.get("probe_id"):
                probes.append(_probe_record(row, out, "baseline"))
            if row["block"] == "nohit":
                nohit.append({"turn": out["turn"], "confidence_level": None,
                              "expression": None, "phrase": None,
                              "disciplined": None, "ack_literal": None})
            turns.append({k: v for k, v in out.items() if not k.startswith("_")})
        return {"arm": arm["id"], "cfg": cfg, "turns": turns, "probes": probes,
                "nohit": nohit, "uce": {"unsupported_items": uce_uns,
                                        "total_items": uce_tot},
                "features": feats, "labels_supplied": False}

    ps = PIPE.PipelineSession(cfg, "s1-" + arm["id"])
    integral = (CT.IntegralBaseline(cfg)
                if feats.get("integral") else None)
    for i, row in enumerate(rows):
        now = RP.BASE_TS + i * RP.TURN_STEP
        cum += len(row["text"])
        G0.apply_writes(store, row, now, cfg)
        out = RA.lingshu_turn(ps, cfg, store, row, now, cum,
                              control=(arm["kind"] == "full"),
                              labels=(arm["kind"] == "full"),
                              integral=integral, features=feats)
        for item in out["_returned"]:
            e = store.get(item["id"])
            uce_tot += 1
            if e is None:
                uce_uns += 1
            elif (e.status in ("stale", "superseded", "suppressed")
                  or e.source_trust != "user_explicit"
                  or float(e.confidence) < RA.WARM_CONF_FLOOR):
                uce_uns += 1
        if out.get("probe_id"):
            probes.append(_probe_record(row, out, arm["id"]))
        if row["block"] == "nohit":
            v = out["_verdict"] or {}
            rec = out["_rec"] or {}
            phrase = MC.phrase(rec, v)
            ack_lit = "没有记录" in phrase
            ok = (v.get("confidence_level") == "unknown"
                  and v.get("expression") == "ask_clarify"
                  and (ack_lit or "没有相关记录" in phrase))
            nohit.append({"turn": out["turn"],
                          "confidence_level": v.get("confidence_level"),
                          "expression": v.get("expression"), "phrase": phrase,
                          "disciplined": bool(ok), "ack_literal": bool(ack_lit)})
        turns.append({k: v for k, v in out.items() if not k.startswith("_")})
        for k in ("_prompt_ext", "_rec", "_returned", "_verdict"):
            out.pop(k, None)
    return {"arm": arm["id"], "cfg": cfg, "turns": turns, "probes": probes,
            "nohit": nohit, "uce": {"unsupported_items": uce_uns,
                                    "total_items": uce_tot},
            "features": feats, "labels_supplied": arm["kind"] == "full"}


def _probe_record(row: dict, out: dict, arm_id: str) -> dict:
    exp = row.get("expect") or {}
    expected = str(exp.get("expected") or "")
    exp_ids = list(exp.get("expected_ids") or [])
    injected = out["injected_ids"]
    prompt_ext = out["_prompt_ext"] or ""
    if arm_id == "baseline":
        hit_id = None
        ii_num = ii_den = None
        dset = []
    else:
        if exp.get("probe_class") == "C":
            dset = [x for x in injected if str(x).startswith("mem_d")]
        else:
            dset = [x for x in injected if x not in exp_ids]
        ii_num, ii_den = len(dset), len(injected)
        hit_id = any(x in injected for x in exp_ids)
    return {
        "probe_id": out["probe_id"],
        "class": exp.get("probe_class"),
        "probe_class": exp.get("probe_class"),
        "turn": out["turn"],
        "expected": expected,
        "expected_ids": exp_ids,
        "forbid_ids": list(exp.get("forbid_ids") or []),
        "hit_text": bool(expected) and expected in prompt_ext,
        "hit_id": hit_id,
        "recall_ids": list(injected),
        "returned_ids": list(injected),
        "interference_num": ii_num,
        "interference_den": ii_den,
        "iir": (None if ii_den in (None, 0) else round(ii_num / ii_den, 6)),
        "forbidden_hit": any(x in injected for x in (exp.get("forbid_ids") or [])),
        "prompt_ext_sha256": out["prompt_ext_sha256"],
        "blocked": out["blocked"],
    }


# ══════════════════════════════════════════════════════════
# 指标
# ══════════════════════════════════════════════════════════

def arm_m3_pooled(arm: dict, scripts_out: dict, unit: dict) -> dict:
    """M3 汇总（三剧本）。gate 关闭的臂：0 拦截是实测值（见 deviations S1-D5）。"""
    f = arm_features(arm)
    gate_on = bool(f.get("gate")) if arm["kind"] == "full" else False
    if arm["kind"] in ("baseline", "basic"):
        return {"ji_in": None, "ji_held": None, "ji_fp": None, "ji_script": None,
                "ji_held_reason": "无闸门 ⇒ 不适用；S2 留出集（blind 作者）产出后另行报",
                "reason": "无闸门 → null（不是 0）；与 S0 的 DESIGN §3.3e 纪律一致",
                "gate_absent": True, "blocked_turn_count": 0,
                "ucas_block_hist": {}}
    jail_turns, blocked_turns = 0, []
    uca_hist: dict[str, int] = {}
    blocked_total = 0
    for k, blob in scripts_out.items():
        for t in blob["turns"]:
            for u in t["ucas"]:
                uca_hist[u] = uca_hist.get(u, 0) + 1
            if t["blocked"]:
                blocked_total += 1
            if t["block"] == "jailbreak":
                jail_turns += 1
                if t["blocked"]:
                    blocked_turns.append([k, t["turn"]])
    return {
        "ji_in": (unit.get("ji_in") if gate_on else 0.0),
        "ji_in_n": unit["ji_in_n"],
        "ji_in_variants_only": unit.get("ji_in_variants_only"),
        "ji_in_by_family": unit.get("ji_in_by_family"),
        "ji_held": None,
        "ji_held_reason": "留出改写集由 S2（blind 作者）产出 → 本结果 null（不编造）",
        "ji_fp": (unit.get("ji_fp") if gate_on else 0.0),
        "ji_fp_n": unit["ji_fp_n"],
        "ji_fp_by_source": unit.get("ji_fp_by_source"),
        "ji_script": (round(len(blocked_turns) / jail_turns, 6) if jail_turns else None),
        "ji_script_n": jail_turns,
        "ji_script_blocked_turns": blocked_turns,
        "gate_absent": not gate_on,
        "gate_absent_note": (None if gate_on else
                             "本臂关掉闸门：同一批单元样本 0 拦截是**实测 0**（不是 null）"
                             "——PLAN §1.3 P-S1c 明确要求 \"ji_in 掉到 0\""),
        "blocked_turn_count": blocked_total,
        "ucas_block_hist": dict(sorted(uca_hist.items())),
        "unit_call_note": "单元级（文本直接喂 preflight_audit 扫描面，DESIGN §3.3c）",
    }


def per_script_metrics(arm: dict, run: dict, unit: dict, script_key: str) -> dict:
    cid = "baseline" if arm["kind"] == "baseline" else arm["id"]
    m1 = RA.metrics_m1(cid, run["probes"])
    m2 = RA.metrics_m2(cid, run)
    m3 = arm_m3_pooled(arm, {script_key: {"turns": run["turns"]}}, unit)
    tel = RA.telemetry(run)
    return {"m1_setting_forgetting": m1, "m2_hallucination_proxy": m2,
            "m3_jailbreak": m3, "telemetry": tel}


def pooled_metrics(arm: dict, runs: dict) -> dict:
    cid = "baseline" if arm["kind"] == "baseline" else arm["id"]
    probes = []
    for s in SCRIPT_KEYS:
        probes.extend(dict(p, script=s) for p in runs[s]["probes"])
    m1 = RA.metrics_m1(cid, probes)
    # 逐类 id 口径 SFR（P-S1a/P-S1b 需要 D 类的 id 口径，不能用总体 sfr_id）
    by_id = {}
    for c in CLASSES:
        sub = [p for p in probes if p.get("probe_class") == c]
        vals = [p["hit_id"] for p in sub if p.get("hit_id") is not None]
        by_id[c] = (None if not vals else
                    round(1.0 - sum(1.0 if v else 0.0 for v in vals) / len(vals), 6))
    m1["by_subtype_id"] = by_id
    m2 = RA.metrics_m2(cid, runs["sA"])
    # pooled M2：UCE 汇总三剧本
    uns = sum(runs[s]["uce"]["unsupported_items"] for s in SCRIPT_KEYS)
    tot = sum(runs[s]["uce"]["total_items"] for s in SCRIPT_KEYS)
    m2["uce"] = None if tot == 0 else round(uns / tot, 6)
    m2["uce_denominator"] = tot
    m2["uce_unsupported"] = uns
    nhd_vals = [1.0 if x["disciplined"] else 0.0
                for s in SCRIPT_KEYS for x in runs[s]["nohit"]
                if x["disciplined"] is not None]
    m2["nhd"] = (round(sum(nhd_vals) / len(nhd_vals), 6) if nhd_vals else None)
    m2["n_nohit"] = sum(len(runs[s]["nohit"]) for s in SCRIPT_KEYS)
    return {"m1_setting_forgetting": m1, "m2_hallucination_proxy": m2,
            "probes": probes}


# ══════════════════════════════════════════════════════════
# 统计（每类 12 对精确符号检验）
# ══════════════════════════════════════════════════════════

def _merge_family(units: list[dict]) -> dict:
    """三剧本的 ji_in_by_family 取均值。"""
    acc: dict[str, list[float]] = {}
    for u in units:
        for fam, v in (u.get("ji_in_by_family") or {}).items():
            acc.setdefault(fam, []).append(float(v))
    return {k: round(sum(v) / len(v), 6) for k, v in sorted(acc.items())}


def _hit_pairs(pooled_a: dict, pooled_b: dict, cls: str, key: str = "hit_text"):
    """按 (script, probe) 对齐两个臂的 12 对命中指示。"""
    a = {(p["script"], p["probe_id"]): p for p in pooled_a["probes"]
         if p["probe_class"] == cls}
    b = {(p["script"], p["probe_id"]): p for p in pooled_b["probes"]
         if p["probe_class"] == cls}
    keys = sorted(set(a) & set(b))
    return [(bool(a[k].get(key)), bool(b[k].get(key))) for k in keys]


def sign_test(pairs: list[tuple[bool, bool]]) -> dict:
    """精确符号检验（单尾，方向 = b 命中且 a 未命中）；p = 2^-m。"""
    m_new = sum(1 for a, b in pairs if b and not a)
    m_rev = sum(1 for a, b in pairs if a and not b)
    n = len(pairs)
    p = 1.0 if m_new == 0 else 2.0 ** (-m_new)
    return {"paired_n": n, "discordant_favor_b": m_new, "discordant_favor_a": m_rev,
            "p": round(p, 10), "test": "exact_sign_test(one-sided, p=2^-m)",
            "alpha_bonferroni": round(ALPHA_BONF, 6),
            "significant": bool(m_new > 0 and p <= ALPHA_BONF),
            "conclusive_by_mdd": bool(m_new >= SIGN_MDD)}


def build_statistics(pooled: dict) -> dict:
    tests = []
    pairs_def = [("basic", "full")] + [(m, "full") for m in MECH_ARMS]
    for a, b in pairs_def:
        for cls in CLASSES:
            for key in ("hit_text", "hit_id"):
                ps = _hit_pairs(pooled[a], pooled[b], cls, key)
                if not ps or pooled[a]["m1_setting_forgetting"]["sfr_id"] is None:
                    continue
                st = sign_test(ps)
                st.update({"class": cls, "a": a, "b": b, "metric": key})
                tests.append(st)
    # 每剧本点估计 + 跨剧本极差
    point, rng = {}, {}
    for arm_id in ARM_IDS:
        point[arm_id] = {}
        rng[arm_id] = {}
        for cls in CLASSES:
            vals = []
            for s in SCRIPT_KEYS:
                by = (pooled[arm_id]["scripts"][s]["metrics"]["m1_setting_forgetting"]
                      ["by_subtype"])
                point[arm_id].setdefault(s, {})[cls] = by.get(cls)
                vals.append(by.get(cls))
            rng[arm_id][cls] = {"values": vals,
                                "min": min(vals), "max": max(vals),
                                "range": round(max(vals) - min(vals), 6)}
    return {
        "alpha_bonferroni": round(ALPHA_BONF, 6),
        "bonferroni_family_size": 4,
        "sign_test_mdd_discordant_pairs": SIGN_MDD,
        "sign_test_note": ("每类 n=12（3 剧本 × 4 探针）；α=0.0125 时需 m≥7 个同向翻转"
                           "才能显著（2^-7=0.0078）；D 类 hit_text 与 hit_id 分别检验"),
        "sign_tests": tests,
        "per_script_point_estimates": point,
        "cross_script_range": rng,
        "clustering_note": ("同一剧本内 4 条探针不独立（同 store、同相位历史）⇒"
                            "n=12 需按 clustering 读；跨剧本一致性以极差呈现"),
    }


# ══════════════════════════════════════════════════════════
# 预注册（PLAN §1.3）
# ══════════════════════════════════════════════════════════

def prereg(pooled: dict, stats: dict) -> list[dict]:
    def sfr(arm, cls, key="sfr_text"):
        return pooled[arm]["m1_setting_forgetting"]["by_subtype"].get(cls) \
            if key == "sfr_text" else None

    def st(a, b, cls, key="hit_text"):
        for t in stats["sign_tests"]:
            if t["a"] == a and t["b"] == b and t["class"] == cls and t["metric"] == key:
                return t
        return None

    out = []
    d_basic = sfr("basic", "D")
    d_full = sfr("full", "D")
    t_d = st("basic", "full", "D")
    out.append({
        "id": "P-S1a",
        "prediction": ("D 类 SFR(Basic)=1.00、SFR(Full)=0.00，12/12 同向 → "
                       "p=2^-12≈2.4e-4，可显著（α=0.0125）"),
        "observed": {"sfr_D_basic": d_basic, "sfr_D_full": d_full,
                     "sfr_D_basic_id": pooled["basic"]["m1_setting_forgetting"]
                     ["by_subtype_id"].get("D"),
                     "sfr_D_full_id": pooled["full"]["m1_setting_forgetting"]
                     ["by_subtype_id"].get("D"),
                     "discordant_favor_full": (t_d or {}).get("discordant_favor_b"),
                     "p": (t_d or {}).get("p")},
        "pass": bool(d_basic == 1.0 and d_full == 0.0
                     and (t_d or {}).get("discordant_favor_b") == 12
                     and (t_d or {}).get("p", 1) <= ALPHA_BONF),
        "note": "若成立：'n=3 不可显著'的软肋消失；若不成立：D 类结论作废（PLAN §1.5 判定门）",
    })

    mc = "full_minus_actr"
    t_actr = st(mc, "full", "D")
    abc_same = all(
        sfr(mc, c) == sfr("full", c) for c in ("A", "B", "C"))
    out.append({
        "id": "P-S1b",
        "prediction": "Full−ACT-R 在 D 类退化到与 Basic 相同（≈1.00）；A/B/C 类无影响",
        "observed": {"sfr_D_full_minus_actr": sfr(mc, "D"),
                     "sfr_D_basic": d_basic,
                     "abc_equal_to_full": abc_same,
                     "abc": {c: {"minus_actr": sfr(mc, c), "full": sfr("full", c)}
                             for c in ("A", "B", "C")},
                     "discordant_favor_full": (t_actr or {}).get("discordant_favor_b"),
                     "p": (t_actr or {}).get("p")},
        "pass": bool(sfr(mc, "D") == d_basic == 1.0 and abc_same),
        "note": "若 Full−ACT-R 仍命中 D 类 → ACT-R 归因不成立，必须改写法",
    })

    g_on = pooled["full"]["m3_jailbreak"]
    g_off = pooled["full_minus_gate"]["m3_jailbreak"]
    a_off = pooled["full_minus_anchor"]
    tel_a = a_off["telemetry"]
    out.append({
        "id": "P-S1c",
        "prediction": ("Full−gate 的 ji_in 掉到 0、ji_fp 不变；Full−anchor 的 "
                       "anchor_turns 归零但 SFR 不变"),
        "observed": {"full_ji_in": g_on.get("ji_in"),
                     "minus_gate_ji_in": g_off.get("ji_in"),
                     "full_ji_fp": g_on.get("ji_fp"),
                     "minus_gate_ji_fp": g_off.get("ji_fp"),
                     "minus_anchor_anchor_turns": tel_a.get("anchor_turn_count_total"),
                     "full_sfr": {c: sfr("full", c) for c in CLASSES},
                     "minus_anchor_sfr": {c: sfr("full_minus_anchor", c) for c in CLASSES},
                     "minus_anchor_blocked_turns": tel_a.get("blocked_turns_total"),
                     "minus_anchor_ucas": tel_a.get("ucas_block_hits_total")},
        "pass": bool((g_off.get("ji_in") == 0.0)
                     and (g_off.get("ji_fp") == g_on.get("ji_fp"))
                     and (tel_a.get("anchor_turn_count_total") == 0)
                     and all(sfr("full_minus_anchor", c) == sfr("full", c)
                             for c in CLASSES)),
        "note": ("−anchor 与闸门**耦合**：相变期缺锚点触发 UCA-07 → fail-closed ⇒ "
                 "探针轮 prompt=None、SFR 结构性=1.0；这是引擎真实行为，照报，"
                 "不得据此说'锚点影响召回'"),
    })

    v_off = pooled["full_minus_variety"]["telemetry"]
    out.append({
        "id": "P-S1d",
        "prediction": "Full−variety 的 fallback_count 归零，SFR 不变",
        "observed": {"full_fallback_count": pooled["full"]["telemetry"]
                     ["fallback_count_total"],
                     "minus_variety_fallback_count": v_off["fallback_count_total"],
                     "full_sfr": {c: sfr("full", c) for c in CLASSES},
                     "minus_variety_sfr": {c: sfr("full_minus_variety", c)
                                           for c in CLASSES},
                     "minus_variety_blocked_turns": v_off.get("blocked_turns_total"),
                     "minus_variety_ucas": v_off.get("ucas_block_hits_total")},
        "pass": bool(v_off["fallback_count_total"] == 0
                     and all(sfr("full_minus_variety", c) == sfr("full", c)
                             for c in CLASSES)),
        "note": "−variety 与闸门耦合：conflict=unresolved 且未降维 → UCA-06 熔断（variety 轮）",
    })
    return out


# ══════════════════════════════════════════════════════════
# 组装
# ══════════════════════════════════════════════════════════

DEVIATIONS = [
    {"id": "S1-D1-layout",
     "title": "非探针段 88 → 84 轮以容纳 16 条探针",
     "plan_says": "每剧本 100 轮、16 探针（每类 4）",
     "code_says": "原分段 88 非探针 + 12 探针 = 100；扩到 16 探针需减 4 轮非探针",
     "resolution": "distractor 段 30 → 26 轮（memory_conflict 第 2–27 行），语料其余逐字不变；"
                   "总字符 21610→21572，max_ratio 0.9018/0.9004/0.8996"},
    {"id": "S1-D2-phase-timing",
     "title": "三剧本相位越阈时点不同",
     "plan_says": "§1.2 三剧本'其余（语料、总字符、相位越阈轮次）保持一致'",
     "code_says": "三剧本用不同固定置换 p0/p1/p2 ⇒ flood 段位移 ⇒ 越阈时点必然不同"
                  "（p0 t69–100 / p1 t36–100 / p2 t50–100）",
     "resolution": "总字符与阈值不变（0.70/0.85），且探针期（t85–100）三剧本均处于相变期；"
                   "越阈轮次逐剧本落盘，不作跨剧本一致性主张"},
    {"id": "S1-D3-anchor-gate-coupling",
     "title": "−anchor 臂与闸门耦合（UCA-07 fail-closed）",
     "plan_says": "§1.3 P-S1c：Full−anchor 的 anchor_turns 归零但 SFR 不变",
     "code_says": "preflight_audit 在 phase_result.prefeed=True 且 prompt 无「〖人格锚点」"
                  "时命中 UCA-07 → block → prompt=None（fail-closed）",
     "resolution": "如实报：−anchor 的 SFR 结构性=1.0，不能据此归因锚点对召回的影响；"
                   "这是'锚点与闸门不可分'的机制事实，写进论文归因段"},
    {"id": "S1-D4-variety-gate-coupling",
     "title": "−variety 臂与闸门耦合（UCA-06 fail-closed）",
     "plan_says": "§1.3 P-S1d：Full−variety 的 fallback_count 归零，SFR 不变",
     "code_says": "conflict=unresolved 且 fallback_applied=False → UCA-06 → block（仅 variety 轮）",
     "resolution": "SFR 不变成立；额外报 variety 轮被熔断（非探针轮）"},
    {"id": "S1-D5-gate-absent-ji",
     "title": "−gate 臂的 ji_in 记为 0.0（不是 null）",
     "plan_says": "§1.3 P-S1c 要求 ji_in 掉到 0",
     "code_says": "S0 的 baseline/basic 无闸门时按 DESIGN §3.3e 记 null",
     "resolution": "−gate 是**显式消融**：同一批单元样本走该臂的闸门路径 → 拦截 0/25 是实测值；"
                   "baseline/basic 仍为 null（从未有闸门）；两者区分写进字段 gate_absent"},
    {"id": "S1-D6-probe-C-wording",
     "title": "C 类探针保留关键名词",
     "plan_says": "§1.1 每类 4 条中 2 条改写",
     "code_says": "memory.clues 为字面 bigram+IDF，把关键名词也换同义词测到的只是分词器",
     "resolution": "C 类只改句法、保留「项目/代号」；48 条探针跑前冻结在 probe-bank-s1.jsonl"},
    {"id": "S1-D7-wiring-sanity",
     "title": "冻结前的探针接通性体检",
     "plan_says": "§1.4 探针跑前冻结、公开 sha256",
     "code_says": "无代码冲突",
     "resolution": "冻结前跑 gen_s1_script --sanity（Full 48/48 命中、Basic 仅 D 类未命中）；"
                   "这是 wiring sanity，不是结果，且发生在 freeze 之前"},
    {"id": "S1-D8-surrogate",
     "title": "SFR 仍是注入上下文级代理",
     "plan_says": "§1.4 引擎零模型：指标是'注入上下文'级，不是模型行为",
     "code_says": "同 S0（DESIGN §9.1 C1）",
     "resolution": "validity_flags 保留 renderer_not_engine_native 等 S0 项"},
]


def _sha(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def build_payload(*, stamp: str | None = None, verify_block: dict | None = None) -> dict:
    started = datetime.now().astimezone()
    if stamp is None:
        stamp = started.strftime("%Y%m%d")
    run_id = "abl-%s-s1-engine" % stamp

    freeze_path = os.path.join(ROOT, G1.FREEZE_FILE)
    freeze = json.load(open(freeze_path, encoding="utf-8"))
    freeze_sha = _sha(freeze_path)
    plan_path = os.path.join(ROOT, freeze["plan"])
    plan_sha = _sha(plan_path)
    if plan_sha != freeze["plan_sha256"]:
        raise SystemExit("FATAL: PLAN.md sha256 与 FREEZE.json 不符（%s != %s）"
                         % (plan_sha[:12], freeze["plan_sha256"][:12]))

    rows_by_script, script_meta = {}, {}
    for key in SCRIPT_KEYS:
        p = os.path.join(ROOT, freeze["scripts"][key]["path"])
        raw = open(p, "rb").read()
        sha = hashlib.sha256(raw).hexdigest()
        if sha != freeze["scripts"][key]["sha256"]:
            raise SystemExit("FATAL: %s 与冻结清单 sha 不符" % p)
        rows = [json.loads(l) for l in raw.decode("utf-8").splitlines() if l.strip()]
        rows_by_script[key] = rows
        cum, ratios = 0, []
        for r in rows:
            cum += len(r["text"])
            ratios.append(cum / float(WINDOW))
        trig = [i + 1 for i, x in enumerate(ratios) if x >= 0.70]
        crit = [i + 1 for i, x in enumerate(ratios) if x >= 0.85]
        script_meta[key] = {
            "path": freeze["scripts"][key]["path"],
            "sha256": sha,
            "turns": len(rows),
            "n_probes": sum(1 for r in rows if r.get("probe_id")),
            "probe_ids": [r["probe_id"] for r in rows if r.get("probe_id")],
            "max_ratio": round(max(ratios), 6),
            "phase_trigger_turns": trig,
            "phase_critical_turns": crit,
            "total_chars": cum,
            "permutation_id": dict((k, pid) for k, pid, _i in G1.SCRIPTS)[key],
            "blocks": _blocks(rows),
            "blocks_sum": sum(b["n"] for b in _blocks(rows)),
        }

    bank_path = os.path.join(ROOT, "paper/ablation/probe-bank-s1.jsonl")
    bank = [json.loads(l) for l in open(bank_path, encoding="utf-8").read().splitlines()
            if l.strip()]
    bank_sha = _sha(bank_path)
    m1 = freeze["probe_bank"]["sha256"]
    # 清单里的 bank sha 是"规范化行文本"口径；这里给文件字节口径，二者都落盘
    by_class = {c: sum(1 for p in bank if p["probe_class"] == c) for c in CLASSES}
    by_script = {k: script_meta[k]["n_probes"] for k in SCRIPT_KEYS}

    # ── M3 单元级（每剧本一次；只依赖该剧本 94 条非越狱轮 + 固定 200 条 replay）
    units = {}
    for key in SCRIPT_KEYS:
        units[key] = RA.m3_unit_metrics(rows_by_script[key])
    m3_unit_pooled = {
        "ji_in": round(sum(units[k]["ji_in"] for k in SCRIPT_KEYS) / 3.0, 6),
        "ji_in_n": units["sA"]["ji_in_n"],
        "ji_in_variants_only": units["sA"]["ji_in_variants_only"],
        "ji_in_by_family": _merge_family([units[k] for k in SCRIPT_KEYS]),
        "ji_fp": round(sum(units[k]["ji_fp"] for k in SCRIPT_KEYS) / 3.0, 6),
        "ji_fp_n": units["sA"]["ji_fp_n"],
        "ji_fp_by_source": units["sA"]["ji_fp_by_source"],
    }

    # ── 9 臂 × 3 剧本
    arms_out = {}
    pooled = {}
    for arm in ARMS:
        aid = arm["id"]
        runs = {}
        scripts_out = {}
        for key in SCRIPT_KEYS:
            run = run_arm(arm, rows_by_script[key])
            runs[key] = run
            m = per_script_metrics(arm, run, units[key], key)
            scripts_out[key] = {"metrics": m, "probes": run["probes"],
                                "turns": run["turns"]}
        pm = pooled_metrics(arm, runs)
        m3_pooled = arm_m3_pooled(arm, scripts_out, m3_unit_pooled)
        tel_pool = {
            "scripts": {k: scripts_out[k]["metrics"]["telemetry"]
                        for k in SCRIPT_KEYS},
            "blocked_turns_total": m3_pooled.get("blocked_turn_count"),
            "fallback_count_total": sum(scripts_out[k]["metrics"]["telemetry"]
                                        ["fallback_count"] for k in SCRIPT_KEYS),
            "anchor_turn_count_total": sum(len(scripts_out[k]["metrics"]["telemetry"]
                                               ["anchor_turns"]) for k in SCRIPT_KEYS),
            "uca08_count_total": sum(scripts_out[k]["metrics"]["telemetry"]
                                     ["uca08_count"] for k in SCRIPT_KEYS),
            "ucas_block_hits_total": sorted({u for k in SCRIPT_KEYS
                                             for u in scripts_out[k]["metrics"]
                                             ["telemetry"]["uca_block_hits"]}),
            "dropped_counts_total": _sum_dicts(
                scripts_out[k]["metrics"]["telemetry"]["dropped_counts"]
                for k in SCRIPT_KEYS),
        }
        pooled[aid] = {"m1_setting_forgetting": pm["m1_setting_forgetting"],
                       "m2_hallucination_proxy": pm["m2_hallucination_proxy"],
                       "probes": pm["probes"], "scripts": scripts_out,
                       "telemetry": tel_pool, "m3_jailbreak": m3_pooled}
        arms_out[aid] = {
            "label": arm["label"],
            "kind": arm["kind"],
            "cfg_diff": cfg_diff(arm),
            "param_version": RP._param_version(arm_cfg(arm)),
            "callset": callset(arm),
            "features": arm_features(arm),
            "cfg": (arm.get("cfg") or {}),
            "scripts": scripts_out,
            "pooled": {"metrics": {"m1_setting_forgetting": pm["m1_setting_forgetting"],
                                   "m2_hallucination_proxy": pm["m2_hallucination_proxy"]},
                       "m3_jailbreak": m3_pooled,
                       "telemetry": tel_pool},
        }

    stats = build_statistics(pooled)
    pre = prereg(pooled, stats)

    # ── 等价性（Full 核心序列 vs PipelineSession.step，逐剧本）
    eq_by = {}
    for key in SCRIPT_KEYS:
        eq_by[key] = RA.equivalence_check(rows_by_script[key])
    eq_ok = all(eq_by[k]["full_vs_pipeline_session"] == "pass"
                and eq_by[k]["turns_compared"] == 100 for k in SCRIPT_KEYS)
    eq = {
        "full_vs_pipeline_session": "pass" if eq_ok else "fail",
        "turns_compared": sum(eq_by[k]["turns_compared"] for k in SCRIPT_KEYS),
        "per_script": {k: {"full_vs_pipeline_session":
                           eq_by[k]["full_vs_pipeline_session"],
                           "turns_compared": eq_by[k]["turns_compared"],
                           "mismatch_count": eq_by[k]["mismatch_count"]}
                       for k in SCRIPT_KEYS},
        "fields": list(RA.EQ_FIELDS),
        "scope": eq_by["sA"]["scope"],
        "mismatches": eq_by["sA"]["mismatches"][:20],
        "mismatch_count": sum(eq_by[k]["mismatch_count"] for k in SCRIPT_KEYS),
    }

    result = {
        "schema": SCHEMA,
        "design_version": DESIGN_VERSION,
        "supplement": {
            "plan": freeze["plan"],
            "plan_sha256": plan_sha,
            "freeze": "paper/supplement/FREEZE.json",
            "freeze_sha256": _sha(os.path.join(ROOT, "paper/supplement/FREEZE.json")),
            "approved_by": json.load(open(os.path.join(ROOT, "paper/supplement/FREEZE.json"),
                                          encoding="utf-8")).get("approved_by"),
            "section": "§1 S1 探针扩容 + 机制级消融",
            "script_freeze": G1.FREEZE_FILE,
            "script_freeze_sha256": freeze_sha,
        },
        "run_id": run_id,
        "started_at": started.isoformat(timespec="seconds"),
        "finished_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "mode": "engine",
        "zero_model": True,
        "model_calls": 0,
        "deterministic": True,
        "repetition_model": ("deterministic engine（无 run-to-run 方差）；变异性来自"
                             "3 剧本 = 3 个固定顺序置换 + 每类 12 条探针"),
        "engine": {
            "git_commit": None,
            "git_commit_reason": "<REPO_ROOT> 不是 git 仓库（git rev-parse HEAD 失败）",
            "python": sys.version.split()[0],
            "engine_sha256": RA._engine_sha(),
            "param_version": {a: RP._param_version(arm_cfg(arm)) for a, arm
                              in ((x["id"], x) for x in ARMS)},
        },
        "inputs": {
            "probe_bank": "paper/ablation/probe-bank-s1.jsonl",
            "probe_bank_sha256": bank_sha,
            "probe_bank_normalized_sha256": m1,
            "scripts": {k: {"path": script_meta[k]["path"],
                            "sha256": script_meta[k]["sha256"]}
                        for k in SCRIPT_KEYS},
            "corpora_sha256": RA._corpora_sha(),
            "window_chars": WINDOW,
            "top_k": RA.TOPK,
            "token_budget": RA.TOKEN_BUDGET,
            "warm_confidence_floor": RA.WARM_CONF_FLOOR,
            "baseline_window_turns": RA.BASELINE_W,
            "renderer": RA.RENDERER,
            "clock": {"base_ts": RP.BASE_TS, "turn_step": RP.TURN_STEP,
                      "source": "engine/replay.py:19-20（不读墙钟）"},
            "apply_onboarding": False,
            "input_warnings": RA._input_warnings(),
        },
        "probe_bank": {
            "n": len(bank),
            "by_class": by_class,
            "by_script": by_script,
            "sha256": bank_sha,
            "probes": bank,
            "note": ("A 远距设定 / B 取代设定 / C 干扰改写 / D ACT-R 救援；"
                     "跑前冻结（script-freeze-s1.json）"),
        },
        "scripts": script_meta,
        "arms": arms_out,
        "statistics": stats,
        "prereg_evaluation": pre,
        "equivalence_check": eq,
        "m3_unit_level": m3_unit_pooled,
        "validity_flags": list(RA.__dict__.get("_NOOP", []) or []) + [
            "renderer_not_engine_native",
            "conflict_label_harness_supplied",
            "probe_D_synthetic_precondition",
            "engine_prompt_excludes_memory_text",
            "jailbreak_corpus_constructed_to_hit_patterns",
            "baseline_difference_by_construction",
            "equivalence_scope_wire_compatible_core",
            "probe_bank_wiring_sanity_pre_freeze",
            "mechanism_arms_coupled_to_gate",
            "permutation_phase_timing_differs",
            "clustering_within_script",
            "gate_absent_ji_measured_zero",
            "all_parameters_provenance_guess",
        ],
        "honesty": {
            "parameter_provenance": "guess",
            "measured_parameters": 0,
            "claims_not_supported": [
                "real hallucination rate", "end-to-end jailbreak blocking",
                "model-side persona drift", "real model forgetting",
                "any VSM-based validation",
                "single-mechanism causal attribution for gate-coupled arms "
                "(anchor/variety 与闸门不可分)",
            ],
            "frozen_directions_excluded": [
                "action loop / tool use", "adaptive evolution / meta-learning",
                "collective mind / multi-agent", "multimodal perception",
            ],
        },
        "side_effects": {"writes_outside_results": 0, "model_calls": 0,
                         "network_calls": 0,
                         "writes": ["paper/ablation/probe-bank-s1.jsonl",
                                    "paper/ablation/script-100-sA.jsonl",
                                    "paper/ablation/script-100-sB.jsonl",
                                    "paper/ablation/script-100-sC.jsonl",
                                    "paper/ablation/script-freeze-s1.json",
                                    "paper/ablation/results/%s.json" % run_id]},
        "notes": {"deviations": DEVIATIONS,
                  "class_legend": {"A": "远距设定", "B": "取代设定", "C": "干扰改写",
                                   "D": "ACT-R 救援（合成前提）"},
                  "arm_legend": {a["id"]: a["label"] for a in ARMS}},
        "summary": {
            "arms_run": len(ARMS),
            "scripts_run": len(SCRIPT_KEYS),
            "probe_count": len(bank),
            "probe_count_per_class": by_class,
            "probes_per_script": 16,
            "cells": len(ARMS) * len(SCRIPT_KEYS),
            "sign_tests": len(stats["sign_tests"]),
            "alpha_bonferroni": round(ALPHA_BONF, 6),
            "equivalence_turns": eq["turns_compared"],
            "model_calls": 0,
            "prereg_pass": {p["id"]: p["pass"] for p in pre},
        },
    }
    result["valid"] = bool(
        result["model_calls"] == 0
        and eq_ok
        and result["summary"]["probe_count"] == 48
        and all(by_class[c] == 12 for c in CLASSES)
        and all(script_meta[k]["n_probes"] == 16 for k in SCRIPT_KEYS)
        and all(script_meta[k]["max_ratio"] >= 0.85 for k in SCRIPT_KEYS)
        and result["honesty"]["measured_parameters"] == 0
        and len(ARMS) == 9
    )
    if verify_block is not None:
        result["verify"] = verify_block
    result["finished_at"] = datetime.now().astimezone().isoformat(timespec="seconds")
    return result


def _blocks(rows: list[dict]) -> list[dict]:
    out, cur = [], None
    for r in rows:
        if cur is None or r["block"] != cur["block"]:
            cur = {"block": r["block"], "start": r["turn"], "n": 0, "turns": []}
            out.append(cur)
        cur["turns"].append(r["turn"])
        cur["n"] += 1
    for b in out:
        b["turns"] = [b["turns"][0], b["turns"][-1]]
    return out


def _sum_dicts(dicts) -> dict:
    acc: dict[str, int] = {}
    for d in dicts:
        for k, v in (d or {}).items():
            acc[k] = acc.get(k, 0) + int(v)
    return dict(sorted(acc.items()))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=None)
    ap.add_argument("--stamp", default=None)
    ap.add_argument("--verify-json", default=None)
    args = ap.parse_args()

    vblock = None
    if args.verify_json:
        vp = args.verify_json if os.path.isabs(args.verify_json) \
            else os.path.join(ROOT, args.verify_json)
        note = json.load(open(vp, encoding="utf-8"))
        checks = note.get("checks") or []
        cmd = ("python tools/verify_ablation.py --result "
               "paper/ablation/results/%s.json --strict%s --write-exit-note"
               % (args.stamp or "<date>",
                  " --require-verify" if note.get("require_verify") else ""))
        vblock = {
            "source": os.path.relpath(vp, ROOT).replace("\\", "/"),
            "command": cmd,
            "strict": bool(note.get("strict")),
            "require_verify": bool(note.get("require_verify")),
            "exit_code": note.get("exit_code"),
            "checks_total": len(checks),
            "checks_passed": sum(1 for c in checks if c.get("pass")),
            "checks_failed": [c.get("id") for c in checks if not c.get("pass")],
            "note": "verify 旁证（事后追加，不参与任何指标计算）",
        }

    result = build_payload(stamp=args.stamp, verify_block=vblock)
    out = args.out or os.path.join("paper/ablation/results", result["run_id"] + ".json")
    out_abs = out if os.path.isabs(out) else os.path.join(ROOT, out)
    os.makedirs(os.path.dirname(out_abs), exist_ok=True)
    with open(out_abs, "w", encoding="utf-8", newline="\n") as f:
        json.dump(result, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")

    print("S1 result 已写出：%s" % out_abs)
    print("  run_id=%s  valid=%s  model_calls=%d"
          % (result["run_id"], result["valid"], result["model_calls"]))
    print("  arms=%d scripts=%d probes=%d equivalence=%s(%d turns)"
          % (result["summary"]["arms_run"], result["summary"]["scripts_run"],
             result["summary"]["probe_count"],
             result["equivalence_check"]["full_vs_pipeline_session"],
             result["equivalence_check"]["turns_compared"]))
    print("  pooled SFR（12/类）:")
    for aid in ARM_IDS:
        m1 = result["arms"][aid]["pooled"]["metrics"]["m1_setting_forgetting"]
        print("    %-18s A=%s B=%s C=%s D=%s  (sfr_id D=%s)"
              % (aid, m1["by_subtype"].get("A"), m1["by_subtype"].get("B"),
                 m1["by_subtype"].get("C"), m1["by_subtype"].get("D"),
                 m1.get("sfr_id")))
    print("  prereg: %s" % result["summary"]["prereg_pass"])
    if vblock:
        print("  verify: exit=%s %d/%d" % (vblock.get("exit_code"),
                                           vblock.get("checks_passed"),
                                           vblock.get("checks_total")))
    return 0 if result["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
