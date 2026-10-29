// workspace-api.js —— 工作区发现 + 面板写路径（persona.json / 未激活工作区）
//
// 依据：decisions/017-人格体隔离-P1P2落地.md、workspace.js（id/布局的纯函数实现）。
// 本模块回答两个问题：
//   1) **当前 profile 数据根下有哪些工作区，各自统计摘要是什么**（只读）；
//   2) **面板可以把 persona.json 写进已有工作区、可以创建未激活工作区**（写路径，白名单 + 原子写）。
//
// 边界（改这里之前先读 ADR-017，不要"顺手"放宽）：
//   1) 读路径只读；写路径只有两个入口（PUT persona / POST create），且都落审计。
//   2) 只返回统计摘要：绝不返回绝对路径、记忆原文、prompt、persona 之外的配置原文。
//   3) 只扫**当前 profileTag** 的有效目录，不跨 tag 聚合：
//        <数据根>/workspace_<id>/profiles/<tag>/   （显式 profile 层）
//        <数据根>/profiles/<tag>/                  （legacy 平铺 + profile 层）
//        LINGSHU_DATA_DIR 覆盖时 profile 层**不叠加**（与 index.js#stateDir 旧语义一致，
//        由调用方传入 pathProfileTag='' 表达）。profileTag 仍原样回显，供面板识别。
//   4) 损坏 JSONL 容错：坏行/坏 state 跳过，不炸整份摘要。
//   5) 目录隔离 != 权限隔离（RK-1）。热切换由 index.js#switchWorkspace 负责（运行时上下文
//      整体重赋值 + turn 保护 + 持久化选择）；本模块只把 canSwitch 标为 true，不执行切换。
//   6) 符号链接不跟随：目录/文件都先 lstat，避免面板借道读写数据根之外的内容。
//   7) create 不伪造 state.json：只建目录（可选 persona.json）；运行态由插件 activate/apply 落盘。
//
// 未实现（不要在这里补）：跨工作区迁移（显式 CLI 在 workspace-admin.mjs）、多租户、
// persona prompt/锚点正文（persona.json 只存用户确认元数据）。

import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { WORKSPACE_ID_RE, PROFILE_TAG_RE } from './workspace.js';
import { describeWorkspacePolicy, loadWorkspacePolicy } from './workspace-policy.js';
// v0.2（施工单 v2）：数据根存在 layout.json 时，工作区 = 沙盒（sandboxId），
// 行内数据在 sandboxes/<sid>/<pid>/ 与 personas/<pid>/，不再用 workspace_<id>/ 目录寻址。
import { readLayout, foldRows } from './memory-paths.js';
import {
  readMappingBindings, writeMappingBindings, canonicalizePath, sandboxId,
} from './memory-paths.js';

export const API_SCHEMA_VERSION = 'workspace-api-0.2';
// legacy 伪工作区的展示 id；若真的存在 workspace_legacy/ 则以真实工作区优先（见 list/detail）。
export const LEGACY_ID = 'legacy';

const WS_DIR_PREFIX = 'workspace_';
const STATE_MAX_BYTES = 4 * 1024 * 1024;
const MEMORY_MAX_BYTES = 32 * 1024 * 1024;
const AUDIT_MAX_BYTES = 32 * 1024 * 1024;
const PERSONA_MAX_BYTES = 64 * 1024;
export const PERSONA_SCHEMA_VERSION = 'persona-0.1';
export const PERSONA_TRAITS = Object.freeze([
  'formality', 'warmth', 'proactiveness', 'humor', 'boundary_firmness',
]);
// 面板历史字段名（camelCase）→ 规范 schema 键。只做这一个兼容别名，落盘永远是 canonical snake。
const PERSONA_TRAIT_ALIASES = Object.freeze({ boundaryFirmness: 'boundary_firmness' });
const PERSONA_KEYS = Object.freeze([
  'schema_version', 'persona_id', 'name', 'summary', 'traits', 'confirmed_by_user', 'confirmed_at',
  // v0.2 表达层（只影响风格，不改变事实/权限）
  'self_reference', 'tone', 'boundaries',
]);
const PERSONA_ID_RE = /^[A-Za-z0-9_-]{1,64}$/;
// 面板能力声明：canSwitch=true（热切换已接线；当前工作区由 UI 自行禁用切换按钮）。
const CAPABILITIES = Object.freeze({
  canRead: true,
  canSwitch: true,
  canCreate: true,
  canWritePersona: true,
});

// ── 文件系统小工具（全部只读、lstat 不跟链接）────────────────────────
function lstatSafe(p) {
  try {
    return fs.lstatSync(p);
  } catch {
    return null;
  }
}

function isRealDir(p) {
  const st = lstatSafe(p);
  return Boolean(st && st.isDirectory());
}

function isRealFile(p) {
  const st = lstatSafe(p);
  return Boolean(st && st.isFile());
}

/** 拼一个必须留在 root 内部的路径；越界返回 null（纵深防御，正则之外再挡一层）。 */
function resolveInside(root, ...segs) {
  const base = path.resolve(root);
  const target = path.resolve(base, ...segs);
  if (target !== base && !target.startsWith(base + path.sep)) return null;
  return target;
}

/** 面向面板的相对目录（不暴露绝对路径）：分隔符统一为 '/'，数据根本身记为 '.'。 */
function relativeDataDir(base, dir) {
  const rel = path.relative(path.resolve(base), path.resolve(dir));
  if (!rel) return '.';
  return rel.split(path.sep).join('/');
}

function fileInfo(file) {
  const st = lstatSafe(file);
  if (!st || !st.isFile()) return { name: path.basename(file), exists: false, bytes: 0, lastModified: null };
  return { name: path.basename(file), exists: true, bytes: st.size, lastModified: st.mtimeMs };
}

/** 读 JSONL：坏行直接跳过；超上限只读前 N 字节并标 truncated（宁可少算，不可报错）。 */
function readJsonl(file, maxBytes) {
  const st = lstatSafe(file);
  if (!st || !st.isFile() || st.size === 0) return { entries: [], truncated: false };
  let raw;
  try {
    if (st.size > maxBytes) {
      const fd = fs.openSync(file, 'r');
      try {
        const buf = Buffer.alloc(maxBytes);
        const n = fs.readSync(fd, buf, 0, maxBytes, 0);
        raw = buf.subarray(0, n).toString('utf8');
      } finally {
        fs.closeSync(fd);
      }
      // 截断点可能落在行中间：丢掉最后一条不完整的行。
      const cut = raw.lastIndexOf('\n');
      raw = cut >= 0 ? raw.slice(0, cut + 1) : '';
    } else {
      raw = fs.readFileSync(file, 'utf8');
    }
  } catch {
    return { entries: [], truncated: false };
  }
  const entries = [];
  for (const line of raw.split('\n')) {
    const text = line.replace(/^\uFEFF/, '').trim();
    if (!text) continue;
    try {
      const value = JSON.parse(text);
      if (value && typeof value === 'object' && !Array.isArray(value)) entries.push(value);
    } catch {
      // 损坏行：跳过（JSONL 是追加写，一行坏不该毁掉整份统计）。
    }
  }
  return { entries, truncated: st.size > maxBytes };
}

