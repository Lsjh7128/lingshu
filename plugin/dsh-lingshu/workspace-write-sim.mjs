// workspace-write-sim.mjs —— 写路径（persona/create）、P2 策略、legacy 迁移的自检
//
// 覆盖（对应 task-4 验收）：
//   1) persona.json：PUT round-trip / 白名单 / 原子写（无 .tmp 残留、旧文件在非法请求后不变）
//   2) workspace create：201 + activated:false、不产 state.json、冲突 409、保留 id、回滚不落盘
//   3) P2 策略：loaded/missing/rejected、deny-only 提升 block、未知规则 fail-closed、判定顺序不变
//   4) 网关接线：evaluate 注入派生策略 / guardTool policyProvider 生效（不改全局策略）
//   5) index.apply 启动加载：workspace_policy_loaded/rejected 审计 + API policy 三态
//   6) legacy 迁移：dry-run / apply / 备份 / 哈希与行数校验 / 冲突拒绝 / profile 层
//   7) HTTP 面：200/201/400/404/409/413/415/403/405，cross-site 拒绝
//
// 用法：node workspace-write-sim.mjs [--assert]

import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { strict as assert } from 'node:assert';
import {
  listWorkspaces,
  readWorkspace,
  writePersona,
  createWorkspace,
  validatePersonaPayload,
  PERSONA_SCHEMA_VERSION,
  LEGACY_ID,
} from './workspace-api.js';
import {
  loadWorkspacePolicy,
  buildEffectivePolicy,
  describeWorkspacePolicy,
} from './workspace-policy.js';
import { evaluate, guardTool, defaultState, POLICY, REGISTRY } from './tool-gateway.js';
import { registerDashboardRoutes } from './dashboard.js';
import { apply } from './index.js';
import { migrate } from './workspace-admin.mjs';

