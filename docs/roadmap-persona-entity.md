# R · 人格体（Persona Entity）隔离（路线图 · 未实现）

> 版本：`roadmap-persona-0.1`（草案）｜ 日期：2026-10-28 ｜ 状态：**设计方向 / 未实现 / 未排期**
> 定位：回答"同一个 DSH 宿主里存在多个工作区时，人格、记忆与安全边界如何互不污染"。本文只写**设计方向与验收草案**，不是实现说明，也不是发布声明。
> 关联：`docs/03`（PAD 基线）、`docs/06`（关系模型）、`docs/12`（人设解析器）、`docs/19`（注入侧 UCA）、`docs/24`（工具侧 T-UCA）、`docs/16`（实现状态矩阵）、`rules/`、`decisions/010`（封存与解冻纪律）、`plugin/README.md`（数据目录与 `profileTag`）
> 文件名不含编号：`docs/NN-` 编号是"已定稿规格"的序号，本文是**未实现的方向记录**，故不加编号，避免与 `docs/00–25` 混淆。
> 引用约定：源码位置一律用**符号级引用**（如 `index.js#stateDir`），不写会漂移的行号——`plugin/` 处于并发开发中，行号不稳定；仅对稳定文件保留行号并注明。

---

## §0 一句话与诚实边界

**本方向未实现。当前代码里不存在 `workspace_*` 隔离。** 逐条核实：

| 断言 | 核实方式 | 结论 |
|---|---|---|
| 代码里存在 `workspace_*` 目录 / 路径构造 | 检索 `plugin/` `engine/` `rules/` 的代码与配置（本文落盘前，`workspace_` 0 命中） | **不存在**；本文与 README 的 Roadmap 是仅有的文本出处 |
| 存在按工作区重载的 PAD 基线 / 关系模型 / 人格锚点 | 读 `plugin/dsh-lingshu/index.js`、`control.js`、`goalrelation.js` | **不存在**：人格锚点是代码常量（见 §2.2） |
| 存在按工作区绑定的 UCA 规则集 | 读 `docs/19`、`docs/24`、`rules/` | **不存在**：规则集是全局单份；`rules/` 下无工作区策略文件 |

必须同时说清"已经有的东西"与"**它们不等价**"，否则下面的设计会被误读成"已经做完一半"：

| 已有机制 | 粒度 | 它解决什么 | **不等价于人格体隔离的原因** |
|---|---|---|---|
| `LINGSHU_DATA_DIR`（env 覆盖数据目录） | 进程 | 压测日志与真实流量不混（`plugin/README.md:169`） | 运维开关，不是产品语义；不重载人格，不切换规则集 |
| `profileTag` → `.../profiles/<tag>/` | 数据目录 | 不同 profile 的 `state.json` / `memory.jsonl` / `audit.jsonl` 分目录（`index.js#profileTag`、`index.js#stateDir`、`index.js#stateFile`） | **只分文件，不分人格**：PAD、关系、锚点仍来自同一份 config 与同一份代码；切换时不重载 |
| `mode: active / observe` | 插件进程 | 是否注入 prompt、是否注册工具（`index.js#mode`） | 行为开关，不随工作区变化 |
| T-UCA-15「逃出工作区」 | 单次工具调用 | 路径逃逸（`../`、绝对路径、盘符、UNC、空字节） | 这里的"工作区"指**工具执行的相对路径根**，不是本文的人格体工作区（`docs/24:171-174`） |

> 上表四条都真实存在（出处见括号），本文的设计**复用**它们，但**不声称其中任何一条已经满足需求**。

对外口径必须与论文一致：`paper/sections/09-conclusion.tex` 的 Future work 写的是 **will introduce**（将来时），不是已交付。

---

## §1 需求来源与问题定义

H0 的直接指令（2026-10-28）：单一人格在不同工作区（写代码 vs 角色扮演）之间切换，会出现**人格撕裂**与**上下文污染**；因此引入「人格体（Persona Entity）」，实现**工作区级别的完整状态隔离**。三条核心设计逻辑（原文）：

