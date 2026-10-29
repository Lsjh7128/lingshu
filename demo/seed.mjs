#!/usr/bin/env node
// demo/seed.mjs — 沙盒预置数据：管家人格体「阿福」的合成记忆 + 越过冷启动的轮次预置。
//
// 红线（与 tools/chaos_seed_conflict.mjs 同口径）：
//   · 只经 plugin/dsh-lingshu/memorywriter.js 的 applyWriteRequests 落盘（I5：记忆唯一写方）；
//   · 绝不写真实插件数据目录 $DSH_HOME/plugins/dsh-lingshu；
//   · 全部数据标注 synthetic —— 这些不是"用户真实流量"，不能进任何标定/证据口径。
//
// 用法：
//   node demo/seed.mjs                       # 写 ./.lingshu-demo-data（默认，走隔离断言）
//   node demo/seed.mjs --dir <隔离目录>       # 测试/校验用（仍拒绝真实插件目录）
//   node demo/seed.mjs --force               # 已有 demo_seed 记忆时仍然再写（默认跳过）
//   node demo/seed.mjs --json                # 机器可读输出
//
// 退出码：0 = 写入或幂等跳过；2 = 用法/路径红线错误；1 = 校验失败。

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { applyWriteRequests } from '../plugin/dsh-lingshu/memorywriter.js';
import {
  DATA_DIR, assertNotRealPluginDataDir, ensureDir, readJson, readMemories,
} from './lib.mjs';

/** 预置 3 轮"历史"，让下一次对话处于 warm 阶段（coldTurns=3 之前不注入记忆）。 */
export const SEED_TURNS = 3;

/**
 * 合成记忆条目。每条都带 sourceQuote（memorywriter 的硬校验），origin 由 request 统一标记。
 * subject/relation 两两不同 → 不会触发"同键不同 object"的冲突降维（那是另一个演示项）。
 */
export const SEED_ITEMS = [
  {
    type: 'semantic',
    subject: '项目',
    relation: '代号',
    object: 'ALPHA',
    content: '用户说过：当前项目代号是 ALPHA。',
    importance: 0.95,
    sourceQuote: '（演示预置）用户：我们的项目代号是 ALPHA。',
  },
  {
    type: 'semantic',
    subject: '项目',
    relation: '当前阶段',
    object: '沙盒 demo 开发中',
    content: '用户说过：现在在做 LINGSHU 的沙盒 demo。',
    importance: 0.85,
    sourceQuote: '（演示预置）用户：现在先做沙盒 demo。',
  },
  {
    type: 'semantic',
    subject: '项目',
    relation: '技术栈',
    object: 'Python + Node',
    content: '用户说过：项目主要用 Python 和 Node。',
    importance: 0.7,
    sourceQuote: '（演示预置）用户：我们主要用 Python 和 Node。',
  },
  {
    type: 'semantic',
    subject: '用户',
    relation: '称呼偏好',
    object: '老板',
    content: '用户说过：叫「老板」就行。',
    importance: 0.8,
    sourceQuote: '（演示预置）用户：以后叫我老板就行。',
  },
  {
    type: 'semantic',
    subject: '用户',
    relation: '雷区',
    object: '空话和客服腔',
    content: '用户说过：最烦空话和客服腔。',
    importance: 0.9,
    sourceQuote: '（演示预置）用户：我最烦空话和客服腔。',
  },
  {
    type: 'episodic',
    subject: '上次约定',
    relation: '待办',
    object: '周五前交一版能跑的 demo',
    content: '用户说过：周五前想看到一版能跑起来的 demo。',
    importance: 0.75,
    sourceQuote: '（演示预置）用户：周五前给我一版能跑的 demo。',
  },
];

/** 预置数据记录文件（放在数据目录内，说明"这些是合成数据"）。 */
export const SEED_RECORD = 'demo-seed.json';

function log(quiet, line) {
  if (!quiet) console.log(line);
}

/**
 * 写入预置数据（幂等：已有 origin=demo_seed 的记忆时默认跳过）。
 * @param {{ dataDir?: string, quiet?: boolean, force?: boolean }} [opts]
 * @returns {{ seeded: number, skipped: boolean, ids: string[], rejected: object[], turns: number, dataDir: string }}
 */
