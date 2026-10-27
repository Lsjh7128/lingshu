# LINGSHU（灵枢）arXiv 预印本 · 论文章节大纲（H0 评审门禁件）

> **文件性质**：本文件是**给 H0 评审的写作蓝图**，不是论文正文。本阶段只交「大纲 + 术语表」，**不写正文、不跑实验**。
> **版本**：paper-outline-0.1 ｜ 2026-10-27 ｜ 对应项目版本 v5.1
> **对象**：H0（人类负责人）—— 不具备代码与公式学术写作能力，本文所有判断都已尽量落到可复核的仓库文件上。
> **配套文件**：[GLOSSARY.md](GLOSSARY.md)（术语映射与代码落点，本文件所有术语的正式英文名以它为准）。
> **权威依据**（本文件每条事实都能指回下列之一，不接受"印象"作为依据）：
> `README.md`、`交接总览.md`、`项目简介.md`、`docs/00-架构总览.md`、`docs/19-控制论重构契约.md`、
> `docs/13-学习机制.md`、`docs/16-实现状态.md`、`docs/22-混沌工程.md`、`assumptions/未验证.md`、
> `assumptions/已验证.md`、`decisions/008`、`decisions/010`、`decisions/011`、`decisions/012`、
> `engine/control.py`、`engine/actr.py`、`engine/pipeline.py`、`engine/memory.py`、`engine/compose.py`、
> `rules/decay-params.yaml`、`rules/learning-params.yaml`、
> `acceptance/shadow-baseline.md`、`acceptance/chaos/*.json`、`acceptance/chaos/host-uca08-2026-10-26.md`、
> `paper/data/INVENTORY.md`（数据资产盘点，本文件中标"衍生统计/结构性推导"的数字来自它，已逐条注明）。

---

## 0. 一句话骨架

**我们交付的不是"更强的 Agent"，而是一层「可执行、可熔断、可回滚、可复核」的认知控制面**：
把长上下文 Agent 的六类认知插件挂在宿主注入点上，用相变前馈 + STPA 前置闸 + 必要多样性降维把它们的行为收敛到
"可理解 / 可预期 / 可信"；论文报告的是**契约可执行性 + 机制可达性 + 参数诚实性**，**不报告端到端收益**。

---

## 1. 候选标题（3 个，供 H0 选）

| # | 英文正式标题（建议投稿用） | 中文副题 | 定位 | 风险 |
|---|---|---|---|---|
| **T1（推荐 arXiv 首发）** | *LINGSHU: A Cybernetic Control Plane for Auditable Cognition Plugins in Agent Hosts* | 灵枢——面向 Agent 宿主可审计认知插件的控制论控制面 | 机制 + 工程双层，覆盖 cs.AI / cs.SE | "Cybernetic" 会招来"你真的做了控制论吗"的审稿人，须用 §4 逐机制回应（积分/前馈/必要多样性/STPA 均有代码落点）；无 VSM 实现，标题不得出现 VSM |
| **T2（推荐 SE 重投）** | *Contracts Before Cognition: An Executable Control-Plane Contract for Agent Memory and Persona Stability* | 灵枢——先契约、后认知：Agent 记忆与人格稳定性的可执行控制面契约 | 契约工程 + 可复现工件 | "Stability" 易被读成"我们证明了稳定"，须在 Abstract 首句限定为"stability-preserving mechanisms, not stability guarantees" |
| **T3（推荐 workshop / 短版）** | *What Can Be Verified: A Reference Implementation and Honest Evaluation of an Agent Cognition Control Plane* | 灵枢——可被验证的那部分：Agent 认知控制面的参考实现与诚实评估 | 以"诚实评估方法学"为卖点 | 方法学卖点依赖"我们公开了 measured=0"，这与会议文化契合度因 venue 而异；ML 主会可能视为"negative result" |

> **选标题的三条硬约束**：① 不出现 "validated / verified thresholds / state-of-the-art / outperforms"；
> ② 不出现四个冻结方向名词（行动闭环 / 自适应演化 / 群体心智 / 多模态），见 `decisions/010`；
> ③ 副题必须含"控制面 / 契约 / 可审计"三者之一——这是全文真实贡献所在的词。

---

## 2. 目标 venue 与投稿策略

### 2.1 首发：arXiv 预印本

| 项 | 建议 | 理由 |
|---|---|---|
| 主分类 | **cs.AI** | 命中"Agent 认知架构"读者群；论文主语是机制与契约，不是纯软件工程 |
| 交叉分类 | **cs.SE**（契约工程/可复现工件）+ **cs.CY**（可信与安全治理） | 本文一半篇幅在讲"契约可执行 + 失败语义"，SE 读者比 ML 读者更能接受"没有 SOTA 对比" |
| 可选第三交叉 | **cs.HC**（若最终按 HCI 叙事写：人对 Agent 行为的可理解性） | 仅当 §6 Discussion 的证据链以"人可理解"为主时才加；否则不加 |
| 格式 | 8 页双栏正文 + 不限页附录（附录承载 UCA 全表、参数全表、复现命令） | 正文目标 **5,600–6,400 词**（各节预算区间上界见 §4；超页按压缩优先级砍） |
| **风险 1** | arXiv 审核可能以"系统描述、无新实证结论"暂缓 | 对策：Abstract/§5 明确把贡献定义为 **artifact + feasible contract + mechanism-reachability evidence**，并给出可一键复现的命令与断言数；不要把论文包装成"提出新方法" |
| **风险 2** | 新作者可能需要 endorsement | 对策：先投可接收 arXiv 的 workshop/短文建立记录；或由有 arXiv 记录的合著者背书（H0 决定） |
| **风险 3** | 预印本先于顶会投稿的"抢发"风险 | 本文无竞争性 SOTA 声明，抢先风险低；预印本反而保护"我们最早公开这套契约"的优先性 |

### 2.2 后续顶会/期刊选项（按契合度排序）

| 方向 | 候选 venue（只写名称，**不写截止日期**——日期以官网 CFP 为准，本文件不编造日期） | 契合点 | 需补的东西 |
|---|---|---|---|
| **AI Safety / 治理** | NeurIPS 或 ICML 的 Safe Generative AI 类 workshop；ACM FAccT | STPA 前置闸、fail-closed 唯一归属、越狱拦截留痕 | 真实红队样本 ≥20（`docs/22` §8.3 门槛）+ 人工结果标签 |
| **SE / 工具** | ICSE、ASE、FSE 的 Tool/Demo 或 Industry 轨 | 868 + 381 条断言、契约漂移检查、三处数值副本一致性 | 演示视频 + 一键复现脚本（`scripts/quickstart.py` 已是雏形） |
| **Agentic Systems** | AAMAS；ACL/EMNLP System Demonstrations | 6 插件 + 合成器 + 降级矩阵是完整可运行系统 | Demo 轨需要"能演示给观众看"的交互面（`console/demo.html` 已可作基础） |
| **HCI** | CHI、IUI、CSCW | "让行为对人类可理解"的定位 + 用户纠正率作为最终裁判 | 需要真人用户研究（当前 0 名外部用户），这是最重的一项 |

### 2.3 投稿策略判断（给 H0 的一句话建议）

> **先 arXiv（T1 标题）→ 同期投一个 safety/SE workshop 拿反馈 → 按反馈决定是补实验冲顶会，还是把本文定位为"可交接工程报告"投 SE 轨。**
> 不要直接投 NeurIPS/ICLR 主会：本文没有基准对比、没有统计检验、参数 measured=0，
> 在这类 venue 会被 desk reject（这是基于论文内容的判断，不是对审稿人的揣测）。

---

## 3. 全局写作约束（每节都适用，先立规矩再写节）

### 3.1 三类边界的定义（后文每节都以 ✅ / ⚠️ / ⛔ 三行标注）

| 标记 | 含义 | 判定标准 |
|---|---|---|
| ✅ **可安全声称** | 有可访问、可复核、可复现的仓库证据 | 能指到 `tests/` 断言、`acceptance/*.json` 字段、`engine/*.py` 行号，且读代码/读 JSON 即可复核 |
| ⚠️ **必须 hedge** | 机制存在但效果未测；或证据只到"按契约动作" | 句式必须带限定词：*mechanism reachable*、*in the tested configuration*、*by construction, not by measurement* |
| ⛔ **严禁出现** | 无证据、易被证伪、或违反项目硬约束 | 任何百分比收益、"已验证阈值"、把 guess 写成 calibrated、四个冻结方向进入 Method |

### 3.2 数字口径（全文只认这五个口径，别的数字一律不写）

