# 02 · DSH 宿主能力核实（本机实测）

> 目的：`assumptions/未验证.md §一` 里那六条 H1–H6 一直是"架构是否成立"的前提。
> 这份文档用**本机实物证据**逐条给出结论。
> 方法：读已安装插件的源码 + 扫 `app.asar` 里的字符串。**只读，不执行第三方代码。**
> 时间：2026-10-25。

---

## 0. 证据来源

| 来源 | 路径 | 用途 |
|---|---|---|
| 插件补丁层 | `~/.dsh/profiles/desktop/cordis.patch.yml`（12 KB） | 插件注册方式、config 覆盖语义 |
| 已安装插件（真实用例） | `~/.dsh/profiles/desktop/node_modules/dsh-novel-writer/lib/index.js` | `ctx` API、`systemPrompt.section` 真实签名 |
| 已安装插件（含审批闸门） | `<DSH_BIN>` | `approval` seam 真实签名 |
| 宿主 bundle | `...\DeepSeek Harness\resources\app.asar`（182 MB） | 钩子名、事件 API 的字符串证据 |

> ⚠️ 本机**没有** DSH 官方文档目录；`@deepseek-ai/dsh-*` 核心包未解包到 `node_modules`。
> 所以下面的结论依据是**可读的插件源码 + bundle 字符串**，不是官方文档。
> 它的强度：足以做设计决策；不足以替代"按官方文档核对一遍"。

---

## 1. 六条假设的结论

| # | 假设 | 结论 | 证据 |
|---|---|---|---|
| **H1** | 支持在 system prompt 指定位置注入片段 | ✅ **完全证实**（且签名与我们猜的一致） | `ctx.systemPrompt.section({name, order, text})` |
| **H2** | 有"轮结束"钩子 | ✅ **证实** | bundle 中 `turn/end` 出现 296 次、`turn/start` 183 次 |
| **H3** | 有"模型调用前"钩子 | ✅ **证实** | `agent/pre-step` 出现 63 次 |
| **H4** | 宿主支持只读挂载 / 启动哈希校验 | ❌ **证伪**（至少就 config 而言） | `cordis.patch.yml` 注释明说"patch 是整段替换该行的 config" |
| **H5** | 插件可持久化状态 | ✅ **证实** | 插件把状态写 `$DSH_HOME/plugins/<name>/state.json` |
| **H6** | 能隔离"提议生成"与"审核" | ⚠️ **部分证实**，且**找到了比自造审核 Agent 更好的落点** | `ctx.get('approval').request(...)` → `allowed-once` |

---

## 2. H1：`systemPrompt.section` 的真实签名

证据：`dsh-novel-writer/lib/index.js`

```js
const inject = ["tools", "systemPrompt"];
// …
ctx.systemPrompt.section({
  name: "novel-writing",
  order: 150,
  text: () => {
    // 每次组装提示词时实时调用
    return promptTextFor(st.systemPromptMode, st.promptScene, st.leanWorkflow === true);
  }
});
// …
export { apply, inject, name };
```

**可用信息**：

| 字段 | 语义 | 对我们的用法 |
|---|---|---|
| `name` | 段落标识 | 每层一个：`lingshu:constitution`、`lingshu:fact` … |
| `order` | 排序号 | **直接实现我们 6 段拼装顺序**（宪法最小、用户消息最大） |
| `text` | **函数**，组装时实时求值 | 正好实现"注入预算/空段丢弃"的动态逻辑 |

**作者踩过的坑（必须照抄结论）**：

> 原文注释：*"DSH 0.1.1-rc.2 对 section 返回 null 会导致对话失败"*
> —— `ctx.systemPrompt.section` 的正文注释亦写明：`text` 返回空串时宿主 `filter(text.length > 0)` 丢弃该段。

**所以我们的硬规则**：`text()` **只能返回字符串**，不注入时返回 `""`，**绝不返回 `null`**。
这条要写进 `docs/09` 的注入契约里。

---

## 3. H2/H3：钩子名与我们猜的完全一致

扫 `app.asar`（182 MB）的字符串计数：

| 字符串 | 次数 | 含义 |
|---|---|---|
| `turn/end` | 296 | 轮结束 |
| `turn/start` | 183 | 轮开始 |
| `agent/pre-step` | 63 | 模型调用前 |
| `tool/result` | 281 | 工具返回后 |
| `ctx.on(` | 340 | 插件可订阅事件 |
| `ctx.emit(` | 66 | 插件可发事件 |
| `session/start` | **0** | 没有这个事件名（别瞎猜） |

