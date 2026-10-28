# -*- coding: utf-8 -*-
"""Phase C · A2 论文图：三系统对比 + 编码器消融 + 能力雷达（手写矢量 PDF / Pillow PNG）

契约：`experiments/phase-c/CONTRACT.md` §8.1。数据来源**唯一**，全部是 Lead 的
`tools/phase_c_eval.py` 产出（三份官方 run）+ 它派生的一份消融分析：

    experiments/phase-c/results/run-semantic.json         官方主口径（auto → offline_concept）
    experiments/phase-c/results/run-semantic-neural.json  消融：真神经 MiniLM（ONNX int8）
    experiments/phase-c/results/run-semantic-hybrid.json  消融：offline OR neural
    experiments/phase-c/analysis/neural_vs_concept_ablation.json  概念层 vs 神经层的 2x2 列联表

每份 run JSON 提供：
      - run_id / generated_at / branch           运行标识（run_id 是必备字段）
      - encoder.*                                backend_requested / backend_effective /
                                                 is_neural / model_id / model_sha256 /
                                                 embedding_dim / formula_version / intent_count /
                                                 threshold / threshold_calibrated_on /
                                                 thresholds_by_backend
      - systems.{A,B,C}.E1.rate                E1 零日集拦截率（H0 指定，40 条）
      - systems.{A,B,C}.E0.rate                E0 新鲜 held-out 拦截率（新 seed，40 条）
      - systems.{A,B,C}.{E1,E0}.latency.p95_ms 完整判定（词法+语义）的 p95 延迟
      - systems.{A,B,C}.benign.fpr             良性语料（74 条）误伤率
      - systems.{A,B,C}.{E1,E0}.adjudicated.*  M/R/B 判定口径分组
      - systems.{A,B,C}.E1.eligible_excluding_benign  应拦口径（E1 剔除 B 类，n=38）
      - latency_budget.limit_ms / .pass        100 ms 红线及其判定
      - delta.C_minus_B_E1 / .C_minus_B_E0
      - eval_sets.E1/E0.{count,sha256}          集合口径与哈希
消融 JSON 提供：E1/E0 的 2x2 列联表（both / offline_only / neural_only / neither）与
三变体的 rates / p95 / 阈值；本文件用它与三份 run JSON 交叉校验"神经层对系统 C 的增量 = 0"。

**缺字段 → 退出码 2，并在 stderr 点名缺的是哪个 JSON 路径**（不画空图、不手填、
不插值、不平滑）。数值一律按 JSON 原样画。

三张图：
1. `phase_c_comparison.pdf/.png`：五系统（A / B / C-offline / C-neural / C-hybrid）×
   E1/E0 拦截率 + M 子集 + p95 延迟（100 ms 红线）+ 良性 FPR；
2. `phase_c_ablation.pdf/.png`：概念层 vs 神经层 2x2 列联表热力图（E1 与 E0 各一组），
   直接可视化"神经增量 0"；
3. `phase_c_radar.pdf/.png`：雷达图（拦截率 / 1-FPR / 速度 1/p95 / 覆盖意图数 / 可解释性），
   C-offline 实线 + C-neural 虚线对照，图下方给出各轴原始数值表。
4. `figure_data.json`：三张图每条数据 + encoder_variants + ablation + caption + 来源字段名。

环境事实（契约 §0）：本机**没有 matplotlib / reportlab**，所以 PDF 走本文件内的最小矢量
写入器（`%PDF-1.4` + 对象表 + xref + content stream：`BT /F1 8 Tf 1 0 0 1 x y Tm (...) Tj ET`
文本、`m/l/c/S` 折线、`re` 矩形、坐标轴、刻度、图例、标题、脚注），只用 base-14 Helvetica，
**图内文字一律可打印 ASCII/英文**（PDF 里中文字体不可靠），中文只进 `figure_data.json`
的 `caption_zh`。PNG 走 Pillow（默认 300 dpi）。两个后端消费**同一份绘制指令表**
（`comparison_ops` / `ablation_ops` / `radar_ops`），所以 PDF 与 PNG 逐条同源。

PDF 原语（`_line` / `_rect` / `_polyline` / `_text` / `_pdf_content` / `_write_pdf` /
`_load_font` / `_write_png` 及 Helvetica 字宽表）复制自 Phase B 的
`tools/plot_coevolution_curve.py`（A2 前序交付，**本文件不改它**），本文件另加
`polygon` 绘制指令（雷达图填充需要）与 `circle` 填充支持。

诚实边界（写进 figure_data.json 的 limitations）：
- 默认/主口径 encoder 是**离线意图概念向量（offline, non-neural）**；另有**真神经**消融后端
  paraphrase-multilingual-MiniLM-L12-v2（ONNX int8，本地 CPU，零 API），二者都按同一
  预声明规则在 D_dev 上校准；`auto` 由该规则选后端，不因"装了神经模型"就默认切神经。
- hybrid 变体含神经推理（尽管该 JSON 里 `encoder.is_neural=false` 标的是主判定路径），
  图里一律标成 "offline OR neural"。
- E1/E0 都是 **held-out 变体，不是 OOD**；E0 只是新随机实现（新 seed），不是新攻击类型；
- 延迟是单机 CPU 实测（含/不含 ONNX 推理，无并发、无网络），不代表生产端到端延迟；
  神经/混合变体各有一条冷启动离群点（hybrid E1 单条 1003.8 ms），p95 不受影响。
- 雷达图后三个轴（速度 / 覆盖意图数 / 可解释性）单位不可通约，按"各轴跨变体最大值"
  归一化后画形状；原始数值在图下方表与 figure_data.json 里逐条给出；
- "可解释性"轴是**由判定输出 schema 派生的序数**（归因通道数），不是测量分数。

CLI：
    python tools/plot_phase_c.py \
        --run experiments/phase-c/results/run-semantic.json \
        --run-neural experiments/phase-c/results/run-semantic-neural.json \
        --run-hybrid experiments/phase-c/results/run-semantic-hybrid.json \
        --ablation experiments/phase-c/analysis/neural_vs_concept_ablation.json \
        --out experiments/phase-c/figures [--dpi 300]
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
SCHEMA = "phase-c-figure-data-v2"
RUN_SCHEMA = "phase-c-run-semantic-v1"
ABLATION_SCHEMA = "phase-c-neural-vs-concept-v1"
RUN_NAME = "run-semantic.json"
RUN_NEURAL_NAME = "run-semantic-neural.json"
RUN_HYBRID_NAME = "run-semantic-hybrid.json"
ABLATION_NAME = "neural_vs_concept_ablation.json"
DEFAULT_RUN = os.path.join("experiments", "phase-c", "results", RUN_NAME)
DEFAULT_RUN_NEURAL = os.path.join("experiments", "phase-c", "results", RUN_NEURAL_NAME)
DEFAULT_RUN_HYBRID = os.path.join("experiments", "phase-c", "results", RUN_HYBRID_NAME)
DEFAULT_ABLATION = os.path.join("experiments", "phase-c", "analysis", ABLATION_NAME)
DEFAULT_OUT = os.path.join("experiments", "phase-c", "figures")
DEFAULT_DPI = 300

#: 图的五条“系统”序列：A/B 来自词法层（三份 run 里必须完全一致），C-* 是三个语义变体
SYSTEMS = ("A", "B", "C-offline", "C-neural", "C-hybrid")
#: C 变体 -> 该变体所在的 run 文件（A/B 取官方 auto run）
VARIANT_RUN = {
    "C-offline": RUN_NAME,
    "C-neural": RUN_NEURAL_NAME,
    "C-hybrid": RUN_HYBRID_NAME,
}
C_VARIANTS = ("C-offline", "C-neural", "C-hybrid")
PRIMARY_SET = "E1"          # H0 指定的零日集；雷达图拦截率轴用它
SECONDARY_SET = "E0"        # 新鲜 held-out

CMP_PDF = "phase_c_comparison.pdf"
CMP_PNG = "phase_c_comparison.png"
ABL_PDF = "phase_c_ablation.pdf"
ABL_PNG = "phase_c_ablation.png"
RADAR_PDF = "phase_c_radar.pdf"
RADAR_PNG = "phase_c_radar.png"
DATA_JSON = "figure_data.json"

# ── 版面（单位：pt，1/72 inch；y 轴向下，与绘图习惯一致）────────────────────
# 行1：五系统总拦截率（E1/E0）；行2：M 子集（左）+ 良性 FPR（右）；
# 行3：p95 延迟（整行，100 ms 红线）。
CMP_W, CMP_H = 504.0, 484.0
CMP_RATE = {"left": 64.0, "right": 486.0, "top": 74.0, "bottom": 190.0}
CMP_M = {"left": 64.0, "right": 296.0, "top": 250.0, "bottom": 318.0}
CMP_FPR = {"left": 330.0, "right": 486.0, "top": 250.0, "bottom": 318.0}
CMP_LAT = {"left": 64.0, "right": 486.0, "top": 366.0, "bottom": 436.0}
CMP_RATE_YMAX = 1.12
CMP_M_YMAX = 1.12
CMP_FOOTNOTE_Y = 470.0
#: 兼容旧测试/外部引用：总拦截率面板即主面板
CMP_MAIN = CMP_RATE

#: 消融图：两个 2x2 列联表（E1 左 / E0 右）
ABL_W, ABL_H = 504.0, 340.0
ABL_CELL = 50.0
ABL_GRID_TOP = 84.0
ABL_PANELS = (
    {"set": "E1", "grid_left": 150.0, "center": 200.0},
    {"set": "E0", "grid_left": 360.0, "center": 410.0},
)
ABL_SUMMARY_Y = (268.0, 280.0, 292.0)
ABL_FOOTNOTE_Y = 326.0

RADAR_W, RADAR_H = 504.0, 470.0
RADAR_CX, RADAR_CY, RADAR_R = 252.0, 216.0, 116.0
RADAR_TABLE_HEADER_Y = 388.0
RADAR_TABLE_ROWS_Y = (402.0, 414.0, 426.0, 438.0)
RADAR_FOOTNOTE_Y = 456.0

# ── 颜色（PDF 里是 rg/RG 三元组，PNG 里换算成 0-255）────────────────────────
BLACK = (0.10, 0.10, 0.10)
AXIS = (0.20, 0.20, 0.20)
GRID = (0.86, 0.86, 0.86)
GRID_STRONG = (0.72, 0.72, 0.72)
RING = (0.60, 0.60, 0.60)
RED = (0.85, 0.10, 0.10)
BLUE = (0.05, 0.28, 0.75)
ORANGE = (0.93, 0.52, 0.05)
PURPLE = (0.46, 0.20, 0.62)
TEAL = (0.08, 0.50, 0.45)
GRAY = (0.45, 0.45, 0.45)
TARGET = (0.15, 0.50, 0.25)
WHITE = (1.0, 1.0, 1.0)

#: 五个系统序列的样式：id -> (图例英文, 主色, 浅色填充(E0)/雷达填充)
SYS_STYLE = {
    "A": ("A: lexical gate", RED, (1.00, 0.87, 0.87)),
    "B": ("B: + evolved patch", ORANGE, (1.00, 0.92, 0.80)),
    "C-offline": ("C-off: intent-concept", BLUE, (0.86, 0.91, 1.00)),
    "C-neural": ("C-neu: MiniLM ONNX int8", PURPLE, (0.93, 0.87, 0.97)),
    "C-hybrid": ("C-hyb: offline OR neural", TEAL, (0.86, 0.95, 0.93)),
}
SYS_SHORT = {"A": "lexical", "B": "patch", "C-offline": "concept",
             "C-neural": "neural", "C-hybrid": "hybrid"}
#: 轴标签用的短名（8pt 下不超过 group 宽度）
SYS_TICK = {"A": "A", "B": "B", "C-offline": "C-off",
            "C-neural": "C-neu", "C-hybrid": "C-hyb"}
SYS_DESC = {
    "A": "S_A: Phase A lexical gate (phase_b_defense.scan, no patch)",
    "B": "S_B: S_A plus the Phase B official run's evolved patch snapshot",
    "C-offline": "S_C with the intent-concept encoder (offline, non-neural; primary)",
    "C-neural": "S_C with paraphrase-multilingual-MiniLM-L12-v2 (ONNX int8, local CPU)",
    "C-hybrid": "S_C with offline_concept OR neural (hybrid; contains neural inference)",
}
#: 雷达图只画四条（C-hybrid 与 C-offline 的拦截率完全相同、只是更慢，画进来只会遮挡）
RADAR_SERIES = ("A", "B", "C-offline", "C-neural")
RADAR_DASHED = {"C-neural"}

#: 雷达图五个轴（契约 §8.1 指定的顺序）。`scale` 决定归一化方式：
#:   native          —— 该轴本身就是 [0,1] 比率，按原值画
#:   max_normalized  —— 单位不可通约（1/ms、个数），按"各轴跨变体最大值"归一化
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
     "note": "E1 上完整判定的 p95 延迟取倒数；按跨变体最大值归一化"},
    {"id": "intent_coverage", "label": "intent coverage", "label_zh": "覆盖意图数",
     "scale": "max_normalized",
     "source": "encoder.intent_count for C-*; 0 for A/B (no intent space exists)",
     "note": "C-* = 对应 run-semantic*.json 的 encoder.intent_count；A/B 的词法规则/补丁"
             "没有意图空间，结构上为 0（该 0 是结构事实，不是测量）"},
    {"id": "explainability", "label": "explainability", "label_zh": "可解释性",
     "scale": "max_normalized",
     "source": "schema-derived attribution channels (see figure_data.derived)",
     "note": "归因通道数序数（A=1, B=1, C-*=3），由 run 的判定输出 schema 派生；"
             "契约 §6.2 没有对应字段，属已知契约缺口"},
)

#: 可解释性轴的推导规则（写进 figure_data.derived，测试会断言这张表）
EXPLAINABILITY_RUBRIC = {
    "A": {"channels": 1,
          "rule": "1 attribution channel: rule-hit boolean (per-item rule id is not "
                  "recorded in run-semantic.json)"},
    "B": {"channels": 1,
          "rule": "1 attribution channel: rule/patch-hit boolean (which patch fired is not "
                  "recorded per item)"},
    "C-offline": {"channels": 3,
                  "rule": "3 attribution channels: hit boolean + similarity score (0-1) + "
                          "matched intent id (plus the STPA UCA reason 'semantic_risk')"},
    "C-neural": {"channels": 3,
                 "rule": "3 attribution channels: hit boolean + neural cosine score (0-1) + "
                         "matched intent prototype id"},
    "C-hybrid": {"channels": 3,
                 "rule": "3 attribution channels: hit boolean + score (0-1) + matched "
                         "intent id; per-item backend attribution is not recorded in "
                         "run-semantic-hybrid.json, so it is NOT counted as a channel"},
}
EXPLAINABILITY_NOTE = (
    "ordinal, NOT a measured psychometric score: counts the machine-readable attribution "
    "channels that the single evaluator (phase_c_eval.judge) exposes for each system. "
    "Contract 8.1 requires this axis but contract 6.2 defines no field for it; the ordinal "
    "is derived from the verdict schema and can be disputed by a reviewer."
)

#: run-semantic*.json 必备字段（缺一即 exit 2，并点名缺的路径）
REQUIRED_PATHS = [
    "run_id",
    "encoder.backend_requested",
    "encoder.backend_effective",
    "encoder.is_neural",
    "encoder.model_id",
    "encoder.model_sha256",
    "encoder.embedding_dim",
    "encoder.formula_version",
    "encoder.intent_count",
    "encoder.threshold",
    "encoder.threshold_calibrated_on",
    "encoder.thresholds_by_backend",
    "eval_sets.E1.count",
    "eval_sets.E1.sha256",
    "latency_budget.limit_ms",
    "latency_budget.pass",
]
#: 消融 JSON 必备字段
ABLATION_REQUIRED_PATHS = [
    "schema",
    "library_sha256",
    "thresholds.offline_concept",
    "thresholds.neural",
    "sets.E1.n",
    "sets.E1.both",
    "sets.E1.offline_only",
    "sets.E1.neural_only",
    "sets.E1.neither",
    "sets.E0.n",
    "sets.E0.both",
    "sets.E0.offline_only",
    "sets.E0.neural_only",
    "sets.E0.neither",
    "variants.auto.E1_rate",
    "variants.neural.E1_rate",
    "variants.hybrid.E1_rate",
]

LIMITATIONS = [
    "the primary encoder is an offline intent-concept encoder (backend_effective "
    "offline_concept, is_neural=false). A neural ablation backend is also present: "
    "paraphrase-multilingual-MiniLM-L12-v2 running as ONNX int8 on local CPU (model sha256 "
    "66fc00f5..., embedding dim 384, zero external API calls). The figures must not be cited "
    "as evidence that the primary detector is neural.",
    "the hybrid variant contains neural inference even though run-semantic-hybrid.json "
    "reports encoder.is_neural=false (that flag marks the primary decision path); the figures "
    "label it 'offline OR neural' and never 'non-neural'.",
    "both backends' thresholds are calibrated on D_dev only (0.45 for concept, 0.69 for "
    "neural) under the same pre-registered rule (max dev recall s.t. zero new benign false "
    "positives); E1/E0 are never used for tuning.",
    "E1 and E0 are held-out variants of the same seed pool, NOT out-of-distribution data; "
    "E0 is a fresh random realisation (new seed), not a new attack family.",
    "latency is a single-machine CPU measurement without concurrency or network; the neural "
    "and hybrid variants include ONNX inference and contain one cold-start outlier each "
    "(hybrid E1 single item 1003.8 ms, neural E1 max 20.6 ms) while their p95 stays below "
    "18 ms; this is not production end-to-end latency.",
    "the radar axes speed / intent coverage / explainability have incommensurable units and "
    "are max-normalised across the plotted systems to show shape; raw values are printed in "
    "the figure's table and in figure_data.json. The radar is not a composite score.",
    "the explainability axis is an ordinal derived from the verdict schema (attribution "
    "channel count), not a measured score; contract 8.1 requires the axis but contract 6.2 "
    "defines no field for it.",
    "M/R/B adjudication labels were frozen by the evaluation owner before the semantic guard "
    "existed (experiments/phase-c/analysis/eval_adjudication*.json), so they cannot be fitted "
    "to any backend's output; they remain author judgement.",
    "the benign corpus (74 items) contains no Russian or Arabic text, so cross-lingual false "
    "positives are untested for both encoders.",
    "the D_dev recall numbers quoted in the paper (concept 0.682 vs neural 0.289 at zero new "
    "benign FP) are read from experiments/phase-c/intent_library.json "
    "(thresholds.by_backend.*.calibration_curve); they are NOT plotted in these figures "
    "because the figure data scope is the three run JSONs plus the ablation JSON.",
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


RAW_SYSTEMS = ("A", "B", "C")   # run-semantic*.json 里的原始系统键（A/B 词法、C 语义）


def _load_json(path: str, label: str) -> dict:
    if not path:
        raise PlotDataError("empty path given for %s" % label)
    p = os.path.abspath(path)
    if not os.path.isfile(p):
        raise PlotDataError("%s not found: %s (wait for the Lead's phase_c_eval.py)" % (label, path))
    with io.open(p, encoding="utf-8-sig") as fh:
        try:
            obj = json.load(fh)
        except ValueError as exc:
            raise PlotDataError("invalid JSON in %s: %s" % (path, exc))
    if not isinstance(obj, dict):
        raise PlotDataError("%s is not a JSON object: %s" % (label, path))
    return obj


def load_run_semantic(path: str, *, allow_missing_e0: bool = False) -> dict:
    """读一份 run-semantic*.json 并做必备字段 / 双后端字段 / 集合哈希口径校验。"""
    run = _load_json(path, "run JSON")
    name = os.path.basename(os.path.abspath(path))
    schema = run.get("schema")
    if schema is not None and schema != RUN_SCHEMA:
        raise PlotDataError(
            "%s has schema %r, expected %r (refusing to plot an unknown run schema)"
            % (name, schema, RUN_SCHEMA))
    for dotted in REQUIRED_PATHS:
        require(run, dotted)
    encoder = run["encoder"]
    for key in ("backend_requested", "backend_effective", "model_id", "model_sha256",
                "formula_version", "threshold_calibrated_on"):
        if not isinstance(encoder.get(key), str):
            raise PlotDataError("field 'encoder.%s' in %s is not a string: %r"
                                % (key, name, encoder.get(key)))
    if not isinstance(encoder["is_neural"], bool):
        raise PlotDataError("field 'encoder.is_neural' in %s is not a boolean: %r"
                            % (name, encoder["is_neural"]))
    dim = _int(run, "encoder.embedding_dim", lo=1)
    intent_count = _int(run, "encoder.intent_count", lo=1)
    threshold = num(run, "encoder.threshold", lo=0.0, hi=1.0)
    by_backend = encoder["thresholds_by_backend"]
    if not isinstance(by_backend, dict):
        raise PlotDataError("field 'encoder.thresholds_by_backend' in %s is not an object"
                            % name)
    effective = encoder["backend_effective"]
    if effective in ("offline_concept", "neural"):
        declared = by_backend.get(effective)
        if not _is_number(declared):
            raise PlotDataError(
                "encoder.thresholds_by_backend.%s missing in %s" % (effective, name))
        if abs(float(declared) - threshold) > 1e-9:
            raise PlotDataError(
                "inconsistent run %s: encoder.threshold=%r != thresholds_by_backend.%s=%r"
                % (name, threshold, effective, declared))
    if effective == "neural" and not encoder["is_neural"]:
        raise PlotDataError("inconsistent run %s: backend_effective=neural but "
                            "is_neural=false" % name)
    if effective == "offline_concept" and encoder["is_neural"]:
        raise PlotDataError("inconsistent run %s: backend_effective=offline_concept but "
                            "is_neural=true" % name)

    for system in RAW_SYSTEMS:
        require(run, "systems.%s.E1.rate" % system)
        require(run, "systems.%s.E1.latency.p95_ms" % system)
        require(run, "systems.%s.benign.fpr" % system)
    if allow_missing_e0:
        if _has_set(run, SECONDARY_SET):
            for system in RAW_SYSTEMS:
                require(run, "systems.%s.E0.rate" % system)
                require(run, "systems.%s.E0.latency.p95_ms" % system)
    else:
        for system in RAW_SYSTEMS:
            require(run, "systems.%s.E0.rate" % system)
            require(run, "systems.%s.E0.latency.p95_ms" % system)
        require(run, "eval_sets.%s.count" % SECONDARY_SET)
        require(run, "eval_sets.%s.sha256" % SECONDARY_SET)

    # 集合条数必须与 systems.*.n 一致（口径自洽，否则数字不可比）
    for set_name in (PRIMARY_SET, SECONDARY_SET):
        declared = _walk(run, "eval_sets.%s.count" % set_name)
        if not _is_number(declared):
            continue
        for system in RAW_SYSTEMS:
            n = _walk(run, "systems.%s.%s.n" % (system, set_name))
            if _is_number(n) and int(n) != int(declared):
                raise PlotDataError(
                    "inconsistent run %s: eval_sets.%s.count=%r but systems.%s.%s.n=%r"
                    % (name, set_name, declared, system, set_name, n))
    return run


def load_ablation(path: str) -> dict:
    """读概念层 vs 神经层的 2x2 消融 JSON 并校验必备字段。"""
    abl = _load_json(path, "ablation JSON")
    schema = abl.get("schema")
    if schema is not None and schema != ABLATION_SCHEMA:
        raise PlotDataError(
            "ablation JSON has schema %r, expected %r" % (schema, ABLATION_SCHEMA))
    for dotted in ABLATION_REQUIRED_PATHS:
        require(abl, dotted)
    for set_name in (PRIMARY_SET, SECONDARY_SET):
        block = abl["sets"][set_name]
        n = _int(abl, "sets.%s.n" % set_name, lo=1)
        parts = {}
        for key in ("both", "offline_only", "neural_only", "neither"):
            parts[key] = _int(abl, "sets.%s.%s" % (set_name, key), lo=0)
        total = sum(parts.values())
        if total != n:
            raise PlotDataError(
                "inconsistent ablation: sets.%s counts sum to %d but n=%d (%r)"
                % (set_name, total, n, parts))
        neural_hits = parts["both"] + parts["neural_only"]
        offline_hits = parts["both"] + parts["offline_only"]
        for key, expected in (("neural_intercepted", neural_hits),
                              ("offline_intercepted", offline_hits),
                              ("neural_increment_over_offline", parts["neural_only"]),
                              ("offline_increment_over_neural", parts["offline_only"])):
            declared = _walk(abl, "sets.%s.%s" % (set_name, key))
            if _is_number(declared) and int(declared) != expected:
                raise PlotDataError(
                    "inconsistent ablation: sets.%s.%s=%r but 2x2 counts give %d"
                    % (set_name, key, declared, expected))
    return abl


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
        for system in RAW_SYSTEMS:
            if _walk(run, "systems.%s.%s.rate" % (system, SECONDARY_SET)) is not _MISSING:
                raise PlotDataError(
                    "inconsistent run: systems.%s.E0 exists but systems.A.E0.rate is missing"
                    % system)

    bars: list[dict] = []
    latency: list[dict] = []
    for system in RAW_SYSTEMS:
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
    for system in RAW_SYSTEMS:
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
    for system in RAW_SYSTEMS:
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
    for system in RAW_SYSTEMS:
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
        "backend_requested": require(run, "encoder.backend_requested"),
        "backend_effective": require(run, "encoder.backend_effective"),
        "is_neural": require(run, "encoder.is_neural"),
        "uses_neural_backend": require(run, "encoder.backend_effective")
        in ("neural", "hybrid"),
        "model_id": require(run, "encoder.model_id"),
        "model_sha256": require(run, "encoder.model_sha256"),
        "embedding_dim": _int(run, "encoder.embedding_dim", lo=1),
        "formula_version": require(run, "encoder.formula_version"),
        "intent_count": _int(run, "encoder.intent_count", lo=1),
        "threshold": num(run, "encoder.threshold", lo=0.0, hi=1.0),
        "threshold_calibrated_on": require(run, "encoder.threshold_calibrated_on"),
        "thresholds_by_backend": require(run, "encoder.thresholds_by_backend"),
        "intent_library_sha256": _walk(run, "encoder.intent_library_sha256"),
        "is_neural_flag_note": (
            "is_neural marks the primary decision path; the hybrid backend still runs "
            "neural inference (see uses_neural_backend)"),
    }
    for key in ("backend_requested", "backend_effective", "model_id", "model_sha256",
                "formula_version", "threshold_calibrated_on"):
        if not isinstance(encoder[key], str):
            raise PlotDataError("field 'encoder.%s' is not a string: %r"
                                % (key, encoder[key]))
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
        "systems": list(RAW_SYSTEMS),
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


# ── 图 1 数据：三份 run → 五系统 ───────────────────────────────────────────
def _variant_meta(run: dict, label: str) -> dict:
    """单个 run 的 encoder / 运行元数据（figure_data.encoder_variants 用）。"""
    enc = run["encoder"]
    sets = [s for s in (PRIMARY_SET, SECONDARY_SET)
            if _is_number(_walk(run, "systems.C.%s.rate" % s))]
    return {
        "label": label,
        "run_id": run.get("run_id"),
        "generated_at": run.get("generated_at"),
        "source_file": "experiments/phase-c/results/" + VARIANT_RUN[label],
        "backend_requested": enc["backend_requested"],
        "backend_effective": enc["backend_effective"],
        "is_neural": bool(enc["is_neural"]),
        "uses_neural_backend": enc["backend_effective"] in ("neural", "hybrid"),
        "model_id": enc["model_id"],
        "model_sha256": enc["model_sha256"],
        "embedding_dim": int(enc["embedding_dim"]),
        "formula_version": enc["formula_version"],
        "intent_count": int(enc["intent_count"]),
        "threshold": float(enc["threshold"]),
        "threshold_calibrated_on": enc["threshold_calibrated_on"],
        "thresholds_by_backend": enc["thresholds_by_backend"],
        "intent_library_sha256": enc.get("intent_library_sha256"),
        "rates": {set_name: float(run["systems"]["C"][set_name]["rate"])
                  for set_name in sets},
        "p95_ms": {set_name: float(run["systems"]["C"][set_name]["latency"]["p95_ms"])
                   for set_name in sets},
        "benign_fpr": float(run["systems"]["C"]["benign"]["fpr"]),
        "latency_budget": dict(run["latency_budget"]),
        "delta_C_minus_B": {
            set_name: float(_walk(run, "delta.C_minus_B_%s" % set_name))
            for set_name in sets
            if _is_number(_walk(run, "delta.C_minus_B_%s" % set_name))},
    }


def _select(entries: list[dict], system: str, eval_set: str | None = None) -> dict:
    for entry in entries:
        if entry["system"] != system:
            continue
        if eval_set is None or entry.get("eval_set") == eval_set:
            return entry
    raise PlotDataError("internal: no entry for system %r eval_set %r" % (system, eval_set))


def build_comparison_multi(variant_runs: dict[str, dict]) -> dict:
    """三份 run → 五系统对比数据（A/B 取官方 run；C-* 各取自己 run）。

    交叉校验（失败即 exit 2，不画自相矛盾的图）：
      - 三份 run 的 E1/E0 必须是同一 count + 同一 sha256；
      - A、B 两个词法系统在三份 run 里必须逐条相同（rate / intercepted / n / FPR）；
      - C-hybrid 的拦截率与 FPR 必须等于 C-offline（hybrid = offline OR neural）。
    """
    if set(variant_runs) != set(C_VARIANTS):
        raise PlotDataError("needs runs for %r, got %r"
                            % (C_VARIANTS, sorted(variant_runs)))
    per = {label: build_comparison(run) for label, run in variant_runs.items()}
    base = per["C-offline"]
    base_sets = base["eval_sets"]
    for label, cd in per.items():
        if cd["eval_sets"] != base_sets:
            raise PlotDataError(
                "eval sets differ between C-offline %r and %s %r"
                % (base_sets, label, cd["eval_sets"]))
        if cd["e0_missing"]:
            raise PlotDataError("E0 missing from run %s; the five-system figure needs E1+E0"
                                % VARIANT_RUN[label])
        for set_name in base_sets:
            a, b = base["eval_meta"][set_name], cd["eval_meta"][set_name]
            if (a["count"], a["sha256"]) != (b["count"], b["sha256"]):
                raise PlotDataError(
                    "inconsistent eval set %s between C-offline and %s: count %r vs %r, "
                    "sha256 %r vs %r" % (set_name, label, a["count"], b["count"],
                                         a["sha256"], b["sha256"]))
    for label, cd in per.items():
        if label == "C-offline":
            continue
        for system in ("A", "B"):
            for set_name in base_sets:
                ei = _select(base["bars"], system, set_name)
                ej = _select(cd["bars"], system, set_name)
                if (ei["rate"], ei["intercepted"]) != (ej["rate"], ej["intercepted"]):
                    raise PlotDataError(
                        "inconsistent %s on %s between C-offline and %s: %r vs %r "
                        "(lexical layers must be identical)"
                        % (system, set_name, label, ei["rate"], ej["rate"]))
            if _select(base["fpr"], system)["fpr"] != _select(cd["fpr"], system)["fpr"]:
                raise PlotDataError(
                    "inconsistent benign FPR for %s between C-offline and %s"
                    % (system, label))

    bars: list[dict] = []
    latency: list[dict] = []
    fpr: list[dict] = []
    adjudicated: list[dict] = []
    eligible: list[dict] = []
    for system in ("A", "B"):
        file_label = "experiments/phase-c/results/" + RUN_NAME
        bars.extend(dict(item, source_file=file_label)
                    for item in base["bars"] if item["system"] == system)
        latency.extend(dict(item, source_file=file_label)
                       for item in base["latency"] if item["system"] == system)
        fpr.append(dict(_select(base["fpr"], system), source_file=file_label))
        adjudicated.extend(dict(item, source_file=file_label)
                           for item in base["adjudicated"] if item["system"] == system)
        eligible.extend(dict(item, source_file=file_label)
                        for item in base["eligible"] if item["system"] == system)
    for label in C_VARIANTS:
        cd = per[label]
        file_label = "experiments/phase-c/results/" + VARIANT_RUN[label]
        bars.extend(dict(item, system=label, source_file=file_label)
                    for item in cd["bars"] if item["system"] == "C")
        latency.extend(dict(item, system=label, source_file=file_label)
                       for item in cd["latency"] if item["system"] == "C")
        fpr.append(dict(_select(cd["fpr"], "C"), system=label, source_file=file_label))
        adjudicated.extend(dict(item, system=label, source_file=file_label)
                           for item in cd["adjudicated"] if item["system"] == "C")
        eligible.extend(dict(item, system=label, source_file=file_label)
                        for item in cd["eligible"] if item["system"] == "C")

    for set_name in base_sets:
        off = _select(bars, "C-offline", set_name)
        hyb = _select(bars, "C-hybrid", set_name)
        if (off["intercepted"], off["n"]) != (hyb["intercepted"], hyb["n"]):
            raise PlotDataError(
                "C-hybrid != C-offline on %s (%d/%d vs %d/%d); the claim "
                "'hybrid == offline' does not hold -- review before plotting"
                % (set_name, hyb["intercepted"], hyb["n"],
                   off["intercepted"], off["n"]))
    if _select(fpr, "C-offline")["fp_count"] != _select(fpr, "C-hybrid")["fp_count"]:
        raise PlotDataError("C-hybrid benign FPR != C-offline; review before plotting")

    budgets = {label: per[label]["budget"] for label in C_VARIANTS}
    return {
        "systems": list(SYSTEMS),
        "eval_sets": base_sets,
        "e0_missing": False,
        "bars": bars,
        "latency": latency,
        "fpr": fpr,
        "adjudicated": adjudicated,
        "eligible": eligible,
        "budget": {
            "limit_ms": base["budget"]["limit_ms"],
            "pass": all(b["pass"] for b in budgets.values()),
            "worst_p95_ms": max(b["worst_p95_ms"] for b in budgets.values()),
            "per_variant": budgets,
            "source_fields": ["latency_budget.limit_ms", "latency_budget.pass",
                              "latency_budget.worst_p95_ms (per run)"],
        },
        "delta": {label: {set_name: per[label]["delta"]["C_minus_B_%s" % set_name]
                          for set_name in base_sets
                          if "C_minus_B_%s" % set_name in per[label]["delta"]}
                  for label in C_VARIANTS},
        "encoder": base["encoder"],
        "encoder_variants": {label: _variant_meta(variant_runs[label], label)
                             for label in C_VARIANTS},
        "variant_runs": {label: per[label]["run"] for label in C_VARIANTS},
        "eval_meta": base["eval_meta"],
        "run": dict(base["run"], source_file="experiments/phase-c/results/" + RUN_NAME),
    }


# ── 图 2 数据：雷达图 ───────────────────────────────────────────────────────
def _radar_raw(cd: dict, system: str, label: str) -> dict:
    """一个 run 的某个系统的五轴原始值 + 来源字段 + 推导规则。"""
    e1 = _select(cd["bars"], system, PRIMARY_SET)
    lat = _select(cd["latency"], system, PRIMARY_SET)
    benign = _select(cd["fpr"], system)
    if system == "C":
        coverage = float(cd["encoder"]["intent_count"])
        coverage_source = "encoder.intent_count"
        coverage_rule = "encoder.intent_count"
    else:
        coverage = 0.0
        coverage_source = "structural zero: %s has no intent space" % system
        coverage_rule = "0 by construction (lexical systems have no intent space)"
    explain = float(EXPLAINABILITY_RUBRIC[label]["channels"])
    explain_source = "derived:EXPLAINABILITY_RUBRIC[%r]" % label
    explain_rule = EXPLAINABILITY_RUBRIC[label]["rule"]
    values = {
        "intercept_rate": e1["rate"],
        "one_minus_fpr": 1.0 - benign["fpr"],
        "speed": 1.0 / lat["p95_ms"],
        "intent_coverage": coverage,
        "explainability": explain,
    }
    sources = {
        "intercept_rate": e1["source_field"] + " (in " + e1.get("source_file", "?") + ")",
        "one_minus_fpr": "1 - %s" % benign["source_field"],
        "speed": "1 / %s" % lat["source_field"],
        "intent_coverage": coverage_source,
        "explainability": explain_source,
    }
    rules = {
        "intercept_rate": "native 0-1 field",
        "one_minus_fpr": "native 0-1 field (1 - FPR)",
        "speed": "1 / p95_ms on E1",
        "intent_coverage": coverage_rule,
        "explainability": explain_rule,
    }
    return {"values": values, "sources": sources, "rules": rules}


def build_radar_multi(comparisons: dict[str, dict]) -> dict:
    """雷达图：A / B / C-offline 实线 + C-neural 虚线对照；C-hybrid 只进 figure_data。"""
    base = comparisons["C-offline"]
    raw = {
        "A": _radar_raw(base, "A", "A"),
        "B": _radar_raw(base, "B", "B"),
        "C-offline": _radar_raw(base, "C", "C-offline"),
        "C-neural": _radar_raw(comparisons["C-neural"], "C", "C-neural"),
    }
    series = [s for s in RADAR_SERIES]
    axes = []
    for axis in RADAR_AXES:
        axis_id = axis["id"]
        values = {s: raw[s]["values"][axis_id] for s in series}
        raw_max = max(values.values())
        if axis["scale"] == "max_normalized":
            if raw_max <= 0:
                raise PlotDataError(
                    "cannot normalise radar axis %r: max raw value is %r across %r"
                    % (axis_id, raw_max, series))
            normalized = {s: values[s] / raw_max for s in series}
        else:
            for system in series:
                if not (-1e-9 <= values[system] <= 1.0 + 1e-9):
                    raise PlotDataError(
                        "radar axis %r is native-0-1 but raw value for %s is %r"
                        % (axis_id, system, values[system]))
            raw_max = 1.0
            normalized = {system: min(1.0, max(0.0, values[system]))
                          for system in series}
        axes.append({
            "id": axis_id,
            "label_en": axis["label"],
            "label_zh": axis["label_zh"],
            "scale": axis["scale"],
            "raw_max": raw_max,
            "raw": values,
            "normalized": normalized,
            "source_fields": {s: raw[s]["sources"][axis_id] for s in series},
            "rules": {s: raw[s]["rules"][axis_id] for s in series},
            "note": axis["note"],
        })
    hybrid = _radar_raw(comparisons["C-hybrid"], "C", "C-hybrid")
    return {
        "series": series,
        "dashed_series": sorted(RADAR_DASHED),
        "axes": axes,
        "encoder": base["encoder"],
        "eval_set": PRIMARY_SET,
        "not_plotted": {
            "C-hybrid": {
                "values": hybrid["values"],
                "sources": hybrid["sources"],
                "reason": "C-hybrid has the same interception rates as C-offline and is "
                          "only slower; plotting it would overplot C-offline without "
                          "adding information (raw values recorded here)"},
        },
        "derived": {
            "explainability_rubric": EXPLAINABILITY_RUBRIC,
            "explainability_note": EXPLAINABILITY_NOTE,
            "intent_coverage_rule": (
                "C-* = encoder.intent_count of the corresponding run; A and B = 0 because "
                "the lexical rule/patch systems have no intent space at all. The zero is a "
                "structural fact, not a measurement."),
        },
        "run": base["run"],
    }


# ── 图 3 数据：神经 vs 概念 2x2 消融 ──────────────────────────────────────
def build_ablation(ablation: dict, variant_runs: dict[str, dict]) -> dict:
    """把 2x2 列联表整理成图 + figure_data，并与三份 run JSON 交叉校验。

    关键口径：`neural_increment_over_concept_on_C` = 神经层独有命中（neural_only）里
    **没有被 S_B 词法层拦住**的条数 —— 只有这些才真正给系统 C 增加拦截。E1 的唯一
    neural_only 条目已被 S_B 的 zero_width 解码器拦住，因此增量为 0。
    """
    base = variant_runs["C-offline"]
    ablation_key = {"C-offline": "auto", "C-neural": "neural", "C-hybrid": "hybrid"}
    for label, key in ablation_key.items():
        run = variant_runs[label]
        expected_rate = {s: run["systems"]["C"][s]["rate"]
                         for s in (PRIMARY_SET, SECONDARY_SET)}
        expected_p95 = {s: run["systems"]["C"][s]["latency"]["p95_ms"]
                        for s in (PRIMARY_SET, SECONDARY_SET)}
        actual_rate = {s: _walk(ablation, "variants.%s.%s_rate" % (key, s))
                       for s in (PRIMARY_SET, SECONDARY_SET)}
        actual_p95 = {s: _walk(ablation, "variants.%s.p95_%s_ms" % (key, s))
                      for s in (PRIMARY_SET, SECONDARY_SET)}
        for set_name in (PRIMARY_SET, SECONDARY_SET):
            if not _is_number(actual_rate[set_name]) or \
                    abs(float(actual_rate[set_name]) - expected_rate[set_name]) > 1e-9:
                raise PlotDataError(
                    "inconsistent ablation: variants.%s.%s_rate=%r but run %s gives %r"
                    % (key, set_name, actual_rate[set_name], label,
                       expected_rate[set_name]))
            if not _is_number(actual_p95[set_name]) or \
                    abs(float(actual_p95[set_name]) - expected_p95[set_name]) > 1e-6:
                raise PlotDataError(
                    "inconsistent ablation: variants.%s.p95_%s_ms=%r but run %s gives %r"
                    % (key, set_name, actual_p95[set_name], label,
                       expected_p95[set_name]))
        for dotted, expected in (
                ("variants.%s.benign_fpr" % key,
                 run["systems"]["C"]["benign"]["fpr"]),
                ("variants.%s.threshold" % key, run["encoder"]["threshold"])):
            actual = _walk(ablation, dotted)
            if not _is_number(actual) or abs(float(actual) - float(expected)) > 1e-9:
                raise PlotDataError(
                    "inconsistent ablation: %s=%r but run %s gives %r"
                    % (dotted, actual, label, expected))
    lib = ablation["library_sha256"]
    for label, run in variant_runs.items():
        declared = run["encoder"].get("intent_library_sha256")
        if declared and declared != lib:
            raise PlotDataError(
                "intent library sha256 mismatch: ablation %r vs %s run %r"
                % (lib, label, declared))
    thresholds = ablation["thresholds"]
    for backend, label in (("offline_concept", "C-offline"), ("neural", "C-neural")):
        if abs(float(thresholds[backend])
               - float(variant_runs[label]["encoder"]["threshold"])) > 1e-9:
            raise PlotDataError(
                "ablation thresholds.%s=%r != %s encoder.threshold %r"
                % (backend, thresholds[backend], label,
                   variant_runs[label]["encoder"]["threshold"]))

    sets = {}
    for set_name in (PRIMARY_SET, SECONDARY_SET):
        block = ablation["sets"][set_name]
        b_misses = {m["id"] for m in
                    base["systems"]["B"][set_name].get("misses", [])}
        neural_items = list(block.get("neural_only_items") or [])
        neural_ids = [str(item.get("id")) for item in neural_items]
        already = [i for i in neural_ids if i not in b_misses]
        added = [i for i in neural_ids if i in b_misses]
        offline_ids = list(block.get("offline_only_ids") or [])
        offline_added = [i for i in offline_ids if i in b_misses]
        sets[set_name] = {
            "n": int(block["n"]),
            "both": int(block["both"]),
            "offline_only": int(block["offline_only"]),
            "neural_only": int(block["neural_only"]),
            "neither": int(block["neither"]),
            "offline_only_ids": offline_ids,
            "neural_only_ids": neural_ids,
            "neural_only_items": neural_items,
            "neural_only_already_intercepted_by_S_B": already,
            "neural_only_added_to_C": added,
            "neural_increment_over_concept_on_C": len(added),
            "offline_only_added_to_C": offline_added,
            "offline_increment_over_concept_on_C": len(offline_added),
            "b_misses_n": len(b_misses),
            "source_fields": {
                "counts": "sets.%s.{both,offline_only,neural_only,neither}" % set_name,
                "neural_only_ids": "sets.%s.neural_only_items[].id" % set_name,
                "b_misses": "run-semantic.json systems.B.%s.misses[].id" % set_name,
            },
        }
    variants = {}
    for label, key in ablation_key.items():
        meta = _variant_meta(variant_runs[label], label)
        variants[label] = {
            "ablation_key": key,
            "backend_effective": meta["backend_effective"],
            "is_neural": meta["is_neural"],
            "uses_neural_backend": meta["uses_neural_backend"],
            "threshold": meta["threshold"],
            "E1_rate": meta["rates"][PRIMARY_SET],
            "E0_rate": meta["rates"][SECONDARY_SET],
            "benign_fpr": meta["benign_fpr"],
            "p95_E1_ms": meta["p95_ms"][PRIMARY_SET],
            "p95_E0_ms": meta["p95_ms"][SECONDARY_SET],
            "source_file": meta["source_file"],
        }
    return {
        "source_file": "experiments/phase-c/analysis/" + ABLATION_NAME,
        "schema": ablation.get("schema"),
        "library_sha256": lib,
        "thresholds": thresholds,
        "sets": sets,
        "variants": variants,
        "claim": {
            "neural_increment_over_concept_on_C": {
                PRIMARY_SET: sets[PRIMARY_SET]["neural_increment_over_concept_on_C"],
                SECONDARY_SET: sets[SECONDARY_SET]["neural_increment_over_concept_on_C"],
            },
            "hybrid_equals_offline": True,
            "note": "hybrid == offline is asserted in build_comparison_multi (raises if "
                    "the run JSONs disagree); the neural increment over concept is derived "
                    "from the 2x2 ablation plus the S_B miss lists, because a neural-only "
                    "hit that S_B already intercepts adds nothing to system C.",
        },
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


# ── 图 1：五系统分组柱状图 ─────────────────────────────────────────────────
def comparison_ops(cd: dict) -> list[dict]:
    """五系统 × (E1,E0) 拦截率 / M 子集 / 良性 FPR / p95 延迟（100 ms 红线）。

    A/B 是词法层（三份 run 一致），C-offline / C-neural / C-hybrid 是语义层的三个
    后端变体。M 子集（必须拦的显式跨语红线）单独成面板：C-offline 与 C-hybrid 拦到
    3/3，C-neural 掉到 0/3 —— 这是"更强的模型 ≠ 更强的防御"最直接的证据。
    E1 应拦口径（剔除 2 条良性创作，n=38）以一行文字注记给出。
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
    title = "Phase C: Five Systems on Held-out Variants"
    ops.append(_text(CMP_W / 2.0, 22.0, title,
                     _fit_text(title, 12.0, CMP_W - 60.0, True), bold=True,
                     align="center", color=BLACK, role="title"))
    subtitle1 = ("A: lexical gate | B: + Phase B evolved patch | "
                 "C-*: B + semantic channel")
    ops.append(_text(CMP_W / 2.0, 35.0, subtitle1,
                     _fit_text(subtitle1, 8.0, CMP_W - 40.0), align="center",
                     color=AXIS, role="subtitle"))
    subtitle2 = ("C-offline: intent-concept (primary) | C-neural: MiniLM-L12 ONNX int8 | "
                 "C-hybrid: offline OR neural")
    ops.append(_text(CMP_W / 2.0, 46.0, subtitle2,
                     _fit_text(subtitle2, 8.0, CMP_W - 30.0), align="center",
                     color=AXIS, role="subtitle"))
    # E1/E0 图例（右上）
    legend_right = CMP_W - 14.0
    ops.append(_rect(legend_right - 126.0, 53.0, 10.0, 9.0, fill=BLACK,
                     role="legend-sample"))
    ops.append(_text(legend_right - 112.0, 59.0, "E1 zero-day", 8, color=BLACK,
                     role="legend-text"))
    ops.append(_rect(legend_right - 60.0, 53.0, 10.0, 9.0, fill=GRID_STRONG,
                     stroke=AXIS, width=0.8, role="legend-sample"))
    ops.append(_text(legend_right - 46.0, 59.0, "E0 fresh", 8, color=BLACK,
                     role="legend-text"))
    # C - B 净增量（三变体；来自各 run JSON 的 delta）
    delta_parts = []
    for label, short in (("C-offline", "off"), ("C-neural", "neu"),
                         ("C-hybrid", "hyb")):
        values = cd["delta"].get(label) or {}
        if values:
            delta_parts.append("%s %+.3f/%+.3f" % (
                short, values.get(PRIMARY_SET, 0.0), values.get(SECONDARY_SET, 0.0)))
    if delta_parts:
        ops.append(_text(12.0, 59.0, "C-B (E1/E0): " + " | ".join(delta_parts),
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

    def group_bars(geom: dict, y_of, pair, colour_of, label_of, role_prefix: str,
                   min_height: float = 0.0):
        left, right = geom["left"], geom["right"]
        bottom = geom["bottom"]
        group_w = (right - left) / float(len(systems))
        bar_w, bar_gap = pair
        pair_w = len(eval_sets) * bar_w + (len(eval_sets) - 1) * bar_gap
        for index, system in enumerate(systems):
            cx = left + (index + 0.5) * group_w
            ops.append(_text(cx, bottom + 12.0, SYS_TICK[system], 8, bold=True,
                             align="center", color=BLACK, role="panel-x-ticklabel"))
            for j, set_name in enumerate(eval_sets):
                x = cx - pair_w / 2.0 + j * (bar_w + bar_gap)
                value = label_of(system, set_name)
                y_top = y_of(value)
                fill, stroke = colour_of(system, set_name)
                ops.append(_rect(x, y_top, bar_w, max(min_height, bottom - y_top),
                                 fill=fill, stroke=stroke, width=0.8,
                                 role="%s:%s:%s" % (role_prefix, system, set_name)))

    # ── 面板 1：总拦截率，五系统 × E1/E0 ──
    geom = CMP_RATE
    y_rate = panel_frame(geom, CMP_RATE_YMAX, (0.0, 0.25, 0.5, 0.75, 1.0),
                         "interception rate (held-out)")
    y_target = y_rate(0.90)
    ops.append(_line(geom["left"], y_target, geom["right"], y_target, TARGET, 0.9,
                     dash=(2.0, 2.0), role="target-line"))
    ops.append(_text(geom["left"] + 3.0, y_target - 4.0, "H0 target: > 0.90 on E1",
                     8, color=TARGET, role="target-label"))

    def _rate_colour(system, set_name):
        if set_name == PRIMARY_SET:
            return SYS_STYLE[system][1], None
        return SYS_STYLE[system][2], SYS_STYLE[system][1]

    group_bars(geom, y_rate, (32.0, 6.0), _rate_colour,
               lambda s, e: by_key[(s, e)]["rate"], "bar")
    group_w = (geom["right"] - geom["left"]) / float(len(systems))
    pair_w = 2 * 32.0 + 6.0
    for index, system in enumerate(systems):
        cx = geom["left"] + (index + 0.5) * group_w
        ops.append(_text(cx, geom["bottom"] + 22.0, SYS_SHORT[system], 8,
                         align="center", color=AXIS, role="panel-x-ticklabel"))
        for j, set_name in enumerate(eval_sets):
            x = cx - pair_w / 2.0 + j * (32.0 + 6.0)
            item = by_key[(system, set_name)]
            ops.append(_text(x + 16.0, y_rate(item["rate"]) - 4.5,
                             _fmt_rate(item["rate"]), 8, align="center",
                             color=BLACK, role="bar-label:%s:%s" % (system, set_name)))

    # ── 面板 2：M 子集（显式跨语红线，必须拦）──
    geom_m = CMP_M
    y_m = panel_frame(geom_m, CMP_M_YMAX, (0.0, 0.5, 1.0),
                      "M subset: must-intercept red lines")
    group_bars(geom_m, y_m, (19.0, 4.0), _rate_colour,
               lambda s, e: adj_by_key[(s, e, "M")]["rate"], "m-bar", min_height=1.0)
    group_w = (geom_m["right"] - geom_m["left"]) / float(len(systems))
    pair_w = 2 * 19.0 + 4.0
    for index, system in enumerate(systems):
        cx = geom_m["left"] + (index + 0.5) * group_w
        for j, set_name in enumerate(eval_sets):
            x = cx - pair_w / 2.0 + j * (19.0 + 4.0)
            item = adj_by_key[(system, set_name, "M")]
            ops.append(_text(x + 9.5, y_m(item["rate"]) - 4.0,
                             "%d/%d" % (item["intercepted"], item["n"]), 8,
                             align="center", color=BLACK,
                             role="m-bar-label:%s:%s" % (system, set_name)))

    # ── 应拦口径注记（E1 剔除 2 条良性创作，n=38）──
    eligible_e1 = {a["system"]: a for a in cd["eligible"]
                   if a["eval_set"] == PRIMARY_SET}
    if len(eligible_e1) == len(systems):
        first = eligible_e1[systems[0]]
        pieces = " | ".join("%s %s" % (SYS_TICK[s], _fmt_rate(eligible_e1[s]["rate"]))
                            for s in systems)
        note = ("E1 excl. %d benign-creation items (n=%d): %s"
                % (first["excluded_benign_creations"], first["n"], pieces))
        ops.append(_text(30.0, 232.0, note, 8, color=AXIS, role="eligible-note"))

    # ── 面板 3：良性 FPR ──
    geom_f = CMP_FPR
    fprs = [x["fpr"] for x in cd["fpr"]]
    fpr_ymax = max(0.05, max(fprs) * 1.25)
    fpr_equal = all(abs(v - fprs[0]) < 1e-12 for v in fprs)
    fpr_title = "benign FPR (n=%s; all %s)" % (
        cd["eval_meta"].get("benign", {}).get("count") or "n/a",
        _fmt_rate(fprs[0])) if fpr_equal else "benign FPR (n=%s)" % (
        cd["eval_meta"].get("benign", {}).get("count") or "n/a")
    y_fpr = panel_frame(geom_f, fpr_ymax,
                        [fpr_ymax * step / 2.0 for step in range(3)], fpr_title)
    group_w = (geom_f["right"] - geom_f["left"]) / float(len(systems))
    bar_w = 22.0
    for index, system in enumerate(systems):
        cx = geom_f["left"] + (index + 0.5) * group_w
        item = fpr_by_sys[system]
        y_top = y_fpr(item["fpr"])
        ops.append(_rect(cx - bar_w / 2.0, y_top, bar_w,
                         max(1.0, geom_f["bottom"] - y_top),
                         fill=SYS_STYLE[system][1], role="fpr-bar:%s" % system))
        ops.append(_text(cx, y_top - 4.0, "%d/%d" % (item["fp_count"], item["n"]),
                         8, align="center", color=BLACK, role="fpr-label:%s" % system))
        ops.append(_text(cx, geom_f["bottom"] + 12.0, SYS_TICK[system], 8,
                         bold=True, align="center", color=BLACK,
                         role="fpr-x-ticklabel:%s" % system))

    # ── 面板 4：p95 延迟 + 100 ms 红线 ──
    geom_l = CMP_LAT
    limit = cd["budget"]["limit_ms"]
    max_p95 = max(x["p95_ms"] for x in cd["latency"])
    ymax = max(limit, max_p95) * 1.25
    if ymax <= 0:
        raise PlotDataError("latency budget limit is not positive: %r" % (limit,))
    y_lat = panel_frame(geom_l, ymax, [ymax * step / 5.0 for step in range(6)],
                        "p95 judgment latency (ms; red = 100 ms budget)",
                        label_fmt=lambda v: "%.0f" % v)
    y_limit = y_lat(limit)
    ops.append(_line(geom_l["left"], y_limit, geom_l["right"], y_limit, RED, 1.4,
                     dash=(4.0, 2.5), role="budget-line"))
    ops.append(_text(geom_l["left"] + 2.0, y_limit - 4.0,
                     "%d ms budget" % int(round(limit)), 8, color=RED,
                     role="budget-label"))
    group_bars(geom_l, y_lat, (32.0, 6.0), _rate_colour,
               lambda s, e: lat_by_key[(s, e)]["p95_ms"], "lat-bar", min_height=1.0)
    group_w = (geom_l["right"] - geom_l["left"]) / float(len(systems))
    pair_w = 2 * 32.0 + 6.0
    for index, system in enumerate(systems):
        cx = geom_l["left"] + (index + 0.5) * group_w
        for j, set_name in enumerate(eval_sets):
            x = cx - pair_w / 2.0 + j * (32.0 + 6.0)
            item = lat_by_key[(system, set_name)]
            ops.append(_text(x + 16.0, y_lat(item["p95_ms"]) - 4.0,
                             _fmt_ms(item["p95_ms"]), 8, align="center",
                             color=BLACK,
                             role="lat-label:%s:%s" % (system, set_name)))

    # ── 脚注（必须以 "source:" 开头：仓库 PDF 校验器允许的唯一 6 pt 文本）──
    e1_sha = (cd["eval_meta"].get("E1") or {}).get("sha256") or "n/a"
    footnote = ("source: run-semantic{,-neural,-hybrid}.json + "
                "neural_vs_concept_ablation.json | MiniLM-L12 ONNX int8 sha %s | "
                "thr 0.45/0.69 (D_dev) | E1 sha %s"
                % (str(cd["encoder_variants"]["C-neural"]["model_sha256"])[:8],
                   str(e1_sha)[:8]))
    ops.append(_text(8.0, CMP_FOOTNOTE_Y, footnote, 6, color=AXIS, role="footnote"))
    return ops


# ── 图 2：神经 vs 概念 2x2 消融热力图 ──────────────────────────────────────
def _shade_rgb(fraction: float) -> tuple[float, float, float]:
    value = 1.0 - 0.72 * max(0.0, min(1.0, fraction))
    return (round(value, 4), round(value, 4), round(value, 4))


def _ablation_panel(ops: list[dict], panel: dict, block: dict, max_count: int) -> None:
    grid_left = panel["grid_left"]
    grid_top = ABL_GRID_TOP
    cell = ABL_CELL
    ops.append(_text(panel["center"], 64.0, "%s (n=%d)" % (panel["set"], block["n"]),
                     9, bold=True, align="center", color=BLACK, role="abl-panel-title"))
    ops.append(_text(grid_left + cell * 0.5, grid_top - 12.0, "neural hit", 8,
                     align="center", color=AXIS, role="abl-col-header"))
    ops.append(_text(grid_left + cell * 1.5, grid_top - 12.0, "neural miss", 8,
                     align="center", color=AXIS, role="abl-col-header"))
    ops.append(_text(grid_left - 6.0, grid_top + cell * 0.5 + 3.0, "offline hit", 8,
                     align="right", color=AXIS, role="abl-row-header"))
    ops.append(_text(grid_left - 6.0, grid_top + cell * 1.5 + 3.0, "offline miss", 8,
                     align="right", color=AXIS, role="abl-row-header"))
    cells = (
        (0, 0, "both", block["both"]),
        (0, 1, "offline only", block["offline_only"]),
        (1, 0, "neural only", block["neural_only"]),
        (1, 1, "neither", block["neither"]),
    )
    for row, col, label, count in cells:
        x = grid_left + col * cell
        y = grid_top + row * cell
        fraction = (float(count) / float(max_count)) if max_count else 0.0
        ops.append(_rect(x, y, cell, cell, fill=_shade_rgb(fraction),
                         stroke=AXIS, width=0.8, role="abl-cell"))
        colour = WHITE if fraction > 0.55 else BLACK
        ops.append(_text(x + cell / 2.0, y + cell / 2.0 - 2.0, "%d" % count, 11,
                         bold=True, align="center", color=colour, role="abl-count"))
        ops.append(_text(x + cell / 2.0, y + cell / 2.0 + 11.0, label, 8,
                         align="center", color=colour, role="abl-cat"))


def ablation_ops(abl: dict, cd: dict) -> list[dict]:
    """2x2 列联表热力图：both / offline_only / neural_only / neither（E1、E0 各一组）。

    这张图直接可视化"神经层对概念层零增量"：neural_only 那格在 E1 只有 1 条、E0 为 0，
    且 E1 那一条已被 S_B 的 zero_width 解码器拦住（图下方的注记说明"对系统 C 的增量"）。
    """
    ops: list[dict] = []
    sets = abl["sets"]
    max_count = max(max(block[k] for k in ("both", "offline_only", "neural_only",
                                           "neither"))
                    for block in sets.values())
    title = "Phase C ablation: neural vs intent-concept semantic channel"
    ops.append(_text(ABL_W / 2.0, 22.0, title,
                     _fit_text(title, 12.0, ABL_W - 50.0, True), bold=True,
                     align="center", color=BLACK, role="title"))
    subtitle = ("2x2 contingency of semantic-layer decisions (independent of the lexical "
                "layer S_B); cell numbers are item counts, shading = count")
    ops.append(_text(ABL_W / 2.0, 36.0, subtitle,
                     _fit_text(subtitle, 8.0, ABL_W - 30.0), align="center",
                     color=AXIS, role="subtitle"))
    for panel in ABL_PANELS:
        _ablation_panel(ops, panel, sets[panel["set"]], max_count)

    # 每个集合的读数 + “对系统 C 的增量”注记（来自 2x2 + S_B 漏拦清单）
    row = 196.0
    for set_name in (PRIMARY_SET, SECONDARY_SET):
        block = sets[set_name]
        offline_hits = block["both"] + block["offline_only"]
        neural_hits = block["both"] + block["neural_only"]
        line1 = ("%s: offline semantic hits %d, neural hits %d, both %d, neural-only %d"
                 % (set_name, offline_hits, neural_hits, block["both"],
                    block["neural_only"]))
        line2 = ("     neural-only items not caught by S_B: %d -> neural increment over "
                 "concept on system C = %d"
                 % (len(block["neural_only_added_to_C"]),
                    block["neural_increment_over_concept_on_C"]))
        ops.append(_text(30.0, row, line1, 8, color=BLACK, role="abl-note"))
        ops.append(_text(30.0, row + 12.0, line2, 8, color=BLACK, role="abl-note"))
        row += 24.0

    # 系统 C 相对 S_B 的净增量（三变体，来自 run JSON 的 delta × n）
    def _increment(label: str, set_name: str) -> int:
        values = cd["delta"].get(label) or {}
        n = next(b["n"] for b in cd["bars"]
                 if b["system"] == label and b["eval_set"] == set_name)
        return int(round(float(values.get(set_name, 0.0)) * n))

    pieces = []
    for label in C_VARIANTS:
        pieces.append("%s +%d/+%d" % (SYS_TICK[label],
                                      _increment(label, PRIMARY_SET),
                                      _increment(label, SECONDARY_SET)))
    summary1 = ("System C increment over S_B (items, E1/E0): " + " | ".join(pieces))
    summary2 = ("     -> neural adds nothing beyond concept; C-hybrid equals C-offline, "
                "only slower.")
    ops.append(_text(30.0, row + 4.0, summary1, 8, color=BLACK, role="abl-summary"))
    ops.append(_text(30.0, row + 16.0, summary2, 8, color=BLACK,
                     role="abl-summary"))
    row += 32.0
    thresholds = abl["thresholds"]
    criterion = ("Thresholds calibrated on D_dev only (concept %s / neural %s; max dev "
                 "recall s.t. zero new benign FP); n=40 per set."
                 % (_fmt_num(float(thresholds["offline_concept"])),
                    _fmt_num(float(thresholds["neural"]))))
    ops.append(_text(30.0, row, criterion, 8, color=AXIS, role="abl-criterion"))
    model1 = ("Neural encoder: paraphrase-multilingual-MiniLM-L12-v2, ONNX int8, "
              "local CPU; zero external API calls.")
    model2 = ("model sha256 %s (full hash in figure_data.json)."
              % str(cd["encoder_variants"]["C-neural"]["model_sha256"])[:16])
    ops.append(_text(30.0, row + 12.0, model1, 8, color=AXIS, role="abl-model-note"))
    ops.append(_text(30.0, row + 24.0, model2, 8, color=AXIS, role="abl-model-note"))
    footnote = ("source: run-semantic{,-neural,-hybrid}.json + "
                "neural_vs_concept_ablation.json | 2x2 counts: %s | neural increment "
                "derived with S_B miss lists"
                % abl["source_file"].split("/")[-1])
    ops.append(_text(8.0, ABL_FOOTNOTE_Y, footnote, 6, color=AXIS, role="footnote"))
    return ops


# ── 图 2：雷达图 ───────────────────────────────────────────────────────────
def radar_axis_angles(count: int) -> list[float]:
    """从正上方开始、顺时针的轴角度（度）。"""
    return [90.0 - 360.0 * index / float(count) for index in range(count)]


def radar_ops(rd: dict) -> list[dict]:
    """五轴雷达图：拦截率 / 1-FPR / 速度 / 覆盖意图数 / 可解释性。

    A / B / C-offline 实线填充；C-neural 只画虚线（消融对照，避免整块遮挡）。
    C-hybrid 与 C-offline 拦截率相同、只是更慢，不画（数值在 figure_data 里）。
    """
    ops: list[dict] = []
    series = rd["series"]
    dashed = set(rd["dashed_series"])
    axes = rd["axes"]
    angles = radar_axis_angles(len(axes))

    title = "Phase C: Capability Profile (E1 held-out)"
    ops.append(_text(RADAR_W / 2.0, 22.0, title,
                     _fit_text(title, 12.0, RADAR_W - 60.0, True), bold=True,
                     align="center", color=BLACK, role="title"))
    subtitle = ("solid: primary systems | dashed: neural ablation | axes 3-5 "
                "max-normalised | raw values in the table")
    ops.append(_text(RADAR_W / 2.0, 38.0, subtitle,
                     _fit_text(subtitle, 8.0, RADAR_W - 30.0), align="center",
                     color=AXIS, role="subtitle"))

    # 图例（四个序列）
    legend_x = (14.0, 104.0, 204.0, 322.0)
    for system, x in zip(series, legend_x):
        color = SYS_STYLE[system][1]
        ops.append(_rect(x, 49.0, 11.0, 9.0, fill=SYS_STYLE[system][2],
                         stroke=color, width=1.0,
                         dash=(2.5, 2.0) if system in dashed else None,
                         role="legend-sample"))
        ops.append(_text(x + 15.0, 56.0, SYS_STYLE[system][0], 8, color=BLACK,
                         role="legend-text"))

    cx, cy, radius = RADAR_CX, RADAR_CY, RADAR_R

    def point(angle: float, value: float) -> tuple[float, float]:
        rad = math.radians(angle)
        return (cx + radius * value * math.cos(rad),
                cy - radius * value * math.sin(rad))

    # 1) 实线序列的浅色填充（没有 alpha 通道，所以网格与描边必须后画）
    def _polygon_area(system: str) -> float:
        values = [axis["normalized"][system] for axis in axes]
        pts = [point(angle, value) for angle, value in zip(angles, values)]
        area = 0.0
        for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
            area += x1 * y2 - x2 * y1
        return abs(area) / 2.0

    filled = [s for s in series if s not in dashed]
    for system in sorted(filled, key=_polygon_area, reverse=True):
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

    # 4) 多边形描边（实线实心 / 消融只画虚线）+ 顶点
    for system in series:
        values = [axis["normalized"][system] for axis in axes]
        pts = [point(angle, value) for angle, value in zip(angles, values)]
        is_dashed = system in dashed
        if is_dashed:
            # 消融序列：只画虚线（不画实线多边形，否则虚线叠在实线上看不出来）
            for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
                ops.append(_line(x1, y1, x2, y2, SYS_STYLE[system][1], 1.6,
                                 dash=(4.0, 3.0), role="radar-dashed:%s" % system))
        else:
            ops.append(_polygon(pts, fill=None, stroke=SYS_STYLE[system][1],
                                width=1.6,
                                role="radar-polygon:%s" % system))
        for angle, value in zip(angles, values):
            vx, vy = point(angle, value)
            ops.append(_circle(vx, vy, 2.2, SYS_STYLE[system][1],
                               role="radar-marker:%s" % system))

    note = ("C-hybrid not plotted: identical interception to C-offline and only slower; "
            "raw values are in figure_data.json")
    ops.append(_text(RADAR_W / 2.0, 352.0, note, 8, align="center", color=AXIS,
                     role="radar-note"))

    # 图下方：各轴原始数值表（归一化只是画形状，原始数必须可见）
    label_x = 36.0
    column_x = (96.0, 176.0, 256.0, 336.0, 416.0)
    header = ["intercept", "1 - FPR", "1/p95", "intents", "explain"]
    ops.append(_text(label_x, RADAR_TABLE_HEADER_Y, "sys", 8, bold=True, color=AXIS,
                     role="table-header"))
    for text_value, x in zip(header, column_x):
        ops.append(_text(x, RADAR_TABLE_HEADER_Y, text_value, 8, bold=True,
                         align="center", color=AXIS, role="table-header"))
    for row, (system, row_y) in enumerate(zip(series, RADAR_TABLE_ROWS_Y)):
        ops.append(_text(label_x, row_y, SYS_TICK[system], 9, bold=True,
                         color=SYS_STYLE[system][1], role="table-row-label"))
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

    neural = rd["encoder"]["thresholds_by_backend"].get("neural")
    concept = rd["encoder"]["thresholds_by_backend"].get("offline_concept")
    footnote = ("source: run-semantic{,-neural}.json | concept thr %s | neural "
                "MiniLM-L12 ONNX int8 thr %s (D_dev) | E1 held-out"
                % (_fmt_num(float(concept)) if _is_number(concept) else "n/a",
                   _fmt_num(float(neural)) if _is_number(neural) else "n/a"))
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
def _import_pil():
    """导入 Pillow；本机 `.venv-neural` 没装 Pillow 时回退到 DSH 捆绑运行时的
    site-packages（同为 CPython 3.12 / win-amd64，ABI 一致）。

    这个回退只影响 PNG 栅格化后端；PDF 与所有数值逻辑都是纯标准库。
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
        return Image, ImageDraw, ImageFont
    except ImportError:
        pass
    dsh_home = os.environ.get("DSH_HOME") or ""
    runtimes = os.path.join(dsh_home, "dsh-runtimes") if dsh_home else ""
    if runtimes and os.path.isdir(runtimes):
        for name in sorted(os.listdir(runtimes)):
            candidate = os.path.join(runtimes, name, "dependencies", "python",
                                     "Lib", "site-packages")
            if not os.path.isdir(os.path.join(candidate, "PIL")):
                continue
            if candidate not in sys.path:
                sys.path.append(candidate)
            try:
                from PIL import Image, ImageDraw, ImageFont
                return Image, ImageDraw, ImageFont
            except ImportError:  # pragma: no cover - ABI mismatch
                continue
    raise PlotDataError(
        "PNG backend needs Pillow, which is missing from this interpreter (%s) and no "
        "bundled runtime with PIL was found; run with the bundled runtime python"
        % sys.executable)


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
    _Image, _ImageDraw, ImageFont = _import_pil()

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
    Image, ImageDraw, _ImageFont = _import_pil()

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
               title="Phase C: Five Systems on Held-out Variants",
               subject="run %s" % run["run_label"],
               created_at=run["generated_at"])
    _write_png(ops, CMP_W, CMP_H, png_path, dpi)


def render_ablation(abl: dict, cd: dict, pdf_path: str, png_path: str, *,
                    dpi: int = DEFAULT_DPI) -> None:
    ops = ablation_ops(abl, cd)
    run = cd["run"]
    _write_pdf(ops, ABL_W, ABL_H, pdf_path,
               title="Phase C ablation: neural vs intent-concept semantic channel",
               subject="run %s" % run["run_label"],
               created_at=run["generated_at"])
    _write_png(ops, ABL_W, ABL_H, png_path, dpi)


def render_radar(rd: dict, pdf_path: str, png_path: str, *,
                 dpi: int = DEFAULT_DPI) -> None:
    ops = radar_ops(rd)
    run = rd["run"]
    _write_pdf(ops, RADAR_W, RADAR_H, pdf_path,
               title="Phase C: Capability Profile (E1 held-out)",
               subject="run %s" % run["run_label"],
               created_at=run["generated_at"])
    _write_png(ops, RADAR_W, RADAR_H, png_path, dpi)


# ── figure_data.json ──────────────────────────────────────────────────────
def default_captions(cd: dict, rd: dict, abl: dict) -> dict[str, str]:
    """按 JSON 事实生成三张图的 caption（数字全部从数据里取，不手填）。"""
    enc_variants = cd["encoder_variants"]
    fprs = {x["system"]: x for x in cd["fpr"]}
    bars = {(b["system"], b["eval_set"]): b for b in cd["bars"]}
    lat = {(x["system"], x["eval_set"]): x for x in cd["latency"]}
    adj = {(a["system"], a["eval_set"], a["subset"]): a for a in cd["adjudicated"]}
    delta = cd["delta"]
    sets = abl["sets"]

    def m_text(set_name: str) -> str:
        parts = []
        for system in C_VARIANTS:
            item = adj[(system, set_name, "M")]
            parts.append("%s %d/%d" % (SYS_TICK[system], item["intercepted"],
                                       item["n"]))
        return ", ".join(parts)

    delta_en = " | ".join(
        "%s %+.3f/%+.3f" % (SYS_TICK[label],
                            delta[label].get(PRIMARY_SET, 0.0),
                            delta[label].get(SECONDARY_SET, 0.0))
        for label in C_VARIANTS)
    ablation_en = (
        "E1: offline %d, neural %d, both %d, neural-only %d (the single neural-only "
        "item is already intercepted by S_B, so it adds 0 to system C); E0: offline %d, "
        "neural %d, both %d, neural-only %d."
        % (sets[PRIMARY_SET]["both"] + sets[PRIMARY_SET]["offline_only"],
           sets[PRIMARY_SET]["both"] + sets[PRIMARY_SET]["neural_only"],
           sets[PRIMARY_SET]["both"], sets[PRIMARY_SET]["neural_only"],
           sets[SECONDARY_SET]["both"] + sets[SECONDARY_SET]["offline_only"],
           sets[SECONDARY_SET]["both"] + sets[SECONDARY_SET]["neural_only"],
           sets[SECONDARY_SET]["both"], sets[SECONDARY_SET]["neural_only"]))
    ablation_zh = (
        "E1：概念层 %d 条、神经层 %d 条、both %d、neural-only %d（唯一一条神经独有"
        "命中已被 S_B 拦掉，对系统 C 增量为 0）；E0：概念 %d、神经 %d、both %d、"
        "neural-only %d。"
        % (sets[PRIMARY_SET]["both"] + sets[PRIMARY_SET]["offline_only"],
           sets[PRIMARY_SET]["both"] + sets[PRIMARY_SET]["neural_only"],
           sets[PRIMARY_SET]["both"], sets[PRIMARY_SET]["neural_only"],
           sets[SECONDARY_SET]["both"] + sets[SECONDARY_SET]["offline_only"],
           sets[SECONDARY_SET]["both"] + sets[SECONDARY_SET]["neural_only"],
           sets[SECONDARY_SET]["both"], sets[SECONDARY_SET]["neural_only"]))

    caption_zh = (
        "Phase C 五系统对比（官方 run_id %s）。上：A 词法闸门 / B Phase B 自愈补丁 / "
        "C-offline（intent-concept，主口径）/ C-neural（paraphrase-multilingual-"
        "MiniLM-L12-v2，ONNX int8，消融）/ C-hybrid（offline OR neural）在 E1（H0 零日集）"
        "与 E0（新 seed held-out）上的拦截率；双色表示评估集，绿色虚线为 H0 的 >0.90"
        "（E1）目标；蓝色注记为 C-B 净增量（E1/E0）：%s。中左：M 子集（必须拦的显式"
        "跨语红线）：%s；中右：良性 FPR（%s）。下：一次完整判定的 p95 延迟（红色虚线为 "
        "100 ms 预算）：C-offline %s / C-neural %s / C-hybrid %s ms。数据来源："
        "run-semantic{,-neural,-hybrid}.json 的 systems.*.{E1,E0}.rate、.latency.p95_ms、"
        "systems.*.benign.fpr、systems.*.{E1,E0}.adjudicated、systems.*.E1."
        "eligible_excluding_benign 与 delta；未平滑、插值或手填。阈值分别在 D_dev 上"
        "校准（concept %s / neural %s）。E1/E0 是 held-out 变体而非 OOD；延迟为单机 "
        "CPU 实测（神经变体含 ONNX int8 推理）。"
        % (cd["run"]["run_label"], delta_en, m_text(PRIMARY_SET),
           "全部 2/74 = " + _fmt_rate(fprs["C-offline"]["fpr"]),
           _fmt_ms(lat[("C-offline", PRIMARY_SET)]["p95_ms"]),
           _fmt_ms(lat[("C-neural", PRIMARY_SET)]["p95_ms"]),
           _fmt_ms(lat[("C-hybrid", PRIMARY_SET)]["p95_ms"]),
           _fmt_num(enc_variants["C-offline"]["threshold"]),
           _fmt_num(enc_variants["C-neural"]["threshold"])))
    caption_en = (
        "Phase C five-system comparison (official run_id %s). Top: interception rates of "
        "A (lexical gate), B (B + Phase B evolved patch), C-offline (intent-concept, "
        "primary), C-neural (paraphrase-multilingual-MiniLM-L12-v2, ONNX int8, ablation) "
        "and C-hybrid (offline OR neural) on E1 (H0 zero-day set) and E0 (fresh held-out); "
        "the two shades encode the evaluation set and the green dashed line is the H0 "
        "target of >0.90 on E1; blue text gives the C-B net increment (E1/E0): %s. "
        "Middle-left: the M subset (must-intercept explicit cross-lingual red lines): %s; "
        "middle-right: benign FPR (%s). Bottom: p95 latency of a complete judgment (red "
        "dashed line = 100 ms budget): C-offline %s / C-neural %s / C-hybrid %s ms. "
        "Sources: run-semantic{,-neural,-hybrid}.json fields systems.*.{E1,E0}.rate, "
        ".latency.p95_ms, systems.*.benign.fpr, systems.*.{E1,E0}.adjudicated, "
        "systems.*.E1.eligible_excluding_benign and delta; no smoothing, interpolation or "
        "hand-filled values. Thresholds are calibrated on D_dev only (concept %s / neural "
        "%s). E1/E0 are held-out variants, not OOD data; latency is a single-machine CPU "
        "measurement (the neural variants include ONNX int8 inference)."
        % (cd["run"]["run_label"], delta_en, m_text(PRIMARY_SET),
           "all 2/74 = " + _fmt_rate(fprs["C-offline"]["fpr"]),
           _fmt_ms(lat[("C-offline", PRIMARY_SET)]["p95_ms"]),
           _fmt_ms(lat[("C-neural", PRIMARY_SET)]["p95_ms"]),
           _fmt_ms(lat[("C-hybrid", PRIMARY_SET)]["p95_ms"]),
           _fmt_num(enc_variants["C-offline"]["threshold"]),
           _fmt_num(enc_variants["C-neural"]["threshold"])))

    abl_zh = (
        "Phase C 消融：神经编码器 vs 意图概念语义通道。2x2 列联表（both / offline only / "
        "neural only / neither，E1 与 E0 各一组，n=40，独立于词法层 S_B；格子颜色深浅 = "
        "计数）。%s 神经层对系统 C 的增量 = %d（E1）/ %d（E0）。这正是"
        "\"更强的编码器不等于更强的防御\"：系统 C 的拦截率 C-hybrid 与 C-offline 完全"
        "相同（%s / %s），只是慢约 %.1f 倍。数据来源："
        "experiments/phase-c/analysis/neural_vs_concept_ablation.json 的 sets.* 计数与 "
        "三份 run JSON 的 systems.B.*.misses 漏拦清单、delta；S_B 漏拦清单用于判断"
        "神经独有命中是否已被词法层覆盖。阈值 concept %s / neural %s 均在 D_dev 上校准。"
        % (ablation_zh,
           sets[PRIMARY_SET]["neural_increment_over_concept_on_C"],
           sets[SECONDARY_SET]["neural_increment_over_concept_on_C"],
           _fmt_rate(bars[("C-hybrid", PRIMARY_SET)]["rate"]),
           _fmt_rate(bars[("C-hybrid", SECONDARY_SET)]["rate"]),
           (lat[("C-hybrid", PRIMARY_SET)]["p95_ms"]
            / lat[("C-offline", PRIMARY_SET)]["p95_ms"]),
           _fmt_num(enc_variants["C-offline"]["threshold"]),
           _fmt_num(enc_variants["C-neural"]["threshold"])))
    abl_en = (
        "Phase C ablation: neural encoder vs intent-concept semantic channel. 2x2 "
        "contingency (both / offline only / neural only / neither) on E1 and E0, n=40 "
        "each, independent of the lexical layer S_B; cell shading = count. %s The neural "
        "increment over concept on system C is %d (E1) / %d (E0). This is the negative "
        "result: a stronger encoder is not a stronger defence -- C-hybrid interception is "
        "identical to C-offline (%s / %s) and only about %.1fx slower. Sources: "
        "experiments/phase-c/analysis/neural_vs_concept_ablation.json (sets.* counts) and "
        "the three run JSONs (systems.B.*.misses and delta); the S_B miss lists decide "
        "whether a neural-only hit is already covered by the lexical layer. Thresholds "
        "concept %s / neural %s, both calibrated on D_dev."
        % (ablation_en,
           sets[PRIMARY_SET]["neural_increment_over_concept_on_C"],
           sets[SECONDARY_SET]["neural_increment_over_concept_on_C"],
           _fmt_rate(bars[("C-hybrid", PRIMARY_SET)]["rate"]),
           _fmt_rate(bars[("C-hybrid", SECONDARY_SET)]["rate"]),
           (lat[("C-hybrid", PRIMARY_SET)]["p95_ms"]
            / lat[("C-offline", PRIMARY_SET)]["p95_ms"]),
           _fmt_num(enc_variants["C-offline"]["threshold"]),
           _fmt_num(enc_variants["C-neural"]["threshold"])))

    radar_zh = (
        "Phase C 能力剖面（E1 held-out）。实线：A / B / C-offline；虚线：C-neural（神经"
        "消融对照）。五轴 = 拦截率（E1）/ 1-FPR / 速度（1/p95）/ 覆盖意图数 / 可解释性；"
        "前两轴原生 0-1，后三轴按所画序列的最大值归一化，原始值见图下方表。C-neural 的"
        "速度轴明显内缩（p95 %s ms vs C-offline %s ms），拦截率轴也低于 C-offline。"
        "C-hybrid 与 C-offline 拦截率相同、只是更慢，未画（原始值见 figure_data.json）。"
        "来源：run-semantic{,-neural}.json；阈值 concept %s / neural %s 均在 D_dev 上校准；"
        "E1 是 held-out 变体而非 OOD。"
        % (_fmt_ms(lat[("C-neural", PRIMARY_SET)]["p95_ms"]),
           _fmt_ms(lat[("C-offline", PRIMARY_SET)]["p95_ms"]),
           _fmt_num(enc_variants["C-offline"]["threshold"]),
           _fmt_num(enc_variants["C-neural"]["threshold"])))
    radar_en = (
        "Phase C capability profile (E1 held-out). Solid: A / B / C-offline; dashed: "
        "C-neural (neural ablation). Axes: interception rate (E1) / 1-FPR / speed (1/p95) "
        "/ intent coverage / explainability; the first two use their native 0-1 scale and "
        "the last three are max-normalised across the plotted systems, with raw values in "
        "the table below. C-neural contracts sharply on the speed axis (p95 %s ms vs %s ms "
        "for C-offline) and is lower on interception. C-hybrid is not plotted (same "
        "interception as C-offline, only slower) but its raw values are in figure_data.json. "
        "Sources: run-semantic{,-neural}.json; thresholds concept %s / neural %s, both "
        "calibrated on D_dev; E1 is a held-out variant, not OOD data."
        % (_fmt_ms(lat[("C-neural", PRIMARY_SET)]["p95_ms"]),
           _fmt_ms(lat[("C-offline", PRIMARY_SET)]["p95_ms"]),
           _fmt_num(enc_variants["C-offline"]["threshold"]),
           _fmt_num(enc_variants["C-neural"]["threshold"])))
    return {"comparison_zh": caption_zh, "comparison_en": caption_en,
            "ablation_zh": abl_zh, "ablation_en": abl_en,
            "radar_zh": radar_zh, "radar_en": radar_en}


def write_figure_data(cd: dict, rd: dict, abl: dict, out_path: str, *,
                      captions: dict, figure_paths: dict) -> None:
    """写 `figure_data.json`：三张图的数据 + encoder_variants + ablation + captions + 边界。"""
    limitations = list(LIMITATIONS)
    data = {
        "schema": SCHEMA,
        "source_files": {
            "official": "experiments/phase-c/results/" + RUN_NAME,
            "neural": "experiments/phase-c/results/" + RUN_NEURAL_NAME,
            "hybrid": "experiments/phase-c/results/" + RUN_HYBRID_NAME,
            "ablation": "experiments/phase-c/analysis/" + ABLATION_NAME,
        },
        "source_schemas": {"run": RUN_SCHEMA, "ablation": abl.get("schema")},
        "generated_at": _now_iso(),
        "run": {
            "run_id": cd["run"]["run_id"],
            "run_label": cd["run"]["run_label"],
            "run_id_source": cd["run"]["run_id_source"],
            "source_generated_at": cd["run"]["generated_at"],
            "branch": cd["run"]["branch"],
            "generated_by": cd["run"]["generated_by"],
        },
        "encoder": cd["encoder"],
        "encoder_variants": cd["encoder_variants"],
        "eval_sets": cd["eval_meta"],
        "systems": list(cd["systems"]),
        "figures": {
            "comparison": {
                "pdf": figure_paths["comparison_pdf"],
                "png": figure_paths["comparison_png"],
                "page_pt": [CMP_W, CMP_H],
                "description_en": (
                    "five systems (A, B, C-offline, C-neural, C-hybrid) x (E1, E0) "
                    "interception rates, the M subset, benign FPR and p95 latency with "
                    "the 100 ms budget line; one-line note gives the E1 rates excluding "
                    "the two benign-creation items (n=38)"),
            },
            "ablation": {
                "pdf": figure_paths["ablation_pdf"],
                "png": figure_paths["ablation_png"],
                "page_pt": [ABL_W, ABL_H],
                "description_en": (
                    "2x2 contingency (both / offline_only / neural_only / neither) of "
                    "semantic-layer decisions on E1 and E0, visualising the zero neural "
                    "increment over the intent-concept layer"),
            },
            "radar": {
                "pdf": figure_paths["radar_pdf"],
                "png": figure_paths["radar_png"],
                "page_pt": [RADAR_W, RADAR_H],
                "description_en": (
                    "radar over interception rate / 1-FPR / speed / intent coverage / "
                    "explainability; solid A/B/C-offline, dashed C-neural ablation; axes "
                    "3-5 max-normalised"),
            },
        },
        "comparison": {
            "caption_zh": captions["comparison_zh"],
            "caption_en": captions["comparison_en"],
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
                "fitted to any backend's output. E0 contains no B items, which is why "
                "eligible_excluding_benign is null there: nothing is excluded, it is not a "
                "missing field."),
            "budget_line": dict(cd["budget"]),
            "delta_C_minus_B_by_variant": cd["delta"],
        },
        "ablation": {
            "caption_zh": captions["ablation_zh"],
            "caption_en": captions["ablation_en"],
            "source_file": abl["source_file"],
            "source_schema": abl.get("schema"),
            "library_sha256": abl["library_sha256"],
            "thresholds": abl["thresholds"],
            "sets": abl["sets"],
            "variants": abl["variants"],
            "claim": abl["claim"],
        },
        "radar": {
            "caption_zh": captions["radar_zh"],
            "caption_en": captions["radar_en"],
            "primary_eval_set": rd["eval_set"],
            "series": rd["series"],
            "dashed_series": rd["dashed_series"],
            "axes": rd["axes"],
            "not_plotted": rd["not_plotted"],
            "derived": rd["derived"],
            "caption_zh_extra": None,
        },
        "verification": {
            "rate_equals_intercepted_over_n": True,
            "fpr_equals_fp_count_over_n": True,
            "p95_ge_p50": True,
            "budget_pass_matches_max_p95": True,
            "delta_matches_C_minus_B": True,
            "ablation_2x2_sums_match_n": True,
            "ablation_variants_match_run_json": True,
            "hybrid_equals_offline": True,
            "neural_increment_over_concept_on_C": {
                PRIMARY_SET: abl["sets"][PRIMARY_SET]
                ["neural_increment_over_concept_on_C"],
                SECONDARY_SET: abl["sets"][SECONDARY_SET]
                ["neural_increment_over_concept_on_C"],
            },
            "note": ("all of the above were asserted while building the figures; any "
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
def generate(run_path: str, out_dir: str = DEFAULT_OUT, *,
             run_neural_path: str = DEFAULT_RUN_NEURAL,
             run_hybrid_path: str = DEFAULT_RUN_HYBRID,
             ablation_path: str = DEFAULT_ABLATION,
             dpi: int = DEFAULT_DPI, allow_missing_e0: bool = False,
             caption_zh: str | None = None,
             caption_en: str | None = None) -> dict:
    """读三份 run JSON + 消融 JSON → 校验 → 画三张图 → 写 figure_data.json。"""
    if not isinstance(dpi, int) or dpi <= 0:
        raise PlotDataError("dpi must be a positive integer, got %r" % (dpi,))
    variant_runs = {
        "C-offline": load_run_semantic(run_path, allow_missing_e0=allow_missing_e0),
        "C-neural": load_run_semantic(run_neural_path,
                                      allow_missing_e0=allow_missing_e0),
        "C-hybrid": load_run_semantic(run_hybrid_path,
                                      allow_missing_e0=allow_missing_e0),
    }
    ablation = load_ablation(ablation_path)
    per = {label: build_comparison(run, allow_missing_e0=allow_missing_e0)
           for label, run in variant_runs.items()}
    cd = build_comparison_multi(variant_runs)
    rd = build_radar_multi({"C-offline": per["C-offline"],
                            "C-neural": per["C-neural"],
                            "C-hybrid": per["C-hybrid"]})
    ad = build_ablation(ablation, variant_runs)

    folder = out_dir if os.path.isabs(out_dir) else os.path.join(ROOT, out_dir)
    comparison_pdf = os.path.join(folder, CMP_PDF)
    comparison_png = os.path.join(folder, CMP_PNG)
    ablation_pdf = os.path.join(folder, ABL_PDF)
    ablation_png = os.path.join(folder, ABL_PNG)
    radar_pdf = os.path.join(folder, RADAR_PDF)
    radar_png = os.path.join(folder, RADAR_PNG)
    data_path = os.path.join(folder, DATA_JSON)

    render_comparison(cd, comparison_pdf, comparison_png, dpi=dpi)
    render_ablation(ad, cd, ablation_pdf, ablation_png, dpi=dpi)
    render_radar(rd, radar_pdf, radar_png, dpi=dpi)

    captions = default_captions(cd, rd, ad)
    if caption_zh is not None:
        captions["comparison_zh"] = caption_zh
    if caption_en is not None:
        captions["comparison_en"] = caption_en
    write_figure_data(
        cd, rd, ad, data_path,
        captions=captions,
        figure_paths={
            "comparison_pdf": _rel(comparison_pdf),
            "comparison_png": _rel(comparison_png),
            "ablation_pdf": _rel(ablation_pdf),
            "ablation_png": _rel(ablation_png),
            "radar_pdf": _rel(radar_pdf),
            "radar_png": _rel(radar_png),
        })
    return {
        "runs": variant_runs,
        "comparison": cd,
        "radar": rd,
        "ablation": ad,
        "paths": {
            "comparison_pdf": comparison_pdf,
            "comparison_png": comparison_png,
            "ablation_pdf": ablation_pdf,
            "ablation_png": ablation_png,
            "radar_pdf": radar_pdf,
            "radar_png": radar_png,
            "figure_data": data_path,
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="plot_phase_c.py",
        description="Phase C A2: five-system comparison + neural-vs-concept ablation "
                    "heatmap + capability radar (hand-written vector PDF + Pillow PNG, "
                    "no matplotlib).")
    parser.add_argument("--run", default=DEFAULT_RUN, metavar="PATH",
                        help="official run-semantic.json (auto -> offline_concept)")
    parser.add_argument("--run-neural", default=DEFAULT_RUN_NEURAL, metavar="PATH",
                        help="neural ablation run-semantic-neural.json")
    parser.add_argument("--run-hybrid", default=DEFAULT_RUN_HYBRID, metavar="PATH",
                        help="hybrid ablation run-semantic-hybrid.json")
    parser.add_argument("--ablation", default=DEFAULT_ABLATION, metavar="PATH",
                        help="neural_vs_concept_ablation.json")
    parser.add_argument("--out", default=DEFAULT_OUT, metavar="DIR",
                        help="output directory (default: %(default)s)")
    parser.add_argument("--dpi", type=int, default=DEFAULT_DPI,
                        help="PNG resolution (default: %(default)s)")
    parser.add_argument("--allow-missing-e0", action="store_true",
                        help="accept runs without E0 (never imputed)")
    parser.add_argument("--caption-zh", default=None,
                        help="override the comparison figure caption_zh")
    parser.add_argument("--caption-en", default=None,
                        help="override the comparison figure caption_en")
    args = parser.parse_args(argv)

    try:
        result = generate(args.run, args.out,
                          run_neural_path=args.run_neural,
                          run_hybrid_path=args.run_hybrid,
                          ablation_path=args.ablation,
                          dpi=args.dpi,
                          allow_missing_e0=args.allow_missing_e0,
                          caption_zh=args.caption_zh,
                          caption_en=args.caption_en)
    except PlotDataError as exc:
        print("ERROR: %s" % exc, file=sys.stderr)
        return 2
    except Exception as exc:  # pragma: no cover - I/O / Pillow failures
        print("ERROR: rendering failed: %s: %s" % (type(exc).__name__, exc),
              file=sys.stderr)
        return 1

    cd = result["comparison"]
    rd = result["radar"]
    ad = result["ablation"]
    print("official run: %s (%s)" % (cd["run"]["run_label"],
                                     cd["run"]["run_id_source"]))
    print("eval sets: %s  (E1 n=%s sha=%s)"
          % (", ".join(cd["eval_sets"]),
             (cd["eval_meta"].get("E1") or {}).get("count"),
             str((cd["eval_meta"].get("E1") or {}).get("sha256"))[:12]))
    for label in C_VARIANTS:
        meta = cd["encoder_variants"][label]
        print("%-10s backend=%-16s is_neural=%-5s uses_neural=%-5s thr=%-5s "
              "E1=%.4f E0=%.4f p95(E1/E0)=%.2f/%.2f"
              % (label, meta["backend_effective"], str(meta["is_neural"]).lower(),
                 str(meta["uses_neural_backend"]).lower(),
                 _fmt_num(meta["threshold"]), meta["rates"][PRIMARY_SET],
                 meta["rates"][SECONDARY_SET], meta["p95_ms"][PRIMARY_SET],
                 meta["p95_ms"][SECONDARY_SET]))
    for eval_set in cd["eval_sets"]:
        cells = []
        for system in cd["systems"]:
            bar = next(b for b in cd["bars"]
                       if b["system"] == system and b["eval_set"] == eval_set)
            cells.append("%s=%.4f (%d/%d)"
                         % (SYS_TICK[system], bar["rate"], bar["intercepted"],
                            bar["n"]))
        print("intercept %s: %s" % (eval_set, "  ".join(cells)))
    for eval_set in cd["eval_sets"]:
        cells = []
        for system in C_VARIANTS:
            item = next(a for a in cd["adjudicated"]
                        if a["system"] == system and a["eval_set"] == eval_set
                        and a["subset"] == "M")
            cells.append("%s M=%d/%d" % (SYS_TICK[system], item["intercepted"],
                                          item["n"]))
        print("M subset %s: %s" % (eval_set, "  ".join(cells)))
    for entry in cd["eligible"]:
        if entry["eval_set"] == PRIMARY_SET:
            print("eligible excl. benign (E1): %s n=%s rate=%s"
                  % (SYS_TICK[entry["system"]], entry["n"], entry["rate"]))
    print("latency budget: limit=%s ms, per-variant pass=%r, worst p95=%.4f ms"
          % (cd["budget"]["limit_ms"],
             {k: v["pass"] for k, v in cd["budget"]["per_variant"].items()},
             cd["budget"]["worst_p95_ms"]))
    for eval_set in cd["eval_sets"]:
        block = ad["sets"][eval_set]
        print("ablation %s: both=%d offline_only=%d neural_only=%d neither=%d | "
              "neural increment over concept on C=%d"
              % (eval_set, block["both"], block["offline_only"],
                 block["neural_only"], block["neither"],
                 block["neural_increment_over_concept_on_C"]))
    for axis in rd["axes"]:
        raw = "  ".join("%s=%.4g" % (SYS_TICK[s], axis["raw"][s])
                        for s in rd["series"])
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
