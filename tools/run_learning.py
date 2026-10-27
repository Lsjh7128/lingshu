# -*- coding: utf-8 -*-
"""学习机制 CLI：跑基线、跑候选、看归因。

用法：
    python tools/run_learning.py                        # 只看基线
    python tools/run_learning.py --set retrieval.topK=2 # 候选（可多次 --set）
    python tools/run_learning.py --json                 # 机器可读
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config as cfgmod           # noqa: E402
from engine import replay as rp               # noqa: E402


def _parse(v: str):
    for f in (int, float):
        try:
            return f(v)
        except ValueError:
            pass
    if v in ("true", "false"):
        return v == "true"
    return v


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--set", action="append", default=[], metavar="PATH=VALUE")
    ap.add_argument("--version", default="v1")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    base = cfgmod.default()
    cand = base
    for item in args.set:
        path, _, val = item.partition("=")
        cand = cfgmod.apply_path(cand, path.strip(), _parse(val.strip()))

    base_score = rp.score(rp.replay(base, ROOT, args.version))
    out = {"baseline": base_score}

    if args.set:
        rep = rp.attribute(base, cand, ROOT, args.version)
        out["candidate"] = rep["combined"]
        out["attribute"] = rep["contributions"]
        out["changed"] = rep["changed"]

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2, sort_keys=True))
        return 0

    def row(name, s):
        return ("%-22s recall@3=%.3f  false_inj=%.3f  used_cov=%.3f  "
                "budget_util=%.3f  supersede=%.3f  stale_leak=%d  conf_match=%.3f"
                % (name, s["recall_at_3"], s["false_inject_rate"], s["used_coverage"],
                   s["budget_utilization"], s["supersede_accuracy"], s["stale_leak"],
                   s["confidence_match"]))

    print(row("baseline", base_score))
    if args.set:
        print(row("candidate", out["candidate"]))
        print("\n归因（单参数消融）：")
        for c in out["attribute"]:
            print("  %-26s Δrecall=%+.4f  Δfalse_inj=%+.4f  Δbudget=%+.4f"
                  % (c["param"], c["d_recall_at_3"], c["d_false_inject_rate"],
                     c["d_budget_utilization"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
