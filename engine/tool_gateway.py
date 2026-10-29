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
POLICY_JSON = '{\n  "policy_version": "t-uca-1.2",\n  "namespace": "T-UCA",\n  "default": {\n    "unknown": "block",\n    "read_only": "allow_with_log",\n    "phase_unknown_mutating": "block"\n  },\n  "egress_allowlist": [\n    "github.com",\n    "api.github.com",\n    "raw.githubusercontent.com",\n    "objects.githubusercontent.com",\n    "npmjs.org",\n    "registry.npmjs.org",\n    "pypi.org",\n    "files.pythonhosted.org",\n    "python.org",\n    "docs.python.org",\n    "nodejs.org",\n    "developer.mozilla.org"\n  ],\n  "evaluation_order": [\n    "T-UCA-01",\n    "T-UCA-02",\n    "T-UCA-03",\n    "T-UCA-04",\n    "T-UCA-05",\n    "T-UCA-06",\n    "T-UCA-07",\n    "T-UCA-08",\n    "T-UCA-09",\n    "T-UCA-12",\n    "T-UCA-14",\n    "T-UCA-15",\n    "T-UCA-10",\n    "T-UCA-13",\n    "T-UCA-16",\n    "T-UCA-11"\n  ],\n  "rules": [\n    {\n      "id": "T-UCA-01",\n      "verdict": "block",\n      "when": {\n        "all": [\n          {\n            "tool_mutating": true\n          },\n          {\n            "any": [\n              {\n                "state": "variety_fallback",\n                "eq": true\n              },\n              {\n                "state": "unresolvedConflict",\n                "eq": true\n              }\n            ]\n          }\n        ]\n      },\n      "args_check": [],\n      "state_inputs": [\n        "variety_fallback",\n        "unresolvedConflict"\n      ],\n      "test_id": "test_tuca_01_unresolved_write",\n      "message_zh": "STPA 前置闸拦截（T-UCA-01）：当前存在未澄清的记忆冲突，系统处于中性降维态。所有写/删/外发/执行类调用被拒绝。请先说明以哪一条记录为准。",\n      "message_en": "STPA pre-flight gate denied (T-UCA-01): an unresolved memory conflict keeps the system in variety-reduced mode. All mutating tool calls are blocked until the authoritative record is stated."\n    },\n    {\n      "id": "T-UCA-02",\n      "verdict": "block",\n      "when": {\n        "any": [\n          {\n            "arg_regex": "(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9_\\\\-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9\\\\-]{10,}|eyJ[A-Za-z0-9_\\\\-]{10,}\\\\.[A-Za-z0-9_\\\\-]{10,}\\\\.)"\n          },\n          {\n            "arg_regex": "(?i)\\\\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|client[_-]?secret|secret|password|passwd)\\\\b\\\\s*[=:]\\\\s*[\\"\']?[A-Za-z0-9/+=_\\\\-]{4,}"\n          },\n          {\n            "sensitive_path": true\n          }\n        ]\n      },\n      "args_check": [\n        {\n          "kind": "regex",\n          "over": "all_strings",\n          "scope": "secret_value_and_assignment"\n        },\n        {\n          "kind": "credential_path",\n          "over": "path_args",\n          "reject": [\n            "dotenv",\n            "ssh_dir",\n            "private_key",\n            "credential_store"\n          ]\n        }\n      ],\n      "state_inputs": [\n        "capabilities"\n      ],\n      "test_id": "test_tuca_02_secret_in_args",\n      "message_zh": "STPA 前置闸拦截（T-UCA-02）：参数里出现高置信度凭证值（私钥/sk-/ghp_/AKIA/JWT）、凭证赋值（api_key=…、password: …），或目标路径属于凭证库（.env/.ssh/id_rsa/*.pem）。仅出现 token/password 这类词不等于凭证。",\n      "message_en": "STPA pre-flight gate denied (T-UCA-02): a high-confidence credential value, a credential assignment, or a credential-store path was detected. The bare words token/password are not treated as credentials."\n    },\n    {\n      "id": "T-UCA-03",\n      "verdict": "degrade",\n      "when": {\n        "all": [\n          {\n            "tool_class_any": [\n              "destructive"\n            ]\n          },\n          {\n            "any": [\n              {\n                "arg_true": "force"\n              },\n              {\n                "arg_true": "recursive"\n              },\n              {\n                "path_glob": true\n              },\n              {\n                "path_prefix_any": [\n                  "/",\n                  "//",\n                  "*",\n                  "c:/windows",\n                  "c:/program files",\n                  "c:/programdata"\n                ]\n              },\n              {\n                "arg_regex": "(?i)(^|[/\\\\\\\\])(rm|rmdir|del|deltree|truncate|format)(\\\\s|$)"\n              }\n            ]\n          },\n          {\n            "not": {\n              "confirmation_exact": true\n            }\n          }\n        ]\n      },\n      "args_check": [\n        {\n          "kind": "no_glob_or_recursive",\n          "scope": "path_args"\n        }\n      ],\n      "state_inputs": [],\n      "test_id": "test_tuca_03_destructive_delete",\n      "message_zh": "STPA 前置闸降级（T-UCA-03）：删除/覆盖类调用命中高风险形态（通配符、根目录或系统目录、递归/强制标志、rm/del/truncate 命令），需人工确认后再执行。普通单文件删除（如 rm file.tmp）不再触发。",\n      "message_en": "STPA pre-flight gate degraded (T-UCA-03): delete/overwrite hits a high-risk shape (wildcard, root/system dir, recursive/force flag, rm/del/truncate). Plain single-file deletes are no longer flagged."\n    },\n    {\n      "id": "T-UCA-04",\n      "verdict": "block",\n      "when": {\n        "all": [\n          {\n            "tool_mutating": true\n          },\n          {\n            "any": [\n              {\n                "path_prefix_any": [\n                  "rules/",\n                  "engine/",\n                  "plugin/",\n                  "replay-set/",\n                  "chaos-set/",\n                  "acceptance/",\n                  "decisions/",\n                  "docs/01-宪法层.md",\n                  "docs/15-验收清单.md",\n                  "docs/24-工具网关UCA.md",\n                  "_frozen/",\n                  ".git/",\n                  ".env",\n                  "c:/windows"\n                ]\n              },\n              {\n                "path_escape": true\n              }\n            ]\n          }\n        ]\n      },\n      "args_check": [\n        {\n          "kind": "path_normalize",\n          "unicode": "NFKC",\n          "resolve_dotdot": true,\n          "case_fold": true\n        }\n      ],\n      "state_inputs": [\n        "frozen_ok"\n      ],\n      "test_id": "test_tuca_04_protected_path",\n      "message_zh": "STPA 前置闸拦截（T-UCA-04）：写/删操作的目标属于冻结/受保护区域（宪法、规则表、回放集、引擎与插件源码）。只读访问不受此规则限制；修改只能由人工按 ADR 流程进行。",\n      "message_en": "STPA pre-flight gate denied (T-UCA-04): a mutating call targets a frozen/protected area. Read-only access is unaffected; changes go through the human ADR process."\n    },\n    {\n      "id": "T-UCA-05",\n      "verdict": "block",\n      "when": {\n        "any": [\n          {\n            "capability": "network"\n          },\n          {\n            "all": [\n              {\n                "url_present": true\n              },\n              {\n                "url_host_allowlisted": false\n              }\n            ]\n          }\n        ]\n      },\n      "args_check": [\n        {\n          "kind": "egress_allowlist",\n          "source": "policy.egress_allowlist",\n          "match": "exact_host_or_subdomain_then_path_prefix"\n        }\n      ],\n      "state_inputs": [\n        "capabilities"\n      ],\n      "test_id": "test_tuca_05_egress_blocked",\n      "message_zh": "STPA 前置闸拦截（T-UCA-05）：网络外发目标不在 Egress 白名单内（默认允许 github.com / npmjs.org / pypi.org 等开发文档站）。只出现在正文里的 URL 文本不算外发；`web_fetch` 等取回类工具按目标域名判定。",\n      "message_en": "STPA pre-flight gate denied (T-UCA-05): egress target is not on the allowlist (dev docs: github.com / npmjs.org / pypi.org, ...). URL text inside file content is not egress; fetch-style tools are judged by target host."\n    },\n    {\n      "id": "T-UCA-06",\n      "verdict": "block",\n      "when": {\n        "any": [\n          {\n            "capability_any": [\n              "dynamic-code"\n            ]\n          },\n          {\n            "all": [\n              {\n                "tool_class_any": [\n                  "exec"\n                ]\n              },\n              {\n                "arg_regex_cmd": "(?i)(\\\\bcurl\\\\b|\\\\bwget\\\\b|\\\\biwr\\\\b|invoke-webrequest|invoke-expression|\\\\biex\\\\b|\\\\bbash\\\\s+-i\\\\b|\\\\bsh\\\\s+-i\\\\b|\\\\bncat?\\\\b|\\\\bsocat\\\\b|/dev/tcp/|\\\\bpowershell(\\\\.exe)?\\\\b[^|;]*(-enc|-encodedcommand|-windowstyle\\\\s+hidden)|\\\\bsetx\\\\b|\\\\breg\\\\s+add\\\\b|setenvironmentvariable|\\\\bexport\\\\s+[A-Za-z_][A-Za-z0-9_]*=|\\\\bchmod\\\\s+777\\\\b|\\\\brm\\\\s+-rf\\\\s+/(\\\\s|$)|\\\\bsudo\\\\b|\\\\bnet\\\\s+user\\\\b|\\\\bsc\\\\s+create\\\\b|\\\\|\\\\s*(sh|bash|iex|powershell|python|node)\\\\b|\\\\bsubprocess\\\\b|\\\\bchild_process\\\\b|\\\\beval\\\\s*\\\\(|\\\\bexec\\\\s*\\\\(|\\\\bos\\\\.system\\\\b|\\\\bscp\\\\s+[^ ]+@)"\n              }\n            ]\n          }\n        ]\n      },\n      "args_check": [\n        {\n          "kind": "exec_shape",\n          "over": "command_args",\n          "reject": [\n            "download",\n            "reverse_shell",\n            "encoded_command",\n            "global_env",\n            "pipe_to_shell"\n          ]\n        }\n      ],\n      "state_inputs": [\n        "capabilities"\n      ],\n      "test_id": "test_tuca_06_exec_blocked",\n      "message_zh": "STPA 前置闸拦截（T-UCA-06）：执行类调用命中高危命令形态（curl/wget/iwr、反向 shell、编码执行、管道进 shell、改全局环境变量、subprocess/eval/exec）。常规 `npm run test`、`python scripts/run.py` 直接放行；源码正文里出现这些词不算执行。",\n      "message_en": "STPA pre-flight gate denied (T-UCA-06): exec call matches a dangerous command shape (curl/wget/iwr, reverse shell, encoded command, pipe-to-shell, global env mutation, subprocess/eval/exec). Ordinary `npm run test` / `python scripts/run.py` pass; those words inside source content are not exec."\n    },\n    {\n      "id": "T-UCA-07",\n      "verdict": "block",\n      "when": {\n        "any": [\n          {\n            "args_bytes_gt": 65536\n          },\n          {\n            "json_depth_gt": 8\n          },\n          {\n            "array_len_gt": 1000\n          },\n          {\n            "json_nodes_gt": 5000\n          },\n          {\n            "encoding_layers_gt": 2\n          }\n        ]\n      },\n      "args_check": [\n        {\n          "kind": "shape_limits",\n          "max_bytes": 65536,\n          "max_depth": 8,\n          "max_array": 1000,\n          "max_nodes": 5000,\n          "max_encoding_layers": 2\n        }\n      ],\n      "state_inputs": [],\n      "test_id": "test_tuca_07_oversize",\n      "message_zh": "STPA 前置闸拦截（T-UCA-07）：参数体积/嵌套/编码层数超过网关上限。请拆分请求或减少嵌套。",\n      "message_en": "STPA pre-flight gate denied (T-UCA-07): payload exceeds size, depth or encoding-layer limits. Split the request or reduce nesting."\n    },\n    {\n      "id": "T-UCA-08",\n      "verdict": "block",\n      "when": {\n        "any": [\n          {\n            "tool_unregistered": true\n          },\n          {\n            "registry_entry_incomplete": true\n          }\n        ]\n      },\n      "args_check": [],\n      "state_inputs": [\n        "tool_registry"\n      ],\n      "test_id": "test_tuca_08_unregistered_tool",\n      "message_zh": "STPA 前置闸拦截（T-UCA-08）：该工具未登记，无法判定副作用类别。未登记工具一律拒绝。宿主核心工具（read/write/edit/pwsh/grep/web_* 等）已登记，不在此列。",\n      "message_en": "STPA pre-flight gate denied (T-UCA-08): tool not registered, side effects unknown. Unregistered tools are denied. Host tools (read/write/edit/pwsh/grep/web_*) are registered."\n    },\n    {\n      "id": "T-UCA-09",\n      "verdict": "block",\n      "when": {\n        "any": [\n          {\n            "schema_invalid": true\n          },\n          {\n            "arg_regex": "[\\\\u0000-\\\\u001F\\\\u2028\\\\u2029\\\\u200B-\\\\u200F\\\\u2060\\\\u202A-\\\\u202E]"\n          },\n          {\n            "arg_regex": "(?i)(<\\\\|im_start\\\\|>|<\\\\|im_end\\\\|>|</system>|\\\\[INST\\\\]|\\\\[/INST\\\\]|\\\\n\\\\nHuman:|\\\\n\\\\nAssistant:)"\n          },\n          {\n            "tool_name_has": [\n              "/",\n              "\\\\\\\\",\n              " ",\n              ":",\n              ";",\n              "\\\\n"\n            ]\n          },\n          {\n            "tool_name_homoglyph_conflict": true\n          }\n        ]\n      },\n      "args_check": [\n        {\n          "kind": "schema",\n          "reject_unknown_keys_unless_declared": true\n        }\n      ],\n      "state_inputs": [\n        "tool_registry"\n      ],\n      "test_id": "test_tuca_09_schema_invalid",\n      "message_zh": "STPA 前置闸拦截（T-UCA-09）：参数不符合 schema，或含控制字符/分隔符/同形字等注入迹象。请提交干净参数。",\n      "message_en": "STPA pre-flight gate denied (T-UCA-09): arguments violate the tool schema or contain control characters, delimiters or homoglyphs. Submit clean arguments."\n    },\n    {\n      "id": "T-UCA-12",\n      "verdict": "block",\n      "when": {\n        "any": [\n          {\n            "capability_undeclared": true\n          },\n          {\n            "capability_not_granted": true\n          }\n        ]\n      },\n      "args_check": [],\n      "state_inputs": [\n        "capabilities"\n      ],\n      "test_id": "test_tuca_12_undeclared_capability",\n      "message_zh": "STPA 前置闸拦截（T-UCA-12）：该工具需要未声明或未授予的能力（网络/命令/凭证/动态代码）。最小能力原则下拒绝。宿主工具的能力由宿主授权，不在此列。",\n      "message_en": "STPA pre-flight gate denied (T-UCA-12): tool requires an undeclared or ungranted capability. Least-capability applies. Host-tool capabilities are granted by the host."\n    },\n    {\n      "id": "T-UCA-14",\n      "verdict": "block",\n      "when": {\n        "all": [\n          {\n            "tool_mutating": true\n          },\n          {\n            "state": "frozen_ok",\n            "eq": false\n          }\n        ]\n      },\n      "args_check": [],\n      "state_inputs": [\n        "frozen_ok"\n      ],\n      "test_id": "test_tuca_14_frozen_fail_blocks",\n      "message_zh": "STPA 前置闸拦截（T-UCA-14）：宪法冻结区校验未通过。完整性恢复前，写类调用一律拒绝。",\n      "message_en": "STPA pre-flight gate denied (T-UCA-14): frozen-region integrity check failed. Mutating calls are denied until restored."\n    },\n    {\n      "id": "T-UCA-15",\n      "verdict": "block",\n      "when": {\n        "any": [\n          {\n            "path_escape": true\n          }\n        ]\n      },\n      "args_check": [\n        {\n          "kind": "path_normalize",\n          "unicode": "NFKC",\n          "workspace_root": "state.workspace_root",\n          "reject": [\n            "dotdot_escape",\n            "absolute_outside_workspace",\n            "unc",\n            "null_byte"\n          ]\n        }\n      ],\n      "state_inputs": [\n        "workspace_root"\n      ],\n      "test_id": "test_tuca_15_path_escape",\n      "message_zh": "STPA 前置闸拦截（T-UCA-15）：路径确实逃出工作区（`../` 越界、工作区外的绝对路径/盘符、UNC、空字节）。工作区内的相对路径与工作区内绝对路径都放行。",\n      "message_en": "STPA pre-flight gate denied (T-UCA-15): the path truly escapes the workspace (dot-dot escape, absolute path outside workspace, UNC, NUL). Relative paths and absolute paths inside the workspace pass."\n    },\n    {\n      "id": "T-UCA-10",\n      "verdict": "degrade",\n      "when": {\n        "all": [\n          {\n            "state": "phase",\n            "eq": "critical"\n          },\n          {\n            "tool_mutating": true\n          }\n        ]\n      },\n      "args_check": [],\n      "state_inputs": [\n        "phase"\n      ],\n      "test_id": "test_tuca_10_critical_write_degrade",\n      "message_zh": "STPA 前置闸降级（T-UCA-10）：当前上下文处于临界相位（≥0.85）。写类操作已暂缓，需确认目标与内容后再执行。",\n      "message_en": "STPA pre-flight gate held (T-UCA-10): context is in the critical phase (>=0.85). Mutating calls are paused for confirmation."\n    },\n    {\n      "id": "T-UCA-13",\n      "verdict": "degrade",\n      "when": {\n        "all": [\n          {\n            "any": [\n              {\n                "state": "saturated.valence",\n                "eq": true\n              },\n              {\n                "state": "saturated.arousal",\n                "eq": true\n              },\n              {\n                "state": "saturated.dominance",\n                "eq": true\n              }\n            ]\n          },\n          {\n            "tool_class_any": [\n              "write",\n              "destructive"\n            ]\n          },\n          {\n            "not": {\n              "confirmation_exact": true\n            }\n          }\n        ]\n      },\n      "args_check": [],\n      "state_inputs": [\n        "saturated"\n      ],\n      "test_id": "test_tuca_13_saturated_write_degrade",\n      "message_zh": "STPA 前置闸降级（T-UCA-13）：情感基线处于饱和状态，写/删类操作已暂缓，请确认后执行。",\n      "message_en": "STPA pre-flight gate held (T-UCA-13): affective baseline saturated; irreversible writes/deletes paused for confirmation."\n    },\n    {\n      "id": "T-UCA-16",\n      "verdict": "degrade",\n      "when": {\n        "all": [\n          {\n            "state": "phase",\n            "eq": "shift"\n          },\n          {\n            "tool_class_any": [\n              "destructive"\n            ]\n          }\n        ]\n      },\n      "args_check": [],\n      "state_inputs": [\n        "phase"\n      ],\n      "test_id": "test_tuca_16_shift_destructive_degrade",\n      "message_zh": "STPA 前置闸降级（T-UCA-16）：上下文处于预警相位（≥0.70），删除/覆盖类操作已暂缓，请二次确认目标后再执行。",\n      "message_en": "STPA pre-flight gate held (T-UCA-16): context is in the warning phase (>=0.70). Destructive calls are paused for re-confirmation."\n    },\n    {\n      "id": "T-UCA-11",\n      "verdict": "block",\n      "when": {\n        "any": [\n          {\n            "state": "audit_context_present",\n            "eq": false\n          },\n          {\n            "audit_write_unavailable": true\n          }\n        ]\n      },\n      "args_check": [],\n      "state_inputs": [\n        "audit_context"\n      ],\n      "test_id": "test_tuca_11_missing_audit_context",\n      "message_zh": "STPA 前置闸拦截（T-UCA-11）：本次调用缺少完整审计上下文，无法留痕。请修复会话/轮次标识后重试。",\n      "message_en": "STPA pre-flight gate denied (T-UCA-11): audit context incomplete; the call cannot be traced. Restore session/turn identifiers."\n    }\n  ]\n}'

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

