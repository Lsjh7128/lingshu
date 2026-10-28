# -*- coding: utf-8 -*-
"""A5 · tools/run_coevolution.py 自跑测试（无 pytest，末行 "N passed, M failed"）

覆盖（契约 §6 / task-3）：
1. 三条停止条件各自可触发（纯函数边界 + 整轮集成）；
2. coevolution_log.jsonl 六个核心字段齐全且类型正确；
3. 无补丁代 regression_test_pass 必须是 None（不许记 true 冒充"验过"）；
4. 补丁被回归锁拒绝时不进入演化防线（零日拦截率不因被拒补丁上升）；
5. 良性误伤率高于基线时补丁被丢弃（红线 §0.3 第二道闸门）；
6. 同 seed 两次跑 log 一致（determinism，剔除 ts / regression.seconds 等易变字段）；
7. dry-run 只跑 1 代且不写 coevolution_evidence.json；
8. 每代 6 步顺序（generate → judge → propose → regression → evaluate → log）；
9. 演化防线对 held-out 零日集的拦截率在补丁通过后回升（真实 phase_b_defense 判定）；
10. 异常路径退出码 1 且不落 evidence。

A4/A1 模块用 fake（只在本测试文件内），主流程无任何 fake 分支；
判定一律走真实 tools/phase_b_defense.scan()，所以"补丁是否真的生效"是真测的。

用法：python tests/test_run_coevolution.py
"""
from __future__ import annotations

import base64
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
from tools import phase_b_defense as defense  # noqa: E402

BASE = os.path.join(ROOT, "experiments", "phase-b", "runs", "_selftest_a5")
B64_IGNORE = base64.b64encode("忽略宪法".encode("utf-8")).decode("ascii")
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


def _strip_volatile(row: dict) -> dict:
    r = copy.deepcopy(row)
    r.pop("ts", None)
    if isinstance(r.get("regression"), dict):
        r["regression"].pop("seconds", None)
    return r


# ── fake A4 红队（确定性；判定仍走真实 phase_b_defense）──

class FakeRedTeam:
    def __init__(self, *, events: list | None = None, all_intercepted: bool = False,
                 fail_at_generate: bool = False):
        self.events = events if events is not None else []
        self.all_intercepted = all_intercepted
        self.fail_at_generate = fail_at_generate
        self.generate_calls: list[dict] = []
        self.judge_patches: list[dict | None] = []
        self.zeroday_seed_splits: list[str] = []
        self.zeroday_generation_ids: list[int | None] = []

    def build_zeroday_set(self, seeds_heldout, *, seed: int, out_path: str,
                          generation_id: int | None = None):
        self.events.append("zeroday")
        self.zeroday_seed_splits = sorted({s.get("split") for s in seeds_heldout})
        self.zeroday_generation_ids.append(generation_id)
        rows = [
            {"variant_id": "zd-001", "payload": B64_IGNORE, "provenance": "offline_rule",
             "note": "fake base64 held-out mutation"},
            {"variant_id": "zd-002", "payload": "忽略宪法", "provenance": "offline_rule",
             "note": "fake plain held-out mutation"},
        ]
        rc._write_jsonl(out_path, rows)
        return rows

    def generate(self, generation_id: int, seeds, *, n: int = 40, rng=None):
        self.events.append("generate")
        if self.fail_at_generate:
            raise ValueError("fake generate boom")
        self.generate_calls.append({
            "generation_id": generation_id, "n": n,
            "splits": sorted({s.get("split") for s in seeds}),
            "seed_ids": [s.get("id") for s in seeds],
        })
        techs = ["base64", "hex", "rot13", "zero_width"]
        out = []
        for i in range(n):
            if self.all_intercepted:
                strategy, tech, payload = "encoding", "encoding", "忽略宪法"
            else:
                tech = "base64" if i == 0 else rng.choice(techs)
                strategy = "encoding"
                payload = B64_IGNORE if tech == "base64" else "忽略宪法 %s" % tech
            out.append({
                "variant_id": "v-g%02d-%04d" % (generation_id, i),
                "generation_id": generation_id,
                "seed_id": (seeds[i % len(seeds)].get("id") if seeds else None),
                "strategy": strategy,
                "technique": tech,
                "depth": 1,
                "parent_variant_id": None,
                "payload": payload,
                "payload_sha256": "fake",
                "provenance": "offline_rule",
                "notes": "fake",
            })
        return out

    def save_results(self, variants, patch, out_path: str):
        self.events.append("judge")
        self.judge_patches.append(copy.deepcopy(patch))
        rows = []
        for v in variants:
            verdict = defense.scan(v["payload"], patch)
            rows.append({
                "variant_id": v["variant_id"], "generation_id": v["generation_id"],
                "seed_id": v.get("seed_id"), "strategy": v.get("strategy"),
                "technique": v.get("technique"), "payload": v["payload"],
                "intercepted": verdict["intercepted"], "tier": verdict["tier"],
                "stage": verdict["stage"], "matched": verdict["matched"],
                "matched_patch_ids": verdict["matched_patch_ids"],
                "decoders_used": verdict["decoders_used"],
                "bypass": not verdict["intercepted"],
            })
        rc._write_jsonl(out_path, rows)
        return rows


