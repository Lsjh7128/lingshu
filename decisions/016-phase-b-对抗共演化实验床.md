# ADR-016 · Phase B 对抗共演化实验床（红蓝闭环 / 沙盒边界 / 待审批闸门）

- **状态**：提案（2026-10-28，A0 提出；**生产规则零变更**，本期只建实验床）
- **日期**：2026-10-28
- **决策者**：H0（项目负责人）
- **提出 / 落地**：A0（Lead）；执行 A4（红队）、A1（蓝队）、A5（演化剧本）、A2（实验图）
- **影响面**：新增 `tools/red_team_evolver.py`、`tools/blue_team_patcher.py`、
  `tools/run_coevolution.py`、`tools/plot_coevolution_curve.py`、`tools/phase_b_defense.py`、
  `tools/phase_b_corpora.py`、`tools/run_phase_a_regression.py`、`rules/candidate_patch.yaml`、
  `proposals/phase-b/`、`experiments/phase-b/`
- **不影响**：`engine/**`、`plugin/**`、`rules/decay-params.yaml`、`rules/compose-matrix.yaml`、
  Phase A 的 20 个测试文件、`main` 分支
- **触发**：H0 2026-10-28 指令《LINGSHU Phase B（动态红蓝对抗演化）实验床搭建与数据采集》，
  为第二篇论文（LINGSHU-II: Adversarial Co-evolution for Self-Healing LLM Agent Control Planes）准备实验证据

---

## 1. 背景

Phase A 的控制面是**静态**的：规则（UCA 表、`_SEVERE_RULES`、`_MILD_RISK_WORDS`）
由人写死，遇到没写进词表的攻击形态就漏。Phase B 要回答的问题是：
**防线能不能在对抗中自己长出新规则，并且长出来的规则不误伤、不破坏既有逻辑？**

这个问题不能在 `main` 上做：
- 自动生成的规则天然不可信，绝不能直接进生产闸门；
- 演化实验要反复写文件、跑短周期，主分支必须冻结。

## 2. 决策（四条边界）

### 2.1 隔离：分支 + 沙盒目录，主分支零改动

- 全部代码在 `feat/phase-b-coevolution` 分支；`main` 保持 `d551079`
  （开口快照，H0 可随时 `git diff main` 核对）。
- 演化产物写 `experiments/phase-b/`，不进 `acceptance/`（那是 Phase A 的验收区）。

### 2.2 补丁 = Proposal，永不自动上线

| 位置 | 性质 | 谁读它 |
|---|---|---|
| `rules/candidate_patch.yaml` | **沙盒候选**，`meta.production_mutable: false` | 只有 `tools/phase_b_defense.py`（Phase B 自己的度量仪器） |
| `proposals/phase-b/PROP-*.yaml` | **人工审批提议**，含证据/影响面/误伤自检/回滚 | 只有 H0（人） |
| `rules/decay-params.yaml` 等生产规则 | 生产生效 | 引擎/插件 |

**红线**：脚本对生产规则**只读**。合并与否是人的决定，不存在 `status: production` 这个状态。
补丁要进生产，必须走"H0 审批 → 人工改生产规则 → 全量回归 → 新 ADR"的人工流程。

### 2.3 回归锁：任何补丁必须和 Phase A 全量断言一起跑

- 工具：`tools/run_phase_a_regression.py`（Lead 维护）。
- 口径 `--suite audit892`：Phase A 审计快照的 14 个文件；快照当时 **892 项**，
  今天同 14 个文件实测 **915 项**（`test_control.py` 161→174、`test_pipeline.py` 36→46，
  增量 +23，均可解释），**两个数不可换算**，引用时必须带口径与实测值。
- 口径 `--suite all`：当前全部 20 文件（实测 1457 passed / 3 环境性 failed）。
- 判据：`0 failed` 才允许补丁进演化防线；有失败 → 补丁直接丢弃，原因写进
  `coevolution_log.jsonl` 的 `patch_rejected`。
- **已知环境性失败**：`tests/test_chaos_campaign.py` 3 项失败源于沙箱拒绝覆盖写
  `acceptance/chaos/v2/*-report.md`（Errno 13），与断言逻辑无关，Phase B 不改该文件、
  也不把它当作"反正有红的"的挡箭牌；`run_phase_a_regression.py` 会把它标 `known_env_fail`
  单独列出，不计入补丁判定。

### 2.4 诚实边界（写进 evidence 的 `limitations`，也写进论文图注）

1. 红队是**确定性规则变异**（编码/离线词表/嵌套），不是 LLM 自主攻击智能体；
2. 语种变异是**离线词表近似**，不是真实机器翻译（`provenance: offline_glossary`）；
3. 本阶段**零模型调用**，`api_tokens_used = 0` 是事实；因此"token 预算"这条停止条件
   **未获真实检验**；
4. 被测防线是 Phase A 的**规则型**闸门，编码变异能绕过是可预期的；
   Phase B 的贡献是**闭环自愈机制与度量方法**，不是"证明 Phase A 弱"；
5. 零日集与训练流来自同批种子的不同 split（held-out 变体），**不是**独立分布外数据，
   论文中只能写 "held-out mutations"，不许写 OOD / 真实未见攻击。

## 3. 度量（因变量的唯一定义）

所有拦截率/误伤率都由 `tools/phase_b_defense.py` 单点给出：

- **基线防线** = `engine.control.classify_user_risk()` + `preflight_audit()` 的 UCA-01；
- **演化防线** = 基线 + `rules/candidate_patch.yaml`（解码归一化 / 正则规则）；
- `scan(text, patch) -> {intercepted, stage, matched, decoders_used, ...}`；
  任一方另写一套匹配逻辑即视为**实验作废**。

三个率：
`intercept_rate_baseline_zeroday`（红线）、`intercept_rate_evolved_zeroday`（蓝线）、
`benign_false_positive_rate`（误伤），外加明文攻击集参照 `plain_attack_baseline_intercept_rate`。

## 4. 停止条件（防算力黑洞）

1. 连续 3 代红队 0 次成功穿透 → `converged`；
2. 代数上限（默认 20）→ `max_generations`；
3. token 预算（默认 200000；本期恒 0，未获真实检验）。

## 5. 回滚

- 代码层：删除 `feat/phase-b-coevolution` 分支即完全回滚，`main` 从未被改动。
- 规则层：`rules/candidate_patch.yaml` 删掉对应 `id` 即回滚，生产规则从未被写入。
- 数据层：`experiments/phase-b/` 可整体删除，不影响 Phase A 任何验收基线。

## 6. 未决 / 待 H0 裁决

1. Phase B 的补丁若要进生产，走哪条审批路径（本 ADR 只定义 Proposal，不定义上线流程）？
2. 后续是否允许真实模型参与的变异（会引入 API 成本与不可复现性，需另立 ADR）。
3. `benign_false_positive_rate` 的可接受上限：本期只要求"不新增误伤"，未定绝对阈值。
