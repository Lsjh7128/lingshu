// workspace-api-sim.mjs —— 只读工作区发现 API 的自检（不起宿主、不碰真实 profile）
//
// 覆盖（对应 task-1 的验收项）：
//   1) 空数据根：空列表 + layout=legacy + detail 404
//   2) 两个工作区：计数/统计互不串（memory last-wins、audit blocked、turns/stage/relation/control/emotion）
//   3) legacy 平铺伪工作区（workspaceId='legacy'、relativeDataDir='.'）
//   4) 非法 id / 路径穿越 → 400，且不读出数据根之外的内容
//   5) profileTag 隔离：只列当前 tag 的有效目录；LINGSHU_DATA_DIR 覆盖语义（pathProfileTag=''）
//   6) HTTP 面：GET 200 / 404 / 400、cross-site 403、非 GET 405、既有 /api/status 不回归
//   7) 损坏 JSONL / 损坏 persona.json 容错；persona 字段白名单；不返回绝对路径/记忆原文
//   8) 只读：调用前后整棵数据根的文件大小与 mtime 不变
//
// 用法：node workspace-api-sim.mjs [--assert]（--assert 只打印汇总与失败项，退出码 0/1）

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { strict as assert } from 'node:assert';
import {
  listWorkspaces, readWorkspace, API_SCHEMA_VERSION, LEGACY_ID,
} from './workspace-api.js';
import { registerDashboardRoutes } from './dashboard.js';
import { apply } from './index.js';