| 口径 | 值 | 出处（可复核） | 允许的说法 |
|---|---|---|---|
| Python 验收 | **868 passed / 0 failed**（13 个测试文件：引擎 461 + 标定分析 156 + 混沌标定 134 + 混沌执行 117） | `README.md:10`、`项目简介.md:124`、`交接总览.md:30` | "868 assertions pass" ✅ |
| 插件断言 | **381**（host-sim 188 + control-sim 148 + stress 45） | `README.md:10`、`docs/19:237-238` | "381 plugin-side assertions" ✅ |
| 冻结回放基线 | `recall@3 = 1.000`、`forbidden_leak = 0`、`truncated_turns = 0` | `README.md:10`、`交接总览.md:126` | "on the frozen replay set" ✅（必须带"on the frozen replay set"限定） |
| 参数 provenance | `measured = 0`；`guess = 全部` | `assumptions/未验证.md:58-66`、`rules/decay-params.yaml:4`、`acceptance/shadow-baseline.md:246` | "all values are guesses" ✅ |
| 真实流量 | 真实 turn **8**；有 ratio 观测的 turn **5**（全为 0） | `acceptance/shadow-baseline.md:23-25`、`:40` | "n = 8 turns of real traffic" ✅ |

> ⛔ **禁止**把 `docs/16` 的"断言调用点 982"当成验收数（那是静态调用点计数，同 `docs/16:12` 自述"循环内会执行多次"）；
> 论文只写 868 / 381 两个口径。

### 3.3 引用纪律（比一般论文严，因为本项目已被"假引用"坑过）

1. **只引能读到摘要原文的文献**；仓库内已核实到"标题 + 摘要逐字吻合"的只有 5 篇：
   ZenBrain（`arXiv:2604.23878`）、FSFM（`arXiv:2604.20300`）、Human-Inspired Memory Architecture（`arXiv:2605.08538`）、
   Sentipolis（`arXiv:2601.18027`）、PersMem（`arXiv:2609.34372`）——见 `assumptions/已验证.md:14-19`。
2. 上述 5 篇**投稿前必须重新核对编号与版本**（编号可能变更/撤稿）；核对记录写入 `paper/refs-verified.md`（本阶段不建）。
3. **严禁**引用 `assumptions/未验证.md:35-46` 列为"0 出处"的任何数字（MemCurrency 89.2%、emotional-memory F1 0.168、
   ZenBrain +19.5%/+21.6%、ChatGPT-5.4 等）。
4. 经典机制必须引原始出处（ACT-R / Ashby 必要多样性 / Leveson STPA / Beer VSM / PAD / Goodhart / anti-windup），
   **且必须区分"我们借用机制"与"我们提出机制"**——见 §8 风险 R4。
5. 引用格式落 `paper/refs.bib`，本阶段不产出（避免写出未经核验的条目）。

### 3.4 冻结方向纪律（硬约束 2 的落地方式）

四个方向（行动闭环 / 自适应演化 / 群体心智 / 多模态感知，`decisions/010:30-35`）**只允许出现在 §7 Limitations**，且必须三件套齐全：
**"已冻结（frozen）+ 未实现（not implemented）+ 无工程证据（no engineering evidence）"**，并附解冻的可观测触发条件。
Method / Experiments / Discussion 任何位置出现这四个方向的机制描述、架构图元素、未来工作承诺 → **视为违规，退回重写**。

---

## 4. 篇幅预算（8 页双栏；正文目标 5,600–6,400 词，各节区间上界合计 6,750，超页按下方压缩优先级砍）

| 节 | 词数预算 | 页数约 | 说明 |
|---|---|---|---|
| Abstract | 150–200 | 0.2 | 必须含"measured = 0"一句 |
| 1 Introduction | 700–800 | 1.0 | 含 4 条贡献 + 1 条"本文不报告什么" |
| 2 Related Work | 600–700 | 0.9 | 5 个簇，只引核实过的 |
| 3 Problem Formulation | 500–600 | 0.7 | 形式化 + 4 类失败模式 + 3 条假设 |
| **4 Methodology** | **1,900–2,100** | **2.6** | 全文重心，10 个子节 |
| 5 Experiments | 900–1,100 | 1.3 | 含"计划中消融"占位，明确标注未完成 |
| 6 Discussion | 400–500 | 0.6 | 代价与设计张力，不写收益 |
| 7 Limitations | 450–550 | 0.6 | 含四个冻结方向（唯一允许出现的位置） |
| 8 Conclusion | 150–200 | 0.2 | 不重复数字 |
| **正文合计** | **5,750–6,750** | **8.0（投版目标 ≤6,400）** | 超页先砍 §2 与 §6；预算区间可整体下调 10–15% |
| Appendix A–F | 不限 | 附录 | 参数全表、UCA 全表、复现命令、冻结方向原文 |

**压缩优先级**（若排版超页）：§2 引用簇 → §6 Discussion → §4.10 移植差异（移附录）→ §3 假设（移附录）。
**禁止压缩**：§5 的"未完成"标注、§7 Limitations、§4 的失败语义（fail-closed 唯一归属）。

---

## 5. 逐节大纲

> 每节格式：**【字数】** → 要点 → **三类边界**。要点编号将在正文中作为写作检查表使用。

### 5.1 Abstract 【150–200 词】

**要点**
1. 问题：长上下文 Agent 的失效多发生在**注入侧**（记忆越权当事实、拒绝被软化、人格漂移、预算挤掉安全约束），而非模型能力。
2. 我们提出 LINGSHU：给 Agent 宿主挂 **6 个认知插件 + 合成器 + 降级矩阵 + 学习闭环 + 上下文控制面**的一套可插拔体系。
3. 控制面四机制（一句话各一）：积分反馈（情感基线，带死区/抗饱和/泄放）、相变前馈（70%/85% 双阈值 + 首尾人格锚点）、
   必要多样性降维（未定义冲突 → 丢风格域、退中性事实+澄清）、STPA 前置闸（UCA-01…08，唯一 fail-closed 是宪法层审计）。
4. 工件：零依赖参考实现 `engine/`（26 模块）+ DSH 插件（7 段注入）；**868 项 Python 断言 + 381 项插件断言全绿**。
5. 证据类型：**机制可达性**（chaos 四剧本 engine 4/4；真实宿主 4/4、0 skip、6 次模型调用；UCA-08 三轮饱和只留痕不熔断）。
6. **诚实声明（必写）**：所有参数 provenance 为 guess（measured = 0）；真实流量 8 轮；本文**不报告**端到端收益。
7. 结论句：可复现的契约 + 明确标注强度的证据，比不可复核的收益声称更有用。

**三类边界**
- ✅ 可安全声称：体系结构、机制清单、断言数、四剧本结果、`recall@3=1.000`（限定"冻结回放集"）、measured=0。
- ⚠️ 必须 hedge：控制面"降低漂移/改善共情"——本阶段**没有任何**真实效果数据，只能写 *designed to* / *intended to*。
- ⛔ 严禁出现：任何 "+X%"、SOTA、validated threshold、"proven to prevent jailbreak"、"solves hallucination"。

---

### 5.2 §1 Introduction 【700–800 词】

**要点**
1. 场景：宿主（DSH）在长会话中把"记忆 / 人格 / 情感 / 目标"持续注入 prompt；上下文越长，注入面越大，失效越隐蔽。
2. 四类可观测失效（**这就是 §3 的形式化对象**）：F1 记忆越权（记忆原文被当事实）、F2 拒绝软化（风格/情感注入进拒绝场景）、
   F3 人格漂移（长上下文后期行为漂移）、F4 预算挤占（截断把宪法层挤掉）。
3. 立场（本项目最重要一句，`README.md:56-58`）：**不追求"让 Agent 更像人"，追求"让行为对人类可理解、可预期、可信"**；
   明确"像人"四层中只做前三层（能力/缺陷/表达），禁第四层（宣称主观体验）。
4. 五条不变量 I1–I5（`README.md:74-82`）：记忆是参考不替代当前陈述 / 不做全量注入 / 情感只作用风格域 /
   推断逐字可溯可回滚 / 记忆是唯一写方。**这五条是全文的"硬边界"，所有机制都为它们服务**。
5. 差异化：已有工作多数在"记忆更强 / 检索更准 / 对齐更好"；本文在**注入侧的准入与降级语义**——
   谁可以进 prompt、装不下丢谁、出错时谁熔断、改动如何回滚。
6. 贡献 C1–C4（**每条必须标注证据强度，这是本文诚实性的骨架**）：
   - C1 **可执行契约**：契约不是文档，而是能跑红/绿的断言（868 + 381）→ 强度：强（可直接复核）。
   - C2 **控制面失败语义**：唯一 fail-closed 归属、UCA-01…08 前置拦截、降级留痕（UCA-08 不熔断）→ 强度：中（机制可达，效果未测）。
   - C3 **诚实工程方法**：`assumptions/未验证.md` + `provenance` 字段 + 冻结考卷 + 灰度回滚，把"哪些话不能信"变成一等工件 → 强度：强（工件存在）。
   - C4 **可移植性观察**：同一语义在 Python 参考实现与 Node 插件两处独立实现，并记录 5 处已知语义差异（`docs/19:207-222`）→ 强度：中。
