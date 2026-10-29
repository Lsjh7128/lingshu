// demo/lib.mjs — 沙盒 demo 的共享事实：路径、DSH 定位、隔离断言、只读数据读取。
//
// 为什么单独一个文件：demo/bootstrap.mjs 与 demo/chat.mjs 必须用**同一套**口径回答
// "DSH 装在哪、数据写在哪、什么算越界"，任何一处口径漂移都会让"绝对隔离"变成口号。
// 本文件不写任何文件（ensureDir / rmrf 除外），不 import 第三方依赖。
//
// 边界（诚实声明）：
//   · 本 demo 使用**本机已装的 DSH**（$DSH_HOME 及其已登录态的模型凭据）——这是"零配置"的前提；
//   · 所有 LINGSHU 数据、DSH 会话、DSH 存储都重定向到仓库内 ./.lingshu-demo-data；
//   · 唯一写进 $DSH_HOME 的是可抛弃 profile `lingshu-demo-profile`（--clean 删除）；
//   · 绝不写 $DSH_HOME/plugins/dsh-lingshu，也绝不碰 desktop profile。

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

/** demo/ 目录（绝对路径）。 */
export const DEMO_DIR = path.dirname(fileURLToPath(import.meta.url));
/** 仓库根（绝对路径）。 */
export const REPO_ROOT = path.dirname(DEMO_DIR);
/** 沙盒 profile 名（可抛弃；不要改成 desktop）。 */
export const PROFILE_NAME = 'lingshu-demo-profile';
/** 强制覆盖的 LINGSHU_DATA_DIR：字面量必须与需求一致（相对仓库根）。 */
export const DATA_DIR_REL = './.lingshu-demo-data';
/** 沙盒数据目录（绝对路径）。 */
export const DATA_DIR = path.resolve(REPO_ROOT, '.lingshu-demo-data');
/** LINGSHU 源插件目录（local link 目标）。 */
export const PLUGIN_DIR = path.join(REPO_ROOT, 'plugin', 'dsh-lingshu');
/** --patch overlay（人格体阿福 + 会话/存储重定向）。 */
export const DEMO_PATCH = path.join(DEMO_DIR, 'cordis.demo.yml');
/** 演示用相变窗口（tokens）。只用于让 70%/85% 在短会话里可达，不是产品阈值。 */
export const DEFAULT_DEMO_WINDOW = 3000;

const IS_WIN = process.platform === 'win32';

/** $DSH_HOME：优先 env，其次 ~/.dsh（与 DSH 自身口径一致；本 demo 只读它来定位）。 */
export function dshHome(env = process.env) {
  const h = String(env.DSH_HOME || '').trim();
  return h ? path.resolve(h) : path.join(os.homedir(), '.dsh');
}

/** 沙盒 profile 目录（$DSH_HOME/profiles/lingshu-demo-profile）。 */
export function profileDir(home = dshHome()) {
  return path.join(home, 'profiles', PROFILE_NAME);
}

/** 真实插件数据目录（红线：任何 demo 路径都不许是它）。 */
export function realPluginDataDir(home = dshHome()) {
  return path.join(home, 'plugins', 'dsh-lingshu');
}

/** 路径是否位于 parent 之内（Windows 大小写不敏感）。 */
export function isInside(child, parent) {
  const c = normalizePath(path.resolve(child));
  const p = normalizePath(path.resolve(parent));
  if (c === p) return true;
  return c.startsWith(p.endsWith(path.sep) ? p : p + path.sep);
}

function normalizePath(p) {
  const s = path.resolve(p);
  return IS_WIN ? s.toLowerCase() : s;
}

/**
 * 红线断言：数据目录不得是真实插件数据目录，也不得落在 $DSH_HOME 内。
 * 失败即抛错（fail-closed），不做"尽力而为"。
 */
export function assertNotRealPluginDataDir(dir, home = dshHome()) {
  const target = path.resolve(dir);
  const real = realPluginDataDir(home);
  if (normalizePath(target) === normalizePath(real)) {
    throw new Error('拒绝写入真实插件数据目录：' + target + '（红线：demo 只写隔离目录）');
  }
  if (isInside(target, home)) {
    throw new Error('拒绝写入 $DSH_HOME 内部：' + target + '（红线：DSH 家目录只放可抛弃 profile）');
  }
  return target;
}

/**
 * 沙盒数据目录断言：必须位于仓库内，且通过红线断言。
 * 用于 demo/bootstrap.mjs 与 demo/chat.mjs（不允许用户用 --data-dir 指向任意位置）。
 */
export function assertSandboxDataDir(dir = DATA_DIR, home = dshHome()) {
  const target = assertNotRealPluginDataDir(dir, home);
  if (!isInside(target, REPO_ROOT)) {
    throw new Error('沙盒数据目录必须在仓库内：' + target + '（仓库根：' + REPO_ROOT + '）');
  }
  return target;
}

function isFile(p) {
  try {
    return fs.statSync(p).isFile();
  } catch {
    return false;
  }
}

