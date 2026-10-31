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

const POLICY_JSON = "{\n  \"policy_version\": \"t-uca-1.3\",\n  \"namespace\": \"T-UCA\",\n  \"default\": {\n    \"unknown\": \"block\",\n    \"read_only\": \"allow_with_log\",\n    \"phase_unknown_mutating\": \"block\"\n  },\n  \"egress_allowlist\": [\n    \"github.com\",\n    \"api.github.com\",\n    \"raw.githubusercontent.com\",\n    \"objects.githubusercontent.com\",\n    \"npmjs.org\",\n    \"registry.npmjs.org\",\n    \"pypi.org\",\n    \"files.pythonhosted.org\",\n    \"python.org\",\n    \"docs.python.org\",\n    \"nodejs.org\",\n    \"developer.mozilla.org\"\n  ],\n  \"evaluation_order\": [\n    \"T-UCA-01\",\n    \"T-UCA-02\",\n    \"T-UCA-03\",\n    \"T-UCA-04\",\n    \"T-UCA-05\",\n    \"T-UCA-06\",\n    \"T-UCA-07\",\n    \"T-UCA-08\",\n    \"T-UCA-09\",\n    \"T-UCA-12\",\n    \"T-UCA-14\",\n    \"T-UCA-15\",\n    \"T-UCA-10\",\n    \"T-UCA-13\",\n    \"T-UCA-16\",\n    \"T-UCA-11\"\n  ],\n  \"rules\": [\n    {\n      \"id\": \"T-UCA-01\",\n      \"verdict\": \"block\",\n      \"when\": {\n        \"all\": [\n          {\n            \"tool_mutating\": true\n          },\n          {\n            \"any\": [\n              {\n                \"state\": \"variety_fallback\",\n                \"eq\": true\n              },\n              {\n                \"state\": \"unresolvedConflict\",\n                \"eq\": true\n              }\n            ]\n          }\n        ]\n      },\n      \"args_check\": [],\n      \"state_inputs\": [\n        \"variety_fallback\",\n        \"unresolvedConflict\"\n      ],\n      \"test_id\": \"test_tuca_01_unresolved_write\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-01\uff09\uff1a\u5f53\u524d\u5b58\u5728\u672a\u6f84\u6e05\u7684\u8bb0\u5fc6\u51b2\u7a81\uff0c\u7cfb\u7edf\u5904\u4e8e\u4e2d\u6027\u964d\u7ef4\u6001\u3002\u6240\u6709\u5199/\u5220/\u5916\u53d1/\u6267\u884c\u7c7b\u8c03\u7528\u88ab\u62d2\u7edd\u3002\u8bf7\u5148\u8bf4\u660e\u4ee5\u54ea\u4e00\u6761\u8bb0\u5f55\u4e3a\u51c6\u3002\",\n      \"message_en\": \"STPA pre-flight gate denied (T-UCA-01): an unresolved memory conflict keeps the system in variety-reduced mode. All mutating tool calls are blocked until the authoritative record is stated.\"\n    },\n    {\n      \"id\": \"T-UCA-02\",\n      \"verdict\": \"block\",\n      \"when\": {\n        \"any\": [\n          {\n            \"arg_regex\": \"(?i)(-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9_\\\\-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9\\\\-]{10,}|eyJ[A-Za-z0-9_\\\\-]{10,}\\\\.[A-Za-z0-9_\\\\-]{10,}\\\\.)\"\n          },\n          {\n            \"arg_regex\": \"(?i)\\\\b(api[_-]?key|apikey|access[_-]?token|auth[_-]?token|client[_-]?secret|secret|password|passwd)\\\\b\\\\s*[=:]\\\\s*[\\\"']?[A-Za-z0-9/+=_\\\\-]{4,}\"\n          },\n          {\n            \"sensitive_path\": true\n          }\n        ]\n      },\n      \"args_check\": [\n        {\n          \"kind\": \"regex\",\n          \"over\": \"all_strings\",\n          \"scope\": \"secret_value_and_assignment\"\n        },\n        {\n          \"kind\": \"credential_path\",\n          \"over\": \"path_args\",\n          \"reject\": [\n            \"dotenv\",\n            \"ssh_dir\",\n            \"private_key\",\n            \"credential_store\"\n          ]\n        }\n      ],\n      \"state_inputs\": [\n        \"capabilities\"\n      ],\n      \"test_id\": \"test_tuca_02_secret_in_args\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-02\uff09\uff1a\u53c2\u6570\u91cc\u51fa\u73b0\u9ad8\u7f6e\u4fe1\u5ea6\u51ed\u8bc1\u503c\uff08\u79c1\u94a5/sk-/ghp_/AKIA/JWT\uff09\u3001\u51ed\u8bc1\u8d4b\u503c\uff08api_key=\u2026\u3001password: \u2026\uff09\uff0c\u6216\u76ee\u6807\u8def\u5f84\u5c5e\u4e8e\u51ed\u8bc1\u5e93\uff08.env/.ssh/id_rsa/*.pem\uff09\u3002\u4ec5\u51fa\u73b0 token/password \u8fd9\u7c7b\u8bcd\u4e0d\u7b49\u4e8e\u51ed\u8bc1\u3002\",\n      \"message_en\": \"STPA pre-flight gate denied (T-UCA-02): a high-confidence credential value, a credential assignment, or a credential-store path was detected. The bare words token/password are not treated as credentials.\"\n    },\n    {\n      \"id\": \"T-UCA-03\",\n      \"verdict\": \"degrade\",\n      \"when\": {\n        \"all\": [\n          {\n            \"tool_class_any\": [\n              \"destructive\"\n            ]\n          },\n          {\n            \"any\": [\n              {\n                \"arg_true\": \"force\"\n              },\n              {\n                \"arg_true\": \"recursive\"\n              },\n              {\n                \"path_glob\": true\n              },\n              {\n                \"path_prefix_any\": [\n                  \"/\",\n                  \"//\",\n                  \"*\",\n                  \"c:/windows\",\n                  \"c:/program files\",\n                  \"c:/programdata\"\n                ]\n              },\n              {\n                \"arg_regex\": \"(?i)(^|[/\\\\\\\\])(rm|rmdir|del|deltree|truncate|format)(\\\\s|$)\"\n              }\n            ]\n          },\n          {\n            \"not\": {\n              \"confirmation_exact\": true\n            }\n          }\n        ]\n      },\n      \"args_check\": [\n        {\n          \"kind\": \"no_glob_or_recursive\",\n          \"scope\": \"path_args\"\n        }\n      ],\n      \"state_inputs\": [],\n      \"test_id\": \"test_tuca_03_destructive_delete\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u964d\u7ea7\uff08T-UCA-03\uff09\uff1a\u5220\u9664/\u8986\u76d6\u7c7b\u8c03\u7528\u547d\u4e2d\u9ad8\u98ce\u9669\u5f62\u6001\uff08\u901a\u914d\u7b26\u3001\u6839\u76ee\u5f55\u6216\u7cfb\u7edf\u76ee\u5f55\u3001\u9012\u5f52/\u5f3a\u5236\u6807\u5fd7\u3001rm/del/truncate \u547d\u4ee4\uff09\uff0c\u9700\u4eba\u5de5\u786e\u8ba4\u540e\u518d\u6267\u884c\u3002\u666e\u901a\u5355\u6587\u4ef6\u5220\u9664\uff08\u5982 rm file.tmp\uff09\u4e0d\u518d\u89e6\u53d1\u3002\",\n      \"message_en\": \"STPA pre-flight gate degraded (T-UCA-03): delete/overwrite hits a high-risk shape (wildcard, root/system dir, recursive/force flag, rm/del/truncate). Plain single-file deletes are no longer flagged.\"\n    },\n    {\n      \"id\": \"T-UCA-04\",\n      \"verdict\": \"block\",\n      \"when\": {\n        \"all\": [\n          {\n            \"tool_mutating\": true\n          },\n          {\n            \"any\": [\n              {\n                \"path_prefix_any\": [\n                  \"rules/\",\n                  \"engine/\",\n                  \"plugin/\",\n                  \"replay-set/\",\n                  \"chaos-set/\",\n                  \"acceptance/\",\n                  \"decisions/\",\n                  \"docs/01-\u5baa\u6cd5\u5c42.md\",\n                  \"docs/15-\u9a8c\u6536\u6e05\u5355.md\",\n                  \"docs/24-\u5de5\u5177\u7f51\u5173UCA.md\",\n                  \"_frozen/\",\n                  \".git/\",\n                  \".env\",\n                  \"c:/windows\"\n                ]\n              },\n              {\n                \"path_escape\": true\n              }\n            ]\n          }\n        ]\n      },\n      \"args_check\": [\n        {\n          \"kind\": \"path_normalize\",\n          \"unicode\": \"NFKC\",\n          \"resolve_dotdot\": true,\n          \"case_fold\": true\n        }\n      ],\n      \"state_inputs\": [\n        \"frozen_ok\"\n      ],\n      \"test_id\": \"test_tuca_04_protected_path\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-04\uff09\uff1a\u5199/\u5220\u64cd\u4f5c\u7684\u76ee\u6807\u5c5e\u4e8e\u51bb\u7ed3/\u53d7\u4fdd\u62a4\u533a\u57df\uff08\u5baa\u6cd5\u3001\u89c4\u5219\u8868\u3001\u56de\u653e\u96c6\u3001\u5f15\u64ce\u4e0e\u63d2\u4ef6\u6e90\u7801\uff09\u3002\u53ea\u8bfb\u8bbf\u95ee\u4e0d\u53d7\u6b64\u89c4\u5219\u9650\u5236\uff1b\u4fee\u6539\u53ea\u80fd\u7531\u4eba\u5de5\u6309 ADR \u6d41\u7a0b\u8fdb\u884c\u3002\",\n      \"message_en\": \"STPA pre-flight gate denied (T-UCA-04): a mutating call targets a frozen/protected area. Read-only access is unaffected; changes go through the human ADR process.\"\n    },\n    {\n      \"id\": \"T-UCA-05\",\n      \"verdict\": \"block\",\n      \"when\": {\n        \"any\": [\n          {\n            \"capability\": \"network\"\n          },\n          {\n            \"all\": [\n              {\n                \"url_present\": true\n              },\n              {\n                \"url_host_allowlisted\": false\n              }\n            ]\n          }\n        ]\n      },\n      \"args_check\": [\n        {\n          \"kind\": \"egress_allowlist\",\n          \"source\": \"policy.egress_allowlist\",\n          \"match\": \"exact_host_or_subdomain_then_path_prefix\"\n        }\n      ],\n      \"state_inputs\": [\n        \"capabilities\"\n      ],\n      \"test_id\": \"test_tuca_05_egress_blocked\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-05\uff09\uff1a\u7f51\u7edc\u5916\u53d1\u76ee\u6807\u4e0d\u5728 Egress \u767d\u540d\u5355\u5185\uff08\u9ed8\u8ba4\u5141\u8bb8 github.com / npmjs.org / pypi.org \u7b49\u5f00\u53d1\u6587\u6863\u7ad9\uff09\u3002\u53ea\u51fa\u73b0\u5728\u6b63\u6587\u91cc\u7684 URL \u6587\u672c\u4e0d\u7b97\u5916\u53d1\uff1b`web_fetch` \u7b49\u53d6\u56de\u7c7b\u5de5\u5177\u6309\u76ee\u6807\u57df\u540d\u5224\u5b9a\u3002\",\n      \"message_en\": \"STPA pre-flight gate denied (T-UCA-05): egress target is not on the allowlist (dev docs: github.com / npmjs.org / pypi.org, ...). URL text inside file content is not egress; fetch-style tools are judged by target host.\"\n    },\n    {\n      \"id\": \"T-UCA-06\",\n      \"verdict\": \"block\",\n      \"when\": {\n        \"any\": [\n          {\n            \"capability_any\": [\n              \"dynamic-code\"\n            ]\n          },\n          {\n            \"all\": [\n              {\n                \"tool_class_any\": [\n                  \"exec\"\n                ]\n              },\n              {\n                \"arg_regex_cmd\": \"(?i)(\\\\bcurl\\\\b|\\\\bwget\\\\b|\\\\biwr\\\\b|invoke-webrequest|invoke-expression|\\\\biex\\\\b|\\\\bbash\\\\s+-i\\\\b|\\\\bsh\\\\s+-i\\\\b|\\\\bncat?\\\\b|\\\\bsocat\\\\b|/dev/tcp/|\\\\bpowershell(\\\\.exe)?\\\\b[^|;]*(-enc|-encodedcommand|-windowstyle\\\\s+hidden)|\\\\bsetx\\\\b|\\\\breg\\\\s+add\\\\b|setenvironmentvariable|\\\\bexport\\\\s+[A-Za-z_][A-Za-z0-9_]*=|\\\\bchmod\\\\s+777\\\\b|\\\\brm\\\\s+-rf\\\\s+/(\\\\s|$)|\\\\bsudo\\\\b|\\\\bnet\\\\s+user\\\\b|\\\\bsc\\\\s+create\\\\b|\\\\|\\\\s*(sh|bash|iex|powershell|python|node)\\\\b|\\\\bsubprocess\\\\b|\\\\bchild_process\\\\b|\\\\beval\\\\s*\\\\(|\\\\bexec\\\\s*\\\\(|\\\\bos\\\\.system\\\\b|\\\\bscp\\\\s+[^ ]+@)\"\n              }\n            ]\n          }\n        ]\n      },\n      \"args_check\": [\n        {\n          \"kind\": \"exec_shape\",\n          \"over\": \"command_args\",\n          \"reject\": [\n            \"download\",\n            \"reverse_shell\",\n            \"encoded_command\",\n            \"global_env\",\n            \"pipe_to_shell\"\n          ]\n        }\n      ],\n      \"state_inputs\": [\n        \"capabilities\"\n      ],\n      \"test_id\": \"test_tuca_06_exec_blocked\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-06\uff09\uff1a\u6267\u884c\u7c7b\u8c03\u7528\u547d\u4e2d\u9ad8\u5371\u547d\u4ee4\u5f62\u6001\uff08curl/wget/iwr\u3001\u53cd\u5411 shell\u3001\u7f16\u7801\u6267\u884c\u3001\u7ba1\u9053\u8fdb shell\u3001\u6539\u5168\u5c40\u73af\u5883\u53d8\u91cf\u3001subprocess/eval/exec\uff09\u3002\u5e38\u89c4 `npm run test`\u3001`python scripts/run.py` \u76f4\u63a5\u653e\u884c\uff1b\u6e90\u7801\u6b63\u6587\u91cc\u51fa\u73b0\u8fd9\u4e9b\u8bcd\u4e0d\u7b97\u6267\u884c\u3002\",\n      \"message_en\": \"STPA pre-flight gate denied (T-UCA-06): exec call matches a dangerous command shape (curl/wget/iwr, reverse shell, encoded command, pipe-to-shell, global env mutation, subprocess/eval/exec). Ordinary `npm run test` / `python scripts/run.py` pass; those words inside source content are not exec.\"\n    },\n    {\n      \"id\": \"T-UCA-07\",\n      \"verdict\": \"block\",\n      \"when\": {\n        \"any\": [\n          {\n            \"args_bytes_gt\": 65536\n          },\n          {\n            \"json_depth_gt\": 8\n          },\n          {\n            \"array_len_gt\": 1000\n          },\n          {\n            \"json_nodes_gt\": 5000\n          },\n          {\n            \"encoding_layers_gt\": 2\n          }\n        ]\n      },\n      \"args_check\": [\n        {\n          \"kind\": \"shape_limits\",\n          \"max_bytes\": 65536,\n          \"max_depth\": 8,\n          \"max_array\": 1000,\n          \"max_nodes\": 5000,\n          \"max_encoding_layers\": 2\n        }\n      ],\n      \"state_inputs\": [],\n      \"test_id\": \"test_tuca_07_oversize\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-07\uff09\uff1a\u53c2\u6570\u4f53\u79ef/\u5d4c\u5957/\u7f16\u7801\u5c42\u6570\u8d85\u8fc7\u7f51\u5173\u4e0a\u9650\u3002\u8bf7\u62c6\u5206\u8bf7\u6c42\u6216\u51cf\u5c11\u5d4c\u5957\u3002\",\n      \"message_en\": \"STPA pre-flight gate denied (T-UCA-07): payload exceeds size, depth or encoding-layer limits. Split the request or reduce nesting.\"\n    },\n    {\n      \"id\": \"T-UCA-08\",\n      \"verdict\": \"block\",\n      \"when\": {\n        \"any\": [\n          {\n            \"tool_unregistered\": true\n          },\n          {\n            \"registry_entry_incomplete\": true\n          }\n        ]\n      },\n      \"args_check\": [],\n      \"state_inputs\": [\n        \"tool_registry\"\n      ],\n      \"test_id\": \"test_tuca_08_unregistered_tool\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-08\uff09\uff1a\u8be5\u5de5\u5177\u672a\u767b\u8bb0\uff0c\u65e0\u6cd5\u5224\u5b9a\u526f\u4f5c\u7528\u7c7b\u522b\u3002\u672a\u767b\u8bb0\u5de5\u5177\u4e00\u5f8b\u62d2\u7edd\u3002\u5bbf\u4e3b\u6838\u5fc3\u5de5\u5177\uff08read/write/edit/pwsh/grep/web_* \u7b49\uff09\u5df2\u767b\u8bb0\uff0c\u4e0d\u5728\u6b64\u5217\u3002\",\n      \"message_en\": \"STPA pre-flight gate denied (T-UCA-08): tool not registered, side effects unknown. Unregistered tools are denied. Host tools (read/write/edit/pwsh/grep/web_*) are registered.\"\n    },\n    {\n      \"id\": \"T-UCA-09\",\n      \"verdict\": \"block\",\n      \"when\": {\n        \"any\": [\n          {\n            \"schema_invalid\": true\n          },\n          {\n            \"arg_regex\": \"[\\\\u0000-\\\\u0008\\\\u000B\\\\u000C\\\\u000E-\\\\u001F\\\\u2028\\\\u2029\\\\u200B-\\\\u200F\\\\u2060\\\\u202A-\\\\u202E]\"\n          },\n          {\n            \"arg_regex\": \"(?i)(<\\\\|im_start\\\\|>|<\\\\|im_end\\\\|>|</system>|\\\\[INST\\\\]|\\\\[/INST\\\\]|\\\\n\\\\nHuman:|\\\\n\\\\nAssistant:)\"\n          },\n          {\n            \"tool_name_has\": [\n              \"/\",\n              \"\\\\\\\\\",\n              \" \",\n              \":\",\n              \";\",\n              \"\\\\n\"\n            ]\n          },\n          {\n            \"tool_name_homoglyph_conflict\": true\n          }\n        ]\n      },\n      \"args_check\": [\n        {\n          \"kind\": \"schema\",\n          \"reject_unknown_keys_unless_declared\": true\n        }\n      ],\n      \"state_inputs\": [\n        \"tool_registry\"\n      ],\n      \"test_id\": \"test_tuca_09_schema_invalid\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-09\uff09\uff1a\u53c2\u6570\u4e0d\u7b26\u5408 schema\uff0c\u6216\u542b\u63a7\u5236\u5b57\u7b26/\u5206\u9694\u7b26/\u540c\u5f62\u5b57\u7b49\u6ce8\u5165\u8ff9\u8c61\u3002\u8bf7\u63d0\u4ea4\u5e72\u51c0\u53c2\u6570\u3002\",\n      \"message_en\": \"STPA pre-flight gate denied (T-UCA-09): arguments violate the tool schema or contain control characters, delimiters or homoglyphs. Submit clean arguments.\"\n    },\n    {\n      \"id\": \"T-UCA-12\",\n      \"verdict\": \"block\",\n      \"when\": {\n        \"any\": [\n          {\n            \"capability_undeclared\": true\n          },\n          {\n            \"capability_not_granted\": true\n          }\n        ]\n      },\n      \"args_check\": [],\n      \"state_inputs\": [\n        \"capabilities\"\n      ],\n      \"test_id\": \"test_tuca_12_undeclared_capability\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-12\uff09\uff1a\u8be5\u5de5\u5177\u9700\u8981\u672a\u58f0\u660e\u6216\u672a\u6388\u4e88\u7684\u80fd\u529b\uff08\u7f51\u7edc/\u547d\u4ee4/\u51ed\u8bc1/\u52a8\u6001\u4ee3\u7801\uff09\u3002\u6700\u5c0f\u80fd\u529b\u539f\u5219\u4e0b\u62d2\u7edd\u3002\u5bbf\u4e3b\u5de5\u5177\u7684\u80fd\u529b\u7531\u5bbf\u4e3b\u6388\u6743\uff0c\u4e0d\u5728\u6b64\u5217\u3002\",\n      \"message_en\": \"STPA pre-flight gate denied (T-UCA-12): tool requires an undeclared or ungranted capability. Least-capability applies. Host-tool capabilities are granted by the host.\"\n    },\n    {\n      \"id\": \"T-UCA-14\",\n      \"verdict\": \"block\",\n      \"when\": {\n        \"all\": [\n          {\n            \"tool_mutating\": true\n          },\n          {\n            \"state\": \"frozen_ok\",\n            \"eq\": false\n          }\n        ]\n      },\n      \"args_check\": [],\n      \"state_inputs\": [\n        \"frozen_ok\"\n      ],\n      \"test_id\": \"test_tuca_14_frozen_fail_blocks\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-14\uff09\uff1a\u5baa\u6cd5\u51bb\u7ed3\u533a\u6821\u9a8c\u672a\u901a\u8fc7\u3002\u5b8c\u6574\u6027\u6062\u590d\u524d\uff0c\u5199\u7c7b\u8c03\u7528\u4e00\u5f8b\u62d2\u7edd\u3002\",\n      \"message_en\": \"STPA pre-flight gate denied (T-UCA-14): frozen-region integrity check failed. Mutating calls are denied until restored.\"\n    },\n    {\n      \"id\": \"T-UCA-15\",\n      \"verdict\": \"block\",\n      \"when\": {\n        \"any\": [\n          {\n            \"path_escape\": true\n          }\n        ]\n      },\n      \"args_check\": [\n        {\n          \"kind\": \"path_normalize\",\n          \"unicode\": \"NFKC\",\n          \"workspace_root\": \"state.workspace_root\",\n          \"reject\": [\n            \"dotdot_escape\",\n            \"absolute_outside_workspace\",\n            \"unc\",\n            \"null_byte\"\n          ]\n        }\n      ],\n      \"state_inputs\": [\n        \"workspace_root\"\n      ],\n      \"test_id\": \"test_tuca_15_path_escape\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-15\uff09\uff1a\u8def\u5f84\u786e\u5b9e\u9003\u51fa\u5de5\u4f5c\u533a\uff08`../` \u8d8a\u754c\u3001\u5de5\u4f5c\u533a\u5916\u7684\u7edd\u5bf9\u8def\u5f84/\u76d8\u7b26\u3001UNC\u3001\u7a7a\u5b57\u8282\uff09\u3002\u5de5\u4f5c\u533a\u5185\u7684\u76f8\u5bf9\u8def\u5f84\u4e0e\u5de5\u4f5c\u533a\u5185\u7edd\u5bf9\u8def\u5f84\u90fd\u653e\u884c\u3002\",\n      \"message_en\": \"STPA pre-flight gate denied (T-UCA-15): the path truly escapes the workspace (dot-dot escape, absolute path outside workspace, UNC, NUL). Relative paths and absolute paths inside the workspace pass.\"\n    },\n    {\n      \"id\": \"T-UCA-10\",\n      \"verdict\": \"degrade\",\n      \"when\": {\n        \"all\": [\n          {\n            \"state\": \"phase\",\n            \"eq\": \"critical\"\n          },\n          {\n            \"tool_mutating\": true\n          }\n        ]\n      },\n      \"args_check\": [],\n      \"state_inputs\": [\n        \"phase\"\n      ],\n      \"test_id\": \"test_tuca_10_critical_write_degrade\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u964d\u7ea7\uff08T-UCA-10\uff09\uff1a\u5f53\u524d\u4e0a\u4e0b\u6587\u5904\u4e8e\u4e34\u754c\u76f8\u4f4d\uff08\u22650.85\uff09\u3002\u5199\u7c7b\u64cd\u4f5c\u5df2\u6682\u7f13\uff0c\u9700\u786e\u8ba4\u76ee\u6807\u4e0e\u5185\u5bb9\u540e\u518d\u6267\u884c\u3002\",\n      \"message_en\": \"STPA pre-flight gate held (T-UCA-10): context is in the critical phase (>=0.85). Mutating calls are paused for confirmation.\"\n    },\n    {\n      \"id\": \"T-UCA-13\",\n      \"verdict\": \"degrade\",\n      \"when\": {\n        \"all\": [\n          {\n            \"any\": [\n              {\n                \"state\": \"saturated.valence\",\n                \"eq\": true\n              },\n              {\n                \"state\": \"saturated.arousal\",\n                \"eq\": true\n              },\n              {\n                \"state\": \"saturated.dominance\",\n                \"eq\": true\n              }\n            ]\n          },\n          {\n            \"tool_class_any\": [\n              \"write\",\n              \"destructive\"\n            ]\n          },\n          {\n            \"not\": {\n              \"confirmation_exact\": true\n            }\n          }\n        ]\n      },\n      \"args_check\": [],\n      \"state_inputs\": [\n        \"saturated\"\n      ],\n      \"test_id\": \"test_tuca_13_saturated_write_degrade\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u964d\u7ea7\uff08T-UCA-13\uff09\uff1a\u60c5\u611f\u57fa\u7ebf\u5904\u4e8e\u9971\u548c\u72b6\u6001\uff0c\u5199/\u5220\u7c7b\u64cd\u4f5c\u5df2\u6682\u7f13\uff0c\u8bf7\u786e\u8ba4\u540e\u6267\u884c\u3002\",\n      \"message_en\": \"STPA pre-flight gate held (T-UCA-13): affective baseline saturated; irreversible writes/deletes paused for confirmation.\"\n    },\n    {\n      \"id\": \"T-UCA-16\",\n      \"verdict\": \"degrade\",\n      \"when\": {\n        \"all\": [\n          {\n            \"state\": \"phase\",\n            \"eq\": \"shift\"\n          },\n          {\n            \"tool_class_any\": [\n              \"destructive\"\n            ]\n          }\n        ]\n      },\n      \"args_check\": [],\n      \"state_inputs\": [\n        \"phase\"\n      ],\n      \"test_id\": \"test_tuca_16_shift_destructive_degrade\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u964d\u7ea7\uff08T-UCA-16\uff09\uff1a\u4e0a\u4e0b\u6587\u5904\u4e8e\u9884\u8b66\u76f8\u4f4d\uff08\u22650.70\uff09\uff0c\u5220\u9664/\u8986\u76d6\u7c7b\u64cd\u4f5c\u5df2\u6682\u7f13\uff0c\u8bf7\u4e8c\u6b21\u786e\u8ba4\u76ee\u6807\u540e\u518d\u6267\u884c\u3002\",\n      \"message_en\": \"STPA pre-flight gate held (T-UCA-16): context is in the warning phase (>=0.70). Destructive calls are paused for re-confirmation.\"\n    },\n    {\n      \"id\": \"T-UCA-11\",\n      \"verdict\": \"block\",\n      \"when\": {\n        \"any\": [\n          {\n            \"state\": \"audit_context_present\",\n            \"eq\": false\n          },\n          {\n            \"audit_write_unavailable\": true\n          }\n        ]\n      },\n      \"args_check\": [],\n      \"state_inputs\": [\n        \"audit_context\"\n      ],\n      \"test_id\": \"test_tuca_11_missing_audit_context\",\n      \"message_zh\": \"STPA \u524d\u7f6e\u95f8\u62e6\u622a\uff08T-UCA-11\uff09\uff1a\u672c\u6b21\u8c03\u7528\u7f3a\u5c11\u5b8c\u6574\u5ba1\u8ba1\u4e0a\u4e0b\u6587\uff0c\u65e0\u6cd5\u7559\u75d5\u3002\u8bf7\u4fee\u590d\u4f1a\u8bdd/\u8f6e\u6b21\u6807\u8bc6\u540e\u91cd\u8bd5\u3002\",\n      \"message_en\": \"STPA pre-flight gate denied (T-UCA-11): audit context incomplete; the call cannot be traced. Restore session/turn identifiers.\"\n    }\n  ]\n}";

