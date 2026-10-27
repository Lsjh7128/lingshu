#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""tools/run_chaos_campaign.py —— LINGSHU 混沌工程 v2 campaign 执行器（A5）

回答的问题（vs v1）：v1 证明"机制在四段固定剧本里按契约动作"；
v2 要回答"这些机制在多大压力、哪些维度、哪种故障模式下仍然守约，误报多少，边界在哪"。
本文件是 v2 的**执行与判定入口**，设计契约见
`docs/23-混沌工程v2-科学压测设计.md`（§4 不变量 / §6 统计与判定 / §7 预算 / §10 schema）。

行为契约（逐条对应 docs/23）：

1. 读 `chaos-set/v2/manifest.json`（预注册：族清单/刺激集/预期/指纹），校验 schema；
2. 逐族核对语料 SHA-256 —— 不符**不静默继续**：该族 `skipped=true` +
   `skip_reason="corpus_hash_mismatch"` + anomalies；
3. 每族 R 次（`seed = seed_base + r`），按 manifest.module 用 importlib 动态导入族模块；
   `r=0` 再跑一遍比 digest（INV-01 确定性）；
4. 族模块 import 失败 / 抛异常 → 该族 skipped + anomalies，**不整轮崩**，退出码 2；
5. 墙钟预算（默认 90s）：超时后剩余族 `skipped=true` + `skip_reason="wall_budget"`，
   已跑结果照常落盘；
6. 产物：`<out-dir>/<run_id>.json`、`<out-dir>/.last.json`、
   `<out-dir>/<YYYY-MM-DD>-report.md`（中文报告）。历史 JSON 不覆盖（同名加 -2/-3）；
7. 退出码：`0` 全部不变量通过、无异常（**FINDING 只记录不门禁**，除非 `--strict-findings`）；
   `2` 有不变量失败/异常/finding（strict 档）；`1` 用法或语料结构性错误；
8. 写盘范围：只写 out-dir（默认 `acceptance/chaos/v2`）下的**文件**，不建临时目录、不建子目录
   （受限沙箱下 Python 不能新建目录；F6 的 node 侧自己在 os.tmpdir() 里建删临时目录）；
   INV-19 用运行前后文件 mtime/size 快照证明零仓库污染；
9. engine 模式**零模型调用**：INV-02 用审计钩子记录子进程/网络事件作为证据。

用法（PowerShell，仓库根目录）：

    $py = "<PYTHON>"
    $py tools\\run_chaos_campaign.py                       # 全族 campaign（engine，零模型，≤90s）
    $py tools\\run_chaos_campaign.py --list                # 列出族 id/标题/mechanism/ucas
    $py tools\\run_chaos_campaign.py --family f1_stpa_matrix --json-only
    $py tools\\run_chaos_campaign.py --seed 20261027 --repeats 3 --run-id my-run
    $py tools\\run_chaos_campaign.py --timeout-seconds 0   # 0 = 不限墙钟（调试用）

