# 24 · 工具网关 UCA 矩阵（T-UCA，v5.2 增量）

> **状态**：**v1.1 冻结（权威版，2026-10-27）**；**实现已三向对齐（2026-10-27 06:28，见 §10.4）**。本文件是 ADR-013 指定的**唯一规则语义来源**；实现（`engine/tool_gateway.py`、`plugin/dsh-lingshu/tool-gateway.js`、测试）必须与之逐条一致，冲突时**以本文件为准**。
> **`policy_version = "t-uca-1.1"`**（v1.0 的 14 条 → v1.1 增补 2 条，见 §10；engine 早期实现自称 `tool-gateway-1.0` 的编号体系作废）。
> **命名空间独立**：本文所有编号为 **T-UCA-xx**，与注入侧 `UCA-01..08`（`engine/control.py:175-184`）**并列、不合并、不互相改写**。
> **不可改**：本文件不修改 `engine/`、`plugin/`、`rules/` 的任何既有语义；注入侧 8 类 UCA 一字不动（ADR-013 §3 不变量 3）。
> **默认姿态**：fail-closed。未知工具、不可判定状态、缺审计上下文 → **block**；只读且无状态风险 → **allow + 记录**。`degrade` = **不执行**，返回强提示并请求确认；无审批通道 → **block**。
> **依据**：`decisions/013-解冻行动闭环-工具网关.md`、`docs/19-控制论重构契约.md` §4、`rules/capabilities.yaml`、`rules/frozen-manifest.yaml`。

---

## 1. 威胁模型（先写清，否则矩阵没有边界）

| # | 威胁 | 具体形态 | 本矩阵的应对 | 残余风险（必须写在论文/README 里） |
|---|---|---|---|---|
| T1 | **工具副作用不可逆** | 删除/覆盖/外发/执行一旦发生，无法用「回滚提示」撤销 | 执行前判定；破坏性操作默认 block，除非精确目标 + 人工确认 | 宿主若无 pre-tool 钩子，已派发调用**无法阻断**（见 T2） |
| T2 | **宿主无 pre-tool dispatch 钩子** | DSH 当前只暴露 `tool/call` / `tool/result` 会话事件（ADR-013 §2.3） | 三层接线：引擎参考循环**真前置**阻断；`guardTool()` 对**经它注册**的工具真前置；插件侧只能**事后检测 + 下一轮强提示 + 审计** | 插件**不能**阻断任意宿主工具；只有被接入的工具受保护（绕过面） |
| T3 | **payload 可编码绕过** | base64/hex/Unicode 转义、零宽字符、同形字、JSON 嵌套、`..` 路径归一化差异 | 解码尝试 + Unicode NFKC + 路径 canonicalize + 递归深度/节点上限；不执行 payload，只做静态检查 | 检测器仍是**模式匹配**，不是语义理解；未知编码可能漏 |
| T4 | **网关自身是攻击面** | ReDoS、超长参数 DoS、schema 解析歧义、check-exec TOCTOU | 正则只允许线性模式（无嵌套量词/回溯引用）；参数先限长再匹配；判定与执行之间**目标必须用判定时的快照**（同对象/同哈希） | 宿主层 TOCTOU 无法由网关完全消除；网关只给出「判定时」的裁决 |
| T5 | **工具未经过网关** | 其他插件/宿主内建工具直接执行 | 只保护「登记在 registry 且经 guardTool/引擎循环」的调用 | 网关定位是**可接入的控制点**，不是全系统强制（ADR-013 §4.4） |
| T6 | **判定依赖的状态本身被污染** | 冲突判定/相变/饱和被伪造或过期 | 状态必须来自**本 turn 的 event**（含 turn_id/session_id），且记录在审计里；状态不可得 → block | 状态生产者自身的正确性不在本契约内（由 docs/19 的机制承担） |

**闸门位置（唯一）**：`模型输出解析 → [注入侧 preflight 已在模型调用前完成] → 工具网关判定 → 工具执行 → tool/result 落审计`。
网关**绝不**执行 payload，**绝不**改工具参数，只返回 `allow | degrade | block` + 强提示文案 + 审计字段。

---

## 2. 状态输入契约（VSM 状态向量）

每条规则的 `when` 只允许读取下列**已存在**的状态字段（来源均为现有实现，网关不得自造状态）：

| 字段 | 类型 | 语义 | 现有生产者（file:line） |
|---|---|---|---|
| `variety_fallback` | bool | 未定义冲突 → 已降维（丢 style、退回中性事实+澄清） | `engine/control.py:157-170`；插件 `state.control.variety.fallback`（`docs/19:201,249`） |
| `unresolvedConflict` | bool | 同一 `(subject,relation)` 存在多个 active 且 object 不同 | 插件 `detectConflicts`（`docs/19:248`）；引擎侧 `conflict` 字段（`engine/pipeline.py:148`） |
| `phase` | `normal\|shift\|critical` | 上下文用量相位，**先判 hard 再判 ratio** | `engine/control.py:103-109`（0.70/0.85） |
| `saturated` | `{valence,arousal,dominance: bool}` | 情感积分基线已到 cap（锁存） | `engine/control.py:60-68` |
| `frozen_ok` | bool | 冻结区哈希校验通过 | `engine/constitution.py:159-173`；`engine/pipeline.py:183` |
| `capabilities` | `{declared:[], granted:[], notRequested:[]}` | 能力声明与宿主实授 | `rules/capabilities.yaml:14-31` |
| `audit_context` | `{present:bool, session_id, turn_id, call_id}` | 本次调用的审计上下文是否完整 | 事件必填字段：`engine/pipeline.py:185-203`；`contracts/event-bus.yaml` |
| `tool_registry` | `{tool: {class, caps, required_args, path_arg}}` | 已登记工具及其静态属性。**按 lead 决定：不新建 `rules/tool-registry.yaml`**；registry **内联在引擎与插件两处实现**，由 parity 测试（`id/verdict/detector` 逐条一致）保证不漂移 | 实现侧 policy（a5） |

`tool_class` 闭集（由 registry 静态声明，不由 payload 自称）——**与 engine v1.0 实现对齐**：
`read | write | destructive | exec | network`。
**mutating** = `{write, destructive, exec, network}`；**只读** = `{read}`。
（`class` 之外的细分语义用 registry 的 `caps` 表达，不再扩展 class 枚举，避免两侧漂移。）

**判定顺序**：按 §4 的 `evaluation_order` 求值（静态 block 在前、状态 degrade 在后、审计最后）；**block 优先于 degrade 优先于 allow**；多命中时全部记入 `ucas[]`，`verdict` 取最高严重度。全部不命中 → 走默认策略（§4）。

---

