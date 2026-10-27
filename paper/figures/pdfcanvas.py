# -*- coding: utf-8 -*-
"""Minimal vector-PDF canvas (pure Python standard library only).

Why this file exists
--------------------
The paper toolchain on the build machine has no matplotlib (pip/PyPI is blocked:
the mirror hangs, plain HTTP returns 403, only raw TLS sockets work).  These
figures must be **vector PDF** anyway, so this module writes PDF 1.4 directly.
No bitmaps, no raster fallback: every glyph is a text object, every stroke is a
path operator.

Design constraints
------------------
* PDF 1.4, single page, uncompressed content stream, valid xref table.
* Base-14 fonts only (/Helvetica, /Helvetica-Bold, WinAnsiEncoding).  The
  standard 14 fonts are part of every PDF reader; they carry no font program,
  so no embedding step is possible or required.  Helvetica advance widths are
  tabulated below (AFM, 1/1000 em) so that every piece of text can be measured,
  wrapped and centred exactly before it is emitted.
* Coordinates are given **y-down** (like a normal drawing canvas); they are
  flipped to PDF's y-up space here.  Units are PostScript points (1/72 in).
* Grayscale only (`g` / `G`), so the figure stays legible when printed in B/W.
* Deterministic output: no timestamp, no randomness, no object ordering churn.

Primitives
----------
line / rect / round rect / polygon / circle / dashed line / arrow / text
(text supports left, centre and right alignment, automatic width measurement
and word wrapping).

A PNG preview backend (Pillow, if importable) is included **for layout
checking only**; it never touches the deliverable PDF.
"""
from __future__ import annotations

# ── Helvetica advance widths, 1/1000 em, ASCII 32..126 (Adobe AFM) ──────────
_HELV = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,   # 32-47
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556,                                # 48-57
    278, 278, 584, 584, 584, 556, 1015,                                              # 58-64
    667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667,
    778, 722, 667, 611, 722, 667, 944, 667, 667, 611,                                # 65-90
    278, 278, 278, 469, 556, 333,                                                    # 91-96
    556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556,
    556, 333, 500, 278, 556, 500, 722, 500, 500, 500,                                # 97-122
    334, 260, 334, 584,                                                              # 123-126
]
# ── Helvetica-Bold advance widths (Adobe AFM) ──────────────────────────────
_HELV_B = [
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
    333, 333, 584, 584, 584, 611, 975,
    722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667,
    778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
    333, 278, 333, 584, 556, 333,
    556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611,
    611, 389, 556, 333, 611, 556, 778, 556, 556, 500,
    389, 280, 389, 584,
]

assert len(_HELV) == 95 and len(_HELV_B) == 95

#: every character this canvas can emit (Helvetica is used with WinAnsiEncoding;
#: we restrict ourselves to printable ASCII so that codes == Unicode < 127).
PRINTABLE = set(chr(c) for c in range(32, 127))

FONT_REGULAR = "F1"      # /Helvetica
FONT_BOLD = "F2"         # /Helvetica-Bold


# ── text metrics ───────────────────────────────────────────────────────────
def char_width(ch: str, size: float, bold: bool = False) -> float:
    """Advance width of one character, in points."""
    code = ord(ch)
    if not 32 <= code <= 126:
        raise ValueError("non-ASCII character %r (base-14 Helvetica only)" % ch)
    return (_HELV_B if bold else _HELV)[code - 32] * size / 1000.0


def text_width(s: str, size: float, bold: bool = False) -> float:
    """Advance width of a string, in points."""
    return sum(char_width(ch, size, bold) for ch in s)


def wrap_text(s: str, max_width: float, size: float, bold: bool = False) -> list:
    """Greedy word wrap.  Returns a list of lines, each <= max_width if possible."""
    words = s.split()
    if not words:
        return [""]
    lines, cur = [], words[0]
    for w in words[1:]:
        trial = cur + " " + w
        if text_width(trial, size, bold) <= max_width:
            cur = trial
        else:
            lines.append(cur)
            cur = w
    lines.append(cur)
    return lines


