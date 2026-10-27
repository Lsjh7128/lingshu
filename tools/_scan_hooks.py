# -*- coding: utf-8 -*-
"""扫描 host 源码里的钩子名与 waterfall dispatch 点（临时工具）。"""
import io
import os
import re
import sys
from collections import Counter

root = sys.argv[1]
hook_re = re.compile(r"['\"]([a-z]+/[a-z][a-z\-]*)['\"]")
names = Counter()
waterfalls = []

for dirpath, _dirs, files in os.walk(root):
    if "node_modules" in dirpath[len(root):].split(os.sep)[:1]:
        pass
    for fn in files:
        if not fn.endswith(".js"):
            continue
        p = os.path.join(dirpath, fn)
        try:
            if os.path.getsize(p) > 900_000:
                continue
            text = io.open(p, encoding="utf-8", errors="ignore").read()
        except OSError:
            continue
        for m in hook_re.finditer(text):
            name = m.group(1)
            if name.split("/")[0] in ("agent", "turn", "session", "tool", "message",
                                      "step", "prompt", "model", "llm", "approval"):
                names[name] += 1
        for m in re.finditer(r"waterfall\(\s*['\"]([^'\"]+)['\"]", text):
            waterfalls.append((m.group(1), os.path.basename(p)))

print("=== 钩子/事件名（次数）===")
for k, v in names.most_common(30):
    print("%6d  %s" % (v, k))

print("\n=== waterfall 调用点 ===")
seen = set()
for name, f in waterfalls:
    if (name, f) in seen:
        continue
    seen.add((name, f))
    print("  %-24s %s" % (name, f))
