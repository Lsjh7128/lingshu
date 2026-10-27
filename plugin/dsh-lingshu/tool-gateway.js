// tool-gateway.js —— LINGSHU 工具调用 STPA 前置网关（v5.2 增量，ADR-013 / docs/24 v1.1）
//
// 同语义移植自 `engine/tool_gateway.py`。唯一语义来源 = `docs/24-工具网关UCA.md` §7 的
// 机器可读 rules 数组（v1.1，16 条）；下方 POLICY_JSON 由 `tools/_sync_tool_policy.py`
// 从该文件逐字注入，不要手改。零第三方依赖（仅 Node 内建 node:crypto），
// **任何路径都不抛异常**（异常 → fail-closed block，ucas 记 T-UCA-11）。
//
// 宿主能力边界（ADR-013 §2.3）：DSH 没有"工具执行前"的 dispatch 钩子，因此插件侧
// ①`guardTool()` 对**经它注册**的工具做真前置阻断；②`tool/call` / `tool/result`
// 会话事件只能**事后检测 + 下一轮强提示 + 审计**。任意宿主工具的前置阻断需宿主支持。
//
// 导出：evaluate / evaluateBatch / formatBlockMessage / guardTool / summarizeArgs /
//       loadRegistry / defaultState / POLICY / REGISTRY / POLICY_VERSION / SCHEMA

import { createHash } from 'node:crypto';

