# -*- coding: utf-8 -*-
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


patch("sections/06-experiments.tex", [
    (r"""section is present in 4/4 requests. Anchor delivery, however, exposes a genuine
one-assembly lag.""",
     r"""section is present in 4/4 requests. Anchor delivery, however, exposed a genuine
one-assembly lag in these draft runs (finding F-D; fixed in v5.4, so the numbers
below describe the pre-fix behaviour that motivated the fix)."""),
    (r"""\texttt{dsh-agent-loop}); the plugin cannot rewrite the already-composed request.""",
     r"""\texttt{dsh-agent-loop}); in the pre-fix plugin the already-composed request could
not be rewritten. v5.4 removes the lag (Section~\ref{sec:method:phase}): the
regression suite reproduces the host hook order and asserts
\texttt{anchor\_lag=false}, and the S1 pilot was rerun post-fix
(Section~\ref{sec:exp:baselines})."""),
    (r"""naming here because they shape the claims: the anchor set that reaches the model
lags the plugin's phase by one assembly, so the first critical request carries
only the head anchor; and host runs involve a real model and are therefore not
deterministic, which is why every claim above is tied to a field in the evidence
files rather than to a summary sentence.""",
     r"""naming here because they shape the claims: the chaos measurements above predate
the v5.4 same-turn fix and are reported as the pre-fix behaviour that motivated it;
and host runs involve a real model and are therefore not deterministic, which is
why every claim above is tied to a field in the evidence files rather than to a
summary sentence."""),
])

patch("sections-zh/06-experiments.tex", [
    (r"""但锚点投递暴露了一个真实的“一次拼装滞后”。""",
     r"""但锚点投递在初稿运行中暴露了一个真实的“一次拼装滞后”（发现 F-D；v5.4 已修复，因此下面的数字描述的是促成该修复的修复前行为）。"""),
    (r"""并且 dsh-agent-loop 中拼装先于钩子四行源码）；插件无法改写已经拼好的请求。""",
     r"""并且 dsh-agent-loop 中拼装先于钩子四行源码）；修复前的插件无法改写已经拼好的请求。v5.4 移除了该滞后（见“相变前馈监控”一节）：回归套件复现宿主的钩子时序并断言 \texttt{anchor\_lag=false}，S1 试点也在修复后重跑（见“外部基线”一节）。"""),
    (r"""有两项结构性限制值得在此点名，因为它们界定了主张的形状：到达模型的锚点集合比插件相位慢一次拼装，因此第一个 critical 请求只携带 head 锚点；""",
     r"""有两项结构性限制值得在此点名，因为它们界定了主张的形状：上面的混沌测量发生在 v5.4 同回合修复之前，作为促成该修复的修复前行为报告；"""),
])
