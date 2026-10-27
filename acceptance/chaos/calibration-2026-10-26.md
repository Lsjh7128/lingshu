# 混沌结果 → 相变双阈值标定报告（2026-10-26）

> 生成：`tools/chaos_calibrate.py`（本文件为生成物，勿手改）｜ repo：`<REPO_ROOT>`
> 数据目录：`<REPO_ROOT>\acceptance\chaos` ｜ 生成时间：2026-10-26 23:46:15
> 规则：H0 裁决 B —— **双阈值联动**（`decisions/011-相变阈值联动规则.md`）
> 结论：**证据不足，保持 guess**

## 1. 判定规则（写死，不提供降门槛开关）

- 只认 `mode == "host"` 的运行；**engine 模式永远不参与**（没有真实模型回答，其 `degraded_at_ratio` 若非空视为 schema 违规）；
- 可判定退化 = `long_context_flood.key_question.keyword_hit === false` **且** `degraded_at_ratio` 为数字 **且** 该 ratio 严格小于当前 shift 阈值 0.7（当前 hard=0.85）；
- 对照 = 同场景 `keyword_hit === true`；
- 跨 run 门槛：degraded ≥ 3 且 对照 ≥ 3（写死在脚本常量里，测试断言不可覆盖）；
- 联动规则 B（ADR-011）：`new_shift = max(0.40, floor((min(degraded_at_ratio)-0.05)*100)/100)`；`new_hard = min(0.95, round(new_shift+0.15, 2))`——**hard 随 shift 移动**，保留 0.15 间距宽度（不是保留 hard 绝对值），hard 上限 0.95；
- shift 算出的新值不低于当前 shift → 没有可执行下调，不产出 applied 候选；

## 2. 当前值：四处副本（shift + hard）

| 位置 | shift | hard |
|---|---|---|
| `rules/decay-params.yaml` | 0.7 | 0.85 |
| `engine/config.py` | 0.7 | 0.85 |
| `plugin/dsh-lingshu/cordis.patch.yml` | 0.7 | 0.85 |
| `plugin/dsh-lingshu/control.js` | 0.7 | 0.85 |

- 四处一致：shift **是** ｜ hard **是**（不一致时 apply 直接拒绝，先修 `check_config_sync`）

## 3. 混沌运行数据

| 文件 | run_id | mode | 参与？ | keyword_hit | degraded_at_ratio | 说明 |
|---|---|---|---|---|---|---|
| chaos-20261026-203440-host.json | chaos-20261026-203440-host | host | 是 | True | None | 已计入证据 |
| chaos-20261026-203604-engine.json | chaos-20261026-203604-engine | engine | 否（engine） | True | None | 不参与标定 |
| chaos-20261026-204750-engine.json | chaos-20261026-204750-engine | engine | 否（engine） | True | None | 不参与标定 |
| chaos-20261026-231322-engine.json | chaos-20261026-231322-engine | engine | 否（engine） | True | None | 不参与标定 |
| chaos-20261026-231322-host.json | chaos-20261026-231322-host | host | 是 | True | None | 已计入证据 |
| chaos-20261026-233046-engine.json | chaos-20261026-233046-engine | engine | 否（engine） | True | None | 不参与标定 |
| chaos-20261026-234106-engine.json | chaos-20261026-234106-engine | engine | 否（engine） | True | None | 不参与标定 |
| chaos-20261026-234209-engine.json | chaos-20261026-234209-engine | engine | 否（engine） | True | None | 不参与标定 |
| chaos-20261026-234349-engine.json | chaos-20261026-234349-engine | engine | 否（engine） | True | None | 不参与标定 |
| chaos-20261026-234400-host.json | chaos-20261026-234400-host | host | 是 | True | None | 已计入证据 |
| .last.json | — | — | 否 | — | — | 跳过：隐藏/汇总文件，不是 run |
| provenance-candidates.json | — | — | 否 | — | — | 跳过：候选文件自身，不是 run |
| s4-seed.json | — | — | 否 | — | — | 跳过：schema: 缺 run_id |

