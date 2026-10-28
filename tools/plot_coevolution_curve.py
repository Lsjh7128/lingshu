# -*- coding: utf-8 -*-
"""Phase B · A2 论文核心图：攻防收敛曲线（Evolution Convergence Curve）

契约：`experiments/phase-b/CONTRACT.md` §7。数据来源唯一，只有以下两处：

1. `experiments/phase-b/runs/<run_id>/coevolution_log.jsonl`（每代一行，字段名见契约 §6.3）
   - 红线  `intercept_rate_baseline_zeroday`  ：未演化（基线）防线在 held-out 变异集上的拦截率
   - 蓝线  `intercept_rate_evolved_zeroday`   ：演化防线在同一 held-out 变异集上的拦截率
   - 橙线  `benign_false_positive_rate`       ：演化防线在良性语料上的误伤率
   - X 轴  `generation_id`；patch 数 `patches_total`
2. `experiments/phase-b/runs/<run_id>/coevolution_evidence.json`（§6.4）
   - 灰虚线 `plain_attack_baseline_intercept_rate`：基线防线在**明文攻击集**上的拦截率
     （"本来能拦"的参照水平；常量参照，不是逐代测量）。
     该字段缺失时用契约 §3 的唯一仪器 `tools/phase_b_defense.evaluate(seeds, None)` 现算，
     并在 `figure_data.json` 的 `source` 里写明来源（不冒充 evidence 字段）。

本机**没有 matplotlib / reportlab**，所以：
- PDF 走本文件内的最小矢量写入器（`%PDF-1.4` + 对象表 + xref + 每页 content stream：
  `BT /F1 8 Tf 1 0 0 1 x y Tm (...) Tj ET` 文本、`m/l/c/S` 折线、`re` 矩形、坐标轴、刻度、
  图例、标题、脚注）；只用 base-14 Helvetica，**图内文字一律 ASCII/英文**
  （PDF 里中文字体不可靠），中文只出现在 `figure_data.json` 的 `caption_zh`。
- PNG 走 Pillow（300 dpi）。两个后端消费**同一份绘制指令表**（`build_ops`），
  所以 PDF 与 PNG 上的数据点、文字、颜色逐条同源，不存在"两张图不一样"。

诚实边界（契约 §8）：held-out 集是同一批种子的 held-out split，**不是**分布外数据；
变异是确定性规则变异，不是 LLM 红队；`api_tokens_used` 恒 0，token 预算停止条件未获检验。
这些写进 `figure_data.json` 的 `limitations`。

CLI：
    python tools/plot_coevolution_curve.py \
        --run experiments/phase-b/runs/<run_id> \
        --out experiments/phase-b/figures [--inset] [--dpi 300]

缺数据 → 退出码 2，并打印缺的是哪个字段（不许画一张空图交差）。
"""
from __future__ import annotations

import argparse
import io
import json
import math
import os
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

# ── 常量 ────────────────────────────────────────────────────────────────────
SCHEMA = "phase-b-evolution-curve-v1"
LOG_NAME = "coevolution_log.jsonl"
EVIDENCE_NAME = "coevolution_evidence.json"
DEFAULT_PDF = "evolution_curve.pdf"
DEFAULT_PNG = "evolution_curve.png"
DEFAULT_DATA = "figure_data.json"
DEFAULT_DPI = 300

#: 图里必须出现的四条线（id -> (契约 §6.3/§6.4 字段名, 来源文件名)）
LINEFIELDS = {
    "baseline_zeroday": ("intercept_rate_baseline_zeroday", LOG_NAME),
    "evolved_zeroday": ("intercept_rate_evolved_zeroday", LOG_NAME),
    "benign_false_positive": ("benign_false_positive_rate", LOG_NAME),
    "plain_attack_baseline": ("plain_attack_baseline_intercept_rate", EVIDENCE_NAME),
}

#: `build_series()` 必须能从每一代记录里读到的字段（缺一即 exit 2）
REQUIRED_FIELDS = (
    "generation_id",
    "intercept_rate_baseline_zeroday",
    "intercept_rate_evolved_zeroday",
    "benign_false_positive_rate",
)

#: 只进 `figure_data.json`、不画线的上下文序列（字段名同样以契约 §6.3 为准）
CONTEXT_FIELDS = (
    "intercept_rate_evolved",
    "bypass_success_count",
    "red_team_variants_count",
    "patches_total",
    "blue_team_patch_generated",
)

Y_TICKS = ((0.0, "0.0"), (0.25, "0.25"), (0.5, "0.5"), (0.75, "0.75"), (1.0, "1.0"))

# ── 版面（单位：pt，1/72 inch；y 轴向下，与绘图习惯一致）────────────────────
PAGE_W_PT = 560.0
PAGE_H_PT = 400.0
INSET_W_PT = 140.0
INSET_GAP_PT = 26.0
LEFT = 64.0
RIGHT = 534.0
TOP = 58.0
BOTTOM = 286.0
LEGEND_ROW_Y = (336.0, 352.0)
FOOTNOTE_Y = 380.0

# ── 颜色（PDF 里是 rg/RG 三元组，PNG 里换算成 0-255）────────────────────────
BLACK = (0.10, 0.10, 0.10)
AXIS = (0.20, 0.20, 0.20)
GRID = (0.86, 0.86, 0.86)
RED = (0.85, 0.10, 0.10)
BLUE = (0.05, 0.25, 0.75)
GRAY = (0.45, 0.45, 0.45)
ORANGE = (0.95, 0.50, 0.05)

LEGEND_STYLE = {
    "baseline_zeroday": ("circle", RED, "Baseline defense - held-out mutations"),
    "evolved_zeroday": ("circle", BLUE, "Evolved defense - held-out mutations"),
    "plain_attack_baseline": ("dash", GRAY, "Baseline defense - plaintext attacks (reference)"),
    "benign_false_positive": ("square", ORANGE, "Benign false-positive rate"),
}

DEFAULT_CAPTION_ZH = (
    "攻防共演化收敛曲线。X 轴为演化代数（generation_id），Y 轴为比率（0-1）。"
    "红线为未演化（基线）防线在 held-out 变异集上的拦截率，蓝线为演化防线在同一 held-out 集上的拦截率，"
    "灰虚线为基线防线在明文攻击集上的拦截率（“本来能拦”的参照水平），"
    "橙线为演化防线在良性语料上的误伤率。"
    "数据来源：coevolution_log.jsonl 的 intercept_rate_baseline_zeroday / "
    "intercept_rate_evolved_zeroday / benign_false_positive_rate 字段；"
    "明文参照取 coevolution_evidence.json 的 plain_attack_baseline_intercept_rate。"
    "诚实边界：held-out 集是同一批种子的 held-out split，不是分布外数据；"
    "变异为确定性规则变异（编码/词表/嵌套），不是 LLM 红队；本阶段零模型调用。"
)
DEFAULT_CAPTION_EN = (
    "Evolution convergence curve of the attack-defense loop. X: evolution generation "
    "(generation_id); Y: rate (0-1). Red: baseline (non-evolved) defense intercept rate on "
    "held-out mutations; blue: evolved defense on the same held-out set; grey dashed: baseline "
    "defense intercept rate on the plaintext attack set (reference level); orange: benign "
    "false-positive rate of the evolved defense. Sources: coevolution_log.jsonl fields "
    "intercept_rate_baseline_zeroday / intercept_rate_evolved_zeroday / "
    "benign_false_positive_rate; plaintext reference from coevolution_evidence.json "
    "plain_attack_baseline_intercept_rate. Honesty note: the held-out set is a held-out split "
    "of the same seed pool, not out-of-distribution data; mutations are deterministic rule "
    "mutations, not an LLM red team."
)

LIMITATIONS = [
    "held-out set = held-out split of the same seed pool; it is NOT out-of-distribution data "
    "(contract §8.5), so the figure must not be cited as OOD evidence.",
    "mutations are deterministic rule mutations (encoding / offline glossary / nesting), not an "
    "LLM red team (contract §8.1); the language variants are offline-glossary approximations, "
    "not real machine translation (contract §8.2).",
    "api_tokens_used = 0 in this phase; the token-budget stop condition was never exercised "
    "(contract §8.3).",
    "the grey plaintext reference is a single baseline measurement (one value per run), not a "
    "per-generation measurement; its provenance is recorded in `lines[*].source`.",
    "each logged rate is measured AFTER that generation's patch/regression step (contract §6.2 "
    "order 4)->5)): generation 0 already contains the gen-0 patch, so the blue curve does NOT "
    "start at the unpatched baseline level. The unpatched comparison is the red line (held-out) "
    "and the grey dashed line (plaintext); the 'gen-0 caveat' is `figure_data.plateau` / this "
    "limitation, and no pre-patch gen-0 measurement exists in coevolution_log.jsonl.",
    "defense under test is the Phase A rule gate (vocabulary matching), so encoding mutations "
    "bypassing it is expected; the contribution claimed is the closed-loop self-healing "
    "mechanism and the measurement method, not 'Phase A is weak' (contract §8.4).",
    "PDF is hand-written vector output (no matplotlib): base-14 Helvetica, ASCII text only; the "
    "Chinese caption lives only in this JSON file.",
]


