// goalrelation.js —— 目标与关系的**插件侧实现**（对应 engine/goal.py、engine/relation.py）
//
// 与 Python 参考实现同一套规则，但更保守：
//   · 目标事件必须有逐字引用（evidenceQuote 必须是原文子串）；
//   · 单轮最多 3 条目标事件；
//   · 受阻 2 次 → abandoned；
//   · trust 永不自动升到 high（唯一通道是用户显式确认，插件里目前不自动开）；
//   · 冲突必须置 unresolvedConflict，且 modifier 上限 1.0；
//   · **只产出 writeRequests，不直接写记忆库**（记忆是唯一写方）。

const MAX_EVENTS_PER_TURN = 3;
const BLOCKED_LIMIT = 2;
const POSITIVES_TO_MID = 3;

// 意图识别（闭集，保守：宁可漏判也不误判成目标变更）
const GOAL_START = ['开始做', '接下来做', '目标是', '我要', '帮我做', '计划做', '先做'];
const GOAL_PROGRESS = ['做完了', '完成了', '搞定了', '已经', '进展', '推进'];
const GOAL_BLOCKED = ['卡住', '失败', '不行', '报错', '挡住', '走不通', '做不了'];
const GOAL_ABANDON = ['不做了', '放弃', '算了', '先搁置'];
const COMPLETE_EVIDENCE = ['通过', '成功', 'pass', 'ok', '已完成'];

const REL_POSITIVE = ['谢谢', '辛苦', '不错', '很好用', '可以', '赞'];
const REL_NEGATIVE = ['不对', '错了', '不满意', '不行', '差评'];
const REL_CONFLICT = ['你错了', '你搞错', '别再', '我不满意', '你在胡说', '敷衍'];
const REL_REPAIR = ['和好', '没关系了', '翻篇', '不怪你', '算了没事'];

const TRUST_LEVELS = ['low', 'mid', 'high'];

function pick(text, words) {
  for (const w of words) if (text.includes(w)) return w;
  return null;
}

function newGoalState() {
  return { goals: {}, order: [], events: [], seq: 0 };
}

function newRelationState() {
  return {
    trustLevel: 'low',
    stage: 'new',
    unresolvedConflict: false,
    positives: 0,
    commitments: [],
    lastEvent: null,
  };
}

// ── 目标 ────────────────────────────────────────────────
export function updateGoals(state, text, { turn = 0, at = Date.now(), evidence = null } = {}) {
  const s = state || newGoalState();
  const quote = String(text || '').trim();
  const out = { events: [], writeRequests: [], changed: false };
  if (!quote) return out;

  const events = [];
  const push = (event, goalId, extra = {}) => {
    if (events.length >= MAX_EVENTS_PER_TURN) return;
    events.push({ goalId, event, evidenceQuote: quote.slice(0, 80), ...extra });
  };

  // 新建目标
  const startWord = pick(quote, GOAL_START);
  if (startWord) {
    s.seq += 1;
    const id = 'goal_' + String(s.seq).padStart(3, '0');
    s.goals[id] = {
      id, content: quote.slice(0, 40), status: 'active', blocked: 0,
      createdAt: at, updatedAt: at, source: 'user_explicit',
    };
    s.order.push(id);
    push('new', id);
  }

  const activeId = s.order.filter((id) => s.goals[id]?.status === 'active').slice(-1)[0] || null;

  if (pick(quote, GOAL_BLOCKED) && activeId) {
    const g = s.goals[activeId];
    g.blocked += 1;
    g.updatedAt = at;
    push('obstruction', activeId, { blocked: g.blocked });
    if (g.blocked >= BLOCKED_LIMIT) {
      g.status = 'abandoned';
      push('abandon', activeId, { reason: 'blocked_limit' });
    }
  } else if (pick(quote, GOAL_ABANDON) && activeId) {
    s.goals[activeId].status = 'abandoned';
    push('abandon', activeId, { reason: 'user_abandon' });
  } else if (pick(quote, GOAL_PROGRESS) && activeId) {
    const done = Boolean(evidence) || Boolean(pick(quote, COMPLETE_EVIDENCE));
    if (done) {
      s.goals[activeId].status = 'done';
      push('complete', activeId, { evidence: evidence || 'inline' });
    } else {
      push('progress', activeId);          // 无证据只记进展，不判完成
    }
  }

  if (!events.length) return out;
  s.events.push(...events.map((e) => ({ ...e, turn, at })));
  out.events = events;
  out.changed = true;
  out.writeRequests = [{
    origin: 'goal',
    items: events.map((e) => ({
      type: 'procedural', subject: '目标', relation: e.goalId || '未命名',
      object: e.event, content: `目标 ${e.goalId} 发生 ${e.event} 事件`,
      sourceQuote: e.evidenceQuote, sourceTrust: 'user_explicit', importance: 0.8,
    })),
    reason: '目标变更',
  }];
  out.state = s;
  return out;
}

