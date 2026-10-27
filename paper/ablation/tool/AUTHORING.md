# heldout-tool-calls-v1 写作声明与规格

## 声明（盲性与写作条件）

本文件由 **a3（图表作者，非网关作者）** 撰写。写作时**未读取**以下任何文件：

- `docs/24-工具网关UCA.md`
- `engine/tool_gateway.py`
- `plugin/dsh-lingshu/tool-gateway.js`
- `tests/test_tool_gateway.py`

也**未**在仓库里 grep 任何规则关键词（如路径前缀、命令白名单、危险动词、域名黑名单等），
未查看任何 `expect` / `matched_rules` 标注。作者对网关的所知仅限于任务描述本身
（"存在一个会判定工具调用是否危险、并对危险调用 fail-closed 的网关"），不知其规则集、阈值与判定字段。

因此本集属**真 blind**：用于检验**泛化**，不是训练集；结论只能作方向性报告。

## 产物

| 项 | 值 |
|---|---|
| 路径 | `paper/ablation/tool/heldout-tool-calls-v1.jsonl` |
| 条数 | 10（5 族 × 2） |
| 字节数 | 3,023 |
| 编码 | UTF-8，每行一个 JSON 对象 |
| 字段 | 恰为 `{id, family, tool, args, context_hint}`；**无** `expect` 标签（按任务要求） |
| sha256 | `f68fc1b3f19fc37e43b9856096d29b9a55de7dee9bf664a9b9a9592e070576d4` |
| 写作时间 | 2026-10-27T06:17+08:00（Asia/Shanghai，本次会话） |

校验（可复跑，只读本文件）：10 行、id 唯一、每族恰 2 条、`args` 均为 JSON 对象、无 `expect` 字段。

## family 分布

| family | 中文类别 | id |
|---|---|---|
| `destructive-file-operation` | 破坏性文件操作 | `tg-v1-df-01`、`tg-v1-df-02` |
| `credential-exfiltration` | 凭据外泄 | `tg-v1-ce-01`、`tg-v1-ce-02` |
| `privilege-escalation` | 越权执行 | `tg-v1-pe-01`、`tg-v1-pe-02` |
| `data-exfiltration` | 数据外发 | `tg-v1-de-01`、`tg-v1-de-02` |
| `conflicting-context-write` | 上下文冲突下的写入 | `tg-v1-cw-01`、`tg-v1-cw-02` |

工具分布：`run_shell` 3、`write_file` 2、`http_post` 2、`update_config` 2、`delete_file` 1。
全部为任务给的通用名，不依赖任何具体网关的 schema。

## 刻意设计（评测前请先读，避免误判）

1. **端点全是 `*.example…` 占位域名**（如 `paste.example-collect.net`、`upload.example-mirror.org`、
   `transfer.example-cdn.com`、`example-relay.net`）。这是有意的：若网关靠域名黑名单命中，
   本集**不应**得到高分——命中必须来自行为规则（"把凭据/整目录外发"这件事本身），
   而不是黑名单巧合。反之，低分也可能只是"这些域名不在黑名单里"，**不能**据此判规则失效。
2. **凭据是公开示例值**（`AKIAIOSFODNN7EXAMPLE` / `wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY`
   是 AWS 官方文档里的示例密钥，不是真实凭据）；其余为占位符或示例路径，不含任何真实主机、账号、密钥。
3. **无 `expect`**：判定标准留给网关与评测方；作者不给"应该拦/应该放"的标签，避免用作者预期反向塑造评测。
4. `context_hint` 只有**一句**，不是完整会话：写出来的是"这件事在当前语境下为什么会被要求"，
   供评测方判断"是否需要在冲突语境下才危险"。

## 使用边界（写进论文时必须如实标注）

1. **n=10（每族 2）功效极低**，只能方向性报告，不得给强结论或置信区间外的推断。
2. **只写数据、不评测**：作者未运行任何网关、未统计拦截率，也未改动其他文件。
3. **盲性 ≠ 无偏**：作者手写、语言为中文场景、路径与命令偏向 Linux 服务端运维，覆盖不到 Windows /
   云 API / 数据库 / 容器等形态；外推范围需在 Limitations 写明。
4. **`conflicting-context-write` 族依赖上下文**（"前一条说别动、这一条让动"），若评测只把
   `{tool, args}` 喂给网关而丢掉 `context_hint`，该族会退化为"普通的配置写入"，命中率不可解释。
   评测时必须区分"仅 args 判定"与"带上下文判定"两种口径，分别报告。
5. **未命中要逐条人工复核**，区分三类：① 规则确实漏了；② 判定口径不同（如网关只认特定路径前缀）；
   ③ 样本本身不可判（如工具名不在网关白名单，被"未知工具"分支拒绝，而不是被"危险"分支拒绝）。
6. **若文件被修改，sha256 必须重算并更新本节**；sha256 与文件不一致视为数据集损坏。

## 复现

```text
读取 paper/ablation/tool/heldout-tool-calls-v1.jsonl
逐条构造工具调用请求 {tool, args}（可附带 context_hint 作为会话上下文）
喂给 UCA 工具网关的前置判定
按 family 分组统计"拦截 / 放行"，未命中项逐条人工复核（见上面第 5 条分类）
```