## 3. T-UCA 矩阵（16 条：v1.0 的 01–14 语义不变 + v1.1 增补 15/16）

> 每条格式：**触发条件 → 依赖状态 → 判定 → 强提示（中/英）→ 测试用例**。
> `degrade` 语义：**不执行原调用**；返回强提示 + 要求显式人工确认（宿主 `approval` seam），确认后以**同一快照**重放；无审批通道 → 视为 block（fail-closed）。

### T-UCA-01 未定义冲突/降维态下写操作
- **触发**：`tool_class` 属于 mutating，且（`variety_fallback=true` 或 `unresolvedConflict=true`）。
- **依赖状态**：`variety_fallback`、`unresolvedConflict`。
- **判定**：**block**。
- **强提示（中）**：`STPA 前置闸拦截（T-UCA-01）：当前存在未澄清的记忆冲突，系统已处于中性降维态。任何写/删/外发/执行类工具调用都被拒绝。请先说明以哪一条记录为准，再重试。`
- **强提示（英）**：`STPA pre-flight gate denied (T-UCA-01): an unresolved memory conflict keeps the system in variety-reduced mode. All mutating tool calls are blocked until you state which record is authoritative.`
- **测试**：`tests/test_tool_gateway.py::test_tuca_01_unresolved_write`

### T-UCA-02 隐私/凭证明文
- **触发**：`args` 任意字符串命中凭证/PII 模式（`api[_-]?key`、`apikey`、`access[_-]?token`、`token`、`secret`、`password`、`passwd`、`credential`、`private[_-]?key`、`bearer`、`authorization`、`session[_-]?cookie`、`BEGIN … PRIVATE KEY`、`sk-…`、`ghp_…`、`AKIA…`、`xox[baprs]-…`、JWT 三段式、中国大陆手机号 `1[3-9]\d{9}`、身份证 `\d{17}[\dXx]`），**或**参数路径是凭据类文件（`~/.dsh/.credentials*`、`.env`、`id_rsa`、`id_ed25519`、`*.pem/*.key/*.pfx/*.p12`、`.netrc`、`.pgpass`）→ 一律视为外泄前置。
- **依赖状态**：`capabilities`（credentials 是否被声明/授予）。
- **判定**：**block**（无论工具类；只读读取凭证也算）。
- **强提示（中）**：`STPA 前置闸拦截（T-UCA-02）：参数中包含凭证/密钥/令牌模式，或目标是凭证文件。网关不代管凭证，此类调用一律拒绝；如需传递密钥，请改由宿主凭证通道完成。`
- **强提示（英）**：`STPA pre-flight gate denied (T-UCA-02): the arguments look like credentials or the target is a credential store. This gateway never handles secrets; use the host credential channel instead.`
- **测试**：`test_tuca_02_secret_in_args`

### T-UCA-03 破坏性删除/覆盖
- **触发**：`tool_class ∈ {destructive, write}` 且（`op ∈ {delete, remove, rmdir, del, truncate, overwrite}` 或 `force=true` 或 `recursive=true` 或 目标含通配 `*`/`?`/`**`），且**没有**与目标逐字一致的 `confirmation{target, token}`。
- **依赖状态**：无（纯参数）。
- **判定**：**block**（递归/通配直接 block；单目标精确删除亦需确认，未确认即 block）。
- **强提示（中）**：`STPA 前置闸拦截（T-UCA-03）：这是删除/覆盖类操作，且目标未取得与你输入完全一致的确认。请给出单个精确路径并显式确认后再试。`
- **强提示（英）**：`STPA pre-flight gate denied (T-UCA-03): destructive delete/overwrite without an exact, matching confirmation. Provide a single explicit path and confirm it.`
- **测试**：`test_tuca_03_destructive_delete`、`test_tuca_03_recursive_blocked`

### T-UCA-04 受保护路径（冻结清单）
- **触发**：`args` 中任意路径经 NFKC + `..` 归一化 + 大小写折叠后，命中受保护前缀：`rules/`、`engine/`、`plugin/`、`replay-set/`、`chaos-set/`、`acceptance/`、`decisions/`、`docs/01-宪法层.md`、`docs/15-验收清单.md`、`docs/24-工具网关UCA.md`、`_frozen/`、`.git/`、`.env`（**并集口径**：契约比 engine v1.0 的清单更严，`plugin/` 整目录受保护，不只两个文件）。
- **依赖状态**：`frozen_ok`（仅用于审计记录；本规则不因其为 true 而放行）。
- **判定**：**block**。
- **强提示（中）**：`STPA 前置闸拦截（T-UCA-04）：目标属于冻结/受保护区域（宪法、规则表、回放集、插件与引擎源码）。该区域只能由人工按 ADR 流程修改。`
- **强提示（英）**：`STPA pre-flight gate denied (T-UCA-04): the target is a frozen or protected path (constitution, rule tables, replay set, plugin/engine sources). These change only through a human ADR process.`
- **测试**：`test_tuca_04_protected_path`、`test_tuca_04_path_traversal_normalized`

### T-UCA-05 非白名单网络外发
- **触发**：工具 capability 含 `network`，或参数含 URL/主机/`webhook`/`http(s)://`；且目标主机**不在** `egress_allowlist`（默认**空**）。`network` 在 `notRequested` 中（`rules/capabilities.yaml:21`）⇒ 默认全部 block。
- **依赖状态**：`capabilities`。
- **判定**：**block**（白名单为空时等价于全禁；未来若开白名单，仅精确主机+路径前缀可 allow）。
- **强提示（中）**：`STPA 前置闸拦截（T-UCA-05）：网络外发不在白名单内（当前白名单为空）。记忆与人格系统不申请网络能力；任何外发都需人工放行。`
- **强提示（英）**：`STPA pre-flight gate denied (T-UCA-05): egress is not allow-listed (the list is empty by default). This system requests no network capability; external sends need human approval.`
- **测试**：`test_tuca_05_egress_blocked`、`test_tuca_05_allowlist_empty`

### T-UCA-06 任意代码执行
- **触发**：工具 capability ∈ `{shell, dynamic-code}`（二者均在 `notRequested`），或 `tool_class=exec`，或参数命中执行面：`eval(`、`exec(`、`subprocess`、`os.system`、`child_process`、`bash -c`、`sh -c`、`pwsh -c`、`powershell -`、`node -e`、`python -c`、反引号、`${...}` 命令替换。
- **依赖状态**：`capabilities`。
- **判定**：**block**。
- **强提示（中）**：`STPA 前置闸拦截（T-UCA-06）：检测到代码执行能力或执行面参数。本系统不申请 shell/dynamic-code 能力，任何执行类调用一律拒绝。`
- **强提示（英）**：`STPA pre-flight gate denied (T-UCA-06): code execution capability or execution-shaped arguments detected. This system requests no shell/dynamic-code capability; exec calls are always denied.`
- **测试**：`test_tuca_06_exec_blocked`

