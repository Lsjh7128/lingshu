// sandbox-host.mjs —— 供应链加载器的父进程侧：静态裁决 + 子进程沙箱 + JSONL RPC
//
// 分工：
//   supply-chain.js  纯函数：Manifest 校验 / 静态扫描 / 裁决 / 权限参数推导（py 同构）
//   sandbox-host.mjs 本文件：读插件文件 → 调裁决 → 通过则 spawn 子进程 → RPC 转发
//   sandbox-child.mjs 子进程：在 Node permission model 里 import 插件，只暴露声明的 exports
//
// 为什么 RPC 走 127.0.0.1 TCP 而不是 stdio 管道：DSH 的文件沙箱在受限模式下禁止打开命名管道，
// `child_process` 用管道 stdio 会 EPERM（本机实测）；TCP loopback 不受该限制，且一次性 token
// 保证只有本进程 spawn 的子进程能握手。这个约束写进 docs/26 §6，不粉饰。
//
// 失败一律 fail-closed：静态 deny → 不 spawn；沙箱启动失败/就绪超时/调用超时 → kill + deny。
// 任何路径都不得抛异常给宿主（设计红线同 index.js）。

import fs from 'node:fs';
import path from 'node:path';
import net from 'node:net';
import crypto from 'node:crypto';
import { spawn } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import {
  decide, renderAlert, ERROR_CODES, POLICY, POLICY_VERSION, POLICY_SOURCE, POLICY_DEGRADED,
} from './supply-chain.js';

const HOST_DIR = path.dirname(fileURLToPath(import.meta.url));
const CHILD_SCRIPT = path.join(HOST_DIR, 'sandbox-child.mjs');
const MAX_MESSAGE_BYTES = 1024 * 1024;
const MANIFEST_FILE = 'plugin.manifest.json';

// 仍在运行的沙箱子进程句柄（宿主卸载时统一关闭；不把 handle 放进可序列化状态里）
const LIVE_HANDLES = [];
export async function closeAllSandboxes(reason = 'host_unload') {
  const handles = LIVE_HANDLES.splice(0);
  for (const handle of handles) {
    try { await handle.close(reason); } catch { /* 已退出/已 kill 都算正常 */ }
  }
  return handles.length;
}

// 进程内沙箱状态（供 lingshu_status / 控制台读；不落盘，不动其它状态字段）
const SANDBOX_STATE = {
  policy_version: POLICY_VERSION,
  policy_source: POLICY_SOURCE,
  policy_degraded: POLICY_DEGRADED,
  loaded: [],      // [{id, pid, exports}]
  denied: [],      // [{id, reason, error_code, fingerprint}]
  suspended: false, // 出现过 deny → 停止继续加载（挂起加载器）
  violations: [],  // [{id, code, message}]
  probes: [],
  support: { supported: false, node: process.version, reason: 'not_probed' },
};

export function getSandboxState() {
  return JSON.parse(JSON.stringify(SANDBOX_STATE));
}

export function resetSandboxState() {
  SANDBOX_STATE.loaded.length = 0;
  SANDBOX_STATE.denied.length = 0;
  SANDBOX_STATE.violations.length = 0;
  SANDBOX_STATE.probes.length = 0;
  SANDBOX_STATE.suspended = false;
}

export class SandboxError extends Error {
  constructor(code, message, detail) {
    super(message || code);
    this.name = 'SandboxError';
    this.code = code;
    this.detail = detail || null;
  }
}

function sha256File(file) {
  try {
    return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
  } catch { return ''; }
}

