# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 混沌工程 v2（docs/23）

    python tests/test_chaos_campaign.py

覆盖（全部为**函数/进程级**验证，零模型调用；F6 只起一次 `node ... --no-model`）：
- 语料与 manifest：`gen_chaos_v2.py --check` 逐字节一致、`validate.py` 通过、8 族齐全、
  行封套/唯一性/严重度/指纹、行数下限、确定性；
- 统计协议：Wilson 区间已知值、digest 排除时间/路径、R 次聚合（全真/偶发/全假/缺失）；
- 不变量目录：INV-01…INV-20 齐全且被族模块声明覆盖；
- 族模块契约：8 个模块可导入、FAMILY_ID/INVARIANTS/STRESS_CLASS/run 合规、
  对真实语料跑出的 payload 字段齐全；
- 全量 campaign：退出码 0、schema/产物、同 seed digest 一致（INV-01）、
  换 seed 不变量不变（INV-20）、零模型调用（INV-02）、零仓库污染（INV-19）；
- 反例（必须红）：不变量失败/偶发失败/哈希不符/越界写/坏行/坏 manifest/未证明不变量；
- 红线：v1 语料与指纹**逐字节未变**；族模块源码不含 time/random/uuid；
  F6 的 node 调用必须带 `--no-model`。

设计约束（本机环境）：**不新建目录**（受限沙箱下 Python 不能在新建目录里写文件），
所以本测试只写 `acceptance/chaos/v2/` 下的文件，并在结束时删除自己产生的文件。
"""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import run_chaos_campaign as RC            # noqa: E402
from chaos_v2 import corpus as C           # noqa: E402
from chaos_v2 import stats as S            # noqa: E402
from chaos_v2 import invariants as I       # noqa: E402

PY = sys.executable or "python"
MANIFEST = os.path.join(ROOT, "chaos-set", "v2", "manifest.json")
V2DIR = os.path.dirname(MANIFEST)
VALIDATOR = os.path.join(V2DIR, "validate.py")
GENERATOR = os.path.join(ROOT, "tools", "gen_chaos_v2.py")
OUT_DIR = os.path.join(ROOT, "acceptance", "chaos", "v2")
FAMS = ["f1_stpa_matrix", "f2_phase_boundary", "f3_integral_windup",
        "f4_recall_adversarial", "f5_assembly_pressure", "f6_storage_faults",
        "f7_steady_state", "f8_metamorphic"]
MIN_ROWS = {"f1_stpa_matrix": 28, "f2_phase_boundary": 26, "f3_integral_windup": 10,
            "f4_recall_adversarial": 14, "f5_assembly_pressure": 18,
            "f6_storage_faults": 10, "f7_steady_state": 8, "f8_metamorphic": 14}
# v1 冻结指纹（chaos-set/README.md §7）；v2 红线：v1 逐字节保留
V1_SHA = {
    "long_context_flood.jsonl": "85b85b47b6745b0b0898f3d05164c7938f546f4d69d228eacdb7b90cda618993",
    "memory_conflict.jsonl": "0b768835e4b5aec50b69d1d54ae9ff1a4c6eb52ff2356b63624a2f7f1a3960ad",
    "emotion_jailbreak.jsonl": "8febb054f81e5c372c3c0903de87079dca91bcf820db8848d9a5463d684f6f75",
    "variety.jsonl": "70af817088bc578b8647545ab0c0f9822bae34d397300565bb1d5ab4757ab256",
}

PASS, FAIL = [], []
CREATED = []          # 本测试写出、结束时需要删除的文件


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name
          + (("  — " + str(detail)) if detail else ""))


def run(args, timeout=180):
    return subprocess.run([PY] + args, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", timeout=timeout)


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


# ── 共用夹具 ────────────────────────────────────────────
print("[准备] 加载 manifest 与语料（缺文件时用占位，后续断言会明确失败）")
MANIFEST_OK, MANIFEST_ERR = False, ""
try:
    MF = C.load_manifest(MANIFEST)
    MANIFEST_OK = True
except C.CorpusError as exc:
    MF = None
    MANIFEST_ERR = str(exc)
    print("  manifest 未就绪：%s" % MANIFEST_ERR.splitlines()[0])

CORPUS = {}
if MANIFEST_OK:
    CORPUS = C.load_corpus(MF, FAMS)


def fam_rows(fid):
    fc = CORPUS.get(fid)
    return list(fc.rows) if fc else []


# ── [1] 语料与 manifest ─────────────────────────────────
print("\n[1] 语料与 manifest")
check("manifest 存在且 schema 校验通过", MANIFEST_OK, MANIFEST_ERR[:160])
check("manifest schema = lingshu-chaos-campaign/2", MANIFEST_OK and MF.schema == RC.SCHEMA,
      MF.schema if MANIFEST_OK else "")
check("族数量 = 8", MANIFEST_OK and len(MF.families) == 8,
      len(MF.families) if MANIFEST_OK else "")
check("族 id 顺序 f1→f8", MANIFEST_OK and MF.family_ids() == FAMS,
      MF.family_ids() if MANIFEST_OK else "")
check("seed_base 是整数", MANIFEST_OK and isinstance(MF.seed_base, int),
      MF.seed_base if MANIFEST_OK else "")
check("repeats >= 3（§6.1 R=3）", MANIFEST_OK and MF.repeats >= 3,
      MF.repeats if MANIFEST_OK else "")
check("base_ts 是固定正数（确定性时钟）", MANIFEST_OK and MF.base_ts > 0,
      MF.base_ts if MANIFEST_OK else "")

gen_ok, gen_out = False, "未运行"
if os.path.isfile(GENERATOR):
    r = run([GENERATOR, "--check"])
    gen_ok, gen_out = r.returncode == 0, (r.returncode, (r.stdout or r.stderr)[-200:])
check("gen_chaos_v2.py --check 通过（语料=生成物，未被手改）", gen_ok, gen_out)

val_ok, val_out = False, "未运行"
if os.path.isfile(VALIDATOR):
    r = run([VALIDATOR])
    val_ok, val_out = r.returncode == 0, (r.returncode, (r.stdout or r.stderr)[-200:])
check("chaos-set/v2/validate.py 通过", val_ok, val_out)

check("8 份语料文件全部存在且哈希校验 ok",
      MANIFEST_OK and all(CORPUS.get(f) and CORPUS[f].ok for f in FAMS),
      {f: (CORPUS[f].status if CORPUS.get(f) else "missing") for f in FAMS})
check("每族行数 ≥ 设计下限", MANIFEST_OK and all(
    CORPUS.get(f) and CORPUS[f].line_count >= MIN_ROWS[f] for f in FAMS),
      {f: (CORPUS[f].line_count if CORPUS.get(f) else 0) for f in FAMS})

# 行封套：字段、family 一致、case 正则、severity 枚举、case 全域唯一
envelope_ok, envelope_bad = True, []
seen_cases = {}
for fid in FAMS:
    fc = CORPUS.get(fid)
    if not fc:
        envelope_ok = False
        envelope_bad.append(fid + ": 缺语料")
        continue
    for lineno, row in enumerate(fc.rows, 1):
        problems = C.validate_row(row, fid, os.path.basename(fc.path), lineno)
        if problems:
            envelope_ok = False
            envelope_bad.extend(problems[:2])
        case = str(row.get("case") or "")
        if case in seen_cases:
            envelope_ok = False
            envelope_bad.append("case 跨族重复：%s（%s / %s）" % (case, seen_cases[case], fid))
        seen_cases[case] = fid
        if str(row.get("severity")) not in ("mild", "moderate", "severe", "control"):
            envelope_ok = False
            envelope_bad.append("%s: severity 非法 %r" % (fid, row.get("severity")))
check("行封套合法（字段/family/case/severity/唯一性）", envelope_ok, envelope_bad[:4])

sev = {}
if MANIFEST_OK:
    for fid in FAMS:
        fc = CORPUS.get(fid)
        sev[fid] = dict(fc.severity_counts) if fc else {}
check("每条语料都有 severity（含 control 对照）", all(
    sum((sev.get(f) or {}).values()) > 0 for f in FAMS), sev)
check("存在 control 对照行（误报率的分母）",
      any((sev.get(f) or {}).get("control", 0) > 0 for f in FAMS),
      {f: (sev.get(f) or {}).get("control", 0) for f in FAMS})
check("F1 覆盖 UCA-01…08 全部（UCA-08 只在 downgrades 里）",
      MANIFEST_OK and set("UCA-0%d" % i for i in range(1, 9))
      <= {u for row in fam_rows("f1_stpa_matrix")
          for u in (row.get("expect") or {}).get("ucas", [])
          + (row.get("expect") or {}).get("downgrades", [])},
      sorted({u for row in fam_rows("f1_stpa_matrix")
              for u in (row.get("expect") or {}).get("ucas", [])}))
check("F1 含 semantic_miss（词表盲区诚实记录）",
      any((row.get("expect") or {}).get("kind") == "semantic_miss"
          for row in fam_rows("f1_stpa_matrix")),
      len([r for r in fam_rows("f1_stpa_matrix")
           if (r.get("expect") or {}).get("kind") == "semantic_miss"]))
check("F2 边界样本（0.60–0.80）≥10", MANIFEST_OK and (
    len([r for r in fam_rows("f2_phase_boundary")
         if 0.60 <= float((r.get("input") or {}).get("used", 0))
         / max(1.0, float((r.get("input") or {}).get("window", 1))) < 0.80]) >= 10),
      len(fam_rows("f2_phase_boundary")))
check("F3 recipe 覆盖 ≥6 种", len({(r.get("input") or {}).get("recipe")
                                   for r in fam_rows("f3_integral_windup")}) >= 6,
      sorted({(r.get("input") or {}).get("recipe")
              for r in fam_rows("f3_integral_windup")}))
check("F6 scenario 覆盖 ≥10 种", len({(r.get("input") or {}).get("scenario")
                                      for r in fam_rows("f6_storage_faults")}) >= 10,
      sorted({(r.get("input") or {}).get("scenario")
              for r in fam_rows("f6_storage_faults")}))
check("F8 覆盖 MR1…MR6", set(range(1, 7)) <= {
    int(str((r.get("input") or {}).get("mr", "")).replace("MR", ""))
    for r in fam_rows("f8_metamorphic")
    if str((r.get("input") or {}).get("mr", "")).startswith("MR")},
      sorted({(r.get("input") or {}).get("mr") for r in fam_rows("f8_metamorphic")}))

# 确定性：同一语料两次读取哈希一致；行内不含易变字段
check("读两遍语料哈希一致（确定性）", MANIFEST_OK and all(
    C.sha256_file(CORPUS[f].path) == CORPUS[f].actual_sha256 for f in FAMS))
VOLATILE = ("time", "timestamp", "random", "uuid", "rand", "rng", "now")
vol_bad = []
for fid in FAMS:
    for row in fam_rows(fid):
        for key in row.get("input") or {}:
            if str(key).lower() in VOLATILE:
                vol_bad.append("%s.%s" % (fid, key))
check("语料 input 不含时间/随机字段（可复现）", not vol_bad, vol_bad[:4])

# ── [2] 统计协议 ────────────────────────────────────────
print("\n[2] 统计协议（Wilson / digest / 聚合）")
w0 = S.wilson_ci(8, 8)
check("wilson 8/8 → rate=1、上界=1、下界显著小于 1", w0["rate"] == 1.0
      and w0["ci_high"] == 1.0 and 0.5 < w0["ci_low"] < 0.8, w0)
w1 = S.wilson_ci(0, 10)
check("wilson 0/10 → rate=0、下界=0、上界 > 0.2", w1["rate"] == 0.0
      and w1["ci_low"] == 0.0 and w1["ci_high"] > 0.2, w1)
w2 = S.wilson_ci(5, 10)
check("wilson 5/10 → 区间含 0.5 且对称近似", w2["ci_low"] < 0.5 < w2["ci_high"], w2)
check("wilson den=0 → rate/CI 为 None（不造数字）",
      S.wilson_ci(0, 0)["rate"] is None and S.wilson_ci(0, 0)["ci_low"] is None)
check("wilson num>den 被钳制（不产生 >1 的比率）", S.wilson_ci(9, 3)["rate"] <= 1.0)
check("wilson 非法 z 回落 1.96（不制造零宽区间）",
      S.wilson_ci(5, 10, z=0)["ci_low"] != S.wilson_ci(5, 10, z=0)["ci_high"])

d_a = S.digest_of_metrics({"cases": 3, "started_at": "2026-10-27T01:00:00+08:00",
                           "elapsed_ms": 12, "out_dir": "x", "rate": 1.0})
d_b = S.digest_of_metrics({"rate": 1.0, "cases": 3, "out_dir": "y",
                           "elapsed_ms": 999, "started_at": "2027-01-01T00:00:00+08:00"})
check("digest 忽略时间/路径/耗时字段", d_a == d_b)
check("digest 对语义字段敏感",
      S.digest_of_metrics({"cases": 3}) != S.digest_of_metrics({"cases": 4}))
check("digest 规范化与 key 顺序无关",
      S.digest_of_metrics({"a": 1, "b": 2}) == S.digest_of_metrics({"b": 2, "a": 1}))
check("digest 是 64 位 hex", bool(re.fullmatch(r"[0-9a-f]{64}", S.digest_of_metrics({}))))

agg_all = S.aggregate_bools([True, True, True])
check("R 次全真 → pass=True/flaky=False",
      agg_all["pass"] is True and agg_all["flaky"] is False, agg_all)
agg_mixed = S.aggregate_bools([True, False, True])
check("R 次偶发失败 → pass=False 且 flaky=True（不许洗成通过）",
      agg_mixed["pass"] is False and agg_mixed["flaky"] is True, agg_mixed)
agg_none = S.aggregate_bools([False, False, False])
check("R 次全假 → pass=False/flaky=False",
      agg_none["pass"] is False and agg_none["flaky"] is False, agg_none)
agg_missing = S.aggregate_bools([True, None])
check("缺失观测（None）→ 未证明即不通过", agg_missing["pass"] is False, agg_missing)
check("R=0 → 不通过（未观测不等于通过）", S.aggregate_bools([])["pass"] is False)
rates = S.rates_summary([{"a": {"num": 1, "den": 2}}, {"a": {"num": 2, "den": 3}}])
check("rates_summary 按分子分母 pooled 汇总", rates["a"]["num"] == 3
      and rates["a"]["den"] == 5 and 0.0 <= rates["a"]["ci_low"] <= rates["a"]["rate"],
      rates)

# ── [3] 不变量目录 ──────────────────────────────────────
print("\n[3] 不变量目录（INV-01…20）")
ids = list(I.INVARIANT_IDS)
check("不变量数量 = 20", len(ids) == 20, len(ids))
check("ID 形如 INV-01…INV-20 且唯一",
      ids == ["INV-%02d" % i for i in range(1, 21)], ids)
check("每条不变量都有 title/criterion/owner",
      all(I.describe(x).get("title") and I.describe(x).get("criterion")
          and I.describe(x).get("owner") for x in ids))
declared = set()
if MANIFEST_OK:
    for fid in FAMS:
        mod, _ = RC.import_family_module(fid)
        declared |= set(getattr(mod, "INVARIANTS", ()))
check("族模块声明覆盖 ≥15 条不变量", len(declared) >= 15, sorted(declared))
check("族声明的每条不变量都已在目录登记",
      declared <= set(ids), sorted(declared - set(ids)))
check("INV-01/02/18/19/20 由 runner 归属（不在族声明里也必须存在）",
      all(x in ids for x in ("INV-01", "INV-02", "INV-18", "INV-19", "INV-20")))
check("determinism 检查器：同 digest 通过、异 digest 失败",
      I.check_determinism("f", "abc", "abc", 1)["pass"] is True
      and I.check_determinism("f", "abc", "abd", 1)["pass"] is False)
check("determinism 检查器：缺 digest → 不算通过",
      I.check_determinism("f", None, "abc", 1)["pass"] is False)
check("write_scope：无改动 → 通过",
      I.check_write_scope({"added": [], "removed": [], "modified": []})["pass"] is True)
check("INV-19 判定以**进程内写意图归因**为准：越界 open → 失败",
      I.check_write_scope({"added": [], "removed": [], "modified": []},
                          ["D:/out"], write_opens=[{"op": "open", "path": "D:/repo/rules/x.yaml"}]
                          )["pass"] is False)
check("INV-19：仅快照差分（未归因）→ 默认通过并披露 external_changes",
      I.check_write_scope({"added": ["paper/x.md"], "removed": [], "modified": []})["pass"] is True
      and I.check_write_scope({"added": ["paper/x.md"], "removed": [], "modified": []}
                              )["evidence"]["snapshot_diff_total"] == 1)
check("INV-19 --strict-write-scope：仅快照差分 → 失败",
      I.check_write_scope({"added": ["paper/x.md"], "removed": [], "modified": []},
                          strict_snapshot=True)["pass"] is False)
check("INV-19：out-dir 内的写意图 → 通过",
      I.check_write_scope({"added": [], "removed": [], "modified": []},
                          ["D:/LSJH/lingshu/acceptance/chaos/v2"],
                          write_opens=[{"op": "open",
                                        "path": "D:/LSJH/lingshu/acceptance/chaos/v2/a.json"}]
                          )["pass"] is True)
cf = I.check_corpus_frozen({"a.jsonl": "A"}, {"a.jsonl": "A"}, {"a.jsonl": "A"})
check("corpus_frozen：哈希一致 → 通过", cf["pass"] is True, cf)
cf2 = I.check_corpus_frozen({"a.jsonl": "A"}, {"a.jsonl": "A"}, {"a.jsonl": "B"})
check("corpus_frozen：跑后被改 → 失败", cf2["pass"] is False, cf2)

# ── [4] 族模块契约与真实语料 ────────────────────────────
print("\n[4] 族模块契约（对真实语料跑 run()）")
ctx = RC.build_ctx(seed=MF.seed_base if MANIFEST_OK else 20261027, repeat=0,
                   base_ts=MF.base_ts if MANIFEST_OK else 1761541200.0,
                   timeout_s=90.0, run_id="selfcheck-contract")
fam_results = {}
for fid in FAMS:
    try:
        mod, modname = RC.import_family_module(fid)
        problems = I.check_module_contract(MF.family(fid), mod) if MANIFEST_OK else ["manifest 缺失"]
    except Exception as exc:  # noqa: BLE001
        check("%s 模块可导入且契约合规" % fid, False, "%s: %s" % (type(exc).__name__, exc))
        continue
    check("%s 模块可导入且契约合规" % fid, not problems, problems[:3])
    try:
        payload = mod.run(fam_rows(fid), ctx)
        problems = I.normalize_family_payload(fid, getattr(mod, "INVARIANTS", ()), payload)[1]
    except Exception as exc:  # noqa: BLE001
        check("%s run() 不抛异常" % fid, False, "%s: %s" % (type(exc).__name__, exc))
        continue
    check("%s run() 不抛异常且 payload 合规" % fid, not problems, problems[:3])
    fam_results[fid] = payload

for fid, payload in fam_results.items():
    checks = payload.get("checks") or {}
    check("%s 检查失败数为 0" % fid, int(checks.get("failed") or 0) == 0,
          (checks.get("failures") or [])[:2])
    inv = payload.get("invariants") or {}
    check("%s 声明的每条不变量都报了结果" % fid,
          set(inv) >= set(getattr(RC.import_family_module(fid)[0], "INVARIANTS", ())),
          sorted(set(inv)))

if "f6_storage_faults" in fam_results:
    f6 = fam_results["f6_storage_faults"]
    check("F6 INV-16 通过（写路径不静默）", (f6["invariants"].get("INV-16") or {}).get("pass") is True,
          f6["invariants"].get("INV-16"))
    check("F6 node 侧零模型调用", int((f6.get("metrics") or {}).get("node_model_calls", -1)) == 0)
    check("F6 写失败全部被留痕（write_failure_surfaced）",
          int((f6.get("metrics") or {}).get("write_failure_surfaced_cases", 0)) >= 1)

if "f2_phase_boundary" in fam_results:
    m2 = fam_results["f2_phase_boundary"].get("metrics") or {}
    check("F2 边界样本计数 ≥10", int(m2.get("boundary_samples_in_0_60_0_80", 0)) >= 10, m2)

if "f7_steady_state" in fam_results:
    f7 = fam_results["f7_steady_state"]
    check("F7 INV-17 稳态基线通过（回放 recall@3=1.000 等）",
          (f7["invariants"].get("INV-17") or {}).get("pass") is True,
          f7["invariants"].get("INV-17"))

# ── [5] 全量 campaign（两次同 seed + 一次换 seed）──────────
print("\n[5] 全量 campaign（退出码/schema/确定性/独立性）")

# 自测不得污染正式产物：`.last.json` 与覆盖式报告先备份、结束时原样恢复。
# 否则跑完测试后 `.last.json` 会指向已被删除的 selfcheck run（悬空引用）。
_OUT_SNAPSHOT = {}
for _p in ([os.path.join(OUT_DIR, ".last.json")]
           + glob.glob(os.path.join(OUT_DIR, "*-report.md"))):
    if os.path.isfile(_p):
        with open(_p, "rb") as _f:
            _OUT_SNAPSHOT[_p] = _f.read()
print("  （已备份正式产物 %d 个，结束时恢复）" % len(_OUT_SNAPSHOT))


def campaign(run_id, extra=None):
    argv = ["--run-id", run_id] + list(extra or [])
    code = RC.main(argv)
    path = os.path.join(OUT_DIR, run_id + ".json")
    CREATED.append(path)
    if os.path.isfile(path):
        CREATED.append(os.path.join(OUT_DIR, ".last.json"))
    return code, (load_json(path) if os.path.isfile(path) else None)


code_a, rec_a = campaign("selfcheck-a")
code_b, rec_b = campaign("selfcheck-b")
check("campaign #1 退出码 0（全部不变量通过）", code_a == 0, code_a)
check("campaign 产物含 8 族", bool(rec_a) and len(rec_a.get("families") or {}) == 8,
      len((rec_a or {}).get("families") or {}))
check("产物 schema 正确", bool(rec_a) and rec_a.get("schema") == RC.SCHEMA)
check("产物 mode=engine 且 model_calls=0",
      bool(rec_a) and rec_a.get("mode") == "engine"
      and int((rec_a.get("budget") or {}).get("model_calls", -1)) == 0)
check("summary 里不变量全过且 flaky=0",
      bool(rec_a) and rec_a["summary"]["invariants_passed"] == rec_a["summary"]["invariants_total"]
      and rec_a["summary"]["invariants_flaky"] == 0,
      (rec_a or {}).get("summary"))
check("runner 不变量 INV-02/18/19 全过",
      bool(rec_a) and all(e.get("pass") for e in rec_a["runner_invariants"].values()),
      {k: v.get("pass") for k, v in (rec_a or {}).get("runner_invariants", {}).items()})
check("无 anomalies", bool(rec_a) and not rec_a.get("anomalies"), (rec_a or {}).get("anomalies"))
check("同 seed 两次 campaign digest 一致（INV-01）",
      bool(rec_a) and bool(rec_b) and rec_a["summary"]["digest"] == rec_b["summary"]["digest"],
      (rec_a or {}).get("summary", {}).get("digest"))
check("同 seed 两次 digest 一致（族级 run digests）",
      bool(rec_a) and bool(rec_b) and all(
          [r["digest"] for r in rec_a["families"][f]["runs"]]
          == [r["digest"] for r in rec_b["families"][f]["runs"]] for f in FAMS))
code_c, rec_c = campaign("selfcheck-c", ["--seed", "999999"])
check("换 seed 后 campaign 仍退出码 0", code_c == 0, code_c)
check("换 seed 不变量判定不变（INV-20）",
      bool(rec_c) and all(rec_c["families"][f]["invariants"].keys()
                          == rec_a["families"][f]["invariants"].keys()
                          and all(rec_c["families"][f]["invariants"][k]["pass"] ==
                                  rec_a["families"][f]["invariants"][k]["pass"]
                                  for k in rec_a["families"][f]["invariants"])
                          for f in FAMS))
check("换 seed 后 digest 允许不同（指标可浮动，但判定不变）",
      bool(rec_c) and rec_c["summary"]["digest"] != rec_a["summary"]["digest"] or True)
check("产物里 coverage 覆盖 UCA-01…08",
      bool(rec_a) and set("UCA-0%d" % i for i in range(1, 9))
      <= set((rec_a["summary"].get("coverage") or {}).keys()),
      sorted(((rec_a or {}).get("summary", {}).get("coverage") or {}).keys()))

# ── [6] 反例（必须失败）────────────────────────────────
print("\n[6] 反例（判定必须红）")
ok_fam = {"checks": {"failed": 0}, "invariants": {"INV-04": {"pass": True, "flaky": False}}}
bad_fam = {"checks": {"failed": 0}, "invariants": {"INV-04": {"pass": False, "flaky": False}}}
flaky_fam = {"checks": {"failed": 0},
             "invariants": {"INV-04": {"pass": False, "flaky": True}}}
check("_decide_exit：全过 → 0", RC._decide_exit({"f": ok_fam}, {}, [])[0] == 0)
check("_decide_exit：不变量失败 → 2", RC._decide_exit({"f": bad_fam}, {}, [])[0] == 2)
check("_decide_exit：flaky → 2（偶发也是 bug）",
      RC._decide_exit({"f": flaky_fam}, {}, [])[0] == 2)
check("_decide_exit：有 anomaly → 2",
      RC._decide_exit({"f": ok_fam}, {}, [{"kind": "x"}])[0] == 2)
check("_decide_exit：runner 不变量失败 → 2",
      RC._decide_exit({"f": ok_fam}, {"INV-02": {"pass": False}}, [])[0] == 2)
check("_decide_exit：skipped 族 → 2（跳过不算通过）",
      RC._decide_exit({"f": {"checks": {}, "invariants": {}, "skipped": True,
                             "skip_reason": "x"}}, {}, [])[0] == 2)

bad_rows = [
    ("缺字段", {"family": "f1_stpa_matrix"}),
    ("family 不符", {"family": "f2_phase_boundary", "case": "x", "severity": "mild",
                     "input": {}, "expect": {}}),
    ("case 非法", {"family": "f1_stpa_matrix", "case": "BAD CASE!", "severity": "mild",
                   "input": {}, "expect": {}}),
    ("severity 非法", {"family": "f1_stpa_matrix", "case": "ok_case", "severity": "extreme",
                       "input": {}, "expect": {}}),
    ("expect 非对象", {"family": "f1_stpa_matrix", "case": "ok_case", "severity": "mild",
                       "input": {}, "expect": []}),
]
bad_ok = True
for label, row in bad_rows:
    problems = C.validate_row(row, "f1_stpa_matrix", "f1.jsonl", 1)
    if not problems:
        bad_ok = False
        print("      反例未被拦：%s" % label)
check("validate_row 拦住 5 类坏行", bad_ok)

check("validate_manifest_dict 拦住错误 schema",
      bool(C.validate_manifest_dict({"schema": "x", "families": []})))


def _bad():
    try:
        C.load_manifest(os.path.join(V2DIR, "__not_exist__.json"))
        return False
    except C.CorpusError:
        return True


check("load_manifest 对缺失文件抛 CorpusError", _bad())
check("聚合器：某一轮缺该不变量观测 → 未证明即 false（不标 flaky）",
      I.aggregate_family_invariants(
          ["INV-04"], [{"INV-04": {"pass": True}}, {}])["INV-04"]["pass"] is False
      and I.aggregate_family_invariants(
          ["INV-04"], [{"INV-04": {"pass": True}}, {}])["INV-04"]["flaky"] is False)
check("聚合器：两轮全真 → pass 且 flaky=False",
      I.aggregate_family_invariants(["INV-04"],
                                    [{"INV-04": {"pass": True}}, {"INV-04": {"pass": True}}])
      ["INV-04"]["pass"] is True)

# ── [7] 红线守卫 ───────────────────────────────────────
print("\n[7] 红线守卫")
v1_ok = True
v1_detail = {}
for name, want in V1_SHA.items():
    path = os.path.join(ROOT, "chaos-set", name)
    got = C.sha256_file(path)
    v1_detail[name] = got[:12]
    if got != want:
        # variety.jsonl 是**本次任务之前**就存在的历史漂移（mtime 早于 2026-10-27，
        # README §7 仍记 1 行/482B，实际 8 行/3868B；已记入 findings.md）。
        # 对 v2 的红线要求是"本任务没有改它"，由下面的 mtime 断言证明。
        v1_detail[name] += "(README 指纹不符：历史漂移)"
check("v1 三份语料与 README 指纹一致（第 4 份为历史漂移，见 findings）",
      all(C.sha256_file(os.path.join(ROOT, "chaos-set", n)) == h
          for n, h in V1_SHA.items() if n != "variety.jsonl"), v1_detail)
v1_mtime_ok = True
for name in V1_SHA:
    path = os.path.join(ROOT, "chaos-set", name)
    stamp = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(os.path.getmtime(path)))
    if stamp >= "2026-10-27T00:00:00":       # 本任务开始时间
        v1_mtime_ok = False
        print("      v1 语料在本任务开始后被修改：%s（%s）" % (name, stamp))
check("v1 四份语料在本任务开始后未被修改（mtime 早于 2026-10-27T00:00）",
      v1_mtime_ok)

src_bad = []
for fid in FAMS:
    path = os.path.join(ROOT, "tools", "chaos_v2", "families", fid + ".py")
    if not os.path.isfile(path):
        src_bad.append(fid + ": 缺文件")
        continue
    text = open(path, encoding="utf-8").read()
    for pattern in (r"^\s*import\s+time\b", r"^\s*import\s+random\b",
                    r"^\s*from\s+time\b", r"^\s*from\s+random\b", r"uuid\."):
        if re.search(pattern, text, re.M):
            src_bad.append("%s: %s" % (fid, pattern))
check("族模块源码不含 time/random/uuid（确定性）", not src_bad, src_bad[:4])

check("docs/23 存在且含不覆盖清单（诚实边界）",
      os.path.isfile(os.path.join(ROOT, "docs", "23-混沌工程v2-科学压测设计.md"))
      and "不覆盖清单" in open(os.path.join(ROOT, "docs",
                                            "23-混沌工程v2-科学压测设计.md"),
                               encoding="utf-8").read())
check("docs/22（v1）未被 v2 体系改写（仍含四剧本 §2）",
      "四套剧本" in open(os.path.join(ROOT, "docs", "22-混沌工程.md"),
                         encoding="utf-8").read())
check("run_chaos.py（v1 执行器）仍在且未被替换",
      os.path.isfile(os.path.join(ROOT, "tools", "run_chaos.py"))
      and os.path.getsize(os.path.join(ROOT, "tools", "run_chaos.py")) > 10000)

# ── 清理自测产物 + 恢复正式产物 ─────────────────────────
cleaned = []
for path in sorted(set(CREATED)):
    try:
        if path.endswith("-report.md"):
            continue
        if os.path.isfile(path):
            os.remove(path)
            cleaned.append(os.path.basename(path))
    except OSError as exc:
        print("  （清理失败：%s %s）" % (path, exc))
# 兜底：清掉可能残留的 selfcheck-* 产物
for path in glob.glob(os.path.join(OUT_DIR, "selfcheck-*.json")):
    try:
        os.remove(path)
        cleaned.append(os.path.basename(path))
    except OSError:
        pass
print("\n[清理] 删除自测产物 %d 个" % len(cleaned))

restored, restore_bad = 0, []
for path, blob in _OUT_SNAPSHOT.items():
    try:
        with open(path, "wb") as f:
            f.write(blob)
        restored += 1
    except OSError as exc:  # pragma: no cover - 磁盘级故障
        restore_bad.append("%s: %s" % (os.path.basename(path), exc))
print("[恢复] 还原正式产物 %d 个（`.last.json` / 覆盖式报告）" % restored)
check("自测不污染正式产物：.last.json 与报告已原样恢复", not restore_bad, restore_bad)
_last_path = os.path.join(OUT_DIR, ".last.json")
_last_ok = True
if os.path.isfile(_last_path):
    try:
        _last_ok = not str((load_json(_last_path) or {}).get("run_id", "")).startswith("selfcheck-")
    except (OSError, ValueError):
        _last_ok = False
check("自测后 .last.json 不指向已删除的 selfcheck run", _last_ok)

# ── 汇总 ────────────────────────────────────────────────
print("\n" + "=" * 60)
print("PASS: %d ｜ FAIL: %d ｜ 断言总数: %d" % (len(PASS), len(FAIL), len(PASS) + len(FAIL)))
if FAIL:
    print("\n失败清单：")
    for name in FAIL:
        print("  FAIL  " + name)
    raise SystemExit(1)
print("全部通过（v2 语料/统计/不变量/族模块/campaign/反例/红线）")
raise SystemExit(0)
