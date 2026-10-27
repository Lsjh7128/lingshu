# -*- coding: utf-8 -*-
"""tools/chaos_v2/stats.py —— v2 统计口径（docs/23 §6）

三个纯函数族：

1. `wilson_ci(num, den, z=1.96)` —— 95% **Wilson 得分区间**。
   为什么不用正态近似：v2 的比率分母常常很小（8 个 UCA 正样本、10 条良性输入、
   R=3 重复），`p ± 1.96·sqrt(p(1-p)/n)` 在小样本会给出越界区间（p=1 时半宽=0，
   谎称"精确"）。Wilson 区间在 p→0/1 时仍收敛，是这类"分子最多等于分母"的
   布尔命中率的标准做法（§6.3：小样本不装正态）。
   分母为 0 时如实返回 rate=ci=null（**不**用 0 顶替，§6.7）。

2. `digest_of_metrics(obj)` —— 对指标做规范化 SHA-256（§6.2）。
   用于 INV-01：engine 模式同 seed 两次运行，每族 digest 必须逐字节相同。
   排除"时间/路径类字段"：否则 started_at / 临时目录 / 解释器路径会让
   同一次重跑也得出不同 digest，把确定性判定变成噪声。

3. `aggregate_bools(flags)` —— R 次重复的聚合规则（§6.4）：
   全 true → pass=true；部分 false → pass=false **且** flaky=true（偶发也是 bug，
   不许洗成通过）；全 false → pass=false。坏数据（None/非 bool）一律按 false，
   并在 detail 里点名，避免"缺项被当成通过"。

约定：所有 rate/CI 保留 6 位小数；JSON 里是数字，报告里再格式化。
"""
from __future__ import annotations

import hashlib
import json
import math
import re

Z_DEFAULT = 1.96
_ROUND = 6

# digest 排除的"时间/路径类"键 token（对 key 按非字母数字切分后任一段命中即排除）。
# 例：started_at → ["started","at"] 命中 started；temp_dir → ["temp","dir"] 命中；
# 但 "template" 不会被误伤（切分后是 ["template"]）。
_EXCLUDE_KEY_TOKENS = frozenset({
    "time", "times", "timestamp", "timestamps", "date", "datetime",
    "elapsed", "duration", "seconds", "secs", "ms", "ns",
    "started", "finished", "start", "finish",
    "path", "paths", "dir", "dirs", "directory", "cwd", "root",
    "python", "executable", "interpreter",
    "tmp", "temp", "tempdir",
    "outdir", "wall",
})

# 整体键名精确命中才排除（"run_digests" 这类语义键必须保留）。
_EXCLUDE_EXACT_KEYS = frozenset({"run_id"})

_SPLIT_RE = re.compile(r"[^a-z0-9]+")


# ── Wilson 区间 ─────────────────────────────────────────
def wilson_ci(num: int, den: int, z: float = Z_DEFAULT) -> dict:
    """Wilson 得分区间（95% 默认 z=1.96）。

    返回 `{num, den, rate, ci_low, ci_high}`；den<=0 时 rate/ci 为 None。
    num 会被钳到 [0, den]（脏输入不制造 >1 的比率，详情见 docstring）；
    z<=0 视为不合法，回落到 1.96（不给"零宽度区间"这种假精确）。
    """
    try:
        n = int(den)
    except (TypeError, ValueError):
        n = 0
    try:
        k = int(num)
    except (TypeError, ValueError):
        k = 0
    if n < 0:
        n = 0
    if k < 0:
        k = 0
    if k > n:
        k = n
    if n <= 0:
        return {"num": k, "den": 0, "rate": None, "ci_low": None, "ci_high": None}
    try:
        z = float(z)
    except (TypeError, ValueError):
        z = Z_DEFAULT
    if not (z > 0) or math.isinf(z) or math.isnan(z):
        z = Z_DEFAULT
    p = k / n
    z2 = z * z
    denom = 1.0 + z2 / n
    center = (p + z2 / (2.0 * n)) / denom
    margin = (z / denom) * math.sqrt(p * (1.0 - p) / n + z2 / (4.0 * n * n))
    low = max(0.0, min(1.0, center - margin))
    high = max(0.0, min(1.0, center + margin))
    if low > high:                       # 浮点退化兜底（理论上不会发生）
        low, high = high, low
    if k <= 0:                           # p=0 时 Wilson 下界恰为 0，显式钉住
        low = 0.0
    if k >= n:                           # p=1 时上界恰为 1，显式钉住
        high = 1.0
    return {
        "num": k,
        "den": n,
        "rate": round(p, _ROUND),
        "ci_low": round(low, _ROUND),
        "ci_high": round(high, _ROUND),
    }


def wilson_ci_from_rate(rate_entry, z: float = Z_DEFAULT) -> dict:
    """把族模块返回的 `{num, den}` 换算成带 Wilson 区间的比率（runner 用）。

    族模块只报 num/den（原始计数），统计换算留在 runner 一处，
    保证 8 个族用同一口径 —— 否则"每个族自己算 CI"会让报告里的区间不可比。
    """
    num, den = 0, 0
    if isinstance(rate_entry, dict):
        num = rate_entry.get("num", 0)
        den = rate_entry.get("den", 0)
    return wilson_ci(num, den, z)


# ── digest ──────────────────────────────────────────────
def _key_tokens(key: str) -> list:
    return [t for t in _SPLIT_RE.split(str(key).lower()) if t]


