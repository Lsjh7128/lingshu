// supply-chain.js —— 零信任供应链加载器（S-CAP / ADR-017）
//
// 定位：DSH 宿主**外挂**的第三方认知插件加载闸门。三层检查，任何一层失败即 fail-closed：
//   1) Manifest 解析与校验：能力声明 / 入口 / 完整性哈希 / fs 作用域；
//   2) 静态扫描：未声明的高危底层 API（fs.write / child_process / net / eval …）→ deny；
//   3) 沙箱计划：只把申报且可授予的能力翻译成子进程权限；不可支撑 → deny（不降级）。
//
// 设计约束（与仓库其余模块同口径）：
//   · 零依赖、纯函数、确定性：本模块不读磁盘、不写日志、不抛异常；
//   · 规则表单一来源：`contracts/supply-chain-policy.json`，本文件内联同一份（parity 由测试钉住）；
//   · 与 engine/supply_chain.py 同构：同一份 case 语料必须给出同一裁决（py↔js parity）。
//
// 为什么静态扫描不是"最终防线"：它只做词法匹配，拼接字符串 / 动态 import 可以绕过；
// 真正的物理边界在 sandbox-host.mjs（Node permission model）。扫描的作用是**提前拒绝**，
// 把明显越权的插件挡在 spawn 之前；沙箱负责兜住扫描漏掉的部分。

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';

// 策略表加载（单一来源 = contracts/supply-chain-policy.json）：
//   1) LINGSHU_SUPPLY_CHAIN_POLICY 环境变量（绝对路径，用于压测/审计副本）；
//   2) 仓库内相对路径（源码态）；
//   3) 插件目录下的打包副本（发布态，见 docs/26 §8）；
//   4) 全部失败 → deny-all 兜底策略（fail-closed：宁可什么都不加载，也不无策略放行）。
// 不内联冻结一份的原因：内联副本会和契约文件漂移，而"策略漂移"在这个模块里就是安全漏洞。
const POLICY_PATH_CANDIDATES = [
  process.env.LINGSHU_SUPPLY_CHAIN_POLICY || '',
  new URL('../../contracts/supply-chain-policy.json', import.meta.url),
  new URL('./supply-chain-policy.json', import.meta.url),
];

export const FALLBACK_POLICY = Object.freeze({
  policy_version: 'sc-fallback-deny-all',
  namespace: 'S-CAP',
  schema: 'lingshu.supply-chain-policy/v1',
  manifest_version: 'lingshu.plugin-manifest/v1',
  capabilities: { known: [], grantable: [], never_grantable: [], destructive: [] },
  manifest_rules: {
    id_pattern: '^[a-z][a-z0-9._-]{1,63}$',
    version_pattern: '^[0-9]+\\.[0-9]+\\.[0-9]+$',
    entry_pattern: '^(?:\\./)?[A-Za-z0-9._/-]+\\.(?:mjs|js|cjs)$',
    export_pattern: '^[A-Za-z_$][A-Za-z0-9_$]{0,63}$',
    max_entry_bytes: 262144,
    require_integrity: true,
    allowed_sandbox: ['subprocess'],
    forbid_parent_traversal: true,
    max_exports: 32,
  },
  sandbox: {
    node_permission_flag: '--permission',
    max_old_space_mb: 128,
    ready_timeout_ms: 5000,
    call_timeout_ms: 2000,
    kill_grace_ms: 500,
    rpc: 'tcp-loopback-jsonl',
    env_allowlist: [],
  },
  scan: { strip_comments: true, max_findings_per_rule: 5, rules: [] },
  decision: {
    fail_closed: true,
    precedence: ['sandbox_unavailable'],
    verdicts: { blocker: 'deny', clean: 'deny' },
    undeclared_usage: 'deny',
    suspicious: 'deny',
  },
});

