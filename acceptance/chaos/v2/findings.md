# 混沌工程 v2 · 压测发现（findings）

> 归属：`docs/23-混沌工程v2-科学压测设计.md` §8 红线 8：**发现缺陷只记录、不在压测任务里改产品代码**。
> 每条发现都在 `acceptance/chaos/v2/<run_id>.json` 的 `summary.findings` 里可复现；
> 修不修、什么时候修，由 H0/后续任务裁决。**发现 ≠ v2 不变量失败**：v2 判的是机制是否守约，
> 这些是"参考实现/插件与 `docs/19` 契约之间的接线缺口或可观测性缺口"。
> 生成日期：2026-10-27 ｜ 基线 run：见 `.last.json` ｜ 全部为 engine 档（零模型调用）证据。

---

## 一、结论摘要

| ID | 位置 | 类型 | 影响 | 是否影响 v2 判定 |
|---|---|---|---|---|
| **F-01** | `engine/pipeline.py:105` | 接线缺口 | 引擎链路里 `conflict` 恒 `none` → `RequisiteVarietyGuard` 永不触发，UCA-06 降维分支在引擎侧是死代码 | 否（机制本身经 F1/F5 直调 `preflight_audit` 验证；端到端用受控 shim 证明接线） |
| **F-02** | `engine/compose.py:_assemble` | 结构性缺口 | 引擎拼出的 prompt 只有固定模板块、不含自由文本 → UCA-01（子串词表）在引擎 pipeline 里结构性不可触发 | 否（插件侧 v4.3 已扩到用户消息扫描，见 `docs/19` §7.1；引擎侧由 F1 决策表覆盖注入侧） |
| **F-03** | `engine/compose.py:compose()` | 可观测性缺口 | 主路径不调 `lookup_mutex`：规则 2/5/6 无 `dropped` 留痕；规则 7/8 与 refuse 分支共用 `constitution_reject` reason → "丢对域"成立，"哪条规则触发"不可从审计区分 | 否（F5 按"丢对域 + 有留痕"判定，notes 记录） |
| **F-04** | `engine/memory.py:recall()` | 语义混淆 | `excluded_by_budget = matched - len(returned)`，而 `returned` 同时受 `retrieval.topK` 限制 → **预算充足但 matched>topK 时也 `truncated=true`**；"预算打满"画像会系统性偏高 | 否（INV-09"不含静默空/不假通过"仍成立；F4 在 notes 标明口径） |
| **F-05** | `plugin/dsh-lingshu/index.js:85-100` | 可观测性缺口 | `readJsonl` 对坏行逐行 try/catch 静默丢弃、读失败返回 `[]` → 与"无记忆"不可区分，无审计事件 | 否（INV-16 只判**写路径**；读路径记为 `observability_gap`，F6 计数但不算失败） |
| **F-06** | `docs/23 §5.5` 预注册区间 | 设计观察 | 合成块总量约 27–39 token，`tokenRatioMax` 2%–30% 对应 160–2400 token → **6 档预注册压力全部处在"无压力区"**，不会触发截断 | 否（F5 用族内探针覆盖 ratio→0 的截断/溢出路径，与预注册行分开统计） |

> **诚实边界**：以上全部是**参考实现/插件**层面的证据，不是真实流量证据；不构成任何
> `measured` 升级依据（`docs/20` §1.2/§5）。修 F-01/F-02 需要新 ADR（会改变 pipeline 语义），
> 不属于 v2 范畴。

---

## 二、逐条证据与复现

### F-01 引擎 pipeline 未接 conflict → UCA-06 降维在链路上不可达

- **证据链**：
  - `engine/pipeline.py:105`：`verdict = metacog.verdict(rec, self.cfg, emotion_intensity=...)`
    —— `metacog.verdict` 有 `conflict: str = "none"` 形参（`engine/metacog.py`），但**没人传**；
  - `engine/pipeline.py:130`：`"conflict": verdict["conflict"]` 恒为 `"none"`；
  - 于是 `pipeline.py:147-151` 的 `RequisiteVarietyGuard.check(conflict="none", ...)`
    恒返回 `fallback=False`；`preflight_audit(conflict=...)` 也永远拿不到 `unresolved`。
  - 引擎里**没有** `detectConflicts` 的对应实现（插件侧有，`index.js#detectConflicts`，v5.1）。
