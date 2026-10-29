# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 零信任供应链加载器（S-CAP / ADR-018 / docs/26）

    <python> tests/test_supply_chain.py

覆盖：
- **策略单一来源**：contracts/supply-chain-policy.json == engine.POLICY == JS POLICY，
  docs/26 §7 内嵌 JSON 逐字段一致；错误码表三方一致；fallback 策略 deny-all。
- **静态扫描 / 注释剥离 / Manifest 校验**：规则命中、行号、注释不误报、字符串保守命中。
- **py↔js parity**：同一份 30+ case（含全部错误码路径）分别喂 Python 参考实现与
  `node supply-chain-sim.mjs --cases`，逐字段比较 verdict/reason/error_code/failures/
  findings/exports/capabilities/fs_scopes/sandbox/fingerprint。
- **物理沙箱实证**：`node supply-chain-sim.mjs --assert --json`（真实 spawn + Node
  permission model；越界写/越界读/拼接 API 绕过静态扫描/调用超时/导入期越界）。
- **index.js 接入实证**：`node supply-chain-integration.mjs --json`（真实 apply →
  Manifest 解析 → 审计事件 → deny 挂起 → lingshu_status 可观测）。
- **文档与实现同帧**：docs/26 存在且章节完整；ADR-018 存在。

原则：全部直接调用被测实现，不复制业务逻辑；实现退化时断言必须变红。
不使用 pytest（与仓库其余测试一致），退出码 0 = 全通过。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import supply_chain as sc                      # noqa: E402

PLUGIN = os.path.join(ROOT, "plugin", "dsh-lingshu")
SIM = os.path.join(PLUGIN, "supply-chain-sim.mjs")
INTEGRATION = os.path.join(PLUGIN, "supply-chain-integration.mjs")
HOST = os.path.join(PLUGIN, "sandbox-host.mjs")
CHILD = os.path.join(PLUGIN, "sandbox-child.mjs")
INDEX_JS = os.path.join(PLUGIN, "index.js")
CONTRACT = os.path.join(ROOT, "contracts", "supply-chain-policy.json")
DOC26 = os.path.join(ROOT, "docs", "26-零信任供应链与物理沙箱.md")
ADR18 = os.path.join(ROOT, "decisions", "018-零信任供应链与物理沙箱.md")
TMP_CASES = os.path.join(ROOT, "tests", "_tmp_supply_chain_cases.json")
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name
          + (("  — " + str(detail)) if (detail and not cond) else ""))


def node_bin() -> str:
    for cand in (os.environ.get("NODE"), shutil.which("node"),
                 r"C:\Program Files\nodejs\node.exe"):
        if cand and os.path.isfile(cand):
            return cand
    return "node"


