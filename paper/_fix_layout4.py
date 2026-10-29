# -*- coding: utf-8 -*-
import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def patch(rel, pairs):
    p = os.path.join(ROOT, rel)
    s = open(p, encoding="utf-8").read()
    for old, new in pairs:
        if old in s:
            s = s.replace(old, new, 1)
            print("OK  ", rel)
        else:
            print("MISS", rel, "|", old[:50].replace("\n", " "))
    open(p, "w", encoding="utf-8", newline="\n").write(s)


patch("sections/06-experiments.tex", [
    (r"""\begin{tabular}{@{}l l l l@{}}
\toprule
Fault & Injected mode & Control-plane observation & Key numbers \\""",
     r"""\begin{tabular}{@{}l l p{0.36\textwidth} p{0.24\textwidth}@{}}
\toprule
Fault & Injected mode & Control-plane observation & Key numbers \\"""),
])

patch("sections/06b-supplement.tex", [
    (r"""\begin{table}[t]
\centering\footnotesize
\caption{Held-out jailbreak evaluation (S2).""",
     r"""\begin{table*}[t]
\centering\footnotesize
\caption{Held-out jailbreak evaluation (S2)."""),
    (r"""\texttt{comparison.\allowbreak{}ji\_\allowbreak{}held.\allowbreak{}*}; word-list hashes recorded in the same file.}
\label{tab:heldout}""",
     r"""\texttt{comparison.*}; word-list hashes in the same file.}
\label{tab:heldout}"""),
])
