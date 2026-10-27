# -*- coding: utf-8 -*-
"""风格/诚实性体检：检查改写后的论文是否 (a) 过度自贬集中/散落，(b) 出现禁区断言。

用法：python paper/_style_audit.py
"""
from __future__ import annotations

import glob
import os
import re

ROOT = os.path.dirname(os.path.abspath(__file__))

# 禁止出现的无条件断言（出现即红旗，人工复核是否带限定）
FORBIDDEN = [
    r"structurally prevents? (prompt )?injections?",
    r"beats? (Mem0|mem0)",
    r"validated thresholds?",
    r"calibrated thresholds?",
    r"proves? that .*prevents?",
    r"guarantees? (safety|no injection|immunity)",
]
# 自贬/证据边界词：应集中在 08-limitations 与 07-discussion，其他节少量
HUMBLE = [
    "effect size", "we do not report", "measured = 0", "measured=0",
    "uncalibrated", "all parameters are guesses", "no end-to-end",
    "not statistically", "n = 1", "n=1", "cannot be measured",
]
SECTION_ORDER = [
    "01-abstract", "02-intro", "03-related", "04-problem", "05-method",
    "06-experiments", "06b-supplement", "06c-host", "06d-engineering",
    "06e-actuator", "06f-baselines", "06g-case-study",
    "07-discussion", "08-limitations", "09-conclusion",
]


def scan_file(path: str):
    text = open(path, encoding="utf-8").read()
    hidden = {w: len(re.findall(w, text, re.I | re.S)) for w in FORBIDDEN}
    humble = {w: len(re.findall(re.escape(w), text, re.I)) for w in HUMBLE}
    return {k: v for k, v in {**{"FORBIDDEN:" + k: v for k, v in hidden.items()},
                              **{"humble:" + k: v for k, v in humble.items()}}.items() if v}


def main():
    print("== 英文 sections ==")
    total_forbidden = 0
    for name in SECTION_ORDER:
        p = os.path.join(ROOT, "sections", name + ".tex")
        if not os.path.exists(p):
            continue
        hits = scan_file(p)
        if hits:
            print(" ", name, hits)
            total_forbidden += sum(v for k, v in hits.items() if k.startswith("FORBIDDEN:"))
    print("\n== 中文 sections ==")
    for name in SECTION_ORDER:
        p = os.path.join(ROOT, "sections-zh", name + ".tex")
        if not os.path.exists(p):
            continue
        text = open(p, encoding="utf-8").read()
        zh_humble = {w: text.count(w) for w in ["猜测", "未标定", "未测出", "不报告", "无法测量"] if text.count(w)}
        if zh_humble:
            print(" ", name, zh_humble)
    print("\nRED FLAGS:", total_forbidden)
    return 0 if total_forbidden == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
