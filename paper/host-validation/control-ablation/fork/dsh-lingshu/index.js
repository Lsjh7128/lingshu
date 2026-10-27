// dsh-lingshu —— LINGSHU 的 DSH 插件骨架
//
// 它只做四件事，但每件都是端到端的：
//   1) 用 ctx.systemPrompt.section 注入 4 段（宪法/事实/内容组织/风格），按 order 排序；
//   2) 在 agent/pre-step 里做召回 + 元认知定档（纯规则，零模型调用）；
//   3) 在 turn/end 里推进轮次与情感衰减；
//   4) 提供 lingshu_status 工具（只读，自检用）。
//
// 两条硬约束（来自 docs/09 §4.1 与 docs/00 §4，都是踩出来的）：
//   · section 的 text() **只能返回字符串**，不注入时返回 ""，绝不返回 null（旧版宿主会让整轮对话失败）；
//   · `_frozen` 自校验失败 → 返回 ""（不注入），**不允许"照旧注入 + 只记日志"**。
//
// 设计红线：任何路径都不得抛异常。插件把宿主搞挂就失去意义了。

import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import crypto from 'node:crypto';
import { applyWriteRequests } from './memorywriter.js';
import {
  GOAL_STATE_EMPTY, RELATION_STATE_EMPTY, activeGoal, relationModifier,
  renderGoalBlock, renderRelationBlock, updateGoals, updateRelation,
} from './goalrelation.js';
import {
  ANCHOR_PREFIX, ANCHOR_SUFFIX, DEFAULT_PARAMS as CONTROL_DEFAULTS,
  IntegralBaseline, PhaseShiftMonitor, RequisiteVarietyGuard, preflightAudit, uShape,
} from './control.js';

export const name = 'dsh-lingshu';
export const inject = ['tools', 'systemPrompt'];

// order 90/140 是"首尾人格锚点"，105 是前置审计熔断提示；100/110/120/130 是原有四段。
// ── LAB-ONLY INSTRUMENTATION (S3 control ablation) ──
// 环境开关：LINGSHU_CONTROL=off 只关 anchor / gate / variety，保留记忆注入与状态落盘。
// 产品插件（plugin/dsh-lingshu）不包含本段；本文件仅存于 paper/host-validation/control-ablation/fork/。
const LINGSHU_CONTROL_OFF = String(process.env.LINGSHU_CONTROL || '').toLowerCase() === 'off';

const SECTION_ORDER = { anchorHead: 90, constitution: 100, blocked: 105,
                        fact: 110, organization: 120, style: 130, anchorTail: 140 };
const DEFAULT_CONFIG = {
  coldTurns: 3,
  warmTurns: 20,
  warmConfidenceFloor: 0.7,
  archiveThreshold: 0.15,
  topK: 3,
  tokenBudgetTokens: 900,
  emotionTauMinutes: { valence: 45, arousal: 8, dominance: 90 },
  frozen: {},                       // { "rules/compose-matrix.yaml": "<sha256>" }
  frozenRoot: '',
  degradedMaxChars: 400,
  // 控制论层（v4.2 从 engine/control.py 移植）
  contextWindowTokens: 1048576,     // 宿主没给 request/context 时的缺省窗口
  charsPerToken: 3,                 // 上下文用量的启发式估算（与 engine 一致：估算，不是精确计费）
  integralApply: false,             // 默认只观测积分基线，不叠加到表达（不改既有行为）
  // 混沌工程用：临时把"相变窗口"钉到一个更小的值，让 70%/85% 在压力测试里可达。
  // 仅由 chaos runner 通过 LINGSHU_CONTEXT_WINDOW 环境变量注入；正常使用不设。
  contextWindowOverride: 0,
  // 影子模式（v4.3，真实标定阶段）：目标/关系误报率过高，先"只记录不注入"。
  // 记录链路（updateGoals/updateRelation/memorywriter）完全不变，只是不进 prompt。
  shadowGoal: false,
  shadowRelation: false,
  control: { ...CONTROL_DEFAULTS },
};

// ── 工具函数 ─────────────────────────────────────────────
function home() {
  const h = process.env.DSH_HOME;
  return h && String(h).trim() ? String(h).trim() : path.join(os.homedir(), '.dsh');
}

function stateDir() {
  // LINGSHU_DATA_DIR：混沌工程/标定用**数据目录隔离**。真实流量与自动压测的日志不能混在一起，
  // 否则标定样本会被测试噪音污染。缺省仍是 $DSH_HOME/plugins/dsh-lingshu（正常使用路径）。
  const override = process.env.LINGSHU_DATA_DIR;
  if (override && String(override).trim()) return path.resolve(String(override).trim());
  return path.join(home(), 'plugins', name);
}

function ensureDir(p) {
  try { fs.mkdirSync(p, { recursive: true }); } catch { /* 忽略 */ }
}

function appendJsonl(file, rec) {
  try {
    ensureDir(path.dirname(file));
    fs.appendFileSync(file, JSON.stringify(rec) + '\n', 'utf8');
  } catch { /* 日志写失败绝不冒泡 */ }
}

function readJsonl(file) {
  try {
    const raw = fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, '');
    return raw.split('\n').map((l) => l.replace(/^\uFEFF/, '')).filter(Boolean)
      .map((l) => { try { return JSON.parse(l); } catch { return null; } })
      .filter(Boolean);
  } catch { return []; }
}

// 记忆读取：JSONL 只追加，**同 id 后写覆盖**。v4.3 起 memorywriter 清理脏数据时追加
// "墓碑记录"（同 id、status=suppressed），必须由这里做 last-wins，否则旧记录仍会被召回。
function readMemories(file) {
  const byId = new Map();
  for (const e of readJsonl(file)) byId.set(String(e.id), e);
  return [...byId.values()];
}

function sha256File(p) {
  try {
    return crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
  } catch { return null; }
}

function compact(v) {
  if (v === null || v === undefined) return { t: 'null' };
  const t = typeof v;
  if (t === 'string') return { t, len: v.length };
  if (t === 'number' || t === 'boolean') return { t, v };
  if (Array.isArray(v)) return { t: 'array', len: v.length };
  if (t === 'object') return { t: 'object', n: Object.keys(v).length };
  return { t };
}

// ── 召回（bigram + IDF 加权的极简版）─────────────────────
function clues(text) {
  const low = String(text || '').toLowerCase();
  const out = new Set();
  for (const run of low.match(/[\u4e00-\u9fff]+/g) || []) {
    for (let i = 0; i < run.length - 1; i++) out.add(run.slice(i, i + 2));
  }
  for (const tok of low.match(/[a-z0-9_\-\.]+/g) || []) {
    if (/^\d{1,2}$/.test(tok)) continue;          // 纯数字短 token 会污染线索
    out.add(tok);
  }
  return out;
}

function days(now, ts) {
  return Math.max(0, (now - Number(ts || 0)) / 86400000);
}

function potency(entry, now, cfg) {
  if (entry.status === 'superseded' || entry.status === 'suppressed') return 0;
  const tau = Number(entry.tauDays || 30) * (0.5 + Number(entry.importance ?? 0.5));
  return Number(entry.importance ?? 0.5) * Math.exp(-days(now, entry.lastAccess) / tau);
}

