# -*- coding: utf-8 -*-
"""F3 · `f3_integral_windup` —— 积分抗饱和与恢复（temporal / IntegralBaseline）

归属：`docs/23-混沌工程v2-科学压测设计.md` §5.3（刺激集）、§4（不变量 INV-10/11/12/05）、
`docs/19` §2.2（积分控制 + 死区 + 抗饱和 + 泄放）。

本族按 **recipe 驱动** `engine.control.IntegralBaseline`（纯函数，确定性，不调模型、不写盘）：

| recipe | 刺激 |
|---|---|
| `sustained_negative` | 连续负偏离 12 轮 ×0.3 → 单调上升到 cap 后停止积分 |
| `exact_cap` | 单轮偏离使 ΔI **恰好**触 cap（e = cap/(Ki·dt)） |
| `deadband_only` | |e|<deadband → 只泄放不积分（默认不预充；给 prefill_rounds 才预充） |
| `positive_only` | 正偏离（e<0）→ 只泄放，不推基线 |
| `saturation_then_neutral` | 饱和 N 轮后中性 40 轮 → 泄漏回落 |
| `alternating` | 正负交替 20 轮 → 方向正确且有界 |
| `dt_scaling` | dt=1/2/5 对照：饱和轮次与 dt 成反比 |
| `uca08_trace` | 饱和后调 preflight_audit → downgrades 含 UCA-08、ucas 不含、verdict=allow |
| `positive_from_saturated` | 饱和后转正偏离 10 轮 → 闩锁不复位、不增长（只按 leak 回落） |

**全部阈值从 `ctx.cfg["control"]` 取**（integralKi / integralCap / integralLeak /
integralDeadband），族模块里不出现 0.15/0.4/0.05/0.02 之类的硬编码数字——
它们只作为"读到的值"出现在 metrics 里。

`expect` 键语义（与语料预注册对齐）：

- `saturated`：末轮任一维是否饱和；
- `saturated_round`：首次任一维饱和的轮次（不适用于 `dt_scaling`，该 recipe 输出逐 dt 表）；
- `saturated_rounds_by_dt`：`dt_scaling` 专用，`{"1.0": 9, "2.0": 5, "5.0": 2}` 逐值比对；
- `growth`：**全程峰值 max|I| − 初始 max|I|**（因为饱和后引擎按 leak 回落，不能用"末值−初值"）；
- `max_abs_integral`：全程峰值，按**上界**比较（`≤ 期望 + 1e-9`，对应 docs/23 失败判据 `|I|>cap`）；
- `recovered`：中性结束后 `I < cap/10`；
- `ucas` / `downgrades` / `verdict`：饱和后 `preflight_audit(saturated=...)` 的三元组（真跑审计比对）。

诚实边界（必读）：

- INV-11 的"单调"限定为"**到 cap 之前**单调不减"。引擎在 `saturated=True` 之后走**泄放**分支
  （`integral *= exp(-leak·dt)`），所以饱和后即使持续负偏离积分项也按 leak 下降——
  这是反饱和设计（不是"憋住 cap"），本族按此语义断言并单列指标。
- INV-12 的恢复轮次按 `integralLeak` **解析**：`N = ceil(ln(10)/(leak·dt))`。默认参数下 N=47，
  所以 docs/23 §5.3 的"中性 40 轮"在 dt=1 时不足以回到 cap/10；语料给的是 dt=1.25
  （解析 N=ceil(36.84)=37，40 轮足够）。语料 40 轮只作回落趋势观测，INV-12 用解析 N 判定。
- `saturated` 是**永久闩锁**：一旦 true 不会复位（本族记录指标，不作失败判据——docs/19 §4
  只要求"抑制继续积分"）。风险观察写进 notes。
- 本族是合成时间序列，不计入 `docs/20` 任何真实门槛，不构成 measured 证据。
"""
from __future__ import annotations

import copy
import math
import sys
from typing import Any

FAMILY_ID = "f3_integral_windup"
STRESS_CLASS = "temporal"
# docs/23 §4：数值有界 / 单调方向 / 恢复性 / UCA-08 只降级不熔断
INVARIANTS = ("INV-10", "INV-11", "INV-12", "INV-05")

RECIPES: tuple[str, ...] = (
    "sustained_negative",
    "exact_cap",
    "deadband_only",
    "positive_only",
    "saturation_then_neutral",
    "alternating",
    "dt_scaling",
    "uca08_trace",
    "positive_from_saturated",
)

_EPS = 1e-9


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
    from engine import pipeline as PL
    from engine.model import Entry, Store
    return {"CFG": CFG, "CT": CT, "PL": PL, "Entry": Entry, "Store": Store}


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


def _max_abs(ib) -> float:
    return max(abs(float(v)) for v in ib.integral.values())


def _any_sat(ib) -> bool:
    return any(bool(v) for v in ib.saturated.values())


def _dev(inp: dict[str, Any], default: dict[str, float]) -> dict[str, float]:
    d = inp.get("deviation")
    if isinstance(d, dict) and d:
        return {str(k): float(v) for k, v in d.items()}
    return dict(default)