function readStateFile(file) {
  const st = lstatSafe(file);
  if (!st || !st.isFile() || st.size > STATE_MAX_BYTES) return {};
  try {
    const parsed = JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

function toCount(value) {
  const n = Number(value);
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : 0;
}

// ── 摘要计算（纯读取）────────────────────────────────────────────
// memory：同 id 后写覆盖（last-wins，与 index.js#readMemories 同口径）。
// warm 是**层计数**（layer === 'warm'），与 active/suppressed 是不同维度，不要相加。
function summarizeMemory(entries, truncated) {
  const byId = new Map();
  let anon = 0;
  for (const entry of entries) {
    if (!entry || typeof entry !== 'object') continue;
    // 缺 id 的坏条目不能被折叠成同一条：用序号各自成键。
    const id = typeof entry.id === 'string' && entry.id ? entry.id : `#anon_${anon++}`;
    byId.set(id, entry);
  }
  const values = [...byId.values()];
  let active = 0;
  let warm = 0;
  for (const entry of values) {
    if (String(entry.status ?? 'active') === 'active') active += 1;
    if (String(entry.layer ?? '') === 'warm') warm += 1;
  }
  return { total: values.length, active, suppressed: values.length - active, warm, truncated: Boolean(truncated) };
}

// audit：blocked = 被前置审计/工具网关拦截的事件数（blocked===true 或 verdict==='block'）。
// lastEventAt = 最后一条带合法 ts 的事件时间（epoch ms），供面板显示"最近活动"。
function summarizeAudit(entries, truncated) {
  let blocked = 0;
  let lastEventAt = null;
  for (const entry of entries) {
    if (!entry || typeof entry !== 'object') continue;
    if (entry.blocked === true || entry.verdict === 'block') blocked += 1;
    const ts = Number(entry.ts);
    if (Number.isFinite(ts) && ts > 0) lastEventAt = ts;
  }
  return { blocked, lastEventAt, truncated: Boolean(truncated) };
}

function emotionLabel(dev) {
  // 与 index.js#emotionLabel 保持一致（纯函数；此处复制是为了避免 index->dashboard->api 的循环导入）。
  const v = Number(dev?.valence || 0);
  const a = Number(dev?.arousal || 0);
  const maxAbs = Math.max(Math.abs(v), Math.abs(a), Math.abs(Number(dev?.dominance || 0)));
  if (maxAbs < 0.05) return '中性';
  if (v > 0) return a >= 0.1 ? '振奋' : '愉悦';
  return a >= 0.15 ? '不满' : '低落';
}

function stageOf(turns, coldTurns, warmTurns) {
  const t = toCount(turns);
  if (t < coldTurns) return 'cold';
  if (t < warmTurns) return 'warm';
  return 'regular';
}

function cleanString(value, max) {
  if (typeof value !== 'string') return '';
  // 控制字符替换为空格：面板只做文本显示，绝不接受换行/转义注入。
  return value.replace(/[\u0000-\u001f\u007f]/g, ' ').trim().slice(0, max);
}

// persona.json 不存在 / 损坏 / 形状不符 → null（绝不伪造）。存在时只白名单三个字段。
export function readPersona(file) {
  const st = lstatSafe(file);
  if (!st || !st.isFile() || st.size > PERSONA_MAX_BYTES) return null;
  let parsed;
  try {
    parsed = JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
  const name = cleanString(parsed.name, 120);
  const summary = cleanString(parsed.summary, 600);
  const traits = {};
  const rawTraits = parsed.traits;
  if (rawTraits && typeof rawTraits === 'object' && !Array.isArray(rawTraits)) {
    for (const [key, value] of Object.entries(rawTraits).slice(0, 24)) {
      if (!/^[A-Za-z0-9_-]{1,32}$/.test(key)) continue;
      if (typeof value !== 'number' || !Number.isFinite(value)) continue;
      traits[key] = value;
    }
  }
  if (!name && !summary && Object.keys(traits).length === 0) return null;
  return {
    name,
    summary,
    traits,
    self_reference: cleanString(parsed.self_reference, 40),
    tone: cleanString(parsed.tone, 200),
    boundaries: cleanString(parsed.boundaries, 300),
  };
}

function summarize({
  base, dataDir, workspaceId, isCurrent, coldTurns, warmTurns, policyDir, policyWorkspaceId,
}) {
  const stateFile = path.join(dataDir, 'state.json');
  const memoryFile = path.join(dataDir, 'memory.jsonl');
  const auditFile = path.join(dataDir, 'audit.jsonl');
  const personaFile = path.join(dataDir, 'persona.json');
  const policyId = policyWorkspaceId === undefined ? workspaceId : policyWorkspaceId;

  const state = readStateFile(stateFile);
  const memoryRead = readJsonl(memoryFile, MEMORY_MAX_BYTES);
  const auditRead = readJsonl(auditFile, AUDIT_MAX_BYTES);
  const turns = toCount(state.turns);
  const phase = state.control?.phase?.phase;
  const checkedAt = Number(state.frozen?.checkedAt);

  const policyStatus = describeWorkspacePolicy(loadWorkspacePolicy({
    policyDir: policyDir || '',
    workspaceId: typeof policyId === 'string' ? policyId : '',
  }));
  return {
    workspaceId,
    relativeDataDir: relativeDataDir(base, dataDir),
    isCurrent: Boolean(isCurrent),
    turns,
    stage: stageOf(turns, coldTurns, warmTurns),
    frozen: {
      ok: state.frozen?.ok === true,
      checkedAt: Number.isFinite(checkedAt) && checkedAt > 0 ? checkedAt : null,
    },
    relation: {
      trustLevel: typeof state.relation?.trustLevel === 'string' ? state.relation.trustLevel : 'unknown',
      stage: typeof state.relation?.stage === 'string' ? state.relation.stage : 'unknown',
      unresolvedConflict: Boolean(state.relation?.unresolvedConflict),
    },
    control: {
      phase: typeof phase === 'string' ? phase : 'unknown',
    },
    emotion: {
      // 与 /api/status 同口径：state.dev，不做额外衰减（衰减在插件运行时已落盘）。
      label: emotionLabel(state.dev),
    },
    memory: summarizeMemory(memoryRead.entries, memoryRead.truncated),
    audit: summarizeAudit(auditRead.entries, auditRead.truncated),
    persona: readPersona(personaFile),
    // P2 策略状态：只读展示；真正生效在 index.js 启动/网关调用路径（本模块不改任何规则）。
    // enforced 只对"当前工作区 + loaded"为 true（非当前工作区的策略要等它成为当前工作区才生效）。
    policy: { ...policyStatus, enforced: Boolean(isCurrent && policyStatus.status === 'loaded') },
    capabilities: { ...CAPABILITIES },
    // 附加字段（契约之外，面板隔离抽屉用）：只有文件名/大小/时间，没有路径。
    files: {
      state: fileInfo(stateFile),
      memory: fileInfo(memoryFile),
      audit: fileInfo(auditFile),
      persona: fileInfo(personaFile),
    },
  };
}

// ── 目录布局 ────────────────────────────────────────────
function normalizeOptions(options = {}) {
  const base = typeof options.base === 'string' && options.base.trim() ? path.resolve(options.base.trim()) : '';
  const profileTag = typeof options.profileTag === 'string' ? options.profileTag.trim() : '';
  const rawPathTag = typeof options.pathProfileTag === 'string' ? options.pathProfileTag.trim() : '';
  const pathProfileTag = PROFILE_TAG_RE.test(rawPathTag) ? rawPathTag : '';
  const currentId = typeof options.currentId === 'string' && WORKSPACE_ID_RE.test(options.currentId.trim())
    ? options.currentId.trim()
    : '';
  const coldTurns = Number.isFinite(Number(options.coldTurns)) && Number(options.coldTurns) >= 0
    ? Math.floor(Number(options.coldTurns)) : 3;
  const warmTurns = Number.isFinite(Number(options.warmTurns)) && Number(options.warmTurns) > coldTurns
    ? Math.floor(Number(options.warmTurns)) : Math.max(coldTurns + 1, 20);
  const rejected = typeof options.rejected === 'string' && options.rejected
    ? options.rejected.slice(0, 64) : null;
  // P2 策略文件目录（<pluginRoot>/workspaces）；只读、只用于状态展示。
  const policyDir = typeof options.policyDir === 'string' && options.policyDir.trim()
    ? path.resolve(options.policyDir.trim()) : '';
  // 审计回调（index.js 注入；写路径用它把事件记到**当前**工作区账本）。
  const audit = typeof options.audit === 'function' ? options.audit : null;
  const workspaceRoot = typeof options.workspaceRoot === 'string' && options.workspaceRoot.trim()
    ? path.resolve(options.workspaceRoot.trim()) : '';
  const dsHome = typeof options.dsHome === 'string' && options.dsHome.trim()
    ? path.resolve(options.dsHome.trim()) : '';
  return { base, profileTag, pathProfileTag, currentId, coldTurns, warmTurns, rejected, policyDir, audit, workspaceRoot, dsHome };
}

/** 工作区的数据目录：workspace 外层 + （可选）profile 内层；越界/非法返回 null。 */
function workspaceDataDir(base, workspaceId, pathProfileTag) {
  if (!WORKSPACE_ID_RE.test(String(workspaceId || ''))) return null;
  const wsDir = resolveInside(base, `${WS_DIR_PREFIX}${workspaceId}`);
  if (!wsDir) return null;
  if (!pathProfileTag) return wsDir;
  return resolveInside(wsDir, 'profiles', pathProfileTag);
}

/** legacy 数据目录：profile 层或数据根本身。 */
function legacyDataDir(base, pathProfileTag) {
  if (!pathProfileTag) return base;
  return resolveInside(base, 'profiles', pathProfileTag);
}

function hasAnyDataFile(dir) {
  if (!dir) return false;
  return isRealFile(path.join(dir, 'state.json'))
    || isRealFile(path.join(dir, 'memory.jsonl'))
    || isRealFile(path.join(dir, 'audit.jsonl'))
    || isRealFile(path.join(dir, 'persona.json'));
}

function collectWorkspaceIds(base) {
  let entries;
  try {
    entries = fs.readdirSync(base, { withFileTypes: true });
  } catch {
    return { ids: [], ignored: 0 };
  }
  const ids = [];
  let ignored = 0;
  for (const entry of entries) {
    if (!entry.name.startsWith(WS_DIR_PREFIX)) continue;
    // 符号链接/普通文件一律不算工作区（Dirent 不跟链接）。
    if (!entry.isDirectory()) continue;
    const id = entry.name.slice(WS_DIR_PREFIX.length);
    if (!WORKSPACE_ID_RE.test(id)) {
      ignored += 1;   // 只计数，不回显非法目录名（可能是注入串）。
      continue;
    }
    ids.push(id);
  }
  ids.sort();
  return { ids, ignored };
}

// ── v0.2：沙盒索引 / 绑定 / 摘要（只读）───────────────────────────────
const SANDBOX_ID_RE = /^[0-9a-f]{8}$/;
const V02_PERSONA_RE = /^[A-Za-z0-9_-]{1,64}$/;

function readSandboxIndexV02(base) {
  const root = path.resolve(base);
  const file = path.join(root, 'sandboxes', 'index.json');
  try {
    const doc = JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
    if (doc && doc.sandboxes && typeof doc.sandboxes === 'object') return doc.sandboxes;
  } catch { /* 回退扫描 */ }
  const out = {};
  try {
    for (const sid of fs.readdirSync(path.join(root, 'sandboxes'))) {
      if (!SANDBOX_ID_RE.test(sid)) continue;
      try {
        const rec = JSON.parse(fs.readFileSync(
          path.join(root, 'sandboxes', sid, 'sandbox.json'), 'utf8').replace(/^\uFEFF/, ''));
        if (rec && typeof rec === 'object') out[sid] = rec;
      } catch { /* 单个坏记录跳过 */ }
    }
  } catch { /* 无 sandboxes 目录 */ }
  return out;
}

function readMappingBindingsV02(base) {
  const file = path.join(path.resolve(base), 'sandbox_mapping.yaml');
  let raw = '';
  try { raw = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''); } catch { return []; }
  const out = [];
  let cur = null;
  for (const line of raw.split(/\r?\n/)) {
    const item = /^\s*-\s*canonical_path:\s*"?(.+?)"?\s*$/.exec(line);
    if (item) { cur = { canonical_path: item[1] }; out.push(cur); continue; }
    if (!cur) continue;
    const kv = /^\s*(sandbox_id|default_persona|label):\s*"?(.*?)"?\s*$/.exec(line);
    if (kv) cur[kv[1]] = kv[2];
  }
  return out;
}

/** 沙盒绑定的人格：目录即事实（只有一个 persona 子目录时用它），否则查 mapping。 */
function sandboxPersonaId(base, sid) {
  let dirs = [];
  try {
    dirs = fs.readdirSync(path.join(path.resolve(base), 'sandboxes', sid), { withFileTypes: true })
      .filter((e) => e.isDirectory() && V02_PERSONA_RE.test(e.name))
      .map((e) => e.name).sort();
  } catch { /* noop */ }
  if (dirs.length === 1) return dirs[0];
  const binding = readMappingBindingsV02(base).find((b) => b.sandbox_id === sid);
  if (binding && V02_PERSONA_RE.test(String(binding.default_persona || ''))) {
    return String(binding.default_persona);
  }
  return dirs[0] || 'butler';
}

function summarizeSandbox(o, sid, rec) {
  const root = path.resolve(o.base);
  const pid = sandboxPersonaId(o.base, sid);
  const sdir = path.join(root, 'sandboxes', sid, pid);
  const pdir = path.join(root, 'personas', pid);
  const state = readStateFile(path.join(sdir, 'state.json'));
  const pstate = readStateFile(path.join(pdir, 'persona_state.json'));
  const local = readJsonl(path.join(sdir, 'local_memory.jsonl'), MEMORY_MAX_BYTES);
  const global = readJsonl(path.join(pdir, 'global_memory.jsonl'), MEMORY_MAX_BYTES);
  const auditAll = readJsonl(path.join(root, 'audit', 'audit.jsonl'), AUDIT_MAX_BYTES);
  const auditEntries = auditAll.entries.filter((e) => String(e.sandbox_id || '') === sid);
  const turns = toCount(state.turns);
  const phase = state.control?.phase?.phase;
  const frozenAt = Number(state.frozen?.checkedAt);
  const relation = (pstate.relations && typeof pstate.relations === 'object')
    ? pstate.relations : (state.relation || {});
  const emo = (pstate.emotion_baseline && typeof pstate.emotion_baseline === 'object')
    ? pstate.emotion_baseline : state.dev;
  const policyStatus = describeWorkspacePolicy(loadWorkspacePolicy({
    policyDir: o.policyDir || '',
    workspaceId: sid,
  }));
  const localRows = foldRows(local.entries);
  const globalRows = foldRows(global.entries);
  return {
    workspaceId: sid,
    label: rec && typeof rec.label === 'string' ? rec.label : null,
    canonicalPath: rec && typeof rec.canonical_path === 'string' ? rec.canonical_path : null,
    relativeDataDir: `sandboxes/${sid}`,
    isCurrent: sid === o.currentId,
    turns,
    stage: stageOf(turns, o.coldTurns, o.warmTurns),
    frozen: {
      ok: state.frozen?.ok === true,
      checkedAt: Number.isFinite(frozenAt) && frozenAt > 0 ? frozenAt : null,
    },
    relation: {
      trustLevel: typeof relation.trustLevel === 'string' ? relation.trustLevel : 'unknown',
      stage: typeof relation.stage === 'string' ? relation.stage : 'unknown',
      unresolvedConflict: Boolean(relation.unresolvedConflict),
    },
    control: { phase: typeof phase === 'string' ? phase : 'unknown' },
    emotion: { label: emotionLabel(emo) },
    // 面板的 memory.total 在 v0.2 指"本项目 local + 该人格 global"的 active 条数。
    memory: summarizeMemory([...localRows.active, ...globalRows.active],
      local.truncated || global.truncated),
    audit: summarizeAudit(auditEntries, auditAll.truncated),
    persona: readPersona(path.join(pdir, 'persona.json')),
    policy: { ...policyStatus, enforced: Boolean(sid === o.currentId && policyStatus.status === 'loaded') },
    capabilities: { ...CAPABILITIES },
    files: {
      state: fileInfo(path.join(sdir, 'state.json')),
      memory: fileInfo(path.join(sdir, 'local_memory.jsonl')),
      audit: fileInfo(path.join(root, 'audit', 'audit.jsonl')),
      persona: fileInfo(path.join(pdir, 'persona.json')),
      skeleton: fileInfo(path.join(sdir, 'skeleton.md')),
    },
  };
}

function v02Layout(o) {
  if (!o.base) return null;
  const doc = readLayout(o.base);
  return doc ? doc : null;
}

// ── 对外 API ────────────────────────────────────────────

/**
 * 列出当前 profile 的工作区摘要。**永不抛异常**：数据根不可用时返回 ok:false。
 * @param {object} options
 * @param {string} options.base          数据根（绝对路径；只用于内部拼路径，不对外返回）
 * @param {string} [options.profileTag]  对外展示的 profileTag（'' -> null）
 * @param {string} [options.pathProfileTag] 真正参与路径的 profileTag（LINGSHU_DATA_DIR 覆盖时传 ''）
 * @param {string} [options.currentId]   当前运行工作区 id（'' = legacy 布局）
 * @param {string} [options.rejected]    被 resolveWorkspace 拒绝的原始 id（可诊断）
 * @param {number} [options.coldTurns]   阶段阈值（与插件 config 同源，由 index.js 传入）
 * @param {number} [options.warmTurns]
 * @returns {object} { ok, schemaVersion, generatedAt, source, currentWorkspaceId, profileTag,
 *                     layout, rejectedWorkspaceId, ignoredWorkspaceDirs, workspaces[] }
 */
export function listWorkspaces(options = {}) {
  const o = normalizeOptions(options);
  if (!o.base) {
    return {
      ok: false,
      schemaVersion: API_SCHEMA_VERSION,
      generatedAt: new Date().toISOString(),
      error: 'data_root_unavailable',
    };
  }

  // v0.2：工作区列表 = 沙盒列表（sandboxes/index.json）；id 是 sandboxId。
  const layoutDoc = v02Layout(o);
  if (layoutDoc) {
    const index = readSandboxIndexV02(o.base);
    const workspaces = Object.keys(index).sort()
      .map((sid) => summarizeSandbox(o, sid, index[sid]));
    workspaces.sort((a, b) => (Number(b.isCurrent) - Number(a.isCurrent))
      || (a.workspaceId < b.workspaceId ? -1 : a.workspaceId > b.workspaceId ? 1 : 0));
    return {
      ok: true,
      schemaVersion: API_SCHEMA_VERSION,
      generatedAt: new Date().toISOString(),
      source: 'dsh-lingshu/v0.2-sandboxes',
      currentWorkspaceId: o.currentId || null,
      profileTag: o.profileTag || null,
      layout: 'v0.2',
      rejectedWorkspaceId: o.rejected,
      ignoredWorkspaceDirs: 0,
      workspaces,
    };
  }

  const { ids, ignored } = collectWorkspaceIds(o.base);
  const workspaces = [];
  for (const id of ids) {
    const dataDir = workspaceDataDir(o.base, id, o.pathProfileTag);
    // 当前 profileTag 下**存在这个数据目录**就算有效工作区（create 会建一个还没有 state.json 的目录，
    // 必须能列出）；只存在别的 tag 的 profiles/<other>/ 时 dataDir 本身不存在 → 不列，避免跨 tag 露出。
    if (!dataDir || !isRealDir(dataDir)) continue;
    workspaces.push(summarize({
      base: o.base,
      dataDir,
      workspaceId: id,
      isCurrent: id === o.currentId,
      coldTurns: o.coldTurns,
      warmTurns: o.warmTurns,
      policyDir: o.policyDir,
    }));
  }

  // legacy 平铺数据：只有在真的存在数据文件时才作为伪工作区出现（空数据根 = 空列表）。
  // 若已存在同名 workspace_legacy/，真实工作区优先，不重复列。
  const legacyDir = legacyDataDir(o.base, o.pathProfileTag);
  if (legacyDir && hasAnyDataFile(legacyDir) && !workspaces.some((w) => w.workspaceId === LEGACY_ID)) {
    workspaces.push(summarize({
      base: o.base,
      dataDir: legacyDir,
      workspaceId: LEGACY_ID,
      isCurrent: o.currentId === '',
      coldTurns: o.coldTurns,
      warmTurns: o.warmTurns,
      policyDir: o.policyDir,
      policyWorkspaceId: '',   // 平铺目录没有工作区 id，不读 workspaces/legacy.json
    }));
  }

  // 当前工作区排前面，其余按 id 字典序（保证面板渲染顺序稳定）。
  workspaces.sort((a, b) => (Number(b.isCurrent) - Number(a.isCurrent))
    || (a.workspaceId < b.workspaceId ? -1 : a.workspaceId > b.workspaceId ? 1 : 0));

  return {
    ok: true,
    schemaVersion: API_SCHEMA_VERSION,
    generatedAt: new Date().toISOString(),
    source: 'dsh-lingshu/workspace-dirs',
    currentWorkspaceId: o.currentId || null,
    profileTag: o.profileTag || null,
    layout: o.currentId ? 'workspace' : 'legacy',
    rejectedWorkspaceId: o.rejected,
    ignoredWorkspaceDirs: ignored,
    workspaces,
  };
}

/**
 * 读单个工作区摘要。
 * @returns {{ok:true, schemaVersion, generatedAt, workspace:object}
 *          | {ok:false, status:number, error:string, workspaceId?:string}}
 *   400 = id 形状非法/路径越界；404 = id 合法但当前 profileTag 下不存在。
 */
export function readWorkspace(options = {}, rawId) {
  const o = normalizeOptions(options);
  const id = typeof rawId === 'string' ? rawId.trim() : '';
  if (!id) {
    return { ok: false, status: 400, error: 'invalid_workspace_id' };
  }
  if (!o.base) {
    return { ok: false, status: 404, error: 'workspace_not_found', workspaceId: WORKSPACE_ID_RE.test(id) ? id : undefined };
  }

  // v0.2：详情按 sandboxId 查；非法/不存在统一 404，不猜旧 workspace_<id> 目录。
  if (v02Layout(o)) {
    const index = readSandboxIndexV02(o.base);
    if (!SANDBOX_ID_RE.test(id) || !index[id]) {
      return { ok: false, status: 404, error: 'workspace_not_found', workspaceId: id };
    }
    return {
      ok: true,
      schemaVersion: API_SCHEMA_VERSION,
      generatedAt: new Date().toISOString(),
      workspace: summarizeSandbox(o, id, index[id]),
    };
  }

  if (id === LEGACY_ID) {
    // 真实 workspace_legacy/ 优先；否则才是 legacy 平铺伪工作区。
    const realDir = workspaceDataDir(o.base, LEGACY_ID, o.pathProfileTag);
    if (realDir && isRealDir(realDir)) {
      return {
        ok: true,
        schemaVersion: API_SCHEMA_VERSION,
        generatedAt: new Date().toISOString(),
        workspace: summarize({
          base: o.base,
          dataDir: realDir,
          workspaceId: LEGACY_ID,
          isCurrent: o.currentId === LEGACY_ID,
          coldTurns: o.coldTurns,
          warmTurns: o.warmTurns,
          policyDir: o.policyDir,
        }),
      };
    }
    const legacyDir = legacyDataDir(o.base, o.pathProfileTag);
    if (!legacyDir || !hasAnyDataFile(legacyDir)) {
      return { ok: false, status: 404, error: 'workspace_not_found', workspaceId: LEGACY_ID };
    }
    return {
      ok: true,
      schemaVersion: API_SCHEMA_VERSION,
      generatedAt: new Date().toISOString(),
      workspace: summarize({
        base: o.base,
        dataDir: legacyDir,
        workspaceId: LEGACY_ID,
        isCurrent: o.currentId === '',
        coldTurns: o.coldTurns,
        warmTurns: o.warmTurns,
        policyDir: o.policyDir,
        policyWorkspaceId: '',
      }),
    };
  }

  if (!WORKSPACE_ID_RE.test(id)) {
    return { ok: false, status: 400, error: 'invalid_workspace_id' };
  }
  const dataDir = workspaceDataDir(o.base, id, o.pathProfileTag);
  // 路径 containment：workspaceDataDir 已用 resolveInside + 正则双重约束，这里再挡一次。
  if (!dataDir || (dataDir !== o.base && !dataDir.startsWith(o.base + path.sep))) {
    return { ok: false, status: 400, error: 'invalid_workspace_path' };
  }
  if (!isRealDir(dataDir)) {
    return { ok: false, status: 404, error: 'workspace_not_found', workspaceId: id };
  }
  return {
    ok: true,
    schemaVersion: API_SCHEMA_VERSION,
    generatedAt: new Date().toISOString(),
    workspace: summarize({
      base: o.base,
      dataDir,
      workspaceId: id,
      isCurrent: id === o.currentId,
      coldTurns: o.coldTurns,
      warmTurns: o.warmTurns,
      policyDir: o.policyDir,
    }),
  };
}

// ── 写路径：persona.json / 未激活工作区 ─────────────────────────
// 只有两个入口，全部白名单 + 原子写；不写 state.json、不改运行中的工作区选择。

function safeAudit(auditFn, kind, data) {
  if (typeof auditFn !== 'function') return;
  try {
    auditFn(kind, data);
  } catch {
    // 审计失败不影响写结果（调用方在 index.js 里组装事件；这里只保证不炸请求）。
  }
}

/**
 * persona.json 白名单校验（pure）。只接受确认为用户确认过的元数据，其他键一律拒绝。
 * @returns {{ok:true, value:object} | {ok:false, error:string, field?:string}}
 */
export function validatePersonaPayload(raw) {
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ok: false, error: 'invalid_body' };
  }
  for (const key of Object.keys(raw)) {
    if (!PERSONA_KEYS.includes(key)) {
      return { ok: false, error: 'unknown_field', field: key.slice(0, 32) };
    }
  }
  if (raw.schema_version !== PERSONA_SCHEMA_VERSION) {
    return { ok: false, error: 'bad_schema_version' };
  }
  const personaId = typeof raw.persona_id === 'string' ? raw.persona_id.trim() : '';
  if (!PERSONA_ID_RE.test(personaId)) return { ok: false, error: 'bad_persona_id' };
  const name = typeof raw.name === 'string' ? raw.name.trim() : '';
  if (!name) return { ok: false, error: 'name_required' };
  if (name.length > 120) return { ok: false, error: 'name_too_long' };
  if (raw.summary !== undefined && raw.summary !== null && typeof raw.summary !== 'string') {
    return { ok: false, error: 'bad_summary' };
  }
  const summary = typeof raw.summary === 'string' ? raw.summary.trim() : '';
  if (summary.length > 600) return { ok: false, error: 'summary_too_long' };
  const readText = (key, max, label) => {
    if (raw[key] === undefined || raw[key] === null) return { value: '' };
    if (typeof raw[key] !== 'string') return { error: `bad_${label}` };
    const text = raw[key].trim();
    if (text.length > max) return { error: `${label}_too_long` };
    return { value: text };
  };
  const selfReference = readText('self_reference', 40, 'self_reference');
  if (selfReference.error) return { ok: false, error: selfReference.error };
  const tone = readText('tone', 200, 'tone');
  if (tone.error) return { ok: false, error: tone.error };
  const boundaries = readText('boundaries', 300, 'boundaries');
  if (boundaries.error) return { ok: false, error: boundaries.error };

  const traitsRaw = raw.traits === undefined ? {} : raw.traits;
  if (!traitsRaw || typeof traitsRaw !== 'object' || Array.isArray(traitsRaw)) {
    return { ok: false, error: 'bad_traits' };
  }
  const traits = {};
  for (const [rawKey, value] of Object.entries(traitsRaw)) {
    const key = PERSONA_TRAIT_ALIASES[rawKey] || rawKey;
    if (!PERSONA_TRAITS.includes(key)) {
      return { ok: false, error: 'unknown_trait', field: rawKey.slice(0, 32) };
    }
    if (Object.prototype.hasOwnProperty.call(traits, key)) {
      // 例：同时给 boundaryFirmness 与 boundary_firmness → 语义冲突，直接拒绝。
      return { ok: false, error: 'duplicate_trait', field: key };
    }
    if (typeof value !== 'number' || !Number.isFinite(value) || value < 0 || value > 1) {
      return { ok: false, error: 'bad_trait_value', field: key };
    }
    traits[key] = value;
  }

  if (raw.confirmed_by_user !== true) {
    return { ok: false, error: 'not_confirmed' };
  }
  let confirmedAt = new Date().toISOString();
  if (raw.confirmed_at !== undefined && raw.confirmed_at !== null && raw.confirmed_at !== '') {
    if (typeof raw.confirmed_at !== 'string' || !Number.isFinite(Date.parse(raw.confirmed_at))) {
      return { ok: false, error: 'bad_confirmed_at' };
    }
    confirmedAt = new Date(Date.parse(raw.confirmed_at)).toISOString();
  }
  return {
    ok: true,
    value: {
      schema_version: PERSONA_SCHEMA_VERSION,
      persona_id: personaId,
      name,
      summary,
      traits,
      self_reference: selfReference.value,
      tone: tone.value,
      boundaries: boundaries.value,
      confirmed_by_user: true,
      confirmed_at: confirmedAt,
    },
  };
}

