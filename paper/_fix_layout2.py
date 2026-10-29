# -*- coding: utf-8 -*-
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

EQ2_OLD = r"""\begin{align}
B_i &= \ln\!\Big(\sum_{j \in \text{last }64} (t_{\mathrm{now}} - t_j + \rho)^{-d}\Big),
& n(B_i) &= \mathrm{clip}\!\Big(\frac{B_i - l}{h - l},\,0,\,1\Big),\\
\tau_i^{\mathrm{eff}} &= \tau_{\mathrm{type}(i)}\,(0.5 + \mathrm{imp}_i),
& p_i &= \mathrm{clip}\Big(\max\big(\mathrm{imp}_i e^{-\Delta t_i/\tau_i^{\mathrm{eff}}},\;
w \cdot \mathrm{clip}(\mathrm{imp}_i\, n(B_i),0,1)\big),\,0,\,1\Big),
\end{align}"""
EQ2_NEW = r"""\begin{equation}
\resizebox{0.98\linewidth}{!}{$\begin{aligned}
B_i &= \ln\!\Big(\sum_{j \in \text{last }64} (t_{\mathrm{now}} - t_j + \rho)^{-d}\Big),
& n(B_i) &= \mathrm{clip}\!\Big(\frac{B_i - l}{h - l},\,0,\,1\Big),\\
\tau_i^{\mathrm{eff}} &= \tau_{\mathrm{type}(i)}\,(0.5 + \mathrm{imp}_i),
& p_i &= \mathrm{clip}\Big(\max\big(\mathrm{imp}_i e^{-\Delta t_i/\tau_i^{\mathrm{eff}}},\;
w \cdot \mathrm{clip}(\mathrm{imp}_i\, n(B_i),0,1)\big),\,0,\,1\Big),
\end{aligned}$}
\end{equation}"""


def patch(rel, pairs):
    p = os.path.join(ROOT, rel)
    s = open(p, encoding="utf-8").read()
    for old, new in pairs:
        if old in s:
            s = s.replace(old, new, 1)
            print("OK  ", rel)
        else:
            print("MISS", rel, old[:40].replace("\n", " "))
    open(p, "w", encoding="utf-8", newline="\n").write(s)


patch("sections-zh/05-method.tex", [(EQ2_OLD, EQ2_NEW)])
patch("sections/06b-supplement.tex", [
    (r"fields \texttt{sets.v1|v2.ji\_held.*}, \texttt{ji\_fp.overall.*}, "
     r"\texttt{comparison.ji\_held.*}; word-list hashes recorded in the same file.}",
     r"fields \texttt{sets.*}, \texttt{ji\_fp.*}; word-list hashes in the same file.}"),
])
