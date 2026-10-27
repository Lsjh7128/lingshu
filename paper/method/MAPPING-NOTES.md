# Methodology 映射笔记：工程实现 → 学术表述

- **状态**：H0 评审门禁用稿（只做映射，不写正文，不跑实验，不改 `engine/`）
- **适用版本**：LINGSHU v5.1（引擎侧 Python 参考实现 `engine/`，插件侧 `plugin/dsh-lingshu/`）
- **证据基准日**：2026-10-27（本地代码状态；`tools/gen_impl_status.py --check` = OK）
- **写作铁律**
  1. 公式必须与代码逐字一致（含比较符、取整、截断、短路顺序）；每个公式下面给出**行级落点**与"代码里到底怎么写的"。
  2. 只写 v5.0/v5.1 已实现内容。`decisions/010-四方向封存与解冻条件.md` 的四个冻结方向（行动闭环 / 自适应演化 / 群体心智 / 多模态感知）**不得**出现在 Method 的"已实现"叙述里。
  3. 参数 provenance **全部是 `guess`，`measured = 0`**（`rules/decay-params.yaml:4`；`docs/15-验收清单.md:199-219`；`acceptance/provenance-candidates.json` 的 `upgrade_eligible` 全为 `false`）。**不得**把任何数值写成"已验证/已标定"。
  4. 引擎零模型（`engine/` 不调用 LLM）。凡涉及"模型行为"的效果（幻觉、人格漂移、真实越狱阻断）都**不可**在 Method 里声称已测量。

### 证据等级约定（本文每行都要带等级）

| 等级 | 含义 | 本次如何取得 |
|---|---|---|
| **E1** | 代码 + 单测 + 可复现实测数字 | 本次实际跑了测试（`159 / 65 / 26` 全绿） |
| **E2** | 代码 + 单测（无端到端实测数字） | 同上 |
| **E3** | 代码已实现但**未接线**到 `PipelineSession.step` | 静态调用图核查（grep 全仓引用） |
| **E4** | 纯解释性映射：**没有对应代码实体**（只有文档/分层表可作依据） | 只能进 Discussion/Related Work，不能进 Method 的"已实现机制" |
| **E5** | 仅文档声明，本次**未复跑** | 必须标注"未复跑" |

---

## 0. 本次复跑的证据锚点（可复核）

```
python tests/test_control.py                 → 159 passed, 0 failed
python tests/test_pipeline_integration.py    →  65 passed, 0 failed
python tests/test_constitution.py            →  26 passed, 0 failed
python tools/check_config_sync.py            →  66 ok, 0 fail, 0 skipped
python tools/gen_impl_status.py --check      →  OK（实现状态矩阵最新）
python tools/run_learning.py --json          →  recall@3=1.000，false_inject=0.0
```

- `test_control.py`：**141 个断言调用点**（`docs/16-实现状态.md:35` 由代码生成记为 141），循环展开后**执行 159 次**。逐机制执行数见第 12 节。
- `test_pipeline_integration.py`：**65 个断言调用点 = 65 次执行**（无循环内断言）。
- 复跑环境：`<PYTHON>`。
  **注意**：该套件在 Windows 默认 GBK 控制台下会因 `check()` 打印 U+2212（`−`）抛 `UnicodeEncodeError`（`tests/test_control.py:39`），需 `PYTHONIOENCODING=utf-8` 才能跑完——这不是断言失败，属环境问题，写论文"可复现命令"时必须带上该变量。

---

## 1. 总览表（九项机制）

| # | 学术名称（英文正式术语） | 工程原名 | 代码落点 | 学术框架 | 等级 |
|---|---|---|---|---|---|
| 1 | **VSM / System 1–5 mapping**（framing-only） | 八层架构 + 优先级表 | `docs/00-架构总览.md:9-20`（**无代码实体**） | VSM（Beer） | **E4** |
| 2 | **Integral Feedback with Anti-Windup** | 负反馈 + 积分控制（情感基线） | `engine/control.py:30-73` | control theory（PI/leaky integrator） | E2 |
| 3 | **Phase-Shift Preemptive Monitoring** | 相变监控 0.70 / 0.85 + 趋势判据 | `engine/control.py:78-123`；接线 `engine/pipeline.py:118-121,156-162` | control theory（feed-forward / early warning） | E1 |
| 4 | **Persona Anchor Injection** | U 型首尾人格锚点 | `engine/control.py:126-138`；`engine/pipeline.py:159-162` | 无既有理论名（prompt-boundary salience 工程假设） | E1 |
| 5 | **Requisite Variety Dimensionality Reduction** | 必要多样性降维 `RequisiteVarietyGuard` | `engine/control.py:143-170`；`engine/pipeline.py:147-154,239-251` | Ashby 必要多样性定律 | E3（引擎侧触发通道缺失，见 §6.2） |
| 6 | **Causal Interposition Safety Gate**（STPA-derived UCA） | STPA 前置拦截 `preflight_audit` | `engine/control.py:175-232`；`engine/pipeline.py:165-180,205-207` | STPA（Leveson） | E1（UCA-01…07）/ E2（UCA-08 降级留痕） |
| 7 | **Base-Level Activation (ACT-R)** + **Dual-Channel Potency** | ACT-R 双通道 potency | `engine/actr.py:30-66`；`engine/memory.py:39-64` | ACT-R（Anderson & Schooler 幂律） | E1 |
| 8 | **Global-Workspace Dynamic Weighting** | 动态权重 + 预算淘汰 | `engine/compose.py:134-150,180-205` | Global Workspace Theory（类比） | E1 |
| 9 | **Impasse-Driven Subgoaling**（Soar） | Impasse 子目标 | `engine/metacog.py:27-42` | Soar impasse resolution | **E3（未接线）** |
| — | **U-shaped Attention Anchoring** | U 型注入排序 `u_shape` | `engine/pipeline.py:43-61` | 无（位置偏置工程假设） | E1（详见 §5.2） |

> **英文术语以团队术语表 `paper/GLOSSARY.md` 为准**（见 §17 的对齐表）。本文括号里的描述性名称只用于解释，不得在正文另起新名；`⚠ framing-only` 与 `⚠ 借用机制` 的标注纪律见 `GLOSSARY.md §0`。

> **一句话结论**：能进 Method 的"已实现机制"= 2/3/4/6/7/8（+ U 型）；5 与 9 必须带"接线缺口"限定；1 只能进 Discussion（**没有代码**）。

---

## 2. VSM：System 1–5 分层 —— **只有解释性映射，没有实现**

### 2.1 事实核查（必须先写清，否则整段就是编造）

- 全仓检索 `VSM|Viable System|生存系统|System 1|System 5|variety engineering`：**0 命中**。
- `docs/19-控制论重构契约.md` 通篇**未使用 VSM 术语**，它列的是 7 个机制（表见 `docs/19:11-19`）。
- 唯一可支撑"分层"的实体是 `docs/00-架构总览.md:9-20` 的**八层优先级表**（序号越小优先级越高）+ `docs/00:68-83` 的"唯一 fail-closed 归属表"。
- 团队术语表已同结论：`paper/GLOSSARY.md` B 段"VSM / System 1–5 映射（framing-only）"，并要求"论文若用，必须写 *borrowed as an interpretive lens; not implemented*"。

**结论**：VSM System 1–5 是对**已存在的优先级分层与强制点**的事后学术读法（retrospective interpretation），**不是** LINGSHU 实现了一个 VSM。论文里只能写：

> "We *interpret* the layering … through the lens of the Viable System Model"，**不能**写 "LINGSHU implements VSM" 或 "System 1–5 are implemented"。

### 2.2 映射表（等级 E4，逐行标真实落点，禁止暗示有代码）

