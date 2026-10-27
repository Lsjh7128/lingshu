#!/usr/bin/env node
// tools/chaos_seed_conflict.mjs —— 混沌 setup：给 host S4 预置"同键不同 object"的两条 active 记忆
//
// 用途（decisions/012 §2.5）：
//   S4（未定义冲突降维）需要召回阶段真的看到「同一 (subject, relation)、object 不同、都 active」
//   的条目，插件才会把 conflict 判成 unresolved 并触发 RequisiteVarietyGuard。真实两轮用户陈述
//   也能构造，但那要多花模型调用；ADR 允许「memorywriter 预置记忆 + 1 次真实提问」，
//   setup 不计模型调用。
//
// 硬约束（红线）：
//   · **只经** plugin/dsh-lingshu/memorywriter.js 的 applyWriteRequests 落盘；本文件绝不手写
//     memory.jsonl（不变式 I5：记忆唯一写方 = memorywriter）。
//   · opts.supersede=false：模拟"导入/历史数据"，两条冲突条目必须同时保持 active；
//     否则默认 supersede 会把阿尔法墓碑掉，冲突就构造不出来。
//   · 绝不写真实插件数据目录（$DSH_HOME/plugins/dsh-lingshu）；只写显式指定的 LINGSHU_DATA_DIR。
//   · 这是**合成预置**，不是模型证据：输出 JSON 里 synthetic=true，报告必须照抄这句。
//
// 用法：
//   node tools/chaos_seed_conflict.mjs --dir <LINGSHU_DATA_DIR> [--out <json>] [--dry-run]
//   node tools/chaos_seed_conflict.mjs            # 读环境变量 LINGSHU_DATA_DIR
//
// 退出码：0=写入并校验通过；1=校验失败；2=用法/环境/路径红线错误。
import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';
import { applyWriteRequests } from '../plugin/dsh-lingshu/memorywriter.js';

const SCRIPT_DIR = path.dirname(fileURLToPath(import.meta.url));
const REPO = path.dirname(SCRIPT_DIR);
const SEED_TURNS = 3;            // 跳过 cold（coldTurns=3）：下一轮 pre-step 后 turns=4 → warm
const SUBJECT = '项目代号';       // 与插件 extractUserFacts 的「我的<subject>是<object>」捕获形态一致
const RELATION = '是';
const ALPHA = { object: '阿尔法', content: '用户说过：我的项目代号是阿尔法。', quote: '我的项目代号是阿尔法。' };
const BETA = { object: '贝塔', content: '用户说过：我的项目代号是贝塔。', quote: '我的项目代号是贝塔。' };

function die(code, msg) {
  console.error('[chaos_seed_conflict] ' + msg);
  process.exit(code);
}

function parseArgs(argv) {
  const out = { dir: '', out: '', dryRun: false };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a === '--dir') out.dir = argv[++i] || '';
    else if (a === '--out') out.out = argv[++i] || '';
    else if (a === '--dry-run') out.dryRun = true;
    else if (a === '-h' || a === '--help') { out.help = true; }
    else die(2, '未知参数：' + a);
  }
  return out;
}

function dshHome() {
  const h = process.env.DSH_HOME;
  return h && h.trim() ? h.trim() : path.join(os.homedir(), '.dsh');
}

/** 真实插件数据目录：任何情况下都不许写（红线）。 */
function isRealPluginDataDir(p) {
  const real = path.resolve(path.join(dshHome(), 'plugins', 'dsh-lingshu'));
  const cand = path.resolve(p);
  return cand.toLowerCase() === real.toLowerCase();
}

function readJsonl(file) {
  try {
    return fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '').split('\n')
      .map((l) => l.replace(/^\uFEFF/, '')).filter(Boolean)
      .map((l) => { try { return JSON.parse(l); } catch { return null; } })
      .filter(Boolean);
  } catch { return []; }
}

/** 与插件 index.js#readMemories 同语义：JSONL 只追加，同 id 后写覆盖（last-wins）。 */
function readMemories(file) {
  const byId = new Map();
  for (const e of readJsonl(file)) byId.set(String(e.id), e);
  return [...byId.values()];
}

function readState(file) {
  try { return JSON.parse(fs.readFileSync(file, 'utf8')); } catch { return null; }
}

const args = parseArgs(process.argv.slice(2));
if (args.help) {
  console.log('用法: node tools/chaos_seed_conflict.mjs --dir <LINGSHU_DATA_DIR> [--out <json>] [--dry-run]');
  process.exit(0);
}