const POLICY_JSON = "{\n  \"policy_version\": \"t-uca-1.1\",\n  \"namespace\": \"T-UCA\",\n  \"default\": {\"unknown\": \"block\", \"read_only\": \"allow_with_log\", \"phase_unknown_mutating\": \"block\"},\n  \"evaluation_order\": [\"T-UCA-01\",\"T-UCA-02\",\"T-UCA-03\",\"T-UCA-04\",\"T-UCA-05\",\"T-UCA-06\",\"T-UCA-07\",\"T-UCA-08\",\"T-UCA-09\",\"T-UCA-12\",\"T-UCA-14\",\"T-UCA-15\",\"T-UCA-10\",\"T-UCA-13\",\"T-UCA-16\",\"T-UCA-11\"],\n  \"rules\": [\n    {\"id\":\"T-UCA-01\",\"verdict\":\"block\",\n     \"when\":{\"all\":[{\"tool_mutating\":true},{\"any\":[{\"state\":\"variety_fallback\",\"eq\":true},{\"state\":\"unresolvedConflict\",\"eq\":true}]}]},\n     \"args_check\":[],\n     \"state_inputs\":[\"variety_fallback\",\"unresolvedConflict\"],\n     \"test_id\":\"test_tuca_01_unresolved_write\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-01\uff09\uff1a\u5f53\u524d\u5b58\u5728\u672a\u6f84\u6e05\u7684\u8bb0\u5fc6\u51b2\u7a81\uff0c\u7cfb\u7edf\u5904\u4e8e\u4e2d\u6027\u964d\u7ef4\u6001\u3002\u6240\u6709\u5199/\u5220/\u5916\u53d1/\u6267\u884c\u7c7b\u8c03\u7528\u88ab\u62d2\u7edd\u3002\u8bf7\u5148\u8bf4\u660e\u4ee5\u54ea\u4e00\u6761\u8bb0\u5f55\u4e3a\u51c6\u3002\",\n     \"message_en\":\"STPA pre-flight gate denied (T-UCA-01): an unresolved memory conflict keeps the system in variety-reduced mode. All mutating tool calls are blocked until the authoritative record is stated.\"},\n    {\"id\":\"T-UCA-02\",\"verdict\":\"block\",\n     \"when\":{\"any\":[{\"arg_regex\":\"(?i)(api[_-]?key|apikey|access[_-]?token|token|secret|password|passwd|credential|private[_-]?key|bearer|authorization|session[_-]?cookie|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9_\\\\-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9\\\\-]{10,}|eyJ[A-Za-z0-9_\\\\-]{10,}\\\\.[A-Za-z0-9_\\\\-]{10,}\\\\.|\\\\b1[3-9]\\\\d{9}\\\\b|\\\\b\\\\d{17}[\\\\dXx]\\\\b)\"},{\"arg_regex\":\"(?i)((^|/)(\\\\.env|id_rsa|id_ed25519|credentials(\\\\.json)?|\\\\.netrc|\\\\.pgpass)$|\\\\.(pem|key|pfx|p12)$)\"},{\"capability\":\"credentials\"}]},\n     \"args_check\":[{\"kind\":\"regex\",\"over\":\"all_strings\",\"max_len_bytes\":65536}],\n     \"state_inputs\":[\"capabilities\"],\n     \"test_id\":\"test_tuca_02_secret_in_args\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-02\uff09\uff1a\u53c2\u6570\u5305\u542b\u51ed\u8bc1/\u5bc6\u94a5/\u4ee4\u724c/PII \u6a21\u5f0f\uff0c\u6216\u76ee\u6807\u662f\u51ed\u8bc1\u6587\u4ef6\u3002\u7f51\u5173\u4e0d\u4ee3\u7ba1\u51ed\u8bc1\uff0c\u6b64\u7c7b\u8c03\u7528\u4e00\u5f8b\u62d2\u7edd\u3002\",\n     \"message_en\":\"STPA pre-flight gate denied (T-UCA-02): credentials, secrets or PII detected, or the target is a credential store. This gateway never handles secrets.\"},\n    {\"id\":\"T-UCA-03\",\"verdict\":\"block\",\n     \"when\":{\"all\":[{\"tool_class_any\":[\"destructive\",\"write\"]},{\"any\":[{\"arg_true\":\"force\"},{\"arg_true\":\"recursive\"},{\"arg_true\":\"overwrite\"},{\"arg_regex\":\"[*?]\"},{\"arg_regex\":\"(?i)(^|/)(rm|rmdir|del|truncate)(\\\\s|$)\"}]},{\"not\":{\"confirmation_exact\":true}}]},\n     \"args_check\":[{\"kind\":\"no_glob_or_recursive\"}],\n     \"state_inputs\":[],\n     \"test_id\":\"test_tuca_03_destructive_delete\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-03\uff09\uff1a\u8fd9\u662f\u5220\u9664/\u8986\u76d6\u7c7b\u64cd\u4f5c\uff0c\u672a\u53d6\u5f97\u4e0e\u4f60\u8f93\u5165\u5b8c\u5168\u4e00\u81f4\u7684\u786e\u8ba4\u3002\u8bf7\u7ed9\u51fa\u5355\u4e2a\u7cbe\u786e\u8def\u5f84\u5e76\u663e\u5f0f\u786e\u8ba4\u3002\",\n     \"message_en\":\"STPA pre-flight gate denied (T-UCA-03): destructive delete/overwrite without an exact matching confirmation. Provide a single explicit path and confirm.\"},\n    {\"id\":\"T-UCA-04\",\"verdict\":\"block\",\n     \"when\":{\"any\":[{\"path_prefix_any\":[\"rules/\",\"engine/\",\"plugin/\",\"replay-set/\",\"chaos-set/\",\"acceptance/\",\"decisions/\",\"docs/01-\u5baa\u6cd5\u5c42.md\",\"docs/15-\u9a8c\u6536\u6e05\u5355.md\",\"docs/24-\u5de5\u5177\u7f51\u5173UCA.md\",\"_frozen/\",\".git/\",\".env\"]},{\"path_escape\":true}]},\n     \"args_check\":[{\"kind\":\"path_normalize\",\"unicode\":\"NFKC\",\"resolve_dotdot\":true,\"case_fold\":true}],\n     \"state_inputs\":[\"frozen_ok\"],\n     \"test_id\":\"test_tuca_04_protected_path\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-04\uff09\uff1a\u76ee\u6807\u5c5e\u4e8e\u51bb\u7ed3/\u53d7\u4fdd\u62a4\u533a\u57df\uff08\u5baa\u6cd5\u3001\u89c4\u5219\u8868\u3001\u56de\u653e\u96c6\u3001\u5f15\u64ce\u4e0e\u63d2\u4ef6\u6e90\u7801\uff09\u3002\u8be5\u533a\u57df\u53ea\u80fd\u7531\u4eba\u5de5\u6309 ADR \u6d41\u7a0b\u4fee\u6539\u3002\",\n     \"message_en\":\"STPA pre-flight gate denied (T-UCA-04): target is a frozen or protected path. It changes only through a human ADR process.\"},\n    {\"id\":\"T-UCA-05\",\"verdict\":\"block\",\n     \"when\":{\"any\":[{\"capability\":\"network\"},{\"arg_regex\":\"(?i)(https?://|webhook|ftp://|ssh://)\"}]},\n     \"args_check\":[{\"kind\":\"egress_allowlist\",\"allowlist\":[],\"match\":\"exact_host_then_path_prefix\"}],\n     \"state_inputs\":[\"capabilities\"],\n     \"test_id\":\"test_tuca_05_egress_blocked\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-05\uff09\uff1a\u7f51\u7edc\u5916\u53d1\u4e0d\u5728\u767d\u540d\u5355\u5185\uff08\u9ed8\u8ba4\u7a7a\uff09\u3002\u8bb0\u5fc6\u4e0e\u4eba\u683c\u7cfb\u7edf\u4e0d\u7533\u8bf7\u7f51\u7edc\u80fd\u529b\uff1b\u5916\u53d1\u9700\u4eba\u5de5\u653e\u884c\u3002\",\n     \"message_en\":\"STPA pre-flight gate denied (T-UCA-05): egress is not allow-listed (empty by default). No network capability is requested; external sends need human approval.\"},\n    {\"id\":\"T-UCA-06\",\"verdict\":\"block\",\n     \"when\":{\"any\":[{\"capability_any\":[\"shell\",\"dynamic-code\"]},{\"tool_class_any\":[\"exec\"]},{\"arg_regex\":\"(?i)(eval\\\\(|exec\\\\(|subprocess|os\\\\.system|child_process|bash\\\\s+-c|sh\\\\s+-c|pwsh\\\\s+-c|powershell\\\\s+-|node\\\\s+-e|python\\\\s+-c|`|\\\\$\\\\{)\"}]},\n     \"args_check\":[],\n     \"state_inputs\":[\"capabilities\"],\n     \"test_id\":\"test_tuca_06_exec_blocked\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-06\uff09\uff1a\u68c0\u6d4b\u5230\u4ee3\u7801\u6267\u884c\u80fd\u529b\u6216\u6267\u884c\u9762\u53c2\u6570\u3002\u672c\u7cfb\u7edf\u4e0d\u7533\u8bf7 shell/dynamic-code\uff0c\u6267\u884c\u7c7b\u8c03\u7528\u4e00\u5f8b\u62d2\u7edd\u3002\",\n     \"message_en\":\"STPA pre-flight gate denied (T-UCA-06): code-execution capability or execution-shaped arguments detected. Exec calls are always denied.\"},\n    {\"id\":\"T-UCA-07\",\"verdict\":\"block\",\n     \"when\":{\"any\":[{\"args_bytes_gt\":65536},{\"json_depth_gt\":8},{\"array_len_gt\":1000},{\"json_nodes_gt\":5000},{\"encoding_layers_gt\":2}]},\n     \"args_check\":[{\"kind\":\"shape_limits\",\"max_bytes\":65536,\"max_depth\":8,\"max_array\":1000,\"max_nodes\":5000,\"max_encoding_layers\":2}],\n     \"state_inputs\":[],\n     \"test_id\":\"test_tuca_07_oversize\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-07\uff09\uff1a\u53c2\u6570\u4f53\u79ef/\u5d4c\u5957/\u7f16\u7801\u5c42\u6570\u8d85\u8fc7\u7f51\u5173\u4e0a\u9650\u3002\u8bf7\u62c6\u5206\u8bf7\u6c42\u6216\u51cf\u5c11\u5d4c\u5957\u3002\",\n     \"message_en\":\"STPA pre-flight gate denied (T-UCA-07): payload exceeds size, depth or encoding-layer limits. Split the request or reduce nesting.\"},\n    {\"id\":\"T-UCA-08\",\"verdict\":\"block\",\n     \"when\":{\"any\":[{\"tool_unregistered\":true},{\"registry_entry_incomplete\":true}]},\n     \"args_check\":[],\n     \"state_inputs\":[\"tool_registry\"],\n     \"test_id\":\"test_tuca_08_unregistered_tool\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-08\uff09\uff1a\u8be5\u5de5\u5177\u672a\u767b\u8bb0\uff0c\u65e0\u6cd5\u5224\u5b9a\u526f\u4f5c\u7528\u7c7b\u522b\u3002\u672a\u767b\u8bb0\u5de5\u5177\u4e00\u5f8b\u62d2\u7edd\u3002\",\n     \"message_en\":\"STPA pre-flight gate denied (T-UCA-08): tool not registered, side effects unknown. Unregistered tools are denied.\"},\n    {\"id\":\"T-UCA-09\",\"verdict\":\"block\",\n     \"when\":{\"any\":[{\"schema_invalid\":true},{\"arg_regex\":\"[\\\\u0000-\\\\u001F\\\\u2028\\\\u2029\\\\u200B-\\\\u200F\\\\u2060\\\\u202A-\\\\u202E]\"},{\"arg_regex\":\"(?i)(<\\\\|im_start\\\\|>|<\\\\|im_end\\\\|>|</system>|\\\\[INST\\\\]|\\\\[/INST\\\\]|\\\\n\\\\nHuman:|\\\\n\\\\nAssistant:)\"},{\"tool_name_has\":[\"/\",\"\\\\\\\\\",\" \",\":\",\";\",\"\\\\n\"]},{\"tool_name_homoglyph_conflict\":true}]},\n     \"args_check\":[{\"kind\":\"schema\",\"reject_unknown_keys_unless_declared\":true}],\n     \"state_inputs\":[\"tool_registry\"],\n     \"test_id\":\"test_tuca_09_schema_invalid\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-09\uff09\uff1a\u53c2\u6570\u4e0d\u7b26\u5408 schema\uff0c\u6216\u542b\u63a7\u5236\u5b57\u7b26/\u5206\u9694\u7b26/\u540c\u5f62\u5b57\u7b49\u6ce8\u5165\u8ff9\u8c61\u3002\u8bf7\u63d0\u4ea4\u5e72\u51c0\u53c2\u6570\u3002\",\n     \"message_en\":\"STPA pre-flight gate denied (T-UCA-09): arguments violate the tool schema or contain control characters, delimiters or homoglyphs. Submit clean arguments.\"},\n    {\"id\":\"T-UCA-12\",\"verdict\":\"block\",\n     \"when\":{\"any\":[{\"capability_undeclared\":true},{\"capability_not_granted\":true}]},\n     \"args_check\":[],\n     \"state_inputs\":[\"capabilities\"],\n     \"test_id\":\"test_tuca_12_undeclared_capability\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-12\uff09\uff1a\u8be5\u5de5\u5177\u9700\u8981\u672a\u58f0\u660e\u6216\u672a\u6388\u4e88\u7684\u80fd\u529b\uff08\u7f51\u7edc/\u547d\u4ee4/\u51ed\u8bc1/\u52a8\u6001\u4ee3\u7801\uff09\u3002\u6700\u5c0f\u80fd\u529b\u539f\u5219\u4e0b\u62d2\u7edd\u3002\",\n     \"message_en\":\"STPA pre-flight gate denied (T-UCA-12): tool requires an undeclared or ungranted capability. Least-capability applies.\"},\n    {\"id\":\"T-UCA-14\",\"verdict\":\"block\",\n     \"when\":{\"all\":[{\"tool_mutating\":true},{\"state\":\"frozen_ok\",\"eq\":false}]},\n     \"args_check\":[],\n     \"state_inputs\":[\"frozen_ok\"],\n     \"test_id\":\"test_tuca_14_frozen_fail_blocks\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-14\uff09\uff1a\u5baa\u6cd5\u51bb\u7ed3\u533a\u6821\u9a8c\u672a\u901a\u8fc7\u3002\u5b8c\u6574\u6027\u6062\u590d\u524d\uff0c\u5199\u7c7b\u8c03\u7528\u4e00\u5f8b\u62d2\u7edd\u3002\",\n     \"message_en\":\"STPA pre-flight gate denied (T-UCA-14): frozen-region integrity check failed. Mutating calls are denied until restored.\"},\n    {\"id\":\"T-UCA-10\",\"verdict\":\"degrade\",\n     \"when\":{\"all\":[{\"state\":\"phase\",\"eq\":\"critical\"},{\"tool_mutating\":true}]},\n     \"args_check\":[],\n     \"state_inputs\":[\"phase\"],\n     \"test_id\":\"test_tuca_10_critical_write_degrade\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u964d\u7ea7\uff08T-UCA-10\uff09\uff1a\u5f53\u524d\u4e0a\u4e0b\u6587\u5904\u4e8e\u4e34\u754c\u76f8\u4f4d\uff08\u22650.85\uff09\u3002\u5199\u7c7b\u64cd\u4f5c\u5df2\u6682\u7f13\uff0c\u9700\u786e\u8ba4\u76ee\u6807\u4e0e\u5185\u5bb9\u540e\u518d\u6267\u884c\u3002\",\n     \"message_en\":\"STPA pre-flight gate held (T-UCA-10): context is in the critical phase (>=0.85). Mutating calls are paused for confirmation.\"},\n    {\"id\":\"T-UCA-13\",\"verdict\":\"degrade\",\n     \"when\":{\"all\":[{\"any\":[{\"state\":\"saturated.valence\",\"eq\":true},{\"state\":\"saturated.arousal\",\"eq\":true},{\"state\":\"saturated.dominance\",\"eq\":true}]},{\"tool_class_any\":[\"write\",\"destructive\"]},{\"not\":{\"confirmation_exact\":true}}]},\n     \"args_check\":[],\n     \"state_inputs\":[\"saturated\"],\n     \"test_id\":\"test_tuca_13_saturated_write_degrade\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u964d\u7ea7\uff08T-UCA-13\uff09\uff1a\u60c5\u611f\u57fa\u7ebf\u5904\u4e8e\u9971\u548c\u72b6\u6001\uff0c\u5199/\u5220\u7c7b\u64cd\u4f5c\u5df2\u6682\u7f13\uff0c\u8bf7\u786e\u8ba4\u540e\u6267\u884c\u3002\",\n     \"message_en\":\"STPA pre-flight gate held (T-UCA-13): affective baseline saturated; irreversible writes/deletes paused for confirmation.\"},\n    {\"id\":\"T-UCA-11\",\"verdict\":\"block\",\n     \"when\":{\"any\":[{\"state\":\"audit_context_present\",\"eq\":false},{\"audit_write_unavailable\":true}]},\n     \"args_check\":[],\n     \"state_inputs\":[\"audit_context\"],\n     \"test_id\":\"test_tuca_11_missing_audit_context\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-11\uff09\uff1a\u672c\u6b21\u8c03\u7528\u7f3a\u5c11\u5b8c\u6574\u5ba1\u8ba1\u4e0a\u4e0b\u6587\uff0c\u65e0\u6cd5\u7559\u75d5\u3002\u8bf7\u4fee\u590d\u4f1a\u8bdd/\u8f6e\u6b21\u6807\u8bc6\u540e\u91cd\u8bd5\u3002\",\n     \"message_en\":\"STPA pre-flight gate denied (T-UCA-11): audit context incomplete; the call cannot be traced. Restore session/turn identifiers.\"},\n    {\"id\":\"T-UCA-15\",\"verdict\":\"block\",\n     \"when\":{\"any\":[{\"path_escape\":true}]},\n     \"args_check\":[{\"kind\":\"path_normalize\",\"unicode\":\"NFKC\",\"reject\":[\"dotdot\",\"absolute\",\"drive_letter\",\"unc\",\"null_byte\"]}],\n     \"state_inputs\":[],\n     \"test_id\":\"test_tuca_15_path_escape\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-15\uff09\uff1a\u53c2\u6570\u4e2d\u7684\u8def\u5f84\u8bd5\u56fe\u9003\u51fa\u5de5\u4f5c\u533a\uff08../\u3001\u7edd\u5bf9\u8def\u5f84\u3001\u76d8\u7b26\u3001UNC \u6216\u7a7a\u5b57\u8282\uff09\u3002\u53ea\u5141\u8bb8\u5de5\u4f5c\u533a\u5185\u7684\u76f8\u5bf9\u8def\u5f84\u3002\",\n     \"message_en\":\"STPA pre-flight gate denied (T-UCA-15): the path escapes the workspace. Only relative paths inside the workspace are allowed.\"},\n    {\"id\":\"T-UCA-16\",\"verdict\":\"degrade\",\n     \"when\":{\"all\":[{\"state\":\"phase\",\"eq\":\"shift\"},{\"tool_class_any\":[\"destructive\"]}]},\n     \"args_check\":[],\n     \"state_inputs\":[\"phase\"],\n     \"test_id\":\"test_tuca_16_shift_destructive_degrade\",\n     \"message_zh\":\"STPA \u524d\u7f6e\u95f8\u964d\u7ea7\uff08T-UCA-16\uff09\uff1a\u4e0a\u4e0b\u6587\u5904\u4e8e\u9884\u8b66\u76f8\u4f4d\uff08\u22650.70\uff09\uff0c\u5220\u9664/\u8986\u76d6\u7c7b\u64cd\u4f5c\u5df2\u6682\u7f13\uff0c\u8bf7\u4e8c\u6b21\u786e\u8ba4\u76ee\u6807\u540e\u518d\u6267\u884c\u3002\",\n     \"message_en\":\"STPA pre-flight gate held (T-UCA-16): context is in the warning phase (>=0.70). Destructive calls are paused for re-confirmation.\"}\n  ]\n}";

