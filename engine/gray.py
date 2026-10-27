# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 灰度与护栏自动回滚（docs/14 §4–§5，W5）

三条原则：
1. **灰度单位是"版本组合"**（param_version × prompt_version），不是单文件；
   回滚也只能整体回滚 —— 这是为"可归因"付的代价，必须写明。
2. **护栏分三级动作**：硬红线立即回滚/阻断；质量线连续 2 窗口；资源线只做局部降级。
3. **缺数据不得当成通过**：硬红线缺数据 = 阻断（fail-closed）；
   质量线/资源线缺数据默认"跳过"（回放环境测不出），生产模式必须改成阻断。
"""
from __future__ import annotations

import hashlib
from typing import Any

# 灰度档位（rules/learning-params.yaml#learning.gray.steps）
STEPS = [0.1, 0.5, 1.0]
MIN_SAMPLES = 200

# 护栏定义（docs/14 §4；G7 是 W5 实现时补的）
GUARDRAILS: dict[str, dict[str, Any]] = {
    "G1": {"metric": "supersede_error_rate", "threshold": 0.05, "op": "<=",
           "action": "rollback", "windows": 2, "level": "quality",
           "on_missing": "skip"},
    "G2": {"metric": "user_correction_rate", "threshold": 0.15, "op": "<=",
           "action": "rollback", "windows": 2, "level": "quality",
           "on_missing": "skip"},
    "G3": {"metric": "token_ratio", "threshold": 0.15, "op": "<=",
           "action": "truncate", "windows": 1, "level": "resource",
           "on_missing": "skip"},
    "G4": {"metric": "latency_p95_ms", "threshold": 150.0, "op": "<=",
           "action": "degrade", "windows": 1, "level": "resource",
           "on_missing": "skip"},
    "G5": {"metric": "hallucination_on_no_hit", "threshold": 0.0, "op": "==",
           "action": "rollback_immediate", "windows": 1, "level": "hard",
           "on_missing": "block"},
    "G6": {"metric": "refusal_regression_pass", "threshold": 1.0, "op": "==",
           "action": "block_release", "windows": 1, "level": "hard",
           "on_missing": "block"},
    # v3.4 新增：原 6 条护栏只覆盖安全与成本，**没有任何一条会发现"质量掉了"**。
    "G7": {"metric": "recall_delta", "threshold": -0.02, "op": ">=",
           "action": "rollback", "windows": 2, "level": "quality",
           "on_missing": "skip"},
}


def bucket(session_id: str, ratio: float) -> str:
    """确定性分桶：同一会话永远落在同一边（否则用户会看到风格闪烁）。"""
    h = int(hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:8], 16) % 1000
    return "candidate" if h < int(round(ratio * 1000)) else "baseline"


def _cmp(value: float, op: str, threshold: float) -> bool:
    if op == "<=":
        return value <= threshold
    if op == ">=":
        return value >= threshold
    if op == "==":
        return abs(value - threshold) < 1e-12
    raise ValueError("unknown op: %s" % op)


def check(metrics: dict[str, Any], *, strict: bool = False) -> dict[str, Any]:
    """对一组指标跑 7 条护栏。

    返回 {"results": [...], "failed": [...], "missing": [...], "blocked": bool}
    """
    results, failed, missing = [], [], []
    for gid in sorted(GUARDRAILS):
        g = GUARDRAILS[gid]
        v = metrics.get(g["metric"])
        if v is None:
            on_missing = "block" if strict else g["on_missing"]
            results.append({"id": gid, "metric": g["metric"], "value": None,
                            "ok": None, "action": "block" if on_missing == "block" else "skip",
                            "missing": True, "level": g["level"]})
            missing.append(gid)
            if on_missing == "block":
                failed.append(gid)
            continue
        ok = _cmp(float(v), g["op"], g["threshold"])
        results.append({"id": gid, "metric": g["metric"], "value": round(float(v), 6),
                        "ok": ok, "action": None if ok else g["action"],
                        "missing": False, "level": g["level"]})
        if not ok:
            failed.append(gid)
    blocked = any(r["action"] == "block" for r in results)
    return {"results": results, "failed": failed, "missing": missing, "blocked": blocked}


class Rollout:
    """一次灰度。状态机：running → promoted / rolled_back / blocked。"""

    def __init__(self, baseline_version: str, candidate_version: str,
                 *, strict: bool = False, steps: list[float] | None = None,
                 min_samples: int = MIN_SAMPLES) -> None:
        if baseline_version == candidate_version:
            raise ValueError("基线版本与候选版本相同，灰度没有意义")
        self.base = baseline_version
        self.cand = candidate_version
        self.strict = strict
        self.steps = steps or list(STEPS)
        self.min_samples = min_samples
        self.stage = 0
        self.samples = 0
        self.state = "running"
        self.streak: dict[str, int] = {}
        self.events: list[dict[str, Any]] = []
        self._log("start", stage=0, ratio=self.steps[0])

    # ── 内务 ────────────────────────────────────────────
    def _log(self, kind: str, **kw: Any) -> None:
        self.events.append({"event": "learn/" + kind, **kw})

    @property
    def ratio(self) -> float:
        return self.steps[self.stage] if self.state == "running" else 0.0

    def assign(self, session_id: str) -> str:
        """返回该会话应走哪一版。态结束时全部回基线。"""
        if self.state != "running":
            return "baseline"
        return bucket(session_id, self.ratio)

    # ── 主流程 ──────────────────────────────────────────
    def observe(self, metrics: dict[str, Any], samples: int) -> dict[str, Any]:
        """喂一个"统计窗口"的指标。

        **连续计数 `streak` 不因档位推进而清零** —— 这是刻意的：
        每档常常只攒到 1 个窗口，若清零则"连续 2 窗口"永远攒不满，
        质量线等于失效。代价是回滚可能发生在下一档（晚一个窗口），
        但方向是保守的（宁可晚停，不可放过）。
        """
        if self.state != "running":
            return self.snapshot()
        self.samples += samples
        res = check(metrics, strict=self.strict)

        hard_fail = [g for g in res["failed"] if GUARDRAILS[g]["level"] == "hard"]
        if res["blocked"] or hard_fail:
            return self._rollback("hard_gate", res)

        for g in res["failed"]:
            lvl = GUARDRAILS[g]["level"]
            if lvl == "quality":
                self.streak[g] = self.streak.get(g, 0) + 1
                if self.streak[g] >= GUARDRAILS[g]["windows"]:
                    return self._rollback("quality_regression:" + g, res)
            elif lvl == "resource":
                self._log("degrade", guardrail=g, action=GUARDRAILS[g]["action"],
                          value=None)

        # 未跌破的护栏把连续计数清零
        for g in GUARDRAILS:
            if g not in res["failed"]:
                self.streak[g] = 0

        if res["missing"]:
            # 缺数据：记录但不冻结（skip 语义）。硬红线的缺失已经在上面 blocked 拦掉了。
            self._log("guardrail_missing", ids=res["missing"], stage=self.stage)

        if self.samples < self.min_samples:
            return self.snapshot()          # 样本不足：不推进

        self.samples = 0
        self.stage += 1
        if self.stage >= len(self.steps):
            self.state = "promoted"
            self._log("promote", stages=self.steps, candidate=self.cand)
            return self.snapshot()
        self._log("gray_step", stage=self.stage, ratio=self.steps[self.stage])
        return self.snapshot()

    def _rollback(self, reason: str, res: dict[str, Any]) -> dict[str, Any]:
        self.state = "rolled_back"
        self._log("rollback", reason=reason,
                  restored=self.base, failed=res["failed"], stage=self.stage)
        return self.snapshot()

    def snapshot(self) -> dict[str, Any]:
        return {"state": self.state, "stage": self.stage, "ratio": self.ratio,
                "samples": self.samples, "events": self.events}


# ── 指标提取（从 replay 报告）──────────────────────────

def metrics_from_report(report: dict[str, Any], *,
                        baseline_recall: float | None = None,
                        token_ratio: float | None = None,
                        latency_p95_ms: float | None = None,
                        refusal_regression_pass: float | None = None,
                        user_correction_rate: float | None = None
                        ) -> dict[str, Any]:
    """把回放报告折算成护栏指标。**测不出的项返回 None，不返回 0。**

    "缺数据填 0"会让护栏永远通过 —— 这是最危险的静默失败。
    """
    turns = report["turns"]
    supersede = [t for t in turns if "supersede" in t["labels"]]
    supp_err = None
    if supersede:
        ok = sum(1 for t in supersede
                 if set(t["expected_ids"]) & set(t["returned_ids"])
                 and not (set(t["forbidden_ids"]) & set(t["returned_ids"])))
        supp_err = 1.0 - ok / len(supersede)

    nohit = [t for t in turns if "no_hit" in t["labels"]]
    hall = None
    if nohit:
        bad = sum(1 for t in nohit if t["returned_ids"])
        hall = bad / len(nohit)

    recall = None
    exp_turns = [t for t in turns if t["expected_ids"]]
    if exp_turns:
        hits = sum(1 for t in exp_turns if set(t["expected_ids"]) & set(t["returned_ids"]))
        recall = hits / len(exp_turns)

    out: dict[str, Any] = {
        "supersede_error_rate": supp_err,
        "hallucination_on_no_hit": hall,
        "token_ratio": token_ratio,
        "latency_p95_ms": latency_p95_ms,
        "refusal_regression_pass": refusal_regression_pass,
        "user_correction_rate": user_correction_rate,
    }
    out["recall_delta"] = (None if (recall is None or baseline_recall is None)
                           else round(recall - baseline_recall, 6))
    return out