/** 临时文件 + fsync + rename：写一半不会留下半个 persona.json，旧文件在 rename 前一直可读。 */
function atomicWriteJson(file, value) {
  const dir = path.dirname(file);
  fs.mkdirSync(dir, { recursive: true });
  const tmp = path.join(dir, `.${path.basename(file)}.tmp-${process.pid}-${crypto.randomBytes(6).toString('hex')}`);
  const json = JSON.stringify(value, null, 2) + '\n';
  let fd = null;
  try {
    fd = fs.openSync(tmp, 'w');
    fs.writeFileSync(fd, json, 'utf8');
    try { fs.fsyncSync(fd); } catch { /* fsync best-effort（部分文件系统不支持） */ }
    fs.closeSync(fd);
    fd = null;
    fs.renameSync(tmp, file);
    return { bytes: Buffer.byteLength(json, 'utf8'), sha256: crypto.createHash('sha256').update(json).digest('hex') };
  } catch (err) {
    if (fd !== null) {
      try { fs.closeSync(fd); } catch { /* noop */ }
    }
    try { fs.unlinkSync(tmp); } catch { /* noop */ }
    throw err;
  }
}

/** 写路径的目标目录：显式 id 或 legacy 平铺；不存在/非法返回错误对象，不做隐式创建。 */
function resolveWriteTarget(o, rawId) {
  const id = typeof rawId === 'string' ? rawId.trim() : '';
  if (!o.base) return { ok: false, status: 503, error: 'data_root_unavailable' };
  if (!id) return { ok: false, status: 400, error: 'invalid_workspace_id' };
  if (id === LEGACY_ID) {
    // 真实 workspace_legacy/ 优先；否则平铺目录，且必须是"已存在数据的 legacy"或当前布局。
    const realDir = workspaceDataDir(o.base, LEGACY_ID, o.pathProfileTag);
    if (realDir && isRealDir(realDir)) {
      return { ok: true, workspaceId: LEGACY_ID, dataDir: realDir, legacy: false };
    }
    const legacyDir = legacyDataDir(o.base, o.pathProfileTag);
    if (legacyDir && isRealDir(legacyDir) && (hasAnyDataFile(legacyDir) || o.currentId === '')) {
      return { ok: true, workspaceId: LEGACY_ID, dataDir: legacyDir, legacy: true };
    }
    return { ok: false, status: 404, error: 'workspace_not_found', workspaceId: LEGACY_ID };
  }
  if (!WORKSPACE_ID_RE.test(id)) return { ok: false, status: 400, error: 'invalid_workspace_id' };
  const dataDir = workspaceDataDir(o.base, id, o.pathProfileTag);
  if (!dataDir || (dataDir !== o.base && !dataDir.startsWith(o.base + path.sep))) {
    return { ok: false, status: 400, error: 'invalid_workspace_path' };
  }
  if (!isRealDir(dataDir)) {
    return { ok: false, status: 404, error: 'workspace_not_found', workspaceId: id };
  }
  return { ok: true, workspaceId: id, dataDir, legacy: false };
}

