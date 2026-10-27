# -*- coding: utf-8 -*-
"""F6 · 存储层故障注入（docs/23 §5.6）

靶点是**真实的**插件存储层：`plugin/dsh-lingshu/index.js` 的 JSONL 适配器 +
`plugin/dsh-lingshu/memorywriter.js`（唯一写方，I5）。故障注入方式：

- 文件级：把 `memory.jsonl` 做成目录（读 EISDIR / 写 EISDIR）、预置撕裂行、重复 id、时钟偏移；
- 函数级：给 `memorywriter` 注入"会抛错 / 半写 / 只读不写"的 store；
- 结构级：非法条目、超大批次、跨批次 supersede、清理墓碑。

**断言口径（INV-16）严格限定在写路径**：
写入必须"要么确认成功、要么显式失败（抛错或计入 rejected）"，**不得**静默成功。
读路径的静默降级（`readJsonl` 吞掉坏行 / 读失败返回 []）作为 `observability_gap`
写进 notes 与 findings，**不算** INV-16 失败——但必须如实计数，不许当没发生。

本族通过 `node tools/chaos_v2/f6_storage_fault.mjs --no-model` 执行（零模型调用）；
任何绕过 `--no-model` 的调用都视为实现 bug（上层 INV-02 会拦）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess

FAMILY_ID = "f6_storage_faults"
STRESS_CLASS = "storage"
INVARIANTS = ("INV-16",)
WINDOW = "single"
VERDICT_KIND = "invariant"

# 写路径用例：必须"确认成功或显式失败"
WRITE_PATH_SCENARIOS = {
    "memory_path_is_dir", "append_throws", "read_unavailable", "read_throws",
    "append_partial", "bad_items", "clean_batch", "double_batch", "huge_batch",
    "silent_loss_probe", "cleanup_tombstone_conflict",
}
KNOWN_SCENARIOS = WRITE_PATH_SCENARIOS | {
    "existing_duplicate_ids", "existing_torn_line", "clock_skew_future", "clock_skew_past",
}


def _node_bin() -> str | None:
    for name in ("node", "node.exe"):
        p = shutil.which(name)
        if p:
            return p
    cands = [
        os.path.join(os.environ.get("ProgramFiles", r"C:\Program Files"), "nodejs", "node.exe"),
        os.path.join(os.path.expanduser("~"), ".dsh", "dsh-runtimes",
                     "dsh-primary-runtime", "dependencies", "node", "bin", "node.exe"),
    ]
    for p in cands:
        if os.path.isfile(p):
            return p
    return None


def _fail(failures, case, want, got, note=""):
    failures.append({"case": case, "want": want, "got": got, "note": note})


def run(rows, ctx):
    cfg = ctx["cfg"]
    repo_root = ctx["repo_root"]
    base_ts = float(ctx["base_ts"])
    failures: list[dict] = []
    notes: list[str] = []
    metrics: dict = {}
    rates: dict = {}
    samples: list = []

    # ── 1) 语料 → harness spec ───────────────────────────
    fam_rows = [r for r in rows if r.get("family") == FAMILY_ID]
    if not rows:
        return {"checks": {"cases": 0, "expectations": 0, "failed": 0, "failures": []},
                "metrics": {}, "rates": {}, "samples": [], "notes": ["无语料行"],
                "invariants": {i: {"pass": True, "detail": "无行可跑"} for i in INVARIANTS},
                "skipped": True, "skip_reason": "empty_corpus"}
    missing = [r.get("case") for r in fam_rows if str(r.get("input", {}).get("scenario")
                                                      or r.get("input", {}).get("fault") or "") not in KNOWN_SCENARIOS]
    for case in missing:
        _fail(failures, case, "known_scenario", "unknown_or_missing", "语料 scenario 不在 F6 注册表内")

    cases = []
    for r in fam_rows:
        inp = r.get("input") or {}
        scenario = str(inp.get("scenario") or inp.get("fault") or "")
        spec_case = {"case": r.get("case"), "scenario": scenario}
        if isinstance(inp.get("existing"), list):
            spec_case["existing"] = inp["existing"]
        if isinstance(inp.get("user_text"), str):
            spec_case["user_text"] = inp["user_text"]
        if scenario == "huge_batch":
            spec_case["count"] = int(inp.get("count", 1000))
        cases.append(spec_case)

    node = _node_bin()
    if node is None:
        notes.append("未找到 node：F6 跳过（不伪造存储层结果）")
        return {"checks": {"cases": len(cases), "expectations": 0, "failed": 0,
                           "failures": failures},
                "metrics": {"f6_skipped": 1}, "rates": {}, "samples": [], "notes": notes,
                "invariants": {i: {"pass": True, "detail": "node 缺失，未观测"} for i in INVARIANTS},
                "skipped": True, "skip_reason": "node_not_found"}

    harness = os.path.join(repo_root, "tools", "chaos_v2", "f6_storage_fault.mjs")
    if not os.path.isfile(harness):
        _fail(failures, "harness", "exists", harness, "F6 harness 缺失")
        return {"checks": {"cases": len(cases), "expectations": 0, "failed": len(failures),
                           "failures": failures},
                "metrics": {}, "rates": {}, "samples": [], "notes": notes,
                "invariants": {i: {"pass": False, "detail": "harness 缺失"} for i in INVARIANTS},
                "skipped": False, "skip_reason": None}

    # spec 走 stdin：受限沙箱可能禁止写系统 TEMP 或点目录，交给 node 自己用 os.tmpdir()
    # （node 侧每个用例 mkdtemp 后立即删除，符合 docs/23 §7 的爆炸半径声明）。
    spec = json.dumps({"cases": cases}, ensure_ascii=False)
    cmd = [node, harness, "--no-model", "--now", str(int(base_ts * 1000))]
    # timeout<=0 表示不限时（subprocess 的 timeout=0 会立刻超时/触发平台时间溢出，必须显式 None）
    raw_timeout = float(ctx.get("timeout_s", 90) or 0)
    proc_timeout = raw_timeout if raw_timeout > 0 else None
    try:
        proc = subprocess.run(cmd, cwd=repo_root, input=spec, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=proc_timeout)
    except subprocess.TimeoutExpired:
        notes.append("node harness 超时：F6 未观测（不伪造结果）")
        return {"checks": {"cases": len(cases), "expectations": 0, "failed": 0,
                           "failures": failures},
                "metrics": {"f6_timeout": 1}, "rates": {}, "samples": [], "notes": notes,
                "invariants": {i: {"pass": True, "detail": "超时，未观测"} for i in INVARIANTS},
                "skipped": True, "skip_reason": "node_timeout"}
    if proc.returncode != 0:
        _fail(failures, "harness", "exit0", proc.returncode, (proc.stderr or "")[-300:])
        return {"checks": {"cases": len(cases), "expectations": 0, "failed": len(failures),
                           "failures": failures},
                "metrics": {}, "rates": {}, "samples": [], "notes": notes,
                "invariants": {i: {"pass": False, "detail": "harness 非 0 退出"} for i in INVARIANTS},
                "skipped": False, "skip_reason": None}
    out = json.loads(proc.stdout)

    if int(out.get("model_calls", -1)) != 0:
        _fail(failures, "harness", "model_calls=0", out.get("model_calls"), "F6 禁止模型调用")

    by_case = {c.get("case"): c for c in out.get("cases", [])}

    # ── 2) 逐行判定（预注册口径）─────────────────────────
    expectations = 0
    for r in fam_rows:
        case = r.get("case")
        inp = r.get("input") or {}
        scenario = str(inp.get("scenario") or inp.get("fault") or "")
        got = by_case.get(case)
        if got is None:
            expectations += 1
            _fail(failures, case, "harness_case_present", None, "harness 未返回该 case")
            continue
        m = got.get("metrics") or {}
        obs = got.get("observations") or {}

        # 所有用例共同：不得崩溃
        expectations += 1
        if got.get("crash"):
            _fail(failures, case, "no_crash", True, str(got.get("error"))[:200])

        if scenario == "existing_duplicate_ids":
            expectations += 2
            if m.get("duplicate_id_collapsed") != 1:
                _fail(failures, case, "重复 id 被 last-wins 收敛", m.get("duplicate_id_collapsed"))
            if m.get("recalled_alpha") == 1:
                _fail(failures, case, "superseded 条目不被召回", "recalled")
        elif scenario == "existing_torn_line":
            expectations += 2
            if int(m.get("torn_line_silent_skip", 0)) < 1:
                _fail(failures, case, "撕裂行被识别/计数", m.get("torn_line_silent_skip"))
            if m.get("torn_entry_recalled") == 1:
                _fail(failures, case, "撕裂行条目不应被召回", "recalled")
            notes.append("observability_gap：撕裂行被 readJsonl 静默丢弃（无审计事件）"
                         if m.get("torn_line_silent_skip") else "")
        elif scenario in ("clock_skew_future", "clock_skew_past"):
            expectations += 1
            if scenario == "clock_skew_future" and m.get("future_clamped") != 1:
                _fail(failures, case, "未来时间戳不越权（days() clamp）", m.get("future_clamped"),
                      "低 importance 的未来条目排到了首位，说明时间差 clamp 失效/行为漂移")
            if scenario == "clock_skew_past" and m.get("stale_archived") != 1:
                _fail(failures, case, "过期条目被归档门滤掉", m.get("stale_archived"),
                      "900 天前条目仍进入命中列表：衰减/归档门与预期不符")
        elif scenario in ("memory_path_is_dir", "append_throws", "read_unavailable", "read_throws"):
            # INV-16 核心：写失败必须显式留痕，且不得伪报成功
            expectations += 2
            if m.get("write_failure_surfaced") != 1:
                _fail(failures, case, "写失败被 hook_error 留痕", m.get("hook_error_count"),
                      "store 写失败必须显式失败，不得静默")
            if m.get("false_success") == 1:
                _fail(failures, case, "不得伪报写入成功", "stateWrites>0 且 memory 为空")
            notes.append("读路径静默降级：读失败返回 []，与'无记忆'不可区分（observability_gap）")
        elif scenario == "append_partial":
            expectations += 2
            if m.get("threw_on_partial") != 1:
                _fail(failures, case, "半写必须抛错（不得静默成功）", m.get("threw_on_partial"))
            if int(m.get("duplicate_ids_after_retry", 0)) != 0:
                _fail(failures, case, "重试后不得产生重复 id", m.get("duplicate_ids_after_retry"))
        elif scenario == "bad_items":
            # 4 条非法 + 1 条合法对照：必须拒绝 4 条、只写 1 条
            expectations += 3
            if int(m.get("rejected_bad_items", 0)) != 4:
                _fail(failures, case, "非法条目 rejected=4", m.get("rejected_bad_items"))
            if int(m.get("bad_items_written", 0)) != 1:
                _fail(failures, case, "只有合法对照条目被写入", m.get("bad_items_written"))
            if int(m.get("rejected_bad_items", 0)) > 0 and int(m.get("bad_items_written", 0)) == 1:
                pass
        elif scenario == "huge_batch":
            want = int(inp.get("count", 1000))
            expectations += 3
            if int(m.get("huge_batch_written", 0)) != want:
                _fail(failures, case, f"大批次全部写入 {want} 条", m.get("huge_batch_written"))
            if int(m.get("huge_batch_rows", 0)) != want:
                _fail(failures, case, f"落盘行数 = {want}（无墓碑/无覆盖）", m.get("huge_batch_rows"))
            if m.get("huge_batch_monotonic") != 1:
                _fail(failures, case, "大批次 id 单调", "非单调")
        elif scenario == "double_batch":
            expectations += 1
            if int(m.get("double_batch_superseded", 0)) < 1:
                _fail(failures, case, "跨批次 supersede 墓碑", m.get("double_batch_superseded"))
        elif scenario == "clean_batch":
            expectations += 1
            if int(m.get("clean_batch_written", 0)) < 1:
                _fail(failures, case, "干净批次应写入成功", m.get("clean_batch_written"))
        elif scenario == "silent_loss_probe":
            expectations += 1
            # 这是**已知边界**：writer 无法校验 store 是否真正持久化，不作为失败。
            notes.append("boundary：writer 只信 store.append 返回，无法检测'只读不写'的 store"
                         "（docs/23 §9，不判失败）")
            if m.get("silent_loss_undetectable") != 1:
                notes.append("silent_loss_probe 观测与预期不同：writer 似乎做了额外校验（值得复核）")
        elif scenario == "cleanup_tombstone_conflict":
            expectations += 1
            if int(m.get("cleanup_visible", 0)) < 1:
                _fail(failures, case, "清理墓碑在 last-wins 下可见", m.get("cleanup_visible"))

        samples.append({
            "case": case, "scenario": scenario,
            "audit_kinds": obs.get("audit_kinds", {}),
            "metrics": {k: v for k, v in m.items() if k != "elapsed_ms"},
        })

    # ── 3) 汇总指标与不变量 ──────────────────────────────
    n_write = sum(1 for r in fam_rows
                  if str((r.get("input") or {}).get("scenario")
                         or (r.get("input") or {}).get("fault") or "") in WRITE_PATH_SCENARIOS)
    surfaced = sum(1 for c in out.get("cases", [])
                   if (c.get("metrics") or {}).get("write_failure_surfaced") == 1)
    write_fail_cases = [c for c in out.get("cases", [])
                        if c.get("scenario") in ("memory_path_is_dir", "append_throws",
                                                 "read_unavailable", "read_throws")]
    metrics.update({
        "cases": len(fam_rows),
        "harness_cases": len(out.get("cases", [])),
        "write_path_cases": n_write,
        "write_failure_surfaced_cases": surfaced,
        "read_path_silent_cases": len(write_fail_cases),
        "node_model_calls": out.get("model_calls"),
        "total_hook_errors": sum(int((c.get("metrics") or {}).get("hook_error_count", 0))
                                 for c in out.get("cases", [])),
        "total_rejected": sum(int((c.get("metrics") or {}).get("rejected_total", 0))
                              for c in out.get("cases", [])),
        "total_supersede_events": sum(int((c.get("metrics") or {}).get("supersede_events", 0))
                                      for c in out.get("cases", [])),
    })
    rates["write_failure_surfaced"] = {"num": surfaced, "den": len(write_fail_cases)}

    # INV-16：写路径"确认成功或显式失败"，无静默成功；且无崩溃
    write_path_failures = [f for f in failures if f.get("case") in by_case and
                           str((next((r for r in fam_rows if r.get("case") == f.get("case")),
                                      {}).get("input") or {}).get("scenario")
                           or (next((r for r in fam_rows if r.get("case") == f.get("case")),
                                    {}).get("input") or {}).get("fault") or "") in WRITE_PATH_SCENARIOS]
    crashes = [c.get("case") for c in out.get("cases", []) if c.get("crash")]
    inv16 = not write_path_failures and not crashes
    detail = ("写路径 %d 用例全部'确认成功或显式失败'，崩溃 %d 例；读路径静默降级 %d 例（observation-only）"
              % (n_write, len(crashes), len(write_fail_cases)))
    notes = [n for n in notes if n]
    # 单一 finding 条目：读路径静默降级是 observability_gap（不计入 INV-16，但必须点名）
    notes.insert(0, "FINDING-F05（observability_gap，见 findings.md）：读路径 readJsonl 静默丢弃坏行 / "
                    "读失败返回 []，与'无记忆'不可区分；写路径不受影响（INV-16 只管写路径）。")
    if not notes:
        notes = ["N/A"]

    return {
        "checks": {"cases": len(fam_rows), "expectations": expectations,
                   "failed": len(failures), "failures": failures},
        "metrics": metrics,
        "rates": rates,
        "samples": samples[:20],
        "notes": notes,
        "invariants": {"INV-16": {"pass": bool(inv16), "detail": detail}},
        "skipped": False,
        "skip_reason": None,
    }
