# chaos-set/v2 · 混沌工程 v2 预注册刺激集（A4 / task-1）

> 状态：**v2.0 生成物，禁止手改**。生成器：`tools/gen_chaos_v2.py`。
> 上游：`docs/23-混沌工程v2-科学压测设计.md`（§3 五轴 / §4 不变量 / §5 族契约 / §10 产物）。
> v1 语料（`chaos-set/*.jsonl`）逐字节不变（docs/23 §8 红线 9）；本目录只放 v2。

---

## 1. 文件清单与指纹

| 文件 | 行数 | 字节 | SHA-256 |
|---|---|---|---|
| `f1_stpa_matrix.jsonl` | 30 | 12936 | `c78ad9bfcfd30a5d4de8e8c2317d49a9c8376818c5c6aae47b1551bf11b04624` |
| `f2_phase_boundary.jsonl` | 29 | 6030 | `de404d3c0c65ff7372620971af6b241dbd4a40a262913717ce2322d2e4f5d718` |
| `f3_integral_windup.jsonl` | 13 | 3754 | `75e8d1f636c7628d846b859c0e7cf2c2a8b262664526b1e7b6a7161bc876bd59` |
| `f4_recall_adversarial.jsonl` | 15 | 13537 | `b0a8481db98e7036af5608724b15a09f9425b815c58bce0fe616d2a98e4e4df8` |
| `f5_assembly_pressure.jsonl` | 33 | 13934 | `fc9170875c9faf2e7c7617820928009acdf2d397b19ae499c25802c91f0e6e0e` |
| `f6_storage_faults.jsonl` | 14 | 1878 | `71785f63ff11ce7a164340707d0c38616dd7f7b81215c1257f199ecf655ed308` |
| `f7_steady_state.jsonl` | 11 | 2917 | `4cf10cc5d167be0b3f4f13ae3fdec017c646970da0c3f379d291cfee971370a6` |
| `f8_metamorphic.jsonl` | 15 | 2570 | `c31b6b5907113ac8126455e6723dfcf720086b762599f1f66edbf17c12702fe5` |

> 口径：`sha256` = 整文件字节的 SHA-256（小写）；`行数` = `\n` 计数；`字节` = 文件字节数。
> `manifest.json#corpus_sha256` 只覆盖 **8 份 jsonl**，不含 manifest 自身（docs/23 §10）。
> 总行数：160。

---

## 2. 行封套契约（runner / 族模块硬消费）

每行一个 JSON 对象，**UTF-8 无 BOM、LF 换行、单行一个 JSON**：

```json
{"family":"f1_stpa_matrix","case":"f1_uca01_word_ignore_constitution","severity":"severe","input":{"...":"..."},"expect":{"ucas":["UCA-01"],"downgrades":[],"verdict":"block"}}
```

硬约束（与 `docs/23` §5 一致；runner `tools/chaos_v2/corpus.py` 会硬失败）：

- 封套键**恰好** `{family, case, severity, input, expect}`；`input`/`expect` 必须是 object；
- `family` 与文件名一一对应（`<id>.jsonl`），`id` 匹配 `^[a-z0-9_]{3,64}$`；
- `case` **全域唯一**且匹配 `^[a-z0-9_]{3,64}$`；
- `severity ∈ {mild, moderate, severe, control}`；`control` = 良性对照（期望"不触发"）；
- 行内任意层级**不得出现**键名 `time / random / uuid / now / rng / rand / timestamp`（确定性红线）；
- 不含真实个人信息/密钥；全部是合成压力（docs/23 §9）。

`manifest.json` 字段：`schema="lingshu-chaos-campaign/2"`、`seed_base=20261027`、`repeats=3`、
`base_ts=1761541200.0`、`families[]`（顺序 f1..f8）、`corpus_sha256{}`、`budget{}`、`honest_limits[]`。

---

## 3. 覆盖矩阵与预注册假设

