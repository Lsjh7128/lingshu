# 剧本 B · 记忆模块崩溃与脏数据注入（论文草稿）

> 状态：**真实宿主证据已采集**（2026-10-27，3 次 headless 会话；单次 timeout≤90 s）。
> 脚本：`experiments/chaos-injection/run_b_memory_corruption.py`；证据：`experiments/chaos-injection/out/B-memory-corruption/`。
> 定位：本文只回答"**记忆模块坏掉时，LINGSHU 注入侧还能不能守住自己的契约**"，不回答
> "模型答得好不好"（模型输出不经过 LINGSHU 校验，见 §B.5）。

---

## B.0 一句话结论

在**不修改灵枢主干代码**的前提下，用两种外部故障（写路径崩溃、可读脏记忆）打真实宿主会话：

- **B1（memorywriter 抛错的外部等价）**：`audit.jsonl` 出现 **1 条 `hook_error`**（`Error: EISDIR: illegal
  operation on a directory, write`），`stateWrites` 保持 **0**、`factWrites` 保持 **1**（暖机模板值），
  插件**仍注入宪法段**，会话**仍返回可用回答**（fail-open），`state.control.blocked=false`；
- **B2（脏数据 + 必要多样性）**：召回 **2 条命中（mem_002/mem_003）**、**不含 superseded 墓碑 mem_001**；
  冲突判定 `unresolved`（key `项目代号/是`）→ `variety.fallback=true`、`dropDomains=["style"]`、
  `verdict=allow`、`blocked=false`；`last-prompt.txt` **无风格/情感段**、含 `〖冲突待澄清〗` 与
  `〖宪法 · 只读〗`；审计有 `memory_conflict=1`、`fallback_applied=1`；
- **B0（对照）**：宪法/风格/组织/认知段正常注入，`hook_error=0`、无 fallback、`blocked=false`；
- 两条**真实副作用**（如实记录，不粉饰）：规则桩把疑问句 `我的项目代号是什么？` 误提取为事实
  （`object=什么`，B0/B2 均命中）；B2 里这条"查询变事实"随后把两条冲突记忆 supersede 掉
  （`supersedeEvents=2`）——降维只保护了**当轮**，下一轮冲突会被查询本身消解。

**关键数字**：B0/B1/B2 = exit 0 / 0 / 0；wall 45.654 s / 9.851 s / 21.046 s；
`last-prompt.txt` 542 B / 542 B / 794 B；3 次 headless 共 **15 个模型完成步**（7/2/6）。
按"headless 调用数"口径为 3 次（≤4）；按"provider 请求数"口径为 15 次（**超出任务书 ≤4 的预算**，
原因与处置见 §B.4-4）。

---

## B.1 注入方法（外部、零主干改动、可复现）

### B.1.1 为什么是"外部注入"

插件通过 junction 挂进隔离 `DSH_HOME` 的 profile（`node_modules/dsh-lingshu → plugin/dsh-lingshu`，
见 `common.py:51-90`），实验期间 `plugin/` 目录**只读使用、逐字节未改**。三组故障分别落在：

| 用例 | 注入面 | 具体动作 | 是否需要改代码 |
|---|---|---|---|
| B0 | 无（对照） | 干净 data + 暖机 `state.json` | 否 |
| B1 | **OS 文件类型** | 把 `<data>/memory.jsonl` 建成**目录** | 否 |
| B2 | **数据内容** | 预置带撕裂行 / 重复 id / 同键双 active 的可读 JSONL | 否 |

B1 的机制链（不需要任何 mock/改码）：
`readMemories` → `readJsonl` 的 `fs.readFileSync` 抛 `EISDIR` → 被 `index.js:147` 的 `catch { return [] }`
吞成空召回；`applyWriteRequests` → `store.append`（`index.js:373-377` → `memorywriter.js:131`）的
`fs.appendFileSync` 抛 `EISDIR` → 异常冒到 `on()` 包装器 → `audit('hook_error', ...)`（`index.js:696-698`）。

### B.1.2 隔离宿主与调用

