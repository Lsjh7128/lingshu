# LINGSHU 真实标定 · 影子基线报告

> **本文件由 `tools/analyze_shadow.py` 生成，请勿手改**；重跑即刷新。
> 生成时间（本机）：2026-10-26 23:46:16 ｜ 数据目录：`<DSH_HOME>\plugins\dsh-lingshu`
> **数据时点（data as-of）**：2026-10-26 20:25:10（取日志内最大时间戳；本报告所有 potency 都按此时点计算，不用墙钟——隔天重跑同一份日志结果不变）
> 退出码：0 = 分析完成；**数据不足同样 exit 0**，但下文会显式写「样本不足」。

## 0. 证据标签（每个数字都要能归到一类）

| 标签 | 含义 | 典型内容 |
|---|---|---|
| 真实日志实测 | 直接数日志事件 / 读 state 字段 | turn 数、verdict 分布、UCA 命中、memory 条数 |
| 由日志推导 | 对实测数据按实现公式做推导（不引入外部假设） | 由 ratio 序列算 trend、越界 turn 列表 |
| 由实现计算 | 用 engine/ 参考实现现算（输入是真实条目） | 内存条目 potency / ACT-R 分量 |

> 本工具只读：不写插件数据目录、不改 `rules/`、不写 `plugin/`。

## 1. 数据规模与可用性（真实日志实测）

| 指标 | 值 | 备注 |
|---|---|---|
| audit 事件 | 308 | bad 行 0 |
| turn_begin 事件（= 可归属轮数） | 8 | state.turns=8 |
| pre_step 事件总数（每 step 一条） | 104 | 其中 firstStep 行 8 |
| 有控制字段的 turn 观测 | 5 | ratio/phase 可算的轮数 |
| legacy turn（缺控制字段，无法补算） | 3 | 旧版插件日志 |
| 插件 apply（≈进程启动次数） | 8 | 控制论层首次启用：2026-10-26 19:49:06 |
| memory 条目 | 2 | stateWrites=2 |
| quality_label 事件（规则自动标签） | 0 | 辅助信号，**不进入**标定门槛（见 §2.6） |
| 数据窗口 | 2026-10-25 22:51:31 ~ 2026-10-26 20:25:10 | 本地时间 |
| stage 分布 | {"cold": 2, "warm": 6} | cold/warm/regular |
| 文件缺失 | 无 | 缺失 = 该维度样本不足 |

## 2. 相变监控（双阈值 / 趋势 / 锚点）

### 2.1 ratio 分布（真实日志实测）

| n | p50 | p90 | p95 | min | max | ≥0.70（shift） | ≥0.85（critical） |
|---|---|---|---|---|---|---|---|
| 5 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0.0000 | 0 | 0 |

阈值边界样本：`0.60≤ratio<0.80` 0 个；`0.80≤ratio<0.90` 0 个。**边界样本为 0 时，阈值放在 0.70 还是 0.75 对观测数据没有区别——这段分布不含标定信息。**

完整性校验：日志 `phase` 与按 ratio 重算的 phase 一致（0 处不一致）。

### 2.2 token 膨胀曲线（ratio vs turn，真实日志实测）

| turn | step | ratio | phase | anchor | verdict |
|---|---|---|---|---|---|
| 4 | 1 | 0.0000 | normal | false | allow |
| 5 | 1 | 0.0000 | normal | false | allow |
| 6 | 1 | 0.0000 | normal | false | allow |
| 7 | 1 | 0.0000 | normal | false | allow |
| 8 | 1 | 0.0000 | normal | false | allow |

### 2.3 阈值越界 / 锚点触发（真实日志实测）

| 指标 | 值 | 说明 |
|---|---|---|
| shift 阶段观测 | 0 | phase=shift |
| critical 阶段观测 | 0 | phase=critical |
| ratio≥shift 阈值的 turn | 0 | 触发所在 turn：无 |
| ratio≥hard 阈值的 turn | 0 | 触发所在 turn：无 |
| 锚点开启次数（日志 anchor=true） | 0 | 所在 turn：无 |
| 其中 shift 趋势触发 / critical 无条件 | 0 / 0 | 实现语义：critical 必锚；shift 需 trend>门槛 |

