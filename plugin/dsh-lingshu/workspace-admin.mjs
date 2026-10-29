#!/usr/bin/env node
// workspace-admin.mjs —— 显式 legacy → workspace_<id> 数据迁移 CLI（管理员工具，不是面板 API）
//
// 为什么是 CLI 而不是自动迁移：目录隔离上线后，旧数据仍留在平铺目录里。自动迁移会在
// 没有用户确认的情况下移动记忆/审计账本（不可逆风险高），所以这里只提供**显式**命令：
//
//   node workspace-admin.mjs migrate --from legacy --to <id> --dry-run
//   node workspace-admin.mjs migrate --from legacy --to <id> --apply
//
// 选项：
//   --home <DSH_HOME>      覆盖 DSH_HOME（临时 home 测试用）
//   --data-dir <dir>       覆盖数据根（等价 LINGSHU_DATA_DIR；覆盖时不叠 profile 层）
//   --profile-tag <tag>    只迁移 <tag> 的 profile 数据（[A-Za-z0-9_-]{1,32}）
//
// 行为：
//   --dry-run  只计算计划（哈希/行数/冲突），不写任何文件；输出 JSON，退出码 0/2。
//   --apply    先备份原文件到 <数据根>/.migration-backups/<时间戳>-legacy-to-<id>/，
//              再移动 state.json / memory.jsonl / audit.jsonl（persona.json 若存在），
//              最后逐文件校验 sha256 + JSONL 行数 + state 可解析；校验失败自动回滚。
//              退出码 0 成功 / 2 前置条件拒绝 / 3 校验失败并已回滚。
//
// 边界：不做“自动迁移”、不改 LINGSHU_WORKSPACE、不写 UI、不合并到已存在的目标目录。

import crypto from 'node:crypto';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { WORKSPACE_ID_RE, PROFILE_TAG_RE, dataDir } from './workspace.js';

export const PLUGIN_NAME = 'dsh-lingshu';
export const MIGRATION_SCHEMA = 'workspace-migration-0.1';
// 迁移的账本文件；persona.json 可选（旧平铺目录一般没有）。last-prompt.txt 属于会话临时文件，不迁移。
const DATA_FILES = ['state.json', 'memory.jsonl', 'audit.jsonl', 'persona.json'];

const USAGE = `workspace-admin — LINGSHU 工作区数据迁移（显式、可回滚）

用法：
  node workspace-admin.mjs migrate --from legacy --to <id> --dry-run
  node workspace-admin.mjs migrate --from legacy --to <id> --apply

选项：
  --from legacy            固定值（当前只支持从平铺目录迁出）
  --to <id>                目标工作区 id（[A-Za-z0-9_-]{1,32}，不能是 legacy）
  --dry-run | --apply      二选一；apply 会先备份再移动并校验
  --home <dir>             DSH_HOME（默认 $DSH_HOME 或 ~/.dsh）
  --data-dir <dir>         数据根（默认 <DSH_HOME>/plugins/dsh-lingshu；设置后不叠 profile 层）
  --profile-tag <tag>      只迁移该 profile 的平铺数据
  --help                   显示本说明
`;

function isFile(file) {
  try { return fs.lstatSync(file).isFile(); } catch { return false; }
}
function isDir(dir) {
  try { return fs.lstatSync(dir).isDirectory(); } catch { return false; }
}
function sha256File(file) {
  return crypto.createHash('sha256').update(fs.readFileSync(file)).digest('hex');
}
function countJsonlLines(file) {
  try {
    return fs.readFileSync(file, 'utf8').split('\n').filter((line) => line.trim()).length;
  } catch {
    return null;
  }
}
function readJsonFile(file) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
  } catch {
    return null;
  }
}
function relativeTo(base, dir) {
  const rel = path.relative(path.resolve(base), path.resolve(dir));
  return rel ? rel.split(path.sep).join('/') : '.';
}

