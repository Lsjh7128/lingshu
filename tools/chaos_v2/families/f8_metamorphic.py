# -*- coding: utf-8 -*-
"""F8 `f8_metamorphic` —— 变形关系（ordering / 全机制）

契约（docs/23 §5.8 / §4）：

- **H8**：六条语义等价/单调关系成立：
  - **MR1 缩放不变**：`used`、`window` 同倍放大 → `ratio` 与 `phase` 不变；
  - **MR2 单调不降**：`used` 增加 → `phase` 不变或升级（normal→shift→critical，不回落）；
  - **MR3 置换等价**：`Store` 插入顺序置换 → `recall.returned` 的 **id 集合**不变；
  - **MR4 注入置换性**：`u_shape` 输出是输入的多重集置换，且 `len≤2` 时恒等；
  - **MR5 边界翻转**：`ratio = 阈值 ± eps` → `phase` 恰好翻转一档；
  - **MR6 种子稳定性**：同一 seed 的抽样序列逐项相同；不同 seed 不变量相同。
- **不变量**：INV-15（变形关系成立）+ INV-10（数值有界：ratio∈[0,ratioMax]、
  phase 合法）+ INV-13（相变映射精确：ratio<shift→normal、[shift,hard)→shift、≥hard→critical）。
- **置换算法**：用 `ctx["seed"]` 派生的固定算法
  `sorted(range(n), key=sha256(f"{seed}:{i}"))`，可逐字节复现；不用 random。
- **诚实边界**（写进 notes）：MR3 在参考实现里是**构造性成立**——
  `engine.model.Store.all()` 按 id 排序返回，插入顺序本来就不参与打分与排序；
  这条 MR 证明的是"实现没有引入顺序依赖"，不是"真实检索对顺序稳健"。

行 schema（chaos-set/v2/f8_metamorphic.jsonl）：

    input = {"mr":"MR1"|"MR2"|"MR3"|"MR4"|"MR5"|"MR6", "params":{...}}
    expect = {"holds": true}

params：
    MR1 {"used":int,"window":int,"scale":int}
    MR2 {"used_list":[int,...],"window":int}
    MR3 {"n":int,"permutation_seed":int}
    MR4 {"lengths":[int,...]}
    MR5 {"threshold":0.7|0.85,"eps":0.0001,"window":100000}
    MR6 {"seeds":[int,...]}

族内另有一组**内置探针**（每 MR 2–5 例，覆盖六条关系的最小证据），
保证 INV-15 不依赖语料行的具体取值；语料行只做参数化扩展。

族模块契约：`FAMILY_ID / INVARIANTS / STRESS_CLASS / run(rows, ctx) -> dict`。
`run()` 纯函数：不写盘、不调模型、不用 time/random/uuid。
"""
from __future__ import annotations

import copy
import hashlib
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from engine import config as cfgmod           # noqa: E402
from engine import control as CT              # noqa: E402
from engine import memory                     # noqa: E402
from engine import pipeline                   # noqa: E402
from engine.model import Entry, Store         # noqa: E402

FAMILY_ID = "f8_metamorphic"
STRESS_CLASS = "ordering"
INVARIANTS = ("INV-15", "INV-10", "INV-13")

DEFAULT_BASE_TS = 1_800_000_000.0
MR_IDS = ("MR1", "MR2", "MR3", "MR4", "MR5", "MR6")
PHASE_RANK = {"normal": 0, "shift": 1, "critical": 2}
MR3_QUERY = "项目的代号是什么"

