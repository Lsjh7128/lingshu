# -*- coding: utf-8 -*-
"""tools/chaos_v2/corpus.py —— 语料加载 / 行封套校验 / SHA-256 指纹（docs/23 §5 / §10）

职责边界（和 runner 分工）：

- **结构错误 = 硬失败**（`CorpusError` → 退出码 1）：manifest 缺失、schema 不符、
  `*.jsonl` 里 JSON 坏行、行封套缺字段、`family` 与文件不一致、`case` 重复或
  不合正则、`severity` 不在闭集、UTF-8 带 BOM、CRLF、行内出现 time/random/uuid 类键。
  理由：语料是**预注册**刺激集（§2.1），结构与指纹就是判定的一部分；
  跑一份被改过的语料等于改判定（红线 6），所以宁可拒绝运行，也不"挑着跑"。

- **指纹不符 / 文件缺失 = 按族 skipped**（不抛异常）：返回
  `status="hash_mismatch"` / `"missing"`，由 runner 标
  `skipped=true` + `skip_reason="corpus_hash_mismatch"` 并写 `anomalies[]`。
  理由：这是运行环境/交付时序问题，不是语料语义错误；红线 3 要求"跑不成写 skipped"，
  因此不整轮崩、也不静默继续。

行封套（docs/23 §5 硬约束）：

```json
{"family":"f1_stpa_matrix","case":"uca01_words_ignore_constitution","severity":"severe",
 "input":{...},"expect":{...}}
```

运行时字段一律英文；本模块的注释/错误信息用中文（与项目风格一致）。
"""
from __future__ import annotations

import hashlib
import json
import os
import re
from dataclasses import dataclass, field
from typing import Any

SCHEMA_ID = "lingshu-chaos-campaign/2"
SEVERITIES = ("mild", "moderate", "severe", "control")
STRESS_CLASSES = ("adversarial", "resource", "temporal", "storage", "ordering", "control")
CASE_RE = re.compile(r"^[a-z0-9_]{3,64}$")
FAMILY_ID_RE = re.compile(r"^[a-z0-9_]{3,64}$")
REQUIRED_ROW_KEYS = ("family", "case", "severity", "input", "expect")
# 非确定性键（docs/23 §5：行内不含 time/random/uuid；用精确名而不是子串，
# 避免把 "timeoutMs" 这类合法配置键误判，同时仍能抓到"塞了个 now 时间戳"的走样）。
FORBIDDEN_ROW_KEYS = frozenset({"time", "random", "uuid", "now", "rng", "rand", "timestamp"})


class CorpusError(Exception):
    """语料结构性错误（runner 转成退出码 1，不写产物、不跑族）。"""


# ── 通用小工具 ──────────────────────────────────────────
def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _is_hex64(text) -> bool:
    return isinstance(text, str) and bool(re.fullmatch(r"[0-9a-f]{64}", text))


def _is_int(value) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


# ── manifest ────────────────────────────────────────────
@dataclass
class Manifest:
    """已校验的 v2 manifest（字段名见 docs/23 §10）。"""

    path: str
    sha256: str
    raw: dict
    families: list
    seed_base: int
    repeats: int
    base_ts: float
    corpus_sha256: dict
    schema: str = SCHEMA_ID

    @property
    def dir(self) -> str:
        """语料目录：manifest 所在目录（默认 chaos-set/v2）。"""
        return os.path.dirname(os.path.abspath(self.path))

    def family_ids(self) -> list:
        return [str(f["id"]) for f in self.families]

    def family(self, family_id: str):
        for fam in self.families:
            if str(fam.get("id")) == str(family_id):
                return fam
        return None


