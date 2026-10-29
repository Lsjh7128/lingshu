# -*- coding: utf-8 -*-
"""v0.2 布局 / 作用域记忆单测（施工单 v2 §8 G1；挂 --suite all，**不进** audit892）

覆盖：规范化 5 / 隔离 6 / scope 路由 8 / 合并裁决 6 / 墓碑回收站 6 /
锁 5 / 热重载 3 / 布局与迁移幂等 4 / UCA-01 12 / skeleton 预留 4。
纯脚本，无 pytest；直接 `python tests/test_layout_v02.py` 运行。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import layout as L  # noqa: E402

PASS, FAIL = [], []
TMP = []


def check(name: str, cond: bool, detail: str = "") -> None:
    if cond:
        PASS.append(name)
    else:
        FAIL.append(name)
        if detail:
            print("  ! %s: %s" % (name, detail))


def tmpdir(tag: str) -> str:
    # 数据根放仓库内 tmp-pytest2/：本机系统 TEMP 的 ACL 会让 os.makedirs 直接拒绝访问
    # （tools/run_phase_a_regression.py 顶部记录过同一坑）。测试结束尽力清理。
    base = os.path.join(ROOT, "tmp-pytest2")
    os.makedirs(base, exist_ok=True)
    d = os.path.join(base, "v02_%s_%s_%d" % (tag, uuid.uuid4().hex[:6], os.getpid()))
    os.makedirs(d, exist_ok=False)
    TMP.append(d)
    return d


def fake(real: str):
    return lambda _p: real


def _raises(fn) -> bool:
    try:
        fn()
        return False
    except Exception:
        return True


def row(rid: str, scope: str, obj: str, *, sid: str = "a1b2c3d4", pid: str = "butler",
        importance: float = 0.9, display: str | None = None,
        subject: str = "偏好", relation: str = "喝") -> dict:
    return L.make_memory_row(
        mem_id=rid, ts=1800000000, mem_type="semantic", origin="user_fact",
        subject=subject, relation=relation, obj=obj, content=obj,
        display=display or ("%s%s%s" % (subject, relation, obj)),
        sandbox=sid, persona=pid, scope=scope, importance=importance,
    )


# ── A. canonicalPath → sandboxId（5 组等价 + 不等价）────────
rp = "D:/Novel"
a = L.canonicalize_path("D:\\Novel", realpath=fake(rp))
b = L.canonicalize_path("d:\\novel", realpath=fake(rp))
c = L.canonicalize_path("D:\\Novel\\", realpath=fake(rp))
d = L.canonicalize_path("D:\\NOVEL~1", realpath=fake(rp))
e = L.canonicalize_path("D:\\Novel\\.\\Novel", realpath=fake(rp))
check("规范化：4 组等价输入同 id", len({L.sandbox_id(x) for x in (a, b, c, d, e)}) == 1)
check("规范化：大小写不敏感", a == b == "d:/novel", "a=%r b=%r" % (a, b))
check("规范化：去尾分隔符", c == "d:/novel", repr(c))
check("规范化：不等价路径 id 不同", L.sandbox_id(a) != L.sandbox_id(
    L.canonicalize_path("D:\\Novel2", realpath=fake("D:/Novel2"))))
check("规范化：Unicode NFC 等价",
      L.canonicalize_path("D:\\Cafe\u0301", realpath=fake("D:/Café"))
      == L.canonicalize_path("D:\\Café", realpath=fake("D:/Café")))
check("规范化：sandboxId 为 8 位 hex",
      bool(L.SANDBOX_ID_RE.match(L.sandbox_id(a))) and L.sandbox_id(a) == L.sandbox_id(a))

# ── B. 六键路径（S1）与隔离（§4.4）──────────────────────────
root = tmpdir("root")
paths = L.resolve_memory_paths(root, "a1b2c3d4", "butler")
check("S1：resolve_memory_paths 恰好六键",
      set(paths) == {"globalMemory", "localMemory", "pending", "skeleton", "state", "personaState"})
check("S1：六键路径都在数据根下", all(p.startswith(root) for p in paths.values()))
check("隔离：非法 sandboxId 抛错", _raises(lambda: L.resolve_memory_paths(root, "nope", "butler")))
check("隔离：非法 personaId 抛错", _raises(lambda: L.resolve_memory_paths(root, "a1b2c3d4", "../x")))
check("隔离：local 与 global 路径不同文件",
      paths["localMemory"] != paths["globalMemory"] and paths["localMemory"].endswith(
          os.path.join("sandboxes", "a1b2c3d4", "butler", "local_memory.jsonl")))
check("隔离：skeleton 与 local 同层（D17）",
      os.path.dirname(paths["skeleton"]) == os.path.dirname(paths["localMemory"]))

# ── C. 布局识别与 layout.json（D6 / 迁移幂等）──────────────
check("布局：空目录识别为 empty", L.detect_layout(root)["kind"] == "empty")
with open(os.path.join(root, "state.json"), "w", encoding="utf-8") as f:
    f.write("{}")
check("布局：legacy 平铺可识别", L.detect_layout(root)["kind"] == "legacy")
res = L.write_layout(root, ["legacy"], now=1800000000)
check("布局：layout.json 写入成功", res.get("ok") and L.read_layout(root)["schema_version"] == "layout-0.2")
check("布局：二次迁移被拒（幂等）", L.write_layout(root)["reason"] == "layout_exists")
check("布局：v0.2 识别", L.detect_layout(root)["kind"] == "v02")

# ── C2. 绑定（sandbox_mapping）：写/读/解析，无绑定返回 None ──
root2 = tmpdir("map")
res = L.write_mapping_bindings(root2, [
    {"canonical_path": "D:\\ProjB", "sandbox_id": "11112222", "default_persona": "pb", "label": "B"},
    {"canonical_path": "D:\\ProjA", "sandbox_id": "aaaabbbb", "default_persona": "pa", "label": "A"},
])
check("绑定：mapping 写入成功", res.get("ok") is True)
bindings = L.read_mapping_bindings(root2)
check("绑定：读回条数正确且按 canonical 排序", len(bindings) == 2
      and bindings[0]["canonical_path"] == "d:/proja", str(bindings))
check("绑定：解析命中（大小写/尾分隔符等价）",
      (L.resolve_binding(root2, "d:\\proja\\") or {}).get("default_persona") == "pa")
check("绑定：解析未命中 → None（无兜底）", L.resolve_binding(root2, "d:\\not-bound") is None)
check("绑定：缺失文件 → 空列表", L.read_mapping_bindings(tmpdir("map-empty")) == [])
res = L.write_mapping_bindings(root2, [])
check("绑定：清空后解析仍为 None", res.get("ok") is True
      and L.resolve_binding(root2, "d:\\proja") is None)

# ── D. 墓碑与回收站（D8 / §4.5）───────────────────────────
rows = [row("m1", "local", "红茶"), row("m2", "global", "绿茶")]
rows.append(dict(rows[0], status="suppressed", content="[已彻底删除]"))   # 同 id 全量墓碑
folded = L.fold_rows(rows)
check("墓碑：同 id 后写覆盖（last-wins）", [r["id"] for r in folded["active"]] == ["m2"])
check("墓碑：全量墓碑进回收站", [r["id"] for r in folded["suppressed"]] == ["m1"])
ev = L.apply_tombstone_event([row("m3", "local", "x")], "m3", at=1800000000, by="user_panel")
check("墓碑：事件墓碑折叠为 suppressed", L.fold_rows(ev)["suppressed"][0]["id"] == "m3")
check("墓碑：append-only（原行数 +1）",
      len(ev) == 2 and ev[0]["status"] == "active" and ev[-1]["type"] == "tombstone")
red = L.purge_redact(L.fold_rows(ev)["suppressed"][0], at=1800000100)
check("墓碑：彻底删除=redact（留骨架）", red["id"] == "m3" and red["content"] == "[已彻底删除]"
      and red["status"] == "suppressed")
check("墓碑：回收站显示保留天数", L.trash_view(ev, now=1800000000 + 86400 * 3)[0]["days_in_trash"] == 3)
restored = L.apply_restore_event(ev, "m3", at=1800000200)
check("墓碑：恢复事件使行回到 active（不删墓碑行）",
      [r["id"] for r in L.fold_rows(restored)["active"]] == ["m3"] and len(restored) == 3)

# ── E. scope 路由（§4.3）8 条 ─────────────────────────────
scopes = [
    ("以后都喝红茶", "global"),
    ("这个项目要保密", "local"),
    ("以后都在这个项目里用中文", "local"),       # 两者同时 → local 胜
    ("今天天气不错", "pending"),
    ("永远别再提这件事", "global"),
    ("本仓库禁止提交 node_modules", "local"),
    ("这次先这样", "local"),
    ("不管在哪个项目，我都不吃香菜", "global"),
]
for text, want in scopes:
    check("scope：%s → %s" % (text[:8], want), L.decide_scope(text)["scope"] == want,
          str(L.decide_scope(text)))

# ── F. 合并裁决（D9 / §4.2）──────────────────────────────
g = row("g1", "global", "红茶", sid="deadbeef", importance=0.95)
l = row("l1", "local", "绿茶", importance=0.5)
mr = L.merge_layers([g, l], sandbox_id="a1b2c3d4", persona_id="butler")
check("合并：同键冲突 local 胜", [r["id"] for r in mr["selected"]] == ["l1"])
check("合并：冲突记录 winner=local", mr["conflicts"] and mr["conflicts"][0]["winner"] == "local")
check("合并：global 行可跨沙盒读取",
      [r["id"] for r in L.merge_layers([row("g1", "global", "x", sid="ffffffff")],
                                       sandbox_id="a1b2c3d4", persona_id="butler")["selected"]] == ["g1"])
bad = L.merge_layers([row("l1", "local", "x", sid="ffffffff")],
                     sandbox_id="a1b2c3d4", persona_id="butler")
check("合并：行内 sandbox 标签不符被丢弃", bad["dropped"] and bad["dropped"][0]["reason"] == "sandbox_label_mismatch")
bad2 = L.merge_layers([row("g1", "global", "x", pid="architect")],
                      sandbox_id="a1b2c3d4", persona_id="butler")
check("合并：行内 persona 标签不符被丢弃", bad2["dropped"][0]["reason"] == "persona_label_mismatch")
check("合并：pending 不参与检索",
      L.merge_layers([row("p1", "pending", "x")], sandbox_id="a1b2c3d4",
                     persona_id="butler")["dropped"][0]["reason"] == "pending_not_searchable")
check("合并：legacy 溯源行视为当前沙盒",
      [r["id"] for r in L.merge_layers([row("m0", "local", "x", sid="legacy")],
                                       sandbox_id="a1b2c3d4", persona_id="butler")["selected"]] == ["m0"])
floor = L.merge_layers([row("g1", "global", "x", importance=0.99),
                        row("l1", "local", "y", importance=0.1)],
                       sandbox_id="a1b2c3d4", persona_id="butler", top_k=1)
check("合并：local 保底挤掉低价值 global", [r["id"] for r in floor["selected"]] == ["l1"])

# ── G. 目录锁（D10 / §4.7）────────────────────────────────
lock_root = os.path.join(root, ".locks")
lk = L.Lock(lock_root, "lock:audit", timeout=1.0)
lk.acquire()
check("锁：mkdir 原子加锁", lk.held and os.path.isdir(lk.dir))
check("锁：owner.json 带 PID", json.load(open(lk.owner_file, encoding="utf-8"))["pid"] == os.getpid())
timeout_raised = False
try:
    L.Lock(lock_root, "lock:audit", timeout=0.2).acquire()
except L.LockTimeout:
    timeout_raised = True
check("锁：并发竞争 5s 超时（此处 0.2s）", timeout_raised)
check("锁：skeleton 与 memory 键不同（S2）",
      L.lock_key("skeleton", sandbox_id="a1b2c3d4", persona_id="butler")
      != L.lock_key("memory", sandbox_id="a1b2c3d4"))
lk.release()
check("锁：释放后可重新获取", not lk.held and os.path.isdir(lk.dir) is False
      and L.Lock(lock_root, "lock:audit", timeout=0.5).acquire().held)
# 陈旧锁（30s）可抢占
key = "lock:global:butler"
digest = hashlib.sha256(key.encode("utf-8")).hexdigest()[:16]
stale_dir = os.path.join(lock_root, digest + ".lock")
os.makedirs(stale_dir, exist_ok=True)
with open(os.path.join(stale_dir, "owner.json"), "w", encoding="utf-8") as f:
    json.dump({"pid": 999999, "at": time.time() - 60}, f)
check("锁：30s 陈旧锁可抢占", L.Lock(lock_root, key, timeout=1.0, stale=30).acquire().held)

# ── H. 热重载（§4.6）─────────────────────────────────────
mem = os.path.join(root, "mem.jsonl")
check("热重载：文件缺失 signature=None", L.file_signature(mem) is None)
with open(mem, "w", encoding="utf-8") as f:
    f.write('{"id":"m1"}\n')
s1 = L.file_signature(mem)
time.sleep(0.01)
with open(mem, "a", encoding="utf-8") as f:
    f.write('{"id":"m2"}\n')
s2 = L.file_signature(mem)
check("热重载：append 后 signature 变化", s1 != s2 and s2["lines"] == 2)
check("热重载：200ms 节流内不重载",
      not L.should_reload(s1, s2, now_ms=1000, last_check_ms=900))
check("热重载：过 200ms 且 signature 变化才重载",
      L.should_reload(s1, s2, now_ms=1200, last_check_ms=900))

# ── I. UCA-01 文本检查（S4）12 条 ─────────────────────────
uca = [
    ("我喜欢喝红茶", "ok"),
    ("sk-abcdefghijklmnopqrstuvwx", "block"),
    ("password=Hunter2hunter", "block"),
    ("-----BEGIN RSA PRIVATE KEY-----", "block"),
    ("110101199003077777", "block"),
    ("6222021234567890123", "block"),
    ("13812345678", "warn"),
    ("这份材料是绝密", "warn"),
    ("订单号 123456789012", "ok"),
    ("手机 138 1234 5678", "ok"),
    ("", "ok"),
    (None, "ok"),
]
for text, want in uca:
    got = L.check_uca01_text(text)
    check("UCA01：%r → %s" % (str(text)[:10], want), got["verdict"] == want, str(got))
check("UCA01：block 带机器码 reasons",
      "sk_key" in L.check_uca01_text("sk-abcdefghijklmnopqrstuvwx")["reasons"])

# ── J. Skeleton / 审计常量（S3 / S5）──────────────────────
check("S3：skeleton 段 order=105", L.SKELETON_SECTION["order"] == 105
      and L.SKELETON_SECTION["name"] == "lingshu:skeleton")
check("S5：本期新增审计 kind 齐全", set(L.AUDIT_KINDS_V02) == {
    "panel_forget", "panel_restore", "panel_purge", "migration_apply", "sandbox_mapped"})
check("S5：skeleton 三事件只登记不产生", set(L.AUDIT_KINDS_RESERVED) == {
    "skeleton_generated", "skeleton_rejected", "skeleton_rebuilt"})
check("S5：新增与预留不重叠", not (set(L.AUDIT_KINDS_V02) & set(L.AUDIT_KINDS_RESERVED)))


# ── 汇总 ────────────────────────────────────────────────
print("PASS: %d ｜ FAIL: %d ｜ 断言总数: %d" % (len(PASS), len(FAIL), len(PASS) + len(FAIL)))
# 尽力清理（Windows 上锁文件可能删不掉；失败不影响断言结果）
if not FAIL:
    import shutil
    for d in TMP:
        shutil.rmtree(d, ignore_errors=True)
if FAIL:
    print("失败项：" + "；".join(FAIL))
    sys.exit(1)
