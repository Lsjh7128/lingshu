# 剧本 C：上下文过载（Context Flood）——论文草稿

> 归属：H0 指令（Robustness under Chaos Injection，剧本 C）
> 证据：`experiments/chaos-injection/out/C-context-flood/case_<ratio>.json`（4 个）+ `summary.json`
> 脚本：`experiments/chaos-injection/run_c_context_flood.py`（可 `--cases` 选跑；`--warmup 0` 只跑 4 次测量）
> 硬约束：本剧本**零真实模型调用**（`DEEPSEEK_BASE_URL` 指向本机 mock）；插件/引擎/规则/宿主代码零改动。

## 0. 一句话结论

在真实 DSH headless 会话里注入 15 万字符确定性乱码（≈50k token @3 chars/token），
4 个用例的实测 ratio 为 **0.6806 / 0.7006 / 0.7206 / 0.8507**，phase 精确落在
**normal / shift / shift / critical**；70%/85% 双阈值判定、宪法段保全、无物理截断均成立。
但同时暴露两条诚实边界：**(a) 插件估算比名义 chars/3 多 43 token（ratio 系统性 +0.0006）；
(b) 宿主在 pre-step 之前组装 system prompt，导致本轮相变锚点赶不上本轮首个请求（一轮投递滞后）**。

## 1. 注入方法（外部注入、零主干改动）

| 注入点 | 值 | 作用 | 是否改代码 |
|---|---|---|---|
| `DEEPSEEK_BASE_URL` | `http://127.0.0.1:<随机端口>` | 本机 mock（Anthropic Messages 兼容，SSE）；逐请求记录 body 字节数/是否含完整输入 | 否 |
| `LINGSHU_CONTEXT_WINDOW` | 73530 / 71429 / 69444 / 58824 | 覆盖插件相变窗口（index.js L787），让 70%/85% 在 50k token 量级可达 | 否 |
| stdin prompt | 150,000 字符确定性乱码（LCG，seed=20261027） | 上下文洪水输入本体；首尾埋 `c_flood_head_9f2a7b_` / `_c_flood_tail_9f2a7b` 标记 | 否 |
| 数据目录 | 每个用例独立 `DSH_HOME` + `LINGSHU_DATA_DIR` | 状态/审计/预览互不污染 | 否 |

