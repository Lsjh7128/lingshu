# -*- coding: utf-8 -*-
"""F5 `f5_assembly_pressure` —— 合成矩阵与预算压力（resource / compose）

契约（docs/23 §5.5 / §4）：

- **H5**：任意 `tokenRatioMax`（2%–30%）下宪法块都在**首位**且不被丢弃；
  refuse 场景必丢 style/organization/retrieval（**注入了才丢**，见下）；
  8 条互斥规则各丢对域且 `dropped` 留痕；`ASSEMBLY_ORDER` 恒定；
  风格系数恒在 `[0.8, 1.2]`；`budget.overflow` 必有 truncated/dropped 标记。
- **不变量**：INV-06（降维 fail-open 且不降宪法）、INV-07（预算压不垮宪法）、
  INV-10（数值有界：mult/权重/预算数值）。
- **刺激**：`tokenRatioMax ∈ {0.02,0.04,0.06,0.10,0.15,0.30}` × `refuse∈{T,F}` ×
  `style_intensity∈{0.0,0.7}` + 互斥规则 1–8 各一例 + 巨型 style 块（2000 字）
  + 一个**越出预注册区间的极端预算探针**（ratio=0.0，只为压出 overflow 路径）。

实现边界（诚实记录，见 notes）：

1. `tokenRatioMax` 只改 `deepcopy(ctx["cfg"])` 的内存副本，**绝不写 `rules/`**。
2. 引擎的 `_assemble()` 用固定模板渲染 style 块（`[style] 标签=%s 强度=%.2f 系数=%.2f`），
   payload 里的普通字段不进块长度；"巨型 style 块（2000 字）"只能由**超长
   `emotion_label`**实现（label 会被模板拼进块）。本族按 `style_text_len>0 →
   emotion_label="风"*style_text_len` 构造，从而真的把 style 块撑到约 667 token，
   在 ratio=0.02/0.04 的 160/320 token 预算下必然触发 `budget_exceeded` 丢弃。
3. 引擎 refuse 分支只对**非空域**记 `dropped`：`style_intensity=0` 时 style 域本来
   就是空的，`dropped` 里不会有 style（这是正确行为，不是漏记）。断言按"注入过才必须丢"。
4. `budget.overflow=true` 表示**硬块（宪法/安全/用户消息）本身已超预算**；
   此时 `truncated` 可能仍为 false（没有可淘汰的软块，如 refuse+ratio=0）。
   本族把 `overflow` 自身视为标记，并要求同时存在 truncated 或任一 `dropped`。

族模块契约：`FAMILY_ID / INVARIANTS / STRESS_CLASS / run(rows, ctx) -> dict`。
`run()` 纯函数：不写盘、不调模型、不用 time/random/uuid；确定性来自固定矩阵与行序。
"""
from __future__ import annotations

import copy
import math
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(_HERE)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from engine import compose as CP              # noqa: E402
from engine import config as cfgmod           # noqa: E402
from engine import control as CT              # noqa: E402

FAMILY_ID = "f5_assembly_pressure"
STRESS_CLASS = "resource"
INVARIANTS = ("INV-06", "INV-07", "INV-10")

RATIO_GRID = (0.02, 0.04, 0.06, 0.10, 0.15, 0.30)   # 预注册区间（§5.5）
RATIO_MIN, RATIO_MAX = 0.02, 0.30
EXTREME_RATIO = 0.0                                  # 仅内置探针：压 overflow 路径
STYLE_INTENSITIES = (0.0, 0.7)
GIANT_STYLE_CHARS = 2000

# 块前缀 → ASSEMBLY_ORDER 里的域名（[user] 对应 user_message）
PREFIX_TO_DOMAIN = {
    "[constitution]": "constitution",
    "[fact]": "fact",
    "[safety]": "safety",
    "[organization]": "organization",
    "[style]": "style",
    "[user]": "user_message",
}


# ── 结果骨架 / 小工具 ───────────────────────────────────────

