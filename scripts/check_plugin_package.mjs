#!/usr/bin/env node
// check_plugin_package.mjs —— 发布前体检。
//
// 目的：保证「别的用户在 DSH 的『添加插件』里输入 dsh-lingshu / 本地路径 / 压缩包」
// 装完之后不是残缺的。DSH 安装器走的是 pnpm 打包安装（只带 package.json 的 files
// 清单 + 根文件），所以任何运行时文件漏在清单外，装完才会以"缺文件/降级"的形式炸。
//
// 用法：
//   node scripts/check_plugin_package.mjs
//   node scripts/check_plugin_package.mjs --json
//
// 退出码：0 = 全部通过；1 = 有阻塞项（发布前必须修）。

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const PLUGIN = path.join(ROOT, 'plugin', 'dsh-lingshu');
const CONTRACTS_POLICY = path.join(ROOT, 'contracts', 'supply-chain-policy.json');
const JSON_OUT = process.argv.includes('--json');

const checks = [];
const check = (id, level, message, detail) => {
  checks.push({ id, level, message, ...(detail === undefined ? {} : { detail }) });
};
const fail = (id, message, detail) => check(id, 'fail', message, detail);
const warn = (id, message, detail) => check(id, 'warn', message, detail);
const pass = (id, message) => check(id, 'pass', message);

const readJson = (file) => JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
const sha256 = (file) => crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
const isFile = (file) => fs.existsSync(file) && fs.statSync(file).isFile();

// ── 1. 清单本体 ──────────────────────────────────────────────────────────────
let pkg;
try {
  pkg = readJson(path.join(PLUGIN, 'package.json'));
  pass('manifest', 'package.json 可解析');
} catch (error) {
  fail('manifest', `package.json 不可读：${error.message}`);
  report();
  process.exit(1);
}

if (pkg.private === true) {
  fail('not-private', 'package.json 仍声明 "private": true —— npm publish 会直接拒绝，其他用户拿不到包名安装');
} else {
  pass('not-private', '包未被标记为 private，可发布');
}
for (const field of ['name', 'version', 'description', 'license']) {
  if (typeof pkg[field] !== 'string' || pkg[field].trim() === '') fail(`field:${field}`, `package.json 缺 ${field}`);
}
if (typeof pkg.repository?.url !== 'string') warn('field:repository', '缺 repository.url，npm 页面会没有源码链接');
if (pkg.name !== 'dsh-lingshu') warn('name', `包名是 ${pkg.name}；文档里的安装示例按 dsh-lingshu 写`);

