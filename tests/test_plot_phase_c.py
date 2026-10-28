# -*- coding: utf-8 -*-
"""Phase C · A2 图自测（自跑脚本，无 pytest；末行固定输出 "N passed, M failed"）

覆盖（契约 §8.1 / 验收命令 `python tests/test_plot_phase_c.py`）：

1. **缺字段 → 退出码 2 并点名**：rate / latency / fpr / limit_ms / intent_count / E0
   缺失时必须 exit 2 且 stderr 出现该 JSON 路径；数值不一致（rate != intercepted/n、
   fpr != fp_count/n、pass 与 p95 矛盾）同样 exit 2 —— 图不会画一张手填的假图。
2. **PDF 结构**（手写矢量）：`%PDF-1.4` 头、`%%EOF` 尾、`startxref` 指向 xref、
   xref 偏移正确、单页、MediaBox 精确、无位图、文本对象全为
   `BT /F1 <size> Tf 1 0 0 1 <x> <y> Tm (...) Tj ET`、全部 ASCII、字号 >= 8pt
   （唯一例外：以 "source:" 开头的 6pt 脚注）、文本在页面内、无禁用词。
3. **PNG**：尺寸 = 版面 pt × dpi / 72、dpi=300、非空白（有实际绘制内容）。
4. **数值逐点一致**：figure_data.json 里每个柱/延迟/FPR/雷达原始值都 == 输入 JSON
   的对应字段；归一化值 == 原始值（native 轴）或 raw/raw_max（max_normalized 轴）；
   caption / limitations 写明 non-neural / held-out-not-OOD。
5. **真实 run-semantic.json**（若已由 Lead 产出）：用它重出到 `.tmp/` 并与
   `experiments/phase-c/figures/` 已提交的图逐点比对，防止图过期；不存在则打印 SKIP。
6. **论文章节**（若已写）：`paper/{sections,sections-zh}/06h-phase-c.tex` 表格里的
   数字必须与 run-semantic.json 一致（容差 = 打印精度），H0 指定英文句子必须**原样**
   出现，顶部注释必须写明"待 H0 决定"与 `\\input` 接入说明。
7. **确定性**：同一输入两次渲染的 PDF 字节完全一致。

运行：
    python tests/test_plot_phase_c.py        # 退出码 0 = 全过；1 = 有断言失败
"""
from __future__ import annotations

import contextlib
import copy
import io
import json
import os
import re
import shutil
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import plot_phase_c as plot  # noqa: E402

TMP_ROOT = os.path.join(ROOT, ".tmp", "test-plot-phase-c")

# ── 合成 fixture（结构测试专用；不是实验数据）────────────────────────────────
FIXTURE = {
    "_fixture_note": "SYNTHETIC fixture embedded in tests/test_plot_phase_c.py; NOT "
                     "experimental data. Values only exercise layout and validation.",
    "schema": "phase-c-run-semantic-v1",
    "run_id": "run-semantic-fixture-20261028-190000",
    "generated_at": "2026-10-28T19:00:00+0800",
    "branch": "feat/phase-c-semantic-guard",
    "encoder": {
        "backend": "offline_concept",
        "is_neural": False,
        "intent_library_sha256": "0" * 63 + "f",
        "intent_count": 9,
        "threshold": 0.45,
        "threshold_calibrated_on": "D_dev",
    },
    "eval_sets": {
        "E1": {"path": "experiments/phase-b/runs/run-20261028-185512/zeroday_set.jsonl",
               "count": 40,
               "sha256": "432e08a2d47cdc968f80803be64cf337d43767781459d4431b180f1d6f7a210c",
               "source": "fixture"},
        "E0": {"path": "experiments/phase-c/eval/zeroday_fresh.jsonl", "count": 40,
               "sha256": "1" * 64, "source": "fixture"},
        "benign": {"path": "experiments/phase-b/corpora/benign.jsonl", "count": 74},
    },
    "systems": {
        "A": {
            "E1": {"n": 40, "intercepted": 15, "rate": 0.375,
                   "latency": {"n": 40, "p50_ms": 0.61, "p95_ms": 0.84,
                               "max_ms": 1.2, "mean_ms": 0.63}, "breakdown": {}, "misses": [],
                   "adjudicated": {
                       "M": {"n": 3, "intercepted": 0, "rate": 0.0},
                       "R": {"n": 4, "intercepted": 0, "rate": 0.0},
                       "B": {"n": 2, "intercepted": 0, "rate": 0.0,
                             "false_positive_count": 0}},
                   "eligible_excluding_benign": {
                       "n": 38, "intercepted": 15, "rate": 0.394737,
                       "excluded_benign_creations": 2, "note": "fixture"}},
            "E0": {"n": 40, "intercepted": 14, "rate": 0.35,
                   "latency": {"n": 40, "p50_ms": 0.59, "p95_ms": 0.81,
                               "max_ms": 1.1, "mean_ms": 0.61}, "breakdown": {}, "misses": [],
                   "adjudicated": {
                       "M": {"n": 5, "intercepted": 0, "rate": 0.0},
                       "R": {"n": 2, "intercepted": 0, "rate": 0.0}}},
            "benign": {"n": 74, "fpr": 0.027027, "fp_count": 2, "fp_ids": ["b1", "b2"],
                       "latency": {"n": 74, "p50_ms": 0.6, "p95_ms": 0.8,
                                   "max_ms": 1.0, "mean_ms": 0.62}},
        },
        "B": {
            "E1": {"n": 40, "intercepted": 31, "rate": 0.775,
                   "latency": {"n": 40, "p50_ms": 3.9, "p95_ms": 4.2,
                               "max_ms": 6.5, "mean_ms": 4.0}, "breakdown": {}, "misses": [],
                   "adjudicated": {
                       "M": {"n": 3, "intercepted": 0, "rate": 0.0},
                       "R": {"n": 4, "intercepted": 0, "rate": 0.0},
                       "B": {"n": 2, "intercepted": 0, "rate": 0.0,
                             "false_positive_count": 0}},
                   "eligible_excluding_benign": {
                       "n": 38, "intercepted": 31, "rate": 0.815789,
                       "excluded_benign_creations": 2, "note": "fixture"}},
            "E0": {"n": 40, "intercepted": 30, "rate": 0.75,
                   "latency": {"n": 40, "p50_ms": 3.8, "p95_ms": 4.1,
                               "max_ms": 6.0, "mean_ms": 3.9}, "breakdown": {}, "misses": [],
                   "adjudicated": {
                       "M": {"n": 5, "intercepted": 0, "rate": 0.0},
                       "R": {"n": 2, "intercepted": 0, "rate": 0.0}}},
            "benign": {"n": 74, "fpr": 0.027027, "fp_count": 2, "fp_ids": ["b1", "b2"],
                       "latency": {"n": 74, "p50_ms": 3.9, "p95_ms": 4.4,
                                   "max_ms": 7.0, "mean_ms": 4.1}},
        },
        "C": {
            "E1": {"n": 40, "intercepted": 38, "rate": 0.95,
                   "latency": {"n": 40, "p50_ms": 7.1, "p95_ms": 7.6,
                               "max_ms": 11.0, "mean_ms": 7.2}, "breakdown": {}, "misses": [],
                   "adjudicated": {
                       "M": {"n": 3, "intercepted": 3, "rate": 1.0},
                       "R": {"n": 4, "intercepted": 0, "rate": 0.0},
                       "B": {"n": 2, "intercepted": 0, "rate": 0.0,
                             "false_positive_count": 0}},
                   "eligible_excluding_benign": {
                       "n": 38, "intercepted": 38, "rate": 1.0,
                       "excluded_benign_creations": 2, "note": "fixture"}},
            "E0": {"n": 40, "intercepted": 37, "rate": 0.925,
                   "latency": {"n": 40, "p50_ms": 7.0, "p95_ms": 7.5,
                               "max_ms": 10.5, "mean_ms": 7.1}, "breakdown": {}, "misses": [],
                   "adjudicated": {
                       "M": {"n": 5, "intercepted": 4, "rate": 0.8},
                       "R": {"n": 2, "intercepted": 0, "rate": 0.0}}},
            "benign": {"n": 74, "fpr": 0.040541, "fp_count": 3, "fp_ids": ["b1", "b2", "b3"],
                       "latency": {"n": 74, "p50_ms": 7.2, "p95_ms": 7.8,
                                   "max_ms": 12.0, "mean_ms": 7.4}},
        },
    },
    "delta": {"C_minus_B_E1": 0.175, "C_minus_A_E1": 0.575, "B_minus_A_E1": 0.4,
              "C_minus_B_E0": 0.175, "C_minus_A_E0": 0.575, "B_minus_A_E0": 0.4},
    "latency_budget": {"limit_ms": 100, "worst_p95_ms": 7.6, "pass": True, "note": "fixture"},
    "budget": {"api_calls": 0, "api_tokens_used": 0},
    "residual_misses_C": {"E1": [{"id": "z01", "semantic_score": 0.72}],
                          "E0": [{"id": "f01", "semantic_score": 0.7}]},
    "limitations": ["fixture"],
}

