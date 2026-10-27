# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 跨环境控制台（API v1 / docs 24 / ADR-014）

    python tests/test_console_server.py        # 退出码 0/1，无 pytest 依赖

覆盖（全部直接调用被测实现，不复制业务逻辑）：
A. 注册表：变量展开、默认 profile、非法 id / 重复 id / 坏 source / 缺文件 → 明确报错
B. 数据源：jsonl 聚合（last-wins/状态计数）、目录缺失 → available:false、陈旧判定、sqlite 适配器
C. 脱敏：**哨兵字符串**（对话原文/记忆 content/sourceQuote/quote/preview/argsHead）
   不得出现在 profiles / snapshot / events 的任何响应里；禁止键递归扫描；未知 kind 只留 kind/ts
D. 热切换：同一服务进程内切换 profile，数据互不串；文件追加后 revision 变化（不重启）
E. HTTP：只读（POST 405）、Host 校验 421、未知 profile 404、静态白名单/路径穿越 404、
   SSE once、响应头（CSP/nosniff/no-store）、未接入 profile 的 planned display
F. 只读性：请求前后数据目录 mtime / 内容不变；不读 last-prompt.txt
"""
from __future__ import annotations

import hashlib
import http.client
import json
import os
import sqlite3
import sys
import tempfile
import threading
import time
import urllib.parse

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from console.server import ConsoleApp, ConsoleHTTPServer, Handler, RegistryError  # noqa: E402
from console.redact import contains_forbidden, redact_event                      # noqa: E402

PASS, FAIL = [], []
SECRET = "SECRET_原文_9f3c1d_绝不该出网"


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail else ""))


def now_ms() -> int:
    return int(time.time() * 1000)


def write_json(path, obj):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(obj, fh, ensure_ascii=False, indent=2)


def write_jsonl(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")


def touch_old(path, age_sec=3600):
    t = time.time() - age_sec
    os.utime(path, (t, t))


# ─────────────────────────────────────────────────────────────
# 夹具：一个"像插件写出来的"数据目录（含哨兵原文，用来抓脱敏漏洞）
# ─────────────────────────────────────────────────────────────
def make_fixture(root: str, name: str, memories: list, audits: list,
                 phase_ratio=0.7231, turns=8, state_writes=2, stale_sec=180) -> str:
    d = os.path.join(root, name)
    os.makedirs(d, exist_ok=True)
    write_json(os.path.join(d, "state.json"), {
        "sessions": 0, "turns": turns, "stateWrites": state_writes,
        "state": "ok",
        "paramVersion": SECRET,          # 即使是版本字段也不能原样出（未在白名单）
        "control": {
            "phase": {"ratio": phase_ratio, "phase": "shift", "trend": 0.02,
                      "prefeed": True, "anchorRequired": False},
            "contextWindow": 1000000,
            "audit": {"verdict": "allow", "ucas": [], "downgrades": [],
                      "failMode": "closed", "sources": {}},
            "blocked": False,
            "saturated": {"valence": False, "arousal": False, "dominance": False},
        },
    })
    write_jsonl(os.path.join(d, "memory.jsonl"), memories)
    write_jsonl(os.path.join(d, "audit.jsonl"), audits + [{"kind": "", "broken": True}])
    # 这一行故意是坏 JSON（模拟插件写到一半进程被杀）
    with open(os.path.join(d, "audit.jsonl"), "a", encoding="utf-8") as fh:
        fh.write("{not-json-" + SECRET + "}\n")
    with open(os.path.join(d, "last-prompt.txt"), "w", encoding="utf-8") as fh:
        fh.write("注入预览（含原文）：" + SECRET)
    return d


def standard_fixture(root: str):
    now = now_ms()
    memories = [
        # mem_001 两次落盘：后写覆盖（先 active 后 suppressed 墓碑）
        {"id": "mem_001", "status": "active", "origin": "goal", "type": "procedural",
         "layer": "warm", "content": SECRET, "sourceQuote": SECRET},
        {"id": "mem_001", "status": "suppressed", "origin": "goal", "type": "procedural",
         "layer": "warm", "content": SECRET, "sourceQuote": SECRET,
         "cleanup": {"reason": "test_data", "sourceQuote": SECRET}},
        {"id": "mem_002", "status": "active", "origin": "relation", "type": "episodic",
         "layer": "warm", "content": SECRET, "sourceQuote": SECRET},
        {"id": "mem_003", "status": "superseded", "origin": "user_fact", "type": "semantic",
         "layer": "warm", "content": SECRET, "sourceQuote": SECRET, "subject": SECRET},
        {"id": "mem_004", "status": "active", "origin": "user_fact", "type": "semantic",
         "layer": "warm", "content": SECRET, "sourceQuote": SECRET, "subject": SECRET},
    ]
    audits = [
        {"ts": now - 6000, "kind": "apply", "sections": 7, "toolOk": True,
         "shadow": {"goal": True, "relation": True}, "preview": SECRET, "configKeys": [SECRET]},
        {"ts": now - 5000, "kind": "pre_step", "stage": "warm", "matched": 2, "level": "mid",
         "excluded": 1, "turn": 3, "step": 1, "firstStep": True, "chars": 123,
         "phase": "shift", "ratio": 0.7231, "anchor": False, "conflict": "none",
         "fallback": False, "dropDomains": [], "verdict": "allow", "ucas": [],
         "downgrades": [], "blocked": False, "preview": SECRET, "argsHead": SECRET,
         "quote": SECRET, "subject": SECRET},
        {"ts": now - 4000, "kind": "stpa_observe", "verdict": "block", "ucas": ["UCA-01"],
         "downgrades": [], "sources": {"UCA-01": "user_message"}, "turn": 4,
         "userText": SECRET, "quote": SECRET},
        {"ts": now - 3000, "kind": "memory_write", "written": 1, "origins": ["user_fact"],
         "ids": ["mem_004"], "rejected": ["bad_type:" + SECRET], "turn": 4},
        {"ts": now - 2500, "kind": "memory_supersede", "count": 1, "subject": SECRET,
         "objects": [SECRET], "turn": 4},
        {"ts": now - 2000, "kind": "phase_alert", "phase": "critical", "ratio": 0.91,
         "thresholds": {"shift": 0.7, "hard": 0.85}, "turn": 5},
        {"ts": now - 1500, "kind": "quality_label", "kind_label": "wrong", "quote": SECRET,
         "phase": {"ratio": 0.91, "phase": "critical"}, "recall_ids": ["mem_004"],
         "source": "user_message_rule"},
        {"ts": now - 1200, "kind": "agent_state", "preview": SECRET,
         "goalEvents": ["new"], "relationEvent": "positive", "trust": "low",
         "conflict": False, "modifier": 1.0},
        {"ts": now - 1000, "kind": "hook_error", "event": SECRET, "err": SECRET},
        {"ts": now - 900, "kind": "unknown_event_kind", "content": SECRET, "n": 1},
    ]
    return make_fixture(os.path.join(root, "data"), "desktop", memories, audits)


def write_registry(path, repo, dsh_home, profiles, default="desktop"):
    write_json(path, {
        "version": 1, "default_profile": default, "refresh_hint_ms": 1500,
        "privacy": {"policy": "allowlist-v1", "raw_text": False},
        "profiles": profiles,
    })


def make_app(tmp: str, profiles, default="desktop"):
    # 阈值单一来源：测试里也放一份 rules/decay-params.yaml，确保读取的是"规则文件"而不是默认值。
    rules = os.path.join(tmp, "rules")
    os.makedirs(rules, exist_ok=True)
    rule_file = os.path.join(rules, "decay-params.yaml")
    if not os.path.isfile(rule_file):
        with open(rule_file, "w", encoding="utf-8") as fh:
            fh.write("control:\n  phaseShiftRatio: 0.7\n  phaseShiftHardRatio: 0.85\n")
    reg = os.path.join(tmp, "profiles.json")
    write_registry(reg, tmp, os.path.join(tmp, "dsh"), profiles, default)
    return ConsoleApp(reg, tmp, os.path.join(tmp, "dsh"), stream_interval=0.5, recent_limit=30)


# ─────────────────────────────────────────────────────────────
# A. 注册表
# ─────────────────────────────────────────────────────────────
def test_registry(tmp):
    print("\n[A] 注册表：展开 / 默认 / 非法输入")
    d = os.path.join(tmp, "data", "desktop")
    write_json(os.path.join(d, "state.json"), {"turns": 1})
    app = make_app(tmp, [{
        "id": "desktop", "label": "日常", "kind": "runtime", "mode": "observe",
        "source": {"type": "jsonl", "dirs": ["${REPO}/data/desktop"]},
    }])
    payload = app.profiles_payload()
    check("注册表加载 + 默认 profile", payload["ok"] and payload["default_profile"] == "desktop")
    check("路径变量 ${REPO} 展开",
          payload["profiles"][0]["source"]["resolved_dir"]
          == "<repo>" + os.sep + "data" + os.sep + "desktop",
          payload["profiles"][0]["source"]["resolved_dir"])
    check("阈值来自 rules/decay-params.yaml（单一来源）",
          payload["thresholds"] == {"shift": 0.7, "hard": 0.85}
          and payload["threshold_source"] == "rules/decay-params.yaml",
          str(payload["threshold_source"]))

    # 坏注册表必须显式失败，不能静默空列表
    bad_cases = [
        ("非法 id", {"profiles": [{"id": "Bad Id", "source": {"type": "jsonl", "dirs": ["x"]}}]}),
        ("重复 id", {"profiles": [{"id": "a", "source": {"type": "jsonl", "dirs": ["x"]}},
                                  {"id": "a", "source": {"type": "jsonl", "dirs": ["x"]}}]}),
        ("坏 source.type", {"profiles": [{"id": "a", "source": {"type": "ftp"}}]}),
        ("缺 dirs", {"profiles": [{"id": "a", "source": {"type": "jsonl"}}]}),
        ("未知变量", {"profiles": [{"id": "a", "source": {"type": "jsonl",
                                                          "dirs": ["${NOPE}/x"]}}]}),
    ]
    for label, doc in bad_cases:
        p = os.path.join(tmp, "bad-%s.json" % abs(hash(label)))
        write_json(p, doc)
        try:
            ConsoleApp(p, tmp, os.path.join(tmp, "dsh"))
            check("坏注册表报错：" + label, False)
        except RegistryError:
            check("坏注册表报错：" + label, True)
    try:
        ConsoleApp(os.path.join(tmp, "missing.json"), tmp, os.path.join(tmp, "dsh"))
        check("注册表文件缺失 → 启动失败（不是空列表）", False)
    except RegistryError:
        check("注册表文件缺失 → 启动失败（不是空列表）", True)


# ─────────────────────────────────────────────────────────────
# B/C/D. 聚合 + 脱敏 + 热切换
# ─────────────────────────────────────────────────────────────
def test_snapshot_and_redaction(tmp):
    print("\n[B] 聚合 / last-wins / 陈旧 / 未接入")
    standard_fixture(tmp)
    lab_dir = make_fixture(os.path.join(tmp, "data"), "lab", [
        {"id": "mem_100", "status": "active", "origin": "goal", "type": "procedural",
         "layer": "warm", "content": "另一 profile 的内容", "sourceQuote": "另一 profile"},
    ], [{"ts": now_ms() - 100, "kind": "apply", "sections": 7}], phase_ratio=0.1, turns=1)

    profiles = [
        {"id": "desktop", "label": "日常", "kind": "runtime", "mode": "observe",
         "source": {"type": "jsonl", "dirs": ["${REPO}/data/desktop"]}},
        {"id": "lingshu-lab", "label": "实验", "kind": "lab", "mode": "active",
         "stale_after_sec": 900,
         "source": {"type": "jsonl", "dirs": ["${REPO}/data/lab"]}},
        {"id": "ghost", "label": "未接入", "kind": "runtime", "mode": "observe",
         "source": {"type": "jsonl", "dirs": ["${REPO}/data/ghost"]}},
    ]
    app = make_app(tmp, profiles)
    snap = app.snapshot("desktop")

    m = snap["memory"]
    check("记忆聚合：last-wins（mem_001 被墓碑覆盖）", m["unique"] == 4, str(m["unique"]))
    check("记忆聚合：total_rows 是物理行数", m["total_rows"] == 5, str(m["total_rows"]))
    check("记忆聚合：active/suppressed/superseded 计数",
          (m["active"], m["suppressed"], m["superseded"]) == (2, 1, 1),
          str((m["active"], m["suppressed"], m["superseded"])))
    check("记忆聚合：坏行计入 unparsable 且不炸",
          snap["audit"]["unparsable"] == 1, str(snap["audit"]["unparsable"]))
    check("审计聚合：total 只统计可解析行", snap["audit"]["total"] == 11,
          str(snap["audit"]["total"]))
    check("审计聚合：phase_alerts / last_alert_ts 来自审计",
          snap["audit"]["phase_alerts"] == 1 and snap["audit"]["last_alert_ts"] is not None)
    check("相变：ratio/phase/thresholds 正确",
          snap["phase"]["ratio"] == 0.7231 and snap["phase"]["phase"] == "shift"
          and snap["phase"]["thresholds"] == {"shift": 0.7, "hard": 0.85})
    check("相变：used_tokens_est 由 ratio×window 派生",
          snap["phase"]["used_tokens_est"] == int(round(0.7231 * 1000000)))
    check("轮次：turns/state_writes/stage 来自 state",
          snap["turns"]["count"] == 8 and snap["turns"]["state_writes"] == 2
          and snap["turns"]["stage"] == "warm")
    check("STPA：优先取 state.control.audit（allow/fail-closed）",
          snap["stpa"]["verdict"] == "allow" and snap["stpa"]["fail_mode"] == "closed")
    check("内部字段 _last_stpa_raw 不出网", "_last_stpa_raw" not in snap["audit"])
    check("未接入 profile：available:false + planned display + hint",
          app.snapshot("ghost")["profile"]["available"] is False
          and app.snapshot("ghost")["profile"]["source"]["display"]
          == "<repo>" + os.sep + "data" + os.sep + "ghost"
          and bool(app.snapshot("ghost")["profile"]["hint"]))
    ghost = app.snapshot("ghost")
    check("未接入 profile：memory/phase 标 observed:false，不伪造 0",
          ghost["memory"]["observed"] is False and ghost["phase"]["observed"] is False
          and ghost["phase"]["ratio"] is None)

    # 陈旧：把文件 mtime 拨老
    touch_old(os.path.join(lab_dir, "state.json"), 4000)
    touch_old(os.path.join(lab_dir, "memory.jsonl"), 4000)
    touch_old(os.path.join(lab_dir, "audit.jsonl"), 4000)
    lab = app.snapshot("lingshu-lab")
    check("陈旧判定：data_age > stale_after → stale:true",
          lab["freshness"]["stale"] is True
          and lab["freshness"]["data_age_sec"] > lab["profile"]["stale_after_sec"])
    check("热切换：两个 profile 数据互不串",
          lab["memory"]["unique"] == 1 and snap["memory"]["unique"] == 4)

    # 热切换 + 文件追加：不重启，revision 必须变化
    rev1 = lab["revision"]
    with open(os.path.join(lab_dir, "audit.jsonl"), "a", encoding="utf-8") as fh:
        fh.write(json.dumps({"ts": now_ms(), "kind": "turn_end_seen", "turns": 1}) + "\n")
    time.sleep(0.9)  # 越过 0.8s 快照缓存
    lab2 = app.snapshot("lingshu-lab")
    check("热切换：文件追加后 revision 变化（同一进程，不重启）",
          lab2["revision"] != rev1 and lab2["audit"]["total"] == lab["audit"]["total"] + 1,
          "%s → %s" % (rev1, lab2["revision"]))

    print("\n[C] 脱敏：哨兵原文 / 禁止键")
    blobs = {
        "profiles": app.profiles_payload(),
        "snapshot": snap,
        "ghost": ghost,
        "events": app.events("desktop", 100),
    }
    for label, blob in blobs.items():
        text = json.dumps(blob, ensure_ascii=False)
        check("哨兵原文不出网：" + label, SECRET not in text)
        forbidden = contains_forbidden(blob)
        check("禁止键不出网：" + label, not forbidden, str(forbidden[:5]))
    kk = json.dumps(snap, ensure_ascii=False)
    for word in ("last-prompt", "注入预览"):
        check("敏感词不出网：" + word, word not in kk)
    unknown = [e for e in snap["audit"]["recent"] if e["kind"] == "unknown_event_kind"]
    check("未知 kind 只留 kind/ts（字段丢弃）",
          len(unknown) == 1 and unknown[0]["fields"] == {}
          and unknown[0].get("fields_omitted") is True)
    apply_ev = [e for e in snap["audit"]["recent"] if e["kind"] == "apply"]
    check("apply 事件丢弃 configKeys（只留白名单）",
          len(apply_ev) == 1 and "configKeys" not in apply_ev[0]["fields"])
    check("redact_event 直接调用也不含禁止键",
          not contains_forbidden(redact_event({"kind": "memory_write", "written": 1,
                                              "content": SECRET, "rejected": [SECRET]})))

    # 只读性：请求不修改数据目录
    data_dir = os.path.join(tmp, "data", "desktop")
    before = {f: (os.stat(os.path.join(data_dir, f)).st_mtime_ns,
                  os.path.getsize(os.path.join(data_dir, f)))
              for f in os.listdir(data_dir)}
    for _ in range(3):
        app.snapshot("desktop")
        app.events("desktop", 10)
    after = {f: (os.stat(os.path.join(data_dir, f)).st_mtime_ns,
                 os.path.getsize(os.path.join(data_dir, f)))
             for f in os.listdir(data_dir)}
    check("只读：连续读取不改数据目录 mtime/大小", before == after)

    # sqlite 适配器
    print("\n[B2] SQLite 适配器（实验性）")
    db = os.path.join(tmp, "sqlite", "lingshu.db")
    os.makedirs(os.path.dirname(db), exist_ok=True)
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE state(key TEXT, value TEXT)")
    conn.execute("INSERT INTO state VALUES('turns','12')")
    conn.execute("CREATE TABLE memory(id TEXT, status TEXT, origin TEXT, type TEXT, layer TEXT, lastAccess INTEGER)")
    conn.executemany("INSERT INTO memory VALUES(?,?,?,?,?,?)", [
        ("mem_001", "active", "goal", "procedural", "warm", now_ms()),
        ("mem_002", "active", "user_fact", "semantic", "warm", now_ms()),
        ("mem_002", "suppressed", "user_fact", "semantic", "warm", now_ms()),
    ])
    conn.execute("CREATE TABLE audit(ts INTEGER, kind TEXT, json TEXT)")
    conn.execute("INSERT INTO audit VALUES(?,?,?)", (
        now_ms(), "phase_alert", json.dumps({"phase": "critical", "ratio": 0.9,
                                             "thresholds": {"shift": 0.7, "hard": 0.85}})))
    conn.commit()
    conn.close()
    sqlite_profiles = [{
        "id": "sqlite-lab", "label": "SQLite 实验", "kind": "lab", "mode": "active",
        "source": {"type": "sqlite", "path": "${REPO}/sqlite/lingshu.db"},
    }]
    write_registry(os.path.join(tmp, "sqlite-app", "profiles.json"),
                   os.path.join(tmp, "sqlite-app"), os.path.join(tmp, "sqlite-app", "dsh"),
                   [{"id": "sqlite-lab", "label": "SQLite 实验", "kind": "lab", "mode": "active",
                     "source": {"type": "sqlite", "path": db}}], default="sqlite-lab")
    app2 = ConsoleApp(os.path.join(tmp, "sqlite-app", "profiles.json"),
                      os.path.join(tmp, "sqlite-app"), os.path.join(tmp, "sqlite-app", "dsh"))
    ss = app2.snapshot("sqlite-lab")
    check("sqlite：available + memory 聚合（last-wins）",
          ss["profile"]["available"] is True and ss["memory"]["unique"] == 2
          and ss["memory"]["suppressed"] == 1, str(ss["memory"]))
    check("sqlite：audit 解析 + phase_alert 计数",
          ss["audit"]["total"] == 1 and ss["audit"]["phase_alerts"] == 1)
    # 缺表 → available:false, reason:sqlite_schema_missing（不猜测）
    bad_db = os.path.join(tmp, "sqlite", "empty.db")
    conn = sqlite3.connect(bad_db)
    conn.execute("CREATE TABLE t(x)")
    conn.commit()
    conn.close()
    write_registry(os.path.join(tmp, "sqlite-app", "profiles.json"),
                   os.path.join(tmp, "sqlite-app"), os.path.join(tmp, "sqlite-app", "dsh"),
                   [{"id": "sqlite-lab", "label": "SQLite 实验", "kind": "lab", "mode": "active",
                     "source": {"type": "sqlite", "path": bad_db}}], default="sqlite-lab")
    app3 = ConsoleApp(os.path.join(tmp, "sqlite-app", "profiles.json"),
                      os.path.join(tmp, "sqlite-app"), os.path.join(tmp, "sqlite-app", "dsh"))
    bad = app3.snapshot("sqlite-lab")
    check("sqlite：表缺失 → available:false + reason:sqlite_schema_missing",
          bad["profile"]["available"] is False
          and bad["profile"]["reason"] == "sqlite_schema_missing")


# ─────────────────────────────────────────────────────────────
# E. HTTP
# ─────────────────────────────────────────────────────────────
def start_server(app):
    srv = ConsoleHTTPServer(("127.0.0.1", 0), Handler, app)
    srv.quiet = True
    t = threading.Thread(target=srv.serve_forever, kwargs={"poll_interval": 0.05},
                         daemon=True)
    t.start()
    return srv


def req(port, method, path, host=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    headers = {}
    if host:
        headers["Host"] = host
    conn.request(method, path, headers=headers)
    r = conn.getresponse()
    body = r.read()
    out = (r.status, dict(r.getheaders()), body)
    conn.close()
    return out


def test_http(tmp):
    print("\n[E] HTTP：只读 / Host / 静态白名单 / SSE / 头")
    standard_fixture(tmp)
    app = make_app(tmp, [
        {"id": "desktop", "label": "日常", "kind": "runtime", "mode": "observe",
         "source": {"type": "jsonl", "dirs": ["${REPO}/data/desktop"]}},
        {"id": "ghost", "label": "未接入", "kind": "runtime", "mode": "observe",
         "source": {"type": "jsonl", "dirs": ["${REPO}/data/ghost"]}},
    ])
    srv = start_server(app)
    port = srv.server_address[1]
    try:
        st, hd, body = req(port, "GET", "/api/health")
        check("GET /api/health 200 + JSON", st == 200 and json.loads(body)["ok"] is True)
        st, hd, body = req(port, "GET", "/api/profiles")
        check("GET /api/profiles 200", st == 200 and len(json.loads(body)["profiles"]) == 2)
        st, hd, body = req(port, "GET", "/api/snapshot?profile=desktop")
        check("GET /api/snapshot 200 + 脱敏", st == 200 and SECRET not in body.decode("utf-8"))
        check("响应头：CSP / nosniff / no-store",
              "Content-Security-Policy" in hd and hd.get("X-Content-Type-Options") == "nosniff"
              and hd.get("Cache-Control") == "no-store")
        st, hd, body = req(port, "GET", "/api/snapshot?profile=nope")
        check("未知 profile → 404 unknown_profile",
              st == 404 and json.loads(body)["code"] == "unknown_profile")
        st, hd, body = req(port, "GET", "/api/snapshot")
        check("缺 profile 参数 → 400", st == 400 and json.loads(body)["code"] == "bad_request")
        st, hd, body = req(port, "POST", "/api/snapshot?profile=desktop")
        check("POST → 405（只读，无写入口）",
              st == 405 and hd.get("Allow") == "GET, HEAD")
        st, hd, body = req(port, "GET", "/api/health", host="evil.example:80")
        check("Host 非回环 → 421（防 DNS rebinding）",
              st == 421 and json.loads(body)["code"] == "bad_host")
        st, hd, body = req(port, "GET", "/")
        check("静态 / → index.html", st == 200 and b"<html" in body.lower())
        st, hd, body = req(port, "GET", "/app.js")
        check("静态 /app.js 200 + JS MIME",
              st == 200 and "javascript" in hd.get("Content-Type", ""))
        st, hd, body = req(port, "GET", "/../server.py")
        check("路径穿越 → 404（静态文件白名单）", st == 404)
        st, hd, body = req(port, "GET", "/api/events?profile=desktop&limit=999")
        ev = json.loads(body)
        check("events：limit 上限 200 + 脱敏", st == 200 and len(ev["events"]) <= 200
              and SECRET not in body.decode("utf-8"))
        st, hd, body = req(port, "GET", "/api/stream?profile=desktop&once=1")
        text = body.decode("utf-8")
        check("SSE once：event: snapshot + data（热切换基于同协议）",
              st == 200 and "event: snapshot" in text and "data: {" in text
              and SECRET not in text)
        # 未接入 profile 的 snapshot
        st, hd, body = req(port, "GET", "/api/snapshot?profile=ghost")
        g = json.loads(body)
        check("未接入 profile 的 HTTP 响应含 hint + planned display",
              st == 200 and g["profile"]["available"] is False and g["profile"]["hint"])
    finally:
        srv.shutdown()
        srv.server_close()


# ─────────────────────────────────────────────────────────────
# F. 独立复核回归（P1 坏 ts 旁路 / HEAD / Host / events 顺序 / 错误体）
# ─────────────────────────────────────────────────────────────
def test_security_regressions(tmp):
    print("\n[F] 独立复核回归：坏 ts / HEAD / Host 精确匹配 / events 顺序 / 错误体")
    regress = os.path.join(tmp, "regress")
    ddir = os.path.join(regress, "data", "badts")
    os.makedirs(ddir, exist_ok=True)
    write_json(os.path.join(ddir, "state.json"), {"turns": 1, "control": {}})
    write_jsonl(os.path.join(ddir, "memory.jsonl"), [
        {"id": "mem_001", "status": "active", "origin": "goal", "type": "procedural",
         "layer": "warm", "content": SECRET, "sourceQuote": SECRET},
    ])
    # 顺序刻意按文件时间递增；第一行 ts 是坏字符串（独立复核 P1 的复现输入）。
    write_jsonl(os.path.join(ddir, "audit.jsonl"), [
        {"ts": "SENTINEL_BAD_TS_" + SECRET, "kind": "apply", "preview": SECRET},
        {"ts": 1000, "kind": "pre_step", "verdict": "allow", "preview": SECRET},
        {"ts": 2000, "kind": "pre_step", "verdict": "allow", "preview": SECRET},
        {"ts": 3000, "kind": "pre_step", "verdict": "allow", "preview": SECRET},
    ])
    app = make_app(regress, [{
        "id": "badts", "label": "坏 ts 夹具", "kind": "runtime", "mode": "observe",
        "source": {"type": "jsonl", "dirs": ["${REPO}/data/badts"]},
    }], default="badts")

    snap = app.snapshot("badts")
    text = json.dumps(snap, ensure_ascii=False)
    check("P1：坏 ts 不抛异常（整个 profile 面板不 500）",
          isinstance(snap.get("audit", {}).get("recent"), list))
    check("P1：坏 ts 被收敛为 0（不把原值带出）",
          any(e.get("ts") == 0 for e in snap["audit"]["recent"]))
    check("P1：坏 ts 原值不出网", SECRET not in text)
    r = redact_event({"ts": "x", "kind": "apply", "profileTag": "desktop", "preview": SECRET})
    check("P1：redact_event 对坏 ts 安全", r["ts"] == 0)
    check("P2：白名单字段 profileTag 能出网（ENUMS 默认值 bug 回归）",
          r["fields"].get("profileTag") == "desktop")

    ev = app.events("badts", 10)
    ts = [e["ts"] for e in ev["events"] if e["ts"] > 0]
    check("P2：/api/events 最新在前（与 API.md §2.4 一致）",
          bool(ts) and ts == sorted(ts, reverse=True) and ts[0] == 3000, str(ts))

    srv = start_server(app)
    port = srv.server_address[1]
    try:
        st, hd, body = req(port, "HEAD", "/api/profiles")
        check("P2：HEAD 响应无 body（HTTP/1.1 协议）", st == 200 and body == b"")
        check("P3：安全头含 X-Frame-Options / frame-ancestors / base-uri",
              hd.get("X-Frame-Options") == "DENY"
              and "frame-ancestors 'none'" in hd.get("Content-Security-Policy", "")
              and "base-uri 'self'" in hd.get("Content-Security-Policy", ""))
        check("P3：Server 头不暴露 Python 版本", "Python" not in hd.get("Server", ""))
        for bad in ("127.0.0.1:80@evil.com", "[::1]:%d@evil.com" % port,
                    "127.0.0.1:%d" % (port + 1), "localhost:1"):
            st, _, _ = req(port, "GET", "/api/health", host=bad)
            check("P2：Host 非法被拒（%s）" % bad, st == 421)
        st, _, _ = req(port, "GET", "/api/health", host="127.0.0.1:%d" % port)
        check("P2：Host 合法端口通过", st == 200)

        original = app.snapshot
        def boom(pid):
            raise ValueError(SECRET)
        app.snapshot = boom
        try:
            st, hd, body = req(port, "GET", "/api/snapshot?profile=badts")
        finally:
            app.snapshot = original
        check("P1：500 错误体不回显异常原文",
              st == 500 and SECRET not in body.decode("utf-8", "replace"))
        st, _, body = req(port, "GET", "/api/snapshot?profile=badts")
        check("P1：坏 ts 夹具下 HTTP 仍 200 且无原文",
              st == 200 and SECRET not in body.decode("utf-8", "replace"))
    finally:
        srv.shutdown()
        srv.server_close()


# ─────────────────────────────────────────────────────────────
def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    print("LINGSHU 控制台验收（API v1）· 哨兵 =", SECRET)
    # 临时目录：不用 tempfile.mkdtemp —— 它在 Windows 沙箱下会带 0o700 模式，
    # 导致随后创建子目录被 ACL 拒绝（实测）。用工作区内普通目录即可。
    base = os.path.join(ROOT, ".console-data", "tests")
    os.makedirs(base, exist_ok=True)
    import random
    tmp = os.path.join(base, "run-%d-%06d" % (int(time.time()), random.randrange(10 ** 6)))
    os.makedirs(tmp)
    try:
        test_registry(tmp)
        test_snapshot_and_redaction(tmp)
        test_http(tmp)
        test_security_regressions(tmp)
    finally:
        import shutil
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FAILED:")
        for f in FAIL:
            print("  - " + f)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
