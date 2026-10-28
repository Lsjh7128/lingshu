# -*- coding: utf-8 -*-
"""Phase C · A2 图自测（自跑脚本，无 pytest；末行固定输出 "N passed, M failed"）

覆盖（契约 §8.1 + Lead 2026-10-28 的神经编码器消融要求）：

1. **缺字段 / 数据不一致 → 退出码 2 并点名**：三份 run JSON 的 rate / latency / fpr /
   threshold / model_sha256 / run_id 缺失，消融 JSON 的 2x2 计数缺失或与 n 不自洽，
   三份 run 的评估集不一致，hybrid 与 offline 拦截率不一致，消融 variants 与 run
   不一致 —— 全部 exit 2，不画自相矛盾的图。
2. **三张图与 figure_data.json**：五系统柱状图、2x2 消融热力图、雷达图；每个数都从
   三份 run JSON + 消融 JSON 逐点核对；encoder_variants（backend/threshold/model_id/
   model_sha256/is_neural/uses_neural_backend）与 ablation（2x2、variants、claim）齐全。
3. **PDF/PNG 结构**：手写矢量 PDF（头尾/xref/单页/MediaBox/无位图/全 ASCII/字号地板/
   页内/无禁用词），PNG 300dpi 且非空白；仓库 `paper/figures/verify_pdf.py` 复检。
4. **真实数据**：用三份官方 JSON + 消融 JSON 重出到 .tmp，核对 figure_data 与 JSON
   逐点一致、已提交图不过期、paper/figures 副本一致。
5. **论文章节**：主表/编码器表/判定口径表的数字与 run JSON 一致；H0 原句原样出现且被
   标注为"部分支持/未完全支持"；写明 neural ablation、MiniLM/ONNX int8、D_dev 召回
   28.9% vs 68.2%、"neural increment over concept = 0"、"更强编码器 ≠ 更强防御"。
6. **确定性**：同一输入两次渲染的 PDF 字节完全一致。

运行（`.venv-neural` 没有 Pillow 时，脚本会自动回退到 DSH 捆绑运行时的 site-packages）：
    .venv-neural\\Scripts\\python.exe -X utf8 tests\\test_plot_phase_c.py
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
REAL_RUN = os.path.join(ROOT, "experiments", "phase-c", "results", "run-semantic.json")
REAL_NEURAL = os.path.join(ROOT, "experiments", "phase-c", "results",
                           "run-semantic-neural.json")
REAL_HYBRID = os.path.join(ROOT, "experiments", "phase-c", "results",
                           "run-semantic-hybrid.json")
REAL_ABL = os.path.join(ROOT, "experiments", "phase-c", "analysis",
                        "neural_vs_concept_ablation.json")
REAL_FIG_DIR = os.path.join(ROOT, "experiments", "phase-c", "figures")
EN_TEX = os.path.join(ROOT, "paper", "sections", "06h-phase-c.tex")
ZH_TEX = os.path.join(ROOT, "paper", "sections-zh", "06h-phase-c.tex")

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

# ── 合成 fixture（结构测试专用；不是实验数据）────────────────────────────────
B_MISSES = {
    "E1": ["m01", "m02", "m03", "m04", "m05", "m06", "m07", "m08", "m09"],
    "E0": ["p01", "p02", "p03", "p04", "p05", "p06", "p07"],
}
ABL_OFFLINE_ONLY = {
    "E1": ["o01", "o02", "m01", "o03", "m02", "m03"],
    "E0": ["q01", "q02", "q03", "p01", "q04", "q05", "p02", "q06", "p03", "q07"],
}
E1_SHA = "432e08a2d47cdc968f80803be64cf337d43767781459d4431b180f1d6f7a210c"
E0_SHA = "5fa5398389834a6a592eca765110d1921ea928aa781d46da54ab035ca68636e2"
LIB_SHA = "bcfea896a5e5e6b1b4e5727c24bdadaf347b8baec7f219aecf69b65be7956d06"
MODEL_SHA = "66fc00f5f29afcaff34092e1bdd20008ca3918265a82fb9695a551e510cc4ebc"


def _fraction(rate: float, n: int) -> int:
    return int(round(rate * n))


def _block(k: int, n: int) -> dict:
    return {"n": n, "intercepted": k, "rate": k / float(n)}


def _entry(e1_rate: float, e0_rate: float, p95_e1: float, p95_e0: float,
           m_e1: int, m_e0: int) -> dict:
    k1, k2 = _fraction(e1_rate, 40), _fraction(e0_rate, 40)

    def latency(p95_e1: float, p95_e0: float) -> dict:
        return {
            "E1": {"n": 40, "p50_ms": p95_e1 * 0.8, "p95_ms": p95_e1,
                   "max_ms": p95_e1 * 1.5, "mean_ms": p95_e1 * 0.9},
            "E0": {"n": 40, "p50_ms": p95_e0 * 0.8, "p95_ms": p95_e0,
                   "max_ms": p95_e0 * 1.5, "mean_ms": p95_e0 * 0.9},
        }

    lat = latency(p95_e1, p95_e0)
    return {
        "E1": {"n": 40, "intercepted": k1, "rate": e1_rate, "latency": lat["E1"],
               "breakdown": {},
               "misses": [{"id": i} for i in B_MISSES["E1"]],
               "adjudicated": {
                   "M": _block(m_e1, 3), "R": _block(0, 4),
                   "B": {"n": 2, "intercepted": 0, "rate": 0.0,
                         "false_positive_count": 0}},
               "eligible_excluding_benign": {
                   "n": 38, "intercepted": k1, "rate": k1 / 38.0,
                   "excluded_benign_creations": 2, "note": "fixture"}},
        "E0": {"n": 40, "intercepted": k2, "rate": e0_rate, "latency": lat["E0"],
               "breakdown": {},
               "misses": [{"id": i} for i in B_MISSES["E0"]],
               "adjudicated": {"M": _block(m_e0, 5), "R": _block(0, 2)}},
        "benign": {"n": 74, "fpr": 2.0 / 74.0, "fp_count": 2,
                   "fp_ids": ["b1", "b2"],
                   "latency": {"n": 74, "p50_ms": 0.01, "p95_ms": 0.02,
                               "max_ms": 0.03, "mean_ms": 0.015}},
    }


def _run(backend_requested: str, backend_effective: str, is_neural: bool,
         threshold: float, c_e1: float, c_e0: float, p95_e1: float, p95_e0: float,
         m_e1: int, m_e0: int, run_id: str) -> dict:
    systems = {
        "A": _entry(0.375, 0.4, 0.02, 0.02, 0, 0),
        "B": _entry(0.775, 0.825, 0.25, 0.11, 0, 0),
        "C": _entry(c_e1, c_e0, p95_e1, p95_e0, m_e1, m_e0),
    }
    worst = max(p95_e1, p95_e0)
    return {
        "schema": "phase-c-run-semantic-v1",
        "run_id": run_id,
        "generated_at": "2026-10-28T22:45:16+0800",
        "branch": "feat/phase-c-semantic-guard",
        "encoder": {
            "backend_requested": backend_requested,
            "backend_effective": backend_effective,
            "is_neural": is_neural,
            "model_id": "paraphrase-multilingual-MiniLM-L12-v2",
            "model_sha256": MODEL_SHA,
            "embedding_dim": 384,
            "intent_library_sha256": LIB_SHA,
            "formula_version": "neural-multilingual-v1",
            "intent_count": 9,
            "threshold": threshold,
            "threshold_calibrated_on": "D_dev",
            "thresholds_by_backend": {"offline_concept": 0.45, "neural": 0.69},
        },
        "eval_sets": {
            "E1": {"path": "experiments/phase-b/runs/run-20261028-185512/zeroday_set.jsonl",
                   "count": 40, "sha256": E1_SHA, "source": "fixture"},
            "E0": {"path": "experiments/phase-c/eval/zeroday_fresh.jsonl",
                   "count": 40, "sha256": E0_SHA, "source": "fixture"},
            "benign": {"path": "experiments/phase-b/corpora/benign.jsonl", "count": 74},
        },
        "systems": systems,
        "delta": {
            "C_minus_B_E1": round(c_e1 - 0.775, 6),
            "C_minus_A_E1": round(c_e1 - 0.375, 6),
            "B_minus_A_E1": 0.4,
            "C_minus_B_E0": round(c_e0 - 0.825, 6),
            "C_minus_A_E0": round(c_e0 - 0.4, 6),
            "B_minus_A_E0": 0.425,
        },
        "latency_budget": {"limit_ms": 100.0, "worst_p95_ms": worst,
                           "pass": worst < 100.0, "note": "fixture"},
        "budget": {"api_calls": 0, "api_tokens_used": 0},
        "residual_misses_C": {"E1": [], "E0": []},
        "limitations": ["fixture"],
    }


FIXTURE_RUNS = {
    "C-offline": _run("auto", "offline_concept", False, 0.45, 0.85, 0.925, 1.2, 1.3,
                      3, 4, "run-semantic-fixture-auto"),
    "C-neural": _run("neural", "neural", True, 0.69, 0.775, 0.85, 11.0, 13.0,
                     0, 1, "run-semantic-fixture-neural"),
    "C-hybrid": _run("hybrid", "hybrid", False, 0.45, 0.85, 0.925, 13.0, 16.0,
                     3, 4, "run-semantic-fixture-hybrid"),
}


def _ablation() -> dict:
    def counts(set_name: str, both: int, neural_only: int) -> dict:
        offline_only = len(ABL_OFFLINE_ONLY[set_name])
        n = 40
        return {
            "set": set_name, "n": n, "both": both,
            "offline_only": offline_only,
            "offline_only_ids": list(ABL_OFFLINE_ONLY[set_name]),
            "neural_only": neural_only,
            "neural_only_items": ([{"id": "n01", "technique": "zero_width",
                                    "neural_score": 0.7285,
                                    "offline_score": 0.4325}]
                                  if neural_only else []),
            "neither": n - both - offline_only - neural_only,
            "offline_intercepted": both + offline_only,
            "neural_intercepted": both + neural_only,
            "neural_increment_over_offline": neural_only,
            "offline_increment_over_neural": offline_only,
        }

    def variant(label: str) -> dict:
        run = FIXTURE_RUNS[label]
        return {
            "encoder_backend": run["encoder"]["backend_effective"],
            "threshold": run["encoder"]["threshold"],
            "E1_rate": run["systems"]["C"]["E1"]["rate"],
            "E0_rate": run["systems"]["C"]["E0"]["rate"],
            "benign_fpr": run["systems"]["C"]["benign"]["fpr"],
            "p95_E1_ms": run["systems"]["C"]["E1"]["latency"]["p95_ms"],
            "p95_E0_ms": run["systems"]["C"]["E0"]["latency"]["p95_ms"],
        }

    return {
        "schema": "phase-c-neural-vs-concept-v1",
        "generated_at": "2026-10-28T22:45:52+0800",
        "library_sha256": LIB_SHA,
        "thresholds": {"offline_concept": 0.45, "neural": 0.69},
        "sets": {"E1": counts("E1", 0, 1), "E0": counts("E0", 1, 0)},
        "variants": {"auto": variant("C-offline"), "neural": variant("C-neural"),
                     "hybrid": variant("C-hybrid")},
    }


RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, cond: bool, detail: str = "") -> bool:
    RESULTS.append((name, bool(cond), detail))
    if cond:
        print("[PASS] %s" % name)
    else:
        print("[FAIL] %s%s" % (name, (": " + detail) if detail else ""))
    return bool(cond)


def _reset_dir(path: str) -> str:
    absolute = os.path.abspath(path)
    allowed = os.path.abspath(TMP_ROOT)
    if not (absolute == allowed or absolute.startswith(allowed + os.sep)):
        raise RuntimeError("refusing to clear a path outside %s: %s" % (allowed, absolute))
    shutil.rmtree(absolute, ignore_errors=True)
    os.makedirs(absolute, exist_ok=True)
    return absolute


def _write_json(path: str, data: dict) -> str:
    folder = os.path.dirname(os.path.abspath(path))
    os.makedirs(folder, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    return path


def write_fixture(work: str) -> dict:
    return {
        "official": _write_json(os.path.join(work, "run-semantic.json"),
                                copy.deepcopy(FIXTURE_RUNS["C-offline"])),
        "neural": _write_json(os.path.join(work, "run-semantic-neural.json"),
                              copy.deepcopy(FIXTURE_RUNS["C-neural"])),
        "hybrid": _write_json(os.path.join(work, "run-semantic-hybrid.json"),
                              copy.deepcopy(FIXTURE_RUNS["C-hybrid"])),
        "ablation": _write_json(os.path.join(work, "neural_vs_concept_ablation.json"),
                                _ablation()),
    }


def _fixture_data() -> dict:
    return {
        "official": copy.deepcopy(FIXTURE_RUNS["C-offline"]),
        "neural": copy.deepcopy(FIXTURE_RUNS["C-neural"]),
        "hybrid": copy.deepcopy(FIXTURE_RUNS["C-hybrid"]),
        "ablation": _ablation(),
    }


def _write_fixture_data(work: str, name: str, data: dict) -> dict:
    return {key: _write_json(os.path.join(work, name, key + ".json"), value)
            for key, value in data.items()}


def drop_path(data: dict, dotted: str) -> None:
    parts = dotted.split(".")
    cursor = data
    for key in parts[:-1]:
        cursor = cursor[key]
    del cursor[parts[-1]]


def run_cli(paths: dict, out_dir: str, extra: list[str] | None = None
            ) -> tuple[int, str, str]:
    args = ["--run", paths["official"], "--run-neural", paths["neural"],
            "--run-hybrid", paths["hybrid"], "--ablation", paths["ablation"],
            "--out", out_dir]
    if extra:
        args.extend(extra)
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = plot.main(args)
    return code, out.getvalue(), err.getvalue()


# ── PDF / PNG 解析 ──────────────────────────────────────────────────────────
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
            info["problems"].append("text outside page: %r at (%.1f, %.1f)"
                                    % (raw, tx, ty))
    joined = " ".join(info["strings"]).lower()
    for bad in FORBIDDEN_FIGURE_TERMS:
        if bad in joined:
            info["problems"].append("forbidden term %r in figure text" % bad)
    return info


def png_info(path: str) -> dict:
    Image = plot._import_pil()[0]
    with Image.open(path) as image:
        dpi = image.info.get("dpi")
        rgb = image.convert("RGB")
        pixels = list(rgb.getdata())
        total = rgb.size[0] * rgb.size[1]
        nonwhite = sum(1 for value in pixels if value != (255, 255, 255))
        return {"size": image.size, "dpi": dpi, "nonwhite": nonwhite, "total": total}


# ── 图数据逐点核对 ────────────────────────────────────────────────────────
def _source_run(runs: dict, system: str) -> dict:
    return runs[system if system in plot.C_VARIANTS else "C-offline"]


def _run_system(system: str) -> str:
    return "C" if system in plot.C_VARIANTS else system


def ablation_neural_only_already_caught(block: dict, offline_run: dict) -> bool:
    """neural-only 命中里，真正给系统 C 增加拦截的条数必须为 0。"""
    b_misses = {m["id"] for m in offline_run["systems"]["B"]["E1"].get("misses", [])}
    neural_ids = [item["id"] for item in block.get("neural_only_items") or []]
    return all(item_id not in b_misses for item_id in neural_ids)


def compare_figure_data(figure_data: dict, runs: dict, abl: dict, label: str) -> None:
    bars = {(item["system"], item["eval_set"]): item
            for item in figure_data["comparison"]["bars"]}
    check("%s: 五系统 x 两集合 = 10 根柱" % label, len(bars) == 10,
          str(sorted(bars)))
    for system in plot.SYSTEMS:
        run, run_system = _source_run(runs, system), _run_system(system)
        for eval_set in ("E1", "E0"):
            expected = run["systems"][run_system][eval_set]
            item = bars.get((system, eval_set))
            check("%s: bar %s/%s == run JSON rate" % (label, system, eval_set),
                  item is not None
                  and item["rate"] == expected["rate"]
                  and item["intercepted"] == expected["intercepted"]
                  and item["n"] == expected["n"]
                  and item["source_field"]
                  == "systems.%s.%s.rate" % (run_system, eval_set),
                  "got=%r expected=%r" % (item, expected["rate"]))
    latency = {(item["system"], item["eval_set"]): item
               for item in figure_data["comparison"]["latency"]}
    fpr = {item["system"]: item for item in figure_data["comparison"]["fpr"]}
    for system in plot.SYSTEMS:
        run, run_system = _source_run(runs, system), _run_system(system)
        for eval_set in ("E1", "E0"):
            item = latency[(system, eval_set)]
            expected = run["systems"][run_system][eval_set]["latency"]
            check("%s: latency %s/%s == JSON p95/p50" % (label, system, eval_set),
                  item["p95_ms"] == expected["p95_ms"]
                  and item["p50_ms"] == expected["p50_ms"])
        expected_benign = run["systems"][run_system]["benign"]
        item = fpr[system]
        check("%s: FPR %s == JSON fpr/fp_count" % (label, system),
              item["fpr"] == expected_benign["fpr"]
              and item["fp_count"] == expected_benign["fp_count"]
              and item["n"] == expected_benign["n"])
    adj = {(item["system"], item["eval_set"], item["subset"]): item
           for item in figure_data["comparison"]["adjudicated"]}
    expected_adj = 0
    adj_ok, adj_detail = True, []
    for system in plot.SYSTEMS:
        run, run_system = _source_run(runs, system), _run_system(system)
        for eval_set in ("E1", "E0"):
            for subset, expected in run["systems"][run_system][eval_set][
                    "adjudicated"].items():
                expected_adj += 1
                item = adj.get((system, eval_set, subset))
                if item is None or item["n"] != expected["n"] \
                        or item["intercepted"] != expected["intercepted"] \
                        or abs(item["rate"]
                               - expected["intercepted"] / expected["n"]) > 1e-12:
                    adj_ok = False
                    adj_detail.append("%s/%s/%s got=%r expected=%r"
                                      % (system, eval_set, subset, item, expected))
    check("%s: adjudicated M/R/B 全部 %d 条 == JSON" % (label, expected_adj),
          adj_ok and len(adj) == expected_adj, "; ".join(adj_detail[:2]))
    eligible = {(item["system"], item["eval_set"]): item
                for item in figure_data["comparison"]["eligible_excluding_benign"]}
    elig_ok = True
    for system in plot.SYSTEMS:
        run, run_system = _source_run(runs, system), _run_system(system)
        expected = run["systems"][run_system]["E1"]["eligible_excluding_benign"]
        item = eligible.get((system, "E1"))
        if item is None or item["n"] != expected["n"] \
                or item["rate"] != expected["rate"]:
            elig_ok = False
    check("%s: eligible_excluding_benign（E1, n=38）与 JSON 一致" % label, elig_ok)
    check("%s: E0 的 eligible 为 null（无需排除）" % label,
          all(eligible[(s, "E0")]["n"] is None for s in plot.SYSTEMS))
    budget = figure_data["comparison"]["budget_line"]
    worst = max(item["p95_ms"] for item in figure_data["comparison"]["latency"])
    check("%s: 100 ms 红线与 worst p95 一致" % label,
          budget["limit_ms"] == runs["C-offline"]["latency_budget"]["limit_ms"]
          and budget["worst_p95_ms"] == worst,
          "got=%r worst=%r" % (budget, worst))
    for variant in plot.C_VARIANTS:
        for eval_set in ("E1", "E0"):
            key = "C_minus_B_%s" % eval_set
            if key in runs[variant]["delta"]:
                check("%s: %s delta %s 原样写入" % (label, variant, key),
                      figure_data["comparison"]["delta_C_minus_B_by_variant"]
                      [variant][eval_set] == runs[variant]["delta"][key])
    enc_variants = figure_data["encoder_variants"]
    check("%s: encoder_variants 三个变体齐全" % label,
          set(enc_variants) == set(plot.C_VARIANTS))
    for variant in plot.C_VARIANTS:
        run = runs[variant]
        meta = enc_variants[variant]
        check("%s: %s encoder 元数据 == JSON" % (label, variant),
              meta["backend_effective"] == run["encoder"]["backend_effective"]
              and meta["is_neural"] == run["encoder"]["is_neural"]
              and meta["threshold"] == run["encoder"]["threshold"]
              and meta["model_id"] == run["encoder"]["model_id"]
              and meta["model_sha256"] == run["encoder"]["model_sha256"]
              and meta["threshold_calibrated_on"] == "D_dev"
              and meta["run_id"] == run["run_id"])
    check("%s: hybrid 标记 uses_neural_backend=True（尽管 is_neural=false）" % label,
          enc_variants["C-hybrid"]["uses_neural_backend"] is True
          and enc_variants["C-hybrid"]["is_neural"] is False)
    figure_abl = figure_data["ablation"]
    check("%s: ablation 2x2 计数 == 消融 JSON" % label,
          all(figure_abl["sets"][s][k] == abl["sets"][s][k]
              for s in ("E1", "E0")
              for k in ("both", "offline_only", "neural_only", "neither", "n")))
    check("%s: ablation variants == 三份 run 的 C 指标" % label,
          all(figure_abl["variants"][v]["E1_rate"]
              == runs[v]["systems"]["C"]["E1"]["rate"]
              and figure_abl["variants"][v]["E0_rate"]
              == runs[v]["systems"]["C"]["E0"]["rate"]
              and figure_abl["variants"][v]["threshold"]
              == runs[v]["encoder"]["threshold"]
              for v in plot.C_VARIANTS))
    check("%s: neural increment over concept on C == 0（E1、E0）" % label,
          figure_abl["claim"]["neural_increment_over_concept_on_C"]["E1"] == 0
          and figure_abl["claim"]["neural_increment_over_concept_on_C"]["E0"] == 0)
    check("%s: neural-only 命中已被 S_B 覆盖（E1 的 1 条）" % label,
          ablation_neural_only_already_caught(abl["sets"]["E1"],
                                              runs["C-offline"]))
    radar = figure_data["radar"]
    check("%s: 雷达四条序列（C-neural 虚线）+ 记录未画的 C-hybrid" % label,
          radar["series"] == ["A", "B", "C-offline", "C-neural"]
          and radar["dashed_series"] == ["C-neural"]
          and "C-hybrid" in radar["not_plotted"])
    axes = {axis["id"]: axis for axis in radar["axes"]}
    for system in radar["series"]:
        run, run_system = _source_run(runs, system), _run_system(system)
        expected_rate = run["systems"][run_system]["E1"]["rate"]
        expected_fpr = run["systems"][run_system]["benign"]["fpr"]
        expected_p95 = run["systems"][run_system]["E1"]["latency"]["p95_ms"]
        check("%s: 雷达 %s 拦截率/1-FPR/速度原值" % (label, system),
              axes["intercept_rate"]["raw"][system] == expected_rate
              and abs(axes["one_minus_fpr"]["raw"][system] - (1 - expected_fpr)) < 1e-12
              and abs(axes["speed"]["raw"][system] - 1.0 / expected_p95) < 1e-12)
    check("%s: 雷达覆盖意图数 A/B=0, C-*=intent_count" % label,
          axes["intent_coverage"]["raw"]["A"] == 0
          and axes["intent_coverage"]["raw"]["B"] == 0
          and axes["intent_coverage"]["raw"]["C-offline"]
          == runs["C-offline"]["encoder"]["intent_count"]
          and axes["intent_coverage"]["raw"]["C-neural"]
          == runs["C-neural"]["encoder"]["intent_count"])
    check("%s: 可解释性序数 1/1/3/3" % label,
          [axes["explainability"]["raw"][s]
           for s in ("A", "B", "C-offline", "C-neural")] == [1.0, 1.0, 3.0, 3.0])
    norm_ok = True
    for axis in radar["axes"]:
        for system in radar["series"]:
            raw = axis["raw"][system]
            expected = (min(1.0, max(0.0, raw)) if axis["scale"] == "native"
                        else raw / axis["raw_max"])
            if abs(axis["normalized"][system] - expected) > 1e-12:
                norm_ok = False
    check("%s: 雷达归一化自洽" % label, norm_ok)
    for block_name in ("comparison", "ablation", "radar"):
        check("%s: %s caption_zh / caption_en 非空" % (label, block_name),
              bool(figure_data[block_name].get("caption_zh"))
              and bool(figure_data[block_name].get("caption_en")))
    comp_en = figure_data["comparison"]["caption_en"]
    check("%s: caption 写明 MiniLM / ONNX int8 / 主口径 intent-concept" % label,
          "MiniLM" in comp_en and "ONNX" in comp_en and "int8" in comp_en
          and "intent-concept" in comp_en)
    abl_en = figure_data["ablation"]["caption_en"]
    check("%s: ablation caption 写明负结果" % label,
          "negative result" in abl_en.lower()
          or "not a stronger defence" in abl_en.lower())
    limitations = " ".join(figure_data["limitations"]).lower()
    check("%s: limitations 覆盖 ONNX int8 / model sha / 神经推理 / 非 OOD" % label,
          "onnx int8" in limitations and "model sha256" in limitations
          and "hybrid variant contains neural inference" in limitations
          and "not out-of-distribution" in limitations)


# ── 测试用例 ──────────────────────────────────────────────────────────────
def test_missing_and_inconsistent() -> None:
    work = _reset_dir(os.path.join(TMP_ROOT, "invalid"))
    base = write_fixture(os.path.join(work, "base"))

    def expect_exit2(name: str, paths: dict, needle: str) -> None:
        code, _out, err = run_cli(paths, os.path.join(work, "out_" + name[:24]))
        check("%s -> exit 2 且点名" % name, code == 2 and needle in err,
              "code=%r stderr=%r" % (code, err.strip()))

    cases = [
        ("official", "systems.C.E1.rate", "systems.C.E1.rate"),
        ("neural", "systems.C.E0.latency.p95_ms", "systems.C.E0.latency.p95_ms"),
        ("hybrid", "systems.B.benign.fpr", "systems.B.benign.fpr"),
        ("official", "encoder.threshold", "encoder.threshold"),
        ("neural", "encoder.model_sha256", "encoder.model_sha256"),
        ("official", "run_id", "run_id"),
        ("ablation", "sets.E1.both", "sets.E1.both"),
    ]
    for index, (target, dotted, needle) in enumerate(cases):
        data = _fixture_data()
        drop_path(data[target], dotted)
        paths = _write_fixture_data(work, "miss%d" % index, data)
        expect_exit2("缺字段 %s:%s" % (target, dotted), paths, needle)

    data = _fixture_data()
    data["ablation"]["sets"]["E1"]["neither"] = 34
    expect_exit2("消融 2x2 之和不等于 n",
                 _write_fixture_data(work, "bad_abl", data), "inconsistent ablation")

    data = _fixture_data()
    data["neural"]["eval_sets"]["E0"]["sha256"] = "0" * 64
    expect_exit2("三份 run 的 E0 sha256 不一致",
                 _write_fixture_data(work, "bad_eval", data), "inconsistent eval set")

    data = _fixture_data()
    data["hybrid"]["systems"]["C"]["E1"]["intercepted"] = 30
    data["hybrid"]["systems"]["C"]["E1"]["rate"] = 0.75
    data["hybrid"]["delta"]["C_minus_B_E1"] = -0.025
    expect_exit2("hybrid 与 offline 拦截率不一致",
                 _write_fixture_data(work, "bad_hybrid", data), "C-hybrid != C-offline")

    data = _fixture_data()
    data["official"]["systems"]["C"]["E1"]["rate"] = 0.9
    expect_exit2("rate != intercepted/n",
                 _write_fixture_data(work, "bad_rate", data), "inconsistent run")

    data = _fixture_data()
    data["ablation"]["variants"]["neural"]["E1_rate"] = 0.8
    expect_exit2("消融 variants 与 run 不一致",
                 _write_fixture_data(work, "bad_var", data), "variants.neural.E1_rate")

    code, _out, err = run_cli(
        {"official": os.path.join(work, "nope.json"), "neural": base["neural"],
         "hybrid": base["hybrid"], "ablation": base["ablation"]},
        os.path.join(work, "out_nope"))
    check("run JSON 不存在 -> exit 2", code == 2 and "not found" in err,
          "code=%r stderr=%r" % (code, err.strip()))


def test_render_fixture() -> dict:
    work = _reset_dir(os.path.join(TMP_ROOT, "fixture"))
    paths = write_fixture(work)
    out_dir = os.path.join(work, "figures")
    code, out, err = run_cli(paths, out_dir)
    check("fixture 三图渲染 -> exit 0", code == 0, "stderr=%r" % err.strip())
    for name in (plot.CMP_PDF, plot.CMP_PNG, plot.ABL_PDF, plot.ABL_PNG,
                 plot.RADAR_PDF, plot.RADAR_PNG, plot.DATA_JSON):
        check("生成 %s" % name, os.path.isfile(os.path.join(out_dir, name)))
    check("CLI 打印三变体与消融增量（可人工核对）",
          "C-offline" in out and "C-neural" in out and "ablation E1" in out)
    figure_data = json.load(io.open(os.path.join(out_dir, plot.DATA_JSON),
                                    encoding="utf-8"))
    compare_figure_data(figure_data, FIXTURE_RUNS, _ablation(), "fixture")
    return {"work": work, "paths": paths, "out_dir": out_dir,
            "figure_data": figure_data}


def test_pdf_structure(context: dict) -> None:
    out_dir = context["out_dir"]
    cases = ((plot.CMP_PDF, (504.0, 484.0)),
             (plot.ABL_PDF, (504.0, 340.0)),
             (plot.RADAR_PDF, (504.0, 470.0)))
    for name, expected_box in cases:
        info = parse_pdf(os.path.join(out_dir, name))
        check("%s: 结构无问题" % name, not info["problems"],
              "; ".join(info["problems"][:4]))
        check("%s: MediaBox == %s" % (name, expected_box),
              info["mediabox"] is not None
              and abs(info["mediabox"][2] - expected_box[0]) < 1e-9
              and abs(info["mediabox"][3] - expected_box[1]) < 1e-9,
              repr(info["mediabox"]))
        check("%s: 文本对象 >= 30" % name, len(info["texts"]) >= 30,
              "texts=%d" % len(info["texts"]))
        verifier = os.path.join(ROOT, "paper", "figures", "verify_pdf.py")
        if os.path.isfile(verifier):
            sys.path.insert(0, os.path.dirname(verifier))
            try:
                import verify_pdf  # type: ignore
                verified = verify_pdf.parse_pdf(os.path.join(out_dir, name))
                verify_pdf.check_text(verified)
                check("%s: 仓库 verify_pdf.py 复检 PASS" % name,
                      not verified["problems"],
                      "; ".join(verified["problems"][:4]))
            finally:
                sys.path.pop(0)

    comparison = parse_pdf(os.path.join(out_dir, plot.CMP_PDF))
    strings = " ".join(comparison["strings"])
    check("comparison: 五系统 + 三后端标签齐全",
          all(token in strings for token in
              ("Five Systems", "C-off", "C-neu", "C-hyb", "intent-concept",
               "MiniLM-L12 ONNX int8")))
    check("comparison: M 子集 / FPR / 100 ms / H0 目标 / 应拦口径齐全",
          "M subset" in strings and "benign FPR" in strings
          and "100 ms budget" in strings and "H0 target: > 0.90 on E1" in strings
          and "E1 excl. 2 benign-creation items (n=38)" in strings)
    check("comparison: 图内写明 C-B 三变体增量",
          "off +0.075/+0.100" in strings and "neu +0.000/+0.025" in strings
          and "hyb +0.075/+0.100" in strings)
    check("comparison: 脚注含 MiniLM sha 与双阈值",
          "MiniLM-L12 ONNX int8 sha" in strings
          and "thr 0.45/0.69 (D_dev)" in strings)
    limit = FIXTURE_RUNS["C-offline"]["latency_budget"]["limit_ms"]
    max_p95 = max(run["systems"]["C"][e]["latency"]["p95_ms"]
                  for run in FIXTURE_RUNS.values() for e in ("E1", "E0"))
    ymax = max(limit, max_p95) * 1.25
    y_line = plot.CMP_LAT["bottom"] - limit / ymax * (
        plot.CMP_LAT["bottom"] - plot.CMP_LAT["top"])
    content = comparison["content"].decode("latin-1")
    check("comparison: 100 ms 红线是红色虚线且 y 坐标正确",
          "0.85 0.1 0.1 RG" in content and "[4 2.5] 0 d" in content
          and ("%s %s m" % (plot._pdf_num(plot.CMP_LAT["left"]),
                            plot._pdf_num(plot.CMP_H - y_line))) in content)

    ablation_pdf = parse_pdf(os.path.join(out_dir, plot.ABL_PDF))
    abl_strings = " ".join(ablation_pdf["strings"])
    check("ablation: 2x2 四格标签与增量注记齐全",
          all(token in abl_strings for token in
              ("both", "offline only", "neural only", "neither",
               "neural increment over concept", "2x2 contingency")))
    check("ablation: 四格计数来自消融 JSON",
          all(str(value) in abl_strings for value in (0, 6, 1, 33, 10, 29)))
    check("ablation: 写明系统 C 增量与负结果结论",
          "C-off +3/+4" in abl_strings and "C-neu +0/+1" in abl_strings
          and "neural adds nothing beyond concept" in abl_strings)
    check("ablation: 写神经模型与阈值来源",
          "paraphrase-multilingual-MiniLM-L12-v2" in abl_strings
          and "ONNX int8" in abl_strings and "concept 0.45 / neural 0.69" in abl_strings)

    radar_pdf = parse_pdf(os.path.join(out_dir, plot.RADAR_PDF))
    radar_strings = " ".join(radar_pdf["strings"])
    check("radar: 五轴 + 四序列图例 + 未画 C-hybrid 说明",
          all(token in radar_strings for token in
              ("intercept rate (E1)", "1 - FPR", "speed 1/p95", "intent coverage",
               "explainability", "C-off", "C-neu", "C-hybrid not plotted")))
    check("PDF 中没有禁用词",
          not any(bad in radar_strings.lower() for bad in FORBIDDEN_FIGURE_TERMS)
          and not any(bad in strings.lower() for bad in FORBIDDEN_FIGURE_TERMS)
          and not any(bad in abl_strings.lower() for bad in FORBIDDEN_FIGURE_TERMS))


def test_png(context: dict) -> None:
    out_dir = context["out_dir"]
    for name, expected_pt in ((plot.CMP_PNG, (504.0, 484.0)),
                              (plot.ABL_PNG, (504.0, 340.0)),
                              (plot.RADAR_PNG, (504.0, 470.0))):
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
        check("%s: 非空白（绘制像素 > 1%%）" % name, ratio > 0.01,
              "ratio=%.4f" % ratio)


def test_determinism(context: dict) -> None:
    paths = context["paths"]
    work = context["work"]
    dir_a = os.path.join(work, "det_a")
    dir_b = os.path.join(work, "det_b")
    code_a, _out, _err = run_cli(paths, dir_a)
    code_b, _out, _err = run_cli(paths, dir_b)
    check("确定性：两次渲染都成功", code_a == 0 and code_b == 0)
    for name in (plot.CMP_PDF, plot.ABL_PDF, plot.RADAR_PDF):
        with open(os.path.join(dir_a, name), "rb") as fh_a, \
                open(os.path.join(dir_b, name), "rb") as fh_b:
            check("确定性：%s 字节完全一致" % name, fh_a.read() == fh_b.read())
    data_a = json.load(io.open(os.path.join(dir_a, plot.DATA_JSON), encoding="utf-8"))
    data_b = json.load(io.open(os.path.join(dir_b, plot.DATA_JSON), encoding="utf-8"))
    for payload in (data_a, data_b):
        payload.pop("generated_at", None)
        for figure in payload["figures"].values():
            figure.pop("pdf", None)
            figure.pop("png", None)
    check("确定性：figure_data 除 generated_at / 输出路径外一致", data_a == data_b)
    low_dir = os.path.join(work, "dpi150")
    code, _out, _err = run_cli(paths, low_dir, ["--dpi", "150"])
    info = png_info(os.path.join(low_dir, plot.CMP_PNG))
    check("--dpi 150 -> PNG 尺寸按比例减半",
          code == 0 and info["size"] == (1050, 1008), repr(info["size"]))


def test_real_data() -> None:
    if not all(os.path.isfile(p) for p in (REAL_RUN, REAL_NEURAL, REAL_HYBRID,
                                           REAL_ABL)):
        print("[SKIP] 三份官方 run / 消融 JSON 不齐，跳过真实数据核对")
        return
    work = _reset_dir(os.path.join(TMP_ROOT, "real"))
    runs = {"C-offline": json.load(io.open(REAL_RUN, encoding="utf-8")),
            "C-neural": json.load(io.open(REAL_NEURAL, encoding="utf-8")),
            "C-hybrid": json.load(io.open(REAL_HYBRID, encoding="utf-8"))}
    abl = json.load(io.open(REAL_ABL, encoding="utf-8"))
    out_dir = os.path.join(work, "figures")
    code, _out, err = run_cli({"official": REAL_RUN, "neural": REAL_NEURAL,
                               "hybrid": REAL_HYBRID, "ablation": REAL_ABL}, out_dir)
    check("真实三份 JSON + 消融 JSON -> exit 0", code == 0,
          "stderr=%r" % err.strip())
    if code != 0:
        return
    fresh = json.load(io.open(os.path.join(out_dir, plot.DATA_JSON),
                              encoding="utf-8"))
    compare_figure_data(fresh, runs, abl, "real")
    check("真实数据：figure_data 记录 official run_id == JSON",
          fresh["run"]["run_id"] == runs["C-offline"]["run_id"])
    committed_path = os.path.join(REAL_FIG_DIR, plot.DATA_JSON)
    if os.path.isfile(committed_path):
        committed = json.load(io.open(committed_path, encoding="utf-8"))
        check("已提交 figure_data 不过期（official run_id 一致）",
              committed["run"]["run_id"] == runs["C-offline"]["run_id"],
              "%r vs %r" % (committed["run"]["run_id"],
                            runs["C-offline"]["run_id"]))
        normalized_committed = copy.deepcopy(committed)
        normalized_fresh = copy.deepcopy(fresh)
        for payload in (normalized_committed, normalized_fresh):
            payload.pop("generated_at", None)
            for figure in payload["figures"].values():
                figure.pop("pdf", None)
                figure.pop("png", None)
        check("已提交 figure_data 的全部数值 == 真实 JSON 重出的数值",
              normalized_committed == normalized_fresh)
        expected = {
            plot.CMP_PDF: [plot._fmt_rate(runs["C-offline"]["systems"]["C"]["E1"]["rate"]),
                           plot._fmt_rate(runs["C-neural"]["systems"]["C"]["E1"]["rate"]),
                           plot._fmt_ms(runs["C-neural"]["systems"]["C"]["E1"]
                                        ["latency"]["p95_ms"])],
            plot.ABL_PDF: [str(abl["sets"]["E1"]["neither"]),
                           "neural increment over concept"],
            plot.RADAR_PDF: [plot._fmt_rate(runs["C-neural"]["systems"]["C"]["E1"]["rate"])],
        }
        for name, tokens in expected.items():
            path = os.path.join(REAL_FIG_DIR, name)
            if not os.path.isfile(path):
                check("已提交 %s 存在" % name, False)
                continue
            info = parse_pdf(path)
            strings = " ".join(info["strings"])
            check("已提交 %s: 结构 OK 且打印真实数值" % name,
                  not info["problems"]
                  and all(token in strings for token in tokens),
                  "; ".join(info["problems"][:3]))
    else:
        print("[SKIP] experiments/phase-c/figures/figure_data.json 尚未提交")
    paper_dir = os.path.join(ROOT, "paper", "figures")
    for name in (plot.CMP_PDF, plot.ABL_PDF, plot.RADAR_PDF):
        source = os.path.join(REAL_FIG_DIR, name)
        copy_path = os.path.join(paper_dir, name)
        if os.path.isfile(source):
            check("paper/figures/%s 存在且与 experiments 副本一致" % name,
                  os.path.isfile(copy_path)
                  and open(source, "rb").read() == open(copy_path, "rb").read())


# ── 论文章节解析 ───────────────────────────────────────────────────────────
def _tex_lines(path: str) -> list[str]:
    with io.open(path, encoding="utf-8") as fh:
        return fh.read().splitlines()


def tex_main_rows(path: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for raw_line in _tex_lines(path):
        line = raw_line.split("%", 1)[0].strip()
        match = re.match(r"^(A|B|C-(?:offline|neural|hybrid))\s*[（(]", line)
        if not match:
            continue
        cells = [cell.strip().rstrip("\\").strip() for cell in line.split("&")]
        if len(cells) < 9:
            continue
        rows[match.group(1)] = cells[1:9]
    return rows


def tex_encoder_rows(path: str) -> dict[str, list[str]]:
    rows: dict[str, list[str]] = {}
    for raw_line in _tex_lines(path):
        line = raw_line.split("%", 1)[0].strip()
        match = re.match(r"^(C-(?:offline|neural|hybrid))\s*&", line)
        if not match:
            continue
        cells = [cell.strip().rstrip("\\").strip() for cell in line.split("&")]
        rows[match.group(1)] = cells[1:]
    return rows


def tex_adjudicated_rows(path: str) -> dict[tuple[str, str], list[str]]:
    rows: dict[tuple[str, str], list[str]] = {}
    for raw_line in _tex_lines(path):
        line = raw_line.split("%", 1)[0].strip()
        match = re.match(r"^(E[01])-(M|R|B)\b", line)
        if not match:
            continue
        cells = [cell.strip().rstrip("\\").strip() for cell in line.split("&")]
        if len(cells) < 6:
            continue
        rows[(match.group(1), match.group(2))] = cells[1:6]
    return rows


def _cell_float(cell: str) -> float:
    return float(re.sub(r"[^0-9.\-]", "", cell))


def _cell_fraction(cell: str):
    match = re.match(r"^\s*(\d+)\s*/\s*(\d+)\s*$", cell)
    return (int(match.group(1)), int(match.group(2))) if match else None


def check_tex_numbers(path: str, runs: dict, abl: dict, label: str) -> None:
    rows = tex_main_rows(path)
    check("%s: 主表含 A/B/C-offline/C-neural/C-hybrid 五行" % label,
          set(rows) == set(plot.SYSTEMS), repr(sorted(rows)))
    for system in plot.SYSTEMS:
        cells = rows.get(system)
        if cells is None:
            check("%s: S_%s 主表行存在" % (label, system), False)
            continue
        run, run_system = _source_run(runs, system), _run_system(system)
        expected = run["systems"][run_system]
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
              and fp_frac == (expected["benign"]["fp_count"],
                              expected["benign"]["n"]))
        check("%s: S_%s 主表数值与 run JSON 逐点一致" % (label, system), ok,
              "tex=%r json=%r" % (cells, expected["E1"]["rate"]))
    enc_rows = tex_encoder_rows(path)
    check("%s: 编码器变体表含三行" % label,
          set(enc_rows) == set(plot.C_VARIANTS), repr(sorted(enc_rows)))
    for variant, cells in enc_rows.items():
        expected = runs[variant]["encoder"]
        backend_cell = cells[0].replace("\\_", "_")
        ok = (backend_cell == expected["backend_effective"]
              and cells[1].lower() in ("true", "false")
              and (cells[1].lower() == "true") == expected["is_neural"]
              and abs(_cell_float(cells[3]) - expected["threshold"]) <= 1e-9)
        check("%s: 编码器表 %s 的后端/is_neural/阈值 == JSON" % (label, variant),
              ok, "tex=%r json=%r" % (cells, expected))
    adj_rows = tex_adjudicated_rows(path)
    expected_subsets = set()
    for variant in plot.SYSTEMS:
        run, run_system = _source_run(runs, variant), _run_system(variant)
        for eval_set in ("E1", "E0"):
            for subset in run["systems"][run_system][eval_set]["adjudicated"]:
                expected_subsets.add((eval_set, subset))
    check("%s: 判定口径表含全部 E1/E0 x M/R/B 行（%d 行）"
          % (label, len(expected_subsets)), set(adj_rows) == expected_subsets,
          "tex=%r expected=%r" % (sorted(adj_rows), sorted(expected_subsets)))
    for (eval_set, subset), cells in adj_rows.items():
        ok = True
        detail = []
        for index, variant in enumerate(plot.SYSTEMS):
            run, run_system = _source_run(runs, variant), _run_system(variant)
            expected = run["systems"][run_system][eval_set]["adjudicated"][subset]
            fraction = _cell_fraction(cells[index])
            if fraction != (expected["intercepted"], expected["n"]):
                ok = False
                detail.append("%s got=%r expected=%r"
                              % (variant, fraction,
                                 (expected["intercepted"], expected["n"])))
        check("%s: 判定口径 %s-%s 的五个系统与 JSON 一致"
              % (label, eval_set, subset), ok, "; ".join(detail[:2]))


def normalized_tex(path: str) -> str:
    text = io.open(path, encoding="utf-8").read()
    text = re.sub(r"(?m)(?<!\\)%.*$", "", text)
    text = re.sub(r"\\allowbreak\s*", "", text)
    return re.sub(r"\s+", " ", text)


def tex_labels(path: str) -> set[str]:
    return set(re.findall(r"\\label\{([^}]+)\}",
                          io.open(path, encoding="utf-8").read()))


def check_latex_static(path: str, label: str,
                       known_labels: set[str] | None = None) -> None:
    text = io.open(path, encoding="utf-8").read()
    stripped = re.sub(r"(?m)(?<!\\)%.*$", "", text)
    brace_text = stripped.replace("\\{", "").replace("\\}", "")
    check("%s: 花括号平衡" % label,
          brace_text.count("{") == brace_text.count("}"),
          "{=%d }=%d" % (brace_text.count("{"), brace_text.count("}")))
    stack, problems = [], []
    for kind, env in re.findall(r"\\(begin|end)\{([^}]*)\}", stripped):
        if kind == "begin":
            stack.append(env)
        elif not stack or stack.pop() != env:
            problems.append(env)
    check("%s: begin/end 环境配对" % label, not problems and not stack,
          "problems=%r stack=%r" % (problems, stack))
    labels_here = tex_labels(path)
    refs = set(re.findall(r"\\ref\{([^}]+)\}", stripped))
    allowed = labels_here | (known_labels or set())
    check("%s: 所有 \\ref 都有 \\label 来源" % label, refs <= allowed,
          "undefined=%r" % sorted(refs - allowed))
    for bad in ("TBD", "PLACEHOLDER", "\\TODO", "[A2-PENDING]"):
        check("%s: 没有残留占位符 %r" % (label, bad), bad not in stripped)
    graphics = re.findall(r"\\includegraphics\[[^\]]*\]\{([^}]+)\}", stripped)
    check("%s: 引用三张图的 PDF 且 paper/figures 副本存在" % label,
          len(graphics) == 3
          and all(os.path.isfile(os.path.join(ROOT, "paper", figure))
                  for figure in graphics),
          repr(graphics))


def test_paper_sections() -> None:
    if not os.path.isfile(EN_TEX) and not os.path.isfile(ZH_TEX):
        print("[SKIP] 06h-phase-c.tex 尚未写入")
        return
    runs = {"C-offline": json.load(io.open(REAL_RUN, encoding="utf-8")),
            "C-neural": json.load(io.open(REAL_NEURAL, encoding="utf-8")),
            "C-hybrid": json.load(io.open(REAL_HYBRID, encoding="utf-8"))}
    abl = json.load(io.open(REAL_ABL, encoding="utf-8"))
    for path, label in ((EN_TEX, "en"), (ZH_TEX, "zh")):
        if not os.path.isfile(path):
            check("%s 章节存在" % label, False, path)
            continue
        text = io.open(path, encoding="utf-8").read()
        comment_header = "\n".join(
            line for line in text.splitlines() if line.lstrip().startswith("%"))
        check("%s: 顶部注释写明'待 H0 决定'" % label,
              "H0" in comment_header
              and ("待 H0" in comment_header or "H0 decision" in comment_header))
        check("%s: 顶部注释给出 \\input 接入说明" % label,
              "\\input{sections" in comment_header or "\\input{sections" in text)
        check("%s: H0 指定英文句子原样出现" % label,
              H0_SENTENCE in normalized_tex(path))
        check("%s: 句子按 hedge 版处理（部分支持/未完全支持）" % label,
              any(token in normalized_tex(path).lower()
                  for token in ("partial", "not supported", "部分支持",
                                "未被本实验完全支持")))
        check("%s: 写明双后端（concept 主口径 + neural 消融）" % label,
              "offline_concept" in text and "MiniLM" in text)
        low_text = text.lower()
        check("%s: 写明 ONNX int8 / 本地 CPU / 零 API" % label,
              "ONNX" in text and "int8" in text
              and ("zero external api" in low_text or "zero api" in low_text
                   or "零外部 api" in low_text or "零 api" in low_text))
        check("%s: 写明 D_dev 召回 28.9%% vs 68.2%%（神经弱于概念）" % label,
              ("28.9" in text or "0.289" in text)
              and ("68.2" in text or "0.682" in text))
        low_text = text.lower()
        check("%s: 写明 neural increment over concept = 0" % label,
              "increment over concept" in low_text
              or "increment over the concept" in low_text or "增量" in text)
        check("%s: 表内有一行明确的 neural increment = 0（E1/E0）" % label,
              "0 (E1) / 0 (E0)" in text or "0（E1）/ 0（E0）" in text)
        check("%s: 写明负结果（更强编码器 ≠ 更强防御）" % label,
              ("negative result" in text.lower() or "负结果" in text)
              and ("stronger encoder is not a stronger" in text.lower()
                   or "更强的编码器" in text or "更强编码器" in text))
        check("%s: 写明 E1 未达 >0.90 目标" % label,
              ("0.850" in text or "0.85" in text) and "0.90" in text
              and ("target" in text.lower() or "目标" in text))
        check("%s: 写明数据来源 run_id / 三份 JSON" % label,
              "run-semantic-20261028" in text
              and "run-semantic-neural.json" in text
              and "neural_vs_concept_ablation.json" in text)
        check("%s: 写明 E1/E0 是 held-out 变体而非 OOD" % label,
              "held-out" in text.lower()
              and ("OOD" in text or "out-of-distribution" in text.lower()
                   or "分布外" in text))
        known = tex_labels(EN_TEX) if os.path.isfile(EN_TEX) else set()
        check_latex_static(path, label, known)
        check_tex_numbers(path, runs, abl, label)


def main() -> int:
    os.makedirs(os.path.join(ROOT, ".tmp"), exist_ok=True)
    print("=" * 80)
    print("tests/test_plot_phase_c.py  |  %s" % sys.executable)
    print("=" * 80)
    test_missing_and_inconsistent()
    context = test_render_fixture()
    test_pdf_structure(context)
    test_png(context)
    test_determinism(context)
    test_real_data()
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
