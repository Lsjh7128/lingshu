# -*- coding: utf-8 -*-
"""LINGSHU 跨环境控制台 · 独立本地只读服务（API v1）。

    python console/server.py                 # 127.0.0.1:3000
    python console/server.py --port 3001 --open
    python console/server.py --check         # 只校验注册表并列出 profile，不起服务

为什么是独立进程而不是插件内嵌页面：
    1) 日常 DSH 的稳定性不能被观测面板拖累（面板崩了不能连累宿主）；
    2) 一个控制台要监控多个 profile（desktop / lingshu-lab / 混沌数据），
       数据源必须能从外部配置，而不是编译进某个插件；
    3) 安全边界清晰：只读文件 + 白名单脱敏，入口只有一个 HTTP 服务。

硬约束（与 `console/API.md` 一致）：
    - 只读：不写任何 profile 数据，HTTP 仅 GET/HEAD；POST 一律 405；
    - 脱敏在服务端：记忆原文/对话原文/注入预览永不出网，见 `console/redact.py`；
    - 路径不来自客户端：profile → 数据目录的映射只由 `profiles.json` 决定；
    - 只绑回环地址；校验 Host 头（防 DNS rebinding）；无 CORS；
    - 数据目录缺失 = `available:false`，绝不拿别的 profile 顶替，也不伪造 0。

设计文档：docs/25-跨环境控制台.md；契约：console/API.md。
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import socket
import sqlite3
import sys
import threading
import time
import webbrowser
from collections import Counter, defaultdict, deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
from console.redact import (ERROR_KINDS, FORBIDDEN_KEYS,  # noqa: E402
                            iso_of, redact_event, safe_kind)

VERSION = "1.0.0"
SERVICE = "lingshu-console"
DEFAULT_PORT = 3000
MAX_LINES = 200_000          # 单文件最多解析行数（超出标 truncated，不静默）
MAX_BYTES = 16 * 1024 * 1024  # 单文件最多读取字节
RECENT_EVENTS = 30
MAX_STREAMS_PER_PROFILE = 8
PROFILE_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")
ERROR_KIND_SET = ERROR_KINDS


# ─────────────────────────────────────────────────────────────
# 小工具
# ─────────────────────────────────────────────────────────────
def _safe_redact(rec) -> dict:
    """单条脱敏的兜底：任何异常都退化为空事件，绝不让一条坏记录打挂整个面板。"""
    try:
        return redact_event(rec)
    except Exception:  # noqa: BLE001 - 这里就是要兜住一切
        return {"ts": 0, "kind": "other", "fields": {}, "fields_omitted": True}


def _now_ms() -> int:
    return int(time.time() * 1000)


def _num(v, default=None):
    if isinstance(v, bool):
        return default
    if isinstance(v, (int, float)):
        if v != v or v in (float("inf"), float("-inf")):
            return default
        return round(float(v), 4)
    return default


def _int(v, default=0) -> int:
    n = _num(v)
    return int(n) if n is not None else default


def _safe_token(v, default="other") -> str:
    s = str(v or "").strip()
    if 0 < len(s) <= 40 and re.match(r"^[A-Za-z0-9_.:\-]+$", s):
        return s
    return default


def _safe_version(v):
    """版本号只允许 token 字符集；异常值置 null（state 里的任意字符串不得直出）。"""
    if v is None:
        return None
    s = str(v).strip()
    if 0 < len(s) <= 64 and re.match(r"^[A-Za-z0-9_.:+\-]+$", s):
        return s
    return None


def _iso(ts):
    return iso_of(ts)


def _display_path(path: str, repo: str, dsh_home: str, home: str) -> str:
    """路径脱敏：家目录 → ~，DSH_HOME → ~/.dsh，仓库 → <repo>。"""
    if not path:
        return ""
    p = os.path.normpath(str(path))
    pairs = [(dsh_home, "~/.dsh"), (repo, "<repo>"), (home, "~")]
    for base, label in pairs:
        if not base:
            continue
        b = os.path.normpath(base)
        if p == b:
            return label
        if p.startswith(b + os.sep):
            return label + p[len(b):]
    return p


def _stat(path: str) -> dict:
    try:
        s = os.stat(path)
        return {"exists": True, "bytes": int(s.st_size), "mtime": int(s.st_mtime * 1000)}
    except OSError:
        return {"exists": False, "bytes": 0, "mtime": None}


def _fingerprint_files(paths):
    out = []
    for p in paths:
        if not p:
            out.append(None)
            continue
        st = _stat(p)
        out.append((st["exists"], st["bytes"], st["mtime"]))
    return tuple(out)


class RegistryError(Exception):
    pass


# ─────────────────────────────────────────────────────────────
# 注册表
# ─────────────────────────────────────────────────────────────
def _expand(value, repo, dsh_home, home):
    if not isinstance(value, str):
        return value
    mapping = {
        "REPO": repo,
        "DSH_HOME": dsh_home,
        "USERPROFILE": home,
        "HOME": home,
        "TMP": os.environ.get("TEMP") or os.environ.get("TMP") or home,
    }

    def repl(m):
        name = m.group(1)
        if name not in mapping or not mapping[name]:
            raise RegistryError("未知或为空的路径变量 ${%s}" % name)
        return mapping[name]

    return re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}", repl, value)


class ProfileRegistry:
    def __init__(self, path, repo, dsh_home, home):
        self.path = os.path.abspath(path)
        self.repo = repo
        self.dsh_home = dsh_home
        self.home = home
        self.default_profile = None
        self.refresh_hint_ms = 1500
        self.profiles = []
        self.warnings = []
        self._load()

    # -- 读取与校验 --
    def _read_json(self, path):
        try:
            with open(path, "r", encoding="utf-8-sig") as fh:
                return json.load(fh)
        except FileNotFoundError:
            raise RegistryError("注册表不存在：%s" % path)
        except json.JSONDecodeError as exc:
            raise RegistryError("注册表 JSON 解析失败：%s（%s）" % (path, exc))

    def _load(self):
        base = self._read_json(self.path)
        local = os.path.join(os.path.dirname(self.path), "profiles.local.json")
        if os.path.isfile(local):
            override = self._read_json(local)
            merged = {p.get("id"): p for p in (base.get("profiles") or [])}
            for p in (override.get("profiles") or []):
                merged[p.get("id")] = p
            base["profiles"] = list(merged.values())
            if override.get("default_profile"):
                base["default_profile"] = override["default_profile"]
            self.warnings.append("local_override:%s" % os.path.basename(local))

        self.refresh_hint_ms = int(base.get("refresh_hint_ms") or 1500)
        self.default_profile = base.get("default_profile")
        seen = set()
        for raw in base.get("profiles") or []:
            if not isinstance(raw, dict):
                raise RegistryError("profiles[] 里有非对象项")
            pid = str(raw.get("id") or "")
            if not PROFILE_ID_RE.match(pid):
                raise RegistryError("非法 profile id：%r（要求 ^[a-z0-9][a-z0-9_-]{0,31}$）" % raw.get("id"))
            if pid in seen:
                raise RegistryError("profile id 重复：%s" % pid)
            seen.add(pid)
            src = raw.get("source") or {}
            stype = str(src.get("type") or "jsonl")
            if stype not in ("jsonl", "sqlite"):
                raise RegistryError("profile %s 的 source.type 不支持：%s" % (pid, stype))
            if stype == "jsonl":
                dirs = src.get("dirs")
                if not dirs:
                    if src.get("dir"):
                        dirs = [src["dir"]]
                    else:
                        raise RegistryError("profile %s 缺少 source.dirs" % pid)
                if not isinstance(dirs, list) or not all(isinstance(x, str) for x in dirs):
                    raise RegistryError("profile %s 的 source.dirs 必须是字符串数组" % pid)
                src = dict(src)
                src["dirs"] = [_expand(x, self.repo, self.dsh_home, self.home) for x in dirs]
            else:
                if not src.get("path"):
                    raise RegistryError("profile %s 的 sqlite 源缺少 source.path" % pid)
                src = dict(src)
                src["path"] = _expand(src["path"], self.repo, self.dsh_home, self.home)
            item = dict(raw)
            item["source"] = src
            item["stale_after_sec"] = int(raw.get("stale_after_sec") or 300)
            self.profiles.append(item)

        if not self.profiles:
            raise RegistryError("注册表里没有任何 profile")
        if not self.default_profile or self.default_profile not in seen:
            self.default_profile = self.profiles[0]["id"]

    def get(self, pid):
        for p in self.profiles:
            if p["id"] == pid:
                return p
        return None


# ─────────────────────────────────────────────────────────────
# 数据源适配器（jsonl / sqlite）
# ─────────────────────────────────────────────────────────────
class JsonlFile:
    """流式读取 JSONL，带行数/字节上限；坏行计数但不炸。"""

    def __init__(self, path, max_lines=MAX_LINES, max_bytes=MAX_BYTES):
        self.path = path
        self.max_lines = max_lines
        self.max_bytes = max_bytes
        self.lines = 0
        self.unparsable = 0
        self.truncated = False
        self.exists = os.path.isfile(path)

    def records(self):
        if not self.exists:
            return
        try:
            with open(self.path, "rb") as fh:
                for raw in fh:
                    if self.lines >= self.max_lines or fh.tell() > self.max_bytes:
                        self.truncated = True
                        break
                    self.lines += 1
                    text = raw.decode("utf-8-sig", "replace").strip()
                    if not text:
                        continue
                    try:
                        rec = json.loads(text)
                    except json.JSONDecodeError:
                        self.unparsable += 1
                        continue
                    yield rec
        except OSError:
            return


class JsonlSource:
    kind = "jsonl"

    def __init__(self, candidate_dirs):
        self.candidate_dirs = candidate_dirs
        self.dir = None
        for d in candidate_dirs:
            if os.path.isdir(d):
                self.dir = d
                break
        self.fallback = bool(self.dir and os.path.normpath(self.dir)
                             != os.path.normpath(candidate_dirs[0]))
        self.state_path = os.path.join(self.dir, "state.json") if self.dir else None
        self.memory_path = os.path.join(self.dir, "memory.jsonl") if self.dir else None
        self.audit_path = os.path.join(self.dir, "audit.jsonl") if self.dir else None
        self.mem = JsonlFile(self.memory_path) if self.memory_path else None
        self.aud = JsonlFile(self.audit_path) if self.audit_path else None

    @property
    def available(self):
        return bool(self.dir)

    def reason(self):
        return None if self.available else "data_dir_missing"

    def files(self):
        return {
            "state.json": _stat(self.state_path) if self.state_path else {"exists": False, "bytes": 0, "mtime": None},
            "memory.jsonl": _stat(self.memory_path) if self.memory_path else {"exists": False, "bytes": 0, "mtime": None},
            "audit.jsonl": _stat(self.audit_path) if self.audit_path else {"exists": False, "bytes": 0, "mtime": None},
        }

    def read_state(self):
        if not self.state_path or not os.path.isfile(self.state_path):
            return None
        try:
            with open(self.state_path, "r", encoding="utf-8-sig") as fh:
                obj = json.load(fh)
            return obj if isinstance(obj, dict) else None
        except (OSError, json.JSONDecodeError):
            return None

    def iter_memory(self):
        return self.mem.records() if self.mem else iter(())

    def iter_audit(self):
        return self.aud.records() if self.aud else iter(())

    def meta(self):
        return {
            "memory": {"unparsable": self.mem.unparsable if self.mem else 0,
                       "truncated": self.mem.truncated if self.mem else False},
            "audit": {"unparsable": self.aud.unparsable if self.aud else 0,
                      "truncated": self.aud.truncated if self.aud else False},
        }

    def fingerprint_paths(self):
        return [self.state_path, self.memory_path, self.audit_path]


class SqliteSource:
    """实验性 SQLite 适配器（契约见 docs/25 §6）。

    期望表：`memory(id,status,origin,type,layer,lastAccess)`、
    `audit(ts,kind,json)`（json 列是事件 JSON 文本）、`state(key,value)`（value 是 JSON 文本）。
    表缺失 → available:false / reason:sqlite_schema_missing，不做猜测。
    """
    kind = "sqlite"

    def __init__(self, path):
        self.path = path
        self._ok = os.path.isfile(path)
        self._schema = {}
        if self._ok:
            try:
                with self._conn() as conn:
                    names = [r[0] for r in conn.execute(
                        "SELECT name FROM sqlite_master WHERE type='table'")]
                    self._schema = set(names)
            except sqlite3.Error:
                self._ok = False

    @contextlib.contextmanager
    def _conn(self):
        uri = Path(self.path).resolve().as_uri() + "?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        try:
            yield conn
        finally:
            conn.close()

    @property
    def available(self):
        return self._ok and "memory" in self._schema and "audit" in self._schema

    def reason(self):
        if not os.path.isfile(self.path):
            return "data_dir_missing"
        return None if self.available else "sqlite_schema_missing"

    def files(self):
        return {os.path.basename(self.path): _stat(self.path)}

    def _columns(self, table):
        try:
            with self._conn() as conn:
                return {r[1] for r in conn.execute("PRAGMA table_info(%s)" % table)}
        except sqlite3.Error:
            return set()

    def read_state(self):
        if "state" not in self._schema:
            return None
        try:
            with self._conn() as conn:
                rows = list(conn.execute("SELECT key, value FROM state"))
        except sqlite3.Error:
            return None
        out = {}
        for k, v in rows:
            try:
                out[str(k)] = json.loads(v)
            except (TypeError, json.JSONDecodeError):
                out[str(k)] = v
        return out

    def iter_memory(self):
        cols = self._columns("memory")
        pick = [c for c in ("id", "status", "origin", "type", "layer", "lastAccess") if c in cols]
        if not pick:
            return iter(())
        try:
            with self._conn() as conn:
                rows = list(conn.execute("SELECT %s FROM memory" % ",".join(pick)))
        except sqlite3.Error:
            return iter(())
        return iter([dict(zip(pick, r)) for r in rows])

    def iter_audit(self):
        cols = self._columns("audit")
        if "ts" in cols and "kind" in cols and "json" in cols:
            try:
                with self._conn() as conn:
                    rows = list(conn.execute("SELECT ts, kind, json FROM audit ORDER BY ts"))
            except sqlite3.Error:
                return iter(())
            out = []
            for ts, kind, blob in rows:
                rec = {"ts": ts, "kind": kind}
                if isinstance(blob, (str, bytes)):
                    try:
                        extra = json.loads(blob)
                        if isinstance(extra, dict):
                            rec.update(extra)
                    except (TypeError, json.JSONDecodeError):
                        pass
                out.append(rec)
            return iter(out)
        return iter(())

    def meta(self):
        return {"memory": {"unparsable": 0, "truncated": False},
                "audit": {"unparsable": 0, "truncated": False}}

    def fingerprint_paths(self):
        return [self.path]


def make_source(cfg, repo, dsh_home, home):
    src = cfg.get("source") or {}
    if src.get("type") == "sqlite":
        return SqliteSource(src.get("path"))
    dirs = src.get("dirs") or ([src["dir"]] if src.get("dir") else [])
    return JsonlSource(dirs)


# ─────────────────────────────────────────────────────────────
# 聚合（只出计数与枚举，不出原文）
# ─────────────────────────────────────────────────────────────
def _top(counter, limit=8):
    items = [{"key": k, "n": int(v)} for k, v in counter.most_common(limit)]
    return items


def aggregate_memory(source):
    observed = bool(source.mem and source.mem.exists) if isinstance(source, JsonlSource) \
        else source.available
    result = {
        "observed": bool(observed),
        "total_rows": 0, "unique": 0, "active": 0, "superseded": 0, "suppressed": 0,
        "unknown_status": 0, "unparsable": 0,
        "truncated": False,
        "by_origin": [], "by_type": [], "by_layer": [], "by_status": [],
        "last_write_ts": None,
    }
    if not observed:
        return result
    by_id = {}
    rows = 0
    for rec in source.iter_memory():
        if not isinstance(rec, dict):
            continue
        mid = str(rec.get("id") or "")
        if not mid:
            continue
        rows += 1
        by_id[mid] = rec
    statuses = Counter()
    origins = Counter()
    types = Counter()
    layers = Counter()
    for rec in by_id.values():
        st = _safe_token(rec.get("status"), "unknown")
        statuses[st] += 1
        origins[_safe_token(rec.get("origin"), "other")] += 1
        types[_safe_token(rec.get("type"), "other")] += 1
        layers[_safe_token(rec.get("layer"), "other")] += 1
    result.update({
        "total_rows": rows,
        "unique": len(by_id),
        "active": statuses.get("active", 0),
        "superseded": statuses.get("superseded", 0),
        "suppressed": statuses.get("suppressed", 0),
        "unknown_status": sum(v for k, v in statuses.items()
                              if k not in ("active", "superseded", "suppressed")),
        "by_origin": _top(origins),
        "by_type": _top(types),
        "by_layer": _top(layers),
        "by_status": _top(statuses),
    })
    if isinstance(source, JsonlSource) and source.memory_path:
        st = _stat(source.memory_path)
        result["last_write_ts"] = st["mtime"]
    mem_meta = source.meta().get("memory", {})
    result["unparsable"] = int(mem_meta.get("unparsable") or 0)
    result["truncated"] = bool(mem_meta.get("truncated"))
    return result


def aggregate_audit(source, limit=RECENT_EVENTS):
    hist = Counter()
    errors = 0
    total = 0
    non_dict = 0
    recent = deque(maxlen=max(1, limit))
    phase_alerts = 0
    last_alert_ts = None
    last_stpa = None
    observed = bool(source.aud and source.aud.exists) if isinstance(source, JsonlSource) \
        else source.available
    for rec in source.iter_audit():
        total += 1
        if not isinstance(rec, dict):
            non_dict += 1
            continue
        kind = rec.get("kind")
        safe = safe_kind(kind)
        hist[safe] += 1
        if kind in ERROR_KIND_SET:
            errors += 1
        if kind == "phase_alert":
            phase_alerts += 1
            ts = _int(rec.get("ts"))
            if ts and (last_alert_ts is None or ts > last_alert_ts):
                last_alert_ts = ts
        if kind in ("pre_step", "preflight_block", "stpa_observe"):
            last_stpa = rec
        recent.append(rec)
    aud_meta = source.meta().get("audit", {})
    result = {
        "observed": bool(observed),
        "total": total,
        "unparsable": int(aud_meta.get("unparsable") or 0) + non_dict,
        "errors": errors,
        "truncated": bool(aud_meta.get("truncated")),
        "by_kind": [{"kind": k, "n": int(v)} for k, v in hist.most_common(12)],
        "recent": [_safe_redact(r) for r in recent],
        "events_limit": int(limit),
        "phase_alerts": phase_alerts,
        "last_alert_ts": last_alert_ts,
        "_last_stpa_raw": last_stpa,
    }
    return result


def load_thresholds(repo):
    """阈值来自 rules/decay-params.yaml#control（单一来源）；解析失败回退默认并标 source。"""
    path = os.path.join(repo, "rules", "decay-params.yaml")
    shift, hard = 0.70, 0.85
    source = "default"
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            text = fh.read()
        m1 = re.search(r"^\s*phaseShiftRatio:\s*([0-9.]+)", text, re.M)
        m2 = re.search(r"^\s*phaseShiftHardRatio:\s*([0-9.]+)", text, re.M)
        if m1 and m2:
            shift, hard = float(m1.group(1)), float(m2.group(1))
            source = "rules/decay-params.yaml"
    except OSError:
        pass
    st = _stat(path)
    return ({"shift": shift, "hard": hard}, source, st["mtime"])