**映射到我们的设计**：

| 我们的设计 | 宿主钩子 |
|---|---|
| 每轮检索 + 注入（`docs/02 §6`） | `agent/pre-step` |
| 情感/目标/关系落库（`turn/end`） | `turn/end` |
| 工具结果入记忆 | `tool/result` |
| 冷启动阶段判定 | `turn/start`（按累计轮次） |

**注意**：`agent/pre-step` 只有 63 次引用，比 `turn/end` 少得多——可能只在内核的特定路径使用。
**实现前应实测一次**（装个插件在不同钩子里打日志，看实际触发顺序），这是本文档唯一的"仍需实测"项。

---

## 4. H4 被证伪：patch 层可以覆盖任何插件 config

`cordis.patch.yml` 的头部注释原文：

> "Your patch layer for this dsh profile, applied after every bundle layer:
> a top-level YAML array of loader patch entries (id-targeted config overrides,
> **disables**, and insert lists; `!!js` expressions allowed)."

以及一处实战注释：

> "注意：patch 是「整段替换该行的 config」，所以下面 personaSuffix 与 personaPrefix
> 两个键必须完整重写，不能只写一个（漏掉的键会退回默认值）。"

**结论**：宿主层**没有**"插件 config 只读"这种东西 —— 用户（或任何能编辑 profile 目录的东西）可以整段替换。
所以 `docs/00 §4` 里那个"强：只读挂载 + 启动哈希校验"**在 config 维度不成立**。

**修正后的强制点设计**（已落到 `docs/00 §4`）：

| 层 | 能做什么 | 强度 |
|---|---|---|
| 代码层 | 插件包本身（node_modules）用户不会随手改 | 中 |
| **插件内自校验** | 启动时算 `_frozen/` 哈希，不匹配 → **拒绝注入并告警**（fail-closed） | **强（本项目唯一可控的强点）** |
| config 层 | patch 可覆盖 → **不可依赖** | 无 |
| 外部扫描 | `dsh-trust-check` 类工具（社区生态里已有 3841 个插件的扫描数据） | 弱（事后） |

**关键洞察**：既然 config 挡不住，就必须让**代码在每次注入前自证**：
`text()` 里读规则文件 → 校验哈希 → 不匹配则返回 `""`（不注入）并记审计事件。
**不能"照旧跑但记个日志"** —— 那等于没防。

---

## 5. H5：状态持久化

插件把状态写 `$DSH_HOME/plugins/<name>/`（`dsh-smart-home` 与 `dsh-stock-workbench` 都是这么干的）。
我们 `docs/14 §2.3` 的"本地缓冲 + 补写"可以直接照这个落。

---

## 6. H6：`approval` seam —— 这才是"人审"该用的东西

证据：`<DSH_BIN>`

```js
const approver = ctx.get('approval')
if (!approver || typeof approver.request !== 'function') {
  return { allowed: false, reason: '…没有可用的审批通道…已按 fail-closed 拒绝。' }
}
const outcome = await approver.request({
  agent: exec.agent, toolName, callId: exec.callId,
  reason: '…', signal: exec.signal,
})
// outcome: 'allowed-once' | 'rejected' | 'cancelled' | 'unavailable'
```

**三个可用结论**：

1. **有正规的人审通道**，且是"一次一授权"（`allowed-once`），不是全局开关；
2. **缺失通道时必须 fail-closed** —— 这段写法可以直接照抄进我们的提议审核；
3. 我们原来设计的"独立审核 Agent"（`docs/13 §7` 第 1 道闸）可以**换成人 + approval seam**：
   - 提议 → `approval.request(...)` → 用户点"允许一次" → 才进灰度；
   - 这样"提议生成方与审核方分离"不靠"两个 Agent 不共享内存"，而靠**UI 上的一次显式授权**，
     强度高得多，也不用假设 DSH 能隔离两个模型角色。

**这直接改写了 `docs/13 §7` 的第一道闸**（已更新）。

---

## 7. 对实现的额外发现（照抄级）