def wrap_max_width(lines: list, size: float, bold: bool = False) -> float:
    return max((text_width(l, size, bold) for l in lines), default=0.0)


# ── canvas ─────────────────────────────────────────────────────────────────
class Canvas:
    """Collects drawing ops, then serialises them to a one-page vector PDF."""

    def __init__(self, width_pt: float, height_pt: float) -> None:
        self.w = float(width_pt)
        self.h = float(height_pt)
        self.ops: list = []

    # -- coordinate helpers ------------------------------------------------
    def _fy(self, y: float) -> float:
        return self.h - y

    def _n(self, v: float) -> str:
        s = "%.3f" % v
        s = s.rstrip("0").rstrip(".")
        return "0" if s in ("-0", "") else s

    # -- primitives --------------------------------------------------------
    def line(self, x1, y1, x2, y2, color=0.0, width=0.7, dash=None):
        self.ops.append(("line", (x1, y1, x2, y2), color, width, dash))

    def dashed(self, x1, y1, x2, y2, on=3.0, off=2.0, color=0.0, width=0.7):
        self.line(x1, y1, x2, y2, color=color, width=width, dash=(on, off))

    def rect(self, x, y, w, h, fill=None, stroke=0.0, width=0.7, dash=None):
        self.ops.append(("rect", (x, y, w, h), fill, stroke, width, dash))

    def roundrect(self, x, y, w, h, fill=None, stroke=0.0, width=0.7, radius=2.0, dash=None):
        self.ops.append(("roundrect", (x, y, w, h), fill, stroke, width, dash, radius))

    def polygon(self, points, fill=None, stroke=0.0, width=0.7):
        self.ops.append(("poly", list(points), fill, stroke, width))

    def circle(self, cx, cy, r, fill=None, stroke=0.0, width=0.7):
        self.ops.append(("circle", (cx, cy, r), fill, stroke, width))

    def arrow(self, x1, y1, x2, y2, color=0.0, width=0.8, dash=None,
              head_len=4.5, head_w=2.2):
        """Straight line with a filled triangular head at (x2, y2)."""
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5
        if length < 1e-6:
            return
        ux, uy = dx / length, dy / length
        bx, by = x2 - ux * min(head_len, length * 0.6), y2 - uy * min(head_len, length * 0.6)
        self.line(x1, y1, bx, by, color=color, width=width, dash=dash)
        px, py = -uy, ux
        hw = head_w
        self.polygon([(x2, y2), (bx + px * hw, by + py * hw), (bx - px * hw, by - py * hw)],
                     fill=color, stroke=None, width=0.0)

    def orth_arrow(self, pts, color=0.0, width=0.7, dash=None,
                   head_len=4.5, head_w=2.2):
        """Poly-line arrow: segments through pts, head on the last segment."""
        pts = [(float(a), float(b)) for a, b in pts]
        if len(pts) < 2:
            return
        (x1, y1), (x2, y2) = pts[-2], pts[-1]
        dx, dy = x2 - x1, y2 - y1
        length = (dx * dx + dy * dy) ** 0.5
        if length < 1e-6:
            return
        ux, uy = dx / length, dy / length
        bx, by = x2 - ux * min(head_len, length * 0.6), y2 - uy * min(head_len, length * 0.6)
        for i in range(len(pts) - 1):
            a, b = pts[i], pts[i + 1]
            self.line(a[0], a[1], b[0], b[1], color=color, width=width, dash=dash)
        px, py = -uy, ux
        self.polygon([(x2, y2), (bx + px * head_w, by + py * head_w),
                      (bx - px * head_w, by - py * head_w)], fill=color, stroke=None, width=0.0)

    def text(self, x, y, s, size=8.0, bold=False, align="left", color=0.0,
             valign="center", rotate=0):
        """Draw one line of text.

        ``x`` is the anchor for ``align``; ``y`` is the vertical **centre** of the
        line box by default (``valign='center'``) or the baseline
        (``valign='baseline'``).  ``rotate=90`` turns the text to read bottom-to-
        top; the anchor is then the *start* of the baseline (bottom of the text).
        """
        for ch in s:
            if ch not in PRINTABLE:
                raise ValueError("non-printable character %r in %r" % (ch, s))
        if rotate not in (0, 90):
            raise ValueError("only rotate=0 or rotate=90 is supported")
        self.ops.append(("text", (x, y), s, size, bold, align, color, valign, rotate))

    def text_lines(self, x, y, lines, size=8.0, bold=False, align="left",
                   color=0.0, leading=None):
        """Draw several lines, ``y`` = vertical centre of the whole block."""
        leading = leading if leading is not None else size * 1.22
        n = len(lines)
        y0 = y - (n - 1) * leading / 2.0
        for i, ln in enumerate(lines):
            self.text(x, y0 + i * leading, ln, size=size, bold=bold,
                      align=align, color=color)

    # -- PDF serialisation -------------------------------------------------
    def _op_stream(self) -> str:
        out = []
        cur_fill = object()    # sentinel: no colour set yet
        cur_stroke = object()
        cur_w = None
        cur_dash = None

        def color_op(v, stroking):
            """Emit a greyscale (``g``/``G``) or RGB (``rg``/``RG``) colour op."""
            if isinstance(v, (tuple, list)):
                r, g, b = v
                return "%s %s %s %s" % (self._n(r), self._n(g), self._n(b),
                                        "RG" if stroking else "rg")
            return "%s %s" % (self._n(v), "G" if stroking else "g")

        def set_fill(v):
            nonlocal cur_fill
            if v != cur_fill:
                out.append(color_op(v, False))
                cur_fill = v

        def set_stroke(v):
            nonlocal cur_stroke
            if v is not None and v != cur_stroke:
                out.append(color_op(v, True))
                cur_stroke = v

        def set_width(v):
            nonlocal cur_w
            if v != cur_w:
                out.append("%s w" % self._n(v))
                cur_w = v

        def set_dash(d):
            nonlocal cur_dash
            if d != cur_dash:
                if d is None:
                    out.append("[] 0 d")
                else:
                    out.append("[%s %s] 0 d" % (self._n(d[0]), self._n(d[1])))
                cur_dash = d

        def paint(fill, stroke):
            if fill is not None and stroke is not None:
                out.append("B")
            elif fill is not None:
                out.append("f")
            else:
                out.append("S")

        for op in self.ops:
            kind = op[0]
            if kind == "line":
                _, (x1, y1, x2, y2), color, width, dash = op
                set_stroke(color)
                set_width(width)
                set_dash(dash)
                out.append("%s %s m %s %s l S" % (self._n(x1), self._n(self._fy(y1)),
                                                  self._n(x2), self._n(self._fy(y2))))
            elif kind == "rect":
                _, (x, y, w, h), fill, stroke, width, dash = op
                set_fill(fill if fill is not None else 1.0)
                if stroke is not None:
                    set_stroke(stroke)
                    set_width(width)
                    set_dash(dash)
                out.append("%s %s %s %s re" % (self._n(x), self._n(self._fy(y + h)),
                                               self._n(w), self._n(h)))
                paint(fill, stroke)
            elif kind == "roundrect":
                _, (x, y, w, h), fill, stroke, width, dash, r = op
                r = min(r, w / 2.0, h / 2.0)
                k = 0.5523 * r
                x0, y0, x1, y1 = x, self._fy(y + h), x + w, self._fy(y)
                set_fill(fill if fill is not None else 1.0)
                if stroke is not None:
                    set_stroke(stroke)
                    set_width(width)
                    set_dash(dash)
                p = []
                p.append("%s %s m" % (self._n(x0 + r), self._n(y1)))
                p.append("%s %s l" % (self._n(x1 - r), self._n(y1)))
                p.append("%s %s %s %s %s %s c" % (self._n(x1 - r + k), self._n(y1),
                                                  self._n(x1), self._n(y1 - r + k),
                                                  self._n(x1), self._n(y1 - r)))
                p.append("%s %s l" % (self._n(x1), self._n(y0 + r)))
                p.append("%s %s %s %s %s %s c" % (self._n(x1), self._n(y0 + r - k),
                                                  self._n(x1 - r + k), self._n(y0),
                                                  self._n(x1 - r), self._n(y0)))
                p.append("%s %s l" % (self._n(x0 + r), self._n(y0)))
                p.append("%s %s %s %s %s %s c" % (self._n(x0 + r - k), self._n(y0),
                                                  self._n(x0), self._n(y0 + r - k),
                                                  self._n(x0), self._n(y0 + r)))
                p.append("%s %s l" % (self._n(x0), self._n(y1 - r)))
                p.append("%s %s %s %s %s %s c" % (self._n(x0), self._n(y1 - r + k),
                                                  self._n(x0 + r - k), self._n(y1),
                                                  self._n(x0 + r), self._n(y1)))
                p.append("h")
                out.append(" ".join(p))
                paint(fill, stroke)
            elif kind == "poly":
                _, points, fill, stroke, width = op
                set_fill(fill if fill is not None else 1.0)
                if stroke is not None:
                    set_stroke(stroke)
                    set_width(width)
                    set_dash(None)
                p = ["%s %s m" % (self._n(points[0][0]), self._n(self._fy(points[0][1])))]
                for (px, py) in points[1:]:
                    p.append("%s %s l" % (self._n(px), self._n(self._fy(py))))
                p.append("h")
                out.append(" ".join(p))
                paint(fill, stroke)
            elif kind == "circle":
                _, (cx, cy, r), fill, stroke, width = op
                k = 0.5523 * r
                cyf = self._fy(cy)
                set_fill(fill if fill is not None else 1.0)
                if stroke is not None:
                    set_stroke(stroke)
                    set_width(width)
                    set_dash(None)
                out.append("%s %s m" % (self._n(cx + r), self._n(cyf)))
                out.append("%s %s %s %s %s %s c" % (self._n(cx + r), self._n(cyf + k),
                                                    self._n(cx + k), self._n(cyf + r),
                                                    self._n(cx), self._n(cyf + r)))
                out.append("%s %s %s %s %s %s c" % (self._n(cx - k), self._n(cyf + r),
                                                    self._n(cx - r), self._n(cyf + k),
                                                    self._n(cx - r), self._n(cyf)))
                out.append("%s %s %s %s %s %s c" % (self._n(cx - r), self._n(cyf - k),
                                                    self._n(cx - k), self._n(cyf - r),
                                                    self._n(cx), self._n(cyf - r)))
                out.append("%s %s %s %s %s %s c" % (self._n(cx + k), self._n(cyf - r),
                                                    self._n(cx + r), self._n(cyf - k),
                                                    self._n(cx + r), self._n(cyf)))
                out.append("h")
                paint(fill, stroke)
            elif kind == "text":
                _, (x, y), s, size, bold, align, color, valign, rotate = op
                w = text_width(s, size, bold)
                if align == "center":
                    x -= w / 2.0
                elif align == "right":
                    x -= w
                set_fill(color)
                set_stroke(None)
                font = FONT_BOLD if bold else FONT_REGULAR
                if rotate == 90:
                    # baseline runs bottom-to-top at x; y is the baseline start
                    fy = self._fy(y)
                    out.append("BT /%s %s Tf 0 1 -1 0 %s %s Tm (%s) Tj ET"
                               % (font, self._n(size), self._n(x), self._n(fy),
                                  escape_string(s)))
                    continue
                if valign == "center":
                    y = y + size * 0.36      # centre of x-height band
                fy = self._fy(y)
                out.append("BT /%s %s Tf 1 0 0 1 %s %s Tm (%s) Tj ET"
                           % (font, self._n(size), self._n(x), self._n(fy),
                              escape_string(s)))
            else:  # pragma: no cover
                raise AssertionError("unknown op %r" % (kind,))
        return "\n".join(out) + "\n"

    def to_pdf(self, path: str, title: str = "", subject: str = "") -> str:
        """Write a single-page PDF; returns the path."""
        content = self._op_stream().encode("latin-1")
        objs = {}
        objs[1] = b"<< /Type /Catalog /Pages 2 0 R >>"
        objs[2] = b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>"
        objs[3] = ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] "
                   "/Resources << /Font << /F1 5 0 R /F2 6 0 R >> "
                   "/ProcSet [/PDF /Text] >> /Contents 4 0 R >>"
                   % (self._n(self.w), self._n(self.h))).encode("latin-1")
        objs[4] = b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream"
        objs[5] = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
                   b"/Encoding /WinAnsiEncoding >>")
        objs[6] = (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
                   b"/Encoding /WinAnsiEncoding >>")
        info = b"<< /Producer (LINGSHU paper figure generator: pdfcanvas.py) "
        if title:
            info += b"/Title (" + escape_string(title).encode("latin-1") + b") "
        if subject:
            info += b"/Subject (" + escape_string(subject).encode("latin-1") + b") "
        info += b">>"
        objs[7] = info

        out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
        offsets = {}
        for num in sorted(objs):
            offsets[num] = len(out)
            out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
        xref_at = len(out)
        count = max(objs) + 1
        out += b"xref\n0 %d\n" % count
        out += b"0000000000 65535 f \n"
        for num in range(1, count):
            out += b"%010d 00000 n \n" % offsets[num]
        out += (b"trailer\n<< /Size %d /Root 1 0 R /Info 7 0 R >>\n" % count)
        out += b"startxref\n%d\n%%%%EOF\n" % xref_at
        with open(path, "wb") as fh:
            fh.write(bytes(out))
        return path

    # -- PNG preview (layout checking only; never a deliverable) -----------
    def to_png(self, path: str, scale: int = 4, font_path: str = r"C:\Windows\Fonts\arial.ttf",
               font_bold_path: str = r"C:\Windows\Fonts\arialbd.ttf") -> str:
        from PIL import Image, ImageDraw, ImageFont  # preview only

        W, H = int(self.w * scale), int(self.h * scale)
        img = Image.new("RGB", (W, H), (255, 255, 255))
        d = ImageDraw.Draw(img)

        def px(v):
            return int(round(v * scale))

        def col(c):
            # PDF colour: 0 = black, 1 = white; a 3-tuple is already RGB
            if c is None:
                c = 0.0
            if isinstance(c, (tuple, list)):
                r, g, b = c
            else:
                r = g = b = c
            return tuple(max(0, min(255, int(round(255 * v)))) for v in (r, g, b))

        fonts = {}

        def font(size, bold):
            key = (round(size, 2), bold)
            if key not in fonts:
                px_size = max(1, int(round(size * scale)))
                fonts[key] = ImageFont.truetype(font_bold_path if bold else font_path, px_size)
            return fonts[key]

        for op in self.ops:
            kind = op[0]
            if kind == "line":
                _, (x1, y1, x2, y2), color, width, dash = op
                wd = max(1, px(width))
                if dash:
                    on, off = px(dash[0]), px(dash[1])
                    total = ((x2 - x1) ** 2 + (y2 - y1) ** 2) ** 0.5 * scale
                    t = 0.0
                    while t < total:
                        t2 = min(t + on, total)
                        f1, f2 = t / total, t2 / total
                        d.line([px(x1 + (x2 - x1) * f1), px(y1 + (y2 - y1) * f1),
                                px(x1 + (x2 - x1) * f2), px(y1 + (y2 - y1) * f2)],
                               fill=col(color), width=wd)
                        t += on + off
                else:
                    d.line([px(x1), px(y1), px(x2), px(y2)], fill=col(color), width=wd)
            elif kind == "rect":
                _, (x, y, w, h), fill, stroke, width, dash = op
                if fill is not None:
                    d.rectangle([px(x), px(y), px(x + w), px(y + h)], fill=col(fill))
                if stroke is not None:
                    d.rectangle([px(x), px(y), px(x + w), px(y + h)],
                                outline=col(stroke), width=max(1, px(width)))
            elif kind == "roundrect":
                _, (x, y, w, h), fill, stroke, width, dash, r = op
                d.rounded_rectangle([px(x), px(y), px(x + w), px(y + h)], radius=px(r),
                                    fill=col(fill) if fill is not None else None,
                                    outline=col(stroke) if stroke is not None else None,
                                    width=max(1, px(width)) if stroke is not None else 0)
            elif kind == "poly":
                _, points, fill, stroke, width = op
                pts = [(px(a), px(b)) for a, b in points]
                if fill is not None:
                    d.polygon(pts, fill=col(fill))
                if stroke is not None:
                    d.line(pts + [pts[0]], fill=col(stroke), width=max(1, px(width)))
            elif kind == "circle":
                _, (cx, cy, r), fill, stroke, width = op
                d.ellipse([px(cx - r), px(cy - r), px(cx + r), px(cy + r)],
                          fill=col(fill) if fill is not None else None,
                          outline=col(stroke) if stroke is not None else None,
                          width=max(1, px(width)) if stroke is not None else 0)
            elif kind == "text":
                _, (x, y), s, size, bold, align, color, valign, rotate = op
                w = text_width(s, size, bold)
                if align == "center":
                    x -= w / 2.0
                elif align == "right":
                    x -= w
                if rotate == 90:
                    tw = max(2, int(round(w * scale)) + 2)
                    th = max(2, int(round(size * scale * 1.45)) + 2)
                    tile = Image.new("RGB", (tw, th), (255, 255, 255))
                    td = ImageDraw.Draw(tile)
                    td.text((1, th - int(round(size * scale * 0.28))), s,
                            font=font(size, bold), fill=col(color), anchor="ls")
                    tile = tile.rotate(90, expand=True)   # reads bottom-to-top
                    img.paste(tile, (px(x), px(y) - tile.size[1]))
                    continue
                ay = y + (size * 0.36 if valign == "center" else 0.0)
                d.text((px(x), px(ay)), s, font=font(size, bold), fill=col(color),
                       anchor="ls")
        img.save(path)
        return path


