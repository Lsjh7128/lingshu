# -*- coding: utf-8 -*-
"""Fig. 1 -- control-plane / data-plane separation of the LINGSHU turn pipeline.

Vector-only output (PDF 1.4, base-14 Helvetica) drawn with ``pdfcanvas.py``.
Every element is traceable to a document:

* eight-layer priority   -> ``docs/00-架构总览.md`` sections 1-3
* mechanism -> module    -> ``docs/19-控制论重构契约.md`` section 1 (rows 1-4)
* 0.70 / 0.85 thresholds -> ``docs/19`` section 2.3
* integral feedback      -> ``docs/19`` section 2.2
* requisite-variety      -> ``docs/19`` sections 2.4 and 7 (v5.1 wiring)
* U-shape injection      -> ``docs/19`` section 2.5
* stage order            -> ``engine/pipeline.py`` ``PipelineSession.step`` (steps 1-6)
* fail-closed ownership  -> ``docs/00`` section 3 (single fail-closed site)
* host hooks             -> ``docs/18-AstrBot协同.md`` lines 15-17, ``plugin/README.md`` 124-126

The diagram carries **no** data series and **no** forward-looking boxes: it is a
structure diagram only.

Usage
-----
    python fig1_architecture.py              # -> fig1-architecture.pdf
    python fig1_architecture.py --preview    # + fig1-architecture.preview.png (layout check)
"""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pdfcanvas as pc
from pdfcanvas import Canvas, box_text

# ── geometry (points, y-down; page = 7.00 x 4.75 in) ───────────────────────
W, H = 504.0, 342.0

BAND_X, BAND_W = 8.0, 488.0

# control plane
CP_Y, CP_H = 2.0, 96.0
CBOX_Y, CBOX_H, CBOX_W = 18.0, 56.0, 95.0
CBOX_X = [8.0, 106.0, 204.0, 302.0, 400.0]
CBOX_CX = [x + CBOX_W / 2.0 for x in CBOX_X]          # 55.5 153.5 251.5 349.5 447.5

# data plane
DP_Y, DP_H = 110.0, 76.0
SBOX_Y, SBOX_H, SBOX_W = 122.0, 54.0, 71.0
SBOX_X = [14.0, 96.0, 178.0, 260.0, 342.0, 424.0]
CHAIN_Y = SBOX_Y + SBOX_H / 2.0                        # 149

# insertion points (tap x on the data path)
TAP_U = 172.5      # after recall            (s1 | s2)
TAP_I = 213.5      # composition, style      (s2 centre)
TAP_V = 254.5      # after composition       (s2 | s3)
TAP_A = 336.5      # after render            (s3 | s4)
TAP_G = 377.5      # before the model call   (s4 centre)

# routed control connectors: distinct horizontal levels, crossing-free
# (requisite-variety needs no jog: its tap sits under the module)
JOG_I, JOG_G, JOG_U = 82.0, 88.0, 94.0

# sensor lane (host-reported context usage: model context -> phase monitor)
SENSOR_X, SENSOR_LANE = 498.0, 12.0

DARK, GREY = 0.20, 0.40


def zone_label(c: Canvas, x: float, y: float, title: str, sub: str) -> None:
    c.text(x, y, title, size=8.5, bold=True)
    if sub:
        c.text(x + pc.text_width(title, 8.5, True) + 6.0, y, sub, size=8.0, color=0.25)


def panel(c: Canvas, x, y, w, h, lines, fill=1.0, width=0.8, dash=None,
          leading=9.2):
    c.rect(x, y, w, h, fill=fill, stroke=0.0, width=width, dash=dash)
    box_text(c, x, y, w, h, lines, size=8.0, title_bold=True, pad=6.0,
             leading=leading)


def fit_problems(lines, w, h, size=8.0, pad_x=6.0, pad_y=3.0, leading=9.2,
                 title_bold=True, tag="box") -> list:
    """Assert that a label fits its box with the requested margins."""
    out = []
    wrapped = []
    for i, ln in enumerate(lines):
        bold = title_bold and i == 0
        for j, part in enumerate(pc.wrap_text(ln, w - 2 * pad_x, size, bold)):
            wrapped.append((part, bold and j == 0))
    for s, bold in wrapped:
        if pc.text_width(s, size, bold) > w - 2 * pad_x + 0.01:
            out.append("%s: line %r is wider than the box" % (tag, s))
    if len(wrapped) * leading > h - 2 * pad_y:
        out.append("%s: %d lines (%.1fpt) do not fit %.1fpt of box height"
                   % (tag, len(wrapped), len(wrapped) * leading, h - 2 * pad_y))
    return out


