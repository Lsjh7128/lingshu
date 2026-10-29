# -*- coding: utf-8 -*-
"""Repair the botched bold-marker replacement in Chinese sections."""
import os

ROOT = os.path.dirname(os.path.abspath(__file__))

fixes = {
    "sections-zh/06h-immersive-case.tex": [
        (r"捕获，\\textbf{\1}（run",
         r"捕获，\textbf{52/52 条断言通过}（run"),
        (r"在\\textbf{\1}被验证",
         r"在\textbf{进程边界之外}被验证"),
    ],
    "sections-zh/10e-risk-classifier.tex": [
        (r"属于\\textbf{\1}，不是质量证明",
         r"属于\textbf{测量装置}，不是质量证明"),
        (r"3 条已解决、\\textbf{\1}。",
         r"3 条已解决、\textbf{8 条仍开放}。"),
    ],
}

for rel, pairs in fixes.items():
    p = os.path.join(ROOT, rel)
    s = open(p, encoding="utf-8").read()
    for old, new in pairs:
        if old in s:
            s = s.replace(old, new, 1)
            print("OK  ", rel, "|", old[:30])
        else:
            print("MISS", rel, "|", old[:30])
    open(p, "w", encoding="utf-8", newline="\n").write(s)