/** 解析 CLI 参数；不抛异常，返回 {ok, options|error}。 */
export function parseArgs(argv = []) {
  const options = {
    command: '', from: '', to: '', dryRun: false, apply: false,
    home: '', dataDir: '', profileTag: '', help: false,
  };
  if (argv.includes('--help') || argv.includes('-h')) {
    return { ok: true, options: { ...options, help: true } };
  }
  options.command = String(argv[0] || '');
  for (let i = 1; i < argv.length; i += 1) {
    const arg = String(argv[i] || '');
    if (arg === '--dry-run') { options.dryRun = true; continue; }
    if (arg === '--apply') { options.apply = true; continue; }
    if (arg === '--help' || arg === '-h') { options.help = true; continue; }
    const takeValue = () => {
      const value = argv[i + 1];
      if (typeof value !== 'string' || value.startsWith('--')) return null;
      i += 1;
      return value;
    };
    if (arg === '--from') { options.from = takeValue() ?? ''; continue; }
    if (arg === '--to') { options.to = takeValue() ?? ''; continue; }
    if (arg === '--home') { options.home = takeValue() ?? ''; continue; }
    if (arg === '--data-dir') { options.dataDir = takeValue() ?? ''; continue; }
    if (arg === '--profile-tag') { options.profileTag = takeValue() ?? ''; continue; }
    return { ok: false, error: `unknown_argument:${arg.slice(0, 32)}` };
  }
  return { ok: true, options };
}

/** 计算迁移上下文（数据根、源目录、目标目录）。 */
export function resolveContext(options = {}) {
  const home = options.home && String(options.home).trim()
    ? path.resolve(String(options.home).trim())
    : (process.env.DSH_HOME && String(process.env.DSH_HOME).trim()
      ? path.resolve(String(process.env.DSH_HOME).trim())
      : path.join(os.homedir(), '.dsh'));
  const overrideRaw = options.dataDir && String(options.dataDir).trim()
    ? String(options.dataDir).trim()
    : (process.env.LINGSHU_DATA_DIR && String(process.env.LINGSHU_DATA_DIR).trim()
      ? String(process.env.LINGSHU_DATA_DIR).trim() : '');
  const profileTag = String(options.profileTag || '').trim();
  const base = overrideRaw ? path.resolve(overrideRaw) : path.join(home, 'plugins', PLUGIN_NAME);
  // 与 index.js#stateDir 同口径：LINGSHU_DATA_DIR 覆盖时不叠 profile 层。
  const pathProfileTag = overrideRaw ? '' : profileTag;
  return { home, base, profileTag, pathProfileTag };
}

/** 只读地计算迁移计划（dry-run 与 apply 共用；apply 会重新算一次再动手）。 */
export function buildPlan({ base, pathProfileTag, to }) {
  const legacyDir = dataDir({ base, workspaceId: '', profileTag: pathProfileTag });
  const targetDir = dataDir({ base, workspaceId: to, profileTag: pathProfileTag });
  const wsRoot = path.join(base, `workspace_${to}`);
  const problems = [];
  if (!isDir(legacyDir)) problems.push({ code: 'source_missing' });
  if (!isFile(path.join(legacyDir, 'state.json'))) problems.push({ code: 'source_state_missing' });
  // 目标必须完全不存在：不合并、不覆盖（存在即拒绝，交给管理员人工处理）。
  if (fs.existsSync(wsRoot) || fs.existsSync(targetDir)) problems.push({ code: 'target_exists' });
  if (path.resolve(legacyDir) === path.resolve(targetDir)) problems.push({ code: 'source_equals_target' });

  const files = DATA_FILES
    .filter((name) => isFile(path.join(legacyDir, name)))
    .map((name) => {
      const file = path.join(legacyDir, name);
      return {
        name,
        bytes: fs.statSync(file).size,
        sha256: sha256File(file),
        lines: name.endsWith('.jsonl') ? countJsonlLines(file) : null,
      };
    });
  const state = readJsonFile(path.join(legacyDir, 'state.json'));
  return {
    legacyDir,
    targetDir,
    wsRoot,
    problems,
    files,
    state: state && typeof state === 'object'
      ? { ok: true, turns: Number(state.turns ?? 0), fields: Object.keys(state).length }
      : { ok: false, turns: null, fields: 0 },
  };
}

function moveFile(source, target) {
  try {
    fs.renameSync(source, target);
    return 'rename';
  } catch (err) {
    if (err && err.code !== 'EXDEV') throw err;
    fs.copyFileSync(source, target);
    fs.unlinkSync(source);
    return 'copy+unlink';
  }
}

function moveJsonlLines(file) {
  return countJsonlLines(file);
}

/** 生成备份目录名（同簇时间戳，避免冒号/点号在 Windows 上非法）。 */
function backupDirName(to) {
  const stamp = new Date().toISOString().replace(/[:.]/g, '-');
  return `${stamp}-legacy-to-${to}`;
}

function output(result) {
  return JSON.stringify(result, null, 2);
}

/**
 * 迁移主入口（可被 sim 直接调用，不必起子进程）。
 * @returns {{exitCode:number, result:object}}
 */
