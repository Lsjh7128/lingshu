# -*- coding: utf-8 -*-
"""F2 · `f2_phase_boundary` —— 相变边界扫描（resource / PhaseShiftMonitor）

归属：`docs/23-混沌工程v2-科学压测设计.md` §5.2（刺激集）、§4（不变量 INV-10/13/14）、
`docs/19` §2.3（相变监控语义：`ratio = used/window`，先判 hard 再判 ratio）。

本族做四件事（纯函数、确定性、不调模型、不写盘）：

1. **逐点扫描**：固定 `window`，只变 `used`，用 `PhaseShiftMonitor.observe(used, window)`
   得到 `{ratio, phase, trend, prefeed, anchor_required}`，逐点与 `row.expect` 比对；
   `row.input.history` 用 `observe` **预喂**实现（ramp/flat 的趋势对照）。
2. **锚点渲染**：`apply_persona_anchor(prompt, monitor_result)` —— normal 必须**不改写**；
   触发（`prefeed` 或 `anchor_required`）必须首含 `ANCHOR_PREFIX`、尾含 `ANCHOR_SUFFIX`。
3. **canary**：`phase_result.prefeed=true` 但 prompt 无锚点 → `preflight_audit` 必须报
   `UCA-07`（证明该 UCA 不是永不触发的死代码）。
4. **数值有界**：`0 ≤ ratio ≤ ratioMax`（含超窗口截断与负 used 的钳制探针）。

诚实边界（必读）：

- 这些是**合成边界样本**，只能证明"检测器在阈值附近行为正确"，
  **不得**计入 `docs/20` §4.1 的"边界样本 ≥10"（那是真实 turn 的门槛）。
- `0.70/0.85` 是 `rules/decay-params.yaml#control` 的**当前值**，不是契约常量
  （`docs/19` §2.3 注）；本族全部阈值从 `ctx.cfg["control"]` 读，不硬编码。
- 本族不证明阈值正确（docs/23 §9"不覆盖清单"）。
"""
from __future__ import annotations

import copy
import math
import sys
from typing import Any

FAMILY_ID = "f2_phase_boundary"
STRESS_CLASS = "resource"     # 上下文用量是资源维度（docs/23 §5.2 标题另含 temporal）
# docs/23 §4：数值有界 / 相变映射精确 / 锚点真的在首尾
INVARIANTS = ("INV-10", "INV-13", "INV-14")

_DEFAULT_WINDOW = 100000      # docs/23 §5.2：固定 window，只变 used
_BOUNDARY_LO = 0.60
_BOUNDARY_HI = 0.80
_BOUNDARY_MIN_SAMPLES = 10    # docs/23 §5.2/§6 要求 ≥10

_AUDIT_DEFAULTS: dict[str, Any] = {
    "prompt": "",
    "frozen_ok": True,
    "refuse": False,
    "style_injected": False,
    "constitution_injected": True,
    "conflict": "none",
    "fallback_applied": False,
    "phase_result": None,
    "fact_block": "",
    "saturated": None,
}


# ── 小工具 ───────────────────────────────────────────

def _skipped(reason: str) -> dict[str, Any]:
    return {
        "checks": {"cases": 0, "expectations": 0, "failed": 0, "failures": []},
        "metrics": {},
        "rates": {},
        "samples": [],
        "notes": ["skipped: %s" % reason],
        "invariants": {inv: {"pass": False, "detail": "未执行（skipped）：%s" % reason}
                       for inv in INVARIANTS},
        "skipped": True,
        "skip_reason": reason,
    }


def _load_engine(ctx: dict[str, Any]) -> dict[str, Any]:
    root = str(ctx.get("repo_root") or "")
    if root and root not in sys.path:
        sys.path.insert(0, root)
    from engine import config as CFG
    from engine import control as CT
    return {"CFG": CFG, "CT": CT}