1. **物理隔离**：数据根目录下建 `workspace_{id}/` 子目录，隔离 `memory.jsonl` 与 `state.json`。
2. **状态重载**：DSH 宿主切换工作区时，控制面重新加载该目录下的 PAD 基线、关系模型、人格锚点。
3. **规则沙盒**：不同工作区可绑定不同 STPA UCA 规则集（如工作区 A 允许 exec，工作区 B 禁止 exec）。

把"人格撕裂 / 上下文污染"拆成三个可独立验收的失效模式：

| # | 失效模式 | 具体表现 | 对应设计逻辑 |
|---|---|---|---|
| F1 | 记忆与状态串味 | 写代码时记下的"用户偏好 vim"被角色扮演读取，当成角色设定；`memory.jsonl` 只有一份 | 逻辑 1 |
| F2 | 人格与情绪残留 | 上一工作区的 PAD 偏离 / 关系阶段（如 `conflict`）被带进新工作区，第一轮"语气就不对" | 逻辑 2 |
| F3 | 安全边界不可区分 | 同一套 UCA 规则集同时套在"可以动手改代码"与"只许角色扮演"两类工作区上：收紧则误伤，放宽则越权 | 逻辑 3 |

本文**不解决**的是：F1/F2/F3 之外的任何东西（多 Agent、跨设备同步、人格复制与共享均不在范围，见 §7）。

---

## §2 设计逻辑（三条隔离逻辑）

### §2.1 物理隔离（`workspace_{id}/`）

#### §2.1.1 拟命名与目录布局（**未实现，名字待定**）

```
<数据根>/                      # 现状 = $DSH_HOME/plugins/dsh-lingshu/（index.js#home / #stateDir）
├── state.json                 # 现状：全局单份（历史平铺目录）
├── memory.jsonl
├── audit.jsonl
└── workspace_{id}/            # ← 拟新增（代码中不存在）
    ├── state.json
    ├── memory.jsonl
    ├── audit.jsonl
    └── persona.json           # ← 拟新增：人格体快照（PAD 基线 / 关系 / 锚点），名字待定
```

| 名称 | 状态 | 依据 |
|---|---|---|
| `memory.jsonl` / `state.json` / `audit.jsonl` | ✅ 已存在 | `index.js#stateFile / #memFile / #auditFile`；`plugin/README.md:39` |
| `workspace_{id}/` | ❌ **拟命名，待定** | 全仓库检索 `workspace_` 无命中 |
| `persona.json` | ❌ **拟命名，待定** | 目前人格锚点是常量、关系在 `state.json#relation`、基线参数在 `rules/decay-params.yaml`，没有"人格体快照"文件 |
| `{id}` 字符集 | 建议 `[A-Za-z0-9_-]{1,32}` | `profileTag` 已有该正则 + "非法即忽略并落审计"处理（`index.js#profileTag 校验`），直接复用可少一套校验 |

#### §2.1.2 隔离不变量

| 编号 | 不变量 | 判定方式（草案） |
|---|---|---|
| I-P1 | 同一进程内，工作区数据通过数据层 API 互相不可见 | 交叉读取返回 0 条 / 路径不共享 |
| I-P2 | `{id}` 非法即拒绝并落审计；`path.resolve` 后不得越出数据根 | 构造 `../`、绝对路径、盘符、UNC、空字节用例（复用 T-UCA-15 思路） |
| I-P3 | 迁移只前进：历史平铺目录 → `workspace_default`，可回滚且行数一致 | 迁移前后 `memory.jsonl` 行数与 `state.json` 字段集比对 |
| I-P4 | "全局聚合"（审计汇总、控制台）只读，不得反向写入工作区 | 控制台按 profile 只读（`docs/25`）不得成为写入口 |

#### §2.1.3 本节开放问题

见 §4 的 OQ-1 / OQ-2 / OQ-3 / OQ-9。

---

### §2.2 状态重载（PAD 基线 / 关系模型 / 人格锚点）

#### §2.2.1 现状：为什么"重载"现在没有落点

