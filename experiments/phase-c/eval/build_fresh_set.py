#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""A4：生成 Phase C 的防泄漏控制集 E0（新鲜 held-out 变体，拒绝采样版）。

背景（契约 §2 / §7）
--------------------
E1 = H0 指定的零日集（`experiments/phase-b/runs/run-20261028-185512/zeroday_set.jsonl`，
40 条 held-out 变体，sha256 `432e08a2…`）。A1 不许读 E1/E0 的内容。
E0 = 用**同一批 35 条 held-out 种子**、**同一个随机种子 seed=20261029**、**同一变异档
generation_id=0** 重新生成的 40 条变体，作为"语义防线不是靠作者见过 E1 才拦得住"的防泄漏控制。

为什么是"拒绝采样"而不是"挑种子"（Lead 2026-10-28 裁决 D）
------------------------------------------------------------
Phase B 变异引擎的 payload 是 `(heldout seed text, technique, idx)` 的**确定性函数**：
随机种子只改变 `rng.shuffle` 出来的 (seed, technique, idx) 配对，不改变 payload 内容。
所以在 seed=20261029 的既定抽样序列里，前 40 条通常会有若干条与 E1 **逐字节相同**
（实测 3/40 = 7.5%）。契约要求 E0 与 E1 的 payload_sha256 零重合。

Lead 裁决：**不换 seed、不扫描种子**，而是对 seed=20261029 的**既定抽样序列**做
**污染过滤（rejection sampling）**：按抽取顺序逐条检查候选，丢弃满足任一条的
    ① `payload_sha256` 与 E1 任一 payload 相同；
    ② 与训练流 `variants_gen*.jsonl` 任一 payload 相同；
    ③ 与已选中的 E0 条目重复；
然后按抽取顺序取前 40 条。**只看字节级撞车，不看任何拦截率/判定结果，不重排、
不挑"看起来更狠"的**——这是标准污染过滤，无选择偏置。

实现约束
--------
- **不改** `tools/red_team_evolver.py`：候选直接用其内部同一生成路径
  `_core_generate(gen, seeds, n=N, rng=Random(seed))` 抽取（`build_zeroday_set` 内部就是这一行），
  选中后用其公开 API `save_variants()` 落盘。
- 抽取序列的 `idx` 只是 `hex` 形态/`zero_width` salt 的块号，不影响变异档；扩大 n 不改变
  前 40 条（rng 的 shuffle 只做一次），所以"既定抽样序列" = seed=20261029 的规范序列的延长。
- 只写 `experiments/phase-c/eval/zeroday_fresh.jsonl` 与 `zeroday_fresh.meta.json`；
  **stdout 绝不打印 payload 原文**，只打印 sha256 / 数量 / 分布。

用法
----
    python experiments/phase-c/eval/build_fresh_set.py                # 生成 + 落盘 + 自检
    python experiments/phase-c/eval/build_fresh_set.py --audit-only   # 只对已落盘文件重跑自检（只读）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime
from glob import glob
from random import Random

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__)))))

if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import red_team_evolver as rte          # noqa: E402  （只调用，不改核心逻辑）

# ── 冻结的参照物（全部只读）─────────────────────────────
CORPUS_PATH = os.path.join(ROOT, "experiments", "phase-b", "corpora", "seed_attacks.jsonl")
PHASE_B_RUN = os.path.join(ROOT, "experiments", "phase-b", "runs", "run-20261028-185512")
E1_PATH = os.path.join(PHASE_B_RUN, "zeroday_set.jsonl")
E1_SHA256 = "432e08a2d47cdc968f80803be64cf337d43767781459d4431b180f1d6f7a210c"
TRAIN_GLOB = os.path.join(PHASE_B_RUN, "variants_gen*.jsonl")
OUT_PATH = os.path.join(ROOT, "experiments", "phase-c", "eval", "zeroday_fresh.jsonl")
META_PATH = os.path.join(ROOT, "experiments", "phase-c", "eval", "zeroday_fresh.meta.json")

