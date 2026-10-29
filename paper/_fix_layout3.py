# -*- coding: utf-8 -*-
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))


def patch(rel, pairs):
    p = os.path.join(ROOT, rel)
    s = open(p, encoding="utf-8").read()
    for old, new in pairs:
        if old in s:
            s = s.replace(old, new, 1)
            print("OK  ", rel, "|", old[:40].replace("\n", " "))
        else:
            print("MISS", rel, "|", old[:40].replace("\n", " "))
    open(p, "w", encoding="utf-8", newline="\n").write(s)


# zh 06: shrink ablation table; make chaos matrix columns wrap
patch("sections-zh/06-experiments.tex", [
    (r"""\begin{table*}[t]
\centering\footnotesize
\caption{三臂消融。""",
     r"""\begin{table*}[t]
\centering\scriptsize\setlength{\tabcolsep}{2pt}
\caption{三臂消融。"""),
    (r"""\begin{tabular}{@{}l l l l@{}}""",
     r"""\begin{tabular}{@{}l l p{0.40\textwidth} p{0.24\textwidth}@{}}"""),
    (r"""\begin{table*}[t]
\centering\footnotesize
\caption{故障注入矩阵。""",
     r"""\begin{table*}[t]
\centering\scriptsize\setlength{\tabcolsep}{2pt}
\caption{故障注入矩阵。"""),
])

# zh 10b: shrink table (a)
patch("sections-zh/10b-appendix-audit.tex", [
    (r"""\small
\begin{tabular}{lrrrrl}""",
     r"""\scriptsize\setlength{\tabcolsep}{2.5pt}
\begin{tabular}{lrrrrl}"""),
])

# zh 10c: tighter columns
patch("sections-zh/10c-appendix-tool.tex", [
    (r"""\scriptsize
\begin{tabular}{llp{3.6cm}lp{2.2cm}l}""",
     r"""\scriptsize\setlength{\tabcolsep}{2.5pt}
\begin{tabular}{llp{3.6cm}lp{2.2cm}l}"""),
])

# zh main: sloppy paragraphs so long English identifiers do not overflow
p = os.path.join(ROOT, "main-zh.tex")
s = open(p, encoding="utf-8").read()
if r"\sloppy" not in s:
    s = s.replace(r"\frenchspacing", "\\frenchspacing\n\\sloppy\n\\setlength{\\emergencystretch}{2em}", 1)
    open(p, "w", encoding="utf-8", newline="\n").write(s)
    print("added sloppy to main-zh.tex")
