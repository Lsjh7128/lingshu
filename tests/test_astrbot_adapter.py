# -*- coding: utf-8 -*-
"""LINGSHU 验收 · AstrBot 适配器（docs/18）

    python tests/test_astrbot_adapter.py

覆盖（都不需要装 AstrBot，验证的是"缺宿主时也不炸"）：
- 无 AstrBot 时能导入（优雅降级）
- 冷启动期只注入宪法段
- 每会话状态隔离（QQ 群并发不能串）
- 钩子里的异常被吞掉（禁止挡用户消息）
- 关系/情感只进风格域，事实段不带情绪字样
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "integrations", "astrbot"))

import main as A  # noqa: E402

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail else ""))


def main() -> int:
    print("LINGSHU 验收 · AstrBot 适配器")

    cfg = A.cfgmod.default()
    # 审计修复（A5-T1）：原为 check(..., True) 恒真断言；改为检查"缺宿主时优雅降级"的
    # 具体契约——宿主标志为 False 且核心入口仍可调用（导入成功已由文件头 import 保证）。
    check("无 AstrBot 时仍可导入且入口可调用",
          A._HAS_ASTRBOT is False and callable(A.build_prompt_sections)
          and callable(A._session_state))
    check("检测到未安装宿主", A._HAS_ASTRBOT is False)

    # 冷启动：只注入宪法段
    A._SESSIONS.clear()
    out = A.build_prompt_sections("开始做 记忆验收", "s1", cfg)
    check("冷启动只注入宪法段", "宪法" in out and "认知状态" not in out, out[:30])
    check("宪法段要求记忆不等于当前陈述", "记忆不等于当前陈述" in out)

    # 会话隔离：两个 session 各自独立
    A._SESSIONS.clear()
    A._session_state("qq:group:1")["ob"].tick()
    A._session_state("qq:group:1")["ob"].tick()
    A._session_state("qq:group:1")["ob"].tick()
    check("群 1 已进入 warm", A._session_state("qq:group:1")["ob"].stage == "warm",
          A._session_state("qq:group:1")["ob"].stage)
    check("群 2 仍在 cold（状态隔离）", A._session_state("qq:group:2")["ob"].stage == "cold",
          A._session_state("qq:group:2")["ob"].stage)
    check("两个会话不是同一对象",
          A._session_state("qq:group:1") is not A._session_state("qq:group:2"))

    # warm 期：注入认知状态，且无记录时禁止编造
    A._session_state("qq:group:1")["last"] = {"matched_count": 0, "returned": []}
    out2 = A.build_prompt_sections("项目代号是什么", "qq:group:1", cfg)
    check("warm 期注入认知状态", "认知状态" in out2, out2[:40])
    check("无记录时明确禁止编造", "不得编造" in out2)

    # 事实域不带情绪字样
    A._session_state("qq:group:1")["active"] = True
    out3 = A.build_prompt_sections("谢谢", "qq:group:1", cfg)
    check("风格段标注'仅影响语气'", "仅影响语气" in out3 or "表达状态" in out3, out3[:40])
    check("风格段不参与事实", "不改变事实" in out3)

    # 异常被吞：传入畸形入参也不能抛
    threw = None
    try:
        A.build_prompt_sections(None, None, {"broken": True})   # type: ignore[arg-type]
    except Exception as exc:                                     # pragma: no cover
        threw = exc
    check("畸形入参不抛异常（红线）", threw is None, str(threw))

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
