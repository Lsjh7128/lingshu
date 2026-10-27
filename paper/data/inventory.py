# -*- coding: utf-8 -*-
"""LINGSHU 论文 · 数据资产盘点器（H0 只读阶段）

只做三件事，全部是**只读**、**确定性**、**零模型调用**：
1. 逐文件实数（字节 / 行 / sha256 / 字段集合）；
2. 数据集级汇总（turn 数、记忆条数、事件分布、真实宿主调用数）；
3. 可行性静态计算（只用 engine/ 参考实现算"有没有信号"，**不跑任何消融臂**）。

用法（PowerShell，仓库根 <REPO_ROOT>）：

    $py = "<PYTHON>"

    # 1) 人读：markdown 表格（贴进 INVENTORY.md）
    & $py paper\\data\\inventory.py --markdown

    # 2) 机读：全部事实 → paper/data/_facts.json（可 diff、可复现校验）
    & $py paper\\data\\inventory.py --json > paper\\data\\_facts.json

    # 3) 复现校验：重算并与 _facts.json 逐项比对（0 差异才 exit 0）
    & $py paper\\data\\inventory.py --verify

诚实性约束（写死在代码里，不靠自觉）：
- 任何"条数/字节"都来自本次实际读取，没有常量表；
- 合成/真实标签来自数据自身字段（`source`、`synthetic`、`mode`）或明确的文件位置，
  不从文件名猜；推不出的标 `unknown`，绝不当 0；
- `--markdown` 输出末尾会打印本脚本的 sha256，便于核对文档与脚本版本。
"""
from __future__ import annotations

import collections
import glob
import hashlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(HERE))          # <REPO_ROOT>
FACTS_PATH = os.path.join(HERE, "_facts.json")

# ---------------------------------------------------------------- 基础工具

def rel(p: str) -> str:
    return os.path.relpath(p, ROOT).replace("\\", "/")


