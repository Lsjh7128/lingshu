# 工具网关（Actuator Gating）· 中文对照稿

> **用途**：README 新章节、论文 §5 `06e-actuator.tex`、附录 A `10c-appendix-tool.tex` 的中文对照 + 口径纪律。
> **契约（唯一来源）**：`docs/24-工具网关UCA.md`（**v1.1 冻结**；T-UCA-01…16；policy `t-uca-1.1`）。
> **实现**：`engine/tool_gateway.py`（Python 参考实现）、`plugin/dsh-lingshu/tool-gateway.js`（同语义移植）；两处规则表由 `tools/_sync_tool_policy.py` 从 `docs/24` §7 机器注入，不手抄。
> **实测数字只来自**：`tests/test_tool_gateway.py`、`tool-gateway-sim.mjs`、`tools/run_actuator_demo.py` 的真实输出。`[TBD]` 在 a5 落地前**不填、不猜**。

---

## 一、这件事的定性（README 与论文必须同一口径）

| 事实 | 精确表述 | ⛔ 不能写成 |
|---|---|---|
| 解冻性质 | **显式越权解冻（explicit override）**：H0 直接指令，**不是**证据驱动 | "行动闭环解冻了"、"条件成熟了"、"达到解冻门槛" |
| 触发条件状态 | ADR-010 的可观测触发**未满足**（真实 turn 8；审计无工具失败字段） | "真实失败样本已达到 10 个" |
| 范围 | **仅限**"模型产出 tool_use → 工具执行"之间的 STPA 前置网关（拦截 + 审计 + 强提示回注） | "实现了完整工具闭环" |
| 仍然冻结 | 多步工具规划/任务分解、执行结果自动回滚/补偿事务、工具结果写入长期记忆 | "已支持工具编排"、"已支持回滚" |
| 与注入侧 UCA 的关系 | **独立命名空间 T-UCA**，与 UCA-01…08 **并列不合并**；顺序：注入 preflight（模型调用前）→ 工具网关（模型输出解析后、执行前）→ 执行 | "扩展了 UCA-01…08"、"统一成一套 UCA" |

---

## 二、核心机制（论文 06e 的中文骨架）

### 1. 动机：副作用不可逆
注入侧闸门管"模型读到什么"；工具是执行器——删/写/外发/执行一旦发生不可撤回。软化一次拒绝只损失一轮对话，误删一个目录损失数据。危险调用常出现在三类状态：未定义冲突未澄清、相变 shift/critical、情感基线饱和。

### 2. T-UCA 矩阵（v1.1，16 条；13 block + 3 degrade）

| 组 | 条数 | 规则 | 判定 |
|---|---:|---|---|
| 状态（state） | 4 | 01 冲突/降维态下写 · 10 critical 下写 · 13 饱和下不可逆写 · 16 shift 期破坏性操作 | 01=block；**10/13/16=degrade** |
| 数据（data） | 1 | 02 凭证明文 | block |
| 变更（mutation） | 4 | 03 破坏性删改未逐字确认 · 04 冻结受保护路径 · 14 冻结校验失败仍写 · 15 路径逃逸（`..`/绝对路径/盘符/UNC/空字节） | block |
| 外发/执行（network/exec） | 2 | 05 非白名单外发 · 06 任意代码执行 | block |
| 形状/登记（shape/registry） | 4 | 07 超长·深嵌套·多层编码 · 08 未登记工具 · 09 schema/分隔符/同形字 · 12 能力未声明未授予 | block |
| 审计（audit） | 1 | 11 缺审计上下文 | block |

- `degrade` = **不执行原调用**，返回强提示并置 `requires_confirmation`，要求经宿主 `approval` seam 确认；**无审批通道 → block**（`degraded_to`）。
- **没有任何规则返回 `allow`**：`allow` 只是"只读且无命中"的默认策略。
- **判定顺序**（`docs/24` §4 `evaluation_order`）：`01,02,03,04,05,06,07,08,09,12,14,15,10,13,16,11`（静态硬 block 在前 → 状态 degrade → 审计最后）；多命中取最高严重度，**所有命中 id 进 `ucas[]`、不截断**。
- 默认策略：`unknown→block`、`read_only→allow_with_log`、`phase_unknown_mutating→block`；解析失败 → block（`reason=parse_error`，不猜意图）。
- `tool_class` 闭集：`read | write | destructive | exec | network`；mutating = `write/destructive/exec/network`，只读 = `read`。

