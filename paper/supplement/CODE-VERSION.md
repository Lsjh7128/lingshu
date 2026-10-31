# CODE-VERSION.md —— 论文结论 ↔ 代码版本绑定

> 为什么需要这份文件：`FREEZE.json` 冻结的是**计划**（`PLAN.md` 的 sha256），
> 不是**代码**。代码在动，而论文里的数字有明确的来源版本。不写清楚，三个月后
> 没人能回答"这个数是用哪版跑出来的"。

## 一、论文基线（所有已报告数字的来源）

| 项 | 值 |
|---|---|
| 提交 | `d551079` |
| 日期 | 2026-10-27 20:53:45 +0800 |
| 提交说明 | `chore(release): 开源前脱敏与发布准备（v1.0.0-paper-baseline 基线快照）` |
| 注入侧 policy | 无独立版本号（`docs/24` v1.1） |
| 工具侧 policy | `t-uca-1.1` |
| `plugin/dsh-lingshu/index.js` | git blob `0bfe036616ed11d81e973318e8f7841c51b049ab`，105,926 字节 |
| `plugin/dsh-lingshu/tool-gateway.js` | git blob `035d30b2a9f7c5ba76858f45692ab390e7bd8bd5`，41,315 字节 |

**可取用的基线副本**：`D:\LSJH\lingshu-release\plugin\dsh-lingshu\`
（`index.js` 同为 105,926 字节，sha256 `DC72158A…`）。

## 二、版本时间线（为什么论文写 `t-uca-1.1` 是对的）

```text
2026-10-27  d551079   t-uca-1.1   ← 论文基线，所有已报告数据在此
2026-10-29  51de07a   t-uca-1.2   ← phase-c 固化，论文之后
2026-10-30  （未提交） t-uca-1.3   ← 本次误报修复 + 反馈层，仅 lab
```

`t-uca-1.1` 由 `d551079` 引入，`t-uca-1.2` 由 `51de07a`（10-29 19:44）引入，
**晚于论文基线两天**。因此 `10c-appendix-tool.tex` 与 `06e-actuator.tex` 里的
`t-uca-1.1` 是准确的，不应改成 1.2 或 1.3。

## 三、hard 档表达行为在两个版本间的差异

| 版本 | hard 档（severe 或连击升级） |
|---|---|
| `t-uca-1.1`（论文基线） | 剥掉 style 段，注入系统口吻 `〖安全熔断〗`；模型回复离开人设 |
| `t-uca-1.3`（lab） | 保留 style 段，注入人设内 `Safe_Redirect`（≤1 句）；模型回复留在人设内 |

论文 `05-method.tex` 与 `06h-immersive-case.tex` 记录的是**前者**，并已在正文加
`Version pin` 说明该行为是版本特定的。**不得**把观测改写成后者的语气——那等于
声称做过一个没做过的实验。

## 四、当前部署状态（2026-10-30 20:51 之后）

| 位置 | 内容 |
|---|---|
| `~/.dsh/plugin-src/dsh-lingshu-v1.0.0` | lab 版（`index.js` sha256 `2FB7555…`，工具侧 `t-uca-1.3`） |
| `…/dsh-lingshu-v1.0.0.bak-frozen-20261030-205116` | 部署前的冻结版（`index.js` sha256 `341899AF…`） |
| `plugin/dsh-lingshu`（仓库产品副本） | `BA9FAD98…`（2026-10-30 00:00 状态，非论文基线） |
| `plugin/dsh-lingshu-lab` | 与当前部署相同 |

**复现论文数字请用 `d551079` 或 `lingshu-release`，不要用当前部署。**
当前部署与论文基线在 hard 档表达、工具闸门策略上均不同。

## 五、重跑论文实验的检查清单

1. 切到 `d551079` 或 `lingshu-release` 的插件副本
2. 确认 `tool-gateway.js` 的 `POLICY_VERSION === 't-uca-1.1'`
3. 用 `LINGSHU_DATA_DIR` 指向独立目录，避免污染现有数据
4. 记录 `LINGSHU_DATA_DIR`、插件副本路径、policy 版本到结果 JSON 的 meta
5. 不要拿当前部署的 `feedback-eval.mjs` / `tool-gateway-fp-eval.mjs` 结果与论文数据对比——
   它们约束的是 `t-uca-1.3` 的行为
