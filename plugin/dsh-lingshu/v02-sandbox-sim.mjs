// v02-sandbox-sim.mjs —— G2/G3/G4 端到端：沙盒隔离 + 作用域记忆 + 记忆面板
//
// 为什么要用"最小宿主"而不是直接调 MemoryRuntime：
//   验收要证明的是**插件运行时**（index.js 的 v0.2 接线）行为，不是镜像库自测。
//   这里用假的 ctx 提供 systemPrompt.section / tools.register / on / inject(webServer)，
//   然后驱动真实的 session/event、agent/pre-step 与 dashboard 路由。
//
// 运行：node plugin/dsh-lingshu/v02-sandbox-sim.mjs --assert
// 隔离：只写系统临时目录（lingshu-v02sim-*），结束尽力清理；不碰真实数据根。
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';

import { apply } from './index.js';
import {
  canonicalizePath, sandboxId, writeLayout, readJsonl, foldRows,
} from './memory-paths.js';

const PASS = [];
const FAIL = [];

function check(name, cond, detail = '') {
  if (cond) PASS.push(name);
  else {
    FAIL.push(name);
    console.log(`  ! ${name}${detail ? ': ' + detail : ''}`);
  }
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value, null, 2), 'utf8');
}

const now = () => new Date().toISOString();
const ROOT = path.join(os.tmpdir(), `lingshu-v02sim-${process.pid}-${Date.now().toString(36)}`);
const DATA = path.join(ROOT, 'data');
const PROJ = {
  A: path.join(ROOT, 'proj-a'),
  B: path.join(ROOT, 'proj-b'),
  C: path.join(ROOT, 'proj-c'),
  D: path.join(ROOT, 'proj-d'),
  E: path.join(ROOT, 'proj-e'),
};
const sid = Object.fromEntries(Object.entries(PROJ).map(
  ([k, d]) => [k, sandboxId(canonicalizePath(d))]));

function makeHost() {
  const sections = [];
  const hooks = {};
  const routes = new Map();
  const webServer = {
    register(spec) {
      routes.set(spec.path, spec);
      return () => {};
    },
  };
  const ctx = {
    systemPrompt: { section(sec) { sections.push(sec); } },
    tools: { register() { return () => {}; } },
    on(evt, fn) { hooks[evt] = fn; },
    inject(deps, fn) {
      // 宿主注入 webServer 服务；这里同步给一个最小假服务，路由表可被测试直接调用。
      fn({ webServer, effect(cb) { try { cb(); } catch { /* noop */ } } });
    },
  };
  return { ctx, sections, hooks, routes };
}

function sectionText(h, name) {
  const sec = h.sections.find((s) => s.name === name);
  if (!sec) return null;
  try { return sec.text(); } catch (err) { return `__error__:${err?.message}`; }
}

async function callRoute(h, routePath, req) {
  const spec = h.routes.get(routePath);
  if (!spec) throw new Error(`route missing: ${routePath}`);
  const res = {
    statusCode: 0,
    headers: {},
    body: null,
    setHeader(k, v) { this.headers[k] = v; },
    end(body) { this.body = body; },
  };
  await spec.handler(req, res);
  return res;
}

function jsonRequest(method, url, body) {
  const handlers = {};
  const req = {
    method,
    url,
    headers: { 'content-type': 'application/json' },
    on(evt, fn) { handlers[evt] = fn; return req; },
    resume() {},
  };
  setTimeout(() => {
    try {
      if (handlers.data) handlers.data(Buffer.from(JSON.stringify(body), 'utf8'));
      if (handlers.end) handlers.end();
    } catch { /* 读取失败由 provider 处理 */ }
  }, 0);
  return req;
}

async function sendUser(h, text, turn) {
  await h.hooks['session/event']({}, {
    type: 'user/message',
    data: { message: { role: 'user', content: text } },
  });
  await h.hooks['session/event']({}, { type: 'turn/end', data: { turn } });
}

let turnSeq = 0;
async function ask(h, text) {
  turnSeq += 1;
  const next = () => ({ kind: 'enter', messages: [] });
  await h.hooks['agent/pre-step'](
    { messages: [{ role: 'user', content: text }], turn: turnSeq, step: 1 }, next);
  // pre-step 会把本轮置为 active turn；真实宿主随后会发 turn/end，这里显式补一条，
  // 否则后面的 /switch 会按契约返回 409 turn_in_progress。
  await h.hooks['session/event']({}, { type: 'turn/end', data: { turn: turnSeq } });
  return sectionText(h, 'lingshu:fact') || '';
}

