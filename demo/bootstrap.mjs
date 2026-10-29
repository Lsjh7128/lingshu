#!/usr/bin/env node
// demo/bootstrap.mjs — 沙盒实验舱的一键启动器（demo.sh / demo.bat 都只是"找到 node 后调它"）。
//
// 它做五件事：
//   1) 环境检查：Node >= 20.10、DSH CLI、$DSH_HOME、pnpm（安装插件时需要）；
//   2) 隔离自检：确认 LINGSHU_DATA_DIR / 会话 / 存储都落在仓库内沙盒，绝不写真实插件目录；
//   3) 准备可抛弃 profile `lingshu-demo-profile`（从 headless 模板建，按需装 LINGSHU 插件 link:）；
//   4) 初始化沙盒数据：用 memorywriter 写入「阿福」的 6 条合成记忆并预置 turns=3；
//   5) 进入交互式对话（demo/chat.mjs）。
//
// 用法：
//   bash demo.sh              # macOS/Linux
//   demo.bat                  # Windows
//   bash demo.sh --clean      # 删除沙盒 profile + ./.lingshu-demo-data 后退出
//   bash demo.sh --fresh      # 清空沙盒数据并重新预置（保留 profile/插件）
//   bash demo.sh --window 3000 | --no-window-override
//
// 退出码：0 正常（含正常退出对话）；2 环境/用法错误；1 启动失败。

import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';

import {
  DATA_DIR, DATA_DIR_REL, DEFAULT_DEMO_WINDOW, DEMO_PATCH, PLUGIN_DIR, PROFILE_NAME, REPO_ROOT,
  assertSandboxDataDir, dshHome, ensureDir, ensurePluginSelfLink, nodeVersionOk,
  prettyPath, profileDir, realPluginDataDir, removeDir, resolveDshCli, resolveExecutable,
} from './lib.mjs';
import { seedDemoData } from './seed.mjs';
import { startDemoChat } from './chat.mjs';

const STEP = (n, total, title) => '\n[' + n + '/' + total + '] ' + title;
const OK = '  [ok]   ';
const WARN = '  [warn] ';
const FAIL = '  [fail] ';

const HELP = [
  '',
  'LINGSHU 沙盒实验舱 · Sandbox Demo',
  '',
  '用法：',
  '  bash demo.sh                # macOS / Linux：一键启动交互式对话',
  '  demo.bat                    # Windows：同上',
  '  bash demo.sh --clean        # 删除沙盒 profile 与 ./.lingshu-demo-data（不碰 desktop profile）',
  '  bash demo.sh --fresh        # 清空沙盒数据并重新预置（保留 profile 与插件）',
  '  bash demo.sh --window 3000  # 覆盖"相变窗口"（tokens）；默认 3000，仅演示口径',
  '  bash demo.sh --no-window-override   # 不覆盖窗口（用宿主真实 request/context）',
  '  bash demo.sh --dry-run      # 只做环境/隔离自检并打印将执行的命令，不写任何东西、不启动对话',
  '  bash demo.sh -h | --help    # 本帮助（demo.bat 同参数）',
  '',
  '隔离口径：',
  '  · LINGSHU_DATA_DIR 被强制设为 ' + DATA_DIR_REL + '（仓库内，可删除）；',
  '  · DSH 会话/存储经 --patch 重定向到 ' + DATA_DIR_REL + '/dsh-sessions、dsh-storages；',
  '  · 只写一个可抛弃 profile：$DSH_HOME/profiles/' + PROFILE_NAME + '；',
  '  · 绝不写 $DSH_HOME/plugins/dsh-lingshu，绝不碰 desktop profile；',
  '  · 子进程设置 DSH_TELEMETRY_MODE=DISABLED、DSH_PERMISSION_MODE=read-only。',
  '',
  '前置条件：本机已装 DSH 并登录（或 export DEEPSEEK_API_KEY）；安装插件需要 pnpm 在 PATH。',
  '找不到 DSH CLI 时可用 DSH_BIN=/abs/path/to/@deepseek-ai/dsh/lib/bin.js 指定。',
  '',
].join('\n');