- 乱码生成：LCG（`x = (1103515245·x + 12345) mod 2^31`），**不调用 random 模块**，逐字节可复现；
  字母表排除 `"`、`\` 与控制字符，保证 prompt 在 JSON body 里逐字出现，可用"完整子串是否在 body 里"判定物理截断。
- 参数 provenance：锚点文案与阈值**运行时从源码读出**（`control.js` 的 `ANCHOR_PREFIX/ANCHOR_SUFFIX`、
  `DEFAULT_PARAMS.phaseShiftRatio/HardRatio/trendThreshold`，并与 `cordis.patch.yml` 交叉核对），
  见证据 JSON 的 `provenance` 字段；未使用任何"记忆里的文案"。
- 调用预算：4 次测量 + 4 次预热/边界探测 = **8 次 headless**（全部 mock，零成本）；首次即成功，无重试。
- 每用例独立 mock 端点；证据记录 `state.control.phase`、`last-prompt.txt` 全文、
  mock 请求（body 字节数、messages JSON 长度、锚点/宪法在 body 与 system 中的偏移）、`audit.jsonl` 增量计数。

## 2. 4 个用例观测表（测量调用，n=1/点）

| case | window | 名义 50000/w | 实测 ratio（4dp） | phase | prefeed | anchorRequired | trend | last-prompt 头/尾 | 宪法 | 请求 system 头/尾 | 请求含宪法 |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0.6799 | 73530 | 0.6800 | **0.6806** | normal | false | false | 0 | 否/否 | 是 | 否/否 | 是 |
| 0.7000 | 71429 | 0.7000 | **0.7006** | shift | true | false | 0 | **是**/否 | 是 | 否/否（上轮 normal） | 是 |
| 0.7200 | 69444 | 0.7200 | **0.7206** | shift | true | false | 0 | **是**/否 | 是 | 是/否（上轮 shift） | 是 |
| 0.8500 | 58824 | 0.8500 | **0.8507** | critical | true | **true** | 0 | **是/是** | 是 | **是/是**（上轮 critical） | 是 |

- `trend=0` 是机制事实：`PhaseShiftMonitor.history` 只在进程内（index.js L364），headless 每次调用是新进程；
  且每个 turn 只有首个 step 做相变观测（index.js L737）。因此 shift 阶段
  `anchorRequired = (trend > 0.02) = false`，只有 critical 无条件 `anchorRequired=true`。
- 落盘预览 `last-prompt.txt` 字节数：normal 152；shift 330；critical 677（头锚点+宪法+尾锚点）。
  锚点存在性与 `control.js` 的渲染门控完全一致：`renderAnchorHead` 要求
  `prefeed || anchorRequired`，`renderAnchorTail` 只要求 `anchorRequired`。
- 运行面：8/8 调用 `exit_code=0`、`final="MOCK-C-OK"`、wall ≈2.7–3.0 s、
  `hook_error=0`、`audit verdict=allow`、`control.blocked=false`、每次测量 `pre_step(firstStep=true)` 恰 1 次。
- 物理截断：mock 收到的 body 为 **509,649 / 509,649 / 510,011 / 510,251 字节**；
  输入 150,000 字符在 body 中**完整出现（3 份）**，模型可见 messages 文本 **151,809 字符**
  （150,000 输入 + 1,809 宿主追加指令块），`prompt_full_in_body=true`，**截断率 0.0（4/4）**。

## 3. 阈值精确判定（用实测 ratio 4 位小数 + 边界窗口说话）

先给估算口径的实测校正：`estimateTokens = ceil(len(JSON.stringify(payload.messages))/3)`。
150,000 字符消息的 JSON 外壳（role/content 等）为 **127 字符**，故插件实际估算
**ceil(150127/3) = 50,043 token**，而不是名义 `150000/3 = 50,000`。
→ 实测 ratio = 50043/window，比名义 `50000/window` 系统性高 **约 +0.0006**（43 token / window）。
这是**估算口径**偏差（envelope 计入），不是阈值执行偏差。

### 3.1 0.70 点（phaseShiftRatio）

| 观测 | window | 实测 ratio | 4dp 显示 | phase | 结论 |
|---|---|---|---|---|---|
| 边界探测（预热） | 71493 | 50043/71493 = 0.699972 | **0.7000** | normal | 显示 0.7000 但**未达**阈值（舍入陷阱） |
| 测量 | 71429 | 50043/71429 = 0.700598 | **0.7006** | shift | 名义 0.7000 的窗口实际落在 0.7006 |
| 由 used=50043 反推 | 71490 | 50043/71490 = **0.700000** | 0.7000 | **shift** | `>=` 语义：恰在 0.7000 即翻转 |
| 同上 | 71491 | 50043/71491 = 0.699990 | 0.7000 | normal | 翻转窗口 = 71490/71491 |

**判定**：阈值确实在 0.7000 处翻转（`r >= 0.7` 为 shift）。任务给的 window=71429
（按名义 50000 token 设计）在真实估算口径下是 **0.7006**，不是 0.7000；
要让 r 恰为 0.7000，window 应取 **71490**。4 位小数显示值不能替代判定：
71493 与 71490 的显示值都是 0.7000，前者 normal、后者 shift。

### 3.2 0.85 点（phaseShiftHardRatio）

| 观测 | window | 实测 ratio | 4dp 显示 | phase | 结论 |
|---|---|---|---|---|---|
| 边界探测（预热） | 58876 | 50043/58876 = 0.849973 | **0.8500** | shift | 显示 0.8500 但**未达** critical |
| 测量 | 58824 | 50043/58824 = 0.850724 | **0.8507** | critical | 名义 0.8500 的窗口实际落在 0.8507 |
| 由 used=50043 反推 | 58874 | 50043/58874 = 0.8500017 | 0.8500 | **critical** | 最大 critical 窗口 |
| 同上 | 58875 | 50043/58875 = 0.8499873 | 0.8500 | shift | 翻转窗口 = 58874/58875 |

**判定**：critical 在 0.85 处按 `r >= 0.85` 翻转；0.8500 的显示值同样出现
"显示 critical 实为 shift"（58876）。任务 window=58824 实测 0.8507 ≥ 0.85 → critical 成立。

## 4. 锚点投递链路：一轮滞后（本剧本最重要的新发现）

`last-prompt.txt` 是**插件本轮意图**，请求体里的 `system` 才是**模型实际看到的东西**。
实测 8/8 调用：**请求 system 里的锚点集合 = 上一次落盘 phase 的锚点集合**，而非本轮 phase。
机制在宿主源码里可直接核对：`dsh-agent-loop/lib/index.js` L907 先
`systemPrompt.assemble(...)`，L911 才 `waterfall("agent/pre-step", ...)` —— 插件在 pre-step
里更新相变状态，**赶不上本轮首个请求的 system prompt**，要到下一次组装（同进程下一 step，
或下一次会话读取已落盘 state）才生效。

| # | case | 角色 | window | 本轮 phase | 上轮 phase | last-prompt 头/尾 | 请求 system 头/尾 | 滞后 |
|---|---|---|---|---|---|---|---|---|
| 1 | 0.6799 | measured | 73530 | normal | —（全新） | 否/否 | 否/否 | — |
| 2 | 0.7000 | below-probe | 71493 | normal | —（全新） | 否/否 | 否/否 | — |
| 3 | 0.7000 | measured | 71429 | **shift** | normal | 是/否 | 否/否 | **是** |
| 4 | 0.7200 | repeat | 69444 | **shift** | —（全新） | 是/否 | 否/否 | **是** |
| 5 | 0.7200 | measured | 69444 | shift | shift | 是/否 | 是/否 | 否（两侧一致） |
| 6 | 0.8500 | below-probe | 58876 | **shift** | —（全新） | 是/否 | 否/否 | **是** |
| 7 | 0.8500 | critical-feed | 58824 | **critical** | shift | 是/是 | **是/否**（缺尾锚点） | **是** |
| 8 | 0.8500 | measured | 58824 | critical | critical | 是/是 | 是/是 | 否（两侧一致） |

- 8/8 请求锚点集合 = 上轮 phase 的锚点集合；其中 4/8 本轮 phase 与上轮不同，直接看到"本轮应有却没有"。
- 第 7 行是最强证据：本轮 critical、`last-prompt` 头尾齐全，但请求 system 只有头锚点、**没有尾锚点**——
  因为组装时落盘 phase 还是上一轮的 shift。
- 第 8 行说明：**critical 的尾锚点确实能进入模型请求，但要等"上轮已是 critical"**。
- 实际含义：单轮 headless/single-shot 任务里，本轮触发的 critical 锚点对**本轮模型调用无效**；
  多步/多轮会话中最多晚一个 step/turn 到位。这是插件控制面与宿主生命周期的接口时延，
  插件无法自行修复（插件不能改写宿主 messages），需要宿主把 pre-step 提前到 system 组装之前，
  或提供"消息级锚点追加"能力。

### 4.1 "首/尾"的空间含义（别把它读成请求首尾）

critical 用例（请求含头+尾锚点）实测：system prompt **2,906 字符**，其中
头锚点偏移 **111**、宪法偏移 **172**、尾锚点偏移 **229**（尾锚点自身 39 字符，止于 268）；
尾锚点之后 system 里还有 **2,638 字符（占 system 90.8%）**，
再之后是 messages 里 **151,809 字符**的用户消息。
即"尾锚点"结束于模型可见文本约 **268 / 154,715 ≈ 0.17%** 处，**99.83% 的文本在它之后**。
所以灵枢的"首尾人格锚点"是**注入段内部的段序 90/140**（7 段之末），不是整个上下文的首尾；
在 Messages API 结构下，尾部锚点无法压住 150k 用户消息造成的尾部稀释。
证据字段：`main_request_offsets_in_system`、`main_request_system_chars_after_anchor_tail`。

## 5. 诚实边界

1. **插件只估算与注入，不做物理截断。** 截断/丢弃由宿主与模型端决定；本实验 mock 端点接受一切，
   所以"截断率 0.0"只说明**宿主组装链路（messages/system 字段）没有截断**，不能外推到真实模型端的接受度。
2. **body 字节数 ≠ 模型输入长度。** body 里输入出现了 3 份（messages 1 份 + `dsh_session_log`/会话事件元数据 2 份），
   还含约 57 KB 工具 schema 与元数据；模型可见长度应以 `messages_json_len`（151,936 字符）为准。
3. **估算口径有常量偏差。** 插件只看 `payload.messages`，不含 system prompt（2,804–2,906 字符 ≈ 935–969 token）、
   工具 schema、以及宿主向用户消息尾部追加的 1,809 字符指令块（≈603 token）。
   插件估算 50,043 vs 宿主 messages 折算 50,646 token（**低估 1.2%**）；ratio 是"相对注入窗口的控制信号"，
   **不是物理占用率**。
4. **没有压到真实模型窗口。** `deepseek-flash` 声明 1,000,000 token 窗口，本实验总模型可见文本
   ≈154,715 字符 ≈51,572 token，物理占用**约 5.2%**。`LINGSHU_CONTEXT_WINDOW` 是实验室覆盖
   （模拟"部署方把窗口配成 56k–74k"的情形）；state 里宿主上报的真实 `contextWindow=1,000,000` 是另一回事，
   两者不可混用。
5. **锚点"一轮滞后"的机制由宿主源码确认，但运行时观测只有 8 次**（n=1/点 + 4 次边界/前态构造）。
   结论方向可靠，量化程度有限；且我们**没有**同进程多 step 的直接观测（mock 不返回 tool call），
   "下一次组装"只由源码与"第二次调用"间接验证。
6. **乱码不是真实对话。** 输入无语义、无真实用户意图、无工具调用；本实验测的是控制面与链路，
   不是模型在真实长对话中的语义鲁棒性。每点 n=1，无统计显著性，不给出置信区间。
7. **本批次每次调用只观测到 1 个 `/v1/messages` 请求**（无标题生成请求）；短输入探针曾观测到
   标题生成的第 2 个请求，其触发条件未在本文展开。
8. **last-prompt.txt 是预览，不是注入证明。** 论文表格若引用"锚点在 prompt 首尾"，必须同时给出
   last-prompt（插件意图）与请求 system（实际投递）两列，否则会把"计划注入"误写成"已注入"。

## 6. 英文 mini-abstract（可直接进论文，6–8 句）

We inject a 150,000-character deterministic context flood (about 50k tokens under the plugin's
3-chars-per-token heuristic) into a real DSH headless session whose model endpoint is a local mock,
making the experiment reproducible at zero model cost. Across four windows chosen to bracket the
70%/85% phase-shift thresholds, the plugin's monitor reports ratios of 0.6806, 0.7006, 0.7206 and
0.8507, classifying them as normal, shift, shift and critical, with no blocked turn and no hook
error in any run. Because the estimate is taken over the messages JSON envelope, the measured ratio
sits about 0.0006 above the nominal chars/3 value (50,043 estimated tokens rather than 50,000).
Boundary probes at windows 71,493 and 58,876 display exactly 0.7000 and 0.8500 while being
classified as normal and shift respectively, so four-decimal reporting cannot replace the unrounded
comparison; from the measured token count the exact flip windows are 71,490/71,491 and 58,874/58,875.
No physical truncation occurs at the mock layer: every request contains the full 150,000-character
input and the injected constitution block is present in 8/8 requests. The persona anchors behave
differently from the monitor, because the host assembles the system prompt before the plugin's
pre-step hook (dsh-agent-loop L907 vs L911): the anchor set reaching the model equals the previous
run's persisted phase in 8/8 cases, so a fresh single-turn session never delivers the current
turn's critical anchors, and the "tail" anchor lies at roughly 0.17% of the model-visible text.
These results support the 70%/85% threshold logic and constitution preservation, but expose a
one-assembly anchor-delivery lag and show that context pressure is a relative control signal
rather than a physical occupancy measure.

## 7. 关键数字速查（给论文/报告引用）

| 指标 | 数值 |
|---|---|
| 输入 | 150,000 字符，确定性 LCG（seed 20261027），sha256_16=`74241c69469223fc` |
| 实测 ratio（4 例） | 0.6806 / 0.7006 / 0.7206 / 0.8507 |
| 名义 ratio（50000/w，4dp） | 0.6800 / 0.7000 / 0.7200 / 0.8500 |
| 插件估算 token | 50,043（messages JSON 150,127 字符，envelope 127 字符 = 43 token） |
| 宿主 messages 折算 | 151,936 JSON 字符 / 50,646 token（输入 150,000 + 宿主追加 1,809） |
| 系统提示长度 | 2,804 / 2,804 / 2,865 / 2,906 字符 |
| ratio 精确翻转窗口 | 0.70：71490（shift）/ 71491（normal）；0.85：58874（critical）/ 58875（shift） |
| mock body 字节 | 509,649 / 509,649 / 510,011 / 510,251；输入在 body 中出现 3 份 |
| 物理截断率 | 0.0（4/4，`prompt_full_in_body=true`） |
| 宪法保全 | 8/8 请求 system 含 `〖宪法 · 只读〗`；last-prompt 8/8 含 |
| 锚点投递 | 8/8 请求锚点集合 = 上轮 phase；4/8 本轮 phase 不同 → 滞后可见 |
| 尾锚点位置 | system 2,906 字符中偏移 229，其后仍有 2,638 字符（90.8%）+ 151,809 字符 messages |
| 运行面 | exit 0（8/8）、wall ≈2.7–3.0 s、hook_error 0（8/8）、blocked=false（8/8） |

## 8. 复现与边界声明

```powershell
# 全部 4 用例（4 测量 + 4 预热/边界探测 = 8 次 headless，全部 mock）
python experiments/chaos-injection/run_c_context_flood.py
# 只跑 4 次测量（跳过预热/边界探测）
python experiments/chaos-injection/run_c_context_flood.py --warmup 0
# 单用例
python experiments/chaos-injection/run_c_context_flood.py --cases 0.7000,0.8500
```

- 输出：`out/C-context-flood/case_<ratio>.json`（4）+ `summary.json`（含 `calibration`、
  `threshold_bracket`、`anchor_pipeline`、`honest_notes`）。
- 重跑会先安全拆除旧 junction（`cmd /c rmdir` 只摘 reparse point，不触碰仓库插件源码）。
- 本批证据对应一次完整运行（8/8 调用成功，无重试）；临时日志在 `_runtime/C-runlog.txt`。
- 与 `paper/sections/06-experiments.tex` 对接：现有小节可保留 0.7000/0.7006、0.8500/0.8507、
  宪法/锚点/截断率 0.0/窗口为实验室覆盖等表述；**建议补一句锚点投递滞后**
  （请求 system 锚点 = 上轮 phase，8/8；critical 尾锚点需上轮已 critical 才进请求），
  否则"critical 必须补锚点"会被读成"本轮就一定补上了"。
