# -*- coding: utf-8 -*-
"""LINGSHU 布局迁移 v0.2：三代旧布局 → layout-0.2（施工单 v2 §7）

    python tools/migrate_layout_v02.py --data-root <dir> --workspace-root <dir>
    python tools/migrate_layout_v02.py ... --apply
    python tools/migrate_layout_v02.py ... --apply --json

设计红线（与施工单 §7 一致）：
- **默认 dry-run，绝不写**；只有显式 `--apply` 才落盘。
- **复制不移动**：旧文件只读保留，不删除、不改写；apply 后逐文件回验源 sha256 未变。
- **先整份备份再迁移**：data_root 复制到 backup-dir，逐文件 sha256 校验一致才继续；
  备份已存在且内容不同 → 拒绝覆盖（exit 2）；内容完全一致 → 跳过复制。
- **幂等**：layout.json 存在即拒绝二次迁移（exit 3），不碰任何文件。
- **失败即回滚**：校验不一致 → 撤销本次新建/覆盖的文件与目录，不留下半套 layout.json（exit 4）。
- 只写本次迁移涉及的新路径；旧布局（state.json / memory.jsonl / audit.jsonl、
  workspace_<id>/、profiles/<tag>/）保持原样。
- 除 §7 规定产物外，补齐 v0.2 目录契约：personas/<pid>/persona.json（原样复制）、
  sandboxes/index.json、sandboxes/<sid>/sandbox.json，并落 kind=sandbox_mapped 审计。

语义源复用 engine/layout.py（D4 唯一来源）：规范路径 / sandboxId / 六键路径 /
JSONL 读写 / 布局识别 / layout.json 写入 / 审计 kind 常量，本文件不另造一套。

退出码：0 成功 ｜ 2 用法/环境错误 ｜ 3 已迁移（幂等拒绝）｜ 4 校验失败已回滚。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from engine import layout as L  # noqa: E402

EXIT_OK = 0
EXIT_USAGE = 2
EXIT_MIGRATED = 3
EXIT_VERIFY = 4

SOURCE_KINDS = ("memory.jsonl", "audit.jsonl", "state.json")
PERSONA_FILE = "persona.json"
WORKSPACE_RE = re.compile(r"^workspace_[A-Za-z0-9_-]{1,32}$")
PROFILE_LABEL_RE = re.compile(r"^(?:workspace_[A-Za-z0-9_-]+/)?profiles/([A-Za-z0-9_-]{1,32})$")

DEFAULT_BACKUP_DIR = r"D:\.lingshu-backups\dsh-lingshu_20261029"
DEFAULT_PERSONA = "butler"

# state.json 拆分：persona 层字段名（§3.6）与 sandbox 层字段名（§3.7）。
PERSONA_FIELDS = ("integral_baseline", "emotion_baseline", "relations")
SANDBOX_EXPLICIT = ("turns", "stage", "sandbox_integral", "control")
# 迁移时从 sandbox state 里排除的键（已归入 persona 层或已改名）。
STATE_TAKEN = set(PERSONA_FIELDS) | {
    "dev", "relation", "relations", "schema_version",
    "turns", "stage", "sandbox_integral", "control",
}


class MigrationError(RuntimeError):
    """迁移前可判定的错误（不是校验失败）。"""


# ── 0. 基础 IO ───────────────────────────────────────────

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def jsonl_line_count(path: str) -> int:
    """非空行数（迁移输出严格一行一条，用来交叉校验 load_jsonl 的行数）。"""
    n = 0
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            if line.strip().lstrip("\ufeff"):
                n += 1
    return n


def rel_to(root: str, path: str) -> str:
    return os.path.relpath(path, root).replace("\\", "/")


def _norm3(value) -> dict:
    """把任意 {valence,arousal,dominance} 污数据归一成三个 float。"""
    src = value if isinstance(value, dict) else {}
    out = {}
    for k in ("valence", "arousal", "dominance"):
        try:
            out[k] = float(src.get(k, 0.0) or 0.0)
        except (TypeError, ValueError):
            out[k] = 0.0
    return out


def _as_int(value, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def derive_stage(turns: int) -> str:
    """与 plugin/dsh-lingshu/index.js#stageOf 默认阈值一致（coldTurns=3 / warmTurns=20）。"""
    if turns < 3:
        return "cold"
    if turns < 20:
        return "warm"
    return "regular"


# ── 1. 来源扫描（legacy / workspace_<id> / profiles/<tag>）────────

