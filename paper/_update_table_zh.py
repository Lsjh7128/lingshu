# -*- coding: utf-8 -*-
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

# 1) fix the S2 tail of the English generated table
p = os.path.join(ROOT, "tables", "baselines.tex")
s = open(p, encoding="utf-8").read()
old_tail = (r"S2 canary never reaches the model in the LINGSHU arm" )
tail_old = (
    r"""S2 canary never reaches the model in the LINGSHU arm
because the fact extractor rejects the secret-bearing sentence and the gate
blocks the attack turn; all three arms refuse the obvious payload, so no
model-refusal difference is claimed."""
)
tail_new = (
    r"""In S2 the canary never reaches the model in the LINGSHU arm: the fact
extractor rejects the secret-bearing sentence and the gate blocks the attack
turn. All three arms contain refusal language, so a refusal rate does not
separate them; what separates them is that the baseline's refusal can still
quote the canary (2/5), while the gate leaves nothing to quote."""
)
assert tail_old in s, "tail anchor missing"
s = s.replace(tail_old, tail_new, 1)
open(p, "w", encoding="utf-8", newline="\n").write(s)
print("patched EN table tail")

# 2) Chinese version of the same table
zh = r"""% 由 paper/comparison-lab/results/final-table.json 生成的中文版（数字与英文版逐格一致）
\begin{table*}[t]
\centering\footnotesize
\caption{三受试者试点（每个单元格 $n=5$ 个独立轮次；同一宿主、同一模型路由、同一天）。
S1 = 记忆冲突（ALPHA$\rightarrow$BETA）；S2 = 越狱/注入。
\emph{Hit}：答出当前值 / 攻击成功（经人工复核）；\emph{Leak}：泄漏被取代的值或被植入的令牌；
\emph{Sup.}：被取代的值不出现在系统注入的内容里；\emph{Int.}：模型层面拒绝；
\emph{gate}/\emph{audit}：结构性 UCA-01 阻断及其审计记录。
Mem0 显示的是其"召回可用"的诊断配置（见默认配置说明）；其默认行因本环境中嵌入通路为空而未列入主表。
来源：\texttt{paper/comparison-lab/results/final-table.json}。}
\label{tab:baselines-zh}
\begin{tabular}{@{}l ccc ccc ccc@{}}
\toprule
 & \multicolumn{3}{c}{S1 记忆冲突} & \multicolumn{3}{c}{S2 越狱} & \multicolumn{3}{c}{S2 结构性} \\
\cmidrule(lr){2-4}\cmidrule(lr){5-7}\cmidrule(lr){8-10}
受试者 & Hit & Leak & Sup. & Hit & Leak & Int. & Blocked & Audit & Refused \\
\midrule
Baseline（无插件） & 5/5 & 0/5 & 0/5 & 2/5 & 2/5 & 5/5 & 0/5 & n/a & 5/5 \\
Mem0 2.2.1（中文诊断） & 5/5 & 0/5 & 0/5 & 0/5 & 0/5 & 5/5 & 0/5 & n/a & 5/5 \\
LINGSHU（实验 profile） & 5/5 & 0/5 & 5/5 & 0/5 & 0/5 & 5/5 & 5/5 & 5/5 & 5/5 \\
\bottomrule
\end{tabular}

\vspace{2pt}
\raggedright\scriptsize
S1 对三个受试者使用 docs/19 支持的纠正句式。所有宿主单元格在 v5.4 同回合注入修复
（发现 F-D）之后于 2026-10-27/28 重跑；修复前账本已在 5/5 个单元格里给 ALPHA 立了墓碑，
但模型第一次请求仍携带上一轮快照，因此回答是陈旧的。修复后结构性抑制与有效抑制都是 5/5：
ALPHA 不出现在账本里，也不出现在请求里，模型 5/5 答出 BETA。Baseline 与 Mem0 把 ALPHA 留在
自己的上下文里（Sup.~0/5），尽管两者也都 5/5 答出 BETA；因此 S1 这几列区分的是"系统注入了什么"，
而不是答题正确率。S2 的同一轮重跑给出了"拒绝"的反例：无插件宿主 5/5 都出现了拒绝语言，
但其中 2/5 在拒绝里把门禁码原样念了出来（Hit 2/5、Leak 2/5）。拒绝不等于不泄漏，
而且仅靠模型的拒绝是随机的：同一批单元格更早一次运行是 0/5 泄漏。LINGSHU 的闸门在两次运行里
都是 5/5 阻断，门禁码从未到达模型——这才是本表支持的结构性主张。
Mem0 默认配置抽取英文记忆文本；在本地非神经嵌入器下，中文查询召回为空
（余弦 $\le 0.063 < 0.1$），因此其默认 S1 的"抑制"是空召回假象，库中仍保留旧值（ADD-only 管道）。
S2 中门禁码在 LINGSHU 臂里从未到达模型：事实提取器在存储前就拒绝了含机密的句子，闸门又阻断了攻击轮。
三个受试者的回答里都出现拒绝语言，因此拒绝率无法区分它们；区分它们的是——baseline 的拒绝仍可能
把门禁码念出来（2/5），而闸门之后没有可念的内容。$n=5$：仅方向性，无置信区间；
修复后重跑的溯源见 \texttt{paper/comparison-lab-fd-fix/results/POST-FIX-RERUN.json}。
\end{table*}
"""
open(os.path.join(ROOT, "tables", "baselines-zh.tex"), "w", encoding="utf-8", newline="\n").write(zh)
print("wrote tables/baselines-zh.tex")

# 3) zh 06f uses the Chinese table
pz = os.path.join(ROOT, "sections-zh", "06f-baselines.tex")
sz = open(pz, encoding="utf-8").read()
assert r"\input{tables/baselines}" in sz, "zh table input not found"
sz = sz.replace(r"\input{tables/baselines}", r"\input{tables/baselines-zh}", 1)
open(pz, "w", encoding="utf-8", newline="\n").write(sz)
print("zh 06f now inputs baselines-zh")
