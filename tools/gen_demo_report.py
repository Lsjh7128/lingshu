# -*- coding: utf-8 -*-
"""LINGSHU · 一键 Demo 报告生成器（真实日志 + 控制论阈值演示）

    python tools/gen_demo_report.py
    python tools/gen_demo_report.py --home <DSH_HOME> --out console/demo.html
    python tools/gen_demo_report.py --no-real     # 强制空态（验证"没有真实日志也能跑"）

产物：单个自包含 HTML（内联 CSS / 内联 SVG，无外部字体/脚本/图片、无网络请求）。
数据：
  1) **真实数据** —— 来自 `$DSH_HOME/plugins/dsh-lingshu/audit.jsonl` 与 `state.json`。
     只提取指标字段白名单（kind/ts/turn/step/stage/matched/excluded/level/firstStep/
     chars/phase/ratio/anchor/verdict/ucas/downgrades/blocked），
     **不输出**任何原始消息文本、记忆内容、目标内容、本机路径或用户名。
  2) **演示数据** —— 相变阈值曲线，由 `engine/control.py` 的 `PhaseShiftMonitor` /
     `apply_persona_anchor` **实际扫描 ratio 0..1.0 计算**，数学不复制、不手写。
     页面在视觉（虚线/琥珀/紫）与文字（"演示：由实现计算，非真实流量"）上与真实数据区分。

确定性：不写生成时刻、不用随机数；固定输入 → 逐字节相同的输出。
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_HOME = os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh")
PLUGIN_REL = os.path.join("plugins", "dsh-lingshu")

# ── 脱敏：只允许这些字段进入 HTML ────────────────────────────────
SAFE_KEYS = frozenset({
    "kind", "ts", "turn", "step", "stage", "matched", "excluded", "level",
    "firstStep", "chars", "phase", "ratio", "anchor", "verdict", "ucas",
    "downgrades", "blocked", "written", "rejected", "sections", "frozenOk",
    "toolOk", "turns",
})
SAFE_LIST_KEYS = frozenset({"ucas", "downgrades", "rejected"})
SAFE_STR_KEYS = frozenset({"kind", "stage", "level", "phase", "verdict"})

TAG_REAL = '<span class="tag tag-real">真实数据</span>'
TAG_DEMO = '<span class="tag tag-demo">演示数据</span>'


# ── 基础工具 ─────────────────────────────────────────────────

def _num(v, default=0):
    try:
        return float(v)
    except (TypeError, ValueError):
        return float(default)


def _int(v, default=0):
    try:
        return int(v)
    except (TypeError, ValueError):
        return int(default)


def esc(x) -> str:
    return html.escape(str(x))


def f2(v) -> str:
    """稳定两位小数格式化（不产生 -0.00）。"""
    s = "%.2f" % float(v)
    return "0.00" if s == "-0.00" else s


def pct(v) -> str:
    return "%d%%" % int(round(float(v) * 100))


def _bool_text(v) -> str:
    if v is True:
        return "true"
    if v is False:
        return "false"
    return "未校验"


# ── 读取 / 脱敏 ─────────────────────────────────────────────

def read_jsonl(path):
    """返回 (解析成功的事件列表, 无法解析行数)。不保留无法解析行的原文。"""
    events, bad = [], 0
    if not os.path.isfile(path):
        return events, bad
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            line = line.strip().lstrip("\ufeff")
            if not line:
                continue
            try:
                raw = json.loads(line)
            except json.JSONDecodeError:
                bad += 1
                continue
            if isinstance(raw, dict):
                events.append(sanitize_event(raw))
            else:
                bad += 1
    events.sort(key=lambda e: _num(e.get("ts")))
    return events, bad


def sanitize_event(raw):
    out = {}
    for k in SAFE_KEYS:
        if k not in raw:
            continue
        v = raw[k]
        if k in SAFE_LIST_KEYS:
            out[k] = [str(x)[:16] for x in v][:12] if isinstance(v, list) else []
        elif k in SAFE_STR_KEYS:
            out[k] = str(v)[:16]
        elif isinstance(v, bool):
            out[k] = v
        elif isinstance(v, (int, float)):
            out[k] = v
        elif v is None:
            out[k] = None
    return out


def read_json(path):
    if not os.path.isfile(path):
        return {}
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            data = json.loads(fh.read().lstrip("\ufeff"))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def sanitize_state(raw):
    ctrl = raw.get("control") if isinstance(raw.get("control"), dict) else {}
    phase = ctrl.get("phase") if isinstance(ctrl.get("phase"), dict) else {}
    audit = ctrl.get("audit") if isinstance(ctrl.get("audit"), dict) else {}
    frozen = raw.get("frozen") if isinstance(raw.get("frozen"), dict) else {}
    sat = ctrl.get("saturated") if isinstance(ctrl.get("saturated"), dict) else {}
    return {
        "turns": _int(raw.get("turns")),
        "sessions": _int(raw.get("sessions")),
        "stateWrites": _int(raw.get("stateWrites")),
        "coldStartSuppressed": _int(raw.get("coldStartSuppressed")),
        "frozen_ok": frozen.get("ok"),
        "frozen_checked": _int(frozen.get("checked")),
        "phase": {
            "ratio": _num(phase.get("ratio")),
            "phase": str(phase.get("phase") or "?"),
            "anchorRequired": bool(phase.get("anchorRequired")),
            "prefeed": bool(phase.get("prefeed")),
        },
        "audit": {
            "verdict": str(audit.get("verdict") or "?"),
            "ucas": [str(x)[:16] for x in (audit.get("ucas") or [])][:12],
            "downgrades": [str(x)[:16] for x in (audit.get("downgrades") or [])][:12],
        },
        "blocked": bool(ctrl.get("blocked")),
        "saturated": {str(k): bool(v) for k, v in sat.items()},
    }


def load_logs(home):
    pdir = os.path.join(home, PLUGIN_REL)
    events, bad = read_jsonl(os.path.join(pdir, "audit.jsonl"))
    state = sanitize_state(read_json(os.path.join(pdir, "state.json")))
    return {"events": events, "unparsable": bad, "state": state,
            "audit_present": os.path.isfile(os.path.join(pdir, "audit.jsonl")),
            "state_present": os.path.isfile(os.path.join(pdir, "state.json"))}


# ── 真实数据：timeline / 轮次 / STPA ─────────────────────────

def build_timeline(logs):
    events = logs["events"]
    pre = [e for e in events if e.get("kind") == "pre_step"]
    begins = [e for e in events if e.get("kind") == "turn_begin"]
    ends = [e for e in events if e.get("kind") == "turn_end_seen"]

    end_by_turn = {}
    for e in ends:
        end_by_turn.setdefault(_int(e.get("turns")), _int(e.get("ts")))

    bounds = sorted((_int(e.get("ts")), _int(e.get("turns")), str(e.get("stage") or "?"))
                    for e in begins)
    turns = []
    for ts, turn, stage in bounds:
        end_ts = end_by_turn.get(turn)
        turns.append({
            "turn": turn, "stage": stage, "begin_ts": ts, "end_ts": end_ts,
            "duration_s": round((end_ts - ts) / 1000.0, 1) if end_ts and end_ts >= ts else None,
            "steps": 0, "first_steps": 0, "chars": 0, "matched": 0,
        })
    by_turn = {t["turn"]: t for t in turns}

    def turn_of(ts):
        best = None
        for b_ts, b_turn, _stage in bounds:
            if b_ts <= ts:
                best = b_turn
            else:
                break
        return best

    for e in pre:
        t = by_turn.get(turn_of(_int(e.get("ts"))))
        if t is None:
            continue
        t["steps"] += 1
        t["chars"] += _int(e.get("chars"))
        t["matched"] += _int(e.get("matched"))
        if e.get("firstStep"):
            t["first_steps"] += 1

    measured = [e for e in pre if "ratio" in e]
    ratio_values = [round(_num(e.get("ratio")), 2) for e in measured]
    return {
        "pre": pre, "measured": measured, "turns": turns,
        "ratio_values": ratio_values,
        "max_chars": max([_int(e.get("chars")) for e in pre] or [0]),
        "max_matched": max([_int(e.get("matched")) for e in pre] or [0]),
        "max_excluded": max([_int(e.get("excluded")) for e in pre] or [0]),
        "unmeasured_ratio": len(pre) - len(measured),
    }


def build_stpa(logs, tl):
    events = logs["events"]
    pre = tl["pre"]
    measured = tl["measured"]
    verdicts = [str(e.get("verdict")) for e in measured]
    kinds = {}
    for e in events:
        k = str(e.get("kind") or "?")
        kinds[k] = kinds.get(k, 0) + 1

    uca_counts = {}
    for e in measured:
        for u in e.get("ucas") or []:
            uca_counts[u] = uca_counts.get(u, 0) + 1
    uca_total = sum(uca_counts.values())
    uca08 = sum(1 for e in measured if "UCA-08" in (e.get("downgrades") or []))
    blocked_flag = sum(1 for e in pre if e.get("blocked") is True)
    writes = sum(_int(e.get("written")) for e in events if e.get("kind") == "memory_write")
    rejected = sum(len(e.get("rejected") or []) for e in events
                   if e.get("kind") == "memory_write")
    sections = {}
    for e in events:
        if e.get("kind") == "apply" and e.get("sections"):
            s = _int(e.get("sections"))
            sections[s] = sections.get(s, 0) + 1

    rows = [
        ("preflight verdict = allow", verdicts.count("allow"), "放行", "#34d399"),
        ("preflight verdict = block", verdicts.count("block"), "熔断", "#f87171"),
        ("UCA 命中", uca_total, "命中", "#f87171"),
        ("UCA-08 降级留痕", uca08, "降级", "#f59e0b"),
        ("blocked=true 的 step", blocked_flag, "回执", "#f87171"),
        ("preflight_block 审计事件", kinds.get("preflight_block", 0), "留痕", "#f87171"),
        ("hook_error", kinds.get("hook_error", 0), "异常", "#f87171"),
        ("frozen_violation", kinds.get("frozen_violation", 0), "不匹配", "#f87171"),
        ("memory_write_rejected", kinds.get("memory_write_rejected", 0),
         "被拒", "#f87171"),
    ]
    return {
        "measured_count": len(measured),
        "allow": verdicts.count("allow"),
        "block": verdicts.count("block"),
        "uca_total": uca_total,
        "uca_counts": uca_counts,
        "uca08": uca08,
        "blocked_flag": blocked_flag,
        "writes": writes,
        "rejected": rejected,
        "sections": sections,
        "kinds": kinds,
        "rows": rows,
    }


# ── 演示数据：直接用 engine/control.py 扫描 ──────────────────

def build_threshold_demo():
    """扫描 ratio 0..1.0，调用 engine/control.py 的实际实现，不复制数学。"""
    sys.path.insert(0, ROOT)
    from engine import config as cfgmod
    from engine.control import (ANCHOR_PREFIX, ANCHOR_SUFFIX,
                                PhaseShiftMonitor, apply_persona_anchor)

    cfg = cfgmod.default()
    ctrl = cfg["control"]
    shift = float(ctrl["phaseShiftRatio"])
    hard = float(ctrl["phaseShiftHardRatio"])

    ratios = [round(i / 100.0, 2) for i in range(101)]
    steady = []
    for r in ratios:
        res = PhaseShiftMonitor(cfg).observe(int(round(r * 1000)), 1000)
        prompt = apply_persona_anchor("__demo__", res)
        steady.append({
            "ratio": r,
            "phase": str(res["phase"]),
            "prefeed": bool(res["prefeed"]),
            "anchorRequired": bool(res["anchor_required"]),
            "segments": prompt.count(ANCHOR_PREFIX) + prompt.count(ANCHOR_SUFFIX),
        })

    # 快速爬升：单个 monitor 连续 observe，每 step +5%（trend = 0.05 > trendThreshold 0.02）
    fast_step = 0.05
    fast_ratios = [round(i * fast_step, 2) for i in range(21)]
    monitor = PhaseShiftMonitor(cfg)
    fast = []
    for r in fast_ratios:
        res = monitor.observe(int(round(r * 1000)), 1000)
        prompt = apply_persona_anchor("__demo__", res)
        fast.append({
            "ratio": r,
            "phase": str(res["phase"]),
            "trend": float(res["trend"]),
            "anchorRequired": bool(res["anchor_required"]),
            "segments": prompt.count(ANCHOR_PREFIX) + prompt.count(ANCHOR_SUFFIX),
        })

    first_anchor = next((s["ratio"] for s in steady if s["segments"] > 0), None)
    first_anchor_fast = next((s["ratio"] for s in fast if s["anchorRequired"]), None)
    first_critical = next((s["ratio"] for s in steady if s["phase"] == "critical"), None)
    return {
        "shift": shift, "hard": hard,
        "ratios": ratios, "steady": steady, "fast": fast, "fast_step": fast_step,
        "first_anchor": first_anchor,
        "first_anchor_fast": first_anchor_fast,
        "first_critical": first_critical,
        "trend_threshold": float(ctrl.get("trendThreshold", 0.02)),
        "ratio_max": float(ctrl.get("ratioMax", 1.5)),
    }


# ── SVG 片段 ─────────────────────────────────────────────────

def _svg_open(w, h, label, extra=""):
    return ('<svg class="fig" viewBox="0 0 %d %d" role="img" aria-label="%s" '
            'preserveAspectRatio="xMidYMid meet">%s'
            % (w, h, esc(label), extra))


def svg_empty(w, h, title, detail):
    return _svg_open(w, h, title) + (
        '<rect x="0" y="0" width="%d" height="%d" rx="14" fill="#0b1220" '
        'stroke="#1e293b"/>' % (w, h)
        + '<text x="%d" y="%d" text-anchor="middle" fill="#e2e8f0" '
          'font-size="22" font-weight="600">%s</text>' % (w // 2, h // 2 - 16, esc(title))
        + '<text x="%d" y="%d" text-anchor="middle" fill="#94a3b8" '
          'font-size="14">%s</text>' % (w // 2, h // 2 + 20, esc(detail))
        + "</svg>"
    )


def _lane(svg, y, h, title, note=""):
    svg.append('<rect x="0" y="%d" width="1040" height="%d" rx="10" fill="#0f1a2e" '
               'stroke="#1b2942"/>' % (y, h))
    svg.append('<text x="18" y="%d" fill="#cbd5e1" font-size="12" '
               'font-weight="600">%s</text>' % (y - 9, esc(title)))
    if note:
        svg.append('<text x="1022" y="%d" text-anchor="end" fill="#64748b" '
                   'font-size="11">%s</text>' % (y - 9, esc(note)))


def svg_timeline(tl):
    """图 1：真实会话 timeline（每 step 的注入字符、召回、ratio、verdict、anchor）。"""
    if not tl["pre"]:
        return svg_empty(1040, 420, "暂无真实流量",
                         "没有可解析的 pre_step 事件；图表 2 的阈值演示仍可生成。")
    n = len(tl["pre"])
    L, R = 96, 24
    W, H = 1040, 522
    pw = W - L - R

    def x(i):
        return L + (i + 0.5) * pw / n

    svg = [_svg_open(W, H, "真实会话 timeline")]
    svg.append('<rect x="0" y="0" width="%d" height="%d" rx="14" fill="#0b1220" '
               'stroke="#1e293b"/>' % (W, H))

    # lane 1：注入字符数
    y1, h1 = 48, 104
    _lane(svg, y1, h1, "注入字符数 chars / step", "真实 · 来自 pre_step.chars")
    max_ch = tl["max_chars"] or 1
    base1 = y1 + h1 - 12
    svg.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#334155"/>'
               % (L, base1, W - R, base1))
    svg.append('<text x="%d" y="%.1f" text-anchor="end" fill="#64748b" '
               'font-size="10">%d</text>' % (L - 8, y1 + 14, tl["max_chars"]))
    svg.append('<text x="%d" y="%.1f" text-anchor="end" fill="#64748b" '
               'font-size="10">0</text>' % (L - 8, base1 - 2))
    for i, e in enumerate(tl["pre"]):
        ch = _int(e.get("chars"))
        if ch <= 0:
            continue
        hh = (h1 - 24) * ch / float(max_ch)
        svg.append('<rect x="%.1f" y="%.1f" width="7" height="%.1f" rx="1.5" '
                   'fill="#38bdf8" opacity="0.9"><title>step %d · chars=%d</title>'
                   '</rect>' % (x(i) - 3.5, base1 - hh, hh, i + 1, ch))

    # lane 2：召回命中 / 预算外
    y2, h2 = 186, 74
    _lane(svg, y2, h2, "记忆召回 matched / 预算外 excluded / step",
          "真实 · 来自 pre_step.matched/excluded")
    base2 = y2 + h2 - 14
    scale2 = max(tl["max_matched"], tl["max_excluded"], 1)
    svg.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#334155"/>'
               % (L, base2, W - R, base2))
    for i, e in enumerate(tl["pre"]):
        m = _int(e.get("matched"))
        ex = _int(e.get("excluded"))
        yy = base2 - (h2 - 30) * m / float(scale2)
        svg.append('<circle cx="%.1f" cy="%.1f" r="2.4" fill="#a78bfa" opacity="0.85">'
                   '<title>step %d · matched=%d</title></circle>' % (x(i), yy, i + 1, m))
        if ex:
            svg.append('<rect x="%.1f" y="%.1f" width="4" height="4" fill="#f59e0b" '
                       'opacity="0.8"><title>step %d · excluded=%d</title></rect>'
                       % (x(i) - 2, base2 - (h2 - 30) * ex / float(scale2) - 2, i + 1, ex))
    if tl["max_matched"] == 0 and tl["max_excluded"] == 0:
        svg.append('<text x="%d" y="%.1f" fill="#a78bfa" font-size="12">'
                   'matched / excluded 全部为 0：这些 step 没有召回命中，'
                   '也没有预算外剔除（真实，不是缺失）</text>'
                   % (L + 12, y2 + 30))

    # lane 3：ratio（只有新版插件落该字段）
    y3, h3 = 292, 76
    _lane(svg, y3, h3, "上下文用量 ratio / step",
          "真实 · 仅 %d/%d step 有该字段，其余未记录" % (len(tl["measured"]), n))
    base3 = y3 + h3 - 14
    svg.append('<line x1="%d" y1="%.1f" x2="%d" y2="%.1f" stroke="#334155"/>'
               % (L, base3, W - R, base3))
    max_r = max([_num(e.get("ratio")) for e in tl["measured"]] or [0.0])
    max_r = max(max_r, 0.05)
    last_label_x = -1e9
    for i, e in enumerate(tl["pre"]):
        if "ratio" not in e:
            svg.append('<rect x="%.1f" y="%.1f" width="4" height="4" fill="#334155" '
                       'opacity="0.55"><title>step %d · ratio 未记录</title></rect>'
                       % (x(i) - 2, base3 - 3, i + 1))
            continue
        r = _num(e.get("ratio"))
        yy = base3 - (h3 - 30) * min(r, 1.0) / float(max_r)
        svg.append('<circle cx="%.1f" cy="%.1f" r="4" fill="#34d399" stroke="#052e1b">'
                   '<title>step %d · ratio=%.2f phase=%s</title></circle>'
                   % (x(i), yy, i + 1, r, esc(e.get("phase"))))
        if x(i) - last_label_x >= 26:      # 相邻测量点的数值标签不叠字
            svg.append('<text x="%.1f" y="%.1f" text-anchor="middle" fill="#34d399" '
                       'font-size="10">%s</text>' % (x(i), yy - 10, f2(r)))
            last_label_x = x(i)
    if tl["measured"]:
        svg.append('<text x="%d" y="%d" fill="#64748b" font-size="10">'
                   '绿色圆点 = 真实测量（%d 个），其余为未记录</text>'
                   % (L + 10, y3 + 32, len(tl["measured"])))

    # lane 4：verdict
    y4, h4 = 396, 44
    _lane(svg, y4, h4, "preflight verdict / step",
          "真实 · block=红 · allow=绿 · 未记录=灰")
    cy4 = y4 + h4 / 2 + 2
    for i, e in enumerate(tl["pre"]):
        v = e.get("verdict")
        if v == "allow":
            svg.append('<rect x="%.1f" y="%.1f" width="6" height="16" rx="2" '
                       'fill="#34d399" opacity="0.9">'
                       '<title>step %d · verdict=allow</title></rect>'
                       % (x(i) - 3, cy4 - 8, i + 1))
        elif v == "block":
            svg.append('<rect x="%.1f" y="%.1f" width="6" height="16" rx="2" '
                       'fill="#f87171" opacity="0.95">'
                       '<title>step %d · verdict=block</title></rect>'
                       % (x(i) - 3, cy4 - 8, i + 1))
        else:
            svg.append('<rect x="%.1f" y="%.1f" width="4" height="6" fill="#334155" '
                       'opacity="0.5"><title>step %d · verdict 未记录</title></rect>'
                       % (x(i) - 2, cy4 - 3, i + 1))

    # lane 5：anchor
    y5, h5 = 468, 34
    _lane(svg, y5, h5, "首尾人格锚点 anchor / step", "真实 · true=琥珀 · false=绿 · 未记录=灰")
    cy5 = y5 + h5 / 2 + 2
    for i, e in enumerate(tl["pre"]):
        a = e.get("anchor")
        if a is True:
            fill, op = "#f59e0b", "0.95"
        elif a is False:
            fill, op = "#2dd4bf", "0.55"
        else:
            fill, op = "#334155", "0.4"
        svg.append('<circle cx="%.1f" cy="%.1f" r="3" fill="%s" opacity="%s">'
                   '<title>step %d · anchor=%s</title></circle>'
                   % (x(i), cy5, fill, op, i + 1, "true" if a is True else
                      "false" if a is False else "未记录"))

    # x 轴刻度
    ticks = sorted({0, n // 4, n // 2, 3 * n // 4, n - 1})
    for i in ticks:
        svg.append('<text x="%.1f" y="514" text-anchor="middle" fill="#64748b" '
                   'font-size="10">%d</text>' % (x(i), i + 1))
    svg.append('<text x="12" y="514" fill="#64748b" font-size="10">'
               'step 序号 →</text>')
    svg.append("</svg>")
    return "".join(svg)


def svg_threshold(d, real_values):
    """图 2：相变阈值 + 首尾锚点注入曲线（演示：由 engine/control.py 实际扫描）。"""
    W, H = 1040, 500
    L, R, T, B = 96, 24, 74, 366
    pw = W - L - R
    shift, hard = d["shift"], d["hard"]

    def x(r):
        return L + r * pw

    vmax = 2.2

    def y(v):
        return B - (B - T) * float(v) / vmax

    svg = [_svg_open(W, H, "相变阈值与锚点注入曲线（演示）")]
    svg.append('<rect x="0" y="0" width="%d" height="%d" rx="14" fill="#0b1220" '
               'stroke="#1e293b"/>' % (W, H))

    # 阈值分区背景
    bands = [(0.0, shift, "normal 正常"),
             (shift, hard, "shift 预警 %s" % pct(shift)),
             (hard, 1.0, "critical 临界 %s" % pct(hard))]
    colors = ["#0f1a2e", "#3b2a12", "#3b1518"]
    for idx, (a, b, label) in enumerate(bands):
        svg.append('<rect x="%.1f" y="%d" width="%.1f" height="%d" fill="%s" '
                   'opacity="0.85"/>' % (x(a), T, x(b) - x(a), B - T, colors[idx]))
        svg.append('<text x="%.1f" y="%d" text-anchor="middle" fill="#94a3b8" '
                   'font-size="11">%s</text>' % ((x(a) + x(b)) / 2, T - 26, esc(label)))

    for tv, name in ((shift, "shift"), (hard, "critical")):
        svg.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#e2e8f0" '
                   'stroke-width="1.4" stroke-dasharray="5 4" opacity="0.75"/>'
                   % (x(tv), T, x(tv), B))
        svg.append('<text x="%.1f" y="%d" fill="#e2e8f0" font-size="11" '
                   'text-anchor="middle">%s</text>' % (x(tv), T - 8, f2(tv)))

    svg.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#334155"/>'
               % (L, B, W - R, B))
    # y 轴
    for v in (0, 1, 2):
        svg.append('<text x="%d" y="%.1f" text-anchor="end" fill="#64748b" '
                   'font-size="10">%d</text>' % (L - 8, y(v) + 4, v))
    svg.append('<text x="18" y="%d" fill="#94a3b8" font-size="11">标志 / 段数</text>'
               % (T + 6))

    def step_groups(samples, key):
        """把扫描结果压成连续同值区间 [(start_ratio, end_ratio, value)]。"""
        groups = []
        start, cur, prev_r = samples[0]["ratio"], samples[0][key], samples[0]["ratio"]
        for s in samples[1:]:
            r, v = s["ratio"], s[key]
            if v != cur:
                groups.append((start, prev_r, cur))
                start, cur = r, v
            prev_r = r
        groups.append((start, prev_r, cur))
        return groups

    def step_svg(samples, key, yof, color, dash="", width=2.0):
        """阶跃曲线：只画非零平台 + 跃迁竖线。0 基线由横轴代表，
        避免三条曲线在 y=0 处互相覆盖。"""
        out = []
        dash_attr = ' stroke-dasharray="%s"' % dash if dash else ""
        groups = step_groups(samples, key)
        for a, b, v in groups:
            if v == 0:
                continue
            yy = yof(v)
            out.append('<path d="M %.1f,%.1f L %.1f,%.1f" fill="none" stroke="%s" '
                       'stroke-width="%.1f"%s/>'
                       % (x(a), yy, x(b), yy, color, width, dash_attr))
        for i in range(1, len(groups)):
            v0 = groups[i - 1][2]
            r_jump, v1 = groups[i][0], groups[i][2]
            if v0 == v1:
                continue
            out.append('<path d="M %.1f,%.1f L %.1f,%.1f" fill="none" stroke="%s" '
                       'stroke-width="1.2" stroke-dasharray="4 4" opacity="0.75"/>'
                       % (x(r_jump), yof(v0), x(r_jump), yof(v1), color))
        return "".join(out)

    steady, fast = d["steady"], d["fast"]
    series = [
        ("首尾锚点注入段数（稳态扫描，0 → 2 段）",
         step_svg(steady, "segments", lambda v: y(v), "#f59e0b", width=2.8),
         "#f59e0b", "", 2.8),
        ("anchor_required（稳态：无上升趋势）",
         step_svg(steady, "anchorRequired",
                  lambda v: y(1.0 if v else 0.0), "#34d399", width=2.2),
         "#34d399", "", 2.2),
        ("anchor_required（快速爬升 +%d%%/step）" % int(round(d["fast_step"] * 100)),
         step_svg(fast, "anchorRequired",
                  lambda v: y(1.0 if v else 0.0), "#a78bfa", "7 4", 2.2),
         "#a78bfa", "7 4", 2.2),
    ]
    for _label, markup, _color, _dash, _w in series:
        svg.append(markup)
    svg.append('<text x="%.1f" y="%.1f" fill="#64748b" font-size="10">'
               '低于阈值时三者均为 0（0 基线由横轴表示）</text>'
               % (L + 10, y(0) - 10))
    svg.append('<text x="%.1f" y="%.1f" text-anchor="middle" fill="#475569" '
               'font-size="12">正常区：无预警、无锚点注入</text>'
               % (x(0.35), y(1.0)))

    # 关键跃迁点标注（位置来自实现扫描结果，不是手画）
    if d["first_anchor"] is not None:
        ax, ay = x(d["first_anchor"]), y(2.0)
        svg.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="#f59e0b" stroke="#3b2a12"/>'
                   % (ax, ay))
        svg.append('<text x="%.1f" y="%.1f" fill="#fcd34d" font-size="10">'
                   '%s 起：首 + 尾各 1 段锚点</text>'
                   % (ax + 8, ay - 7, pct(d["first_anchor"])))
    if d["first_critical"] is not None:
        cx, cy = x(d["first_critical"]), y(1.0)
        svg.append('<circle cx="%.1f" cy="%.1f" r="4.5" fill="#34d399" stroke="#052e1b"/>'
                   % (cx, cy))
        svg.append('<text x="%.1f" y="%.1f" fill="#6ee7b7" font-size="10">'
                   '%s 起：critical（anchor 强制）</text>'
                   % (cx + 8, cy - 7, pct(d["first_critical"])))

    # 图例（SVG 内）
    legend_x, legend_y = L + 8, B + 22
    for i, (label, _markup, color, dash, width) in enumerate(series):
        yy = legend_y + i * 17
        dash_attr = ' stroke-dasharray="%s"' % dash if dash else ""
        svg.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="%s" '
                   'stroke-width="%.1f"%s/>' % (legend_x, yy, legend_x + 26, yy, color, width, dash_attr))
        svg.append('<text x="%.1f" y="%d" fill="#cbd5e1" font-size="11">%s</text>'
                   % (legend_x + 34, yy + 4, esc(label)))

    # 真实观测点（与演示曲线在视觉上分开：绿色菱形，画在底部真实观测带）
    real_y = B + 82
    svg.append('<line x1="%d" y1="%d" x2="%d" y2="%d" stroke="#1b2942"/>'
               % (L, real_y, W - R, real_y))
    svg.append('<text x="18" y="%d" fill="#34d399" font-size="11">真实观测带</text>' % (real_y + 4))
    if real_values:
        for r in real_values:
            rr = max(0.0, min(1.0, float(r)))
            svg.append('<path d="M %.1f,%d l 6,-6 l 6,6 l -6,6 z" fill="#34d399">'
                       '<title>真实观测 ratio=%.2f</title></path>'
                       % (x(rr) - 6, real_y, float(r)))
        svg.append('<text x="%.1f" y="%d" fill="#34d399" font-size="11">'
                   '真实日志观测 ratio（n=%d）：%s —— 与上面的演示曲线无关</text>'
                   % (L + 8, real_y + 20, len(real_values),
                      ", ".join(f2(v) for v in sorted(set(round(float(v), 2) for v in real_values)))))
    else:
        svg.append('<text x="%.1f" y="%d" fill="#64748b" font-size="11">'
                   '没有真实 ratio 观测（插件旧版本未落该字段）</text>'
                   % (L + 8, real_y + 20))

    # x 轴刻度
    for i in range(0, 11):
        r = i / 10.0
        svg.append('<line x1="%.1f" y1="%d" x2="%.1f" y2="%d" stroke="#1b2942"/>'
                   % (x(r), B, x(r), B + 5))
        svg.append('<text x="%.1f" y="%d" text-anchor="middle" fill="#64748b" '
                   'font-size="10">%s</text>' % (x(r), B + 16, pct(r)))
    svg.append("</svg>")
    return "".join(svg)


def svg_stpa(s):
    """图 3：STPA / fail-closed 拦截统计（真实事件计数）。"""
    rows = s["rows"]
    W = 1040
    L, R = 320, 116
    row_h = 34
    T = 58
    H = T + row_h * len(rows) + 30
    bar_w = W - L - R
    vmax = max([v for _l, v, _n, _c in rows] + [1])

    svg = [_svg_open(W, H, "STPA 拦截统计")]
    svg.append('<rect x="0" y="0" width="%d" height="%d" rx="14" fill="#0b1220" '
               'stroke="#1e293b"/>' % (W, H))
    svg.append('<text x="18" y="26" fill="#cbd5e1" font-size="12" font-weight="600">'
               '真实审计事件计数（0 = 未发生，不是"肯定安全"）</text>')
    for i, (label, value, note, color) in enumerate(rows):
        yy = T + i * row_h
        fill = color if value > 0 else "#475569"
        svg.append('<text x="%d" y="%d" text-anchor="end" fill="#cbd5e1" font-size="12">%s</text>'
                   % (L - 14, yy + 4, esc(label)))
        svg.append('<rect x="%d" y="%d" width="%d" height="16" rx="4" fill="#111c33"/>'
                   % (L, yy - 9, bar_w))
        w = 0 if value <= 0 else max(6, bar_w * value / float(vmax))
        svg.append('<rect x="%d" y="%d" width="%.1f" height="16" rx="4" fill="%s" '
                   'opacity="0.9"/>' % (L, yy - 9, w, fill))
        if w > 0:
            svg.append('<text x="%.1f" y="%d" text-anchor="end" fill="#052e1b" '
                       'font-size="13" font-weight="700">%d</text>'
                       % (L + w - 8, yy + 5, value))
        else:
            svg.append('<text x="%d" y="%d" fill="#e2e8f0" font-size="13" '
                       'font-weight="600">%d</text>' % (L + 10, yy + 5, value))
        svg.append('<text x="%d" y="%d" fill="#64748b" font-size="11">%s</text>'
                   % (W - R + 22, yy + 4, esc(note)))
    svg.append("</svg>")
    return "".join(svg)


# ── HTML 片段 ────────────────────────────────────────────────

def _cards(logs, tl, stpa):
    state = logs["state"]
    turns = max(state["turns"], max([t["turn"] for t in tl["turns"]] or [0]))
    seven = stpa["sections"].get(7, 0)
    four = stpa["sections"].get(4, 0)
    cards = [
        ("真实会话", "%d 轮" % turns,
         "%d 条审计事件 / %d 个 step" % (len(logs["events"]), len(tl["pre"]))),
        ("注入分段（真实）", "7 段 ×%d" % seven,
         "v4.2 七段注入；旧版 4 段 ×%d" % four),
        ("preflight（真实）", "allow %d / block %d" % (stpa["allow"], stpa["block"]),
         "可判定样本 %d；UCA 命中 %d" % (stpa["measured_count"], stpa["uca_total"])),
        ("相变阈值（演示）", "%s / %s" % (pct(0.70), pct(0.85)),
         "由 engine/control.py 扫描生成，非真实流量"),
    ]
    out = []
    for k, v, sub in cards:
        out.append('<div class="card"><div class="k">%s</div><div class="v">%s</div>'
                   '<div class="s">%s</div></div>' % (esc(k), esc(v), esc(sub)))
    return "".join(out)


def _measured_table(tl):
    measured = tl["measured"]
    if not measured:
        return ('<p class="empty">暂无真实测量：所有 step 都没有 ratio / verdict 字段'
                '（插件旧版本）。这不是 0，是"未记录"。</p>')
    rows = []
    for i, e in enumerate(tl["pre"]):
        if "ratio" not in e:
            continue
        rows.append(
            "<tr><td>%d</td><td>%s</td><td>%s</td><td>%s</td><td>%s</td>"
            "<td>%s</td><td>%s</td><td>%d</td></tr>" % (
                i + 1, esc(e.get("stage")), f2(_num(e.get("ratio"))),
                esc(e.get("phase")), esc("true" if e.get("anchor") is True else
                                         "false" if e.get("anchor") is False else "未记录"),
                esc(e.get("verdict")), esc(", ".join(e.get("ucas") or []) or "无"),
                _int(e.get("chars"))))
    head = ("<table><thead><tr><th>step</th><th>阶段</th><th>ratio</th><th>phase</th>"
            "<th>anchor</th><th>verdict</th><th>ucas</th><th>chars</th></tr></thead>"
            "<tbody>%s</tbody></table>" % "".join(rows))
    unmeasured = tl["unmeasured_ratio"]
    note = ""
    if unmeasured:
        note = ('<p class="note">其余 <b>%d</b> 个 step 没有 ratio/verdict 字段——'
                '这些是<b>未记录</b>，不能当作 0 或 allow。下表只展示真实测量行。</p>'
                % unmeasured)
    return head + note


def _turns_table(tl):
    turns = tl["turns"]
    if not turns:
        return '<p class="empty">无 turn_begin 事件。</p>'
    rows = []
    for t in turns:
        rows.append(
            "<tr><td>#%d</td><td>%s</td><td>%s</td><td>%d</td><td>%d</td>"
            "<td>%d</td><td>%d</td></tr>" % (
                t["turn"], esc(t["stage"]),
                "-" if t["duration_s"] is None else "%.1f s" % t["duration_s"],
                t["steps"], t["first_steps"], t["chars"], t["matched"]))
    return ("<table><thead><tr><th>轮次</th><th>阶段</th><th>turn 间隔</th>"
            "<th>step 数</th><th>首 step 注入</th><th>注入字符合计</th>"
            "<th>召回命中合计</th></tr></thead><tbody>%s</tbody></table>"
            % "".join(rows))


def _uca_table(stpa, uca_defs):
    rows = []
    for code in sorted(uca_defs):
        n = stpa["uca_counts"].get(code, 0)
        rows.append("<tr><td><code>%s</code></td><td>%s</td><td>%d</td></tr>"
                    % (esc(code), esc(uca_defs[code]), n))
    total = sum(stpa["uca_counts"].values())
    return ("<table><thead><tr><th>UCA</th><th>含义（来自 engine/control.py）</th>"
            "<th>真实命中</th></tr></thead><tbody>%s</tbody></table>"
            "<p class=\"note\">UCA-01..07 命中即熔断（fail-closed）；UCA-08 只降级留痕。"
            "当前真实日志命中合计 <b>%d</b>。</p>" % ("".join(rows), total))


CSS = """
:root{
  --bg:#070d19; --panel:#0b1220; --card:#0f1a2e; --line:#1b2942;
  --fg:#e2e8f0; --muted:#94a3b8; --dim:#64748b;
  --cyan:#38bdf8; --green:#34d399; --amber:#f59e0b; --purple:#a78bfa; --red:#f87171;
}
*{box-sizing:border-box}
html,body{margin:0}
body{background:
  radial-gradient(1100px 420px at 18% -8%, rgba(56,189,248,.13), transparent 60%),
  radial-gradient(900px 380px at 88% 0%, rgba(167,139,250,.10), transparent 60%),
  var(--bg);
  color:var(--fg);font:15px/1.65 -apple-system,"Segoe UI","Microsoft YaHei",system-ui,sans-serif}
