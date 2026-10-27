// memorywriter.js —— 记忆写入的唯一入口（不变式 I5）
//
// 为什么单独一个文件：目标/关系/情感都**不能**直接写记忆库，
// 它们只能产出 writeRequests；由这里统一校验、编号、落盘。
// 这样"记忆是唯一写方"才是代码事实，而不是文档承诺。
//
// 拒绝规则（宁可不写，也不写脏）：
//   · 缺 sourceQuote / 空引用      → reject：无法追溯
//   · importance 不在 [0,1]       → reject：排序会失真
//   · type 不在闭集               → reject：层归属不定
//   · 同一 turn 内同 (subject, relation, object) 重复 → 丢弃，避免自我强化

const TYPES = ['episodic', 'semantic', 'procedural', 'structural', 'profile'];

function maxSeq(entries) {
  let n = 0;
  for (const e of entries) {
    const m = /^mem_(\d+)$/.exec(String(e.id || ''));
    if (m) n = Math.max(n, Number(m[1]));
  }
  return n;
}

export function validateItem(item) {
  const why = [];
  if (!TYPES.includes(item?.type)) why.push('bad_type');
  const q = String(item?.sourceQuote ?? '').trim();
  if (!q) why.push('missing_source_quote');
  const imp = Number(item?.importance ?? NaN);
  if (!Number.isFinite(imp) || imp < 0 || imp > 1) why.push('bad_importance');
  if (!String(item?.content ?? '').trim()) why.push('empty_content');
  return why;
}

/**
 * 把 writeRequests 落成记忆条目。
 *
 * v5.1（ADR-012 §2.2）新增 supersede：同 `(subject, relation)`、不同 `object`、`status="active"`
 * 的旧条目会被追加一条**同 id 墓碑**（status=superseded + supersededBy），读取侧按 id last-wins
 * 覆盖（与 memory-admin 的清理墓碑同一机制）；JSONL 仍然只追加、不改历史行。
 *
 * 为什么墓碑而不是原地改写：记忆库是 append-only 的可审计账本，原地改行会让"旧值曾经 active"
 * 这个事实消失，事后无法回答"用户改口前系统信的是什么"。
 *
 * `opts.supersede === false` 只给"导入/历史数据预置"用（混沌 S4 setup，见 ADR-012 §2.5）；
 * 事件流水类 origin（goal/relation/tool）在自己的 request 上标 `supersede:false`，
 * 因为它们是"发生了什么"的日志，不是"事实是什么"的断言，不该互相覆盖。
 *
 * @param {object[]} requests  形如 [{origin, items:[...], reason, supersede?:boolean}]
 * @param {{read:()=>object[], append:(obj[])=>void, now?:number}} store
 * @param {{now?:number, supersede?:boolean}} [opts]
 * @returns {{written:number, ids:string[], rejected:object[],
 *            superseded:{id:string, object:string, byId:string, subject:string, relation:string}[]}}
 */
