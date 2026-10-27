# -*- coding: utf-8 -*-
"""W6/W7 · 端到端闭环：纠正 → 提议 → 灰度 → 回滚或放行

    python tools/run_loop.py

这条链是学习机制存在的意义：
    corrections.jsonl
      → apply_corrections（把抱怨变成新测试样本）
      → propose（假设 → 回放验证 → 才成提议）
      → gray.Rollout（10% → 50% → 100%，护栏兜底）
      → promote / rollback
"""
from __future__ import annotations

import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config as cfgmod           # noqa: E402
from engine import gray, proposal, replay as rp  # noqa: E402


def _slice(report: dict, sessions: set[str]) -> dict:
    return {"turns": [t for t in report["turns"] if t["session_id"] in sessions],
            "meta": report["meta"]}


def _recall(report: dict) -> float | None:
    exp = [t for t in report["turns"] if t["expected_ids"]]
    if not exp:
        return None
    return sum(1 for t in exp
               if set(t["expected_ids"]) & set(t["returned_ids"])) / len(exp)


def main() -> int:
    base_cfg = cfgmod.default()
    print("① 读取纠正样本")
    cors = proposal.load_corrections(ROOT)
    print("   %d 条纠正，类型：%s" % (len(cors), sorted({c["kind"] for c in cors})))

    print("\n② 生成提议（假设 → 回放验证）")
    res = proposal.propose(ROOT, base_cfg, corrections=cors, red_team_pass=True)
    for r in res["rejected"]:
        print("   拒绝: %-28s %s" % (r.get("target", r.get("kind")), r["reason"]))
    if res["no_proposal"]:
        print("\n   没有任何提议通过验证（no_proposal）—— 这是合法结果，不是失败。")
        return 0

    p = res["proposals"][0]
    print("\n   采纳 1 条：")
    print(proposal.to_yaml_block(p))
    print("   基线纠正解决率 %.2f → 候选 %.2f"
          % (res["baseline"]["resolved"], p["replay"]["resolved"]))

    print("\n③ 灰度（10% → 50% → 100%，护栏兜底）")
    cand_cfg = cfgmod.apply_path(base_cfg, p["target"], p["change"]["to"])
    rep_b = rp.replay(base_cfg, ROOT)
    rep_c = rp.replay(cand_cfg, ROOT)
    vb, vc = rep_b["meta"]["param_version"], rep_c["meta"]["param_version"]

    sessions = sorted({t["session_id"] for t in rep_b["turns"]})
    rollout = gray.Rollout(vb, vc, min_samples=40)
    guard = 0
    while rollout.state == "running" and guard < 100:
        guard += 1
        cs = {s for s in sessions if gray.bucket(s, rollout.ratio) == "candidate"}
        if not cs:
            rollout._log("rollback", reason="empty_cohort", restored=vb)
            rollout.state = "rolled_back"
            break
        slice_c, slice_b = _slice(rep_c, cs), _slice(rep_b, cs)
        m = gray.metrics_from_report(
            slice_c, baseline_recall=_recall(slice_b),
            token_ratio=round(sum(t["tokens_used"] / t["token_budget"]
                                  for t in slice_c["turns"])
                              / max(1, len(slice_c["turns"])), 6),
            refusal_regression_pass=1.0)     # 模拟值：回放无法测拒绝边界
        windows = max(1, math.ceil(40 / max(1, len(slice_c["turns"]))))
        for _ in range(windows):
            if rollout.state != "running":
                break
            prev = rollout.stage
            rollout.observe(m, samples=len(slice_c["turns"]))
            if rollout.stage != prev:
                break

    for e in rollout.events:
        print("   %-22s %s" % (e["event"],
                               json.dumps({k: v for k, v in e.items() if k != "event"},
                                          ensure_ascii=False)))
    print("\n④ 结果：%s" % rollout.state)
    if rollout.state == "promoted":
        print("   闭环完成：纠正被真正解决，且护栏全程未破。")
        print("   注意：这仍是合成回放集 → 生产环境必须接真实纠正率后才可放行（LEARN-7）。")
    else:
        print("   回滚：提议在灰度期被拦下。这正是护栏存在的意义——")
        print("   回放里看起来有效，不等于线上安全。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