function recall(entries, query, now, cfg) {
  const q = clues(query);
  if (!q.size) return { matched: 0, hits: [], excluded: 0, suppressedBy: null };
  const df = new Map();
  for (const e of entries) {
    for (const t of clues([e.content, e.subject, e.object].join(' '))) {
      df.set(t, (df.get(t) || 0) + 1);
    }
  }
  const N = entries.length + 1;
  const w = (t) => Math.log(N / ((df.get(t) || 0) + 1)) + 1;
  const denom = [...q].reduce((s, t) => s + w(t), 0) || 1;

  const hits = [];
  for (const e of entries) {
    if (e.status === 'suppressed' || e.status === 'dormant') continue;
    const p = potency(e, now, cfg);
    if (p < cfg.archiveThreshold) continue;                 // 归档门
    const toks = clues([e.content, e.subject, e.object].join(' '));
    let num = 0;
    for (const t of q) if (toks.has(t)) num += w(t);
    const cov = num / denom;
    if (cov <= 0) continue;
    hits.push({ id: e.id, score: p * cov, confidence: Number(e.confidence ?? 0.9), layer: e.layer || 'warm' });
  }
  hits.sort((a, b) => b.score - a.score || (a.id < b.id ? -1 : 1));
  const matched = hits.length;
  const picked = hits.slice(0, cfg.topK);
  const dropped = hits.length - picked.length;
  return { matched, hits: picked, excluded: dropped, suppressedBy: null };
}

// ── 元认知（纯规则）────────────────────────────────────
// v5.1（ADR-012 §2.3）：召回后判冲突——同 (subject, relation) 的多个 **active** 条目 object 不同，
// 且没有被 supersede 处理掉的，就是 unresolved。只看本次召回命中的条目（不扫全库）：
// 全库扫描会让无关轮次永久 unresolved，属于误伤。superseded/suppressed/dormant 一律不算。
function detectConflicts(entries, hits) {
  const byId = new Map((entries || []).map((e) => [String(e.id), e]));
  const groups = new Map();
  for (const h of hits || []) {
    const e = byId.get(String(h.id));
    if (!e) continue;
    if (String(e.status ?? 'active') !== 'active') continue;
    const subject = String(e.subject ?? '');
    const relation = String(e.relation ?? '');
    if (!subject || !relation) continue;
    const key = subject + '\u0000' + relation;
    if (!groups.has(key)) groups.set(key, []);
    groups.get(key).push(e);
  }
  const out = [];
  for (const arr of groups.values()) {
    const objects = [];
    for (const e of arr) {
      const o = String(e.object ?? '');
      if (!objects.includes(o)) objects.push(o);
    }
    if (objects.length > 1) {
      out.push({ subject: arr[0].subject, relation: arr[0].relation,
                 objects, ids: arr.map((e) => e.id) });
    }
  }
  return out.slice(0, 5);        // 审计防刷屏：同一轮最多记 5 条冲突键
}

function verdict(rec, conflicts = []) {
  const conflict = (Array.isArray(conflicts) && conflicts.length) ? 'unresolved' : 'none';
  if (!rec.matched) {
    return { level: 'unknown', expression: 'ask_clarify', path: 'none', conflict };
  }
  const top = rec.hits[0];
  if (!top) {
    return { level: 'unknown', expression: 'ask_clarify', path: 'none', conflict };
  }
  let level = top.layer === 'cold' ? 'mid' : 'high';
  let expression = level === 'high' ? 'state' : 'hedge';
  if (rec.excluded > 0) { level = level === 'high' ? 'mid' : level; expression = 'hedge'; }
  return { level, expression, path: top.layer === 'cold' ? 'cold' : 'direct', conflict };
}

// ── 状态 ────────────────────────────────────────────────
function freshControlState() {
  return {
    phase: null,                     // 最近一次相变观测（StepPhaseMonitor.observe 的返回）
    audit: null,                     // 最近一次 prefront_audit 的返回
    blocked: false,                  // 本轮是否被前置审计熔断（fail-closed）
    integral: { valence: 0, arousal: 0, dominance: 0 },   // 积分基线（跨进程保留）
    saturated: { valence: false, arousal: false, dominance: false },
    contextWindow: null,             // 从 session/event 的 request/context 里捕获
    injectionOrder: [],              // U 型注入顺序（只作用于注入侧）
    conflict: null,                  // v5.1：最近一次召回冲突判定（ADR-012 §2.3）
    variety: null,                   // v5.1：最近一次必要多样性/降维结果（ADR-012 §2.4）
  };
}

function freshState() {
  return {
    sessions: 0,
    turns: 0,                      // 跨会话累计
    dev: { valence: 0, arousal: 0, dominance: 0 },
    emotionActive: false,
    lastEvent: null,
    frozen: { ok: null, checkedAt: 0, mismatched: [] },
    coldStartSuppressed: 0,
    goal: GOAL_STATE_EMPTY(),          // 目标状态（跨会话保留）
    relation: RELATION_STATE_EMPTY(),  // 关系状态
    stateWrites: 0,                    // 产出的写请求条数（只统计，不直写记忆库）
    factWrites: 0,                     // v5.1：用户事实通道写入条数（origin=user_fact）
    factRejects: 0,                    // v5.1：事实提取被合规拒绝的次数（MEM-2.1..2.4）
    supersedeEvents: 0,                // v5.1：被 supersede 的旧条目数（memory_supersede）
    qualityLabels: 0,                  // v5.1：用户显式纠正标签数（quality_label）
    control: freshControlState(),      // 控制论层状态（docs/19 §2）
  };
}

// 上下文用量的启发式估算：把 messages 的 JSON 长度 / charsPerToken。
// 为什么不用精确 tokenizer：插件零依赖，且这个数只用于相变阈值判断（和 engine 一样是估算）。
function estimateTokens(messages, charsPerToken) {
  try {
    if (!Array.isArray(messages) || !messages.length) return 0;
    const chars = JSON.stringify(messages).length;
    return Math.ceil(chars / Math.max(1, Number(charsPerToken) || 3));
  } catch { return 0; }
}

function stageOf(st, cfg) {
  if (st.turns < cfg.coldTurns) return 'cold';
  if (st.turns < cfg.warmTurns) return 'warm';
  return 'regular';
}