### T-UCA-07 超大/递归 payload
- **触发**：`args` 序列化后 **> 64 KiB**；或 JSON 深度 **> 8**；或数组元素 **> 1000**；或节点总数 **> 5000**；或同一键在展开链上重复出现 > 32 次（疑似递归炸弹）；或字符串含 ≥ 2 层编码前缀（`base64:`/`hex:`/`%` 叠加）。
- **依赖状态**：无（纯形状）。
- **判定**：**block**（限长在正则匹配**之前**执行，防 ReDoS/DoS）。
- **强提示（中）**：`STPA 前置闸拦截（T-UCA-07）：参数体积/嵌套/编码层数超过网关上限。请拆分请求或减少嵌套后重试。`
- **强提示（英）**：`STPA pre-flight gate denied (T-UCA-07): payload exceeds size, depth or encoding-layer limits. Split the request or reduce nesting and retry.`
- **测试**：`test_tuca_07_oversize`、`test_tuca_07_deep_nesting`

### T-UCA-08 未登记工具
- **触发**：`tool` 不在 `tool_registry`；或 registry 中缺 `class/mutating/capabilities/schema` 任一字段（不可判定）。
- **依赖状态**：`tool_registry`。
- **判定**：**block**（fail-closed：未登记 = 未知 = 拒绝）。
- **强提示（中）**：`STPA 前置闸拦截（T-UCA-08）：该工具未在网关登记，无法判定其副作用类别。未登记工具一律拒绝；请由人工登记后再调用。`
- **强提示（英）**：`STPA pre-flight gate denied (T-UCA-08): tool is not registered with the gateway, so its side effects are unknown. Unregistered tools are denied; register it first.`
- **测试**：`test_tuca_08_unregistered_tool`、`test_tuca_08_incomplete_registry_entry`

