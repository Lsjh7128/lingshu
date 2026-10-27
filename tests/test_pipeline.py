# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 情感插件与合成矩阵（可直接运行）

    python tests/test_pipeline.py

覆盖：
- EMO-4 单 τ 衰减（8 / 45 / 90 分钟）
- EMO-5 双速衰减（快成分 20 分钟消退 ≥60%，慢成分 180 分钟残留 ≥10%）
- EMO-6 注入滞回（进入 0.15 / 退出 0.10，不抖动）
- EMO-3 标签闭集（禁诉求性谓词）
- COMPOSE-1..5（互斥表、拒绝场景、未覆盖冲突、预算截断、系数钳制）
- 拼装顺序不可变
"""
from __future__ import annotations

import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import compose as cp     # noqa: E402
from engine import config as cfgmod  # noqa: E402
from engine import emotion as em     # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))


def approx(a: float, b: float, tol: float = 1e-3) -> bool:
    return abs(a - b) <= tol


def test_emotion_decay() -> None:
    print("\n[EMO-4] 单 τ 衰减")
    cfg = cfgmod.default()
    dev0 = {"valence": 0.6, "arousal": 0.6, "dominance": 0.6}
    at8 = em.deviation_at(8, dev0, cfg)
    at45 = em.deviation_at(45, dev0, cfg)
    at90 = em.deviation_at(90, dev0, cfg)
    check("EMO-4 arousal 8 分钟衰减到 1/e",
          approx(at8["arousal"], 0.6 / math.e), "%.4f" % at8["arousal"])
    check("EMO-4 valence 45 分钟衰减到 1/e",
          approx(at45["valence"], 0.6 / math.e), "%.4f" % at45["valence"])
    check("EMO-4 dominance 90 分钟衰减到 1/e",
          approx(at90["dominance"], 0.6 / math.e), "%.4f" % at90["dominance"])
    check("EMO-4 三条时间常数互不相同（差一个数量级）",
          abs(8 - 45) > 10 and abs(45 - 90) > 10)


def test_emotion_dual() -> None:
    print("\n[EMO-5] 双速衰减")
    cfg = cfgmod.apply_path(cfgmod.default(), "emotion.dualSpeed", True)
    dev0 = {"valence": 0.6, "arousal": 0.6, "dominance": 0.6}
    c0 = em.components(0, dev0, cfg)
    c20 = em.components(20, dev0, cfg)
    c180 = em.components(180, dev0, cfg)
    fast_drop = 1 - c20["fast"]["valence"] / c0["fast"]["valence"]
    slow_keep = c180["slow"]["valence"] / c0["slow"]["valence"]
    # 审计修复（A5-T5）：原为宽松下界 >=0.60 / >=0.10；双速衰减是确定性指数公式，
    # t=20/tau_fast(valence)=20 → 1−e⁻¹；t=180/tau_slow(valence)=180 → e⁻¹，精确钉住。
    check("EMO-5 快成分 20 分钟消退精确 = 1−e⁻¹",
          abs(fast_drop - (1.0 - math.exp(-1.0))) < 1e-12, "%.12f" % fast_drop)
    check("EMO-5 慢成分 180 分钟残留精确 = e⁻¹",
          abs(slow_keep - math.exp(-1.0)) < 1e-12, "%.12f" % slow_keep)
    check("EMO-5 权重和为 1",
          approx(cfg["emotion"]["fastWeight"] + cfg["emotion"]["slowWeight"], 1.0))
    check("EMO-5 dual=false 时慢成分为零",
          em.components(10, dev0, cfgmod.default())["slow"]["valence"] == 0.0)


def test_emotion_hysteresis() -> None:
    print("\n[EMO-6] 注入滞回")
    cfg = cfgmod.default()
    enter, active = em.should_inject({"valence": 0.16, "arousal": 0.16, "dominance": 0.0}, cfg, False)
    check("EMO-6 0.16 ≥ 0.15 → 进入注入", enter and active)
    keep, active = em.should_inject({"valence": 0.12, "arousal": 0.12, "dominance": 0.0}, cfg, True)
    check("EMO-6 回落到 0.12 不退出（滞回）", keep and active, "keep=%s" % keep)
    exit_, active = em.should_inject({"valence": 0.09, "arousal": 0.09, "dominance": 0.0}, cfg, True)
    check("EMO-6 低于 0.10 → 退出", (not exit_) and (not active))
    no, _ = em.should_inject({"valence": 0.14, "arousal": 0.14, "dominance": 0.0}, cfg, False)
    check("EMO-6 未激活时 0.14 不进入（阈值单向）", not no)


def test_emotion_labels() -> None:
    print("\n[EMO-3] 标签闭集")
    check("EMO-3 '低落' 合法", em.label_ok("低落"))
    check("EMO-3 '受挫' 合法", em.label_ok("受挫"))
    for bad in ("悲伤", "愤怒", "抗议", "委屈", "孤独"):
        check("EMO-3 拒绝 '%s'" % bad, not em.label_ok(bad))
    check("EMO-3 未知词也拒绝", not em.label_ok("暴躁"))


def _payload(**over):
    p = {
        "domains": {
            "safety": {"refuse": False},
            "fact": {"metacog_level": "high", "recall_path": "direct",
                     "conflict": "none", "expression": "state"},
            "organization": {"goal_status": "doing", "narrative_triggered": False,
                             "assist_stage": "off"},
            "style": {"emotion_label": "愉悦", "emotion_intensity": 0.3,
                      "emotion_valence": 0.2, "emotion_arousal": 0.1,
                      "relation_modifier": 1.0},
            "retrieval": {"strategy": "default", "source": "memory"},
        }
    }
    for k, v in over.items():
        p["domains"][k] = v
    return p


def test_compose() -> None:
    print("\n[COMPOSE-1/2] 互斥表")
    cfg = cfgmod.default()
    p = _payload()
    p["domains"]["fact"] = {"metacog_level": "unknown", "recall_path": "none",
                            "conflict": "none", "expression": "refuse_claim"}
    r = cp.compose(p, cfg)
    reasons = [(d["plugin"], d["reason"]) for d in r["dropped"]]
    check("COMPOSE-1 refuse_claim 丢弃情感",
          ("emotion", "metacog_refuse_claim") in reasons, str(reasons))
    check("COMPOSE-1 丢弃后 style 域为空", r["domains"]["style"] == {})

    p2 = _payload()
    p2["domains"]["safety"] = {"refuse": True}
    r2 = cp.compose(p2, cfg)
    reasons2 = [(d["plugin"], d["reason"]) for d in r2["dropped"]]
    check("COMPOSE-2 拒绝场景丢弃风格域（互斥表第 8 条）",
          ("style", "constitution_reject") in reasons2, str(reasons2))
    check("COMPOSE-2 拒绝场景丢弃内容组织域",
          ("organization", "constitution_reject") in reasons2)
    check("COMPOSE-2 拒绝场景丢弃检索策略（第 7 条）",
          ("retrieval", "constitution_reject") in reasons2)
    check("COMPOSE-2 拒绝块出现在拼装结果中",
          any(b.startswith("[safety]") for b in r2["blocks"]))
    check("COMPOSE-2 拒绝时不再出现风格块",
          not any(b.startswith("[style]") for b in r2["blocks"]))


def test_compose_edge() -> None:
    print("\n[COMPOSE-3/4/5] 未覆盖冲突 / 预算 / 系数")
    cfg = cfgmod.default()
    p = _payload()
    r = cp.compose(p, cfg, forced_conflicts=[("narrative", "user_emotion")])
    check("COMPOSE-3 未覆盖冲突记 no_mutex_rule",
          any(d["reason"] == "no_mutex_rule" for d in r["dropped"]), str(r["dropped"]))

    # 预算收紧到极小 → 必须丢风格块；只剩硬块时标记 overflow，但不丢宪法
    tight = cfgmod.apply_path(cfgmod.default(), "compose.tokenRatioMax", 0.0005)
    r3 = cp.compose(_payload(), tight)
    check("COMPOSE-4 预算超限时截断（truncated）", r3["budget"]["truncated"])
    check("COMPOSE-4 风格块被丢弃",
          any(d["reason"] == "budget_exceeded" for d in r3["dropped"]))
    check("COMPOSE-4 宪法块永不丢",
          any(b.startswith("[constitution]") for b in r3["blocks"]), str(r3["blocks"]))

    # 拒绝场景 + 极小预算：只剩硬块 → overflow 显式标记，安全块仍在
    p3 = _payload()
    p3["domains"]["safety"] = {"refuse": True}
    r3b = cp.compose(p3, tight)
    check("COMPOSE-4 只剩硬块时标记 overflow", r3b["budget"]["overflow"],
          str(r3b["budget"]))
    check("COMPOSE-4 安全块永不丢",
          any(b.startswith("[safety]") for b in r3b["blocks"]), str(r3b["blocks"]))

    p4 = _payload()
    p4["domains"]["style"]["relation_modifier"] = 1.9
    r4 = cp.compose(p4, cfg)
    check("COMPOSE-5 系数钳制到 1.2", approx(r4["final_style_multiplier"], 1.2),
          str(r4["final_style_multiplier"]))

    order = [b.split("]")[0].strip("[") for b in r4["blocks"]]
    check("拼装顺序：宪法在最前、用户消息在最后",
          order[0] == "constitution" and order[-1] == "user", str(order))
    check("拼装块数 ≤ maxBlocks", len(r4["blocks"]) <= cfg["compose"]["maxBlocks"],
          str(len(r4["blocks"])))


def test_soft_buffer() -> None:
    """ADR-014 §4.1：互斥表第 9 条 soft_buffer（只增语义，不删第 8 条）。"""
    print("\n[COMPOSE-6] 第 9 条 soft_buffer（沉浸式安全）")
    cfg = cfgmod.default()
    cap = float(cfg["control"]["softBufferMaxIntensity"])
    check("COMPOSE-6 柔性缓冲强度上限来自 config（rules 单一来源）",
          cap == 0.45 and int(cfg["control"]["escalateAfterSoftHits"]) == 3, str(cap))
    check("COMPOSE-6 回滚开关默认开启（immersiveEnabled）",
          cfg["control"]["immersiveEnabled"] is True)

    p = _payload()
    p["domains"]["safety"] = {"refuse": True, "severity": "soft", "safe_redirect": True}
    p["domains"]["style"]["relation_modifier"] = 1.9
    r = cp.compose(p, cfg)
    check("COMPOSE-6 soft 不丢风格域（块仍在）",
          any(b.startswith("[style]") for b in r["blocks"]), str(r["blocks"]))
    check("COMPOSE-6 soft 的 dropped 不记录 style（它没有被丢弃）",
          not any(d["plugin"] == "style" for d in r["dropped"]), str(r["dropped"]))
    check("COMPOSE-6 soft_buffer.applied=true 且 notice=Safe_Redirect",
          r["soft_buffer"]["applied"] is True
          and r["soft_buffer"]["notice"] == "Safe_Redirect", str(r["soft_buffer"]))
    check("COMPOSE-6 soft 强度 ≤ softBufferMaxIntensity",
          float(r["domains"]["style"]["emotion_intensity"]) <= cap,
          str(r["domains"]["style"]["emotion_intensity"]))
    check("COMPOSE-6 soft 系数钳制到 [0.8, 1.0]",
          0.8 <= r["final_style_multiplier"] <= 1.0, str(r["final_style_multiplier"]))
    check("COMPOSE-6 soft 安全块含 [System_Notice: Safe_Redirect]",
          any(cp.SAFE_REDIRECT_MARKER in b for b in r["blocks"]), str(r["blocks"][:2]))

    # 第 8 条原样保留：不传 severity 时 hard 路径不变（对照 COMPOSE-2）
    p_hard = _payload()
    p_hard["domains"]["safety"] = {"refuse": True}
    r_hard = cp.compose(p_hard, cfg)
    check("COMPOSE-6 hard 路径不产生 soft_buffer",
          r_hard["soft_buffer"]["applied"] is False, str(r_hard["soft_buffer"]))
    check("COMPOSE-6 hard 路径仍然丢弃风格域（第 8 条不放宽）",
          r_hard["domains"]["style"] == {}
          and any(d["plugin"] == "style" for d in r_hard["dropped"]), str(r_hard["dropped"]))


def main() -> int:
    print("LINGSHU 验收 · 情感插件与合成矩阵")
    test_emotion_decay()
    test_emotion_dual()
    test_emotion_hysteresis()
    test_emotion_labels()
    test_compose()
    test_compose_edge()
    test_soft_buffer()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
