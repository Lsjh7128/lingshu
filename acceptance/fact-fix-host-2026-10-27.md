# 真实宿主验证：F-A / F-B / F-D 修复（2026-10-27）

> 由 `<REPO_ROOT>\scripts\verify_fact_fix_host.py` 自动生成；命令、原始 stdout/stderr、
> audit/memory/last-prompt 原文见 `acceptance/fact-fix-host-2026-10-27-raw/`。本文件不写生成时刻，可复核。

## 结论

- 断言：**PASS 22 / FAIL 0 / 共 22**
- F-A（纠正识别 + supersede）：PASS
- F-B（提问不写记忆 + object 合法）：PASS
- F-D（首回合注入无滞后 + anchor_lag 审计）：PASS

## 关键字段摘录（原文见 raw/）

| 证据 | 实测 |
|---|---|
| F-A 账本（`conv-memory.jsonl`） | `mem_001` object=阿尔法 `status=superseded`、`supersededBy=mem_002`；`mem_002` object=贝塔 `status=active` |
| F-A 审计（`conv-audit.jsonl`） | `{"kind":"memory_supersede","subject":"项目代号","relation":"是","superseded":[{"id":"mem_001","object":"阿尔法","byId":"mem_002"}]}` |
| F-B 审计 | `{"kind":"fact_extract_rejected","reason":"interrogative","quoteHead":"我的项目代号是什么？请只","turn":3}`；memory 中无 object=什么 的条目 |
| F-B/F-D 回答（A3 stdout） | `贝塔`（不含 阿尔法） |
| F-D 真实宿主 pre_step | 3 个进程的 firstStep 行均 `anchor_lag=false`（旧版此字段不存在/只能为 true） |
| F-D critical 首轮（`anchor-last-prompt.txt`） | `phase=critical, ratio=1.1595, anchor=true`；last-prompt **首部**含 `〖人格锚点〗`、**尾部**含 `〖人格锚点·复核〗`（旧版首轮请求里没有） |

## 混沌 host S2 复跑（同一修复的真实宿主旁证）

- run：`acceptance/chaos/chaos-20261027-204832-host.json`（`python tools/run_chaos.py --mode host`，6 次模型调用，0 异常）
- S2 `memory_conflict`：`beta_hit=true`、`alpha_leak=false`、`recall_ids=["mem_002"]`、`supersede_events=1`、`statuses={mem_001: superseded, mem_002: active}`、第 3 次回答 `贝塔`
- 对照：修复前同一断言读到的 02:00 host JSON 为 `beta_hit=false`（F-D 滞后导致模型答旧值），现已被新 run 取代。

## 逐条断言

- `ok` 插件 junction 存在（宿主解析包名用）
- `ok` 宿主 CLI bin.js 存在
- `ok` 插件目录存在 index.js
- `ok` A1 进程 exit=0
- `ok` A2 进程 exit=0
- `ok` A3 进程 exit=0
- `ok` A1 之后 memory.jsonl 有 1 条事实（阿尔法）
- `ok` F-A 纠正落盘：存在 object=贝塔 的 active 条目
- `ok` F-A supersede：旧条目 object=阿尔法 已是 superseded
- `ok` F-A supersede：墓碑 supersededBy 指向新条目 id
- `ok` F-B 提问未污染记忆：没有任何 object 是疑问词的条目
- `ok` 审计有 memory_supersede 事件
- `ok` F-B 提问被提取器拒绝并留痕（reason 含 interrogative）
- `ok` F-D 回答命中新值：A3 回答含 贝塔
- `ok` F-D 回答不泄漏旧值：A3 回答不含 阿尔法
- `ok` pre_step 首 step 审计行都带 anchor_lag 字段
- `ok` 真实宿主：首 step anchor_lag=false（预计算生效，本回合无滞后）
- `ok` B1 进程 exit=0
- `ok` B1 相变触发：firstStep ratio>=0.85 且 anchor=true
- `ok` B1 首轮 last-prompt.txt 已含首部锚点『〖人格锚点〗』（旧版这里没有）
- `ok` B1 首轮 last-prompt.txt 含尾部复核锚点『〖人格锚点·复核〗』
- `ok` B1 首 step anchor_lag=false

## 运行命令

```powershell
$env:LINGSHU_DATA_DIR = '<REPO_ROOT>\.chaos-data-fixverify'
& "node" "<DSH_HOME>\profiles\node_modules\@deepseek-ai\dsh\lib\bin.js" lingshu-lab-headless "<prompt>"   # 每轮一个独立进程
$env:LINGSHU_CONTEXT_WINDOW = '2000'   # critical 复验那一轮
```

模型调用预算：短对话 3 轮 + critical 1 轮 = 4 次 headless（未含混沌 host 重跑）。

## 诚实边界

- 单宿主、单模型路由、单日；样本量小，不构成效应量或泛化证据。
- “回答命中贝塔”依赖模型本身；结构性证据以 memory.jsonl/audit.jsonl 的字段为准（本报告两者都列）。
- critical 轮用 `LINGSHU_CONTEXT_WINDOW=2000` 实验室覆写把 ratio 推过阈值（与混沌 host 同一口径），
  不是真实长会话；锚点位于段落位置（order 90/140），不是请求的绝对首尾。
- 逐字段原始证据见 raw 目录；若结论与原始文件冲突，以原始文件为准。