# ── 错误类型（缺数据 → 退出码 2，见契约 §7.3）───────────────────────────────
class PlotDataError(SystemExit):
    """数据缺失 / 不一致。CLI 捕获后以退出码 2 结束（`--` 不画空图）。"""

    exit_code = 2

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ── 时间 / 路径小工具 ───────────────────────────────────────────────────────
def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _rel(path: str | None) -> str | None:
    if not path:
        return None
    try:
        rel = os.path.relpath(path, ROOT)
    except ValueError:  # 跨盘符
        return path
    return rel.replace("\\", "/") if not rel.startswith("..") else path


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.isfile(path):
        raise PlotDataError("coevolution_log.jsonl not found: %s" % path)
    rows: list[dict] = []
    with io.open(path, encoding="utf-8-sig") as fh:
        for lineno, raw in enumerate(fh, 1):
            line = raw.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except ValueError as exc:
                raise PlotDataError(
                    "invalid JSON in %s line %d: %s" % (path, lineno, exc))
            if not isinstance(obj, dict):
                raise PlotDataError(
                    "%s line %d is not a JSON object" % (path, lineno))
            rows.append(obj)
    if not rows:
        raise PlotDataError("coevolution_log.jsonl is empty: %s" % path)
    return rows


def _read_json(path: str) -> dict | None:
    if not os.path.isfile(path):
        return None
    with io.open(path, encoding="utf-8-sig") as fh:
        try:
            obj = json.load(fh)
        except ValueError as exc:
            raise PlotDataError("invalid JSON in %s: %s" % (path, exc))
    return obj if isinstance(obj, dict) else None


# ── 读日志（契约 §7.3）──────────────────────────────────────────────────────
def _resolve_path(path: str) -> tuple[str, str, str]:
    """把 --run 的一个值解析成 (log_file, run_dir, run_id)。"""
    if not path:
        raise PlotDataError("empty path given to --run")
    p = os.path.abspath(path)
    if os.path.isdir(p):
        log_path, run_dir = os.path.join(p, LOG_NAME), p
    elif os.path.isfile(p):
        if not p.lower().endswith(".jsonl"):
            raise PlotDataError(
                "not a run directory nor a .jsonl log file: %s" % path)
        log_path, run_dir = p, os.path.dirname(p)
    else:
        raise PlotDataError("run path not found: %s" % path)
    if not os.path.isfile(log_path):
        raise PlotDataError(
            "missing %s under: %s (wait for A5 / tools/run_coevolution.py)"
            % (LOG_NAME, os.path.dirname(log_path) or path))
    run_id = os.path.basename(os.path.normpath(run_dir)) or "unknown-run"
    return log_path, run_dir, run_id


def load_log(paths: list[str]) -> list[dict]:
    """读取一个或多个 run 的 `coevolution_log.jsonl`。

    `paths` 的元素可以是 run 目录，也可以直接是 `.jsonl` 文件。
    返回按 (run_id, generation_id) 排序的记录；每条记录额外带内部键
    `_run_id` / `_run_dir` / `_log_path` / `_evidence` / `_evidence_path`
    （内部键不进入 `figure_data.json` 的 `per_generation`）。
    """
    if not paths:
        raise PlotDataError("load_log(): no paths given (need --run <run_dir>)")
    records: list[dict] = []
    for path in paths:
        log_path, run_dir, run_id = _resolve_path(path)
        evidence_path = os.path.join(run_dir, EVIDENCE_NAME)
        evidence = _read_json(evidence_path)
        for row in _read_jsonl(log_path):
            rec = dict(row)
            rec["_run_id"] = run_id
            rec["_run_dir"] = run_dir
            rec["_log_path"] = log_path
            rec["_evidence"] = evidence
            rec["_evidence_path"] = evidence_path if evidence else None
            records.append(rec)
    return _dedupe(records)


def _public(rec: dict) -> dict:
    return {k: v for k, v in rec.items() if not k.startswith("_")}


def _dedupe(records: list[dict]) -> list[dict]:
    seen: dict[tuple, dict] = {}
    for rec in records:
        key = (rec.get("_run_id"), rec.get("generation_id"))
        if key in seen:
            if _public(seen[key]) != _public(rec):
                raise PlotDataError(
                    "duplicate generation_id=%r in run %r with conflicting content"
                    % (rec.get("generation_id"), rec.get("_run_id")))
            continue
        seen[key] = rec

    def sort_key(rec: dict):
        g = rec.get("generation_id")
        return (str(rec.get("_run_id")), g if isinstance(g, int) else -1)

    return sorted(seen.values(), key=sort_key)


def _select_run(records: list[dict], run_id: str | None
                ) -> tuple[list[dict], list[str], list[str]]:
    run_ids = sorted({str(r.get("_run_id")) for r in records})
    if run_id is None:
        chosen = run_ids[-1]
    else:
        if run_id not in run_ids:
            raise PlotDataError(
                "run_id %r not found; available runs: %s"
                % (run_id, ", ".join(run_ids)))
        chosen = run_id
    selected = [r for r in records if str(r.get("_run_id")) == chosen]
    ignored = [r for r in run_ids if r != chosen]
    return selected, run_ids, ignored


# ── 数值校验 ────────────────────────────────────────────────────────────────
def _rate_value(rec: dict, field: str, run_id: str, warnings: list[str]
                ) -> tuple[float, str]:
    """取一个 [0,1] 比率字段，返回 (value, source_note)。

    `benign_false_positive_rate` 字段缺失时允许由
    `benign_false_positive_count / benign_total` 精确导出（契约 §6.3 给了这两个分母/分子），
    其余字段缺失一律抛 `PlotDataError` —— 不猜、不补、不插值。
    """
    gen = rec.get("generation_id")
    if field in rec and rec[field] is not None:
        value = rec[field]
        if not _is_number(value):
            raise PlotDataError(
                "field %r in generation %r of run %r is not numeric: %r"
                % (field, gen, run_id, value))
        value = float(value)
        if not (-1e-9 <= value <= 1.0 + 1e-9):
            raise PlotDataError(
                "field %r in generation %r of run %r is outside [0,1]: %r"
                % (field, gen, run_id, value))
        value = min(1.0, max(0.0, value))
        if field == "benign_false_positive_rate":
            count = rec.get("benign_false_positive_count")
            total = rec.get("benign_total")
            if _is_number(count) and _is_number(total) and total > 0:
                derived = float(count) / float(total)
                if abs(derived - value) > 1e-3:
                    warnings.append(
                        "generation %r: benign_false_positive_rate=%r is inconsistent with "
                        "benign_false_positive_count/benign_total=%r; the log field is used as-is"
                        % (gen, value, derived))
        return value, "log"
    if field == "benign_false_positive_rate":
        count = rec.get("benign_false_positive_count")
        total = rec.get("benign_total")
        if _is_number(count) and _is_number(total) and total > 0:
            return float(count) / float(total), "derived:benign_false_positive_count/benign_total"
    raise PlotDataError(
        "missing field %r in generation %r of run %r; cannot plot "
        "(contract §6.3 / §7.3)" % (field, gen, run_id))


