#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""chaos-set/v2/validate.py —— 混沌工程 v2 语料的**独立**校验器（A4 / task-1）

为什么要有它
------------
`chaos-set/v2/*.jsonl` 是 runner（`tools/run_chaos_campaign.py`）直接消费的**输入**。
语料一旦被手改（少一行、case 重复、severity 写错、换行变成 CRLF、BOM 混入、哈希对不上），
夜间压测的数字就会静默失真——而且看起来"跑通了"。
本脚本把"语料应当满足什么"变成可机检的断言：**不通过就 exit 1**。

它**不依赖** `tools/gen_chaos_v2.py`（独立实现，防"生成器自己校验自己"），
也**不 import** engine/plugin（零副作用，只读语料与 manifest）。

检查项（逐项打印；失败带行号）
------------------------------
1. manifest 结构：schema / seed_base / repeats / base_ts / families 顺序 f1..f8 /
   每族元字段齐全 / stress_class 闭集 / corpus_sha256 恰好覆盖 8 份语料（无孤儿、无缺失）；
2. 每份 jsonl：存在 / UTF-8 无 BOM / 无 `\\r`（LF）/ 末尾有换行 / 每行合法 JSON /
   封套键恰好 `{family,case,severity,input,expect}` / `family` 与文件名一致 /
   `case` 全域唯一且匹配 `^[a-z0-9_]{3,64}$` / `severity ∈ {mild,moderate,severe,control}` /
   `input`/`expect` 是 object / 行内任意层级无 `time/random/uuid/now/rng/rand/timestamp` 键名 /
   SHA-256 与 `manifest.corpus_sha256` 一致 / 行数 ≥ manifest 预注册下限；
3. `README.md` 的指纹表与真实文件行数/字节/SHA-256 一致（防 README 被手改）。

用法
----
    python chaos-set/v2/validate.py            # 全量
    python chaos-set/v2/validate.py --quiet
    python chaos-set/v2/validate.py --dir <其他语料目录>

