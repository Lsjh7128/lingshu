# -*- coding: utf-8 -*-
"""离线装 matplotlib：从清华镜像直接拉 wheel 并解包到 paper/.pylib。
绕过 pip 的解析/临时目录问题。"""
import io
import os
import re
import sys
import urllib.parse
import urllib.request
import zipfile

MIRROR = "https://pypi.tuna.tsinghua.edu.cn/simple"
TARGET = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".pylib")
CACHE = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".wheels")
os.makedirs(TARGET, exist_ok=True)
os.makedirs(CACHE, exist_ok=True)

PKGS = ["matplotlib", "contourpy", "cycler", "fonttools", "kiwisolver", "packaging", "pyparsing", "pillow"]
PY = "cp312"
PLAT = "win_amd64"

opener = urllib.request.build_opener()
opener.addheaders = [("User-Agent", "Mozilla/5.0 (Windows NT 10.0; Win64; x64)")]


def get(url, timeout=30):
    with opener.open(url, timeout=timeout) as r:
        return r.read()


def pick(name):
    index_url = "%s/%s/" % (MIRROR, name)
    html = get(index_url).decode("utf-8", "ignore")
    hrefs = re.findall(r'href="([^"]+\.whl)', html)
    cands = []
    for h in hrefs:
        fn = h.rsplit("/", 1)[-1]
        if not fn.endswith(".whl"):
            continue
        low = fn.lower()
        if ("py3-none-any" in low) or (PY in low and PLAT in low):
            cands.append((fn, h))
    if not cands:
        raise SystemExit("no wheel for %s" % name)
    # 版本号降序取最新：从文件名里抽数字段粗略排序
    def key(item):
        nums = re.findall(r"\d+", item[0].split("-")[1])
        return tuple(int(x) for x in nums[:3])
    cands.sort(key=key)
    return cands[-1], index_url


for name in PKGS:
    (fn, href), index_url = pick(name)
    dest = os.path.join(CACHE, fn)
    if not os.path.exists(dest):
        url = urllib.parse.urljoin(index_url, href)
        data = get(url, timeout=180)
        with open(dest, "wb") as f:
            f.write(data)
        print("downloaded %-60s %8d B" % (fn, len(data)), flush=True)
    else:
        print("cached     %s" % fn, flush=True)
    with zipfile.ZipFile(dest) as z:
        z.extractall(TARGET)

print("extracted ->", TARGET)