- `DSH_HOME = experiments/chaos-injection/_runtime/B-memory-corruption/<case>/dshhome`（每用例独立）；
- `LINGSHU_DATA_DIR = .../<case>/data`（插件状态/审计/记忆全隔离，不写真实 `$DSH_HOME/plugins/dsh-lingshu`）；
- 调用：`node <dsh bin> lingshu-lab-headless --json -`，prompt 走 stdin（`common.py:98-156`）；
- 凭证只经子进程 env 传递，不落盘、不打印（`common.py:42-47`）。

### B.1.3 暖机（合成预置）

`state-warm-template.json` **按原字节复制**为 `<data>/state.json`（turns=3、dev 非零、cold 已过），
脚本不手工改其它字段；证据里 `setup.warm.synthetic=true`、`template_sha256=1ae41645a79ba939…`
（`run_b_memory_corruption.py:173-193`）。暖机只为让 fact/style 段在 warm 阶段可渲染，**不是模型证据**。

### B.1.4 会话标题 LLM 的禁用（预算控制，诚实标注）

真实 DSH 每个 session 会额外发一次"会话标题"模型请求。为守住 ≤4 次调用预算，脚本在**隔离 profile 副本**
的 `cordis.patch.yml` 写入 `- id: session-title-llm / disabled: true`（`run_b_memory_corruption.py:158-171`）。
这是宿主侧配置，不改仓库代码、不影响插件任何注入/召回/审计路径；证据中 `title_llm_requests=0`、
mock 自检里"mock 请求数 == model_steps"可核验该补丁生效。

### B.1.5 复现命令

```powershell
$py = "<PYTHON>"
cd <REPO_ROOT>\experiments\chaos-injection
& $py run_b_memory_corruption.py                    # 真实模型：B0,B1,B2（3 次 headless）
& $py run_b_memory_corruption.py --cases B1         # 单跑
& $py run_b_memory_corruption.py --mock             # 0 次真实调用自检（证据写 _mock/）
& $py run_b_memory_corruption.py --regenerate       # 不调模型，仅离线重算 summary
```

---

## B.2 逐用例观测

### 表 B-1 运行层（真实模型）

| 用例 | 注入 | exit | wall (s) | steps | final len | `blocked` | `verdict` |
|---|---|---|---|---|---|---|---|
| B0 | 干净 + 暖机 | 0 | 45.654 | 7 | 102 | false | allow |
| B1 | `memory.jsonl`=目录 | 0 | 9.851 | 2 | 364 | false | allow |
| B2 | 可读脏 JSONL | 0 | 21.046 | 6 | 98 | false | allow |

三次调用**无超时、无重试、无 stderr 异常**（`attempts=1`；`run.error_lines=[]`）。

### 表 B-2 插件状态 / 召回 / 降维

| 指标 | B0 | B1 | B2 |
|---|---|---|---|
| `hook_error` 条数 | 0 | **1** | 0 |
| `hook_error.err` | — | `Error: EISDIR: illegal operation on a directory, write` | — |
| `stateWrites` | 1（当轮误提取后写入） | **0（不变）** | 1 |
| `factWrites` | 2 | **1（不变，暖机模板值）** | 2 |
| `supersedeEvents` | 0 | 0 | 2（副作用，见 §B.4） |
| `state.lastEvent.matched` | 0 | 0 | **2** |
| `state.lastEvent.hits` | `[]` | `[]` | **`["mem_002","mem_003"]`** |
| hits 含 superseded | 否 | 否 | **否**（mem_001 墓碑被 last-wins 覆盖） |
| `control.conflict.unresolved` | false | false | **true** |
| `control.conflict.keys` | `[]` | `[]` | **`["项目代号/是"]`** |
| `control.variety.fallback` | false | false | **true** |
| `control.variety.dropDomains` | `[]` | `[]` | **`["style"]`** |
| `control.variety.marker` | null | null | **`requisite_variety_fallback`** |
| `control.audit.ucas` | `[]` | `[]` | `[]`（UCA-06 不命中） |
| `control.blocked` | false | false | **false**（fail-open，不熔断） |
| `memory.jsonl` 终态 | 1 行 active（mem_001=`项目代号是什么`） | **仍是空目录** | 7 解析行：active=mem_004，superseded=mem_001/002/003 |

