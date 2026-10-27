# 18 · 与 AstrBot 协同（设计 + 适配计划）

> 状态：**设计定稿，适配器骨架已写，尚未在真实 AstrBot 上跑过。**
> 目标：让 LINGSHU 的记忆/元认知/情感/目标/关系那套东西，服务 AstrBot 的对话，
> 而不是只活在 DSH 的插件里。
> 依据：AstrBot 源码 `astrbot/core/star/register/__init__.py`（2026-10-25 抓取）。

---

## 1. 为什么这条路比 DSH 插件更顺

| 维度 | DSH 插件（已实现） | AstrBot 适配器（本方案） |
|---|---|---|
| 语言 | JS，**要重写一遍引擎** | Python，**直接 import `engine/`** |
| 注入点 | `ctx.systemPrompt.section({name, order, text})` | `on_llm_request` 里改 `req.system_prompt` |
| 轮结束 | `session/event` 观察 `turn/end` | `on_llm_response` / `after_message_sent` |
| 工具结果 | `session/event` 观察 `tool/result` | `on_llm_tool_respond` |
| 状态隔离 | 进程内 + `$DSH_HOME` | 按 `session_id`（QQ 群/私聊）分区 |

**关键收益**：`engine/`（20 个模块、248 项验收）可以**零重写复用**。
JS 插件当初是为了验证宿主契约，而不是为了复用引擎——这点必须说清楚：
**它不是产品形态，是契约验证器。**

---

## 2. 钩子映射（AstrBot → LINGSHU）

AstrBot 提供的相关钩子（源码导出的注册器）：

```
register_on_llm_request      register_on_llm_response
register_on_decorating_result register_after_message_sent
register_on_agent_begin      register_on_agent_done
register_on_llm_tool_respond register_on_using_llm_tool
```

| LINGSHU 需要 | AstrBot 钩子 | 做什么 |
|---|---|---|
| 每轮检索 + 注入（对应 `agent/pre-step`） | **`on_llm_request`** | 读用户消息 → `memory.recall` → `metacog.verdict` → 拼 4 段 → 追加进 `req.system_prompt` |
| 轮结束落库/计数（对应 `turn/end`） | **`on_llm_response`** | 情感衰减、轮次 +1、写 `audit`；用户消息的语气事件在此归档 |
| 工具结果入记忆（对应 `tool/result`） | **`on_llm_tool_respond`** | 走 `memorywriter` 校验后落盘 |
| 目标/关系事件抽取 | **`on_llm_request`**（用户消息）+ `on_decorating_result`（可观测到成品） | 复用 `engine/goal.py`、`engine/relation.py` |
| 冷启动阶段判定 | `on_llm_request` 里按 `session_id` 的累计轮次 | `engine/onboarding.py` |

**AstrBot 没有 `agent/pre-step` 那种 waterfall 契约**，所以不会重演 DSH 那个
"返回 undefined 崩整轮"的坑；但仍要遵守一条：
**`on_llm_request` 里任何异常都必须吞掉**，否则会挡掉用户消息。

---

## 3. 注入形态（4 段，与 DSH 插件一致）

```
〖宪法 · 只读〗…               ← 永远注入（除非 _frozen 校验失败）
〖认知状态 · 仅影响表达〗…      ← 置信度/表达方式/丢弃提示
〖当前目标〗…                  ← 只注入最顶层 1–2 个 active 目标
〖关系状态〗…                  ← 仅在有冲突/阶段变化时出现
```

**顺序不可改**；总预算照旧受 `compose.tokenRatioMax` 约束。
冷启动前 3 轮只注入宪法层（`onboarding.allow()` 决定）。

---

## 4. 与 DSH 版的差异（必须承认的边界）

| 项 | DSH | AstrBot | 影响 |
|---|---|---|---|
| `_frozen` 只读保证 | 插件内自校验 | 同样只能自校验（Python 侧算哈希） | 一样弱，不假装更强 |
| 会话身份 | `session_id` | QQ 群/私聊 ID | 需做群聊/私聊的策略区分 |
| 多用户并发 | 单会话为主 | **同一机器人多会话并发** | 状态必须按会话分区，不能全局单例 |
| 长度预算 | 900 tokens | 同上，但 QQ 侧无 token 计数 | 用字符数近似 |

**多会话并发是最大的新风险**：`engine/` 目前的状态对象是单例式的
（`GoalPlugin(cfg)` 一个实例）。适配器必须**每个 session 一个实例**，
且落盘路径按 session 分目录，否则群聊之间会串记忆。

---

## 5. 安装与回滚（待实测）

```text
1) 确认 AstrBot 版本与插件目录（默认 data/plugins/）
2) 把 integrations/astrbot/ 复制进去（或软链）
3) 在 WebUI 的插件管理里启用，看日志是否打印 "lingshu adapter ready"
4) 回滚：WebUI 停用插件 + 删除目录，不碰 AstrBot 本体
```

**不碰 AstrBot 本体代码**——只作为插件存在。这是与 DSH 那次事故（改 profile 把会话搞坏）
相反的做法：**永远以"可卸载的插件"身份存在**。

---

## 6. 已知缺口

1. `integrations/astrbot/` 只有骨架，**没有在真实 AstrBot 上跑过**；
2. `engine/` 的状态对象需改造为"按会话实例化"（目前是调用方自己 new）；
3. AstrBot 的 `req.system_prompt` 字段名以其版本为准，需实测确认；
4. 多会话并发、长群聊上下文、超长回复截断——都还没压测。
