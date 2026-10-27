# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 用户事实提取器 + 记忆账本 supersede（论文 F-A / F-B 回归）

    python tests/test_fact_extractor.py

背景：论文真实宿主压测暴露两个产品缺陷（缺陷编号沿用论文 finding）：
- **F-A**：提取器识别不了自然纠正句式（"我现在改主意了，代号改成贝塔" → 0 条抽取），
  且账本层没有 supersede → 旧值（阿尔法）继续 active，模型照旧召回旧值。
- **F-B**：疑问句被当事实写入（"我的项目代号是什么？" → object=什么），污染记忆库。

本文件新增（不得修改任何既有测试/断言），覆盖：
- F-A：自然纠正句式抽取（"改主意 / 改成 / 调整为 / 把X改成Y / 不是X而是Y"），
  subject/relation/object 准确、origin=user_fact、request.supersede!==false；
- F-A 账本层端到端（论文 S1 验收）：applyWriteRequests 先写阿尔法再写改口 →
  旧条目 status=superseded、supersededBy 指向新条目、新条目 active；
- F-B：疑问句一律不写（requests 为空 + rejected 含 interrogative），
  object 不得是疑问词，且正常陈述/显式记忆请求不被误伤。

原则（与仓库其余验收一致）：
- **不复制实现逻辑**：所有断言都经 node 子进程调用真实 JS 实现
  （plugin/dsh-lingshu/index.js#extractUserFacts + memorywriter.js#applyWriteRequests），
  Python 侧只做断言。实现退化时断言必须变红。
- 不使用 pytest；退出码 0 = 全通过。
- 临时 .mjs / job json 放仓库 .tmp/（当前 Windows 沙箱拒绝在 mkdtemp 目录写入）。

接口依赖（A3 的实现只需满足这些约定，测试不依赖其内部写法）：
- `index.js#extractUserFacts(text, entries = []) -> {requests:[{origin,reason,supersede,items:[{type,subject,relation,object,content,sourceQuote,...}]}], rejected:[{reason,quoteHead,subject?}]}`
  `entries` = 现有记忆条目数组（只读反查；无 subject 的纠正句/"不是X而是Y"用它反查 subject，
  有 subject 时用它把用户简称归一到记忆里的全称）。**插件真实调用点就是
  `extractUserFacts(text, memStore.read())`**（index.js#session/event 的 user/message 分支），
  所以账本层端到端用例按同一调用形状传 entries。
- `memorywriter.js#applyWriteRequests(requests, store, {now}) -> {written, ids, rejected, superseded}`
  store 契约 = `{read(): entry[], append(entry[])}`（内存实现，不落盘）。
- 拒绝 reason 口径：疑问句 `interrogative`；object 是疑问词 `interrogative_object`；
  反查不到 subject `unparsable`。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGIN = os.path.join(ROOT, "plugin", "dsh-lingshu")
TMP = os.path.join(ROOT, ".tmp")
PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name
          + (("  — " + str(detail)[:220]) if detail else ""))


def node_bin() -> str:
    for cand in (os.environ.get("NODE"), shutil.which("node"),
                 r"C:\Program Files\nodejs\node.exe"):
        if cand and os.path.isfile(cand):
            return cand
    return "node"