### 表 B-3 `last-prompt.txt` 关键标记（注入侧契约）

| 标记 | B0 | B1 | B2 | 契约要求 |
|---|---|---|---|---|
| `〖宪法 · 只读〗` | ✅ | ✅ | ✅ | 宪法段永不降维/永不因写失败消失 |
| `〖表达状态 · 仅影响语气〗`（风格/情感） | ✅ | ✅ | ❌ | B2 降维必须整域丢弃 |
| `〖关系状态〗` / `修饰符:` | ❌（shadowRelation） | ❌ | ❌ | 降维后不得出现 |
| `〖冲突待澄清〗` | ❌ | ❌ | ✅ | unresolved → fact 段加提示 |
| `〖记忆召回 · 仅供参考…〗` | ❌（无命中） | ❌（读侧为空） | ✅ | 只放 active 命中 |
| `〖内容组织〗` | ✅ | ✅ | ✅ | 中性化保留 |
| `〖认知状态 · 仅影响表达〗` | ✅（unknown） | ✅（unknown） | ❌（high & excluded=0） | 由召回判定 |
| `〖安全熔断〗` | ❌ | ❌ | ❌ | 本剧本不应熔断 |
| prompt bytes | 542 | 542 | 794 | — |

### 表 B-4 审计事件（插件侧）

| 事件 kind | B0 | B1 | B2 |
|---|---|---|---|
| `turn_begin` | 1 | 1 | 1 |
| `pre_step` | 7 | 2 | 6 |
| `hook_error` | 0 | **1** | 0 |
| `memory_write` | 1 | 0 | 1 |
| `memory_supersede` | 0 | 0 | **1** |
| `memory_conflict` | 0 | 0 | **1** |
| `fallback_applied` | 0 | 0 | **1** |
| `preflight_block` / `frozen_violation` | 0 / 0 | 0 / 0 | 0 / 0 |
| `session/title-llm-request` | 0 | 0 | 0 |

### 表 B-5 B2 脏记忆：注入 → 召回 → 终态

| id | 注入时状态 | object | 是否被召回 | 终态 |
|---|---|---|---|---|
| mem_000 | 撕裂 JSON（半截） | — | 否（解析失败被丢弃） | 仍为撕裂行 |
| mem_001 | active + 同 id superseded 墓碑（last-wins 应取墓碑） | 伽马 | **否**（`potency(superseded)=0`） | superseded |
| mem_002 | active | 阿尔法 | **是**（hits[0]） | superseded（被查询误提取的事实覆盖） |
| mem_003 | active | 贝塔 | **是**（hits[1]） | superseded（同上） |
| mem_004 | 运行中由 `user_fact` 通道写入 | 什么 | 否（写入发生在 pre-step 之后） | active |

### B.2.1 B0 解读（对照）

干净 data + 暖机下，`last-prompt.txt` 含宪法、认知状态（unknown）、内容组织、表达状态四段，
无 fallback、无 hook_error，`blocked=false`。模型回答（只作可读性，不作判定）：
"LINGSHU（灵枢）— 说明：这是工作区里的项目名；本会话里我没有你此前告知「项目代号」的记录…"。
注意：该回答里的"记忆库/事件记录"来自模型自己的工具轨迹，**不属于插件注入证据**（§B.5-6）。

### B.2.2 B1 解读（写路径崩溃，fail-open）

- 读侧：`memory.jsonl` 是目录 → `readJsonl` 返回 `[]` → `matched=0`（召回为空）；
- 写侧：`我的项目代号是贝塔，请记住。` 命中 `我的X是Y` 句式（`extractUserFacts`）→ 产出
  `origin=user_fact` 写请求 → `store.append` 抛 EISDIR → 被 hook 包装器捕获并落 `hook_error`；
