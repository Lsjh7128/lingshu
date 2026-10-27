# 混沌工程 v2 · 验收记录（2026-10-27）

> 归属：`docs/23-混沌工程v2-科学压测设计.md` §11 验收与复现。
> 本文件记录**本次落地的可复现证据**；产物本体在 `acceptance/chaos/v2/`，语料在 `chaos-set/v2/`。
> 纪律：这份记录只写"跑出来什么"，不写"应该是什么"；跑不成的一律写未观测，不填 0。

---

## 1. 一句话结论

**8 族 / 3 次重复 / 212 项检查 / 20 条不变量全部成立 / 零模型调用 / 同 seed 逐字节确定**，
v2 campaign 退出码 **0**；另记录 **5 条产品缺陷 FINDING**（只记录、未改产品代码）与 1 条 v1 历史漂移。

---

## 2. 复现命令与结果

Python = `<PYTHON>`（记为 `$py`）

| # | 命令 | 结果 |
|---|---|---|
| 1 | `$py tools\gen_chaos_v2.py --check` | **PASS**：8 份语料 + manifest + README 与生成器逐字节一致（语料未被手改） |
| 2 | `$py chaos-set\v2\validate.py` | **PASS**：8 份语料 / 160 行；封套/唯一性/severity/LF/无 BOM/哈希/指纹表一致 |
| 3 | `$py tools\run_chaos_campaign.py` | **exit 0**：8/8 族、checks 212、failed 0、不变量 20/20、flaky 0、异常 0、model_calls 0、墙钟 ~3.3s |
| 4 | `$py tests\test_chaos_campaign.py` | **PASS 122 / FAIL 0**（含"反例必须红"与红线守卫；自测后自动还原 `.last.json` 与报告） |
| 5 | `powershell -File scripts\chaos_v2_night.ps1 -DryRun` | **exit 0**：只打印计划，不写文件 |
| 6 | `powershell -File scripts\chaos_v2_night.ps1` | **exit 0**：4 步全过（`--check` → `validate` → campaign → 摘要），日志落 `acceptance/chaos/v2/logs/` |
| 7 | 插件三套断言（`host-sim` / `control-sim` / `stress`，`--assert`） | **exit 0 / exit 0 / exit 0**（插件未被本任务修改；control-sim 自带 148 passed 汇总行） |
| 8 | v1 回归抽查：`tests/test_run_chaos.py` / `test_control.py` / `test_pipeline_integration.py` | **117 / 161 / 65 passed，0 failed** |

> 注：第 8 项的条数会因同期另一项"测试严密性审计"任务增删断言而变化；本任务只保证 v1 套件**在我改动前后都全绿**。

---

## 3. 正式 run 快照

| 项 | 值 |
|---|---|
| run_id | `chaos2-20261027-013720-engine` |
| 产物 | `acceptance/chaos/v2/chaos2-20261027-013720-engine.json`、`.last.json`、`2026-10-27-report.md` |
| 模式 | engine（零模型调用；F6 仅一次 `node ... --no-model` 纯计算） |
| seed / repeats | 20261027 / 3（seed = 20261027、20261028、20261029） |
| 不变量 | 20 / 20 通过，flaky 0，覆盖 20 / 20 |
| checks | 212 cases，failed 0，failures 0 |
| 异常 | 0 |
| findings | 5 条（F-01…F-05，见 `findings.md`） |
| campaign digest | `118f3eb2832775f17ab70df021949aec2f897cc55814a9982ddcbda765fe6cc9` |
| 确定性 | 同 seed 跨进程 digest 完全一致（INV-01）；换 seed 仅指标浮动、判定不变（INV-20） |
| 墙钟 / 预算 | 3.27 s / 90 s |
| INV-19 | 进程内越界写意图 **0**；快照差分按设计披露（共享工作区里其它进程的并发写，见下） |

族级数字（cases / 不变量 / 关键结论）：

