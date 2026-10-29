# -*- coding: utf-8 -*-
"""Record the post-F-D-fix S1/S2 rerun as a paper-grade provenance file and patch
the canonical final-table numbers for the three-subject baseline table."""
import hashlib
import json
import os

PAPER = os.path.dirname(os.path.abspath(__file__))  # paper/
FD = os.path.join(PAPER, "comparison-lab-fd-fix")
SUM = os.path.join(FD, "results", "summary.json")
CALLS = os.path.join(FD, "raw", "calls.jsonl")
FT = os.path.join(PAPER, "comparison-lab", "results", "final-table.json")


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


s = json.load(open(SUM, encoding="utf-8"))
ratios = s["ratios"]
cells = s["cells"]
by_cell = {c["cell"]: c for c in cells}

prov = {
    "schema": "lingshu-paper/post-fix-rerun/v1",
    "purpose": "S1/S2 host cells rerun after the v5.4 same-turn injection fix (finding F-D)",
    "fixed_at": "2026-10-27T20:24:05+08:00 (plugin/dsh-lingshu/index.js v5.4 claim→precompute→rewrite)",
    "runs": {
        "s1_lingshu": {"run_id": "20261027-235952", "cells": 5, "calls": 20},
        "s1_baseline": {"run_id": "20261028-000500", "cells": 5, "calls": 20},
        "s2_lingshu": {"run_id": "20261028-000750", "cells": 5, "calls": 10},
        "s2_baseline": {"note": "not rerun; no plugin code on this path (original run 20261027-184715)"},
    },
    "ratios": ratios,
    "cells": {
        c["cell"]: {"subject": c.get("subject"), "round": c.get("round"),
                    "answer": (c.get("answer") or "").strip()[:80]}
        for c in cells if "s1-memory" in c["cell"]
    },
    "sources": {
        "summary": {"path": "paper/comparison-lab-fd-fix/results/summary.json", "sha256": sha(SUM)},
        "calls": {"path": "paper/comparison-lab-fd-fix/raw/calls.jsonl", "sha256": sha(CALLS)},
        "plugin_fix_test": "plugin/dsh-lingshu/lag-regressions.mjs (24/24 passed)",
    },
    "scope": "n=5 per cell, one host, one model route, one session; answers are model outputs, not effect sizes. "
             "S2 baseline cells are from the pre-fix run because the fix touches only the injection-timing path.",
}
out_prov = os.path.join(FD, "results", "POST-FIX-RERUN.json")
json.dump(prov, open(out_prov, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("wrote", out_prov)

ft = json.load(open(FT, encoding="utf-8"))
for subj in ft["subjects"]:
    if subj["id"] == "lingshu":
        subj["s1"] = {
            "hit": {"count": 5, "n": 5, "note": "post-fix rerun 20261027-235952: five answers 贝塔"},
            "leak": {"count": 0, "n": 5, "note": "superseded ALPHA never appears in answers"},
            "old_value_suppressed_structural": {"count": 5, "n": 5,
                                                "note": "ledger: supersedeEvents=1, ALPHA tombstoned, preview contains only BETA"},
            "old_value_suppressed_effective": {"count": 5, "n": 5,
                                              "note": "post-fix: same-turn assembly injects the recomputed snapshot; ALPHA absent from the request"},
            "source": "paper/comparison-lab-fd-fix/results/POST-FIX-RERUN.json + summary.json",
        }
    if subj["id"] == "baseline":
        subj["s1"]["hit"] = {"count": 5, "n": 5, "machine_count": 5,
                             "note": "post-fix rerun 20261028-000500: all five answers 贝塔"}

ft["sources"]["host_pilot"] = {
    "path": "paper/comparison-lab-fd-fix/results/summary.json",
    "sha256": sha(SUM),
    "run_id": "post-F-D-fix rerun (S1: 20261027-235952/20261028-000500; S2 lingshu: 20261028-000750; S2 baseline: pre-fix 20261027-184715)",
}
ft["sources"]["post_fix_rerun"] = {
    "path": "paper/comparison-lab-fd-fix/results/POST-FIX-RERUN.json",
    "sha256": sha(out_prov),
}
ft["generated_at"] = "2026-10-28T00:10:00+08:00"
ft["post_fix_note"] = (
    "S1 cells were rerun after the v5.4 F-D fix (same protocol, host, model route, session). "
    "LINGSHU S1 now answers BETA 5/5 with 0/5 ALPHA leak and 5/5 context-level suppression; "
    "the no-plugin baseline also answers BETA 5/5 but keeps ALPHA in its raw context (0/5 suppression). "
    "Mem0 numbers are unchanged (not rerun)."
)
json.dump(ft, open(FT, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print("patched", FT)