class FakeBlueTeam:
    """mode: decode（可修的补丁）| none（不产补丁）| benign_bad（会新增良性误伤的补丁）

    internal_reject=True 时模拟 A1 在 propose_patch 内部良性自检不过：
    返回 None，但把决定写进 LAST_ADJUDICATION（A1 的真实行为）。
    """

    def __init__(self, mode: str = "decode", *, events: list | None = None,
                 internal_reject: bool = False):
        if mode not in ("decode", "none", "benign_bad"):
            raise ValueError(mode)
        self.mode = mode
        self.internal_reject = internal_reject
        self.events = events if events is not None else []
        self.calls: list[dict] = []
        self.status_updates: list[dict] = []
        self.LAST_ADJUDICATION: dict = {}

    def propose_patch(self, bypasses, current_patch, *, generation_id: int,
                      ledger_path: str | None = None, persist: bool = True):
        self.events.append("propose")
        existing = (current_patch or {}).get("patches") or []
        self.calls.append({
            "generation_id": generation_id,
            "bypass_count": len(bypasses),
            "bypass_ids": [b.get("variant_id") for b in bypasses],
            "current_patches": len(existing),
            "ledger_path": ledger_path,
        })
        if self.mode == "none" or not bypasses:
            return None
        if self.internal_reject:
            self.LAST_ADJUDICATION = {
                "generation_id": generation_id,
                "reason": "benign_new_false_positive",
                "patch_id": "P-0001",
                "benign_fp": {"new_fp_ids": ["benign-rc-normal-01"], "patch_fp_ids": []},
            }
            return None
        if any(str(p.get("id")) == "P-0001" for p in existing):
            return None
        if self.mode == "benign_bad":
            item = {
                "id": "P-0001", "generation": generation_id, "kind": "stpa_filter",
                "rationale": "fake：故意写一条会误伤 benign 的规则（测试 A5 第二道闸门）",
                "evidence": {"bypass_ids": [b.get("variant_id") for b in bypasses[:3]],
                             "techniques": ["base64"]},
                "rollback": "删除 P-0001", "status": "candidate",
                "rules": [{"id": "RX-001", "regex": "Python", "note": "fake over-broad"}],
            }
        else:
            item = {
                "id": "P-0001", "generation": generation_id, "kind": "normalize_decode",
                "rationale": "fake：第 0 代 base64 绕过未拦截，加解码归一化",
                "evidence": {"bypass_ids": [b.get("variant_id") for b in bypasses[:3]],
                             "techniques": ["base64"]},
                "rollback": "删除 P-0001", "status": "candidate",
                "spec": {"detectors": ["base64"]},
            }
        if ledger_path:  # 模拟 A1 persist=True 往（run 内）账本追加候选
            self._write_ledger(ledger_path, item)
        return item

    @staticmethod
    def _write_ledger(path: str, item: dict) -> None:
        os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        with io.open(path, "w", encoding="utf-8") as fh:
            json.dump({"meta": {"schema": "phase-b-candidate-patch-v1",
                                "production_mutable": False},
                       "version": 1, "patches": [item]}, fh, ensure_ascii=False, indent=2)

    def update_patch_status(self, patch_id, status: str, *, ledger_path: str | None = None,
                            reason: str | None = None, evidence=None):
        self.status_updates.append({"id": patch_id, "status": status,
                                    "ledger_path": ledger_path, "reason": reason,
                                    "evidence": evidence})
        return {"id": patch_id, "status": status}