### T-UCA-09 schema 非法 / 分隔符注入
- **触发**：参数不匹配 registry schema（缺必填/类型不符/未知键未声明 `allowUnknown:true`）；或字符串含控制字符 `U+0000–U+001F`、`U+2028/U+2029`、零宽字符（`U+200B–U+200F`、`U+2060`）、双向控制符（`U+202A–U+202E`）；或出现对话模板分隔符（`<|im_start|>`、`<|im_end|>`、`</system>`、`[INST]`、`[/INST]`、`### system`、可执行围栏 ```` ```system ````、`\n\nHuman:`、`\n\nAssistant:`）；或工具名经 NFKC 后与已登记名**不同但视觉相同**（同形字）；或工具名含 `/`、`\`、空白、`:`, `;`, 换行。
- **依赖状态**：`tool_registry`（schema）。
- **判定**：**block**。
- **强提示（中）**：`STPA 前置闸拦截（T-UCA-09）：参数不符合该工具的 schema，或含有控制字符/分隔符/同形字等注入迹象。请按工具文档提交干净参数。`
- **强提示（英）**：`STPA pre-flight gate denied (T-UCA-09): arguments violate the tool schema or contain control characters, delimiters or homoglyphs. Submit clean arguments per the tool documentation.`
- **测试**：`test_tuca_09_schema_invalid`、`test_tuca_09_delimiter_injection`

### T-UCA-10 相变 critical 下的写操作
- **触发**：`phase=critical` 且 `tool_class` 属于 mutating。
- **依赖状态**：`phase`。
- **判定**：**degrade** → 无审批通道则 **block**。理由：长上下文末期上下文已被灌满，写操作的目标/参数更容易被污染；先请人工确认再执行。
- **强提示（中）**：`STPA 前置闸降级（T-UCA-10）：当前上下文处于临界相位（≥0.85）。写类操作已暂缓，需你确认目标与内容后再执行。`
- **强提示（英）**：`STPA pre-flight gate held (T-UCA-10): context is in the critical phase (>=0.85). Mutating calls are paused until you confirm target and content.`
- **测试**：`test_tuca_10_critical_write_degrade`、`test_tuca_10_no_approval_blocks`

### T-UCA-11 缺失审计上下文
- **触发**：`audit_context.present=false`（缺 `session_id`/`turn_id`/`call_id` 任一），或审计写入预检失败。
- **依赖状态**：`audit_context`。
- **判定**：**block**（不可审计 ⇒ 不执行）。
- **强提示（中）**：`STPA 前置闸拦截（T-UCA-11）：本次调用缺少完整审计上下文，无法留下可追溯记录。请修复会话/轮次标识后重试。`
- **强提示（英）**：`STPA pre-flight gate denied (T-UCA-11): the audit context is incomplete, so the call cannot be traced. Restore the session/turn identifiers and retry.`
- **测试**：`test_tuca_11_missing_audit_context`

### T-UCA-12 能力未声明
- **触发**：工具声明的 `capabilities` 中存在**未在 `declared` 中声明**、或**未在宿主 `granted` 中实授**的项（尤其 `network/shell/credentials/dynamic-code/subagent/env`）。
- **依赖状态**：`capabilities`。
- **判定**：**block**（"用多少申请多少"；多授即告警，未授即拒绝）。
- **强提示（中）**：`STPA 前置闸拦截（T-UCA-12）：该工具需要未声明的能力（网络/命令/凭证/动态代码等）。本系统按最小能力原则运行，未声明能力一律不授予。`
- **强提示（英）**：`STPA pre-flight gate denied (T-UCA-12): the tool requires a capability that is neither declared nor granted (network/shell/credentials/dynamic-code). Least-capability applies; undeclared capabilities are refused.`
- **测试**：`test_tuca_12_undeclared_capability`

### T-UCA-13 积分饱和 + 不可逆写
- **触发**：`saturated` 任一维为 true，且 `tool_class ∈ {write, destructive}`（不可逆/半不可逆），且无逐目标 `confirmation`。
- **依赖状态**：`saturated`。
- **判定**：**degrade** → 无审批通道则 **block**。理由：饱和意味着用户连续负向反馈下基线已到 cap，此时自动写入长期偏好/删除记录最容易留下难以恢复的偏移。
- **强提示（中）**：`STPA 前置闸降级（T-UCA-13）：当前处于情感基线饱和状态，写/删类操作已暂缓，请确认后再执行。`
- **强提示（英）**：`STPA pre-flight gate held (T-UCA-13): the affective baseline is saturated; irreversible writes/deletes are paused for confirmation.`
- **测试**：`test_tuca_13_saturated_write_degrade`

### T-UCA-14 冻结校验失败（纵深防御）
- **触发**：`frozen_ok=false` 且 `tool_class` 属于 mutating。
- **依赖状态**：`frozen_ok`。
- **判定**：**block**。注：注入侧 `UCA-02` 已在模型调用前 fail-closed；本规则是**网关侧纵深防御**（状态串味/重放/伪造 frozen 标志时仍拒绝）。
- **强提示（中）**：`STPA 前置闸拦截（T-UCA-14）：宪法冻结区校验未通过。在完整性恢复前，任何写类工具调用都被拒绝。`
- **强提示（英）**：`STPA pre-flight gate denied (T-UCA-14): the frozen-region integrity check failed. Mutating tool calls are denied until integrity is restored.`
- **测试**：`test_tuca_14_frozen_fail_blocks`

### T-UCA-15 路径逃逸（v1.1，吸收自 engine 实现）
- **触发**：参数中的路径（`path`/`target`/`url` 的文件部分）出现 `..` 段、绝对路径（`/…`）、Windows 盘符（`C:`）、UNC（`\\host`）、或空字节 `\u0000`；不要求同时命中受保护前缀。
- **依赖状态**：无（纯参数）。与 T-UCA-04 的区别：04 拦"受保护的**具体路径**"，15 拦"任何**逃出工作区**的路径"，两者可同时命中。
- **判定**：**block**。
- **强提示（中）**：`STPA 前置闸拦截（T-UCA-15）：参数中的路径试图逃出工作区（../、绝对路径、盘符、UNC 或空字节）。网关只允许工作区内的相对路径。`
- **强提示（英）**：`STPA pre-flight gate denied (T-UCA-15): the path escapes the workspace (../, absolute path, drive letter, UNC, or null byte). Only relative paths inside the workspace are allowed.`
- **测试**：`test_tuca_15_path_escape`

### T-UCA-16 相变 shift 期破坏性操作（v1.1，吸收自 engine 实现）
- **触发**：`phase=shift` 且 `tool_class=destructive`。
- **依赖状态**：`phase`。
- **判定**：**degrade** → 无审批通道则 **block**。理由：shift 是"上下文正在变满"的预警相位，此时破坏性操作的目标/参数最容易受长文本污染；与 critical 下的全类 mutating（T-UCA-10）相比，本规则只针对 destructive，粒度更细。
- **强提示（中）**：`STPA 前置闸降级（T-UCA-16）：上下文处于预警相位（≥0.70），删除/覆盖类操作已暂缓，请二次确认目标后再执行。`
- **强提示（英）**：`STPA pre-flight gate held (T-UCA-16): context is in the warning phase (>=0.70). Destructive calls are paused for re-confirmation of the target.`
- **测试**：`test_tuca_16_shift_destructive_degrade`

---

## 4. 默认策略与判定优先级

| 情形 | 判定 | 记录 |
|---|---|---|
| 只读（`class=read`）且无任何 T-UCA 命中 | **allow** | 必记 `tool_gate`：tool/args 摘要/verdict=allow/ucas=[]/policy_version/state 快照 |
| 未知工具 / registry 缺字段 / 状态不可判定 / 审计不可写 | **block** | 同上，`verdict=block`，并在 `ucas` 里写触发项（未知→T-UCA-08/11） |
| `phase` 不可判定（`unknown`）且 `mutating` | **block** | fail-closed；`ucas` 记 T-UCA-10（相位不可判定视同 critical 的更严处理，见 §10 变更说明） |
| 多规则命中 | **取最高严重度**（block > degrade > allow） | 所有命中 id 全部入 `ucas[]`（**不截断**） |
| `degrade` 且无 `approval` 通道 | **降级为 block** | `verdict=block`、`degraded_to=block`（**degrade 绝不等于放行执行**） |
| 参数解析失败（非法 JSON / 非法 UTF-8） | **block** | `reason=parse_error`（不猜测意图） |

**判定顺序**：`T-UCA-01,02,03,04,05,06,07,08,09,12,14,15,10,13,16,11`（先硬 block 与静态检查，再状态相关 degrade，最后审计）；每步短路，但**已命中项仍并入 `ucas[]`**。

---

## 5. 与注入侧 UCA-01..08 的关系

| 维度 | 注入侧 UCA | 工具侧 T-UCA |
|---|---|---|
| 命名空间 | `UCA-01..08`（`engine/control.py:175-184`） | `T-UCA-01..16`（本文，`policy_version=t-uca-1.1`） |
| 作用对象 | 即将发给模型的 **prompt** | 模型已产出的 **tool_use 调用** |
| 时机 | 拼装后、模型调用前（`preflight_audit`） | 模型输出解析后、工具执行前 |
| 失败语义 | `block` 即 `prompt=None`（fail-closed） | `block` 即不执行（fail-closed）；`degrade`=不执行+等确认，无确认通道→block |
| 事件字段 | `audit{verdict,ucas,downgrades}` | `tool_gate{schema,policy_version,call_id,session_id,turn_id,tool,tool_class,verdict,allowed,ucas[],degraded_to,requires_confirmation,reason,audit{args_digest,args_keys,args_bytes,args_preview,phase,variety_fallback,frozen_ok,saturated}}` |
| 合并规则 | **不合并**：`tool_gate.ucas` 只放 T-UCA，`audit.ucas` 只放 UCA；按 `event.type` 分流 | 同上 |

> **字段命名对齐（ADR-013 §3 原文）**：工具网关审计事件里承载 T-UCA 编号的字段名就叫 **`ucas`**（与 task-20 的实现口径一致）。它与注入侧事件 `audit.ucas` **同名但不同事件类型**：`tool_gate.ucas` 只允许出现 `T-UCA-xx`，`audit.ucas` 只允许出现 `UCA-0x`；两侧解析器按 `event.type` 分流，**绝不合并**。若实现方额外想保留工具侧别名，可附加 `tucas` 字段，但 `ucas` 为权威字段。

**优先级**：注入侧在前（模型没被调用就不会有工具调用）。若同一 turn 注入侧已 block，则不会走到网关；若网关 block，则本 turn 不再重试同一调用（除非用户显式澄清后重放）。

---

## 6. 审计事件契约（`tool_gate`）

```jsonc
{
  "schema": "lingshu/tool-gate/v1",
  "type": "tool_gate",                 // 事件类型；与注入侧 prompt 事件按 type 分流
  "policy_version": "t-uca-1.1",
  "at": 1800000000.0,
  "session_id": "s_x", "turn_id": "t_010", "call_id": "call_7",
  "tool": "write_file",
  "tool_class": "write",               // read|write|destructive|exec|network
  "verdict": "block",                  // allow | degrade | block
  "allowed": false,                    // 只有 allow 为 true；degrade 也必须是 false
  "ucas": ["T-UCA-04"],                // 工具侧编号；全部命中，按判定顺序。字段名就是 ucas
  "degraded_to": "block",              // 仅 degrade 且无审批通道时
  "requires_confirmation": true,       // degrade 或 block 时 true
  "reason": "T-UCA-04:protected_path_write",
  "audit": {                           // 只抄所需字段，用于事后复盘
    "kind": "tool_gate",
    "args_digest": "sha256:…", "args_keys": ["path"], "args_bytes": 42,
    "args_preview": "…",               // ≤120 字，已脱敏
    "phase": "normal",
    "variety_fallback": false, "frozen_ok": true,
    "saturated": {"valence": false, "arousal": false, "dominance": false}
  },
  "snapshot": {"target": "…", "mtime": null, "hash": null}   // 供 check-exec 一致性核对
}
```

**硬约束**：`args_digest` 必须存在；`verdict` 只能取三值；**`ucas` 只能出现 `T-UCA-xx`**（为空仅当 `verdict=allow`）；`allowed` 仅在 `verdict=allow` 时为 `true`（**degrade 不得置 true**）；`audit` 字段只允许本契约列出的键（防状态串味）。

---

## 7. 机器可读规则数组（a5 直接采用）

> 语言中立：`when`/`args_check` 是数据，不是代码；正则使用**线性模式**（无嵌套量词、无回溯引用、无 lookbehind），保证 Python/JS 语义一致。`verdict` 取 `block|degrade|allow`。**`degrade` = 不执行原调用**：返回强提示并请求确认；无审批通道 → 按 `block` 处理；实现中 `allowed` 必须为 `false`。
> **提交前自检（硬性）**：① `json.loads(§7 代码块)` 必须成功；② 16 个 `id` 唯一；③ `set(evaluation_order) == set(ids)`；④ 每条含 `id/verdict/when/args_check/state_inputs/test_id/message_zh/message_en`。任何一条不过，本文件不算可引用。

```json
{
  "policy_version": "t-uca-1.1",
  "namespace": "T-UCA",
  "default": {"unknown": "block", "read_only": "allow_with_log", "phase_unknown_mutating": "block"},
  "evaluation_order": ["T-UCA-01","T-UCA-02","T-UCA-03","T-UCA-04","T-UCA-05","T-UCA-06","T-UCA-07","T-UCA-08","T-UCA-09","T-UCA-12","T-UCA-14","T-UCA-15","T-UCA-10","T-UCA-13","T-UCA-16","T-UCA-11"],
  "rules": [
    {"id":"T-UCA-01","verdict":"block",
     "when":{"all":[{"tool_mutating":true},{"any":[{"state":"variety_fallback","eq":true},{"state":"unresolvedConflict","eq":true}]}]},
     "args_check":[],
     "state_inputs":["variety_fallback","unresolvedConflict"],
     "test_id":"test_tuca_01_unresolved_write",
     "message_zh":"STPA 前置闸拦截（T-UCA-01）：当前存在未澄清的记忆冲突，系统处于中性降维态。所有写/删/外发/执行类调用被拒绝。请先说明以哪一条记录为准。",
     "message_en":"STPA pre-flight gate denied (T-UCA-01): an unresolved memory conflict keeps the system in variety-reduced mode. All mutating tool calls are blocked until the authoritative record is stated."},
    {"id":"T-UCA-02","verdict":"block",
     "when":{"any":[{"arg_regex":"(?i)(api[_-]?key|apikey|access[_-]?token|token|secret|password|passwd|credential|private[_-]?key|bearer|authorization|session[_-]?cookie|-----BEGIN [A-Z ]*PRIVATE KEY-----|sk-[A-Za-z0-9_\\-]{16,}|ghp_[A-Za-z0-9]{20,}|AKIA[0-9A-Z]{16}|xox[baprs]-[A-Za-z0-9\\-]{10,}|eyJ[A-Za-z0-9_\\-]{10,}\\.[A-Za-z0-9_\\-]{10,}\\.|\\b1[3-9]\\d{9}\\b|\\b\\d{17}[\\dXx]\\b)"},{"arg_regex":"(?i)((^|/)(\\.env|id_rsa|id_ed25519|credentials(\\.json)?|\\.netrc|\\.pgpass)$|\\.(pem|key|pfx|p12)$)"},{"capability":"credentials"}]},
     "args_check":[{"kind":"regex","over":"all_strings","max_len_bytes":65536}],
     "state_inputs":["capabilities"],
     "test_id":"test_tuca_02_secret_in_args",
     "message_zh":"STPA 前置闸拦截（T-UCA-02）：参数包含凭证/密钥/令牌/PII 模式，或目标是凭证文件。网关不代管凭证，此类调用一律拒绝。",
     "message_en":"STPA pre-flight gate denied (T-UCA-02): credentials, secrets or PII detected, or the target is a credential store. This gateway never handles secrets."},
    {"id":"T-UCA-03","verdict":"block",
     "when":{"all":[{"tool_class_any":["destructive","write"]},{"any":[{"arg_true":"force"},{"arg_true":"recursive"},{"arg_true":"overwrite"},{"arg_regex":"[*?]"},{"arg_regex":"(?i)(^|/)(rm|rmdir|del|truncate)(\\s|$)"}]},{"not":{"confirmation_exact":true}}]},
     "args_check":[{"kind":"no_glob_or_recursive"}],
     "state_inputs":[],
     "test_id":"test_tuca_03_destructive_delete",
     "message_zh":"STPA 前置闸拦截（T-UCA-03）：这是删除/覆盖类操作，未取得与你输入完全一致的确认。请给出单个精确路径并显式确认。",
     "message_en":"STPA pre-flight gate denied (T-UCA-03): destructive delete/overwrite without an exact matching confirmation. Provide a single explicit path and confirm."},
    {"id":"T-UCA-04","verdict":"block",
     "when":{"any":[{"path_prefix_any":["rules/","engine/","plugin/","replay-set/","chaos-set/","acceptance/","decisions/","docs/01-宪法层.md","docs/15-验收清单.md","docs/24-工具网关UCA.md","_frozen/",".git/",".env"]},{"path_escape":true}]},
     "args_check":[{"kind":"path_normalize","unicode":"NFKC","resolve_dotdot":true,"case_fold":true}],
     "state_inputs":["frozen_ok"],
     "test_id":"test_tuca_04_protected_path",
     "message_zh":"STPA 前置闸拦截（T-UCA-04）：目标属于冻结/受保护区域（宪法、规则表、回放集、引擎与插件源码）。该区域只能由人工按 ADR 流程修改。",
     "message_en":"STPA pre-flight gate denied (T-UCA-04): target is a frozen or protected path. It changes only through a human ADR process."},
    {"id":"T-UCA-05","verdict":"block",
     "when":{"any":[{"capability":"network"},{"arg_regex":"(?i)(https?://|webhook|ftp://|ssh://)"}]},
     "args_check":[{"kind":"egress_allowlist","allowlist":[],"match":"exact_host_then_path_prefix"}],
     "state_inputs":["capabilities"],
     "test_id":"test_tuca_05_egress_blocked",
     "message_zh":"STPA 前置闸拦截（T-UCA-05）：网络外发不在白名单内（默认空）。记忆与人格系统不申请网络能力；外发需人工放行。",
     "message_en":"STPA pre-flight gate denied (T-UCA-05): egress is not allow-listed (empty by default). No network capability is requested; external sends need human approval."},
    {"id":"T-UCA-06","verdict":"block",
     "when":{"any":[{"capability_any":["shell","dynamic-code"]},{"tool_class_any":["exec"]},{"arg_regex":"(?i)(eval\\(|exec\\(|subprocess|os\\.system|child_process|bash\\s+-c|sh\\s+-c|pwsh\\s+-c|powershell\\s+-|node\\s+-e|python\\s+-c|`|\\$\\{)"}]},
     "args_check":[],
     "state_inputs":["capabilities"],
     "test_id":"test_tuca_06_exec_blocked",
     "message_zh":"STPA 前置闸拦截（T-UCA-06）：检测到代码执行能力或执行面参数。本系统不申请 shell/dynamic-code，执行类调用一律拒绝。",
     "message_en":"STPA pre-flight gate denied (T-UCA-06): code-execution capability or execution-shaped arguments detected. Exec calls are always denied."},
    {"id":"T-UCA-07","verdict":"block",
     "when":{"any":[{"args_bytes_gt":65536},{"json_depth_gt":8},{"array_len_gt":1000},{"json_nodes_gt":5000},{"encoding_layers_gt":2}]},
     "args_check":[{"kind":"shape_limits","max_bytes":65536,"max_depth":8,"max_array":1000,"max_nodes":5000,"max_encoding_layers":2}],
     "state_inputs":[],
     "test_id":"test_tuca_07_oversize",
     "message_zh":"STPA 前置闸拦截（T-UCA-07）：参数体积/嵌套/编码层数超过网关上限。请拆分请求或减少嵌套。",
     "message_en":"STPA pre-flight gate denied (T-UCA-07): payload exceeds size, depth or encoding-layer limits. Split the request or reduce nesting."},
    {"id":"T-UCA-08","verdict":"block",
     "when":{"any":[{"tool_unregistered":true},{"registry_entry_incomplete":true}]},
     "args_check":[],
     "state_inputs":["tool_registry"],
     "test_id":"test_tuca_08_unregistered_tool",
     "message_zh":"STPA 前置闸拦截（T-UCA-08）：该工具未登记，无法判定副作用类别。未登记工具一律拒绝。",
     "message_en":"STPA pre-flight gate denied (T-UCA-08): tool not registered, side effects unknown. Unregistered tools are denied."},
    {"id":"T-UCA-09","verdict":"block",
     "when":{"any":[{"schema_invalid":true},{"arg_regex":"[\\u0000-\\u001F\\u2028\\u2029\\u200B-\\u200F\\u2060\\u202A-\\u202E]"},{"arg_regex":"(?i)(<\\|im_start\\|>|<\\|im_end\\|>|</system>|\\[INST\\]|\\[/INST\\]|\\n\\nHuman:|\\n\\nAssistant:)"},{"tool_name_has":["/","\\\\"," ",":",";","\\n"]},{"tool_name_homoglyph_conflict":true}]},
     "args_check":[{"kind":"schema","reject_unknown_keys_unless_declared":true}],
     "state_inputs":["tool_registry"],
     "test_id":"test_tuca_09_schema_invalid",
     "message_zh":"STPA 前置闸拦截（T-UCA-09）：参数不符合 schema，或含控制字符/分隔符/同形字等注入迹象。请提交干净参数。",
     "message_en":"STPA pre-flight gate denied (T-UCA-09): arguments violate the tool schema or contain control characters, delimiters or homoglyphs. Submit clean arguments."},
    {"id":"T-UCA-12","verdict":"block",
     "when":{"any":[{"capability_undeclared":true},{"capability_not_granted":true}]},
     "args_check":[],
     "state_inputs":["capabilities"],
     "test_id":"test_tuca_12_undeclared_capability",
     "message_zh":"STPA 前置闸拦截（T-UCA-12）：该工具需要未声明或未授予的能力（网络/命令/凭证/动态代码）。最小能力原则下拒绝。",
     "message_en":"STPA pre-flight gate denied (T-UCA-12): tool requires an undeclared or ungranted capability. Least-capability applies."},
    {"id":"T-UCA-14","verdict":"block",
     "when":{"all":[{"tool_mutating":true},{"state":"frozen_ok","eq":false}]},
     "args_check":[],
     "state_inputs":["frozen_ok"],
     "test_id":"test_tuca_14_frozen_fail_blocks",
     "message_zh":"STPA 前置闸拦截（T-UCA-14）：宪法冻结区校验未通过。完整性恢复前，写类调用一律拒绝。",
     "message_en":"STPA pre-flight gate denied (T-UCA-14): frozen-region integrity check failed. Mutating calls are denied until restored."},
    {"id":"T-UCA-10","verdict":"degrade",
     "when":{"all":[{"state":"phase","eq":"critical"},{"tool_mutating":true}]},
     "args_check":[],
     "state_inputs":["phase"],
     "test_id":"test_tuca_10_critical_write_degrade",
     "message_zh":"STPA 前置闸降级（T-UCA-10）：当前上下文处于临界相位（≥0.85）。写类操作已暂缓，需确认目标与内容后再执行。",
     "message_en":"STPA pre-flight gate held (T-UCA-10): context is in the critical phase (>=0.85). Mutating calls are paused for confirmation."},
    {"id":"T-UCA-13","verdict":"degrade",
     "when":{"all":[{"any":[{"state":"saturated.valence","eq":true},{"state":"saturated.arousal","eq":true},{"state":"saturated.dominance","eq":true}]},{"tool_class_any":["write","destructive"]},{"not":{"confirmation_exact":true}}]},
     "args_check":[],
     "state_inputs":["saturated"],
     "test_id":"test_tuca_13_saturated_write_degrade",
     "message_zh":"STPA 前置闸降级（T-UCA-13）：情感基线处于饱和状态，写/删类操作已暂缓，请确认后执行。",
     "message_en":"STPA pre-flight gate held (T-UCA-13): affective baseline saturated; irreversible writes/deletes paused for confirmation."},
    {"id":"T-UCA-11","verdict":"block",
     "when":{"any":[{"state":"audit_context_present","eq":false},{"audit_write_unavailable":true}]},
     "args_check":[],
     "state_inputs":["audit_context"],
     "test_id":"test_tuca_11_missing_audit_context",
     "message_zh":"STPA 前置闸拦截（T-UCA-11）：本次调用缺少完整审计上下文，无法留痕。请修复会话/轮次标识后重试。",
     "message_en":"STPA pre-flight gate denied (T-UCA-11): audit context incomplete; the call cannot be traced. Restore session/turn identifiers."},
    {"id":"T-UCA-15","verdict":"block",
     "when":{"any":[{"path_escape":true}]},
     "args_check":[{"kind":"path_normalize","unicode":"NFKC","reject":["dotdot","absolute","drive_letter","unc","null_byte"]}],
     "state_inputs":[],
     "test_id":"test_tuca_15_path_escape",
     "message_zh":"STPA 前置闸拦截（T-UCA-15）：参数中的路径试图逃出工作区（../、绝对路径、盘符、UNC 或空字节）。只允许工作区内的相对路径。",
     "message_en":"STPA pre-flight gate denied (T-UCA-15): the path escapes the workspace. Only relative paths inside the workspace are allowed."},
    {"id":"T-UCA-16","verdict":"degrade",
     "when":{"all":[{"state":"phase","eq":"shift"},{"tool_class_any":["destructive"]}]},
     "args_check":[],
     "state_inputs":["phase"],
     "test_id":"test_tuca_16_shift_destructive_degrade",
     "message_zh":"STPA 前置闸降级（T-UCA-16）：上下文处于预警相位（≥0.70），删除/覆盖类操作已暂缓，请二次确认目标后再执行。",
     "message_en":"STPA pre-flight gate held (T-UCA-16): context is in the warning phase (>=0.70). Destructive calls are paused for re-confirmation."}
  ]
}
```

---

## 8. 验收映射（a5 实现后逐条可核）

| 规则 | 危险用例（期望 block/degrade） | 良性对照（期望 allow） |
|---|---|---|
| T-UCA-01 | 冲突未决时 `memory_write` / `fs_write` | 冲突未决时 `fs_read` |
| T-UCA-02 | 参数含 `sk-…` / 目标 `~/.dsh/.credentials` | 讨论"密钥概念"的只读搜索（无凭证形态） |
| T-UCA-03 | `delete path=logs/ recursive=true` | `fs_read path=logs/` |
| T-UCA-04 | `fs_write path=rules/decay-params.yaml` | `fs_write path=notes/todo.md` |
| T-UCA-05 | `http_post url=https://x` | 本机 `fs_read` |
| T-UCA-06 | `run_shell cmd="rm -rf"` | `fs_read` |
| T-UCA-07 | 100 KiB args / 深度 12 | 1 KiB 正常 args |
| T-UCA-08 | `tool="magic_unknown"` | 登记的 `lingshu_status` |
| T-UCA-09 | 工具名含 `\n` / 参数含 `\u202E` | 合法 schema |
| T-UCA-10 | critical + `memory_write` | critical + `fs_read` |
| T-UCA-11 | 缺 `turn_id` | 完整审计上下文 |
| T-UCA-12 | 工具声明 `network` 未授予 | 声明且授予的 `fs-read` |
| T-UCA-13 | 饱和 + `memory_write` 无确认 | 饱和 + `fs_read` |
| T-UCA-14 | `frozen_ok=false` + 写 | `frozen_ok=false` + 读 |
| T-UCA-15 | `write_file path="../../etc/passwd"` / `C:\Windows\…` | `write_file path="notes/todo.md"` |
| T-UCA-16 | `phase=shift` + `delete_file` | `phase=shift` + `read_file` |

