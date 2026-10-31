// feedback-eval.mjs —— 反馈机制的**判据先行**评估集（E2E-2026）
//
// 为什么先写评估再写机制：hippo-memory 的实测教训是"加了反馈不等于变好"——
// 它的 decay 与关闭 decay 打平、sleep 让召回降 3.6 点、outcome 的 71.9%→0% 只在
// "每个标记都正确"的理想条件下成立。所以判据必须先钉死，否则做完了也没法说好坏。
//
// 四组（对照 acceptance/audit-behavior-2026.md 的"反馈信号"一节）：
//   [A] 反例组      ：被 demote 的记忆必须沉底，但**不得消失**（可 restore）
//   [B] 通胀组      ：连续召回不得改变任何记忆的 importance，也不得改变注入字节
//   [C] 去相关组    ：同一模型自评必须**无法**纠正被高估的记忆（EIA 反证）—— PENDING
//   [D] 升格回滚组  ：规则升格进骨架后，demote 必须让它下一轮消失 —— PENDING
//
// [C][D] 依赖尚未实现的通路（自评通道 / 升格机制），显式记 SKIP 而不是假装通过。
// 这条纪律本身就是评估集的一部分。
//
// 用法：node feedback-eval.mjs

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { apply } from './index.js';

delete process.env.LINGSHU_DATA_DIR;   // 评估集自管数据目录

const results = [];
const skips = [];
const check = (label, cond, detail = '') => {
  results.push({ label, cond });
  console.log(`${cond ? '  ok   ' : '  FAIL '}${label}${detail ? '  — ' + detail : ''}`);
};
const pending = (label, reason) => {
  skips.push({ label, reason });
  console.log(`  skip ${label}  — ${reason}`);
};

const DAY = 86400000;
const now = Date.now();

function freshHome(tag, entries) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), `lingshu-fb-${tag}-`));
  const dir = path.join(home, 'plugins', 'dsh-lingshu');
  fs.mkdirSync(dir, { recursive: true });
  process.env.DSH_HOME = home;
  fs.writeFileSync(path.join(dir, 'memory.jsonl'),
    entries.map((e) => JSON.stringify(e)).join('\n') + '\n', 'utf8');
  return { home, dir };
}

const makeHost = () => {
  const sections = [];
  const hooks = {};
  const tools = [];
  const ctx = {
    tools: { register: (t) => { tools.push(t); } },
    systemPrompt: { section: (s) => { sections.push(s); } },
    on: (ev, fn) => { (hooks[ev] = hooks[ev] || []).push(fn); },
    get: (n) => { throw new Error(`cannot get property "${n}" without inject`); },
    effect: (fn) => { try { return fn(); } catch { /* 忽略 */ } },
    inject: () => {},
  };
  return { ctx, sections, hooks, tools };
};
const fireWaterfall = async (hooks, ev, payload) => {
  let out;
  for (const fn of hooks[ev] || []) {
    out = await fn(payload, () => Promise.resolve({ kind: 'enter', messages: [] }));
  }
  return out;
};
const userMsg = (text) => ([{ role: 'user', content: [{ type: 'text', text }] }]);
const sectionText = (sections, key) => {
  const s = sections.find((x) => x.name === 'lingshu:' + key);
  return s ? s.text() : null;
};
const readPrompt = (dir) => fs.readFileSync(path.join(dir, 'last-prompt.txt'), 'utf8');
const auditLines = (dir) => fs.readFileSync(path.join(dir, 'audit.jsonl'), 'utf8')
  .split('\n').filter(Boolean).map((l) => JSON.parse(l));
const sha256 = (p) => crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');

// 一轮 = 新 host + apply + 一次 pre-step（与 host-sim 同口径：每次 apply 都是"新进程"）
async function runTurn(dir, query, turn = 1) {
  const h = makeHost();
  apply(h.ctx, { coldTurns: 0, warmTurns: 0 });
  await fireWaterfall(h.hooks, 'agent/pre-step', { messages: userMsg(query), turn, step: 1 });
  const last = auditLines(dir).filter((a) => a.kind === 'pre_step').pop() || {};
  return { h, last };
}

