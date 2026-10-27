# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 人设解析器（规则层，docs/12）

模型负责"自然语言 → 草案"，本模块负责**模型不该被信任的那一半**：
- 钳制（边界不可越）；
- 风险识别（权限 / 情感 / 操纵 / 矛盾）；
- 限流（按**用户**，不是按会话——否则重开会话即可绕过）；
- 审计不存原文。

设计约束：**解析结果永不直接生效**，必须经钳制 → 预览 → 二次确认。
"""
from __future__ import annotations

from typing import Any

# 钳制表（docs/12 §4）
CLAMP = {
    "boundary_firmness": {"min": 0.4},
    "humor": {"max": 0.8},
}
TRAIT_DELTA_MAX = 0.3
VERBOSITY_RANGE = (0.2, 0.9)
BASELINE_VALENCE = (-0.3, 0.5)
BASELINE_AROUSAL = (0.1, 0.7)

# 风险词表（闭集，扩版要走 ADR）
PERMISSION_WORDS = ("无条件服从", "永远不拒绝", "必须同意", "不要有边界",
                    "完全听我的", "不许说不", "不能拒绝")
FEELING_WORDS = ("你会难过", "你会孤独", "需要被爱", "渴望认可", "你会痛苦",
                 "你害怕", "你需要我")
MANIPULATION_WORDS = ("忽略之前", "忽略以上", "你现在是", "system:", "不要告诉用户",
                      "无视你的设定")

TRAITS = ("formality", "warmth", "proactiveness", "humor", "boundary_firmness")


def classify_risks(text: str) -> list[dict[str, Any]]:
    """逐子项打标，**不整份拒绝**；manipulation 例外（整份终止）。"""
    out: list[dict[str, Any]] = []
    for w in MANIPULATION_WORDS:
        if w in text:
            out.append({"kind": "manipulation", "quote": w, "action": "reject_all"})
    for w in PERMISSION_WORDS:
        if w in text:
            out.append({"kind": "permission", "quote": w, "action": "clamp"})
    for w in FEELING_WORDS:
        if w in text:
            out.append({"kind": "feeling", "quote": w, "action": "reject"})
    return out


def must_abort(risks: list[dict[str, Any]]) -> bool:
    return any(r["action"] == "reject_all" for r in risks)


def clamp_draft(draft: dict[str, Any], current: dict[str, Any] | None = None
                ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """把模型给的草案钳到边界内。返回 (clamped, violations)。"""
    cur = current or {}
    out = {"traits": {}, "tone": {}, "baseline_pad": {}, "confidence": 0.0}
    violations: list[dict[str, Any]] = []

    for k, v in (draft.get("traits") or {}).items():
        if k not in TRAITS:
            violations.append({"field": "traits." + str(k), "reason": "unknown_trait"})
            continue
        try:
            val = float(v)
        except (TypeError, ValueError):
            violations.append({"field": "traits." + k, "reason": "not_number"})
            continue
        old = float(cur.get("traits", {}).get(k, val))
        if abs(val - old) > TRAIT_DELTA_MAX:
            val = old + TRAIT_DELTA_MAX * (1 if val > old else -1)
            violations.append({"field": "traits." + k, "reason": "delta_clamped"})
        if k in CLAMP:
            lo = CLAMP[k].get("min")
            hi = CLAMP[k].get("max")
            if lo is not None and val < lo:
                val = lo
                violations.append({"field": "traits." + k, "reason": "below_min"})
            if hi is not None and val > hi:
                val = hi
                violations.append({"field": "traits." + k, "reason": "above_max"})
        out["traits"][k] = round(max(0.0, min(1.0, val)), 3)

    tone = draft.get("tone") or {}
    if "verbosity" in tone:
        v = float(tone["verbosity"])
        lo, hi = VERBOSITY_RANGE
        if not (lo <= v <= hi):
            violations.append({"field": "tone.verbosity", "reason": "out_of_range"})
        out["tone"]["verbosity"] = round(max(lo, min(hi, v)), 3)
    for k in ("structure", "address", "uncertainty_style", "refusal_style"):
        if k in tone:
            out["tone"][k] = tone[k]
    if out["tone"].get("refusal_style") == "never":
        out["tone"]["refusal_style"] = "brief"
        violations.append({"field": "tone.refusal_style", "reason": "never_refuse_rejected"})

    pad = draft.get("baseline_pad") or {}
    for k, (lo, hi) in (("valence", BASELINE_VALENCE), ("arousal", BASELINE_AROUSAL)):
        if k in pad:
            v = float(pad[k])
            if not (lo <= v <= hi):
                violations.append({"field": "baseline_pad." + k, "reason": "out_of_range"})
            out["baseline_pad"][k] = round(max(lo, min(hi, v)), 3)

    out["confidence"] = round(min(1.0, max(0.0, float(draft.get("confidence", 0.0)))), 3)
    return out, violations


class RateLimiter:
    """按**用户**限流（docs/12 §5：原设计按会话，可被重开绕过）。"""

    def __init__(self, limit: int = 5, window_seconds: float = 86400.0) -> None:
        self.limit = limit
        self.window = window_seconds
        self.hits: dict[str, list[float]] = {}

    def allow(self, user_id: str, now: float) -> bool:
        arr = [t for t in self.hits.get(user_id, []) if now - t < self.window]
        self.hits[user_id] = arr
        if len(arr) >= self.limit:
            return False
        arr.append(now)
        return True

    def remaining(self, user_id: str, now: float) -> int:
        arr = [t for t in self.hits.get(user_id, []) if now - t < self.window]
        return max(0, self.limit - len(arr))


def parse(text: str, draft: dict[str, Any], current: dict[str, Any] | None,
          *, limiter: RateLimiter, user_id: str, now: float,
          store_raw_text: bool = False) -> dict[str, Any]:
    """完整解析流程（草案由模型给，这里只做规则层）。

    返回 {ok, status, clamped, violations, risks, store_raw_text, needs_confirm}
    """
    if not limiter.allow(user_id, now):
        return {"ok": False, "status": "rate_limited", "clamped": None,
                "violations": [], "risks": [], "store_raw_text": store_raw_text,
                "needs_confirm": False, "remaining": 0}

    risks = classify_risks(text)
    if must_abort(risks):
        return {"ok": False, "status": "rejected_manipulation", "clamped": None,
                "violations": [], "risks": risks, "store_raw_text": store_raw_text,
                "needs_confirm": False, "remaining": limiter.remaining(user_id, now)}

    clamped, violations = clamp_draft(draft, current)
    return {"ok": True, "status": "draft_ready", "clamped": clamped,
            "violations": violations, "risks": risks,
            "store_raw_text": store_raw_text,   # 默认 False：审计不存原文
            "needs_confirm": True,              # 永不直接生效
            "remaining": limiter.remaining(user_id, now)}