H0_SENTENCE = (
    "Our findings demonstrate the limitation of first-order lexical co-evolution. The "
    "plateau at 0.775 indicates that structural mutation (Second-Order Cybernetics) is "
    "mandatory. By introducing a semantic guard, Phase C achieved a structural leap, "
    "restoring the system's requisite variety against semantic mutations."
)

FORBIDDEN_FIGURE_TERMS = (
    "vsm", "rate-distortion", "rate distortion", "cyber-cognitive", "cognitive envelope",
    "action loop", "adaptive evolution", "collective mind", "multi-agent", "multimodal",
)

REAL_RUN = os.path.join(ROOT, "experiments", "phase-c", "results", "run-semantic.json")
REAL_FIG_DIR = os.path.join(ROOT, "experiments", "phase-c", "figures")
EN_TEX = os.path.join(ROOT, "paper", "sections", "06h-phase-c.tex")
ZH_TEX = os.path.join(ROOT, "paper", "sections-zh", "06h-phase-c.tex")

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(cond), detail))
    if cond:
        print("[PASS] %s" % name)
    else:
        print("[FAIL] %s%s" % (name, (": " + detail) if detail else ""))
    return bool(cond)


def _reset_dir(path: str) -> str:
    """只允许清空 .tmp/test-plot-phase-c/ 下的目录（防误删）。"""
    absolute = os.path.abspath(path)
    allowed = os.path.abspath(TMP_ROOT)
    if not (absolute == allowed or absolute.startswith(allowed + os.sep)):
        raise RuntimeError("refusing to clear a path outside %s: %s" % (allowed, absolute))
    shutil.rmtree(absolute, ignore_errors=True)
    os.makedirs(absolute, exist_ok=True)
    return absolute


def write_run(path: str, mutate=None) -> str:
    data = copy.deepcopy(FIXTURE)
    if mutate is not None:
        mutate(data)
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return path


def drop_path(data: dict, dotted: str) -> None:
    parts = dotted.split(".")
    cursor = data
    for key in parts[:-1]:
        cursor = cursor[key]
    del cursor[parts[-1]]


def run_cli(argv: list[str]) -> tuple[int, str, str]:
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = plot.main(argv)
    return code, out.getvalue(), err.getvalue()


# ── PDF / PNG 独立解析（不信任 canvas 对象）────────────────────────────────
TEXT_OBJ = re.compile(
    r"^BT /F([12]) (-?[\d.]+) Tf 1 0 0 1 (-?[\d.]+) (-?[\d.]+) Tm "
    r"\(((?:[^()\\]|\\.)*)\) Tj ET$")


def _pdf_unescape(value: str) -> str:
    return re.sub(r"\\([()\\])", r"\1", value)


def parse_pdf(path: str) -> dict:
    data = open(path, "rb").read()
    info: dict = {"size": len(data), "problems": [], "strings": [], "sizes": [],
                  "texts": [], "content": b"", "mediabox": None, "pages": 0}
    if not data.startswith(b"%PDF-1.4"):
        info["problems"].append("header is not %PDF-1.4")
    if not data.rstrip().endswith(b"%%EOF"):
        info["problems"].append("does not end with %%EOF")
    match = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", data)
    if not match:
        info["problems"].append("missing startxref/%%EOF pair")
        return info
    xref_at = int(match.group(1))
    if data[xref_at:xref_at + 4] != b"xref":
        info["problems"].append("startxref does not point at xref")
    trailer_parts = data[xref_at:].split(b"trailer")
    table = trailer_parts[0].decode("latin-1")
    entries = re.findall(r"(\d{10}) (\d{5}) ([nf])", table)
    info["xref_entries"] = len(entries)
    trailer = trailer_parts[1].decode("latin-1") if len(trailer_parts) > 1 else ""
    if "/Root" not in trailer:
        info["problems"].append("trailer has no /Root")
    size_match = re.search(r"/Size\s+(\d+)", trailer)
    if size_match and int(size_match.group(1)) != len(entries):
        info["problems"].append("trailer /Size != xref entries")
    for index, (offset, _gen, kind) in enumerate(entries):
        if kind != "n":
            continue
        expect = b"%d 0 obj" % index
        offset = int(offset)
        if data[offset:offset + len(expect)] != expect:
            info["problems"].append("xref offset for object %d is wrong" % index)
    objects = {}
    for match in re.finditer(rb"(\d+) 0 obj(.*?)endobj", data, re.S):
        objects[int(match.group(1))] = match.group(2)
    pages = [n for n, body in objects.items()
             if b"/Type /Page " in body or b"/Type /Page>" in body]
    info["pages"] = len(pages)
    if len(pages) != 1:
        info["problems"].append("expected 1 page, found %d" % len(pages))
    if not any(b"/Type /Catalog" in body for body in objects.values()):
        info["problems"].append("no /Type /Catalog")
    if pages:
        media = re.search(rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]",
                          objects[pages[0]])
        if media:
            info["mediabox"] = tuple(float(g) for g in media.groups())
        else:
            info["problems"].append("no /MediaBox")
    content = b""
    for body in objects.values():
        if b"/Length" in body and b"stream" in body and b"/Type" not in body:
            start = body.find(b"stream") + len(b"stream")
            if body[start:start + 1] == b"\n":
                start += 1
            content += body[start:body.rfind(b"endstream")]
    info["content"] = content
    if b"/Subtype /Image" in content or b" BI " in content or b"Do" in content:
        info["problems"].append("content contains a raster image / XObject")
    if not content:
        info["problems"].append("empty content stream")
        return info
    text = content.decode("latin-1")
    width = info["mediabox"][2] if info["mediabox"] else 0.0
    height = info["mediabox"][3] if info["mediabox"] else 0.0
    for block in re.findall(r"BT.*?ET", text, re.S):
        normalized = " ".join(block.split())
        match = TEXT_OBJ.match(normalized)
        if not match:
            info["problems"].append("malformed text object: %r" % normalized[:80])
            continue
        _font, size, tx, ty, raw = match.groups()
        raw = _pdf_unescape(raw)
        size, tx, ty = float(size), float(tx), float(ty)
        info["sizes"].append(size)
        info["strings"].append(raw)
        info["texts"].append((size, tx, ty, raw))
        floor = 6.0 if raw.startswith("source:") else 8.0
        if size < floor - 1e-9:
            info["problems"].append("font %.2fpt < %.1fpt: %r" % (size, floor, raw))
        if any(ord(ch) > 126 for ch in raw):
            info["problems"].append("non-ASCII text: %r" % (raw,))
        if not (-1.0 <= tx <= width + 1.0) or not (-1.0 <= ty <= height + 1.0):
            info["problems"].append("text outside page: %r at (%.1f, %.1f)" % (raw, tx, ty))
    joined = " ".join(info["strings"]).lower()
    for bad in FORBIDDEN_FIGURE_TERMS:
        if bad in joined:
            info["problems"].append("forbidden term %r in figure text" % bad)
    return info


