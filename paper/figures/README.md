# paper/figures —— 论文矢量图（Fig.1–4）

本目录只放**可复现的矢量 PDF 生成脚本**与产物。所有图都是纯矢量（PDF 1.4，无位图），
文字全部为 Helvetica base-14 标准字体、字号 ≥ 8pt；线条靠**线型 + 线宽 + 灰阶/单一强调色**
三重区分，灰度打印下仍可读。

## 产出物

| 文件 | 内容 | 页面尺寸 | 大小 |
|---|---|---|---|
| `fig1-architecture.pdf` | 控制面 / 数据面分离架构图（无数据） | 504 × 342 pt = 7.00 × 4.75 in | ~9.6 KB |
| `fig2-retention.pdf` | 三臂逐轮设定覆盖率（真实 JSON 数据） | 504 × 208 pt = 7.00 × 2.89 in | ~15.0 KB |
| `fig3-robustness.pdf` | 故障注入下的控制面状态转移图（A/B/C 三剧本实测数字） | 504 × 316 pt = 7.00 × 4.39 in | ~6.1 KB |
| `fig4-redteam.pdf` | 变异提示注入的拦截率：静态规则补丁前/后（20 条，L1–L4） | 504 × 300 pt = 7.00 × 4.17 in | ~5.7 KB |
| `fig1-architecture.preview.png` / `fig2-retention.preview.png` / `fig3-robustness.preview.png` / `fig4-redteam.preview.png` | **仅用于版式自检**的位图预览，**不是交付物**，不要用进论文 | — | — |

## 后端：自写最小矢量 PDF（不是 matplotlib）

本机 pip/PyPI 不可用（镜像挂起、HTTP 403，只有裸 TLS socket 通），`paper/.pylib` 里没有
matplotlib。因此图由 `pdfcanvas.py` **纯标准库**生成 PDF 1.4：

- 对象：Catalog / Pages / Page / Contents / 两个 base-14 字体 / Info；内容流未压缩；
  xref 表逐对象偏移正确，`startxref` + `%%EOF` 齐全。
- 图元：`m/l/S/c/re/f` 路径、`BT/ET/Tf/Tm/Tj` 文本、`g/G` 灰度、`rg/RG` RGB
  （全图只用了一个强调色 `#1f4e9c`）、`d` 虚线。
- 文本度量：内置 Helvetica / Helvetica-Bold 的 AFM 字宽表（ASCII 32–126，各 95 项），
  因此所有文字在落笔前即可精确测量、居中与折行（`box_text` 按 6pt 边距折行，超界即自检失败）。
- 输出**确定性**：不写时间戳、无随机数，重跑字节级一致。
- `--preview` 走 Pillow 后端把同一批图元画成 PNG，**只用于人眼版式检查**，与交付 PDF 无关。

## 构建

```powershell
$py = "<PYTHON>"
$env:PYTHONPATH = "<REPO_ROOT>\paper\.pylib"      # 仅 --preview 需要 Pillow
cd <REPO_ROOT>\paper\figures

& $py fig1_architecture.py --preview
& $py fig2_retention.py --preview
& $py fig3_robustness.py --preview
& $py verify_pdf.py            # 结构验收，退出码 0 = 通过
```

`fig2_retention.py` 在**画任何线之前**先断言三臂均值；对不上就非 0 退出且不写 PDF，
因此陈旧/口径不符的 JSON 不可能悄悄变成一张图。`fig3_robustness.py` 同款纪律：
它读取 `experiments/chaos-injection/out/fig3-inputs.json`，先断言 A 的重试/墙面时间、
B1 的 hook_error 与 fail-open、B2 的降维标记、C 的 0.70/0.85 边界与锚点，任一不符即不写 PDF。

## Fig.3 溯源（故障注入状态转移图）