| VSM 功能 | 学术职能（Beer） | 本项目对应实体 | 真实落点 | 能说什么 / 不能说什么 |
|---|---|---|---|---|
| System 1 | 执行操作单元 | 五域注入产出的拼装器 | `engine/compose.py:51-131`（`compose()`）、`:157-177`（`_assemble`） | 能说"能对应操作层"；不能说"S1 有自治与资源谈判" |
| System 2 | 振荡抑制 / 冲突消解 | 8 条互斥表 + 未覆盖冲突的默认策略 | `engine/compose.py:24-34`（`MUTEX`）、`:43-48`（`lookup_mutex`）、`:79-112` | 能说"存在显式互斥裁决表"；不能说"实现了 S2 的振荡阻尼" |
| System 3 | 内部资源调度与优化 | 预算/名额控制（token 预算 + `topK` 名额 + 保序装填） | `engine/memory.py:117-274`（`recall`）、`:133`（`max_returned = retrieval.topK`）、`:232-259`（预算装填）；`engine/compose.py:180-205`（`_apply_budget`） | 能说"有预算化资源分配与降级"；不能说"有 S3 的内部经济/优化目标" |
| System 3* | 审计通道 | `dropped` / `audit.downgrades` / 事件留痕 | `engine/compose.py:60-61,198-199`；`engine/pipeline.py:185-203`；`engine/control.py:221-223` | 能说"有旁路审计留痕"；不能说"有独立审计子系统" |
| System 4 | 环境感知 + 前瞻 | 相变监控（前馈）+ 元认知定档 | `engine/control.py:78-123`（`PhaseShiftMonitor`）；`engine/metacog.py:45-89`（`verdict`） | 能说"有前馈式阈值监控"；不能说"有 S4 的环境建模/未来建模" |
| System 5 | 身份 / 政策 | 宪法层：审计器 + 冻结区自校验 + UCA 表 | `engine/constitution.py:75-117`（`audit`）、`:144-177`（`FrozenGuard`）；`engine/control.py:175-184`（`UCA` 表） | 能说"有不可协商的政策层与 fail-closed"；不能说"有 S5 的组织身份递归" |

### 2.3 当前不能声称什么（VSM 项）

- 不能声称实现了 VSM 的**递归结构**（S1 内部再嵌套 S1–S5）——代码里没有任何递归组织。
- 不能声称 VSM 的**多样性平衡**被度量——没有 variety 计量。
- 不能把"八层"说成 VSM 五系统（层数都不同：8 层 vs 5 系统）；映射是**多对一**的，且丢掉 S2/S3* 的独立通道语义。
- 不能用本映射做**有效性证据**（它不产生任何可测预测）。

---

## 3. 负反馈 + 积分控制（含 anti-windup）

- **代码**：`engine/control.py:30-73`（类 `IntegralBaseline`；`step()` 在 `:47-68`，`apply()` 在 `:70-73`）
- **参数**：`rules/decay-params.yaml:223-233` → `control.integralKi=0.15` / `integralCap=0.4` / `integralLeak=0.05` / `integralDeadband=0.02`，块末 `provenance: guess`（`:233`）；代码影子 `engine/config.py:96-107`（值逐项相同）
- **测试**：`tests/test_control.py:177-256` → `test_integral_baseline`，**18 断言执行（0 failed）**
- **等级**：E2

### 3.1 精确语义（按代码顺序，短路顺序不可改写）

对 $k\in\{\text{valence},\text{arousal},\text{dominance}\}$，$\Delta t=\max(0,\text{dt\_minutes})$（默认 `dt_minutes=1.0`，`control.py:47,49`）：

$$
e_k = -\,\delta_k \qquad(\texttt{control.py:51})
$$

$$
I_k \leftarrow
\begin{cases}
I_k\,e^{-\lambda \Delta t}, & |e_k|<\varepsilon \quad(\texttt{:52-55})\\[2pt]
I_k\,e^{-\lambda \Delta t}, & e_k<0 \quad(\texttt{:56-59})\\[2pt]
I_k\,e^{-\lambda \Delta t}, & s_k=\text{true} \quad(\texttt{:60-62})\\[2pt]
\operatorname{sat}\!\big(I_k + K_i\,e_k\,\Delta t\big), & \text{otherwise}\quad(\texttt{:63-67})
\end{cases}
$$

其中

$$
\operatorname{sat}(x)=\operatorname{sign}(x)\cdot C\ \ \text{if}\ |x|\ge C\ \text{(and then } s_k\leftarrow\text{true}\text{)};\qquad
\operatorname{sat}(x)=x\ \ \text{otherwise}
$$

返回 $(\text{baseline}=I,\ \text{saturated}=s)$（`:68`）。$K_i=0.15$，$C=0.4$，$\lambda=0.05$，$\varepsilon=0.02$。

**必须写进 Method 的四个实现细节（也解释了为什么它不是教科书 PI）**

1. **死区是"只泄放"而不是"保持"**：$|e_k|<\varepsilon$ 时 $I_k$ 仍乘 $e^{-\lambda\Delta t}$（`:54`），所以静默期基线会自然衰减——这是 leaky integrator，不是带死区的理想积分器。
2. **只有负向偏离才积分**：$e_k<0$ 分支（`:56-59`）明确"不推高基线，只泄放历史积分"。因此该控制器是**单侧积分**（one-sided / rectified integrator），不能写成 $\int e\,dt$。
3. **抗饱和是"锁存 + 泄放"**：到帽后 $s_k$ 置真且**代码里没有任何复位路径**（`saturated` 只在 `:66` 置真；`step` 其余分支不复位）。也就是说饱和是 **latch**，恢复只能靠新建实例或让 $I_k$ 泄放到帽以下（但 $s_k$ 仍为真 → 仍然不积分，只泄放）。这与"饱和后可重新积分"的教科书 anti-windup **不同**，写 Method 时必须照实说成 "latching saturation flag that suppresses further integration indefinitely"。
4. **顺序敏感**：死区判断先于符号判断；若 $\varepsilon$ 被设为 0，则 $|e_k|<0$ 恒假，行为才会退化为"负向积分"。调参/消融时这是可观测的行为断点。

### 3.2 学术框架归属

- 经典 **discrete-time integral control / leaky integrator**，附加 **deadband** 与 **anti-windup clamp**。
- 可以被描述为"PI 控制器的 I 支路 + 单侧修正"，但**没有 P、没有 D、没有被控对象模型、没有稳定性分析**（增益 $K_i$ 是 `guess`），所以**不能**声称"闭环稳定性"或"收敛保证"。

### 3.3 当前不能声称什么

- 不能声称"积分控制改善了长期共情/用户满意度"——引擎侧没有任何情绪效果测量；`docs/22:8.5` 明确真实样本不足。
- 不能声称时间常数/增益经过标定：`provenance: guess`，`measured=0`。
- 不能把 $C=0.4$ 描述为"理论界"；它是工程护栏（`docs/15:206-217` 归入 `guess`）。
- 不能声称该机制在插件侧等价（插件 `control.js#IntegralBaseline` 是**同语义移植**，数值三副本，见 `docs/19:207-222`；插件证据是 mock 宿主 381 断言，**不是**真实会话效果）。

---

## 4. 相变监控（前馈 + 双阈值 0.70/0.85 + 趋势判据）

- **代码**：`engine/control.py:78-123`（类 `PhaseShiftMonitor`；`observe()` 在 `:96-123`）；接线 `engine/pipeline.py:118-121`（仅调用方同时给 `context_used_tokens` 与 `context_window_tokens` 才观测）
- **参数**：`rules/decay-params.yaml:223-233` → `control.phaseShiftRatio=0.7`、`phaseShiftHardRatio=0.85`、`trendThreshold=0.02`、`ratioMax=1.5`、`historyCapacity=64`，`provenance: guess`；代码影子 `engine/config.py:101-106`
- **测试**：
  - `tests/test_control.py:258-340` → `test_phase_shift_monitor`，**22 断言**
  - `tests/test_control.py:330-337`（同文件内 `test_phase_shift_monitor` 的锚点段）→ 首尾锚点 5 断言已含在 22 内
  - `tests/test_control.py:623-666` → `test_params_single_source`，**8 断言（其中 6 条属相变参数单一来源）**
  - `tests/test_pipeline_integration.py:190-224` → `test_phase_anchor`，**9 断言**
- **等级**：E1

### 4.1 精确语义

设观测输入 $(u,W)$（已用用量 / 窗口用量）：

