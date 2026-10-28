# -*- coding: utf-8 -*-
"""Phase C · 一键三段式剧本（A5，契约 §5.2）

    python tools/run_phase_c.py --max-generations 8 [--skip-eval] [--seed 20261028]

三段（顺序固定，①②③缺一不可，除非显式 --skip-eval）：

  ① Phase B 报警：调 `tools/run_coevolution.py --enable-meta-controller --handoff-to phase-c`
     （**本文件不重实现演化逻辑**，只构造 argv 调用 A5 的剧本），直到元控制器观察到
     "连续 3 代 marginal_coverage == 0.0"→ stop_reason=trigger_phase_c；
     产物落隔离目录 `experiments/phase-c/runs/<run_id>/`：
        audit.jsonl       Trigger_Phase_C 事件（只写隔离目录，绝不写真实插件 .chaos-data*）
        handoff.json      交接包 {"from":"phase-b","to":"phase-c","reason":"marginal_coverage_saturated",...}
        coevolution_log.jsonl / coevolution_evidence.json / evolved_patch_snapshot.json
  ② 三系统对比：调 **Lead 的** `tools/phase_c_eval.py`，但**评估副本**写到本次
     run 目录 `runs/<run_id>/run-semantic-demo.json` —— **绝不覆盖** Lead 的官方
     `experiments/phase-c/results/run-semantic.json`（评估器与官方结果都归 Lead 独占；
     本文件不重写任何评估逻辑）。若 A1 的 `tools/semantic_guard.py` 尚未落盘，评估器会报
     系统 C 不可用 → 本脚本降级只评 A/B 并明确打印原因，绝不自己补一套语义判定。
  ②b determinism：把 demo 副本与官方 run-semantic.json 逐字段对比（rate/fpr/delta/encoder/
     eval_sets sha256 必须逐位一致；p95 是墙钟测量，只判 <=3 倍且 <100ms 的同量级），
     结果写 `runs/<run_id>/eval_determinism_check.json`。
  ②c 溯源：从评估器产物派生 `phase_c_outcome`（E1/E0/硬红线 M 召回/benign FPR）并**追加**
     进 `handoff.json`（标记 attached_after_eval=true，不改触发时刻字段），让
     “报警 → 接管 → 结果”在同一个交接产物里可追溯。
  ③ 结论表：打印 Phase B 逐代饱和过程（marginal_coverage）+ 三系统 × E1/E0 拦截率、
     误伤率、p95 延迟、预算（api_calls==0）、determinism 与 phase_c_outcome，并把流水线
     摘要写到 `runs/<run_id>/phase_c_pipeline.json`。

`--skip-eval`：评估器/语义防线未就绪时只跑第 ① 段（报警 + 交接），第 ②③ 段跳过。
这是契约允许的降级路径——**宁可少跑，不许伪造评估**。

诚实边界：
- 报警判据是 held-out（固定零日集）边际增益连续 3 代为 0，是"对自身饱和的测量"，
  不是因果证明；两种 zero 来源（补丁无效 / 无可打补丁）不可区分（见 handoff.json limitations）。
- 零日集是 held-out 变体，不是 OOD；"饱和"只能表述为"对该 held-out 分布饱和"。
- 本脚本零外部 API 调用（api_calls/api_tokens_used 恒 0），延迟是单机 numpy 实测。
"""
from __future__ import annotations

import argparse
import importlib
import io
import json
import os
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tools.run_coevolution as coev  # noqa: E402  （A5 自己的剧本：只调用，不复制逻辑）

PHASE_C_RUNS = os.path.join(ROOT, "experiments", "phase-c", "runs")
# Lead 的**官方**评估产物：A5 只读、绝不覆盖（评估器归 Lead 独占）。
OFFICIAL_RESULT_JSON = os.path.join(ROOT, "experiments", "phase-c", "results", "run-semantic.json")
# 本次 demo 的评估副本：写进本次 run 的隔离目录，作为闭环的 determinism 证据。
DEMO_RESULT_NAME = "run-semantic-demo.json"
DETERMINISM_NAME = "eval_determinism_check.json"
EVAL_TOOL = os.path.join(ROOT, "tools", "phase_c_eval.py")
# Phase C 演示用隔离账本：不推进 A1 维护的规范账本 rules/candidate_patch.yaml。
ISOLATED_LEDGER = os.path.join(PHASE_C_RUNS, "_ledger", "candidate_patch.yaml")
DEFAULT_MAX_GENERATIONS = 8
DEFAULT_SEED = 20261028
DEFAULT_VARIANTS_PER_GEN = 40
# p95 延迟是墙钟测量（不保证逐位复现）：判"量级一致"时允许 3 倍以内的抖动。
P95_SAME_ORDER_FACTOR = 3.0