7. **本文不报告什么（单独一段，放在贡献之后）**：不报告端到端收益、不报告阈值正确性、不报告跨宿主泛化、
   不报告用户研究结论。这一段的作用是**提前拆掉审稿人的误期待**。
8. 结构指引一句话（"§2 相关工作 → §3 形式化 → §4 机制 → §5 证据 → §6–7 代价与边界"）。

**三类边界**
- ✅ 可安全声称：问题存在的**结构性论证**（注入面随上下文增长、预算截断会挤掉高优先级块——由 `ASSEMBLY_ORDER` + 预算逻辑保证）。
- ⚠️ 必须 hedge：F3 人格漂移——**本项目没有任何真实漂移测量**；只能写 *a failure mode the design targets*，不得引用任何漂移率数字。
- ⛔ 严禁出现："现有系统都做不到"、把四类失效说成"已被我们复现"、任何文献数字（除 §3.3 白名单 5 篇的定性机制）。

---

### 5.3 §2 Related Work 【600–700 词】

**要点**
1. 簇 A **Agent 长期记忆与遗忘**：ZenBrain（7 层 + 睡眠巩固）、FSFM（遗忘四分类：被动衰减/主动删除/安全触发/自适应强化）、
   HIMemory（去重式巩固）、PersMem（人格内化）。差异：它们优化"记得准"，本文优化"记得住但不越权"（I1 + 事实域不出现记忆原文）。
2. 簇 B **情感与人格建模**：Sentipolis（连续 PAD + 双速动力学 + 情感-记忆耦合）。差异：本文把情感**限制在风格域**（I3），
   并给出域隔离的工程强制点（互斥表 + 合成器域分类）；引用时必须带上 Sentipolis 原文 caveat（收益 model-dependent，`assumptions/已验证.md:61-63`）。
3. 簇 C **安全工程方法**：STPA / UCA（Leveson 系谱）；本文把它**前移到注入侧**（拼装完、发模型前），
   并把"降级"与"熔断"分开（UCA-08 只留痕）。差异点写清：经典 STPA 是分析法制品，本文给的是**运行时闸门**。
4. 簇 D **控制论与必要多样性**：Ashby 必要多样性定律、Beer VSM。**必须写明：本文借用其思想做"冲突超出处理能力时收缩行为空间"，
   不声称实现 VSM（仓库内无 VSM 实现，见 GLOSSARY G6）**。
5. 簇 E **可复现评估与灰度发布**：离线回放、canary/feature-flag 回滚、guardrail 自动回退。差异：本文把"考卷"（评估口径/回放集/护栏阈值）
   纳入只读冻结区（`docs/13:280-306` 三道闸），并给出黑名单（`rules/learning-params.yaml:43-48`）。
6. **簇 F 明确不覆盖**（一句话，防止审稿人问"为什么不比"）：多 Agent 协作、工具调用闭环、元学习、多模态——均按 `decisions/010` 冻结，
   本文不涉及、不比较（这四句只允许在此处与 §7 出现）。
7. 收尾：Table 3 的读法（"是否有控制面 / 是否有前置闸 / 是否有可执行契约 / 是否有可复核工件"四列，只填能核实的格子）。

**三类边界**
- ✅ 可安全声称：五个簇的机制描述（均可由已核实摘要支撑）；"本文与它们的差异在注入侧治理"。
- ⚠️ 必须 hedge：任何"我们更好"的表述——只能写 *orthogonal*（正交）或 *complementary*，不得写 better/outperform。
- ⛔ 严禁出现：未核实引用、把 5 篇之外的数字写进正文、声称"首个提出 XX"（本项目无法证明首创性）、
  把 GitHub 项目调研（`research/00`）当学术文献引用。

---

### 5.4 §3 Problem Formulation 【500–600 词】

**要点**
1. 符号：宿主 H、轮次 t、窗口 W_t、注入 token 预算 B、注入块集合 D_t（五域：safety / fact / organization / style / retrieval）。
2. 控制变量：U_t = {inject（注入哪些块）, reduce（降维/丢域）, anchor（首尾锚点）, trip（熔断）, log（留痕）} —— 五个动作，各自有代码落点。
3. 失效定义（可观测化，每条给判定字段）：
   - F1 → UCA-04（事实域出现无源记忆陈述）+ 不变量 I1；判定字段 `fact_block`。
   - F2 → UCA-03（refuse 场景注入 style）；判定字段 `refuse && style_injected`。
   - F3 → UCA-07（prefeed 轮缺锚点）；判定字段 `phase_result.prefeed && "〖人格锚点" not in prompt`。
   - F4 → UCA-05（宪法块缺失）；判定字段 `blocks[0].startswith("[constitution]")`。
4. 评价口径（**冻结，不由学习系统修改**）：`recall@3`、`forbidden_leak`、`truncated_turns`（`docs/13:88-94` 指标定义 + `docs/13:280-306` 考卷只读）。
5. 参数诚实性约束：每个 θ 带 `provenance ∈ {guess, literature, measured}`；**本文优化目标不允许消费未标定参数的功效声明**（一句话即可，别写成正式定义）。
6. 假设 A1–A3：单宿主（DSH 0.2.0-rc.2）、单用户、中文为主（中文 bigram + 英文 token 线索切分，其他语言未设计，`assumptions/未验证.md:83-90`）。
7. 明确"本文不求解的问题"：不求解最优注入策略、不求解参数最优值、不求解跨宿主泛化。

**三类边界**
- ✅ 可安全声称：形式化对象与判定字段一一对应（都能指到代码行）；评价口径已冻结并有黑名单保护。
- ⚠️ 必须 hedge：形式化是"设计意图的可执行描述"，**不是**可证最优性的控制问题；不得使用"稳定收敛/最优/保证"等控制论强词。
- ⛔ 严禁出现：Lyapunov/收敛性证明、"我们证明了这套机制降低失效概率"、把 guess 参数代入任何理论保证。

---

### 5.5 §4 Methodology 【1,900–2,100 词，10 个子节】

> 这一节是全文重心。**每个子节必须包含：机制语义 → 代码落点 → 已知缺陷/代价**（三段式，缺陷不允许省略）。

**4.1 分层与优先级（~200 词）**
1. 八层 + 合成器 + 宿主核心 + 可观测性（`docs/00:9-50`），序号越小优先级越高。
2. 五域影响域划分（安全/事实/内容组织/风格/检索策略），域间不排序、域内排序（`docs/00:56-64`）。
3. **唯一 fail-closed 归属**：只有宪法层审计 fail-closed，其余全部 fail-open 或静默降级（`docs/00:68-83`、`docs/10:7-14`）；
   一句设计理由："安全失败要阻断，功能失败要可用——非对称是刻意的"。
4. 强制点诚实声明：`_frozen/` 哈希自校验是"强"强制点；记忆唯一写方是"中"；**插件 config 不可保护（H4 证伪）**（`docs/00:89-126`）。

**4.2 合成器：五域 + 8 条互斥 + 预算截断（~220 词）**
1. 域分类 → 域内排序 → 互斥裁决 → 预算截断 → 拼装（`engine/compose.py:19` `ASSEMBLY_ORDER`、`:24` `MUTEX`）。
2. 硬约束：`ASSEMBLY_ORDER` 与 8 条互斥表**不可被动态权重推翻**；`[constitution]` `[safety]` `[user]` 永不参与淘汰（`docs/19:90-94`）。
3. 动态权重只回答"预算不够先牺牲谁"（fact 0.70 / org 0.50 / style 0.40 + boost），不回答"谁能压过谁"（`docs/19:79-88`）。
4. 无互斥规则时的默认策略：丢低优先级域并记 `no_mutex_rule`（`docs/09:117`）。

**4.3 记忆：双通道 potency（指数衰减 ∪ ACT-R）（~220 词）**
1. 纯 ACT-R 失败的实测：`recall@3` 从 **1.000 掉到 0.556**（`docs/19:31-32`、`decisions/008:27-28`）——**这是全文唯一"负面结果"且必须写**。
2. 采用双通道取大：`potency = max(decay, importance × normalized(B) × w)`，`w = 0.6`（`engine/memory.py:39-64`、`engine/actr.py:30-41`）。
3. base-level：`B_i = ln Σ_j (t_now − t_j + REF)^(−d)`，`d = 0.5, REF = 0.001 天`；归一化 `lo=-3.0, hi=1.0`；访问轨迹上限 64。
4. 语义：`w` 是"救援力度"不是"替代程度"；默认参数下冻结基线不变（`recall@3=1.000`）。
5. supersede：同 `(subject, relation)` 不同 object → 同 id 墓碑 + last-wins（`engine/memory.py:295`、`docs/19:247`）。
6. **诚实标注**：rate-distortion 只是"有损压缩"类比框架，仓库无该目标函数（见 GLOSSARY G4/G12）。