def make_runner(*, ok: bool = True, events: list | None = None):
    calls = []
    events = events if events is not None else []

    def runner(run_dir: str, generation_id: int) -> dict:
        calls.append(generation_id)
        events.append("regression")
        return {
            "ok": ok, "returncode": 0 if ok else 1, "suite": "audit892", "files": 14,
            "passed": 915, "failed": 0 if ok else 3, "seconds": 0.01,
            "json_path": None, "parse_error": False, "stdout_tail": [],
        }

    return runner, calls


def _run(name: str, *, red, blue, runner, token_meter=None,
         max_generations: int = 3, patience: int = 3, variants_per_gen: int = 12,
         seed: int = 20261028) -> tuple[dict, str, str]:
    out_base = fresh(name)
    # 测试注入临时账本：绝不写规范账本 rules/candidate_patch.yaml（A1 作用域）
    ledger_path = os.path.join(out_base, "candidate_patch.yaml")
    argv = ["--out", out_base,
            "--max-generations", str(max_generations),
            "--patience", str(patience),
            "--variants-per-gen", str(variants_per_gen),
            "--seed", str(seed)]
    kwargs = {"red_team": red, "blue_team": blue, "regression_runner": runner,
              "ledger_path": ledger_path}
    if token_meter is not None:
        kwargs["token_meter"] = token_meter
    result = rc.run(argv, **kwargs)
    return result, result["run_dir"], out_base


# ── 1. 停止条件（纯函数）────────────────────────────────

def test_stop_conditions_pure() -> None:
    base = dict(generations_run=2, max_generations=20, consecutive_zero_bypass=0,
                patience=3, api_tokens_used=0, token_budget=200000)
    check("stop-check: 连续 patience 代零绕过 → converged",
          rc.check_stop_conditions(**dict(base, consecutive_zero_bypass=3)) == "converged")
    check("stop-check: 未达 patience 不收敛",
          rc.check_stop_conditions(**dict(base, consecutive_zero_bypass=2)) is None)
    check("stop-check: token 超预算 → token_budget",
          rc.check_stop_conditions(**dict(base, generations_run=1,
                                          api_tokens_used=200001)) == "token_budget")
    check("stop-check: token 恰等于预算不触发（严格 >）",
          rc.check_stop_conditions(**dict(base, generations_run=1,
                                          api_tokens_used=200000)) is None)
    check("stop-check: 代数到上限 → max_generations",
          rc.check_stop_conditions(**dict(base, generations_run=20)) == "max_generations")
    check("stop-check: 代数未到上限 → None",
          rc.check_stop_conditions(**dict(base, generations_run=19)) is None)


# ── 2. 无补丁跑：核心字段 / 类型 / null 语义 / 6 步 ──────

