// memory-paths.js —— v0.2 布局 / 作用域记忆的 **JS 运行时镜像**
//
// 语义源：`engine/layout.py`（施工单 v2 §3 §4 §6）。D4：Python 是唯一语义来源，
// 本文件逐函数镜像，**行为必须与 Python 一致**；parity 由 `memory-paths-sim.mjs
// --parity` + `tests/test_v02_parity.py` 双向校验。改这里之前先改 layout.py。
//
// 约定：
// - 返回的数据对象一律 snake_case，与 Python 返回的 dict 逐键一致；
// - 函数入参允许 camelCase（JS 习惯），内部立即归一；
// - 零依赖，只用 node:fs / node:path / node:crypto / node:os。
//
// 范围（只做施工单列出的，不扩展）：
//   D6/D7 layout.json + canonicalPath → sandboxId；D1/D2 六键路径；
//   D8 墓碑/恢复事件 + 折叠；D9 local over global + 配额；§4.3 scope 路由；
//   §4.4 隔离校验；D10 目录锁；§4.6 热重载；S4 UCA-01 纯函数；S5 审计常量；S3 skeleton 段。
// 另外提供 `MemoryRuntime`（面板/index.js 用的薄运行时），它**不是** Python 镜像，
// 只是把上面的纯函数缝到文件上：append-only、路由、折叠、回收站。

import fs from 'node:fs';
import path from 'node:path';
import os from 'node:os';
import crypto from 'node:crypto';

// ── 0. 版本与常量（镜像 chapter 0）──────────────────────────

export const LAYOUT_SCHEMA = 'layout-0.2';
export const SANDBOX_INDEX_SCHEMA = 'sandbox-index-0.1';
export const SANDBOX_SCHEMA = 'sandbox-0.1';
export const PERSONA_STATE_SCHEMA = 'persona-state-0.1';
export const STATE_SCHEMA = 'state-0.1';
export const MAPPING_SCHEMA = 'mapping-0.1';

export const SANDBOX_ID_RE = /^[0-9a-f]{8}$/;
export const PERSONA_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;

// S3：skeleton 注入段（宪法 100 之后、事实 110 之前；空串保证零输出）。
export const SKELETON_SECTION = { name: 'lingshu:skeleton', order: 105 };

// S5：本期新增 kind（会真实产生）+ skeleton 预留（常量存在、不产生）。
export const AUDIT_KINDS_V02 = [
  'panel_forget',
  'panel_restore',
  'panel_purge',
  'migration_apply',
  'sandbox_mapped',
];
export const AUDIT_KINDS_RESERVED = [
  'skeleton_generated',
  'skeleton_rejected',
  'skeleton_rebuilt',
];

// §4.2 配额：local 保底 1 条；global 占注入预算 ≤ 30%。
export const GLOBAL_CAP_RATIO = 0.30;
export const LOCAL_FLOOR = 1;
export const DEFAULT_TOKEN_BUDGET = 900;
export const DEFAULT_TOP_K = 3;
export const PER_ITEM_OVERHEAD = 8;
export const CHARS_PER_TOKEN = 3;

// ── 1. 规范化与 sandboxId（D7 / §4.1）──────────────────────

const IS_WIN32 = process.platform === 'win32';

// Python `_is_root`：盘符根或 POSIX 根。
function _isRoot(s) {
  return /^(?:[A-Za-z]:\/|\/)$/.test(s);
}

// Python `raw_unc`：`//server` 前缀（折叠后其实已不成立，保留为防御性原样镜像）。
function rawUnc(s) {
  return /^\/\/[^/]/.test(s);
}

/**
 * canonicalPath → 唯一字符串。与 Python canonicalize_path **完全同序**：
 * 反斜杠归一 → 折叠重复分隔符 → 去尾分隔符（根除外）→ realpath 解 junction
 * → 再归一 → NFC → Windows 小写。
 *
 * @param {unknown} p 任意路径值（null/undefined 抛错，与 Python 一致）
 * @param {{realpath?: (s: string) => string, windows?: boolean}} [opts]
 */
export function canonicalizePath(p, { realpath, windows } = {}) {
  if (p === null || p === undefined) throw new Error('canonicalize_path: path required');
  let s = String(p);
  s = s.replace(/\\/g, '/');
  s = s.replace(/\/{2,}/g, '/');
  if (rawUnc(s)) s = '/' + s; // 防御性：本期不做网络盘
  if (s.length > 1 && s.endsWith('/') && !_isRoot(s)) s = s.replace(/\/+$/, '');
  const rp = typeof realpath === 'function' ? realpath : (x) => fs.realpathSync.native(x);
  try {
    s = rp(s);
  } catch {
    // Python：OSError/ValueError 直接放行原串（不存在的路径照常返回规范化结果）
  }
  s = String(s).replace(/\\/g, '/');
  s = s.replace(/\/{2,}/g, '/');
  if (s.length > 1 && s.endsWith('/') && !_isRoot(s)) s = s.replace(/\/+$/, '');
  s = s.normalize('NFC');
  const isWin = windows === undefined ? IS_WIN32 : Boolean(windows);
  if (isWin) s = s.toLowerCase();
  return s;
}

/** D7：sha256(canonicalPath) 前 8 位 hex。 */
export function sandboxId(canonicalPath) {
  return crypto.createHash('sha256').update(String(canonicalPath), 'utf8').digest('hex').slice(0, 8);
}

export function sandboxIdForPath(p, opts = {}) {
  return sandboxId(canonicalizePath(p, opts));
}

// ── 2. 六键路径（D1/D2/D17 / S1）───────────────────────────

function _checkSandboxId(sid) {
  const s = String(sid || '');
  if (!SANDBOX_ID_RE.test(s)) throw new Error('resolve_memory_paths: invalid sandbox_id (fail-closed)');
  return s;
}

function _checkPersonaId(pid) {
  const s = String(pid || '');
  if (!PERSONA_ID_RE.test(s) || s === '.' || s === '..') {
    throw new Error('resolve_memory_paths: invalid persona_id (fail-closed)');
  }
  return s;
}

