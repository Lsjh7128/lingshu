// v02-http-sim.mjs —— v0.2 前后端「真 HTTP」打通验证
//
// 与 v02-sandbox-sim 的区别：那里直接调用路由 handler；这里起一个真 http 服务，
// 用真实 fetch 请求 index.js 注册的 dashboard 路由（页面 + /api/memory 四条），
// 证明「前端 URL ↔ 后端路由 ↔ index.js provider ↔ MemoryRuntime ↔ 文件」这条链是通的。
//
// 不做的事（必须如实）：不启动 DSH 宿主、不开真实浏览器；DOM 渲染与 iframe/CSP 属宿主侧，
// 这里只验证 HTTP 契约与静态页可达。
//
// 运行：node plugin/dsh-lingshu/v02-http-sim.mjs --assert
import fs from 'node:fs';
import http from 'node:http';
import os from 'node:os';
import path from 'node:path';

import { apply } from './index.js';
import { writeLayout, sandboxId, canonicalizePath, readJsonl, foldRows } from './memory-paths.js';

const PASS = [];
const FAIL = [];
function check(name, cond, detail = '') {
  if (cond) PASS.push(name);
  else { FAIL.push(name); console.log(`  ! ${name}${detail ? ': ' + detail : ''}`); }
}

const ROOT = path.join(os.tmpdir(), `lingshu-v02http-${process.pid}-${Date.now().toString(36)}`);
const DATA = path.join(ROOT, 'data');
const PROJ = path.join(ROOT, 'proj');
const SID = sandboxId(canonicalizePath(PROJ));

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, JSON.stringify(value, null, 2), 'utf8');
}

// ── 1. 假宿主：只收集 webServer 路由，其余服务给最小实现 ──
function makeHost() {
  const routes = [];
  const hooks = {};
  const sections = [];
  const ctx = {
    systemPrompt: { section(sec) { sections.push(sec); } },
    tools: { register() { return () => {}; } },
    on(evt, fn) { hooks[evt] = fn; },
    inject(_deps, fn) {
      fn({
        webServer: { register(spec) { routes.push(spec); return () => {}; } },
        effect(cb) { try { cb(); } catch { /* noop */ } },
      });
    },
  };
  return { ctx, routes, hooks, sections };
}

fs.mkdirSync(DATA, { recursive: true });
fs.mkdirSync(PROJ, { recursive: true });
writeLayout(DATA, { migratedFrom: ['http-sim'], now: Math.floor(Date.now() / 1000) });
writeJson(path.join(DATA, 'sandboxes', 'index.json'), {
  schema_version: 'sandbox-index-0.1',
  sandboxes: {
    [SID]: {
      canonical_path: canonicalizePath(PROJ), label: 'http-sim',
      created_at: Math.floor(Date.now() / 1000), last_active_at: Math.floor(Date.now() / 1000),
    },
  },
});
writeJson(path.join(DATA, 'sandboxes', SID, 'sandbox.json'), {
  schema_version: 'sandbox-0.1', sandbox_id: SID, canonical_path: canonicalizePath(PROJ),
  label: 'http-sim', created_at: Math.floor(Date.now() / 1000), profile_tag: null,
});
writeJson(path.join(DATA, 'personas', 'butler', 'persona.json'), {
  schema_version: 'persona-0.1', persona_id: 'butler', name: 'Butler',
  summary: '沉稳管家', confirmed_by_user: true, confirmed_at: new Date().toISOString(),
});
// 新绑定模型：没有绑定 = 无人格；测试数据根必须显式把 PROJ 绑给 butler。
fs.writeFileSync(path.join(DATA, 'sandbox_mapping.yaml'),
  `schema_version: mapping-0.1\nbindings:\n  - canonical_path: "${canonicalizePath(PROJ)}"\n    sandbox_id: "${SID}"\n    default_persona: "butler"\n    label: "http-sim"\n`, 'utf8');
process.env.LINGSHU_DATA_DIR = DATA;

const host = makeHost();
apply(host.ctx, { mode: 'active', workspaceRoot: PROJ, coldTurns: 0, warmTurns: 0 });

// ── 2. 用插件真实写路径落两条记忆（global + local）──
async function sendUser(text, turn) {
  await host.hooks['session/event']({}, { type: 'user/message', data: { message: { role: 'user', content: text } } });
  await host.hooks['session/event']({}, { type: 'turn/end', data: { turn } });
}
await sendUser('以后都记住：我的饮料是红茶', 1);
await sendUser('本项目记住：我的资料是涉密', 2);