def scan_sources(data_root: str) -> list[dict]:
    """返回按迁移顺序排列的来源：legacy → workspace_<id> → 其 profiles/<tag> → 顶层 profiles/<tag>。

    每个来源：{label, dir, files:{kind: abs_path}}，只收真实存在的文件。
    """
    root = os.path.abspath(data_root)
    sources: list[dict] = []

    def add(label: str, folder: str) -> None:
        files = {}
        for kind in SOURCE_KINDS + (PERSONA_FILE,):
            p = os.path.join(folder, kind)
            if os.path.isfile(p):
                files[kind] = p
        if files:
            sources.append({"label": label, "dir": folder, "files": files})

    # ① legacy：数据根平铺三件
    add("legacy", root)

    try:
        names = sorted(os.listdir(root))
    except OSError:
        names = []
    # 顺序固定：workspace_<id>（含其 profiles 层）在前，顶层 profiles/<tag> 在后，
    # 保证 memory 追加顺序稳定（legacy → workspace → profiles）。
    workspace_dirs = [n for n in names if WORKSPACE_RE.match(n)
                      and os.path.isdir(os.path.join(root, n))]
    has_profiles = any(n == "profiles" and os.path.isdir(os.path.join(root, n)) for n in names)
    for name in workspace_dirs:
        full = os.path.join(root, name)
        add(name, full)
        pdir = os.path.join(full, "profiles")
        if os.path.isdir(pdir):
            try:
                tags = sorted(os.listdir(pdir))
            except OSError:
                tags = []
            for tag in tags:
                sub = os.path.join(pdir, tag)
                if os.path.isdir(sub):
                    add("%s/profiles/%s" % (name, tag), sub)
    if has_profiles:
        full = os.path.join(root, "profiles")
        try:
            tags = sorted(os.listdir(full))
        except OSError:
            tags = []
        for tag in tags:
            sub = os.path.join(full, tag)
            if os.path.isdir(sub):
                add("profiles/%s" % tag, sub)
    return sources


# ── 2. 迁移计划（dry-run 与 apply 共用）──────────────────

def choose_persona_source(sources: list[dict]) -> dict | None:
    """选 persona.json 来源：优先"同时有 memory.jsonl 的那个"，否则取首个；保持来源顺序。"""
    candidates = [s for s in sources if PERSONA_FILE in s["files"]]
    if not candidates:
        return None
    with_memory = [s for s in candidates if "memory.jsonl" in s["files"]]
    return (with_memory or candidates)[0]


def detect_profile_tag(sources: list[dict]) -> str | None:
    """从来源标签里取 profile tag（profiles/<tag> 或 workspace_<id>/profiles/<tag>）。"""
    for src in sources:
        m = PROFILE_LABEL_RE.match(src["label"])
        if m:
            return m.group(1)
    return None


def build_ctx(args) -> dict:
    data_root = os.path.abspath(args.data_root)
    if not os.path.isdir(data_root):
        raise MigrationError("--data-root 不是目录: %s" % data_root)
    pid = str(args.persona or "").strip()
    if not L.PERSONA_ID_RE.match(pid) or pid in (".", ".."):
        raise MigrationError("--persona 非法: %r" % args.persona)
    workspace_root = os.path.abspath(args.workspace_root)
    canonical_path = L.canonicalize_path(workspace_root)
    sandbox = L.sandbox_id(canonical_path)
    label = os.path.basename(canonical_path.rstrip("/")) or canonical_path
    paths = L.resolve_memory_paths(data_root, sandbox, pid)
    persona_copy = os.path.join(data_root, "personas", pid, PERSONA_FILE)
    sandbox_dir = os.path.join(data_root, "sandboxes", sandbox)
    targets = {
        "memory": paths["localMemory"],
        "state": paths["state"],
        "persona_state": paths["personaState"],
        "audit": os.path.join(data_root, "audit", "audit.jsonl"),
        "layout": os.path.join(data_root, "layout.json"),
        "persona": persona_copy,
        "sandbox": os.path.join(sandbox_dir, "sandbox.json"),
        "index": os.path.join(data_root, "sandboxes", "index.json"),
    }

    scanned = scan_sources(data_root)
    files: list[dict] = []
    memory_total = 0
    audit_total = 0
    state_sources = 0
    for src in scanned:
        for kind in SOURCE_KINDS:
            p = src["files"].get(kind)
            if not p:
                continue
            entry = {
                "kind": ("memory" if kind == "memory.jsonl"
                         else "audit" if kind == "audit.jsonl" else "state"),
                "source": src["label"],
                "src": p,
                "src_rel": rel_to(data_root, p),
                "sha256": sha256_file(p),
                "rows": (1 if kind == "state.json" else len(L.load_jsonl(p))),
                "sandbox_id": ("legacy" if src["label"] == "legacy" else sandbox),
                "persona_id": pid,
            }
            if entry["kind"] == "memory":
                entry["target"] = targets["memory"]
                memory_total += entry["rows"]
            elif entry["kind"] == "audit":
                entry["target"] = targets["audit"]
                audit_total += entry["rows"]
            else:
                state_sources += 1
                entry["targets"] = [targets["persona_state"], targets["state"]]
            files.append(entry)

    # persona.json：多来源时取"有记忆的那个"，按字节原样复制到 personas/<pid>/。
    persona_src = choose_persona_source(scanned)
    if persona_src is not None:
        p = persona_src["files"][PERSONA_FILE]
        files.append({
            "kind": "persona",
            "source": persona_src["label"],
            "src": p,
            "src_rel": rel_to(data_root, p),
            "sha256": sha256_file(p),
            "rows": 1,
            "sandbox_id": ("legacy" if persona_src["label"] == "legacy" else sandbox),
            "persona_id": pid,
            "target": targets["persona"],
        })

    return {
        "data_root": data_root,
        "workspace_root": workspace_root,
        "canonical_path": canonical_path,
        "label": label,
        "profile_tag": detect_profile_tag(scanned),
        "backup_dir": os.path.abspath(args.backup_dir),
        "sandbox_id": sandbox,
        "persona_id": pid,
        "sources": [s["label"] for s in scanned],
        "source_files": [(s["label"], kind, s["files"][kind]) for s in scanned
                         for kind in SOURCE_KINDS if kind in s["files"]],
        "files": files,
        "targets": targets,
        "expected": {
            "memory_rows": memory_total,
            "audit_rows": audit_total,
            "audit_events": len(files),
            "state_sources": state_sources,
            "persona_copied": persona_src is not None,
        },
    }


