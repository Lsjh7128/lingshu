# dsh-lingshu

> LINGSHU 认知插件 —— 给 DeepSeek Harness 装一层「记忆 / 元认知 / 情感 / 目标 / 关系」，
> 外加一层控制论（相变锚点、积分基线、U 型注入、STPA 前置闸）。
> A cognitive-layer plugin for DeepSeek Harness. 0 model calls for its rules: everything here is deterministic code.

**版本**：`0.2.0`（包版本轴，独立于仓库发布版本 `v1.1.x`）
**状态**：已在**可丢弃的实验 profile**（`lingshu-lab-headless`）跑通真实会话（含工具调用）；
**日常 `desktop` profile 零改动**，本插件不向其写入任何内容。

---

## 安装

插件通过 `package.json` 的 `dsh.bundle.patch`（见 `cordis.patch.yml`）把自己挂进 profile 的 loader tree，
因此**不需要手改 profile 的补丁**。用应用自带的 CLI 安装：

```powershell
<DSH_BIN> plugin --profile <可丢弃的profile> add "link:<本包路径>"
```

安装后必须**重启 DSH**（不是刷新页面）：`dsh.client` 的扫描与宿主路由都只在启动时生效。

> ⚠️ 不要把它装进日常 `desktop` profile。默认 `mode: active` 会注入 7 段 prompt；
> 只想观测就用 `mode: observe`（零注入、只记录）。

### 给其他用户安装（DSH 图形界面「添加插件」）

DSH 的「添加插件」对话框接受**包名、Git 地址/压缩包、本地绝对路径**三种输入。
本仓库是 monorepo，**直接输入仓库根地址一定会失败**（根目录没有 `package.json`），
请按下面三种方式之一填。

| 方式 | 输入 | 适用 |
|---|---|---|
| 包名（最省事） | `dsh-lingshu` | 包已发布到 npm 之后；安装源选「中国大陆镜像源」即可走 npmmirror |
| GitHub 子目录 | `github:Lsjh7128/lingshu#path:/plugin/dsh-lingshu`（取默认分支；改动还在别的分支时写 `#分支名&path:/plugin/dsh-lingshu`） | 未发布 npm、但能访问 GitHub 时 |
| 本地目录 / 压缩包 | `D:\path\to\plugin\dsh-lingshu` 或 `dsh-lingshu-0.2.0.tgz` | 离线、内网、不想联网时 |

三种方式安装后都要再做两步，否则插件不生效：

1. 在侧边栏「插件」页打开 `dsh-lingshu` 的开关（图形界面安装默认**不启用**）；
2. **重启 DSH**（不是刷新页面）：`dsh.client` 的扫描与宿主路由只在启动时生效。

补充说明：

- 图形界面安装依赖 `package.json` 的 `dsh.bundle.patch` 声明；缺了它安装器会
  把它当普通依赖装进去但不启用（本包已声明，见 `cordis.patch.yml`）。
- 目录方式装的是 `link:`（源码改动即时生效）；压缩包与 Git 方式装的是复制，
  升级要重新装。
- 发布前在仓库根跑 `node scripts/check_plugin_package.mjs` 自检，确认运行时文件
  都在 `files` 清单里、`supply-chain-policy.json` 与 `contracts/` 一致；否则装完
  才会以「缺文件 / 供应链闸门降级」的形式暴露。
- 维护者发布：`cd plugin/dsh-lingshu && npm publish --dry-run` 先核对内容，
  再去掉 `--dry-run`；发布后 npmmirror 自动同步（通常几分钟）。

## 配置（`cordis.patch.yml` → `config`）

