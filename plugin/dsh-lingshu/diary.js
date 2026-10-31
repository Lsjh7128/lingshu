// diary.js —— LINGSHU 日记本
//
// 设计边界（照抄社区里踩过的坑，见 research/ 里的 README）：
//   · 记忆管"不变的"，日记管"当天怎么过的"，心情管"现在我是什么状态" —— 三者不互相写。
//     所以日记**不进记忆库**，只落 personas/<pid>/diary/YYYY-MM-DD.md。
//   · 素材 → 成文 两步：到点那一刻宿主不一定有活跃会话，拿不到模型，
//     所以只从磁盘上已有的痕迹（audit.jsonl / state.json / 本轮新增记忆）合成，不调模型。
//   · 不写统计数字（"说了 13 轮话"），写画面。
//   · 文风跟着当天主导心情走：疲惫就砍句，烦躁就短，低落就两段收尾。
//   · 只追加、不改写；模型侧没有任何代笔入口。

import fs from 'node:fs';
import path from 'node:path';

export const DIARY_DIRNAME = 'diary';

/** 人物目录下放日记：personas/<pid>/diary/*.md */
export function diaryDirOf(personaDir) {
  return path.join(String(personaDir), DIARY_DIRNAME);
}

/** 从记忆路径推日记目录（v0.2：personas/<pid>/global_memory.jsonl 的同级）。 */
export function diaryDirFromPaths(paths) {
  if (!paths || !paths.globalMemory) return null;
  return diaryDirOf(path.dirname(String(paths.globalMemory)));
}

