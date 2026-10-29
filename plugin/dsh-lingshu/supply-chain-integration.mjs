// supply-chain-integration.mjs —— index.js 接入层的端到端验证（真实 apply + 真实沙箱子进程）
//
// 目的：证明"在 index.js 解析插件 Manifest"这条链路真的走通，而不只是单元函数能跑：
//   1) 用最小假 ctx 调 index.js 的 apply()，配置 supplyChain.plugins=[良性, 恶意]；
//   2) 良性插件真的被放进沙箱子进程（audit 里 allow_sandboxed + pid）；
//   3) 恶意插件被静态 deny（audit 里 supply_chain verdict=deny），并且**从未执行**（标记文件不存在）；
//   4) 任一 deny → 加载器挂起（supply_chain_alert）；lingshu_status 的 "供应链:" 行可见。
//
// 用法：node supply-chain-integration.mjs [--json]
// 退出码 0 = 全部断言通过。

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { apply } from './index.js';
import * as sc from './supply-chain.js';
import { closeAllSandboxes, resetSandboxState } from './sandbox-host.mjs';

const jsonMode = process.argv.includes('--json');
const checks = [];
function check(label, cond, detail = '') {
  checks.push({ label, cond: !!cond, detail: String(detail ?? '') });
  if (!jsonMode) console.log(`${cond ? '  ok   ' : '  FAIL '}${label}${detail && !cond ? '  — ' + detail : ''}`);
}

const root = fs.mkdtempSync(path.join(os.tmpdir(), 'lingshu-sc-int-'));
const dataDir = path.join(root, 'data');
fs.mkdirSync(dataDir, { recursive: true });
const auditFile = path.join(dataDir, 'audit.jsonl');

function sha256(text) { return crypto.createHash('sha256').update(text).digest('hex'); }
function mkPlugin(name, { source, manifest = {} }) {
  const dir = path.join(root, name);
  fs.mkdirSync(dir, { recursive: true });
  fs.writeFileSync(path.join(dir, 'index.mjs'), source, 'utf8');
  const merged = {
    manifest_version: sc.MANIFEST_VERSION,
    id: name.replace(/_/g, '-'),
    version: '0.1.0',
    entry: 'index.mjs',
    exports: ['ping'],
    capabilities: { required: ['llm'] },
    integrity: { entry_sha256: sha256(source) },
    ...manifest,
  };
  fs.writeFileSync(path.join(dir, 'plugin.manifest.json'), JSON.stringify(merged, null, 2), 'utf8');
  return dir;
}

const benignDir = mkPlugin('benign_echo', {
  source: 'export function ping(p){ return { echo: p.name }; }',
  manifest: { exports: ['ping'] },
});
const evilMarker = path.join(root, 'evil_should_not_run.txt');
const evilDir = mkPlugin('evil_writer', {
  source: [
    'import fs from "node:fs";',
    'fs.writeFileSync(' + JSON.stringify(evilMarker) + ', "EXECUTED");',
    'export function ping(){ return "evil"; }',
  ].join('\n'),
  manifest: { exports: ['ping'], capabilities: { required: ['llm'] } },
});

// 假 ctx：index.js 只使用 systemPrompt / on / tools / effect 四个 API
const registered = { sections: [], hooks: {}, tools: {}, effects: 0 };
const ctx = {
  systemPrompt: { section(spec) { registered.sections.push(spec); return () => {}; } },
  on(event, fn) { (registered.hooks[event] = registered.hooks[event] || []).push(fn); return () => {}; },
  tools: { register(spec) { registered.tools[spec.name] = spec; return () => {}; } },
  effect(fn) { registered.effects += 1; return () => {}; },
};

process.env.LINGSHU_DATA_DIR = dataDir;
process.env.DSH_HOME = path.join(root, 'home');
resetSandboxState();
apply(ctx, {
  mode: 'active',
  profileTag: 'sc-it',
  supplyChain: { enabled: true, plugins: [benignDir, evilDir] },
});

