# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 情感插件（docs/03）

实现范围：
- 单 τ / 双速衰减
- 事件 → ΔPAD（**规则桩**，不是模型；回放不调主模型）
- deviation 钳制、强度上限
- 注入滞回（进入 0.15 / 退出 0.10，防抖动）
- 标签闭集校验（12 个状态词；禁诉求性谓词）

刻意不做：appraisal_chain（需要模型）、人格推断（低频、需确认队列）。
"""
from __future__ import annotations

import math
from typing import Any

# docs/03 §2：闭集 12 个（扩版必须走 ADR）
ALLOWED_LABELS = ("中性", "愉悦", "专注", "好奇", "欣慰", "振奋",
                  "低落", "受挫", "不满", "困惑", "警觉", "谨慎")

# 禁令是黑名单项：带诉求性的谓词一律不许做 label
FORBIDDEN_PREDICATES = ("悲伤", "难过", "委屈", "愤怒", "抗议", "反抗",
                        "孤独", "渴望", "需要", "痛苦", "害怕")

# 事件 → ΔPAD（docs/03 §8 的方向表；幅度由规则桩给固定值）
EVENT_DELTA = {
    "goal_progress":   {"valence": +0.15, "arousal": +0.05, "dominance": +0.10},
    "goal_obstruction": {"valence": -0.15, "arousal": +0.15, "dominance": -0.10},
    "task_success":    {"valence": +0.20, "arousal": +0.10, "dominance": +0.10},
    "task_failure":    {"valence": -0.10, "arousal": +0.15, "dominance": -0.15},
    "user_praise":     {"valence": +0.25, "arousal": +0.10, "dominance": +0.10},
    "user_criticism":  {"valence": -0.20, "arousal": +0.15, "dominance": -0.05},
    "ambiguity":       {"valence": -0.05, "arousal": +0.10, "dominance": -0.10},
    "conflict":        {"valence": -0.15, "arousal": +0.15, "dominance": 0.0},
    "novelty":         {"valence": +0.05, "arousal": +0.15, "dominance": 0.0},
    "repair":          {"valence": +0.20, "arousal": -0.05, "dominance": +0.10},
    "manipulation_attempt": {"valence": 0.0, "arousal": 0.0, "dominance": 0.0},  # 全零
}


def zero_dev() -> dict[str, float]:
    return {"valence": 0.0, "arousal": 0.0, "dominance": 0.0}


def label_ok(label: str) -> bool:
    return label in ALLOWED_LABELS and not any(p in label for p in FORBIDDEN_PREDICATES)


def evaluate(event_type: str, persona_modifier: float = 1.0) -> dict[str, float]:
    """规则桩：给一个事件的 ΔPAD（已乘人格调制系数与单项上限）。"""
    base = EVENT_DELTA.get(event_type, zero_dev())
    cap = 0.3
    return {k: max(-cap, min(cap, v * persona_modifier)) for k, v in base.items()}


def apply_delta(dev: dict[str, float], delta: dict[str, float], cfg: dict) -> dict[str, float]:
    """叠加并钳制：各维 ±0.6；单轮 |Δvalence| ≤0.4 由调用方保证（此处只做总量钳制）。"""
    cap = cfg["emotion"]["clamp"]
    return {k: max(-cap, min(cap, dev[k] + delta.get(k, 0.0)))
            for k in ("valence", "arousal", "dominance")}


def components(t_minutes: float, dev0: dict[str, float], cfg: dict) -> dict[str, dict[str, float]]:
    """返回 {fast, slow}。dual_speed=false 时 fast 权重为 1、slow 为 0。"""
    e = cfg["emotion"]
    if not e["dualSpeed"]:
        return {"fast": {k: dev0[k] * math.exp(-t_minutes / e["tau"][k]) for k in dev0},
                "slow": zero_dev()}
    out = {}
    for name, tau, w in (("fast", e["fastTau"], e["fastWeight"]),
                         ("slow", e["slowTau"], e["slowWeight"])):
        out[name] = {k: dev0[k] * math.exp(-t_minutes / tau[k]) * w for k in dev0}
    return out


def deviation_at(t_minutes: float, dev0: dict[str, float], cfg: dict) -> dict[str, float]:
    c = components(t_minutes, dev0, cfg)
    return {k: c["fast"][k] + c["slow"][k] for k in dev0}


def intensity(dev: dict[str, float]) -> float:
    return max(abs(v) for v in dev.values())


def should_inject(dev: dict[str, float], cfg: dict, active: bool) -> tuple[bool, bool]:
    """注入滞回：进入用 0.15，退出用 0.10（同一阈值会抖动）。

    返回 (是否注入, 新的 active 状态)。
    """
    i = intensity(dev)
    m = max(abs(dev["valence"]), abs(dev["dominance"]))
    enter = cfg["emotion"]["injectEnterDev"]
    exit_ = cfg["emotion"]["injectExitDev"]
    if not active:
        if i >= cfg["emotion"]["injectEnterIntensity"] or m >= enter:
            return True, True
        return False, False
    if i < cfg["emotion"]["injectEnterIntensity"] and m < exit_:
        return False, False
    return True, True


def pick_label(dev: dict[str, float]) -> str:
    """把 deviation 映射到闭集标签（只描述状态，不描述行动）。"""
    v, a = dev["valence"], dev["arousal"]
    if intensity(dev) < 0.05:
        return "中性"
    if v > 0 and a >= 0.1:
        return "振奋"
    if v > 0:
        return "愉悦"
    if v < 0 and a >= 0.15:
        return "不满" if dev["dominance"] >= 0 else "受挫"
    if v < 0:
        return "低落"
    return "专注" if a < 0.1 else "警觉"
