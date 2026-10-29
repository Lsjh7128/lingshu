// workspace-policy.js —— P2 工作区策略（deny-only，JSON 文件，随插件包发布）
//
// 文件位置：<pluginRoot>/workspaces/<workspaceId>.json（可被 LINGSHU_WORKSPACE_POLICY_DIR 覆盖，
// 只用于测试/隔离部署；策略本身是 deny-only，指到别处也不会放宽全局规则）。
//
// 纪律（来自 ADR-017 P2 / docs/roadmap-persona-entity.md §2.2，改这里之前先读）：
//   1) 只追加 deny：只允许把**已登记**的 UCA 规则 id 追加进 deny 集，不允许 allow、不允许
//      新增/改写规则正文、不允许重排 evaluation_order、不允许改提示文案。
//   2) 缺失 = 无追加（不是"放开"）：没有策略文件时返回 {status:'missing'}，网关继续用全局策略。
//   3) 非法 = fail-closed 不应用：坏 JSON / 未知键 / 未登记规则 id / workspace 不匹配
//      → {status:'rejected'}，调用方必须**保持全局策略不变**并落审计（不静默、不放宽）。
//   4) deny 的语义是"该类命中时升级为 block"：只把 deny 中规则条目的 verdict 提升为 block；
//      不改变规则触发条件与判定顺序，所以永远只会更严。
//   5) 本模块不写文件；唯一写入点是显式 CLI（workspace-admin.mjs）与面板 API（persona/create）。
//
// 与 workspace.js 的分工：workspace.js 是纯函数（normalizePolicy / mergeDeny），本模块负责
// 读文件、定位、把结果整理成 API/审计可用的形状。

import fs from 'node:fs';
import path from 'node:path';
import { WORKSPACE_ID_RE, normalizePolicy, mergeDeny } from './workspace.js';
import { POLICY as BASE_POLICY } from './tool-gateway.js';

export const POLICY_SCHEMA_VERSION = 'workspace-policy-0.1';
// 已登记规则 id 的唯一来源：tool-gateway.js 的 POLICY（不在这里另存一份，避免漂移）。
export const REGISTERED_RULE_IDS = Object.freeze(BASE_POLICY.rules.map((r) => String(r.id)));

/** 策略文件路径；id 非法或越界返回 null（不抛异常）。 */
export function policyFileOf(policyDir, workspaceId) {
  if (!policyDir) return null;
  const id = String(workspaceId || '').trim();
  if (!WORKSPACE_ID_RE.test(id)) return null;
  const root = path.resolve(String(policyDir));
  const file = path.resolve(root, `${id}.json`);
  if (file !== root && !file.startsWith(root + path.sep)) return null;
  return file;
}

/**
 * 读取并校验一个工作区的策略文件。
 * @returns {{status:'loaded'|'missing'|'rejected', policy:object|null, deny:string[],
 *            ruleset:string|null, reason:string|null, source:string|null, tightened:boolean}}
 *   永远不抛异常；返回的 source 是**相对**文件名（workspaces/<id>.json），不含绝对路径。
 */
export function loadWorkspacePolicy({
  policyDir,
  workspaceId,
  registeredRuleIds = REGISTERED_RULE_IDS,
} = {}) {
  const id = String(workspaceId || '').trim();
  const base = {
    status: 'missing', policy: null, deny: [], ruleset: null,
    reason: null, source: null, tightened: false,
  };
  if (!id) return { ...base, reason: 'no_workspace' };
  if (!WORKSPACE_ID_RE.test(id)) return { ...base, status: 'rejected', reason: 'invalid_workspace_id' };
  // 未配置策略目录（旧宿主/未接线的调用方）等价于"没有策略"：missing，不是 rejected。
  if (!policyDir) return { ...base, reason: 'no_policy_dir' };

  const file = policyFileOf(policyDir, id);
  const source = `workspaces/${id}.json`;
  if (!file) return { ...base, status: 'rejected', reason: 'invalid_policy_path', source };

  let st;
  try {
    st = fs.lstatSync(file);
  } catch (err) {
    if (err && err.code === 'ENOENT') return { ...base, source };
    return { ...base, status: 'rejected', reason: 'unreadable', source };
  }
  // 符号链接不跟随：策略文件必须是普通文件（防止借链接读插件目录之外的内容）。
  if (!st.isFile()) return { ...base, status: 'rejected', reason: 'not_regular_file', source };

  let doc;
  try {
    doc = JSON.parse(fs.readFileSync(file, 'utf8').replace(/^\uFEFF/, ''));
  } catch {
    return { ...base, status: 'rejected', reason: 'bad_json', source };
  }
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) {
    return { ...base, status: 'rejected', reason: 'policy-not-object', source };
  }
  // 文件名即身份：workspace 字段必须与文件名一致（否则 A 文件可能给 B 工作区加 deny）。
  if (doc.workspace !== id) {
    return { ...base, status: 'rejected', reason: 'workspace-mismatch', source };
  }

  const normalized = normalizePolicy(doc);
  if (!normalized.ok) {
    return { ...base, status: 'rejected', reason: normalized.reason, source };
  }
  const merged = mergeDeny({ baseIds: [...registeredRuleIds], policy: normalized.policy });
  if (!merged.ok) {
    // 未登记规则 id / 空基线 → fail-closed：返回 rejected，调用方保持全局策略。
    return { ...base, status: 'rejected', reason: merged.reason, source };
  }
  return {
    status: 'loaded',
    policy: normalized.policy,
    deny: [...merged.deny],
    ruleset: normalized.policy.ruleset ?? null,
    reason: null,
    source,
    tightened: merged.tightened,
  };
}

/**
 * 由全局策略 + 工作区 deny 派生**只收紧**的有效策略：
 *   - rules 数组与 evaluation_order 原样保留（不重排、不新增、不删减、不改文案）；
 *   - 只把 deny 中规则的 verdict 提升为 block（原 verdict 已是 block 时不变）；
 *   - 空 deny / 含未登记 id（防御性过滤）→ 返回原策略对象（不做任何改动）。
 */
export function buildEffectivePolicy(basePolicy = BASE_POLICY, deny = []) {
  const registered = new Set(basePolicy.rules.map((r) => String(r.id)));
  const list = (Array.isArray(deny) ? deny : [])
    .filter((id) => typeof id === 'string' && registered.has(id));
  if (list.length === 0) return basePolicy;
  const denied = new Set(list);
  return {
    ...basePolicy,
    rules: basePolicy.rules.map((rule) => (denied.has(String(rule.id))
      ? {
        ...rule,
        verdict: 'block',
        // 审计/排查用：标出这是工作区 deny 提升，不是全局规则被改。
        workspace_deny: true,
      }
      : rule)),
    // 只记录来源；不参与判定，不改 policy_version。
    workspace_deny: list.slice(),
  };
}

/** API / 审计可用的策略摘要：只有状态、规则集名、deny 与原因，不含路径内容。 */
export function describeWorkspacePolicy(loaded) {
  if (!loaded || typeof loaded !== 'object') {
    return { status: 'missing', ruleset: null, deny: [], reason: null };
  }
  return {
    status: loaded.status,
    ruleset: loaded.ruleset ?? null,
    deny: Array.isArray(loaded.deny) ? loaded.deny.slice() : [],
    reason: loaded.reason ?? null,
  };
}

export default {
  POLICY_SCHEMA_VERSION,
  REGISTERED_RULE_IDS,
  policyFileOf,
  loadWorkspacePolicy,
  buildEffectivePolicy,
  describeWorkspacePolicy,
};
