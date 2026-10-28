# README · Phase C 区块（可直接粘贴）

> 为什么单独放一个文件：本仓库工作区里 `README.md` 还有**另一条并行工作流尚未提交的改动**
> （badge 计数 1395→1460、安装说明等，约 117 行）。为了不把别人的未提交内容混进 Phase C 的提交，
> Lead 已把该区块**直接写进工作区的 `README.md` 末尾**（磁盘上可见），但**没有纳入 git 提交**。
> H0 决定合并时，二选一即可：
> 1. 直接采用工作区已改好的 `README.md`（推荐，若那条工作流的改动也要一起提交）；
> 2. 或把下面 `===== 粘贴开始 =====` 到 `===== 粘贴结束 =====` 之间的内容追加到目标 README 末尾。

===== 粘贴开始 =====

## 十一、防御演化实验数据（Phase A / B / C）

> **状态**：Phase B/C 在分支 `feat/phase-c-semantic-guard` 上（**尚未合并 `main`**）。
> 下表为 **held-out 变异集 E1（n=40）** 实测，可用 `experiments/phase-c/results/run-semantic.json` 复现；
> 完整口径见 [`acceptance/phase-c/复盘-2026-10-28-phase-c.md`](acceptance/phase-c/复盘-2026-10-28-phase-c.md)。

| Defense Stage | Zero-day Interception | Latency (p95) | Core Mechanism |
|---|---|---|---|
| Phase A (Lexical) | 37.5% | 0.02 ms | Regex / keyword gate |
| Phase B (Co-evolution) | 77.5% | 0.25 ms | Auto-patching (**plateaued** at gen 1) |
| **Phase C (Concept Intent)** | **85.0%** | **1.2–1.3 ms** | Intent-concept encoder + meta-controller |
| Phase C (Neural ablation) | 77.5% | 11.9 ms | MiniLM ONNX (rejected: lower recall + ~9× latency) |

**读表必须知道的四件事**（防止过度解读）：

1. **不是"越往后越强"**：Phase C 的 85.0% 是在同样的 40 条 held-out 上测的；另有一份新 seed 的独立实现集 E0（40 条）：
   A 40.0% / B 82.5% / **C 92.5%** / neural 85.0%。
2. **神经消融被否掉的原因是"更差"，不是"误伤"**：在"零新增良性误伤"约束下，神经后端 D_dev 召回 **28.9%**，
   概念层 **68.2%**；两者的良性误伤都是 2/74（未上升）。逐条 2×2 核对显示 **神经层对系统 C 的增量 = 0 条**
   （唯一多拦的那条已被 Phase B 的解码器拦掉）。详见
   [`neural_vs_concept_ablation.json`](experiments/phase-c/analysis/neural_vs_concept_ablation.json)（`KEY_FINDING`）。
3. **口径**：E1/E0 是 held-out **变异**样本，**不是 OOD**；延迟为单机 CPU 实测（无并发、无网络），
   完整判定 p95；语义层单独计价为 concept 1.78 ms / neural 8.49 ms。零外部 API（模型本地推理）。
4. **未达成项照实写**：H0 目标"E1 > 90%"**未达到**（0.850，剔除 2 条本不该拦的良性创作后为 0.895）；
   mild 角色扮演类 4 条仍漏拦（其分数与良性创作重叠，任何阈值都无法在不误伤的前提下分开）。

===== 粘贴结束 =====

## 与 H0 原表的差异（已按实测修正，勿改回）

| 项 | H0 原稿 | 实测/修正 |
|---|---|---|
| 神经消融被否原因 | "Rejected due to FP & Latency" | **不是 FP**：新增误伤 0；否掉是因为**召回更低（28.9% vs 68.2%）+ 延迟更高** |
| 神经延迟 | 14.8 ms | 14.8 是 **E0 的完整判定 p95**；E1 为 11.9 ms。语义层单独 p95 = 8.49 ms（README 用 E1 口径） |
| Phase C 延迟 | 1.3 ms | 完整判定 p95 1.2–1.3 ms；语义层单独 p95 1.78 ms（两个口径都写出来） |
| E1 目标达成 | 表里未标注 | 明确写"未达到 >90%"（0.850 / 应拦口径 0.895） |
