# -*- coding: utf-8 -*-
"""Fig. 4 -- red-team interception rate of the LINGSHU preflight gate, before/after patch.

What is drawn (and the only thing drawn): the machine-judged interception rate

    intercept_rate(L) = |payloads whose preflight gate verdict is block|
                        / |payloads in complexity level L|

for L = L1 direct, L2 encoded, L3 multilingual, L4 nested, twice:
before the static-rule patch (dashed, open markers) and after it (solid, blue).

Data source (pinned):

    paper/case-study/fig4-inputs.json          <- curated, machine-generated
    paper/case-study/results-before.json       <- produced by run_gate.mjs --phase before
    paper/case-study/results-after.json        <- produced by run_gate.mjs --phase after
    paper/case-study/payloads.jsonl            <- the 20 payloads themselves

Discipline (same as fig2_retention.py): the script asserts every number it draws
**before drawing anything** -- pinned per-level counts, the recomputed counts from
both result files, the payload inventory, and the plugin fingerprints.  Any
disagreement (stale inputs, edited JSON, missing `not_run` status) exits non-zero
and writes **no** PDF.

Honest boundary (must stay in the caption/limitations, not extrapolated):
  * "blocked" = the gate stopped the injection (fail-closed) and logged UCA-01;
    it does NOT mean the model never saw the attack text (the host payload is not
    rewritten by this plugin).
  * the 20 payloads are also the set the static patterns were extended for, so
    after-patch 100% is an **in-sample** number; held-out generalisation is future work.

Usage
-----
    python fig4-redteam.py              # -> fig4-redteam.pdf
    python fig4-redteam.py --preview    # + fig4-redteam.preview.png (layout check only)
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from pdfcanvas import Canvas  # noqa: E402
import pdfcanvas as pc       # noqa: E402

HERE = os.path.dirname(os.path.abspath(__file__))
CASE = os.path.join(os.path.dirname(HERE), "case-study")
INPUTS = os.path.join(CASE, "fig4-inputs.json")
RESULTS = {
    "before": os.path.join(CASE, "results-before.json"),
    "after": os.path.join(CASE, "results-after.json"),
}
PAYLOADS = os.path.join(CASE, "payloads.jsonl")

# ── pinned expectations (pre-registered before the figure is allowed to exist) ──
LEVELS = ("L1", "L2", "L3", "L4")
GROUP_OF = {"L1": "direct", "L2": "encoded", "L3": "multilingual", "L4": "nested"}
N_PER_LEVEL = 5
N_TOTAL = 20
EXPECTED = {                                  # (blocked, n) per level
    "before": {"L1": (3, 5), "L2": (0, 5), "L3": (0, 5), "L4": (0, 5)},
    "after": {"L1": (5, 5), "L2": (5, 5), "L3": (5, 5), "L4": (5, 5)},
}

# ── geometry (points, y-down; 7.00 x 4.17 in) ───────────────────────────────
W, H = 504.0, 300.0
BLUE = (0.122, 0.306, 0.612)          # single accent colour (#1f4e9c)
X0, X1 = 90.0, 470.0
Y_TOP, Y_BOT = 58.0, 196.0            # y = 1.0 .. y = 0.0
CAT_X = [X0 + i * (X1 - X0) / 3.0 for i in range(4)]
TICK_LABEL_Y, N_LABEL_Y = 212.0, 222.0
XLABEL_Y = 238.0
LEGEND_Y = 254.0
NOTE_Y = 270.0
SRC_Y = 290.0

FORBIDDEN = ["VSM", "rate-distortion", "Cyber-Cognitive", "action loop",
             "adaptive evolution", "collective mind", "multi-agent", "multimodal"]


def read_json(path):
    with open(path, encoding="utf-8") as fh:
        return json.load(fh)


def load_inputs():
    """Load + assert. Returns (data, problems); problems non-empty => no PDF."""
    problems = []
    if not os.path.isfile(INPUTS):
        return None, ["missing %s" % INPUTS]
    d = read_json(INPUTS)

    # 1) inventory
    if d.get("schema") != "fig4-redteam-inputs/1":
        problems.append("schema=%r" % d.get("schema"))
    if int(d.get("n_total", -1)) != N_TOTAL:
        problems.append("n_total=%r != %d" % (d.get("n_total"), N_TOTAL))
    if tuple(d.get("levels") or ()) != LEVELS:
        problems.append("levels=%r" % (d.get("levels"),))
    lines = [ln for ln in open(PAYLOADS, encoding="utf-8").read().splitlines() if ln.strip()]
    if len(lines) != N_TOTAL:
        problems.append("payloads.jsonl has %d lines != %d" % (len(lines), N_TOTAL))
    payload_ids = []
    level_of = {}
    for i, ln in enumerate(lines):
        try:
            rec = json.loads(ln)
        except Exception as exc:                       # noqa: BLE001
            problems.append("payload line %d is not JSON: %s" % (i + 1, exc))
            continue
        payload_ids.append(rec.get("id"))
        level_of[rec.get("id")] = rec.get("level")
        if rec.get("level") not in LEVELS:
            problems.append("payload %r has level %r" % (rec.get("id"), rec.get("level")))
    if len(set(payload_ids)) != N_TOTAL:
        problems.append("payload ids are not unique")

    # 2) per-level table vs pinned counts
    groups = {g.get("level"): g for g in (d.get("groups") or [])}
    if set(groups) != set(LEVELS):
        problems.append("groups=%r" % (sorted(groups),))
    for lv in LEVELS:
        g = groups.get(lv) or {}
        if g.get("group") != GROUP_OF[lv]:
            problems.append("%s group=%r != %r" % (lv, g.get("group"), GROUP_OF[lv]))
        if int(g.get("n", -1)) != N_PER_LEVEL:
            problems.append("%s n=%r != %d" % (lv, g.get("n"), N_PER_LEVEL))
        for phase in ("before", "after"):
            cnt = g.get(phase + "_blocked")
            rate = g.get(phase + "_rate")
            want_cnt, want_n = EXPECTED[phase][lv]
            if cnt != want_cnt:
                problems.append("%s %s_blocked=%r != pinned %d" % (lv, phase, cnt, want_cnt))
            if not isinstance(rate, (int, float)) or abs(rate - cnt / float(want_n)) > 1e-9:
                problems.append("%s %s_rate=%r inconsistent with %r/%d"
                                % (lv, phase, rate, cnt, want_n))

    # 3) overall
    ov = d.get("overall") or {}
    for phase, key in (("before", "before"), ("after", "after")):
        cnt = ov.get(key + "_blocked")
        if cnt != sum(EXPECTED[phase][lv][0] for lv in LEVELS):
            problems.append("overall %s_blocked=%r" % (phase, cnt))
        if not isinstance(ov.get(key + "_rate"), (int, float)):
            problems.append("overall %s_rate missing" % phase)
        elif abs(ov[key + "_rate"] - cnt / float(N_TOTAL)) > 1e-9:
            problems.append("overall %s_rate mismatch" % phase)
    if ov.get("n") != N_TOTAL:
        problems.append("overall n=%r" % ov.get("n"))

    # 4) raw results re-aggregation (inputs must not drift from the measurements)
    for phase, path in RESULTS.items():
        if not os.path.isfile(path):
            problems.append("missing %s" % path)
            continue
        r = read_json(path)
        if r.get("phase") != phase:
            problems.append("%s phase=%r" % (phase, r.get("phase")))
        recs = r.get("records") or []
        if len(recs) != N_TOTAL:
            problems.append("%s records=%d" % (phase, len(recs)))
        for rec in recs:
            ev = rec.get("evidence") or {}
            if ev.get("status") != "ok":
                problems.append("%s %s evidence.status=%r (not measurable)"
                                % (phase, rec.get("id"), ev.get("status")))
            if level_of.get(rec.get("id")) != rec.get("level"):
                problems.append("%s %s level mismatch" % (phase, rec.get("id")))
        for lv in LEVELS:
            got = sum(1 for rec in recs
                      if rec.get("level") == lv and rec.get("blocked") is True)
            want = EXPECTED[phase][lv][0]
            if got != want:
                problems.append("%s recomputed %s blocked=%d != pinned %d"
                                % (phase, lv, got, want))
        if r.get("plugin_sha256") != (d.get("plugin") or {}).get(phase):
            problems.append("%s plugin_sha256 in inputs != results" % phase)
    return d, problems


def load_and_verify():
    d, problems = load_inputs()
    if problems:
        return None, problems
    # L1-before per-record outcomes are not derivable from the group count, so
    # they are pinned explicitly here (which three of five) and cross-checked
    # against results-before.json.
    pinned_l1_before = {"L1-01": True, "L1-02": True, "L1-03": False,
                        "L1-04": True, "L1-05": False}
    raw = {phase: read_json(path) for phase, path in RESULTS.items()}
    before = {rec["id"]: rec.get("blocked") for rec in raw["before"].get("records") or []}
    after = {rec["id"]: rec.get("blocked") for rec in raw["after"].get("records") or []}
    for pid, want in pinned_l1_before.items():
        if before.get(pid) != want:
            problems.append("results-before.json %s blocked=%r != pinned %r"
                            % (pid, before.get(pid), want))
    # inputs.records is the per-payload audit trail: it must match both runs
    per = {rec.get("id"): rec for rec in (d.get("records") or [])}
    payload_ids = [json.loads(ln)["id"]
                   for ln in open(PAYLOADS, encoding="utf-8").read().splitlines() if ln.strip()]
    if set(per) != set(payload_ids):
        problems.append("inputs.records ids != payload ids")
    for pid in payload_ids:
        rec = per.get(pid) or {}
        if rec.get("before_blocked") != before.get(pid):
            problems.append("inputs.records %s before_blocked != results-before" % pid)
        if rec.get("after_blocked") != after.get(pid):
            problems.append("inputs.records %s after_blocked != results-after" % pid)
        if rec.get("after_evidence_status") != "ok" or rec.get("before_evidence_status") != "ok":
            problems.append("inputs.records %s evidence status != ok" % pid)
    return d, problems


def x_of(i: int) -> float:
    return CAT_X[i]


def y_of(rate: float) -> float:
    return Y_BOT - rate * (Y_BOT - Y_TOP)


def build(d: dict) -> Canvas:
    c = Canvas(W, H)
    groups = {g["level"]: g for g in d["groups"]}

    c.text(W / 2.0, 18.0, "Red-team interception: static preflight gate, before vs after patch",
           size=9.5, bold=True, align="center")
    c.text(W / 2.0, 33.0,
           "%d payloads, %d per complexity level; gate = preflightAudit UCA-01 (user message scan)"
           % (N_TOTAL, N_PER_LEVEL), size=8.0, align="center", color=0.2)

    # grid + axes
    for rate in (0.25, 0.50, 0.75):
        c.line(X0, y_of(rate), X1, y_of(rate), color=0.85, width=0.5)
    c.line(X0, Y_TOP - 10.0, X0, Y_BOT, color=0.0, width=0.8)
    c.line(X0, Y_BOT, X1 + 4.0, Y_BOT, color=0.0, width=0.8)
    for rate in (0.0, 0.25, 0.50, 0.75, 1.0):
        y = y_of(rate)
        c.line(X0 - 3.0, y, X0, y, color=0.0, width=0.7)
        c.text(X0 - 6.0, y, "%d%%" % round(rate * 100), size=8.0, align="right")

    # x category ticks + labels + n
    for i, lv in enumerate(LEVELS):
        x = x_of(i)
        c.line(x, Y_BOT, x, Y_BOT + 4.0, color=0.0, width=0.7)
        c.text(x, TICK_LABEL_Y, "%s %s" % (lv, GROUP_OF[lv]), size=8.0, align="center")
        c.text(x, N_LABEL_Y, "n=%d" % N_PER_LEVEL, size=8.0, align="center", color=0.35)
    c.text((X0 + X1) / 2.0, XLABEL_Y, "complexity level (group; n = 5 per level)",
           size=8.0, align="center")

    # series: before (dashed grey, open markers), after (solid blue, filled markers)
    before_pts = [(x_of(i), y_of(groups[lv]["before_rate"])) for i, lv in enumerate(LEVELS)]
    after_pts = [(x_of(i), y_of(groups[lv]["after_rate"])) for i, lv in enumerate(LEVELS)]
    for (x1, y1), (x2, y2) in zip(before_pts, before_pts[1:]):
        c.line(x1, y1, x2, y2, color=0.45, width=0.9, dash=(3.0, 2.0))
    for (x1, y1), (x2, y2) in zip(after_pts, after_pts[1:]):
        c.line(x1, y1, x2, y2, color=BLUE, width=1.2)
    for x, y in before_pts:
        c.circle(x, y, 2.3, fill=(1.0, 1.0, 1.0), stroke=0.45, width=0.8)
    for x, y in after_pts:
        c.circle(x, y, 2.3, fill=BLUE, stroke=BLUE, width=0.8)

    # value labels: "blocked/n" at every point (the figure's numbers stay readable
    # even at 0% -- which is the honest before-patch result for L2..L4)
    for i, lv in enumerate(LEVELS):
        g = groups[lv]
        bx, by = before_pts[i]
        b = int(g["before_blocked"])
        # place above the point for low rates, below it otherwise (inside the plot);
        # the L1 point sits on the y axis, so its label is nudged right to avoid
        # colliding with the 50% tick label.
        if g["before_rate"] < 0.2:
            c.text(bx, by - 13.0, "%d/%d" % (b, N_PER_LEVEL), size=8.0,
                   align="center", color=0.35)
        else:
            c.text(bx + 16.0, by + 13.0, "%d/%d" % (b, N_PER_LEVEL), size=8.0,
                   align="left", color=0.35)
        ax, ay = after_pts[i]
        c.text(ax, ay - 11.0, "%d/%d" % (int(g["after_blocked"]), N_PER_LEVEL),
               size=8.0, align="center", color=BLUE)

    # rotated y-axis title
    ylabel = "interception rate"
    wl = pc.text_width(ylabel, 8.0)
    c.text(20.0, (Y_TOP + Y_BOT) / 2.0 + wl / 2.0 - 6.0, ylabel, size=8.0, rotate=90)

    # legend
    ov = d["overall"]
    ly = LEGEND_Y
    c.dashed(96.0, ly, 112.0, ly, on=3.0, off=2.0, color=0.45, width=0.9)
    c.circle(104.0, ly, 2.3, fill=(1.0, 1.0, 1.0), stroke=0.45, width=0.8)
    c.text(116.0, ly, "before patch (%d/%d = %.0f%%)"
           % (ov["before_blocked"], N_TOTAL, 100.0 * ov["before_rate"]), size=8.0)
    c.line(300.0, ly, 316.0, ly, color=BLUE, width=1.2)
    c.circle(308.0, ly, 2.3, fill=BLUE, stroke=BLUE, width=0.8)
    c.text(320.0, ly, "after patch (%d/%d = %.0f%%)"
           % (ov["after_blocked"], N_TOTAL, 100.0 * ov["after_rate"]), size=8.0, color=BLUE)

    c.text(W / 2.0, NOTE_Y,
           "blocked = injection stopped fail-closed and logged as UCA-01; the plugin does not"
           " rewrite the host payload", size=8.0, align="center", color=0.15)
    src = ("source: paper/case-study/fig4-inputs.json <- results-before.json / results-after.json;"
           " n=%d payloads, %d per level; mock host, isolated LINGSHU_DATA_DIR" % (N_TOTAL, N_PER_LEVEL))
    c.text(14.0, SRC_Y, src, size=6.0, color=0.45)
    if pc.text_width(src, 6.0) > W - 24.0:
        raise SystemExit("source line too wide: %.1f pt" % pc.text_width(src, 6.0))
    return c


def check(c: Canvas) -> list:
    problems = []
    for op in c.ops:
        if op[0] == "text":
            _, (x, y), s, size, bold, align, color, valign, rotate = op
            floor = 6.0 if s.startswith("source:") else 8.0
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
            for ch in s:
                if ord(ch) > 126:
                    problems.append("non-ASCII text: %r" % (s,))
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
        elif op[0] == "circle":
            _, (cx, cy, r), _fill, _stroke, _width = op
            if cx - r < -0.01 or cy - r < -0.01 or cx + r > W + 0.01 or cy + r > H + 0.01:
                problems.append("circle outside page: %r" % (op[:2],))
    return problems


def main(argv: list) -> int:
    d, problems = load_and_verify()
    if d is None or problems:
        print("SELF-CHECK FAILED (no PDF written):")
        for p in problems or ["inputs missing"]:
            print("  -", p)
        return 2
    print("n_total=%d | before %d/%d = %.1f%% | after %d/%d = %.1f%%"
          % (d["n_total"], d["overall"]["before_blocked"], d["n_total"],
             100.0 * d["overall"]["before_rate"], d["overall"]["after_blocked"],
             d["n_total"], 100.0 * d["overall"]["after_rate"]))
    for g in d["groups"]:
        print("  %s %-12s before %d/%d = %5.1f%% | after %d/%d = %5.1f%%"
              % (g["level"], g["group"], g["before_blocked"], g["n"],
                 100.0 * g["before_rate"], g["after_blocked"], g["n"],
                 100.0 * g["after_rate"]))
    c = build(d)
    problems = check(c)
    if problems:
        print("SELF-CHECK FAILED (no PDF written):")
        for p in problems:
            print("  -", p)
        return 2
    pdf = os.path.join(HERE, "fig4-redteam.pdf")
    c.to_pdf(pdf, title="Fig.4 red-team interception rate before/after static-rule patch",
             subject="20 payloads x 4 complexity levels; gate=preflightAudit UCA-01; "
                     "sources: paper/case-study/results-before.json / results-after.json")
    print("wrote %s  (%.1f x %.1f pt = %.2f x %.2f in)"
          % (pdf, W, H, W / 72.0, H / 72.0))
    if "--preview" in argv:
        png = os.path.join(HERE, "fig4-redteam.preview.png")
        c.to_png(png, scale=4)
        print("wrote %s (layout preview only; never a deliverable)" % png)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
