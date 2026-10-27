# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 真实标定分析工具（tools/analyze_shadow.py）

    python tests/test_shadow_analysis.py

原则（与仓库其它验收一致，R-23）：**直接调用被测实现，不复制业务逻辑**；
断言必须在实现退化时失败。覆盖：

- JSONL 读取：BOM、空行、非法行、非对象行；缺失文件不炸
- 分位数：空集、单值、插值口径
- turn 归属：以 turn_begin.turns 为轮次（pre_step.turn 实测恒为 1，不能当轮次）；
  后续 step 不重复计入轮级统计；早于 turn_begin 的 pre_step 进"未归属"桶
- 相变统计：ratio 分布 / 双阈值越界 / 边界样本 / phase 与 ratio 重算一致性 /
  anchor 计数 / trend 推导（turn 断点不跨空洞）
- STPA：verdict 分布、UCA 计数、block/downgrade 事件级计数、全 allow 不等于"熔断有效"
- 记忆：status/layer/origin 分布、engine potency（双通道）、插件侧纯衰减弱对照、
  supersede/stale 事件缺通道时如实报 0 而不是"没有冲突"
- 影子：agent_state / goal / relation 计数、apply.shadow 开关解析、旧日志不可判定
- 标定门槛：样本不足 → guessed 不升级；分布齐备但缺结果标签 → 仍不能升级；
  全部门槛达标才 upgrade_eligible（证明门槛机检真的会拦截/放行，不是永远 false）
- 输出协议：空目录 exit 0 且报告含"样本不足"；拒绝写插件数据目录；
  只读（不修改输入文件）；默认产出 provenance-candidates.json