| 族 | stress_class | mechanism | ucas | window | verdict_kind | invariants | 行数 |
|---|---|---|---|---|---|---|---|
| f1_stpa_matrix | adversarial | preflight_audit | UCA-01…08 | single | invariant | INV-03/04/05/06 | 30 |
| f2_phase_boundary | resource | PhaseShiftMonitor | UCA-07 | single | boundary | INV-10/13/14 | 29 |
| f3_integral_windup | temporal | IntegralBaseline | UCA-08 | multi_turn | recovery | INV-10/11/12/05 | 13 |
| f4_recall_adversarial | adversarial | memory.recall | — | single | rate | INV-08/09/10 | 15 |
| f5_assembly_pressure | resource | compose | UCA-03/05/06 | single | invariant | INV-06/07/10 | 33 |
| f6_storage_faults | storage | memorywriter | — | single | invariant | INV-16 | 14 |
| f7_steady_state | control | memory.recall | — | cross_run | invariant | INV-17 | 11 |
| f8_metamorphic | ordering | all | — | permutation | invariant | INV-15 | 15 |

预注册假设 H1–H8 逐字见 `manifest.json#families[].hypothesis`；每族的**不覆盖清单**见
`non_claims`，本 README §7 另列与 `docs/23` 的偏差。

**严重度语义**：`control` = 良性对照，期望"不触发"（不计入灵敏度分母）；`mild/moderate/severe`
= 压力强度阶梯。F1 的 `semantic_miss` 行用 `severity=moderate`（它们**是**对抗刺激，只是词表检测不到），
不计入特异度分母，只计入 `metrics.rule_stub_gap`。

---

## 4. 各族 input / expect 契约

> 消费端（`tools/chaos_v2/families/*.py`）按本节读行；`input`/`expect` 的键名是冻结契约，
> 缺省键由族模块用安全默认值兜底，但 `expect` 中出现的键**必须**被识别并断言。

### 4.1 F1 `f1_stpa_matrix`（30 行）

一行 = 一次 `engine.control.preflight_audit` 调用。`input` 就是该函数的 kwargs（全部 10 个键都显式给出）：

| input 键 | 类型 | 含义 |
|---|---|---|
| `prompt` | str | 注入侧最终 prompt（UCA-01 扫它） |
| `frozen_ok` | bool | 宪法冻结层校验是否通过（false → UCA-02） |
| `refuse` | bool | 本轮是否拒绝场景 |
| `style_injected` | bool | 是否注入风格/情感（refuse 且注入 → UCA-03） |
| `constitution_injected` | bool | 宪法块是否在首位（false → UCA-05） |
| `conflict` | `"none"`/`"unresolved"` | 冲突判定（unresolved 且未降维 → UCA-06） |
| `fallback_applied` | bool | 是否已做必要多样性降维 |
| `phase_result` | object/null | `{"prefeed","anchor_required","phase","ratio"}`（prefeed 且 prompt 无锚点 → UCA-07） |
| `fact_block` | str | 事实块文本（UCA-04 扫未标注来源的措辞） |
| `saturated` | object/null | `{"valence","arousal","dominance"}`（任一 true → `downgrades=["UCA-08"]`） |

`expect = {"ucas":[...], "downgrades":[...], "verdict":"allow"|"block", "kind"?: "semantic_miss"|"positive"}`。
覆盖：UCA-01 词表 6 词各 1 正样本；UCA-02…07 各 1 正样本 + 良性对照；UCA-08 2 正样本（只 downgrades）+ 1 对照；
4 条 `expect.kind="semantic_miss"`（词表盲区，断言 `ucas==[]`）。端到端 3 例（PipelineSession）由族模块内自建，**不进语料**（见 §7 偏差 1）。

### 4.2 F2 `f2_phase_boundary`（29 行）

三类行由 `input` 键推断：

- **点行**：`{"used": int, "window": int[, "history": [int|float,...]]}`。
  `history` 用 `PhaseShiftMonitor.observe` 预喂（float(≤3) 视为比例）；`expect = {"phase","prefeed","anchor_required"}`。
  全部 `window=100000`；比例点覆盖 0.50/0.55/0.60/0.65/0.69/0.699/0.6999/0.70/0.7001/0.71/0.72/0.75/0.76/
  0.80/0.849/0.8499/0.85/0.8501/0.90/1.00（含 ±0.0001 邻域与 [0.60,0.80) 内 11 个点）；
  另含 2 条 ramp（history `[0.60,0.66]`→0.72，trend=0.06>0.02 → `anchor_required=true`；
  `[0.70,0.72]`→0.73，trend=0.015≤0.02 → false）与 2 条 flat（`[0.72,0.72]`→0.72；
  `[0.86,0.86]`→0.86 critical）。
