# -*- coding: utf-8 -*-
"""Independent structural verifier for the figure PDFs (pure standard library).

It re-parses the PDF that ``pdfcanvas.py`` wrote -- it does not trust the canvas
object -- and checks the properties the paper needs:

  1. header ``%PDF-1.4``, ``%%EOF`` trailer, ``startxref`` and a well-formed
     xref table whose offsets really point at ``N 0 obj``;
  2. exactly one page, correct ``/MediaBox``;
  3. vector-only content: no image XObjects, no inline images;
  4. every glyph is a text object with an explicit font and size >= 8 pt, all
     text inside the page box, printable ASCII only (base-14 Helvetica has no
     glyphs for anything else);
  5. no forbidden vocabulary (the three framing-only terms and the four frozen
     directions must not appear in the figure).

Exit code 0 = pass.  Any finding is printed and returns 1.

Usage
-----
    python verify_pdf.py fig1-architecture.pdf [fig2-retention.pdf ...]
"""
from __future__ import annotations

import os
import re
import sys

FORBIDDEN = [
    # framing-only terms that must not be presented as implemented machinery
    "vsm", "rate-distortion", "rate distortion", "cyber-cognitive",
    "cognitive envelope",
    # the four frozen directions
    "action loop", "adaptive evolution", "collective mind", "multi-agent",
    "multimodal",
]
MIN_FONT = 8.0
SOURCE_FONT = 6.0     # the one-line in-figure provenance may go down to 6 pt


def parse_pdf(path: str) -> dict:
    data = open(path, "rb").read()
    out = {"size": len(data), "problems": [], "raw": data}

    if not data.startswith(b"%PDF-1.4"):
        out["problems"].append("header is not %PDF-1.4")
    if not data.rstrip().endswith(b"%%EOF"):
        out["problems"].append("file does not end with %%EOF")

    m = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", data)
    if not m:
        out["problems"].append("missing startxref/%%EOF pair")
        return out
    xref_at = int(m.group(1))
    if xref_at >= len(data) or not data[xref_at:xref_at + 4] == b"xref":
        out["problems"].append("startxref does not point at 'xref'")
        return out

    # xref table
    head = data[xref_at:].split(b"\n", 2)
    if not head[0].startswith(b"xref"):
        out["problems"].append("xref header malformed")
    parts = data[xref_at:].split(b"trailer")
    table = parts[0].decode("latin-1")
    entries = re.findall(r"(\d{10}) (\d{5}) ([nf])", table)
    out["xref_entries"] = len(entries)
    trailer = parts[1].decode("latin-1") if len(parts) > 1 else ""
    if "/Root" not in trailer:
        out["problems"].append("trailer has no /Root")
    msize = re.search(r"/Size\s+(\d+)", trailer)
    if msize and int(msize.group(1)) != len(entries):
        out["problems"].append("trailer /Size %s != %d xref entries"
                               % (msize.group(1), len(entries)))

    # every in-use xref entry must point at "N 0 obj"
    for idx, (off, gen, kind) in enumerate(entries):
        if kind != "n":
            continue
        off = int(off)
        expect = b"%d 0 obj" % idx
        if off >= len(data) or not data[off:off + len(expect)] == expect:
            out["problems"].append("xref offset for object %d is wrong" % idx)

    # objects
    objs = {}
    for m in re.finditer(rb"(\d+) 0 obj(.*?)endobj", data, re.S):
        objs[int(m.group(1))] = m.group(2)
    out["n_objects"] = len(objs)

    pages = [n for n, b in objs.items() if b"/Type /Page " in b or b"/Type /Page>" in b]
    catalog = [n for n, b in objs.items() if b"/Type /Catalog" in b]
    out["n_pages"] = len(pages)
    out["n_catalog"] = len(catalog)
    if len(pages) != 1:
        out["problems"].append("expected 1 page, found %d" % len(pages))
    if not catalog:
        out["problems"].append("no /Type /Catalog")

    media = None
    if pages:
        mm = re.search(rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]",
                       objs[pages[0]])
        if mm:
            media = tuple(float(g) for g in mm.groups())
    out["mediabox"] = media
    if not media:
        out["problems"].append("no /MediaBox on the page")
        media = (0.0, 0.0, 0.0, 0.0)

    fonts = {}
    for n, b in objs.items():
        fm = re.search(rb"/BaseFont\s*/([A-Za-z0-9+\-]+)", b)
        if fm and b"/Type /Font" in b:
            fonts[n] = fm.group(1).decode()
    out["fonts"] = fonts
    if not fonts:
        out["problems"].append("no font resources found")

    # contents
    content = b""
    for n, b in objs.items():
        if b"/Length" in b and b"stream" in b and b"/Type" not in b:
            start = b.find(b"stream") + len(b"stream")
            if b[start:start + 1] == b"\n":
                start += 1
            end = b.rfind(b"endstream")
            content += b[start:end]
    out["content_bytes"] = len(content)
    if b"/Subtype /Image" in content or b"BI " in content:
        out["problems"].append("content contains a raster image (must be vector)")
    if b"Do" in content:
        # an XObject paint would be the only way a Do could appear
        out["problems"].append("content uses the Do operator (XObject)")

    out["content"] = content
    return out


