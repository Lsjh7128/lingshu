// f6_storage_fault.mjs —— 混沌工程 v2 · F6 存储层故障注入（docs/23 §5.6）
//
// 为什么单独一个 Node 脚本：F6 的靶点是**真实的** `plugin/dsh-lingshu/index.js` +
// `plugin/dsh-lingshu/memorywriter.js`（唯一写方，I5）。引擎侧没有 JSONL 存储层，
// 用 Python 重写一个 store 等于测自己的赝品。所以这里：
//   1) 用与 host-sim.mjs 同形的 mock ctx 真正 `apply()` 插件；
//   2) 每个用例一个**独立临时数据目录**（LINGSHU_DATA_DIR），按用例预置脏数据 / IO 故障；
//   3) 触发真实 `session/event(user/message)` 与 `agent/pre-step`，读**落盘产物**判读。
//
// 纪律（docs/23 §7/§8）：
//   · 零模型调用（--no-model 必填；本脚本不 import 任何网络/模型模块）；
//   · 只写 os.tmpdir() 下的临时目录，结束即删；绝不写 $DSH_HOME 真实数据目录；
//   · 不修改插件代码；发现 bug 只记录（findings 由上层写 acceptance/chaos/v2/findings.md）；
//   · 时钟固定（--now），保证同参数下输出逐字节可复现。
//
// 用法：
//   node f6_storage_fault.mjs --no-model --now 1761541200000 --spec <cases.json>
// 输出：stdout 一份 JSON：{"harness":"f6_storage_fault","cases":[...],"notes":[...]}

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

// ── 参数 ────────────────────────────────────────────────
const argv = process.argv.slice(2);
function arg(name, def = null) {
  const i = argv.indexOf('--' + name);
  return i >= 0 && argv[i + 1] !== undefined ? argv[i + 1] : def;
}
if (!argv.includes('--no-model')) {
  console.error('f6: 必须显式传 --no-model（本脚本禁止任何模型调用）');
  process.exit(1);
}
const FIXED_NOW = Number(arg('now', '1761541200000'));   // 2025-10-27T04:20:00Z 附近，固定
const SPEC_FILE = arg('spec', '');
// 临时根目录：默认 os.tmpdir()；受沙箱限制时可指定 workspace 内目录。
const TMP_ROOT = arg('tmp-root', '');
const TMP_BASE = TMP_ROOT && String(TMP_ROOT).trim() ? path.resolve(String(TMP_ROOT).trim()) : os.tmpdir();
fs.mkdirSync(TMP_BASE, { recursive: true });
// spec 优先读 --spec 文件；否则读 stdin（避开受限环境写临时文件的问题）。
const specRaw = SPEC_FILE
  ? fs.readFileSync(SPEC_FILE, 'utf8')
  : fs.readFileSync(0, 'utf8');
const spec = JSON.parse(specRaw.replace(/^\uFEFF/, ''));   // 容忍 BOM

// 固定时钟：插件内部大量 `Date.now()`，不钉住就无法逐字节复现（docs/23 §6.1）。
const REAL_DATE_NOW = Date.now;
Date.now = () => FIXED_NOW;

// 动态 import：必须在设置完 LINGSHU_DATA_DIR 之前不做任何 apply；模块顶层只读一次 env，
// 但 stateDir() 是**调用时**读 env（index.js L66-72），所以每个用例 set env → apply 即可。
const { apply } = await import(new URL('../../plugin/dsh-lingshu/index.js', import.meta.url));
const { applyWriteRequests, applyCleanupRequests } =
  await import(new URL('../../plugin/dsh-lingshu/memorywriter.js', import.meta.url));

// 纯写入用例（绕过宿主事件，直接打真实 memorywriter 导出）：用于可控地构造
// 大批次 / 非法条目 / 跨批次 supersede / 清理墓碑，以及"半写"这种要自己造 store 的故障。
const PURE_SCENARIOS = new Set([
  'silent_loss_probe', 'bad_items', 'huge_batch', 'double_batch', 'clean_batch',
  'append_partial', 'cleanup_tombstone_conflict',
]);