未计入的 run/证据：
- `chaos-20261026-203604-engine`（chaos-20261026-203604-engine.json）：engine 模式不参与标定
- `chaos-20261026-204750-engine`（chaos-20261026-204750-engine.json）：engine 模式不参与标定
- `chaos-20261026-231322-engine`（chaos-20261026-231322-engine.json）：engine 模式不参与标定
- `chaos-20261026-233046-engine`（chaos-20261026-233046-engine.json）：engine 模式不参与标定
- `chaos-20261026-234106-engine`（chaos-20261026-234106-engine.json）：engine 模式不参与标定
- `chaos-20261026-234209-engine`（chaos-20261026-234209-engine.json）：engine 模式不参与标定
- `chaos-20261026-234349-engine`（chaos-20261026-234349-engine.json）：engine 模式不参与标定

## 4. 聚合证据

| 指标 | 值 | 门槛 |
|---|---|---|
| 可判定退化样本（host，ratio<当前 shift 阈值） | 0 | ≥3 |
| 对照样本（host，keyword_hit=true） | 3 | ≥3 |
| min(degraded_at_ratio) | — | — |

## 5. 结论

- **证据不足，保持 guess。**
- 原因：样本门槛未达：degraded 0/3，对照 3/3

> 联动规则 B（ADR-011）：hard 跟随 shift 移动，保留 0.15 间距宽度，上限 0.95。hard 没有独立的 critical 级退化证据，属结构性跟随；若日后要独立标定 hard，需要另一组 critical 级证据（本工具当前不做）。

### 5.1 辅助观察：规则自动标签（`quality_labels`，**不参与门槛**）

> **标签是辅助信号。** 本节只读 run JSON 顶层 `quality_labels`（`{total, by_kind}`），来源是插件 `audit.jsonl` 的 `kind==quality_label` 规则自动标签。它**不参与** degraded/对照样本计数，**不影响** `MIN_DEGRADED=3` / `MIN_CONTROL=3` 门槛与任何 candidate 判定；**规则自动标签 ≠ 人工结果标签**（docs/20 §1.2 / decisions/012 §2.7），不能作为 measured 证据。

| 指标 | 值 | 说明 |
|---|---|---|
| 有标签的 run 数 / run 总数 | 4 / 10 | 顶层带 `quality_labels` 的 run |
| 标签总数（`total` 字段） | 0 | 只读，不折入门槛 |
| by_kind 合计 | 0 | 与 total 不一致时以 run JSON 原值为准 |
| 闭集 kind 计数 | {"wrong": 0, "missing": 0, "should_not_recall": 0, "drift": 0, "overreach": 0} | wrong/missing/should_not_recall/drift/overreach |
| 格式不可用的 run / 字段 | 0 / 0 | 坏数据跳过，不猜 |

> 标签分布只用于观察「真实宿主里用户纠正/漂移信号有多频繁」；它们既不能把 degraded 计数补到门槛，也不能替代人工判定的 keyword_hit 判定。

## 6. apply 结果

本次未给 `--apply`：配置零修改。

## 7. 复现 / 下一步

```bash
python tools/chaos_calibrate.py                 # 只出本报告与候选 JSON
python tools/chaos_calibrate.py --apply         # 证据达标才改配置（自动 check + 回滚）
```

- 需要更多 **host 模式** 混沌数据（A5 夜间调度），且每个 run 必须带 `key_question.keyword_hit`；
- 判定 keyword_hit 需要有真实模型回答；engine 模式数据永远不能替代；
- 若长期无 host 数据，本报告会持续写「证据不足，保持 guess」——这是正确行为，不是故障；
- 规则变更记录：`decisions/011-相变阈值联动规则.md`（H0 裁决 B：双阈值联动）。
