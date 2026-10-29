# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · v0.2 布局 / 作用域记忆语义源（施工单 v2 §3 §4 §6）

D4：本模块是 v0.2 语义的**唯一来源**；`plugin/dsh-lingshu/memory-paths.js` 是逐函数
镜像，parity 用例见 `tests/test_v02_parity.py`（Python 结果 == Node 结果）。

范围（只做施工单列出的，不扩展）：
- D6/D7：`layout.json`（layout-0.2）+ canonicalPath → sandboxId（sha256 前 8 位）
- D1/D2：`resolve_memory_paths()` 六键（S1）
- D8：墓碑追加 + 读取折叠（不改历史行、不物理删行）
- D9：local over global 合并裁决 + 配额
- §4.3：scope 路由（global / local / pending）
- §4.4：隔离校验（路径唯一出口 + 行内标签二次校验）
- D10：目录锁（mkdir 原子 + PID + 5s 超时 + 30s 陈旧）
- §4.6：热重载判据（mtime + size + 行数，200ms 节流）
- S4：`check_uca01_text()` 纯函数
- S5：审计 kind 常量表（skeleton 三事件只登记不产生）
- S3：`lingshu:skeleton` 段常量（order 105）

刻意不做：skeleton 生成器、临时人格体、第三层共享记忆、多租户。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
import unicodedata
import uuid
from typing import Any, Callable, Iterable

# ── 0. 版本与常量 ─────────────────────────────────────────

LAYOUT_SCHEMA = "layout-0.2"
SANDBOX_INDEX_SCHEMA = "sandbox-index-0.1"
SANDBOX_SCHEMA = "sandbox-0.1"
PERSONA_STATE_SCHEMA = "persona-state-0.1"
STATE_SCHEMA = "state-0.1"
MAPPING_SCHEMA = "mapping-0.1"

SANDBOX_ID_RE = re.compile(r"^[0-9a-f]{8}$")
PERSONA_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")

# S3：skeleton 注入段（宪法 100 之后、事实 110 之前；与 blocked 同 order 时靠空串保证零输出）。
SKELETON_SECTION = {"name": "lingshu:skeleton", "order": 105}

# S5：本期新增 kind（会真实产生）+ skeleton 预留（常量存在、不产生）。
AUDIT_KINDS_V02 = (
    "panel_forget",
    "panel_restore",
    "panel_purge",
    "migration_apply",
    "sandbox_mapped",
)
AUDIT_KINDS_RESERVED = (
    "skeleton_generated",
    "skeleton_rejected",
    "skeleton_rebuilt",
)

# §4.2 配额：local 保底 1 条；global 占注入预算 ≤ 30%。
GLOBAL_CAP_RATIO = 0.30
LOCAL_FLOOR = 1
DEFAULT_TOKEN_BUDGET = 900
DEFAULT_TOP_K = 3
PER_ITEM_OVERHEAD = 8
CHARS_PER_TOKEN = 3


# ── 1. 规范化与 sandboxId（D7 / §4.1）────────────────────

def canonicalize_path(path: Any, *, realpath: Callable[[str], str] | None = None,
                      windows: bool | None = None) -> str:
    """canonicalPath → 唯一字符串。Windows 下大小写不敏感、解 junction、NFC。

    顺序（§4.1）：反斜杠归一 → 折叠重复分隔符 → 去尾分隔符（根除外）→
    realpath 解 junction → Windows 小写 → Unicode NFC。

    为什么小写在 realpath 之后**再**做一次：`realpath` 在 Windows 上返回磁盘上的
    真实大小写（`D:\\Novel`），若只用输入小写，`d:\\novel` 与 `D:\\Novel` 会得到
    不同 id —— 这正是 §4.1 五组等价输入要防的。小写是幂等的，重复一次没有副作用。
    """
    if path is None:
        raise ValueError("canonicalize_path: path required")
    s = str(path)
    s = s.replace("\\", "/")
    s = re.sub(r"/{2,}", "/", s)
    # UNC 前缀（//server/share）在折叠后被破坏，这里还原一次（防御性；本期不做网络盘）
    if raw_unc(s):
        s = "/" + s
    if len(s) > 1 and s.endswith("/") and not _is_root(s):
        s = s.rstrip("/")
    rp = realpath if realpath is not None else os.path.realpath
    try:
        s = rp(s)
    except (OSError, ValueError):
        pass
    s = s.replace("\\", "/")
    s = re.sub(r"/{2,}", "/", s)
    if len(s) > 1 and s.endswith("/") and not _is_root(s):
        s = s.rstrip("/")
    s = unicodedata.normalize("NFC", s)
    if os.name == "nt" if windows is None else windows:
        s = s.lower()
    return s


