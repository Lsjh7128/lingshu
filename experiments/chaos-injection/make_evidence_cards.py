# -*- coding: utf-8 -*-
"""生成《混沌工程故障注入报告》的"证据卡"图片。

说明（诚实标注）：这些**不是 GUI 截图**，而是把证据 JSON / 日志里的字段用等宽字体
**渲染成图片**，便于放进报告/答辩材料；每张卡底部标明源文件，可逐字复核。
"""
from __future__ import annotations

import json
import os
import textwrap

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
EXP = os.path.join(REPO, "experiments", "chaos-injection")
OUT = os.path.join(EXP, "out")
DEST = os.path.join(REPO, "acceptance", "chaos", "fault-injection", "evidence-cards")
os.makedirs(DEST, exist_ok=True)

MONO = r"C:\Windows\Fonts\consola.ttf"
MONO_B = r"C:\Windows\Fonts\consolab.ttf"


def font(size, bold=False):
    path = MONO_B if bold else MONO
    try:
        return ImageFont.truetype(path, size)
    except OSError:
        return ImageFont.load_default()


def card(name, title, subtitle, lines, source, width=1400):
    pad, lh = 26, 24
    wrapped = []
    for ln in lines:
        if not ln:
            wrapped.append("")
            continue
        wrapped.extend(textwrap.wrap(ln, width=104) or [""])
    height = 120 + len(wrapped) * lh + 46
    img = Image.new("RGB", (width, height), (255, 255, 255))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, width, 8], fill=(31, 78, 156))
    d.text((pad, 22), title, font=font(26, True), fill=(20, 20, 20))
    d.text((pad, 62), subtitle, font=font(17), fill=(70, 70, 70))
    d.line([pad, 96, width - pad, 96], fill=(200, 200, 200), width=1)
    y = 110
    for ln in wrapped:
        d.text((pad, y), ln, font=font(16), fill=(25, 25, 25))
        y += lh
    d.line([pad, height - 38, width - pad, height - 38], fill=(210, 210, 210), width=1)
    d.text((pad, height - 30), "source: " + source + "  (rendered excerpt, not a GUI screenshot)",
           font=font(13), fill=(110, 110, 110))
    path = os.path.join(DEST, name + ".png")
    img.save(path)
    print("wrote", path, img.size)
    return path


