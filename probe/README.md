# dsh-lingshu-probe · 宿主能力探针

> ## ⛔ 不要装进你的 desktop profile
>
> **2026-10-25 事故记录**：装进本机 `desktop` profile 后，重启导致**会话无法对话**，
> 回滚（删 junction + 还原 patch）才恢复。
> 结论：**这个探针只作为源码保留，不再安装**。原因与三条硬规则见
> `research/02-DSH宿主能力核实.md §8.3`。
>
> 若将来确实要测钩子时序：用**应用自带的插件管理入口**装，或在一份**可丢弃的 profile 副本**里做，
> 不要由 agent 手改 `package.json` / `node_modules` / `cordis.patch.yml`。

> 只读、只记日志、**不改变宿主任何行为**。用来回答三个"读源码读不出来"的问题：
> 1. `agent/pre-step` 什么时候触发？每轮都触发吗？在工具调用之前还是之后？
> 2. `tool/result` 的 payload 长什么样？能区分成功/失败吗？含输出全文吗？
> 3. `turn/end` 里做 I/O 会不会拖慢下一轮？

对应 `research/02-DSH宿主能力核实.md §8` 的三个待测项。

---

## 它做什么

| 行为 | 说明 |
|---|---|
| 记录 `probe/apply` | 加载时刻、pid、cwd、`DSH_HOME`、`ctx` 上有哪些能力 |
| 订阅事件 | 默认 `turn/start`、`agent/pre-step`、`tool/result`、`turn/end`、`session/start`、`session/end`、`prompt/configure`、`message/created` |
| 每条事件 | 时间戳、序号、距上次同类事件的 `deltaMs`、payload **结构摘要** |
| 探测服务 | `systemPrompt` / `approval` 是否存在 |
| 注册一个工具 | `probe_status`：看汇总、看最后 N 条、清空日志 |

**隐私**：payload 只记"键名 + 类型 + 长度 + 字符串的短哈希"，**不记原文**。
想临时多测几个事件名，设环境变量 `LINGSHU_PROBE_EVENTS=ev1,ev2` 后重启。

日志位置：`$DSH_HOME/plugins/dsh-lingshu-probe/probe.log`（JSONL，超 1 MB 自动保留最后 400 行）。

---

## 安装（**注意：desktop profile 有前置条件**）

### 实测教训（2026-10-25）

第一次安装用的是"手建 junction + patch 加 insert"，结果：

| 现象 | 结果 |
|---|---|
| 第一次重启 | 插件**加载成功**（日志里有 apply 的报错 → 说明确实跑到了我们的代码） |
| 第二次重启 | junction **消失**、插件未加载、`$DSH_HOME/plugins/dsh-lingshu-probe` 目录也没了 |

**根因**：desktop profile 由 Electron 应用托管，启动时会做依赖对账。
**没写进 `package.json` 的包会被当作 extraneous 清掉**，手建的 junction 第一个阵亡。

所以下面这个"三分钟"版本**只适用于 web profile 或自管 profile**；
desktop profile 必须走依赖登记。

### desktop profile 的正确装法

```powershell
# 1) package.json 的 dependencies 里加一行（注意 link: 协议）
#    "dsh-lingshu-probe": "link:<REPO_ROOT>/probe/dsh-lingshu-probe"

# 2) 让应用（或它自带的 pnpm）去创建链接 —— 不要手建 junction，会被清理
#    桌面端没有 CLI 安装入口（`dsh plugin --profile desktop` 会被拒），
#    实际做法参照 dsh-smart-home：手改 package.json 后由应用拉起 pnpm install

# 3) cordis.patch.yml 末尾追加 insert
#    - insert:
#        - id: dsh-lingshu-probe
#          name: dsh-lingshu-probe

# 4) 重启 DSH
```

**风险提示**：改 `package.json` 会触发 pnpm 对账，可能影响同 profile 的其它插件。
所以这一步应当**由使用者决定**，不要由 agent 单方面执行。

## 安装（三分钟，适用于自管 profile）

```powershell
# 1) 建 junction（让 DSH 的 node_modules 能找到它）
cmd /c mklink /J "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\dsh-lingshu-probe" "<REPO_ROOT>\probe\dsh-lingshu-probe"

# 2) 在 profile 补丁里登记（追加到 cordis.patch.yml 末尾）
#    - insert:
#        - id: dsh-lingshu-probe
#          name: dsh-lingshu-probe

# 3) 重启 DSH
```

**备份**：改 `cordis.patch.yml` 之前先复制一份
`cordis.patch.yml.bak-lingshu-probe-<时间戳>`。这个文件里已经有几个 `.corrupt` / `.bak` 历史备份，
说明它被改坏过——所以每次改都必须留底。

## 卸载

```powershell
# 1) 删掉 cordis.patch.yml 里那条 insert
# 2) 删掉 junction
cmd /c rmdir "$env:USERPROFILE\.dsh\profiles\desktop\node_modules\dsh-lingshu-probe"
# 3) 删日志（可选）
Remove-Item -Recurse "$env:USERPROFILE\.dsh\plugins\dsh-lingshu-probe"
```

