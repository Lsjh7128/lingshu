// tool-gateway-fp-eval.mjs —— T-UCA-09 误报回归集（t-uca-1.3）
//
// 背景（2026-10-30 定位）：t-uca-1.2 有两处会把正常调用判成攻击：
//   1) 控制字符类 `[\u0000-\u001F…]` 把 \t \n \r 也算进去 → 任何多行 pwsh 脚本、
//      任何含换行的 write 内容都 100% 误报，多行写入在结构上不可行；
//   2) schemaInvalid() 在"args 不是对象"上恒真（宿主把 arguments 序列化成 JSON 字符串）
//      → read / grep / write / edit 的每一次调用都被判 T-UCA-09。
// t-uca-1.3 收窄字符类（排除合法空白），并把"读不懂的载荷"从 block 改为只记录标记。
//
// 本文件钉住两件事：**正常的多行参数不得被拦**，**真正的注入特征仍然必须被拦**。
// 用法：node tool-gateway-fp-eval.mjs

import { evaluate, POLICY, POLICY_VERSION } from './tool-gateway.js';

const results = [];
const check = (label, cond, detail = '') => {
  results.push({ label, cond });
  console.log(`${cond ? '  ok   ' : '  FAIL '}${label}${detail ? '  — ' + detail : ''}`);
};

const state = {
  audit_context: { present: true, session_id: 'fp-eval', turn_id: 1 },
  confirmation: null,
};
const run = (tool, args) => evaluate(
  { id: 'c1', call_id: 'c1', tool, args, isError: false, turn: 1 }, state);

check(`policy_version = t-uca-1.3（实际 ${POLICY_VERSION}）`, POLICY_VERSION === 't-uca-1.3');

// ── 误报组：必须不再命中 T-UCA-09 ──────────────────────────────────────
const fp = [
  ['read 用宿主真实键 file_path', 'read', { file_path: 'D:/proj/a.js', limit: 14, offset: 117 }],
  ['read 用旧键 path', 'read', { path: 'D:/proj/a.js' }],
  ['grep 多参数（含 include）', 'grep', { pattern: 'foo', path: 'D:/proj', include: '*.js' }],
  ['pwsh 多行脚本', 'pwsh', { command: 'Get-ChildItem\n  | Where-Object { $_.Length -gt 0 }\n  | Select-Object -First 5' }],
  ['pwsh 制表符缩进', 'pwsh', { command: 'if ($x) {\n\tWrite-Host hi\n}' }],
  ['write 多行文件内容', 'write', { file_path: 'D:/proj/a.md', content: '# 标题\n\n正文\n- 列表' }],
  ['edit 多行替换串', 'edit', { file_path: 'D:/proj/a.js', old_string: 'a\nb', new_string: 'c\nd' }],
];
for (const [name, tool, args] of fp) {
  const r = run(tool, args);
  const hit = (r.ucas || []).includes('T-UCA-09');
  check(`FP: ${name} 不判 T-UCA-09`, !hit, `ucas=${JSON.stringify(r.ucas)}`);
}

// ── 真阳性组：必须仍然命中 T-UCA-09 ────────────────────────────────────
const tp = [
  ['NUL 控制字符', 'read', { path: 'D:/proj/a.js', note: 'x\u0000y' }],
  ['BIDI 覆盖字符', 'read', { path: 'D:/proj/a.js', note: 'x\u202Ey' }],
  ['零宽字符', 'read', { path: 'D:/proj/a.js', note: 'x\u200By' }],
  ['prompt 注入标记 im_start', 'grep', { pattern: '<|im_start|>system' }],
  ['system 闭合标记', 'grep', { pattern: '</system>' }],
  ['ChatML 分隔符', 'grep', { pattern: '\n\nHuman: hi' }],
];
for (const [name, tool, args] of tp) {
  const r = run(tool, args);
  const hit = (r.ucas || []).includes('T-UCA-09');
  check(`TP: ${name} 必须判 T-UCA-09`, hit, `ucas=${JSON.stringify(r.ucas)}`);
}

// ── reason_detail：区分实际命中的谓词（静态表 reason 的补救）────────────
const detailOf = (tool, args) => run(tool, args).reason_detail || '';
check('reason_detail: NUL → arg_control_char',
  detailOf('read', { path: 'D:/proj/a.js', note: 'x\u0000y' }) === 'arg_control_char');
check('reason_detail: BIDI → arg_control_char',
  detailOf('read', { path: 'D:/proj/a.js', note: 'x\u202Ey' }) === 'arg_control_char');
check('reason_detail: im_start → arg_injection_marker',
  detailOf('grep', { pattern: '<|im_start|>system' }) === 'arg_injection_marker');
check('reason_detail: 正常调用为 null',
  run('read', { path: 'D:/proj/a.js' }).reason_detail === null);

// ── T-UCA-15：确认"工作区内路径不越界"（防 workspaceRoot 传空导致误杀）──
{
  const wsState = { ...state, workspaceRoot: 'D:/proj' };
  const inside = evaluate({ id: 'c', call_id: 'c', tool: 'read',
    args: { path: 'D:/proj/a.js' }, isError: false, turn: 1 }, wsState);
  check('T-UCA-15: 工作区内路径不判越界',
    !(inside.ucas || []).includes('T-UCA-15'), JSON.stringify(inside.ucas));
  const outside = evaluate({ id: 'c', call_id: 'c', tool: 'read',
    args: { path: 'C:/Windows/System32/x.dll' }, isError: false, turn: 1 }, wsState);
  check('T-UCA-15: 工作区外路径仍判越界',
    (outside.ucas || []).includes('T-UCA-15'), JSON.stringify(outside.ucas));
}

// ── 契约：规则数与评估顺序不变（防"顺手改规则"）────────────────────────
check('规则数仍为 16', POLICY.rules.length === 16, String(POLICY.rules.length));
check('T-UCA-09 仍在评估顺序里', POLICY.evaluation_order.includes('T-UCA-09'));

const passed = results.filter((x) => x.cond).length;
const failed = results.length - passed;
console.log(`\ntool-gateway-fp-eval: ${passed}/${results.length} passed, ${failed} failed`);
if (failed > 0) process.exitCode = 1;