"""

from __future__ import annotations

import json
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import analyze_shadow as az  # noqa: E402

ASOF_MS = 1_800_000_000_000          # 固定数据时点：不依赖墙钟，跑多少次结果一样
DAY_MS = 86_400_000
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail else ""))


# ── 样本构造 ──────────────────────────────────────────────

def _write_jsonl(path: str, rows: list) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def _write_json(path: str, obj: dict) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def _pre_step(turn: int, step: int, first: bool, *, ratio, phase, anchor,
              verdict, ucas=None, downgrades=None, ts: int = 0) -> dict:
    rec = {"ts": ts, "kind": "pre_step", "turn": 1, "step": step, "firstStep": first,
           "stage": "warm", "chars": 8}
    if ratio is not None:
        rec.update({"ratio": ratio, "phase": phase, "anchor": anchor,
                    "verdict": verdict, "ucas": list(ucas or []),
                    "downgrades": list(downgrades or []),
                    "blocked": verdict == "block", "matched": 0})
    return rec


def _quality_label(*, kind_label="wrong", quote="不对", turn=3, ratio=0.42,
                   phase="normal", anchor=False, recall_ids=None,
                   source="user_message_rule", ts: int = ASOF_MS - 1500) -> dict:
    """构造一条插件 audit 的 quality_label 事件（decisions/012 §2.7 的闭集标签）。"""
    return {"ts": ts, "kind": "quality_label", "kind_label": kind_label, "quote": quote,
            "turn": turn, "phase": {"ratio": ratio, "phase": phase, "anchor": anchor},
            "recall_ids": list(recall_ids or []), "source": source}


def build_fixture(tmp: str, *, ratios: list[float] | None = None,
                  corrections_n: int = 0, window: int | None = 1_048_576,
                  with_shadow: bool = True,
                  quality_labels: list[dict] | None = None) -> str:
    """构造一份"像真实插件日志"的最小样本；返回数据目录。"""
    data = os.path.join(tmp, "plugins", "dsh-lingshu")
    ratios = ratios if ratios is not None else [0.10, 0.50, 0.69, 0.70, 0.86, 0.71]
    audit: list[dict] = [{
        "ts": ASOF_MS - 100_000, "kind": "apply", "sections": 7, "toolOk": True,
        "configKeys": ["control", "shadowGoal", "shadowRelation"],
        "hooks": {"agent/pre-step": "ok"}, "frozenOk": True,
    }]
    if with_shadow:
        audit[0]["shadow"] = {"goal": True, "relation": True}
    phases = []
    anchors = []
    for r in ratios:
        if r >= 0.85:
            phases.append("critical")
            anchors.append(True)
        elif r >= 0.70:
            phases.append("shift")
            anchors.append(r >= 0.71)              # 只让一个 shift 行 anchor=true
        else:
            phases.append("normal")
            anchors.append(False)

    verdicts = ["allow"] * len(ratios)
    ucas_rows = [None] * len(ratios)
    if len(ratios) >= 2:                            # 第 2 轮造一次真实熔断 + 一次降级
        verdicts[1] = "block"
        ucas_rows[1] = ["UCA-01"]
    downgrade_rows = [None] * len(ratios)
    if len(ratios) >= 5:
        downgrade_rows[4] = ["UCA-08"]

    for i, r in enumerate(ratios, start=1):
        audit.append({"ts": ASOF_MS - 100_000 + i * 1000, "kind": "turn_begin",
                      "turns": i, "stage": "cold" if i <= 2 else "warm"})
        audit.append(_pre_step(i, 1, True, ratio=r, phase=phases[i - 1],
                               anchor=anchors[i - 1], verdict=verdicts[i - 1],
                               ucas=ucas_rows[i - 1], downgrades=downgrade_rows[i - 1],
                               ts=ASOF_MS - 100_000 + i * 1000 + 10))
        # 同一轮的第 2 个 step：控制字段沿用首 step；不应重复计成新观测
        audit.append(_pre_step(i, 2, False, ratio=None, phase=None, anchor=None,
                               verdict=None, ts=ASOF_MS - 100_000 + i * 1000 + 20))
    # legacy 行：旧版插件没有控制字段 → 未观测，不是 0
    audit.append({"ts": ASOF_MS - 5000, "kind": "turn_begin", "turns": len(ratios) + 1,
                  "stage": "warm"})
    audit.append(_pre_step(len(ratios) + 1, 1, True, ratio=None, phase=None, anchor=None,
                           verdict=None, ts=ASOF_MS - 4900))
    audit.append({"ts": ASOF_MS - 4000, "kind": "preflight_block",
                  "ucas": ["UCA-01"], "explain": ["x"]})
    audit.append({"ts": ASOF_MS - 3000, "kind": "preflight_downgrade",
                  "downgrades": ["UCA-08"], "explain": ["y"]})
    audit.append({"ts": ASOF_MS - 2000, "kind": "memory_write", "written": 2,
                  "ids": ["mem_001", "mem_002"], "rejected": ["bad_type:临时记录"], "turn": 1})
    audit.append({"ts": ASOF_MS - 1900, "kind": "agent_state", "turn": 1,
                  "goalEvents": ["new", "progress"], "relationEvent": "positive",
                  "trust": "low", "conflict": False, "modifier": 0.9})
    audit.append({"ts": ASOF_MS - 1800, "kind": "agent_state", "turn": 2,
                  "goalEvents": [], "relationEvent": "positive",
                  "trust": "low", "conflict": False, "modifier": 0.9})
    audit.append({"ts": ASOF_MS, "kind": "turn_end_seen", "turns": len(ratios) + 1})
    # 规则自动标签事件（decisions/012 §2.7）：只统计、不进入判定；缺省为旧日志（无标签）。
    for i, ev in enumerate(quality_labels or []):
        rec = dict(ev)
        rec.setdefault("ts", ASOF_MS - 2000 - i)
        audit.append(rec)

    memory = [
        {"id": "mem_001", "type": "procedural", "subject": "目标", "relation": "goal_001",
         "object": "new", "content": "目标 goal_001 发生 new 事件", "importance": 0.8,
         "confidence": 0.9, "layer": "warm", "status": "active",
         "sourceQuote": "开始做验收", "sourceTrust": "user_explicit", "origin": "goal",
         "turn": 1, "lastAccess": ASOF_MS - DAY_MS, "tauDays": 30},
        {"id": "mem_002", "type": "episodic", "subject": "关系", "relation": "new",
         "object": "positive", "content": "关系事件：positive", "importance": 0.5,
         "confidence": 0.9, "layer": "warm", "status": "superseded",
         "sourceQuote": "谢谢", "sourceTrust": "user_explicit", "origin": "relation",
         "turn": 2, "lastAccess": ASOF_MS - 400 * DAY_MS, "tauDays": 30},
        {"id": "mem_bad", "type": "semantic", "subject": "坏", "relation": "r",
         "object": "o", "content": "importance 非法", "importance": "oops",
         "confidence": 0.9, "layer": "warm", "status": "active", "origin": "tool",
         "turn": 3, "lastAccess": ASOF_MS - DAY_MS, "tauDays": 30},
    ]
    state = {
        "sessions": 2, "turns": len(ratios), "stateWrites": 2,
        "coldStartSuppressed": 1,
        "control": {"phase": {"ratio": ratios[-1], "phase": phases[-1], "trend": 0.01,
                              "prefeed": phases[-1] != "normal",
                              "selfReferenceBoost": phases[-1] != "normal",
                              "anchorRequired": anchors[-1]},
                    "audit": {"verdict": "allow", "ucas": [], "downgrades": []},
                    "blocked": False,
                    "contextWindow": window},
        "goal": {"order": ["goal_001"], "events": [{"event": "new"}]},
        "relation": {"trustLevel": "low", "stage": "new", "positives": 3},
    }
    _write_jsonl(os.path.join(data, "audit.jsonl"), audit)
    _write_jsonl(os.path.join(data, "memory.jsonl"), memory)
    _write_json(os.path.join(data, "state.json"), state)
    if corrections_n:
        _write_jsonl(os.path.join(data, "corrections.jsonl"),
                     [{"correction_id": "cor_%03d" % i, "kind": "wrong"} for i in range(corrections_n)])
    return data


# ── 1. 基础读取 / 分位数 ───────────────────────────────────

def test_read_jsonl() -> None:
    print("\n[读取] BOM/空行/非法行/非对象行；缺失文件")
    with tempfile.TemporaryDirectory() as tmp:
        p = os.path.join(tmp, "audit.jsonl")
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            f.write("\ufeff" + json.dumps({"kind": "apply"}, ensure_ascii=False) + "\n")
            f.write("\n")
            f.write("这不是 JSON\n")
            f.write("[1, 2, 3]\n")                       # 合法 JSON 但不是对象
            f.write(json.dumps({"kind": "turn_begin", "turns": 1}) + "\n")
        rows, info = az.read_jsonl(p)
        check("read_jsonl 跳过非法/非对象行，保留合法对象", len(rows) == 2, info)
        check("read_jsonl 记录 skipped 数", info["skipped"] == 2, info["skipped"])
        check("read_jsonl 容忍 BOM 首行", rows[0].get("kind") == "apply")

        rows2, info2 = az.read_jsonl(os.path.join(tmp, "missing.jsonl"))
        check("缺失文件返回空表且 exists=False", rows2 == [] and info2["exists"] is False)

        st, sinfo = az.read_json(os.path.join(tmp, "state.json"))
        check("缺失 state.json 返回空 dict 不抛", st == {} and sinfo["exists"] is False)

        with open(os.path.join(tmp, "bad.json"), "w", encoding="utf-8") as f:
            f.write("{not json")
        st2, sinfo2 = az.read_json(os.path.join(tmp, "bad.json"))
        check("坏 state.json 返回空 dict 并记录错误", st2 == {} and sinfo2["error"])


def test_percentile() -> None:
    print("\n[分位数] 空集/单值/线性插值")
    check("空集 → None", az.percentile([], 50) is None)
    check("单值 → 该值", az.percentile([0.42], 90) == 0.42)
    check("p50 插值 = 2.5", abs(az.percentile([1, 2, 3, 4], 50) - 2.5) < 1e-9,
          az.percentile([1, 2, 3, 4], 50))
    check("p90(1..10) = 9.1", abs(az.percentile(list(range(1, 11)), 90) - 9.1) < 1e-9)
    check("p0/p100 取端点", az.percentile([3, 1, 2], 0) == 1 and az.percentile([3, 1, 2], 100) == 3)


# ── 2. turn 归属 ─────────────────────────────────────────

def test_turn_attribution() -> None:
    print("\n[轮次] turn_begin 计轮；后续 step 不重复；未归属桶")
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp)
        res = az.analyze(data)
        s = res["sample"]
        check("turn_begin 事件 = 7（6 个观测轮 + 1 个 legacy 轮）", s["turn_begin_events"] == 7,
              s["turn_begin_events"])
        check("pre_step 总条数含每轮第 2 个 step", s["steps"] == 6 * 2 + 1, s["steps"])
        check("轮级观测只吃 firstStep（6 个），不受多 step 影响", s["phase_observed"] == 6,
              s["phase_observed"])
        check("legacy 轮单列，不算 0 观测", s["phase_legacy_turns"] == 1, s["phase_legacy_turns"])
        check("state.turns 优先作为规模口径", s["turns"] == 6, s["turns"])

        audit = [{"kind": "pre_step", "firstStep": True, "ratio": 0.1, "phase": "normal",
                  "anchor": False, "verdict": "allow", "step": 1}]
        turns, steps = az.build_turns(audit)
        check("turn_begin 之前的 pre_step 进未归属桶（turn=0）",
              turns[0].get("unattributed") is True and turns[0]["turn"] == 0 and steps == 1)


# ── 3. 相变统计 ─────────────────────────────────────────

def test_phase_stats() -> None:
    print("\n[相变] 分布/越界/边界/anchor/完整性/trend")
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp, ratios=[0.10, 0.50, 0.69, 0.70, 0.86, 0.71])
        ph = az.analyze(data)["phase"]
        check("observed=6", ph["observed_n"] == 6)
        check("p50=0.695（插值）", abs(ph["p50"] - 0.695) < 1e-9, ph["p50"])
        check("p90=0.785", abs(ph["p90"] - 0.785) < 1e-9, ph["p90"])
        check("max=0.86 / min=0.10", ph["max"] == 0.86 and ph["min"] == 0.10)
        check("≥0.70 越界 3 次（0.70/0.86/0.71）", ph["crossings_shift"] == 3, ph["crossings_shift"])
        check("≥0.85 越界 1 次", ph["crossings_hard"] == 1, ph["crossings_hard"])
        check("边界样本 0.60~0.80 = 3", ph["boundary_shift"] == 3, ph["boundary_shift"])
        check("边界样本 0.80~0.90 = 1", ph["boundary_hard"] == 1, ph["boundary_hard"])
        check("phase 分布 shift=2 critical=1 normal=3",
              ph["phase_counts"] == {"shift": 2, "critical": 1, "normal": 3}, ph["phase_counts"])
        check("phase 与 ratio 重算一致（0 处不一致）", ph["phase_mismatches"] == [],
              ph["phase_mismatches"])
        check("anchor=true 共 2 次（1 shift + 1 critical）",
              ph["anchor_true_n"] == 2 and ph["anchor_shift_n"] == 1
              and ph["anchor_critical_n"] == 1)
        check("shift 触发 turn 列表 = [4, 6]", ph["shift_turns"] == [4, 6], ph["shift_turns"])
        check("曲线点数 = 观测数", len(ph["curve"]) == 6)

        tr = ph["trend"]
        check("trend 样本 = 4（6 个连续观测）", tr["sample_n"] == 4, tr["sample_n"])
        check("trend 由 ratio 推导（当前无日志字段）", "推导" in tr["source"], tr["source"])
        check("trend 触发锚点 1 次（turn6 shift 且 anchor=true）", tr["triggered_n"] == 1,
              tr["triggered"])
        check("trend 靠近门槛样本=0", tr["near_threshold"] == 0)

    # turn 不连续 → 不跨空洞算趋势
    with tempfile.TemporaryDirectory() as tmp:
        audit = []
        for turn, ratio in [(1, 0.1), (2, 0.2), (4, 0.4), (5, 0.5), (6, 0.6)]:
            audit.append({"kind": "turn_begin", "turns": turn, "stage": "warm"})
            audit.append(_pre_step(turn, 1, True, ratio=ratio, phase="normal",
                                   anchor=False, verdict="allow",
                                   ts=ASOF_MS - 10_000 + turn * 100))
        turns, _ = az.build_turns(audit)
        ph = az.summarize_phase(turns, az.control_defaults()[0])
        check("turn 断点把序列切成 2 段", ph["trend"]["runs"] == 2, ph["trend"]["runs"])
        check("跨空洞不产生假 trend（短段 0 + 长段 1 = 1 个样本）",
              ph["trend"]["sample_n"] == 1, ph["trend"]["sample_n"])


# ── 4. STPA ─────────────────────────────────────────────

def test_stpa_stats() -> None:
    print("\n[STPA] verdict/UCA/事件级/全 allow 不等于有效")
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp)
        st = az.analyze(data)["stpa"]
        check("轮级观测 = 6", st["observed_n"] == 6, st["observed_n"])
        check("verdict 分布 allow=5 block=1", st["verdicts"] == {"allow": 5, "block": 1},
              st["verdicts"])
        check("UCA-01 轮级命中 1 次", st["ucas"].get("UCA-01") == 1, st["ucas"])
        check("熔断率 1/6 且非 100%", abs(st["block_rate"] - 1 / 6) < 1e-9 and not st["all_block"])
        check("block 事件级统计独立于轮级", st["block_events"]["n"] == 1
              and st["block_events"]["ucas"].get("UCA-01") == 1)
        check("UCA-08 降级留痕 = 轮级 1 + 事件级 1 = 2",
              st["downgrades"].get("UCA-08") == 1
              and st["downgrade_events"]["ids"].get("UCA-08") == 1)
        check("legacy 行不计入观测", st["legacy_n"] == 1, st["legacy_n"])

    # 全 allow + 零越狱尝试：不能推出"100% 熔断有效"
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp, ratios=[0.1, 0.2, 0.3])
        st = az.analyze(data)["stpa"]
        check("全 allow 时 all_block=False", st["all_block"] is False)


# ── 5. 记忆 / potency ────────────────────────────────────

def test_memory_stats() -> None:
    print("\n[记忆] 结构分布/potency/插件对照/冲突通道")
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp)
        mem = az.analyze(data)["memory"]
        check("条目数 = 3", mem["entries"] == 3, mem["entries"])
        check("status 分布包含 superseded", mem["status"].get("superseded") == 1,
              mem["status"])
        check("非 active 条目单列", mem["non_active"] == {"superseded": 1}, mem["non_active"])
        check("origin 分布 goal/relation/tool", mem["origin"] ==
              {"goal": 1, "relation": 1, "tool": 1}, mem["origin"])
        check("memory_write 写入/拒绝计数", mem["writes"]["written"] == 2
              and mem["writes"]["rejected"] == 1, mem["writes"])
        check("拒绝原因归类", "bad_type" in mem["writes"]["reject_reasons"],
              mem["writes"]["reject_reasons"])

        pot = mem["potency"]
        check("engine potency 可用", pot["available"] is True)
        check("坏 importance 行不拖垮整体（错误被记录）", len(pot["errors"]) == 1, pot["errors"])
        check("可算 potency 的条目 = 2", len(pot["rows"]) == 2, len(pot["rows"]))
        by_id = {r["id"]: r for r in pot["rows"]}
        check("新条目 potency 高（≥归档门 0.15）",
              by_id["mem_001"]["potency"] >= 0.15
              and by_id["mem_001"]["above_archive"] is True, by_id["mem_001"])
        check("400 天旧条目 potency 低于归档门",
              by_id["mem_002"]["potency"] < 0.15
              and by_id["mem_002"]["above_archive"] is False, by_id["mem_002"])
        check("potency 均落在 [0,1]", all(0.0 <= r["potency"] <= 1.0 for r in pot["rows"]))

        plugin = {r["id"]: r for r in mem["plugin_decay"]["rows"]}
        check("插件侧：superseded 条目 potency 记 None（不参与召回）",
              plugin["mem_002"]["potency"] is None, plugin["mem_002"])
        check("插件侧新条目 potency 为正", (plugin["mem_001"]["potency"] or 0) > 0)

        check("无 supersede 审计通道 → 事件数 0（只能说明没通道）",
              mem["conflict_events"]["n"] == 0)


# ── 6. 影子模块 ─────────────────────────────────────────

def test_shadow_stats() -> None:
    print("\n[影子] agent_state / goal / relation / 开关可判定性")
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp)
        sh = az.analyze(data)["shadow"]
        check("agent_state 事件 = 2", sh["agent_state_events"] == 2)
        check("goal events 计数", sh["goal_events"] == {"new": 1, "progress": 1}, sh["goal_events"])
        check("relation events 计数", sh["relation_events"] == {"positive": 2},
              sh["relation_events"])
        check("trust 分布", sh["trust"] == {"low": 2}, sh["trust"])
        check("modifier 记录 (n=2, min=max=0.9)",
              sh["modifier"]["n"] == 2 and sh["modifier"]["min"] == 0.9)
        check("apply.shadow 已解析", sh["shadow_observable"] is True
              and sh["shadow_flags"] == {"goal": True, "relation": True}, sh["shadow_flags"])
        check("控制论层启用时间来自 apply.configKeys", sh["control_layer_observed"] is True)

    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp, with_shadow=False)
        sh = az.analyze(data)["shadow"]
        check("旧版 apply 无 shadow 字段 → 日志不可判定（不是默认开/关）",
              sh["shadow_observable"] is False and sh["shadow_flags"] is None)


# ── 6b. 规则自动结果标签（辅助信号：只统计、不入门槛）────────

def test_quality_label_stats() -> None:
    print("\n[结果标签] 闭集计数 / ratio 桶 / source / 明细 / 坏数据容忍")
    labels = [
        _quality_label(kind_label="wrong", quote="不对", turn=3, ratio=0.42,
                       phase="normal", recall_ids=["mem_001"]),
        _quality_label(kind_label="missing", quote="漏了", turn=4, ratio=0.72,
                       phase="shift", recall_ids=["mem_002"]),
        _quality_label(kind_label="should_not_recall", quote="别提", turn=5, ratio=0.90,
                       phase="critical", anchor=True),
        _quality_label(kind_label="drift", quote="又来了", turn=6, ratio=0.69),
        _quality_label(kind_label="overreach", quote="你凭什么", turn=6, ratio=None,
                       phase=None, anchor=None),
        _quality_label(kind_label="oops", quote="？", turn=7, ratio="0.80"),
        _quality_label(kind_label=None, quote="？", turn=7, ratio=0.75),
        _quality_label(kind_label="wrong", quote="不对", turn=8, ratio=0.10),
        {"ts": ASOF_MS - 500, "kind": "quality_label", "kind_label": "wrong",
         "quote": "非 dict phase", "turn": 9, "phase": ["not", "dict"],
         "recall_ids": [], "source": "user_message_rule"},
        {"ts": ASOF_MS - 400, "kind": "quality_label", "kind_label": "wrong",
         "quote": "缺 source", "turn": 10,
         "phase": {"ratio": 0.10, "phase": "normal", "anchor": False}},
    ]
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp, quality_labels=labels)
        res = az.analyze(data)
        ql = res["quality_labels"]
        check("标签事件总数 = 10（含坏标签/坏 phase）", ql["total"] == 10, ql["total"])
        check("by_kind 键恰为五类闭集", set(ql["by_kind"]) == set(az.QUALITY_LABEL_KINDS)
              and len(ql["by_kind"]) == 5, ql["by_kind"])
        check("五类闭集计数（wrong=4, missing=1, should_not_recall=1, drift=1, overreach=1）",
              ql["by_kind"] == {"wrong": 4, "missing": 1, "should_not_recall": 1,
                                "drift": 1, "overreach": 1}, ql["by_kind"])
        check("闭集外 kind_label 单列 unknown_kind=1（不静默合并）",
              ql["unknown_kind"] == 1, ql["unknown_kind"])
        check("kind_label 缺失单列 invalid=1（坏事件不猜）", ql["invalid"] == 1, ql["invalid"])
        check("recognized=8 且 recognized+unknown+invalid=total",
              ql["recognized"] == 8
              and ql["recognized"] + ql["unknown_kind"] + ql["invalid"] == ql["total"])
        check("ratio 桶计数 <0.70=4 / 0.70-0.85=2 / >=0.85=1 / unknown=3",
              {b: v["labels"] for b, v in ql["ratio_buckets"].items()} ==
              {"<0.70": 4, "0.70-0.85": 2, ">=0.85": 1, "unknown": 3},
              {b: v["labels"] for b, v in ql["ratio_buckets"].items()})
        check("桶计数合计 = total（无事件丢失）",
              sum(v["labels"] for v in ql["ratio_buckets"].values()) == ql["total"])
        check("有 ratio 观测的 turn = 6（来自 pre_step）", ql["observed_turns"] == 6,
              ql["observed_turns"])
        check("总标签率 = 100×10/6 = 166.67", abs(ql["per_100_observed_turns"] - 166.67) < 1e-9,
              ql["per_100_observed_turns"])
        check("该桶观测 turn 数 <0.70=3 / 0.70-0.85=2 / >=0.85=1 / unknown=0",
              {b: v["observed_turns"] for b, v in ql["ratio_buckets"].items()} ==
              {"<0.70": 3, "0.70-0.85": 2, ">=0.85": 1, "unknown": 0})
        check("分桶标签率：<0.70 133.33 / 0.70-0.85 100.0 / >=0.85 100.0 / unknown None",
              abs(ql["ratio_buckets"]["<0.70"]["per_100_observed_turns"] - 133.33) < 1e-9
              and ql["ratio_buckets"]["0.70-0.85"]["per_100_observed_turns"] == 100.0
              and ql["ratio_buckets"][">=0.85"]["per_100_observed_turns"] == 100.0
              and ql["ratio_buckets"]["unknown"]["per_100_observed_turns"] is None)
        check("source 分布（缺 source 计 unknown）",
              ql["by_source"] == {"user_message_rule": 9, "unknown": 1}, ql["by_source"])
        check("带 recall_ids 的事件 = 2", ql["recall_ids_events"] == 2, ql["recall_ids_events"])
        e0 = ql["events"][0]
        check("明细解析：quote/turn/ratio/phase/recall_ids 逐字段",
              e0["quote"] == "不对" and e0["turn"] == 3 and e0["ratio"] == 0.42
              and e0["phase"] == "normal" and e0["recall_ids"] == ["mem_001"], e0)
        check("明细解析：ratio 缺失 → None（不填 0）",
              ql["events"][4]["ratio"] is None and ql["events"][4]["phase"] is None)
        check("明细解析：缺 source → None（不猜）", ql["events"][9]["source"] is None)
        check("sample 暴露标签事件数（规模口径）", res["sample"]["quality_label_events"] == 10,
              res["sample"]["quality_label_events"])
        check("audit_kinds 统计到 quality_label=10", res["audit_kinds"].get("quality_label") == 10,
              res["audit_kinds"])
        report = az.render_report(res, None)
        check("报告含 §2.6 结果标签统计小节", "结果标签统计" in report)
        check("报告写明「规则自动标签 ≠ 人工结果标签」",
              "规则自动标签 ≠ 人工结果标签" in report)
        check("报告写明不改变 upgrade_eligible",
              "不改变" in report and "upgrade_eligible" in report)
        check("报告含「每 100 个有 ratio 观测 turn 的标签率」",
              "每 100 个有 ratio 观测 turn 的标签率" in report)
        check("报告展示 source 分布", "source 分布" in report)
        cand = az.build_candidates(res)
        blk = cand["quality_labels"]
        check("候选 JSON 追加 quality_labels 辅助块且标 auxiliary_only",
              blk["auxiliary_only"] is True and blk["total"] == 10
              and blk["by_kind"]["wrong"] == 4, blk)
        check("辅助块不进入 summary/params（升级判定口径不变）",
              cand["summary"]["measured_count_after_run"] == 0
              and len(cand["params"]) == 3
              and all("quality_labels" not in p["evidence"] for p in cand["params"]))

    # 插件占位值（index.js）：尚无相变观测时写 ratio=0 / phase="unknown"
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp, quality_labels=[
            _quality_label(kind_label="wrong", quote="不对", turn=1, ratio=0,
                           phase="unknown")])
        ql0 = az.analyze(data)["quality_labels"]
        check("phase=unknown 的占位标签落 unknown 桶（不算 <0.70 真实观测）",
              ql0["ratio_buckets"]["unknown"]["labels"] == 1
              and ql0["ratio_buckets"]["<0.70"]["labels"] == 0,
              ql0["ratio_buckets"])


def test_quality_label_absent_and_badlines() -> None:
    print("\n[结果标签] 旧日志无标签 → 未观测；坏行只跳过该行")
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp)                    # 旧日志：没有 quality_label
        res = az.analyze(data)
        ql = res["quality_labels"]
        check("无标签事件：total=0 / observed=False",
              ql["total"] == 0 and ql["observed"] is False)
        check("无标签时五类闭集仍列全且全 0",
              set(ql["by_kind"]) == set(az.QUALITY_LABEL_KINDS)
              and all(v == 0 for v in ql["by_kind"].values()))
        check("无标签但 ratio 观测齐备时标签率是 0.0（不是 None）",
              ql["observed_turns"] == 6 and ql["per_100_observed_turns"] == 0.0,
              ql["per_100_observed_turns"])
        report = az.render_report(res, None)
        check("报告写「未观测 / 无标签事件」", "未观测 / 无标签事件" in report)
        check("无标签也保留纪律说明", "规则自动标签 ≠ 人工结果标签" in report)
        cand = az.build_candidates(res)
        check("无标签时候选辅助块 total=0 且 auxiliary_only",
              cand["quality_labels"]["total"] == 0
              and cand["quality_labels"]["auxiliary_only"] is True)

    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp, ratios=[0.10, 0.20])
        path = os.path.join(data, "audit.jsonl")
        with open(path, "a", encoding="utf-8", newline="\n") as f:
            f.write("这不是 JSON\n")
            f.write("[1, 2, 3]\n")
            f.write(json.dumps(_quality_label(kind_label="missing", quote="漏了"),
                               ensure_ascii=False) + "\n")
        res = az.analyze(data)
        ql = res["quality_labels"]
        check("坏行不拖垮统计：合法标签仍被计入", ql["total"] == 1
              and ql["by_kind"]["missing"] == 1, ql["total"])
        check("坏行计入 skipped（逐行容忍，不静默丢弃）",
              res["sample"]["skipped_lines"] == 2, res["sample"]["skipped_lines"])

    with tempfile.TemporaryDirectory() as tmp:
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        check("空目录：无标签事件不崩且 total=0",
              az.analyze(empty)["quality_labels"]["total"] == 0)


def test_quality_labels_not_in_gate() -> None:
    print("\n[结果标签] 绝不进入 OUTCOME_KINDS / outcome_labels 门槛 / upgrade_eligible")
    check("OUTCOME_KINDS 不含 quality_label（规则自动标签 ≠ 结果标签）",
          "quality_label" not in az.OUTCOME_KINDS
          and "quality_labels" not in az.OUTCOME_KINDS)
    needs = {p: next(g["need"] for g in reqs if g["gate"] == "outcome_labels")
             for p, reqs in az.REQUIREMENTS.items()}
    check("outcome_labels 门槛保持 20/10/10（标签通道未改门槛）",
          needs == {"phaseShiftRatio": 20, "phaseShiftHardRatio": 10,
                    "trendThreshold": 10}, needs)

    ratios = [0.75] * 10 + [0.65] * 10 + [0.90] * 10 + [0.10] * 10
    labels = [_quality_label(kind_label="wrong", quote="不对", turn=i + 1, ratio=0.75)
              for i in range(25)]
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp, ratios=ratios, quality_labels=labels)
        res = az.analyze(data)
        cal = res["calibration"]
        p = {x["key"]: x for x in cal["params"]}["phaseShiftRatio"]
        check("分布证据齐备（40 轮）", p["distribution_ready"] is True, p["blocking"])
        check("25 条规则标签被统计（否则本组断言无意义）",
              res["quality_labels"]["total"] == 25)
        check("规则标签不进 outcome_labels 证据（仍 0）",
              cal["evidence"]["outcome_labels"] == 0, cal["evidence"]["outcome_labels"])
        check("规则标签不能把参数升级为 measured",
              p["upgrade_eligible"] is False and p["proposed_provenance"] == "guess")
        check("结论仍写「缺结果标签 → 不能升级」", "结果标签" in p["verdict"], p["verdict"])
        check("参数 evidence 里没有 quality_label 字段",
              all("quality_label" not in x["evidence"] for x in cal["params"]))
        check("gates 里 outcome_labels 的 have 仍为 0",
              all(next(g["have"] for g in x["gates"] if g["gate"] == "outcome_labels") == 0
                  for x in cal["params"]))
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp, ratios=ratios)
        p2 = {x["key"]: x for x in az.analyze(data)["calibration"]["params"]}["phaseShiftRatio"]
        check("有/无规则标签的 upgrade_eligible 相同（标签不改变判定）",
              p2["upgrade_eligible"] == p["upgrade_eligible"]
              and p2["upgrade_eligible"] is False)
        check("proposed_provenance 也相同（都是 guess）",
              p2["proposed_provenance"] == p["proposed_provenance"] == "guess")


# ── 7. 标定门槛（核心纪律）────────────────────────────────

def test_calibration_insufficient() -> None:
    print("\n[标定] 样本不足 → 保持 guess")
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp)                      # 6 个观测，远低于 30
        res = az.analyze(data)
        cal = res["calibration"]
        params = {p["key"]: p for p in cal["params"]}
        check("三个控制参数都在候选里", set(params) ==
              {"phaseShiftRatio", "phaseShiftHardRatio", "trendThreshold"})
        check("当前值来自 engine/rules 单一来源（0.7/0.85/0.02）",
              params["phaseShiftRatio"]["current_value"] == 0.7
              and params["phaseShiftHardRatio"]["current_value"] == 0.85
              and params["trendThreshold"]["current_value"] == 0.02)
        check("全部 upgrade_eligible=False", cal["upgradeable"] == [], cal["upgradeable"])
        check("全部 distribution_ready=False", cal["distribution_ready"] == [])
        check("proposed_provenance 保持 guess（不编 measured）",
              all(p["proposed_provenance"] == "guess" for p in cal["params"]))
        check("结论文本含「样本不足」",
              all("样本不足" in p["verdict"] for p in cal["params"]))
        check("门槛确实拦住：observed_turns 需要 ≥30，当前 6",
              params["phaseShiftRatio"]["blocking"]
              and any(g["gate"] == "observed_turns" and g["need"] >= 30
                      and g["have"] == 6 for g in params["phaseShiftRatio"]["gates"]))
        check("阈值边界样本门槛 ≥10（禁止用零边界样本拟合阈值）",
              any(g["gate"] == "boundary_shift" and g["need"] >= 10
                  for g in params["phaseShiftRatio"]["gates"]))
        check("结果标签门槛 >0（缺标签就不能升级）",
              all(any(g["gate"] == "outcome_labels" and g["need"] > 0 for g in p["gates"])
                  for p in cal["params"]))
        check("未写 measured 参数数 = 0", cal["measured_count_after_run"] == 0)
        check("监测类声明都不达标（STPA 越狱 0 次等）",
              all(c["met"] is False for c in cal["claims"]))


def test_calibration_gate_math() -> None:
    print("\n[标定·门槛机检] 分布齐备但缺结果标签 → 仍不能升级；全达标 → 可升级")
    # 40 轮：20 次越界、20 个边界样本、真实窗口；但没有结果标签
    ratios = [0.75] * 10 + [0.65] * 10 + [0.90] * 10 + [0.10] * 10
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp, ratios=ratios)
        cal = az.analyze(data)["calibration"]
        p = {x["key"]: x for x in cal["params"]}["phaseShiftRatio"]
        check("分布证据齐备（observed/boundary/crossings/window 全达标）",
              p["distribution_ready"] is True, p["blocking"])
        check("缺结果标签 → 不可升级 measured", p["upgrade_eligible"] is False
              and any(g["gate"] == "outcome_labels" for g in p["blocking"]))
        check("结论明确指出缺结果标签", "结果标签" in p["verdict"], p["verdict"])

    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp, ratios=ratios, corrections_n=20)
        cal = az.analyze(data)["calibration"]
        p = {x["key"]: x for x in cal["params"]}["phaseShiftRatio"]
        check("补齐 ≥20 条结果标签后 upgrade_eligible=True（门槛会放行）",
              p["upgrade_eligible"] is True and p["proposed_provenance"] == "measured",
              p["gates"])
        check("可升级参数出现在 cal.upgradeable", "control.phaseShiftRatio" in cal["upgradeable"],
              cal["upgradeable"])


# ── 8. 输出协议 / CLI ───────────────────────────────────

def test_report_and_cli() -> None:
    print("\n[输出] Markdown 报告 / provenance JSON / exit 0 / 只读 / 拒绝写插件目录")
    with tempfile.TemporaryDirectory() as tmp:
        data = build_fixture(tmp)
        out = os.path.join(tmp, "out", "shadow.md")
        before = {name: open(os.path.join(data, name), "rb").read()
                  for name in ("audit.jsonl", "memory.jsonl", "state.json")}
        rc = az.main(["--dir", data, "--out", out, "--quiet"])
        check("main 返回 0", rc == 0, rc)
        # 审计修复（A5-T6）：原为 os.path.isfile 仅证文件存在；改为读入后断言
        # 报告确实是一份带标题的 Markdown（空文件/占位文件不能通过）。
        report = open(out, encoding="utf-8").read() if os.path.isfile(out) else ""
        check("报告文件已生成且含 Markdown 标题",
              report.startswith("#") and len(report) > 500,
              "len=%d" % len(report))
        check("报告含真实统计（观测数/turn 数）", "| 6 |" in report or "有控制字段的 turn 观测 | 6" in report)
        check("报告含「样本不足」", "样本不足" in report)
        check("报告区分证据标签（实测/推导/由实现计算）",
              "真实日志实测" in report and "由日志推导" in report and "由实现计算" in report)
        check("报告逐参数写「保持 guess」", report.count("保持 guess") >= 3)
        check("报告含复现命令", "analyze_shadow.py --dir" in report)
        after = {name: open(os.path.join(data, name), "rb").read()
                 for name in ("audit.jsonl", "memory.jsonl", "state.json")}
        check("分析是只读的（输入文件逐字节不变）", before == after)
        cand_path = os.path.join(tmp, "out", "provenance-candidates.json")
        check("默认在同目录产出 provenance-candidates.json", os.path.isfile(cand_path))
        cand = json.load(open(cand_path, encoding="utf-8"))
        check("候选 JSON schema/参数/摘要齐全",
              cand.get("schema") == "lingshu/provenance-candidates/v1"
              and len(cand.get("params", [])) == 3
              and cand["summary"]["measured_count_after_run"] == 0)

        rc2 = az.main(["--dir", data, "--out", os.path.join(data, "不该写.md"), "--quiet"])
        check("拒绝把报告写进插件数据目录（exit 2）", rc2 == 2, rc2)
        check("拒绝时确实没有落盘", not os.path.isfile(os.path.join(data, "不该写.md")))
        rc3 = az.main(["--dir", data, "--out", os.path.join(tmp, "ok.md"),
                       "--candidates-out", os.path.join(data, "c.json"), "--quiet"])
        check("拒绝把候选写进插件数据目录（exit 2）", rc3 == 2, rc3)

        out2 = os.path.join(tmp, "no-cand", "shadow.md")
        rc4 = az.main(["--dir", data, "--out", out2, "--no-candidates", "--quiet"])
        check("--no-candidates：只写报告，不写候选 JSON",
              rc4 == 0 and os.path.isfile(out2)
              and not os.path.isfile(os.path.join(tmp, "no-cand", "provenance-candidates.json")))


def test_empty_dir() -> None:
    print("\n[空数据] 不崩、如实说样本不足、exit 0")
    with tempfile.TemporaryDirectory() as tmp:
        empty = os.path.join(tmp, "empty")
        os.makedirs(empty)
        res = az.analyze(empty)
        check("空目录：audit 事件 0 / turn 0 / memory 0",
              res["sample"]["audit_events"] == 0 and res["sample"]["turns"] == 0
              and res["sample"]["memory_entries"] == 0)
        check("空目录：三个文件都标缺失",
              all(not v["exists"] for v in res["files"].values()))
        check("空目录：无可用相变观测", res["phase"]["observed_n"] == 0)
        check("空目录：报告仍渲染", "样本不足" in az.render_report(res, None))
        out = os.path.join(tmp, "empty.md")
        rc = az.main(["--dir", empty, "--out", out, "--quiet"])
        check("空目录 main 返回 0", rc == 0, rc)
        check("空目录报告写成功且含样本不足",
              os.path.isfile(out) and "样本不足" in open(out, encoding="utf-8").read())

        rc2 = az.main(["--dir", os.path.join(tmp, "根本不存在"), "--out",
                       os.path.join(tmp, "missing.md"), "--quiet"])
        check("不存在的数据目录也 exit 0（只在报告里标缺失）", rc2 == 0, rc2)


def test_helpers() -> None:
    print("\n[工具函数] 时间戳单位 / 数据目录守卫")
    check("毫秒时间戳转秒", az._to_seconds(1_800_000_000_000) == 1_800_000_000.0)
    check("秒时间戳保持", az._to_seconds(1_800_000_000) == 1_800_000_000.0)
    check("空值 → 0", az._to_seconds(None) == 0.0)
    check("_inside 判断子路径", az._inside(r"C:\a\b\c.md", r"C:\a\b") is True)
    check("_inside 判断父/兄弟路径", az._inside(r"C:\a\b.md", r"C:\a\b") is False)


def main() -> int:
    print("LINGSHU 验收 · 真实标定分析工具（tools/analyze_shadow.py）")
    test_read_jsonl()
    test_percentile()
    test_turn_attribution()
    test_phase_stats()
    test_stpa_stats()
    test_memory_stats()
    test_shadow_stats()
    test_quality_label_stats()
    test_quality_label_absent_and_badlines()
    test_quality_labels_not_in_gate()
    test_calibration_insufficient()
    test_calibration_gate_math()
    test_report_and_cli()
    test_empty_dir()
    test_helpers()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
