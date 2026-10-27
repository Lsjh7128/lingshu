# 工具网关在宿主插件侧的集成与能力边界（ADR-013 / v5.2 增量）

> 状态：**插件侧接线已落地，等待 A5 的 `tool-gateway.js` 落盘后自动生效**（当前以 `verdict:"unavailable"` 诚实降级）。
> 关联：`decisions/013-解冻行动闭环-工具网关.md`（ADR-013）、`docs/24-工具网关UCA.md`、`engine/tool_gateway.py`。
> 本文只描述**已实测**的行为，所有数字可指回 `plugin/dsh-lingshu/host-sim.mjs` 的断言与 `audit.jsonl` 字段。

---

## 0. 一句话边界

**当前 DSH 只暴露 `tool/call` 与 `tool/result` 两个会话事件，没有"工具执行前"的 dispatch 钩子。**
因此插件侧**无法阻断已经派发的工具调用**；它能做的是
**事后判定（STPA 工具网关）→ 落 `tool_gate` 审计 → 在下一轮注入一次性强提示**。
真正的前置阻断只存在于两处：① 经 `guardTool()` 包装的工具内部；② 引擎参考循环（`engine/tool_gateway.py`）。

> 这条边界不是实现取舍，是**宿主能力事实**。论文与 README 必须原样引用，不得写成"插件拦截了工具调用"。

---

## 1. 证据：宿主到底给了什么钩子

| 证据 | 内容 |
|---|---|
| `index.js` 注释（原 `turn/end` / `tool/result` 段） | "实测：`turn/end` / `tool/result` 是**会话事件**，不是 `ctx.on` 的 dispatch 钩子（注册不报错，但永不触发）。真正能观测到它们的是 `session/event`。" |
| `research/02-DSH宿主能力核实.md §8.5` | 同一结论的原始实测记录 |
| `host-sim.mjs` 新增断言 | `宿主无 pre-tool dispatch 钩子（只有 session/event 可观测）` = `!hooks['tool/call'] && !hooks['tool/result'] && Boolean(hooks['session/event'])` |

推论（三条，必须一起写）：

1. 宿主**不会**在工具执行前询问插件 → 插件拿不到"否决不执行"的机会；
2. `tool/call` 事件到达时，调用**已经派发**（甚至可能已执行）；
3. `tool/result` 到达时，副作用**已经发生** → 网关只能事后审计 + 让**下一轮**更保守。

---

## 2. 插件侧做到了什么（可验证）

### 2.1 接线点（`plugin/dsh-lingshu/index.js`）

| 位置 | 行为 | 是否改动既有逻辑 |
|---|---|---|
| `session/event` 的 `tool/call` 分支（新增独立 `if`） | 记录 `st.tools.pending = {tool, args, argsDigest, at, turn}`；`stats.calls++`；落 `tool_call_seen` 审计（含 `blocking_capable:false`、`host_hook:"session/event(tool/call)"`） | **否**（新分支） |
| `session/event` 的 `tool/result` 分支**之后**追加的独立 `if` | 调 `gateToolResult()`：`evaluate(call, state)` → 落 `tool_gate` 审计 → 置 `st.tools.gateBlockedUntilClarify` | **否**（既有"工具结果写记忆 + 情感 nudge + `memory_write`"分支**一字未动**，网关在它之后独立执行） |
| `agent/pre-step` 首 step | 若 `gateBlockedUntilClarify` 为真 → `hintActive=true`、清标志、落 `tool_gate_hint_injected` 审计 | **否**（只在既有首 step 块开头插入） |
| 宪法段 `registerSection('constitution')` | 当 `hintActive` 为真时，在宪法段尾部追加一段强提示 | **否**（段落数仍为 7，order 90/100/105/110/120/130/140 不变；既有断言"注册 7 个段落"仍绿） |
| `ctx.tools.register` 的 `lingshu_status` | 用 `guardTool(spec, stateProvider)` **懒包装**：网关在则走包装，不在则退化为原 spec | **否**（工具名/参数/schema/输出不变） |

### 2.2 审计字段（`tool_gate`，ADR-013 不变量 6）

