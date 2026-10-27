// 探针自检：用 mock ctx 跑一遍 apply()，验证不抛异常、日志落盘、工具可执行。
// 用法：$env:DSH_HOME="<临时目录>"; node probe/self-check.mjs
//
// v0.1.1 新增回归：**inject 受限的 ctx**（访问未 inject 的属性会抛错）。
// 这是 v0.1.0 在真实宿主上翻车的那个原因，必须留一条测试钉住。
import fs from 'node:fs';
import path from 'node:path';
import { apply, name, inject } from './dsh-lingshu-probe/index.js';

const results = [];
function check(label, cond, detail = '') {
  results.push({ label, cond, detail });
  console.log(`${cond ? '  ok   ' : '  FAIL '}${label}${detail ? '  — ' + detail : ''}`);
}

const home = process.env.DSH_HOME;
if (!home) {
  console.error('请先设置 DSH_HOME 到临时目录，避免污染真实 ~/.dsh');
  process.exit(2);
}

const registered = [];
const subscribed = [];
let threw = null;
const ctx = {
  tools: { register: (t) => registered.push(t) },
  on: (ev, fn) => { subscribed.push(ev); },
  get: (n) => (n === 'systemPrompt' ? { section() {} } : n === 'approval' ? { request: async () => 'allowed-once' } : undefined),
};

try {
  apply(ctx, {});
} catch (err) {
  threw = err;
}

check('apply() 不抛异常', threw === null, threw ? String(threw).slice(0, 120) : '');
check('导出了 name', name === 'dsh-lingshu-probe', String(name));
check('导出了 inject=tools', Array.isArray(inject) && inject.includes('tools'), JSON.stringify(inject));
check('注册了 probe_status 工具', registered.length === 1 && registered[0].name === 'probe_status',
  registered.map((t) => t.name).join(','));
check('订阅了事件', subscribed.length >= 4, subscribed.join(','));

// ── 回归：cordis 对未 inject 的属性访问抛错 ──────────────────────────
const badCtx = {
  tools: { register: () => {} },
  on: () => {},
  get: () => { throw new Error('cannot get property "systemPrompt" without inject'); },
  get systemPrompt() { throw new Error('cannot get property "systemPrompt" without inject'); },
};
let badThrew = null;
try { apply(badCtx, {}); } catch (err) { badThrew = err; }
check('inject 受限的 ctx 下 apply 仍不抛异常', badThrew === null,
  badThrew ? String(badThrew).slice(0, 120) : '');

const logPath = path.join(home, 'plugins', 'dsh-lingshu-probe', 'probe.log');
const exists = fs.existsSync(logPath);
check('日志文件已创建', exists, logPath);

let lines = [];
if (exists) lines = fs.readFileSync(logPath, 'utf8').split('\n').filter(Boolean);
const recs = lines.map((l) => { try { return JSON.parse(l); } catch { return { event: 'unparsable' }; } });
const evs = recs.map((r) => r.event);

check('记下了 probe/apply（两次加载各一条）',
  evs.filter((e) => e === 'probe/apply').length >= 2,
  String(evs.filter((e) => e === 'probe/apply').length));
check('记下了 probe/service', evs.includes('probe/service'));
check('记下了 probe/ready', evs.includes('probe/ready'));
check('**没有** probe/error（v0.1.0 的翻车点）', !evs.includes('probe/error'),
  evs.filter((e) => e === 'probe/error').join(',') || '无');
check('service 探测带 known 字段',
  recs.filter((r) => r.event === 'probe/service').every((r) => typeof r?.payload?.keys?.known !== 'undefined'));

// ── 工具可执行 ──────────────────────────────────────────────────
(async () => {
  let value = null;
  let err = null;
  try {
    value = await registered[0].execute({ tail: 5 });
  } catch (e) { err = e; }
  check('probe_status 可执行', err === null, err ? String(err).slice(0, 120) : '');
  check('probe_status 返回 logPath', value && value.logPath === logPath);
  check('probe_status 返回 counts', value && typeof value.counts === 'object');
  check('hasSystemPrompt 是布尔或 null',
    value && (value.hasSystemPrompt === null || typeof value.hasSystemPrompt === 'boolean'),
    String(value && value.hasSystemPrompt));
  check('render 不抛异常', (() => {
    try { const s = registered[0].output.render({}, value); return typeof s === 'string' && s.length > 0; }
    catch { return false; }
  })());

  const failed = results.filter((r) => !r.cond);
  console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
  process.exit(failed.length === 0 ? 0 : 1);
})();
