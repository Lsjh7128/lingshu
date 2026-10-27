# -*- coding: utf-8 -*-
"""校验 annotator-a5 落盘结果（只读）：字节一致、quote 逐字、分布、sha 冻结行。"""
import hashlib
import json
import os
import sys

sys.stdout.reconfigure(encoding="utf-8")
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LAB = os.path.join(ROOT, "paper/supplement/labels/annotator-a5.jsonl")
BUNDLE = os.path.join(ROOT, "paper/ablation/results/_a5_annot_content.json")
BLIND = os.path.join(ROOT, "paper/host-validation/control-ablation/blind-annotation-v1.jsonl")

raw = open(LAB, "rb").read()
sha = hashlib.sha256(raw).hexdigest()
b = json.load(open(BUNDLE, encoding="utf-8"))
blind = {json.loads(l)["item_id"]: json.loads(l)["text"]
         for l in open(BLIND, encoding="utf-8") if l.strip()}
lines = [json.loads(l) for l in raw.decode("utf-8").splitlines() if l.strip()]
ok = True


def chk(name, cond, detail=""):
    global ok
    print("  [%s] %s%s" % ("PASS" if cond else "FAIL", name,
                           "" if cond else "  ← %s" % detail))
    ok = ok and bool(cond)


print("annotator-a5 落盘校验")
chk("文件 sha256 == 生成时冻结值", sha == b["sha"], "%s vs %s" % (sha, b["sha"]))
chk("字节内容与生成器输出逐字一致", raw.decode("utf-8") == b["labels_text"])
chk("18 条 / item_id 与盲评文件一致",
    len(lines) == 18 and {x["item_id"] for x in lines} == set(blind),
    (len(lines),))
q_ok, m_ok = True, True
for x in lines:
    t = blind[x["item_id"]]
    for k, q in (x.get("metric_quotes") or {}).items():
        q_ok = q_ok and (q in t) and len(q) <= 40
    m_ok = m_ok and all(x.get(k) in (0, 1, 2) for k in ("M1", "M2", "M3", "M4"))
    m_ok = m_ok and bool(x.get("quote")) and x["quote"] in t
chk("每条 4 项 quote 均为原文逐字子串（≤40 字）", q_ok)
chk("M1–M4 取值合法且主 quote 逐字", m_ok)
dist = {k: sorted({x[k] for x in lines}) for k in ("M1", "M2", "M3", "M4")}
chk("分布与 meta 记录一致", dist == b["distribution"], dist)
shafile = os.path.join(ROOT, "paper/supplement/labels/annotator-a5.sha256")
if os.path.isfile(shafile):
    txt = open(shafile, encoding="utf-8").read().strip()
    chk("sha256 冻结行与文件一致", txt.split()[0] == sha, txt)
metaf = os.path.join(ROOT, "paper/supplement/labels/annotator-a5.meta.json")
if os.path.isfile(metaf):
    meta_raw = open(metaf, "rb").read()
    chk("meta.json 与生成器输出逐字一致",
        meta_raw.decode("utf-8") == b["meta_text"])
    chk("meta 记录 labels_sha 一致",
        json.loads(meta_raw)["labels_sha256"] == sha)
print("\n结论：%s（sha=%s）" % ("全部通过" if ok else "有失败项", sha))
raise SystemExit(0 if ok else 1)
