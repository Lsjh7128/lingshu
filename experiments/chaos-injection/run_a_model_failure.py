# -*- coding: utf-8 -*-
"""剧本 A：模型 API 超时 / 500 故障注入（外部注入，不改主干代码）

注入点：`DEEPSEEK_BASE_URL` 指向本机 mock 端点（DSH 官方支持的 env，见 app.asar 内的
env 白名单），mock 按模式返回 500 / 空回复 / 伪造回复 / 永不响应（超时）。

观测目标（对应任务书 A 的"预期数据"）：
  1. 系统是否触发 fail-closed 熔断？
     —— 记录 `state.control.blocked` / `audit` 里的 preflight/STPA 事件；
  2. 是否拦截空回复或幻觉回复？
     —— 记录 stdout 是否出现 final text、内容是什么；
  3. 熔断耗时
     —— 记录 wall time、exit code、error 行、HTTP 请求次数（重试）。

诚实边界：LINGSHU 是"注入侧"插件，模型输出不经过它校验；因此"拦截模型幻觉输出"不是它的
职责范围。本实验只报告**观测到的事实**，不把"没拦"写成"拦了"。

用法：
  python run_a_model_failure.py                 # 全部模式
  python run_a_model_failure.py --modes ok,500   # 指定模式
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import common as C  # noqa: E402

STATE = {"mode": "ok", "requests": [], "hang_until": 0.0}
MODE_FINAL_TEXT = {
    "ok": "MOCK-OK",
    "empty": "",
    "fabricated": "你的项目代号是 LSJH（mock 伪造答案）",
    "500": None,
    "clear_error": None,
}


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


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # 静默默认日志
        pass

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(n).decode("utf-8", "replace") if n else ""
        rec = {"t": round(time.time(), 3), "path": self.path, "mode": STATE["mode"],
               "bytes": len(body), "stream": '"stream":true' in body.replace(" ", "")}
        STATE["requests"].append(rec)

        mode = STATE["mode"]
        if mode == "500":
            payload = json.dumps({"error": {"message": "mock upstream 500", "type": "server_error"}}).encode()
            self.send_response(500)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return
        if mode == "hang":
            # 接受连接但永不响应：让客户端自己的超时/外层 timeout 生效
            STATE["hang_until"] = time.time() + 600
            while time.time() < STATE["hang_until"]:
                time.sleep(0.5)
            return

        text = MODE_FINAL_TEXT.get(mode, "")
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


def start_mock(port: int = 0):
    srv = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    th = threading.Thread(target=srv.serve_forever, daemon=True)
    th.start()
    return srv


def run_case(mode: str, *, timeout: float, exp: str) -> dict:
    paths = C.prepare_home(exp)
    STATE["mode"] = mode
    STATE["requests"] = []
    srv = start_mock(0)
    port = srv.server_address[1]
    base = "http://127.0.0.1:%d" % port
    try:
        rec = C.run_headless(
            "请用一句话介绍你自己。", home=paths["home"], data_dir=paths["data"],
            profile=C.LAB_PROFILE, timeout=timeout,
            env_extra={"DEEPSEEK_BASE_URL": base},
        )
    finally:
        srv.shutdown()
        srv.server_close()
    snap = C.data_snapshot(paths["data"])
    requests = list(STATE["requests"])
    statuses = [r["mode"] for r in requests]
    evidence = {
        "scenario": "A-model-failure",
        "mode": mode,
        "injected": {"env": {"DEEPSEEK_BASE_URL": base}, "mock_port": port},
        "run": rec,
        "mock_requests": requests,
        "mock_request_count": len(requests),
        "plugin_state": snap,
        "derived": {
            "exit_code": rec["exit_code"],
            "wall_s": rec["wall_s"],
            "timed_out": rec["timed_out"],
            "final_text_len": len(rec["final"] or ""),
            "final_text_head": (rec["final"] or "")[:120],
            "got_final": rec["final"] is not None,
            "mock_hit": len(requests) > 0,
            "empty_reply_passed_through": (mode == "empty" and (rec["final"] or "") == ""),
            "fabricated_reply_passed_through": (mode == "fabricated"
                                                 and "LSJH" in (rec["final"] or "")),
            "plugin_blocked": bool(((snap["state"] or {}).get("control") or {}).get("blocked")),
            "hook_error_count": (snap["audit_kinds"] or {}).get("hook_error", 0),
            "injection_events": {k: v for k, v in (snap["audit_kinds"] or {}).items()
                                 if k in ("pre_step", "preflight", "memory_write", "emotion")},
            "model_call_events": {k: v for k, v in (snap["audit_kinds"] or {}).items()
                                  if "request" in k or "delivery" in k},
        },
    }
    out = os.path.join(paths["out"], "case_%s.json" % mode)
    C.save_json(out, evidence)
    return evidence


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--modes", default="ok,500,empty,fabricated,hang")
    ap.add_argument("--timeout", type=float, default=90.0)
    args = ap.parse_args()
    modes = [m.strip() for m in args.modes.split(",") if m.strip()]

    results = []
    for mode in modes:
        print("\n== 剧本 A / mode=%s ==" % mode, flush=True)
        ev = run_case(mode, timeout=args.timeout, exp="A-%s" % mode)
        d = ev["derived"]
        print("exit=%s wall=%.2fs timeout=%s final=%r requests=%d blocked=%s hook_errors=%d"
              % (d["exit_code"], d["wall_s"], d["timed_out"], d["final_text_head"],
                 ev["mock_request_count"], d["plugin_blocked"], d["hook_error_count"]), flush=True)
        results.append({"mode": mode, **d})

    summary = {
        "scenario": "A-model-failure",
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "timeout_s": args.timeout,
        "cases": results,
        "notes": [
            "注入点：DEEPSEEK_BASE_URL → 本机 mock（宿主侧外部注入，插件代码零改动）",
            "hang 模式由外层 subprocess timeout 或宿主客户端超时终止；两者都在 wall_s 里体现",
            "LINGSHU 只控制注入侧；模型输出不经过它校验（见报告 §A.4 诚实边界）",
        ],
    }
    out = os.path.join(C.OUT, "A-model-failure", "summary.json")
    C.save_json(out, summary)
    print("\nsummary ->", out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