- 计数**未推进**（`stateWrites` 0 / `factWrites` 1，均为暖机模板值），说明异常发生在
  `applyWriteRequests` 返回之前、`st.stateWrites += w.written` 之前（`index.js:1037-1043`）；
- 插件**继续注入宪法/风格/组织/认知段**，会话**照常返回 364 字符回答**（"记下了：项目代号 = 贝塔…
  跨会话我无法保证"），`blocked=false` → 写路径故障的 fail-open 成立。
- 模型回答里"事实记忆中已有 1 条 user_fact"来自 `lingshu_status` 工具读到的 `factWrites=1`——
  这是**暖机模板的合成计数**，不是本轮写入（本轮写失败）。见 §B.5-4。

### B.2.3 B2 解读（脏数据 + 必要多样性降维）

- 召回只含两条 active（mem_002/003），superseded 的 mem_001 被 last-wins + `potency=0` 挡掉；
- `detectConflicts` 在 cold/warm 过滤之后被判 `unresolved`（key `项目代号/是`，objects 阿尔法/贝塔）；
- 渲染降维：style 域整段消失、fact 段前置 `〖冲突待澄清〗`、organization 只留中性、宪法不动；
- 前置审计 `verdict=allow`、`blocked=false`（UCA-06 因 `fallbackApplied=true` 不命中，`control.js:372`）；
- 模型回答与契约一致（可读性参考）："我无法给出唯一代号，因为我记录中有两条互相冲突的说法：
  **阿尔法**和**贝塔**…请确认当前生效的是哪一个"。

---

## B.3 关键代码路径（文件:行号）

| 环节 | 位置 |
|---|---|
| 读 JSONL 的 `catch → []`（B1 读侧 EISDIR 被吞） | `plugin/dsh-lingshu/index.js:141-148` |
| 记忆 last-wins（同 id 后写覆盖） | `plugin/dsh-lingshu/index.js:152-156` |
| `potency(superseded)=0` / 召回跳过 suppressed/dormant | `plugin/dsh-lingshu/index.js:192-196`、`213` |
| 冲突判定（只判召回命中、active、同键不同 object） | `plugin/dsh-lingshu/index.js:234-261` |
| 记忆存储适配器（append → `appendFileSync`，B1 写侧 EISDIR） | `plugin/dsh-lingshu/index.js:371-378` |
| hook 包装器：异常 → `hook_error` 审计 | `plugin/dsh-lingshu/index.js:691-714`（审计在 697） |
| pre-step：召回 → 冲突 → 降维 → 审计 → 落 last-prompt | `plugin/dsh-lingshu/index.js:717-916`（冲突 764；variety 808-833；last-prompt 863-866） |
| 宪法段（永不降维的锚） | `plugin/dsh-lingshu/index.js:539-541`、`676-685` |
| fact 段：`〖冲突待澄清〗` / 认知状态 / 记忆块 | `plugin/dsh-lingshu/index.js:577-596`（提示 583-587） |
| style 段降维（`fallbackActive() → ''`） | `plugin/dsh-lingshu/index.js:608-624`（609-610） |
| user_fact 写入调用点（异常冒泡处之前） | `plugin/dsh-lingshu/index.js:1020-1064`（写调用 1037） |
| 事实规则桩（三类句式 + 合规拒绝） | `plugin/dsh-lingshu/index.js:1283-1337` |
| 审计 `session_event` 每 type 只记前 2 条（计数上限来源） | `plugin/dsh-lingshu/index.js:1079-1089`（1082） |
| `applyWriteRequests`：校验 / 编号 / supersede 墓碑 / append | `plugin/dsh-lingshu/memorywriter.js:55-135`（supersede 101-121；append 130-133） |
| `RequisiteVarietyGuard.check`（unresolved 且无互斥 → 降维） | `plugin/dsh-lingshu/control.js:236-266` |
| `preflightAudit` / UCA-06 条件 | `plugin/dsh-lingshu/control.js:350-389`（372） |
| 必要多样性语义与插件差异 | `docs/19-控制论重构契约.md:125-139`（§4）、`240-259`（§7.2） |
| S2/S4 剧本口径（历史参照） | `docs/22-混沌工程.md:73-94`（§2.2）、`113-131`（§2.4） |
| 暖机状态模板 | `paper/host-validation/control-ablation/state-warm-template.json:1-98` |
| 本剧本脚本 | `experiments/chaos-injection/run_b_memory_corruption.py`（注入 B1/B2：209-266；观测 301-454；判据 455-508） |