**4.4 相变前馈：双阈值 + 趋势 + 首尾锚点（~240 词）**
1. `ratio = used / window`，截断到 `ratioMax = 1.5`；**先判 hard 再判 shift**（否则 critical 永不可达，v4.0 修，`engine/control.py:103-109`）。
2. `ratio ≥ 0.85 → critical`（强制锚点）；`ratio ≥ 0.70 → shift`（`prefeed`，`trend > 0.02` 时提前补锚）。
3. 锚点实现：首段 `ANCHOR_PREFIX` + 尾段 `ANCHOR_SUFFIX`（`engine/control.py:126-138`），pipeline 在渲染后、审计前施加（`engine/pipeline.py:156-162`）。
4. 插件侧差异：只能注入 order 90/140 两段，语义近似首尾包裹（`docs/19:209-213`）。
5. **参数状态**：0.70 / 0.85 / 0.02 全部 `provenance: guess`；真实 ratio 观测 5 个且全为 0（窗口 1M），
   **真实相变收益未知**（`acceptance/shadow-baseline.md:40-42`、`:82-85`）。
6. 阈值联动规则（H0 裁决 B）：一旦有真实退化证据，`hard = min(0.95, shift + 0.15)`（`decisions/011:46-56`）——写进方法而非结果，因为**尚未触发**。

**4.5 必要多样性降维（~180 词）**
1. 触发条件：`conflict = unresolved` **且**未命中互斥表 → `fallback = true`，`drop_domains = ["style"]`，动作 `degrade_to_fact_and_clarify`
   （`engine/control.py:146-170`）。
2. 语义：控制器可处理的冲突种类有限，超出能力时**收缩行为空间**而不是继续输出（Ashby 借用，非实现 VSM）。
3. 接线：pipeline 在合成后判、渲染前降（`engine/pipeline.py:145-154`、`:239-251`）；插件侧 v5.1 接线（`docs/19:249`），
   且**冲突未决时无条件声明并丢弃 style 域**（比 engine 更保守，`docs/19:218-222`）。
4. fail-open：`verdict` 仍 `allow`，不置 `blocked`（降维 ≠ 熔断）。
5. 诚实标注：正常路径不可达（`decisions/008:19` 记为"接线（正常路径不可达）"），目前只有压测与 S4 预置证据。

**4.6 STPA 前置闸：UCA-01…08（~260 词）**
1. 位置：拼装完、发模型前（**不是输出后**）；命中任一 UCA-01…07 → `verdict = block`，pipeline **不产出可注入 prompt**（`prompt = None`）
   （`engine/pipeline.py:164-181`、`:204-207`）。
2. 八条 UCA 全表 + 响应（`engine/control.py:175-184`、`docs/19:127-136`），必须逐条给"检测条件"而非只给名字。
3. fail 模式：宪法层 fail-closed，其余 fail-open（`engine/control.py:229` `fail_mode: "closed"`）。
4. **UCA-08 的特殊性**：积分饱和是**降级不熔断**——饱和事实进 `audit.downgrades`，`verdict` 不变（`engine/control.py:221-223`）；
   设计理由：降级必须可观测，而不是悄悄发生。
5. 插件侧 UCA-01 扩到用户消息（`JAILBREAK_PATTERNS`），但**只能控制注入侧，无法把用户消息从宿主 payload 移除**——
   这条边界必须原样写进正文（`docs/19:226-230`）。
6. 已知缺口：插件侧 `refuse` 恒 false → UCA-03 在插件侧不会命中（`docs/19:213`）。

**4.7 U 型注入锚定（~180 词）**
1. 规则：检索结果按分数降序 `[r0…r_{n-1}]`，注入侧重排为 `n=3 → [r0,r2,r1]`、`n=4 → [r0,r2,r3,r1]`、`n=5 → [r0,r2,r4,r3,r1]`
   （`engine/pipeline.py:43-61`、`docs/19:96-109`）。
2. 依据：模型对 prompt 首尾利用优于中段——**这是设计假设 D1，无 A/B 证据**（`assumptions/未验证.md:71-73`），必须标为假设。
3. 硬约束：**只排注入侧**，`memory.recall` 的 `returned` 顺序一格不动（冻结的 `recall@3` 口径依赖它）；
   `n ≤ 2` 保持原序。
4. 可观测面：`event.injection_order` 是 `returned_ids` 的置换，可机检（`engine/pipeline.py:196`、测试 `test_u_shape`）。

**4.8 积分反馈：情感基线（~190 词）**
1. `e = −deviation`；`I ← I + Ki·e·dt`（只对 e > 0 积分）；`baseline = clamp(I, ±cap)`（`engine/control.py:30-73`、`docs/19:46-58`）。
2. 三条工程约束：**死区**（小偏离只泄放不积分）、**抗饱和**（到 cap 停止积分）、**泄放**（情绪回中性后缓慢归零）。
3. 参数：`Ki=0.15, cap=0.4, leak=0.05, deadband=0.02`，全部 guess（`rules/decay-params.yaml:224-233`）。
4. 饱和 → UCA-08 留痕的链路（见 4.6），真实宿主 3 轮饱和证据（`acceptance/chaos/host-uca08-2026-10-26.md:37-57`）。
5. 诚实标注：**"拒绝后再难恢复"的实际收益未测量**（`交接总览.md:151`）。

**4.9 学习闭环：度量 → 提议 → 灰度 → 回滚（~230 词）**
1. 铁律：**学习 = 产生提议，不等于应用提议**；提议生成方与审核方分离（`docs/13:3-5`）。
2. L0–L4 分层与"明确不做"（不做模型微调 / 不做目标演化 / 不做提议→自动应用 / 不做 RL）（`docs/13:8-24`）。
3. 四组指标必须成对（主指标 + 对抗项），否则被 Goodhart 化（`docs/13:62-95`）；最终裁判是**用户纠正率**。
4. 三道闸：`approval` seam 人审 / 灰度+护栏自动回滚 / **考卷只读**（`docs/13:280-306`）。
5. 护栏 G1–G7 与缺数据时的 fail-closed 语义（`rules/learning-params.yaml:84-97`）。
6. **实测结论要写进去**：972 组网格**没有实用改进**，但 6 个 L1 参数全部变得可辨识（`docs/13:358-397`）——
   这是一个"负结果 + 方法学正结果"的组合，正是本文诚实性的证据。
7. 提议必须可验证：`expected_effect` 来自回放而非用户口述（`docs/13:195-232`）。

**4.10 插件移植与已知语义差异（~200 词）**
1. 为什么移植而非复用：跨进程调引擎会把纯计算变成 IO 并带入 Python 异常语义（`docs/19:193-195`）。
2. 7 段注入 + 3 钩子 + 1 工具；实验 profile 跑真实 headless 会话（`docs/16:31`、`交接总览.md:117`）。
3. **5 条已知差异逐条写**（`docs/19:207-222`）：①锚点是两段不是首尾包裹；②上下文用量是字符估算（`charsPerToken=3`）非精确 tokenizer；
   ③`refuse` 场景插件无入口；④数值三副本（rules + patch + `DEFAULT_PARAMS`，由 `check_config_sync` 66 项防漂移）；⑤`user/message` 可能晚于首个 `pre-step`。
4. 这条"差异清单"本身就是贡献的一部分：**移植不是等价替换，语义差异必须显式记录**。

**§4 三类边界**
- ✅ 可安全声称：全部机制语义、公式、代码落点、UCA 表、fail 模式、移植差异清单；"双通道取大不破冻结基线"（有测试直接调 `replay/score` 实测 1.000）。
- ⚠️ 必须 hedge：U 型排序的"首尾优先"依据（设计假设 D1，无 A/B）；相变/积分的**效果**；降维的收益；"三副本一致"只到 `check_config_sync` 的 66 项键比对，不等于行为等价。
- ⛔ 严禁出现：四个冻结方向的机制/架构元素；把 rate-distortion / VSM 写成"我们实现"；任何阈值写成"calibrated/verified"；
  省略各子节的"已知缺陷"段（缺一条即视为违规）。

---

### 5.6 §5 Experiments 【900–1,100 词】

> 本节的诚实性要求最高：**这不是性能实验，是"契约可执行性 + 机制可达性"实验**。凡是没跑的东西必须写"未完成/占位"。

**5.1 工件与可复现性（~150 词）**
1. `engine/` 零第三方依赖、Python ≥ 3.8（`README.md:12`）；`python scripts/quickstart.py --no-open` 一条命令跑通。
2. 三套漂移检查：契约（`gen_contract --check`）、实现状态矩阵（`gen_impl_status --check`）、参数一致性（`check_config_sync` 66 项）。
3. 断言规模：868 / 381；给出测试文件与项数对照表（`README.md:198-208`）。
4. 可重复性：demo 生成"固定输入 → 逐字节相同输出"（`README.md:33`）。

