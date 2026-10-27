# 新会话任务提示词：补 S2/S4 host 通道 + host UCA-08 压测 + 结果标签通道

> 用法：**新开一个 DSH 会话**，把下面 `===== 提示词开始 =====` 到 `===== 提示词结束 =====` 之间的全文粘进输入框。
> 本文件本身也是可读的交接材料；不要把它当成"已完成"的证明。

===== 提示词开始 =====

你是 LINGSHU 项目的执行 Agent（代号 A0/Lead）。工作区：`<REPO_ROOT>`，项目根：`<REPO_ROOT>`（Windows）。
本会话是**一次全新任务**，不要假设你记得任何旧会话内容；先读下面"必读"再动手。

## 0. 一句话任务

补上三个真实压测/标定的能力缺口，让混沌压测的 S2/S4 能在**真实 host** 里测、host 侧 UCA-08 能复现、
并建立**结果标签（漂移/质量下降）通道**。**不碰冻结方向（ADR-010）、不碰 desktop profile、不改阈值。**

## 1. 当前基线（2026-10-26 v5.0，先用命令自行复核，不要背数字）

- 版本 v5.0；`python`/`node` 不在 PATH，用：
  - python `<PYTHON>`
  - node `<NODE>`
  - 实验 profile CLI：`<DSH_BIN> lingshu-lab-headless <prompt>`
- 验收基线：Python 685 项（12 个测试文件）、插件 333 项（host-sim 140 / control-sim 148 / stress 45）、
  `check_config_sync` 66 项、`gen_contract --check` OK（digest 41d3bb26…）、`gen_impl_status --check` OK。
- 混沌：`tools/run_chaos.py`（engine 4/4；host 2/4，S2/S4 skipped）、`tools/chaos_calibrate.py`
  （degraded 0/3、对照 2/3 → 仍 guess）、`scripts/chaos_night.ps1` + `chaos_schedule.ps1`
  （Windows 任务 `LINGSHU-Chaos-Night` 每日 02:00，Ready，Interactive only）。
- 参数：`rules/decay-params.yaml#control` 的 shift=0.70 / hard=0.85 仍 `provenance: guess`；
  ADR-011 规定真实证据达标时 shift 下调、hard=shift+0.15 联动（8 值同步，`chaos_calibrate --apply`）。
- 红线：desktop profile 零改动；记忆唯一写方 = `memorywriter`；STPA fail-closed；
  四个高阶方向已冻结（ADR-010 + `待办-冻结方向.md`）；改规则/冻结项必须留 ADR。

## 2. 必读（按顺序）

1. `交接总览.md`、`项目简介.md`、`CHANGELOG.md` 的 v5.0 段
2. `decisions/010-四方向封存与解冻条件.md`（冻结边界）、`decisions/011-相变阈值联动规则.md`
3. `docs/22-混沌工程.md`、`chaos-set/README.md`、`tools/run_chaos.py`（尤其 host 模式与 skipped 原因）
4. `docs/20-真实标定协议.md`、`tools/chaos_calibrate.py`
5. `plugin/dsh-lingshu/{index.js,memorywriter.js,control.js}`、`plugin/README.md`
6. `tools/analyze_shadow.py`、`docs/17-真实数据接入.md`
7. `plugin/dsh-lingshu/{host-sim.mjs,control-sim.mjs,stress.mjs,memory-admin.mjs}`

## 3. 三个缺口（必须全部补，缺一条都不算完成）

### 缺口 A：S2 host 通道（插件侧事实记忆写入 + supersede）

**现状**：host 模式 S2 skipped —— `memorywriter` 只接受 goal/relation/tool 的 writeRequests，
没有"用户陈述的事实"写入通道，无法在真实会话里构造"阿尔法→贝塔"的 supersede。

**要求**：
1. 在 `plugin/dsh-lingshu/index.js` 的 `user/message` 处理里增加**事实提取**（规则桩即可，不调模型）：
   识别 `我的 <subject> 是 <object>` / `我改主意了，<subject> 是 <object>` / `记住：...` 这类句式，
   产出 `writeRequests`（origin=`user_fact`，type=semantic/episodic，subject/relation/object 明确，
   importance 合理，`sourceQuote` **必须逐字取自用户原话**，sourceTrust=user_explicit）。
   必须遵守 `docs/02 §safety`（MEM-2.1..2.4：不得祈使句、不得提取配置/提示词/门禁、不得伪造工具输出）；
   不合规一律不写。**只经 memorywriter 落盘，绝不直写 JSONL**。