### 2.4 trend（趋势提前量，由日志推导）

> 当前 audit 的 `pre_step` **不记录 trend 字段**，下表按 `engine/control.py` 同一公式 `trend=(r[-1]-r[-3])/2` 由 ratio 序列推导；turn 不连续处会断开，不跨空洞硬拼。

| 指标 | 值 | 门槛 |
|---|---|---|
| 连续观测段数 | 1 | — |
| 可算 trend 的样本 | 3 | 每 3 个连续 ratio 观测出 1 个 |
| trend max | 0.0000 | — |
| 靠近门槛（0.01≤trend≤0.03） | 0 | ≥5 才谈得上门槛落点 |
| trend 触发锚点次数 | 0 | 触发来源：日志 anchor 字段 |
| 门槛 | 0.0200 | rules#control.trendThreshold |

### 2.5 观测口径警告（真实数据接入的关键）

- `state.control.contextWindow` = 1000000；插件缺省窗口 `contextWindowTokens` = 1M（1048576）。
- 若宿主 `request/context` 事件没带 `contextWindow`，插件会退回默认 1M。此时 `ratio=used/1048576` 几乎恒为 0——**它反映的是窗口口径未知，不是「上下文没膨胀」**。
- 在拿到真实窗口之前，ratio 分布对阈值标定**零信息量**：既证明不了 0.70 合适，也证伪不了。
- 另一个坑：`pre_step.turn` 是宿主 session turn id（实测恒为 1），**不能**当轮次用；本报告的轮次来自 `turn_begin.turns`。

### 2.6 结果标签统计（插件规则自动标签，真实日志实测；**辅助观察，不进入门槛**）

> **规则自动标签 ≠ 人工结果标签。** `quality_label` 事件由插件按用户消息子串规则自动产生，本节只做计数与 ratio 关联展示：**绝不进入** `OUTCOME_KINDS` / `REQUIREMENTS` 的 `outcome_labels` 门槛，**不改变** `upgrade_eligible`——guess→measured 仍只认 §6 的标签与人工复核门槛（decisions/012 §2.7）。

**未观测 / 无标签事件**：当前 audit 里没有 `kind==quality_label` 的事件（旧版插件日志，或规则桩未启用）。这是**未观测**，不是「标签率为 0」的结论，也不是故障。

## 3. STPA 前置审计（真实日志实测）

| verdict | 次数 | 占比 |
|---|---|---|
| allow | 5 | 100% |

- 熔断率：0.0%（0/5）；是否 100% 熔断：**否**
- 说明：全 allow 且越狱尝试为 0 时，「100% 熔断」**不能被当作机制有效的证据**（没有负样本可触发），只能说明当前流量里没有越狱尝试。

| UCA | 轮级命中 | block 事件级命中 | 降级留痕 |
|---|---|---|---|
| UCA-01 | 0 | 0 | 0 |
| UCA-02 | 0 | 0 | 0 |
| UCA-03 | 0 | 0 | 0 |
| UCA-04 | 0 | 0 | 0 |
| UCA-05 | 0 | 0 | 0 |
| UCA-06 | 0 | 0 | 0 |
| UCA-07 | 0 | 0 | 0 |
| UCA-08 | 0 | 0 | 0 |

- `preflight_block` 事件：0 条；`preflight_downgrade` 事件：0 条。
- UCA-01 命中数即「越狱尝试」代理指标；UCA-08 是降级不熔断（只记 downgrades）。

## 4. 记忆与 potency

### 4.1 条目结构（真实日志实测）

| 维度 | 分布 |
|---|---|
| 总数 | 2 |
| status | {"active": 2} |
| layer | {"warm": 2} |
| type | {"procedural": 1, "episodic": 1} |
| origin | {"goal": 1, "relation": 1} |
| memory_write 事件 / 写入条数 / 拒绝 | 2 / 2 / 0（原因：{}） |