退出码：0 = 全部通过；1 = 有失败项（明细打印在 stderr）。
兼容 Python 3.8+（不用 `X | Y` 注解、不用 match）。
"""
import hashlib
import json
import os
import re
import sys

try:  # Windows 控制台默认 cp936，中文/符号输出先兜底
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    sys.stderr.reconfigure(encoding="utf-8", errors="replace")
except Exception:  # pragma: no cover
    pass

HERE = os.path.dirname(os.path.abspath(__file__))

SCHEMA = "lingshu-chaos-campaign/2"
SEVERITIES = {"mild", "moderate", "severe", "control"}
STRESS_CLASSES = {"adversarial", "resource", "temporal", "storage", "ordering", "control"}
ENVELOPE_KEYS = {"family", "case", "severity", "input", "expect"}
CASE_RE = re.compile(r"^[a-z0-9_]{3,64}$")
FORBIDDEN_KEYS = {"time", "random", "uuid", "now", "rng", "rand", "timestamp"}
FAMILY_ORDER = [
    "f1_stpa_matrix", "f2_phase_boundary", "f3_integral_windup",
    "f4_recall_adversarial", "f5_assembly_pressure", "f6_storage_faults",
    "f7_steady_state", "f8_metamorphic",
]
FAMILY_META_KEYS = [
    "id", "title", "stress_class", "mechanism", "ucas", "corpus", "module",
    "repeats", "severity_levels", "hypothesis", "invariants", "window",
    "verdict_kind", "non_claims",
]
ROW_COUNT_MIN = {
    "f1_stpa_matrix": 28, "f2_phase_boundary": 26, "f3_integral_windup": 10,
    "f4_recall_adversarial": 14, "f5_assembly_pressure": 18, "f6_storage_faults": 10,
    "f7_steady_state": 8, "f8_metamorphic": 14,
}


class Report(object):
    def __init__(self):
        self.failures = []          # [(family, line_no|None, message)]
        self.passes = []            # [(family, rows, bytes, sha)]

    def fail(self, family, line_no, message):
        self.failures.append((family, line_no, message))

    def ok(self):
        return not self.failures


def _sha256(data):
    return hashlib.sha256(data).hexdigest()


def _walk_forbidden_keys(obj, path=""):
    bad = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            if str(k) in FORBIDDEN_KEYS:
                bad.append(path + "." + str(k))
            bad.extend(_walk_forbidden_keys(v, path + "." + str(k)))
    elif isinstance(obj, list):
        for i, v in enumerate(obj):
            bad.extend(_walk_forbidden_keys(v, "%s[%d]" % (path, i)))
    return bad


def _load_manifest(corpus_dir, report):
    path = os.path.join(corpus_dir, "manifest.json")
    if not os.path.exists(path):
        report.fail("manifest.json", None, "文件不存在：%s" % path)
        return None
    try:
        with open(path, "rb") as fh:
            raw = fh.read()
    except OSError as exc:
        report.fail("manifest.json", None, "读取失败：%s" % exc)
        return None
    if raw.startswith(b"\xef\xbb\xbf"):
        report.fail("manifest.json", None, "含 UTF-8 BOM")
    if b"\r" in raw:
        report.fail("manifest.json", None, "含 CR（必须 LF）")
    try:
        manifest = json.loads(raw.decode("utf-8").lstrip("\ufeff"))
    except (ValueError, UnicodeDecodeError) as exc:
        report.fail("manifest.json", None, "JSON 解析失败：%s" % exc)
        return None
    if not isinstance(manifest, dict):
        report.fail("manifest.json", None, "顶层必须是 object")
        return None

    # ── 顶层字段
    if manifest.get("schema") != SCHEMA:
        report.fail("manifest.json", None,
                    "schema 应为 %r，实得 %r" % (SCHEMA, manifest.get("schema")))
    for key, want in (("seed_base", 20261027), ("repeats", 3)):
        if manifest.get(key) != want:
            report.fail("manifest.json", None,
                        "%s 应为 %r，实得 %r" % (key, want, manifest.get(key)))
    if not isinstance(manifest.get("base_ts"), (int, float)):
        report.fail("manifest.json", None, "base_ts 必须是数值")

    families = manifest.get("families")
    if not isinstance(families, list):
        report.fail("manifest.json", None, "families 必须是 list（顺序 f1..f8）")
        return manifest
    ids = [fam.get("id") for fam in families if isinstance(fam, dict)]
    if ids != FAMILY_ORDER:
        report.fail("manifest.json", None,
                    "families 顺序/集合应为 %r，实得 %r" % (FAMILY_ORDER, ids))

    corpus_names = []
    for fam in families:
        if not isinstance(fam, dict):
            report.fail("manifest.json", None, "families 元素必须是 object")
            continue
        fid = str(fam.get("id"))
        for key in FAMILY_META_KEYS:
            if key not in fam:
                report.fail(fid, None, "manifest.families 缺少字段 %r" % key)
        if fam.get("stress_class") not in STRESS_CLASSES:
            report.fail(fid, None, "stress_class=%r 不在五轴闭集"
                        % (fam.get("stress_class"),))
        if fam.get("repeats") != 3:
            report.fail(fid, None, "repeats 应为 3，实得 %r" % (fam.get("repeats"),))
        levels = fam.get("severity_levels")
        if not isinstance(levels, list) or not levels or \
                not set(map(str, levels)).issubset(SEVERITIES):
            report.fail(fid, None, "severity_levels=%r 非法" % (levels,))
        for key in ("hypothesis", "invariants", "non_claims", "ucas"):
            if not isinstance(fam.get(key), list) and key != "hypothesis":
                report.fail(fid, None, "%s 必须是数组" % key)
        if not isinstance(fam.get("hypothesis"), str) or not fam.get("hypothesis"):
            report.fail(fid, None, "hypothesis 必须是非空字符串")
        corpus = fam.get("corpus")
        if not isinstance(corpus, str) or not corpus.endswith(".jsonl"):
            report.fail(fid, None, "corpus 必须是非空 .jsonl 文件名")
            continue
        if os.path.splitext(os.path.basename(corpus))[0] != fid:
            report.fail(fid, None, "corpus 文件名 %r 与族 id 不一致" % corpus)
        corpus_names.append(corpus)

    sha_map = manifest.get("corpus_sha256")
    if not isinstance(sha_map, dict):
        report.fail("manifest.json", None, "corpus_sha256 必须是 object")
    else:
        missing = [n for n in corpus_names if n not in sha_map]
        orphan = [n for n in sha_map if n not in corpus_names]
        if missing:
            report.fail("manifest.json", None, "corpus_sha256 缺少条目：%r" % missing)
        if orphan:
            report.fail("manifest.json", None, "corpus_sha256 含孤儿条目：%r" % orphan)
    return manifest


def _check_family_file(corpus_dir, fam, manifest, seen_cases, report, quiet):
    fid = str(fam.get("id"))
    corpus = str(fam.get("corpus") or "")
    path = os.path.join(corpus_dir, corpus)
    if not os.path.exists(path):
        report.fail(fid, None, "文件不存在：%s" % path)
        return None
    with open(path, "rb") as fh:
        raw = fh.read()

    # ── 字节层
    if raw.startswith(b"\xef\xbb\xbf"):
        report.fail(fid, 1, "含 UTF-8 BOM（必须是 UTF-8 无 BOM）")
    if b"\r" in raw:
        line_no = raw[:raw.index(b"\r")].count(b"\n") + 1
        report.fail(fid, line_no, "含 CR 字符（必须 LF 换行）")
    if not raw.endswith(b"\n"):
        report.fail(fid, len(raw.split(b"\n")), "文件末尾缺少 LF（末行未以换行结束）")
    try:
        text = raw.decode("utf-8").lstrip("\ufeff")
    except UnicodeDecodeError as exc:
        report.fail(fid, None, "不是合法 UTF-8：%s" % exc)
        return None

    lines = text.split("\n")
    if lines and lines[-1] == "":
        lines = lines[:-1]
    if not lines:
        report.fail(fid, None, "空文件")
        return None

    # ── 逐行封套
    family_errors = 0
    for idx, line in enumerate(lines, start=1):
        if line.strip() == "":
            report.fail(fid, idx, "空行（每行必须是一个 JSON 对象）")
            family_errors += 1
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            report.fail(fid, idx, "JSON 解析失败：%s" % exc)
            family_errors += 1
            continue
        if not isinstance(row, dict):
            report.fail(fid, idx, "行必须是 JSON object，实得 %s" % type(row).__name__)
            family_errors += 1
            continue
        keys = set(row.keys())
        if keys != ENVELOPE_KEYS:
            report.fail(fid, idx, "封套键应恰好为 %r，实得 %r"
                        % (sorted(ENVELOPE_KEYS), sorted(keys)))
            family_errors += 1
            continue
        if row.get("family") != fid:
            report.fail(fid, idx, "family=%r 与文件名 id 不一致" % (row.get("family"),))
            family_errors += 1
        case = row.get("case")
        if not isinstance(case, str) or not CASE_RE.match(case):
            report.fail(fid, idx, "case=%r 不匹配 ^[a-z0-9_]{3,64}$" % (case,))
            family_errors += 1
        elif case in seen_cases:
            report.fail(fid, idx, "case 全域重复：%r（首次出现于 %s:%s）"
                        % (case, seen_cases[case][0], seen_cases[case][1]))
            family_errors += 1
        else:
            seen_cases[case] = (fid, idx)
        if row.get("severity") not in SEVERITIES:
            report.fail(fid, idx, "severity=%r 不在 %r"
                        % (row.get("severity"), sorted(SEVERITIES)))
            family_errors += 1
        if not isinstance(row.get("input"), dict):
            report.fail(fid, idx, "input 必须是 object")
            family_errors += 1
        if not isinstance(row.get("expect"), dict):
            report.fail(fid, idx, "expect 必须是 object")
            family_errors += 1
        bad = _walk_forbidden_keys(row)
        if bad:
            report.fail(fid, idx, "出现确定性禁用键名：%r" % (bad,))
            family_errors += 1

    # ── 行数下限
    if len(lines) < ROW_COUNT_MIN.get(fid, 0):
        report.fail(fid, None, "行数 %d < 预注册下限 %d"
                    % (len(lines), ROW_COUNT_MIN.get(fid, 0)))
        family_errors += 1

    # ── 哈希
    sha = _sha256(raw)
    want_sha = None
    if isinstance(manifest.get("corpus_sha256"), dict):
        want_sha = manifest["corpus_sha256"].get(corpus)
    if want_sha is not None and sha != str(want_sha).lower():
        report.fail(fid, None, "SHA-256 与 manifest 不一致：文件 %s / manifest %s"
                    % (sha, want_sha))
        family_errors += 1

    if family_errors == 0:
        report.passes.append((fid, len(lines), len(raw), sha))
    elif not quiet:
        pass  # 明细已在上面逐条打印
    return {"family": fid, "rows": len(lines), "bytes": len(raw), "sha256": sha,
            "ok": family_errors == 0}


def _check_readme(corpus_dir, results, report):
    """README 指纹表必须与真实文件一致（防 README 被手改）。"""
    path = os.path.join(corpus_dir, "README.md")
    if not os.path.exists(path):
        report.fail("README.md", None, "文件不存在")
        return
    with open(path, "rb") as fh:
        raw = fh.read()
    if raw.startswith(b"\xef\xbb\xbf"):
        report.fail("README.md", None, "含 UTF-8 BOM")
    text = raw.decode("utf-8", errors="replace")
    by_family = {r["family"]: r for r in results if r}
    seen = set()
    pattern = re.compile(
        r"^\|\s*`(f[1-8]_[a-z0-9_]+\.jsonl)`\s*\|\s*(\d+)\s*\|\s*(\d+)\s*\|\s*`([0-9a-f]{64})`\s*\|$")
    for line in text.splitlines():
        m = pattern.match(line.strip())
        if not m:
            continue
        name, rows, nbytes, sha = m.group(1), int(m.group(2)), int(m.group(3)), m.group(4)
        fid = os.path.splitext(name)[0]
        res = by_family.get(fid)
        if res is None:
            report.fail("README.md", None, "指纹表出现未知族 %r" % fid)
            continue
        seen.add(fid)
        if rows != res["rows"]:
            report.fail(fid, None, "README 指纹表行数 %d ≠ 实际 %d" % (rows, res["rows"]))
        if nbytes != res["bytes"]:
            report.fail(fid, None, "README 指纹表字节 %d ≠ 实际 %d"
                        % (nbytes, res["bytes"]))
        if sha != res["sha256"]:
            report.fail(fid, None, "README 指纹表 SHA-256 与文件不一致")
    missing = set(by_family) - seen
    if missing:
        report.fail("README.md", None, "指纹表缺少族：%r" % sorted(missing))


def validate(corpus_dir, quiet=False):
    report = Report()
    manifest = _load_manifest(corpus_dir, report)
    results = []
    seen_cases = {}
    if manifest is not None:
        for fam in manifest.get("families") or []:
            if not isinstance(fam, dict):
                continue
            res = _check_family_file(corpus_dir, fam, manifest, seen_cases,
                                     report, quiet)
            results.append(res)
            if res is not None and not quiet:
                status = "PASS" if res["ok"] else "FAIL"
                print("[%s] %-24s rows=%-3d bytes=%-6d sha256=%s"
                      % (status, res["family"], res["rows"], res["bytes"],
                         res["sha256"][:16]))
        if results:
            _check_readme(corpus_dir, results, report)
    return report, results


def main(argv):
    corpus_dir = HERE
    quiet = False
    args = list(argv)
    while args:
        arg = args.pop(0)
        if arg == "--quiet":
            quiet = True
        elif arg == "--dir":
            if not args:
                print("--dir 需要一个目录参数", file=sys.stderr)
                return 1
            corpus_dir = args.pop(0)
        elif arg in ("-h", "--help"):
            print(__doc__)
            return 0
        else:
            print("未知参数：%r（支持 --dir <目录> / --quiet）" % arg, file=sys.stderr)
            return 1
    if not os.path.isdir(corpus_dir):
        print("语料目录不存在：%s" % corpus_dir, file=sys.stderr)
        return 1

    report, results = validate(corpus_dir, quiet=quiet)
    if report.failures:
        print("", file=sys.stderr)
        print("=== 失败项（共 %d 条）===" % len(report.failures), file=sys.stderr)
        for family, line_no, message in report.failures:
            where = family if line_no is None else "%s:%d" % (family, line_no)
            print("[FAIL] %-22s %s" % (where, message), file=sys.stderr)
        print("", file=sys.stderr)
        print("validate FAIL：%d 份语料通过、%d 条失败。语料是生成物，"
              "请改 tools/gen_chaos_v2.py 后重生成。" % (len(report.passes), len(report.failures)),
              file=sys.stderr)
        return 1

    total_rows = sum(r["rows"] for r in results if r)
    print("")
    print("validate PASS：%d 份语料、共 %d 行；封套/唯一性/severity/LF/无 BOM/"
          "manifest 哈希/README 指纹表全部一致。" % (len(results), total_rows))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
