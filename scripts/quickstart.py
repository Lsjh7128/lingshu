#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""LINGSHU · 一条命令跑通开发者体验（quickstart）

    python scripts/quickstart.py            # 冻结回放 → 生成 demo → 打开 → 打印下一步
    python scripts/quickstart.py --no-open  # CI / 无浏览器环境

它做四件事：
  1) 检查 Python 与仓库结构；
  2) 跑一次**冻结回放**（engine/replay：默认参数、固定时间基准、无随机数）；
  3) 调 tools/gen_demo_report.py 生成自包含的 console/demo.html
     （真实日志缺失时自动空态，不会编造数据）；
  4) 打印下一步：CLI、全量验收、插件 mock 宿主、实验 profile（可丢弃，不碰 desktop）。

约束：纯标准库；不安装任何东西；不读写任何 DSH profile。
失败时给出可执行的下一步提示，并以退出码 1 结束。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import webbrowser

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_HOME = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
MIN_PY = (3, 8)

PHASE = "[%d/4]"


def _out(line=""):
    print(line)


def _ok(tag, detail):
    _out("  [OK]   %-22s %s" % (tag, detail))


def _fail(step, reason, hint=""):
    _out("")
    _out("  [FAIL] 第 %d 步失败：%s" % (step, reason))
    if hint:
        _out("         提示：%s" % hint)
    _out("")
    _out("quickstart 中止。仓库：%s" % ROOT)
    return 1


def check_env():
    if sys.version_info < MIN_PY:
        return "需要 Python >= %d.%d，当前是 %s" % (
            MIN_PY[0], MIN_PY[1], sys.version.split()[0]), "用 3.8+ 的 python 运行本脚本"
    _ok("Python", "%s（>= %d.%d，无第三方依赖）"
        % (sys.version.split()[0], MIN_PY[0], MIN_PY[1]))

    required = [
        ("engine/config.py", "参考实现配置"),
        ("engine/replay.py", "冻结回放器"),
        ("engine/pipeline.py", "端到端流水线"),
        ("replay-set/v1.jsonl", "回放集（合成）"),
        ("tools/run_pipeline.py", "流水线 CLI"),
        ("tools/gen_demo_report.py", "demo 生成器"),
        ("plugin/dsh-lingshu/package.json", "DSH 插件"),
    ]
    missing = [rel for rel, _desc in required if not os.path.exists(os.path.join(ROOT, rel))]
    if missing:
        return "仓库结构不完整，缺少：%s" % ", ".join(missing), \
            "请在仓库根目录运行：python scripts/quickstart.py"
    _ok("仓库结构", "%d 个关键路径齐全" % len(required))
    return None


def step_replay():
    """冻结回放：默认参数、固定 BASE_TS、无随机数（engine/replay 的契约）。"""
    sys.path.insert(0, ROOT)
    from engine import config as cfgmod
    from engine import replay as rp

    cfg = cfgmod.default()
    report = rp.replay(cfg, ROOT)
    metrics = rp.score(report)
    _ok("冻结回放", "%d 轮（默认参数 · 固定时间基准）" % metrics["turns"])
    _out("         recall@3=%s · false_inject=%s · stale_leak=%d · 预算利用=%s"
         % ("%.3f" % metrics["recall_at_3"], "%.3f" % metrics["false_inject_rate"],
            metrics["stale_leak"], "%.3f" % metrics["budget_utilization"]))

    # 端到端流水线：把 14 个模块 + 控制论层串起来再跑一遍（0.6s 量级）
    from engine import pipeline as pl
    rep = pl.replay_pipeline(cfg, ROOT)
    s = rep["summary"]
    _ok("端到端流水线", "%d 轮（含相变 / 锚点 / STPA 前置闸）" % s["turns"])
    _out("         冻结基线：违禁泄漏=%d · 预算截断=%d · 前置闸拦截=%d"
         % (s["forbidden_leak"], s["truncated_turns"], s["blocked_turns"]))
    blocks = " / ".join("%s 块 ×%d" % (k, v) for k, v in sorted(s["block_count_hist"].items()))
    if blocks:
        _out("         注入块数分布：%s" % blocks)
    return None


def step_demo(args):
    gen = os.path.join(ROOT, "tools", "gen_demo_report.py")
    out = os.path.abspath(args.out)
    cmd = [sys.executable, gen, "--home", args.home, "--out", out]
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
    except OSError as exc:
        return "无法启动 demo 生成器：%s: %s" % (type(exc).__name__, exc), \
            "直接运行：python tools/gen_demo_report.py"
    if proc.returncode != 0:
        tail = (proc.stderr or proc.stdout or "").strip().splitlines()[-6:]
        return "demo 生成器退出码 %d" % proc.returncode, \
            "直接运行看完整报错：python tools/gen_demo_report.py\n" + "\n".join(tail)
    if not os.path.isfile(out):
        return "demo 生成器没有写出 %s" % out, "检查磁盘权限或 --out 参数"

    lines = [l for l in (proc.stdout or "").splitlines() if l.strip()]
    _ok("Demo 报告", "%s（%.1f KB，自包含单文件）" % (out, os.path.getsize(out) / 1024.0))
    for line in lines[1:]:
        _out("         " + line.strip())
    if args.no_open:
        _out("         --no-open：不自动打开浏览器")
    else:
        opened = False
        try:
            opened = webbrowser.open("file:///" + out.replace("\\", "/"))
        except Exception:
            opened = False
        _out("         %s" % ("已用默认浏览器打开" if opened else
                               "未能自动打开浏览器，请手动打开上面的文件"))
    return None