def test_patchless_run() -> None:
    events: list = []
    red = FakeRedTeam(events=events)
    blue = FakeBlueTeam("none", events=events)
    runner, reg_calls = make_runner(events=events)
    result, run_dir, _ = _run("patchless", red=red, blue=blue, runner=runner)

    rows = read_rows(run_dir)
    check("patchless: 退出码 0", result["exit_code"] == 0, result.get("error"))
    check("patchless: log 行数 == generations_run == 3",
          len(rows) == result["generations_run"] == 3,
          "%d/%s" % (len(rows), result["generations_run"]))
    check("patchless: 无补丁 → stop_reason=max_generations",
          result["stop_reason"] == "max_generations", result["stop_reason"])
    check("patchless: 无补丁代不跑回归锁", reg_calls == [], reg_calls)

    core_ok, type_ok, null_ok = True, True, True
    variants_ok = True
    for row in rows:
        core_ok = core_ok and all(f in row for f in rc.CORE_FIELDS)
        type_ok = type_ok and (
            isinstance(row["generation_id"], int)
            and isinstance(row["red_team_variants_count"], int)
            and isinstance(row["bypass_success_count"], int)
            and isinstance(row["blue_team_patch_generated"], int)
            and (row["regression_test_pass"] is None or isinstance(row["regression_test_pass"], bool))
            and isinstance(row["benign_false_positive_rate"], float))
        null_ok = null_ok and row["regression_test_pass"] is None
        variants_ok = variants_ok and row["red_team_variants_count"] == 12
    check("patchless: 六个核心字段齐全", core_ok)
    check("patchless: 六字段类型正确", type_ok)
    check("patchless: 无补丁代 regression_test_pass 全为 None（不冒充 true）", null_ok)
    check("patchless: red_team_variants_count == --variants-per-gen", variants_ok)
    check("patchless: blue_team_patch_generated == 0", all(
        r["blue_team_patch_generated"] == 0 for r in rows))
    check("patchless: bypass>0（base64 变异确实绕过基线）",
          all(r["bypass_success_count"] > 0 for r in rows),
          [r["bypass_success_count"] for r in rows])
    check("patchless: attacks_intercepted + bypass == variants",
          all(r["attacks_intercepted_count"] + r["bypass_success_count"]
              == r["red_team_variants_count"] for r in rows))
    check("patchless: strategy_mix 计数和 == variants",
          all(sum(r["strategy_mix"].values()) == r["red_team_variants_count"] for r in rows))
    check("patchless: api_tokens_used 恒 0（零 API 事实）",
          all(r["api_tokens_used"] == 0 and r["api_calls"] == 0 for r in rows))
    check("patchless: 只有最后一行带 stop_reason，且为 max_generations",
          [r["stop_reason"] for r in rows] == [None, None, "max_generations"],
          [r["stop_reason"] for r in rows])
    check("patchless: ts 带 +08:00 时区",
          all(str(r.get("ts", "")).endswith("+08:00") for r in rows))
    check("patchless: 零日基线拦截率 < 演化（无补丁时相等）",
          all(r["intercept_rate_evolved_zeroday"] == r["intercept_rate_baseline_zeroday"]
              for r in rows))
    check("patchless: 红队只收到 train 种子",
          all(c["splits"] == ["train"] for c in red.generate_calls))
    check("patchless: generate 收到 n == --variants-per-gen",
          all(c["n"] == 12 for c in red.generate_calls))
    check("patchless: 零日集只来自 heldout split",
          red.zeroday_seed_splits == ["heldout"])
    check("patchless: 零日集显式用 generation_id=0（Lead 口径）",
          red.zeroday_generation_ids == [0], red.zeroday_generation_ids)
    check("patchless: 每行记录 zeroday_generation_id=0",
          all(r.get("zeroday_generation_id") == 0 for r in rows))

    ev = rc._read_json(result["evidence_path"])
    check("patchless: 写了 coevolution_evidence.json", isinstance(ev, dict))
    check("patchless: evidence.generations_run == 3", ev.get("generations_run") == 3)
    check("patchless: evidence.schema 正确",
          ev.get("schema") == "phase-b-coevolution-evidence-v1")
    check("patchless: evidence.patches 为空", ev.get("patches") == [])
    check("patchless: evidence.regression_all_pass 为 null（无补丁不冒充 true）",
          ev.get("regression_all_pass") is None)
    check("patchless: evidence.limitations 含 token 预算未获真实检验",
          any("token" in x and "未获真实检验" in x for x in ev.get("limitations") or []))
    check("patchless: evidence.zeroday_set.sha256 为 64 位 hex",
          isinstance(ev.get("zeroday_set", {}).get("sha256"), str)
          and len(ev["zeroday_set"]["sha256"]) == 64)
    check("patchless: evidence 良性基线误伤率记录 2/74",
          abs(ev.get("benign_false_positive_rate_baseline", 0) - round(2 / 74, 4)) < 1e-9,
          ev.get("benign_false_positive_rate_baseline"))
    check("patchless: 每代良性误伤率 == 基线", all(
        abs(r["benign_false_positive_rate"] - r["benign_false_positive_rate_baseline"]) < 1e-9
        for r in rows))


# ── 3. 补丁通过回归锁 → 进入演化防线 → 零日拦截率回升 ────