- **复现**：F1 端到端用例 `e2e_uca06_needs_shim`（`tools/chaos_v2/families/f1_stpa_matrix.py`
  `_MetacogConflictShim`）：把 `pipeline.metacog.verdict` 换成受控 shim 送 `conflict="unresolved"`，
  真实的 `guard` / `_apply_variety_fallback` / `preflight_audit` 随即按契约动作
  （`fallback=true`、`dropDomains=["style"]`、`verdict=allow`）。
  → 说明**降维机制本身是好的，缺的是"冲突从哪里来"这一段接线**。
- **影响**：任何声称"engine 端到端验证 UCA-06"的表述都不成立；插件侧不受影响（v5.1 已接线，
  host 证据见 `acceptance/chaos/chaos-20261026-234400-host.json` 的 S4）。
- **建议**：单独任务补 `pipeline` 的冲突检测（或显式传 `conflict` 参数 + 新 ADR），
  不要在压测里顺手改。

### F-02 引擎 prompt 无自由文本通道 → UCA-01 在引擎 pipeline 结构性不可触发

- **证据链**：`engine/compose.py:_assemble()` 只拼固定模板块
  （`[constitution] 原则 1-5` / `[fact] 置信度=…` / `[safety] …` / `[style] …` / `[user] 当前消息`），
  **不嵌入召回原文、也不嵌入用户消息正文** → `preflight_audit` 扫的 `prompt` 里不可能出现
  `_OVERRIDE_PATTERNS`（"忽略宪法/放宽拒绝/…"）。引擎侧越狱扫描只对"注入 prompt"有意义，
  而注入 prompt 在这个实现里没有自由文本。
- **复现**：F1 端到端用例 `e2e_uca01_injected`（`_RenderContaminatedShim`）在 render 输出末尾
  追加一句词表句 → `preflight_audit` 立刻 `block` + `UCA-01`；良性对照不熔断。
- **影响**：与 F-01 同性质——机制可达，但在引擎端到端链路上**不可由真实输入触发**；
  host 侧越狱扫描由插件 `user/message` 通道承担（`docs/19` §7.1），本发现不否定插件能力。
- **建议**：若要在引擎侧压真实越狱，需要先定义"用户消息如何进入注入 prompt"，属契约变更。

### F-03 compose 主路径不查互斥表（规则可观测性缺口）

- **证据链**：`engine/compose.py#compose()` 只在 `forced_conflicts` 参数里用 `lookup_mutex`；
  主路径 8 条规则是**硬编码分支**：规则 1/3/4 有专属 `reason`，规则 2/5/6 只改局部变量
  （规则 5/6 的 `whitelist` 只写 `dropped`、不改域），规则 7/8 被 `if refuse:` 统一覆盖、
  reason 一律 `constitution_reject`。
- **复现**：F5 对 8 条规则各造一例，断言"丢的域正确 + `dropped` 有留痕"（8/8 通过）；
  但无法断言"是哪条规则"。F5 notes 已写明，不把共享 reason 当失败。
- **影响**：审计/复盘时"为什么丢了 style"只能靠上下文推断；对 `docs/19` I3 的实现不受影响。
- **建议**：给每条规则一个 `rule_id` 字段（向后兼容追加），或让 `compose` 统一走 `lookup_mutex`。

### F-04 `excluded_by_budget` 混用 topK 与预算不足

- **证据链**：`engine/memory.py#recall()` 末尾
  `excluded_by_budget = max(0, matched - len(returned))`，而 `returned` 上限是
  `retrieval.topK`（默认 3）；`truncated = truncated_any or excluded_by_budget > 0`。
- **复现**：F4 的 `tiny_budget` 行（budget 900、matched 11、returned 3 → excluded 8、truncated=true）；
  corpus-gen 的预检也复现同一条（`dens30` matched=31）。
- **影响**：任何把 `excluded_by_budget` 当"预算打满率"的画像都会偏高；
  `docs/20` 的标定若用该字段需注意（当前未用）。
- **建议**：拆成 `excluded_by_topk` 与 `excluded_by_budget` 两个字段（追加，不破坏 schema）。

### F-05 插件读路径静默降级（observability gap）

- **证据链**：`plugin/dsh-lingshu/index.js:85-100` `readJsonl`：坏行 `catch { return null }` 后过滤；
  整文件读失败 `catch { return [] }`。写路径（`memorywriter` + `fs.appendFileSync`）失败会抛错，
  被 `hook_error` 审计留痕（F6 已证明：`write_failure_surfaced=1`、`false_success=0`）。