def _plaintext_baseline(selected: list[dict], override: float | None,
                        evidence: dict | None, warnings: list[str]
                        ) -> tuple[float, dict]:
    """明文攻击集参照水平：显式参数 > evidence 字段 > 用唯一仪器现算。"""
    if override is not None:
        if not _is_number(override) or not (-1e-9 <= float(override) <= 1.0 + 1e-9):
            raise PlotDataError(
                "plain_attack_baseline override is not a rate in [0,1]: %r" % (override,))
        value = min(1.0, max(0.0, float(override)))
        return value, {"kind": "explicit", "value": value,
                       "field": None, "file": None}

    ev = evidence
    if ev is None:
        for rec in selected:
            if isinstance(rec.get("_evidence"), dict):
                ev = rec["_evidence"]
                break
    if isinstance(ev, dict) and _is_number(ev.get("plain_attack_baseline_intercept_rate")):
        raw = float(ev["plain_attack_baseline_intercept_rate"])
        if not (-1e-9 <= raw <= 1.0 + 1e-9):
            raise PlotDataError(
                "coevolution_evidence.json: plain_attack_baseline_intercept_rate "
                "is outside [0,1]: %r" % (raw,))
        value = min(1.0, max(0.0, raw))
        ev_path = next((r.get("_evidence_path") for r in selected
                        if r.get("_evidence_path")), None)
        return value, {"kind": "evidence", "value": value,
                       "field": "plain_attack_baseline_intercept_rate",
                       "file": _rel(ev_path), "run_id": ev.get("run_id")}

    if isinstance(ev, dict):
        warnings.append(
            "coevolution_evidence.json exists but has no numeric "
            "plain_attack_baseline_intercept_rate; computing it with "
            "tools/phase_b_defense.evaluate(load_seed_attacks(), patch=None)")
    else:
        warnings.append(
            "coevolution_evidence.json not found; computing the plaintext reference with "
            "tools/phase_b_defense.evaluate(load_seed_attacks(), patch=None)")

    try:
        from tools.phase_b_defense import evaluate, load_seed_attacks
    except Exception as exc:  # pragma: no cover - only if the instrument is missing
        raise PlotDataError(
            "cannot compute plaintext baseline: tools/phase_b_defense.py unavailable (%s)" % exc)
    seeds = load_seed_attacks()
    if not seeds:
        raise PlotDataError(
            "cannot compute plaintext baseline: seed attack corpus is empty "
            "(experiments/phase-b/corpora/seed_attacks.jsonl)")
    result = evaluate(seeds, None)
    value = float(result["rate"])
    return value, {"kind": "computed", "value": value,
                   "field": "evaluate(load_seed_attacks(), patch=None).rate",
                   "file": _rel(os.path.join(ROOT, "tools", "phase_b_defense.py")),
                   "n": result["n"], "intercepted": result["intercepted"],
                   "note": "coevolution_evidence.json has no plain_attack_baseline_"
                           "intercept_rate; recomputed with the single contract instrument "
                           "(contract §3): baseline defense = no patch"}


def _plateau_analysis(series: dict) -> dict | None:
    """从 log 数值判断演化防线在 held-out 集上是否"先上升、后平台"。

    只做描述性统计（不插值、不平滑）：找到最后一个值从哪一代开始一直保持不变，
    并对照该区间内累计接受的补丁数。返回 None 表示曲线没有"先变化后平台"的形态。
    """
    gens = [int(g) for g in series.get("generations") or []]
    evolved = [float(v) for v in series.get("evolved_zeroday") or []]
    patches = list(series.get("patches_series") or [])
    n = min(len(gens), len(evolved))
    if n < 3:
        return None
    final = evolved[n - 1]
    start = n - 1
    while start > 0 and evolved[start - 1] == final:
        start -= 1
    if start == 0 or start == n - 1:
        return None
    patches_start = patches[start] if start < len(patches) else None
    patches_end = patches[n - 1] if n - 1 < len(patches) else None
    added = None
    if isinstance(patches_start, int) and isinstance(patches_end, int):
        added = patches_end - patches_start
    increments = [evolved[i] - evolved[i - 1] for i in range(1, start + 1)]
    return {
        "evolved_zeroday_value": final,
        "plateau_start_generation": gens[start],
        "plateau_start_index": start,
        "plateau_end_generation": gens[n - 1],
        "generations_in_plateau": n - start,
        "patches_at_plateau_start": patches_start,
        "patches_at_end": patches_end,
        "patches_added_during_plateau": added,
        "last_increment": increments[-1] if increments else None,
        "evolved_zeroday": evolved,
        "patch_count_series": patches,
    }


def _plateau_limitation(plateau: dict | None) -> str | None:
    """把平台化事实写成一条（数据现算的）诚实说明；没有平台就不写。"""
    if not plateau:
        return None
    added = plateau.get("patches_added_during_plateau")
    if not isinstance(added, int) or added <= 0:
        return None
    return (
        "plateau is data, not smoothing: the evolved defense stays at %.4g on the held-out set "
        "from generation %d through generation %d (%d of %d generations); the accepted patch "
        "count grows from %s to %s (+%d) over that span while the held-out interception rate "
        "gains exactly 0 -- rule-level self-healing consumes surface forms but not rewrites "
        "that destroy semantic surface evidence."
        % (plateau["evolved_zeroday_value"],
           plateau["plateau_start_generation"], plateau["plateau_end_generation"],
           plateau["generations_in_plateau"], plateau["generations_in_plateau"]
           + plateau["plateau_start_index"], plateau["patches_at_plateau_start"],
           plateau["patches_at_end"], added))