| 键 | 默认 | 说明 |
|---|---|---|
| `mode` | `active` | `active` 注入 / `observe` 只记录不注入 |
| `profileTag` | `''` | 数据隔离到 `$DSH_HOME/plugins/dsh-lingshu/profiles/<tag>/` |
| `coldTurns` / `warmTurns` | `3` / `20` | 冷启动：前 3 轮不注入记忆，4–20 轮只用高置信召回 |
| `warmConfidenceFloor` | `0.7` | 暖启动召回置信度下限 |
| `topK` / `tokenBudgetTokens` | `3` / `900` | 每轮召回条数与注入预算 |
| `archiveThreshold` | `0.15` | 归档门：`potency` 低于此值不召回 |
| `workspaceRoot` | `''` | 工具网关判定「路径逃逸」的根；留空回落 `process.cwd()`，也可用 `LINGSHU_SANDBOX_ROOT` 覆盖 |
| `workspaceId` | `''` | 人格体工作区隔离 ID（`[A-Za-z0-9_-]{1,32}`），留空=历史平铺目录 |
| `contextWindowTokens` / `charsPerToken` | `1048576` / `3` | 相变估算口径（**估算，不是 tokenizer**） |
| `integralApply` | `false` | `false` = 积分只观测，不改表达 |
| `shadowGoal` / `shadowRelation` | `false` | 影子模式：只记录不注入 |
| `immersiveEnabled` | `true` | 沉浸式安全：soft 降级为「人设内拒绝」而非人格消失 |
| `permissionExplainEnabled` | `true` | 可解释权限请求（仅表达层，不制造审批通道） |
| `control.*` | 见 `cordis.patch.yml` | 相变阈值 `0.70/0.85`、积分限幅与泄漏、soft buffer 强度/升级门槛 |

## 数据落在哪

`$DSH_HOME/plugins/dsh-lingshu/`（或 `<profileTag>` 子目录）：

- `state.json` — 轮次、阶段、目标、关系、控制论状态
- `memory.jsonl` — 记忆库（**唯一写入口是 `memorywriter.js`**，不直接追加）
- `audit.jsonl` — 审计日志（`apply` / `pre_step` / `memory_write` / `preflight_block` …）

真实运行数据**不入库**，`.gitignore` 已排除；清理脏记忆只能走 `node memory-admin.mjs`（同 id 墓碑）。

## 能做什么

- **注入**：`ctx.systemPrompt.section()` × 7 段（order 90/100/105/110/120/130/140）
- **召回**：JSONL + 中文 bigram/英文词 + IDF 加权 + 归档门
- **元认知**：纯规则三档 `unknown / mid / high` → `ask_clarify / hedge / state`
- **情感**：PAD 三维 + 指数衰减，来源是用户语气与工具成败，**只进风格段**
- **控制论**：积分基线（跨进程）、相变双阈值 + 趋势、首尾锚点、U 型注入、STPA 前置闸（fail-closed）
- **事实记忆**：三类中文句式提取 → 校验落盘 → 同键 supersede（墓碑 + last-wins）
- **冲突降维**：召回后冲突判定 → 只丢 `style` 域，fact 段加 `〖冲突待澄清〗`，fail-open 不熔断
- **沉浸式安全**：`none / mild / severe` 纯规则分级；soft 时仍 `blocked=true`，只改表达
- **人格体中枢**：侧栏 `order: 11` 面板 + `/dsh-lingshu/dashboard` 同源 iframe + `/dsh-lingshu/api/status`

## 不能做什么（诚实边界）

1. **没有 pre-dispatch 钩子**：DSH 只暴露 `tool/call` / `tool/result` 会话事件，工具闸的判定**发生在调用之后**，
   是事后检测与可解释说明，**不是**「已暂停执行」。
2. **拦不住用户消息**：插件只能控制「注入什么」，无法把用户消息从宿主 payload 里移除。
3. **规则桩**：事实提取、目标/关系、越狱分级都是关键词/句式规则，**误报漏报未标定**。
4. **不是多租户**：`workspaces/<id>` 是**目录隔离**，不是权限隔离。
5. **方向性数据未实测**：所有参数 `provenance: guess`，`measured` 数量为 0；控制论在真实长上下文里的收益未验证。

## 验收（mock 宿主，不碰真实 profile）

```powershell
node host-sim.mjs --assert      # 集成契约
node control-sim.mjs --assert   # 控制论层纯函数
node stress.mjs --assert        # 40 轮长会话推移
```

测试脚本**不进发布包**（不在 `files` 白名单），但必须留在仓库里——`tools/check_plugin_pack.py` 会双向校验这件事。

## 回滚

```powershell
<DSH_BIN> plugin --profile <profile> remove dsh-lingshu
Remove-Item -Recurse -Force "$env:DSH_HOME\plugins\dsh-lingshu"
```

## 许可证

MIT。完整发布口径见仓库 `docs/21-开源发布清单.md`、`plugin/README.md`。
