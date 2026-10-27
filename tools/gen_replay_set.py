# -*- coding: utf-8 -*-
"""生成 replay-set v1（120 条 turn + 200 条记忆 fixture）。

为什么要生成而不是手写：
- 回放集必须**可复现、可扩版**（改一个数字就是 v2），手写的会腐化；
- 六类场景各 20 条，保证每类都有足够样本算指标。

两条实测踩到的坑，已固化进设计：
1. **纯数字 token 会污染线索**："用户1" 与 "KD1" 共享 token "1"，导致任何带数字的查询
   都能召回一切带数字的条目 → engine/memory.clues() 已过滤纯数字短 token。
2. **同一套措辞会让不同家族互相召回**：曾出现 D 族（冷层）被 F 族（热点）挤掉，
   因为两者都用"结论"二字 → 本文件为每个家族分配**互不重叠的中文词组**，
   并各带唯一拉丁编号（QA/KC/KD/KE/KF）。

场景族：
A supersede      （20）旧值/新值同键 → 只应命中新值
B no_hit         （20）问从未存过的事 → matched=0，不得编造
C cross_session  （20）跨会话召回 → high/state
D cold_rebuild   （20）Cold 层命中 → mid/hedge（不得逐字复述）
E budget         （20）命中但装不下 → excluded_by_budget>0 → mid/hedge
F rank_pressure  （20）目标条目排第 3 → 让 topK 的改动可被观测（区分集）
"""
from __future__ import annotations

import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BASE_TS = 1_800_000_000.0
DAY = 86400.0
PER_FAMILY = 20
# 约 1170 字：三条同族条目合计 ≈1200 token，足以在 900 预算下挤出第三条
FILLER = "这是用于构造预算压力的长文本，内容本身不重要，重要的是它足够长以触发装填截断。" * 30


def entry(eid: str, **kw) -> dict:
    base = {
        "id": eid, "type": "semantic", "subject": "", "relation": "", "object": "",
        "content": "", "importance": 0.9, "source_trust": "user_explicit",
        "status": "active", "layer": "warm", "keywords": [], "depends_on": [],
        "edges": [], "last_access": BASE_TS - 1 * DAY, "session_id": "s_000",
        "title": "",
    }
    base.update(kw)
    return base


