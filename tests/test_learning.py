# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 学习机制与记忆契约（可直接运行，无 pytest 依赖）

    python tests/test_learning.py

覆盖：
- LEARN-V1 幂等 / V2 可归因 / V3 可复现（docs/13 §4.4）
- recall 契约 V1/V2/V3/V5/V7（contracts/recall.schema.json）
- MEM-19 plan 零写 / MEM-20 六步同步（docs/02 §3.5）
- MEM-1/MEM-8 / MET V6 措辞（docs/15）
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config as cfgmod           # noqa: E402
from engine import memory, metacog, replay as rp  # noqa: E402
from engine.model import Entry, Store         # noqa: E402

BASE = 1_800_000_000.0
DAY = 86400.0
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))


def _cfg(**over) -> dict:
    c = cfgmod.default()
    for k, v in over.items():
        c = cfgmod.apply_path(c, k, v)
    return c


def _e(eid, **kw) -> Entry:
    d = {"id": eid, "type": "semantic", "subject": "s", "relation": "r", "object": "o",
         "content": "内容 " + eid, "importance": 0.9, "last_access": BASE,
         "layer": "warm", "session_id": "s0", "title": eid}
    d.update(kw)
    return Entry.from_json(d)


# ── LEARN-V1 / V3 ─────────────────────────────────────────
def test_determinism() -> None:
    print("\n[LEARN-V1/V3] 幂等与可复现")
    cfg = cfgmod.default()
    a = rp.replay(cfg, ROOT)
    b = rp.replay(cfg, ROOT)
    ja = json.dumps(a, ensure_ascii=False, sort_keys=True)
    jb = json.dumps(b, ensure_ascii=False, sort_keys=True)
    check("LEARN-V1 两次回放逐字节相同",
          hashlib.sha256(ja.encode()).hexdigest() == hashlib.sha256(jb.encode()).hexdigest())
    check("LEARN-V3 param_version 稳定",
          a["meta"]["param_version"] == b["meta"]["param_version"],
          a["meta"]["param_version"])


# ── LEARN-V2 ──────────────────────────────────────────────
def test_attribution() -> None:
    print("\n[LEARN-V2] 单参数消融归因")
    base = cfgmod.default()
    cand = cfgmod.apply_path(base, "decay.tau.semantic", 60.0)
    rep = rp.attribute(base, cand, ROOT)
    top = rep["contributions"][0]["param"] if rep["contributions"] else None
    check("LEARN-V2 归因指向被改的 key", top == "decay.tau.semantic", str(top))
    check("LEARN-V2 只报一个贡献项", len(rep["contributions"]) == 1)


# ── recall 契约 ───────────────────────────────────────────
def test_recall_contract() -> None:
    print("\n[recall 契约] V1/V2/V3/V5/V7")
    cfg = cfgmod.default()
    turns, store = rp.load_dataset(ROOT)

    # V7：无命中 → 两个"空"的语义严格区分
    b = next(t for t in turns if t["id"] == "v1/b001")
    r = memory.recall(store, b["user_message"], BASE, cfg)
    v = metacog.verdict(r, cfg)
    check("V7 无命中 matched_count=0", r["matched_count"] == 0, str(r["matched_count"]))
    check("V7 无命中 → unknown/ask_clarify",
          (v["confidence_level"], v["expression"]) == ("unknown", "ask_clarify"),
          str(v["confidence_level"]) + "/" + str(v["expression"]))

    # V5：层过滤先于截断 —— Cold 高 potency 不得挤占 Warm 名额
    s2 = Store()
    s2.put(_e("w1", content="暗号 项目 代号 暗号", layer="warm", importance=0.95))
    for k in range(5):
        s2.put(_e(f"c{k}", content="暗号 项目 代号 暗号", layer="cold", importance=1.0))
    r5 = memory.recall(s2, "暗号 项目 代号", BASE, cfg)
    ids = [x["id"] for x in r5["returned"]]
    check("V5 Cold 不挤占 Warm 名额", "w1" in ids, str(ids))

    # V2：截短不丢 ID
    s3 = Store()
    s3.put(_e("long", content="长城" * 400, importance=0.9))
    r2 = memory.recall(s3, "长城", BASE, _cfg(**{"recallBudget.tokenBudgetDefault": 100}))
    check("V2 截短保留 ID",
          bool(r2["returned"]) and r2["returned"][0]["id"] == "long"
          and r2["returned"][0]["content_truncated"] is True,
          json.dumps(r2["returned"][:1], ensure_ascii=False)[:90])

    # V1：装不下最小预览 → minimum_budget，而不是空数组
    r1 = memory.recall(s3, "长城", BASE, _cfg(**{"recallBudget.tokenBudgetDefault": 5}))
    check("V1 给出 minimum_budget 且不静默返回空",
          r1["returned"] == [] and r1["minimum_budget"] is not None,
          "minimum_budget=%s" % r1["minimum_budget"])

    # V3：保序 —— 追加更短的条目，不得替换掉第一名
    s4 = Store()
    s4.put(_e("big", content="甲乙" * 200, importance=0.99))
    s4.put(_e("small", content="甲乙", importance=0.10))
    r3 = memory.recall(s4, "甲乙", BASE, _cfg(**{"recallBudget.tokenBudgetDefault": 150}))
    check("V3 保序装填（第一仍第一）",
          bool(r3["returned"]) and r3["returned"][0]["id"] == "big",
          str([x["id"] for x in r3["returned"]]))