async function activate(h, sid) {
  const res = await callRoute(h, '/dsh-lingshu/api/workspaces', {
    method: 'POST',
    url: `/dsh-lingshu/api/workspaces/${sid}/activate`,
    headers: {},
    resume() {},
    on() {},
  });
  try { return JSON.parse(res.body); } catch { return { ok: false, raw: res.body }; }
}

async function panelGet(h, q = '') {
  const res = await callRoute(h, '/dsh-lingshu/api/memory', {
    method: 'GET',
    url: `/dsh-lingshu/api/memory${q ? '?q=' + encodeURIComponent(q) : ''}`,
    headers: {},
  });
  try { return JSON.parse(res.body); } catch { return { ok: false, raw: res.body }; }
}

async function panelPost(h, sub, body) {
  const res = await callRoute(h, `/dsh-lingshu/api/memory${sub}`, jsonRequest('POST', `/dsh-lingshu/api/memory${sub}`, body));
  try { return JSON.parse(res.body); } catch { return { ok: false, raw: res.body }; }
}

function sandboxPaths(sid, pid) {
  return {
    local: path.join(DATA, 'sandboxes', sid, pid, 'local_memory.jsonl'),
    global: path.join(DATA, 'personas', pid, 'global_memory.jsonl'),
    pending: path.join(DATA, 'personas', pid, 'global_pending.jsonl'),
  };
}

function rows(file) {
  return foldRows(readJsonl(file)).active;
}

// ── 夹具：数据根（layout-0.2 + mapping + index + persona）──────────────
try {
  fs.mkdirSync(DATA, { recursive: true });
  for (const d of Object.values(PROJ)) fs.mkdirSync(d, { recursive: true });

  writeLayout(DATA, { migratedFrom: ['sim'], now: Math.floor(Date.now() / 1000) });
  writeJson(path.join(DATA, 'sandboxes', 'index.json'), {
    schema_version: 'sandbox-index-0.1',
    sandboxes: Object.fromEntries(Object.entries(PROJ).map(([k, d]) => [sid[k], {
      canonical_path: canonicalizePath(d), label: k,
      created_at: Math.floor(Date.now() / 1000), last_active_at: Math.floor(Date.now() / 1000),
    }])),
  });
  for (const [k, pid] of [['A', 'butler'], ['B', 'architect'], ['C', 'butler'], ['D', 'butler']]) {
    writeJson(path.join(DATA, 'sandboxes', sid[k], 'sandbox.json'), {
      schema_version: 'sandbox-0.1', sandbox_id: sid[k],
      canonical_path: canonicalizePath(PROJ[k]), label: k,
      created_at: Math.floor(Date.now() / 1000), profile_tag: null,
    });
    writeJson(path.join(DATA, 'personas', pid, 'persona.json'), {
      schema_version: 'persona-0.1', persona_id: pid,
      name: pid === 'butler' ? 'Butler' : 'Architect',
      summary: pid === 'butler' ? '沉稳管家' : '冷静架构师',
      traits: { formality: 0.8, warmth: 0.5, proactiveness: 0.4, humor: 0.2, boundary_firmness: 0.7 },
      confirmed_by_user: true, confirmed_at: now(),
    });
  }
  const bindings = [['A', 'butler'], ['B', 'architect'], ['C', 'butler'], ['D', 'butler']]
    .map(([k, pid]) => `  - canonical_path: "${canonicalizePath(PROJ[k])}"\n    sandbox_id: "${sid[k]}"\n    default_persona: "${pid}"\n    label: "${k}"`)
    .join('\n');
  fs.writeFileSync(path.join(DATA, 'sandbox_mapping.yaml'),
    `schema_version: mapping-0.1\nbindings:\n${bindings}\n`, 'utf8');

  process.env.LINGSHU_DATA_DIR = DATA;
  delete process.env.LINGSHU_WORKSPACE;
  delete process.env.LINGSHU_WORKSPACE_POLICY_DIR;
} catch (err) {
  console.log('  ! 夹具创建失败: ' + String(err?.message || err));
  console.log('PASS: 0 ｜ FAIL: 1 ｜ 断言总数: 1');
  process.exit(1);
}

const CFG = { mode: 'active', coldTurns: 0, warmTurns: 0 };   // 跳过 cold/warm 降级，直接看召回

