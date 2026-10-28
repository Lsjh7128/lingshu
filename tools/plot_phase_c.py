# -*- coding: utf-8 -*-
"""Phase C · A2 论文图：三系统对比（分组柱状图） + 能力雷达图

契约：`experiments/phase-c/CONTRACT.md` §8.1。数据来源**唯一**：

    experiments/phase-c/results/run-semantic.json   (Lead 的 tools/phase_c_eval.py 产出)
      - run_id / generated_at / branch           运行标识（run_id 是必备字段）
      - systems.{A,B,C}.E1.rate                E1 零日集拦截率（H0 指定，40 条）
      - systems.{A,B,C}.E0.rate                E0 新鲜 held-out 拦截率（新 seed，40 条）
      - systems.{A,B,C}.{E1,E0}.latency.p95_ms 完整判定（词法+语义）的 p95 延迟
      - systems.{A,B,C}.benign.fpr             良性语料（74 条）误伤率
      - systems.{A,B,C}.{E1,E0}.adjudicated.*  M/R/B 判定口径分组（M 必须拦 / R 策略应拦 /
                                               B 良性创作、拦了算误伤）
      - systems.{A,B,C}.E1.eligible_excluding_benign  应拦口径（E1 剔除 B 类，n=38；
                                               E0 无 B 类条目 → 该字段为 null，不是缺字段）
      - latency_budget.limit_ms / .pass        100 ms 红线及其判定
      - encoder.intent_count / .backend / .is_neural / .threshold
      - delta.C_minus_B_E1 / .C_minus_B_E0
      - eval_sets.E1/E0.{count,sha256}          集合口径与哈希

**缺字段 → 退出码 2，并在 stderr 点名缺的是哪个 JSON 路径**（`--` 不画空图、不手填、
不插值、不平滑）。数值一律按 JSON 原样画：拦截率 0.775 就画 0.775，不到 0.90 就不画成 0.90。

两张图：
1. `phase_c_comparison.pdf/.png`：分组柱状图（3 系统 × E1/E0 拦截率）
   + 并排小图：p95 延迟（ms，标注 100 ms 硬预算红线）与良性 FPR；
2. `phase_c_radar.pdf/.png`：雷达图（拦截率 / 1-FPR / 速度 1/p95 / 覆盖意图数 / 可解释性），
   三系统三色，图下方给出各轴原始数值表。
3. `figure_data.json`：两条图每条数据 + caption_zh/caption_en + 来源字段名 + 诚实边界。

环境事实（契约 §0）：本机**没有 matplotlib / reportlab**，所以 PDF 走本文件内的最小矢量
写入器（`%PDF-1.4` + 对象表 + xref + content stream：`BT /F1 8 Tf 1 0 0 1 x y Tm (...) Tj ET`
文本、`m/l/c/S` 折线、`re` 矩形、坐标轴、刻度、图例、标题、脚注），只用 base-14 Helvetica，
**图内文字一律可打印 ASCII/英文**（PDF 里中文字体不可靠），中文只进 `figure_data.json`
的 `caption_zh`。PNG 走 Pillow（默认 300 dpi）。两个后端消费**同一份绘制指令表**
（`comparison_ops` / `radar_ops`），所以 PDF 与 PNG 上的数据点、文字、颜色逐条同源。

PDF 原语（`_line` / `_rect` / `_polyline` / `_text` / `_pdf_content` / `_write_pdf` /
`_load_font` / `_write_png` 及 Helvetica 字宽表）复制自 Phase B 的
`tools/plot_coevolution_curve.py`（A2 前序交付，**本文件不改它**），本文件另加
`polygon` 绘制指令（雷达图填充需要）与 `circle` 填充支持。

诚实边界（写进 figure_data.json 的 limitations）：
- encoder 是**离线意图概念向量（offline, non-neural）**，不是神经句向量（本机无 torch /
  transformers / 网络 / Ollama）；
- E1/E0 都是 **held-out 变体，不是 OOD**；E0 只是新随机实现（新 seed），不是新攻击类型；
- 延迟是单机 numpy 实测（无并发、无网络），不代表生产端到端延迟；
- 雷达图后三个轴（速度 / 覆盖意图数 / 可解释性）单位不可通约，按"各轴跨系统最大值"
  归一化后画形状；原始数值在图下方表与 figure_data.json 里逐条给出，**不是**测量出来的
  综合评分；
- "可解释性"轴是**由 run-semantic.json 的判定输出 schema 派生的序数**
  （归因通道数），不是心理测量学分数；契约 §8.1 要求该轴但 §6.2 没有对应字段，
  这是已知契约缺口（本文件在 figure_data 的 `derived` 里写清推导规则）。

CLI：
    python tools/plot_phase_c.py \
        --run experiments/phase-c/results/run-semantic.json \
        --out experiments/phase-c/figures [--dpi 300] [--allow-missing-e0]
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
SCHEMA = "phase-c-figure-data-v1"
RUN_SCHEMA = "phase-c-run-semantic-v1"
RUN_NAME = "run-semantic.json"
DEFAULT_RUN = os.path.join("experiments", "phase-c", "results", RUN_NAME)
DEFAULT_OUT = os.path.join("experiments", "phase-c", "figures")
DEFAULT_DPI = 300
SYSTEMS = ("A", "B", "C")
PRIMARY_SET = "E1"          # H0 指定的零日集；雷达图拦截率轴用它
SECONDARY_SET = "E0"        # 新鲜 held-out

CMP_PDF = "phase_c_comparison.pdf"
CMP_PNG = "phase_c_comparison.png"
RADAR_PDF = "phase_c_radar.pdf"
RADAR_PNG = "phase_c_radar.png"
DATA_JSON = "figure_data.json"

# ── 版面（单位：pt，1/72 inch；y 轴向下，与绘图习惯一致）────────────────────
# 2x2 版面：左上=三系统总拦截率（E1/E0），右上=M 子集（显式跨语红线，最有力的证据），
# 左下=p95 延迟 + 100 ms 红线，右下=良性 FPR。
CMP_W, CMP_H = 504.0, 416.0
CMP_RATE = {"left": 64.0, "right": 270.0, "top": 74.0, "bottom": 240.0}
CMP_M = {"left": 316.0, "right": 486.0, "top": 74.0, "bottom": 240.0}
CMP_LAT = {"left": 64.0, "right": 270.0, "top": 290.0, "bottom": 366.0}
CMP_FPR = {"left": 316.0, "right": 486.0, "top": 290.0, "bottom": 366.0}
CMP_RATE_YMAX = 1.12
CMP_M_YMAX = 1.12
CMP_FOOTNOTE_Y = 402.0
#: 兼容旧测试/外部引用：总拦截率面板即主面板
CMP_MAIN = CMP_RATE

RADAR_W, RADAR_H = 504.0, 452.0
RADAR_CX, RADAR_CY, RADAR_R = 252.0, 214.0, 118.0
RADAR_TABLE_HEADER_Y = 384.0
RADAR_TABLE_ROWS_Y = (398.0, 410.0, 422.0)
RADAR_FOOTNOTE_Y = 442.0

# ── 颜色（PDF 里是 rg/RG 三元组，PNG 里换算成 0-255）────────────────────────
BLACK = (0.10, 0.10, 0.10)
AXIS = (0.20, 0.20, 0.20)
GRID = (0.86, 0.86, 0.86)
GRID_STRONG = (0.72, 0.72, 0.72)
RING = (0.60, 0.60, 0.60)
RED = (0.85, 0.10, 0.10)
BLUE = (0.05, 0.28, 0.75)
ORANGE = (0.93, 0.52, 0.05)
GRAY = (0.45, 0.45, 0.45)
TARGET = (0.15, 0.50, 0.25)
WHITE = (1.0, 1.0, 1.0)

#: 三系统样式：id -> (图例英文, 主色, 浅色填充)
SYS_STYLE = {
    "A": ("A: lexical gate", RED, (1.00, 0.87, 0.87)),
    "B": ("B: + evolved patch", ORANGE, (1.00, 0.92, 0.80)),
    "C": ("C: + semantic guard", BLUE, (0.86, 0.91, 1.00)),
}
SYS_SHORT = {"A": "lexical", "B": "self-heal", "C": "semantic"}
SYS_DESC = {
    "A": "S_A: Phase A lexical gate (phase_b_defense.scan, no patch)",
    "B": "S_B: S_A plus the Phase B official run's evolved patch snapshot",
    "C": "S_C: S_B or semantic_guard.is_risky (offline intent-concept encoder)",
}

#: 雷达图五个轴（契约 §8.1 指定的顺序）。`scale` 决定归一化方式：
#:   native          —— 该轴本身就是 [0,1] 比率，按原值画
#:   max_normalized  —— 单位不可通约（1/ms、个数），按"各轴跨系统最大值"归一化
RADAR_AXES = (
    {"id": "intercept_rate", "label": "intercept rate (E1)", "label_zh": "拦截率（E1）",
     "scale": "native",
     "source": "systems.{S}.E1.rate",
     "note": "H0 指定的零日集 E1 上的拦截率（E0 见柱状图），不做 E1/E0 平均"},
    {"id": "one_minus_fpr", "label": "1 - FPR", "label_zh": "1 - 误伤率",
     "scale": "native",
     "source": "1 - systems.{S}.benign.fpr",
     "note": "良性语料 74 条上的 1 减去误伤率"},
    {"id": "speed", "label": "speed 1/p95", "label_zh": "速度（1/p95）",
     "scale": "max_normalized",
     "source": "1 / systems.{S}.E1.latency.p95_ms",
     "note": "E1 上完整判定的 p95 延迟取倒数；按跨系统最大值归一化"},
    {"id": "intent_coverage", "label": "intent coverage", "label_zh": "覆盖意图数",
     "scale": "max_normalized",
     "source": "encoder.intent_count for C; 0 for A/B (no intent space exists)",
     "note": "C = run-semantic.json 的 encoder.intent_count；A/B 的词法规则/补丁没有"
             "意图空间，结构上为 0（该 0 是结构事实，不是测量）"},
    {"id": "explainability", "label": "explainability", "label_zh": "可解释性",
     "scale": "max_normalized",
     "source": "schema-derived attribution channels (see figure_data.derived)",
     "note": "归因通道数序数（A=1, B=1, C=3），由 run-semantic.json 的判定输出 schema "
             "派生；契约 §6.2 没有对应字段，属已知契约缺口"},
)

#: 可解释性轴的推导规则（写进 figure_data.derived，测试会断言这张表）
EXPLAINABILITY_RUBRIC = {
    "A": {"channels": 1,
          "rule": "1 attribution channel: rule-hit boolean (per-item rule id is not "
                  "recorded in run-semantic.json)"},
    "B": {"channels": 1,
          "rule": "1 attribution channel: rule/patch-hit boolean (which patch fired is not "
                  "recorded per item)"},
    "C": {"channels": 3,
          "rule": "3 attribution channels: hit boolean + semantic similarity score "
                  "(0-1) + matched intent id (plus the STPA UCA reason 'semantic_risk')"},
}
EXPLAINABILITY_NOTE = (
    "ordinate, NOT a measured psychometric score: counts the machine-readable attribution "
    "channels that the single evaluator (phase_c_eval.judge) exposes for each system. "
    "Contract 8.1 requires this axis but contract 6.2 defines no field for it; the ordinal "
    "is derived from the verdict schema and can be disputed by a reviewer."
)

#: run-semantic.json 必备字段（缺一即 exit 2，并点名缺的路径）
REQUIRED_PATHS = [
    "run_id",
    "encoder.backend",
    "encoder.is_neural",
    "encoder.intent_count",
    "encoder.threshold",
    "eval_sets.E1.count",
    "eval_sets.E1.sha256",
    "latency_budget.limit_ms",
    "latency_budget.pass",
]

LIMITATIONS = [
    "encoder is an offline intent-concept vector (non-neural): this machine has no torch / "
    "transformers / scikit-learn / network / Ollama, so no sentence-embedding model was run; "
    "the figure must not be cited as evidence for a neural semantic encoder.",
    "E1 and E0 are held-out variants of the same seed pool, NOT out-of-distribution data; "
    "E0 is a fresh random realisation (new seed), not a new attack family.",
    "thresholds are calibrated on D_dev only; E1/E0 are never used for tuning, and the "
    "intent library is a hand-authored multilingual artefact (author-knowledge bias).",
    "latency is a single-machine numpy measurement without concurrency or network; it is not "
    "production end-to-end latency.",
    "the radar axes speed / intent coverage / explainability have incommensurable units and "
    "are max-normalised across A/B/C to show shape; raw values are printed in the figure's "
    "table and in figure_data.json. The radar is not a composite score and no area is "
    "computed.",
    "the explainability axis is an ordinal derived from the verdict schema (attribution "
    "channel count), not a measured score; contract 8.1 requires the axis but contract 6.2 "
    "defines no field for it.",
    "E0 may be officially absent at figure-generation time; when generated with "
    "--allow-missing-e0 the figure shows E1 only and figure_data records the missing set "
    "explicitly -- it is never imputed.",
    "PDF is hand-written vector output (no matplotlib): base-14 Helvetica, ASCII text only; "
    "the Chinese caption lives only in this JSON file.",
]


# ── 错误类型（缺数据 → 退出码 2，见契约 §8.1）───────────────────────────────
class PlotDataError(SystemExit):
    """数据缺失 / 不一致。CLI 捕获后以退出码 2 结束（`--` 不画空图）。"""

    exit_code = 2

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


# ── 时间 / 路径 / 数值小工具 ───────────────────────────────────────────────
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


_MISSING = object()


def _walk(obj, path: str):
    """按点号路径取 JSON 值；任一级缺失返回 `_MISSING`。"""
    cur = obj
    for part in path.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return _MISSING
        cur = cur[part]
    return cur


def require(run: dict, path: str):
    """取必备字段；缺失 / None → `PlotDataError` 并点名路径（契约 §8.1）。"""
    value = _walk(run, path)
    if value is _MISSING or value is None:
        raise PlotDataError(
            "missing field %r in %s (contract 6.2 / 8.1); refusing to plot a hand-filled "
            "value" % (path, RUN_NAME))
    return value


def num(run: dict, path: str, *, lo: float | None = None,
        hi: float | None = None, gt: float | None = None) -> float:
    """取一个数值字段并校验范围（越界同样是数据不一致，exit 2）。"""
    value = require(run, path)
    if not _is_number(value):
        raise PlotDataError(
            "field %r in %s is not numeric: %r" % (path, RUN_NAME, value))
    value = float(value)
    if lo is not None and value < lo - 1e-9:
        raise PlotDataError(
            "field %r in %s is below %r: %r" % (path, RUN_NAME, lo, value))
    if hi is not None and value > hi + 1e-9:
        raise PlotDataError(
            "field %r in %s is above %r: %r" % (path, RUN_NAME, hi, value))
    if gt is not None and value <= gt:
        raise PlotDataError(
            "field %r in %s must be > %r: %r" % (path, RUN_NAME, gt, value))
    return value


def _int(run: dict, path: str, *, lo: int | None = None) -> int:
    value = require(run, path)
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlotDataError(
            "field %r in %s must be an integer: %r" % (path, RUN_NAME, value))
    if lo is not None and value < lo:
        raise PlotDataError(
            "field %r in %s is below %d: %r" % (path, RUN_NAME, lo, value))
    return value


def _has_set(run: dict, set_name: str) -> bool:
    rate = _walk(run, "systems.A.%s.rate" % set_name)
    return _is_number(rate)


def load_run_semantic(path: str, *, allow_missing_e0: bool = False) -> dict:
    """读 `run-semantic.json` 并做必备字段 / 集合哈希 / 比率一致性校验。"""
    if not path:
        raise PlotDataError("empty path given to --run")
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        raise PlotDataError(
            "%s not found: %s (wait for the Lead's tools/phase_c_eval.py)" % (RUN_NAME, path))
    with io.open(p, encoding="utf-8-sig") as fh:
        try:
            run = json.load(fh)
        except ValueError as exc:
            raise PlotDataError("invalid JSON in %s: %s" % (path, exc))
    if not isinstance(run, dict):
        raise PlotDataError("%s is not a JSON object: %s" % (RUN_NAME, path))

    schema = run.get("schema")
    if schema is not None and schema != RUN_SCHEMA:
        raise PlotDataError(
            "%s has schema %r, expected %r (refusing to plot an unknown run schema)"
            % (RUN_NAME, schema, RUN_SCHEMA))

    for path_ in REQUIRED_PATHS:
        require(run, path_)
    for system in SYSTEMS:
        require(run, "systems.%s.E1.rate" % system)
        require(run, "systems.%s.E1.latency.p95_ms" % system)
        require(run, "systems.%s.benign.fpr" % system)

    if allow_missing_e0:
        if _has_set(run, SECONDARY_SET):
            for system in SYSTEMS:
                require(run, "systems.%s.E0.rate" % system)
                require(run, "systems.%s.E0.latency.p95_ms" % system)
    else:
        for system in SYSTEMS:
            require(run, "systems.%s.E0.rate" % system)
            require(run, "systems.%s.E0.latency.p95_ms" % system)
        require(run, "eval_sets.%s.count" % SECONDARY_SET)
        require(run, "eval_sets.%s.sha256" % SECONDARY_SET)

    # 集合条数必须与 systems.*.n 一致（口径自洽，否则数字不可比）
    for set_name in (PRIMARY_SET, SECONDARY_SET):
        declared = _walk(run, "eval_sets.%s.count" % set_name)
        if not _is_number(declared):
            continue
        for system in SYSTEMS:
            n = _walk(run, "systems.%s.%s.n" % (system, set_name))
            if _is_number(n) and int(n) != int(declared):
                raise PlotDataError(
                    "inconsistent run: eval_sets.%s.count=%r but systems.%s.%s.n=%r"
                    % (set_name, declared, system, set_name, n))
    return run


# ── 数值一致性校验（"图上的数必须能从 JSON 复算"）──────────────────────────
def _check_rate_consistent(run: dict, name: str) -> None:
    rate = num(run, "systems.%s.rate" % name, lo=0.0, hi=1.0)
    intercepted = _int(run, "systems.%s.intercepted" % name, lo=0)
    n = _int(run, "systems.%s.n" % name, lo=1)
    if abs(rate - intercepted / n) > 1e-6:
        raise PlotDataError(
            "inconsistent run: systems.%s.rate=%r != intercepted/n=%d/%d"
            % (name, rate, intercepted, n))


def _check_fpr_consistent(run: dict, system: str) -> None:
    fpr = num(run, "systems.%s.benign.fpr" % system, lo=0.0, hi=1.0)
    fp_count = _int(run, "systems.%s.benign.fp_count" % system, lo=0)
    n = _int(run, "systems.%s.benign.n" % system, lo=1)
    if abs(fpr - fp_count / n) > 1e-6:
        raise PlotDataError(
            "inconsistent run: systems.%s.benign.fpr=%r != fp_count/n=%d/%d"
            % (system, fpr, fp_count, n))


# ── 图 1 数据：分组柱状图 ───────────────────────────────────────────────────
def build_comparison(run: dict, *, allow_missing_e0: bool = False) -> dict:
    """把 run-semantic.json 整理成柱状图要画的全部数值（零推导，除 1-FPR 外都是原字段）。"""
    e0_present = _has_set(run, SECONDARY_SET)
    if not e0_present and not allow_missing_e0:
        raise PlotDataError(
            "missing field %r in %s (contract 8.1 requires E1 and E0); pass "
            "--allow-missing-e0 only if the Lead confirms E0 was not produced"
            % ("systems.*.E0.rate", RUN_NAME))
    eval_sets = [PRIMARY_SET] + ([SECONDARY_SET] if e0_present else [])
    if not e0_present:
        for system in SYSTEMS:
            if _walk(run, "systems.%s.%s.rate" % (system, SECONDARY_SET)) is not _MISSING:
                raise PlotDataError(
                    "inconsistent run: systems.%s.E0 exists but systems.A.E0.rate is missing"
                    % system)

    bars: list[dict] = []
    latency: list[dict] = []
    for system in SYSTEMS:
        for set_name in eval_sets:
            _check_rate_consistent(run, "%s.%s" % (system, set_name))
            rate_path = "systems.%s.%s.rate" % (system, set_name)
            bars.append({
                "system": system,
                "eval_set": set_name,
                "rate": num(run, rate_path, lo=0.0, hi=1.0),
                "intercepted": _int(run, "systems.%s.%s.intercepted" % (system, set_name), lo=0),
                "n": _int(run, "systems.%s.%s.n" % (system, set_name), lo=1),
                "source_field": rate_path,
                "source_count_fields": [
                    "systems.%s.%s.intercepted" % (system, set_name),
                    "systems.%s.%s.n" % (system, set_name)],
            })
            lat = {}
            for key in ("p50_ms", "p95_ms"):
                lat[key] = num(run, "systems.%s.%s.latency.%s"
                               % (system, set_name, key), lo=0.0)
            mean = _walk(run, "systems.%s.%s.latency.mean_ms" % (system, set_name))
            lat["mean_ms"] = float(mean) if _is_number(mean) else None
            max_ms = _walk(run, "systems.%s.%s.latency.max_ms" % (system, set_name))
            latency.append({
                "system": system,
                "eval_set": set_name,
                "p50_ms": lat["p50_ms"],
                "p95_ms": lat["p95_ms"],
                "mean_ms": lat["mean_ms"],
                "max_ms": float(max_ms) if _is_number(max_ms) else None,
                "n": _int(run, "systems.%s.%s.latency.n" % (system, set_name), lo=1)
                if _is_number(_walk(run, "systems.%s.%s.latency.n" % (system, set_name)))
                else None,
                "source_field": "systems.%s.%s.latency.p95_ms" % (system, set_name),
            })
            if lat["p95_ms"] < lat["p50_ms"] - 1e-9:
                raise PlotDataError(
                    "inconsistent run: systems.%s.%s p95_ms (%r) < p50_ms (%r)"
                    % (system, set_name, lat["p95_ms"], lat["p50_ms"]))

    fpr = []
    for system in SYSTEMS:
        _check_fpr_consistent(run, system)
        fpr.append({
            "system": system,
            "fpr": num(run, "systems.%s.benign.fpr" % system, lo=0.0, hi=1.0),
            "fp_count": _int(run, "systems.%s.benign.fp_count" % system, lo=0),
            "n": _int(run, "systems.%s.benign.n" % system, lo=1),
            "source_field": "systems.%s.benign.fpr" % system,
            "source_count_fields": ["systems.%s.benign.fp_count" % system,
                                    "systems.%s.benign.n" % system],
        })

    # ── adjudicated M/R/B 分组（判定口径；字段由 Lead 冻结在 run-semantic.json 内）──
    adjudicated: list[dict] = []
    for system in SYSTEMS:
        for set_name in eval_sets:
            block = _walk(run, "systems.%s.%s.adjudicated" % (system, set_name))
            if not isinstance(block, dict):
                raise PlotDataError(
                    "missing field 'systems.%s.%s.adjudicated' in %s (contract 8.1 / "
                    "adjudication protocol)" % (system, set_name, RUN_NAME))
            total_n = next(b["n"] for b in bars
                           if b["system"] == system and b["eval_set"] == set_name)
            adjudicated_n = 0
            for subset in ("M", "R", "B"):
                entry = block.get(subset)
                if entry is None:
                    if subset == "B":
                        continue  # 该集合没有 B 类（良性创作）条目，属结构事实
                    raise PlotDataError(
                        "missing field 'systems.%s.%s.adjudicated.%s' in %s"
                        % (system, set_name, subset, RUN_NAME))
                path = "systems.%s.%s.adjudicated.%s" % (system, set_name, subset)
                n = _int(run, path + ".n", lo=0)
                k = _int(run, path + ".intercepted", lo=0)
                if n == 0:
                    raise PlotDataError("field %r has n=0; cannot normalise" % (path,))
                if k > n:
                    raise PlotDataError(
                        "inconsistent run: %s.intercepted=%d > n=%d" % (path, k, n))
                expected_rate = float(k) / float(n)
                declared_rate = _walk(run, path + ".rate")
                if _is_number(declared_rate) and abs(float(declared_rate)
                                                     - expected_rate) > 1e-6:
                    raise PlotDataError(
                        "inconsistent run: %s.rate=%r != intercepted/n=%r"
                        % (path, declared_rate, expected_rate))
                fp_count = entry.get("false_positive_count")
                if subset == "B" and _is_number(fp_count) and int(fp_count) != k:
                    raise PlotDataError(
                        "inconsistent run: %s.false_positive_count=%r != intercepted=%d"
                        % (path, fp_count, k))
                adjudicated_n += n
                adjudicated.append({
                    "system": system,
                    "eval_set": set_name,
                    "subset": subset,
                    "n": n,
                    "intercepted": k,
                    "rate": expected_rate,
                    "false_positive_count": (int(fp_count)
                                             if _is_number(fp_count) else None),
                    "source_field": path + ".intercepted",
                    "source_count_fields": [path + ".n", path + ".intercepted"],
                })
            if adjudicated_n > total_n:
                raise PlotDataError(
                    "inconsistent run: %s.%s adjudicated n=%d > n=%d"
                    % (system, set_name, adjudicated_n, total_n))

    # ── 应拦口径（E1 剔除良性创作；E0 为 null = 无需排除，不是缺字段）──
    eligible: list[dict] = []
    for system in SYSTEMS:
        for set_name in eval_sets:
            field = "systems.%s.%s.eligible_excluding_benign" % (system, set_name)
            value = _walk(run, field)
            # 该集合没有 B 类（良性创作）条目时，Lead 的评估器省略该键或写 null：
            # 两者都表示"无需排除"，不是缺字段（契约 §8.1 的 E0 情形）。
            if value is None or value is _MISSING:
                eligible.append({
                    "system": system, "eval_set": set_name, "n": None, "rate": None,
                    "intercepted": None, "excluded_benign_creations": None,
                    "source_field": field,
                    "note": "null in run-semantic.json: this set contains no "
                            "benign-creation (B) items, so nothing is excluded",
                })
                continue
            if not isinstance(value, dict):
                raise PlotDataError(
                    "field %r is neither an object nor null: %r" % (field, value))
            n = _int(run, field + ".n", lo=1)
            k = _int(run, field + ".intercepted", lo=0)
            rate = num(run, field + ".rate", lo=0.0, hi=1.0)
            if k > n:
                raise PlotDataError(
                    "inconsistent run: %s.intercepted=%d > n=%d" % (field, k, n))
            if abs(rate - float(k) / float(n)) > 1e-6:
                raise PlotDataError(
                    "inconsistent run: %s.rate=%r != intercepted/n=%r"
                    % (field, rate, float(k) / float(n)))
            excluded = value.get("excluded_benign_creations")
            total_n = next(b["n"] for b in bars
                           if b["system"] == system and b["eval_set"] == set_name)
            if _is_number(excluded) and n != total_n - int(excluded):
                raise PlotDataError(
                    "inconsistent run: %s.n=%d != total n=%d - excluded=%r"
                    % (field, n, total_n, excluded))
            eligible.append({
                "system": system, "eval_set": set_name, "n": n, "intercepted": k,
                "rate": rate,
                "excluded_benign_creations": (int(excluded)
                                              if _is_number(excluded) else None),
                "source_field": field + ".rate",
                "note": value.get("note"),
            })

    limit_ms = num(run, "latency_budget.limit_ms", gt=0.0)
    pass_flag = require(run, "latency_budget.pass")
    if not isinstance(pass_flag, bool):
        raise PlotDataError(
            "field 'latency_budget.pass' in %s is not a boolean: %r" % (RUN_NAME, pass_flag))
    worst_p95 = max(x["p95_ms"] for x in latency)
    worst_field = _walk(run, "latency_budget.worst_p95_ms")
    declared_worst = float(worst_field) if _is_number(worst_field) else None
    if declared_worst is not None and abs(declared_worst - worst_p95) > 1e-6:
        raise PlotDataError(
            "inconsistent run: latency_budget.worst_p95_ms=%r but max(p95_ms)=%r"
            % (declared_worst, worst_p95))
    # 红线判定必须与图上的数值一致（不许图里"通过"而数据不通过）
    if pass_flag != (worst_p95 < limit_ms):
        raise PlotDataError(
            "inconsistent run: latency_budget.pass=%r but max(p95_ms)=%r vs limit=%r"
            % (pass_flag, worst_p95, limit_ms))

    delta = {}
    for eval_set in eval_sets:
        key = "C_minus_B_%s" % eval_set
        value = _walk(run, "delta.%s" % key)
        if _is_number(value):
            c_rate = next(b["rate"] for b in bars
                          if b["system"] == "C" and b["eval_set"] == eval_set)
            b_rate = next(b["rate"] for b in bars
                          if b["system"] == "B" and b["eval_set"] == eval_set)
            if abs(float(value) - (c_rate - b_rate)) > 1e-6:
                raise PlotDataError(
                    "inconsistent run: delta.%s=%r != C-B=%r (E%s)"
                    % (key, value, c_rate - b_rate, eval_set))
            delta[key] = float(value)

    encoder = {
        "backend": require(run, "encoder.backend"),
        "is_neural": require(run, "encoder.is_neural"),
        "intent_count": _int(run, "encoder.intent_count", lo=1),
        "threshold": num(run, "encoder.threshold", lo=0.0, hi=1.0),
        "threshold_calibrated_on": _walk(run, "encoder.threshold_calibrated_on"),
        "intent_library_sha256": _walk(run, "encoder.intent_library_sha256"),
    }
    if not isinstance(encoder["backend"], str):
        raise PlotDataError("field 'encoder.backend' is not a string: %r"
                            % (encoder["backend"],))
    if not isinstance(encoder["is_neural"], bool):
        raise PlotDataError("field 'encoder.is_neural' is not a boolean: %r"
                            % (encoder["is_neural"],))

    eval_meta = {}
    for set_name in eval_sets:
        eval_meta[set_name] = {
            "count": _int(run, "eval_sets.%s.count" % set_name, lo=1),
            "sha256": _walk(run, "eval_sets.%s.sha256" % set_name),
            "path": _walk(run, "eval_sets.%s.path" % set_name),
            "source": _walk(run, "eval_sets.%s.source" % set_name),
        }
    eval_meta["benign"] = {
        "count": _int(run, "eval_sets.benign.count", lo=1)
        if _is_number(_walk(run, "eval_sets.benign.count")) else None,
        "path": _walk(run, "eval_sets.benign.path"),
    }

    return {
        "systems": list(SYSTEMS),
        "eval_sets": eval_sets,
        "e0_missing": not e0_present,
        "bars": bars,
        "latency": latency,
        "fpr": fpr,
        "adjudicated": adjudicated,
        "eligible": eligible,
        "budget": {
            "limit_ms": limit_ms,
            "pass": pass_flag,
            "worst_p95_ms": worst_p95,
            "declared_worst_p95_ms": worst_p95,
            "source_fields": ["latency_budget.limit_ms", "latency_budget.pass",
                              "latency_budget.worst_p95_ms"],
        },
        "delta": delta,
        "encoder": encoder,
        "eval_meta": eval_meta,
        "run": _run_meta(run),
    }


def _run_meta(run: dict) -> dict:
    """run 级标识：`run_id` 是 run-semantic.json 的必备字段（Lead 已补），缺失时退化为
    generated_at 派生标签（仅用于旧文件的容错，正式产物必须有 run_id）。"""
    run_id = run.get("run_id")
    generated_at = run.get("generated_at") or _now_iso()
    label = run_id
    if not isinstance(label, str) or not label:
        stamp = "".join(ch for ch in str(generated_at) if ch.isdigit())[:14]
        label = "phase-c-%s" % (stamp or "unknown")
    return {
        "run_id": label if isinstance(run_id, str) and run_id else None,
        "run_label": label,
        "run_id_source": "run-semantic.json:run_id" if run_id else
                         "derived from generated_at (run-semantic.json has no run_id field)",
        "generated_at": generated_at,
        "branch": run.get("branch"),
        "schema": run.get("schema"),
        "source_file": "experiments/phase-c/results/" + RUN_NAME,
        "generated_by": "tools/phase_c_eval.py",
    }


# ── 图 2 数据：雷达图 ───────────────────────────────────────────────────────
def build_radar(run: dict, comparison: dict) -> dict:
    """把 run-semantic.json 整理成雷达图数值（原始值 + 归一化值 + 推导规则）。

    不手填任何数：拦截率 / p95 / FPR / intent_count 都来自 JSON；
    "可解释性"是 schema 派生序数（规则写在 EXPLAINABILITY_RUBRIC）；A/B 的意图覆盖为 0
    是结构事实（词法系统没有意图空间），并写进 `derived`。
    """
    encoder = comparison["encoder"]
    intent_count = encoder["intent_count"]
    raw: dict[str, dict[str, float]] = {}
    for system in SYSTEMS:
        e1 = next(b for b in comparison["bars"]
                  if b["system"] == system and b["eval_set"] == PRIMARY_SET)
        lat = next(x for x in comparison["latency"]
                   if x["system"] == system and x["eval_set"] == PRIMARY_SET)
        benign = next(x for x in comparison["fpr"] if x["system"] == system)
        coverage_path = "systems.%s.intent_coverage" % system
        explicit_coverage = _walk(run, coverage_path)
        if _is_number(explicit_coverage):
            coverage, coverage_source = float(explicit_coverage), coverage_path
        elif system == "C":
            coverage, coverage_source = float(intent_count), "encoder.intent_count"
        else:
            coverage, coverage_source = 0.0, (
                "structural zero: %s has no intent space (no encoder field exists)"
                % system)
        explain_path = "systems.%s.explainability" % system
        explicit_explain = _walk(run, explain_path)
        if isinstance(explicit_explain, dict) and _is_number(
                explicit_explain.get("attribution_channels")):
            explain = float(explicit_explain["attribution_channels"])
            explain_source = explain_path + ".attribution_channels"
            explain_rule = "explicit field in run-semantic.json"
        elif _is_number(explicit_explain):
            explain, explain_source = float(explicit_explain), explain_path
            explain_rule = "explicit field in run-semantic.json"
        else:
            explain = float(EXPLAINABILITY_RUBRIC[system]["channels"])
            explain_source = "derived:EXPLAINABILITY_RUBRIC[%r]" % system
            explain_rule = EXPLAINABILITY_RUBRIC[system]["rule"]
        raw[system] = {
            "intercept_rate": e1["rate"],
            "one_minus_fpr": 1.0 - benign["fpr"],
            "speed": 1.0 / lat["p95_ms"],
            "intent_coverage": coverage,
            "explainability": explain,
        }
        raw[system]["_sources"] = {  # type: ignore[assignment]
            "intercept_rate": e1["source_field"],
            "one_minus_fpr": "1 - %s" % benign["source_field"],
            "speed": "1 / %s" % lat["source_field"],
            "intent_coverage": coverage_source,
            "explainability": explain_source,
        }
        raw[system]["_rules"] = {  # type: ignore[assignment]
            "intercept_rate": "native 0-1 field",
            "one_minus_fpr": "native 0-1 field (1 - FPR)",
            "speed": "1 / p95_ms on E1",
            "intent_coverage": ("encoder.intent_count" if system == "C" else
                                "0 by construction (lexical systems have no intent space)"),
            "explainability": explain_rule,
        }

    axes = []
    for axis in RADAR_AXES:
        axis_id = axis["id"]
        values = {s: raw[s][axis_id] for s in SYSTEMS}
        raw_max = max(values.values())
        if axis["scale"] == "max_normalized":
            if raw_max <= 0:
                raise PlotDataError(
                    "cannot normalise radar axis %r: max raw value is %r across A/B/C"
                    % (axis_id, raw_max))
            normalized = {s: values[s] / raw_max for s in SYSTEMS}
        else:
            for system in SYSTEMS:
                if not (-1e-9 <= values[system] <= 1.0 + 1e-9):
                    raise PlotDataError(
                        "radar axis %r is native-0-1 but raw value for %s is %r"
                        % (axis_id, system, values[system]))
            raw_max = 1.0
            normalized = {s: min(1.0, max(0.0, values[s])) for s in SYSTEMS}
        axes.append({
            "id": axis_id,
            "label_en": axis["label"],
            "label_zh": axis["label_zh"],
            "scale": axis["scale"],
            "raw_max": raw_max,
            "raw": values,
            "normalized": normalized,
            "source_fields": {s: raw[s]["_sources"][axis_id] for s in SYSTEMS},
            "rules": {s: raw[s]["_rules"][axis_id] for s in SYSTEMS},
            "note": axis["note"],
        })

    return {
        "systems": list(SYSTEMS),
        "axes": axes,
        "encoder": encoder,
        "eval_set": PRIMARY_SET,
        "derived": {
            "explainability_rubric": EXPLAINABILITY_RUBRIC,
            "explainability_note": EXPLAINABILITY_NOTE,
            "intent_coverage_rule": (
                "C = encoder.intent_count from run-semantic.json; A and B = 0 because the "
                "lexical rule/patch systems have no intent space at all. The zero is a "
                "structural fact, not a measurement."),
        },
        "run": comparison["run"],
    }


# ── 数值格式化 ──────────────────────────────────────────────────────────────
def _fmt_rate(value: float) -> str:
    return "%.3f" % float(value)


def _fmt_ms(value: float) -> str:
    return "%.2f" % float(value)


def _fmt_num(value: float) -> str:
    if abs(value - round(value)) < 1e-9:
        return "%d" % int(round(value))
    return "%.2f" % float(value)


# ── 绘制指令原语（PDF 与 PNG 共用同一张指令表）──────────────────────────────
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


def _polygon(points, fill, stroke=None, width=1.0, role="deco"):
    return {"op": "polygon", "pts": [(float(a), float(b)) for a, b in points],
            "fill": fill, "stroke": stroke, "width": float(width), "role": role}


def _text(x, y, text, size=8.0, bold=False, align="left", color=BLACK, role="deco"):
    for ch in text:
        if not 32 <= ord(ch) <= 126:
            raise PlotDataError(
                "figure text must be printable ASCII (PDF base-14 only): %r" % (text,))
    return {"op": "text", "x": float(x), "y": float(y), "text": text,
            "size": float(size), "bold": bool(bold), "align": align,
            "color": color, "role": role}


def _fit_text(text: str, size: float, max_width: float, bold: bool = False,
              min_size: float = 8.0) -> float:
    """标题过长时按 0.5pt 递减，但不低于 8pt（PDF 校验器的字号地板）。"""
    while size > min_size and _text_width(text, size, bold) > max_width:
        size -= 0.5
    return size


# ── 图 1：分组柱状图 ───────────────────────────────────────────────────────
def comparison_ops(cd: dict) -> list[dict]:
    """2x2 面板：总拦截率（E1/E0）、M 子集（显式跨语红线）、p95 延迟（100 ms 红线）、良性 FPR。

    右上角的 M 子集面板是本轮最有力的证据（契约 §8.1 要求三系统 × 两集合拦截率 +
    延迟 + FPR；M 子集是 Lead 冻结的判定口径，字段 `systems.*.*.adjudicated.M`）。
    "应拦口径"（E1 剔除 2 条良性创作，n=38）以一行文字注记给出，数值来自
    `systems.*.E1.eligible_excluding_benign`；E0 无 B 类条目，故该字段为 null（不是缺字段）。
    """
    ops: list[dict] = []
    systems = cd["systems"]
    eval_sets = cd["eval_sets"]
    by_key = {(b["system"], b["eval_set"]): b for b in cd["bars"]}
    lat_by_key = {(x["system"], x["eval_set"]): x for x in cd["latency"]}
    fpr_by_sys = {x["system"]: x for x in cd["fpr"]}
    adj_by_key = {(a["system"], a["eval_set"], a["subset"]): a
                  for a in cd["adjudicated"]}

    # ── 标题 / 副标题 / 图例 / C-B 增量 ──
    title = "Phase C: Three Systems on Held-out Variants"
    ops.append(_text(CMP_W / 2.0, 22.0, title,
                     _fit_text(title, 12.0, CMP_W - 60.0, True), bold=True,
                     align="center", color=BLACK, role="title"))
    subtitle = ("A: lexical gate | B: + Phase B evolved patch | "
                "C: + semantic guard (offline intent-concept encoder, non-neural)")
    ops.append(_text(CMP_W / 2.0, 36.0, subtitle,
                     _fit_text(subtitle, 8.0, CMP_W - 40.0), align="center",
                     color=AXIS, role="subtitle"))
    legend_right = CMP_W - 16.0
    ops.append(_rect(legend_right - 190.0, 46.0, 11.0, 9.0, fill=BLACK,
                     role="legend-sample"))
    ops.append(_text(legend_right - 175.0, 52.0, "E1: zero-day set", 8, color=BLACK,
                     role="legend-text"))
    ops.append(_rect(legend_right - 76.0, 46.0, 11.0, 9.0, fill=GRID_STRONG,
                     stroke=AXIS, width=0.8, role="legend-sample"))
    ops.append(_text(legend_right - 61.0, 52.0, "E0: fresh held-out", 8, color=BLACK,
                     role="legend-text"))
    if "C_minus_B_E1" in cd["delta"]:
        parts = []
        for set_name in eval_sets:
            key = "C_minus_B_%s" % set_name
            if key in cd["delta"]:
                parts.append("%s %+.3f" % (set_name, cd["delta"][key]))
        if parts:
            ops.append(_text(CMP_RATE["left"], 52.0, "C - B: " + " / ".join(parts),
                             8, color=BLUE, role="delta-note"))

    def panel_frame(geom: dict, ymax: float, ticks, title_text: str,
                    label_fmt=_fmt_rate):
        """画一个带网格/刻度/轴标题的坐标框，返回 y(value) 映射。"""
        left, right = geom["left"], geom["right"]
        top, bottom = geom["top"], geom["bottom"]

        def y_of(value: float) -> float:
            return bottom - float(value) / ymax * (bottom - top)

        for value in ticks:
            y = y_of(value)
            if value > 0:
                ops.append(_line(left, y, right, y, GRID, 0.5, role="panel-grid"))
            ops.append(_text(left - 6.0, y + 2.6, label_fmt(value), 8, align="right",
                             color=AXIS, role="panel-ticklabel"))
        ops.append(_line(left, top, left, bottom, AXIS, 0.9, role="panel-yaxis"))
        ops.append(_line(left, bottom, right, bottom, AXIS, 0.9, role="panel-xaxis"))
        ops.append(_text(left, top - 6.0, title_text, 8, color=AXIS,
                         role="panel-title"))
        return y_of

    # ── 面板 1（左上）：总拦截率，A/B/C × E1/E0 ──
    geom = CMP_RATE
    y_rate = panel_frame(geom, CMP_RATE_YMAX, (0.0, 0.25, 0.5, 0.75, 1.0),
                         "interception rate (held-out)")
    y_target = y_rate(0.90)
    ops.append(_line(geom["left"], y_target, geom["right"], y_target, TARGET, 0.9,
                     dash=(2.0, 2.0), role="target-line"))
    ops.append(_text(geom["left"] + 3.0, y_target - 4.0, "H0 target: > 0.90 on E1",
                     8, color=TARGET, role="target-label"))
    group_w = (geom["right"] - geom["left"]) / float(len(systems))
    bar_w, bar_gap = 28.0, 5.0
    pair_w = len(eval_sets) * bar_w + (len(eval_sets) - 1) * bar_gap
    for index, system in enumerate(systems):
        cx = geom["left"] + (index + 0.5) * group_w
        ops.append(_text(cx, geom["bottom"] + 13.0, system, 9, bold=True,
                         align="center", color=BLACK, role="panel-x-ticklabel"))
        for j, set_name in enumerate(eval_sets):
            item = by_key[(system, set_name)]
            x = cx - pair_w / 2.0 + j * (bar_w + bar_gap)
            y_top = y_rate(item["rate"])
            colour = (SYS_STYLE[system][1] if set_name == PRIMARY_SET
                      else SYS_STYLE[system][2])
            ops.append(_rect(x, y_top, bar_w, geom["bottom"] - y_top, fill=colour,
                             stroke=(SYS_STYLE[system][1]
                                     if set_name != PRIMARY_SET else None),
                             width=0.8, role="bar:%s:%s" % (system, set_name)))
            ops.append(_text(x + bar_w / 2.0, y_top - 5.0, _fmt_rate(item["rate"]),
                             8, align="center", color=BLACK,
                             role="bar-label:%s:%s" % (system, set_name)))

    # ── 面板 2（右上）：M 子集（显式跨语红线，必须拦）──
    geom_m = CMP_M
    y_m = panel_frame(geom_m, CMP_M_YMAX, (0.0, 0.25, 0.5, 0.75, 1.0),
                      "explicit red lines (M subset)")
    group_w = (geom_m["right"] - geom_m["left"]) / float(len(systems))
    bar_w, bar_gap = 24.0, 4.0
    pair_w = len(eval_sets) * bar_w + (len(eval_sets) - 1) * bar_gap
    for index, system in enumerate(systems):
        cx = geom_m["left"] + (index + 0.5) * group_w
        ops.append(_text(cx, geom_m["bottom"] + 13.0, system, 9, bold=True,
                         align="center", color=BLACK, role="panel-x-ticklabel"))
        for j, set_name in enumerate(eval_sets):
            item = adj_by_key[(system, set_name, "M")]
            x = cx - pair_w / 2.0 + j * (bar_w + bar_gap)
            y_top = y_m(item["rate"])
            colour = (SYS_STYLE[system][1] if set_name == PRIMARY_SET
                      else SYS_STYLE[system][2])
            ops.append(_rect(x, y_top, bar_w, max(1.0, geom_m["bottom"] - y_top),
                             fill=colour,
                             stroke=(SYS_STYLE[system][1]
                                     if set_name != PRIMARY_SET else None),
                             width=0.8,
                             role="m-bar:%s:%s" % (system, set_name)))
            ops.append(_text(x + bar_w / 2.0, y_top - 5.0,
                             "%d/%d" % (item["intercepted"], item["n"]), 8,
                             align="center", color=BLACK,
                             role="m-bar-label:%s:%s" % (system, set_name)))

    # ── 应拦口径注记：E1 剔除 2 条良性创作（n=38）──
    eligible_e1 = {a["system"]: a for a in cd["eligible"]
                   if a["eval_set"] == PRIMARY_SET}
    if len(eligible_e1) == len(systems):
        first = eligible_e1[systems[0]]
        pieces = " | ".join("%s %s" % (s, _fmt_rate(eligible_e1[s]["rate"]))
                            for s in systems)
        note = ("E1 excluding %d benign-creation items (n=%d): %s"
                % (first["excluded_benign_creations"], first["n"], pieces))
        ops.append(_text(CMP_RATE["left"], 266.0, note, 8, color=AXIS,
                         role="eligible-note"))

    # ── 面板 3（左下）：p95 延迟 + 100 ms 红线 ──
    geom = CMP_LAT
    limit = cd["budget"]["limit_ms"]
    max_p95 = max(x["p95_ms"] for x in cd["latency"])
    ymax = max(limit, max_p95) * 1.25
    if ymax <= 0:
        raise PlotDataError("latency budget limit is not positive: %r" % (limit,))
    ticks = [ymax * step / 5.0 for step in range(6)]
    y_lat = panel_frame(geom, ymax, ticks, "p95 judgment latency (ms)",
                        label_fmt=lambda v: "%.0f" % v)
    y_limit = y_lat(limit)
    ops.append(_line(geom["left"], y_limit, geom["right"], y_limit, RED, 1.4,
                     dash=(4.0, 2.5), role="budget-line"))
    ops.append(_text(geom["left"] + 2.0, y_limit - 4.0,
                     "%d ms budget" % int(round(limit)), 8, color=RED,
                     role="budget-label"))
    group_w = (geom["right"] - geom["left"]) / float(len(systems))
    bar_w, bar_gap = 28.0, 5.0
    pair_w = len(eval_sets) * bar_w + (len(eval_sets) - 1) * bar_gap
    for index, system in enumerate(systems):
        cx = geom["left"] + (index + 0.5) * group_w
        ops.append(_text(cx, geom["bottom"] + 13.0, system, 9, bold=True,
                         align="center", color=BLACK, role="panel-x-ticklabel"))
        for j, set_name in enumerate(eval_sets):
            item = lat_by_key[(system, set_name)]
            x = cx - pair_w / 2.0 + j * (bar_w + bar_gap)
            y_top = y_lat(item["p95_ms"])
            colour = (SYS_STYLE[system][1] if set_name == PRIMARY_SET
                      else SYS_STYLE[system][2])
            ops.append(_rect(x, y_top, bar_w, max(1.0, geom["bottom"] - y_top),
                             fill=colour,
                             stroke=(SYS_STYLE[system][1]
                                     if set_name != PRIMARY_SET else None),
                             width=0.8,
                             role="panel-bar:%s:%s" % (system, set_name)))
            ops.append(_text(x + bar_w / 2.0, y_top - 4.0, _fmt_ms(item["p95_ms"]),
                             8, align="center", color=BLACK,
                             role="panel-bar-label:%s:%s" % (system, set_name)))

    # ── 面板 4（右下）：良性 FPR ──
    geom = CMP_FPR
    max_fpr = max(x["fpr"] for x in cd["fpr"])
    fpr_ymax = max(0.05, max_fpr * 1.25)
    ticks_fpr = [fpr_ymax * step / 2.0 for step in range(3)]
    y_fpr = panel_frame(geom, fpr_ymax, ticks_fpr,
                        "benign FPR (n=%s)" % (
                            cd["eval_meta"].get("benign", {}).get("count") or "n/a"))
    group_w = (geom["right"] - geom["left"]) / float(len(systems))
    bar_w = 36.0
    for index, system in enumerate(systems):
        cx = geom["left"] + (index + 0.5) * group_w
        item = fpr_by_sys[system]
        y_top = y_fpr(item["fpr"])
        ops.append(_rect(cx - bar_w / 2.0, y_top, bar_w,
                         max(1.0, geom["bottom"] - y_top),
                         fill=SYS_STYLE[system][1], role="panel-bar:fpr:%s" % system))
        ops.append(_text(cx, y_top - 4.0, _fmt_rate(item["fpr"]), 8, align="center",
                         color=BLACK, role="panel-bar-label:fpr:%s" % system))
        ops.append(_text(cx, geom["bottom"] + 13.0, system, 9, bold=True,
                         align="center", color=BLACK, role="panel-x-ticklabel"))
        ops.append(_text(cx, geom["bottom"] + 24.0,
                         "%d/%d" % (item["fp_count"], item["n"]), 8,
                         align="center", color=AXIS, role="panel-x-ticklabel"))

    # ── 脚注（必须以 "source:" 开头：仓库 PDF 校验器允许的唯一 6 pt 文本）──
    run = cd["run"]
    encoder = cd["encoder"]
    e1_sha = (cd["eval_meta"].get("E1") or {}).get("sha256") or "n/a"
    footnote = ("source: %s | run %s | encoder %s, is_neural=%s, thr %s (%s) | "
                "E1 sha %s"
                % (RUN_NAME, run["run_label"], encoder["backend"],
                   str(encoder["is_neural"]).lower(), _fmt_num(encoder["threshold"]),
                   encoder["threshold_calibrated_on"] or "n/a", str(e1_sha)[:8]))
    ops.append(_text(8.0, CMP_FOOTNOTE_Y, footnote, 6, color=AXIS, role="footnote"))
    return ops


# ── 图 2：雷达图 ───────────────────────────────────────────────────────────
def radar_axis_angles(count: int) -> list[float]:
    """从正上方开始、顺时针的轴角度（度）。"""
    return [90.0 - 360.0 * index / float(count) for index in range(count)]


def radar_ops(rd: dict) -> list[dict]:
    """五轴雷达图：拦截率 / 1-FPR / 速度 / 覆盖意图数 / 可解释性，三系统三色。"""
    ops: list[dict] = []
    systems = rd["systems"]
    axes = rd["axes"]
    angles = radar_axis_angles(len(axes))

    title = "Phase C: Three-System Capability Profile (E1 held-out)"
    ops.append(_text(RADAR_W / 2.0, 22.0, title,
                     _fit_text(title, 12.0, RADAR_W - 60.0, True), bold=True,
                     align="center", color=BLACK, role="title"))
    subtitle = ("axes 1-2: native 0-1 scale | axes 3-5: max-normalised across A/B/C | "
                "raw values in the table below")
    ops.append(_text(RADAR_W / 2.0, 38.0, subtitle,
                     _fit_text(subtitle, 8.0, RADAR_W - 30.0), align="center",
                     color=AXIS, role="subtitle"))

    # 系统图例
    legend_x = 30.0
    for system in systems:
        color = SYS_STYLE[system][1]
        ops.append(_rect(legend_x, 49.0, 11.0, 9.0, fill=color, role="legend-sample"))
        ops.append(_text(legend_x + 15.0, 56.0, SYS_STYLE[system][0], 8, color=BLACK,
                         role="legend-text"))
        legend_x += 165.0

    cx, cy, radius = RADAR_CX, RADAR_CY, RADAR_R

    def point(angle: float, value: float) -> tuple[float, float]:
        rad = math.radians(angle)
        return (cx + radius * value * math.cos(rad),
                cy - radius * value * math.sin(rad))

    # 1) 三系统浅色填充（先画填充：没有 alpha 通道，所以网格与描边必须后画，否则被盖住。
    #    按多边形面积从大到小画，保证小图形不被大图形整块盖住）
    def _polygon_area(system: str) -> float:
        values = [axis["normalized"][system] for axis in axes]
        pts = [point(angle, value) for angle, value in zip(angles, values)]
        area = 0.0
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
            area += x1 * y2 - x2 * y1
        return abs(area) / 2.0

    fill_order = sorted(systems, key=_polygon_area, reverse=True)
    for system in fill_order:
        values = [axis["normalized"][system] for axis in axes]
        pts = [point(angle, value) for angle, value in zip(angles, values)]
        ops.append(_polygon(pts, fill=SYS_STYLE[system][2], stroke=None,
                            role="radar-fill:%s" % system))

    # 2) 网格环（0.25 / 0.5 / 0.75 / 1.0）
    for fraction in (0.25, 0.5, 0.75, 1.0):
        pts = [point(angle, fraction) for angle in angles]
        ops.append(_polygon(pts, fill=None, stroke=RING,
                            width=1.0 if fraction == 1.0 else 0.6,
                            role="radar-ring"))
    ops.append(_text(cx + 4.0, point(90.0, 0.5)[1] + 3.0, "0.5", 8, color=AXIS,
                     role="ring-label"))
    ops.append(_text(cx + 4.0, point(90.0, 1.0)[1] + 3.0, "1.0", 8, color=AXIS,
                     role="ring-label"))

    # 3) 轴线与轴标签
    for axis, angle in zip(axes, angles):
        outer = point(angle, 1.0)
        ops.append(_line(cx, cy, outer[0], outer[1], AXIS, 0.9, role="radar-axis"))
        rad = math.radians(angle)
        label_x = cx + radius * 1.13 * math.cos(rad)
        label_y = cy - radius * 1.13 * math.sin(rad) + 3.0
        cos_v = math.cos(rad)
        if cos_v > 0.30:
            align = "left"
        elif cos_v < -0.30:
            align = "right"
        else:
            align = "center"
        label = axis["label_en"]
        if axis["scale"] == "max_normalized":
            label = "%s (max %s)" % (label, _fmt_num(axis["raw_max"]))
        size = _fit_text(label, 8.0, 130.0)
        ops.append(_text(label_x, label_y, label, size, align=align, color=BLACK,
                         role="radar-axis-label"))

    # 4) 三系统多边形描边 + 顶点
    for system in systems:
        values = [axis["normalized"][system] for axis in axes]
        pts = [point(angle, value) for angle, value in zip(angles, values)]
        ops.append(_polygon(pts, fill=None, stroke=SYS_STYLE[system][1], width=1.6,
                            role="radar-polygon:%s" % system))
    for system in systems:
        values = [axis["normalized"][system] for axis in axes]
        for angle, value in zip(angles, values):
            vx, vy = point(angle, value)
            ops.append(_circle(vx, vy, 2.4, SYS_STYLE[system][1],
                               role="radar-marker:%s" % system))

    # 图下方：各轴原始数值表（归一化只是画形状，原始数必须可见）
    label_x = 36.0
    column_x = (96.0, 176.0, 256.0, 336.0, 416.0)
    header = ["intercept", "1 - FPR", "1/p95", "intents", "explain"]
    ops.append(_text(label_x, RADAR_TABLE_HEADER_Y, "sys", 8, bold=True, color=AXIS,
                     role="table-header"))
    for text_value, x in zip(header, column_x):
        ops.append(_text(x, RADAR_TABLE_HEADER_Y, text_value, 8, bold=True,
                         align="center", color=AXIS, role="table-header"))
    for row, (system, row_y) in enumerate(zip(systems, RADAR_TABLE_ROWS_Y)):
        ops.append(_text(label_x, row_y, system, 9, bold=True, color=SYS_STYLE[system][1],
                         role="table-row-label"))
        for axis, x in zip(axes, column_x):
            value = axis["raw"][system]
            if axis["id"] in ("intent_coverage", "explainability"):
                text_value = _fmt_num(value)
            elif axis["id"] == "speed":
                text_value = "%.2f" % value
            else:
                text_value = _fmt_rate(value)
            ops.append(_text(x, row_y, text_value, 8, align="center", color=BLACK,
                             role="table-cell:%s:%s" % (system, axis["id"])))

    run = rd["run"]
    footnote = ("source: %s | run %s | encoder %s, is_neural=%s, thr %s (%s)"
                % (RUN_NAME, run["run_label"], rd["encoder"]["backend"],
                   str(rd["encoder"]["is_neural"]).lower(),
                   _fmt_num(rd["encoder"]["threshold"]),
                   rd["encoder"]["threshold_calibrated_on"] or "n/a"))
    ops.append(_text(8.0, RADAR_FOOTNOTE_Y, footnote, 6, color=AXIS, role="footnote"))
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

    def paint(fill, stroke, width, d, close):
        if fill is not None:
            out.append("%s rg" % rgb(fill))
        if stroke is not None:
            out.append("%s RG" % rgb(stroke))
            out.append("%s w" % _pdf_num(width))
            out.append(dash(d))
        if close:
            out.append("h")
        if fill is not None and stroke is not None:
            out.append("B")
        elif fill is not None:
            out.append("f")
        else:
            out.append("S")

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
        elif kind == "polygon":
            pts = op["pts"]
            seg = ["%s %s m" % (_pdf_num(pts[0][0]), _pdf_num(fy(pts[0][1])))]
            for px, py in pts[1:]:
                seg.append("%s %s l" % (_pdf_num(px), _pdf_num(fy(py))))
            out.append(" ".join(seg))
            paint(op.get("fill"), op.get("stroke"), op.get("width", 1.0),
                  op.get("dash"), close=True)
        elif kind == "rect":
            x, y, w, h = op["x"], op["y"], op["w"], op["h"]
            out.append("%s %s %s %s re"
                       % (_pdf_num(x), _pdf_num(fy(y + h)), _pdf_num(w), _pdf_num(h)))
            paint(op.get("fill"), op.get("stroke"), op.get("width", 1.0),
                  op.get("dash"), close=False)
        elif kind == "circle":
            cx, cy, r = op["cx"], op["cy"], op["r"]
            k = 0.5523 * r
            cyf = fy(cy)
            if op.get("stroke") is not None:
                out.append("%s RG" % rgb(op["stroke"]))
                out.append("%s w" % _pdf_num(op["width"]))
            out.append("%s rg" % rgb(op["fill"]))
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
        7: ("<< /Producer (LINGSHU Phase C A2 plot_phase_c.py) "
            "/Title (%s) /Subject (%s) /CreationDate (%s) >>"
            % (_pdf_escape(title), _pdf_escape(subject),
               _pdf_date(created_at))).encode("latin-1"),
    }
    out = bytearray(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    offsets: dict[int, int] = {}
    for num_ in sorted(objs):
        offsets[num_] = len(out)
        out += b"%d 0 obj\n" % num_ + objs[num_] + b"\nendobj\n"
    xref_at = len(out)
    count = max(objs) + 1
    out += b"xref\n0 %d\n" % count
    out += b"0000000000 65535 f \n"
    for num_ in range(1, count):
        out += b"%010d 00000 n \n" % offsets[num_]
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
        elif kind == "polygon":
            points = [(sx(px), sx(py)) for px, py in op["pts"]]
            fill = _png_rgb(op["fill"]) if op.get("fill") is not None else None
            outline = _png_rgb(op["stroke"]) if op.get("stroke") is not None else None
            if fill is not None:
                draw.polygon(points, fill=fill)
            if outline is not None:
                closed = points + [points[0]]
                width_px = max(1, int(round(op.get("width", 1.0) * scale)))
                draw.line(closed, fill=outline, width=width_px)
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


# ── 渲染入口 ───────────────────────────────────────────────────────────────
def render_comparison(cd: dict, pdf_path: str, png_path: str, *,
                      dpi: int = DEFAULT_DPI) -> None:
    ops = comparison_ops(cd)
    run = cd["run"]
    _write_pdf(ops, CMP_W, CMP_H, pdf_path,
               title="Phase C: Three Systems on Held-out Variants",
               subject="run %s" % run["run_label"],
               created_at=run["generated_at"])
    _write_png(ops, CMP_W, CMP_H, png_path, dpi)


def render_radar(rd: dict, pdf_path: str, png_path: str, *,
                 dpi: int = DEFAULT_DPI) -> None:
    ops = radar_ops(rd)
    run = rd["run"]
    _write_pdf(ops, RADAR_W, RADAR_H, pdf_path,
               title="Phase C: Three-System Capability Profile",
               subject="run %s" % run["run_label"],
               created_at=run["generated_at"])
    _write_png(ops, RADAR_W, RADAR_H, png_path, dpi)


# ── figure_data.json ──────────────────────────────────────────────────────
def default_captions(cd: dict, rd: dict) -> tuple[str, str]:
    """按 run 事实生成 caption：所有数字都从 cd 里取（不手填），编码器名称来自 JSON。"""
    enc = cd["encoder"]
    neural_text_zh = "离线意图概念向量（offline intent-concept, non-neural）" \
        if enc["is_neural"] is False else "神经编码器（is_neural=true）"
    neural_text_en = "offline intent-concept encoder (non-neural)" \
        if enc["is_neural"] is False else "neural encoder (is_neural=true)"
    e0_zh = "" if not cd["e0_missing"] else "；E0 本次未生成，图中只有 E1（未插补）"
    e0_en = "" if not cd["e0_missing"] else \
        "; E0 was not produced for this run, so only E1 is shown (no imputation)"

    adj = {(a["system"], a["eval_set"], a["subset"]): a for a in cd["adjudicated"]}
    m_e1 = adj[("C", PRIMARY_SET, "M")]
    m_a, m_b = adj[("A", PRIMARY_SET, "M")], adj[("B", PRIMARY_SET, "M")]
    if cd["e0_missing"]:
        m_zh = ("右上：M 子集（显式跨语红线，按判定口径必须拦）——C 在 E1 拦 %d/%d，"
                "而 A、B 为 %d/%d 与 %d/%d；"
                % (m_e1["intercepted"], m_e1["n"],
                   m_a["intercepted"], m_a["n"], m_b["intercepted"], m_b["n"]))
        m_en = ("Top-right: the M subset (explicit cross-lingual red lines that must be "
                "intercepted) -- C intercepts %d/%d on E1, while A and B intercept "
                "%d/%d and %d/%d; "
                % (m_e1["intercepted"], m_e1["n"],
                   m_a["intercepted"], m_a["n"], m_b["intercepted"], m_b["n"]))
    else:
        m_e0 = adj[("C", "E0", "M")]
        m_zh = ("右上：M 子集（显式跨语红线，按判定口径必须拦）——C 在 E1 拦 %d/%d、"
                "E0 拦 %d/%d，而 A、B 在 E1 为 %d/%d 与 %d/%d；"
                % (m_e1["intercepted"], m_e1["n"], m_e0["intercepted"], m_e0["n"],
                   m_a["intercepted"], m_a["n"], m_b["intercepted"], m_b["n"]))
        m_en = ("Top-right: the M subset (explicit cross-lingual red lines that must be "
                "intercepted) -- C intercepts %d/%d on E1 and %d/%d on E0, while A and B "
                "intercept %d/%d and %d/%d on E1; "
                % (m_e1["intercepted"], m_e1["n"], m_e0["intercepted"], m_e0["n"],
                   m_a["intercepted"], m_a["n"], m_b["intercepted"], m_b["n"]))
    elig = {a["system"]: a for a in cd["eligible"] if a["eval_set"] == PRIMARY_SET}
    elig_zh = elig_en = ""
    if len(elig) == len(cd["systems"]):
        first = elig[cd["systems"][0]]
        elig_zh = ("E1 剔除 %d 条良性创作（n=%d）后的应拦口径为 A %s、B %s、C %s；"
                   % (first["excluded_benign_creations"], first["n"],
                      _fmt_rate(elig["A"]["rate"]), _fmt_rate(elig["B"]["rate"]),
                      _fmt_rate(elig["C"]["rate"])))
        elig_en = ("excluding the %d benign-creation items (n=%d), the E1 rates are "
                   "A %s, B %s, C %s; "
                   % (first["excluded_benign_creations"], first["n"],
                      _fmt_rate(elig["A"]["rate"]), _fmt_rate(elig["B"]["rate"]),
                      _fmt_rate(elig["C"]["rate"])))
    caption_zh = (
        "Phase C 三系统对比（run_id %s）。左上：三系统（A 词法闸门 / B Phase B 自愈补丁 / "
        "C 语义升维）在 E1（H0 指定零日集）与 E0（新 seed 新鲜 held-out）上的拦截率，"
        "双色表示评估集，绿色虚线为 H0 的 >0.90（E1）目标；%s"
        "左下：完整判定（词法+语义）的 p95 延迟（ms），红色虚线为 100 ms 硬预算（契约 §1.2）；"
        "右下：良性语料（%s 条）上的误伤率 FPR。"
        "数据来源：experiments/phase-c/results/run-semantic.json 的 "
        "systems.{A,B,C}.{E1,E0}.rate、.latency.p95_ms、systems.{A,B,C}.benign.fpr、"
        "systems.{A,B,C}.E1.eligible_excluding_benign 与 systems.{A,B,C}.adjudicated；"
        "未做平滑、插值与手填。雷达图轴 = 拦截率（E1）/ 1-FPR / 速度（1/p95）/ 覆盖意图数 / "
        "可解释性；后三轴按跨系统最大值归一化，原始值见图下方表。"
        "编码器为%s，阈值 %s 只在 %s 上校准；E1/E0 均为 held-out 变体而非 OOD%s。"
        "延迟为单机 numpy 实测，不代表生产端到端延迟。"
        % (cd["run"]["run_label"], m_zh + elig_zh,
           cd["eval_meta"].get("benign", {}).get("count") or "n/a", neural_text_zh,
           _fmt_num(enc["threshold"]), enc["threshold_calibrated_on"] or "n/a", e0_zh))
    caption_en = (
        "Phase C three-system comparison (run_id %s). Top-left: interception rates of the "
        "three systems (A lexical gate; B Phase B evolved patch; C semantic guard) on two "
        "held-out variant sets, E1 (H0 zero-day set) and E0 (fresh held-out, new seed), as "
        "grouped bars; the two shades encode the evaluation set and the green dashed line "
        "is the H0 target of >0.90 on E1. %s"
        "Bottom-left: p95 latency (ms) of the complete judgment (lexical + semantic) with "
        "the 100 ms hard budget as a red dashed line (contract 1.2); bottom-right: benign "
        "false-positive rate (FPR) on the benign corpus. Sources: run-semantic.json fields "
        "systems.{A,B,C}.{E1,E0}.rate, .latency.p95_ms, systems.{A,B,C}.benign.fpr, "
        "systems.{A,B,C}.E1.eligible_excluding_benign and systems.{A,B,C}.adjudicated; no "
        "smoothing, interpolation or hand-filled values. Radar axes: interception rate "
        "(E1) / 1-FPR / speed (1/p95) / intent coverage / explainability; the last three "
        "are max-normalised across A/B/C and raw values appear in the table below the "
        "radar. The encoder is an %s with threshold %s calibrated on %s only; E1/E0 are "
        "held-out variants, not OOD data%s. Latency is a single-machine numpy measurement, "
        "not production end-to-end latency."
        % (cd["run"]["run_label"], m_en + elig_en, neural_text_en,
           _fmt_num(enc["threshold"]), enc["threshold_calibrated_on"] or "n/a", e0_en))
    return caption_zh, caption_en


def write_figure_data(cd: dict, rd: dict, out_path: str, *,
                      caption_zh: str, caption_en: str,
                      figure_paths: dict) -> None:
    """写 `figure_data.json`：两条图每条数据 + caption + 来源字段名 + 真实边界。"""
    limitations = list(LIMITATIONS)
    if cd["e0_missing"]:
        limitations.insert(0, "E0 is absent from run-semantic.json for this run: the figure "
                              "shows E1 only. E0 is NOT imputed; regenerate after A4/E0 "
                              "lands.")
    data = {
        "schema": SCHEMA,
        "source_file": "experiments/phase-c/results/" + RUN_NAME,
        "source_schema": RUN_SCHEMA,
        "generated_at": _now_iso(),
        "run": {
            "run_id": cd["run"]["run_id"],
            "run_label": cd["run"]["run_label"],
            "run_id_source": cd["run"]["run_id_source"],
            "source_generated_at": cd["run"]["generated_at"],
            "branch": cd["run"]["branch"],
            "generated_by": cd["run"]["generated_by"],
        },
        "encoder": {
            "backend": cd["encoder"]["backend"],
            "is_neural": cd["encoder"]["is_neural"],
            "intent_count": cd["encoder"]["intent_count"],
            "threshold": cd["encoder"]["threshold"],
            "threshold_calibrated_on": cd["encoder"]["threshold_calibrated_on"],
            "intent_library_sha256": cd["encoder"]["intent_library_sha256"],
            "naming_note": (
                "the report/paper must call this an 'intent-concept encoder (offline, "
                "non-neural)' when is_neural is false; never 'all-MiniLM-L6-v2' or "
                "'neural sentence embedding' (contract 0.4)"),
        },
        "eval_sets": cd["eval_meta"],
        "figures": {
            "comparison": {
                "pdf": figure_paths["comparison_pdf"],
                "png": figure_paths["comparison_png"],
                "page_pt": [CMP_W, CMP_H],
                "description_en": (
                    "2x2 panels: grouped bars of interception rate for A/B/C x E1/E0; the "
                    "M subset (explicit cross-lingual red lines that must be intercepted); "
                    "p95 latency (ms) with the 100 ms budget line; benign FPR. A one-line "
                    "note gives the E1 rates excluding the two benign-creation items "
                    "(n=38)."),
            },
            "radar": {
                "pdf": figure_paths["radar_pdf"],
                "png": figure_paths["radar_png"],
                "page_pt": [RADAR_W, RADAR_H],
                "description_en": (
                    "radar over interception rate / 1-FPR / speed / intent coverage / "
                    "explainability; axes 3-5 max-normalised"),
            },
        },
        "comparison": {
            "caption_zh": caption_zh,
            "caption_en": caption_en,
            "bars": cd["bars"],
            "latency": cd["latency"],
            "fpr": cd["fpr"],
            "adjudicated": cd["adjudicated"],
            "eligible_excluding_benign": cd["eligible"],
            "adjudication_note": (
                "M = must-intercept explicit harmful intent; R = mild role-play that the "
                "project policy says should be intercepted; B = benign creative framing "
                "that must NOT be intercepted (intercepting it is a false positive). "
                "Adjudication was frozen before the semantic guard existed (Lead, "
                "experiments/phase-c/analysis/eval_adjudication*.json), so it cannot be "
                "fitted to C's output. E0 contains no B items, which is why "
                "eligible_excluding_benign is null there: nothing is excluded, it is not a "
                "missing field."),
            "budget_line": dict(cd["budget"]),
            "delta": cd["delta"],
        },
        "radar": {
            "caption_zh": caption_zh,
            "caption_en": caption_en,
            "primary_eval_set": rd["eval_set"],
            "axes": rd["axes"],
            "derived": rd["derived"],
        },
        "verification": {
            "rate_equals_intercepted_over_n": True,
            "fpr_equals_fp_count_over_n": True,
            "p95_ge_p50": True,
            "budget_pass_matches_max_p95": True,
            "delta_matches_C_minus_B": True,
            "note": ("all of the above were asserted while building the figure; any "
                     "inconsistency aborts with exit code 2 instead of drawing"),
        },
        "limitations": limitations,
        "api_calls": 0,
        "api_tokens_used": 0,
    }
    folder = os.path.dirname(os.path.abspath(out_path))
    if folder:
        os.makedirs(folder, exist_ok=True)
    with io.open(out_path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(data, ensure_ascii=False, indent=2))
        fh.write("\n")


# ── 对外入口（测试直接调用 build_* / render_* / main）───────────────────────
def generate(run_path: str, out_dir: str, *, dpi: int = DEFAULT_DPI,
             allow_missing_e0: bool = False,
             caption_zh: str | None = None, caption_en: str | None = None) -> dict:
    """读 run-semantic.json → 校验 → 画两张图 → 写 figure_data.json。"""
    if not isinstance(dpi, int) or dpi <= 0:
        raise PlotDataError("dpi must be a positive integer, got %r" % (dpi,))
    run = load_run_semantic(run_path, allow_missing_e0=allow_missing_e0)
    cd = build_comparison(run, allow_missing_e0=allow_missing_e0)
    rd = build_radar(run, cd)

    folder = out_dir if os.path.isabs(out_dir) else os.path.join(ROOT, out_dir)
    comparison_pdf = os.path.join(folder, CMP_PDF)
    comparison_png = os.path.join(folder, CMP_PNG)
    radar_pdf = os.path.join(folder, RADAR_PDF)
    radar_png = os.path.join(folder, RADAR_PNG)
    data_path = os.path.join(folder, DATA_JSON)

    render_comparison(cd, comparison_pdf, comparison_png, dpi=dpi)
    render_radar(rd, radar_pdf, radar_png, dpi=dpi)

    auto_zh, auto_en = default_captions(cd, rd)
    write_figure_data(
        cd, rd, data_path,
        caption_zh=caption_zh if caption_zh is not None else auto_zh,
        caption_en=caption_en if caption_en is not None else auto_en,
        figure_paths={
            "comparison_pdf": _rel(comparison_pdf),
            "comparison_png": _rel(comparison_png),
            "radar_pdf": _rel(radar_pdf),
            "radar_png": _rel(radar_png),
        })
    return {
        "run": run,
        "comparison": cd,
        "radar": rd,
        "paths": {
            "comparison_pdf": comparison_pdf,
            "comparison_png": comparison_png,
            "radar_pdf": radar_pdf,
            "radar_png": radar_png,
            "figure_data": data_path,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="plot_phase_c.py",
        description="Phase C A2: three-system comparison bar chart + capability radar "
                    "(hand-written vector PDF + Pillow PNG, no matplotlib).")
    parser.add_argument("--run", default=DEFAULT_RUN, metavar="PATH",
                        help="run-semantic.json produced by tools/phase_c_eval.py "
                             "(default: %(default)s)")
    parser.add_argument("--out", default=DEFAULT_OUT, metavar="DIR",
                        help="output directory (default: %(default)s)")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI,
                        help="PNG resolution (default: %(default)s)")
    parser.add_argument("--allow-missing-e0", action="store_true",
                        help="accept a run-semantic.json without E0 (figure shows E1 only; "
                             "never imputed)")
    parser.add_argument("--caption-zh", default=None, help="override caption_zh")
    parser.add_argument("--caption-en", default=None, help="override caption_en")
    args = parser.parse_args(argv)

    try:
        result = generate(args.run, args.out, dpi=args.dpi,
                          allow_missing_e0=args.allow_missing_e0,
                          caption_zh=args.caption_zh, caption_en=args.caption_en)
    except PlotDataError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - I/O / Pillow failures
        print("ERROR: rendering failed: %s: %s" % (type(exc).__name__, exc),
              file=sys.stderr)
        return 1

    cd = result["comparison"]
    rd = result["radar"]
    print("run: %s (%s)" % (cd["run"]["run_label"], cd["run"]["run_id_source"]))
    print("eval sets: %s  (E1 n=%s sha=%s)"
          % (", ".join(cd["eval_sets"]),
             (cd["eval_meta"].get("E1") or {}).get("count"),
             str((cd["eval_meta"].get("E1") or {}).get("sha256"))[:12]))
    if cd["e0_missing"]:
        print("!! E0 missing from the run: E1-only figure (E0 not imputed)")
    for eval_set in cd["eval_sets"]:
        cells = []
        for system in cd["systems"]:
            bar = next(b for b in cd["bars"]
                       if b["system"] == system and b["eval_set"] == eval_set)
            cells.append("%s=%.4f (%d/%d)"
                         % (system, bar["rate"], bar["intercepted"], bar["n"]))
        print("intercept %s: %s" % (eval_set, "  ".join(cells)))
    for system in cd["systems"]:
        lat = next(x for x in cd["latency"]
                   if x["system"] == system and x["eval_set"] == PRIMARY_SET)
        benign = next(x for x in cd["fpr"] if x["system"] == system)
        print("S_%s: p95(E1)=%.4f ms  FPR=%.4f (%d/%d)"
              % (system, lat["p95_ms"], benign["fpr"],
                 benign["fp_count"], benign["n"]))
    print("latency budget: limit=%.1f ms, worst p95=%.4f ms, pass=%s"
          % (cd["budget"]["limit_ms"], cd["budget"]["worst_p95_ms"],
             cd["budget"]["pass"]))
    for eval_set in cd["eval_sets"]:
        cells = []
        for system in cd["systems"]:
            for subset in ("M", "R", "B"):
                entry = next((a for a in cd["adjudicated"]
                              if a["system"] == system and a["eval_set"] == eval_set
                              and a["subset"] == subset), None)
                if entry is not None:
                    cells.append("%s %s=%d/%d"
                                 % (system, subset, entry["intercepted"], entry["n"]))
        if cells:
            print("adjudicated %s: %s" % (eval_set, "  ".join(cells)))
    for entry in cd["eligible"]:
        if entry["eval_set"] == PRIMARY_SET:
            print("eligible excluding benign (E1): %s n=%s rate=%s"
                  % (entry["system"], entry["n"], entry["rate"]))
    for axis in rd["axes"]:
        raw = "  ".join("%s=%.4g" % (s, axis["raw"][s]) for s in rd["systems"])
        print("radar %-18s [%s] %s" % (axis["id"], axis["scale"], raw))
    for key, path in result["paths"].items():
        print("wrote %s (%d bytes)" % (path, os.path.getsize(path)))
    return 0


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # pragma: no cover
            pass
    raise SystemExit(main())
