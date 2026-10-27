# -*- coding: utf-8 -*-
"""Fig. 2 -- per-turn setting coverage of the injected context, three arms.

Metric (the only quantity drawn; it is *not* model accuracy and not "forgetting"):

    y_t = |setting_ids_injected_t  &  active_setting_ids_t| / |active_setting_ids_t|

i.e. the fraction of the *currently active* planted settings that appear in the
injected context on turn ``t``.  The plot shows a **5-turn trailing moving
average** of y_t; the raw turn-mean printed on the figure is computed from the
raw per-turn indicator, not from the smoothed line.

Data source (pinned by the experiments section):

    paper/ablation/results/abl-20261027-003626-engine.json
      conditions.<arm>.turns[].setting_ids_injected
      conditions.<arm>.turns[].active_setting_ids
      conditions.full.turns[].ratio            -> 0.70 / 0.85 phase bands
      conditions.full.turns[].anchor_applied   -> anchor strip

The script **asserts** the three raw arm means before it draws anything; if they
do not match the values quoted by the experiments section it exits non-zero and
writes no PDF.

Usage
-----
    python fig2_retention.py              # -> fig2-retention.pdf
    python fig2_retention.py --preview    # + fig2-retention.preview.png (layout check)
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import pdfcanvas as pc
from pdfcanvas import Canvas

HERE = os.path.dirname(os.path.abspath(__file__))
RESULT_PATH = os.path.join(HERE, os.pardir, "ablation", "results",
                           "abl-20261027-003626-engine.json")
RUN_ID = "abl-20261027-003626-engine"

# prereregistered raw turn-mean quoted by the experiments section (paper/06)
EXPECTED_MEANS = {"baseline": 0.03, "basic": 0.26, "full": 0.33}
MEAN_TOL = 0.005
MA_WINDOW = 5                 # trailing moving-average window (turns)

ARMS = ("baseline", "basic", "full")
ARM_LABEL = {"baseline": "Baseline", "basic": "LINGSHU-Basic",
             "full": "LINGSHU-Full"}

# style per arm: (colour, width, dash).  Grayscale-safe by construction:
# baseline = mid-grey short dash, basic = blue long dash, full = black solid.
BLUE = (0.122, 0.306, 0.612)          # #1f4e9c
ARM_STYLE = {
    "baseline": (0.45, 0.9, (2.5, 2.0)),
    "basic": (BLUE, 1.1, (6.0, 2.5)),
    "full": (0.0, 1.2, None),
}

# ── geometry (points, y-down; page = 7.00 x 2.89 in) ───────────────────────
W, H = 504.0, 208.0
X0, X1 = 70.0, 492.0          # plot x range (turn 1 .. turn 100)
Y0, Y1 = 36.0, 150.0          # y = 1.0 .. y = 0.0
AXIS_Y = 153.0                # x-axis a little below y = 0, so coverage-0
                              # turns stay visible as a line
LEGEND_Y = 22.0
STRIP_Y, STRIP_H = 156.0, 6.0  # anchor bar, tight under the x axis
TICK_Y = 170.0
XLABEL_Y = 180.0
NOTE_Y = 191.0
SRC_Y = [202.0]               # single 6 pt source line


def load_series():
    with open(RESULT_PATH, encoding="utf-8") as fh:
        d = json.load(fh)
    if d.get("run_id") != RUN_ID:
        raise SystemExit("run_id mismatch: %r != %r" % (d.get("run_id"), RUN_ID))
    if not d.get("valid"):
        raise SystemExit("result JSON is not marked valid=True; refusing to plot")

    turns = {}
    for arm in ARMS:
        t = d["conditions"][arm]["turns"]
        if len(t) != 100:
            raise SystemExit("%s: expected 100 turns, got %d" % (arm, len(t)))
        ys, raw = [], []
        for rec in t:
            inj = set(rec.get("setting_ids_injected") or [])
            act = set(rec.get("active_setting_ids") or [])
            if not act:
                raise SystemExit("%s turn %s: empty active_setting_ids"
                                 % (arm, rec.get("turn")))
            raw.append(len(inj & act) / len(act))
            lo = max(0, len(raw) - MA_WINDOW)      # trailing mean, min_periods=1
            ys.append(sum(raw[lo:]) / len(raw[lo:]))
        turns[arm] = t
        turns[arm + "_y"] = ys
        turns[arm + "_raw"] = raw

    means = {arm: sum(turns[arm + "_raw"]) / 100.0 for arm in ARMS}
    bad = {arm: (means[arm], EXPECTED_MEANS[arm]) for arm in ARMS
           if abs(means[arm] - EXPECTED_MEANS[arm]) > MEAN_TOL}
    if bad:
        raise SystemExit("arm means do not match paper/06: %r" % (bad,))

    ratio = [rec.get("ratio") for rec in turns["full"]]
    if any(r is None for r in ratio):
        raise SystemExit("full arm has turns without ratio; cannot place bands")
    band70 = [rec["turn"] for rec in turns["full"] if rec["ratio"] >= 0.70]
    band85 = [rec["turn"] for rec in turns["full"] if rec["ratio"] >= 0.85]
    anchors = [rec["turn"] for rec in turns["full"] if rec.get("anchor_applied")]
    trig = d["conditions"]["full"].get("telemetry", {}).get("phase_trigger_turns") or []
    if not band70 or not band85:
        raise SystemExit("no ratio threshold crossing in the full arm")
    if trig and trig[0] != band70[0]:
        raise SystemExit("phase_trigger_turns[0]=%s disagrees with ratio>=0.70 at %s"
                         % (trig[0], band70[0]))
    return turns, means, band70, band85, anchors


def x_of(turn: float) -> float:
    return X0 + (turn - 1.0) * (X1 - X0) / 99.0


def y_of(v: float) -> float:
    return Y1 - v * (Y1 - Y0)


def step_line(c: Canvas, ys, color, width, dash=None):
    xs = [x_of(t) for t in range(1, 101)] + [X1]
    vals = list(ys) + [ys[-1]]
    for i in range(len(xs) - 1):
        y = y_of(vals[i])
        c.line(xs[i], y, xs[i + 1], y, color=color, width=width, dash=dash)
        if vals[i] != vals[i + 1]:
            c.line(xs[i + 1], y, xs[i + 1], y_of(vals[i + 1]),
                   color=color, width=width, dash=dash)


def build(series):
    turns, means, band70, band85, anchors = series
    c = Canvas(W, H)
    problems = []

    # phase bands (behind everything)
    c.rect(x_of(band70[0]), Y0, X1 - x_of(band70[0]), AXIS_Y - Y0,
           fill=0.930, stroke=None)
    c.rect(x_of(band85[0]), Y0, X1 - x_of(band85[0]), AXIS_Y - Y0,
           fill=0.855, stroke=None)

    # grid + axes
    for v in (0.25, 0.50, 0.75):
        c.line(X0, y_of(v), X1, y_of(v), color=0.85, width=0.5)
    c.line(X0, Y0, X0, AXIS_Y, color=0.0, width=0.8)
    c.line(X0, AXIS_Y, X1, AXIS_Y, color=0.0, width=0.8)
    for v in (0.0, 0.25, 0.50, 0.75, 1.0):
        y = y_of(v)
        c.line(X0 - 2.5, y, X0, y, color=0.0, width=0.7)
        c.text(X0 - 5.0, y, ("%.2f" % v), size=8.0, align="right", color=0.0)
    for t in (1, 20, 40, 60, 80, 100):
        x = x_of(t)
        c.line(x, AXIS_Y, x, AXIS_Y + 3.0, color=0.0, width=0.7)
        right = (t == 100)
        c.text(x + (4.0 if right else 0.0), TICK_Y, str(t), size=8.0,
               align=("right" if right else "center"), color=0.0)

    # series: baseline -> basic -> full (full on top, solid black)
    for arm in ("baseline", "basic", "full"):
        color, width, dash = ARM_STYLE[arm]
        step_line(c, turns[arm + "_y"], color=color, width=width, dash=dash)

    # y axis label (rotated) + axis titles
    ylabel = "setting coverage per turn"
    wl = pc.text_width(ylabel, 8.0)
    c.text(15.0, (Y0 + Y1) / 2.0 + wl / 2.0, ylabel, size=8.0, rotate=90)
    c.text((X0 + X1) / 2.0, XLABEL_Y, "conversation turn", size=8.0, align="center")

    # legend
    lx = 150.0
    for arm in ("baseline", "basic", "full"):
        color, width, dash = ARM_STYLE[arm]
        if dash:
            c.dashed(lx, LEGEND_Y, lx + 15.0, LEGEND_Y, color=color, width=width,
                     on=dash[0], off=dash[1])
        else:
            c.line(lx, LEGEND_Y, lx + 15.0, LEGEND_Y, color=color, width=width)
        c.text(lx + 18.0, LEGEND_Y, ARM_LABEL[arm], size=8.0,
               color=(BLUE if arm == "basic" else 0.0))
        lx += 18.0 + pc.text_width(ARM_LABEL[arm], 8.0) + 18.0

    # anchor bar: thin strip right under the x axis, horizontally aligned with
    # the turns where Full actually applied the head/tail anchor
    c.rect(x_of(anchors[0]), STRIP_Y, X1 - x_of(anchors[0]), STRIP_H,
           fill=0.45, stroke=None)
    c.text(X0, STRIP_Y + STRIP_H / 2.0,
           "Full anchor (t%d-%d)" % (anchors[0], anchors[-1]), size=8.0)

    # band explanation moved out of the shaded area: one line under the plot
    c.text(X0, NOTE_Y,
           "shaded bands: context-window ratio >= 0.70 from t%d (light), "
           ">= 0.85 from t%d (dark)" % (band70[0], band85[0]),
           size=8.0, color=0.15)

    # one-line provenance; the LaTeX caption carries the full field paths,
    # so the figure itself only keeps a minimal 6 pt source line
    src = ("source: ablation/%s.json (run_id %s); line = %d-turn trailing mean; "
           "bands/bar from ratio & anchor_applied" % (RUN_ID, RUN_ID, MA_WINDOW))
    c.text(14.0, SRC_Y[0], src, size=6.0, color=0.45)
    if pc.text_width(src, 6.0) > W - 24.0:
        problems.append("source line is too wide (%.1fpt)" % pc.text_width(src, 6.0))
    return c, problems


FORBIDDEN = ["VSM", "rate-distortion", "Cyber-Cognitive", "action loop",
             "adaptive evolution", "collective mind", "multi-agent", "multimodal"]


def check(c: Canvas) -> list:
    problems = []
    for op in c.ops:
        if op[0] == "text":
            _, (x, y), s, size, bold, align, color, valign, rotate = op
            floor = 6.0 if s.startswith("source:") else 8.0   # one 6 pt source line
            if size < floor - 1e-9:
                problems.append("font %.2fpt < %.1fpt: %r" % (size, floor, s))
            w = pc.text_width(s, size, bold)
            if rotate == 0:
                x0 = x - (w / 2.0 if align == "center" else (w if align == "right" else 0.0))
                if x0 < -0.01 or x0 + w > W + 0.01:
                    problems.append("text outside page: %r" % (s,))
            else:
                if y - w < -0.01 or y > H + 0.01:
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
    series = load_series()
    turns, means, band70, band85, anchors = series
    print("run_id %s | raw turn-mean: baseline %.4f basic %.4f full %.4f"
          % (RUN_ID, means["baseline"], means["basic"], means["full"]))
    print("5-turn trailing mean | phase bands: ratio>=0.70 from t%d, >=0.85 from t%d"
          " | anchor turns %d" % (band70[0], band85[0], len(anchors)))
    c, problems = build(series)
    problems += check(c)
    if problems:
        print("SELF-CHECK FAILED:")
        for p in problems:
            print("  -", p)
        return 2
    pdf = os.path.join(HERE, "fig2-retention.pdf")
    c.to_pdf(pdf, title="Fig.2 per-turn setting coverage (three arms)",
             subject="5-turn trailing mean of per-turn setting coverage; "
                     "source run %s" % RUN_ID)
    print("wrote %s  (%.1f x %.1f pt = %.2f x %.2f in)"
          % (pdf, W, H, W / 72.0, H / 72.0))
    if "--preview" in argv:
        png = os.path.join(HERE, "fig2-retention.preview.png")
        c.to_png(png, scale=4)
        print("wrote %s (layout preview only; never a deliverable)" % png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
