# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · ACT-R 激活模型（docs/19 §2.1）

为什么用 ACT-R 而不是纯指数衰减：
    纯指数衰减只看"多久没碰过"，于是**常用但久远的偏好会被误删**——
    "他习惯用简体中文"三周没提，就该继续有效，不该因为时间被归档。

ACT-R 的 base-level activation：

    B_i = ln( Σ_j (t_now − t_j + REF)^(−d) )

- 每个 `t_j` 是一次被访问的时刻（这里用"天"为单位）；
- `d ≈ 0.5`：衰退指数（ACT-R 文献常用值）；
- 加总里既有"最近一次"也有"历史多次"——**频率与近因共同决定**，这正是需求。

归一化后与 `importance` 相乘得到 `potency`，用于排序与归档门。
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

DEFAULT_D = 0.5
DEFAULT_REF = 0.001          # 天；避免 t_j 恰为 now 时除零
DEFAULT_LO = -3.0            # B 的归一化下界（经验标定：100 天未碰的单次访问 ≈ -2.3）
DEFAULT_HI = 1.0             # B 的归一化上界（刚被访问 ≈ +3.4，截断到 1.0）
MAX_TRACES = 64              # 每个条目最多保留多少次访问记录


def base_level(access_times_days: Sequence[float], now_days: float,
               d: float = DEFAULT_D, ref: float = DEFAULT_REF) -> float:
    """ACT-R base-level activation；无记录返回 -inf。"""
    total = 0.0
    for t in list(access_times_days)[-MAX_TRACES:]:
        dt = now_days - float(t) + ref
        if dt <= 0:
            dt = ref
        total += dt ** (-d)
    if total <= 0:
        return float("-inf")
    return math.log(total)


def normalized(activation: float, lo: float = DEFAULT_LO, hi: float = DEFAULT_HI) -> float:
    """把 B 压进 (0,1]；-inf → 0。"""
    if activation == float("-inf"):
        return 0.0
    if hi <= lo:
        return 0.0
    return max(0.0, min(1.0, (activation - lo) / (hi - lo)))


def activation(entry, now_days: float) -> float:
    """从条目对象取访问轨迹并算激活（兼容只有 last_access 的老条目）。"""
    times = getattr(entry, "access_times", None)
    if not times:
        last = float(getattr(entry, "last_access", 0) or 0)
        times = [last / 86400.0] if last else []
    return base_level(times, now_days)


def potency(entry, now_sec: float, importance: float | None = None) -> float:
    """ACT-R 版 potency：importance × 归一化激活。"""
    imp = float(getattr(entry, "importance", 0.5) if importance is None else importance)
    b = activation(entry, now_sec / 86400.0)
    return max(0.0, min(1.0, imp * normalized(b)))


def touch(entry, now_sec: float, keep: int = MAX_TRACES) -> list[float]:
    """记一次访问，返回新的 access_times（天）。不改条目本身。"""
    times = list(getattr(entry, "access_times", None) or [])
    if not times:
        last = float(getattr(entry, "last_access", 0) or 0)
        if last:
            times.append(last / 86400.0)
    times.append(now_sec / 86400.0)
    return times[-keep:]


def explain(entry, now_sec: float) -> dict:
    """给控制台/审计用的可解释输出。"""
    times = getattr(entry, "access_times", None) or []
    now_days = now_sec / 86400.0
    b = activation(entry, now_days)
    return {
        "access_count": len(times),
        "base_level": None if b == float("-inf") else round(b, 4),
        "normalized": round(normalized(b), 4),
        "potency": round(potency(entry, now_sec), 4),
    }
