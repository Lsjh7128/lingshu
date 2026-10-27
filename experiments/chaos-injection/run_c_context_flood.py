# -*- coding: utf-8 -*-
"""剧本 C：5 万 token 上下文过载（上下文洪水）——mock 端点，零真实模型调用

注入点（全部在宿主外部，插件/引擎/规则零改动）:
  1. `DEEPSEEK_BASE_URL` → 本机 mock 端点（Anthropic Messages 兼容，SSE）；
     mock 逐请求记录 body 字节数、body 里是否含完整输入、messages JSON 长度，
     用于判断"链路是否发生物理截断"（插件自身不截断，只做估算与注入）。
  2. `LINGSHU_CONTEXT_WINDOW` → 覆盖插件的相变窗口（env，见 index.js L787），
     让 70%/85% 阈值在 50k token 量级可达；不改任何代码。
  3. 输入 = 确定性"乱码"字符流（LCG 固定 seed，不依赖 random 模块），
     150,000 字符 ≈ 50,000 token（插件 charsPerToken=3）。

四个用例（任务给定窗口，按 3 chars/token 名义反推）:
  window=73530 → 名义 ratio 0.6799（期望 normal，不触发锚点）
  window=71429 → 名义 ratio 0.7000（期望 shift，prefeed=true）
  window=69444 → 名义 ratio 0.7200（期望 shift）
  window=58824 → 名义 ratio 0.8500（期望 critical，必须补锚点）

实测口径校正（关键）:
  插件估算 = ceil(len(JSON.stringify(payload.messages)) / 3)，payload.messages 里
  除了 150,000 字符输入外还有 127 字符的 JSON envelope（role/content 外壳），
  因此真实估算 token = ceil(150127/3) = 50,043，而不是名义的 50,000。
  → 每个用例的实测 ratio 比名义值高约 +0.0006（= 43 token / window）；
  → 真正让 r 恰为 0.7000 的窗口是 71490（50043/71490 = 0.7 整），不是 71429。
  这是"估算口径"偏差，不是阈值执行偏差：分类用的是未舍入的 r（>= 0.7 / >= 0.85）。

每个用例测量前跑 0–2 次"预热/边界探测"调用（同一 150k 输入，共 8 次 headless ≤ 8）:
  · 0.7000：预热窗口 71493 → r=0.69997，4 位小数显示 0.7000，但 phase=normal
            （舍入陷阱：显示值不能替代未舍入判定）
  · 0.8500：预热窗口 58876 → r=0.84997，4 位小数显示 0.8500，但 phase=shift
            （同上；随后再用同窗口 58824 预热一次，把落盘 phase 置为 critical，
             用来检验"尾锚点是否真的进入模型请求"）
  · 0.7200：同窗口重复观测（可复现性）
  · 0.6799：不预热（该点不触发任何注入；预热只会在同一目录留下 normal 状态，无增量信息）
  共 4 次测量 + 4 次预热/探测 = 8 次 headless（全部 mock，零成本），未超上限。

关键实测发现（锚点投递链路）:
  宿主在 `agent/pre-step` 之前组装 system prompt（dsh-agent-loop lib/index.js
  L907 `systemPrompt.assemble(...)` 先于 L911 `waterfall("agent/pre-step", ...)`），
  而插件的相变状态恰恰在 pre-step 里更新 → **本轮 pre-step 的相变更新赶不上本轮
  首个请求的 system prompt**。证据：请求 system 里的锚点集合 = 上一次落盘 phase
  的锚点集合（上轮 normal → 本轮 shift 请求无头锚点；上轮 shift → 本轮 critical
  请求有头无尾；上轮 critical → 本轮请求头尾齐全）。单轮 headless 会话里，
  critical 的尾锚点只在"上轮已是 critical"时才真正进入模型请求。

预热为什么不能改变 trend：PhaseShiftMonitor.history 只在**进程内**
（index.js L364 注释），headless 每次调用都是新进程；且每个 turn 只有首个
step 做相变观测（index.js L737）。故 trend 恒为 0 → shift 阶段
anchorRequired=false（只靠 prefeed 触发锚点头部），critical 才无条件尾部锚点。

诚实边界（写进证据 JSON 的 notes，也写进 C-SECTION-DRAFT.md）:
  · 插件只做**估算与注入**，不做物理截断；请求体截断/丢弃由宿主与模型端决定。
    本实验用 mock 端点，body 字节数只反映宿主组装后的请求，不代表真实模型接受度。
  · 乱码不是真实对话；每点 n=1（预热为同配置重复或邻域点，0.7000/0.8500 两处 n=2）。
  · 插件估算只覆盖 `payload.messages`，不含 system prompt 与工具 schema；
    宿主组装请求时还会向用户消息尾部追加约 1.8k 字符（本机用户级 AGENTS.md 指令块），
    该块不在插件估算里（本脚本用请求体反解出这两个差值）。
  · LINGSHU_CONTEXT_WINDOW 是实验室注入的**窄窗口**（模拟 50k 级上下文预算），
    模型真实窗口（deepseek-flash 声明 1,000,000 token）并未被压到极限；ratio 是
    "相对注入窗口的控制信号"，不等于物理占用率。
  · 锚点"首/尾"是灵枢 7 段在 system prompt 内的段序 90/140；system 段之后仍有
    其它段落，messages 里的 150k 用户消息在语义上更靠后。因此"尾部锚点"并不是
    整个模型上下文的末尾（插件没有改写宿主消息的权限）。

用法:
  python run_c_context_flood.py                      # 4 用例 × (预热+测量) = 8 次 headless
  python run_c_context_flood.py --cases 0.7000,0.8500
  python run_c_context_flood.py --warmup 0           # 跳过预热（只做 4 次测量）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

# ── 常量 ──────────────────────────────────────────────────────────
CHARS_PER_TOKEN = 3
INPUT_CHARS = 150_000
NOMINAL_TOKENS = INPUT_CHARS // CHARS_PER_TOKEN          # 50,000
GARBAGE_SEED = 20261027
# 字母表刻意排除 " \ 与控制字符：保证 prompt 在 JSON body 里逐字出现，
# 才能用"完整子串是否在 body 里"判断物理截断。
ALPHABET = (
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    "-_.:,;!?()/"
)
HEAD_MARK = "c_flood_head_9f2a7b_"
TAIL_MARK = "_c_flood_tail_9f2a7b"
CONSTITUTION_MARK = "〖宪法 · 只读〗"
PLUGIN_DIR = os.path.join(C.REPO, "plugin", "dsh-lingshu")
CONTROL_JS = os.path.join(PLUGIN_DIR, "control.js")
CORDIS_PATCH = os.path.join(PLUGIN_DIR, "cordis.patch.yml")
OUT_DIR = os.path.join(C.EXP_ROOT, "out", "C-context-flood")

# 用例定义：窗口按任务给定（3 chars/token 名义反推）。
# warmups：每个用例测量前跑 0–2 次调用（全部 mock）；它们同时充当：
#   · 边界探测（窗口略大，把 ratio 压到阈值下方但 4 位小数仍显示 0.7000/0.8500）
#   · "上一轮状态"构造（case 0.8500 的第 2 次预热把落盘 phase 置为 critical，
#     用来检验尾锚点是否真的进入模型请求——因为宿主在 pre-step 之前组装 system prompt）
#   · 同配置重复观测（case 0.7200）
CASES = [
    {
        "key": "0.6799",
        "window_tokens": 73530,
        "expected_phase": "normal",
        "note": "名义 ratio=50000/73530=0.67998；实测估算 50043 → r=0.6806，期望 normal，无锚点、无前馈",
        "warmups": [],
    },
    {
        "key": "0.7000",
        "window_tokens": 71429,
        "expected_phase": "shift",
        "note": "名义 ratio=50000/71429=0.699996；实测估算 50043 → r=0.7006，期望 shift",
        "warmups": [
            {"window_tokens": 71493, "kind": "below-shift-boundary",
             "expected_phase": "normal"},
        ],
    },
    {
        "key": "0.7200",
        "window_tokens": 69444,
        "expected_phase": "shift",
        "note": "名义 ratio=50000/69444=0.72000；实测估算 50043 → r=0.7206，期望 shift（平台期 trend=0）",
        "warmups": [
            {"window_tokens": 69444, "kind": "repeat", "expected_phase": "shift"},
        ],
    },
    {
        "key": "0.8500",
        "window_tokens": 58824,
        "expected_phase": "critical",
        "note": "名义 ratio=50000/58824=0.85000；实测估算 50043 → r=0.8507，期望 critical，锚点首尾都必须在",
        "warmups": [
            {"window_tokens": 58876, "kind": "below-critical-boundary",
             "expected_phase": "shift"},
            {"window_tokens": 58824, "kind": "critical-state-feed",
             "expected_phase": "critical"},
        ],
    },
]

# ── 确定性乱码（LCG，固定 seed；不调用 random 模块）────────────────
def build_garbage(n: int = INPUT_CHARS, seed: int = GARBAGE_SEED) -> str:
    """生成长度恰为 n 的确定性无意义字符流（首尾埋可检验标记，便于判截断）。"""
    body_n = n - len(HEAD_MARK) - len(TAIL_MARK)
    if body_n < 0:
        raise ValueError("n 太小")
    out = []
    x = seed & 0x7FFFFFFF
    a, m = 1103515245, 2147483648
    alphabet = ALPHABET
    alen = len(alphabet)
    for _ in range(body_n):
        x = (a * x + 12345) % m
        out.append(alphabet[(x >> 16) % alen])
    text = HEAD_MARK + "".join(out) + TAIL_MARK
    assert len(text) == n
    return text


# ── 源码 provenance：锚点文案逐字、阈值 ────────────────────────────
def _read_text(path: str) -> str:
    try:
        return open(path, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def read_provenance() -> dict:
    ctrl = _read_text(CONTROL_JS)
    patch = _read_text(CORDIS_PATCH)

    def const(name: str):
        m = re.search(r"export const %s = '([^']*)';" % re.escape(name), ctrl)
        return m.group(1) if m else None

    def dflt(name: str):
        m = re.search(r"%s:\s*([0-9.]+)," % re.escape(name), ctrl)
        return float(m.group(1)) if m else None

    def patch_val(name: str):
        m = re.search(r"^\s*%s:\s*([0-9.]+)\s*$" % re.escape(name), patch, re.M)
        return float(m.group(1)) if m else None

    return {
        "anchor": {
            "ANCHOR_PREFIX": const("ANCHOR_PREFIX"),
            "ANCHOR_SUFFIX": const("ANCHOR_SUFFIX"),
            "source": "plugin/dsh-lingshu/control.js",
        },
        "thresholds": {
            "phaseShiftRatio": dflt("phaseShiftRatio"),
            "phaseShiftHardRatio": dflt("phaseShiftHardRatio"),
            "trendThreshold": dflt("trendThreshold"),
            "source": "plugin/dsh-lingshu/control.js DEFAULT_PARAMS",
            "profile_patch": {
                "phaseShiftRatio": patch_val("phaseShiftRatio"),
                "phaseShiftHardRatio": patch_val("phaseShiftHardRatio"),
                "trendThreshold": patch_val("trendThreshold"),
                "source": "plugin/dsh-lingshu/cordis.patch.yml",
            },
        },
        "estimated_chars_per_token": CHARS_PER_TOKEN,
        "window_override_env": "LINGSHU_CONTEXT_WINDOW",
        "headless": {"node": C.NODE, "bin": C.BIN, "profile": C.LAB_PROFILE},
    }


# ── mock 端点（Anthropic Messages 兼容；SSE 风格照抄 run_a）──────────
STATE = {"prompt": "", "requests": [], "lock": threading.Lock()}


def _anthropic_message(text: str) -> dict:
    return {
        "id": "msg_mock", "type": "message", "role": "assistant", "model": "deepseek-chat",
        "content": [{"type": "text", "text": text}],
        "stop_reason": "end_turn", "stop_sequence": None,
        "usage": {"input_tokens": 12, "output_tokens": max(1, len(text) // 2)},
    }


def _sse(text: str) -> bytes:
    """最小 Anthropic SSE：message_start → content_block_delta → message_stop。"""
    events = [
        ("message_start", {"type": "message_start", "message": {
            "id": "msg_mock", "type": "message", "role": "assistant", "model": "deepseek-chat",
            "content": [], "stop_reason": None, "usage": {"input_tokens": 12, "output_tokens": 1}}}),
        ("content_block_start", {"type": "content_block_start", "index": 0,
                                 "content_block": {"type": "text", "text": ""}}),
        ("content_block_delta", {"type": "content_block_delta", "index": 0,
                                 "delta": {"type": "text_delta", "text": text}}),
        ("content_block_stop", {"type": "content_block_stop", "index": 0}),
        ("message_delta", {"type": "message_delta",
                           "delta": {"stop_reason": "end_turn", "stop_sequence": None},
                           "usage": {"output_tokens": max(1, len(text) // 2)}}),
        ("message_stop", {"type": "message_stop"}),
    ]
    out = []
    for ev, data in events:
        out.append("event: %s\ndata: %s\n\n" % (ev, json.dumps(data, ensure_ascii=False)))
    return "".join(out).encode("utf-8")


def _prefix_match_len(hay: str, needle: str, start: int) -> int:
    """needle 从 start 开始与 hay 的最长公共前缀长度（用于量化截断点）。"""
    if start < 0:
        return 0
    lo, hi = 0, len(needle)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if hay[start:start + mid] == needle[:mid]:
            lo = mid
        else:
            hi = mid - 1
    return lo


def _message_text(msg) -> str:
    if not isinstance(msg, dict):
        return ""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join((x.get("text") or "") for x in c if isinstance(x, dict))
    return ""


def _analyse_body(body: str, raw: bytes, path: str) -> dict:
    prompt = STATE["prompt"]
    rec = {
        "path": path,
        "body_bytes_utf8": len(raw),
        "body_chars": len(body),
        "body_sha256_16": hashlib.sha256(raw).hexdigest()[:16],
        "stream": '"stream":true' in body.replace(" ", ""),
        "body_head": body[:280],
        "prompt_full_in_body": False,
        "prompt_copies_in_body": 0,
        "prompt_offset_in_body": -1,
        "matched_prompt_chars": 0,
        "truncation_rate": None,
        "system_chars": None,
        "system_text": None,
        "messages_count": None,
        "messages_json_len": None,
        "messages_text_chars": None,
        "messages_text_head": None,
        "messages_text_tail": None,
        "host_appended_chars": None,
        "host_appended_head": None,
        "markers_in_body": {},
        "offsets_in_body": {},
        "markers_in_system": {},
        "offsets_in_system": {},
        "system_chars_after_anchor_tail": None,
        "system_chars_after_anchor_head": None,
    }
    # 1) 输入是否逐字出现在 body 里（字母表无 JSON 转义，可直接子串匹配）
    if prompt:
        off = body.find(prompt)
        rec["prompt_copies_in_body"] = body.count(prompt)
        if off >= 0:
            rec.update(prompt_full_in_body=True, prompt_offset_in_body=off,
                       matched_prompt_chars=len(prompt), truncation_rate=0.0)
        else:
            head = body.find(prompt[:120])
            matched = _prefix_match_len(body, prompt, head)
            rec.update(prompt_offset_in_body=head, matched_prompt_chars=matched,
                       truncation_rate=round(1.0 - matched / len(prompt), 6)
                       if head >= 0 else 1.0)
    # 2) 锚点 / 宪法标记在**实际请求体**里的存在与偏移
    marker_values = {
        "anchor_prefix": _PROV["anchor"].get("ANCHOR_PREFIX") or "",
        "anchor_suffix": _PROV["anchor"].get("ANCHOR_SUFFIX") or "",
        "constitution": CONSTITUTION_MARK,
    }
    for name, needle in marker_values.items():
        rec["markers_in_body"][name] = bool(needle) and needle in body
        rec["offsets_in_body"][name] = body.find(needle) if needle else -1
    # 3) 解析 body：messages JSON 长度 = 插件 estimateTokens 的同源口径
    try:
        payload = json.loads(body)
        sysv = payload.get("system")
        if isinstance(sysv, str):
            rec["system_chars"] = len(sysv)
            rec["system_text"] = sysv
            for name, needle in marker_values.items():
                off = sysv.find(needle) if needle else -1
                rec["markers_in_system"][name] = off >= 0
                rec["offsets_in_system"][name] = off
            tail_off = rec["offsets_in_system"].get("anchor_suffix", -1)
            head_off = rec["offsets_in_system"].get("anchor_prefix", -1)
            if tail_off >= 0:
                rec["system_chars_after_anchor_tail"] = len(sysv) - (
                    tail_off + len(marker_values["anchor_suffix"]))
            if head_off >= 0:
                rec["system_chars_after_anchor_head"] = len(sysv) - (
                    head_off + len(marker_values["anchor_prefix"]))
        elif sysv is not None:
            rec["system_chars"] = len(json.dumps(sysv, ensure_ascii=False))
        msgs = payload.get("messages")
        if isinstance(msgs, list):
            rec["messages_count"] = len(msgs)
            rec["messages_json_len"] = len(json.dumps(
                msgs, ensure_ascii=False, separators=(",", ":")))
            texts = [_message_text(m) for m in msgs]
            joined = "\n".join(texts)
            rec["messages_text_chars"] = len(joined)
            rec["messages_text_head"] = joined[:200]
            rec["messages_text_tail"] = joined[-240:]
            if prompt:
                rec["prompt_copies_in_messages"] = sum(t.count(prompt) for t in texts)
                idx = joined.find(prompt)
                if idx >= 0 and len(texts) == 1:
                    # 宿主在请求组装时向用户消息尾部追加的指令块（不在插件估算里）
                    rec["host_appended_chars"] = len(joined) - len(prompt)
                    rec["host_appended_head"] = joined[len(prompt):len(prompt) + 240]
            rec["request_messages_tokens_host"] = math.ceil(
                rec["messages_json_len"] / CHARS_PER_TOKEN)
            # 插件估算的 payload.messages 与"宿主请求里的 messages"只差这段追加块
            if rec.get("host_appended_chars") is not None:
                payload_len = rec["messages_json_len"] - rec["host_appended_chars"]
                rec["payload_messages_json_len"] = payload_len
                rec["plugin_used_tokens_from_body"] = math.ceil(payload_len / CHARS_PER_TOKEN)
    except (ValueError, TypeError) as exc:   # JSONDecodeError 是 ValueError 子类
        rec["parse_error"] = repr(exc)[:200]
    return rec


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默默认日志
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(n) if n else b""
        body = raw.decode("utf-8", "replace")
        rec = _analyse_body(body, raw, self.path)
        with STATE["lock"]:
            STATE["requests"].append(rec)
        text = "MOCK-C-OK"
        if rec["stream"]:
            payload = _sse(text)
            ctype = "text/event-stream"
        else:
            payload = json.dumps(_anthropic_message(text), ensure_ascii=False).encode("utf-8")
            ctype = "application/json"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def start_mock():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv


# ── 单次调用 + 证据采集 ────────────────────────────────────────────
def _last_prompt_text(data_dir: str) -> str:
    p = os.path.join(data_dir, "last-prompt.txt")
    if not os.path.isfile(p):
        return ""
    try:
        return open(p, encoding="utf-8", errors="replace").read()
    except OSError:
        return ""


def _analyse_last_prompt(text: str) -> dict:
    markers = {
        "anchor_prefix": _PROV["anchor"].get("ANCHOR_PREFIX") or "",
        "anchor_suffix": _PROV["anchor"].get("ANCHOR_SUFFIX") or "",
        "constitution": CONSTITUTION_MARK,
    }
    d = {
        "bytes": len(text.encode("utf-8")),
        "chars": len(text),
        "sha256_16": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
        "text": text,
    }
    for name, needle in markers.items():
        d["has_" + name] = bool(needle) and needle in text
        d["offset_" + name] = text.find(needle) if needle else -1
    return d


def _plugin_summary(snap: dict) -> dict:
    st = snap.get("state") or {}
    ctrl = st.get("control") or {}
    return {
        "phase": ctrl.get("phase"),
        "audit": ctrl.get("audit"),
        "blocked": ctrl.get("blocked"),
        "contextWindow_recorded_by_host": ctrl.get("contextWindow"),
        "variety": ctrl.get("variety"),
        "conflict": ctrl.get("conflict"),
        "turns": st.get("turns"),
        "coldStartSuppressed": st.get("coldStartSuppressed"),
        "files": snap.get("files"),
        "audit_kinds_cumulative": snap.get("audit_kinds"),
        "audit_events_cumulative": snap.get("audit_events"),
        "audit_tail": snap.get("audit_tail"),
        "memory_rows": snap.get("memory_rows"),
        "prompt_bytes": snap.get("prompt_bytes"),
        "prompt_sha256_16": snap.get("prompt_sha256_16"),
    }


def run_call(paths: dict, prompt: str, window_tokens: int, timeout: float) -> dict:
    """一次 headless 调用 + 独立 mock 端点。返回原始观测，不做判定。"""
    STATE["prompt"] = prompt
    with STATE["lock"]:
        STATE["requests"] = []
    srv = start_mock()
    port = srv.server_address[1]
    base = "http://127.0.0.1:%d" % port
    try:
        rec = C.run_headless(
            prompt, home=paths["home"], data_dir=paths["data"],
            profile=C.LAB_PROFILE, timeout=timeout,
            env_extra={"DEEPSEEK_BASE_URL": base,
                       "LINGSHU_CONTEXT_WINDOW": str(window_tokens)},
        )
    finally:
        srv.shutdown()
        srv.server_close()
    with STATE["lock"]:
        requests = list(STATE["requests"])
        STATE["requests"] = []
    snap = C.data_snapshot(paths["data"])
    lp_text = _last_prompt_text(paths["data"])
    return {
        "window_tokens_override": window_tokens,
        "env": {"DEEPSEEK_BASE_URL": base, "LINGSHU_CONTEXT_WINDOW": str(window_tokens)},
        "mock_port": port,
        "run": rec,
        "mock_requests": requests,
        "mock_request_count": len(requests),
        "plugin": _plugin_summary(snap),
        "last_prompt": _analyse_last_prompt(lp_text),
    }


def run_with_retry(paths: dict, prompt: str, window_tokens: int, timeout: float,
                   tag: str, log) -> dict:
    """失败（exit!=0 或无 final）重试 1 次；如实记录尝试历史。"""
    attempts = []
    call = None
    for i in range(2):
        call = run_call(paths, prompt, window_tokens, timeout)
        r = call["run"]
        ok = (r.get("exit_code") == 0 and r.get("final") is not None)
        attempts.append({
            "attempt": i + 1,
            "exit_code": r.get("exit_code"),
            "final": (r.get("final") or "")[:80],
            "wall_s": r.get("wall_s"),
            "mock_request_count": call["mock_request_count"],
            "ok": ok,
        })
        if ok:
            break
        log("  ! %s attempt %d failed: exit=%s final=%r timeout=%s stderr=%r"
            % (tag, i + 1, r.get("exit_code"), (r.get("final") or "")[:60],
               r.get("timed_out"), (r.get("stderr_head") or "")[-300:]))
    call["attempts"] = len(attempts)
    call["attempt_log"] = attempts
    return call


def _audit_delta(before: dict, after: dict) -> dict:
    """只统计"本次测量调用"新增的审计事件（预热会污染累计计数）。"""
    bk = before.get("audit_kinds") or {}
    ak = after.get("audit_kinds") or {}
    delta = {k: ak.get(k, 0) - bk.get(k, 0) for k in set(ak) | set(bk)}
    delta = {k: v for k, v in sorted(delta.items()) if v}
    return {
        "audit_events_delta": (after.get("audit_events") or 0) - (before.get("audit_events") or 0),
        "audit_kinds_delta": delta,
        "hook_error_count": delta.get("hook_error", 0),
        "pre_step_events": delta.get("pre_step", 0),
    }


# ── 派生判定（只记录事实 + 与预期的对照）──────────────────────────
def pick_main_request(requests: list):
    if not requests:
        return None
    return max(requests, key=lambda r: (r.get("matched_prompt_chars") or 0,
                                        r.get("body_bytes_utf8") or 0))


def _anchor_expectation(phase):
    """某 phase 下"应当出现"的锚点集合（与 control.js 的 prefeed/anchorRequired 语义同源）。"""
    phase = phase or "unknown"
    return {"head": phase in ("shift", "critical"), "tail": phase == "critical"}


def _phase_name(call: dict):
    return ((call.get("plugin") or {}).get("phase") or {}).get("phase")


def derive(call: dict, case: dict, prompt: str, audit_delta: dict | None = None,
           state_phase_before: str | None = None) -> dict:
    ph = (call["plugin"] or {}).get("phase") or {}
    ratio = ph.get("ratio")
    phase = ph.get("phase")
    window = call["window_tokens_override"]
    lp = call["last_prompt"]
    main = pick_main_request(call["mock_requests"])
    run = call["run"]
    expected_phase = case["expected_phase"]
    exp_head = expected_phase in ("shift", "critical")
    exp_tail = expected_phase == "critical"
    main = main or {}
    poll = call["mock_requests"] or []
    req_tokens_host = main.get("request_messages_tokens_host")
    plugin_tokens_body = main.get("plugin_used_tokens_from_body")
    nominal_ratio = (NOMINAL_TOKENS / window) if window else None
    # 请求 system 里实际出现的锚点（= 宿主组装 system prompt 时的状态 → 上一轮落盘 phase）
    sys_markers = main.get("markers_in_system") or {}
    delivered = {"head": bool(sys_markers.get("anchor_prefix")),
                 "tail": bool(sys_markers.get("anchor_suffix"))}
    from_current = _anchor_expectation(phase)
    from_prev = _anchor_expectation(state_phase_before)
    return {
        # 1) 相变观测（插件估算口径，4 位小数）
        "ratio_measured": ratio,
        "ratio_measured_display_4dp": ("%.4f" % ratio) if isinstance(ratio, (int, float)) else None,
        "ratio_nominal_from_50000_tokens": (round(nominal_ratio, 6)
                                            if nominal_ratio is not None else None),
        "ratio_delta_vs_nominal": (round(ratio - nominal_ratio, 6)
                                   if isinstance(ratio, (int, float)) and nominal_ratio else None),
        "phase": phase,
        "expected_phase": expected_phase,
        "phase_match": (phase == expected_phase),
        "prefeed": ph.get("prefeed"),
        "anchorRequired": ph.get("anchorRequired"),
        "trend": ph.get("trend"),
        "selfReferenceBoost": ph.get("selfReferenceBoost"),
        "used_tokens_estimate_from_ratio": (round(ratio * window, 2)
                                            if isinstance(ratio, (int, float)) else None),
        "used_tokens_estimate_uncertainty": (round(window * 0.00005, 2)
                                             if isinstance(ratio, (int, float)) else None),
        "plugin_used_tokens_from_body": plugin_tokens_body,
        "request_messages_tokens_host": req_tokens_host,
        "window_tokens_effective": window,
        # 0) 锚点投递链路：插件本轮更新（last-prompt） vs 宿主实际请求（system prompt）
        #    宿主在 agent/pre-step 之前组装 system prompt（agent-loop L907 vs L911），
        #    所以请求里的锚点只可能来自"上一次落盘 phase"，本轮 pre-step 的更新要等下一次组装。
        "state_phase_before_call": state_phase_before,
        "delivered_anchor_head_in_request": delivered["head"],
        "delivered_anchor_tail_in_request": delivered["tail"],
        "delivered_anchor_constitution_in_request": bool(sys_markers.get("constitution")),
        "anchor_expected_from_current_phase": from_current,
        "anchor_expected_from_previous_phase": from_prev,
        "request_matches_current_phase_anchor": (delivered == from_current),
        "request_matches_previous_phase_anchor": (delivered == from_prev),
        "anchor_lag_demonstrated": (delivered == from_prev and delivered != from_current),
        # 2) 落盘预览（last-prompt.txt：插件声称本轮会注入的段落）
        "last_prompt_bytes": lp["bytes"],
        "last_prompt_has_anchor_prefix": lp["has_anchor_prefix"],
        "last_prompt_has_anchor_suffix": lp["has_anchor_suffix"],
        "last_prompt_has_constitution": lp["has_constitution"],
        "last_prompt_expected_anchor_head": exp_head,
        "last_prompt_expected_anchor_tail": exp_tail,
        "last_prompt_offset_anchor_prefix": lp["offset_anchor_prefix"],
        "last_prompt_offset_anchor_suffix": lp["offset_anchor_suffix"],
        "last_prompt_offset_constitution": lp["offset_constitution"],
        # 3) 实际发往 mock 的请求体（比 last-prompt 更接近模型真实输入）
        "mock_request_count": call["mock_request_count"],
        "mock_request_paths": [r.get("path") for r in poll],
        "main_request_body_bytes": main.get("body_bytes_utf8"),
        "main_request_body_chars": main.get("body_chars"),
        "main_request_prompt_copies_in_body": main.get("prompt_copies_in_body"),
        "main_request_messages_json_len": main.get("messages_json_len"),
        "main_request_messages_text_chars": main.get("messages_text_chars"),
        "main_request_system_chars": main.get("system_chars"),
        "main_request_host_appended_chars": main.get("host_appended_chars"),
        "main_request_host_appended_head": main.get("host_appended_head"),
        "main_request_prompt_copies_in_messages": main.get("prompt_copies_in_messages"),
        "main_request_prompt_full_in_body": main.get("prompt_full_in_body"),
        "main_request_matched_prompt_chars": main.get("matched_prompt_chars"),
        "main_request_truncation_rate": main.get("truncation_rate"),
        "main_request_has_anchor_prefix": main.get("markers_in_body", {}).get("anchor_prefix"),
        "main_request_has_anchor_suffix": main.get("markers_in_body", {}).get("anchor_suffix"),
        "main_request_has_constitution": main.get("markers_in_body", {}).get("constitution"),
        "main_request_markers_in_system": main.get("markers_in_system"),
        "main_request_offsets_in_system": main.get("offsets_in_system"),
        "main_request_system_chars_after_anchor_tail": main.get("system_chars_after_anchor_tail"),
        "main_request_system_chars_after_anchor_head": main.get("system_chars_after_anchor_head"),
        "main_request_system_text": main.get("system_text"),
        "main_request_messages_text_tail": main.get("messages_text_tail"),
        "plugin_estimate_vs_host_messages": (
            round(plugin_tokens_body / req_tokens_host, 4)
            if plugin_tokens_body and req_tokens_host else None),
        "input_chars": len(prompt),
        "input_sha256_16": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
        # 4) 运行与审计（审计只算本次测量调用新增的事件）
        "exit_code": run.get("exit_code"),
        "timed_out": run.get("timed_out"),
        "wall_s": run.get("wall_s"),
        "final": (run.get("final") or "")[:120],
        "got_final": run.get("final") is not None,
        "error_lines": run.get("error_lines"),
        "audit": audit_delta or {},
        "hook_error_count": (audit_delta or {}).get("hook_error", 0),
        "pre_step_events": (audit_delta or {}).get("pre_step_events", 0),
        "audit_verdict": ((call["plugin"].get("audit") or {}).get("verdict")),
        "blocked": (call["plugin"].get("blocked")),
        "turns_cumulative_after_call": call["plugin"].get("turns"),
        "context_window_recorded_by_host": call["plugin"].get("contextWindow_recorded_by_host"),
        "attempts": call.get("attempts"),
    }


def _warm_derived(wd: dict, expected_phase: str) -> dict:
    return {
        "ratio_measured": wd["ratio_measured"],
        "ratio_measured_display_4dp": wd["ratio_measured_display_4dp"],
        "ratio_nominal_from_50000_tokens": wd["ratio_nominal_from_50000_tokens"],
        "ratio_delta_vs_nominal": wd["ratio_delta_vs_nominal"],
        "phase": wd["phase"],
        "phase_match_vs_probe_expectation": (wd["phase"] == expected_phase),
        "prefeed": wd["prefeed"],
        "anchorRequired": wd["anchorRequired"],
        "trend": wd["trend"],
        "state_phase_before_call": wd["state_phase_before_call"],
        "last_prompt_has_anchor_prefix": wd["last_prompt_has_anchor_prefix"],
        "last_prompt_has_anchor_suffix": wd["last_prompt_has_anchor_suffix"],
        "last_prompt_has_constitution": wd["last_prompt_has_constitution"],
        "request_has_anchor_prefix": wd["delivered_anchor_head_in_request"],
        "request_has_anchor_suffix": wd["delivered_anchor_tail_in_request"],
        "request_has_constitution": wd["delivered_anchor_constitution_in_request"],
        "request_matches_current_phase_anchor": wd["request_matches_current_phase_anchor"],
        "request_matches_previous_phase_anchor": wd["request_matches_previous_phase_anchor"],
        "anchor_lag_demonstrated": wd["anchor_lag_demonstrated"],
        "main_request_body_bytes": wd["main_request_body_bytes"],
        "main_request_messages_json_len": wd["main_request_messages_json_len"],
        "main_request_system_chars": wd["main_request_system_chars"],
        "main_request_truncation_rate": wd["main_request_truncation_rate"],
        "plugin_used_tokens_from_body": wd["plugin_used_tokens_from_body"],
        "exit_code": wd["exit_code"],
        "final": wd["final"],
        "wall_s": wd["wall_s"],
        "hook_error_count": wd["hook_error_count"],
    }


def _case_evidence(case: dict, prompt: str, warm_calls: list, measured: dict,
                   measured_delta: dict | None,
                   measured_state_phase_before: str | None = None) -> dict:
    d = derive(measured, case, prompt, measured_delta,
               state_phase_before=measured_state_phase_before)
    warm_blocks = []
    for w in warm_calls:
        wd = derive(w["call"], case, prompt, w.get("audit"),
                    state_phase_before=w.get("state_phase_before"))
        warm_blocks.append({
            "kind": w["kind"],
            "window_tokens": w["window_tokens"],
            "expected_phase": w["expected_phase"],
            "state_phase_before_call": w.get("state_phase_before"),
            "run": w["call"]["run"],
            "mock_requests": w["call"]["mock_requests"],
            "plugin": w["call"]["plugin"],
            "last_prompt": w["call"]["last_prompt"],
            "audit": w.get("audit") or {},
            "attempts": w["call"].get("attempts"),
            "attempt_log": w["call"].get("attempt_log"),
            "derived": _warm_derived(wd, w["expected_phase"]),
        })
    return {
        "scenario": "C-context-flood",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "case": {
            "key": case["key"],
            "window_tokens": case["window_tokens"],
            "input_chars": len(prompt),
            "input_tokens_nominal": NOMINAL_TOKENS,
            "input_head": prompt[:40],
            "input_tail": prompt[-40:],
            "input_sha256_16": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
            "expected_phase": case["expected_phase"],
            "note": case["note"],
            "garbage_generator": {"kind": "lcg", "seed": GARBAGE_SEED,
                                  "alphabet": ALPHABET,
                                  "head_marker": HEAD_MARK, "tail_marker": TAIL_MARK},
        },
        "provenance": _PROV,
        "warmups": warm_blocks,
        "measured": measured,
        "derived": d,
        "notes": [
            "注入点：DEEPSEEK_BASE_URL→本机 mock（零真实模型调用）+ LINGSHU_CONTEXT_WINDOW "
            "覆盖相变窗口（env，插件代码零改动）",
            "插件只做估算与注入，不做物理截断；截断/丢弃由宿主与模型端决定（本实验 mock 端不截断）",
            "trend 恒为 0：PhaseShiftMonitor.history 只在进程内，headless 每次调用是新进程，"
            "且每个 turn 只有首 step 做相变观测；故预热不能跨进程制造趋势",
            "shift 阶段 prefeed=true 触发锚点头部，但 anchorRequired 需要 trend>0.02；"
            "critical 无条件 anchorRequired=true（尾锚点只在 critical 出现）",
            "插件估算含 messages JSON envelope 127 字符 → 50,043 token，"
            "故实测 ratio 比名义 50,000/window 高约 +0.0006（估算口径偏差，非阈值偏差）",
            "锚点投递链路存在一步/一轮滞后：宿主在 agent/pre-step 之前组装 system prompt"
            "（dsh-agent-loop L907 assemble vs L911 waterfall），因此请求里的锚点来自上一次落盘 phase；"
            "0.7000 的测量调用（上轮 normal）请求里没有本轮的头锚点、0.8500 的预热调用（上轮 shift）"
            "请求里有头无尾——这是实测到的滞后，不是断言失败",
            "n=1/每点；0.7000/0.8500 的边界探测为邻域窗口，0.7200 的预热为同配置重复观测",
        ],
    }


# ── 主流程 ─────────────────────────────────────────────────────────
_PROV: dict = {}
LOG_LINES: list = []


def _reset_runtime(exp: str) -> None:
    """重跑时先安全拆掉上一轮的 DSH_HOME。

    关键：`prepare_home` 用 Windows junction 指向仓库插件；直接 shutil.rmtree 一个
    已存在的 junction 有删掉仓库源码的风险。这里先用 `cmd /c rmdir <junction>`
    （不带 /s，只摘除 reparse point、不触碰目标目录），再删 _runtime 目录。
    """
    base = os.path.join(C.RUNTIME, exp)
    if not os.path.isdir(base):
        return
    junction = os.path.join(base, "dshhome", "profiles", C.LAB_PROFILE,
                            "node_modules", "dsh-lingshu")
    if os.path.isdir(junction):
        subprocess.run(["cmd", "/c", "rmdir", junction],
                       capture_output=True, text=True)
    shutil.rmtree(base, ignore_errors=True)
    out_work = os.path.join(OUT_DIR, "_work", exp)
    shutil.rmtree(out_work, ignore_errors=True)


def _phase_from_snapshot(snap: dict):
    ctrl = (snap.get("state") or {}).get("control") or {}
    return (ctrl.get("phase") or {}).get("phase")


def log(msg: str) -> None:
    line = str(msg)
    LOG_LINES.append(line)
    print(line, flush=True)


def main() -> int:
    global _PROV
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="0.6799,0.7000,0.7200,0.8500",
                    help="逗号分隔的用例 key（窗口见 CASES）")
    ap.add_argument("--timeout", type=float, default=120.0, help="单次 headless 超时（秒）")
    ap.add_argument("--warmup", type=int, default=1, choices=[0, 1],
                    help="每个用例前是否跑 1 次预热/边界探测调用")
    args = ap.parse_args()

    keys = [k.strip() for k in args.cases.split(",") if k.strip()]
    by_key = {c["key"]: c for c in CASES}
    missing = [k for k in keys if k not in by_key]
    if missing:
        raise SystemExit("未知用例: %s（可选 %s）" % (missing, sorted(by_key)))
    selected = [by_key[k] for k in keys]
    planned_calls = len(selected) + sum(len(c["warmups"]) for c in selected) * (
        1 if args.warmup else 0)
    if planned_calls > 8:
        raise SystemExit("计划调用 %d 次 > 8 次上限；减少 --cases 或 --warmup 0" % planned_calls)

    _PROV = read_provenance()
    anchors = _PROV["anchor"]
    if not anchors.get("ANCHOR_PREFIX") or not anchors.get("ANCHOR_SUFFIX"):
        raise SystemExit("无法从 control.js 提取 ANCHOR 文案（拒绝用记忆硬编码继续）")
    log("provenance: phaseShiftRatio=%s phaseShiftHardRatio=%s trendThreshold=%s"
        % (_PROV["thresholds"]["phaseShiftRatio"],
           _PROV["thresholds"]["phaseShiftHardRatio"],
           _PROV["thresholds"]["trendThreshold"]))
    log("anchor prefix = %r" % anchors["ANCHOR_PREFIX"])
    log("anchor suffix = %r" % anchors["ANCHOR_SUFFIX"])

    prompt = build_garbage()
    log("prompt: %d chars（名义 %d token @%d chars/token） sha256_16=%s"
        % (len(prompt), NOMINAL_TOKENS, CHARS_PER_TOKEN,
           hashlib.sha256(prompt.encode()).hexdigest()[:16]))

    # prepare_home 会在 C.OUT 下建目录：重定向到证据目录内部，避免越界写
    C.OUT = os.path.join(OUT_DIR, "_work")
    os.makedirs(OUT_DIR, exist_ok=True)
    os.makedirs(C.OUT, exist_ok=True)

    results = []
    total_calls = 0
    for case in selected:
        log("\n== 剧本 C / case ratio=%s (window=%d, expected=%s) =="
            % (case["key"], case["window_tokens"], case["expected_phase"]))
        _reset_runtime("C-r%s" % case["key"])
        paths = C.prepare_home("C-r%s" % case["key"])
        prev_phase = _phase_from_snapshot(C.data_snapshot(paths["data"]))
        warm_records = []
        for w in (case["warmups"] if args.warmup else []):
            snap_before = C.data_snapshot(paths["data"])
            log("  warmup/probe: window=%d (%s) state_phase_before=%s"
                % (w["window_tokens"], w["kind"], prev_phase))
            call = run_with_retry(paths, prompt, w["window_tokens"], args.timeout,
                                  "warmup-%s" % case["key"], log)
            total_calls += call.get("attempts", 1)
            delta = _audit_delta(snap_before, C.data_snapshot(paths["data"]))
            wd = derive(call, case, prompt, delta, state_phase_before=prev_phase)
            log("  warmup -> ratio=%s phase=%s prefeed=%s anchorRequired=%s exit=%s wall=%.2fs"
                % (wd["ratio_measured_display_4dp"], wd["phase"], wd["prefeed"],
                   wd["anchorRequired"], wd["exit_code"], wd["wall_s"] or -1))
            log("    pipeline: last-prompt head=%s tail=%s | request head=%s tail=%s | lag_demonstrated=%s"
                % (wd["last_prompt_has_anchor_prefix"], wd["last_prompt_has_anchor_suffix"],
                   wd["delivered_anchor_head_in_request"], wd["delivered_anchor_tail_in_request"],
                   wd["anchor_lag_demonstrated"]))
            warm_records.append({
                "kind": w["kind"],
                "window_tokens": w["window_tokens"],
                "expected_phase": w["expected_phase"],
                "state_phase_before": prev_phase,
                "call": call,
                "audit": delta,
            })
            prev_phase = wd["phase"]
        snap_pre = C.data_snapshot(paths["data"])
        log("  measured: window=%d state_phase_before=%s"
            % (case["window_tokens"], prev_phase))
        measured = run_with_retry(paths, prompt, case["window_tokens"], args.timeout,
                                  "case-%s" % case["key"], log)
        total_calls += measured.get("attempts", 1)
        measured_delta = _audit_delta(snap_pre, C.data_snapshot(paths["data"]))
        d = derive(measured, case, prompt, measured_delta, state_phase_before=prev_phase)
        log("  measured -> ratio=%s (nominal %s) phase=%s prefeed=%s anchorRequired=%s trend=%s"
            % (d["ratio_measured_display_4dp"], d["ratio_nominal_from_50000_tokens"],
               d["phase"], d["prefeed"], d["anchorRequired"], d["trend"]))
        log("  anchors: last-prompt head=%s tail=%s const=%s | request(system) head=%s tail=%s const=%s | lag=%s"
            % (d["last_prompt_has_anchor_prefix"], d["last_prompt_has_anchor_suffix"],
               d["last_prompt_has_constitution"],
               d["delivered_anchor_head_in_request"], d["delivered_anchor_tail_in_request"],
               d["delivered_anchor_constitution_in_request"], d["anchor_lag_demonstrated"]))
        log("  request: body=%s bytes prompt_full=%s copies=%s trunc=%s host_appended=%s | exit=%s wall=%s hook_error=%s"
            % (d["main_request_body_bytes"], d["main_request_prompt_full_in_body"],
               d["main_request_prompt_copies_in_body"], d["main_request_truncation_rate"],
               d["main_request_host_appended_chars"], d["exit_code"], d["wall_s"],
               d["hook_error_count"]))
        ev = _case_evidence(case, prompt, warm_records, measured, measured_delta, prev_phase)
        ev["call_budget"] = {"this_case": len(warm_records) + measured.get("attempts", 1),
                             "total_so_far": total_calls, "planned_total": planned_calls,
                             "cap": 8}
        out = C.save_json(os.path.join(OUT_DIR, "case_%s.json" % case["key"]), ev)
        log("  evidence -> %s" % out)
        results.append({"case": case, "evidence_path": out, "derived": d,
                        "warmup_derived": [(w.get("derived") or {}) for w in ev.get("warmups") or []]})

    # ── summary.json ──
    phase_shift = _PROV["thresholds"]["phaseShiftRatio"]
    phase_hard = _PROV["thresholds"]["phaseShiftHardRatio"]
    cases_summary = []
    used_tokens_seen = []
    for r in results:
        d = r["derived"]
        used_tokens_seen.append((r["case"], d))
        cases_summary.append({
            "ratio_key": r["case"]["key"],
            "window_tokens": r["case"]["window_tokens"],
            "ratio_nominal_from_50000_tokens": d["ratio_nominal_from_50000_tokens"],
            "expected_phase": r["case"]["expected_phase"],
            "ratio_measured": d["ratio_measured"],
            "ratio_measured_display_4dp": d["ratio_measured_display_4dp"],
            "ratio_delta_vs_nominal": d["ratio_delta_vs_nominal"],
            "phase": d["phase"],
            "phase_match": d["phase_match"],
            "prefeed": d["prefeed"],
            "anchorRequired": d["anchorRequired"],
            "trend": d["trend"],
            "plugin_used_tokens_from_body": d["plugin_used_tokens_from_body"],
            "used_tokens_estimate_from_ratio": d["used_tokens_estimate_from_ratio"],
            "request_messages_tokens_host": d["request_messages_tokens_host"],
            "last_prompt_anchor_head": d["last_prompt_has_anchor_prefix"],
            "last_prompt_anchor_tail": d["last_prompt_has_anchor_suffix"],
            "last_prompt_constitution": d["last_prompt_has_constitution"],
            "request_anchor_head": d["main_request_has_anchor_prefix"],
            "request_anchor_tail": d["main_request_has_anchor_suffix"],
            "request_constitution": d["main_request_has_constitution"],
            "system_chars_after_anchor_tail": d["main_request_system_chars_after_anchor_tail"],
            "last_prompt_bytes": d["last_prompt_bytes"],
            "main_request_body_bytes": d["main_request_body_bytes"],
            "main_request_messages_json_len": d["main_request_messages_json_len"],
            "main_request_prompt_copies_in_body": d["main_request_prompt_copies_in_body"],
            "main_request_host_appended_chars": d["main_request_host_appended_chars"],
            "main_request_truncation_rate": d["main_request_truncation_rate"],
            "main_request_prompt_full_in_body": d["main_request_prompt_full_in_body"],
            "exit_code": d["exit_code"],
            "wall_s": d["wall_s"],
            "final": d["final"],
            "hook_error_count": d["hook_error_count"],
            "blocked": d["blocked"],
            "audit_verdict": d["audit_verdict"],
            "evidence": os.path.relpath(r["evidence_path"], C.REPO).replace("\\", "/"),
        })

    # 估算口径校正：payload.messages JSON 长度（= body messages JSON 长 - 宿主追加块）
    calib = None
    for case, d in used_tokens_seen:
        if d.get("plugin_used_tokens_from_body"):
            used = d["plugin_used_tokens_from_body"]
            calib = {
                "plugin_estimate_formula": "ceil(len(JSON.stringify(payload.messages)) / charsPerToken)",
                "chars_per_token": CHARS_PER_TOKEN,
                "input_chars": INPUT_CHARS,
                "nominal_tokens": NOMINAL_TOKENS,
                "payload_messages_json_chars_exact": (
                    d.get("main_request_messages_json_len")
                    - (d.get("main_request_host_appended_chars") or 0)),
                "measured_used_tokens": used,
                "json_envelope_chars": (
                    d.get("main_request_messages_json_len")
                    - (d.get("main_request_host_appended_chars") or 0) - INPUT_CHARS),
                "envelope_tokens_over_nominal": used - NOMINAL_TOKENS,
                "note": "插件估算含 messages JSON envelope（role/content 外壳）→ 实际比 chars/3 多约 43 token；"
                        "因此实测 ratio 比名义值高约 +0.0006。分类使用未舍入 r（>=），显示值只保留 4 位。",
                "exact_boundaries": {
                    "phaseShiftRatio": {
                        "ratio": phase_shift,
                        "largest_shift_window": int(math.floor(used / phase_shift)),
                        "first_normal_window": int(math.floor(used / phase_shift)) + 1,
                        "note": "used/window >= 0.70 即 shift；用实测 used=%d 反推" % used,
                    },
                    "phaseShiftHardRatio": {
                        "ratio": phase_hard,
                        "largest_critical_window": int(math.floor(used / phase_hard)),
                        "first_shift_window": int(math.floor(used / phase_hard)) + 1,
                        "note": "used/window >= 0.85 即 critical；用实测 used=%d 反推" % used,
                    },
                },
                "source_case": case["key"],
            }
            break

    threshold_bracket = {}
    warm_by_key = {r["case"]["key"]: r for r in results}

    def _side(wd: dict, window: int, kind: str) -> dict:
        return {"kind": kind,
                "window_tokens": window,
                "ratio_measured": wd.get("ratio_measured"),
                "ratio_display_4dp": wd.get("ratio_measured_display_4dp"),
                "phase": wd.get("phase"),
                "prefeed": wd.get("prefeed"),
                "anchorRequired": wd.get("anchorRequired"),
                "anchor_tail_in_last_prompt": wd.get("last_prompt_has_anchor_suffix")}

    for key, name, thr in (("0.7000", "phaseShiftRatio", phase_shift),
                           ("0.8500", "phaseShiftHardRatio", phase_hard)):
        r = warm_by_key.get(key)
        if not r:
            continue
        probes = r["case"].get("warmups") or []
        ders = r["warmup_derived"] or []
        below = None
        for p, wd in zip(probes, ders):
            if str(p.get("kind", "")).startswith("below-"):
                below = _side(wd, p["window_tokens"], p["kind"])
                break
        if below is None and probes and ders:
            below = _side(ders[0], probes[0]["window_tokens"], probes[0].get("kind", "warmup"))
        md = r["derived"]
        threshold_bracket[name] = {
            "configured": thr,
            "below_side": below,
            "above_side": _side(md, r["case"]["window_tokens"], "measured"),
            "display_note": "两侧的 4 位小数显示值可能相同（0.7000 / 0.8500），但 phase 由未舍入的 r 决定；"
                            "显示值不能替代判定。",
        }

    # 锚点投递链路（关键发现）：宿主在 pre-step 之前组装 system prompt，
    # 因此"本轮 last-prompt 里的锚点"要等下一次组装才进入请求；请求里的锚点集合
    # 恰好等于上一次落盘 phase 的锚点集合。
    anchor_pipeline = []
    lag_count = 0
    for r in results:
        key = r["case"]["key"]
        probes = r["case"].get("warmups") or []
        ders = r["warmup_derived"] or []
        for p, wd in zip(probes, ders):
            row = {
                "case": key, "role": p.get("kind", "warmup"),
                "window_tokens": p["window_tokens"],
                "phase_current": wd.get("phase"),
                "phase_previous": wd.get("state_phase_before_call"),
                "prefeed": wd.get("prefeed"),
                "anchorRequired": wd.get("anchorRequired"),
                "last_prompt_anchor_head": wd.get("last_prompt_has_anchor_prefix"),
                "last_prompt_anchor_tail": wd.get("last_prompt_has_anchor_suffix"),
                "request_system_anchor_head": wd.get("request_has_anchor_prefix"),
                "request_system_anchor_tail": wd.get("request_has_anchor_suffix"),
                "request_constitution": wd.get("request_has_constitution"),
                "request_matches_current_phase": wd.get("request_matches_current_phase_anchor"),
                "request_matches_previous_phase": wd.get("request_matches_previous_phase_anchor"),
                "one_assembly_lag_demonstrated": wd.get("anchor_lag_demonstrated"),
            }
            lag_count += 1 if row["one_assembly_lag_demonstrated"] else 0
            anchor_pipeline.append(row)
        d = r["derived"]
        row = {
            "case": key, "role": "measured",
            "window_tokens": r["case"]["window_tokens"],
            "phase_current": d.get("phase"),
            "phase_previous": d.get("state_phase_before_call"),
            "prefeed": d.get("prefeed"),
            "anchorRequired": d.get("anchorRequired"),
            "last_prompt_anchor_head": d.get("last_prompt_has_anchor_prefix"),
            "last_prompt_anchor_tail": d.get("last_prompt_has_anchor_suffix"),
            "request_system_anchor_head": d.get("delivered_anchor_head_in_request"),
            "request_system_anchor_tail": d.get("delivered_anchor_tail_in_request"),
            "request_constitution": d.get("delivered_anchor_constitution_in_request"),
            "request_matches_current_phase": d.get("request_matches_current_phase_anchor"),
            "request_matches_previous_phase": d.get("request_matches_previous_phase_anchor"),
            "one_assembly_lag_demonstrated": d.get("anchor_lag_demonstrated"),
        }
        lag_count += 1 if row["one_assembly_lag_demonstrated"] else 0
        anchor_pipeline.append(row)

    summary = {
        "scenario": "C-context-flood",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "host": "真实 DSH headless（node %s / profile %s）+ 本机 mock 模型端点"
                % (os.path.basename(C.NODE), C.LAB_PROFILE),
        "zero_real_model_calls": True,
        "input": {"chars": len(prompt), "nominal_tokens": NOMINAL_TOKENS,
                  "chars_per_token": CHARS_PER_TOKEN, "generator": "lcg seed=%d" % GARBAGE_SEED,
                  "sha256_16": hashlib.sha256(prompt.encode()).hexdigest()[:16]},
        "injected": {"env": ["DEEPSEEK_BASE_URL", "LINGSHU_CONTEXT_WINDOW"],
                     "plugin_code_changed": False},
        "thresholds": _PROV["thresholds"],
        "provenance": _PROV,
        "calibration": calib,
        "cases": cases_summary,
        "threshold_bracket": threshold_bracket,
        "anchor_pipeline": {
            "finding": "请求 system prompt 里的锚点集合 = 上一次落盘 phase 的锚点集合，而不是本轮 phase；"
                       "因为宿主在 agent/pre-step 之前组装 system prompt"
                       "（dsh-agent-loop lib/index.js L907 assemble → L911 waterfall agent/pre-step）。"
                       "单轮 headless 调用里，本轮相变更新因此永远赶不上本轮首个请求。",
            "rows": anchor_pipeline,
            "one_assembly_lag_demonstrated_count": lag_count,
            "rows_total": len(anchor_pipeline),
        },
        "call_budget": {"headless_calls": total_calls, "planned": planned_calls,
                        "cap": 8, "warmup_per_case": args.warmup},
        "honest_notes": [
            "插件只做上下文用量估算与注入；不执行物理截断。mock 端点收到完整输入时，只能说明宿主组装链路未截断，"
            "不能外推到真实模型端的接受/丢弃行为。",
            "插件估算口径 = ceil(JSON.stringify(payload.messages).length / 3)，不含 system prompt 与工具 schema；"
            "宿主还会在请求组装时向用户消息尾部追加约 1.8k 字符指令块，本证据用 main_request_host_appended_chars 给出该差值。",
            "请求体里可能出现多份输入文本（messages 一份 + dsh_session_log/session 事件元数据多份），"
            "因此 body 字节数不能直接当模型输入长度；模型可见长度以 messages_json_len/messages_text_chars 为准。",
            "LINGSHU_CONTEXT_WINDOW 是实验室注入的窄窗口（模拟 50k 级上下文预算），模型真实窗口"
            "（deepseek-flash: 1,000,000 token）并未被压到极限；ratio 是相对控制信号，不是物理占用率。",
            "乱码不是真实对话；每用例 n=1（预热为同配置重复或邻域点），不能据此声称统计显著性。",
            "trend 恒为 0 是机制事实（history 进程内 + 每 turn 只观测首 step），不是测量失败；"
            "shift 阶段 anchorRequired=false，只有 critical 无条件补尾锚点。",
            "锚点头部/尾部是 system prompt 内的段序 90/140，不是整个请求的首尾；"
            "messages 里的 150k 用户消息在语义上位于 system 块之后。",
            "锚点存在一步/一轮投递滞后（见 anchor_pipeline）：宿主先 assemble system prompt、后跑 "
            "agent/pre-step，故本轮相变更新只影响下一次组装；单轮 headless 里 critical 的尾锚点"
            "只有在上一轮已把 phase 落盘为 critical 时才进入模型请求——这是插件控制面与宿主生命周期"
            "的接口时延，不是阈值判定错误。",
            "0.6799 点未跑预热（预算让给 0.7000/0.8500 的边界探测与 critical 前态构造）；"
            "该点不触发任何注入，预热仅会重复同一 normal 观测。",
        ],
        "log": LOG_LINES,
    }
    out = C.save_json(os.path.join(OUT_DIR, "summary.json"), summary)
    log("\nsummary -> %s" % out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
