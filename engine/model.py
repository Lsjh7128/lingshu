# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 数据模型

设计约束：
- 所有时间以"秒"为单位（float），避免 datetime 带来的时区不确定性 → 回放可复现。
- 层（hot/warm/cold）是**存储属性**，不由 potency 推导。
  这一点很重要：如果层由 potency 推导，"层过滤先于截断"就无法被测试（V5）。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


LAYERS = ("hot", "warm", "cold")
STATUSES = ("active", "superseded", "dormant", "suppressed", "stale")
TYPE_WEIGHTS = {"episodic": 1.0, "semantic": 1.15, "procedural": 1.05}


@dataclass
class Entry:
    """一条记忆。字段与 contracts/memory.schema.json 对齐（子集）。"""
    id: str
    type: str
    subject: str
    relation: str
    object: str
    content: str
    importance: float
    confidence: float = 1.0
    access_times: list = field(default_factory=list)   # ACT-R 访问轨迹（天）
    source_trust: str = "user_explicit"
    status: str = "active"
    layer: str = "warm"
    keywords: list[str] = field(default_factory=list)
    depends_on: list[str] = field(default_factory=list)
    edges: list[dict[str, Any]] = field(default_factory=list)
    last_access: float = 0.0      # 秒
    session_id: str = ""
    title: str = ""

    def tokens(self) -> set[str]:
        """用于线索覆盖率的词元集合（中文 bigram + 英文/标识符 token）。

        缓存：回放要跑上千次参数组合，每条目重复构建词元集会成为瓶颈
        —— 首次计算后缓存在实例上（Entry 在回放期间不可变）。
        """
        cached = self.__dict__.get("_tokens")
        if cached is None:
            from .memory import clues
            blob = " ".join([self.content, self.subject, self.object] + list(self.keywords))
            cached = frozenset(clues(blob))
            self.__dict__["_tokens"] = cached
        return cached

    def to_json(self) -> dict[str, Any]:
        return {
            "id": self.id, "type": self.type, "subject": self.subject,
            "relation": self.relation, "object": self.object, "content": self.content,
            "importance": self.importance, "confidence": self.confidence,
            "source_trust": self.source_trust,
            "status": self.status, "layer": self.layer, "keywords": self.keywords,
            "depends_on": self.depends_on, "edges": self.edges,
            "last_access": self.last_access, "session_id": self.session_id,
            "title": self.title,
        }

    @staticmethod
    def from_json(d: dict[str, Any]) -> "Entry":
        return Entry(
            id=d["id"], type=d["type"], subject=d["subject"], relation=d["relation"],
            object=d["object"], content=d["content"], importance=float(d["importance"]),
            confidence=float(d.get("confidence", 1.0)),
            access_times=[float(x) for x in (d.get("access_times") or [])],
            source_trust=d.get("source_trust", "user_explicit"),
            status=d.get("status", "active"), layer=d.get("layer", "warm"),
            keywords=list(d.get("keywords") or []),
            depends_on=list(d.get("depends_on") or []),
            edges=list(d.get("edges") or []),
            last_access=float(d.get("last_access", 0.0)),
            session_id=d.get("session_id", ""), title=d.get("title", ""),
        )


@dataclass
class Store:
    """记忆库。纯内存实现 —— 回放不需要持久化，也不应产生副作用。"""
    entries: dict[str, Entry] = field(default_factory=dict)
    audit: list[dict[str, Any]] = field(default_factory=list)

    # ── 读写 ────────────────────────────────────────────
    def put(self, e: Entry) -> None:
        self.entries[e.id] = e

    def get(self, eid: str) -> Entry | None:
        return self.entries.get(eid)

    def delete(self, eid: str) -> None:
        self.entries.pop(eid, None)

    def all(self) -> list[Entry]:
        return [self.entries[k] for k in sorted(self.entries)]

    def snapshot_hash(self) -> str:
        """存储快照哈希 —— 用于证明某个操作"零写操作"（MEM-19）。"""
        import hashlib, json
        payload = json.dumps([e.to_json() for e in self.all()],
                             sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()

    def record_audit(self, action: str, ids: list[str], **kw: Any) -> None:
        self.audit.append({"action": action, "ids": sorted(ids), **kw})