def png_info(path: str) -> dict:
    from PIL import Image

    with Image.open(path) as image:
        dpi = image.info.get("dpi")
        rgb = image.convert("RGB")
        pixels = rgb.getdata()
        total = rgb.size[0] * rgb.size[1]
        nonwhite = sum(1 for value in pixels if value != (255, 255, 255))
        return {"size": image.size, "dpi": dpi, "nonwhite": nonwhite, "total": total}


# ── 论文章节解析 ───────────────────────────────────────────────────────────
def _tex_lines(path: str) -> list[str]:
    with io.open(path, encoding="utf-8") as fh:
        text = fh.read()
    return text.splitlines()


def tex_main_rows(path: str) -> dict[str, list[str]]:
    """主表每行 8 个单元格：[E1 k/n, E1 rate, E0 k/n, E0 rate, p95E1, p95E0, FPR, FP/n]。"""
    rows: dict[str, list[str]] = {}
    for raw_line in _tex_lines(path):
        line = raw_line.split("%", 1)[0].strip()
        match = re.match(r"^([ABC])\s*[（(]", line)  # 行首是系统字母 + 括号说明
        if not match:
            continue
        cells = [cell.strip().rstrip("\\").strip() for cell in line.split("&")]
        if len(cells) < 9:
            continue
        rows[match.group(1)] = cells[1:9]
    return rows


def tex_adjudicated_rows(path: str) -> dict[tuple[str, str], list[str]]:
    """判定口径表每行 3 个单元格（A/B/C 的 k/n）；行首形如 `E1-M (...)`。"""
    rows: dict[tuple[str, str], list[str]] = {}
    for raw_line in _tex_lines(path):
        line = raw_line.split("%", 1)[0].strip()
        match = re.match(r"^(E[01])-(M|R|B)\b", line)
        if not match:
            continue
        cells = [cell.strip().rstrip("\\").strip() for cell in line.split("&")]
        if len(cells) < 4:
            continue
        rows[(match.group(1), match.group(2))] = cells[1:4]
    return rows


def _cell_float(cell: str) -> float:
    cleaned = re.sub(r"[^0-9.\-]", "", cell)
    return float(cleaned)


def _cell_fraction(cell: str) -> tuple[int, int] | None:
    match = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", cell)
    if not match:
        return None
    return int(match.group(1)), int(match.group(2))


def check_tex_numbers(path: str, run: dict, label: str) -> None:
    rows = tex_main_rows(path)
    check("%s: 主表含 A/B/C 三行" % label, set(rows) == {"A", "B", "C"},
          repr(sorted(rows)))
    for system in ("A", "B", "C"):
        cells = rows.get(system)
        if cells is None:
            check("%s: S_%s 主表行存在" % (label, system), False)
            continue
        expected = run["systems"][system]
        try:
            e1_frac = _cell_fraction(cells[0])
            e1 = _cell_float(cells[1])
            e0_frac = _cell_fraction(cells[2])
            e0 = _cell_float(cells[3])
            p95_e1 = _cell_float(cells[4])
            p95_e0 = _cell_float(cells[5])
            fpr = _cell_float(cells[6])
            fp_frac = _cell_fraction(cells[7])
        except (ValueError, TypeError) as exc:
            check("%s: S_%s 主表单元格可解析" % (label, system), False, str(exc))
            continue
        ok = (e1_frac == (expected["E1"]["intercepted"], expected["E1"]["n"])
              and e0_frac == (expected["E0"]["intercepted"], expected["E0"]["n"])
              and abs(e1 - expected["E1"]["rate"]) <= 5e-4
              and abs(e0 - expected["E0"]["rate"]) <= 5e-4
              and abs(p95_e1 - expected["E1"]["latency"]["p95_ms"]) <= 5e-4
              and abs(p95_e0 - expected["E0"]["latency"]["p95_ms"]) <= 5e-4
              and abs(fpr - expected["benign"]["fpr"]) <= 5e-4
              and fp_frac == (expected["benign"]["fp_count"], expected["benign"]["n"]))
        check("%s: S_%s 主表数值与 run-semantic.json 逐点一致" % (label, system), ok,
              "tex=%r json=%r" % (cells, {
                  "E1_kn": (expected["E1"]["intercepted"], expected["E1"]["n"]),
                  "E1_rate": expected["E1"]["rate"],
                  "E0_kn": (expected["E0"]["intercepted"], expected["E0"]["n"]),
                  "E0_rate": expected["E0"]["rate"],
                  "E1_p95": expected["E1"]["latency"]["p95_ms"],
                  "E0_p95": expected["E0"]["latency"]["p95_ms"],
                  "fpr": expected["benign"]["fpr"]}))
        # 应拦口径（E1，n=38）必须写进章节（表注或正文）
        eligible = run["systems"][system]["E1"].get("eligible_excluding_benign")
        if eligible is not None:
            text = io.open(path, encoding="utf-8").read()
            check("%s: S_%s 的 E1 应拦口径 %.3f 出现在章节" % (label, system,
                                                              eligible["rate"]),
                  ("%.3f" % eligible["rate"]) in text)

    adj_rows = tex_adjudicated_rows(path)
    expected_subsets = set()
    for system in ("A", "B", "C"):
        for eval_set in ("E1", "E0"):
            for subset in run["systems"][system][eval_set]["adjudicated"]:
                expected_subsets.add((eval_set, subset))
    check("%s: 判定口径表含全部 E1/E0 x M/R/B 行（%d 行）"
          % (label, len(expected_subsets)),
          set(adj_rows) == expected_subsets,
          "tex=%r expected=%r" % (sorted(adj_rows), sorted(expected_subsets)))
    for (eval_set, subset), cells in adj_rows.items():
        ok = True
        detail = []
        for index, system in enumerate(("A", "B", "C")):
            expected = run["systems"][system][eval_set]["adjudicated"][subset]
            fraction = _cell_fraction(cells[index])
            if fraction != (expected["intercepted"], expected["n"]):
                ok = False
                detail.append("%s got=%r expected=%r"
                              % (system, fraction,
                                 (expected["intercepted"], expected["n"])))
        check("%s: 判定口径 %s-%s 的 A/B/C 与 JSON 一致" % (label, eval_set, subset),
              ok, "; ".join(detail[:2]))