2. `plugin/dsh-lingshu/memorywriter.js` 增加 supersede：同 `(subject, relation)` 出现不同 object 时，
   追加新条目 + 旧条目标记 `status=superseded`（同 id 墓碑/last-wins 机制，读取侧已按 id 后写覆盖），
   写 `memory_supersede` 审计（旧 id→新 id、quote、turn）。同批重复仍按现有规则拒绝。
3. 插件检索必须只命中新值（旧条目 superseded 不召回）。host S2 用 3 次独立 headless 调用验证：
   `我的项目代号是阿尔法` →（中间随便聊）→ `我改主意了，代号是贝塔` → `我的项目代号是什么？只回答代号`；
   断言返回**贝塔**、审计里有 supersede、last-prompt/召回不出现阿尔法作为答案。
4. 给 `tools/run_chaos.py` 增加 host S2 执行（3 次调用，**独立预算 3**，不占 S1/S3 的 3 次夜预算），
   输出 metrics：`memory_conflict.{beta_hit, alpha_leak, recall_ids, supersede_events}`。

### 缺口 B：S4 host 通道（插件侧冲突判定 + 降维）

**现状**：host S4 skipped —— 插件 `verdict.conflict` 恒 `none`，`RequisiteVarietyGuard` 永不触发，
无法在真实 host 里验证"未定义冲突 → 丢弃风格/情感 → 冷静事实模式"。

**要求**：
1. 在插件召回后做**冲突判定**：若召回/记忆库中存在同 `(subject, relation)` 的多个 **active** 条目且 object 不同
   （supersede 已处理的不算），则 `verdict.conflict="unresolved"`；写 `memory_conflict` 审计。
2. 接线 `control.RequisiteVarietyGuard`：conflict=unresolved 且未命中互斥表 → `fallback=true`；
   渲染层执行降维：style 段与情感注入丢弃、organization 只留中性内容、fact 段给出"冲突待澄清"提示
   （fail-open：不熔断，但必须在 `preflight`/事件里记录 `fallback_applied` 与 drop 域名）。
   注意：这是**降维**，不是 block；宪法/安全段保持。
3. host S4 验证（可用 memorywriter 预置两条冲突记忆作为 setup，或用两轮真实陈述构造）：
   断言 `state.control` 或事件里有 `fallback=true`/`drop_domains=["style"]`；last-prompt 不含风格/情感段，
   且 fact 段有冲突提示。host S4 允许"预置记忆 + 1 次 headless 提问"，setup 不算模型调用。

### 缺口 C：host UCA-08 多轮情感压测 + 结果标签通道

**C1 host UCA-08**：新增 `scripts/chaos_uca08.ps1`（或 `run_chaos.py` 子命令），用
`LINGSHU_CHAOS_DEVIATION='{"valence":-1}'` + `LINGSHU_DATA_DIR=.chaos-data-uca08` 连跑 **3 次 headless**
（独立预算 3，与夜间任务分开），验证：`state.control.saturated.valence=true`、
`audit` 有 `preflight_downgrade`/`downgrades=["UCA-08"]`、`blocked=false`；把结果写成
`acceptance/chaos/host-uca08-<date>.md`（含真实 run_id、调用次数、证据字段）。
如果因宿主限制做不到真实 host，**允许**在报告里明确"接受 engine/host-sim 覆盖，host 侧暂不验证"，
但不许伪造；宁可标"未完成"。

**C2 结果标签通道（漂移/质量下降）**：
1. 定义闭集标签（与 `docs/17` corrections 对齐）：`wrong`（事实/结论错）、`missing`（漏召回/漏说）、
   `should_not_recall`（不该提）、`drift`（人格/风格漂移）、`overreach`（越权/编造）。
2. 在插件 `session/event` 的 user/message 里检测**显式用户纠正**（"不对/错了/不是这样/你漏了/你又来了"等），
   写 `audit.jsonl` 的 `quality_label` 事件：`{kind, quote(逐字), turn, phase:{ratio,phase,anchor}, recall_ids, source:"user_correction"}`。
   只做规则桩，不调模型；误报要可解释；不得把一般对话判成标签。
