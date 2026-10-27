# -*- coding: utf-8 -*-
"""redact.py —— 控制台唯一脱敏出口（API v1 §4）。

设计原则：**白名单，不是黑名单**。
    任何审计事件字段，只有出现在本文件的 `KIND_SAFE_FIELDS[kind]` 里才允许出网；
    记忆记录一条都不出网，只出聚合计数。
为什么这样设计：
    - 黑名单每漏一个字段（`preview` / `quoteHead` / `argsHead` / `rejected`…）就泄一次原文；
    - 白名单漏字段的后果是"少显示"，不是"多泄漏"。方向必须选保守的那个。
不变式：
    - `redact_event()` 的输出里**永远不出现** content / sourceQuote / quote / preview / subject /
      object / args / text 这些**数据字段键**（由测试逐键扫描保证）；
    - 字符串值只允许通过固定枚举或严格字符集的正则，不做"截断后原样输出"；
    - 任何输入（坏 `ts`、坏类型、超长值）都**不得抛异常**，也不得把原值带进异常/错误体。

> 注：HTTP 错误包固定用 `message` 键（`console/API.md §1`），值是**固定的错误码文案**，
> 不是从数据文件读出的字段；`FORBIDDEN_KEYS` 约束的是数据字段键。错误体**不得**包含异常原文，
> 否则等于给"唯一脱敏出口"开了一个旁路（独立复核 P1 实测过这一点）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone

# 项目统一时区固定 UTC+8（docs/14 §2 要求 iso 带明确偏移；本机无夏令时）。
TZ = timezone(timedelta(hours=8))

# 文件级/记录级永远禁止出网的键（前端不应看到；测试会扫描）。
FORBIDDEN_KEYS = frozenset({
    "content", "sourceQuote", "source_quote", "quote", "quoteHead", "preview",
    "subject", "object", "args", "arguments", "argsHead", "argsDigest", "text",
    "message", "evidenceQuote", "rejected", "keys", "objects", "sourceQuoteText",
})

# 只允许固定枚举的字段。
ENUMS = {
    "phase": {"normal", "shift", "critical", "unknown"},
    "verdict": {"allow", "block", "degrade", "unavailable", "unknown"},
    "level": {"unknown", "mid", "high"},
    "stage": {"cold", "warm", "regular", "unknown"},
    "failMode": {"closed", "open", "unknown"},
    "fail_mode": {"closed", "open", "unknown"},
    "kind_label": {"wrong", "missing", "should_not_recall", "drift", "overreach", "unknown"},
    "mode": {"active", "observe"},
    "type": None,  # 由 TOKEN_RE 校验
    "tool": None,
    "reason": None,
    "reason_code": None,
    "origin": None,
}

TOKEN_RE = re.compile(r"^[A-Za-z0-9_.:\-]{1,64}$")
UCA_RE = re.compile(r"^(?:T-)?UCA-[A-Za-z0-9\-]{1,24}$")
EVENT_RE = re.compile(r"^[a-z0-9_./\-]{1,32}$")
DOMAIN_RE = re.compile(r"^[a-z_]{1,24}$")
ORIGIN_ENUM = {"goal", "relation", "tool", "user_fact", "emotion", "system", "other"}

# 每类审计事件允许出网的字段（其余字段一律丢弃）。
KIND_SAFE_FIELDS = {
    "pre_step": {"stage", "matched", "level", "excluded", "turn", "step", "firstStep",
                 "chars", "phase", "ratio", "anchor", "conflict", "fallback",
                 "dropDomains", "verdict", "ucas", "downgrades", "blocked"},
    "turn_begin": {"turns", "stage"},
    "turn_end_seen": {"turns"},
    "stpa_observe": {"verdict", "ucas", "downgrades", "sources", "turn"},
    "preflight_block": {"ucas", "sources", "verdict"},
    "preflight_downgrade": {"downgrades", "ucas"},
    "phase_alert": {"phase", "ratio", "thresholds", "turn"},
    "memory_write": {"written", "origins", "ids_count", "turn"},
    "memory_supersede": {"count", "turn"},
    "memory_conflict": {"count", "turn"},
    "memory_cleanup": {"reason", "ids_count", "turn"},
    "quality_label": {"kind_label", "turn", "phase", "ratio"},
    "fact_extract_rejected": {"reason", "turn"},
    "tool_gate": {"tool", "verdict", "ucas", "reason", "turn"},
    "agent_state": {"goalEvents", "relationEvent", "trust", "conflict", "modifier", "turn"},
    "session_event": {"type", "count"},
    "apply": {"sections", "toolOk", "frozenOk", "shadow", "mode", "profileTag", "tagRejected"},
    "hook_error": set(),
    "section_error": set(),
    "tool_gate_error": set(),
    "variety_error": set(),
    "section_register_failed": set(),
    "tool_register_failed": set(),
    "tool_guard_error": set(),
    "frozen_violation": set(),
    "emotion": {"src"},
    "tool_call_seen": {"tool", "turn"},
    "tool_gate_hint_injected": {"tool", "verdict", "ucas", "turn"},
}

ERROR_KINDS = {"hook_error", "section_error", "frozen_violation", "tool_gate_error",
               "variety_error", "section_register_failed", "tool_register_failed",
               "tool_guard_error"}


def iso_of(ts) -> str | None:
    """毫秒时间戳 → `2026-10-27T19:58:28.095+08:00`；非法返回 None。"""
    try:
        ms = int(ts)
    except (TypeError, ValueError):
        return None
    if ms <= 0:
        return None
    return datetime.fromtimestamp(ms / 1000.0, tz=TZ).isoformat(timespec="milliseconds")


def safe_kind(kind) -> str:
    s = str(kind or "unknown")[:40]
    return s if TOKEN_RE.match(s) else "other"


def safe_ts(v) -> int:
    """把任意 ts 收敛成合法毫秒整数。**绝不抛异常**（坏记录不得让整个面板 500）。"""
    try:
        n = int(v)
    except (TypeError, ValueError, OverflowError):
        return 0
    return n if 0 < n < 10 ** 13 else 0


def _clean_number(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v if abs(v) < 10 ** 12 else None
    if isinstance(v, float):
        if v != v or v in (float("inf"), float("-inf")):
            return None
        return round(v, 4)
    return None


def _clean_str(v, field):
    s = str(v)
    if len(s) > 64:
        return None
    # 只有**登记过**的枚举才按枚举校验；`ENUMS.get(field, "__missing__")` 的旧写法
    # 会把未登记字段送进 `s in "__missing__"`，导致几乎恒 None（独立复核 P2 实测）。
    if field in ENUMS and ENUMS[field] is not None:
        return s if s in ENUMS[field] else None
    if field == "ucas" or field == "downgrades":
        return s if UCA_RE.match(s) or DOMAIN_RE.match(s) else None
    if field in ("goalEvents", "relationEvent"):
        return s if EVENT_RE.match(s) else None
    if field == "type":
        # 会话事件类型形如 `user/message`、`turn/end`（含 `/`），不能用 TOKEN_RE。
        return s if EVENT_RE.match(s) else None
    if field in ("dropDomains", "origins"):
        return s if DOMAIN_RE.match(s) else None
    if field == "trust":
        return s if s in ("low", "mid", "high") else None
    if field == "conflict":
        return s if s in ("none", "unresolved", "resolved", "unknown") else None
    if field == "src":
        return s if s in ("user_tone", "tool_result", "system") else None
    if field in ("reason", "reason_code", "kind_label", "type", "tool", "phase", "stage"):
        return s if TOKEN_RE.match(s) else None
    return s if TOKEN_RE.match(s) else None


def _clean_sources(v):
    """sources: {类别: bool|来源枚举}。键必须是短标识符；值只允许 bool 或来源枚举。"""
    if not isinstance(v, dict):
        return None
    allowed_vals = {"user_message", "injection", "injection+user_message", "tool", "system"}
    out = {}
    for k, val in list(v.items())[:8]:
        key = str(k)
        if not DOMAIN_RE.match(key):
            continue
        if isinstance(val, bool):
            out[key] = val
        elif isinstance(val, str) and val in allowed_vals:
            out[key] = val
        else:
            out[key] = bool(val)
    return out


def _clean_value(field, v):
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return _clean_number(v)
    if isinstance(v, str):
        return _clean_str(v, field)
    if isinstance(v, (list, tuple)):
        if field in ("ucas", "downgrades", "dropDomains", "origins", "goalEvents"):
            items = [x for x in (_clean_str(vv, field) for vv in list(v)[:12]) if x is not None]
            return items
        return None
    if isinstance(v, dict):
        if field == "sources":
            return _clean_sources(v)
        if field == "thresholds":
            out = {}
            for k in ("shift", "hard"):
                n = _clean_number(v.get(k))
                if n is not None and 0 <= n <= 1:
                    out[k] = n
            return out or None
        if field == "shadow":
            return {k: bool(v[k]) for k in ("goal", "relation") if k in v}
        return None
    return None


def redact_event(rec: dict) -> dict:
    """单条审计事件 → 可出网的脱敏视图。未知 kind 只给 kind/ts；坏输入不抛异常。"""
    rec = rec if isinstance(rec, dict) else {}
    kind = safe_kind(rec.get("kind"))
    ts = safe_ts(rec.get("ts"))
    out = {"ts": ts, "kind": kind}
    iso = iso_of(ts)
    if iso:
        out["iso"] = iso
    allowed = KIND_SAFE_FIELDS.get(kind)
    if allowed is None:
        out["fields"] = {}
        out["fields_omitted"] = True
        return out
    fields = {}
    for key in allowed:
        if key not in rec:
            continue
        val = _clean_value(key, rec.get(key))
        if val is not None:
            fields[key] = val
    out["fields"] = fields
    if any(k not in allowed and k not in ("ts", "kind") for k in rec):
        out["fields_omitted"] = True
    return out


def contains_forbidden(obj, path="") -> list[str]:
    """递归检查脱敏结果里是否出现禁止键（测试用；生产路径不调用）。"""
    hits = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in FORBIDDEN_KEYS:
                hits.append(path + "/" + str(k))
            hits.extend(contains_forbidden(v, path + "/" + str(k)))
    elif isinstance(obj, (list, tuple)):
        for i, v in enumerate(obj):
            hits.extend(contains_forbidden(v, f"{path}[{i}]"))
    return hits