```json
{"ts": 1793…, "kind": "tool_gate",
 "tool": "write_file", "argsDigest": "9f2c…", "argsHead": "{\"path\":\"engine/control.py\"…}",
 "verdict": "block", "ucas": ["T-UCA-05"], "reason": "T-UCA-05:protected_path",
 "policy_version": "tool-gateway-1.0", "isError": false, "turn": 7,
 "blocking_capable": false, "host_hook": "session/event(tool/result)",
 "gateway_module": true, "gateway_error": null}
```

配套审计：`tool_call_seen`（pending）、`tool_gate_hint_injected`（强提示已注入）、`tool_gate_error`（网关自身异常，fail-closed）。

### 2.3 强提示的样子（并入宪法段，不新增段落）

> 〖工具闸门 · 强提示〗上一轮工具调用「write_file」被 STPA 工具网关判定为 block（T-UCA-05）。
> 重要边界：当前 DSH 只有 tool/call 与 tool/result 会话事件，没有工具执行前的 dispatch 钩子，
> 因此本插件无法阻断已经派发的调用，该判定是事后检测。
> 在人工澄清之前：不要重试同类调用；先说明意图、影响范围与回滚方式；必要时请用户确认。

出现**一次**即清标志（下一轮不再重复）。

---

## 3. 插件侧做不到什么（必须写进论文 Limitations / README）

| 不能做 | 原因 |
|---|---|
| **阻断**任意宿主工具调用 | 无 pre-tool dispatch 钩子；调用已派发 |
| 撤销/回滚已产生的副作用 | 插件只读事件流，不接管执行 |
| 对**不经过 `guardTool()`** 的工具强制执行闸门 | 包装器只覆盖接入它的调用；其它插件直接执行时网关旁路（ADR-013 §4.4） |
| 保证 `tool/call` 一定先于副作用 | 事件顺序由宿主决定，插件无法保证 |
| 把"事后 block"当成"已拦截" | 审计里 `blocking_capable:false` 是硬字段，禁止改写成 `true` |

---

## 4. 三种"前置"到底差在哪

| 层 | 何时判定 | 能否阻止执行 | 覆盖范围 | 证据/落点 |
|---|---|---|---|---|
| **插件（本文）** | `tool/result` 到达之后 | ❌ 不能 | 全部宿主工具（但只看得到，拦不住） | `index.js` 的 `tool_gate` 审计 + 下一轮强提示 |
| **`guardTool()` 包装器** | 工具 `execute` 内部、真正执行前 | ✅ 能（返回拒绝消息，不执行原逻辑） | 只覆盖经它包装的工具（当前仅 `lingshu_status`，只读，应 allow） | `tool-gateway.js#guardTool`（A5） |
| **引擎参考循环** | "模型输出解析之后、工具执行之前" | ✅ 能 | 参考实现内的全部工具调用 | `engine/tool_gateway.py` + `tools/run_actuator_demo.py` |

> 论文里三者必须分开写，不能合并成"我们实现了工具前置拦截"。

---

## 5. 当前实测状态（诚实记录）

**最终测试时点（2026-10-27 06:26）**：A5 的 `plugin/dsh-lingshu/tool-gateway.js`
（测试时点版本 `sha256` 前缀 `d9dadb3bc62b310d`；A5 之后又更新到 `82c4b9f610540606`，
重跑同一套件仍 **210 passed / 0 failed**；`policy_version="t-uca-1.1"`）已落盘且**可 import**；
插件的**懒加载 + 优雅降级**因此自动激活，此前 7 条 `skip` 全部转为真实断言。
（自测过程中网关曾一度 import 失败：`Unexpected token '_', "__POLICY_J"...`，插件按设计降级为
`verdict="unavailable"`、不伪造拦截；A5 修好后无需改代码即恢复。）

`node plugin/dsh-lingshu/host-sim.mjs`（同上时点）：