def run_node(args: list[str], timeout: int = 180) -> tuple[int, str, str]:
    proc = subprocess.run([node_bin()] + args, cwd=PLUGIN, capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def run_json_node(args: list[str], timeout: int = 180) -> tuple[int, dict | None, str]:
    code, out, err = run_node(args, timeout)
    try:
        return code, json.loads(out), err
    except Exception:
        return code, None, err


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def manifest(entry_source: str, **overrides) -> dict:
    base = {
        "manifest_version": sc.MANIFEST_VERSION,
        "id": "demo-plugin",
        "version": "0.1.0",
        "entry": "index.mjs",
        "exports": ["ping"],
        "capabilities": {"required": ["llm"]},
        "integrity": {"entry_sha256": sha256(entry_source)},
    }
    base.update(overrides)
    return base


# ══════════════════════════════════════════════════════════════════════
# 0) 策略单一来源：contracts ═ engine ═ JS ═ docs/26 §7
# ══════════════════════════════════════════════════════════════════════
def test_policy_single_source() -> None:
    print("\n[0] 策略单一来源（contracts ═ engine ═ JS ═ docs/26 §7）")
    contract = json.load(open(CONTRACT, encoding="utf-8"))
    check("contracts policy_version == sc-1.0", contract["policy_version"] == "sc-1.0",
          contract["policy_version"])
    check("contracts 扫描规则 10 条", len(contract["scan"]["rules"]) == 10,
          len(contract["scan"]["rules"]))
    check("contracts 能力词表 10 种", len(contract["capabilities"]["known"]) == 10)
    check("engine.POLICY == contracts（逐字段）", sc.POLICY == contract)
    check("engine.POLICY_VERSION == contracts", sc.POLICY_VERSION == contract["policy_version"])
    check("engine 加载的是契约文件（非 fallback）", sc.POLICY_DEGRADED is False, sc.POLICY_SOURCE)

    code, dump, err = run_json_node(["-e", "import('./supply-chain.js').then(m=>process.stdout.write(JSON.stringify({policy:m.POLICY,error_codes:m.ERROR_CODES,fallback:m.FALLBACK_POLICY,version:m.POLICY_VERSION,source:m.POLICY_SOURCE,degraded:m.POLICY_DEGRADED})))"])
    check("JS 策略 dump 可解析", dump is not None, err[-200:])
    if dump:
        check("JS.POLICY == contracts（逐字段）", dump["policy"] == contract)
        check("JS.POLICY_VERSION == contracts", dump["version"] == contract["policy_version"])
        check("JS 未落 fallback", dump["degraded"] is False, dump.get("source"))
        check("JS.ERROR_CODES == engine.ERROR_CODES", dump["error_codes"] == sc.ERROR_CODES)
        check("JS fallback 为 deny-all（clean=deny）",
              dump["fallback"]["decision"]["verdicts"]["clean"] == "deny")
    check("engine fallback 为 deny-all（clean=deny）",
          sc.FALLBACK_POLICY["decision"]["verdicts"]["clean"] == "deny")
    check("engine fallback 无扫描规则（无策略即不信任）",
          len(sc.FALLBACK_POLICY["scan"]["rules"]) == 0)
    check("错误码 SC-E01..SC-E19 全覆盖",
          len(sc.ERROR_CODES) >= 19 and sc.ERROR_CODES["plugin_error"] == "SC-E19")

    # docs/26 §7 内嵌 JSON 必须与契约一致（防止文档与实现漂移）
    check("docs/26 存在", os.path.isfile(DOC26))
    if os.path.isfile(DOC26):
        md = open(DOC26, encoding="utf-8").read()
        sec = md.split("## 7.", 1)[1] if "## 7." in md else ""
        m = re.search(r"```json\s*\n(.*?)\n```", sec, re.S)
        check("docs/26 §7 含 json 代码块", bool(m))
        if m:
            try:
                doc_policy = json.loads(m.group(1))
            except Exception as err:  # noqa: BLE001
                doc_policy = None
                check("docs/26 §7 JSON 可解析", False, err)
            check("docs/26 §7 JSON == contracts（逐字段）", doc_policy == contract)


# ══════════════════════════════════════════════════════════════════════
# 1) 静态扫描 / 注释剥离 / Manifest 校验（engine 侧直接测）
# ══════════════════════════════════════════════════════════════════════
def test_scan_and_manifest() -> None:
    print("\n[1] 静态扫描 / 注释剥离 / Manifest 校验")
    hits = sc.scan_source('import fs from "node:fs";\nfs.writeFileSync("x","y");\n')
    check("fs.writeFileSync → SC-01/fs-write",
          any(f["id"] == "SC-01" and f["capability"] == "fs-write" for f in hits["findings"]))
    check("命中行号 == 2", hits["findings"][0]["line"] == 2, hits["findings"][0])
    check("child_process → SC-03/shell",
          any(f["capability"] == "shell" for f in sc.scan_source('import cp from "node:child_process";')["findings"]))
    check("fetch → SC-04/network",
          any(f["capability"] == "network" for f in sc.scan_source('fetch("https://x")')["findings"]))
    check("eval → SC-06/dynamic-code",
          any(f["capability"] == "dynamic-code" for f in sc.scan_source('eval("1+1")')["findings"]))
    check("process.env → SC-09/env",
          any(f["capability"] == "env" for f in sc.scan_source("const k = process.env.HOME;")["findings"]))
    check("Buffer.from(base64) → SC-10",
          any(f["id"] == "SC-10" for f in sc.scan_source('Buffer.from("abc", "base64")')["findings"]))
    check("行注释里的 API 不命中",
          len(sc.scan_source('// fs.writeFileSync("a","b")\nconst x=1;')["findings"]) == 0)
    check("块注释里的 API 不命中",
          len(sc.scan_source('/* child_process.execSync */\nconst x=1;')["findings"]) == 0)
    check("字符串里的 API 保守命中（不剥离字符串）",
          any(f["id"] == "SC-01" for f in sc.scan_source('const s = "fs.writeFileSync";')["findings"]))

    good = manifest("export function ping(){return 'pong'}")
    check("合法 manifest 无错误", sc.validate_manifest(good) == [])
    check("manifest_version 非法 → invalid",
          any(e["detail"] == "manifest_version" for e in sc.validate_manifest({**good, "manifest_version": "x"})))
    check("入口路径穿越 → invalid",
          any(e["detail"] == "entry_traversal" for e in sc.validate_manifest({**good, "entry": "../evil.mjs"})))
    check("缺 integrity → invalid",
          any(e["detail"] == "integrity_missing" for e in sc.validate_manifest({**good, "integrity": {}})))
    check("声明 fs-read 无作用域 → invalid",
          any(e["detail"] == "fs_scope_missing:fs-read"
              for e in sc.validate_manifest({**good, "capabilities": {"required": ["fs-read"]}})))
    check("未知能力 → invalid",
          any(str(e["detail"]).startswith("unknown_capability")
              for e in sc.validate_manifest({**good, "capabilities": {"required": ["telepathy"]}})))

    # 纯函数裁决的基本路径
    base = {
        "manifestPresent": True, "manifestRaw": json.dumps(good), "entryPresent": True,
        "entrySource": "export function ping(){return 'pong'}", "entrySha256": sha256("export function ping(){return 'pong'}"),
        "pluginDir": os.path.join(ROOT, "tests", "_tmp_sc_plugin"), "sandboxSupported": True,
    }
    d = sc.decide(base)
    check("合法插件 → allow_sandboxed", d["verdict"] == "allow_sandboxed", d["reason"])
    check("合法插件 granted=[llm]", d["capabilities"]["granted"] == ["llm"])
    check("沙箱计划含 --permission 与 --allow-fs-read",
          any("--permission" in f for f in d["sandbox"]["node_flags"])
          and any(f.startswith("--allow-fs-read=") for f in d["sandbox"]["node_flags"]))
    check("缺 manifest → deny/SC-E02",
          sc.decide({**base, "manifestPresent": False})["error_code"] == "SC-E02")
    check("沙箱不可用 → deny/sandbox_unavailable",
          sc.decide({**base, "sandboxSupported": False})["reason"] == "sandbox_unavailable")
    check("integrity 漂移 → deny/SC-E07",
          sc.decide({**base, "entrySha256": "b" * 64})["reason"] == "integrity_mismatch")
    check("未声明 fs-write → deny/SC-E11",
          sc.decide({**base, "entrySource": 'import fs from "node:fs";\nfs.writeFileSync("x","y");'})["error_code"] == "SC-E11")


# ══════════════════════════════════════════════════════════════════════
# 2) py↔js parity：同一份 case 文件，逐字段比较
# ══════════════════════════════════════════════════════════════════════
def build_cases() -> tuple[list[dict], dict[str, tuple[str, str]]]:
    plug = os.path.join(ROOT, "tests", "_tmp_sc_plugin")
    ok = "export function ping(){ return 'pong'; }"
    cases: list[dict] = []
    expect: dict[str, tuple[str, str]] = {}

    def add(cid: str, source: str | None, *, man: dict | None = None, verdict: str, reason: str,
            sandbox: bool = True, man_present: bool = True, entry_present: bool = True) -> None:
        inp = {"manifestPresent": man_present, "entryPresent": entry_present,
               "pluginDir": plug, "sandboxSupported": sandbox}
        if man_present:
            if man is None:
                man = manifest(source if source is not None else "")
            inp["manifestRaw"] = json.dumps(man)
        if entry_present:
            inp["entrySource"] = source if source is not None else ""
            inp["entrySha256"] = sha256(source if source is not None else "")
        if man and "integrity" in man and "entry_sha256" in man["integrity"]:
            inp["entrySha256"] = man["integrity"]["entry_sha256"]
        cases.append({"id": cid, "input": inp})
        expect[cid] = (verdict, reason)

    add("allow_minimal", ok, verdict="allow_sandboxed", reason="ok")
    add("allow_fs_read", "import fs from 'node:fs';\nexport function ping(){ return fs.readFileSync('x','utf8'); }",
        man=manifest("import fs from 'node:fs';\nexport function ping(){ return fs.readFileSync('x','utf8'); }",
                     capabilities={"required": ["fs-read"]}, fs={"read": ["data"]}),
        verdict="allow_sandboxed", reason="ok")
    add("allow_fs_write", "import fs from 'node:fs';\nexport function ping(){ fs.writeFileSync('data/x','y'); }",
        man=manifest("import fs from 'node:fs';\nexport function ping(){ fs.writeFileSync('data/x','y'); }",
                     capabilities={"required": ["fs-write"]}, fs={"write": ["data"]}),
        verdict="allow_sandboxed", reason="ok")
    add("allow_comment_only", "// fs.writeFileSync 只是文档里的反例\n/* child_process */\nexport function ping(){ return 1; }",
        verdict="allow_sandboxed", reason="ok")
    add("deny_undeclared_fs_write",
        "import fs from 'node:fs';\nfs.writeFileSync('x','y');\nexport function ping(){ return 1; }",
        verdict="deny", reason="undeclared_usage")
    add("deny_undeclared_shell",
        "import cp from 'node:child_process';\nexport function ping(){ return cp.execSync('whoami').toString(); }",
        verdict="deny", reason="undeclared_usage")
    add("deny_network_declared", ok, man=manifest(ok, capabilities={"required": ["network"]}),
        verdict="deny", reason="forbidden_capability")
    add("deny_shell_declared", ok, man=manifest(ok, capabilities={"required": ["shell"]}),
        verdict="deny", reason="forbidden_capability")
    add("deny_dynamic_code_usage", "eval('1+1');\nexport function ping(){ return 1; }",
        verdict="deny", reason="undeclared_usage")
    add("deny_env_usage", "const k = process.env.HOME;\nexport function ping(){ return k; }",
        verdict="deny", reason="undeclared_usage")
    add("deny_credentials_usage", "const p = 'id_rsa';\nexport function ping(){ return p; }",
        verdict="deny", reason="undeclared_usage")
    add("deny_obfuscation", "const d = Buffer.from('YWJj', 'base64');\nexport function ping(){ return d.length; }",
        verdict="deny", reason="undeclared_usage")
    add("deny_string_api_conservative", "const s = 'fs.writeFileSync';\nexport function ping(){ return s; }",
        verdict="deny", reason="undeclared_usage")
    add("deny_fs_scope_traversal", ok,
        man=manifest(ok, capabilities={"required": ["fs-read"]}, fs={"read": ["../outside"]}),
        verdict="deny", reason="manifest_invalid")
    add("deny_fs_scope_escape_backslash", ok,
        man=manifest(ok, capabilities={"required": ["fs-read"]}, fs={"read": ["..\\outside"]}),
        verdict="deny", reason="fs_scope_escape")
    add("deny_missing_manifest", None, man_present=False, entry_present=False,
        verdict="deny", reason="manifest_missing")
    add("deny_manifest_json_error", ok, man_present=True,
        verdict="deny", reason="manifest_unreadable")
    cases[-1]["input"]["manifestRaw"] = "{not json"
    expect["deny_manifest_json_error"] = ("deny", "manifest_unreadable")
    add("deny_manifest_version", ok, man={**manifest(ok), "manifest_version": "v0"},
        verdict="deny", reason="manifest_invalid")
    add("deny_entry_missing", None, man=manifest("x"), entry_present=False,
        verdict="deny", reason="entry_missing")
    add("deny_entry_too_large", "x", man=manifest("x"), entry_present=True,
        verdict="deny", reason="entry_too_large")
    cases[-1]["input"]["entrySource"] = "x" * 270000
    cases[-1]["input"]["entrySha256"] = sha256("x" * 270000)
    cases[-1]["input"]["manifestRaw"] = json.dumps(manifest("x" * 270000))
    add("deny_integrity_mismatch", ok, man=manifest(ok), verdict="deny", reason="integrity_mismatch")
    cases[-1]["input"]["entrySha256"] = "b" * 64
    add("deny_integrity_missing", ok, man={**manifest(ok), "integrity": {}},
        verdict="deny", reason="manifest_invalid")
    add("deny_unknown_capability", ok, man=manifest(ok, capabilities={"required": ["telepathy"]}),
        verdict="deny", reason="manifest_invalid")
    add("deny_sandbox_unavailable", ok, verdict="deny", reason="sandbox_unavailable", sandbox=False)
    add("deny_id_invalid", ok, man={**manifest(ok), "id": "BAD ID"},
        verdict="deny", reason="manifest_invalid")
    add("deny_entry_traversal", ok, man={**manifest(ok), "entry": "../evil.mjs"},
        verdict="deny", reason="manifest_invalid")
    add("deny_export_duplicate", ok, man={**manifest(ok), "exports": ["ping", "ping"]},
        verdict="deny", reason="manifest_invalid")
    add("allow_multi_exports", ok, man={**manifest(ok, exports=["ping", "pong"])},
        verdict="allow_sandboxed", reason="ok")
    add("deny_manifest_not_object", "{}", man_present=True,
        verdict="deny", reason="manifest_unreadable")
    cases[-1]["input"]["manifestRaw"] = "[1,2,3]"
    add("deny_export_name_invalid", ok, man={**manifest(ok, exports=["bad name"])},
        verdict="deny", reason="manifest_invalid")
    add("deny_sandbox_mode_invalid", ok, man={**manifest(ok, sandbox="wasm")},
        verdict="deny", reason="manifest_invalid")
    add("allow_optional_capability", "import fs from 'node:fs';\nexport function ping(){ return fs.readFileSync('d/x','utf8'); }",
        man=manifest("import fs from 'node:fs';\nexport function ping(){ return fs.readFileSync('d/x','utf8'); }",
                     capabilities={"required": ["llm"], "optional": ["fs-read"]}, fs={"read": ["d"]}),
        verdict="allow_sandboxed", reason="ok")
    return cases, expect


def test_parity() -> None:
    print("\n[2] py↔js parity（同一份 case，逐字段比较）")
    cases, expect = build_cases()
    with open(TMP_CASES, "w", encoding="utf-8") as f:
        json.dump(cases, f, ensure_ascii=False)
    check("case 数 ≥ 30", len(cases) >= 30, len(cases))

    code, out, err = run_node([SIM, "--cases", TMP_CASES])
    check("node --cases 退出码 0", code == 0, err[-300:])
    try:
        js_out = json.loads(out)
    except Exception as e:  # noqa: BLE001
        js_out = None
        check("node --cases 输出可解析", False, f"{e} / {out[:200]}")
    check("js 返回条数 == case 数", js_out is not None and len(js_out) == len(cases),
          len(js_out) if js_out else None)

    py_out = []
    for c in cases:
        py_out.append(_py_view(c["id"], c["input"]))
    check("py 返回条数 == case 数", len(py_out) == len(cases))

    ids = [c["id"] for c in cases]
    for cid in ids:
        exp = expect[cid]
        py = next((r for r in py_out if r["id"] == cid), None)
        js = next((r for r in (js_out or []) if r["id"] == cid), None)
        check(f"{cid}: python 裁决 == 期望（{exp[0]}/{exp[1]}）",
              py is not None and (py["verdict"], py["reason"]) == exp,
              py and (py["verdict"], py["reason"]))
        check(f"{cid}: js 裁决 == 期望", js is not None and (js["verdict"], js["reason"]) == exp,
              js and (js["verdict"], js["reason"]))
        if py and js:
            same = all(py[k] == js[k] for k in
                       ("verdict", "reason", "error_code", "failures", "findings", "exports",
                        "capabilities", "fs_scopes", "fingerprint", "sandbox"))
            check(f"{cid}: py↔js 逐字段一致", same,
                  f"py={json.dumps(py, sort_keys=True)[:220]} js={json.dumps(js, sort_keys=True)[:220]}")

    if os.path.exists(TMP_CASES):
        try:
            os.remove(TMP_CASES)
        except OSError:
            pass


def _py_view(cid: str, inputs: dict) -> dict:
    d = sc.decide(inputs)
    flags = d.get("sandbox", {}).get("node_flags") or []
    return {
        "id": cid,
        "verdict": d["verdict"],
        "reason": d["reason"],
        "error_code": d["error_code"],
        "failures": [f["code"] for f in d["failures"]],
        "findings": [{"id": f["id"], "capability": f["capability"], "line": f["line"]} for f in d["findings"]],
        "exports": d["exports"],
        "capabilities": d["capabilities"],
        "fs_scopes": d["fs_scopes"],
        "sandbox": {"mode": d["sandbox"]["mode"], "flags_len": len(flags)},
        "fingerprint": d["fingerprint"],
    }


# ══════════════════════════════════════════════════════════════════════
# 3) 物理沙箱实证（真实 spawn + permission model）
# ══════════════════════════════════════════════════════════════════════
def test_sandbox_runtime() -> None:
    print("\n[3] 物理沙箱实证（真实子进程 + Node permission model）")
    code, data, err = run_json_node([SIM, "--assert", "--json"], timeout=240)
    check("sim --assert --json 退出码 0", code == 0, err[-400:])
    check("sim 输出可解析", data is not None)
    if not data:
        return
    checks = data.get("checks") or []
    check("sim 断言数 ≥ 55", len(checks) >= 55, len(checks))
    check("sim 全部通过（fail==0）", data.get("fail") == 0,
          f"pass={data.get('pass')} fail={data.get('fail')}")
    names = {c["label"]: c["cond"] for c in checks}
    for label in ["宿主支持 Node permission model",
                  "良性插件加载成功",
                  "子进程 PID 与宿主不同",
                  "越界读 → ERR_ACCESS_DENIED",
                  "拼接 API 名绕过静态扫描",
                  "沙箱内越界写被拒（ERR_ACCESS_DENIED）",
                  "导入期越界写 → 加载被拒（fail-closed）",
                  "调用超时 → sandbox_call_timeout",
                  "声明 network → 不 spawn 直接 deny/SC-E10"]:
        hit = [v for k, v in names.items() if label in k]
        check(f"关键断言存在且通过：{label}", bool(hit) and all(hit), hit)
    check("state: 至少 1 个 denied", len(data.get("state", {}).get("denied", [])) >= 1)


def test_index_integration() -> None:
    print("\n[4] index.js 接入实证（真实 apply → 审计 → 挂起 → status）")
    code, data, err = run_json_node([INTEGRATION, "--json"], timeout=240)
    check("integration 退出码 0", code == 0, err[-500:])
    check("integration 输出可解析", data is not None)
    if not data:
        return
    checks = data.get("checks") or []
    check("integration 断言 10 条", len(checks) == 10, len(checks))
    check("integration 全部通过", data.get("fail") == 0,
          f"pass={data.get('pass')} fail={data.get('fail')}")
    ev = data.get("evidence") or {}
    check("良性插件在独立子进程（pid>0 且 ≠ 宿主）",
          isinstance(ev.get("benign_pid"), int) and ev["benign_pid"] > 0)
    check("恶意插件未执行（标记文件不存在）", ev.get("evil_marker_exists") is False)
    check("status 行含 deny 与挂起", "deny" in (data.get("status_line") or "")
          and "挂起=是" in (data.get("status_line") or ""), data.get("status_line"))


# ══════════════════════════════════════════════════════════════════════
# 4) 静态接线检查 + 文档/ADR
# ══════════════════════════════════════════════════════════════════════
def test_wiring_and_docs() -> None:
    print("\n[5] 接线与文档")
    for path, label in [(HOST, "sandbox-host.mjs"), (CHILD, "sandbox-child.mjs"),
                        (SIM, "supply-chain-sim.mjs"), (INTEGRATION, "supply-chain-integration.mjs")]:
        check(f"{label} 存在", os.path.isfile(path))
    idx = open(INDEX_JS, encoding="utf-8").read() if os.path.isfile(INDEX_JS) else ""
    check("index.js 引入 bootstrapSupplyChain（懒加载）", "bootstrapSupplyChain" in idx)
    check("index.js 配置项 supplyChain（enabled/plugins）",
          "supplyChain: { enabled: true, plugins: [] }" in idx)
    check("index.js 写 supply_chain_alert 告警事件", "supply_chain_alert" in idx)
    host_src = open(HOST, encoding="utf-8").read() if os.path.isfile(HOST) else ""
    check("sandbox-host.mjs 写 supply_chain_manifest 审计", "supply_chain_manifest" in host_src)
    check("index.js 状态可观测（供应链: 行）", "供应链:" in idx)
    check("index.js 未直接 import child_process（沙箱之外不 spawn 插件）",
          "node:child_process" not in idx)
    check("docs/26 存在且行数 ≥ 120",
          os.path.isfile(DOC26) and len(open(DOC26, encoding="utf-8").read().splitlines()) >= 120)
    check("ADR-018 存在且行数 ≥ 60",
          os.path.isfile(ADR18) and len(open(ADR18, encoding="utf-8").read().splitlines()) >= 60)


# ══════════════════════════════════════════════════════════════════════
def main() -> int:
    # Windows 控制台默认 GBK：统一改成 UTF-8，避免 ↔/⚠ 等字符把测试自身打挂
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001
            pass
    test_policy_single_source()
    test_scan_and_manifest()
    test_parity()
    test_sandbox_runtime()
    test_index_integration()
    test_wiring_and_docs()
    total = len(PASS) + len(FAIL)
    print("\n" + "=" * 60)
    print(f"{len(PASS)} ok / {len(FAIL)} fail（共 {total} 条断言）")
    for name in FAIL:
        print("  FAIL " + name)
    print("=" * 60)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    sys.exit(main())
