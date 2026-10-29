import re
import sys
from pypdf import PdfReader

pdf = sys.argv[1]
r = PdfReader(pdf)
full = []
for i, page in enumerate(r.pages, 1):
    try:
        t = page.extract_text() or ""
    except Exception:
        t = ""
    full.append((i, t))

patterns = [
    r"preef", r"preeed", r"prefeed", r"bigvee", r"√", r"∨", r"allowedless",
    r"allowlisted", r"engineering mode", r"engine-only", r"50k", r"50 tokens",
    r"Bonferroni", r"patch loop", r"fail-closed", r"T-UCA", r"undecidable",
    r"verdict = block", r"iff",
]

for pat in patterns:
    hits = [(i, m.start()) for i, t in full for m in re.finditer(pat, t, re.I)]
    print("PATTERN %-18s hits=%d" % (pat, len(hits)))
    for i, pos in hits[:3]:
        t = full[i - 1][1]
        print("   p%-3d ...%s..." % (i, t[max(0, pos - 60):pos + 80].replace("\n", " ")))

print("\n=== pages 3,7,10,11,17,19,26 key lines ===")
for i, t in full:
    if i in (3, 7, 10, 11, 17, 19, 26):
        print("\n--- page", i, "---")
        print(t[:1200])
