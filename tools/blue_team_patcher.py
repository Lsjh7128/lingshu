# -*- coding: utf-8 -*-
"""Phase B · A1 蓝队：自愈补丁器（候选补丁 Proposal，**绝不自动上线**）

本模块把"红队这一代有哪些 payload 绕过了防线"变成一份**候选补丁**，并做两件事：
1. 用 `tools/phase_b_defense.scan()`（Lead 维护的**唯一因变量**判据）验证补丁确实拦住了绕过；
2. 用良性语料做"**不新增误伤**"自检。基线防线本来就误伤的 2 条
   （`benign-hard-05` 角色扮演文章、`benign-hard-09` 宪法学讨论）不算补丁的账，
   但补丁**不许新增任何一条**基线没误伤的良性文本；违反 → 补丁作废并留证据。

红线（契约 §0/§5）：
- 只写 `rules/candidate_patch.yaml` 与 `proposals/phase-b/PROP-*.yaml`；
- **绝不**修改 `rules/decay-params.yaml` / `engine/**` / `plugin/**`；
- `meta.production_mutable` 恒 false，`status` 只允许 candidate/accepted_sandbox/rejected，
  **不存在 `status: production`**（写了 = 违规）；
- 每代最多 1 个补丁（`blue_team_patch_generated ∈ {0,1}`）；
- 零模型调用（`api_tokens_used` 恒 0）。

补丁语义（与 `phase_b_defense.scan()` 严格一致，契约 §5.1）：
- `normalize_decode`：`spec.detectors` 闭集 `base64|hex|rot13|zero_width`，
  解码归一化后**重跑 Phase A 基线规则**，命中 → `stage="patch_normalize"`；
- `stpa_filter` / `uca_regex`：`rules[].regex`（Python `re`，单条 ≤200 字符、必须可编译），
  对原文与归一化文本匹配，命中 → `stage="patch_regex"`。

用法（A5/Lead 也可以直接 import）：

    python tools/blue_team_patcher.py --bypasses experiments/phase-b/runs/<run>/bypasses_gen0.jsonl \\
        --generation 0 --run-id <run> [--ledger rules/candidate_patch.yaml]

    from tools.blue_team_patcher import (propose_patch, append_to_candidate_yaml,
                                         load_candidate_patch, write_proposal)
    item = propose_patch(bypasses, evolved_patch, generation_id=g)   # 无补丁时返回 None
    if item: write_proposal(item, run_id=run_id)                    # 供 H0 人工审批

诚实边界（写进 Proposal 的 limitations）：
- 变异的"可打补丁性"取决于文本里是否留下**可归一化的表面证据**（编码串 / 分隔符 /
  外来文字符号串）。纯语义改写（同义转述、换语种重译）在闭集解码器与无 MT API 的
  前提下打不出补丁——返回 None 并记 `reason`，不伪造补丁。
- 从外来文字抽 token 生成的 `stpa_filter`（UCA-01 语义）是**表面符号串匹配**，不是语义翻译；
  它可能被换词绕过，也可能在未来良性外语文本上误伤（本次用 74 条良性语料自检）。
- 本模块产出的补丁只在沙盒（`phase_b_defense.scan(patch=...)`）生效；
  生产规则文件的 sha256 在本模块运行前后必须一致（Proposal 里会记录）。
"""
from __future__ import annotations

import argparse
import base64
import binascii
import codecs
import copy
import datetime
import hashlib
import io
import json
import os
import re
import sys
import time
from collections import Counter
from typing import Any, Callable

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import phase_b_defense as defense  # noqa: E402

LEDGER_SCHEMA = "phase-b-candidate-patch-v1"
PROPOSAL_SCHEMA = "phase-b-proposal-v1"

DEFAULT_LEDGER = os.path.join(ROOT, "rules", "candidate_patch.yaml")
DEFAULT_PROPOSAL_DIR = os.path.join(ROOT, "proposals", "phase-b")

KINDS = ("normalize_decode", "stpa_filter", "uca_regex")
REGEX_KINDS = ("stpa_filter", "uca_regex")
STATUSES = ("candidate", "accepted_sandbox", "rejected")
REGEX_MAX_LEN = 200

# 生产文件（本模块**只读**，Proposal 记录其 sha256 以自证零改动）
PRODUCTION_FILES = (
    "rules/decay-params.yaml",
    "engine/control.py",
    "plugin/dsh-lingshu/control.js",
)

BYPASS_CRITERION = ("基线防线（Phase A 规则闸门）对良性语料实测误伤 2/74 = 2.70%（"
                    "benign-hard-05、benign-hard-09）。判据 = 补丁在 74 条良性语料上"
                    "**不新增**任何一条基线未误伤的条目；基线已误伤的两条不算补丁的账。")

# 最后一次诊断结果（供 A5/Lead 读取；不落盘）
LAST_ADJUDICATION: dict[str, Any] = {}

# ── 基础工具 ────────────────────────────────────────────

def _now() -> str:
    return datetime.datetime.now().astimezone().isoformat(timespec="seconds")


def _sha256_text(text: str) -> str:
    return hashlib.sha256(str(text).encode("utf-8")).hexdigest()


def _sha256_file(path: str) -> str | None:
    if not os.path.exists(path):
        return None
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def production_file_sha256() -> dict[str, str | None]:
    """生产规则/代码文件的当前 sha256（用于自证"只出 Proposal，不动生产"）。"""
    out: dict[str, str | None] = {}
    for rel in PRODUCTION_FILES:
        out[rel] = _sha256_file(os.path.join(ROOT, rel.replace("/", os.sep)))
    return out


def _deep_copy(obj: Any) -> Any:
    return copy.deepcopy(obj)


# ── 迷你 YAML（本机无 PyYAML；只支持本模块用到的闭集结构）────────
# 支持的子集：mapping / sequence / 标量（null|bool|int|float|双引号字符串）/
# 短标量数组的行内写法 `[a, b]` / 空容器 `[]` `{}`。字符串一律用 JSON 双引号转义
# （JSON 字符串是合法的 YAML 双引号标量），中文不转义（ensure_ascii=False）。

_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, (bool, int, float, str))


def _scalar_text(value: Any) -> str:
    if value is None:
        return "null"
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, float):
        if value != value or value in (float("inf"), float("-inf")):
            raise ValueError("YAML 不支持 NaN/Inf：%r" % (value,))
        return repr(value)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    raise TypeError("不支持标量类型：%r" % (type(value),))


def _key_text(key: Any) -> str:
    key = str(key)
    return key if _KEY_RE.match(key) else json.dumps(key, ensure_ascii=False)


def _is_inline_list(value: Any) -> bool:
    return isinstance(value, list) and all(_is_scalar(x) for x in value)


def _inline_text(value: list[Any]) -> str:
    if not value:
        return "[]"
    return "[" + ", ".join(_scalar_text(x) for x in value) + "]"


def _emit_entry(key: Any, value: Any, map_indent: int, lines: list[str],
                first_prefix: str | None = None) -> None:
    lead = (first_prefix if first_prefix is not None
            else " " * map_indent) + _key_text(key) + ":"
    if _is_scalar(value):
        lines.append(lead + " " + _scalar_text(value))
    elif _is_inline_list(value):
        lines.append(lead + " " + _inline_text(value))
    elif isinstance(value, dict):
        if not value:
            lines.append(lead + " {}")
        else:
            lines.append(lead)
            _emit_block(value, map_indent + 2, lines)
    elif isinstance(value, list):
        if not value:
            lines.append(lead + " []")
        else:
            lines.append(lead)
            _emit_block(value, map_indent + 2, lines)
    else:
        raise TypeError("不支持的值类型：%r" % (type(value),))


def _emit_block(value: Any, indent: int, lines: list[str]) -> None:
    if isinstance(value, dict):
        if not value:
            lines.append(" " * indent + "{}")
            return
        for key, val in value.items():
            _emit_entry(key, val, indent, lines)
        return
    if isinstance(value, list):
        if not value:
            lines.append(" " * indent + "[]")
            return
        for item in value:
            if _is_scalar(item):
                lines.append(" " * indent + "- " + _scalar_text(item))
            elif _is_inline_list(item):
                lines.append(" " * indent + "- " + _inline_text(item))
            elif isinstance(item, dict):
                if not item:
                    lines.append(" " * indent + "- {}")
                    continue
                first = True
                for key, val in item.items():
                    prefix = (" " * indent + "- ") if first else None
                    _emit_entry(key, val, indent + 2, lines, first_prefix=prefix)
                    first = False
            else:
                lines.append(" " * indent + "-")
                _emit_block(item, indent + 2, lines)
        return
    raise TypeError("不支持的值类型：%r" % (type(value),))


