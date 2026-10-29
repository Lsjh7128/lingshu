// workspace-sim.mjs —— 工作区隔离（P1 数据层 / P2 策略层）自检，不起宿主、不碰真实 profile。
//
// 覆盖 docs/roadmap-persona-entity.md §5 的**逻辑层**验收：
//   PE-A1  两个工作区的数据目录互不相同（文件层隔离）
//   PE-A2  重载/切换逻辑：id 解析优先级 + 非法 id 不得静默落到别的 workspace
//   PE-A4  规则沙盒：deny-only、只收紧、未登记 id 拒绝
//   PE-A8  非法 {id}（../、绝对路径、盘符、UNC、空字节、超长）拒绝且不产生越界路径
//
// 用法：node workspace-sim.mjs [--assert]
//
// 注意：这些是**逻辑层**断言。PE-A3/A5/A6/A7（切换重载、坏 state、迁移、并发）需要
// index.js 接线 + 宿主事件，本文件**不**声称覆盖；不要把它们勾成"已验收"。

import path from 'node:path';
import { strict as assert } from 'node:assert';
import {
  SCHEMA_VERSION, SELECTION_SCHEMA_VERSION, WORKSPACE_ID_RE,
  resolveWorkspace, dataDir, normalizePolicy, mergeDeny, normalizeSelection,
} from './workspace.js';

const ASSERT_ONLY = process.argv.includes('--assert');
const results = [];

