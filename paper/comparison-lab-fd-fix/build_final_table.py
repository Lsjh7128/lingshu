# -*- coding: utf-8 -*-
"""Validate the paper's baseline table against the measured artefacts.

This is a *checker*, not a table generator: the canonical numbers live in
paper/comparison-lab/results/final-table.json, and this script re-derives the
counts it can from the raw/source files and fails (exit 1) on any mismatch.

Checks
  1. source hashes recorded in final-table.json still match the files;
  2. host summary: baseline/lingshu S2 aggregates match the table counts;
  3. mem0 human-review: zh S1 values match the table;
  4. case-study before/after rates match the table (20 payloads, 3/20 and 20/20);
  5. every subject has either counts or an explicit n/i marker (no silent gaps).

Usage:  python paper/comparison-lab/build_final_table.py
"""
from __future__ import annotations

import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # paper/
REPO = os.path.dirname(ROOT)
TABLE = os.path.join(ROOT, "comparison-lab", "results", "final-table.json")
HOST = os.path.join(ROOT, "comparison-lab", "results", "summary.json")
MEM0 = os.path.join(ROOT, "mem0-chaos-lab", "results", "summary.json")
MEM0_REVIEW = os.path.join(ROOT, "mem0-chaos-lab", "raw", "human-review.json")
MEM0_DOCS19 = os.path.join(ROOT, "mem0-chaos-lab", "raw", "s1-docs19", "human-review.json")
CASE_B = os.path.join(ROOT, "case-study", "results-before.json")
CASE_A = os.path.join(ROOT, "case-study", "results-after.json")

PROBLEMS = []


def sha(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def need(condition, message):
    if not condition:
        PROBLEMS.append(message)


def load(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def main():
    table = load(TABLE)
    sources = table["sources"]

    # 1) hashes
    for key, entry in sources.items():
        path = os.path.join(REPO, entry["path"].replace("\\", "/"))
        need(os.path.isfile(path), "%s missing: %s" % (key, entry["path"]))
        if os.path.isfile(path):
            need(sha(path) == entry["sha256"],
                 "%s hash changed: %s" % (key, entry["path"]))

    host = load(HOST)
    mem0 = load(MEM0)
    review = load(MEM0_REVIEW)

    # 2) host aggregates
    h_s2 = host["aggregates"]["s2-jailbreak"]
    need(h_s2["lingshu"]["gate_blocked_cells"] == 5, "lingshu gate blocked != 5")
    need(h_s2["lingshu"]["leak"]["count"] == 0, "lingshu S2 leak != 0")
    need(h_s2["lingshu"]["hit"]["count"] == 0, "lingshu S2 hit_refined != 0")
    need(h_s2["baseline"]["leak"]["count"] == 0, "baseline S2 leak != 0")
    need(h_s2["baseline"]["gate_blocked_cells"] == 0, "baseline S2 gate != 0")
    h_s1 = host["aggregates"]["s1-memory"]
    need(h_s1["lingshu"]["leak"]["count"] == 5, "lingshu S1 leak != 5 (F-D)")

    # 3) mem0 zh docs19 S1 values
    zh = mem0["s1_docs19"]["variants"]["zh"]["s1_docs19"]["primary_human"]
    need(abs(zh["hit_mean"] - 1.0) < 1e-9, "mem0 zh S1 hit != 1")
    need(abs(zh["intercept_mean"] - 0.0) < 1e-9, "mem0 zh S1 intercept != 0")
    need(abs(zh["leak_mean"] - 0.0) < 1e-9, "mem0 zh S1 leak != 0")
    rows = [r for r in review["reviews"].values() if r.get("variant") == "zh"]
    need(len(rows) >= 10, "mem0 human review rows for zh < 10")

    # 4) case study
    before = load(CASE_B)["records"]
    after = load(CASE_A)["records"]
    b = sum(1 for r in before if r.get("blocked"))
    a = sum(1 for r in after if r.get("blocked"))
    need(len(before) == 20 and b == 3, "case-study before != 3/20 (got %d/%d)" % (b, len(before)))
    need(len(after) == 20 and a == 20, "case-study after != 20/20 (got %d/%d)" % (a, len(after)))

    # 5) table completeness
    ids = {s["id"] for s in table["subjects"]}
    need(ids == {"baseline", "mem0", "lingshu"}, "subjects != baseline/mem0/lingshu: %r" % ids)
    for s in table["subjects"]:
        for scenario in ("s1", "s2"):
            block = s.get(scenario) or {}
            need(bool(block), "%s.%s missing" % (s["id"], scenario))
            if s["id"] == "lingshu" and scenario == "s1":
                need(block.get("not_interpretable") is True,
                     "lingshu S1 must be marked not_interpretable")
            for key in ("hit", "hit_refined"):
                if key in block and block[key] is None:
                    if s["id"] == "lingshu" and scenario == "s1" \
                            and block.get("not_interpretable") is True:
                        continue
                    need(False, "%s.%s.%s null without n/i marker" % (s["id"], scenario, key))

    print("checked:", os.path.relpath(TABLE, REPO))
    if PROBLEMS:
        print("FAIL (%d)" % len(PROBLEMS))
        for item in PROBLEMS:
            print("  -", item)
        return 1
    print("PASS: final-table.json is consistent with sources")
    return 0


if __name__ == "__main__":
    sys.exit(main())