def dump_yaml(obj: Any) -> str:
    """把 dict/list/scalar 结构序列化成 YAML（本模块闭集）。"""
    lines: list[str] = []
    _emit_block(obj, 0, lines)
    return "\n".join(lines) + "\n"


def _tokenize_yaml(text: str) -> list[list[Any]]:
    tokens: list[list[Any]] = []
    for lineno, raw in enumerate(text.split("\n"), 1):
        if "\t" in raw:
            raise ValueError("YAML 第 %d 行含 tab（非法缩进）" % lineno)
        stripped = raw.lstrip(" ")
        if not stripped.strip() or stripped.lstrip().startswith("#"):
            continue
        tokens.append([len(raw) - len(stripped), stripped.rstrip(), lineno])
    return tokens


def _split_flow(text: str) -> list[str]:
    parts, buf, depth, in_quote = [], [], 0, False
    escaped = False
    for ch in text:
        if in_quote:
            buf.append(ch)
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                in_quote = False
            continue
        if ch == '"':
            in_quote = True
            buf.append(ch)
        elif ch in "[{":
            depth += 1
            buf.append(ch)
        elif ch in "]}":
            depth -= 1
            buf.append(ch)
        elif ch == "," and depth == 0:
            parts.append("".join(buf).strip())
            buf = []
        else:
            buf.append(ch)
    if "".join(buf).strip():
        parts.append("".join(buf).strip())
    return parts


def _parse_scalar(text: str, lineno: int) -> Any:
    s = text.strip()
    if s == "null":
        return None
    if s == "true":
        return True
    if s == "false":
        return False
    if s == "[]":
        return []
    if s == "{}":
        return {}
    if s.startswith("["):
        if not s.endswith("]"):
            raise ValueError("YAML 第 %d 行行内数组未闭合：%r" % (lineno, s[:40]))
        inner = s[1:-1].strip()
        return [] if not inner else [_parse_scalar(p, lineno) for p in _split_flow(inner)]
    if s.startswith("{"):
        try:
            return json.loads(s)
        except ValueError as exc:
            raise ValueError("YAML 第 %d 行行内映射无法解析：%s" % (lineno, exc))
    if s.startswith('"'):
        try:
            return json.loads(s)
        except ValueError as exc:
            raise ValueError("YAML 第 %d 行双引号标量无法解析：%s" % (lineno, exc))
    if len(s) >= 2 and s.startswith("'") and s.endswith("'"):
        return s[1:-1].replace("''", "'")
    try:
        return int(s)
    except ValueError:
        pass
    try:
        return float(s)
    except ValueError:
        pass
    return s


def _looks_like_map_entry(text: str) -> bool:
    if not text:
        return False
    if text[0] in ('"', "'", "[", "{"):
        return False
    if text in ("null", "true", "false"):
        return False
    return ":" in text