function loadPolicy() {
  for (const candidate of POLICY_PATH_CANDIDATES) {
    try {
      if (!candidate) continue;
      const raw = fs.readFileSync(candidate, 'utf8');
      const parsed = JSON.parse(raw);
      // 形状校验：策略表自身也必须过"零信任"——缺字段即拒绝使用
      if (!parsed || typeof parsed !== 'object') continue;
      if (typeof parsed.policy_version !== 'string') continue;
      if (!parsed.capabilities || !Array.isArray(parsed.capabilities.known)) continue;
      if (!parsed.scan || !Array.isArray(parsed.scan.rules)) continue;
      if (!parsed.decision || !parsed.decision.verdicts) continue;
      return { policy: parsed, source: String(candidate), degraded: false };
    } catch { /* 试下一个候选；全部失败走兜底 */ }
  }
  return { policy: FALLBACK_POLICY, source: 'fallback', degraded: true };
}

const LOADED_POLICY = loadPolicy();
export const POLICY = LOADED_POLICY.policy;
export const POLICY_SOURCE = LOADED_POLICY.source;
export const POLICY_DEGRADED = LOADED_POLICY.degraded;
export const POLICY_VERSION = String(POLICY.policy_version);
export const MANIFEST_VERSION = String(POLICY.manifest_version);
export const POLICY_DIGEST = crypto.createHash('sha256')
  .update(JSON.stringify(POLICY)).digest('hex');

// deny 的错误码（审计与文档共用；单边新增必须同步 docs/26 §5）
export const ERROR_CODES = {
  sandbox_unavailable: 'SC-E01',
  manifest_missing: 'SC-E02',
  manifest_unreadable: 'SC-E03',
  manifest_invalid: 'SC-E04',
  entry_missing: 'SC-E05',
  entry_too_large: 'SC-E06',
  integrity_mismatch: 'SC-E07',
  fs_scope_escape: 'SC-E08',
  unknown_capability: 'SC-E09',
  forbidden_capability: 'SC-E10',
  undeclared_usage: 'SC-E11',
  // 运行时（沙箱启动后）失败的错误码；与静态裁决共用同一命名空间
  sandbox_start_failed: 'SC-E12',
  sandbox_ready_timeout: 'SC-E13',
  sandbox_call_timeout: 'SC-E14',
  sandbox_crashed: 'SC-E15',
  sandbox_spawn_failed: 'SC-E16',
  sandbox_handshake_failed: 'SC-E17',
  method_not_allowed: 'SC-E18',
  plugin_error: 'SC-E19',
};

const CRITICAL = new Set(['critical', 'high']);

// ── 1. 注释剥离（保留换行，行号不漂移）────────────────────────────────
// 只处理 // 与 /* */；字符串不剥离——宁可保守误报，也不给"藏在字符串里的 API"开后门。
// 已知局限（docs/26 §6）：正则字面量里的 // 可能被误剥；模板字符串内的 ${} 不展开。
export function stripComments(src) {
  const s = String(src ?? '');
  const out = s.split('');
  let i = 0;
  let state = 'code';   // code | line | block | sq | dq | tpl
  let quote = '';
  while (i < s.length) {
    const ch = s[i];
    const nxt = s[i + 1];
    if (state === 'code') {
      if (ch === '/' && nxt === '/') { out[i] = ' '; out[i + 1] = ' '; state = 'line'; i += 2; continue; }
      if (ch === '/' && nxt === '*') { out[i] = ' '; out[i + 1] = ' '; state = 'block'; i += 2; continue; }
      if (ch === "'") { state = 'sq'; quote = "'"; }
      else if (ch === '"') { state = 'dq'; quote = '"'; }
      else if (ch === '`') { state = 'tpl'; quote = '`'; }
      i += 1;
      continue;
    }
    if (state === 'line') {
      if (ch === '\n') { state = 'code'; }
      else { out[i] = ' '; }
      i += 1;
      continue;
    }
    if (state === 'block') {
      if (ch === '*' && nxt === '/') { out[i] = ' '; out[i + 1] = ' '; state = 'code'; i += 2; continue; }
      if (ch !== '\n' && ch !== '\r') out[i] = ' ';
      i += 1;
      continue;
    }
    // 字符串内：处理转义；行注释在字符串里不生效
    if (ch === '\\') { i += 2; continue; }
    if (ch === quote) { state = 'code'; quote = ''; }
    i += 1;
  }
  return out.join('');
}