function _abspath(p) {
  return path.resolve(String(p));
}

// Python `_under_root`：normcase(abspath(...)) 后做前缀比较，防越界。
function _underRoot(root, p) {
  const norm = (x) => {
    let s = _abspath(x);
    if (IS_WIN32) s = s.toLowerCase();
    return s.replace(/[\\/]+$/, '');
  };
  const r = norm(root);
  const q = norm(p);
  return q === r || q.startsWith(r + path.sep);
}

/**
 * 唯一路径出口（§4.4.6）。返回**恰好**六个键，全部在数据根之下。
 * S1 验收键：globalMemory / localMemory / pending / skeleton / state / personaState
 * 非法 id 一律抛错（fail-closed）。
 */
export function resolveMemoryPaths(dataRoot, sandboxId_, personaId_) {
  const root = _abspath(dataRoot);
  const sid = _checkSandboxId(sandboxId_);
  const pid = _checkPersonaId(personaId_);
  const personaDir = path.join(root, 'personas', pid);
  const sandboxPersonaDir = path.join(root, 'sandboxes', sid, pid);
  const paths = {
    globalMemory: path.join(personaDir, 'global_memory.jsonl'),
    localMemory: path.join(sandboxPersonaDir, 'local_memory.jsonl'),
    pending: path.join(personaDir, 'global_pending.jsonl'),
    skeleton: path.join(sandboxPersonaDir, 'skeleton.md'),
    state: path.join(sandboxPersonaDir, 'state.json'),
    personaState: path.join(personaDir, 'persona_state.json'),
  };
  for (const [key, p] of Object.entries(paths)) {
    if (!_underRoot(root, p)) throw new Error(`resolve_memory_paths: ${key} escapes data root`);
  }
  return paths;
}

// ── 3. 布局识别与 layout.json（D6）─────────────────────────

export function readJson(filePath) {
  try {
    let text = fs.readFileSync(String(filePath), 'utf8');
    if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
    const doc = JSON.parse(text);
    return doc && typeof doc === 'object' && !Array.isArray(doc) ? doc : null;
  } catch {
    return null;
  }
}

export function writeJson(filePath, value) {
  try {
    const abs = _abspath(filePath);
    fs.mkdirSync(path.dirname(abs), { recursive: true });
    const tmp = `${abs}.tmp-${process.pid}-${crypto.randomBytes(4).toString('hex')}`;
    fs.writeFileSync(tmp, JSON.stringify(value, null, 2) + '\n', 'utf8');
    fs.renameSync(tmp, abs);
    return { ok: true };
  } catch (err) {
    return { ok: false, error: String((err && err.code) || 'io_error') };
  }
}

export function readLayout(dataRoot) {
  const doc = readJson(path.join(String(dataRoot), 'layout.json'));
  if (!doc || doc.schema_version !== LAYOUT_SCHEMA) return null;
  return doc;
}

/**
 * 写 layout.json。幂等：已存在则拒绝二次迁移（§7）。
 * @returns {{ok: true, layout: object} | {ok: false, reason?: string, error?: string}}
 */
export function writeLayout(dataRoot, { migratedFrom = [], now } = {}) {
  const root = String(dataRoot);
  if (readLayout(root) !== null) return { ok: false, reason: 'layout_exists' };
  const created = now === undefined || now === null ? Math.trunc(Date.now() / 1000) : Math.trunc(Number(now));
  const doc = {
    schema_version: LAYOUT_SCHEMA,
    created_at: created,
    migrated_from: [...new Set((migratedFrom || []).map(String))].sort(),
  };
  const res = writeJson(path.join(root, 'layout.json'), doc);
  if (res.ok) res.layout = doc;
  return res;
}

function _isDir(p) {
  try {
    return fs.statSync(p).isDirectory();
  } catch {
    return false;
  }
}