// ── mock 宿主（与 host-sim.mjs#makeHost 同形；只 mock 宿主 API，不复制业务逻辑）──
function makeHost() {
  const sections = [];
  const hooks = {};
  const tools = [];
  const ctx = {
    tools: { register: (t) => { tools.push(t); } },
    systemPrompt: { section: (s) => { sections.push(s); } },
    on: (ev, fn) => { (hooks[ev] = hooks[ev] || []).push(fn); },
    get: (n) => { throw new Error(`cannot get property "${n}" without inject`); },
    effect: (fn) => { try { return fn(); } catch { /* ignore */ } },
    inject: () => {},
  };
  return { ctx, sections, hooks, tools };
}
async function fire(hooks, ev, args = []) {
  let out;
  for (const fn of hooks[ev] || []) out = await fn(...args);
  return out;
}
const userMsg = (text) => [{ role: 'user', content: [{ type: 'text', text }] }];
const sessionUser = (text) => ([{}, { type: 'user/message', seq: 1, time: FIXED_NOW,
  data: { message: { role: 'user', content: [{ type: 'text', text }] } } }]);

// ── 临时目录与落盘读取 ─────────────────────────────────
const tmpRoots = [];
function freshDataDir(tag) {
  const root = fs.mkdtempSync(path.join(TMP_BASE, `lingshu-f6-${tag}-`));
  tmpRoots.push(root);
  const dir = path.join(root, 'data');
  fs.mkdirSync(dir, { recursive: true });
  process.env.DSH_HOME = root;
  process.env.LINGSHU_DATA_DIR = dir;
  return { root, dir };
}
const memFile = (dir) => path.join(dir, 'memory.jsonl');
const stateFile = (dir) => path.join(dir, 'state.json');
const auditFile = (dir) => path.join(dir, 'audit.jsonl');
const readState = (dir) => { try { return JSON.parse(fs.readFileSync(stateFile(dir), 'utf8')); } catch { return null; } };
const readAudit = (dir) => {
  try {
    return fs.readFileSync(auditFile(dir), 'utf8').split('\n').filter(Boolean)
      .map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
  } catch { return []; }
};
const rawMemLines = (dir) => {
  try { return fs.readFileSync(memFile(dir), 'utf8').split('\n').filter(Boolean); } catch { return []; }
};
const parseRows = (dir) => rawMemLines(dir).map((l) => {
  try { return JSON.parse(l); } catch { return null; }
}).filter(Boolean);
const lastWins = (dir) => {
  const byId = new Map();
  for (const e of parseRows(dir)) byId.set(String(e.id), e);
  return [...byId.values()];
};
function setMemories(dir, entries) {
  fs.writeFileSync(memFile(dir), entries.map((e) => JSON.stringify(e)).join('\n') + '\n', 'utf8');
}
const auditKinds = (events) => {
  const out = {};
  for (const e of events) out[e.type || e.kind || 'unknown'] = (out[e.type || e.kind || 'unknown'] || 0) + 1;
  return out;
};
const baseEntry = (over = {}) => ({
  id: 'mem_001', type: 'semantic', subject: '项目', relation: '代号', object: '阿尔法',
  content: '我的项目代号是阿尔法', importance: 0.95, confidence: 0.95, layer: 'warm',
  status: 'active', lastAccess: FIXED_NOW - 86400000, tauDays: 30, sourceQuote: '我的项目代号是阿尔法',
  sourceTrust: 'user_explicit', origin: 'user_fact', turn: 1, ...over,
});
const goodItem = (over = {}) => ({
  type: 'semantic', subject: '项目', relation: '代号', object: '贝塔',
  content: '我的项目代号是贝塔', importance: 0.95, sourceQuote: '我的项目代号是贝塔', ...over,
});

