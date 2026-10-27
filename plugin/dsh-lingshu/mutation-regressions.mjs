// mutation-regressions.mjs —— A4 变异测试补充的回归断言（2026-10-27）
//
// 背景：对 control.js / memorywriter.js / index.js 植入 41 个语义变异后，有 4 个
// **真实**变异存活（另有 2 个在合法参数域内等价，见报告）。这里为 4 个盲区各写最小
// 回归断言，全部直接调用被测实现、不复制业务逻辑；失败 exit 1，成功打印 PASS 行。
//
// 用法：node mutation-regressions.mjs        （退出码 0 = 全通过，1 = 有失败）
//
// 对应变异（详见 acceptance/audit/变异测试报告-2026-10-27.md）：
//   MW-04  同一批两条新值取代同一旧值 → 旧值只允许写一条墓碑（工作副本失效）
//   MW-05  同批"先阿尔法后贝塔" → 被取代的新条目 last-wins 后必须是 superseded
//   IDX-03 冲突判定必须忽略非 active 条目（superseded 不得制造 false unresolved）
//   IDX-05 quality_label 规则逐词钉住（原测试全体标签用例都同时命中第二关键词，
//          漏掉任一单词都能全绿 —— '不对' 单独出现时必须判 wrong）
import { applyWriteRequests } from './memorywriter.js';
import { detectConflicts, detectQualityLabel } from './index.js';

const results = [];
function check(label, cond, detail = '') {
  results.push({ label, cond: !!cond });
  console.log(`${cond ? '  ok   ' : '  FAIL '}${label}${detail ? '  — ' + detail : ''}`);
}

// ── MW-04：工作副本失效，避免同一旧条目写两条墓碑 ──────────────
const seed = [{
  id: 'mem_001', type: 'semantic', subject: '项目', relation: '代号', object: '阿尔法',
  content: '项目是阿尔法', importance: 0.9, confidence: 0.9, layer: 'warm',
  status: 'active', lastAccess: 1, tauDays: 30, sourceQuote: '项目是阿尔法',
}];
const store = {
  lines: seed.map((e) => ({ ...e })),
  read() { return this.lines.map((e) => ({ ...e })); },
  append(batch) { this.lines.push(...batch.map((e) => ({ ...e }))); },
};
const mkItem = (object, quote) => ({
  type: 'semantic', subject: '项目', relation: '代号', object,
  content: '项目是' + object, importance: 0.9, sourceQuote: quote,
});
// 同一批：贝塔 取代 阿尔法，伽马 又取代 贝塔 —— 旧值 阿尔法 只允许一条墓碑。
const batch = applyWriteRequests([{
  origin: 'user_fact',
  items: [mkItem('贝塔', '我改主意了，项目是贝塔'), mkItem('伽马', '我改主意了，项目是伽马')],
}], store, { now: 1000 });

const alphaTombs = batch.superseded.filter((s) => s.id === 'mem_001');
check('MW-04 旧值只被记为一次 superseded', alphaTombs.length === 1,
  `superseded 中 mem_001 出现 ${alphaTombs.length} 次`);
const alphaRows = store.lines.filter((e) => e.id === 'mem_001' && e.status === 'superseded');
check('MW-04 旧值只落一条墓碑行', alphaRows.length === 1, `落盘墓碑 ${alphaRows.length} 行`);

// ── MW-05：批内 later-wins 顺序（墓碑必须排在新条目之后）─────────
const byId = new Map();
for (const e of store.lines) byId.set(String(e.id), e);   // 读取侧语义：同 id 后写覆盖
const betaLast = byId.get('mem_002');
check('MW-05 批内被取代的新条目 last-wins 后是 superseded',
  betaLast && betaLast.status === 'superseded',
  `mem_002 last-wins status=${betaLast && betaLast.status}`);
check('MW-05 最后一条新条目仍是 active',
  byId.get('mem_003') && byId.get('mem_003').status === 'active',
  `mem_003 status=${byId.get('mem_003') && byId.get('mem_003').status}`);

// ── IDX-03：冲突判定只认 active 条目 ───────────────────────────
const conflictEntries = [
  { id: 'a', status: 'active', subject: '项目', relation: '代号', object: '阿尔法' },
  { id: 'c', status: 'superseded', subject: '项目', relation: '代号', object: '伽马' },
];
const conflicts = detectConflicts(conflictEntries, [{ id: 'a' }, { id: 'c' }]);
check('IDX-03 superseded 条目不算冲突（不制造 false unresolved）',
  conflicts.length === 0, JSON.stringify(conflicts));
const conflictActive = detectConflicts([
  { id: 'a', status: 'active', subject: '项目', relation: '代号', object: '阿尔法' },
  { id: 'b', status: 'active', subject: '项目', relation: '代号', object: '贝塔' },
], [{ id: 'a' }, { id: 'b' }]);
check('IDX-03 两条 active 不同 object 仍判冲突（对照）',
  conflictActive.length === 1 && conflictActive[0].objects.length === 2,
  JSON.stringify(conflictActive));

// ── IDX-05：quality_label 五类逐词钉住 ─────────────────────────
const labelCases = [
  ['你说的不对', 'wrong'], ['这次错了', 'wrong'],
  ['你漏了上次的约定', 'missing'], ['你又忘了', 'missing'],
  ['这个别提了', 'should_not_recall'],
  ['你又来了', 'drift'],
  ['你凭什么替我做决定', 'overreach'],
];
for (const [text, kind] of labelCases) {
  const got = detectQualityLabel(text);
  check(`IDX-05 quality_label 逐词命中：${text} → ${kind}`,
    got && got.kind === kind && text.includes(got.quote), JSON.stringify(got));
}
check("IDX-05 '不对' 单独出现必须判 wrong",
  (() => { const g = detectQualityLabel('你说的不对'); return g && g.kind === 'wrong' && g.quote === '不对'; })());
check('IDX-05 普通消息不产生标签', detectQualityLabel('今天天气不错') === null);

const failed = results.filter((r) => !r.cond);
console.log(`\nPASS: ${results.length - failed.length} ｜ FAIL: ${failed.length} ｜ 断言总数: ${results.length}`);
if (failed.length) {
  for (const f of failed) console.log('  FAIL  ' + f.label);
  process.exit(1);
}
process.exit(0);
