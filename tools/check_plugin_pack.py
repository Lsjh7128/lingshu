# -*- coding: utf-8 -*-
"""校验插件包的 files 清单是否完整（防"发出去少文件"）。

    python tools/check_plugin_pack.py

规则：
1. `files` 里列出的每个路径都必须存在；
2. **运行时**以外的 .js 必须列进 files（运行时 = index.js + 被 import 的模块）；
3. 测试脚本（host-sim / stress）不进包，但必须存在于仓库；
4. `dsh.bundle.patch` 指向的文件必须存在且被列进 files。
"""
from __future__ import annotations

import io
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PKG_DIR = os.path.join(ROOT, "plugin", "dsh-lingshu")


def main() -> int:
    pkg_path = os.path.join(PKG_DIR, "package.json")
    pkg = json.loads(io.open(pkg_path, encoding="utf-8").read())
    files = pkg.get("files", [])
    errors: list[str] = []

    for f in files:
        if not os.path.isfile(os.path.join(PKG_DIR, f)):
            errors.append("files 列了但不存在: %s" % f)

    # 运行时依赖：从 main 出发，沿 import 递归
    runtime = set()
    stack = [pkg.get("main", "index.js")]
    while stack:
        cur = stack.pop()
        if cur in runtime:
            continue
        runtime.add(cur)
        p = os.path.join(PKG_DIR, cur)
        if not os.path.isfile(p):
            errors.append("main/import 指向的文件不存在: %s" % cur)
            continue
        text = io.open(p, encoding="utf-8").read()
        for m in re.finditer(r"from\s+'\./([^']+)'", text):
            stack.append(m.group(1))

    for f in sorted(runtime):
        if f not in files:
            errors.append("运行时文件未列进 files: %s" % f)

    for req in ("host-sim.mjs", "stress.mjs"):
        if not os.path.isfile(os.path.join(PKG_DIR, req)):
            errors.append("缺少测试脚本: %s" % req)
        if req in files:
            errors.append("测试脚本不该进包: %s" % req)

    patch = pkg.get("dsh", {}).get("bundle", {}).get("patch")
    if not patch:
        errors.append("缺少 dsh.bundle.patch 声明")
    else:
        rel = patch.lstrip("./")
        if not os.path.isfile(os.path.join(PKG_DIR, rel)):
            errors.append("bundle patch 指向的文件不存在: %s" % rel)
        if rel not in files:
            errors.append("bundle patch 未列进 files: %s" % rel)

    if errors:
        print("FAIL: plugin/dsh-lingshu 打包清单有问题")
        for e in errors:
            print("  -", e)
        return 1
    print("OK: 插件打包清单完整（files=%d，运行时=%s）"
          % (len(files), ",".join(sorted(runtime))))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