### 4.2 potency（由实现计算，now=data as-of）

| id | type | status | importance | age(d) | decay | actr | potency | band | ≥archive |
|---|---|---|---|---|---|---|---|---|---|
| mem_001 | procedural | active | 0.80 | 0.9 | 0.7939 | 0.3664 | 0.7939 | high | Y |
| mem_002 | episodic | active | 0.70 | 0.9 | 0.6295 | 0.3210 | 0.6295 | high | Y |

分位：p50=0.7117 ｜ min=0.6295 ｜ max=0.7939 ｜ 高于归档门 0.15：2/2

> 这是 `engine.memory.potency`（指数衰减 ∪ ACT-R 双通道取大）在真实条目上的计算结果，输入真实、公式来自实现；**它不是对「potency 公式正确」的实测证据**。

插件侧纯衰减弱对照（`index.js#potency`，tauDays）：
| id | importance | age(d) | plugin potency |
|---|---|---|---|
| mem_001 | 0.80 | 0.9 | 0.7818 |
| mem_002 | 0.70 | 0.9 | 0.6829 |

### 4.3 冲突覆盖 / 取代 / stale（真实日志实测）

| 指标 | 值 | 说明 |
|---|---|---|
| 非 active 条目 | {} | superseded/stale/suppressed 等 |
| supersede/stale/conflict 类审计事件 | 0 | {} |
| write 拒绝 | 0 | memorywriter 校验拒绝（无 sourceQuote / importance 越界 / 同批重复） |

> 当前插件**没有 supersede/stale 审计事件通道**（engine 有 `apply_supersede`，插件侧未落审计）。所以「冲突覆盖类事件 0 次」只能说明**没有观测通道**，不能说明「没有冲突」。

## 5. 影子模块（Goal / Relation 只记录不注入，真实日志实测）

| 指标 | 值 | 说明 |
|---|---|---|
| agent_state 事件 | 2 | 目标/关系变更留痕 |
| goalEvents 分布 | {"new": 1} | new/progress/blocked/done… |
| relationEvent 分布 | {"positive": 1} | positive/negative/commitment… |
| trust 分布 | {"low": 2} | 关系信任等级 |
| relation modifier | n=2 min=0.90 max=0.90 | 只记录侧产出（是否注入由开关决定） |
| 当前 goal（state） | {"active": ["goal_001"], "events": 1} | active 目标数 / 事件数 |
| 当前 relation（state） | {"trustLevel": "low", "stage": "new", "positives": 1} | — |
| 影子开关（最新 apply 事件） | {"goal": true, "relation": true} | 来自 apply.shadow；旧版本 apply 事件没有该字段 |
| 记忆 origin 分布 | {"goal": 1, "relation": 1} | goal/relation/tool… |

## 6. 标定建议：能否把 guess 升级为 measured

> 机检规则来自 `tools/analyze_shadow.py#REQUIREMENTS`（与 docs/20 的表格同源）。**样本不足就保持 guess**；本工具不改 rules，只输出 `provenance-candidates.json` 候选。

| 参数 | 当前值 | provenance | 分布证据齐备 | 可升级 measured | 结论 |
|---|---|---|---|---|---|
| control.phaseShiftRatio | 0.7 | guess | 否 | 否 | 保持 guess：样本不足（有 ratio 观测的 turn 数 5/30；阈值边界样本（0.60≤ratio<0.80） 0/10；ratio≥0.70 的越界次数 0/20；结果标签（是否漂移/质量下降） 0/20） |
| control.phaseShiftHardRatio | 0.85 | guess | 否 | 否 | 保持 guess：样本不足（有 ratio 观测的 turn 数 5/30；ratio≥0.85 的越界次数 0/10；阈值边界样本（0.80≤ratio<0.90） 0/10；相变期放行且未命中 UCA-07 的轮数 0/10） |
| control.trendThreshold | 0.02 | guess | 否 | 否 | 保持 guess：样本不足（有 ratio 观测的 turn 数 5/30；可算 trend 的连续观测样本（每 3 个连续观测 1 个） 3/28；靠近门槛的 trend 样本（0.01≤trend≤0.03） 0/5；trend 触发提前锚点次数 0/10） |