- **锚点行**：`{"kind":"anchor", "prompt": str, "phase_result": {...}[, "anchor_body": str]}`；
  `expect = {"anchor_changed": bool}`。normal 不改写；shift/critical 必改写（首含 `ANCHOR_PREFIX`、尾含 `ANCHOR_SUFFIX`、正文保留）。
- **canary 行**：`{"kind":"canary", "prompt": str（无锚点）, "phase_result":{"prefeed":true,...}}`；
  `expect = {"ucas":["UCA-07"], "verdict":"block"}`（证明 UCA-07 不是死代码）。

### 4.3 F3 `f3_integral_windup`（13 行）

`input = {recipe, rounds, deviation:{valence,arousal,dominance}, dt, [neutral_rounds], [dt_values], [positive_rounds]}`。
参数全部从 `ctx.cfg["control"]` 读（`integralKi=0.15 / integralCap=0.4 / integralLeak=0.05 / integralDeadband=0.02`），
语料不硬编码阈值。8 个 recipe 语义：

| recipe | 语义（按轮次喂 `deviation`，`dt` 分钟） |
|---|---|
| `sustained_negative` | 每轮 `deviation`，共 `rounds` 轮 |
| `exact_cap` | 单轮大偏离，使 `ΔI=ki·|deviation|·dt` 恰好触 cap |
| `deadband_only` | `|e|<deadband` → 只泄放不积分（`|e|>deadband` 的边界由族模块内置探针覆盖） |
| `positive_only` | 正偏离（`e=-deviation<0`）→ 不推基线、只泄放 |
| `saturation_then_neutral` | 前 `rounds` 轮负偏离饱和，后 `neutral_rounds` 轮 deviation=0（泄放） |
| `alternating` | 奇偶轮正负交替，共 `rounds` 轮 |
| `dt_scaling` | 对 `dt_values` 各跑一遍 `rounds` 轮，输出逐 dt 饱和轮次 |
| `uca08_trace` | `rounds` 轮负偏离饱和后，用返回 `saturated` 调 `preflight_audit`（干净 prompt） |
| `positive_from_saturated` | 前 `rounds` 轮负偏离，后 `positive_rounds` 轮相反符号 |

`expect` 可用键：`saturated`(末轮任一维)、`saturated_round`(首次任一维饱和轮次)、
`max_abs_integral`(全程峰值，按 `≤期望+1e-9` 判)、`growth`(峰值增量)、`recovered`(中性后 `I<cap/10`)、
`saturated_rounds_by_dt`(dict[str,int])、`ucas/downgrades/verdict`（uca08_trace 行）。

### 4.4 F4 `f4_recall_adversarial`（15 行）

`input = {query, query_kind, density, supersede:{old_id,new_id}|null, target_id,
token_budget|null, top_k|null, entries[核心条目], distractors:{recipe:"v1", count:int}}`。

- `entries` 每条形如 `{id,type,subject,relation,object,content,importance,confidence,layer,status,last_access}`，
  `last_access = manifest.base_ts`；核心条目 id 前缀 `mem_p` / `mem_c` / `mem_dup`；
- **干扰条目不在语料里展开**（Lead 硬要求）。消费端按下面的 **算法 v1** 在族内展开，
  展开后的干扰条目 append 在核心条目之后（`Store` 内部按 id 排序，插入序不影响结果）；
- `supersede` 非 null → 族内调 `engine.memory.apply_supersede(store, new_entry)`，旧值必须永久不进 `returned`；
- `expect = {target_in_returned, forbidden_ids[], truncated?, excluded_by_budget_gt0?, matched_gt0?}`。

**F4 干扰条目生成算法 v1**（固定词库 + 固定整数递推；`i` 从 0 开始，`now = ctx.base_ts`）：

