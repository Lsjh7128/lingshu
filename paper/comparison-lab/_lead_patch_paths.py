# -*- coding: utf-8 -*-
"""Patch paths of _lead_fresh_rerun.py to a writable workspace area."""
import io
import os

path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_lead_fresh_rerun.py")
with io.open(path, encoding="utf-8") as f:
    text = f.read()

text = text.replace(
    'FRESH = os.path.join(RAW, "fresh-cells")',
    'FRESH = os.path.join(PAPER, "_lead_tmp", "fresh-cells")')
text = text.replace(
    'HOMES = os.path.join(LAB, ".runtime", "fresh-homes")',
    'HOMES = os.path.join(PAPER, "_lead_tmp", "fresh-homes")')
text = text.replace(
    'CWDS = os.path.join(LAB, ".runtime", "fresh-cwds")',
    'CWDS = os.path.join(PAPER, "_lead_tmp", "fresh-cwds")')
text = text.replace(
    'OUT = os.path.join(LAB, "results", "lead-fresh-cells.json")',
    'OUT = os.path.join(PAPER, "_lead_tmp", "lead-fresh-cells.json")')

with io.open(path, "w", encoding="utf-8", newline="\n") as f:
    f.write(text)
print("patched")
