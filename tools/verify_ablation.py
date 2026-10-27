# -*- coding: utf-8 -*-
"""消融结果机检器（`--strict` 会重生成剧本并整跑一遍复现核对）。

用法：
    python tools/verify_ablation.py --result paper/ablation/results/abl-xxx-engine.json --strict

退出码：0 = 全部通过；1 = 有失败项（失败项逐条打印）。
非 strict 只做静态机检（schema/诚实性/等价性/可溯源字段）；strict 额外：
    1) 用 `gen_ablation_script.generate()` 重生成剧本，比对落盘 stdin 的 sha256；
    2) 用同一 stamp 重跑 `run_ablation.build_payload()`，逐字段比对结果
       （`started_at`/`finished_at` 除外）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
for _p in (ROOT, HERE):
    if _p not in sys.path:
        sys.path.insert(0, _p)

REQUIRED_FLAGS = ("renderer_not_engine_native", "conflict_label_harness_supplied",
                  "probe_D_synthetic_precondition", "engine_prompt_excludes_memory_text",
                  "jailbreak_corpus_constructed_to_hit_patterns",
                  "baseline_difference_by_construction")

FROZEN_TOKENS = ("action_loop", "tool_use", "adaptive_evolution", "meta_learning",
                 "collective_mind", "multi_agent", "multimodal")

EQ_FIELDS = ("recall.returned", "injection_order", "phase_result", "anchor_applied",
             "audit{verdict,ucas,downgrades}", "blocked", "dropped", "weights", "prompt")

METRIC_KEYS = ("m1_setting_forgetting", "m2_hallucination_proxy", "m3_jailbreak")


class Checker:
    def __init__(self) -> None:
        self.rows: list[tuple[str, str, bool, str]] = []

    def check(self, cid: str, title: str, ok: bool, detail: str = "") -> None:
        self.rows.append((cid, title, bool(ok), detail))

    def report(self) -> int:
        fails = [r for r in self.rows if not r[2]]
        for cid, title, ok, detail in self.rows:
            mark = "PASS" if ok else "FAIL"
            line = "  [%s] %-6s %s" % (mark, cid, title)
            if detail and not ok:
                line += "  ← " + str(detail)[:400]
            print(line)
        print("\n合计 %d 项，通过 %d，失败 %d"
              % (len(self.rows), len(self.rows) - len(fails), len(fails)))
        return 0 if not fails else 1


def _mean(xs):
    return (sum(xs) / len(xs)) if xs else None


def _r(x, n=6):
    return None if x is None else round(float(x), n)


def _sha256(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _num_eq(a, b, tol=1e-6) -> bool:
    if a is None or b is None:
        return a is b
    return abs(float(a) - float(b)) <= tol


def static_checks(res: dict, path: str, ck: Checker) -> None:
    conds = res.get("conditions") or {}
    summary = res.get("summary") or {}
    inputs = res.get("inputs") or {}
    script = res.get("script") or {}

    # ── schema / 硬约束 ────────────────────────────────
    ck.check("C01", "schema == lingshu/ablation-result/v1",
             res.get("schema") == "lingshu/ablation-result/v1", res.get("schema"))
    ck.check("C02", "mode=engine 且 zero_model=true",
             res.get("mode") == "engine" and res.get("zero_model") is True)
    ck.check("C03", "model_calls == 0", res.get("model_calls") == 0, res.get("model_calls"))
    ck.check("C04", "deterministic=true 且 repetition_model 非空",
             res.get("deterministic") is True and bool(res.get("repetition_model")))
    rid = str(res.get("run_id") or "")
    stem = os.path.splitext(os.path.basename(path))[0]
    ok_rid = (rid == stem
              and re.fullmatch(r"abl-\d{8}-\d{6}-engine", rid) is not None)
    ck.check("C05", "run_id 与文件名一致且形如 abl-<stamp>-engine", ok_rid,
             "%r vs %r" % (rid, stem))
    ck.check("C06", "valid=true", res.get("valid") is True)

    # 时间戳
    try:
        t0 = datetime.fromisoformat(str(res["started_at"]))
        t1 = datetime.fromisoformat(str(res["finished_at"]))
        ck.check("C07", "started_at/finished_at 可解析且 finished>=started", t1 >= t0)
    except Exception as e:                                     # noqa: BLE001
        ck.check("C07", "started_at/finished_at 可解析且 finished>=started", False, e)

    # ── 三条件 × 三指标 ───────────────────────────────
    ck.check("C08", "三个条件齐全（baseline/basic/full）",
             set(conds) == {"baseline", "basic", "full"}, sorted(conds))
    missing = [(c, k) for c in ("baseline", "basic", "full")
               for k in METRIC_KEYS if k not in (conds.get(c, {}).get("metrics") or {})]
    ck.check("C09", "每条件三个 metrics 块齐全", not missing, missing)

    # ── M1 ─────────────────────────────────────────────
    m1n = {c: (conds.get(c, {}).get("metrics", {})
               .get("m1_setting_forgetting", {}).get("n_probes")) for c in conds}
    ck.check("C10", "m1.n_probes == 12（三臂）",
             all(v == 12 for v in m1n.values()), m1n)
    probe_ok, recompute_ok = True, True
    detail = []
    for c in ("baseline", "basic", "full"):
        probes = conds.get(c, {}).get("probes") or []
        if len(probes) != 12:
            probe_ok = False
            detail.append("%s probes=%d" % (c, len(probes)))
        if len({p.get("probe_id") for p in probes}) != len(probes):
            probe_ok = False
            detail.append("%s probe_id 重复" % c)
        cls = {}
        for p in probes:
            cls[p.get("probe_class")] = cls.get(p.get("probe_class"), 0) + 1
            if not isinstance(p.get("turn"), int) or not (1 <= p["turn"] <= 100):
                probe_ok = False
                detail.append("%s %s turn=%r" % (c, p.get("probe_id"), p.get("turn")))
            if not isinstance(p.get("hit_text"), bool):
                probe_ok = False
                detail.append("%s %s hit_text 非 bool" % (c, p.get("probe_id")))
        if any(cls.get(k) != 3 for k in ("A", "B", "C", "D")):
            probe_ok = False
            detail.append("%s 子类计数=%r" % (c, cls))
        # 从 evidence 复算 m1
        m1 = conds[c]["metrics"]["m1_setting_forgetting"]
        sfr_text = 1.0 - _mean([1.0 if p["hit_text"] else 0.0 for p in probes])
        if not _num_eq(m1.get("sfr_text"), _r(sfr_text)):
            recompute_ok = False
            detail.append("%s sfr_text 复算不符 %r vs %r" % (c, m1.get("sfr_text"), sfr_text))
        hits_id = [p["hit_id"] for p in probes if p.get("hit_id") is not None]
        if c == "baseline":
            if m1.get("sfr_id") is not None:
                recompute_ok = False
                detail.append("baseline sfr_id 应为 null")
            if not m1.get("sfr_id_reason"):
                recompute_ok = False
                detail.append("baseline sfr_id 缺 reason")
            if m1.get("iir") is not None or not m1.get("iir_reason"):
                recompute_ok = False
                detail.append("baseline iir 应为 null+reason")
        else:
            if not _num_eq(m1.get("sfr_id"),
                           _r(1.0 - _mean([1.0 if x else 0.0 for x in hits_id]))):
                recompute_ok = False
                detail.append("%s sfr_id 复算不符" % c)
            vals = [p["iir"] for p in probes if p.get("iir") is not None]
            if vals and not _num_eq(m1.get("iir"), _r(_mean(vals))):
                recompute_ok = False
                detail.append("%s iir 复算不符" % c)
            cp = m1.get("ci95_clopper_pearson") or []
            if len(cp) != 2 or not all(isinstance(x, (int, float)) for x in cp):
                recompute_ok = False
                detail.append("%s CP CI 缺" % c)
            if not (cp[0] <= m1.get("sfr_text") <= cp[1]):
                recompute_ok = False
                detail.append("%s sfr_text 不在 CP CI 内 %r" % (c, cp))
        for k in ("A", "B", "C", "D"):
            sub = [p for p in probes if p.get("probe_class") == k]
            want = _r(1.0 - _mean([1.0 if p["hit_text"] else 0.0 for p in sub]))
            if not _num_eq((m1.get("by_subtype") or {}).get(k), _r(want)):
                recompute_ok = False
                detail.append("%s subtype %s 复算不符" % (c, k))
    ck.check("C11", "探针 evidence 12 条/类 3 条/turn 为实际轮次", probe_ok, detail)
    ck.check("C12", "m1 指标可由 probes evidence 复算", recompute_ok, detail)

    # ── M2 ─────────────────────────────────────────────
    m2_ok, m2_detail = True, []
    for c in ("baseline", "basic", "full"):
        m2 = conds[c]["metrics"]["m2_hallucination_proxy"]
        for k in ("uce", "nhd", "uce_structural_floor", "n_nohit", "uce_denominator",
                  "uce_unsupported"):
            if k not in m2:
                m2_ok = False
                m2_detail.append("%s 缺 %s" % (c, k))
        if m2.get("uce_denominator"):
            want = _r(m2["uce_unsupported"] / m2["uce_denominator"])
            if not _num_eq(m2.get("uce"), want):
                m2_ok = False
                m2_detail.append("%s uce 复算不符" % c)
        if c == "baseline":
            if m2.get("nhd") is not None or not m2.get("nhd_reason"):
                m2_ok = False
                m2_detail.append("baseline nhd 应为 null+reason")
            if m2.get("uce_structural_floor") is not True:
                m2_ok = False
                m2_detail.append("baseline 缺 uce_structural_floor")
        else:
            vals = [1.0 if x.get("disciplined") else 0.0 for x in m2.get("probe_detail") or []
                    if x.get("disciplined") is not None]
            if vals and not _num_eq(m2.get("nhd"), _mean(vals)):
                m2_ok = False
                m2_detail.append("%s nhd 复算不符" % c)
            if m2.get("n_nohit") != 2:
                m2_ok = False
                m2_detail.append("%s n_nohit=%r" % (c, m2.get("n_nohit")))
    ck.check("C13", "M2 结构完整且可由 evidence 复算（null+reason 纪律）", m2_ok, m2_detail)

    # ── M3 ─────────────────────────────────────────────
    m3_ok, m3_detail = True, []
    for c in ("baseline", "basic"):
        m3 = conds[c]["metrics"]["m3_jailbreak"]
        if any(m3.get(k) is not None for k in ("ji_in", "ji_held", "ji_fp", "ji_script")):
            m3_ok = False
            m3_detail.append("%s 的 JI 应为 null（不是 0）" % c)
        if not m3.get("reason"):
            m3_ok = False
            m3_detail.append("%s 的 M3 缺 reason" % c)
    m3f = conds["full"]["metrics"]["m3_jailbreak"]
    if not isinstance(m3f.get("ji_in"), (int, float)):
        m3_ok = False
        m3_detail.append("full ji_in 非数值")
    if m3f.get("ji_held") is not None or not m3f.get("ji_held_reason"):
        m3_ok = False
        m3_detail.append("ji_held 应为 null+reason")
    if m3f.get("ji_fp_n") != summary.get("jailbreak_fp_controls"):
        m3_ok = False
        m3_detail.append("ji_fp_n 与 summary.jailbreak_fp_controls 不一致")
    if summary.get("jailbreak_in") != m3f.get("ji_in_n"):
        m3_ok = False
        m3_detail.append("summary.jailbreak_in 与 ji_in_n 不一致")
    if m3f.get("ji_script_n") != 6:
        m3_ok = False
        m3_detail.append("ji_script_n=%r" % m3f.get("ji_script_n"))
    by = (m3f.get("ji_fp_by_source") or {})
    if sum(v.get("n", 0) for v in by.values()) != m3f.get("ji_fp_n"):
        m3_ok = False
        m3_detail.append("ji_fp_by_source 样本合计不符")
    ck.check("C14", "M3 in/held/fp/script 齐全（Basic/Baseline null+reason）", m3_ok, m3_detail)

    # ── script ─────────────────────────────────────────
    ck.check("C15", "script.turns == 100 且 blocks 覆盖 1..100",
             script.get("turns") == 100 and script.get("blocks_sum") == 100,
             (script.get("turns"), script.get("blocks_sum")))
    ck.check("C16", "script.max_ratio >= 0.85", (script.get("max_ratio") or 0) >= 0.85,
             script.get("max_ratio"))
    ck.check("C17", "script.phase_trigger_turns 非空且 > 60",
             bool(script.get("phase_trigger_turns"))
             and min(script["phase_trigger_turns"]) > 60,
             script.get("phase_trigger_turns"))
    ck.check("C18", "script.probe_ids == 12 且 nohit_turns 2 条",
             len(script.get("probe_ids") or []) == 12
             and len(script.get("nohit_turns") or []) == 2)
    ck.check("C19", "script.store_seeds 含 D 类合成前提（30 次/100 天）",
             any(s.get("id") == "mem_pref_actr" and s.get("access_count") == 30
                 and s.get("last_access_age_days") == 100
                 for s in (script.get("store_seeds") or [])))

    # ── 等价性 ─────────────────────────────────────────
    eq = res.get("equivalence_check") or {}
    ck.check("C20", "equivalence_check == pass 且 100/100",
             eq.get("full_vs_pipeline_session") == "pass" and eq.get("turns_compared") == 100,
             (eq.get("full_vs_pipeline_session"), eq.get("turns_compared")))
    ck.check("C21", "等价性比对字段齐全", set(EQ_FIELDS) <= set(eq.get("fields") or []),
             eq.get("fields"))
    ck.check("C22", "等价性无 mismatch", eq.get("mismatch_count") == 0, eq.get("mismatch_count"))

    # ── honesty / validity_flags ──────────────────────
    hon = res.get("honesty") or {}
    ck.check("C23", "honesty.measured_parameters==0 且 provenance==guess",
             hon.get("measured_parameters") == 0
             and hon.get("parameter_provenance") == "guess",
             (hon.get("measured_parameters"), hon.get("parameter_provenance")))
    ck.check("C24", "valinity_flags 含 DESIGN §1.4/§1.5/§2.4/§3 必报项",
             set(REQUIRED_FLAGS) <= set(res.get("validity_flags") or []),
             sorted(set(REQUIRED_FLAGS) - set(res.get("validity_flags") or [])))
    bad_callsets = []
    for c, v in conds.items():
        blob = json.dumps(v.get("callset") or {}, ensure_ascii=False).lower()
        for tok in FROZEN_TOKENS:
            if tok in blob:
                bad_callsets.append((c, tok))
    ck.check("C25", "callset 未出现四冻结方向", not bad_callsets, bad_callsets)

    # ── 输入指纹 / 常量 / 冻结面 ─────────────────────
    ck.check("C26", "inputs 常量正确（window/topK/budget/renderer）",
             inputs.get("window_chars") == 24000 and inputs.get("top_k") == 3
             and inputs.get("token_budget") == 900
             and str(inputs.get("renderer", "")).startswith("render_ext"),
             {k: inputs.get(k) for k in ("window_chars", "top_k", "token_budget")})
    script_rel = str(inputs.get("script") or "")
    script_abs = os.path.join(ROOT, script_rel)
    ok_script = os.path.isfile(script_abs)
    if ok_script:
        ok_script = _sha256(open(script_abs, "rb").read()) == inputs.get("script_sha256")
    ck.check("C27", "剧本文件存在且 sha256 与结果一致", ok_script, script_rel)
    bad_engine = []
    for rel, sha in (res.get("engine") or {}).get("engine_sha256", {}).items():
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p) or _sha256(open(p, "rb").read()) != sha:
            bad_engine.append(rel)
    ck.check("C28", "engine 文件指纹与当前磁盘一致（未被改动）", not bad_engine, bad_engine)
    bad_corp = []
    for rel, sha in (inputs.get("corpora_sha256") or {}).items():
        p = os.path.join(ROOT, rel)
        if not os.path.isfile(p) or _sha256(open(p, "rb").read()) != sha:
            bad_corp.append(rel)
    ck.check("C29", "语料指纹与当前磁盘一致（未被改动）", not bad_corp, bad_corp)
    ck.check("C30", "git_commit 不可得时有 reason",
             (res.get("engine") or {}).get("git_commit") is not None
             or bool((res.get("engine") or {}).get("git_commit_reason")))

    # ── 置换 / 统计 ────────────────────────────────────
    perm = res.get("permutations") or {}
    orders = perm.get("orders") or []
    ck.check("C31", "R=3 且 p0 为原序、置换已冻结落盘",
             perm.get("R") == 3 and len(orders) == 3
             and orders[0].get("permutation_id") == "p0"
             and orders[0].get("seeded") is False,
             [o.get("permutation_id") for o in orders])
    per_cond = perm.get("per_condition") or {}
    ck.check("C32", "每个置换 × 每条件都有独立 metrics",
             set(per_cond) == {"p0", "p1", "p2"}
             and all(set(v) == {"baseline", "basic", "full"} for v in per_cond.values()))
    cross = perm.get("cross_perm_range") or {}
    ck.check("C33", "跨置换极差已报告",
             all("sfr_text_range" in cross.get(c, {}) for c in ("baseline", "basic", "full")))
    ck.check("C34", "comparisons 含 by_construction 与缺对照臂两条",
             any(x.get("kind") == "by_construction" for x in res.get("comparisons") or [])
             and any(x.get("kind") == "missing_control_arm"
                     for x in res.get("comparisons") or []))
    pre = res.get("prereg_evaluation") or []
    ck.check("C35", "预注册预期 P0–P7 逐条判定已落盘",
             {x.get("id") for x in pre} >= {"P0", "P1a", "P1b", "P2", "P3", "P4",
                                            "P5", "P6", "P7"})
    ck.check("C36", "bootstrap CI 已落盘（非 numpy 口径）",
             "bootstrap_percentile" in str(
                 conds["basic"]["metrics"]["m1_setting_forgetting"].get("ci_method", "")))
    ck.check("C37", "notes.deviations 非空（DESIGN 与代码冲突已记录）",
             bool((res.get("notes") or {}).get("deviations")))

    # ── 逐轮可溯源（Fig.2） ───────────────────────────
    trace_ok, trace_detail = True, []
    for c in ("baseline", "basic", "full"):
        turns = conds[c].get("turns") or []
        if len(turns) != 100 or [t.get("turn") for t in turns] != list(range(1, 101)):
            trace_ok = False
            trace_detail.append("%s turns 不足/编号不连续（%d）" % (c, len(turns)))
            continue
        for t in turns:
            for k in ("injected_ids", "recall_ids", "planted_setting_present",
                      "planted_setting_via", "setting_ids_injected", "phase"):
                if k not in t:
                    trace_ok = False
                    trace_detail.append("%s turn%d 缺 %s" % (c, t.get("turn"), k))
                    break
            if not isinstance(t.get("planted_setting_present"), bool):
                trace_ok = False
                trace_detail.append("%s turn%d planted_setting_present 非 bool"
                                    % (c, t.get("turn")))
            if t.get("planted_setting_via") not in ("id", "text", "none"):
                trace_ok = False
                trace_detail.append("%s turn%d via=%r" % (c, t.get("turn"),
                                                          t.get("planted_setting_via")))
            if c == "baseline":
                if t.get("recall_ids") is not None:
                    trace_ok = False
                    trace_detail.append("baseline turn%d recall_ids 应 null（结构性）"
                                        % t.get("turn"))
                if t.get("planted_setting_via") == "id":
                    trace_ok = False
                    trace_detail.append("baseline 无条目 id，via 不应为 id")
            else:
                if not isinstance(t.get("recall_ids"), list):
                    trace_ok = False
                    trace_detail.append("%s turn%d recall_ids 非 list" % (c, t.get("turn")))
            if t.get("phase") not in (None, "normal", "shift", "critical"):
                trace_ok = False
                trace_detail.append("%s turn%d phase=%r" % (c, t.get("turn"), t.get("phase")))
    ck.check("C38", "逐轮溯源字段齐全（Fig.2：注入项/设定出现/相位）", trace_ok, trace_detail)

    # 遥测与逐轮一致性 + [] 显式
    tel_ok, tel_detail = True, []
    for c in ("baseline", "basic", "full"):
        tel = conds[c].get("telemetry") or {}
        turns = conds[c].get("turns") or []
        for k in ("phase_trigger_turns", "anchor_turns"):
            if k not in tel:
                tel_ok = False
                tel_detail.append("%s telemetry 缺 %s" % (c, k))
        want_phase = [t["turn"] for t in turns if t.get("phase") in ("shift", "critical")]
        if tel.get("phase_trigger_turns") != want_phase:
            tel_ok = False
            tel_detail.append("%s phase_trigger_turns 与逐轮 phase 不一致" % c)
        want_anchor = [t["turn"] for t in turns if t.get("anchor_applied")]
        if tel.get("anchor_turns") != want_anchor:
            tel_ok = False
            tel_detail.append("%s anchor_turns 与逐轮不一致" % c)
        if tel.get("fallback_count") != sum(1 for t in turns if t.get("fallback_applied")):
            tel_ok = False
            tel_detail.append("%s fallback_count 不一致" % c)
    ck.check("C39", "遥测与逐轮记录一致（未越阈时显式 []）", tel_ok, tel_detail)

    # ── 不得用 0 顶替：凡有 *_reason 的键其本键必须为 null ──
    sub_ok, sub_detail = True, []
    pairs = [("sfr_id", "sfr_id_reason"), ("iir", "iir_reason"),
             ("nhd", "nhd_reason"), ("max_ratio", "max_ratio_reason"),
             ("ji_held", "ji_held_reason")]
    for c in conds:
        blobs = [conds[c].get("metrics") or {}, conds[c].get("telemetry") or {}]
        for b in blobs:
            for base, why in pairs:
                if b.get(why) and b.get(base) is not None:
                    sub_ok = False
                    sub_detail.append("%s: %s 有 reason 但值非 null（疑似 0 顶替）" % (c, base))
    if summary.get("jailbreak_held_reason") and summary.get("jailbreak_held") is not None:
        sub_ok = False
        sub_detail.append("summary.jailbreak_held 有 reason 但非 null")
    ck.check("C40", "缺指标为 null+reason（禁止 0 顶替）", sub_ok, sub_detail)

    # ── side effects ──────────────────────────────────
    se = res.get("side_effects") or {}
    ck.check("C41", "side_effects 声明无外部写入/网络",
             se.get("writes_outside_results") == 0 and se.get("model_calls") == 0
             and se.get("network_calls") == 0)
    ck.check("C42", "python 版本已记录", bool((res.get("engine") or {}).get("python")))
    ck.check("C43", "三臂 param_version 已记录且 baseline/basic 与 full 不同",
             len(set((res.get("engine") or {}).get("param_version", {}).values())) == 3)
    vb = res.get("verify")
    ck.check("C44", "verify 旁证若已嵌入则必须 exit_code==0 且无失败项",
             vb is None or (vb.get("exit_code") == 0 and not vb.get("checks_failed")),
             None if vb is None else (vb.get("exit_code"), vb.get("checks_failed")))
    if getattr(ck, "require_verify", False):
        ck.check("C44b", "终版必须已嵌入 verify 旁证（--require-verify）", vb is not None)
    tel_ok2 = all(isinstance((conds[c].get("telemetry") or {}).get("budget_truncated_turns"),
                             int) for c in conds)
    ck.check("C45", "telemetry.budget_truncated_turns 为数值（DESIGN §6 schema 字段）",
             tel_ok2,
             {c: (conds[c].get("telemetry") or {}).get("budget_truncated_turns")
              for c in conds})
    ck.check("C46", "budget_truncated_turns <= recall_incomplete_turns（口径自洽）",
             all(((conds[c].get("telemetry") or {}).get("budget_truncated_turns") or 0)
                 <= ((conds[c].get("telemetry") or {}).get("recall_incomplete_turns") or 0)
                 for c in conds),
             {c: ((conds[c].get("telemetry") or {}).get("budget_truncated_turns"),
                  (conds[c].get("telemetry") or {}).get("recall_incomplete_turns"))
              for c in conds})


def strict_checks(res: dict, ck: Checker) -> None:
    import gen_ablation_script as GEN
    import run_ablation as RUN

    # 1) 剧本可重生成且逐字节一致
    rows, chk = GEN.generate(RUN.WINDOW_CHARS)
    blob = "".join(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n" for r in rows)
    want = _sha256(blob.encode("utf-8"))
    got = (res.get("inputs") or {}).get("script_sha256")
    ck.check("S01", "重生成剧本逐字节一致（确定性）", want == got,
             "regen=%s file=%s fails=%r" % (want[:12], str(got)[:12], chk["fails"]))

    # 2) 整跑复现（同一 stamp），逐字段比对
    rid = res["run_id"]
    stamp = rid[len("abl-"):-len("-engine")]
    try:
        again = RUN.build_payload("paper/ablation/script-100.jsonl", stamp=stamp,
                                  seed=(res.get("permutations") or {}).get("seed", 20261027),
                                  n_perm=(res.get("permutations") or {}).get("R", 3),
                                  verify_block=res.get("verify"))
    except Exception as e:                                      # noqa: BLE001
        ck.check("S02", "整跑复现成功", False, repr(e))
        return
    volatile = {"started_at", "finished_at"}
    a = {k: v for k, v in res.items() if k not in volatile}
    b = {k: v for k, v in again.items() if k not in volatile}
    diff_keys = [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
    ck.check("S02", "整跑复现：除时间戳外全字段逐值相等", not diff_keys, diff_keys)
    if diff_keys:
        for k in diff_keys[:4]:
            print("    · 差异字段 %s" % k)
    # 3) 结果自述与实测的 summary 一致
    s = again.get("summary") or {}
    ck.check("S03", "summary 由脚本复现（conditions/probe/jailbreak 计数）",
             s.get("conditions_run") == 3 and s.get("probe_count") == 12
             and s.get("jailbreak_in") == 25 and s.get("jailbreak_script") == 6
             and s.get("jailbreak_fp_controls") == 294
             and s.get("model_calls") == 0, s)
    # 4) verify 旁证已嵌入（收尾要求）
    vb = res.get("verify") or {}
    ck.check("S04", "结果 JSON 已嵌入 verify 旁证（--require-verify 时强制）",
             bool(vb) or not getattr(ck, "require_verify", False),
             "未嵌入 verify 旁证")


# ══════════════════════════════════════════════════════════
# S1（9 臂 × 3 剧本 × 48 探针）机检
# ══════════════════════════════════════════════════════════

S1_ARMS = ("baseline", "basic", "full", "full_minus_actr", "full_minus_anchor",
           "full_minus_gate", "full_minus_variety", "full_minus_ushape",
           "full_minus_integral")
S1_SCRIPTS = ("sA", "sB", "sC")
S1_CLASSES = ("A", "B", "C", "D")


def _s1_class_sfr(probes: list[dict], cls: str, key: str = "hit_text"):
    sub = [p for p in probes if p.get("probe_class") == cls]
    vals = [p[key] for p in sub if p.get(key) is not None]
    if not vals:
        return None
    return _r(1.0 - sum(1.0 if v else 0.0 for v in vals) / len(vals))


def _s1_sign(pairs: list[tuple[bool, bool]]) -> dict:
    m = sum(1 for a, b in pairs if b and not a)
    return {"m": m, "p": 1.0 if m == 0 else _r(2.0 ** (-m), 10)}


def s1_static_checks(res: dict, path: str, ck: Checker) -> None:
    inputs = res.get("inputs") or {}
    bank = res.get("probe_bank") or {}
    scripts = res.get("scripts") or {}
    arms = res.get("arms") or {}
    stats = res.get("statistics") or {}
    summary = res.get("summary") or {}
    pre = res.get("prereg_evaluation") or []

    ck.check("C01", "schema/design_version 正确",
             res.get("schema") == "lingshu/ablation-result/v1"
             and res.get("design_version") == "ablation-supplement-s1-v0.1",
             (res.get("schema"), res.get("design_version")))
    ck.check("C02", "mode=engine / zero_model / model_calls=0 / deterministic",
             res.get("mode") == "engine" and res.get("zero_model") is True
             and res.get("model_calls") == 0 and res.get("deterministic") is True)
    ck.check("C03", "valid=true", res.get("valid") is True)
    rid = str(res.get("run_id") or "")
    stem = os.path.splitext(os.path.basename(path))[0]
    ck.check("C04", "run_id 与文件名一致且形如 abl-<date>-s1-engine",
             rid == stem and re.fullmatch(r"abl-\d{8}-s1-engine", rid) is not None,
             "%r vs %r" % (rid, stem))
    try:
        t0 = datetime.fromisoformat(str(res["started_at"]))
        t1 = datetime.fromisoformat(str(res["finished_at"]))
        ck.check("C05", "时间戳可解析且 finished>=started", t1 >= t0)
    except Exception as e:                                      # noqa: BLE001
        ck.check("C05", "时间戳可解析且 finished>=started", False, e)

    # ── 冻结契约（PLAN/FREEZE） ────────────────────────
    sup = res.get("supplement") or {}
    freeze_path = os.path.join(ROOT, sup.get("freeze") or "paper/supplement/FREEZE.json")
    ok_freeze = os.path.isfile(freeze_path)
    if ok_freeze:
        raw = open(freeze_path, "rb").read()
        fx = json.loads(raw.decode("utf-8"))
        ok_freeze = (_sha256(raw) == sup.get("freeze_sha256")
                     and fx.get("sha256") == sup.get("plan_sha256")
                     and fx.get("status") == "frozen-before-execution")
    ck.check("C06", "FREEZE.json 存在、sha 一致、状态 frozen-before-execution", ok_freeze)
    plan_path = os.path.join(ROOT, sup.get("plan") or "paper/supplement/PLAN.md")
    ok_plan = os.path.isfile(plan_path) and \
        _sha256(open(plan_path, "rb").read()) == sup.get("plan_sha256")
    ck.check("C07", "PLAN.md sha256 与冻结清单一致（跑前冻结未被改）", ok_plan)
    sfreeze = json.load(open(os.path.join(ROOT, "paper/ablation/script-freeze-s1.json"),
                             encoding="utf-8")) if os.path.isfile(
        os.path.join(ROOT, "paper/ablation/script-freeze-s1.json")) else {}
    ck.check("C08", "剧本冻结清单存在且与结果一致",
             bool(sfreeze) and sfreeze.get("probe_bank", {}).get("n") == 48
             and {k: sfreeze["scripts"][k]["sha256"] for k in S1_SCRIPTS}
             == {k: (scripts.get(k) or {}).get("sha256") for k in S1_SCRIPTS})

    # ── 探针库 48 / 每类 12 / 每剧本 16 ───────────────
    probes = bank.get("probes") or []
    by_class = {c: sum(1 for p in probes if p.get("probe_class") == c)
                for c in S1_CLASSES}
    ck.check("C09", "探针库 48 条、每类 12、id 唯一",
             len(probes) == 48 and all(by_class[c] == 12 for c in S1_CLASSES)
             and len({p.get("probe_id") for p in probes}) == len(probes),
             (len(probes), by_class))
    ck.check("C10", "探针 by_class/by_script 计数与探针库一致",
             bank.get("by_class") == by_class
             and all((bank.get("by_script") or {}).get(k) == 16 for k in S1_SCRIPTS))
    bank_file = os.path.join(ROOT, inputs.get("probe_bank") or "")
    ok_bank = os.path.isfile(bank_file) and \
        _sha256(open(bank_file, "rb").read()) == inputs.get("probe_bank_sha256")
    ck.check("C11", "探针库文件存在且 sha256 一致", ok_bank)
    ck.check("C12", "探针文本 sha256 自校验通过",
             all(_sha256(str(p.get("text", "")).encode("utf-8")) == p.get("sha256")
                 for p in probes))

    # ── 三剧本：100 轮 / 16 探针 / 每类 4 / sha / 冻结 ──
    script_ok, script_detail = True, []
    probe_turn_ok = True
    perms = set()
    bank_by_id = {p["probe_id"]: p for p in probes}
    seen_probe_ids = []
    for k in S1_SCRIPTS:
        meta = scripts.get(k) or {}
        p = os.path.join(ROOT, meta.get("path") or "")
        if not os.path.isfile(p):
            script_ok = False
            script_detail.append("%s 缺文件" % k)
            continue
        raw = open(p, "rb").read()
        if _sha256(raw) != meta.get("sha256") or \
                _sha256(raw) != sfreeze.get("scripts", {}).get(k, {}).get("sha256"):
            script_ok = False
            script_detail.append("%s sha 不符" % k)
        rows = [json.loads(l) for l in raw.decode("utf-8").splitlines() if l.strip()]
        if len(rows) != 100 or [r["turn"] for r in rows] != list(range(1, 101)):
            script_ok = False
            script_detail.append("%s 轮数/编号错" % k)
        prows = [r for r in rows if r.get("probe_id")]
        if len(prows) != 16:
            script_ok = False
            script_detail.append("%s 探针数 %d" % (k, len(prows)))
        cls_cnt = {c: sum(1 for r in prows if r.get("probe_class") == c)
                   for c in S1_CLASSES}
        if any(cls_cnt[c] != 4 for c in S1_CLASSES):
            script_ok = False
            script_detail.append("%s 子类计数 %r" % (k, cls_cnt))
        if meta.get("max_ratio", 0) < 0.85 or not meta.get("phase_trigger_turns"):
            script_ok = False
            script_detail.append("%s ratio/相位不足" % k)
        perms.add(meta.get("permutation_id"))
        seen_probe_ids.extend(r["probe_id"] for r in prows)
        # 探针文本 / 期望 / turn 与探针库 + 实际行一致
        for r in prows:
            b = bank_by_id.get(r["probe_id"])
            if b is None or b["text"] != r["text"] or \
                    (b.get("expect") or {}).get("expected_ids") != \
                    (r.get("expect") or {}).get("expected_ids"):
                probe_turn_ok = False
                script_detail.append("%s %s 与探针库不一致" % (k, r["probe_id"]))
        if [r["turn"] for r in prows] != list(range(85, 101)):
            script_ok = False
            script_detail.append("%s 探针轮次不是 85–100" % k)
    ck.check("C13", "三剧本：100 轮 / 16 探针 / 每类 4 / sha 一致 / 越过 0.85",
             script_ok, script_detail)
    ck.check("C14", "探针落点为实际轮次（85–100）且与探针库逐字一致", probe_turn_ok,
             script_detail[-3:])
    ck.check("C15", "三剧本覆盖三个不同固定置换（p0/p1/p2）",
             perms == {"p0", "p1", "p2"}, perms)
    ck.check("C16", "48 条探针在剧本中恰好各出现一次",
             sorted(seen_probe_ids) == sorted(bank_by_id), len(seen_probe_ids))

    # ── 9 臂结构 ──────────────────────────────────────
    ck.check("C17", "9 个臂齐全（PLAN §1.2 逐字）",
             set(arms) == set(S1_ARMS), sorted(set(S1_ARMS) - set(arms)))
    arm_ok, arm_detail = True, []
    for a in S1_ARMS:
        blob = arms.get(a) or {}
        for k in S1_SCRIPTS:
            sc = (blob.get("scripts") or {}).get(k) or {}
            if len(sc.get("turns") or []) != 100:
                arm_ok = False
                arm_detail.append("%s/%s turns" % (a, k))
            if len(sc.get("probes") or []) != 16:
                arm_ok = False
                arm_detail.append("%s/%s probes" % (a, k))
        pm = (blob.get("pooled") or {}).get("metrics", {}).get(
            "m1_setting_forgetting", {})
        for c in S1_CLASSES:
            if (pm.get("by_subtype") or {}).get(c) is None:
                arm_ok = False
                arm_detail.append("%s/%s pooled by_subtype" % (a, c))
        if not (blob.get("callset") and blob.get("param_version")):
            arm_ok = False
            arm_detail.append("%s callset/param_version" % a)
    ck.check("C18", "每臂 × 每剧本：100 轮 / 16 探针 / pooled 四类齐全", arm_ok,
             arm_detail[:8])
    fps_ok = True
    fp_detail = []
    for a in S1_ARMS:
        callset = (arms[a].get("callset") or {})
        blob = json.dumps(callset, ensure_ascii=False).lower()
        for tok in FROZEN_TOKENS:
            if tok in blob:
                fps_ok = False
                fp_detail.append("%s: %s" % (a, tok))
    ck.check("C19", "9 臂 callset 未出现四冻结方向", fps_ok, fp_detail)

    # ── 指标可由 evidence 复算 ────────────────────────
    rec_ok, rec_detail = True, []
    for a in S1_ARMS:
        blob = arms.get(a) or {}
        all_probes = []
        for k in S1_SCRIPTS:
            psub = ((blob.get("scripts") or {}).get(k) or {}).get("probes") or []
            all_probes.extend(psub)
            m1s = (((blob.get("scripts") or {}).get(k) or {}).get("metrics") or {}).get(
                "m1_setting_forgetting") or {}
            for c in S1_CLASSES:
                want = _s1_class_sfr(psub, c)
                if not _num_eq((m1s.get("by_subtype") or {}).get(c), want):
                    rec_ok = False
                    rec_detail.append("%s/%s/%s by_subtype 复算不符" % (a, k, c))
        pm = (blob.get("pooled") or {}).get("metrics", {}).get(
            "m1_setting_forgetting", {})
        for c in S1_CLASSES:
            want = _s1_class_sfr(all_probes, c)
            if not _num_eq((pm.get("by_subtype") or {}).get(c), want):
                rec_ok = False
                rec_detail.append("%s pooled %s 复算不符" % (a, c))
            want_id = _s1_class_sfr(all_probes, c, "hit_id")
            if a == "baseline":
                if (pm.get("by_subtype_id") or {}).get(c) is not None:
                    rec_ok = False
                    rec_detail.append("baseline %s by_subtype_id 应为 null" % c)
            elif not _num_eq((pm.get("by_subtype_id") or {}).get(c), want_id):
                rec_ok = False
                rec_detail.append("%s pooled %s by_subtype_id 复算不符" % (a, c))
        if a == "baseline":
            if pm.get("sfr_id") is not None or not pm.get("sfr_id_reason"):
                rec_ok = False
                rec_detail.append("baseline sfr_id 应为 null+reason")
    ck.check("C20", "SFR（每剧本 + pooled 四类，text/id 双口径）可由 probes 复算",
             rec_ok, rec_detail[:8])

    # ── 符号检验复算 ──────────────────────────────────
    st_ok, st_detail = True, []
    tests = stats.get("sign_tests") or []
    pairs_def = [("basic", "full")] + [(m, "full") for m in S1_ARMS if
                                       m.startswith("full_minus_")]
    got_keys = {(t.get("a"), t.get("b"), t.get("class"), t.get("metric"))
                for t in tests}
    for a, b in pairs_def:
        for c in S1_CLASSES:
            for key in ("hit_text", "hit_id"):
                if (a, b, c, key) not in got_keys:
                    st_ok = False
                    st_detail.append("缺 %s/%s/%s/%s" % (a, b, c, key))
    for t in tests:
        c = t.get("class")
        a, b = t.get("a"), t.get("b")
        probes_a = [p for k in S1_SCRIPTS
                    for p in (((arms.get(a) or {}).get("scripts") or {}).get(k)
                              or {}).get("probes") or [] if p.get("probe_class") == c]
        probes_b = [p for k in S1_SCRIPTS
                    for p in (((arms.get(b) or {}).get("scripts") or {}).get(k)
                              or {}).get("probes") or [] if p.get("probe_class") == c]
        key = t.get("metric") or "hit_text"
        pa = {(k, p["probe_id"]): p.get(key) for k in S1_SCRIPTS
              for p in (((arms.get(a) or {}).get("scripts") or {}).get(k)
                        or {}).get("probes") or [] if p.get("probe_class") == c}
        pb = {(k, p["probe_id"]): p.get(key) for k in S1_SCRIPTS
              for p in (((arms.get(b) or {}).get("scripts") or {}).get(k)
                        or {}).get("probes") or [] if p.get("probe_class") == c}
        keys_ = sorted(set(pa) & set(pb))
        pairs = [(bool(pa[x]), bool(pb[x])) for x in keys_]
        want = _s1_sign(pairs)
        if t.get("paired_n") != 12 or t.get("discordant_favor_b") != want["m"] or \
                not _num_eq(t.get("p"), want["p"]):
            st_ok = False
            st_detail.append("%s/%s/%s/%s 复算不符" % (a, b, c, key))
        if t.get("alpha_bonferroni") != 0.0125:
            st_ok = False
            st_detail.append("alpha != 0.0125")
        if bool(t.get("significant")) != bool(want["m"] > 0 and want["p"] <= 0.0125):
            st_ok = False
            st_detail.append("significance 判定不符")
    ck.check("C21", "每类 12 对精确符号检验可由 probes 复算（α=0.0125）", st_ok,
             st_detail[:8])
    ck.check("C22", "Bonferroni α=0.0125 且 MDD=7 已落盘",
             stats.get("alpha_bonferroni") == 0.0125
             and stats.get("bonferroni_family_size") == 4
             and stats.get("sign_test_mdd_discordant_pairs") == 7)
    rng_ok = True
    for a in S1_ARMS:
        for c in S1_CLASSES:
            r = (stats.get("cross_script_range") or {}).get(a, {}).get(c) or {}
            pt = (stats.get("per_script_point_estimates") or {}).get(a, {})
            vals = [pt.get(k, {}).get(c) for k in S1_SCRIPTS]
            if r.get("values") != vals or not _num_eq(r.get("min"), min(vals)) \
                    or not _num_eq(r.get("max"), max(vals)):
                rng_ok = False
    ck.check("C23", "每剧本点估计与跨剧本极差一致", rng_ok)

    # ── 预注册 ────────────────────────────────────────
    ids = {p.get("id") for p in pre}
    ck.check("C24", "预注册 P-S1a..d 逐条给 observed/pass",
             ids >= {"P-S1a", "P-S1b", "P-S1c", "P-S1d"}
             and all(isinstance(p.get("pass"), bool) and p.get("observed") is not None
                     for p in pre),
             sorted(ids))
    ck.check("C25", "summary.prereg_pass 与逐条判定一致",
             (summary.get("prereg_pass") or {}) == {p["id"]: p["pass"] for p in pre})
    ck.check("C26", "D 类 n=12 且 P-S1a 的观测值与 pooled 一致",
             (pooled_get(arms, "basic", "D") is not None))
    # D 类 12 对方向核验
    d_ok = True
    d_detail = []
    t_d = [t for t in tests if t.get("a") == "basic" and t.get("class") == "D"
           and t.get("metric") == "hit_text"]
    if not t_d:
        d_ok = False
        d_detail.append("缺 basic/full D 类符号检验")
    else:
        if t_d[0].get("paired_n") != 12 or t_d[0].get("discordant_favor_b") not in range(13):
            d_ok = False
            d_detail.append("D 类配对数/同向数异常")
    ck.check("C27", "D 类 12 对（3 剧本 × 4 探针）已对齐", d_ok, d_detail)

    # ── 等价性 / 诚实性 / 空值纪律 ────────────────────
    eq = res.get("equivalence_check") or {}
    ck.check("C28", "等价性 pass 且 300 轮",
             eq.get("full_vs_pipeline_session") == "pass"
             and eq.get("turns_compared") == 300
             and eq.get("mismatch_count") == 0
             and set(eq.get("fields") or []) >= set(EQ_FIELDS),
             (eq.get("full_vs_pipeline_session"), eq.get("turns_compared")))
    hon = res.get("honesty") or {}
    ck.check("C29", "honesty: measured=0 / provenance=guess",
             hon.get("measured_parameters") == 0
             and hon.get("parameter_provenance") == "guess")
    ck.check("C30", "validity_flags 含 S1 必报项",
             {"probe_bank_wiring_sanity_pre_freeze", "mechanism_arms_coupled_to_gate",
              "clustering_within_script"} <= set(res.get("validity_flags") or []))
    null_ok, null_detail = True, []
    for a in ("baseline", "basic"):
        m3 = ((arms.get(a) or {}).get("pooled") or {}).get("m3_jailbreak") or {}
        if any(m3.get(x) is not None for x in ("ji_in", "ji_held", "ji_fp", "ji_script")):
            null_ok = False
            null_detail.append("%s 的 JI 应为 null" % a)
        if not m3.get("reason"):
            null_ok = False
            null_detail.append("%s 缺 reason" % a)
        m1 = ((arms.get(a) or {}).get("pooled") or {}).get("metrics", {}).get(
            "m1_setting_forgetting") or {}
        if a == "baseline" and (m1.get("sfr_id") is not None
                                or not m1.get("sfr_id_reason")):
            null_ok = False
            null_detail.append("baseline sfr_id 应为 null+reason")
    for a in S1_ARMS:
        m3 = ((arms.get(a) or {}).get("pooled") or {}).get("m3_jailbreak") or {}
        if m3.get("ji_held") is not None or not m3.get("ji_held_reason"):
            null_ok = False
            null_detail.append("%s ji_held 应为 null+reason" % a)
        if a == "full_minus_gate" and m3.get("gate_absent") is not True:
            null_ok = False
            null_detail.append("−gate 臂应标 gate_absent")
        if a != "full_minus_gate" and a.startswith("full_") and \
                m3.get("gate_absent") is True:
            null_ok = False
            null_detail.append("%s 不应 gate_absent" % a)
    ck.check("C31", "缺指标 null+reason 纪律（baseline/basic JI、ji_held、sfr_id）",
             null_ok, null_detail[:6])

    # ── 机制遥测自洽 ──────────────────────────────────
    tel_ok, tel_detail = True, []
    for a in S1_ARMS:
        t = (arms[a].get("pooled") or {}).get("telemetry") or {}
        for k in ("blocked_turns_total", "fallback_count_total",
                  "anchor_turn_count_total", "uca08_count_total"):
            if t.get(k) is None:
                tel_ok = False
                tel_detail.append("%s 缺 %s" % (a, k))
        f = arms[a].get("features") or {}
        if a in ("baseline", "basic"):
            if any((t.get(k) or 0) != 0 for k in ("fallback_count_total",
                                                  "anchor_turn_count_total",
                                                  "uca08_count_total")):
                tel_ok = False
                tel_detail.append("%s 无控制面但遥测非 0" % a)
        else:
            if not f.get("integral") and (t.get("uca08_count_total") or 0) != 0:
                tel_ok = False
                tel_detail.append("−integral 的 uca08 应为 0")
            if not f.get("gate") and (t.get("uca08_count_total") or 0) != 0:
                tel_ok = False
                tel_detail.append("−gate 无 downgrades ⇒ uca08 应为 0")
            if not f.get("anchor") and (t.get("anchor_turn_count_total") or 0) != 0:
                tel_ok = False
                tel_detail.append("−anchor 的 anchor_turns 应为 0")
            if not f.get("variety") and (t.get("fallback_count_total") or 0) != 0:
                tel_ok = False
                tel_detail.append("−variety 的 fallback 应为 0")
            if f.get("gate") and f.get("anchor") and f.get("integral") and \
                    (t.get("uca08_count_total") or 0) == 0:
                tel_ok = False
                tel_detail.append("%s 应产生 UCA-08 留痕" % a)
    ck.check("C32", "机制遥测与臂定义自洽（−gate/−integral/−anchor/−variety）",
             tel_ok, tel_detail[:8])

    # ── 输入指纹 / 冻结面 / 旁证 ─────────────────────
    corp_ok = all(os.path.isfile(os.path.join(ROOT, rel))
                  and _sha256(open(os.path.join(ROOT, rel), "rb").read()) == sha
                  for rel, sha in (inputs.get("corpora_sha256") or {}).items())
    ck.check("C33", "语料指纹与磁盘一致（frozen 未被改）", corp_ok)
    eng_ok = all(os.path.isfile(os.path.join(ROOT, rel))
                 and _sha256(open(os.path.join(ROOT, rel), "rb").read()) == sha
                 for rel, sha in (res.get("engine") or {}).get("engine_sha256",
                                                              {}).items())
    ck.check("C34", "engine 文件指纹与磁盘一致（未被改）", eng_ok)
    ck.check("C35", "summary 计数自洽",
             summary.get("arms_run") == 9 and summary.get("scripts_run") == 3
             and summary.get("probe_count") == 48 and summary.get("cells") == 27
             and summary.get("probes_per_script") == 16
             and summary.get("probe_count_per_class") ==
             {c: 12 for c in S1_CLASSES})
    ck.check("C36", "notes.deviations 非空（PLAN/代码偏差已记录）",
             bool((res.get("notes") or {}).get("deviations")))
    se = res.get("side_effects") or {}
    ck.check("C37", "side_effects：无模型调用/网络调用",
             se.get("model_calls") == 0 and se.get("network_calls") == 0)
    vb = res.get("verify")
    ck.check("C38", "verify 旁证若已嵌入则 exit_code==0 且无失败项",
             vb is None or (vb.get("exit_code") == 0 and not vb.get("checks_failed")),
             None if vb is None else (vb.get("exit_code"), vb.get("checks_failed")))
    ck.check("C39", "S2 留出集未产出时 ji_held 为 null（不编造）",
             all(((arms[a].get("pooled") or {}).get("m3_jailbreak") or {})
                 .get("ji_held") is None for a in S1_ARMS if a.startswith("full")))


def pooled_get(arms: dict, arm: str, cls: str):
    pm = ((arms.get(arm) or {}).get("pooled") or {}).get("metrics", {}).get(
        "m1_setting_forgetting", {})
    return (pm.get("by_subtype") or {}).get(cls)


def s1_strict_checks(res: dict, ck: Checker) -> None:
    import gen_s1_script as G1
    import run_s1 as RUN

    # 1) 三剧本 + 探针库可重生成且逐字节一致
    rows_by_script = {k: G1.build_rows(idx) for k, _p, idx in G1.SCRIPTS}
    ok = True
    detail = []
    scripts = res.get("scripts") or {}
    for k in S1_SCRIPTS:
        blob = ("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True)
                          for r in rows_by_script[k]) + "\n")
        want = _sha256(blob.encode("utf-8"))
        got = (scripts.get(k) or {}).get("sha256")
        if want != got:
            ok = False
            detail.append("%s regen=%s file=%s" % (k, want[:12], str(got)[:12]))
    bank = [p for i in range(G1.N_SCRIPTS) for p in G1.probe_bank(i)]
    bank_blob = "\n".join(json.dumps({"probe_id": p["probe_id"],
                                      "probe_class": p["probe_class"],
                                      "text": p["text"], "expect": p["expect"],
                                      "sha256": p["sha256"]},
                                     ensure_ascii=False, sort_keys=True)
                          for p in bank) + "\n"
    bank_file = os.path.join(ROOT, "paper/ablation/probe-bank-s1.jsonl")
    if os.path.isfile(bank_file) and \
            _sha256(open(bank_file, "rb").read()) != _sha256(bank_blob.encode("utf-8")):
        ok = False
        detail.append("probe-bank 重生成不一致")
    ck.check("S01", "重生成三剧本 + 探针库逐字节一致（确定性）", ok, detail)

    # 2) 整跑复现（同一 stamp），逐字段比对
    rid = res["run_id"]
    stamp = rid[len("abl-"):-len("-s1-engine")]
    try:
        again = RUN.build_payload(stamp=stamp, verify_block=res.get("verify"))
    except Exception as e:                                      # noqa: BLE001
        ck.check("S02", "整跑复现成功", False, repr(e))
        return
    volatile = {"started_at", "finished_at"}
    a = {k: v for k, v in res.items() if k not in volatile}
    b = {k: v for k, v in again.items() if k not in volatile}
    diff = [k for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
    ck.check("S02", "整跑复现：除时间戳外全字段逐值相等", not diff, diff[:6])
    vb = res.get("verify") or {}
    ck.check("S03", "结果 JSON 已嵌入 verify 旁证（--require-verify 时强制）",
             bool(vb) or not getattr(ck, "require_verify", False), "未嵌入")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--result", required=True)
    ap.add_argument("--strict", action="store_true")
    ap.add_argument("--write-exit-note", action="store_true",
                    help="把 verify 结果写成 results/<run_id>.verify.json（旁证，不改结果 JSON）")
    ap.add_argument("--require-verify", action="store_true",
                    help="终版验收：结果 JSON 必须已嵌入 verify 旁证")
    args = ap.parse_args()

    path = args.result if os.path.isabs(args.result) else os.path.join(ROOT, args.result)
    if not os.path.isfile(path):
        print("FATAL: 结果文件不存在：%s" % path)
        return 2
    res = json.load(open(path, encoding="utf-8"))
    is_s1 = (res.get("design_version") == "ablation-supplement-s1-v0.1"
             or "probe_bank" in res) and "arms" in res

    print("verify_ablation.py  result=%s  strict=%s  kind=%s"
          % (path, args.strict, "S1(9 arms)" if is_s1 else "S0(3 arms)"))
    ck = Checker()
    ck.require_verify = bool(args.require_verify)
    if is_s1:
        s1_static_checks(res, path, ck)
        if args.strict:
            s1_strict_checks(res, ck)
    else:
        static_checks(res, path, ck)
        if args.strict:
            strict_checks(res, ck)
    code = ck.report()
    print("退出码：%d" % code)

    if args.write_exit_note:
        note = {"result": _rel_public(path), "strict": bool(args.strict),
                "require_verify": bool(args.require_verify),
                "schema_kind": "s1" if is_s1 else "s0",
                "exit_code": code,
                "checks": [{"id": c, "title": t, "pass": ok,
                            **({"detail": str(d)[:600]} if (d and not ok) else {})}
                           for c, t, ok, d in ck.rows]}
        out = os.path.splitext(path)[0] + ".verify.json"
        with open(out, "w", encoding="utf-8", newline="\n") as f:
            json.dump(note, f, ensure_ascii=False, indent=1, sort_keys=True)
            f.write("\n")
        print("verify 旁证已写出：%s" % out)
    return code


def _rel_public(p: str) -> str:
    try:
        return os.path.relpath(p, ROOT).replace("\\", "/")
    except ValueError:
        return p


if __name__ == "__main__":
    raise SystemExit(main())