# ── 小工具 ──────────────────────────────────────────────

def _rel(path: str) -> str:
    ap = os.path.abspath(path)
    try:
        rel = os.path.relpath(ap, ROOT)
    except ValueError:
        return ap
    return ap if rel.startswith("..") else rel.replace(os.sep, "/")


def _read_json(path: str):
    if not path or not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read_jsonl(path: str) -> list[dict]:
    if not path or not os.path.exists(path):
        return []
    rows = []
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _now_iso() -> str:
    from datetime import datetime, timedelta, timezone
    return datetime.now(timezone(timedelta(hours=8))).isoformat(timespec="seconds")


def _same_path(a: str, b: str) -> bool:
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


# ── 结论溯源：phase_c_outcome / determinism（纯函数，便于单测）──

def build_phase_c_outcome(eval_result: dict, *, source_path: str,
                          official_path: str | None = None) -> dict | None:
    """从**评估器自己的输出**派生 `phase_c_outcome`（不手填任何数字）。

    字段（Lead 指定）：
        E1 / E0                —— 系统 C 的拦截率
        hard_redline_M_recall_E1 —— E1 人工预注册判定里 M（必须拦）子集的 C 召回
        benign_fpr              —— 系统 C 在 74 条良性语料上的误伤率
    数据来源字段全部写进 `derived_from`，可逐项回溯到 run-semantic JSON。
    """
    if not isinstance(eval_result, dict):
        return None
    systems = eval_result.get("systems") or {}
    c = systems.get("C") or {}
    e1 = c.get("E1") or {}
    e0 = c.get("E0") or {}
    benign = c.get("benign") or {}
    if e1.get("rate") is None or e0.get("rate") is None:
        return None
    adjudicated = e1.get("adjudicated") or {}
    m_block = adjudicated.get("M") or {}
    return {
        "E1": e1.get("rate"),
        "E0": e0.get("rate"),
        "hard_redline_M_recall_E1": m_block.get("rate"),
        "benign_fpr": benign.get("fpr"),
        "system": "C",
        "derived_from": {
            "E1": "systems.C.E1.rate",
            "E0": "systems.C.E0.rate",
            "hard_redline_M_recall_E1": "systems.C.E1.adjudicated.M.rate",
            "benign_fpr": "systems.C.benign.fpr",
        },
        "source_eval": _rel(source_path),
        "official_result_json": _rel(official_path) if official_path else None,
        "attached_after_eval": True,
        "attached_at": _now_iso(),
        "note": "由 tools/run_phase_c.py 在第 ② 段评估完成后追加，用于把“报警→接管→结果”"
                "放在同一个交接产物里可追溯；不修改 handoff 的触发时刻字段（trigger/evidence）。",
    }


