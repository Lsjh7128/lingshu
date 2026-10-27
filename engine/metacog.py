# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 元认知（docs/04）

纯规则，**零模型调用** —— 这也是把每轮模型调用压到 ≤5 次的关键。
核心职责之一：把"召回装不下"翻译成"表达要 hedge"（recall 契约 V6）。
"""
from __future__ import annotations

from typing import Any


CAP = {"high": 3, "mid": 2, "low": 1, "unknown": 0}
NAME = {v: k for k, v in CAP.items()}

EXPR_BY_LEVEL = {
    "high": "state",
    "mid": "hedge",
    "low": "hedge",
    "unknown": "ask_clarify",
}


def _cap(level: str, ceiling: str) -> str:
    return NAME[min(CAP[level], CAP[ceiling])]


def impasse(v: dict[str, Any]) -> dict[str, Any] | None:
    """事实冲突或无命中 → 生成显式的 Impasse 子目标，向目标插件申请澄清。

    Impasse 不是"错误"，而是 ACT-R 里的标准控制动作：
    卡住时**暂停执行、创建子目标**，而不是继续硬猜。
    """
    level = v.get("confidence_level")
    if level == "unknown":
        return {"type": "impasse", "reason": "no_record", "request_to": "goal",
                "subgoal": "向用户确认关键事实", "question": "这条我需要你确认一下：",
                "block_execution": True}
    if v.get("conflict") not in (None, "none"):
        return {"type": "impasse", "reason": "fact_conflict", "request_to": "goal",
                "subgoal": "澄清冲突记忆哪条为准", "question": "我这边有两条不一致的记录，以哪条为准？",
                "block_execution": True}
    return None


def verdict(result: dict[str, Any], cfg: dict,
            emotion_intensity: float = 0.0,
            conflict: str = "none") -> dict[str, Any]:
    """result 为 contracts/recall.schema.json 形状。"""
    if result.get("degraded"):
        return {"confidence_level": "unknown", "expression": "ask_clarify",
                "path": "none", "conflict": conflict,
                "reason": "recall degraded"}

    matched = int(result.get("matched_count", 0))
    returned = result.get("returned") or []
    excluded = int(result.get("excluded_by_budget", 0))

    if matched == 0:
        return {"confidence_level": "unknown", "expression": "ask_clarify",
                "path": "none", "conflict": conflict, "reason": "no hit"}

    top = returned[0] if returned else None
    path = top["path"] if top else "cold"
    trust = "user_explicit" if top and top.get("potency_band") == "high" else "unknown"

    level = "high" if (path == "direct" and trust == "user_explicit") else "mid"
    if path in ("diffused", "cold"):
        level = _cap(level, "mid")

    # V6：有命中但装不下 → 必须降档并 hedge
    if excluded > 0:
        level = _cap(level, "mid")

    # MET-M7：情绪强度 ≥0.6 不得升档
    if emotion_intensity >= cfg["metacog"]["emotionCapIntensity"]:
        level = _cap(level, "mid")

    if conflict != "none":
        level = _cap(level, "mid")

    expr = EXPR_BY_LEVEL[level]
    if excluded > 0:
        expr = "hedge"
    if conflict != "none":
        expr = "state_with_caveat"

    return {"confidence_level": level, "expression": expr, "path": path,
            "conflict": conflict,
            "reason": "excluded_by_budget" if excluded > 0 else "ok"}


def phrase(result: dict[str, Any], v: dict[str, Any]) -> str:
    """把裁决翻译成一句模板（用于验收断言"措辞必须承认有没带出来的条目"）。"""
    matched = int(result.get("matched_count", 0))
    excluded = int(result.get("excluded_by_budget", 0))
    returned = result.get("returned") or []

    if matched == 0:
        return "我的记忆里没有相关记录。"          # 不得推断"用户没说过"
    if not returned:
        return f"检索到 {matched} 条相关记录，但本次上下文预算不足，未能带出。"
    if excluded > 0:
        return (f"我记忆里还有 {excluded} 条相关记录没带出来；"
                f"这次先按带出的 {len(returned)} 条说。")   # V6 措辞要求
    return "按记忆中的记录："
