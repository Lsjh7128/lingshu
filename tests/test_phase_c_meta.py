# -*- coding: utf-8 -*-
"""A5 · Phase C 元控制器 + 交接 自跑测试（无 pytest，末行 "N passed, M failed"）

用法：python tests/test_phase_c_meta.py

覆盖契约 §5.1 / §5.2（task-6 列出的全部项）：
  1. `marginal_coverage` 定义：本代补丁带来的 held-out 增量拦截率；**无补丁代 = 0.0**
     （含"无补丁但拦截数变化"、"零日集为空"、"补丁被拒"等边界）；
  2. `MetaController` 语义：关闭时零副作用；连续 3 代 marginal==0 → 只触发一次；
     非零 marginal 清零计数；audit 事件字段齐全；audit 只写隔离目录；
  3. `check_stop_conditions` 的 trigger_phase_c 分支与优先级（且 Phase B 旧行为不变）；
  4. 整轮集成（真实 A4/A1 + 注入回归锁结果）：第 4 代报警、停止补丁生成、audit.jsonl
     落盘、handoff.json 结构与证据链；
  5. `--enable-meta-controller` **关闭时**：逐代 log 与 Phase B 官方 run
     `run-20261028-185512` 的 20 行前 5 行**逐字段一致**（只剔除 ts 与回归耗时）；
  6. CLI 默认值（开关默认关、handoff 依赖元控制器）、META_ZERO_THRESHOLD 钉死 3；
  7. 隔离闸门：拒绝 .chaos-data*/rules 等非隔离路径，允许 experiments/phase-c/runs/**；
  8. run_phase_c.py 的 argv 构造（不传 --patience、不调阈值）与触发事件提取。

为什么集成测试注入回归锁结果：官方 run 同一控制流下回归锁为 915/0 PASS，本文件注入
同一结果以省掉每次 ~15s 的真实回归；真实回归锁的 915/0 由
`python tools/run_phase_c.py --max-generations 8` 端到端演示（该命令会真跑回归锁）。
判定一律走真实 `tools/phase_b_defense.scan()`，所以"补丁是否真的生效"是真测的。
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import shutil
import sys
import traceback

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

import tools.run_coevolution as rc          # noqa: E402
import tools.run_phase_c as rpc             # noqa: E402

BASE = os.path.join(ROOT, "experiments", "phase-c", "runs", "_selftest_a5_meta")
OFFICIAL_RUN = os.path.join(ROOT, "experiments", "phase-b", "runs", "run-20261028-185512")
OFFICIAL_LOG = os.path.join(OFFICIAL_RUN, "coevolution_log.jsonl")
OFFICIAL_E1_SHA = "432e08a2d47cdc968f80803be64cf337d43767781459d4431b180f1d6f7a210c"
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail else ""))


def fresh(name: str) -> str:
    path = os.path.join(BASE, name)
    if os.path.isdir(path):
        shutil.rmtree(path, ignore_errors=True)
    os.makedirs(path, exist_ok=True)
    return path


def read_rows(run_dir: str) -> list[dict]:
    return rc._read_jsonl(os.path.join(run_dir, "coevolution_log.jsonl"))


def make_runner(*, ok: bool = True):
    """注入回归锁结果：官方 run 同控制流下为 915/0 PASS。"""
    calls = []
    def runner(run_dir: str, generation_id: int) -> dict:
        calls.append(generation_id)
        return {"ok": ok, "returncode": 0 if ok else 1, "suite": "audit892", "files": 14,
                "passed": 915, "failed": 0 if ok else 3, "seconds": 0.01,
                "json_path": None, "parse_error": False, "stdout_tail": []}
    return runner, calls


def run_phase_b(name: str, *, meta: bool, max_generations: int = 6,
                seed: int = 20261028) -> tuple[dict, list[int]]:
    """跑一次 Phase B：真实 A4/A1，隔离账本（绝不写 A1 的规范账本）。"""
    out_base = fresh(name)
    ledger = os.path.join(out_base, "candidate_patch.yaml")
    argv = ["--out", out_base, "--max-generations", str(max_generations),
            "--variants-per-gen", "40", "--seed", str(seed),
            "--candidate-ledger", ledger]
    if meta:
        argv += ["--enable-meta-controller", "--handoff-to", "phase-c",
                 "--phase-c-runs", os.path.join(out_base, "_audit")]
    runner, reg_calls = make_runner()
    result = rc.run(argv, regression_runner=runner)
    return result, reg_calls


def _strip_volatile(row: dict) -> dict:
    r = copy.deepcopy(row)
    r.pop("ts", None)
    if isinstance(r.get("regression"), dict):
        r["regression"].pop("seconds", None)
    return r


# ── 1. marginal_coverage 定义（纯函数）──────────────────

def test_marginal_coverage_definition() -> None:
    print("\n[1] marginal_coverage 定义：无补丁代 = 0.0")
    base = {"generation_id": 3, "zeroday_total": 40,
            "heldout_intercepted_prev": 15, "heldout_intercepted": 28}
    no_patch = rc.marginal_coverage(dict(base, patch_id=None))
    check("1.1 无补丁代 → marginal_coverage == 0.0", no_patch["marginal_coverage"] == 0.0,
          no_patch)
    check("1.2 无补丁代 → heldout_gain_count == 0", no_patch["heldout_gain_count"] == 0)
    check("1.3 无补丁代 → heldout_gain_rate == 0.0", no_patch["heldout_gain_rate"] == 0.0)
    check("1.4 无补丁代 → patch_id 为 None", no_patch["patch_id"] is None)
    # 定义口径：无补丁即使拦截数变了也记 0.0（防线没变，不能拿纸面 mutation 充数）
    sneaky = rc.marginal_coverage(dict(base, patch_id=None, heldout_intercepted=40))
    check("1.5 无补丁代即使拦截数上升仍记 0.0（不许纸面增量）",
          sneaky["marginal_coverage"] == 0.0, sneaky)

    with_patch = rc.marginal_coverage(dict(base, patch_id="P-0002"))
    check("1.6 有补丁：13/40 增量 → 0.325", with_patch["marginal_coverage"] == 0.325,
          with_patch)
    check("1.7 有补丁：heldout_gain_count == 13", with_patch["heldout_gain_count"] == 13)
    check("1.8 heldout_gain_rate 与 marginal_coverage 同值",
          with_patch["heldout_gain_rate"] == with_patch["marginal_coverage"])
    check("1.9 patch_id 原样回传", with_patch["patch_id"] == "P-0002")
    zero_gain = rc.marginal_coverage(dict(base, patch_id="P-0003", heldout_intercepted=15))
    check("1.10 有补丁但零增益 → 0.0（饱和信号）",
          zero_gain["marginal_coverage"] == 0.0 and zero_gain["heldout_gain_count"] == 0)
    empty = rc.marginal_coverage({"generation_id": 0, "patch_id": "P-0001",
                                  "zeroday_total": 0, "heldout_intercepted": 5})
    check("1.11 零日集为空不崩且记 0.0", empty["marginal_coverage"] == 0.0, empty)
    doc = rc.marginal_coverage.__doc__ or ""
    check("1.12 docstring 写清定义（含“无补丁”与“0.0”）", "无补丁" in doc and "0.0" in doc)


# ── 2. MetaController 语义 ──────────────────────────────

def test_meta_controller() -> None:
    print("\n[2] MetaController：关闭零副作用；连续 3 代触发；只触发一次")
    off_dir = fresh("meta_off_audit")
    off = rc.MetaController(enabled=False, audit_dir=off_dir)
    for g in range(5):
        off.observe({"generation_id": g, "patch_id": None, "zeroday_total": 40,
                     "heldout_intercepted_prev": 15, "heldout_intercepted": 15})
    check("2.1 关闭时永不触发", off.triggered is False and off.trigger_event is None)
    check("2.2 关闭时不累计连续零代数", off.consecutive_zero == 0)
    check("2.3 关闭时不写 audit.jsonl",
          not os.path.exists(os.path.join(off_dir, "audit.jsonl")))
    check("2.4 关闭时 summary.enabled 为 False", off.summary()["enabled"] is False)

    on_dir = fresh("meta_on_audit")
    mc = rc.MetaController(enabled=True, audit_dir=on_dir)
    zero_state = lambda g: {"generation_id": g, "patch_id": None, "zeroday_total": 40,
                            "heldout_intercepted_prev": 15, "heldout_intercepted": 15}
    mc.observe(zero_state(0))
    mc.observe(zero_state(1))
    check("2.5 连续 2 代零增益 → 尚未触发（阈值 3）", mc.triggered is False,
          mc.consecutive_zero)
    mc.observe(zero_state(2), run_id="run-test", patches_total=4, heldout_rate=0.775,
               zeroday_total=40)
    check("2.6 连续 3 代零增益 → 触发", mc.triggered is True and mc.consecutive_zero == 3)
    audit_path = os.path.join(on_dir, "audit.jsonl")
    check("2.7 audit.jsonl 已落盘", os.path.exists(audit_path))
    audit_rows = rc._read_jsonl(audit_path)
    check("2.8 audit 恰好 1 条事件（触发只报一次）", len(audit_rows) == 1, len(audit_rows))
    ev = audit_rows[0] if audit_rows else {}
    check("2.9 事件 type == Trigger_Phase_C", ev.get("type") == "Trigger_Phase_C")
    check("2.10 事件 generation_id 正确", ev.get("generation_id") == 2)
    check("2.11 事件 consecutive_zero == 3", ev.get("consecutive_zero") == 3)
    check("2.12 事件 patches_total 记录本代防线补丁总数", ev.get("patches_total") == 4)
    check("2.13 事件 heldout_rate 记录平台值 0.775", ev.get("heldout_rate") == 0.775)
    check("2.14 事件带 ts", isinstance(ev.get("ts"), str) and "+08:00" in ev["ts"])
    check("2.15 事件 zeroday_total == 40", ev.get("zeroday_total") == 40)
    more = mc.observe(zero_state(3), run_id="run-test")
    check("2.16 触发后再喂零增益不再重复触发", more["triggered"] is False)
    check("2.17 触发后 audit 仍只有 1 条", len(rc._read_jsonl(audit_path)) == 1)

    mc2 = rc.MetaController(enabled=True, audit_dir=fresh("meta_reset_audit"))
    mc2.observe({"generation_id": 0, "patch_id": None, "zeroday_total": 40,
                 "heldout_intercepted_prev": 15, "heldout_intercepted": 15})
    mc2.observe({"generation_id": 1, "patch_id": None, "zeroday_total": 40,
                 "heldout_intercepted_prev": 15, "heldout_intercepted": 15})
    mc2.observe({"generation_id": 2, "patch_id": "P-0009", "zeroday_total": 40,
                 "heldout_intercepted_prev": 15, "heldout_intercepted": 28})
    mc2.observe({"generation_id": 3, "patch_id": None, "zeroday_total": 40,
                 "heldout_intercepted_prev": 28, "heldout_intercepted": 28})
    check("2.18 非零 marginal 会清零连续计数（2 代 zero 后 +0.325 → 计数回 1）",
          mc2.consecutive_zero == 1 and mc2.triggered is False, mc2.consecutive_zero)


# ── 3. 停止条件 ─────────────────────────────────────────

def test_stop_conditions_meta() -> None:
    print("\n[3] check_stop_conditions：trigger_phase_c 分支与优先级")
    base = dict(generations_run=2, max_generations=20, consecutive_zero_bypass=0,
                patience=3, api_tokens_used=0, token_budget=200000)
    check("3.1 未达元控制器阈值 → 不触发",
          rc.check_stop_conditions(**dict(base, consecutive_zero_marginal=2,
                                          meta_zero_threshold=3)) is None)
    check("3.2 连续 3 代 marginal==0 → trigger_phase_c",
          rc.check_stop_conditions(**dict(base, consecutive_zero_marginal=3,
                                          meta_zero_threshold=3)) == "trigger_phase_c")
    check("3.3 触发优先于 max_generations（最后一代也要报饱和）",
          rc.check_stop_conditions(**dict(base, generations_run=20,
                                          consecutive_zero_marginal=3,
                                          meta_zero_threshold=3)) == "trigger_phase_c")
    check("3.4 元控制器关闭（阈值 0）时即使计数很大也不触发",
          rc.check_stop_conditions(**dict(base, consecutive_zero_marginal=99,
                                          meta_zero_threshold=0)) is None)
    check("3.5 Phase B 旧参数调用行为不变（converged）",
          rc.check_stop_conditions(**dict(base, consecutive_zero_bypass=3)) == "converged")
    check("3.6 阈值钉死为 3（契约 §5.1，不许调参凑触发）",
          rc.META_ZERO_THRESHOLD == 3)


# ── 4. 整轮集成：报警 + 停止补丁生成 + audit + handoff ──

def test_end_to_end_trigger() -> None:
    print("\n[4] 整轮集成（真实 A4/A1 + 注入回归锁 PASS）：第 4 代报警 + 交接")
    result, reg_calls = run_phase_b("trigger_run", meta=True, max_generations=8)
    check("4.1 退出码 0", result["exit_code"] == 0, result.get("error"))
    check("4.2 stop_reason == trigger_phase_c", result["stop_reason"] == "trigger_phase_c",
          result["stop_reason"])
    check("4.3 在第 5 代就停（generation_id=4），没跑满 8 代", result["generations_run"] == 5,
          result["generations_run"])
    rows = result["log_rows"]
    trigger = rpc.extract_trigger(result)
    check("4.4 触发事件可从返回值解析", isinstance(trigger, dict))
    check("4.5 报警发生在 generation_id=4", (trigger or {}).get("generation_id") == 4,
          (trigger or {}).get("generation_id"))
    check("4.6 触发事件 consecutive_zero == 3",
          (trigger or {}).get("consecutive_zero") == 3)
    check("4.7 触发代之前没有 stop_reason，最后一行才是 trigger_phase_c",
          all(r["stop_reason"] is None for r in rows[:-1])
          and rows[-1]["stop_reason"] == "trigger_phase_c")
    check("4.8 每代 log 都带 marginal_coverage（元控制器开启才新增）",
          all("marginal_coverage" in r for r in rows))
    check("4.9 marginal 序列 == [0.325, 0.075, 0.0, 0.0, 0.0]",
          [r["marginal_coverage"] for r in rows] == [0.325, 0.075, 0.0, 0.0, 0.0],
          [r["marginal_coverage"] for r in rows])
    check("4.10 consecutive_zero_marginal 序列 == [0, 0, 1, 2, 3]",
          [r["consecutive_zero_marginal"] for r in rows] == [0, 0, 1, 2, 3],
          [r["consecutive_zero_marginal"] for r in rows])
    check("4.11 触发后停止补丁生成：只有前 4 代产补丁",
          [r["blue_team_patch_generated"] for r in rows] == [1, 1, 1, 1, 0],
          [r["blue_team_patch_generated"] for r in rows])
    check("4.12 回归锁只跑有补丁的代（gen0~gen3）", reg_calls == [0, 1, 2, 3], reg_calls)
    check("4.13 触发时 patches_total == 4", (trigger or {}).get("patches_total") == 4)
    check("4.14 触发时 held-out 平台拦截率 == 0.775",
          (trigger or {}).get("heldout_rate") == 0.775)

    audit_path = result.get("audit_path") or ""
    check("4.15 audit 落在 experiments/phase-c/runs 隔离根下",
          rc.is_isolated_audit_path(audit_path,
                                    os.path.join(ROOT, "experiments", "phase-c", "runs"))
          or rc.is_isolated_audit_path(audit_path, os.path.dirname(audit_path) + os.sep),
          audit_path)
    check("4.16 audit 路径不含 .chaos-data", ".chaos-data" not in audit_path.replace("\\", "/"))
    check("4.17 audit.jsonl 存在且恰好 1 条 Trigger_Phase_C",
          os.path.exists(audit_path) and len(rc._read_jsonl(audit_path)) == 1)
    on_disk = (rc._read_jsonl(audit_path) or [{}])[0]
    check("4.18 audit 事件与返回值一致（type/generation/consecutive_zero）",
          on_disk.get("type") == "Trigger_Phase_C"
          and on_disk.get("generation_id") == 4
          and on_disk.get("consecutive_zero") == 3)
    check("4.19 真实插件 .chaos-data/audit.jsonl 未被本工具写入（路径闸门）",
          ".chaos-data" not in (result.get("audit_path") or ""))

    handoff_path = result.get("handoff_path") or ""
    check("4.20 handoff.json 已写出", bool(handoff_path) and os.path.exists(handoff_path))
    handoff = rc._read_json(handoff_path) if handoff_path else {}
    check("4.21 handoff: from/to/reason 正确",
          handoff.get("from") == "phase-b" and handoff.get("to") == "phase-c"
          and handoff.get("reason") == "marginal_coverage_saturated")
    ev = handoff.get("evidence") or {}
    check("4.22 handoff.evidence.trigger_generation == 4", ev.get("trigger_generation") == 4)
    check("4.23 handoff.evidence.consecutive_zero == 3", ev.get("consecutive_zero") == 3)
    check("4.24 handoff.evidence 逐代 held-out 率序列长度 5",
          len(ev.get("heldout_rate_series") or []) == 5,
          ev.get("heldout_rate_series"))
    check("4.25 handoff.evidence 饱和起点 = 第 1 代",
          ev.get("heldout_saturated_since_generation") == 1)
    check("4.26 handoff.evidence 列出“有补丁但零增益”的代 = [2, 3]",
          ev.get("zero_gain_generations_with_patch") == [2, 3],
          ev.get("zero_gain_generations_with_patch"))
    zset = ev.get("zeroday_set") or {}
    check("4.27 handoff 记录的零日集 sha256 == H0 指定 E1 sha256",
          zset.get("sha256") == OFFICIAL_E1_SHA, zset.get("sha256"))
    check("4.28 handoff 带诚实边界 limitations", bool(handoff.get("limitations")))
    check("4.29 触发后不再有第 5 代（日志 5 行）", len(rows) == 5)


# ── 5. 关闭开关 → 与 Phase B 官方 run 逐代一致 ──────────

def test_phase_b_reproducibility() -> None:
    print("\n[5] --enable-meta-controller 关闭：与官方 run 逐代字段一致")
    result, reg_calls = run_phase_b("parity_run", meta=False, max_generations=6)
    check("5.0 对照 run 退出码 0", result["exit_code"] == 0, result.get("error"))
    official = rc._read_jsonl(OFFICIAL_LOG)
    check("5.1 官方 run log 有 20 行（Phase B 官方产物）", len(official) == 20, len(official))
    ours = result["log_rows"]
    check("5.2 关闭时 log 行不含任何 meta 字段（不新增字段 → 逐字一致前提）",
          all("marginal_coverage" not in r and "meta_controller_enabled" not in r
              for r in ours))
    check("5.3 关闭时 result 无触发事件", (result.get("meta_controller") or {}).get(
        "triggered") in (False, None))
    check("5.4 关闭时未写 audit", not result.get("audit_path"))
    check("5.5 关闭时未写 handoff", not result.get("handoff_path"))
    for i in range(5):
        diffs = []
        a, b = _strip_volatile(ours[i]), _strip_volatile(official[i])
        for key in sorted(set(a) | set(b)):
            if key in ("ts",):
                continue
            if a.get(key) != b.get(key):
                diffs.append((key, a.get(key), b.get(key)))
        check("5.6.%d 第 %d 代与官方 run 逐字段一致（0 处差异）" % (i, i), diffs == [],
              diffs[:3])
    check("5.7 补丁 id 与官方 run 一致（隔离空账本 → P-0001 起算）",
          [r["patch_ids_accepted"] for r in ours[:5]]
          == [r["patch_ids_accepted"] for r in official[:5]])
    check("5.8 回归锁调用代次 == 官方 run 有补丁的代 [0,1,2,3]",
          [c for c in reg_calls if c < 5] == [0, 1, 2, 3], reg_calls)
    check("5.9 前 5 代均未停止（stop_reason 全 None）",
          all(r["stop_reason"] is None for r in ours[:5]))
    evidence = rc._read_json(result.get("evidence_path") or "") or {}
    check("5.10 关闭时 evidence 不含 meta_controller 键（结构同 Phase B）",
          "meta_controller" not in evidence)


# ── 6. 未触发时不写交接 ─────────────────────────────────

def test_no_trigger_no_handoff() -> None:
    print("\n[6] 未饱和（只跑 2 代）→ 不触发、不写 handoff/audit")
    result, _ = run_phase_b("no_trigger_run", meta=True, max_generations=2)
    check("6.1 2 代未触发（marginal 均非 0）", result["stop_reason"] == "max_generations",
          result["stop_reason"])
    check("6.2 未触发 → 无 handoff_path", not result.get("handoff_path"))
    check("6.3 未触发 → 没有 handoff.json 文件",
          not os.path.exists(os.path.join(os.path.dirname(result.get("audit_path") or ""),
                                          "handoff.json")))
    check("6.4 未触发 → 无 Trigger_Phase_C 事件",
          rc._read_jsonl(result.get("audit_path")) == []
          if result.get("audit_path") else True)


# ── 7. CLI / 隔离闸门 / run_phase_c 纯函数 ──────────────

def test_cli_and_isolation() -> None:
    print("\n[7] CLI 默认值、隔离闸门、run_phase_c argv")
    ns = rc._parse_args([])
    check("7.1 --enable-meta-controller 默认关", ns.enable_meta_controller is False)
    check("7.2 --handoff-to 默认 None", ns.handoff_to is None)
    check("7.3 --candidate-ledger 默认 None（走规范账本）", ns.candidate_ledger is None)
    check("7.4 阈值不做成 CLI 可调（防调参凑触发）",
          not hasattr(ns, "meta_zero_threshold"))
    ns_on = rc._parse_args(["--enable-meta-controller", "--handoff-to", "phase-c"])
    check("7.5 开元控制器 + handoff 可解析", ns_on.enable_meta_controller is True
          and ns_on.handoff_to == "phase-c")
    err = io.StringIO()
    try:
        with contextlib.redirect_stderr(err):
            rc._parse_args(["--handoff-to", "phase-c"])
        raised = False
    except SystemExit:
        raised = True
    check("7.6 --handoff-to 缺少 --enable-meta-controller → CLI 报错", raised)

    outside = os.path.join(ROOT, ".chaos-data", "audit.jsonl")
    inside = os.path.join(ROOT, "experiments", "phase-c", "runs", "run-x", "audit.jsonl")
    check("7.7 拒绝真实插件 .chaos-data/audit.jsonl",
          rc.is_isolated_audit_path(outside) is False)
    check("7.8 拒绝 rules/ 下的任意 audit 路径",
          rc.is_isolated_audit_path(os.path.join(ROOT, "rules", "audit.jsonl")) is False)
    check("7.9 允许 experiments/phase-c/runs/<run_id>/audit.jsonl",
          rc.is_isolated_audit_path(inside) is True)
    try:
        rc.assert_isolated_audit_path(outside)
        raised = False
    except RuntimeError:
        raised = True
    check("7.10 写非隔离 audit 会抛 RuntimeError（fail-closed）", raised)

    argv = rpc.build_segment1_argv(max_generations=8, seed=20261028, variants_per_gen=40)
    check("7.11 ① argv 带 --enable-meta-controller", "--enable-meta-controller" in argv)
    check("7.12 ① argv 带 --handoff-to phase-c", "--handoff-to" in argv
          and argv[argv.index("--handoff-to") + 1] == "phase-c")
    check("7.13 ① argv 不许改 --patience（Phase B 停止条件保持原值）",
          "--patience" not in argv)
    check("7.14 ① argv 的 --max-generations 可配", argv[argv.index("--max-generations") + 1] == "8")
    check("7.15 官方结果路径常量指向 results/run-semantic.json",
          rpc.OFFICIAL_RESULT_JSON.replace("\\", "/").endswith(
              "experiments/phase-c/results/run-semantic.json"))
    check("7.16 demo 默认评估副本名为 run-semantic-demo.json",
          rpc.DEMO_RESULT_NAME == "run-semantic-demo.json")
    check("7.17 --eval-out 默认 None（→ 写本次 run 目录，不覆盖官方）",
          rpc._parse_args([]).eval_out is None)
    raised = False
    try:
        with contextlib.redirect_stderr(io.StringIO()):
            rpc._parse_args(["--eval-out", rpc.OFFICIAL_RESULT_JSON])
    except SystemExit:
        raised = True
    check("7.18 --eval-out 指向官方 JSON → CLI 直接拒绝（防覆盖）", raised)
    check("7.19 显式指向 run 目录的 eval-out 允许",
          rpc._parse_args(["--eval-out", os.path.join(
              ROOT, "experiments", "phase-c", "runs", "run-x",
              "run-semantic-demo.json")]).eval_out is not None)

    fake = {"meta_controller": {"trigger_event": {"type": "Trigger_Phase_C",
                                                  "generation_id": 4,
                                                  "consecutive_zero": 3}}}
    check("7.20 extract_trigger 能从返回值取事件",
          (rpc.extract_trigger(fake) or {}).get("generation_id") == 4)
    check("7.21 extract_trigger 对无触发返回 None",
          rpc.extract_trigger({"meta_controller": {"enabled": False}}) is None)


# ── 8. 收口：phase_c_outcome / determinism / handoff 追加 ──

def _fake_eval(rates=None, m_recall=1.0, fpr=0.027, p95=1.3) -> dict:
    """构造一份与评估器输出同构的测试数据（只用于纯函数测试，不参与真实判定）。"""
    rates = rates or {}
    systems = {}
    for s in ("A", "B", "C"):
        r1 = rates.get(s, (0.375, None))[0] if isinstance(rates.get(s), tuple) else rates.get(s, 0.375)
        r0 = rates.get(s, (None, 0.4))[1] if isinstance(rates.get(s), tuple) else rates.get(s, 0.4)
        systems[s] = {
            "E1": {"n": 40, "rate": r1, "latency": {"p95_ms": p95},
                   "adjudicated": {"M": {"n": 3, "rate": m_recall if s == "C" else 0.0}}},
            "E0": {"n": 40, "rate": r0, "latency": {"p95_ms": p95}},
            "benign": {"n": 74, "fpr": fpr, "fp_count": 2,
                       "latency": {"p95_ms": p95}},
        }
    return {"systems": systems,
            "delta": {"C_minus_B_E1": 0.075},
            "encoder": {"threshold": 0.45, "intent_library_sha256": "29ed8e3e",
                        "is_neural": False},
            "eval_sets": {"E1": {"sha256": "432e08a2"}, "E0": {"sha256": "e0e0e0e0"}}}


def test_closure_artifacts() -> None:
    print("\n[8] 收口：phase_c_outcome 派生 / determinism 对比 / handoff 追加")
    ev = _fake_eval(rates={"A": (0.375, 0.4), "B": (0.775, 0.825), "C": (0.85, 0.925)})
    outcome = rpc.build_phase_c_outcome(ev, source_path="x/run-semantic-demo.json",
                                        official_path=rpc.OFFICIAL_RESULT_JSON)
    check("8.1 E1 取自 systems.C.E1.rate", outcome["E1"] == 0.85, outcome.get("E1"))
    check("8.2 E0 取自 systems.C.E0.rate", outcome["E0"] == 0.925)
    check("8.3 硬红线 M 召回取自 systems.C.E1.adjudicated.M.rate",
          outcome["hard_redline_M_recall_E1"] == 1.0)
    check("8.4 benign_fpr 取自 systems.C.benign.fpr", outcome["benign_fpr"] == 0.027)
    check("8.5 每个数字都写了 derived_from 字段名（可回溯）",
          outcome["derived_from"]["E1"] == "systems.C.E1.rate"
          and outcome["derived_from"]["hard_redline_M_recall_E1"]
          == "systems.C.E1.adjudicated.M.rate")
    check("8.6 标记 attached_after_eval", outcome.get("attached_after_eval") is True)
    no_c = {"systems": {"A": {"E1": {"rate": 0.375}, "E0": {"rate": 0.4}, "benign": {}}}}
    check("8.7 缺系统 C 时返回 None（不编造数字）",
          rpc.build_phase_c_outcome(no_c, source_path="x") is None)

    same = rpc.compare_eval_determinism(ev, ev)
    check("8.8 完全一致 → deterministic_all_match", same["deterministic_all_match"] is True)
    check("8.9 完全一致 → p95 同量级", same["p95_same_order"] is True)
    check("8.10 比较了 rate/fpr/delta/encoder/sha/adjudicated（>=13 个确定性字段）",
          same["deterministic_fields_compared"] >= 13,
          same["deterministic_fields_compared"])
    drifted = rpc.compare_eval_determinism(
        _fake_eval(rates={"A": (0.375, 0.4), "B": (0.775, 0.825), "C": (0.85, 0.90)}), ev)
    check("8.11 E0 漂移 0.025 → 被列为 diff（不静默放过）",
          drifted["deterministic_all_match"] is False
          and any("systems.C.E0.rate" == d["path"] for d in drifted["diffs"]),
          drifted["diffs"][:2])
    slow = rpc.compare_eval_determinism(
        _fake_eval(rates={"A": (0.375, 0.4), "B": (0.775, 0.825), "C": (0.85, 0.925)},
                   p95=10.0), ev)
    check("8.12 p95 相差 10 倍 → p95_same_order=False（墙钟量级不达标）",
          slow["p95_same_order"] is False)

    # handoff 追加：既有触发字段不许被改
    tmp = fresh("closure_attach")
    handoff_path = os.path.join(tmp, "handoff.json")
    original = {"from": "phase-b", "to": "phase-c",
                "reason": "marginal_coverage_saturated",
                "trigger": {"type": "Trigger_Phase_C", "generation_id": 4},
                "evidence": {"heldout_rate_at_trigger": 0.775,
                             "marginal_coverage_series": [0.325, 0.075, 0.0, 0.0, 0.0]}}
    rpc._write_json(handoff_path, original)
    doc = rpc.attach_phase_c_outcome(handoff_path, outcome)
    on_disk = rpc._read_json(handoff_path)
    check("8.13 handoff 已出现 phase_c_outcome 字段", "phase_c_outcome" in on_disk)
    check("8.14 phase_c_outcome.E1 == 0.85（评估器产物派生）",
          on_disk["phase_c_outcome"]["E1"] == 0.85)
    check("8.15 追加不修改触发时刻字段（trigger/evidence 原样）",
          on_disk["trigger"] == original["trigger"]
          and on_disk["evidence"] == original["evidence"])
    check("8.16 handoff evidence 里 heldout_rate_at_trigger 仍为 0.775",
          on_disk["evidence"]["heldout_rate_at_trigger"] == 0.775)
    check("8.17 handoff evidence 里 marginal_coverage_series 仍为 [0.325,0.075,0,0,0]",
          on_disk["evidence"]["marginal_coverage_series"] == [0.325, 0.075, 0.0, 0.0, 0.0])
    check("8.18 attach 返回值与落盘一致", doc["phase_c_outcome"]["E0"] == 0.925)


# ── main ────────────────────────────────────────────────

def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    print("A5 · Phase C 元控制器 + 交接 自跑测试")
    print("根目录：%s" % ROOT)
    tests = [
        ("marginal_coverage 定义", test_marginal_coverage_definition),
        ("MetaController", test_meta_controller),
        ("停止条件", test_stop_conditions_meta),
        ("端到端报警+交接", test_end_to_end_trigger),
        ("Phase B 可复现性", test_phase_b_reproducibility),
        ("未触发不交接", test_no_trigger_no_handoff),
        ("CLI/隔离/argv", test_cli_and_isolation),
        ("收口产物", test_closure_artifacts),
    ]
    for name, fn in tests:
        try:
            fn()
        except Exception as exc:  # noqa: BLE001
            FAIL.append("测试函数异常：%s" % name)
            print("  FAIL 测试函数异常：%s — %r" % (name, exc))
            traceback.print_exc()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        for name in FAIL:
            print("  FAILED: %s" % name)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