def validate_manifest_dict(raw, path: str = "") -> list:
    """只做 schema 检查，返回问题列表（空 = 通过）。不发 IO。"""
    where = os.path.basename(path) if path else "manifest"
    problems = []

    def bad(msg: str) -> None:
        problems.append("%s: %s" % (where, msg))

    if not isinstance(raw, dict):
        bad("顶层必须是 JSON object")
        return problems
    if raw.get("schema") != SCHEMA_ID:
        bad("schema 必须是 %r（实际 %r）" % (SCHEMA_ID, raw.get("schema")))
    if not _is_int(raw.get("seed_base")):
        bad("seed_base 必须是整数（实际 %r）" % (raw.get("seed_base"),))
    if not _is_int(raw.get("repeats")) or int(raw.get("repeats", 0)) < 1:
        bad("repeats 必须是 >=1 的整数（实际 %r）" % (raw.get("repeats"),))
    if not _is_number(raw.get("base_ts")) or float(raw.get("base_ts") or 0.0) <= 0:
        bad("base_ts 必须是正数（实际 %r）" % (raw.get("base_ts"),))
    families = raw.get("families")
    if not isinstance(families, list) or not families:
        bad("families 必须是非空数组")
        families = []
    corpus_sha = raw.get("corpus_sha256")
    if not isinstance(corpus_sha, dict):
        bad("corpus_sha256 必须是 {filename: sha256} 对象")
        corpus_sha = {}

    seen_ids, seen_corpus, seen_case_files = set(), set(), set()
    for idx, fam in enumerate(families):
        loc = "families[%d]" % idx
        if not isinstance(fam, dict):
            bad("%s 必须是 object" % loc)
            continue
        fid = fam.get("id")
        if not isinstance(fid, str) or not FAMILY_ID_RE.match(fid):
            bad("%s.id 必须是 ^[a-z0-9_]{3,64}$（实际 %r）" % (loc, fid))
            fid = str(fid or "")
        if fid in seen_ids:
            bad("%s.id 重复：%r" % (loc, fid))
        seen_ids.add(fid)
        for key in ("title", "module", "corpus", "stress_class", "mechanism"):
            if not isinstance(fam.get(key), str) or not str(fam.get(key)).strip():
                bad("%s.%s 必须是非空字符串" % (loc, key))
        corpus_name = str(fam.get("corpus") or "")
        if corpus_name:
            if not corpus_name.endswith(".jsonl"):
                bad("%s.corpus 必须是 *.jsonl（实际 %r）" % (loc, corpus_name))
            if corpus_name in seen_corpus:
                bad("%s.corpus 重复：%r" % (loc, corpus_name))
            seen_corpus.add(corpus_name)
            stem = os.path.splitext(os.path.basename(corpus_name))[0]
            if fid and stem != fid:
                bad("%s.corpus 文件名主干必须等于族 id（%r vs %r，§5 family 与文件一一对应）"
                    % (loc, stem, fid))
            if corpus_name not in corpus_sha:
                bad("corpus_sha256 缺少 %r 的指纹" % corpus_name)
            elif not _is_hex64(corpus_sha.get(corpus_name)):
                bad("corpus_sha256[%r] 必须是 64 位小写 hex" % corpus_name)
        if str(fam.get("stress_class") or "") not in STRESS_CLASSES:
            bad("%s.stress_class 必须是 %s 之一（实际 %r）"
                % (loc, " / ".join(STRESS_CLASSES), fam.get("stress_class")))
        ucas = fam.get("ucas", [])
        if not isinstance(ucas, list) or any(not isinstance(u, str) for u in ucas):
            bad("%s.ucas 必须是字符串数组" % loc)
        invs = fam.get("invariants", [])
        if not isinstance(invs, list) or any(not isinstance(u, str) for u in invs):
            bad("%s.invariants 必须是字符串数组" % loc)
        for key in ("repeats",):
            if key in fam and (not _is_int(fam[key]) or int(fam[key]) < 1):
                bad("%s.%s 必须是 >=1 的整数" % (loc, key))
        for key in ("severity_levels", "non_claims"):
            if key in fam and not isinstance(fam[key], list):
                bad("%s.%s 必须是数组" % (loc, key))
    # corpus_sha256 里不该有孤儿条目（多算/少算都说明指纹与族清单不同步）
    for name in corpus_sha:
        if name not in seen_corpus:
            bad("corpus_sha256 含孤儿条目 %r（families 里没有对应 corpus）" % name)
    return problems


def load_manifest(path: str) -> Manifest:
    """读 + 校验 manifest；任何结构问题抛 CorpusError（退出码 1）。"""
    path = os.path.abspath(path)
    if not os.path.exists(path):
        raise CorpusError(
            "manifest 缺失：%s\n"
            "  语料是先定后跑的预注册刺激集（docs/23 §2.1），没有 manifest 无法判定。\n"
            "  先生成语料：python tools\\gen_chaos_v2.py   （--check 只校验不写盘）" % path)
    try:
        with open(path, "rb") as f:
            raw_bytes = f.read()
    except OSError as exc:
        raise CorpusError("manifest 读取失败：%s（%s）" % (path, exc)) from exc
    try:
        text = raw_bytes.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise CorpusError("manifest 不是 UTF-8：%s（%s）" % (path, exc)) from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise CorpusError("manifest JSON 解析失败：%s（%s）" % (path, exc)) from exc
    problems = validate_manifest_dict(raw, path)
    if problems:
        shown = problems[:40]
        more = "" if len(problems) <= 40 else "\n  …另有 %d 项" % (len(problems) - 40)
        raise CorpusError("manifest schema 不符（%d 项）：\n- %s%s"
                          % (len(problems), "\n- ".join(shown), more))
    return Manifest(
        path=path,
        sha256=sha256_bytes(raw_bytes),
        raw=raw,
        families=list(raw.get("families") or []),
        seed_base=int(raw["seed_base"]),
        repeats=int(raw["repeats"]),
        base_ts=float(raw["base_ts"]),
        corpus_sha256=dict(raw.get("corpus_sha256") or {}),
        schema=str(raw.get("schema")),
    )


