# LINGSHU（灵枢）学术术语映射表 · GLOSSARY

> **文件性质**：论文写作的**术语与落点对照表**，供 H0 评审 + 后续正文写作使用。本文件不写正文。
> **版本**：paper-glossary-0.1 ｜ 2026-10-27 ｜ 对应项目版本 v5.1 ｜ 路径基准：仓库根 `<REPO_ROOT>`
> **配套文件**：[OUTLINE-zh.md](OUTLINE-zh.md)（章节编号以它为准）。
> **一句话规则**：**每个术语必须能指到一个真实文件与真实行号；指不到的，必须显式标 `framing-only`，不得冒充已实现机制。**

---

## 0. 怎么读这张表

| 列 | 含义 | 填法纪律 |
|---|---|---|
| 工程原术语 | 仓库里实际使用的词（代码/文档原文） | 原样抄，不美化 |
| 学术正式术语(EN) | 论文里用的正式英文名 | 首次出现即定义；与正文一字不差 |
| 中文释义 | 给 H0 看的白话解释 | 一句话说清"它到底在干什么" |
| 精确定义 | 可机检的语义（含公式/判定顺序/边界） | 不含"大约/通常/显著"这类词 |
| 代码/文档落点 | `file:line`，**必须是真实读过的行** | 读不到就不写行号（见 §4 核验记录） |
| 使用章节 | 出现在正文哪一节 | 与 OUTLINE 的节号一致；不用的术语不进正文 |

**三种标记**（出现在"精确定义"列）：
- 无标记 = 有代码落点，**可以直接写进 Method**；
- `⚠ framing-only` = 学术名称是解释框架，**仓库内无对应实现**，只能出现在 Related Work / Discussion，且首次出现必须标注；
- `⚠ 借用机制` = 机制来自外部理论（ACT-R / STPA / Ashby / PAD），我们**实现但未提出**，措辞必须写 "we adopt / we adapt"，不得写 "we propose"。

---

## A. 任务指定必含的 5 组映射（正文出现频率最高）

