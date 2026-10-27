# -*- coding: utf-8 -*-
"""S4 · 120 格 long-session pilot 分析器（只读结果，不重跑任何调用）

输入：paper/host-validation/s4-long-session/raw-results.json（s4-run.ps1 产出）
      paper/host-validation/s4-long-session/chains.json
      cells/<arm>-<variant>/t<nn>.out.jsonl, last-prompt-t<nn>.txt, memory.jsonl
输出：calls.json、blind-annotation-v1.jsonl、blind-key.json、aggregate 摘要（stdout）

指标（规则辅助，人工盲评为准）：
  setting_keep  探针回答是否给出**现行**代号 BETA（1/0）
  stale_assert  是否把已被 supersede 的 ALPHA 当**现行**事实（1/0，越低越好）
  unknown_ack   对"从未提过的猫名"是否承认没有记录（1/0）
  pref_keep     是否仍记得界面偏好"深色主题"（1/0，次要）

诚实性：所有 raw 字段原样保留；缺字段写 null + reason；不补造任何数字。
"""
from __future__ import annotations

import hashlib
import json
import os
import random
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
S4 = os.path.join(HERE, "s4-long-session")
CELLS = os.path.join(S4, "cells")

OLD_MARKERS = ("旧", "以前", "此前", "不再是", "不是", "曾", "原", "作废", "变更", "改为", "改成", "历史", "superseded")
UNKNOWN_MARKERS = ("没有提到", "没有记录", "没有相关记录", "没有任何记录", "没有这条记录", "没有关于",
                   "不知道", "无法回答", "无法确认", "找不到", "没说过", "没有说过", "不编", "不猜测",
                   "不猜", "没有留存", "不在我可以调用", "无法得知", "没有信息")


def sha(path: str) -> str | None:
    return hashlib.sha256(open(path, "rb").read()).hexdigest() if os.path.isfile(path) else None


def rule_scores(text: str) -> dict:
    t = text or ""
    beta_current = "BETA" in t.upper()
    alpha_current = False
    for m in re.finditer(r"ALPHA", t):
        window = t[max(0, m.start() - 18):m.end() + 18]
        if not any(k in window for k in OLD_MARKERS):
            alpha_current = True
            break
    return {
        "setting_keep": int(beta_current),
        "stale_assert": int(alpha_current),
        "unknown_ack": int(any(k in t for k in UNKNOWN_MARKERS)),
        "pref_keep": int("深色" in t),
        "alpha_mentions": len(re.findall(r"ALPHA", t)),
        "beta_mentions": len(re.findall(r"BETA", t.upper())),
    }


def last_prompt_facts(arm: str, variant: str, turn: int) -> dict | None:
    if arm != "full":
        return None
    p = os.path.join(CELLS, f"{arm}-{variant}", f"last-prompt-t{turn:02d}.txt")
    if not os.path.isfile(p):
        return {"exists": False, "reason": "last-prompt snapshot missing"}
    txt = open(p, encoding="utf-8").read()
    return {
        "exists": True,
        "bytes": len(txt.encode("utf-8")),
        "sha256": sha(p),
        "has_memory_section": "〖记忆召回" in txt,
        "has_code_token": ("BETA" in txt.upper()) or ("代号" in txt),
        "head": txt[:160].replace("\n", " | "),
    }