| 族 | cases | 不变量 | 关键结论 |
|---|---|---|---|
| F1 STPA 决策表 | 30 | 6/6 | UCA-01…08 正样本全中、控制组 0 误报；`rule_stub_gap=4`（词表盲区诚实计数） |
| F2 相变边界 | 29 | 5/5 | 0.70/0.85 逐点映射正确；`[0.60,0.80)` 边界样本 14（≥10）；canary UCA-07 可触发 |
| F3 积分抗饱和 | 13 | 6/6 | `max|I|=cap`；饱和后只泄放不积分；泄放半衰期实测 14 轮 vs 解析 13.86 |
| F4 召回对抗 | 15 | 5/5 | superseded 零泄漏（0/27）；预算不足如实标记；同义改写只测不设阈值 |
| F5 合成压力 | 68 | 5/5 | 宪法恒在首位；8 条互斥规则丢对域；`mult∈[1.0,1.15]`；无 `overflow` 未标记 |
| F6 存储故障 | 14 | 3/3 | 写路径"确认成功或显式失败"；`write_failure_surfaced=1`、`false_success=0`；node 零模型 |
| F7 稳态基线 | 11 | 3/3 | 回放 `recall@3=1.000`、`forbidden_leak=0`、`truncated_turns=0`、`blocked_turns=0`；良性输入 0 误触发 |
| F8 变形关系 | 32 | 5/5 | MR1–MR6 全成立（MR6 跨 seed 判定不变） |

---

## 4. 红线核对（逐条可复核）

| 红线 | 结论 | 证据 |
|---|---|---|
| `rules/` 一行未改 | ✅ | `rules/decay-params.yaml` 中 `provenance: measured` 计数 = 0，`phaseShiftRatio=0.7` / `phaseShiftHardRatio=0.85` 未动 |
| `measured` 仍 0 | ✅ | 同上（v2 不产标定候选，不适用 `--apply`） |
| v1 未动 | ✅ | `chaos-set/README.md §7` 三份指纹一致；四份 v1 语料 mtime 均早于本任务开始（2026-10-27T00:00）；`docs/22` 仍含四剧本；`tools/run_chaos.py` 未被替换 |
| `desktop` profile 零改动 | ✅ | v2 只写 `acceptance/chaos/v2/`；未安装/未重启/未碰 profile |
| 不伪造 | ✅ | 跑不成一律 `skipped` + `skip_reason`；F6 读路径静默降级只记 `observability_gap`、不算通过 |
| 不改 v1 / 不改判定凑通过 | ✅ | v1 语料/文档/执行器逐字节保留；v2 判定与语料一起冻结在 manifest（`--check` 可复验） |

---

## 5. 并发与环境说明（诚实记录）

- 本任务执行期间，仓库里**同时有另一项「测试严密性审计」任务在跑**（写 `acceptance/audit/`、
  `paper/supplement/`、并会增删 `tests/*.py` 的断言数）。因此：
  1. 全局 Python 断言总数**不由本任务写死**，README/项目简介/CHANGELOG 只声明 v2 新增 **122 项**，
     全局口径以 `acceptance/audit/` 报告为准；
  2. INV-19 在正式 run 里披露过 1 处"未归因快照差分"（`paper/main.tex` 被其它进程修改），
     进程内写意图 `write_ops_total=0` —— 按设计"归因优先"，不判失败；独占工作区跑
     `--strict-write-scope` 可加严。
- 清理了两个沙箱遗留的孤儿目录（`.chaos-v2`、`chaos-set/.v2_fsbackend_orphan`），
  它们与 v1/v2 产物均无关。
- v1 基线存在一处**历史漂移**（早于本任务）：`variety.jsonl` 实际 8 行 / 3868B / `a2ca90c4…`，
  而 `chaos-set/README.md §7` 与 `docs/22 §2` 仍记 1 行 / 482B / `70af8170…`。
  v2 **不修 v1**（红线 9），已记入 `findings.md` 与 `tests/test_chaos_campaign.py` 的已知漂移注明。

---

## 6. 收口待办（不在本任务内做）

1. **F-01…F-05 五条产品缺陷**：各开单独任务 + 必要 ADR；修完在 `findings.md` 标 ✅ 并附新 run_id。
2. **v1 指纹表漂移**：单独任务更新 `chaos-set/README.md §7` 与 `docs/22 §2`（需 H0 批准，因涉"判定基线变更"）。
3. **夜间接入**：`scripts/chaos_v2_night.ps1` 已就绪（独立于 v1 夜间脚本，engine 档零模型 ≤90s）；
   是否注册成每日计划任务由 H0 决定（本任务不擅自注册系统任务）。
4. **v2 不覆盖项**（`docs/23 §9`）：真实模型行为、真实发生率、OS 级 IO 故障、多进程并发写、
   阈值正确性、延迟——这些仍归 v1 host 档与 `docs/20`，**不要**用 v2 结论外推。

---

*本记录由 v2 campaign 与测试的真实输出整理；数字可用第 2 节命令一键复现。*