def _trace_pick(history: list[list[Any]], limit: int = 24) -> list[list[Any]]:
    """压缩轨迹（最多 limit 条；长序列等距抽样，确定性）。"""
    if len(history) <= limit:
        return history
    step = max(1, len(history) // limit)
    out = history[::step]
    if out[-1] is not history[-1]:
        out.append(history[-1])
    return out[:limit + 1]


def _drive(ib, seq: list[dict[str, float]], dts: list[float],
           trace: list[list[Any]]) -> dict[str, Any]:
    """按序列逐轮推进，记录轨迹、峰值、首次饱和轮次。"""
    peak = 0.0
    sat_round: int | None = None
    for i, (d, dt) in enumerate(zip(seq, dts)):
        st = ib.step(d, dt)
        trace.append([i + 1, round(float(st["baseline"]["valence"]), 10), float(dt),
                      float(d.get("valence", 0.0)), bool(st["saturated"]["valence"])])
        peak = max(peak, _max_abs(ib))
        if sat_round is None and _any_sat(ib):
            sat_round = i + 1
    return {"peak_abs_integral": round(peak, 12), "saturated_round": sat_round}


def _run_recipe(CT, cfg: dict[str, Any], recipe: str,
                inp: dict[str, Any]) -> dict[str, Any]:
    """执行一个 recipe，返回逐步结果 + 汇总（纯函数）。"""
    c = cfg["control"]
    ki = float(c["integralKi"])
    cap = float(c["integralCap"])
    leak = float(c["integralLeak"])
    db = float(c["integralDeadband"])
    dt = float(inp.get("dt") if inp.get("dt") is not None else 1.0)
    rounds = int(inp.get("rounds") if inp.get("rounds") is not None else 0)
    dev = _dev(inp, {"valence": -0.3})
    trace: list[list[Any]] = []
    out: dict[str, Any] = {"recipe": recipe, "dt": dt, "ki": ki, "cap": cap,
                           "leak": leak, "deadband": db, "trace": trace,
                           "initial_abs_integral": 0.0}

    def _finish(ib, extra: dict[str, Any] | None = None) -> dict[str, Any]:
        out.update({"max_abs_integral": round(max(float(out["initial_abs_integral"]),
                                                 float(out.get("peak_abs_integral") or 0.0)), 12),
                    "final_integral": dict(ib.integral),
                    "final_saturated": dict(ib.saturated),
                    "final_any_saturated": _any_sat(ib),
                    "state": ib})
        if extra:
            out.update(extra)
        return out

    if recipe == "sustained_negative":
        n = rounds or 12
        ib = CT.IntegralBaseline(cfg)
        out.update(_drive(ib, [dict(dev) for _ in range(n)], [dt] * n, trace))
        return _finish(ib, {"rounds": n})

    if recipe == "deadband_only":
        ib = CT.IntegralBaseline(cfg)
        seq: list[dict[str, float]] = []
        dts: list[float] = []
        prefill = int(inp.get("prefill_rounds") if inp.get("prefill_rounds") is not None else 0)
        if prefill > 0:
            pre_dev = _dev(inp, {"valence": -0.3}) if "prefill_deviation" not in inp \
                else _dev({"deviation": inp["prefill_deviation"]}, {"valence": -0.3})
            for _i in range(prefill):
                seq.append(dict(pre_dev))
                dts.append(dt)
            out["prefill_end_index"] = prefill
            out["prefill_integral"] = None    # 由 _drive 后补
        # 死区阶段：默认 |e| < deadband（deviation 取 row 值，缺省 deadband/2）
        small = dict(dev)
        if "deviation" not in inp:
            small = {"valence": db / 2.0}
        n = rounds or 20
        for _i in range(n):
            seq.append(dict(small))
            dts.append(dt)
        info = _drive(ib, seq, dts, trace)
        out.update(info)
        if prefill > 0:
            out["prefill_integral"] = round(float(trace[prefill - 1][1]), 12)
        return _finish(ib, {"rounds": len(seq)})

    if recipe == "positive_only":
        n = rounds or 12
        ib = CT.IntegralBaseline(cfg)
        pos = {"valence": abs(float(dev.get("valence", 0.3)))}
        out.update(_drive(ib, [dict(pos) for _ in range(n)], [dt] * n, trace))
        return _finish(ib, {"rounds": n})

    if recipe in ("saturation_then_neutral", "uca08_trace"):
        n_sat = rounds or 8
        ib = CT.IntegralBaseline(cfg)
        # 保证"饱和"真的能在 n_sat 轮内发生：|e| = cap/(Ki·dt·n_sat)·1.05
        mag = cap / (ki * max(dt, _EPS) * max(n_sat, 1)) * 1.05
        mag = max(mag, abs(float(dev.get("valence", 0.0))))
        sat_dev = {"valence": -mag}
        info_sat = _drive(ib, [dict(sat_dev) for _ in range(n_sat)], [dt] * n_sat, trace)
        out.update(info_sat)
        out["saturated_at_integral"] = round(float(ib.integral["valence"]), 12)
        out["saturation_phase_rounds"] = n_sat
        if recipe == "saturation_then_neutral":
            neutral_rounds = int(inp.get("neutral_rounds")
                                 if inp.get("neutral_rounds") is not None else 40)
            neutral = {"valence": 0.0}
            base_peak, base_sat = out["peak_abs_integral"], out["saturated_round"]
            info_n = _drive(ib, [dict(neutral) for _ in range(neutral_rounds)],
                            [dt] * neutral_rounds, trace)
            out["peak_abs_integral"] = max(base_peak, info_n["peak_abs_integral"])
            out["saturated_round"] = base_sat if base_sat is not None else info_n["saturated_round"]
            out["neutral_rounds"] = neutral_rounds
            out["neutral_analytic_rounds"] = int(math.ceil(math.log(10.0) / (leak * dt)))
        return _finish(ib, {"rounds": len(trace)})

    if recipe == "alternating":
        n = rounds or 20
        ib = CT.IntegralBaseline(cfg)
        pattern = inp.get("pattern")
        if isinstance(pattern, list) and pattern:
            seq = [{"valence": float(x)} for x in pattern]
        else:
            mag = abs(float(dev.get("valence", 0.3)))
            # 奇负偶正（1-based）：第 1/3/5… 轮负偏离，第 2/4/6… 轮正偏离
            seq = [{"valence": (-mag if i % 2 == 0 else mag)} for i in range(n)]
        out.update(_drive(ib, seq, [dt] * len(seq), trace))
        return _finish(ib, {"rounds": len(seq)})

    if recipe == "positive_from_saturated":
        n1 = rounds or 12
        n2 = int(inp.get("positive_rounds")
                 if inp.get("positive_rounds") is not None else
                 (inp.get("phase2_rounds") if inp.get("phase2_rounds") is not None else 10))
        ib = CT.IntegralBaseline(cfg)
        mag = abs(float(dev.get("valence", 0.3)))
        seq = [{"valence": -mag} for _ in range(n1)] + [{"valence": mag} for _ in range(n2)]
        out.update(_drive(ib, seq, [dt] * len(seq), trace))
        out["phase1_rounds"] = n1
        out["phase2_rounds"] = n2
        out["integral_at_phase_boundary"] = round(float(trace[n1 - 1][1]), 12)
        return _finish(ib, {"rounds": len(seq)})

    if recipe == "exact_cap":
        base = cap / (ki * max(dt, _EPS))
        ib = CT.IntegralBaseline(cfg)
        touched = False
        for factor in (1.0, 1.0 + 1e-12, 1.0 + 1e-9):
            ib = CT.IntegralBaseline(cfg)
            info = _drive(ib, [{"valence": -base * factor}], [dt], trace)
            if info["saturated_round"] is not None:
                touched = True
                break
            trace.clear()
        out.update(info)
        out["exact_e"] = base
        out["reached_cap"] = bool(touched)
        return _finish(ib, {"rounds": 1})

    if recipe == "dt_scaling":
        dt_values = inp.get("dt_values")
        if not isinstance(dt_values, list) or not dt_values:
            dt_values = [1.0, 2.0, 5.0]
        mag = abs(float(dev.get("valence", 0.3)))
        table: list[dict[str, Any]] = []
        peak_all = 0.0
        for dtv in dt_values:
            dtv = float(dtv)
            ib = CT.IntegralBaseline(cfg)
            got = None
            limit = max(50, int(math.ceil(cap / (ki * mag * max(dtv, _EPS)))) + 5)
            local: list[list[Any]] = []
            for i in range(limit):
                st = ib.step({"valence": -mag}, dtv)
                local.append([i + 1, round(float(st["baseline"]["valence"]), 10), dtv,
                              -mag, bool(st["saturated"]["valence"])])
                peak_all = max(peak_all, _max_abs(ib))
                if _any_sat(ib):
                    got = i + 1
                    break
            if not trace:
                trace.extend(local)
            table.append({"dt": dtv, "saturated_round": got,
                          "predicted_round": int(math.ceil(cap / (ki * mag * dtv))),
                          "half_life_rounds_analytic": round(math.log(2.0) / (leak * dtv), 6),
                          "max_abs_integral": round(peak_all, 12)})
        out.update({"dt_table": table,
                    "peak_abs_integral": round(peak_all, 12),
                    "saturated_round": None,
                    "saturated_rounds_by_dt": {str(t["dt"]): t["saturated_round"]
                                               for t in table},
                    # 每个 dt 都饱和 → 该行 saturated=True（供语料 expect 比对）
                    "final_any_saturated": all(t["saturated_round"] is not None for t in table),
                    "max_abs_integral_peak": round(peak_all, 12)})
        out.update({"max_abs_integral": round(peak_all, 12),
                    "final_integral": {"valence": None},
                    "final_saturated": {},
                    "state": None, "rounds": len(trace)})
        return out

    raise ValueError("未知 recipe: %r" % recipe)


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
    CT, CFG, PL = eng["CT"], eng["CFG"], eng["PL"]
    cfg = _cfg_of(ctx, CFG)
    c = cfg["control"]
    ki = float(c["integralKi"])
    cap = float(c["integralCap"])
    leak = float(c["integralLeak"])
    db = float(c["integralDeadband"])
    base_ts = float(ctx.get("base_ts") or 1761541200.0)

    checks: dict[str, Any] = {"cases": 0, "expectations": 0, "failed": 0, "failures": []}
    failures = _Failures()
    metrics: dict[str, Any] = {}
    rates: dict[str, dict[str, int]] = {}
    samples: list[dict[str, Any]] = []
    notes: list[str] = []

    recipes_out: dict[str, Any] = {}
    max_abs_global = 0.0
    monotone_bad: list[str] = []
    deadband_growth: float | None = None
    positive_growth: float | None = None
    recovery_bad: list[str] = []
    recovery_rows: list[dict[str, Any]] = []
    sat_rounds: dict[str, Any] = {}
    uca08_rows: list[dict[str, Any]] = []
    uca08_bad: list[str] = []
    exact_cap_ok: list[str] = []
    dt_tables: dict[str, Any] = {}

    for row in fam_rows:
        checks["cases"] += 1
        case = str(row.get("case") or "<unnamed>")
        inp = row.get("input") if isinstance(row.get("input"), dict) else {}
        exp = row.get("expect") if isinstance(row.get("expect"), dict) else {}
        recipe = str(inp.get("recipe") or "")

        if recipe not in RECIPES:
            checks["expectations"] += 1
            failures.check(False, case, {"recipe": list(RECIPES)}, {"recipe": recipe},
                           "未知 recipe → 失败（不许静默跳过）")
            continue

        try:
            res = _run_recipe(CT, cfg, recipe, inp)
        except Exception as exc:              # 引擎异常 = 真实失败，不许吞
            checks["expectations"] += 1
            failures.check(False, case, "recipe 正常执行",
                           "%s: %s" % (type(exc).__name__, exc),
                           "引擎异常按失败计（docs/23 §6.7）")
            continue

        ib = res.get("state")
        final_i = float(ib.integral["valence"]) if ib is not None else 0.0
        r_max = float(res.get("max_abs_integral") or 0.0)
        peak = float(res.get("peak_abs_integral") or r_max)
        initial = float(res.get("initial_abs_integral") or 0.0)
        growth = peak - initial
        max_abs_global = max(max_abs_global, r_max, peak)
        sat_round = res.get("saturated_round")
        trace = res.get("trace") or []

        summary = {
            "case": case, "recipe": recipe, "dt": res.get("dt"),
            "rounds": len(trace),
            "saturated_round": sat_round,
            "max_abs_integral": round(r_max, 12),
            "peak_abs_integral": round(peak, 12),
            "growth_peak": round(growth, 12),
            "final_valence": round(final_i, 12),
            "final_any_saturated": res.get("final_any_saturated"),
        }
        if "dt_table" in res:
            summary["dt_table"] = res["dt_table"]
            dt_tables[case] = res["dt_table"]
        recipes_out[case] = summary
        if sat_round is not None:
            sat_rounds[case] = sat_round

        # ── 逐 recipe 断言
        if recipe == "sustained_negative":
            prev: float | None = None
            sat_seen = False
            bad = False
            for item in trace:
                i_val = float(item[1])
                sat = bool(item[4])
                if prev is not None:
                    if not sat_seen:
                        # 饱和前：单调不减（饱和发生的当轮允许跳到 cap）
                        if not sat and i_val < prev - 1e-12:
                            bad = True
                    else:
                        # 饱和后：只泄放，不得再增长
                        if i_val > prev + 1e-12:
                            bad = True
                if sat:
                    sat_seen = True
                prev = i_val
            checks["expectations"] += 2
            failures.check(not bad, case, {"单调": "饱和前不减、饱和后不增"},
                           {"monotone_ok": not bad},
                           "INV-11：连续负偏离 baseline 单调上升至 cap")
            failures.check(r_max <= cap + _EPS, case, {"max|I| ≤ cap": cap},
                           {"max|I|": r_max}, "INV-10：数值有界")
            if bad:
                monotone_bad.append(case)

        elif recipe == "deadband_only":
            pre = int(res.get("prefill_end_index") or 0)
            start_idx = pre - 1 if pre > 0 else 0
            if trace:
                start = float(trace[start_idx][1])
                end = float(trace[-1][1])
                g = end - start
                deadband_growth = round(g, 12)
                tail = [float(t[1]) for t in trace[start_idx:]]
                checks["expectations"] += 2
                failures.check(g <= 1e-12, case, {"死区内增长 ≤ 0": 0.0},
                               {"growth": g}, "死区内只泄放不积分")
                failures.check(all(tail[i] <= tail[i - 1] + 1e-12
                                   for i in range(1, len(tail))), case,
                               {"非增": True}, {"tail": tail[:10]},
                               "泄放分支必须单调不增")
            else:
                checks["expectations"] += 1
                failures.check(False, case, "有可观测轮次", {"trace_len": 0},
                               "recipe 参数不足")

        elif recipe == "positive_only":
            if trace:
                start = float(trace[0][1])
                end = float(trace[-1][1])
                g = end - start
                positive_growth = round(g, 12)
                checks["expectations"] += 2
                failures.check(g <= 1e-12, case, {"正偏离增长 ≤ 0": 0.0},
                               {"growth": g}, "正偏离不推基线")
                failures.check(all(float(trace[i][1]) <= float(trace[i - 1][1]) + 1e-12
                                   for i in range(1, len(trace))), case,
                               {"非增": True}, {"trace": [t[1] for t in trace]},
                               "正偏离只泄放")
            else:
                checks["expectations"] += 1
                failures.check(False, case, "有可观测轮次", {"trace_len": 0}, "recipe 参数不足")

        elif recipe == "exact_cap":
            checks["expectations"] += 2
            ok = failures.check(bool(res.get("reached_cap")) and sat_round == 1, case,
                                {"saturated": True, "saturated_round": 1},
                                {"saturated": res.get("final_any_saturated"),
                                 "saturated_round": sat_round},
                                "ΔI 恰好触 cap → 必须饱和（不许差一点没到）")
            ok_cap = failures.check(abs(final_i - cap) <= 1e-12, case, {"I == cap": cap},
                                    {"I": final_i}, "INV-10：恰好钳到 cap，不许过冲")
            if ok and ok_cap:
                exact_cap_ok.append(case)

        elif recipe in ("saturation_then_neutral", "positive_from_saturated"):
            if recipe == "saturation_then_neutral":
                neutral_rounds = int(res.get("neutral_rounds") or 0)
                analytic = int(res.get("neutral_analytic_rounds") or 0)
                sat_start = float(res.get("saturated_at_integral") or 0.0)
                recovered = final_i < cap / 10.0
                recovery_rows.append({"case": case, "after_neutral_rounds": neutral_rounds,
                                      "final": round(final_i, 12),
                                      "cap_over_10": cap / 10.0,
                                      "recovered_to_cap10": bool(recovered),
                                      "analytic_rounds": analytic})
                checks["expectations"] += 3
                ok_sat = failures.check(sat_round is not None, case,
                                        {"饱和阶段必须饱和": True},
                                        {"saturated_round": sat_round},
                                        "刺激设计：饱和阶段须触 cap")
                n_phase = int(res.get("saturation_phase_rounds") or 0)
                tail = [float(t[1]) for t in trace[n_phase:]]
                ok_mono = failures.check(
                    all(tail[i] <= tail[i - 1] + 1e-12 for i in range(1, len(tail))),
                    case, {"中性轮次单调不增": True}, {"tail": tail[:8]},
                    "INV-12：中性轮次只泄放")
                ok_down = failures.check(final_i <= sat_start + 1e-12, case,
                                         {"中性后 ≤ 饱和值": sat_start},
                                         {"final": final_i}, "必须有回落")
                if not (ok_sat and ok_mono and ok_down):
                    recovery_bad.append(case)
            else:
                boundary = float(res.get("integral_at_phase_boundary") or 0.0)
                checks["expectations"] += 2
                ok_sat = failures.check(res.get("final_any_saturated") is True, case,
                                        {"saturated 闩锁不复位": True},
                                        {"final_any_saturated": res.get("final_any_saturated")},
                                        "正偏离不会复位 saturated")
                ok_down = failures.check(final_i <= boundary + 1e-12, case,
                                         {"正偏离阶段不增长": True},
                                         {"growth_in_phase2": round(final_i - boundary, 12)},
                                         "INV-11：饱和后正偏离只泄放不增长")
                if not (ok_sat and ok_down):
                    recovery_bad.append(case)

        elif recipe == "alternating":
            bad = False
            for i in range(1, len(trace)):
                prev_v = float(trace[i - 1][1])
                cur_v = float(trace[i][1])
                dev_v = float(trace[i][3])
                if dev_v < 0:                       # 负偏离 → e>0 → 积分应不下降（饱和时除外）
                    if not bool(trace[i][4]) and cur_v < prev_v - 1e-12:
                        bad = True
                else:                               # 正偏离/中性 → 只泄放
                    if cur_v > prev_v + 1e-12:
                        bad = True
            checks["expectations"] += 2
            failures.check(not bad, case, {"方向正确": "负轮不降、非负轮不增"},
                           {"direction_ok": not bad}, "INV-11：方向正确")
            failures.check(r_max <= cap + _EPS, case, {"max|I| ≤ cap": cap},
                           {"max|I|": r_max}, "INV-10：数值有界")
            if bad:
                monotone_bad.append(case)

        elif recipe == "dt_scaling":
            table = res.get("dt_table") or []
            checks["expectations"] += len(table)
            for t in table:
                obs, pred = t.get("saturated_round"), t.get("predicted_round")
                failures.check(obs is not None and abs(int(obs) - int(pred)) <= 1, case,
                               {"saturated_round": pred, "tolerance": 1},
                               {"dt": t.get("dt"), "saturated_round": obs},
                               "饱和轮次 ≈ ceil(cap/(Ki·|e|·dt))：dt 放大 → 轮次减少")
            by_dt = exp.get("saturated_rounds_by_dt")
            if isinstance(by_dt, dict):
                got_by_dt = {str(k): v for k, v in
                             (res.get("saturated_rounds_by_dt") or {}).items()}
                want_norm = {str(float(k)): v for k, v in by_dt.items()}
                checks["expectations"] += 1
                failures.check(want_norm == got_by_dt, case,
                               {"saturated_rounds_by_dt": want_norm},
                               {"got": got_by_dt},
                               "逐 dt 饱和轮次预注册比对")

        elif recipe == "uca08_trace":
            # 接线层：pipeline.step 传 saturated → event.audit.downgrades 留痕且不 block
            saturated = dict(ib.saturated) if ib is not None else {}
            session = PL.PipelineSession(cfg, "f3-uca08")
            out = session.step("中性消息", base_ts, eng["Store"](), saturated=saturated)
            checks["expectations"] += 3
            ok_p1 = failures.check(
                "UCA-08" in (out["audit"].get("downgrades") or []), case,
                {"pipeline audit.downgrades 含": "UCA-08"},
                {"downgrades": out["audit"].get("downgrades")},
                "接线：saturated 事实必须到达 pipeline 的闸门")
            ok_p2 = failures.check(out["blocked"] is False, case, {"blocked": False},
                                   {"blocked": out["blocked"]},
                                   "降级不是熔断，pipeline 不得 block")
            ok_p3 = failures.check(out["prompt"] is not None, case,
                                   {"prompt 不为 None": True}, {"prompt": out["prompt"]},
                                   "降级必须继续给可注入 prompt")
            if not (ok_p1 and ok_p2 and ok_p3):
                uca08_bad.append(case)

        # ── 通用 expect 比对：ucas/downgrades/verdict（饱和事实真进闸门）
        if any(k in exp for k in ("ucas", "downgrades", "verdict")):
            saturated = dict(ib.saturated) if ib is not None else {}
            audit = CT.preflight_audit(
                prompt=str(inp.get("prompt") or ""), frozen_ok=True, refuse=False,
                style_injected=False, constitution_injected=True, conflict="none",
                fallback_applied=False, phase_result=None, fact_block="",
                saturated=saturated)
            checks["expectations"] += sum(1 for k in ("ucas", "downgrades", "verdict") if k in exp)
            ok_all = True
            if "ucas" in exp:
                ok_all &= failures.check(
                    sorted(audit.get("ucas") or []) == sorted(exp.get("ucas") or []), case,
                    {"ucas": exp.get("ucas")}, {"ucas": audit.get("ucas")},
                    "UCA-08 只降级不熔断：ucas 不得含 UCA-08")
            if "downgrades" in exp:
                ok_all &= failures.check(
                    sorted(audit.get("downgrades") or []) == sorted(exp.get("downgrades") or []),
                    case, {"downgrades": exp.get("downgrades")},
                    {"downgrades": audit.get("downgrades")},
                    "饱和事实必须进 audit.downgrades（可观测）")
            if "verdict" in exp:
                ok_all &= failures.check(str(audit.get("verdict")) == str(exp.get("verdict")),
                                         case, {"verdict": exp.get("verdict")},
                                         {"verdict": audit.get("verdict")},
                                         "降级不得改变 verdict")
            uca08_rows.append({"case": case, "downgrades": audit.get("downgrades"),
                               "ucas": audit.get("ucas"), "verdict": audit.get("verdict")})
            if not ok_all and case not in uca08_bad:
                uca08_bad.append(case)

        # ── 通用 expect 字段比对（语料给了才比）
        for key in ("saturated", "saturated_round", "growth", "recovered"):
            if key not in exp:
                continue
            checks["expectations"] += 1
            if key == "saturated":
                got = bool(res.get("final_any_saturated"))
                failures.check(got == bool(exp[key]), case, {key: bool(exp[key])},
                               {key: got}, "饱和状态与期望一致")
            elif key == "saturated_round":
                failures.check(sat_round == exp[key], case, {key: exp[key]},
                               {key: sat_round}, "饱和发生轮次")
            elif key == "growth":
                failures.check(abs(growth - float(exp[key])) <= 1e-6, case,
                               {key: exp[key]}, {key: round(growth, 12)},
                               "增长量=峰值 max|I|−初始 max|I|（容差 1e-6）")
            elif key == "recovered":
                got = final_i < cap / 10.0
                failures.check(bool(got) == bool(exp[key]), case, {key: bool(exp[key])},
                               {key: bool(got)}, "恢复到 < cap/10 与期望一致")
        if "max_abs_integral" in exp:
            checks["expectations"] += 1
            failures.check(r_max <= float(exp["max_abs_integral"]) + _EPS, case,
                           {"max|I| ≤": exp["max_abs_integral"]}, {"max|I|": r_max},
                           "max|I| 上界（docs/23 §5.3 失败判据 |I|>cap）")

        if len(samples) < 10:
            samples.append({"case": case, "recipe": recipe, "sat_round": sat_round,
                            "peak_abs_integral": round(peak, 12),
                            "growth_peak": round(growth, 12),
                            "trace_pick": _trace_pick(trace, 12)})

    # ── 内部探针：泄放半衰期实测 vs 解析、解析恢复轮次、饱和闩锁
    ib_half = CT.IntegralBaseline(cfg)
    ib_half.integral["valence"] = cap
    half_series: list[float] = []
    for _i in range(400):
        st = ib_half.step({"valence": 0.0})
        half_series.append(float(st["baseline"]["valence"]))
    measured_half = next((i + 1 for i, v in enumerate(half_series) if v <= cap / 2.0), None)
    analytic_half = math.log(2.0) / leak
    half_probe_ok = (measured_half is not None
                     and abs(measured_half - analytic_half) <= 1.0)

    ib_rec = CT.IntegralBaseline(cfg)
    mag = cap / (ki * 8.0) * 1.05
    for _i in range(8):
        ib_rec.step({"valence": -mag})
    analytic_n = int(math.ceil(math.log(10.0) / leak))
    rec_series: list[float] = []
    for _i in range(analytic_n + 1):
        rec_series.append(float(ib_rec.step({"valence": 0.0})["baseline"]["valence"]))
    analytic_recovered = rec_series[-1] < cap / 10.0
    rec_rounds_to_cap10 = next((i + 1 for i, v in enumerate(rec_series)
                                if v < cap / 10.0), None)

    # 饱和闩锁：饱和 → 中性 60 轮 → 再来 20 轮负偏离，看 saturated 是否复位
    ib_latch = CT.IntegralBaseline(cfg)
    for _i in range(12):
        ib_latch.step({"valence": -1.0})
    for _i in range(60):
        ib_latch.step({"valence": 0.0})
    latch_holds_after_neutral = bool(ib_latch.saturated["valence"])
    ib_latch_after = float(ib_latch.integral["valence"])
    for _i in range(20):
        ib_latch.step({"valence": -1.0})
    latch_holds_after_neg = bool(ib_latch.saturated["valence"])
    latch_neg_gain = float(ib_latch.integral["valence"]) - ib_latch_after
    latch_probe = {"saturated_after_neutral_60": latch_holds_after_neutral,
                   "saturated_after_negative_20": latch_holds_after_neg,
                   "integral_after_negative_20": round(float(ib_latch.integral["valence"]), 12),
                   "growth_after_latch": round(latch_neg_gain, 12),
                   "note": "saturated 是永久闩锁：一旦 true 不复位，之后只泄放不积分"}

    neutral40 = next((r for r in recovery_rows if r["after_neutral_rounds"] == 40), None)

    # 死区边界探针（族内，非语料行）：引擎用**严格小于**判死区（`abs(e) < deadband`），
    # 所以 |e| == deadband 应当**积分**而不是泄放。语料只投 |e|<deadband 的行，
    # 边界这一侧没有覆盖，这里补一条，避免"死区边界"成为盲区。
    ib_db = CT.IntegralBaseline(cfg)
    st_db = ib_db.step({"valence": -db}, 1.0)          # e = +deadband（恰好等于）
    db_growth = float(st_db["baseline"]["valence"])
    db_expected = ki * db * 1.0                        # 恰好 == deadband → 走积分分支
    deadband_boundary = {
        "stimulus_deviation": -db,
        "e_equals_deadband": round(db, 12),
        "expected_growth": round(db_expected, 12),
        "got_growth": round(db_growth, 12),
        "integrated_at_boundary": abs(db_growth - db_expected) <= 1e-12,
        "note": ("engine 死区判据是严格小于：|e|<deadband 只泄放；|e|==deadband 走积分。"
                 "语料 deadband_only 只投死区内行，本探针覆盖边界另一侧。"),
    }

    checks["failed"] = len(failures.items)
    checks["failures"] = failures.items

    ok_num = sum(1 for u in uca08_rows if u["case"] not in uca08_bad)
    inv10_pass = bool(max_abs_global <= cap + _EPS and math.isfinite(max_abs_global))
    inv11_pass = bool(not monotone_bad
                      and deadband_growth is not None and deadband_growth <= 1e-12
                      and positive_growth is not None and positive_growth <= 1e-12)
    inv12_pass = bool(half_probe_ok and analytic_recovered and not recovery_bad)
    inv05_pass = bool(uca08_rows and not uca08_bad)

    invariants = {
        "INV-10": {
            "pass": inv10_pass,
            "detail": ("数值有界：全局 max|I|=%s ≤ cap=%s（Ki=%s / leak=%s / deadband=%s 全部取自 "
                       "ctx.cfg.control）；exact_cap 钳到 cap 的用例 %r"
                       % (round(max_abs_global, 12), cap, ki, leak, db, exact_cap_ok)),
        },
        "INV-11": {
            "pass": inv11_pass,
            "detail": ("单调/方向：连续负偏离饱和前不减、饱和后不增（违规行 %r）；"
                       "死区内增长=%r（要求 ≤0）；正偏离增长=%r（要求 ≤0）；"
                       "死区边界 |e|==deadband 走积分分支（严格小于判死区）实测=%r"
                       % (monotone_bad[:8], deadband_growth, positive_growth,
                          deadband_boundary["got_growth"])),
        },
        "INV-12": {
            "pass": inv12_pass,
            "detail": ("恢复性：从 cap 实测泄放半衰期=%s 轮 vs 解析 ln2/leak=%s（差 %s 轮，容差 1）；"
                       "饱和后中性 %d 轮回到 <cap/10=%s（解析 N=%d）；语料 40 轮行 recovered=%r，"
                       "违规行=%r"
                       % (measured_half, round(analytic_half, 6),
                          (None if measured_half is None
                           else round(measured_half - analytic_half, 6)),
                          rec_rounds_to_cap10, round(cap / 10.0, 6), analytic_n,
                          (None if neutral40 is None else neutral40["recovered_to_cap10"]),
                          recovery_bad[:8])),
        },
        "INV-05": {
            "pass": inv05_pass,
            "detail": ("UCA-08 只降级不熔断：%d/%d 个含 ucas/downgrades/verdict 的用例满足 "
                       "downgrades 含 UCA-08、ucas 不含、verdict=allow；pipeline.step 传 saturated "
                       "后不 blocked、仍给 prompt；违规=%r"
                       % (ok_num, len(uca08_rows), uca08_bad[:8])),
        },
    }

    metrics.update({
        "rows_total": len(fam_rows),
        "recipes": recipes_out,
        "saturation_rounds": sat_rounds,
        "dt_scaling_tables": dt_tables,
        "max_abs_integral": round(max_abs_global, 12),
        "leak_half_life": {"measured_rounds": measured_half,
                           "analytic_rounds": round(analytic_half, 6),
                           "ratio": (round(measured_half / analytic_half, 6)
                                     if measured_half else None),
                           "tolerance_rounds": 1.0},
        "deadband_growth": deadband_growth,
        "deadband_boundary": deadband_boundary,
        "positive_growth": positive_growth,
        "saturated_latched": latch_probe,
        "recovery": {"rows": recovery_rows,
                     "analytic_neutral_rounds": analytic_n,
                     "analytic_recovered": bool(analytic_recovered),
                     "rounds_to_cap10_analytic_run": rec_rounds_to_cap10},
        "uca08": {"rows": len(uca08_rows), "ok": ok_num, "mismatch": uca08_bad,
                  "trace": uca08_rows},
        "thresholds": {"integralKi": ki, "integralCap": cap,
                       "integralLeak": leak, "integralDeadband": db},
        "analytic": {"half_life_rounds_expr": "ln(2)/(integralLeak*dt)",
                     "recovery_rounds_expr": "ceil(ln(10)/(integralLeak*dt))",
                     "saturation_round_expr": "ceil(integralCap/(integralKi*|e|*dt))"},
    })

    if uca08_rows:
        rates["uca08_downgrade_only"] = {"num": ok_num, "den": len(uca08_rows)}
    dt_rows = [v for v in recipes_out.values() if v["recipe"] == "dt_scaling"]
    if dt_rows:
        rates["dt_saturation_round_hit"] = {
            "num": sum(1 for v in dt_rows for t in v.get("dt_table", [])
                       if t.get("saturated_round") is not None
                       and abs(int(t["saturated_round"]) - int(t["predicted_round"])) <= 1),
            "den": sum(len(v.get("dt_table", [])) for v in dt_rows)}
    rates["recovery_analytic"] = {"num": 1 if (analytic_recovered and half_probe_ok) else 0,
                                  "den": 1}

    notes.extend([
        "阈值全部来自 ctx.cfg['control']：Ki=%s / cap=%s / leak=%s / deadband=%s（族模块无硬编码）。"
        % (ki, cap, leak, db),
        "expect.growth 口径 = 全程峰值 max|I| − 初始 max|I|；expect.max_abs_integral 按上界比较"
        "（≤ 期望 + 1e-9）。饱和后引擎按 leak 回落，所以'末值−初值'会小于峰值增量。",
        "INV-11 口径：'单调不减'限定在到 cap 之前。引擎 saturated 后走泄放分支"
        "（integral *= exp(-leak·dt)），所以饱和后即使持续负偏离积分项也按 leak 下降——"
        "这是反饱和设计，不是'憋住 cap'。",
        "INV-12 口径：恢复轮次按 integralLeak 解析 N=ceil(ln(10)/(leak·dt))；默认 dt=1 时 N=%d，"
        "docs/23 §5.3 的'中性 40 轮'在 dt=1 下不足以回到 cap/10；语料给的是 dt=1.25（N=37），"
        "40 轮足够。判据用解析 N，40 轮只作回落趋势观测。" % analytic_n,
        "行为观察（非违反）：saturated 是永久闩锁，一旦 true 不复位；闩锁后积分项只泄放，"
        "持续负偏离也不会再积分（可能让'长期不满'的基线一路回到 0）。docs/19 §4 只要求"
        "'抑制继续积分'，本族记录 metrics.saturated_latched 但不作失败判据。",
        "exact_cap 用 e=cap/(Ki·dt) 精确触发；浮点若使 nxt 差一点没到 cap 则引擎不饱和——"
        "本族实测 %d 个用例恰好触 cap（见 metrics.recipes）。" % len(exact_cap_ok),
        "死区边界探针：engine 死区判据是严格小于（abs(e)<deadband），所以 |e|==deadband 会"
        "**积分**（ΔI=Ki·deadband·dt=%r）。语料 deadband_only 只覆盖死区内侧，"
        "边界另一侧由族内探针覆盖（metrics.deadband_boundary）。"
        % deadband_boundary["expected_growth"],
        "dt_scaling：饱和轮次 ≈ ceil(cap/(Ki·|e|·dt))，dt 放大 → 轮次按反比减少；"
        "泄放半衰期 ≈ ln2/(leak·dt) 同比例放大（见 metrics.leak_half_life 与 dt_scaling_tables）。",
        "本族是合成时间序列：不计入 docs/20 任何真实门槛，不构成 measured 证据。",
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