def compare_eval_determinism(demo_eval: dict, official_eval: dict,
                             *, p95_factor: float = P95_SAME_ORDER_FACTOR) -> dict:
    """比较本次 demo 评估与 Lead 官方评估：确定性字段必须逐位一致，墙钟 p95 只判量级。

    确定性字段：A/B/C × {E1,E0} 的 rate、三系统 benign fpr、delta、encoder、评估集 sha256。
    非确定性字段：latency p95（墙钟测量）；判二者同量级（相差 <= p95_factor 倍且都 < 100ms）。
    本函数只读两份 JSON 做比较，**不重实现评估逻辑**，也不修改任何一侧数据。
    """
    diffs: list[dict] = []
    identical = 0

    def _cmp(path: str, a, b, *, exact: bool = True) -> None:
        nonlocal identical
        if exact:
            if a == b:
                identical += 1
            else:
                diffs.append({"path": path, "demo": a, "official": b})
        else:
            diffs.append({"path": path, "demo": a, "official": b})

    for system in ("A", "B", "C"):
        sd = (demo_eval.get("systems") or {}).get(system) or {}
        so = (official_eval.get("systems") or {}).get(system) or {}
        for setname in ("E1", "E0"):
            _cmp("systems.%s.%s.rate" % (system, setname),
                 (sd.get(setname) or {}).get("rate"),
                 (so.get(setname) or {}).get("rate"))
        _cmp("systems.%s.benign.fpr" % system,
             (sd.get("benign") or {}).get("fpr"),
             (so.get("benign") or {}).get("fpr"))
    _cmp("delta", demo_eval.get("delta"), official_eval.get("delta"))
    _cmp("encoder.threshold",
         (demo_eval.get("encoder") or {}).get("threshold"),
         (official_eval.get("encoder") or {}).get("threshold"))
    _cmp("encoder.intent_library_sha256",
         (demo_eval.get("encoder") or {}).get("intent_library_sha256"),
         (official_eval.get("encoder") or {}).get("intent_library_sha256"))
    _cmp("encoder.is_neural",
         (demo_eval.get("encoder") or {}).get("is_neural"),
         (official_eval.get("encoder") or {}).get("is_neural"))
    for setname in ("E1", "E0"):
        _cmp("eval_sets.%s.sha256" % setname,
             ((demo_eval.get("eval_sets") or {}).get(setname) or {}).get("sha256"),
             ((official_eval.get("eval_sets") or {}).get(setname) or {}).get("sha256"))
    demo_m = (((demo_eval.get("systems") or {}).get("C") or {}).get("E1") or {})
    off_m = (((official_eval.get("systems") or {}).get("C") or {}).get("E1") or {})
    _cmp("systems.C.E1.adjudicated.M.rate",
         (demo_m.get("adjudicated") or {}).get("M", {}).get("rate"),
         (off_m.get("adjudicated") or {}).get("M", {}).get("rate"))

    # p95：墙钟量级比较（非逐位一致）
    p95_rows = []
    p95_same_order = True
    for system in ("A", "B", "C"):
        sd = (demo_eval.get("systems") or {}).get(system) or {}
        so = (official_eval.get("systems") or {}).get(system) or {}
        for setname in ("E1", "E0"):
            d = ((sd.get(setname) or {}).get("latency") or {}).get("p95_ms")
            o = ((so.get(setname) or {}).get("latency") or {}).get("p95_ms")
            row = {"system": system, "set": setname, "demo_p95_ms": d, "official_p95_ms": o}
            if isinstance(d, (int, float)) and isinstance(o, (int, float)) and o > 0:
                row["ratio"] = round(float(d) / float(o), 3)
                row["same_order"] = (float(d) / float(o) <= p95_factor
                                     and float(d) / float(o) >= 1.0 / p95_factor
                                     and d < 100.0)
            else:
                row["same_order"] = False
            p95_same_order = p95_same_order and bool(row["same_order"])
            p95_rows.append(row)

    return {
        "schema": "phase-c-eval-determinism-check-v1",
        "purpose": "闭环 demo 的 C 数字与 Lead 官方 run-semantic.json 对比（determinism 证据）",
        "deterministic_fields_compared": identical + len(diffs),
        "deterministic_fields_identical": identical,
        "deterministic_all_match": len(diffs) == 0,
        "diffs": diffs,
        "p95_same_order": p95_same_order,
        "p95_factor": p95_factor,
        "p95_detail": p95_rows,
        "note": "rate/fpr/delta/encoder/评估集 sha256 必须逐位一致；p95 是墙钟测量，只判同量级"
                "（<= %.1f 倍且 < 100ms）" % p95_factor,
    }


# ── 第 ① 段的 argv（纯函数，便于单测）──────────────────

def build_segment1_argv(*, max_generations: int = DEFAULT_MAX_GENERATIONS,
                        seed: int = DEFAULT_SEED,
                        variants_per_gen: int = DEFAULT_VARIANTS_PER_GEN,
                        runs_dir: str = PHASE_C_RUNS,
                        ledger_path: str | None = None) -> list[str]:
    """构造 ① 的 argv：带元控制器跑 Phase B，产物落 Phase C 隔离目录。

    刻意**不传** `--patience`、不改 `META_ZERO_THRESHOLD`：Phase B 的停止条件与
    触发判据都保持契约原值，不许为了"更快触发"调参。
    """
    return [
        "--out", runs_dir,
        "--max-generations", str(int(max_generations)),
        "--variants-per-gen", str(int(variants_per_gen)),
        "--seed", str(int(seed)),
        "--enable-meta-controller",
        "--handoff-to", "phase-c",
        "--candidate-ledger", ledger_path or ISOLATED_LEDGER,
    ]


# ── 第 ① 段：Phase B 报警 ───────────────────────────────

