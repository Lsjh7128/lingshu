# -*- coding: utf-8 -*-
"""S4 收尾：按 Lead 指定口径（v1 变体 60 格 + 6 条探针盲评）重写交付物。

只读 calls.json / cells/，不重跑任何模型调用。
产出：
  - blind-annotation-v1.jsonl（6 条：v1 探针 t10/t20/t30 × 2 臂；仅 item_id + text）
  - blind-annotation-v2.jsonl（6 条，额外保留，v2 已在同批跑完）
  - blind-key.json（解盲映射，评分者不得接触）
  - calls.json 追加 aggregate_v1_subset / totals_v1_subset_60 / observations
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import statistics
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
S4 = os.path.join(HERE, "s4-long-session")


def main() -> int:
    path = os.path.join(S4, "calls.json")
    o = json.load(open(path, encoding="utf-8"))
    cells = o["cells"]

    # 1) 盲评文件
    all_key = {}
    blind_meta = {}
    for variant in ("v1", "v2"):
        probes = [c for c in cells if c["kind"] == "probe" and c["variant"] == variant]
        order = list(range(len(probes)))
        random.Random(20261027 + (0 if variant == "v1" else 1)).shuffle(order)
        fname = f"blind-annotation-{variant}.jsonl"
        with open(os.path.join(S4, fname), "w", encoding="utf-8", newline="\n") as f:
            for n, idx in enumerate(order):
                item_id = f"S4-{variant.upper()}-{n+1:03d}"
                f.write(json.dumps({"item_id": item_id, "text": probes[idx]["model_stdout"]},
                                   ensure_ascii=False) + "\n")
                all_key[item_id] = {"cell_id": probes[idx]["cell_id"], "variant": variant,
                                    "arm": probes[idx]["arm"], "turn": probes[idx]["turn"]}
        blind_meta[variant] = {"file": fname, "items": len(probes),
                               "sha256": hashlib.sha256(open(os.path.join(S4, fname), "rb").read()).hexdigest()}
    json.dump(all_key, open(os.path.join(S4, "blind-key.json"), "w", encoding="utf-8"),
              ensure_ascii=False, indent=1)
    o["blind"] = {"primary_v1": blind_meta["v1"], "extra_v2": blind_meta["v2"],
                  "key": "blind-key.json", "shuffle_seed": 20261027,
                  "leak_check": "jsonl 仅含 item_id/text 两个字段（无 arm/variant 字段）"}

    # 2) v1 子集（60 格）汇总
    v1 = [c for c in cells if c["variant"] == "v1"]
    lat = [c["latency_ms"] for c in v1]
    tok = [(c["usage"] or {}).get("totalTokens", 0) for c in v1]
    o["totals_v1_subset_60"] = {
        "cells": len(v1), "tokens_total": sum(tok),
        "failures": sum(1 for c in v1 if c["exit_code"] != 0),
        "retried_cells": sum(1 for c in v1 if c["attempts"] > 1),
        "latency_ms": {"min": min(lat), "median": int(statistics.median(lat)), "max": max(lat),
                       "total_s": round(sum(lat) / 1000.0, 1)},
        "steps_total": sum(c["steps"] for c in v1),
    }
    all_lat = [c["latency_ms"] for c in cells]
    o["totals"]["latency_ms"] = {"min": min(all_lat), "median": int(statistics.median(all_lat)),
                                 "max": max(all_lat), "total_s": round(sum(all_lat) / 1000.0, 1)}

    # 3) 原始观察（只报计数，不下结论）
    obs = {}
    for variant in ("v1", "v2"):
        for arm in ("full", "baseline"):
            ps = [c for c in cells if c["kind"] == "probe" and c["variant"] == variant and c["arm"] == arm]
            obs[f"{arm}-{variant}"] = {
                "probes": len(ps),
                "setting_keep": sum(p["rule"]["setting_keep"] for p in ps),
                "stale_assert": sum(p["rule"]["stale_assert"] for p in ps),
                "unknown_ack": sum(p["rule"]["unknown_ack"] for p in ps),
                "pref_keep": sum(p["rule"]["pref_keep"] for p in ps),
                "memory_injected": sum(1 for p in ps if (p.get("injection") or {}).get("has_memory_section")),
            }
    o["observations_raw_counts"] = obs

    # 4) Full 臂记忆终态（stale / 抽取失败证据）
    mem_facts = {}
    for variant in ("v1", "v2"):
        mp = os.path.join(S4, "cells", f"full-{variant}", "memory.jsonl")
        entries = []
        if os.path.isfile(mp):
            for line in open(mp, encoding="utf-8"):
                if line.strip():
                    m = json.loads(line)
                    entries.append({k: m.get(k) for k in ("id", "subject", "relation", "object", "status", "origin")})
        mem_facts[variant] = {"file": f"cells/full-{variant}/memory.jsonl",
                              "entries_lastwins": entries,
                              "beta_present": any((e.get("object") or "").upper() == "BETA" for e in entries)}
    o["full_arm_memory_evidence"] = mem_facts

    json.dump(o, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("v1_subset_60:", json.dumps(o["totals_v1_subset_60"], ensure_ascii=False))
    print("all_120:", json.dumps(o["totals"], ensure_ascii=False))
    print("obs:", json.dumps(obs, ensure_ascii=False))
    print("blind:", json.dumps(o["blind"]["primary_v1"], ensure_ascii=False),
          "| v2:", json.dumps(o["blind"]["extra_v2"], ensure_ascii=False))
    print("beta_in_full_memory:", {k: v["beta_present"] for k, v in mem_facts.items()})
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