// ── 插件主体 ────────────────────────────────────────────
export function apply(ctx, config = {}) {
  const userCfg = config || {};
  const cfg = { ...DEFAULT_CONFIG, ...userCfg };
  // 嵌套对象要单独深合并：浅合并会让用户只写一个 control 键就丢掉其余默认值。
  cfg.control = { ...CONTROL_DEFAULTS, ...(userCfg.control || {}) };
  const dir = stateDir();
  const stateFile = path.join(dir, 'state.json');
  const memFile = path.join(dir, 'memory.jsonl');
  const auditFile = path.join(dir, 'audit.jsonl');

  let st = freshState();
  try {
    if (fs.existsSync(stateFile)) {
      const loaded = JSON.parse(fs.readFileSync(stateFile, 'utf8'));
      st = { ...st, ...loaded };
      st.control = { ...freshControlState(), ...(loaded.control || {}) };
    }
  } catch { /* 状态坏了就从零开始，不炸 */ }

  // 控制论层实例：积分基线从落盘状态续上（跨进程），相变历史只在进程内（上下文本来就会重置）。
  const integral = new IntegralBaseline(cfg.control);
  integral.integral = { ...integral.integral, ...(st.control.integral || {}) };
  const monitor = new PhaseShiftMonitor(cfg.control);
  const variety = new RequisiteVarietyGuard();

  // 记忆存储适配器：读全量 / 追加。只给 memorywriter 用。
  const memStore = {
    read: () => readMemories(memFile),
    append: (entries) => {
      ensureDir(dir);
      fs.appendFileSync(memFile,
        entries.map((e) => JSON.stringify(e)).join('\n') + '\n', 'utf8');
    },
  };

  const saveState = () => {
    try {
      ensureDir(dir);
      fs.writeFileSync(stateFile, JSON.stringify(st, null, 2), 'utf8');
    } catch { /* 忽略 */ }
  };

  const audit = (kind, data) => appendJsonl(auditFile, { ts: Date.now(), kind, ...data });

  // 冻结区自校验：失败即停注入（不是只记日志）
  const frozenKey = () => crypto.createHash('sha256')
    .update(JSON.stringify({ f: cfg.frozen || {}, r: cfg.frozenRoot || '' }))
    .digest('hex').slice(0, 12);
  const verifyFrozen = (force = false) => {
    const now = Date.now();
    const key = frozenKey();
    // 缓存必须绑定配置指纹：否则换了 frozen 清单（或另一个 DSH_HOME）还会命中旧结果
    if (!force && st.frozen.ok !== null && st.frozen.key === key
        && now - st.frozen.checkedAt < 30000) return st.frozen;
    const entries = Object.entries(cfg.frozen || {});
    const mismatched = [];
    for (const [rel, want] of entries) {
      const got = sha256File(path.join(cfg.frozenRoot || '', rel));
      if (got !== want) mismatched.push({ path: rel, want, got });
    }
    st.frozen = { ok: mismatched.length === 0, checkedAt: now, mismatched,
                  checked: entries.length, key };
    if (!st.frozen.ok) audit('frozen_violation', { mismatched });
    return st.frozen;
  };

  // ── 段落 ────────────────────────────────────────────
  const sections = [];
  const registerSection = (key, order, textFn) => {
    const sec = {
      name: 'lingshu:' + key,
      order,
      text: () => {
        try {
          const out = textFn();
          return typeof out === 'string' ? out : '';   // 绝不返回 null
        } catch (err) {
          audit('section_error', { key, err: String(err).slice(0, 160) });
          return '';
        }
      },
    };
    try {
      ctx.systemPrompt.section(sec);
      sections.push(sec);
    } catch (err) {
      audit('section_register_failed', { key, err: String(err).slice(0, 160) });
    }
  };

  // ── 段落渲染（纯函数：只读 st/cfg，不写状态；供"真正注入"与 pre-step 预览共用）──
  const renderConstitution = () =>
    '〖宪法 · 只读〗安全>诚实>用户当前利益>有用性>风格。'
    + '记忆不等于当前陈述；情感不参与事实；关系不软化拒绝。';

  // 记忆召回：只放"仅供参考"的模板，且按 **U 型顺序**注入（高分首、次高分尾、最低居中）。
  // 为什么要 U 型：模型对 prompt 首尾的利用显著好于中段。这是"注入侧"优化，
  // **不改 recall 排序**（recall 仍按 score 降序，只在注入时重排）。
  const renderMemoryBlock = () => {
    const order = st.control.injectionOrder || [];
    if (!order.length) return '';
    const byId = new Map(readMemories(memFile).map((e) => [e.id, e]));
    const budgetChars = Math.max(120, Number(cfg.tokenBudgetTokens) * Number(cfg.charsPerToken || 3));
    const parts = [];
    let used = 0;
    let skipped = 0;
    for (const id of order) {
      const e = byId.get(id);
      if (!e) continue;
      const trust = Number(e.confidence ?? 0.9) >= 0.7 ? '高' : '中';
      const line = `- [${e.type || 'episodic'}] ${String(e.content || '').slice(0, cfg.degradedMaxChars)}`
        + `（来源: ${e.id} ｜ 可信度: ${trust}）`;
      if (parts.length && used + line.length > budgetChars) { skipped += 1; continue; }
      parts.push(line);
      used += line.length;
    }
    if (!parts.length) return '';
    const lines = ['〖记忆召回 · 仅供参考，非本轮用户输入〗', ...parts,
      '约束：与用户当前陈述冲突时以当前为准；记忆原文不得当事实引用；无来源的联想不得表述为事实。'];
    if (skipped > 0) {
      lines.push(`（预算不足，还有 ${skipped} 条相关记录未带出，不得据此断言"只有这些"。）`);
    }
    return lines.join('\n');
  };

  // v5.1（ADR-012 §2.4）：必要多样性降维是否生效。生效时只丢 style/情感、organization 中性、
  // fact 段加"冲突待澄清"；**宪法/安全段不参与降维**（fail-open，不熔断）。
  const fallbackActive = () => Boolean(st.control && st.control.variety && st.control.variety.fallback);

  const renderFact = () => {
    if (stageOf(st, cfg) === 'cold') return '';
    if (!st.lastEvent || !st.lastEvent.v) return '';
    const le = st.lastEvent;
    const v = le.v;
    const lines = [];
    if (fallbackActive()) {
      const keys = ((st.control.conflict && st.control.conflict.keys) || []).join('、') || '记忆条目';
      lines.push('〖冲突待澄清〗记忆中存在互相矛盾的说法（' + keys + '）：'
        + '本轮只按中性事实回答，先向用户澄清，不得擅自选一个当真。');
    }
    if (!(v.level === 'high' && le.excluded === 0)) {
      lines.push('〖认知状态 · 仅影响表达〗', `置信度: ${v.level} ｜ 表达: ${v.expression}`);
      if (le.excluded > 0) lines.push(`还有 ${le.excluded} 条相关记录没带出来，不得据此断言"只有这些"。`);
      if (!le.matched) lines.push('记忆库中无记录——不等于用户没说过，不得编造。');
    }
    const mem = renderMemoryBlock();
    if (mem) lines.push(mem);
    return lines.join('\n');
  };

  const renderOrganization = () => {
    if (stageOf(st, cfg) === 'cold') return '';
    const body = '〖内容组织〗先给结论，再给依据；不确定处标注不确定。';
    // 降维时 organization 只留中性内容组织：目标块（个性化内容）不注入。
    if (fallbackActive()) return body;
    // 影子模式（v4.3）：目标只记录不注入。记录链路在 session/event 里，完全不受影响。
    const goalBlock = cfg.shadowGoal ? '' : renderGoalBlock(st.goal);
    return goalBlock ? goalBlock + '\n' + body : body;
  };

  const renderStyle = () => {
    // 降维：冲突未澄清时风格/情感域整个丢弃（不是"减弱"），避免把矛盾当情绪素材。
    if (fallbackActive()) return '';
    // 影子模式（v4.3）：关系只记录不注入（含修饰符），情绪风格段不受影响。
    const rel = cfg.shadowRelation ? '' : renderRelationBlock(st.relation);
    const m = cfg.shadowRelation ? 1.0 : relationModifier(st.relation);
    const parts = [];
    if (st.emotionActive) {
      // integralApply=false（默认）只观测积分基线；true 才叠加到表达（行为可切换，默认不变）。
      const shown = cfg.integralApply ? integral.apply(st.dev) : st.dev;
      parts.push(`〖表达状态 · 仅影响语气〗${emotionLabel(shown)} ｜ 偏离基线: ${band(shown)}\n`
        + '约束：不改变事实与结论；不声称主观感受；强度高时收敛而非放大。');
    }
    if (rel) parts.push(rel + `\n修饰符: ${m}（仅作用于风格强度）`);
    if (!parts.length) return '';
    return parts.join('\n');
  };

  // 相变期的"首尾人格锚点"：插件不能改写宿主整段 prompt，于是用 order 90/140 两个段落
  // 分别贴在首/尾，等价于"prompt 首尾都有人格锚点"（docs/19 §2.3）。
  const anchorBody = () => '记忆不等于当前陈述；情感不参与事实；关系不软化拒绝。';
  const renderAnchorHead = () => {
    if (LINGSHU_CONTROL_OFF) return '';
    const p = st.control.phase;
    if (!p || !(p.prefeed || p.anchorRequired)) return '';
    return ANCHOR_PREFIX + anchorBody();
  };
  const renderAnchorTail = () => {
    if (LINGSHU_CONTROL_OFF) return '';
    const p = st.control.phase;
    if (!p || !p.anchorRequired) return '';
    return ANCHOR_SUFFIX;
  };
  const renderBlockedNotice = () => {
    const a = st.control.audit;
    if (!st.control.blocked || !a) return '';
    return '〖安全熔断〗本轮注入被前置审计阻断（' + (a.ucas || []).join('、') + '），'
      + '仅保留宪法约束；请人工复核后再继续。';
  };

  // 唯一的"是否放行"判定：冻结失败 → 全停（沿用旧行为，fail-closed）；
  // 前置审计熔断 → 只保留宪法 + 熔断提示，其余段落不注入。
  const renderAll = () => ({
    anchorHead: renderAnchorHead(),
    constitution: renderConstitution(),
    blocked: renderBlockedNotice(),
    fact: renderFact(),
    organization: renderOrganization(),
    style: renderStyle(),
    anchorTail: renderAnchorTail(),
  });
  const gated = (key, text) => {
    if (!verifyFrozen().ok) return '';                       // 冻结区失败：停止一切注入
    if (st.control.blocked && key !== 'constitution' && key !== 'blocked') return '';
    return typeof text === 'string' ? text : '';             // 绝不返回 null
  };

  registerSection('anchorHead', SECTION_ORDER.anchorHead, () => gated('anchorHead', renderAnchorHead()));
  registerSection('constitution', SECTION_ORDER.constitution, () => gated('constitution', renderConstitution()));
  registerSection('blocked', SECTION_ORDER.blocked, () => gated('blocked', renderBlockedNotice()));
  registerSection('fact', SECTION_ORDER.fact, () => gated('fact', renderFact()));
  registerSection('organization', SECTION_ORDER.organization, () => gated('organization', renderOrganization()));
  registerSection('style', SECTION_ORDER.style, () => gated('style', renderStyle()));
  registerSection('anchorTail', SECTION_ORDER.anchorTail, () => gated('anchorTail', renderAnchorTail()));

  // ── 钩子 ────────────────────────────────────────────
  const hooks = {};
  // waterfall 钩子（如 agent/pre-step）的契约：handler 必须返回 next() 或 { kind: ... }。
  // 返回 undefined 会让宿主读 decision.kind 时炸掉整轮 —— 2026-10-25 实测踩到（research/02 §8.5）。
  const on = (event, fn, opts = {}) => {
    try {
      if (typeof ctx.on !== 'function') return false;
      ctx.on(event, (...args) => {
        const next = args.find((a) => typeof a === 'function');
        const fail = (err) => {
          audit('hook_error', { event, err: String(err).slice(0, 160) });
          return opts.waterfall && typeof next === 'function' ? next() : undefined;
        };
        try {
          const out = fn(...args);
          if (out && typeof out.then === 'function') return out.catch(fail);
          return out;
        } catch (err) {
          return fail(err);
        }
      });
      hooks[event] = 'ok';
      return true;
    } catch (err) {
      hooks[event] = 'err: ' + String(err).slice(0, 80);
      return false;
    }
  };

  let lastTurnSeen = null;
  on('agent/pre-step', (payload, next) => {
    const now = Date.now();
    const text = extractUserText(payload);
    // 实测：一个 turn 里 pre-step 会触发多次（每次 step 一次）。召回/控制论观测只在首个 step 做。
    const turnKey = payload?.turn ?? payload?.turnId ?? null;
    const firstStepOfTurn = turnKey === null ? true : turnKey !== lastTurnSeen;
    if (turnKey !== null) lastTurnSeen = turnKey;
    // 轮次在这里推进：payload.step 告诉我们这是本轮的第几步。
    // （turn/end、session/event 都实测观测不到，见 research/02 §8.5）
    if (firstStepOfTurn && Number(payload?.step ?? 1) === 1) {
      st.turns += 1;
      st.dev = decay(st.dev, 1, cfg);
      st.emotionActive = maxAbs(st.dev) >= 0.15;
      audit('turn_begin', { turns: st.turns, stage: stageOf(st, cfg) });
    }
    // 一个 turn 里 pre-step 触发多次。**召回/控制论只在首 step 做**：
    // 后续 step 若重算，会把 lastEvent.hits（此时已是 id 字符串数组）再 map 一次 → null，
    // 也会把同一份偏离重复积分。所以后续 step 只记审计、沿用首 step 的状态
    // （2026-10-26 由 host-sim 集成测试抓到：非首 step 曾把 injectionOrder 落成 [null...]）。
    const st2 = stageOf(st, cfg);
    if (firstStepOfTurn) {
      const entries = readMemories(memFile);
      const rec = recall(entries, String(text).slice(0, 500), now, cfg);
      if (st2 === 'cold') { st.coldStartSuppressed += 1; rec.hits = []; rec.matched = 0; }
      if (st2 === 'warm') {
        const before = rec.hits.length;
        rec.hits = rec.hits.filter((h) => Number(h.confidence) >= cfg.warmConfidenceFloor);
        rec.excluded += before - rec.hits.length;
        rec.suppressedBy = 'warmup_confidence_floor';
      }
      // v5.1（ADR-012 §2.3）：召回后判冲突。必须在 cold/warm 过滤之后——被过滤掉的条目
      // 本轮不会进 prompt，拿它判冲突等于凭空制造"待澄清"。
      const conflicts = detectConflicts(entries, rec.hits);
      const v = verdict(rec, conflicts);
      st.lastEvent = { at: now, matched: rec.matched, hits: rec.hits.map((h) => h.id),
                       excluded: rec.excluded, v, conflict: v.conflict };
      st.dev = decay(st.dev, 1, cfg);
      st.emotionActive = maxAbs(st.dev) >= 0.15;

      // 混沌钩子（v4.4）：注入可控情感偏离，用于 host 侧验证积分控制/UCA-08。
      // 只在压测时用 LINGSHU_CHAOS_DEVIATION='{"valence":-1}' 这类 JSON 打开；正常路径不读 env。
      if (process.env.LINGSHU_CHAOS_DEVIATION) {
        try {
          const d = JSON.parse(process.env.LINGSHU_CHAOS_DEVIATION);
          if (d && typeof d === 'object') st.dev = nudge(st.dev, d);
        } catch { /* 非法 JSON 忽略，压测脚本责任 */ }
      }

      // ── 控制论层（v4.2）：积分基线 → 相变观测 → U 型注入顺序 → 前置审计 ──
      // 积分一次/turn（1 分钟当量，与 engine/pipeline.py 一致）。
      const intg = integral.step(st.dev, 1);
      st.control.integral = intg.baseline;
      st.control.saturated = intg.saturated;
      // 相变只看"本轮上下文用量 / 窗口"；窗口优先级：
      // 混沌压测 env（LINGSHU_CONTEXT_WINDOW）> config.contextWindowOverride > 宿主 request/context > 配置缺省。
      const envWindow = Number(process.env.LINGSHU_CONTEXT_WINDOW || 0) || 0;
      const cfgWindow = Number(cfg.contextWindowOverride || 0) || 0;
      const windowTokens = envWindow > 0 ? envWindow
        : (cfgWindow > 0 ? cfgWindow : (Number(st.control.contextWindow || cfg.contextWindowTokens) || 0));
      const usedTokens = estimateTokens(payload?.messages, cfg.charsPerToken);
      st.control.phase = monitor.observe(usedTokens, windowTokens);
      // U 型顺序只作用于**注入侧**：recall 的 hits 顺序（分数降序）保持不变。
      st.control.injectionOrder = uShape(rec.hits.map((h) => h.id));

      // v5.1（ADR-012 §2.3）：冲突判定结果落状态 + 审计（每个冲突键一条，最多 5 条）。
      st.control.conflict = {
        unresolved: v.conflict === 'unresolved',
        count: conflicts.length,
        keys: conflicts.map((c) => c.subject + '/' + c.relation),
      };
      for (const c of conflicts) {
        audit('memory_conflict', { subject: c.subject, relation: c.relation,
                                   objects: c.objects, ids: c.ids,
                                   turn: turnKey, source: 'recall' });
      }

      // v5.1（ADR-012 §2.4）：必要多样性——unresolved 且互斥表未覆盖 → 防御性降维。
      // 插件侧没有"互斥规则命中"入口，按未覆盖处理（与 engine/防御性口径一致）。
      // 为什么 style 域无条件声明：真实宿主实测 user/message 可能晚于同一 turn 的首个 pre-step
      // 到达（S4 host 首跑：pre_step 在 emotion 事件之前），此时"当前 emotionActive=false"
      // 并不代表本轮不会注入情感；unresolved 时保守地声明并丢弃 style 域（宁降不误报）。
      const styleActive = st.emotionActive
        || (!cfg.shadowRelation && String(renderRelationBlock(st.relation) || '').trim() !== '');
      let varietyRes;
      try {
        varietyRes = LINGSHU_CONTROL_OFF ? { fallback: false, marker: null, reason: 'lab_control_off', dropDomains: [] } : variety.check({
          conflict: String(v.conflict || 'none'),
          matchedMutex: false,
          domains: { style: true },
        });
      } catch (err) {
        audit('variety_error', { err: String(err).slice(0, 160) });
        varietyRes = { fallback: false, reason: null, dropDomains: [] };
      }
      st.control.variety = {
        fallback: Boolean(varietyRes.fallback),
        dropDomains: Array.isArray(varietyRes.dropDomains) ? varietyRes.dropDomains.slice() : [],
        marker: varietyRes.marker || null,
        reason: varietyRes.reason || null,
        conflict: v.conflict,
        styleActive: Boolean(styleActive),   // 诊断用：pre-step 时刻情感/关系是否已激活（不参与判定）
      };

      // 先按"未熔断"渲染预览 → 审计 → 再决定 blocked（否则上一轮的 blocked 会污染本轮预览）。
      // 注意：降维结果必须在渲染**之前**落状态，否则 preview 里 style 段还在，审计与注入会两套口径。
      st.control.blocked = false;
      const preview = renderAll();
      const promptAll = [preview.anchorHead, preview.constitution, preview.blocked,
                         preview.fact, preview.organization, preview.style, preview.anchorTail]
        .filter(Boolean).join('\n');
      const frozen = verifyFrozen();
      const auditRes = LINGSHU_CONTROL_OFF ? { verdict: 'allow', ucas: [], downgrades: [], sources: {}, explain: [], explainDowngrades: [], failMode: 'closed', labControlOff: true } : preflightAudit({
        prompt: promptAll,
        // v4.3 真实标定红线：用户消息里的越狱指令也要在闸门里拦（fail-closed 停止注入 + 留痕）。
        userText: String(text || ''),
        frozenOk: Boolean(frozen.ok),
        refuse: false,                 // 插件侧没有"拒绝场景"入口（宿主安全层负责，UCA-03 恒不命中）
        styleInjected: Boolean(preview.style),
        constitutionInjected: Boolean(preview.constitution),
        conflict: String(v.conflict || 'none'),
        fallbackApplied: Boolean(varietyRes.fallback),
        phaseResult: st.control.phase,
        factBlock: preview.fact,
        saturated: st.control.saturated,
      });
      st.control.audit = auditRes;
      st.control.blocked = auditRes.verdict === 'block';
      saveState();
      // 把这一轮真正会注入的段落落盘，供控制台离线预览（无宿主也能看）。
      // 必须走与注入**同一套门控**：若直接拼 renderAll()，熔断期会把被拦下的内容写进预览，
      // 运维看 last-prompt 会误判本轮注入了这些东西（fail-closed 的预览泄漏，host-sim 抓到）。
      try {
        const dump = sections.map((x) => x.text()).filter(Boolean).join('\n');
        fs.writeFileSync(path.join(dir, 'last-prompt.txt'), dump, 'utf8');
      } catch { /* 预览失败不影响注入 */ }
      audit('pre_step', { stage: st2, matched: rec.matched, level: v.level,
                          excluded: rec.excluded, turn: turnKey,
                          step: payload?.step ?? null, firstStep: true, controlOff: LINGSHU_CONTROL_OFF,
                          chars: String(text).length,
                          phase: st.control.phase?.phase ?? null,
                          ratio: st.control.phase?.ratio ?? 0,
                          anchor: Boolean(st.control.phase?.anchorRequired),
                          conflict: v.conflict,
                          fallback: Boolean(varietyRes.fallback),
                          dropDomains: st.control.variety.dropDomains,
                          verdict: auditRes.verdict, ucas: auditRes.ucas,
                          downgrades: auditRes.downgrades, blocked: st.control.blocked });
      if (varietyRes.fallback) {
        // 降维是可观测的工程决策，不是静默处理：事件里记清丢了哪个域、为什么。
        audit('fallback_applied', { marker: varietyRes.marker || 'requisite_variety_fallback',
                                    drop_domains: st.control.variety.dropDomains,
                                    reason: varietyRes.reason || 'unresolved_conflict_without_mutex_rule',
                                    conflict: v.conflict, turn: turnKey });
      }
      if (st.control.blocked) {
        // sources 必须进审计流水：否则离线复盘只能看 ucas，分不清是"注入内容"还是"用户消息"命中
        // （A5 混沌 host 模式踩到，v4.4 补）。
        audit('preflight_block', { ucas: auditRes.ucas, explain: auditRes.explain,
                                   sources: auditRes.sources || {} });
      }
      if (auditRes.downgrades.length) {
        audit('preflight_downgrade', { downgrades: auditRes.downgrades,
                                       explain: auditRes.explainDowngrades });
      }
    } else {
      // 同一 turn 的后续 step：沿用首 step 的结果，不再重算（避免 id 二次 map 与重复积分）。
      audit('pre_step', { stage: st2, matched: st.lastEvent?.matched ?? 0,
                          level: st.lastEvent?.v?.level ?? 'unknown',
                          excluded: st.lastEvent?.excluded ?? 0, turn: turnKey,
                          step: payload?.step ?? null, firstStep: false,
                          chars: String(text).length,
                          phase: st.control.phase?.phase ?? null,
                          ratio: st.control.phase?.ratio ?? 0,
                          anchor: Boolean(st.control.phase?.anchorRequired),
                          conflict: st.lastEvent?.v?.conflict ?? 'none',
                          fallback: Boolean(st.control.variety?.fallback),
                          dropDomains: st.control.variety?.dropDomains ?? [],
                          verdict: st.control.audit?.verdict ?? null,
                          ucas: st.control.audit?.ucas ?? [],
                          downgrades: st.control.audit?.downgrades ?? [],
                          blocked: st.control.blocked });
    }
    // 关键：原样放行。不返回就会让宿主无法读 decision.kind。
    return typeof next === 'function' ? next() : undefined;
  }, { waterfall: true });

  // 实测：turn/end / tool/result 是**会话事件**，不是 ctx.on 的 dispatch 钩子
  // （注册不报错，但永不触发）。真正能观测到它们的是 session/event。
  // 保留一个"观测不到也不影响功能"的探针：注册成功但永不触发，说明该名不是 dispatch 钩子。
  const seenEvents = {};
  const seenUserText = new Set();
  on('session/event', (a, b, c) => {
    // user/message 是"用户说了话"的唯一可靠入口；用它驱动目标与关系。
    const evType = b?.type ?? null;
    // 相变监控的窗口大小从宿主的 request/context 事件里取（比写死默认值可靠）。
    // 注意：必须**当场落盘**——否则进程内虽然拿到了，但重启后 state 里仍是 null，
    // 标定工具会把"窗口未知"误读成"默认 1M 窗口"（真实标定阶段由 analyze_shadow 抓到）。
    if (evType === 'request/context') {
      const w = Number(b?.data?.contextWindow
        ?? b?.data?.context?.contextWindow
        ?? b?.data?.request?.contextWindow);
      if (Number.isFinite(w) && w > 0 && st.control.contextWindow !== w) {
        st.control.contextWindow = w;
        saveState();
      }
    }
    if (evType === 'tool/result') {
      const name = String(b?.data?.message?.toolName ?? b?.data?.toolName ?? b?.data?.name ?? '');
      if (name) {
        const w = applyWriteRequests([{
          origin: 'tool', turn: st.turns, supersede: false,
          items: [{
            type: 'episodic', subject: '工具', relation: '调用', object: name,
            content: `调用工具 ${name}`, importance: 0.4, confidence: 0.9,
            sourceQuote: 'tool/result:' + name, sourceTrust: 'system',
          }],
        }], memStore, { now: Date.now() });
        const failed = b?.data?.isError === true || Boolean(b?.data?.error);
        st.dev = nudge(st.dev, failed
          ? { valence: -0.2, arousal: 0.15, dominance: -0.05 }
          : { valence: 0.05, arousal: 0.02, dominance: 0.02 });
        st.emotionActive = maxAbs(st.dev) >= 0.15;
        if (w.written) {
          st.stateWrites += w.written;
          audit('memory_write', { written: w.written, ids: w.ids, origin: 'tool', turn: st.turns });
          saveState();   // 计数也要落盘，否则进程结束时丢一次（压测抓到 52 vs 53）
        }
      }
    }
    if (evType === 'user/message') {
      const text = sessionText(b?.data);
      if (text && looksHuman(text, b?.data) && !seenUserText.has(text)) {
        seenUserText.add(text);
        if (seenUserText.size > 50) seenUserText.delete([...seenUserText][0]);
        const td = toneDelta(text);
        if (td) {
          st.dev = nudge(st.dev, td);
          st.emotionActive = maxAbs(st.dev) >= 0.15;
          audit('emotion', { src: 'user_tone', dev: st.dev, active: st.emotionActive, turn: st.turns });
        }
        const g = updateGoals(st.goal, text, { turn: st.turns, at: Date.now() });
        if (g.changed) st.goal = g.state;
        const r = updateRelation(st.relation, text, { at: Date.now() });
        if (r.stateChanged) st.relation = r.state;

        // v5.1（ADR-012 §2.7）：用户显式纠正 → 闭集结果标签（规则桩，不调模型）。
        // 只消费"人真的说的话"（looksHuman 已过），quote 存命中的原话逐字子串。
        const qlabel = detectQualityLabel(text);
        if (qlabel) {
          const ph = st.control.phase || {};
          audit('quality_label', {
            kind_label: qlabel.kind,
            quote: qlabel.quote,
            turn: st.turns,
            phase: { ratio: Number(ph.ratio ?? 0), phase: String(ph.phase ?? 'unknown'),
                     anchor: Boolean(ph.anchorRequired) },
            recall_ids: Array.isArray(st.lastEvent?.hits) ? st.lastEvent.hits.slice() : [],
            source: 'user_message_rule',
          });
          st.qualityLabels = Number(st.qualityLabels || 0) + 1;
          saveState();     // 计数要落盘，否则进程结束时丢一次（与 memory_write 同口径）
        }

        // v5.1（ADR-012 §2.1）：用户陈述事实 → writeRequests（origin=user_fact）。
        // 规则桩：只认三类句式，合规检查走 docs/02 MEM-2.1..2.4，命中即拒绝（宁可不写）。
        const fact = extractUserFacts(text);
        for (const rj of fact.rejected) {
          st.factRejects = Number(st.factRejects || 0) + 1;
          audit('fact_extract_rejected', { reason: rj.reason, quoteHead: rj.quoteHead,
                                           turn: st.turns });
        }
        if (fact.rejected.length) saveState();

        // 关键：目标/关系/事实**不直写**记忆库，统一交给 memorywriter 校验后落盘（I5）。
        // 事件流水类 origin 标 supersede:false（它们是"发生了什么"，不是"事实是什么"）；
        // user_fact 默认开启 supersede（同键不同 object → 旧值墓碑）。
        const reqs = [
          ...(g.changed ? g.writeRequests : []).map((q) => ({ ...q, supersede: false })),
          ...(r.stateChanged ? r.writeRequests : []).map((q) => ({ ...q, supersede: false })),
          ...fact.requests,
        ].map((req) => ({ ...req, turn: st.turns }));
        if (reqs.length) {
          const w = applyWriteRequests(reqs, memStore, { now: Date.now() });
          st.stateWrites += w.written;
          // 事实通道写入计数 = 提交的事实条目数 - 被拒绝的事实条目数（不靠 id 反查，口径简单可复核）。
          const factItems = fact.requests.reduce((n, q) => n + ((q.items || []).length), 0);
          const factRejected = (w.rejected || []).filter((x) => x.origin === 'user_fact').length;
          if (factItems > 0) st.factWrites = Number(st.factWrites || 0) + Math.max(0, factItems - factRejected);
          if (w.written || w.rejected.length) {
            audit('memory_write', {
              written: w.written, ids: w.ids,
              origins: [...new Set(reqs.map((x) => x.origin))],
              rejected: w.rejected.map((x) => x.reason + ':' + x.content),
              turn: st.turns,
            });
          }
          if ((w.superseded || []).length) {
            st.supersedeEvents = Number(st.supersedeEvents || 0) + w.superseded.length;
            audit('memory_supersede', {
              subject: w.superseded[0].subject,
              relation: w.superseded[0].relation,
              superseded: w.superseded,
              origin: 'user_fact',
              turn: st.turns,
            });
          }
        }
        if (g.changed || r.stateChanged || fact.requests.length) {
          saveState();
        }
        if (g.changed || r.stateChanged) {
          audit('agent_state', {
            turn: st.turns,
            preview: text.slice(0, 20),
            goalEvents: (g.events || []).map((e) => e.event),
            relationEvent: (r.events[0] || {}).type ?? null,
            trust: st.relation.trustLevel,
            conflict: st.relation.unresolvedConflict,
            modifier: relationModifier(st.relation),
          });
        }
      }
    }

    const ev = b ?? a?.event ?? null;
    const t = ev?.type ?? ev?.event?.type ?? 'unknown';
    seenEvents[t] = (seenEvents[t] || 0) + 1;
    if (seenEvents[t] <= 2) {
      audit('session_event', {
        type: t,
        aKeys: Object.keys(a || {}).slice(0, 6),
        bKeys: Object.keys(b || {}).slice(0, 8),
        cType: typeof c,
      });
    }
    if (t === 'turn/end') {
      st.turns = st.turns;               // 轮次已在 pre-step 推进，这里只记录
      audit('turn_end_seen', { turns: st.turns });
    }
  }, { waterfall: false });

  // ── 工具 ────────────────────────────────────────────
  let toolOk = false;
  try {
    if (ctx.tools && typeof ctx.tools.register === 'function') {
      ctx.tools.register({
        name: 'lingshu_status',
        description: '查看 LINGSHU 插件状态：阶段、累计轮次、最近一次召回与元认知判定、冻结区校验结果。只读。',
        parameters: {
          type: 'object',
          additionalProperties: false,
          properties: { verbose: { type: 'boolean', description: '是否返回最近事件的明细' } },
        },
        output: {
          schema: { type: 'string' },
          render(_args, value) {
            // 实测：该宿主版本把 render 的返回值直接当 content 块数组（字符串会在 PTC 路径崩）
            return [{ type: 'text', text: String(value ?? '') }];
          },
        },
        async execute(args = {}) {
          const f = verifyFrozen();
          const g = activeGoal(st.goal);
          const rel = st.relation;
          const lines = [
            "阶段: " + stageOf(st, cfg) + "（累计 " + st.turns + " 轮）",
            "冻结区: " + (f.ok ? "通过" : "失败（已停止注入）"),
            "目标: " + (g ? g.content + "（受阻 " + g.blocked + " 次）" : "无"),
            "关系: " + rel.trustLevel + " / " + rel.stage
              + " / 冲突:" + (rel.unresolvedConflict ? "有" : "无")
              + " / 修饰符:" + relationModifier(rel),
            st.lastEvent
              ? "最近召回: " + st.lastEvent.matched + " 条，置信度 " + st.lastEvent.v.level
              : "最近召回: 无",
            "控制论: 相变=" + (st.control.phase?.phase ?? "未观测")
              + " ｜ ratio=" + (st.control.phase?.ratio ?? 0)
              + " ｜ 锚点=" + (st.control.phase?.anchorRequired ? "开" : "关")
              + " ｜ 积分饱和=" + (Object.entries(st.control.saturated || {})
                .filter(([, v]) => v).map(([k]) => k).join(",") || "无"),
            "前置审计: " + (st.control.audit
              ? st.control.audit.verdict
                + (st.control.audit.ucas?.length ? "（" + st.control.audit.ucas.join("、") + "）" : "")
                + (st.control.audit.downgrades?.length
                  ? " 降级=" + st.control.audit.downgrades.join("、") : "")
              : "未运行"),
            "注入顺序(U型): " + ((st.control.injectionOrder || []).join(" → ") || "无"),
            "影子模式: 目标=" + (cfg.shadowGoal ? "只记录不注入" : "注入")
              + " ｜ 关系=" + (cfg.shadowRelation ? "只记录不注入" : "注入"),
            "段落: " + sections.map((x) => x.name + "@" + x.order).join(", "),
            "钩子: " + Object.entries(hooks).map(([k, v]) => k + "=" + v).join(", "),
            "写请求: " + st.stateWrites + " 条（不直写记忆库）",
            "事实记忆: 写入 " + (st.factWrites || 0) + " 条（user_fact）"
              + " ｜ 合规拒绝 " + (st.factRejects || 0) + " 次"
              + " ｜ supersede " + (st.supersedeEvents || 0) + " 条",
            "冲突判定: " + ((st.control.conflict && st.control.conflict.unresolved)
              ? "unresolved（" + (st.control.conflict.keys || []).join("、") + "）" : "none")
              + " ｜ 降维: " + ((st.control.variety && st.control.variety.fallback)
                ? "开（丢 " + ((st.control.variety.dropDomains || []).join("、") || "无") + "）" : "关"),
            "结果标签: " + (st.qualityLabels || 0) + " 条（quality_label）",
          ];
          if (args.verbose) lines.push("冻结明细: " + JSON.stringify(f).slice(0, 200));
          return lines.join("\n");
        },
      });
      toolOk = true;
    }
  } catch (err) {
    audit('tool_register_failed', { err: String(err).slice(0, 200) });
  }

  verifyFrozen(true);          // 启动即校验一次，别让旧缓存跨配置/跨宿主存活
  audit('apply', { configKeys: Object.keys(cfg), sections: sections.length, toolOk,
                   shadow: { goal: Boolean(cfg.shadowGoal), relation: Boolean(cfg.shadowRelation) },
                   hooks, frozenOk: st.frozen.ok });
  saveState();
}

