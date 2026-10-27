# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 目标 / 关系 / 叙事（docs/05/06/07）

    python tests/test_agent_state.py

覆盖：
- GOAL：逐字证据必需 / 完成需可验证证据 / 单轮 3 条上限 / 受阻两次→abandoned /
        推断目标 confidence 封顶 / 只发写请求不直写 / 注入最多 2 个目标
- REL：trust 单轮 ±1 / 永不自动升 high / 冲突必须置 unresolved / repair 需确认且不回升 /
       modifier 硬边界与冲突上限 / 阶段迁移 / 只发写请求
- NAR：触发词闭集 / 节点可追溯 / 因果只取已有边 / gap 显式 / arc 最少节点 /
       unknown 不拼装 / 不持久化 / 最多 12 节点
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config as cfgmod            # noqa: E402
from engine import goal as G                   # noqa: E402
from engine import narrative as N              # noqa: E402
from engine import relation as R               # noqa: E402
from engine.model import Entry                 # noqa: E402

PASS, FAIL = [], []
DAY = 86400.0


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))


def _cfg():
    return cfgmod.default()


# ── 目标 ─────────────────────────────────────────────────
def test_goal() -> None:
    print("\n[GOAL]")
    cfg = _cfg()
    p = G.GoalPlugin(cfg)
    g = p.add("把记忆插件写完", source="user_explicit")
    check("新建目标默认 active", g.status == "active")

    r = p.update("我写完了", intent="instruct", event="progress", quote="我写完了")
    check("有逐字证据才记录事件", len(r["goal_events"]) == 1)
    r2 = p.update("我写完了", intent="instruct", event="progress", quote="")
    check("缺证据 → 不记录", len(r2["goal_events"]) == 0)

    r3 = p.update("完成了", intent="instruct", event="complete", quote="完成了")
    check("完成无证据 → 不判完成", len(r3["goal_events"]) == 0)
    r4 = p.update("完成了", intent="instruct", event="complete", quote="完成了",
                  evidence="工具成功：write ok")
    check("完成有证据 → 记录", len(r4["goal_events"]) == 1)
    check("完成后目标状态 done", p.goals[g.goal_id].status == "done")

    # 受阻两次 → abandoned
    p2 = G.GoalPlugin(cfg)
    g2 = p2.add("部署上线", source="user_explicit")
    for i in range(2):
        p2.update("卡住了", intent="challenge", event="obstruction",
                  quote="卡住了", goal_id=g2.goal_id, next_step="再试一次")
    check("受阻达上限 → abandoned",
          p2.goals[g2.goal_id].status == "abandoned",
          p2.goals[g2.goal_id].status)

    # 意图与事件不匹配
    r5 = p2.update("随便说说", intent="express", event="progress", quote="随便说说")
    check("express 不允许 progress 事件", len(r5["goal_events"]) == 0)

    # 推断目标 confidence 封顶
    g3 = p2.add("可能想学英语", source="user_implicit", confidence=0.9)
    check("推断目标 confidence ≤ 0.5", g3.confidence <= 0.5, str(g3.confidence))
    check("推断目标不能直接激活", p2.activate(g3.goal_id) is False)

    # 只发写请求
    check("产出 memory/write_request 而不是直写",
          r4["write_requests"] and r4["write_requests"][0]["origin"] == "goal")

    # 单轮上限
    evs = p2.trim_events([{"i": i} for i in range(5)])
    check("单轮事件 ≤ 3", len(evs) == cfg["goal"]["maxEventsPerTurn"], str(len(evs)))

    # 注入最多 2 个
    p3 = G.GoalPlugin(cfg)
    for k in range(4):
        p3.add("目标%d" % k, priority=0.5 + k * 0.1)
    inj = p3.injection()
    check("注入最多 2 个目标", inj.count("活跃目标:") == 2, str(inj.count("活跃目标:")))
    check("注入不提 goal_id", "goal_" not in inj)