def extract_trigger(result: dict) -> dict | None:
    """从 run() 返回值（或隔离 audit.jsonl）里取出 Trigger_Phase_C 事件原文。"""
    if not isinstance(result, dict):
        return None
    meta = result.get("meta_controller") or {}
    event = meta.get("trigger_event")
    if isinstance(event, dict) and event.get("type") == "Trigger_Phase_C":
        return event
    # 双保险：从隔离 audit.jsonl 回读（验证"事件确实落盘"而不只是内存里有）
    for row in _read_jsonl(result.get("audit_path")):  # type: ignore[arg-type]
        if row.get("type") == "Trigger_Phase_C":
            return row
    return None


def run_segment1(args) -> dict:
    print("=" * 78)
    print("【第 1 段】Phase B + 元控制器：跑到一阶词法自愈饱和报警")
    argv = build_segment1_argv(max_generations=args.max_generations, seed=args.seed,
                               variants_per_gen=args.variants_per_gen)
    print("[run_phase_c] 调用 tools/run_coevolution.py 的 run()：%s"
          % " ".join(argv))
    result = coev.run(argv)
    ok = int(result.get("exit_code", 1)) == 0
    trigger = extract_trigger(result)
    if not ok:
        print("[FAIL] 第 1 段失败：%s" % result.get("error"))
        return {"ok": False, "result": result, "trigger": None}
    if trigger is None:
        print("[WARN] 第 1 段未触发 Phase C（stop_reason=%s）。一阶自愈尚未饱和："
              "把 --max-generations 调大再跑（不要改判据阈值）" % result.get("stop_reason"))
        return {"ok": False, "result": result, "trigger": None}
    print("[OK] 第 1 段：Phase B 在第 %d 代报警（stop_reason=%s，连续 %d 代 marginal=0）"
          % (trigger.get("generation_id"), result.get("stop_reason"),
             trigger.get("consecutive_zero")))
    if result.get("handoff_path"):
        print("[OK] 交接包：%s" % _rel(result["handoff_path"]))
    else:
        print("[WARN] 未写出 handoff.json（检查 --handoff-to phase-c 是否生效）")
    return {"ok": True, "result": result, "trigger": trigger}


# ── 第 ② 段：调 Lead 的评估器 ───────────────────────────

def _load_eval_module():
    if not os.path.exists(EVAL_TOOL):
        print("[SKIP] Lead 的评估器不存在：%s（用 --skip-eval 只跑第 1 段）" % _rel(EVAL_TOOL))
        return None
    try:
        return importlib.import_module("tools.phase_c_eval")
    except Exception as exc:  # noqa: BLE001
        print("[SKIP] Lead 的评估器导入失败：%r（用 --skip-eval 只跑第 1 段）" % (exc,))
        return None


def run_segment2(args, run_dir: str = "", eval_out: str | None = None) -> dict:
    print("=" * 78)
    print("【第 2 段】三系统对比：调用 Lead 的 tools/phase_c_eval.py（唯一评估器）")
    if args.skip_eval:
        print("[SKIP] --skip-eval：按契约允许的降级路径跳过第 ②③ 段的评估与对比，"
              "只保留第 ① 段的报警 + 交接。")
        return {"ok": True, "skipped": True, "eval_result": None}
    mod = _load_eval_module()
    if mod is None:
        return {"ok": True, "skipped": True, "eval_result": None}
    # demo 的评估副本默认写本次 run 目录，**绝不覆盖** Lead 的官方 run-semantic.json。
    eval_out = eval_out or os.path.join(run_dir or PHASE_C_RUNS, DEMO_RESULT_NAME)
    if _same_path(eval_out, OFFICIAL_RESULT_JSON):
        print("[FAIL] 拒绝覆盖 Lead 的官方评估产物：%s（demo 副本必须写本次 run 目录）"
              % _rel(OFFICIAL_RESULT_JSON))
        return {"ok": False, "skipped": False, "eval_result": None, "returncode": 2,
                "out_path": eval_out}
    systems_arg = args.systems
    attempts = [systems_arg]
    if "C" in systems_arg:
        # 系统 C 依赖 A1 的 tools/semantic_guard.py；未就绪时降级 A/B（不自己实现语义判定）
        attempts.append(",".join(s for s in systems_arg.split(",") if s.strip().upper() != "C"))
    last_rc = None
    for i, sysnames in enumerate(attempts):
        # 降级（无 C）时产物另起文件名，同样写在本次 run 目录
        is_fallback = i > 0
        out_path = eval_out
        if is_fallback:
            out_path = os.path.join(run_dir or PHASE_C_RUNS, "run-semantic-partial-AB.json")
        print("[run_phase_c] 评估器 argv：--systems %s --out %s"
              % (sysnames, _rel(out_path)))
        last_rc = mod.main(["phase_c_eval.py", "--systems", sysnames,
                            "--out", out_path])
        if last_rc == 0:
            eval_result = _read_json(out_path)
            print("[OK] 第 2 段：评估完成 → %s%s"
                  % (_rel(out_path),
                     "（降级：系统 C 未就绪，不覆盖 Lead 的正式 run-semantic.json）"
                     if is_fallback else ""))
            return {"ok": True, "skipped": False, "eval_result": eval_result,
                    "sysnames": sysnames, "returncode": 0, "out_path": out_path}
        if i == 0 and len(attempts) > 1:
            print("[WARN] 三系统评估失败（多半是 A1 的 tools/semantic_guard.py 尚未落盘）→ "
                  "降级只评 A/B；本脚本不重写任何评估逻辑")
    print("[FAIL] 第 2 段失败：tools/phase_c_eval.py 退出码 %s" % last_rc)
    return {"ok": False, "skipped": False, "eval_result": None, "returncode": last_rc}


