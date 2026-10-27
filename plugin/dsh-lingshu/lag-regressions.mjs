// lag-regressions.mjs —— F-D「前馈注入一回合滞后」独立回归（A4，2026-10-27 新增）
//
// 为什么必须单独一个文件：host-sim.mjs 只按 mock 时序 fire `agent/pre-step`，
// **从不 fire `agent/inbox/claimed`、也不跑 `systemPrompt.assemble`**，所以它结构上
// 不可能发现 F-D。本脚本独立实现最小 mock ctx（不 import host-sim），按宿主**真实时序**跑：
//
//     claimed → systemPrompt.assemble（会调用注册的 7 个 section text()，并跑
//     waterfall "system-prompt/assemble"，返回的 assembly.sections 才是最终注入文本）
//     → agent/pre-step
//
// 宿主源码依据（本机 @deepseek-ai/dsh-agent-loop/lib/index.js，preStep）：
//   L906  const claimed = this.inbox.claim(target, position.turn)
//         ↳ claim() 内 dispatch.emit("agent/inbox/claimed", { message, turn })
//   L907  await this.loopCtx.systemPrompt.assemble(assembleContextFor(this, signal))
//         ↳ @deepseek-ai/dsh-system-prompt: sections 的 text() 在 assemble() 内求值，
//           随后 await ctx.waterfall("system-prompt/assemble", assembly, context, next)，
//           返回的 transformed.sections[].text 是权威注入文本（可整段改写）。
//   L911  dispatch.waterfall("agent/pre-step", { messages: claimed, ...position, signal }, next)
//
// 于是：插件在 pre-step 里更新的 phase/anchor/blocked 只能影响**下一次** assemble ——
// 这正是论文 F-D。本脚本钉住修复后的语义：
//   ① critical 回合的**首次 assemble** 文本就同时含首尾锚点；
//   ② 该回合 pre_step 审计 anchor_lag=false（已预计算，无滞后）；
//   ③ 只 fire pre-step、不 fire claimed 的旧路径仍正常（兜底语义）：anchor_lag=true；
//   ④ 段落数/order 仍为 90,100,105,110,120,130,140（不新增段落）。
//
// 注意：A3 未完成时 ①②（以及"下一回合不残留锚点"）预期红，这是正常的。
//
// 用法：node plugin/dsh-lingshu/lag-regressions.mjs [--assert]

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { apply } from './index.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.resolve(HERE, '..', '..');
const TMP = path.join(REPO, '.tmp');
const ASSERT_ONLY = process.argv.includes('--assert');

const SECTION_ORDER = '90,100,105,110,120,130,140';
const ANCHOR_HEAD = '〖人格锚点〗';
const ANCHOR_TAIL = '〖人格锚点·复核〗';

const PASS = [];
const FAIL = [];
const keptDirs = [];

function check(label, cond, detail = '') {
  (cond ? PASS : FAIL).push(label);
  if (cond && ASSERT_ONLY) return;              // --assert 模式只打失败行 + 汇总
  const tail = detail === '' || detail === undefined ? '' : '  — ' + String(detail).slice(0, 220);
  console.log(`${cond ? '  ok   ' : '  FAIL '}${label}${tail}`);
}

const has = (obj, key) => Boolean(obj) && Object.prototype.hasOwnProperty.call(obj, key);

// ── 隔离数据目录（仓库 .tmp/，不用 os.tmpdir()：本机沙箱会拒绝临时目录写入）──
function freshDir(tag) {
  fs.mkdirSync(TMP, { recursive: true });
  const dir = path.join(TMP, `lag-${tag}-${process.pid}-${Date.now()}`);
  fs.mkdirSync(dir, { recursive: true });
  // LINGSHU_DATA_DIR 是插件官方的数据目录隔离入口（与 stateDir() 同口径），
  // 只影响本脚本进程，不碰真实 $DSH_HOME/plugins/dsh-lingshu。
  process.env.LINGSHU_DATA_DIR = dir;
  keptDirs.push(dir);
  return dir;
}

function readJsonl(file) {
  try {
    return fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '').split('\n')
      .filter(Boolean).map((l) => { try { return JSON.parse(l); } catch { return null; } })
      .filter(Boolean);
  } catch { return []; }
}
const auditRows = (dir) => readJsonl(path.join(dir, 'audit.jsonl'));
const readState = (dir) => JSON.parse(fs.readFileSync(path.join(dir, 'state.json'), 'utf8'));