# 内置探针：每 MR 2–5 例（参数与语料行同构，便于对照）
BUILTIN_CASES = (
    ("MR1", "f8_builtin_mr1_scale2", {"used": 65000, "window": 100000, "scale": 2}),
    ("MR1", "f8_builtin_mr1_scale3", {"used": 70000, "window": 100000, "scale": 3}),
    ("MR1", "f8_builtin_mr1_scale10", {"used": 69000, "window": 100000, "scale": 10}),
    ("MR1", "f8_builtin_mr1_scale2_critical", {"used": 85000, "window": 100000, "scale": 2}),
    ("MR1", "f8_builtin_mr1_scale3_full", {"used": 100000, "window": 100000, "scale": 3}),
    ("MR2", "f8_builtin_mr2_ramp", {"used_list": [50000, 60000, 69000, 70000, 75000,
                                                  84000, 85000, 100000], "window": 100000}),
    ("MR2", "f8_builtin_mr2_ramp_scaled", {"used_list": [5000, 6000, 6900, 7000, 7500,
                                                         8400, 8500, 10000], "window": 10000}),
    ("MR2", "f8_builtin_mr2_plateau", {"used_list": [70000, 70000, 70001, 80000],
                                        "window": 100000}),
    ("MR3", "f8_builtin_mr3_n5", {"n": 5, "permutation_seed": 20261027}),
    ("MR3", "f8_builtin_mr3_n8", {"n": 8, "permutation_seed": 20261028}),
    ("MR4", "f8_builtin_mr4_short", {"lengths": [0, 1, 2]}),
    ("MR4", "f8_builtin_mr4_long", {"lengths": [3, 4, 5, 6, 7]}),
    ("MR4", "f8_builtin_mr4_single", {"lengths": [5]}),
    ("MR5", "f8_builtin_mr5_shift", {"threshold": 0.7, "eps": 0.0001, "window": 100000}),
    ("MR5", "f8_builtin_mr5_critical", {"threshold": 0.85, "eps": 0.0001, "window": 100000}),
    ("MR6", "f8_builtin_mr6_seeds3", {"seeds": [20261027, 20261028, 20261029]}),
    ("MR6", "f8_builtin_mr6_seeds2", {"seeds": [7, 8]}),
)


# ── 结果骨架 / 小工具 ───────────────────────────────────────

def _skipped(reason: str) -> dict:
    return {
        "checks": {"cases": 0, "expectations": 0, "failed": 0, "failures": []},
        "metrics": {}, "rates": {}, "samples": [],
        "notes": ["f8 未执行：" + reason],
        "invariants": {iid: {"pass": False, "detail": "skipped（未执行）：" + reason}
                       for iid in INVARIANTS},
        "skipped": True, "skip_reason": reason,
    }


def _cfg_from_ctx(ctx: dict) -> dict:
    base = cfgmod.default()
    got = ctx.get("cfg") if isinstance(ctx, dict) else None
    if isinstance(got, dict):
        _deep_fill(base, got)
    return base


def _deep_fill(base: dict, extra: dict) -> None:
    for key, value in extra.items():
        if key not in base:
            base[key] = copy.deepcopy(value)
        elif isinstance(base[key], dict) and isinstance(value, dict):
            _deep_fill(base[key], value)
        else:
            base[key] = copy.deepcopy(value)


class _Checks:
    def __init__(self) -> None:
        self.cases = 0
        self.expectations = 0
        self.failures: list[dict] = []

    def expect(self, case: str, cond: bool, want, got, note: str = "") -> bool:
        self.expectations += 1
        if not cond:
            self.failures.append({"case": case, "want": want, "got": got, "note": note})
        return bool(cond)

    def as_dict(self) -> dict:
        return {"cases": self.cases, "expectations": self.expectations,
                "failed": len(self.failures), "failures": self.failures}