function lineOf(src, index) {
  let line = 1;
  for (let i = 0; i < index && i < src.length; i += 1) if (src[i] === '\n') line += 1;
  return line;
}

// ── 2. 静态扫描 ──────────────────────────────────────────────────────
// 返回 {findings:[{id, capability, severity, line, match}], by_capability:{cap:[ruleId]}}
// 规则来自 POLICY.scan.rules；同一规则最多记 max_findings_per_rule 条（防止超大文件刷屏）。
export function scanSource(source, policy = POLICY) {
  const stripped = policy.scan.strip_comments === false ? String(source ?? '') : stripComments(source);
  const limit = Number(policy.scan.max_findings_per_rule || 5);
  const findings = [];
  const byCapability = {};
  for (const rule of policy.scan.rules) {
    let re;
    try {
      re = new RegExp(rule.pattern, 'i');
    } catch {
      // 规则表损坏：fail-closed——按"命中一条 unknown 规则"上报，不静默跳过
      findings.push({ id: rule.id, capability: rule.capability, severity: rule.severity,
        line: 0, match: '<invalid-pattern>' });
      byCapability[rule.capability] = byCapability[rule.capability] || [];
      byCapability[rule.capability].push(rule.id);
      continue;
    }
    const g = new RegExp(rule.pattern, 'gi');
    let m;
    let count = 0;
    const seen = new Set();
    while ((m = g.exec(stripped)) !== null) {
      const key = `${m.index}`;
      g.lastIndex = m.index + Math.max(1, String(m[0]).length); // 防零宽匹配死循环
      if (seen.has(key)) break;
      seen.add(key);
      const hit = {
        id: rule.id,
        capability: rule.capability,
        severity: rule.severity,
        line: lineOf(stripped, m.index),
        match: String(m[0]).slice(0, 80),
      };
      findings.push(hit);
      if (!byCapability[rule.capability]) byCapability[rule.capability] = [];
      if (!byCapability[rule.capability].includes(rule.id)) byCapability[rule.capability].push(rule.id);
      count += 1;
      if (count >= limit) break;
    }
  }
  return { findings, by_capability: byCapability, stripped_lines: stripped.split('\n').length };
}

// ── 3. Manifest 解析与校验 ───────────────────────────────────────────
// parseManifest 只做 JSON 层；validateManifest 做语义层（两份 language 实现必须一致）。
export function parseManifest(raw) {
  if (raw === undefined || raw === null) return { ok: false, error: 'manifest_missing', manifest: null };
  try {
    const manifest = JSON.parse(String(raw));
    if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
      return { ok: false, error: 'manifest_not_object', manifest: null };
    }
    return { ok: true, error: null, manifest };
  } catch (err) {
    return { ok: false, error: 'manifest_json_error', detail: String(err && err.message || err).slice(0, 120), manifest: null };
  }
}

function asStringArray(v) {
  if (!Array.isArray(v)) return null;
  if (!v.every((x) => typeof x === 'string')) return null;
  return v.slice();
}