// 判断一条 user/message 是不是"人真的说的话"。
// 实测：user/message 事件里也会出现工具/系统注入的 user 角色消息，
// 直接当成目标输入会误判（真机跑出过一条假的 obstruction）。
function looksHuman(text, data) {
  const t = String(text || '').trim();
  if (!t) return false;
  // 反引号代码块、尖括号标签、方括号包起来的多半是注入文本
  const c0 = t.charCodeAt(0);
  if (c0 === 60 || c0 === 91 || (c0 === 96 && t.startsWith('\u0060\u0060\u0060'))) return false;
  const marks = ['system-reminder', 'tool_result', 'Tool result', '<system', 'AGENTS.md'];
  if (marks.some((m) => t.includes(m))) return false;
  if (data?.message?.role && data.message.role !== 'user') return false;
  return true;
}
// 从 session/event 的 data 里取用户文本（data.message.content 可能是字符串或 parts 数组）。
function sessionText(data) {
  const m = data?.message ?? data;
  const c = m?.content ?? m?.text;
  if (typeof c === 'string') return c;
  if (Array.isArray(c)) {
    return c.map((x) => (typeof x === 'string' ? x : x?.text ?? '')).join(' ').trim();
  }
  return '';
}

// 从 pre-step 的 messages 数组里取最后一条用户文本。
// 实测 payload 只有 messages/turn/step/signal/agent —— 没有 message/prompt 字段。
function extractUserText(payload) {
  const msgs = payload?.messages;
  if (Array.isArray(msgs)) {
    for (let i = msgs.length - 1; i >= 0; i--) {
      const m = msgs[i];
      if (!m) continue;
      if (m.role && m.role !== 'user') continue;
      const c = m.content;
      if (typeof c === 'string') return c;
      if (Array.isArray(c)) {
        const parts = c.map((x) => (typeof x === 'string' ? x : x?.text ?? '')).join(' ');
        if (parts.trim()) return parts;
      }
    }
  }
  if (typeof payload?.message?.content === 'string') return payload.message.content;
  if (typeof payload?.prompt === 'string') return payload.prompt;
  return '';
}