# ── 关系 ─────────────────────────────────────────────────
def test_relation() -> None:
    print("\n[REL]")
    cfg = _cfg()
    p = R.RelationPlugin(cfg)
    s = p.state
    check("初始 trust=low / stage=new", (s.trust_level, s.stage) == ("low", "new"))

    # 单轮最多升一档 + 需累计 3 次正面
    for _ in range(3):
        p.update([{"type": "positive", "evidence_quote": "谢谢"}])
    check("累计 3 次正面 → mid", s.trust_level == "mid", s.trust_level)

    # 永不自动升 high
    for _ in range(10):
        p.update([{"type": "positive", "evidence_quote": "谢谢"}])
    check("永不自动升 high", s.trust_level == "mid", s.trust_level)
    p.update([{"type": "positive", "evidence_quote": "谢谢"}], user_confirmed_high=True)
    check("用户确认后可升 high", s.trust_level == "high", s.trust_level)

    # 缺证据不记录
    r = p.update([{"type": "positive", "evidence_quote": ""}])
    check("缺逐字证据 → 不记录", r["changed"] is False)

    # 冲突必须置 unresolved
    p2 = R.RelationPlugin(cfg)
    p2.update([{"type": "conflict", "evidence_quote": "你错了", "severity": "mid"}])
    check("冲突 → unresolved_conflict=true", p2.state.unresolved_conflict)
    check("冲突 → stage=conflict", p2.state.stage == "conflict")
    check("冲突时 modifier ≤ 1.0",
          p2.state.modifier({"relation": cfg["relation"]}) <= 1.0,
          str(p2.state.modifier({"relation": cfg["relation"]})))

    # repair 需用户确认，且 trust 不回升
    before = p2.state.trust_level
    p2.update([{"type": "repair", "evidence_quote": "我们和好吧"}], user_confirmed_repair=False)
    check("未确认的 repair 不清冲突", p2.state.unresolved_conflict)
    p2.update([{"type": "repair", "evidence_quote": "我们和好吧"}], user_confirmed_repair=True)
    check("确认后 repair 清冲突", not p2.state.unresolved_conflict)
    check("repair 不回升 trust", p2.state.trust_level == before)

    # modifier 硬边界
    for lvl in R.TRUST_LEVELS:
        s.trust_level = lvl
        s.interaction_density = 1.0
        m = s.modifier({"relation": cfg["relation"]})
        check("modifier 在 [0.8,1.2]（%s）" % lvl, 0.8 <= m <= 1.2, str(m))

    # 阶段推进
    p3 = R.RelationPlugin(cfg)
    for _ in range(3):
        p3.tick_session()
    check("3 次会话 → stage=regular", p3.state.stage == "regular", p3.state.stage)

    # 只发写请求
    rr = p3.update([{"type": "commitment", "evidence_quote": "我明天给你",
                     "content": "明天交付"}])
    check("承诺入库到 state", len(p3.state.commitments) == 1)
    check("产出 memory/write_request", rr["write_requests"][0]["origin"] == "relation")


# ── 叙事 ─────────────────────────────────────────────────
def _entries(n: int) -> list[Entry]:
    out = []
    for i in range(n):
        out.append(Entry.from_json({
            "id": "mem_%03d" % i, "type": "episodic", "subject": "项目", "relation": "进展",
            "object": "o%d" % i, "content": "第 %d 个里程碑" % i, "importance": 0.8,
            "confidence": 0.9 if i % 2 == 0 else 0.5,
            "last_access": 1_800_000_000.0 + i * 10 * DAY,
        }))
    return out


def test_narrative() -> None:
    print("\n[NAR]")
    cfg = _cfg()
    ents = _entries(15)

    r0 = N.compose(ents, "今天天气不错", cfg)
    check("非时间语义查询不触发", r0["triggered"] is False)

    r = N.compose(ents, "梳理一下项目历史", cfg)
    check("触发词命中", r["triggered"] is True)
    check("节点 ≤ 12", len(r["nodes"]) == 12, str(len(r["nodes"])))
    check("每个节点都有 entry_id", all(n["entry_id"] for n in r["nodes"]))
    check("节点按时间升序",
          all(r["nodes"][i]["at"] <= r["nodes"][i + 1]["at"] for i in range(len(r["nodes"]) - 1)))
    check("arc 在节点 ≥3 时生成", bool(r["arc"]))
    check("confidence 取最小值", abs(r["confidence"] - 0.5) < 1e-9, str(r["confidence"]))
    check("不持久化", r["persisted"] is False)

    ru = N.compose(ents, "回顾一下", cfg, confidence_level="unknown")
    check("元认知 unknown → 不拼装", ru["nodes"] == [] and ru["reason"] == "no_record")

    # gap：10 天间隔 vs 阈值 30 天 → 无 gap；改成 40 天间隔 → 有 gap
    far = _entries(3)
    for i, e in enumerate(far):
        e.last_access = 1_800_000_000.0 + i * 40 * DAY
    rg = N.compose(far, "时间线", cfg)
    check("超阈值间隔 → 标 gap", len(rg["gaps"]) == 2, str(len(rg["gaps"])))

    # 因果链只取已有边
    a, b = _entries(2)
    a.edges = [{"to": b.id, "type": "caused_by", "weight": 0.7}]
    a.edges.append({"to": "mem_nonexistent", "type": "caused_by", "weight": 0.5})
    rl = N.compose([a, b], "后来怎么走到", cfg)
    check("因果链只取已有边", len(rl["links"]) == 1 and rl["links"][0]["to"] == b.id,
          str(rl["links"]))

    # 节点少于 3 → 无 arc
    small = N.compose(_entries(2), "回顾", cfg)
    check("节点 <3 不生成 arc", small["arc"] is None)

    # 渲染不崩且带来源
    txt = N.render(r)
    check("渲染含来源 entry_id", "来源 mem_" in txt)
    check("空结果渲染为空串", N.render(r0) == "")


def main() -> int:
    print("LINGSHU 验收 · 目标 / 关系 / 叙事")
    test_goal()
    test_relation()
    test_narrative()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
