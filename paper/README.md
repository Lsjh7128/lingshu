# LINGSHU 论文工程 · `paper/`

本目录是 **LINGSHU（灵枢）v5.1** 的 arXiv 预印本工程：LaTeX 源码、矢量图、三臂消融结果、真实宿主验证记录，以及 H0 评审用的《大纲》《术语表》。

> **一句话**：本文只报告 **契约可执行性 + 机制可达性**，**不报告端到端收益**；所有参数 `provenance: guess`、`measured = 0`；四个冻结方向只出现在 Limitations。

---

## 1. 目录结构

```
paper/
├── main.tex                    论文主文件（\input 各节）
├── refs.bib                    参考文献（14 条，全部被引用）
├── sections/
│   ├── 01-abstract.tex         Abstract
│   ├── 02-intro.tex            §1 Introduction
│   ├── 03-related.tex          §2 Related Work
│   ├── 04-problem.tex          §3 Problem Formulation
│   ├── 05-method.tex           §4 Method（10 子节，全文重心）
│   ├── 06-experiments.tex      §5 Experiments（真实数据回填）
│   ├── 07-discussion.tex       §6 Discussion
│   ├── 08-limitations.tex      §7 Limitations（冻结方向唯一允许出现处）
│   ├── 09-conclusion.tex       §8 Conclusion
│   └── 10-appendix.tex         Appendix A–F（UCA 全表、参数全表、复现命令）
├── figures/
│   ├── fig1-architecture.pdf   Fig.1 控制面/数据面架构（矢量）
│   ├── fig2-retention.pdf      Fig.2 设定保持率三线 + 70% 相位带（矢量）
│   ├── pdfcanvas.py            自写最小矢量 PDF 后端（零依赖）
│   ├── fig1_architecture.py    Fig.1 生成脚本（可重复运行）
│   └── fig2_retention.py       Fig.2 生成脚本（读消融结果 JSON）
├── ablation/
│   ├── DESIGN.md               消融设计规格（预注册，跑前定稿）
│   ├── script-100.jsonl        100 轮确定性剧本（由 tools/gen_ablation_script.py 生成）
│   └── results/
│       └── abl-20261027-003437-engine.json   ← 正式结果（唯一引用源）
├── host-validation/
│   ├── REPORT.md               真实 LLM 宿主小样本验证报告（n=10 格，诚实边界）
│   ├── calls.json              逐格机读记录（prompt_sha256/step/usage/注入字段）
│   └── evidence/               41 个原始证据文件（raw 事件流、last-prompt、audit、state）
├── data/
│   ├── INVENTORY.md            60 个数据文件实数盘点（合成占比 94.47%）
│   └── DATASET-SPEC.md         数据集构建规格
├── method/MAPPING-NOTES.md     机制 → 公式 → 代码落点 → 断言数 → 不能声称什么
├── OUTLINE-zh.md               H0 评审用论文大纲（599 行）
└── GLOSSARY.md                 H0 评审用术语映射表（29 条，含 framing-only 标注）
```

---

## 2. 怎么编译（本机已装 TeX Live 2026 portable）

TeX Live 2026 已装在工作区：`<REPO_ROOT>\tools-tex\texlive`（portable，不写系统 PATH；`bin\windows` 下有 `pdflatex` / `xelatex` / `bibtex`）。中文对照版另装了 `xecjk` / `ctex` / `fontspec`（字体用 Windows 自带的 SimHei）。

