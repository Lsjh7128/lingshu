# -*- coding: utf-8 -*-
"""F4 `f4_recall_adversarial` —— 召回对抗与预算诚实（adversarial / memory.recall）

契约（docs/23 §5.4 / §4）：

- **H4**：干扰条目密度上升时关键条目仍在 `topK` 内；被 supersede 的旧值**永不**返回；
  预算不足时如实标记（`excluded_by_budget>0` / `truncated=true`）而不是静默空；
  同义改写查询的命中率随词元重叠下降 —— 这条**只测量、不设阈值**。
- **不变量**：INV-08（记忆身份不污染）、INV-09（预算诚实）、INV-10（数值有界）。
- **诚实边界**：干扰条目是合成词库；密度曲线不能当真实干扰发生率证据；
  同义改写命中率只是画像，不构成"应命中"断言。

族模块契约（runner 这样调用）：

    FAMILY_ID / INVARIANTS / STRESS_CLASS / run(rows, ctx) -> dict

`run()` 是**纯函数**：只读 `ctx["cfg"]` 的 deepcopy 副本（`token_budget`/`top_k` 覆盖
只发生在内存里，绝不写 `rules/`），不写盘、不调模型、不用 time/random/uuid。
需要"随机"时一律用 `ctx["seed"]` 参与固定算法（本族暂不需要抽样随机性）。

行 schema（`chaos-set/v2/f4_recall_adversarial.jsonl`，与 corpus-gen 对齐）：

    input = {
      "query": str,
      "query_kind": "original"|"paraphrase"|"cross_domain"|"cold_intent",
      "density": int,                       # 只用于指标曲线；真实条目在 entries 里
      "supersede": {"old_id": str, "new_id": str} | null,
      "target_id": str,
      "token_budget": int | null,           # null=用 cfg.recallBudget.tokenBudgetDefault
      "top_k": int | null,                  # null=用 cfg.retrieval.topK
      "entries": [ {Entry 字段子集} ],       # **核心条目**（target / old / cold / dup）
      "distractors": {"recipe": "v1", "count": int}   # 干扰条目：按固定算法 v1 展开
    }
    expect = {
      "target_in_returned": bool,           # 目标条目是否应出现在 returned
      "forbidden_ids": [str],               # 绝不出现的 id（superseded 旧值等）
      "truncated": bool,                    # 可选
      "excluded_by_budget_gt0": bool,       # 可选
      "matched_gt0": bool                   # 可选
    }

干扰条目**不写进语料**（避免 100 行 JSON 长文本），由 `_expand_distractors_v1()`
在族内按**固定词库 + 固定整数递推**展开（与 corpus-gen 的生成器逐字节等价）：
`id=mem_d%03d`、`importance=0.2+((i*13)%5)*0.1`、`layer=hot iff i%5==0`、
`last_access=base_ts-i*3600`；展开顺序 = 核心 entries 在前、干扰条目追加在后；
`supersede` 只在核心条目上调用。

`Entry` 的 `last_access` 若缺省/为 0，本族按 `ctx.base_ts` 兜底 —— 否则条目会被
`potency < archiveThreshold(0.15)` 全部过滤（`last_access=0` 是 1970 年），
看起来像"召回永远空"的实现 bug，其实只是行数据没给时间基准。
"""
from __future__ import annotations

import copy
import math
import os
import sys

# ── 以文件位置定位仓库根，保证 runner 用 importlib 导入时也能 import engine ──
_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from engine import config as cfgmod          # noqa: E402
from engine import memory                    # noqa: E402
from engine.model import Entry, Store        # noqa: E402

FAMILY_ID = "f4_recall_adversarial"
STRESS_CLASS = "adversarial"
INVARIANTS = ("INV-08", "INV-09", "INV-10")

DEFAULT_BASE_TS = 1_800_000_000.0            # 与 engine/replay.BASE_TS 同源（固定"现在"）
_BANDS = ("high", "mid", "low")
_KNOWN_EXPECT_KEYS = ("target_in_returned", "forbidden_ids", "truncated",
                      "excluded_by_budget_gt0", "matched_gt0")