def raw_unc(s: str) -> bool:
    return bool(re.match(r"^//[^/]", s))


def _is_root(s: str) -> bool:
    return bool(re.fullmatch(r"(?:[A-Za-z]:/|/)", s))


def sandbox_id(canonical_path: str) -> str:
    """D7：sha256(canonicalPath) 前 8 位 hex。"""
    return hashlib.sha256(str(canonical_path).encode("utf-8")).hexdigest()[:8]


def sandbox_id_for_path(path: Any, **kwargs: Any) -> str:
    return sandbox_id(canonicalize_path(path, **kwargs))


# ── 2. 六键路径（D1/D2/D17 / S1）──────────────────────────

def _check_sandbox_id(sid: str) -> str:
    s = str(sid or "")
    if not SANDBOX_ID_RE.match(s):
        raise ValueError("resolve_memory_paths: invalid sandbox_id (fail-closed)")
    return s


def _check_persona_id(pid: str) -> str:
    s = str(pid or "")
    if not PERSONA_ID_RE.match(s) or s in (".", ".."):
        raise ValueError("resolve_memory_paths: invalid persona_id (fail-closed)")
    return s


def resolve_memory_paths(data_root: Any, sandbox_id: str, persona_id: str) -> dict[str, str]:
    """唯一路径出口（§4.4.6）。返回**恰好**六个键，全部在数据根之下。

    S1 验收键：globalMemory / localMemory / pending / skeleton / state / personaState
    """
    root = os.path.abspath(os.fspath(data_root))
    sid = _check_sandbox_id(sandbox_id)
    pid = _check_persona_id(persona_id)
    persona_dir = os.path.join(root, "personas", pid)
    sandbox_persona_dir = os.path.join(root, "sandboxes", sid, pid)
    paths = {
        "globalMemory": os.path.join(persona_dir, "global_memory.jsonl"),
        "localMemory": os.path.join(sandbox_persona_dir, "local_memory.jsonl"),
        "pending": os.path.join(persona_dir, "global_pending.jsonl"),
        "skeleton": os.path.join(sandbox_persona_dir, "skeleton.md"),
        "state": os.path.join(sandbox_persona_dir, "state.json"),
        "personaState": os.path.join(persona_dir, "persona_state.json"),
    }
    for key, p in paths.items():
        if not _under_root(root, p):
            raise ValueError(f"resolve_memory_paths: {key} escapes data root")
    return paths


def _under_root(root: str, p: str) -> bool:
    r = os.path.normcase(os.path.abspath(root)).rstrip(os.sep)
    q = os.path.normcase(os.path.abspath(p))
    return q == r or q.startswith(r + os.sep)


# ── 3. 布局识别与 layout.json（D6）────────────────────────

def read_json(path: Any) -> dict[str, Any] | None:
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            doc = json.load(f)
        return doc if isinstance(doc, dict) else None
    except (OSError, ValueError):
        return None


def write_json(path: Any, value: dict[str, Any]) -> dict[str, Any]:
    try:
        parent = os.path.dirname(os.path.abspath(os.fspath(path)))
        os.makedirs(parent, exist_ok=True)
        tmp = f"{path}.tmp-{os.getpid()}-{uuid.uuid4().hex[:8]}"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(value, f, ensure_ascii=False, indent=2)
            f.write("\n")
        os.replace(tmp, path)
        return {"ok": True}
    except OSError as err:
        return {"ok": False, "error": str(getattr(err, "code", "io_error"))}


def read_layout(data_root: Any) -> dict[str, Any] | None:
    doc = read_json(os.path.join(os.fspath(data_root), "layout.json"))
    if not doc or doc.get("schema_version") != LAYOUT_SCHEMA:
        return None
    return doc