**一键构建（推荐）**：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File paper\rebuild.ps1 both
# 只构建英文：... -File paper\rebuild.ps1 en
# 只构建对照：... -File paper\rebuild.ps1 zh
```

`rebuild.ps1` 会把源码复制到 `<REPO_ROOT>\build\{en,zh}` **隔离编译**（避免与其他并行编辑/编译抢文件），成功后把产物拷回 `paper/`：

| 产物 | 页数 | 说明 |
|---|---|---|
| `paper/main-en.pdf` | 26 | 英文版（`xelatex`；因为 06f 引用了一句中文语料，编译时自动加载 CJK 字体） |
| `paper/main-bilingual.pdf` | 59 | 中英对照版（`xelatex`；每节先英文后中文） |

**手动构建**（在 `paper/` 下，先 `$env:PATH="<REPO_ROOT>\tools-tex\texlive\bin\windows;$env:PATH"`）：

```powershell
# 英文（建议 xelatex，理由同上）
xelatex main; bibtex main; xelatex main; xelatex main
# 中英对照
xelatex main-bilingual; bibtex main-bilingual; xelatex main-bilingual; xelatex main-bilingual
```

**Overleaf 备选**：上传 `paper/`（保持 `sections/`、`sections-zh/`、`figures/`、`tables/` 相对路径），英文版 pdfLaTeX 也可编译（但 06f 的中文例句会缺字，需把 Overleaf 编译器切到 XeLaTeX）；对照版**必须** XeLaTeX。

> **依赖宏包**：`geometry times amsmath amssymb booktabs graphicx microtype natbib hyperref url`；对照版另需 `fontspec xeCJK`。缺少时用 `tlmgr install <pkg>`（仓库已指向清华镜像）。

> **`paper/_*.py` 说明**：`_install_mpl.py` / `_extract_mpl_cache.py` 是当时尝试安装 matplotlib 的一次性脚本（失败留档），`_lead_verify.py` 是 Lead 独立复核脚本。三者**不参与编译**。

---

## 3. 复现论文中的每个数字

所有命令在 `<REPO_ROOT>` 下执行；Python 用捆绑解释器（系统 `python` 是 WindowsApps 占位符，不可用）：

```powershell
$py = "<PYTHON>"
$env:PYTHONIOENCODING = "utf-8"
```

| 论文中的数字 | 复现命令 / 文件 |
|---|---|
| 892 Python 断言、14 文件（审计快照） | `python tests/test_*.py`（逐文件跑；汇总见 `README.md` 与 `acceptance/audit/`） |
| 396 插件断言（审计快照） | `node plugin/dsh-lingshu/host-sim.mjs --assert`、`control-sim.mjs --assert`、`stress.mjs --assert`、`mutation-regressions.mjs --assert` |
| 变异测试 38/44 → 42/44（42/42 非等价） | `acceptance/audit/变异测试报告-2026-10-27.md`、`mutation-results-2026-10-27.json` |
| 覆盖率 engine/ 89.95% line / 80.71% branch | `acceptance/audit/断言强度与覆盖率报告-2026-10-27.md`、`coverage-summary-2026-10-27.json` |
| 冻结基线 `recall@3=1.000` / `forbidden_leak=0` | `python tools/run_learning.py --json` 或 `python -c "from engine import pipeline,config;print(pipeline.replay_pipeline(config.default(),'.','v1')['summary'])"` |
| 972 组网格无改进 | `python tools/search_params.py` → `research/01-参数搜索报告.md` |
| 参数 `measured=0` | `acceptance/shadow-baseline.md`；`python tools/analyze_shadow.py` |
| Table 2 / Fig.2 全部数字 | `paper/ablation/results/abl-20261027-003437-engine.json` |
| Fig.2 | `python paper/figures/fig2_retention.py`（读上述 JSON） |
| Fig.1 | `python paper/figures/fig1_architecture.py` |
| 宿主 pilot 数字 | `paper/host-validation/calls.json`、`REPORT.md` |
| chaos 四剧本数字 | `acceptance/chaos/chaos-20261026-234349-engine.json`、`chaos-20261026-234400-host.json` |

**图的生成方式（必须知道）**：本机 PyPI/镜像不可达，`matplotlib` 装不上；两张图由 `paper/figures/pdfcanvas.py`——一个**纯 Python 标准库的矢量 PDF 后端**（PDF 1.4，Helvetica base-14，无位图）——生成。这不影响矢量性与可复现性，但与"用 matplotlib"不同，论文与 README 都不应声称用了 matplotlib。

---

## 4. 写作纪律（三色边界，改稿前必读）

| 标记 | 含义 |
|---|---|
| ✅ 可安全声称 | 能指到 `tests/` 断言、`acceptance/*.json` 字段、`engine/*.py` 行号，且可复现 |
| ⚠️ 必须 hedge | 机制存在但效果未测；句式用 *mechanism reachable* / *in the tested configuration* / *by construction, not by measurement* |
| ⛔ 严禁出现 | 任何百分比收益、SOTA、validated threshold、"solves hallucination"、把 `guess` 写成 calibrated、四个冻结方向进入 Method |

**术语纪律**（见 `GLOSSARY.md`）：
- 五个指定映射必须用学术正式名：U-shaped Attention Anchoring / Phase-Shift Preemptive Monitoring / Requisite Variety Dimensionality Reduction / Information-Theoretic Rate-Distortion Memory Consolidation / Causal Interposition Safety Gate。
- `VSM`、`rate-distortion`、`Cyber-Cognitive Envelope` **无实现**，只能作 framing：首次出现即声明"本文命名/借用，不声称实现"。
- `ACT-R` / `STPA` / `Ashby` / `PAD` / `anti-windup` / `Soar impasse` 一律写 *we adopt / adapt*，不得写 *we propose*。

---

## 5. 已知环境限制（写进论文 Limitations，不要藏）

1. **零模型引擎**：`engine/` 不调用 LLM，所有指标是注入侧/结构侧代理，不是模型行为。
2. **无 TeX 工具链**：源码已做静态校验（括号/环境/引用一致），但**未在本机编译过**；首次编译请留意。
3. **宿主 pilot 的检索失效**：把 100 轮压成 78 KB 单条消息时，插件检索 `matched=0`——这是对 `docs/22` host 压测口径的实证质疑，已写入 §5.3。
4. **成本事实**：1 次 headless CLI ≈ 1–10 次模型请求；10 格 pilot 实测 26+ 请求 / 193,288 token，"6 次调用"的旧假设不成立。

---

## 6. 版本

| 版本 | 日期 | 内容 |
|---|---|---|
| paper-0.1 | 2026-10-27 | 大纲 + 术语表（H0 门禁） |
| paper-0.2 | 2026-10-27 | 全节初稿 + 三臂消融首跑 + Fig.1/Fig.2 + 宿主 pilot |
| **paper-1.0** | 2026-10-27 | 正文 9 节 + 附录全落盘；消融终版 `abl-20261027-003626-engine`（verify 51/51）；Fig.1/Fig.2 定稿（矢量，504×342 / 504×244 pt）；加入"赛博养鱼/虚拟伴侣"动机；README 增 Background 章节 |

**定稿规模**：正文约 6,600 词（Abstract 199 / Intro 800 / Related 612 / Problem 527 / Method 2070 / Experiments 1095 / Discussion 492 / Limitations 538 / Conclusion 178）。图 2 张、表 2 张（Table 1 由 `GLOSSARY.md` 派生、Table 2 在 Experiments、Table 3 在附录）。

*本目录任何与仓库代码/JSON 冲突的表述，以仓库为准并把本文档视为 bug。*
