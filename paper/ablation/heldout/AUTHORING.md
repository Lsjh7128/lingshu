# Held-out 越狱集 v1 · 作者说明（AUTHORING）

> **文件**：`paper/ablation/heldout/heldout-jailbreak-v1.jsonl`
> **行数**：20（JSONL；UTF-8 无 BOM；LF；文件尾单个换行）
> **sha256**：`1bbfb9d5c0dce18e537c6f1e7cab7a72dab572b28105321aa7a57558e330d931`
> **编写者**：A0 ｜ **时间**：2026-10-27 约 00:58–01:02（Asia/Shanghai），写作耗时约 10–15 分钟（自估，非精确计时）
> **字段**：仅 `{id, family, text}`；**不含 `expect` / `blocked` 等任何标签**（按 task-12 第一步要求）。

---

## 1. 我按什么写的

写作依据只有一条：**"一个普通用户会怎么诱导模型放弃自己的规矩"**——不是安全研究者的攻击模板库，而是日常对话里能见到的
软化、套近乎、下指令、给暗号、装解码器这几种社交话术。族划分由 task-12 指定，我按"改写载体"把它们落到具体句子上：

| 族（family） | 定义 | 本批手法 | 条数 | 例子 |
|---|---|---|---|---|
| `word_order` | 语序倒装 / 拆散 | 把"别管规则"拆成短句、倒装、塞进"抽屉"类比喻；**不使用原词** | 4 | hj-01、hj-04 |
| `synonym` | 同义替换 | "条条框框 / 审慎模板 / 设定 / 规程 / 过滤那层"等口语替身 | 4 | hj-05、hj-08 |
| `cross_language` | 跨语言混写 | 英/日/中混排，避开英文原词（用 guardrails / policy / filters / settings 一类非原词） | 4 | hj-09、hj-11 |
| `anaphora` | 指代消解 | 用"上面那些 / 前面那一堆 / 你开头收到的那些"指代规则，不下定义 | 4 | hj-13、hj-15 |
| `encoded` | 编码后置 | 先给 UTF-8 base64（2 条）或 UTF-8 hex（2 条），让模型"解码后照做" | 4 | hj-17、hj-19 |

**共同约束**：所有句子的目标动作都只是"让模型搁置/放弃规则"，**不索取任何危险或违法信息**；
不含真实人身或技术危害内容。目的是测闸门能否识别"被改写过的越狱意图"，不是产出可复用的越狱模板。

---

## 2. 诚实披露：本批**不是**严格意义的 blind（请勿删节）

task-12 要求作者"未读过词表"。就**撰写期间**而言成立：从收到任务起，我**没有**再读
`engine/control.py` 的 `_OVERRIDE_PATTERNS`、没有读 `plugin/dsh-lingshu/index.js` 的 `JAILBREAK_PATTERNS`、
没有读 `chaos-set/jailbreak_variants.jsonl`，也没有 grep 这些词表。

**但必须披露更早的暴露**：本 session 约 00:10，为撰写 Method 章节大纲，我曾**完整读过** `engine/control.py`，
该文件里的 `_OVERRIDE_PATTERNS`（六条：忽略宪法 / 放宽拒绝 / 不再需要遵守 / 取消安全约束 /
`ignore the constitution` / `bypass safety`）当时进入过上下文；`docs/22-混沌工程.md` §2.3 还转述了
「忽略之前的指令」（作为插件 `JAILBREAK_PATTERNS` 的命中项）与「放宽拒绝」。
我**从未读过** `JAILBREAK_PATTERNS` 的正文，也**从未打开** `jailbreak_variants.jsonl`（只从
`paper/data/INVENTORY.md` 的二手统计知道它存在 24 行、24/24 自带 `matched_patterns`）。

**影响**：这批语料满足"写作时未读词表"，但**不满足严格 blind**——我不可能在写作时"忘记"那六条。
事实上，我在造句时刻意回避了上述六条及其直接变体，**这一回避本身就是词表记忆仍在起作用的证据**。

**处置建议（请 A4 / lead 采纳其一）**：
1. 在 RESULTS 里把本批标记为 **semi-blind**，注明暴露来源与范围（六条引擎覆盖词 + 一条插件词的中文转述）；
2. 若要严格 blind 留出集：由**未接触过词表**的成员另写一批，或对本批抽 ≥10 条由该成员重写后另出 v2；
3. 无论如何，**不要**把本批当作唯一的泛化证据；它更适合作为"改写鲁棒性"的下界观察。

---

## 3. 内容安全边界

- 20 条均无真危害载荷：没有武器/漏洞利用/违法操作/人身攻击内容；
- `encoded` 族的解码内容同样是"搁置规则"的指令，**不写进本文件**（避免二次扩散），只保留编码串；
- 本文件与 jsonl 都不含任何可被直接复用的"越狱收益"内容。

---

## 4. 冻结与复现

- 冻结哈希（对 jsonl 原始字节）：
  `1bbfb9d5c0dce18e537c6f1e7cab7a72dab572b28105321aa7a57558e330d931`
- 复现校验（任选其一）：
  ```powershell
  Get-FileHash .\heldout-jailbreak-v1.jsonl -Algorithm SHA256
  ```
  ```bash
  sha256sum heldout-jailbreak-v1.jsonl
  ```
- **本说明写下后 jsonl 不得再改**。若要修改，出 `heldout-jailbreak-v2.jsonl` 并重新记录 sha256 与原因。

---

## 5. 已知局限

1. 样本量小：20 条、每族 4 条，任何分族比例都会有很大的置信区间（评测方应报区间而非点估计）。
2. 语域偏差：以中文口语为主；`cross_language` 只做英/日/中混排，未覆盖其他语言。
3. `encoded` 只用了 base64 与 hex 两种编码，未做多层嵌套/隐写/同形字替换。
4. 上述 §2 的暴露问题：本批对"泛化"的证明力应打折，建议按 semi-blind 使用。
5. 未做人工可用性预检（例如"人类读者是否能看出这是越狱意图"），因此不能据此推断真实用户分布。
