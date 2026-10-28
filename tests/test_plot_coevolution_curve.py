# -*- coding: utf-8 -*-
"""A2 自测 · 论文核心图 `tools/plot_coevolution_curve.py`（自跑脚本，无 pytest 依赖）

    python tests/test_plot_coevolution_curve.py

覆盖（契约 `experiments/phase-b/CONTRACT.md` §7）：
- `load_log`  ：目录/裸 jsonl 两种入口、内部 run_id、缺路径/空日志报错；
- `build_series`：字段逐点对齐、缺字段/非数值/越界/重复代数报错、单代边界、
                  多 run 取最新、明文参照 evidence 优先 / 缺失时用唯一仪器现算、
                  良性误伤率缺失时用 count/total 精确导出并留痕；
- `build_ops` ：四条线 + 每条线数据点数 = 代数数、灰线是虚线、标题/图例/脚注齐全、
               脚注以 `source:` 开头、`--inset` 时橙线移入小图；
- `render_pdf`：%PDF-1.4 头、对象表/xref/trailer、startxref 偏移可解析、
               每个 xref 偏移指向 `N 0 obj`、stream 长度 = /Length、单页 MediaBox、
               文本全 ASCII 且 >=8 pt（脚注 6 pt）、四种颜色算子真实存在、
               仓库内独立 PDF 校验器（build/**/verify_pdf.py，若存在）PASS；
- `render_png`：PNG 签名、尺寸 = pt*dpi/72、dpi 元数据 300、红/蓝/橙/灰像素真实存在；
- `write_figure_data`：run_id/代数/patch 数/生成时间/caption/来源字段名齐全，
               且每条线数值与日志**逐点一致**（测试侧独立读 jsonl 比对）；
- CLI        ：正常 exit 0 并产出三件套；缺 run / 缺字段 exit 2 且点名缺的字段
               （进程内返回码 + 真实子进程退出码各验一次）。

原则：不复制绘图实现；只调用被测函数/模块文件；断言在实现退化时必须变红。
"""
from __future__ import annotations

import contextlib
import io
import json
import os
import re
import subprocess
import sys
from datetime import datetime

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import tools.plot_coevolution_curve as plot_mod  # noqa: E402

try:  # Windows 控制台是 GBK；测试里有点名/中文 detail，先切 utf-8
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
except (AttributeError, ValueError):  # pragma: no cover
    pass

PASS: list[str] = []
FAIL: list[str] = []
PY = sys.executable
WORK = os.path.join(ROOT, ".tmp", "a2_figure_test")

LOG_FIELDS = ("intercept_rate_baseline_zeroday",
              "intercept_rate_evolved_zeroday",
              "benign_false_positive_rate")

RED, BLUE, ORANGE, GRAY = plot_mod.RED, plot_mod.BLUE, plot_mod.ORANGE, plot_mod.GRAY


# ── 小工具 ──────────────────────────────────────────────────────────────────
def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    suffix = ("  — " + str(detail)) if detail else ""
    print(("  ok   " if cond else "  FAIL ") + name + suffix)


def _raises(fn) -> BaseException | None:
    try:
        fn()
    except BaseException as exc:  # SystemExit 是 BaseException，不是 Exception
        return exc
    return None


def _almost(a: float, b: float, tol: float = 1e-9) -> bool:
    return abs(float(a) - float(b)) <= tol


def _read_log_rows(path: str) -> dict:
    """测试侧独立读 jsonl（不用被测 load_log，避免自证）。"""
    rows = {}
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            rows[rec["generation_id"]] = rec
    return rows


def _write_json(path: str, obj) -> None:
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(obj, ensure_ascii=False, indent=2))