def sandbox_docs(ctx: dict, now_s: int) -> tuple[dict, dict]:
    """§3.3 / §3.4：sandboxes/index.json 与 sandboxes/<sid>/sandbox.json。"""
    sid = ctx["sandbox_id"]
    doc = {
        "schema_version": L.SANDBOX_SCHEMA,
        "sandbox_id": sid,
        "canonical_path": ctx["canonical_path"],
        "label": ctx["label"],
        "created_at": now_s,
        "profile_tag": ctx["profile_tag"],
    }
    index = {
        "schema_version": L.SANDBOX_INDEX_SCHEMA,
        "sandboxes": {
            sid: {
                "canonical_path": ctx["canonical_path"],
                "label": ctx["label"],
                "created_at": now_s,
                "last_active_at": now_s,
            },
        },
    }
    return doc, index


def print_plan(ctx: dict) -> None:
    root = ctx["data_root"]
    print("LINGSHU 布局迁移 v0.2 · %s" % ("APPLY" if ctx.get("apply") else "DRY-RUN（不写任何文件）"))
    print("  数据根      : %s" % root)
    print("  项目根      : %s" % ctx["workspace_root"])
    print("  canonical   : %s" % ctx["canonical_path"])
    print("  sandbox_id  : %s  (sha256(canonicalPath)[:8])" % ctx["sandbox_id"])
    print("  persona_id  : %s" % ctx["persona_id"])
    print("  备份目录    : %s" % ctx["backup_dir"])
    print("  来源        : %d 个 %s" % (len(ctx["sources"]), ctx["sources"]))
    print("  待迁移文件  : %d 个" % len(ctx["files"]))
    for f in ctx["files"]:
        if f["kind"] == "state":
            dst = " + ".join(rel_to(root, p) for p in f["targets"])
        else:
            dst = rel_to(root, f["target"])
        print("  [%-6s] %-42s rows=%-5d sha256=%s… -> %s" % (
            f["kind"], f["src_rel"], f["rows"], f["sha256"][:12], dst))
    print("  [索引  ] sandboxes/index.json + sandboxes/%s/sandbox.json" % ctx["sandbox_id"])
    exp = ctx["expected"]
    print("  计划合计    : memory %d 行 / audit %d 行 + %d 条 migration_apply + 1 条 sandbox_mapped"
          " / state %d 源" % (
              exp["memory_rows"], exp["audit_rows"], exp["audit_events"], exp["state_sources"]))


# ── 3. 行补全（§3.8 / §3.9）──────────────────────────────

def enrich_memory_row(row: dict, sandbox: str, pid: str, source_label: str) -> dict:
    row = dict(row)
    row["sandbox_id"] = str(row.get("sandbox_id") or sandbox)
    row["persona_id"] = str(row.get("persona_id") or pid)
    row["scope"] = "local"
    if not str(row.get("display_text") or "").strip():
        row["display_text"] = L.display_text(row)
    # 迁移溯源：legacy 行 sandbox_id 固定为 "legacy"；其余行落到当前 sid，另留来源标签。
    row["migrated_from"] = source_label
    return row


def enrich_audit_row(row: dict, sandbox: str, pid: str, source_label: str) -> dict:
    row = dict(row)
    row["sandbox_id"] = row.get("sandbox_id") or sandbox
    row["persona_id"] = row.get("persona_id") or pid
    row["migrated_from"] = source_label
    return row


# ── 4. state.json 拆两份（§3.6 / §3.7）───────────────────

