# PROVENANCE · dsh-lingshu

本文件说明该插件包的来源与打包口径。任何声称「本包内容」的说法以本文件 + `package.json#files` 为准。

## 来源

- 上游仓库：`https://github.com/Lsjh7128/lingshu`
- 包内路径：`plugin/dsh-lingshu/`（仓库内的插件子目录，非独立仓库）
- 包版本：`0.2.0`（`package.json#version`，「包版本轴」；与仓库发布版本 `v1.1.x` 不同轴、不可换算）
- 许可证：MIT（与仓库根 `LICENSE` 一致）
- 维护者：LINGSHU Project Team

精确提交见仓库 `git log -- plugin/dsh-lingshu`；本文件随包分发，**不绑定某一提交哈希**，以免误引。

## 打包口径

发布内容 = `package.json#files` 白名单，其余物理文件一律**不随包**：

| 随包 | 说明 |
|---|---|
| `index.js` `control.js` `tool-gateway.js` `goalrelation.js` `memorywriter.js` `memory-paths.js` `workspace*.js` `supply-chain.js` `dashboard.js` `client.js` | 运行时 |
| `sandbox-host.mjs` `sandbox-child.mjs` `workspace-admin.mjs` | 运行时 CLI / 子进程 |
| `ui/` `workspaces/` | 控制面板静态页与工作区策略目录 |
| `cordis.patch.yml` | bundle patch，被 `dsh.bundle.patch` 引用 |

| 不随包 | 理由 |
|---|---|
| `*-sim.mjs`、`*-regressions.mjs`（host-sim / control-sim / stress / workspace-* 等） | 验收脚本与 mock 宿主，只服务仓库内 CI，**不属于运行时** |
| `node_modules/` | 由宿主解析，无需随包 |

`tools/check_plugin_pack.py` 做双向校验：`files` 里的路径必须存在；运行时 `.js` 必须列进 `files`；
**测试脚本必须存在于仓库但不得进包**；`dsh.bundle.patch` 指向的文件必须存在且已列出。

## 脱敏

- 包内**不含**任何本机绝对路径（`workspaceRoot` 默认留空，由集成方或 `LINGSHU_SANDBOX_ROOT` 提供）。
- 包内**不含**真实运行数据（`state.json` / `memory.jsonl` / `audit.jsonl` / `last-prompt.txt` 均不入库、不入包）。
- 包内**不含**密钥；测试夹具里的示例值（如 `sk-…` / `AKIA…`）是**公开构造值**，用于断言，不是真实凭证。

## 验证

```powershell
<PYTHON> tools/check_plugin_pack.py     # 打包清单完整性
<PYTHON> tools/check_config_sync.py     # cordis.patch.yml#control 与 rules/decay-params.yaml 同源
<PYTHON> tools/check_patch_yaml.py      # bundle patch 语法
node host-sim.mjs --assert              # 集成契约（mock 宿主）
```

## 已知偏差

- 本包在**可丢弃的实验 profile** 上验证过真实会话，**未**进入日常使用；长会话/多工具只压到 40 轮。
- 目标/关系、事实提取、越狱分级均为**规则桩**，误报/漏报未标定。
- 控制论层数值在插件内有构建期副本（`cordis.patch.yml#control` 与 `control.js#DEFAULT_PARAMS`），
  由 `check_config_sync.py` 逐键比对，非运行时共享常量。