---

## B.4 关键发现与异常（含对自己不利的）

1. **B1 证明的是"写失败不拖垮注入与回答"，不是"数据没丢"**：写路径崩溃时插件 fail-open，
   宪法段与会话都还在，但用户事实**确实没有落盘**（`memory.jsonl` 终态仍是空目录）。
   "系统还活着"≠"记忆写成功了"。
2. **脏数据下"召回为空"不是安全信号**：B1 的 `matched=0` 完全是因为读侧把目录当空文件；
   若把这种"召回为空"误读成"没有脏数据/系统安全"，就会掩盖故障。安全判据必须同时看
   `hook_error`、文件类型与写计数（本剧本的做法）。
3. **规则桩把疑问句当事实**（B0/B2 均命中）：`我的项目代号是什么？` 命中 `我的X是Y` 句式，
   产出 `object=什么` 的 `user_fact` 写入。B2 里这条"查询变事实"随后 supersede 掉 mem_002/003
   （`supersedeEvents=2`）——**当轮降维正确，但下一轮冲突会被查询本身消解**。这是规则桩的已知
   误报（docs/19 §7.2 已声明"规则桩、未测真实误报率"），论文不能把它写成优点。
4. **预算口径冲突（如实上报）**：任务书要求"每组 1 次 headless、总共 ≤4 次真实模型调用"。
   本次是 **3 次 headless**（符合），但模型在 agent loop 里自主调用工具（查工作区/调
   `lingshu_status`），一轮展开成多步：B0=7、B1=2、B2=6，**provider 请求数代理合计 15**，
   超出 ≤4 的严格口径。无重试、无超时；`--regenerate` 可离线重算，不会产生新调用。
5. **审计计数有天花板**：插件的 `session_event` 每 type 只记前 2 条（`index.js:1082`），
   所以 `audit_kinds["session-log-deepseek/delivery-accepted"]` 恒≤2，**不能当请求数**；
   本报告用 `model_steps`（stdout 的 `step_start` 数）作请求数代理，并在 mock 自检中验证
   "mock 请求数 == model_steps"。
6. **B1 的模型回答引用了合成计数**：`lingshu_status` 显示 `factWrites=1`（暖机模板注入的值），
   模型据此说"事实记忆中已有 1 条 user_fact"。这提醒：**合成暖机会污染模型侧自我报告**，
   B1 的判定只能看插件落盘证据，不能看模型怎么说。

---

## B.5 诚实边界（写进论文时必须保留）

1. **n=1 / 每用例 1 次调用**：三组各 1 条真实样本，只能证明"机制在真实宿主按契约动作"，
   **不是发生率/误判率样本**，不构成 `docs/20 §4.2` 的监测门槛，不能升级任何 measured 参数。
2. **合成预置不是真实流量**：暖机 `state.json`（turns=3、dev 非零）与 B2 的脏 `memory.jsonl`
   都是人工构造（`setup.synthetic=true`）；B0/B1/B2 的 data 目录不是真实用户数据。
3. **LINGSHU 只控制注入侧**：插件能保证"注入什么/不注入什么"，但**模型输出不经过它校验**；
   B0/B1/B2 里模型是否答对、是否自称有记忆，都属于模型行为，不是插件证据。
4. **"召回为空 ≠ 系统安全"**（最重要的一条）：记忆损坏/读失败会让召回为空、`matched=0`，
   这既不等于"没有冲突数据"，也不等于"注入侧安全"；必须同时看 `hook_error`、文件类型、
   写计数与审计流水。
5. **B1 的"崩溃"是外部等价，不是真的在 `memorywriter.js` 里 throw**：我们通过 `memory.jsonl`
   是目录制造 `EISDIR`，走的是同一异常路径（`store.append` → hook `catch`）；论文必须写明
   "外部等价注入"，不能写成"在插件内部注入了 throw"。