| 工程原术语 | 学术正式术语(EN) | 中文释义 | 精确定义 | 代码/文档落点(file:line) | 使用章节 |
|---|---|---|---|---|---|
| **U 型注入**（`u_shape`） | **U-shaped Attention Anchoring** | 把最强的召回结果放在 prompt 首与尾、最弱的塞中间，让"最容易被忽略的位置"留给最弱的条目 | 输入为按分数降序的召回列表 `r0…r_{n-1}`；注入序 `head=r[0::2]`、`tail=r[1::2]` 逆序，输出 `head + rev(tail)`：`n=3→[r0,r2,r1]`、`n=4→[r0,r2,r3,r1]`、`n=5→[r0,r2,r4,r3,r1]`；`n≤2` 保持原序。**只改注入侧**（`event.injection_order`），`memory.recall().returned` 顺序不变 | `engine/pipeline.py:43-61`（`u_shape` 定义与注释）；`engine/pipeline.py:101-102`（计算 `injection_order`）；`engine/pipeline.py:196`（写入事件）；`docs/19-控制论重构契约.md:96-109`（§2.5 语义与 n=3/4/5 例子）；验收 `tests/test_control.py`（`test_u_shape`，见 `docs/19:184`） | §4.7、§5.2、Table 1 |
| **相变监控（70%/85%）** | **Phase-Shift Preemptive Monitoring** | 上下文快满之前就提前"拉一把"：提高自我指涉、在 prompt 首尾补人格锚点，而不是等模型变傻再补救 | `ratio = clamp(used_tokens / window_tokens, 0, ratioMax=1.5)`；history 容量 64；**判定顺序必须先 hard 后 shift**：`ratio≥0.85→critical`，否则 `ratio≥0.70→shift`，否则 `normal`；`trend=(r[-1]−r[-3])/2`；`prefeed = phase∈{shift,critical}`；`anchor_required = (phase==critical) ∨ (phase==shift ∧ trend>0.02)`；`self_reference_boost = phase≠normal`。**0.70/0.85/0.02 全部 `provenance: guess`** | `engine/control.py:78-123`（`PhaseShiftMonitor`）；`engine/control.py:103-109`（先判 hard 的修复）；`engine/pipeline.py:116-121`（仅在调用方同时给 used/window 时观测）；`docs/19-控制论重构契约.md:59-77`（§2.3）；`rules/decay-params.yaml:228-232`（`phaseShiftRatio`/`phaseShiftHardRatio`/`trendThreshold`/`ratioMax`/`historyCapacity`） | §4.4、§5.3、§5.4、Fig. 2、Table 1 |
| **互斥表降维** | **Requisite Variety Dimensionality Reduction** | 遇到"互斥表没覆盖的冲突"时**主动收缩行为空间**：只丢风格域，退回中性事实 + 澄清，不硬撑 | `if conflict=="unresolved" and not matched_mutex:` → `fallback=True`、`marker="requisite_variety_fallback"`、`drop_domains=实际存在的 style 域`、`action="degrade_to_fact_and_clarify"`；否则 `fallback=False`。**fail-open**：不改变 `preflight` 的 `verdict`（仍 `allow`），只降渲染。engine 侧按"渲染结果是否非空"声明域；插件侧在冲突未决时**无条件**声明并丢弃 style（更保守，宁可多降一域） | `engine/control.py:146-170`（`RequisiteVarietyGuard.check`）；`engine/pipeline.py:145-154`（接线）；`engine/pipeline.py:239-251`（`_apply_variety_fallback` 实际丢块）；`docs/19-控制论重构契约.md:15`（机制 3 状态=已实现）；`docs/19:249`（插件侧 v5.1 接线与 fail-open）；`docs/19:218-222`（插件更保守的已知差异）；`decisions/008:19`（"实现 + 接线（正常路径不可达）"） | §4.5、§5.3（S4）、§5.4（S4 host）、Table 1 |
| **ACT-R 衰减** | ⚠ **Information-Theoretic Rate-Distortion Memory Consolidation**（**framing-only**） | 用"频率 + 近因"决定一条记忆还该不该被想起来；论文里把它类比成"记忆巩固=有损压缩的率失真权衡" | **学术名称为论文框架用词，仓库无 rate-distortion 目标函数、无熵/失真度量实现。**实际实现：双通道 `potency = clamp(max(importance·exp(−Δt/τ_eff), importance·normalized(B)·w), 0, 1)`，其中 `τ_eff = τ_type·(0.5+importance)`、`w = memory.actrWeight = 0.6`；`B` 为 ACT-R base-level（见 C 段「ACT-R base-level」）。纯 ACT-R 替换会把 `recall@3` 从 1.000 打到 **0.556**，故必须取大而非替换 | `engine/memory.py:39-64`（`potency` 双通道与"为什么不是纯 ACT-R"注释）；`engine/actr.py:30-41`（base-level）；`docs/19-控制论重构契约.md:25-44`（§2.1 数学与实测数字）；`rules/decay-params.yaml:216-221`（`actr{d,ref,lo,hi}`） | §4.3、Table 1（**必须标 framing-only**）、§7.1 |
| **STPA 前置闸** | **Causal Interposition Safety Gate** | 不等输出后再审：在"拼装完、还没发模型"这一步拦下会破坏安全约束的注入，命中就熔断 | 在 `compose → render →（锚点）` 之后、发模型之前，对候选 prompt（+ 结构化事实块 + 相位结果 + 饱和状态）做 8 类 UCA 检查；命中 `UCA-01…07` 任一 → `verdict="block"`、`fail_mode="closed"`，pipeline **返回 `prompt=None`** 并只给熔断文本；`UCA-08`（积分饱和）**只写 `downgrades`，不改 verdict**（降级不熔断） | `engine/control.py:191-232`（`preflight_audit` 全文）；`engine/control.py:221-223`（UCA-08 降级仅留痕）；`engine/pipeline.py:164-181`（闸门调用）；`engine/pipeline.py:204-207`（`prompt_out=None` 的 fail-closed 出口）；`docs/19:125-139`（§4 UCA 表与 fail 模式） | §4.6、§5.3（S3）、§5.4、App. B、Table 1 |

---

## B. 论文引入但**仓库无实现**的术语（framing-only，必须显式标注）

