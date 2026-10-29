# -*- coding: utf-8 -*-
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

OLD = r"""r &= \begin{cases}
0, & W \le 0 \text{ (no update, phase unknown)},\\
\max(0, \min(r_{\max}, u/W)), & W > 0;
\end{cases}\\"""
NEW = r"""r &= \begin{cases}
0, & W \le 0,\\
\max(0, \min(r_{\max}, u/W)), & W > 0;
\end{cases}\\"""

TAIL_EN = r"""\end{equation}
}
with $\alpha_s=0.70$"""
TAIL_ZH = r"""\end{equation}
}
其中 $\alpha_s=0.70$"""


def patch(path, tail_old, tail_new):
    s = open(path, encoding="utf-8").read()
    n = 0
    if OLD in s:
        s = s.replace(OLD, NEW, 1)
        n += 1
    if tail_old in s:
        s = s.replace(tail_old, tail_new, 1)
        n += 1
    print(os.path.basename(path), "patches:", n)
    open(path, "w", encoding="utf-8", newline="\n").write(s)


patch(os.path.join(ROOT, "sections", "05-method.tex"), TAIL_EN,
      r"""\end{equation}
}
When $W \le 0$ there is no history update and the phase is unknown; with $\alpha_s=0.70$""")
patch(os.path.join(ROOT, "sections-zh", "05-method.tex"), TAIL_ZH,
      r"""\end{equation}
}
当 $W \le 0$ 时不更新历史、相位未知；其中 $\alpha_s=0.70$""")

# zh appendix params: widen shadow column slightly
p = os.path.join(ROOT, "sections-zh", "10-appendix.tex")
s = open(p, encoding="utf-8").read()
s = s.replace(r"@{}p{0.24\textwidth}p{0.40\textwidth}p{0.16\textwidth}p{0.14\textwidth}@{}",
              r"@{}p{0.24\textwidth}p{0.39\textwidth}p{0.16\textwidth}p{0.15\textwidth}@{}", 1)
open(p, "w", encoding="utf-8", newline="\n").write(s)
print("zh params columns adjusted")