/**
 * 定位 DSH CLI（<pkg>/lib/bin.js）。返回 { binJs, source }；找不到返回 null。
 *
 * 查找顺序（前三个是"正常安装"，最后一个是"曾用 npx 跑过"的缓存兜底）：
 *   1) env DSH_BIN（显式指定，dev/测试也能用 stub）
 *   2) $DSH_HOME/profiles/node_modules/@deepseek-ai/dsh/lib/bin.js（本机 DSH 自己的位置）
 *   3) npm 全局 node_modules / node 安装前缀（npm i -g @deepseek-ai/dsh 的路径）
 *   4) npx 缓存 _npx/<hash>/node_modules/@deepseek-ai/dsh/lib/bin.js（取最新）
 */
export function resolveDshCli(env = process.env, home = dshHome(env)) {
  const seen = new Set();
  const pick = (candidate) => {
    if (!candidate) return null;
    const p = path.resolve(candidate);
    if (seen.has(normalizePath(p))) return null;
    seen.add(normalizePath(p));
    return isFile(p) ? p : null;
  };

  const direct = pick(String(env.DSH_BIN || '').trim());
  if (direct) return { binJs: direct, source: 'DSH_BIN' };

  const inHome = pick(path.join(home, 'profiles', 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js'));
  if (inHome) return { binJs: inHome, source: 'DSH_HOME' };

  for (const root of npmGlobalRoots(env)) {
    const found = pick(path.join(root, '@deepseek-ai', 'dsh', 'lib', 'bin.js'));
    if (found) return { binJs: found, source: 'npm-global' };
  }

  for (const cache of npxCacheRoots(env)) {
    const found = pickNewestNpxDsh(cache);
    if (found) return { binJs: found, source: 'npx-cache' };
  }

  return null;
}

/** npm 全局 node_modules 候选（只在本地拼路径，不 spawn npm，避免拖慢启动）。 */
export function npmGlobalRoots(env = process.env) {
  const roots = [];
  if (IS_WIN) {
    if (env.APPDATA) roots.push(path.join(env.APPDATA, 'npm', 'node_modules'));
    if (env.LOCALAPPDATA) roots.push(path.join(env.LOCALAPPDATA, 'npm', 'node_modules'));
  } else {
    roots.push('/usr/local/lib/node_modules');
    roots.push('/usr/lib/node_modules');
    roots.push('/opt/homebrew/lib/node_modules');
  }
  // 当前 node 的安装前缀（<prefix>/lib/node_modules；Windows 为 <prefix>/node_modules）
  try {
    const execDir = path.dirname(process.execPath);
    roots.push(IS_WIN ? path.join(execDir, 'node_modules')
      : path.join(execDir, '..', 'lib', 'node_modules'));
    roots.push(IS_WIN ? path.join(execDir, '..', 'node_modules')
      : path.join(execDir, '..', '..', 'lib', 'node_modules'));
  } catch { /* 忽略 */ }
  return actualDirs(roots);
}

/** npx 缓存根候选。 */
export function npxCacheRoots(env = process.env) {
  const roots = [];
  if (IS_WIN) {
    if (env.LOCALAPPDATA) roots.push(path.join(env.LOCALAPPDATA, 'npm-cache', '_npx'));
  } else {
    roots.push(path.join(os.homedir(), '.npm', '_npx'));
  }
  return actualDirs(roots);
}

function actualDirs(candidates) {
  const out = [];
  for (const c of candidates) {
    try {
      if (fs.statSync(c).isDirectory()) out.push(path.resolve(c));
    } catch { /* 不存在就跳过 */ }
  }
  return out;
}

/** 在 _npx/<hash>/node_modules/@deepseek-ai/dsh/lib/bin.js 里取 mtime 最新的一份。 */
function pickNewestNpxDsh(cacheRoot) {
  let best = null;
  let entries = [];
  try {
    entries = fs.readdirSync(cacheRoot, { withFileTypes: true });
  } catch {
    return null;
  }
  for (const ent of entries) {
    if (!ent.isDirectory()) continue;
    const bin = path.join(cacheRoot, ent.name, 'node_modules', '@deepseek-ai', 'dsh', 'lib', 'bin.js');
    if (!isFile(bin)) continue;
    if (!best) { best = { bin, mtime: mtimeOf(bin) }; continue; }
    const m = mtimeOf(bin);
    if (m > best.mtime) best = { bin, mtime: m };
  }
  return best ? best.bin : null;
}

function mtimeOf(p) {
  try {
    return fs.statSync(p).mtimeMs;
  } catch {
    return 0;
  }
}

/** 找可执行文件（pnpm / npm），返回绝对路径或 null。只在 Windows 上扫 PATHEXT 的 .cmd/.exe。 */
export function resolveExecutable(name, env = process.env) {
  const pathVar = IS_WIN ? String(env.Path || env.PATH || '') : String(env.PATH || '');
  const exts = IS_WIN ? String(env.PATHEXT || '.COM;.EXE;.BAT;.CMD').split(';').filter(Boolean) : [''];
  for (const dir of pathVar.split(IS_WIN ? ';' : ':')) {
    if (!dir) continue;
    for (const ext of exts) {
      const candidate = path.join(dir, name + (IS_WIN ? ext.toLowerCase() : ''));
      if (isFile(candidate)) return candidate;
      if (IS_WIN) {
        const upper = path.join(dir, name + ext.toUpperCase());
        if (isFile(upper)) return upper;
      }
    }
    // 无扩展名（POSIX 或 Windows 的 .exe 已覆盖）
    const raw = path.join(dir, name);
    if (isFile(raw)) return raw;
  }
  return null;
}

/** 读 JSON（容忍 BOM；失败返回 null，绝不抛）。 */
export function readJson(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
  } catch {
    return null;
  }
}

/** 读 JSONL（容忍 BOM / 坏行；坏行跳过）。 */
export function readJsonl(file) {
  try {
    const raw = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
    return raw.split('\n')
      .map((l) => l.replace(/^\uFEFF/, '').trim())
      .filter(Boolean)
      .map((l) => { try { return JSON.parse(l); } catch { return null; } })
      .filter(Boolean);
  } catch {
    return [];
  }
}

/** 插件 state.json（不存在返回 {}）。 */
export function readState(dataDir = DATA_DIR) {
  return readJson(path.join(dataDir, 'state.json')) || {};
}

/** 插件记忆（与 index.js#readMemories 同语义：JSONL 只追加，同 id 后写覆盖）。 */
export function readMemories(dataDir = DATA_DIR) {
  const byId = new Map();
  for (const e of readJsonl(path.join(dataDir, 'memory.jsonl'))) byId.set(String(e.id), e);
  return [...byId.values()];
}

/** 审计尾部 n 条。 */
export function tailAudit(dataDir = DATA_DIR, n = 10) {
  const all = readJsonl(path.join(dataDir, 'audit.jsonl'));
  return all.slice(-Math.max(0, Number(n) || 0));
}

/** 审计按 kind 计数。 */
export function auditKindCounts(dataDir = DATA_DIR) {
  const counts = new Map();
  for (const ev of readJsonl(path.join(dataDir, 'audit.jsonl'))) {
    const k = String(ev && ev.kind || 'unknown');
    counts.set(k, (counts.get(k) || 0) + 1);
  }
  return counts;
}

/** 当前控制论状态摘要（相变 / STPA / 记忆 / 审计）。 */
export function controlSnapshot(dataDir = DATA_DIR) {
  const st = readState(dataDir);
  const c = st.control || {};
  const phase = c.phase || {};
  const audit = c.audit || {};
  const counts = auditKindCounts(dataDir);
  const mem = readMemories(dataDir);
  return {
    turns: Number(st.turns || 0),
    ratio: Number(phase.ratio || 0),
    phase: String(phase.phase || 'unknown'),
    anchorRequired: Boolean(phase.anchorRequired),
    blocked: Boolean(c.blocked),
    verdict: String(audit.verdict || 'unknown'),
    severity: audit.severity || null,
    ucas: Array.isArray(audit.ucas) ? audit.ucas : [],
    memories: mem.filter((e) => e.status === 'active').length,
    memoryTotal: mem.length,
    audits: [...counts.values()].reduce((a, b) => a + b, 0),
    auditKinds: Object.fromEntries(counts),
    preflightBlock: counts.get('preflight_block') || 0,
  };
}

export function ensureDir(dir) {
  fs.mkdirSync(dir, { recursive: true });
}

/** 删目录（不存在=成功；不做危险路径猜测：调用方必须先过断言）。 */
export function removeDir(dir) {
  let removed = false;
  try {
    fs.rmSync(dir, { recursive: true, force: true });
    removed = true;
  } catch {
    removed = false;
  }
  return removed;
}

/** 建目录连接：宿主要能从插件包目录解析自己的包名（缺它插件会静默不激活）。 */
export function ensurePluginSelfLink(pluginDir = PLUGIN_DIR) {
  const linkParent = path.join(path.dirname(pluginDir), 'node_modules');
  const linkPath = path.join(linkParent, 'dsh-lingshu');
  let existing = null;
  try {
    existing = fs.lstatSync(linkPath);
  } catch { /* 不存在 */ }
  if (existing) return { ok: true, created: false, linkPath };
  ensureDir(linkParent);
  try {
    fs.symlinkSync(pluginDir, linkPath, IS_WIN ? 'junction' : 'dir');
    return { ok: true, created: true, linkPath };
  } catch (e) {
    return { ok: false, created: false, linkPath, error: String(e && e.message || e) };
  }
}

/** 人类可读的路径（相对仓库根时用 ./ 前缀）。 */
export function prettyPath(p, repoRoot = REPO_ROOT) {
  const abs = path.resolve(p);
  if (isInside(abs, repoRoot)) {
    const rel = path.relative(repoRoot, abs);
    return rel ? './' + rel.split(path.sep).join('/') : '.';
  }
  return abs;
}

/** Node 版本是否满足 DSH 下限（20.10+；DSH 官方要求 >=20.10）。 */
export function nodeVersionOk(version = process.versions.node) {
  const [maj, min] = String(version).split('.').map(Number);
  return maj > 20 || (maj === 20 && min >= 10);
}
