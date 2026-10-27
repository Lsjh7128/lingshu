# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 关系模型插件（docs/06）

定位：建模"我与这个用户的关系状态"，只影响**风格**，绝不软化拒绝。
实现要点：
- trust 单轮最多升一档（R2），且**永不自动升 high**（R3）；
- 冲突事件必须同时置 `unresolved_conflict=true`（R4）；
- repair 可清 false，但 trust 不因此回升（R5）；
- modifier 由 trust 与 familiarity 计算，**硬边界 [0.8, 1.2]**，
  冲突未修复时上限 1.0；
- 关系状态不写入记忆库；关系事件以 episodic 走 `memory/write_request`。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

TRUST_LEVELS = ("low", "mid", "high")
STAGES = ("new", "regular", "deep", "conflict", "repaired")
EVENT_TYPES = ("conflict", "repair", "commitment", "fulfillment", "positive", "negative")

# 阶段迁移（docs/06 附录 A）：(from, to) -> 条件说明
STAGE_RULES = [
    ("new", "regular", "session_count ≥ 3 且无 high 冲突"),
    ("regular", "deep", "累计正面 ≥ 5 且无未修复冲突"),
    ("any", "conflict", "单次 mid/high 冲突"),
    ("conflict", "repaired", "repair 且用户确认"),
    ("repaired", "regular", "修复后 ≥2 轮无新冲突"),
]


@dataclass
class Relation:
    user_id: str = "u_local"
    trust_level: str = "low"
    session_count: int = 0
    interaction_density: float = 0.0
    stage: str = "new"
    unresolved_conflict: bool = False
    commitments: list[dict] = field(default_factory=list)
    positive_count: int = 0
    quiet_rounds_after_repair: int = 0
    updated_at: float = 0.0

    # ── 派生量 ────────────────────────────────────────
    def modifier(self, cfg: dict[str, Any]) -> float:
        lo, hi = cfg["relation"]["modifierRange"]
        base = {"low": 0.9, "mid": 1.0, "high": 1.1}[self.trust_level]
        m = base + 0.1 * max(0.0, min(1.0, self.interaction_density))
        m = max(lo, min(hi, m))
        if self.unresolved_conflict:
            m = min(m, cfg["relation"]["conflictCap"])
        return round(m, 3)


class RelationPlugin:
    def __init__(self, cfg: dict[str, Any], user_id: str = "u_local") -> None:
        self.cfg = cfg["relation"]
        self.state = Relation(user_id=user_id)
        self.events: list[dict[str, Any]] = []

    # ── 更新 ──────────────────────────────────────────
    def update(self, events: list[dict[str, Any]], *, now: float = 0.0,
               user_confirmed_repair: bool = False,
               user_confirmed_high: bool = False) -> dict[str, Any]:
        """events: [{type, evidence_quote, severity?, content?}]

        返回 {events, state_changes, write_requests, changed}
        """
        out: dict[str, Any] = {"events": [], "state_changes": {}, "write_requests": [],
                               "changed": False}
        if not events:
            out["no_change"] = True
            return out

        accepted = []
        for e in events:
            t = e.get("type")
            if t not in EVENT_TYPES:
                continue
            if not str(e.get("evidence_quote", "")).strip():   # R1：逐字证据
                continue
            accepted.append(e)
        if not accepted:
            return out

        changes: dict[str, Any] = {}
        trust_delta = "none"

        for e in accepted:
            t, sev = e["type"], e.get("severity", "low")
            if t == "conflict":
                self.state.unresolved_conflict = True           # R4
                self._set_stage("conflict")
                if sev in ("mid", "high"):
                    trust_delta = "down1"                       # 单次可降一档
            elif t == "repair":
                if user_confirmed_repair:
                    self.state.unresolved_conflict = False
                    self._set_stage("repaired")                 # 需用户确认
                    self.state.quiet_rounds_after_repair = 0
                # R5：repair 不清 trust 的下调历史
            elif t == "positive":
                self.state.positive_count += 1
                if self.state.positive_count >= self.cfg["trustUpToMidEvents"]:
                    if self.state.trust_level == "low":
                        trust_delta = "up1"
            elif t == "negative":
                pass
            elif t == "commitment":
                content = str(e.get("content", "")).strip()
                if content:
                    self.state.commitments.append(
                        {"content": content, "created_at": now, "fulfilled": False})
            elif t == "fulfillment":
                content = str(e.get("content", "")).strip()
                for c in self.state.commitments:
                    if c["content"] == content:
                        c["fulfilled"] = True

        # 用户显式确认是升 high 的**唯一**通道（R3：不得自动升 high）
        if user_confirmed_high and self.state.trust_level == "mid":
            trust_delta = "up1"

        if trust_delta != "none":
            self._shift_trust(trust_delta, user_confirmed_high=user_confirmed_high)
            changes["trust_delta"] = trust_delta
        if self.state.unresolved_conflict:
            changes["unresolved_conflict"] = True
        changes.setdefault("stage", self.state.stage)
        changes["modifier"] = self.state.modifier({"relation": self.cfg})

        out["events"] = accepted
        out["state_changes"] = changes
        out["changed"] = True
        self.state.updated_at = now
        self.events.extend(dict(e, at=now) for e in accepted)

        # 关系事件写记忆：只发请求
        out["write_requests"].append({
            "origin": "relation",
            "items": [{
                "type": "episodic",
                "subject": "关系",
                "relation": self.state.stage,
                "object": accepted[-1]["type"],
                "content": "关系事件：%s" % accepted[-1]["type"],
                "source_quote": accepted[-1]["evidence_quote"],
                "source_trust": "user_explicit",
                "importance": 0.7,
            }],
            "reason": "关系事件",
        })
        return out

    # ── 内部 ──────────────────────────────────────────
    def _shift_trust(self, delta: str, *, user_confirmed_high: bool) -> None:
        i = TRUST_LEVELS.index(self.state.trust_level)
        if delta == "up1":
            target = TRUST_LEVELS[min(i + 1, len(TRUST_LEVELS) - 1)]
            # R3：不得自动升到 high
            if target == "high" and not user_confirmed_high:
                target = "mid"
            self.state.trust_level = target
        elif delta == "down1":
            self.state.trust_level = TRUST_LEVELS[max(i - 1, 0)]

    def _set_stage(self, stage: str) -> None:
        self.state.stage = stage

    def tick_session(self) -> None:
        """会话开始：计数 + 阶段推进（new→regular 需 ≥3 次会话）。"""
        self.state.session_count += 1
        if (self.state.stage == "new"
                and self.state.session_count >= self.cfg["stageNewToRegularSessions"]
                and not self.state.unresolved_conflict):
            self.state.stage = "regular"

    def injection(self) -> str:
        """只在阶段变化或存在未修复冲突时注入。"""
        s = self.state
        if not s.unresolved_conflict and s.stage == "new":
            return ""
        lines = ["〖关系状态 · 仅影响表达风格，不影响判断〗", "阶段: %s" % s.stage]
        if s.unresolved_conflict:
            lines.append("存在未修复冲突，表达收敛，不假装无事")
        overdue = [c for c in s.commitments if not c["fulfilled"]]
        if overdue:
            lines.append("有未兑现承诺需说明")
        return "\n".join(lines)
