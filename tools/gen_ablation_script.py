# -*- coding: utf-8 -*-
"""三臂消融剧本生成器（确定性，无 RNG，零模型）。

依据：`paper/ablation/DESIGN.md` §2（唯一依据）。本脚本只做"逐字复用 + 机械拼装"：
除 12 条探针（DESIGN §2.3 明确要求"探针文本需新写"）外，**不生成任何新文本**；
每一行都记录 `source` 与 `source_sha256`（源文件原始行的 sha256，或自撰文本的 sha256）。

用法：
    python tools/gen_ablation_script.py --out paper/ablation/script-100.jsonl --window 24000

前置断言（DESIGN §2.1，任一不过即 exit != 0，且**不写产物**）：
    A1 行数 = 100，`turn` 1..100 连续；
    A2 既有语料逐字相等（`text` == 源行 `text`，且 `source_sha256` 可复现）；
    A3 `max_ratio = max_i(Σ_{j<=i} len(text_j) / window) >= 0.85`；
    A4 `phase_trigger_turns`（ratio >= 0.70 的轮）>= 10 轮且全部落在第 60 轮之后；
    A5 探针 12 条齐全（A/B/C/D 各 3）+ 无命中探针 2 条齐全；
    S1 store 语义断言（DESIGN §2.4）：t3 后 `mem_alpha` active；t34 后 `mem_alpha`
       superseded、`mem_beta` active、`mem_seed_01` active，且 `apply_supersede`
       的返回值恰为 `mem_alpha`（走引擎真实路径，不手改 status）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import memory as MEM                      # noqa: E402
from engine import replay as RP                       # noqa: E402
from engine.model import Entry, Store                 # noqa: E402

# ── 语料（相对项目根） ────────────────────────────────────
FLOOD = "chaos-set/long_context_flood.jsonl"
CONFLICT = "chaos-set/memory_conflict.jsonl"
EMOTION = "chaos-set/emotion_jailbreak.jsonl"
VARIETY = "chaos-set/variety.jsonl"
JAILBREAK = "chaos-set/jailbreak_variants.jsonl"
REPLAY = "replay-set/v1.jsonl"

SOURCES = (FLOOD, CONFLICT, EMOTION, VARIETY, JAILBREAK, REPLAY)

# ── 12 条探针（DESIGN §2.3；**跑之前冻结**，不得事后改） ─────────────
# 说明（诚实标注）：C 类是"同义措辞"，但引擎检索是字面 bigram+IDF，
# 若把"项目代号"也换掉同义词，测到的就只是分词器而不是容量上限；
# 因此 C 类保留关键名词（项目/代号），只改写句法。此选择写进结果 validity_flags。
PROBES = (
    # (probe_id, class, text, expected, expected_ids, forbid_ids)
    ("A1", "A", "我最开始告诉过你，我的项目代号是什么？", "贝塔", ["mem_seed_01"], []),
    ("A2", "A", "回想一下最开头，我的项目代号是什么？", "贝塔", ["mem_seed_01"], []),
    ("A3", "A", "对话一开始我提过的项目代号，你还记得吗？", "贝塔", ["mem_seed_01"], []),
    ("B1", "B", "我的项目代号现在是什么？只回答代号。", "贝塔", ["mem_beta"], ["mem_alpha"]),
    ("B2", "B", "现在我的项目代号是什么？直接说代号。", "贝塔", ["mem_beta"], ["mem_alpha"]),
    ("B3", "B", "我改口之后，项目代号变成什么了？", "贝塔", ["mem_beta"], ["mem_alpha"]),
    ("C1", "C", "麻烦你说说，我那个项目的代号到底是哪个？", "贝塔", ["mem_seed_01"], []),
    ("C2", "C", "我把项目代号告诉过你，现在你说说看是什么？", "贝塔", ["mem_seed_01"], []),
    ("C3", "C", "关于项目代号，我最初说的那个是什么来着？", "贝塔", ["mem_seed_01"], []),
    ("D1", "D", "我平时更习惯用哪种输出语言？", "简体中文", ["mem_pref_actr"], []),
    ("D2", "D", "我习惯用什么语言来输出？", "简体中文", ["mem_pref_actr"], []),
    ("D3", "D", "我的输出语言偏好是什么？", "简体中文", ["mem_pref_actr"], []),
)

# ── 写入规格（DESIGN §2.4：三个不同键必须分开） ────────────────────
PLANT_ENTRY = {"action": "put", "id": "mem_seed_01", "subject": "项目A",
               "relation": "代号", "object": "贝塔", "type": "episodic",
               "importance": 0.95, "confidence": 0.95}
PREF_ENTRY = {"id": "mem_pref_actr", "type": "semantic", "subject": "偏好",
              "relation": "输出语言", "object": "简体中文",
              "content": "我平时更习惯用简体中文输出，回复请保持简体中文。",
              "importance": 0.6, "confidence": 0.9, "layer": "warm",
              "status": "active",
              "keywords": ["输出语言", "简体中文", "语言偏好"],
              "access_count": 30, "last_access_age_days": 100}


def sha256_bytes(b: bytes) -> str:
    return hashlib.sha256(b).hexdigest()


def _load(rel: str) -> list[dict]:
    """读取 jsonl，同时返回"原始行字节"以便逐字校验 sha。"""
    path = os.path.join(ROOT, rel)
    raw = open(path, "rb").read()
    lines = raw.split(b"\n")
    out = []
    for ln in lines:
        if not ln.strip():
            continue
        out.append({"obj": json.loads(ln.decode("utf-8")),
                    "sha256": sha256_bytes(ln),
                    "line": len(out) + 1})
    return out


def _rows_src(src: dict, rel: str, line: int) -> str:
    return "%s#%d" % (rel, line)


def _entry_from_expect(eid: str, exp: dict, importance: float,
                       confidence: float = 0.95) -> dict:
    return {"action": "put", "id": eid,
            "subject": str(exp["subject"]), "relation": str(exp["relation"]),
            "object": str(exp["object"]), "type": "episodic",
            "importance": float(importance), "confidence": float(confidence)}


def build_rows(window: int = 24000) -> list[dict]:
    """按 DESIGN §2.2 分段表拼装 100 轮。"""
    flood = _load(FLOOD)
    conflict = _load(CONFLICT)
    emotion = _load(EMOTION)
    variety = _load(VARIETY)
    jail = _load(JAILBREAK)
    replay = _load(REPLAY)

    nohit = [r for r in replay if "no_hit" in (r["obj"].get("labels") or [])][:2]
    if len(nohit) != 2:
        raise SystemExit("FATAL: replay-set 里 no_hit 行不足 2 条（实际 %d）" % len(nohit))

    rows: list[dict] = []

    def add(block, text, source, source_sha, expect,
            probe_id=None, probe_class=None, entry=None, authored=False):
        rows.append({
            "turn": len(rows) + 1,
            "block": block,
            "text": text,
            "expect": expect,
            "probe_id": probe_id,
            "probe_class": probe_class,
            "entry": entry,
            "authored": bool(authored),
            "source": source,
            "source_sha256": source_sha,
        })

    # 1–2 plant（A 类远距埋点；t2 是同义重述，不重复入库，见 §2.4 单键）
    for k in (1, 2):
        r = flood[k - 1]
        add("plant", r["obj"]["text"], _rows_src(r, FLOOD, r["line"]), r["sha256"],
            dict(r["obj"]["expect"]),
            entry=(PLANT_ENTRY if k == 1 else None))

    # 3 plant2_alpha
    r = conflict[0]
    add("plant2_alpha", r["obj"]["text"], _rows_src(r, CONFLICT, r["line"]), r["sha256"],
        dict(r["obj"]["expect"]),
        entry=_entry_from_expect("mem_alpha", r["obj"]["expect"], 0.9))

    # 4–33 distractor（memory_conflict 第 2–31 行）
    for r in conflict[1:31]:
        exp = r["obj"]["expect"]
        add("distractor", r["obj"]["text"], _rows_src(r, CONFLICT, r["line"]),
            r["sha256"], dict(exp),
            entry=_entry_from_expect("mem_d%03d" % len(rows), exp, 0.5))

    # 34 supersede（走引擎真实 apply_supersede 路径，见 §2.4）
    r = conflict[31]
    add("supersede", r["obj"]["text"], _rows_src(r, CONFLICT, r["line"]), r["sha256"],
        dict(r["obj"]["expect"]),
        entry={"action": "supersede", "id": "mem_beta",
               "subject": "项目", "relation": "代号", "object": "贝塔",
               "type": "episodic", "importance": 0.9, "confidence": 0.95})

    # 35–39 insult
    for r in emotion[:5]:
        add("insult", r["obj"]["text"], _rows_src(r, EMOTION, r["line"]), r["sha256"],
            dict(r["obj"]["expect"]))

    # 40–55 flood（long_context_flood 第 3–18 行）
    for r in flood[2:18]:
        add("flood", r["obj"]["text"], _rows_src(r, FLOOD, r["line"]), r["sha256"],
            dict(r["obj"]["expect"]))

    # 56–59 variety（该文件实测 8 行，取前 4；见 §2.6 漂移说明）
    for r in variety[:4]:
        add("variety", r["obj"]["text"], _rows_src(r, VARIETY, r["line"]), r["sha256"],
            dict(r["obj"]["expect"]))

    # 60–75 flood（第 19–34 行）
    for r in flood[18:34]:
        add("flood", r["obj"]["text"], _rows_src(r, FLOOD, r["line"]), r["sha256"],
            dict(r["obj"]["expect"]))

    # 76–81 jailbreak（emotion 第 6 行 + jailbreak_variants 第 1–5 行）
    r = emotion[5]
    add("jailbreak", r["obj"]["text"], _rows_src(r, EMOTION, r["line"]), r["sha256"],
        dict(r["obj"]["expect"]))
    for r in jail[:5]:
        add("jailbreak", r["obj"]["text"], _rows_src(r, JAILBREAK, r["line"]),
            r["sha256"], dict(r["obj"]["expect"]))

    # 82–85 flood（第 35–38 行）
    for r in flood[34:38]:
        add("flood", r["obj"]["text"], _rows_src(r, FLOOD, r["line"]), r["sha256"],
            dict(r["obj"]["expect"]))

    # 86–87 nohit（replay-set 里 labels 含 no_hit 的前 2 行；逐字 user_message）
    for r in nohit:
        add("nohit", r["obj"]["user_message"], _rows_src(r, REPLAY, r["line"]),
            r["sha256"], dict(r["obj"].get("expect") or {}))

    # 88–99 probe（12 条 = A/B/C/D × 3；自撰，冻结在此）
    for pid, cls, text, expected, exp_ids, forbid in PROBES:
        add("probe", text, "authored:paper/ablation/DESIGN.md#§2.3",
            sha256_bytes(text.encode("utf-8")),
            {"probe_class": cls, "expected": expected,
             "expected_ids": list(exp_ids), "forbid_ids": list(forbid)},
            probe_id=pid, probe_class=cls, authored=True)

    # 100 filler（第 39 行）
    r = flood[38]
    add("filler", r["obj"]["text"], _rows_src(r, FLOOD, r["line"]), r["sha256"],
        dict(r["obj"]["expect"]))

    _ = window
    return rows


# ── store 语义（生成器断言 + 执行器共用，单一来源） ────────────────

def make_entry(spec: dict, text: str, now: float):
    return Entry(
        id=spec["id"], type=spec.get("type", "episodic"),
        subject=spec.get("subject", ""), relation=spec.get("relation", ""),
        object=spec.get("object", ""),
        content=spec.get("content") or text,
        importance=float(spec.get("importance", 0.5)),
        confidence=float(spec.get("confidence", 0.95)),
        status=spec.get("status", "active"), layer=spec.get("layer", "warm"),
        keywords=list(spec.get("keywords") or []),
        last_access=now, session_id="abl",
    )


def apply_writes(store, row: dict, now: float, cfg: dict) -> dict | None:
    """按剧本的 `entry` 字段写库。supersede 走引擎真实 `memory.apply_supersede`。

    返回 {"superseded": id|None} 供生成器断言使用。
    """
    spec = row.get("entry")
    if not spec:
        return None
    action = spec.get("action", "put")
    e = make_entry(spec, row["text"], now)
    if action == "supersede":
        superseded = MEM.apply_supersede(store, e, cfg)
        store.put(e)
        return {"superseded": superseded}
    store.put(e)
    return {"superseded": None}


def build_store(rows: list[dict], cfg: dict):
    """重放全部写入（确定性，无 RNG）→ 最终 store。"""
    store = Store()
    for row in rows:
        now = RP.BASE_TS + (int(row["turn"]) - 1) * RP.TURN_STEP
        apply_writes(store, row, now, cfg)
    return store


# ── 断言 ───────────────────────────────────────────────────

def check_rows(rows: list[dict], window: int) -> dict:
    fails: list[str] = []
    notes: list[str] = []

    # A1
    if len(rows) != 100:
        fails.append("A1 行数 %d != 100" % len(rows))
    turns = [int(r["turn"]) for r in rows]
    if turns != list(range(1, 101)):
        fails.append("A1 turn 不是 1..100 连续")

    # A2：逐字校验
    cache: dict[str, list[dict]] = {}
    for r in rows:
        if r["authored"]:
            if sha256_bytes(r["text"].encode("utf-8")) != r["source_sha256"]:
                fails.append("A2 自撰行 sha 不可复现 turn=%d" % r["turn"])
            continue
        path = r["source"].split("#")[0]
        if path not in cache:
            cache[path] = _load(path)
        line = int(r["source"].split("#")[1])
        src = cache[path][line - 1]
        if src["line"] != line:
            fails.append("A2 源行号错位 %s" % r["source"])
        key = "user_message" if "replay-set" in path else "text"
        if src["obj"].get(key) != r["text"]:
            fails.append("A2 非逐字：%s turn=%d" % (r["source"], r["turn"]))
        if src["sha256"] != r["source_sha256"]:
            fails.append("A2 源行 sha 不匹配 %s" % r["source"])

    # A3 / A4：字符累计（口径 = harness，DESIGN §1.1）
    cum, ratios = 0, []
    for r in rows:
        cum += len(r["text"])
        ratios.append(cum / float(window))
    max_ratio = max(ratios)
    phase_trigger = [i + 1 for i, x in enumerate(ratios) if x >= 0.70]
    if max_ratio < 0.85:
        fails.append("A3 max_ratio=%.4f < 0.85" % max_ratio)
    if len(phase_trigger) < 10:
        fails.append("A4 phase_trigger_turns=%d < 10" % len(phase_trigger))
    if phase_trigger and min(phase_trigger) <= 60:
        fails.append("A4 phase_trigger_turns 有轮次 <= 60：%r" % phase_trigger[:5])
    notes.append("total_chars=%d max_ratio=%.4f" % (cum, max_ratio))

    # A5：探针与 no-hit
    probes = [r for r in rows if r.get("probe_id")]
    classes = {}
    for r in probes:
        classes.setdefault(r["probe_class"], []).append(r["probe_id"])
    if len(probes) != 12:
        fails.append("A5 探针数 %d != 12" % len(probes))
    for cls in ("A", "B", "C", "D"):
        if len(classes.get(cls, [])) != 3:
            fails.append("A5 %s 类探针数 != 3" % cls)
    if len({r["probe_id"] for r in probes}) != len(probes):
        fails.append("A5 探针 id 重复")
    nohit_rows = [r for r in rows if r["block"] == "nohit"]
    if len(nohit_rows) != 2:
        fails.append("A5 nohit 行数 %d != 2" % len(nohit_rows))

    # S1：store 语义（DESIGN §2.4）
    from engine import config as CFGM
    cfg = CFGM.default()
    store = Store()
    sup_ret = None
    for r in rows:
        now = RP.BASE_TS + (int(r["turn"]) - 1) * RP.TURN_STEP
        w = apply_writes(store, r, now, cfg)
        if w and w.get("superseded"):
            sup_ret = (int(r["turn"]), w["superseded"])
    e_alpha = store.get("mem_alpha")
    e_beta = store.get("mem_beta")
    e_seed = store.get("mem_seed_01")
    if e_alpha is None or e_alpha.status != "superseded":
        fails.append("S1 mem_alpha.status != superseded")
    if e_beta is None or e_beta.status != "active":
        fails.append("S1 mem_beta.status != active")
    if e_seed is None or e_seed.status != "active":
        fails.append("S1 mem_seed_01.status != active")
    if sup_ret is None or sup_ret[1] != "mem_alpha" or sup_ret[0] != 34:
        fails.append("S1 apply_supersede 返回值不是 mem_alpha@t34（实际 %r）" % (sup_ret,))

    return {"fails": fails, "notes": notes, "max_ratio": max_ratio,
            "phase_trigger_turns": phase_trigger,
            "total_chars": cum,
            "n_store_entries": len(store.all()),
            "probe_ids": [r["probe_id"] for r in probes]}


def _try_extend_flood(rows: list[dict], window: int) -> list[dict]:
    """§2.5：max_ratio/phase 不足时顺延取 flood 行（最多到第 50 行），
    并从 distractor 段尾部等量删行以保持 100 轮。只在断言失败时调用。"""
    flood = _load(FLOOD)
    extra = flood[39:50]           # 第 40–50 行
    if not extra:
        return rows
    added = 0
    out = list(rows)
    for r in extra[:len(extra)]:
        probe_idx = next((i for i, x in enumerate(out) if x["block"] == "probe"), None)
        distr_idx = [i for i, x in enumerate(out) if x["block"] == "distractor"]
        if probe_idx is None or not distr_idx:
            break
        new = {
            "turn": 0, "block": "flood", "text": r["obj"]["text"],
            "expect": dict(r["obj"]["expect"]), "probe_id": None,
            "probe_class": None, "entry": None, "authored": False,
            "source": _rows_src(r, FLOOD, r["line"]), "source_sha256": r["sha256"],
        }
        out.insert(probe_idx, new)
        out.pop(distr_idx[-1])
        added += 1
        for i, x in enumerate(out):
            x["turn"] = i + 1
        chk = check_rows(out, window)
        if not chk["fails"]:
            return out
    if added:
        for i, x in enumerate(out):
            x["turn"] = i + 1
    return out


def generate(window: int = 24000) -> tuple[list[dict], dict]:
    rows = build_rows(window)
    chk = check_rows(rows, window)
    adaptation = None
    if chk["fails"]:
        pre = list(chk["fails"])
        rows2 = _try_extend_flood(rows, window)
        chk2 = check_rows(rows2, window)
        if not chk2["fails"]:
            adaptation = {"reason": "; ".join(pre),
                          "added_flood_turns": len(rows2) - len(rows),
                          "removed": "distractor_tail"}
            rows, chk = rows2, chk2
        else:
            raise SystemExit("FATAL: 前置断言失败（附 §2.5 补偿后仍失败）：\n  " +
                             "\n  ".join(pre + chk2["fails"]))
    chk["script_adaptation"] = adaptation
    return rows, chk


def script_sha256(rows: list[dict]) -> str:
    blob = "\n".join(json.dumps(r, ensure_ascii=False, sort_keys=True) for r in rows)
    return sha256_bytes(blob.encode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="paper/ablation/script-100.jsonl")
    ap.add_argument("--window", type=int, default=24000)
    args = ap.parse_args()

    rows, chk = generate(args.window)
    out_path = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, "w", encoding="utf-8", newline="\n") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False, sort_keys=True) + "\n")

    print("script 已生成：%s" % out_path)
    print("  turns=%d  probe_ids=%s" % (len(rows), ",".join(chk["probe_ids"])))
    print("  total_chars=%d  max_ratio=%.4f" % (chk["total_chars"], chk["max_ratio"]))
    print("  phase_trigger_turns=%r" % (chk["phase_trigger_turns"],))
    print("  store_entries=%d  script_sha256=%s"
          % (chk["n_store_entries"], script_sha256(rows)))
    print("  script_adaptation=%r" % (chk["script_adaptation"],))
    for n in chk["notes"]:
        print("  note: %s" % n)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
