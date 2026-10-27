# -*- coding: utf-8 -*-
"""LINGSHU 真实标定 · 影子 / 每日分析工具（只读）

把"每天看日志"变成可重复的工具，而不是一次性肉眼核对。数据源只有插件落盘的三个文件：

    $DSH_HOME/plugins/dsh-lingshu/
        audit.jsonl    每 step / turn 的审计事件（pre_step、preflight_*、memory_write、agent_state…）
        state.json     插件当前状态（turns、control.phase、control.audit、goal、relation…）
        memory.jsonl   记忆条目（potency 用 engine.memory 现算）

用法：

    python tools/analyze_shadow.py --out acceptance/shadow-baseline.md
    python tools/analyze_shadow.py --dir <插件数据目录> --out /tmp/r.md
    python tools/analyze_shadow.py --out r.md --candidates-out /tmp/provenance-candidates.json

约束（与 docs/20-真实标定协议.md 一致）：

- **只读**：绝不写插件数据目录；如果 --out / --candidates-out 指向数据目录内部，直接拒绝（exit 2）。
- **样本不足就说"样本不足"**：缺字段、缺事件、legacy 日志都如实分栏，不把"没观测到"填成 0 来凑结论。
- **不直接改 rules/**：本工具只输出 `provenance-candidates.json` 候选；是否把 `guess` 改成
  `measured` 由人按 docs/20 的流程决定，且必须同时更新 assumptions/已验证.md。
- **规则自动标签只是辅助信号**：插件按用户消息规则产出的 `quality_label` 事件（decisions/012
  §2.7）只做统计与 ratio 关联展示，**不进入** `OUTCOME_KINDS` / `REQUIREMENTS` 的
  `outcome_labels` 门槛，**不改变** `upgrade_eligible`（规则自动标签 ≠ 人工结果标签）。
- 纯标准库（engine/ 是本仓库的 stdlib-only 参考实现，仅在算 potency 时 import）。

退出码：0 = 分析完成（**数据不足也是 0**，报告里标"样本不足"）；2 = 参数/路径非法或内部错误。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import math
import os
import sys
from collections import Counter

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN_NAME = "dsh-lingshu"
DEFAULT_HOME = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")

# 报告里每个数字都带标签：读者必须一眼看出哪些是日志实测、哪些是按实现公式算出来的。
LABEL_REAL = "真实日志实测"
LABEL_DERIVED = "由日志推导"
LABEL_IMPL = "由实现计算"

# engine/config.py 不可用时的兜底（仅用于展示；正常路径以 engine/config.py 为准，
# 它与 rules/decay-params.yaml#control 的一致性由 tools/check_config_sync.py 校验）。
FALLBACK_CONTROL = {
    "integralKi": 0.15, "integralCap": 0.4, "integralLeak": 0.05, "integralDeadband": 0.02,
    "phaseShiftRatio": 0.7, "phaseShiftHardRatio": 0.85, "trendThreshold": 0.02,
    "ratioMax": 1.5, "historyCapacity": 64,
}

# 可能承载"结果标签"（质量下降 / 人格漂移 / 用户纠正）的审计事件名。
# docs/17 规划了 corrections 通道，但当前插件与引擎都还没有实现 → 实测应为 0。
OUTCOME_KINDS = (
    "quality", "quality_drop", "drift", "persona_drift", "correction", "correction_label",
    "outcome", "human_label", "label", "review_result",
)

# ── 规则自动结果标签（decisions/012 §2.7；**辅助信号，绝不进入标定门槛**）──
# 闭集与 docs/17 §2.1 对齐并收敛。插件在用户消息通道按子串规则打标，写 audit.jsonl 的
# kind=="quality_label" 事件（字段：kind_label / quote / turn / phase{ratio,phase,anchor} /
# recall_ids / source）。
# 纪律：**规则自动标签 ≠ 人工结果标签**——本节统计只用于观察与 ratio 关联展示，绝不进入
# OUTCOME_KINDS，也绝不进入 REQUIREMENTS 的 outcome_labels 门槛，不改变 upgrade_eligible
# （docs/20 §1.2/§4.1；decisions/012 §2.7）。
QUALITY_LABEL_KINDS = ("wrong", "missing", "should_not_recall", "drift", "overreach")
# ratio 桶固定边界（不随控制参数漂移），与 decisions/012 §2.7 的统计口径一致。
QUALITY_LABEL_RATIO_BUCKETS = ("<0.70", "0.70-0.85", ">=0.85", "unknown")

# ── 标定门槛（单一来源：docs/20 引用本表，不重复写死数字）─────────────
# 为什么不许降门槛：真实数据只有个位数到几十个 turn 时，任何"看上去合理"的阈值都能被拟合出来。
# 门槛的作用是**阻止在证据不足时把 guess 改成 measured**，所以它必须写死在代码里、可被测试断言。
REQUIREMENTS = {
    "phaseShiftRatio": [
        {"gate": "observed_turns", "need": 30, "kind": "distribution",
         "label": "有 ratio 观测的 turn 数"},
        {"gate": "boundary_shift", "need": 10, "kind": "distribution",
         "label": "阈值边界样本（0.60≤ratio<0.80）"},
        {"gate": "crossings_shift", "need": 20, "kind": "distribution",
         "label": "ratio≥0.70 的越界次数"},
        {"gate": "window_source", "need": 1, "kind": "validity",
         "label": "真实 contextWindow 来源（非默认 1M 兜底）"},
        {"gate": "outcome_labels", "need": 20, "kind": "outcome",
         "label": "结果标签（是否漂移/质量下降）"},
    ],
    "phaseShiftHardRatio": [
        {"gate": "observed_turns", "need": 30, "kind": "distribution",
         "label": "有 ratio 观测的 turn 数"},
        {"gate": "crossings_hard", "need": 10, "kind": "distribution",
         "label": "ratio≥0.85 的越界次数"},
        {"gate": "boundary_hard", "need": 10, "kind": "distribution",
         "label": "阈值边界样本（0.80≤ratio<0.90）"},
        {"gate": "prefeed_allowed_nouca07", "need": 10, "kind": "validity",
         "label": "相变期放行且未命中 UCA-07 的轮数"},
        {"gate": "outcome_labels", "need": 10, "kind": "outcome",
         "label": "结果标签（critical 前后是否避免漂移）"},
    ],
    "trendThreshold": [
        {"gate": "observed_turns", "need": 30, "kind": "distribution",
         "label": "有 ratio 观测的 turn 数"},
        {"gate": "trend_samples", "need": 28, "kind": "distribution",
         "label": "可算 trend 的连续观测样本（每 3 个连续观测 1 个）"},
        {"gate": "trend_near", "need": 5, "kind": "distribution",
         "label": "靠近门槛的 trend 样本（0.01≤trend≤0.03）"},
        {"gate": "trend_triggers", "need": 10, "kind": "distribution",
         "label": "trend 触发提前锚点次数"},
        {"gate": "shift_observations", "need": 10, "kind": "distribution",
         "label": "shift 阶段观测数（trend 只在 shift 内提前补锚）"},
        {"gate": "outcome_labels", "need": 10, "kind": "outcome",
         "label": "结果标签（提前锚是否真的减少漂移）"},
    ],
}

PARAM_TITLES = {
    "phaseShiftRatio": "control.phaseShiftRatio（shift 阈值 0.70）",
    "phaseShiftHardRatio": "control.phaseShiftHardRatio（critical 阈值 0.85）",
    "trendThreshold": "control.trendThreshold（趋势提前量门槛 0.02）",
}


# ── 基础 IO / 工具 ─────────────────────────────────────────

def read_jsonl(path: str) -> tuple[list[dict], dict]:
    """读 JSONL；坏行跳过并计数（不中断分析），缺失文件返回空表。"""
    info = {"path": path, "exists": os.path.isfile(path), "lines": 0, "skipped": 0, "bytes": 0}
    if not info["exists"]:
        return [], info
    try:
        info["bytes"] = os.path.getsize(path)
    except OSError:
        pass
    out: list[dict] = []
    for line in io.open(path, encoding="utf-8", errors="replace"):
        info["lines"] += 1
        s = line.strip().lstrip("\ufeff")
        if not s:
            continue
        try:
            rec = json.loads(s)
        except json.JSONDecodeError:
            info["skipped"] += 1
            continue
        if isinstance(rec, dict):
            out.append(rec)
        else:
            info["skipped"] += 1         # 合法 JSON 但不是对象：同样算不可用行
    return out, info


def read_json(path: str) -> tuple[dict, dict]:
    info = {"path": path, "exists": os.path.isfile(path), "bytes": 0, "error": None}
    if not info["exists"]:
        return {}, info
    try:
        info["bytes"] = os.path.getsize(path)
        raw = io.open(path, encoding="utf-8", errors="replace").read().lstrip("\ufeff")
        rec = json.loads(raw) if raw.strip() else {}
        return (rec if isinstance(rec, dict) else {}), info
    except (OSError, json.JSONDecodeError) as exc:
        info["error"] = str(exc)[:200]
        return {}, info


def percentile(values: list[float], p: float) -> float | None:
    """线性插值分位数（与 numpy 默认口径一致，避免"手工分位数"不可复现）。"""
    if not values:
        return None
    s = sorted(float(v) for v in values)
    if len(s) == 1:
        return s[0]
    pos = (len(s) - 1) * max(0.0, min(100.0, p)) / 100.0
    lo, hi = int(math.floor(pos)), int(math.ceil(pos))
    if lo == hi:
        return s[lo]
    return s[lo] + (s[hi] - s[lo]) * (pos - lo)


def _to_seconds(ts) -> float:
    """把秒/毫秒时间戳统一成秒（插件 memory.jsonl 用 lastAccess 毫秒）。"""
    try:
        v = float(ts or 0)
    except (TypeError, ValueError):
        return 0.0
    return v / 1000.0 if v > 1e11 else v


def _iso(ms) -> str:
    if ms is None:
        return "—"
    try:
        dt = _dt.datetime.fromtimestamp(float(ms) / 1000.0)
    except (OverflowError, OSError, ValueError):
        return "—"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _round(v, n=4):
    if v is None:
        return None
    try:
        return round(float(v), n)
    except (TypeError, ValueError):
        return None


def _load_engine():
    """惰性加载 engine/（stdlib-only 参考实现）；失败则返回 None，工具仍能出报告。"""
    try:
        if ROOT not in sys.path:
            sys.path.insert(0, ROOT)
        from engine import actr as actr_mod          # noqa: F401
        from engine import config as cfg_mod         # noqa: F401
        from engine import memory as mem_mod         # noqa: F401
        from engine import model as model_mod        # noqa: F401
        return {"actr": actr_mod, "config": cfg_mod, "memory": mem_mod, "model": model_mod}
    except Exception:                                # pragma: no cover - 正常仓库内不会走到
        return None


def control_defaults() -> tuple[dict, str]:
    """控制参数当前值 + 来源说明（避免工具里再抄一份数字当"真值"）。"""
    eng = _load_engine()
    if eng is not None:
        try:
            return (dict(eng["config"].DEFAULTS["control"]),
                    "engine/config.py DEFAULTS.control（与 rules/decay-params.yaml#control 由 "
                    "tools/check_config_sync.py 校验一致）")
        except (KeyError, TypeError):
            pass
    return dict(FALLBACK_CONTROL), "内置兜底（engine 不可用，未做单一来源交叉校验）"


def _inside(path: str, parent: str) -> bool:
    """path 是否在 parent 目录内部（用于拒绝写插件数据目录）。"""
    try:
        p = os.path.normcase(os.path.abspath(path))
        d = os.path.normcase(os.path.abspath(parent))
        return os.path.commonpath([p, d]) == d
    except ValueError:                                # 不同盘符
        return False


# ── 1. turn 重建 ────────────────────────────────────────────
#
# 为什么不能直接用 pre_step 的 turn 字段：实测该字段是**宿主 session 的 turn id**（一直为 1），
# 不是"第几轮"。轮次由插件在首 step 用 turn_begin 事件推进（index.js），所以这里以
# turn_begin.turns 为轮次编号，把其后的 pre_step 归到该轮。
# pre_step 在一个 turn 内每 step 触发一次；只有 firstStep=true 的行带控制论字段（ratio/phase/verdict），
# 后续 step 沿用首 step 结果 —— 因此所有"按轮"的统计只吃 firstStep 行，避免同一轮重复计数。

def _phase_row(rec: dict, turn: int) -> dict:
    ratio = rec.get("ratio")
    try:
        ratio = float(ratio) if ratio is not None else None
    except (TypeError, ValueError):
        ratio = None
    phase = rec.get("phase")
    if not isinstance(phase, str):
        phase = None
    anchor = rec.get("anchor")
    if not isinstance(anchor, bool):
        anchor = None
    verdict = rec.get("verdict")
    if verdict not in ("allow", "block"):
        verdict = None
    ucas = rec.get("ucas")
    downgrades = rec.get("downgrades")
    step = rec.get("step")
    return {
        "turn": turn,
        "step": step if isinstance(step, (int, float)) else None,
        "first_step": bool(rec.get("firstStep")),
        "ratio": ratio,
        "phase": phase,
        "anchor": anchor,
        "verdict": verdict,
        "ucas": [str(x) for x in ucas] if isinstance(ucas, list) else [],
        "downgrades": [str(x) for x in downgrades] if isinstance(downgrades, list) else [],
        "blocked": bool(rec.get("blocked")),
        "chars": rec.get("chars") if isinstance(rec.get("chars"), (int, float)) else None,
        "has_ratio": ratio is not None,
    }


def build_turns(audit: list[dict]) -> tuple[list[dict], int]:
    """把审计事件序列切成 turn 列表；返回 (turns, pre_step 总条数)。"""
    turns: list[dict] = []
    steps = 0
    for rec in audit:
        kind = rec.get("kind")
        if kind == "turn_begin":
            idx = rec.get("turns")
            if not isinstance(idx, (int, float)):
                idx = len(turns) + 1
            turns.append({"turn": int(idx), "stage": rec.get("stage"),
                          "ts": rec.get("ts"), "pre_steps": [], "control_rows": []})
            continue
        if kind != "pre_step":
            continue
        steps += 1
        if not turns:                   # 首 step 早于 turn_begin：单列"未归属"桶，不丢数据
            turns.append({"turn": 0, "stage": None, "ts": rec.get("ts"),
                          "pre_steps": [], "control_rows": [], "unattributed": True})
        row = _phase_row(rec, turns[-1]["turn"])
        turns[-1]["pre_steps"].append(row)
        if row["first_step"]:
            turns[-1]["control_rows"].append(row)
    return turns, steps


# ── 2. 相变（token 膨胀 / 双阈值 / 趋势）────────────────────

def _trend_runs(observed: list[dict]) -> tuple[list[list[dict]], list[dict]]:
    """按"turn 连续"切分观测序列，再算 trend=(r[-1]-r[-3])/2。

    为什么切连续段：legacy 日志缺控制字段会造成 turn 空洞；跨空洞算斜率等于拿两段不同
    上下文的用量硬拼一条趋势线，会制造假提前量。
    """
    runs: list[list[dict]] = []
    cur: list[dict] = []
    for o in observed:
        if cur and o["turn"] is not None and cur[-1]["turn"] is not None \
                and o["turn"] != cur[-1]["turn"] + 1:
            runs.append(cur)
            cur = []
        cur.append(o)
    if cur:
        runs.append(cur)
    samples: list[dict] = []
    for run in runs:
        for i in range(2, len(run)):
            trend = (run[i]["ratio"] - run[i - 2]["ratio"]) / 2.0
            samples.append({"turn": run[i]["turn"], "trend": trend,
                            "phase": run[i]["phase"], "logged": False})
    return runs, samples


def summarize_phase(turns: list[dict], controls: dict) -> dict:
    shift_thr = float(controls.get("phaseShiftRatio", 0.70))
    hard_thr = float(controls.get("phaseShiftHardRatio", 0.85))
    trend_thr = float(controls.get("trendThreshold", 0.02))

    observed: list[dict] = []
    legacy_turns: list[int] = []
    first_step_total = 0
    for t in turns:
        if t.get("unattributed") and not t["control_rows"]:
            continue
        for o in t["control_rows"]:
            first_step_total += 1
            if o["has_ratio"]:
                observed.append(o)
            else:
                legacy_turns.append(t["turn"])
    ratios = [o["ratio"] for o in observed]
    phase_counts = Counter(o["phase"] for o in observed if o["phase"])

    # 完整性校验：日志里的 phase 与用 ratio 按双阈值重算的结果应当一致；
    # 不一致说明插件版本/参数与工具读到的 rules 不同源，报告必须显式列出。
    mismatches = []
    for o in observed:
        if o["phase"] is None:
            continue
        expect = ("critical" if o["ratio"] >= hard_thr
                  else "shift" if o["ratio"] >= shift_thr else "normal")
        if o["phase"] != expect:
            mismatches.append({"turn": o["turn"], "ratio": o["ratio"],
                               "logged": o["phase"], "expected": expect})

    anchor_logged_available = any(o["anchor"] is not None for o in observed)
    anchor_true = [o for o in observed if o["anchor"] is True]
    anchor_shift = [o for o in anchor_true if o["phase"] == "shift"]
    anchor_critical = [o for o in anchor_true if o["phase"] == "critical"]

    runs, samples = _trend_runs(observed)
    # 若未来插件把 trend 直接写进 pre_step，优先用日志值（现状没有，所以来源会标"由日志推导"）。
    logged_trend = [o for o in observed if isinstance(o.get("trend"), (int, float))]
    trend_source = "日志字段 trend" if logged_trend else "由 ratio 序列按同一公式推导"
    derived_triggers = [s for s in samples
                        if s["phase"] == "shift" and s["trend"] > trend_thr]
    # 实现语义：shift 阶段 trend 超门槛才提前补锚；critical 是无条件锚定，不归 trend 触发。
    logged_triggers = [{"turn": o["turn"], "trend": None}
                       for o in observed if o["phase"] == "shift" and o["anchor"] is True]
    triggers = logged_triggers if anchor_logged_available else derived_triggers

    return {
        "targets": {"phaseShiftRatio": shift_thr, "phaseShiftHardRatio": hard_thr,
                    "trendThreshold": trend_thr},
        "first_step_rows": first_step_total,
        "observed": observed,
        "observed_n": len(observed),
        "legacy_turns": sorted(set(legacy_turns)),
        "legacy_n": len(set(legacy_turns)),
        "ratios": ratios,
        "p50": percentile(ratios, 50), "p90": percentile(ratios, 90),
        "p95": percentile(ratios, 95), "max": max(ratios) if ratios else None,
        "min": min(ratios) if ratios else None,
        "crossings_shift": sum(1 for r in ratios if r >= shift_thr),
        "crossings_hard": sum(1 for r in ratios if r >= hard_thr),
        "boundary_shift": sum(1 for r in ratios if 0.60 <= r < 0.80),
        "boundary_hard": sum(1 for r in ratios if 0.80 <= r < 0.90),
        "phase_counts": dict(phase_counts),
        "phase_mismatches": mismatches,
        "anchor_logged_available": anchor_logged_available,
        "anchor_true_n": len(anchor_true),
        "anchor_shift_n": len(anchor_shift),
        "anchor_critical_n": len(anchor_critical),
        "anchor_true_turns": [o["turn"] for o in anchor_true],
        "shift_turns": [o["turn"] for o in observed if o["phase"] == "shift"],
        "critical_turns": [o["turn"] for o in observed if o["phase"] == "critical"],
        "curve": [{"turn": o["turn"], "step": o["step"], "ratio": o["ratio"],
                   "phase": o["phase"], "anchor": o["anchor"],
                   "verdict": o["verdict"], "blocked": o["blocked"]}
                  for o in observed],
        "trend": {
            "source": trend_source,
            "threshold": trend_thr,
            "runs": len(runs),
            "samples": samples,
            "sample_n": len(samples),
            "values": [s["trend"] for s in samples],
            "max": max((s["trend"] for s in samples), default=None),
            "triggered": triggers,
            "triggered_n": len(triggers),
            "trigger_source": "日志 anchor 字段" if anchor_logged_available else "由实现公式推导",
            "near_threshold": sum(1 for s in samples if 0.01 <= s["trend"] <= 0.03),
        },
    }


# ── 3. STPA 前置审计 ──────────────────────────────────────

def summarize_stpa(turns: list[dict], audit: list[dict]) -> dict:
    rows = [o for t in turns for o in t["control_rows"]]
    observed = [o for o in rows if o["verdict"] is not None]
    verdicts = Counter(o["verdict"] for o in observed)
    ucas = Counter()
    downgrades = Counter()
    for o in observed:
        ucas.update(o["ucas"])
        downgrades.update(o["downgrades"])
    block_events = [r for r in audit if r.get("kind") == "preflight_block"]
    downgrade_events = [r for r in audit if r.get("kind") == "preflight_downgrade"]
    block_event_ucas = Counter()
    for r in block_events:
        for u in r.get("ucas") or []:
            block_event_ucas[str(u)] += 1
    downgrade_event_ids = Counter()
    for r in downgrade_events:
        for d in r.get("downgrades") or []:
            downgrade_event_ids[str(d)] += 1
    block_n = verdicts.get("block", 0)
    allow_n = verdicts.get("allow", 0)
    return {
        "observed_n": len(observed),
        "legacy_n": len(rows) - len(observed),
        "verdicts": dict(verdicts),
        "ucas": dict(ucas),
        "downgrades": dict(downgrades),
        "block_n": block_n,
        "allow_n": allow_n,
        "block_rate": (block_n / len(observed)) if observed else None,
        "all_block": bool(observed) and block_n == len(observed),
        "any_block": block_n > 0,
        "block_events": {"n": len(block_events), "ucas": dict(block_event_ucas)},
        "downgrade_events": {"n": len(downgrade_events), "ids": dict(downgrade_event_ids)},
        "per_turn": [{"turn": o["turn"], "verdict": o["verdict"],
                      "ucas": o["ucas"], "downgrades": o["downgrades"]}
                     for o in observed],
    }


# ── 4. 记忆 / potency ────────────────────────────────────

def _entry_from_row(raw: dict, model_mod):
    """把插件落盘的 memory.jsonl 行转成 engine.model.Entry（字段名 camelCase → snake_case）。"""
    d = {
        "id": str(raw.get("id") or "?"),
        "type": raw.get("type") if raw.get("type") in ("episodic", "semantic", "procedural") else "semantic",
        "subject": str(raw.get("subject") or ""),
        "relation": str(raw.get("relation") or ""),
        "object": str(raw.get("object") or ""),
        "content": str(raw.get("content") or ""),
        "importance": float(raw.get("importance", 0.5) or 0.0),
        "confidence": float(raw.get("confidence", 1.0) or 0.0),
        "access_times": [float(x) for x in (raw.get("access_times") or [])
                         if isinstance(x, (int, float))],
        "source_trust": raw.get("sourceTrust") or raw.get("source_trust") or "user_explicit",
        "status": str(raw.get("status") or "active"),
        "layer": str(raw.get("layer") or "warm"),
        "keywords": list(raw.get("keywords") or []),
        "depends_on": list(raw.get("depends_on") or []),
        "edges": list(raw.get("edges") or []),
        "last_access": _to_seconds(raw.get("lastAccess", raw.get("last_access", 0))),
        "session_id": str(raw.get("session_id") or raw.get("sessionId") or ""),
        "title": str(raw.get("title") or ""),
    }
    return model_mod.Entry.from_json(d)


def summarize_memory(memory: list[dict], audit: list[dict], now_ms: float) -> dict:
    status = Counter(str(r.get("status") or "unknown") for r in memory)
    layer = Counter(str(r.get("layer") or "unknown") for r in memory)
    etype = Counter(str(r.get("type") or "unknown") for r in memory)
    origin = Counter(str(r.get("origin") or "unknown") for r in memory)

    write_events = [r for r in audit if r.get("kind") == "memory_write"]
    written = sum(int(r.get("written") or 0) for r in write_events)
    rejected: list[str] = []
    for r in write_events:
        for x in r.get("rejected") or []:
            rejected.append(str(x))
    reject_reasons = Counter(x.split(":", 1)[0] for x in rejected)

    conflict_kinds = ("supersede", "stale", "conflict", "dedupe", "dedupe_dormant",
                      "forget_soft", "forget_hard")
    conflict_events = [r for r in audit
                       if str(r.get("kind", "")).split(".")[0] in conflict_kinds]
    non_active = {k: v for k, v in status.items() if k != "active"}

    eng = _load_engine()
    pot = {"available": eng is not None, "now_ms": now_ms, "rows": [], "errors": [],
           "p50": None, "min": None, "max": None, "above_archive": 0, "below_archive": 0}
    plugin_rows: list[dict] = []
    now_sec = float(now_ms) / 1000.0

    for raw in memory:
        try:
            imp = float(raw.get("importance", 0.0) or 0.0)
        except (TypeError, ValueError):
            imp = 0.0                     # 坏数据不拖垮整份报告，只影响该条 potency
        last_ms = raw.get("lastAccess", raw.get("last_access", 0))
        last_sec = _to_seconds(last_ms)
        days = max(0.0, (now_sec - last_sec) / 86400.0) if last_sec else None
        # 插件侧口径（index.js#potency）：纯指数衰减 + tauDays；与引擎双通道口径对照用。
        tau_days = float(raw.get("tauDays") or 30.0)
        p_plugin = None
        if days is not None and str(raw.get("status") or "active") not in ("superseded", "suppressed"):
            tau = tau_days * (0.5 + imp)
            p_plugin = imp * math.exp(-days / tau) if tau > 0 else 0.0
        plugin_rows.append({"id": raw.get("id"), "type": raw.get("type"),
                            "status": raw.get("status"), "importance": imp,
                            "age_days": days, "potency": p_plugin})

        if eng is None:
            continue
        try:
            e = _entry_from_row(raw, eng["model"])
            cfg = eng["config"].default()
            tau = eng["memory"].tau_eff(e, cfg)
            decay = imp * math.exp(-days / tau) if days is not None else None
            w = float(cfg["memory"]["actrWeight"])
            actr_p = eng["actr"].potency(e, now_sec) * w
            p = eng["memory"].potency(e, now_sec, cfg)
            archive = float(cfg["potency"]["archiveThreshold"])
            pot["rows"].append({
                "id": e.id, "type": raw.get("type"), "importance": imp,
                "status": e.status, "age_days": days, "tau_days": tau,
                "decay": decay, "actr": actr_p, "potency": p,
                "band": "high" if p >= 0.6 else ("mid" if p >= 0.3 else "low"),
                "above_archive": p >= archive,
            })
        except Exception as exc:                     # 单条坏数据不拖垮整份报告
            pot["errors"].append("%s: %s" % (raw.get("id"), str(exc)[:120]))

    if pot["rows"]:
        vals = [r["potency"] for r in pot["rows"]]
        pot["p50"] = percentile(vals, 50)
        pot["min"], pot["max"] = min(vals), max(vals)
        pot["above_archive"] = sum(1 for r in pot["rows"] if r["above_archive"])
        pot["below_archive"] = len(pot["rows"]) - pot["above_archive"]
        archive = None
        if eng is not None:
            archive = float(eng["config"].default()["potency"]["archiveThreshold"])
        pot["archive_threshold"] = archive

    return {
        "entries": len(memory),
        "status": dict(status), "layer": dict(layer), "type": dict(etype), "origin": dict(origin),
        "non_active": non_active,
        "potency": pot,
        "plugin_decay": {"rows": sorted(plugin_rows, key=lambda r: -(r["potency"] or 0.0))},
        "writes": {"events": len(write_events), "written": written,
                   "rejected": len(rejected), "reject_reasons": dict(reject_reasons)},
        "conflict_events": {"n": len(conflict_events),
                            "kinds": dict(Counter(str(r.get("kind")) for r in conflict_events))},
    }


# ── 5. 影子模块（Goal / Relation 只记录不注入）──────────────

def summarize_shadow(audit: list[dict], state: dict, memory_summary: dict) -> dict:
    apply_events = [r for r in audit if r.get("kind") == "apply"]
    latest_shadow = None
    shadow_ts = None
    control_since = None
    for r in apply_events:
        if isinstance(r.get("shadow"), dict):
            latest_shadow = {"goal": bool(r["shadow"].get("goal")),
                             "relation": bool(r["shadow"].get("relation"))}
            shadow_ts = r.get("ts")
        keys = r.get("configKeys") or []
        if isinstance(keys, list) and "control" in keys and control_since is None:
            control_since = r.get("ts")

    agent_events = [r for r in audit if r.get("kind") == "agent_state"]
    goal_events = Counter()
    relation_events = Counter()
    trust = Counter()
    modifiers: list[float] = []
    for r in agent_events:
        for e in r.get("goalEvents") or []:
            goal_events[str(e)] += 1
        if r.get("relationEvent"):
            relation_events[str(r["relationEvent"])] += 1
        if r.get("trust"):
            trust[str(r["trust"])] += 1
        if isinstance(r.get("modifier"), (int, float)):
            modifiers.append(float(r["modifier"]))

    goal_state = state.get("goal") if isinstance(state.get("goal"), dict) else {}
    relation_state = state.get("relation") if isinstance(state.get("relation"), dict) else {}
    return {
        "agent_state_events": len(agent_events),
        "goal_events": dict(goal_events),
        "relation_events": dict(relation_events),
        "trust": dict(trust),
        "modifier": {"n": len(modifiers), "min": min(modifiers) if modifiers else None,
                     "max": max(modifiers) if modifiers else None},
        "shadow_flags": latest_shadow,
        "shadow_flags_ts": shadow_ts,
        "shadow_observable": latest_shadow is not None,
        "apply_events": len(apply_events),
        "control_layer_since": control_since,
        "control_layer_observed": control_since is not None,
        "state_goal": {"active": goal_state.get("order") or [],
                       "events": len(goal_state.get("events") or [])},
        "state_relation": {"trustLevel": relation_state.get("trustLevel"),
                           "stage": relation_state.get("stage"),
                           "positives": relation_state.get("positives")},
        "memory_origin": memory_summary.get("origin", {}),
    }


# ── 5b. 规则自动结果标签（辅助信号：只统计，不入门槛）──────────

def _quality_ratio_bucket(ratio) -> str:
    """把标签事件里的 phase.ratio 归到固定桶；缺失/非数值 → unknown（不猜）。"""
    if not isinstance(ratio, (int, float)) or isinstance(ratio, bool):
        return "unknown"
    r = float(ratio)
    if r < 0.70:
        return "<0.70"
    if r < 0.85:
        return "0.70-0.85"
    return ">=0.85"


def _rate_per_100(labels: int, observed: int) -> float | None:
    if not observed:
        return None
    return round(100.0 * labels / observed, 2)


def _quality_phase_bucket(ph) -> str:
    """标签事件的 ratio 桶；插件在"尚无相变观测"时写 ratio=0 / phase="unknown"。

    这种占位值必须归入 unknown 桶，不能当成真实的 `<0.70` 观测——否则就是把"未观测"
    填成 0（与 docs/20 §1.3 S2 同一条纪律）。
    """
    if not isinstance(ph, dict):
        return "unknown"
    name = ph.get("phase")
    if isinstance(name, str) and name.strip().lower() in ("unknown", "none", ""):
        return "unknown"
    return _quality_ratio_bucket(ph.get("ratio"))


def summarize_quality_labels(audit: list[dict], phase: dict | None = None) -> dict:
    """统计 audit 里 kind=="quality_label" 的**规则自动标签**（decisions/012 §2.7）。

    这些计数只用于报告展示：**不会**被 evaluate_calibration 使用——规则自动标签不是人工
    结果标签，不得计入 REQUIREMENTS 的 outcome_labels 门槛（也绝不进 OUTCOME_KINDS）。
    旧日志没有该事件时返回 total=0（"未观测"），不把缺失折成 0 标签率结论。
    """
    phase = phase if isinstance(phase, dict) else {}
    observed = [o for o in (phase.get("observed") or []) if isinstance(o, dict)]
    observed_n = len(observed)
    obs_buckets = Counter(_quality_ratio_bucket(o.get("ratio")) for o in observed)

    events = [r for r in audit if r.get("kind") == "quality_label"]
    by_kind = {k: 0 for k in QUALITY_LABEL_KINDS}
    by_source = Counter()
    bucket_labels = {b: 0 for b in QUALITY_LABEL_RATIO_BUCKETS}
    unknown_kind = 0          # 闭集外的字符串 kind_label：schema 漂移，单列不合并
    invalid = 0               # kind_label 缺失/非字符串：坏事件不猜标签
    recall_events = 0
    rows: list[dict] = []

    for r in events:
        label = r.get("kind_label")
        if not isinstance(label, str) or not label:
            invalid += 1
        elif label in by_kind:
            by_kind[label] += 1
        else:
            unknown_kind += 1
        src = r.get("source")
        by_source[src if isinstance(src, str) and src else "unknown"] += 1

        ph = r.get("phase")
        ph = ph if isinstance(ph, dict) else {}
        ratio = ph.get("ratio")
        bucket_labels[_quality_phase_bucket(ph)] += 1

        rid = r.get("recall_ids")
        recall_ids = [str(x) for x in rid] if isinstance(rid, list) else []
        if recall_ids:
            recall_events += 1
        quote = r.get("quote")
        rows.append({
            "turn": r.get("turn") if isinstance(r.get("turn"), (int, float)) else None,
            "kind_label": label if isinstance(label, str) else None,
            "quote": quote if isinstance(quote, str) else None,
            "ratio": (float(ratio) if isinstance(ratio, (int, float))
                      and not isinstance(ratio, bool) else None),
            "phase": ph.get("phase") if isinstance(ph.get("phase"), str) else None,
            "anchor": ph.get("anchor") if isinstance(ph.get("anchor"), bool) else None,
            "recall_ids": recall_ids,
            "source": src if isinstance(src, str) else None,
        })

    total = len(events)
    recognized = sum(by_kind.values())
    ratio_buckets = {}
    for b in QUALITY_LABEL_RATIO_BUCKETS:
        n_obs = int(obs_buckets.get(b, 0))
        ratio_buckets[b] = {
            "labels": bucket_labels[b],
            "observed_turns": n_obs,
            "per_100_observed_turns": _rate_per_100(bucket_labels[b], n_obs),
        }
    return {
        "total": total,
        "recognized": recognized,
        "unknown_kind": unknown_kind,
        "invalid": invalid,
        "by_kind": by_kind,
        "by_source": dict(by_source),
        "ratio_buckets": ratio_buckets,
        "observed_turns": observed_n,
        "per_100_observed_turns": _rate_per_100(total, observed_n),
        "recall_ids_events": recall_events,
        "events": rows,
        "observed": total > 0,
        "note": ("规则自动标签（插件按用户消息子串规则产出）仅作辅助统计；"
                 "不进入 OUTCOME_KINDS / REQUIREMENTS 的 outcome_labels 门槛，"
                 "不改变 upgrade_eligible——规则自动标签 ≠ 人工结果标签。"),
    }


# ── 6. 标定判定（guess → measured 的机检）───────────────────

def _outcome_labels(audit: list[dict], data_dir: str) -> tuple[int, str, list[dict]]:
    """统计结果标签样本数。当前仓库还没有 corrections 通道（docs/17 设计未实现）。"""
    hits: list[dict] = [r for r in audit if str(r.get("kind", "")) in OUTCOME_KINDS]
    corrections_path = os.path.join(data_dir, "corrections.jsonl")
    if os.path.isfile(corrections_path):
        recs, _info = read_jsonl(corrections_path)
        return len(recs), "corrections.jsonl", hits
    if hits:
        return len(hits), "audit 结果标签事件", hits
    return 0, "无（未实现 corrections 通道 / 无标签事件）", hits


def evaluate_calibration(phase: dict, stpa: dict, audit: list[dict],
                         state: dict, controls: dict, control_source: str,
                         data_dir: str, memory_summary: dict) -> dict:
    observed_n = phase["observed_n"]
    shift_obs = int(phase["phase_counts"].get("shift", 0))
    critical_obs = int(phase["phase_counts"].get("critical", 0))
    trend = phase["trend"]
    anchor_logged = phase["anchor_logged_available"]
    triggers_n = trend["triggered_n"]
    outcome_n, outcome_source, _hits = _outcome_labels(audit, data_dir)
    window = (state.get("control") or {}).get("contextWindow") if isinstance(state.get("control"), dict) else None
    window_source = 1 if isinstance(window, (int, float)) and window > 0 else 0

    # 相变期放行且无 UCA-07 = "相变补偿确实生效"的正样本；只有出现这种轮次，才能谈
    # "补锚点有效"，否则 UCA-07 的 0 命中只是没有机会触发的假阴性。
    prefeed_rows = [o for o in phase["observed"]
                    if o["phase"] in ("shift", "critical") and o["verdict"] == "allow"]
    prefeed_allowed_nouca07 = sum(1 for o in prefeed_rows if "UCA-07" not in o["ucas"])

    evidence = {
        "observed_turns": observed_n,
        "crossings_shift": phase["crossings_shift"],
        "crossings_hard": phase["crossings_hard"],
        "boundary_shift": phase["boundary_shift"],
        "boundary_hard": phase["boundary_hard"],
        "window_source": window_source,
        "outcome_labels": outcome_n,
        "prefeed_allowed_nouca07": prefeed_allowed_nouca07,
        "shift_observations": shift_obs,
        "critical_observations": critical_obs,
        "trend_samples": trend["sample_n"],
        "trend_near": trend["near_threshold"],
        "trend_triggers": triggers_n,
        "trend_trigger_source": trend["trigger_source"],
        "anchor_true_n": phase["anchor_true_n"],
        "anchor_logged_available": anchor_logged,
    }

    params = []
    for key, reqs in REQUIREMENTS.items():
        gates = []
        for r in reqs:
            have = int(evidence.get(r["gate"], 0) or 0)
            gates.append({"gate": r["gate"], "label": r["label"], "kind": r["kind"],
                          "need": int(r["need"]), "have": have, "met": have >= int(r["need"])})
        blocking = [g for g in gates if not g["met"]]
        distribution_ready = all(g["met"] for g in gates if g["kind"] != "outcome")
        upgrade_eligible = all(g["met"] for g in gates)
        if upgrade_eligible:
            verdict = "证据齐备：可提交 guess→measured（仍需人工复核并同步 assumptions/已验证.md）"
        elif distribution_ready:
            verdict = "分布证据够，但缺结果标签 → 不能升级 measured"
        else:
            verdict = "保持 guess：样本不足（%s）" % ("；".join(
                "%s %d/%d" % (g["label"], g["have"], g["need"]) for g in blocking[:4]))
        params.append({
            "key": key,
            "param": "control." + key,
            "title": PARAM_TITLES[key],
            "current_value": controls.get(key),
            "current_provenance": "guess",       # 当前 rules 全为 guess（docs/15 §14）
            "proposed_provenance": "measured" if upgrade_eligible else "guess",
            "distribution_ready": distribution_ready,
            "upgrade_eligible": upgrade_eligible,
            "gates": gates,
            "blocking": blocking,
            "evidence": evidence,
            "verdict": verdict,
        })

    # 监测类声明（不是参数升级，而是"能不能据此宣称某机制有效"）——
    # 这些同样需要样本量门槛，否则会把"没触发"误读成"没问题"。
    uca01_n = int(stpa["ucas"].get("UCA-01", 0)) + int(stpa["block_events"]["ucas"].get("UCA-01", 0))
    uca08_n = int(stpa["downgrades"].get("UCA-08", 0)) + \
        int(stpa["downgrade_events"]["ids"].get("UCA-08", 0))
    conflict_n = int(memory_summary["conflict_events"]["n"])
    non_active = memory_summary.get("non_active", {})
    non_active_n = sum(int(v) for v in non_active.values()) if isinstance(non_active, dict) else 0
    gates_claims = [
        {"claim": "STPA 熔断 100% 有效（UCA-01 越狱尝试全部被 block）",
         "need": "越狱尝试(UCA-01) ≥20 次 + 熔断率=100% + 0 漏放",
         "have": "UCA-01 命中 %d 次；pre_step 观测 %d 轮" % (uca01_n, stpa["observed_n"]),
         "met": uca01_n >= 20 and stpa["all_block"]},
        {"claim": "UCA-08（积分饱和）降级可观测",
         "need": "≥10 次饱和降级事件",
         "have": "%d 次" % uca08_n, "met": uca08_n >= 10},
        {"claim": "supersede 误判率 ≤5%（G1）",
         "need": "≥50 次 supersede 记录 + ≥3 次人工抽检",
         "have": "冲突类审计事件 %d 次、非 active 条目 %d 条（当前插件无 supersede 审计通道）"
                 % (conflict_n, non_active_n),
         "met": conflict_n >= 50},
        {"claim": "potency 归档门 0.15 可标定",
         "need": "≥1000 个真实 turn（docs/17 §5 W4.5）",
         "have": "%d 个 turn" % int((state.get("turns") or 0) or observed_n), "met": False},
        {"claim": "影子 goal/relation 误报率可评估",
         "need": "≥200 条 agent_state 记录 + ≥50 条人工标注",
         "have": "%d 条记录" % int(state.get("_agent_state_n", 0)), "met": False},
    ]
    return {
        "control_params_source": control_source,
        "targets": phase["targets"],
        "evidence": evidence,
        "outcome_source": outcome_source,
        "params": params,
        "upgradeable": [p["param"] for p in params if p["upgrade_eligible"]],
        "distribution_ready": [p["param"] for p in params if p["distribution_ready"]],
        "measured_count_after_run": 0,
        "claims": gates_claims,
    }


# ── 7. 汇总入口 ──────────────────────────────────────────

def analyze(data_dir: str, now_ms: float | None = None) -> dict:
    """读三个文件，产出结构化分析结果（纯数据，不写任何文件）。"""
    data_dir = os.path.abspath(data_dir)
    audit, audit_info = read_jsonl(os.path.join(data_dir, "audit.jsonl"))
    memory, memory_info = read_jsonl(os.path.join(data_dir, "memory.jsonl"))
    state, state_info = read_json(os.path.join(data_dir, "state.json"))
    controls, control_source = control_defaults()

    # 数据时点：取日志内最大时间戳。**不用墙钟**——否则同一份日志隔天再跑，
    # potency 会变，报告不可复现（PIT 纪律）。
    ts_candidates = [float(r["ts"]) for r in audit if isinstance(r.get("ts"), (int, float))]
    mem_max = 0.0
    for r in memory:
        v = r.get("lastAccess", r.get("last_access"))
        if isinstance(v, (int, float)):
            mem_max = max(mem_max, _to_seconds(v) * 1000.0)
    data_asof_ms = max(ts_candidates + ([mem_max] if mem_max else [])) if \
        (ts_candidates or mem_max) else None
    analysis_ms = float(now_ms) if now_ms else (data_asof_ms if data_asof_ms else 0.0)
    if analysis_ms and analysis_ms < 1e11:            # 容错：有人把秒当毫秒传
        analysis_ms *= 1000.0

    turns, steps_total = build_turns(audit)
    phase = summarize_phase(turns, controls)
    stpa = summarize_stpa(turns, audit)
    quality_labels = summarize_quality_labels(audit, phase)
    memory_summary = summarize_memory(memory, audit, analysis_ms)
    shadow = summarize_shadow(audit, state, memory_summary)
    if isinstance(state.get("turns"), (int, float)):
        sample_turns = int(state["turns"])
    else:
        sample_turns = max((t["turn"] for t in turns), default=0)

    # 让 claims 能引用 memory/shadow 的规模（避免 evaluate_calibration 再传一堆参数）。
    state_view = dict(state)
    state_view["_agent_state_n"] = shadow["agent_state_events"]
    if "turns" not in state_view:
        state_view["turns"] = sample_turns

    calibration = evaluate_calibration(phase, stpa, audit, state_view, controls,
                                       control_source, data_dir, memory_summary)

    stages = Counter(t.get("stage") for t in turns if t.get("stage"))
    kinds = Counter(str(r.get("kind")) for r in audit)
    window_ts = [float(r["ts"]) for r in audit if isinstance(r.get("ts"), (int, float))]
    return {
        "dir": data_dir,
        "generated_at_ms": _dt.datetime.now().timestamp() * 1000.0,
        "data_asof_ms": data_asof_ms,
        "data_asof": _iso(data_asof_ms),
        "analysis_at": _iso(analysis_ms),
        "files": {"audit": audit_info, "memory": memory_info, "state": state_info},
        "audit_kinds": dict(kinds),
        "controls": controls,
        "control_source": control_source,
        "sample": {
            "audit_events": len(audit),
            "skipped_lines": audit_info["skipped"] + memory_info["skipped"],
            "turns": sample_turns,
            "turn_begin_events": sum(1 for t in turns if not t.get("unattributed")),
            "steps": steps_total,
            "first_step_rows": phase["first_step_rows"],
            "phase_observed": phase["observed_n"],
            "phase_legacy_turns": phase["legacy_n"],
            "quality_label_events": quality_labels["total"],
            "stages": dict(stages),
            "apply_events": sum(1 for r in audit if r.get("kind") == "apply"),
            "memory_entries": len(memory),
            "window": {"start": _iso(min(window_ts)) if window_ts else "—",
                       "end": _iso(max(window_ts)) if window_ts else "—"},
            "state": {"turns": state.get("turns"), "sessions": state.get("sessions"),
                      "stateWrites": state.get("stateWrites"),
                      "coldStartSuppressed": state.get("coldStartSuppressed"),
                      "control": {"phase": (state.get("control") or {}).get("phase"),
                                  "contextWindow": (state.get("control") or {}).get("contextWindow"),
                                  "blocked": (state.get("control") or {}).get("blocked")}},
        },
        "phase": phase,
        "stpa": stpa,
        "quality_labels": quality_labels,
        "memory": memory_summary,
        "shadow": shadow,
        "calibration": calibration,
    }


# ── 8. Markdown 报告 ─────────────────────────────────────

def _md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(out)


def _num(v, n=4):
    return "—" if v is None else ("%.*f" % (n, float(v)) if isinstance(v, (int, float)) else str(v))


def build_candidates(res: dict) -> dict:
    """provenance-candidates.json：只是候选，绝不直接改 rules/。"""
    cal = res["calibration"]
    ql = res["quality_labels"]
    params = []
    for p in cal["params"]:
        params.append({
            "param": p["param"],
            "current_value": p["current_value"],
            "current_provenance": p["current_provenance"],
            "proposed_provenance": p["proposed_provenance"],
            "distribution_ready": p["distribution_ready"],
            "upgrade_eligible": p["upgrade_eligible"],
            "evidence": p["evidence"],
            "gates": p["gates"],
            "blocking": p["blocking"],
            "verdict": p["verdict"],
        })
    return {
        "schema": "lingshu/provenance-candidates/v1",
        "generated_by": "tools/analyze_shadow.py",
        "data_dir": res["dir"],
        "data_asof": res["data_asof"],
        "data_asof_ms": res["data_asof_ms"],
        "analysis_at": res["analysis_at"],
        "sample": {"audit_events": res["sample"]["audit_events"],
                   "turns": res["sample"]["turns"],
                   "phase_observed": res["sample"]["phase_observed"],
                   "memory_entries": res["sample"]["memory_entries"]},
        "control_params_source": cal["control_params_source"],
        "note": ("本文件是候选建议，不修改 rules/decay-params.yaml。任何 guess→measured 变更都必须"
                 "按 docs/20-真实标定协议.md 的流程人工执行，并同步更新 assumptions/已验证.md；"
                 "样本不足时本文件会给出 proposed_provenance=guess。"),
        "params": params,
        "monitoring_claims": cal["claims"],
        # 辅助块（只追加）：规则自动标签统计，**不参与**任何门槛/候选判定。
        "quality_labels": {
            "auxiliary_only": True,
            "note": ql["note"],
            "total": ql["total"],
            "recognized": ql["recognized"],
            "unknown_kind": ql["unknown_kind"],
            "invalid": ql["invalid"],
            "by_kind": ql["by_kind"],
            "by_source": ql["by_source"],
            "ratio_buckets": ql["ratio_buckets"],
            "observed_turns": ql["observed_turns"],
            "per_100_observed_turns": ql["per_100_observed_turns"],
        },
        "summary": {"upgrade_eligible": cal["upgradeable"],
                    "distribution_ready": cal["distribution_ready"],
                    "measured_count_after_run": cal["measured_count_after_run"]},
    }


def render_report(res: dict, candidates_path: str | None) -> str:
    s = res["sample"]
    ph = res["phase"]
    st = res["stpa"]
    mem = res["memory"]
    sh = res["shadow"]
    cal = res["calibration"]
    L = []
    A = L.append

    A("# LINGSHU 真实标定 · 影子基线报告")
    A("")
    A("> **本文件由 `tools/analyze_shadow.py` 生成，请勿手改**；重跑即刷新。")
    A("> 生成时间（本机）：%s ｜ 数据目录：`%s`" % (_iso(res["generated_at_ms"]), res["dir"]))
    A("> **数据时点（data as-of）**：%s（取日志内最大时间戳；本报告所有 potency 都按此时点计算，"
      "不用墙钟——隔天重跑同一份日志结果不变）" % res["data_asof"])
    A("> 退出码：0 = 分析完成；**数据不足同样 exit 0**，但下文会显式写「样本不足」。")
    A("")
    A("## 0. 证据标签（每个数字都要能归到一类）")
    A("")
    A(_md_table(["标签", "含义", "典型内容"], [
        [LABEL_REAL, "直接数日志事件 / 读 state 字段", "turn 数、verdict 分布、UCA 命中、memory 条数"],
        [LABEL_DERIVED, "对实测数据按实现公式做推导（不引入外部假设）", "由 ratio 序列算 trend、越界 turn 列表"],
        [LABEL_IMPL, "用 engine/ 参考实现现算（输入是真实条目）", "内存条目 potency / ACT-R 分量"],
    ]))
    A("")
    A("> 本工具只读：不写插件数据目录、不改 `rules/`、不写 `plugin/`。")
    A("")

    # ── 1. 规模 ──
    A("## 1. 数据规模与可用性（%s）" % LABEL_REAL)
    A("")
    files_present = {k: v.get("exists") for k, v in res["files"].items()}
    missing = [k for k, v in files_present.items() if not v]
    rows = [
        ["audit 事件", s["audit_events"], "bad 行 %d" % s["skipped_lines"]],
        ["turn_begin 事件（= 可归属轮数）", s["turn_begin_events"], "state.turns=%s" % s["state"]["turns"]],
        ["pre_step 事件总数（每 step 一条）", s["steps"], "其中 firstStep 行 %d" % s["first_step_rows"]],
        ["有控制字段的 turn 观测", s["phase_observed"], "ratio/phase 可算的轮数"],
        ["legacy turn（缺控制字段，无法补算）", s["phase_legacy_turns"], "旧版插件日志"],
        ["插件 apply（≈进程启动次数）", s["apply_events"], "控制论层首次启用：%s" % _iso(sh["control_layer_since"])],
        ["memory 条目", s["memory_entries"], "stateWrites=%s" % s["state"]["stateWrites"]],
        ["quality_label 事件（规则自动标签）", s["quality_label_events"],
         "辅助信号，**不进入**标定门槛（见 §2.6）"],
        ["数据窗口", "%s ~ %s" % (s["window"]["start"], s["window"]["end"]), "本地时间"],
        ["stage 分布", json.dumps(s["stages"], ensure_ascii=False) if s["stages"] else "—", "cold/warm/regular"],
        ["文件缺失", ", ".join(missing) if missing else "无", "缺失 = 该维度样本不足"],
    ]
    A(_md_table(["指标", "值", "备注"], rows))
    A("")

    # ── 2. 相变 ──
    A("## 2. 相变监控（双阈值 / 趋势 / 锚点）")
    A("")
    A("### 2.1 ratio 分布（%s）" % LABEL_REAL)
    A("")
    if ph["observed_n"] == 0:
        A("**样本不足（0 个可用观测），无法计算 ratio 分布。**")
        if ph["legacy_n"]:
            A("有 %d 个 turn 的 pre_step 缺 `ratio/phase` 字段（legacy 日志，插件版本早于控制论层），"
              "这些不是 0，是**未观测**，不能拿来凑样本。" % ph["legacy_n"])
    else:
        A(_md_table(["n", "p50", "p90", "p95", "min", "max", "≥%s（shift）" % _num(ph["targets"]["phaseShiftRatio"], 2),
                     "≥%s（critical）" % _num(ph["targets"]["phaseShiftHardRatio"], 2)],
                    [[ph["observed_n"], _num(ph["p50"]), _num(ph["p90"]), _num(ph["p95"]),
                      _num(ph["min"]), _num(ph["max"]), ph["crossings_shift"], ph["crossings_hard"]]]))
        A("")
        A("阈值边界样本：`0.60≤ratio<0.80` %d 个；`0.80≤ratio<0.90` %d 个。"
          "**边界样本为 0 时，阈值放在 0.70 还是 0.75 对观测数据没有区别——这段分布不含标定信息。**"
          % (ph["boundary_shift"], ph["boundary_hard"]))
        if ph["phase_mismatches"]:
            A("")
            A("⚠️ phase 与 ratio 重算不一致 %d 处（插件版本/参数与 rules 不同源）：%s"
              % (len(ph["phase_mismatches"]),
                 "; ".join("turn %s ratio=%s 日志=%s 应为=%s" % (
                     m["turn"], _num(m["ratio"]), m["logged"], m["expected"])
                     for m in ph["phase_mismatches"][:5])))
        else:
            A("")
            A("完整性校验：日志 `phase` 与按 ratio 重算的 phase 一致（0 处不一致）。")
    A("")

    A("### 2.2 token 膨胀曲线（ratio vs turn，%s）" % LABEL_REAL)
    A("")
    if not ph["curve"]:
        A("**样本不足（0 个数据点）。** 真实 token 膨胀曲线无法绘制。")
    else:
        A(_md_table(["turn", "step", "ratio", "phase", "anchor", "verdict"],
                    [[c["turn"], c["step"], _num(c["ratio"]), c["phase"] or "—",
                      "—" if c["anchor"] is None else str(c["anchor"]).lower(),
                      c["verdict"] or "—"] for c in ph["curve"]]))
    A("")

    A("### 2.3 阈值越界 / 锚点触发（%s）" % LABEL_REAL)
    A("")
    A(_md_table(["指标", "值", "说明"], [
        ["shift 阶段观测", ph["phase_counts"].get("shift", 0), "phase=shift"],
        ["critical 阶段观测", ph["phase_counts"].get("critical", 0), "phase=critical"],
        ["ratio≥shift 阈值的 turn", ph["crossings_shift"], "触发所在 turn：%s" % (ph["shift_turns"] or "无")],
        ["ratio≥hard 阈值的 turn", ph["crossings_hard"], "触发所在 turn：%s" % (ph["critical_turns"] or "无")],
        ["锚点开启次数（日志 anchor=true）", ph["anchor_true_n"],
         "所在 turn：%s" % (ph["anchor_true_turns"] or "无")],
        ["其中 shift 趋势触发 / critical 无条件", "%d / %d" % (ph["anchor_shift_n"], ph["anchor_critical_n"]),
         "实现语义：critical 必锚；shift 需 trend>门槛"],
    ]))
    A("")

    A("### 2.4 trend（趋势提前量，%s）" % LABEL_DERIVED)
    A("")
    tr = ph["trend"]
    A("> 当前 audit 的 `pre_step` **不记录 trend 字段**，下表按 `engine/control.py` 同一公式"
      " `trend=(r[-1]-r[-3])/2` 由 ratio 序列推导；turn 不连续处会断开，不跨空洞硬拼。")
    A("")
    A(_md_table(["指标", "值", "门槛"], [
        ["连续观测段数", tr["runs"], "—"],
        ["可算 trend 的样本", tr["sample_n"], "每 3 个连续 ratio 观测出 1 个"],
        ["trend max", _num(tr["max"]), "—"],
        ["靠近门槛（0.01≤trend≤0.03）", tr["near_threshold"], "≥5 才谈得上门槛落点"],
        ["trend 触发锚点次数", tr["triggered_n"], "触发来源：%s" % tr["trigger_source"]],
        ["门槛", _num(tr["threshold"]), "rules#control.trendThreshold"],
    ]))
    if tr["triggered"]:
        A("")
        A("触发 turn：" + ", ".join(str(x["turn"]) for x in tr["triggered"]))
    A("")

    A("### 2.5 观测口径警告（真实数据接入的关键）")
    A("")
    cw = s["state"]["control"]["contextWindow"]
    A("- `state.control.contextWindow` = %s；插件缺省窗口 `contextWindowTokens` = 1M（1048576）。"
      % ("%s" % cw if cw not in (None, 0) else "**未捕获（null）**"))
    A("- 若宿主 `request/context` 事件没带 `contextWindow`，插件会退回默认 1M。此时 "
      "`ratio=used/1048576` 几乎恒为 0——**它反映的是窗口口径未知，不是「上下文没膨胀」**。")
    A("- 在拿到真实窗口之前，ratio 分布对阈值标定**零信息量**：既证明不了 0.70 合适，也证伪不了。")
    A("- 另一个坑：`pre_step.turn` 是宿主 session turn id（实测恒为 1），**不能**当轮次用；"
      "本报告的轮次来自 `turn_begin.turns`。")
    A("")

    # ── 2.6 规则自动结果标签（辅助信号）──
    ql = res["quality_labels"]
    A("### 2.6 结果标签统计（插件规则自动标签，%s；**辅助观察，不进入门槛**）" % LABEL_REAL)
    A("")
    A("> **规则自动标签 ≠ 人工结果标签。** `quality_label` 事件由插件按用户消息子串规则自动产生，"
      "本节只做计数与 ratio 关联展示：**绝不进入** `OUTCOME_KINDS` / `REQUIREMENTS` 的 "
      "`outcome_labels` 门槛，**不改变** `upgrade_eligible`——guess→measured 仍只认 §6 的"
      "标签与人工复核门槛（decisions/012 §2.7）。")
    A("")
    if not ql["observed"]:
        A("**未观测 / 无标签事件**：当前 audit 里没有 `kind==quality_label` 的事件"
          "（旧版插件日志，或规则桩未启用）。这是**未观测**，不是「标签率为 0」的结论，也不是故障。")
        A("")
    else:
        A(_md_table(["指标", "值", "备注"], [
            ["标签事件总数", ql["total"], "kind==quality_label"],
            ["闭集内可识别", ql["recognized"], "计入下方 by_kind"],
            ["闭集外 kind_label", ql["unknown_kind"], "schema 漂移：单列、不静默合并"],
            ["kind_label 缺失/非法", ql["invalid"], "坏事件不猜标签"],
            ["带 recall_ids 的事件", ql["recall_ids_events"], "召回 id 属检测时刻最近观测"],
            ["有 ratio 观测的 turn", ql["observed_turns"], "C2 标签率的分母"],
            ["每 100 个有 ratio 观测 turn 的标签率",
             _num(ql["per_100_observed_turns"], 2) if ql["per_100_observed_turns"] is not None
             else "—（无 ratio 观测）", "标签总数 / 有 ratio 观测 turn 数 × 100"],
        ]))
        A("")
        A("按闭集 kind 计数（`wrong | missing | should_not_recall | drift | overreach`）：")
        A("")
        A(_md_table(["kind_label", "次数"],
                    [[k, ql["by_kind"][k]] for k in QUALITY_LABEL_KINDS]))
        A("")
        A("按 ratio 桶的标签数与标签率（分母为该桶内有 ratio 观测的 turn）：")
        A("")
        bucket_display = {"<0.70": "<0.70", "0.70-0.85": "0.70–0.85",
                          ">=0.85": "≥0.85", "unknown": "unknown"}
        A(_md_table(["ratio 桶", "标签数", "该桶有 ratio 观测的 turn",
                     "每 100 个有 ratio 观测 turn 的标签率"], [
            [bucket_display[b], v["labels"], v["observed_turns"],
             _num(v["per_100_observed_turns"], 2)
             if v["per_100_observed_turns"] is not None else "—"]
            for b, v in ql["ratio_buckets"].items()]))
        A("")
        A("> 插件在尚无相变观测时写 `ratio=0` / `phase=\"unknown\"`（占位值）；这类标签归入 "
          "`unknown` 桶，**不算作** `<0.70` 的真实观测（不把「未观测」填成 0）。")
        A("")
        A("source 分布：" + json.dumps(ql["by_source"], ensure_ascii=False))
        A("")
        A("标签事件明细（`quote` 只存命中的用户原话**逐字子串**，隐私最小化；"
          "`phase`/`recall_ids` 取检测时刻最近一次观测，可能属于上一轮）：")
        A("")
        A(_md_table(["turn", "kind_label", "ratio", "phase", "anchor", "source",
                     "recall_ids", "quote"],
                    [[e["turn"], e["kind_label"] or "—",
                      _num(e["ratio"]) if e["ratio"] is not None else "—",
                      e["phase"] or "—",
                      "—" if e["anchor"] is None else str(e["anchor"]).lower(),
                      e["source"] or "—",
                      ",".join(e["recall_ids"]) or "—",
                      (e["quote"] or "—").replace("|", "/")]
                     for e in ql["events"]][:20]))
        A("")
        A("> 规则标签的误报/漏报**未做真实测量**（规则桩，无人工标注对照）；"
          "词表与优先级见 `decisions/012` §2.7。")
        A("")

    # ── 3. STPA ──
    A("## 3. STPA 前置审计（%s）" % LABEL_REAL)
    A("")
    if st["observed_n"] == 0:
        A("**样本不足（0 个带 verdict 的轮级观测）**，无法判断熔断率。"
          "legacy 行 %d 个：旧日志没有 verdict 字段，不等于 allow。" % st["legacy_n"])
    else:
        A(_md_table(["verdict", "次数", "占比"], [
            [k, v, "%.0f%%" % (100.0 * v / st["observed_n"])]
            for k, v in sorted(st["verdicts"].items(), key=lambda kv: -kv[1])]))
        A("")
        A("- 熔断率：%s（%d/%d）；是否 100%% 熔断：**%s**"
          % ("%.1f%%" % (100.0 * st["block_rate"]) if st["block_rate"] is not None else "—",
             st["block_n"], st["observed_n"], "是" if st["all_block"] else "否"))
        A("- 说明：全 allow 且越狱尝试为 0 时，「100% 熔断」**不能被当作机制有效的证据**"
          "（没有负样本可触发），只能说明当前流量里没有越狱尝试。")
    A("")
    uca_ids = ["UCA-0%d" % i for i in range(1, 9)]
    uca_rows = []
    for u in uca_ids:
        uca_rows.append([u, st["ucas"].get(u, 0), st["block_events"]["ucas"].get(u, 0),
                         st["downgrades"].get(u, 0) + st["downgrade_events"]["ids"].get(u, 0)])
    A(_md_table(["UCA", "轮级命中", "block 事件级命中", "降级留痕"], uca_rows))
    A("")
    A("- `preflight_block` 事件：%d 条；`preflight_downgrade` 事件：%d 条。"
      % (st["block_events"]["n"], st["downgrade_events"]["n"]))
    A("- UCA-01 命中数即「越狱尝试」代理指标；UCA-08 是降级不熔断（只记 downgrades）。")
    A("")

    # ── 4. 记忆 ──
    A("## 4. 记忆与 potency")
    A("")
    A("### 4.1 条目结构（%s）" % LABEL_REAL)
    A("")
    A(_md_table(["维度", "分布"], [
        ["总数", mem["entries"]],
        ["status", json.dumps(mem["status"], ensure_ascii=False)],
        ["layer", json.dumps(mem["layer"], ensure_ascii=False)],
        ["type", json.dumps(mem["type"], ensure_ascii=False)],
        ["origin", json.dumps(mem["origin"], ensure_ascii=False)],
        ["memory_write 事件 / 写入条数 / 拒绝", "%d / %d / %d（原因：%s）"
         % (mem["writes"]["events"], mem["writes"]["written"], mem["writes"]["rejected"],
            json.dumps(mem["writes"]["reject_reasons"], ensure_ascii=False))],
    ]))
    A("")
    A("### 4.2 potency（%s，now=data as-of）" % LABEL_IMPL)
    A("")
    pot = mem["potency"]
    if not pot["available"]:
        A("engine/ 不可用，potency 无法计算（用插件侧纯衰减口径见下）。")
    elif pot["errors"]:
        A("⚠️ %d 条记忆无法计算 potency：%s" % (len(pot["errors"]), "; ".join(pot["errors"])))
    if not pot["rows"]:
        A("**样本不足（0 条可算记忆）。**")
    else:
        A(_md_table(["id", "type", "status", "importance", "age(d)", "decay", "actr", "potency", "band", "≥archive"],
                    [[r["id"], r["type"], r["status"], _num(r["importance"], 2), _num(r["age_days"], 1),
                      _num(r["decay"]), _num(r["actr"]), _num(r["potency"]), r["band"],
                      "Y" if r["above_archive"] else "N"] for r in pot["rows"]][:50]))
        A("")
        A("分位：p50=%s ｜ min=%s ｜ max=%s ｜ 高于归档门 %s：%s/%s"
          % (_num(pot["p50"]), _num(pot["min"]), _num(pot["max"]),
             _num(pot.get("archive_threshold"), 2), pot["above_archive"], len(pot["rows"])))
        A("")
        A("> 这是 `engine.memory.potency`（指数衰减 ∪ ACT-R 双通道取大）在真实条目上的计算结果，"
          "输入真实、公式来自实现；**它不是对「potency 公式正确」的实测证据**。")
    A("")
    A("插件侧纯衰减弱对照（`index.js#potency`，tauDays）：")
    plugin_rows = [r for r in mem["plugin_decay"]["rows"] if r["potency"] is not None]
    if plugin_rows:
        A(_md_table(["id", "importance", "age(d)", "plugin potency"],
                    [[r["id"], _num(r["importance"], 2), _num(r["age_days"], 1),
                      _num(r["potency"])] for r in plugin_rows][:50]))
    else:
        A("**样本不足（0 条可算）。**")
    A("")
    A("### 4.3 冲突覆盖 / 取代 / stale（%s）" % LABEL_REAL)
    A("")
    A(_md_table(["指标", "值", "说明"], [
        ["非 active 条目", json.dumps(mem["non_active"], ensure_ascii=False), "superseded/stale/suppressed 等"],
        ["supersede/stale/conflict 类审计事件", mem["conflict_events"]["n"],
         json.dumps(mem["conflict_events"]["kinds"], ensure_ascii=False)],
        ["write 拒绝", mem["writes"]["rejected"],
         "memorywriter 校验拒绝（无 sourceQuote / importance 越界 / 同批重复）"],
    ]))
    A("")
    A("> 当前插件**没有 supersede/stale 审计事件通道**（engine 有 `apply_supersede`，插件侧未落审计）。"
      "所以「冲突覆盖类事件 0 次」只能说明**没有观测通道**，不能说明「没有冲突」。")
    A("")

    # ── 5. 影子 ──
    A("## 5. 影子模块（Goal / Relation 只记录不注入，%s）" % LABEL_REAL)
    A("")
    A(_md_table(["指标", "值", "说明"], [
        ["agent_state 事件", sh["agent_state_events"], "目标/关系变更留痕"],
        ["goalEvents 分布", json.dumps(sh["goal_events"], ensure_ascii=False), "new/progress/blocked/done…"],
        ["relationEvent 分布", json.dumps(sh["relation_events"], ensure_ascii=False), "positive/negative/commitment…"],
        ["trust 分布", json.dumps(sh["trust"], ensure_ascii=False), "关系信任等级"],
        ["relation modifier", "n=%s min=%s max=%s" % (sh["modifier"]["n"],
                                                      _num(sh["modifier"]["min"], 2),
                                                      _num(sh["modifier"]["max"], 2)),
         "只记录侧产出（是否注入由开关决定）"],
        ["当前 goal（state）", json.dumps(sh["state_goal"], ensure_ascii=False), "active 目标数 / 事件数"],
        ["当前 relation（state）", json.dumps(sh["state_relation"], ensure_ascii=False), "—"],
        ["影子开关（最新 apply 事件）", json.dumps(sh["shadow_flags"], ensure_ascii=False)
         if sh["shadow_observable"] else "**日志不可判定**",
         "来自 apply.shadow；旧版本 apply 事件没有该字段"],
        ["记忆 origin 分布", json.dumps(sh["memory_origin"], ensure_ascii=False), "goal/relation/tool…"],
    ]))
    A("")
    if not sh["shadow_observable"]:
        A("> ⚠️ 当前日志里没有带 `shadow` 字段的 apply 事件（v4.3 之前的插件版本），"
          "**无法从日志判断影子开关是开还是关**。要评估影子模式误报率，先让插件把开关写进 apply 事件并在跑数据时固定它。")
        A("")

    # ── 6. 标定建议 ──
    A("## 6. 标定建议：能否把 guess 升级为 measured")
    A("")
    A("> 机检规则来自 `tools/analyze_shadow.py#REQUIREMENTS`（与 docs/20 的表格同源）。"
      "**样本不足就保持 guess**；本工具不改 rules，只输出 `provenance-candidates.json` 候选。")
    A("")
    A(_md_table(["参数", "当前值", "provenance", "分布证据齐备", "可升级 measured", "结论"],
                [[p["param"], p["current_value"], p["current_provenance"],
                  "是" if p["distribution_ready"] else "否",
                  "是" if p["upgrade_eligible"] else "否", p["verdict"]] for p in cal["params"]]))
    A("")
    A("> 结果标签门槛（`outcome_labels`）只认**人工纠正通道**（`corrections.jsonl` / 人工标签事件）；"
      "插件**规则自动标签**（`quality_label`，见 §2.6）**不计入**该门槛，也不能把 guess 升级为 measured。")
    A("")
    A("### 6.1 逐参数证据明细")
    A("")
    for p in cal["params"]:
        A("#### %s" % p["title"])
        A("")
        A("当前证据（%s / %s）：" % (LABEL_REAL, LABEL_DERIVED))
        A("")
        A(_md_table(["门槛", "需要", "当前证据", "是否达标"],
                    [[g["label"], "≥%d" % g["need"], "%d 次" % g["have"],
                      "✅" if g["met"] else "❌"] for g in p["gates"]]))
        A("")
        if p["blocking"]:
            A("**结论：%s**" % p["verdict"])
            A("")
            A("缺口（按门槛）：" + "；".join("%s 差 %d" % (g["label"], g["need"] - g["have"])
                                            for g in p["blocking"]))
        else:
            A("**结论：%s**" % p["verdict"])
        A("")
    A("### 6.2 监测类声明门槛（不是参数升级，但也需要样本量）")
    A("")
    A(_md_table(["声明", "门槛", "当前证据", "是否达标"],
                [[c["claim"], c["need"], c["have"], "✅" if c["met"] else "❌"] for c in cal["claims"]]))
    A("")
    A("### 6.3 provenance-candidates.json")
    A("")
    if candidates_path:
        A("- 本次运行已写出：`%s`" % candidates_path)
    else:
        A("- 本次运行未单独落盘（`--no-candidates`）。日常运行默认在 `--out` 同目录生成 "
          "`provenance-candidates.json`，也可用 `--candidates-out <path>` 指定。")
    A("- 内容摘要（与候选 JSON 一致）：可升级 `%s`；分布证据齐备 `%s`；本次运行后 measured 参数数 = %d。"
      % (cal["upgradeable"] or "无", cal["distribution_ready"] or "无",
         cal["measured_count_after_run"]))
    A("- 当前 `rules/decay-params.yaml#control` 全部为 `provenance: guess`；本报告**不构成**升级依据。")
    A("- 候选 JSON 另含 `quality_labels` 辅助块（规则自动标签统计，`auxiliary_only=true`，"
      "**不参与**任何门槛/候选判定）。")
    A("")

    # ── 7. 缺口 ──
    A("## 7. 下一步缺什么数据（按优先级）")
    A("")
    A("1. **真实 contextWindow**：让插件从宿主 `request/context` 真正拿到窗口（当前 state 为 %s），"
      "否则 ratio 恒 0，相变相关门槛全部无法达成。" % ("null" if cw in (None, 0) else cw))
    A("2. **结果标签通道**：docs/17 的 corrections（用户说「不对 / 不该提 / 漏了」）尚未实现；"
      "没有「漂移/质量下降」标签，就**只能观测分布，不能标定阈值落点**。")
    A("3. **更多真实轮次**：三个参数都要求 ≥30 个有 ratio 的 turn；当前 %d 个（且多为 ratio=0）。"
      % s["phase_observed"])
    A("4. **趋势样本**：trendThreshold 需要 ≥28 个连续观测（≥30 个 turn）；当前 %d 个 trend 样本。"
      % tr["sample_n"])
    A("5. **越狱/红队样本**：STPA 要证明「100%% 熔断有效」需要 ≥20 次真实 UCA-01 尝试；当前 %d 次。"
      % (int(st["ucas"].get("UCA-01", 0)) + int(st["block_events"]["ucas"].get("UCA-01", 0))))
    A("6. **supersede / stale 审计通道**：插件侧要落事件，才谈得上 G1 误判率。")
    A("7. **影子开关留痕**：apply 事件写 `shadow`（v4.3 起已有）；跑数据时固定开关并记录，否则误报率不可归因。")
    A("8. **人工结果标签**：规则自动标签（§2.6 的 `quality_label`）只能当辅助信号；"
      "要把 `outcome_labels` 门槛填满，仍需 `docs/17` 的 corrections 人工纠正通道（当前未实现）。")
    A("")

    A("## 8. 复现")
    A("")
    A("```bash")
    A("python tools/analyze_shadow.py --dir \"%s\" --out <报告.md>" % res["dir"])
    A("python tools/analyze_shadow.py --out acceptance/shadow-baseline.md  # 默认读 $DSH_HOME")
    A("```")
    A("")
    A("> 数据时点：%s ｜ 分析时点：%s" % (res["data_asof"], res["analysis_at"]))
    A("")
    return "\n".join(L)


# ── 9. CLI ───────────────────────────────────────────────

def _default_data_dir() -> str:
    home = os.environ.get("DSH_HOME") or DEFAULT_HOME
    return os.path.join(home, "plugins", PLUGIN_NAME)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")     # Windows 控制台默认 GBK，报告里有中文
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="LINGSHU 真实标定 · 影子分析（只读，Markdown 报告 + provenance 候选）")
    ap.add_argument("--dir", default=None,
                    help="插件数据目录（默认 $DSH_HOME/plugins/dsh-lingshu）")
    ap.add_argument("--out", default=None, help="Markdown 报告输出路径；省略则打印到 stdout")
    ap.add_argument("--candidates-out", default=None,
                    help="provenance-candidates.json 输出路径（默认：--out 同目录）")
    ap.add_argument("--no-candidates", action="store_true",
                    help="不单独写 provenance-candidates.json（报告里仍含候选摘要）")
    ap.add_argument("--now", type=float, default=None,
                    help="分析时点（epoch 毫秒）。省略 = 取日志 data as-of（推荐，保证可复现）")
    ap.add_argument("--quiet", action="store_true", help="只输出结论摘要")
    args = ap.parse_args(argv)

    data_dir = os.path.abspath(args.dir or _default_data_dir())
    out = os.path.abspath(args.out) if args.out else None
    if args.no_candidates:
        candidates_path = None
    elif args.candidates_out:
        candidates_path = os.path.abspath(args.candidates_out)
    else:
        candidates_path = os.path.join(os.path.dirname(out), "provenance-candidates.json") \
            if out else None

    for target in (out, candidates_path):
        if target and _inside(target, data_dir):
            print("拒绝写入插件数据目录：%s（本工具只读）" % target, file=sys.stderr)
            return 2

    try:
        res = analyze(data_dir, now_ms=args.now)
    except Exception as exc:                          # 分析失败要给出可诊断信息，不静默
        print("分析失败：%s: %s" % (type(exc).__name__, exc), file=sys.stderr)
        return 2

    report = render_report(res, candidates_path)
    if out:
        try:
            os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
            io.open(out, "w", encoding="utf-8", newline="\n").write(report)
        except OSError as exc:
            print("写报告失败：%s" % exc, file=sys.stderr)
            return 2
    else:
        print(report)

    if candidates_path:
        cand = build_candidates(res)
        try:
            os.makedirs(os.path.dirname(candidates_path) or ".", exist_ok=True)
            io.open(candidates_path, "w", encoding="utf-8", newline="\n").write(
                json.dumps(cand, ensure_ascii=False, indent=2) + "\n")
        except OSError as exc:
            print("写候选文件失败：%s" % exc, file=sys.stderr)
            return 2

    if not args.quiet:
        s = res["sample"]
        cal = res["calibration"]
        print("LINGSHU 影子分析 ｜ 数据目录：%s" % data_dir)
        print("审计 %d 事件 ｜ turn %d（有 ratio 观测 %d）｜ memory %d 条 ｜ data as-of %s"
              % (s["audit_events"], s["turns"], s["phase_observed"],
                 s["memory_entries"], res["data_asof"]))
        print("规则自动标签 %d 条（辅助观察，不进入 outcome_labels 门槛）"
              % res["quality_labels"]["total"])
        print("可升级 measured：%s ｜ 分布证据齐备：%s ｜ measured 参数数：%d"
              % (cal["upgradeable"] or "无", cal["distribution_ready"] or "无",
                 cal["measured_count_after_run"]))
        if out:
            print("报告：%s" % out)
        if candidates_path:
            print("候选：%s" % candidates_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