// ── 最小 mock ctx：只实现插件真正用到的 4 个能力 ───────────────────────
// 与 host-sim 的 MockHost 独立实现（不 import），因为这里要模拟**宿主内部时序**，
// 需要把 assemble 也纳入 mock（host-sim 没有 assemble 概念）。
function makeHost() {
  const sections = [];     // {name, order, text}
  const hooks = {};        // event -> handler[]
  const tools = [];
  const ctx = {
    tools: { register: (t) => { tools.push(t); } },
    systemPrompt: { section: (s) => { sections.push(s); } },
    on: (ev, fn) => { (hooks[ev] = hooks[ev] || []).push(fn); },
    effect: (fn) => { try { return fn(); } catch { /* 忽略 */ } },
    inject: () => {},
    get: (n) => { throw new Error(`cannot get property "${n}" without inject`); },
  };
  return { ctx, sections, hooks, tools };
}

// dispatch.emit：宿主 claim() 里 emit("agent/inbox/claimed", {message, turn})
async function emit(hooks, ev, ...args) {
  let out;
  for (const fn of hooks[ev] || []) out = await fn(...args);
  return out;
}

// dispatch.waterfall / ctx.waterfall：handler(...args, next)；next() 走到下一个 handler，
// 全部走完后返回 fallback。这是 cordis waterfall 的语义，插件侧 pre-step 就依赖它。
async function waterfall(hooks, ev, args, fallback) {
  const hs = (hooks[ev] || []).slice();
  let i = 0;
  const next = async () => {
    const h = hs[i++];
    if (!h) return fallback();
    return await h(...args, next);
  };
  return next();
}

// systemPrompt.assemble 的最小忠实实现（@deepseek-ai/dsh-system-prompt SystemPrompt#assemble）：
// 1) 段落按 order 升序（order 相同按 name）排序；2) 逐段调用 text(context)；
// 3) 跑 waterfall "system-prompt/assemble"(assembly, context, next)；
// 4) 最终注入文本 = 非空 section.text 用 "\n\n" 连接（renderPrompt/renderContextSections 口径）。
function buildAssembly(sections, context) {
  const defs = sections.slice().sort((a, b) => (Number(a.order) - Number(b.order))
    || (a.name < b.name ? -1 : a.name > b.name ? 1 : 0));
  return {
    sections: defs.map((s) => ({
      name: s.name,
      text: typeof s.text === 'function' ? s.text(context) : s.text,
    })),
    contexts: [],
    tools: [],
    variables: {},
  };
}

async function assemblePrompt(host, context = {}) {
  const assembly = buildAssembly(host.sections, context);
  const handlerCount = (host.hooks['system-prompt/assemble'] || []).length;
  let transformed;
  let error = null;
  try {
    transformed = await waterfall(host.hooks, 'system-prompt/assemble',
      [assembly, context], () => Promise.resolve(assembly));
  } catch (e) {
    error = String((e && e.stack) || e);
    transformed = assembly;
  }
  // 宿主语义：waterfall 的返回值就是 authoritative assembly（宿主随后 renderContextSections/
  // joined 都会用它）。handler 返回 undefined/null 会让宿主拿到 undefined assembly → 整轮崩，
  // 这里显式判红，而不是静默退回未改写的 assembly。
  if ((transformed === undefined || transformed === null) && handlerCount > 0) {
    error = 'system-prompt/assemble handler 返回 ' + String(transformed)
      + '（宿主会拿到 undefined assembly，整轮崩）';
    transformed = assembly;
  }
  const final = (transformed && Array.isArray(transformed.sections)) ? transformed : assembly;
  const text = final.sections
    .filter((s) => String(s && s.text != null ? s.text : '').length > 0)
    .map((s) => String(s.text))
    .join('\n\n');
  return { assembly: final, text, error };
}

const userMsg = (text) => [{ role: 'user', content: [{ type: 'text', text }] }];
const sectionText = (host, key) => {
  const s = host.sections.find((x) => x.name === 'lingshu:' + key);
  return s ? s.text() : null;
};
const head = (t) => String(t || '').slice(0, 40);
const tail = (t) => String(t || '').slice(-40);

// 长文本把 ratio 推过 0.85（charsPerToken=3 是插件默认估算口径；这里只造输入，不复制插件语义）。
const LONG_TEXT = '上下文用量压力测试 ' + 'x'.repeat(4000);