def build_state_docs(ctx: dict) -> tuple[dict | None, dict | None]:
    """把各来源 state.json 合成 persona_state（积分/情绪基线/关系）与 sandbox state（其余）。

    合并顺序 = 来源顺序（legacy → workspace → profiles），后者覆盖同名字段。
    `control.integral` 是"跨进程保留的积分基线"，归 persona 层；`sandbox_integral` 是
    v0.2 新概念，旧数据没有对应值，保留零基线（不臆造）。两者的来源都写进注释性字段
    `migrated_from`，便于回溯。
    """
    persona = {
        "schema_version": L.PERSONA_STATE_SCHEMA,
        "integral_baseline": _norm3(None),
        "emotion_baseline": _norm3(None),
        "relations": {},
        "migrated_from": [],
    }
    sandbox_doc = {
        "schema_version": L.STATE_SCHEMA,
        "turns": 0,
        "stage": "cold",
        "sandbox_integral": _norm3(None),
        "control": {},
    }
    rest: dict = {}
    stage_explicit = False
    found = False
    for entry in ctx["files"]:
        if entry["kind"] != "state":
            continue
        doc = L.read_json(entry["src"])
        if not isinstance(doc, dict):
            raise MigrationError("state.json 解析失败: %s" % entry["src"])
        found = True
        persona["migrated_from"].append(entry["source"])
        ctl = doc.get("control") if isinstance(doc.get("control"), dict) else {}
        integral = ctl.get("integral")
        if not isinstance(integral, dict):
            integral = doc.get("integral_baseline")
        if isinstance(integral, dict):
            persona["integral_baseline"] = _norm3(integral)
        emotion = doc.get("emotion_baseline")
        if not isinstance(emotion, dict):
            emotion = doc.get("dev")
        if isinstance(emotion, dict):
            persona["emotion_baseline"] = _norm3(emotion)
        relations = doc.get("relations")
        if not isinstance(relations, dict):
            relations = doc.get("relation")
        if isinstance(relations, dict):
            persona["relations"] = relations
        if "turns" in doc:
            sandbox_doc["turns"] = _as_int(doc.get("turns"))
        if "stage" in doc and doc.get("stage") is not None:
            sandbox_doc["stage"] = str(doc["stage"])
            stage_explicit = True
        if isinstance(doc.get("sandbox_integral"), dict):
            sandbox_doc["sandbox_integral"] = _norm3(doc["sandbox_integral"])
        if ctl:
            sandbox_doc["control"] = ctl
        for key, value in doc.items():
            if key in STATE_TAKEN:
                continue
            rest[key] = value
    if not found:
        return None, None
    if not stage_explicit:
        sandbox_doc["stage"] = derive_stage(_as_int(sandbox_doc["turns"]))
    sandbox_doc.update(rest)
    return persona, sandbox_doc


def migration_events(ctx: dict, when_ms: int) -> list[dict]:
    """每条迁移文件一条审计事件（kind=migration_apply），带 sandbox_id/persona_id。"""
    root = ctx["data_root"]
    events = []
    for entry in ctx["files"]:
        if entry["kind"] == "state":
            target = rel_to(root, ctx["targets"]["persona_state"]) + "+" + \
                     rel_to(root, ctx["targets"]["state"])
        else:
            target = rel_to(root, entry["target"])
        events.append({
            "ts": when_ms,
            "kind": "migration_apply",
            "sandbox_id": ctx["sandbox_id"],
            "persona_id": ctx["persona_id"],
            "source": entry["source"],
            "source_kind": entry["kind"],
            "source_file": entry["src_rel"],
            "target": target,
            "rows": entry["rows"],
            "source_sha256": entry["sha256"],
        })
    return events


# ── 5. 备份（复制 + 逐文件 sha256 校验）───────────────────

