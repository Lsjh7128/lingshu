# -*- coding: utf-8 -*-
"""Re-typeset the four wide method equations as line-broken, normal-size math."""
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))

EQ1 = r"""{\small
\begin{equation}
\begin{aligned}
w_{\mathrm{fact}} &= 0.70 + 0.50\,\mathbf{1}[\mathrm{refuse\_claim}],\\
w_{\mathrm{org}} &= 0.50 + 0.35\,\mathbf{1}[\mathrm{goal\_blocked}],\\
w_{\mathrm{style}} &= 0.40 + 0.40\,\mathbf{1}[\mathrm{intensity} \ge \theta_s].
\end{aligned}
\end{equation}
}"""

EQ2 = r"""{\small
\begin{equation}
\begin{aligned}
B_i &= \ln\!\Big(\sum_{j \in \text{last }64} (t_{\mathrm{now}} - t_j + \rho)^{-d}\Big),\\
n(B_i) &= \mathrm{clip}\!\Big(\frac{B_i - l}{h - l},\,0,\,1\Big),\\
\tau_i^{\mathrm{eff}} &= \tau_{\mathrm{type}(i)}\,(0.5 + \mathrm{imp}_i),\\
p_i &= \mathrm{clip}\Big(\max\big(\mathrm{imp}_i e^{-\Delta t_i/\tau_i^{\mathrm{eff}}},\\
&\qquad w \cdot \mathrm{clip}(\mathrm{imp}_i\, n(B_i),0,1)\big),\,0,\,1\Big).
\end{aligned}
\end{equation}
}"""

EQ3 = r"""{\small
\begin{equation}
\begin{aligned}
r &= \begin{cases}
0, & W \le 0 \text{ (no update, phase unknown)},\\
\max(0, \min(r_{\max}, u/W)), & W > 0;
\end{cases}\\
\phi &= \begin{cases}
\text{critical}, & r \ge \alpha_h,\\
\text{shift}, & \alpha_s \le r < \alpha_h,\\
\text{normal}, & r < \alpha_s.
\end{cases}
\end{aligned}
\end{equation}
}"""

EQ4 = r"""{\small
\begin{equation}
\begin{aligned}
H_1 &= \exists\, w \in W_{\mathrm{override}}:\ w \subseteq p,\\
H_2 &= \neg \mathrm{frozen\_ok},\qquad
H_3 = \mathrm{refuse} \land \mathrm{style\_injected},\\
H_4 &= \exists\, w \in W_{\mathrm{unsourced}}:\ w \subseteq f,\\
H_5 &= \neg \mathrm{constitution\_injected},\\
H_6 &= (\mathrm{conflict} = \texttt{unresolved}) \land \neg \mathrm{fallback\_applied},\\
H_7 &= \mathrm{prefeed} \land (\text{anchor marker} \not\subseteq p).
\end{aligned}
\end{equation}
}"""

NEW = [EQ1, EQ2, EQ3, EQ4]


def rewrite(path):
    s = open(path, encoding="utf-8").read()
    blocks = list(re.finditer(r"\\begin\{equation\}.*?\\end\{equation\}", s, flags=re.S))
    targets = [b for b in blocks if "resizebox" in b.group(0)]
    print(os.path.basename(path), "equation blocks:", len(blocks), "resizebox blocks:", len(targets))
    if len(targets) != 4:
        print("WARNING: expected 4 resizebox equations, got", len(targets))
        return
    out = []
    last = 0
    for t, new in zip(targets, NEW):
        out.append(s[last:t.start()])
        out.append(new)
        last = t.end()
    out.append(s[last:])
    open(path, "w", encoding="utf-8", newline="\n").write("".join(out))
    print("rewrote", os.path.basename(path))


rewrite(os.path.join(ROOT, "sections", "05-method.tex"))
rewrite(os.path.join(ROOT, "sections-zh", "05-method.tex"))