// ═══════════════════════════════════════════════════════════════════
// [1] 真实宿主时序：claimed → assemble → pre-step（F-D 主场景）
// ═══════════════════════════════════════════════════════════════════
console.log('[1] 真实宿主时序 claimed → assemble → pre-step');
const dirReal = freshDir('real-order');
const hostReal = makeHost();
apply(hostReal.ctx, { coldTurns: 0, warmTurns: 0, contextWindowTokens: 1000, topK: 3 });
let firstAssemble = { text: '', assembly: { sections: [] }, error: null };
let realDecision = null;
let realThrew = null;
try {
  // ① claimed：宿主 inbox.claim() 在 assemble **之前** emit，携带本 step 的用户消息与 turn
  await emit(hostReal.hooks, 'agent/inbox/claimed', {
    message: { role: 'user', content: [{ type: 'text', text: LONG_TEXT }] },
    turn: 1,
  });
  // ② assemble：注册段落的 text() 在这里被求值；修复后在首个 assemble 就应拿到本回合状态
  firstAssemble = await assemblePrompt(hostReal, { agent: {}, scope: {}, signal: undefined });
  // ③ pre-step：宿主把 claimed 作为 messages 传进来
  realDecision = await waterfall(hostReal.hooks, 'agent/pre-step',
    [{ messages: userMsg(LONG_TEXT), turn: 1, step: 1, signal: {} }],
    () => Promise.resolve({ kind: 'enter', messages: [] }));
} catch (e) {
  realThrew = String((e && e.stack) || e);
}

check('真实时序下 claimed/assemble/pre-step 不抛异常', realThrew === null, realThrew || '');
check('assemble 未抛错且拿到 7 段 assembly',
  firstAssemble.error === null && firstAssemble.assembly.sections.length === 7,
  firstAssemble.error || `sections=${firstAssemble.assembly.sections.length}`);
check('① critical 回合**首次 assemble** 文本含首部锚点『〖人格锚点〗』',
  firstAssemble.text.includes(ANCHOR_HEAD), head(firstAssemble.text));
check('① critical 回合**首次 assemble** 文本含尾部锚点『〖人格锚点·复核〗』',
  firstAssemble.text.includes(ANCHOR_TAIL), tail(firstAssemble.text));
check('① 首次 assemble 文本以首部锚点开头（order=90 在最前）',
  firstAssemble.text.startsWith(ANCHOR_HEAD), head(firstAssemble.text));
check('① 首次 assemble 文本以复核锚点结尾（order=140 在最后）',
  firstAssemble.text.trimEnd().endsWith('不编造记忆。'), tail(firstAssemble.text));
check('pre-step 仍返回 next() 的决策（waterfall 契约不变）',
  realDecision && realDecision.kind === 'enter', JSON.stringify(realDecision));

{
  const rows = auditRows(dirReal);
  const pre = rows.filter((r) => r.kind === 'pre_step');
  const row = pre.find((r) => r.firstStep === true) || pre[0] || null;
  check('真实时序回合有 pre_step 审计行', Boolean(row), `pre_step=${pre.length}`);
  check('② pre_step 审计**含 anchor_lag 字段**（只增不改）', has(row, 'anchor_lag'),
    row ? Object.keys(row).join(',') : 'no row');
  check('② 真实时序回合 pre_step.anchor_lag=false（本回合已预计算，无滞后）',
    Boolean(row) && row.anchor_lag === false, row ? JSON.stringify(row.anchor_lag) : 'no row');
  const st = readState(dirReal);
  check('claimed+assemble+pre-step 后轮次只 +1（预计算不重复积分/不重复记轮次）',
    Number(st.turns) === 1, String(st.turns));
  check('真实时序回合 phase=critical 且 anchorRequired=true（本回合状态，不是上一回合快照）',
    st.control && st.control.phase && st.control.phase.phase === 'critical'
      && st.control.phase.anchorRequired === true,
    JSON.stringify(st.control && st.control.phase));
}

// ═══════════════════════════════════════════════════════════════════
// [2] 段落契约：不新增段落（7 段 / order 90..140）
// ═══════════════════════════════════════════════════════════════════
console.log('\n[2] 段落契约（不新增段落）');
check('④ 注册段落数仍为 7', hostReal.sections.length === 7, String(hostReal.sections.length));
check('④ 段落 order 仍为 ' + SECTION_ORDER,
  hostReal.sections.map((s) => s.order).join(',') === SECTION_ORDER,
  hostReal.sections.map((s) => s.order).join(','));
check('④ 段落名仍全部是 lingshu:*',
  hostReal.sections.every((s) => String(s.name).startsWith('lingshu:')),
  hostReal.sections.map((s) => s.name).join(','));
check('assemble 后 7 段 text() 仍全部返回字符串（绝不返回 null）',
  hostReal.sections.every((s) => typeof s.text() === 'string'));