诚实边界：v2 不产 measured、不宣布阈值正确、不把合成压力当真实证据（docs/23 §0/§9）。
"""
from __future__ import annotations

import argparse
import importlib
import json
import os
import re
import sys
import time
import traceback
from datetime import datetime

# 必须在 import 本仓库模块之前关掉字节码写入：否则 tools/ 或 engine/ 下会生成
# __pycache__，INV-19「零仓库污染」就会误报（解释器副产物也会改仓库 mtime）。
sys.dont_write_bytecode = True

# Windows 控制台默认 cp936，中文/emoji 会 UnicodeEncodeError；先兜底重配。
try:
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover - 老解释器/被重定向时忽略
    pass

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS_DIR = os.path.dirname(os.path.abspath(__file__))
for _path in (ROOT, TOOLS_DIR):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from engine import config as cfgmod          # noqa: E402
from chaos_v2 import invariants as I          # noqa: E402
from chaos_v2 import corpus as C              # noqa: E402
from chaos_v2 import report as R              # noqa: E402
from chaos_v2 import stats as S               # noqa: E402

DEFAULT_MANIFEST = os.path.join(ROOT, "chaos-set", "v2", "manifest.json")
DEFAULT_OUT_DIR = os.path.join(ROOT, "acceptance", "chaos", "v2")
DEFAULT_TIMEOUT_S = 90.0
MODE = "engine"
SCHEMA = "lingshu-chaos-campaign/2"


# ── 小工具 ──────────────────────────────────────────────
class UsageError(Exception):
    """命令行用法错误（退出码 1，不写产物）。"""


class FamilyImportError(Exception):
    """族模块动态导入失败。"""


def now_local() -> datetime:
    return datetime.now().astimezone()


def iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


def _rel_display(path: str) -> str:
    """展示用相对路径（仓库内），仓库外退回绝对路径。"""
    if not path:
        return "—"
    try:
        rel = os.path.relpath(os.path.abspath(path), ROOT)
    except (OSError, ValueError):
        return str(path)
    if rel.startswith(".."):
        return str(path)
    return rel.replace("\\", "/")


def write_json(path: str, obj) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2, sort_keys=False)
        f.write("\n")


def write_text(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(text if text.endswith("\n") else text + "\n")


def _unique_path(path: str) -> str:
    """历史文件不覆盖：同名已存在就加 -2 / -3 …（docs/23 §10）。"""
    if not os.path.exists(path):
        return path
    base, ext = os.path.splitext(path)
    idx = 2
    while True:
        candidate = "%s-%d%s" % (base, idx, ext)
        if not os.path.exists(candidate):
            return candidate
        idx += 1


def _tb_tail(limit: int = 1200) -> str:
    text = traceback.format_exc()
    return text if len(text) <= limit else text[-limit:]


def _freeze(value) -> str:
    try:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return str(value)


class _WallBudget:
    """软墙钟预算（docs/23 §7）。<=0 表示不限（调试用），默认 90s。"""

    def __init__(self, timeout_s: float) -> None:
        self.timeout_s = float(timeout_s or 0.0)
        self.unlimited = self.timeout_s <= 0
        self.t0 = time.monotonic()

    def elapsed(self) -> float:
        return time.monotonic() - self.t0

    def expired(self) -> bool:
        return (not self.unlimited) and self.elapsed() >= self.timeout_s

    def remaining(self) -> float:
        if self.unlimited:
            return float("inf")
        return max(0.0, self.timeout_s - self.elapsed())

    def label(self) -> str:
        return "无限" if self.unlimited else "%.1f" % self.timeout_s


# ── 族模块动态导入 ──────────────────────────────────────
def _module_candidates(module_ref: str) -> list:
    ref = str(module_ref or "").strip()
    candidates = []
    if ref.startswith("tools.chaos_v2."):
        candidates.append(ref[len("tools."):])
    if ref.startswith("chaos_v2."):
        candidates.append(ref)
    elif "." in ref:
        candidates.extend([ref, "chaos_v2." + ref])
    else:
        candidates.extend([
            "chaos_v2.families." + ref,
            "tools.chaos_v2.families." + ref,
            ref,
        ])
    out = []
    for item in candidates:
        if item and item not in out:
            out.append(item)
    return out


def import_family_module(module_ref: str):
    """按 manifest.families[].module 动态导入族模块，返回 (module, 实际导入名)。"""
    errors = []
    for name in _module_candidates(module_ref):
        try:
            return importlib.import_module(name), name
        except Exception as exc:  # noqa: BLE001 - 任何导入期异常都要变成 skipped
            errors.append("%s → %s: %s" % (name, type(exc).__name__, exc))
    raise FamilyImportError("导入 %r 失败：%s" % (module_ref, "；".join(errors)))


# ── ctx（族模块的只读上下文）────────────────────────────
def build_ctx(seed: int, repeat: int, base_ts: float, timeout_s: float,
              run_id: str) -> dict:
    """构造族 run(rows, ctx) 的 ctx（契约见 families/__init__.py）。

    `cfg` 每次都是 `engine.config.default()` 的**全新深拷贝**：族想改 tokenRatioMax
    之类只能改自己拿到的副本，绝不污染下一族或 `rules/`（docs/23 §5.5 / §7）。

    注意：**不提供 temp_dir**（环境约束：受限沙箱下 Python 不能新建目录，F6 的
    node 侧自己在 os.tmpdir() 里建删临时目录，不需要 runner 给路径）。
    """
    return {
        "seed": int(seed),
        "repeat": int(repeat),
        "cfg": cfgmod.default(),      # default() 内部已 deepcopy
        "base_ts": float(base_ts),
        "repo_root": ROOT,
        "timeout_s": float(timeout_s),
        "python": sys.executable or "",
        "run_id": run_id,
    }


# ── 族结果记录与跳过 ────────────────────────────────────
def _anomaly(global_anomalies: list, record: dict, family_id: str,
             kind: str, detail: str) -> None:
    item = {"family": str(family_id), "kind": str(kind), "detail": str(detail)}
    global_anomalies.append(item)
    record.setdefault("anomalies", []).append(dict(item))


def _unproven_invariant(detail: str) -> dict:
    return {"pass": False, "flaky": False, "observed": 0, "detail": str(detail)}


def _skip_family(record: dict, declared, reason: str, detail: str,
                 global_anomalies: list, family_id: str) -> None:
    """把族标成 skipped=true（**绝不**用 0/空冒充结果，docs/23 §6.7 / 红线 3）。"""
    record["skipped"] = True
    record["skip_reason"] = str(reason)
    invariants = record.setdefault("invariants", {})
    for inv_id in list(declared or []):
        invariants[str(inv_id)] = _unproven_invariant(
            "族 skipped（%s）：不变量未证明" % reason)
    for inv_id in ("INV-01", "INV-20"):   # runner 归属，缺了也算 schema 不完整
        invariants.setdefault(inv_id, _unproven_invariant(
            "族 skipped（%s）：无法证明" % reason))
    _anomaly(global_anomalies, record, family_id, "skipped",
             "%s：%s" % (reason, detail))


def _normalize_abort_reason(reason: str) -> str:
    text = str(reason or "").strip()
    if text.startswith("family_skip:"):
        inner = text.split(":", 1)[1].strip() or "unspecified"
        return "family_skip:" + inner
    return text or "family_exception"


def _run_digest(normalized: dict) -> str:
    """单次运行的 digest（INV-01 逐字节确定性的比较对象）。

    覆盖 checks / metrics / rates / invariants（比 §6.2 字面的"对 metrics 做 SHA-256"
    更严：失败清单与不变量判定也纳入确定性），时间/路径类键由 stats 排除。
    """
    return S.digest_of_metrics({
        "checks": normalized.get("checks") or {},
        "metrics": normalized.get("metrics") or {},
        "rates": normalized.get("rates") or {},
        "invariants": normalized.get("invariants") or {},
    })


# ── 单族执行 ────────────────────────────────────────────
def _run_family(fam: dict, fc, repeats: int, seed_base: int, base_ts: float,
                budget: _WallBudget, run_id: str,
                global_anomalies: list, probe: I.AuditProbe) -> dict:
    """跑一个族的 R 次 + 确定性复跑 + 聚合；任何异常都变成 skipped/anomaly。"""
    fid = str(fam["id"])
    record = {
        "family": fid,
        "title": str(fam.get("title") or ""),
        "stress_class": str(fam.get("stress_class") or ""),
        "mechanism": str(fam.get("mechanism") or ""),
        "ucas": [str(u) for u in (fam.get("ucas") or [])],
        "window": fam.get("window"),
        "verdict_kind": fam.get("verdict_kind"),
        "severities": dict(fc.severity_counts) if fc is not None else {},
        "skipped": False,
        "skip_reason": None,
        "modules": [],
        "runs": [],
        "checks": {"cases": 0, "expectations": 0, "failed": 0, "failures": []},
        "rates": {},
        "metrics": {},
        "samples": [],
        "notes": [],
        "invariants": {},
        "anomalies": [],
    }
    declared_manifest = [str(x) for x in (fam.get("invariants") or [])]

    # ── 1. 数据资格：语料指纹不符/缺失 → skipped，绝不静默继续 ──
    if fc is None:
        _skip_family(record, declared_manifest, "corpus_missing",
                     "manifest 承诺的语料未加载（没有对应文件记录）", global_anomalies, fid)
        return record
    if not fc.ok:
        reason = "corpus_missing" if fc.status == "missing" else "corpus_hash_mismatch"
        detail = ("语料文件缺失：%s" % fc.path) if fc.status == "missing" else (
            "语料 SHA-256 不符：%s（manifest=%s… 实测=%s…，行封套校验已通过但指纹被改，"
            "按 docs/23 §6.2/§6.7 拒绝判定）"
            % (os.path.basename(fc.path), str(fc.expected_sha256)[:12],
               str(fc.actual_sha256)[:12]))
        _skip_family(record, declared_manifest, reason, detail, global_anomalies, fid)
        return record

    # ── 2. 动态导入 + 模块契约 ──
    try:
        module, import_name = import_family_module(str(fam["module"]))
    except FamilyImportError as exc:
        _skip_family(record, declared_manifest, "family_import_error", str(exc),
                     global_anomalies, fid)
        return record
    record["modules"] = [import_name]
    for problem in I.check_module_contract(fam, module):
        _anomaly(global_anomalies, record, fid, "contract", problem)
    if not callable(getattr(module, "run", None)):
        _skip_family(record, declared_manifest, "family_contract",
                     "族模块缺可调用 run(rows, ctx)", global_anomalies, fid)
        return record

    declared = []
    for source in (declared_manifest, list(getattr(module, "INVARIANTS", ()) or ())):
        for inv_id in source:
            text = str(inv_id)
            if text not in declared:
                declared.append(text)
    if not declared:
        _anomaly(global_anomalies, record, fid, "contract",
                 "族未声明任何不变量：无法判定（缺失 = schema 违规 = 失败）")

    rows = list(fc.rows)
    normalized_runs = []      # [{"repeat","seed","payload","digest"}]
    aborted = None
    probe.begin_family(fid)
    try:
        # ── 3. R 次重复（seed = seed_base + r）──
        for r in range(int(repeats)):
            if budget.expired():
                aborted = "wall_budget"
                _anomaly(global_anomalies, record, fid, "wall_budget",
                         "族内第 %d/%d 次重复前墙钟预算（%ss）用尽"
                         % (r + 1, repeats, budget.label()))
                break
            seed = int(seed_base) + r
            ctx = build_ctx(seed, r, base_ts, budget.remaining(), run_id)
            try:
                payload = module.run(rows, ctx)
            except Exception as exc:  # noqa: BLE001 - 族异常 → skipped，不整轮崩
                _anomaly(global_anomalies, record, fid, "exception",
                         "run(repeat=%d, seed=%d) 抛异常：%s: %s\n%s"
                         % (r, seed, type(exc).__name__, exc, _tb_tail()))
                aborted = "family_exception"
                break
            normalized, problems = I.normalize_family_payload(fid, declared, payload)
            for problem in problems:
                _anomaly(global_anomalies, record, fid, "contract",
                         "repeat=%d：%s" % (r, problem))
            digest = _run_digest(normalized)
            record["runs"].append({
                "repeat": r,
                "seed": seed,
                "digest": digest,
                "cases": int(normalized["checks"]["cases"]),
                "pass": max(0, int(normalized["checks"]["cases"])
                            - int(normalized["checks"]["failed"])),
                "fail": int(normalized["checks"]["failed"]),
                "skipped": bool(normalized["skipped"]),
                "skip_reason": normalized.get("skip_reason"),
            })
            normalized_runs.append({"repeat": r, "seed": seed,
                                    "payload": normalized, "digest": digest})
            if normalized["skipped"]:
                aborted = "family_skip:" + str(normalized.get("skip_reason") or "unspecified")
                break

        # ── 4. INV-01：r=0 同 seed 复跑（逐字节确定性）──
        det = {"pass": False, "detail": "未执行：确定性复跑条件不满足（%s）"
                                        % (aborted or "unknown")}
        if normalized_runs and not aborted:
            if budget.expired():
                det = {"pass": False, "detail": "墙钟预算用尽，确定性复跑未执行（未证明）"}
                _anomaly(global_anomalies, record, fid, "wall_budget",
                         "确定性复跑前墙钟预算用尽：INV-01 未证明")
            else:
                first = normalized_runs[0]
                try:
                    ctx2 = build_ctx(first["seed"], 0, base_ts, budget.remaining(),
                                     run_id)
                    payload2 = module.run(rows, ctx2)
                    normalized2, problems2 = I.normalize_family_payload(fid, declared, payload2)
                    for problem in problems2:
                        _anomaly(global_anomalies, record, fid, "contract",
                                 "r=0 确定性复跑：%s" % problem)
                    det = I.check_determinism(fid, first["digest"],
                                              _run_digest(normalized2), first["seed"])
                except Exception as exc:  # noqa: BLE001
                    _anomaly(global_anomalies, record, fid, "exception",
                             "r=0 确定性复跑抛异常：%s: %s\n%s"
                             % (type(exc).__name__, exc, _tb_tail()))
                    det = {"pass": False, "detail": "r=0 确定性复跑抛异常：%s" % exc}
    finally:
        probe.end_family()

    # ── 5. 聚合（§6.3 / §6.4）──
    if normalized_runs:
        base = normalized_runs[0]["payload"]
        cases_list = [int(nr["payload"]["checks"]["cases"]) for nr in normalized_runs]
        if len(set(cases_list)) > 1:
            _anomaly(global_anomalies, record, fid, "contract",
                     "各重复的 cases 数不一致：%r（同一份语料应恒定）" % (cases_list,))
        failures, seen = [], set()
        failed_max = 0
        for nr in normalized_runs:
            checks = nr["payload"]["checks"]
            failed_max = max(failed_max, int(checks["failed"]))
            for item in checks["failures"]:
                key = (str(item.get("case")), _freeze(item.get("want")),
                       _freeze(item.get("got")), str(item.get("note")))
                if key in seen:
                    continue
                seen.add(key)
                failures.append({
                    "case": str(item.get("case", "")),
                    "want": item.get("want", ""),
                    "got": item.get("got", ""),
                    "note": str(item.get("note", "") or ""),
                    "repeat": nr["repeat"],
                })
        record["checks"] = {
            "cases": int(base["checks"]["cases"]),
            "expectations": int(base["checks"]["expectations"]),
            "failed": max(failed_max, len(failures)),
            "failures": failures,
        }
        record["rates"] = S.rates_summary([nr["payload"]["rates"]
                                           for nr in normalized_runs])
        record["metrics"] = base["metrics"]
        record["samples"] = list(base.get("samples") or [])[:20]
        seen_notes = set()
        for nr in normalized_runs:
            for note in nr["payload"]["notes"]:
                if note not in seen_notes:
                    seen_notes.add(note)
                    record["notes"].append(note)
        aggregated = I.aggregate_family_invariants(
            declared, [nr["payload"]["invariants"] for nr in normalized_runs])
        aggregated["INV-01"] = {
            "pass": bool(det.get("pass")), "flaky": False, "observed": 1,
            "detail": str(det.get("detail") or ""),
        }
        stability = I.check_verdict_stability(
            fid, [nr["payload"]["invariants"] for nr in normalized_runs])
        aggregated["INV-20"] = {
            "pass": bool(stability.get("pass")), "flaky": False,
            "observed": len(normalized_runs),
            "detail": str(stability.get("detail") or ""),
        }
        record["invariants"] = aggregated
    else:
        for inv_id in declared:
            record["invariants"][str(inv_id)] = _unproven_invariant(
                "族未产出任何有效重复（%s）：不变量未证明" % aborted)
        for inv_id in ("INV-01", "INV-20"):
            record["invariants"].setdefault(inv_id, _unproven_invariant(
                "族未产出任何有效重复（%s）：无法证明" % aborted))

    if aborted:
        record["skipped"] = True
        record["skip_reason"] = _normalize_abort_reason(aborted)
        for inv_id, entry in record["invariants"].items():
            original = str(entry.get("detail") or "")
            entry["pass"] = False
            entry["flaky"] = False
            entry["detail"] = ("族未完成（%s，R=%d/%d）：不变量未证明；原判读：%s"
                               % (record["skip_reason"], len(normalized_runs), repeats,
                                  original or "—"))
    return record


# ── 汇总 / 退出码 ───────────────────────────────────────
def _summarize(families: dict, runner_invariants: dict, manifest: C.Manifest,
               selected: list, global_anomalies: list) -> dict:
    """跨族汇总（docs/23 §10 summary 字段；额外字段只追加）。"""
    per_id = {}
    for inv_id in I.INVARIANT_IDS:
        entries = []
        for fam in families.values():
            entry = (fam.get("invariants") or {}).get(inv_id)
            if isinstance(entry, dict):
                entries.append(entry)
        if inv_id in runner_invariants:
            entries.append(runner_invariants[inv_id])
        if entries:
            per_id[inv_id] = {
                "pass": all(bool(e.get("pass")) for e in entries),
                "flaky": any(bool(e.get("flaky")) for e in entries),
            }
    families_run = len([f for f in families.values() if not f.get("skipped")])
    families_skipped = len(families) - families_run
    checks_total = sum(int((f.get("checks") or {}).get("cases") or 0)
                       for f in families.values())
    checks_failed = sum(int((f.get("checks") or {}).get("failed") or 0)
                        for f in families.values())
    failures_total = sum(len((f.get("checks") or {}).get("failures") or [])
                         for f in families.values())
    coverage = {}
    for fam in selected:
        fid = str(fam.get("id"))
        for uca in fam.get("ucas") or []:
            coverage.setdefault(str(uca), [])
            if fid not in coverage[str(uca)]:
                coverage[str(uca)].append(fid)
    uncovered = [inv_id for inv_id in I.INVARIANT_IDS if inv_id not in per_id]
    summary = {
        "families_run": families_run,
        "families_skipped": families_skipped,
        "invariants_total": len(I.INVARIANT_IDS),
        "invariants_passed": sum(1 for v in per_id.values() if v["pass"]),
        "invariants_flaky": sum(1 for v in per_id.values() if v["flaky"]),
        "invariants_covered": len(per_id),
        "invariants_uncovered": uncovered,
        "checks_total": checks_total,
        "checks_failed": checks_failed,
        "failures_total": failures_total,
        "anomalies_total": len(global_anomalies),
        "findings": _collect_findings(families),
        "findings_total": len(_collect_findings(families)),
        "coverage": coverage,
        "digest": "",
    }
    return summary


_FINDING_TOKEN_RE = re.compile(r"(?i)\bfinding\b")


def _collect_findings(families: dict) -> list:
    """从族 notes 里收集 finding（产品缺陷发现，单独任务修）。

    标签约定（族模块实际在用的两种写法，统一按 token 识别）：
      - 前缀式：`FINDING-1（engine 与 docs/19 §5 不符）…`
      - 标注式：`…这是引擎接线缺口（finding）`
    为什么用 `\\bfinding\\b` 而不是更宽的字符串包含：`findings.md`、`findings_total`
    这类词不能被误判为"一条 finding"；而 `FINDING-1`、`（finding）` 都能命中。
    findings **不参与不变量布尔判定**（默认不改变退出码，`--strict-findings` 才门禁），
    但原样进 `summary.findings`、报告顶部与 `acceptance/chaos/v2/findings.md`（§8.8）。
    """
    findings = []
    for fid, fam in families.items():
        for note in (fam.get("notes") or []):
            text = str(note).strip()
            if _FINDING_TOKEN_RE.search(text):
                findings.append({"family": fid, "note": text})
    return findings


def _decide_exit(families: dict, runner_invariants: dict,
                 global_anomalies: list, strict_findings: bool = False) -> tuple:
    """退出码（§6.6 + Lead 2026-10-27 修订）：

    - `0`：全部不变量通过、无异常、无检查失败 —— **即便有 FINDING 也算 0**
      （FINDING 是"产品缺陷发现"，单独任务修；它不是 v2 不变量回归，不该让夜间永远红）。
    - `2`：有不变量失败 / 异常 / 检查失败 / finding（仅 `--strict-findings` 时）。
    - `1`（不在这里）：用法或语料结构性错误。

    findings 永远不会被洗成 pass：它们原样进 `summary.findings`、报告顶部与
    `findings.md`，只是不参与"不变量是否成立"的布尔判定。
    """
    problems = []
    for fid, fam in families.items():
        if fam.get("skipped"):
            problems.append("%s skipped（%s）" % (fid, fam.get("skip_reason")))
        checks = fam.get("checks") or {}
        if int(checks.get("failed") or 0) > 0:
            problems.append("%s 有 %d 条检查失败" % (fid, int(checks["failed"])))
        for inv_id, entry in (fam.get("invariants") or {}).items():
            if not (isinstance(entry, dict) and entry.get("pass")):
                problems.append("%s 不变量 %s 未通过" % (fid, inv_id))
            elif entry.get("flaky"):
                problems.append("%s 不变量 %s flaky" % (fid, inv_id))
    for inv_id, entry in runner_invariants.items():
        if not (isinstance(entry, dict) and entry.get("pass")):
            problems.append("runner 不变量 %s 未通过" % inv_id)
    if global_anomalies:
        problems.append("存在 %d 条 anomaly（不得用 0/空顶替，§6.7）"
                        % len(global_anomalies))
    if strict_findings:
        for finding in _collect_findings(families):
            problems.append("--strict-findings：%s finding：%s"
                            % (finding["family"], finding["note"][:200]))
    return (0 if not problems else 2), problems


def _campaign_digest(families: dict, runner_invariants: dict) -> str:
    """campaign digest：只喂确定性内容（每轮 digest + 不变量布尔），不含时间/路径。"""
    payload = {
        "families": {
            fid: {
                "run_digests": [str((r or {}).get("digest") or "")
                                for r in (fam.get("runs") or [])],
                "invariants": {
                    inv_id: {"pass": bool(entry.get("pass")),
                             "flaky": bool(entry.get("flaky"))}
                    for inv_id, entry in (fam.get("invariants") or {}).items()
                },
            }
            for fid, fam in families.items()
        },
        "runner_invariants": {inv_id: bool(entry.get("pass"))
                              for inv_id, entry in runner_invariants.items()},
    }
    return S.digest_of_metrics(payload)


# ── 产物 ────────────────────────────────────────────────
def write_artifacts(record: dict, out_dir: str) -> dict:
    """写 `<run_id>.json` / `.last.json` / `<date>-report.md`（历史 JSON 不覆盖）。"""
    run_id = record["run_id"]
    json_path = _unique_path(os.path.join(out_dir, run_id + ".json"))
    write_json(json_path, record)
    last_path = os.path.join(out_dir, ".last.json")
    write_json(last_path, record)
    day = str(record.get("started_at") or "")[:10] or now_local().strftime("%Y-%m-%d")
    report_path = os.path.join(out_dir, day + "-report.md")
    write_text(report_path, R.render_report(record))
    return {"json": json_path, "last": last_path, "report": report_path}


# ── CLI ─────────────────────────────────────────────────
class _Parser(argparse.ArgumentParser):
    """把 argparse 的"用法错误"统一成退出码 1（默认是 2，和 v2 判定冲突）。"""

    def error(self, message):
        raise UsageError(message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="run_chaos_campaign.py",
        description="LINGSHU 混沌工程 v2 campaign 执行器（engine 档：确定性、零模型调用）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "退出码：0 = 全部不变量通过、无异常（FINDING 只记录，除非 --strict-findings）；\n"
            "        2 = 有不变量失败/异常，或 --strict-findings 下的 finding；\n"
            "        1 = 用法或语料结构性错误（manifest 缺失 / schema 不符）。\n\n"
            "产物：<out-dir>/<run_id>.json、<out-dir>/.last.json、<out-dir>/<YYYY-MM-DD>-report.md\n"
            "写盘范围：只写 out-dir 下的文件（不建临时目录/子目录），结束不清理产物。\n"
            "INV-19 运行时排除（诚实披露）：paper/host-validation/.runtime/、"
            "s4-long-session/cells/、probe(s)/、.chaos-data*；\n"
            "campaign 自身的写意图另由审计钩子逐调用归因，越界即失败。\n"
            "注意：campaign 运行期间不要编辑仓库文件（INV-19 会把并发编辑当越界改动）。\n\n"
            "示例：\n"
            "  python tools\\run_chaos_campaign.py\n"
            "  python tools\\run_chaos_campaign.py --list\n"
            "  python tools\\run_chaos_campaign.py --family f1_stpa_matrix --json-only\n"
            "  python tools\\run_chaos_campaign.py --timeout-seconds 0 --seed 20261027\n"
        ),
    )
    parser.add_argument("--manifest", default="",
                        help="manifest 路径（默认 chaos-set/v2/manifest.json；语料按 manifest 所在目录解析）")
    parser.add_argument("--out-dir", default="",
                        help="产物目录（默认 acceptance/chaos/v2）")
    parser.add_argument("--family", action="append", default=[], metavar="FAMILY_ID",
                        help="只跑指定族（可重复；其余族不出现在输出里）")
    parser.add_argument("--repeats", type=int, default=None,
                        help="覆盖 manifest.repeats（默认取 manifest，R=3）")
    parser.add_argument("--seed", type=int, default=None,
                        help="覆盖 manifest.seed_base（第 r 次 seed = seed + r）")
    parser.add_argument("--run-id", default="",
                        help="覆盖 run_id（默认 chaos2-<YYYYMMDD>-<HHMMSS>-engine）")
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_S,
                        help="软墙钟预算秒数（默认 90；**<=0 表示不限**，仅供调试："
                             "此时 ctx.timeout_s 传给 F6 的子进程超时也是不限）")
    parser.add_argument("--strict-findings", action="store_true",
                        help="有 FINDING 时也返回退出码 2（CI 用；默认 findings 只记录不门禁）")
    parser.add_argument("--strict-write-scope", action="store_true",
                        help="INV-19 严格模式：仓库快照差分（含其它进程的并发写入）也判失败；"
                             "默认只以**进程内写意图归因**为准，快照差分作为证据披露")
    parser.add_argument("--json-only", "-q", action="store_true",
                        help="只打印一行摘要 JSON（机器消费）")
    parser.add_argument("--list", action="store_true",
                        help="列出族 id/标题/mechanism/ucas 后退出（不跑 campaign）")
    return parser


def list_families(args) -> int:
    """--list：列族清单。manifest 未就绪时回落到 docs/23 §5 的内置设计目录。"""
    manifest_path = os.path.abspath(args.manifest) if args.manifest else DEFAULT_MANIFEST
    manifest = None
    source = ""
    try:
        manifest = C.load_manifest(manifest_path)
        families = manifest.families
        source = "manifest：%s" % _rel_display(manifest_path)
    except C.CorpusError as exc:
        families = C.FALLBACK_FAMILIES
        first = str(exc).splitlines()[0] if str(exc) else "manifest 缺失"
        source = "内置设计目录（%s）" % first
    print("LINGSHU 混沌工程 v2 · 族清单")
    print("  来源：%s" % source)
    if manifest is not None:
        print("  seed_base=%s  repeats=%s  base_ts=%s  语料目录=%s"
              % (manifest.seed_base, manifest.repeats, manifest.base_ts,
                 _rel_display(manifest.dir)))
    else:
        print("  语料目录：chaos-set/v2（manifest 未就绪时，族模块/语料缺失会按族 skipped，不会崩）")
    print("")
    header = ("%-22s %-34s %-12s %-20s %s"
              % ("族 id", "标题", "压力类", "机制靶点", "UCA"))
    print(header)
    print("-" * len(header))
    for fam in families:
        ucas = fam.get("ucas") or []
        print("%-22s %-34s %-12s %-20s %s" % (
            str(fam.get("id") or "—"),
            str(fam.get("title") or "—")[:32],
            str(fam.get("stress_class") or "—"),
            str(fam.get("mechanism") or "—"),
            " ".join(str(u) for u in ucas) if ucas else "（manifest 未声明时留空）"))
    print("")
    print("共 %d 族" % len(families))
    if manifest is None:
        print("提示：这是 docs/23 §5 的设计目录；manifest 落盘后 --list 会显示预注册的真实字段。")
    return 0


def select_families(manifest: C.Manifest, wanted) -> tuple:
    if not wanted:
        return list(manifest.families), None
    known = manifest.family_ids()
    unknown = [w for w in wanted if w not in known]
    if unknown:
        return None, ("未知族 id：%r\n可用族：%s"
                      % (unknown, ", ".join(known)))
    wanted_set = set(wanted)
    return [f for f in manifest.families if str(f["id"]) in wanted_set], None


def _family_repeats(fam: dict, manifest: C.Manifest, override) -> int:
    if override is not None:
        return int(override)
    value = fam.get("repeats", manifest.repeats)
    try:
        return max(1, int(value))
    except (TypeError, ValueError):
        return int(manifest.repeats)


def _print_human(record: dict, paths: dict, exit_code: int, problems: list) -> None:
    summary = record.get("summary") or {}
    budget = record.get("budget") or {}
    print("")
    print("LINGSHU 混沌工程 v2 · campaign 摘要")
    print("  run_id   : %s" % record.get("run_id"))
    print("  模式     : engine（零模型调用；F6 仅 node --no-model）")
    print("  墙钟     : %.2f s / %s s"
          % (float(budget.get("wall_seconds") or 0.0),
             "无限" if not budget.get("timeout_seconds")
             else "%.1f" % float(budget.get("timeout_seconds"))))
    print("  族       : %d 跑 / %d skipped"
          % (summary.get("families_run", 0), summary.get("families_skipped", 0)))
    print("  检查     : cases=%d，failed=%d"
          % (summary.get("checks_total", 0), summary.get("checks_failed", 0)))
    print("  不变量   : %d/%d 通过，flaky=%d（覆盖 %d/%d）"
          % (summary.get("invariants_passed", 0), summary.get("invariants_total", 20),
             summary.get("invariants_flaky", 0), summary.get("invariants_covered", 0),
             summary.get("invariants_total", 20)))
    verdict = str(summary.get("verdict") or ("pass" if exit_code == 0 else "fail"))
    verdict_text = {"pass": "✅ 通过", "pass_with_findings": "✅ 通过（附 product findings）",
                    "fail": "❌ 未通过"}.get(verdict, verdict)
    print("  判定     : %s（exit %d）" % (verdict_text, exit_code))
    if int(summary.get("findings_total") or 0) > 0:
        print("  发现     : %d 条产品缺陷 FINDING（不计入 v2 不变量判定；"
              "见 acceptance/chaos/v2/findings.md，必须单独修）"
              % int(summary["findings_total"]))
    print("  JSON     : %s" % _rel_display(paths.get("json") or ""))
    print("  报告     : %s" % _rel_display(paths.get("report") or ""))
    if exit_code != 0:
        if problems:
            print("\n未通过原因：", file=sys.stderr)
            for item in problems[:15]:
                print("  ✗ %s" % item, file=sys.stderr)
            if len(problems) > 15:
                print("  …另有 %d 项（详见 JSON/anomalies）" % (len(problems) - 15),
                      file=sys.stderr)
        anomalies = record.get("anomalies") or []
        if anomalies:
            print("\n异常清单（前 10 条）：", file=sys.stderr)
            for item in anomalies[:10]:
                print("  ⚠️ [%s] %s" % (item.get("family"), item.get("detail")),
                      file=sys.stderr)
            if len(anomalies) > 10:
                print("  …另有 %d 条" % (len(anomalies) - 10), file=sys.stderr)


def _print_json_line(record: dict, paths: dict, exit_code: int) -> None:
    summary = record.get("summary") or {}
    budget = record.get("budget") or {}
    line = {
        "schema": SCHEMA,
        "run_id": record.get("run_id"),
        "exit_code": int(exit_code),
        "verdict": (record.get("summary") or {}).get("verdict")
        or ("pass" if exit_code == 0 else "fail"),
        "families_run": summary.get("families_run", 0),
        "families_skipped": summary.get("families_skipped", 0),
        "invariants_total": summary.get("invariants_total", 20),
        "invariants_passed": summary.get("invariants_passed", 0),
        "invariants_flaky": summary.get("invariants_flaky", 0),
        "invariants_covered": summary.get("invariants_covered", 0),
        "checks_total": summary.get("checks_total", 0),
        "checks_failed": summary.get("checks_failed", 0),
        "anomalies_total": summary.get("anomalies_total", 0),
        "findings_total": summary.get("findings_total", 0),
        "wall_seconds": budget.get("wall_seconds", 0.0),
        "digest": summary.get("digest", ""),
        "artifacts": {"json": _rel_display(paths.get("json") or ""),
                      "last": _rel_display(paths.get("last") or ""),
                      "report": _rel_display(paths.get("report") or "")},
    }
    print(json.dumps(line, ensure_ascii=False, separators=(",", ":")))


# ── 主流程 ──────────────────────────────────────────────
def run_campaign(manifest: C.Manifest, selected: list, corpus_map: dict,
                 repeats_override, seed_base: int, timeout_s: float, run_id: str,
                 out_dir: str, json_only: bool, strict_findings: bool = False,
                 strict_write_scope: bool = False) -> int:
    """跑一次完整 campaign，写产物并返回退出码。"""
    started = now_local()
    global_anomalies = []
    budget = _WallBudget(timeout_s)
    probe = I.AuditProbe().install().start()
    # INV-19：允许写盘范围只有 out-dir（环境约束下 Python 不能新建目录，v2 不建临时目录）
    exclude_paths = [out_dir]
    families = {}
    try:
        snapshot_before = I.snapshot_tree(ROOT, exclude_paths)
        hashes_before = {os.path.basename(fc.path): fc.actual_sha256
                         for fc in corpus_map.values()}
        expected_hashes = {}
        for fc in corpus_map.values():
            name = os.path.basename(fc.path)
            if name in manifest.corpus_sha256:
                expected_hashes[name] = str(manifest.corpus_sha256[name])

        total = len(selected)
        for idx, fam in enumerate(selected, 1):
            fid = str(fam["id"])
            fam_repeats = _family_repeats(fam, manifest, repeats_override)
            record = _run_family(fam, corpus_map.get(fid), fam_repeats, seed_base,
                                 manifest.base_ts, budget, run_id,
                                 global_anomalies, probe)
            families[fid] = record
            if not json_only:
                checks = record.get("checks") or {}
                invs = record.get("invariants") or {}
                passed_inv = len([1 for v in invs.values() if v.get("pass")])
                status = "skipped(%s)" % record.get("skip_reason") if record.get("skipped") else \
                         ("OK" if int(checks.get("failed") or 0) == 0 else "FAIL")
                print("  [%2d/%2d] %-22s R=%d cases=%-4d failed=%-3d 不变量 %2d/%-2d %s"
                      % (idx, total, fid, len(record.get("runs") or []),
                         int(checks.get("cases") or 0), int(checks.get("failed") or 0),
                         passed_inv, len(invs), status))

        probe.stop()
        snapshot_after = I.snapshot_tree(ROOT, exclude_paths)
        hashes_after = {}
        for fc in corpus_map.values():
            name = os.path.basename(fc.path)
            hashes_after[name] = C.sha256_file(fc.path) if os.path.exists(fc.path) else ""
        runner_invariants = {
            "INV-02": I.check_zero_model_calls(probe, MODE),
            "INV-18": I.check_corpus_frozen(expected_hashes, hashes_before, hashes_after),
            "INV-19": I.check_write_scope(
                I.diff_snapshot(snapshot_before, snapshot_after),
                exclude_paths,
                write_opens=probe.write_ops,
                excluded_prefixes=I.runtime_excluded_prefixes(),
                strict_snapshot=bool(strict_write_scope)),
        }
        runner_invariants["INV-19"]["evidence"]["fd_opens"] = int(probe.fd_opens)
    finally:
        probe.stop()

    finished = now_local()
    wall_seconds = round(budget.elapsed(), 3)
    summary = _summarize(families, runner_invariants, manifest, selected, global_anomalies)
    summary["digest"] = _campaign_digest(families, runner_invariants)
    exit_code, problems = _decide_exit(families, runner_invariants, global_anomalies,
                                       strict_findings=strict_findings)
    findings_total = int(summary.get("findings_total") or 0)
    if exit_code != 0:
        verdict = "fail"
    elif findings_total > 0:
        verdict = "pass_with_findings"
    else:
        verdict = "pass"
    summary["verdict"] = verdict
    summary["strict_findings"] = bool(strict_findings)
    summary["strict_write_scope"] = bool(strict_write_scope)
    summary["exit_code"] = exit_code

    record = {
        "schema": SCHEMA,
        "run_id": run_id,
        "started_at": iso(started),
        "finished_at": iso(finished),
        "mode": MODE,
        "manifest_sha256": manifest.sha256,
        "seed_base": int(seed_base),
        "repeats": int(repeats_override) if repeats_override is not None
        else int(manifest.repeats),
        "families": families,
        "summary": summary,
        "anomalies": global_anomalies,
        "runner_invariants": runner_invariants,
        "invariants_catalog": [I.describe(inv_id) for inv_id in I.INVARIANT_IDS],
        "budget": {
            "timeout_seconds": None if timeout_s <= 0 else float(timeout_s),
            "wall_seconds": wall_seconds,
            "model_calls": 0,
        },
        "corpus": {
            "manifest": _rel_display(manifest.path),
            "families": {fid: fc.as_record() for fid, fc in corpus_map.items()},
        },
        "out_dir": _rel_display(out_dir),
        "manifest_family_count": len(manifest.families),
        "options": {
            "families": [str(f["id"]) for f in selected],
            "repeats_override": repeats_override,
            "seed_override": seed_base if seed_base != manifest.seed_base else None,
            "strict_findings": bool(strict_findings),
            "strict_write_scope": bool(strict_write_scope),
        },
        "exit_code": exit_code,
    }
    try:
        paths = write_artifacts(record, out_dir)
    except OSError as exc:
        print("产物写入失败（退出码 1）：%s" % exc, file=sys.stderr)
        return 1

    if json_only:
        _print_json_line(record, paths, exit_code)
    else:
        _print_human(record, paths, exit_code, problems)
    return exit_code


def main(argv=None) -> int:
    try:
        args = build_parser().parse_args(argv)
    except UsageError as exc:
        print("用法错误：%s" % exc, file=sys.stderr)
        print("（--help 查看用法）", file=sys.stderr)
        return 1

    if args.list:
        return list_families(args)

    # ── manifest：缺失 / schema 不符 → 退出码 1（不写产物、不跑族）──
    manifest_path = os.path.abspath(args.manifest) if args.manifest else DEFAULT_MANIFEST
    try:
        manifest = C.load_manifest(manifest_path)
    except C.CorpusError as exc:
        print("语料结构性错误（退出码 1）：\n%s" % exc, file=sys.stderr)
        return 1

    selected, error = select_families(manifest, args.family)
    if error:
        print("用法错误（退出码 1）：%s" % error, file=sys.stderr)
        return 1

    if args.repeats is not None and int(args.repeats) < 1:
        print("用法错误（退出码 1）：--repeats 必须 >= 1（实际 %r）" % args.repeats,
              file=sys.stderr)
        return 1
    repeats = args.repeats if args.repeats is not None else int(manifest.repeats)
    seed_base = args.seed if args.seed is not None else int(manifest.seed_base)
    timeout_s = float(args.timeout_seconds) if args.timeout_seconds is not None \
        else DEFAULT_TIMEOUT_S
    out_dir = os.path.abspath(args.out_dir) if args.out_dir else DEFAULT_OUT_DIR
    run_id = str(args.run_id).strip() or ("chaos2-%s-engine"
                                          % now_local().strftime("%Y%m%d-%H%M%S"))

    try:
        corpus_map = C.load_corpus(manifest, [str(f["id"]) for f in selected])
    except C.CorpusError as exc:
        print("语料结构性错误（退出码 1）：\n%s" % exc, file=sys.stderr)
        return 1

    try:
        os.makedirs(out_dir, exist_ok=True)
    except OSError as exc:
        print("产物目录不可写（退出码 1）：%s（%s）" % (out_dir, exc), file=sys.stderr)
        return 1

    if not args.json_only:
        print("LINGSHU 混沌工程 v2 · campaign（engine 档，零模型调用）")
        print("  manifest : %s" % _rel_display(manifest.path))
        print("  seed     : %d（R=%d → %s）" % (seed_base, repeats,
                                               ", ".join(str(seed_base + r) for r in range(repeats))))
        print("  预算     : %.1f s ｜ 产物：%s" % (timeout_s, _rel_display(out_dir)))
        print("")

    try:
        exit_code = run_campaign(manifest, selected, corpus_map, args.repeats, seed_base,
                                 timeout_s, run_id, out_dir, bool(args.json_only),
                                 strict_findings=bool(args.strict_findings),
                                 strict_write_scope=bool(args.strict_write_scope))
    except KeyboardInterrupt:
        print("\n已中断（未写完的产物不会被视为有效 campaign）", file=sys.stderr)
        return 1
    except Exception:  # noqa: BLE001 - 内部错误不吐栈到用户面前，但要 loud
        print("内部错误（退出码 2）：%s" % _tb_tail(), file=sys.stderr)
        return 2
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