- **复现**：F6 `existing_torn_line`（撕裂行被静默丢弃、`torn_entry_recalled=0`、无审计）与
  `memory_path_is_dir`（读失败返回 []、与"无记忆"不可区分）。
- **影响**：真实磁盘/并发写损坏时，"记忆全损"与"刚开库"在观测上不可区分；写路径不受影响。
- **建议**：读路径增加 `memory_read_error` 审计事件（计数 + 首行号），不改变 fail-open 行为。

### F-06 预注册的预算压力区间实际是"无压力区"

- **证据链**：`compose.ASSUMED_CONTEXT_TOKENS = 8000`，`tokenRatioMax ∈ [0.02, 0.30]`
  → 预算 160–2400 token；而普通 payload 的拼装块 `_est` 合计约 27–39 token。
- **复现**：F5 预注册 18 行在 6 档 ratio 下 `truncated` 全为 false；只有族内探针
  （2000 字 `emotion_label` 撑大 style 块 / ratio→0）才压出 `truncated=true` 与 `overflow`。
- **影响**：不能把"F5 通过"读成"预算压力下宪法不被挤掉"已充分压测——只能说
  "在无压力区成立 + 探针证明极端截断路径也成立"。
- **建议**：把 F5 的 ratio 档位改成 `0.005 / 0.01 / 0.02 / 0.05 / 0.10 / 0.30`（下一版语料），
  或在 README 里明确"2%–30% 档是无压力区，极端压力由探针覆盖"。

---

## 三、附：v1 基线一致性观察（V1-DRIFT，不是 v2 缺陷）

**观察**：`chaos-set/README.md` §7 的指纹表与 `docs/22 §2` 的四剧本表，对 `variety.jsonl`
仍记为「1 行 / 482 B / `70af8170…`」；而实际文件为「**8 行 / 3868 B / `a2ca90c4…`**」。

**证据**：该文件 mtime = `2026-10-26T20:36:43`，**早于本次 v2 任务开始时间（2026-10-27T00:00）**；
v2 的全部改动都发生在 `chaos-set/v2/`、`tools/chaos_v2/`、`docs/23`、`acceptance/chaos/v2/`，
未触碰 v1 任何文件（`tests/test_chaos_campaign.py` 的 mtime 断言可复核）。

**影响**：按 `docs/22 §3`「语料指纹变更 = 判定基线变更，必须同步改 §5 判定与 `chaos-set/README.md`
指纹表」的纪律，这次漂移当时没有同步；v1 的 `run_chaos.py` 按行消费 8 行语料仍能跑通，
所以不是功能故障，而是**基线文档与语料不一致**。

**v2 的处理**：**不修 v1**（红线 9：v2 不改 v1 语料/文档）。v2 侧只做两件事：
1. 在 `tests/test_chaos_campaign.py` 里把红线断言拆成"3 份与 README 一致 + 4 份 mtime 早于本任务"，
   并把 `variety.jsonl` 的不一致作为已知历史漂移标注（不静默通过、也不假装一致）；
2. 在本文件记录，供后续单独任务更新 `chaos-set/README.md §7` 与 `docs/22 §2`（需 H0 批准，因为有
   「判定基线变更」语义）。

**建议**：后续任务把 v1 的 S4 行数改为 8 并重算指纹表；同时确认 `docs/22 §5` 的 S4 判定是否需要
按 8 行调整（当前 `engine_variety` 逐行检查，判定不依赖行数，预计只需改文档与指纹）。

---

## 四、处理纪律

1. **不在 v2 任务里修**：以上全部只记录；改产品代码需要单独任务 + 对应 ADR。
2. **不因为发现而改判定**：F-01…F-06 都不修改 `docs/23 §4` 的不变量口径，
   也不放宽 `docs/22`/`docs/20` 的任何门槛。
3. **每条发现必须有复现路径**：见上（族模块用例如实写在 `tools/chaos_v2/families/*.py`，
   corpus case 名可在 `chaos-set/v2/*.jsonl` 检索）。
4. **收口方式**：修完在下一份 CHANGELOG 记一行 + 把本条标 ✅ 并附新的 campaign run_id；
   未修就保持 open，**不得从报告里删掉**。