| 工程原术语 | 学术正式术语(EN) | 中文释义 | 精确定义 | 代码/文档落点(file:line) | 使用章节 |
|---|---|---|---|---|---|
| （无） | ⚠ **VSM / System 1–5 映射**（Viable System Model Hierarchy Mapping） | 用 Beer 的可生存系统模型解释"哪一层管哪一层" | **全库检索 `VSM` 命中 0 条，无映射代码、无映射文档。**仓库里唯一可类比的层级不变量是：八层优先级表（序号越小优先级越高）与"全系统唯一 fail-closed 环节"的归属。论文若用，必须写 *borrowed as an interpretive lens; not implemented* | 无直接落点。结构类比对象：`docs/00-架构总览.md:9-20`（八层优先级表）、`docs/00:68-83`（fail-closed 唯一归属） | §2 簇 D、Table 1（标 "no code locus"） |
| （无） | ⚠ **Cyber-Cognitive Envelope** | "把宿主 prompt 组装面整个包起来的那层控制包络"——相变前馈 + 必要多样性 + STPA 前置闸 + 锚点，合起来的那一层 | **仓库无此术语（检索命中 0）。**对应实体是"上下文控制论层"。若论文用作总称，首次出现必须定义为 *the control envelope over the host's prompt-assembly surface*，并声明此名称为本文命名、非项目原术语 | 无字面落点。实体落点：`docs/00-架构总览.md:24-50`（架构图中控制层位置）、`docs/19-控制论重构契约.md:11-20`（机制→模块映射表）、`engine/control.py:1-22`（模块头注释列四机制） | §1、§4.1、Fig. 1、Table 1 |
| （无） | ⚠ **rate-distortion**（率失真） | 信息论里"压缩率与失真度的权衡" | **无落点。**只能作"记忆巩固=有损压缩"的类比；最接近的真实实现决策是**归档门**（`potency < archiveThreshold` 即归档）与遗忘两段式。论文**不得**写率失真公式，除非另行实现并标"未验证" | 无直接落点。最接近：`engine/memory.py:368`（按 `archiveThreshold` 判定归档）、`engine/memory.py:39-64`（衰减通道） | §4.3（只允许一段类比）、§7.1；**不得**进 Table 1 的"已实现"列 |

> **H0 需要知道**：这三组是"更学术但更脆"的术语。团队建议**降级标注**而非删掉——保留解释力，但不给审稿人抓到"包装"的机会（见 OUTLINE §8 风险 R6）。

---

## C. 其余核心术语（正文会反复用到）