def _cfg_of(ctx: dict[str, Any], CFG) -> dict[str, Any]:
    cfg = ctx.get("cfg")
    if isinstance(cfg, dict) and cfg:
        return copy.deepcopy(cfg)
    return CFG.default()


class _Failures:
    def __init__(self) -> None:
        self.items: list[dict[str, Any]] = []

    def check(self, cond: bool, case: str, want: Any, got: Any, note: str = "") -> bool:
        if not cond:
            self.items.append({"case": case, "want": want, "got": got, "note": note})
        return bool(cond)


def _hist_used(h: Any, window: int) -> int | None:
    """history 元素：int = used tokens；float(≤3) = 比率（换算回 used）。"""
    if isinstance(h, bool):
        return None
    if isinstance(h, int):
        return h
    if isinstance(h, float):
        if h <= 3.0:
            return int(round(h * window))
        return int(round(h))
    return None


def _audit_kwargs(inp: dict[str, Any]) -> dict[str, Any]:
    kw = dict(_AUDIT_DEFAULTS)
    for key in _AUDIT_DEFAULTS:
        if key in inp:
            kw[key] = inp[key]
    kw["prompt"] = str(kw.get("prompt") or "")
    kw["fact_block"] = str(kw.get("fact_block") or "")
    kw["conflict"] = str(kw.get("conflict") or "none")
    for key, default in (("frozen_ok", True), ("refuse", False),
                         ("style_injected", False), ("constitution_injected", True),
                         ("fallback_applied", False)):
        kw[key] = bool(default if kw.get(key) is None else kw[key])
    kw["phase_result"] = kw.get("phase_result") if isinstance(kw.get("phase_result"), dict) else None
    kw["saturated"] = ({str(k): bool(v) for k, v in kw["saturated"].items()}
                       if isinstance(kw.get("saturated"), dict) else None)
    return kw


# ── 主入口 ───────────────────────────────────────────