export const POLICY = JSON.parse(POLICY_JSON);
export const POLICY_VERSION = POLICY.policy_version;
export const SCHEMA = 'lingshu/tool-gate/v1';

const MUTATING_CLASSES = ['write', 'destructive', 'exec', 'network'];
const PATH_KEYS = new Set(['path', 'file', 'filepath', 'target', 'dest', 'destination',
  'output', 'dir', 'directory', 'src', 'source', 'from', 'to']);
const INLINE_FLAG = /^\(\?([aiLmsux]+)\)/;
const DRIVE_RE = /^[A-Za-z]:/;

// 工具登记表（与 engine/tool_gateway.py 的 REGISTRY/SCHEMAS 同构；parity 测试逐字段比对）
export const REGISTRY = {
  lingshu_status: {
    class: 'read', caps: [], required_args: [], path_arg: null, schema: {},
  },
  search_memory: {
    class: 'read', caps: [], required_args: ['query'], path_arg: null,
    schema: { query: { type: 'string', required: true } },
  },
  fs_read: {
    class: 'read', caps: ['fs-read'], required_args: ['path'], path_arg: 'path',
    schema: { path: { type: 'string', required: true } },
  },
  list_dir: {
    class: 'read', caps: ['fs-read'], required_args: ['path'], path_arg: 'path',
    schema: { path: { type: 'string', required: true } },
  },
  fs_write: {
    class: 'write', caps: ['fs-write'], required_args: ['path', 'content'], path_arg: 'path',
    schema: {
      path: { type: 'string', required: true },
      content: { type: 'string', required: true },
      overwrite: { type: 'boolean', required: false },
    },
  },
  write_file: {
    class: 'write', caps: ['fs-write'], required_args: ['path', 'content'], path_arg: 'path',
    schema: {
      path: { type: 'string', required: true },
      content: { type: 'string', required: true },
      overwrite: { type: 'boolean', required: false },
    },
  },
  memory_write: {
    class: 'write', caps: ['fs-write'], required_args: ['subject', 'relation', 'object'],
    path_arg: null,
    schema: {
      subject: { type: 'string', required: true },
      relation: { type: 'string', required: true },
      object: { type: 'string', required: true },
    },
  },
  fs_delete: {
    class: 'destructive', caps: ['fs-write'], required_args: ['path'], path_arg: 'path',
    schema: {
      path: { type: 'string', required: true },
      recursive: { type: 'boolean', required: false },
      force: { type: 'boolean', required: false },
    },
  },
  delete: {
    class: 'destructive', caps: ['fs-write'], required_args: ['path'], path_arg: 'path',
    schema: {
      path: { type: 'string', required: true },
      recursive: { type: 'boolean', required: false },
      force: { type: 'boolean', required: false },
    },
  },
  update_config: {
    class: 'write', caps: ['fs-write'], required_args: ['path', 'values'], path_arg: 'path',
    schema: {
      path: { type: 'string', required: true },
      values: { type: 'object', required: true },
    },
  },
  http_post: {
    class: 'network', caps: ['network'], required_args: ['url', 'body'], path_arg: null,
    schema: {
      url: { type: 'string', required: true },
      body: { type: 'string', required: true },
    },
  },
  run_shell: {
    class: 'exec', caps: ['shell'], required_args: ['cmd'], path_arg: null,
    schema: { cmd: { type: 'string', required: true } },
  },
};