# ── 行封套 ──────────────────────────────────────────────
def _find_forbidden_keys(obj, found=None) -> list:
    """递归找非确定性键（time/random/uuid…），返回命中的键名（保序去重）。"""
    if found is None:
        found = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(key, str) and key.strip().lower() in FORBIDDEN_ROW_KEYS:
                if key not in found:
                    found.append(key)
            _find_forbidden_keys(value, found)
    elif isinstance(obj, list):
        for item in obj:
            _find_forbidden_keys(item, found)
    return found


def validate_row(row, family_id: str, corpus_name: str, lineno: int) -> list:
    """单行行封套校验（docs/23 §5），返回问题列表（空 = 通过）。"""
    where = "%s:%d" % (corpus_name, lineno)
    problems = []
    if not isinstance(row, dict):
        return ["%s 不是 JSON object" % where]
    for key in REQUIRED_ROW_KEYS:
        if key not in row:
            problems.append("%s 缺行封套字段 %r" % (where, key))
    if str(row.get("family")) != family_id:
        problems.append("%s family=%r 与文件族 id=%r 不一致"
                        % (where, row.get("family"), family_id))
    case = row.get("case")
    if not isinstance(case, str) or not CASE_RE.match(case):
        problems.append("%s case 必须是 ^[a-z0-9_]{3,64}$（实际 %r）" % (where, case))
    severity = row.get("severity")
    if severity not in SEVERITIES:
        problems.append("%s severity 必须是 %s 之一（实际 %r）"
                        % (where, " / ".join(SEVERITIES), severity))
    for key in ("input", "expect"):
        if key in row and not isinstance(row[key], dict):
            problems.append("%s %r 必须是 object" % (where, key))
    forbidden = _find_forbidden_keys(row)
    if forbidden:
        problems.append("%s 含非确定性键 %r（行内不允许 time/random/uuid，§5）"
                        % (where, forbidden))
    return problems


# ── 单族语料 ────────────────────────────────────────────
@dataclass
class FamilyCorpus:
    family_id: str
    path: str
    expected_sha256: str
    actual_sha256: str
    status: str                      # ok | hash_mismatch | missing
    rows: list = field(default_factory=list)
    bytes_len: int = 0
    line_count: int = 0
    severity_counts: dict = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == "ok"

    def as_record(self) -> dict:
        return {
            "file": os.path.basename(self.path),
            "sha256": self.actual_sha256,
            "expected_sha256": self.expected_sha256,
            "rows": line_count_rows(self),
            "bytes": self.bytes_len,
            "hash_verified": self.ok,
            "status": self.status,
            "severities": dict(self.severity_counts),
        }


def line_count_rows(fc: FamilyCorpus) -> int:
    return len(fc.rows)


