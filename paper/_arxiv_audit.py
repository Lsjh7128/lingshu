# -*- coding: utf-8 -*-
"""Verify the eight camera-ready items against the compiled English/Chinese PDFs."""
import re
from pypdf import PdfReader

ITEMS = [
    ("1a", "prefeed", r"prefeed"),
    ("1b", "prefeed_t in F3", r"prefeedt"),
    ("2", "bigvee", r"\u2228"),
    ("3", "Bonferroni paren", r"\(6/12"),
    ("4", "50k tokens", r"50k tokens"),
    ("5", "non-allowlisted", r"non-allowlisted"),
    ("6", "patch loop dash", r"patch loop\s*[\u2014\u2013-]"),
    ("7", "blocked (fail-closed)", r"blocked\s*\(fail"),
    ("8", "engine-only", r"engine-\s*only"),
]


def audit(pdf):
    r = PdfReader(pdf)
    full = [(i, p.extract_text() or "") for i, p in enumerate(r.pages, 1)]
    print("==", pdf, "pages:", len(r.pages))
    for label, name, pat in ITEMS:
        hits = []
        for i, t in full:
            for m in re.finditer(pat, t, re.I):
                hits.append((i, t[max(0, m.start() - 45):m.start() + 75].replace("\n", " ")))
        print("%-4s %-22s hits=%-3d %s" % (label, name, len(hits), hits[0][1] if hits else "-"))
    subs = {}
    for p in r.pages:
        a = p.get("/Annots")
        if not a:
            continue
        for ref in a:
            try:
                obj = ref.get_object()
                st = obj.get("/Subtype")
                subs[str(st)] = subs.get(str(st), 0) + 1
            except Exception:
                pass
    print("annotation subtypes:", subs)
    print()


audit("main-en.pdf")
audit("main-zh.pdf")
