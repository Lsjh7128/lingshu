// memory-paths-sim.mjs —— v0.2 布局 / 作用域记忆 JS 镜像自检 + Python parity 驱动
//
// 两种用法：
//   node memory-paths-sim.mjs --assert
//     自检（≥40 条断言）。最后一行固定为 `memory-paths-sim: N/N passed, 0 failed`。
//   node memory-paths-sim.mjs --parity <cases.json>
//     读用例 → stdout 最后一行输出一条 JSON，供 tests/test_v02_parity.py 与
//     Python `engine/layout.py` 逐字段比对。用例缺失/解析失败 → stderr + exit 2。
//
// 注意：本文件是**逻辑层**自检，不启宿主、不碰真实 profile；所有落盘都在系统临时目录。

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import {
  LAYOUT_SCHEMA, SANDBOX_ID_RE, PERSONA_ID_RE, SKELETON_SECTION,
  AUDIT_KINDS_V02, AUDIT_KINDS_RESERVED,
  canonicalizePath, sandboxId, sandboxIdForPath,
  resolveMemoryPaths, readLayout, writeLayout, detectLayout,
  readJsonl, foldRows, applyTombstoneEvent, applyRestoreEvent,
  purgeRedact, displayText, trashView, decideScope,
  mergeLayers, lockKey, Lock, LockTimeout,
  fileSignature, shouldReload, checkUca01Text, makeMemoryRow,
  MemoryRuntime,
} from './memory-paths.js';

const argv = process.argv.slice(2);
const PARITY_IDX = argv.indexOf('--parity');

// ── parity 模式 ─────────────────────────────────────────────
if (PARITY_IDX >= 0) {
  const casesPath = argv[PARITY_IDX + 1];
  if (!casesPath) {
    process.stderr.write('memory-paths-sim: --parity 需要用例文件路径\n');
    process.exit(2);
  }
  let cases;
  try {
    cases = JSON.parse(fs.readFileSync(casesPath, 'utf8'));
  } catch (err) {
    process.stderr.write(`memory-paths-sim: 读取/解析用例失败 ${casesPath}: ${err.message}\n`);
    process.exit(2);
  }
  if (!cases || typeof cases !== 'object') {
    process.stderr.write(`memory-paths-sim: 用例不是对象 ${casesPath}\n`);
    process.exit(2);
  }

  const pyStr = (v) => (v === null || v === undefined ? 'None' : String(v));
  const cmpStr = (a, b) => (a < b ? -1 : a > b ? 1 : 0);
  const guard = (fn) => {
    try {
      return fn();
    } catch (err) {
      return { error: String((err && err.message) || err) };
    }
  };

  // 1) canonical：用例一律显式给 realpath（期望真实路径）；也允许缺失时用默认 realpath。
  const canonical = (cases.canonical || []).map((c) => guard(() => {
    const opts = { windows: process.platform === 'win32' };
    if (c && c.realpath !== undefined && c.realpath !== null) {
      const fixed = String(c.realpath);
      opts.realpath = () => fixed;
    }
    return canonicalizePath(c.input, opts);
  }));

  // 2) scope
  const scope = (cases.scope || []).map((c) => guard(() => decideScope(c.text)));

  // 3) uca：只输出 verdict + 排序后的 reasons
  const uca = (cases.uca || []).map((c) => guard(() => {
    const r = checkUca01Text(c.text);
    return { verdict: r.verdict, reasons: [...r.reasons].sort() };
  }));

  // 4) merge：selected_ids 按 selected 顺序；dropped 按 (id, reason) 字典序（Lead 约定）
  const merge = (cases.merge || []).map((c) => guard(() => {
    const res = mergeLayers(c.rows || [], {
      sandboxId: c.sandbox_id,
      personaId: c.persona_id,
      budget: c.budget === undefined ? 900 : c.budget,
      topK: c.top_k === undefined ? 3 : c.top_k,
    });
    const dropKey = (d) => `${pyStr(d[0])}\u0000${d[1]}`;
    const dropped = res.dropped.map((d) => [d.id === undefined ? null : d.id, d.reason]);
    dropped.sort((a, b) => cmpStr(dropKey(a), dropKey(b)));
    return {
      selected_ids: res.selected.map((r) => r.id),
      dropped_ids_reasons: dropped,
      conflicts: res.conflicts,
      global_tokens: res.global_tokens,
      local_count: res.local_count,
    };
  }));

  // 5) lock_keys
  const lockKeys = (cases.lock_keys || []).map((c) => guard(() => lockKey(c.kind, {
    sandboxId: c.sandbox_id,
    personaId: c.persona_id,
    scope: c.scope,
  })));

  // 6) paths：六键改成相对 root 的 '/' 路径，键名不变
  const paths = (cases.paths || []).map((c) => guard(() => {
    const p = resolveMemoryPaths(c.root, c.sandbox_id, c.persona_id);
    const base = path.resolve(String(c.root));
    const out = {};
    for (const [k, v] of Object.entries(p)) out[k] = path.relative(base, v).split(path.sep).join('/');
    return out;
  }));

  // 输出 ASCII 安全 JSON：中文走 \uXXXX 转义，语义等价。
  // 原因：调用方（tests/test_v02_parity.py）用 subprocess text=True 读 stdout，
  // 在 zh-CN Windows 上按 GBK 解码，直接输出 UTF-8 中文会 UnicodeDecodeError。
  const asciiJson = (obj) => JSON.stringify(obj)
    .replace(/[\u007f-\uffff]/g, (ch) => `\\u${ch.charCodeAt(0).toString(16).padStart(4, '0')}`);
  // 用 writeSync(1) 同步写，避免 pipe 场景下 process.exit 截断 stdout。
  fs.writeSync(1, asciiJson({
    canonical, scope, uca, merge, lock_keys: lockKeys, paths,
  }) + '\n');
  process.exit(0);
}