def build():
    c = Canvas(W, H)
    problems = []

    # ══ 1. control plane ══════════════════════════════════════════════════
    c.rect(BAND_X, CP_Y, BAND_W, CP_H, fill=0.965, stroke=0.45, width=0.6)
    zone_label(c, 14, 13, "CONTROL PLANE",
               "per-turn control actions; all degrade open except the gate")

    ctrl = [
        ("U-shape injection",
         ["strongest first and last", "weakest in the middle",
          "reorders injection only"]),
        ("Integral feedback",
         ["e = -deviation", "I += Ki * e * dt", "deadband, anti-windup",
          "leak; style only"]),
        ("Requisite-variety",
         ["unresolved conflict", "drops style domain", "fail-open, traced"]),
        ("Phase-shift monitor",
         ["ratio = used / window", "0.70 shift", "0.85 critical",
          "anchor head and tail"]),
        ("STPA UCA gate",
         ["UCA-01..08 preflight", "verdict allow / block",
          "only fail-closed site"]),
    ]
    for i, (title, detail) in enumerate(ctrl):
        gate = (i == 4)
        c.rect(CBOX_X[i], CBOX_Y, CBOX_W, CBOX_H,
               fill=(0.86 if gate else 1.0), stroke=0.0,
               width=(1.8 if gate else 0.9))
        box_text(c, CBOX_X[i], CBOX_Y, CBOX_W, CBOX_H, [title] + detail,
                 size=8.0, title_bold=True, pad=6.0, leading=9.2)
        problems += fit_problems([title] + detail, CBOX_W, CBOX_H, tag=title)

    # fail-closed marker: black square + white "!" on the gate box
    mx, my = CBOX_X[4] + 5.0, CBOX_Y + 5.0
    c.rect(mx, my, 6.5, 6.5, fill=0.0, stroke=None)
    c.text(mx + 3.25, my + 3.25, "!", size=8.0, bold=True, align="center", color=1.0)

    # ══ 2. control connectors (vertical drops, routed at distinct levels) ═
    def routed(src_x, tap_x, jog_y, width=0.75):
        c.orth_arrow([(src_x, CBOX_Y + CBOX_H), (src_x, jog_y),
                      (tap_x, jog_y), (tap_x, SBOX_Y)],
                     color=0.0, width=width, dash=(3.2, 2.0),
                     head_len=4.0, head_w=2.0)

    routed(CBOX_CX[0], TAP_U, JOG_U)
    routed(CBOX_CX[1], TAP_I, JOG_I)
    c.arrow(TAP_V, CBOX_Y + CBOX_H, TAP_V, SBOX_Y, color=0.0, width=0.75,
            dash=(3.2, 2.0), head_len=4.0, head_w=2.0)
    # phase monitor -> persona anchor, straight vertical drop
    c.arrow(TAP_A, CBOX_Y + CBOX_H, TAP_A, SBOX_Y, color=0.0, width=0.75,
            dash=(3.2, 2.0), head_len=4.0, head_w=2.0)
    # STPA gate -> preflight audit (thick: the fail-closed control path)
    routed(CBOX_CX[4], TAP_G, JOG_G, width=1.8)

    # ══ 3. sensor: host-reported context usage (model context -> monitor) ═
    c.orth_arrow([(SBOX_X[5] + SBOX_W, CHAIN_Y), (SENSOR_X, CHAIN_Y),
                  (SENSOR_X, SENSOR_LANE), (CBOX_CX[3], SENSOR_LANE),
                  (CBOX_CX[3], CBOX_Y)],
                 color=0.32, width=0.7, dash=(1.2, 2.6), head_len=3.4, head_w=1.7)
    c.circle(SBOX_X[5] + SBOX_W, CHAIN_Y, 1.2, fill=0.32, stroke=None)
    c.text(CBOX_CX[3] - 7.0, 7.0, "context usage (used / window)", size=8.0,
           align="right", color=0.32)

    # ══ 4. data plane ═════════════════════════════════════════════════════
    c.rect(BAND_X, DP_Y, BAND_W, DP_H, fill=0.985, stroke=0.45, width=0.6)
    zone_label(c, 14, 116.5, "DATA PLANE", "one turn, in order")

    stages = [
        ("User input", ["current message"], "external"),
        ("Memory recall", ["budgeted top-k"], "inner"),
        ("Composition", ["5 domains,", "8 mutex rules"], "inner"),
        ("Render", ["assemble blocks"], "inner"),
        ("Preflight audit", ["UCA-01..08", "fail-closed"], "audit"),
        ("Model", ["external"], "external"),
    ]
    for i, (title, sub, kind) in enumerate(stages):
        x = SBOX_X[i]
        if kind == "external":
            panel(c, x, SBOX_Y, SBOX_W, SBOX_H, [title] + sub, dash=(2.4, 1.7))
        elif kind == "audit":
            c.rect(x, SBOX_Y, SBOX_W, SBOX_H, fill=0.90, stroke=0.0, width=1.8)
            c.rect(x + 2.3, SBOX_Y + 2.3, SBOX_W - 4.6, SBOX_H - 4.6,
                   fill=None, stroke=0.0, width=0.5)
            box_text(c, x, SBOX_Y, SBOX_W, SBOX_H, [title] + sub, size=8.0,
                     title_bold=True, pad=6.0, leading=9.2)
        else:
            panel(c, x, SBOX_Y, SBOX_W, SBOX_H, [title] + sub)
        problems += fit_problems([title] + sub, SBOX_W, SBOX_H, tag=title)

    for i in range(len(SBOX_X) - 1):
        c.arrow(SBOX_X[i] + SBOX_W + 0.8, CHAIN_Y, SBOX_X[i + 1] - 0.8, CHAIN_Y,
                color=0.0, width=0.9, head_len=4.0, head_w=2.0)

    # insertion-point tags: centred on the drop line, masked out of the dashes
    for tag, x in (("T1", TAP_U), ("T2", TAP_I), ("T3", TAP_V),
                   ("T4", TAP_A), ("T5", TAP_G)):
        c.circle(x, DP_Y, 1.1, fill=0.0, stroke=None)
        tw = pc.text_width(tag, 8.0, True)
        c.rect(x - tw / 2.0 - 1.6, 99.6, tw + 3.2, 8.8, fill=1.0, stroke=None)
        c.text(x, 104.0, tag, size=8.0, bold=True, align="center", color=0.10)

    # ══ 5. fail-closed side exit ══════════════════════════════════════════
    c.arrow(TAP_G, SBOX_Y + SBOX_H, TAP_G, 190.0, color=DARK, width=1.8,
            head_len=4.6, head_w=2.4)
    c.rect(288.0, 190.0, 160.0, 26.0, fill=1.0, stroke=0.0, width=1.5)
    box_text(c, 288.0, 190.0, 160.0, 26.0,
             ["block -> prompt = None", "fail-closed; audit logged"],
             size=8.0, title_bold=False, pad=6.0, leading=9.2)
    problems += fit_problems(["block -> prompt = None", "fail-closed; audit logged"],
                             160.0, 26.0, title_bold=False, tag="block box")

    # ══ 6. host hooks ═════════════════════════════════════════════════════
    hooks = [
        (49.5, ["agent/pre-step", "turn start"]),
        (131.5, ["tool/result", "memory write"]),
        (459.5, ["turn/end", "decay, counters"]),
    ]
    for cx, lines in hooks:
        c.rect(cx - 38.0, 222.0, 76.0, 26.0, fill=1.0, stroke=0.0, width=0.8,
               dash=(2.4, 1.7))
        box_text(c, cx - 38.0, 222.0, 76.0, 26.0, lines, size=8.0,
                 title_bold=False, pad=6.0, leading=9.2)
        c.arrow(cx, 222.0, cx, DP_Y + DP_H + 0.8, color=0.38, width=0.7,
                dash=(2.0, 1.8), head_len=3.8, head_w=1.9)
        problems += fit_problems(lines, 76.0, 26.0, title_bold=False, tag=lines[0])
    c.text(178.0, 235.0, "HOST HOOKS (session events, observers)", size=8.0,
           bold=True, color=0.25)

    # ══ 7. eight-layer priority strip ═════════════════════════════════════
    c.rect(BAND_X, 254.0, BAND_W, 58.0, fill=0.965, stroke=0.45, width=0.6)
    zone_label(c, 14, 261, "LAYER PRIORITY",
               "frozen order, 1 = highest; layer 1 is read-only")
    layers = [
        "1 Constitution (read-only)", "2 Metacognition (facts)",
        "3 Goal & intent (content)", "4 Relation model (style)",
        "5 Narrative (content)", "6 Memory (facts)",
        "7 Emotion (style)", "8 User-affect (design-only)",
    ]
    for i, name in enumerate(layers):
        col, row = i % 4, i // 4
        x = 9.0 + col * 123.0
        y = 266.0 + row * 22.0
        c.rect(x, y, 117.0, 18.0, fill=1.0, stroke=0.0, width=0.7)
        c.text(x + 58.5, y + 9.0, name, size=8.0, align="center")
        problems += fit_problems([name], 117.0, 18.0, pad_y=1.5, tag=name)

    # ══ 8. legend + provenance ════════════════════════════════════════════
    c.line(14.0, 320.0, 28.0, 320.0, color=0.0, width=0.9)
    c.text(31.0, 320.0, "data flow", size=8.0)
    c.dashed(74.0, 320.0, 88.0, 320.0, color=0.0, width=0.8, on=3.2, off=2.0)
    c.text(91.0, 320.0, "control signal (observe / act)", size=8.0)
    c.line(218.0, 320.0, 232.0, 320.0, color=DARK, width=1.8)
    c.text(235.0, 320.0, "fail-closed path", size=8.0)
    c.rect(298.0, 317.0, 6.0, 6.0, fill=0.0, stroke=None)
    c.text(306.0, 320.0, "!", size=8.0, bold=True)
    c.text(312.0, 320.0, "only fail-closed point in the system", size=8.0)

    c.text(14.0, 328.0,
           "T1 after recall | T2 composition (style) | T3 after composition "
           "| T4 after render | T5 before the model call",
           size=8.0, color=0.15)
    c.text(14.0, 336.0,
           "source: docs/00 sec.1-3; docs/19 sec.1, 2.2-2.5; "
           "engine/pipeline.py (step order, fail-closed gate)",
           size=8.0, color=GREY)
    return c, problems