**实现红线**：Python 与 JS 两个实现必须共用本文 `rules` 数组（可 `JSON.parse` 同一份），逐条 verdict 一致；任何规则改动先改本文 + `policy_version`，再改代码。

---

## 9. 已知误杀面（必须与规则一起发布）

1. **T-UCA-10（critical 下写）**：长会话几乎总是处于 critical ⇒ 所有写操作都要确认，**确认疲劳**是最现实的误杀/绕过诱因；恢复路径：按目标白名单（如 `notes/` 下的只读日志）自动放行，需 H0 决定。
2. **T-UCA-04（受保护路径）**：`replay-set/`、`rules/` 的**合法维护**（生成器、冻结脚本）也会被拦；这些操作必须走人工/离线，不走工具网关。
3. **T-UCA-02（凭证模式）**：讨论"password 字段"的普通文档也可能触发；当前按 fail-closed 拒绝，误杀成本低于漏放。
4. **T-UCA-07（形状上限）**：合法的批量导入可能超 64 KiB；需按工具声明 `schema.maxBytes` 覆盖默认值（本契约允许 registry 放宽到 256 KiB，但不得放宽深度 8）。
5. **规则只是静态检查**：不执行 payload ⇒ 无法保证语义安全；误杀率与漏放率都**未标定**（`provenance: guess`），必须在论文 limitations 里写明。
6. **T-UCA-15（路径逃逸）**：任何绝对路径都会被拦，包括合法的"读取仓库外某个只读文档"；当前 fail-closed，若将来需要跨根目录只读，应由 H0 决定"只读根白名单"，不要改成默认放行。

