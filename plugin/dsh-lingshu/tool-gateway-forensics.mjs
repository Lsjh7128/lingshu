#!/usr/bin/env node
// 工具网关误报取证（只读，不改任何判定行为）
//
// 目的：用真实审计把"判了 block 但究竟是什么形状"摊开，定位 docs/29 §3 的 H1–H4。
// 为什么必须先取证：REGISTRY 对宿主工具写了 schema:{} + allowUnknown:true，
// schemaInvalid() 在那种条目下应当为 false —— 真实事件却判了 T-UCA-09，
// 说明判定时拿到的 ctx 与 sim 构造的不是同一个形状。没有样本的"修"就是 v1.2 那样只修样例。
//
// 用法：
//   node tool-gateway-forensics.mjs [--data <路径>] [--json] [--check] [--top N]
//   --data  审计文件或数据根；省略时按 LINGSHU_DATA_DIR / DSH_HOME 推断
//   --check 发布门禁：任一工具 block 占比 > 1% 时 exit 1
//   --json  机器可读输出
//
// 隐私：只输出 args_keys / args_bytes / args_preview(≤120 字符)，不输出正文。

import fs from 'node:fs'
import path from 'node:path'

const argv = process.argv.slice(2)
const opt = { data: null, json: false, check: false, top: 10 }
for (let i = 0; i < argv.length; i++) {
  const a = argv[i]
  if (a === '--data') opt.data = argv[++i]
  else if (a === '--json') opt.json = true
  else if (a === '--check') opt.check = true
  else if (a === '--top') opt.top = Number(argv[++i]) || 10
  else if (a === '--help' || a === '-h') { console.log(fs.readFileSync(new URL(import.meta.url), 'utf8').slice(0, 1200)); process.exit(0) }
}

function candidates() {
  const out = []
  if (opt.data) out.push(opt.data)
  if (process.env.LINGSHU_DATA_DIR) out.push(process.env.LINGSHU_DATA_DIR)
  if (process.env.DSH_HOME) out.push(path.join(process.env.DSH_HOME, 'plugins', 'dsh-lingshu'))
  out.push(path.join(process.cwd(), 'audit.jsonl'))
  return out
}

function findAudit(p) {
  if (!p) return null
  try {
    if (fs.statSync(p).isFile()) return p.endsWith('audit.jsonl') ? p : null
  } catch { return null }
  const direct = path.join(p, 'audit.jsonl')
  if (fs.existsSync(direct)) return direct
  // layout-0.2：sandboxes/<sid>/<pid>/audit.jsonl —— 浅扫三层，不猜深路径
  const stack = [[p, 0]]
  while (stack.length) {
    const [dir, d] = stack.pop()
    if (d > 3) continue
    let entries = []
    try { entries = fs.readdirSync(dir, { withFileTypes: true }) } catch { continue }
    for (const e of entries) {
      const full = path.join(dir, e.name)
      if (e.isFile() && e.name === 'audit.jsonl') return full
      if (e.isDirectory()) stack.push([full, d + 1])
    }
  }
  return null
}

let auditPath = null
const tried = []
for (const c of candidates()) {
  tried.push(c)
  auditPath = findAudit(c)
  if (auditPath) break
}
if (!auditPath) {
  console.error('[forensics] 找不到 audit.jsonl。已尝试：\n  ' + tried.join('\n  ')
    + '\n用 --data <审计文件或数据根> 指定。')
  process.exit(2)
}

const gates = []
const callsSeen = new Set()
let parseErrors = 0
for (const line of fs.readFileSync(auditPath, 'utf8').split(/\r?\n/)) {
  if (!line.trim()) continue
  let ev
  try { ev = JSON.parse(line) } catch { parseErrors++; continue }
  if (ev.kind === 'tool_call_seen') { if (ev.tool) callsSeen.add(ev.tool); continue }
  if (ev.kind !== 'tool_gate') continue
  gates.push(ev)
}

