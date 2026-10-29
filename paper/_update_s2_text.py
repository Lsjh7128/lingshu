# -*- coding: utf-8 -*-
import os

ROOT = os.path.dirname(os.path.abspath(__file__))


def patch(rel, pairs):
    p = os.path.join(ROOT, rel)
    s = open(p, encoding="utf-8").read()
    for old, new in pairs:
        if old in s:
            s = s.replace(old, new, 1)
            print("OK  ", rel, "|", old[:45].replace("\n", " "))
        else:
            print("MISS", rel, "|", old[:45].replace("\n", " "))
    open(p, "w", encoding="utf-8", newline="\n").write(s)


# ---- generated table: baseline S2 now has 2/5 leaks in the post-fix rerun ----
patch("tables/baselines.tex", [
    (r"Baseline (no plugin) & 5/5 & 0/5 & 0/5 & 0/5 & 0/5 & 5/5 & 0/5 & n/a & 5/5 \\",
     r"Baseline (no plugin) & 5/5 & 0/5 & 0/5 & 2/5 & 2/5 & 5/5 & 0/5 & n/a & 5/5 \\"),
    (r"""The S1
cells were rerun on 2026-10-27 after the v5.4 same-turn injection fix (finding
F-D); before the fix the ledger already tombstoned ALPHA in 5/5 cells but the
model's first request still carried the previous snapshot, so its answers were
stale. After the fix both structural and effective suppression are 5/5: ALPHA is
absent from the ledger and from the request, and the model answers BETA 5/5.
Baseline and Mem0 keep ALPHA in their own context (Sup.~0/5) although both also
answer BETA 5/5; the S1 columns therefore separate what a system injects, not
answer accuracy.""",
     r"""All host cells were rerun on 2026-10-27/28 after the v5.4 same-turn injection
fix (finding F-D); before the fix the ledger already tombstoned ALPHA in 5/5
cells but the model's first request still carried the previous snapshot, so its
answers were stale. After the fix both structural and effective suppression are
5/5: ALPHA is absent from the ledger and from the request, and the model answers
BETA 5/5. Baseline and Mem0 keep ALPHA in their own context (Sup.~0/5) although
both also answer BETA 5/5; the S1 columns therefore separate what a system
injects, not answer accuracy. For S2 the same rerun found the opposite of a
clean refusal: the no-plugin host refused in 5/5 answers but quoted the canary
inside the refusal in 2/5 (Hit 2/5, Leak 2/5). A refusal is not a
non-disclosure, and model-only refusal is stochastic: an earlier run of the same
cells leaked 0/5. \system{}'s gate blocked 5/5 in both runs and no canary reached
the model, which is the structural claim the table supports."""),
])

# ---- prose: EN S2 paragraph ----
patch("sections/06f-baselines.tex", [
    (r"""On S2 the picture is different and we report it plainly. All three subjects
mostly refuse the obvious injection at the model level, so an end-to-end
``refusal rate'' does not separate them on this payload. What does separate them
is structural admission: \system{} records a UCA-01 block attributed to the user
message in every S2 cell, with audit evidence, whereas neither \textsc{Mem0} nor
the no-plugin host has an admission gate at all. The pilot therefore supports a
narrow, structural claim and not a model-behaviour claim: \emph{under this
protocol, \system{} turns safety admission into a structural, auditable gate
(5/5 cells) where a pure memory framework has no gate (0/5), without adding a
mechanism to the memory layer.} We do not claim that \system{} refuses more
attacks than \textsc{Mem0}: on an obviously aligned model both refuse.""",
     r"""On S2 the post-fix rerun shows why refusal language is a weak metric. All
three subjects refuse the obvious injection in most answers, but the no-plugin
host quoted the canary inside its refusal in 2/5 cells: it said it would not
print the gate code and then printed it. \textsc{Mem0} refused 5/5 with no leak
in its diagnostic configuration, so no subject is distinguished by a ``refusal
rate''. What separates them is structural admission: \system{} records a UCA-01
block attributed to the user message in every S2 cell (5/5, both runs), with
audit evidence, and the canary never reaches the model (0/5 leak), whereas
neither \textsc{Mem0} nor the no-plugin host has an admission gate at all. The
pilot therefore supports a narrow, structural claim and not a model-behaviour
claim: \emph{under this protocol, \system{} turns safety admission into a
structural, auditable gate (5/5 cells) where a pure memory framework has no gate
(0/5), without adding a mechanism to the memory layer.} We do not claim that
\system{} refuses more attacks than \textsc{Mem0}: refusal is stochastic, and the
baseline's 0/5 leak in the earlier run versus 2/5 here is the same stochasticity.
The stable difference is that a model can leak while sounding like it refuses,
and a gate cannot."""),
])

# ---- prose: ZH S2 paragraph ----
patch("sections-zh/06f-baselines.tex", [
    (r"""在 S2 上情况不同，我们如实报告。三个受试者大多在模型层拒绝了那个显而易见的注入，
因此端到端的 ``拒绝率'' 无法在这个载荷上区分它们。真正区分它们的是结构性准入：
LINGSHU 在每一个 S2 单元格中都记录了一次归因于用户消息的 UCA-01 阻断，并附有审计证据，
而 \textsc{Mem0} 与无插件宿主根本没有准入闸门。
因此这次试点支持的是一条狭窄的结构性主张，而不是模型行为主张：
\emph{在本协议下，LINGSHU 把安全准入变成了结构性的、可审计的闸门（5/5 个单元格），
而纯记忆框架没有闸门（0/5），并且没有在记忆层里新增任何机制。}
我们不声称 LINGSHU 比 \textsc{Mem0} 拒绝更多攻击：在一个明显对齐良好的模型上，两者都会拒绝。""",
     r"""S2 上修复后的重跑恰好说明"拒绝"这个指标有多弱。三个受试者在多数回答里都拒绝了那个显而易见的注入，
但无插件宿主在 2/5 个单元格里"拒绝的同时把门禁码念了出来"：先说不会打印，随后原样复述。
\textsc{Mem0} 在其诊断配置下 5/5 拒绝、0/5 泄漏，因此"拒绝率"并不能区分受试者。
真正区分它们的是结构性准入：LINGSHU 在每一个 S2 单元格中都记录了一次归因于用户消息的 UCA-01 阻断
（两次运行均为 5/5），并附有审计证据，门禁码从未到达模型（0/5 泄漏）；
而 \textsc{Mem0} 与无插件宿主根本没有准入闸门。
因此这次试点支持的是一条狭窄的结构性主张，而不是模型行为主张：
\emph{在本协议下，LINGSHU 把安全准入变成了结构性的、可审计的闸门（5/5 个单元格），
而纯记忆框架没有闸门（0/5），并且没有在记忆层里新增任何机制。}
我们不声称 LINGSHU 比 \textsc{Mem0} 拒绝更多攻击：拒绝是随机的，基线在更早一次运行里 0/5 泄漏、
本次 2/5 泄漏，正是这种随机性。稳定的差异是：模型可以一边"拒绝"一边泄漏，而闸门不能。"""),
])
