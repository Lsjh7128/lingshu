# -*- coding: utf-8 -*-
"""Apply the same layout fixes to the Chinese section files."""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def patch(rel, pairs, required=True):
    p = os.path.join(ROOT, rel)
    s = open(p, encoding="utf-8").read()
    miss = []
    for old, new in pairs:
        if old in s:
            s = s.replace(old, new, 1)
        else:
            miss.append(old[:50].replace("\n", " "))
    open(p, "w", encoding="utf-8", newline="\n").write(s)
    if miss:
        print("MISS", rel, miss)
    else:
        print("OK  ", rel)


EQ1_OLD = r"""\begin{equation}
w_{\mathrm{fact}} = 0.70 + 0.50\,\mathbf{1}[\mathrm{refuse\_claim}],\quad
w_{\mathrm{org}} = 0.50 + 0.35\,\mathbf{1}[\mathrm{goal\_blocked}],\quad
w_{\mathrm{style}} = 0.40 + 0.40\,\mathbf{1}[\mathrm{intensity} \ge \theta_s].
\end{equation}"""
EQ1_NEW = r"""\begin{equation}
\resizebox{0.98\linewidth}{!}{$\displaystyle
w_{\mathrm{fact}} = 0.70 + 0.50\,\mathbf{1}[\mathrm{refuse\_claim}],\quad
w_{\mathrm{org}} = 0.50 + 0.35\,\mathbf{1}[\mathrm{goal\_blocked}],\quad
w_{\mathrm{style}} = 0.40 + 0.40\,\mathbf{1}[\mathrm{intensity} \ge \theta_s].
$}
\end{equation}"""

EQ3_OLD = r"""\begin{equation}
r = \begin{cases}
0, & W \le 0 \quad \text{(no history update, phase unknown)}\\
\max(0, \min(r_{\max}, u/W)), & W > 0
\end{cases}
\qquad
\phi = \begin{cases}
\text{critical}, & r \ge \alpha_h\\
\text{shift}, & \alpha_s \le r < \alpha_h\\
\text{normal}, & r < \alpha_s
\end{cases}
\end{equation}"""
EQ3_NEW = r"""\begin{equation}
\resizebox{0.98\linewidth}{!}{$\displaystyle
r = \begin{cases}
0, & W \le 0 \quad \text{(no history update, phase unknown)}\\
\max(0, \min(r_{\max}, u/W)), & W > 0
\end{cases}
\qquad
\phi = \begin{cases}
\text{critical}, & r \ge \alpha_h\\
\text{shift}, & \alpha_s \le r < \alpha_h\\
\text{normal}, & r < \alpha_s
\end{cases}
$}
\end{equation}"""

EQ4_OLD = r"""\begin{equation}
\begin{aligned}
H_1 &= \exists\, w \in W_{\mathrm{override}}:\ w \subseteq p, &
H_2 &= \neg \mathrm{frozen\_ok}, &
H_3 &= \mathrm{refuse} \land \mathrm{style\_injected},\\
H_4 &= \exists\, w \in W_{\mathrm{unsourced}}:\ w \subseteq f, &
H_5 &= \neg \mathrm{constitution\_injected}, &
H_6 &= (\mathrm{conflict} = \texttt{unresolved}) \land \neg \mathrm{fallback\_applied},\\
H_7 &= \mathrm{prefeed} \land (\text{anchor marker} \not\subseteq p), &&
\end{aligned}
\end{equation}"""
EQ4_NEW = r"""\begin{equation}
\resizebox{0.98\linewidth}{!}{$\begin{aligned}
H_1 &= \exists\, w \in W_{\mathrm{override}}:\ w \subseteq p, &
H_2 &= \neg \mathrm{frozen\_ok}, &
H_3 &= \mathrm{refuse} \land \mathrm{style\_injected},\\
H_4 &= \exists\, w \in W_{\mathrm{unsourced}}:\ w \subseteq f, &
H_5 &= \neg \mathrm{constitution\_injected}, &
H_6 &= (\mathrm{conflict} = \texttt{unresolved}) \land \neg \mathrm{fallback\_applied},\\
H_7 &= \mathrm{prefeed} \land (\text{anchor marker} \not\subseteq p), &&
\end{aligned}$}
\end{equation}"""

patch("sections-zh/05-method.tex", [(EQ1_OLD, EQ1_NEW), (EQ3_OLD, EQ3_NEW), (EQ4_OLD, EQ4_NEW)])

# zh appendix tables: narrower/monospace-safe column widths + smaller font
patch("sections-zh/10-appendix.tex", [
    (r"\centering\footnotesize", r"\centering\scriptsize\setlength{\tabcolsep}{2.5pt}"),
    (r"@{}p{0.25\textwidth}p{0.42\textwidth}p{0.18\textwidth}p{0.11\textwidth}@{}",
     r"@{}p{0.24\textwidth}p{0.40\textwidth}p{0.16\textwidth}p{0.14\textwidth}@{}"),
    (r"@{}p{0.36\textwidth}p{0.34\textwidth}p{0.10\textwidth}p{0.12\textwidth}@{}",
     r"@{}p{0.34\textwidth}p{0.36\textwidth}p{0.10\textwidth}p{0.12\textwidth}@{}"),
])

patch("sections-zh/10c-appendix-tool.tex", [
    (r"""\small
\begin{tabular}{llp{4.0cm}lp{1.7cm}l}""",
     r"""\scriptsize
\begin{tabular}{llp{3.6cm}lp{2.2cm}l}"""),
])

patch("sections-zh/10b-appendix-audit.tex", [
    (r"""\begin{tabular}{lrrrr}
\toprule
Quantity & Python & JavaScript & Sample & Note \\""",
     r"""\begin{tabular}{@{}l r r r p{0.28\textwidth}@{}}
\toprule
Quantity & Python & JavaScript & Sample & Note \\"""),
], required=False)
