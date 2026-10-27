# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 默认配置（rules/decay-params.yaml 的代码影子）

为什么另存一份而不是读 YAML：
- 回放必须零依赖、零歧义、可复现；
- 解析 YAML 需要一个库，而"回放器不引入依赖"是明确要求。
**两者必须一致** —— 由 tools/check_config_sync.py 校验（见 CHANGELOG C29）。
"""
from __future__ import annotations

import copy
from typing import Any

DEFAULTS: dict[str, Any] = {
    "decay": {
        "formula": "exponential",
        "tau": {"episodic": 7.0, "semantic": 30.0, "procedural": 90.0},  # 天
        "importanceScaling": True,
    },
    "potency": {"archiveThreshold": 0.15},
    "memory": {"actrWeight": 0.6, "touchOnRecall": False},
    "retrieval": {"topK": 3, "timeoutMs": 150, "timeProxTauDays": 30.0},
    "edge": {"minWeight": 0.4, "maxHop": 2, "perHopLimit": 20, "diffusionDiscount": 0.8},
    "dedupe": {"similarity": 0.85, "confidenceCap": 0.6},
    "recallBudget": {"tokenBudgetDefault": 900, "maxReturned": 3,
                     "maxExcludedRefs": 10, "maxExpandIds": 20},
    "forget": {"ttlDefault": None, "ageDays": 180.0,
               "importanceMaxForHardDelete": 0.2,
               "contradictionJaccard": 0.90, "softDeleteDays": 7.0},
    "metacog": {"emotionCapIntensity": 0.6},
    "emotion": {
        "tau": {"valence": 45.0, "arousal": 8.0, "dominance": 90.0},   # 分钟
        "dualSpeed": False,
        "fastTau": {"valence": 20.0, "arousal": 8.0, "dominance": 45.0},
        "slowTau": {"valence": 180.0, "arousal": 60.0, "dominance": 360.0},
        "fastWeight": 0.6,
        "slowWeight": 0.4,
        "clamp": 0.6,
        "intensityMax": 0.85,
        "injectEnterIntensity": 0.2,
        "injectEnterDev": 0.15,
        "injectExitDev": 0.10,
    },
    "compose": {
        "tokenRatioMax": 0.15,
        "styleMultiplierClamp": [0.8, 1.2],
        "maxBlocks": 6,
        # 全局工作空间：域权重动态竞争（docs/19 §2.2）
        "weights": {
            "fact": 0.70,
            "organization": 0.50,
            "style": 0.40,
            "retrieval": 0.45,
            "boostFact": 0.50,
            "boostOrganization": 0.35,
            "boostStyle": 0.40,
            # v3.2（docs/19 §2.4）：风格域 boost 的情绪强度门槛
            "styleBoostThreshold": 0.6,
        },
    },
    "goal": {
        "maxDepth": 3,
        "maxEventsPerTurn": 3,
        "blockedRetryLimit": 2,
        "inferredConfidenceCap": 0.5,
    },
    "relation": {
        "modifierRange": [0.8, 1.2],
        "conflictCap": 1.0,
        "trustUpToMidEvents": 3,
        "trustUpToHighSessions": 10,
        "stageNewToRegularSessions": 3,
    },
    "narrative": {
        "maxNodes": 12,
        "arcMinNodes": 3,
        "gapThresholdDays": 30.0,
    },
    "onboarding": {
        "coldTurns": 3,
        "warmTurns": 20,
        "warmConfidenceFloor": 0.7,
        "regularMemoryCount": 10,
    },
    "persona": {
        "rateLimitPerUser": 5,
        "rateWindowSeconds": 86400.0,
        "storeRawText": False,
    },
    "actr": {
        "d": 0.5,
        "ref": 0.001,
        "lo": -3.0,
        "hi": 1.0,
    },
    "control": {
        "integralKi": 0.15,
        "integralCap": 0.4,
        "integralLeak": 0.05,
        "integralDeadband": 0.02,
        "phaseShiftRatio": 0.7,
        "phaseShiftHardRatio": 0.85,
        # v3.2（docs/19 §2.3）：相变监控的三处工程量也收归单一来源
        "trendThreshold": 0.02,      # 上升趋势提前补锚点的门槛
        "ratioMax": 1.5,             # ratio 观测值上限（用量超窗口时截断）
        "historyCapacity": 64,       # 趋势历史窗口（条）
        # v5.3（ADR-014）：沉浸式安全（Immersive Safety）表达层
        "softBufferMaxIntensity": 0.45,   # 柔性缓冲表达强度上限
        "escalateAfterSoftHits": 3,       # 连续非豁免 mild 命中 → 升级 hard 的门槛
        "immersiveEnabled": True,         # 回滚开关：false → soft 一律按 v3.0 hard
    },
    "replay": {
        "applyOnboarding": False,      # 默认关：保持基线指标可比；开=模拟冷启动
    },
}


def default() -> dict[str, Any]:
    return copy.deepcopy(DEFAULTS)


def _paths(obj: Any, prefix: str = "") -> dict[str, Any]:
    out: dict[str, Any] = {}
    if isinstance(obj, dict):
        for k in sorted(obj):
            out.update(_paths(obj[k], f"{prefix}.{k}" if prefix else k))
    else:
        out[prefix] = obj
    return out


def flatten(cfg: dict[str, Any]) -> dict[str, Any]:
    return _paths(cfg)


def apply_path(cfg: dict[str, Any], path: str, value: Any) -> dict[str, Any]:
    out = copy.deepcopy(cfg)
    cur = out
    parts = path.split(".")
    for p in parts[:-1]:
        cur = cur[p]
    cur[parts[-1]] = value
    return out


def diff(a: dict[str, Any], b: dict[str, Any]) -> dict[str, tuple[Any, Any]]:
    """返回 {path: (a_val, b_val)}。用于归因与灰度对比。"""
    fa, fb = flatten(a), flatten(b)
    return {k: (fa.get(k), fb[k]) for k in sorted(fb) if fa.get(k) != fb[k]}
