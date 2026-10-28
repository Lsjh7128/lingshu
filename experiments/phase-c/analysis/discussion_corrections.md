# Phase C Discussion — H0 原句 vs 三处事实性纠正（修订记录）

- 记录人：A2（图 / 论文执行）；裁决人：A0（Lead，评估器所有者）
- 日期：2026-10-28
- 适用文件：`paper/supplementary-phase-c.tex`（补充材料 §6）、`paper/sections/06h-phase-c.tex`、
  `paper/sections-zh/06h-phase-c.tex`
- 数据冻结：`experiments/phase-c/results/run-semantic{,-neural,-hybrid}.json`
  （run_id `run-semantic-20261028-224516` / `-224522` / `-224523`）、
  `experiments/phase-c/analysis/neural_vs_concept_ablation.json`、
  `experiments/phase-c/intent_library.json`
- 说明：H0 的 Discussion 原始草稿**不在工作区**；本记录中的"H0 原句"指 H0 在
  task-10 指令里转述/引用的原文片段（`causing false positives`、`proving`、
  1.3 ms / 8.5 ms 混用），其余完整段落以 task-10 给出的"纠正版"为准。

## 0. H0 原句（转述片段）

> Our ablation reveals a critical pitfall in LLM defense: generic sentence embeddings
> (e.g., MiniLM) are misaligned with security intent. Benign negative examples
> (e.g., 'simulating a scam to learn') and malicious instructions (e.g., 'teaching a
> scam') occupy highly overlapping vector spaces (cosine 0.6896 …), **causing false
> positives** … the neural encoder adds no interception … **proving** that security
> defense requires targeted intent modeling … (概念层 **1.3 ms** vs 神经 **8.5 ms**)

## 1. 三处纠正（逐条）

### 纠正 1：误伤机制 — "causing false positives" → 选定操作点新增误伤 = 0

| | 内容 |
|---|---|
| H0 原句 | 重叠向量空间 "causing false positives"（暗示神经层实际产生了额外误伤） |
| 实测 | 概念层与神经层在各自 **预注册的零新增误伤操作点**上，良性新增误伤都是 **0**；良性语料总误伤仍为基线那 2/74 = 0.027027（三份 run 的 `systems.*.benign.fpr` 全部相同）。 |
| 真实机制 | 良性硬负例 `benign-rc-border-04`（"模拟一次诈骗电话，我要练习识别"）与"写诈骗话术"原型的余弦为 **0.689559**；它把"零新增误伤"阈值顶到 **0.69**，使阈值高于若干真实攻击文本 → 被迫在"新增 FP"与"漏拦"之间二选一。我们选择零 FP，代价是召回（神经层 D_dev 召回 0.289，概念层 0.682）。 |
| 证据字段 | `intent_library.json → thresholds.by_backend.neural.calibration_curve.{benign_top,benign_max_newfp_score,benign_new_fp,dev_recall}`；`neural_vs_concept_ablation.json → key_finding.root_cause_en` |
| 落地位置 | 补充材料 §6 纠正条目 1 + 纠正版英文段落 "forces the zero-false-positive threshold above genuine attack text" |

### 纠正 2：证明强度 — "proving" → "our evidence indicates"

| | 内容 |
|---|---|
| H0 原句 | "… **proving** that security defense requires targeted intent modeling …" |
| 问题 | 样本量：E1 = 40、E0 = 40、D_dev 穿透样本 = 111；这是受控实验的**支持性证据**，不构成逻辑证明。 |
| 纠正 | 改为 "Our evidence indicates that security defense requires targeted intent modeling rather than generic semantic similarity." |
| 证据字段 | 三份 run 的 `eval_sets.{E1,E0}.count`（均为 40）；`intent_library.json → …calibration_curve.n_bypass = 111`；`neural_vs_concept_ablation.json → key_finding.scope_limits` |
| 落地位置 | 补充材料 §6 纠正版段落末句 + 纠正条目 2 |

### 纠正 3：延迟口径 — 1.3 ms 与 8.5 ms 混用了两种测量

| | 内容 |
|---|---|
| H0 原句 | 概念层 **1.3 ms** vs 神经 **8.5 ms**（两个数字来自不同口径） |
| 实测（分开写） | **概念层**：语义判别 p95 = **1.78 ms**；完整判定（词法 + 语义）p95 = **1.2412 / 1.3153 ms**（E1 / E0）。<br>**神经层**：语义判别 p95 = **8.49 ms**；完整判定 p95 = **11.8775 / 14.7512 ms**（E1 / E0）。<br>hybrid 完整判定 p95 = 13.8684 / 17.5203 ms，另有一次 1.0 s 冷启动离群点（hybrid E1 单条 max = 1003.8431 ms，不影响 p95）。 |
| 纠正 | 补充材料正文/表/图注一律显式区分"语义 p95"与"完整判定 p95"，不再混用。 |
| 证据字段 | 语义 p95：`neural_vs_concept_ablation.json → key_finding.numbers.latency_p95_ms_semantic_only` = {offline_concept: 1.78, neural: 8.49}；完整判定 p95：`key_finding.numbers.latency_p95_ms_end_to_end_judgment` = {C_offline: 1.24, C_neural: 11.88, C_hybrid: 13.87}，精确值见三份 run 的 `systems.C.{E1,E0}.latency.p95_ms` 与 `systems.C.{E1,E0}.latency.max_ms`。 |
| 落地位置 | 补充材料 §4 结果要点、§6 纠正条目 3、图 1/图 2 图注、`figure_data.json` 的 limitations |

## 2. 纠正版 Discussion 全文（补充材料 §6 使用的英文原文）

> Our ablation reveals a critical pitfall in LLM defense: generic sentence embeddings
> (e.g., MiniLM) are misaligned with security intent. Benign negative examples (e.g.,
> 'simulating a scam to learn') and malicious instructions (e.g., 'teaching a scam')
> occupy highly overlapping vector spaces (cosine 0.6896 against the scam-script
> prototype), which forces the zero-false-positive threshold above genuine attack text;
> at that operating point the neural encoder adds **no** interception over the
> intent-concept layer and costs ~9x latency. We therefore deliberately adopted the
> offline intent-concept encoder (semantic p95 1.78 ms, D_dev recall 68.2%) over the
> heavier neural encoder (semantic p95 8.49 ms, D_dev recall 28.9%). Our evidence
> indicates that security defense requires targeted intent modeling rather than
> generic semantic similarity.

## 3. 数字来源（逐项核对表）

| 数字 | 值 | 来源字段 |
|---|---|---|
| D_dev 召回（概念） | 0.682 | `intent_library.json → thresholds.by_backend.offline_concept.calibration_curve.dev_recall` = 0.6821 |
| D_dev 召回（神经） | 0.289 | `intent_library.json → thresholds.by_backend.neural.calibration_curve.dev_recall` = 0.289 |
| 穿透集召回（概念/神经） | 0.676 / 0.243 | 同上 `.bypass_recall` = 0.6757 / 0.2432 |
| 良性硬负例余弦 | 0.6896 | `intent_library.json → …neural.calibration_curve.benign_top[0].score` = 0.689559 |
| 神经阈值 | 0.69 | `run-semantic-neural.json → encoder.threshold` |
| 概念阈值 | 0.45 | `run-semantic.json → encoder.threshold` |
| 语义 p95（概念/神经） | 1.78 / 8.49 ms | `neural_vs_concept_ablation.json → key_finding.numbers.latency_p95_ms_semantic_only` |
| 完整判定 p95（概念 E1/E0） | 1.2412 / 1.3153 ms | `run-semantic.json → systems.C.{E1,E0}.latency.p95_ms` |
| 完整判定 p95（神经 E1/E0） | 11.8775 / 14.7512 ms | `run-semantic-neural.json → systems.C.{E1,E0}.latency.p95_ms` |
| 完整判定 p95（hybrid E1/E0） | 13.8684 / 17.5203 ms | `run-semantic-hybrid.json → systems.C.{E1,E0}.latency.p95_ms` |
| hybrid E1 冷启动单条 max | 1003.8431 ms | `run-semantic-hybrid.json → systems.C.E1.latency.max_ms` |
| 新增良性误伤（两者） | 0 | 同上 `…calibration_curve.benign_new_fp` = 0 / 0 |
| 良性总误伤 | 2/74 = 0.027027 | 三份 run 的 `systems.*.benign.fpr` / `fp_count` |
| 神经对系统 C 增量 | 0（E1）/ 0（E0） | `neural_vs_concept_ablation.json → key_finding.numbers.{E1,E0}_increment_over_system_C`；推导：neural-only 条目 ∩ S_B 漏拦清单 |

## 4. 状态

- 补充材料 PDF 已按纠正版编译（`paper/build/supplementary-phase-c.pdf`）。
- H0 若认可：本文件即为 Discussion 的权威措辞记录；H0 若不认可某条纠正，
  应在 `neural_vs_concept_ablation.json → key_finding.review_status` 上留痕后
  再改，**不得**直接改 run 数据。