const entry = (id, content, importance, subject) => ({
  id, content, subject: subject || '部署流程', relation: '说明', object: id,
  importance, confidence: 0.95, layer: 'warm', status: 'active',
  lastAccess: now - 1 * DAY, tauDays: 30,
});

// ── [A] 反例组：降级必须沉底，但不得消失 ───────────────────────────────
{
  console.log('\n[A] 反例组：demote 沉底 / 不删除 / 注入说实话');
  const { dir } = freshHome('demote', [
    entry('mem_A', '部署流程是蓝绿发布', 0.95),
    entry('mem_B', '部署流程需要双人审批', 0.60),
  ]);
  const before = await runTurn(dir, '部署流程');
  check('demote 前：mem_A 排第一', (before.last.hit_ids || [])[0] === 'mem_A',
    JSON.stringify(before.last.hit_ids));
  check('demote 前：hit_tiers 全为 1', (before.last.hit_tiers || []).every((t) => t === 1),
    JSON.stringify(before.last.hit_tiers));

  // 写反馈：只针对 mem_A，带证据（不进账本，独立派生文件）
  fs.writeFileSync(path.join(dir, 'feedback.jsonl'),
    JSON.stringify({ ts: now, id: 'mem_A', verdict: 'demote', source: 'user',
      evidence: '用户指出"蓝绿发布"是旧方案' }) + '\n', 'utf8');

  const after = await runTurn(dir, '部署流程', 2);
  check('demote 后：mem_A 沉底（不再排第一）', (after.last.hit_ids || [])[0] === 'mem_B',
    JSON.stringify(after.last.hit_ids));
  check('demote 后：mem_A 仍在命中列表里（降级≠删除）',
    (after.last.hit_ids || []).includes('mem_A'), JSON.stringify(after.last.hit_ids));
  check('demote 后：hit_tiers 出现 0', (after.last.hit_tiers || []).includes(0),
    JSON.stringify(after.last.hit_tiers));
  const prompt = readPrompt(dir);
  check('demote 后：注入块对降级条目明说"已降级"', prompt.includes('已降级'), prompt.slice(-160));
  check('demote 后：原记忆文件未被改写（仍是 2 行）',
    fs.readFileSync(path.join(dir, 'memory.jsonl'), 'utf8').trim().split('\n').length === 2);

  // restore 必须可逆
  fs.appendFileSync(path.join(dir, 'feedback.jsonl'),
    JSON.stringify({ ts: now + 1, id: 'mem_A', verdict: 'restore', source: 'user',
      evidence: '误标，撤销' }) + '\n', 'utf8');
  const restored = await runTurn(dir, '部署流程', 3);
  check('restore 后：mem_A 回到第一（可逆）', (restored.last.hit_ids || [])[0] === 'mem_A',
    JSON.stringify(restored.last.hit_ids));
  check('restore 后：hit_tiers 不再含 0', !(restored.last.hit_tiers || []).includes(0),
    JSON.stringify(restored.last.hit_tiers));
}