| 工程原术语 | 学术正式术语(EN) | 中文释义 | 精确定义 | 代码/文档落点(file:line) | 使用章节 |
|---|---|---|---|---|---|
| **fail-closed** | **Fail-Closed Safety Posture** | 出错时**阻断**，而不是降级放行 | 全系统**只有宪法层审计**是 fail-closed：规则版兜底，不确定一律 reject；其余环节全部 fail-open 或静默降级。`preflight_audit` 的返回里 `fail_mode="closed"` 指"该闸门采取阻断姿态"，不等于全系统 fail-closed | `docs/00-架构总览.md:68-83`（§3 唯一 fail-closed + 对照表）；`docs/10-降级矩阵.md:7-14`（§1 唯一 fail-closed 环节）；`engine/control.py:229`（`fail_mode:"closed"`）；`engine/pipeline.py:204-207`（block 时 `prompt=None`） | §4.1、§4.6、§7.8、Table 1 |
| **fail-open / 降级** | **Fail-Open Degradation** | 功能失败时"降级并记录"，保证可用 | 记忆编码器降级为规则提取（`confidence≤0.5`，连续 3 次暂停钩子）；情感评估器沿用上一轮偏离并跳过注入；合成器降级为"仅宪法层 + 用户当前消息"；其余见降级矩阵逐项 | `docs/10-降级矩阵.md:18-31`（§2 全表）；`docs/00:68-83`（汇总表） | §4.1、§6、§7 |
| **UCA-01…08** | ⚠ **Unsafe Control Actions (STPA-derived)** | STPA 里的"不安全控制动作"：这套系统里 8 种会导致安全属性被破坏的注入行为 | `UCA-01` 注入内容覆盖宪法约束；`02` 宪法校验失败仍注入；`03` 拒绝场景注入风格/情感；`04` 记忆被当事实陈述；`05` 预算截断挤掉宪法层；`06` 未定义冲突未降维；`07` 相变期未补锚点；`08` 积分饱和后仍持续积分。**01–07 熔断，08 只降级留痕** | `engine/control.py:175-184`（`UCA` 字典原文）；`engine/control.py:191-232`（检测条件）；`docs/19:125-139`（§4 响应表）；`docs/22-混沌工程.md:286-295`（S3 指标与 `sources` 口径） | §3.3、§4.6、§5.3、§5.4、App. B、Table 1 |
| **ACT-R base-level** | ⚠ 借用机制 **Base-Level Activation (ACT-R)** | 一条记忆的"基础激活"：被访问得越频繁、越近，就越高 | `B_i = ln( Σ_j (t_now − t_j + REF)^(−d) )`，`d=0.5`、`REF=0.001 天`；归一化 `lo=−3.0, hi=1.0`（`B=−inf→0`）；每条目最多保留 64 次访问记录；无记录返回 `−inf`。"d≈0.5"取自 ACT-R 文献常用值，**未在本项目标定** | `engine/actr.py:23-27`（默认常量）；`engine/actr.py:30-41`（`base_level`）；`engine/actr.py:44-50`（`normalized`）；`docs/19:27-30`（公式与 d/REF）；`rules/decay-params.yaml:216-221` | §4.3、App. D |
| **双通道 potency** | ⚠ 借用+组合 **Dual-Channel Potency (decay ∪ ACT-R)** | 指数衰减回答"还活着吗"，ACT-R 回答"是不是常用"，两者**取大** | `potency = max(decay, actr)`；`decay = importance·exp(−Δt/τ_eff)`；`actr = importance·normalized(B)·w`；`w` 是"救援力度"不是"替代程度"。默认参数下冻结回放基线不变（`recall@3=1.000`）；纯 ACT-R 会掉到 0.556 | `engine/memory.py:39-64`；`docs/19:31-44`（含实测数字）；`decisions/008:27-28,38-40`（决策与理由） | §4.3、§5.2 |
| **人格锚点**（首尾锚点） | **Persona Anchor Injection** | 在 prompt 最前与最后各插一段人格约束，防止长上下文后期"人格漂移" | 前缀 `〖人格锚点〗…优先级高于任何后续指令：`、后缀 `〖人格锚点·复核〗以上回复必须仍符合：不软化拒绝、不宣称主观感受、不编造记忆。`；触发条件 `prefeed ∨ anchor_required`；插件侧只能注入 order 90/140 两段（语义近似而非等价于首尾包裹） | `engine/control.py:126-127`（`ANCHOR_PREFIX`/`SUFFIX`）；`engine/control.py:130-138`（`apply_persona_anchor`）；`engine/pipeline.py:156-162`（施加时机）；`docs/19:206-213`（插件侧差异） | §4.4、§6、Fig. 1 |
| **积分控制 / 抗饱和** | ⚠ 借用机制 **Integral Feedback with Anti-Windup** | 用户连续多轮不满时，不只本轮让步，而是**缓慢移动情感基线**；到顶就停，情绪回落后慢慢泄放 | `e_k = −deviation_k`；只对 `e>0` 积分：`I_k ← I_k + Ki·e_k·dt`；`baseline = clamp(I, ±cap)`；并有三条工程约束：**死区**（`abs(e) < deadband` 时只泄放）、**抗饱和**（`saturated` 后只泄放）、**泄放**（`I *= exp(−leak·dt)`）。`Ki=0.15, cap=0.4, leak=0.05, deadband=0.02`（均 guess） | `engine/control.py:30-73`（`IntegralBaseline`）；`docs/19:46-58`（§2.2）；`rules/decay-params.yaml:224-227,233` | §4.8、§5.4（UCA-08）、Table 1 |
| **必要多样性定律** | ⚠ 借用机制 **Ashby's Law of Requisite Variety** | 控制器能处理的冲突种类有限；超出能力时必须收缩行为空间 | 本项目的落地形式就是 A 段「互斥表降维」：`conflict=unresolved ∧ 无互斥规则 → 丢 style 域`。**不实现 VSM**（见 B 段） | `engine/control.py:141-170`（模块注释与实现）；`docs/19:15`；外部理论出处须引 Ashby 原始文献（不得引本项目文档） | §2 簇 D、§4.5 |
| **五域合成** | **Five-Domain Prompt Composition** | 把要注入的东西分成五个"影响轴"，各自内部排序、互不越界 | 五域 = `safety`（是否拒绝）/ `fact`（说什么）/ `organization`（是否给选项/叙事）/ `style`（语气详略）/ `retrieval`（用哪种检索策略）。域间不排序，靠互斥表裁决；域内排序固定 | `docs/00-架构总览.md:56-64`（§2 影响域表）；`engine/compose.py:51`（`compose`）；`docs/09-合成矩阵.md:8`（§1 五域分类） | §4.1、§4.2、Fig. 1 |
| **8 条互斥表** | **Mutual-Exclusion Table** | 两个域同时想说话时，规则先裁决"丢谁" | `MUTEX` 列表逐条裁决；无互斥规则时默认丢低优先级域并记 `dropped.reason="no_mutex_rule"`；**互斥表优先于动态权重**（权重只决定预算淘汰顺序） | `engine/compose.py:24`（`MUTEX`）；`engine/compose.py:44`（裁决循环）；`docs/09-合成矩阵.md:34`（§3 互斥表）；`docs/09:117`（无规则时的默认策略）；`docs/00:64` | §4.2、§6 |
| **拼装顺序** | **Frozen Assembly Order** | 注入块的固定排列顺序，任何机制都不能改 | `ASSEMBLY_ORDER = ("constitution","fact","safety","organization","style","user_message")`；`[constitution]`/`[safety]`/`[user]` **永不参与预算淘汰**；动态权重不改变顺序 | `engine/compose.py:19`（常量）；`engine/compose.py:208`（`render` 按此顺序）；`docs/19:90-94`（硬约束）；`rules/decay-params.yaml:252`（`frozen: true`） | §4.1、§4.2 |
| **动态权重 / 全局工作空间** | ⚠ 借用 **Global-Workspace Dynamic Weighting** | 预算不够时，按权重从低到高牺牲；只是"淘汰顺序"，不是"谁能压过谁" | `w_fact=0.70+0.50·[refuse_claim]`；`w_org=0.50+0.35·[goal_blocked]`；`w_style=0.40+0.40·[emotion_intensity≥0.6]`；淘汰 = 权重升序。**硬约束**：拒绝场景丢弃 style/organization/retrieval；互斥表仍生效 | `engine/compose.py:134`（`_compute_weights`）；`docs/19:79-94`（§2.4）；`rules/decay-params.yaml:238-247` | §4.2、§6 |
| **预算化召回契约** | **Budgeted Recall Contract** | 装不下的召回结果要"如实标记"，不能假装没有 | 返回字段含 `matched_count` / `excluded_by_budget` / `truncated` / `minimum_budget` 等；**保序装填**、**截短不丢 ID**、**诚实标记**（"没注入"≠"没有记录"）；元认知据此把置信度降到 `mid` 并给 `hedge` | `engine/memory.py:117`（`recall`）；`engine/memory.py:104-109`（`_empty_recall` 的预算字段）；`contracts/recall.schema.json`；`research/00-外部项目参考.md:110-135`（契约来源与三条规则） | §4.2、§4.3、App. F |
| **参数单一来源** | **Single-Source-of-Truth Parameterization** | 一个数值只允许写一次；重复副本必须被机器比对 | 权威在 `rules/*.yaml`；`engine/config.py` 是零依赖影子；插件自包含导致**三副本**（rules / `cordis.patch.yml` / `control.js#DEFAULT_PARAMS`），由 `check_config_sync` **66 项**（rules↔config 48 + 插件侧 18）逐键比对；含重复键检测 | `docs/00-架构总览.md:130-136`（§5）；`docs/19:214-216`（三副本与防漂移）；`tools/check_config_sync.py`（工具本体）；`项目简介.md:127`（66 项 0 fail） | §4.10、§6、App. D |
| **provenance: guess / measured** | **Parameter Provenance Classification** | 每个数值都要标"它是猜的、抄文献的、还是实测标定的" | 三种取值 `guess / literature / measured`；文档与代码冲突以 `rules/` 为准。**当前 `measured` 数量 = 0**；真实 turn 8、有 ratio 观测 5（全 0）；标定门槛缺 `degraded 0/3`、`ratio turn 5/30` | `rules/decay-params.yaml:4`（取值定义与"全部为 guess"）；`assumptions/未验证.md:57-66`（§三 参数取值）；`acceptance/shadow-baseline.md:174-241`（逐参数门槛与缺口）；`acceptance/chaos/calibration-2026-10-26.md`（degraded 0/3、对照 3/3） | §3.2、§4.4、§5.5、§7.1、App. D、Table 2 |
| **灰度 + 护栏回滚** | **Staged Rollout with Guardrail-Triggered Rollback** | 改动先小流量试，跌破红线就自动退回上一版 | 灰度档 `steps=[0.1,0.5,1.0]`、每档最少 200 样本；护栏 `G1–G7`（G1 supersede 误判 ≤5%、G5 `hallucination_on_no_hit=0.0` 硬红线、G6 拒绝回归=1.0 硬红线、G7 `recall_delta ≥ −0.02`）；缺数据时 hard→`block`、quality/resource→`skip` | `rules/learning-params.yaml:50-55`（灰度）；`:84-97`（护栏 + `onMissing`）；`engine/gray.py:21-22`（`GUARDRAILS`）；`docs/13-学习机制.md:262-306`（§7 闭环与三道闸） | §4.9、§6、Table 1 |
| **考卷只读** | **Frozen Evaluation Instrument** | 评估口径、回放集、护栏阈值不许被"学习者"改——否则系统最先学会的是改考卷 | 三道闸之一；`evaluation.*` / `replay-set.*` / `guardrail.*` / `constitution.*` / `compose-matrix.*` 为提议黑名单；写入 `_frozen/` 并由审计器 `A10` 拒绝 | `docs/13-学习机制.md:280-306`（三道闸）；`rules/learning-params.yaml:43-48`（黑名单）；`engine/constitution.py:22`（A1–A10）；`engine/constitution.py:75`（`audit`） | §4.9、§6、Table 1 |
| **提议（proposal）** | **Evidence-Gated Parameter Proposal** | 抱怨不是提议；**回放验证过的抱怨**才是提议 | 四步：纠正 → 改写成新测试样本（原集不动）→ 假设生成 → 回放验证（① 纠正解决率提升 ≥0.02 ② 整体目标函数不退化 ③ 护栏不破）→ 必填字段 + 黑名单校验；同类纠正 **≥3 条**才够证据门槛 | `engine/proposal.py`；`docs/13:195-232`（§5.1 四步与三条硬规则）；`rules/learning-params.yaml:38-48`（`proposal` 门禁） | §4.9 |
| **影子模式** | **Shadow Mode** | 推断只记录、不注入——先攒数据再决定要不要开 | `shadowGoal` / `shadowRelation` 为真时，目标/关系照常记录（`updateGoals`/`updateRelation` + `writeRequests`），但**不注入 prompt**；真实标定阶段默认开启（因规则桩误报率高） | `docs/19:231-232`（§7.1 第 2 项）；`交接总览.md:101-102`（只记录不注入）；`交接总览.md:122`（真实 headless 的 `apply.shadow` 观测） | §4.9、§7.5 |
| **supersede（取代）** | **Value Supersession with Tombstoning** | 同一件事有新值时，旧值不是删除而是"立墓碑"，检索按 id last-wins | 同 `(subject, relation)`、`object` 不同、旧条目 `status=active` → 落新条目前给旧条目追加**同 id 墓碑**（`status=superseded` + `supersededBy/At/Reason`），返回 `superseded[]`；检索侧 `potency(superseded)=0`，读取侧 last-wins | `engine/memory.py:295`（`apply_supersede`）；`docs/19:247`（v5.1 supersede 契约）；`docs/00:146`（术语表定义）；真实宿主证据 `acceptance/chaos/chaos-20261026-234400-host.json`（`mem_001 superseded`/`mem_002 active`/`supersede_events:1`） | §4.3、§5.3、§5.4 |
| **Impasse（僵局）子目标** | ⚠ 借用机制 **Impasse-Driven Subgoaling (Soar 概念)** | 元认知走到"无法定档/冲突未决"时，不硬答，而是生成一个澄清子目标 | `impasse(verdict) → subgoal 或 None`；与 `ask_clarify` 路径配合 | `engine/metacog.py:27`（`impasse`）；`docs/19:19`（§1 机制 7） | §4.6、§4.9 |
| **冻结方向** | **Deferred (Frozen) Research Directions** | 四个高阶方向被 H0 明确封存：不给原型、不加依赖、不加开关 | 行动闭环（工具调用）/ 自适应演化（元学习）/ 群体心智（多 Agent 黑板）/ 多模态感知；**解冻需可观测触发条件 + 新 ADR + H0 批准**；冻结期只允许补证据 | `decisions/010-四方向封存与解冻条件.md:30-35`（四方向与触发条件）；`:39-48`（解冻程序）；`交接总览.md:214-228`；`待办-冻结方向.md` | **仅** §2 尾句、§7.10、App. E（**不得进 Method**） |
| **合成数据上限** | **Synthetic Data Share Bound** | 合成数据能用来调参，但**不能用来判定上线** | `syntheticShareMax = 1.0`（v1 允许全合成；接真实数据后应降到 0.5）；超限时门禁拒绝用于上线判定（`LEARN-8`） | `rules/learning-params.yaml:57-62`（`replay` 块）；`docs/13:146-150`（合成数据的用途边界）；`docs/13:450`（`LEARN-8`） | §5.1、§7.3、App. D |
| **合成器的可追溯** | **Per-turn Injection Traceability** | 每一轮注入都要能离线复盘"为什么注入了这段" | 每轮落 `event`：`param_version` / `prompt_version` / `domains` / `dropped[]`（含 reason）/ `blocks` / `budget` / `injection_order` / `phase_result` / `anchor_applied` / `audit{verdict,ucas,downgrades}` / `fallback_applied` / `blocked`；`check_event` 校验必填字段 | `engine/pipeline.py:185-203`（事件构造）；`engine/pipeline.py:332-337`（`check_event`）；`docs/00:136`（每轮必须记 `param_version`/`prompt_version`，否则归因不成立）；`docs/22:245-325`（审计字段口径） | §4.9、§5.2、App. F |