function summaryForTarget(o, target) {
  return summarize({
    base: o.base,
    dataDir: target.dataDir,
    workspaceId: target.workspaceId,
    isCurrent: target.legacy ? o.currentId === '' : target.workspaceId === o.currentId,
    coldTurns: o.coldTurns,
    warmTurns: o.warmTurns,
    policyDir: o.policyDir,
    policyWorkspaceId: target.legacy ? '' : target.workspaceId,
  });
}

/**
 * 写已有工作区的 persona.json（PUT /api/workspaces/:id/persona）。
 * @returns {{ok:true, status:200, activated:false, persona:object, workspace:object, audit:object}
 *          | {ok:false, status:number, error:string, field?:string, reason?:string, workspaceId?:string}}
 */
export function writePersona(options = {}, rawId, rawPayload) {
  const o = normalizeOptions(options);
  // v0.2：persona 写在 personas/<pid>/persona.json；persona_id 必须与沙盒绑定一致。
  if (v02Layout(o)) {
    const id = typeof rawId === 'string' ? rawId.trim() : '';
    if (!id) return { ok: false, status: 400, error: 'invalid_workspace_id' };
    const index = readSandboxIndexV02(o.base);
    if (!SANDBOX_ID_RE.test(id) || !index[id]) {
      return { ok: false, status: 404, error: 'workspace_not_found', workspaceId: id };
    }
    const validated = validatePersonaPayload(rawPayload);
    if (!validated.ok) {
      return { ok: false, status: 400, error: validated.error, field: validated.field };
    }
    const pid = sandboxPersonaId(o.base, id);
    if (validated.value.persona_id !== pid) {
      return { ok: false, status: 400, error: 'persona_id_mismatch', workspaceId: id };
    }
    const file = path.join(path.resolve(o.base), 'personas', pid, 'persona.json');
    const prevExists = isRealFile(file);
    let written;
    try {
      written = atomicWriteJson(file, validated.value);
    } catch (err) {
      return { ok: false, status: 500, error: 'persona_write_failed', detail: String(err?.code || 'io_error') };
    }
    const auditEvent = {
      workspace: id,
      layout: 'v0.2',
      persona_id: pid,
      name_len: validated.value.name.length,
      summary_len: validated.value.summary.length,
      traits: Object.keys(validated.value.traits),
      bytes: written.bytes,
      sha256: written.sha256,
      prev_exists: prevExists,
      atomic: 'tmp+fsync+rename',
      source: 'api',
      host_hook: 'PUT /dsh-lingshu/api/workspaces/:id/persona',
    };
    safeAudit(o.audit, 'persona_write', auditEvent);
    return {
      ok: true,
      status: 200,
      activated: false,
      persona: validated.value,
      workspace: summarizeSandbox(o, id, index[id]),
      audit: auditEvent,
    };
  }
  const target = resolveWriteTarget(o, rawId);
  if (!target.ok) return target;
  const validated = validatePersonaPayload(rawPayload);
  if (!validated.ok) {
    return { ok: false, status: 400, error: validated.error, field: validated.field };
  }
  const file = path.join(target.dataDir, 'persona.json');
  // 旧文件在 rename 前一直可读；这里只记录"写之前是否已有文件"（坏文件也算存在）。
  const prevExists = isRealFile(file);
  let written;
  try {
    written = atomicWriteJson(file, validated.value);
  } catch (err) {
    return { ok: false, status: 500, error: 'persona_write_failed', detail: String(err?.code || 'io_error') };
  }
  const auditEvent = {
    workspace: target.workspaceId,
    legacy: target.legacy,
    persona_id: validated.value.persona_id,
    name_len: validated.value.name.length,
    summary_len: validated.value.summary.length,
    traits: Object.keys(validated.value.traits),
    bytes: written.bytes,
    sha256: written.sha256,
    prev_exists: prevExists,
    atomic: 'tmp+fsync+rename',
    source: 'api',
    host_hook: 'PUT /dsh-lingshu/api/workspaces/:id/persona',
  };
  safeAudit(o.audit, 'persona_write', auditEvent);
  return {
    ok: true,
    status: 200,
    activated: false,
    persona: validated.value,
    workspace: summaryForTarget(o, target),
    audit: auditEvent,
  };
}

