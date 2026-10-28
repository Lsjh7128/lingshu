# -*- coding: utf-8 -*-
"""Phase C 消融核对：神经后端相对概念层到底新增了多少条拦截。

为什么要有这个脚本：
`run-semantic{,-neural,-hybrid}.json` 里的 rate 是聚合值，无法直接回答
"神经层有没有拦到概念层漏掉的东西"。本脚本对 E1/E0 逐条跑两个后端，
做 2x2 列联表（both / offline_only / neural_only / neither）。

要点：只读 E1/E0 的 payload（评估用途，Lead 已预注册判定），
**不参与任何阈值选择**（阈值早就在 D_dev 上冻结了）。

用法（必须用装了 onnxruntime 的 venv）：
    .venv-neural\\Scripts\\python.exe -X utf8 experiments\\phase-c\\analysis\\neural_overlap_check.py
"""
import io
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))
sys.path.insert(0, ROOT)

from tools import semantic_guard as sg  # noqa: E402

E1 = os.path.join(ROOT, "experiments", "phase-b", "runs", "run-20261028-185512",
                  "zeroday_set.jsonl")
E0 = os.path.join(ROOT, "experiments", "phase-c", "eval", "zeroday_fresh.jsonl")
OUT = os.path.join(ROOT, "experiments", "phase-c", "analysis",
                   "neural_vs_concept_ablation.json")


def read(path):
    rows = []
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def overlap(rows, tag):
    both, off_only, neu_only, neither = [], [], [], []
    for r in rows:
        text = r.get("payload") or r.get("text") or ""
        off = sg.is_risky(text, backend="offline_concept")
        neu = sg.is_risky(text, backend="neural")
        rid = r.get("variant_id") or r.get("id")
        if off["risky"] and neu["risky"]:
            both.append(rid)
        elif off["risky"]:
            off_only.append(rid)
        elif neu["risky"]:
            neu_only.append({"id": rid, "technique": r.get("technique"),
                             "neural_score": round(float(neu["score"]), 4),
                             "offline_score": round(float(off["score"]), 4)})
        else:
            neither.append(rid)
    return {"set": tag, "n": len(rows), "both": len(both),
            "offline_only": len(off_only), "offline_only_ids": off_only,
            "neural_only": len(neu_only), "neural_only_items": neu_only,
            "neither": len(neither), "neither_ids": neither,
            "offline_intercepted": len(both) + len(off_only),
            "neural_intercepted": len(both) + len(neu_only),
            "neural_increment_over_offline": len(neu_only),
            "offline_increment_over_neural": len(off_only)}


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    res = {"schema": "phase-c-neural-vs-concept-v1",
           "generated_at": __import__("time").strftime("%Y-%m-%dT%H:%M:%S%z"),
           "library_sha256": (sg.load_intent_library().get("meta") or {}).get("library_sha256"),
           "thresholds": {"offline_concept": sg.load_intent_library()
                          .get("thresholds", {}).get("by_backend", {})
                          .get("offline_concept", {}).get("default"),
                          "neural": sg.load_intent_library()
                          .get("thresholds", {}).get("by_backend", {})
                          .get("neural", {}).get("default")},
           "sets": {}, "variants": {}}
    for tag, path in (("E1", E1), ("E0", E0)):
        res["sets"][tag] = overlap(read(path), tag)
        s = res["sets"][tag]
        print("%s n=%d | offline 拦 %d | neural 拦 %d | both %d | neural_only %d | offline_only %d"
              % (tag, s["n"], s["offline_intercepted"], s["neural_intercepted"],
                 s["both"], s["neural_only"], s["offline_only"]))
        for it in s["neural_only_items"]:
            print("   neural_only:", it)
    base = os.path.join(ROOT, "experiments", "phase-c", "results")
    for name, fn in (("auto", "run-semantic.json"),
                     ("neural", "run-semantic-neural.json"),
                     ("hybrid", "run-semantic-hybrid.json")):
        p = os.path.join(base, fn)
        if not os.path.exists(p):
            continue
        d = json.load(io.open(p, encoding="utf-8"))
        res["variants"][name] = {
            "encoder_backend": d["encoder"].get("backend_effective"),
            "threshold": d["encoder"].get("threshold"),
            "E1_rate": d["systems"]["C"]["E1"]["rate"],
            "E0_rate": d["systems"]["C"]["E0"]["rate"],
            "benign_fpr": d["systems"]["C"]["benign"]["fpr"],
            "p95_E1_ms": d["systems"]["C"]["E1"]["latency"]["p95_ms"],
            "p95_E0_ms": d["systems"]["C"]["E0"]["latency"]["p95_ms"],
        }
    os.makedirs(os.path.dirname(OUT), exist_ok=True)
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(res, fh, ensure_ascii=False, indent=2)
    print("\nvariants:", json.dumps(res["variants"], ensure_ascii=False, indent=1))
    print("saved:", OUT)


if __name__ == "__main__":
    main()