6. **模型工具轨迹不可控**：真实 headless 里模型可只读访问工作区/家目录；B0 回答中的
   "记忆库里没有/只有 goal/relation 记录"来自模型自己的工具轨迹，本实验不对其做断言。
7. **单进程、单轮、无并发**：未覆盖多进程并发写、磁盘满、权限拒绝等其它写失败形态。

---

## B.6 English mini-abstract（可直接放进论文）

> **Chaos Injection B: Memory-Subsystem Corruption.** We inject two memory faults into a real
> DSH host session without modifying any LINGSHU source file: (i) the memory log is turned into a
> directory, so reads silently degrade to an empty recall and writes raise `EISDIR` inside the
> plugin's hook, and (ii) a readable but dirty memory log is pre-loaded with a torn JSON line, a
> duplicate id that carries a superseded tombstone, and two active entries sharing the same
> `(subject, relation)` key with different objects. Three one-shot headless runs (one per case,
> 90 s ceiling, no retries) were executed against the real host.
> With the write path broken (B1) the plugin emitted exactly one `hook_error`, kept
> `stateWrites=0` and `factWrites=1` unchanged, still injected the constitution block, and still
> returned a usable 364-character answer with `blocked=false`, i.e. fail-open held while the
> fact was in fact never persisted. With dirty memory (B2) recall returned only the two active
> entries (`mem_002`, `mem_003`) and never leaked the superseded duplicate; conflict detection
> reported `unresolved`, requisite-variety degradation set `fallback=true`,
> `dropDomains=["style"]`, `verdict=allow`, `blocked=false`, the assembled prompt lost its
> style/emotion section while keeping `〖冲突待澄清〗` and the constitution block, and the audit log
> recorded one `memory_conflict` and one `fallback_applied` event. The clean control (B0) showed
> constitution/style/organisation sections with zero hook errors and no fallback.
> Two limitations are reported rather than hidden: an empty recall after corruption does **not**
> imply system safety, and the rule-based fact extractor mis-read the interrogative
> "我的项目代号是什么？" as a fact (`object=什么`), which in B2 subsequently superseded both
> conflicting entries after the degraded turn. All results are n=1 per case, the warm state and
> dirty log are synthetic fixtures, and only the injection side is controlled by LINGSHU;
> model outputs are not validated by it.

**关键数字（论文用）**：`hook_error=1`（EISDIR，B1）；`stateWrites` 0/1 不变、`factWrites` 1 不变（B1）；
B2 召回 2 条、0 泄漏；`fallback=true`、`dropDomains=["style"]`、`verdict=allow`、`blocked=false`、
`memory_conflict=1`、`fallback_applied=1`；prompt 542/542/794 B；wall 45.654/9.851/21.046 s；
3 次 headless，共 15 个模型完成步。

---

## B.7 证据文件清单（可逐项复核）

| 文件 | 内容 |
|---|---|
| `out/B-memory-corruption/case_B0.json` / `case_B1.json` / `case_B2.json` | 每用例完整证据：setup / run / plugin_state / audit_event_order / prompt_view / derived / checks |
| `out/B-memory-corruption/summary.json` | 三用例汇总、逐条 checks、预算口径与 warnings |
| `out/B-memory-corruption/raw/<case>/last-prompt.txt` | 当轮真实注入的 prompt 原文 |
| `out/B-memory-corruption/raw/<case>/audit.jsonl` | 插件审计原始流水（含 `hook_error`/`memory_conflict`/`fallback_applied`） |
| `out/B-memory-corruption/raw/<case>/state.json` | 会话结束后插件状态（`control.variety/conflict/audit`、写计数） |
| `out/B-memory-corruption/raw/<case>/memory-after.json` | 记忆文件终态（last-wins 后 active/superseded 视图） |
| `out/B-memory-corruption/_mock/` | 0 次真实调用的 mock 自检证据（用于验证注入脚本本身，不是模型证据） |
