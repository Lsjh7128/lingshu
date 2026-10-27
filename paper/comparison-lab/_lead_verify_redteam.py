# -*- coding: utf-8 -*-
"""Lead 独立复核（只读）：红队案例数据、补丁范围、主分支未动。"""
import hashlib
import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CS = os.path.join(ROOT, "paper", "case-study")

def sha(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()

# 1) payloads
rows = [json.loads(l) for l in open(os.path.join(CS, "payloads.jsonl"), encoding="utf-8") if l.strip()]
print("payloads:", len(rows))
from collections import Counter
print("levels:", dict(Counter(r["level"] for r in rows)))
print("groups:", dict(Counter(r["group"] for r in rows)))
print("ids unique:", len({r["id"] for r in rows}) == len(rows))
print("all have note:", all(r.get("note") for r in rows))

# 2) recompute rates from records
for phase in ("before", "after"):
    d = json.load(open(os.path.join(CS, f"results-{phase}.json"), encoding="utf-8"))
    recs = d["records"]
    by = {}
    for r in recs:
        by.setdefault(r["level"], []).append(bool(r.get("blocked")))
    print(f"-- {phase}: n={len(recs)} overall={sum(bool(r.get('blocked')) for r in recs)}/{len(recs)}")
    for lvl in sorted(by):
        v = by[lvl]
        print("   %s: %d/%d" % (lvl, sum(v), len(v)))
    # 与文件内 groups 对照
    bad = [k for k, g in d["groups"].items()
           if g["blocked"] != sum(by.get(k, []))]
    print("   groups match recompute:", not bad, bad)

# 3) fig4
fig = os.path.join(ROOT, "paper", "figures", "fig4-redteam.pdf")
print("fig4 exists:", os.path.isfile(fig), os.path.getsize(fig) if os.path.isfile(fig) else 0)
print("fig4 sha:", sha(fig)[:16] if os.path.isfile(fig) else "-")
inp = json.load(open(os.path.join(CS, "fig4-inputs.json"), encoding="utf-8"))
print("fig4-inputs keys:", list(inp.keys())[:8])

# 4) product plugin untouched vs fork (only patched files should differ)
prod = os.path.join(ROOT, "plugin", "dsh-lingshu")
fork = os.path.join(CS, "fork", "dsh-lingshu")
diffs = []
for name in sorted(os.listdir(prod)):
    p, f = os.path.join(prod, name), os.path.join(fork, name)
    if not os.path.isfile(p):
        continue
    if not os.path.isfile(f):
        diffs.append((name, "missing-in-fork"))
    elif sha(p) != sha(f):
        diffs.append((name, "differs"))
print("product-vs-fork differing files:", diffs)

# 5) product plugin suites still green
NODE = os.path.join(
    os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh"),
    "dsh-runtimes", "dsh-primary-runtime", "dependencies", "node", "bin", "node.exe")
for sim in ("control-sim.mjs", "host-sim.mjs", "stress.mjs"):
    p = subprocess.run([NODE, os.path.join(prod, sim), "--assert"], capture_output=True,
                       text=True, encoding="utf-8", errors="replace", cwd=prod, timeout=300)
    print("product", sim, "exit", p.returncode)