def normalized_tex(path: str) -> str:
    with io.open(path, encoding="utf-8") as fh:
        text = fh.read()
    text = re.sub(r"(?m)(?<!\\)%.*$", "", text)      # 去掉行内注释
    text = re.sub(r"\\allowbreak\s*", "", text)
    text = re.sub(r"\s+", " ", text)
    return text


def tex_labels(path: str) -> set[str]:
    text = io.open(path, encoding="utf-8").read()
    return set(re.findall(r"\\label\{([^}]+)\}", text))


def check_latex_static(path: str, label: str,
                       known_labels: set[str] | None = None) -> None:
    """静态 LaTeX 健全性（本机无 TeX 工具链，编译不了，只能静态查）。

    - 花括号平衡；\\begin/\\end 配对且顺序正确；
    - 每个 \\ref{} 必须有 \\label{} 来源（本文件或英文版，中文版经 xr 导入）；
    - 不允许残留占位符（TBD/TODO/PLACEHOLDER/?）。
    """
    text = io.open(path, encoding="utf-8").read()
    stripped = re.sub(r"(?m)(?<!\\)%.*$", "", text)
    brace_text = stripped.replace("\\{", "").replace("\\}", "")
    check("%s: 花括号平衡" % label, brace_text.count("{") == brace_text.count("}"),
          "{=%d }=%d" % (brace_text.count("{"), brace_text.count("}")))
    stack, problems = [], []
    for kind, env in re.findall(r"\\(begin|end)\{([^}]*)\}", stripped):
        if kind == "begin":
            stack.append(env)
        else:
            if not stack or stack.pop() != env:
                problems.append(env)
    check("%s: begin/end 环境配对且嵌套正确" % label,
          not problems and not stack, "problems=%r stack=%r" % (problems, stack))
    labels_here = tex_labels(path)
    refs = set(re.findall(r"\\ref\{([^}]+)\}", stripped))
    allowed = labels_here | (known_labels or set())
    check("%s: 所有 \\ref 都有 \\label 来源" % label, refs <= allowed,
          "undefined=%r" % sorted(refs - allowed))
    for bad in ("TBD", "PLACEHOLDER", "\\TODO", "[A2-PENDING]"):
        check("%s: 没有残留占位符 %r" % (label, bad), bad not in stripped)
    check("%s: 引用的 PDF 副本存在于 paper/figures/"
          % label,
          all(os.path.isfile(os.path.join(ROOT, "paper", figure))
              for figure in re.findall(r"\\includegraphics\[[^\]]*\]\{([^}]+)\}", stripped)))


