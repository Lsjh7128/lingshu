# -*- coding: utf-8 -*-
"""DEPRECATED: superseded by ``build_final_table.py``.

The canonical table numbers now live in
``paper/comparison-lab/results/final-table.json`` (schema v2, with provenance
hashes, n/i markers and footnotes). The earlier generator in this file produced
a schema-v1 table from the pre-canonical host summary and must not be used.

Use:

    python paper/comparison-lab/build_final_table.py   # validate table vs sources

To regenerate the LaTeX body, edit ``paper/tables/baselines.tex`` explicitly and
re-run the validator; table numbers are deliberately not generated from prose.
"""
raise SystemExit(
    "make_table.py is deprecated -- use build_final_table.py "
    "(canonical numbers: results/final-table.json)"
)