// ── 自检模式 ───────────────────────────────────────────────
const ASSERT_ONLY = argv.includes('--assert');
const results = [];

function check(label, cond, detail = '') {
  results.push({ label, cond: Boolean(cond) });
  if (!ASSERT_ONLY) console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}${detail ? '  — ' + detail : ''}`);
}
function throws(label, fn, re) {
  try {
    fn();
    check(label, false, 'expected throw, none');
  } catch (err) {
    const msg = String((err && err.message) || err);
    check(label, re ? re.test(msg) : true, msg);
  }
}
function eq(label, got, want) {
  check(label, JSON.stringify(got) === JSON.stringify(want), `got=${JSON.stringify(got)}`);
}

const TMP = fs.mkdtempSync(path.join(os.tmpdir(), 'lingshu-mem-'));
const T = (...p) => path.join(TMP, ...p);
const ROOT = T('data');
fs.mkdirSync(ROOT, { recursive: true });

const fake = (real) => () => real;
const mkRow = (id, scope, extra = {}) => makeMemoryRow({
  memId: id, ts: 1800000000, memType: 'semantic', origin: 'user_fact',
  subject: extra.subject ?? '偏好', relation: extra.relation ?? '是', obj: extra.obj ?? 'x',
  sandbox: extra.sandbox ?? 'a1b2c3d4', persona: extra.persona ?? 'butler',
  scope, importance: extra.importance ?? 0.5, display: extra.display,
});

// ── A. 常量与版本 ─────────────────────────────────────────
{
  check('A1 LAYOUT_SCHEMA=layout-0.2', LAYOUT_SCHEMA === 'layout-0.2');
  check('A2 sandboxId 正则只收 8 位小写 hex',
    SANDBOX_ID_RE.test('a1b2c3d4') && !SANDBOX_ID_RE.test('A1B2C3D4') && !SANDBOX_ID_RE.test('a1b2c3d'));
  check('A3 personaId 正则收 [A-Za-z0-9_-]{1,64}',
    PERSONA_ID_RE.test('butler') && PERSONA_ID_RE.test('a-b_C9') && !PERSONA_ID_RE.test('../x')
    && !PERSONA_ID_RE.test('x'.repeat(65)) && !PERSONA_ID_RE.test('中文'));
  check('A4 S3 skeleton 段 order=105',
    SKELETON_SECTION.name === 'lingshu:skeleton' && SKELETON_SECTION.order === 105);
  eq('A5 S5 新增审计 kind 齐全', [...AUDIT_KINDS_V02].sort(),
    ['migration_apply', 'panel_forget', 'panel_purge', 'panel_restore', 'sandbox_mapped']);
  eq('A6 S5 skeleton 预留 kind 齐全', [...AUDIT_KINDS_RESERVED].sort(),
    ['skeleton_generated', 'skeleton_rebuilt', 'skeleton_rejected']);
  check('A7 新增与预留不重叠', !AUDIT_KINDS_V02.some((k) => AUDIT_KINDS_RESERVED.includes(k)));
}

// ── B. §4.1 规范化（5 组等价 + 边界）──────────────────────
{
  const rp = 'D:/Novel';
  const a = canonicalizePath('D:\\Novel', { realpath: fake(rp) });
  const b = canonicalizePath('d:\\novel', { realpath: fake(rp) });
  const c = canonicalizePath('D:\\Novel\\', { realpath: fake(rp) });
  const d = canonicalizePath('D:\\NOVEL~1', { realpath: fake(rp) });
  const e = canonicalizePath('D:\\Novel\\.\\Novel', { realpath: fake(rp) });
  check('B1 5 组等价输入同 id', new Set([a, b, c, d, e].map(sandboxId)).size === 1);
  check('B2 Windows 小写归一', a === 'd:/novel' && b === 'd:/novel', `a=${a}`);
  check('B3 去尾分隔符（根除外）', c === 'd:/novel', c);
  check('B4 相邻路径 id 不同', sandboxId(a) !== sandboxId(canonicalizePath('D:\\Novel2', { realpath: fake('D:/Novel2') })));
  check('B5 Unicode NFC 等价',
    canonicalizePath('D:\\Cafe\u0301', { realpath: fake('D:/Café') }).normalize('NFC')
      === canonicalizePath('D:\\Café', { realpath: fake('D:/Café') }));
  check('B6 折叠重复分隔符', canonicalizePath('D:\\\\Novel\\\\sub', { realpath: (s) => s, windows: true }) === 'd:/novel/sub');
  check('B7 盘符根不被去尾', canonicalizePath('D:/', { realpath: (s) => s, windows: true }) === 'd:/');
  check('B8 windows=false 保留大小写',
    canonicalizePath('D:\\Novel', { realpath: (s) => s, windows: false }) === 'D:/Novel');
  check('B9 sandboxId 为 8 位 hex 且幂等',
    /^[0-9a-f]{8}$/.test(sandboxId(a)) && sandboxId(a) === sandboxId(a));
  check('B10 sandboxIdForPath 走同一规范化',
    sandboxIdForPath('d:\\novel', { realpath: fake(rp) }) === sandboxId(a));
  throws('B11 null 路径抛错', () => canonicalizePath(null), /path required/);
}

// ── C. S1 六键路径与隔离（§4.4）──────────────────────────
{
  const p = resolveMemoryPaths(ROOT, 'a1b2c3d4', 'butler');
  eq('C1 恰好六键（顺序稳定）', Object.keys(p),
    ['globalMemory', 'localMemory', 'pending', 'skeleton', 'state', 'personaState']);
  check('C2 六键全在数据根下', Object.values(p).every((x) => x.startsWith(path.resolve(ROOT) + path.sep)));
  check('C3 global 落在 personas/{pid}/', p.globalMemory === path.join(path.resolve(ROOT), 'personas', 'butler', 'global_memory.jsonl'));
  check('C4 local 落在 sandboxes/{sid}/{pid}/', p.localMemory === path.join(path.resolve(ROOT), 'sandboxes', 'a1b2c3d4', 'butler', 'local_memory.jsonl'));
  check('C5 D17 skeleton 与 local 同层', path.dirname(p.skeleton) === path.dirname(p.localMemory));
  check('C6 global 与 local 是不同文件', p.globalMemory !== p.localMemory);
  throws('C7 非法 sandboxId fail-closed', () => resolveMemoryPaths(ROOT, 'nope', 'butler'), /invalid sandbox_id/);
  throws('C8 非法 personaId fail-closed', () => resolveMemoryPaths(ROOT, 'a1b2c3d4', '../x'), /invalid persona_id/);
  throws('C9 personaId=".." fail-closed', () => resolveMemoryPaths(ROOT, 'a1b2c3d4', '..'), /invalid persona_id/);
  throws('C10 空 personaId fail-closed', () => resolveMemoryPaths(ROOT, 'a1b2c3d4', ''), /invalid persona_id/);
}

// ── D. D6 布局识别与 layout.json 幂等 ────────────────────
{
  const r1 = T('layout-empty');
  fs.mkdirSync(r1, { recursive: true });
  check('D1 空目录识别为 empty', detectLayout(r1).kind === 'empty' && detectLayout(r1).layout === null);
  fs.writeFileSync(path.join(r1, 'state.json'), '{}');
  check('D2 平铺 state.json 识别为 legacy', detectLayout(r1).kind === 'legacy');
  fs.mkdirSync(path.join(r1, 'workspace_code'), { recursive: true });
  fs.mkdirSync(path.join(r1, 'profiles', 'lab'), { recursive: true });
  eq('D3 四代来源收集', detectLayout(r1).sources, ['legacy', 'profiles/lab', 'workspace_code']);
  const w = writeLayout(r1, { migratedFrom: ['legacy', 'legacy', 'workspace_code'], now: 1800000000 });
  check('D4 layout.json 写入成功', w.ok === true && w.layout.created_at === 1800000000);
  eq('D5 migrated_from 去重排序', w.layout.migrated_from, ['legacy', 'workspace_code']);
  check('D6 readLayout 校验 schema', readLayout(r1).schema_version === LAYOUT_SCHEMA);
  check('D7 二次迁移被拒（幂等）', writeLayout(r1).reason === 'layout_exists');
  const v = detectLayout(r1);
  eq('D8 v0.2 识别并回带来源', [v.kind, v.layout, v.sources], ['v02', 'layout-0.2', ['legacy', 'workspace_code']]);
}

// ── E. D8 墓碑 / 恢复 / 回收站 ───────────────────────────
{
  const base = [mkRow('m1', 'local'), mkRow('m2', 'global')];
  check('E1 last-wins：同 id 后写覆盖',
    foldRows([...base, { ...base[0], obj: '新' }]).active.length === 2
      && foldRows([...base, { ...base[0], obj: '新' }]).active.find((r) => r.id === 'm1').obj === '新');
  const full = [...base, { ...base[0], status: 'suppressed', content: '[已彻底删除]' }];
  const f1 = foldRows(full);
  check('E2 全量墓碑行进回收站', f1.suppressed.length === 1 && f1.active.length === 1 && f1.suppressed[0].id === 'm1');
  const tomb = applyTombstoneEvent(base, 'm1', { at: 1800000000 });
  check('E3 append-only（原行数 +1，入参未改）',
    tomb.length === 3 && base.length === 2 && tomb[2].type === 'tombstone' && tomb[2].by === 'user_panel');
  check('E4 事件墓碑折叠为 suppressed',
    foldRows(tomb).suppressed.length === 1 && foldRows(tomb).suppressed[0].suppressed_at === 1800000000);
  const restored = applyRestoreEvent(tomb, 'm1', { at: 1800000100 });
  check('E5 恢复事件撤销面板删除（不删墓碑行）',
    foldRows(restored).active.length === 2 && foldRows(restored).suppressed.length === 0 && restored.length === 4);
  check('E6 全量墓碑不因 restore 复活',
    foldRows(applyRestoreEvent(full, 'm1')).suppressed.length === 1);
  check('E7 文件顺序：restore 后又 tombstone → suppressed',
    foldRows(applyTombstoneEvent(restored, 'm1', { at: 1800000200 })).suppressed.length === 1);
  const red = purgeRedact(foldRows(tomb).suppressed[0], { at: 1800000300 });
  check('E8 D16 彻底删除=redact（留骨架）',
    red.status === 'suppressed' && red.content === '[已彻底删除]' && red.display_text === '[已彻底删除]' && red.redacted_at === 1800000300);
  const tv = trashView(restored.length ? applyTombstoneEvent(base, 'm2', { at: 1800000000 - 86400 * 3 }) : [], { now: 1800000000 });
  check('E9 回收站显示保留天数与 redact 标记',
    tv.length === 1 && tv[0].id === 'm2' && tv[0].days_in_trash === 3 && tv[0].redacted === false && tv[0].scope === 'global');
  check('E10 displayText 兜底：display_text > subject/relation/object > content > 记忆 id',
    displayText({ id: 'x', display_text: '人话' }) === '人话'
      && displayText({ id: 'x', subject: '我', relation: '喝', object: '茶' }) === '我喝茶'
      && displayText({ id: 'x', content: 'y'.repeat(100) }).length === 80
      && displayText({ id: 'x' }) === '记忆 x');
  check('E11 readJsonl 坏行跳过 + BOM 容错',
    (() => {
      const p = T('rows.jsonl');
      fs.writeFileSync(p, '\uFEFF{"id":"a"}\r\nnot-json\r\n[1,2]\r\n{"id":"b"}\n', 'utf8');
      const rows = readJsonl(p);
      return rows.length === 2 && rows[0].id === 'a' && rows[1].id === 'b';
    })());
  check('E12 readJsonl 文件缺失返回空数组', readJsonl(T('nope.jsonl')).length === 0);
}

// ── F. §4.3 scope 路由（8 例）────────────────────────────
{
  const cases = [
    ['以后都喝红茶', 'global'], ['这个项目要保密', 'local'],
    ['以后都在这个项目里用中文', 'local'], ['今天天气不错', 'pending'],
    ['永远别再提这件事', 'global'], ['本仓库禁止提交 node_modules', 'local'],
    ['这次先这样', 'local'], ['不管在哪个项目，我都不吃香菜', 'global'],
  ];
  for (const [text, want] of cases) check(`F scope: ${text.slice(0, 8)} → ${want}`, decideScope(text).scope === want);
  const both = decideScope('以后都在这个项目里用中文');
  eq('F9 两者同时命中 → reasons 带 local_wins', both.reasons, ['project_reference', 'persistent_adverb', 'local_wins']);
  check('F10 pending 可晋升、global 不可', decideScope('天气').promotable === true && decideScope('永远').promotable === false);
}

// ── G. D9 合并裁决（§4.2 §4.4）──────────────────────────
{
  const g = mkRow('g1', 'global', { obj: '红茶', sandbox: 'deadbeef', importance: 0.95, subject: '喜好', relation: '喝' });
  const l = mkRow('l1', 'local', { obj: '绿茶', importance: 0.5, subject: '喜好', relation: '喝' });
  const m = mergeLayers([g, l], { sandboxId: 'a1b2c3d4', personaId: 'butler' });
  eq('G1 同键冲突 local 胜', m.selected.map((r) => r.id), ['l1']);
  check('G2 冲突记录 winner=local', m.conflicts.length === 1 && m.conflicts[0].winner === 'local'
    && JSON.stringify(m.conflicts[0].dropped_ids) === '["g1"]');
  const cross = mergeLayers([mkRow('g2', 'global', { sandbox: 'ffffffff' })], { sandboxId: 'a1b2c3d4', personaId: 'butler' });
  eq('G3 global 行可跨沙盒读取', cross.selected.map((r) => r.id), ['g2']);
  const badSandbox = mergeLayers([mkRow('l2', 'local', { sandbox: 'ffffffff' })], { sandboxId: 'a1b2c3d4', personaId: 'butler' });
  check('G4 行内 sandbox 标签不符被丢弃', badSandbox.dropped[0].reason === 'sandbox_label_mismatch');
  const badPersona = mergeLayers([mkRow('g3', 'global', { persona: 'architect' })], { sandboxId: 'a1b2c3d4', personaId: 'butler' });
  check('G5 行内 persona 标签不符被丢弃', badPersona.dropped[0].reason === 'persona_label_mismatch');
  const pend = mergeLayers([mkRow('p1', 'pending')], { sandboxId: 'a1b2c3d4', personaId: 'butler' });
  check('G6 pending 默认不参与检索', pend.dropped[0].reason === 'pending_not_searchable' && pend.selected.length === 0);
  const legacy = mergeLayers([mkRow('m0', 'local', { sandbox: 'legacy' })], { sandboxId: 'a1b2c3d4', personaId: 'butler' });
  eq('G7 legacy 溯源行视为当前沙盒', legacy.selected.map((r) => r.id), ['m0']);
  const floor = mergeLayers([
    mkRow('g4', 'global', { importance: 0.99 }), mkRow('l3', 'local', { importance: 0.1 }),
  ], { sandboxId: 'a1b2c3d4', personaId: 'butler', topK: 1 });
  eq('G8 local 保底挤掉 top-K 里的 global', floor.selected.map((r) => r.id), ['l3']);
  const quota = mergeLayers([
    mkRow('g5', 'global', { importance: 0.99, display: 'x'.repeat(100) }),
    mkRow('l4', 'local', { importance: 0.1 }),
  ], { sandboxId: 'a1b2c3d4', personaId: 'butler', budget: 100 });
  check('G9 global 配额 ≤ 30% 预算时丢弃 global_quota',
    quota.dropped.some((d) => d.id === 'g5' && d.reason === 'global_quota') && quota.global_tokens === 0);
  const topk = mergeLayers([mkRow('a1', 'local'), mkRow('a2', 'local'), mkRow('a3', 'local')],
    { sandboxId: 'a1b2c3d4', personaId: 'butler', topK: 2 });
  check('G10 top-K 限制残留记 top_k',
    topk.selected.length === 2 && topk.dropped.length === 1 && topk.dropped[0].reason === 'top_k');
  const order = mergeLayers([
    mkRow('b2', 'local', { importance: 0.2 }), mkRow('b1', 'local', { importance: 0.2 }),
    mkRow('b3', 'local', { importance: 0.9 }),
  ], { sandboxId: 'a1b2c3d4', personaId: 'butler' });
  eq('G11 排序：potency 降序、同效力按 id 升序', order.selected.map((r) => r.id), ['b3', 'b1', 'b2']);
  const budgetDrop = mergeLayers([mkRow('c1', 'local', { display: 'x'.repeat(3000) })],
    { sandboxId: 'a1b2c3d4', personaId: 'butler', budget: 100 });
  check('G12 超预算记 budget', budgetDrop.dropped[0].reason === 'budget');
  check('G13 返回体字段齐全',
    ['selected', 'dropped', 'conflicts', 'tokens_used', 'token_budget', 'global_tokens', 'global_cap', 'local_count']
      .every((k) => k in m));
}

// ── H. §4.7 锁键 ─────────────────────────────────────────
{
  check('H1 memory 锁键', lockKey('memory', { sandboxId: 'a1b2c3d4' }) === 'lock:memory:a1b2c3d4');
  check('H2 global 锁键', lockKey('global', { personaId: 'butler' }) === 'lock:global:butler');
  check('H3 audit 锁键全局单本', lockKey('audit') === 'lock:audit');
  check('H4 skeleton 锁键独立于 memory（S2）',
    lockKey('skeleton', { sandboxId: 'a1b2c3d4', personaId: 'butler' }) === 'lock:skeleton:a1b2c3d4:butler'
      && lockKey('skeleton', { sandboxId: 'a1b2c3d4', personaId: 'butler' }) !== lockKey('memory', { sandboxId: 'a1b2c3d4' }));
  check('H5 compaction 锁键默认 local', lockKey('compaction', { scope: 'global' }) === 'lock:compaction:global');
  throws('H6 未知 kind 抛错', () => lockKey('nope'), /unknown kind/);
  throws('H7 非法 sandboxId 抛错', () => lockKey('memory', { sandboxId: '../x' }), /invalid sandbox_id/);
}

// ── I. D10 目录锁 ────────────────────────────────────────
{
  const lockRoot = T('locks');
  const lk = new Lock(lockRoot, 'lock:audit', { timeout: 1 });
  lk.acquire();
  check('I1 mkdir 原子加锁（held + 目录存在）', lk.held === true && fs.existsSync(lk.dir));
  check('I2 owner.json 带 pid/host/at/token',
    (() => {
      const o = JSON.parse(fs.readFileSync(path.join(lk.dir, 'owner.json'), 'utf8'));
      return o.pid === process.pid && typeof o.host === 'string' && typeof o.at === 'number' && typeof o.token === 'string';
    })());
  let timeoutErr = null;
  try {
    new Lock(lockRoot, 'lock:audit', { timeout: 0.2 }).acquire();
  } catch (err) {
    timeoutErr = err;
  }
  check('I3 同 key 竞争超时抛 LockTimeout', timeoutErr instanceof LockTimeout, String(timeoutErr));
  lk.release();
  check('I4 释放后目录消失', lk.held === false && !fs.existsSync(lk.dir));
  check('I5 释放后可重新获取', new Lock(lockRoot, 'lock:audit', { timeout: 0.5 }).acquire().held === true);
  // 陈旧锁（30s）可抢占
  const staleKey = 'lock:global:butler';
  const digest = crypto.createHash('sha256').update(staleKey, 'utf8').digest('hex').slice(0, 16);
  const staleDir = path.join(path.resolve(lockRoot), `${digest}.lock`);
  fs.mkdirSync(staleDir, { recursive: true });
  fs.writeFileSync(path.join(staleDir, 'owner.json'), JSON.stringify({ pid: 999999, at: Date.now() / 1000 - 60 }), 'utf8');
  check('I6 30s 陈旧锁可抢占', new Lock(lockRoot, staleKey, { timeout: 1, stale: 30 }).acquire().held === true);
  check('I7 skeleton 锁与 memory 锁互不阻塞（键不同）',
    new Lock(lockRoot, lockKey('skeleton', { sandboxId: 'a1b2c3d4', personaId: 'butler' }), { timeout: 0.3 }).acquire().held === true
      && new Lock(lockRoot, lockKey('memory', { sandboxId: 'a1b2c3d4' }), { timeout: 0.3 }).acquire().held === true);
}

// ── J. §4.6 热重载 ───────────────────────────────────────
{
  const p = T('hot.jsonl');
  check('J1 文件缺失 signature=null', fileSignature(p) === null);
  fs.writeFileSync(p, '{"id":"m1"}\n', 'utf8');
  const s1 = fileSignature(p);
  check('J2 签名含 mtimeNs/size/lines（并给 mtime_ns 别名）',
    s1 && typeof s1.mtimeNs === 'number' && typeof s1.mtime_ns === 'number' && s1.lines === 1 && s1.size > 0);
  fs.appendFileSync(p, '{"id":"m2"}\n', 'utf8');
  const s2 = fileSignature(p);
  check('J3 append 后签名变化且行数=2', s2.lines === 2 && (s2.mtimeNs !== s1.mtimeNs || s2.size !== s1.size));
  check('J4 200ms 节流内不重载', shouldReload(s1, s2, { nowMs: 1000, lastCheckMs: 900 }) === false);
  check('J5 过 200ms 且签名变化才重载', shouldReload(s1, s2, { nowMs: 1200, lastCheckMs: 900 }) === true);
  check('J6 签名不变不重载', shouldReload(s1, s1, { nowMs: 1200, lastCheckMs: 900 }) === false);
  check('J7 任一侧为 null 即重载', shouldReload(null, s1, { nowMs: 1200, lastCheckMs: 900 }) === true);
}

// ── K. S4 UCA-01（12+ 例）────────────────────────────────
{
  const cases = [
    ['我喜欢喝红茶', 'ok'],
    ['sk-abcdefghijklmnopqrstuvwx', 'block'],
    ['password=Hunter2hunter', 'block'],
    ['-----BEGIN RSA PRIVATE KEY-----', 'block'],
    ['110101199003077777', 'block'],
    ['6222021234567890123', 'block'],
    ['13812345678', 'warn'],
    ['这份材料是绝密', 'warn'],
    ['订单号 123456789012', 'ok'],
    ['手机 138 1234 5678', 'ok'],
    ['', 'ok'],
    [null, 'ok'],
    ['AKIAIOSFODNN7EXAMPLE', 'block'],
    ['api_key: abcdef123456', 'block'],
  ];
  for (const [text, want] of cases) {
    const got = checkUca01Text(text);
    check(`K UCA01: ${String(text).slice(0, 10)} → ${want}`, got.verdict === want, JSON.stringify(got));
  }
  eq('K15 id_card + bank_card 双命中（规则表顺序）',
    checkUca01Text('110101199003077777').reasons, ['id_card', 'bank_card']);
  check('K16 中文紧邻关键词不误判（Python \\b 的 Unicode 语义）',
    checkUca01Text('备注password=Hunter2hunter').verdict === 'ok');
  check('K17 大小写：secret_marker 不忽略大小写',
    checkUca01Text('Confidential').verdict === 'ok' && checkUca01Text('confidential').reasons.includes('secret_marker'));
  check('K18 不返回命中原文', !JSON.stringify(checkUca01Text('sk-abcdefghijklmnopqrstuvwx')).includes('sk-a'));
}

// ── L. §3.8 记忆行构造 ───────────────────────────────────
{
  const row = makeMemoryRow({
    memId: 'm1', ts: 1800000000.9, memType: 'semantic', origin: 'user_fact',
    subject: '我', relation: '喜欢', obj: '红茶', content: '我喜欢红茶',
    sandbox: 'a1b2c3d4', persona: 'butler', scope: 'global', importance: 0.8,
  });
  check('L1 ★字段 snake_case 齐全',
    ['display_text', 'sandbox_id', 'persona_id', 'scope', 'potency_inputs', 'suppressed_at', 'suppressed_by']
      .every((k) => k in row));
  check('L2 display_text 默认 = subject+relation+object', row.display_text === '我喜欢红茶');
  check('L3 potency_inputs 结构', JSON.stringify(row.potency_inputs) === '{"hits":0,"last_hit_at":null,"importance":0.8}');
  check('L4 ts 取整 / layer=warm / 墓碑字段为 null',
    row.ts === 1800000000 && row.layer === 'warm' && row.suppressed_at === null && row.superseded_by === null);
  throws('L5 非法 scope 抛错', () => makeMemoryRow({ memId: 'x', ts: 1, memType: 't', origin: 'o', sandbox: 's', persona: 'p', scope: 'bad' }), /bad scope/);
  check('L6 display 显式传入优先', makeMemoryRow({ memId: 'x', ts: 1, memType: 't', origin: 'o', sandbox: 's', persona: 'p', scope: 'local', display: '人话' }).display_text === '人话');
}

// ── M. MemoryRuntime（读写路由 / 折叠 / 恢复 / purge / 隔离 / UCA）──
{
  const rtRoot = T('runtime');
  const rt = new MemoryRuntime({ dataRoot: rtRoot, sandboxId: 'a1b2c3d4', personaId: 'butler', now: 1800000000 });
  eq('M1 paths 六键', Object.keys(rt.paths), ['globalMemory', 'localMemory', 'pending', 'skeleton', 'state', 'personaState']);
  const r1 = rt.append([{ id: 'L1', display_text: '我喜欢喝红茶', content: '我喜欢喝红茶', subject: '喜好', relation: '喝', object: '红茶' }]);
  check('M2 scope 缺省 local + 落到 local_memory.jsonl',
    r1.written === 1 && r1.routed.local === 1 && fs.existsSync(rt.paths.localMemory));
  const r2 = rt.append([{ id: 'G1', scope: 'global', display_text: '我是鲸鲸', sandbox_id: 'ffffffff', persona_id: 'other' }]);
  const gRows = readJsonl(rt.paths.globalMemory);
  check('M3 global 路由 + 强制当前标签 + 补 origin_sandbox_id',
    r2.routed.global === 1 && gRows[0].scope === 'global' && gRows[0].sandbox_id === 'a1b2c3d4'
      && gRows[0].persona_id === 'butler' && gRows[0].origin_sandbox_id === 'a1b2c3d4');
  const r3 = rt.append([{ id: 'P1', scope: 'pending', display_text: '待定' }]);
  check('M4 pending 路由到 global_pending.jsonl', r3.routed.pending === 1 && readJsonl(rt.paths.pending)[0].id === 'P1');
  const all = rt.readAll();
  check('M5 readAll 行内 scope 由文件位置派生',
    all.local.length === 1 && all.local[0].scope === 'local' && all.global.length === 1 && all.global[0].scope === 'global');
  check('M6 pending 不进 merge', mergeLayers([...all.local, ...all.global, ...all.pending],
    { sandboxId: 'a1b2c3d4', personaId: 'butler' }).dropped.some((d) => d.id === 'P1' && d.reason === 'pending_not_searchable'));
  const f = rt.forget('L1');
  check('M7 forget 追加墓碑事件并进回收站',
    f.ok === true && f.scope === 'local' && rt.readAll().local.length === 0 && rt.readAll().trash.length === 1);
  const rs = rt.restore('L1');
  check('M8 restore 撤销面板删除', rs.ok === true && rs.scope === 'local' && rt.readAll().local.length === 1);
  const pg = rt.purge('L1');
  const trash = rt.readAll().trash;
  check('M9 purge=redact（回收站显示已删除、标记 redacted）',
    pg.ok === true && trash.length === 1 && trash[0].redacted === true
      && trash[0].display_text === '[已彻底删除]' && trash[0].days_in_trash === 0);
  const blocked = rt.append([{ id: 'BAD', display_text: 'sk-abcdefghijklmnopqrstuvwx' }]);
  check('M10 UCA block 拒写', blocked.written === 0 && blocked.rejected[0].reason === 'uca01_block');
  const warned = rt.append([{ id: 'WARN', display_text: '手机 13812345678' }]);
  check('M11 UCA warn 写入并回传 reasons',
    warned.written === 1 && warned.uca01.length === 1 && warned.uca01[0].reasons.includes('phone_number'));
  const other = new MemoryRuntime({ dataRoot: rtRoot, sandboxId: 'f04ac905', personaId: 'butler', now: 1800000000 });
  check('M12 跨沙盒隔离：另一个沙盒读不到 local', other.readAll().local.length === 0 && other.readAll().global.length === 1);
  const list = rt.list({ query: '鲸鲸' });
  check('M13 list 只给人话视图（id/display_text/date/uses）',
    list.global.length === 1 && list.global[0].id === 'G1' && typeof list.global[0].display_text === 'string'
      && /^\d{4}-\d{2}-\d{2}$/.test(list.global[0].date) && typeof list.global[0].uses === 'number'
      && typeof list.pending_count === 'number');
  throws('M14 构造即校验非法 sandboxId', () => new MemoryRuntime({ dataRoot: rtRoot, sandboxId: '../x', personaId: 'butler' }), /invalid sandbox_id/);
}

// ── 汇总 ─────────────────────────────────────────────────────
const failed = results.filter((r) => !r.cond);
const passed = results.length - failed.length;
try {
  fs.rmSync(TMP, { recursive: true, force: true });
} catch {
  // 临时目录清理失败不影响断言结论
}
if (ASSERT_ONLY) {
  for (const f of failed) console.log(`FAIL  ${f.label}`);
  console.log(`memory-paths-sim: ${passed}/${results.length} passed, ${failed.length} failed`);
}
process.exit(failed.length ? 1 : 0);
