// dsh-lingshu —— 人格体中枢的宿主路由
//
// 面板是预先构建好的单文件静态页（ui/dashboard.html），由本模块在 webServer 上
// 提供 /dsh-lingshu/dashboard。iframe 同源加载，便于保留面板自己的
// React/Tailwind/Framer Motion 视觉与交互；宿主只负责静态分发，不引入额外依赖。
//
// 路由（全部同源；拒绝 Sec-Fetch-Site: cross-site）：
//   GET  /dsh-lingshu/api/status                     运行态快照（index.js#dashboardStatus）
//   GET  /dsh-lingshu/api/workspaces                 工作区列表（workspace-api.js#listWorkspaces）
//   POST /dsh-lingshu/api/workspaces                 创建**未激活**工作区（可选 persona）
//   GET  /dsh-lingshu/api/workspaces/:id             单工作区摘要
//   PUT  /dsh-lingshu/api/workspaces/:id/persona     写 persona.json（白名单 + 原子写）
//   POST /dsh-lingshu/api/workspaces/:id/activate    热切换当前工作区（无 body，turn 中 409）
//   GET  /dsh-lingshu/api/memory                     记忆面板只读视图（人话句子，不含原文 JSON）
//   POST /dsh-lingshu/api/memory/forget              单条移到回收站（追加墓碑，可恢复）
//   POST /dsh-lingshu/api/memory/trash/restore       从回收站恢复
//   POST /dsh-lingshu/api/memory/trash/purge         彻底删除（redact，留骨架）
//
// 边界：没有删除/迁移 API；写入口只有上面三个，白名单/原子写/审计/切换保护都在
// workspace-api.js 与 index.js#switchWorkspace 里做。不要在这里补假写操作，也不要把 Mock 数据混进真实响应。
// 记忆面板同样只走 provider（index.js#getMemory/memoryOp），不直连文件系统。

import fs from 'node:fs';
import { fileURLToPath } from 'node:url';

const DASHBOARD_FILE = fileURLToPath(new URL('./ui/dashboard.html', import.meta.url));
const MEMORY_PAGE_FILE = fileURLToPath(new URL('./ui/memory.html', import.meta.url));
const ROUTE = '/dsh-lingshu/dashboard';
const MEMORY_PAGE_ROUTE = '/dsh-lingshu/dashboard/memory';
const STATUS_ROUTE = '/dsh-lingshu/api/status';
const WORKSPACES_ROUTE = '/dsh-lingshu/api/workspaces';
const MEMORY_ROUTE = '/dsh-lingshu/api/memory';
// v0.2 人格体 / 绑定（人格舱）
const PERSONAS_ROUTE = '/dsh-lingshu/api/personas';
const BINDINGS_ROUTE = '/dsh-lingshu/api/bindings';
const AVAILABLE_ROUTE = '/dsh-lingshu/api/workspaces/available';
const PERSONAS_PAGE_ROUTE = '/dsh-lingshu/dashboard/personas';
const PERSONAS_PAGE_FILE = fileURLToPath(new URL('./ui/personas.html', import.meta.url));
// 与 workspace-api.js 的 persona 上限一致：面板请求体 64KB。
const JSON_BODY_MAX_BYTES = 64 * 1024;

function sendText(res, status, text) {
  const body = Buffer.from(text, 'utf8');
  res.statusCode = status;
  res.setHeader('content-type', 'text/plain; charset=utf-8');
  res.setHeader('content-length', String(body.byteLength));
  res.setHeader('cache-control', 'no-store');
  res.end(body);
}

function sendJson(res, status, value) {
  const body = Buffer.from(JSON.stringify(value), 'utf8');
  res.statusCode = status;
  res.setHeader('content-type', 'application/json; charset=utf-8');
  res.setHeader('content-length', String(body.byteLength));
  res.setHeader('cache-control', 'no-store');
  res.setHeader('x-content-type-options', 'nosniff');
  res.end(body);
}