```
[27] v5.2 工具网关接线：事后检测 + tool_gate 审计 + 下一轮强提示
  ok   宿主无 pre-tool dispatch 钩子（只有 session/event 可观测）
  ok   tool/call 记录 pending（tool + argsDigest + args）
  ok   tool/call 落 tool_call_seen 且声明 blocking_capable=false
  ok   tool/result 落 tool_gate 审计（tool/argsDigest/verdict/ucas/policy_version）
  ok   tool_gate 声明宿主无 pre-tool 钩子（blocking_capable=false + host_hook）
  ok   verdict 值域合法（allow|block|degrade|unavailable）
  ok   既有行为不变：tool/result 仍写 1 条工具记忆
  ok   既有行为不变：memory_write 审计仍为 1 条
  ok   有网关：只读工具 lingshu_status 判定为 allow
  ok   有网关：policy_version 来自网关（非 unavailable）
  ok   有网关：判定过程无 tool_gate_error
  ok   危险调用（受保护路径写入）被判 block
  ok   危险调用命中 T-UCA 规则（ucas 非空）
  ok   block 后设置 gateBlockedUntilClarify=true
  ok   block 计入 stats.blocked
  ok   下一轮宪法段注入工具闸门强提示
  ok   强提示审计含 tool_gate_hint_injected 且声明 blocking_capable=false
  ok   注入后 gateBlockedUntilClarify 已清（一次性）
  ok   再下一轮强提示消失（一次性，不常驻）
  ok   新增网关后段落数仍为 7（不破坏既有契约）
  ok   lingshu_status 经包装后仍注册且可执行
  ok   lingshu_status 执行返回状态文本（只读放行，不因网关崩掉）

210 passed, 0 failed
```

| 项 | 值 |
|---|---:|
| 既有断言 | **188 / 188 通过**（一条未改） |
| 新增断言 | **22 条**（`host-sim.mjs` 组 `[27]`） |
| 合计 | **210 passed, 0 failed, 0 skipped** |
| 其它 JS 套件 | `control-sim` 148/148、`mutation-regressions` 15/15、`stress` exit 0、`eval-agent-state` exit 0 |
| 文件指纹 | 测试时点：`index.js` `240cb31a751a6f2a`、`host-sim.mjs` `a8254d8368ac5b92`、`tool-gateway.js` `d9dadb3bc62b310d`；A5 后续更新 `tool-gateway.js` 到 `82c4b9f610540606` 后重跑仍 210/0 |

### 5.1 JS 与 Python 两版网关的差异（论文引用时必须区分）

| 项 | JS（`tool-gateway.js`，`t-uca-1.1`） | Python（`engine/tool_gateway.py`） | 对插件的影响 |
|---|---|---|---|
| 只读工具登记 | `REGISTRY.lingshu_status`（`class:'read'`）✅ | 无此条目 → 会判 `unknown_tool` 并 block | **JS 版已登记**，只读路径可 allow；Python 侧若要一致需补登记 |
| `guardTool()` 返回值 | envelope（见 5.3） | — | 插件解包成文本 |
| T-UCA 编号 | 16 条：`T-UCA-04 protected_path`、`T-UCA-08 tool_unregistered`、`T-UCA-09 schema_invalid`、`T-UCA-11 audit_context_missing` | 同语义编号不同（如 protected_path 是 `T-UCA-05`、unknown_tool 是 `T-UCA-01`） | 审计原样记录；**跨版本比较 ucas 前必须先映射编号** |
| `blocking_capable` | session/event 路径 `false`；guardTool 路径 `true` | —（引擎在参考循环里，天然前置） | 两者绝不可混写 |

---

### 5.2 真实产物样例（来自隔离 DSH_HOME 的一次实跑，非手写）

```json
{"kind":"tool_call_seen","tool":"write_file","turn":1,"argsDigest":"2392e367e32d8694",
 "blocking_capable":false,"host_hook":"session/event(tool/call)"}
{"kind":"tool_gate","tool":"write_file","argsDigest":"2392e367e32d8694",
 "argsHead":"{\"path\":\"engine/control.py\",\"content\":\"x\"}",
 "verdict":"block","ucas":["T-UCA-11"],"reason":"T-UCA-11:internal_error:RangeError",
 "policy_version":"t-uca-1.1","turn":1,"blocking_capable":false,
 "host_hook":"session/event(tool/result)","gateway_module":true,"gateway_error":null}
{"kind":"tool_gate_hint_injected","tool":"write_file","verdict":"block","ucas":["T-UCA-11"],
 "turn":2,"blocking_capable":false}
```

