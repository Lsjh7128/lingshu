# 迁移指南：沙盒命名空间 + 作用域记忆（layout-0.2）

> 适用：LINGSHU v0.2 布局重构（施工单 v2）。分支 `feat/refactor-sandbox-namespace`。
> 一句话：**旧数据只读保留，新布局复制生成，`layout.json` 是唯一开关；删掉它即回到旧布局。**

---

## 0. 先弄懂三个开关

| 开关 | 作用 | 回滚方式 |
|---|---|---|
| 数据根下 `layout.json`（`schema_version:"layout-0.2"`） | 存在 → 插件按 v0.2 双布局读/写；不存在 → 完全沿用旧平铺布局（逐字节不变） | 删除该文件（旧数据从未被改动） |
| `LINGSHU_DATA_DIR` | 指定数据根（替代 `$DSH_HOME/plugins/dsh-lingshu`），用于实验/隔离 | 取消环境变量 |
| `LINGSHU_SANDBOX_ROOT` / config `workspaceRoot` | 决定**当前** sandboxId = `sha256(canonicalPath)[0:8]` | 改回原来的项目根 |

> 设计红线：v0.2 是**加法**。没有 `layout.json` 时，插件连一行旧逻辑都不会变；这就是 915/0 基线与 JS sims 保持全绿的原因。

---

## 1. 迁移前

```powershell
# 1) 停掉正在使用插件的 DSH 会话（避免迁移期间追加写入）
# 2) 记下当前分支与 HEAD，便于回滚
cd D:\LSJH\lingshu
git rev-parse HEAD
git status --short          # 应为空

# 3) 确认真实数据根（默认位置）
$data = "$env:USERPROFILE\.dsh\plugins\dsh-lingshu"
Get-ChildItem $data
```

迁移脚本**只读取**数据根，**只复制**，不移动、不改写旧文件。

---

## 2. dry-run（默认，不写任何文件）

```powershell
.\.venv-neural\Scripts\python.exe tools\migrate_layout_v02.py `
  --data-root "$env:USERPROFILE\.dsh\plugins\dsh-lingshu" `
  --workspace-root "D:\QQ鲸鱼" `
  --persona butler `
  --json
```

输出包含：识别到的旧布局（`legacy` / `workspace_<id>` / `profiles/<tag>`）、每个源文件的 sha256 与行数、
目标 `sandboxId`（8 位 hex）与目标路径计划。**这一步 exit 0 且没有创建任何目录**才算通过。

---

## 3. 正式迁移（先备份，后写入）

```powershell
.\.venv-neural\Scripts\python.exe tools\migrate_layout_v02.py `
  --data-root "$env:USERPROFILE\.dsh\plugins\dsh-lingshu" `
  --workspace-root "D:\QQ鲸鱼" `
  --persona butler `
  --backup-dir "D:\.lingshu-backups\dsh-lingshu_20261029" `
  --apply
```

脚本会按顺序做：

1. 整份复制数据根 → `--backup-dir`（复制不移动），逐文件 sha256 校验；
2. 旧记忆 → `sandboxes/{sid}/{pid}/local_memory.jsonl`（每行补 `sandbox_id` / `persona_id` / `scope:"local"` / `display_text`）；
3. 各代审计 → `audit/audit.jsonl`（单本，每行补 `sandbox_id` / `persona_id`）；
4. `state.json` → 拆成 `personas/{pid}/persona_state.json` + `sandboxes/{sid}/{pid}/state.json`；
5. `workspace_<id>/persona.json` → `personas/{pid}/persona.json`；
6. 写 `sandboxes/index.json`、`sandboxes/{sid}/sandbox.json`；
7. 最后写 `layout.json`（`migrated_from` 记录来源）；
8. 写 `kind:"migration_apply"`、`kind:"sandbox_mapped"` 审计。

**幂等**：`layout.json` 已存在 → exit 3，拒绝二次迁移；任何校验失败 → exit 4，回滚本次新建文件。

退出码：`0` 成功 / `2` 用法错误 / `3` 已迁移 / `4` 校验失败已回滚。

---

## 4. 迁移后校验

```powershell
$data = "$env:USERPROFILE\.dsh\plugins\dsh-lingshu"
Get-Content "$data\layout.json"                       # schema_version = layout-0.2
Get-Content "$data\sandboxes\index.json"              # sid → canonical_path
Get-ChildItem "$data\sandboxes" -Recurse -File | Select-Object FullName,Length
(Get-Content "$data\audit\audit.jsonl" | Measure-Object -Line).Lines   # 行数应 ≥ 旧 audit 行数
```

核对三件事：

1. 新 `local_memory.jsonl` 行数 == 旧 `memory.jsonl` 行数（`profiles/` 多份时按来源分别核对）；
2. `audit/audit.jsonl` 每行都有 `sandbox_id` 与 `persona_id`；
3. 旧 `state.json` / `memory.jsonl` / `audit.jsonl` **仍在原位且字节未变**（`Get-FileHash` 对比备份清单）。

---

## 5. 重装 / 切换运行

1. 把 `plugin/dsh-lingshu/` 整个目录拷到宿主插件目录（或按既有安装方式重装）；
2. 若使用隔离实验根（推荐先这样验证一次）：
   ```powershell
   $env:LINGSHU_DATA_DIR = "D:\tmp\lingshu-lab-v02"
   $env:LINGSHU_WORKSPACE = ""      # 不再用旧 workspaceId 语义
   ```
   把迁移后的数据根内容复制到该目录（或直接从该目录重跑迁移脚本的 apply）；
3. 启动 DSH。首条消息应看到面板/审计出现：
   - `audit/audit.jsonl` 新行带 `sandbox_id`/`persona_id`；
   - 记忆面板页面：`/dsh-lingshu/dashboard/memory`（只读 + 单条移到回收站 + 回收站；数据来自
     `GET /dsh-lingshu/api/memory?q=`，写操作 `POST /api/memory/forget|/trash/restore|/trash/purge`）；
   - 面板顶部显示当前 `sandbox_id` 与 `persona_id`，与 `sandboxes/index.json` 对应。

### profileTag 的变化

旧的路径级 `profiles/<tag>/` 在 v0.2 不再参与寻址；tag 被迁移进 `sandbox.json.profile_tag`（仅溯源用）。
需要多套隔离环境时，用**独立的 `LINGSHU_DATA_DIR`**，不要再用 profile 目录。

---

## 6. 回滚（三选一，从轻到重）

**R1 只回滚布局开关（最常用）**

```powershell
Remove-Item "$env:USERPROFILE\.dsh\plugins\dsh-lingshu\layout.json"
```

删掉后插件回到旧平铺布局；旧 `state.json` / `memory.jsonl` / `audit.jsonl` 从未被改写，行为与迁移前一致。
迁移期间新写入的记忆（在 `sandboxes/` 下）不会被旧布局看到——它们仍在，重新放回 `layout.json` 即可找回。

**R2 还原整份数据（数据被误改时）**

```powershell
Copy-Item "D:\.lingshu-backups\dsh-lingshu_20261029\*" `
  "$env:USERPROFILE\.dsh\plugins\dsh-lingshu" -Recurse -Force
```

