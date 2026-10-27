# -*- coding: utf-8 -*-
"""校验 profile 补丁文件的结构（cordis.patch.yml），防止改坏 DSH 启动。

为什么需要：`~/.dsh/profiles/desktop/` 里有若干 `.corrupt` / `.bak` 备份，
说明这个文件被改坏过。它坏掉的后果是 **DSH 起不来**，而那时候就没人能来修了。

用法：
    python tools/check_patch_yaml.py <patch文件> [--expect-marker "dsh-lingshu-probe"]

检查项（都是能自动判定的结构不变量，不做完整 YAML 解析）：
1. 没有 tab 字符（YAML 里 tab 非法缩进）
2. 每个非空非注释行，要么以 "- " 开头（顶层数组项），要么有缩进
3. 顶层以一个或多个 "- " 项开头（本文件是数组，不是映射）
4. 引号成对（单/双引号计数为偶数）
5. 末尾有换行
6. 可选：指定标记恰好出现 N 次（默认 1）
"""
from __future__ import annotations

import argparse
import io
import sys


def check(path: str, marker: str | None, marker_count: int = 1) -> list[str]:
    raw = io.open(path, encoding="utf-8").read()
    errors: list[str] = []
    lines = raw.split("\n")

    if "\t" in raw.replace("\t", "\t"):
        for i, ln in enumerate(lines, 1):
            if "\t" in ln:
                errors.append("第 %d 行含 tab（YAML 不允许 tab 缩进）：%r" % (i, ln[:60]))

    top_items = 0
    for i, ln in enumerate(lines, 1):
        if not ln.strip() or ln.lstrip().startswith("#"):
            continue
        if ln.startswith("- "):
            top_items += 1
            continue
        if ln[0] in (" ", "\t"):
            continue
        errors.append("第 %d 行既不是顶层数组项也不是缩进行：%r" % (i, ln[:60]))

    if top_items == 0:
        errors.append("顶层没有 '- ' 数组项：这不像 cordis.patch.yml")

    if raw.count('"') % 2 != 0:
        errors.append("双引号数量为奇数（可能有未闭合字符串）")
    if raw.count("'") % 2 != 0:
        errors.append("单引号数量为奇数（可能有未闭合字符串）")

    if not raw.endswith("\n"):
        errors.append("文件末尾缺少换行")

    if marker:
        n = raw.count(marker)
        if n != marker_count:
            errors.append("标记 %r 出现 %d 次，期望 %d 次" % (marker, n, marker_count))

    return errors


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("path")
    ap.add_argument("--expect-marker", default=None)
    ap.add_argument("--marker-count", type=int, default=1)
    args = ap.parse_args()

    errs = check(args.path, args.expect_marker, args.marker_count)
    if errs:
        print("FAIL: %s" % args.path)
        for e in errs:
            print("  -", e)
        return 1
    print("OK: %s（结构检查通过）" % args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