FORBIDDEN = ["VSM", "rate-distortion", "rate distortion", "Cyber-Cognitive",
             "action loop", "adaptive evolution", "collective mind",
             "multi-agent", "multimodal", "cognitive envelope"]


def check(c: Canvas) -> list:
    """Structural self-checks: font-size floor, page bounds, forbidden vocabulary."""
    problems = []
    for op in c.ops:
        if op[0] == "text":
            _, (x, y), s, size, bold, align, color, valign, rotate = op
            if size < 8.0 - 1e-9:
                problems.append("font %.2fpt < 8pt: %r" % (size, s))
            w = pc.text_width(s, size, bold)
            if rotate == 0:
                x0 = x - (w / 2.0 if align == "center" else (w if align == "right" else 0.0))
                if x0 < -0.01 or x0 + w > W + 0.01:
                    problems.append("text outside page horizontally: %r (%.1f..%.1f)"
                                    % (s, x0, x0 + w))
                if y - size * 0.55 < -0.01 or y + size * 0.55 > H + 0.01:
                    problems.append("text outside page vertically: %r (y=%.1f)" % (s, y))
            else:
                if y - w - 0.01 < 0 or y > H + 0.01 or x < 0 or x + size > W:
                    problems.append("rotated text outside page: %r" % (s,))
            for bad in FORBIDDEN:
                if bad.lower() in s.lower():
                    problems.append("forbidden term %r in %r" % (bad, s))
        elif op[0] in ("rect", "roundrect"):
            _, (x, y, w, h) = op[:2]
            if x < -0.01 or y < -0.01 or x + w > W + 0.01 or y + h > H + 0.01:
                problems.append("shape outside page: %r" % (op[:2],))
        elif op[0] == "line":
            _, (x1, y1, x2, y2) = op[:2]
            for x, y in ((x1, y1), (x2, y2)):
                if x < -0.01 or x > W + 0.01 or y < -0.01 or y > H + 0.01:
                    problems.append("line outside page: %r" % (op[:2],))
    return problems


def main(argv: list) -> int:
    out_dir = os.path.dirname(os.path.abspath(__file__))
    pdf_path = os.path.join(out_dir, "fig1-architecture.pdf")
    c, problems = build()
    problems += check(c)
    if problems:
        print("SELF-CHECK FAILED:")
        for p in problems:
            print("  -", p)
        return 2
    c.to_pdf(pdf_path, title="Fig.1 LINGSHU control plane / data plane",
             subject="Vector architecture diagram; no data series")
    print("wrote %s  (%.1f x %.1f pt = %.2f x %.2f in)"
          % (pdf_path, W, H, W / 72.0, H / 72.0))
    if "--preview" in argv:
        png = os.path.join(out_dir, "fig1-architecture.preview.png")
        c.to_png(png, scale=4)
        print("wrote %s (layout preview only; never a deliverable)" % png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