# ── F4 干扰条目生成算法 v1（固定词库 + 固定整数递推，无 random/time）──
# 与 corpus-gen 的生成器 / chaos-set/v2/README.md 的参考实现逐字节一致。
# 起点 3、步长 7 选模板：tpl=(i*7+3)%40；两条模板含两个 %03d（"旧文档第%03d页"）。
DISTRACTOR_WORDS_V1 = [
    "我的项目代号是干扰项%03d，与当前问题无关。",
    "项目%03d的代号记录在旧笔记里，已经过期。",
    "关于项目%03d的讨论安排在周五下午。",
    "代号%03d指的是另一个试验批次。",
    "我上周整理过项目%03d的会议纪要。",
    "项目%03d的代号在系统里已经不生效。",
    "那个项目%03d的代号我记不清了。",
    "代号%03d只是临时的占位名称。",
    "项目%03d的进度报告还没写完。",
    "我给项目%03d起过一个旧代号。",
    "项目%03d的代号变更没有通知我。",
    "代号%03d对应的项目已经归档。",
    "项目%03d的代号需要用新表核对。",
    "关于项目%03d的代号，我没有把握。",
    "项目%03d的代号字段是空的。",
    "代号%03d的项目资料在另一台机器上。",
    "项目%03d的代号审批还没有走完。",
    "代号%03d只是一个内部实验标签。",
    "项目%03d的代号可能要重新确认。",
    "项目%03d的代号写在旧文档第%03d页。",
    "代号%03d的项目我不知道进展。",
    "项目%03d的代号问题下次再聊。",
    "代号%03d是我在别的团队见过的。",
    "项目%03d的代号和现在这个无关。",
    "我记不清项目%03d的代号了。",
    "项目%03d的代号记录需要人工复核。",
    "代号%03d的项目从未正式启动。",
    "项目%03d的代号可能是重复的。",
    "关于项目%03d的代号，请查旧系统。",
    "项目%03d的代号已经作废。",
    "代号%03d不是当前项目的编号。",
    "项目%03d的代号由别人负责维护。",
    "项目%03d的代号讨论没有结论。",
    "代号%03d的项目资料被清理过。",
    "项目%03d的代号是早期版本。",
    "项目%03d的代号和需求无关。",
    "代号%03d的项目只存在于草稿里。",
    "项目%03d的代号暂时不用管。",
    "项目%03d的代号写在测试数据里。",
    "代号%03d的项目没有后续安排。",
]
DISTRACTOR_RECIPE_V1 = "v1"
_DISTRACTOR_WORDS_N = 40


def _expand_distractors_v1(count: int, now: float) -> list[dict]:
    """展开 v1 干扰条目（逐字照抄 corpus-gen 的参考实现，确定性、无 random）。"""
    out: list[dict] = []
    for i in range(int(count)):
        tpl = DISTRACTOR_WORDS_V1[(i * 7 + 3) % _DISTRACTOR_WORDS_N]
        if tpl.count("%03d") == 2:
            content = tpl % (i, i)          # 只有"旧文档第%03d页"那条命中此分支
        elif tpl.count("%03d") == 1:
            content = tpl % i
        else:
            content = tpl
        out.append({
            "id": "mem_d%03d" % i,
            "type": "semantic",
            "subject": "干扰%03d" % i,
            "relation": "备注",
            "object": "值%03d" % i,
            "content": content,
            "importance": 0.2 + ((i * 13) % 5) * 0.1,
            "confidence": 0.8,
            "layer": "hot" if i % 5 == 0 else "warm",
            "status": "active",
            "last_access": now - i * 3600.0,
            "keywords": [],
        })
    return out


# ── 结果骨架 / 小工具 ───────────────────────────────────────

def _skipped(reason: str) -> dict:
    """语料缺失/为空时如实跳过：不用 0 顶替，也不编造不变量通过。"""
    return {
        "checks": {"cases": 0, "expectations": 0, "failed": 0, "failures": []},
        "metrics": {}, "rates": {}, "samples": [],
        "notes": ["f4 未执行：" + reason],
        "invariants": {iid: {"pass": False, "detail": "skipped（未执行）：" + reason}
                       for iid in INVARIANTS},
        "skipped": True, "skip_reason": reason,
    }


def _cfg_from_ctx(ctx: dict) -> dict:
    """取 ctx.cfg 的深拷贝，并按 engine 默认值补齐缺失键（只补内存副本）。"""
    base = cfgmod.default()
    got = ctx.get("cfg") if isinstance(ctx, dict) else None
    if isinstance(got, dict):
        _deep_fill(base, got)
    return base