# ── 图数据逐点核对 ────────────────────────────────────────────────────────
def compare_figure_data(figure_data: dict, run: dict, label: str) -> None:
    bars = {(item["system"], item["eval_set"]): item
            for item in figure_data["comparison"]["bars"]}
    check("%s: 柱状图有 6 根柱（3 系统 x 2 集合）" % label, len(bars) == 6, str(sorted(bars)))
    for system in ("A", "B", "C"):
        for eval_set in ("E1", "E0"):
            item = bars.get((system, eval_set))
            expected = run["systems"][system][eval_set]
            check("%s: bar %s/%s == JSON rate" % (label, system, eval_set),
                  item is not None
                  and item["rate"] == expected["rate"]
                  and item["intercepted"] == expected["intercepted"]
                  and item["n"] == expected["n"]
                  and item["source_field"]
                  == "systems.%s.%s.rate" % (system, eval_set),
                  "got=%r expected_rate=%r" % (item, expected["rate"]))
    latency = {(item["system"], item["eval_set"]): item
               for item in figure_data["comparison"]["latency"]}
    for system in ("A", "B", "C"):
        for eval_set in ("E1", "E0"):
            item = latency[(system, eval_set)]
            expected = run["systems"][system][eval_set]["latency"]
            check("%s: latency %s/%s == JSON p95/p50" % (label, system, eval_set),
                  item["p95_ms"] == expected["p95_ms"]
                  and item["p50_ms"] == expected["p50_ms"]
                  and item["source_field"]
                  == "systems.%s.%s.latency.p95_ms" % (system, eval_set),
                  "got=%r expected=%r" % (item, expected))
    fpr = {item["system"]: item for item in figure_data["comparison"]["fpr"]}
    for system in ("A", "B", "C"):
        expected = run["systems"][system]["benign"]
        item = fpr[system]
        check("%s: FPR %s == JSON fpr/fp_count" % (label, system),
              item["fpr"] == expected["fpr"] and item["fp_count"] == expected["fp_count"]
              and item["n"] == expected["n"]
              and item["source_field"] == "systems.%s.benign.fpr" % system,
              "got=%r expected=%r" % (item, expected))
    budget = figure_data["comparison"]["budget_line"]
    check("%s: 100 ms 红线取 JSON latency_budget.limit_ms / pass" % label,
          budget["limit_ms"] == run["latency_budget"]["limit_ms"]
          and budget["pass"] == run["latency_budget"]["pass"]
          and budget["worst_p95_ms"]
          == max(run["systems"][s][e]["latency"]["p95_ms"]
                 for s in ("A", "B", "C") for e in ("E1", "E0")),
          "got=%r" % (budget,))
    # ── adjudicated M/R/B 分组：figure_data 必须与 JSON 逐点一致 ──
    adjudicated = {(item["system"], item["eval_set"], item["subset"]): item
                   for item in figure_data["comparison"]["adjudicated"]}
    adj_ok, adj_detail = True, []
    expected_count = 0
    for system in ("A", "B", "C"):
        for eval_set in ("E1", "E0"):
            for subset, expected in run["systems"][system][eval_set]["adjudicated"].items():
                expected_count += 1
                item = adjudicated.get((system, eval_set, subset))
                if item is None or item["n"] != expected["n"] \
                        or item["intercepted"] != expected["intercepted"] \
                        or abs(item["rate"] - expected["intercepted"] / expected["n"]) > 1e-12 \
                        or item["source_field"] \
                        != "systems.%s.%s.adjudicated.%s.intercepted" % (
                            system, eval_set, subset):
                    adj_ok = False
                    adj_detail.append("%s/%s/%s got=%r expected=%r"
                                      % (system, eval_set, subset, item, expected))
    check("%s: adjudicated M/R/B 全部条目 == JSON（%d 条）" % (label, expected_count),
          adj_ok and len(adjudicated) == expected_count, "; ".join(adj_detail[:2]))
    # ── 应拦口径（E1）与 E0 的 null（无需排除）──
    eligible = {(item["system"], item["eval_set"]): item
                for item in figure_data["comparison"]["eligible_excluding_benign"]}
    elig_ok, elig_detail = True, []
    for system in ("A", "B", "C"):
        for eval_set in ("E1", "E0"):
            expected = run["systems"][system][eval_set].get("eligible_excluding_benign")
            item = eligible.get((system, eval_set))
            if expected is None:
                if item is None or item["n"] is not None or item["rate"] is not None:
                    elig_ok = False
                    elig_detail.append("%s/%s expected null, got %r"
                                       % (system, eval_set, item))
                continue
            if item is None or item["n"] != expected["n"] \
                    or item["intercepted"] != expected["intercepted"] \
                    or abs(item["rate"] - expected["rate"]) > 1e-12 \
                    or item["excluded_benign_creations"] \
                    != expected["excluded_benign_creations"]:
                elig_ok = False
                elig_detail.append("%s/%s got=%r expected=%r"
                                   % (system, eval_set, item, expected))
    check("%s: eligible_excluding_benign 与 JSON 一致（E0 的 null 表示无需排除）" % label,
          elig_ok, "; ".join(elig_detail[:2]))
    check("%s: figure_data 记录 run_id / threshold / is_neural=false" % label,
          figure_data["run"]["run_id"] == run["run_id"]
          and figure_data["encoder"]["threshold"] == run["encoder"]["threshold"]
          and figure_data["encoder"]["is_neural"] == run["encoder"]["is_neural"] is False
          and figure_data["encoder"]["threshold_calibrated_on"]
          == run["encoder"]["threshold_calibrated_on"])
    for eval_set in ("E1", "E0"):
        key = "C_minus_B_%s" % eval_set
        if key in run.get("delta", {}):
            check("%s: delta %s 原样写入" % (label, key),
                  figure_data["comparison"]["delta"].get(key) == run["delta"][key])
    # 雷达原始值
    axes = {axis["id"]: axis for axis in figure_data["radar"]["axes"]}
    check("%s: 雷达 5 个轴齐全" % label,
          set(axes) == {"intercept_rate", "one_minus_fpr", "speed", "intent_coverage",
                        "explainability"}, str(sorted(axes)))
    for system in ("A", "B", "C"):
        expected_e1 = run["systems"][system]["E1"]
        expected_fpr = run["systems"][system]["benign"]["fpr"]
        check("%s: 雷达 %s 拦截率/1-FPR/速度原值" % (label, system),
              axes["intercept_rate"]["raw"][system] == expected_e1["rate"]
              and abs(axes["one_minus_fpr"]["raw"][system] - (1.0 - expected_fpr)) < 1e-12
              and abs(axes["speed"]["raw"][system] - 1.0 / expected_e1["latency"]["p95_ms"])
              < 1e-12)
    check("%s: 雷达覆盖意图数 C == encoder.intent_count" % label,
          axes["intent_coverage"]["raw"]["C"] == run["encoder"]["intent_count"])
    check("%s: 雷达覆盖意图数 A/B 为结构 0" % label,
          axes["intent_coverage"]["raw"]["A"] == 0.0
          and axes["intent_coverage"]["raw"]["B"] == 0.0)
    check("%s: 可解释性为 schema 派生序数 1/1/3" % label,
          [axes["explainability"]["raw"][s] for s in ("A", "B", "C")] == [1.0, 1.0, 3.0])
    # 归一化自洽
    normalization_ok = True
    detail = []
    for axis in axes.values():
        for system in ("A", "B", "C"):
            raw = axis["raw"][system]
            if axis["scale"] == "native":
                expected = min(1.0, max(0.0, raw))
            else:
                expected = raw / axis["raw_max"]
            if abs(axis["normalized"][system] - expected) > 1e-12:
                normalization_ok = False
                detail.append("%s/%s %r != %r"
                              % (axis["id"], system, axis["normalized"][system], expected))
    check("%s: 雷达归一化值 == raw（native）/ raw/raw_max（max_normalized）" % label,
          normalization_ok, "; ".join(detail))
    # caption / limitations
    zh = figure_data["comparison"]["caption_zh"]
    en = figure_data["comparison"]["caption_en"]
    check("%s: caption_zh/en 非空且提到数据源" % label,
          bool(zh.strip()) and bool(en.strip())
          and "run-semantic.json" in zh and "run-semantic.json" in en)
    expected_encoder_en = "offline intent-concept encoder (non-neural)" \
        if not run["encoder"]["is_neural"] else "neural encoder"
    check("%s: caption_en 按 JSON 写 encoder 类型（non-neural）" % label,
          expected_encoder_en in en, en[:200])
    check("%s: caption_zh 写明 held-out 变体而非 OOD" % label,
          "held-out" in zh and ("OOD" in zh or "分布外" in zh))
    limitations = " ".join(figure_data["limitations"]).lower()
    check("%s: limitations 覆盖 non-neural / 非 OOD / 雷达归一化 / 可解释性序数" % label,
          "non-neural" in limitations and "out-of-distribution" in limitations
          and "max-normalised" in limitations and "ordinal" in limitations)