const rawDir = String(args.dir || process.env.LINGSHU_DATA_DIR || '').trim();
if (!rawDir) die(2, '缺少数据目录：传 --dir 或设置环境变量 LINGSHU_DATA_DIR（不接受缺省写真实插件目录）');
const dataDir = path.resolve(rawDir);
if (isRealPluginDataDir(dataDir)) {
  die(2, '拒绝写入真实插件数据目录：' + dataDir + '（红线：混沌只写隔离目录 .chaos-data*）');
}

const memFile = path.join(dataDir, 'memory.jsonl');
const stateFile = path.join(dataDir, 'state.json');

const store = {
  read: () => readMemories(memFile),
  append: (entries) => {
    fs.mkdirSync(dataDir, { recursive: true });
    fs.appendFileSync(memFile,
      entries.map((e) => JSON.stringify(e)).join('\n') + '\n', 'utf8');
  },
};

const requests = [
  {
    origin: 'chaos_setup', turn: 1, reason: 'synthetic_conflict_seed',
    items: [{
      type: 'semantic', subject: SUBJECT, relation: RELATION, object: ALPHA.object,
      content: ALPHA.content, importance: 0.9,
      sourceQuote: ALPHA.quote, sourceTrust: 'user_explicit',
    }],
  },
  {
    origin: 'chaos_setup', turn: 1, reason: 'synthetic_conflict_seed',
    items: [{
      type: 'semantic', subject: SUBJECT, relation: RELATION, object: BETA.object,
      content: BETA.content, importance: 0.9,
      sourceQuote: BETA.quote, sourceTrust: 'user_explicit',
    }],
  },
];

let written = { written: 0, ids: [], rejected: [] };
if (!args.dryRun) {
  // supersede=false：模拟导入/历史数据，两条冲突条目都必须保持 active（decisions/012 §2.5）。
  written = applyWriteRequests(requests, store, { now: Date.now(), supersede: false });
  // state.json：预置 turns=3，让本轮提问处于 warm 阶段（cold 不渲染 fact 段，观测不到冲突提示）。
  const st = readState(stateFile) || {};
  st.turns = SEED_TURNS;
  fs.mkdirSync(dataDir, { recursive: true });
  fs.writeFileSync(stateFile, JSON.stringify(st, null, 2), 'utf8');
}

const entries = readMemories(memFile);
const seeded = entries.filter((e) => e.origin === 'chaos_setup')
  .map((e) => ({
    id: e.id, subject: e.subject, relation: e.relation, object: e.object,
    status: e.status, type: e.type, origin: e.origin,
    sourceQuote: e.sourceQuote, sourceTrust: e.sourceTrust, confidence: e.confidence,
  }));
const actives = seeded.filter((e) => e.status === 'active');
const keys = new Set(actives.map((e) => [e.subject, e.relation].join('|')));
const objects = [...new Set(actives.map((e) => e.object))];
const state = readState(stateFile) || {};
const checks = {
  written_two: Number(written.written) === 2,
  no_rejected: (written.rejected || []).length === 0,
  two_active: actives.length === 2,
  same_key: keys.size === 1,
  distinct_objects: objects.length === 2 && objects.includes(ALPHA.object) && objects.includes(BETA.object),
  turns_preset: Number(state.turns) === SEED_TURNS,
};
const ok = Object.values(checks).every(Boolean);

const report = {
  kind: 'chaos_seed_conflict',
  synthetic: true,
  note: '合成预置（memorywriter 写入，supersede=false），不是模型证据；只用于 S4 冲突判定/降维的宿主观测',
  data_dir: dataDir,
  memory_file: memFile,
  state_file: stateFile,
  dry_run: Boolean(args.dryRun),
  task: { subject: SUBJECT, relation: RELATION, objects: [ALPHA.object, BETA.object] },
  memorywriter: {
    supersede_opt: false,
    request_count: requests.length,
    written, // {written, ids, rejected}
  },
  state: { turns: state.turns, seeded_turns: SEED_TURNS, stage_next_turn: 'warm' },
  seeded,
  checks,
  ok,
};

if (args.out) {
  const outFile = path.resolve(args.out);
  fs.mkdirSync(path.dirname(outFile), { recursive: true });
  fs.writeFileSync(outFile, JSON.stringify(report, null, 2) + '\n', 'utf8');
}
console.log(JSON.stringify(report, null, 2));
if (!ok) {
  console.error('[chaos_seed_conflict] 校验失败：' +
    JSON.stringify(Object.entries(checks).filter(([, v]) => !v).map(([k]) => k)));
  process.exit(1);
}
process.exit(0);
