// console_demo_feed.mjs —— 给控制台造"活的"演示流量（**合成数据，不是真实宿主证据**）。
//
// 为什么需要它：
//   验收要看到"记忆条目随对话增长"和"相变环形图随 Token 变色"，但真实日常流量不可控、
//   也不能拿 desktop profile 做实验（红线）。这个脚本用 **插件真代码 + mock 宿主**，
//   把一轮轮会话事件喂给 `apply()`，写到 `.console-data/<profile>/`，控制台实时读取。
//
// 诚实边界（会打印在控制台 warnings 里，也写进验收报告）：
//   - mock 宿主不是真实 DSH；它只证明"插件写出的数据能被控制台正确观测"；
//   - observe 模式的 ratio 是 chars/3/window 估算，不是 tokenizer；
//   - 演示数据全部标记为 kind=demo（注册表里配的），不得当作标定样本。
//
// 用法：
//   node tools/console_demo_feed.mjs --profile desktop-demo --turns 40 --interval 1200 --reset
//   node tools/console_demo_feed.mjs --profile lab-demo --turns 40 --interval 1200 --reset

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { apply } from '../plugin/dsh-lingshu/index.js';
import { applyCleanupRequests } from '../plugin/dsh-lingshu/memorywriter.js';

const HERE = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.dirname(HERE);

function arg(name, dflt) {
  const i = process.argv.indexOf('--' + name);
  if (i < 0) return dflt;
  const v = process.argv[i + 1];
  return v && !v.startsWith('--') ? v : true;
}
const PROFILE = String(arg('profile', 'desktop-demo'));
const TURNS = Math.max(4, Number(arg('turns', 40)) || 40);
const INTERVAL = Math.max(200, Number(arg('interval', 1200)) || 1200);
const WINDOW = Math.max(1000, Number(arg('window', 6000)) || 6000);
const DATA_DIR = path.resolve(String(arg('data-dir', path.join(ROOT, '.console-data', PROFILE))));
const RESET = Boolean(arg('reset', false));

function sleep(ms) { return new Promise((r) => setTimeout(r, ms)); }

// 最小 mock 宿主：只实现插件在对应模式下会用到的那几个 API（与 host-sim 同形，不复制业务逻辑）。
function makeHost() {
  const sections = [];
  const hooks = {};
  const tools = [];
  const ctx = {
    tools: { register: (t) => tools.push(t) },
    systemPrompt: { section: (s) => sections.push(s) },
    on: (ev, fn) => { (hooks[ev] = hooks[ev] || []).push(fn); },
    get: () => { throw new Error('mock host: get() 未实现（本脚本用不到 inject）'); },
    effect: (fn) => { try { return fn(); } catch { /* 忽略 */ } },
    inject: () => {},
  };
  return { ctx, sections, hooks, tools };
}

async function fire(hooks, ev, args = []) {
  let out;
  for (const fn of hooks[ev] || []) out = await fn(...args);
  return out;
}
async function fireWaterfall(hooks, ev, payload) {
  let out;
  for (const fn of hooks[ev] || []) {
    out = await fn(payload, () => Promise.resolve({ kind: 'enter', messages: [] }));
  }
  return out;
}

const userEvent = (text) => ([{}, { type: 'user/message', seq: 1, time: Date.now(),
  data: { message: { role: 'user', content: [{ type: 'text', text }] } } }]);
const assistantEvent = (text) => ([{}, { type: 'assistant/message', seq: 2, time: Date.now(),
  data: { message: { role: 'assistant', content: [{ type: 'text', text }] } } }]);

// 记忆唯一写方需要 store 适配器；演示清理也必须经 memorywriter（不许手改 JSONL）。
function makeMemoryStore(dir) {
  const file = path.join(dir, 'memory.jsonl');
  return {
    read() {
      const byId = new Map();
      let rows = [];
      try {
        rows = fs.readFileSync(file, 'utf8').split('\n').filter(Boolean)
          .map((l) => { try { return JSON.parse(l); } catch { return null; } }).filter(Boolean);
      } catch { rows = []; }
      for (const e of rows) byId.set(String(e.id), e);
      return [...byId.values()];
    },
    append(objs) {
      fs.appendFileSync(file, objs.map((o) => JSON.stringify(o)).join('\n') + '\n', 'utf8');
    },
  };
}

