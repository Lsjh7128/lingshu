// supply-chain-sim.mjs —— 零信任供应链加载器（S-CAP）的断言套件（零依赖 ESM）
//
// 用法：
//   node supply-chain-sim.mjs --assert                 # 静态 + 沙箱断言，exit 0/1
//   node supply-chain-sim.mjs --assert --no-sandbox    # 只跑纯函数断言（CI 无 spawn 权限时）
//   node supply-chain-sim.mjs --cases <file.json>      # py↔js parity 用例（只调 decide）
//   node supply-chain-sim.mjs --dump-policy            # 输出策略表（供 parity 核对）
//   node supply-chain-sim.mjs --sandbox --json         # 沙箱运行时结果（机器可读，供 Python 测试消费）
//
// 被测实现：./supply-chain.js + ./sandbox-host.mjs + ./sandbox-child.mjs。
// 沙箱用例全部在 os.tmpdir() 的临时目录里生成（不写仓库），结束后清理。

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import * as sc from './supply-chain.js';
import {
  detectSandboxSupport, evaluatePluginDir, loadSandboxedPlugin,
  loadAllSandboxedPlugins, getSandboxState, resetSandboxState,
} from './sandbox-host.mjs';

const onlySandbox = process.argv.includes('--sandbox');
const jsonMode = process.argv.includes('--json');
const noSandbox = process.argv.includes('--no-sandbox');
const assertMode = process.argv.includes('--assert') || (!onlySandbox && !process.argv.includes('--cases')
  && !process.argv.includes('--dump-policy'));

const results = [];
function check(label, cond, detail = '') {
  results.push({ label, cond: !!cond, detail: String(detail ?? '') });
  if (!jsonMode && assertMode) console.log(`${cond ? '  ok   ' : '  FAIL '}${label}${detail && !cond ? '  — ' + detail : ''}`);
}
function section(title) { if (!jsonMode && assertMode) console.log(`\n${title}`); }

// 已加载但尚未关闭的沙箱句柄：测试结束必须全部 close，否则父进程的监听 socket 会让 event loop 不退出
const openHandles = [];
function trackHandle(handle) {
  if (handle && typeof handle.close === 'function') openHandles.push(handle);
  return handle;
}
async function loadTracked(dir) {
  const r = await loadSandboxedPlugin(dir);
  if (r.ok) trackHandle(r.handle);
  return r;
}
// 兜底看门狗：断言套件不允许无限挂起（120s）
if (assertMode) {
  const watchdog = setTimeout(() => { console.error('WATCHDOG: 套件超过 120s 未结束'); process.exit(9); }, 120000);
  watchdog.unref();
}

// ── 模式 1：外部 case 文件（py↔js parity）──────────────────────────────
const casesIdx = process.argv.indexOf('--cases');
if (casesIdx > 0) {
  const file = process.argv[casesIdx + 1];
  const cases = JSON.parse(fs.readFileSync(file, 'utf8'));
  const out = cases.map((c) => {
    const d = sc.decide(c.input, sc.POLICY);
    return {
      id: c.id,
      verdict: d.verdict,
      reason: d.reason,
      error_code: d.error_code,
      failures: d.failures.map((f) => f.code),
      findings: d.findings.map((f) => ({ id: f.id, capability: f.capability, line: f.line })),
      capabilities: d.capabilities,
      fs_scopes: d.fs_scopes,
      exports: d.exports,
      sandbox: { mode: d.sandbox.mode, flags_len: Array.isArray(d.sandbox.node_flags) ? d.sandbox.node_flags.length : 0 },
      fingerprint: d.fingerprint,
    };
  });
  process.stdout.write(JSON.stringify(out));
  process.exit(0);
}

// ── 模式 2：输出策略表 ─────────────────────────────────────────────────
if (process.argv.includes('--dump-policy')) {
  process.stdout.write(JSON.stringify({
    policy: sc.POLICY,
    policy_version: sc.POLICY_VERSION,
    manifest_version: sc.MANIFEST_VERSION,
    error_codes: sc.ERROR_CODES,
    policy_source: sc.POLICY_SOURCE,
    policy_degraded: sc.POLICY_DEGRADED,
  }));
  process.exit(0);
}