// ── 3. 真 HTTP 服务：按 exact/prefix 分发到 index.js 注册的路由 ──
const server = http.createServer((req, res) => {
  let pathname = '/';
  try { pathname = new URL(req.url ?? '/', 'http://127.0.0.1').pathname; } catch { /* 保持 / */ }
  const exact = host.routes.find((r) => r.kind === 'exact' && r.path === pathname);
  const prefix = exact ? null : host.routes
    .filter((r) => r.kind === 'prefix' && pathname.startsWith(r.path))
    .sort((a, b) => b.path.length - a.path.length)[0];
  const spec = exact || prefix;
  if (!spec) { res.statusCode = 404; res.end('not_found'); return; }
  Promise.resolve(spec.handler(req, res)).catch((err) => {
    try { res.statusCode = 500; res.end(String(err)); } catch { /* noop */ }
  });
});
await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
const BASE = `http://127.0.0.1:${server.address().port}`;

const ROUTES = host.routes.map((r) => r.path);
check('HTTP：记忆 API 四条路由已注册',
  ['/dsh-lingshu/api/memory', '/dsh-lingshu/api/memory/forget',
   '/dsh-lingshu/api/memory/trash/restore', '/dsh-lingshu/api/memory/trash/purge']
    .every((p) => ROUTES.includes(p)), JSON.stringify(ROUTES));
check('HTTP：面板页面路由已注册', ROUTES.includes('/dsh-lingshu/dashboard/memory'));

// 页面可达
const page = await fetch(`${BASE}/dsh-lingshu/dashboard/memory`);
const pageText = await page.text();
check('HTTP：面板页面 200 + text/html',
  page.status === 200 && String(page.headers.get('content-type')).includes('text/html'),
  String(page.status));
check('HTTP：页面内引用后端地址与中文文案',
  pageText.includes('/dsh-lingshu/api/memory') && pageText.includes('移到回收站') && pageText.includes('彻底删除'));

// 只读视图
const listRes = await fetch(`${BASE}/dsh-lingshu/api/memory`, { headers: { accept: 'application/json' } });
const list = await listRes.json();
check('HTTP：GET /api/memory 200 且 ok', listRes.status === 200 && list.ok === true);
check('HTTP：返回当前 sandbox/persona', list.sandbox_id === SID && list.persona_id === 'butler',
  `${list.sandbox_id}/${list.persona_id}`);
const local = list.local || [];
const global = list.global || [];
check('HTTP：local 层有「涉密」', local.some((r) => String(r.display_text).includes('涉密')), JSON.stringify(local));
check('HTTP：global 层有「红茶」', global.some((r) => String(r.display_text).includes('红茶')), JSON.stringify(global));
check('HTTP：面板行只给 display_text/date/uses（无 content 原文）',
  [...local, ...global].every((r) => !Object.prototype.hasOwnProperty.call(r, 'content')
    && typeof r.display_text === 'string' && 'date' in r && 'uses' in r));

const secret = local.find((r) => String(r.display_text).includes('涉密'));

// 删除 → 回收站 → 恢复 → 彻底删除（全走真 HTTP）
async function post(sub, body) {
  const r = await fetch(`${BASE}/dsh-lingshu/api/memory${sub}`, {
    method: 'POST',
    headers: { 'content-type': 'application/json' },
    body: JSON.stringify(body),
  });
  let json = null;
  try { json = await r.json(); } catch { /* 非 JSON */ }
  return { status: r.status, json };
}
const forget = await post('/forget', { id: secret.id });
check('HTTP：POST /forget 200', forget.status === 200 && forget.json?.ok === true, JSON.stringify(forget));
const afterForget = (await (await fetch(`${BASE}/dsh-lingshu/api/memory`)).json());
check('HTTP：遗忘后 local 不含、回收站含',
  !(afterForget.local || []).some((r) => r.id === secret.id)
    && (afterForget.trash || []).some((r) => r.id === secret.id));

const restore = await post('/trash/restore', { id: secret.id });
const afterRestore = await (await fetch(`${BASE}/dsh-lingshu/api/memory`)).json();
check('HTTP：POST /trash/restore 200 且恢复可召回',
  restore.status === 200 && (afterRestore.local || []).some((r) => r.id === secret.id));

const purge = await post('/trash/purge', { id: secret.id });
const afterPurge = await (await fetch(`${BASE}/dsh-lingshu/api/memory`)).json();
check('HTTP：POST /trash/purge 200 且回收站标记 redacted',
  purge.status === 200 && (afterPurge.trash || []).some((r) => r.id === secret.id && r.redacted === true));

const bad = await post('/forget', {});
check('HTTP：空 id → 400', bad.status === 400, String(bad.status));
const missing = await fetch(`${BASE}/dsh-lingshu/api/nope`);
check('HTTP：未知路由 → 404', missing.status === 404, String(missing.status));

