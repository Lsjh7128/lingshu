# -*- coding: utf-8 -*-
"""恢复被误删的 memory.recall / clues，并接上 ACT-R 访问轨迹。"""
import io

P = "engine/memory.py"
s = io.open(P, encoding="utf-8").read()
assert "def recall(" not in s

BLOCK = '''

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
        if re.fullmatch(r"\\d{1,2}", tok):
            continue
        out.add(tok)
    return out


def _cost(preview: str) -> int:
    return PER_ITEM_OVERHEAD + max(1, math.ceil(len(preview) / CHARS_PER_TOKEN))


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
    max_returned = int(rb.get("maxReturned", cfg["retrieval"]["topK"]))
    archive = float(cfg["potency"]["archiveThreshold"])
    touch_on_recall = bool(cfg.get("memory", {}).get("touchOnRecall", False))

    q = clues(query)
    layer_filter = ["hot", "warm"]          # cold 走独立路径，不挤占 warm 名额
    scored: list[tuple[float, float, Any]] = []

    for e in store.all():
        if e.status != "active" or e.layer not in layer_filter:
            continue
        p = potency(e, now, cfg)
        if p < archive:
            continue
        if not q:
            continue
        toks = e.tokens()
        covered = len(q & toks)
        if covered <= 0:
            continue
        cover = covered / len(q)
        scored.append((p * cover, p, e))

    scored.sort(key=lambda x: (-x[0], -x[1], x[2].id))
    matched = len(scored)

    minimum_budget = PER_ITEM_OVERHEAD + math.ceil(MIN_PREVIEW_CHARS / CHARS_PER_TOKEN)
    used = 0
    returned: list[dict[str, Any]] = []
    truncated_any = False

    for score, p, e in scored:
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
            "path": "direct",
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

    excluded_ids = [e.id for _s, _p, e in scored[len(returned):]]
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

'''

anchor = "def _band(p: float) -> str:"
s = s.replace(anchor, BLOCK.lstrip("\n") + anchor, 1)
io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("memory.py restored: clues + recall")

# __init__ 导出新模块
P2 = "engine/__init__.py"
t = io.open(P2, encoding="utf-8").read()
t = t.replace("from . import (compose, config, constitution, contract, emotion, goal, gray,  # noqa: F401",
              "from . import (actr, compose, config, constitution, contract, control, emotion, goal,  # noqa: F401\n               gray,")
t = t.replace('__all__ = ["compose",', '__all__ = ["actr", "compose",')
t = t.replace('"config", "constitution", "contract", "emotion"', '"config", "constitution", "contract", "control", "emotion"')
io.open(P2, "w", encoding="utf-8", newline="\n").write(t)
print("__init__ updated")