// ── 2. DSH 组合包声明 ───────────────────────────────────────────────────────
const patchRel = pkg.dsh?.bundle?.patch;
if (typeof patchRel !== 'string' || patchRel.trim() === '') {
  fail('dsh-bundle', '缺 dsh.bundle.patch —— 安装器会以 not-a-bundle 拒绝，或装成普通依赖不启用');
} else {
  const patchFile = path.join(PLUGIN, patchRel);
  if (!isFile(patchFile)) fail('dsh-bundle-file', `dsh.bundle.patch 指向的文件不存在：${patchRel}`);
  else {
    const text = fs.readFileSync(patchFile, 'utf8');
    if (!/^\s*-\s*insert:/m.test(text)) fail('dsh-bundle-shape', `${patchRel} 里没有顶层 insert 列表`);
    else pass('dsh-bundle', `bundle patch 存在：${patchRel}`);
    if (!/name:\s*['"]?dsh-lingshu['"]?\s*$/m.test(text)) {
      warn('dsh-bundle-name', `${patchRel} 里没有 name: 'dsh-lingshu' 的行，清单里的包名与 patch 不一致时 loader 会解析失败`);
    }
  }
}

// ── 3. files 清单逐项存在 ───────────────────────────────────────────────────
const files = Array.isArray(pkg.files) ? pkg.files : [];
if (files.length === 0) fail('files', '缺 files 白名单 —— 打包内容不可控');
const missing = files.filter((entry) => !fs.existsSync(path.join(PLUGIN, entry)));
if (missing.length > 0) fail('files-missing', 'files 清单里有不存在的条目', missing);
else pass('files', `files 清单 ${files.length} 项全部存在`);

// ── 4. 供应链策略副本：必须与 contracts 一致，否则安装态静默降级 ──────────────
const policyCopy = path.join(PLUGIN, 'supply-chain-policy.json');
if (!isFile(policyCopy)) {
  fail('policy-copy', '插件目录缺 supply-chain-policy.json 副本；用 tgz / GitHub 安装后 supply-chain.js 会退回 FALLBACK_POLICY（闸门降级）');
} else if (!files.includes('supply-chain-policy.json')) {
  fail('policy-files', 'supply-chain-policy.json 没进 files 清单，打包时会被丢掉');
} else if (!isFile(CONTRACTS_POLICY)) {
  warn('policy-source', '找不到 contracts/supply-chain-policy.json，无法校验副本一致性');
} else {
  const same = sha256(policyCopy) === sha256(CONTRACTS_POLICY);
  if (same) pass('policy-copy', '策略副本与 contracts/supply-chain-policy.json 一致');
  else fail('policy-drift', '策略副本与 contracts/supply-chain-policy.json 不一致（先同步再发布，否则发布包与仓库策略两套）');
}

// ── 5. 运行时依赖图：入口出发能到达的本地文件必须都在 files 里 ─────────────────
const covered = (rel) => files.some((entry) => (entry.endsWith('/') ? rel === entry.slice(0, -1) || rel.startsWith(entry) : rel === entry));
const resolved = new Set();
const queue = [pkg.main ?? 'index.js'];
if (typeof pkg.dsh?.client === 'object') queue.push('client.js');
while (queue.length > 0) {
  const rel = queue.shift();
  if (resolved.has(rel)) continue;
  const abs = path.join(PLUGIN, rel);
  if (!isFile(abs)) {
    fail('entry-missing', `入口/依赖文件不存在：${rel}`);
    continue;
  }
  resolved.add(rel);
  if (!covered(rel)) fail('files-coverage', `运行时文件不在 files 清单里：${rel}`);
  const source = fs.readFileSync(abs, 'utf8');
  const pattern = /(?:from\s+|import\s*\(\s*)['"](\.[^'"]+)['"]/g;
  for (const match of source.matchAll(pattern)) {
    const target = path.normalize(path.join(path.dirname(rel), match[1])).replaceAll('\\', '/');
    if (target.startsWith('..')) {
      fail('escape', `${rel} 引用了包外文件 ${target}（打包安装后不存在）`);
      continue;
    }
    queue.push(target);
  }
}
if (resolved.size > 0 && !checks.some((item) => item.level === 'fail')) {
  pass('files-coverage', `入口依赖图 ${resolved.size} 个文件全部在 files 清单内`);
}

// ── 6. 安装后的客户端补充件 ─────────────────────────────────────────────────
for (const rel of (pkg.dsh?.client?.inject ?? []).length > 0 ? ['client.js', 'ui/dashboard.html'] : ['ui/dashboard.html']) {
  if (!isFile(path.join(PLUGIN, rel))) fail('asset', `缺客户端资源：${rel}`);
}

function report() {
  if (JSON_OUT) {
    console.log(JSON.stringify({ ok: !checks.some((item) => item.level === 'fail'), checks }, null, 2));
    return;
  }
  const icon = { pass: 'PASS', warn: 'WARN', fail: 'FAIL' };
  for (const item of checks) {
    const suffix = item.detail === undefined ? '' : ` -> ${Array.isArray(item.detail) ? item.detail.join(', ') : item.detail}`;
    console.log(`[${icon[item.level]}] ${item.id}: ${item.message}${suffix}`);
  }
  const failed = checks.filter((item) => item.level === 'fail').length;
  const warned = checks.filter((item) => item.level === 'warn').length;
  console.log(`\n${failed === 0 ? 'OK' : 'BLOCKED'} — ${failed} 阻塞项 / ${warned} 提醒项`);
  if (failed === 0) {
    console.log('下一步（发布给其他用户）：npm login && npm publish --dry-run 先看内容，再去掉 --dry-run');
  }
}

report();
process.exit(checks.some((item) => item.level === 'fail') ? 1 : 0);