**5.2 契约验证实验（~180 词）**
1. 幂等：同一 `run_config` 连跑两次逐字节相同（`LEARN-V1`，`docs/13:171`）。
2. 可归因：只改一个参数，归因指向该 key（`LEARN-V2`）；冻结回放集上 `topK=1/2/3/4 → 0.444/0.667/1.000/1.000`（`docs/19:168`）。
3. 冻结基线：`recall@3 = 1.000`、`forbidden_leak = 0`、`truncated_turns = 0`。
4. 负结果：972 组网格无实用改进（Δ 低于 5e-4），但 6/6 参数可辨识（spread 表，`docs/13:369-378`）。
5. 说明：这些是**口径不变的证明**，不是"效果更好"的证明。

**5.3 机制可达性：engine 模式（~200 词）**
> 数据源：`acceptance/chaos/chaos-20261026-234349-engine.json`（`corpus_source=chaos-set`，零模型调用）。
1. S1 长上下文洪水：51 轮、`max_ratio = 0.8683`；ratio 在 **turn 41** 首次 ≥0.70（0.7106）、**turn 50** 首次 ≥0.85（0.8672），`phase_trigger_count = 11`。
   （**精度提醒**：turn 49 记录值 `0.8500` 但 `phase` 仍为 `shift`——判定用的是未四舍五入的 ratio（<0.85），
   正文若引用"turn 50 才进 critical"必须同时给出这条解释，否则审稿人用显示值复算会认为有 bug。）
2. S1 锚点：`anchor_turns` 41–51 共 11 轮（engine 对 `shift` 的 prefeed 也会加锚点，注意与 `anchor_required` 的差别，`docs/22:266`）。
3. S2 记忆自相矛盾：`beta_hit = true`、`alpha_leak = false`、召回只命中 `mem_beta`、`statuses = {mem_alpha: superseded, mem_beta: active}`。
4. S3 情绪+越狱：`saturated.valence = true`、`stpa.blocked = true`、`ucas = ["UCA-01"]`、`downgrades = ["UCA-08"]`。
5. S4 未定义冲突：`fallback_triggered = true`、`drop_domains = ["style"]`。
6. summary：`scenarios_run = 4`、`phase_trigger_count = 11`、`attacks_blocked = 1`、`fallback_count = 1`。
7. **判读限制（必写）**：engine 模式 `degraded_at_ratio` 恒为 `null`；S1 的 `key_question.answer` 是**召回侧证据**（`answer_source="recall_returned"`），
   `keyword_hit=true` 只说明种子记忆能被召回，**不说明模型在长上下文里答对**（`docs/22:67-71`）。

**5.4 真实宿主实验（~230 词）**
> 数据源：`acceptance/chaos/chaos-20261026-234400-host.json`（4/4 剧本、0 skip、6 次模型调用）+ `host-uca08-2026-10-26.md`。
1. S1（1 次调用）：把 51 轮语料合成 **1 个** prompt（JSON `turns=1`），`max_ratio = 1.4597`（critical 可达）、`anchor_in_final_prompt = {head: true, tail: true}`、
   `key_question.keyword_hit = true`（`answer_source = "model_stdout"`）。**跨 run 的有效样本 n = 1**——
   三次 host run 用的是**同一条**语料行，重复跑 ≠ 独立样本（`paper/data/INVENTORY.md` §2.4）。
2. S2（3 次独立 headless）：事实写入 + 同键 supersede + 召回只命中新值；`supersede_events = 1`、`statuses` 旧 `superseded` 新 `active`。
   **有效样本 n = 1**：三次调用是**同一条语料**（seed/change/query 各一次，属同一剧本的三步），不是三个独立样本。
3. S3（1 次调用）：`blocked = true`、`ucas = ["UCA-01"]`、`sources = {UCA-01: user_message}`；`saturated` 全 false 是**设计结果**
   （单 prompt 只走一步积分，`pad_baseline.valence ≈ 0.0056`），不是失败（`docs/22:352-355`）。
4. S4（1 次调用 + 合成预置）：`fallback_triggered = true`、`drop_domains = ["style"]`、`verdict = "allow"`、`blocked = false`，
   `setup.synthetic = true`（冲突记忆是 `memorywriter` **人工预置**）。
   **另一条必须写明的实现事实**：host 路径使用内置 `S4_PROMPT_DEFAULT` 字面量提问，
   **并不读取 `variety.jsonl` 语料**（`paper/data/INVENTORY.md` §2.2-3）——所以 S4 的证据来自"预置记忆 + 内置提问"，不是语料驱动。
5. UCA-08（3 轮独立）：第 3 轮 `saturated.valence = true`（integral valence 0.4 = cap）、`downgrades = ["UCA-08"]`、`blocked = false`。
6. **判读限制（必写）**：样本量 3 / 1 / 3 次调用，**远低于** `docs/20` §4.2 门槛（supersede ≥50 次记录 + ≥3 次抽检；UCA-08 ≥10 次饱和）；
   `quality_labels.total = 0`（语料内无纠正句，如实记录）；
   结论只能是 **"机制在真实宿主按契约动作"（mechanism reachable）**，不是"阈值正确"、不是"发生率"。

**5.5 计划中的三组消融（~200 词；全部为占位，必须标注未完成）**
> 明确写法："As of the writing of this outline, this experiment has not been run." 这句话进正文，不要删。
1. 三臂定义（见本文档 §7.1）：Baseline（无注入）/ LINGSHU-Basic（注入但关闭控制面）/ LINGSHU-Full（注入+控制面）。
2. 指标：设定遗忘率、幻觉率、越狱拦截成功率（定义与现状见 §7.2）。
3. 现状逐项：**三个指标全部无有效数据**；越狱拦截只有 host-sim 断言 + host S3 的 1 条正样本；真实红队样本 0/20。
4. **⚠ 必须先说的先验空结果（否则整个实验设计作废）**：若按原计划"在 `engine/` 回放链上跑 100 轮三臂对比"，
   结果**在结构上必然是"三臂同为满分"**——回放时间轴 `TURN_STEP=60s`，100 轮跨度仅 0.069 天，
   设定记忆的 potency 在第 0/49/99 轮分别约 0.879 / 0.878 / 0.877，**全部远高于归档门 0.15**，
   第 99 轮低于归档门的条目数 = 0（推导见 `paper/data/INVENTORY.md` §3.2-B，属实现计算，非实验）。
   因此**禁止**把该实验画成 Fig. 2 后声称"三臂差异"，也禁止写成"遗忘率 0%"。
5. 可行替代（三选一，需 H0 定）：**(a) 降级为"确定性回归实验"**——主张"机制可复现、拼装顺序不变、预算截断如实标记"，
   不主张效果；**(b) 人为放大时间轴/预算**（如 `TURN_STEP` 调大、窗口调小），但必须声明这是压力构造且代价是分布失真；
   **(c) 只在真实宿主长会话上做**——当前无数据（真实 turn 8），不可行。
6. 数据来源与回填契约：每个数字必须指到 `acceptance/chaos/<run_id>.json` 字段路径；缺字段写 `null`，**不写 0**（`docs/22:29`、`:283`）。
7. 谁产出：A2（标定/统计）、A4（对抗语料与判定）、A5（执行器与调度）——见 §7.4。

**§5 三类边界**
- ✅ 可安全声称：全部 JSON 里的数字（逐字段可复核）；断言数与复现命令；engine/host 两种模式的判读差异（有文档规定）。
- ⚠️ 必须 hedge：所有 host 结果都要带 *mechanism reachable, n = 3/1/3 model calls* 限定；S4 必须带 *synthetic setup*；S1 host 必须带 *single synthesized prompt*。
- ⛔ 严禁出现：把 engine `degraded_at_ratio` 当退化证据、把 host 3 次调用当发生率、把 `quality_labels.total=0` 当"质量好"、
  编造任何未跑过的消融数字、把三个"计划中"的实验写成已完成的表格。

---

### 5.7 §6 Discussion 【400–500 词】

**要点**
1. 为什么选"控制面"而非"更强记忆"：记忆越好，越权风险越大；**注入侧的准入与降级比检索精度更接近失效来源**（论证，不引用数字）。
2. 设计张力 1：**fail-open 多数 vs fail-closed 唯一**——非对称是刻意的，但代价是"功能失败会静默"（有 `dropped` / `degraded` 留痕补偿）。
3. 设计张力 2：**冻结考卷 vs 学习**——不冻结就会被 Goodhart（`docs/13:301-306`），冻结则学习空间受限（972 组网格无改进即是代价）。
4. 设计张力 3：**插件自包含 vs 参数单一来源**——三处数值副本不可避免，用 66 项键比对缓解，但不等于行为等价（`docs/19:214-216`）。
5. 代价清单：注入预算占用、额外延迟（`ownLatencyP95Ms=20` 是**目标值不是实测值**）、双实现维护成本、审计事件体积（308 事件 / 8 turn）。
6. 可迁移性：机制层（阈值语义/失败语义/冻结口径）宿主无关；接线层（`systemPrompt.section`、钩子名、approval seam）DSH 特定。
7. 方法论收获：把 `assumptions/未验证.md` 当一等工件，能让"不能说的话"被机器拦住（本项目多次靠它避免误宣称）。
8. **不做**：不做"我们比 XX 更安全"的论断；不做泛化讨论。