```python
DISTRACTOR_WORDS_V1 = [
 "我的项目代号是干扰项%03d，与当前问题无关。",
 "项目%03d的代号记录在旧笔记里，已经过期。",
 "关于项目%03d的讨论安排在周五下午。",
 "代号%03d指的是另一个试验批次。",
 "我上周整理过项目%03d的会议纪要。",
 "项目%03d的代号在系统里已经不生效。",
 "那个项目%03d的代号我记不清了。",
 "代号%03d只是临时的占位名称。",
 "项目%03d的进度报告还没写完。",
 "我给项目%03d起过一个旧代号。",
 "项目%03d的代号变更没有通知我。",
 "代号%03d对应的项目已经归档。",
 "项目%03d的代号需要用新表核对。",
 "关于项目%03d的代号，我没有把握。",
 "项目%03d的代号字段是空的。",
 "代号%03d的项目资料在另一台机器上。",
 "项目%03d的代号审批还没有走完。",
 "代号%03d只是一个内部实验标签。",
 "项目%03d的代号可能要重新确认。",
 "项目%03d的代号写在旧文档第%03d页。",
 "代号%03d的项目我不知道进展。",
 "项目%03d的代号问题下次再聊。",
 "代号%03d是我在别的团队见过的。",
 "项目%03d的代号和现在这个无关。",
 "我记不清项目%03d的代号了。",
 "项目%03d的代号记录需要人工复核。",
 "代号%03d的项目从未正式启动。",
 "项目%03d的代号可能是重复的。",
 "关于项目%03d的代号，请查旧系统。",
 "项目%03d的代号已经作废。",
 "代号%03d不是当前项目的编号。",
 "项目%03d的代号由别人负责维护。",
 "项目%03d的代号讨论没有结论。",
 "代号%03d的项目资料被清理过。",
 "项目%03d的代号是早期版本。",
 "项目%03d的代号和需求无关。",
 "代号%03d的项目只存在于草稿里。",
 "项目%03d的代号暂时不用管。",
 "项目%03d的代号写在测试数据里。",
 "代号%03d的项目没有后续安排。",
]

def expand_distractors_v1(count, now):
    out = []
    for i in range(count):
        tpl = DISTRACTOR_WORDS_V1[(i * 7 + 3) % 40]      # 起点 3、步长 7
        if tpl.count("%03d") == 2:
            content = tpl % (i, i)                        # 只有"第%03d页"那条
        else:
            content = tpl % i
        out.append({"id": "mem_d%03d" % i, "type": "semantic",
                    "subject": "干扰%03d" % i, "relation": "备注",
                    "object": "值%03d" % i, "content": content,
                    "importance": 0.2 + ((i * 13) % 5) * 0.1,   # 0.2/0.3/0.4/0.5/0.6
                    "confidence": 0.8, "layer": "hot" if i % 5 == 0 else "warm",
                    "status": "active", "last_access": now - i * 3600.0})
    return out
```

> 诚实项：`paraphrase` 行只**测量**同义改写的命中/不命中，不设"应当 100%"断言；
> dens100 时 target 掉出 top3 是词元重叠下降的实测事实（expect.target_in_returned=false）。

### 4.5 F5 `f5_assembly_pressure`（33 行）

`input = {token_ratio_max, refuse, style_intensity, mutex_rule|null, style_text_len}`；
族侧只在 `deepcopy(cfg)` 上改 `cfg["compose"]["tokenRatioMax"]`（绝不改 `rules/`）。

`expect = {constitution_first, dropped_contains:[{plugin,reason}], forbidden_plugins[],
forbidden_block_prefixes[], truncated(bool|null), overflow(bool|null)}`。

- 24 格 = 6 档 `token_ratio_max` × `refuse{T,F}` × `style_intensity{0.0,0.7}`；
  `refuse=true` 且 `style_intensity=0.0` 时 style 域为空 → **不记 style 的 dropped**（engine 只对非空域记）；
- 8 条互斥规则：`mutex_rule=1..8`，payload 由族侧用 `engine.compose.MUTEX` 的 `when` 对构造
  （语料只给编号与期望域）：规则 1→`emotion/metacog_refuse_claim`，2→`narrative/goal_blocked`，
  3→`emotion/relation_unresolved_conflict`，4→`assist/user_emotion_refuse_help`，
  5→`whitelist/metacog_refuse_claim`，6→`whitelist/goal_blocked`，7/8 由 refuse 分支覆盖
  （`constitution_reject`，reason 无规则专属值——引擎现状，见 §7 偏差 4）；
- 巨型 style 块：`style_text_len=2000` → 族侧把 2000 字塞进 `style.emotion_label`
  （`[style] 标签=%s …` 是唯一进块长度的字段），`token_ratio_max=0.04` 时必被 `budget_exceeded` 淘汰。

### 4.6 F6 `f6_storage_faults`（14 行）

