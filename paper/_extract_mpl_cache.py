# -*- coding: utf-8 -*-
"""从 pip 的 http 缓存 .body 文件里识别 wheel 并解包到 paper/.pylib。
pip 第一次尝试已经把 wheel 拉下来过，只是解包时被临时目录权限挡住。"""
import os
import re
import zipfile

CACHE = os.path.join(os.environ.get("LOCALAPPDATA")
                     or os.path.join(os.path.expanduser("~"), "AppData", "Local"),
                     "pip", "Cache")
TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pylib")
WANT = {"matplotlib", "contourpy", "cycler", "fonttools", "kiwisolver", "packaging", "pyparsing", "pillow"}

os.makedirs(TARGET, exist_ok=True)
found = {}

for root, _dirs, files in os.walk(CACHE):
    for fn in files:
        if not fn.endswith(".body"):
            continue
        p = os.path.join(root, fn)
        try:
            if os.path.getsize(p) < 50000:
                continue
            with zipfile.ZipFile(p) as z:
                names = z.namelist()
                metas = [n for n in names if n.endswith(".dist-info/METADATA")]
                if not metas:
                    continue
                meta = z.read(metas[0]).decode("utf-8", "ignore")
                m = re.search(r"^Name: (.+)$", meta, re.M)
                v = re.search(r"^Version: (.+)$", meta, re.M)
                if not m:
                    continue
                name = m.group(1).strip().lower()
                ver = v.group(1).strip() if v else "?"
                if name in WANT and name not in found:
                    found[name] = (p, ver, len(names))
                    print("FOUND %-12s %-12s entries=%-5d %s" % (name, ver, len(names), p), flush=True)
        except Exception:
            continue

print("\nfound %d/%d" % (len(found), len(WANT)))
for name in sorted(WANT):
    if name not in found:
        print("MISSING", name)
        continue
    p, ver, _ = found[name]
    with zipfile.ZipFile(p) as z:
        z.extractall(TARGET)
    print("extracted", name, ver)