def write_layout(data_root: Any, migrated_from: Iterable[str] | None = None,
                 *, now: float | None = None) -> dict[str, Any]:
    """写 layout.json。幂等：已存在则拒绝二次迁移（§7）。"""
    root = os.fspath(data_root)
    if read_layout(root) is not None:
        return {"ok": False, "reason": "layout_exists"}
    doc = {
        "schema_version": LAYOUT_SCHEMA,
        "created_at": int(now if now is not None else time.time()),
        "migrated_from": sorted({str(x) for x in (migrated_from or [])}),
    }
    res = write_json(os.path.join(root, "layout.json"), doc)
    if res.get("ok"):
        res["layout"] = doc
    return res


def detect_layout(data_root: Any) -> dict[str, Any]:
    """识别四代布局：v0.2 / legacy / workspace_<id> / profiles/<tag> / empty。"""
    root = os.path.abspath(os.fspath(data_root))
    doc = read_layout(root)
    if doc is not None:
        return {"kind": "v02", "layout": LAYOUT_SCHEMA, "sources": list(doc.get("migrated_from") or [])}
    sources: list[str] = []
    if os.path.isfile(os.path.join(root, "state.json")):
        sources.append("legacy")
    try:
        for name in sorted(os.listdir(root)):
            full = os.path.join(root, name)
            if os.path.isdir(full) and re.fullmatch(r"workspace_[A-Za-z0-9_-]{1,32}", name):
                sources.append(name)
            if name == "profiles" and os.path.isdir(full):
                for tag in sorted(os.listdir(full)):
                    if os.path.isdir(os.path.join(full, tag)):
                        sources.append(f"profiles/{tag}")
    except OSError:
        pass
    return {"kind": ("legacy" if sources else "empty"), "layout": None, "sources": sources}


# ── 4. JSONL 与墓碑折叠（D8 / §4.5）──────────────────────

