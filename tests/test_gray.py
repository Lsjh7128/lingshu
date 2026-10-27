# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 灰度与护栏（W5）

    python tests/test_gray.py

覆盖：
- 分桶确定性（同一会话固定在同一侧）
- 缺数据处理：硬红线缺失 → 阻断；质量/资源线缺失 → 跳过（不通过也不阻断）
- G5 硬红线 1 窗口即回滚
- G7 质量线需连续 2 窗口才回滚
- G4 资源线只做局部降级，不回滚
- 样本不足不推进
- 全绿 → 逐档推进 → promoted
- 回滚后 assign 一律回 baseline
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import gray            # noqa: E402
from engine import replay as rp    # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + detail) if detail else ""))


GOOD = {"supersede_error_rate": 0.0, "user_correction_rate": 0.05, "token_ratio": 0.10,
        "latency_p95_ms": 120.0, "hallucination_on_no_hit": 0.0,
        "refusal_regression_pass": 1.0, "recall_delta": 0.0}


def test_bucket() -> None:
    print("\n[分桶] 确定性")
    a = [gray.bucket("s_001", 0.1) for _ in range(5)]
    check("同一会话多次分桶结果相同", len(set(a)) == 1, str(a[0]))
    check("ratio=0 → 全部 baseline",
          all(gray.bucket(f"s_{i}", 0.0) == "baseline" for i in range(50)))
    check("ratio=1 → 全部 candidate",
          all(gray.bucket(f"s_{i}", 1.0) == "candidate" for i in range(50)))
    r01 = sum(1 for i in range(400) if gray.bucket(f"x{i}", 0.1) == "candidate")
    # 审计修复（A5-T4）：原为宽松区间 20..60；分桶是确定性哈希，400 个固定输入下
    # 命中数应精确等于 45（≈11.25%，与 ratio=0.1 的 10% 偏差在确定性样本内）。
    check("ratio=0.1 确定性样本命中数精确 = 45/400", r01 == 45, "%d/400" % r01)


def test_missing() -> None:
    print("\n[缺数据] 不得当成通过")
    m = dict(GOOD)
    del m["hallucination_on_no_hit"]
    r = gray.check(m, strict=False)
    check("G5 缺数据 → 阻断（fail-closed）", r["blocked"], str(r["missing"]))
    r2 = gray.check({"hallucination_on_no_hit": 0.0, "refusal_regression_pass": 1.0},
                    strict=False)
    check("质量线缺数据 → 跳过而非阻断",
          ("G2" in r2["missing"]) and (not r2["blocked"]), str(r2["missing"]))
    r3 = gray.check({"hallucination_on_no_hit": 0.0, "refusal_regression_pass": 1.0},
                    strict=True)
    check("严格模式下任何缺失都阻断", r3["blocked"])


def test_hard_gate() -> None:
    print("\n[G5] 硬红线立即回滚")
    ro = gray.Rollout("v1", "v2", min_samples=10)
    m = dict(GOOD, hallucination_on_no_hit=0.05)
    ro.observe(m, samples=10)
    check("单窗口即回滚", ro.state == "rolled_back", ro.state)
    check("回滚后 ratio=0", ro.ratio == 0.0)
    check("assign 一律 baseline", ro.assign("s_001") == "baseline")


def test_quality_two_windows() -> None:
    print("\n[G7] 质量线需连续 2 窗口")
    ro = gray.Rollout("v1", "v2", min_samples=10)
    ro.observe(dict(GOOD, recall_delta=-0.20), samples=10)
    check("第 1 窗口不回滚", ro.state == "running", ro.state)
    ro.observe(dict(GOOD, recall_delta=-0.20), samples=10)
    check("第 2 窗口回滚", ro.state == "rolled_back", ro.state)
    check("回滚原因含 quality_regression", "quality_regression:G7" in
          [e.get("reason") for e in ro.events if e["event"] == "learn/rollback"][0])

    ro2 = gray.Rollout("v1", "v2", min_samples=10)
    ro2.observe(dict(GOOD, recall_delta=-0.20), samples=10)
    ro2.observe(dict(GOOD, recall_delta=0.05), samples=10)   # 中间恢复 → 计数清零
    check("中间恢复会清零连续计数", ro2.state == "running", ro2.state)


def test_resource_gate() -> None:
    print("\n[G4] 资源线只降级不回滚")
    ro = gray.Rollout("v1", "v2", min_samples=10)
    ro.observe(dict(GOOD, latency_p95_ms=999.0), samples=10)
    check("不回滚", ro.state == "running", ro.state)
    check("记录了 degrade 事件",
          any(e["event"] == "learn/degrade" and e.get("guardrail") == "G4"
              for e in ro.events))


def test_samples_gate() -> None:
    print("\n[样本] 不足不推进")
    ro = gray.Rollout("v1", "v2", min_samples=100)
    ro.observe(GOOD, samples=30)
    check("未达 min_samples 不推进", ro.stage == 0 and ro.state == "running")
    ro.observe(GOOD, samples=30)
    ro.observe(GOOD, samples=30)
    check("累计不足仍不推进", ro.stage == 0)
    ro.observe(GOOD, samples=30)
    check("累计达标后推进到下一档", ro.stage == 1, "stage=%d" % ro.stage)


def test_promote() -> None:
    print("\n[推进] 全绿到达 100%")
    ro = gray.Rollout("v1", "v2", min_samples=10)
    for _ in range(6):
        if ro.state != "running":
            break
        ro.observe(GOOD, samples=10)
    check("最终 promoted", ro.state == "promoted", ro.state)
    check("经历 3 档", ro.stage == 3, "stage=%d" % ro.stage)
    check("promoted 后 assign 回 baseline（不残留灰度）",
          ro.assign("s_001") == "baseline")


def test_metrics_from_report() -> None:
    print("\n[指标提取] 缺数据返回 None 而不是 0")
    rep = rp.replay(__import__("engine").config.default(), ROOT)
    m = gray.metrics_from_report(rep)
    check("无用户纠正数据 → None", m["user_correction_rate"] is None)
    check("无延迟数据 → None", m["latency_p95_ms"] is None)
    check("no_hit 族未编造 → 0.0", m["hallucination_on_no_hit"] == 0.0,
          str(m["hallucination_on_no_hit"]))
    check("supersede 错误率可测且为 0", m["supersede_error_rate"] == 0.0)
    check("未给 baseline_recall 时 recall_delta 为 None", m["recall_delta"] is None)


def main() -> int:
    print("LINGSHU 验收 · 灰度与护栏（W5）")
    test_bucket()
    test_missing()
    test_hard_gate()
    test_quality_two_windows()
    test_resource_gate()
    test_samples_gate()
    test_promote()
    test_metrics_from_report()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