function parseArgs(argv) {
  const out = {
    clean: false, fresh: false, windowOverride: DEFAULT_DEMO_WINDOW,
    noWindowOverride: false, help: false, dryRun: false,
  };
  for (let i = 0; i < argv.length; i += 1) {
    const a = argv[i];
    if (a === '--clean') out.clean = true;
    else if (a === '--fresh') out.fresh = true;
    else if (a === '--window') {
      const v = Number(argv[++i]);
      if (!Number.isFinite(v) || v <= 0) throw new Error('--window 需要一个正整数 tokens，例如 --window 3000');
      out.windowOverride = v;
      out.noWindowOverride = false;
    } else if (a === '--no-window-override') {
      out.noWindowOverride = true;
      out.windowOverride = null;
    } else if (a === '-h' || a === '--help') out.help = true;
    else if (a === '--dry-run') out.dryRun = true;
    else throw new Error('未知参数：' + a + '（用 --help 查看用法）');
  }
  return out;
}

function runCli(cli, args, { timeout = 240000, env = process.env } = {}) {
  const r = spawnSync(process.execPath, [cli.binJs, ...args], {
    cwd: REPO_ROOT, env, encoding: 'utf8', timeout,
    windowsHide: true,
  });
  return {
    status: r.status,
    stdout: String(r.stdout || ''),
    stderr: String(r.stderr || ''),
    error: r.error ? String(r.error.message || r.error) : '',
    timedOut: Boolean(r.error && r.error.code === 'ETIMEDOUT'),
  };
}

function tail(text, n = 12) {
  const lines = String(text || '').split('\n').map((l) => l.trim()).filter(Boolean);
  return lines.slice(-n).join('\n  ');
}

function reportCliFailure(label, r) {
  const code = (r.status === null || r.status === undefined) ? '' : '（exit=' + r.status + '）';
  console.error(FAIL + label + '失败' + code);
  if (r.error && /EPERM|EACCES|operation not permitted/i.test(r.error)) {
    console.error('  无法创建 DSH CLI 子进程：当前环境限制进程创建（EPERM/EACCES）。');
    console.error('  请在普通终端里运行 demo.sh / demo.bat，而不是在受限沙箱/容器内。');
    return;
  }
  if (r.timedOut) console.error('  命令超时（' + Math.round((r.timeout || 0) / 1000) + ' 秒）');
  if (r.error) console.error('  ' + r.error);
  const detail = tail(r.stderr || r.stdout || '', 12);
  if (detail) console.error('  ' + detail);
}

/** profile 里的插件状态：ok / missing（没装或不完整）/ stale（link 指向别的仓库副本或链接已断）。 */
function profilePluginState(profilePath) {
  const pkgPath = path.join(profilePath, 'package.json');
  let pkg;
  try {
    pkg = JSON.parse(fs.readFileSync(pkgPath, 'utf8').replace(/^\uFEFF/, ''));
  } catch {
    return 'missing';
  }
  const deps = pkg.dependencies || {};
  const dep = String(deps['dsh-lingshu'] || '');
  const bundles = (pkg.dsh && pkg.dsh.profile && pkg.dsh.profile.bundles) || [];
  if (!dep || !Array.isArray(bundles) || !bundles.includes('dsh-lingshu')) return 'missing';
  const linkPath = path.join(profilePath, 'node_modules', 'dsh-lingshu');
  let target;
  let want;
  try {
    target = fs.realpathSync(linkPath);
    want = fs.realpathSync(PLUGIN_DIR);
  } catch {
    return 'stale';
  }
  const norm = (p) => (process.platform === 'win32' ? String(p).toLowerCase() : String(p));
  return norm(path.resolve(target)) === norm(path.resolve(want)) ? 'ok' : 'stale';
}