/**
 * 创建**未激活**工作区（POST /api/workspaces）：只建目录 +（可选）persona.json，不写 state.json。
 * @returns {{ok:true, status:201, activated:false, workspace:object, audit:object}
 *          | {ok:false, status:number, error:string, field?:string, reason?:string, workspaceId?:string}}
 */
export function createWorkspace(options = {}, rawPayload) {
  const o = normalizeOptions(options);
  // v0.2：沙盒由"项目根 → sandboxId"派生，不允许面板凭空创建目录（会与 mapping 脱节）。
  if (v02Layout(o)) {
    return { ok: false, status: 400, error: 'layout_v02_create_unsupported' };
  }
  if (!o.base) return { ok: false, status: 503, error: 'data_root_unavailable' };
  if (!rawPayload || typeof rawPayload !== 'object' || Array.isArray(rawPayload)) {
    return { ok: false, status: 400, error: 'invalid_body' };
  }
  for (const key of Object.keys(rawPayload)) {
    if (key !== 'workspaceId' && key !== 'persona') {
      return { ok: false, status: 400, error: 'unknown_field', field: key.slice(0, 32) };
    }
  }
  const rawId = typeof rawPayload.workspaceId === 'string' ? rawPayload.workspaceId.trim() : '';
  if (!WORKSPACE_ID_RE.test(rawId)) return { ok: false, status: 400, error: 'invalid_workspace_id' };
  if (rawId === LEGACY_ID) return { ok: false, status: 400, error: 'reserved_workspace_id' };

  const wsRoot = resolveInside(o.base, `${WS_DIR_PREFIX}${rawId}`);
  if (!wsRoot) return { ok: false, status: 400, error: 'invalid_workspace_path' };
  // 冲突判定按 workspace 根目录算（跨 profile 也不允许重名：同一数据根下 id 唯一）。
  if (fs.existsSync(wsRoot)) {
    return { ok: false, status: 409, error: 'workspace_exists', workspaceId: rawId };
  }
  // 先校验 persona，任何非法都不落盘（避免"建了目录再 400"）。
  let persona = null;
  if (rawPayload.persona !== undefined && rawPayload.persona !== null) {
    const validated = validatePersonaPayload(rawPayload.persona);
    if (!validated.ok) {
      return { ok: false, status: 400, error: 'invalid_persona', reason: validated.error, field: validated.field };
    }
    persona = validated.value;
  }
  const dataDir = workspaceDataDir(o.base, rawId, o.pathProfileTag);
  if (!dataDir) return { ok: false, status: 400, error: 'invalid_workspace_path' };

  try {
    fs.mkdirSync(dataDir, { recursive: true });
    if (persona) atomicWriteJson(path.join(dataDir, 'persona.json'), persona);
  } catch (err) {
    // 回滚：wsRoot 是本请求刚建的（前面已判不存在），整棵删掉；不碰任何旧数据。
    try { fs.rmSync(wsRoot, { recursive: true, force: true }); } catch { /* noop */ }
    return { ok: false, status: 500, error: 'workspace_create_failed', detail: String(err?.code || 'io_error') };
  }

  const auditEvent = {
    workspace: rawId,
    activated: false,
    persona: Boolean(persona),
    traits: persona ? Object.keys(persona.traits) : [],
    atomic: 'mkdir+tmp+fsync+rename',
    source: 'api',
    host_hook: 'POST /dsh-lingshu/api/workspaces',
  };
  safeAudit(o.audit, 'workspace_create', auditEvent);
  return {
    ok: true,
    status: 201,
    activated: false,
    workspace: summarize({
      base: o.base,
      dataDir,
      workspaceId: rawId,
      isCurrent: rawId === o.currentId,
      coldTurns: o.coldTurns,
      warmTurns: o.warmTurns,
      policyDir: o.policyDir,
    }),
    audit: auditEvent,
  };
}

