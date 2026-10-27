# -*- coding: utf-8 -*-
"""LINGSHU 学习机制 · 回放器（docs/13 §4）

三个函数：replay / score / attribute。契约：
- **幂等**：同一 (cfg, dataset) 两次运行，输出逐字节相同（LEARN-V1）。
- **可归因**：只改一个参数时，attribute 必须把变化归到那个 key（LEARN-V2）。
- **可复现**：不依赖墙钟、不依赖随机数、不调主模型（LEARN-V3）。
"""
from __future__ import annotations

import json
import os
from typing import Any

from . import config as cfgmod
from . import memory, metacog, onboarding
from .model import Entry, Store

BASE_TS = 1_800_000_000.0          # 固定"现在"，避免墙钟影响可复现性
TURN_STEP = 60.0                    # 每轮 60 秒


# ── 数据加载 ───────────────────────────────────────────────

def load_dataset(root: str, version: str = "v1") -> tuple[list[dict], Store]:
    turns_path = os.path.join(root, "replay-set", f"{version}.jsonl")
    memo_path = os.path.join(root, "replay-set", "fixtures", f"{version}.memories.jsonl")
    turns = [json.loads(line) for line in open(turns_path, encoding="utf-8") if line.strip()]
    turns.sort(key=lambda t: (t["session_id"], t["turn_index"]))
    store = Store()
    for line in open(memo_path, encoding="utf-8"):
        if line.strip():
            store.put(Entry.from_json(json.loads(line)))
    return turns, store


# ── replay ────────────────────────────────────────────────

def replay(cfg: dict[str, Any], root: str, version: str = "v1",
           turns: list[dict] | None = None) -> dict[str, Any]:
    """用给定配置跑一遍回放。

    `turns` 可注入"被纠正过的样本"（见 engine/proposal.py）——
    记忆库仍从 fixture 加载，只有 turn 的期望/禁止集合被改写。
    """
    ds_turns, store = load_dataset(root, version)
    turns = ds_turns if turns is None else turns
    records: list[dict[str, Any]] = []
    ob = onboarding.Onboarding(cfg) if cfg.get("replay", {}).get("applyOnboarding") else None

    for i, t in enumerate(turns):
        now = BASE_TS + i * TURN_STEP
        result = memory.recall(store, t["user_message"], now, cfg,
                               session_id=t["session_id"])
        if ob is not None:
            result = ob.filter_recall(result)
        v = metacog.verdict(result, cfg)
        records.append({
            "turn_id": t["id"],
            "session_id": t["session_id"],
            "expected_ids": t["expected_ids"],
            "forbidden_ids": t["forbidden_ids"],
            "used_ids": t.get("used_ids", []),
            "labels": t.get("labels", []),
            "expect": t.get("expect", {}),
            "returned_ids": [r["id"] for r in result["returned"]],
            "excluded_by_budget": result["excluded_by_budget"],
            "stage": ob.stage if ob is not None else None,
            "tokens_used": result["tokens_used"],
            "token_budget": result["token_budget"],
            "degraded": result["degraded"],
            "verdict": {"confidence_level": v["confidence_level"],
                        "expression": v["expression"]},
            "phrase": metacog.phrase(result, v),
        })
        if ob is not None:
            ob.tick()          # 本轮结束才累计；阶段在该轮开始时已判定

    return {"version": version, "turns": records,
            "meta": {"turns": len(records),
                     "param_version": _param_version(cfg),
                     "base_ts": BASE_TS}}


def _param_version(cfg: dict[str, Any]) -> str:
    import hashlib
    blob = json.dumps(cfgmod.flatten(cfg), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]


# ── score ─────────────────────────────────────────────────

def _mean(xs: list[float]) -> float:
    return round(sum(xs) / len(xs), 6) if xs else 0.0


def score(report: dict[str, Any]) -> dict[str, Any]:
    """把 report 折算成 docs/13 §3.1 的四组指标。纯函数，无随机性。"""
    turns = report["turns"]

    recalls: list[float] = []
    for t in turns:
        exp = set(t["expected_ids"])
        if not exp:
            continue
        got = set(t["returned_ids"])
        recalls.append(len(exp & got) / len(exp))

    false_inj: list[float] = []
    for t in turns:
        got = set(t["returned_ids"])
        forb = set(t["forbidden_ids"])
        if got:
            false_inj.append(len(got & forb) / len(got))

    used_cov: list[float] = []
    for t in turns:
        used = set(t["used_ids"])
        if used:
            used_cov.append(len(used & set(t["returned_ids"])) / len(used))

    util = [t["tokens_used"] / t["token_budget"] for t in turns]

    status_conf: list[float] = []
    for t in turns:
        if "supersede" not in t["labels"]:
            continue
        got = set(t["returned_ids"])
        ok = bool(set(t["expected_ids"]) & got) and not (set(t["forbidden_ids"]) & got)
        status_conf.append(1.0 if ok else 0.0)

    stale_leak = sum(1 for t in turns
                     if "stale" in t["labels"] and set(t["forbidden_ids"]) & set(t["returned_ids"]))

    exp_ok = sum(1 for t in turns
                 if t["expect"].get("confidence_level") == t["verdict"]["confidence_level"])
    expr_ok = sum(1 for t in turns
                  if t["expect"].get("expression") == t["verdict"]["expression"])

    return {
        "recall_at_3": _mean(recalls),
        "false_inject_rate": _mean(false_inj),
        "used_coverage": _mean(used_cov),
        "budget_utilization": _mean(util),
        "supersede_accuracy": _mean(status_conf),
        "stale_leak": stale_leak,
        "confidence_match": round(exp_ok / len(turns), 6) if turns else 0.0,
        "expression_match": round(expr_ok / len(turns), 6) if turns else 0.0,
        "turns": len(turns),
    }


# ── attribute ─────────────────────────────────────────────

def attribute(base_cfg: dict[str, Any], cand_cfg: dict[str, Any], root: str,
              version: str = "v1") -> dict[str, Any]:
    """单参数消融归因：把候选配置的每个改动单独施加回基线，看各自贡献多少。

    比"假设只有一个参数变了"更强：即使一次改了三个参数，也能拆开。
    """
    base_score = score(replay(base_cfg, root, version))
    changed = cfgmod.diff(base_cfg, cand_cfg)
    if not changed:
        return {"changed": {}, "contributions": [], "combined": base_score}

    trips: list[dict[str, Any]] = []
    for path, (_old, new) in changed.items():
        one = cfgmod.apply_path(base_cfg, path, new)
        s = score(replay(one, root, version))
        trips.append({
            "param": path,
            "value": new,
            "d_recall_at_3": round(s["recall_at_3"] - base_score["recall_at_3"], 6),
            "d_false_inject_rate": round(s["false_inject_rate"] - base_score["false_inject_rate"], 6),
            "d_budget_utilization": round(s["budget_utilization"] - base_score["budget_utilization"], 6),
            "metrics": s,
        })
    trips.sort(key=lambda x: (-abs(x["d_recall_at_3"]), x["param"]))

    return {"changed": {k: {"from": v[0], "to": v[1]} for k, v in changed.items()},
            "contributions": trips,
            "combined": score(replay(cand_cfg, root, version))}