function pluginLinkSpec() {
  return 'link:' + path.resolve(REPO_ROOT, 'plugin', 'dsh-lingshu').split(path.sep).join('/');
}

function printHeader() {
  console.log('\n' + '='.repeat(64));
  console.log('  LINGSHU 沙盒实验舱 · Sandbox Demo');
  console.log('  灵为行为，枢为边界。  —  隔离数据，随时可删。');
  console.log('='.repeat(64));
}

function printIsolation({ home, dataDir, cliSource }) {
  console.log(OK + 'LINGSHU_DATA_DIR 强制 = ' + DATA_DIR_REL + '（绝对路径 ' + dataDir + '）');
  console.log(OK + 'DSH 会话/存储重定向 = ' + prettyPath(path.join(dataDir, 'dsh-sessions'))
    + ' · ' + prettyPath(path.join(dataDir, 'dsh-storages')));
  console.log(OK + '唯一写入的 DSH profile 位置 = ' + prettyPath(profileDir(home))
    + '（可抛弃；首次启动时 DSH 自身可能另外初始化 headless 模板 profile）');
  console.log(OK + '拒绝写入 = ' + prettyPath(realPluginDataDir(home)) + '（真实插件数据目录）');
  console.log(OK + '不碰 desktop profile · 不修改 DSH 家目录里的其它 profile');
  console.log(OK + '子进程遥测 = DISABLED；工具权限 = read-only；DSH CLI 来源 = ' + cliSource);
}

/** --dry-run：只做只读自检并打印将执行的命令；不写文件、不建 profile、不调用模型。 */
function printDryRun({ cli, home, dataDir, pnpm, args }) {
  const pdir = profileDir(home);
  const hasProfile = fs.existsSync(path.join(pdir, 'package.json'));
  const pluginState = profilePluginState(pdir);
  console.log('\n  [dry-run] 以下命令**不会执行**，仅打印（不写任何文件、不调用模型）：');
  console.log('  1) 创建 profile ' + PROFILE_NAME + '：' + (hasProfile ? '已存在，将跳过' : '将执行'));
  if (!hasProfile) {
    console.log('     "' + process.execPath + '" "' + cli.binJs + '" ' + PROFILE_NAME
      + ' --from-default-profile headless --dump-config');
  }
  console.log('  2) 安装 LINGSHU 插件：' + (pluginState === 'ok' ? '已安装，将跳过'
    : (pluginState === 'stale' ? '链接失效，将重新安装（需要 pnpm）' : '将执行（需要 pnpm）')));
  if (pluginState !== 'ok') {
    console.log('     "' + process.execPath + '" "' + cli.binJs + '" plugin --profile '
      + PROFILE_NAME + ' add ' + pluginLinkSpec());
  }
  console.log('  3) 预置合成记忆：node demo/seed.mjs --dir "' + dataDir + '"');
  console.log('  4) 每轮对话（cwd=' + REPO_ROOT + '）：');
  console.log('     "' + process.execPath + '" "' + cli.binJs + '" --profile ' + PROFILE_NAME
    + ' --patch "' + DEMO_PATCH + '" --json [--session-id <id>] "<你说的话>"');
  console.log('     子进程 env：LINGSHU_DATA_DIR=' + DATA_DIR_REL
    + ' · LINGSHU_DEMO_SANDBOX_DIR=' + dataDir
    + ' · DSH_TELEMETRY_MODE=DISABLED · DSH_PERMISSION_MODE=read-only · LINGSHU_CONTEXT_WINDOW='
    + (args.noWindowOverride ? '（未设，用真实窗口）' : args.windowOverride));
  console.log('  pnpm：' + (pnpm || '（未找到；首次安装插件需要 npm i -g pnpm）'));
  console.log('  沙盒数据：' + (fs.existsSync(path.join(dataDir, 'memory.jsonl'))
    ? '已存在预置记忆' : '尚不存在（首次启动将新建）'));
  console.log('');
}