def run(rows: list[dict[str, Any]], ctx: dict[str, Any]) -> dict[str, Any]:
    ctx = dict(ctx or {})
    raw_rows = list(rows or [])
    fam_rows = [r for r in raw_rows
                if isinstance(r, dict) and str(r.get("family") or "") == FAMILY_ID]
    if not fam_rows:
        return _skipped("rows 为空或 family 与 %s 不匹配（rows=%d）"
                        % (FAMILY_ID, len(raw_rows)))

    eng = _load_engine(ctx)
    CT, CFG = eng["CT"], eng["CFG"]
    cfg = _cfg_of(ctx, CFG)
    c = cfg["control"]
    th_ratio = float(c["phaseShiftRatio"])
    th_hard = float(c["phaseShiftHardRatio"])
    th_trend = float(c["trendThreshold"])
    ratio_max = float(c["ratioMax"])
    hist_cap = int(c["historyCapacity"])

    checks: dict[str, Any] = {"cases": 0, "expectations": 0, "failed": 0, "failures": []}
    failures = _Failures()
    metrics: dict[str, Any] = {}
    rates: dict[str, dict[str, int]] = {}
    samples: list[dict[str, Any]] = []
    notes: list[str] = []

    point_map: list[dict[str, Any]] = []
    point_total = point_match = 0
    point_mismatch: list[str] = []
    phase_counts: dict[str, int] = {"normal": 0, "shift": 0, "critical": 0, "unknown": 0}
    observed_ratios: list[float] = []
    boundary_samples = 0
    shift_without_trend = 0
    ramp_entries: list[dict[str, Any]] = []
    flat_entries: list[dict[str, Any]] = []
    anchor_total = anchor_consistent = anchor_changed_n = 0
    anchor_mismatch: list[str] = []
    anchor_normal_rows = 0
    canary_rows = canary_hits = 0
    clamp_probes: dict[str, Any] = {}
    window_defaulted = False
    rows_seen = 0

    for row in fam_rows:
        checks["cases"] += 1
        rows_seen += 1
        case = str(row.get("case") or "<unnamed>")
        inp = row.get("input") if isinstance(row.get("input"), dict) else {}
        exp = row.get("expect") if isinstance(row.get("expect"), dict) else {}
        row_kind = str(inp.get("kind") or "")

        is_point = ("used" in inp) or ("points" in inp)
        is_anchor = (not is_point) and (("prompt" in inp) or row_kind == "anchor")
        is_canary = row_kind == "canary" or (
            "UCA-07" in [str(x) for x in (exp.get("ucas") or [])])

        # ── (1) 逐点扫描
        if is_point:
            used = inp.get("used")
            window = inp.get("window")
            if window is None:
                window = _DEFAULT_WINDOW
                window_defaulted = True
            try:
                used_i, window_i = int(used), int(window)
            except (TypeError, ValueError):
                checks["expectations"] += 1
                failures.check(False, case, "input.used / input.window 为整数",
                               {"used": used, "window": window}, "语料行字段非法")
                continue
            history = inp.get("history") or []
            if not isinstance(history, list):
                history = [history]
            monitor = CT.PhaseShiftMonitor(cfg)
            bad_hist = False
            for h in history:
                hu = _hist_used(h, window_i)
                if hu is None:
                    checks["expectations"] += 1
                    failures.check(False, case, "history 元素为 int(used) 或 float(ratio)",
                                   {"history": history}, "history 元素类型非法")
                    bad_hist = True
                    break
                monitor.observe(hu, window_i)
            if bad_hist:
                continue
            res = monitor.observe(used_i, window_i)
            ratio_raw = (used_i / window_i) if window_i > 0 else 0.0
            observed_ratios.append(float(res.get("ratio") or 0.0))
            phase_counts[str(res.get("phase"))] = phase_counts.get(str(res.get("phase")), 0) + 1

            if _BOUNDARY_LO <= ratio_raw < _BOUNDARY_HI:
                boundary_samples += 1
            if res.get("phase") == "shift" and not res.get("anchor_required"):
                shift_without_trend += 1

            want_phase = exp.get("phase")
            want_prefeed = exp.get("prefeed")
            want_anchor = exp.get("anchor_required")
            n_expect = (1 if want_phase is not None else 0) + \
                       (1 if want_prefeed is not None else 0) + \
                       (1 if want_anchor is not None else 0)
            if n_expect == 0:
                checks["expectations"] += 1
                failures.check(False, case, "expect 至少给出 phase/prefeed/anchor_required 之一",
                               {"expect": exp}, "无可断言字段 → 不许静默通过")
                continue
            ok = True
            checks["expectations"] += n_expect
            if want_phase is not None:
                ok &= failures.check(str(res.get("phase")) == str(want_phase), case,
                                     {"phase": want_phase}, {"phase": res.get("phase")},
                                     "INV-13：0.70/0.85 分段映射必须精确")
            if want_prefeed is not None:
                ok &= failures.check(bool(res.get("prefeed")) == bool(want_prefeed), case,
                                     {"prefeed": bool(want_prefeed)},
                                     {"prefeed": res.get("prefeed")},
                                     "prefeed = phase ∈ {shift, critical}")
            if want_anchor is not None:
                ok &= failures.check(bool(res.get("anchor_required")) == bool(want_anchor),
                                     case, {"anchor_required": bool(want_anchor)},
                                     {"anchor_required": res.get("anchor_required")},
                                     "critical 恒真；shift 仅在 trend > trendThreshold 时真")
            point_total += 1
            if ok:
                point_match += 1
            else:
                point_mismatch.append(case)
            entry = {"case": case, "used": used_i, "window": window_i,
                     "history": history, "ratio": res.get("ratio"),
                     "trend": res.get("trend"), "phase": res.get("phase"),
                     "prefeed": res.get("prefeed"),
                     "anchor_required": res.get("anchor_required"), "match": bool(ok)}
            point_map.append(entry)
            if len(samples) < 10:
                samples.append(entry)
            tag = case.lower()
            if "ramp" in tag:
                ramp_entries.append(entry)
            if "flat" in tag:
                flat_entries.append(entry)
            continue

        # ── (2) canary：prefeed 却无锚点 → 必报 UCA-07
        if is_canary:
            canary_rows += 1
            kw = _audit_kwargs(inp)
            audit = CT.preflight_audit(**kw)
            checks["expectations"] += 2
            hit = failures.check("UCA-07" in (audit.get("ucas") or []), case,
                                 {"ucas 含": "UCA-07"}, {"ucas": audit.get("ucas")},
                                 "canary：prefeed=true 且 prompt 无锚点 → UCA-07 必须触发"
                                 "（否则该 UCA 是死代码）")
            failures.check(str(audit.get("verdict")) == "block", case,
                           {"verdict": "block"}, {"verdict": audit.get("verdict")},
                           "UCA-07 命中即熔断（fail-closed）")
            if hit:
                canary_hits += 1
            if len(samples) < 12:
                samples.append({"case": case, "kind": "canary",
                                "ucas": audit.get("ucas"),
                                "verdict": audit.get("verdict")})
            continue

        # ── (3) 锚点渲染
        if is_anchor:
            anchor_total += 1
            prompt = str(inp.get("prompt") or "")
            mr = inp.get("phase_result")
            if not isinstance(mr, dict):
                mr = inp.get("monitor") if isinstance(inp.get("monitor"), dict) else {}
            body = str(inp.get("anchor_body") or "")
            out = CT.apply_persona_anchor(prompt, mr, body)
            changed = out != prompt
            want_changed = exp.get("anchor_changed")
            if want_changed is None:
                want_changed = bool(mr.get("prefeed") or mr.get("anchor_required"))
            want_changed = bool(want_changed)
            ok = True
            checks["expectations"] += 1
            ok &= failures.check(changed == want_changed, case,
                                 {"anchor_changed": want_changed},
                                 {"anchor_changed": changed},
                                 "触发条件 = prefeed 或 anchor_required（两者取或）")
            if changed:
                checks["expectations"] += 2
                ok &= failures.check(out.startswith(CT.ANCHOR_PREFIX), case,
                                     {"首含": CT.ANCHOR_PREFIX[:8]},
                                     {"head": out[:16]},
                                     "INV-14：ANCHOR_PREFIX 在首")
                ok &= failures.check(out.endswith(CT.ANCHOR_SUFFIX), case,
                                     {"尾含": CT.ANCHOR_SUFFIX[:8]},
                                     {"tail": out[-16:]},
                                     "INV-14：ANCHOR_SUFFIX 在尾")
                checks["expectations"] += 1
                ok &= failures.check(prompt in out, case,
                                     {"原文保留": True}, {"原文保留": prompt in out},
                                     "锚点是包裹（head + prompt + tail），不改写正文")
                if body:
                    checks["expectations"] += 1
                    ok &= failures.check(body in out, case,
                                         {"anchor_body 在输出中": body},
                                         {"anchor_body_in_out": body in out},
                                         "自定义 anchor_body 必须原样进入锚点正文")
                anchor_changed_n += 1
            else:
                anchor_normal_rows += 1
                checks["expectations"] += 1
                ok &= failures.check(out == prompt, case, {"不改写": prompt},
                                     {"got": out}, "normal 不得改写 prompt")
            if ok:
                anchor_consistent += 1
            else:
                anchor_mismatch.append(case)
            if len(samples) < 12:
                samples.append({"case": case, "kind": "anchor",
                                "anchor_changed": changed,
                                "monitor": {k: mr.get(k) for k in
                                            ("phase", "ratio", "prefeed", "anchor_required")}})
            continue

        # ── 未知行类型：显式失败，不许静默跳过
        checks["expectations"] += 1
        failures.check(False, case,
                       "行类型 ∈ {point(used/window), anchor(prompt), canary}",
                       {"input_keys": sorted(inp.keys())},
                       "未知行类型 → 计入失败（docs/23 §5.2 行契约）")

    # ── INV-10 内部探针：ratio 截断与负 used 钳制（模块自建，不占语料行）
    mon_hi = CT.PhaseShiftMonitor(cfg)
    res_hi = mon_hi.observe(int(round(_DEFAULT_WINDOW * 10)), _DEFAULT_WINDOW)
    mon_lo = CT.PhaseShiftMonitor(cfg)
    res_lo = mon_lo.observe(-500, _DEFAULT_WINDOW)
    clamp_probes = {
        "over_window": {"used": int(round(_DEFAULT_WINDOW * 10)), "ratio": res_hi.get("ratio"),
                        "expect_ratio": ratio_max,
                        "ok": abs(float(res_hi.get("ratio") or 0.0) - ratio_max) <= 1e-9},
        "negative_used": {"used": -500, "ratio": res_lo.get("ratio"), "expect_ratio": 0.0,
                          "ok": abs(float(res_lo.get("ratio") or 0.0)) <= 1e-12},
        "history_capacity": {"capacity": hist_cap,
                             "monitor_history_len": len(mon_hi.history),
                             "ok": len(mon_hi.history) <= hist_cap},
    }
    ratio_bounded = all(0.0 <= r <= ratio_max + 1e-12 for r in observed_ratios)
    ratio_finite = all(math.isfinite(r) for r in observed_ratios)

    checks["failed"] = len(failures.items)
    checks["failures"] = failures.items

    # ramp/flat 对照：取"能代表语义"的那条——ramp 优先取趋势真的触发锚点的行，
    # flat 优先取 prefeed=true 但趋势未超阈值的行（对照组）。
    ramp = next((e for e in ramp_entries if e.get("anchor_required") is True),
                (ramp_entries[-1] if ramp_entries else None))
    flat = next((e for e in flat_entries
                 if e.get("prefeed") is True and e.get("anchor_required") is False),
                (flat_entries[-1] if flat_entries else None))
    ramp_flat = {
        "ramp": ramp,
        "flat": flat,
        "contrast_ok": bool(ramp and flat
                            and ramp.get("anchor_required") is True
                            and flat.get("prefeed") is True
                            and flat.get("anchor_required") is False),
        "note": ("ramp：历史 [0.60,0.66] → 0.72，trend=(0.72−0.60)/2=0.06>trendThreshold → "
                 "anchor_required=true；flat：历史 [0.72,0.72] → 0.72，trend=0 → "
                 "anchor_required=false 但 prefeed=true"),
    }

    boundary_ok = boundary_samples >= _BOUNDARY_MIN_SAMPLES
    inv10_pass = bool(ratio_bounded and ratio_finite
                      and all(v.get("ok") for v in clamp_probes.values()))
    inv13_pass = bool(point_total > 0 and not point_mismatch)
    inv14_pass = bool(anchor_total > 0 and not anchor_mismatch and canary_hits == canary_rows
                      and canary_rows > 0)

    invariants = {
        "INV-10": {
            "pass": inv10_pass,
            "detail": ("数值有界：%d 个观测点 ratio∈[0, ratioMax=%s]，trend 有限；截断探针 %r"
                       % (len(observed_ratios), ratio_max,
                          {k: v.get("ok") for k, v in clamp_probes.items()})),
        },
        "INV-13": {
            "pass": inv13_pass,
            "detail": ("相变映射精确：%d/%d 个扫描点 phase/prefeed/anchor_required 与期望一致"
                       "（阈值 shift=%s / hard=%s，来自 cfg.control）%s"
                       % (point_match, point_total, th_ratio, th_hard,
                          ("；不一致：%r" % point_mismatch) if point_mismatch
                          else ("；无扫描点（语料覆盖缺失）" if point_total == 0 else ""))),
        },
        "INV-14": {
            "pass": inv14_pass,
            "detail": ("锚点真的在首尾：%d 个渲染用例一致=%d（改写 %d / normal 不改写 %d）%s；"
                       "canary UCA-07 命中 %d/%d（证明该 UCA 不是死代码）"
                       % (anchor_total, anchor_consistent, anchor_changed_n, anchor_normal_rows,
                          ("；不一致：%r" % anchor_mismatch) if anchor_mismatch else "",
                          canary_hits, canary_rows)),
        },
    }

    metrics.update({
        "rows_total": rows_seen,
        "points": point_total,
        "point_matched": point_match,
        "point_map": point_map,
        "phase_counts": dict(sorted(phase_counts.items())),
        "boundary_samples_in_0_60_0_80": boundary_samples,
        "boundary_requirement": _BOUNDARY_MIN_SAMPLES,
        "boundary_ok": bool(boundary_ok),
        "boundary_range": [_BOUNDARY_LO, _BOUNDARY_HI],
        "ramp_flat": ramp_flat,
        "anchor": {"rows": anchor_total, "consistent": anchor_consistent,
                   "changed": anchor_changed_n, "normal_unchanged": anchor_normal_rows,
                   "mismatch": anchor_mismatch},
        "canary": {"rows": canary_rows, "uca07_hits": canary_hits},
        "shift_prefeed_without_trend": shift_without_trend,
        "clamp_probes": clamp_probes,
        "window_defaulted": bool(window_defaulted),
        "thresholds": {"phaseShiftRatio": th_ratio, "phaseShiftHardRatio": th_hard,
                       "trendThreshold": th_trend, "ratioMax": ratio_max,
                       "historyCapacity": hist_cap},
    })
    if point_total:
        rates["phase_exact"] = {"num": point_match, "den": point_total}
    if anchor_total:
        rates["anchor_consistent"] = {"num": anchor_consistent, "den": anchor_total}
    if canary_rows:
        rates["canary_uca07"] = {"num": canary_hits, "den": canary_rows}
    rates["boundary_coverage"] = {"num": min(boundary_samples, _BOUNDARY_MIN_SAMPLES),
                                  "den": _BOUNDARY_MIN_SAMPLES}

    notes.extend([
        "合成边界样本不计入 docs/20 §4.1 真实边界门槛：本族的 %d 个 ratio∈[0.60,0.80) 样本"
        "只证明检测器在阈值附近按设计动作，不能当真实 turn 的边界证据。"
        % boundary_samples,
        "阈值来源：ctx.cfg['control']（shift=%s / hard=%s / trendThreshold=%s / ratioMax=%s），"
        "不是契约常量；docs/19 §2.3 明确 0.70/0.85 是 rules/ 当前值，ADR-011 双阈值联动整组移动。"
        % (th_ratio, th_hard, th_trend, ratio_max),
        "边界样本数=%d（要求 ≥%d，%s）；不足属语料覆盖问题，不记为引擎失败。"
        % (boundary_samples, _BOUNDARY_MIN_SAMPLES, "达标" if boundary_ok else "不足"),
        "锚点触发语义：apply_persona_anchor 用 `prefeed or anchor_required`；shift 且 trend≤阈值时"
        "prefeed=True、anchor_required=False，pipeline 仍会加锚（本族 shift_prefeed_without_trend=%d）。"
        % shift_without_trend,
        "ramp/flat 对照：%s。" % ("成立" if ramp_flat["contrast_ok"] else "不成立（见 metrics.ramp_flat）"),
        "本族不证明 0.70/0.85 阈值正确（docs/23 §9 不覆盖清单）；不构成 measured 证据。",
    ])

    return {
        "checks": checks,
        "metrics": metrics,
        "rates": rates,
        "samples": samples,
        "notes": notes,
        "invariants": invariants,
        "skipped": False,
        "skip_reason": None,
    }