$$
r=\begin{cases}
0.0, & W\le 0\quad(\texttt{control.py:97-98，且**不入历史**、phase="unknown"、无 prefeed})\\[2pt]
\min\!\big(r_{\max},\ \max(0,\ u/W)\big), & W>0\quad(\texttt{:99})
\end{cases}
\qquad r_{\max}=1.5
$$

历史 $H\leftarrow\big(H\,\Vert\,[r]\big)[-64:]$（`:100-102`，容量 `control.historyCapacity`）。

相位（**先判 hard，再判 shift**，`:103-109`，注释明确说明这是 v4.0 修的 bug："否则 `critical` 永远不可达"）：

$$
\text{phase}=
\begin{cases}
\texttt{critical}, & r\ge 0.85\\
\texttt{shift}, & 0.70\le r<0.85\\
\texttt{normal}, & r<0.70
\end{cases}
$$

趋势（`:111-113`，需要 $|H|\ge3$，否则 0）：

$$
\tau_t=\frac{r_t-r_{t-2}}{2}\quad(\text{即 }(\texttt{history[-1]}-\texttt{history[-3]})/2)
$$

输出（`:114-123`）：

$$
\begin{aligned}
\text{prefeed}&=\text{phase}\in\{\texttt{shift},\texttt{critical}\}\\
\text{self\_reference\_boost}&=\text{phase}\neq\texttt{normal}\\
\text{anchor\_required}&=(\text{phase}=\texttt{critical})\ \lor\ \big(\text{phase}=\texttt{shift}\ \land\ \tau_t>0.02\big)
\end{aligned}
$$

返回时 `ratio` 与 `trend` 被 `round(...,4)`（`:115-117`），**历史里存的是未取整的原始值**——做边界样本分析时必须区分这两个口径。

### 4.2 落点与触发几何（论文里值得写的一句）

- `prefeed` 与 `anchor_required` 不是独立信号：`anchor_required ⇒ prefeed`（因为 critical/shift 都属于 prefeed）。因此 `apply_persona_anchor` 的条件 `not prefeed and not anchor_required`（`:133`）实际等价于 `not prefeed`。**anchor_required 是"是否强制"的说明性子标志，不是第二条触发路径**——不能写成"两个独立触发条件"。
- 严格不等式：趋势触发用 $>$（`:122`），阈值触发用 $\ge$（`:104,106`）。$r=0.70$ 恰好触发 `shift`；$r=0.85$ 恰好触发 `critical`（测试钉住：`tests/test_control.py:276,283`）。
- 窗口非法（$W\le0$）时**不写历史**，所以紧接着的 trend 不会被污染（测试钉住："负 window 同样安全"，`test_control.py:325`）。**注意返回字典不同构**：$W\le0$ 时只有 `{ratio, phase, prefeed}` 三个键（`control.py:97-98`），**没有** `trend` / `self_reference_boost` / `anchor_required`；调用方必须用 `.get()`（`pipeline.py:159` 与 `control.py:133` 正是这么做的）。写 Method 时不要把这三种返回写成同一 schema。

### 4.3 当前不能声称什么

