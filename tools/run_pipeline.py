# -*- coding: utf-8 -*-
"""端到端流水线：在回放集上把 14 个模块串起来跑一遍，看汇总。

    python tools/run_pipeline.py
    python tools/run_pipeline.py --onboarding   # 开启冷启动门控
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config as cfgmod            # noqa: E402
from engine import pipeline                   # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--onboarding", action="store_true", help="开启冷启动门控")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    cfg = cfgmod.default()
    if args.onboarding:
        cfg = cfgmod.apply_path(cfg, "replay.applyOnboarding", True)

    rep = pipeline.replay_pipeline(cfg, ROOT)
    s = rep["summary"]

    if args.json:
        print(json.dumps(s, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    print("端到端流水线 · 回放 %d 轮（冷启动门控：%s）" % (s["turns"], args.onboarding))
    print("  recall@3        : %.3f" % s["recall_at_3"])
    print("  违禁条目泄漏    : %d" % s["forbidden_leak"])
    print("  预算截断轮次    : %d" % s["truncated_turns"])
    print("\n  注入块数分布：")
    for k, v in s["block_count_hist"].items():
        print("    %s 块 → %d 轮" % (k, v))
    print("\n  丢弃原因（plugin:reason → 次数）：")
    for k, v in s["dropped_reasons"].items() or [("（无）", 0)]:
        print("    %-40s %d" % (k, v))
    if s["suppressed"]:
        print("\n  被门控抑制：")
        for k, v in s["suppressed"].items():
            print("    %-30s %d 轮" % (k, v))

    # 契约校验：一条最小 event 是否包含全部必填字段
    missing_demo = pipeline.check_event(
        {"event_id": "x", "at": 0, "session_id": "s", "turn_id": "t",
         "param_version": "pv", "prompt_version": "pp", "compose_version": "c",
         "domains": {}, "dropped": [], "final_style_multiplier": 1.0})
    print("\n  prompt/compose 事件必填字段校验：%s" % ("通过" if not missing_demo else missing_demo))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