> **必须记录的异常**：该次调用的 `reason` 是 `T-UCA-11:internal_error:RangeError` ——
> 网关内部抛错后**按 fail-closed 回退成 block**（安全方向正确），但**不是**按 `T-UCA-04 protected_path`
> 正常命中的理想路径。`ucas` 里仍是 `T-UCA-*`，所以测试断言通过；**不能用它证明保护路径规则有效**。
> 已在 §5.4 标为待 A5 复核项。

### 5.3 接线时必须知道的 JS 契约细节（全部实测）

| 项 | JS 网关行为 | 插件侧对策 |
|---|---|---|
| `evaluate(call, state, policy=POLICY)` | 三参；第三参默认策略 | 插件只传两参，用默认策略（避免 registry 被覆盖成空） |
| 审计上下文（T-UCA-11） | 需 `state.audit_context={present:true, session_id, turn_id}` 且 `turn_id` **truthy**、`call.id` 非空 | 插件补 `present/session_id`，`turn_id=st.turns+1`（**0 是 falsy → 会误判 block**），callId 兜底 `tool-<turn>-<digest>` |
| 能力声明（T-UCA-12） | `state.capabilities={declared, granted}`（对象） | 插件同时给对象版与 camel 字段 |
| schema 校验（T-UCA-09） | `reject_unknown_keys_unless_declared:true` → registry 里 `schema:{}` 时**任何参数键都非法** | 插件在 `state.tool_registry` 声明自有工具 schema（`lingshu_status.verbose:boolean`），与 `gw.REGISTRY` 合并 |
| `guardTool(spec, stateProvider)` | 返回 **envelope** `{ok,blocked,held,verdict,ucas,content:[{type:'text'}]}`，不是原值 | 插件解包取 `content[].text`；该路径 `blocking_capable:true`（execute 内部、原逻辑执行前判定） |
| 冻结区（T-UCA-14） | `state.frozen_ok`（snake），仅对 mutating 类 | 插件两套字段都给；只读工具不受影响 |
| T-UCA 编号 | 16 条（`T-UCA-04 protected_path`、`T-UCA-09 schema_invalid`、`T-UCA-11 audit_context_missing`…） | 审计原样记录，不做编号映射；论文引用须注明版本 |

### 5.4 待 A5 复核 / 未覆盖项

1. **`internal_error:RangeError`**（§5.2）：`write_file` + 受保护路径走了网关内部回退，而非规则命中，需 A5 定位。
2. 只有**自有工具**（`lingshu_status`）经 `guardTool` 包装；其它宿主工具仍只有事后检测，无法前置阻断。
3. `tool/call` 与宿主实际派发/执行的先后由宿主决定，插件无法保证"先看到 call 再看到 result"。
4. 本次无 Python 侧改动，`tests/` 未跑（与本改动文件无交集；ADR 基线已知 1 条既有失败与本改动无关）。

## 6. 与既有红线的对齐

| 红线 | 本改动的处理 |
|---|---|
| 不改记忆逻辑与情感 PAD 稳态 | 既有 `tool/result` 分支**未改一字**；网关逻辑在其后独立执行；新增断言验证"仍写 1 条工具记忆 + 1 条 `memory_write`" |
| 不改 `memorywriter.js` / `cordis.patch.yml` | 未触碰 |
| 不新增写能力 | 只新增"读 + 审计 + 提示"；`lingshu_status` 仍只读 |
| 不改既有断言 | 既有 188 条未动，且新增断言不依赖修改它们 |
| 注入侧 8 类 UCA 不变 | 工具侧用独立命名空间 `T-UCA-*`，不碰 `UCA-01..08` |

---

## 7. 复现

```powershell
$node = "<NODE>"
Set-Location <REPO_ROOT>\plugin\dsh-lingshu

# 全量（含新增 [27] 组）；退出码 0 = 无失败
& $node host-sim.mjs
& $node host-sim.mjs --assert          # 只输出失败项

# 检查插件语法（ESM）
& $node --check index.js
& $node --check host-sim.mjs
```

审计产物路径：`$DSH_HOME/plugins/dsh-lingshu/audit.jsonl`（`tool_call_seen` / `tool_gate` /
`tool_gate_hint_injected` / `tool_gate_error`）；状态字段：`state.json` 的 `tools.{pending,last,gateBlockedUntilClarify,hintActive,stats}`。
