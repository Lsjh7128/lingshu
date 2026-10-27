# -*- coding: utf-8 -*-
"""把 `docs/24-工具网关UCA.md` §7 的机器可读 rules 数组注入两处实现（单一来源）。

- 条数/字段不硬编码：读 §7 后用 `check_doc()` 按**实际条数**校验（当前 16 条 v1.1）。
- 注入 Python（`POLICY_JSON = <repr>`）与 JS（`const POLICY_JSON = <json string>`），
  规则文案零手抄。改规则 → 改 docs/24 → 跑本脚本 → bump policy_version。
- 沙箱说明：harness 文件工具创建的文件对子进程只读 → 用 new-file + rename 覆盖。

用法：
    python tools/_sync_tool_policy.py            # 注入 + 自检
    python tools/_sync_tool_policy.py --check    # 只检查（0=已同步）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOC = os.path.join(ROOT, "docs/24-工具网关UCA.md")
PY = os.path.join(ROOT, "engine/tool_gateway.py")
JS = os.path.join(ROOT, "plugin/dsh-lingshu/tool-gateway.js")

PY_MARK = 'POLICY_JSON = r"""__POLICY_JSON_PLACEHOLDER__"""'
JS_MARK = 'const POLICY_JSON = "__POLICY_JSON_PLACEHOLDER__";'
REQUIRED_FIELDS = ("id", "verdict", "when", "args_check", "state_inputs",
                   "test_id", "message_zh", "message_en")


def extract_policy_text() -> str:
    md = open(DOC, encoding="utf-8").read()
    sec = md.split("## 7.", 1)
    if len(sec) != 2:
        raise SystemExit("FATAL: docs/24 里找不到 §7")
    m = re.search(r"```json\s*\n(.*?)\n```", sec[1], re.S)
    if not m:
        raise SystemExit("FATAL: §7 里找不到 ```json 代码块")
    return m.group(1)


def check_doc(text: str) -> dict:
    """§7 提交前自检（4 项），条数按实际值校验，不硬编码。"""
    obj = json.loads(text)                                   # 1) 可 json.loads
    rules = obj["rules"]
    ids = [r["id"] for r in rules]
    if len(set(ids)) != len(ids):                            # 2) id 唯一
        raise SystemExit("FATAL: rules id 重复：%r" % ids)
    if sorted(obj["evaluation_order"]) != sorted(ids):       # 3) 顺序集合一致
        raise SystemExit("FATAL: evaluation_order 与 rules 集合不一致")
    for r in rules:                                          # 4) 必填字段齐全
        missing = [k for k in REQUIRED_FIELDS if k not in r]
        if missing:
            raise SystemExit("FATAL: %s 缺字段 %r" % (r.get("id"), missing))
        if r["verdict"] not in ("block", "degrade", "allow"):
            raise SystemExit("FATAL: %s verdict=%r" % (r["id"], r["verdict"]))
    if not re.fullmatch(r"t-uca-\d+\.\d+", str(obj.get("policy_version"))):
        raise SystemExit("FATAL: policy_version=%r" % obj.get("policy_version"))
    return obj


def injected_lines(text: str) -> dict:
    return {"py": "POLICY_JSON = " + repr(text),
            "js": "const POLICY_JSON = " + json.dumps(text) + ";"}


def replace_file(path: str, content: str) -> None:
    tmp_new, tmp_old = path + ".new", path + ".old"
    with open(tmp_new, "w", encoding="utf-8", newline="\n") as f:
        f.write(content)
    if os.path.exists(tmp_old):
        os.remove(tmp_old)
    os.replace(path, tmp_old)
    os.replace(tmp_new, path)
    os.remove(tmp_old)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    text = extract_policy_text()
    obj = check_doc(text)
    lines = injected_lines(text)
    py_src = open(PY, encoding="utf-8").read()
    js_src = open(JS, encoding="utf-8").read()
    in_sync = (lines["py"] in py_src) and (lines["js"] in js_src)
    n = len(obj["rules"])
    b = sum(1 for r in obj["rules"] if r["verdict"] == "block")
    d = sum(1 for r in obj["rules"] if r["verdict"] == "degrade")
    if args.check:
        print("policy_version=%s rules=%d(block=%d,degrade=%d) in_sync=%s"
              % (obj["policy_version"], n, b, d, in_sync))
        return 0 if in_sync else 1

    changed = []
    if lines["py"] not in py_src:
        if PY_MARK in py_src:
            py_src = py_src.replace(PY_MARK, lines["py"])
        else:
            py_src = re.sub(r"^POLICY_JSON = .*$", lambda _m: lines["py"], py_src,
                            count=1, flags=re.M)
        replace_file(PY, py_src)
        changed.append("engine/tool_gateway.py")
    if lines["js"] not in js_src:
        if JS_MARK in js_src:
            js_src = js_src.replace(JS_MARK, lines["js"])
        else:
            js_src = re.sub(r"^const POLICY_JSON = .*$", lambda _m: lines["js"], js_src,
                            count=1, flags=re.M)
        replace_file(JS, js_src)
        changed.append("plugin/dsh-lingshu/tool-gateway.js")

    sys.path.insert(0, ROOT)
    import importlib
    from engine import tool_gateway as tg
    importlib.reload(tg)
    if tg.POLICY != obj:
        raise SystemExit("FATAL: engine 内联 policy != docs/24 §7")
    print("sync OK: policy_version=%s rules=%d(block=%d,degrade=%d) changed=%s"
          % (obj["policy_version"], n, b, d, ",".join(changed) or "(none)"))
    print("ids=" + ",".join(r["id"] for r in obj["rules"]))
    print("doc_sha256=" + hashlib.sha256(text.encode("utf-8")).hexdigest()[:16])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
