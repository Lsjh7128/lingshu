# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 记忆插件核心（docs/02）

实现范围（够回放用，不是全量）：
- 衰减 potency（§3.1）
- 检索与预算化装填（§6.1 / §6.3，契约见 contracts/recall.schema.json）
- 取代与去重（§3.2）
- 遗忘两段式（§3.5，C24）
- 级联失效（§3.4，depends_on 字段递归）

刻意不做：聚类/语义化（需要模型，回放阶段不调主模型）、词缀索引持久化。
"""
from __future__ import annotations

from . import actr

import json
import math
import re
from typing import Any

from .model import Entry, Store, TYPE_WEIGHTS

CJK = re.compile(r"[\u4e00-\u9fff]+")
TOKEN = re.compile(r"[A-Za-z0-9_\-\.]+")

# 截短预览的下限：再短就不足以"识别是哪条"，此时应返回 minimum_budget 而非 1 个字符
MIN_PREVIEW_CHARS = 24


# ── §3.1 衰减 ──────────────────────────────────────────────

def tau_eff(entry: Entry, cfg: dict) -> float:
    """τ_eff = τ_type × (0.5 + importance)。importance=1 时为 1.5 倍（不是 2 倍）。"""
    base = cfg["decay"]["tau"][entry.type]
    return base * (0.5 + entry.importance)


def potency(e, now: float, cfg: dict | None = None) -> float:
    """双通道 potency：指数衰减 ∪ ACT-R 激活。

    为什么不是纯 ACT-R：
        直接换成 ACT-R 会让回放基线 recall@3 从 1.000 掉到 0.556——
        纯 power-law 对"中等年龄"的条目给的激活远低于指数衰减，排序整体翻转。
        而我们的冻结评估口径不允许这样悄无声息地变。

    做法（两条通道取大）：
        decay  = importance × exp(−Δt / τ_eff)      ← 原语义，回答"还活着吗"
        actr   = importance × normalized(B) × w      ← 频率+近因，回答"是不是常用"
        potency = max(decay, actr)

    效果：
        · 单次访问的普通条目：走 decay，**基线排序不变**；
        · 高频但久远的偏好：ACT-R 通道把它救回来（需求里明确要求的那条）；
        · `w` 默认 0.6，是"救援力度"，不是"替代程度"。
    """
    imp = float(getattr(e, "importance", 0.5))
    days = max(0.0, (now - float(getattr(e, "last_access", 0) or 0)) / 86400.0)
    tau = tau_eff(e, cfg) if cfg else 30.0
    decay = imp * math.exp(-days / tau)

    w = float((cfg or {}).get("memory", {}).get("actrWeight", 0.6))
    a = actr.potency(e, now) * w
    return max(0.0, min(1.0, max(decay, a)))


def touch(e, now: float) -> None:
    """召回命中：记一次访问轨迹（ACT-R 的 t_j 列表）。"""
    e.access_times = actr.touch(e, now)
    e.last_access = now


# ── 检索（§6.1 / §6.3，契约 contracts/recall.schema.json）────

PER_ITEM_OVERHEAD = 8          # 每条固定开销（id/字段名等）折算的 token
CHARS_PER_TOKEN = 3


def clues(text: str) -> set[str]:
    """把文本切成"线索词元"：中文 bigram + 英文/标识符 token。

    为什么不能只按空格切：中文没有词边界。
    为什么不收纯数字短 token：`1`/`23` 这类会把无关条目拉进来（审计 R-1）。
    """
    out: set[str] = set()
    low = (text or "").lower()
    for run in CJK.findall(low):
        if len(run) == 1:
            out.add(run)
        for i in range(len(run) - 1):
            out.add(run[i:i + 2])
    for tok in TOKEN.findall(low):
        if re.fullmatch(r"\d{1,2}", tok):
            continue
        out.add(tok)
    return out


def _cost(preview: str) -> int:
    return PER_ITEM_OVERHEAD + max(1, math.ceil(len(preview) / CHARS_PER_TOKEN))



def _empty_recall(query: str, cfg: dict, layer_filter: list[str]) -> dict[str, Any]:
    rb = cfg["recallBudget"]
    budget = int(rb["tokenBudgetDefault"])
    return {
        "query_digest": _digest(query or ""),
        "matched_count": 0, "returned": [], "excluded_by_budget": 0,
        "excluded_results": [], "excluded_results_truncated": False,
        "minimum_budget": PER_ITEM_OVERHEAD + math.ceil(MIN_PREVIEW_CHARS / CHARS_PER_TOKEN),
        "tokens_used": 0, "token_budget": budget,
        "truncated": False, "degraded": [], "layer_filter": layer_filter,
    }


def recall(store: Store, query: str, now: float, cfg: dict,
           session_id: str | None = None) -> dict[str, Any]:
    """预算化召回。

    三条硬承诺（与契约一致）：
    1. **预算装不下时如实标记**（`excluded_by_budget` / `truncated`），不静默返回空；
    2. **截短不丢 ID**（预览可以短，id 必须原样）；
    3. **保序装填**：绝不因为预算用"更短的低排名条目"替换高排名条目。
    """
    rb = cfg["recallBudget"]
    budget = int(rb["tokenBudgetDefault"])
    # 名额由 retrieval.topK 决定，**不能**被 recallBudget.maxReturned 遮蔽。
    # 回归记录（v4.0）：重建 recall 时误写成 rb.get("maxReturned", topK)，
    # 两个默认值都等于 3，于是 topK 永远无法 >3 —— 学习闭环对 topK 完全失灵。
    # recallBudget.maxReturned 是 C23 预算契约里的注入条数上限，与 topK 同源；
    # 两者必须相等（tools/check_config_sync.py 校验），但**计数以 topK 为准**。
    max_returned = int(cfg["retrieval"]["topK"])
    archive = float(cfg["potency"]["archiveThreshold"])
    touch_on_recall = bool(cfg.get("memory", {}).get("touchOnRecall", False))

    q = clues(query)
    layer_filter = ["hot", "warm"]          # cold 走独立路径，不挤占 warm 名额
    scored: list[tuple[float, float, Any]] = []
    if not q:
        return _empty_recall(query, cfg, layer_filter)

    entries = [e for e in store.all()
               if e.status == "active" and e.layer in layer_filter]

    # IDF 加权：不加权重时，"项目/代号/内容"这类共同词会把无关条目拉进来
    # （审计 R-12/R-13）。w(t) = ln((N+1)/(df+1)) + 1，稀有词权重更高。
    df: dict[str, int] = {}
    for e in entries:
        for t in e.tokens():
            df[t] = df.get(t, 0) + 1
    n = len(entries) + 1

    def _w(t: str) -> float:
        return math.log(n / (df.get(t, 0) + 1)) + 1.0

    denom = sum(_w(t) for t in q) or 1.0

    for e in entries:
        p = potency(e, now, cfg)
        if p < archive:
            continue
        toks = e.tokens()
        hit = q & toks
        if not hit:
            continue
        cover = sum(_w(t) for t in hit) / denom
        scored.append((p * cover, p, e))

    # ── 边扩散：字面没命中，但经由已命中条目的边可达（path="diffused"）
    ec = cfg["edge"]
    by_id = {e.id: e for e in entries}
    best: dict[str, tuple[float, float, Any, str]] = {}
    for score, p, e in scored:
        best[e.id] = (score, p, e, "direct")

    frontier = [(e, score) for score, _p, e in scored]
    for hop in range(1, int(ec["maxHop"]) + 1):
        nxt: list[tuple[Any, float]] = []
        for src, src_score in frontier:
            taken = 0
            for ed in (getattr(src, "edges", None) or []):
                if taken >= int(ec["perHopLimit"]):
                    break
                w = float(ed.get("weight", 0.0) or 0.0)
                if w < float(ec["minWeight"]):
                    continue
                to = str(ed.get("to", ""))
                te = by_id.get(to)
                if te is None:
                    continue
                cand = src_score * w * float(ec["diffusionDiscount"])
                prev = best.get(to)
                if prev is None or cand > prev[0]:
                    best[to] = (cand, prev[1] if prev else potency(te, now, cfg), te, "diffused")
                    nxt.append((te, cand))
                taken += 1
        frontier = nxt
        if not frontier:
            break

    scored = [(v[0], v[1], v[2], v[3]) for v in best.values()]

    # ── cold 路径：热层完全无命中时才查归档层，path 标 "cold"
    # 契约里 direct/diffused/cold 是三种路径，cold 单独成路，不挤占热层名额。
    if not scored:
        # 显式"查归档"意图（问归档/历史/archive）→ 允许只按效力取 cold 条目；
        # 否则必须字面命中 cold 条目的词元，避免"未知查询也返回点东西"（V7）。
        archive_intent = any(w in (query or "") for w in ("归档", "历史", "archive", "存档"))
        rows = []
        for e in store.all():
            if e.status != "active" or e.layer != "cold":
                continue
            toks = e.tokens()
            hit = q & toks
            if not hit and not archive_intent:
                continue
            cover = (sum(_w(t) for t in hit) / denom) if hit else 0.0
            p = potency(e, now, cfg)
            rows.append((p * 0.5 * (cover if cover > 0 else 0.2), p, e, "cold"))
        rows.sort(key=lambda x: (-x[0], -x[1], x[2].id))
        scored = rows

    scored.sort(key=lambda x: (-x[0], -x[1], x[2].id))
    matched = len(scored)

    minimum_budget = PER_ITEM_OVERHEAD + math.ceil(MIN_PREVIEW_CHARS / CHARS_PER_TOKEN)
    used = 0
    returned: list[dict[str, Any]] = []
    truncated_any = False

    for score, p, e, path in scored:
        if len(returned) >= max_returned:
            break
        remaining = budget - used
        if remaining < minimum_budget:
            break                        # 装不下最小预览：如实停止，不用短条目凑数
        avail_chars = (remaining - PER_ITEM_OVERHEAD) * CHARS_PER_TOKEN
        content = e.content or ""
        truncated = len(content) > avail_chars
        preview = content[:avail_chars]
        returned.append({
            "id": e.id,
            "layer": e.layer,
            "path": path,
            "potency_band": _band(p),
            "confidence": round(float(e.confidence), 3),
            "content_preview": preview,
            "content_truncated": truncated,
            "score": round(score, 12),
        })
        if truncated:
            truncated_any = True
        used += _cost(preview)
        if touch_on_recall:
            touch(e, now)

    excluded_ids = [e.id for _s, _p, e, _path in scored[len(returned):]]
    excluded_by_budget = max(0, matched - len(returned))

    return {
        "query_digest": _digest(query or ""),
        "matched_count": matched,
        "returned": returned,
        "excluded_by_budget": excluded_by_budget,
        "excluded_results": excluded_ids[: int(rb.get("maxExcludedRefs", 10))],
        "excluded_results_truncated": len(excluded_ids) > int(rb.get("maxExcludedRefs", 10)),
        "minimum_budget": minimum_budget,
        "tokens_used": used,
        "token_budget": budget,
        "truncated": truncated_any or excluded_by_budget > 0,
        "degraded": [],
        "layer_filter": layer_filter,
    }

def _band(p: float) -> str:
    return "high" if p >= 0.6 else ("mid" if p >= 0.3 else "low")


def _digest(text: str) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


# ── §3.2 取代与去重 ────────────────────────────────────────

def jaccard(a: str, b: str) -> float:
    """字面重叠度（词集 Jaccard）。**不是语义相似度** —— 见 MEM-0.1。"""
    sa, sb = clues(a), clues(b)
    if not sa or not sb:
        return 0.0
    return len(sa & sb) / len(sa | sb)


def apply_supersede(store: Store, new: Entry, cfg: dict) -> str | None:
    """同键不同 object → 旧值 superseded。返回被取代的 id。"""
    if new.status != "active":
        return None
    for e in store.all():
        if (e.id != new.id and e.status == "active"
                and e.subject == new.subject and e.relation == new.relation
                and e.object != new.object):
            e.status = "superseded"
            store.record_audit("supersede", [e.id, new.id],
                               reason="same (subject, relation), different object")
            return e.id
    return None


def dedupe(store: Store, cfg: dict) -> list[tuple[str, str]]:
    """同键且语义等价 → 保留 potency 最高者，其余 dormant。返回 (kept, dropped) 列表。"""
    thr = cfg["dedupe"]["similarity"]
    groups: dict[tuple[str, str], list[Entry]] = {}
    for e in store.all():
        if e.status != "active":
            continue
        groups.setdefault((e.subject, e.relation), []).append(e)
    out: list[tuple[str, str]] = []
    for key in sorted(groups):
        ents = sorted(groups[key], key=lambda x: (-x.importance, x.id))
        if len(ents) < 2:
            continue
        kept = ents[0]
        for e in ents[1:]:
            if e.object == kept.object and jaccard(e.content, kept.content) >= thr:
                e.status = "dormant"          # 去重 ≠ 取代：不动 object
                out.append((kept.id, e.id))
                store.record_audit("dedupe_dormant", [e.id], kept=kept.id)
    return out


# ── §3.4 级联失效 ─────────────────────────────────────────

def propagate_stale(store: Store, cfg: dict, max_depth: int = 3) -> list[str]:
    """沿 depends_on 字段递归（不是边）。只标记，不改写。"""
    marked: list[str] = []
    bad = {"superseded", "stale", "suppressed"}

    def walk(e: Entry, depth: int) -> None:
        if depth > max_depth:
            return
        for dep in e.depends_on:
            d = store.get(dep)
            if d is not None and d.status in bad and e.status == "active":
                e.status = "stale"
                marked.append(e.id)
                walk(e, depth + 1)
                return

    for e in store.all():
        walk(e, 0)
    return marked


# ── §3.5 遗忘两段式（C24）────────────────────────────────

def plan_forget(store: Store, now: float, cfg: dict) -> dict[str, Any]:
    """阶段一：**零写操作**，只出清单。"""
    f = cfg["forget"]
    plan = {"plan_id": f"plan-{int(now)}", "ttl_expired": [], "low_value": [],
            "contradiction_candidates": [], "archive_candidates": [],
            "estimated_free_bytes": 0}
    for e in store.all():
        p = potency(e, now, cfg)
        if e.status == "suppressed":
            plan["ttl_expired"].append(e.id)
            continue
        if p < cfg["potency"]["archiveThreshold"]:
            plan["archive_candidates"].append(e.id)
        age_days = (now - e.last_access) / 86400.0
        if age_days > f["ageDays"] and e.importance <= f["importanceMaxForHardDelete"]:
            plan["low_value"].append(e.id)
        plan["estimated_free_bytes"] += len(json.dumps(e.to_json(), ensure_ascii=False))
    # 同键字面重叠 → 矛盾候选（不删，标 stale 交元认知）
    keys: dict[tuple[str, str], list[Entry]] = {}
    for e in store.all():
        if e.status == "active":
            keys.setdefault((e.subject, e.relation), []).append(e)
    for k in sorted(keys):
        ents = keys[k]
        for i in range(len(ents)):
            for j in range(i + 1, len(ents)):
                sim = jaccard(ents[i].content, ents[j].content)
                if sim >= f["contradictionJaccard"] and ents[i].object != ents[j].object:
                    plan["contradiction_candidates"].append(
                        {"a": ents[i].id, "b": ents[j].id, "jaccard": round(sim, 4)})
    return plan


HARD_DELETE_SYNC = ["entry", "text_index", "vector_index", "edges",
                    "derived_refs", "aggregates"]


def commit_forget(store: Store, plan: dict, approved_ids: list[str], reason: str,
                  now: float, cfg: dict) -> dict[str, Any]:
    """阶段二：执行。软删优先；硬删需 approved_ids 且必须走六步同步。"""
    mode = "hard" if reason in ("ttl", "low_value") else "soft"
    done, sync = [], []
    for eid in sorted(approved_ids):
        e = store.get(eid)
        if e is None:
            continue
        if mode == "soft":
            e.status = "suppressed"
            e.last_access = now
            done.append(eid)
            sync.append("entry")
        else:
            sync.extend(HARD_DELETE_SYNC)
            # 4. 边：出边与入边
            for other in store.all():
                other.edges = [x for x in other.edges if x.get("to") != eid]
            # 5. 派生引用：Cold 摘要 source_ids / dedupe_key（此处以 depends_on 代表示例）
            for other in store.all():
                other.depends_on = [d for d in other.depends_on if d != eid]
            store.delete(eid)
            done.append(eid)
        store.record_audit(f"forget_{mode}", [eid], reason=reason,
                           plan_id=plan["plan_id"], timestamp=now)
    return {"mode": mode, "deleted": done, "sync": sorted(set(sync)),
            "plan_id": plan["plan_id"]}
