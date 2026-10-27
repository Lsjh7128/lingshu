// dsh-lingshu-probe —— 宿主能力探针（只读、只记日志、不改变任何行为）
//
// 目的：回答三个读源码读不出来的问题（research/02 §8）：
//   1) `agent/pre-step` 到底什么时候触发？每轮都触发吗？在工具调用之前还是之后？
//   2) `tool/result` 的 payload 长什么样？能区分成功/失败吗？含输出全文吗？
//   3) `turn/end` 里做 I/O 会不会拖慢下一轮？
//
// 设计红线：
//   - **绝不抛异常**：任何失败都只记一条日志，不影响宿主（探针把宿主搞挂就失去意义了）。
//   - **只写自己目录**：$DSH_HOME/plugins/dsh-lingshu-probe/probe.log（JSONL）。
//   - **不记录用户内容**：payload 只记结构（键名、类型、长度、截断后的短字符串哈希）。

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';

const NAME = 'dsh-lingshu-probe';
const inject = ['tools'];

// 默认订阅的候选事件名（可用环境变量 LINGSHU_PROBE_EVENTS 覆盖，逗号分隔）
const DEFAULT_EVENTS = [
  'turn/start',
  'agent/pre-step',
  'tool/result',
  'turn/end',
  'session/start',
  'session/end',
  'prompt/configure',
  'message/created',
];

const state = {
  loadedAt: new Date().toISOString(),
  pid: process.pid,
  dshHome: '',
  logPath: '',
  subscribed: {},      // event -> 'ok' | 'err: ...'
  counts: {},          // event -> n
  firstAt: {},         // event -> ms
  lastAt: {},          // event -> ms
  seq: 0,
  lastError: null,
  hasSystemPrompt: null,
  approvalAvailable: null,
};

function dshHome() {
  const env = process.env.DSH_HOME;
  if (env && String(env).trim()) return String(env).trim();
  return path.join(os.homedir(), '.dsh');
}

function logPath() {
  return path.join(dshHome(), 'plugins', NAME, 'probe.log');
}

function hash(s) {
  return crypto.createHash('sha256').update(String(s)).digest('hex').slice(0, 12);
}

/**
 * 读可选服务，**永不抛异常**。
 *
 * 坑（v0.1.0 实测踩到）：cordis 对**未在 `inject` 声明**的属性访问会直接抛
 * `cannot get property "x" without inject`。所以旧版那种
 * `typeof ctx.get === 'function' ? ctx.get(x) : ctx.x` 的"兜底"并不安全 ——
 * 一旦走到 fallback 分支就抛，把整个 apply 打断（日志里只剩一行 probe/error）。
 *
 * 返回 { known, value }：known=false 表示"读不到"（未 inject 或未注册），
 * **不等于服务不存在**，所以上层记 null 而不是 false。
 */
function safeGet(ctx, name) {
  try {
    if (ctx && typeof ctx.get === 'function') {
      const v = ctx.get(name);
      return { known: true, value: v ?? null };
    }
  } catch { /* 未 inject / 未注册：视为读不到 */ }
  return { known: false, value: null };
}

/** 把任意 payload 压成"结构摘要"：只留键名/类型/长度，字符串只留哈希+长度。 */
function summarize(value, depth = 0) {  try {
    if (value === null || value === undefined) return { t: 'null' };
    const t = typeof value;
    if (t === 'string') return { t: 'string', len: value.length, sha: hash(value) };
    if (t === 'number' || t === 'boolean') return { t, v: value };
    if (t === 'function') return { t: 'function' };
    if (Array.isArray(value)) {
      return {
        t: 'array',
        len: value.length,
        item: value.length > 0 && depth < 2 ? summarize(value[0], depth + 1) : undefined,
      };
    }
    if (t === 'object') {
      if (depth >= 2) return { t: 'object', keys: Object.keys(value).length };
      const keys = Object.keys(value).slice(0, 16);
      const out = {};
      for (const k of keys) out[k] = summarize(value[k], depth + 1);
      return { t: 'object', n: Object.keys(value).length, keys: out };
    }
    return { t };
  } catch (err) {
    return { t: 'unsummarizable', err: String(err && err.message ? err.message : err).slice(0, 80) };
  }
}

function write(event, data) {
  try {
    const p = state.logPath;
    if (!p) return;
    fs.mkdirSync(path.dirname(p), { recursive: true });
    // 超过 1MB 就保留最后 400 行，避免无限增长
    try {
      const st = fs.statSync(p);
      if (st.size > 1024 * 1024) {
        const lines = fs.readFileSync(p, 'utf8').split('\n').filter(Boolean).slice(-400);
        fs.writeFileSync(p, lines.join('\n') + '\n', 'utf8');
      }
    } catch { /* 文件不存在：忽略 */ }
    const rec = { ts: Date.now(), iso: new Date().toISOString(), seq: ++state.seq, event, ...data };
    fs.appendFileSync(p, JSON.stringify(rec) + '\n', 'utf8');
  } catch { /* 日志写失败绝不冒泡 */ }
}

function record(ev, payload, extra = {}) {
  const now = Date.now();
  state.counts[ev] = (state.counts[ev] || 0) + 1;
  if (!state.firstAt[ev]) state.firstAt[ev] = now;
  const prev = state.lastAt[ev] || 0;
  state.lastAt[ev] = now;
  write(ev, {
    ...extra,
    deltaMs: prev ? now - prev : null,     // 距同一事件上次触发的间隔
    sinceLoadMs: now - Number(new Date(state.loadedAt)),
    payload: summarize(payload),
  });
}

