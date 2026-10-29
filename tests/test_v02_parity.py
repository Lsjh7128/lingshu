# -*- coding: utf-8 -*-
"""v0.2 语义 parity：Python 语义源 ↔ JS 镜像（施工单 v2 §8 G1 parity 8 条）

做法：Node 侧 `memory-paths-sim.mjs --parity tests/v02_parity_cases.json` 输出一条 JSON，
本文件用 engine/layout.py 对同一批输入计算，逐组比对。任一侧缺失/报错即失败（不许跳过）。
"""
from __future__ import annotations

import json
import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CASES = os.path.join(ROOT, "tests", "v02_parity_cases.json")
SIM = os.path.join(ROOT, "plugin", "dsh-lingshu", "memory-paths-sim.mjs")
sys.path.insert(0, ROOT)

from engine import layout as L  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
    else:
        FAIL.append(name)
        if detail:
            print("  ! %s: %s" % (name, detail))


def _fake(real: str):
    return lambda _p: real


def run_js() -> tuple[dict | None, str]:
    if not os.path.isfile(SIM):
        return None, "sim 不存在: %s" % SIM
    try:
        proc = subprocess.run(["node", SIM, "--parity", CASES], cwd=ROOT,
                              capture_output=True, text=True, timeout=120,
                              encoding="utf-8", errors="replace")
    except (OSError, subprocess.TimeoutExpired) as err:
        return None, str(err)
    if proc.returncode != 0:
        return None, "exit=%s stdout=%s stderr=%s" % (
            proc.returncode, proc.stdout[-300:], proc.stderr[-300:])
    lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
    if not lines:
        return None, "stdout 为空"
    try:
        return json.loads(lines[-1]), ""
    except ValueError as err:
        return None, "最后一行不是 JSON: %s / %s" % (err, lines[-1][:200])


def main() -> int:
    if not os.path.isfile(CASES):
        print("  ! 用例文件缺失: %s" % CASES)
        print("PASS: 0 ｜ FAIL: 1 ｜ 断言总数: 1")
        return 1
    cases = json.load(open(CASES, encoding="utf-8"))
    data, err = run_js()
    check("parity：Node 镜像可执行并输出 JSON", data is not None, err)
    if data is None:
        print("PASS: %d ｜ FAIL: %d ｜ 断言总数: %d" % (len(PASS), len(FAIL), len(PASS) + len(FAIL)))
        return 1

    # 1) 规范化
    exp = [L.canonicalize_path(c["input"], realpath=_fake(c["realpath"]))
           for c in cases["canonical"]]
    check("parity：canonicalPath 规范化 %d 例" % len(exp),
          data.get("canonical") == exp,
          "js=%r py=%r" % (data.get("canonical"), exp))

    # 2) scope 路由
    exp = [L.decide_scope(c["text"]) for c in cases["scope"]]
    check("parity：scope 路由 %d 例" % len(exp), data.get("scope") == exp,
          "js=%r py=%r" % (data.get("scope"), exp))

    # 3) UCA-01
    exp = [{"verdict": r["verdict"], "reasons": sorted(r["reasons"])}
           for r in (L.check_uca01_text(c.get("text")) for c in cases["uca"])]
    got = data.get("uca") or []
    got_norm = [{"verdict": r.get("verdict"), "reasons": sorted(r.get("reasons") or [])} for r in got]
    check("parity：UCA-01 文本检查 %d 例" % len(exp), got_norm == exp,
          "js=%r py=%r" % (got_norm, exp))

    # 4) 合并裁决
    ok = True
    details = []
    for idx, case in enumerate(cases["merge"]):
        js = (data.get("merge") or [])[idx] if idx < len(data.get("merge") or []) else {}
        py = L.merge_layers(case["rows"], sandbox_id=case["sandbox_id"],
                            persona_id=case["persona_id"],
                            budget=case.get("budget", 900), top_k=case.get("top_k", 3))
        py_ids = [str(r["id"]) for r in py["selected"]]
        py_dropped = sorted((str(d["id"]), str(d["reason"])) for d in py["dropped"])
        js_dropped = sorted(tuple(x) for x in (js.get("dropped_ids_reasons") or []))
        if js.get("selected_ids") != py_ids:
            ok = False
            details.append("case%d selected js=%r py=%r" % (idx, js.get("selected_ids"), py_ids))
        if js_dropped != py_dropped:
            ok = False
            details.append("case%d dropped js=%r py=%r" % (idx, js_dropped, py_dropped))
        if js.get("global_tokens") != py["global_tokens"] or js.get("local_count") != py["local_count"]:
            ok = False
            details.append("case%d tokens/count js=%r/%r py=%r/%r" % (
                idx, js.get("global_tokens"), js.get("local_count"),
                py["global_tokens"], py["local_count"]))
        js_conf = [(c.get("winner"), sorted(c.get("dropped_ids") or [])) for c in (js.get("conflicts") or [])]
        py_conf = [(c["winner"], sorted(c["dropped_ids"])) for c in py["conflicts"]]
        if js_conf != py_conf:
            ok = False
            details.append("case%d conflicts js=%r py=%r" % (idx, js_conf, py_conf))
    check("parity：合并裁决 %d 例" % len(cases["merge"]), ok, "；".join(details))

    # 5) 锁键
    exp = [L.lock_key(c["kind"], sandbox_id=c.get("sandbox_id"),
                      persona_id=c.get("persona_id"), scope=c.get("scope"))
           for c in cases["lock_keys"]]
    check("parity：锁键 %d 例" % len(exp), data.get("lock_keys") == exp,
          "js=%r py=%r" % (data.get("lock_keys"), exp))

    # 6) 六键路径
    exp = []
    for c in cases["paths"]:
        p = L.resolve_memory_paths(c["root"], c["sandbox_id"], c["persona_id"])
        exp.append({k: os.path.relpath(v, c["root"]).replace("\\", "/") for k, v in p.items()})
    check("parity：六键路径 %d 例" % len(exp), data.get("paths") == exp,
          "js=%r py=%r" % (data.get("paths"), exp))

    # 7) 值域自证（防止镜像把异常当空结果）
    check("parity：JS 结果非空且六组齐全",
          isinstance(data, dict) and all(k in data for k in
                                         ("canonical", "scope", "uca", "merge", "lock_keys", "paths")))

    # 8) 语义源一致性口径：Python 自身不因用例文件变化而漂移
    check("parity：用例数与断言数固定（%d/%d/%d/%d/%d/%d）" % (
        len(cases["canonical"]), len(cases["scope"]), len(cases["uca"]),
        len(cases["merge"]), len(cases["lock_keys"]), len(cases["paths"])),
        len(cases["canonical"]) >= 5 and len(cases["scope"]) >= 8
        and len(cases["uca"]) >= 12 and len(cases["merge"]) >= 6)

    print("PASS: %d ｜ FAIL: %d ｜ 断言总数: %d" % (len(PASS), len(FAIL), len(PASS) + len(FAIL)))
    if FAIL:
        print("失败项：" + "；".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