| 要重载的东西 | 现在在哪 | 现在的加载方式 | 缺口 |
|---|---|---|---|
| PAD 基线 | `state.json#control.integral`（积分基线）+ `rules/decay-params.yaml#pad.initial` | 启动时恢复积分基线（`index.js#IntegralBaseline 构造`）；初始值来自 `rules/` | 没有"按工作区多份基线" |
| 情绪偏离 | `state.json#dev` | 每轮 `integral.step(st.dev, 1)` 后回写（`index.js#integral.step 回写`） | 切换时不重置 → **残留** |
| 关系模型 | `state.json#relation`（`trustLevel` / `stage` / `unresolvedConflict`） | `goalrelation.js#updateRelation` 读写 | 只有单份，无工作区维度 |
| 人格锚点 | **代码常量**：`control.js:220-222` 的 `ANCHOR_PREFIX` / `ANCHOR_SUFFIX` / `DEFAULT_ANCHOR_BODY`；`index.js#anchorBody` 内联了同样的字面量 | 无加载、无配置 | **重载没有落点** |

> 这是本方向最关键的工程量：**人格锚点目前是常量，不是状态**。要做"重载人格锚点"，必须先把锚点从常量改成"可加载的人格体快照 + 默认值兜底"，否则最多只能做到"重载记忆与状态"。

#### §2.2.2 重载契约（草案）

拟新增控制面入口 `onWorkspaceSwitch(from, to)`（**拟命名，待定**）：

1. **flush**：把 `from` 的 `state.json` 落盘（复用现有 `saveState`，整文件覆盖，`index.js#saveState`）；
2. **load**：读 `to` 的 `persona.json` + `state.json`；缺文件 → 用默认人格快照，但**必须记审计**；
3. **rebuild**：重建 `IntegralBaseline` / `PhaseShiftMonitor` / 关系状态的进程内实例——不得沿用旧实例，否则闭包状态残留（对照 `index.js#IntegralBaseline / PhaseShiftMonitor 构造`）；
4. **verify**：`persona.json` 与工作区策略集进冻结哈希校验；失败 → 不进入该工作区（见 §2.2.4）；
5. **audit**：写 `workspace_switch {from, to, loaded, fallback, mismatched, at}`（**拟新增审计事件，待定**）。

#### §2.2.3 重载不变量

| 编号 | 不变量 |
|---|---|
| I-S1 | 切换后第一轮注入**不得**包含上一工作区的记忆 id、关系 `stage`、PAD `deviation` |
| I-S2 | 重载是**替换**语义，不是叠加：同一字段不得"新值 ∪ 旧值" |
| I-S3 | 重载失败不得静默降级为"默认人格"——默认人格在角色扮演工作区里等于人格错位 |
| I-S4 | 宪法层优先级不因重载而改变（安全域仍唯一 fail-closed，`docs/00 §3`） |

#### §2.2.4 失败语义（非对称，沿用项目既有原则）

| 失败 | 处置 | 理由 |
|---|---|---|
| 目标工作区 `persona.json` 缺失 | 用默认快照 + 审计 `fallback=true` | 首次进入工作区是常态，不能阻断 |
| 目标工作区 `state.json` 解析失败 | 不进入该工作区，转人工确认 | 静默重置会丢记忆且用户无感 |
| 哈希 / 冻结校验失败 | 拒绝进入（fail-closed） | 与被篡改的规则表同等级（`rules/frozen-manifest.yaml` 的语义） |
| 重载超时 | 回退到"上一工作区"并提示 | 不允许"半重载"状态开始注入 |

> 非对称原则见 `docs/00 §3`：安全失败要阻断，功能失败要可用。重载属"功能"，但**人格错位**会造成可感知的角色崩塌，因此对"状态损坏"这一档取偏保守策略。

#### §2.2.5 本节开放问题

见 §4 的 OQ-4 / OQ-5 / OQ-8。

---

### §2.3 规则沙盒（工作区级 STPA UCA 规则集）

#### §2.3.1 现状：规则是全局单份

| 层 | 规则在哪 | 粒度 | 可切换吗 |
|---|---|---|---|
| 注入侧 UCA-01…08 | `docs/19 §4`；`engine/control.py#preflight_audit`；插件 `control.js#preflightAudit` | 全局一份 | ❌ |
| 工具侧 T-UCA-01…16 | `docs/24`（policy `t-uca-1.1`，13 条 `block` / 3 条 `degrade` / 0 条 `allow`） | 全局一份 | ❌ |
| 能力声明 | `rules/capabilities.yaml`（只申请 `fs-read` / `fs-write` / `llm`，**不申请 `shell`**） | 插件级 | ❌ |
| 冻结清单 | `rules/frozen-manifest.yaml`（启动自校验，不匹配即停止注入） | 全局 | ❌ |