# ─────────────────────────────────────────────────────────────
# 应用主体
# ─────────────────────────────────────────────────────────────
class ConsoleApp:
    def __init__(self, registry_path, repo, dsh_home, stream_interval=1.5,
                 recent_limit=RECENT_EVENTS, default_profile=None):
        self.repo = os.path.abspath(repo)
        self.dsh_home = os.path.abspath(dsh_home)
        self.home = os.path.expanduser("~")
        self.stream_interval = max(0.5, float(stream_interval))
        self.recent_limit = max(1, int(recent_limit))
        self.registry = ProfileRegistry(registry_path, self.repo, self.dsh_home, self.home)
        self.default_profile_override = None
        if default_profile:
            if self.registry.get(str(default_profile)) is None:
                raise RegistryError("--default-profile 指向不存在的 profile：%s" % default_profile)
            self.default_profile_override = str(default_profile)
        self.thresholds, self.threshold_source, self.threshold_mtime = load_thresholds(self.repo)
        self._cache = {}
        self._cache_lock = threading.Lock()
        self._streams = defaultdict(int)
        self._streams_lock = threading.Lock()
        self.started_at = time.time()

    # -- 连接计数（SSE 限流） --
    def acquire_stream(self, pid):
        with self._streams_lock:
            if self._streams[pid] >= MAX_STREAMS_PER_PROFILE:
                return False
            self._streams[pid] += 1
            return True

    def release_stream(self, pid):
        with self._streams_lock:
            self._streams[pid] = max(0, self._streams.get(pid, 0) - 1)

    def stream_count(self):
        with self._streams_lock:
            return sum(self._streams.values())

    # -- profile 视图 --
    def _profile_entry(self, cfg):
        src = make_source(cfg, self.repo, self.dsh_home, self.home)
        files = src.files()
        mtimes = [f["mtime"] for f in files.values() if f.get("mtime")]
        last_write = max(mtimes) if mtimes else None
        age = None
        if last_write:
            age = max(0, (_now_ms() - last_write) // 1000)
        stale_after = int(cfg.get("stale_after_sec") or 300)
        warnings = []
        if not src.available:
            warnings.append("source_unavailable")
        elif isinstance(src, JsonlSource) and src.fallback:
            warnings.append("legacy_flat_dir")
        if src.available and not any(f.get("exists") for f in files.values()):
            warnings.append("state_missing")
        # 目录还没出现时也要显示"计划中的路径"，否则用户不知道去哪里找/建（API v1 允许 display 为计划路径）。
        if isinstance(src, JsonlSource):
            shown_dir = src.dir or (src.candidate_dirs[0] if src.candidate_dirs else None)
            resolved_dir = src.dir
        else:
            shown_dir = src.path
            resolved_dir = None
        entry = {
            "id": cfg["id"],
            "label": str(cfg.get("label") or cfg["id"]),
            "kind": str(cfg.get("kind") or "runtime"),
            "mode": str(cfg.get("mode") or "active"),
            "color": str(cfg.get("color") or "#38bdf8"),
            "available": bool(src.available),
            "reason": src.reason(),
            "hint": (str(cfg.get("hint") or "") or None),
            "source": {
                "type": src.kind,
                "resolved_dir": _display_path(resolved_dir, self.repo, self.dsh_home, self.home)
                if resolved_dir else None,
                "display": _display_path(shown_dir, self.repo, self.dsh_home, self.home),
            },
            "stale_after_sec": stale_after,
            "last_write_ts": last_write,
            "data_age_sec": age,
            "stale": bool(age is not None and age > stale_after) or not src.available,
            "files": files,
            "warnings": warnings,
        }
        if not src.available:
            entry["hint"] = entry["hint"] or (
                "数据目录尚未出现：日常接入请人工运行 scripts/console_desktop_onboard.ps1；"
                "演示请运行 scripts/console_demo.ps1")
        return entry, src

    def profiles_payload(self):
        entries = []
        for cfg in self.registry.profiles:
            entry, _src = self._profile_entry(cfg)
            entries.append(entry)
        return {
            "ok": True,
            "generated_at": _now_ms(),
            "repo": _display_path(self.repo, self.repo, self.dsh_home, self.home),
            "dsh_home": _display_path(self.dsh_home, self.repo, self.dsh_home, self.home),
            "registry": _display_path(self.registry.path, self.repo, self.dsh_home, self.home),
            "privacy": {
                "policy": "allowlist-v1", "raw_text": False,
                "omitted_fields": sorted(FORBIDDEN_KEYS),
            },
            "thresholds": dict(self.thresholds),
            "threshold_source": self.threshold_source,
            "registry_warnings": list(self.registry.warnings),
            "refresh_hint_ms": self.registry.refresh_hint_ms,
            "default_profile": self.default_profile_override or self.registry.default_profile,
            "profiles": entries,
        }

    # -- 快照 --
    def _cache_key(self, src, cfg):
        paths = list(src.fingerprint_paths())
        return (cfg["id"], tuple(_fingerprint_files(paths)),
                self.threshold_mtime)

    def snapshot(self, pid):
        cfg = self.registry.get(pid)
        if not cfg:
            raise KeyError(pid)
        entry, src = self._profile_entry(cfg)
        key = self._cache_key(src, cfg)
        now = time.time()
        with self._cache_lock:
            hit = self._cache.get(pid)
            if hit and hit["key"] == key and now - hit["built_at"] < 0.8:
                return hit["snapshot"]
        snap = self._build_snapshot(entry, src, cfg)
        with self._cache_lock:
            self._cache[pid] = {"key": key, "built_at": now, "snapshot": snap}
        return snap

    def _build_snapshot(self, entry, src, cfg):
        now_ms = _now_ms()
        warnings = list(entry.get("warnings") or [])
        state = src.read_state()
        memory = aggregate_memory(src)
        audit = aggregate_audit(src, self.recent_limit)
        if memory.get("unparsable"):
            warnings.append("unparsable_lines")
        if memory.get("truncated") or audit.get("truncated"):
            warnings.append("count_truncated")
        if state is None:
            warnings.append("state_missing")
        if self.threshold_source == "default":
            warnings.append("threshold_default")
        if isinstance(src, JsonlSource) and src.fallback:
            warnings.append("legacy_flat_dir")

        control = (state or {}).get("control") or {}
        phase_raw = control.get("phase") if isinstance(control.get("phase"), dict) else {}
        observed_phase = bool(phase_raw) and ("ratio" in phase_raw or "phase" in phase_raw)
        ratio = _num(phase_raw.get("ratio"), 0.0) if observed_phase else None
        window = _int(control.get("contextWindow") or (state or {}).get("contextWindow")
                      or 0, 0)
        used_est = int(round(ratio * window)) if (observed_phase and ratio is not None and window) else None
        phase = {
            "observed": observed_phase,
            "ratio": ratio,
            "phase": _safe_token(phase_raw.get("phase"), "unknown") if observed_phase else "unknown",
            "trend": _num(phase_raw.get("trend"), 0.0) if observed_phase else None,
            "anchor_required": bool(phase_raw.get("anchorRequired")) if observed_phase else False,
            "prefeed": bool(phase_raw.get("prefeed")) if observed_phase else False,
            "context_window": window or None,
            "used_tokens_est": used_est,
            "thresholds": dict(self.thresholds),
            "alerts": int(audit.get("phase_alerts") or 0),
            "last_alert_at": audit.get("last_alert_ts"),
            "source": "plugin_state" if observed_phase else "absent",
        }

        stpa = self._build_stpa(control, audit)
        turns = {
            "count": _int((state or {}).get("turns")),
            "state_writes": _int((state or {}).get("stateWrites")),
            "sessions": _int((state or {}).get("sessions")),
            "stage": None,
        }
        if state is not None:
            n = turns["count"]
            turns["stage"] = "cold" if n < 3 else ("warm" if n < 20 else "regular")

        fingerprint = {
            "pid": cfg["id"],
            "available": entry["available"],
            "memory": [memory.get("unique"), memory.get("active"), memory.get("superseded"),
                       memory.get("suppressed"), memory.get("total_rows")],
            "turns": turns["count"],
            "phase": [phase.get("ratio"), phase.get("phase"), phase.get("observed")],
            "audit": audit.get("total"),
            "stpa": [stpa.get("verdict"), stpa.get("observed")],
            "warnings": sorted(set(warnings)),
        }
        revision = hashlib.sha1(
            json.dumps(fingerprint, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()[:16]

        data_asof = entry.get("last_write_ts")
        snap = {
            "ok": True,
            "profile": entry,
            "generated_at": now_ms,
            "revision": revision,
            "privacy": {"policy": "allowlist-v1", "raw_text": False,
                        "omitted_fields": sorted(FORBIDDEN_KEYS)},
            "freshness": {
                "data_asof": data_asof,
                "data_age_sec": entry.get("data_age_sec"),
                "stale": bool(entry.get("stale")),
                "sources": [{"file": k, **v} for k, v in (entry.get("files") or {}).items()],
            },
            "warnings": sorted(set(warnings)),
            "turns": turns,
            "memory": memory,
            "phase": phase,
            "stpa": stpa,
            "audit": audit,
            "versions": {
                "param_version": _safe_version((state or {}).get("paramVersion")
                                               or (state or {}).get("param_version")),
                "prompt_version": _safe_version((state or {}).get("promptVersion")
                                                or (state or {}).get("prompt_version")),
                "compose_version": _safe_version((state or {}).get("composeVersion")
                                                 or (state or {}).get("compose_version")),
            },
        }
        return snap

    def _build_stpa(self, control, audit):
        """STPA 摘要：优先 observe 模式的 control.stpa，其次 active 模式的 control.audit。"""
        raw = None
        mode = "active"
        if isinstance(control.get("stpa"), dict) and control.get("stpa"):
            raw = control["stpa"]
            mode = "observe"
        elif isinstance(control.get("audit"), dict) and control.get("audit"):
            raw = control["audit"]
        else:
            last = audit.pop("_last_stpa_raw", None)
            if isinstance(last, dict):
                raw = last
                mode = "observe" if last.get("kind") == "stpa_observe" else "active"
        # 内部字段不随快照出网（A3 联调发现 `_last_stpa_raw` 裸露；它虽经 redact 但不应出现在契约里）
        audit.pop("_last_stpa_raw", None)
        if not raw:
            return {"observed": False, "verdict": "unknown", "ucas": [], "downgrades": [],
                    "blocked": False, "fail_mode": "unknown", "sources": {},
                    "last_at": None, "mode": mode}
        sources_raw = raw.get("sources") if isinstance(raw.get("sources"), dict) else {}
        sources = {}
        for k, v in list(sources_raw.items())[:8]:
            key = str(k)
            if not re.match(r"^[A-Za-z0-9_]{1,24}$", key):
                continue
            if isinstance(v, bool):
                sources[key] = v
            elif isinstance(v, str) and re.match(r"^[a-z_+]{1,32}$", v):
                sources[key] = v
            else:
                sources[key] = bool(v)
        return {
            "observed": True,
            "verdict": _safe_token(raw.get("verdict"), "unknown"),
            "ucas": [x for x in (_safe_token(v, "") for v in (raw.get("ucas") or [])) if x][:12],
            "downgrades": [x for x in (_safe_token(v, "") for v in (raw.get("downgrades") or [])) if x][:12],
            "blocked": bool(raw.get("blocked")),
            "fail_mode": _safe_token(raw.get("failMode") or raw.get("fail_mode"), "unknown"),
            "sources": sources,
            "last_at": _int(raw.get("at") or raw.get("ts") or 0) or None,
            "mode": mode,
        }

    # -- 事件（单独端点，可指定条数） --
    def events(self, pid, limit):
        cfg = self.registry.get(pid)
        if not cfg:
            raise KeyError(pid)
        _entry, src = self._profile_entry(cfg)
        if not src.available:
            return {"ok": True, "profile_id": pid, "events": [], "available": False}
        recs = deque(maxlen=max(1, min(200, int(limit))))
        for rec in src.iter_audit():
            if isinstance(rec, dict):
                recs.append(rec)
        # API 契约（API.md §2.4）：**最新在前**。deque 是文件顺序（最旧→最新），必须反转。
        events = [_safe_redact(r) for r in list(recs)[::-1]]
        return {"ok": True, "profile_id": pid, "events": events, "available": True}

    def health(self):
        return {
            "ok": True, "service": SERVICE, "version": VERSION,
            "uptime_s": int(time.time() - self.started_at),
            "read_only": True, "profile_count": len(self.registry.profiles),
            "streams": self.stream_count(),
        }


# ─────────────────────────────────────────────────────────────
# HTTP 层
# ─────────────────────────────────────────────────────────────
STATIC_FILES = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/app.js": ("app.js", "application/javascript; charset=utf-8"),
    "/app.css": ("app.css", "text/css; charset=utf-8"),
    "/favicon.svg": ("favicon.svg", "image/svg+xml"),
}


class ConsoleHTTPServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, addr, handler, app):
        self.app = app
        super().__init__(addr, handler)


class Handler(BaseHTTPRequestHandler):
    server_version = "LingshuConsole/" + VERSION
    sys_version = ""                     # 不暴露 Python 版本（Server 头只留服务名/版本）
    protocol_version = "HTTP/1.1"

    # -- 基础设施 --
    def log_message(self, fmt, *args):  # 不把访问日志写成满屏
        if getattr(self.server, "quiet", False):
            return
        sys.stderr.write("[console] %s - %s\n" % (self.address_string(), fmt % args))

    def _security_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Content-Security-Policy",
                         "default-src 'self'; img-src 'self' data:; "
                         "style-src 'self' 'unsafe-inline'; script-src 'self'; "
                         "frame-ancestors 'none'; base-uri 'self'")

    def _host_ok(self):
        """Host 精确匹配回环（API.md §1）：拒绝 `127.0.0.1:80@evil.com`、错误端口、尾随串。"""
        host = (self.headers.get("Host") or "").strip().lower()
        if not host:
            return False
        m = re.match(r"^(127\.0\.0\.1|localhost|\[::1\])(?::(\d{1,5}))?$", host)
        if not m:
            return False
        port = m.group(2)
        if port is None:
            return True
        try:
            return int(port) == int(self.server.server_address[1])
        except (TypeError, ValueError):
            return False

    def _send_json(self, obj, status=200, head=False):
        body = json.dumps(obj, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        head = head or self.command == "HEAD"    # HTTP/1.1：HEAD 绝不能带 body
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _error(self, status, code, message, hint=None):
        payload = {"ok": False, "code": code, "message": message}
        if hint:
            payload["hint"] = hint
        self._send_json(payload, status=status)

    def _method_not_allowed(self):
        self.send_response(405)
        self.send_header("Allow", "GET, HEAD")
        self.send_header("Content-Type", "application/json; charset=utf-8")
        body = json.dumps({"ok": False, "code": "method_not_allowed",
                           "message": "控制台只读：仅支持 GET/HEAD"},
                          ensure_ascii=False).encode("utf-8")
        self.send_header("Content-Length", str(len(body)))
        self._security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_POST = _method_not_allowed
    do_PUT = _method_not_allowed
    do_DELETE = _method_not_allowed
    do_PATCH = _method_not_allowed
    do_OPTIONS = _method_not_allowed

    def _profile_arg(self, qs):
        vals = qs.get("profile") or []
        if not vals:
            self._error(400, "bad_request", "缺少 profile 参数",
                        "示例：/api/snapshot?profile=desktop")
            return None
        pid = vals[0].strip()[:64]
        if not PROFILE_ID_RE.match(pid):
            self._error(404, "unknown_profile", "profile id 不合法")
            return None
        if self.server.app.registry.get(pid) is None:
            self._error(404, "unknown_profile", "未知 profile：%s" % pid,
                        "可用 profile 见 /api/profiles")
            return None
        return pid

    # -- GET --
    def do_GET(self):
        if not self._host_ok():
            self._error(421, "bad_host", "Host 头不被接受（只允许回环地址）")
            return
        parsed = urlsplit(self.path)
        path = unquote(parsed.path or "/")
        qs = parse_qs(parsed.query)
        app = self.server.app
        try:
            if path == "/api/health":
                self._send_json(app.health()); return
            if path == "/api/profiles":
                self._send_json(app.profiles_payload()); return
            if path == "/api/snapshot":
                pid = self._profile_arg(qs)
                if not pid:
                    return
                self._send_json(app.snapshot(pid)); return
            if path == "/api/events":
                pid = self._profile_arg(qs)
                if not pid:
                    return
                try:
                    limit = int((qs.get("limit") or ["100"])[0])
                except ValueError:
                    limit = 100
                self._send_json(app.events(pid, limit)); return
            if path == "/api/stream":
                if self.command == "HEAD":
                    self._error(400, "bad_request", "HEAD 不支持流式端点")
                    return
                pid = self._profile_arg(qs)
                if not pid:
                    return
                self._serve_stream(pid, once=bool(qs.get("once")))
                return
            entry = STATIC_FILES.get(path)
            if entry:
                self._serve_static(*entry)
                return
            self._error(404, "not_found", "路径不存在")
        except BrokenPipeError:
            return
        except Exception:  # 任何意外都不让服务进程退出，也不回显异常原文（防止脱敏旁路）
            import traceback
            traceback.print_exc(file=sys.stderr)
            try:
                self._error(500, "internal",
                            "服务内部错误（详情见服务端 stderr；出于脱敏考虑不回显异常原文）")
            except Exception:
                pass

    def do_HEAD(self):
        self.do_GET()

    def _serve_static(self, name, ctype):
        path = os.path.join(_HERE, "ui", name)
        try:
            with open(path, "rb") as fh:
                body = fh.read()
        except OSError:
            self._error(503, "internal", "前端文件缺失：%s" % name,
                        "确认 console/ui/ 已随仓库交付（index.html / app.js / app.css）")
            return
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self._security_headers()
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _serve_stream(self, pid, once=False):
        app = self.server.app
        if not app.acquire_stream(pid):
            self._error(503, "too_many_streams", "该 profile 的实时连接已满（8）",
                        "关闭多余的控制台页面后重试")
            return
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "close" if once else "keep-alive")
            self.send_header("X-Accel-Buffering", "no")
            self._security_headers()
            self.end_headers()
            if once:
                self.close_connection = True
            first = True
            last_rev = None
            last_write = time.time()
            while True:
                snap = app.snapshot(pid)
                rev = snap.get("revision")
                if first or rev != last_rev:
                    payload = json.dumps(snap, ensure_ascii=False, separators=(",", ":"))
                    self.wfile.write(("event: snapshot\ndata: " + payload + "\n\n").encode("utf-8"))
                    self.wfile.flush()
                    last_rev = rev
                    last_write = time.time()
                    first = False
                elif time.time() - last_write > 15:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    last_write = time.time()
                if once:
                    return
                time.sleep(app.stream_interval)
                if self._peer_closed():
                    return
        except (BrokenPipeError, ConnectionResetError, ConnectionAbortedError):
            return
        finally:
            app.release_stream(pid)

    def _peer_closed(self):
        try:
            import select
            r, _, _ = select.select([self.connection], [], [], 0)
            if not r:
                return False
            data = self.connection.recv(1, socket.MSG_PEEK)
            return len(data) == 0
        except OSError:
            return True


