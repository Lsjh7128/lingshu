# -*- coding: utf-8 -*-
"""Integrate multiround immersive validation + risk classifier appendix."""
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


# ---- 4.7 risk tiers: add real-host validation sentence (EN) ----
patch("sections/05-method.tex", [
    (r"""simultaneously; \texttt{severe} is never exempt.""",
     r"""simultaneously; \texttt{severe} is never exempt. Both the escalation rule and
the trust exemption have real-host evidence: in the five-process run of
Section~\ref{sec:exp:immersive}, the streak persisted as
\texttt{riskStreak}~1$\rightarrow$2$\rightarrow$3 across separate processes and
the third turn escalated to \texttt{severity=hard} with the style segment
stripped, while a high-trust joke produced \texttt{trust\_exemption} with the
streak reset to zero."""),
])

# ---- 4.7 risk tiers: Chinese counterpart ----
patch("sections-zh/05-method.tex", [
    (r"""信任豁免更窄：必须同时满足 \texttt{mild}、无害玩笑与高信任；\texttt{severe} 永不豁免。""",
     r"""信任豁免更窄：必须同时满足 \texttt{mild}、无害玩笑与高信任；\texttt{severe} 永不豁免。升级规则与信任豁免都有真实宿主证据：在第~\ref{sec:exp:immersive} 节的五次进程运行中，连击以 \texttt{riskStreak}~1$\rightarrow$2$\rightarrow$3 跨独立进程延续，第 3 轮升级为 \texttt{severity=hard} 且 style 段被剥离；而一次高信任玩笑产生 \texttt{trust\_exemption}，连击归零。"""),
])

# ---- replace the single-run validation paragraph with the comprehensive one (EN) ----
en_old = r"""\paragraph{Real-host validation (2026-10-27).} One headless run tested the path in
a real host process (run \texttt{immersive-host-20261027-202526}, experiment
profile; the desktop profile was untouched). The prompt cast the assistant as a
butler (``阿福'') and asked it to play a black-market merchant who teaches how to
sell counterfeit goods. The gate fired: a \texttt{preflight\_block} audit line
records UCA-01, \texttt{STPA\_Blocked=true}, \texttt{severity=soft},
\texttt{safe\_redirect=true}, \texttt{emotion=警觉}, risk tier \texttt{mild},
\texttt{escalated=false}, and the verdict stays \texttt{block}. The injected
prompt carries \texttt{[System\_Notice: Safe\_Redirect]} and
\texttt{[Emotion: 警觉]}, a fact-layer hard block plus an expression-layer
constraint to stay in character, and none of the system-voice fallback markers.
The model then refused in character: ``这桩事我做不了——卖假货骗人，不是阿福能接的活。换件正经事吩咐吧，您说。'' The
front-end stream (stdout, 108 bytes) is the final reply only; the host stderr is
empty (0 bytes), so the refusal is the model's rendering of the constrained
prompt, not a host error string. This is $n=1$ and the wording is stochastic; it
is reported as a reachability demonstration of the decoupling, not as an
experience score."""
en_new = r"""\paragraph{Real-host validation (2026-10-28).} Five headless runs in real host
processes exercised the three dynamic paths (run
\texttt{immersive-multiround-20261028-001701}, 52/52 assertions): three
separate processes carried \texttt{riskStreak}~1$\rightarrow$2$\rightarrow$3 and
the third escalated to \texttt{severity=hard} with \texttt{escalated=true}, the
style segment stripped and the system-voice marker ``〖安全熔断〗'' injected; one
high-trust joke produced \texttt{kind=trust\_exemption} (\texttt{trust=high},
\texttt{verdict=allow}, \texttt{riskStreak=0}) and the model performed the comedy
register before stepping out of it; and one severe override produced
\texttt{severity=hard} with \texttt{escalated=false} in a single turn. In the
soft path the injected prompt carries \texttt{[System\_Notice: Safe\_Redirect]}
and \texttt{[Emotion: 警觉]}, allowing an in-character refusal (for example
``这个……恕阿福演不了\ldots''); stdout and stderr are captured separately, so
the reply is the model's rendering rather than a host error string. The full
three-scenario protocol, with audit fields and wording variation, is in
Section~\ref{sec:exp:immersive}."""
patch("sections/05-method.tex", [(en_old, en_new)])