> 结果标签门槛（`outcome_labels`）只认**人工纠正通道**（`corrections.jsonl` / 人工标签事件）；插件**规则自动标签**（`quality_label`，见 §2.6）**不计入**该门槛，也不能把 guess 升级为 measured。

### 6.1 逐参数证据明细

#### control.phaseShiftRatio（shift 阈值 0.70）

当前证据（真实日志实测 / 由日志推导）：

| 门槛 | 需要 | 当前证据 | 是否达标 |
|---|---|---|---|
| 有 ratio 观测的 turn 数 | ≥30 | 5 次 | ❌ |
| 阈值边界样本（0.60≤ratio<0.80） | ≥10 | 0 次 | ❌ |
| ratio≥0.70 的越界次数 | ≥20 | 0 次 | ❌ |
| 真实 contextWindow 来源（非默认 1M 兜底） | ≥1 | 1 次 | ✅ |
| 结果标签（是否漂移/质量下降） | ≥20 | 0 次 | ❌ |

**结论：保持 guess：样本不足（有 ratio 观测的 turn 数 5/30；阈值边界样本（0.60≤ratio<0.80） 0/10；ratio≥0.70 的越界次数 0/20；结果标签（是否漂移/质量下降） 0/20）**

缺口（按门槛）：有 ratio 观测的 turn 数 差 25；阈值边界样本（0.60≤ratio<0.80） 差 10；ratio≥0.70 的越界次数 差 20；结果标签（是否漂移/质量下降） 差 20

#### control.phaseShiftHardRatio（critical 阈值 0.85）

当前证据（真实日志实测 / 由日志推导）：

| 门槛 | 需要 | 当前证据 | 是否达标 |
|---|---|---|---|
| 有 ratio 观测的 turn 数 | ≥30 | 5 次 | ❌ |
| ratio≥0.85 的越界次数 | ≥10 | 0 次 | ❌ |
| 阈值边界样本（0.80≤ratio<0.90） | ≥10 | 0 次 | ❌ |
| 相变期放行且未命中 UCA-07 的轮数 | ≥10 | 0 次 | ❌ |
| 结果标签（critical 前后是否避免漂移） | ≥10 | 0 次 | ❌ |

**结论：保持 guess：样本不足（有 ratio 观测的 turn 数 5/30；ratio≥0.85 的越界次数 0/10；阈值边界样本（0.80≤ratio<0.90） 0/10；相变期放行且未命中 UCA-07 的轮数 0/10）**

缺口（按门槛）：有 ratio 观测的 turn 数 差 25；ratio≥0.85 的越界次数 差 10；阈值边界样本（0.80≤ratio<0.90） 差 10；相变期放行且未命中 UCA-07 的轮数 差 10；结果标签（critical 前后是否避免漂移） 差 10

#### control.trendThreshold（趋势提前量门槛 0.02）

当前证据（真实日志实测 / 由日志推导）：

| 门槛 | 需要 | 当前证据 | 是否达标 |
|---|---|---|---|
| 有 ratio 观测的 turn 数 | ≥30 | 5 次 | ❌ |
| 可算 trend 的连续观测样本（每 3 个连续观测 1 个） | ≥28 | 3 次 | ❌ |
| 靠近门槛的 trend 样本（0.01≤trend≤0.03） | ≥5 | 0 次 | ❌ |
| trend 触发提前锚点次数 | ≥10 | 0 次 | ❌ |
| shift 阶段观测数（trend 只在 shift 内提前补锚） | ≥10 | 0 次 | ❌ |
| 结果标签（提前锚是否真的减少漂移） | ≥10 | 0 次 | ❌ |

**结论：保持 guess：样本不足（有 ratio 观测的 turn 数 5/30；可算 trend 的连续观测样本（每 3 个连续观测 1 个） 3/28；靠近门槛的 trend 样本（0.01≤trend≤0.03） 0/5；trend 触发提前锚点次数 0/10）**