# ─────────────────────────────────────────────────────────────
# CLI
# ─────────────────────────────────────────────────────────────
def _is_loopback(host):
    return host in ("127.0.0.1", "localhost", "::1", "[::1]")


def build_app(args):
    repo = os.path.abspath(args.repo or _ROOT)
    dsh_home = os.path.abspath(
        args.dsh_home or os.environ.get("DSH_HOME")
        or os.path.join(os.path.expanduser("~"), ".dsh"))
    registry = os.path.abspath(
        args.registry or os.path.join(_HERE, "profiles.json"))
    return ConsoleApp(registry, repo, dsh_home,
                      stream_interval=args.interval,
                      recent_limit=args.recent,
                      default_profile=args.default_profile)


def _print_check(app, args):
    payload = app.profiles_payload()
    print("注册表：%s" % payload["registry"])
    print("仓库：%s ｜ DSH_HOME：%s" % (payload["repo"], payload["dsh_home"]))
    print("阈值：shift=%.2f hard=%.2f（来源：%s）"
          % (payload["thresholds"]["shift"], payload["thresholds"]["hard"],
             payload["threshold_source"]))
    print("默认 profile：%s" % payload["default_profile"])
    print("")
    width = max(len(p["id"]) for p in payload["profiles"]) if payload["profiles"] else 8
    for p in payload["profiles"]:
        state = "可用" if p["available"] else "未接入(%s)" % (p["reason"] or "?")
        print("  %-*s  %-8s  %-7s  %s" % (width, p["id"], p["kind"], p["mode"], state))
        print("      %s" % (p["source"]["display"] or "-"))
    if not any(p["available"] for p in payload["profiles"]):
        print("")
        print("提示：当前没有可用的数据源。演示用：python tools/console_fixture.py --write")
        return 1
    return 0


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass
    ap = argparse.ArgumentParser(description="LINGSHU 跨环境控制台（只读本地服务）")
    ap.add_argument("--host", default="127.0.0.1", help="绑定地址（默认 127.0.0.1）")
    ap.add_argument("--port", type=int, default=DEFAULT_PORT, help="端口（默认 3000）")
    ap.add_argument("--registry", default="", help="profile 注册表路径（默认 console/profiles.json）")
    ap.add_argument("--repo", default="", help="仓库根（默认 console/ 的上级目录）")
    ap.add_argument("--dsh-home", default="", help="DSH_HOME（默认环境变量或 ~/.dsh）")
    ap.add_argument("--interval", type=float, default=1.5, help="SSE 推送间隔秒（默认 1.5）")
    ap.add_argument("--recent", type=int, default=RECENT_EVENTS, help="快照里的事件条数（默认 30）")
    ap.add_argument("--default-profile", default="", help="覆盖默认选中的 profile（演示/验收用）")
    ap.add_argument("--open", action="store_true", help="启动后打开默认浏览器")
    ap.add_argument("--allow-remote", action="store_true",
                    help="允许非回环绑定（不推荐：控制台只设计给本机用）")
    ap.add_argument("--check", action="store_true", help="只校验注册表与数据源，不起服务")
    ap.add_argument("--quiet", action="store_true", help="不打印访问日志")
    args = ap.parse_args(argv)

    if not _is_loopback(args.host) and not args.allow_remote:
        print("[FAIL] 控制台只允许绑定回环地址；确需远程请显式 --allow-remote（不推荐）",
              file=sys.stderr)
        return 2
    try:
        app = build_app(args)
    except RegistryError as exc:
        print("[FAIL] 注册表错误：%s" % exc, file=sys.stderr)
        return 2

    if args.check:
        return _print_check(app, args)

    try:
        httpd = ConsoleHTTPServer((args.host, args.port), Handler, app)
    except OSError as exc:
        print("[FAIL] 无法监听 %s:%d —— %s" % (args.host, args.port, exc), file=sys.stderr)
        print("       端口可能被占用：换一个端口，例如 --port 3001", file=sys.stderr)
        return 2
    httpd.quiet = args.quiet
    url = "http://%s:%d/" % ("127.0.0.1" if args.host == "0.0.0.0" else args.host, args.port)
    print("LINGSHU 控制台 · 只读 · 本机 %s" % url)
    print("  profile：%s" % " / ".join(
        "%s(%s)" % (p["id"], "可用" if p["available"] else "未接入")
        for p in [app._profile_entry(c)[0] for c in app.registry.profiles]))
    print("  数据源注册表：%s" % app.registry.path)
    print("  隐私：字段白名单脱敏，记忆/对话原文永不出网（API v1 §4）")
    print("  Ctrl+C 停止（不写任何数据）")
    sys.stdout.flush()

    if args.open:
        threading.Timer(0.6, lambda: webbrowser.open(url)).start()

    try:
        httpd.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        print("\n[console] 已停止")
    finally:
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
