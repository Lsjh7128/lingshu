# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 工具调用 STPA 前置网关（v5.2 增量，ADR-013 / docs/24 v1.1）

定位：位于「模型输出 tool_use / function_call 解析之后、工具实际执行之前」的
**纯函数**控制点。不执行任何 payload、不做 IO、零依赖（仅标准库），
不可判定输入一律 **fail-closed**。

语义唯一来源：`docs/24-工具网关UCA.md` §7 的机器可读 `rules` 数组（v1.1，16 条）。
下方 `POLICY_JSON` 由 `tools/_sync_tool_policy.py` 从该文件**逐字注入**，不要手改；
`tests/test_tool_gateway.py` 会设 parity 断言。改规则 = 改 docs/24 → 重跑 sync → bump version。

契约要点（docs/24 v1.1）：
- `tool_class` 闭集：`read | write | destructive | exec | network`；
  mutating = write/destructive/exec/network；只读 = read。
- 判定顺序 = `policy["evaluation_order"]`；block > degrade > allow；多命中全部入 `ucas[]`。
- `degrade` **不执行原调用**；无 approval 通道 → block + `degraded_to="block"`。
- `phase=unknown` 且 mutating → block（ucas 记 T-UCA-10，docs/24 §4）。
- 审计事件 `tool_gate`：`schema/type/policy_version/at/session_id/turn_id/call_id/tool/
  tool_class/verdict/allowed/ucas/degraded_to/requires_confirmation/reason/
  audit{…}/snapshot{target,mtime,hash}`。

接口：
    evaluate(call, state=None, policy=None) -> dict
    evaluate_batch / format_block_message / summarize_args / default_state / load_registry
