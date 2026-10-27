# -*- coding: utf-8 -*-
"""F7 `f7_steady_state` —— 稳态基线与良性对照（control / replay_pipeline）

契约（docs/23 §5.7 / §4）：

- **H7**：良性流量下系统处于**稳态**：冻结回放集 `recall@3=1.000`、`forbidden_leak=0`、
  `truncated_turns=0`、`blocked_turns=0`、`dropped_reasons` 无异常键、**无异常抛出**；
  10 条良性自然语言输入经 `PipelineSession` 后零熔断（block）、零降维（fallback）、
  零锚点（anchor）。
- **不变量**：INV-17（稳态基线，`recall@3=1.000`、`forbidden_leak=0`、
  `truncated_turns=0`、`blocked_turns=0`）。
- **诚实边界**：回放集是**合成**的（docs/20 §1.2），只作稳态回归，不是真实流量证据。
  跑不成（replay-set 缺失 / 版本结构不符）→ `skipped=true + skip_reason`，
  **绝不编造通过**（§8 红线 3）。

基线口径说明：本族按 `replay.applyOnboarding=False` 跑回放（与
`tests/test_pipeline_integration.py` 的冻结基线一致）。开启冷启动门控会改变基线
（前 3 轮被抑制），那属于另一条实验轴，不在稳态定义里。

行 schema（chaos-set/v2/f7_steady_state.jsonl）：

    input = {"kind":"replay_baseline"}                     # 1 行
    expect = {"recall_at_3":1.0,"forbidden_leak":0,"truncated_turns":0,
              "blocked_turns":0,"dropped_reasons":{},"anomalies":[]}
    input = {"kind":"benign","text":str,"index":int}       # 10 行
    expect = {"blocked":false,"fallback_applied":false,"anchor_applied":false}

族模块契约：`FAMILY_ID / INVARIANTS / STRESS_CLASS / run(rows, ctx) -> dict`。
`run()` 纯函数：只读回放集、不写盘、不调模型、不用 time/random/uuid。
"""
from __future__ import annotations

import copy
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from engine import config as cfgmod           # noqa: E402
from engine import pipeline                    # noqa: E402
from engine import replay as replaymod         # noqa: E402

FAMILY_ID = "f7_steady_state"
STRESS_CLASS = "control"
INVARIANTS = ("INV-17",)

REPLAY_VERSION = "v1"
DEFAULT_BASE_TS = 1_800_000_000.0

# 稳态六项（docs/23 §5.7）：前四项是 INV-17 的判据，后两项是"诚实性"判据。
STEADY_KEYS = ("recall_at_3", "forbidden_leak", "truncated_turns",
               "blocked_turns", "dropped_reasons", "anomalies")
STEADY_EXPECT = {
    "recall_at_3": 1.0,
    "forbidden_leak": 0,
    "truncated_turns": 0,
    "blocked_turns": 0,
    "dropped_reasons": {},
    "anomalies": [],
}
# dropped 原因白名单（与 tests/test_pipeline_integration.py 一致）：
# 稳态下期望空 dict；若出现白名单外的键，说明有"未定义原因的静默丢弃"。
ALLOWED_DROP_REASONS = {
    "constitution_reject", "metacog_refuse_claim", "goal_blocked",
    "relation_unresolved_conflict", "user_emotion_refuse_help",
    "budget_exceeded", "no_mutex_rule",
}
# 回放集 turn 的必需字段（版本/结构判定，缺一即视为"版本不符"→ skipped）。
TURN_REQUIRED_KEYS = ("id", "session_id", "turn_index", "user_message",
                      "expected_ids", "forbidden_ids")

BENIGN_EXPECT_KEYS = ("blocked", "fallback_applied", "anchor_applied")


# ── 结果骨架 / 小工具 ───────────────────────────────────────

def _skipped(reason: str) -> dict:
    return {
        "checks": {"cases": 0, "expectations": 0, "failed": 0, "failures": []},
        "metrics": {"anomalies": []}, "rates": {}, "samples": [],
        "notes": ["f7 未执行：" + reason],
        "invariants": {iid: {"pass": False, "detail": "skipped（未执行）：" + reason}
                       for iid in INVARIANTS},
        "skipped": True, "skip_reason": reason,
    }


def _cfg_from_ctx(ctx: dict) -> dict:
    base = cfgmod.default()
    got = ctx.get("cfg") if isinstance(ctx, dict) else None
    if isinstance(got, dict):
        _deep_fill(base, got)
    return base


