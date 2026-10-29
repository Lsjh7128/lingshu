# -*- coding: utf-8 -*-
"""Map Overfull \hbox warnings in a LaTeX log to source files / line ranges."""
import re
import sys
from collections import Counter

path = sys.argv[1]
lines = open(path, encoding="utf-8", errors="replace").read().split("\n")

stack = []
overfull = []
# Roughly track file open/close via parentheses (nested). TeX logs are messy but usable.
for ln in lines:
    # update file stack: look for "(./path" or "(path" tokens
    for m in re.finditer(r"\((?:\./)?((?:sections|sections-zh|tables|figures)/[^\s()]+?\.(?:tex|sty))", ln):
        stack.append(m.group(1))
    if "Overfull \\hbox" in ln:
        # find next lines for location
        overfull.append({"file": stack[-1] if stack else "?", "raw": ln.strip()})
    # pop roughly on ')' at line end
    if ln.count(")") > ln.count("(") and stack:
        stack.pop()

# enrich with following lines' location info
idx = 0
for i, ln in enumerate(lines):
    if "Overfull \\hbox" in ln:
        nxt = " ".join(lines[i:i + 3])
        m = re.search(r"(?:detected at line|in paragraph at lines)\s+(\d+)(?:--(\d+))?", nxt)
        if m:
            overfull[idx]["line"] = int(m.group(1))
            overfull[idx]["line_end"] = int(m.group(2) or m.group(1))
        idx += 1

# compact output counter by file, then list >=20pt
big = []
for o in overfull:
    m = re.search(r"\(([\d.]+)pt too wide\)", o["raw"])
    o["pt"] = float(m.group(1)) if m else 0.0
    if o["pt"] >= 10:
        big.append(o)

print("TOTAL overfull:", len(overfull))
print("\nBy file (all):")
for f, c in Counter(o["file"] for o in overfull).most_common():
    print(f"  {c:4d}  {f}")
print("\nBig overfull (>=10pt):")
for o in big:
    print(f"  {o['pt']:8.1f}pt  {o['file']}:{o.get('line','?')}  ({o['raw']})")
