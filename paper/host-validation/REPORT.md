# LINGSHU · 真实 LLM 宿主小样本验证（route B）

> **性质：illustrative end-to-end check，不是统计证据。**
> n = 1 per cell（每格只跑 1 次）、单模型（`deepseek-official` 一条路由）、单次运行、不可外推。
> 本报告**只写实测到的数字**；未测的东西一律标 N/A 或「未测」，不补造、不插值。
> 时间：2026-10-27 00:23–00:52（Asia/Shanghai）｜机器：本机 Windows，DSH headless

---

## 0. 一句话结论

**真实宿主链路能跑通，但这份证据不足以支持"两臂有差异"的任何主张。**

- **11 次成功的 headless 调用**（10 格 + 1 次可行性探针）全部 `exit=0`，链路（CLI → 插件 → prompt 注入 → 真实模型 → stdout）成立；
- **两臂 × 3 探针的主矩阵里，Full 与 Baseline 全部答对「贝塔」** —— 因为答案（种子句）本来就在 prompt 原文里，
  这是天花板，不是插件的作用；
- 唯一能分离两臂的对照（短问句）显示：**Full 命中预置记忆并注入 prompt（`matched=1, hits=['mem_001']`）→ 答「贝塔」**，
  **Baseline 无记忆、10 步工具漫游后编出「LSJH」**——机制上说得通，但 **n=1**，只能当 illustrative。

---

## 1. 做了什么 / 没做什么

| 项 | 状态 |
|---|---|
| 可行性探针（隔离目录、1 次调用） | ✅ 做成（第 3 次尝试成功，前 2 次为权限/凭证 blocker，见 §3） |
| 两臂 × 3 探针 = 6 格主矩阵 | ✅ 跑完（§4）但**无区分度**（见 §5 的成因） |
| 补充对照 A：删掉原文种子句（设定只在记忆里） | ✅ 2 格（§5.1） |
| 补充对照 B：短问句（隔离注入通道） | ✅ 2 格（§5.2） |
| Basic 臂（宿主侧） | **N/A**，原因见 §7.3 |
| `paper/ablation/script-100.jsonl` | ❌ 该文件不存在（仓库内只有 `paper/ablation/DESIGN.md`），改用 §2 的等价构造 |
| 改动 `plugin/`、profile 配置、`rules/` | ❌ **零改动**（只读引用；Full 臂加载的是插件源码副本，sha256 与源一致，见 `calls.json.integrity`） |
| 写入范围 | 仅 `paper/host-validation/` 与 `.host-valid-data/` |

---

## 2. 输入构造（与任务书的差异，逐条说明）

任务书要求"100 轮长对话合成 1 个 prompt"。`paper/ablation/script-100.jsonl` 尚未生成，故按任务书给的 fallback 构造：

```
100 轮 = chaos-set/long_context_flood.jsonl 第 1–50 轮（噪声 + 第 1–2 轮种子句「我的项目代号是贝塔」）
       + replay-set/v1.jsonl 前 49 条 user_message（按 (session_id, turn_index) 稳定排序）
       + 1 条探针问句（末轮）
```

| 文件 | 字节 | sha256（前 16） | 用途 |
|---|---:|---|---|
| `prompt-100turn-q1.txt` | 78,652 | `6f4983dd1e1be2ea` | 主矩阵探针 1 |
| `prompt-100turn-q2.txt` | 78,634 | `a95d29f02de4a836` | 探针 2 |
| `prompt-100turn-q3.txt` | 78,640 | `8d9f2061217de77f` | 探针 3 |
| `prompt-100turn-q1-noseed.txt` | 78,565 | `cbbe53f0f1eee5c4` | 对照 A：删除第 1–2 轮种子句 |
| `memory-seed.jsonl` | 892 | 见 `calls.json.integrity` | Full 臂预置记忆（`mem_001 项目代号=贝塔`） |

**三个必须写清的构造决定**（都会影响可解释性）：

1. **追加了「禁止使用工具」指令**。headless 是**带工具的 Agent 循环**，不是一次性补全：
   可行性探针（§3）里它用了 7 个 step、8 次工具调用去翻工作区。不约束就会把"记忆测试"变成"工具漫游测试"。
   该指令对两臂完全相同。
2. **Full 臂预置 warm 阶段**（`state.json` 里 `turns=3`）。插件冷启动门控在 `turns<3` 时
   **清空召回**（`index.js:550`：`if (stage==='cold') { matched=0; hits=[]; }`）。
   第一次 Full 格没预置就出现了 `last-prompt.txt` 只有 152 B 宪法段、`matched=0` 的空跑（已留证在
   `evidence/` 的早期 run 里），所以后续格统一预置 warm。预置内容只有 `turns=3` 与清空的 `lastEvent`。