def test_patch_accepted_improves_zeroday() -> None:
    events: list = []
    red = FakeRedTeam(events=events)
    blue = FakeBlueTeam("decode", events=events)
    runner, reg_calls = make_runner(ok=True, events=events)
    result, run_dir, out_base = _run("accepted", red=red, blue=blue, runner=runner,
                                     max_generations=2, patience=3)
    rows = read_rows(run_dir)
    check("accepted: 退出码 0", result["exit_code"] == 0, result.get("error"))
    check("accepted: 2 代跑完", len(rows) == 2)
    check("accepted: 6 步顺序 generate→judge→propose→regression→…",
          events[:5] == ["zeroday", "generate", "judge", "propose", "regression"],
          events[:6])
    check("accepted: 第 0 代产补丁且回归锁通过",
          rows[0]["blue_team_patch_generated"] == 1
          and rows[0]["regression_test_pass"] is True)
    check("accepted: 回归锁只跑有补丁的代（gen0）", reg_calls == [0], reg_calls)
    check("accepted: 补丁进入演化防线（patches_total=1, ids=[P-0001]）",
          rows[0]["patches_total"] == 1 and rows[0]["patch_ids_accepted"] == ["P-0001"])
    check("accepted: 回归字段 suite/files/passed/failed 齐全且 0 failed",
          rows[0]["regression"]["suite"] == "audit892"
          and rows[0]["regression"]["files"] == 14
          and rows[0]["regression"]["failed"] == 0)
    check("accepted: 零日拦截率 基线 < 补丁后（真实 scan 判定）",
          rows[0]["intercept_rate_baseline_zeroday"]
          < rows[0]["intercept_rate_evolved_zeroday"],
          (rows[0]["intercept_rate_baseline_zeroday"],
           rows[0]["intercept_rate_evolved_zeroday"]))
    check("accepted: 第 1 代 bypass 归零（补丁真的挡住了 base64）",
          rows[1]["bypass_success_count"] == 0, rows[1]["bypass_success_count"])
    check("accepted: 第 1 代无补丁 → regression_test_pass 为 None",
          rows[1]["blue_team_patch_generated"] == 0
          and rows[1]["regression_test_pass"] is None)
    check("accepted: 补丁不新增良性误伤（计数仍为基线 2 条）",
          all(r["benign_false_positive_count"] == 2 for r in rows),
          [r["benign_false_positive_count"] for r in rows])
    check("accepted: 演化防线零日拦截率到达 1.0",
          rows[-1]["intercept_rate_evolved_zeroday"] == 1.0)
    check("accepted: judge 收到的第 0 代防线基线为空 patch（顺序未颠倒）",
          red.judge_patches[0] == {"version": 0, "patches": []},
          red.judge_patches[0])
    check("accepted: propose 收到的 bypass 数与第 0 代一致",
          blue.calls[0]["bypass_count"] == rows[0]["bypass_success_count"])
    check("accepted: propose 收到注入的候选账本路径（测试隔离，不写规范账本）",
          blue.calls[0]["ledger_path"] == os.path.join(out_base, "candidate_patch.yaml"),
          blue.calls[0]["ledger_path"])
    check("accepted: 注入账本已写入候选补丁（A1 persist 行为）",
          os.path.exists(os.path.join(out_base, "candidate_patch.yaml")))
    check("accepted: 通过回归锁后 A1 账本状态置 accepted_sandbox",
          bool(blue.status_updates)
          and blue.status_updates[-1]["status"] == "accepted_sandbox",
          blue.status_updates[-1:] or None)
    check("accepted: log 记录账本 basename 与每代快照名",
          rows[0].get("candidate_ledger") == "candidate_patch.yaml"
          and rows[0].get("candidate_ledger_snapshot") == "candidate_patch_snapshot_g0.yaml")
    check("accepted: run 目录内每代都有 candidate_patch_snapshot_g<g>.yaml",
          all(os.path.exists(os.path.join(run_dir, "candidate_patch_snapshot_g%d.yaml" % g))
              for g in (0, 1)))
    check("accepted: 零日集显式用 generation_id=0（Lead 口径）",
          red.zeroday_generation_ids == [0])
    check("accepted: evidence 记录 zeroday_protocol（held-out 非 OOD）",
          "generation-0" in str(rc._read_json(result["evidence_path"]).get("zeroday_protocol"))
          and "NOT OOD" in str(rc._read_json(result["evidence_path"]).get("zeroday_protocol")))

    ev = rc._read_json(result["evidence_path"])
    check("accepted: evidence.patches == ['P-0001']", ev.get("patches") == ["P-0001"])
    check("accepted: evidence.regression_all_pass is True", ev.get("regression_all_pass") is True)
    check("accepted: evidence 最终零日拦截率 1.0",
          ev.get("zeroday_evolved_intercept_rate_final") == 1.0)
    check("accepted: evidence 记录零日集 sha256 与基线拦截率 0.5",
          len(ev["zeroday_set"]["sha256"]) == 64
          and abs(ev["zeroday_baseline_intercept_rate"] - 0.5) < 1e-9)
    check("accepted: evidence.determinism.seed 落盘",
          ev.get("determinism", {}).get("seed") == 20261028)