# ── 第 ③ 段：结论表 ─────────────────────────────────────

def _fmt(x) -> str:
    if x is None:
        return "—"
    if isinstance(x, float):
        return "%.4f" % x
    return str(x)


def print_conclusion(seg1: dict, seg2: dict, *,
                     determinism: dict | None = None,
                     phase_c_outcome: dict | None = None) -> None:
    print("=" * 78)
    print("【第 3 段】结论表")
    result = seg1.get("result") or {}
    trigger = seg1.get("trigger") or {}
    meta = result.get("meta_controller") or {}
    history = meta.get("history") or []
    rows = result.get("log_rows") or []

    print("\n-- ① Phase B 一阶词法自愈饱和过程（held-out 零日集，n=%s）--"
          % (rows[0].get("zeroday_total") if rows else "?"))
    print("%-4s %-10s %-13s %-10s %s"
          % ("gen", "patch_gen", "heldout_rate", "marginal", "consecutive_zero"))
    for row, obs in zip(rows, history):
        print("%-4s %-10s %-13s %-10s %s"
              % (row.get("generation_id"), row.get("blue_team_patch_generated"),
                 _fmt(row.get("intercept_rate_evolved_zeroday")),
                 _fmt(obs.get("marginal_coverage")),
                 _fmt(obs.get("consecutive_zero"))))
    print("报警：generation_id=%s，连续 %s 代 marginal_coverage==0.0，held-out 平台=%.4f，"
          "patches_total=%s"
          % (trigger.get("generation_id"), trigger.get("consecutive_zero"),
             float(trigger.get("heldout_rate") or 0.0), trigger.get("patches_total")))
    print("stop_reason=%s；audit → %s；handoff → %s"
          % (result.get("stop_reason"), _rel(result.get("audit_path") or "") or "—",
             _rel(result.get("handoff_path") or "") or "—"))

    if seg2.get("skipped"):
        print("\n-- ②③ 三系统对比：已跳过（--skip-eval 或评估器/语义防线未就绪）--")
        print("   恢复方式：等 A1 的 tools/semantic_guard.py 落盘后去掉 --skip-eval 重跑。")
        return

    ev = seg2.get("eval_result") or {}
    systems = ev.get("systems") or {}
    print("\n-- ② 三系统 × 评估集（数据来自本次 demo 副本 %s，由 Lead 评估器写出）--"
          % os.path.basename(seg2.get("out_path") or DEMO_RESULT_NAME))
    print("%-4s %-7s %-5s %-12s %-9s %-8s %s"
          % ("SYS", "SET", "n", "intercepted", "rate", "p95_ms", "note"))
    for s in ("A", "B", "C"):
        if s not in systems:
            continue
        for e in ("E1", "E0", "benign"):
            blk = systems[s].get(e) or {}
            if e == "benign":
                if blk.get("fpr") is None:
                    continue
                print("%-4s %-7s %-5s %-12s %-9s %-8s %s"
                      % (s, e, blk.get("n"), blk.get("fp_count"),
                         "fpr=" + _fmt(blk.get("fpr")),
                         _fmt((blk.get("latency") or {}).get("p95_ms")), "误伤"))
            else:
                if blk.get("rate") is None:
                    print("%-4s %-7s %-5s %-12s %-9s %-8s %s"
                          % (s, e, blk.get("n"), "—", "缺失", "—", blk.get("note", "")))
                    continue
                print("%-4s %-7s %-5s %-12s %-9s %-8s %s"
                      % (s, e, blk.get("n"), blk.get("intercepted"),
                         _fmt(blk.get("rate")),
                         _fmt((blk.get("latency") or {}).get("p95_ms")), ""))
    print("\n差值（delta）：%s" % json.dumps(ev.get("delta") or {}, ensure_ascii=False))
    print("延迟红线：%s" % json.dumps(ev.get("latency_budget") or {}, ensure_ascii=False))
    print("预算：%s（零外部 API 调用是硬事实）"
          % json.dumps(ev.get("budget") or {}, ensure_ascii=False))
    encoder = ev.get("encoder") or {}
    if encoder:
        print("编码器：backend=%s is_neural=%s threshold=%s calibrated_on=%s"
              % (encoder.get("backend"), encoder.get("is_neural"),
                 encoder.get("threshold"), encoder.get("threshold_calibrated_on")))
    misses = ev.get("residual_misses_C") or {}
    if misses:
        print("系统 C 残余漏拦：E1=%d 条，E0=%d 条（详见 demo 副本 residual_misses_C）"
              % (len(misses.get("E1") or []), len(misses.get("E0") or [])))

    if determinism is not None:
        print("\n-- ②b determinism：闭环 demo 评估 vs Lead 官方 run-semantic.json --")
        print("确定性字段：%d/%d 一致；diffs=%d；p95 同量级=%s（<=%.1f 倍且 <100ms）"
              % (determinism.get("deterministic_fields_identical", 0),
                 determinism.get("deterministic_fields_compared", 0),
                 len(determinism.get("diffs") or []),
                 determinism.get("p95_same_order"), determinism.get("p95_factor", 3.0)))
        for diff in (determinism.get("diffs") or [])[:8]:
            print("  DIFF %-32s demo=%r official=%r"
                  % (diff.get("path"), diff.get("demo"), diff.get("official")))
        for row in determinism.get("p95_detail") or []:
            print("  p95 %-3s %-2s demo=%sms official=%sms ratio=%s"
                  % (row.get("system"), row.get("set"), row.get("demo_p95_ms"),
                     row.get("official_p95_ms"), row.get("ratio")))

    if phase_c_outcome:
        print("\n-- ②c phase_c_outcome（已追加进 handoff.json，可回溯“报警→接管→结果”）--")
        print(json.dumps({k: phase_c_outcome.get(k) for k in
                          ("E1", "E0", "hard_redline_M_recall_E1", "benign_fpr")},
                         ensure_ascii=False))


