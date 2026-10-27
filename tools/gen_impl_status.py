# -*- coding: utf-8 -*-
"""生成"实现状态矩阵"（docs/16-实现状态.md），并支持漂移检查。

为什么需要它：
    审计里出现过一类最阴的错误 —— **文档说"已实现"但代码里没有**（R-8），
    或反过来"代码早做了、文档还说未实现"。
    人写的状态表必然腐化；所以这里让它**由代码生成**：
        python tools/gen_impl_status.py --write   # 重新生成
        python tools/gen_impl_status.py --check   # 不一致退出码 1
"""
from __future__ import annotations

import argparse
import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(ROOT, "docs", "16-实现状态.md")

# 能力 → (文档, 引擎模块, 验收测试, 说明)
MAP = [
    ("记忆：衰减 / 取代 / 去重 / 召回 / 继承", ["docs/02-记忆插件.md"],
     ["engine/memory.py"], ["tests/test_learning.py"],
     "含预算化召回与遗忘两段式"),
    ("元认知：置信度分档", ["docs/04-元认知插件.md"],
     ["engine/metacog.py"], ["tests/test_learning.py"], "纯规则，零模型调用"),
    ("情感：单 τ / 双速衰减 / 滞回 / 标签闭集", ["docs/03-情感插件.md"],
     ["engine/emotion.py"], ["tests/test_pipeline.py"], "评估器是规则桩，非模型"),
    ("合成矩阵：五域 / 互斥 / 预算 / 顺序", ["docs/09-合成矩阵.md"],
     ["engine/compose.py"], ["tests/test_pipeline.py"], "8 条互斥 + overflow 标记"),
    ("学习回放：replay / score / attribute", ["docs/13-学习机制.md"],
     ["engine/replay.py"], ["tests/test_learning.py"], "幂等 + 可归因 + 可复现"),
    ("参数搜索（W4）", ["docs/13-学习机制.md", "research/01-参数搜索报告.md"],
     ["tools/search_params.py"], [], "产出可辨识性结论"),
    ("灰度与护栏（W5）", ["docs/14-工程与可观测性.md"],
     ["engine/gray.py"], ["tests/test_gray.py"], "7 条护栏 + 自动回滚"),
    ("纠正 → 提议（W6）", ["docs/13-学习机制.md"],
     ["engine/proposal.py"], ["tests/test_proposal.py"], "回放验证后才成提议"),
    ("控制台 v0", ["docs/14-工程与可观测性.md"],
     ["tools/report_console.py"], [],
     "静态单页：卡片 + 注入预览 + 记忆表 + 审计时间线"),
    ("跨环境控制台（独立只读服务）", ["docs/25-跨环境控制台.md"],
     ["console/server.py", "console/redact.py", "console/ui/app.js"],
     ["tests/test_console_server.py"],
     "多 profile 热切换 + 服务端白名单脱敏 + SSE；只读；演示用 tools/console_demo_feed.mjs"),
    ("插件静默监控模式（observe）", ["docs/25-跨环境控制台.md", "plugin/README.md"],
     ["plugin/dsh-lingshu/index.js"],
     ["plugin/dsh-lingshu/host-sim.mjs"],
     "不注册 prompt 段/waterfall/工具；保留记忆+相变估算+STPA 留痕；profileTag 隔离数据目录"),
    ("AstrBot 适配器", ["docs/18-AstrBot协同.md", "integrations/astrbot/README.md"],
     ["integrations/astrbot/main.py"], ["tests/test_astrbot_adapter.py"],
     "复用 engine/；钩子 on_llm_request/on_llm_response；未在真实 AstrBot 跑过"),
    ("目标/关系误判率", ["docs/05-目标与意图.md", "docs/06-关系模型.md"],
     ["plugin/dsh-lingshu/eval-agent-state.mjs"], [],
     "47 条标注样本；precision 0.556–1.000，负样本误报 31.6%"),
    ("DSH 插件骨架", ["docs/00-架构总览.md", "plugin/README.md"],
     ["plugin/dsh-lingshu/index.js", "plugin/dsh-lingshu/control.js"],
     ["plugin/dsh-lingshu/host-sim.mjs", "plugin/dsh-lingshu/control-sim.mjs"],
     "7 段注入 + 控制论层（相变锚点/U 型注入/UCA 前置闸/UCA-08 留痕）+ 3 钩子 + 1 工具；实验 profile 跑过真实会话"),
    ("端到端流水线", ["docs/00-架构总览.md", "docs/09-合成矩阵.md"],
     ["engine/pipeline.py"], ["tests/test_pipeline_integration.py"],
     "recall→metacog→emotion→compose 串起来；事件符合 prompt/compose 契约"),
    ("契约工程化", ["docs/00-架构总览.md"],
     ["engine/contract.py", "tools/gen_contract.py"], [], "生成 + digest + 漂移测试"),
    ("参数一致性", ["rules/decay-params.yaml"],
     ["tools/check_config_sync.py"], [],
     "72 ok / 0 fail / 1 skip（rules↔config + 插件侧副本 + 布尔开关显式 skip）"),
    ("控制论：积分/相变/降维/UCA/ACT-R/权重/impasse",
     ["docs/19-控制论重构契约.md"],
     ["engine/control.py", "engine/actr.py"], ["tests/test_control.py"],
     "STPA 前置拦截 + 双通道 ACT-R + 动态权重"),
    ("沉浸式安全：Soft Buffer / Safe_Redirect / 风险分级（ADR-014）",
     ["decisions/014-沉浸式安全表达层.md", "docs/09-合成矩阵.md", "docs/10-降级矩阵.md"],
     ["engine/compose.py"], ["tests/test_immersive_safety.py"],
     "mild→soft（STPA_Blocked 仍 true、人设不崩）；severe/连续升级→hard；信任豁免；默认零回归"),
    ("宪法层", ["docs/01-宪法层.md"], ["engine/constitution.py"],
     ["tests/test_constitution.py"],
     "提示词为主 + 审计器规则桩（A1–A10 / C6 / FrozenGuard）"),
    ("目标与意图", ["docs/05-目标与意图.md"], ["engine/goal.py"],
     ["tests/test_agent_state.py"], "事件必须有逐字证据；只发写请求不直写"),
    ("关系模型", ["docs/06-关系模型.md"], ["engine/relation.py"],
     ["tests/test_agent_state.py"], "trust 不自动升 high；modifier 硬边界"),
    ("叙事整合", ["docs/07-叙事整合.md"], ["engine/narrative.py"],
     ["tests/test_agent_state.py"], "仅查询时拼装；因果只取已有边"),
    ("用户情感感知", ["docs/08-用户情感感知.md"], [], [], "design-only，不在主线"),
    ("冷启动 / 用户侧追溯", ["docs/11-冷启动与追溯.md"], ["engine/onboarding.py"],
     ["tests/test_onboarding_persona.py"], "三阶段 + 情感 no_event + 纠正即生效（不直写）"),
    ("人设解析器", ["docs/12-人设解析器.md"], ["engine/persona.py"],
     ["tests/test_onboarding_persona.py"], "规则层：风险识别 / 钳制 / 按用户限流；映射由模型"),
    ("宿主能力探针", ["research/02-DSH宿主能力核实.md", "probe/README.md"],
     ["probe/dsh-lingshu-probe/index.js"], ["probe/self-check.mjs"],
     "只读探针，测钩子触发顺序与 payload 形状"),
]


