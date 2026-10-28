# proposals/ · Phase B 候选防御规则提议 · H0 审批记录

> 本文件是**人工审批记录**（Lead 维护目录的只读交接），不是生效规则。
> 未在此列出的 `PROP-*.yaml` 仍为 `candidate`，等待 H0 审批。

## 审批结论（H0 指令 2026-10-28 §一.3）

| 项 | 内容 |
|---|---|
| 批准对象 | Phase B 官方 run `run-20261028-185512` 的 **P-0001（normalize_decode）**、**P-0002（stpa_filter，语种 token 正则）** |
| 批准后的状态 | `promoted_to_sandbox_baseline`（沙盒基线） |
| 批准范围 | **仅限沙盒度量**：`rules/sandbox-baseline.yaml`，通过 `tools/blue_team_patcher.merge_sandbox_baseline()` 交给 `tools/phase_b_defense.scan(patch=...)` 使用 |
| **不在批准范围** | **生产上线**。`engine/`、`plugin/dsh-lingshu/`、`rules/decay-params.yaml`、任何生产词表**零改动** |
| 生产上线路径 | 仍需 **H0 人工审批 + 立 ADR**；本项目不存在脚本自动合入生产的路径 |
| 记录时间 | 2026-10-28 |
| 证据来源 | `rules/candidate_patch.yaml`（P-0001/P-0002 条目 + `evidence.bypass_ids`）；`experiments/phase-b/runs/run-20261028-185512/regression_gen0.json` / `regression_gen1.json`（14 files / 915 passed / 0 failed） |

## 批准的两个补丁

- **P-0001**：`kind=normalize_decode`，`detectors=[base64, hex, zero_width, rot13]`；
  把编码/隐藏过的 payload 归一化后重跑 Phase A 规则（`stage=patch_normalize`）。
- **P-0002**：`kind=stpa_filter`，一条 ≤200 字符的俄/阿语 token 正则（`RX-01-01`）；
  从 bypass payload 提取符号串，非写死词表、非语义翻译（`stage=patch_regex`）。

两者在官方 run 中均通过每代回归锁（`audit892`：14 files / 915 passed / 0 failed）与
良性"不新增误伤"自检（74 条良性，基线误伤 2 条，新增 0 条）。

## 回滚方式

1. 从 `rules/sandbox-baseline.yaml` 删除对应 `baseline_patches` 条目；
2. 把 `rules/candidate_patch.yaml` 中该条 `status` 改回 `accepted_sandbox`，
   并去掉 `promoted_at` / `promoted_to`；
3. 生产文件无需回滚——本次提升**从未写入生产文件**（sha256 前后一致，见任务记录）。

## 与生产的关系（再次强调）

- 沙盒基线只影响 Phase B/C 的**沙盒度量**；`phase_b_defense.scan(text)` 的默认行为
  （纯 Phase A 基线）未变，Phase B 20 代历史数字仍可复现；
- 生产规则是否合并 = 人（H0）手工改 `engine/`/`plugin/` + 立 ADR，不在本目录决策。