def escape_string(s: str) -> str:
    return s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")


# ── small typographic helpers used by both figure scripts ─────────────────
def box_text(c: Canvas, x, y, w, h, lines, size=8.0, title_bold=True,
             leading=None, color=0.0, pad=2.5):
    """Draw a centred, auto-wrapped paragraph inside a box.

    ``lines`` is a list of logical lines; the first one is drawn bold when
    ``title_bold`` is set.  Every logical line is wrapped to the box width.
    """
    leading = leading if leading is not None else size * 1.24
    wrapped = []
    for i, ln in enumerate(lines):
        bold = title_bold and i == 0
        parts = wrap_text(ln, w - 2 * pad, size, bold)
        for j, p in enumerate(parts):
            wrapped.append((p, bold if (title_bold and i == 0 and j == 0) else False))
    total_h = len(wrapped) * leading
    y0 = y + h / 2.0 - total_h / 2.0 + leading / 2.0
    for i, (s, bold) in enumerate(wrapped):
        c.text(x + w / 2.0, y0 + i * leading, s, size=size, bold=bold,
               align="center", color=color)


def chip(c: Canvas, x, y, w, h, lines, size=8.0, fill=None, stroke=0.0,
         width=0.7, color=0.0, pad=2.0, radius=1.5):
    """Small rounded label chip with centred wrapped text."""
    c.roundrect(x, y, w, h, fill=fill, stroke=stroke, width=width, radius=radius)
    leading = size * 1.2
    wrapped = []
    for ln in lines:
        wrapped.extend(wrap_text(ln, w - 2 * pad, size, False))
    total_h = len(wrapped) * leading
    y0 = y + h / 2.0 - total_h / 2.0 + leading / 2.0
    for i, s in enumerate(wrapped):
        c.text(x + w / 2.0, y0 + i * leading, s, size=size, align="center", color=color)
