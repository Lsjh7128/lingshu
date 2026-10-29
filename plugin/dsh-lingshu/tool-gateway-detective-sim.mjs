// tool-gateway-detective-sim.mjs —— 事后判定（detective path）不得谎称"能拦"
//
// 背景（2026-10-28 实测）：audit.jsonl 里成片 `tool:"pwsh"|"read"|"write" verdict:"block"
// ucas:["T-UCA-08"]` —— 宿主工具全判"未登记"；而事后路径（session/event(tool/result)，
// blocking_capable=false）本来就没有阻断能力，却照旧置"待澄清"标志并给模型注入强提示。
//
// v1.2 起宿主工具已进登记表（`HOST_TOOL_SPECS`，与 engine/tool_gateway.py 逐字段 parity），
// 所以 T-UCA-08 从根上消失。本文件钉住两条**不变式**：
//   ① 未登记/空工具名 → enforceable=false + would_block=true：调用方只计数，不得注入强提示；
//   ② 登记过的工具 → enforceable=true：规则命中按 ADR-013 走"事后检测 + 强提示 + 审计"，
//      且判定必须按 **工具类别** 而不是 payload 文本（写代码 ≠ 执行代码）。
//
// 用法：node tool-gateway-detective-sim.mjs [--assert]

import { evaluate, defaultState, REGISTRY } from './tool-gateway.js';

const ASSERT_ONLY = process.argv.includes('--assert');
const results = [];
function check(label, cond, detail = '') {
  results.push({ label, cond: Boolean(cond) });
  if (!ASSERT_ONLY) console.log(`${cond ? 'PASS' : 'FAIL'}  ${label}${detail ? '  — ' + detail : ''}`);
}
const WORKSPACE = 'D:\\LSJH';
function state(extra = {}) {
  return { ...defaultState(), at: 1, workspace_root: WORKSPACE, ...extra };
}

// ── 1. 未登记工具 / 空工具名：只能记 would_block ──────────────
for (const [label, tool] of [['未登记工具', 'totally_unknown_tool'], ['空工具名', '']]) {
  const r = evaluate({ id: 'c1', tool, args: { a: 1 } }, state());
  check(`${label}: 判 block + T-UCA-08（保留可追溯）`,
    r.verdict === 'block' && r.ucas.includes('T-UCA-08'), `${r.verdict} ${r.ucas.join(',')}`);
  check(`${label}: enforceable=false（不宣称能拦）`, r.enforceable === false);
  check(`${label}: would_block=true（调用方只记不提示）`, r.would_block === true);
  check(`${label}: audit 同口径`, r.audit.enforceable === false && r.audit.would_block === true);
}

// ── 2. v1.2：宿主工具已登记 → 工作区内常规操作不再误报 ────────
const inWorkspace = [
  ['read', { file_path: 'D:\\LSJH\\lingshu\\README.md' }],
  ['grep', { pattern: 'token|secret', path: 'D:\\LSJH\\lingshu' }],   // payload 含敏感词也不该误报
  ['glob', { pattern: '**/*.md', path: 'D:\\LSJH\\lingshu' }],
  ['write', { file_path: 'D:\\LSJH\\lingshu\\docs\\x.md', content: 'function f(){ return 1 }' }], // 写代码 ≠ 执行代码
  ['edit', { file_path: 'D:\\LSJH\\lingshu\\docs\\x.md', old_string: 'a', new_string: 'b' }],
  ['pwsh', { command: 'Get-ChildItem D:\\LSJH\\lingshu' }],
  ['job_output', { job_id: 'j1' }],
  ['team_task_create', { subject: 's', description: 'd' }],
];
for (const [tool, args] of inWorkspace) {
  const r = evaluate({ id: 'c_' + tool, tool, args }, state());
  check(`${tool}: 工作区内放行，不产生 T-UCA-08`, !r.ucas.includes('T-UCA-08'), `${r.verdict} ${r.ucas.join(',')}`);
  check(`${tool}: verdict=allow（无路径/能力误报）`, r.verdict === 'allow', `${r.verdict} ${r.ucas.join(',')}`);
  check(`${tool}: 已登记 → enforceable=true`, r.enforceable === true);
}

// ── 3. 真规则仍然生效（这才是"可执行"的判定，允许走强提示） ────
{
  const out = evaluate({ id: 'c_out', tool: 'read', args: { file_path: 'C:\\Windows\\win.ini' } }, state());
  check('工作区外读 → T-UCA-15（真路径逃逸）', out.verdict === 'block' && out.ucas.includes('T-UCA-15'),
    `${out.verdict} ${out.ucas.join(',')}`);
  check('真规则命中仍标 enforceable=true（ADR-013 事后纠偏允许提示）', out.enforceable === true);

  const gh = evaluate({ id: 'c_gh', tool: 'web_fetch', args: { url: 'https://github.com/deepseek-ai/dsh' } }, state());
  check('白名单外发（github）放行', gh.verdict === 'allow', `${gh.verdict} ${gh.ucas.join(',')}`);
  const ex = evaluate({ id: 'c_ex', tool: 'web_fetch', args: { url: 'https://example.com' } }, state());
  check('非白名单外发 → T-UCA-05', ex.verdict === 'block' && ex.ucas.includes('T-UCA-05'),
    `${ex.verdict} ${ex.ucas.join(',')}`);
}

// ── 4. 自家工具 schema 误报（2026-10-28）────────────────────
{
  const v = evaluate({ id: 'c_ls', tool: 'lingshu_status', args: { verbose: true } }, state());
  check('lingshu_status({verbose:true}) 不误判 T-UCA-09',
    v.verdict === 'allow' && !v.ucas.includes('T-UCA-09'), `${v.verdict} ${v.ucas.join(',')}`);
  const empty = evaluate({ id: 'c_ls2', tool: 'lingshu_status', args: {} }, state());
  check('lingshu_status({}) 放行', empty.verdict === 'allow');
}

// ── 5. 登记表结构自检（v1.2 宿主工具已登记，防止回退） ─────────
{
  const names = Object.keys(REGISTRY);
  check('登记表已含宿主工具（v1.2 HOST_TOOL_SPECS）',
    ['read', 'write', 'edit', 'pwsh', 'grep', 'glob', 'job_output'].every((n) => names.includes(n)),
    `count=${names.length}`);
  check('登记表条目数 ≥ 60（v1.2 全量）', names.length >= 60, `count=${names.length}`);
  check('lingshu_status 的 schema 已声明 verbose',
    Boolean(REGISTRY.lingshu_status && REGISTRY.lingshu_status.schema
      && 'verbose' in REGISTRY.lingshu_status.schema));
}

const failed = results.filter((r) => !r.cond);
const passed = results.length - failed.length;
if (ASSERT_ONLY) {
  for (const f of failed) console.log(`FAIL  ${f.label}`);
  console.log(`tool-gateway-detective-sim: ${passed}/${results.length} passed, ${failed.length} failed`);
}
process.exit(failed.length ? 1 : 0);