export function applyWriteRequests(requests, store, { now = Date.now(), supersede = true } = {}) {
  const out = { written: 0, ids: [], rejected: [], superseded: [] };
  const existing = store.read();
  let seq = maxSeq(existing);
  const batch = new Set();
  const fresh = [];
  const tombstones = [];
  // 工作副本：批内先写入的条目也要参与后续 supersede 扫描，否则同一批里的"先阿尔法后贝塔"
  // 会留下两条 active（批内重复只按同 object 拒绝，不按同键拒绝）。
  const working = existing.map((e) => ({ ...e }));

  for (const req of requests || []) {
    const allowSupersede = req?.supersede === false ? false : supersede !== false;
    for (const item of req?.items || []) {
      const why = validateItem(item);
      if (why.length) {
        out.rejected.push({ origin: req.origin, reason: why.join(','), content: String(item?.content ?? '').slice(0, 40) });
        continue;
      }
      const key = [item.subject, item.relation, item.object].join('|');
      if (batch.has(key)) {
        out.rejected.push({ origin: req.origin, reason: 'duplicate_in_batch', content: String(item.content).slice(0, 40) });
        continue;
      }
      batch.add(key);
      seq += 1;
      const id = 'mem_' + String(seq).padStart(3, '0');
      const entry = {
        id,
        type: item.type,
        subject: item.subject ?? '',
        relation: item.relation ?? '',
        object: item.object ?? '',
        content: String(item.content).slice(0, 200),
        importance: Number(item.importance),
        confidence: 0.9,
        layer: 'warm',
        status: 'active',
        sourceQuote: String(item.sourceQuote).slice(0, 120),
        sourceTrust: item.sourceTrust ?? 'user_explicit',
        origin: req.origin ?? 'unknown',
        turn: req.turn ?? null,
        lastAccess: now,
        tauDays: 30,
      };

      if (allowSupersede) {
        // 倒序扫描：同键多条 active（历史脏数据/导入）一次全部取代，而不是只取代第一条。
        for (let i = working.length - 1; i >= 0; i--) {
          const e = working[i];
          if (String(e.id) === id) continue;
          if (e.status !== 'active') continue;
          if (String(e.subject ?? '') !== String(entry.subject)) continue;
          if (String(e.relation ?? '') !== String(entry.relation)) continue;
          if (String(e.object ?? '') === String(entry.object)) continue;
          tombstones.push({
            ...e,
            status: 'superseded',
            supersededBy: id,
            supersededAt: now,
            supersededReason: 'same (subject, relation), different object',
          });
          e.status = 'superseded';       // 工作副本立即失效，避免同一旧条目写两条墓碑
          out.superseded.push({ id: e.id, object: e.object, byId: id,
                                subject: entry.subject, relation: entry.relation });
        }
      }

      fresh.push(entry);
      working.push({ ...entry });
      out.ids.push(id);
    }
  }

  // 墓碑排在新增条目之后：同批被取代的新条目也能 last-wins 生效（读取侧按 id 后写覆盖）。
  if (fresh.length || tombstones.length) {
    store.append(fresh.concat(tombstones));
    out.written = fresh.length;
  }
  return out;
}

export { TYPES };

// ── 真实数据清理（v4.3）：仍然只能走这里 ─────────────────
//
// 真实流量一定会产生脏数据（测试记忆、错误事实、隐私内容）。规定：**清理也只能由
// memorywriter 执行**，不许直接改 memory.jsonl。做法是追加一条**同 id 的墓碑记录**
// （status=suppressed + cleanup 溯源），读取侧按 id 后写覆盖（见 index.js#readMemories），
// 这样既保持 JSONL 只追加、可审计，又不破坏"唯一写方"。
const CLEANUP_REASONS = ['test_data', 'wrong_fact', 'privacy', 'superseded_by_user', 'manual_review'];

/**
 * 把清理请求落成墓碑记录。
 * @param {object[]} requests [{id, reason, sourceQuote, operator?}]
 * @param {{read:()=>object[], append:(obj[])=>void}} store
 * @returns {{cleaned:number, ids:string[], rejected:object[]}}
 */
export function applyCleanupRequests(requests, store, { now = Date.now(), operator = 'operator' } = {}) {
  const out = { cleaned: 0, ids: [], rejected: [] };
  const existing = store.read();
  const byId = new Map();
  for (const e of existing) byId.set(String(e.id), e);
  const batch = new Set();
  const tombstones = [];
  for (const req of requests || []) {
    const id = String(req?.id ?? '');
    const why = [];
    if (!CLEANUP_REASONS.includes(req?.reason)) why.push('bad_reason');
    if (!String(req?.sourceQuote ?? '').trim()) why.push('missing_source_quote');
    const orig = byId.get(id);
    if (!orig) why.push('unknown_id');
    else if (orig.status !== 'active') why.push('not_active');
    if (batch.has(id)) why.push('duplicate_in_batch');
    if (why.length) {
      out.rejected.push({ id, reason: why.join(','), content: String(orig?.content ?? '').slice(0, 40) });
      continue;
    }
    batch.add(id);
    tombstones.push({
      ...orig,
      status: 'suppressed',
      lastAccess: now,
      cleanup: {
        reason: req.reason,
        sourceQuote: String(req.sourceQuote).slice(0, 120),
        by: String(req.operator || operator).slice(0, 40),
        at: now,
      },
    });
    out.ids.push(id);
  }
  if (tombstones.length) {
    store.append(tombstones);
    out.cleaned = tombstones.length;
  }
  return out;
}

export { CLEANUP_REASONS };