# ── 序列构建（契约 §7.3）────────────────────────────────────────────────────
def build_series(records: list[dict], *, run_id: str | None = None,
                 plain_attack_baseline: float | None = None,
                 evidence: dict | None = None) -> dict:
    """把日志记录整理成画图用的定长数值序列。

    返回 dict 至少含 `generations` / `baseline_zeroday` / `evolved_zeroday` /
    `benign_false_positive` / `plain_attack_baseline`，另带 figure_data 需要的
    run_id、patch 数、来源、警告等元数据。缺字段/越界/重复代数 → `PlotDataError`。
    """
    if not records:
        raise PlotDataError("build_series(): no records (log is empty or was not loaded)")

    prepared: list[dict] = []
    for rec in records:
        if not isinstance(rec, dict):
            raise PlotDataError("build_series(): record is not a dict: %r" % (rec,))
        item = dict(rec)
        item.setdefault("_run_id", run_id or "unknown-run")
        prepared.append(item)

    selected, run_ids_seen, ignored_runs = _select_run(prepared, run_id)
    selected.sort(key=lambda r: r.get("generation_id")
                  if isinstance(r.get("generation_id"), int) else -1)

    warnings: list[str] = []
    derived_notes: list[dict] = []
    gens: list[int] = []
    baseline: list[float] = []
    evolved_z: list[float] = []
    benign: list[float] = []
    evolved_mut: list[float | None] = []
    bypass: list[int | None] = []
    patches_series: list[int | None] = []

    for rec in selected:
        gen = rec.get("generation_id")
        if isinstance(gen, bool) or not isinstance(gen, int):
            raise PlotDataError(
                "field 'generation_id' must be an integer in run %r, got %r"
                % (rec.get("_run_id"), gen))
        if gen < 0:
            raise PlotDataError("field 'generation_id' must be >= 0, got %r" % (gen,))
        gens.append(gen)
        for field in REQUIRED_FIELDS:
            # `benign_false_positive_rate` 允许由 count/total 导出（_rate_value 里处理），
            # 其余必需的比率字段缺失时必须当场报错。
            if field in ("generation_id", "benign_false_positive_rate"):
                continue
            if field not in rec:
                raise PlotDataError(
                    "missing field %r in generation %r of run %r; cannot plot "
                    "(contract §6.3 / §7.3)"
                    % (field, gen, rec.get("_run_id")))
        baseline.append(_rate_value(rec, "intercept_rate_baseline_zeroday",
                                    str(rec.get("_run_id")), warnings)[0])
        evolved_z.append(_rate_value(rec, "intercept_rate_evolved_zeroday",
                                     str(rec.get("_run_id")), warnings)[0])
        benign_value, benign_source = _rate_value(
            rec, "benign_false_positive_rate", str(rec.get("_run_id")), warnings)
        benign.append(benign_value)
        if benign_source.startswith("derived:"):
            derived_notes.append({"generation_id": gen,
                                  "field": "benign_false_positive_rate",
                                  "source": benign_source,
                                  "value": benign_value})
        mut = rec.get("intercept_rate_evolved")
        evolved_mut.append(float(mut) if _is_number(mut) else None)
        b = rec.get("bypass_success_count")
        bypass.append(int(b) if isinstance(b, int) and not isinstance(b, bool) else None)
        p = rec.get("patches_total")
        patches_series.append(int(p) if isinstance(p, int) and not isinstance(p, bool) else None)

    if len(set(gens)) != len(gens):
        dup = sorted({g for g in gens if gens.count(g) > 1})
        raise PlotDataError("duplicate generation_id(s) in run %r: %r"
                            % (selected[0].get("_run_id"), dup))
    if gens != sorted(gens):
        warnings.append("generation_id order was not ascending in the log; sorted for plotting")
    if gens and gens != list(range(gens[0], gens[0] + len(gens))):
        warnings.append(
            "generation_id is not contiguous (%r); points are placed at their real "
            "generation_id on the x axis" % (gens,))

    plain_value, plain_source = _plaintext_baseline(selected, plain_attack_baseline,
                                                   evidence, warnings)
    n = len(gens)
    last = selected[-1]

    patch_ids = last.get("patch_ids_accepted")
    if patch_ids is None:
        patch_ids = []
    if not isinstance(patch_ids, list):
        patch_ids = []
    patches_total = patches_series[-1] if patches_series[-1] is not None else None
    patch_generated_total = sum(
        1 for rec in selected if rec.get("blue_team_patch_generated") == 1)
    if patches_total is None:
        warnings.append("`patches_total` missing from the log; footnote shows 'n/a' "
                        "for the patch count")

    stop_reason = None
    for rec in reversed(selected):
        if rec.get("stop_reason"):
            stop_reason = rec.get("stop_reason")
            break

    tokens = [rec.get("api_tokens_used") for rec in selected]
    token_sum = sum(t for t in tokens if _is_number(t))
    calls = [rec.get("api_calls") for rec in selected]
    call_sum = sum(c for c in calls if _is_number(c))

    ev = evidence
    if ev is None:
        ev = next((r.get("_evidence") for r in selected
                   if isinstance(r.get("_evidence"), dict)), None)
    ev_path = next((r.get("_evidence_path") for r in selected if r.get("_evidence_path")), None)

    evidence_subset = {}
    if isinstance(ev, dict):
        for key in ("schema", "run_id", "started_at", "finished_at", "generations_run",
                    "stop_reason", "converged", "dry_run",
                    "plain_attack_baseline_intercept_rate",
                    "zeroday_baseline_intercept_rate",
                    "zeroday_evolved_intercept_rate_final", "max_intercept_rate_evolved",
                    "max_intercept_rate_evolved_generation",
                    "benign_false_positive_rate_max",
                    "benign_false_positive_rate_baseline", "benign_total",
                    "red_team_variants_total", "bypass_total", "patches_total",
                    "patches", "regression_all_pass", "api_calls", "api_tokens_used",
                    "determinism", "limitations", "zeroday_set", "zeroday_protocol",
                    "zeroday_generation_id", "defense_under_test"):
            if key in ev:
                evidence_subset[key] = ev[key]

    ts_first = next((r.get("ts") for r in selected if r.get("ts")), None)
    ts_last = next((r.get("ts") for r in reversed(selected) if r.get("ts")), None)

    out = {
        "schema": SCHEMA,
        "run_id": str(selected[0].get("_run_id")),
        "generated_at": _now_iso(),
        "generation_count": n,
        "generations": gens,
        "baseline_zeroday": baseline,
        "evolved_zeroday": evolved_z,
        "benign_false_positive": benign,
        "plain_attack_baseline": [plain_value] * n,
        "plain_attack_baseline_value": plain_value,
        "plain_attack_baseline_source": plain_source,
        "evolved_mutated": evolved_mut,
        "bypass_success_count": bypass,
        "patches_series": patches_series,
        "patches": {
            "patches_total": patches_total,
            "patch_ids_accepted": [str(x) for x in patch_ids],
            "patch_generated_total": patch_generated_total,
        },
        "stop_reason": stop_reason,
        "api_calls": call_sum,
        "api_tokens_used": token_sum,
        "regression": {
            "last_regression_test_pass": last.get("regression_test_pass"),
            "last_regression": last.get("regression"),
        },
        "run_ids_seen": run_ids_seen,
        "ignored_runs": ignored_runs,
        "ts_first": ts_first,
        "ts_last": ts_last,
        "evidence": evidence_subset,
        "source": {
            "log_files": sorted({_rel(r.get("_log_path")) for r in selected
                                 if r.get("_log_path")}),
            "run_dirs": sorted({_rel(r.get("_run_dir")) for r in selected
                                if r.get("_run_dir")}),
            "evidence_file": _rel(ev_path),
        },
        "fields": {
            "generations": "generation_id",
            "baseline_zeroday": "intercept_rate_baseline_zeroday",
            "evolved_zeroday": "intercept_rate_evolved_zeroday",
            "benign_false_positive": "benign_false_positive_rate",
            "plain_attack_baseline": "plain_attack_baseline_intercept_rate",
        },
        "per_generation": [_public(rec) for rec in selected],
        "fields_derived": derived_notes,
        "warnings": warnings,
    }
    # 平台化事实（"先上升后平台"）由 log 现算，供图注/论文如实引用，不做平滑。
    out["plateau"] = _plateau_analysis(out)
    return out


# ── 版面：数据 → 绘制指令表（PDF 与 PNG 共用）──────────────────────────────
def page_size(series: dict) -> tuple[float, float]:
    inset = bool((series.get("layout") or {}).get("inset"))
    width = PAGE_W_PT + (INSET_W_PT + INSET_GAP_PT if inset else 0.0)
    return width, PAGE_H_PT


def _fmt_rate(value: float) -> str:
    text = ("%.3f" % float(value)).rstrip("0").rstrip(".")
    return text or "0"


def _nice_max(value: float) -> float:
    if value <= 0:
        return 0.02
    for candidate in (0.01, 0.02, 0.05, 0.1, 0.2, 0.25, 0.5, 1.0):
        if value <= candidate * 1.0001:
            return candidate
    return 1.0


def _line(x1, y1, x2, y2, color, width=1.0, dash=None, role="deco"):
    return {"op": "line", "x1": float(x1), "y1": float(y1),
            "x2": float(x2), "y2": float(y2), "color": color,
            "width": float(width), "dash": dash, "role": role}


def _rect(x, y, w, h, fill=None, stroke=None, width=1.0, dash=None, role="deco"):
    return {"op": "rect", "x": float(x), "y": float(y), "w": float(w), "h": float(h),
            "fill": fill, "stroke": stroke, "width": float(width), "dash": dash,
            "role": role}


def _circle(cx, cy, r, fill, stroke=None, width=1.0, role="deco"):
    return {"op": "circle", "cx": float(cx), "cy": float(cy), "r": float(r),
            "fill": fill, "stroke": stroke, "width": float(width), "role": role}


def _polyline(points, color, width=1.5, dash=None, role="deco"):
    return {"op": "polyline", "pts": [(float(a), float(b)) for a, b in points],
            "color": color, "width": float(width), "dash": dash, "role": role}


def _text(x, y, text, size=8.0, bold=False, align="left", color=BLACK, role="deco"):
    for ch in text:
        if not 32 <= ord(ch) <= 126:
            raise PlotDataError(
                "figure text must be printable ASCII (PDF base-14 only): %r" % (text,))
    return {"op": "text", "x": float(x), "y": float(y), "text": text,
            "size": float(size), "bold": bool(bold), "align": align,
            "color": color, "role": role}


def _require_series(series: dict) -> None:
    for key in ("generations", "baseline_zeroday", "evolved_zeroday",
                "benign_false_positive", "plain_attack_baseline"):
        if key not in series:
            raise PlotDataError("build_ops(): series is missing %r" % key)
    n = len(series["generations"])
    if n == 0:
        raise PlotDataError("build_ops(): series has no generations")
    for key in ("baseline_zeroday", "evolved_zeroday", "benign_false_positive",
                "plain_attack_baseline"):
        if len(series[key]) != n:
            raise PlotDataError(
                "build_ops(): series %r has %d values but %d generations"
                % (key, len(series[key]), n))