| 发现 | 出处 | 用途 |
|---|---|---|
| `inject = ["tools", "systemPrompt"]` + `ctx.inject([...], cb)` | novel-writer / smart-home | 可选服务用 `ctx.inject`，缺失则静默跳过（headless 兼容） |
| `ctx.effect(fn, label)` | novel-writer | 注册清理函数（卸载时还原） |
| 插件**每次调用重读 config** | smart-home | 我们的 rules 热更新可照做，不必重启 DSH |
| 插件包内 `cordis.patch.yml` + `package.json` 的 `dsh.bundle` | smart-home | 自声明补丁与打包 |
| bundle 里 `"@deepseek-ai/dsh-system-prompt"` 122 次 | app.asar | 确认这是独立核心包，不是插件私有实现 |

---

## 8. 仍需实测的三件事 —— **探针已交付**

1. **`agent/pre-step` 的实际触发时机**（是否每轮都触发、是否在检索之前）；
2. **`tool/result` 的 payload 形状**（能否区分成功/失败、是否含输出全文）；
3. **一次 `turn/end` 内能否安全地做 I/O**（会不会拖慢下一轮）。

**已交付**：`probe/dsh-lingshu-probe`（只读探针，14 项自检全绿）。

- 已装进本机 profile：junction `~/.dsh/profiles/desktop/node_modules/dsh-lingshu-probe`
  + `cordis.patch.yml` 末尾一条 `insert`（改动前已备份为 `.bak-lingshu-probe-<ts>`）；
- **需要重启 DSH** 才会加载；重启后跑一轮对话，再调 `probe_status` 或读
  `$DSH_HOME/plugins/dsh-lingshu-probe/probe.log`；
- 判定口径见 `probe/README.md`「该看什么」那张表。

**注意**：探针只证明"事件会被触发"，不证明"该事件语义上适合做 X"。
后者仍是设计判断，不因探针通过而免检。

### 8.1 第一次装就翻车：cordis 的 inject 约束（v0.1.0 → v0.1.1）

装上、重启，日志里只有两行 `probe/error`：

```
Error: cannot get property "systemPrompt" without inject
    at new apply (…/dsh-lingshu-probe/index.js:231:43)
    at Fiber.execute (…/@deepseek-ai/cordis/lib/index.js:1068:24)
```

**这本身就是一条宿主 API 事实**：cordis 对**未在 `inject` 声明**的属性访问**直接抛异常**，
不存在"读不到就返回 undefined"的温和行为。

所以"加个兜底"这种写法在这里是反模式：

```js
// ✗ 错：走到 else 分支就抛
typeof ctx.get === 'function' ? ctx.get('systemPrompt') : ctx.systemPrompt
// ✓ 对：只用 ctx.get，且整体 try/catch
try { ctx.get('systemPrompt') } catch { /* 读不到 */ }
```

**另一个更隐蔽的连锁**：`probe/apply` 那条日志的 payload 里也读了 `ctx.systemPrompt`，
于是**连第一条日志都没写出来**，只留下一个 error —— 排查时看不到任何现场信息。
教训：**日志的 payload 本身必须是无副作用的纯读取**，否则"记录失败现场"这件事会先失败。

**提示的正面结论**：抛错恰好证明插件被加载了、junction 解析对了、`insert` 生效了。
三个安装环节都是通的。

修法见 `probe/README.md#实测记录`；自检新增了"inject 受限的 ctx"回归用例（17 项全绿）。

### 8.2 第二次重启后插件消失：desktop profile 会清理未登记依赖

第二次重启后，`node_modules/dsh-lingshu-probe` 这个 junction **不见了**，
`$DSH_HOME/plugins/dsh-lingshu-probe` 目录也没了，探针未加载。

**对照证据**：同一 profile 里 `dsh-smart-home` / `dsh-kaogong` / `dsh-stock-workbench`
的 junction 都还在，而它们的共同点是——**都写进了 `package.json` 的 `dependencies`**（`link:` 协议）。
我的探针没写，于是被当 extraneous 清掉。

**结论（一条新的宿主事实）**：desktop profile 由 Electron 应用托管，
启动时做依赖对账；**插件必须登记进 `package.json`**，手建 junction 活不过一次重启。

**这对本项目的影响**：`docs/00 §4` 的"插件内自校验"依然成立，
但**安装方式**属于宿主约定，不能自己发明。装探针（或将来装 LINGSHU 本身）
必须走应用的依赖登记流程，不能手搓。