### 3. 三层接线（必须分清"预防"与"检测"）

| 接线 | 位置 | 能做到 | 局限 |
|---|---|---|---|
| 引擎参考循环 | `engine/tool_gateway.py`，解析后、执行前 | **真前置阻断**（被拦即不执行） | 是参考实现，不是产品宿主强制点 |
| `guardTool(spec, stateProvider)` 包装器 | 插件侧 | 对**经由它**注册的工具做前置判定 | 只覆盖走包装器的工具，不是任意宿主工具 |
| 宿主会话事件 | `tool/call` / `tool/result` | **事后检测**：算 verdict、落 `tool_gate` 审计、下一轮注入强提示（`gateBlockedUntilClarify`） | 宿主无 pre-tool dispatch 钩子 → **拦不住已派发的调用** |

### 4. 宿主边界（README/论文都必须原样写）
- DSH 当前只暴露 `tool/call` / `tool/result` 会话事件，**没有"工具执行前"的 dispatch 钩子**（实测）。
- 因此"插件能拦住任意宿主工具"是**错的**；宿主级前置拦截需要宿主支持，已记录为上游请求。

### 5. 绕过面（README/论文都必须原样写）
- 工具若不经网关执行（其它插件直接执行 / 宿主绕过 `guardTool`）→ 网关**无效**；
- 定位是"**可被接入的控制点**"，不是"全系统强制"；
- 网关不执行 payload，只做静态检查 → 解析本身是攻击面（编码/嵌套/超长；正则只用线性模式防 ReDoS）；
- **误杀率未标定**（无真实流量，`provenance: guess`）；`docs/24` §9 列四类已知误杀面（critical 下写→确认疲劳、受保护路径的合法维护、讨论凭证格式的普通文档、超限的合法批量导入）。恢复路径 = 强提示 + 人工确认。

### 6. 审计事件 `tool_gate`（v1.1 字段，逐字）

```
schema, policy_version, call_id, session_id, turn_id, tool, tool_class,
verdict, allowed, ucas[], degraded_to, requires_confirmation, reason,
audit{args_digest, args_keys, args_bytes, args_preview, phase,
      variety_fallback, frozen_ok, saturated},
snapshot{target, mtime, hash}
```

- **`ucas` 是权威字段名**（不是 `tucas`）：`tool_gate.ucas` 只允许 `T-UCA-xx`；注入侧 `audit.ucas` 只允许 `UCA-0x`；**按 `event.type` 分流，绝不合并**。
- `audit` 只允许契约列出的键（防状态串味）；`snapshot` 用于"判定时的对象 = 执行时的对象"核对（check-exec 一致性）。

### 7. 拦截示例（作文案基准）

`docs/24` §3 T-UCA-01：

> `STPA 前置闸拦截（T-UCA-01）：当前存在未澄清的记忆冲突，系统已处于中性降维态。任何写/删/外发/执行类工具调用都被拒绝。请先说明以哪一条记录为准，再重试。`

> `STPA pre-flight gate denied (T-UCA-01): an unresolved memory conflict keeps the system in variety-reduced mode. All mutating tool calls are blocked until you state which record is authoritative.`

---

## 三、数字纪律（已回填：2026-10-27 本机实跑；每个数字可指回文件）