def load_jsonl(path: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8-sig") as f:
            for line in f:
                line = line.strip().lstrip("\ufeff")
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
    except OSError:
        return []
    return rows


def dumps_jsonl(rows: Iterable[dict[str, Any]]) -> str:
    return "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows)


def fold_rows(rows: Iterable[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    """读取时按文件顺序折叠（last-wins + 墓碑/恢复事件），**绝不改历史行**。

    两种墓碑形态都认：
    1. 同 id 全量墓碑（既有 memorywriter.applyCleanupRequests：status=suppressed）；
    2. 轻量墓碑事件 `{type:"tombstone", target, at, by}`（§4.5 面板删除）；
    恢复事件 `{type:"restore", target, at, by}` 只撤销**事件墓碑**（面板删除可撤销）。
    """
    by_id: dict[str, dict[str, Any]] = {}
    suppressed: dict[str, dict[str, Any]] = {}
    for row in rows:
        if not isinstance(row, dict):
            continue
        kind = row.get("type")
        if kind == "tombstone" and row.get("target") is not None:
            tid = str(row.get("target"))
            suppressed[tid] = row
            continue
        if kind == "restore" and row.get("target") is not None:
            suppressed.pop(str(row.get("target")), None)
            continue
        rid = row.get("id")
        if rid is None:
            continue
        by_id[str(rid)] = row          # last-wins：后写覆盖前写

    folded: dict[str, dict[str, Any]] = {}
    for rid, row in by_id.items():
        if rid in suppressed and row.get("status") != "suppressed":
            ev = suppressed[rid]
            folded[rid] = {
                **row,
                "status": "suppressed",
                "suppressed_at": ev.get("at"),
                "suppressed_by": ev.get("by", "user_panel"),
                "suppressed_reason": ev.get("reason", "user_panel"),
            }
        else:
            folded[rid] = row

    active, suppressed_rows, superseded = [], [], []
    for rid in sorted(folded):
        row = folded[rid]
        status = str(row.get("status", "active"))
        if status == "active":
            active.append(row)
        elif status == "suppressed":
            suppressed_rows.append(row)
        elif status in ("superseded", "dormant", "stale"):
            superseded.append(row)
        else:
            active.append(row)
    return {"active": active, "suppressed": suppressed_rows, "superseded": superseded}


def apply_tombstone_event(rows: Iterable[dict[str, Any]], target: str, *,
                          at: float | None = None, by: str = "user_panel",
                          reason: str = "user_panel") -> list[dict[str, Any]]:
    """D8：遗忘 = **追加**一行墓碑事件，返回新列表（不改入参、不物理删行）。"""
    out = [dict(r) for r in rows]
    out.append({
        "type": "tombstone",
        "target": str(target),
        "at": int(at if at is not None else time.time()),
        "by": str(by),
        "reason": str(reason),
    })
    return out


def apply_restore_event(rows: Iterable[dict[str, Any]], target: str, *,
                        at: float | None = None, by: str = "user_panel") -> list[dict[str, Any]]:
    """§4.5：恢复 = 追加恢复事件（只撤销面板删除，scope 不重算），不删墓碑行。"""
    out = [dict(r) for r in rows]
    out.append({
        "type": "restore",
        "target": str(target),
        "at": int(at if at is not None else time.time()),
        "by": str(by),
    })
    return out


def purge_redact(row: dict[str, Any], *, at: float | None = None,
                 by: str = "user_panel") -> dict[str, Any]:
    """D16：彻底删除 = redact（抹内容留骨架），不删行。"""
    out = dict(row)
    out.update({
        "status": "suppressed",
        "content": "[已彻底删除]",
        "display_text": "[已彻底删除]",
        "sourceQuote": "",
        "object": "",
        "redacted_at": int(at if at is not None else time.time()),
        "redacted_by": str(by),
    })
    return out


def display_text(row: dict[str, Any]) -> str:
    """面板展示唯一来源（§3.8）：一句人话。缺失时用 subject/object 兜底。"""
    text = str(row.get("display_text") or "").strip()
    if text:
        return text
    subject = str(row.get("subject") or "").strip()
    relation = str(row.get("relation") or "").strip()
    obj = str(row.get("object") or "").strip()
    piece = f"{subject}{relation}{obj}".strip()
    if piece:
        return piece
    content = str(row.get("content") or "").strip()
    if content:
        return content[:80]
    return f"记忆 {row.get('id', '?')}"


def trash_view(rows: Iterable[dict[str, Any]], *, now: float | None = None) -> list[dict[str, Any]]:
    """回收站视图：只读 `suppressed`，不复制数据、不自动清除（D14/D15）。"""
    t = now if now is not None else time.time()
    out = []
    for row in fold_rows(rows)["suppressed"]:
        at = row.get("suppressed_at") or row.get("redacted_at")
        age = None
        if at is not None:
            try:
                age = max(0, int((t - float(at)) // 86400))
            except (TypeError, ValueError):
                age = None
        out.append({
            "id": str(row.get("id")),
            "display_text": display_text(row),
            "scope": str(row.get("scope") or "local"),
            "sandbox_id": row.get("sandbox_id"),
            "suppressed_at": at,
            "days_in_trash": age,
            "redacted": "redacted_at" in row,
        })
    return out


# ── 5. 作用域路由（§4.3）─────────────────────────────────

PERSISTENT_RE = re.compile(
    r"(以后都|以后也要|永远|再也别|从此|一直|永久|无论哪个项目|不管在哪个项目|无论去哪)")
PROJECT_RE = re.compile(
    r"(这个项目|本项目|本仓库|这个仓库|这次|当前项目|这个工程|本工程|当前仓库|当前工作区)")


def decide_scope(text: Any) -> dict[str, Any]:
    """§4.3：持久副词 + 无项目指代 → global；项目指代 → local；两者同时 → **local 胜**；
    都没有 → pending（待定区，默认不参与检索）。"""
    s = str(text or "")
    persistent = bool(PERSISTENT_RE.search(s))
    project = bool(PROJECT_RE.search(s))
    reasons: list[str] = []
    if project:
        reasons.append("project_reference")
    if persistent:
        reasons.append("persistent_adverb")
    if project and persistent:
        return {"scope": "local", "reasons": reasons + ["local_wins"], "promotable": False}
    if project:
        return {"scope": "local", "reasons": reasons, "promotable": False}
    if persistent:
        return {"scope": "global", "reasons": reasons, "promotable": False}
    return {"scope": "pending", "reasons": ["no_signal"], "promotable": True}


# ── 6. 双层合并与冲突裁决（D9 / §4.2 §4.4）──────────────

def _row_tokens(row: dict[str, Any]) -> int:
    return PER_ITEM_OVERHEAD + max(1, math.ceil(len(display_text(row)) / CHARS_PER_TOKEN))


def _row_potency(row: dict[str, Any]) -> float:
    try:
        return float(row.get("potency", row.get("importance", 1.0)))
    except (TypeError, ValueError):
        return 1.0


def merge_layers(rows: Iterable[dict[str, Any]], *, sandbox_id: str, persona_id: str,
                 budget: int = DEFAULT_TOKEN_BUDGET, top_k: int = DEFAULT_TOP_K,
                 global_cap_ratio: float = GLOBAL_CAP_RATIO,
                 local_floor: int = LOCAL_FLOOR) -> dict[str, Any]:
    """§4.2：merge → 行内标签校验 → potency → 冲突裁决 → 配额 → top-K。

    - 行内标签不符 → 丢弃 + 告警（§4.4.5）；
    - local over global（D9）：同 (subject, relation) 不同 object 时 local 胜；
    - 配额：local 至少 `local_floor` 条；global 占用 token ≤ `global_cap_ratio × budget`。
    """
    sid = str(sandbox_id or "")
    pid = str(persona_id or "")
    dropped: list[dict[str, Any]] = []
    candidates: list[dict[str, Any]] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        scope = str(row.get("scope") or "")
        if scope == "pending":
            dropped.append({"id": row.get("id"), "reason": "pending_not_searchable"})
            continue
        if scope not in ("global", "local"):
            dropped.append({"id": row.get("id"), "reason": "scope_label_mismatch"})
            continue
        if str(row.get("persona_id") or "") != pid:
            dropped.append({"id": row.get("id"), "reason": "persona_label_mismatch"})
            continue
        row_sandbox = str(row.get("sandbox_id") or "")
        if scope == "local" and row_sandbox not in (sid, "legacy"):
            # "legacy" 是迁移溯源标记（§7），迁移后落在当前沙盒的 local 文件里，视为当前。
            dropped.append({"id": row.get("id"), "reason": "sandbox_label_mismatch"})
            continue
        candidates.append(dict(row, scope=scope))

    # 冲突：同 (subject, relation)、不同 object，global 与 local 同时存在 → local 胜
    conflicts: list[dict[str, Any]] = []
    by_key: dict[tuple[str, str], dict[str, list[dict[str, Any]]]] = {}
    for row in candidates:
        key = (str(row.get("subject") or ""), str(row.get("relation") or ""))
        by_key.setdefault(key, {"global": [], "local": []})[row["scope"]].append(row)
    loser_ids: set[str] = set()
    for (subject, relation), group in sorted(by_key.items()):
        if not group["global"] or not group["local"]:
            continue
        globals_obj = {str(r.get("object") or "") for r in group["global"]}
        locals_obj = {str(r.get("object") or "") for r in group["local"]}
        if globals_obj == locals_obj:
            continue
        for row in group["global"]:
            loser_ids.add(str(row.get("id")))
        conflicts.append({
            "subject": subject,
            "relation": relation,
            "global_objects": sorted(globals_obj),
            "local_objects": sorted(locals_obj),
            "winner": "local",
            "dropped_ids": sorted(str(r.get("id")) for r in group["global"]),
        })

    pool = []
    for row in candidates:
        if str(row.get("id")) in loser_ids:
            dropped.append({"id": row.get("id"), "reason": "conflict_local_wins"})
            continue
        pool.append(row)
    pool.sort(key=lambda r: (-_row_potency(r), str(r.get("id"))))

    selected: list[dict[str, Any]] = []
    global_tokens = 0
    local_count = 0
    used = 0
    cap = max(0.0, float(global_cap_ratio)) * max(0, int(budget))
    for row in pool:
        if len(selected) >= top_k:
            dropped.append({"id": row.get("id"), "reason": "top_k"})
            continue
        cost = _row_tokens(row)
        if used + cost > budget:
            dropped.append({"id": row.get("id"), "reason": "budget"})
            continue
        if row["scope"] == "global":
            if len(selected) >= top_k:
                continue
            if global_tokens + cost > cap:
                dropped.append({"id": row.get("id"), "reason": "global_quota"})
                continue
            global_tokens += cost
        else:
            local_count += 1
        selected.append(row)
        used += cost

    # local 保底：若选中里 local 少于下限，用本地候选补足；
    # top-K 已满时**挤掉效力最低的 global**（local 保底优先于 global 配额）。
    if local_count < local_floor:
        selected_ids = {str(r.get("id")) for r in selected}
        for row in pool:
            if local_count >= local_floor:
                break
            if row["scope"] != "local" or str(row.get("id")) in selected_ids:
                continue
            if len(selected) >= top_k:
                victims = [i for i, r in enumerate(selected) if r["scope"] == "global"]
                if not victims:
                    break
                victim = victims[-1]
                selected.pop(victim)
            selected.append(row)
            selected_ids.add(str(row.get("id")))
            local_count += 1
            used += _row_tokens(row)

    return {
        "selected": selected,
        "dropped": dropped,
        "conflicts": conflicts,
        "tokens_used": used,
        "token_budget": int(budget),
        "global_tokens": global_tokens,
        "global_cap": int(cap),
        "local_count": local_count,
    }


# ── 7. 目录锁（D10 / §4.7）───────────────────────────────

LOCK_TIMEOUT_DEFAULT = 5.0
LOCK_STALE_DEFAULT = 30.0
LOCK_KEYS = ("memory", "global", "audit", "skeleton", "compaction")


class LockTimeout(RuntimeError):
    """拿不到锁（超时）。调用方必须 fail-closed，不得无锁写入。"""


def lock_key(kind: str, *, sandbox_id: str | None = None,
             persona_id: str | None = None, scope: str | None = None) -> str:
    """§4.7 锁键：分级保护，skeleton 独立于 memory（S2）。"""
    if kind == "memory":
        return f"lock:memory:{_check_sandbox_id(sandbox_id or '')}"
    if kind == "global":
        return f"lock:global:{_check_persona_id(persona_id or '')}"
    if kind == "audit":
        return "lock:audit"
    if kind == "skeleton":
        return f"lock:skeleton:{_check_sandbox_id(sandbox_id or '')}:{_check_persona_id(persona_id or '')}"
    if kind == "compaction":
        return f"lock:compaction:{str(scope or 'local')}"
    raise ValueError(f"lock_key: unknown kind {kind!r}")


def _pid_alive(pid: int) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class Lock:
    """目录锁：`mkdir` 原子创建；持锁者信息写 `owner.json`；陈旧 30s 可抢占。"""

    def __init__(self, lock_root: Any, key: str, *, timeout: float = LOCK_TIMEOUT_DEFAULT,
                 stale: float = LOCK_STALE_DEFAULT, poll: float = 0.05):
        self.key = str(key)
        self.timeout = float(timeout)
        self.stale = float(stale)
        self.poll = max(0.01, float(poll))
        digest = hashlib.sha256(self.key.encode("utf-8")).hexdigest()[:16]
        self.dir = os.path.join(os.path.abspath(os.fspath(lock_root)), f"{digest}.lock")
        self.owner_file = os.path.join(self.dir, "owner.json")
        self.held = False
        self._owner: dict[str, Any] = {}

    def _try_acquire(self) -> bool:
        try:
            os.makedirs(os.path.dirname(self.dir), exist_ok=True)
            os.mkdir(self.dir)
        except FileExistsError:
            return False
        except OSError:
            return False
        host = os.environ.get("COMPUTERNAME") or ""
        if not host and hasattr(os, "uname"):
            host = os.uname().nodename
        self._owner = {
            "key": self.key,
            "pid": os.getpid(),
            "host": host or "unknown",
            "at": time.time(),
            "token": uuid.uuid4().hex,
        }
        try:
            with open(self.owner_file, "w", encoding="utf-8") as f:
                json.dump(self._owner, f, ensure_ascii=False)
        except OSError:
            pass
        self.held = True
        return True

    def _is_stale(self) -> bool:
        try:
            with open(self.owner_file, "r", encoding="utf-8") as f:
                owner = json.load(f)
        except (OSError, ValueError):
            owner = {}
        try:
            at = float(owner.get("at", 0))
        except (TypeError, ValueError):
            at = 0.0
        if not at:
            # owner.json 还没落盘（正在创建）：按目录 mtime 判陈旧，避免误抢占
            try:
                at = os.path.getmtime(self.dir)
            except OSError:
                return False
        if time.time() - at > self.stale:
            return True
        pid = owner.get("pid")
        if isinstance(pid, int) and pid > 0 and pid != os.getpid() and not _pid_alive(pid):
            return True
        return False

    def acquire(self) -> "Lock":
        deadline = time.time() + self.timeout
        while True:
            if self._try_acquire():
                return self
            if self._is_stale():
                self._break_stale()
                continue
            if time.time() >= deadline:
                raise LockTimeout(f"lock timeout after {self.timeout:.1f}s: {self.key}")
            time.sleep(self.poll)

    def _break_stale(self) -> None:
        for name in (os.path.basename(self.owner_file),):
            try:
                os.remove(os.path.join(self.dir, name))
            except OSError:
                pass
        try:
            os.rmdir(self.dir)
        except OSError:
            pass

    def release(self) -> None:
        if not self.held:
            return
        try:
            os.remove(self.owner_file)
        except OSError:
            pass
        try:
            os.rmdir(self.dir)
        except OSError:
            pass
        self.held = False

    def __enter__(self) -> "Lock":
        return self.acquire()

    def __exit__(self, exc_type, exc, tb) -> bool:
        self.release()
        return False


# ── 8. 热重载判据（§4.6）────────────────────────────────

def _count_lines(path: str) -> int:
    try:
        with open(path, "rb") as f:
            return sum(1 for _ in f)
    except OSError:
        return 0


def file_signature(path: Any) -> dict[str, Any] | None:
    p = os.fspath(path)
    try:
        st = os.stat(p)
    except OSError:
        return None
    return {"mtime_ns": st.st_mtime_ns, "size": st.st_size, "lines": _count_lines(p)}


def should_reload(prev: dict[str, Any] | None, cur: dict[str, Any] | None, *,
                  now_ms: float, last_check_ms: float, throttle_ms: float = 200.0) -> bool:
    """mtime/size/行数 任一变化且过 200ms 节流 → 重载。"""
    if now_ms - last_check_ms < throttle_ms:
        return False
    if prev is None or cur is None:
        return True
    return prev != cur


# ── 9. S4：UCA-01 文本检查（纯函数）──────────────────────

UCA01_RULES: tuple[tuple[str, re.Pattern[str], str], ...] = (
    ("private_key", re.compile(r"-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----"), "block"),
    ("credential_leak", re.compile(
        r"(?i)\b(?:api[_-]?key|secret|token|password|passwd|pwd)\b\s*[:=]\s*\S{6,}"), "block"),
    ("aws_key", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"), "block"),
    ("sk_key", re.compile(r"\bsk-[A-Za-z0-9_-]{20,}\b"), "block"),
    ("id_card", re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), "block"),
    ("bank_card", re.compile(r"(?<!\d)(?:\d[ -]?){15,18}\d(?!\d)"), "block"),
    ("phone_number", re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), "warn"),
    ("secret_marker", re.compile(r"(?:绝密|机密|涉密|内部机密|confidential)"), "warn"),
)


def check_uca01_text(text: Any) -> dict[str, Any]:
    """S4：记忆写入前的 UCA-01 文本检查。

    verdict: block（凭据/密钥/证件/银行卡）> warn（手机号/涉密标记）> ok。
    reasons 是稳定机器码，供审计与面板展示；不返回匹配到的原文（防二次泄露）。
    """
    s = str(text or "")
    if not s.strip():
        return {"verdict": "ok", "reasons": []}
    reasons: list[tuple[str, str]] = []
    for code, pattern, level in UCA01_RULES:
        if pattern.search(s):
            reasons.append((code, level))
    if any(level == "block" for _c, level in reasons):
        verdict = "block"
    elif reasons:
        verdict = "warn"
    else:
        verdict = "ok"
    return {"verdict": verdict, "reasons": [c for c, _l in reasons]}


# ── 10. 记忆行构造（§3.8）────────────────────────────────

def make_memory_row(*, mem_id: str, ts: float, mem_type: str, origin: str,
                    subject: str = "", relation: str = "", obj: str = "",
                    content: str = "", display: str | None = None,
                    sandbox: str, persona: str, scope: str,
                    importance: float = 0.5, status: str = "active") -> dict[str, Any]:
    """按 §3.8 契约构造一行记忆（★ = 本期新增字段）。"""
    if scope not in ("global", "local", "pending"):
        raise ValueError("make_memory_row: bad scope")
    return {
        "id": str(mem_id),
        "ts": int(ts),
        "type": str(mem_type),
        "origin": str(origin),
        "layer": "warm",
        "status": status,
        "subject": str(subject),
        "relation": str(relation),
        "object": str(obj),
        "content": str(content),
        "display_text": str(display if display is not None else (subject + relation + obj)),
        "sandbox_id": str(sandbox),
        "persona_id": str(persona),
        "scope": scope,
        "importance": float(importance),
        "potency_inputs": {"hits": 0, "last_hit_at": None, "importance": float(importance)},
        "supersedes": None,
        "superseded_by": None,
        "suppressed_at": None,
        "suppressed_by": None,
    }