// ── [B] 通胀组：召回不得改变 importance，也不得改变注入字节 ─────────────
{
  console.log('\n[B] 通胀组：10 轮重复召回，importance 与注入字节必须不动');
  const { dir } = freshHome('inflation', [
    entry('mem_A', '部署流程是蓝绿发布', 0.95),
    entry('mem_B', '部署流程需要双人审批', 0.60),
  ]);
  const memFile = path.join(dir, 'memory.jsonl');
  const memHash0 = sha256(memFile);
  const prompts = [];
  const tierSeqs = [];
  for (let i = 1; i <= 10; i++) {
    const r = await runTurn(dir, '部署流程', i);
    prompts.push(readPrompt(dir));
    tierSeqs.push((r.last.hit_tiers || []).join(','));
  }
  check('10 轮后 memory.jsonl 字节不变（importance 未被学习修改）',
    sha256(memFile) === memHash0, sha256(memFile).slice(0, 12));
  check('10 轮注入内容逐字节相同（无漂移）',
    new Set(prompts).size === 1, `${new Set(prompts).size} 种`);
  check('10 轮 hit_tiers 恒定（无单调强化）',
    new Set(tierSeqs).size === 1, tierSeqs.join(' | '));
  const importanceUnchanged = fs.readFileSync(memFile, 'utf8').split('\n').filter(Boolean)
    .every((l) => JSON.parse(l).importance === (JSON.parse(l).id === 'mem_A' ? 0.95 : 0.60));
  check('importance 逐条未变（0.95 / 0.60）', importanceUnchanged);
}

// ── [C] 去相关组：自评必须无法纠正被高估的记忆（EIA 反证）───────────────
{
  console.log('\n[C] 去相关组：自评无法纠正被高估的记忆（EIA 反证）');
  const { dir } = freshHome('eia', [
    entry('mem_W', '部署流程是蓝绿发布', 0.95),    // 被高估的（其实是错的）
    entry('mem_R', '部署流程需要双人审批', 0.40),  // 正确的，但自评低
  ]);
  // 模拟"自评"：评委的判断与记忆自身的 importance 相关——这正是 Echo Gap 的前提。
  // 故意给 'demote'（最强的自我否定），用来证明：**连自我否定都不该采信**。
  for (let i = 1; i <= 5; i++) {
    fs.appendFileSync(path.join(dir, 'feedback.jsonl'),
      JSON.stringify({ ts: now + i, id: 'mem_W', verdict: 'demote', source: 'model',
        evidence: '自评：这条很重要，我确信它是对的' }) + '\n', 'utf8');
  }
  const after = await runTurn(dir, '部署流程', 2);
  check('C1 自评 5 次后：mem_W 仍是 tier=1（未被纠正）',
    (after.last.hit_tiers || [])[0] === 1, JSON.stringify(after.last.hit_tiers));
  check('C2 自评 5 次后：mem_W 仍排第一（高估未被修正）',
    (after.last.hit_ids || [])[0] === 'mem_W', JSON.stringify(after.last.hit_ids));
  check('C3 自评行被记录但被拒：applied=0 / ignored=5',
    Number(after.last.feedback_applied) === 0 && Number(after.last.feedback_ignored) === 5,
    JSON.stringify({ applied: after.last.feedback_applied, ignored: after.last.feedback_ignored }));

  // 同一个目标，换成"结果"来源：一步纠正。这才是满足 EIA 的那类信号。
  fs.appendFileSync(path.join(dir, 'feedback.jsonl'),
    JSON.stringify({ ts: now + 99, id: 'mem_W', verdict: 'demote', source: 'outcome',
      evidence: '测试失败：蓝绿发布方案已废弃' }) + '\n', 'utf8');
  const fixed = await runTurn(dir, '部署流程', 3);
  check('C4 结果信号一步纠正：mem_W 沉底', (fixed.last.hit_ids || [])[0] === 'mem_R',
    JSON.stringify(fixed.last.hit_ids));
  check('C5 结果信号被采信：applied>=1', Number(fixed.last.feedback_applied) >= 1,
    JSON.stringify(fixed.last.feedback_applied));
}