// ── 工作区面板：v0.2 列表 = 沙盒列表；热切换按 sandboxId ──
const wsList = await (await fetch(`${BASE}/dsh-lingshu/api/workspaces`)).json();
check('HTTP：工作区列表 v0.2 = 沙盒列表（当前项高亮）',
  wsList.ok === true && wsList.layout === 'v0.2'
    && (wsList.workspaces || []).some((w) => w.workspaceId === SID && w.isCurrent === true),
  JSON.stringify((wsList.workspaces || []).map((w) => [w.workspaceId, w.isCurrent])));
const wsOne = await (await fetch(`${BASE}/dsh-lingshu/api/workspaces/${SID}`)).json();
check('HTTP：工作区详情按 sandboxId 可读',
  wsOne.ok === true && wsOne.workspace?.workspaceId === SID);
const act = await fetch(`${BASE}/dsh-lingshu/api/workspaces/${SID}/activate`, { method: 'POST' });
const actJson = await act.json();
check('HTTP：热切换（POST activate）200 且返回 sandboxId',
  act.status === 200 && actJson.ok === true && actJson.currentWorkspaceId === SID,
  JSON.stringify(actJson));

// 文件层自证：purge 之后本地文件里仍有该 id 的骨架行（append-only、不物理删行）
const rawLocal = readJsonl(path.join(DATA, 'sandboxes', SID, 'butler', 'local_memory.jsonl'));
const folded = foldRows(rawLocal);
check('HTTP：purge 后文件仍保留骨架行（append-only）',
  rawLocal.some((r) => String(r.id) === secret.id)
    && folded.suppressed.some((r) => String(r.id) === secret.id && r.content === '[已彻底删除]'));

// ── 人格体 / 人格舱（绑定）真 HTTP ──────────────────────────────
const secStyle = (name) => {
  const s = host.sections.find((x) => x.name === name);
  return s ? String(s.text() || '') : '';
};
const personas0 = await (await fetch(`${BASE}/dsh-lingshu/api/personas`)).json();
check('HTTP：人格体列表含 butler', personas0.ok === true
  && (personas0.personas || []).some((p) => p.persona_id === 'butler'));
const created = await (await fetch(`${BASE}/dsh-lingshu/api/personas`, {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ name: 'Tester', summary: '测试人格', traits: { warmth: 0.4 } }),
})).json();
check('HTTP：创建人格体 201', created.ok === true && Boolean(created.persona?.persona_id), JSON.stringify(created));
const PROJ2 = path.join(ROOT, 'proj2');
fs.mkdirSync(PROJ2, { recursive: true });
const bind2 = await (await fetch(`${BASE}/dsh-lingshu/api/bindings`, {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ persona_id: created.persona.persona_id, workspace_root: PROJ2 }),
})).json();
check('HTTP：绑定人格舱 200', bind2.ok === true
  && bind2.binding?.default_persona === created.persona.persona_id, JSON.stringify(bind2));
const avail = await (await fetch(`${BASE}/dsh-lingshu/api/workspaces/available`)).json();
check('HTTP：可选人格舱列表含当前工作区（且已绑定可见）', avail.ok === true
  && (avail.workspaces || []).some((w) => w.workspace_root === canonicalizePath(PROJ)
    && w.bound_persona_id === 'butler'));
const unbind = await (await fetch(`${BASE}/dsh-lingshu/api/bindings/unbind`, {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ workspace_root: PROJ }),
})).json();
check('HTTP：解绑当前人格舱 200', unbind.ok === true);
const memUnbound = await (await fetch(`${BASE}/dsh-lingshu/api/memory`)).json();
check('HTTP：解绑后无人格 → 不读记忆', memUnbound.ok === true && memUnbound.unbound === true
  && (memUnbound.local || []).length === 0);
check('HTTP：解绑后风格段为空（不注入人格）', secStyle('lingshu:style') === '');
const rebind = await (await fetch(`${BASE}/dsh-lingshu/api/bindings`, {
  method: 'POST', headers: { 'content-type': 'application/json' },
  body: JSON.stringify({ persona_id: 'butler', workspace_root: PROJ }),
})).json();
check('HTTP：重新绑定 200', rebind.ok === true);
const memBack = await (await fetch(`${BASE}/dsh-lingshu/api/memory`)).json();
check('HTTP：重绑后记忆仍可读（global 保留、回收站保留 redacted）',
  (memBack.global || []).length === 1
    && (memBack.trash || []).some((r) => r.redacted === true),
  JSON.stringify({ global: (memBack.global || []).length, trash: (memBack.trash || []).length }));

server.close();
try { fs.rmSync(ROOT, { recursive: true, force: true }); } catch { /* Windows 占用时忽略 */ }

console.log(`v02-http-sim: ${PASS.length}/${PASS.length + FAIL.length} passed, ${FAIL.length} failed`);
if (FAIL.length) {
  console.log('失败项：' + FAIL.join('；'));
  process.exit(1);
}