export function dayKey(ts = Date.now()) {
  const d = new Date(Number(ts));
  const p = (n) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

const WEEK_CN = ['周日', '周一', '周二', '周三', '周四', '周五', '周六'];
export function weekdayOf(key) {
  const d = new Date(`${key}T12:00:00`);
  return Number.isNaN(d.getTime()) ? '' : WEEK_CN[d.getDay()];
}

function pad(n) { return String(n).padStart(2, '0'); }
function hourOf(ts) { return new Date(Number(ts)).getHours(); }

/** 把一天的时间跨度说成人话，不报点钟。整句接在「{星期}这天，」后面。 */
function spanPhrase(firstAt, lastAt) {
  if (!firstAt || !lastAt) return '';
  const h0 = hourOf(firstAt);
  const h1 = hourOf(lastAt);
  const bucket = (h) => (h < 6 ? '夜里' : h < 11 ? '早上' : h < 14 ? '中午' : h < 18 ? '下午' : h < 23 ? '晚上' : '深夜');
  if (h0 === h1) return `差不多整个${bucket(h0)}都在忙`;
  return `从${bucket(h0)}一直忙到${bucket(h1)}`;
}

// ── 心情 → 文风 ────────────────────────────────────────
// 段数、拖尾、收尾句。这是整套东西里最像"人"的一层。
const VOICE = {
  平静: { paras: 4, tail: '没什么起伏，也挺好。' },
  愉悦: { paras: 3, tail: '今天就到这儿，明天接着来。' },
  振奋: { paras: 4, tail: '还想再弄一会儿。' },
  不满: { paras: 2, tail: '算了。' },
  低落: { paras: 2, tail: '不说了。' },
};

const UNKNOWN_VOICE = { paras: 3, tail: '记一笔，明天再说。' };

function voiceOf(mood) { return VOICE[mood] || UNKNOWN_VOICE; }

/**
 * 从一天的痕迹里提炼"素材"。全部来自磁盘，不读用户原文。
 * @param {{auditEvents?: object[], memoryEvents?: object[]}} input
 */
export function collectMaterials({ auditEvents = [], memoryEvents = [] } = {}) {
  // audit 用 ts，state/记忆有时用 at —— 两个都认，别再踩一次。
  const tsOf = (e) => Number(e && (e.ts ?? e.at));
  const events = auditEvents.filter((e) => Number.isFinite(tsOf(e)));
  const mems = memoryEvents.filter((e) => Number.isFinite(tsOf(e)));
  const kinds = {};
  for (const e of events) {
    const k = String(e.kind || 'unknown');
    kinds[k] = (kinds[k] || 0) + 1;
  }
  // 轮次才是"忙不忙"的单位；pre_step / tool_call 这种每步一条会把数字灌爆。
  const turns = events.filter((e) => e.kind === 'turn_begin' || e.kind === 'turn_end_seen'
    || (e.kind === 'pre_step' && e.firstStep === true));
  // 真正的拦截：tool_gate 的 verdict=block，或前置熔断。
  const blocked = events.filter((e) => (e.kind === 'tool_gate' && String(e.verdict) === 'block')
    || e.kind === 'preflight_block' || e.kind === 'tool_gateway_blocked').length;
  const activity = turns.length || events.length;
  // 时间跨度只看轮次/记忆，别让半夜的一次 apply 把跨度拉到全天。
  const focus = turns.length ? turns : events;
  const times = focus.map(tsOf).concat(mems.map(tsOf)).sort((a, b) => a - b);
  return {
    activity,
    blocked,
    memoryAdded: mems.length,
    kinds,
    firstAt: times.length ? times[0] : null,
    lastAt: times.length ? times[times.length - 1] : null,
  };
}

/** 把素材写成第一人称正文。返回 {text, summary}。 */
export function composeDiary({ personaName = '', date, mood = '中性', materials = {} } = {}) {
  const voice = voiceOf(mood);
  const m = materials;
  const today = weekdayOf(date);
  const paras = [];

  // 1) 时间感
  const span = spanPhrase(m.firstAt, m.lastAt);
  if (!m.activity) {
    paras.push(`${today}这天基本是空的，没什么事发生。`);
  } else if (span) {
    paras.push(`${today}这天，${span}。`);
  } else {
    paras.push(`${today}这天照常。`);
  }

  // 2) 干了什么（用事件密度说事，不报数字）
  //    同一份素材，心情好和心情差写出来不能一个调子 —— 否则正文会跟心情打架。
  const positive = mood === '愉悦' || mood === '振奋';
  const busy = m.activity >= 40 ? '大半时间都在' : m.activity >= 12 ? '陆陆续续' : '零零碎碎';
  if (m.blocked >= 8) {
    paras.push(positive
      ? `${busy}跟工具较劲。卡了好几回，倒也不算什么事，最后都绕过去了。`
      : `${busy}跟工具较劲。一次接一次地没跑通，改一版试一版，火气是这么一点点上来的。`);
  } else if (m.blocked >= 2) {
    paras.push(positive
      ? `${busy}忙手上的活，中间卡了两回，换个法子就通了。`
      : `${busy}忙手上的活，中间卡过几回，绕一下才继续。`);
  } else if (m.activity >= 12) {
    paras.push(`${busy}做事，节奏还算顺，没怎么被打断。`);
  }

  // 3) 记了什么
  if (m.memoryAdded >= 3) {
    paras.push('顺手攒下几件事，都是怕以后想不起来的。写完心里踏实一点。');
  } else if (m.memoryAdded > 0) {
    paras.push('记下了一条。不多，但那条值得留。');
  } else if (m.activity >= 12) {
    paras.push('今天什么也没特意记，过了就过了。');
  }

  // 4) 心情那一层（只说感受，不解释原因，也不替对方编）
  const feel = {
    平静: '心里是平的，不着急也不烦躁。',
    愉悦: '有几次是真的笑出来了，虽然也说不清为什么。',
    振奋: '手上的事越做越顺，有点停不下来。',
    不满: '有些话说出来也不会改变什么，就没说。',
    低落: '不太想说话，回什么都嫌费劲。',
  }[mood];
  if (feel && paras.length < voice.paras) paras.push(feel);

  const body = paras.slice(0, Math.max(2, voice.paras)).join('\n\n');
  const text = `${body}\n\n${voice.tail}`;
  const summary = buildSummary({ mood, materials: m, tail: voice.tail });

  return { text: personaName ? text : text, summary };
}

function buildSummary({ mood, materials: m, tail }) {
  const bits = [];
  if (m.activity >= 40) bits.push('忙了一整天');
  else if (m.activity >= 12) bits.push('断断续续在做事');
  else if (m.activity > 0) bits.push('没干什么大事');
  else bits.push('一天是空的');
  if (m.blocked >= 8) bits.push('工具老卡');
  else if (m.memoryAdded > 0) bits.push('记下了几件事');
  bits.push(`心情${mood}`);
  return `${bits.join('，')}。${tail}`.slice(0, 120);
}

// ── 落盘 ───────────────────────────────────────────────

function metaLine(meta) {
  return `<!-- lingshu-diary ${JSON.stringify(meta)} -->`;
}

export function diaryFileOf(dir, date) {
  return path.join(dir, `${date}.md`);
}

/** 已存在就不覆盖（日记是流水账，只增不改）。 */
export function writeDiary(dir, { date, personaName, mood, text, summary }) {
  try {
    fs.mkdirSync(dir, { recursive: true });
    const file = diaryFileOf(dir, date);
    if (fs.existsSync(file)) return { ok: true, created: false, file };
    const head = `# ${personaName || '我'}的日记 · ${date} ${weekdayOf(date)}\n\n> 心情：${mood}\n\n`;
    const body = `${head}${text}\n\n${metaLine({ date, mood, summary })}\n`;
    const tmp = `${file}.tmp-${process.pid}`;
    fs.writeFileSync(tmp, body, 'utf8');
    fs.renameSync(tmp, file);
    return { ok: true, created: true, file };
  } catch (err) {
    return { ok: false, created: false, error: String(err && err.code || err) };
  }
}

/** 读索引（只读头部，不整篇塞进内存）。 */
export function listDiaryIndex(dir) {
  const out = [];
  let names = [];
  try { names = fs.readdirSync(dir).filter((n) => /^\d{4}-\d{2}-\d{2}\.md$/.test(n)); } catch { return out; }
  for (const name of names) {
    const date = name.slice(0, 10);
    let text = '';
    try { text = fs.readFileSync(path.join(dir, name), 'utf8'); } catch { continue; }
    const mood = (/^> 心情：(.+)$/m.exec(text) || [])[1] || '中性';
    const meta = (/(<!-- lingshu-diary (\{.*?\}) -->)/.exec(text) || [])[2];
    let summary = '';
    try { summary = meta ? String(JSON.parse(meta).summary || '') : ''; } catch { summary = ''; }
    if (!summary) summary = text.split('\n').filter((l) => l && !l.startsWith('#') && !l.startsWith('>')
      && !l.startsWith('<!--'))[0] || '';
    out.push({ date, weekday: weekdayOf(date), mood: String(mood).trim(), summary, chars: text.length });
  }
  out.sort((a, b) => (a.date < b.date ? 1 : a.date > b.date ? -1 : 0));   // 新 → 旧
  return out;
}

export function readDiaryText(dir, date) {
  try {
    const file = diaryFileOf(dir, String(date || ''));
    if (!/^\d{4}-\d{2}-\d{2}$/.test(String(date || ''))) return null;
    return fs.readFileSync(file, 'utf8');
  } catch { return null; }
}

/** 连续记录天数（从今天或昨天往前数）。 */
export function streakOf(index, today) {
  const set = new Set(index.map((e) => e.date));
  const d = new Date(`${today}T12:00:00`);
  let n = 0;
  // 今天没写不算断：从昨天起算。
  if (!set.has(today)) d.setDate(d.getDate() - 1);
  while (set.has(dayKey(d.getTime()))) {
    n += 1;
    d.setDate(d.getDate() - 1);
  }
  return n;
}

/** 给 prompt 用的"昨天那篇"摘要；没有就返回空串。 */
export function yesterdaySummary(dir, today) {
  const d = new Date(`${today}T12:00:00`);
  d.setDate(d.getDate() - 1);
  const key = dayKey(d.getTime());
  const hit = listDiaryIndex(dir).find((e) => e.date === key);
  if (!hit) return '';
  return `〖日记 · 昨天 ${key}〗${hit.summary}`;
}