# ── 测试用例 ──────────────────────────────────────────────────────────────
def test_missing_fields() -> None:
    work = _reset_dir(os.path.join(TMP_ROOT, "missing"))
    cases = [
        ("systems.A.E1.rate", "rate"),
        ("systems.C.E1.latency.p95_ms", "latency"),
        ("systems.B.benign.fpr", "fpr"),
        ("latency_budget.limit_ms", "limit"),
        ("encoder.intent_count", "intent_count"),
        ("eval_sets.E1.sha256", "sha"),
    ]
    for path, tag in cases:
        def mutate(data, dotted=path):
            drop_path(data, dotted)

        run_path = write_run(os.path.join(work, "run_%s.json" % tag), mutate)
        code, _out, err = run_cli(["--run", run_path, "--out",
                                   os.path.join(work, "out_%s" % tag)])
        check("缺字段 %s -> exit 2 并点名" % path,
              code == 2 and path in err, "code=%r stderr=%r" % (code, err.strip()))
        check("缺字段 %s 时没有生产 figure_data.json" % path,
              not os.path.isfile(os.path.join(work, "out_%s" % tag, plot.DATA_JSON)))

    # E0 缺失：默认严格 exit 2；--allow-missing-e0 才允许 E1-only
    def drop_e0(data):
        del data["eval_sets"]["E0"]
        for system in ("A", "B", "C"):
            del data["systems"][system]["E0"]
        for key in ("C_minus_B_E0", "C_minus_A_E0", "B_minus_A_E0"):
            data["delta"].pop(key, None)
        data["latency_budget"]["worst_p95_ms"] = max(
            data["systems"][s]["E1"]["latency"]["p95_ms"] for s in ("A", "B", "C"))

    e0_less = write_run(os.path.join(work, "run_no_e0.json"), drop_e0)
    code, _out, err = run_cli(["--run", e0_less, "--out", os.path.join(work, "out_no_e0")])
    check("缺 E0（默认）-> exit 2 并点名 E0", code == 2 and "E0" in err,
          "code=%r stderr=%r" % (code, err.strip()))
    allowed_dir = os.path.join(work, "out_no_e0_allowed")
    code, _out, err = run_cli(["--run", e0_less, "--out", allowed_dir,
                               "--allow-missing-e0"])
    data_path = os.path.join(allowed_dir, plot.DATA_JSON)
    check("--allow-missing-e0 -> exit 0 且 figure_data 记录缺 E0（不插补）",
          code == 0 and os.path.isfile(data_path)
          and "E0" not in json.load(io.open(data_path, encoding="utf-8"))["eval_sets"]
          and any("E0 is absent" in item for item in
                  json.load(io.open(data_path, encoding="utf-8"))["limitations"]),
          "code=%r stderr=%r" % (code, err.strip()))

    # 文件不存在 / 数值不一致
    code, _out, err = run_cli(["--run", os.path.join(work, "nope.json"),
                               "--out", os.path.join(work, "out_nope")])
    check("run-semantic.json 不存在 -> exit 2", code == 2 and "not found" in err,
          "code=%r stderr=%r" % (code, err.strip()))

    def bad_rate(data):
        data["systems"]["C"]["E1"]["rate"] = 0.99

    code, _out, err = run_cli(["--run", write_run(os.path.join(work, "bad_rate.json"),
                                                  bad_rate),
                               "--out", os.path.join(work, "out_bad_rate")])
    check("rate != intercepted/n -> exit 2（拒绝画不一致的数）",
          code == 2 and "inconsistent" in err, "code=%r stderr=%r" % (code, err.strip()))

    def bad_fpr(data):
        data["systems"]["C"]["benign"]["fpr"] = 0.5

    code, _out, err = run_cli(["--run", write_run(os.path.join(work, "bad_fpr.json"),
                                                  bad_fpr),
                               "--out", os.path.join(work, "out_bad_fpr")])
    check("fpr != fp_count/n -> exit 2", code == 2 and "inconsistent" in err,
          "code=%r stderr=%r" % (code, err.strip()))

    def bad_budget(data):
        data["latency_budget"]["pass"] = False

    code, _out, err = run_cli(["--run", write_run(os.path.join(work, "bad_budget.json"),
                                                  bad_budget),
                               "--out", os.path.join(work, "out_bad_budget")])
    check("latency_budget.pass 与实测 p95 矛盾 -> exit 2",
          code == 2 and "inconsistent" in err, "code=%r stderr=%r" % (code, err.strip()))


def test_render_from_fixture() -> dict:
    work = _reset_dir(os.path.join(TMP_ROOT, "fixture"))
    run_path = write_run(os.path.join(work, "run-semantic.json"))
    out_dir = os.path.join(work, "figures")
    code, out, err = run_cli(["--run", run_path, "--out", out_dir])
    check("fixture 正常渲染 -> exit 0", code == 0, "stderr=%r" % err.strip())
    for name in ("phase_c_comparison.pdf", "phase_c_comparison.png",
                 "phase_c_radar.pdf", "phase_c_radar.png", "figure_data.json"):
        check("生成 %s" % name, os.path.isfile(os.path.join(out_dir, name)))
    check("CLI 打印每条线的数值（可人工核对）",
          "intercept E1:" in out and "intercept E0:" in out
          and "radar intent_coverage" in out)
    figure_data = json.load(io.open(os.path.join(out_dir, plot.DATA_JSON),
                                    encoding="utf-8"))
    compare_figure_data(figure_data, FIXTURE, "fixture")
    return {"work": work, "run_path": run_path, "out_dir": out_dir,
            "figure_data": figure_data}


def test_pdf_structure(context: dict) -> None:
    out_dir = context["out_dir"]
    for name, expected_box in (("phase_c_comparison.pdf", (504.0, 416.0)),
                               ("phase_c_radar.pdf", (504.0, 452.0))):
        info = parse_pdf(os.path.join(out_dir, name))
        check("%s: 结构无问题（头/尾/xref/单页/无位图/ASCII/字号/页内）" % name,
              not info["problems"], "; ".join(info["problems"][:4]))
        check("%s: MediaBox == %s" % (name, expected_box),
              info["mediabox"] is not None
              and abs(info["mediabox"][2] - expected_box[0]) < 1e-9
              and abs(info["mediabox"][3] - expected_box[1]) < 1e-9,
              repr(info["mediabox"]))
        check("%s: 文本对象 >= 30 且全部为 base-14 Helvetica 文本" % name,
              len(info["texts"]) >= 30, "texts=%d" % len(info["texts"]))
        strings = " ".join(info["strings"])
        required = ("Phase C", "source:")
        if name.startswith("phase_c_comparison"):
            required += ("E1", "E0", "A: lexical gate")
        else:
            required += ("A: lexical gate", "B: + evolved patch", "C: + semantic guard")
        check("%s: 含标题/系统标签/图例/来源脚注" % name,
              all(token in strings for token in required),
              "missing=%r" % [token for token in required if token not in strings])
        # 仓库自带 PDF 校验器（若存在）也必须 PASS
        verifier = os.path.join(ROOT, "paper", "figures", "verify_pdf.py")
        if os.path.isfile(verifier):
            sys.path.insert(0, os.path.dirname(verifier))
            try:
                import verify_pdf  # type: ignore

                verified = verify_pdf.parse_pdf(os.path.join(out_dir, name))
                verify_pdf.check_text(verified)
                check("%s: build/bi/figures/verify_pdf.py 复检 PASS" % name,
                      not verified["problems"], "; ".join(verified["problems"][:4]))
            except Exception as exc:  # pragma: no cover - verifier import failure
                check("%s: 仓库 verify_pdf.py 可复检" % name, False, repr(exc))
            finally:
                sys.path.pop(0)

    comparison = parse_pdf(os.path.join(out_dir, "phase_c_comparison.pdf"))
    strings = " ".join(comparison["strings"])
    # 100 ms 红线：红色 + 虚线，且线 y 坐标 == 版面公式算出的位置
    limit = FIXTURE["latency_budget"]["limit_ms"]
    max_p95 = max(FIXTURE["systems"][s][e]["latency"]["p95_ms"]
                  for s in ("A", "B", "C") for e in ("E1", "E0"))
    ymax = max(limit, max_p95) * 1.25
    y_line = plot.CMP_LAT["bottom"] - limit / ymax * (
        plot.CMP_LAT["bottom"] - plot.CMP_LAT["top"])
    pdf_y = plot.CMP_H - y_line
    content = comparison["content"].decode("latin-1")
    check("comparison: 100 ms 红线是红色虚线且 y 坐标正确",
          "0.85 0.1 0.1 RG" in content and "[4 2.5] 0 d" in content
          and ("%s %s m" % (plot._pdf_num(plot.CMP_LAT["left"]),
                            plot._pdf_num(pdf_y))) in content,
          "expected y=%s" % plot._pdf_num(pdf_y))
    check("comparison: 标注 100 ms 预算与 H0 0.90 目标线",
          "%d ms budget" % int(limit) in strings and "H0 target: > 0.90 on E1" in strings)
    check("comparison: M 子集面板存在且标注 C 的 3/3 与 A/B 的 0/3",
          "explicit red lines (M subset)" in strings
          and "3/3" in strings and "0/3" in strings and "4/5" in strings)
    eligible_note = ("E1 excluding 2 benign-creation items (n=38): "
                     "A %s | B %s | C %s"
                     % (plot._fmt_rate(FIXTURE["systems"]["A"]["E1"]
                                       ["eligible_excluding_benign"]["rate"]),
                        plot._fmt_rate(FIXTURE["systems"]["B"]["E1"]
                                       ["eligible_excluding_benign"]["rate"]),
                        plot._fmt_rate(FIXTURE["systems"]["C"]["E1"]
                                       ["eligible_excluding_benign"]["rate"])))
    check("comparison: 应拦口径（n=38）注记来自 JSON",
          eligible_note in strings, eligible_note)
    check("comparison: 图内写明 encoder 非神经与阈值来源",
          "is_neural=false" in strings and "thr 0.45 (D_dev)" in strings)
    check("comparison: 每根柱的数值标签都来自 JSON",
          all(plot._fmt_rate(FIXTURE["systems"][s][e]["rate"]) in strings
              for s in ("A", "B", "C") for e in ("E1", "E0")))
    check("comparison: p95 数值与 FPR 数值打印在图上",
          all(plot._fmt_ms(FIXTURE["systems"][s][e]["latency"]["p95_ms"]) in strings
              for s in ("A", "B", "C") for e in ("E1", "E0"))
          and all(plot._fmt_rate(FIXTURE["systems"][s]["benign"]["fpr"]) in strings
                  for s in ("A", "B", "C")))

    radar = parse_pdf(os.path.join(out_dir, "phase_c_radar.pdf"))
    radar_strings = " ".join(radar["strings"])
    check("radar: 五轴标签齐全",
          all(label in radar_strings for label in
              ("intercept rate (E1)", "1 - FPR", "speed 1/p95", "intent coverage",
               "explainability")))
    check("radar: 三系统图例齐全",
          all(label in radar_strings for label in
              ("A: lexical gate", "B: + evolved patch", "C: + semantic guard")))
    check("radar: 原始值表含 intent_count 与可解释性序数",
          "intents" in radar_strings and "explain" in radar_strings
          and "%d" % FIXTURE["encoder"]["intent_count"] in radar_strings)
    check("PDF 中没有禁用词",
          not any(bad in radar_strings.lower() for bad in FORBIDDEN_FIGURE_TERMS)
          and not any(bad in strings.lower() for bad in FORBIDDEN_FIGURE_TERMS))