export function migrate(argv = []) {
  const parsed = parseArgs(argv);
  if (!parsed.ok) {
    return { exitCode: 2, result: { ok: false, command: 'migrate', error: parsed.error } };
  }
  const options = parsed.options;
  if (options.help) {
    return { exitCode: 0, result: { ok: true, help: USAGE } };
  }
  if (options.command !== 'migrate') {
    return { exitCode: 2, result: { ok: false, error: 'unknown_command', command: options.command || null } };
  }
  if (options.dryRun === options.apply) {
    return { exitCode: 2, result: { ok: false, error: 'exactly_one_of_dry_run_or_apply_required' } };
  }
  if (options.from !== 'legacy') {
    return { exitCode: 2, result: { ok: false, error: 'from_must_be_legacy', from: options.from || null } };
  }
  const to = String(options.to || '').trim();
  if (!WORKSPACE_ID_RE.test(to) || to === 'legacy') {
    return { exitCode: 2, result: { ok: false, error: 'invalid_target_workspace_id' } };
  }
  if (options.profileTag && !PROFILE_TAG_RE.test(String(options.profileTag).trim())) {
    return { exitCode: 2, result: { ok: false, error: 'invalid_profile_tag' } };
  }

  const context = resolveContext(options);
  const plan = buildPlan({ ...context, to });
  // 备份目录名只算一次：dry-run 展示的路径必须与 apply 实际使用的路径一致。
  const backupDir = path.join(context.base, '.migration-backups', backupDirName(to));
  const baseResult = {
    ok: true,
    schema: MIGRATION_SCHEMA,
    command: 'migrate',
    dryRun: Boolean(options.dryRun),
    applied: false,
    from: 'legacy',
    to,
    dataRoot: path.resolve(context.base),
    profileTag: context.profileTag || null,
    confirmation: {
      from: 'legacy',
      to,
      dataRoot: path.resolve(context.base),
      profileTag: context.profileTag || null,
      files: plan.files.map((f) => f.name),
    },
    source: {
      relativeDataDir: relativeTo(context.base, plan.legacyDir),
      files: plan.files,
      state: plan.state,
    },
    target: {
      relativeDataDir: relativeTo(context.base, plan.targetDir),
      exists: fs.existsSync(plan.targetDir) || fs.existsSync(plan.wsRoot),
    },
    backup: {
      relativeDataDir: relativeTo(context.base, backupDir),
    },
    problems: plan.problems,
    rollback: {
      backupRoot: relativeTo(context.base, path.join(context.base, '.migration-backups')),
      steps: [
        '1) 停止使用该数据根的 LINGSHU 插件（避免迁移/回滚过程中并发写）',
        '2) 把备份目录里的 state.json / memory.jsonl / audit.jsonl（及 persona.json）复制回源平铺目录',
        '3) 删除 workspace_<id>/ 下对应文件（备份目录保留，供事后核对）',
        '4) 重启宿主，确认 /dsh-lingshu/api/workspaces 回到 legacy 视图',
      ],
    },
  };

  if (plan.problems.length) {
    return {
      exitCode: 2,
      result: { ...baseResult, ok: false, error: 'precondition_failed' },
    };
  }
  if (options.dryRun) {
    return {
      exitCode: 0,
      result: {
        ...baseResult,
        changes: plan.files.map((f) => ({
          action: 'move', file: f.name, bytes: f.bytes, sha256: f.sha256, lines: f.lines,
        })),
      },
    };
  }

  // ── apply：先备份，再移动，再校验；失败回滚 ──────────────────────
  try {
    fs.mkdirSync(backupDir, { recursive: true });
    for (const f of plan.files) {
      const src = path.join(plan.legacyDir, f.name);
      fs.copyFileSync(src, path.join(backupDir, f.name));
    }
    fs.writeFileSync(path.join(backupDir, 'manifest.json'), JSON.stringify({
      schema: MIGRATION_SCHEMA,
      created_at: new Date().toISOString(),
      from: 'legacy',
      to,
      data_root: path.resolve(context.base),
      profile_tag: context.profileTag || null,
      source_relative_data_dir: relativeTo(context.base, plan.legacyDir),
      target_relative_data_dir: relativeTo(context.base, plan.targetDir),
      files: plan.files,
      state: plan.state,
      note: '迁移前原文件副本；回滚 = 复制回源目录并删除目标目录下对应文件',
    }, null, 2) + '\n', 'utf8');
  } catch (err) {
    return {
      exitCode: 2,
      result: {
        ...baseResult,
        ok: false,
        error: 'backup_failed',
        detail: String(err?.code || 'io_error'),
        backup: { relativeDataDir: relativeTo(context.base, backupDir) },
      },
    };
  }

  const moved = [];
  const movedBy = {};
  try {
    fs.mkdirSync(plan.targetDir, { recursive: true });
    for (const f of plan.files) {
      const src = path.join(plan.legacyDir, f.name);
      const dst = path.join(plan.targetDir, f.name);
      movedBy[f.name] = moveFile(src, dst);
      moved.push(f.name);
    }
  } catch (err) {
    // 移动阶段失败：把已经移走的文件搬回去（备份还在），再报错。
    for (const name of moved) {
      const src = path.join(plan.legacyDir, name);
      const dst = path.join(plan.targetDir, name);
      try { fs.copyFileSync(dst, src); fs.unlinkSync(dst); } catch { /* best-effort */ }
    }
    return {
      exitCode: 3,
      result: {
        ...baseResult,
        ok: false,
        error: 'move_failed_rolled_back',
        detail: String(err?.code || 'io_error'),
        moved,
        backup: { relativeDataDir: relativeTo(context.base, backupDir) },
      },
    };
  }

  // ── 校验：sha256 + JSONL 行数 + state 可解析 ─────────────────────
  const verification = [];
  let verified = true;
  for (const f of plan.files) {
    const dst = path.join(plan.targetDir, f.name);
    const exists = isFile(dst);
    const sha256 = exists ? sha256File(dst) : null;
    const lines = exists && f.name.endsWith('.jsonl') ? moveJsonlLines(dst) : null;
    const hashOk = exists && sha256 === f.sha256;
    const linesOk = f.lines === null || lines === f.lines;
    if (!hashOk || !linesOk) verified = false;
    verification.push({ name: f.name, exists, hash_ok: hashOk, lines_expected: f.lines, lines_actual: lines, lines_ok: linesOk, move: movedBy[f.name] || null });
  }
  const stateAfter = readJsonFile(path.join(plan.targetDir, 'state.json'));
  const stateFields = stateAfter && typeof stateAfter === 'object' ? Object.keys(stateAfter).length : 0;
  const stateOk = Boolean(stateAfter) && Number(stateAfter.turns ?? 0) === plan.state.turns
    && stateFields === plan.state.fields;
  if (!stateOk) verified = false;

  if (!verified) {
    // 回滚：从备份恢复源文件，删除目标目录下迁移过去的文件（备份目录保留，供人工核对）。
    for (const f of plan.files) {
      const src = path.join(plan.legacyDir, f.name);
      const dst = path.join(plan.targetDir, f.name);
      try {
        const backup = path.join(backupDir, f.name);
        if (isFile(backup)) fs.copyFileSync(backup, src);
        if (isFile(dst)) fs.unlinkSync(dst);
      } catch { /* best-effort */ }
    }
    try {
      const left = fs.readdirSync(plan.targetDir).filter((n) => !n.startsWith('.'));
      if (left.length === 0) fs.rmdirSync(plan.targetDir);
    } catch { /* noop */ }
    return {
      exitCode: 3,
      result: {
        ...baseResult,
        ok: false,
        applied: false,
        rolledBack: true,
        error: 'verification_failed_rolled_back',
        files: verification,
        state: { ok: stateOk, turns: stateAfter ? Number(stateAfter.turns ?? 0) : null, fields: stateFields },
        backup: { relativeDataDir: relativeTo(context.base, backupDir) },
      },
    };
  }

  return {
    exitCode: 0,
    result: {
      ...baseResult,
      ok: true,
      applied: true,
      activated: false,               // 迁移不等于激活：新工作区仍需 LINGSHU_WORKSPACE + 重启
      files: verification,
      state: { ok: true, turns: Number(stateAfter.turns ?? 0), fields: stateFields },
      backup: { relativeDataDir: relativeTo(context.base, backupDir) },
      audit_note: '迁移事件在源目录/目标目录之外由管理员独立记录；插件不会自动补写审计',
    },
  };
}

const isMain = process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url);
if (isMain) {
  const { exitCode, result } = migrate(process.argv.slice(2));
  if (result && result.help) {
    process.stdout.write(result.help);
  } else {
    process.stdout.write(output(result) + '\n');
  }
  process.exitCode = exitCode;
}

export default { migrate, parseArgs, resolveContext, buildPlan, PLUGIN_NAME, MIGRATION_SCHEMA };