def _read_family(manifest: Manifest, fam: dict, problems: list, cases: dict) -> FamilyCorpus:
    fid = str(fam["id"])
    corpus_name = str(fam["corpus"])
    path = os.path.join(manifest.dir, corpus_name)
    expected = str(manifest.corpus_sha256.get(corpus_name) or "")
    if not os.path.exists(path):
        return FamilyCorpus(family_id=fid, path=path, expected_sha256=expected,
                            actual_sha256="", status="missing")
    try:
        with open(path, "rb") as f:
            data = f.read()
    except OSError as exc:
        problems.append("%s 读取失败（%s）" % (corpus_name, exc))
        return FamilyCorpus(family_id=fid, path=path, expected_sha256=expected,
                            actual_sha256="", status="missing")
    actual = sha256_bytes(data)
    if data.startswith(b"\xef\xbb\xbf"):
        problems.append("%s 带 UTF-8 BOM（§5 要求无 BOM）" % corpus_name)
    if b"\r" in data:
        problems.append("%s 含 CR（§5 要求 LF 换行）" % corpus_name)
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise CorpusError("%s 不是 UTF-8（%s）" % (corpus_name, exc)) from exc

    rows, severities = [], {}
    for lineno, line in enumerate(text.split("\n"), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append("%s:%d JSON 解析失败（%s）" % (corpus_name, lineno, exc))
            continue
        if not isinstance(row, dict):
            problems.append("%s:%d 不是 JSON object" % (corpus_name, lineno))
            continue
        row_problems = validate_row(row, fid, corpus_name, lineno)
        problems.extend(row_problems)
        case = row.get("case")
        if isinstance(case, str):
            if case in cases:
                problems.append("%s:%d case 全域重复：%r（首次出现在 %s）"
                                % (corpus_name, lineno, case, cases[case]))
            else:
                cases[case] = "%s:%d" % (corpus_name, lineno)
        sev = row.get("severity")
        if isinstance(sev, str):
            severities[sev] = severities.get(sev, 0) + 1
        rows.append(row)
    status = "ok" if (expected and actual == expected) else "hash_mismatch"
    return FamilyCorpus(family_id=fid, path=path, expected_sha256=expected,
                        actual_sha256=actual, status=status, rows=rows,
                        bytes_len=len(data), line_count=len(rows),
                        severity_counts=severities)


def load_corpus(manifest: Manifest, family_ids=None) -> dict:
    """加载（指定族的）语料并做全量结构校验；结构问题抛 CorpusError。

    `family_ids=None` 表示全部族；指定子集时只校验子集（`--family` 只跑指定族，
    不该被其它族的语料问题挡住）。
    返回 `{family_id: FamilyCorpus}`；哈希不符/文件缺失**不抛异常**，由 runner 处理。
    """
    wanted = set(family_ids) if family_ids is not None else None
    families = [f for f in manifest.families if wanted is None or str(f.get("id")) in wanted]
    problems, cases, out = [], {}, {}
    for fam in families:
        fc = _read_family(manifest, fam, problems, cases)
        out[fc.family_id] = fc
    if problems:
        shown = problems[:60]
        more = "" if len(problems) <= 60 else "\n  …另有 %d 项" % (len(problems) - 60)
        raise CorpusError("语料行封套校验失败（%d 项）：\n- %s%s"
                          % (len(problems), "\n- ".join(shown), more))
    return out


# ── --list 的内置设计目录（manifest 尚不存在时的兜底；不推测 UCA 归属）──
FALLBACK_FAMILIES = [
    {"id": "f1_stpa_matrix", "title": "F1 STPA 决策表（全扫 + 良性近邻）",
     "stress_class": "adversarial", "mechanism": "preflight_audit",
     "ucas": ["UCA-01", "UCA-02", "UCA-03", "UCA-04", "UCA-05", "UCA-06", "UCA-07", "UCA-08"]},
    {"id": "f2_phase_boundary", "title": "F2 相变边界扫描（0.50→1.00 + 邻域）",
     "stress_class": "resource", "mechanism": "PhaseShiftMonitor", "ucas": ["UCA-07"]},
    {"id": "f3_integral_windup", "title": "F3 积分抗饱和与恢复",
     "stress_class": "temporal", "mechanism": "IntegralBaseline", "ucas": ["UCA-08"]},
    {"id": "f4_recall_adversarial", "title": "F4 召回对抗与预算诚实",
     "stress_class": "adversarial", "mechanism": "memory.recall", "ucas": []},
    {"id": "f5_assembly_pressure", "title": "F5 合成矩阵与预算压力",
     "stress_class": "resource", "mechanism": "compose", "ucas": []},
    {"id": "f6_storage_faults", "title": "F6 存储层故障注入（Node 参考实现）",
     "stress_class": "storage", "mechanism": "memorywriter", "ucas": []},
    {"id": "f7_steady_state", "title": "F7 稳态基线与良性对照",
     "stress_class": "control", "mechanism": "compose", "ucas": []},
    {"id": "f8_metamorphic", "title": "F8 变形关系（MR1–MR6）",
     "stress_class": "ordering", "mechanism": "compose", "ucas": []},
]


__all__ = [
    "SCHEMA_ID", "SEVERITIES", "STRESS_CLASSES", "CASE_RE", "CorpusError",
    "Manifest", "FamilyCorpus", "validate_manifest_dict", "load_manifest",
    "validate_row", "load_corpus", "sha256_bytes", "sha256_file", "FALLBACK_FAMILIES",
]