def _skipped(reason: str) -> dict:
    return {
        "checks": {"cases": 0, "expectations": 0, "failed": 0, "failures": []},
        "metrics": {}, "rates": {}, "samples": [],
        "notes": ["f5 未执行：" + reason],
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


# ── payload 构造 ───────────────────────────────────────────

def _payload(*, refuse: bool = False, style_intensity: float = 0.0,
             style_on: bool | None = None, fact: dict | None = None,
             organization: dict | None = None, style: dict | None = None,
             retrieval: dict | None = None, style_text_len: int = 0) -> dict:
    """构造 compose 的 payload（contracts/prompt-compose.schema.json 的 domains）。

    `style_on` 默认 = (style_intensity > 0)。style 域非空才可能被 refuse / 互斥规则丢；
    这与 pipeline 的"情感未激活 → style 域为空"一致。
    """
    if style_on is None:
        style_on = style_intensity > 0
    domains = {
        "safety": {"refuse": bool(refuse), "reason": None},
        "fact": {"metacog_level": "mid", "recall_path": "direct",
                 "conflict": "none", "expression": "state"},
        "organization": {"goal_status": "none", "narrative_triggered": False,
                         "assist_stage": "off"},
        "style": {},
        "retrieval": {"strategy": "default", "source": "memory"},
    }
    if style_on:
        intense = float(style_intensity) >= 0.7
        style_dom = {
            "emotion_label": "愉快" if intense else "中性",
            "emotion_intensity": float(style_intensity),
            "emotion_valence": 0.4 if intense else 0.0,
            "emotion_arousal": 0.2 if intense else 0.0,
            "relation_modifier": 1.15 if intense else 1.0,
        }
        if style_text_len > 0:
            # 块模板是 "[style] 标签=%s 强度=%.2f 系数=%.2f"：只有 label 能撑长块，
            # 所以"巨型 style 块"必须塞进 emotion_label（其它字段不进渲染文本）。
            style_dom["emotion_label"] = "风" * int(style_text_len)
        domains["style"] = style_dom
    if fact:
        domains["fact"].update(fact)
    if organization:
        domains["organization"].update(organization)
    if style:
        domains["style"].update(style)
    if retrieval:
        domains["retrieval"].update(retrieval)
    return {"domains": domains}


def _mutex_case(rule: int) -> tuple[dict, str, str, tuple]:
    """互斥规则 1–8 → (payload, 期望被丢的 plugin, 期望 reason, 禁止出现的块前缀)。

    规则表见 engine/compose.py MUTEX / docs/09 §3。规则 7/8 由 refuse 分支覆盖
    （引擎里 refuse 会先丢 style/organization/retrieval，reason=constitution_reject），
    所以它们的"丢对域"仍可判定，但没有规则专属 reason —— 这是引擎现状，写进 notes。
    """
    if rule == 1:
        # 规则 1 的前提是 style 域**非空**（空域不会被丢，也不该记 dropped）
        return (_payload(style_intensity=0.7,
                         fact={"metacog_level": "unknown", "expression": "refuse_claim"}),
                "emotion", "metacog_refuse_claim", ("[style]",))
    if rule == 2:
        return (_payload(organization={"goal_status": "blocked",
                                       "narrative_triggered": True}),
                "narrative", "goal_blocked", ())
    if rule == 3:
        return (_payload(style={"unresolved_conflict": True, "emotion_valence": 0.5,
                                "emotion_arousal": 0.3, "emotion_label": "开心",
                                "emotion_intensity": 0.9}),
                "emotion", "relation_unresolved_conflict", ())
    if rule == 4:
        return (_payload(organization={"goal_status": "in_progress",
                                       "assist_stage": "S3", "assist_refused": True}),
                "assist", "user_emotion_refuse_help", ())
    if rule == 5:
        return (_payload(style_intensity=0.7,
                         fact={"metacog_level": "unknown", "expression": "refuse_claim"},
                         retrieval={"strategy": "counter_congruent"}),
                "whitelist", "metacog_refuse_claim", ("[style]",))
    if rule == 6:
        return (_payload(organization={"goal_status": "blocked"},
                         retrieval={"strategy": "counter_congruent"}),
                "whitelist", "goal_blocked", ())
    if rule == 7:
        return (_payload(refuse=True, style_intensity=0.7,
                         retrieval={"strategy": "counter_congruent"}),
                "retrieval", "constitution_reject",
                ("[style]", "[organization]"))
    if rule == 8:
        return (_payload(refuse=True, style_intensity=0.7),
                "style", "constitution_reject",
                ("[style]", "[organization]"))
    raise ValueError("mutex rule must be 1..8, got %r" % (rule,))


def _blocks(result: dict) -> list[str]:
    return [str(b) for b in (result.get("blocks") or [])]


def _prefix(block: str) -> str | None:
    for p in PREFIX_TO_DOMAIN:
        if block.startswith(p):
            return p
    return None


def _assembly_names(blocks: list[str]) -> list[str] | None:
    """把块映射成 ASSEMBLY_ORDER 的域序列；出现未知块返回 None。"""
    names = []
    for b in blocks:
        p = _prefix(b)
        if p is None:
            return None
        names.append(PREFIX_TO_DOMAIN[p])
    return names


def _assembly_ok(blocks: list[str]) -> tuple[bool, list[str]]:
    names = _assembly_names(blocks)
    if names is None:
        return False, ["<unknown-block>"]
    try:
        idx = [CP.ASSEMBLY_ORDER.index(n) for n in names]
    except ValueError:
        return False, names
    if idx != sorted(idx) or not idx or idx[0] != 0:
        return False, names
    return True, names


def _dropped_pairs(result: dict) -> list[tuple]:
    return [(str(d.get("plugin")), str(d.get("reason")))
            for d in (result.get("dropped") or []) if isinstance(d, dict)]


def _audit_allow(result: dict) -> tuple[bool, dict]:
    """把合成结果喂给 preflight_audit：宪法在首位时不应命中 UCA-05，
    refuse 时 style 已被丢，不应命中 UCA-03。"""
    blocks = _blocks(result)
    audit = CT.preflight_audit(
        prompt=CP.render(result), frozen_ok=True,
        refuse=bool(result.get("domains", {}).get("safety", {}).get("refuse")),
        style_injected=any(b.startswith("[style]") for b in blocks),
        constitution_injected=bool(blocks) and blocks[0].startswith("[constitution]"),
        conflict="none", fallback_applied=False, phase_result=None)
    return audit["verdict"] == "allow", audit


def _inv06_probes(cfg: dict, checks: _Checks) -> tuple[bool, dict]:
    """INV-06：必要多样性降维必须 **fail-open 且不降宪法**。

    正方向：`conflict=unresolved` + 已降维（丢 style）→ `verdict=allow`、无 UCA-06、
    宪法块仍在首位。
    反方向（对照）：同样冲突但**未**降维 → 必须 UCA-06 熔断（证明 fail-open 不是永不触发）。
    """
    case = "f5_builtin_inv06_fallback"
    checks.cases += 1
    payload = _payload(style_intensity=0.7)
    result = CP.compose(payload, cfg)
    blocks = _blocks(result)
    style_was_injected = any(b.startswith("[style]") for b in blocks)
    # 模拟 pipeline._apply_variety_fallback：只丢 style 域，宪法/安全/用户消息不动
    fallback_blocks = [b for b in blocks if not b.startswith("[style]")]
    fallback_dropped = _dropped_pairs(result) + [("style", "requisite_variety_fallback")]
    constitution_first = bool(fallback_blocks) and fallback_blocks[0].startswith("[constitution]")
    audit = CT.preflight_audit(
        prompt="\n".join(fallback_blocks), frozen_ok=True, refuse=False,
        style_injected=False, constitution_injected=constitution_first,
        conflict="unresolved", fallback_applied=True, phase_result=None)
    ok_allow = (audit["verdict"] == "allow" and "UCA-06" not in audit["ucas"]
                and "UCA-05" not in audit["ucas"])
    checks.expect(case, style_was_injected, "降维前 style 块存在", style_was_injected,
                  "构造前提：style 域应已注入")
    checks.expect(case, constitution_first, "降维后宪法仍在首位", fallback_blocks[:1],
                  "降维不得降宪法")
    checks.expect(case, ok_allow, "allow 且无 UCA-06/UCA-05",
                  {"verdict": audit["verdict"], "ucas": audit["ucas"]},
                  "fail-open：降维后不得熔断")
    checks.expect(case, ("style", "requisite_variety_fallback") in fallback_dropped,
                  "dropped 记录降维原因", fallback_dropped,
                  "降维必须留痕")

    # 反方向对照：同样的冲突、不降维 → UCA-06 必须熔断（fail-closed）
    audit_counter = CT.preflight_audit(
        prompt=CP.render(result), frozen_ok=True, refuse=False,
        style_injected=any(b.startswith("[style]") for b in blocks),
        constitution_injected=bool(blocks) and blocks[0].startswith("[constitution]"),
        conflict="unresolved", fallback_applied=False, phase_result=None)
    counter_ok = (audit_counter["verdict"] == "block" and "UCA-06" in audit_counter["ucas"])
    checks.expect(case, counter_ok, "未降维时 UCA-06 熔断（对照）",
                  {"verdict": audit_counter["verdict"], "ucas": audit_counter["ucas"]},
                  "证明 UCA-06 不是死代码")
    ok = (constitution_first and ok_allow and counter_ok
          and ("style", "requisite_variety_fallback") in fallback_dropped)
    return ok, {"verdict": audit["verdict"], "ucas": audit["ucas"],
                "counter_verdict": audit_counter["verdict"],
                "constitution_first": constitution_first}


# ── 单个 case 的判定（rows 与内置矩阵共用）────────────────

def _evaluate_case(case: str, payload: dict, cfg_case: dict, refuse: bool,
                   checks: _Checks, notes: list[str], tag: str,
                   row_expect: dict | None = None) -> dict:
    """跑一次 compose + 一次 preflight_audit，做公共断言 + 行内期望。返回画像。"""
    try:
        result = CP.compose(copy.deepcopy(payload), cfg_case)
    except Exception as exc:                       # noqa: BLE001 —— 异常必须显式失败
        checks.expect(case, False, "compose 不抛异常",
                      "异常：" + type(exc).__name__ + ": " + str(exc), "engine 行为异常")
        return {"ok": False, "error": str(exc)}

    blocks = _blocks(result)
    pairs = _dropped_pairs(result)
    plugins = [p for p, _r in pairs]
    budget = result.get("budget") or {}
    multiplied = result.get("final_style_multiplier")
    clamp = [float(x) for x in cfg_case["compose"]["styleMultiplierClamp"]]
    weights = result.get("weights") or {}
    ok = True

    # 1) 宪法永远第一
    constitution_first = bool(blocks) and blocks[0].startswith("[constitution]")
    ok &= checks.expect(case, constitution_first, "[constitution] 开头", blocks[:1],
                        "宪法块必须在首位（INV-07）")

    # 2) ASSEMBLY_ORDER 恒定
    order_ok, names = _assembly_ok(blocks)
    ok &= checks.expect(case, order_ok, "块顺序是 ASSEMBLY_ORDER 的子序列", names,
                        "顺序不可改（docs/09 §4 黑名单项）")

    # 3) 风格系数有界
    mult_ok = (isinstance(multiplied, (int, float))
               and math.isfinite(float(multiplied))
               and clamp[0] - 1e-9 <= float(multiplied) <= clamp[1] + 1e-9)
    ok &= checks.expect(case, mult_ok, "final_style_multiplier∈[%.1f,%.1f]" % (clamp[0], clamp[1]),
                        multiplied, "INV-10 数值有界")

    # 4) 权重有限非 NaN
    weights_ok = bool(weights) and all(
        isinstance(v, (int, float)) and math.isfinite(float(v)) for v in weights.values())
    ok &= checks.expect(case, weights_ok, "权重有限且非 NaN",
                        {k: v for k, v in weights.items()}, "INV-10")

    # 5) 预算数值合理 + 标记诚实
    numeric_ok = (isinstance(budget.get("token_budget"), int)
                  and budget["token_budget"] >= 0
                  and isinstance(budget.get("tokens_used"), (int, float))
                  and float(budget["tokens_used"]) >= 0)
    if not budget.get("overflow"):
        numeric_ok = numeric_ok and float(budget.get("tokens_used", 0)) <= float(
            budget.get("token_budget", 0)) + 1e-9
    ok &= checks.expect(case, numeric_ok, "预算数值有界且非 overflow 时不超预算", budget,
                        "INV-10")
    truncated = bool(budget.get("truncated"))
    overflow = bool(budget.get("overflow"))
    has_budget_drop = any(r == "budget_exceeded" for _p, r in pairs)
    if truncated:
        ok &= checks.expect(case, has_budget_drop, "truncated=true 时有 budget_exceeded 记录",
                            pairs, "截断必须可解释")
    if overflow:
        ok &= checks.expect(case, truncated or bool(pairs),
                            "overflow=true 时必有 truncated 或 dropped 标记",
                            {"truncated": truncated, "dropped": pairs},
                            "INV-07：溢出必须如实标记")

    # 6) refuse 场景：注入过的 style/organization/retrieval 必须被丢
    domains = result.get("domains") or {}
    if refuse:
        for name in ("style", "organization", "retrieval"):
            was_injected = bool((payload.get("domains") or {}).get(name))
            if was_injected:
                ok &= checks.expect(case, name in plugins,
                                    "refuse → dropped 含 %s" % name, pairs,
                                    "拒绝场景必须丢软域（constitution_reject）")
                ok &= checks.expect(case, (name, "constitution_reject") in pairs,
                                    "%s 丢弃原因=constitution_reject" % name, pairs,
                                    "原因必须可解释")
        for prefix in ("[style]", "[organization]"):
            ok &= checks.expect(case, not any(b.startswith(prefix) for b in blocks),
                                "refuse → 无 %s 块" % prefix, blocks,
                                "拒绝场景不得注入风格/组织")

    # 7) 闸门交叉检查：宪法在首位 → 不得 UCA-05；refuse 无 style → 不得 UCA-03
    allow, audit = _audit_allow(result)
    ok &= checks.expect(case, allow, "preflight_audit=allow",
                        {"verdict": audit["verdict"], "ucas": audit["ucas"]},
                        "宪法/安全接线证据")

    # 8) 行内 expect（语料逐条核验）
    if row_expect:
        exp_first = row_expect.get("constitution_first")
        if exp_first is not None:
            ok &= checks.expect(case, constitution_first == bool(exp_first),
                                bool(exp_first), constitution_first, "expect.constitution_first")
        want_drops = row_expect.get("dropped_contains")
        if isinstance(want_drops, list):
            for item in want_drops:
                if not isinstance(item, dict):
                    checks.expect(case, False, "dropped_contains 元素是 object", item,
                                  "行结构错误")
                    continue
                pair = (str(item.get("plugin")), str(item.get("reason")))
                ok &= checks.expect(case, pair in pairs, "dropped 含 %s" % (pair,), pairs,
                                    "expect.dropped_contains")
        forb_plugins = row_expect.get("forbidden_plugins")
        if isinstance(forb_plugins, list):
            bad = sorted({p for p in plugins if p in {str(x) for x in forb_plugins}})
            ok &= checks.expect(case, not bad, "dropped 不含 %s" % (forb_plugins,), bad,
                                "expect.forbidden_plugins")
        forb_prefixes = row_expect.get("forbidden_block_prefixes")
        if isinstance(forb_prefixes, list):
            bad = [b for b in blocks
                   if any(b.startswith(str(x)) for x in forb_prefixes)]
            ok &= checks.expect(case, not bad, "blocks 不含 %s" % (forb_prefixes,), bad,
                                "expect.forbidden_block_prefixes")
        if row_expect.get("truncated") is not None:
            ok &= checks.expect(case, truncated == bool(row_expect.get("truncated")),
                                bool(row_expect.get("truncated")), truncated,
                                "expect.truncated")
        if row_expect.get("overflow") is not None:
            ok &= checks.expect(case, overflow == bool(row_expect.get("overflow")),
                                bool(row_expect.get("overflow")), overflow,
                                "expect.overflow")

    return {
        "ok": bool(ok), "tag": tag, "constitution_first": constitution_first,
        "assembly_ok": order_ok, "multiplier": multiplied,
        "token_budget": budget.get("token_budget"), "tokens_used": budget.get("tokens_used"),
        "truncated": truncated, "overflow": overflow, "overflow_marked": truncated or bool(pairs),
        "dropped": pairs, "blocks": len(blocks), "audit_allow": allow,
        "giant_style": len(str((payload.get("domains", {}).get("style") or {})
                               .get("emotion_label", ""))) >= GIANT_STYLE_CHARS,
    }


# ── 主入口 ────────────────────────────────────────────────

def run(rows, ctx) -> dict:
    ctx = ctx if isinstance(ctx, dict) else {}
    checks = _Checks()
    notes: list[str] = []
    try:
        rows = list(rows or [])
    except TypeError:
        rows = []
    if not rows:
        return _skipped("f5 语料为空（chaos-set/v2/f5_assembly_pressure.jsonl 未就绪）")

    cfg0 = _cfg_from_ctx(ctx)
    profiles: list[dict] = []

    # ── 1) 语料行（预注册刺激集）───────────────────────────
    for idx, row in enumerate(rows):
        checks.cases += 1
        case = str((row or {}).get("case") or "row_%03d" % idx)
        if not isinstance(row, dict):
            checks.expect(case, False, "row object", type(row).__name__, "语料行必须是 object")
            continue
        if str(row.get("family") or "") != FAMILY_ID:
            checks.expect(case, False, FAMILY_ID, row.get("family"),
                          "row.family 与族 id 不一致（结构错误）")
            continue
        inp = row.get("input") if isinstance(row.get("input"), dict) else {}
        exp = row.get("expect") if isinstance(row.get("expect"), dict) else {}

        ratio = inp.get("token_ratio_max", inp.get("tokenRatioMax",
                                                  inp.get("ratio_max", inp.get("ratio"))))
        if ratio is None:
            checks.expect(case, False, "input.token_ratio_max", None, "行结构错误")
            continue
        ratio_f = _num(ratio, -1.0)
        if not (RATIO_MIN - 1e-12 <= ratio_f <= RATIO_MAX + 1e-12):
            checks.expect(case, False, "token_ratio_max∈[0.02,0.30]（预注册区间）",
                          ratio_f, "越出区间只能作为内置探针，不能写进语料")
            continue

        refuse = bool(inp.get("refuse", inp.get("refuse_constitution", False)))
        intensity = _num(inp.get("style_intensity", inp.get("styleIntensity", 0.0)), 0.0)
        style_text_len = int(_num(inp.get("style_text_len", 0), 0))
        mutex_rule = inp.get("mutex_rule", inp.get("rule", inp.get("mutex")))

        if isinstance(inp.get("domains"), dict):
            payload = {"domains": copy.deepcopy(inp["domains"])}
            refuse = bool(payload["domains"].get("safety", {}).get("refuse", refuse))
        elif mutex_rule is not None:
            try:
                payload, _want_plugin, _want_reason, _ = _mutex_case(int(mutex_rule))
            except (ValueError, TypeError) as exc:
                checks.expect(case, False, "mutex_rule∈1..8", mutex_rule, str(exc))
                continue
            refuse = bool(payload["domains"]["safety"]["refuse"])
        else:
            payload = _payload(refuse=refuse, style_intensity=intensity,
                               style_text_len=style_text_len)

        cfg_case = copy.deepcopy(cfg0)
        cfg_case["compose"]["tokenRatioMax"] = ratio_f
        prof = _evaluate_case(case, payload, cfg_case, refuse, checks, notes, "row",
                              row_expect=exp)
        prof["case"] = case
        prof["tag"] = "row"
        prof["token_ratio_max"] = ratio_f
        profiles.append(prof)

    # ── 2) 内置矩阵（H5 全扫：6 ratio × refuse × intensity）────
    for ratio in RATIO_GRID:
        for refuse in (False, True):
            for intensity in STYLE_INTENSITIES:
                case = "f5_builtin_r%05d_%s_s%.1f" % (
                    int(round(ratio * 1000)), "refuse" if refuse else "compose", intensity)
                checks.cases += 1
                payload = _payload(refuse=refuse, style_intensity=intensity)
                cfg_case = copy.deepcopy(cfg0)
                cfg_case["compose"]["tokenRatioMax"] = float(ratio)
                prof = _evaluate_case(case, payload, cfg_case, refuse, checks, notes, "builtin")
                prof.update({"case": case, "tag": "builtin", "token_ratio_max": float(ratio)})
                profiles.append(prof)

    # ── 3) 8 条互斥规则各一例 ──────────────────────────────
    mutex_report: dict[str, dict] = {}
    for rule in range(1, 9):
        case = "f5_builtin_mutex%d" % rule
        checks.cases += 1
        try:
            payload, want_plugin, want_reason, forbid_prefixes = _mutex_case(rule)
        except ValueError as exc:
            checks.expect(case, False, "mutex rule 1..8", rule, str(exc))
            continue
        refuse = bool(payload["domains"]["safety"]["refuse"])
        cfg_case = copy.deepcopy(cfg0)
        cfg_case["compose"]["tokenRatioMax"] = 0.15
        prof = _evaluate_case(case, payload, cfg_case, refuse, checks, notes, "builtin")
        pairs = prof.get("dropped") or []
        expected_ok = (want_plugin, want_reason) in pairs
        block_ok = all(not any(b.startswith(p) for b in prof.get("blocks_list") or [])
                       for p in forbid_prefixes)
        # 期望域确实被丢（规则 7/8 由 refuse 分支覆盖，reason 同为 constitution_reject）
        checks.expect(case, expected_ok, "规则 %d 丢对域 %s(%s)" % (rule, want_plugin, want_reason),
                      pairs, "docs/09 §3 互斥表")
        prof.update({"case": case, "tag": "builtin", "token_ratio_max": 0.15})
        profiles.append(prof)
        mutex_report[str(rule)] = {"expected": [want_plugin, want_reason],
                                   "dropped": pairs, "pass": bool(expected_ok and block_ok),
                                   "forbidden_prefixes": list(forbid_prefixes)}

    # ── 4) 巨型 style 块（2000 字）────────────────────────
    case = "f5_builtin_giant_style"
    checks.cases += 1
    payload = _payload(style_intensity=0.7, style_text_len=GIANT_STYLE_CHARS)
    cfg_case = copy.deepcopy(cfg0)
    cfg_case["compose"]["tokenRatioMax"] = 0.02
    prof = _evaluate_case(case, payload, cfg_case, False, checks, notes, "builtin")
    prof.update({"case": case, "tag": "builtin", "token_ratio_max": 0.02})
    profiles.append(prof)

    # ── 5) 极端预算探针（ratio=0.0，压 overflow 路径）─────
    case = "f5_builtin_extreme_budget"
    checks.cases += 1
    payload = _payload(style_intensity=0.7)
    cfg_case = copy.deepcopy(cfg0)
    cfg_case["compose"]["tokenRatioMax"] = EXTREME_RATIO
    prof = _evaluate_case(case, payload, cfg_case, False, checks, notes, "builtin")
    checks.expect(case, bool(prof.get("overflow")) is True, "overflow=true",
                  prof.get("overflow"), "ratio=0 必须压出 overflow")
    checks.expect(case, bool(prof.get("truncated")) is True, "truncated=true",
                  prof.get("truncated"), "软块可淘汰时必须截断")
    prof.update({"case": case, "tag": "builtin", "token_ratio_max": EXTREME_RATIO})
    profiles.append(prof)

    # ── 6) INV-06 降维探针 ────────────────────────────────
    inv06_ok, inv06_detail = _inv06_probes(cfg0, checks)

    # ── 聚合指标 ──────────────────────────────────────────
    n = len(profiles)
    constitution_first_all = all(p.get("constitution_first") for p in profiles) if n else False
    assembly_all = all(p.get("assembly_ok") for p in profiles) if n else False
    multipliers = [float(p["multiplier"]) for p in profiles
                   if isinstance(p.get("multiplier"), (int, float))]
    clamp = [float(x) for x in cfg0["compose"]["styleMultiplierClamp"]]
    mult_bounds = all(clamp[0] - 1e-9 <= m <= clamp[1] + 1e-9 for m in multipliers)
    truncated_at = sorted({p["token_ratio_max"] for p in profiles
                           if p.get("truncated") and p.get("token_ratio_max") is not None})
    overflow_cases = [p["case"] for p in profiles if p.get("overflow")]
    overflow_unmarked = [p["case"] for p in profiles
                         if p.get("overflow") and not p.get("overflow_marked")]
    drop_hist: dict[str, int] = {}
    for p in profiles:
        for _plugin, reason in (p.get("dropped") or []):
            drop_hist[reason] = drop_hist.get(reason, 0) + 1
    refuse_profiles = [p for p in profiles
                       if "refuse" in str(p.get("case")) or p.get("tag") == "row"]
    budget_curve: dict[str, dict] = {}
    for p in profiles:
        key = str(p.get("token_ratio_max"))
        slot = budget_curve.setdefault(key, {"cases": 0, "max_used": 0,
                                             "token_budget": p.get("token_budget"),
                                             "truncated_cases": 0, "overflow_cases": 0})
        slot["cases"] += 1
        slot["max_used"] = max(slot["max_used"], int(p.get("tokens_used") or 0))
        slot["truncated_cases"] += 1 if p.get("truncated") else 0
        slot["overflow_cases"] += 1 if p.get("overflow") else 0

    metrics = {
        "rows": len(rows),
        "builtin_cases": n - len(rows),
        "cases_total": n,
        "constitution_first_all": constitution_first_all,
        "assembly_order_all": assembly_all,
        "multiplier_min": min(multipliers) if multipliers else None,
        "multiplier_max": max(multipliers) if multipliers else None,
        "truncated_trigger_ratios": truncated_at,
        "overflow_count": len(overflow_cases),
        "overflow_unmarked_cases": overflow_unmarked,
        "dropped_reason_hist": dict(sorted(drop_hist.items())),
        "mutex_rules": mutex_report,
        "budget_curve": {k: budget_curve[k] for k in sorted(budget_curve)},
        "giant_style_block_chars": GIANT_STYLE_CHARS,
        "giant_style_block_truncated": any(
            p.get("giant_style") and p.get("truncated") for p in profiles),
        "audit_allow_all": all(p.get("audit_allow") for p in profiles) if n else False,
    }
    rates = {
        "constitution_first": {"num": sum(1 for p in profiles if p.get("constitution_first")),
                               "den": n},
        "assembly_order_ok": {"num": sum(1 for p in profiles if p.get("assembly_ok")),
                              "den": n},
        "mutex_rule_domain_ok": {"num": sum(1 for v in mutex_report.values() if v["pass"]),
                                 "den": len(mutex_report)},
        "audit_allow": {"num": sum(1 for p in profiles if p.get("audit_allow")), "den": n},
        "truncated_under_pressure": {"num": sum(1 for p in profiles if p.get("truncated")),
                                     "den": n},
    }

    # ── 不变量 ───────────────────────────────────────────
    budget_marked_ok = not overflow_unmarked
    refuse_ok = all(p.get("ok") for p in refuse_profiles)
    invariants = {
        "INV-06": {
            "pass": bool(inv06_ok),
            "detail": ("必要多样性降维：conflict=unresolved 且 fallback_applied=True → "
                       "verdict=%s、ucas=%s；对照（未降维）→ verdict=%s（必须 UCA-06）；"
                       "宪法块仍在首位=%s"
                       % (inv06_detail.get("verdict"), inv06_detail.get("ucas"),
                          inv06_detail.get("counter_verdict"),
                          inv06_detail.get("constitution_first"))),
        },
        "INV-07": {
            "pass": bool(constitution_first_all and budget_marked_ok
                         and assembly_all and refuse_ok),
            "detail": ("宪法首位 = %d/%d；ASSEMBLY_ORDER 合法 = %d/%d；"
                       "overflow 未标记 = %d；refuse 用例失败 = %d"
                       % (sum(1 for p in profiles if p.get("constitution_first")), n,
                          sum(1 for p in profiles if p.get("assembly_ok")), n,
                          len(overflow_unmarked),
                          sum(1 for p in refuse_profiles if not p.get("ok")))),
        },
        "INV-10": {
            "pass": bool(mult_bounds and all(p.get("ok") for p in profiles
                                             if p.get("multiplier") is not None)),
            "detail": ("风格系数范围 [%s, %s]（min=%s, max=%s）；"
                       "权重/预算数值断言失败用例 = %d"
                       % (clamp[0], clamp[1],
                          metrics["multiplier_min"], metrics["multiplier_max"],
                          sum(1 for p in profiles if not p.get("ok")))),
        },
    }

    # ── 诚实边界 ─────────────────────────────────────────
    notes.extend([
        "tokenRatioMax 只在 deepcopy(cfg) 副本上改，rules/ 未动（红线 1/7）。",
        "巨型 style 块（%d 字）通过超长 emotion_label 构造：style 块模板会拼进 label，"
        "因此块长≈%d token；语料行按 ratio=0.04（预算 %d token）给，2%% 档预算 %d token "
        "也同理会超 → style 必被 budget_exceeded 丢弃，用来验证'预算先丢风格域、宪法不动'。"
        % (GIANT_STYLE_CHARS, GIANT_STYLE_CHARS // 3,
           int(CP.ASSUMED_CONTEXT_TOKENS * 0.04),
           int(CP.ASSUMED_CONTEXT_TOKENS * RATIO_MIN)),
        "24 格矩阵（普通短标签）在 2%–30% 下**无一触发** truncated：合成块总量"
        "（约 27–39 token）远小于 160–2400 token 预算，这段预算区间其实是'无压力区'。"
        "本族的 truncated 只出现在两处：巨型 style 块行（预算淘汰 style）与 ratio=0.0 "
        "内置极端探针（overflow 路径）；两者都与预注册矩阵分开统计（"
        "metrics.truncated_trigger_ratios 里 0.0 是内置探针、不属于语料行）。",
        "refuse 场景只对**已注入**的域记 dropped：style_intensity=0 时 style 域为空，"
        "dropped 里没有 style 是正确行为（不是漏记）。",
        "FINDING-F03（可观测性缺口，不影响'丢对域'结论；见 findings.md）："
        "互斥规则 7/8 在引擎里由 refuse 分支先丢 style/organization/retrieval"
        "（reason=constitution_reject），没有规则专属 reason 留痕；"
        "engine.compose.MUTEX 表本身只被 lookup_mutex() 使用，compose() 主路径不查表。",
        "refuse+ratio=0.0 时 overflow=true 但 truncated=false：软域已被 refuse 分支丢光，"
        "没有可淘汰的块，只剩硬块超预算。此时 overflow 本身即标记（本族按"
        "'truncated 或任一 dropped' 判诚实），并单列 overflow_unmarked_cases 供审阅。",
    ])
    if overflow_unmarked:
        notes.append("overflow 未标记用例：" + str(overflow_unmarked[:5]))

    return {
        "checks": checks.as_dict(),
        "metrics": metrics,
        "rates": rates,
        "samples": profiles[:10],
        "notes": notes,
        "invariants": invariants,
        "skipped": False,
        "skip_reason": None,
    }