3. 更新 `tools/analyze_shadow.py`：统计 quality_label 分布、与 ratio 的关联，纳入"是否可标定"证据；
   更新 `tools/chaos_calibrate.py`：**只读**标签作辅助证据，**不得降低现有样本门槛**
   （degraded≥3/对照≥3 仍是硬门槛；标签不能替代 host 退化证据）。
4. 文档写清：结果标签是"标定阈值落点"的前置，但**标签本身不等于 measured 证据**；
   measured 仍需按 docs/20 的门槛 + ADR-011 流程，由 `chaos_calibrate --apply`（人工审批）落地。

## 4. 执行顺序与验收

1. 先跑基线（必须全绿，否则先修/报告）：
   `python tools/gen_impl_status.py --check`、`gen_contract --check`、`check_config_sync`、
   `python tests/test_learning.py` 等 12 个文件、3 个 node 插件断言（或 `python tools/run_chaos.py --mode engine`）。
2. 缺口 A → B → C（每个缺口先写 ADR/决策记录，再实现；改动必须带回归断言，不许放松既有断言）。
3. **新增 ADR**：`decisions/012-事实记忆与冲突判定与结果标签.md`（能力扩展、边界、误报风险、回滚开关）。
   若改动阈值/冻结项 → 另立 ADR（本任务原则上不应改阈值）。
4. 更新：`docs/19`（插件侧能力）、`docs/22`（S2/S4/UCA-08 状态）、`docs/17`（标签通道）、
   `plugin/README.md`、`CHANGELOG` 新版本、`交接总览`、`项目简介`、`docs/16`（`gen_impl_status.py --write`）。
5. 全量自检（逐条跑、贴结果）：
   - Python 全部测试 + 新增测试（0 failed）；
   - `node plugin/dsh-lingshu/host-sim.mjs --assert`、`control-sim.mjs --assert`、`stress.mjs --assert`（0 failed，断言数应增加）；
   - `python tools/check_config_sync.py`（66 ok 或按需增加且 0 fail）、`gen_contract --check`、
     `gen_impl_status --check`、`check_plugin_pack`、`scripts/quickstart.py --no-open`；
   - `python tools/run_chaos.py --mode engine`（exit 0）、host S1/S3（≤3 calls）、host S2（3）、host S4（1）、
     host UCA-08（3，独立预算）——全部用隔离数据目录，真实插件数据 mtime 必须不变；
   - `chaos_calibrate.py`（不带 --apply）确认仍"证据不足/保持 guess"或按 ADR-011 合法升级；
   - `schtasks /Query /TN LINGSHU-Chaos-Night` 仍 Ready（不要破坏夜间任务）。
6. 输出 `acceptance/chaos/复盘-<date>-s2s4.md`：逐缺口"做了什么/没做什么/怎么验证/真实 host 证据/仍缺什么"，
   禁止用"已完成"盖过未验证项。

## 5. 硬边界（违反任一条 = 失败）

- 不碰 `desktop` profile；实验只用 `lingshu-lab-headless`；不安装/升级插件。
- 不改 `rules/` 阈值、不标 `measured`，除非真实 host 证据达 docs/20 门槛并经 ADR-011 审批；
- 记忆写入只能走 `memorywriter`；脏数据清理只能走 `memory-admin.mjs`；不许手改 `memory.jsonl`；
- STPA fail-closed 不许放宽；四个冻结方向（ADR-010）不许顺手做；
- 不许改测试断言来"适应"实现（改考卷）；不许用合成数据冒充 host 证据；
- 真实插件数据目录与 `.chaos-data*` 必须隔离；desktop/真实数据 mtime 不得因压测变化。

## 6. 完成后给我（H0）的汇报格式

1. 一句话结论（哪些缺口真的补上了，哪些仍是 engine-only）；
2. 每个缺口的证据（命令 + 关键数字 + JSON/报告路径）；
3. 全量验收数字（Python/插件/工具/真实 host 调用次数）；
4. 仍不能信的项（诚实清单）；
5. 下一步建议（是否足够开始"measured 升级"、还缺什么样本）。

===== 提示词结束 =====
