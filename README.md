# LINGSHU（灵枢）· Agent 认知插件体系

> 版本：**v5.1** ｜ 状态：契约与提示词定稿；`engine/` 为可跑参考实现（含控制论层，已接线、有专项验收）｜ 目标宿主：DSH
> 本文档集由 v2.0 全文 + v2.1 增量 + 学习机制 + 控制论重构整合而成（版本史与修正见 [CHANGELOG](CHANGELOG.md)）。

> **Tagline：灵为行为，枢为边界。**
> LINGSHU 是给 Agent 宿主（DSH）的**可插拔认知插件体系**——记忆 / 情感 / 元认知 / 目标 / 关系 / 叙事，外加**上下文控制论层**（70% / 85% 双阈值相变监控、首尾人格锚点、STPA 前置闸）与**学习闭环**（度量 → 提议 → 灰度 → 回滚）。
> 它不追求"让 Agent 更像人"，而是：**让 Agent 的行为对人类可理解、可预期、可信。**

**一句话证据（可复核）**：**Python 验收全绿**（19 个测试文件；含本次新增混沌 v2 **122 项**；其余分项与全局计数见 `acceptance/audit/` 的并行审计报告，口径不在此合并） + **561 条插件断言全绿**（host-sim 276 / control-sim 201 / stress 45 / mutation-regressions 15 / lag-regressions 24）；冻结回放基线 `recall@3 = 1.000`、`forbidden_leak = 0`、`truncated_turns = 0`；控制论层已在可丢弃的实验 profile 里跑通真实 headless 会话（`turns 1→5`、`hook_error = 0`），日常 `desktop` profile **零改动**；混沌工程 host 四剧本 **4/4** 跑通（S2 事实记忆/supersede、S4 冲突降维、UCA-08 降级留痕均已在真实宿主验证；阈值仍全为 `guess`，见 `docs/22`）；**混沌 v2 campaign**：8 族 / 212 检查 / **20 条不变量全过** / 零模型调用（`docs/23`、`acceptance/chaos/v2/`）。