function sendMethodNotAllowed(res, allow) {
  res.setHeader('allow', allow.join(', '));
  sendJson(res, 405, { ok: false, error: 'method_not_allowed', allow: allow.join(', ') });
}

function sendDashboard(res) {
  let html;
  try {
    html = fs.readFileSync(DASHBOARD_FILE);
  } catch (error) {
    sendText(res, 503, `LINGSHU dashboard 未随插件安装：${String(error?.message || error)}`);
    return;
  }
  res.statusCode = 200;
  res.setHeader('content-type', 'text/html; charset=utf-8');
  res.setHeader('content-length', String(html.byteLength));
  res.setHeader('cache-control', 'no-store');
  res.setHeader('x-content-type-options', 'nosniff');
  // 只允许 DSH 自身（web 与 dsh-app: 桌面外壳）嵌入；面板内联脚本/样式是构建产物。
  res.setHeader(
    'content-security-policy',
    "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; frame-ancestors 'self' dsh-app:;",
  );
  res.end(html);
}

/** 记忆面板 v1 静态页（同源 iframe 或直接打开；页面只通过 /api/memory 读写）。 */
function sendMemoryPage(res) {
  let html;
  try {
    html = fs.readFileSync(MEMORY_PAGE_FILE);
  } catch (error) {
    sendText(res, 503, `LINGSHU 记忆面板未随插件安装：${String(error?.message || error)}`);
    return;
  }
  res.statusCode = 200;
  res.setHeader('content-type', 'text/html; charset=utf-8');
  res.setHeader('content-length', String(html.byteLength));
  res.setHeader('cache-control', 'no-store');
  res.setHeader('x-content-type-options', 'nosniff');
  res.setHeader(
    'content-security-policy',
    "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; frame-ancestors 'self' dsh-app:;",
  );
  res.end(html);
}

/** 从 URL pathname 里取出 workspaces 路由之后的剩余部分（'' 表示列表路径本身）。 */
function workspacesRemainder(req) {
  let pathname;
  try {
    pathname = new URL(req.url ?? '/', 'http://dsh.local').pathname;
  } catch {
    return null;
  }
  if (!pathname.startsWith(WORKSPACES_ROUTE)) return null;
  return pathname.slice(WORKSPACES_ROUTE.length);
}

/**
 * 读 JSON 请求体：先查 content-type，再按上限累积，最后 parse。
 * 永远 resolve（不 reject），失败返回 {ok:false,status,error}；上游负责回响应。
 */
function readJsonBody(req, limitBytes) {
  return new Promise((resolve) => {
    const type = String(req.headers['content-type'] ?? '').split(';')[0].trim().toLowerCase();
    if (type !== 'application/json') {
      resolve({ ok: false, status: 415, error: 'content_type_must_be_application_json' });
      return;
    }
    const chunks = [];
    let size = 0;
    let settled = false;
    const finish = (value) => {
      if (settled) return;
      settled = true;
      resolve(value);
    };
    req.on('data', (chunk) => {
      if (settled) return;
      size += chunk.length;
      if (size > limitBytes) {
        finish({ ok: false, status: 413, error: 'payload_too_large', limitBytes });
        try { req.resume(); } catch { /* 丢弃剩余数据 */ }
        return;
      }
      chunks.push(chunk);
    });
    req.on('end', () => {
      if (settled) return;
      if (size === 0) {
        finish({ ok: false, status: 400, error: 'empty_body' });
        return;
      }
      let value;
      try {
        value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
      } catch {
        finish({ ok: false, status: 400, error: 'invalid_json' });
        return;
      }
      finish({ ok: true, value, bytes: size });
    });
    req.on('error', () => finish({ ok: false, status: 400, error: 'body_read_error' }));
  });
}

/** 把 workspace-api 的写结果映射成 HTTP 响应（状态码只接受 4xx/5xx，避免误传 200 当错误）。 */
function sendProviderResult(res, result, fallbackError) {
  if (!result || typeof result !== 'object') {
    sendJson(res, 500, { ok: false, error: fallbackError });
    return;
  }
  if (result.ok !== true) {
    const status = Number.isInteger(result.status) && result.status >= 400 && result.status <= 599
      ? result.status : 500;
    sendJson(res, status, result);
    return;
  }
  const status = Number.isInteger(result.status) && result.status >= 200 && result.status <= 299
    ? result.status : 200;
  sendJson(res, status, result);
}