# ── 交接产物补“结果”段（追加，不改触发时刻字段）────────

def attach_phase_c_outcome(handoff_path: str, outcome: dict) -> dict:
    """把 `phase_c_outcome` 追加进已有 handoff.json（第 ② 段评估完成后执行）。

    诚实性约束：
    - handoff 由第 ① 段在**触发时刻**写定；这里只做**追加**，不改 `trigger`/`evidence`/
      `phase_b_run` 任何既有字段，并显式标记 `attached_after_eval: true`；
    - outcome 的数字全部由评估器产物派生（build_phase_c_outcome），不手填；
    - 若 handoff 不存在则抛错（不静默新建一份假交接）。
    """
    if not os.path.exists(handoff_path):
        raise RuntimeError("handoff.json 不存在，无法追加 phase_c_outcome：%s" % handoff_path)
    doc = _read_json(handoff_path) or {}
    doc["phase_c_outcome"] = dict(outcome, attached_after_eval=True, attached_at=_now_iso())
    _write_json(handoff_path, doc)
    return doc


# ── 流水线摘要（落 Phase C 隔离目录）────────────────────

def build_pipeline_summary(*, seg1: dict, seg2: dict,
                           determinism: dict | None = None,
                           phase_c_outcome: dict | None = None) -> dict:
    result = seg1.get("result") or {}
    trigger = seg1.get("trigger") or {}
    ev = seg2.get("eval_result") or {}
    return {
        "schema": "phase-c-pipeline-v1",
        "generated_at": _now_iso(),
        "segment1_phase_b": {
            "ok": bool(seg1.get("ok")),
            "run_id": result.get("run_id"),
            "run_dir": _rel(result.get("run_dir") or ""),
            "stop_reason": result.get("stop_reason"),
            "generations_run": result.get("generations_run"),
            "patches": result.get("patch_ids_accepted"),
            "audit_path": _rel(result.get("audit_path") or "") or None,
            "handoff_path": _rel(result.get("handoff_path") or "") or None,
            "trigger": trigger or None,
        },
        "segment2_eval": {
            "ok": bool(seg2.get("ok")),
            "skipped": bool(seg2.get("skipped")),
            "systems": seg2.get("sysnames"),
            "demo_result_json": (_rel(seg2.get("out_path") or "")
                                 if not seg2.get("skipped") else None),
            "official_result_json": _rel(OFFICIAL_RESULT_JSON),
            "official_overwritten": False,
            "returncode": seg2.get("returncode"),
        },
        "segment3_conclusion": {
            "trigger_generation": trigger.get("generation_id"),
            "consecutive_zero": trigger.get("consecutive_zero"),
            "heldout_rate_at_trigger": trigger.get("heldout_rate"),
            "delta": ev.get("delta"),
            "latency_budget": ev.get("latency_budget"),
            "budget": ev.get("budget"),
            "phase_c_outcome": phase_c_outcome,
        },
        "determinism_check": determinism,
        "budget": {"api_calls": 0, "api_tokens_used": 0},
        "limitations": [
            "报警判据是 held-out 边际增益连续 3 代为 0，是“对自身饱和的测量”而非因果证明；"
            "“补丁无效”与“无可打补丁”在 marginal_coverage 上不可区分，见 handoff.json。",
            "零日集是 held-out 变体而非 OOD；饱和只能表述为“对该 held-out 分布饱和”。",
            "三系统数字全部来自 Lead 的 tools/phase_c_eval.py，本脚本不重写评估逻辑；"
            "demo 副本写本次 run 目录，官方 run-semantic.json 不被覆盖。",
            "phase_c_outcome 是评估完成后追加进 handoff.json 的字段（attached_after_eval=true），"
            "不改触发时刻的 trigger/evidence；p95 是墙钟量级，不保证逐位复现。",
        ],
    }