def build() -> tuple[list[dict], list[dict]]:
    turns: list[dict] = []
    memos: list[dict] = []
    n = 0

    def add_turn(**kw) -> None:
        nonlocal n
        n += 1
        kw.setdefault("turn_index", n % 5)
        kw.setdefault("source", "synthetic")
        kw.setdefault("version", "v1")
        turns.append(kw)

    for i in range(1, PER_FAMILY + 1):
        # 每个 i 一个会话（10 条 turn）→ 20 个会话，便于灰度按会话分桶
        sess = f"s_{i:03d}"

        # ── A · supersede（"代号" 专属）
        old_id, new_id = f"mem_a{i:03d}_old", f"mem_a{i:03d}_new"
        memos.append(entry(old_id, subject=f"项目{i}", relation="代号", object="ALPHA",
                           content=f"项目{i}的代号为 ALPHA，编号 QA{i}",
                           status="superseded", session_id=sess, title=f"项目{i}旧代号"))
        memos.append(entry(new_id, subject=f"项目{i}", relation="代号", object=f"BETA{i}",
                           content=f"项目{i}的代号为 BETA{i}，编号 QA{i}",
                           session_id=sess, title=f"项目{i}新代号"))
        add_turn(id=f"v1/a{i:03d}", session_id=sess,
                 user_message=f"QA{i} 项目{i}的代号是什么",
                 expected_ids=[new_id], forbidden_ids=[old_id], used_ids=[new_id],
                 expect={"confidence_level": "high", "expression": "state"},
                 labels=["supersede"])

        # ── B · no_hit（拉丁 token，与库中零重叠）
        add_turn(id=f"v1/b{i:03d}", session_id=sess,
                 user_message=f"zxq{i} quasar flux ratio",
                 expected_ids=[], forbidden_ids=[], used_ids=[],
                 expect={"confidence_level": "unknown", "expression": "ask_clarify"},
                 labels=["no_hit"])

        # ── C · cross_session（"界面偏好" 专属）
        cid = f"mem_c{i:03d}"
        memos.append(entry(cid, subject=f"用户{i}", relation="界面偏好",
                           content=f"用户{i}的界面偏好为深色主题{i}号，编号 KC{i}",
                           object=f"深色主题{i}号", importance=0.95,
                           session_id=sess, title=f"用户{i}界面偏好"))
        add_turn(id=f"v1/c{i:03d}", session_id=sess,
                 user_message=f"KC{i} 界面偏好{i}号是什么",
                 expected_ids=[cid], forbidden_ids=[], used_ids=[cid],
                 expect={"confidence_level": "high", "expression": "state"},
                 labels=["cross_session"])

        # ── D · cold_rebuild（"归档摘要" 专属；Cold + 40 天未访问）
        did = f"mem_d{i:03d}"
        memos.append(entry(did, subject=f"归档{i}", relation="归档摘要",
                           content=f"归档{i}的归档摘要为记录 KD{i} 号",
                           object=f"COLD{i}", importance=0.8, layer="cold",
                           session_id=sess, last_access=BASE_TS - 40 * DAY,
                           title=f"归档{i}"))
        add_turn(id=f"v1/d{i:03d}", session_id=sess,
                 user_message=f"KD{i} 归档{i}的归档摘要是什么",
                 expected_ids=[did], forbidden_ids=[], used_ids=[did],
                 expect={"confidence_level": "mid", "expression": "hedge"},
                 labels=["cold_rebuild", "stale"])

        # ── E · budget（"长文内容" 专属；三条长文，第三条装不下）
        eids = [f"mem_e{i:03d}_{k}" for k in range(1, 4)]
        for k, eid in enumerate(eids, 1):
            memos.append(entry(eid, subject=f"长文{i}", relation="长文内容",
                               content=f"长文{i}的长文内容片段{k}，编号 KE{i}：" + FILLER,
                               importance=0.9, session_id=sess, title=f"长文{i}-{k}"))
        add_turn(id=f"v1/e{i:03d}", session_id=sess,
                 user_message=f"KE{i} 长文{i}的长文内容",
                 expected_ids=[eids[0]], forbidden_ids=[], used_ids=[],
                 expect={"confidence_level": "mid", "expression": "hedge"},
                 labels=["budget_pressure"])

        # ── F · rank_pressure（"排序候选" 专属；目标条目 importance 0.50 → 排第 3）
        fids = [f"mem_f{i:03d}_{k}" for k in range(1, 4)]
        for k, eid in enumerate(fids, 1):
            imp = {1: 0.98, 2: 0.90, 3: 0.87}[k]
            memos.append(entry(eid, subject=f"排序条目{i}", relation="排序候选",
                               content=f"排序条目{i}的排序候选{k}，编号 KF{i}",
                               object=f"候选{k}", importance=imp,
                               session_id=sess, title=f"排序{i}-{k}"))
        add_turn(id=f"v1/f{i:03d}", session_id=sess,
                 user_message=f"KF{i} 排序候选",
                 expected_ids=[fids[2]], forbidden_ids=[], used_ids=[fids[2]],
                 expect={"confidence_level": "high", "expression": "state"},
                 labels=["rank_pressure"])

        # ── G · decay_tradeoff（让 τ 可被观测）
        # 4 条同线索条目竞争 3 个名额：谁被挤出取决于 τ，因此 recall@3 会随 τ 变化。
        # 期望命中"新而轻"的那条（g2）：τ 变大 → 老的更重要条目爬上来，把它挤出前 3。
        gspecs = [(1, 0.95, 40.0), (2, 0.55, 2.0), (3, 0.70, 10.0), (4, 0.65, 12.0)]
        gids = {}
        for k, imp, age in gspecs:
            eid = f"mem_g{i:03d}_{k}"
            gids[k] = eid
            memos.append(entry(eid, subject=f"权衡{i}", relation="衰减权衡",
                               content=f"权衡{i}的衰减权衡结论{k}为 G{k}，编号 KG{i}",
                               object=f"结论{k}", importance=imp, session_id=sess,
                               last_access=BASE_TS - age * DAY, title=f"权衡{i}-{k}"))
        add_turn(id=f"v1/g{i:03d}", session_id=sess,
                 user_message=f"KG{i} 衰减权衡",
                 expected_ids=[gids[2]], forbidden_ids=[], used_ids=[gids[2]],
                 expect={"confidence_level": "mid", "expression": "hedge"},
                 labels=["decay_tradeoff"])

        # ── H · diffusion（"扩散链路" 专属；答案只能经边到达）
        # 让 `edge.minWeight` / `edge.diffusionDiscount` 可被观测：
        # 答案本身不含查询词，只能从 seed 经一条 weight=0.5 的边扩散得到；
        # 与两条弱直连命中（importance 0.35）竞争第 2 个名额。
        # 查询刻意只用一个编号 token（KZ{i}）—— 隔离变量，避免跨条目串扰；
        # 真实的多词查询串扰已由 A–G 族覆盖。
        hid, hd1, hd2, hans = (f"mem_h{i:03d}_seed", f"mem_h{i:03d}_d1",
                               f"mem_h{i:03d}_d2", f"mem_h{i:03d}_ans")
        memos.append(entry(hid, subject=f"扩散{i}", relation="扩散链路",
                           content=f"扩散链路起点 KZ{i}",
                           object=f"SEED{i}", importance=0.99, session_id=sess,
                           edges=[{"to": hans, "type": "caused_by",
                                   "weight": 0.5, "origin": "encoder"}],
                           title=f"扩散{i}起点"))
        for tag, eid in (("A", hd1), ("B", hd2)):
            memos.append(entry(eid, subject=f"扩散{i}", relation="扩散链路",
                               content=f"扩散链路旁支{tag} KZ{i}",
                               object=f"BRANCH{tag}{i}", importance=0.35,
                               session_id=sess, title=f"扩散{i}旁支{tag}"))
        memos.append(entry(hans, subject=f"扩散{i}", relation="终点",
                           content=f"终点答案 ANS{i}",
                           object=f"ANS{i}", importance=0.6, session_id=sess,
                           title=f"扩散{i}答案"))
        add_turn(id=f"v1/h{i:03d}", session_id=sess,
                 user_message=f"KZ{i}",
                 expected_ids=[hans], forbidden_ids=[], used_ids=[hans],
                 expect={"confidence_level": "high", "expression": "state"},
                 labels=["diffusion"])

        # ── I · episodic_tau（"情景取舍" 专属；episodic 型，让 τ_episodic 可观测）
        # 4 条竞争 3 个名额；τ 从 7 调到 14 时"更重要但更老"的那条挤掉期望条目。
        ispecs = [(1, 0.90, 0.0), (2, 0.80, 1.0), (3, 0.60, 3.0), (4, 0.95, 9.0)]
        iids = {}
        for k, imp, age in ispecs:
            eid = f"mem_i{i:03d}_{k}"
            iids[k] = eid
            memos.append(entry(eid, type="episodic", subject=f"情景{i}",
                               relation="情景取舍",
                               content=f"情景取舍结论{k}，编号 KY{i}",
                               object=f"结论{k}", importance=imp, session_id=sess,
                               last_access=BASE_TS - age * DAY, title=f"情景{i}-{k}"))
        add_turn(id=f"v1/i{i:03d}", session_id=sess,
                 user_message=f"KY{i}",
                 expected_ids=[iids[3]], forbidden_ids=[], used_ids=[iids[3]],
                 expect={"confidence_level": "high", "expression": "state"},
                 labels=["episodic_tau"])

        # ── J · archive_threshold（"留存阈值" 专属；让 archiveThreshold 可观测）
        # 期望条目的 potency ≈ 0.18：阈值 0.10/0.15 时存活，0.25 时被判为已归档。
        jspecs = [(1, 0.99, 1.0), (2, 0.30, 12.0), (3, 0.45, 12.0)]
        jids = {}
        for k, imp, age in jspecs:
            eid = f"mem_j{i:03d}_{k}"
            jids[k] = eid
            memos.append(entry(eid, subject=f"留存{i}", relation="留存阈值",
                               content=f"留存阈值结论{k}，编号 KX{i}",
                               object=f"结论{k}", importance=imp, session_id=sess,
                               last_access=BASE_TS - age * DAY, title=f"留存{i}-{k}"))
        add_turn(id=f"v1/j{i:03d}", session_id=sess,
                 user_message=f"KX{i}",
                 expected_ids=[jids[2]], forbidden_ids=[], used_ids=[],
                 expect={"confidence_level": "high", "expression": "state"},
                 labels=["archive_threshold"])

    turns.sort(key=lambda t: (t["session_id"], t["turn_index"]))
    return turns, memos


def main() -> None:
    turns, memos = build()
    tdir = os.path.join(ROOT, "replay-set")
    os.makedirs(os.path.join(tdir, "fixtures"), exist_ok=True)
    with open(os.path.join(tdir, "v1.jsonl"), "w", encoding="utf-8", newline="\n") as f:
        for t in turns:
            f.write(json.dumps(t, ensure_ascii=False, sort_keys=True) + "\n")
    with open(os.path.join(tdir, "fixtures", "v1.memories.jsonl"), "w",
              encoding="utf-8", newline="\n") as f:
        for m in memos:
            f.write(json.dumps(m, ensure_ascii=False, sort_keys=True) + "\n")
    from collections import Counter
    print("turns: %d  memories: %d" % (len(turns), len(memos)))
    print("labels:", dict(Counter(l for t in turns for l in t["labels"])))


if __name__ == "__main__":
    main()