KNOWN_OPS = {"m", "l", "c", "re", "f", "S", "B", "h", "w", "d",
             "g", "G", "rg", "RG", "BT", "ET", "Tf", "Tm", "Tj"}
ESC_STRING = r"\(((?:[^()\\]|\\.)*)\)"
# one text object, fully validated token by token (no stray operands allowed)
TEXT_OBJ = re.compile(
    r"^BT /F([12]) (-?[\d.]+) Tf (1 0 0 1|0 1 -1 0) (-?[\d.]+) (-?[\d.]+) Tm "
    + ESC_STRING + r" Tj ET$")


def check_text(info: dict) -> None:
    content = info["content"].decode("latin-1")
    W, H = info["mediabox"][2], info["mediabox"][3]

    # every operator must be one this generator is allowed to emit
    stripped = re.sub(r"\((?:\\.|[^()\\])*\)", "()", content)
    stripped = re.sub(r"/[A-Za-z0-9#]+", " ", stripped)   # drop PDF names (/F1)
    for tok in re.findall(r"[A-Za-z]+", stripped):
        if tok not in KNOWN_OPS:
            info["problems"].append("unknown operator %r in content stream" % tok)
            break

    blocks = re.findall(r"BT.*?ET", content, re.S)
    info["n_text_objects"] = len(blocks)
    sizes, outside, nonascii, strings, malformed = [], [], [], [], []
    for block in blocks:
        m = TEXT_OBJ.match(" ".join(block.split()))
        if not m:
            malformed.append(" ".join(block.split())[:90])
            continue
        font, size, matrix, tx, ty, s = m.groups()
        size = float(size)
        tx, ty = float(tx), float(ty)
        sizes.append(size)
        # 8 pt floor for figure text; the single provenance line may be 6 pt
        floor = SOURCE_FONT if s.startswith("source:") else MIN_FONT
        if size < floor - 1e-9:
            info["problems"].append("font %.2fpt < %.1fpt: %r" % (size, floor, s))
        strings.append(s)
        if any(ord(ch) > 126 for ch in s):
            nonascii.append(s)
        if not (-1.0 <= tx <= W + 1.0) or not (-1.0 <= ty <= H + 1.0):
            outside.append((s, tx, ty))
    info["font_sizes"] = sorted(set(sizes))
    info["strings"] = strings
    if malformed:
        info["problems"].append("malformed text object(s) (wrong operand count "
                                "or unknown form): %r" % (malformed[:2],))
    if outside:
        info["problems"].append("text baseline outside the page: %r" % (outside[:3],))
    if nonascii:
        info["problems"].append("non-ASCII text (base-14 Helvetica cannot render it): %r"
                                % (nonascii[:3],))
    joined = " ".join(strings).lower()
    for bad in FORBIDDEN:
        if bad in joined:
            info["problems"].append("forbidden term %r present in figure text" % bad)


def report(path: str) -> int:
    info = parse_pdf(path)
    check_text(info)
    ok = not info["problems"]
    print("=" * 78)
    print("%s | %d bytes | pages=%d | objects=%d | xref=%d"
          % (os.path.basename(path), info["size"], info["n_pages"],
             info["n_objects"], info["xref_entries"]))
    print("  MediaBox: %s  (%.2f x %.2f in)"
          % (tuple("%.1f" % v for v in info["mediabox"]),
             info["mediabox"][2] / 72.0, info["mediabox"][3] / 72.0))
    print("  fonts: %s" % info["fonts"])
    print("  text objects: %d | font sizes: %s | content bytes: %d"
          % (info["n_text_objects"], info["font_sizes"], info["content_bytes"]))
    print("  text strings: %d" % len(info["strings"]))
    if ok:
        print("  RESULT: PASS")
    else:
        print("  RESULT: FAIL")
        for p in info["problems"]:
            print("   -", p)
    return 0 if ok else 1


def main(argv: list) -> int:
    if not argv:
        here = os.path.dirname(os.path.abspath(__file__))
        argv = [os.path.join(here, n) for n in
                ("fig1-architecture.pdf", "fig2-retention.pdf")
                if os.path.exists(os.path.join(here, n))]
    rc = 0
    for p in argv:
        if not os.path.exists(p):
            print("missing:", p)
            rc = 1
            continue
        rc |= report(p)
    return rc


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