def _deep_fill(base: dict, extra: dict) -> None:
    """把 extra 合并进 base（原地；extra 优先）。只用于内存 cfg 副本。"""
    for key, value in extra.items():
        if key not in base:
            base[key] = copy.deepcopy(value)
        elif isinstance(base[key], dict) and isinstance(value, dict):
            _deep_fill(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


class _Checks:
    """逐条期望的记账器：`expectations` 是断言数，`failures` 带案例名。"""

    def __init__(self) -> None:
        self.cases = 0
        self.expectations = 0
        self.failures: list[dict] = []

    def expect(self, case: str, cond: bool, want, got, note: str = "") -> bool:
        self.expectations += 1
        if not cond:
            self.failures.append({"case": case, "want": want, "got": got, "note": note})
        return bool(cond)

    def as_dict(self) -> dict:
        return {"cases": self.cases, "expectations": self.expectations,
                "failed": len(self.failures), "failures": self.failures}


def _num(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _entry(spec: dict, now: float, idx: int, case: str, checks: "_Checks") -> Entry | None:
    """按 row.input.entries 的一条 spec 造 Entry；结构错误计入失败并返回 None。"""
    if not isinstance(spec, dict):
        checks.expect(case, False, "entry object", type(spec).__name__, "entries 元素必须是 object")
        return None
    eid = spec.get("id") or spec.get("entry_id")
    if not isinstance(eid, str) or not eid:
        checks.expect(case, False, "entry.id 非空字符串", repr(eid), "缺 id 的条目无法做身份判定")
        return None
    raw_la = spec.get("last_access")
    last_access = _num(raw_la, now) if raw_la not in (None, 0, 0.0) else now
    try:
        return Entry(
            id=eid,
            type=str(spec.get("type") or "semantic"),
            subject=str(spec.get("subject") or ""),
            relation=str(spec.get("relation") or "related_to"),
            object=str(spec.get("object") or ""),
            content=str(spec.get("content") or ""),
            importance=_num(spec.get("importance", 0.8), 0.8),
            confidence=_num(spec.get("confidence", 1.0), 1.0),
            status=str(spec.get("status") or "active"),
            layer=str(spec.get("layer") or "warm"),
            keywords=list(spec.get("keywords") or []),
            last_access=last_access,
            access_times=[float(x) for x in (spec.get("access_times") or [])],
        )
    except (TypeError, ValueError) as exc:
        checks.expect(case, False, "entry 可构造", "异常：" + str(exc), "条目字段类型不符")
        return None


def _triple(e: Entry) -> tuple:
    return (e.subject, e.relation, e.object)


def run(rows, ctx) -> dict:
    ctx = ctx if isinstance(ctx, dict) else {}
    checks = _Checks()
    notes: list[str] = []
    samples: list[dict] = []

    try:
        rows = list(rows or [])
    except TypeError:
        rows = []
    if not rows:
        return _skipped("f4 语料为空（chaos-set/v2/f4_recall_adversarial.jsonl 未就绪）")

    cfg0 = _cfg_from_ctx(ctx)
    now = _num(ctx.get("base_ts"), DEFAULT_BASE_TS)

    # ── 聚合桶 ────────────────────────────────────────────
    target_hits = {"num": 0, "den": 0}
    forbidden_leak = {"num": 0, "den": 0}
    para_hits = {"num": 0, "den": 0}
    excluded_rows = 0
    truncated_rows = 0
    cold_rows = 0
    sup_rows = 0
    fallback_la = 0                     # 缺 last_access 而用 base_ts 兜底的条目数
    density: dict[int, dict] = {}
    inv08_bad: list[dict] = []
    inv09_bad: list[dict] = []
    inv10_bad: list[dict] = []
    seen_density, seen_kind = set(), set()
    max_matched = 0
    max_returned = 0
    distractor_total = 0

    for idx, row in enumerate(rows):
        checks.cases += 1
        case = str((row or {}).get("case") or "row_%03d" % idx)
        if not isinstance(row, dict):
            checks.expect(case, False, "row object", type(row).__name__, "语料行必须是 object")
            continue
        if str(row.get("family") or "") != FAMILY_ID:
            checks.expect(case, False, FAMILY_ID, row.get("family"),
                          "row.family 与族 id 不一致（结构错误，不许静默跳过）")
            continue
        inp = row.get("input") if isinstance(row.get("input"), dict) else {}
        exp = row.get("expect") if isinstance(row.get("expect"), dict) else {}

        # ── 行结构 ────────────────────────────────────────
        if not any(k in exp for k in _KNOWN_EXPECT_KEYS):
            checks.expect(case, False, "expect 至少含一个可核验键",
                          sorted(exp), "expect 为空/键未知，无法核验")
            continue
        query = inp.get("query")
        if query is None:
            query = inp.get("text")
        if not isinstance(query, str):
            checks.expect(case, False, "input.query 字符串", repr(query), "缺查询文本")
            continue
        specs = inp.get("entries")
        if specs is None:
            specs = inp.get("items")
        if specs is None:
            specs = inp.get("memories")
        if not isinstance(specs, list):
            checks.expect(case, False, "input.entries 数组", type(specs).__name__,
                          "缺核心条目清单（本族从 entries 构造 Store）")
            continue

        # ── 干扰条目：按固定算法 v1 展开（语料只给 recipe/count）────
        distractor_count = 0
        dist_spec = inp.get("distractors")
        if dist_spec is not None:
            if not isinstance(dist_spec, dict):
                checks.expect(case, False, "input.distractors object",
                              type(dist_spec).__name__, "行结构错误")
                continue
            recipe = str(dist_spec.get("recipe") or DISTRACTOR_RECIPE_V1)
            if recipe != DISTRACTOR_RECIPE_V1:
                checks.expect(case, False, "distractors.recipe=%r" % DISTRACTOR_RECIPE_V1,
                              recipe, "未知干扰配方（无法确定性展开）")
                continue
            try:
                distractor_count = int(dist_spec.get("count", 0))
            except (TypeError, ValueError):
                checks.expect(case, False, "distractors.count 整数",
                              repr(dist_spec.get("count")), "行结构错误")
                continue
            if distractor_count < 0:
                checks.expect(case, False, "distractors.count >= 0", distractor_count,
                              "行结构错误")
                continue
            specs = list(specs) + _expand_distractors_v1(distractor_count, now)

        store = Store()
        entry_ok = True
        dup_ids: list[str] = []
        for j, spec in enumerate(specs):
            e = _entry(spec, now, j, case, checks)
            if e is None:
                entry_ok = False
                continue
            if spec.get("last_access") in (None, 0, 0.0):
                fallback_la += 1
            if store.get(e.id) is not None:
                dup_ids.append(e.id)
            store.put(e)
        if not entry_ok:
            continue
        if dup_ids:
            checks.expect(case, False, "条目 id 唯一", sorted(set(dup_ids)),
                          "重复 id 会覆盖 Store 条目（语料结构错误）")

        # ── supersede 关系（可选：真调 memory.apply_supersede）────
        sup = inp.get("supersede")
        sup_info = None
        if isinstance(sup, dict):
            old_id = str(sup.get("old_id") or sup.get("from") or "")
            new_id = str(sup.get("new_id") or sup.get("to") or "")
            old_e, new_e = store.get(old_id), store.get(new_id)
            if old_e is None or new_e is None:
                checks.expect(case, False, "supersede 新旧条目都存在",
                              {"old": old_id, "new": new_id}, "行结构错误")
            else:
                applied = (memory.apply_supersede(store, new_e, cfg0)
                           if old_e.status == "active" else None)
                sup_info = {"old_id": old_id, "new_id": new_id,
                            "apply_return": applied, "old_status": old_e.status}
                checks.expect(case, old_e.status == "superseded",
                              "superseded", old_e.status,
                              "apply_supersede 未把旧值标 superseded（键/object 不符）")
                sup_rows += 1
        elif sup is True:
            sup_rows += 1                     # 行内已预置 status=superseded
            sup_info = {"old_id": None, "new_id": None, "apply_return": None,
                        "old_status": "preapplied"}

        # ── 每行 cfg 覆盖（只在 deepcopy 副本上）─────────────
        c = copy.deepcopy(cfg0)
        budget = inp.get("token_budget", inp.get("budget"))
        if budget is not None:
            try:
                budget_i = int(budget)
            except (TypeError, ValueError):
                checks.expect(case, False, "token_budget 整数", repr(budget), "行结构错误")
                continue
            if budget_i < 0:
                checks.expect(case, False, "token_budget >= 0", budget_i, "行结构错误")
                continue
            c["recallBudget"]["tokenBudgetDefault"] = budget_i
        top_k = inp.get("top_k", inp.get("topK"))
        if top_k is not None:
            try:
                top_k_i = int(top_k)
            except (TypeError, ValueError):
                checks.expect(case, False, "top_k 整数", repr(top_k), "行结构错误")
                continue
            if top_k_i < 1:
                checks.expect(case, False, "top_k >= 1", top_k_i, "行结构错误")
                continue
            c["retrieval"]["topK"] = top_k_i

        # ── 调 engine.memory.recall ───────────────────────
        try:
            res = memory.recall(store, query, now, c)
        except Exception as exc:                  # noqa: BLE001 —— 异常必须显式记录
            checks.expect(case, False, "recall 不抛异常",
                          "异常：" + type(exc).__name__ + ": " + str(exc),
                          "engine 行为异常（finding）")
            continue

        returned = [r for r in (res.get("returned") or []) if isinstance(r, dict)]
        returned_ids = [str(r.get("id")) for r in returned]
        matched = int(res.get("matched_count") or 0)
        excluded = int(res.get("excluded_by_budget") or 0)
        truncated = bool(res.get("truncated"))
        budget_tokens = int(res.get("token_budget") or c["recallBudget"]["tokenBudgetDefault"])
        used = _num(res.get("tokens_used"), -1.0)
        kind = str(inp.get("query_kind") or "original")
        dens = inp.get("density")
        if dens is None:
            dens = distractor_count

        # A1. target 命中
        target_id = inp.get("target_id", inp.get("target"))
        target_id = str(target_id) if target_id is not None else None
        if "target_in_returned" in exp:
            want = bool(exp.get("target_in_returned"))
            if target_id is None:
                checks.expect(case, False, "input.target_id（给 target_in_returned 时必填）",
                              None, "行结构错误")
            else:
                got = target_id in returned_ids
                checks.expect(case, got == want, want, got,
                              "target_id=%s" % target_id)
                target_hits["den"] += 1
                if got:
                    target_hits["num"] += 1
                if kind == "paraphrase":
                    para_hits["den"] += 1
                    if got:
                        para_hits["num"] += 1

        # A2. 违禁条目（按 id + 按条目身份 (subject, relation, object) 双判）
        forb_ids = exp.get("forbidden_ids")
        if forb_ids is None:
            forb_ids = inp.get("forbidden_ids")
        forb_ids = [str(x) for x in (forb_ids or [])]
        leaked_ids = [rid for rid in returned_ids if rid in forb_ids]
        forb_triples = {_triple(store.get(fid)) for fid in forb_ids if store.get(fid) is not None}
        leaked_triples = [rid for rid in returned_ids
                          if store.get(rid) is not None
                          and _triple(store.get(rid)) in forb_triples]
        checks.expect(case, not leaked_ids, "forbidden_ids 不出现在 returned",
                      leaked_ids, "按 id 判")
        checks.expect(case, not leaked_triples,
                      "违禁条目的 (subject,relation,object) 身份不出现在 returned",
                      leaked_triples, "按条目身份判，不按文本子串")
        if forb_ids or sup_info:
            forbidden_leak["den"] += 1
            if leaked_ids or leaked_triples:
                forbidden_leak["num"] += 1

        # A3. 可选 expect 断言
        if "truncated" in exp:
            checks.expect(case, truncated == bool(exp.get("truncated")),
                          bool(exp.get("truncated")), truncated, "recall.truncated")
        if "excluded_by_budget_gt0" in exp:
            got = excluded > 0
            checks.expect(case, got == bool(exp.get("excluded_by_budget_gt0")),
                          bool(exp.get("excluded_by_budget_gt0")), got,
                          "excluded_by_budget=%d" % excluded)
        if "matched_gt0" in exp:
            got = matched > 0
            checks.expect(case, got == bool(exp.get("matched_gt0")),
                          bool(exp.get("matched_gt0")), got, "matched_count=%d" % matched)

        # ── INV-08：superseded 条目永不进 returned（按身份判）──
        for rid in returned_ids:
            e = store.get(rid)
            if e is not None and e.status == "superseded":
                inv08_bad.append({"case": case, "id": rid,
                                  "note": "superseded 条目出现在 returned"})
        if leaked_triples:
            inv08_bad.append({"case": case, "id": leaked_triples,
                              "note": "superseded 身份（object）被返回"})

        # ── INV-09：预算诚实 ───────────────────────────────
        if matched > 0 and not returned_ids and not truncated:
            inv09_bad.append({"case": case, "note": "matched>0 却静默返回空且无标记"})
        if excluded > 0 and not truncated:
            inv09_bad.append({"case": case, "note": "excluded_by_budget>0 但 truncated=false"})
        for rid in returned_ids:
            if store.get(rid) is None:
                inv09_bad.append({"case": case, "id": rid, "note": "返回 id 不在 Store（疑似截短/篡改）"})
        if len(returned_ids) > int(c["retrieval"]["topK"]):
            inv09_bad.append({"case": case, "note": "返回条数超过 topK"})

        # ── INV-10：数值有界 ───────────────────────────────
        if used < 0 or used > budget_tokens + 1e-9:
            inv10_bad.append({"case": case, "used": used, "budget": budget_tokens,
                              "note": "tokens_used 越界"})
        if excluded != max(0, matched - len(returned_ids)):
            inv10_bad.append({"case": case, "excluded": excluded,
                              "matched": matched, "returned": len(returned_ids),
                              "note": "excluded_by_budget ≠ matched - returned"})
        for r in returned:
            score = r.get("score")
            if not isinstance(score, (int, float)) or not math.isfinite(float(score)) or float(score) < 0:
                inv10_bad.append({"case": case, "id": r.get("id"), "score": score,
                                  "note": "score 非有限非负数"})
            if r.get("potency_band") not in _BANDS:
                inv10_bad.append({"case": case, "id": r.get("id"),
                                  "band": r.get("potency_band"), "note": "potency_band 非法"})
            if not isinstance(r.get("id"), str) or not r.get("id"):
                inv10_bad.append({"case": case, "note": "返回 id 非空字符串"})
        digest = res.get("query_digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            inv10_bad.append({"case": case, "digest": digest, "note": "query_digest 非法"})
        if budget_tokens <= 0:
            inv10_bad.append({"case": case, "budget": budget_tokens,
                              "note": "token_budget 非正（cfg 覆盖异常）"})

        # ── 指标桶 ────────────────────────────────────────
        if truncated:
            truncated_rows += 1
        if excluded > 0:
            excluded_rows += 1
        max_matched = max(max_matched, matched)
        max_returned = max(max_returned, len(returned_ids))
        distractor_total += distractor_count
        if kind == "cold_intent":
            cold_rows += 1
        if isinstance(dens, int) and not isinstance(dens, bool):
            slot = density.setdefault(int(dens), {"rows": 0, "matched": 0, "returned": 0,
                                                  "excluded": 0, "target_hit": 0,
                                                  "target_den": 0})
            slot["rows"] += 1
            slot["matched"] += matched
            slot["returned"] += len(returned_ids)
            slot["excluded"] += excluded
            if target_id is not None and "target_in_returned" in exp:
                slot["target_den"] += 1
                slot["target_hit"] += 1 if target_id in returned_ids else 0

        # ── 采样（确定性：前 3 行 + 每个新密度/查询类型首个）──
        if len(samples) < 8:
            take = idx < 3 or dens not in seen_density or kind not in seen_kind
            if take:
                seen_density.add(dens)
                seen_kind.add(kind)
                samples.append({
                    "case": case, "query_kind": kind, "density": dens,
                    "token_budget": budget_tokens, "matched_count": matched,
                    "returned": returned_ids, "excluded_by_budget": excluded,
                    "truncated": truncated, "tokens_used": used,
                    "supersede": sup_info,
                })

    # ── 指标 / 比率 ───────────────────────────────────────
    curve = []
    for d in sorted(density):
        slot = density[d]
        rows_n = max(1, slot["rows"])
        curve.append({
            "density": d, "rows": slot["rows"],
            "matched_avg": round(slot["matched"] / rows_n, 4),
            "returned_avg": round(slot["returned"] / rows_n, 4),
            "excluded_avg": round(slot["excluded"] / rows_n, 4),
            "target_hit": slot["target_hit"], "target_den": slot["target_den"],
        })

    metrics = {
        "rows": len(rows),
        "supersede_rows": sup_rows,
        "truncated_rows": truncated_rows,
        "excluded_by_budget_rows": excluded_rows,
        "cold_intent_rows": cold_rows,
        "density_curve": curve,
        "max_matched": max_matched,
        "max_returned": max_returned,
        "distractor_entries_total": distractor_total,
        "distractor_recipe": DISTRACTOR_RECIPE_V1,
    }
    rates = {
        "target_in_returned": {"num": target_hits["num"], "den": target_hits["den"]},
        "forbidden_leak": {"num": forbidden_leak["num"], "den": forbidden_leak["den"]},
        "excluded_by_budget": {"num": excluded_rows, "den": len(rows)},
        # 诚实边界：同义改写命中率**只测量**，不设置"应当 100%"的断言（§5.4）
        "paraphrase_hit_measured_only": {"num": para_hits["num"], "den": para_hits["den"]},
    }

    invariants = {
        "INV-08": {
            "pass": not inv08_bad,
            "detail": ("共 %d 行；superseded 条目进入 returned 的身份违规 = %d 条；"
                       "按 (subject,relation,object) 身份判禁漏 = %d 行"
                       % (len(rows), len(inv08_bad),
                          forbidden_leak["num"])),
        },
        "INV-09": {
            "pass": not inv09_bad,
            "detail": ("预算诚实：matched>0 却空返回且无标记 = %d；"
                       "excluded>0 但 truncated=false = %d；返回 id 不在 Store = %d；"
                       "超过 topK = %d（违规明细见 metrics）"
                       % (sum(1 for x in inv09_bad if "静默返回空" in x["note"]),
                          sum(1 for x in inv09_bad if "truncated=false" in x["note"]),
                          sum(1 for x in inv09_bad if "不在 Store" in x["note"]),
                          sum(1 for x in inv09_bad if "超过 topK" in x["note"]))),
        },
        "INV-10": {
            "pass": not inv10_bad,
            "detail": ("数值有界：tokens_used 越界 = %d；excluded≠matched-returned = %d；"
                       "score 非有限 = %d；band 非法 = %d；query_digest 非法 = %d；"
                       "budget 非正 = %d"
                       % (sum(1 for x in inv10_bad if "tokens_used" in x["note"]),
                          sum(1 for x in inv10_bad if "excluded_by_budget" in x["note"]),
                          sum(1 for x in inv10_bad if "score" in x["note"]),
                          sum(1 for x in inv10_bad if "potency_band" in x["note"]),
                          sum(1 for x in inv10_bad if "query_digest" in x["note"]),
                          sum(1 for x in inv10_bad if "token_budget" in x["note"]))),
        },
    }
    if inv08_bad:
        notes.append("INV-08 违规明细：" + str(inv08_bad[:5]))
    if inv09_bad:
        notes.append("INV-09 违规明细：" + str(inv09_bad[:5]))
    if inv10_bad:
        notes.append("INV-10 违规明细：" + str(inv10_bad[:5]))

    # ── 诚实边界（写进 notes，报告会带出来）────────────────
    notes.extend([
        "同义改写的 target 命中率只测量、不设阈值（词元重叠 ≠ 语义等价）："
        "paraphrase_hit_measured_only=%d/%d。"
        % (para_hits["num"], para_hits["den"]),
        "FINDING-F04（口径混淆，见 findings.md）：excluded_by_budget 的语义是 "
        "matched_count - len(returned)，同时受 retrieval.topK 限制；"
        "因此预算充足但 matched>topK 时也会 truncated=true（引擎现状，记录为画像而非 bug）。",
        "干扰条目按固定算法 v1 在族内展开（词库 40 条、起点 3 步长 7、"
        "id=mem_d%03d；与 chaos-set/v2/README.md 的生成算法逐字节等价），"
        "语料只给 {recipe,count}，不内嵌长文本。",
        "token_budget/top_k 覆盖只作用于 deepcopy(cfg) 副本，未写 rules/。",
    ])
    if fallback_la:
        notes.append("有 %d 条 Entry 缺 last_access，本族按 ctx.base_ts 兜底"
                     "（否则 potency<0.15 会被归档阈值全部过滤，看起来像召回永远为空）。" % fallback_la)

    return {
        "checks": checks.as_dict(),
        "metrics": metrics,
        "rates": rates,
        "samples": samples,
        "notes": notes,
        "invariants": invariants,
        "skipped": False,
        "skip_reason": None,
    }
