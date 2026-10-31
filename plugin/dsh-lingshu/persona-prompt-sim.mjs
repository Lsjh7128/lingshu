import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import assert from 'node:assert/strict'

const home = fs.mkdtempSync(path.join(os.tmpdir(), 'lingshu-persona-prompt-'))
const dataDir = path.join(home, 'data', 'workspace_qq')
fs.mkdirSync(dataDir, { recursive: true })
fs.writeFileSync(path.join(dataDir, 'persona.json'), JSON.stringify({
  schema_version: 'persona-0.1',
  persona_id: 'pe_qq',
  name: '鲸鲸',
  summary: '说话温和、简洁，先给结论，偶尔用一个比喻。',
  traits: {
    formality: 0.25,
    warmth: 0.9,
    proactiveness: 0.4,
    humor: 0.7,
    boundary_firmness: 0.8,
  },
  confirmed_by_user: true,
  confirmed_at: new Date().toISOString(),
}, null, 2))

process.env.DSH_HOME = home
process.env.LINGSHU_DATA_DIR = path.join(home, 'data')
process.env.LINGSHU_WORKSPACE = 'qq'

const { apply } = await import('./index.js')
const sections = []
const ctx = {
  tools: { register: () => {} },
  systemPrompt: { section: (s) => sections.push(s) },
  on: () => {},
  get: () => { throw new Error('no inject') },
  effect: (fn) => { try { return fn() } catch {} },
  inject: () => {},
}
apply(ctx, {})
const style = sections.find((s) => s.name === 'lingshu:style')
assert.ok(style, 'style section registered')
const text = style.text()
assert.match(text, /〖人格体 · 影响表达与思考链〗/)
assert.match(text, /连思考链一起对齐/)
assert.match(text, /鲸鲸/)
assert.match(text, /风格摘要/)
assert.match(text, /正式度 0.25（口语化）/)
assert.match(text, /温度 0.90（热络）/)
assert.match(text, /边界强度 0.80（边界强硬）/)
assert.match(text, /不得改变事实、安全判断、工具权限或拒绝边界/)

// 改文件后同一进程下一条消息应重读，无需重启。
fs.writeFileSync(path.join(dataDir, 'persona.json'), JSON.stringify({
  schema_version: 'persona-0.1',
  persona_id: 'pe_qq',
  name: '鲸鲸·改',
  summary: '更新后的风格摘要。',
  traits: { formality: 0.9, warmth: 0.2, proactiveness: 0.8, humor: 0.1, boundary_firmness: 0.5 },
  confirmed_by_user: true,
  confirmed_at: new Date().toISOString(),
}, null, 2))
const text2 = style.text()
assert.match(text2, /鲸鲸·改/)
assert.match(text2, /更新后的风格摘要/)
assert.match(text2, /正式度 0.90（正式）/)

// 非当前工作区（legacy 目录）的 persona 不应进入 prompt。
fs.rmSync(path.join(dataDir, 'persona.json'))
const text3 = style.text()
assert.doesNotMatch(text3, /〖人格体 · 影响表达与思考链〗/)
console.log('[ persona-prompt ] PASS')
console.log(text)
fs.rmSync(home, { recursive: true, force: true })