export const POLICY = JSON.parse(POLICY_JSON);
export const POLICY_VERSION = POLICY.policy_version;
export const SCHEMA = 'lingshu/tool-gate/v1';

const MUTATING_CLASSES = ['write', 'destructive', 'exec', 'network'];
const PATH_KEYS = new Set(['path', 'file', 'filepath', 'target', 'dest', 'destination',
  'output', 'dir', 'directory', 'src', 'source', 'from', 'to']);
const INLINE_FLAG = /^\(\?([aiLmsux]+)\)/;
const DRIVE_RE = /^[A-Za-z]:/;
// v1.2（误报收敛）：路径/URL/命令参数按**语义槽位**判定，不再对全部字符串做路径匹配。
// 只有真正的路径参数或"裸路径字符串"参与逃逸/受保护路径判定；正文里的 URL 与路径文本不算。
const URL_KEYS = new Set(['url', 'uri', 'endpoint', 'link', 'href', 'webhook', 'callback',
  'source_url', 'target_url']);
const CMD_KEYS = new Set(['cmd', 'command', 'commandline', 'script', 'code', 'shell']);
const BARE_PATH_RE = /^([A-Za-z]:[\\/]|[\\/]{1,2}|\.{1,2}[\\/])/;
const SENSITIVE_PATH_RE = /(^|\/)(\.env(\.[a-z0-9_-]+)?|\.ssh(\/|$)|id_rsa|id_ed25519|credentials(\.json)?|\.netrc|\.pgpass|\.aws\/credentials)$|\.(pem|key|pfx|p12)$/;