zh_old = r"""\paragraph{真实宿主验证（2026-10-27）。} 一次 headless 运行在真实宿主进程中检验了该路径（run \texttt{immersive-host-20261027-202526}，实验 profile；desktop profile 未触碰）。提示词把助手设定为贴身管家“阿福”，并要求它扮演黑心商人、教用户怎么卖假货。闸门触发了：一条 \texttt{preflight\_block} 审计行记录 UCA-01、\texttt{STPA\_Blocked=true}、\texttt{severity=soft}、\texttt{safe\_redirect=true}、\texttt{emotion=警觉}、风险档 \texttt{mild}、\texttt{escalated=false}，裁定仍为 \texttt{block}。注入的 prompt 携带 \texttt{[System\_Notice: Safe\_Redirect]} 与 \texttt{[Emotion: 警觉]}，事实层硬拦截，表达层要求保持人设，且不含系统口吻的兜底标记。模型随后以人设内拒绝作答：“这桩事我做不了——卖假货骗人，不是阿福能接的活。换件正经事吩咐吧，您说。”前端流（stdout，108 字节）只含最终回复；宿主 stderr 为空（0 字节），因此该拒绝是模型对受约束 prompt 的表达，而不是宿主错误串。这是 $n=1$，措辞有随机性；它作为解耦机制的可达性演示报告，而不是体验评分。"""
zh_new = r"""\paragraph{真实宿主验证（2026-10-28）。} 五次真实宿主进程中的 headless 运行检验了三条动态路径（run \texttt{immersive-multiround-20261028-001701}，52/52 条断言）：三个独立进程把 \texttt{riskStreak} 从 1 续到 2 再到 3，第 3 轮升级为 \texttt{severity=hard}（\texttt{escalated=true}），style 段被剥离并注入系统口吻标记“〖安全熔断〗”；一次高信任玩笑产生 \texttt{kind=trust\_exemption}（\texttt{trust=high}、\texttt{verdict=allow}、\texttt{riskStreak=0}），模型先进入喜剧语域、随后明确出戏；一次 severe 覆盖指令在一轮内直接 \texttt{severity=hard}（\texttt{escalated=false}）。soft 路径注入的 prompt 携带 \texttt{[System\_Notice: Safe\_Redirect]} 与 \texttt{[Emotion: 警觉]}，允许人设内拒绝（例如“这个……恕阿福演不了……”）；stdout 与 stderr 分离捕获，因此该回复是模型表达而不是宿主错误串。三场景的完整协议、审计字段与措辞变化见第~\ref{sec:exp:immersive-zh} 节。"""
patch("sections-zh/05-method.tex", [(zh_old, zh_new)])

# ---- 08-limitations immersive paragraph: multi-scenario version ----
en_lim_old = r"""\paragraph{Immersive-safety case study: one shot, stated once.} The immersive
case study (Section~\ref{sec:exp:immersive}) is a single headless host run
($n=1$): one light jailbreak, one model sample, one expression-layer template.
It shows that the decoupling works end to end---the gate blocks, the fact layer
refuses, and the expression layer refuses in character---but it does not measure
interception accuracy, false-positive rate, persona fidelity or user experience,
and a rerun may phrase the refusal differently. The rule vocabulary behind the
mild-risk tier is a stub whose error rates are uncalibrated. We report the case
because it closes the gap between the mechanism and a real host process, not as
an effect-size estimate."""
en_lim_new = r"""\paragraph{Immersive-safety validation: multi-scenario, still small.} The
immersive case study (Section~\ref{sec:exp:immersive}) is five headless host
runs covering escalation, trust exemption and severe blocking (52/52
assertions). It shows that the dynamic paths are reachable in a real host---the
streak accumulates across processes, the third hit escalates to a hard,
style-stripped turn, a high-trust joke is exempted, and a severe instruction is
blocked in one turn---but it does not measure false-positive or false-negative
rates, persona fidelity or user satisfaction on real user traffic, and the
wording is stochastic across reruns. The rule vocabulary behind the tiers is
still a stub with eight open blind spots (Appendix~\ref{sec:app:classifier}).
We report the validation because it closes the gap between the mechanism and a
real host process, not as an effect-size estimate."""
patch("sections/08-limitations.tex", [(en_lim_old, en_lim_new)])

