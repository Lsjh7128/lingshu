# -*- coding: utf-8 -*-
"""Fig. 3 -- state transitions of the control plane under fault injection.

Drawn from a curated inputs file produced by the three fault-injection runs
(A: LLM transport, B: memory corruption, C: context flood):

    experiments/chaos-injection/out/fig3-inputs.json

The script **asserts** every number it draws (same discipline as
fig2_retention.py): if an input contradicts its evidence or a boundary case is
missing, it exits non-zero and writes **no** PDF.

Usage
-----
    python fig3_robustness.py              # -> fig3-robustness.pdf
    python fig3_robustness.py --preview    # + fig3-robustness.preview.png
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pdfcanvas import Canvas  # noqa: E402
import pdfcanvas as pc       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
INPUTS = os.path.join(REPO, "experiments", "chaos-injection", "out", "fig3-inputs.json")

W, H = 504.0, 316.0            # 7.00 x 4.39 in
BLUE = (0.122, 0.306, 0.612)   # single accent colour (#1f4e9c)

# geometry (points, y-down)
NORMAL = (152.0, 26.0, 200.0, 54.0)
ROW_Y, ROW_H, ROW_W = 168.0, 104.0, 116.0
ROW_X = (8.0, 132.0, 256.0, 380.0)
BUS_DEGRADE, BUS_RECOVER = 130.0, 148.0


def load_inputs():
    if not os.path.isfile(INPUTS):
        return None, ["缺少 %s（先跑 A/B/C 三个剧本并生成该文件）" % INPUTS]
    data = json.load(open(INPUTS, encoding="utf-8"))
    problems = []
    a = data.get("A", {})
    if int(a.get("http_attempts", 0)) < 5:
        problems.append("A: http_attempts=%r（应 >=5）" % a.get("http_attempts"))
    if float(a.get("wall_s", 0)) < 5.0:
        problems.append("A: wall_s=%r（应 >=5）" % a.get("wall_s"))
    if any(bool(x) for x in (a.get("blocked_by_mode") or {}).values()):
        problems.append("A: 有 mode 报 blocked=true")
    if not a.get("empty_passthrough"):
        problems.append("A: empty 200 未记录为透传")
    if not a.get("fabricated_passthrough"):
        problems.append("A: fabricated 200 未记录为透传")
    b = data.get("B", {})
    if int(b.get("b1_hook_errors", 0)) < 1:
        problems.append("B1: hook_errors=%r（应 >=1）" % b.get("b1_hook_errors"))
    if bool(b.get("b1_blocked")):
        problems.append("B1: blocked=true")
    if not b.get("b1_answer_still_returned"):
        problems.append("B1: 未记录'仍返回可用回答'")
    for key, msg in (("b2_variety_fallback", "B2: variety fallback 未触发"),
                     ("b2_style_dropped", "B2: style 未被丢弃"),
                     ("b2_conflict_hint", "B2: prompt 缺冲突提示"),
                     ("b2_constitution", "B2: prompt 缺宪法段")):
        if not b.get(key):
            problems.append(msg)
    c = data.get("C", {})
    if abs(float(c.get("ratio70", 0)) - 0.70) > 0.01:
        problems.append("C: ratio70=%r 偏离 0.70" % c.get("ratio70"))
    if str(c.get("phase70")) != "shift":
        problems.append("C: 0.70 点 phase=%r" % c.get("phase70"))
    if str(c.get("phase85")) != "critical":
        problems.append("C: 0.85 点 phase=%r" % c.get("phase85"))
    if not c.get("anchor85"):
        problems.append("C: critical 点未观测到锚点")
    if not c.get("constitution_all"):
        problems.append("C: 有输入点丢宪法段")
    return data, problems


def _state_box(c, x, y, w, h, title, cause, lines, chip_text, dashed=False):
    c.roundrect(x, y, w, h, fill=(1.0, 1.0, 1.0), stroke=0.0, width=0.9,
                radius=3.0, dash=(2.0, 2.0) if dashed else None)
    pc.box_text(c, x, y + 2, w, h - 28,
                [title, cause] + list(lines), size=8.0)
    if chip_text:
        pc.chip(c, x + 6, y + h - 24, w - 12, 18, [chip_text], size=8.0,
                fill=(1.0, 1.0, 1.0), stroke=BLUE, color=BLUE)


def build(d: dict) -> Canvas:
    c = Canvas(W, H)
    c.text(W / 2, 12, "Control-plane state transitions under injected faults",
           size=9.5, bold=True, align="center")

    nx, ny, nw, nh = NORMAL
    c.roundrect(nx, ny, nw, nh, fill=(0.985, 0.985, 0.985), stroke=0.0, width=1.1,
                radius=3.0)
    pc.box_text(c, nx, ny + 2, nw, nh - 4,
                ["Normal (full function)",
                 "constitution + fact + organization + style + recall; verdict = allow"],
                size=8.0)

    # degradation bus (solid): Normal -> one vertical per state
    ncx = nx + nw / 2.0
    c.line(ncx, ny + nh, ncx, BUS_DEGRADE, width=1.0)
    c.line(ROW_X[0] + ROW_W / 2.0, BUS_DEGRADE,
           ROW_X[-1] + ROW_W / 2.0, BUS_DEGRADE, width=1.0)
    for x in ROW_X:
        xc = x + ROW_W / 2.0
        c.arrow(xc, BUS_DEGRADE, xc, ROW_Y - 1.0, width=1.0)

    # recovery bus (dashed): states -> Normal; only one arrowhead (at Normal)
    c.dashed(ROW_X[0] + ROW_W / 2.0, BUS_RECOVER,
             ROW_X[-1] + ROW_W / 2.0, BUS_RECOVER, on=2.5, off=2.0, width=0.7)
    for x in ROW_X:
        xc = x + ROW_W / 2.0
        c.dashed(xc, ROW_Y - 1.0, xc, BUS_RECOVER, on=2.5, off=2.0, width=0.7)
    c.arrow(ncx, BUS_RECOVER, ncx, ny + nh + 1.0, width=0.7, dash=(2.5, 2.0))

    # bus captions (left of the central verticals, so they never sit on a line)
    c.text(ncx - 8, 96.0, "degradation", size=8.0, align="right")
    c.text(ncx - 8, 158.0, "recovery", size=8.0, align="right")

    _state_box(c, ROW_X[0], ROW_Y, ROW_W, ROW_H, "Feature-isolated",
               "cause: memory write throws (B1)",
               ("recall empty; metacog hedges;", "emotion / phase still injected"),
               "hook_error=1")
    _state_box(c, ROW_X[1], ROW_Y, ROW_W, ROW_H, "Neutral-safe",
               "cause: unresolved recall conflict (B2)",
               ("style dropped; facts neutral;", "conflict-to-clarify; constitution kept"),
               "fallback = true")
    _state_box(c, ROW_X[2], ROW_Y, ROW_W, ROW_H, "Safety cut-off",
               "cause: constitution violation",
               ("injection stopped: prompt = None;", "constitution-only + notice"),
               "fail-closed (S3)")
    _state_box(c, ROW_X[3], ROW_Y, ROW_W, ROW_H, "Host transport fault",
               "cause: HTTP 500 / empty / hang (A)",
               ("host retries with backoff;", "no fabricated content emitted"),
               "%d tries; %.1f s" % (int(d["A"]["http_attempts"]),
                                              float(d["A"]["wall_s"])),
               dashed=True)

    # legend + provenance
    ly = 290.0
    c.line(16.0, ly, 44.0, ly, width=1.0)
    c.text(48.0, ly, "fault -> degradation", size=8.0)
    c.dashed(150.0, ly, 178.0, ly, on=2.5, off=2.0, width=0.7)
    c.text(182.0, ly, "recovery", size=8.0)
    c.rect(232.0, ly - 5.0, 10.0, 10.0, fill=None,
           stroke=0.0, width=0.7, dash=(2.0, 2.0))
    c.text(246.0, ly, "outside the control plane", size=8.0)
    c.text(W / 2, 308.0,
           "source: fault-injection runs 2026-10-27 (A transport, B memory, C context);"
           " plugin unmodified; n = 1 per injected point.",
           size=6.0, align="center")
    return c


def check(c: Canvas) -> list:
    problems = []
    for op in c.ops:
        if op[0] != "text":
            continue
        x, y, s, size = op[1][0], op[1][1], op[2], op[3]
        if size < 6.0:
            problems.append("font %.1f < 6pt: %r" % (size, s[:30]))
        if not (0 < x < W and 0 < y < H):
            problems.append("text outside page: %r" % s[:30])
    return problems


def main(argv) -> int:
    data, problems = load_inputs()
    if data is None or problems:
        print("SELF-CHECK FAILED:")
        for p in problems or ["inputs missing"]:
            print("  -", p)
        return 2
    c = build(data)
    problems = check(c)
    if problems:
        print("SELF-CHECK FAILED:")
        for p in problems:
            print("  -", p)
        return 2
    pdf = os.path.join(HERE, "fig3-robustness.pdf")
    c.to_pdf(pdf, title="Fig.3 control-plane state transitions under injected faults",
             subject="Fault injection A/B/C, 2026-10-27; plugin unmodified; n=1 per point")
    print("wrote %s  (%.1f x %.1f pt = %.2f x %.2f in)"
          % (pdf, W, H, W / 72.0, H / 72.0))
    if "--preview" in argv:
        png = os.path.join(HERE, "fig3-robustness.preview.png")
        c.to_png(png, scale=4)
        print("wrote %s (layout preview only; never a deliverable)" % png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