---

## 1. 术语使用纪律（写正文时逐条执行）

1. **首次出现即定义 + 给落点**：正文每个术语第一次出现，必须用**本表的"学术正式术语(EN)"原文**，并在附录 A 给出 `file:line`。落点指不到 → 改用 `framing-only` 措辞或不写该术语。
2. **"借用"与"提出"必须分开**：ACT-R / STPA / Ashby / PAD / anti-windup / Soar Impasse 一律写 *we adopt / adapt*；本文自己命名的只有"控制面（control plane）"这一层的**组合方式**与失败语义，不得声称提出新机制。
3. **framing-only 三术语专用规则**（B 段）：只能出现在 Related Work / Discussion / 首次出现的一句定义里；**不得出现在 Method 子节标题、Figure 1 方框、Table 2 指标名**。
4. **数值必须带 provenance 词**：正文提到 0.70 / 0.85 / 0.02 / `w=0.6` / `Ki=0.15` 等任何一个数，必须同句出现 *guess* 或 *design default*，**禁止**与 "calibrated / tuned / optimal / validated" 同句。
5. **"机制可达"与"效果"分开写**：host 实验一律用 *mechanism reachable*；任何 *improves / reduces / prevents* 必须能指到真实对照数据，否则改成 *is designed to*。
6. **术语表与正文一一对应**：Table 1 的每一行必须来自本文件的 A/B/C 段；正文出现的机制术语若不在本表，**先补表再写正文**（防止正文越写越松）。

