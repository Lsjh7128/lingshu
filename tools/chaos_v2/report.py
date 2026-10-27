# -*- coding: utf-8 -*-
"""tools/chaos_v2/report.py —— v2 中文 markdown 报告（docs/23 §10 产物）

报告是**人读**的，JSON 是**机读**的：两者字段一一对应，报告不再做第二次判定。
结构（与 docs/23 §9 诚实边界一一对应）：

    0. 一句话结论
    1. 运行元信息
    2. 总览
    3. 族表（cases / failed / invariants）
    4. 不变量矩阵（INV-01…INV-20 × 族 + runner）
    5. 比率与 95% Wilson 区间（只画像，不做门槛）
    6. UCA 与机制覆盖率
    7. 失败清单
    8. 异常清单
    9. 诚实边界（引用 docs/23 §9，不是装饰）
   10. 复现命令
    附录：指纹 / 预算 / digest

写作纪律：所有"没跑成/没观测到"都显式写 skipped 与原因（红线 3）；
报告里不出现"通过"以外的结论词去暗示 measured 证据（§0 / §8.5）。
"""
from __future__ import annotations

import os

from . import invariants as I

_CAP_FAILURES = 200


def _fmt_rate(entry) -> str:
    if not isinstance(entry, dict):
        return "—"
    rate = entry.get("rate")
    if rate is None:
        return "—（den=0）"
    lo, hi = entry.get("ci_low"), entry.get("ci_high")
    return "%.3f（95%% CI %.3f–%.3f）" % (float(rate), float(lo or 0.0), float(hi or 0.0))


def _cell(entry) -> str:
    if not isinstance(entry, dict):
        return "—"
    if entry.get("flaky"):
        return "⚠️"
    return "✅" if entry.get("pass") else "❌"


def _yes_no(value) -> str:
    return "✅ 通过" if value else "❌ 未通过"


def _short_family_id(fid: str) -> str:
    """矩阵表头缩写：f1_stpa_matrix → f1；若缩写撞车则退回完整 id（避免表头都叫 probe）。"""
    text = str(fid)
    idx = text.find("_")
    return text if idx <= 0 else text[:idx]


def _family_short_names(family_ids) -> dict:
    ids = [str(x) for x in family_ids]
    short = {fid: _short_family_id(fid) for fid in ids}
    if len(set(short.values())) != len(short):
        return {fid: fid for fid in ids}
    return short


def _rel(path: str) -> str:
    """报告里一律用仓库内相对路径（可读 + 不含用户机器绝对路径）。"""
    text = str(path or "")
    if not text:
        return "—"
    cwd = os.getcwd()
    for base in (cwd, os.path.dirname(cwd)):
        try:
            rel = os.path.relpath(os.path.abspath(text), os.path.abspath(base))
        except (OSError, ValueError):
            continue
        if not rel.startswith(".."):
            return rel.replace("\\", "/")
    return os.path.basename(text)


def _severity_text(severities) -> str:
    if not isinstance(severities, dict) or not severities:
        return "—"
    order = ("control", "mild", "moderate", "severe")
    parts = []
    for key in order:
        if key in severities:
            parts.append("%s=%d" % (key, int(severities[key])))
    for key in sorted(severities):
        if key not in order:
            parts.append("%s=%d" % (key, int(severities[key])))
    return " ".join(parts)


def _verdict_of(record: dict) -> bool:
    """`pass` 与 `pass_with_findings` 都算"不变量通过"（finding 不门禁）。"""
    summary = record.get("summary") or {}
    verdict = summary.get("verdict")
    if isinstance(verdict, str):
        return verdict.lower() in ("pass", "pass_with_findings")
    if isinstance(record.get("exit_code"), int):
        return int(record["exit_code"]) == 0
    return bool(summary.get("invariants_passed") == summary.get("invariants_total")
                and not summary.get("checks_failed"))