**处置**：已把 `cordis.patch.yml` 从备份**完整恢复**（12252 B，校验通过，零残留），
探针源码留在仓库里（`probe/`）+ 安装说明，等使用者决定是否正式登记。


---

## 9. 本次核实对文档的改动

| 文档 | 改动 |
|---|---|
| `docs/00 §4` | H4 证伪 → 强制点改为"插件内启动自检 + 失败即停注入"，并标注 config 维度不可靠 |
| `docs/09 §4` | 注入契约补两条：`text()` 必须返回字符串（空串=不注入）；用 `order` 实现拼装顺序 |
| `docs/13 §7` | 第一道闸改为"人 + approval seam（`allowed-once`）"，不再假设 DSH 能隔离两个模型角色 |
| `assumptions/已验证.md` | 新增"宿主能力"一节 |
| `assumptions/未验证.md` | H1/H2/H3/H5 转证实；H4 转证伪并改写；H6 转部分证实 |


### 8.3 事故：装上探针后，重启导致会话不可用（2026-10-25）

> ⚠️ **本节结论已被 §8.5 推翻。** 当时归因为"工具 `output.schema` 在 apply 之外校验失败"，
> 那是猜测；真因是 **`agent/pre-step` 是 waterfall 钩子，我们返回了 `undefined`**，宿主读
> `decision.kind` 时崩掉整轮。保留原文以记录当时的判断路径，**修法请以 §8.5 为准**。

**经过**：探针 v0.1.0 装上后重启 → 被加载、`apply()` 抛 `cannot get property "systemPrompt" without inject`；
修成 v0.1.1（自检 17 项全绿）再重启 → **无法对话**；回滚（删 junction + 还原 patch）后恢复。

**最可能的机制（假设，未复现）**：
v0.1.0 在服务探测处就抛了，`ctx.tools.register()` 没走到；
v0.1.1 才第一次真正执行工具注册 —— 而我们的 `output.schema` 用了
`type: ["boolean","null"]`、`additionalProperties: true` 这类写法。
若宿主在 apply 之外校验 schema 并失败，则 **agent 初始化失败 → 整个会话不可用**。

**这条如果成立，是对我们上一节结论的重要补充**：
`apply()` 内的 try/catch 只能保护"我们自己的代码"，
**保护不了宿主在 apply 之外做的校验**。所以"探针绝不抛异常"这条设计红线，
不足以让它安全地装进别人的日常 profile。

**处置**：
- 探针**只保留源码**，不再安装（`probe/README.md` 顶部有警告）；
- 三条硬规则写进 `probe/README.md`：不改他人 profile、实验插件只进可丢弃 profile、
  工具 schema 按宿主支持的子集写；
- `cordis.patch.yml` 已恢复为安装前状态（12252 B，零残留，结构校验通过）。


### 8.4 另一类失败：宿主 API 对"不存在的路径"直接抛 TypeError（2026-10-25）

> ⚠️ **本节归因同样被 §8.5 推翻。** 那次 `reading 'kind'` 并非 `present` 缺失文件所致，
> 而是探针插件的 `agent/pre-step` 返回 `undefined`（见 §8.5）。
> `present` 对缺失路径的行为**尚未单独验证**，不要据此下结论。

**现象**：某轮对话连续两次 `本轮运行失败  Cannot read properties of undefined (reading 'kind')`。

**经过**：该轮调用 `present` 交付三个文件，其中一个是被应用清理掉的
`$DSH_HOME/plugins/dsh-lingshu-probe/probe.log.v0.1.0-error`（已不存在）。
宿主的文件卡片逻辑读到 `undefined` 后取 `.kind` → TypeError → **整轮 run 中断**。

**两条结论**：

1. **宿主 API 的失败模式要防御**：`present` 对缺失路径不是"返回错误让调用方处理"，
   而是抛出 TypeError 打断整轮。实现 LINGSHU 时不能假设宿主 API 会温和返回。
2. **交付物必须是"此刻存在"的路径**：`present`/读取前先校验存在，
   尤其不要展示临时目录、插件数据目录、`.dsh/` 下的运行时状态——它们会被外部清理。

**与 §8.3 的区别（别读混）**：§8.3 是**装插件把应用搞坏**（应用起不来、回滚才恢复）；
§8.4 是**一次工具调用参数错**（单轮失败、后续正常）。两者原因、影响范围都不同。


