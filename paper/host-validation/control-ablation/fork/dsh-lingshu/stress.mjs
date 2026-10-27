// stress.mjs —— 长会话压测（30+ 轮），跑在插件真实状态机上（mock 宿主）。
//
// 为什么单独一个脚本：sim 是"逐条契约"的断言，压测关心的是**跨轮推移**——
// 冷启动阶段是否按时迁移、目标会不会堆积、关系阶段会不会卡死、记忆 id 会不会重号。
//
// 用法：node stress.mjs [--turns 40] [--assert]

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { apply } from './index.js';
import { applyWriteRequests } from './memorywriter.js';

const ASSERT_ONLY = process.argv.includes('--assert');
const argTurns = Number((process.argv.find((a) => a.startsWith('--turns=')) || '').split('=')[1]);
const TURNS = Number.isFinite(argTurns) && argTurns > 0 ? argTurns : 40;

const results = [];
function check(label, cond, detail = '') {
  results.push({ label, cond });
  if (!ASSERT_ONLY) console.log(`${cond ? '  ok   ' : '  FAIL '}${label}${detail ? '  — ' + detail : ''}`);
}

const home = fs.mkdtempSync(path.join(os.tmpdir(), 'lingshu-stress-'));
process.env.DSH_HOME = home;
const dir = path.join(home, 'plugins', 'dsh-lingshu');
fs.mkdirSync(dir, { recursive: true });

const sections = [];
const hooks = {};
const tools = [];
const ctx = {
  tools: { register: (t) => tools.push(t) },
  systemPrompt: { section: (s) => sections.push(s) },
  on: (ev, fn) => { (hooks[ev] = hooks[ev] || []).push(fn) },
  get: (n) => { throw new Error(`cannot get property "${n}" without inject`) },
  effect: (fn) => { try { return fn() } catch { /* ignore */ } },
  inject: () => {},
};

const cfg = { coldTurns: 3, warmTurns: 20 };
apply(ctx, cfg);

