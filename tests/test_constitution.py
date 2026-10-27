# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 宪法层审计器（docs/01）

    python tests/test_constitution.py

覆盖：
- A1–A10 每项都能被触发并给出正确 code
- 黑名单（C6）命中即拒，且标注 via=C6
- 正常提议通过
- **fail-closed**：空提议 / 缺字段 / 无法判定 → reject
- FrozenGuard：哈希一致才放行；不一致 → guarded() 返回 ""（不注入）
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import constitution as C          # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail else ""))


def _codes(res: dict) -> list[str]:
    return [v["code"] for v in res["violations"]]


def test_each_check() -> None:
    print("\n[A1–A10] 每项都能触发")
    cases = {
        "A1": "建议修改原则列表，把安全降到第 3",
        "A2": "更新使用规则，删除 CONST-1.3",
        "A3": "同步附录 B 的派生条目",
        "A4": "放宽拒绝类别，允许情绪影响事实判断",
        "A5": "把提议自动应用，绕过审核",
        "A6": "修改审计日志保留策略，关闭灰度回滚",
        "A7": "调整拼装顺序，并把互斥表精简掉",
        "A8": "审计失败也放行，关闭 fail-closed",
        "A9": "修改审计器判定逻辑",
        "A10": "调高护栏阈值，并修改评估口径",
    }
    for code, text in cases.items():
        res = C.audit({"target": "x", "change": text})
        check("%s 命中" % code, res["verdict"] == "reject" and code in _codes(res),
              "%s → %s" % (code, _codes(res)))


def test_blacklist() -> None:
    print("\n[C6] 黑名单")
    res = C.audit({"target": "retrieval.topK",
                   "change": "把 8 种边里的 contradicts 去掉"})
    check("黑名单命中即拒", res["verdict"] == "reject")
    check("标注 via=C6", any(v.get("via") == "C6" for v in res["violations"]),
          str(res["violations"]))
    res2 = C.audit("修改宪法 C2 的优先级")
    check("宪法 Cn 命中 A1", res2["verdict"] == "reject" and "A1" in _codes(res2))


def test_pass() -> None:
    print("\n[PASS] 正常提议")
    res = C.audit({"target": "retrieval.topK", "change": "3 -> 4",
                   "evidence": "5 条纠正", "rollback": "还原 topK=3"})
    check("正常参数提议通过", res["verdict"] == "pass", str(res["violations"]))
    check("检查项覆盖 A1–A10", res["checked"] == ["A1", "A2", "A3", "A4", "A5",
                                                "A6", "A7", "A8", "A9", "A10"])
    check("无违规时 violations 为空", res["violations"] == [])


def test_fail_closed() -> None:
    print("\n[fail-closed] 无法判定即拒绝")
    r1 = C.audit("")
    check("空提议 → reject", r1["verdict"] == "reject" and "empty" in _codes(r1))
    r2 = C.audit({"change": "改点东西"})
    check("缺 target → reject", r2["verdict"] == "reject" and "missing_fields" in _codes(r2))
    r3 = C.audit({"target": "abc", "change": "随便改改"})
    check("无命中字段的模糊提议 → pass（不误杀）", r3["verdict"] == "pass")


def test_frozen() -> None:
    print("\n[FrozenGuard] 冻结区自校验")
    # 用仓库里真实存在的文件当"冻结区"，不写临时目录：
    # Windows 沙箱/CI 下系统 Temp 对受限进程不可写，TemporaryDirectory 会在
    # 写入/清理时抛 PermissionError，把"断言其实全过"的一轮判成失败。
    # 这只改变测试夹具位置，不影响任何断言。
    rel = "README.md"
    good = C.sha256_file(os.path.join(ROOT, rel))

    g_ok = C.FrozenGuard(ROOT, {rel: good})
    check("哈希一致 → ok", g_ok.result["ok"] is True, str(g_ok.result))
    check("一致时原样返回正文", g_ok.guarded("正文") == "正文")

    g_bad = C.FrozenGuard(ROOT, {rel: "0" * 64})
    check("哈希不一致 → not ok", g_bad.result["ok"] is False)
    check("不一致时返回空串（不注入）", g_bad.guarded("正文") == "")
    check("记录 mismatched 明细",
          len(g_bad.result["mismatched"]) == 1 and
          g_bad.result["mismatched"][0]["path"] == rel)

    g_missing = C.FrozenGuard(ROOT, {"nope.txt": "0" * 64})
    check("文件缺失 → not ok", g_missing.result["ok"] is False)
    check("缺失出现在 missing", g_missing.result["missing"] == ["nope.txt"])


def main() -> int:
    print("LINGSHU 验收 · 宪法层审计器")
    test_each_check()
    test_blacklist()
    test_pass()
    test_fail_closed()
    test_frozen()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