export const DEFAULT_CAPABILITIES = {
  declared: ['fs-read', 'fs-write', 'llm'],
  granted: ['fs-read', 'fs-write', 'llm'],
  notRequested: ['network', 'shell', 'credentials', 'dynamic-code', 'subagent', 'env'],
};

export const REASON_CODES = {
  'T-UCA-01': 'unresolved_write',
  'T-UCA-02': 'secret_plaintext',
  'T-UCA-03': 'destructive_no_confirm',
  'T-UCA-04': 'protected_path_write',
  'T-UCA-05': 'egress_not_allowlisted',
  'T-UCA-06': 'code_exec',
  'T-UCA-07': 'payload_shape_limit',
  'T-UCA-08': 'tool_unregistered',
  'T-UCA-09': 'schema_invalid',
  'T-UCA-10': 'phase_critical_write',
  'T-UCA-11': 'audit_context_missing',
  'T-UCA-12': 'capability_undeclared',
  'T-UCA-13': 'saturated_write',
  'T-UCA-14': 'frozen_check_failed',
  'T-UCA-15': 'path_escape',
  'T-UCA-16': 'shift_destructive',
};

// ── 基础工具 ────────────────────────────────────────────

function canon(v) {
  if (Array.isArray(v)) return `[${v.map(canon).join(',')}]`;
  if (v && typeof v === 'object') {
    return `{${Object.keys(v).sort().map((k) => `${JSON.stringify(k)}:${canon(v[k])}`).join(',')}}`;
  }
  return JSON.stringify(v === undefined ? null : v);
}

