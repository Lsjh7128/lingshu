# LINGSHU ↔ AstrBot 适配器

> **状态：骨架，未在真实 AstrBot 上运行验证。** 设计见 `docs/18-AstrBot协同.md`。

## 它做什么

把 LINGSHU 的引擎（Python，`engine/`）接到 AstrBot 的对话链上：

| 钩子 | 动作 |
|---|---|
| `on_llm_request` | 召回 + 元认知定档 + 拼 4 段，追加进 `req.system_prompt` |
| `on_llm_response` | 轮次 +1、情感衰减、冷启动阶段推进 |
| `on_llm_tool_respond` | 工具结果 → 走校验后写记忆（待实现） |

## 为什么不是把 DSH 插件搬过来

DSH 插件是 **JS**，写它是为了验证宿主契约（waterfall / render / session-event），
**不是**产品形态。AstrBot 是 Python，可以直接复用 `engine/`（20 模块 / 248 项验收），
零重写。

## 安装（待实测）

```text
1) 把本目录放进 AstrBot 的 data/plugins/（或用软链）
2) 确保同级的 lingshu 仓库可被 import（见 main.py 顶部的路径探测）
3) WebUI 插件管理里启用，日志出现 [lingshu] 即成功
4) 回滚：停用 + 删目录，**不碰 AstrBot 本体**
```

## 三条红线

1. **钩子里任何异常都必须吞掉**——挡掉用户消息比不注入严重得多；
2. **不碰宿主本体**，永远以可卸载插件身份存在（DSH 那次改 profile 把会话搞坏的教训）；
3. **按 session 实例化状态**——QQ 群聊并发，全局单例会让不同群串记忆。

## 已知缺口

- `req.system_prompt` 字段名/可用性需按 AstrBot 版本实测；
- 记忆库落盘还没接（现在召回是空库）；
- 多会话并发、长群聊、超长回复都没压测。