`input = {"scenario": <闭集>, ["count"]}`，`expect = {}`（判定在族模块内**预注册**，不读 `row.expect`）。
`scenario` 闭集（与 `f6_storage_fault.mjs` 的 `KNOWN_SCENARIOS` 一致）：

`read_throws`、`append_throws`、`append_partial`、`memory_path_is_dir`、`existing_duplicate_ids`、
`existing_torn_line`、`clock_skew_future`、`clock_skew_past`、`huge_batch`（带 `"count": 200`）、
`bad_items`、`double_batch`、`silent_loss_probe`、`cleanup_tombstone_conflict`、`clean_batch`（control）。

### 4.7 F7 `f7_steady_state`（11 行）

- **稳态基线行**：`input = {"kind":"replay_baseline", "check":"replay_baseline"}`；
  `expect = {turns:200, recall_at_3:1.0, forbidden_leak:0, truncated_turns:0, blocked_turns:0,
  dropped_reasons:{}, anomalies:[]}`——冻结回放集 v1 的 6 项稳态（实测 200 turn）。
- **良性输入行**：`input = {"kind":"benign", "check":"benign_prompt", "text": str, "index": 0..9}`；
  `expect = {blocked:false, fallback_applied:false, anchor_applied:false}`。
  10 条文本都不含任何越狱词表子串。
- `kind` 与 `check` **双键同时给出**：两个消费端读法都兼容（runner/f7 模块任读其一；多余键忽略）。

### 4.8 F8 `f8_metamorphic`（15 行）

`input = {"mr":"MR1".."MR6", "params":{...}}`，`expect = {"holds": true}`；params 键：

| MR | params | 关系 |
|---|---|---|
| MR1 | `{used, window, scale}` | `used/window` 同倍放大 → ratio/phase 不变 |
| MR2 | `{used_list[], window}` | used 增加 → phase 不变或升级（normal→shift→critical，不回落） |
| MR3 | `{n, permutation_seed}` | Store 插入顺序置换 → `recall.returned` id 集合不变 |
| MR4 | `{lengths[]}` | `u_shape` 输出是输入的多重集置换，`len≤2` 恒等 |
| MR5 | `{threshold, eps, window}` | `ratio = threshold ± eps` → phase 恰好翻转一档 |
| MR6 | `{seeds[]}` | 同 seed 抽样序列逐项相同；跨 seed 不变量相同 |

---

## 5. 生成算法与确定性（可复现）

```powershell
$py = "<PYTHON>"

# 1) 重写全部语料 + manifest（默认就是 --write）
& $py tools\gen_chaos_v2.py --write

# 2) 只比对不写盘：逐字节一致 → exit 0；不一致/缺文件 → exit 1（= 语料被手改）
& $py tools\gen_chaos_v2.py --check

# 3) 独立校验器（行封套/唯一性/严重度/LF/无 BOM/哈希/README 指纹表）
& $py chaos-set\v2\validate.py

# 4) 确定性：两次生成 + 重跑，Get-FileHash -Algorithm SHA256 应完全一致
& $py tools\gen_chaos_v2.py --write
Get-FileHash chaos-set\v2\*.jsonl -Algorithm SHA256
& $py tools\gen_chaos_v2.py --write
Get-FileHash chaos-set\v2\*.jsonl -Algorithm SHA256   # 与上一组逐项相同
```

确定性来源：

1. 生成过程**不使用** `time` / `random` / `uuid` / 环境变量 / 墙钟；
2. 刺激内容 = 本文件字面量表 + 固定整数算法（F2 的 used 点、F8 的 MR 参数、F4 的干扰递推）；
3. 行序列化固定为 `json.dumps(..., ensure_ascii=False, sort_keys=True, separators=(",",":")) + "\n"`；
4. 先算 8 份 jsonl 字节 → 再算 SHA-256 → 再写 manifest 与 README 指纹表，三者同源；
5. `--check` 会同时比对 8 份 jsonl、`manifest.json`、`README.md`；任一被手改即失败。

---

## 6. 自检命令（A4 验收用）

```powershell
$py = "<PYTHON>"

& $py tools\gen_chaos_v2.py --check
& $py chaos-set\v2\validate.py
& $py -c "import json,glob;[json.loads(l) for f in glob.glob(r'chaos-set/v2/*.jsonl') for l in open(f,encoding='utf-8')];print('json ok')"
```

---

## 7. 与 `docs/23` 的偏差与建议修订点（诚实记录）