def _loc(path: str) -> int:
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        return 0
    return sum(1 for l in io.open(full, encoding="utf-8") if l.strip())


def _checks(path: str) -> int:
    full = os.path.join(ROOT, path)
    if not os.path.exists(full):
        return 0
    text = io.open(full, encoding="utf-8").read()
    return len(re.findall(r'check\(\s*[\'"`]', text))


def _exists(path: str) -> bool:
    return os.path.exists(os.path.join(ROOT, path))


def build() -> str:
    lines = ["# 16 · 实现状态矩阵", "",
             "> **本文件由 `tools/gen_impl_status.py` 生成，请勿手改。**",
             "> 改代码后跑 `python tools/gen_impl_status.py --write`，CI 用 `--check` 防漂移。", ""]

    total_mod = total_test = total_check = 0
    rows, missing_doc, missing_mod = [], [], []
    for name, docs, mods, tests, note in MAP:
        for d in docs:
            if not _exists(d):
                missing_doc.append(d)
        for m in mods:
            if not _exists(m):
                missing_mod.append(m)
        mod_loc = sum(_loc(m) for m in mods)
        n_checks = sum(_checks(t) for t in tests)
        total_mod += len(mods)
        total_test += len(tests)
        total_check += n_checks
        if mods and n_checks:
            status = "✅ 参考实现 + 验收"
        elif mods:
            status = "⚠️ 有实现，无专项验收"
        elif "design-only" in note:
            status = "⏸ design-only"
        elif "部分" in note:
            status = "⚠️ 部分"
        elif "产品" in note:
            status = "📄 文档（无引擎代码）"
        else:
            status = "📄 仅文档"
        rows.append((name, docs, mods, tests, mod_loc, n_checks, status, note))

    lines += ["## 汇总", "", "| 项 | 值 |", "|---|---|"]
    lines.append("| engine 模块数 | %d |" % len({m for _, _, ms, _, _ in MAP for m in ms}))
    lines.append("| 有验收的模块数 | %d |" % sum(1 for r in rows if r[5] > 0))
    lines.append("| 验收断言调用点 | %d（循环内会执行多次，实际次数见测试输出） |" % total_check)
    lines.append("| 文档缺失 | %s |" % (", ".join(sorted(set(missing_doc))) or "无"))
    lines.append("| 声明了但缺文件 | %s |" % (", ".join(sorted(set(missing_mod))) or "无"))
    lines.append("")

    lines += ["## 矩阵", "",
              "| 能力 | 文档 | 引擎模块 | 验收文件 | 代码行 | 断言点 | 状态 | 备注 |",
              "|---|---|---|---|---|---|---|---|"]
    for name, docs, mods, tests, loc, n, status, note in rows:
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s |" % (
            name,
            "<br>".join("`%s`" % d for d in docs) or "—",
            "<br>".join("`%s`" % m for m in mods) or "—",
            "<br>".join("`%s`" % t for t in tests) or "—",
            loc or "—", n or "—", status, note))
    lines.append("")

    lines += ["## 怎么读这张表", "",
              "- **✅ 参考实现 + 验收**：`engine/` 里有可跑的代码，且有测试断言；"
              "它证明**契约可实现**，不等于产品级实现；",
              "- **📄 仅文档**：契约与提示词已定稿，尚无引擎代码。这类**不算欠债**——"
              "提示词产品本就只需要文档；",
              "- **⏸ design-only**：有意不实现（见 ADR/文档）；",
              "- **⚠️ 部分**：有设计、缺某一段实现，需在文档里写明缺哪段。", "",
              "> 这张表的存在意义：**杜绝\"文档说已实现、代码里没有\"**（审计 R-8）。"
              "它由代码生成，所以不会腐化。"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    fresh = build()
    if args.write:
        io.open(OUT, "w", encoding="utf-8", newline="\n").write(fresh)
        print("written:", OUT)
        return 0
    if not os.path.exists(OUT):
        print("MISSING:", OUT)
        return 1
    cur = io.open(OUT, encoding="utf-8").read()
    if cur != fresh:
        print("DRIFT: docs/16-实现状态.md 与代码不一致。"
              "运行 `python tools/gen_impl_status.py --write` 并 review diff。")
        return 1
    print("OK: 实现状态矩阵是最新的")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