// ── v0.2 人格体 ↔ 人格舱（绑定）：创建/列表/绑定/解绑/可选工作区 ────
function personaRoot(o) {
  return path.join(path.resolve(o.base), 'personas');
}

function personaExists(o, pid) {
  return V02_PERSONA_RE.test(pid) && isRealFile(path.join(personaRoot(o), pid, 'persona.json'));
}

/** 列出所有有效人格体（有 persona.json）+ 各自的绑定。 */
export function listPersonas(options = {}) {
  const o = normalizeOptions(options);
  if (!o.base) return { ok: false, status: 503, error: 'data_root_unavailable' };
  const bindings = readMappingBindings(o.base);
  let dirs = [];
  try {
    dirs = fs.readdirSync(personaRoot(o), { withFileTypes: true });
  } catch { dirs = []; }
  const personas = [];
  for (const entry of dirs) {
    if (!entry.isDirectory() || !V02_PERSONA_RE.test(entry.name)) continue;
    const p = readPersona(path.join(personaRoot(o), entry.name, 'persona.json'));
    if (!p) continue;                    // 无 persona.json 的孤儿目录不展示
    personas.push({
      persona_id: entry.name,
      name: p.name,
      summary: p.summary,
      traits: p.traits,
      self_reference: p.self_reference,
      tone: p.tone,
      boundaries: p.boundaries,
      bindings: bindings.filter((b) => b.default_persona === entry.name).map((b) => ({ ...b })),
    });
  }
  personas.sort((a, b) => (a.persona_id < b.persona_id ? -1 : a.persona_id > b.persona_id ? 1 : 0));
  return { ok: true, schemaVersion: 'persona-panel-0.1', personas, bindings };
}