export function seedDemoData({ dataDir = DATA_DIR, quiet = false, force = false } = {}) {
  const dir = path.resolve(assertNotRealPluginDataDir(dataDir));
  ensureDir(dir);
  const memFile = path.join(dir, 'memory.jsonl');
  const stateFile = path.join(dir, 'state.json');

  const existing = readMemories(dir);
  const alreadySeeded = existing.some((e) => String(e.origin || '') === 'demo_seed');
  if (alreadySeeded && !force) {
    const record = readJson(path.join(dir, SEED_RECORD)) || {};
    log(quiet, '  [seed] 已存在 demo_seed 预置记忆，跳过写入（--force 可强制追加）');
    return { seeded: 0, skipped: true, ids: Array.isArray(record.ids) ? record.ids : [], rejected: [],
      turns: Number((readJson(stateFile) || {}).turns || 0), dataDir: dir };
  }

  const store = {
    read: () => readMemories(dir),
    append: (entries) => {
      ensureDir(dir);
      fs.appendFileSync(memFile, entries.map((e) => JSON.stringify(e)).join('\n') + '\n', 'utf8');
    },
  };

  const requests = [{
    origin: 'demo_seed',
    turn: SEED_TURNS,
    reason: 'sandbox_demo_preset',
    // 预置数据不再叠加 supersede：这些是"演示用历史"，不是用户真实修改。
    supersede: false,
    // sourceTrust 显式标 synthetic：原文来自演示脚本，不是真实用户陈述，不能当"用户明示"的证据。
    items: SEED_ITEMS.map((item) => ({ ...item, sourceTrust: 'synthetic_demo' })),
  }];

  const written = applyWriteRequests(requests, store, { now: Date.now(), supersede: false });

  // 轮次预置：下一次 pre-step 的 turn 从 4 开始（warm），否则冷启动 3 轮内记忆不注入。
  const state = readJson(stateFile) || {};
  const prevTurns = Number(state.turns || 0);
  state.turns = Math.max(prevTurns, SEED_TURNS);
  fs.writeFileSync(stateFile, JSON.stringify(state, null, 2) + '\n', 'utf8');

  const seededEntries = readMemories(dir).filter((e) => String(e.origin || '') === 'demo_seed');
  const record = {
    generator: 'demo/seed.mjs',
    synthetic: true,
    note: '沙盒演示预置数据：由脚本合成的"虚假记忆"，不是真实对话流量，不得进入任何标定或证据口径。',
    persona: '阿福（管家人格体，定义在 demo/cordis.demo.yml）',
    turns_seeded: SEED_TURNS,
    memory_file: 'memory.jsonl',
    state_file: 'state.json',
    ids: seededEntries.map((e) => e.id),
    items: seededEntries.map((e) => ({
      id: e.id, type: e.type, subject: e.subject, relation: e.relation, object: e.object,
      origin: e.origin, status: e.status, sourceQuote: e.sourceQuote,
    })),
    memorywriter: { written: written.written, rejected: written.rejected },
  };
  fs.writeFileSync(path.join(dir, SEED_RECORD), JSON.stringify(record, null, 2) + '\n', 'utf8');

  log(quiet, '  [seed] 写入 ' + written.written + ' 条合成记忆（origin=demo_seed），预置轮次 turns='
    + state.turns);
  if (written.rejected.length) {
    log(quiet, '  [seed] 被 memorywriter 拒绝 ' + written.rejected.length + ' 条：'
      + JSON.stringify(written.rejected.map((r) => r.reason)));
  }

  return {
    seeded: written.written,
    skipped: false,
    ids: record.ids,
    rejected: written.rejected,
    turns: state.turns,
    dataDir: dir,
  };
}

function parseArgs(argv) {
  const out = { dataDir: '', quiet: false, force: false, json: false, help: false };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--dir') out.dataDir = argv[++i] || '';
    else if (a === '--quiet') out.quiet = true;
    else if (a === '--force') out.force = true;
    else if (a === '--json') out.json = true;
    else if (a === '-h' || a === '--help') out.help = true;
    else throw new Error('未知参数：' + a);
  }
  return out;
}

function main() {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (e) {
    console.error('[demo/seed] ' + (e && e.message || e));
    process.exit(2);
  }
  if (args.help) {
    console.log('用法: node demo/seed.mjs [--dir <隔离目录>] [--force] [--json] [--quiet]');
    console.log('说明: 经 memorywriter 写入合成记忆（origin=demo_seed）+ 预置 turns=3；从不写真实插件数据目录。');
    return;
  }
  const dataDir = args.dataDir || process.env.LINGSHU_DATA_DIR || DATA_DIR;
  try {
    const report = seedDemoData({ dataDir, quiet: args.quiet || args.json, force: args.force });
    if (args.json) console.log(JSON.stringify(report, null, 2));
    else {
      console.log('  [seed] 数据目录：' + report.dataDir);
      console.log('  [seed] 记忆 id：' + (report.ids.length ? report.ids.join(', ') : '（无）'));
      console.log('  [seed] 预置轮次：' + report.turns + '（下一次对话从 warm 阶段开始）');
    }
  } catch (e) {
    console.error('[demo/seed] ' + (e && e.message || e));
    process.exit(2);
  }
}

if (process.argv[1] && path.resolve(process.argv[1]) === path.resolve(fileURLToPath(import.meta.url))) {
  main();
}