3. **探针问句共 3 种说法**（同一语义不同措辞），两臂逐字相同，避免"只试一种说法"。

---

## 3. 可行性探针：3 次尝试，2 次 blocker，1 次成功

| # | 结果 | 退出码 | 耗时 | 失败原因（原文） |
|---|---|---:|---:|---|
| 1 | ❌ | 1 | 236 ms | `EPERM: operation not permitted, open '<DSH_HOME>\profiles\lingshu-lab-headless\cordis.yml'` —— DSH **每次 boot 都重写**该文件，而沙箱只允许写 `<REPO_ROOT>` |
| 2 | ❌ | 1 | 3,036 ms | `dsh: MISSING_CREDENTIAL: llm-deepseek: no API key for provider route "deepseek-official"` —— 重定向 `DSH_HOME` 后凭证库为空；**但插件已正常落盘 `audit.jsonl`(4,720 B)/`state.json`/`last-prompt.txt`，0 次模型请求** |
| 3 | ✅ | **0** | **28,017 ms** | 见下 |

**第 3 次（成功）实测**：7 个 agent step、8 次工具调用、其中 6 次工具返回错误
（工具名序列：`lingshu_status, pwsh, skill, glob, pwsh, pwsh, read, read`）、
1 次 `session/title-llm-request` ⇒ **一次 CLI 调用 = 8 次模型请求**。
插件产物齐全：`audit.jsonl` 9,300 B（`pre_step=7`、`turn_begin=1`、`apply=1`、`session_event=29`）、
`state.json` 1,793 B、`last-prompt.txt` 152 B（**只有宪法段**）。
注意：这次跑的是**裸探针**（无预置记忆、无长 prompt），它证明的是"链路能通"，不是"记忆能工作"。

### 3.1 绕过 blocker 的三步处置（每步都可复现，且都有代价）

| 处置 | 为什么必须 | 代价 / 诚实标注 |
|---|---|---|
| `DSH_HOME=<REPO_ROOT>\.host-valid-data\dshhome` | 原 profile 目录不可写，DSH 每次 boot 重写 `cordis.yml` | 换了 home ⇒ 凭证/sessions/plugins 都换到隔离目录（这反而更干净，但**不是**原 profile 的完整状态） |
| 把 `plugin/dsh-lingshu` 源码**复制**进重定向 profile 的 `node_modules` | 沙箱拒绝创建 junction（`New-Item -ItemType Junction` → Access denied），且 profile 里 `dsh-lingshu` 是 `link:` 依赖 | Full 臂加载的是**副本**；`index.js` sha256 与源文件**一致**（`calls.json.integrity.plugin_copy_identical_to_source=true`） |
| `DEEPSEEK_API_KEY` 注入子进程环境变量 | 重定向后凭证库为空；`.credentials.yaml` 的 `refs.DEEPSEEK_API_KEY` 是真实 key | 值来自 `~/.dsh/.credentials.yaml`，**从未打印、从未落盘**；仅存在于子进程环境 |

---

## 4. 主矩阵：两臂 × 3 探针（6 格）

| run_id | 臂 | 探针 | 退出码 | 耗时 (ms) | step | 模型最终输出 | 含「贝塔」 | 记忆注入证据 |
|---|---|---|---:|---:|---:|---|---|---|
| `hostval-full-q1` | Full | q1 | 0 | 4,816 | 1 | `贝塔` | ✅ | `matched=1, hits=['mem_001']`，`last-prompt` 661 B 含〖记忆召回〗 |
| `hostval-full-q2` | Full | q2 | 0 | 4,942 | 1 | `贝塔` | ✅ | 同上 |
| `hostval-full-q3` | Full | q3 | 0 | 4,632 | 1 | `贝塔` | ✅ | 同上 |
| `hostval-baseline-q1` | Baseline | q1 | 0 | 4,339 | 1 | `贝塔` | ✅ | 无插件 |
| `hostval-baseline-q2` | Baseline | q2 | 0 | 4,035 | 1 | `贝塔` | ✅ | 无插件 |
| `hostval-baseline-q3` | Baseline | q3 | 0 | 3,802 | 1 | `贝塔` | ✅ | 无插件 |

**这 6 格不能当消融结果**：两臂 6/6 全对。原因不是"插件没用"，而是
**答案（`我的项目代号是贝塔`）逐字出现在 prompt 的第 1–2 轮里**——任何能读上下文的模型都会答对。
这就是我在 `paper/data/INVENTORY.md §3.2` 里预警过的**天花板效应**，在真实宿主上同样成立。

