import json
import glob
import os

base = os.path.dirname(os.path.abspath(__file__))
s = json.load(open(os.path.join(base, "results", "summary.json"), encoding="utf-8"))
print("keys:", list(s.keys()))
cells = s.get("cells")
print("cells type:", type(cells), "len:", len(cells))
if isinstance(cells, list):
    for c in cells:
        if isinstance(c, dict) and "s1" in str(c.get("cell", c.get("id", ""))):
            print({k: c.get(k) for k in c if k in ("cell", "subject", "round", "hit", "leak", "intercept", "n", "answer", "answers")})
else:
    for k, v in cells.items():
        if "s1" in k:
            print(k, {kk: v.get(kk) for kk in ("hit", "leak", "intercept", "n")})

print("\n=== per-cell U4 stdout ===")
for d in sorted(glob.glob(os.path.join(base, "raw", "cells", "s1-memory-*"))):
    name = os.path.basename(d)
    p = os.path.join(d, "turn4.a1.stdout.txt")
    out = open(p, encoding="utf-8", errors="replace").read().strip() if os.path.exists(p) else "(missing)"
    print("\n--", name, "--")
    print(out[:400].replace("\n", " "))