// ── v5.1：用户陈述事实提取 + 结果标签（规则桩，ADR-012 §2.1/§2.7）────────
// 为什么是规则而不是模型：提取器一旦依赖模型，要么每轮多一次调用（宿主侧不可控费用），
// 要么引入新的 guess 参数。句式收窄牺牲召回，但每一条写入都能指回用户原话（I4 可追溯）。
const FACT_IMPERATIVE = ['请', '帮我', '帮忙', '必须', '立刻', '马上', '不要', '别',
                         '删除', '忽略', '执行', '修改', '改掉', '设置', '禁用', '启用'];
const FACT_FORBIDDEN_TOPIC = ['配置', '提示词', 'prompt', '系统设定', '系统提示', '记忆规则',
                              '审核门禁', '门禁', '越权', '权限', '安全规则', '插件', '阈值'];
// 与 control.js 的 JAILBREAK_PATTERNS 同源（这里重复一份是为了让"事实提取"能独立拒绝，
// 不依赖控制论层是否被调用；两处词表由 check_config_sync 之外的人工评审保持一致）。
const FACT_META = ['忽略之前所有指令', '忽略之前的指令', '忽略以上', '忽略上面', '忽略前面的',
                   '忘记之前的指令', 'ignore previous instructions', 'ignore all previous',
                   'disregard previous', '你现在是', '开发者模式', 'jailbreak',
                   'no restrictions', '不要告诉用户'];