def main():
    a500 = json.load(open(os.path.join(OUT, "A-500", "case_500.json"), encoding="utf-8"))
    st = a500["plugin_state"]["state"] or {}
    reqs = a500["mock_requests"]
    gaps = [round(b["t"] - a["t"], 2) for a, b in zip(reqs, reqs[1:])]
    card(
        "A-http-500", "A / transport fault: HTTP 500",
        "host retried, surfaced one error line, exited non-zero, no fabricated text",
        [
            "run.exit_code            = %s" % a500["run"]["exit_code"],
            "run.wall_s               = %s s" % a500["run"]["wall_s"],
            "mock requests            = %d（paths=%s）" % (len(reqs), sorted({r["path"] for r in reqs})),
            "retry gaps (s)           = %s" % gaps,
            "stderr                   = %s" % (a500["run"]["stderr_head"] or "").strip(),
            "final text length        = %d" % len(a500["run"]["final"] or ""),
            "state.control.blocked    = %s" % bool((st.get("control") or {}).get("blocked")),
            "pre_step events          = %d" % (a500["derived"]["injection_events"].get("pre_step", 0)),
            "prompt head              = %s" % (a500["plugin_state"]["prompt_head"] or "")[:80],
        ],
        "experiments/chaos-injection/out/A-500/case_500.json",
    )

    b1 = json.load(open(os.path.join(OUT, "B-memory-corruption", "case_B1.json"), encoding="utf-8"))
    kinds = b1["plugin_state"].get("audit_kinds") or {}
    hooks = [e for e in (b1["plugin_state"].get("audit_tail") or [])
             if (e.get("type") or e.get("kind")) == "hook_error"]
    card(
        "B1-memory-write-crash", "B1 / memory write path crash (external injection)",
        "memory.jsonl replaced by a directory -> reads swallowed, writes raise EISDIR",
        [
            "run.exit_code            = %s" % b1["run"]["exit_code"],
            "run.wall_s               = %s s" % b1["run"]["wall_s"],
            "final text length        = %d（仍返回可用回答 → feature fail-open）"
            % b1["derived"]["final_text_len"],
            "audit hook_error         = %s" % kinds.get("hook_error", 0),
            "hook_error.err           = %s" % (hooks[-1].get("err") if hooks else "n/a"),
            "state.stateWrites        = %s（写失败，计数未增）" % b1["derived"]["stateWrites"],
            "state.factWrites         = %s（暖机模板值，未变）" % b1["derived"]["factWrites"],
            "state.control.blocked    = %s" % b1["derived"]["blocked"],
            "state.control.audit      = %s" % b1["derived"]["verdict"],
            "prompt has constitution  = %s" % b1["checks"]["constitution_injected"],
            "prompt has style section = %s"
            % any("表达状态" in ln for ln in (b1.get("prompt_view") or {}).get("lines") or []),
        ],
        "experiments/chaos-injection/out/B-memory-corruption/case_B1.json",
    )

    b2 = json.load(open(os.path.join(OUT, "B-memory-corruption", "case_B2.json"), encoding="utf-8"))
    d2 = b2["derived"]
    vars_ = [
        "state.control.variety.fallback   = %s" % d2["variety_fallback"],
        "state.control.variety.dropDomains= %s" % d2["dropDomains"],
        "state.control.variety.conflict   = %s" % d2["variety_conflict"],
        "state.control.variety.marker     = %s" % d2["variety_marker"],
        "state.control.audit.verdict      = %s" % d2["verdict"],
        "state.control.blocked            = %s" % d2["blocked"],
        "recall hits                      = %s" % (b2["plugin_state"]["state"].get("lastEvent") or {}).get("hits"),
        "conflict keys                    = %s" % d2.get("conflict_keys"),
        "prompt style absent             = %s" % b2["checks"]["prompt_style_absent"],
        "prompt has conflict hint        = %s" % b2["checks"]["prompt_has_conflict_hint"],
        "prompt has constitution         = %s" % b2["checks"]["prompt_has_constitution"],
        "audit memory_conflict/fallback  = %s / %s"
        % (b2["plugin_state"]["audit_kinds"].get("memory_conflict"),
           b2["plugin_state"]["audit_kinds"].get("fallback_applied")),
    ]
    prompt_lines = (b2["prompt_view"] or {}).get("lines") or []
    excerpt = [ln for ln in prompt_lines if "冲突" in ln or "召回" in ln][:4]
    card(
        "B2-conflict-fallback", "B2 / dirty memory store -> neutral-safe (fail-open)",
        "unresolved conflict -> style domain dropped; constitution kept; user asked to disambiguate",
        vars_ + ["", "prompt excerpt (逐字，含 CJK):"] + excerpt,
        "experiments/chaos-injection/out/B-memory-corruption/case_B2.json",
    )

    c = json.load(open(os.path.join(OUT, "C-context-flood", "summary.json"), encoding="utf-8"))
    rows = []
    for r in c["cases"]:
        rows.append("ratio=%-6.4f  phase=%-8s  prefeed=%-5s  anchorRequired=%-5s  head/tail=%s/%s  "
                    "constitution=%s  body_trunc=%s"
                    % (r["ratio_measured"], r["phase"], r["prefeed"], r["anchorRequired"],
                       r["last_prompt_anchor_head"], r["last_prompt_anchor_tail"],
                       r["last_prompt_constitution"], r["main_request_truncation_rate"]))
    bracket = c["threshold_bracket"]
    card(
        "C-context-flood", "C / 50k-token context flood (mock endpoint, zero model calls)",
        "deterministic 150,000-char garbage; 4 windows + exact boundary brackets",
        [
            "input = %d chars ~ %d tokens @ %s chars/token (lcg seed=%s)"
            % (c["input"]["chars"], c["input"]["nominal_tokens"], c["input"]["chars_per_token"],
               c["input"]["generator"]),
            "thresholds: shift=%.2f hard=%.2f trend=%.2f (plugin DEFAULT_PARAMS)"
            % (c["thresholds"]["phaseShiftRatio"], c["thresholds"]["phaseShiftHardRatio"],
               c["thresholds"]["trendThreshold"]),
            "",
            "measured phase mapping:",
        ] + ["  " + x for x in rows] + [
            "",
            "exact brackets: %.4f -> %s | %.4f -> %s | %.4f -> %s | %.4f -> %s"
            % (bracket["phaseShiftRatio"]["below_side"]["ratio_measured"],
               bracket["phaseShiftRatio"]["below_side"]["phase"],
               bracket["phaseShiftRatio"]["above_side"]["ratio_measured"],
               bracket["phaseShiftRatio"]["above_side"]["phase"],
               bracket["phaseShiftHardRatio"]["below_side"]["ratio_measured"],
               bracket["phaseShiftHardRatio"]["below_side"]["phase"],
               bracket["phaseShiftHardRatio"]["above_side"]["ratio_measured"],
               bracket["phaseShiftHardRatio"]["above_side"]["phase"]),
            "zero real model calls = %s; headless calls = %s"
            % (c["zero_real_model_calls"], c["call_budget"]["headless_calls"]),
        ],
        "experiments/chaos-injection/out/C-context-flood/summary.json",
    )


if __name__ == "__main__":
    main()