#### §2.3.2 设计草案：工作区 = 一份策略集的引用

拟新增 `rules/workspaces/{id}.yaml`（**拟命名，待定，未实现**），只允许**引用已注册的策略集**，不允许内联改写规则正文：

```yaml
# 拟命名，待定。未实现；当前仓库中不存在此文件。
workspace: code
ruleset: t-uca-strict     # 引用 docs/24 的判定顺序，不复制规则正文
deny: [T-UCA-06]          # 只允许"增加禁止"，见 §2.3.3
allow_exec: false         # H0 示例里的"工作区 B 禁止 exec"
```

判定顺序不变：仍由 `docs/24 §4` 的 `evaluation_order` 决定；工作区配置**只能收窄**，不能改写判定顺序与文案（文案唯一来源是 `docs/24 §3`，改文案先改文档 + 升 `policy_version`）。

#### §2.3.3 诚实约束：第一版只能"收紧"；"工作区 A 允许 exec"需要先改能力声明

H0 的例子是"工作区 A 允许 exec、工作区 B 禁止 exec"。**当前做不到"允许"，本文不假装做到**：

| 事实 | 出处 | 含义 |
|---|---|---|
| 插件**不申请** `shell` 能力 | `rules/capabilities.yaml#notRequested` | 现在没有 exec 能力可"允许" |
| T-UCA-06 拦截任意代码执行 | `docs/24 §3` | "允许 exec"等于先改这条规则的语义 |
| 能力声明进冻结清单，改动需 ADR | `rules/capabilities.yaml#meta.note`、`rules/frozen-manifest.yaml` | 不是配置项，是决策 |

因此分两步：

- **P2（可先做，保守）**：沙盒只做**单调收紧**——默认全局策略，工作区只能追加 `deny`，不能追加 `allow`。这已能落地"工作区 B 禁止 exec"。
- **P3（需 ADR + H0 批准）**：要支持"工作区 A 允许 exec"，必须先完成：新 ADR 修改 `rules/capabilities.yaml`、宿主授予 `shell` / `dynamic-code` 能力、T-UCA-06 语义从"一律 block"改为"按策略判定"、重新评估误杀面与越权面。**在此之前，任何"工作区 A 可执行代码"的说法都是错的。**

#### §2.3.4 沙盒不变量

| 编号 | 不变量 |
|---|---|
| I-R1 | 工作区策略集必须来自冻结清单，运行时可哈希校验 |
| I-R2 | 工作区策略**不得**覆盖宪法层、安全域、UCA 判定顺序与提示文案 |
| I-R3 | 缺策略集 → 用最严格的内置默认（deny-by-default），不得先回落到"全局默认"再放宽 |
| I-R4 | 无法证明"更严格"的策略变更 = 放宽，需 ADR |

#### §2.3.5 本节开放问题

见 §4 的 OQ-6 / OQ-7。

---

## §3 与既有文档 / 代码的引用关系（不重复造）