// 一个用例的完整生命周期：预置 → apply → 写事实 → pre-step → 收产物
async function runCase(c) {
  const scenario = String(c.scenario || c.fault || '');
  const { root, dir } = freshDataDir(scenario || 'case');
  const rec = { case: c.case || scenario, scenario, ok: true, crash: false, error: null,
                metrics: {}, observations: {}, notes: [] };
  const t0 = process.hrtime.bigint();
  try {
    // ── 预置：脏数据 / IO 故障 ──────────────────────────
    if (scenario === 'existing_duplicate_ids') {
      setMemories(dir, [
        baseEntry({ id: 'mem_001', object: '阿尔法', status: 'active' }),
        baseEntry({ id: 'mem_001', object: '阿尔法', status: 'superseded',
                    supersededBy: 'mem_002', content: '我的项目代号是阿尔法（墓碑）' }),
        baseEntry({ id: 'mem_002', object: '贝塔', content: '我的项目代号是贝塔' }),
      ]);
      rec.observations.pre_rows = rawMemLines(dir).length;
    } else if (scenario === 'existing_torn_line') {
      const rows = [baseEntry({ id: 'mem_001', object: '阿尔法' }),
                    baseEntry({ id: 'mem_003', object: '伽马', content: '我的项目代号是伽马' })];
      // 中间塞一行**半截 JSON**（模拟掉电/并发写撕裂）
      fs.writeFileSync(memFile(dir),
        JSON.stringify(rows[0]) + '\n' + '{"id":"mem_002","object":"贝塔","content":"我的项目代号是贝' + '\n'
        + JSON.stringify(rows[1]) + '\n', 'utf8');
      rec.observations.pre_rows = rawMemLines(dir).length;
    } else if (scenario === 'clock_skew_future') {
      setMemories(dir, [
        baseEntry({ id: 'mem_001', object: '贝塔', importance: 0.95,
                    content: '我的项目代号是贝塔' }),
        // 未来 30 天：days() 会 clamp 负时间差（index.js L132-134），所以 potency 不会越权；
        // 这里用低 importance，正确行为是**排不到首位**（若排到首位说明 clamp 失效/行为漂移）。
        baseEntry({ id: 'mem_002', object: '伽马', importance: 0.2,
                    content: '我的项目代号是伽马', lastAccess: FIXED_NOW + 30 * 86400000 }),
      ]);
    } else if (scenario === 'clock_skew_past') {
      setMemories(dir, [
        baseEntry({ id: 'mem_001', object: '贝塔', importance: 0.95,
                    content: '我的项目代号是贝塔' }),
        baseEntry({ id: 'mem_002', object: '伽马', importance: 0.95,
                    content: '我的项目代号是伽马', lastAccess: FIXED_NOW - 900 * 86400000 }),
      ]);
    } else if (scenario === 'memory_path_is_dir' || scenario === 'append_throws'
               || scenario === 'read_unavailable' || scenario === 'read_throws') {
      // 把 memory.jsonl 做成**目录**：readFileSync → EISDIR（readJsonl 吞掉 → []），
      // appendFileSync → EISDIR（抛出 → 写失败必须被留痕）。这是真实可达的 IO 故障形态，
      // 不需要管理员权限，也不需要伪造 store。
      fs.mkdirSync(memFile(dir), { recursive: true });
      rec.observations.pre_path_kind = 'directory';
    } else if (scenario === 'huge_batch' || scenario === 'bad_items'
               || scenario === 'double_batch' || scenario === 'clean_batch'
               || scenario === 'silent_loss_probe' || scenario === 'cleanup_tombstone_conflict') {
      // 这几类不需要脏数据预置；写入内容由下面 requests 决定
      if (scenario === 'cleanup_tombstone_conflict') {
        setMemories(dir, [baseEntry({ id: 'mem_001', object: '阿尔法', status: 'active' })]);
      }
    } else if (Array.isArray(c.existing)) {
      setMemories(dir, c.existing);              // 语料自定义预置
    } else {
      setMemories(dir, []);
    }

    const h = makeHost();
    // coldTurns/warmTurns=0：跳过冷启动门控，让召回真的发生（与 host-sim.mjs 同口径）。
    apply(h.ctx, { coldTurns: 0, warmTurns: 0 });

    // ── 触发真实事件（插件集成用例）──────────────────────
    // 时钟偏移用例只读不写：避免"新写入的事实条目"干扰排序观测。
    const CLOCK_SCENARIOS = new Set(['clock_skew_future', 'clock_skew_past']);
    const factText = String(c.user_text || '我的项目代号是贝塔');
    if (!PURE_SCENARIOS.has(scenario)) {
      if (!CLOCK_SCENARIOS.has(scenario)) {
        await fire(h.hooks, 'session/event', sessionUser(factText));
      }
      // pre-step 会真正做召回 / 拼装 / 审计
      await fire(h.hooks, 'agent/pre-step', [
        { messages: userMsg(factText) },
        () => Promise.resolve({ kind: 'enter', messages: [] }),
      ]);
    }

    // ── 纯写入语义用例（直接打 memorywriter 真实导出）──────
    // 文件型 store：与 index.js#memStore 同形（read 全量 / append 追加），但文件在临时目录里。
    const fileStore = (file) => ({
      read: () => fs.existsSync(file)
        ? fs.readFileSync(file, 'utf8').split('\n').filter(Boolean)
          .map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean)
        : [],
      append: (objs) => fs.appendFileSync(file, objs.map((o) => JSON.stringify(o)).join('\n') + '\n', 'utf8'),
    });
    if (PURE_SCENARIOS.has(scenario) && scenario !== 'append_partial') {
      const file = memFile(dir);
      if (scenario === 'silent_loss_probe') {
        // 不抛错但什么也不写的 store：验证"writer 能否察觉 store 撒谎"。
        // 预期：writer 无法察觉（它只信任 store.append 返回）→ 这是**已知边界**，不是 bug。
        const noop = { read: () => [], append: () => {} };
        const w = applyWriteRequests([{ origin: 'user_fact', items: [goodItem()] }], noop,
                                     { now: FIXED_NOW });
        rec.observations.writer_out = { written: w.written, ids: w.ids, rejected: w.rejected.length };
        rec.metrics.silent_loss_undetectable = w.written > 0 ? 1 : 0;
        rec.notes.push('writer 只能确认"store.append 正常返回"，无法校验持久化；这是已知边界（docs/23 §9）');
      } else if (scenario === 'clean_batch') {
        const w = applyWriteRequests([{ origin: 'user_fact',
          items: [goodItem({ object: '贝塔' }), goodItem({ subject: '会议', relation: '时间', object: '周三' })] }],
          fileStore(file), { now: FIXED_NOW });
        rec.observations.writer_out = { written: w.written, ids: w.ids, rejected: w.rejected.length };
        rec.metrics.clean_batch_written = w.written;
      } else if (scenario === 'bad_items') {
        const w = applyWriteRequests([{ origin: 'user_fact', items: [
          goodItem({ object: '无耻', sourceQuote: '' }),        // 缺 sourceQuote
          goodItem({ object: '无度', importance: 1.5 }),         // importance 越界
          goodItem({ object: '无名', type: 'bogus' }),           // type 不在闭集
          goodItem({ object: '无文', content: '   ' }),          // 空内容
          goodItem({ object: '合规' }),                          // 唯一合法条目（对照）
        ] }], fileStore(file), { now: FIXED_NOW });
        rec.observations.writer_out = { written: w.written, ids: w.ids, rejected: w.rejected };
        rec.metrics.rejected_bad_items = w.rejected.length;
        rec.metrics.bad_items_written = w.written;
      } else if (scenario === 'huge_batch') {
        const n = Number(c.count || 1000);
        const items = [];
        // (subject, relation) 必须两两不同：否则会被 supersede 判成同键覆盖（写 1000 条变 1000 墓碑）
        for (let i = 0; i < n; i++) items.push(goodItem({ subject: '批量' + i, relation: '编号', object: 'O' + i }));
        const w = applyWriteRequests([{ origin: 'user_fact', items }], fileStore(file), { now: FIXED_NOW });
        rec.observations.writer_out = { written: w.written, ids_len: w.ids.length,
                                        rejected: w.rejected.length, superseded: (w.superseded || []).length };
        rec.metrics.huge_batch_written = w.written;
      } else if (scenario === 'double_batch') {
        const store = fileStore(file);
        applyWriteRequests([{ origin: 'user_fact',
          items: [goodItem({ object: '阿尔法' })] }], store, { now: FIXED_NOW });
        const w2 = applyWriteRequests([{ origin: 'user_fact',
          items: [goodItem({ object: '贝塔' })] }], store, { now: FIXED_NOW });
        rec.observations.writer_out = { written: w2.written, superseded: w2.superseded };
        rec.metrics.double_batch_superseded = (w2.superseded || []).length;
      } else if (scenario === 'cleanup_tombstone_conflict') {
        const store = fileStore(file);
        const cw = applyCleanupRequests([{ id: 'mem_001', reason: 'test_data',
          sourceQuote: 'F6 清理故障注入' }], store, { now: FIXED_NOW });
        rec.observations.cleanup = { cleaned: cw.cleaned, rejected: cw.rejected };
        rec.metrics.cleanup_cleaned = cw.cleaned;
      }
    }
    if (scenario === 'append_partial') {
      // 半写：append 先落一半再抛错（模拟写入过程中磁盘/进程故障）。
      // 用真实 memorywriter + 会半写的 store，检验"不得静默成功 + 重试不产生 id 冲突"。
      const file2 = path.join(root, 'partial.jsonl');
      const store = {
        read: () => fs.existsSync(file2)
          ? fs.readFileSync(file2, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l))
          : [],
        append: (objs) => {
          const half = objs.slice(0, Math.max(1, Math.floor(objs.length / 2)));
          fs.appendFileSync(file2, half.map((o) => JSON.stringify(o)).join('\n') + '\n', 'utf8');
          throw new Error('EIO: simulated partial write failure');
        },
      };
      const items = [goodItem({ object: '甲' }), goodItem({ object: '乙' }), goodItem({ object: '丙' })];
      let threw = null;
      try { applyWriteRequests([{ origin: 'user_fact', items }], store, { now: FIXED_NOW }); }
      catch (e) { threw = String(e && e.message || e); }
      const rowsAfterFail = fs.existsSync(file2)
        ? fs.readFileSync(file2, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
      let threw2 = null;
      let w2 = null;
      try { w2 = applyWriteRequests([{ origin: 'user_fact', items }], store, { now: FIXED_NOW }); }
      catch (e) { threw2 = String(e && e.message || e); }
      const rowsAfterRetry = fs.existsSync(file2)
        ? fs.readFileSync(file2, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l)) : [];
      const ids = rowsAfterRetry.map((r) => r.id);
      rec.observations.partial = {
        threw_first: threw, rows_after_failure: rowsAfterFail.length,
        threw_retry: threw2, retry_written: w2 ? w2.written : null,
        rows_after_retry: rowsAfterRetry.length,
        duplicate_ids: ids.length - new Set(ids).size,
      };
      rec.metrics.threw_on_partial = threw ? 1 : 0;
      rec.metrics.duplicate_ids_after_retry = ids.length - new Set(ids).size;
    }

    // ── 收集落盘证据 ────────────────────────────────────
    const st = readState(dir) || {};
    const events = readAudit(dir);
    const rows = parseRows(dir);
    const lw = lastWins(dir);
    rec.observations.audit_kinds = auditKinds(events);
    rec.observations.hook_errors = events.filter((e) => (e.type || e.kind) === 'hook_error')
      .map((e) => String(e.err || '').slice(0, 120));
    rec.observations.memory_rows = rows.length;
    rec.observations.memory_ids = rows.map((r) => String(r.id));
    rec.observations.lastwins = lw.map((r) => ({ id: String(r.id), object: String(r.object || ''),
                                                  status: String(r.status || '') }));
    rec.observations.state = {
      stateWrites: st.stateWrites ?? null, factWrites: st.factWrites ?? null,
      supersedeEvents: st.supersedeEvents ?? null, turns: st.turns ?? null,
      lastEventHits: (st.lastEvent && st.lastEvent.hits) || [],
      blocked: Boolean(st.control && st.control.blocked),
      injectionOrder: (st.control && st.control.injectionOrder) || [],
    };
    rec.observations.memory_write_events = events
      .filter((e) => (e.type || e.kind) === 'memory_write')
      .map((e) => ({ written: e.written, ids: e.ids, rejected: e.rejected }));
    rec.observations.supersede_events = events
      .filter((e) => (e.type || e.kind) === 'memory_supersede')
      .map((e) => ({ n: (e.superseded || []).length }));

    // ── 逐场景机检（结果只描述"观测到的事实"，不在这里下"通过"结论）──
    const m = rec.metrics;
    m.hook_error_count = rec.observations.hook_errors.length;
    m.memory_rows = rows.length;
    m.lastwins_active = lw.filter((r) => String(r.status) === 'active').length;
    m.recall_hits = (rec.observations.state.lastEventHits || []).length;
    m.silent_empty_recall = (rec.observations.state.lastEventHits || []).length === 0 ? 1 : 0;
    m.state_writes = st.stateWrites ?? 0;
    m.supersede_events = (rec.observations.supersede_events || [])
      .reduce((n, x) => n + Number(x.n || 0), 0);
    m.rejected_total = (rec.observations.memory_write_events || [])
      .reduce((n, e) => n + ((e.rejected || []).length), 0);
    m.raw_rows = rawMemLines(dir).length;
    m.unparsable_rows = m.raw_rows - rows.length;

    switch (scenario) {
      case 'existing_duplicate_ids': {
        const ids = lw.map((r) => String(r.id));
        m.duplicate_id_collapsed = ids.length === new Set(ids).size ? 1 : 0;
        m.superseded_still_active = lw.filter((r) => String(r.id) === 'mem_001'
          && String(r.status) === 'active').length;
        m.recalled_alpha = (rec.observations.state.lastEventHits || []).includes('mem_001') ? 1 : 0;
        break;
      }
      case 'existing_torn_line': {
        m.torn_line_silent_skip = m.unparsable_rows;
        m.torn_entry_recalled = (rec.observations.state.lastEventHits || []).includes('mem_002') ? 1 : 0;
        rec.notes.push('撕裂行被 readJsonl 逐行 try/catch 静默丢弃（index.js L85-92），无审计事件');
        break;
      }
      case 'clock_skew_future': {
        const hits = rec.observations.state.lastEventHits || [];
        // days() clamp 生效 → 低 importance 的未来条目不应排首位
        m.future_clamped = hits.length > 0 && hits[0] !== 'mem_002' ? 1 : 0;
        rec.notes.push('未来 lastAccess 由 days() clamp（index.js L132-134）；只断言"不越权"，不推断 potency 数值');
        break;
      }
      case 'clock_skew_past': {
        const hits = rec.observations.state.lastEventHits || [];
        // 900 天前条目 potency 已低于 archiveThreshold → 被归档门滤掉（应完全不可召回）
        m.stale_archived = hits.includes('mem_002') ? 0 : 1;
        rec.notes.push('900 天前条目被 archiveThreshold 归档门滤掉（plugin recall L159），不进入命中列表');
        break;
      }
      case 'memory_path_is_dir':
      case 'append_throws':
      case 'read_unavailable':
      case 'read_throws': {
        m.write_failure_surfaced = m.hook_error_count > 0 ? 1 : 0;
        m.false_success = (m.state_writes > 0 && m.memory_rows === 0) ? 1 : 0;
        rec.notes.push('memory.jsonl 是目录：读被 readJsonl 吞成 []（静默），写抛 EISDIR 并被 hook_error 留痕');
        break;
      }
      case 'bad_items': {
        // 写路径：非法条目必须 rejected（纯写入用例已给出 written/rejected，勿被 state 覆盖）
        m.bad_items_rejected_seen = m.rejected_total;
        break;
      }
      case 'huge_batch': {
        m.huge_batch_rows = m.memory_rows;
        m.huge_batch_monotonic = isMonotonic(rec.observations.memory_ids) ? 1 : 0;
        break;
      }
      case 'double_batch': {
        m.double_batch_audit_events = m.supersede_events;
        break;
      }
      case 'clean_batch': {
        m.clean_batch_audit_events = m.supersede_events;
        break;
      }
      case 'cleanup_tombstone_conflict': {
        // 清理墓碑 + 重复 id：读取侧 last-wins 必须让 suppressed 生效
        m.cleanup_visible = lw.filter((r) => String(r.status) === 'suppressed').length;
        break;
      }
      default:
        break;
    }

    // 时长（只作画像，不进 digest：由上层族模块剔除）
    rec.metrics.elapsed_ms = Number((process.hrtime.bigint() - t0) / 1000000n);
  } catch (err) {
    rec.ok = false;
    rec.crash = true;
    rec.error = String(err && err.stack || err).slice(0, 500);
  } finally {
    try { fs.rmSync(root, { recursive: true, force: true }); } catch { /* 忽略 */ }
  }
  return rec;
}

function isMonotonic(ids) {
  const nums = ids.map((x) => Number(String(x).replace(/^mem_/, '')))
    .filter((n) => Number.isFinite(n));
  for (let i = 1; i < nums.length; i++) if (nums[i] < nums[i - 1]) return false;
  return true;
}

// ── main ────────────────────────────────────────────────
const cases = Array.isArray(spec.cases) ? spec.cases : [];
const results = [];
for (const c of cases) results.push(await runCase(c));

Date.now = REAL_DATE_NOW;
const out = {
  harness: 'f6_storage_fault',
  version: '2.0.0',
  now: FIXED_NOW,
  model_calls: 0,
  cases: results,
  notes: [
    'F6 只做**函数级/文件级**故障注入，不模拟坏道、断电、多进程并发写（docs/23 §9）',
    '所有用例在 os.tmpdir() 独立目录内完成，结束即删；未触碰 $DSH_HOME 真实数据目录',
  ],
};
process.stdout.write(JSON.stringify(out, null, 2) + '\n');