// ══ G2：DirA 启动 → 写记忆 → /switch 到 DirB → 读不到 DirA → 自动加载 DirB 人格 ══
const hA = makeHost();
apply(hA.ctx, { ...CFG, workspaceRoot: PROJ.A });
const skel = hA.sections.find((s) => s.name === 'lingshu:skeleton');
check('G2：v0.2 注册 8 段（含 skeleton@105）', hA.sections.length === 8, String(hA.sections.length));
check('G2：skeleton 段为空串（空段落不改变拼装）', skel && skel.order === 105 && skel.text() === '');
check('G2：插件识别 sandbox = sha256(canonicalPath)[:8]',
  readJsonl(path.join(DATA, 'audit', 'audit.jsonl')).some((r) => r.kind === 'apply' && r.sandbox_id === sid.A),
  JSON.stringify(sid.A));

await sendUser(hA, '以后都记住：我的饮料是红茶', 1);
await sendUser(hA, '这个项目记住：我的资料是涉密', 2);
const pA = sandboxPaths(sid.A, 'butler');
const globalRowsA = rows(pA.global);
const localRowsA = rows(pA.local);
check('G2：global 层写入（持久副词 → global）',
  globalRowsA.some((r) => String(r.display_text || r.content).includes('红茶')),
  JSON.stringify(globalRowsA.map((r) => r.subject)));
check('G2：local 层写入（项目指代 → local）',
  localRowsA.some((r) => String(r.display_text || r.content).includes('涉密')));
check('G2：写入行带 sandbox_id/persona_id/scope',
  [...globalRowsA, ...localRowsA].every((r) => r.sandbox_id && r.persona_id && r.scope));
const auditLines = readJsonl(path.join(DATA, 'audit', 'audit.jsonl'));
check('G2：审计单本每行带 sandbox_id/persona_id',
  auditLines.length > 0 && auditLines.filter((r) => r.sandbox_id && r.persona_id).length === auditLines.length);

const factTeaA = await ask(hA, '红茶');
const factSecretA = await ask(hA, '涉密');
check('G2：DirA 问红茶可召回', factTeaA.includes('红茶'), factTeaA.slice(0, 120));
check('G2：DirA 问涉密可召回', factSecretA.includes('涉密'));

const sw = await activate(hA, sid.B);
check('G2：/switch 到 DirB 成功',
  sw.ok === true && sw.currentWorkspaceId === sid.B, JSON.stringify(sw));
const styleB = sectionText(hA, 'lingshu:style') || '';
check('G2：切换后自动加载 DirB 默认人格（architect）', styleB.includes('Architect'), styleB.slice(0, 80));
const factTeaB = await ask(hA, '红茶');
check('G2：DirB 读不到 DirA 的 global 记忆（人格隔离）', !factTeaB.includes('红茶'), factTeaB.slice(0, 120));
const panelB = await panelGet(hA);
check('G2：切换后面板只显示当前沙盒（local 为空）',
  panelB.ok === true && Array.isArray(panelB.local) && panelB.local.length === 0,
  JSON.stringify(panelB.local));

// ══ G3：ProjectA「红茶」global +「涉密」local → ProjectB 答得出红茶、答不出涉密 ══
const hC = makeHost();
apply(hC.ctx, { ...CFG, workspaceRoot: PROJ.C });
await sendUser(hC, '以后都记住：我的饮料是红茶', 10);
await sendUser(hC, '本项目记住：我的资料是涉密', 11);
check('G3：ProjectC 内部问红茶答得出', (await ask(hC, '红茶')).includes('红茶'));
check('G3：ProjectC 内部问涉密答得出', (await ask(hC, '涉密')).includes('涉密'));

// ── G4-c：记忆面板 v1（在 ProjectC 内部做；条目属于 C 的 local）──
const panelC = await panelGet(hC);
const secretRow = (panelC.local || []).find((r) => String(r.display_text).includes('涉密'));
check('G4：面板按人话展示（display_text）', Boolean(secretRow) && typeof secretRow.id === 'string',
  JSON.stringify(panelC.local));
check('G4：面板不直接给 content 原文（技术字段折叠）',
  (panelC.local || []).every((r) => !Object.prototype.hasOwnProperty.call(r, 'content')));