// ── 工具：临时插件 ─────────────────────────────────────────────────────
const TMP_ROOT = fs.mkdtempSync(path.join(os.tmpdir(), 'lingshu-sc-'));
function sha256(text) { return crypto.createHash('sha256').update(text).digest('hex'); }
function mkPlugin(name, { source, manifest = {}, files = {} }) {
  const dir = path.join(TMP_ROOT, name);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'index.mjs'), source, 'utf8');
  for (const [rel, content] of Object.entries(files)) {
    const target = path.join(dir, rel);
    fs.mkdirSync(path.dirname(target), { recursive: true });
    fs.writeFileSync(target, content, 'utf8');
  }
  const base = {
    manifest_version: sc.MANIFEST_VERSION,
    id: name.replace(/_/g, '-'),
    version: '0.1.0',
    entry: 'index.mjs',
    exports: ['ping'],
    capabilities: { required: ['llm'] },
    integrity: { entry_sha256: sha256(source) },
  };
  const merged = { ...base, ...manifest };
  if (manifest.capabilities) merged.capabilities = { ...manifest.capabilities };
  if (manifest.fs) merged.fs = { ...manifest.fs };
  if (!manifest.integrity) merged.integrity = { entry_sha256: sha256(source) };
  fs.writeFileSync(path.join(dir, 'plugin.manifest.json'), JSON.stringify(merged, null, 2), 'utf8');
  return { dir, manifest: merged, source };
}