def _tree_files(root: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for base, _dirs, names in os.walk(root):
        for name in names:
            full = os.path.join(base, name)
            out[rel_to(root, full)] = sha256_file(full)
    return out


def backup_data_root(src: str, dst: str) -> dict:
    """把 data_root 整份复制到 backup_dir；返回 {files, skipped}。

    备份已存在：内容完全一致 → 跳过复制；否则拒绝覆盖（由调用方转成 exit 2）。
    """
    if os.path.exists(dst):
        if not os.path.isdir(dst):
            raise MigrationError("备份路径已存在且不是目录: %s" % dst)
        same = _tree_files(src)
        have = _tree_files(dst)
        if same == have:
            return {"files": len(have), "skipped": True}
        raise MigrationError("备份目录已存在且内容不一致，拒绝覆盖: %s" % dst)
    parent = os.path.dirname(dst)
    if parent:
        os.makedirs(parent, exist_ok=True)
    try:
        shutil.copytree(src, dst)
    except (OSError, shutil.Error) as err:
        # 复制失败不留半份备份（否则下次会被"内容不一致"永久拒绝）。
        shutil.rmtree(dst, ignore_errors=True)
        raise MigrationError("备份复制失败: %s" % err)
    src_files = _tree_files(src)
    dst_files = _tree_files(dst)
    if src_files != dst_files:
        diff = sorted(set(src_files) ^ set(dst_files))
        diff += [k for k in sorted(src_files)
                 if k in dst_files and src_files[k] != dst_files[k]]
        raise MigrationError("备份校验失败（sha256/文件集不一致）: %s" % ", ".join(diff[:5]))
    return {"files": len(dst_files), "skipped": False}


# ── 6. 事务（回滚本次新建/覆盖的文件与目录）───────────────

class Transaction:
    """只记录**本次**写过的文件；回滚时删除新建文件、还原被覆盖文件、移除空目录。"""

    def __init__(self) -> None:
        self._writes: list[tuple[str, bytes | None]] = []
        self._dirs: list[str] = []

    def ensure_dir(self, folder: str) -> None:
        folder = os.path.abspath(folder)
        missing: list[str] = []
        cur = folder
        while cur and not os.path.isdir(cur):
            missing.append(cur)
            parent = os.path.dirname(cur)
            if parent == cur:
                break
            cur = parent
        for path in reversed(missing):
            os.mkdir(path)
            self._dirs.append(path)

    def write_text(self, path: str, text: str) -> None:
        path = os.path.abspath(path)
        self.ensure_dir(os.path.dirname(path))
        prior = None
        if os.path.isfile(path):
            with open(path, "rb") as f:
                prior = f.read()
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        self._writes.append((path, prior))

    def write_bytes(self, path: str, data: bytes) -> None:
        """按字节原样复制（persona.json 必须与源文件 sha256 一致）。"""
        path = os.path.abspath(path)
        self.ensure_dir(os.path.dirname(path))
        prior = None
        if os.path.isfile(path):
            with open(path, "rb") as f:
                prior = f.read()
        with open(path, "wb") as f:
            f.write(data)
        self._writes.append((path, prior))

    def track_created(self, path: str) -> None:
        """登记一个由外部函数（engine.layout.write_layout）新建的文件。"""
        self._writes.append((os.path.abspath(path), None))

    def rollback(self) -> None:
        for path, prior in reversed(self._writes):
            try:
                if prior is None:
                    if os.path.isfile(path):
                        os.remove(path)
                else:
                    with open(path, "wb") as f:
                        f.write(prior)
            except OSError:
                pass
        for folder in reversed(self._dirs):
            try:
                os.rmdir(folder)
            except OSError:
                pass
        self._writes.clear()
        self._dirs.clear()


# ── 7. 应用与校验 ───────────────────────────────────────

def sandbox_mapped_event(ctx: dict, when_ms: int) -> dict:
    """§3.9：沙盒建立/映射的审计事件（kind=sandbox_mapped）。"""
    return {
        "ts": when_ms,
        "kind": "sandbox_mapped",
        "sandbox_id": ctx["sandbox_id"],
        "persona_id": ctx["persona_id"],
        "canonical_path": ctx["canonical_path"],
        "label": ctx["label"],
        "profile_tag": ctx["profile_tag"],
    }


# ── 4b. 应用 ────────────────────────────────────────────

def apply_migration(ctx: dict, tx: Transaction) -> dict:
    root = ctx["data_root"]
    sid = ctx["sandbox_id"]
    pid = ctx["persona_id"]
    targets = ctx["targets"]

    memory_rows: list[dict] = []
    audit_rows: list[dict] = []
    for entry in ctx["files"]:
        src_row_sandbox = "legacy" if entry["source"] == "legacy" else sid
        if entry["kind"] == "memory":
            for row in L.load_jsonl(entry["src"]):
                memory_rows.append(enrich_memory_row(row, src_row_sandbox, pid, entry["source"]))
        elif entry["kind"] == "audit":
            for row in L.load_jsonl(entry["src"]):
                audit_rows.append(enrich_audit_row(row, src_row_sandbox, pid, entry["source"]))

    persona_doc, sandbox_doc = build_state_docs(ctx)
    sandbox_meta, index_doc = sandbox_docs(ctx, int(time.time()))

    when_ms = int(time.time() * 1000)
    events = migration_events(ctx, when_ms)
    mapped = sandbox_mapped_event(ctx, when_ms)
    audit_text = L.dumps_jsonl(audit_rows + events + [mapped])

    written: list[str] = []
    if memory_rows:
        tx.write_text(targets["memory"], L.dumps_jsonl(memory_rows))
        written.append("memory")
    tx.write_text(targets["audit"], audit_text)
    written.append("audit")
    if persona_doc is not None and sandbox_doc is not None:
        tx.write_text(targets["persona_state"],
                      json.dumps(persona_doc, ensure_ascii=False, indent=2) + "\n")
        tx.write_text(targets["state"],
                      json.dumps(sandbox_doc, ensure_ascii=False, indent=2) + "\n")
        written.extend(["persona_state", "state"])

    # persona.json：按字节原样复制（复制不移动；多来源时已在 build_ctx 选定）。
    persona_entry = next((f for f in ctx["files"] if f["kind"] == "persona"), None)
    if persona_entry is not None:
        with open(persona_entry["src"], "rb") as f:
            raw = f.read()
        tx.write_bytes(targets["persona"], raw)
        written.append("persona")

    # §3.3 / §3.4：沙盒索引与 sandbox.json（index.js 靠它们发现/切换沙盒）。
    tx.write_text(targets["sandbox"],
                  json.dumps(sandbox_meta, ensure_ascii=False, indent=2) + "\n")
    tx.write_text(targets["index"],
                  json.dumps(index_doc, ensure_ascii=False, indent=2) + "\n")
    written.extend(["sandbox", "index"])

    problems = verify_migration(ctx, memory_rows, audit_rows, events, mapped)
    if problems:
        tx.rollback()
        return {"ok": False, "code": EXIT_VERIFY, "problems": problems}

    labels = sorted({entry["source"] for entry in ctx["files"]})
    res = L.write_layout(root, migrated_from=labels, now=time.time())
    if not res.get("ok"):
        tx.rollback()
        return {"ok": False, "code": EXIT_VERIFY,
                "problems": ["layout.json 写入失败: %s" % res.get("reason")]}
    tx.track_created(targets["layout"])

    # layout 落盘后回读，确认幂等标记真的生效（半套 layout 不允许留下）。
    if L.read_layout(root) is None:
        tx.rollback()
        return {"ok": False, "code": EXIT_VERIFY, "problems": ["layout.json 回读失败"]}

    return {
        "ok": True,
        "code": EXIT_OK,
        "written": written,
        "layout": res.get("layout"),
        "post_sha256": {
            key: sha256_file(path)
            for key, path in targets.items()
            if key != "layout" and os.path.isfile(path)
        },
    }


def verify_migration(ctx: dict, memory_rows: list[dict],
                     audit_rows: list[dict], events: list[dict],
                     mapped: dict | None = None) -> list[str]:
    """校验：源文件未被改动 + 目标行数/行内标签 + state 拆分 + audit 单本 + 沙盒元数据。"""
    root = ctx["data_root"]
    problems: list[str] = []
    exp = ctx["expected"]

    # ① 旧文件只读保留：逐文件 sha256 必须与计划一致（复制不移动的硬证据）。
    for label, kind, path in ctx["source_files"]:
        if not os.path.isfile(path):
            problems.append("源文件消失: %s/%s" % (label, kind))
            continue
        planned = next((f["sha256"] for f in ctx["files"]
                        if f["src"] == path), None)
        if planned is None:
            continue
        if sha256_file(path) != planned:
            problems.append("源文件被改写: %s/%s" % (label, kind))

    # ② memory：行数 == 源行数；每行 sandbox_id/persona_id/scope/display_text 齐全；顺序保持。
    mem_target = ctx["targets"]["memory"]
    if exp["memory_rows"]:
        if not os.path.isfile(mem_target):
            problems.append("缺少目标 local_memory.jsonl")
        else:
            lines = jsonl_line_count(mem_target)
            if lines != exp["memory_rows"]:
                problems.append("memory 行数不符: 目标 %d != 源 %d" % (lines, exp["memory_rows"]))
            got = L.load_jsonl(mem_target)
            if len(got) != exp["memory_rows"]:
                problems.append("memory 可解析行数不符: %d != %d" % (len(got), exp["memory_rows"]))
            for idx, row in enumerate(got):
                if not row.get("sandbox_id"):
                    problems.append("memory 第 %d 行缺 sandbox_id" % (idx + 1))
                if not row.get("persona_id"):
                    problems.append("memory 第 %d 行缺 persona_id" % (idx + 1))
                if row.get("scope") != "local":
                    problems.append("memory 第 %d 行 scope!=local" % (idx + 1))
                if not str(row.get("display_text") or "").strip():
                    problems.append("memory 第 %d 行缺 display_text" % (idx + 1))
            if [str(r.get("id")) for r in got] != [str(r.get("id")) for r in memory_rows]:
                problems.append("memory 行顺序与来源顺序不一致")

    # ③ audit 单本：源 audit 行 + migration_apply 事件 + sandbox_mapped；每行带 sandbox_id/persona_id。
    audit_target = ctx["targets"]["audit"]
    expected_audit = exp["audit_rows"] + len(events) + (1 if mapped else 0)
    if not os.path.isfile(audit_target):
        problems.append("缺少统一 audit/audit.jsonl")
    else:
        lines = jsonl_line_count(audit_target)
        if lines != expected_audit:
            problems.append("audit 行数不符: 目标 %d != 期望 %d" % (lines, expected_audit))
        got = L.load_jsonl(audit_target)
        if len(got) != expected_audit:
            problems.append("audit 可解析行数不符: %d != %d" % (len(got), expected_audit))
        for idx, row in enumerate(got):
            if "sandbox_id" not in row or not str(row.get("sandbox_id") or ""):
                problems.append("audit 第 %d 行缺 sandbox_id" % (idx + 1))
            if "persona_id" not in row or not str(row.get("persona_id") or ""):
                problems.append("audit 第 %d 行缺 persona_id" % (idx + 1))
        if sum(1 for r in got if r.get("kind") == "migration_apply") != len(events):
            problems.append("migration_apply 事件数不符")

    # ④ state 拆两份：persona_state 只放 persona 字段，sandbox state 放其余。
    if exp["state_sources"]:
        persona_path = ctx["targets"]["persona_state"]
        state_path = ctx["targets"]["state"]
        for path, name in ((persona_path, "persona_state.json"), (state_path, "state.json")):
            if not os.path.isfile(path):
                problems.append("缺少 %s" % name)
        persona_doc = L.read_json(persona_path) if os.path.isfile(persona_path) else None
        sandbox_doc = L.read_json(state_path) if os.path.isfile(state_path) else None
        if persona_doc is None:
            problems.append("persona_state.json 无法解析")
        else:
            if persona_doc.get("schema_version") != L.PERSONA_STATE_SCHEMA:
                problems.append("persona_state.json schema_version 不符")
            for key in PERSONA_FIELDS:
                if key not in persona_doc:
                    problems.append("persona_state.json 缺 %s" % key)
            if "turns" in persona_doc or "control" in persona_doc:
                problems.append("persona_state.json 混入 sandbox 字段")
        if sandbox_doc is None:
            problems.append("state.json 无法解析")
        else:
            if sandbox_doc.get("schema_version") != L.STATE_SCHEMA:
                problems.append("state.json schema_version 不符")
            for key in ("turns", "stage", "control"):
                if key not in sandbox_doc:
                    problems.append("state.json 缺 %s" % key)
            for key in ("relations", "dev", "emotion_baseline"):
                if key in sandbox_doc:
                    problems.append("state.json 混入 persona 字段 %s" % key)

    # ⑤ persona.json：按字节原样复制（目标 sha256 必须等于源）。
    persona_entry = next((f for f in ctx["files"] if f["kind"] == "persona"), None)
    if persona_entry is not None:
        target = ctx["targets"]["persona"]
        if not os.path.isfile(target):
            problems.append("缺少 personas/%s/persona.json" % ctx["persona_id"])
        elif sha256_file(target) != persona_entry["sha256"]:
            problems.append("persona.json 复制后 sha256 不一致")

    # ⑥ 沙盒元数据：sandbox.json / index.json 必须与 canonicalPath→sid 对应（index.js 靠它发现沙盒）。
    sid = ctx["sandbox_id"]
    meta = L.read_json(ctx["targets"]["sandbox"]) if os.path.isfile(ctx["targets"]["sandbox"]) else None
    if meta is None:
        problems.append("sandbox.json 无法解析")
    else:
        if meta.get("schema_version") != L.SANDBOX_SCHEMA:
            problems.append("sandbox.json schema_version 不符")
        if meta.get("sandbox_id") != sid:
            problems.append("sandbox.json sandbox_id 与 canonicalPath 算出的 %s 不符" % sid)
        if meta.get("canonical_path") != ctx["canonical_path"]:
            problems.append("sandbox.json canonical_path 不符")
    index = L.read_json(ctx["targets"]["index"]) if os.path.isfile(ctx["targets"]["index"]) else None
    if index is None:
        problems.append("sandboxes/index.json 无法解析")
    else:
        if index.get("schema_version") != L.SANDBOX_INDEX_SCHEMA:
            problems.append("index.json schema_version 不符")
        entry = (index.get("sandboxes") or {}).get(sid) or {}
        if entry.get("canonical_path") != ctx["canonical_path"]:
            problems.append("index.json 缺 %s 或 canonical_path 不符" % sid)
    return problems


# ── 8. CLI ──────────────────────────────────────────────

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="migrate_layout_v02.py",
        description="LINGSHU 三代旧布局 → layout-0.2 迁移（默认 dry-run，绝不写）",
        epilog="退出码：0 成功 / 2 用法错误 / 3 已迁移 / 4 校验失败已回滚",
    )
    p.add_argument("--data-root", required=True, help="旧布局所在数据根")
    p.add_argument("--workspace-root", required=True,
                   help="迁移后 local 记忆归属的项目根（用于计算 sandboxId）")
    p.add_argument("--persona", default=DEFAULT_PERSONA, help="人格 id，默认 butler")
    p.add_argument("--backup-dir", default=DEFAULT_BACKUP_DIR,
                   help="迁移前整份备份目录，默认 %s" % DEFAULT_BACKUP_DIR)
    p.add_argument("--apply", action="store_true",
                   help="真正执行迁移（不传 = dry-run，绝不写）")
    p.add_argument("--json", action="store_true", help="输出机器可读摘要")
    return p


