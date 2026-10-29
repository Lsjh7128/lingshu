# -*- coding: utf-8 -*-
"""Final2 fixes: multiplicative dot in eq (5); Chinese wording/localisation; table spacing."""
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))


def rd(rel):
    return open(os.path.join(ROOT, rel), encoding="utf-8").read()


def wr(rel, s):
    open(os.path.join(ROOT, rel), "w", encoding="utf-8", newline="\n").write(s)


def sub(rel, old, new, count=1):
    s = rd(rel)
    n = s.count(old)
    if n == 0:
        print("MISS", rel, "|", old[:50].replace("\n", " "))
        return False
    s = s.replace(old, new, count)
    wr(rel, s)
    print("OK  ", rel, "|", old[:40].replace("\n", " "))
    return True


# 1) eq (5): explicit multiplication dot before the indicator
for f in ("sections/05-method.tex", "sections-zh/05-method.tex"):
    s = rd(f)
    s2 = s.replace(r"\,\mathbf{1}[", r"\cdot\mathbf{1}[")
    if s2 != s:
        wr(f, s2)
        print("OK  ", f, "| multiplication dot added")
    else:
        print("MISS", f, "| multiplication dot")

# 2) Chinese wording: "大声失败"
sub("sections-zh/02-intro.tex",
    "契约被破坏时它们会大声失败：",
    "契约被破坏时它们会立刻报错（显式抛出异常）：")
sub("sections-zh/09-conclusion.tex",
    "一份可执行且会大声失败的契约",
    "一份可执行、失败即显式报错的契约")
sub("sections-zh/09-conclusion.tex",
    "契约会执行、并会大声失败；",
    "契约会执行、并在失败时显式抛出异常；")

# 3) Chinese localisation of the four failure predicates
sub("sections-zh/04-problem.tex",
    r"""F_1 &: \text{fact block asserts an unsourced memory statement},\\
F_2 &: \mathit{refuse}_t \wedge \text{a style block is present},\\
F_3 &: \mathit{prefeed}_t \wedge \text{no anchor marker in the prompt},\\
F_4 &: \text{the constitutional block is absent or not first}.""",
    r"""F_1 &: \text{事实块断言了无来源的记忆陈述},\\
F_2 &: \mathit{refuse}_t \wedge \text{存在风格块},\\
F_3 &: \mathit{prefeed}_t \wedge \text{prompt 中无锚点标记},\\
F_4 &: \text{宪法块缺失或不在首位}.""")

# 4) table spacing
# 4a EN ablation table: normal font, wider columns, scaled to width
s = rd("sections/06-experiments.tex")
s = s.replace(r"\centering\scriptsize\setlength{\tabcolsep}{2pt}" + "\n" + r"\caption{Three-arm ablation.",
              r"\centering\footnotesize\setlength{\tabcolsep}{5pt}" + "\n" + r"\caption{Three-arm ablation.", 1)
m = re.search(r"\\begin\{tabular\}\{@\{\}l cccc c c c c c ccc c@\{\}\}(.*?)\\end\{tabular\}", s, re.S)
if m:
    s = s[:m.start()] + r"\resizebox{\textwidth}{!}{" + m.group(0) + "}" + s[m.end():]
wr("sections/06-experiments.tex", s)
print("OK   06-experiments EN table 1 squared")

# 4b ZH ablation table
s = rd("sections-zh/06-experiments.tex")
s = s.replace(r"\centering\scriptsize\setlength{\tabcolsep}{2pt}" + "\n" + r"\caption{三臂消融。",
              r"\centering\footnotesize\setlength{\tabcolsep}{5pt}" + "\n" + r"\caption{三臂消融。", 1)
m = re.search(r"\\begin\{tabular\}\{@\{\}l cccc c c c c c ccc c@\{\}\}(.*?)\\end\{tabular\}", s, re.S)
if m:
    s = s[:m.start()] + r"\resizebox{\textwidth}{!}{" + m.group(0) + "}" + s[m.end():]
# chaos table: a little more column separation
s = s.replace(r"\centering\scriptsize\setlength{\tabcolsep}{2pt}" + "\n" + r"\caption{故障注入矩阵。",
              r"\centering\footnotesize\setlength{\tabcolsep}{3pt}" + "\n" + r"\caption{故障注入矩阵。", 1)
wr("sections-zh/06-experiments.tex", s)
print("OK   zh 06 tables squared")

# 4c mechanism ablation Table 3 (EN + ZH): wider last column, more column gap
for rel, cap in (("sections/06b-supplement.tex", r"\caption{Mechanism-level ablation"),
                 ("sections-zh/06b-supplement.tex", r"\caption{机制级消融")):
    s = rd(rel)
    s = s.replace(r"\centering\footnotesize" + "\n" + cap,
                  r"\centering\footnotesize\setlength{\tabcolsep}{4pt}" + "\n" + cap, 1)
    s = s.replace(r"@{}l cccc c p{0.42\textwidth}@{}", r"@{}l cccc c p{0.46\textwidth}@{}", 1)
    wr(rel, s)
    print("OK   ", rel, "table 3 spacing")

# 4d Table 6 (EN + ZH): wider column gap
for rel in ("tables/baselines.tex", "tables/baselines-zh.tex"):
    s = rd(rel)
    s = s.replace(r"\centering\footnotesize" + "\n" + r"\caption{",
                  r"\centering\footnotesize\setlength{\tabcolsep}{8pt}" + "\n" + r"\caption{", 1)
    wr(rel, s)
    print("OK   ", rel, "tabcolsep 8pt")

# 5) global row height
s = rd("main-zh.tex")
if r"\renewcommand{\arraystretch}" not in s:
    s = s.replace(r"\frenchspacing", "\\frenchspacing\n\\renewcommand{\\arraystretch}{1.3}", 1)
    wr("main-zh.tex", s)
    print("OK   main-zh arraystretch 1.3")
s = rd("main.tex")
if r"\renewcommand{\arraystretch}" not in s:
    s = s.replace(r"\frenchspacing", "\\frenchspacing\n\\renewcommand{\\arraystretch}{1.15}", 1)
    wr("main.tex", s)
    print("OK   main arraystretch 1.15")
