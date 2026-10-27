# -*- coding: utf-8 -*-
"""tools/chaos_v2/ —— LINGSHU 混沌工程 v2 执行器核心包（A5）

本包实现 `docs/23-混沌工程v2-科学压测设计.md` 的**执行与判定层**，与 v1
（`docs/22` + `tools/run_chaos.py`，逐字节保留、不导入也不改）并列存在：

    corpus.py      manifest / 8 份刺激集加载、行封套校验、SHA-256 指纹（§5 / §10）
    stats.py       Wilson 区间、指标 digest、R 次结果聚合规则（§6.1–§6.4）
    invariants.py  INV-01…INV-20 目录 + 全局不变量检查器（§4）
    report.py      中文 markdown 报告渲染（族表 / 不变量矩阵 / 覆盖率 / 诚实边界）
    families/      8 个族模块（**不含**在枚举里：runner 用 importlib 按 manifest.module
                   动态导入，见 docs/23 §10 的产物清单）

设计约束（来自 docs/23）：

- v2 **零模型调用**、**不写真实记忆库**、**不碰 rules/ plugin/ desktop**；
  唯一副作用是 `acceptance/chaos/v2/` 下的**文件**（不建临时目录/子目录；
  受限沙箱下 Python 不能新建目录，F6 的 node 侧自己在 `os.tmpdir()` 里建删临时目录）。
- v2 的产物只有三种：不变量违反、机制画像、候选边界样本；**不产 measured**，
  **不宣布阈值正确**，**不把合成压力当真实证据**（§0 / §8.5）。
- 任何跑不成的情况都写 `skipped=true` + `skip_reason` + `anomalies[]`，
  绝不用 0 / 空顶替（§6.7）。

本文件刻意只放包级元信息，不 import 子模块：runner 的启动开销与
import 顺序要可控（尤其是 `sys.dont_write_bytecode` 必须在导入前生效，
以免 `__pycache__` 破坏 INV-19 零仓库污染判定）。
"""
from __future__ import annotations

__version__ = "2.0"
SCHEMA_ID = "lingshu-chaos-campaign/2"
MODE = "engine"
EXIT_PASS = 0
EXIT_FAIL = 2
EXIT_USAGE = 1
EXIT_CODES = {
    EXIT_PASS: "全部不变量通过",
    EXIT_FAIL: "有不变量失败 / 异常 / finding",
    EXIT_USAGE: "用法或语料结构性错误（manifest 缺失 / schema 不符）",
}

__all__ = ["__version__", "SCHEMA_ID", "MODE", "EXIT_PASS", "EXIT_FAIL", "EXIT_USAGE",
           "EXIT_CODES"]