function check(label, cond, detail = '') {
  results.push({ label, cond: Boolean(cond) });
  if (!ASSERT_ONLY) console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}${detail ? '  — ' + detail : ''}`);
}
function throws(label, fn, re) {
  try {
    fn();
    check(label, false, 'expected throw, none');
  } catch (e) {
    check(label, re ? re.test(String(e.message)) : true, String(e.message));
  }
}

// 旧 stateDir 的语义复刻（用于证明"缺省零回归"逐字节一致；实现里已拆成 root+布局）
function legacyStateDir(base, profileTag = '') {
  const tag = String(profileTag || '').trim();
  if (/^[A-Za-z0-9_-]{1,32}$/.test(tag)) return path.join(base, 'profiles', tag);
  return base;
}

const ROOT = path.resolve('C:/tmp/lingshu-data-root');

// ── 1. PE-A2：id 解析优先级与拒绝 ───────────────────────────────
{
  const legacy = resolveWorkspace({ config: {}, env: {} });
  check('默认无配置 -> legacy 布局', legacy.id === '' && legacy.source === 'legacy' && legacy.rejected === null);

  const fromCfg = resolveWorkspace({ config: { workspaceId: 'code' }, env: {} });
  check('config.workspaceId 生效', fromCfg.id === 'code' && fromCfg.source === 'config');

  const fromEnv = resolveWorkspace({ config: { workspaceId: 'code' }, env: { LINGSHU_WORKSPACE: 'roleplay' } });
  check('env 优先于 config', fromEnv.id === 'roleplay' && fromEnv.source === 'env');

  const blankEnv = resolveWorkspace({ config: { workspaceId: 'code' }, env: { LINGSHU_WORKSPACE: '   ' } });
  check('空白 env 视为未设置', blankEnv.id === 'code' && blankEnv.source === 'config');

  const bad = ['../etc', '..\\win', 'C:\\x', '/abs', 'a/b', 'a b', '中文', 'x'.repeat(33), 'a\u0000b', 'name.'];
  for (const raw of bad) {
    const r = resolveWorkspace({ config: { workspaceId: raw }, env: {} });
    check(`非法 id 被拒绝(${JSON.stringify(raw).slice(0, 18)})`, r.id === '' && r.rejected === raw.trim());
  }
  check('id 正则只收 [A-Za-z0-9_-]{1,32}', WORKSPACE_ID_RE.test('a-b_C9') && !WORKSPACE_ID_RE.test('a.b'));
}

// ── 2. PE-A1 / 零回归：目录布局 ────────────────────────────────
{
  for (const tag of ['', 'desktop', 'linger_tag-1']) {
    check(`legacy 布局与旧 stateDir 一致(tag=${tag || '∅'})`,
      dataDir({ base: ROOT, profileTag: tag }) === legacyStateDir(ROOT, tag));
    // 非法 tag：旧行为=忽略该层，不抛异常
    check(`非法 profileTag 忽略该层(tag=${tag || '∅'})`,
      dataDir({ base: ROOT, workspaceId: 'code', profileTag: 'bad tag!' })
        === path.join(ROOT, 'workspace_code'));
  }

  const a = dataDir({ base: ROOT, workspaceId: 'code' });
  const b = dataDir({ base: ROOT, workspaceId: 'roleplay' });
  check('PE-A1: 两个工作区目录不同', a !== b && a === path.join(ROOT, 'workspace_code') && b === path.join(ROOT, 'workspace_roleplay'));
  check('PE-A1: 都落在数据根内', a.startsWith(ROOT + path.sep) && b.startsWith(ROOT + path.sep));
  check('工作区 + profile 两层嵌套',
    dataDir({ base: ROOT, workspaceId: 'code', profileTag: 'lab' }) === path.join(ROOT, 'workspace_code', 'profiles', 'lab'));
  check('LINGSHU_DATA_DIR 语义(由调用方传入 base) 不受影响',
    dataDir({ base: path.resolve('D:/chaos'), workspaceId: 'code' }) === path.join(path.resolve('D:/chaos'), 'workspace_code'));
}

// ── 3. PE-A8：非法 id 不得产生越界路径 ─────────────────────────
{
  throws('dataDir 拒绝非法 workspaceId', () => dataDir({ base: ROOT, workspaceId: '../etc' }), /invalid workspaceId/);
  throws('dataDir 拒绝空字节 id', () => dataDir({ base: ROOT, workspaceId: 'a\u0000b' }), /invalid workspaceId/);
  throws('dataDir 缺 base 直接抛错', () => dataDir({}), /base required/);
  // 合法 id 的反向断言：拼出来的路径不可能是 ".." 段
  const dir = dataDir({ base: ROOT, workspaceId: 'a-b_c' });
  check('PE-A8: 合法 id 路径无越界段', !dir.split(path.sep).includes('..') && dir.startsWith(ROOT + path.sep));
}

// ── 4. PE-A4：规则沙盒 deny-only ──────────────────────────────
const BASE_IDS = ['T-UCA-01', 'T-UCA-02', 'T-UCA-05', 'T-UCA-06', 'T-UCA-15'];
{
  const ok = normalizePolicy({ schema_version: SCHEMA_VERSION, workspace: 'roleplay', ruleset: 't-uca-1.1', deny: ['T-UCA-06'] });
  check('合规策略被接受', ok.ok === true && ok.policy.deny.length === 1 && ok.policy.ruleset === 't-uca-1.1');

  const emptyDeny = normalizePolicy({ workspace: 'roleplay', deny: [] });
  check('deny 为空 = 无操作（不是放开）', emptyDeny.ok === true && emptyDeny.policy.deny.length === 0);

  const cases = [
    ['allow 键被拒绝（P3 未解冻）', { workspace: 'roleplay', allow: ['T-UCA-06'] }, /unknown-key:allow/],
    ['evaluation_order 被拒绝', { workspace: 'roleplay', evaluation_order: ['T-UCA-06'] }, /unknown-key:evaluation_order/],
    ['文案键被拒绝（文案唯一来源 docs/24）', { workspace: 'roleplay', message_zh: 'x' }, /unknown-key:message_zh/],
    ['未知键被拒绝', { workspace: 'roleplay', background: true }, /unknown-key:background/],
    ['非法 workspace 被拒绝', { workspace: '../x', deny: [] }, /bad-workspace-id/],
    ['deny 非数组被拒绝', { workspace: 'roleplay', deny: 'T-UCA-06' }, /deny-not-array/],
    ['非法规则 id 被拒绝', { workspace: 'roleplay', deny: ['DROP TABLE'] }, /bad-deny-id/],
    ['重复规则 id 被拒绝', { workspace: 'roleplay', deny: ['T-UCA-06', 'T-UCA-06'] }, /duplicate-deny-id/],
    ['非对象被拒绝', ['T-UCA-06'], /policy-not-object/],
    ['坏 schema 版本被拒绝', { schema_version: 'v9', workspace: 'roleplay' }, /bad-schema-version/],
  ];
  for (const [label, doc, re] of cases) {
    const r = normalizePolicy(doc);
    check(label, r.ok === false && re.test(r.reason || ''), r.reason || 'accepted?!');
  }

  const merged = mergeDeny({ baseIds: BASE_IDS, policy: ok.policy });
  check('PE-A4: deny 追加成功', merged.ok === true && merged.deny.length === 1 && merged.deny[0] === 'T-UCA-06' && merged.tightened === true);
  check('PE-A4: 判定顺序/成员不被重写', JSON.stringify(merged.effective) === JSON.stringify(BASE_IDS));
  check('PE-A4: baseIds 未被就地修改', BASE_IDS.length === 5 && BASE_IDS[3] === 'T-UCA-06');

  const unknownRule = mergeDeny({ baseIds: BASE_IDS, policy: { deny: ['T-UCA-99'] } });
  check('未登记规则 id fail-closed', unknownRule.ok === false && /unknown-rule-id/.test(unknownRule.reason));

  const noBase = mergeDeny({ baseIds: [], policy: { deny: [] } });
  check('空规则集 fail-closed', noBase.ok === false && noBase.reason === 'no-base-rule-ids');

  const noop = mergeDeny({ baseIds: BASE_IDS, policy: { deny: [] } });
  check('I-R4: deny 为空不改变任何东西', noop.ok === true && noop.tightened === false
    && JSON.stringify(noop.effective) === JSON.stringify(BASE_IDS));
}

// ── 5. 热切换选择：active-workspace.json 解析 + 启动优先级 ──────
{
  // normalizeSelection：纯函数（不读文件）
  check('selection: 无文件 -> provided=false', normalizeSelection(undefined).provided === false);
  check('selection: 非对象 -> invalid',
    normalizeSelection('code').invalid === true && normalizeSelection('code').reason === 'selection-not-object');
  const ok = normalizeSelection({
    schema_version: SELECTION_SCHEMA_VERSION, workspaceId: 'code', profileTag: 'lab',
  }, 'lab');
  check('selection: 合法选择', ok.provided === true && ok.workspaceId === 'code' && !ok.invalid);
  const legacySel = normalizeSelection({ schema_version: SELECTION_SCHEMA_VERSION, workspaceId: null });
  check('selection: workspaceId=null -> 显式 legacy', legacySel.provided === true && legacySel.workspaceId === '');
  check('selection: legacy 伪 id 被拒绝',
    normalizeSelection({ workspaceId: 'legacy' }).reason === 'reserved_workspace_id');
  check('selection: 非法 id 被拒绝',
    normalizeSelection({ workspaceId: '../evil' }).reason === 'invalid_workspace_id');
  check('selection: 坏 schema 被拒绝',
    normalizeSelection({ schema_version: 'active-workspace-9', workspaceId: 'code' }).reason === 'bad-selection-schema');
  check('selection: 跨 profile 不生效',
    normalizeSelection({ workspaceId: 'code', profileTag: 'lab' }, '').reason === 'profile-mismatch');

  // resolveWorkspace 优先级：env > selected > config > legacy
  const envWins = resolveWorkspace({
    config: { workspaceId: 'cfg' }, env: { LINGSHU_WORKSPACE: 'env' }, selected: 'sel',
  });
  check('selection: env 优先于 selected/config', envWins.id === 'env' && envWins.source === 'env');
  const selWins = resolveWorkspace({
    config: { workspaceId: 'cfg' }, env: {}, selected: 'sel',
  });
  check('selection: selected 优先于 config', selWins.id === 'sel' && selWins.source === 'selected');
  const selLegacy = resolveWorkspace({
    config: { workspaceId: 'cfg' }, env: {}, selected: '',
  });
  check('selection: 显式 legacy 选择覆盖 config', selLegacy.id === '' && selLegacy.source === 'selected');
  const badSel = resolveWorkspace({
    config: { workspaceId: 'cfg' }, env: {}, selected: '../evil',
  });
  check('selection: 非法 selected 不落到别的 workspace',
    badSel.id === '' && badSel.rejected === '../evil' && badSel.rejectedSource === 'selected');
  const noSel = resolveWorkspace({ config: { workspaceId: 'cfg' }, env: {}, selected: undefined });
  check('selection: 无选择时 config 生效', noSel.id === 'cfg' && noSel.source === 'config');
  const badEnv = resolveWorkspace({
    config: { workspaceId: 'cfg' }, env: { LINGSHU_WORKSPACE: '../evil' }, selected: 'sel',
  });
  check('selection: 非法 env 优先接管（不静默用 selected）',
    badEnv.id === '' && badEnv.rejectedSource === 'env');
}

// ── 汇总 ─────────────────────────────────────────────────────
const failed = results.filter((r) => !r.cond);
const passed = results.length - failed.length;
if (ASSERT_ONLY) {
  if (failed.length) {
    for (const f of failed) console.log(`FAIL  ${f.label}`);
  }
  console.log(`workspace-sim: ${passed}/${results.length} passed, ${failed.length} failed`);
}
process.exit(failed.length ? 1 : 0);
