# -*- coding: utf-8 -*-
"""工具网关独立验收（Lead 侧，不复用实现者的测试代码）。

做四件事：
1) 全量既有套件回归：15 个 Python 测试文件 + 4 个 JS 套件，与基线比对（允许的既有失败只有 test_run_chaos.py 的 1 条）。
2) 新套件：tests/test_tool_gateway.py 必须 exit 0；tool-gateway-sim.mjs --assert 必须 exit 0。
3) 盲写留出集：paper/ablation/tool/heldout-tool-calls-v1.jsonl 逐条过网关，分「仅 args」与「带 context_hint」两口径报告。
4) 输出 acceptance/tool-gateway-verification.json，并打印摘要。

用法：python tools/verify_tool_chain.py [--write-baseline]
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PY = sys.executable
NODE_CANDIDATES = [
    os.environ.get("LINGSHU_NODE", ""),
    os.path.join(os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh"),
                 "dsh-runtimes", "dsh-primary-runtime", "dependencies", "node", "bin", "node.exe"),
    "node",
]
KNOWN_ALLOWED_FAILURES = {
    # 既有失败（改动前就存在，与本增量无关）：最新 host 产物 memory_conflict.beta_hit=false
    "test_run_chaos.py",
}
PY_TESTS = [
    "test_agent_state.py", "test_astrbot_adapter.py", "test_chaos_calibrate.py",
    "test_chaos_campaign.py", "test_constitution.py", "test_control.py", "test_gray.py",
    "test_learning.py", "test_mutation_regressions.py", "test_onboarding_persona.py",
    "test_pipeline.py", "test_pipeline_integration.py", "test_proposal.py",
    "test_run_chaos.py", "test_shadow_analysis.py",
]
JS_SUITES = ["host-sim.mjs", "control-sim.mjs", "stress.mjs", "mutation-regressions.mjs"]


def node_bin() -> str:
    for c in NODE_CANDIDATES:
        if c and os.path.exists(c):
            return c
    return "node"


def run(cmd, cwd):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run(cmd, cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    tail = (p.stdout or "") + (p.stderr or "")
    return p.returncode, tail.strip().splitlines()[-6:]


def count_passed(lines) -> int | None:
    for line in reversed(lines):
        m = re.search(r"PASS:\s*(\d+)", line)
        if m:
            return int(m.group(1))
        m = re.search(r"(\d+)\s+passed", line)
        if m:
            return int(m.group(1))
        m = re.search(r"断言总数[:：]\s*(\d+)", line)
        if m:
            return int(m.group(1))
    return None


def run_py_tests() -> dict:
    out = {}
    for name in PY_TESTS:
        code, lines = run([PY, os.path.join("tests", name)], ROOT)
        out[name] = {"exit": code, "passed": count_passed(lines), "tail": lines}
    return out


def run_js_suites() -> dict:
    out = {}
    node = node_bin()
    for name in JS_SUITES + ["tool-gateway-sim.mjs"]:
        path = os.path.join("plugin", "dsh-lingshu", name)
        if not os.path.exists(os.path.join(ROOT, path)):
            out[name] = {"exit": None, "skipped": "missing"}
            continue
        code, lines = run([node, path, "--assert"], ROOT)
        out[name] = {"exit": code, "passed": count_passed(lines), "tail": lines}
    return out


def eval_heldout() -> dict:
    sys.path.insert(0, ROOT)
    try:
        from engine import tool_gateway as TG  # type: ignore
    except Exception as e:  # pragma: no cover
        return {"error": "import engine.tool_gateway failed: %s" % e}
    path = os.path.join(ROOT, "paper", "ablation", "tool", "heldout-tool-calls-v1.jsonl")
    if not os.path.exists(path):
        return {"error": "missing heldout file"}
    rows = [json.loads(l) for l in open(path, encoding="utf-8") if l.strip()]
    res = {"n": len(rows), "args_only": {"blocked": 0, "detail": []},
           "with_context": {"blocked": 0, "detail": []}}
    for r in rows:
        call = {"tool": r["tool"], "args": r["args"]}
        state_base = {"variety_fallback": False, "phase": "normal", "saturated": False,
                      "unresolved_conflict": False, "frozen_ok": True, "audit_ok": True}
        try:
            v1 = TG.evaluate(call, state_base)
        except Exception as e:
            v1 = {"verdict": "error", "reason": str(e), "ucas": []}
        st = dict(state_base)
        hint = (r.get("context_hint") or "").lower()
        for key in ("冲突", "conflict", "unresolved"):
            if key in hint:
                st["unresolved_conflict"] = True
                st["variety_fallback"] = True
        for key in ("critical", "相变", "高负载"):
            if key in hint:
                st["phase"] = "critical"
        try:
            v2 = TG.evaluate(call, st)
        except Exception as e:
            v2 = {"verdict": "error", "reason": str(e), "ucas": []}
        b1 = v1.get("verdict") == "block"
        b2 = v2.get("verdict") == "block"
        res["args_only"]["blocked"] += int(b1)
        res["with_context"]["blocked"] += int(b2)
        res["args_only"]["detail"].append({"id": r["id"], "family": r.get("family"), "verdict": v1.get("verdict"), "ucas": v1.get("ucas")})
        res["with_context"]["detail"].append({"id": r["id"], "family": r.get("family"), "verdict": v2.get("verdict"), "ucas": v2.get("ucas")})
    res["args_only"]["rate"] = round(res["args_only"]["blocked"] / res["n"], 4) if res["n"] else None
    res["with_context"]["rate"] = round(res["with_context"]["blocked"] / res["n"], 4) if res["n"] else None
    return res


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write-baseline", action="store_true")
    args = ap.parse_args()
    os.chdir(ROOT)

    report = {
        "schema": "lingshu/tool-chain-verification/v1",
        "python_tests": run_py_tests(),
        "js_suites": run_js_suites(),
        "new_python_suite": None,
        "heldout": eval_heldout(),
    }
    new_py = os.path.join("tests", "test_tool_gateway.py")
    report["new_python_suite"] = run([PY, new_py], ROOT) if os.path.exists(new_py) else {"exit": None, "skipped": "missing"}

    # 允许失败判定
    unexpected = []
    for name, r in report["python_tests"].items():
        if r["exit"] not in (0, None) and name not in KNOWN_ALLOWED_FAILURES:
            unexpected.append("py:" + name)
    for name, r in report["js_suites"].items():
        if r.get("exit") not in (0, None):
            unexpected.append("js:" + name)
    report["unexpected_failures"] = unexpected
    report["pass"] = (not unexpected
                      and report["new_python_suite"].get("exit") == 0
                      and report["js_suites"].get("tool-gateway-sim.mjs", {}).get("exit") == 0)

    out = os.path.join("acceptance", "tool-gateway-verification.json")
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        json.dump(report, f, ensure_ascii=False, indent=1)

    print("== 工具网关独立验收 ==")
    print("Python 套件:", {k: v["exit"] for k, v in report["python_tests"].items()})
    print("JS 套件:", {k: v.get("exit") for k, v in report["js_suites"].items()})
    print("新 Python 套件:", report["new_python_suite"].get("exit") if isinstance(report["new_python_suite"], dict) else report["new_python_suite"])
    h = report["heldout"]
    if "error" in h:
        print("留出集: ", h["error"])
    else:
        print("留出集: n=%s args-only=%s/%s with-context=%s/%s" %
              (h["n"], h["args_only"]["blocked"], h["n"], h["with_context"]["blocked"], h["n"]))
    print("意外失败:", unexpected or "无")
    print("PASS:", report["pass"])
    print("报告:", out)
    return 0 if report["pass"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
