import json
import os

paper = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mem0 = os.path.join(paper, "mem0-chaos-lab", "results", "summary.json")
review = os.path.join(paper, "mem0-chaos-lab", "raw", "human-review.json")
for p in (mem0, review):
    print("==", p, os.path.exists(p))
    if os.path.exists(p):
        d = json.load(open(p, encoding="utf-8"))
        if isinstance(d, dict):
            for k in ("ratios", "aggregates", "cells", "review", "human_review", "notes"):
                if k in d:
                    print(k, json.dumps(d[k], ensure_ascii=False)[:800])