function subscribe(ctx) {
  const raw = process.env.LINGSHU_PROBE_EVENTS;
  const events = raw && raw.trim()
    ? raw.split(',').map((s) => s.trim()).filter(Boolean)
    : DEFAULT_EVENTS;
  for (const ev of events) {
    try {
      if (typeof ctx.on !== 'function') {
        state.subscribed[ev] = 'err: ctx.on 不是函数';
        continue;
      }
      ctx.on(ev, (...args) => {
        try {
          record(ev, args.length === 1 ? args[0] : args);
        } catch { /* 单条记录失败忽略 */ }
      });
      state.subscribed[ev] = 'ok';
    } catch (err) {
      state.subscribed[ev] = 'err: ' + String(err && err.message ? err.message : err).slice(0, 100);
    }
  }
}

function registerTools(ctx) {
  if (!ctx.tools || typeof ctx.tools.register !== 'function') return;
  ctx.tools.register({
    name: 'probe_status',
    description:
      '查看 linghsu 宿主能力探针的采集情况：已订阅事件、各事件触发次数与时间、是否检测到 systemPrompt/approval 服务。可清空日志。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        tail: { type: 'number', description: '返回最后多少条日志（默认 20，最多 200）' },
        clear: { type: 'boolean', description: '清空日志文件' },
      },
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: true,
        required: ['loadedAt', 'logPath', 'subscribed', 'counts', 'events'],
        properties: {
          loadedAt: { type: 'string' },
          logPath: { type: 'string' },
          subscribed: { type: 'object' },
          counts: { type: 'object' },
          hasSystemPrompt: { type: ['boolean', 'null'] },
          approvalAvailable: { type: ['boolean', 'null'] },
          events: { type: 'array' },
        },
      },
      render(args, value) {
        const sub = Object.entries(value.subscribed || {})
          .map(([k, v]) => `  ${k}: ${v}`)
          .join('\n');
        const cnt = Object.entries(value.counts || {})
          .map(([k, v]) => `  ${k}: ${v}`)
          .join('\n');
        return [
          `探针加载于 ${value.loadedAt}（pid ${state.pid}）`,
          `日志：${value.logPath}`,
          `systemPrompt 服务：${value.hasSystemPrompt === null ? '未探测' : value.hasSystemPrompt ? '存在' : '不存在'}`,
          `approval 服务：${value.approvalAvailable === null ? '未探测' : value.approvalAvailable ? '存在' : '不存在'}`,
          '订阅结果：',
          sub || '  （空）',
          '触发次数：',
          cnt || '  （还没触发过）',
          `最后 ${value.events.length} 条：`,
          ...value.events.map((e) => `  ${e.ts} ${e.event}`),
        ].join('\n');
      },
    },
    async execute(args = {}) {
      const tailN = Math.min(200, Math.max(0, Number(args.tail) || 20));
      if (args.clear) {
        try { fs.writeFileSync(state.logPath, '', 'utf8'); } catch { /* 忽略 */ }
      }
      let events = [];
      try {
        const lines = fs.readFileSync(state.logPath, 'utf8').split('\n').filter(Boolean);
        events = lines.slice(-tailN).map((l) => {
          try { return JSON.parse(l); } catch { return { event: 'unparsable' }; }
        });
      } catch { /* 文件可能还没建 */ }
      return {
        loadedAt: state.loadedAt,
        logPath: state.logPath,
        subscribed: state.subscribed,
        counts: state.counts,
        hasSystemPrompt: state.hasSystemPrompt,
        approvalAvailable: state.approvalAvailable,
        events,
      };
    },
  });
}

export function apply(ctx, config = {}) {
  try {
    state.dshHome = dshHome();
    state.logPath = logPath();
    write('probe/apply', {
      node: process.version,
      cwd: process.cwd(),
      dshHome: state.dshHome,
      configKeys: Object.keys(config || {}),
      hasTools: Boolean(ctx && ctx.tools),
      hasOn: Boolean(ctx && typeof ctx.on === 'function'),
      hasGet: Boolean(ctx && typeof ctx.get === 'function'),
      argv: (process.argv || []).slice(0, 12),
    });

    // 探测可选服务（拿不到记 unknown，不记 false —— false 会被误读成"服务不存在"）
    const sp = safeGet(ctx, 'systemPrompt');
    state.hasSystemPrompt = sp.known ? Boolean(sp.value) : null;
    record('probe/service', { name: 'systemPrompt', known: sp.known, present: state.hasSystemPrompt });

    const ap = safeGet(ctx, 'approval');
    state.approvalAvailable = ap.known ? Boolean(ap.value && typeof ap.value.request === 'function') : null;
    record('probe/service', { name: 'approval', known: ap.known, present: state.approvalAvailable });

    subscribe(ctx);
    registerTools(ctx);
    write('probe/ready', { subscribed: state.subscribed });
  } catch (err) {
    state.lastError = String(err && err.stack ? err.stack : err).slice(0, 400);
    try { write('probe/error', { message: state.lastError }); } catch { /* 放弃 */ }
  }
}

export { NAME as name, inject };
export default { name: NAME, inject, apply };