const readState = () => JSON.parse(fs.readFileSync(path.join(dir, 'state.json'), 'utf8'));
const memLines = () => fs.readFileSync(path.join(dir, 'memory.jsonl'), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
const auditLines = () => fs.readFileSync(path.join(dir, 'audit.jsonl'), 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
const stageOf = (turns) => (turns < cfg.coldTurns ? 'cold' : turns < cfg.warmTurns ? 'warm' : 'regular');
const sectionText = (key) => {
  const sec = sections.find((x) => x.name === 'lingshu:' + key);
  return sec ? sec.text() : '';
};

// 每 4 轮一个"剧情"，覆盖目标/关系/工具的常见形态
const script = [
  '开始做 记忆插件 的验收',
  '进展不错，谢谢',
  '卡住了',
  '又卡住了',
  '开始做 关系模型的迁移',
  '完成了',
  '谢谢',
  '你错了，别再敷衍',
  '和好吧',
  '谢谢',
  '可以',
  '不错',
];

async function fire(ev, args) {
  for (const fn of hooks[ev] || []) await fn(...args);
}
const userEv = (text, turn) => ([{}, {
  type: 'user/message', seq: turn, time: Date.now(),
  data: { message: { role: 'user', content: [{ type: 'text', text }] } },
}]);
const preEv = (text, turn) => ({
  messages: [{ role: 'user', content: [{ type: 'text', text }] }], turn, step: 1,
});

const stages = [];
// 每个 turn 记录首 step / 第二 step 之后的 lastEvent.hits 与 injectionOrder，
// 用来钉住"同 turn 后续 step 不得污染召回与 U 型顺序"（控制论层回归点）。
const snapshots = [];
for (let turn = 1; turn <= TURNS; turn++) {
  const text = script[(turn - 1) % script.length];
  await fire('session/event', [{}, { type: 'turn/start', data: { turn } }]);
  await fire('agent/pre-step', [preEv(text, turn), () => Promise.resolve({ kind: 'enter', messages: [] })]);
  const after_first = readState();
  snapshots.push({ turn, phase: 'after-first',
    hits: after_first.lastEvent?.hits ?? [], order: after_first.control.injectionOrder ?? [] });
  await fire('agent/pre-step', [preEv('', turn), () => Promise.resolve({ kind: 'enter', messages: [] })]); // 同轮第二 step
  const after_second = readState();
  snapshots.push({ turn, phase: 'after-second',
    hits: after_second.lastEvent?.hits ?? [], order: after_second.control.injectionOrder ?? [] });
  await fire('session/event', [{}, { type: 'tool/result', data: { message: { toolName: 'read' } } }]);
  await fire('session/event', userEv(text, turn));
  stages.push(stageOf(readState().turns));
  await fire('session/event', [{}, { type: 'turn/end', data: { turn } }]);
}

const st = readState();
const mem = memLines();
const audit = auditLines();

// ── 断言 ────────────────────────────────────────────────
check('轮次累计正确', st.turns === TURNS, `${st.turns} vs ${TURNS}`);
check('阶段迁移 cold→warm→regular 齐全',
  stages[0] === 'cold' && stages[3] === 'warm' && stages[Math.min(20, TURNS - 1)] === 'regular',
  `${stages[0]}/${stages[3]}/${stages[Math.min(20, TURNS - 1)]}`);
check('阶段单调不回退',
  stages.every((s, i) => i === 0 || ['cold', 'warm', 'regular'].indexOf(s) >= ['cold', 'warm', 'regular'].indexOf(stages[i - 1])));
check('同轮第二次 pre-step 不再涨轮次', st.turns === TURNS);

const ids = mem.map((m) => m.id);
check('记忆 id 无重号', new Set(ids).size === ids.length, `${ids.length} 条`);
check('记忆 id 严格递增',
  ids.every((id, i) => i === 0 || Number(id.split('_')[1]) > Number(ids[i - 1].split('_')[1])));
check('记忆条目全部可追溯（有 sourceQuote）',
  mem.every((m) => String(m.sourceQuote || '').trim().length > 0),
  mem.filter((m) => !String(m.sourceQuote || '').trim()).length + ' 条缺引用');
check('记忆来源只有 goal/relation/tool 三类',
  [...new Set(mem.map((m) => m.origin))].every((o) => ['goal', 'relation', 'tool'].includes(o)),
  [...new Set(mem.map((m) => m.origin))].join(','));
check('目标经过新建与终止',
  Object.values(st.goal.goals).some((g) => g.status === 'done' || g.status === 'abandoned'),
  Object.values(st.goal.goals).map((g) => g.status).join(','));
check('关系推进到 mid 或更高', ['mid', 'high'].includes(st.relation.trustLevel), st.relation.trustLevel);
check('关系曾进入冲突', st.relation.stage !== 'new', st.relation.stage);
check('写请求全部落盘（stateWrites == 记忆条数）', st.stateWrites === mem.length,
  `${st.stateWrites} vs ${mem.length}`);
check('审计含 memory_write', audit.some((a) => a.kind === 'memory_write'));
check('审计无 hook_error', !audit.some((a) => a.kind === 'hook_error'),
  audit.filter((a) => a.kind === 'hook_error').length + ' 条');
check('审计无 section_error', !audit.some((a) => a.kind === 'section_error'));
check('工具被注册', tools.length === 1);
check('语气事件写进审计', audit.some((a) => a.kind === 'emotion'));
check('情感被激活（敌对/阻塞语气）', st.emotionActive === true, JSON.stringify(st.dev));
check('风格段注入表达状态', sectionText('style').includes('表达状态'), sectionText('style').slice(0, 30));
check('情感偏离在 [-1,1] 内',
  ['valence', 'arousal', 'dominance'].every((k) => Math.abs(Number(st.dev[k] || 0)) <= 1),
  JSON.stringify(st.dev));
check('情感只进风格段（事实段无情绪字样）',
  !sectionText('fact').includes('情绪'), sectionText('fact').slice(0, 30));

// ── 控制论层（v4.2）长跑断言 ────────────────────────────
const preSteps = audit.filter((a) => a.kind === 'pre_step');
const firstSteps = preSteps.filter((a) => a.firstStep === true);
const laterSteps = preSteps.filter((a) => a.firstStep === false);
const ctl = st.control || {};
const firstSnaps = snapshots.filter((s) => s.phase === 'after-first');
const secondSnaps = snapshots.filter((s) => s.phase === 'after-second');
const validIds = new Set(ids);
const sameList = (a, b) => JSON.stringify(a) === JSON.stringify(b);
const isPermutation = (s) => s.order.length === s.hits.length
  && sameList([...s.order].sort(), [...s.hits].sort());

check('段落注册为 7 段（含首尾锚点与熔断段）', sections.length === 7, String(sections.length));
check('段落 order 90/100/105/110/120/130/140',
  sections.map((s) => s.order).join(',') === '90,100,105,110,120,130,140',
  sections.map((s) => s.order).join(','));
check('每个 turn 都落盘首/非首 step 两条 pre_step 审计',
  firstSteps.length === TURNS && laterSteps.length === TURNS,
  `${firstSteps.length} first / ${laterSteps.length} later`);
check('审计无 preflight_block（40 轮全放行）',
  !audit.some((a) => a.kind === 'preflight_block'),
  audit.filter((a) => a.kind === 'preflight_block').length + ' 条');
check('每次 pre_step 审计 verdict=allow',
  preSteps.length === TURNS * 2 && preSteps.every((a) => a.verdict === 'allow'),
  preSteps.filter((a) => a.verdict !== 'allow').length + ' 条非 allow');
check('pre_step 审计带 phase/ratio/anchor/downgrades 观测字段',
  preSteps.every((a) => ['normal', 'shift', 'critical', 'unknown'].includes(a.phase)
    && typeof a.ratio === 'number' && typeof a.anchor === 'boolean'
    && Array.isArray(a.downgrades)));
check('state.control 结构完整（7 键）',
  ['phase', 'audit', 'blocked', 'integral', 'saturated', 'contextWindow', 'injectionOrder']
    .every((k) => k in ctl), Object.keys(ctl).join(','));
check('state.control.phase 六键齐全',
  ['ratio', 'phase', 'trend', 'prefeed', 'selfReferenceBoost', 'anchorRequired']
    .every((k) => k in (ctl.phase || {})), JSON.stringify(ctl.phase));
check('state.control.phase.phase 合法',
  ['normal', 'shift', 'critical', 'unknown'].includes(ctl.phase?.phase), String(ctl.phase?.phase));
check('state.control.integral 三维数值',
  ['valence', 'arousal', 'dominance'].every((k) => Number.isFinite(Number(ctl.integral?.[k]))),
  JSON.stringify(ctl.integral));
check('state.control.saturated 三维布尔',
  ['valence', 'arousal', 'dominance'].every((k) => typeof ctl.saturated?.[k] === 'boolean'),
  JSON.stringify(ctl.saturated));
check('state.control.audit failMode=closed 且 verdict=allow',
  ctl.audit?.failMode === 'closed' && ctl.audit?.verdict === 'allow', JSON.stringify(ctl.audit));
check('长跑未熔断（blocked=false）', ctl.blocked === false);
check('injectionOrder 是 lastEvent.hits 的置换（全部轮次）',
  snapshots.every(isPermutation),
  snapshots.filter((s) => !isPermutation(s)).length + ' 轮不满足');
check('injectionOrder/hits 元素都是合法 memory id（不是 null）',
  snapshots.every((s) => [...s.hits, ...s.order].every((id) => validIds.has(id))),
  JSON.stringify(snapshots.find((s) => [...s.hits, ...s.order].some((id) => !validIds.has(id))) || null));
check('同 turn 第二 step 不改变 hits/injectionOrder（多 step 不污染回归点）',
  firstSnaps.every((f, i) => secondSnaps[i] && sameList(secondSnaps[i].hits, f.hits)
    && sameList(secondSnaps[i].order, f.order)),
  firstSnaps.filter((f, i) => !secondSnaps[i] || !sameList(secondSnaps[i].hits, f.hits)).length + ' 轮被污染');
check('至少一轮召回非空（U 型顺序被真实数据覆盖）',
  firstSnaps.some((s) => s.hits.length > 0),
  firstSnaps.reduce((m, s) => Math.max(m, s.hits.length), 0) + ' 最大命中数');
check('最终落盘 injectionOrder 与 lastEvent.hits 同集合',
  sameList([...(ctl.injectionOrder || [])].sort(), [...(st.lastEvent?.hits || [])].sort()),
  `${JSON.stringify(ctl.injectionOrder)} vs ${JSON.stringify(st.lastEvent?.hits)}`);
check('40 轮长跑审计无 hook_error / section_error',
  !audit.some((a) => a.kind === 'hook_error' || a.kind === 'section_error'));


// ── 拒绝规则（直接测 writer）─────────────────────────────
const rejected = applyWriteRequests([{ origin: 'goal', items: [
  { type: 'episodic', subject: 'x', relation: 'y', object: 'z', content: '无引用', importance: 0.5, sourceQuote: '' },
  { type: 'bogus', subject: 'x', relation: 'y', object: 'z', content: '错类型', importance: 0.5, sourceQuote: 'q' },
  { type: 'episodic', subject: 'x', relation: 'y', object: 'z', content: '坏重要度', importance: 9, sourceQuote: 'q' },
] }], { read: () => mem, append: () => {} });
check('缺引用被拒', rejected.rejected.some((r) => r.reason.includes('missing_source_quote')));
check('错类型被拒', rejected.rejected.some((r) => r.reason.includes('bad_type')));
check('坏重要度被拒', rejected.rejected.some((r) => r.reason.includes('bad_importance')));
check('全被拒时不写任何一条', rejected.written === 0);

// ── 批量去重 ────────────────────────────────────────────
const dup = applyWriteRequests([{ origin: 'goal', items: [
  { type: 'episodic', subject: 'a', relation: 'b', object: 'c', content: '1', importance: 0.5, sourceQuote: 'q' },
  { type: 'episodic', subject: 'a', relation: 'b', object: 'c', content: '2', importance: 0.5, sourceQuote: 'q' },
] }], { read: () => mem, append: () => {} });
check('同批同三元组只写一条', dup.written === 1 && dup.rejected.some((r) => r.reason === 'duplicate_in_batch'),
  `${dup.written} written`);

const failed = results.filter((r) => !r.cond);
if (!ASSERT_ONLY) {
  console.log(`\n${results.length - failed.length} passed, ${failed.length} failed`);
  console.log(`轮次 ${TURNS} ｜ 记忆 ${mem.length} 条 ｜ 目标 ${Object.keys(st.goal.goals).length} 个 ｜ 关系 ${st.relation.trustLevel}/${st.relation.stage}`);
}
if (failed.length) {
  if (ASSERT_ONLY) console.error(failed.map((f) => 'FAIL: ' + f.label).join('\n'));
  process.exit(1);
}
process.exit(0);