def test_png(context: dict) -> None:
    out_dir = context["out_dir"]
    for name, expected_pt in (("phase_c_comparison.png", (504.0, 416.0)),
                              ("phase_c_radar.png", (504.0, 452.0))):
        info = png_info(os.path.join(out_dir, name))
        expected = (int(round(expected_pt[0] * 300 / 72.0)),
                    int(round(expected_pt[1] * 300 / 72.0)))
        check("%s: 尺寸 == 版面pt x 300dpi/72" % name, info["size"] == expected,
              "got=%r expected=%r" % (info["size"], expected))
        check("%s: dpi == 300" % name,
              info["dpi"] is not None
              and all(abs(float(value) - 300.0) < 0.5 for value in info["dpi"]),
              repr(info["dpi"]))
        ratio = info["nonwhite"] / float(info["total"])
        check("%s: 非空白（绘制像素 > 1%%）" % name, ratio > 0.01, "ratio=%.4f" % ratio)


def test_determinism(context: dict) -> None:
    work = context["work"]
    run_path = context["run_path"]
    dir_a = os.path.join(work, "det_a")
    dir_b = os.path.join(work, "det_b")
    code_a, _out, _err = run_cli(["--run", run_path, "--out", dir_a])
    code_b, _out, _err = run_cli(["--run", run_path, "--out", dir_b])
    check("确定性：两次渲染都成功", code_a == 0 and code_b == 0)
    for name in ("phase_c_comparison.pdf", "phase_c_radar.pdf"):
        with open(os.path.join(dir_a, name), "rb") as fh_a, \
                open(os.path.join(dir_b, name), "rb") as fh_b:
            check("确定性：%s 字节完全一致（CreationDate 取 JSON generated_at）" % name,
                  fh_a.read() == fh_b.read())
    data_a = json.load(io.open(os.path.join(dir_a, plot.DATA_JSON), encoding="utf-8"))
    data_b = json.load(io.open(os.path.join(dir_b, plot.DATA_JSON), encoding="utf-8"))
    for payload in (data_a, data_b):
        payload.pop("generated_at", None)
        for figure in payload["figures"].values():
            figure.pop("pdf", None)
            figure.pop("png", None)
    check("确定性：figure_data 除 generated_at / 输出路径外一致", data_a == data_b)

    low_dir = os.path.join(work, "dpi150")
    code, _out, _err = run_cli(["--run", run_path, "--out", low_dir, "--dpi", "150"])
    info = png_info(os.path.join(low_dir, "phase_c_comparison.png"))
    check("--dpi 150 -> PNG 尺寸按比例减半",
          code == 0 and info["size"] == (1050, 867), repr(info["size"]))