/** 创建人格体：persona.json + 全局记忆空文件 + persona_state 默认值。 */
export function createPersona(options = {}, raw = {}) {
  const o = normalizeOptions(options);
  if (!o.base) return { ok: false, status: 503, error: 'data_root_unavailable' };
  if (!raw || typeof raw !== 'object' || Array.isArray(raw)) {
    return { ok: false, status: 400, error: 'invalid_body' };
  }
  const allowed = new Set(['persona_id', 'name', 'summary', 'traits']);
  for (const key of Object.keys(raw)) {
    if (!allowed.has(key)) return { ok: false, status: 400, error: 'unknown_field', field: key.slice(0, 32) };
  }
  const pid = String(raw.persona_id || `pe_${crypto.randomBytes(4).toString('hex')}`).trim();
  if (!V02_PERSONA_RE.test(pid)) return { ok: false, status: 400, error: 'bad_persona_id' };
  if (personaExists(o, pid)) return { ok: false, status: 409, error: 'persona_exists', persona_id: pid };
  const validated = validatePersonaPayload({
    schema_version: PERSONA_SCHEMA_VERSION,
    persona_id: pid,
    name: raw.name,
    summary: raw.summary === undefined ? '' : raw.summary,
    traits: raw.traits === undefined ? {} : raw.traits,
    confirmed_by_user: true,
  });
  if (!validated.ok) {
    return { ok: false, status: 400, error: validated.error, field: validated.field };
  }
  const dir = path.join(personaRoot(o), pid);
  try {
    fs.mkdirSync(dir, { recursive: true });
    atomicWriteJson(path.join(dir, 'persona.json'), validated.value);
    for (const name of ['global_memory.jsonl', 'global_pending.jsonl']) {
      const file = path.join(dir, name);
      if (!fs.existsSync(file)) fs.writeFileSync(file, '', 'utf8');
    }
    const psFile = path.join(dir, 'persona_state.json');
    if (!fs.existsSync(psFile)) {
      atomicWriteJson(psFile, {
        schema_version: 'persona-state-0.1',
        integral_baseline: {}, emotion_baseline: {}, relations: {},
      });
    }
  } catch {
    try { fs.rmSync(dir, { recursive: true, force: true }); } catch { /* noop */ }
    return { ok: false, status: 500, error: 'persona_create_failed' };
  }
  const persona = {
    persona_id: pid, name: validated.value.name,
    summary: validated.value.summary, traits: validated.value.traits, bindings: [],
  };
  safeAudit(o.audit, 'persona_created', {
    persona_id: pid, name_len: persona.name.length, source: 'api', layout: 'v0.2',
  });
  return { ok: true, status: 201, persona };
}