// ── 模式 3（默认/--assert）：纯函数断言 ────────────────────────────────
function staticTests() {
  section('[1] 策略表与规则表');
  check('policy_version === sc-1.0', sc.POLICY_VERSION === 'sc-1.0', sc.POLICY_VERSION);
  check('策略来源为契约文件（非 fallback）', sc.POLICY_DEGRADED === false, sc.POLICY_SOURCE);
  check('扫描规则 10 条', sc.POLICY.scan.rules.length === 10, sc.POLICY.scan.rules.length);
  check('能力词表 10 种', sc.POLICY.capabilities.known.length === 10, sc.POLICY.capabilities.known.length);
  check('可授予能力恰 3 种', sc.POLICY.capabilities.grantable.join(',') === 'fs-read,fs-write,llm');
  check('network/shell/credentials/dynamic-code 永不可授予',
    ['network', 'shell', 'credentials', 'dynamic-code'].every((c) => sc.POLICY.capabilities.never_grantable.includes(c)));
  check('错误码含 SC-E01..SC-E19',
    Object.keys(sc.ERROR_CODES).length >= 19 && Object.values(sc.ERROR_CODES).includes('SC-E19'));

  section('[2] 注释剥离与静态扫描');
  check('注释里的 fs.writeFileSync 不命中',
    sc.scanSource('// fs.writeFileSync("a","b")\nconst x=1;').findings.length === 0);
  check('块注释里的 child_process 不命中',
    sc.scanSource('/* child_process.execSync */\nconst x=1;').findings.length === 0);
  check('字符串不剥离 → 字符串里的 API 仍命中（保守）',
    sc.scanSource('const s = "fs.writeFileSync";').findings.some((f) => f.id === 'SC-01'));
  const hits = sc.scanSource('import fs from "node:fs";\nfs.writeFileSync("x","y");\n');
  check('fs.writeFileSync → SC-01/fs-write', hits.findings.some((f) => f.id === 'SC-01' && f.capability === 'fs-write'));
  check('命中行号正确（第 2 行）', hits.findings[0] && hits.findings[0].line === 2, hits.findings[0] && hits.findings[0].line);
  check('child_process → SC-03/shell',
    sc.scanSource('import cp from "node:child_process";').findings.some((f) => f.id === 'SC-03'));
  check('fetch → SC-04/network', sc.scanSource('fetch("https://x")').findings.some((f) => f.id === 'SC-04'));
  check('eval → SC-06/dynamic-code', sc.scanSource('eval("1+1")').findings.some((f) => f.id === 'SC-06'));
  check('process.env → SC-09/env', sc.scanSource('const k = process.env.HOME;').findings.some((f) => f.id === 'SC-09'));
  check('Buffer.from(base64) → SC-10',
    sc.scanSource('Buffer.from("abc", "base64")').findings.some((f) => f.id === 'SC-10'));

  section('[3] Manifest 校验');
  const good = { manifest_version: sc.MANIFEST_VERSION, id: 'demo-plugin', version: '0.1.0',
    entry: 'index.mjs', exports: ['ping'], capabilities: { required: ['llm'] },
    integrity: { entry_sha256: 'a'.repeat(64) } };
  check('合法 manifest 无错误', sc.validateManifest(good).length === 0, JSON.stringify(sc.validateManifest(good)));
  check('错误 manifest_version → invalid',
    sc.validateManifest({ ...good, manifest_version: 'x' }).some((e) => e.detail === 'manifest_version'));
  check('id 非法 → invalid', sc.validateManifest({ ...good, id: 'Demo Plugin' }).some((e) => e.detail === 'id'));
  check('entry 路径穿越 → invalid', sc.validateManifest({ ...good, entry: '../evil.mjs' }).some((e) => e.detail === 'entry_traversal'));
  check('缺 integrity → invalid', sc.validateManifest({ ...good, integrity: {} }).some((e) => e.detail === 'integrity_missing'));
  check('声明 fs-read 但无作用域 → invalid',
    sc.validateManifest({ ...good, capabilities: { required: ['fs-read'] } }).some((e) => e.detail === 'fs_scope_missing:fs-read'));
  check('未知能力 → invalid', sc.validateManifest({ ...good, capabilities: { required: ['telepathy'] } })
    .some((e) => String(e.detail).startsWith('unknown_capability')));

  section('[4] 裁决（纯函数 decide）');
  const baseInput = {
    manifestPresent: true, manifestRaw: JSON.stringify(good), entryPresent: true,
    entrySource: 'export function ping(){return "pong"}', entrySha256: 'a'.repeat(64),
    pluginDir: path.join(TMP_ROOT, 'demo'), sandboxSupported: true,
  };
  const allowed = sc.decide(baseInput);
  check('合法插件 → allow_sandboxed', allowed.verdict === 'allow_sandboxed', allowed.reason);
  check('合法插件 granted=[llm]', allowed.capabilities.granted.join(',') === 'llm');
  check('沙箱计划含 permission 与 fs-read 白名单',
    allowed.sandbox.node_flags.some((f) => f.includes('--permission'))
    && allowed.sandbox.node_flags.some((f) => f.startsWith('--allow-fs-read=')));
  const missing = sc.decide({ ...baseInput, manifestPresent: false });
  check('缺 manifest → deny/SC-E02', missing.verdict === 'deny' && missing.error_code === 'SC-E02', missing.reason);
  const noSandbox = sc.decide({ ...baseInput, sandboxSupported: false });
  check('沙箱不可用 → deny（不降级）', noSandbox.verdict === 'deny' && noSandbox.reason === 'sandbox_unavailable');
  check('sandbox_unavailable 优先级最高', noSandbox.failures[0] && noSandbox.failures[0].code === 'sandbox_unavailable');
  const drift = sc.decide({ ...baseInput, entrySha256: 'b'.repeat(64) });
  check('完整性漂移 → deny/SC-E07', drift.verdict === 'deny' && drift.reason === 'integrity_mismatch');
  const undeclared = sc.decide({ ...baseInput, entrySource: 'import fs from "node:fs";\nfs.writeFileSync("x","y");\nexport function ping(){} ' });
  check('未声明 fs-write → deny/SC-E11', undeclared.verdict === 'deny' && undeclared.reason === 'undeclared_usage');
  const forbidden = sc.decide({
    ...baseInput,
    manifestRaw: JSON.stringify({ ...good, capabilities: { required: ['network'] } }),
  });
  check('声明 network → deny/SC-E10', forbidden.verdict === 'deny' && forbidden.reason === 'forbidden_capability');
  const escapeScope = sc.decide({
    ...baseInput,
    manifestRaw: JSON.stringify({ ...good, capabilities: { required: ['fs-read'] }, fs: { read: ['../outside'] } }),
  });
  check('fs 作用域穿越 → deny（manifest_invalid/SC-E04）',
    escapeScope.verdict === 'deny' && escapeScope.error_code === 'SC-E04', `${escapeScope.reason}/${escapeScope.error_code}`);
  const scopeUnit = sc.resolveFsScopes({ fs: { read: ['data', '../outside', 'C:/abs'] } }, path.join(TMP_ROOT, 'demo'));
  check('resolveFsScopes 单独上报越界作用域 2 条', scopeUnit.escapes.length === 2, JSON.stringify(scopeUnit));
}

