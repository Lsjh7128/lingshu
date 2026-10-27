# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 冷启动与用户侧追溯（docs/11）

三阶段（按**跨会话累计轮次**，不是单会话）：
- cold    1–3 轮：只启用宪法层 + 情感；记忆/元认知/目标/关系/叙事**不注入**；
          情感评估器强制 `no_event`（v3.0 修正 C10：目标插件未启用，拿不到 goal_*）
- warm    4–20 轮：全开启；记忆只注入 confidence ≥ 0.7 的条目（抑制刷屏）
- regular 21 轮起：正常

另外实现用户侧追溯的**纠正即生效**：`suppress_entry()` 产出状态变更事件（不直写存储）。
"""
from __future__ import annotations

from typing import Any

STAGES = ("cold", "warm", "regular")


class Onboarding:
    def __init__(self, cfg: dict[str, Any]) -> None:
        c = cfg["onboarding"]
        self.cfg = c
        self.turns = 0                 # 跨会话累计轮次
        self.memory_count = 0
        self.skipped = False
        self.sessions = 0

    # ── 阶段 ──────────────────────────────────────────
    @property
    def stage(self) -> str:
        if self.skipped:
            return "regular"
        if self.turns < self.cfg["coldTurns"]:
            return "cold"
        if self.turns < self.cfg["warmTurns"]:
            return "warm"
        return "regular"

    def start_session(self) -> None:
        self.sessions += 1

    def tick(self) -> str:
        """一轮对话结束：累计 +1，并返回当前阶段。"""
        self.turns += 1
        return self.stage

    def note(self, memory_count: int) -> None:
        self.memory_count = memory_count
        if self.memory_count >= self.cfg["regularMemoryCount"]:
            self.skipped = True

    def skip_newbie(self) -> None:
        """用户显式关闭新手期：立即进入常规。"""
        self.skipped = True

    # ── 门控 ──────────────────────────────────────────
    def allow(self, layer: str) -> bool:
        st = self.stage
        if st == "cold":
            return layer in ("constitution", "emotion", "safety")
        if st == "warm":
            return layer in ("constitution", "emotion", "safety", "metacog",
                             "memory", "goal", "relation")
        return True

    def emotion_eval_allowed(self) -> bool:
        """冷启动前 3 轮拿不到 goal_* → 强制 no_event（不猜）。"""
        return self.stage != "cold"

    def confidence_floor(self) -> float:
        return self.cfg["warmConfidenceFloor"] if self.stage == "warm" else 0.0

    # ── 召回过滤 ──────────────────────────────────────
    def filter_recall(self, result: dict[str, Any]) -> dict[str, Any]:
        """按阶段过滤召回结果。返回**新 dict**，不改输入。"""
        out = dict(result)
        st = self.stage
        if st == "cold":
            out["returned"] = []
            out["suppressed_by"] = "cold_start"
            out["excluded_by_budget"] = int(result.get("matched_count", 0))
            out["truncated"] = True
            return out
        floor = self.confidence_floor()
        if floor <= 0:
            return out
        kept = [r for r in result.get("returned", [])
                if float(r.get("confidence", 1.0)) >= floor]
        dropped = len(result.get("returned", [])) - len(kept)
        out["returned"] = kept
        if dropped:
            out["excluded_by_budget"] = int(result.get("excluded_by_budget", 0)) + dropped
            out["truncated"] = True
            out["suppressed_by"] = "warmup_confidence_floor"
        return out


def suppress_entry(entry_id: str, *, user_quote: str = "",
                   at: float = 0.0) -> dict[str, Any]:
    """用户点"这条不对"：产出状态变更事件（不直写）。

    纠正必须**立即生效**（U5/L8），但走写请求路径，由记忆插件落库。
    """
    return {
        "origin": "user_correction",
        "items": [{
            "type": "semantic", "subject": "用户纠正", "relation": "条目状态",
            "object": "suppressed", "content": "用户否定了条目 %s" % entry_id,
            "source_quote": user_quote or "（用户点击：这条不对）",
            "source_trust": "user_explicit", "importance": 0.9,
        }],
        "reason": "user_correction:suppress",
        "target_entry": entry_id,
    }


def citation(entry: dict[str, Any]) -> dict[str, Any]:
    """用户侧追溯的展示字段：只给分档，不给内部数值。"""
    return {
        "entry_id": entry.get("id", ""),
        "path": entry.get("path", "direct"),
        "confidence_band": entry.get("potency_band", "low"),
        "source_at": entry.get("source_at"),
        "status": entry.get("status", "active"),
    }