### 8.5 真因：`agent/pre-step` 是 waterfall，返回 undefined 会崩掉整轮（2026-10-25 实测）

用官方 CLI 在**可丢弃的 profile 副本**里跑真实会话，把这条契约彻底钉死了。

#### 证据链

| 步骤 | 结果 |
|---|---|
| 装插件后在副本里跑一轮 | `dsh: UNKNOWN: Cannot read properties of undefined (reading 'kind')` |
| 用 `--patch` 把插件禁用后重跑 | **正常回答**（`1+1 等于 2`）→ 确认是我们引起 |
| 查 host 源码 `@deepseek-ai/dsh-agent-loop` | `dispatch.waterfall('agent/pre-step', …, () => Promise.resolve({ kind: 'enter', messages }))` |
| 官方插件写法（`archived-session-gate`） | `ctx.on('agent/pre-step', (payload, next) => cond ? Promise.resolve({kind:'reject'}) : next())` |

**契约**：`agent/pre-step` 的 handler 签名是 `(payload, next)`，
**必须返回 `next()` 或 `{ kind: ... }`**。返回 `undefined` → 宿主读 `decision.kind` → TypeError → 整轮失败。

#### 同一份源码里确认的三条附带事实

| # | 事实 | 影响 |
|---|---|---|
| 1 | 一个 turn 内 `pre-step` **触发多次**（实测 4 次，每 step 一次） | 检索/注入必须**幂等**，否则重复召回、重复计数 |
| 2 | `turn/end`、`tool/result` **不是** `ctx.on` 的 dispatch 钩子，而是**会话事件** | 用 `ctx.on('session/event', (session, event) => …)` 观测，`event = {type, seq, time, data}` 才是真的 |
| 3 | `payload` 只有 `{messages, turn, step, signal, agent}` | 取用户文本要从 `messages` 数组里找 `role==='user'` 的 `content[{type:'text',text}]`；没有 `message`/`prompt` 字段 |

#### 两个工程陷阱（都让我们静默失效过）

- **BOM**：PowerShell 5.1 的 `Set-Content -Encoding UTF8` 会写 BOM；
  `JSON.parse` 遇到 BOM 直接抛错，被 `try/catch` 吞掉 → **整个记忆库读成空数组**，
  表现为"召回永远 0 命中"。修法：读时 `replace(/^\uFEFF/, '')` + 写时用无 BOM 编码。
- **`session/event` 的第二个参数才是事件**：第一个参数是 Session 对象（有 `header`/`log` 等字段），
  只看第一个参数会以为"钩子没触发"（我第一版就误判成"`session/event` 不触发"）。

#### 修正后的结论

事故 A/B（会话不可用、单轮 `reading 'kind'`）**是同一个原因**：waterfall 钩子返回 `undefined`。
不是工具 schema，也不是 `present` 缺失文件。这条修正已同步到 `CHANGELOG v3.17`。


### 8.6 工具契约：`output.render` 必须返回 content 块数组（2026-10-25 实测）

**现象**：`lingshu_status` 一调就报 `content.some is not a function`，
换严格 schema、换基本类型返回值、换字符串返回值，**全都不管用**。

**真因**：这个宿主版本把 `output.render(...)` 的返回值**直接当成 content 块数组**使用
（PTC 路径上有 `result.content.some(block => block.type === 'image')`）。
返回字符串 → `content` 是字符串 → `.some` 崩。

```js
// ✗ 错：返回字符串
render(args, value) { return String(value) }

// ✓ 对：返回 content 块数组
render(args, value) { return [{ type: 'text', text: String(value) }] }
```

**验证**：改完后真机一轮，模型成功复述出 `阶段: cold（累计 1 轮）`。

**排查成本记录**：这个问题花掉了 4 轮真机 + 3 次源码扫描。
中间我一度怀疑是 schema 的联合类型、裸 array、`additionalProperties`，
**全都不是**。教训：**当"改类型也不管用"时，说明错在契约形状而不是值类型**，
应该先去看调用点的消费代码（`.some(` 这样的用法），而不是继续改自己的 schema。

> 同源提醒：`probe/` 里的探针 `render` 也是返回字符串，
> 如果哪天要恢复它，这条 bug 会让 `probe_status` 以完全相同的方式失败。
