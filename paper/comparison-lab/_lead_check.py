# -*- coding: utf-8 -*-
"""Lead 只读检查：插件模式列表 + 真实数据目录是否被实验写入。"""
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CTRL = os.path.join(ROOT, "plugin", "dsh-lingshu", "control.js")
text = open(CTRL, encoding="utf-8").read()
i = text.find("JAILBREAK_PATTERNS")
print("---- control.js JAILBREAK_PATTERNS ----")
print(text[i:i + 900])

real = os.path.join(os.path.expanduser("~"), ".dsh", "plugins", "dsh-lingshu")
print("\n---- real plugin data dir ----")
if os.path.isdir(real):
    for name in ("audit.jsonl", "state.json", "memory.jsonl", "last-prompt.txt"):
        p = os.path.join(real, name)
        if os.path.isfile(p):
            st = os.stat(p)
            import datetime
            print("%-18s size=%-8d mtime=%s" % (name, st.st_size,
                  datetime.datetime.fromtimestamp(st.st_mtime).isoformat()))
else:
    print("real dir missing:", real)

cell = os.path.join(ROOT, "paper", "comparison-lab", "raw", "cells", "s2-jailbreak-lingshu-r1")
print("\n---- s2 lingshu r1 cell files ----")
for name in sorted(os.listdir(cell)):
    print("  ", name, os.path.getsize(os.path.join(cell, name)))