def _write_json(path: str, payload: dict) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)


# ── 主流程 ──────────────────────────────────────────────

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Phase C 一键三段式：Phase B 报警 → Lead 评估器三系统对比 → 结论表")
    ap.add_argument("--max-generations", type=int, default=DEFAULT_MAX_GENERATIONS,
                    help="① 的 Phase B 代数上限（默认 %d；实测第 4~6 代报警，别跑满 20 代）"
                         % DEFAULT_MAX_GENERATIONS)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--variants-per-gen", type=int, default=DEFAULT_VARIANTS_PER_GEN)
    ap.add_argument("--skip-eval", action="store_true",
                    help="只跑第 ① 段（报警 + 交接）；评估器/语义防线未就绪时用")
    ap.add_argument("--systems", default="A,B,C",
                    help="② 传给 Lead 评估器的系统集合（默认 A,B,C；C 未就绪会自动降级 A,B）")
    ap.add_argument("--eval-out", default=None,
                    help="② 的评估副本路径（默认写本次 run 目录的 %s；**绝不覆盖** Lead 的"
                         "官方 %s）" % (DEMO_RESULT_NAME, _rel(OFFICIAL_RESULT_JSON)))
    ns = ap.parse_args(argv)
    if ns.max_generations < 1:
        ap.error("--max-generations 必须 >= 1")
    if ns.variants_per_gen < 1:
        ap.error("--variants-per-gen 必须 >= 1")
    if ns.eval_out and _same_path(ns.eval_out, OFFICIAL_RESULT_JSON):
        ap.error("--eval-out 指向 Lead 的官方评估产物（%s）：demo 不允许覆盖它。"
                 "官方结果请由 Lead 直接运行 tools/phase_c_eval.py 生成。"
                 % _rel(OFFICIAL_RESULT_JSON))
    systems = [s.strip().upper() for s in ns.systems.split(",") if s.strip()]
    bad = [s for s in systems if s not in ("A", "B", "C")]
    if bad:
        ap.error("--systems 只接受 A/B/C，收到：%s" % bad)
    ns.systems = ",".join(systems)
    return ns