function cleanSandbox({ home }) {
  const pdir = profileDir(home);
  const removed = [];
  const dataRemoved = removeDir(DATA_DIR);
  removed.push({ path: prettyPath(DATA_DIR), removed: dataRemoved });
  if (fs.existsSync(pdir)) {
    const r = removeDir(pdir);
    removed.push({ path: prettyPath(pdir), removed: r });
  } else {
    removed.push({ path: prettyPath(pdir), removed: false });
  }
  console.log('\n  已清理沙盒（只删下面两处，不碰其它 profile）：');
  for (const item of removed) {
    console.log('  ' + (item.removed ? '[deleted]' : '[absent] ') + ' ' + item.path);
  }
  console.log('  提醒：LINGSHU 插件目录 $DSH_HOME/plugins/dsh-lingshu 从未被本 demo 写入，未做任何删除。\n');
}

async function main() {
  let args;
  try {
    args = parseArgs(process.argv.slice(2));
  } catch (e) {
    console.error('[demo] ' + (e && e.message || e));
    process.exit(2);
  }
  if (args.help) {
    console.log(HELP);
    return;
  }

  const home = dshHome();
  const dataDir = assertSandboxDataDir(DATA_DIR, home);

  printHeader();

  if (args.clean) {
    cleanSandbox({ home });
    return;
  }

  // ── 1) 环境检查 ───────────────────────────────────────────────
  console.log(STEP(1, 5, '环境检查'));
  if (!nodeVersionOk()) {
    console.error(FAIL + 'Node 版本过低：' + process.version + '（DSH 要求 >= 20.10；建议 22/24 LTS）');
    console.error('  提示：装 Node 后重跑；或用 DSH 自带运行时（见 demo.sh 的探测逻辑）。');
    process.exit(2);
  }
  console.log(OK + 'Node ' + process.version + '（' + process.execPath + '）');

  const cli = resolveDshCli();
  if (!cli) {
    console.error(FAIL + '找不到 DSH CLI（@deepseek-ai/dsh/lib/bin.js）');
    console.error('  安装：npm i -g @deepseek-ai/dsh   或   npx @deepseek-ai/dsh --version');
    console.error('  已装但不在默认位置：export DSH_BIN=/abs/path/to/@deepseek-ai/dsh/lib/bin.js');
    console.error('  （本 demo 复用本机 DSH 与其已登录的模型凭据，不需要你重新配置 API。）');
    process.exit(2);
  }
  console.log(OK + 'DSH CLI ' + prettyPath(cli.binJs) + '（来源：' + cli.source + '）');
  console.log(OK + 'DSH_HOME ' + prettyPath(home));

  const pnpm = resolveExecutable('pnpm');
  if (pnpm) console.log(OK + 'pnpm ' + prettyPath(pnpm));
  else console.log(WARN + 'PATH 里没有 pnpm（仅首次安装插件时需要；没有会失败并给出提示）');

  // ── 2) 隔离自检 ───────────────────────────────────────────────
  console.log(STEP(2, 5, '隔离自检'));
  printIsolation({ home, dataDir, cliSource: cli.source });

  if (args.dryRun) {
    printDryRun({ cli, home, dataDir, pnpm, args });
    return;
  }

  ensureDir(dataDir);
  ensureDir(path.join(dataDir, 'dsh-sessions'));
  ensureDir(path.join(dataDir, 'dsh-storages'));

  const selfLink = ensurePluginSelfLink();
  if (selfLink.ok) {
    console.log(OK + '插件自解析链接 ' + prettyPath(selfLink.linkPath)
      + (selfLink.created ? '（已补建）' : '（已存在）'));
  } else {
    console.error(WARN + '插件自解析链接创建失败（宿主可能 failed to import）：' + selfLink.error);
  }

  // ── 3) 准备沙盒 profile ───────────────────────────────────────
  console.log(STEP(3, 5, '准备可抛弃 profile：' + PROFILE_NAME));
  const pdir = profileDir(home);
  const pkgFile = path.join(pdir, 'package.json');
  if (!fs.existsSync(pkgFile)) {
    console.log('  · 从 headless 模板创建 profile（首次启动会写 ' + prettyPath(pdir) + '）…');
    const r = runCli(cli, [PROFILE_NAME, '--from-default-profile', 'headless', '--dump-config']);
    if (!fs.existsSync(pkgFile)) {
      reportCliFailure('创建 profile ', r);
      console.error('  提示：确认 $DSH_HOME 可写、DSH 版本 >= 0.2.0；也可先手动执行：');
      console.error('    node "' + cli.binJs + '" ' + PROFILE_NAME + ' --from-default-profile headless --dump-config');
      process.exit(1);
    }
    console.log(OK + 'profile 已创建 ' + prettyPath(pdir));
  } else {
    console.log(OK + 'profile 已存在 ' + prettyPath(pdir));
  }

  const pluginState = profilePluginState(pdir);
  if (pluginState !== 'ok') {
    if (pluginState === 'stale') {
      console.log('  · 已有插件链接指向别处或已失效（仓库移动过？），重新安装…');
    } else {
      console.log('  · 安装 LINGSHU 插件（官方 CLI，link: 到本仓库）…');
    }
    if (!pnpm) {
      console.error(FAIL + '缺少 pnpm，无法安装插件。先执行：npm i -g pnpm');
      process.exit(2);
    }
    const r = runCli(cli, ['plugin', '--profile', PROFILE_NAME, 'add', pluginLinkSpec()]);
    if (r.status !== 0 || profilePluginState(pdir) !== 'ok') {
      reportCliFailure('安装插件 ', r);
      console.error('  提示：插件安装走 pnpm；若报 pnpm 存储/锁错误，检查磁盘权限后重试。');
      process.exit(1);
    }
    console.log(OK + '插件已安装（link: 到本仓库 plugin/dsh-lingshu，改动即时生效）');
  } else {
    console.log(OK + '插件已安装（link: 到本仓库 plugin/dsh-lingshu）');
  }

  // ── 4) 初始化沙盒数据 ─────────────────────────────────────────
  console.log(STEP(4, 5, '初始化沙盒数据：' + prettyPath(dataDir)));
  if (args.fresh) {
    console.log('  · --fresh：清空沙盒数据…');
    removeDir(dataDir);
    ensureDir(dataDir);
    ensureDir(path.join(dataDir, 'dsh-sessions'));
    ensureDir(path.join(dataDir, 'dsh-storages'));
  }
  const seeded = seedDemoData({ dataDir, quiet: true });
  if (seeded.skipped) {
    console.log(OK + '预置记忆已存在（' + seeded.ids.length + ' 条 demo_seed；--fresh 可重建）');
  } else {
    console.log(OK + '写入 ' + seeded.seeded + ' 条合成记忆，预置 turns=' + seeded.turns + '（越过冷启动）');
    console.log(OK + '预置数据说明 ' + prettyPath(path.join(dataDir, 'demo-seed.json')) + '（synthetic: true）');
  }

  // ── 5) 进入对话 ───────────────────────────────────────────────
  console.log(STEP(5, 5, '启动交互式对话'));
  if (!args.noWindowOverride) {
    console.log(OK + '相变窗口覆盖 ' + args.windowOverride
      + ' tokens（演示口径：只为让 70%/85% 在短会话里可达；--no-window-override 关闭）');
  } else {
    console.log(OK + '相变窗口使用宿主真实值（未覆盖）');
  }
  await startDemoChat({
    dshBin: cli.binJs,
    dataDir,
    windowOverride: args.noWindowOverride ? null : args.windowOverride,
    profileName: PROFILE_NAME,
  });
}

main().catch((e) => {
  console.error('[demo] 启动失败：' + (e && e.stack || e));
  process.exit(1);
});