# ── 4. 回归锁拒绝的补丁不许进演化防线 ───────────────────

def test_regression_rejection_blocks_patch() -> None:
    red = FakeRedTeam()
    blue = FakeBlueTeam("decode")
    runner, reg_calls = make_runner(ok=False)
    result, run_dir, _ = _run("regression_reject", red=red, blue=blue, runner=runner,
                              max_generations=1)
    rows = read_rows(run_dir)
    row = rows[0]
    check("reg-reject: 退出码 0（补丁被拒不是崩溃）", result["exit_code"] == 0)
    check("reg-reject: 补丁确实生成了（blue_team_patch_generated=1）",
          row["blue_team_patch_generated"] == 1)
    check("reg-reject: regression_test_pass 记 False", row["regression_test_pass"] is False)
    check("reg-reject: 被拒补丁不进演化防线（patches_total=0）",
          row["patches_total"] == 0 and row["patch_ids_accepted"] == [])
    check("reg-reject: patch_rejected 记录 reason=regression 与补丁 id",
          row["patch_rejected"]
          and row["patch_rejected"][0]["reason"] == "regression"
          and row["patch_rejected"][0]["id"] == "P-0001")
    check("reg-reject: 零日拦截率未因被拒补丁上升（演化==基线）",
          row["intercept_rate_evolved_zeroday"] == row["intercept_rate_baseline_zeroday"],
          (row["intercept_rate_evolved_zeroday"], row["intercept_rate_baseline_zeroday"]))
    check("reg-reject: 回归锁只跑一次", reg_calls == [0], reg_calls)
    check("reg-reject: A1 账本状态置 rejected（reason=regression_lock_failed）",
          bool(blue.status_updates)
          and blue.status_updates[0]["status"] == "rejected"
          and blue.status_updates[0]["reason"] == "regression_lock_failed",
          blue.status_updates[:1] or None)
    ev = rc._read_json(result["evidence_path"])
    check("reg-reject: evidence.patches 为空且 regression_all_pass False",
          ev.get("patches") == [] and ev.get("regression_all_pass") is False)


# ── 5. 良性误伤超过基线 → 丢弃补丁（第二道闸门）────────

def test_benign_false_positive_rejects_patch() -> None:
    red = FakeRedTeam()
    blue = FakeBlueTeam("benign_bad")
    runner, _ = make_runner(ok=True)
    result, run_dir, _ = _run("benign_fp", red=red, blue=blue, runner=runner,
                              max_generations=1)
    rows = read_rows(run_dir)
    row = rows[0]
    rejected = row["patch_rejected"][0] if row["patch_rejected"] else {}
    check("benign-fp: 回归锁通过（regression_test_pass=True），但补丁仍被丢弃",
          row["regression_test_pass"] is True and row["patches_total"] == 0)
    check("benign-fp: patch_rejected reason=benign_fp",
          rejected.get("reason") == "benign_fp", rejected.get("reason"))
    check("benign-fp: 记录补丁误伤率 > 基线误伤率",
          rejected.get("evidence", {}).get("patch_fp", 0)
          > rejected.get("evidence", {}).get("baseline_fp", 1))
    check("benign-fp: 记录新增误伤的良性 id",
          bool(rejected.get("evidence", {}).get("new_false_positive")))
    check("benign-fp: 落盘误伤率回落到基线（被拒补丁未生效）",
          abs(row["benign_false_positive_rate"]
              - row["benign_false_positive_rate_baseline"]) < 1e-9,
          (row["benign_false_positive_rate"], row["benign_false_positive_rate_baseline"]))
    check("benign-fp: patch_ids_accepted 为空",
          row["patch_ids_accepted"] == [])
    check("benign-fp: A1 账本状态置 rejected（reason=benign_fp）",
          bool(blue.status_updates)
          and blue.status_updates[0]["status"] == "rejected"
          and blue.status_updates[0]["reason"] == "benign_fp",
          blue.status_updates[:1] or None)
    ev = rc._read_json(result["evidence_path"])
    check("benign-fp: evidence.patches 为空", ev.get("patches") == [])