function _isFile(p) {
  try {
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}

/** 识别四代布局：v0.2 / legacy / workspace_<id> / profiles/<tag> / empty。 */
export function detectLayout(dataRoot) {
  const root = _abspath(dataRoot);
  const doc = readLayout(root);
  if (doc !== null) {
    const mf = doc.migrated_from;
    let sources = [];
    if (Array.isArray(mf)) sources = mf.slice();
    else if (typeof mf === 'string') sources = mf.split('');
    return { kind: 'v02', layout: LAYOUT_SCHEMA, sources };
  }
  const sources = [];
  if (_isFile(path.join(root, 'state.json'))) sources.push('legacy');
  let names = [];
  try {
    names = fs.readdirSync(root).sort();
  } catch {
    names = [];
  }
  for (const name of names) {
    const full = path.join(root, name);
    if (_isDir(full) && /^workspace_[A-Za-z0-9_-]{1,32}$/.test(name)) sources.push(name);
    if (name === 'profiles' && _isDir(full)) {
      let tags = [];
      try {
        tags = fs.readdirSync(full).sort();
      } catch {
        tags = [];
      }
      for (const tag of tags) {
        if (_isDir(path.join(full, tag))) sources.push(`profiles/${tag}`);
      }
    }
  }
  return { kind: sources.length ? 'legacy' : 'empty', layout: null, sources };
}

// ── 3b. sandbox_mapping.yaml（人格舱 ↔ 人格体绑定）──────────
// 新模型：绑定是唯一身份来源；没有绑定 = 不加载人格、不读不写记忆（fail-closed）。
// 与 engine/layout.py 的 read_mapping_bindings / write_mapping_bindings / resolve_binding 逐函数镜像。

export function readMappingBindings(dataRoot) {
  const file = path.join(_abspath(dataRoot), 'sandbox_mapping.yaml');
  let raw = '';
  try {
    raw = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
  } catch {
    return [];
  }
  const out = [];
  let cur = null;
  for (const line of raw.split(/\r?\n/)) {
    const item = /^\s*-\s*canonical_path:\s*"?(.+?)"?\s*$/.exec(line);
    if (item) {
      cur = { canonical_path: item[1] };
      out.push(cur);
      continue;
    }
    if (!cur) continue;
    const kv = /^\s*(sandbox_id|default_persona|label|bound_at):\s*"?(.*?)"?\s*$/.exec(line);
    if (kv) cur[kv[1]] = kv[2];
  }
  return out;
}

function _yamlQuote(value) {
  return `"${String(value ?? '').replace(/\\/g, '\\\\').replace(/"/g, '\\"')}"`;
}

/** 整份重写（原子）；绑定按 canonical_path 排序，保证 diff 稳定。 */
export function writeMappingBindings(dataRoot, bindings = []) {
  const root = _abspath(dataRoot);
  const rows = [];
  for (const b of bindings || []) {
    const cp = String(b?.canonical_path ?? '').trim();
    if (!cp) continue;
    rows.push({
      canonical_path: canonicalizePath(cp),
      sandbox_id: String(b.sandbox_id ?? ''),
      default_persona: String(b.default_persona ?? ''),
      label: String(b.label ?? ''),
      bound_at: String(b.bound_at ?? ''),
    });
  }
  rows.sort((a, b) => (a.canonical_path < b.canonical_path ? -1 : a.canonical_path > b.canonical_path ? 1 : 0));
  const lines = ['schema_version: mapping-0.1', 'bindings:'];
  for (const b of rows) {
    lines.push(`  - canonical_path: ${_yamlQuote(b.canonical_path)}`);
    for (const key of ['sandbox_id', 'default_persona', 'label', 'bound_at']) {
      if (b[key] !== '') lines.push(`    ${key}: ${_yamlQuote(b[key])}`);
    }
  }
  const file = path.join(root, 'sandbox_mapping.yaml');
  try {
    fs.mkdirSync(root, { recursive: true });
    const tmp = `${file}.tmp-${process.pid}-${crypto.randomBytes(4).toString('hex')}`;
    fs.writeFileSync(tmp, lines.join('\n') + '\n', 'utf8');
    fs.renameSync(tmp, file);
    return { ok: true, bindings: rows };
  } catch (err) {
    return { ok: false, error: String(err?.code || 'io_error') };
  }
}

/** 工作区 → 绑定；没有绑定返回 null（调用方必须进入"无人格"模式）。 */
export function resolveBinding(dataRoot, canonicalPath) {
  const target = canonicalizePath(canonicalPath);
  for (const b of readMappingBindings(dataRoot)) {
    if (canonicalizePath(String(b.canonical_path ?? '')) === target) return b;
  }
  return null;
}

// ── 4. JSONL 与墓碑折叠（D8 / §4.5）──────────────────────

/** 读 JSONL：坏行跳过、BOM 容错、非对象行丢弃；文件缺失返回 []。 */
export function readJsonl(filePath) {
  let text;
  try {
    text = fs.readFileSync(String(filePath), 'utf8');
  } catch {
    return [];
  }
  if (text.charCodeAt(0) === 0xfeff) text = text.slice(1);
  const rows = [];
  for (const raw of text.split(/\r\n|\r|\n/)) {
    const line = raw.trim().replace(/^\uFEFF/, '');
    if (!line) continue;
    let row;
    try {
      row = JSON.parse(line);
    } catch {
      continue;
    }
    if (row && typeof row === 'object' && !Array.isArray(row)) rows.push(row);
  }
  return rows;
}

/** 追加型 JSONL 序列化（与 Python dumps_jsonl 一致：一行一对象 + 换行）。 */
export function dumpsJsonl(rows) {
  return (rows || []).map((r) => JSON.stringify(r) + '\n').join('');
}

function _pyStr(v) {
  // Python `str(x)`：None → 'None'
  if (v === null || v === undefined) return 'None';
  return String(v);
}

function _cmpStr(a, b) {
  if (a < b) return -1;
  if (a > b) return 1;
  return 0;
}

/**
 * 读取时**按文件顺序**折叠（last-wins + 墓碑/恢复事件），绝不改历史行。
 *
 * 两种墓碑形态都认：
 *  1. 同 id 全量墓碑（status=suppressed 的整行，既有 memorywriter 行为）；
 *  2. 轻量墓碑事件 `{type:"tombstone", target, at, by}`（§4.5 面板删除）。
 * 恢复事件 `{type:"restore", target}` 只撤销**事件墓碑**；全量墓碑行不复活。
 *
 * @returns {{active: object[], suppressed: object[], superseded: object[]}}
 */
export function foldRows(rows) {
  const byId = new Map();
  const tomb = new Map(); // target -> 最近的 tombstone 事件
  for (const row of rows || []) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue;
    const kind = row.type;
    if (kind === 'tombstone' && row.target !== null && row.target !== undefined) {
      tomb.set(String(row.target), row);
      continue;
    }
    if (kind === 'restore' && row.target !== null && row.target !== undefined) {
      tomb.delete(String(row.target));
      continue;
    }
    if (row.id === null || row.id === undefined) continue;
    byId.set(String(row.id), row); // last-wins
  }

  const folded = new Map();
  for (const [rid, row] of byId) {
    if (tomb.has(rid) && row.status !== 'suppressed') {
      const ev = tomb.get(rid);
      folded.set(rid, {
        ...row,
        status: 'suppressed',
        suppressed_at: ev.at === undefined ? null : ev.at,
        suppressed_by: ev.by === undefined ? 'user_panel' : ev.by,
        suppressed_reason: ev.reason === undefined ? 'user_panel' : ev.reason,
      });
    } else {
      folded.set(rid, row);
    }
  }

  const active = [];
  const suppressed = [];
  const superseded = [];
  for (const rid of [...folded.keys()].sort()) {
    const row = folded.get(rid);
    const status = row.status === undefined ? 'active' : String(row.status);
    if (status === 'active') active.push(row);
    else if (status === 'suppressed') suppressed.push(row);
    else if (status === 'superseded' || status === 'dormant' || status === 'stale') superseded.push(row);
    else active.push(row);
  }
  return { active, suppressed, superseded };
}

function _nowSec(at) {
  return at === undefined || at === null ? Math.trunc(Date.now() / 1000) : Math.trunc(Number(at));
}