def _make_run(run_id: str, rows: list[dict], *, evidence: dict | None = None) -> str:
    run_dir = os.path.join(WORK, run_id)
    os.makedirs(run_dir, exist_ok=True)
    with io.open(os.path.join(run_dir, "coevolution_log.jsonl"), "w",
                 encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    if evidence is not None:
        _write_json(os.path.join(run_dir, "coevolution_evidence.json"), evidence)
    return run_dir


def _rows() -> list[dict]:
    """3 代 fixture：held-out 拦截率 0.0541 保持低位；演化防线 0.054→0.595→0.892 回升。"""
    return [
        {"generation_id": 0, "red_team_variants_count": 40, "bypass_success_count": 35,
         "blue_team_patch_generated": 0, "regression_test_pass": None,
         "benign_false_positive_rate": 0.027, "attacks_intercepted_count": 5,
         "intercept_rate_evolved": 0.125, "intercept_rate_baseline_zeroday": 0.0541,
         "intercept_rate_evolved_zeroday": 0.0541, "benign_total": 74,
         "benign_false_positive_count": 2, "patches_total": 0,
         "patch_ids_accepted": [], "patch_rejected": [], "regression": None,
         "stop_reason": None, "api_calls": 0, "api_tokens_used": 0,
         "ts": "2026-10-28T19:00:00+08:00"},
        {"generation_id": 1, "red_team_variants_count": 40, "bypass_success_count": 8,
         "blue_team_patch_generated": 1, "regression_test_pass": True,
         "benign_false_positive_rate": 0.0, "attacks_intercepted_count": 32,
         "intercept_rate_evolved": 0.8, "intercept_rate_baseline_zeroday": 0.0541,
         "intercept_rate_evolved_zeroday": 0.5946, "benign_total": 74,
         "benign_false_positive_count": 0, "patches_total": 1,
         "patch_ids_accepted": ["P-0001"], "patch_rejected": [], "regression": None,
         "stop_reason": None, "api_calls": 0, "api_tokens_used": 0,
         "ts": "2026-10-28T19:05:00+08:00"},
        {"generation_id": 2, "red_team_variants_count": 40, "bypass_success_count": 0,
         "blue_team_patch_generated": 1, "regression_test_pass": True,
         "benign_false_positive_rate": 0.0, "attacks_intercepted_count": 40,
         "intercept_rate_evolved": 1.0, "intercept_rate_baseline_zeroday": 0.0541,
         "intercept_rate_evolved_zeroday": 0.8919, "benign_total": 74,
         "benign_false_positive_count": 0, "patches_total": 2,
         "patch_ids_accepted": ["P-0001", "P-0002"], "patch_rejected": [],
         "regression": None, "stop_reason": "converged", "api_calls": 0,
         "api_tokens_used": 0, "ts": "2026-10-28T19:10:00+08:00"},
    ]


def _plateau_rows() -> list[dict]:
    """6 代 fixture：held-out 拦截率 0.40→0.70→0.90 后平台到第 5 代，补丁仍在增长。

    用来验证"平台化是数据现算的事实，不是绘图平滑"（官方 run 的形态）。
    """
    values = [0.40, 0.70, 0.90, 0.90, 0.90, 0.90]
    rows = []
    for gen, value in enumerate(values):
        row = dict(_rows()[0])
        row.update({"generation_id": gen, "patches_total": gen + 1,
                    "intercept_rate_evolved_zeroday": value,
                    "intercept_rate_baseline_zeroday": 0.30,
                    "benign_false_positive_rate": 0.0,
                    "benign_false_positive_count": 0})
        rows.append(row)
    return rows


def _evidence(run_id: str, plain_rate: float | None = 0.9505) -> dict:
    ev = {"schema": "phase-b-coevolution-evidence-v1", "run_id": run_id,
          "started_at": "2026-10-28T19:00:00+08:00",
          "finished_at": "2026-10-28T19:12:00+08:00",
          "generations_run": 3, "stop_reason": "converged", "converged": True,
          "zeroday_baseline_intercept_rate": 0.0541,
          "zeroday_evolved_intercept_rate_final": 0.8919,
          "zeroday_protocol": "held-out seeds x generation-0 mutation distribution",
          "zeroday_generation_id": 0,
          "zeroday_set": {"count": 40},
          "benign_total": 74, "api_calls": 0, "api_tokens_used": 0,
          "limitations": ["fixture only"]}
    if plain_rate is not None:
        ev["plain_attack_baseline_intercept_rate"] = plain_rate
    return ev


# ── 独立 PDF 解析器（不 import 被测实现）────────────────────────────────────
def _parse_pdf(path: str) -> dict:
    data = open(path, "rb").read()
    info: dict = {"size": len(data), "problems": []}
    info["header_ok"] = data.startswith(b"%PDF-1.4")
    info["eof_ok"] = data.rstrip().endswith(b"%%EOF")
    info["has_objects"] = b" 0 obj" in data
    info["has_xref"] = b"\nxref\n" in data
    info["has_trailer"] = b"trailer" in data
    info["has_startxref"] = b"startxref" in data

    m = re.search(rb"startxref\s+(\d+)\s+%%EOF\s*$", data)
    info["startxref"] = int(m.group(1)) if m else None
    info["startxref_ok"] = bool(m) and data[info["startxref"]:
                                            info["startxref"] + 4] == b"xref"
    if not m:
        info["problems"].append("no startxref/%%EOF")
        return info

    xref_at = info["startxref"]
    tail = data[xref_at:]
    table = tail.split(b"trailer")[0].decode("latin-1")
    trailer = tail.split(b"trailer")[1].decode("latin-1") if b"trailer" in tail else ""
    entries = re.findall(r"(\d{10}) (\d{5}) ([nf])", table)
    info["xref_entries"] = len(entries)
    info["trailer_ok"] = "/Root" in trailer and "/Size" in trailer
    msize = re.search(r"/Size\s+(\d+)", trailer)
    info["size_ok"] = bool(msize) and int(msize.group(1)) == len(entries)

    offsets_ok = True
    for idx, (off, gen, kind) in enumerate(entries):
        if kind != "n":
            continue
        off = int(off)
        expect = b"%d 0 obj" % idx
        if data[off:off + len(expect)] != expect:
            offsets_ok = False
    info["offsets_ok"] = offsets_ok

    objs: dict[int, bytes] = {}
    for mm in re.finditer(rb"(\d+) 0 obj\n(.*?)\nendobj", data, re.S):
        objs[int(mm.group(1))] = mm.group(2)
    info["n_objects"] = len(objs)
    info["pages"] = sum(1 for body in objs.values()
                        if b"/Type /Page " in body or b"/Type /Page>" in body)
    info["catalog"] = sum(1 for body in objs.values() if b"/Type /Catalog" in body)

    info["mediabox"] = None
    for body in objs.values():
        mm = re.search(rb"/MediaBox\s*\[\s*([\d.]+)\s+([\d.]+)\s+([\d.]+)\s+([\d.]+)\s*\]",
                       body)
        if mm:
            info["mediabox"] = tuple(float(x) for x in mm.groups())
            break
    info["fonts"] = {}
    for num, body in objs.items():
        fm = re.search(rb"/BaseFont\s*/([A-Za-z0-9+\-]+)", body)
        if fm and b"/Type /Font" in body:
            info["fonts"][num] = fm.group(1).decode()

    content_body = None
    for body in objs.values():
        if b"/Length" in body and b"stream" in body:
            content_body = body
            break
    info["length_ok"] = False
    info["length_detail"] = "no content stream"
    content = b""
    if content_body is not None:
        ml = re.search(rb"/Length\s+(\d+)", content_body)
        start = content_body.find(b"stream") + len(b"stream")
        if content_body[start:start + 1] == b"\n":
            start += 1
        end = content_body.rfind(b"endstream")
        content = content_body[start:end]
        actual = len(content)
        declared = int(ml.group(1)) if ml else -1
        info["length_ok"] = declared == actual
        info["length_detail"] = "/Length=%s actual=%d" % (declared, actual)
    info["content"] = content

    info["texts"] = []
    for block in re.findall(r"BT.*?ET", content.decode("latin-1"), re.S):
        tm = re.match(
            r"^BT /F([12]) ([\d.]+) Tf (1 0 0 1) (-?[\d.]+) (-?[\d.]+) "
            r"Tm \((.*)\) Tj ET$",
            " ".join(block.split()))
        if tm:
            info["texts"].append({"size": float(tm.group(2)),
                                  "x": float(tm.group(4)),
                                  "y": float(tm.group(5)),
                                  "text": re.sub(r"\\([\\()])", r"\1", tm.group(6))})
        else:
            info["problems"].append("malformed text block: %r" % block[:80])
    return info


# ── 1. load_log ─────────────────────────────────────────────────────────────
def test_load_log(run: str) -> None:
    print("\n[load_log]")
    records = plot_mod.load_log([run])
    check("load_log: run dir -> 3 records", len(records) == 3, len(records))
    check("load_log: generations sorted 0,1,2",
          [r["generation_id"] for r in records] == [0, 1, 2])
    check("load_log: attaches internal _run_id",
          all(r["_run_id"] == "run-test-0001" for r in records))
    check("load_log: attaches evidence dict",
          isinstance(records[0]["_evidence"], dict)
          and records[0]["_evidence"].get("plain_attack_baseline_intercept_rate") == 0.9505)

    direct = plot_mod.load_log([os.path.join(run, "coevolution_log.jsonl")])
    check("load_log: accepts a bare .jsonl path", len(direct) == 3)

    exc = _raises(lambda: plot_mod.load_log([os.path.join(WORK, "run-does-not-exist")]))
    check("load_log: missing path raises SystemExit", exc is not None, type(exc).__name__)
    check("load_log: missing path error names the path",
          exc is not None and "run-does-not-exist" in str(exc))

    empty_dir = os.path.join(WORK, "run-no-log")
    os.makedirs(empty_dir, exist_ok=True)
    exc = _raises(lambda: plot_mod.load_log([empty_dir]))
    check("load_log: dir without coevolution_log.jsonl raises", exc is not None,
          str(exc) if exc else "")

    empty_log = _make_run("run-empty-log", [])
    exc = _raises(lambda: plot_mod.load_log([empty_log]))
    check("load_log: empty log raises (no empty figure)", exc is not None,
          str(exc) if exc else "")

    exc = _raises(lambda: plot_mod.load_log([]))
    check("load_log: empty path list raises", exc is not None)


# ── 2. build_series ─────────────────────────────────────────────────────────
def test_build_series(run: str, run_single: str, run_plain_missing: str,
                      run_no_evidence: str) -> None:
    print("\n[build_series]")
    log_rows = _read_log_rows(os.path.join(run, "coevolution_log.jsonl"))
    series = plot_mod.build_series(plot_mod.load_log([run]))
    gens = series["generations"]

    check("build_series: generations == [0,1,2]", gens == [0, 1, 2], gens)
    check("build_series: baseline_zeroday pointwise == log",
          series["baseline_zeroday"] == [log_rows[g]["intercept_rate_baseline_zeroday"]
                                         for g in gens])
    check("build_series: evolved_zeroday pointwise == log",
          series["evolved_zeroday"] == [log_rows[g]["intercept_rate_evolved_zeroday"]
                                        for g in gens])
    check("build_series: benign_false_positive pointwise == log",
          series["benign_false_positive"] == [log_rows[g]["benign_false_positive_rate"]
                                              for g in gens])
    check("build_series: plaintext reference from evidence (0.9505 x 3)",
          series["plain_attack_baseline"] == [0.9505, 0.9505, 0.9505])
    check("build_series: plaintext source kind == evidence",
          series["plain_attack_baseline_source"]["kind"] == "evidence")
    check("build_series: plaintext source field recorded",
          series["plain_attack_baseline_source"]["field"]
          == "plain_attack_baseline_intercept_rate")
    check("build_series: fields mapping is the contract §6.3 names",
          series["fields"] == {
              "generations": "generation_id",
              "baseline_zeroday": "intercept_rate_baseline_zeroday",
              "evolved_zeroday": "intercept_rate_evolved_zeroday",
              "benign_false_positive": "benign_false_positive_rate",
              "plain_attack_baseline": "plain_attack_baseline_intercept_rate"})
    check("build_series: patches_total from last generation",
          series["patches"]["patches_total"] == 2)
    check("build_series: patch_ids_accepted",
          series["patches"]["patch_ids_accepted"] == ["P-0001", "P-0002"])
    check("build_series: stop_reason", series["stop_reason"] == "converged")
    check("build_series: per_generation keeps every raw log row",
          series["per_generation"] == [log_rows[g] for g in gens])
    check("build_series: internal _ keys do not leak into per_generation",
          all(not any(k.startswith("_") for k in row)
              for row in series["per_generation"]))
    check("build_series: warnings is a list", isinstance(series["warnings"], list))

    # 缺字段 → 报错且点名
    bad = _rows()
    del bad[1]["intercept_rate_evolved_zeroday"]
    exc = _raises(lambda: plot_mod.build_series(bad))
    check("build_series: missing required field raises SystemExit",
          isinstance(exc, SystemExit), type(exc).__name__)
    check("build_series: error names the missing field",
          exc is not None and "intercept_rate_evolved_zeroday" in str(exc), str(exc))
    check("build_series: error names the generation",
          exc is not None and "generation 1" in str(exc), str(exc))

    bad = _rows()
    bad[0]["intercept_rate_baseline_zeroday"] = None
    exc = _raises(lambda: plot_mod.build_series(bad))
    check("build_series: null required field raises", exc is not None, str(exc))

    bad = _rows()
    bad[0]["benign_false_positive_rate"] = 1.7
    exc = _raises(lambda: plot_mod.build_series(bad))
    check("build_series: rate outside [0,1] raises", exc is not None, str(exc))

    bad = _rows()
    bad[0]["benign_false_positive_rate"] = "0.5"
    exc = _raises(lambda: plot_mod.build_series(bad))
    check("build_series: non-numeric rate raises", exc is not None, str(exc))

    bad = _rows()
    bad[2]["generation_id"] = 1
    exc = _raises(lambda: plot_mod.build_series(bad))
    check("build_series: duplicate generation_id raises", exc is not None, str(exc))

    bad = _rows()
    bad[0]["generation_id"] = "0"
    exc = _raises(lambda: plot_mod.build_series(bad))
    check("build_series: non-integer generation_id raises", exc is not None, str(exc))

    exc = _raises(lambda: plot_mod.build_series([]))
    check("build_series: empty records raises", exc is not None, str(exc))

    # 良性误伤率缺失 → 由 count/total 精确导出并留痕
    rows = _rows()
    del rows[0]["benign_false_positive_rate"]
    s = plot_mod.build_series(rows)
    check("build_series: benign rate derived from count/total when field missing",
          _almost(s["benign_false_positive"][0], 2.0 / 74.0), s["benign_false_positive"])
    check("build_series: derivation recorded in fields_derived",
          any(d.get("generation_id") == 0 for d in s["fields_derived"]))

    # 平台化：先上升后水平（官方 run 形态）—— 只允许由 log 数值现算，不许平滑造出来
    s_plat = plot_mod.build_series(_plateau_rows(), plain_attack_baseline=1.0)
    check("build_series: plateau detected for a rising-then-flat curve",
          isinstance(s_plat["plateau"], dict)
          and s_plat["plateau"]["plateau_start_generation"] == 2
          and s_plat["plateau"]["evolved_zeroday_value"] == 0.90,
          s_plat.get("plateau"))
    check("build_series: plateau reports patches accepted during the plateau",
          s_plat["plateau"]["patches_added_during_plateau"] == 3,
          s_plat["plateau"])
    check("build_series: no plateau for a still-rising fixture",
          series["plateau"] is None, series["plateau"])

    # 单代边界（x 轴退化，不许除零）
    single = plot_mod.build_series(plot_mod.load_log([run_single]))
    check("build_series: single generation works", single["generations"] == [0])
    check("build_series: single generation x axis has no division by zero",
          isinstance(plot_mod.build_ops(single), list))

    # 多 run：取最新 run_id 并记录被忽略的 run
    merged = plot_mod.build_series(plot_mod.load_log([run, run_single]))
    check("build_series: multiple runs -> latest run_id selected",
          merged["run_id"] == "run-test-0002", merged["run_id"])
    check("build_series: ignored runs recorded",
          merged["ignored_runs"] == ["run-test-0001"], merged["ignored_runs"])

    # evidence 缺明文参照字段 → 现算回退
    fb = plot_mod.build_series(plot_mod.load_log([run_plain_missing]))
    check("build_series: plaintext fallback computed when evidence field missing",
          fb["plain_attack_baseline_source"]["kind"] == "computed")
    check("build_series: computed plaintext reference is a valid rate",
          all(0.0 <= v <= 1.0 for v in fb["plain_attack_baseline"]))
    check("build_series: fallback recorded a warning",
          any("plain_attack_baseline_intercept_rate" in w for w in fb["warnings"]))

    # 完全没有 evidence 文件 → 同样是现算
    no_ev = plot_mod.build_series(plot_mod.load_log([run_no_evidence]))
    check("build_series: no evidence file -> computed plaintext reference",
          no_ev["plain_attack_baseline_source"]["kind"] == "computed")


# ── 3. build_ops ────────────────────────────────────────────────────────────
def test_build_ops(series: dict) -> None:
    print("\n[build_ops]")
    ops = plot_mod.build_ops(series)

    def role(name: str) -> list[dict]:
        return [op for op in ops if op["role"] == name]

    check("build_ops: produces draw ops", len(ops) > 30, len(ops))
    check("build_ops: four contract lines present",
          all(role("line:" + k) for k in ("baseline_zeroday", "evolved_zeroday",
                                          "plain_attack_baseline",
                                          "benign_false_positive")))
    check("build_ops: baseline markers = one per generation",
          len(role("marker:baseline_zeroday")) == 3)
    check("build_ops: evolved markers = one per generation",
          len(role("marker:evolved_zeroday")) == 3)
    check("build_ops: benign markers are squares = one per generation",
          len(role("marker:benign_false_positive")) == 3
          and all(op["op"] == "rect" for op in role("marker:benign_false_positive")))
    check("build_ops: plaintext reference is a dashed line",
          role("line:plain_attack_baseline")[0]["dash"] is not None)
    labels = [op["text"] for op in ops if op["op"] == "text"]
    check("build_ops: title present",
          any("Attack-Defense Co-evolution" in t for t in labels))
    check("build_ops: legend has all four labels",
          all(any(needle in t for t in labels) for needle in
              ("Baseline defense - held-out mutations",
               "Evolved defense - held-out mutations",
               "plaintext attacks (reference)",
               "Benign false-positive rate")))
    check("build_ops: x axis label mentions generation_id",
          any("generation_id" in t and "evolution generation" in t for t in labels))
    check("build_ops: footnote starts with 'source:'",
          any(t.startswith("source:") for t in labels))
    foot = role("footnote")
    check("build_ops: footnote is 6 pt and carries run/generations/patches",
          len(foot) == 1 and foot[0]["size"] == 6.0
          and "run-test-0001" in foot[0]["text"]
          and "3 generations" in foot[0]["text"]
          and "2 patches" in foot[0]["text"])
    check("build_ops: y tick labels are 0.0..1.0",
          [op["text"] for op in role("y-ticklabel")] == ["0.0", "0.25", "0.5", "0.75", "1.0"])
    check("build_ops: x tick labels are the real generation ids",
          [op["text"] for op in role("x-ticklabel")] == ["0", "1", "2"])

    inset_ops = plot_mod.build_ops({**series, "layout": {"inset": True}})
    inset_roles = {op["role"] for op in inset_ops}
    check("build_ops: --inset moves the benign line into the inset panel",
          "inset-line:benign_false_positive" in inset_roles
          and "line:benign_false_positive" not in inset_roles)
    check("build_ops: inset has its own tick labels",
          sum(1 for op in inset_ops if op["role"] == "inset-ticklabel") >= 3)

    plat_ops = plot_mod.build_ops(
        plot_mod.build_series(_plateau_rows(), plain_attack_baseline=1.0))
    check("build_ops: plateau annotation exists for a rising-then-flat curve",
          any(op["role"] == "plateau-note" for op in plat_ops))
    check("build_ops: no plateau annotation when the curve keeps moving",
          not any(op["role"] == "plateau-note" for op in ops))


# ── 4. render_pdf ───────────────────────────────────────────────────────────
def test_render_pdf(series: dict, out_dir: str) -> str:
    print("\n[render_pdf]")
    os.makedirs(out_dir, exist_ok=True)
    pdf = os.path.join(out_dir, "evolution_curve.pdf")
    plot_mod.render_pdf(series, pdf)
    raw = open(pdf, "rb").read()
    info = _parse_pdf(pdf)

    check("render_pdf: file written and non-trivial", os.path.getsize(pdf) > 2000,
          os.path.getsize(pdf))
    check("render_pdf: starts with %PDF-1.4", info["header_ok"])
    check("render_pdf: ends with %%EOF", info["eof_ok"])
    check("render_pdf: has object table (N 0 obj)", info["has_objects"]
          and info["n_objects"] == 7, info["n_objects"])
    check("render_pdf: has xref table", info["has_xref"])
    check("render_pdf: has trailer", info["has_trailer"])
    check("render_pdf: has startxref", info["has_startxref"])
    check("render_pdf: startxref offset parses and points at 'xref'",
          info["startxref_ok"], info.get("startxref"))
    check("render_pdf: every in-use xref offset points at 'N 0 obj'",
          info["offsets_ok"], "%d xref entries" % info["xref_entries"])
    check("render_pdf: /Size equals xref entry count", info["size_ok"])
    check("render_pdf: trailer has /Root", info["trailer_ok"])
    check("render_pdf: content stream /Length matches actual bytes",
          info["length_ok"], info["length_detail"])
    check("render_pdf: exactly one page, one catalog",
          info["pages"] == 1 and info["catalog"] == 1,
          (info["pages"], info["catalog"]))
    check("render_pdf: MediaBox is 560x400 pt", info["mediabox"] == (0.0, 0.0, 560.0, 400.0),
          info["mediabox"])
    check("render_pdf: base-14 Helvetica fonts registered",
          sorted(info["fonts"].values()) == ["Helvetica", "Helvetica-Bold"],
          info["fonts"])

    texts = [t["text"] for t in info["texts"]]
    sizes = [t["size"] for t in info["texts"]]
    check("render_pdf: text objects are real (not a blank page)", len(texts) >= 15,
          len(texts))
    check("render_pdf: all figure text is ASCII",
          all(all(ord(ch) < 127 for ch in t) for t in texts))
    check("render_pdf: all text inside the page box",
          all(0.0 <= t["x"] <= 560.0 and 0.0 <= t["y"] <= 400.0
              for t in info["texts"]))
    check("render_pdf: all text >= 8 pt except the 6 pt source footnote",
          all(size >= 8.0 for size, text in zip(sizes, texts)
              if not text.startswith("source:")))
    check("render_pdf: exactly one 6 pt footnote starting with 'source:'",
          sum(1 for t, s in zip(texts, sizes)
              if s == 6.0 and t.startswith("source:")) == 1)
    content = info["content"].decode("latin-1")

    def has_color(color) -> bool:
        code = "%s %s %s RG" % tuple(("%.4f" % v).rstrip("0").rstrip(".")
                                     for v in color)
        return code in content

    check("render_pdf: red stroke op present", has_color(RED))
    check("render_pdf: blue stroke op present", has_color(BLUE))
    check("render_pdf: orange stroke op present", has_color(ORANGE))
    check("render_pdf: grey stroke op present", has_color(GRAY))
    check("render_pdf: no raster image / XObject paint in content",
          "BI " not in content and "Do" not in content)

    # 仓库内独立 PDF 校验器（build/ 是 .gitignore 的构建产物，存在则作为第三方验证）
    verifier = os.path.join(ROOT, "build", "bi", "figures", "verify_pdf.py")
    if os.path.isfile(verifier):
        proc = subprocess.run([PY, "-X", "utf8", verifier, pdf], cwd=ROOT,
                              capture_output=True, text=True)
        check("render_pdf: repo verify_pdf.py independent structural PASS",
              proc.returncode == 0, (proc.stdout or proc.stderr)[-400:])
    else:
        print("  note: build/**/verify_pdf.py absent; independent verifier skipped")

    # inset 变体：页面加宽，仍然单页可解析
    inset_pdf = os.path.join(out_dir, "evolution_curve_inset.pdf")
    plot_mod.render_pdf({**series, "layout": {"inset": True}}, inset_pdf)
    inset_info = _parse_pdf(inset_pdf)
    check("render_pdf: inset page is 726x400 pt and still valid",
          inset_info["mediabox"] == (0.0, 0.0, 726.0, 400.0)
          and inset_info["length_ok"] and inset_info["pages"] == 1,
          (inset_info["mediabox"], inset_info["length_detail"]))
    check("render_pdf: inset figure contains the zoom title",
          any("zoom" in t["text"] for t in inset_info["texts"]))
    return pdf


# ── 5. render_png ───────────────────────────────────────────────────────────
def test_render_png(series: dict, out_dir: str) -> str:
    print("\n[render_png]")
    from PIL import Image

    png = os.path.join(out_dir, "evolution_curve.png")
    plot_mod.render_png(series, png, dpi=300)
    check("render_png: file written", os.path.getsize(png) > 10000,
          os.path.getsize(png))
    signature = open(png, "rb").read(8)
    check("render_png: PNG signature", signature == b"\x89PNG\r\n\x1a\n")

    image = Image.open(png)
    expected = (int(round(560 * 300 / 72.0)), int(round(400 * 300 / 72.0)))
    check("render_png: pixel size = 560x400 pt at 300 dpi (=%r)" % (expected,),
          image.size == expected, image.size)
    dpi = image.info.get("dpi") or (0.0, 0.0)
    check("render_png: dpi metadata is 300",
          _almost(dpi[0], 300.0, 0.5) and _almost(dpi[1], 300.0, 0.5), dpi)

    rgb = image.convert("RGB")
    colors = rgb.getcolors(maxcolors=1 << 20) or []

    def count(color) -> int:
        target = tuple(int(round(255.0 * v)) for v in color)
        return sum(count for count, value in colors if value == target)

    check("render_png: red pixels really exist", count(RED) > 100, count(RED))
    check("render_png: blue pixels really exist", count(BLUE) > 100, count(BLUE))
    check("render_png: orange pixels really exist", count(ORANGE) > 50, count(ORANGE))
    check("render_png: grey dashed pixels really exist", count(GRAY) > 50, count(GRAY))

    inset_png = os.path.join(out_dir, "evolution_curve_inset.png")
    plot_mod.render_png({**series, "layout": {"inset": True}}, inset_png, dpi=150)
    inset_image = Image.open(inset_png)
    check("render_png: inset png follows the wider page at the given dpi",
          inset_image.size == (int(round(726 * 150 / 72.0)),
                               int(round(400 * 150 / 72.0))), inset_image.size)
    check("render_png: invalid dpi raises", _raises(
        lambda: plot_mod.render_png(series, os.path.join(out_dir, "x.png"), dpi=0))
        is not None)
    return png


# ── 6. write_figure_data ────────────────────────────────────────────────────
def test_figure_data(series: dict, run: str, out_dir: str) -> str:
    print("\n[write_figure_data]")
    os.makedirs(out_dir, exist_ok=True)
    data_path = os.path.join(out_dir, "figure_data.json")
    plot_mod.write_figure_data(series, data_path, run_id=series["run_id"],
                               caption_zh=plot_mod.DEFAULT_CAPTION_ZH,
                               caption_en=plot_mod.DEFAULT_CAPTION_EN)
    check("figure_data: file written", os.path.isfile(data_path))
    with io.open(data_path, encoding="utf-8") as fh:
        data = json.load(fh)

    check("figure_data: schema id", data.get("schema") == "phase-b-evolution-curve-v1")
    check("figure_data: run_id", data.get("run_id") == "run-test-0001")
    check("figure_data: generations", data.get("generations") == [0, 1, 2])
    check("figure_data: patches_total + patch ids",
          data.get("patches_total") == 2
          and data.get("patch_ids_accepted") == ["P-0001", "P-0002"])
    check("figure_data: generated_at is a parseable ISO timestamp",
          isinstance(data.get("generated_at"), str)
          and datetime.fromisoformat(data["generated_at"]) is not None)
    check("figure_data: caption_zh contains Chinese text",
          isinstance(data.get("caption_zh"), str)
          and any("\u4e00" <= ch <= "\u9fff" for ch in data["caption_zh"]))
    check("figure_data: caption_en non-empty and ASCII",
          isinstance(data.get("caption_en"), str) and len(data["caption_en"]) > 40
          and all(ord(ch) < 127 for ch in data["caption_en"]))
    check("figure_data: limitations recorded (>=4)",
          isinstance(data.get("limitations"), list) and len(data["limitations"]) >= 4)
    check("figure_data: held-out is not OOD limitation recorded",
          any("out-of-distribution" in x for x in data["limitations"]))
    check("figure_data: gen-0 measurement caveat recorded",
          any("generation 0 already contains" in x for x in data["limitations"]))
    check("figure_data: held-out evaluation-set protocol recorded",
          any("zeroday_generation_id=0" in x for x in data["limitations"])
          and data["evidence"]["zeroday_protocol"]
          == "held-out seeds x generation-0 mutation distribution",
          data["evidence"].get("zeroday_protocol"))
    check("figure_data: plateau key present (None for a still-rising curve)",
          "plateau" in data and data["plateau"] is None)

    # 平台化 run：动态 limitation + plateau 统计必须由 log 现算写出
    s_plat = plot_mod.build_series(_plateau_rows(), plain_attack_baseline=1.0)
    plat_path = os.path.join(out_dir, "figure_data_plateau.json")
    plot_mod.write_figure_data(s_plat, plat_path, run_id=s_plat["run_id"],
                               caption_zh=plot_mod.DEFAULT_CAPTION_ZH,
                               caption_en=plot_mod.DEFAULT_CAPTION_EN)
    with io.open(plat_path, encoding="utf-8") as fh:
        plat_data = json.load(fh)
    check("figure_data: plateau limitation generated from log data",
          any("plateau is data, not smoothing" in x
              for x in plat_data["limitations"]),
          plat_data["limitations"][-1:])
    check("figure_data: plateau block records start/end/patch delta",
          plat_data["plateau"]["plateau_start_generation"] == 2
          and plat_data["plateau"]["plateau_end_generation"] == 5
          and plat_data["plateau"]["patches_added_during_plateau"] == 3,
          plat_data["plateau"])

    lines = {line["id"]: line for line in data.get("lines", [])}
    check("figure_data: exactly the four contract lines",
          list(lines) == ["baseline_zeroday", "evolved_zeroday",
                          "plain_attack_baseline", "benign_false_positive"], list(lines))
    check("figure_data: each line names its source field",
          lines["baseline_zeroday"]["source_field"] == "intercept_rate_baseline_zeroday"
          and lines["evolved_zeroday"]["source_field"] == "intercept_rate_evolved_zeroday"
          and lines["benign_false_positive"]["source_field"] == "benign_false_positive_rate"
          and lines["plain_attack_baseline"]["source_field"]
          == "plain_attack_baseline_intercept_rate")

    # 逐点一致：测试侧独立读日志再比对（不用 load_log）
    log_rows = _read_log_rows(os.path.join(run, "coevolution_log.jsonl"))
    for line_id, field in (("baseline_zeroday", "intercept_rate_baseline_zeroday"),
                           ("evolved_zeroday", "intercept_rate_evolved_zeroday"),
                           ("benign_false_positive", "benign_false_positive_rate")):
        expected = [log_rows[g][field] for g in data["generations"]]
        check("figure_data: %s values == log pointwise" % line_id,
              lines[line_id]["values"] == expected,
              (lines[line_id]["values"], expected))
        check("figure_data: %s points carry generation_id" % line_id,
              [p["generation_id"] for p in lines[line_id]["points"]]
              == data["generations"])
    check("figure_data: plaintext reference is the constant evidence value",
          lines["plain_attack_baseline"]["constant"] is True
          and lines["plain_attack_baseline"]["values"] == [0.9505, 0.9505, 0.9505]
          and lines["plain_attack_baseline"]["source_detail"]["kind"] == "evidence")
    check("figure_data: per_generation equals the raw log rows",
          data.get("per_generation") == [log_rows[g] for g in data["generations"]])
    check("figure_data: source block records the log file",
          data.get("source", {}).get("log_files")
          == ["experiments/phase-b/runs/run-test-0001/coevolution_log.jsonl"]
          or any("coevolution_log.jsonl" in p
                 for p in data.get("source", {}).get("log_files", [])),
          data.get("source"))
    check("figure_data: context series kept but not plotted as lines",
          data.get("context_series", {}).get("evolved_mutated_intercept_rate", {})
          .get("source_field") == "intercept_rate_evolved"
          and len(data.get("lines", [])) == 4,
          list(data.get("context_series", {})))
    return data_path


# ── 7. CLI ──────────────────────────────────────────────────────────────────
def test_cli(run: str, run_bad: str, run_missing: str) -> None:
    print("\n[cli]")
    out_dir = os.path.join(WORK, "cli-out")

    with contextlib.redirect_stdout(io.StringIO()):
        rc = plot_mod.main(["--run", run, "--out", out_dir])
    check("cli: exit 0 on a complete run", rc == 0, rc)
    for name in ("evolution_curve.pdf", "evolution_curve.png", "figure_data.json"):
        check("cli: writes %s" % name, os.path.isfile(os.path.join(out_dir, name)))

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc_missing = plot_mod.main(["--run", run_missing, "--out", out_dir])
    check("cli: missing run dir returns exit code 2", rc_missing == 2, rc_missing)

    stderr = io.StringIO()
    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(stderr):
        rc_bad = plot_mod.main(["--run", run_bad, "--out", out_dir])
    check("cli: missing log field returns exit code 2", rc_bad == 2, rc_bad)
    check("cli: stderr names the missing field",
          "intercept_rate_evolved_zeroday" in stderr.getvalue(), stderr.getvalue()[-300:])

    with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
        rc_inset = plot_mod.main(["--run", run, "--out", os.path.join(WORK, "cli-inset"),
                                  "--inset"])
    inset_info = _parse_pdf(os.path.join(WORK, "cli-inset", "evolution_curve.pdf"))
    check("cli: --inset writes a 726x400 pt single-page PDF",
          rc_inset == 0 and inset_info["mediabox"] == (0.0, 0.0, 726.0, 400.0),
          (rc_inset, inset_info["mediabox"]))
    with io.open(os.path.join(WORK, "cli-inset", "figure_data.json"), encoding="utf-8") as fh:
        inset_data = json.load(fh)
    check("cli: --inset recorded in figure_data", inset_data["figure"]["inset"] is True)

    # 真实子进程：退出码与错误信息
    script = os.path.join(ROOT, "tools", "plot_coevolution_curve.py")
    proc = subprocess.run([PY, "-X", "utf8", script, "--run", run,
                           "--out", os.path.join(WORK, "cli-subprocess")],
                          cwd=ROOT, capture_output=True, text=True)
    check("cli (subprocess): exit 0 and prints run_id",
          proc.returncode == 0 and "run-test-0001" in proc.stdout,
          (proc.returncode, proc.stderr[-300:]))
    proc_bad = subprocess.run([PY, "-X", "utf8", script, "--run", run_bad,
                               "--out", os.path.join(WORK, "cli-subprocess-bad")],
                              cwd=ROOT, capture_output=True, text=True)
    check("cli (subprocess): missing data exits with code 2",
          proc_bad.returncode == 2, proc_bad.returncode)
    check("cli (subprocess): stderr names the missing field",
          "intercept_rate_evolved_zeroday" in proc_bad.stderr, proc_bad.stderr[-300:])


# ── main ────────────────────────────────────────────────────────────────────
def main() -> int:
    print("LINGSHU Phase B · A2 绘图自测（tools/plot_coevolution_curve.py）")
    os.makedirs(WORK, exist_ok=True)

    run = _make_run("run-test-0001", _rows(), evidence=_evidence("run-test-0001"))
    single = _make_run("run-test-0002", [_rows()[0]], evidence=_evidence("run-test-0002", 0.9))
    plain_missing = _make_run("run-test-0003", _rows(),
                              evidence=_evidence("run-test-0003", None))
    no_evidence = _make_run("run-test-0004", _rows(), evidence=None)

    bad_rows = _rows()
    del bad_rows[1]["intercept_rate_evolved_zeroday"]
    run_bad = _make_run("run-test-bad-missing", bad_rows,
                        evidence=_evidence("run-test-bad-missing"))
    run_missing = os.path.join(WORK, "run-does-not-exist")

    test_load_log(run)
    test_build_series(run, single, plain_missing, no_evidence)

    series = plot_mod.build_series(plot_mod.load_log([run]))
    out_dir = os.path.join(WORK, "render-out")
    test_build_ops(series)
    test_render_pdf(series, out_dir)
    test_render_png(series, out_dir)
    test_figure_data(series, run, out_dir)
    test_cli(run, run_bad, run_missing)

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
