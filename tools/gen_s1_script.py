# -*- coding: utf-8 -*-
"""S1 剧本生成器（探针扩容 + 三 replicate），确定性、零 RNG 内容。

依据：`paper/supplement/PLAN.md` §1（已冻结：`paper/supplement/FREEZE.json`，
PLAN sha256 记录在该清单里）+ `paper/ablation/DESIGN.md` §2 的语料分段口径。

产出：
    1) `paper/ablation/probe-bank-s1.jsonl`  48 条探针库（A/B/C/D 各 12）
    2) `paper/ablation/script-100-s{A,B,C}.jsonl`  三个 100 轮剧本，各 16 探针（每类 4）
    3) `paper/ablation/script-freeze-s1.json`  三剧本 + 探针库的 sha256 冻结清单

与 S0（DESIGN 的 12 探针版）的差别（记入结果 `notes.deviations`）：
    - 每剧本 16 探针 ⇒ 非探针段需从 88 轮降到 84 轮：distractor 段 30 → 26 轮
      （取 `chaos-set/memory_conflict.jsonl` 第 2–27 行），语料其余部分逐字不变；
    - 三剧本分别使用**不同的固定顺序置换** p0/p1/p2（同一 seed=20261027 的
      `random.Random` 生成，冻结在清单里），可置换段 = t4..t84 之间的 10 段。

用法：
    python tools/gen_s1_script.py                 # 生成并冻结（若已存在冻结清单则校验）
    python tools/gen_s1_script.py --sanity        # 只做探针接通性体检（不写文件）
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import gen_ablation_script as G0                      # noqa: E402

PERM_SEED = 20261027
N_PROBE_PER_CLASS_PER_SCRIPT = 4
N_SCRIPTS = 3
SCRIPTS = (("sA", "p0", 0), ("sB", "p1", 1), ("sC", "p2", 2))
OUT_DIR = "paper/ablation"
FREEZE_FILE = "paper/ablation/script-freeze-s1.json"

CLASS_EXPECT = {
    "A": {"expected": "贝塔", "expected_ids": ["mem_seed_01"], "forbid_ids": []},
    "B": {"expected": "贝塔", "expected_ids": ["mem_beta"], "forbid_ids": ["mem_alpha"]},
    "C": {"expected": "贝塔", "expected_ids": ["mem_seed_01"], "forbid_ids": []},
    "D": {"expected": "简体中文", "expected_ids": ["mem_pref_actr"], "forbid_ids": []},
}

# ── 48 条探针库（跑前冻结；A 远距 / B 取代 / C 干扰改写 / D ACT-R） ──────────
PROBES = (
    # A 类：问第 1–2 轮埋的远距设定
    ("A1", "A", "我最开始告诉过你，我的项目代号是什么？"),
    ("A2", "A", "回想一下最开头，我的项目代号是什么？"),
    ("A3", "A", "对话一开始我提过的项目代号，你还记得吗？"),
    ("A4", "A", "最早那会儿我说过项目代号，是什么来着？"),
    ("A5", "A", "一开始我跟你说的项目代号是哪个？"),
    ("A6", "A", "我们刚开始聊的时候，我的项目代号是什么？"),
    ("A7", "A", "你还记得我最初说的项目代号吗？"),
    ("A8", "A", "最开始提到的那个项目代号是什么？"),
    ("A9", "A", "第一轮里我说过的项目代号，还记得吗？"),
    ("A10", "A", "从头讲，我的项目代号是什么？"),
    ("A11", "A", "我在最前面说过的项目代号是什么？"),
    ("A12", "A", "一开始报给你的项目代号还记得吗？"),
    # B 类：旧值被 supersede，问当前值
    ("B1", "B", "我的项目代号现在是什么？只回答代号。"),
    ("B2", "B", "现在我的项目代号是什么？直接说代号。"),
    ("B3", "B", "我改口之后，项目代号变成什么了？"),
    ("B4", "B", "我改主意以后，项目代号现在是多少？"),
    ("B5", "B", "现在这个项目代号是哪个？"),
    ("B6", "B", "你已经知道我改过口了，现在项目代号是什么？"),
    ("B7", "B", "按最新的说法，我的项目代号是什么？"),
    ("B8", "B", "我最后确认的项目代号是什么？"),
    ("B9", "B", "当前我的项目代号是什么？"),
    ("B10", "B", "改过之后，项目代号是多少？"),
    ("B11", "B", "现在生效的项目代号是哪个？"),
    ("B12", "B", "最新的项目代号是什么？"),
    # C 类：同 A 的问法但换措辞（保留关键名词的项目/代号，见 deviations）
    ("C1", "C", "麻烦你说说，我那个项目的代号到底是哪个？"),
    ("C2", "C", "我把项目代号告诉过你，现在你说说看是什么？"),
    ("C3", "C", "关于项目代号，我最初说的那个是什么来着？"),
    ("C4", "C", "你可能记得，我提过一个项目代号，是哪个？"),
    ("C5", "C", "那个项目代号，你能说出来吗？"),
    ("C6", "C", "我问一下，我的项目代号叫啥？"),
    ("C7", "C", "项目代号这事你还有印象吗？是哪个？"),
    ("C8", "C", "你还记得我的项目代号叫什么嘛？"),
    ("C9", "C", "帮我想想，我的项目代号是哪个？"),
    ("C10", "C", "说说看，我项目用的代号是什么？"),
    ("C11", "C", "我那个项目代号，你记的是哪个？"),
    ("C12", "C", "项目代号你还留着吧？是什么？"),
    # D 类：ACT-R 救援（store 预置的高频久远偏好）
    ("D1", "D", "我平时更习惯用哪种输出语言？"),
    ("D2", "D", "我习惯用什么语言来输出？"),
    ("D3", "D", "我的输出语言偏好是什么？"),
    ("D4", "D", "你记得我偏好的输出语言吗？"),
    ("D5", "D", "我平时输出更习惯用哪种语言？"),
    ("D6", "D", "关于输出语言，我的习惯是什么？"),
    ("D7", "D", "我说过习惯用什么语言输出？"),
    ("D8", "D", "我的语言偏好是哪种？"),
    ("D9", "D", "平时让你输出时用哪种语言？"),
    ("D10", "D", "我输出语言的偏好你还记得吗？"),
    ("D11", "D", "我更习惯哪种语言来输出？"),
    ("D12", "D", "你输出的语言按我什么偏好来？"),
)


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def sha256_text(s: str) -> str:
    return sha256_bytes(s.encode("utf-8"))


def probe_bank(script_index: int) -> list[dict]:
    """取该剧本的 16 条探针（每类 4 条，按类分组，顺序固定）。"""
    out = []
    for cls in ("A", "B", "C", "D"):
        pool = [p for p in PROBES if p[1] == cls]
        lo = script_index * N_PROBE_PER_CLASS_PER_SCRIPT
        for pid, c, text in pool[lo:lo + N_PROBE_PER_CLASS_PER_SCRIPT]:
            e = dict(CLASS_EXPECT[c])
            e.update({"probe_class": c, "expected": e["expected"],
                      "expected_ids": list(e["expected_ids"]),
                      "forbid_ids": list(e["forbid_ids"])})
            out.append({"probe_id": pid, "probe_class": c, "text": text,
                        "expect": e, "sha256": sha256_text(text)})
    return out


def build_rows(script_index: int) -> list[dict]:
    """100 轮：t1–3 设定，t4–84 可置换语料段，t85–100 探针（16 条）。"""
    flood = G0._load(G0.FLOOD)
    conflict = G0._load(G0.CONFLICT)
    emotion = G0._load(G0.EMOTION)
    variety = G0._load(G0.VARIETY)
    jail = G0._load(G0.JAILBREAK)
    replay = G0._load(G0.REPLAY)
    nohit = [r for r in replay if "no_hit" in (r["obj"].get("labels") or [])][:2]
    if len(nohit) != 2:
        raise SystemExit("FATAL: replay-set no_hit 行不足 2")

    rows: list[dict] = []

    def add(block, text, source, source_sha, expect, probe_id=None,
            probe_class=None, entry=None, authored=False, segment=None):
        rows.append({"turn": len(rows) + 1, "block": block, "text": text,
                     "expect": expect, "probe_id": probe_id,
                     "probe_class": probe_class, "entry": entry,
                     "authored": bool(authored), "source": source,
                     "source_sha256": source_sha, "segment": segment})

    # ── 固定头部：t1–2 plant / t3 plant2_alpha
    for k in (1, 2):
        r = flood[k - 1]
        add("plant", r["obj"]["text"], "%s#%d" % (G0.FLOOD, r["line"]), r["sha256"],
            dict(r["obj"]["expect"]),
            entry=(G0.PLANT_ENTRY if k == 1 else None), segment="fixed_head")
    r = conflict[0]
    add("plant2_alpha", r["obj"]["text"], "%s#%d" % (G0.CONFLICT, r["line"]),
        r["sha256"], dict(r["obj"]["expect"]),
        entry=G0._entry_from_expect("mem_alpha", r["obj"]["expect"], 0.9),
        segment="fixed_head")

    # ── 可置换段（t4..t84，共 10 段 / 81 轮）
    seg = []

    def seg_add(name, block, text, source, source_sha, expect,
                entry=None, probe_id=None, probe_class=None):
        seg.append({"segment": name, "block": block, "text": text,
                    "source": source, "source_sha256": source_sha,
                    "expect": expect, "entry": entry, "probe_id": probe_id,
                    "probe_class": probe_class, "authored": False})

    # distractor：26 行（memory_conflict 第 2–27 行）
    for j, r in enumerate(conflict[1:27]):
        seg_add("distractor", "distractor", r["obj"]["text"],
                "%s#%d" % (G0.CONFLICT, r["line"]), r["sha256"],
                dict(r["obj"]["expect"]),
                entry=G0._entry_from_expect("mem_d%03d" % (4 + j),
                                            r["obj"]["expect"], 0.5))
    # supersede（走引擎真实 apply_supersede 路径）
    r = conflict[31]
    seg_add("supersede", "supersede", r["obj"]["text"],
            "%s#%d" % (G0.CONFLICT, r["line"]), r["sha256"],
            dict(r["obj"]["expect"]),
            entry={"action": "supersede", "id": "mem_beta", "subject": "项目",
                   "relation": "代号", "object": "贝塔", "type": "episodic",
                   "importance": 0.9, "confidence": 0.95})
    # insult 5
    for r in emotion[:5]:
        seg_add("insult", "insult", r["obj"]["text"],
                "%s#%d" % (G0.EMOTION, r["line"]), r["sha256"],
                dict(r["obj"]["expect"]))
    # flood A（long_context_flood 第 3–18 行）
    for r in flood[2:18]:
        seg_add("flood_a", "flood", r["obj"]["text"],
                "%s#%d" % (G0.FLOOD, r["line"]), r["sha256"],
                dict(r["obj"]["expect"]))
    # variety 4
    for r in variety[:4]:
        seg_add("variety", "variety", r["obj"]["text"],
                "%s#%d" % (G0.VARIETY, r["line"]), r["sha256"],
                dict(r["obj"]["expect"]))
    # flood B（第 19–34 行）
    for r in flood[18:34]:
        seg_add("flood_b", "flood", r["obj"]["text"],
                "%s#%d" % (G0.FLOOD, r["line"]), r["sha256"],
                dict(r["obj"]["expect"]))
    # jailbreak 6
    r = emotion[5]
    seg_add("jailbreak", "jailbreak", r["obj"]["text"],
            "%s#%d" % (G0.EMOTION, r["line"]), r["sha256"],
            dict(r["obj"]["expect"]))
    for r in jail[:5]:
        seg_add("jailbreak", "jailbreak", r["obj"]["text"],
                "%s#%d" % (G0.JAILBREAK, r["line"]), r["sha256"],
                dict(r["obj"]["expect"]))
    # flood C（第 35–38 行）
    for r in flood[34:38]:
        seg_add("flood_c", "flood", r["obj"]["text"],
                "%s#%d" % (G0.FLOOD, r["line"]), r["sha256"],
                dict(r["obj"]["expect"]))
    # nohit 2
    for r in nohit:
        seg_add("nohit", "nohit", r["obj"]["user_message"],
                "%s#%d" % (G0.REPLAY, r["line"]), r["sha256"],
                dict(r["obj"].get("expect") or {}))
    # filler 1
    r = flood[38]
    seg_add("filler", "filler", r["obj"]["text"],
            "%s#%d" % (G0.FLOOD, r["line"]), r["sha256"],
            dict(r["obj"]["expect"]))

    # ── 置换：p0 原序；p1/p2 由 random.Random(20261027) 生成后冻结
    orders = permutation_orders(seg)
    for s in orders[script_index]:
        for x in s:
            add(x["block"], x["text"], x["source"], x["source_sha256"],
                x["expect"], entry=x["entry"], segment=x["segment"])

    # ── 探针段 t85..t100（固定位置）
    for p in probe_bank(script_index):
        add("probe", p["text"], "authored:paper/supplement/PLAN.md#§1.1",
            p["sha256"], dict(p["expect"]), probe_id=p["probe_id"],
            probe_class=p["probe_class"], authored=True, segment="probe")

    return rows


def permutation_orders(seg: list[dict]) -> list[list[list[dict]]]:
    """把 10 个语料段分成 3 组顺序：p0 原序，p1/p2 用固定 seed 洗牌。

    `seg` 是原序的扁平条目；先按 (segment 名 + 相邻) 切成 10 段，再洗牌。
    """
    groups, cur = [], None
    for x in seg:
        if cur is None or x["segment"] != cur[0]["segment"]:
            cur = [x]
            groups.append(cur)
        else:
            cur.append(x)
    if len(groups) != 10:
        raise SystemExit("FATAL: 可置换段数 %d != 10" % len(groups))
    base = [g[0]["segment"] for g in groups]
    outs = [list(groups)]
    rnd = random.Random(PERM_SEED)
    seen = {tuple(base)}
    while len(outs) < N_SCRIPTS:
        perm = list(groups)
        rnd.shuffle(perm)
        key = tuple(g[0]["segment"] for g in perm)
        if key in seen:
            continue
        seen.add(key)
        outs.append(perm)
    return outs


def freeze_manifest(rows_by_script: dict, bank: list[dict]) -> dict:
    m = {
        "schema": "lingshu/s1-script-freeze/v1",
        "plan": "paper/supplement/PLAN.md",
        "plan_sha256": sha256_bytes(open(os.path.join(ROOT, "paper/supplement/PLAN.md"),
                                         "rb").read()),
        "freeze_ref": "paper/supplement/FREEZE.json",
        "freeze_sha256": sha256_bytes(open(os.path.join(ROOT, "paper/supplement/FREEZE.json"),
                                           "rb").read()),
        "perm_seed": PERM_SEED,
        "n_scripts": N_SCRIPTS,
        "probes_per_script": 16,
        "probe_bank": {"n": len(bank),
                       "sha256": sha256_text(
                           "\n".join(json.dumps(p, ensure_ascii=False, sort_keys=True)
                                     for p in bank))},
        "scripts": {},
        "probe_bank_sha256": sha256_text(
            "\n".join(json.dumps(p, ensure_ascii=False, sort_keys=True) for p in bank)),
    }
    for key, _pid, _idx in SCRIPTS:
        rows = rows_by_script[key]
        blob = ("\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True)
                          for r in rows) + "\n")
        m["scripts"][key] = {
            "path": "%s/script-100-%s.jsonl" % (OUT_DIR, key),
            "sha256": sha256_bytes(blob.encode("utf-8")),
            "turns": len(rows),
            "n_probes": sum(1 for r in rows if r.get("probe_id")),
            "probe_ids": [r["probe_id"] for r in rows if r.get("probe_id")],
        }
    return m


def sanity(rows_by_script: dict) -> int:
    """探针接通性体检（跑前、冻结前；**不是结果**）：确认每个探针在该剧本的
    Full/Basic 召回下能否命中期望条目。仅用于发现探针写坏（例如词元完全对不上）。"""
    from engine import config as C, memory as M, replay as RP
    from engine.model import Store
    import run_ablation as RA
    import gen_ablation_script as G
    bad = 0
    print("%-4s %-3s %-4s %-8s %-8s %s" % ("prob", "cls", "scr", "full_id", "basic_id",
                                           "expected_ids"))
    for key, _pid, idx in SCRIPTS:
        rows = rows_by_script[key]
        now_last = RP.BASE_TS + (len(rows) - 1) * RP.TURN_STEP
        for cond, cfg in (("full", C.default()),
                          ("basic", C.apply_path(C.default(), "memory.actrWeight", 0.0))):
            st = RA.new_store(now_last)     # 含 D 类预置
            for r in rows:
                now = RP.BASE_TS + (int(r["turn"]) - 1) * RP.TURN_STEP
                G.apply_writes(st, r, now, C.default())
                if not r.get("probe_id"):
                    continue
                rec = M.recall(st, r["text"], now, cfg, session_id="s1")
                ids = [x["id"] for x in rec["returned"]]
                exp = (r.get("expect") or {}).get("expected_ids") or []
                got = any(e in ids for e in exp)
                if cond == "full":
                    print("%-4s %-3s %-4s %-8s" % (r["probe_id"], r["probe_class"], key, got),
                          end=" ")
                    if not got:
                        bad += 1
                else:
                    print("%-8s %-14s %s" % (got, ",".join(exp), r["probe_id"] if got else ""))
                    if r["probe_class"] in ("A", "B", "C") and not got:
                        bad += 1
    print("\n体检失败项（Full 未命中，或 A/B/C 在 Basic 未命中）：%d" % bad)
    return bad


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--sanity", action="store_true")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    rows_by_script = {key: build_rows(idx) for key, _pid, idx in SCRIPTS}
    bank = [p for i in range(N_SCRIPTS) for p in probe_bank(i)]

    if args.sanity:
        return 1 if sanity(rows_by_script) else 0

    os.makedirs(os.path.join(ROOT, OUT_DIR), exist_ok=True)
    # 写探针库
    bank_path = os.path.join(ROOT, OUT_DIR, "probe-bank-s1.jsonl")
    with open(bank_path, "w", encoding="utf-8", newline="\n") as f:
        for p in bank:
            f.write(json.dumps({"probe_id": p["probe_id"],
                                "probe_class": p["probe_class"],
                                "text": p["text"],
                                "expect": p["expect"],
                                "sha256": p["sha256"]},
                               ensure_ascii=False, sort_keys=True) + "\n")
    # 写三剧本
    for key, _pid, _idx in SCRIPTS:
        p = os.path.join(ROOT, OUT_DIR, "script-100-%s.jsonl" % key)
        with open(p, "w", encoding="utf-8", newline="\n") as f:
            for r in rows_by_script[key]:
                f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")
    manifest = freeze_manifest(rows_by_script, bank)
    man_path = os.path.join(ROOT, FREEZE_FILE)
    with open(man_path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, ensure_ascii=False, indent=1, sort_keys=True)
        f.write("\n")

    print("S1 剧本已生成并冻结：")
    for key, pid, _idx in SCRIPTS:
        m = manifest["scripts"][key]
        rows = rows_by_script[key]
        cum, ratios = 0, []
        for r in rows:
            cum += len(r["text"])
            ratios.append(cum / 24000.0)
        trig = [i + 1 for i, x in enumerate(ratios) if x >= 0.70]
        print("  %s (%s): turns=%d probes=%d max_ratio=%.4f phase_n=%d (%s..%s) sha=%s"
              % (key, pid, m["turns"], m["n_probes"], max(ratios), len(trig),
                 trig[0] if trig else "-", trig[-1] if trig else "-",
                 m["sha256"][:12]))
    print("  probe_bank n=%d sha=%s" % (manifest["probe_bank"]["n"],
                                        manifest["probe_bank"]["sha256"][:12]))
    print("  freeze: %s" % man_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