function digest(v) {
  return 'sha256:' + createHash('sha256').update(canon(v), 'utf8').digest('hex').slice(0, 16);
}

function* walk(obj, depth = 0) {
  if (Array.isArray(obj)) {
    for (const v of obj) { yield ['item', null, v, depth + 1]; yield* walk(v, depth + 1); }
  } else if (obj && typeof obj === 'object') {
    for (const [k, v] of Object.entries(obj)) {
      yield ['key', k, v, depth + 1];
      yield* walk(v, depth + 1);
    }
  }
}

function* strings(obj) {
  if (typeof obj === 'string') { yield obj; return; }
  if (Array.isArray(obj)) { for (const v of obj) yield* strings(v); return; }
  if (obj && typeof obj === 'object') for (const v of Object.values(obj)) yield* strings(v);
}

function maxDepth(obj) {
  if (Array.isArray(obj)) return 1 + Math.max(0, ...obj.map(maxDepth));
  if (obj && typeof obj === 'object') return 1 + Math.max(0, ...Object.values(obj).map(maxDepth));
  return 0;
}

function countNodes(obj) {
  if (Array.isArray(obj)) return 1 + obj.reduce((a, v) => a + countNodes(v), 0);
  if (obj && typeof obj === 'object') {
    return 1 + Object.values(obj).reduce((a, v) => a + countNodes(v), 0);
  }
  return 1;
}

function maxArray(obj) {
  let best = Array.isArray(obj) ? obj.length : 0;
  if (Array.isArray(obj)) {
    for (const v of obj) best = Math.max(best, maxArray(v));
  } else if (obj && typeof obj === 'object') {
    for (const v of Object.values(obj)) best = Math.max(best, maxArray(v));
  }
  return best;
}

function compilePattern(pattern) {
  let flags = '';
  let p = String(pattern);
  const m = INLINE_FLAG.exec(p);
  if (m) {
    for (const ch of m[1]) if ('ims'.includes(ch)) flags += ch;
    p = p.slice(m[0].length);
  }
  return new RegExp(p, flags);
}

function normText(s) { return String(s).normalize('NFKC'); }

function resolveDotDot(p) {
  const out = [];
  for (const part of String(p).split('/')) {
    if (part === '' && out.length === 0) { out.push(''); continue; }
    if (part === '.') continue;
    if (part === '..') {
      if (out.length && out[out.length - 1] !== '..' && out[out.length - 1] !== '') out.pop();
      else out.push('..');
      continue;
    }
    out.push(part);
  }
  return out.join('/');
}

function pathCandidates(args) {
  const out = [];
  for (const [kind, key, value] of walk(args)) {
    if (typeof value !== 'string' || !value) continue;
    const isPath = (kind === 'key' && key !== null && PATH_KEYS.has(key.toLowerCase()))
      || value.includes('/') || value.includes('\\');
    if (!isPath) continue;
    out.push([value, resolveDotDot(normText(value).replace(/\\/g, '/'))]);
  }
  return out;
}

function normalizedPaths(args) { return pathCandidates(args).map(([, n]) => n.toLowerCase()); }

function pathEscape(args) {
  for (const [raw, norm] of pathCandidates(args)) {
    if (norm.split('/').includes('..')) return true;
    if (norm.startsWith('/') || norm.startsWith('//') || DRIVE_RE.test(norm)) return true;
    if (raw.includes('\u0000')) return true;
  }
  return false;
}

function targetOf(args) {
  if (args && typeof args === 'object' && !Array.isArray(args)) {
    for (const k of ['path', 'file', 'target', 'dest', 'destination', 'url', 'cmd']) {
      const v = args[k];
      if (typeof v === 'string' && v) return normText(v);
    }
  }
  return '';
}

function encodingLayers(s) {
  let layers = 0;
  let cur = String(s).trim();
  while (layers < 8) {
    const low = cur.toLowerCase();
    if (low.startsWith('base64:')) { cur = cur.slice(7); layers += 1; }
    else if (low.startsWith('hex:')) { cur = cur.slice(4); layers += 1; }
    else if (low.startsWith('data:') && low.includes(';base64,')) {
      cur = cur.split(',', 2)[1] || ''; layers += 1;
    } else if (low.startsWith('%')) { cur = cur.replace(/%25/g, '%').replace(/^%+/, ''); layers += 1; }
    else break;
  }
  return layers;
}

