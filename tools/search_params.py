# -*- coding: utf-8 -*-
"""W4 · 第一次参数搜索（只搜检索参数，离线网格）

用法：
    python tools/search_params.py                # 网格搜索 + 生成报告
    python tools/search_params.py --top 15       # 看前 15 名

设计要点：
- **只搜 L1 允许的参数**（rules/learning-params.yaml#searchSpace）；
- 目标函数 `recall@3 - 0.3*budget_utilization - 0.5*false_inject_rate`（含对抗项）；
- 约束：supersede_accuracy ≥ 0.95、false_inject_rate ≤ 0.05；
- **可辨识性检查**：若某参数在整个网格上对目标函数没有影响，报告里显式标 "not identifiable"
  —— 这是最容易自欺的一步（改了参数没变化，会误以为"参数不重要"，其实是回放集测不出）。
"""
from __future__ import annotations

import argparse
import io
import itertools
import os
import sys
import time
from typing import Any

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config as cfgmod           # noqa: E402
from engine import replay as rp               # noqa: E402

# 取自 rules/learning-params.yaml#learning.searchSpace（扩大端点以便看到退化区）
GRID: dict[str, list[Any]] = {
    "decay.tau.episodic": [3.0, 7.0, 14.0],
    "decay.tau.semantic": [14.0, 30.0, 60.0],
    "retrieval.topK": [2, 3, 4, 5],
    "edge.minWeight": [0.2, 0.4, 0.6],
    "edge.diffusionDiscount": [0.6, 0.8, 0.9],
    "potency.archiveThreshold": [0.10, 0.15, 0.25],
}

W_RECALL, W_BUDGET, W_FALSE = 1.0, 0.3, 0.5
MIN_SUPERSEDE_ACC, MAX_FALSE_INJ = 0.95, 0.05
# 实用显著性阈值：小于这个量的"改进"视为噪声（不要因为 1e-5 的差就去改配置）
SIGNIFICANT = 5e-4

# 参数"为什么测不出来"的三种不同原因（不要混为一谈）
CAUSE = {
    "edge.minWeight": "fixture 无边（v1 的记忆条目未生成 edges），扩散路径从不触发",
    "edge.diffusionDiscount": "fixture 无边，扩散路径从不触发",
    "decay.tau.episodic": "族内排序由 importance 主导；last_access 只差 1 天，衰减差不足以改变名次",
    "decay.tau.semantic": "同上：语义记忆的衰减在 1 天量级上看不出差别",
    "potency.archiveThreshold": "层是**存储属性**（不是由 potency 推导），且回放不跑巩固/迁移，"
                                "归档阈值没有可观测入口",
}


def objective(m: dict[str, Any]) -> float:
    return round(m["recall_at_3"]
                 - W_BUDGET * m["budget_utilization"]
                 - W_FALSE * m["false_inject_rate"], 6)