def print_next_steps(args):
    out = os.path.abspath(args.out)
    node_hint = os.path.join(os.path.expanduser("~"), ".dsh", "dsh-runtimes",
                             "dsh-primary-runtime", "dependencies", "node", "bin", "node.exe")
    repo_link = os.path.join(ROOT, "plugin", "dsh-lingshu").replace("\\", "/")
    _out("")
    _out("下一步（按需选择）")
    _out("")
    _out("  · 打开 demo：%s" % out)
    _out("    或只重生成：python tools/gen_demo_report.py")
    _out("    没有真实日志也能跑：python tools/gen_demo_report.py --no-real（空态 + 阈值演示）")
    _out("")
    _out("  · 更多 CLI（回放 / 归因 / 灰度 / 闭环）：")
    _out("      python tools/run_pipeline.py --json")
    _out("      python tools/run_learning.py")
    _out("      python tools/run_gray.py --set retrieval.topK=4")
    _out("      python tools/run_loop.py")
    _out("")
    _out("  · Python 验收（570 项：引擎 461 + 标定分析 109，纯标准库）：")
    _out("      python tests/test_control.py                # 159")
    _out("      python tests/test_pipeline_integration.py   # 65")
    _out("      python tests/test_agent_state.py            # 45")
    _out("      python tests/test_onboarding_persona.py     # 50")
    _out("      其余套件见 README「快速开始」")
    _out("")
    _out("  · 插件验收（517 条，需要 Node；mock 宿主，不碰真实 profile）：")
    _out("      node plugin/dsh-lingshu/host-sim.mjs --assert     # 276（含 observe 20）")
    _out("      node plugin/dsh-lingshu/control-sim.mjs --assert  # 197")
    _out("      node plugin/dsh-lingshu/stress.mjs --assert       # 45")
    _out("    本机 Node 可能在：%s" % node_hint)
    _out("")
    _out("  · 跨环境控制台（独立只读服务，默认 http://127.0.0.1:3000）：")
    _out("      python tests/test_console_server.py     # 72 项（脱敏/热切换/只读/SSE）")
    _out("      .\\scripts\\console.ps1 -Check            # 校验 profile 注册表与数据源")
    _out("      .\\scripts\\console_demo.ps1              # 演示：起服务 + 40 轮合成流量")
    _out("      .\\scripts\\console_desktop_onboard.ps1   # desktop 接入计划（默认 dry-run，人工执行）")
    _out("    设计：docs/25-跨环境控制台.md；契约：console/API.md")
    _out("  · 真实实验会话（可丢弃 profile；日常 desktop profile 零改动）：")
    _out('      $node = "<上面的 node.exe>"')
    _out('      $bin  = "$env:USERPROFILE\\.dsh\\profiles\\node_modules\\@deepseek-ai\\dsh\\lib\\bin.js"')
    _out('      & $node $bin lingshu-lab-headless --from-default-profile headless --dump-config')
    _out('      & $node $bin plugin --profile lingshu-lab-headless add "link:%s"' % repo_link)
    _out('      & $node $bin lingshu-lab-headless "用一句话回答：项目代号是什么？"')
    _out("    命令细节与三分钟回滚：plugin/README.md")
    _out("")
    _out("  · 一致性 / 漂移检查：")
    _out("      python tools/check_config_sync.py")
    _out("      python tools/gen_contract.py --check")
    _out("      python tools/gen_impl_status.py --check")


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    ap = argparse.ArgumentParser(description="LINGSHU 一键上手：冻结回放 + demo 报告 + 下一步")
    ap.add_argument("--no-open", action="store_true",
                    help="生成 demo 后不自动打开浏览器（CI / headless 用）")
    ap.add_argument("--home", default=DEFAULT_HOME,
                    help="DSH_HOME（默认取 env DSH_HOME，否则 ~/.dsh）；只读，不修改")
    ap.add_argument("--out", default=os.path.join(ROOT, "console", "demo.html"),
                    help="demo 输出路径（默认 console/demo.html）")
    args = ap.parse_args(argv)

    _out("LINGSHU quickstart · 灵为行为，枢为边界")
    _out("仓库：%s" % ROOT)
    _out("")

    _out(PHASE % 1 + " 检查环境")
    err = check_env()
    if err:
        return _fail(1, err[0], err[1])

    _out("")
    _out(PHASE % 2 + " 冻结回放（engine/replay + engine/pipeline）")
    try:
        err = step_replay()
    except Exception as exc:  # 明确失败原因，不吞异常
        return _fail(2, "%s: %s" % (type(exc).__name__, exc),
                     "先单独复现：python tools/run_pipeline.py --json")
    if err:
        return _fail(2, err[0], err[1])

    _out("")
    _out(PHASE % 3 + " 生成 Demo 报告（tools/gen_demo_report.py）")
    err = step_demo(args)
    if err:
        return _fail(3, err[0], err[1])

    _out("")
    _out(PHASE % 4 + " 完成（未安装任何东西、未改动任何 profile）")
    print_next_steps(args)
    _out("")
    _out("[OK] quickstart 全流程通过。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