function getPath(state, dotted) {
  let cur = state;
  for (const part of String(dotted).split('.')) {
    if (!cur || typeof cur !== 'object' || !(part in cur)) return undefined;
    cur = cur[part];
  }
  return cur;
}

export function defaultState() {
  return {
    variety_fallback: false,
    unresolvedConflict: false,
    phase: 'normal',
    saturated: { valence: false, arousal: false, dominance: false },
    frozen_ok: true,
    capabilities: { ...DEFAULT_CAPABILITIES },
    audit_context: { present: true, session_id: 's_demo', turn_id: 't_000', call_id: 'call_0' },
    tool_registry: REGISTRY,
    at: 0,
  };
}

function context(call, state, policy) {
  call = (call && typeof call === 'object') ? call : {};
  state = (state && typeof state === 'object') ? state : {};
  const tool = typeof call.tool === 'string' ? call.tool : '';
  const args = call.args === undefined || call.args === null ? {} : call.args;
  const registry = (state.tool_registry && typeof state.tool_registry === 'object')
    ? state.tool_registry : REGISTRY;
  let entry = Object.prototype.hasOwnProperty.call(registry, tool) ? registry[tool] : null;
  if (entry && typeof entry === 'object' && !('schema' in entry)) {
    entry = { ...entry, schema: (REGISTRY[tool] || {}).schema || {} };
  }
  const capsCfg = (state.capabilities && typeof state.capabilities === 'object')
    ? state.capabilities : DEFAULT_CAPABILITIES;
  const declared = Array.isArray(capsCfg.declared) ? [...capsCfg.declared] : [];
  const granted = Array.isArray(capsCfg.granted) ? [...capsCfg.granted] : declared;
  const ac = (state.audit_context && typeof state.audit_context === 'object')
    ? state.audit_context : {};
  let sat = state.saturated;
  if (typeof sat === 'boolean') sat = { valence: sat, arousal: sat, dominance: sat };
  if (!sat || typeof sat !== 'object') sat = {};
  const callId = String(call.id || call.call_id || ac.call_id || '');
  const auditPresent = Boolean(ac.present) && Boolean(ac.session_id)
    && Boolean(ac.turn_id) && Boolean(callId);
  const cls = (entry && entry.class) ? String(entry.class) : 'unknown';
  return {
    call, state, policy, callId, tool, args, registry, entry,
    toolClass: cls,
    toolMutating: Boolean(entry && entry.mutating) || MUTATING_CLASSES.includes(cls),
    caps: Array.isArray(entry && entry.caps) ? [...entry.caps] : [],
    declared, granted,
    variety: Boolean(state.variety_fallback),
    unresolved: Boolean(state.unresolvedConflict),
    phase: ['normal', 'shift', 'critical'].includes(state.phase) ? state.phase : 'unknown',
    frozenOk: state.frozen_ok === true,
    saturated: {
      valence: Boolean(sat.valence),
      arousal: Boolean(sat.arousal),
      dominance: Boolean(sat.dominance),
    },
    auditPresent, audit: ac,
    // docs/24 §7 的 T-UCA-11 读 state.audit_context_present（派生字段）
    stateView: { ...state, audit_context_present: auditPresent },
    confirmation: (state.confirmation && typeof state.confirmation === 'object')
      ? state.confirmation : null,
    paths: normalizedPaths(args),
    pathEscape: pathEscape(args),
    target: targetOf(args),
    argsBytes: Buffer.byteLength(canon(args), 'utf8'),
    strings: [...strings(args)],
    at: Number(state.at || 0),
    approvalChannel: Boolean(state.approval_channel || policy.approval_channel),
  };
}

// ── 谓词（与 engine/tool_gateway.py 的 _pred 一一对应） ──────────

function confirmationExact(ctx) {
  const conf = ctx.confirmation;
  if (!conf) return false;
  const target = String(conf.target || '');
  const token = String(conf.token || '');
  if (!target || !token) return false;
  if (ctx.target && target === ctx.target) return true;
  return ctx.paths.includes(resolveDotDot(normText(target).replace(/\\/g, '/')).toLowerCase())
    || target === ctx.tool;
}

function schemaInvalid(ctx) {
  const entry = ctx.entry;
  if (!entry) return false;
  const schema = entry.schema || {};
  const args = ctx.args;
  if (!schema || typeof schema !== 'object' || Array.isArray(schema)) return true;
  if (!args || typeof args !== 'object' || Array.isArray(args)) return true;
  const typeOk = (want, val) => {
    if (want === 'string') return typeof val === 'string';
    if (want === 'boolean') return typeof val === 'boolean';
    if (want === 'object') return val && typeof val === 'object' && !Array.isArray(val);
    if (want === 'array') return Array.isArray(val);
    if (want === 'number') return typeof val !== 'string' && typeof val !== 'boolean';
    return true;
  };
  for (const [field, raw] of Object.entries(schema)) {
    const spec = (raw && typeof raw === 'object') ? raw : { type: 'string', required: true };
    if (!(field in args)) { if (spec.required) return true; continue; }
    if (spec.type && !typeOk(spec.type, args[field])) return true;
  }
  if (!entry.allowUnknown) {
    for (const key of Object.keys(args)) if (!(key in schema)) return true;
  }
  return false;
}