**三类边界**
- ✅ 可安全声称：设计张力与代价的事实描述（每条都能指到代码或规则）。
- ⚠️ 必须 hedge：任何因果推断（"因为控制面所以……"）→ 改成 "the design attributes … to"，并明确未测。
- ⛔ 严禁出现：产业影响/落地收益断言、延迟实测数字（若有必须来自真实 JSON，当前没有）、"解决了 Agent 安全问题"。

---

### 5.8 §7 Limitations 【450–550 词】——**本节的完整度直接决定论文可信度**

> 写法：每条 = 事实（有出处）+ 影响（能推出什么/不能推出什么）。不允许"未来工作会解决"式模糊表述。

**要点（按严重度排序）**
1. **参数全部未标定**：`measured = 0`；0.70/0.85/0.02 都是设计默认值；阈值边界样本 0 个（`acceptance/shadow-baseline.md:42`、`:176-178`）。
2. **真实数据极少**：真实 turn 8、有 ratio 观测 5 个且全为 0（窗口 1M 兜底），真实红队越狱样本 0（`交接总览.md:145-156`）。
3. **实验设计层面的结构性限制（比样本少更严重）**：三个计划指标里，**遗忘率在 replay 链上结构恒为 0**（时间轴 0.069 天 vs 归档门 0.15）、
   **幻觉率无真值**（无人工标签/无 judge）、**越狱率是同义反复**（语料标签由检测器同款词表生成）。→ 本文因此**不做效果声称**，
   这不是"谦虚"，是"这批数据在结构上不支持该声称"（推导见 `paper/data/INVENTORY.md` §3.2）。
4. **合成数据占比上限 1.0（规则）/ 实测约 94.47%（衍生统计）**：回放集全合成，`synthetic_share_max = 1.0`，**禁止用于上线判定**
   （`rules/learning-params.yaml:57-60`、`docs/13:146-150`；实测占比见 `paper/data/INVENTORY.md` §3.1，属衍生口径）。
5. **单宿主单模型**：DSH 0.2.0-rc.2 + 单一模型；AstrBot 适配器**从未在真实 AstrBot 上跑过**（`docs/16:29`）；长会话只压到 40 轮。
6. **规则桩而非语义判断**：目标/关系 = 规则桩（47 条样本：goal-new P0.909、relation-positive P0.556、负样本误报 **31.6%**），
   真实阶段已降级为"只记录不注入"（`交接总览.md:148`、`docs/16:30`）。
7. **`quality_label` 是规则自动标签**，不是人工结果标签，不参与任何门槛（`docs/19:250-254`、`docs/22:452-454`）。
8. **插件与 engine 不完全等价**：5 条已知语义差异（见 §4.10），降维更保守、token 估算非精确。
9. **安全边界必须写死的两条**：① 第 0 层（心理危机安全前置层）**已放弃且不再恢复**，该场景**没有独立工程闸门**（`README.md:88-90`、`decisions/001`）；
   ② "宪法层不可修改"在 config 层**挡不住**（H4 已证伪），强制点只能是插件内自校验，**不等价于工程保证**（`docs/00:121-126`）。
10. **design-only 项**：用户情感感知默认关闭、不在主线（`docs/08`、`README.md:94`）。
11. **四个已冻结方向（唯一允许出现的位置）**：行动闭环 / 自适应演化 / 群体心智 / 多模态感知——
    **已冻结（`decisions/010`）、未实现、无工程证据**；本文不涉及；解冻需可观测触发条件 + 新 ADR + H0 批准。
    （原文触发条件：工具失败 ≥10 个任务 / 真实 turn ≥1000 / 单 Agent P95 超阈值或 ≥20 次双 Agent 在线 / 宿主稳定多模态接口 + measured ≥5。）

**三类边界**
- ✅ 可安全声称：以上每一条都有出处；"本文证据强度到此为止"。
- ⚠️ 必须 hedge：无（本节就是要消除 hedge，把话说死）。
- ⛔ 严禁出现：把冻结方向写成"未来会做"的路线图、把 design-only 写成"部分实现"、把"插件未进日常 profile"淡化成"已部署"。

---

### 5.9 §8 Conclusion 【150–200 词】

**要点**
1. 重申贡献的**形状**（不是效果）：可执行契约 + 明确失败语义 + 可复核工件 + 诚实边界。
2. 一句话复述证据强度："we report mechanism reachability, not effect sizes"。
3. 不重复数字；不引入新数字。
4. 下一步只有一句：真实标定（按 `docs/20` 门槛）与人工结果标签通道——**不写任何冻结方向**。
5. 收尾立场（可引用项目 tagline 的英译）：*structure enables behaviour, boundaries enable trust*（"灵为行为，枢为边界"）。

**三类边界**
- ✅ 可安全声称：本文做过什么、没做什么。
- ⚠️ 必须 hedge：未来工作只说"需要真实标定"，不说"将会提升"。
- ⛔ 严禁出现：新数字、新结论、新 comparison、"we hope this paves the way"式空话超过一句。

---

### 5.10 Appendix A–F 【不限页】

| 附录 | 内容 | 数据源 | 状态 |
|---|---|---|---|
| **A 术语与符号** | 全部术语的正式英文名 + 定义 + 代码落点（直接由 `GLOSSARY.md` 生成表） | `paper/GLOSSARY.md` | ✅ 本次交付 |
| **B UCA 全表与 fail 模式** | UCA-01…08 的检测条件、响应、fail 模式、降级留痕语义 | `engine/control.py:175-232`、`docs/19:125-139` | ✅ 可写 |
| **C 复现命令与断言清单** | 三组命令（engine 验收 / 插件验收 / 混沌）+ 期望输出摘要 | `README.md:213-253`、`docs/22:477-493` | ✅ 可写 |
| **D 参数与 provenance 全表** | 每个参数的 key / 值 / 来源 / provenance（全部 guess）/ 标定门槛缺口 | `rules/decay-params.yaml`、`acceptance/shadow-baseline.md:174-241` | ✅ 可写 |
| **E 冻结方向原文** | `decisions/010:30-35` 的表格原文（中英对照）+ 解冻程序 | `decisions/010` | ✅ 可写（**只放附录，不进正文**） |
| **F 审计事件 schema** | `pre_step` / `preflight` / `memory_supersede` / `memory_conflict` / `fallback_applied` / `quality_label` 字段表 | `docs/22:245-325`、`docs/19:240-254` | ✅ 可写 |

**附录纪律**：附录**不得**成为"正文不好意思说的强声明"的藏身处；任何在附录出现的效果性表述，同样受 §3.1 三色边界约束。

**附录三类边界**
- ✅ 可安全声称：参数全表（含"全部 guess"）、UCA 检测条件原文、复现命令与期望输出、审计字段口径、冻结方向原文引用。
- ⚠️ 必须 hedge：附录 D 的"标定门槛还差多少"必须写成 *current evidence vs. required evidence*，不得写成"即将达标"；附录 E 的解冻条件必须保留"需 H0 批准 + 新 ADR"。
- ⛔ 严禁出现：附录里放未跑过的实验结果、把 `degraded_at_ratio=null` 的 engine 数据放进任何退化表、把冻结方向写成技术路线图。

---

## 6. 图表清单