function readAudit() {
  try {
    return fs.readFileSync(auditFile, 'utf8').split('\n').filter(Boolean).map((l) => JSON.parse(l));
  } catch { return []; }
}
function waitFor(fn, timeoutMs = 20000, stepMs = 100) {
  return new Promise((resolve) => {
    const t0 = Date.now();
    const tick = () => {
      let v = false;
      try { v = fn(); } catch { v = false; }
      if (v) { resolve(true); return; }
      if (Date.now() - t0 > timeoutMs) { resolve(false); return; }
      setTimeout(tick, stepMs);
    };
    tick();
  });
}

const ready = await waitFor(() => readAudit().some((e) => e.kind === 'supply_chain_alert' || e.kind === 'supply_chain'));
await new Promise((r) => setTimeout(r, 300));
const audit = readAudit();
const kinds = audit.map((e) => e.kind);
const manifests = audit.filter((e) => e.kind === 'supply_chain_manifest');
const loads = audit.filter((e) => e.kind === 'supply_chain');
const alerts = audit.filter((e) => e.kind === 'supply_chain_alert');

check('apply() 后出现 supply_chain 审计事件', ready && kinds.includes('supply_chain'), JSON.stringify(kinds.slice(-8)));
check('两个插件的 Manifest 都被解析（2 条 supply_chain_manifest）', manifests.length === 2, String(manifests.length));
check('良性插件 allow_sandboxed 且有沙箱 pid', loads.some((e) => e.verdict === 'allow_sandboxed' && Number(e.pid) > 0),
  JSON.stringify(loads.map((e) => ({ v: e.verdict, pid: e.pid }))));
const benignPid = (loads.find((e) => e.verdict === 'allow_sandboxed') || {}).pid;
check('良性插件真的在独立子进程里（pid ≠ 宿主）', Number(benignPid) > 0 && Number(benignPid) !== process.pid, String(benignPid));
check('恶意插件被 deny（supply_chain verdict=deny）', loads.some((e) => e.verdict === 'deny'), JSON.stringify(loads.map((e) => e.verdict)));
check('恶意插件从未执行（标记文件不存在）', fs.existsSync(evilMarker) === false);
check('deny 触发挂起告警（supply_chain_alert）', alerts.length === 1, String(alerts.length));
check('apply 审计带 supplyChain 快照', audit.some((e) => e.kind === 'apply' && e.supplyChain && e.supplyChain.enabled === true));

// lingshu_status：供应链行可见，且工具注册成功
let statusText = '';
try {
  statusText = await registered.tools.lingshu_status.execute({});
} catch (err) {
  statusText = 'THROW:' + String(err).slice(0, 200);
}
const statusLine = String(statusText).split('\n').find((l) => l.startsWith('供应链:')) || '';
check('lingshu_status 含"供应链:"行', statusLine.length > 0, statusLine);
check('状态行显示 deny / 挂起', /deny/.test(statusLine) && /挂起=是/.test(statusLine), statusLine);

// 清洗：关掉沙箱子进程
try { await closeAllSandboxes('integration_test_done'); } catch { /* noop */ }

const pass = checks.filter((c) => c.cond).length;
const fail = checks.length - pass;
if (jsonMode) {
  process.stdout.write(JSON.stringify({ checks, pass, fail, total: checks.length,
    audit_kinds: kinds, status_line: statusLine,
    evidence: { benign_pid: benignPid, evil_marker_exists: fs.existsSync(evilMarker) } }));
} else {
  console.log(`\n${pass} ok / ${fail} fail（共 ${checks.length} 条）`);
  for (const c of checks.filter((x) => !x.cond)) console.log(`  FAIL ${c.label}${c.detail ? ' — ' + c.detail : ''}`);
}
try { fs.rmSync(root, { recursive: true, force: true }); } catch { /* 沙箱可能禁止删除 */ }
process.exitCode = fail === 0 ? 0 : 1;