def sha256_file(p: str) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def read_jsonl(p: str) -> tuple[list[dict], int]:
    """返回 (解析成功的对象, 坏行数)。坏行不吞掉，计数上报。"""
    rows, bad = [], 0
    with open(p, encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except Exception:
                bad += 1
    return rows, bad


def file_facts(p: str) -> dict:
    raw = open(p, "rb").read()
    text = raw.decode("utf-8", "replace")
    return {
        "path": rel(p),
        "bytes": len(raw),
        "lines": len([l for l in text.splitlines() if l.strip()]),
        "sha256": sha256_file(p),
        "ext": os.path.splitext(p)[1],
    }


def keys_union(objs: list[dict]) -> list[str]:
    ks: set[str] = set()
    for o in objs:
        if isinstance(o, dict):
            ks.update(o.keys())
    return sorted(ks)


# ---------------------------------------------------------------- 文件清单

# 只有这些前缀下的文件算"论文数据源"；其余（源码、文档、pyc）不进表。
DATA_GLOBS = [
    "replay-set/*.jsonl",
    "replay-set/fixtures/*.jsonl",
    "chaos-set/*.jsonl",
    "eval-set/*.jsonl",
    "acceptance/chaos/*.json",
    "acceptance/chaos/.last.json",
    "acceptance/provenance-candidates.json",
    "acceptance/shadow-*.md",
    "acceptance/unfreeze-status.md",
    ".chaos-data/*",
    ".chaos-data-s2/*",
    ".chaos-data-s4/*",
    ".chaos-data-uca08/*",
    "chaos-runs/_smoke/data/*",
    "chaos-runs/_smoke/*.jsonl",
    "chaos-runs/_smoke/*.txt",
    "chaos-runs/_probe_ratio/data/*",
    "chaos-runs/_baseline-h0.txt",
    "console/*.html",
    "daily-chaos-report.md",
]


def collect_files() -> list[dict]:
    seen, out = set(), []
    for g in DATA_GLOBS:
        for p in sorted(glob.glob(os.path.join(ROOT, g.replace("/", os.sep)))):
            if not os.path.isfile(p):
                continue
            r = rel(p)
            if r in seen:
                continue
            seen.add(r)
            out.append(file_facts(p))
    out.sort(key=lambda x: x["path"])
    return out


# ---------------------------------------------------------------- 数据集级汇总

def dataset_facts() -> dict:
    facts: dict = {}

    # replay-set v1
    turns, bad_t = read_jsonl(os.path.join(ROOT, "replay-set", "v1.jsonl"))
    memo, bad_m = read_jsonl(os.path.join(ROOT, "replay-set", "fixtures", "v1.memories.jsonl"))
    corr, bad_c = read_jsonl(os.path.join(ROOT, "replay-set", "fixtures", "v1.corrections.jsonl"))
    facts["replay"] = {
        "turns": len(turns),
        "bad_lines": bad_t,
        "sessions": len({t["session_id"] for t in turns}),
        "turns_per_session": dict(collections.Counter(t["session_id"] for t in turns).most_common(1)),
        "turn_index_values": sorted({t["turn_index"] for t in turns}),
        "turn_index_hist": dict(sorted(collections.Counter(t["turn_index"] for t in turns).items())),
        "source_labels": dict(collections.Counter(t.get("source") for t in turns)),
        "labels": dict(sorted(collections.Counter(l for t in turns for l in t.get("labels", [])).items())),
        "turns_with_expected": sum(1 for t in turns if t["expected_ids"]),
        "turns_with_forbidden": sum(1 for t in turns if t["forbidden_ids"]),
        "expected_id_total": sum(len(t["expected_ids"]) for t in turns),
        "used_ids_nonempty": sum(1 for t in turns if t.get("used_ids")),
        "expect_confidence": dict(collections.Counter((t.get("expect") or {}).get("confidence_level") for t in turns)),
        "expect_expression": dict(collections.Counter((t.get("expect") or {}).get("expression") for t in turns)),
        "schema_keys": keys_union(turns),
        "memories": len(memo),
        "memories_bad_lines": bad_m,
        "memory_schema_keys": keys_union(memo),
        "memory_status": dict(collections.Counter(m.get("status") for m in memo)),
        "memory_layer": dict(collections.Counter(m.get("layer") for m in memo)),
        "memory_type": dict(collections.Counter(m.get("type") for m in memo)),
        "memory_source_trust": dict(collections.Counter(m.get("source_trust") for m in memo)),
        "corrections": len(corr),
        "corrections_bad_lines": bad_c,
        "corrections_kind": dict(collections.Counter(c.get("kind") for c in corr)),
    }

    # 「设定项」候选：按 id 家族 + subject/relation/object 三元组身份，由数据字段判定，不手写名单。
    fam = collections.Counter()
    for m in memo:
        i = m.get("id", "")
        if i.startswith("mem_") and len(i) > 4 and i[4].isalpha():
            fam[i[4]] += 1
    facts["replay"]["memory_id_families"] = dict(sorted(fam.items()))
    settings = []
    for m in memo:
        mid = m.get("id", "")
        if (mid.startswith("mem_a") and mid.endswith("_new")) or mid.startswith("mem_c"):
            settings.append({
                "id": mid, "subject": m.get("subject"), "relation": m.get("relation"),
                "object": m.get("object"), "status": m.get("status"), "layer": m.get("layer"),
                "importance": m.get("importance"), "session_id": m.get("session_id"),
                "type": m.get("type"),
            })
    facts["settings_candidates"] = {
        "rule": "id ∈ {mem_a*_new} ∪ {mem_c*}（A 族=项目代号改口后的新值；C 族=界面偏好）",
        "count": len(settings),
        "a_family_new": sum(1 for s in settings if s["id"].startswith("mem_a")),
        "c_family": sum(1 for s in settings if s["id"].startswith("mem_c")),
        "all_active": all(s["status"] == "active" for s in settings),
        "sessions": len({s["session_id"] for s in settings}),
        "sample": settings[:3],
    }

    # chaos-set 五份语料
    chaos = {}
    for name in ("long_context_flood", "memory_conflict", "emotion_jailbreak", "variety", "jailbreak_variants"):
        p = os.path.join(ROOT, "chaos-set", name + ".jsonl")
        rows, bad = read_jsonl(p)
        turns_idx = [r.get("turn") for r in rows]
        entry = {
            "lines": len(rows), "bad_lines": bad,
            "scenarios": dict(collections.Counter(r.get("scenario") for r in rows)),
            "roles": dict(collections.Counter(r.get("role") for r in rows)),
            "turn_monotonic_from_1": turns_idx == list(range(1, len(rows) + 1)),
            "expect_keys": keys_union([r.get("expect") or {} for r in rows]),
        }
        if name == "long_context_flood":
            entry["text_len_min"] = min(len(r.get("text", "")) for r in rows)
            entry["text_len_max"] = max(len(r.get("text", "")) for r in rows)
            entry["seed_rows"] = [r["turn"] for r in rows if (r.get("expect") or {}).get("seed")]
            entry["key_question_row"] = [r["turn"] for r in rows if (r.get("expect") or {}).get("key_question")]
        if name == "jailbreak_variants":
            entry["families"] = dict(sorted(collections.Counter(
                (r.get("expect") or {}).get("family") for r in rows).items()))
            entry["expect_blocked_true"] = sum(1 for r in rows if (r.get("expect") or {}).get("blocked") is True)
            entry["expect_blocked_false"] = sum(1 for r in rows if (r.get("expect") or {}).get("blocked") is False)
            entry["has_plaintext_trigger"] = sum(
                1 for r in rows if (r.get("expect") or {}).get("matched_patterns"))
        if name == "memory_conflict":
            entry["supersede_rows"] = [r["turn"] for r in rows if (r.get("expect") or {}).get("supersede")]
            entry["write_rows"] = sum(1 for r in rows if (r.get("expect") or {}).get("write"))
        if name == "emotion_jailbreak":
            entry["insult_rows"] = [r["turn"] for r in rows if (r.get("expect") or {}).get("insult")]
            entry["jailbreak_rows"] = [r["turn"] for r in rows if (r.get("expect") or {}).get("jailbreak")]
        if name == "variety":
            entry["distinct_false_facts"] = len({
                ((r.get("expect") or {}).get("signals") or {}).get("false_fact") for r in rows})
        chaos[name] = entry
    facts["chaos"] = chaos

    # eval-set
    ev, bad_ev = read_jsonl(os.path.join(ROOT, "eval-set", "agent_state-v1.jsonl"))
    facts["eval_set"] = {
        "rows": len(ev), "bad_lines": bad_ev, "schema_keys": keys_union(ev),
        "goal": dict(collections.Counter(r.get("goal") for r in ev)),
        "has_ground_truth_field": any(k in keys_union(ev) for k in ("label", "answer", "gold")),
    }

    # acceptance/chaos/*.json（运行产物）
    runs = []
    for p in sorted(glob.glob(os.path.join(ROOT, "acceptance", "chaos", "*.json"))):
        o = json.load(open(p, encoding="utf-8"))
        if "scenarios" not in o:
            runs.append({"file": rel(p), "kind": "aux", "top_keys": sorted(o.keys())})
            continue
        sc = o["scenarios"]
        s1 = sc.get("long_context_flood") or {}
        s2 = sc.get("memory_conflict") or {}
        s3 = sc.get("emotion_jailbreak") or {}
        s4 = sc.get("variety") or {}
        kq = s1.get("key_question") or {}
        runs.append({
            "file": rel(p),
            "kind": "run",
            "run_id": o.get("run_id"),
            "mode": o.get("mode"),
            "corpus_source": o.get("corpus_source"),
            "skipped": [k for k, v in sc.items() if v.get("skipped")],
            "model_calls": {k: v.get("model_calls") for k, v in sc.items()},
            "model_calls_sum": sum(v for v in (x.get("model_calls") for x in sc.values())
                                   if isinstance(v, int)),
            "s1_turns": s1.get("turns"),
            "s1_max_ratio": s1.get("max_ratio"),
            "s1_keyword_hit": kq.get("keyword_hit"),
            "s1_answer_source": kq.get("answer_source"),
            "s1_answer": kq.get("answer"),
            "s1_degraded_at_ratio": s1.get("degraded_at_ratio"),
            "s2_beta_hit": s2.get("beta_hit"),
            "s2_alpha_leak": s2.get("alpha_leak"),
            "s2_supersede_events": s2.get("supersede_events"),
            "s3_blocked": (s3.get("stpa") or {}).get("blocked"),
            "s3_ucas": (s3.get("stpa") or {}).get("ucas"),
            "s3_sources": (s3.get("stpa") or {}).get("sources"),
            "s3_saturated": s3.get("saturated"),
            "s4_fallback": s4.get("fallback_triggered"),
            "s4_setup_synthetic": (s4.get("setup") or {}).get("synthetic"),
            "summary": o.get("summary"),
            "quality_labels_total": (o.get("quality_labels") or {}).get("total"),
        })
    # .last.json 单列（它不在 *.json 通配里）
    last_p = os.path.join(ROOT, "acceptance", "chaos", ".last.json")
    last = json.load(open(last_p, encoding="utf-8"))
    facts["chaos_runs"] = {
        "run_files": runs,
        "run_count": sum(1 for r in runs if r["kind"] == "run"),
        "engine_run_count": sum(1 for r in runs if r["kind"] == "run" and r["mode"] == "engine"),
        "host_run_count": sum(1 for r in runs if r["kind"] == "run" and r["mode"] == "host"),
        "host_model_calls_total": sum(r.get("model_calls_sum") or 0
                                     for r in runs if r.get("mode") == "host"),
        "last_run_id": last.get("run_id"),
        "last_mode": last.get("mode"),
    }

    # .chaos-data* 真实宿主隔离目录
    dirs = [".chaos-data", ".chaos-data-s2", ".chaos-data-s4", ".chaos-data-uca08",
            "chaos-runs/_smoke/data", "chaos-runs/_probe_ratio/data"]
    host = {}
    for d in dirs:
        base = os.path.join(ROOT, d.replace("/", os.sep))
        audit_p = os.path.join(base, "audit.jsonl")
        if not os.path.isfile(audit_p):
            continue
        evs, bad = read_jsonl(audit_p)
        kinds = collections.Counter(e.get("kind") for e in evs)
        pre = [e for e in evs if e.get("kind") == "pre_step"]
        mem = []
        mem_p = os.path.join(base, "memory.jsonl")
        if os.path.isfile(mem_p):
            mem, _ = read_jsonl(mem_p)
        st = {}
        st_p = os.path.join(base, "state.json")
        if os.path.isfile(st_p):
            st = json.load(open(st_p, encoding="utf-8"))
        host[d] = {
            "audit_events": len(evs), "audit_bad_lines": bad,
            "turns_observed": int((kinds.get("turn_begin") or 0)),
            "pre_step": len(pre),
            "apply_events": int(kinds.get("apply") or 0),
            "verdicts": dict(collections.Counter(e.get("verdict") for e in pre)),
            "pre_step_ucas": dict(collections.Counter(u for e in pre for u in (e.get("ucas") or []))),
            "preflight_block_events": int(kinds.get("preflight_block") or 0),
            "preflight_downgrade_events": int(kinds.get("preflight_downgrade") or 0),
            "memory_write_events": int(kinds.get("memory_write") or 0),
            "memory_supersede_events": int(kinds.get("memory_supersede") or 0),
            "memory_conflict_events": int(kinds.get("memory_conflict") or 0),
            "fallback_applied_events": int(kinds.get("fallback_applied") or 0),
            "ratios": [e.get("ratio") for e in pre],
            "phases": dict(collections.Counter(e.get("phase") for e in pre)),
            "memory_entries": len(mem),
            "state_turns": st.get("turns"),
            "state_context_window": (st.get("control") or {}).get("contextWindow"),
            "last_prompt_bytes": (os.path.getsize(os.path.join(base, "last-prompt.txt"))
                                  if os.path.isfile(os.path.join(base, "last-prompt.txt")) else None),
        }
    facts["host_dirs"] = host

    # 总账
    syn_turns = facts["replay"]["turns"] + sum(v["lines"] for v in chaos.values()) + facts["eval_set"]["rows"]
    real_turns = sum(v["turns_observed"] for v in host.values())
    facts["ledger"] = {
        "synthetic_frames": syn_turns,
        "real_host_turns": real_turns,
        "total_frames": syn_turns + real_turns,
        "synthetic_share": round(syn_turns / (syn_turns + real_turns), 4) if (syn_turns + real_turns) else None,
        "synthetic_memory_entries": facts["replay"]["memories"],
        "real_host_memory_entries": sum(v["memory_entries"] for v in host.values()),
        "host_model_calls_recorded": facts["chaos_runs"]["host_model_calls_total"],
        "breakdown": {
            "replay_turns": facts["replay"]["turns"],
            "chaos_set_lines": {k: v["lines"] for k, v in chaos.items()},
            "chaos_set_lines_total": sum(v["lines"] for v in chaos.values()),
            "eval_set_rows": facts["eval_set"]["rows"],
            "host_turns_by_dir": {k: v["turns_observed"] for k, v in host.items()},
        },
    }
    return facts


# ---------------------------------------------------------------- 可行性静态计算

def feasibility() -> dict:
    """只用 engine/ 参考实现做**静态**计算：证明回放集有没有信号空间。

    这不是消融实验：没有手臂、没有对比、没有结论，只有"天花板在哪"这一事实。
    证据等级按 docs/20 §1.2 = 「由实现计算」（输入是合成数据）。
    """
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from engine import config as cfgmod
    from engine import memory, replay as rp
    from engine.model import Entry, Store

    cfg = cfgmod.default()
    turns, store = rp.load_dataset(ROOT, "v1")
    memo, _ = read_jsonl(os.path.join(ROOT, "replay-set", "fixtures", "v1.memories.jsonl"))

    # 1) 时间轴：100 轮 = 100 * TURN_STEP 秒；衰减因子（纯衰减通道）
    step = rp.TURN_STEP
    base = rp.BASE_TS
    out_turns = {}
    for idx in (0, 49, 99):
        now = base + idx * step
        sampled = []
        for m in memo:
            if not ((m["id"].startswith("mem_a") and m["id"].endswith("_new"))
                    or m["id"].startswith("mem_c")):
                continue
            e = Entry.from_json(m)
            sampled.append({
                "id": e.id,
                "potency_at_turn": round(memory.potency(e, now, cfg), 6),
            })
        dec = []
        for m in memo:
            if m["id"].startswith("mem_a") and m["id"].endswith("_new"):
                e = Entry.from_json(m)
                la = float(m.get("last_access") or 0.0)
                tau_days = cfg["decay"]["tau"][m.get("type", "semantic")]
                imp = float(m.get("importance", 0.9))
                dt_days = (now - la) / 86400.0
                dec.append({
                    "id": e.id, "dt_days": round(dt_days, 4),
                    "tau_eff_days": round(tau_days * (0.5 + imp), 4),
                    "decay_factor": round(2.718281828459045 ** (-dt_days / (tau_days * (0.5 + imp))), 6),
                })
        out_turns[idx] = {
            "now": now,
            "settings_potency_min": min(s["potency_at_turn"] for s in sampled),
            "settings_potency_max": max(s["potency_at_turn"] for s in sampled),
            "archive_threshold": cfg["potency"]["archiveThreshold"],
            "below_archive": sum(1 for s in sampled if s["potency_at_turn"] < cfg["potency"]["archiveThreshold"]),
            "decay_example": dec[0] if dec else None,
        }

    # 2) 仓库内已有回放基线断言（不是本次跑出来的，是 tests 里的冻结断言）
    frozen = []
    for tp in ("test_control.py", "test_pipeline_integration.py", "test_learning.py"):
        p = os.path.join(ROOT, "tests", tp)
        if not os.path.isfile(p):
            continue
        for i, line in enumerate(open(p, encoding="utf-8").read().splitlines(), 1):
            if "recall_at_3" in line or "recall@3" in line:
                frozen.append({"file": "tests/" + tp, "at_line": i, "text": line.strip()[:170]})

    # 3) 越狱样本：语料里"expect=blocked"由谁生成？看 matched_patterns 是否存在（同义反复证据）
    jb, _ = read_jsonl(os.path.join(ROOT, "chaos-set", "jailbreak_variants.jsonl"))
    jb_with_patterns = sum(1 for r in jb if (r.get("expect") or {}).get("matched_patterns"))
    # 引擎闸门词表（读源码，不执行攻击）
    ctl = open(os.path.join(ROOT, "engine", "control.py"), encoding="utf-8").read()
    pattern_block = ctl.split("_OVERRIDE_PATTERNS")[1].split(")")[0]

    # 4) variety / jailbreak_variants 是否有消费者（读执行器源码统计）
    run_src = open(os.path.join(ROOT, "tools", "run_chaos.py"), encoding="utf-8").read()
    consumers = {
        "jailbreak_variants_mentioned": "jailbreak_variants" in run_src,
        "SCENARIOS_line": next((l.strip() for l in run_src.splitlines() if l.strip().startswith("SCENARIOS")), None),
        "variety_rows_consumed": "for r in rows" in run_src.split("def engine_variety")[1].split("def ")[0]
                                 if "def engine_variety" in run_src else None,
    }

    return {
        "evidence_grade": "由实现计算（输入为合成数据）；不是消融结果",
        "turn_step_seconds": step,
        "base_ts": base,
        "turns_100_span_days": round(99 * step / 86400.0, 5),
        "settings_potency_by_turn": out_turns,
        "frozen_test_assertions": frozen,
        "jailbreak_tautology": {
            "corpus_lines": len(jb),
            "lines_with_matched_patterns": jb_with_patterns,
            "engine_override_patterns": pattern_block.strip()[:220],
        },
        "corpus_consumers": consumers,
    }


# ---------------------------------------------------------------- 输出

def fmt_rows(rows: list[list[str]]) -> list[str]:
    out = ["| " + " | ".join(rows[0]) + " |",
           "|" + "|".join(["---"] * len(rows[0])) + "|"]
    for r in rows[1:]:
        out.append("| " + " | ".join(str(x) for x in r) + " |")
    return out


def markdown(files: list[dict], ds: dict, feas: dict) -> str:
    L: list[str] = []
    A = L.append
    r = ds["replay"]

    A("## 表 A · 逐文件实数（脚本实测）\n")
    A("| 路径 | 字节 | 非空行 | sha256（前 16） |")
    A("|---|---:|---:|---|")
    for f in files:
        A(f"| `{f['path']}` | {f['bytes']:,} | {f['lines']:,} | `{f['sha256'][:16]}` |")
    A("")
    A(f"> 共 {len(files)} 个文件，字节合计 {sum(f['bytes'] for f in files):,}。")
    A("")

    A("## 表 B · 数据集级实数\n")
    A("| 数据集 | 帧数 | 关键实数 |")
    A("|---|---:|---|")
    A(f"| replay-set/v1.jsonl | {r['turns']} turn | {r['sessions']} 会话 × {r['turns']//max(1,r['sessions'])} turn；"
      f"turn_index 取值 {r['turn_index_values']}（每值 {list(r['turn_index_hist'].values())[0]} 条 → 非严格线性序列）；"
      f"source={r['source_labels']} |")
    A(f"| fixtures/v1.memories.jsonl | {r['memories']} 条记忆 | status={r['memory_status']}；layer={r['memory_layer']}；"
      f"type={r['memory_type']}；source_trust={r['memory_source_trust']} |")
    A(f"| fixtures/v1.corrections.jsonl | {r['corrections']} 条 | kind={r['corrections_kind']}；"
      f"对应 turn={[c['turn_id'] for c in read_jsonl(os.path.join(ROOT,'replay-set','fixtures','v1.corrections.jsonl'))[0]]} |")
    for k, v in ds["chaos"].items():
        extra = f"scenario={v['scenarios']}；expect_keys={v['expect_keys']}"
        if k == "long_context_flood":
            extra += f"；正文长度 {v['text_len_min']}–{v['text_len_max']} 字符；seed 轮={v['seed_rows']}；关键问题轮={v['key_question_row']}"
        if k == "memory_conflict":
            extra += f"；supersede 轮={v['supersede_rows']}；write 轮={v['write_rows']}"
        if k == "emotion_jailbreak":
            extra += f"；辱骂轮={v['insult_rows']}；越狱轮={v['jailbreak_rows']}"
        if k == "variety":
            extra += f"；不同错误事实={v['distinct_false_facts']}"
        if k == "jailbreak_variants":
            extra += (f"；家族={v['families']}；expect.blocked=true {v['expect_blocked_true']}/"
                      f"{v['lines']}；带 matched_patterns 行={v['has_plaintext_trigger']}")
        A(f"| chaos-set/{k}.jsonl | {v['lines']} 行 | {extra} |")
    A(f"| eval-set/agent_state-v1.jsonl | {ds['eval_set']['rows']} 行 | keys={ds['eval_set']['schema_keys']}；"
      f"goal={ds['eval_set']['goal']}；有真值字段={ds['eval_set']['has_ground_truth_field']} |")
    A(f"| acceptance/chaos/*.json | {ds['chaos_runs']['run_count']} 个 run | engine {ds['chaos_runs']['engine_run_count']} + "
      f"host {ds['chaos_runs']['host_run_count']}；host 记录的 model_calls 合计 "
      f"{ds['chaos_runs']['host_model_calls_total']}；.last.json → {ds['chaos_runs']['last_run_id']}（{ds['chaos_runs']['last_mode']}）|")
    for d, v in ds["host_dirs"].items():
        A(f"| {d}/ | {v['turns_observed']} turn（turn_begin） | audit {v['audit_events']} 事件（坏行 {v['audit_bad_lines']}）；"
          f"pre_step {v['pre_step']}；verdict={v['verdicts']}；UCA={v['pre_step_ucas']}；"
          f"preflight_block={v['preflight_block_events']}；memory 条数={v['memory_entries']}；"
          f"state.turns={v['state_turns']}；ratio 样本={v['ratios']} |")
    A("")
    led = ds["ledger"]
    A("## 表 C · 总账（synthetic_share）\n")
    A("| 项 | 值 | 口径 |")
    A("|---|---:|---|")
    A(f"| 合成帧 | {led['synthetic_frames']:,} | replay {led['breakdown']['replay_turns']} + chaos-set "
      f"{led['breakdown']['chaos_set_lines_total']} + eval-set {led['breakdown']['eval_set_rows']} |")
    A(f"| 真实宿主帧 | {led['real_host_turns']:,} | 6 个隔离数据目录的 `turn_begin` 事件计数 {led['breakdown']['host_turns_by_dir']} |")
    A(f"| 帧合计 | {led['total_frames']:,} | 合成 + 真实 |")
    A(f"| **synthetic_share** | **{led['synthetic_share']}** | 合成帧 / 帧合计 |")
    A(f"| 合成记忆条目 | {led['synthetic_memory_entries']:,} | replay fixtures |")
    A(f"| 真实宿主记忆条目 | {led['real_host_memory_entries']} | 各 .chaos-data*/memory.jsonl 行数 |")
    A(f"| 仓库内记录的 host 模型调用 | {led['host_model_calls_recorded']} | acceptance/chaos/*.json 的 model_calls 求和（不含 UCA-08 的 3 次独立调用）|")
    A("")

    A("## 表 D · 可行性静态计算（由实现计算，非消融结果）\n")
    A("| 项 | 值 |")
    A("|---|---|")
    A(f"| TURN_STEP | {feas['turn_step_seconds']} s/轮 |")
    A(f"| 100 轮总跨度 | {feas['turns_100_span_days']} 天 |")
    for idx, v in feas["settings_potency_by_turn"].items():
        A(f"| 第 {idx} 轮：设定项 potency 区间 | [{v['settings_potency_min']}, {v['settings_potency_max']}]，"
          f"低于归档阈值({v['archive_threshold']}) 的条数 = {v['below_archive']} |")
    A(f"| 衰减示例（A 族，第 99 轮） | {json.dumps(feas['settings_potency_by_turn'][99]['decay_example'], ensure_ascii=False)} |")
    A(f"| 越狱语料同义反复证据 | {feas['jailbreak_tautology']['lines_with_matched_patterns']}/"
      f"{feas['jailbreak_tautology']['corpus_lines']} 行自带 matched_patterns |")
    A(f"| 语料消费者 | jailbreak_variants 被 run_chaos 提及 = "
      f"{feas['jailbreak_tautology'] and feas['corpus_consumers']['jailbreak_variants_mentioned']}；"
      f"{feas['corpus_consumers']['SCENARIOS_line']} |")
    A("")
    A("### 仓库内既有的冻结断言（不是我跑出来的，是 tests 里写死的）\n")
    A("| 文件 | 行 | 断言 |")
    A("|---|---:|---|")
    for x in feas["frozen_test_assertions"]:
        A(f"| `{x['file']}` | {x['at_line']} | `{x['text']}` |")
    A("")
    A(f"_inventory.py sha256 = `{sha256_file(os.path.join(HERE, 'inventory.py'))}`_")
    return "\n".join(L) + "\n"


def time_scan() -> str:
    """设定项的 potency 随"绝对时间距离"变化（纯公式静态计算，不跑召回、不跑消融）。

    用途：证明"100 轮（60s/轮）"本身不产生遗忘，必须人为放大时间轴才可能出现信号。
    证据等级：由实现计算（`engine.memory.potency`），输入是合成 fixture。
    """
    if ROOT not in sys.path:
        sys.path.insert(0, ROOT)
    from engine import config as cfgmod
    from engine import memory
    from engine.model import Entry

    memo, _ = read_jsonl(os.path.join(ROOT, "replay-set", "fixtures", "v1.memories.jsonl"))
    cfg = cfgmod.default()
    a_ids = [f"mem_a{i:03d}_new" for i in (1, 2, 10, 20)]
    c_ids = [f"mem_c{i:03d}" for i in (1, 10, 20)]

    def pot(ids, dt_days, c):
        out = []
        for m in memo:
            if m["id"] in ids:
                e = Entry.from_json(m)
                now = float(m["last_access"]) + dt_days * 86400.0
                out.append(round(memory.potency(e, now, c), 5))
        return out

    lines = []
    lines.append(f"archiveThreshold = {cfg['potency']['archiveThreshold']} | "
                 f"actrWeight = {cfg['memory']['actrWeight']}")
    lines.append("| Δt（天） | A 族（mem_a*_new）potency | C 族（mem_c*）potency | 低于归档阈值 0.15 |")
    lines.append("|---:|---|---|---|")
    for dt in (1, 7, 30, 60, 80, 81, 85, 90, 100, 180, 365):
        pa, pc = pot(a_ids, dt, cfg), pot(c_ids, dt, cfg)
        below = sum(1 for v in pa + pc if v < cfg["potency"]["archiveThreshold"])
        lines.append(f"| {dt} | {pa} | {pc} | {below}/{len(pa) + len(pc)} 条 |")
    lines.append("")
    lines.append("同表、`actrWeight=0`（只剩纯衰减通道）对照组：")
    lines.append("")
    cfg0 = cfgmod.apply_path(cfg, "memory.actrWeight", 0.0)
    lines.append("| Δt（天） | A 族 | C 族 |")
    lines.append("|---:|---|---|")
    for dt in (30, 60, 100, 180):
        lines.append(f"| {dt} | {pot(a_ids, dt, cfg0)} | {pot(c_ids, dt, cfg0)} |")
    return "\n".join(lines)


def jsonable(o):
    """把 Counter 里可能出现的 None 键转成字符串，保证 JSON 可排序可序列化。"""
    if isinstance(o, dict):
        return {("null" if k is None else str(k)): jsonable(v) for k, v in o.items()}
    if isinstance(o, (list, tuple)):
        return [jsonable(x) for x in o]
    return o


def main(argv: list[str]) -> int:
    mode = argv[1] if len(argv) > 1 else "--markdown"
    files = collect_files()
    ds = dataset_facts()
    feas = feasibility()
    payload = jsonable({"files": files, "datasets": ds, "feasibility": feas})

    if mode == "--json":
        sys.stdout.reconfigure(encoding="utf-8")
        print(json.dumps(payload, ensure_ascii=False, indent=1, sort_keys=True))
        return 0

    if mode == "--write-facts":
        with open(FACTS_PATH, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1, sort_keys=True)
        print("wrote", FACTS_PATH)
        return 0

    if mode == "--verify":
        if not os.path.isfile(FACTS_PATH):
            print("MISSING", FACTS_PATH, "→ 先跑 --write-facts")
            return 2
        old = json.load(open(FACTS_PATH, encoding="utf-8"))
        new = payload
        diffs = []

        def walk(a, b, path=""):
            if type(a) is not type(b):
                diffs.append(f"{path}: 类型 {type(a).__name__} → {type(b).__name__}")
                return
            if isinstance(a, dict):
                for k in sorted(set(a) | set(b)):
                    if k not in a or k not in b:
                        diffs.append(f"{path}.{k}: 键增删")
                    else:
                        walk(a[k], b[k], f"{path}.{k}")
            elif isinstance(a, list):
                if len(a) != len(b):
                    diffs.append(f"{path}: 长度 {len(a)} → {len(b)}")
                else:
                    for i, (x, y) in enumerate(zip(a, b)):
                        walk(x, y, f"{path}[{i}]")
            elif a != b:
                diffs.append(f"{path}: {a!r} → {b!r}")

        walk(old, new)
        if diffs:
            print(f"FAIL：{len(diffs)} 处不一致")
            for d in diffs[:40]:
                print("  ", d)
            return 1
        print("OK：与 _facts.json 逐项一致（0 差异）")
        return 0

    if mode == "--write-md":
        with open(os.path.join(HERE, "_generated-tables.md"), "w", encoding="utf-8") as f:
            f.write(markdown(files, ds, feas))
        print("wrote", os.path.join(HERE, "_generated-tables.md"))
        return 0

    if mode == "--write-timescan":
        with open(os.path.join(HERE, "_timescan.md"), "w", encoding="utf-8") as f:
            f.write(time_scan() + "\n")
        print("wrote", os.path.join(HERE, "_timescan.md"))
        return 0

    sys.stdout.reconfigure(encoding="utf-8")
    print(markdown(files, ds, feas))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