def test_real_run() -> None:
    if not os.path.isfile(REAL_RUN):
        print("[SKIP] 真实 run-semantic.json 尚未生成：跳过真实数据逐点核对"
              "（Lead 产出后重跑本测试即可）")
        return
    work = _reset_dir(os.path.join(TMP_ROOT, "real"))
    real = json.load(io.open(REAL_RUN, encoding="utf-8"))
    out_dir = os.path.join(work, "figures")
    code, out, err = run_cli(["--run", REAL_RUN, "--out", out_dir,
                              "--allow-missing-e0"])
    check("真实 run-semantic.json 渲染 -> exit 0", code == 0,
          "stderr=%r" % err.strip())
    if code != 0:
        return
    fresh = json.load(io.open(os.path.join(out_dir, plot.DATA_JSON), encoding="utf-8"))
    compare_figure_data(fresh, real, "real")
    check("真实数据：figure_data 记录的 source_generated_at == JSON generated_at",
          fresh["run"]["source_generated_at"] == real["generated_at"],
          "%r vs %r" % (fresh["run"]["source_generated_at"], real["generated_at"]))
    # 已提交的图必须与真实 JSON 同源（防过期）
    committed_path = os.path.join(REAL_FIG_DIR, plot.DATA_JSON)
    if os.path.isfile(committed_path):
        committed = json.load(io.open(committed_path, encoding="utf-8"))
        check("已提交 figure_data.json 的 source_generated_at == 真实 JSON（图不过期）",
              committed["run"]["source_generated_at"] == real["generated_at"],
              "%r vs %r" % (committed["run"]["source_generated_at"],
                            real["generated_at"]))
        normalized_committed = copy.deepcopy(committed)
        normalized_fresh = copy.deepcopy(fresh)
        for payload in (normalized_committed, normalized_fresh):
            payload.pop("generated_at", None)
            for figure in payload["figures"].values():
                figure.pop("pdf", None)
                figure.pop("png", None)
        check("已提交 figure_data.json 的全部数值 == 真实 JSON 重出的数值",
              normalized_committed == normalized_fresh)
        for name, expected_strings in (
                ("phase_c_comparison.pdf",
                 [plot._fmt_rate(real["systems"][s][e]["rate"])
                  for s in ("A", "B", "C") for e in ("E1", "E0") if e in real["systems"][s]]),
                ("phase_c_radar.pdf",
                 [plot._fmt_rate(real["systems"][s]["E1"]["rate"])
                  for s in ("A", "B", "C")])):
            path = os.path.join(REAL_FIG_DIR, name)
            if not os.path.isfile(path):
                check("已提交 %s 存在" % name, False)
                continue
            info = parse_pdf(path)
            strings = " ".join(info["strings"])
            check("已提交 %s: 结构 OK 且打印真实拦截率" % name,
                  not info["problems"] and all(text in strings for text in expected_strings),
                  "; ".join(info["problems"][:3]))
    else:
        print("[SKIP] experiments/phase-c/figures/figure_data.json 尚未提交")
    # 论文目录副本必须与 experiments 下的图一致（LaTeX 构建读 paper/figures/）
    paper_dir = os.path.join(ROOT, "paper", "figures")
    for name in ("phase_c_comparison.pdf", "phase_c_radar.pdf"):
        source = os.path.join(REAL_FIG_DIR, name)
        copy_path = os.path.join(paper_dir, name)
        if os.path.isfile(source):
            check("paper/figures/%s 存在且与 experiments/phase-c/figures 副本逐字节一致" % name,
                  os.path.isfile(copy_path)
                  and open(source, "rb").read() == open(copy_path, "rb").read())


def test_paper_sections() -> None:
    if not os.path.isfile(EN_TEX) and not os.path.isfile(ZH_TEX):
        print("[SKIP] 06h-phase-c.tex 尚未写入：跳过论文章节核对")
        return
    run = FIXTURE
    if os.path.isfile(REAL_RUN):
        run = json.load(io.open(REAL_RUN, encoding="utf-8"))
    for path, label in ((EN_TEX, "en"), (ZH_TEX, "zh")):
        if not os.path.isfile(path):
            check("%s 章节存在" % label, False, path)
            continue
        text = io.open(path, encoding="utf-8").read()
        body_no_macro = re.sub(r"(?m)^\\providecommand\{\\TODO\}.*$", "", text)
        check("%s: 没有残留 \\TODO 占位（数据冻结后必须填实）" % label,
              "\\TODO{" not in body_no_macro)
        comment_header = "\n".join(
            line for line in text.splitlines() if line.lstrip().startswith("%"))
        check("%s: 顶部注释写明'待 H0 决定'" % label,
              ("H0" in comment_header
               and ("待 H0" in comment_header or "H0 decision" in comment_header)))
        check("%s: 顶部注释给出 \\input 接入说明" % label,
              "\\input{sections" in comment_header or "\\input{sections" in text)
        check("%s: 正文出现 H0 指定英文句子（原样，空白归一化）" % label,
              H0_SENTENCE in normalized_tex(path))
        check("%s: 写明 encoder 为 offline / non-neural" % label,
              ("offline" in text.lower()) and ("non-neural" in text.lower()))
        check("%s: 写明数据来源 run-semantic.json（含 generated_at 或 run_id）" % label,
              "run-semantic.json" in text
              and (run.get("generated_at", "") in text
                   or "generated_at" in text or "run_id" in text))
        if isinstance(run.get("run_id"), str) and run["run_id"]:
            check("%s: 正文标注 run_id == run-semantic.json:run_id" % label,
                  run["run_id"] in text, run["run_id"])
        check("%s: 写明 E1/E0 是 held-out 变体而非 OOD" % label,
              "held-out" in text.lower()
              and ("OOD" in text or "out-of-distribution" in text.lower()
                   or "分布外" in text))
        # Lead 的口径（2026-10-28 裁决）：H0 指定句子只作 "the hypothesis this experiment
        # tests" 出现，紧跟实测限定；E1 增量 < +0.10（本轮 +0.075）或 FPR 上升时，
        # 句子必须被显式标注为"未被本实验支持/部分支持"。
        e1_gain = run.get("delta", {}).get("C_minus_B_E1")
        fpr_up = (run["systems"]["C"]["benign"]["fpr"]
                  > run["systems"]["B"]["benign"]["fpr"] + 1e-9)
        material = (isinstance(e1_gain, (int, float)) and e1_gain >= 0.10
                    and not fpr_up)
        if not material:
            normalized = normalized_tex(path).lower()
            check("%s: E1 增量不足 -> H0 句子被标注为未被本实验支持/部分支持" % label,
                  "not supported" in normalized or "未被本实验支持" in normalized
                  or "unsupported" in normalized or "partial" in normalized,
                  "E1_gain=%r fpr_up=%r" % (e1_gain, fpr_up))
        # Lead 给的实测限定句（可原样使用的关键片段）
        check("%s: 写明 E1 未达预注册 >0.90 目标" % label,
              ("0.850" in text or "0.85" in text)
              and ("0.90" in text or "0.9" in text)
              and ("target" in text.lower() or "目标" in text))
        check("%s: 写明 M 子集从 0%% 提升到 100%% 与 R 类 0/4 未覆盖" % label,
              ("3/3" in text) and ("0/4" in text) and ("M" in text))
        check("%s: 引用两张图的 PDF（paper/figures 副本路径）" % label,
              "figures/phase_c_comparison.pdf" in text
              and "figures/phase_c_radar.pdf" in text)
        known = tex_labels(EN_TEX) if os.path.isfile(EN_TEX) else set()
        check_latex_static(path, label, known)
        check_tex_numbers(path, run, label)


def main() -> int:
    if not os.path.isdir(os.path.join(ROOT, ".tmp")):
        os.makedirs(os.path.join(ROOT, ".tmp"), exist_ok=True)
    print("=" * 80)
    print("tests/test_plot_phase_c.py  |  python %s" % sys.version.split()[0])
    print("=" * 80)
    test_missing_fields()
    context = test_render_from_fixture()
    test_pdf_structure(context)
    test_png(context)
    test_determinism(context)
    test_real_run()
    test_paper_sections()

    passed = sum(1 for _name, ok, _detail in RESULTS if ok)
    failed = len(RESULTS) - passed
    print("-" * 80)
    for name, ok, detail in RESULTS:
        if not ok:
            print("[FAILED] %s %s" % (name, detail))
    print("%d passed, %d failed" % (passed, failed))
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):  # pragma: no cover
            pass
    raise SystemExit(main())
