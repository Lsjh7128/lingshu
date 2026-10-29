# -*- coding: utf-8 -*-
"""Allow line breaks inside long \\texttt{...} identifiers/paths."""
import glob
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))
SEP = re.compile(r"([/._:\-])")


def add_breaks(s):
    body = s
    # insert \allowbreak{} after separators, unless already followed by one
    parts = []
    i = 0
    for m in re.finditer(r"[/._:\-]", body):
        parts.append(body[i:m.end()])
        nxt = body[m.end():m.end() + 12]
        if not nxt.startswith("\\allowbreak"):
            parts.append("\\allowbreak{}")
        i = m.end()
    parts.append(body[i:])
    return "".join(parts)


def process(path):
    txt = open(path, encoding="utf-8").read()
    changed = 0

    def repl(m):
        nonlocal changed
        inner = m.group(1)
        if len(inner) < 18:
            return m.group(0)
        if "\\allowbreak" in inner:
            return m.group(0)
        if not re.search(r"[/._:\-]", inner):
            return m.group(0)
        changed += 1
        return "\\texttt{" + add_breaks(inner) + "}"

    out = re.sub(r"\\texttt\{([^{}]+)\}", repl, txt)
    if changed:
        open(path, "w", encoding="utf-8", newline="\n").write(out)
    return changed


total = 0
for d in ("sections", "sections-zh"):
    for f in sorted(glob.glob(os.path.join(ROOT, d, "*.tex"))):
        n = process(f)
        if n:
            print("%-40s %d" % (os.path.basename(f), n))
            total += n
print("total texttt wrapped:", total)
