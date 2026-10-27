// memory-admin.mjs —— 记忆脏数据清理的**受控入口**（v4.3 真实标定阶段）
//
// 红线：真实日志里的脏数据（测试造成的错误记忆、隐私内容、错误事实）不许直接手改
// `memory.jsonl`，必须走 memorywriter 的 `applyCleanupRequests`：
//   · 追加一条同 id 的墓碑记录（status=suppressed + cleanup 溯源），读取侧 last-wins；
//   · 每条清理都要求 reason 在闭集里 + sourceQuote（可追溯）+ operator；
//   · 追加一条 `memory_cleanup` 审计事件。
//
// 用法（不装进插件包，等同 host-sim/stress 的开发工具）：
//   $node memory-admin.mjs plan
//   $node memory-admin.mjs apply --ids mem_003,mem_004 --reason test_data \
//         --source-quote "2026-10-26 测试写入，非真实偏好" --operator H0
//   $node memory-admin.mjs apply --ids mem_003 --reason test_data \
//         --source-quote "..." --dry-run
//
// 环境变量 DSH_HOME 决定数据目录（缺省 ~/.dsh）；--dir 可直接指向 plugins/dsh-lingshu。

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { applyCleanupRequests, CLEANUP_REASONS } from './memorywriter.js';

function home() {
  const h = process.env.DSH_HOME;
  return h && String(h).trim() ? String(h).trim() : path.join(os.homedir(), '.dsh');
}

function parseArgs(argv) {
  const args = { _: [] };
  for (let i = 0; i < argv.length; i++) {
    const a = argv[i];
    if (a.startsWith('--')) {
      const eq = a.indexOf('=');
      if (eq > 0) args[a.slice(2, eq)] = a.slice(eq + 1);
      else if (i + 1 < argv.length && !argv[i + 1].startsWith('--')) args[a.slice(2)] = argv[++i];
      else args[a.slice(2)] = true;
    } else args._.push(a);
  }
  return args;
}

function readJsonl(file) {
  try {
    return fs.readFileSync(file, 'utf8').split('\n').filter(Boolean)
      .map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
  } catch { return []; }
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const cmd = args._[0] || 'plan';
  const dir = args.dir ? path.resolve(String(args.dir))
    : path.join(home(), 'plugins', 'dsh-lingshu');
  const memFile = path.join(dir, 'memory.jsonl');
  const auditFile = path.join(dir, 'audit.jsonl');
  if (!fs.existsSync(memFile)) {
    console.error('找不到 ' + memFile + '（先用 --dir 或 DSH_HOME 指向插件数据目录）');
    return 2;
  }
  const rows = readJsonl(memFile);
  const byId = new Map();
  for (const r of rows) byId.set(String(r.id), r);
  const entries = [...byId.values()];

  if (cmd === 'plan') {
    console.log('数据目录: ' + dir);
    console.log('记忆条数: ' + entries.length + '（原始行 ' + rows.length + '，含墓碑）');
    for (const e of entries) {
      console.log([e.id, e.status, e.origin || '-', 'turn=' + (e.turn ?? '-'),
        String(e.content || '').slice(0, 50)].join(' | '));
    }
    console.log('\n可清理原因: ' + CLEANUP_REASONS.join(', '));
    return 0;
  }

  if (cmd !== 'apply') {
    console.error('未知命令: ' + cmd + '（支持 plan / apply）');
    return 2;
  }

  const ids = String(args.ids || '').split(',').map((s) => s.trim()).filter(Boolean);
  const reqs = ids.map((id) => ({
    id,
    reason: args.reason,
    sourceQuote: args['source-quote'],
    operator: args.operator,
  }));
  if (!ids.length) {
    console.error('apply 需要 --ids（逗号分隔）');
    return 2;
  }

  const store = {
    read: () => entries,
    append: (objs) => {
      fs.appendFileSync(memFile, objs.map((o) => JSON.stringify(o)).join('\n') + '\n', 'utf8');
    },
  };

  if (args['dry-run']) {
    const dry = applyCleanupRequests(reqs, { read: () => entries, append: () => {} });
    console.log('DRY-RUN：将清理 ' + dry.cleaned + ' 条；拒绝 ' + dry.rejected.length + ' 条');
    for (const r of dry.rejected) console.log('  拒绝 ' + r.id + ': ' + r.reason);
    return 0;
  }

  const res = applyCleanupRequests(reqs, store, { operator: args.operator });
  console.log('已清理 ' + res.cleaned + ' 条：' + (res.ids.join(', ') || '无'));
  for (const r of res.rejected) console.log('  拒绝 ' + r.id + ': ' + r.reason);
  if (res.cleaned) {
    const rec = {
      ts: Date.now(),
      kind: 'memory_cleanup',
      ids: res.ids,
      reason: args.reason,
      sourceQuote: String(args['source-quote'] || '').slice(0, 120),
      operator: String(args.operator || 'operator'),
    };
    try { fs.appendFileSync(auditFile, JSON.stringify(rec) + '\n', 'utf8'); } catch { /* 日志失败不冒泡 */ }
    console.log('已写审计 memory_cleanup（' + auditFile + '）');
  }
  return res.rejected.length && !res.cleaned ? 1 : 0;
}

process.exitCode = main();