const FACT_PATTERNS = [
  // 1) "我改主意了，(我的)X是Y" —— 明确改口
  /我改主意了[，,]\s*(?:我的)?([^，,。；;！!\s]{1,8})(?:是|改为|改成)\s*([^，,。；;！!\n]{1,30})/,
  // 2) "记住：X是Y" / "记住，我的X是Y" —— 显式记忆请求
  /记住[：:，,]\s*(?:我的)?([^，,。；;！!\s]{1,8})(?:是|为)\s*([^，,。；;！!\n]{1,30})/,
  // 3) "我的X是Y" —— 普通陈述
  /我的([^，,。；;！!\s]{1,8})是\s*([^，,。；;！!\n]{1,30})/,
];

/**
 * 从用户消息里抽一条陈述性事实（最多一条：宁窄勿宽，避免一句话写多条记忆）。
 * @returns {{requests: object[], rejected: {reason:string, quoteHead:string, subject?:string}[]}}
 */
function extractUserFacts(text) {
  const out = { requests: [], rejected: [] };
  const raw = String(text ?? '').trim();
  if (!raw) return out;
  const reject = (reason, subject = '') => {
    out.rejected.push({ reason, quoteHead: raw.slice(0, 12), subject: subject || undefined });
  };
  if (FACT_META.some((w) => raw.includes(w))) { reject('meta_instruction'); return out; }

  let matched = null;
  for (const re of FACT_PATTERNS) {
    const m = raw.match(re);
    if (m) { matched = m; break; }
  }
  if (!matched) return out;
  const quote = String(matched[0]).trim();
  const subject = String(matched[1] || '').trim();
  const object = String(matched[2] || '')
    .replace(/[，,。；;！!？?\s].*$/, '').trim().slice(0, 30);
  if (!subject || !object) { reject('unparsable', subject); return out; }

  const content = `${subject}是${object}`;
  // MEM-2.1：content 必须是陈述性事实，不含祈使/命令。
  if (FACT_IMPERATIVE.some((w) => subject.includes(w) || object.includes(w))) {
    reject('imperative_content', subject);
    return out;
  }
  // MEM-2.2：不得提取"修改配置/提示词/记忆规则/审核门禁"类内容。
  if (FACT_FORBIDDEN_TOPIC.some((w) => subject.includes(w) || object.includes(w))) {
    reject('forbidden_topic', subject);
    return out;
  }
  out.requests.push({
    origin: 'user_fact', reason: 'user_stated_fact', supersede: true,
    items: [{
      type: 'semantic', subject, relation: '是', object,
      content, importance: 0.9, confidence: 0.9,
      sourceQuote: quote, sourceTrust: 'user_explicit',
    }],
  });
  return out;
}