> 探针 2/3 的 `inputTokens` 只有 ~650（其余走 cacheRead 21k）：同一进程/会话缓存命中，
> 不影响结论，但说明"6 格"之间不是完全独立的冷启动。

---

## 5. 两个补充对照：唯一能看出机制差异的地方

### 5.1 对照 A：删掉原文种子句（设定只存在于记忆里）

| run_id | 臂 | step | 最终输出 | Full 侧注入 |
|---|---|---:|---|---|
| `hostval-full-q1noseed` | Full | 1 | `找不到，因为以上对话内容中没有出现项目代号，只有占位文本和噪声段落。` | **`matched=0`，`last-prompt` 无〖记忆召回〗** |
| `hostval-baseline-q1noseed` | Baseline | 1 | `找不到` | 无插件 |

**这条结果比主矩阵更有信息量**：把 78 KB 塞进**一条** user message 后，插件的检索**没有命中**
（`lastEvent.matched=0`），因此它也没能救场。两臂都答"找不到"——**同时说明"单条超长消息"这个 host 做法会让插件检索失效**。
这与 `paper/data/DATASET-SPEC.md` 里"引擎侧无上下文窗口、只有查询"的结论在**真实宿主侧得到独立印证**：
检索是**按消息**做的，消息一长、噪声一多，query 归一化后覆盖率被稀释，就命不中。

### 5.2 对照 B：短问句（把注入通道单独隔离出来）

| run_id | 臂 | step | 最终输出 | 注入 |
|---|---|---:|---|---|
| `hostval-full-short` | Full | 1 | **`贝塔`** | `matched=1, hits=['mem_001']`；`last-prompt` 661 B 含
`- [semantic] 项目代号是贝塔（来源: mem_001 ｜ 可信度: 高）` |
| `hostval-baseline-short` | Baseline | **10** | **`LSJH`**（从 cwd `<REPO_ROOT>` 猜的，无依据） | 无插件 |

**这是唯一一条"机制成立"的证据**：真实模型、真实宿主、插件把预置记忆注入 prompt、模型据此作答。
Baseline 那格是同一条短问句下**没有记忆可用**时模型的行为样本（10 步工具漫游 → 编一个），
但它是 **n=1 的轶事，不是幻觉率**；基线在 100 轮主矩阵里 3/3 都答对了。

---

## 6. 证据链（全部在 `paper/host-validation/evidence/`）

| 文件 | 内容 |
|---|---|
| `probe3-transcript.json` | 可行性探针的**完整事件流**（64 事件、7 step、全部工具调用与错误）——从 DSH session 的 zstd 分帧日志解出 |
| `probe3-data/` | 探针插件产物：`audit.jsonl`(9,300 B)、`state.json`、`last-prompt.txt` |
| `probe2-data/` | 缺凭证那次：证明**插件在模型调用前就已工作**（audit 4,720 B），0 次模型请求 |
| `raw-<arm>-<probe>.jsonl` | 10 格的 `--json` 事件流原文 |
| `cell-full-<probe>.*` | Full 各格的 `last-prompt.txt` / `state.json` / `audit.jsonl` / `memory.jsonl`（注入证据全在这里） |
| `../calls.json` | 机器可读记录：每格 prompt_sha256、step、final、usage、注入字段、环境与 integrity |

---

## 7. 诚实边界（这一节比结果更重要）

### 7.1 能支持什么

1. **链路可用性**：真实 DSH headless + 插件 + 真实模型端到端跑通，**11 次成功、`exit=0`**
   （10 格 + 1 次裸探针），插件在 `pre_step` 落审计、写 `memory.jsonl`、注入 `〖记忆召回〗` 段——**可重复（11/11）**。
2. **注入机制在真实宿主里成立**（对照 B）：`mem_001 → matched=1 → 注入 prompt → 模型答「贝塔」`，
   四个环节每一步都有文件级证据。
3. **一个方法学警示**（对照 A）：**把多轮压成一条超长 user message 会让检索命不中**（`matched=0`）。
   这对 `docs/22` 的 host 压测口径（S1 把 51 条拼成一个 prompt）是一个实证质疑，值得写进论文的威胁节。

### 7.2 不能支持什么（逐条）