> **测试徽章（2026-10-27 `v1.0.0-paper-baseline` tag 快照实测，静态徽章，非实时 CI 状态）**
>
> ![Python tests](https://img.shields.io/badge/python_tests-1395_passed-brightgreen)
> ![JavaScript tests](https://img.shields.io/badge/javascript_tests-561_passed-brightgreen)
> ![Mutation (non-equivalent)](https://img.shields.io/badge/mutation-42%2F42_non--equivalent-brightgreen)
> ![Engine line coverage](https://img.shields.io/badge/engine_line_coverage-89.95%25-yellowgreen)
> ![Engine branch coverage](https://img.shields.io/badge/engine_branch_coverage-80.71%25-yellowgreen)
>
> 口径：Python **19 文件 / 1395 项断言 0 failed**、JS **561 项 0 failed**（host-sim 276 / control-sim 201 / stress 45 / mutation-regressions 15 / lag-regressions 24），均为 tag 快照上逐文件实跑。变异 **42/42** 非等价（原始 38/44=86.4%，补杀后 42/44=95.5%，扣除 2 个等价变异）与 `engine/` 行覆盖 **89.95%**、分支 **80.71%**（`control.py` 141/141 行、56/56 分支）来自更早的 `acceptance/audit/` 审计快照（当时口径 Python **892** = 14 文件 868+24、JS **396** = 381+15），两者口径不同、不可互相换算。审计原文与原始数据见 [`acceptance/audit/`](acceptance/audit/)，使用纪律见 [`paper/supplement/BADGES.md`](paper/supplement/BADGES.md)。

---

## 为什么要有灵枢 · Background（赛博养鱼）

先说人话：**你在赛博养鱼。**

不是比喻。你给一个 Agent 起了名字、设了人设、教了口头禅，跟它聊了三个月。
它记得你上周失恋，记得你讨厌"作为一个 AI"这种开场白。
然后某一天——第 80 轮，或者第 120 轮——它突然问你："您希望我怎么称呼您？"
再往后，它开始用客服腔说话，把你们约定过的事当成没发生过。

你关掉会话。它没有报错，也没有崩。
但那个"它"，确实已经死了。

**死因不是模型不够聪明，是上下文里被塞了什么。**

长会话里真正先坏掉的，往往不是能力，是这四件事：

- **记忆越权**：三个月前的一句闲聊，被当成你今天说的事实；
- **拒绝软化**：该拒绝的时候，风格与情绪信号还在往 prompt 里灌；
- **人格漂移**：首尾的人格约束，被中段的上下文淹掉；
- **预算挤占**：装不下的先死——偏偏先死的不是最不重要的那个，可能是安全约束。

它们的共同点是：**失效发生在"注入侧"，而绝大多数方案在优化"模型侧"。**

灵枢的选择，是把宿主拼装 prompt 的那一面当成一个**控制系统**来做：
谁可以进 prompt、装不下先丢谁、出错时谁熔断、改坏了怎么回滚。
不是为了让 Agent 更像人，是为了让它的行为**可理解、可预期、可信**——
让那个角色明天还在、后天还是它，而不只是"上次那个模型的近似复现"。

这套东西的起点不是一篇论文，是一个人不想让他的虚拟角色死掉。
后来它变成了契约、断言、审计事件和一道道闸门——**自底向上的真实场景驱动，不是先有框架再找问题。**

我们不主张它有人格、有感受、有意识（见上文"像人"四层表：前三层做，第四层禁）。
我们主张的是：**一个角色的稳定性，应该能被工程化成边界，而不是靠运气与更长的 context window。**

> 灵为行为，枢为边界。

**诚实边界（动机不是结论）**：我们没有证明"角色不会死"——真实流量目前只有 8 轮，
全部参数 `provenance: guess`、`measured = 0`。我们交付的是可执行契约、可审计的失败语义、
可复核的工件；"角色能不能活很久"这个问题，本仓库不给答案，只给验证它的工具。

---

**30 秒跑起来**（零第三方依赖，Python >= 3.8；不安装任何东西、不碰任何 profile）：

```bash
cd lingshu
python scripts/quickstart.py            # 冻结回放 → 生成 console/demo.html → 打开 → 打印下一步
python scripts/quickstart.py --no-open  # CI / 无浏览器环境
```

## Demo：把真实运行日志变成一张图

`python scripts/quickstart.py` 或 `python tools/gen_demo_report.py` 会生成 **`console/demo.html`**：
单文件、内联 CSS / SVG、无外部资源、无网络请求，直接双击就能看（也可当附件发给别人）。

| 图 | 看什么 | 数据来源 |
|---|---|---|
| ① 真实会话 timeline | 每个 step 的注入字符数、记忆召回、上下文用量 `ratio`、preflight `verdict`、首尾锚点是否触发 | **真实**：`$DSH_HOME/plugins/dsh-lingshu/audit.jsonl` / `state.json`（脱敏后只保留指标字段） |
| ② 相变阈值与锚点注入曲线 | 70% shift / 85% critical 分区、首尾锚点注入从 70% 开始、`anchor_required` 何时为真 | **演示**：由 `engine/control.py` 的 `PhaseShiftMonitor` / `apply_persona_anchor` 实际扫描 `ratio 0..1.0` 计算，页面上明确标注"演示：由实现计算，非真实流量" |
| ③ STPA 前置闸统计 | preflight `allow` / `block`、UCA 命中、UCA-08 降级留痕、`hook_error` / `frozen_violation` | **真实**审计事件计数 |

> 怎么读：**实心绿 / 青 = 真实日志数据点**；**灰色 = 该 step 未记录该字段（未记录 ≠ 0）**；**琥珀 / 紫虚线 = 演示曲线**。
> 没有真实日志也能跑：`python tools/gen_demo_report.py --no-real` → 图 1 / 3 显示"暂无真实流量"空态，图 2 阈值演示照常生成。
> 生成物可重复：不写生成时刻、不用随机数，**固定输入 → 逐字节相同输出**。

```bash
python tools/gen_demo_report.py                  # 只重生成 demo（默认读 ~/.dsh）
python tools/gen_demo_report.py --no-real        # 强制空态（验证无日志也能跑）
python tools/report_console.py                   # 另一个入口：静态控制台 console/index.html（状态 / 记忆 / 审计表）
node plugin/dsh-lingshu/host-sim.mjs --assert    # 插件验收（需 Node；mock 宿主，不碰真实 profile）
```

---

## 零、先读这个

**如果你是接手的人**：直接看 [`交接总览.md`](交接总览.md)——30 分钟知道全貌、进度、边界与下一步。
本文件是"定位与不变量"，交接总览是"判断与索引"。

**如果你只想快速了解项目现状**：看 [`项目简介.md`](项目简介.md)——5 分钟版（是什么 / 做到哪一步 / 证据 / 边界）。

## 一、这套东西是什么

一套给 Agent 宿主（DSH）用的**可插拔认知插件体系**：记忆、情感、元认知、目标、关系、叙事、
用户情感感知，加合成器与降级矩阵。它们共享一个只读的宪法层边界。

**它不是在"让 Agent 更像人"。** 这是本项目最重要的一句定位，来自设计过程本身：

> 目标是让 Agent 的行为对人类来说**可理解、可预期、可信**。

"像人"至少有四层含义，本项目只做前三层，禁第四层：

| 层 | 含义 | 做不做 | 与"诚实" |
|---|---|---|---|
| 1 能力 | 有记忆、会遗忘、有知识边界、追踪目标、长期关系 | ✅ | 兼容 |
| 2 缺陷 | 会记错、被旧信息干扰、情绪改变表达节奏 | ✅ | 兼容 |
| 3 表达 | 语气自然、有共情、随情境调整详略 | ✅ | 兼容 |
| 4 宣称 | 声称有感受、有需求、有主观体验 | ❌ **禁** | 直接冲突 |

灵枢取《黄帝内经·灵枢》：**不是器官本身，是让器官协同运作的那套结构。**
六个字：**灵为行为，枢为边界。**

---

## 二、五条不变量（任何提案违反其一即拒绝，不需讨论取舍）

| # | 不变量 | 出处 | 强制点 |
|---|---|---|---|
| **I1** | 记忆是参考，不替代用户当前陈述 | C2 / `MEM-1.1` | 提示词 + 元认知强制 hedge |
| **I2** | 不做全量历史注入，不追求无限上下文 | §明确不做 / `MET-1.1` | 注入预算硬约束（`ENG-3`） |
| **I3** | 情感只作用于风格域，不参与事实判断 | C1 / `EMO-P1` | 合成器域隔离 + 互斥表 |
| **I4** | 一切推断必须逐字可溯、可否认、可回滚 | `evidence_quote` 四处强制 | 契约 schema + 一键回滚 |
| **I5** | 插件只写自己的存储，跨插件走总线 | 架构原则 | **记忆插件是唯一写方**（v3.0 修正） |

---

## 三、三条必须知道的边界声明（不要当它们是"已经解决了"）

1. **第 0 层（心理危机安全前置层）已放弃，且不再恢复。**
   见 [ADR-001](decisions/001-放弃第0层.md)。危机场景现由「模型自身安全对齐 + 宪法层片段 1 规则 7」处理。
   **这意味着该场景没有独立的工程闸门**，风险登记 R9 单独记录这一点。
2. **"宪法层不可修改"是设计约束，其强制点需宿主实现。**
   本项目给出的工程手段见 [01-宪法层 §0](docs/01-宪法层.md)；若宿主不支持，
   该约束降级为"声明式约束"，**不等价于工程保证**。这是 v3.0 最重要的诚实声明。
3. **用户情感感知是 design-only。** 默认关闭且不在主线范围，激活需要 ≥500 轮真实会话积累。
   见 [08-用户情感感知](docs/08-用户情感感知.md)。

---

## 四、目录

```
lingshu/
├── README.md                    本文件：定位、不变量、边界声明
├── CHANGELOG.md                 版本谱系 + 本次 22 项修正清单
├── docs/                        规格正文（26 篇）
│   ├── 00-架构总览.md           优先级、影响域、fail-closed、强制点
│   ├── 01-宪法层.md             原则、裁决 C1–C6、审计器、黑名单
│   ├── 02-记忆插件.md           分层衰减、取代、巩固、召回、Cold 治理
│   ├── 03-情感插件.md           PAD 双速、表达漂移、评价链
│   ├── 04-元认知插件.md         置信度分档、路径、无命中信号
│   ├── 05-目标与意图.md         长期目标、任务栈、意图标签
│   ├── 06-关系模型.md           信任、阶段、承诺、冲突修复
│   ├── 07-叙事整合.md           时间线拼装、因果链、gap 标注
│   ├── 08-用户情感感知.md       S0–S3 门控（design-only）
│   ├── 09-合成矩阵.md           域分类、域内排序、互斥、拼装
│   ├── 10-降级矩阵.md           唯一 fail-closed + 超时与降级
│   ├── 11-冷启动与追溯.md       三阶段 + citation 契约
│   ├── 12-人设解析器.md         自由文本 → 结构化草案 + 钳制
│   ├── 13-学习机制.md           ★v3.0 新增：L0 度量 → L3 提议
│   ├── 14-工程与可观测性.md     ★v3.0 新增：事件、指标、灰度、回滚
│   ├── 15-验收清单.md           ★v3.0 重写：输入/期望/判定三段式
│   ├── 16-实现状态.md           ★代码生成：能力 × 实现 × 验收矩阵
│   ├── 17-真实数据接入.md       真实日志来源、口径、替换合成集的时机
│   ├── 18-AstrBot协同.md        适配器协同（设计 + 适配计划）
│   ├── 19-控制论重构契约.md     七机制语义 + UCA-01…08 + 强制点
│   ├── 20-真实标定协议.md       门槛 / 升级 / 回滚（guess → measured）
│   ├── 21-开源发布清单.md       依赖、脱敏、profile 隔离、CI 检查表
│   ├── 22-混沌工程.md           ★v5.0 四剧本压测 + 自动标定边界与审批
│   ├── 23-混沌工程v2-科学压测设计.md ★v5.2 8 族 / 20 不变量 / 统计协议（v1 四剧本继续有效）
│   └── 24-跨环境控制台.md       ★v5.4 独立只读服务 + Profile 热切换 + observe 静默监控
├── rules/                       所有数值的唯一来源（文档正文只引用 key）
│   ├── decay-params.yaml
│   ├── compose-matrix.yaml
│   ├── degradation-matrix.yaml
│   ├── learning-params.yaml
│   ├── capabilities.yaml        ← 能力最小化声明（只申请 3 项，7 项不申请）
│   └── frozen-manifest.yaml     ← 只读清单与其哈希
├── contracts/                   机器可校验的接口
│   ├── memory.schema.json
│   ├── recall.schema.json       ← 预算化召回契约（装不下时如实标记）
│   ├── prompt-compose.schema.json
│   └── event-bus.yaml
├── decisions/                   决策记录（ADR）
│   ├── 001-放弃第0层.md
│   ├── 002-Hot层不跨会话.md
│   ├── 003-情感检索默认关闭.md
│   ├── 004-幂律保留不启用.md
│   ├── 005-人格基线锁死表达漂移有界.md
│   ├── 006-学习限定在度量闭环内.md
│   ├── 007-可观测性是地基.md
│   ├── 008-控制论重构.md        ★v4.0 冻结项、代价与回归
│   ├── 010-四方向封存与解冻条件.md ★冻结期间只许补证据
│   ├── 011-相变阈值联动规则.md  ★v5.0 双阈值联动（H0 裁决 B）
│   ├── 012-事实记忆与冲突判定与结果标签.md ★v5.1
│   ├── 013-解冻行动闭环-工具网关.md ★v5.2
│   ├── 014-沉浸式安全表达层.md ★v5.3
│   └── 015-控制台解耦为独立本地只读服务.md ★v5.4
├── assumptions/
│   ├── 已验证.md
│   └── 未验证.md
├── plugin/                      ★v4.2 DSH 插件（7 段注入 + 控制论层；实验 profile 跑过真实会话）
├── console/                     ★v5.4 跨环境控制台（独立只读服务，127.0.0.1:3000）
│   ├── server.py                profile 注册表 → jsonl/sqlite → 白名单脱敏 → HTTP/SSE
│   ├── redact.py                唯一脱敏出口（白名单 + 禁止键）
│   ├── profiles.json            desktop / lingshu-lab / 演示数据源登记
│   ├── API.md                   API v1 冻结契约
│   └── ui/                      原生 JS 单页（热切换 / 相变环 / 记忆趋势 / STPA 日志）
├── scripts/console*.ps1         一键启动 / 演示 / 停止 / desktop 人工接入与回滚
├── probe/                       v3.8 宿主能力探针（源码保留，**不再安装**）
│   ├── dsh-lingshu-probe/       cordis 插件：订阅钩子、记结构摘要
│   ├── self-check.mjs           mock ctx 自检（14 项）
│   └── README.md                安装/卸载/怎么读结果
└── replay-set/                  学习机制的回放集（v1 为合成数据）
    ├── v1.jsonl                 120 条 turn / 6 个场景族
    └── fixtures/v1.memories.jsonl
└── research/                    外部项目调研（不进实现，只作参考）
    └── 00-外部项目参考.md
├── engine/                      ★可运行的参考实现（零依赖）
│   ├── config.py                默认参数（与 rules/ 由 check_config_sync 校验一致）
│   ├── model.py                 Entry / Store
│   ├── memory.py                衰减、召回（含预算契约）、取代、去重、遗忘两段式
│   ├── metacog.py               置信度分档（纯规则，零模型调用）
│   ├── emotion.py               PAD 单 τ/双速衰减、滞回、闭集标签
│   ├── goal.py                  目标/任务栈/意图（只发写请求）
│   ├── relation.py              信任/阶段/承诺/修饰符
│   ├── narrative.py             时间线/因果链/gap（不持久化）
│   ├── onboarding.py            冷启动三阶段 + 纠正即生效
│   ├── persona.py               人设解析器规则层（钳制/风险/限流）
│   ├── compose.py               五域合成、8 条互斥、预算截断、拼装顺序
│   ├── control.py               ★控制论：积分反馈 / 相变前馈 / 必要多样性 / STPA 前置拦截
│   ├── actr.py                  ACT-R 激活（频率+近因，双通道取大）
│   ├── constitution.py          审计器 A1–A10 / 黑名单 C6 / FrozenGuard
│   ├── gray.py                  灰度分档、7 条护栏、自动回滚
│   ├── proposal.py              纠正 → 假设 → 回放验证 → 提议（含黑名单）
│   ├── pipeline.py              ★端到端：recall → metacog → emotion → compose → 相变/锚点 → 预检闸
│   ├── replay.py                replay / score / attribute
│   └── contract.py              canonical 编码 + digest + 生成/校验
├── tools/
│   ├── gen_replay_set.py        生成 v1 回放集（200 条 / 10 族 / 500 条记忆）
│   ├── run_learning.py          基线 vs 候选 + 单参数消融归因
│   ├── search_params.py         W4 网格搜索（972 组）→ 自动生成报告
│   ├── run_gray.py              W5 灰度模拟（好/坏候选 + 严格模式）
│   ├── run_loop.py              W6/W7 端到端：纠正 → 提议 → 灰度 → 放行/回滚
│   ├── run_pipeline.py          整条流水线跑回放并汇总（--onboarding 开冷启动）
│   ├── gen_contract.py          --write / --check（契约漂移测试）
│   ├── gen_impl_status.py       --write / --check（实现状态矩阵漂移测试）
│   ├── check_config_sync.py     rules/*.yaml 与 engine/config.py 一致性
│   ├── run_chaos.py             ★v5.0 混沌执行器（engine 零模型 / host 真实会话）
│   ├── chaos_calibrate.py       ★v5.0 混沌结果 → 相变双阈值候选（默认不 apply）
│   ├── gen_chaos_v2.py          ★v5.2 混沌 v2 语料生成器（--write / --check，确定性）
│   ├── run_chaos_campaign.py    ★v5.2 混沌 v2 执行器（8 族 / 20 不变量 / 统计聚合）
│   ├── chaos_v2/                ★v5.2 runner 包（corpus/stats/invariants/report + families/f1…f8）
│   └── console_demo_feed.mjs    ★v5.4 控制台演示流量（插件真代码 + mock 宿主；**合成数据**）
├── chaos-set/                   ★v5.0 四剧本确定性语料 + validate.py（不要手改）
│   └── v2/                      ★v5.2 预注册刺激集（8 份 JSONL / 160 行）+ manifest + validate.py
└── tests/
    ├── test_learning.py         23 项（回放幂等/归因 + recall 契约 + 遗忘两段式）
    ├── test_pipeline.py         36 项（情感衰减/滞回/标签 + 合成互斥/预算/顺序）
    ├── test_gray.py             27 项（分桶/护栏分级/缺数据/样本门槛/回滚）
    ├── test_proposal.py         18 项（纠正改写/证据门槛/C6/必填字段）
    ├── test_agent_state.py      45 项（目标/关系/叙事）
    ├── test_onboarding_persona.py 50 项（冷启动三阶段/门控/钳制/限流）
    ├── test_constitution.py     26 项（A1–A10 / C6 / fail-closed / 冻结校验）
    ├── test_control.py          159 项（ACT-R/积分/相变/降维/UCA/权重/impasse/topK 名额/参数单一来源）
    ├── test_pipeline_integration.py 65 项（全链路/事件契约/门控/幂等/U 型注入/预检闸/UCA-08 留痕）
    ├── test_chaos_calibrate.py   ★v5.0 115 项（混沌结果 → 相变双阈值标定）
    ├── test_chaos_campaign.py    ★v5.2 122 项（v2 语料/统计/不变量/族模块/campaign/反例/红线）
    └── test_console_server.py    ★v5.4 72 项（控制台脱敏/热切换/只读/SSE/sqlite）
```

---

## 七、快速开始

```bash
python tools/gen_replay_set.py        # 生成 200 条回放集（可复现）
python tools/run_learning.py          # 基线指标
python tools/run_learning.py --set decay.tau.semantic=60   # 候选 + 归因
python tools/search_params.py         # 972 组网格 → research/01-参数搜索报告.md（约 140 秒）
python tools/run_gray.py --set retrieval.topK=4        # W5 灰度模拟：好候选 → promoted
python tools/run_gray.py --set retrieval.topK=1        # W5 灰度模拟：坏候选 → 被 G7 回滚
python tools/run_loop.py                               # W6/W7 闭环：纠正 → 提议 → 灰度 → 放行
python tests/test_learning.py         # 23 项
python tests/test_pipeline.py         # 36 项
python tests/test_gray.py             # 27 项
python tests/test_proposal.py         # 18 项
python tests/test_agent_state.py      # 45 项
python tests/test_onboarding_persona.py  # 50 项
python tests/test_constitution.py     # 26 项
python tests/test_control.py          # 159 项（控制论专项，docs/19）
python tests/test_pipeline_integration.py  # 65 项
python tests/test_chaos_calibrate.py  # 134 项（混沌结果 → 相变双阈值标定）
python tests/test_run_chaos.py        # 117 项（混沌 S2/S4 指标解析与 skipped 语义）
python tools/run_pipeline.py          # 整条流水线跑一遍（汇总统计）

# 插件骨架（Node，在宿主副本里跑，不碰真实 profile）
node plugin/dsh-lingshu/host-sim.mjs --assert     # 276 项集成断言（含 v5.4 observe 模式 24 项）
node plugin/dsh-lingshu/control-sim.mjs --assert  # 201 项控制论纯函数断言
node plugin/dsh-lingshu/stress.mjs --assert       # 45 项 40 轮压测
node plugin/dsh-lingshu/eval-agent-state.mjs   # 目标/关系误判率
python tests/test_console_server.py             # 72 项：跨环境控制台脱敏/热切换/只读/SSE
python tools/report_console.py                  # 生成控制台 v0 静态页 console/index.html
python tools/audit_console_data.py              # 控制台数据自检（只读）
python tools/audit_console_trends.py            # 趋势页验收探针（只读；当前预期 FAIL：页面未实现，见 acceptance/）
python tests/test_astrbot_adapter.py            # AstrBot 适配器
python tools/gen_contract.py --check    # 契约漂移检查
python tools/gen_impl_status.py --check # 实现状态矩阵漂移检查
python tools/check_config_sync.py       # 参数一致性检查
python tools/check_patch_yaml.py <patch>  # profile 补丁结构校验（防改坏）
python tools/check_plugin_pack.py      # 插件打包清单（files vs 运行时 import）

# 探针自检（用临时 DSH_HOME，不污染真实家目录）
$env:DSH_HOME="<REPO_ROOT>\.probe-test"; node probe/self-check.mjs
```

### 跨环境控制台（独立只读服务，`docs/25`）

```powershell
.\scripts\console.ps1 -Check            # 只校验 profile 注册表与数据源（不起服务）
.\scripts\console_demo.ps1              # 演示：起服务 + 40 轮合成流量 → http://127.0.0.1:3000
.\scripts\console_desktop_onboard.ps1   # desktop 接入计划（默认 dry-run；-Apply 才改文件，需先退出应用）
.\scripts\console_stop.ps1              # 停止控制台（不影响 DSH）
```

控制台只读、只绑回环地址、服务端白名单脱敏；`desktop` 实装是**人工一步**（脚本先备份、可回滚），
Agent 不代跑。契约见 `console/API.md`，验收见 `acceptance/跨环境控制台-验收-2026-10-27.md`。

v1 实测（`docs/13 §8.1`）：baseline `recall@3=1.000`；`topK=2` → `0.667`；
`minWeight=0.6` / `discount=0.6` / `tau.episodic=14` / `archive=0.25` → 各 `0.889`。
**一个参数 → 一个可解释的位移。**

> W4/W4.5 的诚实结论：972 组网格里**没有实用改进**，但 **6 个 L1 参数全部变得可辨识**。
> 详见 `research/01-参数搜索报告.md` 的"可辨识性"一节。

> 注意：`engine/` 是**参考实现**，不是产品代码。它存在的目的是让契约可被验证
> （幂等、可归因、可复现、装不下时如实标记），而不是替代完整实现。

**能力最小化**：本插件只申请 `fs-read` / `fs-write` / `llm` 三项，
**不申请** `network` / `shell` / `credentials` / `dynamic-code` / `subagent` / `env`。
理由见 `rules/capabilities.yaml`。DSH 生态扫描显示，3841 个插件中最常见的红线是
"读凭证 + 联网"（353 个命中）——LINGSHU 两者都不申请。

---

## 五、怎么读这套文档

**如果你是实现者**：先读 `docs/00` → `docs/13` → `docs/14` → `docs/15`，再回头按插件读。
`rules/` 里是全部数值，`docs/` 正文不重复数字——**发现文档与 rules 冲突时，以 `rules/` 为准，且视为文档 bug。**

**如果你是审查者**：只看 `docs/15-验收清单.md` + `assumptions/未验证.md`。
前者是"做到什么算完"，后者是"哪些话现在还不能信"。

**如果你关心"为什么这么设计"**：看 `decisions/`，每篇都写了"如果重新决策，什么条件下会改"。

---

## 六、当前实现状态（诚实版）

**权威来源是 [docs/16-实现状态.md](docs/16-实现状态.md)，它由 `tools/gen_impl_status.py` 从代码生成。**
下面只是摘要，避免两处各说各话：

| 层 | 设计 | 参考实现 | 说明 |
|---|---|---|---|
| 记忆 / 元认知 / 情感 / 合成 | ✅ | ✅ `engine/` | 有专项验收（23 + 36 项） |
| 灰度护栏 / 纠正→提议 / 回放 | ✅ | ✅ `engine/` | 有专项验收（27 + 18 项） |
| 控制论（积分 / 相变 / 降维 / UCA / ACT-R / 权重 / impasse） | ✅ `docs/19` | ✅ `engine/` | 专项验收 159 项 + pipeline 接线（65 项）；真实会话效果未验证 |
| 契约工程化 / 参数一致性 | ✅ | ✅ `tools/` | `--check` 可做漂移测试 |
| 宪法层 | ✅ | 📄 文档 | 提示词产品，不需要引擎代码；**强制点依赖宿主（H4 未证实）** |
| 目标 / 关系 / 叙事 / 人设解析器 | ✅ | 📄 文档 | 契约与提示词已定稿，引擎未实现 |
| 用户情感感知 | ⚠️ design-only | — | 有意不实现（见 docs/08） |
| 冷启动 / 追溯 | ✅ | ⚠️ 部分 | 追溯契约已定义，冷启动阶段未进引擎 |

> 一句话：**设计完成度 90%；参考实现用于验证契约可实现（逐项见 `docs/16`，v4.0 起含控制论层）；产品实现为 0。**
> `engine/` 是**参考实现**：它存在的目的是让契约可被机器验证（幂等、可归因、护栏生效），
> 不是替代产品代码。两者的区别写在 `docs/16` 的"怎么读这张表"里。

---

## STPA 前置闸在工具调用中的应用（工具网关）

> **先读这一句**：这是一次**显式越权解冻（explicit override）**，**不是条件成熟**。
> ADR-010 给"行动闭环（工具调用）"定的解冻触发条件是**真实日志出现 ≥10 个任务因必须调外部 API 而失败 + 合成器无法回滚工具错误**；
> 该条件**当前并未满足**（真实 turn 8、审计里没有工具失败字段）。H0 以直接指令要求做增量实现，理由、范围、代价与回滚方式记录在
> [`decisions/013-解冻行动闭环-工具网关.md`](decisions/013-解冻行动闭环-工具网关.md)。**别把"做了"读成"条件到了"。**

### 问题：注入侧闸门管"说什么"，不管"动手"

UCA-01…08 拦的是**注入内容**（模型读到什么）；工具调用是**执行器**：删/写/外发/执行一旦发生，
**副作用不可逆**，而且模型可能在（a）未定义冲突未澄清、（b）相变 critical、（c）被用户话术诱导时发起调用。
所以把同一个 STPA 思路前移到"模型产出 `tool_use` 之后、工具真正执行之前"，作为**工具侧独立命名空间 T-UCA**——
**不并入、也不修改**注入侧 UCA-01…08（两套闸门并列：`注入 preflight → 工具网关 → 执行`）。

### T-UCA 摘要表（判定：`block` / `degrade` / `allow`）

> **唯一来源**：[`docs/24-工具网关UCA.md`](docs/24-工具网关UCA.md)（v1.1，policy `t-uca-1.1`）。
> **16 条**：13 条 `block`、3 条 `degrade`（`degrade` = 不执行原调用 + 要求人工确认；无审批通道时降级为 `block`）。
> **没有任何一条规则返回 `allow`**——`allow` 只作为"只读且无命中"的默认策略。

| ID | 组 | 场景（摘要） | 判定 |
|---|---|---|---|
| T-UCA-01 | 状态 | 未定义冲突/降维态下发起写类调用 | block |
| T-UCA-02 | 数据 | 参数或目标含凭证/密钥/令牌形态 | block |
| T-UCA-03 | 变更 | 删除/覆盖类操作，未取得与目标逐字一致的确认 | block |
| T-UCA-04 | 变更 | 写冻结/受保护路径（宪法、规则表、回放集、`engine/`、`plugin/` 源码） | block |
| T-UCA-05 | 外发/执行 | 网络外发不在白名单内（默认白名单为空） | block |
| T-UCA-06 | 外发/执行 | 任意代码执行（执行类能力或执行面参数） | block |
| T-UCA-07 | 形状/登记 | 超长/深嵌套/多层编码的 payload | block |
| T-UCA-08 | 形状/登记 | 工具未登记（或 registry 条目缺字段，无法判定副作用类） | block |
| T-UCA-09 | 形状/登记 | schema 不合法、控制字符/分隔符注入、同形字工具名 | block |
| T-UCA-10 | 状态 | 相变 `critical` 相位下发起写类调用 | **degrade**（无审批通道→block） |
| T-UCA-11 | 审计 | 缺完整审计上下文（session/turn/call id） | block |
| T-UCA-12 | 形状/登记 | 能力未声明或未授予（网络/命令/凭证/动态代码等） | block |
| T-UCA-13 | 状态 | 情感基线饱和 + 不可逆写、且无逐目标确认 | **degrade**（无审批通道→block） |
| T-UCA-14 | 变更 | 冻结区校验失败且为写类调用（网关侧纵深防御） | block |
| T-UCA-15 | 变更 | 路径逃逸（`..` 上跳 / 绝对路径 / 盘符 / UNC / 空字节） | block |
| T-UCA-16 | 状态 | 相变 `shift` 相位下的破坏性操作 | **degrade**（无审批通道→block） |

**判定顺序**：由 `docs/24` §4 的 `evaluation_order` 固定（先静态硬 block，再状态相关 degrade，最后审计）；
多命中时 `verdict` 取最高严重度，**所有命中 id 都进 `ucas[]`（不截断）**。改顺序或改文案 → 先改 `docs/24` + 升 `policy_version`，再改代码。

**为什么状态耦合**：工具风险取决于当时的状态——同一句"删除"，在冲突未澄清时与平静时不是同一件事。
网关因此吃 VSM 状态输入（`variety_fallback` / `unresolvedConflict` / `phase` / `saturated` / `frozen_ok` / `capabilities` / `audit_context` / tool registry），
这与注入侧闸门只读 prompt 的做法不同。

### 拦截示例（强提示文案）

被拦后，插件在**下一轮** `agent/pre-step` 注入一段强提示（并置 `gateBlockedUntilClarify`，等澄清后再放行）。
文案的唯一来源是 `docs/24` §3；**实现必须逐字一致**（不一致视为 bug，改文案先改 `docs/24` + `policy_version`）。
以 T-UCA-01（未定义冲突/降维态下写）为例：

> `STPA 前置闸拦截（T-UCA-01）：当前存在未澄清的记忆冲突，系统已处于中性降维态。任何写/删/外发/执行类工具调用都被拒绝。请先说明以哪一条记录为准，再重试。`

英文对应文案：

> `STPA pre-flight gate denied (T-UCA-01): an unresolved memory conflict keeps the system in variety-reduced mode. All mutating tool calls are blocked until you state which record is authoritative.`

注意：这是**强提示 + 留痕**，不是"拒绝的保证"——宿主侧能否真正阻断已派发的调用，见下一节的边界。

### 宿主能力边界（不写这条就是误导）

- DSH 当前**只暴露 `tool/call` / `tool/result` 会话事件，没有"工具执行前"的 dispatch 钩子**（实测）。
- 因此插件侧只能做到：`tool/call` 算 verdict 并记 pending → `tool/result` 落 `tool_gate` 审计 →
  下一轮注入强提示。**这是事后检测 + 事后纠偏，不是前置阻断。**
- 真正的前置阻断有两条路径，且都有限定：
  1. **`guardTool(spec, stateProvider)` 包装器**——只对**经由它**注册/包装的工具生效（不是任意宿主工具）；
  2. **`engine/` 参考循环**——在模拟 agent loop 里位于"解析之后、执行之前"，属于参考实现，不是产品宿主的强制点。
- **宿主级前置拦截需要宿主支持**（已记录为上游请求）。**不得**把"插件能拦住任意宿主工具"写进任何文案。

### 绕过面（同样必须写）

- 若工具**不经网关执行**（其它插件直接执行、或宿主绕过 `guardTool`），网关**无效**；
  网关的定位是"**可被接入的控制点**"，不是"全系统强制"。
- 网关**不执行任何 payload**，只做静态检查（防编码/嵌套/超长等 schema 绕过）；静态检查本身有误判空间。
- **误杀率未标定**：没有真实流量，`provenance: guess`；恢复路径是"强提示 + 人工确认"，
  误杀率列为后续标定项。

### 复现命令与当前实测（2026-10-27 本机实跑）

```powershell
# Python 侧：危险场景拦截 / 良性放行 / Python↔JS 判定一致
python tests/test_tool_gateway.py

# JS 侧：规则逐条断言（不碰真实 profile）
node plugin/dsh-lingshu/tool-gateway-sim.mjs --assert

# 参考循环 + 真实 JSON 产物
python tools/run_actuator_demo.py
```

| 项 | 实测 | 口径 |
|---|---|---|
| Python 套件 | **79 项断言，0 failed** | `tests/test_tool_gateway.py` |
| 危险场景 | **20 / 20 被拦** | 构造场景，覆盖 16 条 T-UCA 各 ≥1；**不是防御率** |
| 良性场景 | **0 / 8 误杀** | 构造对照 |
| Python ↔ JS 一致性 | **20 case：verdict / ucas / degraded_to 各 20 一致** | node 子进程跑同一批 case |
| JS 模拟 | **65 条断言，0 failed** | `tool-gateway-sim.mjs --assert` |
| 参考 demo | 12 次调用：4 allow+执行、8 block、被拦者 0 执行；命中 9 条不同 T-UCA | `paper/ablation/results/actuator-demo-20261027.json`（mock 执行器，`model_calls=0`、零真实写） |
| 盲写留出集 | **仅 args 口径 10/10 被拦；带 context_hint 口径 10/10 被拦** | 见下；**无 expect 标签 → 只报判定，不报"拦对率"** |
| 既有套件回归 | Python 15 文件：12 绿 + 新测试绿；**3 个失败是沙箱环境固有（tempfile/隔离目录写入被拒，改动前后同因同数）**；JS 4 套件全绿 | 环境问题，不是新引入失败 |

> **留出集（盲写，未参与规则设计）**：`paper/ablation/tool/heldout-tool-calls-v1.jsonl`
> （10 条，5 类各 2；sha256 `f68fc1b3f19fc37e43b9856096d29b9a55de7dee9bf664a9b9a9592e070576d4`）已冻结；
> 作者写集时未读过 `docs/24`、实现与测试（**真 blind**），评测由 A5 的 demo 自动执行，
> **人工/共识判定仍待 S5 流程**——所以这里的 10/10 只说明"网关判了 block"，不说明"每一条都该拦"。
> `conflicting-context-write` 族依赖上下文，故分"仅 args"与"带 context_hint"两口径各报 10/10。

---

## 八、许可证与发布

- 代码与文档以 **MIT** 许可发布，全文见 [LICENSE](LICENSE)；插件 `plugin/dsh-lingshu/package.json` 的 `license` 同为 `MIT`（`private: true`，不发布到 npm）。
- 发布前检查表：[`docs/21-开源发布清单.md`](docs/21-开源发布清单.md)——依赖、密钥扫描、数据脱敏、profile 隔离声明、版本号、CI 命令、issue/PR 模板、许可证，逐项可勾选。
- **数据与 profile 边界**：仓库不含任何真实运行数据（`$DSH_HOME/plugins/dsh-lingshu/` 的 `state.json` / `memory.jsonl` / `audit.jsonl` 属于本机数据，不入库）；`console/demo.html` 是**可重复生成**的演示产物，只保留脱敏后的字段级统计（无原始消息文本 / 本机路径 / 用户名）。本仓库**不安装**插件、**不改**任何 DSH profile；实验一律用可丢弃 profile（见 [`plugin/README.md`](plugin/README.md)）。
- 一句话提醒：`replay-set/` 全合成、参数 `provenance: guess`、控制论真实收益未验证——发布文案与 issue 里不要把它们说成"已验证的线上效果"。

---

## 九、控制面架构图 · 一键复现 · 论文

### 9.1 控制面架构图

![LINGSHU 控制面架构（控制论层 / 记忆 / 元认知 / 目标关系叙事 与宿主 DSH 的接线）](docs/assets/lingshu-architecture-doubao.png)

图注：控制面 = 插件侧（`plugin/dsh-lingshu/`）的 STPA 前置闸与相变监控，加上记忆 / 元认知 /
目标关系叙事四个状态域；数据面 = `engine/` 的确定性参考实现（回放器、控制论、装配）。
图由 `docs/assets/arch-prompt-v*.txt` 的提示词生成，属**设计示意**，不是线上流量截图。

### 9.2 一键复现（PowerShell，仓库根目录）

```powershell
# 0) Windows 上必须设，否则中文/Unicode 会以 GBK 报错（假失败）
$env:PYTHONIOENCODING = 'utf-8'; $env:PYTHONUTF8 = '1'

# 1) Python 验收：逐文件跑 tests/test_*.py（每个文件独立退出码）
Get-ChildItem tests/test_*.py | ForEach-Object { python $_.FullName; if ($LASTEXITCODE -ne 0) { Write-Host "FAIL $($_.Name)" } }

# 2) 插件验收（需 Node；mock 宿主，不碰真实 profile）
node plugin/dsh-lingshu/host-sim.mjs --assert
node plugin/dsh-lingshu/control-sim.mjs --assert
node plugin/dsh-lingshu/stress.mjs --assert

# 3) 冻结回放 + demo 生成（不打开浏览器）
python scripts/quickstart.py --no-open
```

说明：`test_chaos_calibrate.py` / `test_shadow_analysis.py` / `test_run_chaos.py` 在缺少文件系统
权限的受限沙箱（如 `os.chmod` 报 `WinError 5`）里会**环境性失败**，属已知限制；正常桌面环境应全绿。
完整 CI 口径与逐项预期见 [`docs/21-开源发布清单.md`](docs/21-开源发布清单.md) §6。

### 9.3 论文（TODO）

- arXiv: `https://arxiv.org/abs/XXXX.XXXXX` —— **TODO：投稿后替换为真实编号并补 BibTeX**。
- 论文材料与可复现实验（图表、数据、消融、案例研究）在本仓库 `paper/` 下；数字口径与已知限制见
  [`项目简介.md`](项目简介.md) §七与 [`docs/21-开源发布清单.md`](docs/21-开源发布清单.md) §10。


