# -*- coding: utf-8 -*-
"""W5 · 灰度通道与护栏自动回滚（模拟）

用法：
    python tools/run_gray.py --set retrieval.topK=4        # 好候选 → 逐档推进
    python tools/run_gray.py --set retrieval.topK=1        # 坏候选 → 被 G7 拦下并回滚
    python tools/run_gray.py --set retrieval.topK=1 --strict

说明：
- 用回放集模拟两会话队列（candidate / baseline），**同一会话永远落在同一边**；
- 每档位喂若干"窗口"，样本不足不会推进；跌破护栏则自动回滚；
- 回放环境测不出的指标（延迟、用户纠正率、拒绝回归）按 `None` 传入 ——
  **不填 0**。硬红线缺数据 = 阻断（fail-closed）。
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config as cfgmod           # noqa: E402
from engine import gray, replay as rp         # noqa: E402


def _parse(v: str):
    for f in (int, float):
        try:
            return f(v)
        except ValueError:
            pass
    return v


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
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", default=[], metavar="PATH=VALUE")
    ap.add_argument("--min-samples", type=int, default=40,
                    help="每档最少样本（生产默认 200；回放集 200 turn，故默认 40 → 每档 2 个窗口）")
    ap.add_argument("--strict", action="store_true", help="严格模式：缺任何指标都阻断")
    ap.add_argument("--stub-refusal", type=float, default=1.0,
                    help="模拟红队回归通过率（回放无法测拒绝边界）；传 -1 表示不提供")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    base_cfg = cfgmod.default()
    cand_cfg = base_cfg
    for item in args.set:
        path, _, val = item.partition("=")
        cand_cfg = cfgmod.apply_path(cand_cfg, path.strip(), _parse(val.strip()))

    rep_b = rp.replay(base_cfg, ROOT)
    rep_c = rp.replay(cand_cfg, ROOT)

    vb = rep_b["meta"]["param_version"]
    vc = rep_c["meta"]["param_version"]
    if vb == vc:
        print("candidate 与 baseline 参数相同（param_version=%s），灰度无意义" % vb)
        return 2

    refusal = None if args.stub_refusal < 0 else args.stub_refusal
    rollout = gray.Rollout(vb, vc, strict=args.strict, min_samples=args.min_samples)

    sessions = sorted({t["session_id"] for t in rep_b["turns"]})
    guard = 0
    while rollout.state == "running":
        guard += 1
        if guard > 100:
            rollout._log("rollback", reason="no_progress_guard", restored=vb)
            rollout.state = "rolled_back"
            break
        ratio = rollout.ratio
        cand_sessions = {s for s in sessions if gray.bucket(s, ratio) == "candidate"}
        base_sessions = set(sessions) - cand_sessions
        if not cand_sessions:
            # 回放集会话数很少时，10% 分桶可能命中 0 个会话。
            # 这里退化为"确定性取前 N 个"，并如实记录，不假装分桶成功。
            n = max(1, round(ratio * len(sessions)))
            cand_sessions = set(sessions[:n])
            rollout._log("cohort_too_small", requested=ratio, fallback=len(cand_sessions))
            if not cand_sessions:
                rollout._log("rollback", reason="empty_candidate_cohort", restored=vb)
                rollout.state = "rolled_back"
                break

        slice_c = _slice(rep_c, cand_sessions)
        slice_b = _slice(rep_b, cand_sessions)      # 同一批会话，基线配置
        baseline_recall = _recall(slice_b)
        token_proxy = (sum(t["tokens_used"] / t["token_budget"] for t in slice_c["turns"])
                       / max(1, len(slice_c["turns"])))

        metrics = gray.metrics_from_report(
            slice_c, baseline_recall=baseline_recall,
            token_ratio=round(token_proxy, 6),
            refusal_regression_pass=refusal,
        )
        windows = max(1, math.ceil(args.min_samples / max(1, len(slice_c["turns"]))))
        for _ in range(windows):
            if rollout.state != "running":
                break
            prev_stage = rollout.stage
            rollout.observe(metrics, samples=len(slice_c["turns"]))
            if rollout.stage != prev_stage:
                break
        if rollout.state == "running" and rollout.stage == len(gray.STEPS):
            break

    if args.json:
        print(json.dumps({"baseline": vb, "candidate": vc,
                          "result": rollout.snapshot()}, ensure_ascii=False, indent=2))
        return 0

    print("baseline=%s  candidate=%s  strict=%s" % (vb, vc, args.strict))
    print("stub refusal_regression_pass = %s%s"
          % (refusal, "（模拟值；回放无法测拒绝边界）" if refusal is not None else ""))
    print("\n事件流：")
    for e in rollout.events:
        detail = {k: v for k, v in e.items() if k != "event"}
        print("  %-22s %s" % (e["event"], json.dumps(detail, ensure_ascii=False)))
    s = rollout.snapshot()
    print("\n最终状态：%s（stage=%d, ratio=%.1f）" % (s["state"], s["stage"], s["ratio"]))
    print("灰度结束后 assign() 一律返回 baseline：%s"
          % (rollout.assign(sessions[0]) if sessions else "-"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
