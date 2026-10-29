// workspace-switch-sim.mjs —— 工作区热切换（activate API + 运行时重载 + turn 保护 + 持久化）
//
// 覆盖（task-7 验收）：
//   1) 空闲切换：200 {activated:true, previousWorkspaceId, currentWorkspaceId, workspace}
//   2) 当前 already active：200 alreadyActive=true（不重建上下文）
//   3) 非法 id / 保留 legacy / 不存在目录：400 / 400 / 404
//   4) turn 中：409 turn_in_progress（claimed/pre_step 置位；turn/end 清除；TTL 超时兜底）
//   5) state 计数/关系/persona/policy 随切换变化；旧工作区不再被写
//   6) active-workspace.json 持久化（tmp+rename）+ 启动优先级 env > selected > config > legacy
//   7) 路由面料：403 cross-site / 405 非 POST / 503 provider 缺失
//
// 用法：node workspace-switch-sim.mjs [--assert]

import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { strict as assert } from 'node:assert';
import { apply } from './index.js';
import { registerDashboardRoutes } from './dashboard.js';
import { listWorkspaces } from './workspace-api.js';

const ASSERT_ONLY = process.argv.includes('--assert');
const results = [];
function check(label, cond, detail = '') {
  results.push({ label, cond: Boolean(cond) });
  if (!ASSERT_ONLY) console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}${detail ? '  — ' + detail : ''}`);
}

// ── 夹具/工具 ────────────────────────────────────────────
function mkdir(tag) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `lingshu-switch-${tag}-`));
}
function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value, null, 2), 'utf8');
}
function writeJsonl(file, lines) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, lines.map((l) => JSON.stringify(l)).join('\n') + '\n', 'utf8');
}
function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}
function readJson(file) {
  return JSON.parse(fs.readFileSync(file, 'utf8'));
}
function auditLines(dir) {
  const file = path.join(dir, 'audit.jsonl');
  if (!fs.existsSync(file)) return [];
  return fs.readFileSync(file, 'utf8').split('\n').filter(Boolean)
    .map((line) => { try { return JSON.parse(line); } catch { return null; } })
    .filter(Boolean);
}
function withEnv(env, fn) {
  const keys = ['DSH_HOME', 'LINGSHU_DATA_DIR', 'LINGSHU_WORKSPACE',
    'LINGSHU_WORKSPACE_POLICY_DIR', 'LINGSHU_TURN_ACTIVE_TTL_MS'];
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
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 40));
const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms));

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
    const pathname = new URL(url, 'http://mock.local').pathname;
    const route = match(pathname);
    if (!route) return { status: 404, headers: {}, body: '' };
    const req = {
      method,
      url,
      headers,
      on() { return req; },
      resume() {},
      destroy() {},
    };
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
  return { register, match, call };
}

function makeHost() {
  const sections = [];
  const hooks = new Map();
  const server = makeWebServer();
  const ctx = {
    tools: { register: () => {} },
    systemPrompt: { section: (s) => { sections.push(s); } },
    on: (event, fn) => { if (!hooks.has(event)) hooks.set(event, []); hooks.get(event).push(fn); },
    get: () => { throw new Error('no inject'); },
    effect: (fn) => { try { return fn(); } catch { /* ignore */ } },
    inject: (deps, fn) => { if (Array.isArray(deps) && deps.includes('webServer')) fn(ctx); },
    webServer: server,
  };
  return { ctx, hooks, server, sections };
}
async function fire(hooks, event, args = []) {
  let out;
  for (const fn of hooks.get(event) || []) out = await fn(...args);
  return out;
}
async function fireWaterfall(hooks, event, payload) {
  let out;
  for (const fn of hooks.get(event) || []) {
    out = await fn(payload, () => Promise.resolve({ kind: 'enter', messages: [] }));
  }
  return out;
}
const userMsg = (text) => ([{ role: 'user', content: [{ type: 'text', text }] }]);
function sectionText(sections, key) {
  const sec = sections.find((s) => s.name === `lingshu:${key}`);
  return sec ? sec.text() : '';
}

async function jsonCall(server, method, url, headers = {}) {
  const res = await server.call(method, url, headers);
  let body = null;
  try { body = res.body ? JSON.parse(res.body) : null; } catch { body = null; }
  return { ...res, json: body };
}

// ── 初始化两份隔离数据 + 策略 ─────────────────────────────
const home = mkdir('home');
const base = path.join(home, 'plugins', 'dsh-lingshu');
const alphaDir = path.join(base, 'workspace_alpha');
const betaDir = path.join(base, 'workspace_beta');
const policyDir = mkdir('policy');
writeJson(path.join(policyDir, 'alpha.json'), {
  schema_version: 'workspace-policy-0.1', workspace: 'alpha', ruleset: 't-uca', deny: ['T-UCA-10'],
});
fs.writeFileSync(path.join(policyDir, 'beta.json'), '{oops', 'utf8');

writeJson(path.join(alphaDir, 'state.json'), {
  turns: 5,
  relation: { trustLevel: 'high', stage: 'deep', unresolvedConflict: false },
  control: { phase: { phase: 'normal', ratio: 0.2 } },
  frozen: { ok: true, checkedAt: 111 },
});
writeJsonl(path.join(alphaDir, 'memory.jsonl'), [
  { id: 'a1', status: 'active', layer: 'warm' },
  { id: 'a2', status: 'active', layer: 'warm' },
]);
writeJson(path.join(alphaDir, 'persona.json'), {
  name: 'Alpha 人格', summary: 'alpha 风格', traits: { formality: 0.2, warmth: 0.9 },
});
writeJson(path.join(betaDir, 'state.json'), {
  turns: 12,
  relation: { trustLevel: 'low', stage: 'new', unresolvedConflict: true },
  control: { phase: { phase: 'critical', ratio: 0.8 } },
  frozen: { ok: false, checkedAt: 0 },
});
writeJsonl(path.join(betaDir, 'memory.jsonl'), [{ id: 'b1', status: 'active', layer: 'warm' }]);
writeJson(path.join(betaDir, 'persona.json'), {
  name: 'Beta 人格', summary: 'beta 风格', traits: { formality: 0.8, warmth: 0.2 },
});

const host = makeHost();
withEnv({
  DSH_HOME: home,
  LINGSHU_WORKSPACE: 'alpha',
  LINGSHU_WORKSPACE_POLICY_DIR: policyDir,
}, () => apply(host.ctx, {}));
await tick();

// ── 1. 启动态 ────────────────────────────────────────────
{
  const status = await jsonCall(host.server, 'GET', '/dsh-lingshu/api/status');
  check('启动：当前工作区 alpha', status.status === 200
    && status.json.workspaceId === 'alpha' && status.json.layout === 'workspace');
  const list = await jsonCall(host.server, 'GET', '/dsh-lingshu/api/workspaces');
  check('启动：列表含 alpha/beta，current=alpha', list.status === 200
    && list.json.currentWorkspaceId === 'alpha'
    && list.json.workspaces.length === 2);
  check('启动：capabilities.canSwitch=true（热切换已接线）',
    list.json.workspaces.every((w) => w.capabilities.canSwitch === true));
  check('启动：alpha policy=loaded、beta policy=rejected',
    list.json.workspaces.find((w) => w.workspaceId === 'alpha').policy.status === 'loaded'
    && list.json.workspaces.find((w) => w.workspaceId === 'beta').policy.status === 'rejected');
  check('启动：样式段使用 alpha persona', sectionText(host.sections, 'style').includes('Alpha 人格'));
  const audit = auditLines(alphaDir).map((e) => e.kind);
  check('启动：workspace_policy_loaded 审计（alpha）', audit.includes('workspace_policy_loaded'));
}

// ── 2. 路由错误面 ────────────────────────────────────────
{
  const ghost = await jsonCall(host.server, 'POST', '/dsh-lingshu/api/workspaces/ghost/activate');
  check('路由：不存在 → 404 workspace_not_found', ghost.status === 404
    && ghost.json.error === 'workspace_not_found');

  // 裸 `%2e%2e` 会被 URL 解析器归一化掉，测试必须用编码斜杠形式才能到 handler。
  const traversal = await jsonCall(host.server, 'POST', '/dsh-lingshu/api/workspaces/%2e%2e%2fevil/activate');
  check('路由：非法 id（编码斜杠穿越）→ 400', traversal.status === 400
    && traversal.json.error === 'invalid_workspace_id');

  const legacy = await jsonCall(host.server, 'POST', '/dsh-lingshu/api/workspaces/legacy/activate');
  check('路由：legacy 保留 id → 400', legacy.status === 400
    && legacy.json.error === 'reserved_workspace_id');

  const get = await jsonCall(host.server, 'GET', '/dsh-lingshu/api/workspaces/beta/activate');
  check('路由：GET activate → 405（只允许 POST）', get.status === 405
    && String(get.headers.allow).includes('POST'));

  const cross = await jsonCall(host.server, 'POST', '/dsh-lingshu/api/workspaces/beta/activate',
    { 'sec-fetch-site': 'cross-site' });
  check('路由：cross-site → 403', cross.status === 403);

  // 503：provider 缺失
  const bare = makeWebServer();
  registerDashboardRoutes({ webServer: bare }, {
    getStatus: () => ({ ok: true }),
    getWorkspaces: () => ({ ok: true, workspaces: [] }),
  });
  const noProvider = await jsonCall(bare, 'POST', '/dsh-lingshu/api/workspaces/beta/activate');
  check('路由：activate provider 缺失 → 503', noProvider.status === 503
    && noProvider.json.error === 'workspace_activate_unavailable');
}

// ── 3. turn 保护：claimed → 409；turn/end → 200 ───────────
{
  await fire(host.hooks, 'agent/inbox/claimed', [{ turn: 900, message: { role: 'user', content: 'hi' } }]);
  const status = await jsonCall(host.server, 'GET', '/dsh-lingshu/api/status');
  check('turn：claimed 后 turnActive=true', status.json.turnActive === true);

  const blocked = await jsonCall(host.server, 'POST', '/dsh-lingshu/api/workspaces/beta/activate');
  check('turn：turnActive 时切换 → 409 turn_in_progress', blocked.status === 409
    && blocked.json.error === 'turn_in_progress');
  check('turn：409 后仍是 alpha、旧目录未被切换',
    (await jsonCall(host.server, 'GET', '/dsh-lingshu/api/workspaces')).json.currentWorkspaceId === 'alpha');

  await fire(host.hooks, 'session/event', [{}, { type: 'turn/end', data: { turn: 900 } }]);
  const afterEnd = await jsonCall(host.server, 'GET', '/dsh-lingshu/api/status');
  check('turn：turn/end 后 turnActive=false', afterEnd.json.turnActive === false);
}

// ── 4. 空闲切换 alpha → beta ─────────────────────────────
const alphaStateHashBefore = sha256(path.join(alphaDir, 'state.json'));
const alphaAuditHashBefore = sha256(path.join(alphaDir, 'audit.jsonl'));
{
  const res = await jsonCall(host.server, 'POST', '/dsh-lingshu/api/workspaces/beta/activate');
  check('切换：200 {activated:true}', res.status === 200 && res.json.ok === true
    && res.json.activated === true && res.json.alreadyActive === false);
  check('切换：from/to 正确', res.json.previousWorkspaceId === 'alpha'
    && res.json.currentWorkspaceId === 'beta');
  check('切换：响应 workspace 是 beta（turns=12/关系 low/persona Beta）',
    res.json.workspace?.workspaceId === 'beta'
    && res.json.workspace?.turns === 12
    && res.json.workspace?.relation?.trustLevel === 'low'
    && res.json.workspace?.persona?.name === 'Beta 人格');

  const status = await jsonCall(host.server, 'GET', '/dsh-lingshu/api/status');
  check('切换：运行态重载 beta state（turns/relation/policy 源）',
    status.json.workspaceId === 'beta' && status.json.turns === 12
    && status.json.relation.trustLevel === 'low');

  const list = await jsonCall(host.server, 'GET', '/dsh-lingshu/api/workspaces');
  check('切换：列表 current=beta、alpha 不再 current',
    list.json.currentWorkspaceId === 'beta'
    && list.json.workspaces.find((w) => w.workspaceId === 'beta').isCurrent === true
    && list.json.workspaces.find((w) => w.workspaceId === 'alpha').isCurrent === false);

  check('切换：样式段改用 beta persona', sectionText(host.sections, 'style').includes('Beta 人格')
    && !sectionText(host.sections, 'style').includes('Alpha 人格'));

  const selFile = path.join(base, 'active-workspace.json');
  const sel = readJson(selFile);
  check('切换：持久化 active-workspace.json（workspaceId=beta）',
    sel.workspaceId === 'beta' && sel.profileTag === '' && sel.source === 'api');
  check('切换：select 文件无 tmp 残留',
    fs.readdirSync(base).filter((n) => n.includes('.tmp-')).length === 0);

  const betaAudit = auditLines(betaDir);
  const sw = betaAudit.find((e) => e.kind === 'workspace_switch');
  check('切换：审计写进新工作区（from/to/host_hook）',
    Boolean(sw) && sw.from === 'alpha' && sw.to === 'beta'
    && sw.host_hook === 'POST /dsh-lingshu/api/workspaces/:id/activate' && sw.persisted === true);
  check('切换：新工作区策略审计（beta rejected）',
    betaAudit.some((e) => e.kind === 'workspace_policy_rejected' && e.source === 'switch'));
  check('切换：旧工作区 state 未被切换过程改写',
    sha256(path.join(alphaDir, 'state.json')) === alphaStateHashBefore);
}

// ── 5. 切换后旧工作区不再被写 ─────────────────────────────
{
  const betaAuditBefore = auditLines(betaDir).length;
  await fire(host.hooks, 'agent/inbox/claimed', [{ turn: 901, message: { role: 'user', content: '继续' } }]);
  await fireWaterfall(host.hooks, 'agent/pre-step', {
    messages: userMsg('继续'), turn: 901, step: 1,
  });
  await fire(host.hooks, 'session/event', [{}, { type: 'turn/end', data: { turn: 901 } }]);

  const status = await jsonCall(host.server, 'GET', '/dsh-lingshu/api/status');
  check('切换后：新回合落在 beta（turns=13）', status.json.turns === 13);
  check('切换后：旧工作区 state/audit 哈希不变',
    sha256(path.join(alphaDir, 'state.json')) === alphaStateHashBefore
    && sha256(path.join(alphaDir, 'audit.jsonl')) === alphaAuditHashBefore);
  check('切换后：新审计继续写 beta', auditLines(betaDir).length > betaAuditBefore);
}

// ── 6. already active + 切回 alpha ────────────────────────
{
  const same = await jsonCall(host.server, 'POST', '/dsh-lingshu/api/workspaces/beta/activate');
  check('already active：200 + alreadyActive=true', same.status === 200
    && same.json.activated === true && same.json.alreadyActive === true
    && same.json.previousWorkspaceId === 'beta' && same.json.currentWorkspaceId === 'beta');
  check('already active：审计 already_active=true',
    auditLines(betaDir).some((e) => e.kind === 'workspace_switch' && e.already_active === true));

  const back = await jsonCall(host.server, 'POST', '/dsh-lingshu/api/workspaces/alpha/activate');
  check('切回 alpha：200 且 persona/state 恢复',
    back.status === 200 && back.json.currentWorkspaceId === 'alpha'
    && back.json.workspace.turns === 5
    && back.json.workspace.persona.name === 'Alpha 人格');
  check('切回 alpha：样式段恢复 Alpha',
    sectionText(host.sections, 'style').includes('Alpha 人格'));
  check('切回 alpha：selection 持久化为 alpha',
    readJson(path.join(base, 'active-workspace.json')).workspaceId === 'alpha');

  // create（只建目录/无 state）→ activate 要落一份 fresh state，下一条消息才有落点。
  fs.mkdirSync(path.join(base, 'workspace_gamma'), { recursive: true });
  const gamma = await jsonCall(host.server, 'POST', '/dsh-lingshu/api/workspaces/gamma/activate');
  check('切换：激活无 state 的新建工作区（落 fresh state）', gamma.status === 200
    && gamma.json.currentWorkspaceId === 'gamma'
    && gamma.json.workspace.turns === 0
    && fs.existsSync(path.join(base, 'workspace_gamma', 'state.json')));
  const backAlpha = await jsonCall(host.server, 'POST', '/dsh-lingshu/api/workspaces/alpha/activate');
  check('切换：gamma → alpha 仍可切回', backAlpha.status === 200
    && backAlpha.json.currentWorkspaceId === 'alpha');
}

// ── 7. 启动优先级：env > selected > config > legacy ───────
{
  // 7a. selected（无 env）→ beta（上一步切回了 alpha，这里先写 beta 选择）
  writeJson(path.join(base, 'active-workspace.json'), {
    schema_version: 'active-workspace-0.1', workspaceId: 'beta', profileTag: null, source: 'api',
  });
  const h1 = makeHost();
  withEnv({ DSH_HOME: home, LINGSHU_WORKSPACE_POLICY_DIR: policyDir }, () => apply(h1.ctx, {}));
  await tick();
  const s1 = await jsonCall(h1.server, 'GET', '/dsh-lingshu/api/status');
  check('启动优先级：selected 生效（beta）', s1.json.workspaceId === 'beta'
    && s1.json.turns >= 12);

  // 7b. env 覆盖 selected
  const h2 = makeHost();
  withEnv({
    DSH_HOME: home, LINGSHU_WORKSPACE: 'alpha', LINGSHU_WORKSPACE_POLICY_DIR: policyDir,
  }, () => apply(h2.ctx, {}));
  await tick();
  const s2 = await jsonCall(h2.server, 'GET', '/dsh-lingshu/api/status');
  check('启动优先级：env 覆盖 selected（alpha）', s2.json.workspaceId === 'alpha');

  // 7c. 显式 legacy 选择覆盖 config
  writeJson(path.join(base, 'active-workspace.json'), {
    schema_version: 'active-workspace-0.1', workspaceId: null, profileTag: null, source: 'api',
  });
  const h3 = makeHost();
  withEnv({ DSH_HOME: home, LINGSHU_WORKSPACE_POLICY_DIR: policyDir },
    () => apply(h3.ctx, { workspaceId: 'beta' }));
  await tick();
  const s3 = await jsonCall(h3.server, 'GET', '/dsh-lingshu/api/status');
  check('启动优先级：显式 legacy 选择覆盖 config', s3.json.workspaceId === null
    && s3.json.layout === 'legacy');
}

// ── 8. 非法/缺失选择：忽略 + 审计；TTL 超时兜底 ───────────
{
  writeJson(path.join(base, 'active-workspace.json'), {
    schema_version: 'active-workspace-0.1', workspaceId: '../evil',
  });
  const h4 = makeHost();
  withEnv({ DSH_HOME: home, LINGSHU_WORKSPACE_POLICY_DIR: policyDir }, () => apply(h4.ctx, {}));
  await tick();
  const s4 = await jsonCall(h4.server, 'GET', '/dsh-lingshu/api/status');
  check('非法选择：忽略并落 legacy', s4.json.layout === 'legacy');
  check('非法选择：workspace_selection_ignored 审计',
    auditLines(base).some((e) => e.kind === 'workspace_selection_ignored'
      && e.reason === 'invalid_workspace_id' && e.applied === false));

  writeJson(path.join(base, 'active-workspace.json'), {
    schema_version: 'active-workspace-0.1', workspaceId: 'ghost',
  });
  const h5 = makeHost();
  withEnv({ DSH_HOME: home, LINGSHU_WORKSPACE_POLICY_DIR: policyDir }, () => apply(h5.ctx, {}));
  await tick();
  check('缺失目标选择：workspace_missing 审计',
    auditLines(base).some((e) => e.kind === 'workspace_selection_ignored'
      && e.reason === 'workspace_missing' && e.workspaceId === 'ghost'));

  // TTL 超时兜底：claimed 后 turn 事件丢失，超过 TTL 允许切换并留 turn_active_timeout
  const homeTtl = mkdir('ttl');
  writeJson(path.join(homeTtl, 'plugins', 'dsh-lingshu', 'workspace_a', 'state.json'), { turns: 1 });
  writeJson(path.join(homeTtl, 'plugins', 'dsh-lingshu', 'workspace_b', 'state.json'), { turns: 2 });
  const h6 = makeHost();
  withEnv({
    DSH_HOME: homeTtl,
    LINGSHU_WORKSPACE: 'a',
    LINGSHU_WORKSPACE_POLICY_DIR: policyDir,
    LINGSHU_TURN_ACTIVE_TTL_MS: '40',
  }, () => apply(h6.ctx, {}));
  await tick();
  await fire(h6.hooks, 'agent/inbox/claimed', [{ turn: 1, message: { role: 'user', content: 'x' } }]);
  const blockedTtl = await jsonCall(h6.server, 'POST', '/dsh-lingshu/api/workspaces/b/activate');
  check('TTL：未超时仍 409', blockedTtl.status === 409);
  await wait(80);
  const afterTtl = await jsonCall(h6.server, 'POST', '/dsh-lingshu/api/workspaces/b/activate');
  check('TTL：超时后允许切换（200）', afterTtl.status === 200
    && afterTtl.json.currentWorkspaceId === 'b');
  check('TTL：turn_active_timeout 审计', auditLines(path.join(homeTtl, 'plugins', 'dsh-lingshu',
    'workspace_a')).some((e) => e.kind === 'turn_active_timeout'));
}

// ── 9. 只读 API 不回归 ────────────────────────────────────
{
  const list = listWorkspaces({
    base, profileTag: '', pathProfileTag: '', currentId: 'beta',
    coldTurns: 3, warmTurns: 20, policyDir, audit: () => {},
  });
  check('只读 API 不回归：列表/current/policy/capabilities',
    list.ok === true && list.currentWorkspaceId === 'beta'
    && Boolean(list.workspaces.find((w) => w.workspaceId === 'alpha'))
    && Boolean(list.workspaces.find((w) => w.workspaceId === 'beta'))
    && list.workspaces.every((w) => typeof w.policy.status === 'string'));
}

// ── 汇总 ────────────────────────────────────────────────
const failed = results.filter((r) => !r.cond);
const passed = results.length - failed.length;
if (ASSERT_ONLY) {
  for (const f of failed) console.log(`FAIL  ${f.label}`);
  console.log(`workspace-switch-sim: ${passed}/${results.length} passed, ${failed.length} failed`);
}
assert.equal(failed.length, 0, `workspace-switch-sim failures: ${failed.map((f) => f.label).join('; ')}`);
process.exit(failed.length ? 1 : 0);
