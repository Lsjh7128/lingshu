# proposals/ · Phase B 候选防御规则提议（等待 H0 审批）

> **这里放的全是"提议"，不是"生效规则"。** 没有任何脚本会把本目录的内容合入生产。

## 这些文件是什么

Phase B 的红蓝闭环每代产出一个候选补丁（`tools/blue_team_patcher.py` 生成），
它在这里以**人工审批提议**的形式存在：

```
proposals/phase-b/PROP-<run_id>-g<生成代>-<序号>.yaml
```

每份提议包含：

| 字段 | 含义 |
|---|---|
| `patch_id` / `kind` | `P-0001` / `normalize_decode` \| `stpa_filter` \| `uca_regex` |
| `evidence.bypass_ids` | 它要堵的那几条真实穿透 payload（可回溯到 `experiments/phase-b/runs/`） |
| `evidence.techniques` | 绕过用的变异手法（base64 / zero_width / ru_glossary …） |
| `impact` | 影响面：堵什么、可能误伤什么、只在沙盒生效 |
| `benign_check` | 良性语料误伤自检：基线误伤 2/74，本补丁新增 0 条（否则不会出现在这里） |
| `regression` | Phase A 回归锁结果（`--suite audit892`，必须 0 failed） |
| `rollback` | 回滚方式（删哪一条即回到基线） |
| `status` | 只能是 `candidate` / `accepted_sandbox`；**不存在 `production`** |

## 审批流程（H0 人工）

1. 读提议 → 判断"这条规则该不该进生产防线"；
2. 若批准：由**人**手工改生产规则（`engine/control.py` 的规则表或插件侧词表），
   走既有 ADR 流程与全量回归；
3. 反馈结论到 `proposals/phase-b/REVIEW.md`（批准/驳回 + 理由），
   脚本**不自动**搬运任何内容进生产。

## 与其他 `proposals/` 的关系

`engine/proposal.py` 里的"提议"是**学习旋钮提议**（retrieval.topK 之类的数值微调，
`tests/test_proposal.py` 18 项断言覆盖），与本目录的**安全规则提议**是两套东西，
只是都叫 proposal。别混用，也别把 Phase B 的规则写进 `replay-set/` 或学习提议队列。

## 纪律

- 本目录只允许 `tools/blue_team_patcher.py` 写入（`PROP-*.yaml`），`REVIEW.md` 由人写。
- 不许把 `rules/candidate_patch.yaml` 的内容原样拷贝进生产规则文件；
  批准后必须由人改写为生产规则的表达方式并补断言。