def test_recall_schema_fields() -> None:
    print("\n[recall 契约] 必填字段齐全")
    cfg = cfgmod.default()
    turns, store = rp.load_dataset(ROOT)
    r = memory.recall(store, "KE001 长文1的内容", BASE, cfg)
    required = ["matched_count", "returned", "excluded_by_budget", "excluded_results",
                "excluded_results_truncated", "minimum_budget", "tokens_used",
                "token_budget", "truncated", "degraded", "layer_filter"]
    missing = [k for k in required if k not in r]
    check("recall 结果字段齐全", not missing, ",".join(missing))


# ── V6 措辞 ───────────────────────────────────────────────
def test_metacog_wording() -> None:
    print("\n[V6] 装不下必须说出来")
    cfg = cfgmod.default()
    turns, store = rp.load_dataset(ROOT)
    e = next(t for t in turns if t["id"] == "v1/e001")
    r = memory.recall(store, e["user_message"], BASE, cfg)
    v = metacog.verdict(r, cfg)
    ph = metacog.phrase(r, v)
    check("V6 excluded_by_budget>0", r["excluded_by_budget"] > 0, str(r["excluded_by_budget"]))
    check("V6 置信度封顶 mid + hedge",
          v["confidence_level"] == "mid" and v["expression"] == "hedge",
          v["confidence_level"] + "/" + v["expression"])
    check("V6 措辞承认有未带出的条目", "没带出来" in ph, ph)


# ── 取代 / 去重 ───────────────────────────────────────────
def test_supersede_dedupe() -> None:
    print("\n[MEM-1/8] 取代与去重")
    cfg = cfgmod.default()
    s = Store()
    s.put(_e("n1", subject="项目X", relation="代号", object="A",
             content="项目X的代号是 A，编号 QX"))
    s.put(_e("n2", subject="项目X", relation="代号", object="B",
             content="项目X的代号是 B，编号 QX"))
    old = memory.apply_supersede(s, s.get("n2"), cfg)
    r = memory.recall(s, "QX 项目X的代号是什么", BASE, cfg)
    ids = [x["id"] for x in r["returned"]]
    check("MEM-1 旧值被取代后不再命中", old == "n1" and "n1" not in ids, str(ids))

    s2 = Store()
    s2.put(_e("d1", subject="项目Y", relation="代号", object="C",
              content="项目Y的代号是 C，编号 QY"))
    s2.put(_e("d2", subject="项目Y", relation="代号", object="C",
              content="项目Y的代号是 C，编号 QY"))
    out = memory.dedupe(s2, cfg)
    r2 = memory.recall(s2, "QY 项目Y的代号", BASE, cfg)
    check("MEM-8 语义等价条目被标 dormant 且不再命中",
          len(out) == 1 and "d2" not in [x["id"] for x in r2["returned"]], str(out))


# ── 遗忘两段式 ────────────────────────────────────────────
def test_forget() -> None:
    print("\n[MEM-19/20] 遗忘两段式")
    cfg = cfgmod.default()
    s = Store()
    s.put(_e("old", importance=0.1, last_access=BASE - 400 * DAY,
             edges=[{"to": "keep", "type": "caused_by", "weight": 0.9, "origin": "rule"}]))
    s.put(_e("keep", importance=0.9, depends_on=["old"]))

    before = s.snapshot_hash()
    plan = memory.plan_forget(s, BASE, cfg)
    after = s.snapshot_hash()
    check("MEM-19 plan 阶段零写操作", before == after)
    check("MEM-19 plan 列出低价值条目", "old" in plan["low_value"], str(plan["low_value"]))

    res = memory.commit_forget(s, plan, ["old"], "low_value", BASE, cfg)
    ok_entry = s.get("old") is None
    ok_edges = all("old" not in [x.get("to") for x in e.edges] for e in s.all())
    ok_deps = all("old" not in e.depends_on for e in s.all())
    check("MEM-20 硬删后条目消失", ok_entry)
    check("MEM-20 边（含入边）已清理", ok_edges)
    check("MEM-20 depends_on 引用已清理", ok_deps)
    check("MEM-20 六步同步清单完整",
          set(res["sync"]) == set(memory.HARD_DELETE_SYNC), str(res["sync"]))
    check("MEM-20 审计留痕", any(a["action"].startswith("forget") for a in s.audit))


def main() -> int:
    print("LINGSHU 验收 · 学习机制与记忆契约")
    test_determinism()
    test_attribution()
    test_recall_contract()
    test_recall_schema_fields()
    test_metacog_wording()
    test_supersede_dedupe()
    test_forget()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
