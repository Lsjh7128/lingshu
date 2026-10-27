# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 合成矩阵（docs/09）

实现范围：
- 五域分类 + 域内排序（数据表）
- 8 条互斥规则（含 v3.0 补的第 8 条：拒绝场景丢弃风格域）
- 未覆盖冲突的默认策略：丢弃低优先级域 + 记 `no_mutex_rule`
- 预算超限：先丢风格域尾部，**永不丢安全域**
- 拼装顺序固定（不可改）
- `dropped` 完整记录（可解释性的唯一证据）

刻意不做：真正的字符串模板渲染细节（保留 blocks 结构即可验证）。
"""
from __future__ import annotations

import math
from typing import Any

ASSEMBLY_ORDER = ("constitution", "fact", "safety", "organization", "style", "user_message")

DOMAIN_RANK = {"safety": 0, "fact": 1, "organization": 2, "style": 3, "retrieval": 4}

# docs/09 §3 互斥表（8 条；第 9 条 soft_buffer 见下，有独立的"不丢弃"语义）
MUTEX = [
    {"id": 1, "when": ("metacog.refuse_claim", "emotion.positive"), "drop": "emotion"},
    {"id": 2, "when": ("goal.blocked", "narrative.trigger"), "drop": "narrative"},
    {"id": 3, "when": ("relation.unresolved_conflict", "emotion.positive_high_arousal"),
     "drop": "emotion", "to": "中性"},
    {"id": 4, "when": ("user_emotion.refuse_help", "user_emotion.S3_assist"), "drop": "assist"},
    {"id": 5, "when": ("metacog.refuse_claim", "user_emotion.whitelist"), "drop": "whitelist"},
    {"id": 6, "when": ("goal.blocked", "user_emotion.whitelist"), "drop": "whitelist"},
    {"id": 7, "when": ("constitution.reject", "retrieval.strategy"), "drop": "retrieval"},
    {"id": 8, "when": ("constitution.reject", "style.domain"), "drop": "style"},
]

# ADR-014 §4.1：互斥表第 9 条（compose-matrix-3.1）。它**不丢弃**风格域，
# 而是把风格域重挂载为受约束的安全缓冲表达（警觉/困惑 + Safe_Redirect）。
# 只在 `safety.severity == "soft"`（且 immersiveEnabled）时生效；第 8 条原样保留，
# hard 路径与 v3.0 逐字节一致。
SOFT_BUFFER = {
    "id": 9,
    "conflict": ("constitution.reject", "style.domain"),
    "action": "soft_buffer",
    "notice": "Safe_Redirect",
    "emotion": ("警觉", "困惑"),
}

# ADR-014 §4.2 引擎侧精简模板（插件侧的 blocked 段更完整；两边共享同一语义）。
SAFE_REDIRECT_MARKER = "[System_Notice: Safe_Redirect]"
SAFE_REDIRECT_NOTICE = (
    "[System_Notice: Safe_Redirect] 本轮请求必须拒绝：不得执行、不得提供越界内容、"
    "不得给变通做法。表层保持当前人设（有称呼/说话习惯就继续用），用警觉或困惑的口吻"
    "一句拒绝（可加一句提醒或反问），不超过 2 句；不提系统/规则/政策/审查/本条通知，"
    "不解释拦截原因。"
)

SOFT_BUFFER_SAFETY_TEXT = "[safety] 拒绝（安全缓冲：不注入事实与内容组织）"

ASSUMED_CONTEXT_TOKENS = 8000   # 只用于把"注入占比"折算成 token 预算


def _est(text: str) -> int:
    return max(1, math.ceil(len(text) / 3))


def lookup_mutex(a: str, b: str) -> dict[str, Any] | None:
    for r in MUTEX:
        w = r["when"]
        if set(w) == {a, b}:
            return r
    return None


def compose(payload: dict[str, Any], cfg: dict[str, Any],
            forced_conflicts: list[tuple[str, str]] | None = None) -> dict[str, Any]:
    """给定各域的原始产出，返回合成结果（含 dropped 与 blocks）。

    payload 结构见 contracts/prompt-compose.schema.json 的 domains。
    ADR-014：`safety.severity == "soft"` 时执行互斥表第 9 条 soft_buffer——
    风格域不被丢弃，而是重挂载为受约束的警觉/困惑表达（强度 ≤
    `control.softBufferMaxIntensity`，`relation_modifier` 钳制到 [0.8, 1.0]），
    并在安全域块内追加 `[System_Notice: Safe_Redirect]`。
    不传 severity / severity != "soft" 时，行为与 compose-3.0 逐字节一致。
    """
    domains = {k: dict(v) for k, v in payload.get("domains", {}).items()}
    dropped: list[dict[str, str]] = []
    # 原始风格域：soft 场景要从这里重挂载（规则 1/3/8 可能已把它清空）
    orig_style = dict(domains.get("style") or {})

    def drop(plugin: str, reason: str) -> None:
        dropped.append({"plugin": plugin, "reason": reason})

    safety = domains.get("safety", {})
    fact = domains.get("fact", {})
    org = domains.get("organization", {})
    style = domains.get("style", {})
    retr = domains.get("retrieval", {})

    # ADR-014 §5.6：immersiveEnabled=false 时 soft 路径一律按 v3.0 hard 处理（可回滚）。
    # 第 9 条的冲突组合是 constitution.reject × style.domain，因此必须 refuse=true 才生效；
    # 只传 severity 没传 refuse 时不改写任何东西（防御性，避免误伤正常路径）。
    severity = safety.get("severity")
    immersive = bool((cfg.get("control") or {}).get("immersiveEnabled", True))
    soft = (severity == "soft") and immersive and bool(safety.get("refuse"))
    immersive_disabled = (severity == "soft") and not immersive

    refuse = bool(safety.get("refuse"))
    refuse_claim = fact.get("metacog_level") == "unknown" or fact.get("expression") == "refuse_claim"
    goal_blocked = org.get("goal_status") == "blocked"
    narrative = bool(org.get("narrative_triggered"))
    unresolved = bool(style.get("unresolved_conflict"))
    positive = style.get("emotion_valence", 0) > 0
    high_arousal = style.get("emotion_arousal", 0) >= 0.15
    whitelist = bool(retr.get("strategy") == "counter_congruent")

    # ── 8 条互斥规则
    if refuse:
        if style and not soft:
            drop("style", "constitution_reject")          # 规则 8（hard 路径）
            style = {}
        if org:
            drop("organization", "constitution_reject")   # 拒绝场景不注入内容组织
            org = {}
        if retr:
            drop("retrieval", "constitution_reject")      # 规则 7
            retr = {}
    if refuse_claim and style and not soft:
        drop("emotion", "metacog_refuse_claim")           # 规则 1
        style = {}
    if refuse_claim and whitelist:
        drop("whitelist", "metacog_refuse_claim")         # 规则 5
        whitelist = False
    if goal_blocked and narrative:
        drop("narrative", "goal_blocked")                 # 规则 2
        org = {**org, "narrative_triggered": False}
    if goal_blocked and whitelist:
        drop("whitelist", "goal_blocked")                 # 规则 6
        whitelist = False
    if unresolved and positive and high_arousal and not soft:
        drop("emotion", "relation_unresolved_conflict")   # 规则 3
        style = {**style, "emotion_label": "中性", "emotion_valence": 0.0}
    if org.get("assist_stage") == "S3" and org.get("assist_refused"):
        drop("assist", "user_emotion_refuse_help")        # 规则 4
        org = {**org, "assist_stage": "off"}

    # ── 未覆盖的冲突：丢低优先级域（默认策略）
    for a, b in (forced_conflicts or []):
        if lookup_mutex(a, b) is None:
            loser = _lower_priority(a, b)
            drop(loser.split(".")[0], "no_mutex_rule")

    # ── ADR-014 第 9 条：soft buffer 在规则 1–8 之后重挂载受约束的风格域
    # （保证不会被规则 1/8 连带丢弃；也保证 dropped 不记录 style —— 它没有被丢弃）。
    if soft:
        dropped = [d for d in dropped if d.get("plugin") not in ("style", "emotion")]
        style = _constrained_buffer_style(orig_style, cfg)

    # ── 全局工作空间：域权重动态竞争（不推翻互斥表，只决定"谁先被牺牲"）
    weights = _compute_weights(cfg, fact, org, style, refuse_claim, goal_blocked)

    # ── 风格系数（统一钳制 [0.8, 1.2]；soft 场景额外钳到 [0.8, 1.0]）
    mult = float(style.get("relation_modifier", 1.0))
    mult = max(0.8, min(1.2, mult))
    if soft:
        mult = max(0.8, min(1.0, mult))

    result = {
        "domains": {"safety": safety, "fact": fact, "organization": org,
                    "style": style, "retrieval": retr},
        "dropped": dropped,
        "final_style_multiplier": round(mult, 3),
        "compose_version": "compose-3.1",
        "weights": weights,
        "soft_buffer": _soft_buffer_result(style, soft, immersive_disabled),
    }
    result["blocks"] = _assemble(result, refuse, soft)
    result["budget"] = _apply_budget(result, cfg, soft)
    return result


def _constrained_buffer_style(style: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    """把风格域改写成"安全缓冲表达"（ADR-014 §5.3 三条不变量）。

    - `emotion_label ∈ {警觉, 困惑}`（不在闭集内的一律降为警觉）；
    - `emotion_intensity ≤ control.softBufferMaxIntensity`（无既有情绪时取上限，
      有则取两者较小值——只收不放）；
    - `relation_modifier ∈ [0.8, 1.0]`（不得借安全缓冲放大亲密感）；
    - 不保留正面情绪方向（`emotion_valence` 不得为正）。
    """
    c = cfg.get("control") or {}
    try:
        cap = float(c.get("softBufferMaxIntensity", 0.45))
    except (TypeError, ValueError):
        cap = 0.45
    label = style.get("emotion_label")
    if label not in SOFT_BUFFER["emotion"]:
        label = "警觉"
    try:
        raw_intensity = float(style.get("emotion_intensity", 0.0) or 0.0)
    except (TypeError, ValueError):
        raw_intensity = 0.0
    intensity = cap if raw_intensity <= 0.0 else min(cap, raw_intensity)
    try:
        rel = float(style.get("relation_modifier", 1.0))
    except (TypeError, ValueError):
        rel = 1.0
    rel = max(0.8, min(1.0, rel))
    try:
        valence = float(style.get("emotion_valence", 0.0) or 0.0)
    except (TypeError, ValueError):
        valence = 0.0
    out = dict(style)
    out.update({
        "emotion_label": label,
        "emotion_intensity": round(intensity, 3),
        "emotion_valence": round(min(0.0, valence), 3),
        "relation_modifier": round(rel, 3),
    })
    return out


def _soft_buffer_result(style: dict[str, Any], soft: bool,
                        immersive_disabled: bool) -> dict[str, Any]:
    """soft_buffer 可观测字段（ADR-014 §4.1：applied/reason/emotion_label/notice/intensity）。"""
    if soft and style:
        return {
            "applied": True,
            "reason": "constitution_reject_soft",
            "emotion_label": style.get("emotion_label"),
            "notice": SOFT_BUFFER["notice"],
            "intensity": round(float(style.get("emotion_intensity", 0.0)), 3),
        }
    return {
        "applied": False,
        "reason": "immersive_disabled" if immersive_disabled else None,
        "emotion_label": None,
        "notice": None,
        "intensity": 0.0,
    }


def _compute_weights(cfg: dict[str, Any], fact: dict, org: dict, style: dict,
                     refuse_claim: bool, goal_blocked: bool) -> dict[str, float]:
    """全局工作空间竞争：信号越强，域权重越高。

    约束：权重只影响**预算淘汰顺序**与可观测性，**不能**推翻互斥表与拒绝路径。
    """
    c = cfg["compose"]["weights"]
    w = {"fact": float(c["fact"]), "organization": float(c["organization"]),
         "style": float(c["style"]), "retrieval": float(c["retrieval"])}
    if refuse_claim:
        w["fact"] += float(c["boostFact"])
    if goal_blocked:
        w["organization"] += float(c["boostOrganization"])
    hi = float(style.get("emotion_intensity", 0.0) or 0.0)
    if hi >= float(c.get("styleBoostThreshold", 0.6)):      # v3.2：门槛收归 rules
        w["style"] += float(c["boostStyle"])
    return {k: round(v, 3) for k, v in w.items()}


def _lower_priority(a: str, b: str) -> str:
    return a if DOMAIN_RANK.get(a.split(".")[0], 9) > DOMAIN_RANK.get(b.split(".")[0], 9) else b


def _assemble(r: dict[str, Any], refuse: bool, soft: bool = False) -> list[str]:
    """按固定顺序拼装。空域不产生块。

    ADR-014 §4.1/§4.2：soft 时安全域块追加 `[System_Notice: Safe_Redirect]`
    （拼装顺序不变，仍是第 3 块；hard 路径文本逐字节不变）。
    """
    blocks: list[str] = ["[constitution] 原则 1-5"]
    fact = r["domains"]["fact"]
    if fact.get("metacog_level") not in (None, "high"):
        blocks.append("[fact] 置信度=%s 表达=%s" % (fact.get("metacog_level"),
                                                    fact.get("expression")))
    if refuse:
        if soft and r["domains"]["style"]:
            blocks.append(SOFT_BUFFER_SAFETY_TEXT + "\n" + SAFE_REDIRECT_NOTICE)
        else:
            blocks.append("[safety] 拒绝（不注入风格与内容组织）")
    org = r["domains"]["organization"]
    if org:
        blocks.append("[organization] 目标=%s 协助=%s" % (org.get("goal_status"),
                                                          org.get("assist_stage", "off")))
    style = r["domains"]["style"]
    if style:
        blocks.append("[style] 标签=%s 强度=%.2f 系数=%.2f"
                      % (style.get("emotion_label", "中性"),
                         float(style.get("emotion_intensity", 0.0)),
                         r["final_style_multiplier"]))
    blocks.append("[user] 当前消息")
    return blocks


def _apply_budget(r: dict[str, Any], cfg: dict[str, Any],
                  soft: bool = False) -> dict[str, Any]:
    """超预算时先丢风格域尾部，**永不丢安全域**。

    ADR-014：soft 场景下风格块是"人设不崩塌"的载体，不参与预算淘汰
    （soft_buffer 的 applied 不能与 blocks 互相矛盾）；hard 路径不变。
    """
    ratio_max = cfg["compose"]["tokenRatioMax"]
    budget = int(ASSUMED_CONTEXT_TOKENS * ratio_max)
    used = sum(_est(b) for b in r["blocks"])
    truncated = False
    soft_plugins = {"[style]": "style", "[organization]": "organization",
                    "[retrieval]": "retrieval"}
    weights = r.get("weights") or {}
    while used > budget:
        candidates = [(weights.get(soft_plugins[b[:b.index("]") + 1]], 0.0), i)
                      for i, b in enumerate(r["blocks"])
                      if b[:b.index("]") + 1] in soft_plugins
                      and soft_plugins[b[:b.index("]") + 1]] in weights
                      # soft：风格块受保护，不参与淘汰
                      and not (soft and soft_plugins[b[:b.index("]") + 1]] == "style")]
        if not candidates:
            break                       # 只剩硬块；安全域永不丢
        weight, idx = min(candidates)
        name = soft_plugins[r["blocks"][idx][:r["blocks"][idx].index("]") + 1]]
        r["blocks"].pop(idx)
        r["dropped"].append({"plugin": name, "reason": "budget_exceeded",
                             "weight": weight})
        truncated = True
        used = sum(_est(b) for b in r["blocks"])
    return {"token_budget": budget, "tokens_used": used,
            "truncated": truncated,
            # 只剩硬块（宪法/安全/用户消息）仍超预算时：不丢，但必须如实标记
            "overflow": used > budget}


def render(r: dict[str, Any]) -> str:
    """最终的 prompt 片段（顺序即 ASSEMBLY_ORDER）。"""
    return "\n".join(r["blocks"])