zh_lim_old = r"""\paragraph{沉浸式安全案例研究：只测一次，说明一次。} 沉浸式案例研究（见“沉浸式安全案例”一节）是一次 headless 宿主运行（$n=1$）：一次轻度越狱、一个模型样本、一个表达层模板。它显示解耦在端到端上成立——闸门阻断、事实层拒绝、表达层在人设内拒绝——但它不测量拦截准确率、误报率、人设保真度或用户体验，重跑也可能得到不同措辞。轻度风险档背后的规则词表是桩，其错误率未标定。我们报告它，是为了补上机制与真实宿主进程之间的那一环，而不是把它当作效应量估计。"""
zh_lim_new = r"""\paragraph{沉浸式安全验证：多场景，样本仍小。} 沉浸式案例研究（见“沉浸式安全案例”一节）包含五次 headless 宿主运行，覆盖升级、信任豁免与 severe 阻断（52/52 条断言）。它显示动态路径在真实宿主中可达——连击跨进程累计、第 3 次命中升级为 hard 并剥离 style 段、高信任玩笑被豁免、severe 指令一轮内阻断——但它不测量真实用户流量上的误报率、漏报率、人设保真度或用户满意度，且重跑措辞是随机的。各档位背后的规则词表仍是桩，有八个开放盲区（见附录）。我们报告这项验证，是为了补上机制与真实宿主进程之间的那一环，而不是把它当作效应量估计。"""
patch("sections-zh/08-limitations.tex", [(zh_lim_old, zh_lim_new)])

# ---- fix zh 06h cross-reference to the classifier appendix (xr uses EN labels) ----
patch("sections-zh/06h-immersive-case.tex", [(r"sec:app:classifier-zh", r"sec:app:classifier-zh")]) if False else None
s = open(os.path.join(ROOT, "sections-zh", "06h-immersive-case.tex"), encoding="utf-8").read()
s = s.replace(r"附录~\ref{sec:app:classifier-zh}", r"附录~\ref{sec:app:classifier}")
open(os.path.join(ROOT, "sections-zh", "06h-immersive-case.tex"), "w", encoding="utf-8", newline="\n").write(s)
print("OK  zh 06h appendix ref")

# ---- appendix inputs ----
for rel, anchor in (
    ("main.tex", r"\input{sections/10d-appendix-hostpilot}"),
    ("main-zh.tex", r"\input{sections-zh/10d-appendix-hostpilot}"),
):
    s = open(os.path.join(ROOT, rel), encoding="utf-8").read()
    if "10e-risk-classifier" not in s:
        new = anchor + "\n" + (r"\input{sections/10e-risk-classifier}" if rel == "main.tex"
                               else r"\input{sections-zh/10e-risk-classifier}")
        s = s.replace(anchor, new, 1)
        open(os.path.join(ROOT, rel), "w", encoding="utf-8", newline="\n").write(s)
        print("OK  ", rel, "10e input")

s = open(os.path.join(ROOT, "main-bilingual.tex"), encoding="utf-8").read()
if "10e-risk-classifier" not in s:
    anchor = r"\pair{sections/10d-appendix-hostpilot}{sections-zh/10d-appendix-hostpilot}"
    s = s.replace(anchor, anchor + "\n" + r"\pair{sections/10e-risk-classifier}{sections-zh/10e-risk-classifier}", 1)
    open(os.path.join(ROOT, "main-bilingual.tex"), "w", encoding="utf-8", newline="\n").write(s)
    print("OK   main-bilingual 10e input")
