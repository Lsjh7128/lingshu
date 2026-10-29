// sandbox-child.mjs —— 第三方插件的沙箱子进程（运行在 Node permission model 之下）
//
// 它由 sandbox-host.mjs 用 `node --permission --allow-fs-read=... --allow-fs-write=...` 启动。
// 设计要点（任何一条都别改，改了物理边界就没了）：
//   · 子进程**不接收**入口路径以外的宿主能力：配置由父进程经 TCP 一次性下发；
//   · 只把 manifest 声明的 exports 暴露成 RPC 方法，其它名字一律拒绝；
//   · 权限模型是权威边界：越界 fs 写、child_process、addon 会抛 ERR_ACCESS_DENIED；
//     本脚本只负责把**未捕获**的违规上报父进程（插件若吞掉异常，OS 层仍然拒绝副作用）。
//   · 父进程挂掉 / 连接断 → 子进程自杀，不留在后台。
//
// 协议：TCP 127.0.0.1（父进程监听），newline-delimited JSON。
//   child → parent : {type:"hello", token, pid}
//   parent → child : {type:"init", pluginId, entry, exports, pluginDir}
//   child → parent : {type:"ready", exports:[...], node, pid}
//   parent → child : {type:"call", id, method, params}
//   child → parent : {type:"result", id, ok, value|error}
//   child → parent : {type:"violation", code, message} / {type:"fatal", code, message}
//   parent → child : {type:"close"}

import net from 'node:net';
import readline from 'node:readline';
import { pathToFileURL } from 'node:url';

const PORT = Number(process.env.LINGSHU_SC_PORT || 0);
const TOKEN = String(process.env.LINGSHU_SC_TOKEN || '');
const MAX_MESSAGE_BYTES = 1024 * 1024;

if (!PORT || !TOKEN) {
  // 没有父进程下发的连接参数 = 不允许独立运行
  process.exit(64);
}

const state = {
  pluginId: '',
  entry: '',
  pluginDir: '',
  exports: [],
  moduleExports: null,
  connected: false,
  closed: false,
};

function send(msg) {
  if (!state.connected || state.closed) return;
  try {
    const text = JSON.stringify(msg);
    if (Buffer.byteLength(text, 'utf8') > MAX_MESSAGE_BYTES) return;
    socket.write(text + '\n');
  } catch { /* 连接已断：按协议不该发生；吞掉避免插件把子进程搞崩 */ }
}

function serializeError(err) {
  const out = { name: String((err && err.name) || 'Error'), message: String((err && err.message) || err) };
  if (err && typeof err.code === 'string') out.code = err.code;
  if (err && typeof err.stack === 'string') out.stack = err.stack.slice(0, 800);
  return out;
}

const socket = net.connect(PORT, '127.0.0.1', () => {
  state.connected = true;
  send({ type: 'hello', token: TOKEN, pid: process.pid });
});
socket.setNoDelay(true);
socket.setEncoding('utf8');

// 父进程断开 = 子进程失去监管，自杀（避免孤儿插件进程）
socket.on('close', () => { state.closed = true; process.exit(0); });
socket.on('error', () => { process.exit(1); });

process.on('uncaughtException', (err) => {
  send({ type: 'violation', code: 'uncaught_exception', error: serializeError(err) });
  // 违规后不再接受调用：让父进程看到并 kill（fail-closed）
  setTimeout(() => process.exit(70), 50);
});
process.on('unhandledRejection', (err) => {
  send({ type: 'violation', code: 'unhandled_rejection', error: serializeError(err) });
  setTimeout(() => process.exit(70), 50);
});

const rl = readline.createInterface({ input: socket, crlfDelay: Infinity });

async function activate(init) {
  state.pluginId = String(init.pluginId || '');
  state.pluginDir = String(init.pluginDir || '');
  state.entry = String(init.entry || '');
  state.exports = Array.isArray(init.exports) ? init.exports.slice() : [];
  let mod;
  try {
    mod = await import(pathToFileURL(state.entry).href);
  } catch (err) {
    send({ type: 'fatal', code: 'entry_import_failed', error: serializeError(err) });
    setTimeout(() => process.exit(65), 50);
    return;
  }
  state.moduleExports = mod && (mod.default && typeof mod.default === 'object') ? mod.default : mod;
  const missing = state.exports.filter((name) => typeof state.moduleExports[name] !== 'function');
  if (missing.length) {
    send({ type: 'fatal', code: 'export_missing', missing });
    setTimeout(() => process.exit(66), 50);
    return;
  }
  send({ type: 'ready', exports: state.exports.slice(), node: process.version, pid: process.pid });
}

async function handleCall(msg) {
  const id = msg.id;
  const method = String(msg.method || '');
  if (!state.exports.includes(method)) {
    send({ type: 'result', id, ok: false, error: { name: 'SandboxError', code: 'method_not_allowed', message: `method not in manifest.exports: ${method}` } });
    return;
  }
  const fn = state.moduleExports[method];
  const params = msg.params && typeof msg.params === 'object' ? msg.params : {};
  try {
    const value = await fn(params);
    // 只允许 JSON 可序列化的返回值（否则跨进程序列化会产生隐式行为）
    let json;
    try { json = JSON.stringify(value === undefined ? null : value); }
    catch (err) { send({ type: 'result', id, ok: false, error: { name: 'SandboxError', code: 'result_not_serializable', message: String(err && err.message || err) } }); return; }
    send({ type: 'result', id, ok: true, value: JSON.parse(json) });
  } catch (err) {
    send({ type: 'result', id, ok: false, error: serializeError(err) });
  }
}

let initialized = false;
rl.on('line', (line) => {
  if (Buffer.byteLength(line, 'utf8') > MAX_MESSAGE_BYTES) { send({ type: 'fatal', code: 'message_too_large' }); return; }
  let msg;
  try { msg = JSON.parse(line); } catch { return; }
  if (msg.type === 'init' && !initialized) {
    initialized = true;
    activate(msg);
    return;
  }
  if (msg.type === 'call') { handleCall(msg); return; }
  if (msg.type === 'close') {
    state.closed = true;
    send({ type: 'bye' });
    setTimeout(() => process.exit(0), 30);
  }
});
