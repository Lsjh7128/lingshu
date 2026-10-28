# -*- coding: utf-8 -*-
"""A5 收口验证（只读）：本次 demo 的 C 数字 vs Lead 官方 run-semantic.json 逐字段对比。

用法（仓库根目录）：
    python experiments/phase-c/runs/run-20261028-193349/verify_closure.py

只读两份评估 JSON 并打印/落盘对比，不重算任何判定、不修改任何一侧数据。
"""
import io
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
DEMO = os.path.join(HERE, "run-semantic-demo.json")
OFFICIAL = os.path.join(ROOT, "experiments", "phase-c", "results", "run-semantic.json")
OUT = os.path.join(HERE, "closure_numbers.json")


def main() -> int:
    demo = json.load(io.open(DEMO, encoding="utf-8"))
    official = json.load(io.open(OFFICIAL, encoding="utf-8"))
    rows = []
    all_ok = True
    print("demo run_id=%s | official run_id=%s"
          % (demo.get("run_id"), official.get("run_id")))
    print("%-4s %-7s %-10s %-10s %-6s %-12s"
          % ("SYS", "SET", "demo", "official", "same", "official_p95_ms"))
    for s in ("A", "B", "C"):
        for e in ("E1", "E0"):
            d = demo["systems"][s][e]
            o = official["systems"][s][e]
            same = d["rate"] == o["rate"] and d["intercepted"] == o["intercepted"]
            all_ok = all_ok and same
            rows.append({"system": s, "set": e, "demo_rate": d["rate"],
                         "official_rate": o["rate"],
                         "demo_intercepted": d["intercepted"],
                         "official_intercepted": o["intercepted"],
                         "identical": same})
            print("%-4s %-7s %-10s %-10s %-6s %-12s"
                  % (s, e, d["rate"], o["rate"], same,
                     (o.get("latency") or {}).get("p95_ms")))
        db, ob = demo["systems"][s]["benign"], official["systems"][s]["benign"]
        same = db["fpr"] == ob["fpr"] and db["fp_count"] == ob["fp_count"]
        all_ok = all_ok and same
        rows.append({"system": s, "set": "benign", "demo_fpr": db["fpr"],
                     "official_fpr": ob["fpr"], "identical": same})
        print("%-4s %-7s %-10s %-10s %-6s"
              % (s, "benign", "fpr=" + str(db["fpr"]), "fpr=" + str(ob["fpr"]), same))
    dm = demo["systems"]["C"]["E1"]["adjudicated"]["M"]["rate"]
    om = official["systems"]["C"]["E1"]["adjudicated"]["M"]["rate"]
    all_ok = all_ok and dm == om
    print("硬红线 M 召回（E1）：demo=%s official=%s same=%s" % (dm, om, dm == om))
    delta_same = demo.get("delta") == official.get("delta")
    all_ok = all_ok and delta_same
    print("delta 完全一致：%s" % delta_same)
    payload = {
        "schema": "phase-c-closure-numbers-v1",
        "demo_run_id": demo.get("run_id"),
        "official_run_id": official.get("run_id"),
        "demo_result_json": os.path.relpath(DEMO, ROOT).replace(os.sep, "/"),
        "official_result_json": os.path.relpath(OFFICIAL, ROOT).replace(os.sep, "/"),
        "all_rates_fpr_intercepted_identical": all_ok,
        "delta_identical": delta_same,
        "hard_redline_M_recall_E1": {"demo": dm, "official": om},
        "rows": rows,
        "note": "p95 是墙钟测量，不要求逐位一致；量级对比见 eval_determinism_check.json",
    }
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print("ALL_RATES_FPR_INTERCEPTED_IDENTICAL=%s" % all_ok)
    print("saved:", OUT)
    return 0 if all_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