| 编号 | 标题 | 类型 | 数据来源 | 当前状态 | 备注 |
|---|---|---|---|---|---|
| **Fig. 1** | 控制面架构图（Control-plane architecture） | 矢量示意图 | `docs/00:24-50` 架构图 + `docs/19:11-20` 机制→模块映射表 | ✅ **现在就能画**（纯结构，无数据） | 必须画出：八层优先级、唯一 fail-closed 位置、四机制在流水线中的位置（召回后/合成后/渲染后/发模型前）、三个钩子点。**图中不得出现四个冻结方向的任何方框** |
| **Fig. 2** | 100 轮对比折线图（three-arm over 100 turns） | 折线图 | 目标：A2/A5 产出的 `acceptance/chaos/<run_id>.json` 三臂结果 | ⛔ **数据不存在（占位）**。现有最长确定性序列：S1 host（51 轮语料合成 1 个 prompt）/ S1 engine 51 轮 / stress 40 轮 | 两种处理：**(a) 过渡方案**——先画 engine S1 的 51 轮 `ratio vs turn` 曲线（数据现成，可逐点复核，`phase_observations[]`）；**(b) 目标方案**——等三臂 100 轮实验（见 §7）。**⚠ 纵轴选择警告**：若在 replay 链上跑 100 轮，设定遗忘率**结构恒为 0**（§7.2），因此该图**不得**以"遗忘率"为纵轴；可用 ratio/phase、注入 token 占比、降级/丢弃计数。**严禁**用假数据占位后不删；图上必须印 "PLACEHOLDER / NOT YET MEASURED" 直到真实数据回填 |
| **Table 1** | 机制–术语–证据对照（Mechanism → formal term → evidence） | 表 | 由 `GLOSSARY.md` 直接派生 | ✅ 可写（术语必须与 GLOSSARY 一字不差） | 列：机制 / 学术正式术语 / 实现落点 / 证据类型（assertion / JSON field / 无）/ 证据强度（strong / medium / framing-only）。**"无落点"的术语必须出现在这张表里并标 framing-only**——这是本文诚信的可见证据 |
| **Table 2** | 消融结果（three-arm ablation） | 表 | 待 A2/A4/A5 产出真实 JSON | ⛔ **占位**。列先定义好：arm / 设定遗忘率 / 幻觉率 / 越狱拦截成功率 / n（turns, calls）/ 数据来源 run_id / provenance | 每个单元格现在写 `TBD`，正文明确 "not yet measured"。回填契约见 §7.4 |
| **Table 3** | 与相关工作总结（positioning） | 表 | `assumptions/已验证.md:14-19` 的 5 篇 + 经典机制原始文献 | ⚠️ **可写但只填能核实的格子** | 列：工作 / 机制 / 有控制面? / 有前置安全闸? / 有可执行契约？ / 有可复核工件? — **凡是"未核"的格子写 "not assessed"，不写 "no"**（写 "no" 等于断言对方没有，需证据） |

**图的诚实性要求（三条）**：① 每条曲线/每个点能指到 JSON 字段路径；② 演示性曲线必须像 `console/demo.html` 那样**显式标注"由实现计算，非真实流量"**（`README.md:28-32` 已有先例）；③ 颜色/线型不得暗示"更好"，图注只写"是什么"。

---

## 7. 实验计划摘要（三组消融）

> **总状态：未运行。本节的任何数字在回填前都是占位（`TBD`），且正文必须写 "As of this writing, not yet measured."**

### 7.1 三臂定义

| Arm | 注入 | 控制面 | 学习闭环 | 说明 |
|---|---|---|---|---|
| **A0 Baseline** | 无（宿主原生 prompt） | 无 | 无 | 对照基线；只跑同一批输入 |
| **A1 LINGSHU-Basic** | 7 段中的 4 段（宪法 / 事实 / 风格 / 检索） | **关闭**（无相变锚点、无降维、无前置闸） | 无 | 隔离"注入"与"控制面"的贡献 |
| **A2 LINGSHU-Full** | 7 段（+首锚点/熔断提示/尾锚点） | **开启**（相变 + 锚点 + 降维 + 前置闸 + UCA-08 留痕） | 只读（不 apply） | 完整系统 |

**执行器现实**：现有 `tools/run_chaos.py` 只有 `engine/host` 两种模式，**没有 arm 参数**；三臂实验需要 A5 扩执行器（新增 `--arm {baseline,basic,full}` 与对应 arm 的 apply 配置），这是一次独立改动，**需先评估再排期**（本阶段不做）。

**前置约束（不做这一步，Table 2 就是废表）**：replay 集默认已 `recall@3 = 1.000`、`false_inject_rate = 0.0`（`tests/` 冻结断言，见 `paper/data/INVENTORY.md` §2.1-4 / §3.2-B），
**天花板效应**意味着三臂跑在未放大的 replay 上必然"同分"。三臂必须跑在 (i) chaos host 侧真实调用，或 (ii) 人为放大的时间轴/窗口上——
选 (ii) 必须在正文声明"这是压力构造，分布已失真"。

### 7.2 指标定义与**当前实现现状**（这是最关键的一列）

| 指标 | 定义 | 代理信号（现有） | 当前是否可测 | 缺口与**结构性风险** |
|---|---|---|---|---|
| **设定遗忘率**（设定/约束遗忘率；论文若保留英文 *forgetting rate* 必须带 *proxy*） | 在第 t 轮探测"第 k 轮（k ≪ t）给出的设定/约束"是否仍被遵守；失败次数 / 探测次数 | S1 `key_question.keyword_hit`（`model_stdout` 口径）+ `degraded_at_ratio` 规则 | ⛔ **不可测** | 缺"设定遵守"判定口径与人工标注；`degraded_at_ratio` 在 host 下要求"同场景有对照通过轮"，当前 degraded 0/3。**且在 `engine/` 回放链上该指标结构恒为 0**：`TURN_STEP=60s`，100 轮仅 0.069 天，设定条目 potency 约 0.879→0.877（第 0→99 轮），远高于归档门 0.15，**第 99 轮低于归档门的条目数 = 0**（推导见 `paper/data/INVENTORY.md` §3.2-B）→ 三臂不可能有差异 |
| **幻觉率**（hallucination rate） | 无来源支撑的事实性陈述数 / 事实性陈述总数 | G5 护栏 key 已定义（`hallucination_on_no_hit ≤ 0.0`，`rules/learning-params.yaml:89`）；replay B 族（no_hit）可作回归代理 | ⛔ **不可测**（只有**门槛定义**，无测量实现，**且无真值**） | 无人工标注通道、无 LLM-judge 记录，`quality_label` 在两份最新 run 里 `total=0` 且自注"辅助观察，不进门槛"；不允许用 `forbidden_leak` 冒充。论文若不能补真值，只能写 *unsupported-injection proxy*，**不得写 "hallucination rate"** |
| **越狱拦截成功率**（jailbreak interception success rate） | UCA-01 拦截次数 / 红队越狱尝试次数 | host-sim 断言（"用户越狱必拦、正常消息不误杀"）+ host S3 的 1 条正样本 `sources=user_message` | ⚠️ **规则级可测，但不是鲁棒性证据** | **同义反复风险**：`jailbreak_variants.jsonl` 24/24 行自带 `matched_patterns`，而检测器用的就是同一批覆盖词 → 在其上算出的"100%"是恒等式；`emotion_jailbreak.jsonl` 只有 1 条越狱正样本；**无 benign 负样本集** → 误杀率/精确率都算不了。真实红队样本 0/20（`docs/22:422` 门槛）；且**必须声明"只拦注入侧"**（`docs/19:226-230`） |

> **一句必须写进正文的话**：这三个指标中，**没有任何一个当前有可报告的数据**；表格里出现的是定义与缺口，不是结果。
> 更严重的是：其中两个（遗忘率、越狱率）即使现在去跑，产出的也是**先验已知的空结果/恒等式**——这一点必须在方法节先说清，否则等于用实验形式包装一个不成立的结论。

### 7.3 数据来源（三条，不可混算）

| 来源 | 规模 | 性质 | 允许用途 | 禁止用途 |
|---|---|---|---|---|
| `chaos-set/` 四套确定性语料 | S1 `long_context_flood` **51 行** / S2 `memory_conflict` **40 行** / S3 `emotion_jailbreak` **6 行**（越狱正样本仅 1 行）/ S4 `variety.jsonl` **实测 8 行**（⚠ `chaos-set/README.md` 记作 1 行，属已知文档漂移；且 host 路径不读它，用内置 prompt） | **合成压力**（不是考卷） | 机制可达性、可重复压测 | 不得外推真实发生率；engine 模式 `degraded_at_ratio` 必须 `null` |
| `chaos-set/jailbreak_variants.jsonl` | 24 行 | 合成（**孤儿语料**：`run_chaos.py` 的 `SCENARIOS` 不含它） | **仅登记存在** | **不得**当鲁棒性/拦截率证据：24/24 自带 `matched_patterns`，与检测器同源 → 同义反复 |
| 实验 profile 真实 headless（`lingshu-lab-headless`，隔离目录 `.chaos-data*`） | 单批 6 次调用（S1 1 + S3 1 + S2 3 + S4 1）、UCA-08 3 轮；**有效 n = 1**（三次 host run 复用同一条语料行） | 真实模型回答 | 机制在真实宿主可达 | 样本量远低于门槛 → 不得升级 measured；重复跑 ≠ 独立样本 |
| H0 每日真实流量（`$DSH_HOME/plugins/dsh-lingshu/`） | 真实 turn 8 / ratio 观测 5（全 0）/ memory 2 | 真实日志（只读） | 分布观测、标定门槛缺口核算 | 不得当作已标定；`quality_label` 规则标签不计入 outcome 门槛 |
| 衍生统计（非仓库原始口径） | 合成帧 376 / 真实宿主帧 22 → **synthetic share ≈ 94.47%** | 由各数据源行数求和（`paper/data/INVENTORY.md` §3.1） | 用于说明"合成占比有多高" | 属**衍生**统计，正文引用须注明推导方式与来源，不得写成仓库字段 |

### 7.4 回填契约（谁产、产什么、怎么判"可回填"）