/** D8：遗忘 = **追加**一行墓碑事件，返回新列表（不改入参、不物理删行）。 */
export function applyTombstoneEvent(rows, target, { at, by = 'user_panel', reason = 'user_panel' } = {}) {
  const out = (rows || []).map((r) => ({ ...r }));
  out.push({
    type: 'tombstone',
    target: String(target),
    at: _nowSec(at),
    by: String(by),
    reason: String(reason),
  });
  return out;
}

/** §4.5：恢复 = 追加恢复事件（只撤销面板删除，scope 不重算），不删墓碑行。 */
export function applyRestoreEvent(rows, target, { at, by = 'user_panel' } = {}) {
  const out = (rows || []).map((r) => ({ ...r }));
  out.push({
    type: 'restore',
    target: String(target),
    at: _nowSec(at),
    by: String(by),
  });
  return out;
}

/** D16：彻底删除 = redact（抹内容留骨架），不删行。 */
export function purgeRedact(row, { at, by = 'user_panel' } = {}) {
  return {
    ...row,
    status: 'suppressed',
    content: '[已彻底删除]',
    display_text: '[已彻底删除]',
    sourceQuote: '',
    object: '',
    redacted_at: _nowSec(at),
    redacted_by: String(by),
  };
}

/** 面板展示唯一来源（§3.8）：一句人话。缺失时用 subject/object 兜底。 */
export function displayText(row) {
  const r = row || {};
  const text = String(r.display_text || '').trim();
  if (text) return text;
  const subject = String(r.subject || '').trim();
  const relation = String(r.relation || '').trim();
  const obj = String(r.object || '').trim();
  const piece = `${subject}${relation}${obj}`.trim();
  if (piece) return piece;
  const content = String(r.content || '').trim();
  if (content) return Array.from(content).slice(0, 80).join('');
  return `记忆 ${r.id === undefined ? '?' : r.id}`;
}

/** 回收站视图：只读 `suppressed`，不复制数据、不自动清除（D14/D15）。 */
export function trashView(rows, { now } = {}) {
  const t = now === undefined || now === null ? Date.now() / 1000 : Number(now);
  const out = [];
  for (const row of foldRows(rows).suppressed) {
    const at = row.suppressed_at || row.redacted_at;
    let age = null;
    if (at !== null && at !== undefined) {
      let num = Number(at);
      if (typeof at === 'string' && at.trim() === '') num = NaN;
      if (Number.isFinite(num)) age = Math.max(0, Math.floor((t - num) / 86400));
    }
    out.push({
      id: String(row.id),
      display_text: displayText(row),
      scope: String(row.scope || 'local'),
      sandbox_id: row.sandbox_id === undefined ? null : row.sandbox_id,
      suppressed_at: at === undefined ? null : at,
      days_in_trash: age,
      redacted: Object.prototype.hasOwnProperty.call(row, 'redacted_at'),
    });
  }
  return out;
}

// ── 5. 作用域路由（§4.3）─────────────────────────────────

export const PERSISTENT_RE = /(以后都|以后也要|永远|再也别|从此|一直|永久|无论哪个项目|不管在哪个项目|无论去哪)/;
export const PROJECT_RE = /(这个项目|本项目|本仓库|这个仓库|这次|当前项目|这个工程|本工程|当前仓库|当前工作区)/;

/**
 * §4.3：持久副词 + 无项目指代 → global；项目指代 → local；两者同时 → **local 胜**；
 * 都没有 → pending（待定区，默认不参与检索）。
 */
export function decideScope(text) {
  const s = String(text || '');
  const persistent = PERSISTENT_RE.test(s);
  const project = PROJECT_RE.test(s);
  const reasons = [];
  if (project) reasons.push('project_reference');
  if (persistent) reasons.push('persistent_adverb');
  if (project && persistent) return { scope: 'local', reasons: [...reasons, 'local_wins'], promotable: false };
  if (project) return { scope: 'local', reasons, promotable: false };
  if (persistent) return { scope: 'global', reasons, promotable: false };
  return { scope: 'pending', reasons: ['no_signal'], promotable: true };
}

// ── 6. 双层合并与冲突裁决（D9 / §4.2 §4.4）──────────────

function _rowTokens(row) {
  const dp = Array.from(displayText(row)).length;
  return PER_ITEM_OVERHEAD + Math.max(1, Math.ceil(dp / CHARS_PER_TOKEN));
}

function _rowPotency(row) {
  const v = row.potency !== undefined ? row.potency : row.importance !== undefined ? row.importance : 1.0;
  if (v === null) return 1.0;
  if (typeof v === 'string' && v.trim() === '') return 1.0;
  const n = Number(v);
  return Number.isFinite(n) ? n : 1.0;
}

/**
 * §4.2：merge → 行内标签校验 → potency → 冲突裁决 → 配额 → top-K。
 *
 * - 行内标签不符 → 丢弃（§4.4.5）；
 * - local over global（D9）：同 (subject, relation) 不同 object 时 local 胜；
 * - 配额：local 至少 `localFloor` 条（top-K 满时挤掉效力最低的 global）；global ≤ ratio × budget。
 */