| 图中元素 | 出处（字段级） |
|---|---|
| A 主机传输故障：7 次尝试 / 17.99 s | `experiments/chaos-injection/out/A-500/case_500.json`（`mock_requests`、`run.wall_s`） |
| A：empty/fabricated 透传、`blocked=false` | `out/A-empty`、`out/A-fabricated` 的 `run.final` / `derived.plugin_blocked` |
| B1：`hook_error=1`、仍返回回答 | `out/B-memory-corruption/case_B1.json`（`plugin_state.audit_kinds`、`derived.got_final`） |
| B2：`fallback=true`、丢 style、冲突提示、宪法保留 | `out/B-memory-corruption/case_B2.json`（`derived.variety_*`、`checks.prompt_*`） |
| C：ratio→phase、锚点、宪法 | `out/C-context-flood/summary.json`（`cases[].ratio_measured/phase/last_prompt_anchor_*`） |
| 安全熔断（fail-closed）路径 | `docs/22` §2.3 S3 host 证据（UCA-01 真实宿主拦截） |

## Fig.1 溯源（结构图，零数据）

| 图中元素 | 出处 |
|---|---|
| 八层优先级与影响域 | `docs/00-架构总览.md` §1 表格、§2 影响域表 |
| 唯一 fail-closed 归属 | `docs/00-架构总览.md` §3（唯一 fail-closed 环节 = 宪法层审计；其余 fail-open） |
| 机制 → 模块映射 | `docs/19-控制论重构契约.md` §1 第 1–4 行 |
| 相变监控 0.70 / 0.85、趋势预触发 | `docs/19` §2.3 |
| 积分反馈（死区 / 抗饱和 / 泄放） | `docs/19` §2.2 |
| 必要多样性降维（unresolved → 丢 style，fail-open） | `docs/19` §2.4、§7（v5.1 接线） |
| U 型注入排序（只排注入侧） | `docs/19` §2.5 |
| 数据面六阶段顺序、T1–T5 插入点 | `engine/pipeline.py` `PipelineSession.step` 步骤 1–6 |
| 三个钩子点 `agent/pre-step` / `turn/end` / `tool/result` | `docs/18-AstrBot协同.md` 行 15–17；`plugin/README.md` 行 124–126 |
| 层 1 只读、层 8 design-only | `docs/00` §1 表格的"可修改 / 可关闭"两列 |

强制自检（`fig1_architecture.py: check()`）：字号 ≥ 8pt、文字不出页、每个标签能装进自己的框
（6pt 水平边距）、**禁止词表**——`VSM`、`rate-distortion`、`Cyber-Cognitive / cognitive envelope`
三个 framing-only 术语，以及四个冻结方向（action loop、adaptive evolution、collective mind、
multi-agent、multimodal）一旦出现即失败。

## Fig.2 溯源（真实数据，逐轮可复核）

**指标**（不叫遗忘率、不叫 accuracy）：

```
y_t = |setting_ids_injected_t  ∩  active_setting_ids_t| / |active_setting_ids_t|
```

即第 t 轮注入上下文覆盖了"当前仍 active 的植入设定"的多大比例。X = 1..100 轮。
**画出来的是 y_t 的 5 轮尾随滑动平均**（min_periods=1）。三臂原始 turn-mean
（0.0250 / 0.2600 / 0.3267）**不印在图里**，由 `06-experiments.tex` 的图注给出；
图内底部只留一行 6pt 灰字溯源（`source: ablation/<run_id>.json …`），因为图注已写全字段路径。

**数据源（唯一）**：

```
paper/ablation/results/abl-20261027-003626-engine.json
run_id = abl-20261027-003626-engine   (valid=true, zero_model=true)
```

| 图中元素 | JSON 字段路径 |
|---|---|
| 三条线（5 轮滑动平均） | `conditions.<arm>.turns[].setting_ids_injected` 与 `conditions.<arm>.turns[].active_setting_ids`（turn=1..100） |
| 70% 相位带起点 t73 | `conditions.full.turns[].ratio >= 0.70`；与 `conditions.full.telemetry.phase_trigger_turns[0]` 交叉校验 |
| 85% 相位带起点 t85 | `conditions.full.turns[].ratio >= 0.85` |
| X 轴下细条带（Full anchor） | `conditions.full.turns[].anchor_applied == true`（28 轮，与相位带水平对齐） |

**线型/颜色（灰度安全）**：

| 臂 | 线型 | 颜色 | 线宽 |
|---|---|---|---|
| Baseline | 短虚线 | 中灰 0.45 | 0.9pt |
| LINGSHU-Basic | 长虚线 | `#1f4e9c` 蓝 | 1.1pt |
| LINGSHU-Full | 实线 | 黑 | 1.2pt |