| 论文/README 位置 | 实测值 | 来源 | 状态 |
|---|---|---|---|
| 06e "Evidence" | 危险构造场景 **20 条 / 拦截 20**；良性 **8 条 / 误杀 0** | `tests/test_tool_gateway.py`（79 项断言，0 failed） | ✅ |
| 06e "Evidence" | Python↔JS 共享 **20 case：verdict / ucas / degraded_to 各 20 一致** | 同上（node 子进程跑 sim） | ✅ |
| 06e "Evidence" | `tool-gateway-sim.mjs`：**65 条断言，0 failed** | `--assert`（exit 0） | ✅ |
| 06e "Evidence" | demo：`paper/ablation/results/actuator-demo-20261027.json`；**12 次调用 → 4 allow+执行 / 8 block / 被拦者 0 执行**；命中 **9 条不同 T-UCA**；mock 执行器，`model_calls=0` | `tools/run_actuator_demo.py` | ✅ |
| 06e / 附录 | 盲写留出集：**仅 args 10/10 被拦；带 context_hint 10/10 被拦**（无 expect 标签 → **只报判定，不报"拦对率"**） | demo 内置评测（a3 写集，a5 跑） | ✅ |
| 06e / README | Python 15 文件：12 绿 + 新测试绿；**3 个失败为沙箱环境固有**（tempfile/隔离目录写入被拒，改动前后同因同数）；JS 4 套件全绿 | a5 回归跑 + 本机复跑 | ✅ |
| 附录 Table | 16 条 id/组/判定/状态输入/测试 id | `docs/24` v1.1 | ✅ 已填 |

**留出集事实（a3，已冻结）**：`paper/ablation/tool/heldout-tool-calls-v1.jsonl`，10 条、5 类各 2，
sha256 `f68fc1b3f19fc37e43b9856096d29b9a55de7dee9bf664a9b9a9592e070576d4`；作者未读 `docs/24`/实现/测试（真 blind）；
外发端点为 `*.example…` 占位域名（命中只能来自行为规则）；`conflicting-context-write` 族分两口径各报。
**注意**：`10/10` 是"网关判了 block"，**不是**"每一条都该拦"——人工/共识判定仍待 S5；论文附录与 §5 必须带这句限定。

---

## 四、H0/文案原话 → 论文精确表述（改写表）

| 可能出现的说法 | 必须改写为 | 原因 |
|---|---|---|
| "工具网关 100% 拦截 / 100% 防御" | "N/N 构造危险场景被拦截；盲写留出集 X/10；不构成防御率" | 构造场景由设计者挑选；"防御"是效果声称 |
| "覆盖了所有危险工具调用" | "16 条 T-UCA 覆盖 6 组副作用/状态面，不覆盖真实分布" | 覆盖 ≠ 完备 |
| "插件能拦住工具调用" | "插件对 `tool/call` 做**事后检测**；前置阻断只在 `guardTool` 包装的工具与引擎参考循环内成立" | 宿主无 pre-tool 钩子 |
| "解冻了行动闭环" | "**显式越权解冻**，仅限前置网关；规划/回滚/记忆回写仍冻结" | ADR-013 定性 |
| "T-UCA 是 UCA-01…08 的扩展" | "独立命名空间（16 条），并列不合并；顺序：注入 preflight → 工具网关 → 执行" | 不改注入侧契约 |
| "误杀率 0（良性场景全放行）" | "M 个构造良性场景 0 误杀；真实误杀率未标定（无真实流量）" | 构造样本 ≠ 真实分布 |
| "Python 与 JS 完全一致" | "K 个共享 case 的 verdict 一致；不覆盖全部规则分支" | 一致性检查有范围 |
| "网关能防住越权写" | "网关在**被接入**的执行路径上拦截；绕过面见正文" | 不宣称全系统强制 |

---

## 五、复现命令

```powershell
python tests/test_tool_gateway.py                     # 危险/良性/状态门控/fail-closed + Python↔JS 一致性
node plugin/dsh-lingshu/tool-gateway-sim.mjs --assert # JS 规则逐条
python tools/run_actuator_demo.py                     # 参考循环 + 真实 JSON
```

> 环境提示：Windows 沙箱下临时目录不可写时需与其它 Python 套件相同的 shim（只替换临时目录创建，不改断言/不改被测代码）。

---

## 六、写作检查清单

- [ ] 出现 "100%" 时紧跟的是"构造场景"或"共享 case"，**不是**"防御率"；
- [ ] 每处提到网关能力，都有一句宿主边界或绕过面（或紧邻引用）；
- [ ] "解冻"旁必有"显式越权（explicit override）"；
- [ ] 所有数字能指回 `tests/test_tool_gateway.py` 或 `run_actuator_demo.py` 的具体字段；
- [ ] 附录 16 条 id 与 `docs/24` v1.1 逐字一致；`tool_gate.ucas` 与注入侧 `audit.ucas` 不混用；
- [ ] 未把"留出集未评测"写成任何分数。