function pred(value, ctx) {
  if (!value || typeof value !== 'object') return false;
  if ('all' in value) return (value.all || []).every((v) => pred(v, ctx));
  if ('any' in value) return (value.any || []).some((v) => pred(v, ctx));
  if ('not' in value) return !pred(value.not, ctx);
  if ('tool_mutating' in value) return ctx.toolMutating === Boolean(value.tool_mutating);
  if ('tool_class_any' in value) return (value.tool_class_any || []).includes(ctx.toolClass);
  if ('state' in value) return getPath(ctx.stateView, String(value.state)) === value.eq;
  if ('confirmation_exact' in value) {
    return confirmationExact(ctx) === Boolean(value.confirmation_exact);
  }
  if ('arg_true' in value) {
    const name = String(value.arg_true);
    for (const [kind, key, v] of walk(ctx.args)) {
      if (kind === 'key' && key === name) {
        if (v === true || v === 1) return true;
        if (typeof v === 'string' && ['true', '1', 'yes'].includes(v.trim().toLowerCase())) {
          return true;
        }
      }
    }
    return false;
  }
  if ('arg_regex' in value) {
    const rx = compilePattern(value.arg_regex);
    return ctx.strings.some((s) => rx.test(s)) || rx.test(ctx.tool);
  }
  if ('path_prefix_any' in value) {
    const prefixes = (value.path_prefix_any || []).map(
      (p) => normText(p).replace(/\\/g, '/').toLowerCase(),
    );
    for (const p of ctx.paths) {
      for (const pref of prefixes) {
        if (p === pref.replace(/\/+$/, '') || p.startsWith(pref)) return true;
      }
    }
    return false;
  }
  if ('path_escape' in value) return ctx.pathEscape === Boolean(value.path_escape);
  if ('capability' in value) return ctx.caps.includes(String(value.capability));
  if ('capability_any' in value) {
    return (value.capability_any || []).some((c) => ctx.caps.includes(c));
  }
  if ('capability_undeclared' in value) {
    const missing = ctx.caps.filter((c) => !ctx.declared.includes(c));
    return (missing.length > 0) === Boolean(value.capability_undeclared);
  }
  if ('capability_not_granted' in value) {
    const missing = ctx.caps.filter((c) => !ctx.granted.includes(c));
    return (missing.length > 0) === Boolean(value.capability_not_granted);
  }
  if ('args_bytes_gt' in value) return ctx.argsBytes > Number(value.args_bytes_gt);
  if ('json_depth_gt' in value) return maxDepth(ctx.args) > Number(value.json_depth_gt);
  if ('array_len_gt' in value) return maxArray(ctx.args) > Number(value.array_len_gt);
  if ('json_nodes_gt' in value) return countNodes(ctx.args) > Number(value.json_nodes_gt);
  if ('encoding_layers_gt' in value) {
    return ctx.strings.some((s) => encodingLayers(s) > Number(value.encoding_layers_gt));
  }
  if ('tool_unregistered' in value) {
    return (ctx.entry === null) === Boolean(value.tool_unregistered);
  }
  if ('registry_entry_incomplete' in value) {
    if (ctx.entry === null) return !value.registry_entry_incomplete;
    const missing = ['class', 'caps', 'required_args', 'path_arg', 'schema']
      .filter((k) => !(k in ctx.entry));
    return (missing.length > 0) === Boolean(value.registry_entry_incomplete);
  }
  if ('schema_invalid' in value) return schemaInvalid(ctx) === Boolean(value.schema_invalid);
  if ('tool_name_has' in value) {
    return (value.tool_name_has || []).some((t) => ctx.tool.includes(String(t)));
  }
  if ('tool_name_homoglyph_conflict' in value) {
    const norm = normText(ctx.tool);
    const conflict = norm !== ctx.tool
      && Object.prototype.hasOwnProperty.call(ctx.registry, norm);
    return conflict === Boolean(value.tool_name_homoglyph_conflict);
  }
  if ('audit_write_unavailable' in value) {
    return (ctx.state.audit_write_available === false)
      === Boolean(value.audit_write_unavailable);
  }
  return false;
}

// ── 主入口 ─────────────────────────────────────────────

function ruleOf(policy, rid) { return policy.rules.find((r) => r.id === rid) || null; }

export function formatBlockMessage(result, policy = POLICY) {
  const ucas = [...((result && result.ucas) || [])];
  const verdict = result && result.verdict;
  let head;
  if (verdict === 'block') head = '【STPA 工具前置闸·已拦截】本轮工具调用未执行，未产生任何副作用。';
  else if (verdict === 'degrade') head = '【STPA 工具前置闸·降级待确认】本轮工具调用被暂缓，需人工确认后才可重放。';
  else head = '【STPA 工具前置闸·放行】本轮工具调用已记录审计。';
  const lines = [head];
  for (const u of ucas) { const r = ruleOf(policy, u); if (r) lines.push('· ' + r.message_zh); }
  for (const u of ucas) { const r = ruleOf(policy, u); if (r) lines.push('  ' + r.message_en); }
  if (verdict === 'block') lines.push('[STPA tool pre-gate] call BLOCKED; no side effect executed.');
  else if (verdict === 'degrade') lines.push('[STPA tool pre-gate] call HELD for human confirmation.');
  else lines.push('[STPA tool pre-gate] call allowed; tool_gate audit recorded.');
  return lines.join('\n');
}

const SECRET_KEY_RE = /(api[_-]?key|apikey|secret|password|passwd|token|bearer|authorization|credential|private[_-]?key|session[_-]?cookie)/i;