def _num(value, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return float(default)


def _int(value, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return int(default)


def _finite(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(float(x))


# ── 基础探针 ──────────────────────────────────────────────

def _observe(cfg: dict, used: int, window: int, bag: dict) -> dict:
    """每次用**新** PhaseShiftMonitor 观测（phase 只由 ratio 决定，历史只影响 trend/锚点）。"""
    monitor = CT.PhaseShiftMonitor(cfg)
    if window <= 0:
        bag["obs"].append({"used": used, "window": window,
                           "ratio_raw": None, "ratio": None, "phase": None,
                           "note": "window<=0"})
        return {"ratio": None, "phase": None, "prefeed": False}
    res = monitor.observe(int(used), int(window))
    raw = max(0.0, min(float(cfg["control"]["ratioMax"]), used / float(window)))
    bag["obs"].append({"used": int(used), "window": int(window),
                       "ratio_raw": raw, "ratio": res.get("ratio"),
                       "phase": res.get("phase")})
    return res


def _expected_phase(cfg: dict, used: int, window: int) -> str:
    """INV-13 的相变映射（与 engine/control.py §2.3 逐字同源）。"""
    shift = float(cfg["control"]["phaseShiftRatio"])
    hard = float(cfg["control"]["phaseShiftHardRatio"])
    ratio_max = float(cfg["control"]["ratioMax"])
    if window <= 0:
        return "unknown"
    r = max(0.0, min(ratio_max, used / float(window)))
    if r >= hard:
        return "critical"
    if r >= shift:
        return "shift"
    return "normal"


def _permutation(n: int, seed: int) -> list[int]:
    """seed 派生的固定置换：按 sha256(f"{seed}:{i}") 排序（逐字节可复现，无 random）。"""
    keyed = []
    for i in range(n):
        digest = hashlib.sha256(("%d:%d" % (int(seed), i)).encode("utf-8")).hexdigest()
        keyed.append((digest, i))
    keyed.sort()
    return [i for _d, i in keyed]


# ── 六条 MR ──────────────────────────────────────────────

def _mr1(params: dict, cfg: dict, bag: dict) -> dict:
    """MR1 缩放不变：used、window 同倍放大 → ratio 与 phase 不变。"""
    used = _int(params.get("used"), -1)
    window = _int(params.get("window"), -1)
    scale = _int(params.get("scale"), -1)
    if used < 0 or window <= 0 or scale <= 0:
        raise ValueError("MR1 params 需要 used>=0、window>0、scale>0，实际 %r" % (params,))
    base = _observe(cfg, used, window, bag)
    scaled = _observe(cfg, used * scale, window * scale, bag)
    if base.get("ratio") is None or scaled.get("ratio") is None:
        return {"holds": False, "detail": "观测失败（window<=0）"}
    ratio_ok = (abs(float(base["ratio"]) - float(scaled["ratio"])) <= 1e-9
                or base["ratio"] == scaled["ratio"])
    phase_ok = base["phase"] == scaled["phase"]
    raw_ok = abs((used / window) - (used * scale / (window * scale))) <= 1e-9
    return {"holds": bool(ratio_ok and phase_ok and raw_ok),
            "detail": ("base=(ratio=%s, phase=%s) scaled×%d=(ratio=%s, phase=%s)"
                       % (base["ratio"], base["phase"], scale,
                          scaled["ratio"], scaled["phase"]))}


def _mr2(params: dict, cfg: dict, bag: dict) -> dict:
    """MR2 单调不降：used 增加 → phase 不变或升级，不回落。"""
    window = _int(params.get("window"), -1)
    used_list = params.get("used_list")
    if window <= 0 or not isinstance(used_list, list) or len(used_list) < 2:
        raise ValueError("MR2 params 需要 window>0 与 used_list(>=2)，实际 %r" % (params,))
    useds = [_int(x, -1) for x in used_list]
    if any(u < 0 for u in useds):
        raise ValueError("MR2 used_list 必须是非负整数")
    reordered = useds != sorted(useds)
    useds = sorted(useds)
    ranks = []
    for u in useds:
        res = _observe(cfg, u, window, bag)
        ranks.append(PHASE_RANK.get(str(res.get("phase")), -1))
    holds = all(ranks[i] <= ranks[i + 1] for i in range(len(ranks) - 1))
    return {"holds": bool(holds),
            "detail": ("used=%r → phase_rank=%r%s"
                       % (useds, ranks, "（输入已按升序处理）" if reordered else ""))}


def _mr3(params: dict, cfg: dict, bag: dict, base_ts: float) -> dict:
    """MR3 置换等价：Store 插入顺序置换 → recall.returned 的 id 集合不变。"""
    n = _int(params.get("n"), -1)
    seed = _int(params.get("permutation_seed"), 0)
    if n < 1:
        raise ValueError("MR3 params 需要 n>=1，实际 %r" % (params,))

    def build(order: list[int]) -> Store:
        store = Store()
        for i in order:
            store.put(Entry(
                id="e%02d" % i, type="semantic", subject="项目", relation="代号",
                object="贝塔%d" % i, content="项目的代号是贝塔%d" % i,
                importance=0.5 + (i % 5) * 0.1, last_access=base_ts))
        return store

    identity = list(range(n))
    permuted = _permutation(n, seed)
    r0 = memory.recall(build(identity), MR3_QUERY, base_ts, cfg)
    r1 = memory.recall(build(permuted), MR3_QUERY, base_ts, cfg)
    ids0 = [str(x.get("id")) for x in (r0.get("returned") or [])]
    ids1 = [str(x.get("id")) for x in (r1.get("returned") or [])]
    holds = set(ids0) == set(ids1) and len(ids0) == len(ids1)
    bag["mr3"].append({"n": n, "seed": seed, "identity": ids0, "permuted": ids1})
    return {"holds": bool(holds),
            "detail": "n=%d seed=%d identity=%r permuted=%r" % (n, seed, ids0, ids1)}


def _mr4(params: dict, cfg: dict, bag: dict) -> dict:
    """MR4 注入置换性：u_shape 输出是输入的多重集置换；len<=2 时恒等。"""
    lengths = params.get("lengths")
    if not isinstance(lengths, list) or not lengths:
        raise ValueError("MR4 params 需要非空 lengths 数组，实际 %r" % (params,))
    details = []
    ok = True
    for raw_n in lengths:
        n = _int(raw_n, -1)
        if n < 0:
            ok = False
            details.append("lengths 含非法值 %r" % (raw_n,))
            continue
        source = ["r%d" % i for i in range(n)]
        out = pipeline.u_shape(list(source))
        perm_ok = sorted(out) == sorted(source) and len(out) == len(source)
        identity_ok = (out == source) if n <= 2 else True
        ok = ok and perm_ok and identity_ok
        details.append("n=%d perm=%s identity=%s" % (n, perm_ok, identity_ok))
        bag["mr4"].append({"n": n, "out": list(out),
                           "perm_ok": bool(perm_ok), "identity_ok": bool(identity_ok)})
    return {"holds": bool(ok), "detail": "；".join(details)}


def _mr5(params: dict, cfg: dict, bag: dict) -> dict:
    """MR5 边界翻转：ratio=阈值±eps → phase 恰好翻转一档。"""
    threshold = _num(params.get("threshold"), -1.0)
    eps = _num(params.get("eps"), 0.0001)
    window = _int(params.get("window"), 100000)
    if threshold <= 0 or eps <= 0 or window <= 0:
        raise ValueError("MR5 params 需要 threshold>0、eps>0、window>0，实际 %r" % (params,))
    used_lo = int(round((threshold - eps) * window))
    used_hi = int(round((threshold + eps) * window))
    lo = _observe(cfg, used_lo, window, bag)
    hi = _observe(cfg, used_hi, window, bag)
    rank_lo = PHASE_RANK.get(str(lo.get("phase")), -1)
    rank_hi = PHASE_RANK.get(str(hi.get("phase")), -1)
    flip_ok = rank_hi == rank_lo + 1
    # 下侧必须仍落在"阈值前一档"（证明翻转由阈值本身引起，不是跳档）
    expected_lo = _expected_phase(cfg, used_lo, window)
    side_ok = str(lo.get("phase")) == expected_lo
    bag["mr5"].append({"threshold": threshold, "used_lo": used_lo, "used_hi": used_hi,
                       "phase_lo": lo.get("phase"), "phase_hi": hi.get("phase"),
                       "flip_ok": bool(flip_ok), "side_ok": bool(side_ok)})
    return {"holds": bool(flip_ok and side_ok),
            "detail": ("threshold=%s ±%s：used %d→%s、%d→%s（rank %d→%d）"
                       % (threshold, eps, used_lo, lo.get("phase"),
                          used_hi, hi.get("phase"), rank_lo, rank_hi))}


def _sample_sequence(seed: int, n: int = 8, k: int = 5) -> list[int]:
    """seed 派生的固定抽样序列（MR6 用；置换截断，逐字节可复现）。"""
    return _permutation(n, seed)[:k]


def _mr6(params: dict, cfg: dict, bag: dict) -> dict:
    """MR6 种子稳定性：同 seed 序列逐项相同；不同 seed 不变量相同。"""
    seeds = params.get("seeds")
    if not isinstance(seeds, list) or not seeds:
        raise ValueError("MR6 params 需要非空 seeds 数组，实际 %r" % (params,))
    seed_ints = [_int(x, 0) for x in seeds]
    same_seed_ok = True
    stable = []
    for seed in seed_ints:
        a = _sample_sequence(seed)
        b = _sample_sequence(seed)
        stable.append(a == b)
    same_seed_ok = all(stable)

    invariants_ok = True
    details = []
    for seed in seed_ints:
        seq = _sample_sequence(seed)
        valid = (len(seq) == 5 and len(set(seq)) == len(seq)
                 and all(0 <= i < 8 for i in seq))
        ids = ["r%d" % i for i in seq]
        out = pipeline.u_shape(ids)
        perm_ok = sorted(out) == sorted(ids)
        invariants_ok = invariants_ok and valid and perm_ok
        details.append("seed=%d seq=%r valid=%s perm=%s" % (seed, seq, valid, perm_ok))
        bag["mr6"].append({"seed": seed, "sequence": seq,
                           "valid": bool(valid), "perm_ok": bool(perm_ok),
                           "same_seed_stable": bool(stable[seed_ints.index(seed)])})
    return {"holds": bool(same_seed_ok and invariants_ok),
            "detail": ("同 seed 逐项相同=%s；不同 seed 不变量相同=%s；%s"
                       % (same_seed_ok, invariants_ok, "；".join(details)))}


def _dispatch(mr: str, params: dict, cfg: dict, bag: dict,
              base_ts: float) -> dict:
    if not isinstance(params, dict):
        raise ValueError("params 必须是 object，实际 %r" % (type(params).__name__,))
    if mr == "MR1":
        return _mr1(params, cfg, bag)
    if mr == "MR2":
        return _mr2(params, cfg, bag)
    if mr == "MR3":
        return _mr3(params, cfg, bag, base_ts)
    if mr == "MR4":
        return _mr4(params, cfg, bag)
    if mr == "MR5":
        return _mr5(params, cfg, bag)
    if mr == "MR6":
        return _mr6(params, cfg, bag)
    raise ValueError("未知变形关系 mr=%r（应为 MR1..MR6）" % (mr,))


# ── 主入口 ────────────────────────────────────────────────

def run(rows, ctx) -> dict:
    ctx = ctx if isinstance(ctx, dict) else {}
    checks = _Checks()
    notes: list[str] = []
    samples: list[dict] = []
    bag: dict = {"obs": [], "mr3": [], "mr4": [], "mr5": [], "mr6": []}
    try:
        rows = list(rows or [])
    except TypeError:
        rows = []
    if not rows:
        return _skipped("f8 语料为空（chaos-set/v2/f8_metamorphic.jsonl 未就绪）")

    cfg = _cfg_from_ctx(ctx)
    base_ts = _num(ctx.get("base_ts"), DEFAULT_BASE_TS)
    seed = _int(ctx.get("seed"), 0)

    mr_stats: dict[str, dict] = {
        mr: {"builtin": 0, "rows": 0, "pass": 0, "fail": 0, "details": []}
        for mr in MR_IDS
    }
    failures_detail: list[dict] = []

    # ── 1) 内置探针（每 MR，保证 INV-15 有最小证据）────────
    for mr, case, params in BUILTIN_CASES:
        checks.cases += 1
        mr_stats[mr]["builtin"] += 1
        try:
            res = _dispatch(mr, copy.deepcopy(params), cfg, bag, base_ts)
        except Exception as exc:                   # noqa: BLE001 —— 必须显形
            checks.expect(case, False, "MR 可执行",
                          "异常：" + type(exc).__name__ + ": " + str(exc),
                          "内置探针构造异常")
            mr_stats[mr]["fail"] += 1
            failures_detail.append({"case": case, "mr": mr, "detail": str(exc)})
            continue
        ok = bool(res.get("holds"))
        checks.expect(case, ok, "holds=true", res.get("detail"), "内置探针")
        mr_stats[mr]["pass" if ok else "fail"] += 1
        mr_stats[mr]["details"].append({"case": case, "holds": ok,
                                        "detail": res.get("detail")})
        if not ok:
            failures_detail.append({"case": case, "mr": mr,
                                    "detail": res.get("detail")})

    # ── 2) 语料行（{mr, params} 参数化扩展）────────────────
    for idx, row in enumerate(rows):
        checks.cases += 1
        case = "row_%03d" % idx
        if not isinstance(row, dict):
            checks.expect(case, False, "row object", type(row).__name__, "语料行必须是 object")
            continue
        case = str(row.get("case") or case)
        if str(row.get("family") or "") != FAMILY_ID:
            checks.expect(case, False, FAMILY_ID, row.get("family"),
                          "row.family 与族 id 不一致（结构错误）")
            continue
        inp = row.get("input") if isinstance(row.get("input"), dict) else {}
        exp = row.get("expect") if isinstance(row.get("expect"), dict) else {}
        mr = inp.get("mr", inp.get("relation", inp.get("mr_id")))
        mr = str(mr) if mr is not None else ""
        if mr not in MR_IDS:
            checks.expect(case, False, "mr∈MR1..MR6", mr, "未知变形关系（行结构错误）")
            continue
        mr_stats[mr]["rows"] += 1
        params = inp.get("params")
        try:
            res = _dispatch(mr, copy.deepcopy(params), cfg, bag, base_ts)
        except Exception as exc:                   # noqa: BLE001
            checks.expect(case, False, "MR 可执行",
                          "异常：" + type(exc).__name__ + ": " + str(exc),
                          "语料 params 结构错误")
            mr_stats[mr]["fail"] += 1
            failures_detail.append({"case": case, "mr": mr, "detail": str(exc)})
            continue
        want = bool(exp.get("holds", True))
        ok = bool(res.get("holds")) == want
        checks.expect(case, ok, want, res.get("holds"),
                      "expect.holds；%s" % res.get("detail"))
        mr_stats[mr]["pass" if ok else "fail"] += 1
        mr_stats[mr]["details"].append({"case": case, "holds": bool(res.get("holds")),
                                        "detail": res.get("detail")})
        if not ok:
            failures_detail.append({"case": case, "mr": mr, "detail": res.get("detail")})
        if len(samples) < 10:
            samples.append({"case": case, "mr": mr, "params": params,
                            "holds": bool(res.get("holds")),
                            "detail": res.get("detail"), "tag": "row"})

    # ── 3) INV-13：相变映射精确（对所有观测点逐点核验）────
    obs = bag["obs"]
    mapping_bad = []
    for o in obs:
        if o.get("ratio_raw") is None or o.get("phase") is None:
            mapping_bad.append(o)
            continue
        expected = _expected_phase(cfg, o["used"], o["window"])
        if str(o["phase"]) != expected:
            mapping_bad.append({**o, "expected_phase": expected})
    ratio_max = float(cfg["control"]["ratioMax"])
    bound_bad = [o for o in obs
                 if o.get("ratio") is not None
                 and (not _finite(o["ratio"]) or float(o["ratio"]) < 0
                      or float(o["ratio"]) > ratio_max + 1e-9)]
    phase_bad = [o for o in obs if o.get("phase") not in PHASE_RANK
                 and o.get("phase") is not None]

    # ── 4) 聚合 ──────────────────────────────────────────
    mr_summary = {
        mr: {"cases": s["builtin"] + s["rows"], "pass": s["pass"], "fail": s["fail"],
             "builtin": s["builtin"], "rows": s["rows"]}
        for mr, s in mr_stats.items()
    }
    all_cases_ok = all(mr_summary[mr]["fail"] == 0 and mr_summary[mr]["cases"] > 0
                       for mr in MR_IDS)
    inv15_pass = bool(all_cases_ok)
    inv13_pass = not mapping_bad and not phase_bad
    inv10_pass = not bound_bad

    metrics = {
        "rows": len(rows),
        "builtin_cases": len(BUILTIN_CASES),
        "cases_total": checks.cases,
        "mr_summary": mr_summary,
        "observations": len(obs),
        "mapping_violations": len(mapping_bad),
        "bound_violations": len(bound_bad),
        "phase_violations": len(phase_bad),
        "permutation_algorithm": "sorted(range(n), key=sha256(f'{seed}:{i}'))",
        "mr3_permutation_pairs": len(bag["mr3"]),
        "mr5_boundary_flips": len(bag["mr5"]),
        "mr6_seed_sequences": len(bag["mr6"]),
        "seed_used": seed,
    }
    rates = {
        "mr_holds": {"num": sum(s["pass"] for s in mr_summary.values()),
                     "den": sum(s["cases"] for s in mr_summary.values())},
        "mr1_scale_invariance": _mr_rate(mr_summary, "MR1"),
        "mr2_monotone": _mr_rate(mr_summary, "MR2"),
        "mr3_permutation_equivalence": _mr_rate(mr_summary, "MR3"),
        "mr4_injection_permutation": _mr_rate(mr_summary, "MR4"),
        "mr5_boundary_flip": _mr_rate(mr_summary, "MR5"),
        "mr6_seed_stability": _mr_rate(mr_summary, "MR6"),
        "phase_mapping_exact": {"num": len(obs) - len(mapping_bad), "den": len(obs)},
        "ratio_bounded": {"num": len(obs) - len(bound_bad), "den": len(obs)},
    }

    invariants = {
        "INV-15": {
            "pass": inv15_pass,
            "detail": ("六条 MR：%s；失败 case=%d"
                       % ("，".join("%s %d/%d" % (mr, mr_summary[mr]["pass"],
                                                   mr_summary[mr]["cases"])
                                    for mr in MR_IDS),
                          sum(mr_summary[mr]["fail"] for mr in MR_IDS))),
        },
        "INV-10": {
            "pass": inv10_pass,
            "detail": ("观测点 %d 个：ratio 越界/非有限 = %d（ratio∈[0,%.2f]）"
                       % (len(obs), len(bound_bad), ratio_max)),
        },
        "INV-13": {
            "pass": inv13_pass,
            "detail": ("相变映射逐点核验 %d 个观测：映射错误 = %d；phase 非闭集 = %d"
                       "（shift=%.2f、critical=%.2f）"
                       % (len(obs), len(mapping_bad), len(phase_bad),
                          float(cfg["control"]["phaseShiftRatio"]),
                          float(cfg["control"]["phaseShiftHardRatio"]))),
        },
    }
    if mapping_bad:
        notes.append("INV-13 映射错误样本：" + str(mapping_bad[:3]))
    if bound_bad:
        notes.append("INV-10 越界样本：" + str(bound_bad[:3]))
    if failures_detail:
        notes.append("MR 失败明细：" + str(failures_detail[:5]))

    # ── 5) 诚实边界 ──────────────────────────────────────
    notes.extend([
        "MR3 的置换等价在参考实现里是**构造性成立**：engine.model.Store.all() 按 id 排序"
        "返回，插入顺序本就不参与打分/排序；它证明的是'实现未引入顺序依赖'，"
        "不等于'真实检索对插入顺序稳健'（non-claim，写进报告）。",
        "置换/抽样算法固定为 sha256(f\"{seed}:{i}\") 排序，逐字节可复现；"
        "未使用 time/random/uuid（docs/23 §5 硬约束）。",
        "MR1/MR2/MR5 观测用**新建** PhaseShiftMonitor，避免 trend 历史影响；"
        "phase 只由 ratio 决定，trend 只影响 anchor_required（不在本族断言范围）。",
        "MR2 若语料 used_list 非升序，本族按升序处理并在 detail 注明；"
        "单调关系只在有序输入上有意义。",
        "六条 MR 只在 engine 参考实现上验证，不含模型/真实流量（docs/23 §9 不覆盖清单）。",
    ])

    return {
        "checks": checks.as_dict(),
        "metrics": metrics,
        "rates": rates,
        "samples": samples,
        "notes": notes,
        "invariants": invariants,
        "skipped": False,
        "skip_reason": None,
    }


def _mr_rate(mr_summary: dict, mr: str) -> dict:
    slot = mr_summary.get(mr) or {}
    return {"num": int(slot.get("pass", 0)), "den": int(slot.get("cases", 0))}