def build_ops(series: dict) -> list[dict]:
    """把序列排成一张图的绘制指令表（纯数据，两个后端共用）。"""
    _require_series(series)
    inset = bool((series.get("layout") or {}).get("inset"))
    ops: list[dict] = []
    gens: list[int] = [int(g) for g in series["generations"]]
    n = len(gens)
    g_first, g_last = gens[0], gens[-1]

    def x_at(gen: int, left: float = LEFT, right: float = RIGHT) -> float:
        if g_last == g_first:
            return (left + right) / 2.0
        return left + (float(gen) - g_first) / (g_last - g_first) * (right - left)

    def y_at(value: float) -> float:
        return BOTTOM - float(value) * (BOTTOM - TOP)

    # 1) 网格 + Y 刻度
    for value, label in Y_TICKS:
        y = y_at(value)
        if value > 0.0:
            ops.append(_line(LEFT, y, RIGHT, y, GRID, 0.5, role="grid"))
        ops.append(_text(LEFT - 7.0, y + 2.6, label, 8, align="right",
                         color=AXIS, role="y-ticklabel"))
    # 2) 坐标轴
    ops.append(_line(LEFT, TOP, LEFT, BOTTOM, AXIS, 1.0, role="y-axis"))
    ops.append(_line(LEFT, BOTTOM, RIGHT, BOTTOM, AXIS, 1.0, role="x-axis"))
    # 3) X 刻度（真实 generation_id，整数）
    for gen in gens:
        x = x_at(gen)
        ops.append(_line(x, BOTTOM, x, BOTTOM + 3.0, AXIS, 0.8, role="x-tick"))
        ops.append(_text(x, BOTTOM + 13.0, "%d" % gen, 8, align="center",
                         color=AXIS, role="x-ticklabel"))
    # 4) 轴标题
    ops.append(_text(LEFT, TOP - 6.0, "rate (0.0 - 1.0)", 8, color=AXIS,
                     role="y-axislabel"))
    ops.append(_text((LEFT + RIGHT) / 2.0, BOTTOM + 29.0,
                     "evolution generation (generation_id)", 9, align="center",
                     color=BLACK, role="x-axislabel"))

    # 5) 灰虚线：基线防线在明文攻击集上的拦截率（参照水平）
    plain = series["plain_attack_baseline"]
    plain_pts = [(x_at(g), y_at(v)) for g, v in zip(gens, plain)]
    ops.append(_polyline(plain_pts, GRAY, 1.2, dash=(4.0, 2.5),
                         role="line:plain_attack_baseline"))

    # 6) 红线：基线防线在 held-out 变异集上的拦截率
    baseline_pts = [(x_at(g), y_at(v))
                    for g, v in zip(gens, series["baseline_zeroday"])]
    ops.append(_polyline(baseline_pts, RED, 1.8, role="line:baseline_zeroday"))
    for x, y in baseline_pts:
        ops.append(_circle(x, y, 2.4, RED, role="marker:baseline_zeroday"))

    # 7) 蓝线：演化防线在 held-out 变异集上的拦截率
    evolved_pts = [(x_at(g), y_at(v))
                   for g, v in zip(gens, series["evolved_zeroday"])]
    ops.append(_polyline(evolved_pts, BLUE, 1.8, role="line:evolved_zeroday"))
    for x, y in evolved_pts:
        ops.append(_circle(x, y, 2.4, BLUE, role="marker:evolved_zeroday"))

    # 8) 橙线：良性误伤率（默认画在主轴上，不同标记；--inset 时改到小图）
    if not inset:
        benign_pts = [(x_at(g), y_at(v))
                      for g, v in zip(gens, series["benign_false_positive"])]
        ops.append(_polyline(benign_pts, ORANGE, 1.4,
                             role="line:benign_false_positive"))
        for x, y in benign_pts:
            ops.append(_rect(x - 2.1, y - 2.1, 4.2, 4.2, fill=ORANGE,
                             role="marker:benign_false_positive"))

    # 8b) 平台化标注：由 log 数值现算（不插值、不平滑），把"回升后平台化"写在图上
    plateau = series.get("plateau")
    if plateau is None:
        plateau = _plateau_analysis(series)
    if plateau and plateau["generations_in_plateau"] >= 3:
        x_start = x_at(gens[plateau["plateau_start_index"]])
        x_end = x_at(gens[-1])
        note_y = max(TOP + 12.0, y_at(plateau["evolved_zeroday_value"]) - 7.0)
        ops.append(_text((x_start + x_end) / 2.0, note_y,
                         "plateau %.3f from generation %d (data, not smoothing)"
                         % (plateau["evolved_zeroday_value"],
                            plateau["plateau_start_generation"]),
                         8, align="center", color=BLUE, role="plateau-note"))

    # 9) inset 面板：误伤率的放大视图（可选，契约 §7.2 允许的"单独小图"方案）
    if inset:
        ops.extend(_inset_ops(series, page_w=page_size(series)[0]))

    # 10) 标题 / 副标题
    title = ("Attack-Defense Co-evolution: Interception vs Benign False-Positive Rate")
    title_size = 12.0
    while _text_width(title, title_size, True) > (page_size(series)[0] - 80.0) \
            and title_size > 9.0:
        title_size -= 0.5
    ops.append(_text(page_size(series)[0] / 2.0, 26.0, title, title_size, bold=True,
                     align="center", color=BLACK, role="title"))
    ops.append(_text(page_size(series)[0] / 2.0, 42.0,
                     "held-out mutations only; grey dashed = baseline defense on the "
                     "plaintext attack set (reference)", 8, align="center",
                     color=AXIS, role="subtitle"))

    # 11) 图例（两列两行）
    entries = ["baseline_zeroday", "evolved_zeroday",
               "plain_attack_baseline", "benign_false_positive"]
    positions = [(70.0, LEGEND_ROW_Y[0]), (300.0, LEGEND_ROW_Y[0]),
                 (70.0, LEGEND_ROW_Y[1]), (300.0, LEGEND_ROW_Y[1])]
    for line_id, (lx, ly) in zip(entries, positions):
        style, color, label = LEGEND_STYLE[line_id]
        if line_id == "benign_false_positive" and inset:
            label = "Benign false-positive rate (see inset)"
        if style == "dash":
            ops.append(_line(lx, ly, lx + 22.0, ly, color, 1.2, dash=(4.0, 2.5),
                             role="legend-sample"))
        else:
            ops.append(_line(lx, ly, lx + 22.0, ly, color, 1.6, role="legend-sample"))
            if style == "circle":
                ops.append(_circle(lx + 11.0, ly, 2.4, color, role="legend-sample"))
            else:
                ops.append(_rect(lx + 8.9, ly - 2.1, 4.2, 4.2, fill=color,
                                 role="legend-sample"))
        ops.append(_text(lx + 28.0, ly + 2.6, label, 8, color=BLACK, role="legend-text"))

    # 12) 脚注（6 pt，必须以 "source:" 开头 —— 仓库内 PDF 校验器允许的唯一 6 pt 文本）
    patches_total = (series.get("patches") or {}).get("patches_total")
    patch_text = "%d" % patches_total if isinstance(patches_total, int) else "n/a"
    footnote = ("source: coevolution_log.jsonl | run %s | %d generations | %s patches | "
                "generated %s" % (series.get("run_id", "unknown"),
                                  n, patch_text, series.get("generated_at", "n/a")))
    ops.append(_text(LEFT, FOOTNOTE_Y, footnote, 6, color=AXIS, role="footnote"))
    return ops


def _inset_ops(series: dict, *, page_w: float) -> list[dict]:
    """右侧小图：良性误伤率放大视图（自己的 0..nice_max 纵轴）。"""
    ops: list[dict] = []
    gens = [int(g) for g in series["generations"]]
    benign = list(series["benign_false_positive"])
    left = page_w - INSET_W_PT - 12.0
    right = left + INSET_W_PT
    top, bottom = TOP, BOTTOM
    ymax = _nice_max(max(benign) if benign else 0.0)
    g_first, g_last = gens[0], gens[-1]

    def x_at(gen: int) -> float:
        if g_last == g_first:
            return (left + right) / 2.0
        return left + (float(gen) - g_first) / (g_last - g_first) * (right - left)

    def y_at(value: float) -> float:
        return bottom - float(value) / ymax * (bottom - top)

    ops.append(_text(left, TOP - 6.0, "benign FP rate (zoom)", 8, color=AXIS,
                     role="inset-title"))
    for value, label in ((0.0, "0"), (ymax / 2.0, _fmt_rate(ymax / 2.0)),
                         (ymax, _fmt_rate(ymax))):
        y = y_at(value)
        if value > 0:
            ops.append(_line(left, y, right, y, GRID, 0.5, role="inset-grid"))
        ops.append(_text(left - 4.0, y + 2.6, label, 8, align="right", color=AXIS,
                         role="inset-ticklabel"))
    ops.append(_line(left, top, left, bottom, AXIS, 0.9, role="inset-yaxis"))
    ops.append(_line(left, bottom, right, bottom, AXIS, 0.9, role="inset-xaxis"))
    for gen in gens:
        if len(gens) > 6 and gen not in (g_first, g_last):
            continue
        x = x_at(gen)
        ops.append(_line(x, bottom, x, bottom + 2.5, AXIS, 0.7, role="inset-x-tick"))
        ops.append(_text(x, bottom + 12.0, "%d" % gen, 8, align="center", color=AXIS,
                         role="inset-x-ticklabel"))

    pts = [(x_at(g), y_at(v)) for g, v in zip(gens, benign)]
    ops.append(_polyline(pts, ORANGE, 1.4, role="inset-line:benign_false_positive"))
    for x, y in pts:
        ops.append(_rect(x - 1.9, y - 1.9, 3.8, 3.8, fill=ORANGE,
                         role="inset-marker:benign_false_positive"))
    if max(benign) == 0.0:
        cx, cy = (left + right) / 2.0, (top + bottom) / 2.0
        ops.append(_text(cx, cy - 2.0, "0 false positives", 8, align="center",
                         color=AXIS, role="inset-note"))
        ops.append(_text(cx, cy + 10.0, "in all generations", 8, align="center",
                         color=AXIS, role="inset-note"))
    return ops