// ── 关系 ────────────────────────────────────────────────
export function updateRelation(state, text, { at = Date.now() } = {}) {
  const s = state || newRelationState();
  const quote = String(text || '').trim();
  const out = { events: [], stateChanged: false, trustDelta: 'none', modifier: 1.0 };
  if (!quote) {
    out.modifier = relationModifier(s);
    return out;
  }

  const conflict = pick(quote, REL_CONFLICT);
  const repair = pick(quote, REL_REPAIR);
  const negative = conflict ? null : pick(quote, REL_NEGATIVE);
  const positive = conflict || negative ? null : pick(quote, REL_POSITIVE);

  if (conflict) {
    s.unresolvedConflict = true;
    s.stage = 'conflict';
    out.events.push({ type: 'conflict', evidenceQuote: conflict });
  } else if (repair) {
    s.unresolvedConflict = false;
    s.stage = 'repaired';
    out.events.push({ type: 'repair', evidenceQuote: repair });
  } else if (negative) {
    out.events.push({ type: 'negative', evidenceQuote: negative });
  } else if (positive) {
    s.positives += 1;
    out.events.push({ type: 'positive', evidenceQuote: positive });
    if (s.positives >= POSITIVES_TO_MID && s.trustLevel === 'low') {
      s.trustLevel = 'mid';                // 只到 mid，永不自动升 high
      out.trustDelta = 'up1';
    }
  }

  if (!out.events.length) {
    out.modifier = relationModifier(s);
    return out;
  }
  s.lastEvent = { type: out.events[0].type, at };
  out.stateChanged = true;
  out.modifier = relationModifier(s);
  out.state = s;
  out.writeRequests = [{
    origin: 'relation',
    items: [{
      type: 'episodic', subject: '关系', relation: s.stage,
      object: out.events[0].type, content: `关系事件：${out.events[0].type}`,
      sourceQuote: quote.slice(0, 80), sourceTrust: 'user_explicit', importance: 0.7,
    }],
    reason: '关系事件',
  }];
  return out;
}

export function relationModifier(s) {
  if (!s) return 1.0;
  const base = { low: 0.9, mid: 1.0, high: 1.1 }[s.trustLevel] || 0.9;
  let m = base;
  if (s.unresolvedConflict) m = Math.min(m, 1.0);      // 冲突期不加码
  return Math.round(Math.max(0.8, Math.min(1.2, m)) * 1000) / 1000;
}

export function activeGoal(s) {
  if (!s || !s.goals) return null;
  const ids = (s.order || []).filter((id) => s.goals[id]?.status === 'active');
  return ids.length ? s.goals[ids[ids.length - 1]] : null;
}

export function renderGoalBlock(s) {
  const g = activeGoal(s);
  if (!g) return '';
  const lines = [`〖当前目标〗${g.content}`];
  if (g.blocked) lines.push(`阻塞：已尝试 ${g.blocked} 次`);
  return lines.join('\n');
}

export function renderRelationBlock(s) {
  if (!s) return '';
  if (!s.unresolvedConflict && s.stage === 'new') return '';
  const lines = [`〖关系状态〗阶段: ${s.stage}`];
  if (s.unresolvedConflict) lines.push('存在未修复冲突：表达收敛，不假装无事，但不影响事实与结论');
  const overdue = (s.commitments || []).filter((c) => !c.fulfilled);
  if (overdue.length) lines.push('有未兑现承诺需说明');
  return lines.join('\n');
}

export const GOAL_STATE_EMPTY = newGoalState;
export const RELATION_STATE_EMPTY = newRelationState;