- 不能声称"0.70/0.85 是经过标定的最优阈值"——两者都是 `guess`；真实标定规则见 `decisions/011-相变阈值联动规则.md`，且该 ADR 明确"真实配置未改（仍 0.70/0.85，provenance=guess）"，宿主证据远低于门槛（`docs/22:441-455`）。
- 不能声称"相变监控防止了长上下文性能退化"——引擎**没有模型**，`ratio` 只是字符估算（`engine/pipeline.py:118-121`；A5 的 engine 模式同样用累计字符，见 `tools/run_chaos.py:64` 的 `ENGINE_CONTEXT_WINDOW=30000` 注释），`docs/22:268` 明确 engine 模式 `degraded_at_ratio` **必须为 null`**。
- 不能声称触发阈值与真实 tokenizer 一致：引擎侧没有 tokenizer，只有 `len(text)/3` 之类的折算（`engine/compose.py:39-40`；插件侧 `charsPerToken` 估算见 `docs/19:211-212`）。
- 不能把 `round(,4)` 后的 ratio 当成精确值做分布统计。

---

## 5. U 型首尾人格锚点（`apply_persona_anchor`）

- **代码**：`engine/control.py:126-138`（`ANCHOR_PREFIX`/`ANCHOR_SUFFIX` 在 `:126-127`）；接线 `engine/pipeline.py:159-162`
- **参数**：**无 `rules/` 参数**——锚点文案与"首尾夹持"行为是硬编码字面量（`:126-127`、默认 body 在 `:135`）。这一点必须诚实：不是所有数值都在 `rules/*.yaml`。
- **测试**：`tests/test_control.py:330-337`（3 断言：未触发不改 prompt / 触发后首尾都有锚点 / 不吞掉原 prompt / 自定义 body 生效——共 4 条中的 3 条 + 自定义 1 条，含在 22 内）；`tests/test_pipeline_integration.py:190-224`（9 断言，含"锚点未改动拼装块顺序/块数"与"补锚点后 UCA-07 不命中"）
- **等级**：E1

### 5.1 精确语义

$$
\text{anchor}(p)=
\begin{cases}
p, & \lnot\text{prefeed}\land\lnot\text{anchor\_required}\quad(\texttt{control.py:133-134})\\[2pt]
P\ \Vert\ \texttt{"\textbackslash n"}\ \Vert\ p\ \Vert\ \texttt{"\textbackslash n"}\ \Vert\ S, & \text{otherwise}\quad(\texttt{:135-138})
\end{cases}
$$

其中 $P=$ `ANCHOR_PREFIX` + body，$S=$ `ANCHOR_SUFFIX`；默认 body 为 `"记忆不等于当前陈述；情感不参与事实；关系不软化拒绝。"`（`:135`）。

### 5.2 与"U 型注入排序"必须分开写（两个不同机制）

论文草稿最易犯的错误是把这两件事混成"U 型"：

| 机制 | 做什么 | 代码 | 是否影响检索顺序 |
|---|---|---|---|
| **U 型注入排序** | 把已按分数降序的 recall 结果**重排注入次序**：高分首、次高分尾、最低居中 | `engine/pipeline.py:43-61`（`u_shape`）；接线 `:102,131,196` | **不影响** `memory.recall` 的 `returned` 顺序（测试钉住：`test_pipeline_integration.py:171-179`） |
| **首尾人格锚点** | 在渲染文本**首尾**各插一段人格约束 | `engine/control.py:126-138`；接线 `pipeline.py:159-162` | 不涉及检索 |

`u_shape` 的精确置换（$n>2$；$n\le2$ 原序，`:57-58`）：

$$
\text{out}=[r_0,r_2,r_4,\dots]\ \Vert\ \big([r_1,r_3,\dots]\big)^{\text{rev}}
$$

例：$n=3\Rightarrow[r_0,r_2,r_1]$；$n=4\Rightarrow[r_0,r_2,r_3,r_1]$；$n=5\Rightarrow[r_0,r_2,r_4,r_3,r_1]$（测试钉住：`test_pipeline_integration.py:144-152`）。
**U 型的理论依据只是工程假设**（"模型对 prompt 首尾利用更好"），`engine/pipeline.py:49-50` 的自述是理由陈述，**没有引用实验**，也没有任何 LINGSHU 自己的证据。论文里必须写成"motivated by the well-known position bias of LLMs"并引文献，而**不能**声称本项目验证过。

### 5.3 当前不能声称什么

- 不能声称锚点"减少了人格漂移"——引擎侧没有模型输出，无漂移度量；`docs/22:441-455` 明确"v5.1 的 host 证据只到机制可达"。
- 不能声称锚点是 U 型包裹的严格实现：插件侧只能通过 `systemPrompt.section` 的 order 90/140 注入**两段**，`docs/19:209-210` 已诚实记录"语义近似但不等价于 engine 的 head+prompt+tail"。
- 不能把"U 型排序"说成有实证支持的最优排列——$n\le2$ 退化为原序，且排序只作用于注入侧。

---

## 6. 必要多样性降维（`RequisiteVarietyGuard`）

- **代码**：`engine/control.py:143-170`（`STYLE_DOMAINS` 在 `:143`，类在 `:146`，`check()` 在 `:157-170`）；接线 `engine/pipeline.py:147-154`（判定）、`:239-251`（`_apply_variety_fallback` 实际丢域）
- **参数**：无 `rules/` 参数；唯一"参数"是硬编码的 `STYLE_DOMAINS=("style",)` 与固定 `marker`/`action` 字符串（`:143,154,167-169`）
- **测试**：`tests/test_control.py:342-385` → `test_requisite_variety_guard`，**11 断言**
- **等级**：**E3**（实现 + 单测齐全，但引擎侧**没有自动冲突判定通道**，见 6.2）

### 6.1 精确语义

$$
\text{fallback}=\mathbb{1}\big[\text{conflict}=\texttt{"unresolved"}\ \land\ \lnot\text{matched\_mutex}\big]\quad(\texttt{control.py:160})
$$

若 `fallback=false` → 返回 `{"fallback": false, "reason": null}`（`:161`），不写事件。
若 `fallback=true`：

$$
\text{drop\_domains}=[d\in\{\texttt{"style"}\}\mid d\in\operatorname{dom}(D)]\quad(\texttt{:162})
$$

并追加事件 `{marker, dropped}`（`:163`）、返回固定 `reason="unresolved_conflict_without_mutex_rule"`、`action="degrade_to_fact_and_clarify"`（`:164-170`）。注意：**即使 `style` 不在 $D$ 里，`fallback` 仍为 true、仍写事件**（测试钉住："无风格域时仍标记降维但无域可丢"，`test_control.py:371-374`）——所以"fallback=true"不等于"真的丢了域"。

### 6.2 接线缺口（**H0 门禁必须写进 Method 的局限段**）

1. `PipelineSession.step` 里 `conflict` 来自 `verdict["conflict"]`（`engine/pipeline.py:148`），而 `metacog.verdict()` 在 `:105` **没有传 `conflict` 实参**（签名默认 `conflict="none"`，`engine/metacog.py:45-47`）。⇒ **引擎流水线里 `conflict` 恒为 `"none"`，守卫永远不会触发**。`engine/pipeline.py:145-146` 的注释也自认"正常路径 `conflict="none"`，守卫返回 fallback=False"。
2. `matched_mutex` 在流水线里被**写死为 `False`**（`engine/pipeline.py:150`，注释：单条 conflict 标签无法证明被互斥规则覆盖，防御性按"未覆盖"处理）。
3. 结论：**引擎只能在"人工/外部喂 conflict"的单元级路径上验证该机制**。已有先例：`tools/run_chaos.py:463-474` 的 `engine_variety()` 直接调 `guard.check(conflict="unresolved", ...)`；`docs/22:113-133` 也要求 S4 由外部构造。
4. 真实冲突判定在**插件侧**（v5.1，`index.js#detectConflicts`，`docs/19:248`），那是规则桩、不是引擎能力。**不得**把插件能力写成引擎能力。

### 6.3 当前不能声称什么

- 不能声称"引擎实现了未定义冲突检测"——引擎没有检测器，只有降维动作。
- 不能声称降维"提升了回答质量/安全性"——引擎无模型，`docs/22:304` 只要求 `verdict=allow`/`blocked=false`（fail-open）与渲染证据（style 段缺席、事实段保留）。
- 不能声称该机制实现了 Ashby 的多样性定律：**没有 variety 计量、没有能力-环境多样性比较**，只做了一个"能力不足时单域收缩"的定性动作。论文里应写 "inspired by Ashby's law of requisite variety; it operationalizes only a one-domain attenuation rule"。
- 不能声称"UCA-06 在真实流量里被触发过"——引擎回放 `blocked_turns=0`（`engine/pipeline.py:327` 汇总字段；`tests/test_pipeline_integration.py:55` 钉住）。

---

## 7. STPA 前置拦截：UCA-01…08（含 UCA-08 只降级不熔断）

- **代码**：`engine/control.py:175-184`（`UCA` 表）、`:186-188`（两条词表）、`:191-232`（`preflight_audit`）；接线 `engine/pipeline.py:165-180`（调用）、`:181,205-207`（fail-closed 输出）
- **参数**：`UCA` 表与 `_OVERRIDE_PATTERNS` / `_UNSOURCED_FACT` 词表是**硬编码**（`:175-188`），`rules/` 里**没有对应 key**；`fail_mode` 是常量字符串 `"closed"`（`:229`）
- **测试**：
  - `tests/test_control.py:387-482` → `test_preflight_audit`，**33 断言**（逐条 UCA-01…07 + 多命中顺序 + UCA-08 六条）
  - `tests/test_pipeline_integration.py:227-284` → `test_preflight_gate`，**15 断言**（正常 allow / `frozen_ok=False` 熔断 / `prompt=None` / 熔断文本不含注入块 / UCA-08 不熔断但留痕）
- **等级**：E1（UCA-01…07 与 UCA-08 留痕）/ E2（UCA-08 的"抑制"发生在 `IntegralBaseline`，闸门只看事实）

### 7.1 精确判定（逐条，顺序即代码顺序，`hits` 是 append 序）

$$
\begin{aligned}
h_1&=\exists w\in W_{\text{override}}:\ w\subseteq p &&(\texttt{:207-208})\\
h_2&=\lnot\text{frozen\_ok} &&(\texttt{:209-210})\\
h_3&=\text{refuse}\land\text{style\_injected} &&(\texttt{:211-212})\\
h_4&=\exists w\in W_{\text{unsourced}}:\ w\subseteq \text{fact\_block} &&(\texttt{:213-214})\\
h_5&=\lnot\text{constitution\_injected} &&(\texttt{:215-216})\\
h_6&=(\text{conflict}=\texttt{"unresolved"})\land\lnot\text{fallback\_applied} &&(\texttt{:217-218})\\
h_7&=\text{phase\_result.prefeed}\land \texttt{"〖人格锚点"}\not\subseteq p &&(\texttt{:219-220})
\end{aligned}
$$

$$
\text{verdict}=\begin{cases}\texttt{"block"},&\exists i\in[1,7]:h_i\\ \texttt{"allow"},&\text{otherwise}\end{cases}\quad(\texttt{:226})
$$

UCA-08（**独立于 verdict**）：

$$
\text{downgrades}=\begin{cases}[\texttt{"UCA-08"}],&\exists k:\ s_k=\text{true}\\ [\,],&\text{otherwise}\end{cases}\quad(\texttt{:221-223})
$$

返回值另含 `fail_mode="closed"`（常量）、`explain=[\text{UCA}[h]\ \text{for}\ h\in hits]`、`explain_downgrades`（`:225-232`）。

词表（逐字，`:186-188`）：
- $W_{\text{override}}$ = {`忽略宪法`, `放宽拒绝`, `不再需要遵守`, `取消安全约束`, `ignore the constitution`, `bypass safety`}
- $W_{\text{unsourced}}$ = {`依据我认为`, `我记得你说过，所以`, `根据我的记忆，这是事实`}

### 7.2 必须写进 Method 的五个诚实点

1. **fail-closed 只对"注入"生效**：`block` 时 `prompt=None`、只返回 `BLOCKED_TEXT`（`engine/pipeline.py:37-38,205-207`）。它拦的是"本轮不给可注入 prompt"，**不是**"阻止用户消息到达模型"。
2. **UCA-01 在引擎里只扫拼装后的 prompt**：而引擎拼装的块是**固定模板**（`[constitution] 原则 1-5`、`[fact] 置信度=…`、`[style] 标签=…`，`compose.py:157-177`），**用户原文不进入 prompt**。⇒ 引擎侧 UCA-01 对"用户越狱"**几乎不可能触发**；用户消息扫描是**插件 v4.3** 的能力（`preflightAudit(prompt, userText)` + `JAILBREAK_PATTERNS`，`docs/19:226-230`），且插件自己也明确"无法把用户消息从宿主 payload 移除"（`docs/19:229-230`）。论文里**不能**把插件用户消息扫描写成引擎能力。
3. **UCA-08 不是熔断**：它只把"饱和事实"记进 `downgrades`（`control.py:221-223`），`verdict` 不变；真正的"抑制继续积分"发生在 `IntegralBaseline` 的 `saturated` latch（§3）。测试双钉：`test_control.py:449-466`、`test_pipeline_integration.py:271-284`。
4. **`fail_mode="closed"` 是标签不是开关**：没有任何分支会返回 `"open"`；"其余 fail-open" 的语义在别处（`docs/00:68-82` 的降级表），不在这个函数里。不能写成"该函数实现了 fail-closed/fail-open 双模"。
5. **UCA 表是"检查清单"，不是完整 STPA**：STPA 要求控制结构建模 + 因果情景分析（causal scenarios）；这里只有 UCA 枚举 + 词表/布尔判定，没有控制结构模型、没有 hazard、没有 causal factor。论文必须写 "STPA-informed checklist" 或 "UCA-based pre-flight gate"，**不能**写 "we performed STPA"。

### 7.3 当前不能声称什么

- 不能声称"越狱拦截成功率 100%（STPA 100% 有效）"：`docs/22:445` 明确"当前**不能**声明 STPA 100% 有效"，真实红队样本 < `docs/20 §4.2` 的 ≥20 次门槛；且 `chaos-set/jailbreak_variants.jsonl` 是**为命中词表而构造**的（`docs/22:96-112`），24/24 条全命中只证明"词表覆盖自己造的样本"（本次复核：24/24 命中 UCA-01，属**构造性饱和**，不是鲁棒性证据）。
- 不能声称"零误杀"：本次在 200 条回放用户消息上以单元级方式跑 `preflight_audit(prompt=user_message)` 得到 **0 误判**，但这是**单元级**结论（把用户文本直接喂给闸门扫描面），不是端到端误杀率；宿主真实误杀率未测。
- 不能声称 UCA-03 在插件侧可达：插件 `refuse` 恒 `false`（`docs/19:213`），UCA-03 不会命中。
- 不能声称这些词表经过对抗评估：它们没有任何覆盖率/误报率测量记录。

---

## 8. ACT-R base-level 双通道

- **代码**：`engine/actr.py:30-41`（`base_level`）、`:44-50`（`normalized`）、`:53-59`（`activation`）、`:62-66`（`potency`）、`:69-77`（`touch`）；双通道在 `engine/memory.py:39-64`（`potency`）
- **参数**：`rules/decay-params.yaml:216-221` → `actr.d=0.5`、`ref=0.001`、`lo=-3.0`、`hi=1.0`，`provenance: guess`；`memory.actrWeight=0.6`（`:18`，该行**无行内 provenance**，依据文件头约定 `:4` 与 `actr` 块 provenance）；另需 `memory.decay.tau.{episodic,semantic,procedural}=7/30/90d`、`memory.decay.importanceScaling=true`（`:38-48`，`provenance: guess`）。代码影子 `engine/config.py:15-22,90-95`
- **测试**：
  - `tests/test_control.py:58-99` → `test_actr_activation`，**15 断言**
  - `tests/test_control.py:103-139` → `test_actr_dual_channel`，**7 断言**（含**直接跑 `replay/score` 实测 `recall@3=1.000`**、200 轮全参与、零泄漏）
- **等级**：E1

### 8.1 精确公式（与代码逐字）

单次激活（`actr.py:30-41`，$t$ 以**天**为单位，只取最近 64 条轨迹）：

$$
B_i=\ln\!\Big(\sum_{j\in \text{last }64} \big(t_{\text{now}}-t_j+\rho\big)^{-d}\Big),\qquad
d=0.5,\ \rho=0.001
$$

代码细节（论文脚注建议保留）：
- 若 $t_{\text{now}}-t_j+\rho\le 0$ 则该项用 $\rho$ 代替（`actr.py:35-37`）；
- 轨迹为空 → $B_i=-\infty$（`:39-41`）；
- 无 `access_times` 的老条目退化为"仅 `last_access` 一条轨迹"（`:53-59`）。

归一化（`actr.py:44-50`）：

$$
n(B)=\begin{cases}0,&B=-\infty\ \lor\ h\le l\\ \operatorname{clip}\!\big((B-l)/(h-l),0,1\big),&\text{otherwise}\end{cases}
\qquad (l,h)=(-3.0,\ 1.0)
$$

ACT-R 侧 potency（`actr.py:62-66`）：

$$
p^{\text{actr}}_i=\operatorname{clip}\big(\text{imp}_i\cdot n(B_i),0,1\big)
$$

双通道（`memory.py:39-64`）：

$$
\Delta t_i=\max\!\Big(0,\frac{t_{\text{now}}-t^{\text{last}}_i}{86400}\Big),\quad
\tau^{\text{eff}}_i=\tau_{\text{type}(i)}\cdot(0.5+\text{imp}_i)
$$

$$
p_i=\operatorname{clip}\Big(\max\big(\underbrace{\text{imp}_i e^{-\Delta t_i/\tau^{\text{eff}}_i}}_{\text{decay channel}},\ \underbrace{w\cdot p^{\text{actr}}_i}_{\text{ACT-R channel}}\big),0,1\Big),\qquad w=0.6
$$

**三个必须在 Method 里说清的非标准点**

1. $w$ 乘在 **已经乘过 importance 且已 clip 的** $p^{\text{actr}}$ 上（`memory.py:63`），所以 ACT-R 通道上限是 $0.6$，而 decay 通道上限是 $\text{imp}$。二者**不同量纲**，`max` 是工程折中，**不是** ACT-R 理论里的激活比较。
2. `importanceScaling` 使 $\tau^{\text{eff}}=\tau_{\text{type}}(0.5+\text{imp})$，$\text{imp}=1$ 时是 1.5×（不是 2×）——`docs/15:27` 有专门验收项。
3. 双通道的选择有**工程实测依据**（不是理论依据）：`docs/19:31-33` 记录"纯 ACT-R 会把冻结回放集 `recall@3` 从 1.000 打到 0.556"，因此改为取大（`docs/19:36-44`）。这是**项目内部回放集数字**，应在 Method 里如实标注为 "on our frozen synthetic replay set (200 turns, 10 scenario families)"，不能写成通用结论。

`touch`/访问轨迹写入（`memory.py:67-70` 调 `actr.touch`，`actr.py:69-77`）默认**不**在召回时触发：`memory.recall` 受 `touchOnRecall` 开关控制，默认 `False`（`config.py:21`；`memory.py:135,255-256`）。⇒ 回放期间轨迹不变，这是"冻结基线不变"的必要条件，也是**真实使用中 actr 通道会漂移**的风险点（消融设计必须钉住）。

### 8.2 当前不能声称什么

- 不能声称"实现了 ACT-R 认知架构"：只有 base-level 激活方程，**没有** production rules、buffers、utility learning、partial matching、spreading activation、retrieval latency。
- 不能声称 `d=0.5 / REF=0.001 / lo/hi` 有文献标定：`ref/lo/hi` 是工程截断参数（`actr.py:24-26` 注释自认"经验标定"），provenance 仍是 `guess`。
- 不能声称双通道 `max` 有理论保证；它是"救援"（rescue），`docs/19:44` 明确"`w` 是救援力度，不是替代程度"。
- 不能声称 ACT-R 通道提升了真实使用中的召回质量：`recall@3=1.000` 是**合成回放集**上的**天花板**数字（10 个族全 1.000，本次复核；见 `ablation/DESIGN.md` §4 的天花板证据），对真实流量没有外推力。

---

## 9. 动态权重（全局工作空间）

- **代码**：`engine/compose.py:134-150`（`_compute_weights`）；消费者 `:180-205`（`_apply_budget` 按权重升序淘汰）；可观测字段 `:127`（`weights` 进结果）、`:198-199`（`dropped` 带 `weight`）
- **参数**：`rules/decay-params.yaml:238-247` → `compose.weights.fact=0.70` / `organization=0.50` / `style=0.40` / `retrieval=0.45` / `boostFact=0.50` / `boostOrganization=0.35` / `boostStyle=0.40` / `styleBoostThreshold=0.6`，`provenance: guess`；代码影子 `engine/config.py:49-59`
- **测试**：`tests/test_control.py:484-621` → `test_compose_weights`，**29 断言**；`tests/test_control.py:623-666` → `test_params_single_source`，其中**2 条**属 `styleBoostThreshold` 单一来源
- **等级**：E1

### 9.1 精确公式

$$
\begin{aligned}
w_{\text{fact}}&=0.70+0.50\cdot\mathbb{1}[\text{refuse\_claim}]\\
w_{\text{org}}&=0.50+0.35\cdot\mathbb{1}[\text{goal\_blocked}]\\
w_{\text{style}}&=0.40+0.40\cdot\mathbb{1}[\text{intensity}\ge 0.6]\\
w_{\text{retrieval}}&=0.45\quad(\text{常量，无 boost})
\end{aligned}
$$

其中 `refuse_claim = (metacog_level == "unknown") or (expression == "refuse_claim")`（`compose.py:70`）、`goal_blocked = (organization.goal_status == "blocked")`（`:71`）、`intensity = style.emotion_intensity`（`:147`），返回前统一 `round(,3)`（`:150`）。

淘汰（`_apply_budget`，`:180-205`）：预算 $= \lfloor 8000\times0.15\rfloor = 1200$（`:36,182-183`）；用量 $=\sum_b \max(1,\lceil |b|/3\rceil)$（`:39-40,184`）；当用量超预算时，反复取

$$
b^\star=\arg\min_{b\in \mathcal{S}}\big(w_{\text{domain}(b)},\, \text{index}(b)\big)
$$

（`:189-199`，`min` 作用于 `(weight, index)` 元组 → 先比权重、权重相同取更靠前块），弹出并记 `{plugin, reason:"budget_exceeded", weight}`。$\mathcal{S}$ 只含 `[style]`/`[organization]`/`[retrieval]` 三类软块（`:186-187`）。

**必须写进 Method 的三个真相**

1. **`[retrieval]` 块在 `_assemble` 里根本不生成**（`compose.py:157-177` 只产 `[constitution]/[fact]/[safety]/[organization]/[style]/[user]`）。⇒ `w_retrieval` 实际不影响任何淘汰决策。它是"预留权重"，不是有效机制。
2. **正常回放预算根本不会超**：`recall@3` 基线下 `budget_utilization=0.137983`（本次实测），块数分布 `{3:20, 4:180}`（`CHANGELOG.md:429` v4.0 记录，与改前一致），**没有一轮触发淘汰**；`tests/test_pipeline_integration.py:53-54` 钉住 `truncated_turns=0`、`dropped_reasons={}`。⇒ 动态权重在引擎回放里是**不可观测**的，只能靠单测构造超预算场景验证。
3. **权重不能推翻互斥表**：拒绝场景先按 `:79-88` 丢 style/organization/retrieval，再算权重；测试钉住"权重已 boost 到最高仍不能复活风格域"（`test_control.py:606-610`）。拼装顺序 `ASSEMBLY_ORDER` 是黑名单项（`compose.py:19`；`docs/19:90-94`）。

### 9.2 当前不能声称什么

- 不能声称"全局工作空间理论被实现"：这里只是**加权竞争 + 顺序淘汰**，没有广播、没有 ignition、没有竞争性抑制；`docs/19:§2.4` 自己只用"全局工作空间"作比喻。GWT 只能进 Discussion 的类比段。
- 不能声称权重"动态分配了注意力并提升回答质量"：引擎无模型，且回放里淘汰从未发生。
- 不能声称权重经过标定（全 `guess`），也不能声称 `styleBoostThreshold=0.6` 与 `metacog.emotionCapIntensity=0.6` 是同源（见 §11 的单一来源例外）。

---

## 10. Impasse 子目标

- **代码**：`engine/metacog.py:27-42`
- **参数**：无（三个字面量返回值）
- **测试**：`tests/test_control.py:668-691` → `test_impasse`，**7 断言**
- **等级**：**E3——已实现、已单测，但未接线**

> **术语归属修正（必须写进论文）**：`engine/metacog.py:30-31` 的注释说 "Impasse 是 ACT-R 里的标准控制动作"，**这句归属不准确**：impasse → 子目标的机制出自 **Soar**（Newell），不是 ACT-R。团队术语表 `paper/GLOSSARY.md` C 段已正确标为 "Impasse-Driven Subgoaling (Soar 概念) ⚠ 借用机制"。正文按 Soar 归属，`file:line` 照引；**不要**照抄代码注释的 ACT-R 归属。

### 10.1 精确语义

$$
\text{impasse}(v)=
\begin{cases}
\{\text{type}:\texttt{impasse},\text{reason}:\texttt{no\_record},\text{request\_to}:\texttt{goal},\text{subgoal}:\text{"向用户确认关键事实"},\text{question}:\text{"这条我需要你确认一下："},\text{block\_execution}:\text{true}\},&\text{level}=\texttt{"unknown"}\quad(\texttt{:33-37})\\[2pt]
\{\text{type}:\texttt{impasse},\text{reason}:\texttt{fact\_conflict},\dots,\text{subgoal}:\text{"澄清冲突记忆哪条为准"},\text{question}:\text{"我这边有两条不一致的记录，以哪条为准？"},\text{block\_execution}:\text{true}\},&\text{conflict}\notin\{\text{None},\texttt{"none"}\}\quad(\texttt{:38-41})\\[2pt]
\text{None},&\text{otherwise}\quad(\texttt{:42})
\end{cases}
$$

优先级：`unknown` 先判（`test_control.py:680` 钉住"unknown 优先于 conflict"）。

### 10.2 接线缺口（必须在 Method 局限里写）

- 全仓 grep `impasse`：只有 `engine/metacog.py`（定义）、`tests/test_control.py`（测试）、`tools/gen_impl_status.py:61`（状态矩阵条目）、`tools/_cyber1.py`（一次性生成脚本）。**`engine/pipeline.py` 从不调用 `metacog.impasse`**。
- 因此 Impasse 目前只能进 Method 的"实现清单（未接线）"，或进 Future Work。**不得**写"系统在无命中时创建子目标并向目标插件申请澄清"——那是契约意图（`docs/04`），不是运行行为。
- 与之配套的第二个缺口：`conflict` 在引擎流水线恒为 `"none"`（§6.2），所以 `fact_conflict` 分支在引擎里也**不可达**。

### 10.3 当前不能声称什么

- 不能声称"Impasse 机制已接入主流程/已生效"。
- 不能声称"卡住时暂停执行"：`block_execution=True` 只是一个返回字段，没有消费者。
- 不能把它写成 ACT-R 的完整 impasse resolution（没有 knot/子目标栈/策略切换）。

---

## 11. 参数 provenance 总表（全部 `guess`，`measured = 0`）

| 机制 | `rules/decay-params.yaml` key（行） | 值 | provenance | 代码影子 |
|---|---|---|---|---|
| ACT-R | `actr.d` / `.ref` / `.lo` / `.hi`（216-220） | 0.5 / 0.001 / −3.0 / 1.0 | `guess`（`:221`） | `config.py:90-95` |
| ACT-R 权重 | `memory.actrWeight`（18） | 0.6 | 文件头 `guess`（`:4`）+ `actr.provenance` | `config.py:21` |
| 双通道衰减 | `memory.decay.tau.*`（38-43）、`importanceScaling`（44） | 7/30/90 d；true | `guess`（`:48`） | `config.py:15-19` |
| 积分 | `control.integralKi/.integralCap/.integralLeak/.integralDeadband`（224-227） | 0.15 / 0.4 / 0.05 / 0.02 | `guess`（`:233`） | `config.py:97-100` |
| 相变 | `control.phaseShiftRatio/.phaseShiftHardRatio`（228-229） | 0.7 / 0.85 | `guess`（`:233`） | `config.py:101-102` |
| 相变工程量 | `control.trendThreshold/.ratioMax/.historyCapacity`（230-232） | 0.02 / 1.5 / 64 | `guess`（`:233`） | `config.py:104-106` |
| 动态权重 | `compose.weights.*`（239-246） | 0.70/0.50/0.40/0.45； boosts 0.50/0.35/0.40； 阈值 0.6 | `guess`（`:247`） | `config.py:49-59` |
| 召回名额 | `memory.retrieval.topK`（20） | 3 | `guess`（`:27`） | `config.py:22` |
| 召回预算 | `memory.recallBudget.*`（69-76） | 900 / 3 / 10 / 20 | 块内无 provenance；文件头 `guess` | `config.py:25-26` |
| 归档门 | `memory.potency.archiveThreshold`（50） | 0.15 | 块内无 provenance；文件头 `guess` | `config.py:20` |
| 元认知情绪上限 | 无 rules key（仅 `metacog.emotionCapOnHighIntensity: true`，`:156`） | 代码 0.6 | **单一来源例外** | `config.py:30` |
| 注入占比 | `compose.injectionTokenRatioMax`（236） | 0.15 | 无 provenance 字段 | `config.py:45`（**键名不同**：`tokenRatioMax`） |

### 11.1 单一来源的**三处例外**（诚实记录，写论文时可作为"工程债"一句）

1. `engine/compose.py:36` 的 `ASSUMED_CONTEXT_TOKENS = 8000`：**不在 `rules/`、不在 `config.py`**，纯硬编码（决定 token 预算 1200）。全仓 grep 仅 2 处命中（`:36` 定义、`:183` 使用）。
2. `engine/config.py:30` 的 `metacog.emotionCapIntensity = 0.6`：`rules` 的 `metacog` 块只有布尔 `emotionCapOnHighIntensity: true`（`:156`），**没有 0.6 这个数值 key**。
3. `rules` 的 `compose.injectionTokenRatioMax`（`:236`）与 `config.py:45` 的 `compose.tokenRatioMax` **键名不一致**，且 `tools/check_config_sync.py` 未比对它（grep `tokenRatio|injection` → 0 命中）。两处值同为 0.15，但**一致性不是被工具保证的**。

> 因此论文方法段不能写 "all tunable constants live in a single declarative rule file"；应写 "most tunable constants are centralized in `rules/decay-params.yaml`（66/66 cross-checked by `tools/check_config_sync.py`），with three documented exceptions"。

---

## 12. 证据矩阵（机制 × 测试 × 断言数，均为本次复跑）

| 机制 | 测试文件 | 测试函数 | 断言执行数 | 覆盖要点 |
|---|---|---|---|---|
| ACT-R base-level | `tests/test_control.py` | `test_actr_activation` | 15 | 频率↑/近因↑/无记录 −inf/归一化截断/touch |
| ACT-R 双通道 | `tests/test_control.py` | `test_actr_dual_channel` | 7 | 取大救回高频久远；`w=0` 退化；**直跑 replay/score 实测 recall@3=1.000、200 轮、零泄漏** |
| 名额契约 | `tests/test_control.py` | `test_topk_slot_contract` | 9 | `topK=1..5` 名额；maxReturned 不得遮蔽 topK |
| 积分控制 | `tests/test_control.py` | `test_integral_baseline` | 18 | Ki 累积、dt、死区泄放、cap 饱和、抗饱和锁存、leak、正偏离不推高、apply |
| 相变监控 | `tests/test_control.py` | `test_phase_shift_monitor` | 22 | 0.70/0.85 双阈值（含 0.90）、hard 先判、趋势提前量、ratioMax 截断、history 64、非法窗口、首尾锚点 |
| 参数单一来源 | `tests/test_control.py` | `test_params_single_source` | 8（相变 6 + 权重 2） | 改配置真的改变行为 |
| 必要多样性 | `tests/test_control.py` | `test_requisite_variety_guard` | 11 | 仅未定义冲突且无互斥才降维、只丢 style、事件留痕、无 style 时仍标记 |
| STPA 前置闸 | `tests/test_control.py` | `test_preflight_audit` | 33 | UCA-01…07 逐条 block、多命中顺序、UCA-08 只记 downgrades 不改 verdict |
| 动态权重 | `tests/test_control.py` | `test_compose_weights` | 29 | boost 生效、阈值 0.6、按权重升序淘汰、硬块不淘汰、拒绝场景必丢、权重不推翻互斥表 |
| Impasse | `tests/test_control.py` | `test_impasse` | 7 | unknown→no_record、conflict→fact_conflict、正常 None、unknown 优先 |
| **小计** | | | **159** | 0 failed（`test_control.py` 总行数 711） |
| 端到端流水线 | `tests/test_pipeline_integration.py` | `test_runs` / `test_event_contract` / `test_dropped_whitelist` / `test_onboarding_gate` / `test_idempotent` / `test_text_sanity` / `test_u_shape` / `test_phase_anchor` / `test_preflight_gate` | 8/10/2/4/1/2/**14**/**9**/**15** | U 型只动注入顺序、相变锚点首尾、`prompt=None` fail-closed、UCA-08 留痕 |
| **小计** | | | **65** | 0 failed |
| 宪法层审计 | `tests/test_constitution.py` | `test_each_check`/`test_blacklist`/`test_fail_closed`/`test_pass`/`test_frozen` | 10/3/3/3/7 = **26** | A1–A10、黑名单 C6、空/缺字段 fail-closed、FrozenGuard |

> 插件侧（**本次未复跑**，E5）：`host-sim` 188 + `control-sim` 148 + `stress` 45 = **381**（`docs/19:252`、`plugin/README.md`）。写 Method 时必须标"mock host / 未在真实 profile 长期运行"。

---

## 13. 逐字一致复核方法（给合作者/审稿人的可执行命令）

```powershell
$py = "<PYTHON>"
$env:PYTHONIOENCODING = "utf-8"          # 不加会在 Windows GBK 控制台崩在 U+2212

# 1) 公式落点：直接看代码，不要在正文里"重述成更漂亮的公式"
#    control.py:47-68（积分）/ 96-123（相变）/ 130-138（锚点）/ 157-170（降维）/ 191-232（STPA）
#    actr.py:30-66 / memory.py:39-64 / compose.py:134-150 / metacog.py:27-42

python tests/test_control.py                 # 期望 159 passed, 0 failed
python tests/test_pipeline_integration.py    # 期望  65 passed, 0 failed
python tests/test_constitution.py            # 期望  26 passed, 0 failed
python tools/check_config_sync.py            # 期望  66 ok, 0 fail
python tools/gen_impl_status.py --check      # 期望  OK
python tools/run_learning.py --json          # 期望  recall_at_3=1.0 / false_inject_rate=0.0
```

公式逐字校验清单（人工，每条一行核对）：
1. 积分方向 `e = -deviation`（不是 `deviation - baseline`）；
2. 死区 `|e| < deadband`（严格小于）；
3. 抗饱和 `>= cap`（大于等于）且 `saturated` 不复位；
4. 相变 `r >= hard` 先判，再 `r >= ratio`；`ratio = min(ratioMax, max(0, used/window))`；
5. 趋势 `(history[-1]-history[-3])/2`，需要 `len(history) >= 3`，严格 `>` 阈值；
6. 锚点条件 `prefeed || anchor_required`，输出 `head + "\n" + prompt + "\n" + tail`；
7. 降维条件 `conflict == "unresolved" and not matched_mutex`；
8. UCA-06 用 `fallback_applied`（**已降维事实**）而 UCA-07 用 `"〖人格锚点" in prompt`（**渲染结果**）——两个判据来源不同，不能统一成"标志位"；
9. ACT-R `dt <= 0 → dt = ref`、`MAX_TRACES=64`、`normalized` 区间 `[-3,1]`；
10. `memory.potency` 里 `w` 乘在 `actr.potency()`（已含 imp、已 clip）之后。

---

## 14. 写 Method 时的红线清单（逐条对照，违反即返工）

**必须做**

- [ ] 每个机制给 `file:line`；公式与代码同序同符号（本文件 §3/§4/§6/§7/§8/§9 可直接转写）。
- [ ] 明确写 "provenance of all numeric parameters is `guess`; none is `measured`"。
- [ ] 明确写 "the reference engine makes zero LLM calls; all reported behaviour is deterministic replay"。
- [ ] 明确写实现边界：Impasse 未接线（§10）、必要多样性缺自动冲突通道（§6.2）、UCA-01 只覆盖注入侧（§7.2）、ACT-R 只有 base-level（§8.2）、动态权重在回放中不可观测（§9.1）。
- [ ] VSM 只作为解释性框架（§2），并显式说明"no VSM-specific artifact exists"。
- [ ] 引用本项目的内部数字时标注来源与口径（例：`recall@3=1.000 on the frozen synthetic replay set v1, 200 turns, 10 families`）。

**禁止**

- [ ] 禁止把 `chaos-set` / 插件 host-sim 的"机制可达"写成"机制有效"。
- [ ] 禁止写 "STPA 100% 拦截"、"measured/calibrated thresholds"、"VSM implemented"、"ACT-R architecture implemented"、"rate-distortion optimal"。
- [ ] 禁止把四冻结方向（行动闭环/自适应演化/群体心智/多模态感知，`decisions/010`）写进已实现 Method——包括"我们预留了工具闭环接口"这类暗示。
- [ ] 禁止把"引擎渲染的 prompt 不含记忆正文"这一事实隐去后在消融里用"prompt 含设定"做指标（见 `ablation/DESIGN.md` §4）。

---

## 15. 学术框架归属：能借什么、不能借什么

| 框架 | 借什么（可写） | 不能借什么（禁写） |
|---|---|---|
| **VSM（Beer）** | 用 S1–S5 组织 Related Work 的分层讨论；用 S3/S3* 解释"审计旁路" | 不能说实现了 VSM；不能用 VSM 预测性能 |
| **STPA（Leveson）** | UCA 概念、fail-closed 控制动作、前置闸（pre-flight） | 不能说做了完整 STPA（无控制结构/因果情景）；不能声称覆盖率 |
| **ACT-R（Anderson）** | base-level 激活方程、频率+近因的认知解释、impasse 概念 | 不能说实现了 ACT-R 架构；不能说双通道 `max` 来自 ACT-R |
| **Requisite variety（Ashby）** | "能力不足则收缩行为空间"的设计动机 | 不能说实现了 variety 计量/平衡；不能量化 |
| **rate-distortion（Shannon）** | 仅可作**类比**：token 预算=rate、预览截断=distortion | 不能说形式化了率失真；**没有 distortion 度量**（`MIN_PREVIEW_CHARS=24` 只是工程下限，`memory.py:28`） |
| **Global Workspace（Baars）** | 域权重竞争的可读类比 | 不能说实现了广播/点火；淘汰在回放中从未发生 |
| **Control theory** | PI/leaky integrator、feed-forward、deadband、anti-windup、双阈值迟滞（注意：本实现**没有**迟滞，见下） | 不能声称稳定性/收敛/最优性；**不能说有 hysteresis**——`PhaseShiftMonitor` 无回落阈值，相位随 ratio 单调回退（`:104-109`） |

> 一个易被审稿人抓的点：`PhaseShiftMonitor` **不是** Schmitt trigger（无双阈值回差）；0.70/0.85 是**两个不同语义的触发点**（shift 预警 / critical 强制），不是"进入/退出"迟滞。真正的迟滞在情感注入（`engine/emotion.py:86-101`，进入 0.15/0.2、退出 0.10/0.2），别把两者混写。

---

## 16. 待 Lead/H0 决策的三个开放问题

1. **VSM 要不要留在 Method**：建议移到 Discussion/Related Work，Method 只写"分层与强制点"（有代码）。若 H0 坚持 Method 出现 VSM，必须同时给出 §2.1 的"解释性映射"声明。
2. **Impasse 与必要多样性怎么写**：建议在 Method 用独立小段 "Implemented but not wired (design debt)"，并给出 `file:line` 证据——这比删掉更诚实，也防止审稿人发现代码后质疑。
3. **插件（v5.1）是否进 Method**：建议只在 Method 末尾用一段"reference engine vs host plugin"讲**同语义移植与已知差异**（`docs/19:191-222`），不把插件的 381 条断言当作引擎证据。

---

## 17. 与团队其它交付物的对齐（避免 Lead 合成时术语打架）

同批交付里已有 `paper/GLOSSARY.md`（术语表）、`paper/OUTLINE-zh.md`（章节编号）、`paper/data/DATASET-SPEC.md` + `paper/data/_facts.json`（数据与事实核验）。本文件与它们的关系如下：

### 17.1 英文术语**以 GLOSSARY 为准**（本文件用同名）

| 机制 | GLOSSARY 的正式名 | 本文件对应节 |
|---|---|---|
| U 型注入排序 | **U-shaped Attention Anchoring** | §5.2 |
| 相变监控 | **Phase-Shift Preemptive Monitoring** | §4 |
| 必要多样性降维 | **Requisite Variety Dimensionality Reduction** | §6 |
| ACT-R base-level / 双通道 | **Base-Level Activation (ACT-R)** / **Dual-Channel Potency (decay ∪ ACT-R)** | §8 |
| STPA 前置闸 | **Causal Interposition Safety Gate**（UCA = Unsafe Control Actions, STPA-derived） | §7 |
| 积分控制 | **Integral Feedback with Anti-Windup** | §3 |
| 动态权重 | **Global-Workspace Dynamic Weighting** | §9 |
| 首尾锚点 | **Persona Anchor Injection** | §5 |
| Impasse | **Impasse-Driven Subgoaling（Soar 概念）** | §10 |

> 本文件正文里出现的**描述性英文**（如 "feed-forward early warning"）只作解释，不构成另一套术语；正文首次定义必须用上表 GLOSSARY 名。

### 17.2 三处需要 Lead 裁决的表述差异（都已在本文件给出证据）

1. **rate-distortion 挂在哪个机制上**：`GLOSSARY.md` B 段把 rate-distortion 作为 **ACT-R / 记忆巩固**行的 framing-only 标签；本文件 §15 只把它当**注入预算**的类比（预算=rate、预览截断=distortion）。两处都禁止出现率失真公式，但**归属不同**。建议：正文只用一处类比（记忆巩固），并把注入预算那段降级为脚注；不要同时挂两处。
2. **Impasse 的理论归属**：`engine/metacog.py:30-31` 注释写成 ACT-R，`GLOSSARY.md` 写 Soar。**按 Soar**（代码注释是笔误级不准确），见 §10 修正框。
3. **"参数单一来源"的强度**：`GLOSSARY.md` C 段写"66 项逐键比对"，本文件 §11.1 给出**三处未纳入比对/不在 rules 的例外**（`ASSUMED_CONTEXT_TOKENS=8000`、`metacog.emotionCapIntensity=0.6`、`injectionTokenRatioMax` 键名不一致）。建议正文把 66/66 写成 "66 cross-checked keys + 3 documented exceptions"，否则审稿人一 grep 就能反证。

### 17.3 与 `paper/data/` 的事实一致性（已互相印证）

- `_facts.json` 的 `jailbreak_tautology`（24 行语料、24 行均含引擎 pattern）**与本文件 §7.3、`ablation/DESIGN.md` §3.3(a) 的"构造性饱和"结论一致**——即语料是为命中词表而造的，24/24 不是鲁棒性证据。
- `_facts.json` 的 `corpus_consumers`（`jailbreak_variants_mentioned: false`）说明现有 A5 执行器**没有**把 `jailbreak_variants.jsonl` 纳入消费；`ablation/DESIGN.md` 若用它，属于**新增消费者**，必须在设计里注明（已注明）。
- `_facts.json` 的 `evidence_grade`： "由实现计算（输入为合成数据）；不是消融结果"——与本文件"全部数字可复现、但都不是模型效果证据"的立场一致。

### 17.4 本文件不覆盖的内容（归属别的交付物）

| 内容 | 归属 |
|---|---|
| 章节编号 / 论证顺序 | `paper/OUTLINE-zh.md`（本文件不指定节号） |
| 术语的中文释义与 Table 1 生成规则 | `paper/GLOSSARY.md` §0–§2 |
| 数据集字段、族分布、指纹清单 | `paper/data/DATASET-SPEC.md`、`paper/data/INVENTORY.md` |
| 三组消融的条件/指标/schema | `paper/ablation/DESIGN.md` |

---

*本文件只做映射，不含实验结果；所有数字均可由 §13 的命令复现。*