/** 把一个人格体绑定到一个 DSH 工作区（人格舱）。1 工作区 ≤ 1 人格体。 */
export function bindWorkspace(options = {}, raw = {}) {
  const o = normalizeOptions(options);
  if (!o.base) return { ok: false, status: 503, error: 'data_root_unavailable' };
  const pid = String(raw.persona_id || '').trim();
  const wsRoot = String(raw.workspace_root || '').trim();
  if (!pid || !wsRoot) return { ok: false, status: 400, error: 'invalid_body' };
  if (!personaExists(o, pid)) return { ok: false, status: 404, error: 'persona_not_found', persona_id: pid };
  if (!isRealDir(wsRoot)) {
    return { ok: false, status: 400, error: 'workspace_not_found', workspace_root: wsRoot };
  }
  const canonical = canonicalizePath(wsRoot);
  const bindings = readMappingBindings(o.base);
  const existing = bindings.find((b) => canonicalizePath(b.canonical_path) === canonical);
  if (existing && existing.default_persona !== pid) {
    return {
      ok: false, status: 409, error: 'workspace_already_bound',
      workspace_root: canonical, current_persona_id: existing.default_persona,
    };
  }
  if (existing) return { ok: true, status: 200, already_bound: true, binding: existing };

  const sid = sandboxId(canonical);
  const label = String(raw.label || path.basename(canonical) || '').slice(0, 64);
  const binding = {
    canonical_path: canonical, sandbox_id: sid, default_persona: pid,
    label, bound_at: new Date().toISOString(),
  };
  const written = writeMappingBindings(o.base,
    bindings.filter((b) => canonicalizePath(b.canonical_path) !== canonical).concat([binding]));
  if (!written.ok) return { ok: false, status: 500, error: 'binding_write_failed' };
  try {
    const sdir = path.join(path.resolve(o.base), 'sandboxes', sid);
    fs.mkdirSync(sdir, { recursive: true });
    atomicWriteJson(path.join(sdir, 'sandbox.json'), {
      schema_version: 'sandbox-0.1', sandbox_id: sid, canonical_path: canonical,
      label, created_at: Math.trunc(Date.now() / 1000), profile_tag: null,
    });
    const idxFile = path.join(path.resolve(o.base), 'sandboxes', 'index.json');
    let idx = { schema_version: 'sandbox-index-0.1', sandboxes: {} };
    try {
      idx = JSON.parse(fs.readFileSync(idxFile, 'utf8').replace(/^\uFEFF/, '')) || idx;
    } catch { /* 新建 */ }
    if (!idx.sandboxes || typeof idx.sandboxes !== 'object') idx.sandboxes = {};
    idx.sandboxes[sid] = {
      canonical_path: canonical, label,
      created_at: Math.trunc(Date.now() / 1000), last_active_at: Math.trunc(Date.now() / 1000),
    };
    atomicWriteJson(idxFile, idx);
  } catch {
    return { ok: false, status: 500, error: 'sandbox_metadata_failed' };
  }
  safeAudit(o.audit, 'sandbox_bound', {
    sandbox_id: sid, persona_id: pid, canonical_path: canonical,
    label, source: 'api', layout: 'v0.2',
  });
  return { ok: true, status: 200, binding };
}

/** 解绑（人格舱回到"无人格"）；沙盒数据保留，不删除。 */
export function unbindWorkspace(options = {}, raw = {}) {
  const o = normalizeOptions(options);
  if (!o.base) return { ok: false, status: 503, error: 'data_root_unavailable' };
  const wsRoot = String(raw.workspace_root || '').trim();
  if (!wsRoot) return { ok: false, status: 400, error: 'invalid_body' };
  const canonical = canonicalizePath(wsRoot);
  const bindings = readMappingBindings(o.base);
  const hit = bindings.find((b) => canonicalizePath(b.canonical_path) === canonical);
  if (!hit) return { ok: false, status: 404, error: 'binding_not_found', workspace_root: canonical };
  const written = writeMappingBindings(o.base,
    bindings.filter((b) => canonicalizePath(b.canonical_path) !== canonical));
  if (!written.ok) return { ok: false, status: 500, error: 'binding_write_failed' };
  safeAudit(o.audit, 'sandbox_unbound', {
    sandbox_id: hit.sandbox_id, persona_id: hit.default_persona,
    canonical_path: canonical, source: 'api', layout: 'v0.2',
  });
  return { ok: true, status: 200, removed: hit };
}

/** DSH 会话目录名（--D-LSJH-- / --C-Users-~9648~5CA9-.dsh-x--）→ 工作区路径。 */
function decodeSessionDir(name) {
  if (typeof name !== 'string' || !name.startsWith('--') || !name.endsWith('--')) return null;
  let s = name.slice(2, -2);
  // 编码：非 ASCII → ~XXXX（UTF-16 码元，连续时形如 ~9CB8~9C7C，最后一个不带尾部 ~）。
  // 用负向前瞻而不是"?": 不能把下一个 ~ 吃掉，否则 `~9CB8~9C7C` 只解出前半截。
  s = s.replace(/~([0-9A-Fa-f]{4})(?![0-9A-Fa-f])/g, (_m, hex) => String.fromCharCode(parseInt(hex, 16)));
  const m = /^([A-Za-z])-(.*)$/.exec(s);
  if (!m) return null;
  return `${m[1]}:\\${m[2].replace(/-/g, '\\')}`;
}

/** 可绑定的人格舱候选：当前会话工作区 + DSH sessions 目录（best-effort，失败就只给当前）。 */
export function availableWorkspaces(options = {}) {
  const o = normalizeOptions(options);
  const bindings = o.base ? readMappingBindings(o.base) : [];
  const out = [];
  const seen = new Set();
  const push = (root, source) => {
    let canonical;
    try {
      canonical = canonicalizePath(root);
    } catch { return; }
    if (seen.has(canonical)) return;
    seen.add(canonical);
    const b = bindings.find((x) => canonicalizePath(x.canonical_path) === canonical);
    out.push({
      workspace_root: canonical,
      label: path.basename(canonical) || canonical,
      exists: isRealDir(root),
      source,
      bound_persona_id: b ? b.default_persona : null,
      sandbox_id: b ? b.sandbox_id : null,
    });
  };
  if (o.workspaceRoot) push(o.workspaceRoot, 'current');
  const sessionsRoot = path.join(o.dsHome || '', 'sessions');
  try {
    for (const entry of fs.readdirSync(sessionsRoot, { withFileTypes: true })) {
      if (!entry.isDirectory()) continue;
      const p = decodeSessionDir(entry.name);
      if (p) push(p, 'sessions');
    }
  } catch { /* 宿主目录不存在：只返回当前工作区 */ }
  out.sort((a, b) => (Number(b.source === 'current') - Number(a.source === 'current'))
    || (a.workspace_root < b.workspace_root ? -1 : a.workspace_root > b.workspace_root ? 1 : 0));
  return { ok: true, workspaces: out };
}

/** 更新人格体（persona 级，不经 workspace）：只写 persona.json 白名单字段。 */
export function updatePersona(options = {}, raw = {}) {
  const o = normalizeOptions(options);
  if (!o.base) return { ok: false, status: 503, error: 'data_root_unavailable' };
  const pid = String(raw.persona_id || '').trim();
  if (!personaExists(o, pid)) {
    return { ok: false, status: 404, error: 'persona_not_found', persona_id: pid };
  }
  const validated = validatePersonaPayload({
    ...raw,
    schema_version: PERSONA_SCHEMA_VERSION,
    persona_id: pid,
    confirmed_by_user: true,
  });
  if (!validated.ok) {
    return { ok: false, status: 400, error: validated.error, field: validated.field };
  }
  const file = path.join(personaRoot(o), pid, 'persona.json');
  try {
    atomicWriteJson(file, validated.value);
  } catch {
    return { ok: false, status: 500, error: 'persona_write_failed' };
  }
  safeAudit(o.audit, 'persona_updated', {
    persona_id: pid, name_len: validated.value.name.length,
    has_tone: Boolean(validated.value.tone), source: 'api', layout: 'v0.2',
  });
  return {
    ok: true, status: 200,
    persona: { persona_id: pid, ...validated.value },
  };
}

export default {
  listWorkspaces,
  readWorkspace,
  writePersona,
  createWorkspace,
  listPersonas,
  createPersona,
  updatePersona,
  bindWorkspace,
  unbindWorkspace,
  availableWorkspaces,
  validatePersonaPayload,
  API_SCHEMA_VERSION,
  LEGACY_ID,
  PERSONA_SCHEMA_VERSION,
  PERSONA_TRAITS,
};
