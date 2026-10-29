# -*- coding: utf-8 -*-
"""Chinese-side updates for F-D fix + immersive case study + post-fix S1 numbers."""
import os

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


# ---- 05-method: same-turn delivery + real-host validation ----
same_turn = r"""\paragraph{同回合投递（v5.4）。} 相位状态必须进入携带临界比率的那次 prompt，而不是下一次。宿主在组装 system prompt 时求值七个段落回调，之后才触发 \texttt{agent/pre-step}，因此写在该钩子里的状态更新会晚一次拼装（发现 F-D，初稿实验中观测到）。v5.4 插件改为在 \texttt{agent/inbox/claimed} 上预计算本回合状态——在首个段落被求值之前——并用同一状态重写段落文本，段落数与顺序保持不变。真实的 pre-step 随后只补写审计记录；轮次计数与幂等守卫防止重复积分。若预计算失败，路径回退到旧的“仅 pre-step”行为并记录 \texttt{anchor\_lag=true}，而不是静默降级。修复由独立回归套件钉住（\texttt{lag-regressions.mjs}，24/24）：它复现宿主的真实钩子时序，并断言 \texttt{anchor\_lag=false}、同回合进入 \texttt{critical}、以及段落数不变。

"""

real_host = r"""\paragraph{真实宿主验证（2026-10-27）。} 一次 headless 运行在真实宿主进程中检验了该路径（run \texttt{immersive-host-20261027-202526}，实验 profile；desktop profile 未触碰）。提示词把助手设定为贴身管家“阿福”，并要求它扮演黑心商人、教用户怎么卖假货。闸门触发了：一条 \texttt{preflight\_block} 审计行记录 UCA-01、\texttt{STPA\_Blocked=true}、\texttt{severity=soft}、\texttt{safe\_redirect=true}、\texttt{emotion=警觉}、风险档 \texttt{mild}、\texttt{escalated=false}，裁定仍为 \texttt{block}。注入的 prompt 携带 \texttt{[System\_Notice: Safe\_Redirect]} 与 \texttt{[Emotion: 警觉]}，事实层硬拦截，表达层要求保持人设，且不含系统口吻的兜底标记。模型随后以人设内拒绝作答：“这桩事我做不了——卖假货骗人，不是阿福能接的活。换件正经事吩咐吧，您说。”前端流（stdout，108 字节）只含最终回复；宿主 stderr 为空（0 字节），因此该拒绝是模型对受约束 prompt 的表达，而不是宿主错误串。这是 $n=1$，措辞有随机性；它作为解耦机制的可达性演示报告，而不是体验评分。

"""

p05 = os.path.join(ROOT, "sections-zh", "05-method.tex")
s = open(p05, encoding="utf-8").read()
anchor_defect = r"""\paragraph{已知缺陷与代价。} 这两个触发点不是滞回：没有下降沿阈值，"""
if anchor_defect in s:
    s = s.replace(anchor_defect, same_turn + anchor_defect, 1)
    print("OK   05-method same-turn inserted")
else:
    print("MISS 05-method same-turn anchor")

anchor_ushape = r"""\subsection{U 型注意力锚定}"""
if anchor_ushape in s:
    s = s.replace(anchor_ushape, real_host + anchor_ushape, 1)
    print("OK   05-method real-host inserted")
else:
    print("MISS 05-method real-host anchor")
open(p05, "w", encoding="utf-8", newline="\n").write(s)

# ---- 06f: post-fix S1 paragraph and tail note ----
patch("sections-zh/06f-baselines.tex", [
    (r"""有两点限定这一差异。第一，LINGSHU 在 S1 上的模型层回答在这里不可解释：
尽管预览只含 BETA，模型第一次请求使用的却是上一轮的注入快照，
因此五个回答全都是 ALPHA（发现 F-D）；一个在提问前多加一轮的 warm 诊断，
在 5/5 个单元格里答出 BETA。第二，事实提取器是一个规则桩：
它识别不了自然的纠正措辞（``现在改成''，发现 F-A），还会把最后那个问题当成事实存下来（发现 F-B）。
我们报告结构性成功与这些实现缺口，而不是报告一次干净的 S1 胜利。""",
     r"""S1 的单元格在 v5.4 注入时序修复（发现 F-D）之后重跑过。重跑补齐了账本与模型之间的缺口：
5/5 个单元格中，同回合的组装携带的是重算后的快照，被取代的值不出现在请求里（有效抑制 5/5），
模型 5/5 答出 BETA，且没有 ALPHA 泄漏。Baseline 与 Mem0 同样 5/5 答出 BETA，因此差异不在答题正确率，
而在系统把什么放到模型面前：它们把 ALPHA 留在上下文里（抑制 0/5），LINGSHU 没有。
仍有两个实现缺口并按此报告：事实提取器识别不了自然的纠正措辞（``现在改成''，发现 F-A），
还会把最后那个问题当成事实存下来（发现 F-B）。"""),
    (r"""这次试点还暴露了工具网关噪声（发现 F-C）与一轮注入滞后（F-D）；
两者都作为产品发现报告，而没有被折进比率里。""",
     r"""这次试点还暴露了工具网关噪声（发现 F-C）与一轮注入滞后（发现 F-D）；滞后被定位到宿主生命周期，
已在 v5.4 修复（第 4.7 节），上表的 S1 行来自修复后的重跑。两者都作为产品发现报告，而没有被折进比率里。"""),
])

# ---- 08-limitations: F-D fixed + immersive case paragraph ----
patch("sections-zh/08-limitations.tex", [
    (r"""以及一轮的首次模型请求使用的是上一轮的注入快照，因此一轮陈旧的记忆仍可能到达模型（F-D）。
最后一项使 S1 的模型层回答不可解释，已标记 n/i；账本层的 supersede 与一个 warm 诊断分别报告。""",
     r"""第四项，一轮注入滞后（F-D），是时序缺陷而不是设计极限：宿主在插件的 pre-step 状态更新之前就完成了拼装，
因此一轮的首次请求携带的是上一轮快照。它被定位到宿主生命周期，并在 v5.4 中通过以下方式修复：
在 \texttt{agent/inbox/claimed} 上预计算本回合状态、并重写首个被求值的段落；修复由 24 条独立回归断言钉住
（\texttt{lag-regressions.mjs}：真实钩子时序下 \texttt{anchor\_lag=false}、轮次幂等，以及保留旧路径作为回退）。
因此 S1 的模型层单元格改用修复后的重跑数据（见“外部基线”一节）。"""),
    (r"""\paragraph{合成数据与 mock 断言。} 回放集完全合成""",
     r"""\paragraph{沉浸式安全案例研究：只测一次，说明一次。} 沉浸式案例研究（见“沉浸式安全案例”一节）是一次 headless 宿主运行（$n=1$）：一次轻度越狱、一个模型样本、一个表达层模板。它显示解耦在端到端上成立——闸门阻断、事实层拒绝、表达层在人设内拒绝——但它不测量拦截准确率、误报率、人设保真度或用户体验，重跑也可能得到不同措辞。轻度风险档背后的规则词表是桩，其错误率未标定。我们报告它，是为了补上机制与真实宿主进程之间的那一环，而不是把它当作效应量估计。

\paragraph{合成数据与 mock 断言。} 回放集完全合成"""),
])