async function main() {
  if (!['desktop-demo', 'lab-demo'].includes(PROFILE)) {
    console.error(`[feed] profile 只支持 desktop-demo / lab-demo，收到 ${PROFILE}`);
    process.exit(2);
  }
  if (RESET) {
    fs.rmSync(DATA_DIR, { recursive: true, force: true });
    console.log(`[feed] 已清空 ${DATA_DIR}`);
  }
  fs.mkdirSync(DATA_DIR, { recursive: true });
  process.env.LINGSHU_DATA_DIR = DATA_DIR;
  process.env.LINGSHU_CONTEXT_WINDOW = String(WINDOW);

  const mode = PROFILE === 'desktop-demo' ? 'observe' : 'active';
  const h = makeHost();
  apply(h.ctx, { mode, contextWindowTokens: WINDOW });
  console.log(`[feed] ${PROFILE} · mode=${mode} · window=${WINDOW} · turns=${TURNS} · dir=${DATA_DIR}`);
  console.log('[feed] 合成数据：插件真代码 + mock 宿主；不是真实 DSH 流量，不得作标定证据');

  // 首帧请求上下文：让插件拿到真实窗口（observe/active 都通过 session/event 落盘）
  await fire(h.hooks, 'session/event', [{}, { type: 'request/context',
    data: { contextWindow: WINDOW } }]);

  const facts = ['阿尔法', '贝塔', '伽马', '德尔塔'];
  let lastFact = null;
  for (let i = 1; i <= TURNS; i++) {
    const ratio = Math.min(0.96, 0.04 + (0.92 * i) / TURNS);
    // active：每轮上下文是"本轮 messages 大小" → padding 按 ratio 造；
    // observe：没有 pre-step，插件按"累计消息字符"估算 → 每轮只加一份增量，
    //          让整体曲线在第 TURNS 轮附近逼近 ratio，而不是第一轮就撑满。
    const targetChars = mode === 'active'
      ? Math.max(60, Math.round(ratio * WINDOW * 3))
      : Math.max(60, Math.round((0.92 * WINDOW * 3) / TURNS));
    const unit = '上下文填充。';
    const padding = unit.repeat(Math.max(1, Math.round(targetChars / unit.length)));
    let userText = `第 ${i} 轮继续。请基于当前上下文回答。`;
    if (i % 5 === 0) {
      const f = facts[Math.floor(i / 5) % facts.length];
      userText = i % 10 === 0 && lastFact
        ? `我改主意了，项目代号是${f}`
        : `我的项目代号是${f}`;
      lastFact = f;
    } else if (i % 13 === 0) {
      userText = `不对，你漏了第 ${i} 轮的要点`;
    } else if (i % 17 === 0) {
      userText = '记住：我的常用终端是 PowerShell。';
    }

    if (mode === 'active') {
      const messages = [{ role: 'user', content: [{ type: 'text', text: userText + padding }] }];
      await fireWaterfall(h.hooks, 'agent/pre-step', { turn: i, step: 1, messages });
      await fire(h.hooks, 'session/event', userEvent(userText));
    } else {
      await fire(h.hooks, 'session/event', userEvent(userText));
      await fire(h.hooks, 'session/event', assistantEvent('答复：' + padding));
    }
    await fire(h.hooks, 'session/event', [{}, { type: 'turn/end', seq: 3, time: Date.now(), data: {} }]);

    // 第 30 轮演示"清理测试数据"：走 memorywriter 唯一写方，active 计数会减少（真实代码路径）。
    if (i === 30) {
      try {
        const res = applyCleanupRequests([{
          id: 'mem_001', reason: 'test_data',
          sourceQuote: '演示数据清理：非真实偏好', operator: 'console-demo',
        }], makeMemoryStore(DATA_DIR), { now: Date.now() });
        console.log(`[feed] cleanup mem_001 → ${JSON.stringify(res)}`);
      } catch (err) {
        console.log(`[feed] cleanup skipped: ${String(err).slice(0, 120)}`);
      }
    }

    if (i % 5 === 0 || i === TURNS) {
      const stateFile = path.join(DATA_DIR, 'state.json');
      let st = {};
      try { st = JSON.parse(fs.readFileSync(stateFile, 'utf8')); } catch { /* 忽略 */ }
      const memFile = path.join(DATA_DIR, 'memory.jsonl');
      let rows = 0;
      try { rows = fs.readFileSync(memFile, 'utf8').split('\n').filter(Boolean).length; } catch { /* 忽略 */ }
      console.log(`[feed] turn ${i}/${TURNS} · turns=${st.turns ?? '?'}`
        + ` · ratio=${st.control?.phase?.ratio ?? '?'} (${st.control?.phase?.phase ?? '?'})`
        + ` · memory_rows=${rows}`);
    }
    if (i < TURNS) await sleep(INTERVAL);
  }
  console.log('[feed] 完成。控制台里切换到该 profile 即可看到数据；这只是合成演示，不是真实流量。');
  process.exit(0);
}

main().catch((err) => {
  console.error('[feed] 失败：' + (err && err.stack ? err.stack : String(err)));
  process.exit(1);
});
