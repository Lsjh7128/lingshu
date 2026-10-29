# -*- coding: utf-8 -*-
"""LINGSHU 迁移 v0.2 验收（施工单 v2 §7，纯脚本、无 pytest）

    D:\\LSJH\\lingshu\\.venv-neural\\Scripts\\python.exe tests/test_migrate_v02.py

覆盖：
1. dry-run 零写入：目标目录/备份目录不存在，源文件字节不变
2. apply：layout.json schema_version=layout-0.2 且 migrated_from 含 legacy
3. local_memory.jsonl 行数 == 源行数，每行 sandbox_id/persona_id/scope/display_text 齐全
4. 统一 audit 单本存在，每行带 sandbox_id/persona_id，migration_apply 事件齐全
5. state.json 拆两份，persona 字段 / sandbox 字段归属正确
6. 备份目录文件数、逐文件 sha256 与源一致
7. 二次 apply exit=3，layout.json 字节与 mtime 未被改写
8. 旧 legacy 文件仍存在且字节不变（复制不移动）
9. 失败路径：坏 state.json → exit=4，不留下半套 layout.json

沙箱 ACL 说明：tempfile.mkdtemp() 在本机 workspace 沙箱下可能建出不可删目录，
这里仿 tools/run_phase_a_regression.py 顶部的 `_mkdtemp`，用 os.makedirs 自建
临时目录，清理用 try/except 包住（清理失败不影响断言结果）。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import layout as L  # noqa: E402

TOOL = os.path.join(ROOT, "tools", "migrate_layout_v02.py")
PY = sys.executable
PASS, FAIL = [], []
_COUNTER = [0]


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))


# ── 沙箱兼容的临时目录 ───────────────────────────────────

def _mkdtemp() -> str:
    base = os.path.join(ROOT, ".tmp")
    os.makedirs(base, exist_ok=True)
    while True:
        _COUNTER[0] += 1
        path = os.path.join(base, "migrate_v02_%d_%d" % (os.getpid(), _COUNTER[0]))
        try:
            os.makedirs(path, exist_ok=False)
            return path
        except FileExistsError:
            continue


def _rmtree(path: str) -> None:
    try:
        shutil.rmtree(path, ignore_errors=True)
    except Exception:
        pass


# ── 夹具 ────────────────────────────────────────────────

def _write(path: str, text: str) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)


def _write_json(path: str, doc) -> None:
    _write(path, json.dumps(doc, ensure_ascii=False, indent=2) + "\n")


def _write_jsonl(path: str, rows) -> None:
    _write(path, "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _tree(directory: str) -> dict[str, str]:
    out = {}
    for base, _dirs, names in os.walk(directory):
        for name in names:
            full = os.path.join(base, name)
            rel = os.path.relpath(full, directory).replace("\\", "/")
            out[rel] = _sha256(full)
    return out


def _jsonl_rows(path: str) -> list[dict]:
    rows = []
    with open(path, "r", encoding="utf-8-sig") as f:
        for line in f:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


LEGACY_MEMORY = [
    {"id": "m1", "ts": 1700000000, "type": "episodic", "origin": "user", "layer": "warm",
     "status": "active", "subject": "项目", "relation": "进度", "object": "完成一半",
     "content": "项目做到一半", "importance": 0.7},
    {"id": "m2", "ts": 1700000010, "type": "semantic", "origin": "user", "layer": "warm",
     "status": "active", "subject": "我", "relation": "喜欢", "object": "红茶",
     "content": "我喜欢红茶", "display_text": "我更喜欢红茶", "importance": 0.8},
    {"id": "m3", "ts": 1700000020, "type": "episodic", "origin": "user", "layer": "warm",
     "status": "active", "subject": "部署", "relation": "完成", "object": "上线",
     "content": "部署完成", "importance": 0.6},
]
LEGACY_AUDIT = [
    {"ts": 1700000100000, "kind": "turn_begin", "turns": 1},
    {"ts": 1700000101000, "kind": "memory_write", "written": 2},
]
LEGACY_PERSONA = {
    "schema_version": "persona-0.1",
    "persona_id": "butler",
    "name": "鲸鲸",
    "summary": "legacy 头像",
    "traits": ["懒", "嘴甜"],
    "confirmed_by_user": True,
    "confirmed_at": 1700000000,
}
WORKSPACE_PERSONA = {
    "schema_version": "persona-0.1",
    "persona_id": "butler",
    "name": "鲸鲸（workspace 副本）",
    "summary": "workspace 头像",
}

LEGACY_STATE = {
    "sessions": 5,
    "turns": 12,
    "dev": {"valence": 0.1, "arousal": 0.2, "dominance": 0.0},
    "emotionActive": True,
    "relation": {"trust_level": "mid", "stage": "regular", "commitments": []},
    "goal": {"goals": {}, "goal_events": []},
    "control": {
        "integral": {"valence": 0.3, "arousal": 0.0, "dominance": -0.1},
        "saturated": {"valence": False, "arousal": False, "dominance": False},
        "phase": None,
        "riskStreak": 2,
    },
    "tools": {"stats": {"calls": 4}},
    "stateWrites": 7,
}


def make_fixture() -> dict:
    """三代来源：legacy 平铺 / workspace_code / workspace_code/profiles/dev / profiles/dev。"""
    base = _mkdtemp()
    data = os.path.join(base, "data")
    os.makedirs(data)
    workspace = os.path.join(base, "project")
    os.makedirs(workspace)
    backup = os.path.join(base, "backup")       # 故意不存在：apply 时才创建

    _write_jsonl(os.path.join(data, "memory.jsonl"), LEGACY_MEMORY)
    _write_jsonl(os.path.join(data, "audit.jsonl"), LEGACY_AUDIT)
    _write_json(os.path.join(data, "state.json"), LEGACY_STATE)
    _write_json(os.path.join(data, "persona.json"), LEGACY_PERSONA)

    _write_jsonl(os.path.join(data, "workspace_code", "memory.jsonl"), [
        {"id": "w1", "ts": 1700000030, "type": "episodic", "origin": "user",
         "subject": "代码", "relation": "改", "object": "迁移脚本", "content": "改脚本"},
        {"id": "w2", "ts": 1700000040, "type": "episodic", "origin": "tool",
         "subject": "测试", "relation": "跑", "object": "通过", "content": "测试通过"},
    ])
    _write_jsonl(os.path.join(data, "workspace_code", "audit.jsonl"), [
        {"ts": 1700000200000, "kind": "tool_gate", "tool": "write"},
    ])
    # 多来源 persona.json：按"有记忆的那个"取（legacy 先于 workspace_code），目标应与 legacy 一致。
    _write_json(os.path.join(data, "workspace_code", "persona.json"), WORKSPACE_PERSONA)
    _write_jsonl(os.path.join(data, "workspace_code", "profiles", "dev", "memory.jsonl"), [
        {"id": "wp1", "ts": 1700000050, "type": "semantic", "origin": "user",
         "subject": "环境", "relation": "是", "object": "dev", "content": "dev 环境"},
    ])
    _write_jsonl(os.path.join(data, "profiles", "dev", "memory.jsonl"), [
        {"id": "p1", "ts": 1700000060, "type": "semantic", "origin": "user",
         "subject": "偏好", "relation": "为", "object": "深色主题", "content": "深色主题"},
    ])
    _write_jsonl(os.path.join(data, "profiles", "dev", "audit.jsonl"), [
        {"ts": 1700000300000, "kind": "turn_end_seen", "turns": 2},
    ])

    return {
        "base": base, "data": data, "workspace": workspace, "backup": backup,
        "sid": L.sandbox_id_for_path(workspace),
        "canonical_path": L.canonicalize_path(workspace),
        "memory_rows": 3 + 2 + 1 + 1,
        "audit_rows": 2 + 1 + 0 + 1,
        "migrated_files": 9,          # legacy 3 + ws 2 + ws/profiles 1 + profiles 2 + persona 1
        "persona_sha": _sha256(os.path.join(data, "persona.json")),
        "source_tree": _tree(data),
    }


def run_tool(fx: dict, *extra: str, data: str | None = None) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [PY, "-X", "utf8", TOOL,
           "--data-root", data or fx["data"],
           "--workspace-root", fx["workspace"],
           "--backup-dir", fx["backup"]] + list(extra)
    return subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", env=env, timeout=180)


# ── 测试主体 ────────────────────────────────────────────

def main() -> int:
    print("LINGSHU 迁移 v0.2 验收")
    fx = make_fixture()
    data = fx["data"]
    sid = fx["sid"]
    pid = "butler"
    local_mem = os.path.join(data, "sandboxes", sid, pid, "local_memory.jsonl")
    persona_state = os.path.join(data, "personas", pid, "persona_state.json")
    sandbox_state = os.path.join(data, "sandboxes", sid, pid, "state.json")
    audit_book = os.path.join(data, "audit", "audit.jsonl")
    layout_path = os.path.join(data, "layout.json")
    persona_copy = os.path.join(data, "personas", pid, "persona.json")
    index_path = os.path.join(data, "sandboxes", "index.json")
    sandbox_meta_path = os.path.join(data, "sandboxes", sid, "sandbox.json")

    try:
        # ── 1. dry-run：零写入 ────────────────────────
        print("\n[1] dry-run")
        before = _tree(data)
        dry = run_tool(fx)
        check("dry-run 退出码 0", dry.returncode == 0, str(dry.returncode) + " " + dry.stderr[-200:])
        check("dry-run 输出含 DRY-RUN", "DRY-RUN" in dry.stdout)
        check("dry-run 计划含 sha256", "sha256=" in dry.stdout)
        check("dry-run 不建 sandboxes/", not os.path.isdir(os.path.join(data, "sandboxes")))
        check("dry-run 不建 audit/", not os.path.isdir(os.path.join(data, "audit")))
        check("dry-run 不写 layout.json", not os.path.exists(layout_path))
        check("dry-run 不建备份目录", not os.path.exists(fx["backup"]))
        check("dry-run 后源文件字节不变", _tree(data) == before)

        dry_json = run_tool(fx, "--json")
        try:
            summary = json.loads(dry_json.stdout.strip())
        except ValueError:
            summary = {}
        check("--json dry-run 可解析且含 sandbox_id",
              summary.get("status") == "dry_run" and summary.get("sandbox_id") == sid,
              str(summary.get("status")))

        # ── 2. apply ────────────────────────────────
        print("\n[2] apply")
        applied = run_tool(fx, "--apply")
        check("apply 退出码 0", applied.returncode == 0,
              str(applied.returncode) + " " + applied.stderr[-300:])
        layout = L.read_layout(data)
        check("layout.json 存在且 schema_version=layout-0.2",
              layout is not None and layout.get("schema_version") == "layout-0.2")
        check("layout.migrated_from 含 legacy",
              bool(layout) and "legacy" in layout.get("migrated_from", []),
              str(layout.get("migrated_from") if layout else None))
        check("layout.migrated_from 含 workspace_code 与 profiles/dev",
              bool(layout) and {"workspace_code", "profiles/dev"} <=
              set(layout.get("migrated_from", [])),
              str(layout.get("migrated_from") if layout else None))

        # ── 2b. 沙盒元数据 / persona 收编 ──────────────
        print("\n[2b] 沙盒元数据 / persona")
        index = L.read_json(index_path) if os.path.isfile(index_path) else None
        meta = L.read_json(sandbox_meta_path) if os.path.isfile(sandbox_meta_path) else None
        check("sandboxes/index.json 存在且 schema=sandbox-index-0.1",
              bool(index) and index.get("schema_version") == "sandbox-index-0.1")
        check("index.json 的 sid 与 canonical_path 对应",
              bool(index) and (index.get("sandboxes") or {}).get(sid, {}).get("canonical_path")
              == fx["canonical_path"],
              str((index or {}).get("sandboxes")))
        check("sandboxes/<sid>/sandbox.json 存在且 sid/canonical_path 对应",
              bool(meta) and meta.get("schema_version") == "sandbox-0.1"
              and meta.get("sandbox_id") == sid
              and meta.get("canonical_path") == fx["canonical_path"],
              str(meta))
        check("persona.json 已复制到 personas/<pid>/ 且字节一致",
              os.path.isfile(persona_copy) and _sha256(persona_copy) == fx["persona_sha"],
              "missing" if not os.path.isfile(persona_copy) else "")

        # ── 3. 记忆行契约 ───────────────────────────
        print("\n[3] local_memory 行契约")
        mem_rows = _jsonl_rows(local_mem) if os.path.isfile(local_mem) else []
        check("local_memory 行数 == 源行数", len(mem_rows) == fx["memory_rows"],
              "%d vs %d" % (len(mem_rows), fx["memory_rows"]))
        check("每行 sandbox_id/persona_id/scope/display_text 齐全",
              all(r.get("sandbox_id") and r.get("persona_id") == pid and r.get("scope") == "local"
                  and str(r.get("display_text") or "").strip() for r in mem_rows))
        check("legacy 来源行 sandbox_id=legacy",
              all(r["sandbox_id"] == "legacy" for r in mem_rows if r.get("migrated_from") == "legacy"))
        check("非 legacy 来源行 sandbox_id=目标 sandbox_id",
              all(r["sandbox_id"] == sid for r in mem_rows if r.get("migrated_from") != "legacy"))
        check("行顺序与来源顺序一致（m1,m2,m3,w1,w2,wp1,p1）",
              [r["id"] for r in mem_rows] == ["m1", "m2", "m3", "w1", "w2", "wp1", "p1"],
              str([r["id"] for r in mem_rows]))
        check("缺 display_text 的行被补成一句人话",
              next(r for r in mem_rows if r["id"] == "m1").get("display_text") == "项目进度完成一半",
              str(next(r for r in mem_rows if r["id"] == "m1").get("display_text")))
        check("已有 display_text 不被覆盖",
              next(r for r in mem_rows if r["id"] == "m2").get("display_text") == "我更喜欢红茶")

        # ── 4. audit 单本 ───────────────────────────
        print("\n[4] audit 单本")
        audit_rows = _jsonl_rows(audit_book) if os.path.isfile(audit_book) else []
        check("audit/audit.jsonl 存在", os.path.isfile(audit_book))
        check("audit 行数 == 源 audit 行 + migration_apply + sandbox_mapped",
              len(audit_rows) == fx["audit_rows"] + fx["migrated_files"] + 1,
              "%d vs %d" % (len(audit_rows), fx["audit_rows"] + fx["migrated_files"] + 1))
        check("每行 audit 带 sandbox_id/persona_id",
              all(r.get("sandbox_id") and r.get("persona_id") == pid for r in audit_rows))
        check("migration_apply 事件数 == 迁移文件数",
              sum(1 for r in audit_rows if r.get("kind") == "migration_apply") == fx["migrated_files"])
        check("sandbox_mapped 事件恰好 1 条且带 canonical_path",
              sum(1 for r in audit_rows if r.get("kind") == "sandbox_mapped") == 1
              and any(r.get("canonical_path") == fx["canonical_path"]
                      for r in audit_rows if r.get("kind") == "sandbox_mapped"))
        check("migration_apply 带 source_kind 与 source_sha256",
              all(r.get("source_kind") and r.get("source_sha256")
                  for r in audit_rows if r.get("kind") == "migration_apply"))

        # ── 5. state 拆两份 ─────────────────────────
        print("\n[5] state 拆两份")
        pdoc = L.read_json(persona_state) if os.path.isfile(persona_state) else None
        sdoc = L.read_json(sandbox_state) if os.path.isfile(sandbox_state) else None
        check("persona_state.json 存在且 schema=persona-state-0.1",
              bool(pdoc) and pdoc.get("schema_version") == "persona-state-0.1")
        check("persona_state 含 integral/emotion/relations 三字段",
              bool(pdoc) and all(k in pdoc for k in
                                 ("integral_baseline", "emotion_baseline", "relations")))
        check("control.integral → persona_state.integral_baseline",
              bool(pdoc) and pdoc.get("integral_baseline", {}).get("valence") == 0.3,
              str(pdoc.get("integral_baseline") if pdoc else None))
        check("dev → persona_state.emotion_baseline",
              bool(pdoc) and pdoc.get("emotion_baseline", {}).get("arousal") == 0.2)
        check("relation → persona_state.relations",
              bool(pdoc) and pdoc.get("relations", {}).get("trust_level") == "mid")
        check("persona_state 不含 turns/control 等 sandbox 字段",
              bool(pdoc) and "turns" not in pdoc and "control" not in pdoc)
        check("state.json 存在且 schema=state-0.1",
              bool(sdoc) and sdoc.get("schema_version") == "state-0.1")
        check("turns/stage/control 落在 sandbox state",
              bool(sdoc) and sdoc.get("turns") == 12 and sdoc.get("stage") == "warm"
              and isinstance(sdoc.get("control"), dict),
              str({k: sdoc.get(k) for k in ("turns", "stage")} if sdoc else None))
        check("其余字段（goal/tools）落在 sandbox state",
              bool(sdoc) and sdoc.get("goal", {}).get("goals") == {}
              and sdoc.get("tools", {}).get("stats", {}).get("calls") == 4)
        check("sandbox state 不混入 relation/dev",
              bool(sdoc) and "relation" not in sdoc and "dev" not in sdoc)

        # ── 6. 备份 ─────────────────────────────────
        print("\n[6] 备份")
        backup_tree = _tree(fx["backup"]) if os.path.isdir(fx["backup"]) else {}
        check("备份目录文件数与源一致",
              len(backup_tree) == len(fx["source_tree"]),
              "%d vs %d" % (len(backup_tree), len(fx["source_tree"])))
        check("备份逐文件 sha256 与源一致",
              backup_tree == fx["source_tree"],
              str(sorted(set(backup_tree) ^ set(fx["source_tree"]))[:3]))

        # ── 7. 幂等 ─────────────────────────────────
        print("\n[7] 幂等拒绝")
        layout_before = open(layout_path, "rb").read()
        mtime_before = os.stat(layout_path).st_mtime_ns
        mem_sha_before = _sha256(local_mem)
        again = run_tool(fx, "--apply")
        check("二次 apply 退出码 3", again.returncode == 3, str(again.returncode))
        check("二次 apply 未改写 layout.json",
              open(layout_path, "rb").read() == layout_before
              and os.stat(layout_path).st_mtime_ns == mtime_before)
        check("二次 apply 未改写 local_memory", _sha256(local_mem) == mem_sha_before)

        # ── 8. 旧文件只读保留 ───────────────────────
        print("\n[8] 旧布局只读保留")
        now_tree = _tree(data)
        check("旧 legacy 文件仍存在",
              all(os.path.isfile(os.path.join(data, name))
                  for name in ("state.json", "memory.jsonl", "audit.jsonl")))
        check("旧文件名与字节均未改动",
              all(now_tree.get(rel) == sha for rel, sha in fx["source_tree"].items()),
              str([rel for rel, sha in fx["source_tree"].items()
                   if now_tree.get(rel) != sha][:3]))
        check("旧 workspace_/profiles 目录仍在",
              os.path.isdir(os.path.join(data, "workspace_code"))
              and os.path.isdir(os.path.join(data, "profiles", "dev")))

        # ── 9. 失败路径：坏 state.json → exit 4 且无 layout ──
        print("\n[9] 失败路径")
        bad = make_fixture()
        try:
            _write(os.path.join(bad["data"], "state.json"), "{不是合法 JSON")
            bad_run = run_tool(bad, "--apply")
            check("坏 state.json → exit 4", bad_run.returncode == 4, str(bad_run.returncode))
            check("失败后不留下 layout.json",
                  not os.path.exists(os.path.join(bad["data"], "layout.json")))
            check("失败后不创建 sandboxes/",
                  not os.path.isdir(os.path.join(bad["data"], "sandboxes")))
            check("失败后旧文件未改动",
                  _sha256(os.path.join(bad["data"], "memory.jsonl"))
                  == bad["source_tree"]["memory.jsonl"])
        finally:
            _rmtree(bad["base"])
    finally:
        _rmtree(fx["base"])

    total = len(PASS) + len(FAIL)
    print("\nPASS: %d ｜ FAIL: %d ｜ 断言总数: %d" % (len(PASS), len(FAIL), total))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