# ── 6. A1 内部良性自检拒绝（LAST_ADJUDICATION）→ 如实记录 ──

def test_internal_benign_rejection_recorded() -> None:
    red = FakeRedTeam()
    blue = FakeBlueTeam("decode", internal_reject=True)
    runner, reg_calls = make_runner(ok=True)
    result, run_dir, _ = _run("internal_reject", red=red, blue=blue, runner=runner,
                              max_generations=1)
    rows = read_rows(run_dir)
    row = rows[0]
    rejected = row["patch_rejected"][0] if row["patch_rejected"] else {}
    check("internal-reject: 补丁未生成（blue_team_patch_generated=0）",
          row["blue_team_patch_generated"] == 0)
    check("internal-reject: 不跑回归锁（没有候选补丁）",
          row["regression_test_pass"] is None and reg_calls == [])
    check("internal-reject: 如实记录 A1 内部拒绝原因 benign_fp_internal",
          rejected.get("reason") == "benign_fp_internal", rejected.get("reason"))
    check("internal-reject: 拒绝证据含新增误伤 id 与来源 LAST_ADJUDICATION",
          rejected.get("evidence", {}).get("source") == "A1.LAST_ADJUDICATION"
          and "benign-rc-normal-01" in (rejected.get("evidence", {}).get("new_fp_ids") or []))
    check("internal-reject: 零日演化拦截率不因被拒补丁上升",
          row["intercept_rate_evolved_zeroday"] == row["intercept_rate_baseline_zeroday"])


# ── 7. 收敛停止条件（连续 patience 代零绕过）────────────

def test_converged_stop() -> None:
    red = FakeRedTeam(all_intercepted=True)
    blue = FakeBlueTeam("decode")
    runner, _ = make_runner(ok=True)
    result, run_dir, _ = _run("converged", red=red, blue=blue, runner=runner,
                              max_generations=10, patience=2)
    rows = read_rows(run_dir)
    check("converged: 只跑了 patience 代（2 代）", len(rows) == 2, len(rows))
    check("converged: stop_reason == converged", result["stop_reason"] == "converged")
    check("converged: 第 0 代未立即判停", rows[0]["stop_reason"] is None)
    check("converged: 每代 bypass 都为 0", all(
        r["bypass_success_count"] == 0 for r in rows))
    check("converged: 末行带 stop_reason=converged",
          rows[-1]["stop_reason"] == "converged")
    ev = rc._read_json(result["evidence_path"])
    check("converged: evidence.converged is True 且 generations_run=2",
          ev.get("converged") is True and ev.get("generations_run") == 2)


# ── 7. token 预算停止条件（零 API 下用注入模拟）─────────
def test_token_budget_stop() -> None:
    red = FakeRedTeam()
    blue = FakeBlueTeam("none")
    runner, _ = make_runner()
    result, run_dir, _ = _run("token_budget", red=red, blue=blue, runner=runner,
                              max_generations=5, token_meter=lambda: 250000)
    rows = read_rows(run_dir)
    check("token: 第 0 代后即停（1 代）", len(rows) == 1)
    check("token: stop_reason == token_budget", result["stop_reason"] == "token_budget")
    check("token: 末行记录 token_budget", rows[-1]["stop_reason"] == "token_budget")
    ev = rc._read_json(result["evidence_path"])
    check("token: evidence.api_tokens_used 记录注入值 250000",
          ev.get("api_tokens_used") == 250000)
    check("token: evidence 如实写“token 预算条件未获真实检验”",
          any("token" in x and "未获真实检验" in x for x in ev.get("limitations") or []))


# ── 8. determinism：同 seed 两次跑 log 一致 ─────────────