def feasible(m: dict[str, Any]) -> bool:
    return (m["supersede_accuracy"] >= MIN_SUPERSEDE_ACC
            and m["false_inject_rate"] <= MAX_FALSE_INJ)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--top", type=int, default=10)
    ap.add_argument("--version", default="v1")
    ap.add_argument("--report", default=os.path.join(ROOT, "research", "01-参数搜索报告.md"))
    args = ap.parse_args()

    keys = sorted(GRID)
    combos = list(itertools.product(*(GRID[k] for k in keys)))
    print("grid: %d runs (%s)" % (len(combos), " × ".join(str(len(GRID[k])) for k in keys)))

    t0 = time.perf_counter()
    rows: list[dict[str, Any]] = []
    for idx, combo in enumerate(combos, 1):
        cfg = cfgmod.default()
        for k, v in zip(keys, combo):
            cfg = cfgmod.apply_path(cfg, k, v)
        m = rp.score(rp.replay(cfg, ROOT, args.version))
        rows.append({"params": dict(zip(keys, combo)), "metrics": m,
                     "objective": objective(m), "feasible": feasible(m)})
        if idx % 200 == 0:
            print("  ... %d/%d" % (idx, len(combos)))

    elapsed = time.perf_counter() - t0
    base = rp.score(rp.replay(cfgmod.default(), ROOT, args.version))
    base_obj = objective(base)

    ok = [r for r in rows if r["feasible"]]
    ok.sort(key=lambda r: (-r["objective"], str(r["params"])))
    best = ok[0] if ok else None

    # 可辨识性：某个参数在其取值上，目标函数的平均差异有多大
    ident: dict[str, dict[str, Any]] = {}
    for k in keys:
        means = []
        for v in GRID[k]:
            sel = [r["objective"] for r in rows if r["params"][k] == v]
            means.append((v, round(sum(sel) / len(sel), 6)))
        spread = round(max(m for _, m in means) - min(m for _, m in means), 6)
        ident[k] = {"by_value": means, "spread": spread,
                    "identifiable": spread > 1e-9}

    lines = []
    lines.append("# W4 · 第一次参数搜索报告\n")
    lines.append("> 由 `tools/search_params.py` 自动生成，请勿手改（数字会与工具不一致）。\n")
    lines.append("| 项 | 值 |")
    lines.append("|---|---|")
    lines.append("| 回放集 | `replay-set/%s`（%d 条 turn） |" % (args.version, base["turns"]))
    lines.append("| 网格规模 | %d 组（%s） |" % (len(combos), " × ".join(str(len(GRID[k])) for k in keys)))
    lines.append("| 耗时 | %.1f 秒 |" % elapsed)
    lines.append("| 目标函数 | `recall@3 − %.1f·budget_util − %.1f·false_inject` |"
                 % (W_BUDGET, W_FALSE))
    lines.append("| 约束 | supersede_accuracy ≥ %.2f，false_inject ≤ %.2f |"
                 % (MIN_SUPERSEDE_ACC, MAX_FALSE_INJ))
    lines.append("| 可行解 | %d / %d |" % (len(ok), len(rows)))
    lines.append("")

    lines.append("## 基线 vs 最优\n")
    lines.append("| | recall@3 | false_inj | supersede | budget_util | **objective** |")
    lines.append("|---|---|---|---|---|---|")
    lines.append("| 基线（默认参数） | %.3f | %.3f | %.3f | %.3f | **%.4f** |"
                 % (base["recall_at_3"], base["false_inject_rate"],
                    base["supersede_accuracy"], base["budget_utilization"], base_obj))
    if best and (best["objective"] - base_obj) > SIGNIFICANT:
        m = best["metrics"]
        lines.append("| 网格最优 | %.3f | %.3f | %.3f | %.3f | **%.4f** |"
                     % (m["recall_at_3"], m["false_inject_rate"], m["supersede_accuracy"],
                        m["budget_utilization"], best["objective"]))
        lines.append("")
        lines.append("最优参数：\n")
        lines.append("```yaml")
        for k in keys:
            old = cfgmod.flatten(cfgmod.default())[k]
            new = best["params"][k]
            mark = "" if old == new else "   # ← 改动"
            lines.append("%s: %s%s" % (k, new, mark))
        lines.append("```")
        lines.append("")
        lines.append("Δ objective = **%+.4f**（相对基线）。" % (best["objective"] - base_obj))
    else:
        lines.append("| 网格最优 | （与基线并列） | | | | **%.4f** |" % base_obj)
        lines.append("")
        lines.append("> **网格内没有实用意义上的改进**（最优 − 基线 = %+.6f，"
                     "低于显著性阈值 %.1e）。"
                     % ((best["objective"] - base_obj) if best else 0.0, SIGNIFICANT))
        lines.append("> 因此**不建议改动任何参数** —— 报告里出现的“最优参数”只是排序兜底产生的并列解，")
        lines.append("> 照它改没有任何收益，反而会让配置偏离已验证的默认值。")
        lines.append("> 这是回放集区分度不足的直接后果，不是“参数已经调好了”。")
    lines.append("")

    lines.append("## Top %d\n" % args.top)
    lines.append("| # | objective | recall@3 | false_inj | budget_util | " +
                 " | ".join(k.split(".")[-1] for k in keys) + " |")
    lines.append("|---|---|---|---|---|" + "---|" * len(keys))
    for i, r in enumerate(ok[:args.top], 1):
        m = r["metrics"]
        lines.append("| %d | %.4f | %.3f | %.3f | %.3f | " % (
            i, r["objective"], m["recall_at_3"], m["false_inject_rate"], m["budget_utilization"])
            + " | ".join(str(r["params"][k]) for k in keys) + " |")
    lines.append("")

    lines.append("## 可辨识性（最容易被自欺的一节）\n")
    lines.append("| 参数 | 各取值下的目标均值 | spread | 结论 |")
    lines.append("|---|---|---|---|")
    for k in keys:
        d = ident[k]
        vals = " / ".join("%s→%.4f" % (v, m) for v, m in d["by_value"])
        verdict = "可辨识" if d["identifiable"] else "**不可辨识（v1 测不出）**"
        lines.append("| `%s` | %s | %.4f | %s |" % (k, vals, d["spread"], verdict))
    lines.append("")

    unident = [k for k in keys if not ident[k]["identifiable"]]
    if unident:
        lines.append("> ⚠️ 以下参数在 v1 上**对目标函数没有任何影响**——不是“不重要”，"
                     "而是这份回放集**测不出来**：\n")
        for k in unident:
            lines.append("- `%s`：%s" % (k, CAUSE.get(k, "原因待查")))
        lines.append("")
        lines.append("**结论：这些参数不要“搜”，先去补能测它的样本。**"
                     "把“测不出来”当成“不重要”，是学习机制最容易犯的自欺。\n")

    lines.append("## 诚实声明\n")
    lines.append("- 回放集全是合成数据，本报告的\"最优\"只对 v1 成立，**不可直接上线**；")
    lines.append("- `token_ratio ≤ 0.15`（占上下文比例）无法在本回放中度量，"
                 "用的是 `budget_utilization` 作代理；")
    lines.append("- 灰度与护栏未接入，本步只产出候选，不产生任何配置变更。")

    os.makedirs(os.path.dirname(args.report), exist_ok=True)
    io.open(args.report, "w", encoding="utf-8", newline="\n").write("\n".join(lines) + "\n")
    print("\nreport:", args.report)
    if best:
        print("baseline objective=%.4f  best=%.4f  (%+.4f)"
              % (base_obj, best["objective"], best["objective"] - base_obj))
    else:
        print("no feasible solution")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