---

## 10. 权威裁决与实现对齐（v1.1，2026-10-27）

**权威顺序**：`ADR-013` → **本文档（docs/24）** → `engine/tool_gateway.py` / `plugin/.../tool-gateway.js` / `tests/test_tool_gateway.py`。
实现与本文冲突时，**改实现**；若要改契约，必须 bump `policy_version` 并同步 a0（论文附录）与 a5（实现），禁止两侧各自演化。

### 10.1 v1.0 → v1.1 的变化（只增不改语义）

| # | 变化 | 理由 |
|---|---|---|
| 1 | 新增 **T-UCA-15 路径逃逸**（block） | 实现审查发现：仅"受保护前缀"不足以拦 `../../etc/passwd` / `C:\Windows`；逃出工作区本身即危险 |
| 2 | 新增 **T-UCA-16 相变 shift 期破坏性操作**（degrade） | 与 T-UCA-10（critical 全 mutating）形成两级预警，粒度为 destructive |
| 3 | `tool_class` 闭集统一为 `read/write/destructive/exec/network` | 与 engine 实现对齐，消除 10 类 vs 5 类漂移 |
| 4 | 受保护路径取**并集**（`plugin/` 整目录 + `chaos-set/`、`acceptance/`、`decisions/`、`docs/15` 等） | 契约比 engine v1.0 更严；实现需扩表 |
| 5 | T-UCA-02 增加 **PII**（手机号/身份证）与凭证文件后缀（`.pem/.key/.pfx/.p12` 等） | 与 engine 实现对齐且更严 |
| 6 | T-UCA-09 增加**对话模板分隔符**（`<|im_start|>` / `[INST]` / `\n\nHuman:` 等） | 与 engine 实现对齐 |
| 7 | `degrade` 语义**收紧**：不执行 + 请求确认；无审批通道 → block；`allowed=false` | engine v1.0 把 degrade 当"降级放行（`allowed=true`）"，与 ADR-013 fail-closed 不变量冲突，必须改 |