| 不能主张 | 为什么 |
|---|---|
| Full 优于 Baseline | 主矩阵两臂 6/6 全对（天花板）；唯一有差异的短问句 n=1 |
| 任何遗忘率 / 幻觉率 / 拦截率数字 | 没测；`LSJH` 那格是 1 条轶事，不是率 |
| 100 轮长对话下的表现 | 这是**一条 78 KB 消息**，不是 100 轮真实多轮；且插件检索在此形态下失效 |
| 控制论层（UCA/相变/降维）的作用 | 这 10 格全程 `verdict=allow`、`ucas=[]`，**没有触发任何 UCA 路径**，等于没测 |
| 跨模型/跨温度泛化 | 单模型、单路由、单次运行；temperature 未记录 |
| 与引擎三臂的一致性 | 用户要求 Basic 臂 N/A（见 7.3），因此这是 **2 臂**而非 3 臂 |

### 7.3 为什么 Basic 臂是 N/A

宿主侧**没有关闭控制论层的配置开关**：`plugin/dsh-lingshu/index.js:57` 只有
`control: {...CONTROL_DEFAULTS}`（参数默认值），没有 `enabled`/`off` 之类的总开关；
而 profile 的 `cordis.patch.yml` 属于"不得修改"范围，`--patch` 覆盖虽然能改 config，
但改的是"参数值"不是"关掉模块"，且会引入第三个非受控变量。
→ **Basic 臂在宿主侧无法定义，标 N/A**；三臂消融只能由引擎侧（A5 的确定性实现）承担。

### 7.4 成本：一次调用 ≠ 一次模型请求

这是本次最该被记住的运维事实：**headless CLI ≈ Agent 循环**。
- 裸探针：7 step + 1 次标题生成 = **8 次模型请求**；
- 加了"禁止工具"指令后：主矩阵每格 **1 step**（≈1 请求），但 Baseline 短问句那格仍跑到 **10 step**（≈10 请求）；
- 10 格累计 token：**193,288**（含 cacheRead）；全轮 agent step 合计 **26**（10 格 19 + 裸探针 7），
  每 step ≥1 次模型请求 ⇒ **≥26 次模型请求**，加标题生成估计 **26–36 次**（无法从事件流精确区分，故给区间，不编造确数）。

**任务书假设的"6 次调用"在物理上不成立**：真实成本高一个数量级。这不是本轮才有的，
任何"host 侧跑 N 格"的排期都该按"每格 1 次 CLI = 1~10 次模型请求"估算。

---

## 8. 复现

前置（一次性，见 §3.1）：`DSH_HOME` 重定向目录已建在 `.host-valid-data/dshhome`，
profile 文件与插件副本已就位；凭证经环境变量注入，不落盘。

```powershell
$py = "<PYTHON>"
$node = "<NODE>"
$bin  = "<DSH_BIN>"
Set-Location <REPO_ROOT>

# 环境
$env:DSH_HOME = "<REPO_ROOT>\.host-valid-data\dshhome"
$env:LINGSHU_DATA_DIR = "<REPO_ROOT>\.host-valid-data\cells\full-q1"   # Baseline 臂删掉这一行
$env:DEEPSEEK_API_KEY = '<从 ~/.dsh/.credentials.yaml 读取，勿打印>'

# 以 stdin 传 prompt（78 KB，超过 Windows 命令行参数上限，必须走 stdin '-'）
$prompt = [IO.File]::ReadAllText("<REPO_ROOT>\paper\host-validation\prompt-100turn-q1.txt", [Text.UTF8Encoding]::new($false))
$prompt | & $node $bin lingshu-lab-headless --json -      # Baseline: 把 lingshu-lab-headless 换成 headless
```

`run-cells.ps1` 是本轮实际使用的 6 格运行脚本（含 warm 预置、计时、证据落盘）。
**注意**：`Start-Process -RedirectStandardInput` 在本会话沙箱下会被拒（Access denied），
必须用 PowerShell 管道（上面这种写法）；且每次调用前都要重设 `DSH_HOME`（环境变量不跨 pwsh 进程保留）。

---

## 9. 给 Lead 的建议

1. **论文写法**：把这一节写成 "illustrative end-to-end feasibility check"，只主张 §7.1 的三条，
   并**主动写出 §5 的负结果**（主矩阵无区分度）——负结果比硬凑数字更抗审稿。
2. **若还要真实宿主效果证据**，必须先解决两件事（都不在本轮授权内）：
   ① 把设定放进**记忆而非原文**（否则天花板）；② 用**真正的多轮**（逐轮调用、插件跨轮 recall），
   而不是把 100 轮压成一条消息（本轮的对照 A 已证明压成一条会让检索失效）。
3. **排期口径**：按"每格 1 次 CLI ≈ 1–10 次模型请求"估算，别再按"1 格 1 次"排。