const byTool = new Map()
const byVerdict = { allow: 0, block: 0, degrade: 0, other: 0 }
const byReason = new Map()
for (const g of gates) {
  const tool = String(g.tool || '<unknown>')
  const ucas = Array.isArray(g.ucas) ? g.ucas.join('+') : String(g.ucas || '')
  const key = `${tool} || ${g.verdict || '?'} || ${ucas || '-'}`
  if (!byTool.has(tool)) byTool.set(tool, { tool, total: 0, block: 0, byKey: new Map() })
  const t = byTool.get(tool)
  t.total++
  if (g.verdict === 'block') t.block++
  byTool.set(tool, t)
  if (!t.byKey.has(key)) {
    t.byKey.set(key, {
      verdict: g.verdict, ucas, count: 0,
      sample: {
        reason: g.reason,
        policy_version: g.policy_version,
        args_keys: g.args_keys || g.audit?.args_keys || null,
        args_bytes: g.args_bytes ?? g.audit?.args_bytes ?? null,
        args_preview: String(g.args_preview ?? g.audit?.args_preview ?? '').slice(0, 120),
        phase: g.phase ?? g.audit?.phase ?? null,
        enforceable: g.enforceable ?? null,
      },
    })
  }
  t.byKey.get(key).count++
  byVerdict[g.verdict] = (byVerdict[g.verdict] ?? 0) + 1
  const rc = String(g.reason || '').split(':')[0] || '-'
  byReason.set(rc, (byReason.get(rc) || 0) + 1)
}

const rows = [...byTool.values()]
  .map((t) => ({ tool: t.tool, total: t.total, block: t.block, blockShare: t.total ? t.block / t.total : 0 }))
  .sort((a, b) => b.block - a.block || b.total - a.total)

const report = {
  audit: auditPath,
  parse_errors: parseErrors,
  events: gates.length,
  verdicts: byVerdict,
  tools_with_call_seen_but_no_gate: [...callsSeen].filter((t) => !byTool.has(t)),
  block_share_overall: gates.length ? byVerdict.block / gates.length : 0,
  tools: rows,
  reasons: [...byReason.entries()].sort((a, b) => b[1] - a[1]),
  buckets: [...byTool.values()].flatMap((t) => [...t.byKey.entries()]
    .map(([key, v]) => ({ key, ...v }))
    .sort((a, b) => b.count - a.count).slice(0, opt.top)),
}

if (opt.json) {
  console.log(JSON.stringify(report, null, 2))
} else {
  console.log(`[forensics] ${auditPath}`)
  console.log(`  事件 ${report.events} ｜ block ${byVerdict.block} ｜ allow ${byVerdict.allow} ｜ degrade ${byVerdict.degrade}`
    + ` ｜ 总体 block 占比 ${(report.block_share_overall * 100).toFixed(1)}%`)
  if (report.tools_with_call_seen_but_no_gate.length) {
    console.log(`  ⚠ 有 tool_call_seen 但无 tool_gate（网关没跑到）：${report.tools_with_call_seen_but_no_gate.join(', ')}`)
  }
  console.log('\n  按工具：')
  for (const r of rows) {
    console.log(`    ${r.tool.padEnd(22)} total ${String(r.total).padStart(3)}  block ${String(r.block).padStart(3)}  (${(r.blockShare * 100).toFixed(0)}%)`)
  }
  console.log('\n  命中 code：')
  for (const [k, n] of report.reasons) console.log(`    ${k.padEnd(12)} ${n}`)
  console.log(`\n  Top ${opt.top} 分桶（含样本形状）：`)
  for (const b of report.buckets) {
    console.log(`\n    ${b.key}  ×${b.count}`)
    console.log(`      reason        ${b.sample.reason ?? '-'}`)
    console.log(`      policy        ${b.sample.policy_version ?? '-'}`)
    console.log(`      args_keys     ${JSON.stringify(b.sample.args_keys)}`)
    console.log(`      args_bytes    ${b.sample.args_bytes ?? '-'}`)
    console.log(`      phase         ${b.sample.phase ?? '-'}   enforceable ${b.sample.enforceable ?? '-'}`)
    console.log(`      preview       ${b.sample.args_preview || '-'}`)
  }
  console.log('\n  下一步：把上面 args_keys / schema 形状与 HOST_TOOL_SPECS 对照，判定 H1–H4 哪一个成立。')
}

const offenders = rows.filter((r) => r.blockShare > 0.01)
if (opt.check && offenders.length) {
  console.error(`\n[forensics][check] 失败：${offenders.map((o) => `${o.tool}=${(o.blockShare * 100).toFixed(0)}%`).join(', ')} 超过 1% 阈值`)
  process.exit(1)
}