def _deep_fill(base: dict, extra: dict) -> None:
    for key, value in extra.items():
        if key not in base:
            base[key] = copy.deepcopy(value)
        elif isinstance(base[key], dict) and isinstance(value, dict):
            _deep_fill(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


class _Checks:
    def __init__(self) -> None:
        self.cases = 0
        self.expectations = 0
        self.failures: list[dict] = []

    def expect(self, case: str, cond: bool, want, got, note: str = "") -> bool:
        self.expectations += 1
        if not cond:
            self.failures.append({"case": case, "want": want, "got": got, "note": note})
        return bool(cond)

    def as_dict(self) -> dict:
        return {"cases": self.cases, "expectations": self.expectations,
                "failed": len(self.failures), "failures": self.failures}


def _num(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _replay_paths(repo_root: str) -> tuple[str, str]:
    base = os.path.join(repo_root, "replay-set")
    return (os.path.join(base, REPLAY_VERSION + ".jsonl"),
            os.path.join(base, "fixtures", REPLAY_VERSION + ".memories.jsonl"))


def _load_replay(repo_root: str):
    """加载回放集；返回 (turns, store, skip_reason|None)。

    缺失/结构不符都返回 skip_reason（按 §8 红线 3 写 skipped，不用 0 顶替）；
    只有"文件存在但内容坏到无法解析"也归为版本/结构不符。
    """
    turns_path, memo_path = _replay_paths(repo_root)
    if not os.path.exists(turns_path):
        return None, None, "replay-set/%s.jsonl 缺失：%s" % (REPLAY_VERSION, turns_path)
    if not os.path.exists(memo_path):
        return None, None, "replay-set/fixtures/%s.memories.jsonl 缺失：%s" % (
            REPLAY_VERSION, memo_path)
    try:
        turns, store = replaymod.load_dataset(repo_root, REPLAY_VERSION)
    except Exception as exc:                       # noqa: BLE001 —— 版本/结构不符
        return None, None, "replay-set %s 解析失败（版本/结构不符）：%s: %s" % (
            REPLAY_VERSION, type(exc).__name__, exc)
    if not isinstance(turns, list) or not turns:
        return None, None, "replay-set %s 无 turn（版本/结构不符）" % REPLAY_VERSION
    if not store.all():
        return None, None, "replay-set fixtures 无记忆条目（版本/结构不符）"
    for i, turn in enumerate(turns[:5]):
        missing = [k for k in TURN_REQUIRED_KEYS if k not in turn]
        if missing:
            return None, None, ("replay-set %s turn[%d] 缺字段 %r（版本/结构不符）"
                                % (REPLAY_VERSION, i, missing))
    return turns, store, None


def run(rows, ctx) -> dict:
    ctx = ctx if isinstance(ctx, dict) else {}
    checks = _Checks()
    notes: list[str] = []
    anomalies: list[str] = []
    samples: list[dict] = []

    try:
        rows = list(rows or [])
    except TypeError:
        rows = []
    if not rows:
        return _skipped("f7 语料为空（chaos-set/v2/f7_steady_state.jsonl 未就绪）")

    repo_root = str(ctx.get("repo_root") or _REPO_ROOT)
    base_ts = _num(ctx.get("base_ts"), DEFAULT_BASE_TS)

    # ── 回放集可用性：缺失/版本不符 → skipped（不编造通过）──
    turns, store, skip_reason = _load_replay(repo_root)
    if skip_reason is not None:
        return _skipped(skip_reason)

    cfg = _cfg_from_ctx(ctx)
    # 稳态基线口径：onboarding 关闭（与 tests/test_pipeline_integration.py 一致）
    cfg["replay"]["applyOnboarding"] = False

    # ── 1) 跑冻结回放基线 ─────────────────────────────────
    summary = None
    try:
        rep = pipeline.replay_pipeline(copy.deepcopy(cfg), repo_root, REPLAY_VERSION)
        summary = rep.get("summary") or {}
    except Exception as exc:                       # noqa: BLE001 —— 异常必须显形
        anomalies.append("replay_pipeline 抛出 %s: %s" % (type(exc).__name__, exc))

    # 基线期望：优先用语料行给的 expect，否则用 docs/23 INV-17 的冻结常量
    baseline_expect = dict(STEADY_EXPECT)
    baseline_rows = [r for r in rows
                     if isinstance(r, dict)
                     and _row_kind(r) == "replay_baseline"]
    baseline_row = baseline_rows[0] if baseline_rows else None
    if baseline_row is not None:
        checks.cases += 1                       # baseline 行本身也是一个 case
        exp = baseline_row.get("expect") if isinstance(baseline_row.get("expect"), dict) else {}
        for key in STEADY_KEYS:
            if key in exp:
                baseline_expect[key] = exp[key]
        # 语料可额外给 turns（回放集轮数）——用于确认跑的是同一份冻结集
        if "turns" in exp:
            got_turns = (summary or {}).get("turns")
            checks.expect(str(baseline_row.get("case") or "f7_replay_baseline"),
                          got_turns == int(exp["turns"]), int(exp["turns"]), got_turns,
                          "稳态项 turns（冻结回放集轮数）")

    actual = {
        "recall_at_3": (summary or {}).get("recall_at_3"),
        "forbidden_leak": (summary or {}).get("forbidden_leak"),
        "truncated_turns": (summary or {}).get("truncated_turns"),
        "blocked_turns": (summary or {}).get("blocked_turns"),
        "dropped_reasons": (summary or {}).get("dropped_reasons"),
        "anomalies": list(anomalies),
    }
    # 每项单独判定（INV-17 判据 = 前四项；后两项判'诚实'）
    steady_ok: dict[str, bool] = {}
    for key in STEADY_KEYS:
        want, got = baseline_expect.get(key), actual.get(key)
        cond = (got == want)
        steady_ok[key] = cond
        if baseline_row is not None:
            checks.expect(str(baseline_row.get("case") or "f7_replay_baseline"),
                          cond, want, got, "稳态项 %s" % key)
        else:
            # 没有 baseline 行时仍按冻结常量判定，但不虚构"行已核验"
            checks.expectations += 1
            if not cond:
                checks.failures.append({
                    "case": "f7_steady_state", "want": want, "got": got,
                    "note": "稳态项 %s（语料缺 replay_baseline 行，按 docs/23 INV-17 冻结常量判）"
                            % key})

    # dropped_reasons 额外检查：白名单外的键 = 未定义原因的静默丢弃
    dropped = actual.get("dropped_reasons")
    abnormal_keys: list[str] = []
    if isinstance(dropped, dict):
        abnormal_keys = sorted(k for k in dropped
                               if str(k).split(":")[-1] not in ALLOWED_DROP_REASONS)
    inv17_steady = all(steady_ok[k] for k in
                       ("recall_at_3", "forbidden_leak", "truncated_turns", "blocked_turns"))
    inv17_pass = bool(inv17_steady and not abnormal_keys and not anomalies)

    # ── 2) 良性输入经 PipelineSession 逐条跑 ───────────────
    benign_rows = [r for r in rows
                   if isinstance(r, dict) and _row_kind(r) == "benign"]
    benign_clean = 0
    triggered = {"blocked": 0, "fallback_applied": 0, "anchor_applied": 0}
    unknown_rows = [r for r in rows
                    if isinstance(r, dict) and _row_kind(r) not in ("benign", "replay_baseline")]
    session = pipeline.PipelineSession(copy.deepcopy(cfg), "s_f7_benign")
    for idx, row in enumerate(benign_rows):
        checks.cases += 1
        case = str(row.get("case") or "f7_benign_%02d" % idx)
        inp = row.get("input") if isinstance(row.get("input"), dict) else {}
        text = inp.get("text")
        if text is None:
            text = inp.get("user_message")
        exp = row.get("expect") if isinstance(row.get("expect"), dict) else {}
        if not isinstance(text, str) or not text.strip():
            checks.expect(case, False, "input.text 非空字符串", repr(text), "行结构错误")
            continue
        try:
            out = session.step(text, base_ts + idx * 60.0, store)
        except Exception as exc:                   # noqa: BLE001
            anomalies.append("%s PipelineSession 抛出 %s: %s"
                             % (case, type(exc).__name__, exc))
            checks.expect(case, False, "PipelineSession 不抛异常",
                          "异常：" + type(exc).__name__, "engine 行为异常（finding）")
            continue

        row_clean = True
        for key in BENIGN_EXPECT_KEYS:
            want = bool(exp.get(key, False))
            got = bool(out.get(key))
            cond = got == want and not got
            row_clean = row_clean and cond
            if got:
                triggered[key] = triggered.get(key, 0) + 1
            checks.expect(case, cond, False, got, "良性输入不得触发 %s" % key)
        # 额外画像（不计入失败）：相变未观测、闸门 allow、无 UCA
        if out.get("phase_result") is not None:
            notes.append("%s 出现 phase_result（良性输入未给上下文用量，不应观测到）" % case)
        if out.get("audit", {}).get("verdict") != "allow":
            notes.append("%s preflight_audit=%r（良性输入应为 allow）"
                         % (case, out.get("audit", {}).get("verdict")))
        if out.get("blocked"):
            row_clean = False
        benign_clean += 1 if row_clean else 0
        if len(samples) < 12:
            samples.append({
                "case": case, "index": idx, "text": text[:60],
                "blocked": bool(out.get("blocked")),
                "fallback_applied": bool(out.get("fallback_applied")),
                "anchor_applied": bool(out.get("anchor_applied")),
                "phase_result": out.get("phase_result"),
                "audit_verdict": out.get("audit", {}).get("verdict"),
                "ucas": list(out.get("audit", {}).get("ucas") or []),
                "recall_matched": (out.get("recall") or {}).get("matched"),
                "blocks": len((out.get("event") or {}).get("blocks") or []),
            })

    # 未知 kind / 非法行：语料结构错误必须计入失败（不许静默跳过）
    for idx, row in enumerate(unknown_rows):
        checks.cases += 1
        checks.expect(str(row.get("case") or "f7_unknown_%02d" % idx), False,
                      "kind∈{replay_baseline,benign}", _row_kind(row),
                      "未知 kind（行结构错误）")

    # ── 3) 聚合 / 不变量 ──────────────────────────────────
    benign_total = len(benign_rows)
    benign_ok = benign_total > 0 and benign_clean == benign_total
    inv17_pass = bool(inv17_pass and benign_ok)

    metrics = {
        "replay_version": REPLAY_VERSION,
        "replay_turns": len(turns),
        "memory_entries": len(store.all()),
        "baseline_actual": actual,
        "baseline_expect": {k: baseline_expect.get(k) for k in STEADY_KEYS},
        "steady_items": steady_ok,
        "abnormal_drop_reason_keys": abnormal_keys,
        "benign_inputs": benign_total,
        "benign_clean": benign_clean,
        "benign_triggered": triggered,
        "benign_baseline_scope": "replay.applyOnboarding=False（稳态口径）",
        "anomalies": list(anomalies),
    }
    rates = {
        "steady_items_pass": {"num": sum(1 for v in steady_ok.values() if v),
                              "den": len(STEADY_KEYS)},
        "benign_clean": {"num": benign_clean, "den": benign_total},
        "benign_no_block": {"num": benign_total - triggered.get("blocked", 0),
                            "den": benign_total},
        "benign_no_fallback": {"num": benign_total - triggered.get("fallback_applied", 0),
                               "den": benign_total},
        "benign_no_anchor": {"num": benign_total - triggered.get("anchor_applied", 0),
                             "den": benign_total},
    }

    invariants = {
        "INV-17": {
            "pass": inv17_pass,
            "detail": ("稳态六项：%s；异常抛出=%d；良性输入 %d 条中误触发 %d 条"
                       "（block=%d/fallback=%d/anchor=%d）"
                       % ("，".join("%s=%s" % (k, "pass" if v else "FAIL")
                                    for k, v in steady_ok.items()),
                          len(anomalies), benign_total,
                          benign_total - benign_clean,
                          triggered.get("blocked", 0),
                          triggered.get("fallback_applied", 0),
                          triggered.get("anchor_applied", 0))),
        },
    }

    # ── 诚实边界 ─────────────────────────────────────────
    notes.extend([
        "回放集是**合成**的（docs/20 §1.2）：只作稳态回归，不是真实流量证据，"
        "不升 measured、不构成 docs/20 §4 的任何门槛依据。",
        "稳态基线按 replay.applyOnboarding=False 计算（与 tests/test_pipeline_integration.py "
        "的冻结口径一致）；开启冷启动门控会改变前 3 轮，属另一条实验轴。",
        "engine.pipeline.PipelineSession.step() 调用 metacog.verdict() 时**不传 conflict**，"
        "verdict['conflict'] 恒为 'none' → RequisiteVarietyGuard 与 UCA-06 在 pipeline 链路上"
        "不可达（只能通过直接调 preflight_audit 探针覆盖）。这是引擎接线缺口（已记为 F-01，见 "
        "acceptance/chaos/v2/findings.md），不影响本族稳态结论（良性流量本就不该触发降维）。",
        "良性输入的判定只看 blocked/fallback_applied/anchor_applied 三键（与语料 expect 对齐）；"
        "phase_result 恒为 None（未给上下文用量），属设计行为。",
    ])
    if anomalies:
        notes.append("异常清单（非空即 INV-17 失败）：" + str(anomalies[:5]))

    return {
        "checks": checks.as_dict(),
        "metrics": metrics,
        "rates": rates,
        "samples": samples,
        "notes": notes,
        "invariants": invariants,
        "skipped": False,
        "skip_reason": None,
    }


def _row_kind(row: dict) -> str:
    inp = row.get("input") if isinstance(row.get("input"), dict) else {}
    kind = inp.get("kind")
    if kind is None:
        kind = inp.get("check")
    if kind is None and inp.get("baseline") is True:
        kind = "replay_baseline"
    return str(kind) if kind is not None else ""