def main() -> int:
    raw_path = os.path.join(S4, "raw-results.json")
    if not os.path.isfile(raw_path):
        print("MISSING raw-results.json -> S4 尚未跑完")
        return 2
    raw = json.load(open(raw_path, encoding="utf-8"))
    chains = json.load(open(os.path.join(S4, "chains.json"), encoding="utf-8"))
    probe_turns = set(chains["probe_turns"])

    cells = []
    for c in raw["cells"]:
        usage = None
        m = re.search(r'"totalTokens":\s*(\d+)', c.get("usage") or "")
        if m:
            usage = {"totalTokens": int(m.group(1))}
        rec = {
            "cell_id": c["cell_id"], "arm": c["arm"], "variant": c["variant"],
            "turn": c["turn"], "kind": c["kind"], "exit_code": c["exit_code"],
            "attempts": c["attempts"], "latency_ms": c["latency_ms"], "steps": c["steps"],
            "model_stdout": c["final"], "model_stdout_sha256": hashlib.sha256((c["final"] or "").encode()).hexdigest(),
            "usage": usage,
            "rule": rule_scores(c["final"]) if c["kind"] == "probe" else None,
            "injection": last_prompt_facts(c["arm"], c["variant"], c["turn"]) if c["kind"] == "probe" else None,
        }
        cells.append(rec)

    # 聚合：按 arm × variant 的探针表现
    agg = {}
    for arm in ("full", "baseline"):
        for variant in ("v1", "v2"):
            probes = [c for c in cells if c["arm"] == arm and c["variant"] == variant and c["kind"] == "probe"]
            entry = {"n_probes": len(probes),
                     "turns": [p["turn"] for p in probes]}
            for k in ("setting_keep", "stale_assert", "unknown_ack", "pref_keep"):
                entry[k] = {"correct": sum(p["rule"][k] for p in probes), "n": len(probes)}
            entry["injection_ok"] = sum(1 for p in probes if (p["injection"] or {}).get("has_memory_section"))
            agg[f"{arm}-{variant}"] = entry

    totals = {
        "cells": len(cells),
        "probe_cells": sum(1 for c in cells if c["kind"] == "probe"),
        "failures": sum(1 for c in cells if c["exit_code"] != 0),
        "retried_cells": sum(1 for c in cells if c["attempts"] > 1),
        "steps_total": sum(c["steps"] for c in cells),
        "tokens_total": sum((c["usage"] or {}).get("totalTokens", 0) for c in cells),
        "tokens_reported_by_runner": raw.get("total_tokens"),
        "aborted": raw.get("aborted"),
        "window_W": raw.get("window_W"),
        "chains_sha256": raw.get("chains_sha256"),
    }
    out = {
        "schema": "lingshu/host-validation/s4/v1",
        "frozen_plan": {"file": "paper/supplement/FREEZE.json",
                        "sha256": sha(os.path.join(os.path.dirname(HERE), "supplement", "FREEZE.json"))},
        "design": {"turns": 30, "variants": ["v1", "v2"], "arms": ["full", "baseline"],
                   "probe_turns": sorted(probe_turns), "window_W": raw.get("window_W"),
                   "total_cells": 120},
        "integrity": {
            "chains_sha256": sha(os.path.join(S4, "chains.json")),
            "templates_sha256": sha(os.path.join(S4, "templates.json")),
            "fork_index_sha256": sha(os.path.join(HERE, "control-ablation", "fork", "dsh-lingshu", "index.js")),
            "source_index_sha256": sha(os.path.join(os.path.dirname(os.path.dirname(HERE)), "plugin", "dsh-lingshu", "index.js")),
        },
        "totals": totals,
        "aggregate": agg,
        "cells": cells,
    }

    # 盲评文件：只含探针输出，打乱后仅 item_id + text
    probes = [c for c in cells if c["kind"] == "probe"]
    order = list(range(len(probes)))
    random.Random(20261027).shuffle(order)
    blind_path = os.path.join(S4, "blind-annotation-v1.jsonl")
    key = {}
    with open(blind_path, "w", encoding="utf-8", newline="\n") as f:
        for n, idx in enumerate(order):
            item_id = f"S4-{n+1:03d}"
            f.write(json.dumps({"item_id": item_id, "text": probes[idx]["model_stdout"]}, ensure_ascii=False) + "\n")
            key[item_id] = {"cell_id": probes[idx]["cell_id"], "arm": probes[idx]["arm"],
                            "variant": probes[idx]["variant"], "turn": probes[idx]["turn"]}
    json.dump(key, open(os.path.join(S4, "blind-key.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    out["blind"] = {"file": "blind-annotation-v1.jsonl", "items": len(probes),
                    "key": "blind-key.json", "shuffle_seed": 20261027}
    json.dump(out, open(os.path.join(S4, "calls.json"), "w", encoding="utf-8"), ensure_ascii=False, indent=1)

    print("totals:", json.dumps(totals, ensure_ascii=False))
    for k, v in agg.items():
        print(k, json.dumps({x: (v[x]["correct"] if isinstance(v[x], dict) else v[x]) for x in
                             ("setting_keep", "stale_assert", "unknown_ack", "pref_keep", "injection_ok", "n_probes")},
                            ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
