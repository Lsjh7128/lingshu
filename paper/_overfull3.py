import re
import sys

path = sys.argv[1]
minpt = float(sys.argv[2]) if len(sys.argv) > 2 else 8.0
lines = open(path, encoding="utf-8", errors="replace").read().split("\n")

cur = "?"
out = []
for i, ln in enumerate(lines):
    for m in re.finditer(r"\(\.?/?(sections(?:-zh)?/[^\s()]+\.tex)", ln):
        cur = m.group(1)
    if ln.startswith("Overfull \\hbox"):
        m = re.search(r"\(([\d.]+)pt too wide", ln)
        pt = float(m.group(1)) if m else 0.0
        if pt >= minpt:
            nxt = " ".join(lines[i:i + 3])
            loc = re.search(r"(detected at line|in paragraph at lines)\s+(\d+)", nxt)
            content = ""
            for j in range(i + 1, min(i + 7, len(lines))):
                if lines[j].strip() and not lines[j].startswith("Overfull"):
                    content = lines[j].strip()[:70]
                    break
            out.append((pt, cur, loc.group(2) if loc else "?", content))

print("count(>=%.0fpt): %d" % (minpt, len(out)))
for pt, cur, line, content in sorted(out, reverse=True):
    print("%7.1fpt  %-32s line=%-5s  %s" % (pt, cur, line, content))