# v1.2 宿主工具白名单（Capability Allowlist）：DSH 核心工具不再落入 T-UCA-08。
# 能力由**宿主**授权（DSH workspace-write + approval 策略），网关只对"形状可疑"的调用
# 追加拦截；插件自有工具仍走最小能力原则（T-UCA-12）。与 JS 侧 HOST_TOOL_SPECS 一致。
HOST_TOOL_SPECS = {
    "read": ("read", ["fs-read"], ["path"], "path"),
    "write": ("write", ["fs-write"], ["path", "content"], "path"),
    "edit": ("write", ["fs-write"], ["path", "old_string", "new_string"], "path"),
    "read_image": ("read", ["fs-read"], ["path"], "path"),
    "glob": ("read", ["fs-read"], ["pattern"], None),
    "grep": ("read", ["fs-read"], ["pattern"], None),
    "pwsh": ("exec", [], ["command"], None),
    "present": ("read", [], [], None),
    "todo_write": ("read", [], [], None),
    "skill": ("read", [], [], None),
    "working_directory": ("read", [], [], None),
    "load_workspace_dependencies": ("read", [], [], None),
    "job_list": ("read", [], [], None),
    "job_output": ("read", [], [], None),
    "job_kill": ("read", [], [], None),
    "list_agents": ("read", [], [], None),
    "wait_agent": ("read", [], [], None),
    "send_message": ("read", [], [], None),
    "spawn_teammate": ("read", [], [], None),
    "interrupt_agent": ("read", [], [], None),
    "subagent": ("read", [], [], None),
    "subagent_fork": ("read", [], [], None),
    "workflow": ("read", [], [], None),
    "team_task_list": ("read", [], [], None),
    "team_task_get": ("read", [], [], None),
    "team_task_create": ("read", [], [], None),
    "team_task_update": ("read", [], [], None),
    "web_search": ("network", [], ["query"], None),
    "web_fetch": ("network", [], ["url"], None),
    "home_list": ("read", [], [], None),
    "home_state": ("read", [], [], None),
    "home_policy": ("read", [], [], None),
    "home_speak": ("read", [], [], None),
    "home_call": ("write", [], [], None),
    "stock_board_list": ("read", [], [], None),
    "stock_board_get": ("read", [], [], None),
    "stock_run_list": ("read", [], [], None),
    "stock_completeness": ("read", [], [], None),
    "stock_cost_sensitivity": ("read", [], [], None),
    "stock_workspace_policy": ("read", [], [], None),
    "stock_settle_record": ("write", [], [], None),
    "schedule_create": ("read", [], [], None),
    "schedule_list": ("read", [], [], None),
    "schedule_update": ("read", [], [], None),
    "schedule_delete": ("read", [], [], None),
    "create_goal": ("read", [], [], None),
    "get_goal": ("read", [], [], None),
    "update_goal": ("read", [], [], None),
    "ask_user_question": ("read", [], [], None),
    "exit_plan_mode": ("read", [], [], None),
}
for _name, _spec in HOST_TOOL_SPECS.items():
    REGISTRY[_name] = {"class": _spec[0], "caps": list(_spec[1]),
                       "required_args": list(_spec[2]), "path_arg": _spec[3],
                       "schema": {}, "allowUnknown": True}


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
# v1.2（误报收敛）：路径/URL/命令参数按**语义槽位**判定，不再对全部字符串做路径匹配。
# 只有真正的路径参数或"裸路径字符串"参与逃逸/受保护路径判定；正文里的 URL 与路径文本不算。
_URL_KEYS = frozenset(("url", "uri", "endpoint", "link", "href", "webhook", "callback",
                       "source_url", "target_url"))
