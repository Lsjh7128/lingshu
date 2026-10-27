# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 端到端流水线（把 14 个模块第一次串起来）

一轮的顺序（与 docs/00 架构图一致）：

    agent/pre-step
      → 冷启动门控（onboarding）        ← 决定要不要注入、注入多少
      → 记忆召回（memory.recall）        ← 预算化，装不下如实标记
      → 元认知定档（metacog）           ← 纯规则，零模型调用
      → 情感状态（emotion）             ← 无事件时 no_event，不猜
      → 相变监控（PhaseShiftMonitor）    ← 仅在调用方给出上下文用量时观测
      → 合成（compose）                 ← 五域 + 8 条互斥 + 预算截断
      → U 型注入排序（u_shape）          ← 只排注入顺序，不动 recall 返回值
      → 首尾人格锚点（apply_persona_anchor）
      → STPA 前置审计（preflight_audit） ← fail-closed：block 即不给可注入 prompt
    turn/end
      → 情感衰减推进、阶段计数

**边界说明**：目标/关系状态需要事件输入，回放里没有，所以这里固定为
`goal_status="none"`、`assist_stage="off"`、`relation_modifier=1.0`。
它们各自的语义已由 `engine/goal.py` / `engine/relation.py` 的单元验收覆盖，
这里只验证"接起来不炸、顺序不乱、丢弃有记录"。
"""
from __future__ import annotations

from typing import Any

from . import compose as CP
from . import config as cfgmod
from . import control as CT
from . import emotion as EM
from . import memory, metacog, onboarding

PROMPT_VERSION = "memory-3.0+metacog-3.0+emotion-3.0+compose-3.0+control-4.0"

# fail-closed：前置审计不过时**唯一**允许输出的文本（不含任何可注入 prompt）。
BLOCKED_TEXT = ("【安全熔断】本轮预检未通过（%s），已按 fail-closed 停止注入；"
                "请人工复核后再继续。")


# ── 注入侧排序：U 型 ─────────────────────────────────

def u_shape(entries: list[Any]) -> list[Any]:
    """U 型注入排序：输入已按分数降序的 `returned` 列表。

    规则：**高分首、次高分尾、最低居中** —— 0 号放开头，1 号放结尾，
    其余按强弱交替向中间收（n=3 → [r0,r2,r1]；n=5 → [r0,r2,r4,r3,r1]）。

    为什么是 U 型：模型对 prompt **首尾**的利用显著好于中段。于是把最强两条
    放在首尾、最弱的放进中段，等价于把"最容易被忽略的位置"留给最弱的条目。

    **只用于注入侧**（`event.domains.fact.entry_ids` / 结果里的 `injection_order`），
    绝不改动 `memory.recall` 返回的 `returned` 顺序——冻结的 `recall@3` 口径
    依赖那个顺序（见 docs/13 §3）。n ≤ 2 时保持原序。
    """
    seq = list(entries or [])
    if len(seq) <= 2:
        return seq
    head = seq[0::2]
    tail = seq[1::2]
    return head + tail[::-1]


class PipelineSession:
    """一条会话的流水线状态。跨会话只共享 onboarding 的累计轮次。"""

    def __init__(self, cfg: dict[str, Any], session_id: str = "s",
                 ob: onboarding.Onboarding | None = None) -> None:
        self.cfg = cfg
        self.session_id = session_id
        # 冷启动状态必须**跨会话共享**（docs/11：按累计轮次，不是单会话）。
        # 每条会话各自 new 一个 Onboarding，等于每个会话都从头冷启动一次 —— 实测踩到。
        self.ob = ob if ob is not None else onboarding.Onboarding(cfg)
        self.apply_onboarding = bool(cfg.get("replay", {}).get("applyOnboarding"))
        self.dev = EM.zero_dev()          # 情感偏离
        self.active = False                # 情感是否处于注入态（滞回）
        self.turn = 0
        self.label = "中性"
        # 控制论层（docs/19）：相变监控 + 必要多样性守卫，每条会话一份状态。
        # 只在调用方显式给出上下文用量时才 observe（默认不 observe，回放不受影响）。
        self.phase_monitor = CT.PhaseShiftMonitor(cfg)
        self.variety_guard = CT.RequisiteVarietyGuard(cfg)
        # 沉浸式安全（ADR-014）：连续非豁免 mild 命中数，跨轮续传（升级门槛用）。
        self.risk_streak = 0

    # ── 一轮 ──────────────────────────────────────────
    def step(self, message: str, now: float, store, *, recall_result: dict | None = None,
             param_version: str = "",
             context_used_tokens: int | None = None,
             context_window_tokens: int | None = None,
             frozen_ok: bool = True,
             frozen_verified: bool = False,
             saturated: dict[str, bool] | None = None,
             trust_level: str = "low") -> dict[str, Any]:
        self.turn += 1
        stage = self.ob.stage

        # 0) 风险分级 + 干预决策（ADR-014，纯规则、零模型调用）。
        # soft/hard 只决定**表达层**，不决定检索/召回；默认 trust_level="low"。
        risk = CT.classify_user_risk(message, trust_level=trust_level)
        decision = CT.decide_intervention(risk, self.risk_streak, self.cfg)
        self.risk_streak = int(decision.get("risk_streak", 0))
        severity = decision.get("severity")
        soft = severity == "soft"

        # 1) 召回（允许外部注入，便于复用回放结果）
        rec = recall_result if recall_result is not None else memory.recall(
            store, message, now, self.cfg, session_id=self.session_id)
        if self.apply_onboarding:
            rec = self.ob.filter_recall(rec)

        # 1.5) U 型注入排序：只算"注入顺序"，rec["returned"] 保持 recall 原序不动。
        injection_order = [r["id"] for r in u_shape(rec.get("returned", []))]

        # 2) 元认知定档
        verdict = metacog.verdict(rec, self.cfg, emotion_intensity=EM.intensity(self.dev))

        # 3) 情感：冷启动期强制 no_event（拿不到 goal_*）
        no_event = self.apply_onboarding and (not self.ob.emotion_eval_allowed())
        if not no_event:
            self.dev = EM.deviation_at(1.0, self.dev, self.cfg)   # 每轮衰减 1 分钟当量
        inject, self.active = EM.should_inject(self.dev, self.cfg, self.active)
        self.label = EM.pick_label(self.dev)
        if not inject:
            self.label = "中性"

        # 3.5) 相变监控（前馈）：只有调用方**同时**给出上下文用量才观测。
        # 默认不观测 → 回放/旧调用方式的行为与事件完全不变。
        phase_result = None
        if context_used_tokens is not None and context_window_tokens is not None:
            phase_result = self.phase_monitor.observe(
                int(context_used_tokens), int(context_window_tokens))

        # 4) 合成
        # ADR-014：风险分级只改**表达层**——soft 时 safety.refuse=True + severity="soft"
        # （compose 走互斥表第 9 条），并把风格域改为受约束的警觉/困惑缓冲；
        # none 时不加任何新键，事件与 v3.0 一致。
        safety_domain: dict[str, Any] = {"refuse": False}
        if severity in ("soft", "hard"):
            safety_domain = {
                "refuse": True,
                "severity": severity,
                "safe_redirect": bool(decision.get("safe_redirect")),
                "reason": "user_risk_%s" % (decision.get("risk_tier") or "unknown"),
            }
        style_domain: dict[str, Any] = ({"emotion_label": self.label,
                                         "emotion_intensity": round(EM.intensity(self.dev), 3),
                                         "emotion_valence": self.dev["valence"],
                                         "emotion_arousal": self.dev["arousal"],
                                         "relation_modifier": 1.0}
                                        if inject else {})
        if soft:
            # 缓冲表达：标签来自风险分级（警觉/困惑），强度只收不放（compose 再钳制）
            cap = float((self.cfg.get("control") or {}).get("softBufferMaxIntensity", 0.45))
            style_domain = {
                "emotion_label": decision.get("emotion") or "警觉",
                "emotion_intensity": round(min(cap, EM.intensity(self.dev)), 3),
                "emotion_valence": self.dev["valence"],
                "emotion_arousal": self.dev["arousal"],
                "relation_modifier": 1.0,
            }
        payload = {
            "domains": {
                "safety": safety_domain,
                "fact": {"metacog_level": verdict["confidence_level"],
                         "recall_path": verdict["path"],
                         "conflict": verdict["conflict"],
                         "expression": verdict["expression"],
                         "entry_ids": list(injection_order)},
                "organization": {"goal_status": "none", "narrative_triggered": False,
                                 "assist_stage": "off"},
                "style": style_domain,
                "retrieval": {"strategy": "default", "source": "memory"},
            }
        }
        result = CP.compose(payload, self.cfg)

        # 4.5) 必要多样性定律（docs/19 §2.4）：互斥表覆盖不到的冲突 → 防御性降维。
        # 正常路径 conflict="none"，守卫返回 fallback=False，不做任何改动。
        guard = self.variety_guard.check(
            conflict=str(verdict.get("conflict") or "none"),
            # 单条 conflict 标签无法证明被某条互斥规则覆盖 → 防御性按"未覆盖"处理
            matched_mutex=False,
            domains=payload["domains"])
        fallback_applied = bool(guard.get("fallback"))
        if fallback_applied:
            result = _apply_variety_fallback(result)

        # 5) 渲染 →（相变期）首尾人格锚点
        text = CP.render(result)
        anchor_applied = False
        if phase_result and (phase_result.get("prefeed") or phase_result.get("anchor_required")):
            anchored = CT.apply_persona_anchor(text, phase_result)
            anchor_applied = anchored != text
            text = anchored

        # 6) STPA 前置审计：拼装完、发模型前的强制闸（fail-closed）
        blocks = result["blocks"]
        refuse = bool(payload["domains"]["safety"].get("refuse"))
        conflict = str(verdict.get("conflict") or "none")
        soft_buffer = result.get("soft_buffer") or {}
        audit = CT.preflight_audit(
            prompt=text,
            frozen_ok=bool(frozen_ok),
            refuse=refuse,
            style_injected=any(b.startswith("[style]") for b in blocks),
            constitution_injected=bool(blocks) and blocks[0].startswith("[constitution]"),
            conflict=conflict,
            fallback_applied=fallback_applied,
            phase_result=phase_result,
            fact_block=_block_text(blocks, "[fact]"),
            # UCA-08（v3.2）：积分饱和是"降级不熔断"，由调用方传入 saturated 事实，
            # 闸门只负责把它记进 audit.downgrades（可观测），不改变 verdict。
            saturated=saturated,
            # ADR-014 §4.3：新增可选入参（不传时 preflight 行为与 v3.0 一致）
            risk_tier=decision.get("risk_tier"),
            safe_redirect=bool(decision.get("safe_redirect")),
            soft_buffer_applied=bool(soft_buffer.get("applied")),
            trust_exempt=bool(decision.get("trust_exempt")),
            escalated=bool(decision.get("escalated")))
        # hard（含升级/回滚开关）必须 fail-closed；soft 的 verdict 同样是 block。
        blocked = audit["verdict"] == "block" or severity == "hard"
        # ADR-014：soft 时允许回"受限 Safe_Redirect 文本"；一旦同时命中其它 UCA
        # （如 UCA-02 宪法校验失败），仍按 hard fail-closed 处理，不给任何 prompt。
        soft_prompt_ok = (blocked and audit["severity"] == "soft"
                          and set(audit["ucas"]) <= {"UCA-01"})
        # 是否**真校验过**宪法冻结层：pipeline 自身不做校验，默认 only assumed。
        frozen_audit = {"ok": bool(frozen_ok), "verified": bool(frozen_verified)}

        # ADR-014 §4.4：可观测字段（跨进程/审计对齐用；risk_tier/pattern 保留原始分类值）
        immersive = {
            "severity": audit["severity"],
            "safe_redirect": audit["safe_redirect"],
            # 缓冲情绪以本次决策为准（preflight 输出按 ADR 字面只标"警觉"，不携带分级细节）
            "emotion": decision.get("emotion") or audit["emotion"],
            "trust_exempt": audit["trust_exempt"],
            "escalated": audit["escalated"],
            "risk_tier": audit["risk_tier"],
            "pattern": decision.get("pattern"),
            "risk_streak": self.risk_streak,
        }

        event = {
            "event_id": "p-%s-%03d" % (self.session_id, self.turn),
            "at": now,
            "session_id": self.session_id,
            "turn_id": "t_%03d" % self.turn,
            "param_version": param_version or _pv(self.cfg),
            "prompt_version": PROMPT_VERSION,
            "compose_version": result["compose_version"],
            **{k: result[k] for k in ("domains", "dropped",
                                      "final_style_multiplier", "blocks", "budget")},
            "soft_buffer": soft_buffer,
            # 控制论接线证据（docs/19 §5 第 2/3/4 项）
            "injection_order": list(injection_order),
            "phase_result": phase_result,
            "anchor_applied": anchor_applied,
            "audit": audit,
            "frozen_audit": frozen_audit,
            "fallback_applied": fallback_applied,
            "blocked": blocked,
            # ADR-014 §4.4：只增不改（hard/soft/None 的表达层事实）
            "blocked_severity": audit["severity"],
            "immersive": immersive,
        }
        # fail-closed：hard blocked 时不返回任何可注入 prompt，只给安全熔断文本；
        # soft blocked 时返回受限的 Safe_Redirect 文本（不是可正常对话的 prompt）。
        if soft_prompt_ok:
            prompt_out, output_text = text, text
        elif blocked:
            prompt_out = None
            output_text = BLOCKED_TEXT % ("、".join(audit["ucas"]) or "unknown")
        else:
            prompt_out, output_text = text, text
        return {"stage": stage, "no_event": no_event,
                "recall": {"matched": rec.get("matched_count", 0),
                           "returned": [r["id"] for r in rec.get("returned", [])],
                           "suppressed_by": rec.get("suppressed_by")},
                "verdict": verdict, "emotion": {"label": self.label,
                                                "intensity": round(EM.intensity(self.dev), 3),
                                                "active": self.active},
                "injection_order": list(injection_order),
                "phase_result": phase_result,
                "anchor_applied": anchor_applied,
                "audit": audit,
                "frozen_audit": frozen_audit,
                "fallback_applied": fallback_applied,
                "blocked": blocked,
                "blocked_severity": audit["severity"],
                "immersive": immersive,
                "event": event,
                "prompt": prompt_out,
                "text": output_text}

    def end_turn(self) -> str:
        """turn/end：阶段计数推进。"""
        return self.ob.tick()


def _block_text(blocks: list[str], prefix: str) -> str:
    """取首个以 prefix 开头的拼装块（用于 audit 的 fact_block / 证据留存）。"""
    for b in blocks:
        if b.startswith(prefix):
            return b
    return ""


def _apply_variety_fallback(result: dict[str, Any]) -> dict[str, Any]:
    """必要多样性降维：丢风格域，退回"中性事实 + 澄清"，并留痕。

    只在 `conflict="unresolved"` 且无互斥规则覆盖时触发（正常路径不会发生）。
    """
    style_block = any(b.startswith("[style]") for b in result["blocks"])
    if result["domains"].get("style"):
        result["domains"]["style"] = {}
    if style_block:
        result["blocks"] = [b for b in result["blocks"] if not b.startswith("[style]")]
        result["dropped"].append({"plugin": "style",
                                  "reason": "requisite_variety_fallback"})
    return result


def _pv(cfg: dict[str, Any]) -> str:
    import hashlib
    import json
    blob = json.dumps(cfgmod.flatten(cfg), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"))
    return hashlib.sha256(blob.encode("utf-8")).hexdigest()[:8]


# ── 回放整条链 ────────────────────────────────────────

def replay_pipeline(cfg: dict[str, Any], root: str, version: str = "v1") -> dict[str, Any]:
    """在回放集上跑整条流水线，返回逐轮输出 + 汇总统计。"""
    from . import replay as rp

    turns, store = rp.load_dataset(root, version)
    base_ts = rp.BASE_TS
    step = rp.TURN_STEP
    ob = onboarding.Onboarding(cfg)
    sessions: dict[str, PipelineSession] = {}
    records: list[dict[str, Any]] = []
    dropped_reasons: dict[str, int] = {}
    block_counts: dict[int, int] = {}
    suppressed: dict[str, int] = {}

    for i, t in enumerate(turns):
        sid = t["session_id"]
        if sid not in sessions:
            sessions[sid] = PipelineSession(cfg, sid, ob=ob)
            ob.start_session()
        ps = sessions[sid]
        out = ps.step(t["user_message"], base_ts + i * step, store)
        ob.tick()

        for d in out["event"]["dropped"]:
            key = "%s:%s" % (d.get("plugin"), d.get("reason"))
            dropped_reasons[key] = dropped_reasons.get(key, 0) + 1
        n = len(out["event"]["blocks"])
        block_counts[n] = block_counts.get(n, 0) + 1
        if out["recall"].get("suppressed_by"):
            k = out["recall"]["suppressed_by"]
            suppressed[k] = suppressed.get(k, 0) + 1

        records.append({
            "turn_id": t["id"], "session_id": sid, "stage": out["stage"],
            "returned_ids": out["recall"]["returned"],
            "expected_ids": t["expected_ids"], "forbidden_ids": t["forbidden_ids"],
            "verdict": {"confidence_level": out["verdict"]["confidence_level"],
                        "expression": out["verdict"]["expression"]},
            "blocks": len(out["event"]["blocks"]),
            "dropped": len(out["event"]["dropped"]),
            "text_len": len(out["text"]),
            "budget_truncated": out["event"]["budget"]["truncated"],
            # 注入侧证据：U 型顺序是 returned_ids 的置换，replay 口径仍用 returned_ids
            "injection_order": out["injection_order"],
            "blocked": out["blocked"],
        })

    exp = [r for r in records if r["expected_ids"]]
    hit = sum(1 for r in exp if set(r["expected_ids"]) & set(r["returned_ids"]))
    forb = [r for r in records if r["returned_ids"]]
    leak = sum(1 for r in forb if set(r["forbidden_ids"]) & set(r["returned_ids"]))

    return {
        "turns": records,
        "summary": {
            "turns": len(records),
            "recall_at_3": round(hit / len(exp), 6) if exp else 0.0,
            "forbidden_leak": leak,
            "dropped_reasons": dict(sorted(dropped_reasons.items())),
            "block_count_hist": dict(sorted(block_counts.items())),
            "suppressed": dict(sorted(suppressed.items())),
            "truncated_turns": sum(1 for r in records if r["budget_truncated"]),
            # 前置审计闸：正常回放必须为 0（fail-closed 若在基线里触发，说明接线有问题）
            "blocked_turns": sum(1 for r in records if r["blocked"]),
        },
    }


def check_event(event: dict[str, Any]) -> list[str]:
    """校验 prompt/compose 事件是否符合契约必填字段。"""
    required = ("event_id", "at", "session_id", "turn_id", "param_version",
                "prompt_version", "compose_version", "domains", "dropped",
                "final_style_multiplier")
    return [f for f in required if f not in event]
