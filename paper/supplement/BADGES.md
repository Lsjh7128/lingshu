# BADGES — 徽章代码与使用纪律

> **数据源**：`acceptance/audit/` 三份审计报告（2026-10-27）；Python **892**（14 文件）、JS **396**、变异 **42/42**（非等价）、`engine/` line **89.95%** / branch **80.71%**。
> **纪律**：这些是**静态**徽章（manually maintained），不是 CI 实时状态。README/论文仓库**不得**暗示其为 live build status；每次冻结（见 `supplement/FREEZE.json`）时手工重生成。

---

## 1. Markdown（推荐，直接粘 README）

```markdown
![Python tests](https://img.shields.io/badge/python_tests-892_passed-brightgreen)
![JavaScript tests](https://img.shields.io/badge/javascript_tests-396_passed-brightgreen)
![Mutation (non-equivalent)](https://img.shields.io/badge/mutation-42%2F42_non--equivalent-brightgreen)
![Engine line coverage](https://img.shields.io/badge/engine_line_coverage-89.95%25-yellowgreen)
![Engine branch coverage](https://img.shields.io/badge/engine_branch_coverage-80.71%25-yellowgreen)
```

一行版（空间不够时）：

```markdown
![tests](https://img.shields.io/badge/tests-892_py_%2B_396_js-brightgreen) ![mutation](https://img.shields.io/badge/mutation-42%2F42_non--equivalent-brightgreen) ![engine coverage](https://img.shields.io/badge/engine_coverage-89.95%25_line_%2F_80.71%25_branch-yellowgreen)
```

## 2. HTML（GitHub Pages / 静态站点）

```html
<p>
  <img alt="Python tests" src="https://img.shields.io/badge/python_tests-892_passed-brightgreen">
  <img alt="JavaScript tests" src="https://img.shields.io/badge/javascript_tests-396_passed-brightgreen">
  <img alt="Mutation, non-equivalent" src="https://img.shields.io/badge/mutation-42%2F42_non--equivalent-brightgreen">
  <img alt="Engine line coverage" src="https://img.shields.io/badge/engine_line_coverage-89.95%25-yellowgreen">
  <img alt="Engine branch coverage" src="https://img.shields.io/badge/engine_branch_coverage-80.71%25-yellowgreen">
</p>
```

## 3. 可选：带链接的徽章（链接到审计报告）

```markdown
[![Mutation](https://img.shields.io/badge/mutation-42%2F42_non--equivalent-brightgreen)](acceptance/audit/变异测试报告-2026-10-27.md)
[![Coverage](https://img.shields.io/badge/engine_coverage-89.95%25_line_%2F_80.71%25_branch-yellowgreen)](acceptance/audit/断言强度与覆盖率报告-2026-10-27.md)
[![Test rigor](https://img.shields.io/badge/rigor-audited_2026--10--27-blue)](acceptance/audit/测试严密性审计报告-2026-10-27.md)
```

## 4. 编码说明（shields.io 静态徽章语法）

格式：`https://img.shields.io/badge/<LABEL>-<MESSAGE>-<COLOR>`

| 转义 | 含义 | 例 |
|---|---|---|
| `_` | 空格 | `python_tests` → "python tests" |
| `%2F` | `/` | `42%2F42` → "42/42" |
| `%25` | `%` | `89.95%25` → "89.95%" |
| `--` | 字面 `-` | `non--equivalent` → "non-equivalent" |
| 颜色 | `brightgreen` / `green` / `yellowgreen` / `yellow` / `orange` / `red` / `blue` / `lightgrey` | 覆盖率用 `yellowgreen` 而非 `brightgreen`（未声称完美） |

## 5. 禁止使用的徽章（会被审稿人/读者视为过度声称）

| ⛔ 不要写 | 原因 | 可替代 |
|---|---|---|
| `mutation-44/44` 或 `100%_killed` | 44/44 不存在；100% 仅对**非等价**变异成立 | `42%2F42_non--equivalent` |
| `coverage-100%25` | 只有 `control.py` 是 100%，全仓 45.81% | `engine_line_coverage-89.95%25` |
| `bug--free` / `production--ready` / `battle--tested` | 无法被任何审计支持 | `rigor-audited_2026--10--27` |
| `all_tests_pass` 当作质量结论 | 通过数 ≠ 强度（本次审计正因发现弱/恒真断言） | 并列 `mutation` 徽章 |
| `jailbreak--proof` / `100%25_defense` | 本审计只测回归检出能力，未测端到端防御 | 论文 §7 的边界声明 |

## 6. 冻结与更新

- 徽章数值必须与 `supplement/FREEZE.json` 及论文正文（§5 / 附录 A）三个数字一致：**892 / 396 / 42/42 / 89.95 / 80.71**。
- 任何一项变化 → 先重跑审计 → 更新 `FREEZE.json` → 再改本文件与 README，**顺序不可颠倒**（防止徽章先于证据）。
- 若将来接入 CI，改成 dynamic badge（shields.io endpoint）；在此之前，README 中请加一句：*"Static badges, regenerated at each freeze; not live CI status."*
