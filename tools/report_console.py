# -*- coding: utf-8 -*-
"""LINGSHU 控制台 v0：把三个数据文件渲染成一个自包含 HTML（离线可看）。

    python tools/report_console.py
    python tools/report_console.py --home <DSH_HOME> --out console/index.html

为什么先做静态版：
- 不引入服务器、不碰宿主 profile、不需要前端构建；
- 数据全来自 `$DSH_HOME/plugins/dsh-lingshu/`（state.json / memory.jsonl / audit.jsonl / last-prompt.txt）；
- 生成的单个 .html 可以直接打开、可以发给别人看，**不含任何网络请求**。

它不替代交互式面板：改状态、点按钮纠正、开关参数，这些要写真正的 UI（DSH client half 或独立服务）。
"""
from __future__ import annotations

import argparse
import html
import io
import json
import os
import sys

DEFAULT_HOME = os.path.join(os.path.expanduser("~"), ".dsh")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def read_jsonl(path: str) -> list[dict]:
    out = []
    if not os.path.isfile(path):
        return out
    for line in io.open(path, encoding="utf-8"):
        line = line.strip().lstrip("\ufeff")
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            out.append({"__unparsable__": line[:120]})
    return out


def read_json(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        return json.loads(io.open(path, encoding="utf-8").read().lstrip("\ufeff"))
    except json.JSONDecodeError:
        return {}


def stage_of(turns: int, cold: int = 3, warm: int = 20) -> str:
    return "cold" if turns < cold else "warm" if turns < warm else "regular"


KIND_COLOR = {
    "apply": "#6b7280", "pre_step": "#2563eb", "turn_begin": "#0891b2",
    "turn_end_seen": "#0891b2", "agent_state": "#7c3aed", "memory_write": "#059669",
    "emotion": "#db2777", "hook_error": "#dc2626", "section_error": "#dc2626",
    "frozen_violation": "#dc2626", "memory_write_rejected": "#dc2626",
}


def render(home: str) -> str:
    pdir = os.path.join(home, "plugins", "dsh-lingshu")
    state = read_json(os.path.join(pdir, "state.json"))
    mem = read_jsonl(os.path.join(pdir, "memory.jsonl"))
    audit = read_jsonl(os.path.join(pdir, "audit.jsonl"))
    prompt_file = os.path.join(pdir, "last-prompt.txt")
    prompt = io.open(prompt_file, encoding="utf-8").read() if os.path.isfile(prompt_file) else ""

    turns = int(state.get("turns") or 0)
    stage = stage_of(turns)
    frozen = state.get("frozen") or {}
    goal = state.get("goal") or {}
    relation = state.get("relation") or {}
    active_goals = [g for g in (goal.get("goals") or {}).values() if g.get("status") == "active"]
    errors = [a for a in audit if a.get("kind") in ("hook_error", "section_error",
                                                    "frozen_violation")]

    def esc(x) -> str:
        return html.escape(str(x))

    cards = [
        ("阶段", stage, "累计 %d 轮" % turns),
        ("冻结区", "通过" if frozen.get("ok") else "失败/未校验",
         "校验 %s 个文件" % (frozen.get("checked") or 0)),
        ("记忆条目", str(len(mem)), "来源：" + ", ".join(sorted({m.get("origin", "?") for m in mem})) if mem else "尚无"),
        ("写请求", str(state.get("stateWrites") or 0), "已落盘"),
        ("活跃目标", str(len(active_goals)),
         active_goals[-1].get("content", "")[:24] if active_goals else "无"),
        ("关系", "%s / %s" % (relation.get("trustLevel", "low"), relation.get("stage", "new")),
         "冲突：%s" % ("有" if relation.get("unresolvedConflict") else "无")),
        ("审计事件", str(len(audit)), "错误 %d 条" % len(errors)),
    ]

    card_html = "".join(
        '<div class="card"><div class="k">%s</div><div class="v">%s</div>'
        '<div class="s">%s</div></div>' % (esc(k), esc(v), esc(s))
        for k, v, s in cards
    )

    mem_rows = "".join(
        "<tr><td><code>%s</code></td><td>%s</td><td>%s</td><td>%s</td>"
        "<td>%s</td><td class='q'>%s</td></tr>"
        % (esc(m.get("id", "")), esc(m.get("origin", "")), esc(m.get("type", "")),
           esc(m.get("content", "")), esc(round(float(m.get("importance") or 0), 2)),
           esc((m.get("sourceQuote") or "")[:42]))
        for m in reversed(mem[-200:])
    ) or "<tr><td colspan='6' class='muted'>还没有记忆条目</td></tr>"

    audit_rows = ""
    for a in reversed(audit[-200:]):
        kind = a.get("kind", "?")
        color = KIND_COLOR.get(kind, "#475569")
        detail = a.get("preview") or a.get("ids") or a.get("goalEvents") or a.get("err") or ""
        if isinstance(detail, list):
            detail = ", ".join(map(str, detail))[:60]
        audit_rows += (
            "<tr><td class='t'>%s</td><td><span class='tag' style='background:%s'>%s</span></td>"
            "<td class='muted'>%s</td></tr>"
            % (esc(a.get("iso") or a.get("ts") or ""), color, esc(kind), esc(str(detail)[:80]))
        )
    if not audit_rows:
        audit_rows = "<tr><td colspan='3' class='muted'>还没有审计事件</td></tr>"

    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<title>LINGSHU 控制台 · %(stage)s</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{--bg:#0f172a;--fg:#e2e8f0;--muted:#94a3b8;--line:#1e293b;--card:#111c33}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);font:14px/1.6 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}
header{padding:20px 24px;border-bottom:1px solid var(--line);display:flex;gap:16px;align-items:baseline}
h1{font-size:18px;margin:0}
.muted{color:var(--muted)}
main{padding:20px 24px;max-width:1180px;margin:0 auto}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:24px}
.card{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.card .k{color:var(--muted);font-size:12px}
.card .v{font-size:20px;font-weight:600;margin:2px 0}
.card .s{color:var(--muted);font-size:12px}
h2{font-size:15px;margin:26px 0 10px;padding-bottom:6px;border-bottom:1px solid var(--line)}
table{width:100%%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:500}
td.t{white-space:nowrap;color:var(--muted);font-size:12px}
td.q{color:var(--muted)}
code{background:#0b1226;padding:1px 5px;border-radius:4px}
.tag{color:#fff;padding:1px 7px;border-radius:999px;font-size:12px}
pre{background:#0b1226;border:1px solid var(--line);border-radius:10px;padding:14px;overflow:auto;white-space:pre-wrap}
footer{padding:24px;text-align:center;color:var(--muted);font-size:12px}
</style></head><body>
<header><h1>LINGSHU 控制台</h1>
<span class="muted">数据源 %(pdir)s ｜ 生成于 %(now)s ｜ 纯静态、无网络请求</span></header>
<main>
<div class="cards">%(cards)s</div>

<h2>本轮注入预览（4 段，按 order 拼装）</h2>
<pre>%(prompt)s</pre>

<h2>记忆条目（最近 200 条）</h2>
<table><thead><tr><th>id</th><th>来源</th><th>类型</th><th>内容</th><th>重要度</th><th>逐字引用</th></tr></thead>
<tbody>%(mem_rows)s</tbody></table>

<h2>审计时间线（最近 200 条）</h2>
<table><thead><tr><th>时间</th><th>事件</th><th>详情</th></tr></thead>
<tbody>%(audit_rows)s</tbody></table>
</main>
<footer>LINGSHU · 可观测性优先于功能（docs/14）｜ 这一页只读，改状态要走插件自己的入口</footer>
</body></html>
""" % {
        "stage": esc(stage),
        "pdir": esc(pdir),
        "now": esc(__import__("datetime").datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        "cards": card_html,
        "prompt": esc(prompt) or "<span class='muted'>还没有注入记录（跑一轮对话后会有）</span>",
        "mem_rows": mem_rows,
        "audit_rows": audit_rows,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=os.environ.get("DSH_HOME", DEFAULT_HOME))
    ap.add_argument("--out", default=os.path.join(ROOT, "console", "index.html"))
    ap.add_argument("--open", action="store_true")
    args = ap.parse_args()

    page = render(args.home)
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    io.open(args.out, "w", encoding="utf-8", newline="\n").write(page)
    size = os.path.getsize(args.out)
    print("已生成: %s（%.1f KB）" % (args.out, size / 1024))

    if args.open:
        os.startfile(args.out)  # type: ignore[attr-defined]
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