def _verdict_text(record: dict) -> str:
    summary = record.get("summary") or {}
    verdict = str(summary.get("verdict") or "").lower()
    if verdict == "pass_with_findings":
        return "**通过（附 %d 条 FINDING）**" % int(summary.get("findings_total") or 0)
    if verdict == "pass":
        return "**通过**"
    if verdict == "fail":
        return "**未通过**"
    return "**通过**" if _verdict_of(record) else "**未通过**"


def render_report(record: dict, manifest=None) -> str:
    """把一次 campaign 的 JSON 记录渲染成中文 markdown。"""
    record = record or {}
    fams = record.get("families") or {}
    summary = record.get("summary") or {}
    runner_inv = record.get("runner_invariants") or {}
    anomalies = list(record.get("anomalies") or [])
    passed = _verdict_of(record)
    run_id = record.get("run_id") or "（unknown）"
    lines = []

    # ── 0. 标题 / 一句话结论 ──
    lines += [
        "# LINGSHU 混沌工程 v2 压测报告",
        "",
        "> run_id：`%s` ｜ 模式：**%s**（零模型调用） ｜ 生成：%s"
        % (run_id, record.get("mode") or "engine", record.get("finished_at") or "—"),
        "> 判定：%s ｜ 不变量 %s/%s 通过 ｜ flaky=%s ｜ finding=%s ｜ 异常 %d 条"
        % (_verdict_text(record),
           summary.get("invariants_passed", 0), summary.get("invariants_total", 20),
           summary.get("invariants_flaky", 0), summary.get("findings_total", 0),
           len(anomalies)),
        "",
    ]
    findings = summary.get("findings") or []
    if findings:
        lines += [
            "> ⚠️ **发现 %d 条产品缺陷（FINDING-…）**：见 `acceptance/chaos/v2/findings.md`；"
            "它们**不计入 v2 不变量判定**（默认不改变退出码），但**必须单独任务修**，"
            "不许顺手改绿（docs/23 §8.8）。" % len(findings),
            "",
        ]
    lines += [
        "## 0. 一句话结论",
        "",
    ]
    if passed:
        lines.append("- %d 族 / %d 次重复全部跑完，**不变量全部成立**、无失败项、无异常；"
                     "同 seed 复跑逐字节确定，语料指纹冻结。"
                     % (summary.get("families_run", 0), record.get("repeats", 0)))
        lines.append("- 这**只说明机制在预注册刺激集下守约**；不说明阈值（0.70/0.85）正确，"
                     "也**不构成任何 measured 证据**（docs/23 §0 / §9）。")
    else:
        reasons = []
        if summary.get("families_skipped"):
            reasons.append("%d 族 skipped" % int(summary["families_skipped"]))
        if summary.get("invariants_flaky"):
            reasons.append("%d 条不变量 flaky" % int(summary["invariants_flaky"]))
        if summary.get("checks_failed"):
            reasons.append("%d 条检查失败" % int(summary["checks_failed"]))
        if anomalies:
            reasons.append("%d 条异常" % len(anomalies))
        lines.append("- **未通过**：%s。失败/跳过项在 §3/§7/§8 逐条列出，"
                     "不得用带异常的运行做任何标定或结论。" % ("；".join(reasons) or "存在未通过项"))
        lines.append("- 退出码 2 = 有不变量失败 / 异常（docs/23 §6.6 + Lead 修订）；"
                     "按红线 8/9，先修问题或补语料，不许改判定凑绿。")
    if findings:
        lines.append("- 另有 **%d 条产品缺陷 FINDING**：已记录在 `summary.findings` 与 "
                     "`acceptance/chaos/v2/findings.md`，**不参与不变量判定**（除非 "
                     "`--strict-findings`），但必须单独任务修（§8.8）。" % len(findings))
    lines += [""]

    # ── 1. 运行元信息 ──
    corpus = record.get("corpus") or {}
    budget = record.get("budget") or {}
    lines += [
        "## 1. 运行元信息",
        "",
        "| 项 | 值 |",
        "| --- | --- |",
        "| run_id | `%s` |" % run_id,
        "| 开始 / 结束 | %s → %s |" % (record.get("started_at") or "—",
                                      record.get("finished_at") or "—"),
        "| 模式 | `%s`（engine 档；零模型调用，F6 只起 node 纯计算） |" % (record.get("mode") or "engine"),
        "| seed_base / repeats | %s / %s |" % (record.get("seed_base"), record.get("repeats")),
        "| manifest SHA-256 | `%s` |" % (record.get("manifest_sha256") or "—"),
        "| 语料 manifest | `%s` |" % _rel(corpus.get("manifest") or ""),
        "| 墙钟 / 预算 | %.2f s / %s s |" % (
            float(budget.get("wall_seconds") or 0.0),
            "无限" if not budget.get("timeout_seconds") else
            ("%g" % float(budget.get("timeout_seconds")))),
        "| 产物目录 | `%s` |" % _rel(record.get("out_dir") or ""),
        "",
    ]

    # ── 2. 总览 ──
    lines += [
        "## 2. 总览",
        "",
        "| 指标 | 值 |",
        "| --- | --- |",
        "| 跑过的族 / 跳过 | %s / %s |" % (summary.get("families_run", 0),
                                          summary.get("families_skipped", 0)),
        "| 不变量（通过 / 总数 / flaky） | %s / %s / %s |"
        % (summary.get("invariants_passed", 0), summary.get("invariants_total", 20),
           summary.get("invariants_flaky", 0)),
        "| 不变量被覆盖数（本 campaign） | %s |" % summary.get("invariants_covered", "—"),
        "| 检查（cases / failed） | %s / %s |" % (summary.get("checks_total", 0),
                                                  summary.get("checks_failed", 0)),
        "| 失败明细条数 | %s |" % summary.get("failures_total", 0),
        "| finding 条数 | %s |" % summary.get("findings_total", 0),
        "| 异常条数 | %s |" % summary.get("anomalies_total", len(anomalies)),
        "| campaign digest | `%s` |" % (summary.get("digest") or "—"),
        "",
    ]

    # ── 3. 族表 ──
    lines += [
        "## 3. 族表（cases / failed / invariants）",
        "",
        "| 族 | 标题 | 压力类 | severity 分布 | cases | failed | 不变量（通过/声明） | 判定 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for fid, fam in fams.items():
        fam = fam or {}
        invs = fam.get("invariants") or {}
        total_inv = len(invs)
        passed_inv = len([1 for v in invs.values() if isinstance(v, dict) and v.get("pass")])
        if fam.get("skipped"):
            verdict = "**skipped**（`%s`）" % (fam.get("skip_reason") or "unspecified")
        elif total_inv == passed_inv and int((fam.get("checks") or {}).get("failed") or 0) == 0:
            verdict = "✅ 通过"
        else:
            verdict = "❌ 未通过"
        lines.append("| `%s` | %s | %s | %s | %s | %s | %s/%s | %s |" % (
            fid, fam.get("title") or "—", fam.get("stress_class") or "—",
            _severity_text(fam.get("severities")),
            (fam.get("checks") or {}).get("cases", 0),
            (fam.get("checks") or {}).get("failed", 0),
            passed_inv, total_inv, verdict))
    if not fams:
        lines.append("| — | （本 campaign 未包含任何族） | — | — | — | — | — | — |")
    lines.append("")
    # 每族 notes（含 F6 诚实边界之类）单列，避免表格挤爆
    note_lines = []
    for fid, fam in fams.items():
        for note in (fam or {}).get("notes") or []:
            note_lines.append("- `%s`：%s" % (fid, note))
    if note_lines:
        lines += ["族模块说明（notes，不影响判定）：", ""] + note_lines + [""]

    # ── 4. 不变量矩阵 ──
    lines += [
        "## 4. 不变量矩阵",
        "",
        "✅ 通过 ｜ ⚠️ flaky（偶发失败，同样算不通过） ｜ ❌ 失败 ｜ — 未被本 campaign 覆盖/未声明。",
        "按 docs/23 §4：**缺失 = schema 违规 = 失败**；按 §6.4：R 次里出现 false 就是 false。",
        "",
    ]
    family_ids = list(fams)
    short = _family_short_names(family_ids)
    header = ["不变量", "说明"] + [short[fid] for fid in family_ids] + ["runner"]
    lines.append("| " + " | ".join(header) + " |")
    lines.append("| " + " | ".join(["---"] * len(header)) + " |")
    all_ids = []
    for inv_id in I.INVARIANT_IDS:
        if inv_id in runner_inv or any(inv_id in ((fams.get(fid) or {}).get("invariants") or {})
                                       for fid in family_ids):
            all_ids.append(inv_id)
    for inv_id in all_ids:
        meta = I.describe(inv_id)
        cells = []
        for fid in family_ids:
            fam = fams.get(fid) or {}
            entry = (fam.get("invariants") or {}).get(inv_id)
            cells.append(_cell(entry))
        cells.append(_cell(runner_inv.get(inv_id)) if inv_id in runner_inv else "—")
        lines.append("| `%s` | %s | %s |" % (inv_id, meta.get("title") or "—",
                                            " | ".join(cells)))
    uncovered = [inv_id for inv_id in I.INVARIANT_IDS if inv_id not in all_ids]
    lines.append("")
    if uncovered:
        lines.append("未覆盖的不变量：%s（本次 `--family` 过滤或跳过所致；"
                     "全量 campaign 应覆盖全部 20 条）。" % ", ".join(uncovered))
        lines.append("")
    inv19 = runner_inv.get("INV-19") or {}
    excluded_prefixes = inv19.get("excluded_runtime_prefixes") or []
    if excluded_prefixes:
        lines += [
            "**INV-19 运行时排除（诚实披露，不计入 campaign 写盘）**：%s。"
            % "、".join("`%s`" % p for p in excluded_prefixes),
            "排除的是 DSH 宿主会话 / 并行 host 验证 / 混沌隔离数据这些**别的进程**会写的目录；"
            "campaign 自身的写意图另由审计钩子逐调用归因（JSON `runner_invariants.INV-19.evidence`），"
            "越出 out-dir 即失败——**排除 ≠ 放宽红线**。",
            "另注意：campaign 运行期间不要编辑仓库文件（并发编辑会被 INV-19 当作越界改动）。",
            "",
        ]

    # ── 5. 比率与置信区间 ──
    lines += [
        "## 5. 比率与 95% Wilson 区间（画像，不做门槛）",
        "",
        "按 docs/23 §6.3：所有比率给 `{num, den, rate, ci_low, ci_high}`，小样本用 Wilson 而非正态近似；",
        "按 §6.5：`rate` 类判定**不给「必须达标」**，只有 `invariant` 能让 campaign 失败。",
        "",
        "| 族 | 指标 | num/den | rate（95% CI） |",
        "| --- | --- | --- | --- |",
    ]
    rate_rows = 0
    for fid, fam in fams.items():
        for name, entry in sorted(((fam or {}).get("rates") or {}).items()):
            rate_rows += 1
            lines.append("| `%s` | %s | %s/%s | %s |" % (
                fid, name, entry.get("num", 0), entry.get("den", 0), _fmt_rate(entry)))
    if not rate_rows:
        lines.append("| — | （无比率型指标） | — | — |")
    lines += [
        "",
        "> Wilson 区间宽 = 样本少，**不是**缺陷；真正的缺陷是把小样本点估计当成定论。",
        "",
    ]

    # ── 6. 覆盖矩阵 ──
    coverage = summary.get("coverage") or {}
    lines += [
        "## 6. UCA 与机制覆盖矩阵",
        "",
        "### 6.1 UCA 覆盖（manifest 的预注册归属）",
        "",
        "| UCA | 覆盖族 | 备注 |",
        "| --- | --- | --- |",
    ]
    if coverage:
        for uca in sorted(coverage):
            where = coverage.get(uca) or []
            lines.append("| `%s` | %s | %s |" % (
                uca, "、".join("`%s`" % x for x in where) or "—",
                "已覆盖" if where else "**未覆盖**（§3 要求每 UCA ≥1 正样本 + 1 对照）"))
    else:
        lines.append("| — | — | manifest 未声明 UCA 归属 |")
    lines += ["", "### 6.2 机制覆盖（docs/19 §1 的机制靶点）", "",
              "| 机制 | 压力类 | 覆盖族 |", "| --- | --- | --- |"]
    mechs = {}
    for fid, fam in fams.items():
        mech = str((fam or {}).get("mechanism") or "—")
        mechs.setdefault(mech, []).append((fid, str((fam or {}).get("stress_class") or "—")))
    for mech in sorted(mechs):
        entries = mechs[mech]
        stress = "/".join(sorted({s for _, s in entries}))
        lines.append("| `%s` | %s | %s |" % (
            mech, stress, "、".join("`%s`" % f for f, _ in entries)))
    lines += [""]

    # ── 7. 失败清单 ──
    lines += ["## 7. 失败清单", ""]
    failures = []
    for fid, fam in fams.items():
        for item in ((fam or {}).get("checks") or {}).get("failures") or []:
            failures.append((fid, item))
    if failures:
        lines.append("共 %d 条（表格最多列 %d 条；完整清单见 JSON 的 `families.*.checks.failures`）。"
                     % (len(failures), _CAP_FAILURES))
        lines += ["", "| 族 | 轮次 | case | 期望 want | 实际 got | 说明 |",
                  "| --- | --- | --- | --- | --- | --- |"]
        for fid, item in failures[:_CAP_FAILURES]:
            lines.append("| `%s` | r%s | `%s` | `%s` | `%s` | %s |" % (
                fid, item.get("repeat", "?"), item.get("case", ""),
                str(item.get("want", ""))[:160], str(item.get("got", ""))[:160],
                str(item.get("note", ""))[:240]))
        lines.append("")
    else:
        lines += ["- 无失败项。", ""]

    # ── 8. 异常清单 ──
    lines += ["## 8. 异常清单（skipped / 导入失败 / 超预算 / 指纹不符）", ""]
    if anomalies:
        for item in anomalies:
            if isinstance(item, dict):
                fid = item.get("family") or "runner"
                lines.append("- `%s`：%s" % (fid, item.get("detail") or item))
            else:
                lines.append("- %s" % item)
    else:
        lines.append("- 无异常。")
    lines += [""]

    # ── 8.5 Finding 清单 ──
    findings = summary.get("findings") or []
    lines += ["## 8.5 Finding 清单（machine tag：note 含 `FINDING-…` 或 `（finding）`）", ""]
    if findings:
        lines.append("按 docs/23 §8.8：finding 写进 `acceptance/chaos/v2/findings.md`，"
                     "**单独任务修，不许顺手改代码**；默认**不计入 v2 不变量判定、不改变退出码**"
                     "（`--strict-findings` 时才让 exit=2）。")
        lines.append("")
        for item in findings:
            lines.append("- `%s`：%s" % (item.get("family"), item.get("note")))
    else:
        lines.append("- 无 finding。")
    lines += [""]

    # ── 9. 诚实边界 ──
    lines += [
        "## 9. 诚实边界（docs/23 §9，必读）",
        "",
        "**不覆盖清单**：",
        "",
        "- **真实模型行为**：v2 无模型调用，`keyword_hit` 类语义能力一律不测（v1 host 档才测）。",
        "- **真实冲突/退化发生率**：合成刺激集不构成发生率证据（`docs/20` §1.2）。",
        "- **OS/磁盘级故障**：F6 是函数级故障注入，不是坏道/断电。",
        "- **多进程并发写**：F6 只测单进程内的批内/批间语义。",
        "- **阈值正确性**：v2 只证明「检测器在阈值附近按设计动作」，不证明 0.70/0.85 是对的。",
        "- **性能/延迟**：不测 P95 延迟（`rules/degradation-matrix.yaml#latencyBudget` 另案）。",
        "",
        "**判定分层（§6.5）**：`invariant` 必须 100%；`rate` 只给 CI；`boundary` 只记落点；"
        "`recovery` 只给轮次。**只有 invariant 能让 campaign 失败。**",
        "",
        "**红线提醒（§8）**：",
        "",
        "- 不把合成压力当真实证据：不升 measured、不计入 `docs/20` 门槛、不改任何判定。",
        "- 不改判定来凑通过；语料/判定/门槛三者任一变更都要走评审并更新指纹。",
        "- 发现 bug 写进 `acceptance/chaos/v2/findings.md`，单独任务修，不许「顺手改绿」。",
        "- 不改 v1：`docs/22`、`chaos-set/*.jsonl`、`tools/run_chaos.py` 逐字节保留。",
        "",
    ]

    # ── 10. 复现命令 ──
    py = os.environ.get("LINGSHU_PYTHON", "python")
    lines += [
        "## 10. 复现命令（PowerShell，仓库根目录）",
        "",
        "```powershell",
        '$py = "%s"' % py,
        "",
        "# 1) 语料 = 生成物：逐字节一致（被手改则非 0 退出）",
        "$py tools\\gen_chaos_v2.py --check",
        "",
        "# 2) 行封套 / 唯一性 / 严重度 / 哈希",
        "$py chaos-set\\v2\\validate.py",
        "",
        "# 3) 本报告对应的全量 campaign",
        "%s" % _repro_command(record),
        "",
        "# 4) 验收（不变量 + 统计 + 确定性 + 反例）",
        "$py tests\\test_chaos_campaign.py",
        "```",
        "",
        "> 同 seed 复跑得到**同一 digest**（INV-01）；跨 seed 只要求不变量不变（INV-20），"
        "指标允许浮动。",
        "",
    ]

    # ── 附录 ──
    lines += [
        "## 附录 A. 语料指纹与 digest",
        "",
        "| 族 | 语料 | 行数 | SHA-256（前 16 位） | 校验 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for fid, fam in (corpus.get("families") or {}).items():
        lines.append("| `%s` | `%s` | %s | `%s` | %s |" % (
            fid, fam.get("file") or "—", fam.get("rows", 0),
            str(fam.get("sha256") or "")[:16],
            "✅ 一致" if fam.get("hash_verified") else "❌ %s" % (fam.get("status") or "mismatch")))
    lines += [
        "",
        "| 族 | 每轮 digest（r0…r{R-1}） |",
        "| --- | --- |",
    ]
    for fid, fam in fams.items():
        digests = [str((r or {}).get("digest") or "—")[:16] for r in (fam or {}).get("runs") or []]
        lines.append("| `%s` | %s |" % (fid, "、".join("`%s`" % d for d in digests) or "—"))
    lines += [
        "",
        "- campaign digest：`%s`" % (summary.get("digest") or "—"),
        "- 预算：墙钟 %.2f s / %s s；模型调用 0 次（engine 零模型，§7）"
        % (float(budget.get("wall_seconds") or 0.0),
           "无限" if not budget.get("timeout_seconds") else "%g" % float(budget.get("timeout_seconds"))),
        "",
    ]
    return "\n".join(lines).rstrip() + "\n"


def _repro_command(record: dict) -> str:
    parts = ["$py tools\\run_chaos_campaign.py"]
    seed = record.get("seed_base")
    repeats = record.get("repeats")
    if seed is not None:
        parts.append("--seed %s" % seed)
    if repeats is not None:
        parts.append("--repeats %s" % repeats)
    fams = list((record.get("families") or {}).keys())
    manifest_families = record.get("manifest_family_count")
    if isinstance(manifest_families, int) and len(fams) < manifest_families:
        for fid in fams:
            parts.append("--family %s" % fid)
    return " ".join(parts)


__all__ = ["render_report"]