def run(argv: list[str] | None = None) -> dict:
    """执行三段式，返回汇总 dict（CLI 只看 exit_code）。"""
    args = _parse_args(argv)
    print("[run_phase_c] Phase C 三段式：max_generations=%d seed=%d variants/gen=%d systems=%s"
          % (args.max_generations, args.seed, args.variants_per_gen, args.systems))

    # ① 报警 + 交接
    seg1 = run_segment1(args)
    summary_path = None
    if seg1.get("result") and seg1["result"].get("run_dir"):
        summary_path = os.path.join(seg1["result"]["run_dir"], "phase_c_pipeline.json")
    if not seg1["ok"]:
        # 未触发也落一份摘要（run dir 已建），便于复查
        if summary_path:
            _write_json(summary_path, build_pipeline_summary(seg1=seg1, seg2={
                "ok": False, "skipped": True, "eval_result": None}))
        return {"exit_code": 3 if seg1.get("trigger") is None else 2,
                "segment1": seg1, "segment2": None, "summary_path": summary_path}

    # ② 三系统对比（调 Lead 的评估器；副本写本次 run 目录，绝不覆盖官方文件）
    run_dir = seg1["result"].get("run_dir") or ""
    eval_out = args.eval_out or os.path.join(run_dir or PHASE_C_RUNS, DEMO_RESULT_NAME)
    seg2 = run_segment2(args, run_dir=run_dir, eval_out=eval_out)

    # ②b determinism：本次 demo 副本 vs Lead 官方 run-semantic.json
    determinism = None
    phase_c_outcome = None
    handoff_path = seg1["result"].get("handoff_path") or ""
    if seg2.get("ok") and not seg2.get("skipped"):
        demo_eval = seg2.get("eval_result") or {}
        official_eval = _read_json(OFFICIAL_RESULT_JSON)
        if official_eval:
            determinism = compare_eval_determinism(demo_eval, official_eval)
            det_path = os.path.join(run_dir, DETERMINISM_NAME)
            _write_json(det_path, determinism)
            print("[run_phase_c] determinism 检查 → %s（确定性字段 %d/%d 一致，p95 同量级=%s）"
                  % (_rel(det_path), determinism["deterministic_fields_identical"],
                     determinism["deterministic_fields_compared"],
                     determinism["p95_same_order"]))
        else:
            print("[WARN] 官方评估产物不存在：%s → 跳过 determinism 对比"
                  % _rel(OFFICIAL_RESULT_JSON))
        # ②c 把 C 的结果追加进 handoff（同一产物可追溯“报警→接管→结果”）
        phase_c_outcome = build_phase_c_outcome(
            demo_eval, source_path=seg2.get("out_path") or eval_out,
            official_path=OFFICIAL_RESULT_JSON if official_eval else None)
        if phase_c_outcome and handoff_path and os.path.exists(handoff_path):
            attach_phase_c_outcome(handoff_path, phase_c_outcome)
            print("[run_phase_c] phase_c_outcome 已追加进 handoff.json（attached_after_eval=true）")

    # ③ 结论表
    print_conclusion(seg1, seg2, determinism=determinism, phase_c_outcome=phase_c_outcome)
    summary = build_pipeline_summary(seg1=seg1, seg2=seg2, determinism=determinism,
                                     phase_c_outcome=phase_c_outcome)
    if summary_path:
        _write_json(summary_path, summary)
        print("\n[run_phase_c] 流水线摘要 → %s" % _rel(summary_path))

    exit_code = 0
    if not seg2.get("ok"):
        exit_code = 4
    if seg1["trigger"] is None:
        exit_code = 3
    print("\n[run_phase_c] 完成：报警代数=%s，交接=%s，②=%s，demo 副本=%s，官方 JSON 未被覆盖，exit=%d"
          % ((seg1.get("trigger") or {}).get("generation_id"),
             _rel(seg1["result"].get("handoff_path") or "") or "无",
             "skipped" if seg2.get("skipped") else ("ok" if seg2.get("ok") else "failed"),
             _rel(seg2.get("out_path") or "") or "无",
             exit_code))
    return {"exit_code": exit_code, "segment1": seg1, "segment2": seg2,
            "summary_path": summary_path, "trigger": seg1["trigger"],
            "determinism": determinism, "phase_c_outcome": phase_c_outcome,
            "handoff_path": handoff_path}


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        result = run(argv)
    except Exception as exc:  # noqa: BLE001
        print("[FAIL] run_phase_c 未捕获异常：%s" % exc)
        import traceback
        traceback.print_exc()
        return 1
    return int(result.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