def test_determinism() -> None:
    def one(name: str) -> list[dict]:
        red = FakeRedTeam()
        blue = FakeBlueTeam("decode")
        runner, _ = make_runner(ok=True)
        _, run_dir, _ = _run(name, red=red, blue=blue, runner=runner,
                             max_generations=2, seed=20261028)
        return read_rows(run_dir)

    rows_a = one("determinism_a")
    rows_b = one("determinism_b")
    check("determinism: 两次跑代数相同", len(rows_a) == len(rows_b) == 2)
    check("determinism: 同 seed 两次跑 log 逐行一致（剔除 ts/seconds）",
          [_strip_volatile(r) for r in rows_a] == [_strip_volatile(r) for r in rows_b])
    check("determinism: bypass 序列一致",
          [r["bypass_success_count"] for r in rows_a]
          == [r["bypass_success_count"] for r in rows_b])
    check("determinism: 接受补丁序列一致",
          [r["patch_ids_accepted"] for r in rows_a]
          == [r["patch_ids_accepted"] for r in rows_b])


# ── 9. dry-run：只跑 1 代、不写 evidence ─────────────────

def test_dry_run() -> None:
    out_base = fresh("dry_run")
    red = FakeRedTeam()
    blue = FakeBlueTeam("decode")
    runner, reg_calls = make_runner(ok=True)
    code = rc.main(["--out", out_base, "--dry-run", "--max-generations", "5",
                    "--variants-per-gen", "8", "--seed", "1"],
                   red_team=red, blue_team=blue, regression_runner=runner,
                   ledger_path=os.path.join(out_base, "candidate_patch.yaml"))
    runs = [os.path.join(out_base, d) for d in os.listdir(out_base)
            if os.path.isdir(os.path.join(out_base, d))]
    check("dry-run: 退出码 0", code == 0, code)
    check("dry-run: 只有一个 run 目录", len(runs) == 1, runs)
    run_dir = runs[0]
    rows = read_rows(run_dir)
    check("dry-run: 只跑 1 代（即使 --max-generations 5）", len(rows) == 1, len(rows))
    check("dry-run: stop_reason=max_generations", rows[0]["stop_reason"] == "max_generations")
    check("dry-run: 不写 coevolution_evidence.json",
          not os.path.exists(os.path.join(run_dir, "coevolution_evidence.json")))
    check("dry-run: log 仍可解析且核心字段齐全",
          all(f in rows[0] for f in rc.CORE_FIELDS))


# ── 10. 异常路径：退出码 1、不落 evidence ─────────────────

def test_exception_exit_code() -> None:
    red = FakeRedTeam(fail_at_generate=True)
    blue = FakeBlueTeam("none")
    runner, _ = make_runner()
    # run() 会 print_exc() 到 stderr（异常路径的审计需要），这里预期内，捕获避免干扰测试输出
    with contextlib.redirect_stderr(io.StringIO()):
        result, run_dir, _ = _run("exception", red=red, blue=blue, runner=runner,
                                  max_generations=2)
    check("exception: 退出码 1", result["exit_code"] == 1, result["exit_code"])
    check("exception: 错误里指明失败步骤 generate",
          "1/6 generate" in str(result.get("error")), result.get("error"))
    check("exception: 异常时不写 evidence",
          not os.path.exists(os.path.join(run_dir, "coevolution_evidence.json")))


TESTS = (
    test_stop_conditions_pure,
    test_patchless_run,
    test_patch_accepted_improves_zeroday,
    test_regression_rejection_blocks_patch,
    test_benign_false_positive_rejects_patch,
    test_internal_benign_rejection_recorded,
    test_converged_stop,
    test_token_budget_stop,
    test_determinism,
    test_dry_run,
    test_exception_exit_code,
)


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    shutil.rmtree(BASE, ignore_errors=True)
    os.makedirs(BASE, exist_ok=True)
    try:
        for fn in TESTS:
            print("\n[%s]" % fn.__name__)
            try:
                fn()
            except Exception as exc:  # noqa: BLE001
                traceback.print_exc()
                check("%s 执行无异常" % fn.__name__, False, repr(exc))
    finally:
        shutil.rmtree(BASE, ignore_errors=True)
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