/**
 * 注册人格体中枢静态路由、只读运行态/工作区 API 与两个白名单写入口。
 * @param {object} ctx - 带 webServer 服务的宿主上下文。
 * @param {object} [options]
 * @param {() => object} [options.getStatus]
 * @param {() => object} [options.getWorkspaces]
 * @param {(id:string) => object} [options.getWorkspace]
 * @param {(id:string, body:object) => object} [options.writePersona]  PUT :id/persona
 * @param {(body:object) => object} [options.createWorkspace]          POST /workspaces
 * @param {(id:string) => object} [options.activateWorkspace]          POST :id/activate（热切换）
 * @returns {() => void} 取消注册的 disposer。
 */
export function registerDashboardRoutes(ctx, options = {}) {
  const disposers = [];
  const registerRoute = (kind, routePath, handler, methods = ['GET', 'HEAD']) => {
    const dispose = ctx.webServer.register({
      kind,
      path: routePath,
      handler: async (req, res) => {
        // 只读面板/状态/工作区不做跨站服务：浏览器跨站 fetch 会带 Sec-Fetch-Site: cross-site。
        if (String(req.headers['sec-fetch-site'] ?? '').toLowerCase() === 'cross-site') {
          sendText(res, 403, 'cross-site request rejected');
          return;
        }
        if (!methods.includes(req.method)) {
          sendMethodNotAllowed(res, methods);
          return;
        }
        await handler(req, res);
      },
    });
    disposers.push(dispose);
  };
  const registerExact = (routePath, handler, methods) => registerRoute('exact', routePath, handler, methods);

  const pageHandler = (_req, res) => sendDashboard(res);
  registerExact(ROUTE, pageHandler);
  registerExact(`${ROUTE}/`, pageHandler);
  registerExact(MEMORY_PAGE_ROUTE, (_req, res) => sendMemoryPage(res));

  registerExact(STATUS_ROUTE, (_req, res) => {
    try {
      const snapshot = typeof options.getStatus === 'function' ? options.getStatus() : null;
      if (!snapshot || typeof snapshot !== 'object') {
        sendJson(res, 503, { ok: false, error: 'LINGSHU 运行态不可用' });
        return;
      }
      sendJson(res, 200, snapshot);
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error?.message || error) });
    }
  });

  const listHandler = (_req, res) => {
    try {
      const payload = typeof options.getWorkspaces === 'function' ? options.getWorkspaces() : null;
      if (!payload || typeof payload !== 'object' || payload.ok === false) {
        sendJson(res, 503, payload && typeof payload === 'object'
          ? payload
          : { ok: false, error: 'LINGSHU 工作区数据源不可用' });
        return;
      }
      sendJson(res, 200, payload);
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error?.message || error) });
    }
  };

  const createHandler = async (req, res) => {
    if (typeof options.createWorkspace !== 'function') {
      sendJson(res, 503, { ok: false, error: 'workspace_create_unavailable' });
      return;
    }
    const body = await readJsonBody(req, JSON_BODY_MAX_BYTES);
    if (!body.ok) {
      sendJson(res, body.status, {
        ok: false, error: body.error, ...(body.limitBytes ? { limitBytes: body.limitBytes } : {}),
      });
      return;
    }
    try {
      sendProviderResult(res, options.createWorkspace(body.value), 'workspace_create_failed');
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error?.message || error) });
    }
  };

  const detailHandler = (res, rawId) => {
    try {
      const result = typeof options.getWorkspace === 'function'
        ? options.getWorkspace(rawId)
        : { ok: false, status: 503, error: 'workspace_api_unavailable' };
      sendProviderResult(res, result, 'workspace_api_unavailable');
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error?.message || error) });
    }
  };

  const personaHandler = async (req, res, rawId) => {
    if (typeof options.writePersona !== 'function') {
      sendJson(res, 503, { ok: false, error: 'persona_write_unavailable' });
      return;
    }
    const body = await readJsonBody(req, JSON_BODY_MAX_BYTES);
    if (!body.ok) {
      sendJson(res, body.status, {
        ok: false, error: body.error, ...(body.limitBytes ? { limitBytes: body.limitBytes } : {}),
      });
      return;
    }
    try {
      sendProviderResult(res, options.writePersona(rawId, body.value), 'persona_write_failed');
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error?.message || error) });
    }
  };

  // exact 表先匹配 /api/workspaces（GET 列表 / POST 创建）；其余 /<id>、/<id>/persona、/<id>/activate 走前缀路由。
  registerExact(WORKSPACES_ROUTE, (req, res) => {
    if (req.method === 'POST') return createHandler(req, res);
    listHandler(req, res);
    return undefined;
  }, ['GET', 'HEAD', 'POST']);

  // 热切换：POST /api/workspaces/:id/activate（无 body）。
  // 契约错误码由 provider 给：400 invalid_workspace_id / 404 workspace_not_found / 409 turn_in_progress。
  const activateHandler = (req, res, rawId) => {
    if (typeof options.activateWorkspace !== 'function') {
      sendJson(res, 503, { ok: false, error: 'workspace_activate_unavailable' });
      return undefined;
    }
    // 契约是"无 body"：不解析、不写任何东西；只把可能存在的 body 排空，避免 keep-alive 连接挂住。
    try { req.resume(); } catch { /* noop */ }
    try {
      sendProviderResult(res, options.activateWorkspace(rawId), 'workspace_activate_failed');
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error?.message || error) });
    }
    return undefined;
  };

  // 前缀路由只接受 "/<单段 id>"、"/<单段 id>/persona"、"/<单段 id>/activate"；其它一律 404（不猜）。
  registerRoute('prefix', WORKSPACES_ROUTE, (req, res) => {
    const rest = workspacesRemainder(req);
    const detail = typeof rest === 'string' ? /^\/([^/]+)$/.exec(rest) : null;
    const persona = typeof rest === 'string' ? /^\/([^/]+)\/persona$/.exec(rest) : null;
    const activate = typeof rest === 'string' ? /^\/([^/]+)\/activate$/.exec(rest) : null;
    if (!detail && !persona && !activate) {
      sendJson(res, 404, { ok: false, error: 'not_found' });
      return;
    }
    let rawId;
    try {
      rawId = decodeURIComponent((detail || persona || activate)[1]);
    } catch {
      sendJson(res, 400, { ok: false, error: 'invalid_workspace_id' });
      return;
    }
    if (activate) {
      if (req.method !== 'POST') {
        sendMethodNotAllowed(res, ['POST']);
        return undefined;
      }
      return activateHandler(req, res, rawId);
    }
    if (persona) {
      if (req.method !== 'PUT') {
        sendMethodNotAllowed(res, ['PUT']);
        return undefined;
      }
      return personaHandler(req, res, rawId);
    }
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      sendMethodNotAllowed(res, ['GET', 'HEAD']);
      return undefined;
    }
    detailHandler(res, rawId);
    return undefined;
  }, ['GET', 'HEAD', 'PUT', 'POST']);

  // ── 记忆面板 v1（D14/D15/§5）：只读视图 + 单条墓碑删除 + 回收站 ──────────
  // 所有数据都走 provider（index.js#getMemory/memoryOp），本文件不读记忆文件、不拼假数据。
  const memoryListHandler = (req, res) => {
    if (typeof options.getMemory !== 'function') {
      sendJson(res, 503, { ok: false, error: 'memory_api_unavailable' });
      return;
    }
    let q = '';
    try {
      q = new URL(req.url ?? '/', 'http://dsh.local').searchParams.get('q') ?? '';
    } catch { q = ''; }
    try {
      sendProviderResult(res, options.getMemory({ q }), 'memory_api_failed');
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error?.message || error) });
    }
  };
  const memoryWriteHandler = (provider) => async (req, res) => {
    if (typeof options[provider] !== 'function') {
      sendJson(res, 503, { ok: false, error: 'memory_api_unavailable' });
      return;
    }
    const body = await readJsonBody(req, JSON_BODY_MAX_BYTES);
    if (!body.ok) {
      sendJson(res, body.status, { ok: false, error: body.error });
      return;
    }
    try {
      sendProviderResult(res, options[provider](body.value), 'memory_api_failed');
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error?.message || error) });
    }
  };
  registerExact(MEMORY_ROUTE, memoryListHandler, ['GET', 'HEAD']);
  registerExact(`${MEMORY_ROUTE}/forget`, memoryWriteHandler('forgetMemory'), ['POST']);
  registerExact(`${MEMORY_ROUTE}/trash/restore`, memoryWriteHandler('restoreMemory'), ['POST']);
  registerExact(`${MEMORY_ROUTE}/trash/purge`, memoryWriteHandler('purgeMemory'), ['POST']);

  // ── 人格体 / 人格舱（绑定）面板 ────────────────────────────────
  const personasGet = (_req, res) => {
    try {
      const out = typeof options.getPersonas === 'function'
        ? options.getPersonas() : { ok: false, status: 503, error: 'personas_api_unavailable' };
      sendProviderResult(res, out, 'personas_api_failed');
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error?.message || error) });
    }
  };
  const personasWrite = (provider, fallback) => async (req, res) => {
    if (typeof options[provider] !== 'function') {
      sendJson(res, 503, { ok: false, error: fallback });
      return;
    }
    const body = await readJsonBody(req, JSON_BODY_MAX_BYTES);
    if (!body.ok) {
      sendJson(res, body.status, { ok: false, error: body.error });
      return;
    }
    try {
      sendProviderResult(res, options[provider](body.value), fallback);
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error?.message || error) });
    }
  };
  const createPersonaHandler = personasWrite('createPersona', 'persona_create_failed');
  registerExact(PERSONAS_ROUTE, (req, res) => (
    req.method === 'POST' ? createPersonaHandler(req, res) : personasGet(req, res)
  ), ['GET', 'HEAD', 'POST']);
  registerExact(BINDINGS_ROUTE, personasWrite('bindWorkspace', 'binding_failed'), ['POST']);
  registerExact(`${BINDINGS_ROUTE}/unbind`, personasWrite('unbindWorkspace', 'unbind_failed'), ['POST']);
  registerExact(AVAILABLE_ROUTE, (_req, res) => {
    try {
      const out = typeof options.getAvailableWorkspaces === 'function'
        ? options.getAvailableWorkspaces() : { ok: false, status: 503, error: 'available_api_unavailable' };
      sendProviderResult(res, out, 'available_api_failed');
    } catch (error) {
      sendJson(res, 500, { ok: false, error: String(error?.message || error) });
    }
  }, ['GET', 'HEAD']);
  registerExact(PERSONAS_PAGE_ROUTE, (_req, res) => {
    let html;
    try {
      html = fs.readFileSync(PERSONAS_PAGE_FILE);
    } catch (error) {
      sendText(res, 503, `LINGSHU 人格体面板未随插件安装：${String(error?.message || error)}`);
      return;
    }
    res.statusCode = 200;
    res.setHeader('content-type', 'text/html; charset=utf-8');
    res.setHeader('content-length', String(html.byteLength));
    res.setHeader('cache-control', 'no-store');
    res.setHeader('x-content-type-options', 'nosniff');
    res.setHeader('content-security-policy',
      "default-src 'self' 'unsafe-inline' 'unsafe-eval' data: blob:; frame-ancestors 'self' dsh-app:;");
    res.end(html);
  });

  return () => {
    for (const dispose of disposers) {
      try {
        dispose();
      } catch {
        // 卸载路径不抛异常
      }
    }
  };
}