### 10.2 engine v1.0 → 契约 ID 映射（a5 按此重排 `RULES`）

| engine v1.0 现 ID | detector | 契约 ID（本文） | 备注 |
|---|---|---|---|
| T-UCA-01 | unknown_tool | **T-UCA-08** | 未登记工具 |
| T-UCA-02 | malformed_schema | **T-UCA-09** | schema/分隔符 |
| T-UCA-03 | unresolved_write | **T-UCA-01** | 冲突/降维态写 |
| T-UCA-04 | destructive_no_confirm | **T-UCA-03** | 破坏性无确认 |
| T-UCA-05 | protected_path_write | **T-UCA-04** | 受保护路径 |
| T-UCA-06 | credentials_plaintext | **T-UCA-02** | 凭证/PII |
| T-UCA-07 | network_not_allowlisted | **T-UCA-05** | 外发 |
| T-UCA-08 | code_exec | **T-UCA-06** | 代码执行 |
| T-UCA-09 | payload_oversize | **T-UCA-07** | 形状上限 |
| T-UCA-10 | phase_critical_write | **T-UCA-10** | verdict `block`→**`degrade`**（phase unknown 仍 block） |
| T-UCA-11 | audit_missing | **T-UCA-11** | 审计 |
| T-UCA-12 | capability_undeclared | **T-UCA-12** | 能力 |
| T-UCA-13 | path_escape | **T-UCA-15** | 路径逃逸 |
| T-UCA-14 | frozen_not_ok_write | **T-UCA-14** | 冻结校验 |
| T-UCA-15 | saturated_write | **T-UCA-13** | 语义改为"不执行+确认" |
| T-UCA-16 | phase_shift_destructive | **T-UCA-16** | 语义改为"不执行+确认" |