_CMD_KEYS = frozenset(("cmd", "command", "commandline", "script", "code", "shell"))
_BARE_PATH_RE = re.compile(r"^([A-Za-z]:[\\/]|[\\/]{1,2}|\.{1,2}[\\/])")
_SENSITIVE_PATH_RE = re.compile(
    r"(^|/)(\.env(\.[a-z0-9_-]+)?|\.ssh(/|$)|id_rsa|id_ed25519|credentials(\.json)?|"
    r"\.netrc|\.pgpass|\.aws/credentials)$|\.(pem|key|pfx|p12)$")
# 登记表 schema（docs/24 §2 要求 registry 带 schema；未知键一律拒绝，T-UCA-09）
SCHEMAS = {
    # 2026-10-28 修误报：lingshu_status 确有 `verbose` 参数，空 schema 会把每次调用判成
    # T-UCA-09（schema_invalid）；且该工具是**可执行**路径，会真给模型注入强提示。
    # 与 plugin/dsh-lingshu/tool-gateway.js 的 REGISTRY 同步（parity 测试逐字段比对）。
    "lingshu_status": {"verbose": {"type": "boolean", "required": False}},
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


def _is_path_value(kind, key, value) -> bool:
    if not isinstance(value, str) or not value:
        return False
    k = str(key).lower() if (kind == "key" and key is not None) else ""
    if k in _URL_KEYS:
        return False
    if k and k in PATH_KEYS:
        return True
    v = value.strip()
    return bool(_BARE_PATH_RE.match(v)) and not any(c.isspace() for c in v)


def _locate_path(norm: str, workspace_root):
    """工作区感知：工作区内绝对路径 → 相对路径（不逃逸）；越界才逃逸。"""
    if "\x00" in norm:
        return norm, True
    low = norm.lower()
    if low.startswith("//"):
        return norm, True
    is_abs = bool(DRIVE_RE.match(norm)) or norm.startswith("/")
    if not is_abs:
        return norm, (".." in norm.split("/"))
    root = str(workspace_root or "").lower().replace("\\", "/").rstrip("/")
    if root and low == root:
        return "", False
    if root and low.startswith(root + "/"):
        return norm[len(root) + 1:], False
    return norm, True


def _path_info(args, workspace_root) -> dict:
    rels, escape, glob_hit, sensitive = [], False, False, False
    for kind, key, value, _d in _walk(args):
        if not _is_path_value(kind, key, value):
            continue
        norm = _resolve_dotdot(_norm_text(value).replace("\\", "/"))
        rel, esc = _locate_path(norm, workspace_root)
        rels.append(rel.lower())
        escape = escape or esc
        glob_hit = glob_hit or ("*" in rel or "?" in rel)
        sensitive = sensitive or bool(_SENSITIVE_PATH_RE.search(rel.lower()))
    return {"paths": rels, "escape": escape, "glob": glob_hit, "sensitive": sensitive}


def _url_candidates(args) -> list[str]:
    out = []
    for kind, key, value, _d in _walk(args):
        if not isinstance(value, str) or not value:
            continue
        k = str(key).lower() if (kind == "key" and key is not None) else ""
        v = value.strip()
        if k in _URL_KEYS:
            out.append(v)
        elif re.match(r"(?i)^https?://\S+$", v):
            out.append(v)
    return out


def _host_of(url: str) -> str:
    m = re.match(r"(?i)^[a-z][a-z0-9+.-]*://([^/?#]+)", str(url))
    if not m:
        return ""
    host = m.group(1).rsplit("@", 1)[-1]
    return host.split(":")[0].lower()


def _url_hosts_allowlisted(urls, allowlist) -> bool:
    if not urls:
        return False
    allow = [str(a).lower() for a in (allowlist or [])]
    for u in urls:
        host = _host_of(u)
        if not any(host == a or host.endswith("." + a) for a in allow):
            return False
    return True


def _cmd_strings(args) -> list[str]:
    out = []
    for kind, key, value, _d in _walk(args):
        if (kind == "key" and key is not None and str(key).lower() in _CMD_KEYS
                and isinstance(value, str)):
            out.append(value)
    return out


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
    workspace_root = state.get("workspace_root") or state.get("workspaceRoot") or ""
    _pinfo = _path_info(args, workspace_root)
    _urls = _url_candidates(args)
    ctx = {
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
        "paths": _pinfo["paths"], "path_escape": _pinfo["escape"],
        "path_glob": _pinfo["glob"], "sensitive_path": _pinfo["sensitive"],
        "workspace_root": workspace_root,
        "urls": _urls, "url_present": bool(_urls),
        "url_host_allowlisted": _url_hosts_allowlisted(_urls, policy.get("egress_allowlist")),
        "cmd_strings": _cmd_strings(args),
        "target": _target_of(args), "args_bytes": len(_canon(args).encode("utf-8")),
        "strings": list(_strings(args)), "at": float(state.get("at") or 0.0),
        "approval_channel": bool(state.get("approval_channel")
                                 or policy.get("approval_channel")),
    }
    # 相位豁免（docs/24 §4.1）：normal 相位且无冲突/未饱和 → 写操作按 low_risk 记录。
    ctx["phase_relaxed"] = (ctx["phase"] == "normal" and not ctx["variety_fallback"]
                            and not ctx["unresolvedConflict"]
                            and not ctx["saturated_any"])
    ctx["low_risk"] = bool(ctx["tool_mutating"] and ctx["phase_relaxed"])
    return ctx


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
    if "path_glob" in value:
        return ctx["path_glob"] == bool(value["path_glob"])
    if "sensitive_path" in value:
        return ctx["sensitive_path"] == bool(value["sensitive_path"])
    if "arg_regex_cmd" in value:
        rx = _compile(str(value["arg_regex_cmd"]))
        return any(rx.search(s) for s in ctx["cmd_strings"])
    if "url_present" in value:
        return ctx["url_present"] == bool(value["url_present"])
    if "url_host_allowlisted" in value:
        return ctx["url_host_allowlisted"] == bool(value["url_host_allowlisted"])
    if "phase_relaxed" in value:
        return ctx["phase_relaxed"] == bool(value["phase_relaxed"])
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
        "low_risk": bool(ctx.get("low_risk")),
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
            "low_risk": bool(ctx.get("low_risk")),
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