# ── Helvetica 字宽（base-14 AFM，1/1000 em）─────────────────────────────────
_HELV = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
    278, 278, 584, 584, 584, 556, 1015,
    667, 667, 722, 722, 667, 611, 778, 722, 278, 500, 667, 556, 833, 722, 778, 667,
    778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
    278, 278, 278, 469, 556, 333,
    556, 556, 500, 556, 556, 278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556,
    556, 333, 500, 278, 556, 500, 722, 500, 500, 500,
    334, 260, 334, 584,
]
_HELV_B = [
    278, 333, 474, 556, 556, 889, 722, 238, 333, 333, 389, 584, 278, 333, 278, 278,
    556, 556, 556, 556, 556, 556, 556, 556, 556, 556,
    333, 333, 584, 584, 584, 611, 975,
    722, 722, 722, 722, 667, 611, 778, 722, 278, 556, 722, 611, 833, 722, 778, 667,
    778, 722, 667, 611, 722, 667, 944, 667, 667, 611,
    333, 278, 333, 584, 556, 333,
    556, 611, 556, 611, 556, 333, 611, 611, 278, 278, 556, 278, 889, 611, 611, 611,
    611, 389, 556, 333, 611, 556, 778, 556, 556, 500,
    389, 280, 389, 584,
]
assert len(_HELV) == 95 and len(_HELV_B) == 95


def _char_width(ch: str, size: float, bold: bool = False) -> float:
    code = ord(ch)
    if not 32 <= code <= 126:
        raise ValueError("non-ASCII character %r (base-14 Helvetica only)" % ch)
    return (_HELV_B if bold else _HELV)[code - 32] * size / 1000.0


def _text_width(text: str, size: float, bold: bool = False) -> float:
    return sum(_char_width(ch, size, bold) for ch in text)


def _pdf_num(value: float) -> str:
    text = "%.4f" % float(value)
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    if text in ("", "-0"):
        text = "0"
    return text


def _pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


# ── 最小矢量 PDF 写入器 ─────────────────────────────────────────────────────
def _pdf_content(ops: list[dict], height: float) -> bytes:
    out: list[str] = []

    def fy(y: float) -> float:
        return float(height) - float(y)

    def rgb(color) -> str:
        return "%s %s %s" % (_pdf_num(color[0]), _pdf_num(color[1]), _pdf_num(color[2]))

    def dash(d) -> str:
        return "[] 0 d" if not d else "[%s %s] 0 d" % (_pdf_num(d[0]), _pdf_num(d[1]))

    for op in ops:
        kind = op["op"]
        if kind == "line":
            out.append("%s RG" % rgb(op["color"]))
            out.append("%s w" % _pdf_num(op["width"]))
            out.append(dash(op.get("dash")))
            out.append("%s %s m %s %s l S"
                       % (_pdf_num(op["x1"]), _pdf_num(fy(op["y1"])),
                          _pdf_num(op["x2"]), _pdf_num(fy(op["y2"]))))
        elif kind == "polyline":
            pts = op["pts"]
            out.append("%s RG" % rgb(op["color"]))
            out.append("%s w" % _pdf_num(op["width"]))
            out.append(dash(op.get("dash")))
            seg = ["%s %s m" % (_pdf_num(pts[0][0]), _pdf_num(fy(pts[0][1])))]
            for px, py in pts[1:]:
                seg.append("%s %s l" % (_pdf_num(px), _pdf_num(fy(py))))
            seg.append("S")
            out.append(" ".join(seg))
        elif kind == "rect":
            x, y, w, h = op["x"], op["y"], op["w"], op["h"]
            if op.get("fill") is not None:
                out.append("%s rg" % rgb(op["fill"]))
            if op.get("stroke") is not None:
                out.append("%s RG" % rgb(op["stroke"]))
                out.append("%s w" % _pdf_num(op["width"]))
                out.append(dash(op.get("dash")))
            out.append("%s %s %s %s re"
                       % (_pdf_num(x), _pdf_num(fy(y + h)), _pdf_num(w), _pdf_num(h)))
            if op.get("fill") is not None and op.get("stroke") is not None:
                out.append("B")
            elif op.get("fill") is not None:
                out.append("f")
            else:
                out.append("S")
        elif kind == "circle":
            cx, cy, r = op["cx"], op["cy"], op["r"]
            k = 0.5523 * r
            cyf = fy(cy)
            out.append("%s rg" % rgb(op["fill"]))
            if op.get("stroke") is not None:
                out.append("%s RG" % rgb(op["stroke"]))
                out.append("%s w" % _pdf_num(op["width"]))
            out.append(dash(None))
            seg = [
                "%s %s m" % (_pdf_num(cx + r), _pdf_num(cyf)),
                "%s %s %s %s %s %s c" % (_pdf_num(cx + r), _pdf_num(cyf + k),
                                          _pdf_num(cx + k), _pdf_num(cyf + r),
                                          _pdf_num(cx), _pdf_num(cyf + r)),
                "%s %s %s %s %s %s c" % (_pdf_num(cx - k), _pdf_num(cyf + r),
                                          _pdf_num(cx - r), _pdf_num(cyf + k),
                                          _pdf_num(cx - r), _pdf_num(cyf)),
                "%s %s %s %s %s %s c" % (_pdf_num(cx - r), _pdf_num(cyf - k),
                                          _pdf_num(cx - k), _pdf_num(cyf - r),
                                          _pdf_num(cx), _pdf_num(cyf - r)),
                "%s %s %s %s %s %s c" % (_pdf_num(cx + k), _pdf_num(cyf - r),
                                          _pdf_num(cx + r), _pdf_num(cyf - k),
                                          _pdf_num(cx + r), _pdf_num(cyf)),
                "h",
            ]
            out.append(" ".join(seg))
            out.append("f")
        elif kind == "text":
            text = op["text"]
            for ch in text:
                if not 32 <= ord(ch) <= 126:
                    raise PlotDataError(
                        "figure text must be printable ASCII: %r" % (text,))
            x = op["x"]
            align = op.get("align", "left")
            if align == "center":
                x -= _text_width(text, op["size"], op["bold"]) / 2.0
            elif align == "right":
                x -= _text_width(text, op["size"], op["bold"])
            font = "F2" if op["bold"] else "F1"
            out.append("%s rg" % rgb(op["color"]))
            out.append("BT /%s %s Tf 1 0 0 1 %s %s Tm (%s) Tj ET"
                       % (font, _pdf_num(op["size"]), _pdf_num(x),
                          _pdf_num(fy(op["y"])), _pdf_escape(text)))
        else:  # pragma: no cover
            raise PlotDataError("unknown draw op: %r" % (kind,))
    return ("\n".join(out) + "\n").encode("latin-1")


def _pdf_date(iso: str) -> str:
    try:
        dt = datetime.fromisoformat(iso)
    except (TypeError, ValueError):
        dt = datetime.now().astimezone()
    offset = dt.strftime("%z")  # e.g. +0800
    tz = "%s'%s'" % (offset[:3], offset[3:5]) if len(offset) == 5 else "+00'00'"
    return "D:%s%s" % (dt.strftime("%Y%m%d%H%M%S"), tz)