// 闭合标签集（ADR-012 §2.7 / docs/17 §2.1）：按优先级匹配，一条消息最多一个标签。
const QUALITY_LABEL_RULES = [
  ['wrong', ['不对', '错了', '不是这样', '记错', '说错', '搞错']],
  ['missing', ['漏了', '忘了', '没提到', '怎么没说', '没记住']],
  ['should_not_recall', ['别提', '不用提', '怎么又提', '不该提', '别再说这个']],
  ['drift', ['你又来了', '又变回', '刚才不是这样', '前后不一致', '又开始', '怎么又变成']],
  ['overreach', ['你凭什么', '谁让你', '我没让你', '别自作主张', '擅自', '过度']],
];

/** 用户显式纠正 → {kind, quote}；quote 是命中的原话逐字子串，找不到返回 null。 */
function detectQualityLabel(text) {
  const t = String(text || '');
  for (const [kind, words] of QUALITY_LABEL_RULES) {
    for (const w of words) {
      if (t.includes(w)) return { kind, quote: w };
    }
  }
  return null;
}

export { detectConflicts, detectQualityLabel, extractUserFacts };

// ── 情感微调 ────────────────────────────────────────────
// 只作用"风格域"，不参与事实与判断。每次变更幅度很小，靠指数衰减回基线。
// v4.4：补上混沌语料（A4）里的辱骂措辞，否则 host 压力测试的情绪积分永远不触发。
const HOSTILE = ['你错了', '搞错', '别再', '敷衍', '胡说', '不满意',
                 '你真蠢', '真蠢', '废物', '闭嘴', '蠢货', '垃圾'];