def refusals(ctx: dict) -> tuple[int, str] | None:
    """迁移前的一次性拒绝条件（幂等 / 备份路径冲突）。"""
    layout_path = ctx["targets"]["layout"]
    if os.path.exists(layout_path):
        detail = "layout.json 已存在"
        doc = L.read_json(layout_path)
        if doc is not None:
            detail += "（schema_version=%s）" % doc.get("schema_version")
        else:
            detail += "（无法解析，按已迁移处理，避免覆盖）"
        return EXIT_MIGRATED, "拒绝二次迁移：%s" % detail

    data_root = os.path.normcase(os.path.abspath(ctx["data_root"])).rstrip(os.sep)
    backup = os.path.normcase(os.path.abspath(ctx["backup_dir"])).rstrip(os.sep)
    if backup == data_root or backup.startswith(data_root + os.sep) \
            or data_root.startswith(backup + os.sep):
        return EXIT_USAGE, "备份目录不能与数据根互相包含: %s" % ctx["backup_dir"]
    return None


def run(args) -> int:
    try:
        ctx = build_ctx(args)
    except MigrationError as err:
        print("错误: %s" % err, file=sys.stderr)
        return EXIT_USAGE
    ctx["apply"] = bool(args.apply)

    refused = refusals(ctx)
    if refused:
        code, message = refused
        if args.json:
            print(json.dumps({"status": "refused", "code": code, "message": message,
                              "data_root": ctx["data_root"]}, ensure_ascii=False))
        else:
            print(message)
        return code

    if not args.apply:
        if args.json:
            print(json.dumps(_summary(ctx, "dry_run"), ensure_ascii=False, indent=2))
        else:
            print_plan(ctx)
            print("(dry-run) 未创建任何目录/文件；确认无误后加 --apply 执行。")
        return EXIT_OK

    if not args.json:
        print_plan(ctx)

    tx = Transaction()
    try:
        backup = backup_data_root(ctx["data_root"], ctx["backup_dir"])
    except MigrationError as err:
        print("错误: %s" % err, file=sys.stderr)
        return EXIT_VERIFY
    except OSError as err:
        print("错误: 备份失败: %s" % err, file=sys.stderr)
        return EXIT_VERIFY

    try:
        result = apply_migration(ctx, tx)
    except MigrationError as err:
        tx.rollback()
        print("错误: %s（未写入任何迁移产物）" % err, file=sys.stderr)
        return EXIT_VERIFY
    if not result.get("ok"):
        if args.json:
            print(json.dumps({"status": "rolled_back", "code": EXIT_VERIFY,
                              "problems": result.get("problems", [])}, ensure_ascii=False))
        else:
            print("校验失败，已回滚本次新建文件（旧布局未改动）:", file=sys.stderr)
            for problem in result.get("problems", []):
                print("  - %s" % problem, file=sys.stderr)
        return EXIT_VERIFY

    ctx["backup"] = backup
    ctx["result"] = result
    if args.json:
        print(json.dumps(_summary(ctx, "applied"), ensure_ascii=False, indent=2))
    else:
        root = ctx["data_root"]
        print("迁移完成：")
        print("  layout.json : %s" % rel_to(root, ctx["targets"]["layout"]))
        if "memory" in result.get("written", []):
            print("  local 记忆  : %s（%d 行）" % (
                rel_to(root, ctx["targets"]["memory"]), ctx["expected"]["memory_rows"]))
        print("  审计单本    : %s（%d 行 + %d 条 migration_apply）" % (
            rel_to(root, ctx["targets"]["audit"]),
            ctx["expected"]["audit_rows"], ctx["expected"]["audit_events"]))
        if "persona_state" in result.get("written", []):
            print("  persona 状态: %s" % rel_to(root, ctx["targets"]["persona_state"]))
            print("  sandbox 状态: %s" % rel_to(root, ctx["targets"]["state"]))
        if "persona" in result.get("written", []):
            print("  persona.json: %s（原样复制）" % rel_to(root, ctx["targets"]["persona"]))
        print("  沙盒元数据  : %s + %s" % (
            rel_to(root, ctx["targets"]["index"]), rel_to(root, ctx["targets"]["sandbox"])))
        print("  备份        : %s（%d 文件%s）" % (
            ctx["backup_dir"], backup["files"], "，已存在且一致，跳过" if backup["skipped"] else ""))
        print("  旧布局      : 只读保留，未删除/未改写")
    return EXIT_OK