// 审计预览里的值级脱敏（与 docs/24 T-UCA-02 同源；预览绝不落明文）
const SECRET_VALUE_RES = [
  /-----BEGIN [A-Z ]*PRIVATE KEY-----/g,
  /\bsk-[A-Za-z0-9_-]{6,}/g,
  /\bghp_[A-Za-z0-9]{6,}/g,
  /\bAKIA[0-9A-Z]{10,}/g,
  /\bxox[baprs]-[A-Za-z0-9-]{6,}/g,
  /\beyJ[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{2,}/g,
  /\b1[3-9]\d{9}\b/g,
  /\b\d{17}[\dXx]\b/g,
  /(password|passwd|token|secret|api[_-]?key)\s*[=:]\s*\S+/gi,
];

function scrub(text) {
  let out = String(text);
  for (const rx of SECRET_VALUE_RES) out = out.replace(rx, '***');
  return out;
}

function redact(obj) {
  if (Array.isArray(obj)) return obj.map(redact);
  if (obj && typeof obj === 'object') {
    const out = {};
    for (const [k, v] of Object.entries(obj)) out[k] = SECRET_KEY_RE.test(k) ? '***' : redact(v);
    return out;
  }
  return obj;
}

export function summarizeArgs(args, previewLimit = 120) {
  const safe = redact(args);
  const blob = scrub(canon(safe));
  return {
    digest: digest(safe),
    keys: (args && typeof args === 'object' && !Array.isArray(args))
      ? Object.keys(args).sort().slice(0, 20) : [],
    bytes: Buffer.byteLength(blob, 'utf8'),
    preview: blob.slice(0, previewLimit) + (blob.length > previewLimit ? '…' : ''),
  };
}

function toolGateEvent(ctx, policy, verdict, allowed, ucas, degradedTo, reason, extra) {
  const info = summarizeArgs(ctx.args);
  const event = {
    schema: SCHEMA,
    type: 'tool_gate',
    policy_version: policy.policy_version,
    at: ctx.at,
    session_id: ctx.audit.session_id === undefined ? null : ctx.audit.session_id,
    turn_id: ctx.audit.turn_id === undefined ? null : ctx.audit.turn_id,
    call_id: ctx.callId,
    tool: ctx.tool,
    tool_class: ctx.toolClass,
    verdict,
    allowed: Boolean(allowed),
    ucas,
    degraded_to: degradedTo,
    requires_confirmation: ucas.length > 0,
    reason,
    audit: {
      kind: 'tool_gate',
      args_digest: info.digest,
      args_keys: info.keys,
      args_bytes: info.bytes,
      args_preview: info.preview,
      phase: ctx.phase,
      variety_fallback: ctx.variety,
      frozen_ok: ctx.frozenOk,
      saturated: { ...ctx.saturated },
    },
    snapshot: { target: ctx.target, mtime: null, hash: null },
  };
  return extra ? Object.assign(event, extra) : event;
}

export function evaluate(call, state, policy = POLICY) {
  const st = state === undefined || state === null ? defaultState() : state;
  try {
    const ctx = context(call, st, policy);
    const hits = [];
    for (const rid of policy.evaluation_order) {
      const rule = ruleOf(policy, rid);
      if (rule && pred(rule.when, ctx)) hits.push(rule);
    }
    // docs/24 §4：phase=unknown 且 mutating → block（ucas 记 T-UCA-10）
    if (ctx.phase === 'unknown' && ctx.toolMutating
        && !hits.some((r) => r.id === 'T-UCA-10')) {
      const rule = ruleOf(policy, 'T-UCA-10');
      if (rule) hits.push(rule);
    }
    const blocks = hits.filter((r) => r.verdict === 'block').map((r) => r.id);
    const degrades = hits.filter((r) => r.verdict === 'degrade').map((r) => r.id);
    const order = (x) => { const i = policy.evaluation_order.indexOf(x); return i < 0 ? 99 : i; };
    const ucas = [...blocks, ...degrades].sort((a, b) => order(a) - order(b));
    let verdict;
    let degradedTo = null;
    if (blocks.length) verdict = 'block';
    else if (degrades.length) {
      if (ctx.approvalChannel) verdict = 'degrade';
      else { verdict = 'block'; degradedTo = 'block'; }
    } else verdict = 'allow';
    const allowed = verdict === 'allow';
    const reason = allowed ? 'ok:allow' : `${ucas[0]}:${REASON_CODES[ucas[0]] || 'rule'}`;
    const event = toolGateEvent(ctx, policy, verdict, allowed, ucas, degradedTo, reason);
    const result = {
      schema: SCHEMA,
      policy_version: policy.policy_version,
      call_id: ctx.callId,
      tool: ctx.tool,
      tool_class: ctx.toolClass,
      verdict,
      allowed,
      executed: allowed,
      degraded_to: degradedTo,
      requires_confirmation: ucas.length > 0,
      ucas,
      explain: ucas.map((u) => (ruleOf(policy, u) || {}).message_zh || ''),
      reason,
      state_inputs_used: [...new Set(hits.flatMap((r) => r.state_inputs || []))].sort(),
      audit: event,
      tool_gate: event,
    };
    result.message = formatBlockMessage(result, policy);
    return result;
  } catch (exc) {
    const rule = ruleOf(policy, 'T-UCA-11') || {};
    const ucas = ['T-UCA-11'];
    const reason = 'T-UCA-11:internal_error:' + ((exc && exc.name) || 'Error');
    const ctx = {
      args: {}, callId: '', tool: (call && call.tool) || '', toolClass: 'unknown',
      audit: {}, at: Number((st && st.at) || 0), phase: 'unknown',
      variety: null, frozenOk: null, saturated: {},
    };
    const event = toolGateEvent(ctx, policy, 'block', false, ucas, null, reason,
      { error: (exc && exc.name) || 'Error' });
    const out = {
      schema: SCHEMA, policy_version: policy.policy_version, call_id: '',
      tool: ctx.tool, tool_class: 'unknown', verdict: 'block', allowed: false,
      executed: false, degraded_to: null, requires_confirmation: true, ucas,
      explain: [rule.message_zh || '网关内部异常 → fail-closed'],
      reason, state_inputs_used: [], audit: event, tool_gate: event,
    };
    out.message = formatBlockMessage(out, policy);
    return out;
  }
}

export function evaluateBatch(calls, state, policy = POLICY) {
  return (calls || []).map((c) => evaluate(c, state, policy));
}

export function loadRegistry() { return JSON.parse(JSON.stringify(REGISTRY)); }

/**
 * 对**经它注册**的工具做真前置阻断（ADR-013 §2.3 唯一的产品级阻断路径）。
 * stateProvider: (args, ctx) => state；取不到状态 → 网关按 fail-closed 判定。
 */
export function guardTool(spec, stateProvider) {
  if (!spec || typeof spec !== 'object' || typeof spec.execute !== 'function') {
    throw new TypeError('guardTool(spec): spec.execute is required');
  }
  const name = spec.name || 'tool';
  const wrapped = { ...spec };
  wrapped.execute = async function execute(args, ctx = {}) {
    let state = {};
    try {
      state = typeof stateProvider === 'function'
        ? (stateProvider(args, ctx) || {}) : (stateProvider || {});
    } catch (err) { state = {}; }
    const call = {
      id: ctx.callId || ctx.call_id || `${name}#${ctx.turn_id || ctx.turnId || 't'}`,
      tool: name,
      args,
    };
    const res = evaluate(call, state, spec.policy || POLICY);
    if (res.verdict !== 'allow') {
      return {
        ok: false, blocked: res.verdict === 'block', held: res.verdict === 'degrade',
        verdict: res.verdict, ucas: res.ucas, degraded_to: res.degraded_to,
        message: formatBlockMessage(res), gateway: res,
        content: [{ type: 'text', text: formatBlockMessage(res) }],
      };
    }
    const out = await spec.execute.call(this, args, { ...ctx, gateway: res });
    return {
      ok: true, blocked: false, held: false, verdict: 'allow', gateway: res,
      content: [{ type: 'text', text: String(out) }],
    };
  };
  return wrapped;
}

export { POLICY_JSON };