| 既有文档或文件 | 本文用它做什么 | 它需要改什么（**本文不改**） |
|---|---|---|
| `docs/03-情感插件.md` | PAD 基线、`deviation` 钳制、衰减、`pad.initial` 是人格快照的字段来源 | 多工作区下"基线"的语义需补一节，属后续规格修订 |
| `docs/06-关系模型.md` | `trustLevel` / `stage` / `unresolvedConflict` 作为重载对象；**§7 `R-G1`「多租户：`user_id` 从哪来、如何隔离，未定义」正是本方向的上游缺口** | 该缺口由本文承接，但**尚未解决** |
| `docs/12-人设解析器.md` | `baseline_pad`、`traits`、钳制表是人格体快照的字段来源；解析器只产草案 | 工作区维度下"用户二次确认"流程需扩展（未做） |
| `docs/19-控制论重构契约.md` | 注入侧 `UCA-01…08`、积分 / 相变 / 人格锚点机制 | 规则沙盒不改变判定顺序，只做策略选择 |
| `docs/24-工具网关UCA.md` | T-UCA-01…16、`evaluation_order`、`policy_version` | P3 若要"按工作区允许 exec"必须改它（新 ADR） |
| `docs/16-实现状态.md` | 能力 × 实现 × 验收矩阵；**本方向当前不在矩阵里 = 未实现** | 实现后由 `tools/gen_impl_status.py --write` 生成新行；**不得手改** |
| `rules/capabilities.yaml` | 能力最小化声明（不申请 `shell`） | P3 改动需 ADR |
| `rules/frozen-manifest.yaml` | 冻结清单与哈希自校验 | 新增 `rules/workspaces/` 需 ADR 并入清单 |
| `rules/decay-params.yaml` | 全部数值的唯一来源（PAD / 衰减 / 阈值） | 本文不引入任何新数值；草案中的名字一律标"待定" |
| `plugin/README.md` | `LINGSHU_DATA_DIR`、`profileTag` 的现状与审计事件命名 | 复用其 id 校验与拒绝落审计的做法 |
| `decisions/010-四方向封存与解冻条件.md` | 封存 / 解冻纪律（可观测触发条件 + ADR + H0 批准） | 本方向按同一纪律的 P 阶段推进，**暂不新增冻结项** |
| `decisions/015-控制台解耦为独立本地只读服务.md` | 若合并 `profileTag` 与 `workspace_{id}` 两套维度，需参照其 ADR 形式 | 见 OQ-2 |

---

## §4 开放问题（Open Questions）

| # | 问题 | 为什么阻塞 | 建议动作 |
|---|---|---|---|
| OQ-1 | 工作区 `{id}` 由谁赋值：宿主 workspace 标识 / 用户配置 / 会话 cwd？ | 逻辑 1/2/3 全部依赖一个稳定 id | 先做宿主能力探针（`probe/`）；`docs/00 §4.1` 的 H1–H6 **没有** workspace 项，不能假设存在 |
| OQ-2 | `profileTag` 与 `workspace_{id}` 两套维度合并还是并存？ | 合并 = 数据目录语义变更，影响控制台与混沌脚本 | 写 ADR 后再动；并存期间必须写清优先级 |
| OQ-3 | 是否允许"有意共享"的跨工作区层（例：用户对代码的稳定偏好）？ | 若允许，隔离会被"共享层"名义绕过；若不允许，写代码工作区的稳定偏好无法跨工作区复用 | 建议第一版**不允许**，只做显式导出 / 导入 |
| OQ-4 | 人格锚点常量化后，默认值来源放哪：`rules/decay-params.yaml` 还是 `persona.json`？ | 关系到"参数唯一来源"这条项目铁律 | 默认值进 `rules/`，用户确认值进 `persona.json` |
| OQ-5 | 宿主是否提供"工作区切换"事件？ | 没有事件就只能轮询 / 在首 step 惰性判定，重载时机不同 | 探针实测 `agent/pre-step` 的 payload 与 `session/event` 类型 |
| OQ-6 | `docs/06 R-G1` 的 `user_id` 多租户缺口如何在工作区维度定义？ | 关系模型的重载粒度取决于"关系属于谁" | 与 `docs/06` 规格修订一起做，避免两处各说各话 |
| OQ-7 | 跨工作区审计聚合是否泄露隐私（控制台按 profile 读）？ | `docs/25` 控制台只读，但"聚合多工作区"是新的读取路径 | 聚合层只出统计量，不出原文（沿用 `docs/25` 脱敏原则） |
| OQ-8 | 两个会话同时切换工作区时如何避免交叉写？ | 当前 `saveState()` 是整文件 `writeFileSync` 覆盖，未见文件锁（`index.js#saveState`） | 先定义单写者约束；必要时加原子写 + 锁 |
| OQ-9 | 迁移与冻结回放集的关系 | `replay-set/` 在冻结清单里，迁移脚本不得改动它 | 迁移脚本只碰数据根，不碰 `replay-set/` |
| OQ-10 | 发布口径何时可以从"will introduce"改为"implemented"？ | 诚实性红线 | 代码 + 验收 + `docs/16` 矩阵行同时存在后，单独一次文档提交 |

---

## §5 验收草案（PE-A1…PE-A9）

> **这些是草案，不是通过记录。** 状态列当前全部为 ⏳（未实现）；未实现的验收项不得勾选，也不得写进发布文案。