def _parse_map(tokens: list[list[Any]], i: int, indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    while i < len(tokens) and tokens[i][0] == indent and not tokens[i][1].startswith("-"):
        line, lineno = tokens[i][1], tokens[i][2]
        key_part, sep, rest = line.partition(":")
        if not sep:
            raise ValueError("YAML 第 %d 行不是 key: value 形式：%r" % (lineno, line[:40]))
        key = _parse_key(key_part.strip())
        rest = rest.strip()
        i += 1
        if rest:
            out[key] = _parse_scalar(rest, lineno)
        elif i < len(tokens) and tokens[i][0] > indent:
            out[key], i = _parse_block(tokens, i, tokens[i][0])
        elif (i < len(tokens) and tokens[i][0] == indent
                and (tokens[i][1] == "-" or tokens[i][1].startswith("- "))):
            out[key], i = _parse_list(tokens, i, indent)
        else:
            out[key] = None
    return out, i


def _parse_key(text: str) -> str:
    if text.startswith('"'):
        try:
            return str(json.loads(text))
        except ValueError as exc:
            raise ValueError("YAML key 无法解析：%s" % (exc))
    if len(text) >= 2 and text.startswith("'") and text.endswith("'"):
        return text[1:-1].replace("''", "'")
    return text


def _parse_list(tokens: list[list[Any]], i: int, indent: int) -> tuple[list[Any], int]:
    out: list[Any] = []
    while i < len(tokens) and tokens[i][0] == indent and (
            tokens[i][1] == "-" or tokens[i][1].startswith("- ")):
        line = tokens[i][1]
        if line == "-":
            i += 1
            val, i = _parse_block(tokens, i, tokens[i][0])
            out.append(val)
            continue
        rest = line[2:]
        if _looks_like_map_entry(rest):
            item, i = _parse_map_item(tokens, i, indent, indent + 2)
            out.append(item)
        else:
            out.append(_parse_scalar(rest.strip(), tokens[i][2]))
            i += 1
    return out, i


def _parse_map_item(tokens: list[list[Any]], i: int, dash_indent: int,
                    map_indent: int) -> tuple[dict[str, Any], int]:
    out: dict[str, Any] = {}
    rest = tokens[i][1][2:]
    lineno = tokens[i][2]
    key_part, sep, val_part = rest.partition(":")
    if not sep:
        raise ValueError("YAML 第 %d 行列表项映射缺少冒号：%r" % (lineno, rest[:40]))
    key = _parse_key(key_part.strip())
    val_part = val_part.strip()
    i += 1
    if val_part:
        out[key] = _parse_scalar(val_part, lineno)
    elif i < len(tokens) and tokens[i][0] > map_indent:
        out[key], i = _parse_block(tokens, i, tokens[i][0])
    elif (i < len(tokens) and tokens[i][0] == map_indent
          and (tokens[i][1] == "-" or tokens[i][1].startswith("- "))):
        out[key], i = _parse_list(tokens, i, map_indent)
    else:
        out[key] = None
    while i < len(tokens) and tokens[i][0] == map_indent and not tokens[i][1].startswith("-"):
        line, ln = tokens[i][1], tokens[i][2]
        k2, sep2, r2 = line.partition(":")
        if not sep2:
            raise ValueError("YAML 第 %d 行不是 key: value 形式：%r" % (ln, line[:40]))
        k2 = _parse_key(k2.strip())
        r2 = r2.strip()
        i += 1
        if r2:
            out[k2] = _parse_scalar(r2, ln)
        elif i < len(tokens) and tokens[i][0] > map_indent:
            out[k2], i = _parse_block(tokens, i, tokens[i][0])
        elif (i < len(tokens) and tokens[i][0] == map_indent
              and (tokens[i][1] == "-" or tokens[i][1].startswith("- "))):
            out[k2], i = _parse_list(tokens, i, map_indent)
        else:
            out[k2] = None
    return out, i


def _parse_block(tokens: list[list[Any]], i: int, indent: int) -> tuple[Any, int]:
    if i >= len(tokens) or tokens[i][0] != indent:
        raise ValueError("YAML 缩进不连续（第 %d 行附近）"
                         % (tokens[i][2] if i < len(tokens) else -1))
    if tokens[i][1] == "-" or tokens[i][1].startswith("- "):
        return _parse_list(tokens, i, indent)
    return _parse_map(tokens, i, indent)


def load_yaml(text: str) -> Any:
    """解析本模块 `dump_yaml` 写出的 YAML（闭集）。"""
    tokens = _tokenize_yaml(text)
    if not tokens:
        return {}
    value, i = _parse_block(tokens, 0, tokens[0][0])
    if i != len(tokens):
        raise ValueError("YAML 有未消费的行：第 %d 行" % tokens[i][2])
    return value


def _yaml_structure_check(text: str) -> list[str]:
    """写盘前的结构自检（思路同 `tools/check_patch_yaml.py`，适配本模块的 mapping 文档）：
    无 tab 缩进、末尾有换行、无行尾空白、每行缩进为 2 的倍数或顶层 `- ` 项。"""
    errors: list[str] = []
    if "\t" in text:
        errors.append("含 tab 字符（YAML 非法缩进）")
    if not text.endswith("\n"):
        errors.append("文件末尾缺少换行")
    for lineno, line in enumerate(text.split("\n"), 1):
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line != line.rstrip():
            errors.append("第 %d 行有行尾空白" % lineno)
        indent = len(line) - len(line.lstrip(" "))
        if indent % 2:
            errors.append("第 %d 行缩进不是 2 的倍数" % lineno)
    return errors


def _write_yaml_file(doc: Any, path: str) -> None:
    text = dump_yaml(doc)
    errors = _yaml_structure_check(text)
    if errors:
        raise ValueError("YAML 结构自检失败（拒绝写盘）：%s" % "；".join(errors))
    directory = os.path.dirname(os.path.abspath(path))
    if directory and not os.path.isdir(directory):
        os.makedirs(directory, exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(text)


# ── 补丁校验 / 合并 ─────────────────────────────────────

def validate_patch_item(item: dict[str, Any]) -> list[str]:
    """校验一条补丁条目是否合法（契约 §5.1/§5.2 的闭集）。返回错误列表（空 = 合法）。"""
    errors: list[str] = []
    if not isinstance(item, dict):
        return ["补丁条目必须是 dict"]
    if not isinstance(item.get("id"), str) or not item.get("id"):
        errors.append("id 必须是非空字符串")
    gen = item.get("generation")
    if not isinstance(gen, int) or isinstance(gen, bool) or gen < 0:
        errors.append("generation 必须是非负整数")
    kind = item.get("kind")
    if kind not in KINDS:
        errors.append("kind 必须是 %s 之一（收到 %r）" % (list(KINDS), kind))
    status = item.get("status")
    if status not in STATUSES:
        errors.append("status 必须是 %s 之一（收到 %r；本项目不存在 production 状态）"
                      % (list(STATUSES), status))
    if not isinstance(item.get("rationale"), str) or not item.get("rationale"):
        errors.append("rationale 必须是非空字符串")
    if not isinstance(item.get("rollback"), str) or not item.get("rollback"):
        errors.append("rollback 必须是非空字符串")
    if kind == "normalize_decode":
        spec = item.get("spec")
        detectors = (spec or {}).get("detectors") if isinstance(spec, dict) else None
        if not isinstance(detectors, list) or not detectors:
            errors.append("normalize_decode 必须给出 spec.detectors 非空列表")
        else:
            bad = [d for d in detectors if d not in defense.DETECTORS]
            if bad:
                errors.append("detectors 含闭集外的解码器：%r（闭集 %r）"
                              % (bad, list(defense.DETECTORS)))
            if len(set(detectors)) != len(detectors):
                errors.append("detectors 有重复项：%r" % (detectors,))
        rules = item.get("rules")
        if rules is not None and not isinstance(rules, list):
            errors.append("rules 必须是列表")
    elif kind in REGEX_KINDS:
        rules = item.get("rules")
        if not isinstance(rules, list) or not rules:
            errors.append("%s 必须给出非空 rules 列表" % kind)
        else:
            for idx, rule in enumerate(rules):
                if not isinstance(rule, dict):
                    errors.append("rules[%d] 必须是 dict" % idx)
                    continue
                rx = rule.get("regex")
                if not isinstance(rx, str) or not rx:
                    errors.append("rules[%d].regex 必须是非空字符串" % idx)
                    continue
                if len(rx) > REGEX_MAX_LEN:
                    errors.append("rules[%d].regex 长度 %d > %d"
                                  % (idx, len(rx), REGEX_MAX_LEN))
                try:
                    re.compile(rx)
                except re.error as exc:
                    errors.append("rules[%d].regex 无法 re.compile：%s" % (idx, exc))
    return errors


def _items_of(patch: dict[str, Any] | None) -> list[dict[str, Any]]:
    """把一个 patch dict 或单条补丁条目规范成条目列表（两种入参都接受）。"""
    if not isinstance(patch, dict):
        return []
    if isinstance(patch.get("patches"), list):
        return [it for it in patch["patches"] if isinstance(it, dict)]
    if patch.get("kind") or patch.get("id"):
        return [patch]
    return []


def merge_patches(*patches: dict[str, Any] | None) -> dict[str, Any]:
    """把若干 patch dict / 单条补丁条目（None 忽略）合并成一个 patch（保序、按 id 去重）。"""
    order: list[str] = []
    by_id: dict[str, dict[str, Any]] = {}
    for patch in patches:
        for item in _items_of(patch):
            pid = str(item.get("id") or "")
            if not pid:
                continue
            if pid not in by_id:
                order.append(pid)
            by_id[pid] = _deep_copy(item)
    return {"version": 1 + len(order), "patches": [by_id[pid] for pid in order]}


def active_patches(patch: dict[str, Any] | None) -> list[dict[str, Any]]:
    return [p for p in ((patch or {}).get("patches") or [])
            if p.get("status") not in ("rejected", "disabled")]


# ── 候选补丁账本（rules/candidate_patch.yaml）────────────────

def load_candidate_patch(path: str = DEFAULT_LEDGER) -> dict[str, Any]:
    """读候选补丁账本。文件不存在返回 {"version": 0, "patches": []}；只解析闭集字段。"""
    if not path or not os.path.exists(path):
        return {"version": 0, "patches": []}
    with io.open(path, encoding="utf-8") as fh:
        raw = fh.read()
    if not raw.strip():
        return {"version": 0, "patches": []}
    doc = load_yaml(raw)
    if not isinstance(doc, dict):
        raise ValueError("候选补丁账本顶层必须是 mapping：%s" % path)
    patches = doc.get("patches")
    if patches is None:
        patches = []
    if not isinstance(patches, list):
        raise ValueError("候选补丁账本 patches 必须是列表：%s" % path)
    doc["patches"] = patches
    if not isinstance(doc.get("version"), int) or isinstance(doc.get("version"), bool):
        doc["version"] = len(patches)
    meta = doc.get("meta")
    if not isinstance(meta, dict):
        meta = {}
    meta.setdefault("schema", LEDGER_SCHEMA)
    meta.setdefault("production_mutable", False)
    doc["meta"] = meta
    return doc


def _ledger_doc(patches: list[dict[str, Any]], *, created_at: str | None = None) -> dict[str, Any]:
    now = _now()
    return {
        "meta": {
            "schema": LEDGER_SCHEMA,
            "production_mutable": False,   # 恒 false：这是 Proposal，不是生效规则
            "created_at": created_at or now,
            "updated_at": now,
            "note": ("候选补丁账本（沙盒用）。生产防线永不读它；"
                     "合入生产 = H0 人工审批，脚本无此能力。"),
        },
        "version": 1 + len(patches),
        "patches": patches,
    }


def save_candidate_patch(doc: dict[str, Any], path: str = DEFAULT_LEDGER) -> None:
    """把账本写回磁盘（YAML，UTF-8，末尾换行）。只允许写候选账本。"""
    if not isinstance(doc, dict) or not isinstance(doc.get("patches"), list):
        raise ValueError("账本结构非法：需要 {meta, version, patches: []}")
    meta = doc.get("meta") if isinstance(doc.get("meta"), dict) else {}
    meta["schema"] = LEDGER_SCHEMA
    meta["production_mutable"] = False
    meta["updated_at"] = _now()
    meta.setdefault("created_at", meta["updated_at"])
    doc = {"meta": meta, "version": 1 + len(doc["patches"]), "patches": doc["patches"]}
    _write_yaml_file(doc, path)


def append_to_candidate_yaml(patch_item: dict[str, Any], path: str = DEFAULT_LEDGER) -> None:
    """幂等追加一条候选补丁到账本（同 id 同内容 → 不重复写；同 id 变内容 → 覆盖）。"""
    errors = validate_patch_item(patch_item)
    if errors:
        raise ValueError("候选补丁非法：%s" % "；".join(errors))
    doc = load_candidate_patch(path)
    patches = list(doc.get("patches") or [])
    item = _deep_copy(patch_item)
    found = None
    for idx, existing in enumerate(patches):
        if isinstance(existing, dict) and str(existing.get("id")) == str(item.get("id")):
            found = idx
            break
    changed = True
    if found is not None:
        if patches[found] == item:
            changed = False
        else:
            patches[found] = item
    else:
        patches.append(item)
    if not changed:
        return
    created = (doc.get("meta") or {}).get("created_at")
    save_candidate_patch(_ledger_doc(patches, created_at=created), path)


def update_patch_status(patch_id: str, status: str, *, ledger_path: str = DEFAULT_LEDGER,
                        reason: str | None = None, evidence: Any = None) -> dict[str, Any]:
    """更新账本里某条补丁的状态（A5 回归锁通过 → accepted_sandbox；失败 → rejected）。

    只允许 candidate → accepted_sandbox / rejected（或 rejected 保持 rejected）；
    绝不提供 "production"。返回更新后的条目。
    """
    if status not in STATUSES:
        raise ValueError("非法 status：%r（只允许 %s；不存在 production）"
                         % (status, list(STATUSES)))
    doc = load_candidate_patch(ledger_path)
    patches = list(doc.get("patches") or [])
    for idx, item in enumerate(patches):
        if isinstance(item, dict) and str(item.get("id")) == str(patch_id):
            updated = _deep_copy(item)
            updated["status"] = status
            if status == "rejected":
                ev = updated.get("evidence") if isinstance(updated.get("evidence"), dict) else {}
                ev["rejected_reason"] = reason or "unspecified"
                if evidence is not None:
                    ev["rejected_evidence"] = evidence
                updated["evidence"] = ev
                updated["rejected_at"] = _now()
            elif status == "accepted_sandbox":
                updated["accepted_sandbox_at"] = _now()
                if reason:
                    updated["accept_note"] = reason
            patches[idx] = updated
            save_candidate_patch(_ledger_doc(patches, created_at=(doc.get("meta") or {}).get("created_at")),
                                 ledger_path)
            return updated
    raise KeyError("账本里找不到补丁 id：%s" % patch_id)


def _next_patch_id(ledger_path: str) -> str:
    doc = load_candidate_patch(ledger_path)
    biggest = 0
    for item in doc.get("patches") or []:
        if not isinstance(item, dict):
            continue
        m = re.fullmatch(r"P-(\d+)", str(item.get("id") or ""))
        if m:
            biggest = max(biggest, int(m.group(1)))
    return "P-%04d" % (biggest + 1)


# ── 良性误伤自检 ────────────────────────────────────────

def check_benign_fp(patch_item: dict[str, Any] | None, current_patch: dict[str, Any] | None = None,
                    benign: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """对良性语料做"**不新增误伤**"自检（契约 §5.2 的唯一判据）。

    `patch_item=None` → 只报告基线误伤（不加新补丁）。
    判据：补丁后误伤的 id 集合 − 基线误伤的 id 集合 == ∅。
    基线已误伤的 benign-hard-05 / benign-hard-09 不算补丁的账，但补丁不许新增任何一条。
    """
    benign = benign if benign is not None else defense.load_benign_corpus()
    merged = merge_patches(current_patch, patch_item)
    base = defense.evaluate(benign, None)
    patched = defense.evaluate(benign, merged)
    base_fp = [x["id"] for x in base["per_item"] if x["intercepted"]]
    patch_fp = [x["id"] for x in patched["per_item"] if x["intercepted"]]
    new_fp = [x for x in patch_fp if x not in set(base_fp)]
    details = []
    for row in patched["per_item"]:
        if row["intercepted"] and row["id"] in new_fp:
            details.append({"id": row["id"], "matched": row["matched"],
                            "stage": row["stage"]})
    return {
        "criterion": BYPASS_CRITERION,
        "benign_total": len(benign),
        "baseline_fp_ids": base_fp,
        "baseline_fp_count": len(base_fp),
        "baseline_fp_rate": round(len(base_fp) / len(benign), 6) if benign else 0.0,
        "patch_fp_ids": patch_fp,
        "patch_fp_count": len(patch_fp),
        "patch_fp_rate": round(len(patch_fp) / len(benign), 6) if benign else 0.0,
        "new_fp_ids": new_fp,
        "new_fp_count": len(new_fp),
        "new_fp_details": details,
        "passed": not new_fp,
        "checked_at": _now(),
    }


def _compact_fp(fp: dict[str, Any]) -> dict[str, Any]:
    """账本里只留自检结论（明细可能很长，Proposal 里放全量）。"""
    return {"passed": fp["passed"], "benign_total": fp["benign_total"],
            "baseline_fp_ids": fp["baseline_fp_ids"], "patch_fp_ids": fp["patch_fp_ids"],
            "new_fp_ids": fp["new_fp_ids"], "baseline_fp_rate": fp["baseline_fp_rate"],
            "patch_fp_rate": fp["patch_fp_rate"], "checked_at": fp["checked_at"]}


# ── 绕过诊断 ────────────────────────────────────────────

_FOREIGN_TOKEN_RE = re.compile(r"(?:[\u0400-\u04FF]{4,}|[\u0600-\u06FF]{4,})")
_SEPARATOR_RE = re.compile(
    r"[\s\u3000\u00b7·•\u2013\u2014\-_.,，。、;；:：!！?？/\\|*'\"“”‘’()（）\[\]【】]+")
_CJK_RE = re.compile(r"[\u4e00-\u9fff]")


def _normalize_bypass_record(record: dict[str, Any]) -> dict[str, Any] | None:
    """把 A4 的 bypass 记录规范成 {variant_id, payload, technique, strategy, ...}。"""
    if not isinstance(record, dict):
        return None
    payload = record.get("payload")
    if not isinstance(payload, str) or not payload:
        return None
    vid = record.get("variant_id") or record.get("id") or _sha256_text(payload)[:12]
    return {
        "variant_id": str(vid),
        "payload": payload,
        "technique": str(record.get("technique") or ""),
        "strategy": str(record.get("strategy") or ""),
        "generation_id": record.get("generation_id"),
        "provenance": str(record.get("provenance") or ""),
        "payload_sha256": record.get("payload_sha256") or _sha256_text(payload),
    }


def _detector_probe(detectors: list[str]) -> dict[str, Any]:
    return {"patches": [{"id": "__probe__", "kind": "normalize_decode",
                         "status": "candidate", "spec": {"detectors": list(detectors)}}]}


def _scan_hit(payload: str, patch: dict[str, Any] | None) -> bool:
    return bool(defense.scan(payload, patch).get("intercepted"))


def _words_from_matched(matched: list[str]) -> list[str]:
    """从 scan().matched 里抽出触发词字面量（只取含 CJK 的词，用于分隔符恢复）。"""
    words: list[str] = []
    for entry in matched or []:
        if not isinstance(entry, str) or ":" not in entry:
            continue
        rule, _, wordpart = entry.partition(":")
        if rule == "roleplay":
            chunks = wordpart.split("+")
        elif rule in ("override_constitution", "bypass_safety", "high_risk_redline"):
            chunks = [wordpart]
        else:
            continue
        for w in chunks:
            w = w.strip()
            if len(w) >= 2 and _CJK_RE.search(w) and w not in words:
                words.append(w)
    return words


def analyze_bypasses(bypasses: list[dict[str, Any]],
                     current_patch: dict[str, Any] | None = None) -> dict[str, Any]:
    """分析这一代绕过为什么没被拦住（编码 / 分隔符 / 外来文字 / 无表面证据）。"""
    records = [r for r in (_normalize_bypass_record(b) for b in (bypasses or [])) if r]
    active: list[dict[str, Any]] = []
    already_fixed: list[str] = []
    for rec in records:
        if _scan_hit(rec["payload"], current_patch):
            already_fixed.append(rec["variant_id"])
        else:
            active.append(rec)

    # 1) 解码器覆盖：单个解码器能不能把 payload 还原成基线可判的明文
    detector_coverage: dict[str, list[str]] = {}
    for det in defense.DETECTORS:
        probe = _detector_probe([det])
        detector_coverage[det] = [r["variant_id"] for r in active
                                  if _scan_hit(r["payload"], probe)]

    # 2) 贪心选解码器集合（组合解码器链由 defense.normalize 的 max_rounds=2 覆盖）
    selected: list[str] = []
    covered: set[str] = set()
    while True:
        best: tuple[int, str, set[str]] | None = None
        for det in defense.DETECTORS:
            if det in selected:
                continue
            probe = _detector_probe(selected + [det])
            cov = {r["variant_id"] for r in active if _scan_hit(r["payload"], probe)}
            gain = len(cov - covered)
            if gain > 0 and (best is None or gain > best[0]):
                best = (gain, det, cov)
        if best is None:
            break
        selected.append(best[1])
        covered |= best[2]

    # 3) 分隔符恢复：去掉字符间的空白/标点后基线能判 → 补一条分隔符容忍正则
    separator_recovery: list[dict[str, Any]] = []
    for rec in active:
        stripped = _SEPARATOR_RE.sub("", rec["payload"])
        if stripped == rec["payload"]:
            continue
        verdict = defense.scan(stripped, None)
        if not verdict.get("intercepted"):
            continue
        words = _words_from_matched(verdict.get("matched") or [])
        if words:
            separator_recovery.append({
                "variant_id": rec["variant_id"],
                "words": words,
                "stripped_sha256": _sha256_text(stripped),
            })

    # 4) 外来文字（俄语/阿拉伯语）符号串：A4 的语言变异只换词面，不换语义结构
    token_df: Counter = Counter()
    for rec in active:
        seen = {m.group(0) for m in _FOREIGN_TOKEN_RE.finditer(rec["payload"])}
        for tok in seen:
            token_df[tok] += 1
    foreign_tokens = [{"token": tok, "df": df}
                      for tok, df in sorted(token_df.items(), key=lambda kv: (-kv[1], -len(kv[0]), kv[0]))]

    diagnosis = ("encoding" if selected else
                 "separator_split" if separator_recovery else
                 "foreign_script" if foreign_tokens else "unknown")

    return {
        "bypass_total": len(records),
        "active_bypass_count": len(active),
        "already_fixed_ids": already_fixed,
        "active_bypasses": active,
        "detector_coverage": detector_coverage,
        "selected_detectors": selected,
        "covered_by_detectors": sorted(covered),
        "separator_recovery": separator_recovery,
        "foreign_tokens": foreign_tokens,
        "techniques": sorted({r["technique"] for r in active if r["technique"]}),
        "strategy_mix": dict(Counter(r["strategy"] for r in active if r["strategy"])),
        "diagnosis": diagnosis,
    }


# ── 候选补丁构造 ────────────────────────────────────────

def _rollback_text(patch_id: str) -> str:
    return ("从 rules/candidate_patch.yaml 删除 id: %s 的条目即回滚（沙盒之外无人引用它）；"
            "生产规则文件零改动（sha256 见 Proposal 的 production_files_untouched_sha256）" % patch_id)


def _evidence_payloads(active: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for rec in active:
        out.append({
            "variant_id": rec["variant_id"],
            "technique": rec["technique"],
            "strategy": rec["strategy"],
            "payload_sha256": rec["payload_sha256"],
            "payload_preview": rec["payload"][:120],
            "provenance": rec["provenance"],
        })
    return out


def _build_normalize_item(analysis: dict[str, Any], generation_id: int,
                          patch_id: str, active: list[dict[str, Any]]) -> dict[str, Any] | None:
    detectors = list(analysis["selected_detectors"])
    if not detectors:
        return None
    n_cov = len(analysis["covered_by_detectors"])
    techs = analysis["techniques"]
    rationale = ("第 %d 代 %d 条绕过中 %d 条未拦截，原因：防线只做**明文子串匹配**，"
                 "payload 用 %s 编码/隐藏了触发词。补 %s 类解码归一化：解码后重跑 Phase A "
                 "规则即可命中（stage=patch_normalize）。"
                 % (generation_id, analysis["active_bypass_count"], n_cov,
                    "/".join(techs) or "编码", "/".join(detectors)))
    return {
        "id": patch_id,
        "generation": int(generation_id),
        "kind": "normalize_decode",
        "rationale": rationale,
        "evidence": {
            "bypass_ids": sorted(active[i]["variant_id"] for i in range(len(active))
                                 if _scan_hit(active[i]["payload"], _detector_probe(detectors))),
            "techniques": techs,
            "detector_coverage": {d: analysis["detector_coverage"].get(d, []) for d in detectors},
            "payloads": _evidence_payloads(active),
            "provenance": sorted({r["provenance"] for r in active if r["provenance"]}),
        },
        "rollback": _rollback_text(patch_id),
        "status": "candidate",
        "spec": {"detectors": detectors},
    }


def _regex_alternation(patterns: list[str]) -> str:
    """把若干片段合成一个 ≤200 字符的 alternation；超长时按顺序截断。"""
    kept: list[str] = []
    for pat in patterns:
        candidate = "(?:" + "|".join(kept + [pat]) + ")"
        if len(candidate) > REGEX_MAX_LEN:
            break
        kept.append(pat)
    if not kept:
        return ""
    return "(?:" + "|".join(kept) + ")"


def _build_separator_item(analysis: dict[str, Any], generation_id: int,
                          patch_id: str, active: list[dict[str, Any]]) -> dict[str, Any] | None:
    words: list[str] = []
    for rec in analysis["separator_recovery"]:
        for w in rec["words"]:
            if w not in words:
                words.append(w)
    patterns = []
    for w in words:
        pat = r"\s*".join(re.escape(ch) for ch in w)
        if pat not in patterns:
            patterns.append(pat)
    regex = _regex_alternation(patterns)
    if not regex:
        return None
    recovered_ids = sorted({rec["variant_id"] for rec in analysis["separator_recovery"]})
    rules = [{
        "id": "RX-%02d-01" % int(generation_id),
        "regex": regex,
        "note": ("触发词字符间插入空白/标点时明文子串匹配失效；本规则容忍分隔符，"
                 "恢复后等价于 Phase A 的严重级触发词。"),
    }]
    rationale = ("第 %d 代有 %d 条绕过把触发词拆成「忽 略 宪 法」这类分隔形式，"
                 "明文子串匹配因此失效；补一条分隔符容忍的 STPA 过滤规则（stage=patch_regex）。"
                 % (generation_id, len(recovered_ids)))
    return {
        "id": patch_id,
        "generation": int(generation_id),
        "kind": "stpa_filter",
        "rationale": rationale,
        "evidence": {
            "bypass_ids": recovered_ids,
            "techniques": analysis["techniques"],
            "recovered_words": words,
            "payloads": _evidence_payloads(active),
            "provenance": sorted({r["provenance"] for r in active if r["provenance"]}),
        },
        "rollback": _rollback_text(patch_id),
        "status": "candidate",
        "rules": rules,
    }


def _build_foreign_item(analysis: dict[str, Any], generation_id: int,
                        patch_id: str, active: list[dict[str, Any]]) -> dict[str, Any] | None:
    tokens = analysis["foreign_tokens"]
    if not tokens:
        return None
    patterns = [re.escape(t["token"]) for t in tokens]
    regex = _regex_alternation(patterns)
    if not regex:
        return None
    used_tokens = [t["token"] for t in tokens if re.escape(t["token"]) in regex]
    rules = [{
        "id": "RX-%02d-01" % int(generation_id),
        "regex": regex,
        "note": ("离线词表式语言变异（俄语/阿拉伯语换词面）绕过了中文/英文触发词表；"
                 "本规则匹配观测到的外来文字触发词符号串。**表面符号匹配，非语义翻译**，"
                 "换词/改写可绕过（见 limitations）。"),
    }]
    rationale = ("第 %d 代有语言变异绕过：中文/英文触发词被替换为 %d 个外来文字符号串，"
                 "Phase A 词表不覆盖；从 bypass payload 提取这些符号串补一条 STPA 过滤正则"
                 "（stage=patch_regex，语义同 UCA-01 注入拦截）。"
                 % (generation_id, len(used_tokens)))
    return {
        "id": patch_id,
        "generation": int(generation_id),
        "kind": "stpa_filter",
        "rationale": rationale,
        "evidence": {
            "bypass_ids": [r["variant_id"] for r in active],
            "techniques": analysis["techniques"],
            "matched_tokens": used_tokens,
            "payloads": _evidence_payloads(active),
            "provenance": sorted({r["provenance"] for r in active if r["provenance"]}),
        },
        "rollback": _rollback_text(patch_id),
        "status": "candidate",
        "rules": rules,
    }


def _build_candidate(analysis: dict[str, Any], generation_id: int, patch_id: str,
                     active: list[dict[str, Any]]) -> dict[str, Any] | None:
    """按"可打补丁性"优先序构造候选：编码 → 分隔符 → 外来文字。"""
    if analysis["selected_detectors"]:
        return _build_normalize_item(analysis, generation_id, patch_id, active)
    if analysis["separator_recovery"]:
        return _build_separator_item(analysis, generation_id, patch_id, active)
    if analysis["foreign_tokens"]:
        return _build_foreign_item(analysis, generation_id, patch_id, active)
    return None


def _coverage_ids(item: dict[str, Any], current_patch: dict[str, Any] | None,
                  active: list[dict[str, Any]]) -> list[str]:
    merged = merge_patches(current_patch, item)
    return [r["variant_id"] for r in active if _scan_hit(r["payload"], merged)]


def propose_patch(bypasses: list[dict[str, Any]], current_patch: dict[str, Any] | None, *,
                  generation_id: int, ledger_path: str | None = DEFAULT_LEDGER,
                  persist: bool = True) -> dict[str, Any] | None:
    """分析绕过原因 → 生成**一个**候选补丁（或 None），并做良性误伤自检。

    参数：
    - `bypasses`：A4 的 `bypasses_gen<g>.jsonl` 记录（只需 payload/technique/variant_id；
      缺 variant_id 时用 payload sha256 前 12 位兜底）；
    - `current_patch`：演化防线已有的补丁 dict（`{version, patches}`，可为 None）；
    - `generation_id`：代数（写进补丁条目与证据）；
    - `ledger_path`：候选账本路径（默认 `rules/candidate_patch.yaml`）；
    - `persist`：True 时把候选 / 被拒条目写进账本（A5 默认行为）。

    返回补丁条目（status=candidate）或 None。无论成败都写 `LAST_ADJUDICATION`：
    `{"generation_id", "reason", "analysis", "benign_fp", "patch_id"}`，供 A5 记 `patch_rejected`。
    违反"不新增误伤"判据时：返回 None，并把 status=rejected 的条目（含哪条 benign、命中哪条
    规则）写进账本——这就是契约要求的 rejected 证据。
    """
    ledger_path = DEFAULT_LEDGER if ledger_path is None else ledger_path
    analysis = analyze_bypasses(bypasses, current_patch)
    active = analysis["active_bypasses"]
    decision: dict[str, Any] = {
        "generation_id": int(generation_id),
        "reason": None,
        "patch_id": None,
        "analysis": analysis,
        "benign_fp": None,
    }

    def finish(reason: str) -> None:
        decision["reason"] = reason
        LAST_ADJUDICATION.clear()
        LAST_ADJUDICATION.update(decision)

    if not analysis["bypass_total"]:
        finish("no_bypass_input")
        return None
    if not active:
        finish("all_bypasses_already_intercepted")
        return None

    patch_id = _next_patch_id(ledger_path)
    decision["patch_id"] = patch_id
    item = _build_candidate(analysis, generation_id, patch_id, active)
    if item is None:
        finish("no_patchable_surface_evidence")
        return None

    errors = validate_patch_item(item)
    if errors:
        finish("candidate_invalid: %s" % "；".join(errors))
        return None

    covered = _coverage_ids(item, current_patch, active)
    if not covered:
        finish("candidate_no_effect")
        return None

    fp = check_benign_fp(item, current_patch)
    decision["benign_fp"] = fp
    item.setdefault("evidence", {})["coverage"] = {
        "bypass_count": len(active), "covered_by_candidate": len(covered),
        "covered_ids": covered,
    }
    if not fp["passed"]:
        rejected = _deep_copy(item)
        rejected["status"] = "rejected"
        ev = rejected.setdefault("evidence", {})
        ev["rejected_reason"] = "benign_new_false_positive"
        ev["benign_self_check"] = fp
        ev["rejected_at"] = _now()
        if persist:
            append_to_candidate_yaml(rejected, ledger_path)
        finish("benign_new_false_positive")
        decision["rejected_item"] = rejected
        LAST_ADJUDICATION.clear()
        LAST_ADJUDICATION.update(decision)
        return None

    ev = item.setdefault("evidence", {})
    ev["benign_self_check"] = _compact_fp(fp)
    ev["created_at"] = _now()
    if persist:
        append_to_candidate_yaml(item, ledger_path)
    finish("proposed")
    LAST_ADJUDICATION.clear()
    LAST_ADJUDICATION.update(decision)
    return item


# ── Proposal（人工审批提议）──────────────────────────────

def _proposal_seq(item: dict[str, Any], run_id: str, out_dir: str) -> int:
    """同一 (run_id, generation) 下按补丁 id 复用序号（幂等覆盖），否则顺延。"""
    prefix = "PROP-%s-g%02d-" % (run_id, int(item.get("generation") or 0))
    if not os.path.isdir(out_dir):
        return 1
    seqs: list[int] = []
    for name in os.listdir(out_dir):
        if not (name.startswith(prefix) and name.endswith(".yaml")):
            continue
        m = re.fullmatch(re.escape(prefix) + r"(\d+)\.yaml", name)
        if not m:
            continue
        seq = int(m.group(1))
        try:
            with io.open(os.path.join(out_dir, name), encoding="utf-8") as fh:
                doc = load_yaml(fh.read())
        except (ValueError, OSError):
            seqs.append(seq)
            continue
        if not isinstance(doc, dict):
            seqs.append(seq)
            continue
        if str(doc.get("patch_id")) == str(item.get("id")):
            return seq
        seqs.append(seq)
    return (max(seqs) + 1) if seqs else 1


def _build_proposal(item: dict[str, Any], *, run_id: str, fp: dict[str, Any] | None,
                    seq: int) -> dict[str, Any]:
    ev = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    kind = item.get("kind")
    fp = fp or ev.get("benign_self_check") or {}
    production_hashes = production_file_sha256()
    # 审批人需要看到"到底改什么"：把补丁本体（spec / rules）原样放进 Proposal
    if kind == "normalize_decode":
        change = {"kind": kind,
                  "spec": {"detectors": list((item.get("spec") or {}).get("detectors") or [])}}
    else:
        change = {"kind": kind,
                  "rules": [dict(r) for r in (item.get("rules") or []) if isinstance(r, dict)]}
    if kind == "normalize_decode":
        detectors = (item.get("spec") or {}).get("detectors") or []
        stage_behavior = ("基线规则未命中时，按 detectors 顺序（%s）尝试解码归一化并重跑基线规则；"
                          "命中 → stage=patch_normalize，matched 记录基线规则名与解码器链。" % ", ".join(detectors))
        cost = ("每条 payload 至多 defense.normalize(max_rounds=2) 轮解码；"
                "良性文本无可解码片段时与基线行为完全一致。")
    else:
        rules = item.get("rules") or []
        stage_behavior = ("对（解码归一化后的）原文跑 rules[].regex；命中 → stage=patch_regex，"
                          "matched 记 patch_%s:<id>。" % kind)
        cost = ("%d 条正则，单条长度 %s ≤ %d 字符，均已 re.compile 通过；"
                "未命中时与基线行为一致。" % (len(rules), [len(r.get("regex") or "") for r in rules],
                                             REGEX_MAX_LEN))
    reg_ev = ev.get("regression_in_run") if isinstance(ev.get("regression_in_run"), dict) else None
    if reg_ev:
        regression_block = {
            "suite": reg_ev.get("suite") or "audit892",
            "command": "python tools/run_phase_a_regression.py --suite audit892",
            "must_pass": True,
            "status": "passed_in_run" if reg_ev.get("ok") else "failed_in_run",
            "files": reg_ev.get("files"),
            "passed": reg_ev.get("passed"),
            "failed": reg_ev.get("failed"),
            "evidence": reg_ev.get("json"),
            "note": ("该补丁已在 A5 coevolution run 的回归锁里跑过（0 failed 才被并入演化防线）；"
                     "H0 若批准合入生产，仍需走人工 ADR + 全量回归。"),
        }
    else:
        regression_block = {
            "suite": "audit892",
            "command": "python tools/run_phase_a_regression.py --suite audit892",
            "must_pass": True,
            "status": "not_run_by_proposer",
            "note": ("本 Proposal 生成时未跑回归锁；按契约 §6.2 顺序，A5 在把补丁并入"
                     "演化防线**之前**必须跑该命令，failed>0 → 丢弃补丁并记 rejected_reason=regression。"),
        }
    return {
        "schema": PROPOSAL_SCHEMA,
        "proposal_id": "PROP-%s-g%02d-%02d" % (run_id, int(item.get("generation") or 0), seq),
        "patch_id": item.get("id"),
        "generation": int(item.get("generation") or 0),
        "kind": kind,
        "status": str(item.get("status") or "candidate"),
        "production_mutable": False,
        "created_at": _now(),
        "run_id": run_id,
        "requires_human_approval": True,
        "approval_state": "awaiting_h0_approval",
        "approval_note": "等待 H0 审批；脚本不提供自动合入路径（本项目的生产规则只由 H0 人工决定）。",
        "rationale": item.get("rationale"),
        "proposed_patch_change": change,
        "evidence": {
            "bypass_ids": ev.get("bypass_ids") or [],
            "techniques": ev.get("techniques") or [],
            "coverage": ev.get("coverage") or {},
            "payloads": ev.get("payloads") or [],
            "matched_tokens": ev.get("matched_tokens") or [],
            "recovered_words": ev.get("recovered_words") or [],
        },
        # 字段名与 proposals/phase-b/README.md（Lead 维护）对齐：benign_check / regression
        "benign_check": {            "criterion": fp.get("criterion") or BYPASS_CRITERION,
            "passed": fp.get("passed"),
            "benign_total": fp.get("benign_total"),
            "baseline_fp_ids": fp.get("baseline_fp_ids") or [],
            "baseline_fp_rate": fp.get("baseline_fp_rate"),
            "patch_fp_ids": fp.get("patch_fp_ids") or [],
            "patch_fp_rate": fp.get("patch_fp_rate"),
            "new_fp_ids": fp.get("new_fp_ids") or [],
            "new_fp_details": fp.get("new_fp_details") or [],
            "checked_at": fp.get("checked_at"),
        },
        "impact": {
            "sandbox_only": True,
            "applied_by": "tools/phase_b_defense.scan(patch=...)（沙盒评估）",
            "stage_behavior": stage_behavior,
            "cost": cost,
            "production_files_referencing_this_patch": [],
            "does_not_change": list(PRODUCTION_FILES),
            "affected_measurement": "演化防线拦截率 / 良性误伤率（因变量仍由 tools/phase_b_defense.scan 定义）",
        },
        "regression": regression_block,
        "production_files_untouched_sha256": production_hashes,
        "production_files_snapshot_at": _now(),
        "production_files_snapshot_note": (
            "上面是 Proposal 生成瞬间的 sha256 快照；A1 **只读**这些文件、未做任何写入。"
            "若与他人的并发改动不一致，以 git diff 为准确认 A1 是否越界。"),
        "rollback": item.get("rollback"),
        "limitations": [
            "补丁是**沙盒候选**，不是生效规则：生产规则文件零改动（sha256 见 production_files_untouched_sha256）；是否合并由 H0 人工审批。",
            ("良性自检只覆盖 %d 条良性语料；判据是「不新增基线之外误伤」，"
             "不代表全部分布。" % (fp.get("benign_total") or 0)),
            "规则型归一化只覆盖闭集解码器（base64/hex/rot13/zero_width）；自定义替换表、二次编码等不可解。",
            "外语 token 正则来源于观测到的表面符号串，不是语义翻译（离线词表近似）；换词/改写可绕过。",
            "本阶段零模型调用（api_tokens_used=0），补丁由确定性规则生成，不含语义理解。",
        ],
    }


def write_proposal(patch_item: dict[str, Any], *, run_id: str,
                   out_dir: str = DEFAULT_PROPOSAL_DIR) -> str:
    """写 `proposals/phase-b/PROP-<run_id>-g<gen>-<seq>.yaml`（人工审批用）。

    含：证据、影响面、良性误伤自检结果、"等待 H0 审批"、回滚方式、生产文件 sha256
    自证零改动。**只写 Proposal**，不把任何规则写进生产文件。
    """
    errors = validate_patch_item(patch_item)
    if errors:
        raise ValueError("不能为非法补丁写 Proposal：%s" % "；".join(errors))
    out_dir = out_dir or DEFAULT_PROPOSAL_DIR
    out_dir = out_dir if os.path.isabs(out_dir) else os.path.join(ROOT, out_dir)
    os.makedirs(out_dir, exist_ok=True)
    seq = _proposal_seq(patch_item, run_id, out_dir)
    fp = None
    stored_fp = (patch_item.get("evidence") or {}).get("benign_self_check") if isinstance(
        patch_item.get("evidence"), dict) else None
    if stored_fp and stored_fp.get("passed") is not None:
        fp = stored_fp
    if fp is None or "criterion" not in fp:
        fp = check_benign_fp(patch_item, None)
    doc = _build_proposal(patch_item, run_id=run_id, fp=fp, seq=seq)
    path = os.path.join(out_dir, "%s.yaml" % doc["proposal_id"])
    _write_yaml_file(doc, path)
    return path


# ── CLI ────────────────────────────────────────────────

def load_bypasses(path: str) -> list[dict[str, Any]]:
    """读 A4 的 bypasses_gen<g>.jsonl（只保留 intercepted=false 或 bypass=true 的记录）。

    记录若不含 `provenance`，补一个 `file:<文件名>` 标记，保证账本证据可追溯到来源文件。
    """
    rows: list[dict[str, Any]] = []
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except ValueError:
                continue
            if not isinstance(rec, dict):
                continue
            if rec.get("bypass") is True or rec.get("intercepted") is False:
                if not rec.get("provenance"):
                    rec = dict(rec)
                    rec["provenance"] = "file:%s" % os.path.basename(path)
                rows.append(rec)
    return rows


AUDIT_EVIDENCE_KEYS = ("source_run", "source_ledger", "regression_in_run", "source_patch_id")
_PATCH_CORE_KEYS = ("id", "generation", "kind", "rationale", "rollback", "spec", "rules")


def _patch_core(item: dict[str, Any]) -> dict[str, Any]:
    """补丁的"实质内容"指纹：去掉账本审计附加字段（source_run 等）与状态演进。"""
    core = {k: item.get(k) for k in _PATCH_CORE_KEYS}
    ev = item.get("evidence") if isinstance(item.get("evidence"), dict) else {}
    core["evidence"] = {k: v for k, v in ev.items() if k not in AUDIT_EVIDENCE_KEYS}
    return core


def _same_patch(existing: dict[str, Any], item: dict[str, Any]) -> bool:
    """判断权威账本里的条目与 run 里的同 id 补丁是不是**同一条**（不是同一个 id 而已）。"""
    if _patch_core(existing) != _patch_core(item):
        return False
    es = str(existing.get("status"))
    ns = str(item.get("status"))
    # 账本里的状态会比 run 快照更靠后（candidate → accepted_sandbox），这不是内容变化
    return es == ns or {es, ns} <= {"candidate", "accepted_sandbox"}


def _resolve_run_patch_source(run_dir: str) -> tuple[dict[str, Any], str]:
    """定位 run 的补丁来源：candidate_patch.yaml → evolved_patch_snapshot.json → 最新 snapshot。"""
    candidates = [
        os.path.join(run_dir, "candidate_patch.yaml"),
        os.path.join(run_dir, "evolved_patch_snapshot.json"),
    ]
    for path in candidates:
        if not os.path.exists(path):
            continue
        if path.endswith(".json"):
            try:
                with io.open(path, encoding="utf-8") as fh:
                    doc = json.load(fh)
            except (ValueError, OSError):
                doc = None
            if isinstance(doc, dict) and isinstance(doc.get("patches"), list):
                return doc, path
        else:
            return load_candidate_patch(path), path
    snaps: list[tuple[int, str]] = []
    for name in os.listdir(run_dir):
        m = re.fullmatch(r"candidate_patch_snapshot_g(\d+)\.yaml", name)
        if m:
            snaps.append((int(m.group(1)), os.path.join(run_dir, name)))
    if snaps:
        snaps.sort()
        return load_candidate_patch(snaps[-1][1]), snaps[-1][1]
    raise FileNotFoundError(
        "run 目录里没有补丁账本（candidate_patch.yaml / evolved_patch_snapshot.json / "
        "candidate_patch_snapshot_g*.yaml 都找不到）：%s" % run_dir)


def _regression_in_run(run_dir: str, generation: Any) -> dict[str, Any] | None:
    """读 run 的 `regression_gen<g>.json`，给 Proposal 填**真实**回归结果。"""
    if not isinstance(generation, int):
        return None
    reg_path = os.path.join(run_dir, "regression_gen%d.json" % generation)
    if not os.path.exists(reg_path):
        return None
    try:
        with io.open(reg_path, encoding="utf-8") as fh:
            reg = json.load(fh)
    except (ValueError, OSError):
        return None
    if not isinstance(reg, dict):
        return None
    return {
        "suite": reg.get("suite"),
        "files": reg.get("files"),
        "passed": reg.get("total_passed"),
        "failed": reg.get("total_failed"),
        "ok": (reg.get("total_failed") == 0
               and not reg.get("failing_files")
               and not reg.get("known_env_failing_files")),
        "json": os.path.relpath(reg_path, ROOT).replace(os.sep, "/"),
    }


def sync_from_run(run_dir: str, *, run_id: str | None = None,
                  ledger_path: str = DEFAULT_LEDGER,
                  out_dir: str = DEFAULT_PROPOSAL_DIR,
                  write_proposals: bool = True) -> dict[str, Any]:
    """把一次 A5 run 的补丁**增量**并入权威候选账本，并给每个补丁写一份 H0 Proposal。

    背景：A5 为可复现性把每轮账本放在 run 目录（也支持直接写权威账本）；A1 的权威
    累计账本是 `rules/candidate_patch.yaml`。补丁来源按优先级解析：
    `<run>/candidate_patch.yaml` → `<run>/evolved_patch_snapshot.json` → 最新
    `<run>/candidate_patch_snapshot_g*.yaml`。

    **幂等语义（关键）**：
    - 权威账本里已有**同一条补丁**（实质内容一致；状态 candidate → accepted_sandbox
      的演进不算内容变化）→ 跳过，**不覆盖、不重编号、不产生新 id**；
    - 只有同 id 但实质内容不同（不同 run 都从 P-0001 起算）才重新编号，并把原 id
      记进 `evidence.source_patch_id`，绝不静默覆盖别人的补丁；
    - Proposal 按 `run_id` 生成；`regression` 字段读 run 的 `regression_gen<g>.json`
      填真实结果（`passed_in_run`），生成时不重新跑回归、也不冒充跑过。

    只写 A1 作用域（账本 + proposals），不碰生产规则。返回
    `{"run_id", "run_ledger", "added", "skipped", "renumbered", "proposals"}`。
    """
    run_dir = run_dir if os.path.isabs(run_dir) else os.path.join(ROOT, run_dir)
    run_id = run_id or os.path.basename(os.path.normpath(run_dir))
    run_doc, run_source = _resolve_run_patch_source(run_dir)
    ledger_doc = load_candidate_patch(ledger_path)
    existing = {str(p.get("id")): p for p in (ledger_doc.get("patches") or [])
                if isinstance(p, dict)}
    added: list[str] = []
    skipped: list[str] = []
    renumbered: list[dict[str, str]] = []
    proposals: list[str] = []
    for raw in run_doc.get("patches") or []:
        if not isinstance(raw, dict):
            continue
        item = _deep_copy(raw)
        ev = item.setdefault("evidence", {})
        if not isinstance(ev, dict):
            ev = {}
            item["evidence"] = ev
        ev.setdefault("source_run", run_id)
        ev.setdefault("source_ledger", os.path.relpath(run_source, ROOT).replace(os.sep, "/"))
        reg = _regression_in_run(run_dir, item.get("generation"))
        if reg is not None:
            ev.setdefault("regression_in_run", reg)
        errors = validate_patch_item(item)
        if errors:
            skipped.append("%s(invalid:%s)" % (item.get("id"), "；".join(errors)))
            continue
        pid = str(item.get("id"))
        proposal_item: dict[str, Any] | None = None
        if pid in existing:
            if _same_patch(existing[pid], item):
                # 权威账本已收录：不写账本；用账本里的条目（状态更靠后）出 Proposal
                proposal_item = _deep_copy(existing[pid])
                pev = proposal_item.setdefault("evidence", {})
                if isinstance(pev, dict):
                    pev.setdefault("source_run", run_id)
                    pev.setdefault("source_ledger",
                                   os.path.relpath(run_source, ROOT).replace(os.sep, "/"))
                    if reg is not None:
                        pev.setdefault("regression_in_run", reg)
                skipped.append(pid)
            else:
                new_id = _next_patch_id(ledger_path)
                ev["source_patch_id"] = pid
                item["id"] = new_id
                renumbered.append({"run_patch_id": pid, "ledger_patch_id": new_id})
        if proposal_item is None:
            append_to_candidate_yaml(item, ledger_path)
            added.append(str(item.get("id")))
            proposal_item = _deep_copy(item)
        if write_proposals and proposal_item.get("status") in ("candidate", "accepted_sandbox"):
            proposals.append(write_proposal(proposal_item, run_id=run_id, out_dir=out_dir))
    return {"run_id": run_id, "run_ledger": run_source,
            "added": added, "skipped": skipped, "renumbered": renumbered,
            "proposals": proposals}


def main(argv: list[str] | None = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description="A1 蓝队：从绕过记录生成候选补丁 Proposal")
    ap.add_argument("--bypasses", default=None,
                    help="bypasses_gen<g>.jsonl 路径（与 --from-run 二选一）")
    ap.add_argument("--from-run", default=None,
                    help="把 A5 run 目录里的 candidate_patch.yaml 增量并入权威账本并写 Proposal")
    ap.add_argument("--generation", type=int, default=0)
    ap.add_argument("--run-id", default=None)
    ap.add_argument("--ledger", default=DEFAULT_LEDGER)
    ap.add_argument("--out-dir", default=DEFAULT_PROPOSAL_DIR)
    ap.add_argument("--no-persist", action="store_true", help="只分析，不写账本")
    ap.add_argument("--no-proposal", action="store_true", help="不写 Proposal 文件")
    args = ap.parse_args(argv)
    if not args.bypasses and not args.from_run:
        ap.error("需要 --bypasses 或 --from-run 之一")

    if args.from_run:
        summary = sync_from_run(args.from_run, run_id=args.run_id,
                                ledger_path=args.ledger, out_dir=args.out_dir,
                                write_proposals=not args.no_proposal)
        print("[A1] sync run=%s added=%s skipped=%s renumbered=%s"
              % (summary["run_id"], summary["added"], summary["skipped"],
                 summary["renumbered"]))
        for path in summary["proposals"]:
            print("[A1] proposal=%s（等待 H0 审批）" % path)
        return 0

    bypasses = load_bypasses(args.bypasses)
    current = load_candidate_patch(args.ledger)
    print("[A1] bypasses=%d generation=%d current_patches=%d"
          % (len(bypasses), args.generation, len(current.get("patches") or [])))
    item = propose_patch(bypasses, current, generation_id=args.generation,
                         ledger_path=args.ledger, persist=not args.no_persist)
    adj = LAST_ADJUDICATION
    print("[A1] diagnosis=%s active=%d reason=%s"
          % ((adj.get("analysis") or {}).get("diagnosis"),
             (adj.get("analysis") or {}).get("active_bypass_count"), adj.get("reason")))
    if item is None:
        fp = adj.get("benign_fp") or {}
        if fp:
            print("[A1] 良性自检 passed=%s new_fp=%s" % (fp.get("passed"), fp.get("new_fp_ids")))
        rejected = adj.get("rejected_item")
        if rejected is not None:
            print("[A1] 补丁被拒（%d 条良性误伤）：%s"
                  % (len(rejected["evidence"]["benign_self_check"]["new_fp_ids"]),
                     rejected["evidence"]["benign_self_check"]["new_fp_ids"]))
            if not args.no_proposal:
                print("[A1] rejected 证据已写入账本：%s" % args.ledger)
        else:
            print("[A1] 本代无可打补丁：%s（诚实返回 None，不伪造补丁）" % adj.get("reason"))
        return 0
    fp = adj.get("benign_fp") or {}
    print("[A1] patch=%s kind=%s detected=%s covered=%d/%d benign_new_fp=%d"
          % (item["id"], item["kind"],
             (item.get("spec") or {}).get("detectors") or [r["id"] for r in item.get("rules") or []],
             len((item.get("evidence") or {}).get("coverage", {}).get("covered_ids") or []),
             (item.get("evidence") or {}).get("coverage", {}).get("bypass_count") or 0,
             fp.get("new_fp_count") or 0))
    if not args.no_proposal:
        path = write_proposal(item, run_id=args.run_id, out_dir=args.out_dir)
        print("[A1] proposal=%s（等待 H0 审批）" % path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