// 工具登记表（与 engine/tool_gateway.py 的 REGISTRY/SCHEMAS 同构；parity 测试逐字段比对）
export const REGISTRY = {
  lingshu_status: {
    class: 'read', caps: [], required_args: [], path_arg: null,
    // 2026-10-28 修误报：schema 原来写 `{}`，而此工具确有 `verbose` 参数 →
    // 每次调用都被自我判成 T-UCA-09（schema_invalid），且这是**可执行**路径
    // （registered → enforceable=true），会真给模型注入强提示。改成显式声明可选参数。
    schema: { verbose: { type: 'boolean', required: false } },
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

// v1.2 宿主工具白名单（Capability Allowlist）：DSH 核心工具不再落入 T-UCA-08。
// 说明：能力由**宿主**授权（DSH workspace-write + approval 策略），网关只对"形状可疑"的
// 调用追加拦截（下载执行、越界写、凭证外发）；插件自有工具仍走最小能力原则（T-UCA-12）。
// 字段与 engine/tool_gateway.py 的 HOST_TOOL_SPECS 完全一致（parity 断言逐字段比对）。
const HOST_TOOL_SPECS = {
  read: ['read', ['fs-read'], ['path'], 'path'],
  write: ['write', ['fs-write'], ['path', 'content'], 'path'],
  edit: ['write', ['fs-write'], ['path', 'old_string', 'new_string'], 'path'],
  read_image: ['read', ['fs-read'], ['path'], 'path'],
  glob: ['read', ['fs-read'], ['pattern'], null],
  grep: ['read', ['fs-read'], ['pattern'], null],
  pwsh: ['exec', [], ['command'], null],
  present: ['read', [], [], null],
  todo_write: ['read', [], [], null],
  skill: ['read', [], [], null],
  working_directory: ['read', [], [], null],
  load_workspace_dependencies: ['read', [], [], null],
  job_list: ['read', [], [], null],
  job_output: ['read', [], [], null],
  job_kill: ['read', [], [], null],
  list_agents: ['read', [], [], null],
  wait_agent: ['read', [], [], null],
  send_message: ['read', [], [], null],
  spawn_teammate: ['read', [], [], null],
  interrupt_agent: ['read', [], [], null],
  subagent: ['read', [], [], null],
  subagent_fork: ['read', [], [], null],
  workflow: ['read', [], [], null],
  team_task_list: ['read', [], [], null],
  team_task_get: ['read', [], [], null],
  team_task_create: ['read', [], [], null],
  team_task_update: ['read', [], [], null],
  web_search: ['network', [], ['query'], null],
  web_fetch: ['network', [], ['url'], null],
  home_list: ['read', [], [], null],
  home_state: ['read', [], [], null],
  home_policy: ['read', [], [], null],
  home_speak: ['read', [], [], null],
  home_call: ['write', [], [], null],
  stock_board_list: ['read', [], [], null],
  stock_board_get: ['read', [], [], null],
  stock_run_list: ['read', [], [], null],
  stock_completeness: ['read', [], [], null],
  stock_cost_sensitivity: ['read', [], [], null],
  stock_workspace_policy: ['read', [], [], null],
  stock_settle_record: ['write', [], [], null],
  schedule_create: ['read', [], [], null],
  schedule_list: ['read', [], [], null],
  schedule_update: ['read', [], [], null],
  schedule_delete: ['read', [], [], null],
  create_goal: ['read', [], [], null],
  get_goal: ['read', [], [], null],
  update_goal: ['read', [], [], null],
  ask_user_question: ['read', [], [], null],
  exit_plan_mode: ['read', [], [], null],
};
for (const [name, spec] of Object.entries(HOST_TOOL_SPECS)) {
  REGISTRY[name] = {
    class: spec[0], caps: spec[1], required_args: spec[2], path_arg: spec[3],
    schema: {}, allowUnknown: true,
  };
}


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

function isPathValue(kind, key, value) {
  if (typeof value !== 'string' || !value) return false;
  const k = (kind === 'key' && key !== null) ? String(key).toLowerCase() : '';
  if (URL_KEYS.has(k)) return false;
  if (k && PATH_KEYS.has(k)) return true;
  const v = value.trim();
  return BARE_PATH_RE.test(v) && !/\s/.test(v);
}

// 工作区感知：工作区内绝对路径 → 转成工作区相对路径（不判逃逸）；越界才逃逸。
function locatePath(norm, workspaceRoot) {
  if (norm.includes('\u0000')) return { rel: norm, escape: true };
  const low = norm.toLowerCase();
  if (low.startsWith('//')) return { rel: norm, escape: true };
  const abs = DRIVE_RE.test(norm) || norm.startsWith('/');
  if (!abs) return { rel: norm, escape: norm.split('/').includes('..') };
  const root = String(workspaceRoot || '').toLowerCase().replace(/\\/g, '/').replace(/\/+$/, '');
  if (root && low === root) return { rel: '', escape: false };
  if (root && low.startsWith(root + '/')) {
    return { rel: norm.slice(root.length + 1), escape: false };
  }
  return { rel: norm, escape: true };
}

function pathInfo(args, workspaceRoot) {
  const rels = [];
  let escape = false;
  let glob = false;
  let sensitive = false;
  for (const [kind, key, value] of walk(args)) {
    if (!isPathValue(kind, key, value)) continue;
    const norm = resolveDotDot(normText(value).replace(/\\/g, '/'));
    const loc = locatePath(norm, workspaceRoot);
    rels.push(loc.rel.toLowerCase());
    escape = escape || loc.escape;
    glob = glob || /[*?]/.test(loc.rel);
    sensitive = sensitive || SENSITIVE_PATH_RE.test(loc.rel.toLowerCase());
  }
  return { paths: rels, escape, glob, sensitive };
}

function urlCandidates(args) {
  const out = [];
  for (const [kind, key, value] of walk(args)) {
    if (typeof value !== 'string' || !value) continue;
    const k = (kind === 'key' && key !== null) ? String(key).toLowerCase() : '';
    const v = value.trim();
    if (URL_KEYS.has(k)) out.push(v);
    else if (/^https?:\/\/\S+$/i.test(v)) out.push(v);
  }
  return out;
}

function hostOf(url) {
  const m = /^[a-z][a-z0-9+.-]*:\/\/([^/?#]+)/i.exec(String(url));
  if (!m) return '';
  return m[1].replace(/^.*@/, '').replace(/:\d+$/, '').toLowerCase();
}

function urlHostsAllowlisted(urls, allowlist) {
  if (!urls.length) return false;
  const allow = (allowlist || []).map((a) => String(a).toLowerCase());
  return urls.every((u) => {
    const host = hostOf(u);
    return allow.some((a) => host === a || host.endsWith('.' + a));
  });
}

function cmdStrings(args) {
  const out = [];
  for (const [kind, key, value] of walk(args)) {
    if (kind === 'key' && key !== null && CMD_KEYS.has(String(key).toLowerCase())
        && typeof value === 'string') out.push(value);
  }
  return out;
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
  const workspaceRoot = String(state.workspace_root || state.workspaceRoot || '');
  const pinfo = pathInfo(args, workspaceRoot);
  const urls = urlCandidates(args);
  const ctx = {
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
    paths: pinfo.paths,
    pathEscape: pinfo.escape,
    pathGlob: pinfo.glob,
    sensitivePath: pinfo.sensitive,
    workspaceRoot,
    urls, urlPresent: urls.length > 0,
    urlHostAllowlisted: urlHostsAllowlisted(urls, policy.egress_allowlist),
    cmdStrings: cmdStrings(args),
    target: targetOf(args),
    argsBytes: Buffer.byteLength(canon(args), 'utf8'),
    strings: [...strings(args)],
    at: Number(state.at || 0),
    approvalChannel: Boolean(state.approval_channel || policy.approval_channel),
  };
  // 相位豁免（docs/24 §4.1）：normal 相位且无冲突/未饱和 → 写操作按 low_risk 记录，不再加严。
  const satAny = ctx.saturated.valence || ctx.saturated.arousal || ctx.saturated.dominance;
  ctx.phaseRelaxed = ctx.phase === 'normal' && !ctx.variety && !ctx.unresolved && !satAny;
  ctx.lowRisk = Boolean(ctx.toolMutating && ctx.phaseRelaxed);
  return ctx;
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
  if (!args || typeof args !== 'object' || Array.isArray(args)) {
    // t-uca-1.3（误报根治）：宿主常把 arguments 序列化成 JSON 字符串，这里旧行为恒真
    // → read/grep/write/edit 每次调用都误报 T-UCA-09（实测 100%）。
    // "读不懂的载荷"不等于"攻击"：真正的注入特征由 T-UCA-09 的两条 arg_regex 覆盖；
    // 而 allowUnknown=true + schema={} 让"未知键"分支本来就是死代码。
    ctx.argsUnparsed = true;
    return false;
  }
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
  if ('path_glob' in value) return ctx.pathGlob === Boolean(value.path_glob);
  if ('sensitive_path' in value) return ctx.sensitivePath === Boolean(value.sensitive_path);
  if ('arg_regex_cmd' in value) {
    const rx = compilePattern(value.arg_regex_cmd);
    return ctx.cmdStrings.some((s) => rx.test(s));
  }
  if ('url_present' in value) return ctx.urlPresent === Boolean(value.url_present);
  if ('url_host_allowlisted' in value) {
    return ctx.urlHostAllowlisted === Boolean(value.url_host_allowlisted);
  }
  if ('phase_relaxed' in value) return ctx.phaseRelaxed === Boolean(value.phase_relaxed);
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
    // 可执行性（2026-10-28 修误报）：只有登记表里的工具才可能被本插件真正约束。
    // 未登记 = 宿主工具（read/write/pwsh/grep…），事后路径只能记 would_block，不得宣称已拦截。
    enforceable: ctx.entry !== null && ctx.entry !== undefined,
    would_block: (ctx.entry === null || ctx.entry === undefined) && verdict !== 'allow',
    // 相位豁免落点：normal 相位下的写/执行类调用按 low_risk 记录（可审计、不打扰）。
    low_risk: Boolean(ctx.lowRisk),
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

// T-UCA-09 是 5 条谓词的 any，而 reason 走静态表 REASON_CODES → 不管实际命中哪条都写
// schema_invalid。2026-10-30 排查被这个标签带偏了好几轮，故这里单独回放谓词给出 reason_detail。
// 顺序必须与 POLICY 里 T-UCA-09.when.any 逐项一致。
const TUCA09_DETAILS = ['schema_invalid', 'arg_control_char', 'arg_injection_marker', 'tool_name', 'homoglyph'];
function tuca09Detail(ctx, policy) {
  const rule = ruleOf(policy, 'T-UCA-09');
  const any = rule && rule.when && Array.isArray(rule.when.any) ? rule.when.any : null;
  if (!any) return 'unknown';
  for (let i = 0; i < any.length; i++) {
    try {
      if (pred(any[i], ctx)) return TUCA09_DETAILS[i] || ('pred_' + i);
    } catch { /* 谓词异常不影响主判定 */ }
  }
  return 'unknown';
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
    // 只有 T-UCA-09 需要细分：它的 reason 来自静态表，分不清实际命中的谓词。
    const reasonDetail = ucas[0] === 'T-UCA-09' ? tuca09Detail(ctx, policy) : null;
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
      // 实际命中的谓词（仅 T-UCA-09 有意义，其余为 null）。见 tuca09Detail 的注释。
      reason_detail: reasonDetail,
      // 与 audit 事件同口径：enforceable=false 表示"本插件管不到这个工具"（未登记/宿主工具），
      // 调用方不得据此置"待澄清"标志或注入强提示，只能记 would_block。
      enforceable: ctx.entry !== null && ctx.entry !== undefined,
      would_block: (ctx.entry === null || ctx.entry === undefined) && verdict !== 'allow',
      low_risk: Boolean(ctx.lowRisk),
      state_inputs_used: [...new Set(hits.flatMap((r) => r.state_inputs || []))].sort(),
      audit: event,
      tool_gate: event,
    };
    if (event && reasonDetail) event.reason_detail = reasonDetail;
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
 * policyProvider: 可选 (args, ctx) => policy | Promise<policy>；用于把工作区策略（deny-only 派生）
 *   注入判定，而不改全局 POLICY / evaluation_order。取不到就回退 spec.policy / POLICY。
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
    let policy = spec.policy || POLICY;
    if (typeof spec.policyProvider === 'function') {
      try {
        const provided = await spec.policyProvider(args, ctx);
        if (provided && typeof provided === 'object') policy = provided;
      } catch { /* 策略来源失败 → 用全局策略（不能因此放宽/放行） */ }
    }
    const call = {
      id: ctx.callId || ctx.call_id || `${name}#${ctx.turn_id || ctx.turnId || 't'}`,
      tool: name,
      args,
    };
    const res = evaluate(call, state, policy);
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