const ASSERT_ONLY = process.argv.includes('--assert');
const results = [];
function check(label, cond, detail = '') {
  results.push({ label, cond: Boolean(cond) });
  if (!ASSERT_ONLY) console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}${detail ? '  — ' + detail : ''}`);
}

// ── 夹具工具 ─────────────────────────────────────────────
function mkBase(tag) {
  const dir = path.join(os.tmpdir(),
    `lingshu-wsapi-${tag}-${process.pid}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`);
  fs.mkdirSync(dir, { recursive: true });
  return dir;
}
function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value, null, 2), 'utf8');
}
function writeJsonl(file, lines) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, lines.map((l) => (typeof l === 'string' ? l : JSON.stringify(l))).join('\n') + '\n', 'utf8');
}
function opt(base, extra = {}) {
  return {
    base,
    profileTag: '',
    pathProfileTag: '',
    currentId: '',
    rejected: null,
    coldTurns: 3,
    warmTurns: 20,
    ...extra,
  };
}
function treeSig(root) {
  if (!fs.existsSync(root)) return '';
  const out = [];
  const walk = (dir, rel) => {
    const entries = fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name));
    for (const ent of entries) {
      const p = path.join(dir, ent.name);
      const st = fs.lstatSync(p);
      out.push(`${rel}/${ent.name}|${ent.isDirectory() ? 'd' : 'f'}|${st.size}|${st.mtimeMs}`);
      if (ent.isDirectory()) walk(p, `${rel}/${ent.name}`);
    }
  };
  walk(root, '');
  return out.join('\n');
}
function byId(payload, workspaceId) {
  return payload.workspaces.find((w) => w.workspaceId === workspaceId) || null;
}

// ── 1. 空数据根 ────────────────────────────────────────────
{
  const base = mkBase('empty');
  const payload = listWorkspaces(opt(base));
  check('空目录：ok=true', payload.ok === true);
  check('空目录：workspaces 为空数组', Array.isArray(payload.workspaces) && payload.workspaces.length === 0);
  check('空目录：layout=legacy', payload.layout === 'legacy');
  check('空目录：currentWorkspaceId=null', payload.currentWorkspaceId === null);
  check('空目录：profileTag=null', payload.profileTag === null);
  check('空目录：schemaVersion', payload.schemaVersion === API_SCHEMA_VERSION);
  const miss = readWorkspace(opt(base), 'code');
  check('空目录：detail 合法但不存在 → 404', miss.ok === false && miss.status === 404 && miss.error === 'workspace_not_found');
  const legacy = readWorkspace(opt(base), LEGACY_ID);
  check('空目录：detail legacy 无数据 → 404', legacy.ok === false && legacy.status === 404);
}

// ── 2. 两个工作区的统计互不串 ─────────────────────────────
const baseTwo = mkBase('two');
{
  writeJson(path.join(baseTwo, 'workspace_alpha', 'state.json'), {
    turns: 7,
    dev: { valence: 0.3, arousal: 0.05, dominance: 0.1 },
    emotionActive: true,
    frozen: { ok: true, checkedAt: 111111 },
    relation: { trustLevel: 'high', stage: 'close', unresolvedConflict: true },
    control: { phase: { phase: 'normal', ratio: 0.2 } },
  });
  writeJsonl(path.join(baseTwo, 'workspace_alpha', 'memory.jsonl'), [
    { id: 'm1', status: 'active', layer: 'warm', text: 'SECRET_MEMORY_ALPHA' },
    { id: 'm2', status: 'active', layer: 'warm' },
    { id: 'm3', status: 'active', layer: 'warm' },
    'this line is not json',
    { id: 'm1', status: 'suppressed', layer: 'warm' },   // last-wins：m1 变墓碑
    { id: 'm4', status: 'active', layer: 'cold' },
  ]);
  writeJsonl(path.join(baseTwo, 'workspace_alpha', 'audit.jsonl'), [
    { ts: 1000, kind: 'apply' },
    { ts: 2000, kind: 'pre_step', blocked: true, verdict: 'block' },
    '{{{ broken json',
    { ts: 3000, kind: 'pre_step', blocked: false, verdict: 'allow' },
    { ts: 4000, kind: 'tool_gate', verdict: 'block' },
  ]);

  writeJson(path.join(baseTwo, 'workspace_beta', 'state.json'), {
    turns: 21,
    dev: { valence: -0.2, arousal: 0.2, dominance: 0 },
    frozen: { ok: false, checkedAt: 0 },
    relation: { trustLevel: 'low', stage: 'new' },
    control: { phase: { phase: 'critical', ratio: 0.85 } },
  });
  writeJsonl(path.join(baseTwo, 'workspace_beta', 'memory.jsonl'), [
    { id: 'b1', status: 'active', layer: 'warm' },
    { id: 'b2', status: 'active', layer: 'warm' },
  ]);
  writeJsonl(path.join(baseTwo, 'workspace_beta', 'audit.jsonl'), [
    { ts: 5000, kind: 'tool_gate', blocked: true },
  ]);

  const before = treeSig(baseTwo);
  const payload = listWorkspaces(opt(baseTwo, { currentId: 'alpha' }));
  // 详情/非法 id 同样不得落盘（面板轮询不应改写账本）。
  readWorkspace(opt(baseTwo, { currentId: 'alpha' }), 'alpha');
  readWorkspace(opt(baseTwo, { currentId: 'alpha' }), 'beta');
  readWorkspace(opt(baseTwo, { currentId: 'alpha' }), '../evil');
  const after = treeSig(baseTwo);
  check('只读：列表/详情调用不改动数据根（大小/mtime）', before === after);
  check('两工作区：list ok 且 2 项', payload.ok === true && payload.workspaces.length === 2, String(payload.workspaces.length));
  check('两工作区：currentWorkspaceId/layout', payload.currentWorkspaceId === 'alpha' && payload.layout === 'workspace');

  const alpha = byId(payload, 'alpha');
  const beta = byId(payload, 'beta');
  check('两工作区：alpha 排第一（isCurrent）', payload.workspaces[0].workspaceId === 'alpha' && alpha.isCurrent === true);
  check('两工作区：relativeDataDir 不含绝对路径', alpha.relativeDataDir === 'workspace_alpha' && beta.relativeDataDir === 'workspace_beta');
  check('两工作区：turns 隔离', alpha.turns === 7 && beta.turns === 21);
  check('两工作区：stage 由 turns 推得', alpha.stage === 'warm' && beta.stage === 'regular');
  check('两工作区：frozen 摘要', alpha.frozen.ok === true && alpha.frozen.checkedAt === 111111
    && beta.frozen.ok === false && beta.frozen.checkedAt === null);
  check('两工作区：relation 摘要', alpha.relation.trustLevel === 'high' && alpha.relation.stage === 'close'
    && alpha.relation.unresolvedConflict === true && beta.relation.unresolvedConflict === false);
  check('两工作区：control.phase 摘要', alpha.control.phase === 'normal' && beta.control.phase === 'critical');
  check('两工作区：emotion.label 摘要', alpha.emotion.label === '愉悦' && beta.emotion.label === '不满');
  check('两工作区：memory last-wins 计数（alpha）',
    alpha.memory.total === 4 && alpha.memory.active === 3 && alpha.memory.suppressed === 1 && alpha.memory.warm === 3,
    JSON.stringify(alpha.memory));
  check('两工作区：memory 计数隔离（beta）',
    beta.memory.total === 2 && beta.memory.active === 2 && beta.memory.suppressed === 0 && beta.memory.warm === 2);
  check('两工作区：audit blocked 计数 + lastEventAt（alpha）',
    alpha.audit.blocked === 2 && alpha.audit.lastEventAt === 4000, JSON.stringify(alpha.audit));
  check('两工作区：audit 计数隔离（beta）', beta.audit.blocked === 1 && beta.audit.lastEventAt === 5000);
  check('两工作区：persona 缺失 → null', alpha.persona === null && beta.persona === null);
  check('两工作区：capabilities 声明读/建/写人格体/热切换',
    alpha.capabilities.canRead === true && alpha.capabilities.canSwitch === true
    && alpha.capabilities.canCreate === true && alpha.capabilities.canWritePersona === true,
    JSON.stringify(alpha.capabilities));
  check('两工作区：无策略文件 → policy.status=missing（不是 loaded）',
    alpha.policy.status === 'missing' && Array.isArray(alpha.policy.deny) && alpha.policy.deny.length === 0);
  check('两工作区：损坏 JSONL 行被跳过但仍统计', alpha.memory.total === 4 && alpha.audit.blocked === 2);

  const serialized = JSON.stringify(payload);
  check('脱敏：不返回绝对数据根', !serialized.includes(baseTwo));
  check('脱敏：不返回记忆原文', !serialized.includes('SECRET_MEMORY_ALPHA'));

  const detailAlpha = readWorkspace(opt(baseTwo, { currentId: 'alpha' }), 'alpha');
  const detailBeta = readWorkspace(opt(baseTwo, { currentId: 'alpha' }), 'beta');
  check('detail：alpha 200 且计数正确', detailAlpha.ok === true && detailAlpha.workspace.turns === 7
    && detailAlpha.workspace.memory.total === 4);
  check('detail：beta 不被 alpha 的数据污染', detailBeta.ok === true && detailBeta.workspace.turns === 21
    && detailBeta.workspace.memory.total === 2 && detailBeta.workspace.isCurrent === false);
  check('detail：alpha.isCurrent=true', detailAlpha.workspace.isCurrent === true);
}

// ── 3. legacy 平铺伪工作区 ────────────────────────────────
{
  const base = mkBase('legacy');
  writeJson(path.join(base, 'state.json'), { turns: 4, control: { phase: { phase: 'normal' } } });
  writeJsonl(path.join(base, 'memory.jsonl'), [{ id: 'l1', status: 'active', layer: 'warm' }]);
  writeJsonl(path.join(base, 'audit.jsonl'), [{ ts: 7000, kind: 'apply' }]);

  const payload = listWorkspaces(opt(base));
  check('legacy：伪工作区被列出', payload.workspaces.length === 1 && payload.workspaces[0].workspaceId === LEGACY_ID);
  const legacy = payload.workspaces[0];
  check('legacy：isCurrent=true（currentId 为空）', legacy.isCurrent === true);
  check('legacy：relativeDataDir="."', legacy.relativeDataDir === '.');
  check('legacy：计数来自平铺目录', legacy.turns === 4 && legacy.memory.total === 1 && legacy.audit.lastEventAt === 7000);
  const detail = readWorkspace(opt(base), LEGACY_ID);
  check('legacy：detail 200', detail.ok === true && detail.workspace.workspaceId === LEGACY_ID);
  const serialized = JSON.stringify(payload);
  check('legacy：不返回绝对路径', !serialized.includes(base));
}

// ── 4. 非法 id / 路径穿越 ─────────────────────────────────
{
  const base = mkBase('bad-id');
  const sibling = mkBase('outside');
  writeJson(path.join(sibling, 'state.json'), { turns: 999 });
  const badIds = [
    '../outside', '..\\outside', 'a/b', 'a b', '', '   ', 'x'.repeat(33),
    '中文', 'a\u0000b', 'name.', '.hidden', '..', '.', '%2e%2e', 'C:\\x', '/abs',
    `${sibling}/workspace_x`,
  ];
  let all400 = true;
  const details = [];
  for (const id of badIds) {
    const r = readWorkspace(opt(base), id);
    if (!(r.ok === false && r.status === 400)) {
      all400 = false;
      details.push(`${JSON.stringify(id)} => ${JSON.stringify(r)}`);
    }
  }
  check('非法 id：全部 400（不落到 legacy/别的 workspace）', all400, details.slice(0, 3).join(' | '));
  check('非法 id：不读出数据根之外的内容', !JSON.stringify(readWorkspace(opt(base), `../${path.basename(sibling)}`)).includes('999'));

  const unknown = readWorkspace(opt(base), 'nosuch');
  check('合法但不存在：404', unknown.ok === false && unknown.status === 404 && unknown.error === 'workspace_not_found');
  const legacy = readWorkspace(opt(base), LEGACY_ID);
  check('legacy 无数据：404', legacy.ok === false && legacy.status === 404);
  const pathTraversal = readWorkspace(opt(base), `..${path.sep}..${path.sep}${path.basename(sibling)}`);
  check('路径穿越（分隔符形式）：400', pathTraversal.ok === false && pathTraversal.status === 400);
}

// ── 5. profileTag 隔离 ───────────────────────────────────
{
  const base = mkBase('profile');
  writeJson(path.join(base, 'workspace_alpha', 'profiles', 'lab', 'state.json'), { turns: 5 });
  writeJsonl(path.join(base, 'workspace_alpha', 'profiles', 'lab', 'memory.jsonl'), [{ id: 'a1', status: 'active', layer: 'warm' }]);
  writeJson(path.join(base, 'workspace_alpha', 'profiles', 'desktop', 'state.json'), { turns: 9 });
  writeJson(path.join(base, 'workspace_beta', 'profiles', 'desktop', 'state.json'), { turns: 3 });
  writeJson(path.join(base, 'profiles', 'lab', 'state.json'), { turns: 4 });   // legacy + lab
  writeJson(path.join(base, 'state.json'), { turns: 999 });                    // legacy 根（不属于 lab）

  const lab = listWorkspaces(opt(base, { profileTag: 'lab', pathProfileTag: 'lab', currentId: 'alpha' }));
  const labIds = lab.workspaces.map((w) => w.workspaceId).sort();
  check('profileTag：lab 只列 alpha + legacy', JSON.stringify(labIds) === JSON.stringify(['alpha', 'legacy']), JSON.stringify(labIds));
  check('profileTag：lab 的 alpha 计数来自 profiles/lab',
    byId(lab, 'alpha').turns === 5 && byId(lab, 'alpha').relativeDataDir === 'workspace_alpha/profiles/lab');
  check('profileTag：lab 的 legacy 用 profiles/lab（不是根 999）',
    byId(lab, 'legacy').turns === 4 && byId(lab, 'legacy').relativeDataDir === 'profiles/lab');
  check('profileTag：lab 不出现 desktop-only 的 beta', byId(lab, 'beta') === null);
  check('profileTag：顶层回显 profileTag', lab.profileTag === 'lab');

  const desktop = listWorkspaces(opt(base, { profileTag: 'desktop', pathProfileTag: 'desktop', currentId: 'beta' }));
  check('profileTag：desktop 看到 alpha + beta', desktop.workspaces.length === 2);
  check('profileTag：desktop 的 alpha.turns=9（与 lab 的 5 不同）', byId(desktop, 'alpha').turns === 9);
  check('profileTag：desktop 的 beta 是当前工作区', byId(desktop, 'beta').isCurrent === true);
  check('profileTag：desktop 不出现 legacy（该 tag 无平铺数据）', byId(desktop, 'legacy') === null);

  const detailLab = readWorkspace(opt(base, { profileTag: 'lab', pathProfileTag: 'lab' }), 'alpha');
  const detailDesktop = readWorkspace(opt(base, { profileTag: 'desktop', pathProfileTag: 'desktop' }), 'alpha');
  check('profileTag：detail 同 id 不同 tag 的计数不同', detailLab.workspace.turns === 5 && detailDesktop.workspace.turns === 9);
  const detailBetaLab = readWorkspace(opt(base, { profileTag: 'lab', pathProfileTag: 'lab' }), 'beta');
  check('profileTag：detail 跨 tag → 404', detailBetaLab.ok === false && detailBetaLab.status === 404);

  // LINGSHU_DATA_DIR 覆盖语义：profile 层不叠加（pathProfileTag=''），但 profileTag 仍回显。
  // 该模式下 workspace_alpha/ 与 workspace_beta/ 的根目录都存在（只有 profiles/<tag>/ 子层），
  // 按"目录存在即可见"的规则会以 0 计数出现；legacy 根目录数据是 999。
  const override = listWorkspaces(opt(base, { profileTag: 'lab', pathProfileTag: '', currentId: '' }));
  const overrideLegacy = byId(override, LEGACY_ID);
  check('覆盖语义：pathProfileTag="" 时不叠加 profile 层（legacy 读根目录 999）',
    Boolean(overrideLegacy) && overrideLegacy.turns === 999 && overrideLegacy.relativeDataDir === '.');
  check('覆盖语义：根目录工作区以 0 计数列出（不读取 profiles/<tag> 数据）',
    override.workspaces.length === 3
    && byId(override, 'alpha').turns === 0 && byId(override, 'beta').turns === 0,
    JSON.stringify(override.workspaces.map((w) => [w.workspaceId, w.turns])));
  check('覆盖语义：profileTag 仍然回显', override.profileTag === 'lab');
}

// ── 6. persona.json：存在才返回、字段白名单、坏文件不伪造 ──
{
  const base = mkBase('persona');
  writeJson(path.join(base, 'workspace_persona', 'state.json'), { turns: 1 });
  writeJson(path.join(base, 'workspace_persona', 'persona.json'), {
    name: '小铃',
    summary: '只读的人格体快照',
    traits: { formality: 0.5, warmth: 0.8, bad: 'not-a-number', '__proto__': { x: 1 } },
    extra: 'SECRET_PERSONA_EXTRA',
    snapshotHash: 'DEADBEEF',
  });
  const r = readWorkspace(opt(base), 'persona');
  check('persona：存在时返回 name/summary', r.ok === true && r.workspace.persona.name === '小铃'
    && r.workspace.persona.summary === '只读的人格体快照');
  check('persona：traits 只保留有限数值', JSON.stringify(r.workspace.persona.traits) === JSON.stringify({ formality: 0.5, warmth: 0.8 }));
  const serialized = JSON.stringify(r);
  check('persona：不返回契约外字段/伪造 hash', !serialized.includes('SECRET_PERSONA_EXTRA') && !serialized.includes('DEADBEEF'));

  writeJson(path.join(base, 'workspace_nopersona', 'state.json'), { turns: 2 });
  check('persona：文件不存在 → null', readWorkspace(opt(base), 'nopersona').workspace.persona === null);

  fs.mkdirSync(path.join(base, 'workspace_badpersona'), { recursive: true });
  writeJson(path.join(base, 'workspace_badpersona', 'state.json'), { turns: 3 });
  fs.writeFileSync(path.join(base, 'workspace_badpersona', 'persona.json'), '{ not json', 'utf8');
  const bad = readWorkspace(opt(base), 'badpersona');
  check('persona：损坏文件 → null 且不炸', bad.ok === true && bad.workspace.persona === null);
  check('persona：损坏文件仍标出 persona 文件存在', bad.workspace.files.persona.exists === true);
}

// ── 7. HTTP 面（dashboard 路由 + mock webServer）─────────────
function makeWebServer() {
  const exact = new Map();
  const prefixes = new Map();
  const register = (route) => {
    const table = route.kind === 'exact' ? exact : prefixes;
    if (table.has(route.path)) throw new Error(`duplicate ${route.kind} route ${route.path}`);
    table.set(route.path, route);
    return () => table.delete(route.path);
  };
  const match = (pathname) => {
    const hit = exact.get(pathname);
    if (hit) return hit;
    let best;
    for (const [prefix, route] of prefixes) {
      if (pathname !== prefix && !pathname.startsWith(`${prefix}/`)) continue;
      if (!best || prefix.length > best.prefix.length) best = { prefix, route };
    }
    return best ? best.route : undefined;
  };
  const call = async (method, url, headers = {}) => {
    let pathname;
    try {
      pathname = new URL(url, 'http://mock.local').pathname;
    } catch {
      pathname = url;
    }
    const route = match(pathname);
    if (!route) return { status: 404, headers: {}, body: '' };
    const req = { method, url, headers };
    const res = {
      statusCode: 200,
      headers: {},
      body: '',
      setHeader(key, value) { this.headers[String(key).toLowerCase()] = value; },
      end(chunk) { this.body = Buffer.isBuffer(chunk) ? chunk.toString('utf8') : String(chunk ?? ''); },
    };
    await route.handler(req, res);
    return { status: res.statusCode, headers: res.headers, body: res.body };
  };
  return { register, match, call, routes: () => ({ exact: [...exact.keys()], prefixes: [...prefixes.keys()] }) };
}

{
  const ctx = { webServer: makeWebServer() };
  const providers = {
    getStatus: () => ({ ok: true, source: 'sim', mode: 'active' }),
    getWorkspaces: () => listWorkspaces(opt(baseTwo, { currentId: 'alpha' })),
    getWorkspace: (id) => readWorkspace(opt(baseTwo, { currentId: 'alpha' }), id),
  };
  const dispose = registerDashboardRoutes(ctx, providers);

  const list = await ctx.webServer.call('GET', '/dsh-lingshu/api/workspaces');
  check('HTTP：GET /api/workspaces → 200', list.status === 200);
  check('HTTP：content-type JSON + nosniff',
    String(list.headers['content-type']).startsWith('application/json')
    && list.headers['x-content-type-options'] === 'nosniff');
  const listBody = JSON.parse(list.body);
  check('HTTP：列表 payload 形状', listBody.ok === true && listBody.currentWorkspaceId === 'alpha'
    && listBody.layout === 'workspace' && listBody.workspaces.length === 2);

  const detail = await ctx.webServer.call('GET', '/dsh-lingshu/api/workspaces/alpha');
  const detailBody = JSON.parse(detail.body);
  check('HTTP：GET /api/workspaces/alpha → 200', detail.status === 200 && detailBody.ok === true);
  const contractKeys = ['workspaceId', 'relativeDataDir', 'isCurrent', 'turns', 'stage', 'frozen', 'relation',
    'control', 'emotion', 'memory', 'audit', 'persona', 'capabilities', 'policy'];
  const missing = contractKeys.filter((k) => !(k in detailBody.workspace));
  check('HTTP：单工作区含全部契约字段', missing.length === 0, missing.join(','));
  check('HTTP：capabilities 含 canSwitch/canCreate/canWritePersona',
    detailBody.workspace.capabilities.canCreate === true
    && detailBody.workspace.capabilities.canWritePersona === true
    && detailBody.workspace.capabilities.canSwitch === true);
  check('HTTP：policy 三态字段形状', ['missing', 'loaded', 'rejected'].includes(detailBody.workspace.policy.status)
    && Array.isArray(detailBody.workspace.policy.deny));
  check('HTTP：非当前工作区/无策略 → policy.enforced=false',
    detailBody.workspace.policy.enforced === false);

  const unknown = await ctx.webServer.call('GET', '/dsh-lingshu/api/workspaces/nosuch');
  check('HTTP：不存在 → 404', unknown.status === 404 && JSON.parse(unknown.body).error === 'workspace_not_found');

  // URL 解析器会把裸 `%2e%2e` 归一化成上一级路径（落到 fallback → 404）；
  // 带编码斜杠的 `%2e%2e%2f` 不会被归一化，必须由 API 自己拒绝（400）。
  const normalizedTraversal = await ctx.webServer.call('GET', '/dsh-lingshu/api/workspaces/%2e%2e');
  check('HTTP：URL 归一化的路径穿越 → 404（不落进 API）', normalizedTraversal.status === 404);
  const encodedTraversal = await ctx.webServer.call('GET', '/dsh-lingshu/api/workspaces/%2e%2e%2fevil');
  check('HTTP：编码斜杠路径穿越 → 400', encodedTraversal.status === 400
    && JSON.parse(encodedTraversal.body).error === 'invalid_workspace_id');

  const multiSegment = await ctx.webServer.call('GET', '/dsh-lingshu/api/workspaces/alpha/extra');
  check('HTTP：多段路径 → 404（不猜）', multiSegment.status === 404);

  const trailing = await ctx.webServer.call('GET', '/dsh-lingshu/api/workspaces/');
  check('HTTP：空 id（尾斜杠）→ 404', trailing.status === 404);

  const crossSite = await ctx.webServer.call('GET', '/dsh-lingshu/api/workspaces', { 'sec-fetch-site': 'cross-site' });
  check('HTTP：cross-site → 403', crossSite.status === 403);

  // 写入口在本 sim 里没有 provider（读-only 路由沿用同一注册器）：POST/PUT 应 503，而不是 405 或假成功。
  const post = await ctx.webServer.call('POST', '/dsh-lingshu/api/workspaces',
    { 'content-type': 'application/json' });
  check('HTTP：POST 无 provider → 503（不假成功）', post.status === 503
    && JSON.parse(post.body).error === 'workspace_create_unavailable');
  const putPersona = await ctx.webServer.call('PUT', '/dsh-lingshu/api/workspaces/alpha/persona',
    { 'content-type': 'application/json' });
  check('HTTP：PUT persona 无 provider → 503（不假成功）', putPersona.status === 503
    && JSON.parse(putPersona.body).error === 'persona_write_unavailable');
  const del = await ctx.webServer.call('DELETE', '/dsh-lingshu/api/workspaces');
  check('HTTP：未允许的方法 → 405 + allow 头', del.status === 405
    && String(del.headers.allow || '').includes('GET'));

  const status = await ctx.webServer.call('GET', '/dsh-lingshu/api/status');
  check('HTTP：既有 /api/status 不回归', status.status === 200 && JSON.parse(status.body).ok === true);

  const dash = await ctx.webServer.call('GET', '/dsh-lingshu/dashboard');
  check('HTTP：既有面板路由不回归', dash.status === 200 && String(dash.headers['content-type']).startsWith('text/html'));

  const routes = ctx.webServer.routes();
  check('HTTP：注册了列表 exact 与 detail prefix', routes.exact.includes('/dsh-lingshu/api/workspaces')
    && routes.prefixes.includes('/dsh-lingshu/api/workspaces'));

  dispose();
  const afterDispose = await ctx.webServer.call('GET', '/dsh-lingshu/api/workspaces');
  check('HTTP：dispose 后路由取消', afterDispose.status === 404);
}

// ── 8. 真实接线：apply() → 宿主路由 → API 读到同一份数据 ─────
// 这一段证明 index.js 传给 API 的 base/profileTag/workspaceId 与 stateDir 的落盘口径一致
// （LINGSHU_DATA_DIR 覆盖时不叠 profile 层这一细节也在这里验）。
{
  const withEnv = (env, fn) => {
    const keys = ['DSH_HOME', 'LINGSHU_DATA_DIR', 'LINGSHU_WORKSPACE'];
    const saved = {};
    for (const key of keys) { saved[key] = process.env[key]; delete process.env[key]; }
    try {
      Object.assign(process.env, env);
      return fn();
    } finally {
      for (const key of keys) {
        if (saved[key] === undefined) delete process.env[key]; else process.env[key] = saved[key];
      }
    }
  };
  const makeHostCtx = (webServer) => {
    const ctx = {
      tools: { register: () => {} },
      systemPrompt: { section: () => {} },
      on: () => {},
      get: () => { throw new Error('no inject'); },
      effect: (fn) => { try { return fn(); } catch { /* ignore */ } },
      inject: (deps, fn) => { if (Array.isArray(deps) && deps.includes('webServer')) fn(ctx); },
      webServer,
    };
    return ctx;
  };

  // 8a. LINGSHU_WORKSPACE=alpha → workspace_alpha/ 落盘，API 认这个目录为当前工作区
  const home = mkBase('host-home');
  const server = makeWebServer();
  const ctx = makeHostCtx(server);
  withEnv({ DSH_HOME: home, LINGSHU_WORKSPACE: 'alpha' }, () => apply(ctx, {}));
  const base = path.join(home, 'plugins', 'dsh-lingshu');
  check('接线：apply 落盘 workspace_alpha/audit.jsonl', fs.existsSync(path.join(base, 'workspace_alpha', 'audit.jsonl')));
  check('接线：legacy 平铺目录未写', !fs.existsSync(path.join(base, 'audit.jsonl')));
  const hostList = await server.call('GET', '/dsh-lingshu/api/workspaces');
  const hostBody = JSON.parse(hostList.body);
  check('接线：API 200 + 当前工作区是 alpha', hostList.status === 200
    && hostBody.ok === true && hostBody.currentWorkspaceId === 'alpha' && hostBody.layout === 'workspace');
  check('接线：API 与落盘目录同一份（alpha 由发现的目录而来）',
    hostBody.workspaces.length === 1 && hostBody.workspaces[0].workspaceId === 'alpha'
    && hostBody.workspaces[0].isCurrent === true && hostBody.workspaces[0].relativeDataDir === 'workspace_alpha');
  check('接线：audit 已含 apply 事件（same file）', hostBody.workspaces[0].audit.lastEventAt !== null);
  const hostDetail = JSON.parse((await server.call('GET', '/dsh-lingshu/api/workspaces/alpha')).body);
  check('接线：detail 与列表一致', hostDetail.ok === true && hostDetail.workspace.turns === hostBody.workspaces[0].turns);

  // 8b. config.workspaceId + profileTag='lab' → workspace_<id>/profiles/lab
  const home2 = mkBase('host-home2');
  const server2 = makeWebServer();
  const ctx2 = makeHostCtx(server2);
  withEnv({ DSH_HOME: home2 }, () => apply(ctx2, { workspaceId: 'roleplay', profileTag: 'lab' }));
  const list2 = JSON.parse((await server2.call('GET', '/dsh-lingshu/api/workspaces')).body);
  check('接线：config.workspaceId+profileTag → relativeDataDir 带 profiles/lab',
    list2.ok === true && list2.profileTag === 'lab' && list2.workspaces.length === 1
    && list2.workspaces[0].workspaceId === 'roleplay'
    && list2.workspaces[0].relativeDataDir === 'workspace_roleplay/profiles/lab',
    JSON.stringify(list2.workspaces.map((w) => w.relativeDataDir)));

  // 8c. LINGSHU_DATA_DIR 覆盖：profile 层不叠加（与 stateDir 旧语义一致）
  const home3 = mkBase('host-home3');
  const override = mkBase('host-override');
  const server3 = makeWebServer();
  const ctx3 = makeHostCtx(server3);
  withEnv({ DSH_HOME: home3, LINGSHU_DATA_DIR: override }, () => apply(ctx3, { workspaceId: 'code', profileTag: 'lab' }));
  check('接线：override 下数据落在 <override>/workspace_code',
    fs.existsSync(path.join(override, 'workspace_code', 'state.json'))
    && !fs.existsSync(path.join(override, 'workspace_code', 'profiles')));
  const list3 = JSON.parse((await server3.call('GET', '/dsh-lingshu/api/workspaces')).body);
  check('接线：override 下 API relativeDataDir=workspace_code（不叠 profile）',
    list3.ok === true && list3.profileTag === 'lab' && list3.workspaces.length === 1
    && list3.workspaces[0].relativeDataDir === 'workspace_code',
    JSON.stringify(list3.workspaces.map((w) => w.relativeDataDir)));

  // 8d. 非法 LINGSHU_WORKSPACE → legacy 布局，且 API 暴露 rejectedWorkspaceId
  const home4 = mkBase('host-home4');
  const server4 = makeWebServer();
  const ctx4 = makeHostCtx(server4);
  withEnv({ DSH_HOME: home4, LINGSHU_WORKSPACE: '../evil' }, () => apply(ctx4, {}));
  const base4 = path.join(home4, 'plugins', 'dsh-lingshu');
  check('接线：非法 id 落 legacy 平铺目录', fs.existsSync(path.join(base4, 'audit.jsonl')));
  const list4 = JSON.parse((await server4.call('GET', '/dsh-lingshu/api/workspaces')).body);
  check('接线：API layout=legacy 且 rejectedWorkspaceId 可见',
    list4.ok === true && list4.layout === 'legacy' && list4.currentWorkspaceId === null
    && list4.rejectedWorkspaceId === '../evil');
  check('接线：legacy 伪工作区为当前工作区', list4.workspaces.length === 1
    && list4.workspaces[0].workspaceId === LEGACY_ID && list4.workspaces[0].isCurrent === true);
  const auditText = fs.readFileSync(path.join(base4, 'audit.jsonl'), 'utf8');
  check('接线：非法 id 仍落 workspace_rejected 审计', auditText.includes('workspace_rejected'));
}

// ── 汇总 ────────────────────────────────────────────────
const failed = results.filter((r) => !r.cond);
const passed = results.length - failed.length;
if (ASSERT_ONLY) {
  for (const f of failed) console.log(`FAIL  ${f.label}`);
  console.log(`workspace-api-sim: ${passed}/${results.length} passed, ${failed.length} failed`);
}
assert.equal(failed.length, 0, `workspace-api-sim failures: ${failed.map((f) => f.label).join('; ')}`);
process.exit(failed.length ? 1 : 0);
