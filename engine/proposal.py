# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 纠正 → 提议（docs/13 §5，W6）

核心主张：**提议不是"用户抱怨了就改"，而是「抱怨 → 假设 → 回放验证 → 才成提议」。**

流程：
1. `corrections.jsonl` 提供用户纠正（kind + 目标条目 + 原话）；
2. `apply_corrections()` 把纠正变成**新的测试样本**（改 expected / forbidden）；
3. `propose()` 对每个候选旋钮跑一次回放，只有同时满足三条才成提议：
   ① 纠正被真正解决；② 整体目标函数不退化；③ 护栏不破。
4. 触到黑名单的目标直接拒绝（宪法 C6）。
"""
from __future__ import annotations

import json
import os
from typing import Any

from . import config as cfgmod
from . import gray
from . import replay as rp

# 提议必须带的字段（缺一个就不许进队列）
REQUIRED_FIELDS = ("id", "target", "change", "expected_effect", "risk",
                   "rollback", "evidence", "confidence", "status")

# 与 learning-params.yaml#proposal.forbiddenTargets 保持一致（黑名单，宪法 C6）
FORBIDDEN_TARGETS = ("evaluation.", "replay-set.", "guardrail.", "constitution.",
                     "compose-matrix.", "learning.objective")

# 假设生成器：纠正类型 → 值得一试的旋钮（docs/13 §6 的 L1 范围）
KNOB_HYPOTHESES: dict[str, list[tuple[str, Any]]] = {
    "should_have_recalled": [
        ("retrieval.topK", 4),
        ("potency.archiveThreshold", 0.10),
        ("edge.minWeight", 0.2),
    ],
    "should_not_recall": [
        ("retrieval.topK", 2),
        ("dedupe.similarity", 0.75),
    ],
    "wrong": [
        ("dedupe.similarity", 0.90),
        ("potency.archiveThreshold", 0.25),
    ],
}

MIN_EVIDENCE = 3          # 少于 3 条同类纠正不生成提议
SIGNIFICANCE = 0.02       # 纠正解决率的提升门槛


# ── 纠正样本 ──────────────────────────────────────────────

def load_corrections(root: str, version: str = "v1") -> list[dict[str, Any]]:
    path = os.path.join(root, "replay-set", "fixtures", f"{version}.corrections.jsonl")
    if not os.path.exists(path):
        return []
    return [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]


def apply_corrections(turns: list[dict], corrections: list[dict]) -> list[dict]:
    """把纠正变成新的测试样本。返回**深拷贝**，不改原数据集。"""
    by_turn = {t["id"]: t for t in turns}
    out = json.loads(json.dumps(turns))          # 简单深拷贝
    idx = {t["id"]: t for t in out}
    for c in corrections:
        t = idx.get(c.get("turn_id"))
        if t is None:
            continue
        kind, eid = c.get("kind"), c.get("entry_id")
        if not eid:
            continue
        if kind == "should_have_recalled":
            if eid not in t["expected_ids"]:
                t["expected_ids"].append(eid)
        elif kind in ("should_not_recall", "wrong"):
            t.setdefault("forbidden_ids", [])
            if eid not in t["forbidden_ids"]:
                t["forbidden_ids"].append(eid)
            t["expected_ids"] = [x for x in t["expected_ids"] if x != eid]
        # clamp / reject 属人设解析器，不影响检索，忽略
        t.setdefault("labels", [])
        if "corrected" not in t["labels"]:
            t["labels"].append("corrected")
    _ = by_turn
    return out


def _resolved(turn: dict) -> bool:
    """纠正样本的判定从严：expected 必须全部出现，forbidden 一个都不能出现。"""
    got = set(turn.get("returned_ids") or [])
    exp = set(turn.get("expected_ids") or [])
    forb = set(turn.get("forbidden_ids") or [])
    return exp.issubset(got) and not (forb & got)


# ── 提议 ─────────────────────────────────────────────────

def _mk_id(target: str, change: Any) -> str:
    return "prop-%s-%s" % (target.replace(".", "_"), str(change).replace(".", "_"))


def propose(root: str, base_cfg: dict | None = None,
            version: str = "v1",
            corrections: list[dict] | None = None,
            red_team_pass: bool | None = None) -> dict[str, Any]:
    """生成提议。

    `red_team_pass`：
      - `True`  → 声称红队已通过（生产环境由红队回归集给出）；
      - `False` → 直接拒绝（安全回归不通过）；
      - `None`  → **允许成提议，但标 `status=needs_red_team`，禁止进入灰度**。
    把"提议"和"可上线"分开，是这条链上最容易糊掉的一步。
    """
    base = base_cfg or cfgmod.default()
    turns, _store = rp.load_dataset(root, version)
    corrections = corrections if corrections is not None else load_corrections(root, version)
    if not corrections:
        return {"proposals": [], "no_proposal": True, "reason": "no_corrections"}

    corrected = apply_corrections(turns, corrections)
    ids = {c["turn_id"] for c in corrections}

    base_rep = rp.replay(base, root, version, turns=corrected)
    base_resolved = _resolved_rate(base_rep, ids)
    base_score = rp.score(base_rep)

    # 按 kind 分组的证据
    evidence: dict[str, list[dict]] = {}
    for c in corrections:
        evidence.setdefault(c["kind"], []).append(c)

    proposals, rejected = [], []
    for kind, items in sorted(evidence.items()):
        if len(items) < MIN_EVIDENCE:
            rejected.append({"kind": kind, "reason": "insufficient_evidence",
                             "count": len(items), "need": MIN_EVIDENCE})
            continue
        for target, value in KNOB_HYPOTHESES.get(kind, []):
            if any(target.startswith(f) for f in FORBIDDEN_TARGETS):
                rejected.append({"kind": kind, "target": target,
                                 "reason": "blacklist:C6"})
                continue
            cand = cfgmod.apply_path(base, target, value)
            rep = rp.replay(cand, root, version, turns=corrected)
            resolved = _resolved_rate(rep, ids)
            if resolved - base_resolved < SIGNIFICANCE:
                rejected.append({"kind": kind, "target": target,
                                 "reason": "not_effective",
                                 "resolved": round(resolved, 4)})
                continue

            score = rp.score(rep)
            if red_team_pass is None:
                status, guard_failed = "needs_red_team", []
            else:
                metrics = gray.metrics_from_report(
                    rep, baseline_recall=rp.score(base_rep)["recall_at_3"],
                    token_ratio=score["budget_utilization"],
                    refusal_regression_pass=1.0 if red_team_pass else 0.0)
                check = gray.check(metrics, strict=False)
                guard_failed = check["failed"]
                if check["blocked"] or any(g in ("G5", "G6") for g in guard_failed):
                    rejected.append({"kind": kind, "target": target,
                                     "reason": "guardrail", "failed": guard_failed})
                    continue
                status = "ready"

            proposals.append({
                "id": _mk_id(target, value),
                "target": target,
                "change": {"from": cfgmod.flatten(base).get(target), "to": value},
                "expected_effect": "纠正解决率 %.2f → %.2f（%d 条样本）"
                                   % (base_resolved, resolved, len(items)),
                "risk": "token 占用 %.3f → %.3f；可能挤掉其它条目"
                        % (base_score["budget_utilization"], score["budget_utilization"]),
                "rollback": "还原 %s = %s" % (target, cfgmod.flatten(base).get(target)),
                "evidence": {"kind": kind, "count": len(items),
                             "quotes": [str(i.get("user_quote", ""))[:40] for i in items[:3]]},
                "confidence": round(min(0.95, 0.55 + 0.1 * len(items)), 3),
                "status": status,
                "guardrails": {"failed": guard_failed},
                "replay": {"resolved": round(resolved, 4),
                           "recall_at_3": score["recall_at_3"],
                           "budget_utilization": score["budget_utilization"]},
            })

    schema_errs = [e for p in proposals if (e := validate(p))]
    proposals = [p for p in proposals if not validate(p)]
    return {
        "proposals": proposals,
        "no_proposal": not proposals,
        "reason": None if proposals else "no_effective_knob",
        "baseline": {"resolved": round(base_resolved, 4), "recall": base_score["recall_at_3"]},
        "rejected": rejected,
        "invalid": schema_errs,
    }


def _resolved_rate(report: dict, ids: set[str]) -> float:
    sel = [t for t in report["turns"] if t["turn_id"] in ids]
    if not sel:
        return 0.0
    return sum(1 for t in sel if _resolved(t)) / len(sel)


def validate(p: dict) -> str | None:
    """返回错误信息；None 表示通过。"""
    missing = [f for f in REQUIRED_FIELDS if f not in p or p[f] in (None, "", [])]
    if missing:
        return "missing:" + ",".join(missing)
    if any(str(p["target"]).startswith(f) for f in FORBIDDEN_TARGETS):
        return "blacklist:" + str(p["target"])
    if not p.get("rollback"):
        return "missing_rollback"
    return None


def to_yaml_block(p: dict) -> str:
    """把提议渲染成可粘贴的 YAML（进 proposal 队列用）。"""
    return ("- id: %s\n  target: %s\n  change: %s -> %s\n  evidence: %s ×%d\n"
            "  expected: %s\n  risk: %s\n  rollback: %s\n  confidence: %s"
            % (p["id"], p["target"], p["change"]["from"], p["change"]["to"],
               p["evidence"]["kind"], p["evidence"]["count"],
               p["expected_effect"], p["risk"], p["rollback"], p["confidence"]))