export function mergeLayers(rows, {
  sandboxId,
  personaId,
  budget = DEFAULT_TOKEN_BUDGET,
  topK = DEFAULT_TOP_K,
  globalCapRatio = GLOBAL_CAP_RATIO,
  localFloor = LOCAL_FLOOR,
} = {}) {
  const sid = String(sandboxId || '');
  const pid = String(personaId || '');
  const dropped = [];
  const candidates = [];
  for (const row of rows || []) {
    if (!row || typeof row !== 'object' || Array.isArray(row)) continue;
    const scope = String(row.scope || '');
    if (scope === 'pending') {
      dropped.push({ id: row.id === undefined ? null : row.id, reason: 'pending_not_searchable' });
      continue;
    }
    if (scope !== 'global' && scope !== 'local') {
      dropped.push({ id: row.id === undefined ? null : row.id, reason: 'scope_label_mismatch' });
      continue;
    }
    if (String(row.persona_id || '') !== pid) {
      dropped.push({ id: row.id === undefined ? null : row.id, reason: 'persona_label_mismatch' });
      continue;
    }
    const rowSandbox = String(row.sandbox_id || '');
    if (scope === 'local' && rowSandbox !== sid && rowSandbox !== 'legacy') {
      // "legacy" 是迁移溯源标记（§7），迁移后落在当前沙盒的 local 文件里，视为当前。
      dropped.push({ id: row.id === undefined ? null : row.id, reason: 'sandbox_label_mismatch' });
      continue;
    }
    candidates.push({ ...row, scope });
  }

  // 冲突：同 (subject, relation)、不同 object，global 与 local 同时存在 → local 胜
  const conflicts = [];
  const byKey = new Map();
  for (const row of candidates) {
    const key = `${String(row.subject || '')}\u0000${String(row.relation || '')}`;
    if (!byKey.has(key)) {
      byKey.set(key, { subject: String(row.subject || ''), relation: String(row.relation || ''), global: [], local: [] });
    }
    byKey.get(key)[row.scope].push(row);
  }
  const loserIds = new Set();
  const groupEntries = [...byKey.values()].sort(
    (a, b) => _cmpStr(a.subject, b.subject) || _cmpStr(a.relation, b.relation),
  );
  for (const group of groupEntries) {
    const { subject, relation } = group;
    if (!group.global.length || !group.local.length) continue;
    const globalsObj = new Set(group.global.map((r) => String(r.object || '')));
    const localsObj = new Set(group.local.map((r) => String(r.object || '')));
    if (globalsObj.size === localsObj.size && [...globalsObj].every((x) => localsObj.has(x))) continue;
    for (const row of group.global) loserIds.add(_pyStr(row.id));
    conflicts.push({
      subject,
      relation,
      global_objects: [...globalsObj].sort(),
      local_objects: [...localsObj].sort(),
      winner: 'local',
      dropped_ids: group.global.map((r) => _pyStr(r.id)).sort(),
    });
  }

  const pool = [];
  for (const row of candidates) {
    if (loserIds.has(_pyStr(row.id))) {
      dropped.push({ id: row.id === undefined ? null : row.id, reason: 'conflict_local_wins' });
      continue;
    }
    pool.push(row);
  }
  pool.sort((a, b) => {
    const pa = -_rowPotency(a);
    const pb = -_rowPotency(b);
    if (Number.isNaN(pa)) return Number.isNaN(pb) ? 0 : 1; // NaN 放末尾（Python 未定义，取稳定行为）
    if (Number.isNaN(pb)) return -1;
    if (pa !== pb) return pa < pb ? -1 : 1;
    return _cmpStr(_pyStr(a.id), _pyStr(b.id));
  });

  const selected = [];
  let globalTokens = 0;
  let localCount = 0;
  let used = 0;
  const cap = Math.max(0.0, Number(globalCapRatio)) * Math.max(0, Math.trunc(Number(budget)));
  for (const row of pool) {
    if (selected.length >= topK) {
      dropped.push({ id: row.id === undefined ? null : row.id, reason: 'top_k' });
      continue;
    }
    const cost = _rowTokens(row);
    if (used + cost > budget) {
      dropped.push({ id: row.id === undefined ? null : row.id, reason: 'budget' });
      continue;
    }
    if (row.scope === 'global') {
      if (selected.length >= topK) continue;
      if (globalTokens + cost > cap) {
        dropped.push({ id: row.id === undefined ? null : row.id, reason: 'global_quota' });
        continue;
      }
      globalTokens += cost;
    } else {
      localCount += 1;
    }
    selected.push(row);
    used += cost;
  }

  // local 保底：若选中里 local 少于下限，用本地候选补足；
  // top-K 已满时**挤掉效力最低的 global**（local 保底优先于 global 配额）。
  if (localCount < localFloor) {
    const selectedIds = new Set(selected.map((r) => _pyStr(r.id)));
    for (const row of pool) {
      if (localCount >= localFloor) break;
      if (row.scope !== 'local' || selectedIds.has(_pyStr(row.id))) continue;
      if (selected.length >= topK) {
        const victims = selected.map((r, i) => (r.scope === 'global' ? i : -1)).filter((i) => i >= 0);
        if (!victims.length) break;
        selected.splice(victims[victims.length - 1], 1);
      }
      selected.push(row);
      selectedIds.add(_pyStr(row.id));
      localCount += 1;
      used += _rowTokens(row);
    }
  }

  return {
    selected,
    dropped,
    conflicts,
    tokens_used: used,
    token_budget: Math.trunc(Number(budget)),
    global_tokens: globalTokens,
    global_cap: Math.trunc(cap),
    local_count: localCount,
  };
}

// ── 7. 目录锁（D10 / §4.7）───────────────────────────────

export const LOCK_TIMEOUT_DEFAULT = 5.0;
export const LOCK_STALE_DEFAULT = 30.0;
export const LOCK_KEYS = ['memory', 'global', 'audit', 'skeleton', 'compaction'];

/** 拿不到锁（超时）。调用方必须 fail-closed，不得无锁写入。 */
export class LockTimeout extends Error {
  constructor(message) {
    super(message);
    this.name = 'LockTimeout';
  }
}

/** §4.7 锁键：分级保护，skeleton 独立于 memory（S2）。 */
export function lockKey(kind, { sandboxId, personaId, scope } = {}) {
  if (kind === 'memory') return `lock:memory:${_checkSandboxId(sandboxId || '')}`;
  if (kind === 'global') return `lock:global:${_checkPersonaId(personaId || '')}`;
  if (kind === 'audit') return 'lock:audit';
  if (kind === 'skeleton') return `lock:skeleton:${_checkSandboxId(sandboxId || '')}:${_checkPersonaId(personaId || '')}`;
  if (kind === 'compaction') return `lock:compaction:${scope || 'local'}`;
  throw new Error(`lock_key: unknown kind ${JSON.stringify(kind)}`);
}