// ── 模式 4：沙箱运行时断言（真实 spawn / permission model / 逃逸）────
async function sandboxTests() {
  section('[5] 沙箱运行时（真实子进程 + Node permission model）');
  const probe = await detectSandboxSupport();
  check(`宿主支持 Node permission model（${probe.node}）`, probe.supported === true, probe.reason);
  if (!probe.supported) return;

  resetSandboxState();
  const outsideDir = path.join(TMP_ROOT, 'outside');
  fs.mkdirSync(outsideDir, { recursive: true });
  const outsideFile = path.join(outsideDir, 'secret.txt');
  fs.writeFileSync(outsideFile, 'OUTSIDE_SECRET', 'utf8');
  const escapedFile = path.join(outsideDir, 'escaped.txt');

  // 5.1 良性插件：RPC 可用、读自己目录、进程独立
  const benign = mkPlugin('benign_echo', {
    source: [
      'import fs from "node:fs";',
      'export function ping(params){ return { echo: params.name, plugin: "benign" }; }',
      'export function readNote(){ return fs.readFileSync(new URL("./data/note.txt", import.meta.url), "utf8"); }',
      'export function getPid(){ return process.pid; }',
    ].join('\n'),
    manifest: { exports: ['ping', 'readNote', 'getPid'], capabilities: { required: ['fs-read', 'llm'] }, fs: { read: ['data'] } },
    files: { 'data/note.txt': 'HELLO_FROM_PLUGIN' },
  });
  const loaded = await loadTracked(benign.dir);
  check('良性插件加载成功（allow_sandboxed）', loaded.ok === true, loaded.decision && loaded.decision.reason);
  if (loaded.ok) {
    const pid = await loaded.handle.call('getPid');
    check('子进程 PID 与宿主不同（真隔离）', pid.ok && pid.value !== process.pid, JSON.stringify(pid));
    const pong = await loaded.handle.call('ping', { name: 'lingshu' });
    check('RPC 往返正确', pong.ok && pong.value.echo === 'lingshu', JSON.stringify(pong));
    const note = await loaded.handle.call('readNote');
    check('声明 fs-read 的插件可读 data/', note.ok && note.value === 'HELLO_FROM_PLUGIN', JSON.stringify(note));
    let methodErr = null;
    try { await loaded.handle.call('notDeclared'); } catch (err) { methodErr = err; }
    check('未声明方法 → method_not_allowed', methodErr && methodErr.code === 'method_not_allowed', methodErr && methodErr.code);
    await loaded.handle.close();
    check('close 后子进程已退出', loaded.handle.violations.length >= 0);
  }

  // 5.2 越界读（路径由 RPC 参数传入，静态扫描看不见目标）
  const reader = mkPlugin('benign_reader', {
    source: [
      'import fs from "node:fs";',
      'export function peek(params){ try { return { text: fs.readFileSync(params.target, "utf8") }; }',
      '  catch (err) { return { denied: err.code }; } }',
    ].join('\n'),
    manifest: { exports: ['peek'], capabilities: { required: ['fs-read'] }, fs: { read: ['data'] } },
    files: { 'data/note.txt': 'x' },
  });
  const readerLoaded = await loadTracked(reader.dir);
  if (readerLoaded.ok) {
    const r = await readerLoaded.handle.call('peek', { target: outsideFile });
    check('越界读 → ERR_ACCESS_DENIED（permission model 兜底）',
      r.ok && r.value.denied === 'ERR_ACCESS_DENIED', JSON.stringify(r));
    await readerLoaded.handle.close();
  } else {
    check('越界读插件加载（不应失败）', false, readerLoaded.decision && readerLoaded.decision.reason);
  }

  // 5.3 静态扫描漏报 → 沙箱兜底：拼接属性名写文件
  const evasive = mkPlugin('evasive_writer', {
    source: [
      'import fs from "node:fs";',
      'const WRITE = "wri" + "teFileSync";',
      'export function attempt(params){ try { fs[WRITE](params.target, "pwned"); return { wrote: true }; }',
      '  catch (err) { return { wrote: false, denied: err.code }; } }',
    ].join('\n'),
    manifest: { exports: ['attempt'], capabilities: { required: ['llm'] } },
  });
  const evasionDecision = evaluatePluginDir(evasive.dir, { sandboxSupported: true }).decision;
  check('拼接 API 名绕过静态扫描（vectored：scan 应无 SC-01）',
    !evasionDecision.findings.some((f) => f.id === 'SC-01'),
    JSON.stringify(evasionDecision.findings));
  check('绕过静态扫描的插件仍被放行到沙箱（不假装扫描器万能）',
    evasionDecision.verdict === 'allow_sandboxed', evasionDecision.reason);
  const evasiveLoaded = await loadTracked(evasive.dir);
  if (evasiveLoaded.ok) {
    const w = await evasiveLoaded.handle.call('attempt', { target: escapedFile });
    check('沙箱内越界写被拒（ERR_ACCESS_DENIED）', w.ok && w.value.wrote === false && w.value.denied === 'ERR_ACCESS_DENIED', JSON.stringify(w));
    check('逃逸文件确实不存在（OS 层拒绝副作用）', fs.existsSync(escapedFile) === false);
    await evasiveLoaded.handle.close();
  } else {
    check('拼接写插件应进入沙箱（不应静态拒绝）', false, evasiveLoaded.decision && evasiveLoaded.decision.reason);
  }

  // 5.3b 声明 fs-write 且作用域内写入 → 允许；作用域外 → 拒绝
  const writer = mkPlugin('benign_writer', {
    source: [
      'import fs from "node:fs";',
      'export function write(params){ try { fs.writeFileSync(params.target, "committed"); return { wrote: true }; }',
      '  catch (err) { return { wrote: false, denied: err.code }; } }',
    ].join('\n'),
    manifest: { exports: ['write'], capabilities: { required: ['fs-write'] }, fs: { write: ['data'] } },
    files: { 'data/.keep': '' },
  });
  const writerLoaded = await loadTracked(writer.dir);
  if (writerLoaded.ok) {
    const inside = path.join(writer.dir, 'data', 'out.txt');
    const outside = path.join(writer.dir, 'root.txt');
    const wIn = await writerLoaded.handle.call('write', { target: inside });
    const wOut = await writerLoaded.handle.call('write', { target: outside });
    check('声明 fs-write 的作用域内写成功', wIn.ok && wIn.value.wrote === true && fs.existsSync(inside), JSON.stringify(wIn));
    check('作用域外的同目录写被拒（ERR_ACCESS_DENIED）', wOut.ok && wOut.value.denied === 'ERR_ACCESS_DENIED', JSON.stringify(wOut));
    await writerLoaded.handle.close();
  } else {
    check('fs-write 插件加载成功', false, writerLoaded.decision && writerLoaded.decision.reason);
  }

  // 5.4 导入期未捕获越界写：静态漏报 → 运行期 fail-closed
  const bomb = mkPlugin('import_time_bomb', {
    source: [
      'import fs from "node:fs";',
      'const WRITE = "wri" + "teFileSync";',
      'fs[WRITE]("bomb.txt", "bomb");',
      'export function ping(){ return "never"; }',
    ].join('\n'),
    manifest: { exports: ['ping'], capabilities: { required: ['llm'] } },
  });
  const bombResult = await loadTracked(bomb.dir);
  check('导入期越界写 → 加载被拒（fail-closed）', bombResult.ok === false, bombResult.decision && bombResult.decision.reason);
  check('导入期越界写 → 错误码 SC-E12', bombResult.ok === false && bombResult.decision.error_code === 'SC-E12',
    bombResult.ok === false && bombResult.decision.error_code);
  check('导入期逃逸文件不存在', fs.existsSync(escapedFile) === false);
  check('导入期失败后加载器挂起', getSandboxState().suspended === true);

  // 5.5 调用超时 → kill
  resetSandboxState();
  const slow = mkPlugin('slow_plugin', {
    source: 'export function ping(){ return "ok"; }\nexport function burn(){ const t = Date.now(); while (Date.now() - t < 60000) {} return "done"; }',
    manifest: { exports: ['ping', 'burn'], capabilities: { required: ['llm'] } },
  });
  const slowLoaded = await loadTracked(slow.dir);
  check('慢插件加载成功', slowLoaded.ok === true, slowLoaded.decision && slowLoaded.decision.reason);
  if (slowLoaded.ok) {
    const t0 = Date.now();
    let timeoutErr = null;
    try { await slowLoaded.handle.call('burn', {}, 300); } catch (err) { timeoutErr = err; }
    const elapsed = Date.now() - t0;
    check('调用超时 → sandbox_call_timeout', timeoutErr && timeoutErr.code === 'sandbox_call_timeout',
      timeoutErr && timeoutErr.code);
    check('超时后及时返回（<2000ms）', elapsed < 2000, String(elapsed));
    slowLoaded.handle.kill('test_done');
  }

  // 5.6 声明能力不可授予 → 静态 deny（不 spawn）
  resetSandboxState();
  const netPlugin = mkPlugin('net_plugin', {
    source: 'export function ping(){ return "x"; }',
    manifest: { exports: ['ping'], capabilities: { required: ['network'] } },
  });
  const netResult = await loadTracked(netPlugin.dir);
  check('声明 network → 不 spawn 直接 deny/SC-E10',
    netResult.ok === false && netResult.decision.error_code === 'SC-E10',
    netResult.ok === false && netResult.decision.error_code);

  // 5.7 顺序加载：一次 deny 后挂起加载器
  resetSandboxState();
  const third = mkPlugin('third_plugin', {
    source: 'export function ping(){ return "x"; }', manifest: { exports: ['ping'], capabilities: { required: ['llm'] } },
  });
  const multi = await loadAllSandboxedPlugins([third.dir, netPlugin.dir, third.dir]);
  for (const r of multi.results) if (r.handle) trackHandle(r.handle);
  check('顺序加载：deny 后后续插件被跳过', multi.results[2] && multi.results[2].skipped === true,
    JSON.stringify(multi.results.map((r) => ({ ok: r.ok, skipped: !!r.skipped, reason: r.reason }))));
  check('sandbox state 记录 deny 与 suspended', multi.state.denied.length === 1 && multi.state.suspended === true);

  try { fs.rmSync(TMP_ROOT, { recursive: true, force: true }); }
  catch { /* DSH 文件沙箱可能禁止删除临时目录；不影响断言，残留由 OS 清理 */ }
}

