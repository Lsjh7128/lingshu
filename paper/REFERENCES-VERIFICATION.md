# LINGSHU 论文参考文献查证报告

- 查证时间：2026-10-28（本地时区 +08:00）
- 查证对象：疑似"幻觉引用" [6]、[8]、[10]、[11]、[15]，以及顺带核对的 [7]（Mem0）
- 查证渠道：arXiv API（`export.arxiv.org/api/query?id_list=…`）、Crossref REST API（`api.crossref.org/works?query.bibliographic=…`）
- 结论：**五篇全部是真实存在的 arXiv 预印本，没有一篇需要替换**；其中两篇已找到正式出版版本（[8] Sentipolis、[7] Mem0），已补 DOI。

## 1. 逐条判定

| 编号 | key | 标题 | arXiv ID | arXiv API 结果 | Crossref 正式版 | 判定 |
|---|---|---|---|---|---|---|
| [6] | `zenbrain2026` | ZenBrain: A Neuroscience-Inspired 7-Layer Memory Architecture for Autonomous AI Systems | 2604.23878 | 存在，v3（2026-04-26 首发，2026-08-09 更新），作者 Alexander Bering，摘要与正文描述一致 | 无 DOI（Zenodo concept DOI 见 arXiv 评论） | **真实** |
| [8] | `sentipolis2026` | Sentipolis: Emotion-Aware Agents for Social Simulations | 2601.18027 | 存在，v2，作者 Fu/Chen/Xiao/Xuan/Busso/Diab | **已出版**：Findings of ACL 2026，DOI `10.18653/v1/2026.findings-acl.368` | **真实，已补 DOI/会议** |
| [10] | `fsfm2026` | FSFM: A Biologically-Inspired Framework for Selective Forgetting of Agent Memory | 2604.20300 | 存在，v2，作者 Gu, Yingjie 等 10 人（拼写与大小写已逐字核对） | 无 DOI | **真实** |
| [11] | `himemory2026` | Human-Inspired Memory Architecture for LLM Agents | 2605.08538 | 存在，v1，作者 Kerestecioglu 等 5 人 | 无 DOI | **真实** |
| [15] | `persmem2026` | PersMem: Internalizing Personality into Dual-Pathway Memory for LLM Agents | 2609.34372 | 存在，v1，作者 Zhang 等 5 人 | 无 DOI | **真实** |
| [7]（附加） | `mem0_2025` | Mem0: Building Production-Ready AI Agents with Scalable Long-Term Memory | 2504.19413 | 存在 | **已出版**：ECAI 2025，DOI `10.3233/faia251160` | **真实，已补 DOI/会议** |

> 关于"日期看起来像未来"：`2604`/`2605`/`2609` 是 arXiv 的 `YYMM` 编号，分别对应 2026 年 4 月、5 月、9 月；查证日为 2026-10-28，均早于当前日期，不属于未来文献。

## 2. 为什么不替换

Crossref 对 FSFM / Human-Inspired / PersMem / ZenBrain 的标题检索没有返回同文 DOI，但返回了**主题相近的真实论文**（例如 ICASSP 2026 的 *FadeMem: Biologically-Inspired Forgetting for Efficient Agent Memory*、HAI 2025 的 *Human-Like Remembering and Forgetting in LLM Agents: An ACT-R-Inspired Memory Architecture*）。这些是"相近方向"的真实论文，但**不是**我们引用的对象；既然被引 arXiv 预印本本身可验证，按查证规则就不做替换（替换会改变 Related Work 的论述对象）。

## 3. 已落实的格式修改

1. **arXiv 标题大小写**：五条 arXiv 条目的 `title` 改为双花括号 `{{…}}`，BibTeX 不再强制小写（现在 References 中保留 "Neuroscience-Inspired 7-Layer…" 等原始大小写）。
2. **全书名斜体**：[1] Anderson 2007、[3] Ashby 1956、[5] Beer 1972、[12] Leveson 2011、[14] Mehrabian & Russell 1974 的标题在 `.bbl` 中已由 `plainnat` 输出为 `\emph{…}`（见 `build/en/main-en.bbl`），PDF 第 30 页可见斜体，无需再手工包 `\textit{}`。
3. **作者拼写**：[10] `Gu, Yingjie` 等 10 位作者、[8] `Mona T. Diab`、[7] Mem0 五位作者均按 arXiv/Crossref 记录逐字核对；`Åström, Karl J.` 等重音字符保留。
4. **引用标号颜色**：`hyperref` 由 `hidelinks` 改为 `colorlinks=true, citecolor=blue, linkcolor=black, urlcolor=blue`，正文中 `[6]` 等编号与 DOI/arXiv 链接呈蓝色（第 2 页与第 30 页已核验）；其余正文仍为黑色。
5. **链接补全**：五篇 arXiv 条目 note 中加入 `\url{https://arxiv.org/abs/…}`；[8] 与 [7] 加入 DOI 链接。

## 4. 复现命令

```bash
# arXiv 存在性（一次查五条）
https://export.arxiv.org/api/query?id_list=2604.23878,2604.20300,2605.08538,2601.18027,2609.34372

# Crossref 正式版（示例）
https://api.crossref.org/works?rows=2&query.bibliographic=Sentipolis+Emotion-Aware+Agents+for+Social+Simulations
https://api.crossref.org/works?rows=2&query.bibliographic=Mem0+Building+Production-Ready+AI+Agents+Scalable+Long-Term+Memory
```

## 5. 产物

- `paper/refs.bib`：更新后的 15 条文献（2 条补 DOI/会议、5 条补 arXiv 链接、标题大小写修正）
- `paper/main-en.tex`：英文版主文件（与 `main.tex` 同源，构建产物为 `main-en.pdf`）
- `paper/main-en.pdf`：31 页（引用蓝色、文献页见第 30 页）
- `paper/main-zh.pdf`：34 页（同一 bib，中文版同步更新）