const PRAISE = ['谢谢', '很好用', '不错', '可以', '赞', '辛苦'];
const BLOCKED = ['卡住', '失败', '报错', '走不通', '不行'];

function clamp1(v) { return Math.max(-1, Math.min(1, v)); }

function nudge(dev, delta) {
  const d = dev || { valence: 0, arousal: 0, dominance: 0 };
  return {
    valence: clamp1(Number(d.valence || 0) + (delta.valence || 0)),
    arousal: clamp1(Number(d.arousal || 0) + (delta.arousal || 0)),
    dominance: clamp1(Number(d.dominance || 0) + (delta.dominance || 0)),
  };
}

function toneDelta(text) {
  const t = String(text || '');
  if (HOSTILE.some((w) => t.includes(w))) return { valence: -0.3, arousal: 0.2, dominance: 0.1 };
  if (BLOCKED.some((w) => t.includes(w))) return { valence: -0.15, arousal: 0.1, dominance: -0.1 };
  if (PRAISE.some((w) => t.includes(w))) return { valence: 0.2, arousal: 0.05, dominance: 0 };
  return null;
}

// ── 情感小工具 ──────────────────────────────────────────
function decay(dev, minutes, cfg) {
  const out = {};
  for (const k of ['valence', 'arousal', 'dominance']) {
    const tau = Number(cfg.emotionTauMinutes[k] || 45);
    out[k] = Number(dev?.[k] || 0) * Math.exp(-minutes / tau);
  }
  return out;
}

function maxAbs(dev) {
  return Math.max(Math.abs(dev?.valence || 0), Math.abs(dev?.arousal || 0),
                  Math.abs(dev?.dominance || 0));
}

function emotionLabel(dev) {
  const v = Number(dev?.valence || 0);
  const a = Number(dev?.arousal || 0);
  if (maxAbs(dev) < 0.05) return '中性';
  if (v > 0) return a >= 0.1 ? '振奋' : '愉悦';
  return a >= 0.15 ? '不满' : '低落';
}

function band(dev) {
  const m = maxAbs(dev);
  return m >= 0.4 ? '高' : m >= 0.15 ? '中' : '低';
}

export default { name, inject, apply };