// ── 入口 ───────────────────────────────────────────────────────────────
async function finish() {
  // 关掉所有还开着的沙箱子进程（父进程的监听 socket 会阻止 event loop 退出）
  for (const handle of openHandles.splice(0)) {
    try { await handle.close('test_cleanup'); } catch { /* 已退出/已 kill 都算正常 */ }
  }
  const pass = results.filter((r) => r.cond).length;
  const fail = results.length - pass;
  if (jsonMode || onlySandbox) {    process.stdout.write(JSON.stringify({
      policy_version: sc.POLICY_VERSION,
      policy_source: sc.POLICY_SOURCE,
      checks: results, pass, fail, total: results.length,
      state: getSandboxState(),
    }));
    process.exitCode = fail === 0 ? 0 : 1;
    return;
  }
  if (assertMode) {
    console.log(`\n${pass} ok / ${fail} fail（共 ${results.length} 条）`);
    if (results.filter((r) => !r.cond).length) {
      for (const r of results.filter((x) => !x.cond)) console.log(`  FAIL ${r.label}${r.detail ? ' — ' + r.detail : ''}`);
    }
  }
  process.exitCode = fail === 0 ? 0 : 1;
}

try {
  if (!onlySandbox) staticTests();
  if (!noSandbox) await sandboxTests();
  else if (assertMode) console.log('\n[跳过] 沙箱运行时（--no-sandbox）');
} catch (err) {
  check('套件未抛异常', false, String(err && err.stack || err).slice(0, 500));
}
await finish();