| # | 输入 / 步骤 | 期望 | 判定 | 状态 |
|---|---|---|---|---|
| PE-A1 | 在 `workspace_a` 与 `workspace_b` 各写 1 条记忆，再从另一工作区召回 | 交叉召回为 0 条；两目录文件独立 | 召回结果条数 == 0，且两工作区文件哈希不同 | ⏳ |
| PE-A2 | 在 `workspace_a` 制造 PAD 偏离与关系 `conflict`，切换到 `workspace_b` | `b` 第一轮注入不含 `a` 的记忆 id / `stage` / `deviation` | 审计 `pre_step` 的注入摘要字段断言 | ⏳ |
| PE-A3 | 写入三份快照后切换 | PAD 基线 / 关系状态 / 人格锚点与快照逐字段相等 | 字段级 diff == 空（`null` 视为未设置） | ⏳ |
| PE-A4 | `workspace_b` 绑定 `deny: [T-UCA-06]`，发起 exec 类调用 | 判定 `block`，`ucas` 含 `T-UCA-06` | `tool_gate.verdict == "block"` 且 `ucas` 包含该项 | ⏳ |
| PE-A5 | 人为损坏目标工作区 `state.json` 后切换 | 不进入该工作区，转人工确认，不静默用默认人格 | 审计出现拒绝事件，且下一轮注入仍属上一工作区 | ⏳ |
| PE-A6 | 从历史平铺目录迁移到 `workspace_default` | 可回滚；`memory.jsonl` 行数与 `state.json` 字段集不变 | 迁移前后计数相等，回滚后哈希回到原值 | ⏳ |
| PE-A7 | 两个会话并发切换 | 无交叉写、无半重载状态 | 两工作区文件分别只含自己的记录 | ⏳ |
| PE-A8 | 伪造非法 `{id}`（`../` / 绝对路径 / 盘符 / UNC / 空字节） | 拒绝并落审计，路径不越出数据根 | 审计含拒绝原因，实际写入路径前缀校验通过 | ⏳ |
| PE-A9 | 检查对外文案 | 未实现期间，README / 论文 / docs 均为将来时或"未实现" | 全文检索 `workspace_` 只出现在本文与 Roadmap 的"未实现"语境 | ⏳ |

---

## §6 分阶段与解冻纪律

按 `decisions/010` 的同一纪律推进：**先补证据 → 写 ADR → H0 批准 → 再排期**；禁止"顺手做"。

| 阶段 | 内容 | 前置条件 | 交付 / 判定 | 回滚 |
|---|---|---|---|---|
| **P0** | 只做本文（设计方向 + 开放问题 + 验收草案） | 无 | 本文合并；不写任何产品代码 | 删文即可 |
| **P1** | 单工作区等价改造：`workspace_default`，行为零回归 | OQ-1 / OQ-2 有结论 | 现有套件全绿；`docs/16` 新增行（生成，不手改） | 保留平铺目录兼容开关 |
| **P2** | 多工作区 + 状态重载 + 规则沙盒（只收紧） | P1 完成；OQ-4 / OQ-5 / OQ-8 有结论 | PE-A1…A3、A4（deny）、A5…A9 通过 | 切回 P1 的单工作区模式 |
| **P3** | "工作区 A 允许 exec"（能力放宽） | 新 ADR + H0 批准 + 宿主授予能力 | T-UCA-06 语义改造完成，误杀面重新评估 | 撤销能力声明即回到 P2 |

风险登记（设计阶段）：

| # | 风险 | 当前判断 |
|---|---|---|
| RK-1 | "物理隔离"被读成**安全隔离** | 同进程、同 OS 用户，文件目录隔离 ≠ 权限隔离；文案必须写清 |
| RK-2 | 插件 config 可被 `cordis.patch.yml` 整段覆盖（H4 已证伪，`docs/00 §4.1`） | 工作区策略文件不能声称"用户改不了"；只能靠冻结哈希 + 自校验 |
| RK-3 | 重载与冻结回放基线冲突 | 迁移脚本只碰数据根；回放集不动（OQ-9） |
| RK-4 | 沙盒被当成"已经能按工作区放开能力" | 见 §2.3.3：放宽必须走 P3 |

---

## §7 边界（明确不做）

