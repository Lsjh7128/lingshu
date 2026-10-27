# -*- coding: utf-8 -*-
"""解冻状态检查：四个冻结方向是否到达触发条件（ADR-010 / 待办-冻结方向.md）

只读：真实插件数据目录（缺省 $DSH_HOME/plugins/dsh-lingshu，**不含** .chaos-data 压测数据）
+ rules/decay-params.yaml。不写任何插件数据。

用法：
    python tools/unfreeze_status.py
    python tools/unfreeze_status.py --out acceptance/unfreeze-status.md

为什么需要它：冻结不是"忘掉"，而是"到点自动提醒"。指标不可测时必须写 `不可测`，
不允许用 0 冒充"未触发"（那会让冻结永远看起来安全）。
"""
from __future__ import annotations

import argparse
import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)


def _home() -> str:
    h = os.environ.get("DSH_HOME")
    return h.strip() if h and h.strip() else os.path.join(os.path.expanduser("~"), ".dsh")


def _read_jsonl(path: str) -> list[dict]:
    out = []
    try:
        for line in io.open(path, encoding="utf-8"):
            line = line.strip().lstrip("\ufeff")
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        return []
    return out


def collect(plugin_dir: str) -> dict:
    """采集可测指标。不可测的字段返回 None，由调用方标注。"""
    st = {}
    try:
        st = json.load(io.open(os.path.join(plugin_dir, "state.json"), encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        st = {}
    mem = _read_jsonl(os.path.join(plugin_dir, "memory.jsonl"))
    by_id = {}
    for e in mem:
        if isinstance(e, dict) and e.get("id"):
            by_id[e["id"]] = e                       # last-wins（含墓碑）
    memory_entries = len(by_id)
    active = sum(1 for e in by_id.values() if e.get("status") == "active")
    audit = _read_jsonl(os.path.join(plugin_dir, "audit.jsonl"))
    pre_step = sum(1 for a in audit if a.get("kind") == "pre_step")

    # measured 参数数：只看 rules（单一来源）
    measured = 0
    try:
        text = io.open(os.path.join(ROOT, "rules", "decay-params.yaml"), encoding="utf-8").read()
        measured = len(re.findall(r"provenance:\s*measured", text))
    except OSError:
        pass

    return {
        "turns": int(st.get("turns") or 0),
        "sessions": int(st.get("sessions") or 0),
        "memory_entries": memory_entries,
        "memory_active": active,
        "audit_events": len(audit),
        "pre_step_events": pre_step,
        "measured_params": measured,
    }


def _row(no: str, name: str, trigger: str, current: str, met: str, status: str, note: str) -> dict:
    return {"no": no, "name": name, "trigger": trigger, "current": current,
            "met": met, "status": status, "note": note}


def build(plugin_dir: str) -> dict:
    c = collect(plugin_dir)
    rows = []

    # 方向一：行动闭环（工具调用）
    rows.append(_row(
        "1", "行动闭环（工具调用）",
        "≥10 个任务因无法调外部 API 失败 + 合成器无法回滚工具错误",
        "不可测（审计无任务失败/工具失败字段）", "—", "❔ 不可测",
        "先补审计：task_result / tool_error / rollback_needed；再用真实失败样本计数"))

    # 方向二：自适应演化
    turns = c["turns"]
    met2a = turns >= 1000
    eff_evidence = os.path.exists(os.path.join(ROOT, "acceptance", "manual-calibration-effort.md"))
    rows.append(_row(
        "2", "自适应演化（元学习）",
        "真实 turn ≥1000 且 manual guess→measured 单参数 ≥2 人日",
        "真实 turn=%d（目标 1000）；measured=0；人工耗时证据=%s"
        % (turns, "有" if eff_evidence else "无"),
        "turn %s / 效率证据 %s" % ("✅" if met2a else "⬜", "✅" if eff_evidence else "⬜"),
        "⬜ 未达标" if not (met2a and eff_evidence) else "✅ 已达标",
        "先积累真实样本；效率证据需人工记录到 acceptance/manual-calibration-effort.md"))

    # 方向三：群体心智
    rows.append(_row(
        "3", "群体心智（多 Agent 黑板）",
        "单 Agent 响应 P95 超业务阈值，或 ≥20 次写+审同时在线任务",
        "不可测（审计无端到端延迟/任务复杂度字段）", "—", "❔ 不可测",
        "先补审计：turn_latency_ms；业务阈值需 H0 给出"))

    # 方向四：多模态感知
    rows.append(_row(
        "4", "多模态感知",
        "DSH 官方稳定语音/图像接口 + 文本记忆成熟（measured≥5、记忆≥1000、稳定30天）",
        "measured=%d（目标 5）；真实记忆=%d（目标 1000）；宿主接口=需人工核实"
        % (c["measured_params"], c["memory_entries"]),
        "measured %s / 记忆 %s" % ("✅" if c["measured_params"] >= 5 else "⬜",
                                   "✅" if c["memory_entries"] >= 1000 else "⬜"),
        "⬜ 未达标",
        "宿主接口需人工核实官方发布说明；接口不稳定前不评"))

    met = sum(1 for r in rows if r["status"].startswith("✅"))
    return {"plugin_dir": plugin_dir, "metrics": c, "rows": rows, "met": met}


def render(res: dict) -> str:
    c = res["metrics"]
    lines = [
        "# 解冻状态检查（ADR-010 / 待办-冻结方向.md）",
        "",
        "> 由 `tools/unfreeze_status.py` 生成；**只读真实插件数据**（不含 .chaos-data 压测数据）。",
        "> 规则：触发条件未满足 → 只允许补证据，不允许做原型/加依赖/加配置开关。",
        "",
        "## 环境",
        "",
        "| 项 | 值 |",
        "|---|---|",
        "| 数据目录 | `%s` |" % res["plugin_dir"],
        "| 真实 turn | %d |" % c["turns"],
        "| 记忆条数（last-wins） | %d（active %d） |" % (c["memory_entries"], c["memory_active"]),
        "| 审计事件 / pre_step | %d / %d |" % (c["audit_events"], c["pre_step_events"]),
        "| `provenance: measured` 参数数 | %d |" % c["measured_params"],
        "",
        "## 四个方向",
        "",
        "| # | 方向 | 解冻触发 | 当前 | 达标 | 状态 |",
        "|---|---|---|---|---|---|",
    ]
    for r in res["rows"]:
        lines.append("| %s | %s | %s | %s | %s | %s |"
                     % (r["no"], r["name"], r["trigger"], r["current"], r["met"], r["status"]))
    lines += ["", "## 说明与下一步", ""]
    for r in res["rows"]:
        lines.append("- **%s**：%s" % (r["name"], r["note"]))
    lines += [
        "",
        "## 结论",
        "",
        "- 已达标方向：**%d / 4**" % res["met"],
        "- 未达标/不可测 ≠ 安全：不可测的项要优先补审计字段，否则触发条件永远不会被观测到。",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dir", default=os.path.join(_home(), "plugins", "dsh-lingshu"),
                    help="插件数据目录（缺省 $DSH_HOME/plugins/dsh-lingshu）")
    ap.add_argument("--out", default="", help="把 Markdown 报告写到这里")
    ap.add_argument("--json", action="store_true", help="额外打印 JSON")
    args = ap.parse_args()

    res = build(args.dir)
    md = render(res)
    if args.out:
        path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        io.open(path, "w", encoding="utf-8", newline="\n").write(md + "\n")
        print("written:", path)
    else:
        print(md)
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    print("\n已达标方向: %d / 4" % res["met"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