---

## 怎么读结果

重启后随便跑一轮对话，然后：

- **工具**：调 `probe_status`（`tail: 50`）
- **或直接看日志**：

```powershell
Get-Content "$env:USERPROFILE\.dsh\plugins\dsh-lingshu-probe\probe.log" -Tail 40
```

### 该看什么

| 现象 | 结论 |
|---|---|
| `probe/apply` 里 `hasOn: true` 且多条事件 `ok` | `ctx.on` 可用，事件名正确 |
| 某事件 `subscribed` 为 `err: ...` | 该事件名不存在 → 从订阅列表删掉 |
| 一轮对话里 `turn/start` 出现 1 次、`turn/end` 1 次 | 钩子按轮触发，可作为落库点 |
| `agent/pre-step` 出现在 `tool/result` **之前** | 适合放检索注入 |
| `agent/pre-step` 一轮出现 **多次** | 注意：注入会被执行多遍，需要幂等 |
| `tool/result` 的 payload 里有 `ok`/`error` 字段 | 可用于区分工具成功/失败 |
| `probe/service` 里 `systemPrompt: true` | H1 在本机也成立（不只插件作者的说法） |

### 时间口径

`deltaMs` 是**同一事件**距上次触发的间隔，不是跨事件间隔。
要看跨事件顺序，看 `seq` 与 `ts` 两列——`seq` 是单调递增的到达顺序。

---

## 已知边界

- 只能测**本机这一版** DSH（`app.asar` 182 MB 那版）的行为；
- 事件名若写错，表现为 `subscribed: "err: ..."`，**不会**让宿主启动失败；
- 探针自身不产生任何注入，因此**不影响** prompt 与模型输出；
- 它证明的是"事件会被触发"，不证明"该事件在语义上适合做 X"——后者仍要靠设计判断。

---

## 实测记录

### v0.1.0：加载成功，但立刻抛错（已修）

第一次装进本机 profile 后，日志只有两行 `probe/error`：

```
Error: cannot get property "systemPrompt" without inject
    at new apply (…/probe/dsh-lingshu-probe/index.js:231)
    at Fiber.execute (…/@deepseek-ai/cordis/lib/index.js:1068)
```

**根因**：cordis 对**未在 `inject` 声明**的属性访问会直接抛异常。
旧代码写的"兜底"是

```js
typeof ctx.get === 'function' ? ctx.get('systemPrompt') : ctx.systemPrompt
```

—— 一旦走到 `else` 分支就抛；更糟的是 `probe/apply` 那条日志的 payload 里也读了
`ctx.systemPrompt`，于是**连第一条日志都没写出来**，只留下一个 error。

**修法（v0.1.1）**：

- 只走 `ctx.get(name)`，整体包在 try/catch 里（`safeGet`）；
- 读不到记 `null`（unknown），**不记 `false`** —— `false` 会被误读成"服务不存在"；
- 自检新增回归用例：**inject 受限的 ctx 下 apply 仍不抛异常**（17 项全绿）。

**顺带确认的好消息**：抛错本身证明了三件事 ——
插件被宿主**加载**了、junction **解析**对了、`cordis.patch.yml` 的 `insert` **生效**了。
只是 apply 的第一行逻辑写错。

---

## 事故记录（2026-10-25）：装上去会让人无法对话

### 发生了什么

| 步骤 | 结果 |
|---|---|
| 手建 junction + patch 加 `insert`，重启 | 插件被加载，`apply()` 抛 `cannot get property "systemPrompt" without inject`（已修） |
| 修成 v0.1.1（`safeGet` + 17 项自检全绿），再重启 | **会话无法对话**；回滚后恢复 |
| 回滚（删 junction、还原 patch） | 正常 |

### 最可能的机制（无法复现，故标为假设）

v0.1.0 在**读完服务后**就抛了，`ctx.tools.register(...)` 根本没执行；
v0.1.1 修掉那个异常后，**第一次真正执行到工具注册**——然后就坏了。

所以最可能的原因是：**工具注册的 `output.schema` 不合法**（我们用了
`type: ["boolean","null"]`、`additionalProperties: true` 这类写法），
宿主在 apply 之外的阶段校验失败 → **agent 初始化失败** → 整个会话不可用。

这条如果是真的，含义很重：**`apply()` 里包 try/catch 救不了宿主在 apply 之外做的校验**。
（也可能是 profile 对账把未登记包判为不一致而拒绝启动；两种都无法在不动他 profile 的前提下证伪。）

### 三条硬规则（从此不再违反）

1. **不得由 agent 单方面改动他人 profile**：`package.json` / `node_modules` / `cordis.patch.yml`
   都是应用托管文件，装插件必须走应用自己的入口。
2. **实验插件不得装进日常使用的 profile**：要测就在可丢弃的 profile 副本里测。
3. **工具 schema 必须按宿主支持的子集写**：联合类型（`["boolean","null"]`）这类先确认再用，
   否则可能不是"注册失败"，而是"agent 起不来"。