### 10.3 a5 必改清单（历史记录：已于 2026-10-27 06:28 全部完成，见 §10.4）

1. `POLICY_VERSION = "t-uca-1.1"`；输出的 `policy_version` 与 `SCHEMA="lingshu/tool-gate/v1"` 一致。
2. `RULES` 按 10.2 重排 **id**（`detector` 名可保留），`RULE_BY_ID`/`TITLE`/`REASON` 随之自动更新。
3. `evaluate()`：`degrade` 时 `allowed=false`、`requires_confirmation=true`；无审批通道时把 `verdict` 置 `block` 并记 `degraded_to="block"`；`ucas` 收集全部命中（当前实现已如此）。
4. 审计事件补字段：`session_id`、`turn_id`、`allowed`、`requires_confirmation`、`degraded_to`、`schema`、`type="tool_gate"`（其余沿用现有 `audit{...}`）。
5. `protected_paths` 用本文 §3 T-UCA-04 的并集；`SENSITIVE_*` 增 PII 与凭证文件后缀；`INJECTION_TOKENS` 纳入 T-UCA-09；`DEFAULT_POLICY["version"]` 同步。
6. `phase=unknown` 且 `mutating` → **block**（可在 T-UCA-10 分支内实现，或走默认策略；必须留审计）。
7. 测试 id 按 §3 每条与 §8 表；`tests/test_tool_gateway.py` 与 `tool-gateway-sim.mjs` 必须与 JSON `rules` 同源。

### 10.4 执行状态（2026-10-27 06:28，已对齐）

**结论：实现与 §7 规则表逐字段一致；T-UCA-15/16 两侧均已实现；无 predicate 语义分歧。**

| 项 | 状态 / 证据 |
|---|---|
| 三方 parity | **严格相等**：docs §7 JSON == `engine/tool_gateway.py:POLICY` == `plugin/dsh-lingshu/tool-gateway.js:POLICY`；16 条逐字段 0 diff，`policy_version="t-uca-1.1"`（本次独立复核用 `json.loads` 对比，diff=[]） |
| 单一来源机制 | `tools/_sync_tool_policy.py`（零依赖）把 §7 注入两处实现——规则表不是手抄，不存在两处漂移；Python 侧 `POLICY_JSON` 内嵌即 §7 原文（engine/tool_gateway.py:35），JS 侧同源注入 |
| T-UCA-15 | Python `_path_escape()`（engine/tool_gateway.py:244）+ JS `path_escape`（tool-gateway.js:417）均实现：`..` 段/绝对路径/盘符/UNC/`\0` → block |
| T-UCA-16 | 两侧均由数据驱动谓词 `when:{phase=shift ∧ destructive}` 实现（通用 `_pred()`，engine:416；JS 同构），无手写特判 → 无漂移风险 |
| degrade 语义 | 复核：`phase=critical + write` 无审批 → `block` + `degraded_to=block`；`approval_channel=true` → `verdict=degrade`、`allowed=false`、`executed=false`；`phase=unknown + mutating → block`（ucas 记 T-UCA-10） |
| 15/16 行为 | 复核：`write path="../../etc/passwd"` → block（`T-UCA-15`，另有 02/04 多命中）；`phase=shift + delete` + 审批 → degrade（`T-UCA-16`）；`saturated + write` + 审批 → degrade（`T-UCA-13`） |
| 审计事件 | 顶层 17 字段 + 内层 `audit{9}` + `snapshot{3}`；`ucas` 只含 `T-UCA-xx`；allow 时也写事件且 `allowed=true`；degrade 恒 `allowed=false`；`args_preview` 已做**值级脱敏** |
| 测试 | `tests/test_tool_gateway.py` **79 checks / exit 0**（本次复跑）；`tool-gateway-sim.mjs --assert` **exit 0**（a5 报 65 断言）；a5 报危险 20/20 拦截、良性 8/8 放行、Python↔JS 20/20 一致 |

**遗留（不阻塞）**：T-UCA-10/13/16 的三条 degrade 在没有审批通道的宿主里都会落成 block（本契约 §4 的有意设计），因此"确认路径"尚未在真实宿主跑过；误杀率仍为 `guess`，需在论文 limitations 与 README 原样写。

**冻结声明**：本文件 v1.1 自 2026-10-27 起为**附录与实现的唯一表格源**；本轮落盘后未修改任何 `engine/`、`plugin/`、`rules/` 文件。

---

*本契约由 ADR-013 授权，仅新增工具侧命名空间；注入侧 `UCA-01..08`、`engine/`、`plugin/` 语义零改动。*