const ASSERT_ONLY = process.argv.includes('--assert');
const results = [];
function check(label, cond, detail = '') {
  results.push({ label, cond: Boolean(cond) });
  if (!ASSERT_ONLY) console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}${detail ? '  — ' + detail : ''}`);
}

// ── 夹具 ────────────────────────────────────────────────
function mkdir(tag) {
  return fs.mkdtempSync(path.join(os.tmpdir(), `lingshu-wswrite-${tag}-`));
}
function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value, null, 2), 'utf8');
}
function writeJsonl(file, lines) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, lines.map((l) => (typeof l === 'string' ? l : JSON.stringify(l))).join('\n') + '\n', 'utf8');
}
function sha256(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}
function personaBody(over = {}) {
  return {
    schema_version: 'persona-0.1',
    persona_id: 'pe_alpha',
    name: '小铃',
    summary: '只读的人格体快照',
    traits: {
      formality: 0.5, warmth: 0.8, proactiveness: 0.6, humor: 0.2, boundary_firmness: 0.9,
    },
    confirmed_by_user: true,
    confirmed_at: '2026-10-28T00:00:00.000Z',
    ...over,
  };
}
function opts(base, extra = {}) {
  return {
    base, profileTag: '', pathProfileTag: '', currentId: '', rejected: null,
    coldTurns: 3, warmTurns: 20, policyDir: '', audit: null,
    ...extra,
  };
}
function treeSig(root) {
  if (!fs.existsSync(root)) return '';
  const out = [];
  const walk = (dir, rel) => {
    for (const ent of fs.readdirSync(dir, { withFileTypes: true }).sort((a, b) => a.name.localeCompare(b.name))) {
      const p = path.join(dir, ent.name);
      const st = fs.lstatSync(p);
      out.push(`${rel}/${ent.name}|${ent.isDirectory() ? 'd' : 'f'}|${st.size}|${st.mtimeMs}`);
      if (ent.isDirectory()) walk(p, `${rel}/${ent.name}`);
    }
  };
  walk(root, '');
  return out.join('\n');
}
function tmpLeftovers(dir) {
  try {
    return fs.readdirSync(dir).filter((n) => n.includes('.tmp-'));
  } catch {
    return [];
  }
}

// ── mock webServer（支持请求体；匹配语义与宿主一致：exact 优先 + 最长前缀）──
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
  const call = async (method, url, { headers = {}, body } = {}) => {
    const pathname = new URL(url, 'http://mock.local').pathname;
    const route = match(pathname);
    if (!route) return { status: 404, headers: {}, body: '' };
    const listeners = new Map();
    const req = {
      method,
      url,
      headers,
      on(type, cb) {
        if (!listeners.has(type)) listeners.set(type, []);
        listeners.get(type).push(cb);
        return req;
      },
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
    const emit = (type, arg) => {
      for (const cb of listeners.get(type) || []) {
        try { cb(arg); } catch { /* 忽略 */ }
      }
    };
    const pending = route.handler(req, res);
    const payload = body === undefined ? null : (Buffer.isBuffer(body) ? body : Buffer.from(String(body)));
    queueMicrotask(() => {
      if (payload) emit('data', payload);
      emit('end');
    });
    await pending;
    return { status: res.statusCode, headers: res.headers, body: res.body };
  };
  return { register, match, call, routes: () => ({ exact: [...exact.keys()], prefixes: [...prefixes.keys()] }) };
}

// ── 1. persona 校验（纯函数）────────────────────────────
{
  const ok = validatePersonaPayload(personaBody());
  check('persona 校验：合法 payload 通过且 confirmed_at 归一为 ISO', ok.ok === true
    && ok.value.confirmed_at === '2026-10-28T00:00:00.000Z'
    && ok.value.confirmed_by_user === true);
  check('persona 校验：未知字段被拒绝', validatePersonaPayload(personaBody({ prompt: 'x' })).error === 'unknown_field');
  check('persona 校验：未知 trait 被拒绝',
    validatePersonaPayload(personaBody({ traits: { formality: 0.5, evil: 0.1 } })).error === 'unknown_trait');
  const alias = validatePersonaPayload(personaBody({ traits: { boundaryFirmness: 0.9 } }));
  check('persona 校验：camelCase 兼容别名归一为 boundary_firmness',
    alias.ok === true && alias.value.traits.boundary_firmness === 0.9
    && alias.value.traits.boundaryFirmness === undefined);
  check('persona 校验：同一语义键两种写法 → 拒绝',
    validatePersonaPayload(personaBody({ traits: { boundaryFirmness: 0.9, boundary_firmness: 0.9 } }))
      .error === 'duplicate_trait');
  check('persona 校验：trait 越界被拒绝',
    validatePersonaPayload(personaBody({ traits: { formality: 1.5 } })).error === 'bad_trait_value');
  check('persona 校验：非用户确认被拒绝',
    validatePersonaPayload(personaBody({ confirmed_by_user: false })).error === 'not_confirmed');
  check('persona 校验：坏 schema_version 被拒绝',
    validatePersonaPayload(personaBody({ schema_version: 'persona-9' })).error === 'bad_schema_version');
  check('persona 校验：name 超长被拒绝',
    validatePersonaPayload(personaBody({ name: 'x'.repeat(121) })).error === 'name_too_long');
  check('persona 校验：summary 超长被拒绝',
    validatePersonaPayload(personaBody({ summary: 'x'.repeat(601) })).error === 'summary_too_long');
  check('persona 校验：坏 confirmed_at 被拒绝',
    validatePersonaPayload(personaBody({ confirmed_at: 'not-a-date' })).error === 'bad_confirmed_at');
}

// ── 2. persona 写 + HTTP 面 ─────────────────────────────
const base = mkdir('api');
writeJson(path.join(base, 'workspace_alpha', 'state.json'), { turns: 2 });
writeJson(path.join(base, 'workspace_alpha', 'persona.json'), {
  name: '旧人格体', summary: '旧摘要', traits: { formality: 0.1 }, extra: 'SECRET',
});
const policyDir = mkdir('policy-empty');
const audits = [];
const writeOpts = opts(base, {
  currentId: 'alpha',
  policyDir,
  audit: (kind, data) => audits.push({ kind, data }),
});
{
  const before = treeSig(base);
  // 无效请求不得改动旧文件
  const invalid = writePersona(writeOpts, 'alpha', personaBody({ traits: { formality: 2 } }));
  check('persona 写：非法 payload 400 且不落盘', invalid.ok === false && invalid.status === 400
    && treeSig(base) === before);

  const res = writePersona(writeOpts, 'alpha', personaBody());
  check('persona 写：200 + 返回白名单 persona', res.ok === true && res.status === 200
    && res.persona?.name === '小铃' && res.persona?.traits?.warmth === 0.8);
  const file = path.join(base, 'workspace_alpha', 'persona.json');
  const stored = JSON.parse(fs.readFileSync(file, 'utf8'));
  check('persona 写：磁盘内容 = 白名单 schema（无旧文件残余字段）',
    stored.schema_version === PERSONA_SCHEMA_VERSION && stored.name === '小铃'
    && stored.confirmed_by_user === true && stored.confirmed_at === '2026-10-28T00:00:00.000Z'
    && stored.extra === undefined && stored.prompt === undefined);
  check('persona 写：原子写无 .tmp 残留', tmpLeftovers(path.join(base, 'workspace_alpha')).length === 0);
  const ev = audits.find((a) => a.kind === 'persona_write');
  check('persona 写：审计 persona_write 含 sha256/bytes/prev_exists 且无正文',
    Boolean(ev) && /^[0-9a-f]{64}$/.test(ev.data.sha256) && ev.data.bytes > 0
    && ev.data.prev_exists === true && ev.data.name_len === 2 && ev.data.summary === undefined);
  check('persona 写：workspace 摘要同步 persona', res.workspace?.persona?.name === '小铃');
}
{
  // 二次覆盖：prev_exists 为 true，旧文件可被替换
  const res = writePersona(writeOpts, 'alpha', personaBody({ name: '小铃二号' }));
  check('persona 写：覆盖旧文件成功', res.ok === true && res.persona.name === '小铃二号');
  const ev = audits.filter((a) => a.kind === 'persona_write').pop();
  check('persona 写：覆盖时 prev_exists=true', ev.data.prev_exists === true);
}
{
  const res = writePersona(writeOpts, 'nosuch', personaBody());
  check('persona 写：不存在的工作区 → 404', res.ok === false && res.status === 404
    && res.error === 'workspace_not_found');
  const bad = writePersona(writeOpts, '../evil', personaBody());
  check('persona 写：非法 id → 400', bad.ok === false && bad.status === 400);
  const legacy = writePersona(writeOpts, LEGACY_ID, personaBody());
  check('persona 写：无数据的 legacy 且非当前布局 → 404', legacy.ok === false && legacy.status === 404);
}

// HTTP：persona PUT / create POST
{
  const server = makeWebServer();
  const providers = {
    getStatus: () => ({ ok: true }),
    getWorkspaces: () => listWorkspaces(writeOpts),
    getWorkspace: (id) => readWorkspace(writeOpts, id),
    writePersona: (id, body) => writePersona(writeOpts, id, body),
    createWorkspace: (body) => createWorkspace(writeOpts, body),
  };
  const dispose = registerDashboardRoutes({ webServer: server }, providers);
  const jsonHeaders = { 'content-type': 'application/json' };

  const put = await server.call('PUT', '/dsh-lingshu/api/workspaces/alpha/persona',
    { headers: jsonHeaders, body: JSON.stringify(personaBody({ name: 'HTTP 小铃' })) });
  check('HTTP：PUT persona 200', put.status === 200 && JSON.parse(put.body).ok === true);
  check('HTTP：PUT persona 响应含 persona + workspace',
    JSON.parse(put.body).persona?.name === 'HTTP 小铃'
    && JSON.parse(put.body).workspace?.persona?.name === 'HTTP 小铃');

  const badType = await server.call('PUT', '/dsh-lingshu/api/workspaces/alpha/persona',
    { headers: { 'content-type': 'text/plain' }, body: 'x' });
  check('HTTP：persona 非 JSON content-type → 415', badType.status === 415);

  const empty = await server.call('PUT', '/dsh-lingshu/api/workspaces/alpha/persona',
    { headers: jsonHeaders });
  check('HTTP：persona 空 body → 400', empty.status === 400);

  const badJson = await server.call('PUT', '/dsh-lingshu/api/workspaces/alpha/persona',
    { headers: jsonHeaders, body: '{not json' });
  check('HTTP：persona 坏 JSON → 400', badJson.status === 400);

  const tooBig = await server.call('PUT', '/dsh-lingshu/api/workspaces/alpha/persona',
    { headers: jsonHeaders, body: JSON.stringify({ pad: 'x'.repeat(70 * 1024) }) });
  check('HTTP：persona 超 64KB → 413', tooBig.status === 413
    && JSON.parse(tooBig.body).error === 'payload_too_large');

  const cross = await server.call('PUT', '/dsh-lingshu/api/workspaces/alpha/persona',
    { headers: { ...jsonHeaders, 'sec-fetch-site': 'cross-site' }, body: JSON.stringify(personaBody()) });
  check('HTTP：cross-site → 403', cross.status === 403);

  const getPersona = await server.call('GET', '/dsh-lingshu/api/workspaces/alpha/persona');
  check('HTTP：GET persona 路径 → 405（只允许 PUT）', getPersona.status === 405
    && String(getPersona.headers.allow).includes('PUT'));

  const delPersona = await server.call('DELETE', '/dsh-lingshu/api/workspaces/alpha/persona');
  check('HTTP：DELETE persona → 405', delPersona.status === 405);

  // create
  const createBody = { workspaceId: 'news', persona: personaBody({ persona_id: 'pe_news', name: '新闻人格体' }) };
  const created = await server.call('POST', '/dsh-lingshu/api/workspaces',
    { headers: jsonHeaders, body: JSON.stringify(createBody) });
  const createdBody = JSON.parse(created.body);
  check('HTTP：POST create → 201 + activated:false', created.status === 201
    && createdBody.ok === true && createdBody.activated === false);
  check('HTTP：create 返回 workspace 摘要与 persona', createdBody.workspace?.workspaceId === 'news'
    && createdBody.workspace?.persona?.name === '新闻人格体');
  check('HTTP：create 不产 state.json、不 activate',
    !fs.existsSync(path.join(base, 'workspace_news', 'state.json'))
    && fs.existsSync(path.join(base, 'workspace_news', 'persona.json')));
  const listAfter = await server.call('GET', '/dsh-lingshu/api/workspaces');
  check('HTTP：create 后列表出现新工作区', JSON.parse(listAfter.body).workspaces
    .some((w) => w.workspaceId === 'news'));

  const conflict = await server.call('POST', '/dsh-lingshu/api/workspaces',
    { headers: jsonHeaders, body: JSON.stringify({ workspaceId: 'news' }) });
  check('HTTP：create 冲突 → 409', conflict.status === 409
    && JSON.parse(conflict.body).error === 'workspace_exists');

  const reserved = await server.call('POST', '/dsh-lingshu/api/workspaces',
    { headers: jsonHeaders, body: JSON.stringify({ workspaceId: 'legacy' }) });
  check('HTTP：create 保留 id legacy → 400', reserved.status === 400
    && JSON.parse(reserved.body).error === 'reserved_workspace_id');

  const badCreate = await server.call('POST', '/dsh-lingshu/api/workspaces',
    { headers: jsonHeaders, body: JSON.stringify({ workspaceId: '..' }) });
  check('HTTP：create 非法 id → 400', badCreate.status === 400);

  const extraField = await server.call('POST', '/dsh-lingshu/api/workspaces',
    { headers: jsonHeaders, body: JSON.stringify({ workspaceId: 'x2', evil: true }) });
  check('HTTP：create 未知字段 → 400', extraField.status === 400
    && JSON.parse(extraField.body).error === 'unknown_field');

  const badPersona = await server.call('POST', '/dsh-lingshu/api/workspaces',
    { headers: jsonHeaders, body: JSON.stringify({ workspaceId: 'x3', persona: personaBody({ traits: { formality: -1 } }) }) });
  check('HTTP：create 非法 persona → 400 且不建目录', badPersona.status === 400
    && JSON.parse(badPersona.body).error === 'invalid_persona'
    && !fs.existsSync(path.join(base, 'workspace_x3')));

  const plainCreate = await server.call('POST', '/dsh-lingshu/api/workspaces',
    { headers: jsonHeaders, body: JSON.stringify({ workspaceId: 'plain' }) });
  check('HTTP：create 无 persona → 201 且目录存在、无 state/persona',
    plainCreate.status === 201
    && fs.existsSync(path.join(base, 'workspace_plain'))
    && !fs.existsSync(path.join(base, 'workspace_plain', 'state.json'))
    && !fs.existsSync(path.join(base, 'workspace_plain', 'persona.json')));

  const detailPlain = await server.call('GET', '/dsh-lingshu/api/workspaces/plain');
  check('HTTP：新建空工作区 detail 200（0 计数）', detailPlain.status === 200
    && JSON.parse(detailPlain.body).workspace.turns === 0);

  const postBadType = await server.call('POST', '/dsh-lingshu/api/workspaces',
    { headers: { 'content-type': 'text/plain' }, body: '{}' });
  check('HTTP：create 非 JSON content-type → 415', postBadType.status === 415);

  dispose();
}
{
  // profileTag 下创建：目录必须带 profiles/<tag> 层
  const baseTag = mkdir('create-tag');
  const tagOpts = opts(baseTag, { profileTag: 'lab', pathProfileTag: 'lab' });
  const res = createWorkspace(tagOpts, { workspaceId: 'labws' });
  check('create：带 profileTag 时建 workspace_<id>/profiles/<tag>',
    res.ok === true && res.status === 201
    && res.workspace.relativeDataDir === 'workspace_labws/profiles/lab'
    && fs.existsSync(path.join(baseTag, 'workspace_labws', 'profiles', 'lab')));
}

// ── 3. P2 策略：加载 / 拒绝 / deny 生效 ───────────────────
const policyBase = mkdir('policy');
writeJson(path.join(policyBase, 'code.json'), {
  schema_version: 'workspace-policy-0.1',
  workspace: 'code',
  ruleset: 't-uca',
  deny: ['T-UCA-10'],
});
writeJson(path.join(policyBase, 'empty.json'), {
  schema_version: 'workspace-policy-0.1', workspace: 'empty', deny: [],
});
writeJson(path.join(policyBase, 'unknownrule.json'), {
  schema_version: 'workspace-policy-0.1', workspace: 'unknownrule', deny: ['T-UCA-99'],
});
writeJson(path.join(policyBase, 'wrongws.json'), {
  schema_version: 'workspace-policy-0.1', workspace: 'other', deny: ['T-UCA-10'],
});
writeJson(path.join(policyBase, 'allowkey.json'), {
  schema_version: 'workspace-policy-0.1', workspace: 'allowkey', allow: ['x'], deny: [],
});
fs.writeFileSync(path.join(policyBase, 'badschema.json'), JSON.stringify({
  schema_version: 'workspace-policy-9', workspace: 'badschema', deny: [],
}), 'utf8');
fs.writeFileSync(path.join(policyBase, 'broken.json'), '{oops', 'utf8');
{
  const loaded = loadWorkspacePolicy({ policyDir: policyBase, workspaceId: 'code' });
  check('策略：合法文件 → loaded + deny', loaded.status === 'loaded'
    && JSON.stringify(loaded.deny) === JSON.stringify(['T-UCA-10']));
  check('策略：ruleset 只做展示', describeWorkspacePolicy(loaded).ruleset === 't-uca');

  const eff = buildEffectivePolicy(POLICY, loaded.deny);
  const deniedRule = eff.rules.find((r) => r.id === 'T-UCA-10');
  check('策略：deny 只把目标规则提升为 block', deniedRule.verdict === 'block'
    && deniedRule.workspace_deny === true);
  check('策略：规则数量/顺序不变',
    eff.rules.length === POLICY.rules.length
    && JSON.stringify(eff.evaluation_order) === JSON.stringify(POLICY.evaluation_order));
  const baseVerdicts = Object.fromEntries(POLICY.rules.map((r) => [r.id, r.verdict]));
  const changed = eff.rules.filter((r) => r.verdict !== baseVerdicts[r.id]);
  check('策略：只收紧不提权（变化项只有 block）',
    changed.every((r) => r.verdict === 'block' && baseVerdicts[r.id] !== 'allow'));
  check('策略：policy_version 不被改写', eff.policy_version === POLICY.policy_version);

  // deny 生效：phase=unknown + 写类工具 → 基础 degrade；工作区策略下 block
  const state = {
    ...defaultState(),
    phase: 'unknown',
    approval_channel: true,
    audit_context: { present: true, session_id: 's', turn_id: 't1' },
    tool_registry: REGISTRY,
  };
  const call = { id: 'c1', tool: 'fs_write', args: { path: 'a.txt', content: 'x' } };
  const baseRes = evaluate(call, state);
  const denyRes = evaluate(call, state, eff);
  check('策略生效：基础策略为 degrade', baseRes.verdict === 'degrade');
  check('策略生效：工作区 deny 后为 block', denyRes.verdict === 'block'
    && denyRes.ucas.includes('T-UCA-10') && denyRes.allowed === false);
  check('策略生效：提示文案仍来自原规则（未改写）',
    denyRes.explain.length === baseRes.explain.length);

  // guardTool policyProvider
  const ran = [];
  const spec = {
    name: 'fs_write',
    execute: (args) => { ran.push(args); return 'ran'; },
    policyProvider: async () => eff,
  };
  const wrapped = guardTool(spec, () => state);
  const out = await wrapped.execute({ path: 'a.txt', content: 'x' }, { callId: 'c2' });
  check('策略接线：guardTool policyProvider → 阻断且不执行', out.ok === false
    && out.blocked === true && out.ucas.includes('T-UCA-10') && ran.length === 0);
  const held = await guardTool({ name: 'fs_write', execute: () => 'ran' }, () => state)
    .execute({ path: 'a.txt', content: 'x' }, { callId: 'c3' });
  check('策略接线：无 policyProvider 时保持基础 degrade（held）',
    held.held === true && held.blocked === false);

  // deny 为空 / 未知规则 / 各种非法 → 不应用
  const empty = loadWorkspacePolicy({ policyDir: policyBase, workspaceId: 'empty' });
  check('策略：deny 为空 → loaded 且 effective 原样', empty.status === 'loaded'
    && buildEffectivePolicy(POLICY, empty.deny) === POLICY);
  const unknown = loadWorkspacePolicy({ policyDir: policyBase, workspaceId: 'unknownrule' });
  check('策略：未登记规则 → rejected（fail-closed）', unknown.status === 'rejected'
    && String(unknown.reason).startsWith('unknown-rule-id'));
  check('策略：rejected 不产生 deny', unknown.deny.length === 0
    && evaluate(call, state, buildEffectivePolicy(POLICY, unknown.deny)).verdict === 'degrade');
  const wrong = loadWorkspacePolicy({ policyDir: policyBase, workspaceId: 'wrongws' });
  check('策略：workspace 字段不匹配 → rejected', wrong.status === 'rejected'
    && wrong.reason === 'workspace-mismatch');
  const allow = loadWorkspacePolicy({ policyDir: policyBase, workspaceId: 'allowkey' });
  check('策略：不允许 allow 键 → rejected', allow.status === 'rejected'
    && String(allow.reason).startsWith('unknown-key'));
  const badSchema = loadWorkspacePolicy({ policyDir: policyBase, workspaceId: 'badschema' });
  check('策略：坏 schema_version → rejected', badSchema.status === 'rejected'
    && badSchema.reason === 'bad-schema-version');
  const broken = loadWorkspacePolicy({ policyDir: policyBase, workspaceId: 'broken' });
  check('策略：坏 JSON → rejected', broken.status === 'rejected' && broken.reason === 'bad_json');
  const missing = loadWorkspacePolicy({ policyDir: policyBase, workspaceId: 'missing-ws' });
  check('策略：文件缺失 → missing（不是 rejected）', missing.status === 'missing');

  // API policy 三态
  writeJson(path.join(base, 'workspace_code', 'state.json'), { turns: 1 });
  writeJson(path.join(base, 'workspace_badpol', 'state.json'), { turns: 1 });
  const apiOpts = opts(base, { policyDir: policyBase });
  const detailCode = readWorkspace(apiOpts, 'code');
  check('API：policy.loaded 透出 deny（只读展示）', detailCode.workspace.policy.status === 'loaded'
    && JSON.stringify(detailCode.workspace.policy.deny) === JSON.stringify(['T-UCA-10']));
  fs.writeFileSync(path.join(policyBase, 'badpol.json'), '{oops', 'utf8');
  const detailBad = readWorkspace(apiOpts, 'badpol');
  check('API：policy.rejected 透出 reason', detailBad.workspace.policy.status === 'rejected'
    && detailBad.workspace.policy.reason === 'bad_json');
  const list = listWorkspaces(apiOpts);
  check('API：列表含策略状态且缺失为 missing',
    list.workspaces.find((w) => w.workspaceId === 'alpha').policy.status === 'missing');
  check('API：策略目录不可读也不影响列表', Array.isArray(list.workspaces));
}

// ── 4. index.apply 启动加载 + 写路径接线 ─────────────────
function withEnv(env, fn) {
  const keys = ['DSH_HOME', 'LINGSHU_DATA_DIR', 'LINGSHU_WORKSPACE', 'LINGSHU_WORKSPACE_POLICY_DIR'];
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
function makeHostCtx(webServer) {
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
}
const tick = () => new Promise((resolve) => setTimeout(resolve, 30));

const policyDirApply = mkdir('policy-apply');
writeJson(path.join(policyDirApply, 'code.json'), {
  schema_version: 'workspace-policy-0.1', workspace: 'code', ruleset: 't-uca-1.2', deny: ['T-UCA-10'],
});
{
  const home = mkdir('home');
  const server = makeWebServer();
  const ctx = makeHostCtx(server);
  withEnv({
    DSH_HOME: home,
    LINGSHU_WORKSPACE: 'code',
    LINGSHU_WORKSPACE_POLICY_DIR: policyDirApply,
  }, () => apply(ctx, {}));
  await tick();

  const dataDir = path.join(home, 'plugins', 'dsh-lingshu', 'workspace_code');
  const auditLines = fs.readFileSync(path.join(dataDir, 'audit.jsonl'), 'utf8')
    .split('\n').filter(Boolean).map((l) => JSON.parse(l));
  const loadedAudit = auditLines.find((e) => e.kind === 'workspace_policy_loaded');
  check('接线：启动写 workspace_policy_loaded 审计', Boolean(loadedAudit)
    && loadedAudit.status === 'loaded'
    && JSON.stringify(loadedAudit.deny) === JSON.stringify(['T-UCA-10'])
    && loadedAudit.workspace === 'code');

  const listRes = await server.call('GET', '/dsh-lingshu/api/workspaces');
  const listBody = JSON.parse(listRes.body);
  check('接线：API 返回 policy.loaded 与 capabilities', listRes.status === 200
    && listBody.workspaces[0].policy.status === 'loaded'
    && listBody.workspaces[0].policy.enforced === true
    && listBody.workspaces[0].capabilities.canCreate === true
    && listBody.workspaces[0].capabilities.canWritePersona === true);

  const putRes = await server.call('PUT', '/dsh-lingshu/api/workspaces/code/persona',
    { headers: { 'content-type': 'application/json' }, body: JSON.stringify(personaBody({ name: '接线人格体' })) });
  check('接线：经面板写入 persona.json', putRes.status === 200
    && fs.existsSync(path.join(dataDir, 'persona.json')));
  const createRes = await server.call('POST', '/dsh-lingshu/api/workspaces',
    { headers: { 'content-type': 'application/json' }, body: JSON.stringify({ workspaceId: 'panelws' }) });
  check('接线：经面板创建未激活工作区', createRes.status === 201
    && JSON.parse(createRes.body).activated === false
    && !fs.existsSync(path.join(home, 'plugins', 'dsh-lingshu', 'workspace_panelws', 'state.json')));
  const auditText = fs.readFileSync(path.join(dataDir, 'audit.jsonl'), 'utf8');
  check('接线：写路径审计 persona_write / workspace_create',
    auditText.includes('"persona_write"') && auditText.includes('"workspace_create"'));
}
{
  // 非法策略：apply 必须记 workspace_policy_rejected 且不应用
  const home = mkdir('home-bad');
  const policyDirBad = mkdir('policy-bad');
  fs.writeFileSync(path.join(policyDirBad, 'bad.json'), '{oops', 'utf8');
  const server = makeWebServer();
  const ctx = makeHostCtx(server);
  withEnv({
    DSH_HOME: home,
    LINGSHU_WORKSPACE: 'bad',
    LINGSHU_WORKSPACE_POLICY_DIR: policyDirBad,
  }, () => apply(ctx, {}));
  await tick();
  const dataDir = path.join(home, 'plugins', 'dsh-lingshu', 'workspace_bad');
  const auditLines = fs.readFileSync(path.join(dataDir, 'audit.jsonl'), 'utf8')
    .split('\n').filter(Boolean).map((l) => JSON.parse(l));
  const rejected = auditLines.find((e) => e.kind === 'workspace_policy_rejected');
  check('接线：非法策略写 workspace_policy_rejected（applied:false）',
    Boolean(rejected) && rejected.applied === false && rejected.fallback === 'global_policy');
  const listBody = JSON.parse((await server.call('GET', '/dsh-lingshu/api/workspaces')).body);
  check('接线：API 对非法策略报 rejected', listBody.workspaces[0].policy.status === 'rejected');
}

// ── 5. legacy 迁移 CLI ──────────────────────────────────
function seedLegacy(dir) {
  writeJson(path.join(dir, 'state.json'), { turns: 3, dev: { valence: 0.1 }, frozen: { ok: true } });
  writeJsonl(path.join(dir, 'memory.jsonl'), [
    { id: 'm1', status: 'active', layer: 'warm' },
    { id: 'm2', status: 'active', layer: 'warm' },
  ]);
  writeJsonl(path.join(dir, 'audit.jsonl'), [
    { ts: 1, kind: 'apply' },
    { ts: 2, kind: 'turn_begin', turns: 3 },
  ]);
  writeJson(path.join(dir, 'persona.json'), { name: '旧人格体', summary: 'x', traits: { formality: 0.4 } });
}
{
  const root = mkdir('migrate');
  seedLegacy(root);
  const sourceHashes = Object.fromEntries(
    ['state.json', 'memory.jsonl', 'audit.jsonl', 'persona.json'].map((n) => [n, sha256(path.join(root, n))]),
  );

  const dry = migrate(['migrate', '--from', 'legacy', '--to', 'code', '--dry-run', '--data-dir', root]);
  check('迁移：dry-run 退出 0 且未应用', dry.exitCode === 0 && dry.result.ok === true
    && dry.result.dryRun === true && dry.result.applied === false);
  check('迁移：dry-run 不动文件/不建目标', !fs.existsSync(path.join(root, 'workspace_code'))
    && sha256(path.join(root, 'state.json')) === sourceHashes['state.json']);
  check('迁移：dry-run 输出哈希/行数/计划', dry.result.changes.length === 4
    && dry.result.changes.every((c) => c.action === 'move' && /^[0-9a-f]{64}$/.test(c.sha256))
    && dry.result.changes.find((c) => c.file === 'memory.jsonl').lines === 2);
  check('迁移：dry-run 给出确认参数与回滚说明',
    dry.result.confirmation.to === 'code' && Array.isArray(dry.result.rollback.steps)
    && dry.result.rollback.steps.length >= 3);
  check('迁移：dry-run 不创建备份目录',
    !fs.existsSync(path.join(root, dry.result.backup.relativeDataDir)));

  const applyRes = migrate(['migrate', '--from', 'legacy', '--to', 'code', '--apply', '--data-dir', root]);
  check('迁移：apply 退出 0 且 applied', applyRes.exitCode === 0 && applyRes.result.applied === true);
  check('迁移：apply 输出的备份路径与磁盘一致',
    fs.existsSync(path.join(root, applyRes.result.backup.relativeDataDir, 'manifest.json')));
  check('迁移：文件已移动到 workspace_code/',
    ['state.json', 'memory.jsonl', 'audit.jsonl', 'persona.json']
      .every((n) => fs.existsSync(path.join(root, 'workspace_code', n))));
  check('迁移：源文件已移走（无重复写入）',
    ['state.json', 'memory.jsonl', 'audit.jsonl', 'persona.json']
      .every((n) => !fs.existsSync(path.join(root, n))));
  check('迁移：逐文件 sha256 校验通过',
    applyRes.result.files.length === 4 && applyRes.result.files.every((f) => f.hash_ok === true));
  check('迁移：JSONL 行数校验通过',
    applyRes.result.files.find((f) => f.name === 'memory.jsonl').lines_actual === 2
    && applyRes.result.files.find((f) => f.name === 'audit.jsonl').lines_actual === 2);
  check('迁移：state 可解析且 turns 一致', applyRes.result.state.ok === true
    && applyRes.result.state.turns === 3);
  check('迁移：state 顶层字段数一致', applyRes.result.state.fields === 3);

  const backupRoot = path.join(root, '.migration-backups');
  const backupDirs = fs.readdirSync(backupRoot);
  check('迁移：备份目录含原文件与 manifest', backupDirs.length === 1
    && fs.existsSync(path.join(backupRoot, backupDirs[0], 'manifest.json'))
    && fs.existsSync(path.join(backupRoot, backupDirs[0], 'state.json')));
  check('迁移：备份哈希 = 原文件哈希',
    backupDirs.length === 1
    && ['state.json', 'memory.jsonl', 'audit.jsonl', 'persona.json']
      .every((n) => sha256(path.join(backupRoot, backupDirs[0], n)) === sourceHashes[n]));

  // 迁移后 API 视图：legacy 消失、workspace_code 出现
  const view = listWorkspaces(opts(root, { currentId: 'code' }));
  check('迁移后：legacy 不再出现、workspace_code 可见',
    view.workspaces.length === 1 && view.workspaces[0].workspaceId === 'code'
    && view.workspaces[0].turns === 3 && view.workspaces[0].persona?.name === '旧人格体');

  const again = migrate(['migrate', '--from', 'legacy', '--to', 'code', '--apply', '--data-dir', root]);
  check('迁移：目标已存在 → 退出 2 拒绝', again.exitCode === 2
    && again.result.error === 'precondition_failed'
    && again.result.problems.some((p) => p.code === 'target_exists'));

  const badId = migrate(['migrate', '--from', 'legacy', '--to', '../evil', '--dry-run', '--data-dir', root]);
  check('迁移：非法目标 id → 退出 2', badId.exitCode === 2
    && badId.result.error === 'invalid_target_workspace_id');
  const badFrom = migrate(['migrate', '--from', 'workspace', '--to', 'code', '--dry-run', '--data-dir', root]);
  check('迁移：--from 非 legacy → 退出 2', badFrom.exitCode === 2 && badFrom.result.error === 'from_must_be_legacy');
  const noMode = migrate(['migrate', '--from', 'legacy', '--to', 'code', '--data-dir', root]);
  check('迁移：未指定 dry-run/apply → 退出 2', noMode.exitCode === 2);
  const help = migrate(['--help']);
  check('迁移：--help 输出用法', help.exitCode === 0 && String(help.result.help).includes('migrate'));
}
{
  // profile 层迁移：用 --home（不用 --data-dir 覆盖，否则 profile 层不叠加）
  const home = mkdir('migrate-home');
  const legacyDir = path.join(home, 'plugins', 'dsh-lingshu', 'profiles', 'lab');
  seedLegacy(legacyDir);
  const res = migrate(['migrate', '--from', 'legacy', '--to', 'roleplay', '--apply',
    '--home', home, '--profile-tag', 'lab']);
  check('迁移：profile 层 → workspace_roleplay/profiles/lab',
    res.exitCode === 0 && fs.existsSync(path.join(home, 'plugins', 'dsh-lingshu',
      'workspace_roleplay', 'profiles', 'lab', 'state.json')));
  check('迁移：profile 源目录文件已移走',
    !fs.existsSync(path.join(legacyDir, 'state.json')));
}
{
  const root = mkdir('migrate-empty');
  const res = migrate(['migrate', '--from', 'legacy', '--to', 'code', '--dry-run', '--data-dir', root]);
  check('迁移：源无 state.json → 退出 2', res.exitCode === 2
    && res.result.problems.some((p) => p.code === 'source_state_missing'));
}
{
  // CLI 入口冒烟（子进程）：stdio=ignore 避开管道限制，只断言退出码与文件副作用。
  const cli = path.join(process.cwd(), 'workspace-admin.mjs');
  const root = mkdir('migrate-cli');
  seedLegacy(root);
  const dry = spawnSync(process.execPath,
    [cli, 'migrate', '--from', 'legacy', '--to', 'cliws', '--dry-run', '--data-dir', root],
    { stdio: 'ignore' });
  check('CLI：dry-run 子进程退出 0 且不建目标目录', dry.status === 0
    && !fs.existsSync(path.join(root, 'workspace_cliws')));
  const applied = spawnSync(process.execPath,
    [cli, 'migrate', '--from', 'legacy', '--to', 'cliws', '--apply', '--data-dir', root],
    { stdio: 'ignore' });
  check('CLI：apply 子进程退出 0 且文件移入 workspace_cliws',
    applied.status === 0 && fs.existsSync(path.join(root, 'workspace_cliws', 'state.json')));
  const help = spawnSync(process.execPath, [cli, '--help'], { stdio: 'ignore' });
  check('CLI：--help 退出 0', help.status === 0);
  const usage = spawnSync(process.execPath, [cli, 'migrate', '--bogus'], { stdio: 'ignore' });
  check('CLI：未知参数退出 2', usage.status === 2);
}

// ── 汇总 ────────────────────────────────────────────────
const failed = results.filter((r) => !r.cond);
const passed = results.length - failed.length;
if (ASSERT_ONLY) {
  for (const f of failed) console.log(`FAIL  ${f.label}`);
  console.log(`workspace-write-sim: ${passed}/${results.length} passed, ${failed.length} failed`);
}
assert.equal(failed.length, 0, `workspace-write-sim failures: ${failed.map((f) => f.label).join('; ')}`);
process.exit(failed.length ? 1 : 0);