def _summary(ctx: dict, status: str) -> dict:
    root = ctx["data_root"]
    summary = {
        "status": status,
        "data_root": root,
        "workspace_root": ctx["workspace_root"],
        "canonical_path": ctx["canonical_path"],
        "label": ctx["label"],
        "profile_tag": ctx["profile_tag"],
        "backup_dir": ctx["backup_dir"],
        "sandbox_id": ctx["sandbox_id"],
        "persona_id": ctx["persona_id"],
        "sources": ctx["sources"],
        "expected": ctx["expected"],
        "files": [
            {
                "kind": f["kind"],
                "source": f["source"],
                "src": f["src_rel"],
                "sha256": f["sha256"],
                "rows": f["rows"],
                "target": (
                    [rel_to(root, p) for p in f["targets"]]
                    if f["kind"] == "state" else rel_to(root, f["target"])
                ),
                "sandbox_id": f["sandbox_id"],
                "persona_id": f["persona_id"],
            }
            for f in ctx["files"]
        ],
        "targets": {k: rel_to(root, v) for k, v in ctx["targets"].items()},
    }
    if ctx.get("backup"):
        summary["backup"] = ctx["backup"]
    if ctx.get("result"):
        summary["post_sha256"] = ctx["result"].get("post_sha256", {})
        summary["written"] = ctx["result"].get("written", [])
        summary["layout"] = ctx["result"].get("layout")
    return summary


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run(args)


if __name__ == "__main__":
    raise SystemExit(main())