def _write_pdf(ops: list[dict], width: float, height: float, out_path: str,
               *, title: str, subject: str, created_at: str) -> str:
    content = _pdf_content(ops, height)
    objs: dict[int, bytes] = {
        1: b"<< /Type /Catalog /Pages 2 0 R >>",
        2: b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        3: ("<< /Type /Page /Parent 2 0 R /MediaBox [0 0 %s %s] "
            "/Resources << /Font << /F1 5 0 R /F2 6 0 R >> >> /Contents 4 0 R >>"
            % (_pdf_num(width), _pdf_num(height))).encode("latin-1"),
        4: b"<< /Length %d >>\nstream\n" % len(content) + content + b"endstream",
        5: (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica "
            b"/Encoding /WinAnsiEncoding >>"),
        6: (b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica-Bold "
            b"/Encoding /WinAnsiEncoding >>"),
        7: ("<< /Producer (LINGSHU Phase B A2 plot_coevolution_curve.py) "
            "/Title (%s) /Subject (%s) /CreationDate (%s) >>"
            % (_pdf_escape(title), _pdf_escape(subject),
               _pdf_date(created_at))).encode("latin-1"),
    }
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for num in sorted(objs):
        offsets[num] = len(out)
        out += b"%d 0 obj\n" % num + objs[num] + b"\nendobj\n"
    xref_at = len(out)
    count = max(objs) + 1
    out += b"xref\n0 %d\n" % count
    out += b"0000000000 65535 f \n"
    for num in range(1, count):
        out += b"%010d 00000 n \n" % offsets[num]
    out += b"trailer\n<< /Size %d /Root 1 0 R /Info 7 0 R >>\n" % count
    out += b"startxref\n%d\n%%%%EOF\n" % xref_at
    folder = os.path.dirname(os.path.abspath(out_path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    with open(out_path, "wb") as fh:
        fh.write(bytes(out))
    return out_path


# ── PNG（Pillow）───────────────────────────────────────────────────────────
_FONT_CANDIDATES_BOLD = (
    r"C:\Windows\Fonts\arialbd.ttf",
    r"C:\Windows\Fonts\segoeuib.ttf",
    r"C:\Windows\Fonts\DejaVuSans-Bold.ttf",
)
_FONT_CANDIDATES_REGULAR = (
    r"C:\Windows\Fonts\arial.ttf",
    r"C:\Windows\Fonts\segoeui.ttf",
    r"C:\Windows\Fonts\DejaVuSans.ttf",
)


def _load_font(px: int, bold: bool):
    from PIL import ImageFont

    for candidate in (_FONT_CANDIDATES_BOLD if bold else _FONT_CANDIDATES_REGULAR):
        if os.path.isfile(candidate):
            try:
                return ImageFont.truetype(candidate, px)
            except OSError:
                continue
    try:
        return ImageFont.load_default(size=px)
    except TypeError:  # pragma: no cover - very old Pillow
        return ImageFont.load_default()


def _png_rgb(color) -> tuple[int, int, int]:
    return tuple(max(0, min(255, int(round(255.0 * float(v))))) for v in color)


def _draw_dashed(draw, points, fill, width, on, off) -> None:
    if on <= 0:
        return
    drawing, remaining = True, on
    for (x1, y1), (x2, y2) in zip(points, points[1:]):
        dx, dy = x2 - x1, y2 - y1
        length = math.hypot(dx, dy)
        if length <= 1e-9:
            continue
        t = 0.0
        while t < length:
            step = min(remaining, length - t)
            if drawing and step > 1e-9:
                draw.line([x1 + dx * t / length, y1 + dy * t / length,
                           x1 + dx * (t + step) / length, y1 + dy * (t + step) / length],
                          fill=fill, width=width)
            t += step
            remaining -= step
            if remaining <= 1e-9:
                drawing = not drawing
                remaining = on if drawing else off


def _write_png(ops: list[dict], width: float, height: float, out_path: str,
               dpi: int) -> tuple[int, int]:
    from PIL import Image, ImageDraw

    scale = float(dpi) / 72.0
    px_w = int(round(width * scale))
    px_h = int(round(height * scale))
    image = Image.new("RGB", (px_w, px_h), (255, 255, 255))
    draw = ImageDraw.Draw(image)
    fonts: dict[tuple, object] = {}

    def font(size: float, bold: bool):
        key = (round(size, 2), bool(bold))
        if key not in fonts:
            fonts[key] = _load_font(max(1, int(round(size * scale))), bold)
        return fonts[key]

    def sx(value: float) -> float:
        return value * scale

    for op in ops:
        kind = op["op"]
        if kind == "line":
            color = _png_rgb(op["color"])
            width_px = max(1, int(round(op["width"] * scale)))
            points = [(sx(op["x1"]), sx(op["y1"])), (sx(op["x2"]), sx(op["y2"]))]
            dash = op.get("dash")
            if dash:
                _draw_dashed(draw, points, color, width_px,
                             dash[0] * scale, dash[1] * scale)
            else:
                draw.line(points, fill=color, width=width_px)
        elif kind == "polyline":
            color = _png_rgb(op["color"])
            width_px = max(1, int(round(op["width"] * scale)))
            points = [(sx(px), sx(py)) for px, py in op["pts"]]
            dash = op.get("dash")
            if dash:
                _draw_dashed(draw, points, color, width_px,
                             dash[0] * scale, dash[1] * scale)
            else:
                draw.line(points, fill=color, width=width_px, joint="curve")
        elif kind == "rect":
            box = [sx(op["x"]), sx(op["y"]),
                   sx(op["x"] + op["w"]), sx(op["y"] + op["h"])]
            fill = _png_rgb(op["fill"]) if op.get("fill") is not None else None
            outline = _png_rgb(op["stroke"]) if op.get("stroke") is not None else None
            width_px = max(1, int(round(op["width"] * scale))) if outline else 0
            draw.rectangle(box, fill=fill, outline=outline, width=width_px)
        elif kind == "circle":
            r = op["r"]
            box = [sx(op["cx"] - r), sx(op["cy"] - r),
                   sx(op["cx"] + r), sx(op["cy"] + r)]
            outline = _png_rgb(op["stroke"]) if op.get("stroke") is not None else None
            draw.ellipse(box, fill=_png_rgb(op["fill"]), outline=outline,
                         width=max(1, int(round(op["width"] * scale))) if outline else 0)
        elif kind == "text":
            text = op["text"]
            size_px = font(op["size"], op["bold"])
            x, y = sx(op["x"]), sx(op["y"])
            align = op.get("align", "left")
            try:
                if align != "left":
                    text_len = draw.textlength(text, font=size_px)
                    x -= text_len / 2.0 if align == "center" else text_len
            except (AttributeError, TypeError):  # pragma: no cover - bitmap fallback font
                pass
            try:
                draw.text((x, y), text, font=size_px, fill=_png_rgb(op["color"]),
                          anchor="ls")
            except (ValueError, TypeError):  # pragma: no cover - anchor unsupported
                draw.text((x, y - op["size"] * scale * 0.8), text,
                          font=size_px, fill=_png_rgb(op["color"]))
        else:  # pragma: no cover
            raise PlotDataError("unknown draw op: %r" % (kind,))

    folder = os.path.dirname(os.path.abspath(out_path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    image.save(out_path, format="PNG", dpi=(dpi, dpi))
    return px_w, px_h


# ── 三个对外渲染入口（契约 §7.3）────────────────────────────────────────────
def render_pdf(series: dict, out_path: str) -> None:
    """把序列写成单页矢量 PDF（手写 PDF 1.4，base-14 Helvetica）。"""
    width, height = page_size(series)
    ops = build_ops(series)
    _write_pdf(ops, width, height, out_path,
               title="Attack-Defense Co-evolution: Interception vs Benign False-Positive Rate",
               subject="run %s" % series.get("run_id", "unknown"),
               created_at=series.get("generated_at") or _now_iso())


def render_png(series: dict, out_path: str, dpi: int = DEFAULT_DPI) -> None:
    """把同一份绘制指令表栅格化成 300 dpi PNG（Pillow）。"""
    if not isinstance(dpi, int) or dpi <= 0:
        raise PlotDataError("render_png(): dpi must be a positive integer, got %r" % (dpi,))
    width, height = page_size(series)
    ops = build_ops(series)
    _write_png(ops, width, height, out_path, dpi)


def _figure_lines(series: dict) -> list[dict]:
    gens = series["generations"]
    values = {
        "baseline_zeroday": series["baseline_zeroday"],
        "evolved_zeroday": series["evolved_zeroday"],
        "benign_false_positive": series["benign_false_positive"],
        "plain_attack_baseline": series["plain_attack_baseline"],
    }
    labels_zh = {
        "baseline_zeroday": "基线防线 · held-out 变异集拦截率",
        "evolved_zeroday": "演化防线 · 同一 held-out 变异集拦截率",
        "plain_attack_baseline": "基线防线 · 明文攻击集拦截率（“本来能拦”的参照）",
        "benign_false_positive": "良性误伤率（演化防线）",
    }
    roles = {
        "baseline_zeroday": "baseline defense on held-out mutations "
                            "(held-out split of the same seed pool)",
        "evolved_zeroday": "evolved defense on the same held-out mutations",
        "plain_attack_baseline": "reference: baseline defense on the plaintext attack set",
        "benign_false_positive": "benign false-positive rate of the evolved defense",
    }
    source = series.get("plain_attack_baseline_source") or {}
    out = []
    for line_id in ("baseline_zeroday", "evolved_zeroday",
                    "plain_attack_baseline", "benign_false_positive"):
        field, filename = LINEFIELDS[line_id]
        entry = {
            "id": line_id,
            "label_en": LEGEND_STYLE[line_id][2],
            "label_zh": labels_zh[line_id],
            "role": roles[line_id],
            "source_file": filename,
            "source_field": field,
            "constant": line_id == "plain_attack_baseline",
            "values": [float(v) for v in values[line_id]],
            "points": [{"generation_id": int(g), "value": float(v)}
                       for g, v in zip(gens, values[line_id])],
        }
        if line_id == "plain_attack_baseline":
            entry["source_detail"] = source
            if source.get("kind") in ("computed", "explicit"):
                entry["source_file"] = (
                    _rel(os.path.join(ROOT, "tools", "phase_b_defense.py"))
                    if source.get("kind") == "computed" else None)
        out.append(entry)
    return out


def write_figure_data(series: dict, out_path: str, *, run_id: str,
                      caption_zh: str, caption_en: str) -> None:
    """写 `figure_data.json`：每条线的原始数值 + 来源字段名 + caption + 元数据。"""
    gens = [int(g) for g in series["generations"]]
    width, height = page_size(series)
    patches = series.get("patches") or {}
    plateau = series.get("plateau")
    if plateau is None:
        plateau = _plateau_analysis(series)
    limitations = list(LIMITATIONS)
    evidence_block = series.get("evidence") or {}
    protocol = evidence_block.get("zeroday_protocol")
    if protocol:
        limitations.append(
            "held-out evaluation-set protocol (from coevolution_evidence.json): %s; "
            "zeroday_generation_id=%r -- the mutation strength of the held-out set is fixed "
            "at generation 0, so movement of the red/blue curves reflects the defense, not a "
            "stronger attack stream. This run's zeroday set: %s payloads."
            % (protocol, evidence_block.get("zeroday_generation_id"),
               ((evidence_block.get("zeroday_set") or {}).get("count") or "n/a")))
    plateau_note = _plateau_limitation(plateau)
    if plateau_note:
        limitations.append(plateau_note)
    data = {
        "schema": SCHEMA,
        "run_id": run_id,
        "series_run_id": series.get("run_id"),
        "generated_at": series.get("generated_at") or _now_iso(),
        "generations": gens,
        "generation_count": len(gens),
        "patches_total": patches.get("patches_total"),
        "patch_ids_accepted": patches.get("patch_ids_accepted") or [],
        "patch_generated_total": patches.get("patch_generated_total"),
        "stop_reason": series.get("stop_reason"),
        "caption_zh": caption_zh,
        "caption_en": caption_en,
        "figure": {
            "page_pt": [width, height],
            "pdf_ascii_only": True,
            "pdf_font_family": "Helvetica (base-14)",
            "lines_plotted": 4,
            "inset": bool((series.get("layout") or {}).get("inset")),
        },
        "lines": _figure_lines(series),
        "context_series": {
            "evolved_mutated_intercept_rate": {
                "source_field": "intercept_rate_evolved",
                "source_file": LOG_NAME,
                "note": "evolved defense intercept rate on the current red-team mutation set; "
                        "plotted as context only, not one of the four contract lines",
                "values": series.get("evolved_mutated"),
            },
            "bypass_success_count": {
                "source_field": "bypass_success_count",
                "source_file": LOG_NAME,
                "values": series.get("bypass_success_count"),
            },
            "patches_total_per_generation": {
                "source_field": "patches_total",
                "source_file": LOG_NAME,
                "values": series.get("patches_series"),
            },
        },
        "fields": series.get("fields"),
        "fields_derived": series.get("fields_derived") or [],
        "plateau": plateau,
        "per_generation": series.get("per_generation"),
        "source": series.get("source"),
        "evidence": series.get("evidence"),
        "warnings": series.get("warnings") or [],
        "limitations": limitations,
        "api_calls": series.get("api_calls"),
        "api_tokens_used": series.get("api_tokens_used"),
    }
    folder = os.path.dirname(os.path.abspath(out_path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2))
        fh.write("\n")


# ── CLI（契约 §7.3）─────────────────────────────────────────────────────────
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="plot_coevolution_curve.py",
        description="Phase B A2: plot the attack-defense convergence curve "
                    "(hand-written vector PDF + Pillow PNG, no matplotlib).")
    parser.add_argument("--run", action="append", required=True, metavar="PATH",
                        help="run directory containing coevolution_log.jsonl "
                             "(or a .jsonl path); repeatable")
    parser.add_argument("--out", default=os.path.join("experiments", "phase-b", "figures"),
                        metavar="DIR", help="output directory (default: %(default)s)")
    parser.add_argument("--pdf", default=DEFAULT_PDF, help="PDF file name")
    parser.add_argument("--png", default=DEFAULT_PNG, help="PNG file name")
    parser.add_argument("--data", default=DEFAULT_DATA, help="figure_data.json file name")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI,
                        help="PNG resolution (default: %(default)s)")
    parser.add_argument("--inset", action="store_true",
                        help="draw the benign false-positive rate in a separate magnified "
                             "inset panel instead of on the main y axis")
    parser.add_argument("--caption-zh", default=None, help="override caption_zh")
    parser.add_argument("--caption-en", default=None, help="override caption_en")
    args = parser.parse_args(argv)

    try:
        records = load_log(list(args.run))
        series = build_series(records)
        series["layout"] = {"inset": bool(args.inset)}
    except PlotDataError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2

    out_dir = args.out if os.path.isabs(args.out) else os.path.join(ROOT, args.out)
    pdf_path = os.path.join(out_dir, args.pdf)
    png_path = os.path.join(out_dir, args.png)
    data_path = os.path.join(out_dir, args.data)
    # 图注带 run 级事实（run_id / 代数 / patch 数），与脚注相互印证；显式 --caption-* 时不追加。
    patch_count = (series.get("patches") or {}).get("patches_total")
    patch_text = "%d" % patch_count if isinstance(patch_count, int) else "n/a"
    if args.caption_zh is not None:
        caption_zh = args.caption_zh
    else:
        caption_zh = DEFAULT_CAPTION_ZH + (
            "（本图 run_id：%s；%d 代；%s patches；生成时间 %s。）"
            % (series["run_id"], len(series["generations"]), patch_text,
               series.get("generated_at")))
    if args.caption_en is not None:
        caption_en = args.caption_en
    else:
        caption_en = DEFAULT_CAPTION_EN + (
            " (run_id: %s; %d generations; %s patches; generated %s)"
            % (series["run_id"], len(series["generations"]), patch_text,
               series.get("generated_at")))

    try:
        render_pdf(series, pdf_path)
        render_png(series, png_path, dpi=args.dpi)
        write_figure_data(series, data_path, run_id=series["run_id"],
                          caption_zh=caption_zh, caption_en=caption_en)
    except PlotDataError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - I/O / Pillow failures
        print("ERROR: rendering failed: %s: %s" % (type(exc).__name__, exc),
              file=sys.stderr)
        return 1

    patches = series.get("patches") or {}
    print("run_id: %s" % series["run_id"])
    print("generations: %d (%s..%s)"
          % (len(series["generations"]), series["generations"][0],
             series["generations"][-1]))
    print("baseline_zeroday      (intercept_rate_baseline_zeroday) = %s"
          % _list_text(series["baseline_zeroday"]))
    print("evolved_zeroday       (intercept_rate_evolved_zeroday)  = %s"
          % _list_text(series["evolved_zeroday"]))
    print("benign_false_positive (benign_false_positive_rate)      = %s"
          % _list_text(series["benign_false_positive"]))
    print("plaintext_reference   (plain_attack_baseline_intercept_rate) = %s  [%s]"
          % (_fmt_rate(series["plain_attack_baseline_value"]),
             (series.get("plain_attack_baseline_source") or {}).get("kind")))
    plateau = series.get("plateau")
    if plateau:
        print("plateau (data, not smoothing): evolved_zeroday=%.4g constant from "
              "generation %d to %d (%d/%d generations); patches %s -> %s (+%s)"
              % (plateau["evolved_zeroday_value"],
                 plateau["plateau_start_generation"],
                 plateau["plateau_end_generation"],
                 plateau["generations_in_plateau"],
                 len(series["generations"]),
                 plateau["patches_at_plateau_start"], plateau["patches_at_end"],
                 plateau["patches_added_during_plateau"]))
    else:
        print("plateau (data, not smoothing): none (curve keeps moving)")
    print("patches_total: %s  stop_reason: %s"
          % (patches.get("patches_total"), series.get("stop_reason")))
    print("wrote %s (%d bytes)" % (pdf_path, os.path.getsize(pdf_path)))
    print("wrote %s (%d bytes)" % (png_path, os.path.getsize(png_path)))
    print("wrote %s (%d bytes)" % (data_path, os.path.getsize(data_path)))
    return 0


def _list_text(values: list) -> str:
    return "[" + ", ".join(_fmt_rate(v) if isinstance(v, float) else
                           ("n/a" if v is None else str(v)) for v in values) + "]"


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # pragma: no cover
            pass
    raise SystemExit(main())