// ── 1. 读取插件目录的原始输入（不做判断，交给 supply-chain.decide）────────
export function readPluginInputs(pluginDir, opts = {}) {
  const root = path.resolve(String(pluginDir));
  const manifestPath = path.join(root, MANIFEST_FILE);
  let manifestRaw;
  let manifestPresent = false;
  try {
    if (fs.existsSync(manifestPath)) {
      manifestRaw = fs.readFileSync(manifestPath, 'utf8');
      manifestPresent = true;
    }
  } catch { manifestPresent = false; }

  let entry = 'index.mjs';
  try {
    if (manifestRaw) {
      const parsed = JSON.parse(manifestRaw);
      if (parsed && typeof parsed.entry === 'string') entry = parsed.entry;
    }
  } catch { /* 交给 decide 判 manifest_unreadable */ }
  const entryRel = String(entry).replace(/^\.\//, '');
  const entryPath = path.resolve(root, entryRel);
  let entryPresent = false;
  let entrySource = '';
  try {
    if (fs.existsSync(entryPath) && fs.statSync(entryPath).isFile()) {
      entryPresent = true;
      entrySource = fs.readFileSync(entryPath, 'utf8');
    }
  } catch { entryPresent = false; }

  return {
    manifestPresent,
    manifestRaw,
    entryPresent,
    entrySource,
    entrySha256: entryPresent ? sha256File(entryPath) : '',
    pluginDir: root,
    entryPath,
    sandboxSupported: opts.sandboxSupported === undefined
      ? SANDBOX_STATE.support.supported === true
      : Boolean(opts.sandboxSupported),
  };
}

// ── 2. 纯静态裁决（不 spawn，可单独用于 CI/审计）─────────────────────────
export function evaluatePluginDir(pluginDir, opts = {}) {
  const inputs = readPluginInputs(pluginDir, opts);
  const decision = decide(inputs, POLICY);
  return { decision, inputs };
}

// ── 3. 探测宿主是否支持 Node permission model ───────────────────────────
// 探针本身也走 spawn；在 DSH 受限沙箱里 stdio 管道会 EPERM，所以只信 `stdio:'ignore'` 的退出码。
export async function detectSandboxSupport(opts = {}) {
  const execPath = String(opts.execPath || process.execPath);
  const timeoutMs = Number(opts.timeoutMs || 3000);
  const flag = String(POLICY.sandbox.node_permission_flag || '--permission');
  const probe = { supported: false, node: process.version, execPath, flag, exit: null, reason: 'not_run' };
  await new Promise((resolve) => {
    let settled = false;
    const done = (patch) => {
      if (settled) return;
      settled = true;
      Object.assign(probe, patch);
      resolve();
    };
    let child;
    try {
      child = spawn(execPath, [flag, '-e', 'process.exit(0)'], {
        stdio: ['ignore', 'ignore', 'ignore'], windowsHide: true, env: childEnv(),
      });
    } catch (err) {
      done({ reason: `spawn_error:${err && err.code ? err.code : 'unknown'}` });
      return;
    }
    const timer = setTimeout(() => { try { child.kill('SIGKILL'); } catch { /* noop */ } done({ reason: 'timeout' }); }, timeoutMs);
    child.on('error', (err) => { clearTimeout(timer); done({ reason: `error:${err && err.code ? err.code : 'unknown'}` }); });
    child.on('exit', (code) => {
      clearTimeout(timer);
      done({ supported: code === 0, exit: code, reason: code === 0 ? 'ok' : `exit_${code}` });
    });
  });
  SANDBOX_STATE.probes.push(probe);
  SANDBOX_STATE.support = probe;
  return probe;
}

function childEnv(extra = {}) {
  const allow = Array.isArray(POLICY.sandbox.env_allowlist) && POLICY.sandbox.env_allowlist.length
    ? POLICY.sandbox.env_allowlist
    : ['PATH', 'SystemRoot', 'TEMP', 'TMP', 'NODE_ENV'];
  const env = {};
  for (const key of allow) if (process.env[key] !== undefined) env[key] = process.env[key];
  env.NODE_ENV = 'production';
  return { ...env, ...extra };
}

// ── 4. 启动沙箱并等待 ready ─────────────────────────────────────────────
// 返回 {ok:true, handle} 或 {ok:false, decision, error}；任何失败都不抛异常。
export async function loadSandboxedPlugin(pluginDir, opts = {}) {
  const policy = opts.policy || POLICY;
  const isDeny = (decision) => decision && decision.verdict === 'deny';
  let decision = null;
  try {
    const evaluated = evaluatePluginDir(pluginDir, opts);
    decision = evaluated.decision;
    if (isDeny(decision)) {
      recordDenied(decision);
      return { ok: false, decision, error: new SandboxError(decision.error_code || 'SC-EX', decision.reason), alert: renderAlert(decision) };
    }
    const started = await spawnSandbox(decision, {
      entryPath: evaluated.inputs.entryPath,
      pluginDir: evaluated.inputs.pluginDir,
      pluginExports: Array.isArray(decision.exports) ? decision.exports : [],
      policy,
      onViolation: opts.onViolation,
    });
    if (!started.ok) {
      const runtimeDecision = {
        ...decision,
        verdict: String(policy.decision.verdicts.blocker || 'deny'),
        reason: started.reason,
        error_code: started.error_code || 'SC-E12',
        failures: [...(decision.failures || []), { code: started.reason, detail: started.detail || '', error_code: started.error_code || 'SC-E12' }],
        runtime: started,
      };
      recordDenied(runtimeDecision);
      return { ok: false, decision: runtimeDecision, error: new SandboxError(started.error_code || 'SC-E12', started.reason, started.detail), alert: renderAlert(runtimeDecision) };
    }
    const handle = started.handle;
    LIVE_HANDLES.push(handle);
    SANDBOX_STATE.loaded.push({ id: decision.manifest_id, pid: handle.pid, exports: handle.exports.slice() });
    return { ok: true, decision, handle, pid: handle.pid };
  } catch (err) {
    // 兜底：宿主侧异常也按 deny 处理（fail-closed），绝不把插件放进进程
    const fallback = decision || { manifest_id: null, verdict: 'deny', reason: 'host_exception', error_code: 'SC-EX', failures: [] };
    const errorDecision = { ...fallback, verdict: 'deny', reason: 'host_exception', error_code: 'SC-EX' };
    recordDenied(errorDecision);
    return { ok: false, decision: errorDecision, error, alert: renderAlert(errorDecision) };
  }
}

function recordDenied(decision) {
  SANDBOX_STATE.denied.push({
    id: decision.manifest_id || null,
    reason: decision.reason,
    error_code: decision.error_code || null,
    fingerprint: decision.fingerprint || null,
  });
  SANDBOX_STATE.suspended = true;
}

// 顺序加载多个插件：一旦出现 deny 即停止（fail-fast + 挂起加载器），返回汇总。
export async function loadAllSandboxedPlugins(pluginDirs, opts = {}) {
  const results = [];
  for (const dir of pluginDirs || []) {
    if (SANDBOX_STATE.suspended) {
      results.push({ pluginDir: dir, ok: false, skipped: true, reason: 'loader_suspended' });
      continue;
    }
    const r = await loadSandboxedPlugin(dir, opts);
    results.push({ pluginDir: dir, ...r });
  }
  return { results, state: getSandboxState() };
}

function spawnSandbox(decision, ctx) {
  return new Promise((resolve) => {
    const policy = ctx.policy;
    const execPath = process.execPath;
    const nodeFlags = Array.isArray(decision.sandbox && decision.sandbox.node_flags)
      ? decision.sandbox.node_flags.slice()
      : [];
    // 子进程脚本本身也必须在 allow-fs-read 白名单里（Node 加载主模块要读它）
    nodeFlags.push(`--allow-fs-read=${CHILD_SCRIPT}`);

    const token = crypto.randomBytes(24).toString('hex');
    const readyTimeoutMs = Number(policy.sandbox.ready_timeout_ms || 5000);
    const callTimeoutMs = Number(policy.sandbox.call_timeout_ms || 2000);
    const manifestExports = Array.isArray(ctx.pluginExports) ? ctx.pluginExports.slice() : [];

    let child;
    let socket = null;
    let settled = false;
    let exited = false;
    let closed = false;
    const pending = new Map();
    const violations = [];
    const timers = new Set();

    const server = net.createServer();
    const setTimer = (fn, ms) => { const t = setTimeout(fn, ms); timers.add(t); return t; };
    const clearAllTimers = () => { for (const t of timers) clearTimeout(t); timers.clear(); };

    const killChild = (reason) => {
      if (exited) return;
      try { child.kill('SIGKILL'); } catch { /* noop */ }
      if (reason) violations.push({ code: 'killed', message: String(reason) });
    };

    const cleanup = () => {
      clearAllTimers();
      try { if (socket) socket.destroy(); } catch { /* noop */ }
      try { server.close(); } catch { /* noop */ }
    };

    const failReady = (reason, errorCode, detail) => {
      if (settled) return;
      settled = true;
      cleanup();
      killChild(reason);
      resolve({ ok: false, reason, error_code: errorCode || ERROR_CODES.sandbox_start_failed || 'SC-E12', detail: detail || null, violations });
    };

    const succeed = (handle) => {
      if (settled) return;
      settled = true;
      resolve({ ok: true, handle });
    };

    const write = (msg) => {
      try {
        if (!socket || socket.destroyed) return false;
        const text = JSON.stringify(msg);
        if (Buffer.byteLength(text, 'utf8') > MAX_MESSAGE_BYTES) return false;
        socket.write(text + '\n');
        return true;
      } catch { return false; }
    };

    let seq = 0;
    const makeHandle = (readyExports) => ({
      pluginId: decision.manifest_id,
      pid: child.pid,
      exports: readyExports.slice(),
      decision,
      violations,
      call(method, params = {}, timeoutMs = callTimeoutMs) {
        return new Promise((res, rej) => {
          if (closed || exited) { rej(new SandboxError('sandbox_closed', 'sandbox already closed')); return; }
          if (!readyExports.includes(String(method))) { rej(new SandboxError('method_not_allowed', `method not in manifest.exports: ${method}`)); return; }
          seq += 1;
          const id = `c${seq}`;
          const timer = setTimeout(() => {
            pending.delete(id);
            violations.push({ code: 'call_timeout', message: `${method} > ${timeoutMs}ms` });
            killChild('call_timeout');
            rej(new SandboxError('sandbox_call_timeout', `call timeout after ${timeoutMs}ms`, { method, id }));
          }, Math.max(50, Number(timeoutMs) || callTimeoutMs));
          pending.set(id, { resolve: res, reject: rej, timer, method });
          if (!write({ type: 'call', id, method: String(method), params })) {
            clearTimeout(timer);
            pending.delete(id);
            rej(new SandboxError('sandbox_channel_closed', 'rpc channel unavailable'));
          }
        });
      },
      async close(reason = 'closed_by_host') {
        if (closed) return;
        closed = true;
        try { write({ type: 'close' }); } catch { /* noop */ }
        await new Promise((res) => {
          const t = setTimeout(res, Number(policy.sandbox.kill_grace_ms || 500));
          child.once('exit', () => { clearTimeout(t); res(); });
        });
        killChild(reason);
        cleanup();
      },
      kill(reason = 'killed_by_host') { killChild(reason); cleanup(); },
    });

    // 1) 起 TCP 服务（loopback，端口 0 = 系统分配）
    server.on('error', () => failReady('sandbox_server_error', 'SC-E12'));
    server.listen(0, '127.0.0.1', () => {
      let port;
      try { port = server.address().port; } catch { failReady('sandbox_server_error', 'SC-E12'); return; }
      // 2) spawn 子进程：权限参数全部来自 decision.sandbox.node_flags（由能力推导）
      try {
        child = spawn(execPath, [...nodeFlags, CHILD_SCRIPT], {
          cwd: ctx.pluginDir,
          env: childEnv({ LINGSHU_SC_PORT: String(port), LINGSHU_SC_TOKEN: token }),
          stdio: ['ignore', 'ignore', 'inherit'],
          windowsHide: true,
        });
      } catch (err) {
        failReady('sandbox_spawn_failed', 'SC-E12', err && err.message);
        return;
      }
      child.on('error', (err) => failReady('sandbox_spawn_failed', 'SC-E12', err && err.message));
      child.on('exit', (code, signal) => {
        exited = true;
        if (!settled) failReady('sandbox_start_failed', 'SC-E12', `child exited code=${code} signal=${signal}`);
        for (const [id, p] of pending) {
          clearTimeout(p.timer);
          pending.delete(id);
          p.reject(new SandboxError('sandbox_crashed', `sandbox child exited (code=${code})`));
        }
      });

      // 3) 握手：等子进程 hello（token 必须匹配），再下发 init
      const readyTimer = setTimer(() => failReady('sandbox_ready_timeout', ERROR_CODES.sandbox_ready_timeout || 'SC-E13', `ready > ${readyTimeoutMs}ms`), readyTimeoutMs);
      server.on('connection', (sock) => {
        if (socket) { try { sock.destroy(); } catch { /* noop */ } return; }
        socket = sock;
        socket.setEncoding('utf8');
        let buf = '';
        socket.on('data', (chunk) => {
          buf += chunk;
          if (Buffer.byteLength(buf, 'utf8') > MAX_MESSAGE_BYTES) { failReady('sandbox_protocol_error', 'SC-E12', 'message too large'); return; }
          let idx;
          while ((idx = buf.indexOf('\n')) >= 0) {
            const line = buf.slice(0, idx);
            buf = buf.slice(idx + 1);
            if (!line) continue;
            let msg;
            try { msg = JSON.parse(line); } catch { continue; }
            onChildMessage(msg);
          }
        });
        socket.on('close', () => {
          if (!settled) failReady('sandbox_channel_closed', 'SC-E12', 'child closed channel before ready');
        });
        socket.on('error', () => { /* close 事件会兜 */ });
      });
    });

    function onChildMessage(msg) {
      if (!msg || typeof msg !== 'object') return;
      if (msg.type === 'hello') {
        if (String(msg.token || '') !== token) { failReady('sandbox_handshake_failed', 'SC-E12', 'token mismatch'); return; }
        // 子进程 pid 必须与 spawn 出来的一致（防本机其它进程抢连）
        if (Number(msg.pid) !== Number(child.pid)) { failReady('sandbox_handshake_failed', 'SC-E12', `pid mismatch child=${child.pid}`); return; }
        const exportsList = ctx.pluginExports || manifestExports;
        if (!write({ type: 'init', pluginId: decision.manifest_id, entry: ctx.entryPath, exports: exportsList, pluginDir: ctx.pluginDir })) {
          failReady('sandbox_channel_closed', 'SC-E12', 'init write failed');
        }
        return;
      }
      if (msg.type === 'ready') {
        clearAllTimers();
        succeed(makeHandle(Array.isArray(msg.exports) && msg.exports.length ? msg.exports : manifestExports));
        return;
      }
      if (msg.type === 'fatal') {
        clearAllTimers();
        failReady(String(msg.code || 'sandbox_entry_failed'), 'SC-E12', msg.error ? JSON.stringify(msg.error).slice(0, 400) : JSON.stringify(msg).slice(0, 400));
        return;
      }
      if (msg.type === 'violation') {
        violations.push({ code: String(msg.code || 'violation'), message: JSON.stringify(msg.error || msg).slice(0, 800) });
        if (typeof ctx.onViolation === 'function') {
          try { ctx.onViolation(violations[violations.length - 1]); } catch { /* noop */ }
        }
        return;
      }
      if (msg.type === 'result') {
        const p = pending.get(String(msg.id));
        if (!p) return;
        pending.delete(String(msg.id));
        clearTimeout(p.timer);
        if (msg.ok) p.resolve({ ok: true, value: msg.value });
        else p.reject(new SandboxError(String((msg.error && msg.error.code) || 'plugin_error'), String((msg.error && msg.error.message) || 'plugin error'), msg.error || null));
        return;
      }
      if (msg.type === 'bye') {
        try { socket.destroy(); } catch { /* noop */ }
      }
    }
  });
}

// ── 5. 宿主侧编排：Manifest 解析 → 静态裁决 → 逐个沙箱加载 ───────────────
// index.js 只需调用本函数；审计事件通过 audit 回调注入（index.js 的 audit.jsonl）。
// 返回的 summary 会被写进插件状态，供 lingshu_status / 控制台观测。
export async function bootstrapSupplyChain({ pluginDirs = [], audit = () => {}, opts = {} } = {}) {
  const dirs = (Array.isArray(pluginDirs) ? pluginDirs : [])
    .filter((d) => typeof d === 'string' && d.length > 0);
  const summary = {
    enabled: dirs.length > 0,
    policy_version: POLICY_VERSION,
    policy_source: POLICY_SOURCE,
    policy_degraded: POLICY_DEGRADED,
    sandbox_supported: false,
    verdict: dirs.length ? 'pending' : 'idle',
    plugins: [],
    suspended: false,
    manifests: 0,
    findings: 0,
  };
  if (!dirs.length) return summary;

  // 1) 探测宿主是否支持 Node permission model（失败 → 全部 deny，不降级）
  const probe = await detectSandboxSupport(opts.probe || {});
  summary.sandbox_supported = probe.supported === true;

  // 2) 逐个 Manifest 解析 + 静态裁决，先把"每个插件看到了什么"落到审计
  const decisions = [];
  for (const dir of dirs) {
    let decision = null;
    try {
      decision = evaluatePluginDir(dir, { sandboxSupported: probe.supported }).decision;
    } catch (err) {
      decision = { manifest_id: null, verdict: 'deny', reason: 'host_exception', error_code: 'SC-EX',
        findings: [], failures: [{ code: 'host_exception', detail: String(err && err.message || err) }],
        fingerprint: null, capabilities: { requested: [], granted: [], denied: [] } };
    }
    decisions.push({ dir, decision });
    summary.manifests += 1;
    summary.findings += Array.isArray(decision.findings) ? decision.findings.length : 0;
    try {
      audit('supply_chain_manifest', {
        plugin_dir: dir,
        plugin_id: decision.manifest_id || null,
        verdict: decision.verdict,
        reason: decision.reason,
        error_code: decision.error_code || null,
        findings: (decision.findings || []).slice(0, 10).map((f) => ({ id: f.id, capability: f.capability, line: f.line })),
        capabilities: decision.capabilities || null,
        fingerprint: decision.fingerprint || null,
        policy_version: POLICY_VERSION,
        sandbox_supported: probe.supported === true,
      });
    } catch { /* 审计失败不得影响加载流程 */ }
  }

  // 3) 真正的加载：静态 deny ≥1 时 loadAll 会立刻挂起（fail-fast），不 spawn 后续插件
  const loaded = await loadAllSandboxedPlugins(dirs, { ...opts, sandboxSupported: probe.supported });
  for (const r of loaded.results) {
    if (r.ok) {
      summary.plugins.push({ plugin_dir: r.pluginDir, plugin_id: r.decision.manifest_id, pid: r.pid,
        exports: r.decision.exports || [], verdict: 'allow_sandboxed' });
      try {
        audit('supply_chain', {
          verdict: 'allow_sandboxed', plugin_id: r.decision.manifest_id, pid: r.pid,
          exports: r.decision.exports || [], fingerprint: r.decision.fingerprint,
          capabilities: r.decision.capabilities, policy_version: POLICY_VERSION,
          sandbox_supported: true, suspended: false,
        });
      } catch { /* noop */ }
    } else {
      summary.plugins.push({ plugin_dir: r.pluginDir, plugin_id: (r.decision && r.decision.manifest_id) || null,
        verdict: 'deny', reason: r.skipped ? 'loader_suspended' : (r.decision && r.decision.reason) || 'unknown',
        error_code: r.skipped ? 'SC-E20' : ((r.decision && r.decision.error_code) || 'SC-EX') });
      try {
        audit('supply_chain', denyAuditEvent(r, { pluginDir: r.pluginDir || '' }));
      } catch { /* noop */ }
    }
  }
  const state = getSandboxState();
  summary.suspended = state.suspended;
  summary.verdict = summary.plugins.some((p) => p.verdict === 'deny') ? 'deny' : 'allow_sandboxed';
  return summary;
}

// 供 index.js 使用：把 deny 转成一条审计事件体（不写盘，由调用方决定审计文件）。
export function denyAuditEvent(result, { pluginDir = '' } = {}) {
  const d = result && result.decision ? result.decision : {};
  return {
    kind: 'supply_chain',
    verdict: 'deny',
    reason: String(d.reason || (result && result.reason) || 'unknown'),
    error_code: String(d.error_code || 'SC-EX'),
    plugin_id: d.manifest_id || null,
    fingerprint: d.fingerprint || null,
    findings: Array.isArray(d.findings) ? d.findings.slice(0, 10) : [],
    failures: Array.isArray(d.failures) ? d.failures.map((f) => f.code) : [],
    policy_version: POLICY_VERSION,
    suspended: true,
    plugin_dir: pluginDir,
    host_hook: 'supply-chain/loadSandboxedPlugin',
  };
}

// CLI 自检：node sandbox-host.mjs --probe
if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  if (process.argv.includes('--probe')) {
    detectSandboxSupport().then((p) => {
      process.stdout.write(JSON.stringify(p) + '\n');
      process.exit(p.supported ? 0 : 1);
    });
  }
}
