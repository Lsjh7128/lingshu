# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 端到端流水线（recall → metacog → emotion → compose → control）

    python tests/test_pipeline_integration.py

覆盖：
- 200 轮全链路不抛异常；每轮事件符合 prompt/compose 契约必填字段
- 注入块顺序固定（宪法第一、用户消息最后）、块数 ≤ maxBlocks
- dropped 原因只能来自白名单（不允许出现无记录原因的静默丢弃）
- 冷启动门控：默认关；开启后**跨会话累计**只压前 3 轮（不是每会话 3 轮）
- 幂等：两次运行汇总一致
- 违禁条目零泄漏
- docs/19 控制论接线：相变监控 + 首尾人格锚点、U 型注入排序、preflight_audit 强制闸
"""
from __future__ import annotations

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config as cfgmod            # noqa: E402
from engine import pipeline                    # noqa: E402

PASS, FAIL = [], []

ALLOWED_DROP_REASONS = {
    "constitution_reject", "metacog_refuse_claim", "goal_blocked",
    "relation_unresolved_conflict", "user_emotion_refuse_help",
    "budget_exceeded", "no_mutex_rule",
}


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail else ""))


def _cfg(on: bool):
    c = cfgmod.default()
    return cfgmod.apply_path(c, "replay.applyOnboarding", True) if on else c


def test_runs() -> None:
    print("\n[PIPE] 全链路")
    rep = pipeline.replay_pipeline(_cfg(False), ROOT)
    s = rep["summary"]
    check("200 轮全跑完", s["turns"] == 200, s["turns"])
    check("recall@3 = 1.000（门控关闭）", abs(s["recall_at_3"] - 1.0) < 1e-9, s["recall_at_3"])
    check("违禁条目零泄漏", s["forbidden_leak"] == 0, s["forbidden_leak"])
    check("没有预算截断", s["truncated_turns"] == 0, s["truncated_turns"])
    check("没有丢弃（正常路径）", s["dropped_reasons"] == {}, str(s["dropped_reasons"]))
    check("正常回放无前置熔断", s.get("blocked_turns") == 0, s.get("blocked_turns"))

    hist = s["block_count_hist"]
    check("块数在 2–6 之间", all(2 <= k <= 6 for k in hist), str(hist))
    check("块数分布稳定（3 或 4）", set(hist) <= {3, 4}, str(hist))


def test_event_contract() -> None:
    print("\n[PIPE] 事件契约")
    cfg = _cfg(False)
    turns, store = pipeline.rp.load_dataset(ROOT) if hasattr(pipeline, "rp") else (None, None)
    from engine import replay as rp
    turns, store = rp.load_dataset(ROOT)
    ps = pipeline.PipelineSession(cfg, "s_t")
    out = ps.step(turns[0]["user_message"], rp.BASE_TS, store)
    ev = out["event"]
    check("事件必填字段齐全", pipeline.check_event(ev) == [], str(pipeline.check_event(ev)))
    check("compose_version 正确（3.1：动态权重）", ev["compose_version"] == "compose-3.1", ev["compose_version"])
    check("含 param_version / prompt_version",
          bool(ev["param_version"]) and "compose" in ev["prompt_version"])
    check("blocks 非空且可渲染", len(ev["blocks"]) >= 2 and len(out["text"]) > 0)
    check("首块是宪法", ev["blocks"][0].startswith("[constitution]"), ev["blocks"][0])
    check("末块是用户消息", ev["blocks"][-1].startswith("[user]"), ev["blocks"][-1])

    check("事件含控制论审计字段",
          all(k in ev for k in ("injection_order", "phase_result", "audit",
                                "frozen_audit", "blocked")), str(sorted(ev)))
    check("injection_order 是 returned 的置换（不改 recall 顺序）",
          sorted(ev["injection_order"]) == sorted(out["recall"]["returned"]),
          str(ev["injection_order"]))
    check("默认不观测相变（回放不受影响）", ev["phase_result"] is None)

    check("check_event 能发现缺字段",
          pipeline.check_event({"event_id": "x"}) != [])


def test_dropped_whitelist() -> None:
    print("\n[PIPE] 丢弃必须有原因")
    # 构造：元认知 refuse_claim + 情感活跃 → 应丢弃 emotion 并记原因
    cfg = _cfg(False)
    from engine import replay as rp
    turns, store = rp.load_dataset(ROOT)
    ps = pipeline.PipelineSession(cfg, "s_d")
    ps.dev = {"valence": -0.4, "arousal": 0.3, "dominance": -0.2}
    ps.active = True
    ps.label = "受挫"
    out = ps.step("zxq_nohit quasar", rp.BASE_TS, store)     # 必然无命中 → refuse_claim?
    reasons = [d["reason"] for d in out["event"]["dropped"]]
    check("无命中场景要么不注入风格、要么记原因",
          (not out["event"]["domains"]["style"]) or reasons, str(reasons))
    check("所有丢弃原因都在白名单内",
          all(r in ALLOWED_DROP_REASONS for r in reasons), str(reasons))


def test_onboarding_gate() -> None:
    print("\n[PIPE] 冷启动门控")
    off = pipeline.replay_pipeline(_cfg(False), ROOT)["summary"]
    on = pipeline.replay_pipeline(_cfg(True), ROOT)["summary"]
    check("关闭时无抑制", off["suppressed"] == {}, str(off["suppressed"]))
    check("开启后只压 3 轮（跨会话累计）",
          on["suppressed"].get("cold_start") == 3, str(on["suppressed"]))
    check("开启后 recall 略降", on["recall_at_3"] < off["recall_at_3"],
          "%.3f vs %.3f" % (on["recall_at_3"], off["recall_at_3"]))
    check("开启后仍零泄漏", on["forbidden_leak"] == 0)


def test_idempotent() -> None:
    print("\n[PIPE] 幂等")
    a = json.dumps(pipeline.replay_pipeline(_cfg(False), ROOT)["summary"],
                   sort_keys=True, ensure_ascii=False)
    b = json.dumps(pipeline.replay_pipeline(_cfg(False), ROOT)["summary"],
                   sort_keys=True, ensure_ascii=False)
    check("两次运行汇总逐字节相同", a == b)


def test_text_sanity() -> None:
    print("\n[PIPE] 渲染文本")
    rep = pipeline.replay_pipeline(_cfg(False), ROOT)
    lens = [t["text_len"] for t in rep["turns"]]
    check("每轮都产出了文本", all(x > 0 for x in lens), str(min(lens)))
    check("文本长度合理（20–2000 字符）", 20 <= min(lens) and max(lens) <= 2000,
          "%d..%d" % (min(lens), max(lens)))


def test_u_shape() -> None:
    print("\n[PIPE] U 型注入排序（文档/19 §5-3）")
    check("u_shape 空列表 → 空", pipeline.u_shape([]) == [])
    check("u_shape n=1 原序", pipeline.u_shape(["a"]) == ["a"])
    check("u_shape n=2 原序", pipeline.u_shape(["a", "b"]) == ["a", "b"])
    check("u_shape n=3 → [r0, r2, r1]",
          pipeline.u_shape(["a", "b", "c"]) == ["a", "c", "b"])
    five = ["r%d" % i for i in range(5)]
    order = pipeline.u_shape(five)
    check("u_shape n=4 → [r0, r2, r3, r1]（次高分尾、弱项居中）",
          pipeline.u_shape(["a", "b", "c", "d"]) == ["a", "c", "d", "b"],
          str(pipeline.u_shape(["a", "b", "c", "d"])))
    check("u_shape n=5 → [r0, r2, r4, r3, r1]（最低居中）",
          order == ["r0", "r2", "r4", "r3", "r1"], str(order))
    check("u_shape 结果是原列表的置换", sorted(order) == sorted(five), str(order))
    check("u_shape 不原地修改输入", five == ["r%d" % i for i in range(5)], str(five))

    # 注入侧：用伪造 recall 结果精确验证顺序，且 recall 顺序保持不变
    from engine import replay as rp
    turns, store = rp.load_dataset(ROOT)
    fake = {
        "matched_count": 5, "excluded_by_budget": 0,
        "returned": [
            {"id": "r0", "path": "direct", "potency_band": "high", "score": 0.9},
            {"id": "r1", "path": "direct", "potency_band": "high", "score": 0.8},
            {"id": "r2", "path": "diffused", "potency_band": "mid", "score": 0.7},
            {"id": "r3", "path": "direct", "potency_band": "mid", "score": 0.6},
            {"id": "r4", "path": "cold", "potency_band": "low", "score": 0.5},
        ],
    }
    ps = pipeline.PipelineSession(_cfg(False), "s_u")
    out = ps.step(turns[0]["user_message"], rp.BASE_TS, store, recall_result=fake)
    check("recall 返回顺序未被改动（冻结 recall@3 前提）",
          out["recall"]["returned"] == five, str(out["recall"]["returned"]))
    check("结果 injection_order 是 U 型", out["injection_order"] == order,
          str(out["injection_order"]))
    check("event.domains.fact.entry_ids 用注入顺序",
          out["event"]["domains"]["fact"]["entry_ids"] == order,
          str(out["event"]["domains"]["fact"]["entry_ids"]))
    check("调用方传入的 recall 结果未被就地改动",
          [r["id"] for r in fake["returned"]] == five, str(fake["returned"]))

    rep = pipeline.replay_pipeline(_cfg(False), ROOT)
    check("回放：每轮注入顺序都是 recall 顺序的置换",
          all(sorted(t["injection_order"]) == sorted(t["returned_ids"])
              for t in rep["turns"]))
    check("回放：注入顺序 == u_shape(recall 顺序)",
          all(t["injection_order"] == pipeline.u_shape(t["returned_ids"])
              for t in rep["turns"]))


def test_phase_anchor() -> None:
    print("\n[PIPE] 相变监控 + 首尾人格锚点（文档/19 §2.3）")
    from engine import replay as rp
    turns, store = rp.load_dataset(ROOT)
    ps = pipeline.PipelineSession(_cfg(False), "s_phase")

    cold = ps.step(turns[0]["user_message"], rp.BASE_TS, store)
    check("默认不观测相变", cold["phase_result"] is None
          and cold["event"]["phase_result"] is None)
    check("默认不加锚点", (not cold["anchor_applied"])
          and "〖人格锚点" not in cold["text"])

    low = ps.step(turns[1]["user_message"], rp.BASE_TS + 60, store,
                  context_used_tokens=100, context_window_tokens=1000)
    check("用量低（0.10）不触发前馈", bool(low["phase_result"])
          and not low["phase_result"]["prefeed"] and not low["anchor_applied"],
          str(low["phase_result"]))

    hot = ps.step(turns[2]["user_message"], rp.BASE_TS + 120, store,
                  context_used_tokens=950, context_window_tokens=1000)
    check("用量高（0.95）触发前馈", bool(hot["phase_result"])
          and hot["phase_result"]["prefeed"], str(hot["phase_result"]))
    check("prompt 首部有人格锚点", hot["text"].startswith("〖人格锚点"),
          hot["text"][:24])
    check("prompt 尾部有人格锚点复核",
          "〖人格锚点·复核〗" in hot["text"]
          and hot["text"].rstrip().endswith("不编造记忆。"),
          hot["text"][-40:])
    check("锚点未改动拼装块（顺序/块数不变）",
          hot["event"]["blocks"][0].startswith("[constitution]")
          and hot["event"]["blocks"][-1].startswith("[user]"))
    check("锚点事件留痕", hot["event"]["anchor_applied"]
          and hot["event"]["phase_result"]["prefeed"])
    check("补锚点后 UCA-07 不命中",
          "UCA-07" not in hot["audit"]["ucas"], str(hot["audit"]["ucas"]))


def test_preflight_gate() -> None:
    print("\n[PIPE] preflight_audit 强制闸（文档/19 §4）")
    from engine import replay as rp
    turns, store = rp.load_dataset(ROOT)
    ps = pipeline.PipelineSession(_cfg(False), "s_audit")

    ok = ps.step(turns[0]["user_message"], rp.BASE_TS, store)
    check("正常路径 audit=allow",
          ok["audit"]["verdict"] == "allow" and ok["audit"]["ucas"] == [],
          str(ok["audit"]))
    check("正常路径未熔断且 prompt 可注入",
          ok["blocked"] is False and ok["prompt"] == ok["text"])
    check("正常路径 frozen 标注为未真校验",
          ok["frozen_audit"] == {"ok": True, "verified": False},
          str(ok["frozen_audit"]))
    check("事件记录 audit / blocked",
          ok["event"]["audit"]["verdict"] == "allow"
          and ok["event"]["blocked"] is False)

    bad = ps.step(turns[1]["user_message"], rp.BASE_TS + 60, store, frozen_ok=False)
    check("frozen_ok=False → 熔断", bad["blocked"] is True
          and bad["audit"]["verdict"] == "block")
    check("命中 UCA-02", "UCA-02" in bad["audit"]["ucas"],
          str(bad["audit"]["ucas"]))
    check("fail-closed：不返回可注入 prompt", bad["prompt"] is None)
    check("熔断文本不含任何注入块",
          "[constitution]" not in bad["text"] and "[style]" not in bad["text"]
          and "[fact]" not in bad["text"] and "〖人格锚点" not in bad["text"],
          bad["text"])
    check("熔断文本标明原因且可读",
          "安全熔断" in bad["text"] and "UCA-02" in bad["text"], bad["text"])
    check("事件记录 blocked=true 与 audit",
          bad["event"]["blocked"] is True
          and bad["event"]["audit"]["ucas"] == ["UCA-02"])
    check("事件 frozen_audit 标注真实校验状态",
          bad["event"]["frozen_audit"] == {"ok": False, "verified": False},
          str(bad["event"]["frozen_audit"]))

    verified = ps.step(turns[2]["user_message"], rp.BASE_TS + 120, store,
                       frozen_verified=True)
    check("显式真校验过 → 事件留痕 verified=True",
          verified["frozen_audit"] == {"ok": True, "verified": True},
          str(verified["frozen_audit"]))

    # v3.2（docs/19 §4 UCA-08）：积分饱和 → 降级留痕，但**不熔断**
    downgraded = ps.step(turns[3]["user_message"], rp.BASE_TS + 180, store,
                         saturated={"valence": True, "arousal": False})
    check("UCA-08 饱和：不熔断、prompt 仍可注入",
          downgraded["blocked"] is False and downgraded["prompt"] is not None,
          str(downgraded["blocked"]))
    check("UCA-08 饱和：audit 记录 downgrades",
          downgraded["audit"]["downgrades"] == ["UCA-08"]
          and downgraded["audit"]["ucas"] == [],
          str(downgraded["audit"]))
    check("UCA-08 饱和：事件留痕",
          downgraded["event"]["audit"]["downgrades"] == ["UCA-08"]
          and downgraded["event"]["blocked"] is False,
          str(downgraded["event"]["audit"]))


def main() -> int:
    print("LINGSHU 验收 · 端到端流水线")
    test_runs()
    test_event_contract()
    test_dropped_whitelist()
    test_onboarding_gate()
    test_idempotent()
    test_text_sanity()
    test_u_shape()
    test_phase_anchor()
    test_preflight_gate()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
