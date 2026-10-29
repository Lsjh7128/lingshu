# -*- coding: utf-8 -*-
p = "sections-zh/06-experiments.tex"
s = open(p, encoding="utf-8").read()
old = "；插件无法改写已经拼好的请求。"
new = ("；修复前的插件无法改写已经拼好的请求。v5.4 移除了该滞后（见“相变前馈监控”一节）："
       "回归套件复现宿主的钩子时序并断言 \\texttt{anchor\\_lag=false}，"
       "S1 试点也在修复后重跑（见“外部基线”一节）。")
assert old in s, "anchor missing"
open(p, "w", encoding="utf-8", newline="\n").write(s.replace(old, new, 1))
print("patched zh lag sentence")
