// eval-agent-state.mjs —— 目标/关系规则桩的误判率（**直接调用被测实现**）
//
// 为什么要用 Node 而不是 Python：真正运行的是 `goalrelation.js`（插件侧），
// 不是 `engine/goal.py`。评测必须打在被测对象上。
//
// 第一版评测器在 Python 里**复制了一份规则逻辑**，还给 relation 加了
// "没匹配就当 positive" 的缺省，结果测出 100% 误报率——测的是评测器，不是插件。
// 这类"评测器与被测实现分叉"是评测里最贵的错误，比指标难看严重得多。
//
// 用法：node eval-agent-state.mjs [--show-miss]

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { GOAL_STATE_EMPTY, RELATION_STATE_EMPTY, updateGoals, updateRelation }
  from './goalrelation.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const SET = path.join(HERE, '..', '..', 'eval-set', 'agent_state-v1.jsonl');
const SHOW = process.argv.includes('--show-miss');

const cases = fs.readFileSync(SET, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));

function predict(c) {
  const gs = GOAL_STATE_EMPTY();
  const rs = RELATION_STATE_EMPTY();
  // 先放一个占位目标，让 obstruction/progress 有归属（模拟"已有在办目标"）
  updateGoals(gs, '开始做 占位任务');
  const g = updateGoals(gs, c.text, { turn: 1 });
  const r = updateRelation(rs, c.text);
  return {
    goal: g.events.length ? g.events[0].event : null,
    relation: r.events.length ? r.events[0].type : null,
  };
}

const preds = new Map(cases.map((c) => [c.id, predict(c)]));

function score(labelKey, posValue) {
  let tp = 0, fp = 0, fn = 0, tn = 0;
  const misses = [];
  for (const c of cases) {
    const want = c[labelKey] === posValue;
    const got = preds.get(c.id)[labelKey === 'goal' ? 'goal' : 'relation'] === posValue;
    if (want && got) tp++;
    else if (!want && got) { fp++; misses.push(['FP', c.id, c.text, c[labelKey]]); }
    else if (want && !got) { fn++; misses.push(['FN', c.id, c.text, c[labelKey]]); }
    else tn++;
  }
  const p = tp + fp ? tp / (tp + fp) : 0;
  const r = tp + fn ? tp / (tp + fn) : 0;
  return { tp, fp, fn, tn, p, r, f1: p + r ? (2 * p * r) / (p + r) : 0, misses };
}

const rows = [
  ['目标·新建', score('goal', 'new')],
  ['目标·受阻', score('goal', 'obstruction')],
  ['关系·正向', score('relation', 'positive')],
  ['关系·冲突', score('relation', 'conflict')],
  ['关系·修复', score('relation', 'repair')],
];

console.log(`目标/关系规则桩 · 误判率（样本 ${cases.length} 条，直接调用 goalrelation.js）\n`);
console.log('类别        TP  FP  FN  TN   precision  recall     F1');
console.log('-'.repeat(60));
for (const [name, s] of rows) {
  console.log(
    `${name.padEnd(10)} ${String(s.tp).padStart(3)} ${String(s.fp).padStart(3)} `
    + `${String(s.fn).padStart(3)} ${String(s.tn).padStart(3)}   `
    + `${s.p.toFixed(3).padStart(9)} ${s.r.toFixed(3).padStart(7)} ${s.f1.toFixed(3).padStart(6)}`);
}

if (SHOW) {
  console.log('\n误判明细：');
  for (const [name, s] of rows) {
    for (const [kind, id, text, gold] of s.misses) {
      console.log(`  ${name.padEnd(10)} ${kind} ${id}  标注=${gold ?? 'null'}  「${text}」`);
    }
  }
}

const neg = cases.filter((c) => !c.goal && !c.relation);
const spoke = neg.filter((c) => preds.get(c.id).goal || preds.get(c.id).relation);
console.log(`\n负样本 ${neg.length} 条，误开口 ${spoke.length} 条（误报率 ${(100 * spoke.length / Math.max(1, neg.length)).toFixed(1)}%）`);
if (SHOW && spoke.length) {
  for (const c of spoke) {
    const p = preds.get(c.id);
    console.log(`  ${c.id}  「${c.text}」 → goal=${p.goal} relation=${p.relation}`);
  }
}