---

## 2. Table 1 的生成规则（正文用）

Table 1 列：`机制 | 学术正式术语 | 实现落点 | 证据类型 | 证据强度`。

| 证据类型 | 判定 | 强度 |
|---|---|---|
| `assertion` — 有测试断言直接覆盖 | 能指到 `tests/*.py` 或 `plugin/*-sim.mjs` 的断言项 | strong |
| `json field` — 有真实/确定性运行产物的字段 | 能指到 `acceptance/chaos/*.json` 字段路径 | medium（仅机制可达） |
| `code only` — 只有实现、无断言、无运行证据 | 只有 `engine/*.py` 行号 | weak（不得写进 Abstract） |
| `none` — 无落点（framing-only） | 见 B 段 | 明确写 "no code locus" |

---

## 3. 落点核验记录（本文件的每个 `file:line` 是怎么来的）

**逐行读过、行号可直接引用的文件**：
`README.md`、`交接总览.md`、`项目简介.md`、`docs/00-架构总览.md`、`docs/09-合成矩阵.md`（部分，经检索上下文）、
`docs/10-降级矩阵.md`（部分，经检索上下文）、`docs/13-学习机制.md`、`docs/16-实现状态.md`、`docs/19-控制论重构契约.md`、`docs/22-混沌工程.md`、
`decisions/008-控制论重构.md`、`decisions/010-四方向封存与解冻条件.md`、`decisions/011-相变阈值联动规则.md`、
`assumptions/已验证.md`、`assumptions/未验证.md`、
`engine/control.py`（全文）、`engine/actr.py`（全文）、`engine/pipeline.py`（全文）、`engine/memory.py`（部分，§3.1–§6 区段 + 归档门行）、
`engine/compose.py`（经检索定位常量与函数）、`engine/constitution.py`（经检索）、`engine/metacog.py`（经检索）、`engine/gray.py`（经检索）、
`rules/decay-params.yaml`（部分：`actr`/`control`/`compose` 块 + 头部 provenance 注释）、`rules/learning-params.yaml`（全文）、
`acceptance/shadow-baseline.md`、`acceptance/chaos/chaos-20261026-234400-host.json`、`acceptance/chaos/host-uca08-2026-10-26.md`、
`acceptance/chaos/chaos-20261026-234349-engine.json`（读取时控制台编码有损，但数值字段可辨）、
`research/00-外部项目参考.md`。

