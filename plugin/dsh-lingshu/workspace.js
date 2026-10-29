// workspace.js —— 人格体（Persona Entity）工作区隔离：数据层 + 策略层（P1/P2）
// 依据：decisions/017（H0 显式越权解冻）、docs/roadmap-persona-entity.md §2.1/§2.3/§5
//
// 四条边界（改这里之前先读 ADR-017，不要"顺手"放宽）：
//   1) 缺省零回归：不显式配置工作区时，路径与 index.js#stateDir 的既有语义逐字节一致；
//      只有 `workspaceId` / `LINGSHU_WORKSPACE` 显式存在，或 active-workspace.json（热切换选择）
//      显式指向某个 workspace 时，才启用 `workspace_{id}/` 这一层。
//   2) 目录隔离 != 权限隔离：同一进程、同一 OS 用户，这层只隔离数据文件，不是安全边界（RK-1）。
//   3) 策略只收紧：工作区策略只能往全局 UCA 规则集**追加 deny**；不允许 allow、不允许改判定
//      顺序、不允许改提示文案（文案唯一来源 docs/24 §3，改文案先改文档 + 升 policy_version）。
//   4) 本模块是纯函数库：不读文件、不写文件、不读 process.env（env 由调用方传入），便于自检。
//
// 未实现（P3，需另立 ADR + H0 批准）：按工作区**放开**能力（如"工作区 A 允许 exec"）。
// 当前插件不申请 `shell`，T-UCA-06 一律 block，所以这里没有任何 allow 通道。

import path from 'node:path';

export const SCHEMA_VERSION = 'workspace-policy-0.1';
// active-workspace.json（热切换选择）的 schema 版本。文件放在**数据根**，不在 workspace 目录内。
export const SELECTION_SCHEMA_VERSION = 'active-workspace-0.1';
export const WORKSPACE_ID_RE = /^[A-Za-z0-9_-]{1,32}$/;
export const PROFILE_TAG_RE = /^[A-Za-z0-9_-]{1,32}$/;
// 规则 id 形状：T-UCA-01…16 / UCA-01…08。只做形状校验，**不登记**具体 id —— 登记表来自 docs/24。
const RULE_ID_RE = /^(T-)?UCA-[0-9]{2}$/;
// 策略文件允许的键。多一个少一个都 fail-closed（登记制，避免"配置里偷偷塞开关"）。
const POLICY_KEYS = Object.freeze(['schema_version', 'workspace', 'ruleset', 'deny']);

/**
 * 解析 active-workspace.json 的内容（纯函数，不读文件）。
 * 非法选择必须能被忽略并审计，而不是抛异常。
 *
 * @param {object|null|undefined} doc 文件解析结果
 * @param {string} [profileTag] 当前 profileTag（文件里的 profileTag 必须匹配；缺省视为匹配旧文件）
 * @returns {{provided:boolean, workspaceId?:string, invalid?:boolean, reason?:string}}
 *   provided=false  = 没有选择文件/没有选择
 *   workspaceId=''  = 显式选择 legacy 布局（文件里 workspaceId 为 null/''）
 */
export function normalizeSelection(doc, profileTag = '') {
  if (doc === undefined || doc === null) return { provided: false };
  if (typeof doc !== 'object' || Array.isArray(doc)) {
    return { provided: true, invalid: true, reason: 'selection-not-object' };
  }
  if (doc.schema_version !== undefined && doc.schema_version !== SELECTION_SCHEMA_VERSION) {
    return { provided: true, invalid: true, reason: 'bad-selection-schema' };
  }
  const tag = typeof doc.profileTag === 'string' ? doc.profileTag.trim() : null;
  const wantTag = String(profileTag || '').trim();
  // 选择文件跨 profile 共用数据根：带 profileTag 的文件只能被同 profile 采用。
  // profileTag 缺失/null 视为旧文件（兼容），不参与匹配。
  if (typeof tag === 'string' && tag !== wantTag) {
    return { provided: true, invalid: true, reason: 'profile-mismatch' };
  }

  const raw = doc.workspaceId;
  if (raw === null || raw === undefined || raw === '') {
    return { provided: true, workspaceId: '' };   // 显式 legacy
  }
  if (typeof raw !== 'string') {
    return { provided: true, invalid: true, reason: 'selection-not-string' };
  }
  const id = raw.trim();
  // 'legacy' 是伪工作区展示名，不是真实目录名；显式 legacy 用 workspaceId:null 表达。
  if (id === 'legacy') return { provided: true, invalid: true, reason: 'reserved_workspace_id' };
  if (!WORKSPACE_ID_RE.test(id)) return { provided: true, invalid: true, reason: 'invalid_workspace_id' };
  return { provided: true, workspaceId: id };
}

/**
 * 解析工作区身份。
 * 优先级：env.LINGSHU_WORKSPACE > selected（active-workspace.json）> config.workspaceId > 空（legacy 布局）。
 * 非法 id **不**回退到别的 workspace（那会让拼写错误静默共享数据），而是返回
 * `{ id: '', rejected: <原值>, rejectedSource }`：调用方按 legacy 布局运行**并必须落审计**。
 * `selected === ''` 表示"持久化选择就是 legacy"（文件里 workspaceId:null），会覆盖 config。
 */