1. **不做多 Agent / 多智能体黑板**——`decisions/010` 方向 3 仍封存；本方向是**单 Agent、多工作区**。
2. **不做人格复制、跨工作区人格继承**——第一版只做隔离；共享与导入见 OQ-3。
3. **不声称"物理隔离 = 安全隔离"**——同一进程内的目录隔离不是权限边界。
4. **不改变宪法层、安全域与 UCA 判定顺序**——沙盒只做策略选择与收紧。
5. **不在实现前更新 `docs/16` 矩阵**——矩阵由 `tools/gen_impl_status.py` 生成，手改即造假。
6. **不在本文引入任何新数值**——数值一律进 `rules/`；草案里的名字（`workspace_{id}`、`persona.json`、`rules/workspaces/`）**未实现，待定**。

---

## §8 本文自身的验收（写完即核）

| # | 检查项 | 结论 |
|---|---|---|
| 1 | 「本方向未实现、当前代码里不存在 `workspace_*` 隔离」已显式写出 | ✅ §0 首句 + §0 表格 |
| 2 | 文中出现的路径 / 字段名均在仓库核实，未核实的一律标"拟命名，待定" | ✅ `state.json` / `memory.jsonl` / `audit.jsonl` / `profileTag` / `LINGSHU_DATA_DIR` 已核实；`workspace_{id}/` / `persona.json` / `workspace_switch` / `rules/workspaces/` 均标待定 |
| 3 | 源码引用用符号级引用（`index.js#...`），不依赖会漂移的行号 | ✅ 仅 `control.js:220-222`、`plugin/README.md:169`、`docs/24:171-174` 保留行号（文件稳定，已复核） |
| 4 | 标题层级自洽：`## §2` 只出现一次，三条逻辑为 `### §2.1/§2.2/§2.3`，子节为 `####` | ✅ 无重复编号、无跳级 |
| 5 | 引用关系覆盖 `docs/03`、`06`、`12`、`19`、`24`、`16` 与 `rules/` | ✅ §3 |
| 6 | 开放问题与验收草案齐全 | ✅ §4 / §5 |
| 7 | 文件名不与 `docs/` 编号体系冲突 | ✅ 无编号（H0 指定文件名） |
| 8 | 论文 / README 口径一致（将来时、未实现） | ✅ 见 §0 末段；README「Roadmap」区块与 `09-conclusion.tex` 的 Future work 同口径 |

---

## §9 进展（2026-10-28）：ADR-017 + P1/P2 **逻辑层 + 接线**已落地（活插件待同步/重载）

> 本节只记事实，不改上面任何"未实现"结论——**接线已进 `index.js`，但活插件副本尚未同步/重载，对运行中的插件仍零影响**。

| 项 | 事实 | 证据 |
|---|---|---|
| 解冻性质 | H0 直接指令（**显式越权解冻**），不是"证据成熟" | `decisions/017` §1 |
| P1 数据层 | `plugin/dsh-lingshu/workspace.js`：id 解析 + `workspace_{id}/` 目录布局；**缺省零回归**（不配 = 旧路径逐字节不变） | `workspace-sim` PE-A1 / PE-A8 / "legacy 一致" |
| P1 接线 | `index.js` 已调用：`stateDir(profileTag, workspaceId)` + `resolveWorkspace()`；非法 id 落 `workspace_rejected` 审计 | `workspace-wiring-sim` **16/16**（真 `apply()`） |
| P2 策略层 | deny-only 合并；未登记规则 id fail-closed；不允许 `allow` / 改判定顺序 / 改文案 | `workspace-sim` PE-A4 |
| 自检 | `workspace-sim` 47/47、`workspace-wiring-sim` 16/16；`npm run workspace` / `workspace:wiring` | 2026-10-28 本机实测 |
| 与运行态的关系 | 活插件副本（`~/.dsh/plugin-src/...`）**未同步、未重载**；README 的 `[ ]` 保持不勾 | `decisions/017` §5 |
| 仍未做 | 策略文件位置（`workspaces/{id}.yaml` 放哪）、PE-A3/A5/A6/A7、P3 | `decisions/017` §5 |

口径不变：**对外仍写"未实现 / will introduce"**；`docs/16` 矩阵仍无对应行（生成器管，不手改）。