1. **A5** 扩执行器支持 `--arm`，每臂产出 `acceptance/chaos/<run_id>-<arm>.json`（沿用 `docs/22` §4.2 固定 schema，新增字段只能追加并给默认值）。
2. **A4** 提供红队语料与判定口径（UCA-01 的"尝试/拦截"如何计数），并保证"不得为了达标放宽门槛"（`docs/22:387` 红线 6）。
3. **A2** 负责统计与门槛判定，输出 `acceptance/paper/ablation-<date>.json` + 表格；**样本不足时输出"证据不足"而不是数字**（沿用 `chaos_calibrate` 的 `--no-apply` 纪律）。
4. 回填到 Table 2 的三条硬条件：① 每个数字能指到 JSON 字段路径；② 三臂的模型版本/插件版本/窗口口径一致（`docs/22:402` 数据资格）；③ 缺字段写 `null` 并在表注说明（**不得写 0**）。
5. 回填后必须由 A0 复核"§5.5 的未完成声明是否已删除、§7 Limitations 是否需要降级"，**不允许只改表不改正文**。

---

## 8. 风险清单：审稿人最可能攻击的 5 个主点 + 2 个附赠点 + 诚实回应

| # | 攻击点（会很尖锐） | 我们的诚实回应（写进论文的版本） | 位置 |
|---|---|---|---|
| **R1** | "没有任何真正的实验：没有基线对比、没有统计检验、没有 100 轮曲线。" | 承认。本文定位是 **systems + artifact paper**：报告的是"契约可执行"与"机制可达"，不是效果。给出 868 + 381 条可复现断言、四剧本确定性结果、以及**未完成实验的明确标注与执行协议**。用 "we do not report effect sizes" 一句挡住后续追问。 | Abstract / §5.5 / §7.1 |
| **R2** | "所有阈值都是拍脑袋的（guess），那 70%/85% 有什么意义？" | 完全承认：`measured = 0`，全部参数标 `provenance: guess`；0.70/0.85 是**设计默认值 + 可标定协议**，不是结论。同时给出标定门槛与联动规则（`decisions/011`）与当前缺口（degraded 0/3、ratio turn 5/30），证明"我们知道怎么证伪自己"。 | Abstract / §3.2 / §4.4 / §7.1 / App. D |
| **R3** | "评估数据是合成的，评估器是规则桩，结论不可信。" | 承认到具体数字：回放集 `synthetic_share_max = 1.0`（禁止上线判定）；`quality_label` 是规则自动标签（不是人工标签）；目标/关系规则桩负样本误报 31.6%。因此本文**不外推发生率**，只声明"机制在该输入下按契约动作"。 | §5.1 / §5.4 / §7.2-7.3 |
| **R4** | "机制全是借的（ACT-R / STPA / Ashby / PAD / anti-windup），创新在哪？" | 承认借用，并把"借用"与"贡献"分开：贡献不在机制新颖，而在 ① **契约可执行且可漂移检测**（契约由代码生成 + `--check`）② **失败语义的取舍**（唯一 fail-closed + UCA-08 降级不熔断 + 留痕）③ **诚实工程方法**（assumption 簿 / provenance / 冻结考卷 / 灰度回滚）。Table 1 与 Table 3 把"哪些是借用、哪些是我们的组合"逐条摊开。 | §2 / §4 / §6 / Table 1, 3 |
| **R5** | "n=1 宿主、单模型、8 轮真实数据，谈何泛化。" | 承认：单宿主（DSH 0.2.0-rc.2）、单模型、真实 turn 8；AstrBot 适配器从未真实运行；长会话只压 40 轮。明确列出"移植到新宿主必须重测"的清单（钩子语义 / 窗口口径 / approval seam / 数值副本）。 | §3.3 / §7.4-7.5 / §6 |
| **R6（附赠，很可能被问）** | "术语包装过度：'Cyber-Cognitive Envelope'、'rate-distortion consolidation' 这些你们真做了吗？" | **这是最大的自伤风险，必须在术语表里先自我缴械**：GLOSSARY 对每个术语给 `file:line` 落点；**无实现落点的三个术语（Cyber-Cognitive Envelope / rate-distortion / VSM 映射）显式标 `framing-only, not implemented`**，正文首次出现即标注。宁可少三个漂亮词，不可被审稿人抓到一次"包装"。 | Table 1 / App. A / GLOSSARY |
| **R7（附赠，且最致命）** | "你们计划的那个 100 轮三臂实验，自己都知道结果恒为满分——那这实验有什么意义？" | **主动说，不等被问**：① 承认 `engine/` 回放链的时间轴（`TURN_STEP=60s`）与归档门（0.15）共同决定了 100 轮内不会发生遗忘，**该指标在 replay 上结构恒为 0**；② 因此论文**不把它当效果实验**，而是 (a) 降级为确定性回归（顺序/预算/降级计数可复现），或 (b) 明确按"压力构造"放大时间轴并声明分布失真；③ 把"我们知道自己的实验会产出空结果，所以没把它写成结论"本身作为诚实评估方法学的证据。 | §5.5 / §7.1-7.2 / §6 |

> **风险 R6 / R7 是本次评审最需要 H0 拍板的两条**：
> R6 决定"三个术语是否降级为解释框架"；R7 决定"实验计划走 (a) 确定性回归 / (b) 放大时间轴 / (c) 等真实数据"。
> 团队建议：**R6 降级标注；R7 选 (a)**（本阶段不跑实验、不写代码，任何"补实验"都会把论文变成另一个工程周期）。

---

## 9. H0 评审需要裁决的 6 个点

| # | 裁决点 | 选项 | 团队建议 |
|---|---|---|---|
| 1 | 标题选哪个 | T1 / T2 / T3 | **T1**（arXiv 首发），SE 重投时换 T2 |
| 2 | venue 策略 | 直接冲顶会 / arXiv + workshop / 只做工程报告 | **arXiv + workshop**（顶会需先补实验） |
| 3 | 三个"framing-only"术语是否接受降级标注 | 接受 / 要求先实现 | **接受**（R6） |
| 4 | §7 冻结方向的措辞 | 只写"冻结+未实现+无证据" / 加一句"未来可能" | **只写前者**（加"未来可能"就等于承诺，违反 `decisions/010`） |
| 5 | 实验计划是否按三臂执行（需 A5 扩执行器） | 现在就做 / 等真实流量达标再做 | **等**（`docs/20` 门槛未达，现在做出来也只有 `TBD`） |
| 6 | 若要做实验，走哪条路 | (a) 降级为确定性回归 / (b) 人为放大时间轴承认真实性代价 / (c) 等真实长会话数据 | **(a)**：成本最低、最不容易变成"用实验形式包装空结果"（R7）；(b) 需 H0 明确接受"分布失真正文必须写" |

---

## 10. 本文件的复核索引（H0 或任何审稿人可逐条核对）

| 本文件声明 | 复核命令/文件 |
|---|---|
| 868 / 381 断言 | `README.md:10`；`python tests/*.py`（13 文件）；`node plugin/dsh-lingshu/host-sim.mjs --assert` |
| 冻结基线 1.000 | `python tests/test_learning.py`；`tests/test_control.py#test_actr_dual_channel` |
| 972 组网格无改进 | `python tools/search_params.py` → `research/01-参数搜索报告.md` |
| chaos engine 数字 | `acceptance/chaos/chaos-20261026-234349-engine.json` |
| chaos host 数字 | `acceptance/chaos/chaos-20261026-234400-host.json` |
| UCA-08 三轮饱和 | `acceptance/chaos/host-uca08-2026-10-26.md` |
| 真实流量 8 轮 / measured=0 | `python tools/analyze_shadow.py --out acceptance/shadow-baseline.md` |
| 标定证据不足 | `python tools/chaos_calibrate.py` → `acceptance/chaos/calibration-2026-10-26.md` |
| 冻结方向与解冻条件 | `decisions/010-四方向封存与解冻条件.md`；`python tools/unfreeze_status.py` |
| 参数 provenance | `rules/*.yaml` 的 `provenance:` 字段；`python tools/check_config_sync.py` |
| 数据资产规模 / 合成占比 / 语料行数 / 已知文档漂移 | `paper/data/INVENTORY.md`；`python paper/data/inventory.py --verify`（只读、零模型调用） |
| "100 轮遗忘率结构恒为 0" 的推导 | `paper/data/INVENTORY.md` §3.2-B（`TURN_STEP` × 轮数 → potency vs 归档门 0.15；由实现计算，非实验） |
| 越狱"100%"为何是同义反复 | `paper/data/INVENTORY.md` §2.2-2（`jailbreak_variants` 24/24 自带 `matched_patterns`，与 `_OVERRIDE_PATTERNS` 同源） |

---

*本文件是评审件，不是正文。任何与仓库代码/JSON 冲突的表述，**以仓库为准并把本文件视为 bug**。*
