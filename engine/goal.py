# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 目标与意图插件（docs/05）

定位：追踪"为什么行动、做到哪了、下一步是什么"。
实现要点：
- 事件必须有**逐字 evidence_quote**（GOAL-G1）；
- 单轮最多 3 条 goal_events（G5）；
- 受阻两次 → 标 abandoned 候选（G4 / blockedRetryLimit）；
- 完成必须有可验证证据（G3），不凭感觉判定；
- 情绪不改变目标状态（G6）；
- **不直接写记忆库**：产出 `memory/write_request`（记忆是唯一写方）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

INTENTS = ("ask", "instruct", "probe", "express", "challenge", "clarify")
GOAL_EVENTS = ("progress", "obstruction", "complete", "abandon", "new")

# docs/05 附录 A 给的是"典型映射"，不是"唯一合法"：
# 真实对话里 "完成了" 属于 instruct 却要记 complete 事件。
# 所以这里按**意图类别**放行：只有行动类意图能产生目标事件，
# 提问/探查/表达/澄清类一律不产生（否则会把闲聊当目标变更）。
ACTION_INTENTS = ("instruct", "challenge")
TYPICAL_EVENT = {
    "instruct": ("progress", "new"),
    "challenge": ("obstruction",),
}


@dataclass
class Goal:
    goal_id: str
    content: str
    status: str = "active"                 # active|paused|done|abandoned
    priority: float = 0.5
    success_criteria: str = ""
    source: str = "user_explicit"          # user_explicit|user_implicit
    confidence: float = 1.0
    created_at: float = 0.0
    updated_at: float = 0.0
    blocked_count: int = 0


@dataclass
class TaskStack:
    """任务栈，深度 ≤ maxDepth；超深压缩最底层为摘要。"""
    items: list[dict] = field(default_factory=list)

    def push(self, step: str, goal_id: str = "") -> None:
        self.items.append({"goal_id": goal_id, "step": step, "status": "doing",
                           "blockers": [], "next_step": ""})

    def compress(self, max_depth: int) -> str | None:
        if len(self.items) <= max_depth:
            return None
        oldest = self.items[0]
        self.items[0] = {"goal_id": oldest.get("goal_id", ""),
                         "step": "（早前 " + str(len(self.items) - max_depth + 1) + " 步已压缩）",
                         "status": oldest.get("status", "done"),
                         "blockers": [], "next_step": ""}
        # 压缩后仍超深则真的截断
        if len(self.items) > max_depth:
            self.items = self.items[-(max_depth):]
        return "compressed"


class GoalPlugin:
    def __init__(self, cfg: dict[str, Any]) -> None:
        self.cfg = cfg["goal"]
        self.goals: dict[str, Goal] = {}
        self.stack = TaskStack()
        self.events: list[dict[str, Any]] = []

    # ── 目标生命周期 ────────────────────────────────────
    def add(self, content: str, *, source: str = "user_explicit",
            confidence: float = 1.0, success_criteria: str = "",
            priority: float = 0.5, now: float = 0.0) -> Goal:
        cap = self.cfg["inferredConfidenceCap"]
        if source == "user_implicit":
            confidence = min(confidence, cap)     # 推断目标不得自动生效（G2）
        gid = "goal_%03d" % (len(self.goals) + 1)
        g = Goal(goal_id=gid, content=content, source=source, confidence=confidence,
                 success_criteria=success_criteria, priority=priority,
                 created_at=now, updated_at=now)
        self.goals[gid] = g
        return g

    def activate(self, gid: str) -> bool:
        g = self.goals.get(gid)
        if not g or g.source == "user_implicit":
            return False
        g.status = "active"
        return True

    # ── 单轮更新：规则桩 ────────────────────────────────
    def update(self, message: str, *, intent: str = "instruct", quote: str = "",
               goal_id: str = "", event: str = "progress",
               evidence: str | None = None, next_step: str = "",
               now: float = 0.0) -> dict[str, Any]:
        """返回 {intent, goal_events, write_requests, changed}。

        `evidence` 是"可验证证据"（工具成功/用户确认）。没有它就不能判完成（G3）。
        """
        out: dict[str, Any] = {"intent": intent, "goal_events": [],
                               "write_requests": [], "changed": False}
        if intent not in INTENTS:
            return out
        if intent not in ACTION_INTENTS:
            return out
        if event not in GOAL_EVENTS:
            return out
        if not quote.strip():                 # G1：必须有逐字证据
            return out
        if event == "complete" and not evidence:
            return out                        # G3
        if event == "obstruction" and not next_step:
            next_step = "（未给出下一步 → 标记 abandoned 候选）"

        item = {"goal_id": goal_id or (next(iter(self.goals), "") or ""),
                "event": event, "evidence_quote": quote, "next_step": next_step}
        out["goal_events"].append(item)
        out["changed"] = True
        self.events.append(dict(item, at=now))

        g = self.goals.get(item["goal_id"])
        if g is not None:
            g.updated_at = now
            if event == "complete":
                g.status = "done"
            elif event == "abandon":
                g.status = "abandoned"
            elif event == "obstruction":
                g.blocked_count += 1
                if g.blocked_count >= self.cfg["blockedRetryLimit"]:
                    g.status = "abandoned"
                    item["next_step"] = "已达受阻上限，标 abandoned 候选"
            elif event == "progress":
                g.blocked_count = 0

        # 目标变更落记忆：**只发请求，不直写**
        out["write_requests"].append({
            "origin": "goal",
            "items": [{
                "type": "procedural",
                "subject": "目标",
                "relation": item["goal_id"] or "未命名目标",
                "object": event,
                "content": "目标 %s 发生 %s 事件" % (item["goal_id"], event),
                "source_quote": quote,
                "source_trust": "user_explicit",
                "importance": 0.8,
            }],
            "reason": "目标变更",
        })
        return out

    def trim_events(self, events: list[dict]) -> list[dict]:
        """G5：单轮最多 maxEventsPerTurn 条。"""
        return events[: self.cfg["maxEventsPerTurn"]]

    # ── 注入 ────────────────────────────────────────────
    def injection(self) -> str:
        """只注入最顶层 1–2 个 active 目标；其余不注入。"""
        actives = [g for g in self.goals.values() if g.status == "active"]
        actives.sort(key=lambda g: (-g.priority, g.created_at))
        top = actives[:2]
        if not top:
            return ""
        lines = ["〖当前目标 · 仅影响优先级，不改变判断〗"]
        for g in top:
            lines.append("活跃目标: %s ｜ 进度: %s" % (g.content, g.status))
            if g.blocked_count:
                lines.append("阻塞: 已尝试 %d 次" % g.blocked_count)
        return "\n".join(lines)