if (secretRow) {
  const f = await panelPost(hC, '/forget', { id: secretRow.id });
  check('G4：面板删除 = 墓碑（ok）', f.ok === true, JSON.stringify(f));
  check('G4：遗忘后不复活（召回不含涉密）', !(await ask(hC, '涉密')).includes('涉密'));
  const trash = await panelGet(hC);
  check('G4：回收站可见被删条', (trash.trash || []).some((r) => r.id === secretRow.id),
    JSON.stringify(trash.trash));
  const r = await panelPost(hC, '/trash/restore', { id: secretRow.id });
  check('G4：恢复后重新可召回', r.ok === true && (await ask(hC, '涉密')).includes('涉密'));
  const p = await panelPost(hC, '/trash/purge', { id: secretRow.id });
  check('G4：彻底删除 = redact（留骨架、不删行）', p.ok === true);
  const after = await panelGet(hC);
  check('G4：redact 后内容被抹掉', (after.trash || []).some((r) => r.id === secretRow.id && r.redacted === true));
  check('G4：彻底删除后不可召回', !(await ask(hC, '涉密')).includes('涉密'));
}

const swD = await activate(hC, sid.D);
check('G3：切到 ProjectD 成功（同人格 butler）',
  swD.ok === true && swD.currentWorkspaceId === sid.D, JSON.stringify(swD));
const factTeaD = await ask(hC, '红茶');
const factSecretD = await ask(hC, '涉密');
check('G3：ProjectD 问红茶答得出（global 跨项目）', factTeaD.includes('红茶'), factTeaD.slice(0, 120));
check('G3：ProjectD 问涉密答不知道（local 不跨项目）', !factSecretD.includes('涉密'));

// ══ G4 负例（其余）══
// a) 重复说不重复写
await sendUser(hC, '本项目记住：我的框架是 Flask', 20);
await sendUser(hC, '本项目记住：我的框架是 Flask', 21);
const pyRows = rows(sandboxPaths(sid.D, 'butler').local)
  .filter((r) => String(r.object || '').toLowerCase().includes('flask')
    || String(r.display_text || '').toLowerCase().includes('flask'));
check('G4：重复陈述不重复写（同 subject/relation/object 只 1 行）', pyRows.length === 1, String(pyRows.length));

// b) 判错 scope → 待定区，不参与检索
await sendUser(hC, '我的颜色是蓝色', 30);
const pendingRows = rows(sandboxPaths(sid.D, 'butler').pending);
check('G4：无信号陈述落 global_pending（待定区）', pendingRows.length >= 1, String(pendingRows.length));
check('G4：待定区默认不参与检索', !(await ask(hC, '蓝色')).includes('蓝色'));

// d) 跨沙盒读被拒（DirB 层面看不到 DirA 的任何 local）
const hB2 = makeHost();
apply(hB2.ctx, { ...CFG, workspaceRoot: PROJ.B });
const factInB = await ask(hB2, '红茶');
check('G4：新进程在 DirB 读不到 DirA 记忆', !factInB.includes('红茶'));
const panelB2 = await panelGet(hB2);
check('G4：面板在 DirB 不暴露 DirA 条目',
  panelB2.ok === true && (panelB2.local || []).length === 0 && (panelB2.global || []).length === 0);

// e) 冷启动：无 mapping 绑定 / 无 persona 文件 / 无记忆
const hE = makeHost();
apply(hE.ctx, { ...CFG, workspaceRoot: PROJ.E });
check('G4：冷启动无 mapping 时回退 config 默认人格（butler）',
  readJsonl(path.join(DATA, 'audit', 'audit.jsonl')).some(
    (r) => r.kind === 'apply' && r.sandbox_id === sid.E && r.persona_id === 'butler'),
  sid.E);
const coldFact = await ask(hE, '任何事');
check('G4：冷启动无记忆时召回为空且不报错（unknown/ask_clarify）',
  coldFact === '' || coldFact.includes('记忆库中无记录') || coldFact.includes('unknown'), coldFact.slice(0, 100));
check('G4：冷启动仍注册骨架段（v0.2 8 段，skeleton 空串）',
  hE.sections.length === 8 && (hE.sections.find((s) => s.name === 'lingshu:skeleton')?.text() === ''));

// ── 汇总 ────────────────────────────────────────────────
// LINGSHU_V02SIM_KEEP=1 时保留临时数据根（用于采集 [Host][Sandbox][Persona] 日志样例）。
if (process.env.LINGSHU_V02SIM_KEEP === '1') {
  console.log('保留临时数据根: ' + ROOT);
} else {
  try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* Windows 占用时忽略 */ }
}
console.log(`v02-sandbox-sim: ${PASS.length}/${PASS.length + FAIL.length} passed, ${FAIL.length} failed`);
if (FAIL.length) {
  console.log('失败项：' + FAIL.join('；'));
  process.exit(1);
}
