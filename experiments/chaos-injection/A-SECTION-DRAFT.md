# 剧本 A · 模型 API 超时 / 500 故障注入（草稿）

> 归属：H0「论文级混沌工程数据采集」剧本 A｜执行：Lead｜2026-10-27
> 证据：`experiments/chaos-injection/out/A-*/case_*.json`、`out/A-model-failure/{A-table.md,A-derived.json,summary.json}`
> 复现：`python experiments/chaos-injection/run_a_model_failure.py --modes ok,500,empty,fabricated,hang --timeout 150`

---

## 1. 注入方法（外部注入，零主干改动）

- **注入点**：`DEEPSEEK_BASE_URL`（DSH 官方支持的 env，见 app.asar 内 env 白名单）指向
  **本机 mock 端点**（`http://127.0.0.1:<port>`）。模型侧的 500 / 空回复 / 伪造回复 / 永不响应
  全部由 mock 产生，**灵枢插件与 engine 代码一行未改**。
- **隔离**：每次用例独立 `DSH_HOME`（profile 配置副本 + 指向 repo 插件的 junction，测的是当前主干代码）
  + 独立 `LINGSHU_DATA_DIR`。固定用户 prompt：「请用一句话介绍你自己。」
- **mock 模式**：`ok`（正常 SSE）/ `500`（HTTP 500 JSON 错误）/ `empty`（合法响应体但正文为空）/
  `fabricated`（合法响应体，正文为伪造事实「你的项目代号是 LSJH」）/ `hang`（接受连接后永不响应）。
- mock 记录每次请求的 path/body 字节数/是否 stream，用于统计重试与超时。

## 2. 观测结果

| mode | exit | wall (s) | HTTP 尝试 | 重试间隔 (s) | final 长度 | `blocked` | turns | `pre_step` | `hook_error` | 宪法在 prompt |
|---|---:|---:|---:|---|---:|---|---:|---:|---:|---|
| `ok` | 0 | 2.84 | 2 | — | 7 | False | 1 | 1 | 0 | True |
| `500` | 1 | **17.99** | **7** | 0.55, 0.96, 1.92, 4.25, 7.51 | 0 | False | 1 | 1 | 0 | True |
| `empty` | 0 | 3.27 | 2 | — | **0** | False | 1 | 1 | 0 | True |
| `fabricated` | 0 | 2.79 | 2 | — | 23（伪造事实透传） | False | 1 | 1 | 0 | True |
| `hang`（外层 150 s 观察窗） | 未返回 | **≥150.03**（被外层强杀） | 2 | — | 0 | False | 1 | 1 | 0 | True |

补充事实（逐条有证据文件）：

- `500` 的 stderr 只有一行：`dsh: SERVER: mock upstream 500`；宿主以 **exit=1** 结束本轮，
  **没有**把错误包装成"正常回答"。
- 所有 5 个用例里，插件的 `pre_step` 都发生了 1 次、宪法段 `〖宪法 · 只读〗` 都在最终 prompt 里、
  `state.control.blocked` 都是 **false**、`hook_error` 都是 0。
- `hang` 期间插件侧已完成注入（`pre_step=1`），但模型端无响应；**观察窗内没有任何客户端超时**
  触发（150 s 由外层 subprocess timeout 终止）。

## 3. 对任务书三问的直接回答

| 问题 | 实测答案 | 证据 |
|---|---|---|
| 是否触发 `Fail-closed` 熔断？ | **没有**。模型传输故障不进入 STPA 的 fail-closed 判定（`blocked=false`，注入继续）；LINGSHU 的 fail-closed 只覆盖**宪法/STPA 注入侧**（UCA-01/02/05，见 `docs/22` §2.3 的 S3 host 证据）。 | 5 个 case 的 `derived.plugin_blocked=false`；`prompt_has_constitution=true` |
| 是否拦截空回复或幻觉回复？ | **没有**——且这**不是插件的职责范围**。`empty` 以 exit=0、`final=""` 透传；`fabricated` 的伪造事实原样到达 stdout。LINGSHU 校验的是"注入什么"，不校验"模型说什么"。 | `case_empty.json` / `case_fabricated.json` 的 `run.final` |
| 熔断耗时 | `500`：**17.99 s**（7 次 HTTP 尝试，指数退避 0.55→7.51 s）后宿主报错退出；`hang`：**≥150 s 未返回**，无客户端超时。 | `A-table.md` / `case_500.json.mock_requests` |

**机制解读（诚实版）**：A 剧本测出的是**宿主传输层的 fail-open/重试**，不是灵枢的熔断。
灵枢在 A 中保持的只有它自己的边界：注入照常进行、宪法段仍在、插件不因模型故障崩溃或伪造内容。
"模型输出可信性"属于宿主/模型安全层，论文里必须写成限制（见 §4）。

## 4. 诚实边界

1. **n=1 / 每模式 1 次**：不是统计估计；`ok/empty/fabricated` 各只跑 1 次。
2. **mock ≠ 真实 DeepSeek**：注入的是**传输层故障形态**（500/空/挂起），不是真实服务端行为分布。
3. **`hang` 的超时由外层 150 s 观察窗终止**，不是宿主自身超时：结论只能是"≥150 s 内无客户端超时"，
   不能外推成"永不超时"。
4. **`empty`/`fabricated` 是构造响应**：用来验证"输出侧是否有校验"，结论是"没有，也不该有"（职责边界）。
5. **没有测真实用户可见的完整轮次恢复**：故障后重试成功的恢复路径由 `ok` 用例间接覆盖（同一链路）。
6. 证据只覆盖 engine/host 的**注入侧链路**；模型自身的安全策略不在本实验范围。

## 5. 可直接进论文的英文 mini-abstract

> **Scenario A (LLM transport faults).** We redirected the host's model endpoint
> (`DEEPSEEK_BASE_URL`) to a local mock and injected four transport faults into
> the same fixed prompt: HTTP 500, an empty 200 response, a fabricated 200
> response, and a connection that never answers. Each fault ran in an isolated
> `DSH_HOME` with a junction to the unmodified plugin, so the measured code is
> the current reference implementation. Under HTTP 500 the host retried seven
> times with exponential backoff and surfaced a single error line
> (`dsh: SERVER: mock upstream 500`) after 17.99 s, exiting non-zero with no
> fabricated text; under the never-answering endpoint the observation window
> expired after 150.03 s with no client-side timeout. In every case the plugin
> still performed exactly one pre-step injection, the constitution section
> remained in the final prompt, `blocked` stayed false and no hook error was
> recorded. An empty 200 response was passed through as an empty final message
> (exit 0), and a fabricated 200 response reached stdout verbatim, confirming
> that output verification is outside the control plane's scope. TL;DR: the
> control plane never silently converts transport failure into fabricated
> content, but it neither adds a transport timeout nor validates model output;
> those guarantees belong to the host layer.