function _pidAlive(pid) {
  if (!pid || pid <= 0) return false;
  try {
    // JS 的 signal 0 只做存在性探测，不发送任何信号、不杀进程。
    // 实测（本机 Python 3.12.14 / Windows）语义源的 os.kill(pid, 0) 同样只探测，
    // 未观察到终止子进程的副作用，因此两侧语义一致。
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function _sleepSync(ms) {
  // 同步 sleep：主线程可用，避免 async 化破坏 Lock 的同步 API。
  const sab = new SharedArrayBuffer(4);
  Atomics.wait(new Int32Array(sab), 0, 0, Math.max(0, ms));
}

/** 目录锁：`mkdir` 原子创建；持锁者信息写 `owner.json`；陈旧 30s 可抢占。 */
export class Lock {
  constructor(lockRoot, key, { timeout = LOCK_TIMEOUT_DEFAULT, stale = LOCK_STALE_DEFAULT, poll = 0.05 } = {}) {
    this.key = String(key);
    this.timeout = Number(timeout);
    this.stale = Number(stale);
    this.poll = Math.max(0.01, Number(poll));
    const digest = crypto.createHash('sha256').update(this.key, 'utf8').digest('hex').slice(0, 16);
    this.dir = path.join(_abspath(lockRoot), `${digest}.lock`);
    this.ownerFile = path.join(this.dir, 'owner.json');
    this.held = false;
    this._owner = {};
  }

  _tryAcquire() {
    try {
      fs.mkdirSync(path.dirname(this.dir), { recursive: true });
      fs.mkdirSync(this.dir);
    } catch (err) {
      return false; // EEXIST 或其它 OSError：一律视为未获得（fail-closed）
    }
    const host = process.env.COMPUTERNAME || os.hostname() || '';
    this._owner = {
      key: this.key,
      pid: process.pid,
      host: host || 'unknown',
      at: Date.now() / 1000,
      token: crypto.randomBytes(16).toString('hex'),
    };
    try {
      fs.writeFileSync(this.ownerFile, JSON.stringify(this._owner), 'utf8');
    } catch {
      // owner.json 落盘失败：仍持锁，靠目录存在性保证互斥；陈旧判定退化为目录 mtime
    }
    this.held = true;
    return true;
  }

  _isStale() {
    let owner = {};
    try {
      const raw = fs.readFileSync(this.ownerFile, 'utf8');
      const parsed = JSON.parse(raw);
      if (parsed && typeof parsed === 'object' && !Array.isArray(parsed)) owner = parsed;
    } catch {
      owner = {};
    }
    let at = owner.at === undefined || owner.at === null ? 0 : Number(owner.at);
    if (!Number.isFinite(at)) at = 0;
    if (!at) {
      // owner.json 还没落盘（正在创建）：按目录 mtime 判陈旧，避免误抢占
      try {
        at = fs.statSync(this.dir).mtimeMs / 1000;
      } catch {
        return false;
      }
    }
    if (Date.now() / 1000 - at > this.stale) return true;
    const pid = owner.pid;
    if (Number.isInteger(pid) && pid > 0 && pid !== process.pid && !_pidAlive(pid)) return true;
    return false;
  }

  acquire() {
    const deadline = Date.now() + this.timeout * 1000;
    for (;;) {
      if (this._tryAcquire()) return this;
      if (this._isStale()) {
        this._breakStale();
        // 与 Python 同语义：抢占后立刻重试；若目录仍在（抢占失败），不要死循环。
        if (fs.existsSync(this.dir)) {
          if (Date.now() >= deadline) {
            throw new LockTimeout(`lock timeout after ${this.timeout.toFixed(1)}s: ${this.key}`);
          }
          _sleepSync(this.poll * 1000);
        }
        continue;
      }
      if (Date.now() >= deadline) {
        throw new LockTimeout(`lock timeout after ${this.timeout.toFixed(1)}s: ${this.key}`);
      }
      _sleepSync(this.poll * 1000);
    }
  }

  _breakStale() {
    try {
      fs.unlinkSync(this.ownerFile);
    } catch {
      // 忽略
    }
    try {
      fs.rmdirSync(this.dir);
    } catch {
      // 忽略
    }
  }

  release() {
    if (!this.held) return;
    try {
      fs.unlinkSync(this.ownerFile);
    } catch {
      // 忽略
    }
    try {
      fs.rmdirSync(this.dir);
    } catch {
      // 忽略
    }
    this.held = false;
  }
}

// ── 8. 热重载判据（§4.6）────────────────────────────────

function _countLines(p) {
  try {
    const buf = fs.readFileSync(p);
    if (!buf.length) return 0;
    let n = 0;
    for (let i = 0; i < buf.length; i += 1) if (buf[i] === 0x0a) n += 1;
    if (buf[buf.length - 1] !== 0x0a) n += 1; // 末行无换行也算一行（Python 同）
    return n;
  } catch {
    return 0;
  }
}

/**
 * 文件签名。`mtimeNs` 为 JS 侧命名；同时给 `mtime_ns` 别名以对齐 Python dict 键名。
 * @returns {{mtimeNs: number, mtime_ns: number, size: number, lines: number} | null}
 */
export function fileSignature(filePath) {
  let st;
  try {
    st = fs.statSync(String(filePath), { bigint: true });
  } catch {
    return null;
  }
  const mtimeNs = Number(st.mtimeNs);
  return { mtimeNs, mtime_ns: mtimeNs, size: Number(st.size), lines: _countLines(String(filePath)) };
}

function _deepEqual(a, b) {
  if (a === b) return true;
  if (typeof a !== typeof b) return false;
  if (a === null || b === null || typeof a !== 'object') return false;
  if (Array.isArray(a) !== Array.isArray(b)) return false;
  if (Array.isArray(a)) {
    if (a.length !== b.length) return false;
    for (let i = 0; i < a.length; i += 1) if (!_deepEqual(a[i], b[i])) return false;
    return true;
  }
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  for (const k of ka) {
    if (!Object.prototype.hasOwnProperty.call(b, k)) return false;
    if (!_deepEqual(a[k], b[k])) return false;
  }
  return true;
}

/** mtime/size/行数 任一变化且过 200ms 节流 → 重载。 */
export function shouldReload(prev, cur, { nowMs, lastCheckMs, throttleMs = 200 } = {}) {
  if (nowMs - lastCheckMs < throttleMs) return false;
  if (prev === null || prev === undefined || cur === null || cur === undefined) return true;
  return !_deepEqual(prev, cur);
}

// ── 9. S4：UCA-01 文本检查（纯函数）──────────────────────

// Python 的 \b / \d / \w 是 Unicode 语义；JS 的 \b / \d 只认 ASCII，
// 镜像时用 Unicode 属性类手工还原，避免中文紧邻关键词时失配（parity 会抓）。
const PY_WORD = '[\\p{L}\\p{N}_]';
const PY_DIGIT = '\\p{Nd}';
const PY_BOUNDARY = `(?:(?<=${PY_WORD})(?!${PY_WORD})|(?<!${PY_WORD})(?=${PY_WORD}))`;

export const UCA01_RULES = [
  ['private_key', /-----BEGIN (?:RSA |EC |OPENSSH |PGP )?PRIVATE KEY-----/u, 'block'],
  ['credential_leak', new RegExp(`${PY_BOUNDARY}(?:api[_-]?key|secret|token|password|passwd|pwd)${PY_BOUNDARY}\\s*[:=]\\s*\\S{6,}`, 'iu'), 'block'],
  ['aws_key', new RegExp(`${PY_BOUNDARY}(?:AKIA|ASIA)[0-9A-Z]{16}${PY_BOUNDARY}`, 'u'), 'block'],
  ['sk_key', new RegExp(`${PY_BOUNDARY}sk-[A-Za-z0-9_-]{20,}${PY_BOUNDARY}`, 'u'), 'block'],
  ['id_card', new RegExp(`(?<!${PY_DIGIT})${PY_DIGIT}{17}[\\p{Nd}Xx](?!${PY_DIGIT})`, 'u'), 'block'],
  ['bank_card', new RegExp(`(?<!${PY_DIGIT})(?:${PY_DIGIT}[ -]?){15,18}${PY_DIGIT}(?!${PY_DIGIT})`, 'u'), 'block'],
  ['phone_number', new RegExp(`(?<!${PY_DIGIT})1[3-9]${PY_DIGIT}{9}(?!${PY_DIGIT})`, 'u'), 'warn'],
  ['secret_marker', /(?:绝密|机密|涉密|内部机密|confidential)/u, 'warn'],
];

/**
 * S4：记忆写入前的 UCA-01 文本检查。
 * verdict: block（凭据/密钥/证件/银行卡）> warn（手机号/涉密标记）> ok。
 * reasons 是稳定机器码，**不返回匹配到的原文**（防二次泄露）。
 */
export function checkUca01Text(text) {
  const s = String(text || '');
  if (!s.trim()) return { verdict: 'ok', reasons: [] };
  const hits = [];
  for (const [code, pattern, level] of UCA01_RULES) {
    if (pattern.test(s)) hits.push([code, level]);
  }
  let verdict = 'ok';
  if (hits.some(([, level]) => level === 'block')) verdict = 'block';
  else if (hits.length) verdict = 'warn';
  return { verdict, reasons: hits.map(([code]) => code) };
}

// ── 10. 记忆行构造（§3.8）────────────────────────────────

/**
 * 按 §3.8 契约构造一行记忆（★ = 本期新增字段）。入参允许 camelCase。
 * @returns {object} snake_case 行
 */
export function makeMemoryRow({
  memId, mem_id, ts, memType, mem_type, origin,
  subject = '', relation = '', obj = '', content = '', display,
  sandbox, persona, scope, importance = 0.5, status = 'active',
} = {}) {
  const id = memId !== undefined ? memId : mem_id;
  const type = memType !== undefined ? memType : mem_type;
  if (scope !== 'global' && scope !== 'local' && scope !== 'pending') {
    throw new Error('make_memory_row: bad scope');
  }
  const imp = Number(importance);
  const sub = String(subject);
  const rel = String(relation);
  const object = String(obj);
  return {
    id: String(id),
    ts: Math.trunc(Number(ts)),
    type: String(type),
    origin: String(origin),
    layer: 'warm',
    status,
    subject: sub,
    relation: rel,
    object,
    content: String(content),
    display_text: String(display !== undefined && display !== null ? display : sub + rel + object),
    sandbox_id: String(sandbox),
    persona_id: String(persona),
    scope,
    importance: imp,
    potency_inputs: { hits: 0, last_hit_at: null, importance: imp },
    supersedes: null,
    superseded_by: null,
    suppressed_at: null,
    suppressed_by: null,
  };
}

// ── 11. MemoryRuntime（JS 运行时薄层，非 Python 镜像）─────

const RUNTIME_SCOPES = ['local', 'global', 'pending'];

/**
 * 面板 / index.js 用的运行时：把纯函数缝到文件上。
 *
 * - 写入一律 **append-only**，绝不重写历史行（D8）；
 * - 每次写前过 `checkUca01Text`：block 拒写，warn 写入并回传 reasons；
 * - 锁由调用方负责（D10）：本类内部不持锁，避免长事务；
 * - 路径只走 `resolveMemoryPaths()`（§4.4.6 唯一出口）。
 */
export class MemoryRuntime {
  constructor({ dataRoot, sandboxId: sid, personaId: pid, now } = {}) {
    if (typeof dataRoot !== 'string' || !dataRoot.trim()) throw new Error('MemoryRuntime: dataRoot required');
    this.dataRoot = _abspath(dataRoot);
    this.sandboxId = String(sid ?? '');
    this.personaId = String(pid ?? '');
    this._nowFn = typeof now === 'function' ? now : () => (now === undefined ? Date.now() / 1000 : Number(now));
    // 构造即校验 id（fail-closed），脏 id 不会等到写盘才炸。
    this._paths = resolveMemoryPaths(this.dataRoot, this.sandboxId, this.personaId);
  }

  get paths() {
    return { ...this._paths };
  }

  _now() {
    return Math.trunc(Number(this._nowFn()));
  }

  _line(file, row) {
    try {
      fs.mkdirSync(path.dirname(file), { recursive: true });
      fs.appendFileSync(file, JSON.stringify(row) + '\n', 'utf8');
      return true;
    } catch {
      return false;
    }
  }

  _newId() {
    return `mem_${crypto.randomUUID().replace(/-/g, '').slice(0, 16)}`;
  }

  _layers() {
    return [
      { scope: 'global', file: this._paths.globalMemory },
      { scope: 'local', file: this._paths.localMemory },
      { scope: 'pending', file: this._paths.pending },
    ];
  }

  /** @returns {{local: object[], global: object[], pending: object[], trash: object[], paths: object}} */
  readAll() {
    const localRaw = readJsonl(this._paths.localMemory);
    const globalRaw = readJsonl(this._paths.globalMemory);
    const pendingRaw = readJsonl(this._paths.pending);
    return {
      local: foldRows(localRaw).active.map((r) => ({ ...r, scope: 'local' })),
      global: foldRows(globalRaw).active.map((r) => ({ ...r, scope: 'global' })),
      pending: foldRows(pendingRaw).active.map((r) => ({ ...r, scope: 'pending' })),
      trash: trashView([...localRaw, ...globalRaw, ...pendingRaw], { now: this._nowFn() }),
      paths: this.paths,
    };
  }

  /**
   * 追加写入。scope 缺省 local；行内标签强制为当前上下文（隔离由文件位置 + 标签双保险）。
   * @returns {{written: number, written_ids: string[], rejected: {id: string|null, reason: string}[],
   *            routed: {local: number, global: number, pending: number},
   *            uca01: {id: string, verdict: string, reasons: string[]}[]}}
   */
  append(rows) {
    const list = Array.isArray(rows) ? rows : [rows];
    const rejected = [];
    const uca01 = [];
    const writtenIds = [];
    const routed = { local: 0, global: 0, pending: 0 };
    const now = this._now();
    for (const raw of list) {
      if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
        rejected.push({ id: null, reason: 'not_object' });
        continue;
      }
      const id = raw.id === undefined || raw.id === null || raw.id === '' ? this._newId() : String(raw.id);
      const label = String(raw.scope ?? '').trim();
      const scope = label === '' ? 'local' : RUNTIME_SCOPES.includes(label) ? label : null;
      if (!scope) {
        rejected.push({ id, reason: 'bad_scope' });
        continue;
      }
      const text = String(raw.display_text ?? '').trim() !== '' ? raw.display_text : (raw.content ?? '');
      const uca = checkUca01Text(text);
      if (uca.verdict === 'block') {
        rejected.push({ id, reason: 'uca01_block' });
        continue;
      }
      const row = {
        ...raw,
        id,
        ts: raw.ts === undefined || raw.ts === null ? now : Math.trunc(Number(raw.ts)),
        scope,
        sandbox_id: this.sandboxId,
        persona_id: this.personaId,
      };
      if (scope === 'global') row.origin_sandbox_id = this.sandboxId;
      const file = scope === 'local'
        ? this._paths.localMemory
        : scope === 'global' ? this._paths.globalMemory : this._paths.pending;
      if (!this._line(file, row)) {
        rejected.push({ id, reason: 'io_error' });
        continue;
      }
      writtenIds.push(id);
      routed[scope] += 1;
      if (uca.verdict === 'warn') uca01.push({ id, verdict: 'warn', reasons: uca.reasons });
    }
    return { written: writtenIds.length, written_ids: writtenIds, rejected, routed, uca01 };
  }

  _locate(id) {
    for (const layer of this._layers()) {
      const raw = readJsonl(layer.file);
      const folded = foldRows(raw);
      const row = [...folded.active, ...folded.suppressed, ...folded.superseded]
        .find((r) => String(r.id) === String(id));
      if (row) return { ...layer, row };
    }
    return null;
  }

  /** 遗忘：追加墓碑事件（可被 restore 撤销）。 */
  forget(id) {
    const hit = this._locate(id);
    if (!hit) return { ok: false, scope: null, reason: 'not_found' };
    const ok = this._line(hit.file, {
      type: 'tombstone',
      target: String(id),
      at: this._now(),
      by: 'user_panel',
      reason: 'user_panel',
    });
    return { ok, scope: hit.scope };
  }

  /** 恢复：追加恢复事件（只撤销面板删除；全量墓碑不复活）。 */
  restore(id) {
    const hit = this._locate(id);
    if (!hit) return { ok: false, scope: null, reason: 'not_found' };
    const ok = this._line(hit.file, {
      type: 'restore',
      target: String(id),
      at: this._now(),
      by: 'user_panel',
    });
    return { ok, scope: hit.scope };
  }

  /** 彻底删除：redact（抹内容留骨架），仍 append-only。 */
  purge(id) {
    const hit = this._locate(id);
    if (!hit) return { ok: false, scope: null, reason: 'not_found' };
    const ok = this._line(hit.file, purgeRedact(hit.row, { at: this._now(), by: 'user_panel' }));
    return { ok, scope: hit.scope };
  }

  /** 面板人话视图。 */
  list({ query } = {}) {
    const all = this.readAll();
    const q = String(query ?? '').trim().toLowerCase();
    const match = (r) => !q
      || String(r.id ?? '').toLowerCase().includes(q)
      || displayText(r).toLowerCase().includes(q);
    const brief = (r) => ({
      id: String(r.id),
      display_text: displayText(r),
      date: r.ts === undefined || r.ts === null ? null : _isoDate(r.ts),
      uses: Number((r.potency_inputs && r.potency_inputs.hits) || 0),
    });
    return {
      global: all.global.filter(match).map(brief),
      local: all.local.filter(match).map(brief),
      pending_count: all.pending.length,
      trash: all.trash.filter(match),
    };
  }
}

function _isoDate(ts) {
  const n = Number(ts);
  if (!Number.isFinite(n)) return null;
  const d = new Date(n * 1000);
  if (Number.isNaN(d.getTime())) return null;
  return d.toISOString().slice(0, 10);
}