备份目录里有 `_manifest.sha256`，可逐文件校验。

**R3 回退代码**

```powershell
cd D:\LSJH\lingshu
git switch feat/phase-c-semantic-guard     # 或迁移前的 HEAD
```

代码回退后即使 `layout.json` 还在，旧代码也不认识它（忽略该文件），行为等价 R1。

> 旧文件保留口径：迁移后旧平铺文件**只读保留 ≥7 天**，确认无回滚需求后才允许人工清理；脚本不会自动删除任何旧文件。

---

## 7. Skeleton 是派生数据（S6 验收项）

`sandboxes/{sid}/{pid}/skeleton.md` **本期只占位、不生成**，且定性为派生数据：

- 可随时删除并重建，**不进备份关键路径**；
- 不参与哈希校验（冻结区/审计链都不读它）；
- **不得作为写入源**：任何记忆/人格数据都不能只存在于 skeleton.md；
- 本期只登记注入段 `lingshu:skeleton`（order 105，空段落，不改变拼装输出）与 3 个预留审计 kind。

---

## 8. 常见问题

| 现象 | 原因 | 处理 |
|---|---|---|
| apply exit 3 `layout_exists` | 已经迁移过 | 不要强删 layout.json 重跑；先 R1 回滚布局再决定 |
| 面板 `503 layout_not_v02` | 数据根没有 `layout.json` | 正常：v0.2 面板只在迁移后可用 |
| 迁移后读不到旧记忆 | `--workspace-root` 与旧数据不是同一个项目根 → 换了 sandboxId | 用迁移输出里的 canonical_path 核对，必要时按 R2 重来 |
| 旧 `profiles/<tag>` 里的数据在 v0.2 看不到 | profile 层已废弃 | 迁移脚本已把它收编进 `sandboxes/<sid>/<pid>/`；确认 `migrated_from` 含 `profiles/<tag>` |
| 想彻底删除一条记忆 | 面板「彻底删除」= redact（抹内容留骨架） | 需要"磁盘无痕"必须单独审批，本期不做 |
| `--apply` 报 `备份复制失败: [WinError 5]` | DSH 下 Python 子进程只能写会话工作区（如 `D:\LSJH`），不能写 `D:\.lingshu-backups` 或数据根 | 先把数据根整份复制到工作区快照（如 `.tmp/v02stage`），在**快照**上 `--apply`；再用 PowerShell 把 `layout.json` / `audit/` / `personas/` / `sandboxes/` 拷进真实数据根；备份同理先落工作区，再拷到 `D:\.lingshu-backups`。旧 `workspace_qq` 保持不动 |

---

## 9. 与施工单字面口径的两处偏差（已裁定，记录在案）

1. **迁移行的 `sandbox_id`**：`legacy` 来源填 `"legacy"`（施工单 §7 原文）；`workspace_<id>` /
   `profiles/<tag>` 来源填**目标 sandboxId**，来源标签落在 `migrated_from` 与审计 `source` 里。
   原因：`merge_layers()` 只对 `(当前 sid, "legacy")` 放行；若填 `workspace_code`，迁移后的 local
   记忆会全部被判"行内标签不符"而检索不到。来源可追溯性没有丢。
2. **嵌套 profile 的 `migrated_from`**：记为 `workspace_code/profiles/dev`（保留 workspace 层信息），
   而不是压缩成 `profiles/dev`。

另外两点实现口径（非偏差，但读代码时需要知道）：

- **S3 的 skeleton 段只在 v0.2 布局下注册**（`layout.json` 存在）。legacy 布局保持原"7 段契约"
  逐字节不变（host-sim/stress/lag-regressions 的 7 段断言全绿）；v0.2 下是 8 段，其中
  `lingshu:skeleton` 恒为空串，拼装输出不变。
- **state 拆分映射**：`control.integral → persona_state.integral_baseline`、`dev → emotion_baseline`、
  `relation → relations`；`goal/tools/计数器 → sandboxes/<sid>/<pid>/state.json`。
  `sandbox_integral` 旧数据无对应值，置零（不臆造）。