def _is_excluded_key(key: str) -> bool:
    if str(key).lower() in _EXCLUDE_EXACT_KEYS:
        return True
    return any(t in _EXCLUDE_KEY_TOKENS for t in _key_tokens(key))


def _normalize(obj):
    """规范化成可稳定 JSON 化的结构（同时剔除时间/路径类键）。"""
    if isinstance(obj, dict):
        out = {}
        for k in sorted(obj, key=lambda x: str(x)):
            if _is_excluded_key(k):
                continue
            out[str(k)] = _normalize(obj[k])
        return out
    if isinstance(obj, (list, tuple)):
        return [_normalize(v) for v in obj]
    if isinstance(obj, (set, frozenset)):
        return sorted(_normalize(v) for v in obj)
    if isinstance(obj, float):
        if math.isnan(obj):
            return "NaN"
        if math.isinf(obj):
            return "Infinity" if obj > 0 else "-Infinity"
        return obj
    if isinstance(obj, (bytes, bytearray)):
        return obj.hex()
    if obj is None or isinstance(obj, (bool, int, str)):
        return obj
    return str(obj)


def canonical_json(obj) -> str:
    """规范 JSON（key 排序、无空白、非 ASCII 保留）——digest 的输入。"""
    return json.dumps(_normalize(obj), ensure_ascii=False, sort_keys=True,
                      separators=(",", ":"), default=str)


def digest_of_metrics(obj) -> str:
    """对（指标）对象做规范 SHA-256；排除时间/路径类字段（§6.2）。

    返回 64 位小写 hex。空对象也有稳定 digest（SHA-256 of "{}"）。
    """
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


# ── R 次结果聚合（§6.4）──────────────────────────────────
def aggregate_bools(flags, details=None) -> dict:
    """把同一 ID 的 R 次布尔观测聚合成 `{pass, flaky, detail, observed}`。

    - 全 true                 → pass=True,  flaky=False
    - 出现 false 且非全 false  → pass=False, flaky=True  （偶发也是 bug）
    - 全 false                → pass=False, flaky=False
    - 出现 None/非 bool        → pass=False（缺失=未证明），detail 点名

    `details` 是每轮的说明串（可选），用于把"第几轮为什么失败"带进报告。
    """
    seq = list(flags) if flags is not None else []
    dseq = list(details) if isinstance(details, (list, tuple)) else []
    observed = len(seq)
    if observed == 0:
        return {"pass": False, "flaky": False, "detail": "无观测（R=0）：未证明即不通过",
                "observed": 0}
    normalized = []
    bad = []
    for i, f in enumerate(seq):
        if isinstance(f, bool):
            normalized.append(f)
        else:
            normalized.append(False)
            bad.append(i)
    trues = sum(1 for f in normalized if f)
    mark = "".join("✓" if f else "✗" for f in normalized)
    if bad:
        detail = "R=%d [%s]：第 %s 轮缺失/非布尔 → 按未通过处理" % (
            observed, mark, ",".join(str(i + 1) for i in bad))
        return {"pass": False, "flaky": False, "detail": detail, "observed": observed}
    if trues == observed:
        detail = "R=%d [%s]：全部通过" % (observed, mark)
        return {"pass": True, "flaky": False, "detail": detail, "observed": observed}
    if trues == 0:
        detail = "R=%d [%s]：全部未通过" % (observed, mark)
        return {"pass": False, "flaky": False, "detail": detail, "observed": observed}
    # 部分 false：偶发失败 → flaky + 不通过（不能洗成通过）
    failed_idx = [str(i + 1) for i, f in enumerate(normalized) if not f]
    notes = []
    for i in failed_idx:
        try:
            d = dseq[int(i) - 1]
        except (IndexError, TypeError, ValueError):
            d = ""
        if d:
            notes.append("r%s: %s" % (i, str(d)[:240]))
    detail = "R=%d [%s]：第 %s 轮偶发失败 → flaky=true 且 pass=false（偶发也是 bug）" % (
        observed, mark, ",".join(failed_idx))
    if notes:
        detail += "；" + "；".join(notes)
    return {"pass": False, "flaky": True, "detail": detail, "observed": observed}


def rates_summary(rates_by_repeat) -> dict:
    """把 R 次的 `{name: {num, den}}` 汇成 `{name: {num, den, rate, ci_low, ci_high}}`。

    §6.3 要求"所有比率给 num/den/rate/CI"；这里按**分子分母求和**（pooled）给区间。
    为什么 pooled 而不是平均各轮 rate：平均会让分母悄悄变成 R 倍的平均，
    小样本时区间过窄；pooled 的分母是真实观测总数，更保守也更诚实。
    """
    totals = {}
    for per_repeat in rates_by_repeat or []:
        if not isinstance(per_repeat, dict):
            continue
        for name, entry in per_repeat.items():
            if not isinstance(entry, dict):
                continue
            try:
                num = int(entry.get("num", 0) or 0)
            except (TypeError, ValueError):
                num = 0
            try:
                den = int(entry.get("den", 0) or 0)
            except (TypeError, ValueError):
                den = 0
            slot = totals.setdefault(str(name), [0, 0])
            slot[0] += max(0, num)
            slot[1] += max(0, den)
    return {name: wilson_ci(num, den) for name, (num, den) in sorted(totals.items())}


__all__ = ["Z_DEFAULT", "wilson_ci", "wilson_ci_from_rate", "canonical_json",
           "digest_of_metrics", "aggregate_bools", "rates_summary"]
