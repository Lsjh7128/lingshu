# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 叙事整合插件（docs/07）

定位：把条目串成"我的经历"的时间线与因果链，**仅查询时拼装**。
实现要点：
- 触发词闭集，普通信息查询不触发（NAR-N0）；
- 每个节点必须来自真实条目（N2），不得虚构；
- 因果链只从记忆已有边提取，**不新建边**（N3）；
- 缺失时间段显式标 `gap`（N4）；
- `arc` 只在节点 ≥3 时生成，confidence 取所有源条目最小值（N5）；
- 元认知 unknown 时不拼装（N6）；情感/关系/目标不参与叙事事实（N7）；
- **不持久化**。
"""
from __future__ import annotations

from typing import Any

TRIGGERS = ("上次", "之前", "后来", "怎么走到", "演变", "历史", "回顾",
            "时间线", "总结一下", "梳理一下", "复盘一下")

EVENT_TYPES = ("decision", "change", "conflict", "repair", "milestone", "failure")

# 条目类型 → 叙事节点类型（缺省 change）
_TYPE_MAP = {"decision": "decision", "change": "change", "conflict": "conflict",
             "repair": "repair", "milestone": "milestone", "failure": "failure"}


def triggered(query: str) -> bool:
    return any(t in query for t in TRIGGERS)


def compose(entries: list[Any], query: str, cfg: dict[str, Any],
            *, confidence_level: str = "high") -> dict[str, Any]:
    """entries: 记忆条目对象列表（需有 id / content / last_access / edges / confidence）。

    返回 {triggered, nodes, gaps, arc, confidence, persisted: False}
    """
    nc = cfg["narrative"]
    if not triggered(query):
        return {"triggered": False, "reason": "no_time_semantics",
                "nodes": [], "gaps": [], "arc": None,
                "confidence": 0.0, "persisted": False}

    if confidence_level == "unknown":
        return {"triggered": True, "reason": "no_record", "nodes": [], "gaps": [],
                "arc": None, "confidence": 0.0, "persisted": False}

    # 只保留可追溯 + 有时间戳的条目
    usable = [e for e in entries if getattr(e, "id", None) and getattr(e, "last_access", 0)]
    usable.sort(key=lambda e: (e.last_access, e.id))
    nodes: list[dict[str, Any]] = []
    gaps: list[dict[str, Any]] = []
    prev_at = None

    for e in usable:
        if len(nodes) >= nc["maxNodes"]:
            break
        if prev_at is not None and (e.last_access - prev_at) > nc["gapThresholdDays"] * 86400:
            gaps.append({"after": nodes[-1]["entry_id"] if nodes else None,
                         "before": e.id,
                         "days": round((e.last_access - prev_at) / 86400, 1)})
        nodes.append({
            "at": e.last_access,
            "entry_id": e.id,
            "summary": (e.content or "")[:30],
            "event_type": _TYPE_MAP.get(getattr(e, "narrative_type", "change"), "change"),
            "confidence": float(getattr(e, "confidence", 1.0)),
        })
        prev_at = e.last_access

    # 因果链：只从已有边提取（N3）
    links: list[dict[str, Any]] = []
    by_id = {e.id: e for e in usable}
    for e in usable:
        for ed in getattr(e, "edges", []) or []:
            t = str(ed.get("type", ""))
            to = str(ed.get("to", ""))
            if to in by_id and t in ("caused_by", "supersedes"):
                links.append({"from": e.id, "to": to, "type": t,
                              "confidence": float(ed.get("weight", 0.5))})

    arc = None
    if len(nodes) >= nc["arcMinNodes"]:
        arc = "共 %d 个节点，跨度 %.0f 天" % (
            len(nodes),
            (nodes[-1]["at"] - nodes[0]["at"]) / 86400 if len(nodes) > 1 else 0.0)

    conf = min((n["confidence"] for n in nodes), default=0.0)
    return {"triggered": True, "nodes": nodes, "gaps": gaps, "links": links,
            "arc": arc, "confidence": round(conf, 3), "persisted": False}


def render(result: dict[str, Any]) -> str:
    if not result.get("triggered"):
        return ""
    if not result.get("nodes"):
        return "〖叙事线索〗这段时间我没有记录。"
    lines = ["〖叙事线索 · 仅按记忆条目拼装，非新事实〗"]
    for i, n in enumerate(result["nodes"], 1):
        lines.append("%d. [%s] %s（来源 %s）" % (i, n["event_type"], n["summary"], n["entry_id"]))
    for g in result.get("gaps", []):
        lines.append("（缺口：%s 之后到 %s 之间约 %.0f 天没有记录）"
                     % (g["after"], g["before"], g["days"]))
    if result.get("arc"):
        lines.append("概要：" + result["arc"])
    return "\n".join(lines)