缺口（按门槛）：有 ratio 观测的 turn 数 差 25；可算 trend 的连续观测样本（每 3 个连续观测 1 个） 差 25；靠近门槛的 trend 样本（0.01≤trend≤0.03） 差 5；trend 触发提前锚点次数 差 10；shift 阶段观测数（trend 只在 shift 内提前补锚） 差 10；结果标签（提前锚是否真的减少漂移） 差 10

### 6.2 监测类声明门槛（不是参数升级，但也需要样本量）

| 声明 | 门槛 | 当前证据 | 是否达标 |
|---|---|---|---|
| STPA 熔断 100% 有效（UCA-01 越狱尝试全部被 block） | 越狱尝试(UCA-01) ≥20 次 + 熔断率=100% + 0 漏放 | UCA-01 命中 0 次；pre_step 观测 5 轮 | ❌ |
| UCA-08（积分饱和）降级可观测 | ≥10 次饱和降级事件 | 0 次 | ❌ |
| supersede 误判率 ≤5%（G1） | ≥50 次 supersede 记录 + ≥3 次人工抽检 | 冲突类审计事件 0 次、非 active 条目 0 条（当前插件无 supersede 审计通道） | ❌ |
| potency 归档门 0.15 可标定 | ≥1000 个真实 turn（docs/17 §5 W4.5） | 8 个 turn | ❌ |
| 影子 goal/relation 误报率可评估 | ≥200 条 agent_state 记录 + ≥50 条人工标注 | 2 条记录 | ❌ |

### 6.3 provenance-candidates.json

- 本次运行未单独落盘（`--no-candidates`）。日常运行默认在 `--out` 同目录生成 `provenance-candidates.json`，也可用 `--candidates-out <path>` 指定。
- 内容摘要（与候选 JSON 一致）：可升级 `无`；分布证据齐备 `无`；本次运行后 measured 参数数 = 0。
- 当前 `rules/decay-params.yaml#control` 全部为 `provenance: guess`；本报告**不构成**升级依据。
- 候选 JSON 另含 `quality_labels` 辅助块（规则自动标签统计，`auxiliary_only=true`，**不参与**任何门槛/候选判定）。

## 7. 下一步缺什么数据（按优先级）

1. **真实 contextWindow**：让插件从宿主 `request/context` 真正拿到窗口（当前 state 为 1000000），否则 ratio 恒 0，相变相关门槛全部无法达成。
2. **结果标签通道**：docs/17 的 corrections（用户说「不对 / 不该提 / 漏了」）尚未实现；没有「漂移/质量下降」标签，就**只能观测分布，不能标定阈值落点**。
3. **更多真实轮次**：三个参数都要求 ≥30 个有 ratio 的 turn；当前 5 个（且多为 ratio=0）。
4. **趋势样本**：trendThreshold 需要 ≥28 个连续观测（≥30 个 turn）；当前 3 个 trend 样本。
5. **越狱/红队样本**：STPA 要证明「100% 熔断有效」需要 ≥20 次真实 UCA-01 尝试；当前 0 次。
6. **supersede / stale 审计通道**：插件侧要落事件，才谈得上 G1 误判率。
7. **影子开关留痕**：apply 事件写 `shadow`（v4.3 起已有）；跑数据时固定开关并记录，否则误报率不可归因。
8. **人工结果标签**：规则自动标签（§2.6 的 `quality_label`）只能当辅助信号；要把 `outcome_labels` 门槛填满，仍需 `docs/17` 的 corrections 人工纠正通道（当前未实现）。

## 8. 复现

```bash
python tools/analyze_shadow.py --dir "<DSH_HOME>\plugins\dsh-lingshu" --out <报告.md>
python tools/analyze_shadow.py --out acceptance/shadow-baseline.md  # 默认读 $DSH_HOME
```

> 数据时点：2026-10-26 20:25:10 ｜ 分析时点：2026-10-26 20:25:10
