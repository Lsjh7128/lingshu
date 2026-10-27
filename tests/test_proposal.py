# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 纠正 → 提议（W6）

    python tests/test_proposal.py

覆盖：
- 纠正能正确改写 expected / forbidden
- 证据不足（<3 条）→ no_proposal
- 无效旋钮 → 拒绝（not_effective），不硬凑提议
- 有效旋钮 → 成提议，且必带 evidence / risk / rollback
- 黑名单目标 → 拒绝（C6）
- validate() 的必填字段检查
- 提议不直接改配置（只产出文本/队列条目）
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config as cfgmod           # noqa: E402
from engine import proposal                   # noqa: E402
from engine import replay as rp               # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))


def _cors(kind="should_have_recalled", n=5):
    return [{"turn_id": f"v1/g00{i}", "kind": kind, "entry_id": f"mem_g00{i}_1",
             "user_quote": f"第{i}条应该想起来"} for i in range(1, n + 1)]


def test_apply() -> None:
    print("\n[改写] 纠正变成新测试样本")
    turns, _ = rp.load_dataset(ROOT)
    out = proposal.apply_corrections(turns, _cors(n=2))
    t = next(x for x in out if x["id"] == "v1/g001")
    check("should_have_recalled 扩展 expected",
          "mem_g001_1" in t["expected_ids"], str(t["expected_ids"]))
    check("打上 corrected 标签", "corrected" in t["labels"])
    orig = next(x for x in turns if x["id"] == "v1/g001")
    check("不改原数据集（深拷贝）", "mem_g001_4" not in orig["expected_ids"])

    out2 = proposal.apply_corrections(
        turns, [{"turn_id": "v1/a001", "kind": "wrong",
                 "entry_id": "mem_a001_new", "user_quote": "这个值不对"}])
    t2 = next(x for x in out2 if x["id"] == "v1/a001")
    check("wrong 把条目移入 forbidden",
          "mem_a001_new" in t2["forbidden_ids"] and "mem_a001_new" not in t2["expected_ids"])


def test_insufficient() -> None:
    print("\n[证据不足] 不硬凑提议")
    res = proposal.propose(ROOT, corrections=_cors(n=1))
    check("1 条纠正 → no_proposal", res["no_proposal"], str(res["reason"]))
    check("原因标 insufficient_evidence",
          any(r["reason"] == "insufficient_evidence" for r in res["rejected"]),
          str(res["rejected"]))


def test_effective() -> None:
    print("\n[有效假设] 回放验证后成提议")
    res = proposal.propose(ROOT, corrections=_cors(n=5), red_team_pass=True)
    check("产出至少 1 条提议", len(res["proposals"]) >= 1, str(len(res["proposals"])))
    p = res["proposals"][0]
    check("必带 evidence 且计数正确", p["evidence"]["count"] == 5, str(p["evidence"]))
    check("必带 rollback", bool(p["rollback"]), p["rollback"])
    check("必带 risk", bool(p["risk"]), p["risk"][:40])
    check("expected_effect 含基线→候选", "→" in p["expected_effect"], p["expected_effect"])
    check("变更确实是 topK 3→4",
          p["target"] == "retrieval.topK" and p["change"]["to"] == 4,
          "%s %s" % (p["target"], p["change"]))
    check("提议不直接改配置", cfgmod.flatten(cfgmod.default())["retrieval.topK"] == 3)


def test_unresolvable() -> None:
    print("\n[无效假设] 解决不了就不提议")
    res = proposal.propose(ROOT, corrections=_cors(kind="should_not_recall", n=5),
                           red_team_pass=True)
    # should_not_recall 的候选旋钮（topK=2 / dedupe.similarity=0.75）不应误报有效
    for p in res["proposals"]:
        check("提案 %s 必须真的提升解决率" % p["target"],
              p["replay"]["resolved"] > res["baseline"]["resolved"],
              "%.3f vs %.3f" % (p["replay"]["resolved"], res["baseline"]["resolved"]))
    check("至少记录了 not_effective 或 no_proposal",
          res["no_proposal"] or any(r["reason"] == "not_effective" for r in res["rejected"]))


def test_blacklist() -> None:
    print("\n[黑名单] C6 拒绝")
    bad = {"id": "x", "status": "ready", "target": "guardrail.G7", "change": {"from": 1, "to": 2},
           "expected_effect": "e", "risk": "r", "rollback": "rb",
           "evidence": {"kind": "wrong", "count": 9}, "confidence": 0.9}
    check("validate 拒绝 guardrail.*", proposal.validate(bad) == "blacklist:guardrail.G7",
          str(proposal.validate(bad)))
    bad2 = dict(bad, target="evaluation.threshold")
    check("validate 拒绝 evaluation.*", str(proposal.validate(bad2)).startswith("blacklist"))
    bad3 = dict(bad, target="retrieval.topK", rollback="")
    check("缺 rollback → 拒绝", proposal.validate(bad3) == "missing:rollback",
          str(proposal.validate(bad3)))
    ok = dict(bad, target="retrieval.topK")
    check("合法提案通过校验", proposal.validate(ok) is None, str(proposal.validate(ok)))


def main() -> int:
    print("LINGSHU 验收 · 纠正 → 提议（W6）")
    test_apply()
    test_insufficient()
    test_effective()
    test_unresolvable()
    test_blacklist()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