def run_node(args: list[str], timeout: int = 180) -> tuple[int, str, str]:
    proc = subprocess.run([node_bin()] + args, cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", timeout=timeout)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


# ══════════════════════════════════════════════════════════════════════
# node 侧 harness：只做"调用真实实现 + 回传 JSON"，零业务逻辑
# ══════════════════════════════════════════════════════════════════════
HARNESS = r'''
import fs from 'node:fs';

const repo = process.argv[2];
const jobPath = process.argv[3];
const job = JSON.parse(fs.readFileSync(jobPath, 'utf8'));
const toUrl = (p) => 'file:///' + String(p).replace(/\\/g, '/').replace(/^\/+/, '');
const out = { extract: {}, e2e: {}, meta: {}, errors: [] };

let idx = null;
let mw = null;
try {
  idx = await import(toUrl(repo + '/plugin/dsh-lingshu/index.js'));
  mw = await import(toUrl(repo + '/plugin/dsh-lingshu/memorywriter.js'));
} catch (e) {
  out.errors.push('import_failed: ' + String((e && e.stack) || e));
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
}
out.meta.extractUserFacts = typeof idx.extractUserFacts === 'function';
out.meta.extractUserFacts_arity = typeof idx.extractUserFacts === 'function'
  ? idx.extractUserFacts.length : -1;
out.meta.applyWriteRequests = typeof mw.applyWriteRequests === 'function';

// 逐条抽取（entries 仅在用例显式给出时作为第二参数传入）
for (const c of job.extract || []) {
  try {
    const withEntries = Array.isArray(c.entries);
    const r = withEntries ? idx.extractUserFacts(c.text, c.entries)
                          : idx.extractUserFacts(c.text);
    out.extract[c.id] = {
      ok: true,
      withEntries,
      requests: (r && r.requests) || [],
      rejected: (r && r.rejected) || [],
    };
  } catch (e) {
    out.extract[c.id] = { ok: false, error: String((e && e.stack) || e) };
  }
}

// 内存 store（不落盘）：契约 = {read(): entry[], append(entry[])}，读取侧按 id 后写覆盖
function memStore() {
  const rows = [];
  return {
    read() {
      const byId = new Map();
      for (const e of rows) byId.set(String(e.id), e);
      return [...byId.values()];
    },
    append(objs) { for (const o of objs) rows.push({ ...o }); },
    rows() { return rows.map((e) => ({ ...e })); },
  };
}

// 账本层端到端：抽取（真实实现）→ applyWriteRequests（真实实现）→ 回传 last-wins 账本
// 注意：与插件真实调用点同形 —— index.js#user/message 分支是
//   const fact = extractUserFacts(text, memStore.read());
// 所以每一步都把"当前账本"作为第二参数传进去（否则字面 subject 简称无法归一到记忆里的全称）。
for (const sc of job.e2e || []) {
  const store = memStore();
  const steps = [];
  for (const s of sc.steps) {
    try {
      // 默认按插件真实调用点传 entries；withEntries === false 的步骤用于固化"无 entries"的反面语义。
      const r = s.withEntries === false
        ? idx.extractUserFacts(s.text)
        : idx.extractUserFacts(s.text, store.read());
      const reqs = (r && r.requests) || [];
      const w = reqs.length
        ? mw.applyWriteRequests(reqs, store, { now: Number(s.now) || 1700000000000 })
        : { written: 0, ids: [], rejected: [], superseded: [] };
      steps.push({ id: s.id, text: s.text, requests: reqs,
                   rejected: (r && r.rejected) || [], write: w });
    } catch (e) {
      steps.push({ id: s.id, text: s.text, error: String((e && e.stack) || e) });
    }
  }
  out.e2e[sc.id] = { steps, lastWins: store.read(), raw: store.rows() };
}

// ── 插件级端到端（最强验收）：apply() 真实插件 + session/event 用户消息 + 真实 memory.jsonl ──
// 这一条会连"调用点是否把 entries 传进去"一起钉住；只走内存 store 的用例抓不到调用点回退。
if (job.pluginE2E) {
  const pe = { steps: [], entries: [], auditKinds: [], error: null };
  const dir = repo + '/.tmp/fact_plugin_e2e_' + process.pid + '_' + Date.now();
  try {
    fs.mkdirSync(repo + '/.tmp', { recursive: true });
    fs.rmSync(dir, { recursive: true, force: true });
    process.env.LINGSHU_DATA_DIR = dir;
    const sections = [];
    const hooks = {};
    const pctx = {
      tools: { register() { /* 工具不参与本用例 */ } },
      systemPrompt: { section(s) { sections.push(s); } },
      on(ev, fn) { (hooks[ev] = hooks[ev] || []).push(fn); },
      effect(fn) { try { return fn(); } catch { /* 忽略 */ } },
      inject() { /* noop */ },
      get() { throw new Error('no service'); },
    };
    idx.apply ? idx.apply(pctx, {}) : (idx.default && idx.default.apply(pctx, {}));
    const fire = async (text) => {
      for (const fn of hooks['session/event'] || []) {
        await fn({}, {
          type: 'user/message', seq: 1, time: Date.now(),
          data: { message: { role: 'user', content: [{ type: 'text', text }] } },
        });
      }
      pe.steps.push(text);
    };
    for (const text of job.pluginE2E.steps) await fire(text);
    const rows = fs.readFileSync(dir + '/memory.jsonl', 'utf8')
      .split('\n').filter(Boolean).map((l) => JSON.parse(l));
    const byId = new Map();
    for (const e of rows) byId.set(String(e.id), e);
    pe.entries = [...byId.values()];
    pe.rawRows = rows.length;
    const audit = fs.readFileSync(dir + '/audit.jsonl', 'utf8')
      .split('\n').filter(Boolean).map((l) => JSON.parse(l));
    pe.auditKinds = audit.map((a) => String(a.kind));
    pe.auditRows = audit;
  } catch (e) {
    pe.error = String((e && e.stack) || e);
  } finally {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* 忽略 */ }
  }
  out.pluginE2E = pe;
}

process.stdout.write(JSON.stringify(out));
'''

# ══════════════════════════════════════════════════════════════════════
# 用例输入（断言不硬编码期望结果，期望值只描述产品规格）
# ══════════════════════════════════════════════════════════════════════
# "不是阿尔法而是贝塔" 的既有记忆上下文：唯一一条 object=阿尔法 的 active 事实。
# 若实现支持第二参数（entries），应能反查 subject=代号；否则必须不写脏数据。
NOTX_ENTRIES = [{
    "id": "mem_001", "type": "semantic", "subject": "代号", "relation": "是",
    "object": "阿尔法", "content": "代号是阿尔法", "importance": 0.9,
    "confidence": 0.9, "layer": "warm", "status": "active",
    "origin": "user_fact", "lastAccess": 1700000000000, "tauDays": 30,
}]

EXT_CASES = [
    # ── F-A：自然纠正句式 ──
    {"id": "fa_redo", "text": "我现在改主意了，代号改成贝塔"},
    {"id": "fa_bare", "text": "代号改成贝塔"},
    {"id": "fa_adjust", "text": "我的代号调整为贝塔"},
    {"id": "fa_move", "text": "把代号改成贝塔"},
    {"id": "fa_sentence", "text": "我现在改主意了，代号改成贝塔，之前那个不算"},
    {"id": "fa_notx", "text": "不是阿尔法而是贝塔", "entries": NOTX_ENTRIES},
    # ── F-B / 防误伤：正常陈述与显式记忆请求 ──
    {"id": "fa_plain", "text": "我的代号是贝塔"},
    {"id": "fa_remember", "text": "记住：我的代号是贝塔"},
    # ── F-B：疑问句（一律不写） ──
    {"id": "fb_what", "text": "我的项目代号是什么？"},
    {"id": "fb_ma", "text": "我的代号是贝塔吗"},
    {"id": "fb_howto", "text": "项目代号怎么改？"},
    {"id": "fb_why", "text": "为什么我的代号是贝塔"},
    {"id": "fb_what2", "text": "我的代号是什么"},
]

# 账本层 E2E：论文 S1 场景（先阿尔法，再改口贝塔）
# 每一步默认**按插件真实调用点**把当前账本作为 entries 传入（index.js: extractUserFacts(text, memStore.read())）；
# 只有 withEntries=False 的步骤走"无 entries"的对照语义。
E2E_SCENARIOS = [
    # 论文 S1 场景原文：字面简称"代号"由 entries 归一到记忆里的全称"项目代号" → 同键 supersede
    {"id": "e2e_s1", "steps": [
        {"id": "s1", "text": "我的项目代号是阿尔法"},
        {"id": "s2", "text": "我现在改主意了，代号改成贝塔"},
    ]},
    # 键一致场景（subject 前后都是"代号"）：账本层 supersede 机制本身必须正确
    {"id": "e2e_samekey", "steps": [
        {"id": "s1", "text": "我的代号是阿尔法"},
        {"id": "s2", "text": "我现在改主意了，代号改成贝塔"},
    ]},
    # 反面语义（不得误伤）：不传 entries 时 subject 是字面"代号"，与旧条目"项目代号"不同键
    # → 账本层按规格**不得** supersede 别的 subject 的旧值（宁可不覆盖）。
    {"id": "e2e_noentries", "steps": [
        {"id": "s1", "text": "我的项目代号是阿尔法"},
        {"id": "s2", "text": "我现在改主意了，代号改成贝塔", "withEntries": False},
    ]},
]

# 插件级端到端：apply() 真实插件 + session/event 用户消息 + 真实 memory.jsonl
# 这条会连"调用点是否把 entries 传进去"一起钉住（只走内存 store 的用例抓不到调用点回退）。
PLUGIN_E2E = {"steps": ["我的项目代号是阿尔法", "我现在改主意了，代号改成贝塔"]}

# F-B③：写入项的 object 不得落在疑问词闭集里（task-1 规格 + 常见英文疑问词）
BANNED_OBJECTS = {
    "what", "how", "why", "which", "who", "什么", "怎么", "为什么", "如何", "哪个",
    "哪", "谁", "多少", "是否", "吗", "呢", "未知", "无",
}
INTERROGATIVE_HINTS = ("什么", "怎么", "为什么", "如何", "是否", "哪", "谁", "多少")


def run_job(job: dict) -> dict | None:
    """把 job 交给 node 跑真实 JS 实现，返回解析后的结果（失败返回 None）。"""
    os.makedirs(TMP, exist_ok=True)
    stamp = "%d_%d" % (os.getpid(), int(time.time() * 1000) % 1_000_000)
    harness = os.path.join(TMP, "fact_extractor_harness_%s.mjs" % stamp)
    jobfile = os.path.join(TMP, "fact_extractor_job_%s.json" % stamp)
    try:
        with open(harness, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(HARNESS)
        with open(jobfile, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(job, fh, ensure_ascii=False)
        code, out, err = run_node([harness, ROOT, jobfile])
        if code != 0 or not out.strip():
            check("node 子进程调用真实 JS 实现（exit 0 且有 JSON 输出）", False,
                  "exit=%r stderr=%s" % (code, (err or out)[-400:]))
            return None
        return json.loads(out)
    finally:
        for p in (harness, jobfile):
            try:
                os.remove(p)
            except OSError:
                pass


def first_item(rec: dict | None):
    """取第 1 条 request 的第 1 个 item（没有则 (None, None)）。"""
    reqs = (rec or {}).get("requests") or []
    if not reqs:
        return None, None
    items = reqs[0].get("items") or []
    return reqs[0], (items[0] if items else None)


def fmt(rec: dict | None) -> str:
    return json.dumps(rec or {}, ensure_ascii=False)[:300]


# ══════════════════════════════════════════════════════════════════════
# [0] 接线自检：真实实现可被 node 子进程加载
# ══════════════════════════════════════════════════════════════════════
def test_wiring(out: dict) -> None:
    print("\n[0] 接线：经 node 子进程加载真实实现")
    check("index.js#extractUserFacts 已导出（函数）", bool(out["meta"].get("extractUserFacts")),
          out["meta"])
    check("memorywriter.js#applyWriteRequests 已导出（函数）",
          bool(out["meta"].get("applyWriteRequests")), out["meta"])
    check("node 侧无 import/运行错误", not out.get("errors"), out.get("errors"))
    print("  extractUserFacts.length = %r（>1 表示声明了第二参数 entries）"
          % (out["meta"].get("extractUserFacts_arity")))


# ══════════════════════════════════════════════════════════════════════
# [1] F-A：自然纠正句式抽取
# ══════════════════════════════════════════════════════════════════════
FA_EXPECT = [
    # (用例 id, 输入原文, 期望 subject)
    ("fa_redo", "我现在改主意了，代号改成贝塔", "代号"),
    ("fa_bare", "代号改成贝塔", "代号"),
    ("fa_adjust", "我的代号调整为贝塔", "代号"),
    ("fa_move", "把代号改成贝塔", "代号"),
    ("fa_sentence", "我现在改主意了，代号改成贝塔，之前那个不算", "代号"),
]


def _subject_ok(subject, expect: str) -> bool:
    """subject 允许多一层"我的/项目"前缀噪音，但不许是空/命令词/整句。"""
    s = str(subject or "").strip()
    return s == expect or s.endswith(expect)


def test_fa_extract(out: dict) -> None:
    print("\n[1] F-A 自然纠正句式 → 抽取 subject=代号 / relation=是 / object=贝塔")
    ext = out.get("extract") or {}
    for cid, text, expect_subject in FA_EXPECT:
        rec = ext.get(cid) or {}
        req, item = first_item(rec)
        ok = bool(req) and bool(item)
        check("F-A %-12s 抽出 1 条写入请求" % cid, ok, fmt(rec))
        if not ok:
            continue
        check("F-A %-12s subject=%s" % (cid, expect_subject),
              _subject_ok(item.get("subject"), expect_subject), str(item.get("subject")))
        check("F-A %-12s relation=是" % cid, item.get("relation") == "是",
              str(item.get("relation")))
        check("F-A %-12s object=贝塔" % cid, str(item.get("object") or "").strip() == "贝塔",
              str(item.get("object")))
        check("F-A %-12s origin=user_fact 且 supersede!==false" % cid,
              req.get("origin") == "user_fact" and req.get("supersede") is not False,
              "origin=%r supersede=%r" % (req.get("origin"), req.get("supersede")))
        check("F-A %-12s sourceQuote 是用户原话逐字子串（I4 可追溯）" % cid,
              bool(str(item.get("sourceQuote") or "").strip())
              and str(item.get("sourceQuote")) in text, str(item.get("sourceQuote")))
        check("F-A %-12s content 含新值且不含后续分句" % cid,
              "贝塔" in str(item.get("content") or "")
              and "之前那个不算" not in str(item.get("content") or ""),
              str(item.get("content")))
        check("F-A %-12s object 不吞后续分句（<= 30 字且无逗号句号）" % cid,
              len(str(item.get("object") or "")) <= 30
              and not any(ch in str(item.get("object") or "") for ch in "，,。；;！!？?"),
              str(item.get("object")))

    # F-A③ "不是阿尔法而是贝塔"：传入既有记忆（extractUserFacts 第二参数 entries）时应能反查 subject。
    # A3 已实现该反查（subjectByOldObject：旧值唯一匹配才认，多义 → 不写）。
    # 若将来退回"不实现第二参数"，按任务书口径改为断言"不写脏数据（requests 为空或 rejected 非空）"，
    # 但那是能力回退，必须在报告里显式说明，不得悄悄放宽。
    rec = ext.get("fa_notx") or {}
    req, item = first_item(rec)
    resolved = bool(item) and _subject_ok(item.get("subject"), "代号") \
        and str(item.get("object") or "").strip() == "贝塔"
    check("F-A③ '不是阿尔法而是贝塔' 传入既有记忆后能反查 subject=代号、object=贝塔",
          resolved, fmt(rec))
    reqs = rec.get("requests") or []
    rejs = rec.get("rejected") or []
    check("F-A③ 该句式绝不写入脏数据（有写入则必须是 代号/贝塔，否则 requests 应为空）",
          (not reqs) or resolved, fmt(rec))


# ══════════════════════════════════════════════════════════════════════
# [2] F-A 账本层端到端：supersede 墓碑（论文 S1 验收）
# ══════════════════════════════════════════════════════════════════════
def _active_with_key(entries, subject, relation):
    return [e for e in entries
            if str(e.get("subject")) == str(subject)
            and str(e.get("relation")) == str(relation)
            and str(e.get("status") or "active") == "active"]


def _test_e2e(scenario: dict, label: str) -> None:
    steps = scenario.get("steps") or []
    assert len(steps) == 2, "E2E 场景必须 2 步"
    s1, s2 = steps
    w1 = s1.get("write") or {}
    w2 = s2.get("write") or {}
    check("%s 第 1 步写入 1 条（阿尔法）" % label,
          int(w1.get("written") or 0) == 1 and not w1.get("rejected"), fmt(w1))
    check("%s 第 2 步抽取到改口请求（贝塔）" % label,
          bool(s2.get("requests")), fmt({"requests": s2.get("requests")}))
    if not s2.get("requests"):
        return
    check("%s 第 2 步写入 1 条新条目" % label,
          int(w2.get("written") or 0) == 1 and not w2.get("rejected"), fmt(w2))
    old_id = (w1.get("ids") or [None])[0]
    new_id = (w2.get("ids") or [None])[0]
    check("%s 新 id != 旧 id" % label, bool(old_id) and bool(new_id) and old_id != new_id,
          "old=%r new=%r" % (old_id, new_id))
    check("%s memorywriter 产出 superseded 记录" % label,
          bool(w2.get("superseded")), fmt(w2))

    wins = {str(e.get("id")): e for e in (scenario.get("lastWins") or [])}
    old = wins.get(str(old_id)) if old_id else None
    new = wins.get(str(new_id)) if new_id else None
    check("%s 旧条目在账本里 status=superseded（墓碑 last-wins 生效）" % label,
          bool(old) and old.get("status") == "superseded",
          "old=%r" % ({"id": old_id, "status": (old or {}).get("status")},))
    check("%s 旧条目 supersededBy 指向新条目 id" % label,
          bool(old) and old.get("supersededBy") == new_id,
          "supersededBy=%r new_id=%r" % ((old or {}).get("supersededBy"), new_id))
    check("%s 新条目在账本里 status=active" % label,
          bool(new) and str(new.get("status") or "active") == "active",
          "new=%r" % ({"id": new_id, "status": (new or {}).get("status")},))
    check("%s 新条目 object=贝塔" % label,
          bool(new) and str(new.get("object") or "").strip() == "贝塔",
          str((new or {}).get("object")))
    if new:
        act = _active_with_key(scenario.get("lastWins") or [],
                               new.get("subject"), new.get("relation"))
        check("%s 同 (subject,relation) 只剩 1 条 active（旧值不再可召回）" % label,
              len(act) == 1, json.dumps([{"id": e.get("id"), "object": e.get("object")}
                                         for e in act], ensure_ascii=False))


def test_fa_supersede_e2e(out: dict) -> None:
    print("\n[2] F-A 账本层端到端：改口 → 旧值墓碑 superseded（论文 S1 验收）")
    e2e = out.get("e2e") or {}
    # 论文 S1 场景原文（按插件真实调用点传 entries）：字面简称"代号"归一 → 同键 supersede。
    spec = e2e.get("e2e_s1")
    if spec:
        _test_e2e(spec, "E2E-S1原文")
    else:
        check("E2E-S1原文 场景可运行", False, "node 未返回 e2e_s1")
    sc = e2e.get("e2e_samekey")
    if sc:
        _test_e2e(sc, "E2E-键一致")
    else:
        check("E2E-键一致 场景可运行", False, "node 未返回 e2e_samekey")

    # 反面语义：不传 entries 时 subject 是字面"代号"，与旧条目"项目代号"不同键
    # → 账本层按规格**不得** supersede 其他 subject 的旧值（宁可不覆盖，也不误删正确记忆）。
    noe = e2e.get("e2e_noentries")
    if not noe:
        check("E2E-无entries 场景可运行", False, "node 未返回 e2e_noentries")
        return
    s1, s2 = noe.get("steps") or [{}, {}]
    w1 = s1.get("write") or {}
    w2 = s2.get("write") or {}
    req2, item2 = first_item(s2)
    check("E2E-无entries 第 1 步写入 1 条（项目代号=阿尔法）",
          int(w1.get("written") or 0) == 1 and not w1.get("rejected"), fmt(w1))
    check("E2E-无entries 第 2 步仍抽取，subject 为字面简称『代号』（不是凭旧值猜全称）",
          bool(item2) and str(item2.get("subject")).strip() == "代号", fmt({"requests": s2.get("requests")}))
    check("E2E-无entries 账本层没有 superseded 记录（不同键不得互相覆盖）",
          not (w2.get("superseded") or []), fmt(w2))
    old_id = (w1.get("ids") or [None])[0]
    wins = {str(e.get("id")): e for e in (noe.get("lastWins") or [])}
    old = wins.get(str(old_id)) if old_id else None
    check("E2E-无entries 旧条目（项目代号=阿尔法）仍 active（不被误 supersede）",
          bool(old) and str(old.get("status") or "active") == "active",
          str((old or {}).get("status")))
    active = [e for e in (noe.get("lastWins") or [])
              if str(e.get("status") or "active") == "active" and e.get("origin") == "user_fact"]
    check("E2E-无entries 账本里 2 条 active 且 subject 不同（各自独立事实）",
          len(active) == 2 and len({str(e.get("subject")) for e in active}) == 2,
          json.dumps([{"id": e.get("id"), "subject": e.get("subject"),
                       "object": e.get("object")} for e in active], ensure_ascii=False))


# ══════════════════════════════════════════════════════════════════════
# [2b] 插件级端到端：apply() 真实链路 → memory.jsonl（连调用点一起钉住）
# ══════════════════════════════════════════════════════════════════════
def test_plugin_e2e(out: dict) -> None:
    print("\n[2b] 插件级端到端：apply + session/event → 真实 memory.jsonl 的 supersede")
    pe = out.get("pluginE2E") or {}
    check("插件级 E2E 不抛异常（真实 apply/session-event 链路可跑）", not pe.get("error"),
          pe.get("error") or "")
    if pe.get("error"):
        return
    entries = pe.get("entries") or []
    facts = [e for e in entries if str(e.get("origin")) == "user_fact"]
    check("插件级 E2E：写入 2 条 user_fact（阿尔法 + 贝塔）", len(facts) == 2,
          json.dumps([{"id": e.get("id"), "object": e.get("object"),
                       "status": e.get("status")} for e in facts], ensure_ascii=False))
    alpha = next((e for e in facts if str(e.get("object")).strip() == "阿尔法"), None)
    beta = next((e for e in facts if str(e.get("object")).strip() == "贝塔"), None)
    check("插件级 E2E：旧值（阿尔法）条目存在", bool(alpha),
          json.dumps([e.get("object") for e in facts], ensure_ascii=False))
    check("插件级 E2E：新值（贝塔）条目存在", bool(beta),
          json.dumps([e.get("object") for e in facts], ensure_ascii=False))
    if not (alpha and beta):
        return
    check("插件级 E2E：两条条目同 (subject,relation) 键（简称被归一，supersede 才能命中）",
          str(alpha.get("subject")) == str(beta.get("subject"))
          and str(alpha.get("relation")) == str(beta.get("relation")),
          "alpha=%r beta=%r" % (alpha.get("subject"), beta.get("subject")))
    check("插件级 E2E：旧条目 status=superseded（墓碑 last-wins 生效）",
          str(alpha.get("status")) == "superseded", str(alpha.get("status")))
    check("插件级 E2E：旧条目 supersededBy 指向新条目 id",
          str(alpha.get("supersededBy")) == str(beta.get("id")),
          "supersededBy=%r beta.id=%r" % (alpha.get("supersededBy"), beta.get("id")))
    check("插件级 E2E：新条目 status=active", str(beta.get("status")) == "active",
          str(beta.get("status")))
    check("插件级 E2E：审计流水含 memory_supersede",
          "memory_supersede" in (pe.get("auditKinds") or []),
          ",".join((pe.get("auditKinds") or [])[:12]))
    act = [e for e in facts if str(e.get("status") or "active") == "active"
           and str(e.get("subject")) == str(beta.get("subject"))]
    check("插件级 E2E：同键只剩 1 条 active（旧值不再可召回）", len(act) == 1,
          json.dumps([{"id": e.get("id"), "object": e.get("object")} for e in act],
                     ensure_ascii=False))


# ══════════════════════════════════════════════════════════════════════
# [3] F-B：疑问句过滤 + object 合法性 + 防误伤
# ══════════════════════════════════════════════════════════════════════
def _reasons(rec: dict | None) -> str:
    return " | ".join(str(r.get("reason")) for r in ((rec or {}).get("rejected") or []))


def test_fb_extract(out: dict) -> None:
    print("\n[3] F-B 疑问句不得当事实写入 + object 合法性 + 防误伤")
    ext = out.get("extract") or {}

    for cid, text in (("fb_what", "我的项目代号是什么？"),
                      ("fb_ma", "我的代号是贝塔吗"),
                      ("fb_howto", "项目代号怎么改？"),
                      ("fb_why", "为什么我的代号是贝塔"),
                      ("fb_what2", "我的代号是什么")):
        rec = ext.get(cid) or {}
        reqs = rec.get("requests") or []
        check("F-B %-9s requests 为空（疑问句不写入）" % cid, not reqs, fmt(rec))
        check("F-B %-9s rejected 非空且 reason 含 interrogative（留痕可审计）" % cid,
              "interrogative" in _reasons(rec), _reasons(rec) or fmt(rec))

    # F-B③ object 合法性：遍历整批用例，任何写入项的 object 都不得是疑问词
    bad = []
    for cid, rec in ext.items():
        for req in (rec.get("requests") or []):
            for item in (req.get("items") or []):
                obj = str(item.get("object") or "").strip()
                if obj in BANNED_OBJECTS or any(ch in obj for ch in "?？"):
                    bad.append({"case": cid, "object": obj})
    check("F-B③ 整批用例没有任何写入项的 object 落在疑问词闭集", not bad,
          json.dumps(bad, ensure_ascii=False))
    bad_content = []
    for cid, rec in ext.items():
        for req in (rec.get("requests") or []):
            for item in (req.get("items") or []):
                content = str(item.get("content") or "")
                if any(w in content for w in INTERROGATIVE_HINTS):
                    bad_content.append({"case": cid, "content": content})
    check("F-B③ 写入项 content 不含疑问代词", not bad_content,
          json.dumps(bad_content, ensure_ascii=False))

    # 防误伤：正常陈述 / 显式记忆请求仍必须写入
    for cid, expect_subject in (("fa_plain", "代号"), ("fa_remember", "代号")):
        rec = ext.get(cid) or {}
        req, item = first_item(rec)
        check("F-B④ %s 仍正常写入（防误伤）" % cid, bool(req) and bool(item), fmt(rec))
        if item:
            check("F-B④ %s subject/object 正确（%s / 贝塔）" % (cid, expect_subject),
                  _subject_ok(item.get("subject"), expect_subject)
                  and str(item.get("object") or "").strip() == "贝塔",
                  json.dumps({k: item.get(k) for k in ("subject", "relation", "object")},
                             ensure_ascii=False))
            check("F-B④ %s origin=user_fact" % cid, req.get("origin") == "user_fact",
                  str(req.get("origin")))
        check("F-B④ %s 无 rejected（正常陈述不得被疑问句过滤器误杀）" % cid,
              not (rec.get("rejected") or []), _reasons(rec) or fmt(rec))


# ══════════════════════════════════════════════════════════════════════
def main() -> int:
    print("LINGSHU 验收 · 用户事实提取器 + 记忆账本 supersede（F-A / F-B 回归）")
    job = {"extract": EXT_CASES, "e2e": E2E_SCENARIOS, "pluginE2E": PLUGIN_E2E}
    out = run_job(job)
    if out is None:
        print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
        return 1
    test_wiring(out)
    test_fa_extract(out)
    test_fa_supersede_e2e(out)
    test_plugin_e2e(out)
    test_fb_extract(out)
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("FAILED: " + "; ".join(FAIL[:20]))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
