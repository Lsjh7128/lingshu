# -*- coding: utf-8 -*-
"""A4 变异测试 · 补充回归断言（2026-10-27 新增，不依赖 pytest）

背景：对 engine/control.py 植入 11 个语义变异、对 rules/decay-params.yaml 植入 4 个
配置值变异后，行为断言全部由 tests/test_control.py / tests/test_chaos_calibrate.py 杀死；
但存在一个**结构性盲区**：控制论数值的"单一来源"（rules/decay-params.yaml ↔
engine/config.py ↔ plugin/dsh-lingshu/control.js）在测试里只被 test_chaos_calibrate
的 fixture 顺带覆盖，没有一个测试**直接**钉住这份三方差值一致。

本文件用最小断言把这个不变式显式化：任一份配置副本单独漂移都必须 FAIL。

用法：python tests/test_mutation_regressions.py   （退出码 0 = 全通过，1 = 有失败）
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config as cfgmod  # noqa: E402

RULES = os.path.join(ROOT, "rules", "decay-params.yaml")
PLUGIN_CTL = os.path.join(ROOT, "plugin", "dsh-lingshu", "control.js")

# control 段的全部数值键（与 tools/check_config_sync.py 的口径一致）
KEYS = ["integralKi", "integralCap", "integralLeak", "integralDeadband",
        "phaseShiftRatio", "phaseShiftHardRatio", "trendThreshold",
        "ratioMax", "historyCapacity"]

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail else ""))


def _read(path: str) -> str:
    with open(path, "r", encoding="utf-8") as fh:
        return fh.read()


def yaml_control(path: str) -> dict[str, float]:
    """从 rules/decay-params.yaml 的 control 段取标量（不依赖 PyYAML，按测试口径手解析）。"""
    out: dict[str, float] = {}
    in_control = False
    for line in _read(path).splitlines():
        if re.match(r"^control:\s*(#.*)?$", line):
            in_control = True
            continue
        if in_control and line and not line[0].isspace():
            break                      # 离开 control 段
        if not in_control:
            continue
        m = re.match(r"^\s+([A-Za-z][A-Za-z0-9]*):\s*([-\d.]+)\s*(#.*)?$", line)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def js_default_params(path: str) -> dict[str, float]:
    """从 plugin/dsh-lingshu/control.js 的 DEFAULT_PARAMS 字面量取数值（插件侧副本）。"""
    text = _read(path)
    m = re.search(r"export const DEFAULT_PARAMS = \{(.*?)\n\};", text, re.S)
    if not m:
        raise AssertionError("control.js 里找不到 DEFAULT_PARAMS 字面量")
    out: dict[str, float] = {}
    for km in re.finditer(r"^\s*([A-Za-z][A-Za-z0-9]*):\s*([-\d.]+)\s*,", m.group(1), re.M):
        out[km.group(1)] = float(km.group(2))
    return out


def main() -> int:
    print("[MUT-REG] 控制论数值的单一来源：rules YAML ↔ engine/config.py ↔ plugin control.js")
    yaml_c = yaml_control(RULES)
    cfg_c = cfgmod.default()["control"]
    js_c = js_default_params(PLUGIN_CTL)

    missing = [k for k in KEYS if k not in yaml_c or k not in cfg_c or k not in js_c]
    check("control 段 9 个数值键在三处都齐全", not missing, str(missing))
    for k in KEYS:
        if missing and k not in (yaml_c.keys() & cfg_c.keys() & js_c.keys()):
            continue
        check("YAML == engine/config.py：%s" % k,
              abs(float(yaml_c[k]) - float(cfg_c[k])) < 1e-12,
              "yaml=%s config=%s" % (yaml_c.get(k), cfg_c.get(k)))
        check("engine/config.py == plugin/control.js：%s" % k,
              abs(float(cfg_c[k]) - float(js_c[k])) < 1e-12,
              "config=%s plugin=%s" % (cfg_c.get(k), js_c.get(k)))

    # 相变双阈值必须 shift < hard，且都在开区间 (0,1)：配置值本身退化成无意义区间也要挡住
    check("phaseShiftRatio < phaseShiftHardRatio",
          yaml_c.get("phaseShiftRatio", 1.0) < yaml_c.get("phaseShiftHardRatio", 0.0),
          "%s / %s" % (yaml_c.get("phaseShiftRatio"), yaml_c.get("phaseShiftHardRatio")))
    check("双阈值都在 (0,1) 开区间",
          all(0.0 < yaml_c[k] < 1.0 for k in ("phaseShiftRatio", "phaseShiftHardRatio")),
          str({k: yaml_c.get(k) for k in ("phaseShiftRatio", "phaseShiftHardRatio")}))

    # IntegralBaseline 的 Ki/cap/leak/deadband 必须为正（否则积分控制语义反了）
    check("积分参数均为正数",
          all(yaml_c[k] > 0 for k in ("integralKi", "integralCap", "integralLeak", "integralDeadband")),
          str({k: yaml_c.get(k) for k in ("integralKi", "integralCap", "integralLeak", "integralDeadband")}))

    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