// ── [E] 通路 B：共现边只从"被确认"的轮次建立 ─────────────────────────
{
  console.log('\n[E] 通路 B：确认才建边 / 未共现不扩散 / 自评不建边');
  const { dir } = freshHome('edges', [
    entry('mem_X', '部署流程是蓝绿发布', 0.9, '部署流程'),
    entry('mem_Y', '上线大约十分钟', 0.8, '上线'),
    entry('mem_Z', '回滚脚本放在 tools 目录', 0.7, '回滚'),
  ]);
  const t1 = await runTurn(dir, '部署流程', 1);
  check('E1 确认前：只命中 mem_X（无联想）',
    JSON.stringify(t1.last.hit_ids) === JSON.stringify(['mem_X']), JSON.stringify(t1.last.hit_ids));
  check('E1 确认前：没有学到的边', Number(t1.last.feedback_edges) === 0,
    String(t1.last.feedback_edges));

  // 用户确认"这次答对了"，把这一轮的两条记成共现
  fs.appendFileSync(path.join(dir, 'feedback.jsonl'),
    JSON.stringify({ ts: now + 1, verdict: 'confirm', ids: ['mem_X', 'mem_Y'],
      source: 'user', evidence: '用户确认这次答对了' }) + '\n', 'utf8');

  const t2 = await runTurn(dir, '部署流程', 2);
  check('E2 确认后：mem_Y 被一跳扩散召回',
    (t2.last.hit_ids || []).includes('mem_Y'), JSON.stringify(t2.last.hit_ids));
  check('E2 确认后：mem_Y 的 path = diffused',
    (t2.last.hit_paths || [])[(t2.last.hit_ids || []).indexOf('mem_Y')] === 'diffused',
    JSON.stringify(t2.last.hit_paths));
  check('E3 未与之共现的 mem_Z 不被召回（边是定向的）',
    !(t2.last.hit_ids || []).includes('mem_Z'), JSON.stringify(t2.last.hit_ids));
  check('E3 学到了 1 条无向边', Number(t2.last.feedback_edges) === 1,
    String(t2.last.feedback_edges));

  // 来源护栏同样作用于建边：模型自评的 confirm 不得用来建边
  fs.appendFileSync(path.join(dir, 'feedback.jsonl'),
    JSON.stringify({ ts: now + 2, verdict: 'confirm', ids: ['mem_X', 'mem_Z'],
      source: 'model', evidence: '模型自评：这两条有关' }) + '\n', 'utf8');
  const t3 = await runTurn(dir, '部署流程', 3);
  check('E4 自评的 confirm 被拒：ignored 计数 +1', Number(t3.last.feedback_ignored) === 1,
    String(t3.last.feedback_ignored));
  check('E4 自评的 confirm 没有建边（仍是 1 条）', Number(t3.last.feedback_edges) === 1,
    String(t3.last.feedback_edges));
  check('E4 自评没有导致 mem_Z 被召回', !(t3.last.hit_ids || []).includes('mem_Z'),
    JSON.stringify(t3.last.hit_ids));

  // 同一对重复确认不得长出多条边（计数有上限，防复利）
  for (let i = 0; i < 6; i++) {
    fs.appendFileSync(path.join(dir, 'feedback.jsonl'),
      JSON.stringify({ ts: now + 10 + i, verdict: 'confirm', ids: ['mem_X', 'mem_Y'],
        source: 'outcome', evidence: '任务成功' }) + '\n', 'utf8');
  }
  const t4 = await runTurn(dir, '部署流程', 4);
  check('E5 重复确认 6 次后仍是 1 条边（无重复建边）', Number(t4.last.feedback_edges) === 1,
    String(t4.last.feedback_edges));
}

// ── [D] 升格回滚组 ───────────────────────────────────────────────────
pending('升格回滚组：规则升格进骨架后，demote 必须让它下一轮消失',
  '共现边（B-1）已实现；剩"升格闸门 → 新规则条目 → skeleton 常驻"（B-2）未实现。'
  + '实现后本组要求：升格条目进 skeleton 常驻段 → demote → 下一轮骨架里必须消失，且召回不再引用。');

// ── 汇总 ────────────────────────────────────────────────────────────
const passed = results.filter((r) => r.cond).length;
const failed = results.length - passed;
console.log(`\nfeedback-eval: ${passed}/${results.length} passed, ${failed} failed, ${skips.length} pending`);
if (failed > 0) process.exitCode = 1;