export function resolveWorkspace({ config = {}, env = {}, selected } = {}) {
  const envRaw = typeof env.LINGSHU_WORKSPACE === 'string' ? env.LINGSHU_WORKSPACE.trim() : '';
  const cfgRaw = typeof config.workspaceId === 'string' ? config.workspaceId.trim() : '';
  if (envRaw) {
    if (!WORKSPACE_ID_RE.test(envRaw)) {
      return { id: '', source: 'legacy', rejected: envRaw, rejectedSource: 'env' };
    }
    return { id: envRaw, source: 'env', rejected: null, rejectedSource: null };
  }
  if (selected !== undefined && selected !== null) {
    const sel = typeof selected === 'string' ? selected.trim() : String(selected).trim();
    if (!sel) return { id: '', source: 'selected', rejected: null, rejectedSource: null };
    if (!WORKSPACE_ID_RE.test(sel)) {
      return { id: '', source: 'legacy', rejected: sel, rejectedSource: 'selected' };
    }
    return { id: sel, source: 'selected', rejected: null, rejectedSource: null };
  }
  if (!cfgRaw) return { id: '', source: 'legacy', rejected: null, rejectedSource: null };
  if (!WORKSPACE_ID_RE.test(cfgRaw)) {
    return { id: '', source: 'legacy', rejected: cfgRaw, rejectedSource: 'config' };
  }
  return { id: cfgRaw, source: 'config', rejected: null, rejectedSource: null };
}

/**
 * 计算工作区数据目录。
 * base 由调用方传入（= index.js#stateDir 拆出的数据根，已含 LINGSHU_DATA_DIR / home()），
 * 本函数只追加工作区层与 profile 层：
 *   workspaceId=''          -> <base> 或 <base>/profiles/<tag>        （与旧行为一致）
 *   workspaceId='code'      -> <base>/workspace_code
 *   workspaceId='code'+tag  -> <base>/workspace_code/profiles/<tag>
 *
 * 非法 workspaceId 直接抛错（纵深防御：正常调用应先过 resolveWorkspace，不该走到这里）；
 * 非法 profileTag 沿用旧行为——忽略该层、退回平铺目录，不抛异常。
 */
export function dataDir({ base, workspaceId = '', profileTag = '' } = {}) {
  if (!base || typeof base !== 'string') throw new Error('workspace.dataDir: base required');
  const root = path.resolve(base);
  const ws = String(workspaceId || '').trim();
  const tag = String(profileTag || '').trim();

  if (ws && !WORKSPACE_ID_RE.test(ws)) {
    throw new Error('workspace.dataDir: invalid workspaceId (fail-closed)');
  }

  const segs = [];
  if (ws) segs.push(`workspace_${ws}`);
  if (tag && PROFILE_TAG_RE.test(tag)) segs.push('profiles', tag);

  const dir = segs.length ? path.join(root, ...segs) : root;
  // 纵深防御：任何分支都不得越出数据根（ws 已被正则约束，这里只防将来改坏正则）。
  const resolved = path.resolve(dir);
  if (resolved !== root && !resolved.startsWith(root + path.sep)) {
    throw new Error('workspace.dataDir: path escapes data root');
  }
  return dir;
}

/**
 * 归一化一份工作区策略（来自 rules/workspaces/{id}.yaml 的解析结果或对象）。
 * 返回 { ok:true, policy } 或 { ok:false, reason }；**任何**不合规都 fail-closed。
 */
export function normalizePolicy(doc) {
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
    return { ok: false, reason: 'policy-not-object' };
  }
  for (const k of Object.keys(doc)) {
    if (!POLICY_KEYS.includes(k)) return { ok: false, reason: `unknown-key:${k}` };
  }
  if (doc.schema_version !== undefined && doc.schema_version !== SCHEMA_VERSION) {
    return { ok: false, reason: 'bad-schema-version' };
  }
  if (typeof doc.workspace !== 'string' || !WORKSPACE_ID_RE.test(doc.workspace)) {
    return { ok: false, reason: 'bad-workspace-id' };
  }
  if (doc.ruleset !== undefined
      && (typeof doc.ruleset !== 'string' || !/^[A-Za-z0-9_.-]{1,32}$/.test(doc.ruleset))) {
    return { ok: false, reason: 'bad-ruleset' };
  }
  const deny = doc.deny === undefined ? [] : doc.deny;
  if (!Array.isArray(deny)) return { ok: false, reason: 'deny-not-array' };
  const seen = new Set();
  for (const id of deny) {
    if (typeof id !== 'string' || !RULE_ID_RE.test(id)) return { ok: false, reason: 'bad-deny-id' };
    if (seen.has(id)) return { ok: false, reason: 'duplicate-deny-id' };
    seen.add(id);
  }
  return {
    ok: true,
    policy: {
      schema_version: SCHEMA_VERSION,
      workspace: doc.workspace,
      ruleset: doc.ruleset ?? null,
      deny: [...deny],
    },
  };
}

/**
 * 把工作区策略合并进全局规则集 —— **单调收紧**语义：
 *   - 规则顺序永远是 `baseIds` 的原样顺序（不得重排、不得删减）；
 *   - 工作区只能往 `deny` 里追加已登记的规则 id；
 *   - 未登记 id → fail-closed；`deny` 为空 = 无操作（不是"放开"）。
 * 返回 { ok, deny, effective, tightened } 或 { ok:false, reason }。
 */
export function mergeDeny({ baseIds, policy } = {}) {
  if (!Array.isArray(baseIds) || baseIds.length === 0) {
    return { ok: false, deny: [], effective: [], tightened: false, reason: 'no-base-rule-ids' };
  }
  const base = new Set(baseIds);
  const deny = Array.isArray(policy?.deny) ? policy.deny : [];
  const unknown = deny.filter((id) => !base.has(id));
  if (unknown.length) {
    return {
      ok: false, deny: [], effective: [], tightened: false,
      reason: `unknown-rule-id:${unknown.join(',')}`, unknown,
    };
  }
  return {
    ok: true,
    deny: [...deny],
    effective: baseIds.slice(),   // 顺序/成员恒定：沙盒只做"额外拦截"，不重写规则集
    tightened: deny.length > 0,
  };
}