灰度打印时蓝色降为深灰，仍靠短虚线 / 长虚线 / 实线区分。

**断言（写进脚本，不通过就不出图）**：三臂**原始**逐轮均值必须为
`baseline ≈ 0.03`、`basic ≈ 0.26`、`full ≈ 0.33`（容差 0.005；实测 0.0250 / 0.2600 / 0.3267）；
每臂 100 轮齐全；`active_setting_ids` 不允许为空；`run_id` 必须等于上面写死的值。

**诚实边界（图注必须写，正文不得外推）**：

1. 这是**注入上下文**里的覆盖，不是模型是否记得/答对，不得写成 accuracy、forgetting 或模型侧 retention。
2. `baseline` 无条目 id（滑动窗口），其 y_t 由窗口内注入集合算出，与 LINGSHU 两臂口径不同，**差异是构造性的**（`DESIGN.md` §9.1 C9），不得当效应量。
3. `basic` 与 `full` 的多数轮次重合：控制面不改变召回成员，Full 的独有信息在相位带与锚点条里；**重合本身是结论**，不许为了让线分开而改口径或加平滑。
4. 相位阈值 0.70 / 0.85 是 `rules/` 的当前默认值（`provenance: guess`），不是标定结论。
5. 合成剧本压力构造（`script-100.jsonl`），不得外推真实发生率。

## 验收

`python verify_pdf.py` 独立重解析 PDF（不信任生成器），检查：头部/尾部、xref 偏移逐对象正确、
`/Size` 与条目数一致、页数 = 1、MediaBox、无位图 XObject / 无 `Do`、内容流操作符白名单、
**逐个文字对象做 token 级校验**（`BT /Fn size Tf 1 0 0 1 x y Tm (…) Tj ET` 的操作数必须恰好齐全，
多一个少一个都算失败）、字号 ≥ 8pt（**唯一例外**：图内底部那一行 `source:` 溯源允许 6pt，
见 `SOURCE_FONT`）、文字在页内、纯 ASCII、无禁止词。

> 这条 token 级校验不是形式主义：它在本次修订中真的抓到过一个 bug——文本对象里多带了一个
> 颜色操作数（`Tf` 之后跟了颜色值，7 个操作数喂给 `Tm`），旧版宽松正则放过了它，多数阅读器
> 也会容忍，但严格解析器会报错。修好后重跑两张图均 PASS。

当前状态：Fig.1（75 个文字对象、字号 8.0/8.5pt）、Fig.2（19 个文字对象、字号 8.0pt + 1 行 6pt
溯源）均 **PASS**。

## 已知限制

1. **字体未嵌入**：使用 PDF standard-14（Helvetica / Helvetica-Bold），这是阅读器内置字体，
   没有可嵌入的字体程序；好处是零缺字形风险、文件只有十几 KB，坏处是 `pdffonts` 会显示 `emb=no`。
   若投稿系统的 PDF 检查强制要求 "all fonts embedded"，需要再加一遍 TrueType 子集嵌入
   （本机有 `C:\Windows\Fonts\arial*.ttf` 可用），这是**已知的、需 Lead 决策**的选项，脚本暂未实现。
2. `*.preview.png` 是位图、只用于版式检查，属构建副产物，**不得**作为论文图源。
3. 图注（caption）由 Lead 在 LaTeX 侧撰写；PDF 页脚只放最小溯源行，便于核对，不替代图注。
4. Fig.2 的 5 轮滑动平均会滞后约 2 轮，相位带与锚点条用的是**逐轮原始值**，两者时间对齐时需注意。

## 脚本清单

| 文件 | 作用 |
|---|---|
| `pdfcanvas.py` | 最小矢量 PDF 画布（图元 + AFM 字宽 + 折行 + PDF 序列化 + PNG 预览后端） |
| `fig1_architecture.py` | Fig.1 生成 + 版式/字号/禁止词自检 |
| `fig2_retention.py` | Fig.2 生成 + 数据断言（均值/口径/相位带）+ 自检 |
| `verify_pdf.py` | 独立 PDF 结构验收器（token 级文本校验，退出码 0 = 通过） |