REQUESTED_SEED = 20261029          # Lead 裁决：**保留**契约 §7 钉死的种子
GENERATION_ID = int(rte.ZERODAY_GENERATION)   # 0：与训练/ E1 同变异档（只差随机实现）
TARGET_COUNT = int(rte.ZERODAY_COUNT)         # 40
DRAW_POOL = 4000                   # 候选池上限（正常 45 条内就够；防御性上限）
SELECTION_RULE = (
    "rejection sampling on exact payload_sha256 collision only; "
    "no label/score-based selection"
)


# ── 基础 IO ────────────────────────────────────────────

def _sha256_file(path: str) -> str:
    with open(path, "rb") as fh:
        return hashlib.sha256(fh.read()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_jsonl(path: str) -> list[dict]:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _load_heldout() -> list[dict]:
    rows = _read_jsonl(CORPUS_PATH)
    heldout = [r for r in rows if r.get("split") == "heldout"]
    train_n = sum(1 for r in rows if r.get("split") == "train")
    if len(heldout) != 35:
        raise SystemExit("[A4] 期望 35 条 heldout 种子，实际 %d 条（train=%d），拒绝继续"
                         % (len(heldout), train_n))
    return heldout


def _payload_sha_set(rows: list[dict]) -> set[str]:
    return {str(r["payload_sha256"]) for r in rows if r.get("payload_sha256")}


def _load_train_payload_sha() -> tuple[set[str], list[str]]:
    files = sorted(glob(TRAIN_GLOB))
    if not files:
        raise SystemExit("[A4] 找不到训练流 variants_gen*.jsonl：%s" % TRAIN_GLOB)
    shas: set[str] = set()
    for f in files:
        for row in _read_jsonl(f):
            if row.get("payload_sha256"):
                shas.add(str(row["payload_sha256"]))
    return shas, files


def _load_e1() -> tuple[list[dict], set[str]]:
    if not os.path.exists(E1_PATH):
        raise SystemExit("[A4] 找不到 E1：%s" % E1_PATH)
    rows = _read_jsonl(E1_PATH)
    sha = _sha256_file(E1_PATH)
    if sha != E1_SHA256:
        raise SystemExit(
            "[A4] E1 文件 sha256 与契约冻结值不符！\n  期望 %s\n  实际 %s\n"
            "拒绝继续（E1 被改过就不能做防泄漏对照）。" % (E1_SHA256, sha))
    return rows, _payload_sha_set(rows)


# ── 抽取既定序列 + 拒绝采样 ────────────────────────────

def _draw_sequence(heldout: list[dict], seed: int, n: int) -> list[dict]:
    """seed 的"既定抽样序列"：与 build_zeroday_set 内部完全相同的生成路径。"""
    if not hasattr(rte, "_core_generate"):
        raise SystemExit("[A4] red_team_evolver 缺少 _core_generate，无法复用官方生成路径")
    return rte._core_generate(GENERATION_ID, heldout, n=int(n), rng=Random(int(seed)))


def _rejection_sample(pool: list[dict], e1_shas: set[str], train_shas: set[str],
                      target: int) -> dict:
    """按抽取顺序做污染过滤；只丢逐字节撞车的候选，保留抽取顺序。"""
    selected: list[dict] = []
    selected_shas: set[str] = set()
    removed_e1 = removed_train = removed_dup = 0
    examined = 0
    first40_e1_hit = 0
    for i, cand in enumerate(pool):
        examined = i + 1
        sha = str(cand.get("payload_sha256"))
        if i < 40 and sha in e1_shas:
            first40_e1_hit += 1
        # ① 与 E1 撞车
        if sha in e1_shas:
            removed_e1 += 1
            continue
        # ② 与训练流撞车
        if sha in train_shas:
            removed_train += 1
            continue
        # ③ 与已选中的 E0 条目重复
        if sha in selected_shas:
            removed_dup += 1
            continue
        selected.append(cand)
        selected_shas.add(sha)
        if len(selected) == target:
            break
    if len(selected) < target:
        raise SystemExit(
            "[A4] 候选池 %d 条内只筛出 %d/%d 条干净候选；请扩大 DRAW_POOL"
            % (len(pool), len(selected), target))
    return {
        "selected": selected,
        "candidates_drawn": examined,
        "removed_collision_with_E1": removed_e1,
        "removed_collision_with_train": removed_train,
        "removed_internal_dup": removed_dup,
        "first40_canonical_e1_hits": first40_e1_hit,
    }


def _build_e0_rows(selected: list[dict], seed: int) -> list[dict]:
    """给选中的变体补齐零日集字段（与 E1 同 schema），并保留可审计的抽取位置。"""
    rows: list[dict] = []
    for seq, cand in enumerate(selected, start=1):
        row = dict(cand)
        row["zeroday"] = True
        row["seed_split"] = "heldout"
        row["zeroday_seed"] = int(seed)
        row["e0_selection_seq"] = seq              # 1..40，抽取顺序
        row["e0_candidate_index"] = int(cand.get("_e0_draw_index", seq - 1))
        rows.append(row)
    return rows


# ── 落盘后的完整自检 ───────────────────────────────────

def audit(rows: list[dict], heldout: list[dict], e1_rows: list[dict], e1_shas: set[str],
          train_shas: set[str], train_files: list[str], file_sha: str) -> dict:
    shas = [str(r.get("payload_sha256")) for r in rows]
    sha_ok = sum(1 for r in rows
                 if _sha256_text(str(r.get("payload"))) == str(r.get("payload_sha256")))
    heldout_ids = {str(s.get("id")) for s in heldout}
    all_seed_rows = _read_jsonl(CORPUS_PATH)
    train_ids = {str(s.get("id")) for s in all_seed_rows if s.get("split") == "train"}
    seed_ids = [str(r.get("seed_id")) for r in rows]
    technique_mix: dict[str, int] = {}
    strategy_mix: dict[str, int] = {}
    for r in rows:
        technique_mix[str(r.get("technique"))] = technique_mix.get(str(r.get("technique")), 0) + 1
        strategy_mix[str(r.get("strategy"))] = strategy_mix.get(str(r.get("strategy")), 0) + 1
    return {
        "count": len(rows),
        "sha256": file_sha,
        "internal_unique_payload_sha256": len(set(shas)),
        "internal_duplicates": len(shas) - len(set(shas)),
        "payload_sha256_selfcheck_pass": sha_ok,
        "overlap_vs_E1": sorted(set(shas) & e1_shas),
        "overlap_vs_E1_count": len(set(shas) & e1_shas),
        "overlap_vs_train_count": len(set(shas) & train_shas),
        "train_reference": {"files": len(train_files),
                            "unique_payload_sha256": len(train_shas)},
        "e1_reference": {"path": os.path.relpath(E1_PATH, ROOT).replace("\\", "/"),
                         "sha256": E1_SHA256, "count": len(e1_rows),
                         "unique_payload_sha256": len(e1_shas)},
        "seed_split": "heldout",
        "n_distinct_heldout_seeds_used": len(set(seed_ids)),
        "n_heldout_seed_pool": len(heldout_ids),
        "non_heldout_seed_ids": sorted(set(seed_ids) - heldout_ids),
        "train_seed_ids_present": sorted(set(seed_ids) & train_ids),
        "generation_ids": sorted({int(r.get("generation_id", -1)) for r in rows}),
        "all_zeroday_flag": all(bool(r.get("zeroday")) for r in rows),
        "all_seed_split_heldout": all(r.get("seed_split") == "heldout" for r in rows),
        "technique_mix": dict(sorted(technique_mix.items())),
        "strategy_mix": dict(sorted(strategy_mix.items())),
        "zero_overlap": (len(set(shas) & e1_shas) == 0 and len(set(shas) & train_shas) == 0),
        "all_pass": (len(rows) == TARGET_COUNT
                     and len(set(shas)) == TARGET_COUNT
                     and sha_ok == TARGET_COUNT
                     and not (set(shas) & e1_shas)
                     and not (set(shas) & train_shas)
                     and not (set(seed_ids) - heldout_ids)
                     and all(int(r.get("generation_id", -1)) == GENERATION_ID for r in rows)),
    }


def _print_report(meta: dict) -> None:
    c = meta["checks"]
    sel = {k: meta.get(k) for k in (
        "requested_seed", "selected_seed", "generation_id", "target_count",
        "candidates_drawn", "removed_collision_with_E1", "removed_collision_with_train",
        "removed_internal_dup", "selected_count", "collision_rate_vs_E1_at_seed")}
    sel["selected_seed"] = sel["selected_seed"] or meta.get("seed")
    print("=" * 78)
    print("[A4] E0（新鲜 held-out，拒绝采样）生成/自检报告 —— 不打印任何 payload 原文")
    print("=" * 78)
    print("文件      : %s" % meta["path"])
    print("生成时间  : %s" % meta["generated_at"])
    print("")
    print("1) sha256 = %s" % c["sha256"])
    print("   count  = %d  （目标 %d）" % (c["count"], meta.get("target_count")))
    print("2) E0 ∩ E1 payload_sha256 = %d 条  （E1: %d 条/%d 唯一, sha256=%s…）"
          % (c["overlap_vs_E1_count"], c["e1_reference"]["count"],
             c["e1_reference"]["unique_payload_sha256"], E1_SHA256[:8]))
    print("3) E0 ∩ 训练流 variants_gen* = %d 条  （训练流: %d 文件 / %d 唯一 payload_sha256）"
          % (c["overlap_vs_train_count"], c["train_reference"]["files"],
             c["train_reference"]["unique_payload_sha256"]))
    print("4) E0 内部重复 = %d 条（唯一 %d/%d）；payload sha256 自洽 %d/%d"
          % (c["internal_duplicates"], c["internal_unique_payload_sha256"], c["count"],
             c["payload_sha256_selfcheck_pass"], c["count"]))
    print("5) technique 分布: %s" % json.dumps(c["technique_mix"], ensure_ascii=False))
    print("   strategy  分布: %s" % json.dumps(c["strategy_mix"], ensure_ascii=False))
    print("   种子覆盖: %d/%d 条 heldout 种子（非 heldout 种子 %d 条）；generation_id=%s"
          % (c["n_distinct_heldout_seeds_used"], c["n_heldout_seed_pool"],
             len(c["non_heldout_seed_ids"]), c["generation_ids"]))
    print("")
    print("拒绝采样: requested_seed=%s generation_id=%s target=%s → selected=%s"
          % (sel["requested_seed"], sel["generation_id"], sel["target_count"],
             sel["selected_count"]))
    print("  候选抽取 %d 条；丢弃：∩E1=%d、∩train=%d、内部重复=%d"
          % (sel["candidates_drawn"], sel["removed_collision_with_E1"],
             sel["removed_collision_with_train"], sel["removed_internal_dup"]))
    print("  seed 处规范前 40 条的 ∩E1 命中率 = %s（%s）"
          % (sel["collision_rate_vs_E1_at_seed"],
             meta.get("collision_rate_vs_E1_at_seed_hits", "?")))
    print("")
    print("总判定: %s" % ("PASS ✅ （五项自检全过，E0 可冻结）" if c["all_pass"]
                         else "FAIL ❌ （见上，禁止冻结）"))
    print("=" * 78)


# ── meta 组装 ──────────────────────────────────────────

def _build_meta(rows: list[dict], heldout: list[dict], file_sha: str, sample: dict,
                auditor: dict) -> dict:
    removed_e1 = int(sample["removed_collision_with_E1"])
    drawn = int(sample["candidates_drawn"])
    hits40 = int(sample["first40_canonical_e1_hits"])
    return {
        "schema": "phase-c-zeroday-fresh-meta-v1",
        # —— Lead 裁决要求写进 meta 的字段（根级）——
        "requested_seed": REQUESTED_SEED,
        "generation_id": GENERATION_ID,
        "target_count": TARGET_COUNT,
        "candidates_drawn": drawn,
        "removed_collision_with_E1": removed_e1,
        "removed_collision_with_train": int(sample["removed_collision_with_train"]),
        "removed_internal_dup": int(sample["removed_internal_dup"]),
        "selected_count": len(rows),
        "selection_rule": SELECTION_RULE,
        "collision_rate_vs_E1_at_seed": round(hits40 / float(TARGET_COUNT), 6),
        "collision_rate_vs_E1_at_seed_hits": "%d/%d" % (hits40, TARGET_COUNT),
        "collision_rate_vs_E1_at_seed_measure": (
            "seed=20261029 的规范前 40 条抽取（即 build_zeroday_set(seed=20261029) 会产出的集合）中"
            "与 E1 payload_sha256 逐字节相同的条数 / 40"),
        "observed_removal_rate_in_draw": round(removed_e1 / float(drawn), 6) if drawn else 0.0,
        "selected_seed": REQUESTED_SEED,
        # —— 通用字段 ——
        "schema_note": ("E0 = held-out 变体；拒绝采样只丢与 E1/训练流逐字节撞车的候选，"
                        "按既定抽取顺序保留前 40 条，未参考任何拦截率/判定结果。"),
        "path": OUT_PATH,
        "count": len(rows),
        "sha256": file_sha,
        "seed": REQUESTED_SEED,
        "generated_at": datetime.now().astimezone().isoformat(timespec="seconds"),
        "source": {
            "corpus": os.path.relpath(CORPUS_PATH, ROOT).replace("\\", "/"),
            "seed_split": "heldout",
            "heldout_seed_count": len(heldout),
            "generator": ("tools/red_team_evolver.py::_core_generate"
                          "（build_zeroday_set 内部同一生成路径，未改动引擎）"),
            "writer": "tools/red_team_evolver.py::save_variants",
            "generator_args": {"seed": REQUESTED_SEED, "generation_id": GENERATION_ID,
                               "n_pool": DRAW_POOL,
                               "n_heldout_seeds": len(heldout),
                               "target_count": TARGET_COUNT},
            "phase_b_run": os.path.relpath(PHASE_B_RUN, ROOT).replace("\\", "/"),
            "e1": {"path": os.path.relpath(E1_PATH, ROOT).replace("\\", "/"),
                   "sha256": E1_SHA256, "count": auditor["e1_reference"]["count"]},
            "role": ("E0 = 防泄漏控制集：与 E1 同批 held-out 种子、同随机种子、同变异档，"
                     "只做了逐字节污染过滤；用途是检验 Phase C 语义防线不依赖作者对 E1 的可见性。"),
        },
        "leakage_control": {
            "payload_sha256_zero_overlap": auditor["zero_overlap"],
            "vs_E1": {"overlap_count": auditor["overlap_vs_E1_count"],
                      "e1_frozen_sha256": E1_SHA256},
            "vs_train_stream": {"overlap_count": auditor["overlap_vs_train_count"],
                                "train_reference_files": auditor["train_reference"]["files"],
                                "train_unique_payload_sha256":
                                    auditor["train_reference"]["unique_payload_sha256"]},
            "collision_rate_vs_E1_at_seed": round(hits40 / float(TARGET_COUNT), 6),
            "observed_collision_distribution_note": (
                "payload 是 (种子文本, technique, idx) 的确定性函数，换随机种子只重排配对关系、"
                "不扩展 payload 空间；seed=20261029 的规范 40 条中有 %d 条与 E1 逐字节相同，"
                "过滤后 E0 与 E1/训练流零重合。E0 不是新攻击类型、不是 OOD。"
                % hits40),
        },
        "checks": auditor,
        "visibility": "a4_only",
        "blue_team_access": False,
        "read_only_after_creation": True,
        "note": ("held-out 变体，不是 OOD（与 Phase B 同口径，契约 §11.2）；"
                 "E0 与 E1 共享同一 payload 生成函数，靠\"拒绝采样只丢逐字节撞车候选\""
                 "保证与 E1 及训练流零重合；生成后只读，sha256 冻结。"),
        "limitations": [
            "离线规则变异，不是 LLM 红队（Phase B 契约 §8.1）。",
            "E0 与 E1 是同一批 held-out 种子、同一随机种子 seed=20261029 的变体集合；"
            "E0 只做了逐字节污染过滤，**不是 OOD**，也不是新攻击类型。",
            ("payload 由确定性规则生成，随机种子只重排 (种子, technique) 配对；"
             "E0 的\"新鲜\"来自过滤掉与 E1/训练流逐字节相同的候选，不是新的攻击空间；"
             "单 seed 与 E1 的逐字节重合率约 7.5%（3/40）。"),
            "拒绝采样规则 outcome-blind：只看 payload_sha256 是否撞车，未参考任何拦截率/判定分数。",
            "可见性隔离是流程约定（A1/A5 训练路径不读 zeroday 文件），非 OS 级强制。",
            "零模型调用：api_calls/api_tokens_used 恒 0。",
        ],
    }


# ── 主流程 ─────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="A4：生成 Phase C 防泄漏控制集 E0（拒绝采样）")
    ap.add_argument("--out", default=OUT_PATH)
    ap.add_argument("--audit-only", action="store_true",
                    help="只对已落盘文件重跑自检（只读，不生成）")
    args = ap.parse_args(argv)
    out_path = os.path.abspath(args.out)

    heldout = _load_heldout()
    e1_rows, e1_shas = _load_e1()
    train_shas, train_files = _load_train_payload_sha()

    # ---- audit-only：只读复核 ----
    if args.audit_only:
        if not os.path.exists(out_path):
            raise SystemExit("[A4] --audit-only 但文件不存在：%s" % out_path)
        rows = _read_jsonl(out_path)
        meta = (json.load(open(META_PATH, encoding="utf-8"))
                if os.path.exists(META_PATH) else {})
        auditor = audit(rows, heldout, e1_rows, e1_shas, train_shas, train_files,
                        _sha256_file(out_path))
        report = {"path": out_path, "generated_at": meta.get("generated_at", "?"),
                  "target_count": meta.get("target_count"),
                  "requested_seed": meta.get("requested_seed"),
                  "generation_id": meta.get("generation_id"),
                  "candidates_drawn": meta.get("candidates_drawn"),
                  "removed_collision_with_E1": meta.get("removed_collision_with_E1"),
                  "removed_collision_with_train": meta.get("removed_collision_with_train"),
                  "removed_internal_dup": meta.get("removed_internal_dup"),
                  "selected_count": meta.get("selected_count"),
                  "collision_rate_vs_E1_at_seed": meta.get("collision_rate_vs_E1_at_seed"),
                  "collision_rate_vs_E1_at_seed_hits":
                      meta.get("collision_rate_vs_E1_at_seed_hits"),
                  "checks": auditor}
        _print_report(report)
        if meta.get("sha256") and meta["sha256"] != auditor["sha256"]:
            print("[A4] ⚠️ meta.sha256 与实测文件 sha256 不一致！")
            return 2
        return 0 if auditor["all_pass"] else 2

    # ---- 已落盘 = 冻结；不重写，改跑自检 ----
    if os.path.exists(out_path):
        print("[A4] 已存在 %s（冻结后只读，不重写）；改为重跑自检。" % out_path)
        return main(["--audit-only", "--out", out_path])

    # ---- 抽取既定序列（此时一个字节都还没落盘）----
    pool = _draw_sequence(heldout, REQUESTED_SEED, DRAW_POOL)
    # 记录每条候选在序列中的位置（供落盘后审计；不进 payload）
    sample = _rejection_sample(pool, e1_shas, train_shas, TARGET_COUNT)
    selected = sample["selected"]
    index_of = {id(c): i for i, c in enumerate(pool)}
    for cand in selected:
        cand["_e0_draw_index"] = index_of[id(cand)]
    rows = _build_e0_rows(selected, REQUESTED_SEED)
    for r in rows:
        r.pop("_e0_draw_index", None)   # 不把内部临时字段漏进文件

    # 预检（落盘前最后一道闸）
    pre_shas = [str(r["payload_sha256"]) for r in rows]
    if (len(pre_shas) != TARGET_COUNT or len(set(pre_shas)) != TARGET_COUNT
            or set(pre_shas) & e1_shas or set(pre_shas) & train_shas):
        raise SystemExit("[A4] 内部错误：拒绝采样结果不干净，拒绝落盘")

    # ---- 正式落盘（官方 save_variants，不改引擎）----
    rte.save_variants(rows, out_path)
    file_sha = _sha256_file(out_path)

    # ---- 落盘后完整自检（从磁盘读回，不信内存）----
    rows = _read_jsonl(out_path)
    auditor = audit(rows, heldout, e1_rows, e1_shas, train_shas, train_files, file_sha)
    meta = _build_meta(rows, heldout, file_sha, sample, auditor)
    with open(META_PATH, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(meta, ensure_ascii=False, indent=2) + "\n")

    # meta 写完后再核对一次文件 sha256（保证冻结值与文件一致）
    if _sha256_file(out_path) != meta["sha256"]:
        raise SystemExit("[A4] 落盘后文件 sha256 发生变化，冻结失败")

    _print_report(meta)
    print("meta      : %s" % META_PATH)
    print("checks    : %s" % ("PASS" if auditor["all_pass"] else "FAIL"))
    return 0 if auditor["all_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