**未逐行读、因此本表不给行号的**（若正文需要引用，须先补读再补行号）：
`docs/02`、`docs/03`、`docs/04`、`docs/05`、`docs/06`、`docs/07`、`docs/08`、`docs/11`、`docs/12`、`docs/14`、`docs/15`、`docs/17`、`docs/18`、`docs/20`、`docs/21`、
`decisions/001`–`007`、`decisions/012`（仅经他处转述）、`plugin/dsh-lingshu/index.js` / `control.js` / `memorywriter.js` 全文、
`tools/*.py` 全文、`tests/*.py` 全文、`chaos-set/README.md`、`contracts/*.json`。

> **纪律**：上段文件里的事实（如 `memorywriter.applyWriteRequests` 的 supersede 默认值、`JAILBREAK_PATTERNS` 词表、
> `contracts/recall.schema.json` 字段全集）在本表中**只经 `docs/19` / `docs/22` 的转述引用并已标为转述**，
> 正文若要用这些细节，必须由实现者补一次逐行核对。

---

## 4. 术语 → 章节速查（写作时按此分配）

| 章节 | 必须出现的术语 | 禁止出现的术语 |
|---|---|---|
| Abstract | 控制面 / fail-closed 唯一归属 / UCA 前置闸 / precision evidence | VSM、rate-distortion、Cyber-Cognitive Envelope、四个冻结方向 |
| §1 Introduction | 五条不变量、四类失效、控制面 | 任何机制公式 |
| §2 Related Work | ACT-R、STPA/UCA、Ashby、PAD（均标 borrowed）、VSM（framing） | 未核实的引用与数字 |
| §3 Problem Formulation | 五域、预算、UCA 判定字段 | framing-only 三术语 |
| §4 Method | 全部 A/C 段机制术语 + 公式 + 落点 | VSM、rate-distortion（除 §4.3 一句类比）、Cyber-Cognitive Envelope、冻结方向 |
| §5 Experiments | 机制可达性、JSON 字段名、synthetic share | 任何效果性术语 |
| §6 Discussion | fail-open/fail-closed 张力、三副本、考卷冻结 | 收益断言 |
| §7 Limitations | provenance=guess、measured=0、synthetic share、冻结方向 | 路线图式承诺 |
| App. A–F | 全部术语（含 framing-only 的显式标注） | — |

---

*本文件与仓库代码/文档冲突时，**以仓库为准并把本表视为 bug**；补行号、改定义都必须先在仓库里复核一次。*