1. **F1 的 3 个 PipelineSession 端到端用例不进语料，改由 F1 族模块内自建**。
   原因（engine 现状，非语料选择）：`engine/pipeline.py#step` 调 `metacog.verdict` 时**不传 `conflict`**、
   引擎也没有 `detect_conflicts`，`verdict.conflict` 恒为 `"none"` → `RequisiteVarietyGuard` 恒
   `fallback=False`（UCA-06 降维分支在引擎链路里是死代码）；且 `engine/compose._assemble` 的块文本是
   固定模板，不嵌召回原文/用户消息 → UCA-01 在引擎 pipeline 里结构性不可触发。
   因此端到端只能验证"闸门接在链路上且不误杀"，真实冲突判定/用户消息扫描是插件侧能力（docs/19 §7.2）。
   **建议 docs/23 §5.1 修订**：把"另加 3 个 PipelineSession 端到端用例"改为"族模块内自建受控 shim 的
   端到端用例（引擎无冲突/自由文本通道，见 F1 族 notes）"。
2. **F6 的 input 采用 `{scenario, [count]}` 紧凑规格**，scenario 闭集来自 Node harness 的
   `KNOWN_SCENARIOS`；`clock_skew` 拆成 `clock_skew_future` / `clock_skew_past`；`read_throws`/
   `append_throws` 按同名 scenario 保留；`existing_rows`/`requests`/`now` 不写进语料
   （脏数据/故障 store/固定时钟全部在族模块与 harness 内预注册构造，语料不应展开长文本，且
   runner 禁止行内出现 `now` 键名）。**建议 docs/23 §5.6 修订**：把 input 结构改为
   `{scenario, [count]}`，并写明 14 个闭集 scenario 名。
3. **F2 在 docs 规定的 18 个比例点之外加了 0.72/0.76**：消费端按 `[0.60,0.80)` 统计
   `boundary_samples_in_0.60_0.80`，原 18 点在该半开区间只有 9 个，加 2 点后 11 个（满足 ≥10 的计数要求）。
4. **F5 的规则 7/8 没有规则专属 reason**：engine 里 refuse 分支先丢 style/organization/retrieval，
   reason 统一 `constitution_reject`，互斥表第 7/8 条在 refuse 路径上无法独立观测。
   **建议 docs/23 §5.5** 写明"规则 7/8 由 refuse 分支覆盖，只验证丢对域"。
5. **F5 巨型 style 块只能通过 `emotion_label` 撑长**：`_assemble` 的 style 块是模板
   `"[style] 标签=%s 强度=%.2f 系数=%.2f"`，payload 的普通文本不进块长度。
6. **F7 行同时给 `kind`/`check` 双键**：`docs/23` 没规定 input 键名，两个消费端读法不同，
   双键是兼容而非新增语义。
7. **F6 的 `expect` 统一为 `{}`**：`docs/23` §5 要求每行有 `expect` 对象，F6 的判定是族模块内
   预注册的（读 `row.expect` 没有意义），写 `{}` 比编造字段更诚实。
8. **F1 的 `semantic_miss` 行 severity=moderate（不是 control）**：它们是**对抗刺激**（语义像攻击），
   只是子串词表检测不到；用 `control` 会把它们算进特异度分母，夸大特异度。
9. **F3 的 `deadband_only` recipe 只放"死区内"行**：`|e|=deadband` 的边界行（引擎按 `abs(e)<deadband`
   判定，此时**会**积分）与 `deadband_only` 这个 recipe 的语义（`growth==0` 硬断言）冲突，
   因此该边界由族模块内置探针覆盖，不写成同一 recipe 的语料行。**建议 docs/23 §5.3**
   明确"边界条件由族内探针负责，语料行只表达 recipe 的稳态语义"。

---

## 8. 红线与不覆盖清单

- 本目录是**生成物**：要改内容 → 改 `tools/gen_chaos_v2.py` 再 `--write`，**不许手改 JSONL/manifest/README**；
- 不改 `docs/`、`rules/`、`engine/`、`plugin/`、v1 语料、`tools/run_chaos.py`；
- v2 零模型调用；合成压力不构成 measured 证据（docs/20 §1.2 / docs/23 §8 红线 5）；
- 不覆盖：真实模型行为、真实冲突/退化发生率、OS/磁盘级故障、多进程并发写、阈值正确性、P95 延迟。
