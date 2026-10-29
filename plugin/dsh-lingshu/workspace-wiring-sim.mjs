// workspace-wiring-sim.mjs —— P1 接线自检：工作区隔离真的接到了数据目录上吗？
//
// 与 workspace-sim.mjs 的分工：
//   workspace-sim      = 纯函数层（id 解析 / 目录布局 / 策略合并），不碰插件
//   workspace-wiring-sim = **接线层**：真的 `apply()` 一个 mock 宿主，检查落盘目录与审计
//
// 覆盖 PE-A1（两工作区数据目录不同）/ PE-A2（id 优先级 + 非法 id 不静默串味）/ PE-A8（非法 id 拒绝）
// 的**接线**部分。PE-A3/A5/A6/A7（坏 state、迁移、并发写）仍未覆盖。
//
// 用法：node workspace-wiring-sim.mjs [--assert]

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { strict as assert } from 'node:assert';
import { apply } from './index.js';

const ASSERT_ONLY = process.argv.includes('--assert');
const results = [];
function check(label, cond, detail = '') {
  results.push({ label, cond: Boolean(cond) });
  if (!ASSERT_ONLY) console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}${detail ? '  — ' + detail : ''}`);
}

const ENV_KEYS = ['DSH_HOME', 'LINGSHU_DATA_DIR', 'LINGSHU_WORKSPACE'];

function makeCtx() {
  const ctx = {
    tools: { register: () => {} },
    systemPrompt: { section: () => {} },
    on: () => {},
    get: () => { throw new Error('no inject'); },
    effect: (fn) => { try { return fn(); } catch { /* ignore */ } },
    inject: () => {},
  };
  return ctx;
}

function withEnv(env, fn) {
  const saved = {};
  for (const k of ENV_KEYS) { saved[k] = process.env[k]; delete process.env[k]; }
  try {
    Object.assign(process.env, env);
    return fn();
  } finally {
    for (const k of ENV_KEYS) {
      if (saved[k] === undefined) delete process.env[k]; else process.env[k] = saved[k];
    }
  }
}

function freshHome(tag) {
  const h = fs.mkdtempSync(path.join(os.tmpdir(), `lingshu-ws-${tag}-`));
  return h;
}

function applyIn(env, cfg) {
  const home = freshHome('case');
  const ctx = makeCtx();
  withEnv({ DSH_HOME: home, ...env }, () => apply(ctx, cfg || {}));
  return home;
}

function exists(p) { return fs.existsSync(p); }
function auditOf(dir) {
  const f = path.join(dir, 'audit.jsonl');
  if (!exists(f)) return [];
  return fs.readFileSync(f, 'utf8').split('\n').filter(Boolean)
    .map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
}
const baseOf = (home) => path.join(home, 'plugins', 'dsh-lingshu');

// ── 1. 缺省零回归：不配工作区 → 旧路径 ────────────────────────
{
  const home = applyIn({});
  const base = baseOf(home);
  check('缺省：数据落在旧平铺目录', exists(path.join(base, 'audit.jsonl')), base);
  check('缺省：不创建 workspace_* 目录',
    !fs.readdirSync(base).some((n) => n.startsWith('workspace_')));
  const aud = auditOf(base);
  check('缺省：不产生 workspace_rejected', !aud.some((e) => e.kind === 'workspace_rejected'));
}

// ── 2. PE-A1：env 指定工作区 → 独立目录 ──────────────────────
{
  const home = applyIn({ LINGSHU_WORKSPACE: 'code' });
  const dir = path.join(baseOf(home), 'workspace_code');
  check('env LINGSHU_WORKSPACE=code → workspace_code/audit.jsonl', exists(path.join(dir, 'audit.jsonl')), dir);
  check('env 工作区：legacy 目录不被写入', !exists(path.join(baseOf(home), 'audit.jsonl')));

  const home2 = applyIn({ LINGSHU_WORKSPACE: 'roleplay' });
  const dir2 = path.join(baseOf(home2), 'workspace_roleplay');
  check('PE-A1：code 与 roleplay 是不同的目录（同名 home 下比较）', dir !== dir2);
  check('env 工作区只写自己的目录', exists(path.join(dir2, 'audit.jsonl')));
}

// ── 3. config.workspaceId + profileTag 两层 ─────────────────
{
  const home = applyIn({}, { workspaceId: 'roleplay', profileTag: 'lab' });
  const dir = path.join(baseOf(home), 'workspace_roleplay', 'profiles', 'lab');
  check('config.workspaceId + profileTag → workspace_<id>/profiles/<tag>', exists(path.join(dir, 'audit.jsonl')), dir);
}

// ── 4. env 优先于 config ────────────────────────────────────
{
  const home = applyIn({ LINGSHU_WORKSPACE: 'fromenv' }, { workspaceId: 'fromconfig' });
  const base = baseOf(home);
  check('env 优先于 config：落 workspace_fromenv',
    exists(path.join(base, 'workspace_fromenv', 'audit.jsonl'))
    && !exists(path.join(base, 'workspace_fromconfig', 'audit.jsonl')));
}

// ── 5. PE-A8：非法 id 拒绝、不越界、必须留审计 ───────────────
{
  const home = applyIn({ LINGSHU_WORKSPACE: '../evil' });
  const base = baseOf(home);
  check('非法 id：退回 legacy 平铺目录', exists(path.join(base, 'audit.jsonl')), base);
  check('非法 id：不产生 workspace_../evil 之类的目录',
    !fs.readdirSync(base).some((n) => n.startsWith('workspace_')));
  const aud = auditOf(base);
  const rej = aud.find((e) => e.kind === 'workspace_rejected');
  check('非法 id：落 workspace_rejected 审计（不静默失效）', Boolean(rej), rej ? JSON.stringify(rej).slice(0, 120) : 'missing');
  check('非法 id：审计记录原值与原因',
    Boolean(rej) && rej.reason === 'invalid_workspace_id' && typeof rej.value === 'string' && rej.value.length > 0);
}

// ── 6. LINGSHU_DATA_DIR：旧语义保持，仅显式工作区才叠加 ────────
{
  // 6a. override + 无 workspaceId → override 本身就是数据根（不叠 profiles 层）
  const fixed = path.join(freshHome('override'), 'data');
  const homeA = freshHome('caseA');
  withEnv({ DSH_HOME: homeA, LINGSHU_DATA_DIR: fixed }, () => apply(makeCtx(), {}));
  check('压测覆盖：override 直落 audit.jsonl（无 profiles/<tag>）', exists(path.join(fixed, 'audit.jsonl')), fixed);
  check('压测覆盖：override 下不创建 workspace_*',
    !fs.readdirSync(fixed).some((n) => n.startsWith('workspace_')));

  // 6b. override + 显式工作区 → <override>/workspace_<id>/
  const fixed2 = path.join(freshHome('override2'), 'data');
  const homeB = freshHome('caseB');
  withEnv({ DSH_HOME: homeB, LINGSHU_DATA_DIR: fixed2, LINGSHU_WORKSPACE: 'code' },
    () => apply(makeCtx(), {}));
  check('压测覆盖 + 显式工作区 → <override>/workspace_code/audit.jsonl',
    exists(path.join(fixed2, 'workspace_code', 'audit.jsonl')), path.join(fixed2, 'workspace_code'));
}

const failed = results.filter((r) => !r.cond);
const passed = results.length - failed.length;
if (ASSERT_ONLY) {
  for (const f of failed) console.log(`FAIL  ${f.label}`);
  console.log(`workspace-wiring-sim: ${passed}/${results.length} passed, ${failed.length} failed`);
}
process.exit(failed.length ? 1 : 0);
