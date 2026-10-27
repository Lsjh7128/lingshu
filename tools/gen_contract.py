# -*- coding: utf-8 -*-
"""生成 / 校验契约（engram 模式）。

用法：
    python tools/gen_contract.py --write     # 重新生成 contracts/agent-contract-v1.json
    python tools/gen_contract.py --check     # 漂移检查，退出码 0/1

为什么要有 --check：
    手写契约必然与代码漂移。`--check` 让"契约过期"变成**可自动发现的失败**，
    而不是等到实现方按旧契约写完之后才发现（R2 契约漂移的缓解措施）。
"""
from __future__ import annotations

import argparse
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import contract as c  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="重新生成契约文件")
    ap.add_argument("--check", action="store_true", help="漂移检查（不一致退出码 1）")
    args = ap.parse_args()

    manifest = json.load(open(os.path.join(ROOT, "contracts", "_manifest.json"),
                              encoding="utf-8"))
    fresh = c.dumps(c.build(manifest, ROOT))
    path = os.path.join(ROOT, "contracts", "agent-contract-v1.json")

    if args.write:
        open(path, "w", encoding="utf-8", newline="\n").write(fresh)
        print("written:", path)
        print("digest:", json.loads(fresh)["digest"])
        return 0

    code, msg = c.check(ROOT)
    print(msg)
    return code


if __name__ == "__main__":
    raise SystemExit(main())