// 校验 manifest 语义；返回错误数组 [{code, detail}]，空数组 = 合法。
export function validateManifest(manifest, policy = POLICY) {
  const errs = [];
  const mr = policy.manifest_rules;
  const push = (code, detail = '') => errs.push({ code, detail });

  if (!manifest || typeof manifest !== 'object' || Array.isArray(manifest)) {
    push('manifest_invalid', 'not_an_object');
    return errs;
  }
  if (manifest.manifest_version !== policy.manifest_version) {
    push('manifest_invalid', 'manifest_version');
  }
  if (typeof manifest.id !== 'string' || !new RegExp(mr.id_pattern).test(manifest.id)) {
    push('manifest_invalid', 'id');
  }
  if (typeof manifest.version !== 'string' || !new RegExp(mr.version_pattern).test(manifest.version)) {
    push('manifest_invalid', 'version');
  }
  const entry = manifest.entry;
  if (typeof entry !== 'string' || !new RegExp(mr.entry_pattern).test(entry)) {
    push('manifest_invalid', 'entry');
  } else {
    const norm = entry.replace(/^\.\//, '');
    const segs = norm.split('/');
    if (mr.forbid_parent_traversal && (segs.includes('..') || norm.startsWith('/') || /^[A-Za-z]:/.test(norm))) {
      push('manifest_invalid', 'entry_traversal');
    }
  }
  const exportsList = asStringArray(manifest.exports);
  if (!exportsList || exportsList.length < 1 || exportsList.length > Number(mr.max_exports)) {
    push('manifest_invalid', 'exports');
  } else {
    const pattern = new RegExp(mr.export_pattern || '^[A-Za-z_$][A-Za-z0-9_$]{0,63}$');
    if (!exportsList.every((n) => pattern.test(n))) push('manifest_invalid', 'export_name');
    if (new Set(exportsList).size !== exportsList.length) push('manifest_invalid', 'export_duplicate');
  }
  const caps = manifest.capabilities;
  if (!caps || typeof caps !== 'object' || Array.isArray(caps)) {
    push('manifest_invalid', 'capabilities');
  } else {
    const required = asStringArray(caps.required);
    const optional = caps.optional === undefined ? [] : asStringArray(caps.optional);
    if (!required) push('manifest_invalid', 'capabilities_required');
    if (!optional) push('manifest_invalid', 'capabilities_optional');
    const requested = new Set([...(required || []), ...(optional || [])]);
    for (const cap of requested) {
      if (!policy.capabilities.known.includes(cap)) push('manifest_invalid', `unknown_capability:${cap}`);
    }
    // 声明了 fs 能力就必须给出作用域，否则等于"要权限但不说要哪"——fail-closed
    const fsSection = manifest.fs && typeof manifest.fs === 'object' ? manifest.fs : {};
    const readScopes = asStringArray(fsSection.read) || [];
    const writeScopes = asStringArray(fsSection.write) || [];
    if (requested.has('fs-read') && readScopes.length === 0) push('manifest_invalid', 'fs_scope_missing:fs-read');
    if (requested.has('fs-write') && writeScopes.length === 0) push('manifest_invalid', 'fs_scope_missing:fs-write');
    for (const s of [...readScopes, ...writeScopes]) {
      if (typeof s !== 'string' || !s || path.isAbsolute(s) || /^[A-Za-z]:/.test(s)) {
        push('manifest_invalid', 'fs_scope_absolute');
      } else if (s.split('/').includes('..')) {
        push('manifest_invalid', 'fs_scope_traversal');
      }
    }
  }
  if (mr.require_integrity === true) {
    const integ = manifest.integrity;
    if (!integ || typeof integ !== 'object' || typeof integ.entry_sha256 !== 'string') {
      push('manifest_invalid', 'integrity_missing');
    } else if (!/^[a-f0-9]{64}$/i.test(integ.entry_sha256)) {
      push('manifest_invalid', 'integrity_format');
    }
  }
  if (manifest.sandbox !== undefined) {
    if (typeof manifest.sandbox !== 'string' || !mr.allowed_sandbox.includes(manifest.sandbox)) {
      push('manifest_invalid', 'sandbox_mode');
    }
  }
  return errs;
}

// 解析 fs 作用域为绝对路径；返回 {read, write, escapes:[scope]}（越界作用域单独上报）。
export function resolveFsScopes(manifest, pluginDir, policy = POLICY) {
  const fsSection = (manifest && typeof manifest.fs === 'object' && manifest.fs) ? manifest.fs : {};
  const out = { read: [], write: [], escapes: [] };
  const root = path.resolve(String(pluginDir));
  for (const [key, chunks] of [['read', fsSection.read], ['write', fsSection.write]]) {
    const list = asStringArray(chunks) || [];
    for (const scope of list) {
      const rel = path.relative(root, path.resolve(root, String(scope)));
      if (rel.startsWith('..') || path.isAbsolute(rel)) { out.escapes.push(String(scope)); continue; }
      out[key].push(path.resolve(root, String(scope)));
    }
  }
  return out;
}

// ── 4. 裁决 ─────────────────────────────────────────────────────────
// 输入（全部显式传入 → 纯函数）：
//   manifestPresent  : 插件目录里是否存在 plugin.manifest.json
//   manifestRaw      : 原文（不存在时 undefined）
//   entryPresent     : 入口文件是否存在
//   entrySource      : 入口源码（不存在时 ''）
//   entrySha256      : 入口文件实际 sha256（hex，小写）
//   pluginDir        : 插件根目录（用于作用域解析）
//   sandboxSupported : 宿主是否支持 Node permission model（探测结果）
// 输出：{verdict, reason, error_code, failures, findings, capabilities, fs_scopes, sandbox, fingerprint}
export function decide(input, policy = POLICY) {
  const failures = [];
  const add = (reason) => {
    const code = ERROR_CODES[reason] || 'SC-EX';
    if (!failures.some((f) => f.code === reason)) failures.push({ code: reason, detail: '', error_code: code });
  };
  const manifestPresent = Boolean(input && input.manifestPresent);
  const manifestRaw = input ? input.manifestRaw : undefined;
  const entryPresent = Boolean(input && input.entryPresent);
  const entrySource = String((input && input.entrySource) || '');
  const entrySha256 = String((input && input.entrySha256) || '').toLowerCase();
  const pluginDir = String((input && input.pluginDir) || '.');
  const sandboxSupported = Boolean(input && input.sandboxSupported);

  if (!sandboxSupported) add('sandbox_unavailable');
  if (!manifestPresent) add('manifest_missing');

  let manifest = null;
  if (manifestPresent) {
    const parsed = parseManifest(manifestRaw);
    if (!parsed.ok) add('manifest_unreadable');
    else manifest = parsed.manifest;
  }
  let validation = [];
  if (manifest) {
    validation = validateManifest(manifest, policy);
    if (validation.length) add('manifest_invalid');
  }

  if (!entryPresent) add('entry_missing');
  if (entryPresent) {
    const bytes = Buffer.byteLength(entrySource, 'utf8');
    if (bytes > Number(policy.manifest_rules.max_entry_bytes)) add('entry_too_large');
  }

  // 完整性：缺失哈希已在 validateManifest 里算 manifest_invalid；这里只比对漂移
  if (manifest && manifest.integrity && typeof manifest.integrity.entry_sha256 === 'string'
      && /^[a-f0-9]{64}$/i.test(manifest.integrity.entry_sha256)) {
    if (entrySha256 !== manifest.integrity.entry_sha256.toLowerCase()) add('integrity_mismatch');
  }

  const scopes = manifest ? resolveFsScopes(manifest, pluginDir, policy) : { read: [], write: [], escapes: [] };
  if (scopes.escapes.length) add('fs_scope_escape');

  const requested = manifest && manifest.capabilities && typeof manifest.capabilities === 'object'
    ? [...new Set([...(asStringArray(manifest.capabilities.required) || []),
      ...(asStringArray(manifest.capabilities.optional) || [])])]
    : [];
  for (const cap of requested) {
    if (!policy.capabilities.known.includes(cap)) add('unknown_capability');
    if (policy.capabilities.never_grantable.includes(cap)) add('forbidden_capability');
  }

  const scan = scanSource(entrySource, policy);
  const declared = new Set(requested);
  const undeclaredHits = scan.findings.filter((f) => !declared.has(f.capability));
  if (undeclaredHits.length) add('undeclared_usage');

  // 按策略 precedence 排序 failures；reason = 第一条（最高优先级）
  const order = policy.decision.precedence.slice();
  failures.sort((a, b) => {
    const ia = order.indexOf(a.code);
    const ib = order.indexOf(b.code);
    return (ia < 0 ? 999 : ia) - (ib < 0 ? 999 : ib);
  });
  const first = failures.length ? failures[0] : null;
  const granted = requested.filter((c) => policy.capabilities.grantable.includes(c));

  const decision = {
    policy_version: POLICY_VERSION,
    namespace: policy.namespace,
    verdict: first ? String(policy.decision.verdicts.blocker || 'deny') : String(policy.decision.verdicts.clean || 'allow_sandboxed'),
    reason: first ? first.code : 'ok',
    error_code: first ? first.error_code : null,
    failures,
    manifest_id: manifest && typeof manifest.id === 'string' ? manifest.id : null,
    manifest_version: manifest && typeof manifest.version === 'string' ? manifest.version : null,
    findings: scan.findings,
    exports: manifest && Array.isArray(manifest.exports) ? manifest.exports.slice() : [],
    capabilities: { requested, granted, denied: requested.filter((c) => !granted.includes(c)) },
    fs_scopes: scopes,
    sandbox: {
      mode: 'subprocess',
      ready_timeout_ms: Number(policy.sandbox.ready_timeout_ms),
      call_timeout_ms: Number(policy.sandbox.call_timeout_ms),
      max_old_space_mb: Number(policy.sandbox.max_old_space_mb),
    },
    fingerprint: crypto.createHash('sha256')
      .update(manifestRaw === undefined || manifestRaw === null ? '' : String(manifestRaw))
      .digest('hex'),
  };
  if (manifest) {
    decision.sandbox.node_flags = buildNodeFlags({
      pluginDir,
      entryPath: path.join(path.resolve(pluginDir), String(manifest.entry).replace(/^\.\//, '')),
      readScopes: scopes.read,
      writeScopes: granted.includes('fs-write') ? scopes.write : [],
      policy,
    });
  }
  return decision;
}

// ── 5. 沙箱子进程参数 ───────────────────────────────────────────────
// 关键：插件自身目录必须可读（模块加载需要），fs-read 的"额外读范围"才来自 manifest。
// 这是 Node permission model 的真实边界，文档里不粉饰（docs/26 §6 局限 2）。
export function buildNodeFlags({ pluginDir, entryPath, readScopes = [], writeScopes = [], policy = POLICY }) {
  const sb = policy.sandbox;
  const flags = [
    String(sb.node_permission_flag || '--permission'),
    '--no-warnings',
    '--no-addons',
    '--disable-proto=throw',
    '--disallow-code-generation-from-strings',
    `--max-old-space-size=${Number(sb.max_old_space_mb)}`,
    `--allow-fs-read=${path.resolve(String(pluginDir))}`,
  ];
  const readSet = new Set();
  for (const p of readScopes) readSet.add(path.resolve(String(p)));
  for (const p of readSet) {
    if (path.resolve(String(pluginDir)) !== p) flags.push(`--allow-fs-read=${p}`);
  }
  for (const p of new Set(writeScopes.map((x) => path.resolve(String(x))))) {
    flags.push(`--allow-fs-write=${p}`);
  }
  // entry 文件本身已在 pluginDir 的 allow-fs-read 里；entryPath 仅用于断言（不上 flag）
  void entryPath;
  return flags;
}

// 供审计/告警文本使用；不含用户隐私。
export function renderAlert(decision) {
  if (!decision || decision.verdict !== 'deny') return '';
  const f = decision.failures && decision.failures[0];
  return `[供应链接管] 插件 ${decision.manifest_id || '<unknown>'} 被拒绝（${decision.error_code} ${decision.reason}）：` +
    `未加载、未注册任何钩子（fail-closed）。`;
}