// ═══════════════════════════════════════════════════════════════════
// [3] 下一回合不残留上一回合锚点（滞后缺陷的"反向"验收）
// ═══════════════════════════════════════════════════════════════════
console.log('\n[3] 下一回合不残留上一回合锚点');
// 同一 host 继续第 2 回合：短消息 → ratio 很低 → phase=normal，首次 assemble 不得还有锚点。
// （若仍有锚点，说明注入用的还是上一回合快照 —— 正是 F-D 的表现。）
let turn2Assemble = { text: '', error: null };
try {
  await emit(hostReal.hooks, 'agent/inbox/claimed', {
    message: { role: 'user', content: [{ type: 'text', text: '项目代号是什么' }] },
    turn: 2,
  });
  turn2Assemble = await assemblePrompt(hostReal, { agent: {}, scope: {} });
  await waterfall(hostReal.hooks, 'agent/pre-step',
    [{ messages: userMsg('项目代号是什么'), turn: 2, step: 1, signal: {} }],
    () => Promise.resolve({ kind: 'enter', messages: [] }));
} catch (e) {
  turn2Assemble = { text: '', error: String((e && e.stack) || e) };
}
check('第 2 回合（低用量）首次 assemble 不残留上一回合的首部锚点',
  turn2Assemble.error === null && !turn2Assemble.text.includes(ANCHOR_HEAD),
  head(turn2Assemble.text) || turn2Assemble.error || '');
check('第 2 回合（低用量）首次 assemble 不残留上一回合的复核锚点',
  turn2Assemble.error === null && !turn2Assemble.text.includes(ANCHOR_TAIL),
  tail(turn2Assemble.text) || turn2Assemble.error || '');

// ═══════════════════════════════════════════════════════════════════
// [4] 旧路径（只 fire pre-step，不 fire claimed）：兜底语义不回归
// ═══════════════════════════════════════════════════════════════════
console.log('\n[4] 旧路径（只 fire pre-step，不 fire claimed）');
const dirLegacy = freshDir('legacy');
const hostLegacy = makeHost();
apply(hostLegacy.ctx, { coldTurns: 0, warmTurns: 0, contextWindowTokens: 1000, topK: 3 });
let legacyDecision = null;
let legacyThrew = null;
try {
  legacyDecision = await waterfall(hostLegacy.hooks, 'agent/pre-step',
    [{ messages: userMsg(LONG_TEXT), turn: 1, step: 1, signal: {} }],
    () => Promise.resolve({ kind: 'enter', messages: [] }));
} catch (e) {
  legacyThrew = String((e && e.stack) || e);
}
check('③ 旧路径不崩：pre-step 返回 next() 的决策',
  legacyThrew === null && legacyDecision && legacyDecision.kind === 'enter',
  legacyThrew || JSON.stringify(legacyDecision));
check('③ 旧路径段落渲染正常：宪法段含"宪法"',
  String(sectionText(hostLegacy, 'constitution') || '').includes('宪法'),
  head(sectionText(hostLegacy, 'constitution')));
check('③ 旧路径 critical 仍补首尾锚点（兜底语义不回归）',
  String(sectionText(hostLegacy, 'anchorHead') || '').includes(ANCHOR_HEAD)
    && String(sectionText(hostLegacy, 'anchorTail') || '').includes('复核'),
  `${head(sectionText(hostLegacy, 'anchorHead'))} | ${tail(sectionText(hostLegacy, 'anchorTail'))}`);
{
  const rows = auditRows(dirLegacy);
  const pre = rows.filter((r) => r.kind === 'pre_step');
  const row = pre.find((r) => r.firstStep === true) || pre[0] || null;
  check('③ 旧路径 pre_step 审计含 anchor_lag 字段', has(row, 'anchor_lag'),
    row ? Object.keys(row).join(',') : 'no row');
  check('③ 旧路径 pre_step.anchor_lag=true（兜底：只能用上一回合快照）',
    Boolean(row) && row.anchor_lag === true, row ? JSON.stringify(row.anchor_lag) : 'no row');
  check('③ 旧路径轮次 +1（既有行为不变）', Number(readState(dirLegacy).turns) === 1,
    String(readState(dirLegacy).turns));
}

// ── 汇总 ─────────────────────────────────────────────────────────
const total = PASS.length + FAIL.length;
if (FAIL.length) {
  console.log(`\n保留失败现场的 .tmp 目录：\n  ${keptDirs.join('\n  ')}`);
} else {
  for (const d of keptDirs) { try { fs.rmSync(d, { recursive: true, force: true }); } catch { /* 忽略 */ } }
}
console.log(`\n${PASS.length} passed, ${FAIL.length} failed (共 ${total} 条断言)`);
if (FAIL.length) {
  console.log('FAILED: ' + FAIL.slice(0, 20).join('; '));
  process.exitCode = 1;
} else {
  process.exitCode = 0;
}