"""
from __future__ import annotations

import hashlib
import json
import posixpath
import re
import unicodedata

# ── 规则表（由 docs/24 §7 注入；不要手改） ────────────────────────
POLICY_JSON = '{\n  "policy_version": "t-uca-1.1",\n  "namespace": "T-UCA",\n  "default": {"unknown": "block", "read_only": "allow_with_log", "phase_unknown_mutating": "block"},\n  "evaluation_order": ["T-UCA-01","T-UCA-02","T-UCA-03","T-UCA-04","T-UCA-05","T-UCA-06","T-UCA-07","T-UCA-08","T-UCA-09","T-UCA-12","T-UCA-14","T-UCA-15","T-UCA-10","T-UCA-13","T-UCA-16","T-UCA-11"],\n  "rules": [\n    {"id":"T-UCA-01","verdict":"block",\n     "when":{"all":[{"tool_mutating":true},{"any":[{"state":"variety_fallback","eq":true},{"state":"unresolvedConflict","eq":true}]}]},\n     "args_check":[],\n     "state_inputs":["variety_fallback","unresolvedConflict"],\n     "test_id":"test_tuca_01_unresolved_write",\n     "message_zh":"STPA 前置闸拦截（T-UCA-01）：当前存在未澄清的记忆冲突，系统处于中性降维态。所有写/删/外发/执行类调用被拒绝。请先说明以哪一条记录为准。",\n     "message_en":"STPA pre-flight gate denied (T-UCA-01): an unresolved memory conflict keeps the system in variety-reduced mode. All mutating tool calls are blocked until the authoritative record is stated."},\n    {"id":"T-UCA-02","verdict":"block",\n     "when":{"any":[{"arg_regex":"(?i)(api[_-]?key|apikey|access[_-]?token|token|secret|password|passwd|credential|private[_-]?key|bearer|authorization|session[_-]?cookie|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9_\\\\-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9\\\\-]{10,}|eyJ[A-Za-z0-9_\\\\-]{10,}\\\\.[A-Za-z0-9_\\\\-]{10,}\\\\.|\\\\b1[3-9]\\\\d{9}\\\\b|\\\\b\\\\d{17}[\\\\dXx]\\\\b)"},{"arg_regex":"(?i)((^|/)(\\\\.env|id_rsa|id_ed25519|credentials(\\\\.json)?|\\\\.netrc|\\\\.pgpass)$|\\\\.(pem|key|pfx|p12)$)"},{"capability":"credentials"}]},\n     "args_check":[{"kind":"regex","over":"all_strings","max_len_bytes":65536}],\n     "state_inputs":["capabilities"],\n     "test_id":"test_tuca_02_secret_in_args",\n     "message_zh":"STPA 前置闸拦截（T-UCA-02）：参数包含凭证/密钥/令牌/PII 模式，或目标是凭证文件。网关不代管凭证，此类调用一律拒绝。",\n     "message_en":"STPA pre-flight gate denied (T-UCA-02): credentials, secrets or PII detected, or the target is a credential store. This gateway never handles secrets."},\n    {"id":"T-UCA-03","verdict":"block",\n     "when":{"all":[{"tool_class_any":["destructive","write"]},{"any":[{"arg_true":"force"},{"arg_true":"recursive"},{"arg_true":"overwrite"},{"arg_regex":"[*?]"},{"arg_regex":"(?i)(^|/)(rm|rmdir|del|truncate)(\\\\s|$)"}]},{"not":{"confirmation_exact":true}}]},\n     "args_check":[{"kind":"no_glob_or_recursive"}],\n     "state_inputs":[],\n     "test_id":"test_tuca_03_destructive_delete",\n     "message_zh":"STPA 前置闸拦截（T-UCA-03）：这是删除/覆盖类操作，未取得与你输入完全一致的确认。请给出单个精确路径并显式确认。",\n     "message_en":"STPA pre-flight gate denied (T-UCA-03): destructive delete/overwrite without an exact matching confirmation. Provide a single explicit path and confirm."},\n    {"id":"T-UCA-04","verdict":"block",\n     "when":{"any":[{"path_prefix_any":["rules/","engine/","plugin/","replay-set/","chaos-set/","acceptance/","decisions/","docs/01-宪法层.md","docs/15-验收清单.md","docs/24-工具网关UCA.md","_frozen/",".git/",".env"]},{"path_escape":true}]},\n     "args_check":[{"kind":"path_normalize","unicode":"NFKC","resolve_dotdot":true,"case_fold":true}],\n     "state_inputs":["frozen_ok"],\n     "test_id":"test_tuca_04_protected_path",\n     "message_zh":"STPA 前置闸拦截（T-UCA-04）：目标属于冻结/受保护区域（宪法、规则表、回放集、引擎与插件源码）。该区域只能由人工按 ADR 流程修改。",\n     "message_en":"STPA pre-flight gate denied (T-UCA-04): target is a frozen or protected path. It changes only through a human ADR process."},\n    {"id":"T-UCA-05","verdict":"block",\n     "when":{"any":[{"capability":"network"},{"arg_regex":"(?i)(https?://|webhook|ftp://|ssh://)"}]},\n     "args_check":[{"kind":"egress_allowlist","allowlist":[],"match":"exact_host_then_path_prefix"}],\n     "state_inputs":["capabilities"],\n     "test_id":"test_tuca_05_egress_blocked",\n     "message_zh":"STPA 前置闸拦截（T-UCA-05）：网络外发不在白名单内（默认空）。记忆与人格系统不申请网络能力；外发需人工放行。",\n     "message_en":"STPA pre-flight gate denied (T-UCA-05): egress is not allow-listed (empty by default). No network capability is requested; external sends need human approval."},\n    {"id":"T-UCA-06","verdict":"block",\n     "when":{"any":[{"capability_any":["shell","dynamic-code"]},{"tool_class_any":["exec"]},{"arg_regex":"(?i)(eval\\\\(|exec\\\\(|subprocess|os\\\\.system|child_process|bash\\\\s+-c|sh\\\\s+-c|pwsh\\\\s+-c|powershell\\\\s+-|node\\\\s+-e|python\\\\s+-c|`|\\\\$\\\\{)"}]},\n     "args_check":[],\n     "state_inputs":["capabilities"],\n     "test_id":"test_tuca_06_exec_blocked",\n     "message_zh":"STPA 前置闸拦截（T-UCA-06）：检测到代码执行能力或执行面参数。本系统不申请 shell/dynamic-code，执行类调用一律拒绝。",\n     "message_en":"STPA pre-flight gate denied (T-UCA-06): code-execution capability or execution-shaped arguments detected. Exec calls are always denied."},\n    {"id":"T-UCA-07","verdict":"block",\n     "when":{"any":[{"args_bytes_gt":65536},{"json_depth_gt":8},{"array_len_gt":1000},{"json_nodes_gt":5000},{"encoding_layers_gt":2}]},\n     "args_check":[{"kind":"shape_limits","max_bytes":65536,"max_depth":8,"max_array":1000,"max_nodes":5000,"max_encoding_layers":2}],\n     "state_inputs":[],\n     "test_id":"test_tuca_07_oversize",\n     "message_zh":"STPA 前置闸拦截（T-UCA-07）：参数体积/嵌套/编码层数超过网关上限。请拆分请求或减少嵌套。",\n     "message_en":"STPA pre-flight gate denied (T-UCA-07): payload exceeds size, depth or encoding-layer limits. Split the request or reduce nesting."},\n    {"id":"T-UCA-08","verdict":"block",\n     "when":{"any":[{"tool_unregistered":true},{"registry_entry_incomplete":true}]},\n     "args_check":[],\n     "state_inputs":["tool_registry"],\n     "test_id":"test_tuca_08_unregistered_tool",\n     "message_zh":"STPA 前置闸拦截（T-UCA-08）：该工具未登记，无法判定副作用类别。未登记工具一律拒绝。",\n     "message_en":"STPA pre-flight gate denied (T-UCA-08): tool not registered, side effects unknown. Unregistered tools are denied."},\n    {"id":"T-UCA-09","verdict":"block",\n     "when":{"any":[{"schema_invalid":true},{"arg_regex":"[\\\\u0000-\\\\u001F\\\\u2028\\\\u2029\\\\u200B-\\\\u200F\\\\u2060\\\\u202A-\\\\u202E]"},{"arg_regex":"(?i)(<\\\\|im_start\\\\|>|<\\\\|im_end\\\\|>|</system>|\\\\[INST\\\\]|\\\\[/INST\\\\]|\\\\n\\\\nHuman:|\\\\n\\\\nAssistant:)"},{"tool_name_has":["/","\\\\\\\\"," ",":",";","\\\\n"]},{"tool_name_homoglyph_conflict":true}]},\n     "args_check":[{"kind":"schema","reject_unknown_keys_unless_declared":true}],\n     "state_inputs":["tool_registry"],\n     "test_id":"test_tuca_09_schema_invalid",\n     "message_zh":"STPA 前置闸拦截（T-UCA-09）：参数不符合 schema，或含控制字符/分隔符/同形字等注入迹象。请提交干净参数。",\n     "message_en":"STPA pre-flight gate denied (T-UCA-09): arguments violate the tool schema or contain control characters, delimiters or homoglyphs. Submit clean arguments."},\n    {"id":"T-UCA-12","verdict":"block",\n     "when":{"any":[{"capability_undeclared":true},{"capability_not_granted":true}]},\n     "args_check":[],\n     "state_inputs":["capabilities"],\n     "test_id":"test_tuca_12_undeclared_capability",\n     "message_zh":"STPA 前置闸拦截（T-UCA-12）：该工具需要未声明或未授予的能力（网络/命令/凭证/动态代码）。最小能力原则下拒绝。",\n     "message_en":"STPA pre-flight gate denied (T-UCA-12): tool requires an undeclared or ungranted capability. Least-capability applies."},\n    {"id":"T-UCA-14","verdict":"block",\n     "when":{"all":[{"tool_mutating":true},{"state":"frozen_ok","eq":false}]},\n     "args_check":[],\n     "state_inputs":["frozen_ok"],\n     "test_id":"test_tuca_14_frozen_fail_blocks",\n     "message_zh":"STPA 前置闸拦截（T-UCA-14）：宪法冻结区校验未通过。完整性恢复前，写类调用一律拒绝。",\n     "message_en":"STPA pre-flight gate denied (T-UCA-14): frozen-region integrity check failed. Mutating calls are denied until restored."},\n    {"id":"T-UCA-10","verdict":"degrade",\n     "when":{"all":[{"state":"phase","eq":"critical"},{"tool_mutating":true}]},\n     "args_check":[],\n     "state_inputs":["phase"],\n     "test_id":"test_tuca_10_critical_write_degrade",\n     "message_zh":"STPA 前置闸降级（T-UCA-10）：当前上下文处于临界相位（≥0.85）。写类操作已暂缓，需确认目标与内容后再执行。",\n     "message_en":"STPA pre-flight gate held (T-UCA-10): context is in the critical phase (>=0.85). Mutating calls are paused for confirmation."},\n    {"id":"T-UCA-13","verdict":"degrade",\n     "when":{"all":[{"any":[{"state":"saturated.valence","eq":true},{"state":"saturated.arousal","eq":true},{"state":"saturated.dominance","eq":true}]},{"tool_class_any":["write","destructive"]},{"not":{"confirmation_exact":true}}]},\n     "args_check":[],\n     "state_inputs":["saturated"],\n     "test_id":"test_tuca_13_saturated_write_degrade",\n     "message_zh":"STPA 前置闸降级（T-UCA-13）：情感基线处于饱和状态，写/删类操作已暂缓，请确认后执行。",\n     "message_en":"STPA pre-flight gate held (T-UCA-13): affective baseline saturated; irreversible writes/deletes paused for confirmation."},\n    {"id":"T-UCA-11","verdict":"block",\n     "when":{"any":[{"state":"audit_context_present","eq":false},{"audit_write_unavailable":true}]},\n     "args_check":[],\n     "state_inputs":["audit_context"],\n     "test_id":"test_tuca_11_missing_audit_context",\n     "message_zh":"STPA 前置闸拦截（T-UCA-11）：本次调用缺少完整审计上下文，无法留痕。请修复会话/轮次标识后重试。",\n     "message_en":"STPA pre-flight gate denied (T-UCA-11): audit context incomplete; the call cannot be traced. Restore session/turn identifiers."},\n    {"id":"T-UCA-15","verdict":"block",\n     "when":{"any":[{"path_escape":true}]},\n     "args_check":[{"kind":"path_normalize","unicode":"NFKC","reject":["dotdot","absolute","drive_letter","unc","null_byte"]}],\n     "state_inputs":[],\n     "test_id":"test_tuca_15_path_escape",\n     "message_zh":"STPA 前置闸拦截（T-UCA-15）：参数中的路径试图逃出工作区（../、绝对路径、盘符、UNC 或空字节）。只允许工作区内的相对路径。",\n     "message_en":"STPA pre-flight gate denied (T-UCA-15): the path escapes the workspace. Only relative paths inside the workspace are allowed."},\n    {"id":"T-UCA-16","verdict":"degrade",\n     "when":{"all":[{"state":"phase","eq":"shift"},{"tool_class_any":["destructive"]}]},\n     "args_check":[],\n     "state_inputs":["phase"],\n     "test_id":"test_tuca_16_shift_destructive_degrade",\n     "message_zh":"STPA 前置闸降级（T-UCA-16）：上下文处于预警相位（≥0.70），删除/覆盖类操作已暂缓，请二次确认目标后再执行。",\n     "message_en":"STPA pre-flight gate held (T-UCA-16): context is in the warning phase (>=0.70). Destructive calls are paused for re-confirmation."}\n  ]\n}'

POLICY = json.loads(POLICY_JSON)
POLICY_VERSION = POLICY["policy_version"]
SCHEMA = "lingshu/tool-gate/v1"

MUTATING_CLASSES = ("write", "destructive", "exec", "network")
READONLY_CLASSES = ("read",)

# 工具登记表（内联；与 plugin/dsh-lingshu/tool-gateway.js 的 REGISTRY 同构，
# 由 tests/test_tool_gateway.py 做 parity 断言。字段：class/caps/required_args/path_arg）
REGISTRY = {
    "lingshu_status": {"class": "read", "caps": [], "required_args": [], "path_arg": None},
    "search_memory": {"class": "read", "caps": [], "required_args": ["query"],
                      "path_arg": None},
    "fs_read": {"class": "read", "caps": ["fs-read"], "required_args": ["path"],
                "path_arg": "path"},
    "list_dir": {"class": "read", "caps": ["fs-read"], "required_args": ["path"],
                 "path_arg": "path"},
    "fs_write": {"class": "write", "caps": ["fs-write"], "required_args": ["path", "content"],
                 "path_arg": "path"},
    "write_file": {"class": "write", "caps": ["fs-write"],
                   "required_args": ["path", "content"], "path_arg": "path"},
    "memory_write": {"class": "write", "caps": ["fs-write"],
                     "required_args": ["subject", "relation", "object"], "path_arg": None},
    "fs_delete": {"class": "destructive", "caps": ["fs-write"], "required_args": ["path"],
                  "path_arg": "path"},
    "delete": {"class": "destructive", "caps": ["fs-write"], "required_args": ["path"],
               "path_arg": "path"},
    "update_config": {"class": "write", "caps": ["fs-write"],
                      "required_args": ["path", "values"], "path_arg": "path"},
    "http_post": {"class": "network", "caps": ["network"], "required_args": ["url", "body"],
                  "path_arg": None},
    "run_shell": {"class": "exec", "caps": ["shell"], "required_args": ["cmd"],
                  "path_arg": None},
}

# 能力声明默认值（rules/capabilities.yaml 的 required / notRequested；纯函数不读文件）
DEFAULT_CAPABILITIES = {
    "declared": ["fs-read", "fs-write", "llm"],
    "granted": ["fs-read", "fs-write", "llm"],
    "notRequested": ["network", "shell", "credentials", "dynamic-code", "subagent", "env"],
}

REASON_CODES = {
    "T-UCA-01": "unresolved_write",
    "T-UCA-02": "secret_plaintext",
    "T-UCA-03": "destructive_no_confirm",
    "T-UCA-04": "protected_path_write",
    "T-UCA-05": "egress_not_allowlisted",
    "T-UCA-06": "code_exec",
    "T-UCA-07": "payload_shape_limit",
    "T-UCA-08": "tool_unregistered",
    "T-UCA-09": "schema_invalid",
    "T-UCA-10": "phase_critical_write",
    "T-UCA-11": "audit_context_missing",
    "T-UCA-12": "capability_undeclared",
    "T-UCA-13": "saturated_write",
    "T-UCA-14": "frozen_check_failed",
    "T-UCA-15": "path_escape",
    "T-UCA-16": "shift_destructive",
}

PATH_KEYS = frozenset(("path", "file", "filepath", "target", "dest", "destination",
                       "output", "dir", "directory", "src", "source", "from", "to"))
INLINE_FLAG = re.compile(r"^\(\?([aiLmsux]+)\)")
DRIVE_RE = re.compile(r"^[A-Za-z]:")
# 登记表 schema（docs/24 §2 要求 registry 带 schema；未知键一律拒绝，T-UCA-09）
SCHEMAS = {
    "lingshu_status": {},
    "search_memory": {"query": {"type": "string", "required": True}},
    "fs_read": {"path": {"type": "string", "required": True}},
    "list_dir": {"path": {"type": "string", "required": True}},
    "fs_write": {"path": {"type": "string", "required": True},
                 "content": {"type": "string", "required": True},
                 "overwrite": {"type": "boolean", "required": False}},
    "write_file": {"path": {"type": "string", "required": True},
                   "content": {"type": "string", "required": True},
                   "overwrite": {"type": "boolean", "required": False}},
    "memory_write": {"subject": {"type": "string", "required": True},
                     "relation": {"type": "string", "required": True},
                     "object": {"type": "string", "required": True}},
    "fs_delete": {"path": {"type": "string", "required": True},
                  "recursive": {"type": "boolean", "required": False},
                  "force": {"type": "boolean", "required": False}},
    "delete": {"path": {"type": "string", "required": True},
               "recursive": {"type": "boolean", "required": False},
               "force": {"type": "boolean", "required": False}},
    "update_config": {"path": {"type": "string", "required": True},
                      "values": {"type": "object", "required": True}},
    "http_post": {"url": {"type": "string", "required": True},
                  "body": {"type": "string", "required": True}},
    "run_shell": {"cmd": {"type": "string", "required": True}},
}


# registry 的 schema 与登记表合并（docs/24 §2：registry 必须带 schema；两处实现同构）
for _tool, _schema in SCHEMAS.items():
    if _tool in REGISTRY:
        REGISTRY[_tool]["schema"] = _schema


# ══════════════════════════════════════════════════════════
# 基础工具
# ══════════════════════════════════════════════════════════

def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _digest(obj) -> str:
    return "sha256:" + hashlib.sha256(_canon(obj).encode("utf-8")).hexdigest()[:16]


def _walk(obj, depth=0):
    if isinstance(obj, dict):
        for k, v in obj.items():
            yield ("key", str(k), v, depth + 1)
            yield from _walk(v, depth + 1)
    elif isinstance(obj, list):
        for v in obj:
            yield ("item", None, v, depth + 1)
            yield from _walk(v, depth + 1)


def _strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)


def _max_depth(obj) -> int:
    if isinstance(obj, dict):
        return 1 + max([_max_depth(v) for v in obj.values()] or [0])
    if isinstance(obj, list):
        return 1 + max([_max_depth(v) for v in obj] or [0])
    return 0


def _count_nodes(obj) -> int:
    if isinstance(obj, dict):
        return 1 + sum(_count_nodes(v) for v in obj.values())
    if isinstance(obj, list):
        return 1 + sum(_count_nodes(v) for v in obj)
    return 1


def _max_array(obj) -> int:
    best = len(obj) if isinstance(obj, list) else 0
    for v in (obj.values() if isinstance(obj, dict) else obj if isinstance(obj, list) else ()):
        best = max(best, _max_array(v))
    return best


def _compile(pattern: str):
    flags, p = 0, str(pattern)
    m = INLINE_FLAG.match(p)
    if m:
        for ch in m.group(1):
            flags |= {"i": re.I, "m": re.M, "s": re.S}.get(ch, 0)
        p = p[m.end():]
    return re.compile(p, flags)


def _norm_text(s) -> str:
    return unicodedata.normalize("NFKC", str(s))


def _resolve_dotdot(p: str) -> str:
    out = []
    for part in p.split("/"):
        if part == "" and not out:
            out.append("")
            continue
        if part == ".":
            continue
        if part == "..":
            if out and out[-1] not in ("..", ""):
                out.pop()
            else:
                out.append("..")
            continue
        out.append(part)
    return "/".join(out)


def _path_candidates(args) -> list[tuple[str, str]]:
    """(raw, normalized) 路径候选：key 属于 PATH_KEYS，或字符串本身含路径分隔符。"""
    out = []
    for kind, key, value, _d in _walk(args):
        if not isinstance(value, str) or not value:
            continue
        is_path = (kind == "key" and key is not None
                   and key.lower() in PATH_KEYS) or ("/" in value or "\\" in value)
        if not is_path:
            continue
        out.append((value, _resolve_dotdot(_norm_text(value).replace("\\", "/"))))
    return out


def _normalized_paths(args) -> list[str]:
    return [n.casefold() for _raw, n in _path_candidates(args)]


def _path_escape(args) -> bool:
    for raw, norm in _path_candidates(args):
        q = norm
        segs = q.split("/")
        if ".." in segs:
            return True
        if q.startswith("/") or q.startswith("//") or DRIVE_RE.match(q):
            return True
        if "\x00" in raw:
            return True
    return False


def _target_of(args) -> str:
    if isinstance(args, dict):
        for k in ("path", "file", "target", "dest", "destination", "url", "cmd"):
            v = args.get(k)
            if isinstance(v, str) and v:
                return _norm_text(v)
    return ""


def _encoding_layers(s: str) -> int:
    layers, cur = 0, str(s).strip()
    while layers < 8:
        low = cur.lower()
        if low.startswith("base64:"):
            cur, layers = cur[7:], layers + 1
        elif low.startswith("hex:"):
            cur, layers = cur[4:], layers + 1
        elif low.startswith("data:") and ";base64," in low:
            cur, layers = low.split(",", 1)[1], layers + 1
        elif low.startswith("%"):
            cur, layers = cur.replace("%25", "%").lstrip("%"), layers + 1
        else:
            break
    return layers


def _get_path(state, dotted: str):
    cur = state
    for part in str(dotted).split("."):
        if not isinstance(cur, dict) or part not in cur:
            return None
        cur = cur[part]
    return cur


def default_state() -> dict:
    """便利默认值（测试/演示用）；生产调用方必须传真实 VSM 状态。"""
    return {
        "variety_fallback": False,
        "unresolvedConflict": False,
        "phase": "normal",
        "saturated": {"valence": False, "arousal": False, "dominance": False},
        "frozen_ok": True,
        "capabilities": dict(DEFAULT_CAPABILITIES),
        "audit_context": {"present": True, "session_id": "s_demo", "turn_id": "t_000",
                          "call_id": "call_0"},
        "tool_registry": REGISTRY,
        "at": 0.0,
    }


def _context(call, state, policy) -> dict:
    call = call if isinstance(call, dict) else {}
    state = state if isinstance(state, dict) else {}
    tool = call.get("tool")
    tool = tool if isinstance(tool, str) else ""
    args = call.get("args")
    if args is None:
        args = {}
    registry = state.get("tool_registry")
    if not isinstance(registry, dict):
        registry = REGISTRY
    entry = registry.get(tool)
    if isinstance(entry, dict) and "schema" not in entry:
        entry = {**entry, "schema": SCHEMAS.get(tool, {})}
    caps_cfg = state.get("capabilities")
    if not isinstance(caps_cfg, dict):
        caps_cfg = DEFAULT_CAPABILITIES
    declared = list(caps_cfg.get("declared") or [])
    granted = list(caps_cfg.get("granted") if caps_cfg.get("granted") is not None
                   else declared)
    ac = state.get("audit_context")
    if not isinstance(ac, dict):
        ac = {}
    sat = state.get("saturated")
    if isinstance(sat, bool):
        sat = {"valence": sat, "arousal": sat, "dominance": sat}
    if not isinstance(sat, dict):
        sat = {}
    call_id = str(call.get("id") or call.get("call_id") or ac.get("call_id") or "")
    audit_present = bool(ac.get("present")) and bool(ac.get("session_id")) and \
        bool(ac.get("turn_id")) and bool(call_id)
    cls = str((entry or {}).get("class") or "unknown")
    return {
        "call": call, "state": state, "policy": policy,
        "call_id": call_id, "tool": tool, "args": args, "registry": registry,
        "entry": entry, "tool_class": cls,
        "tool_mutating": bool((entry or {}).get("mutating")) or cls in MUTATING_CLASSES,
        "caps": list((entry or {}).get("caps") or []),
        "declared": declared, "granted": granted,
        "variety_fallback": bool(state.get("variety_fallback")),
        "unresolvedConflict": bool(state.get("unresolvedConflict")),
        "phase": state.get("phase") if state.get("phase") in
                 ("normal", "shift", "critical") else "unknown",
        "frozen_ok": state.get("frozen_ok") is True,
        "saturated": {k: bool(sat.get(k)) for k in ("valence", "arousal", "dominance")},
        "saturated_any": any(bool(v) for v in sat.values()),
        "audit_present": audit_present, "audit": ac,
        # docs/24 §7 的 T-UCA-11 读 state.audit_context_present（派生字段）
        "state_view": {**state, "audit_context_present": audit_present},
        "confirmation": state.get("confirmation")
                        if isinstance(state.get("confirmation"), dict) else None,
        "paths": _normalized_paths(args), "path_escape": _path_escape(args),
        "target": _target_of(args), "args_bytes": len(_canon(args).encode("utf-8")),
        "strings": list(_strings(args)), "at": float(state.get("at") or 0.0),
        "approval_channel": bool(state.get("approval_channel")
                                 or policy.get("approval_channel")),
    }


# ══════════════════════════════════════════════════════════
# `when` DSL 解释器（谓词名 = docs/24 §7）
# ══════════════════════════════════════════════════════════

def _confirmation_exact(ctx) -> bool:
    conf = ctx["confirmation"]
    if not conf:
        return False
    target, token = str(conf.get("target") or ""), str(conf.get("token") or "")
    if not target or not token:
        return False
    if ctx["target"] and target == ctx["target"]:
        return True
    return target in ctx["paths"] or target == ctx["tool"]


def _schema_invalid(ctx) -> bool:
    entry = ctx["entry"]
    if entry is None:
        return False
    schema = entry.get("schema") or {}
    args = ctx["args"]
    if not isinstance(schema, dict) or not isinstance(args, dict):
        return True
    for field, rawspec in schema.items():
        spec = rawspec if isinstance(rawspec, dict) else {"type": "string",
                                                          "required": True}
        if field not in args:
            if spec.get("required"):
                return True
            continue
        want, val = spec.get("type"), args[field]
        if want == "string" and not isinstance(val, str):
            return True
        if want == "boolean" and not isinstance(val, bool):
            return True
        if want == "object" and not isinstance(val, dict):
            return True
        if want == "array" and not isinstance(val, list):
            return True
        if want == "number" and isinstance(val, (str, bool)):
            return True
    if not entry.get("allowUnknown"):
        for key in args:
            if key not in schema:
                return True
    return False


def _pred(value, ctx) -> bool:
    if not isinstance(value, dict):
        return False
    if "all" in value:
        return all(_pred(v, ctx) for v in value["all"])
    if "any" in value:
        return any(_pred(v, ctx) for v in value["any"])
    if "not" in value:
        return not _pred(value["not"], ctx)
    if "tool_mutating" in value:
        return ctx["tool_mutating"] == bool(value["tool_mutating"])
    if "tool_class_any" in value:
        return ctx["tool_class"] in (value["tool_class_any"] or [])
    if "state" in value:
        return _get_path(ctx["state_view"], str(value["state"])) == value.get("eq")
    if "confirmation_exact" in value:
        return _confirmation_exact(ctx) == bool(value["confirmation_exact"])
    if "arg_true" in value:
        name = str(value["arg_true"])
        for kind, key, v, _d in _walk(ctx["args"]):
            if kind == "key" and key == name:
                if v is True or v == 1:
                    return True
                if isinstance(v, str) and v.strip().lower() in ("true", "1", "yes"):
                    return True
        return False
    if "arg_regex" in value:
        rx = _compile(str(value["arg_regex"]))
        return any(rx.search(s) for s in ctx["strings"]) or bool(rx.search(ctx["tool"]))
    if "path_prefix_any" in value:
        prefixes = [_norm_text(p).replace("\\", "/").casefold()
                    for p in (value["path_prefix_any"] or [])]
        for p in ctx["paths"]:
            for pref in prefixes:
                if p == pref.rstrip("/") or p.startswith(pref):
                    return True
        return False
    if "path_escape" in value:
        return ctx["path_escape"] == bool(value["path_escape"])
    if "capability" in value:
        return str(value["capability"]) in ctx["caps"]
    if "capability_any" in value:
        return any(c in ctx["caps"] for c in (value["capability_any"] or []))
    if "capability_undeclared" in value:
        missing = [c for c in ctx["caps"] if c not in ctx["declared"]]
        return bool(missing) == bool(value["capability_undeclared"])
    if "capability_not_granted" in value:
        missing = [c for c in ctx["caps"] if c not in ctx["granted"]]
        return bool(missing) == bool(value["capability_not_granted"])
    if "args_bytes_gt" in value:
        return ctx["args_bytes"] > int(value["args_bytes_gt"])
    if "json_depth_gt" in value:
        return _max_depth(ctx["args"]) > int(value["json_depth_gt"])
    if "array_len_gt" in value:
        return _max_array(ctx["args"]) > int(value["array_len_gt"])
    if "json_nodes_gt" in value:
        return _count_nodes(ctx["args"]) > int(value["json_nodes_gt"])
    if "encoding_layers_gt" in value:
        return any(_encoding_layers(s) > int(value["encoding_layers_gt"])
                   for s in ctx["strings"])
    if "tool_unregistered" in value:
        return (ctx["entry"] is None) == bool(value["tool_unregistered"])
    if "registry_entry_incomplete" in value:
        if ctx["entry"] is None:
            return not bool(value["registry_entry_incomplete"])
        missing = [k for k in ("class", "caps", "required_args", "path_arg", "schema")
                   if k not in ctx["entry"]]
        return bool(missing) == bool(value["registry_entry_incomplete"])
    if "schema_invalid" in value:
        return _schema_invalid(ctx) == bool(value["schema_invalid"])
    if "tool_name_has" in value:
        return any(str(t) in ctx["tool"] for t in (value["tool_name_has"] or []))
    if "tool_name_homoglyph_conflict" in value:
        norm = _norm_text(ctx["tool"])
        conflict = norm != ctx["tool"] and norm in ctx["registry"]
        return conflict == bool(value["tool_name_homoglyph_conflict"])
    if "audit_write_unavailable" in value:
        return (ctx["state"].get("audit_write_available") is False) == \
            bool(value["audit_write_unavailable"])
    return False


# ══════════════════════════════════════════════════════════
# 主入口
# ══════════════════════════════════════════════════════════

def _rule(policy, rid):
    return next((r for r in policy["rules"] if r["id"] == rid), None)


def format_block_message(result: dict, policy=None) -> str:
    """强提示文案（中英双语，来自 docs/24 §7；不拼接 payload 原文）。"""
    policy = policy or POLICY
    ucas = list(result.get("ucas") or [])
    verdict = result.get("verdict")
    if verdict == "block":
        head = "【STPA 工具前置闸·已拦截】本轮工具调用未执行，未产生任何副作用。"
    elif verdict == "degrade":
        head = "【STPA 工具前置闸·降级待确认】本轮工具调用被暂缓，需人工确认后才可重放。"
    else:
        head = "【STPA 工具前置闸·放行】本轮工具调用已记录审计。"
    lines = [head]
    for u in ucas:
        r = _rule(policy, u)
        if r:
            lines.append("· " + r["message_zh"])
    for u in ucas:
        r = _rule(policy, u)
        if r:
            lines.append("  " + r["message_en"])
    if verdict == "block":
        lines.append("[STPA tool pre-gate] call BLOCKED; no side effect executed.")
    elif verdict == "degrade":
        lines.append("[STPA tool pre-gate] call HELD for human confirmation.")
    else:
        lines.append("[STPA tool pre-gate] call allowed; tool_gate audit recorded.")
    return "\n".join(lines)


_SECRET_KEY_RE = re.compile(
    r"(api[_-]?key|apikey|secret|password|passwd|token|bearer|authorization|"
    r"credential|private[_-]?key|session[_-]?cookie)", re.I)

# 审计预览里的值级脱敏（与 docs/24 T-UCA-02 的凭证/PII 模式同源；预览绝不落明文）
_SECRET_VALUE_RES = (
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{6,}"),
    re.compile(r"\bghp_[A-Za-z0-9]{6,}"),
    re.compile(r"\bAKIA[0-9A-Z]{10,}"),
    re.compile(r"\bxox[baprs]-[A-Za-z0-9\-]{6,}"),
    re.compile(r"\beyJ[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{6,}\.[A-Za-z0-9_\-]{2,}"),
    re.compile(r"\b1[3-9]\d{9}\b"),
    re.compile(r"\b\d{17}[\dXx]\b"),
    re.compile(r"(?i)(password|passwd|token|secret|api[_-]?key)\s*[=:]\s*\S+"),
)


def _scrub(text: str) -> str:
    for rx in _SECRET_VALUE_RES:
        text = rx.sub("***", text)
    return text


def _redact(obj):
    if isinstance(obj, dict):
        return {k: ("***" if _SECRET_KEY_RE.search(str(k)) else _redact(v))
                for k, v in obj.items()}
    if isinstance(obj, list):
        return [_redact(v) for v in obj]
    return obj


def summarize_args(args, preview_limit: int = 120) -> dict:
    safe = _redact(args)
    blob = _scrub(_canon(safe))
    return {"digest": _digest(safe),
            "keys": sorted(args.keys())[:20] if isinstance(args, dict) else [],
            "bytes": len(blob.encode("utf-8")),
            "preview": blob[:preview_limit] + ("…" if len(blob) > preview_limit else "")}


def _tool_gate_event(ctx, policy, verdict, allowed, ucas, degraded_to, reason,
                     status_extra=None) -> dict:
    info = summarize_args(ctx["args"])
    event = {
        "schema": SCHEMA,
        "type": "tool_gate",
        "policy_version": policy["policy_version"],
        "at": ctx["at"],
        "session_id": ctx["audit"].get("session_id"),
        "turn_id": ctx["audit"].get("turn_id"),
        "call_id": ctx["call_id"],
        "tool": ctx["tool"],
        "tool_class": ctx["tool_class"],
        "verdict": verdict,
        "allowed": bool(allowed),
        "ucas": ucas,
        "degraded_to": degraded_to,
        "requires_confirmation": bool(ucas),
        "reason": reason,
        "audit": {
            "kind": "tool_gate",
            "args_digest": info["digest"],
            "args_keys": info["keys"],
            "args_bytes": info["bytes"],
            "args_preview": info["preview"],
            "phase": ctx["phase"],
            "variety_fallback": ctx["variety_fallback"],
            "frozen_ok": ctx["frozen_ok"],
            "saturated": dict(ctx["saturated"]),
        },
        "snapshot": {"target": ctx.get("target", ""), "mtime": None, "hash": None},
    }
    if status_extra:
        event.update(status_extra)
    return event


def evaluate(call, state=None, policy=None) -> dict:
    """纯函数判定。**任何路径都不抛异常**（异常 → fail-closed block，记 T-UCA-11）。"""
    pol = POLICY if policy is None else policy
    st = default_state() if state is None else state
    try:
        ctx = _context(call, st, pol)
        hits = []
        for rid in pol["evaluation_order"]:
            rule = _rule(pol, rid)
            if rule and _pred(rule.get("when"), ctx):
                hits.append(rule)
        # docs/24 §4：phase=unknown 且 mutating → block（ucas 记 T-UCA-10）
        if (ctx["phase"] == "unknown" and ctx["tool_mutating"]
                and all(r["id"] != "T-UCA-10" for r in hits)):
            rule = _rule(pol, "T-UCA-10")
            if rule:
                hits.append(rule)
        blocks = [r["id"] for r in hits if r["verdict"] == "block"]
        degrades = [r["id"] for r in hits if r["verdict"] == "degrade"]
        order = pol["evaluation_order"]
        ucas = sorted(blocks + degrades, key=lambda x: order.index(x) if x in order else 99)
        degraded_to = None
        if blocks:
            verdict = "block"
        elif degrades:
            if ctx["approval_channel"]:
                verdict = "degrade"
            else:
                verdict, degraded_to = "block", "block"
        else:
            verdict = "allow"
        allowed = verdict == "allow"
        reason = "ok:allow" if verdict == "allow" else "%s:%s" % (
            ucas[0], REASON_CODES.get(ucas[0], "rule"))
        event = _tool_gate_event(ctx, pol, verdict, allowed, ucas, degraded_to, reason)
        result = {
            "schema": SCHEMA,
            "policy_version": pol["policy_version"],
            "call_id": ctx["call_id"],
            "tool": ctx["tool"],
            "tool_class": ctx["tool_class"],
            "verdict": verdict,
            "allowed": allowed,
            "executed": allowed,          # degrade/block 都不执行原调用
            "degraded_to": degraded_to,
            "requires_confirmation": bool(ucas),
            "ucas": ucas,
            "explain": [(_rule(pol, u) or {}).get("message_zh", "") for u in ucas],
            "reason": reason,
            "state_inputs_used": sorted({s for r in hits
                                         for s in (r.get("state_inputs") or [])}),
            "audit": event,
            "tool_gate": event,
        }
        result["message"] = format_block_message(result, pol)
        return result
    except Exception as exc:                                    # noqa: BLE001
        rule = _rule(pol, "T-UCA-11") or {}
        ctx = {"args": {}, "call_id": "", "tool": (call or {}).get("tool", "")
               if isinstance(call, dict) else "", "tool_class": "unknown",
               "audit": {}, "at": float((st or {}).get("at") or 0.0) if isinstance(st, dict)
               else 0.0, "phase": "unknown", "variety_fallback": None,
               "frozen_ok": None, "saturated": {}, "target": "", "paths": [],
               "confirmation": None, "state_view": {}}
        ucas = ["T-UCA-11"]
        reason = "T-UCA-11:internal_error:%s" % type(exc).__name__
        event = _tool_gate_event(ctx, pol, "block", False, ucas, None, reason,
                                 {"error": type(exc).__name__})
        out = {
            "schema": SCHEMA, "policy_version": pol["policy_version"], "call_id": "",
            "tool": ctx["tool"], "tool_class": "unknown", "verdict": "block",
            "allowed": False, "executed": False, "degraded_to": None,
            "requires_confirmation": True, "ucas": ucas,
            "explain": [rule.get("message_zh", "网关内部异常 → fail-closed")],
            "reason": reason, "state_inputs_used": [], "audit": event, "tool_gate": event,
        }
        out["message"] = format_block_message(out, pol)
        return out


def evaluate_batch(calls, state=None, policy=None) -> list[dict]:
    """批量判定（同一状态）；不短路，便于一次审计多个候选调用。"""
    return [evaluate(c, state, policy) for c in (calls or ())]


def preflight_tool_call(call, state=None, policy=None) -> dict:
    """别名：与 `control.preflight_audit`（注入侧）并列的**工具侧**前置闸入口。"""
    return evaluate(call, state, policy)


def load_registry() -> dict:
    return json.loads(json.dumps(REGISTRY))


__all__ = ["POLICY_VERSION", "SCHEMA", "POLICY", "REGISTRY", "SCHEMAS",
           "DEFAULT_CAPABILITIES", "REASON_CODES", "default_state", "evaluate",
           "evaluate_batch", "preflight_tool_call", "format_block_message",
           "summarize_args", "load_registry"]