.wrap{max-width:1120px;margin:0 auto;padding:0 22px}
header{padding:40px 0 8px}
.eyebrow{color:var(--cyan);font-size:12px;letter-spacing:.18em;text-transform:uppercase;font-weight:700}
h1{font-size:30px;line-height:1.25;margin:10px 0 12px;letter-spacing:-.01em}
.lead{color:#cbd5e1;font-size:16px;max-width:980px;margin:0 0 16px}
.lead b{color:#fff}
.badges{display:flex;flex-wrap:wrap;gap:8px;margin-bottom:8px}
.badges span{font-size:12px;color:#cbd5e1;background:#0f1a2e;border:1px solid var(--line);
  border-radius:999px;padding:3px 11px}
main{padding-bottom:30px}
section{margin:30px 0}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:26px 0}
.card{background:linear-gradient(180deg,#101c33,#0d1628);border:1px solid var(--line);
  border-radius:14px;padding:16px 18px}
.card .k{color:var(--muted);font-size:12px;letter-spacing:.04em}
.card .v{font-size:22px;font-weight:700;margin:4px 0 3px;letter-spacing:-.01em}
.card .s{color:var(--dim);font-size:12px}
.reading{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:12px;
  background:#0b1220;border:1px solid var(--line);border-radius:14px;padding:16px 18px}
.reading h3{margin:0 0 6px;font-size:14px}
.reading p{margin:2px 0;color:var(--muted);font-size:13px}
.dot-real{display:inline-block;width:10px;height:10px;border-radius:50%;background:var(--green);
  margin-right:7px;vertical-align:middle}
.dot-unmeasured{display:inline-block;width:10px;height:10px;border-radius:50%;background:#334155;
  margin-right:7px;vertical-align:middle}
.dot-demo{display:inline-block;width:16px;height:0;border-top:3px dashed var(--amber);
  margin-right:7px;vertical-align:middle}
h2{font-size:20px;margin:38px 0 6px;display:flex;align-items:center;gap:10px;flex-wrap:wrap}
h2 .num{color:var(--cyan);font-weight:800}
.tag{font-size:11px;font-weight:700;letter-spacing:.05em;border-radius:999px;padding:2px 10px;
  vertical-align:middle}
.tag-real{background:rgba(52,211,153,.14);color:var(--green);border:1px solid rgba(52,211,153,.4)}
.tag-demo{background:rgba(245,158,11,.14);color:var(--amber);border:1px solid rgba(245,158,11,.45)}
.caption{color:var(--muted);font-size:13px;margin:0 0 14px}
.caption b{color:#e2e8f0}
.fig{width:100%;height:auto;display:block;border-radius:14px}
.demo-note{margin:12px 0 0;padding:12px 16px;border-radius:12px;font-size:13px;
  background:rgba(245,158,11,.08);border:1px solid rgba(245,158,11,.35);color:#fde68a}
.demo-note b{color:#fff}
table{width:100%;border-collapse:collapse;font-size:13px;margin:14px 0 0;
  background:#0b1220;border:1px solid var(--line);border-radius:10px;overflow:hidden}
th,td{text-align:left;padding:7px 10px;border-bottom:1px solid var(--line);vertical-align:top}
th{color:var(--muted);font-weight:600;background:#0d1526}
tr:last-child td{border-bottom:none}
code{background:#0b1220;border:1px solid var(--line);padding:1px 6px;border-radius:5px;
  font-size:12px;color:#cbd5e1}
.note{color:var(--muted);font-size:13px;margin:8px 0 0}
.empty{color:var(--amber);font-size:13px;margin:8px 0 0}
pre{background:#0b1220;border:1px solid var(--line);border-radius:12px;padding:14px 16px;
  overflow:auto;font-size:13px;line-height:1.7;color:#cbd5e1}
details{background:#0b1220;border:1px solid var(--line);border-radius:12px;padding:12px 16px;
  margin-top:12px}
summary{cursor:pointer;color:var(--cyan);font-weight:600;font-size:14px}
footer{border-top:1px solid var(--line);margin-top:36px;padding:26px 0 40px;
  color:var(--dim);font-size:12px}
footer .wrap{display:flex;justify-content:space-between;gap:18px;flex-wrap:wrap}
.footer-strong{color:#cbd5e1}
"""


def render_page(logs, tl, stpa, demo, real_ratio_values, opts):
    state = logs["state"]
    turns = max(state["turns"], max([t["turn"] for t in tl["turns"]] or [0]))
    measured_n = len(tl["measured"])
    unmeasured = tl["unmeasured_ratio"]
    n_events = len(logs["events"])
    n_pre = len(tl["pre"])
    demo_note = (
        '<p class="demo-note"><b>演示：由实现计算，非真实流量。</b> 本图的阈值分区、'
        '首尾锚点注入段数、anchor_required 标志，全部由 <code>engine/control.py</code> 的 '
        '<code>PhaseShiftMonitor</code> 与 <code>apply_persona_anchor</code> 对 ratio '
        '0%..100% 实际扫描生成；它不是本机真实日志里的流量。</p>'
    )
    empty_banner = ""
    if not n_pre:
        empty_banner = (
            '<div class="demo-note" style="border-color:rgba(248,113,113,.4);'
            'background:rgba(248,113,113,.08);color:#fecaca">'
            '<b>暂无真实流量：</b>没有找到可解析的 pre_step 事件。图表 1 / 3 显示空态，'
            '图表 2 的阈值演示仍可查看。产生真实日志的方法见文末"复现"。</div>'
        )
    data_lines = [
        "<li><b>audit.jsonl</b>：%d 条事件（无法解析 %d 条已跳过，不保留原文）</li>"
        % (n_events, logs["unparsable"]),
        "<li><b>state.json</b>：turns=%d · frozen.ok=%s（checked=%d）· "
        "phase.ratio=%s · audit.verdict=%s</li>"
        % (state["turns"], _bool_text(state["frozen_ok"]), state["frozen_checked"],
           f2(state["phase"]["ratio"]), esc(state["audit"]["verdict"])),
        "<li><b>字段白名单</b>：kind / ts / turn / step / stage / matched / excluded / "
        "level / firstStep / chars / phase / ratio / anchor / verdict / ucas / "
        "downgrades / blocked</li>",
        "<li><b>不包含</b>：原始对话文本、记忆内容、目标内容、本机路径、用户名</li>",
        "<li><b>真实数据点</b>：pre_step 事件 %d 个；其中带 ratio/verdict 的测量 %d 个；"
        "带 anchor 字段 %d 个；真实 ratio 观测值 %s</li>"
        % (n_pre, measured_n,
           len([e for e in tl["pre"] if "anchor" in e]),
           ", ".join(f2(v) for v in sorted(set(real_ratio_values))) or "无"),
    ]
    if not opts["no_real"]:
        source_note = ("本页面数据来自本机 <code>$DSH_HOME/plugins/dsh-lingshu/</code> 的"
                       "真实运行日志（下方为脱敏后的字段级统计）。")
    else:
        source_note = "本页面以 <code>--no-real</code> 生成：图表 1 / 3 为强制空态。"

    uca_defs = {}
    try:
        from engine.control import UCA
        uca_defs = dict(UCA)
    except Exception:
        uca_defs = {}

    html_out = []
    html_out.append("<!doctype html>")
    html_out.append('<html lang="zh-CN"><head><meta charset="utf-8">')
    html_out.append('<meta name="viewport" content="width=device-width,initial-scale=1">')
    html_out.append("<title>LINGSHU Demo · 上下文控制论可视化（真实日志 + 阈值演示）</title>")
    html_out.append("<style>" + CSS + "</style></head><body>")
    html_out.append('<header><div class="wrap">')
    html_out.append('<div class="eyebrow">LINGSHU · Developer Demo</div>')
    html_out.append("<h1>上下文控制论：相变监控、首尾锚点与 STPA 前置闸，一张图看懂</h1>")
    html_out.append(
        '<p class="lead">LINGSHU 给 Agent 宿主加了一层<b>上下文控制论</b>：上下文用量到 '
        '<b>70%</b> 进入预警（shift）、到 <b>85%</b> 进入临界（critical），并在 prompt '
        '首尾注入人格锚点；拼装完成、发模型之前还有一道 <b>STPA 前置闸</b>（UCA 命中即 fail-closed 熔断）。'
        '本页把<b>真实插件日志</b>与<b>实现扫描出的阈值行为</b>画在一起，并明确区分两者。</p>')
    html_out.append('<div class="badges"><span>纯静态单文件</span><span>内联 CSS / SVG</span>'
                    '<span>无外部资源 · 无网络请求</span><span>已脱敏</span>'
                    '<span>固定输入 → 固定输出</span></div>')
    html_out.append("</div></header>")

    html_out.append('<main class="wrap">')
    html_out.append(empty_banner)
    if not n_pre:
        html_out.append(
            '<p class="note">建议先跑：<code>python scripts/quickstart.py</code>；'
            '真实日志来自实验 profile（见 <code>plugin/README.md</code>，'
            '只进可丢弃 profile，不碰 desktop）。</p>')

    # 30 秒总结
    html_out.append('<section class="cards">' + _cards(logs, tl, stpa) + "</section>")
    html_out.append(
        '<section class="reading">'
        '<div><h3>30 秒结论</h3>'
        '<p>实现侧：相变监控在 <b>70%%</b> 触发预警、<b>85%%</b> 进入临界，'
        '到点后在 prompt <b>首尾各注入一段人格锚点</b>（实现扫描曲线见 图 2）。</p>'
        '<p>真实侧：本机日志里 ratio 测量 <b>%d</b> 次、preflight 可判定 <b>%d</b> 次、'
        'UCA 命中 <b>%d</b> 次、block <b>%d</b> 次（真实日志共 %d 个 step）。'
        '样本很小，<b>不能据此宣称"长期安全"</b>。</p>'
        '</div>'
        '<div><h3>怎么读</h3>'
        '<p><span class="dot-real"></span><b>实心绿 / 青</b> = 真实日志数据点</p>'
        '<p><span class="dot-unmeasured"></span><b>灰色</b> = 该 step 没写这个字段（未记录，'
        '<b>不等于 0</b>）</p>'
        '<p><span class="dot-demo"></span><b>琥珀 / 紫虚线</b> = 演示曲线（由实现计算，'
        '非真实流量）</p>'
        '<p>图 1 / 3 是真实数据，图 2 是演示数据；两者在颜色与标题上分开。</p>'
        '</div></section>' % (measured_n, stpa["measured_count"], stpa["uca_total"],
                              stpa["block"], n_pre))

    # 图 1
    html_out.append(
        '<section><h2><span class="num">①</span> 真实会话 timeline %s</h2>' % TAG_REAL)
    html_out.append(
        '<p class="caption">每个 step 一行：注入字符数、记忆召回、上下文用量 ratio、'
        'preflight verdict、首尾锚点是否触发。<b>横轴 = pre_step 事件序号</b>（按日志时间'
        '排序）；<b>只画真实测量到的点</b>，旧版插件没有 ratio/verdict/anchor 字段的 step '
        '标记为"未记录"。</p>')
    html_out.append(svg_timeline(tl))
    html_out.append(
        '<p class="note">真实会话：%d 轮 / %d 个 step / 事件源 %d 条。'
        'ratio 测量 %d 个，anchor 字段 %d 个——其余为未记录。</p>'
        % (turns, n_pre, n_events, measured_n,
           len([e for e in tl["pre"] if "anchor" in e])))
    html_out.append(_measured_table(tl))
    html_out.append('<h3 style="font-size:14px;margin:20px 0 0">真实轮次（turn_begin / '
                    'turn_end_seen）</h3>')
    html_out.append(_turns_table(tl))

    # 图 2
    html_out.append(
        '<section><h2><span class="num">②</span> 相变阈值与首尾锚点注入曲线 %s</h2>' % TAG_DEMO)
    html_out.append(
        '<p class="caption">对 ratio 0%%→100%% 扫描，调用 <code>engine/control.py</code> '
        '实际实现：<b>phase 分区</b>（normal / shift / critical）、<b>首尾锚点注入段数</b>、'
        '<b>anchor_required</b> 标志。阈值来自 <code>rules/decay-params.yaml</code>: '
        'shift=%s、critical=%s（<code>provenance: guess</code>，尚未实测标定）。</p>'
        % (f2(demo["shift"]), f2(demo["hard"])))
    html_out.append(demo_note)
    html_out.append(svg_threshold(demo, real_ratio_values))
    html_out.append(
        '<p class="note">实现语义（来自代码，不是本页新定义）：phase 由 ratio 单值判定；'
        '<code>prefeed</code> 在 shift/critical 为真；<code>apply_persona_anchor</code> 在 '
        'prefeed 或 anchor_required 为真时，于 prompt <b>首部 + 尾部各加一段锚点</b>'
        '（共 %d 段），因此注入从 shift 阈值（%s）开始；'
        '<code>anchor_required</code> 还要求 critical，或在 shift 区间 trend &gt; %s。'
        '快速爬升曲线用单实例连续 observe（+%d%%/step）得到 trend，'
        '稳态扫描每点用全新实例（trend=0）。</p>'
        % (2, f2(demo["shift"]) if demo["first_anchor"] is not None else "未触发",
           f2(demo["trend_threshold"]), int(round(demo["fast_step"] * 100))))

    # 图 3
    html_out.append(
        '<section><h2><span class="num">③</span> STPA 前置闸拦截统计 %s</h2>' % TAG_REAL)
    html_out.append(
        '<p class="caption">真实审计事件计数：preflight verdict、UCA 命中、UCA-08 降级留痕，'
        '以及 hook_error / frozen_violation / 记忆写入被拒等 fail-closed 事件。'
        '<b>0 = 未发生</b>，不代表"已经证明安全"。</p>')
    html_out.append(svg_stpa(stpa))
    html_out.append(
        '<p class="note">可判定的 preflight 样本 <b>%d</b> 个（verdict=allow %d / block %d）；'
        '另有 %d 个 step 未落 verdict 字段。真实记忆写入 %d 条、被拒 %d 条。'
        '样本量不足以做安全性结论——这里只做"可观测性"。</p>'
        % (stpa["measured_count"], stpa["allow"], stpa["block"], unmeasured,
           stpa["writes"], stpa["rejected"]))
    if uca_defs:
        html_out.append(_uca_table(stpa, uca_defs))

    # 数据来源与复现
    html_out.append('<section><h2><span class="num">④</span> 数据来源、脱敏与复现</h2>')
    html_out.append('<p class="caption">%s</p>' % source_note)
    html_out.append("<ul class=\"note\">" + "".join(data_lines) + "</ul>")
    html_out.append(
        "<details><summary>重新生成（固定输入 → 固定输出）</summary><pre>"
        "python scripts/quickstart.py --no-open   # 冻结回放 + 生成 demo + 打印下一步\n"
        "python tools/gen_demo_report.py          # 只重生成 console/demo.html\n"
        "python tools/gen_demo_report.py --no-real  # 强制空态（验证没有真实日志也能跑）"
        "</pre></details>")
    html_out.append("</section>")
    html_out.append("</main>")

    html_out.append('<footer><div class="wrap">')
    html_out.append(
        '<div><div class="footer-strong">免责声明</div>'
        '图表 1 / 3 是真实日志的字段级统计；图表 2 是<b>演示</b>（由实现计算，'
        '非真实流量）。真实样本量很小，禁止把本页当作"长期安全"或"收益已验证"的证据。'
        '参数 provenance 仍为 guess。</div>')
    html_out.append(
        '<div style="text-align:right">LINGSHU · 灵为行为，枢为边界。<br>'
        '纯静态页面，无外部资源、无网络请求；生成器不含本机路径与用户名。</div>')
    html_out.append("</div></footer></body></html>")
    return "".join(html_out)


# ── 入口 ────────────────────────────────────────────────────

def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="生成 LINGSHU Demo 报告（自包含 HTML）")
    ap.add_argument("--home", default=DEFAULT_HOME,
                    help="DSH_HOME（默认取 env DSH_HOME，否则 ~/.dsh）")
    ap.add_argument("--out", default=os.path.join(ROOT, "console", "demo.html"),
                    help="输出 HTML 路径（默认 console/demo.html）")
    ap.add_argument("--no-real", action="store_true",
                    help="强制空态：不读取真实日志（验证无数据也能生成）")
    ap.add_argument("--open", action="store_true", help="生成后用默认浏览器打开")
    args = ap.parse_args(argv)

    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass

    logs = load_logs(args.home) if not args.no_real else {
        "events": [], "unparsable": 0, "state": sanitize_state({}),
        "audit_present": False, "state_present": False}
    tl = build_timeline(logs)
    stpa = build_stpa(logs, tl)
    try:
        demo = build_threshold_demo()
    except Exception as exc:  # engine 不可用 → 明确失败，不编造曲线
        sys.stderr.write("生成失败：无法调用 engine/control.py 生成阈值演示：%s: %s\n"
                         % (type(exc).__name__, exc))
        sys.stderr.write("请确认当前目录是仓库根目录（含 engine/control.py）。\n")
        return 1

    real_ratio_values = tl["ratio_values"]
    page = render_page(logs, tl, stpa, demo, real_ratio_values,
                       {"no_real": args.no_real})
    out_dir = os.path.dirname(os.path.abspath(args.out))
    os.makedirs(out_dir, exist_ok=True)
    with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(page)

    n_real = len(tl["pre"])
    print("已生成: %s（%.1f KB）" % (args.out, os.path.getsize(args.out) / 1024.0))
    print("  真实数据：%d 条审计事件 / %d 个 pre_step / 可判定 preflight %d 个"
          % (len(logs["events"]), n_real, stpa["measured_count"]))
    print("  演示数据：ratio 扫描 %d 点（engine/control.py 实际计算，非真实流量）"
          % len(demo["ratios"]))
    if not n_real:
        print("  提示：未找到真实日志 → 图表 1/3 为空态（可用 --home 指向有日志的 DSH_HOME）")

    if args.open:
        import webbrowser
        try:
            webbrowser.open("file:///" + os.path.abspath(args.out).replace("\\", "/"))
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
