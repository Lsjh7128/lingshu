# -*- coding: utf-8 -*-
"""校验 engine/config.py 与 rules/decay-params.yaml 是否一致（防"两份数值各自漂移"）。

为什么需要它：
    `rules/*.yaml` 是"唯一来源"，但回放器为了零依赖把默认值抄进了 engine/config.py。
    抄了就一定会漂 —— 这个脚本把"漂移"变成可执行的检查：
        python tools/check_config_sync.py        # 退出码 0/1
"""
from __future__ import annotations

import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config as cfgmod  # noqa: E402

# (yaml 中的键名, 取第几个出现, config 路径, 单位后缀)
CHECKS = [
    ("episodic", 0, "decay.tau.episodic", "d"),
    ("semantic", 0, "decay.tau.semantic", "d"),
    ("procedural", 0, "decay.tau.procedural", "d"),
    ("archiveThreshold", 0, "potency.archiveThreshold", ""),
    ("topK", 0, "retrieval.topK", ""),
    ("timeoutMs", 0, "retrieval.timeoutMs", ""),
    ("minWeight", 0, "edge.minWeight", ""),
    ("maxHop", 0, "edge.maxHop", ""),
    ("perHopLimit", 0, "edge.perHopLimit", ""),
    ("diffusionDiscount", 0, "edge.diffusionDiscount", ""),
    ("similarity", 0, "dedupe.similarity", ""),
    ("tokenBudgetDefault", 0, "recallBudget.tokenBudgetDefault", ""),
    ("maxExcludedRefs", 0, "recallBudget.maxExcludedRefs", ""),
    ("ageDays", 0, "forget.ageDays", ""),
    ("importanceMaxForHardDelete", 0, "forget.importanceMaxForHardDelete", ""),
    ("contradictionJaccard", 0, "forget.contradictionJaccard", ""),
    ("softDeleteDays", 0, "forget.softDeleteDays", ""),
    ("maxDepth", 0, "goal.maxDepth", ""),
    ("eventMaxPerTurn", 0, "goal.maxEventsPerTurn", ""),
    ("timelineMaxNodes", 0, "narrative.maxNodes", ""),
    ("modifierConflictCap", 0, "relation.conflictCap", ""),
    ("coldTurns", 0, "onboarding.coldTurns", ""),
    ("warmTurns", 0, "onboarding.warmTurns", ""),
    ("warmConfidenceFloor", 0, "onboarding.warmConfidenceFloor", ""),
    ("rateLimitPerUser", 0, "persona.rateLimitPerUser", ""),
    # v3.1 控制论参数（docs/19 §2）。这里必须用 idx 消歧：yaml 解析器是"按行收集 + 行内映射展开"，
    # 不保留层级路径，所以同名键会按出现顺序堆在一个列表里：
    #   - fact / organization / style 在 compose.domainBudgets 里先出现（idx=0），
    #     compose.weights 里才是比对目标（idx=1）；
    #   - d 在 emotion.pad.initial 的行内映射里先出现（idx=0），actr.d 是 idx=1；
    #   - retrieval 只有 memory.retrieval（该行无标量值、被解析器跳过），
    #     所以 compose.weights.retrieval 是 idx=0。
    ("actrWeight", 0, "memory.actrWeight", ""),
    ("d", 1, "actr.d", ""),
    ("ref", 0, "actr.ref", ""),
    ("lo", 0, "actr.lo", ""),
    ("hi", 0, "actr.hi", ""),
    ("integralKi", 0, "control.integralKi", ""),
    ("integralCap", 0, "control.integralCap", ""),
    ("integralLeak", 0, "control.integralLeak", ""),
    ("integralDeadband", 0, "control.integralDeadband", ""),
    ("phaseShiftRatio", 0, "control.phaseShiftRatio", ""),
    ("phaseShiftHardRatio", 0, "control.phaseShiftHardRatio", ""),
    ("fact", 1, "compose.weights.fact", ""),
    ("organization", 1, "compose.weights.organization", ""),
    ("style", 1, "compose.weights.style", ""),
    ("retrieval", 0, "compose.weights.retrieval", ""),
    ("boostFact", 0, "compose.weights.boostFact", ""),
    ("boostOrganization", 0, "compose.weights.boostOrganization", ""),
    ("boostStyle", 0, "compose.weights.boostStyle", ""),
    # v3.2：原先硬编码在 control.py / compose.py 里的工程量，收归单一来源（docs/19 §2.3/§2.4）
    ("trendThreshold", 0, "control.trendThreshold", ""),
    ("ratioMax", 0, "control.ratioMax", ""),
    ("historyCapacity", 0, "control.historyCapacity", ""),
    ("styleBoostThreshold", 0, "compose.weights.styleBoostThreshold", ""),
    # v5.3（ADR-014）：沉浸式安全的两个数值旋钮（yaml ↔ engine/config.py）
    ("softBufferMaxIntensity", 0, "control.softBufferMaxIntensity", ""),
    ("escalateAfterSoftHits", 0, "control.escalateAfterSoftHits", ""),
]


def _parse_yaml_pairs(path: str) -> dict[str, list[str]]:
    pairs: dict[str, list[str]] = {}
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^\s*([A-Za-z_][\w\-\.]*):\s*([^#]*?)\s*(?:#.*)?$", line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if not val:
            continue
        pairs.setdefault(key, []).append(val)
        # 行内映射 `{a: 1, b: 2}`：把每个键也展开成独立条目，否则这些键查不到
        if val.startswith("{") and val.endswith("}"):
            for k2, v2 in re.findall(r"([A-Za-z_][\w\-\.]*)\s*:\s*([^,}\s]+)", val):
                pairs.setdefault(k2, []).append(v2)
    return pairs


def _num(v: str) -> float | None:
    v = v.strip().strip('"').strip("'")
    m = re.match(r"^(-?\d+(?:\.\d+)?)(?:d|ms|min|h)?$", v)
    return float(m.group(1)) if m else None


def _first_scalar(yaml_pairs: dict[str, list[str]], key: str) -> float | None:
    """取某键第一次出现的数值（跨键检查用，不依赖 CHECKS 的 idx）。"""
    vals = yaml_pairs.get(key) or []
    return _num(vals[0]) if vals else None


# v4.2：控制论层移植进插件后，插件包必须**自包含**（装到 profile 后读不到 repo 的 rules），
# 于是同一组数值在插件侧出现两份"构建期副本"：
#   1) plugin/dsh-lingshu/cordis.patch.yml 的 control 段（运行时注入的配置）
#   2) plugin/dsh-lingshu/control.js 的 DEFAULT_PARAMS（config 缺省时的兜底）
# 副本漂移不会报错、只会让插件行为与规则悄悄分叉，所以这里逐键比对它们与 rules 的一致性。
# v5.3（ADR-014）：新增两个沉浸式安全的数值旋钮。插件侧由 T2 同步；未同步时
# _check_plugin_copies 会记为 skipped（不是 fail），避免在插件未改完前产生假失败。
# 布尔开关 immersiveEnabled **不做跨语言比对**：插件侧落在 DEFAULT_CONFIG（布尔），
# 本脚本的插件解析器只认数值（零依赖、不执行 JS）；ADR-014 §6 授权"显式 skip 并文档说明"。
PLUGIN_CONTROL_KEYS = ("integralKi", "integralCap", "integralLeak", "integralDeadband",
                       "phaseShiftRatio", "phaseShiftHardRatio", "trendThreshold",
                       "ratioMax", "historyCapacity",
                       "softBufferMaxIntensity", "escalateAfterSoftHits")

# 只存在于 engine/config.py（与插件 DEFAULT_CONFIG）的布尔开关：不参与数值比对。
PLUGIN_SKIP_BOOL_KEYS = ("immersiveEnabled",)


def _plugin_patch_control(path: str) -> dict[str, float]:
    """行级解析插件 cordis.patch.yml 里 `control:` 段的标量键值（零依赖，不引 yaml 库）。"""
    out: dict[str, float] = {}
    try:
        lines = open(path, encoding="utf-8").read().splitlines()
    except OSError:
        return out
    in_control = False
    ctrl_indent = -1
    for raw in lines:
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip())
        if re.match(r"^\s*control:\s*$", raw):
            in_control = True
            ctrl_indent = indent
            continue
        if in_control:
            if indent <= ctrl_indent:
                break
            m = re.match(r"^\s*([A-Za-z_]\w*):\s*(-?\d+(?:\.\d+)?)\s*(?:#.*)?$", raw)
            if m:
                out[m.group(1)] = float(m.group(2))
    return out


def _plugin_default_params(path: str) -> dict[str, float]:
    """行级解析插件 control.js 的 DEFAULT_PARAMS（不执行 node，避免把校验变成运行插件）。"""
    out: dict[str, float] = {}
    try:
        text = open(path, encoding="utf-8").read()
    except OSError:
        return out
    m = re.search(r"export const DEFAULT_PARAMS = \{([\s\S]*?)\n\};", text)
    if not m:
        return out
    for km in re.finditer(r"^\s*([A-Za-z_]\w*):\s*(-?\d+(?:\.\d+)?)\s*,", m.group(1), re.M):
        out[km.group(1)] = float(km.group(2))
    return out


def _check_plugin_copies(yaml_pairs: dict[str, list[str]]) -> tuple[int, list[str], list[str]]:
    """比对插件侧两份副本与 rules。返回 (ok_count, fails, skipped)。"""
    plugin_dir = os.path.join(ROOT, "plugin", "dsh-lingshu")
    sources = [
        ("plugin/cordis.patch.yml#control",
         _plugin_patch_control(os.path.join(plugin_dir, "cordis.patch.yml"))),
        ("plugin/control.js#DEFAULT_PARAMS",
         _plugin_default_params(os.path.join(plugin_dir, "control.js"))),
    ]
    ok, fails, skipped = 0, [], []
    for label, got in sources:
        for key in PLUGIN_CONTROL_KEYS:
            want = _first_scalar(yaml_pairs, key)
            have = got.get(key)
            if want is None or have is None:
                skipped.append("%s.%s（有一侧缺失/不可解析）" % (label, key))
                continue
            if abs(float(want) - float(have)) < 1e-9:
                print("  ok   %-40s = %s" % (label + "." + key, want))
                ok += 1
            else:
                fails.append("%s.%s: rules=%s plugin=%s" % (label, key, want, have))
                print("  FAIL %-40s rules=%s plugin=%s" % (label + "." + key, want, have))
    return ok, fails, skipped


def _duplicate_paths(path: str) -> list[str]:
    """v3.2：检测同一层级路径的重复键（历史踩过 `memory.forget:` 重复两次）。

    本脚本为了零依赖按行解析 YAML，**不做真正的 YAML 语义**；而真实 YAML 解析器遇到
    重复键会"后者覆盖前者"，等于前面的值被静默丢弃。这类错误不会让任何断言失败，
    只会让规则文件与实际生效值不一致，所以必须单独查。
    """
    stack: list[tuple[int, str]] = []
    seen: dict[str, int] = {}
    dups: list[str] = []
    for i, raw in enumerate(open(path, encoding="utf-8"), 1):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        m = re.match(r"^(\s*)([A-Za-z_][\w\-\.]*):", line)
        if not m:
            continue
        indent, key = len(m.group(1)), m.group(2)
        while stack and stack[-1][0] >= indent:
            stack.pop()
        full = ".".join([k for _, k in stack] + [key])
        if full in seen:
            dups.append("%s（第 %d 行与第 %d 行）" % (full, seen[full], i))
        else:
            seen[full] = i
        stack.append((indent, key))
    return dups


def main() -> int:
    yaml_pairs = _parse_yaml_pairs(os.path.join(ROOT, "rules", "decay-params.yaml"))
    flat = cfgmod.flatten(cfgmod.default())

    fails, skipped = [], []
    ok = 0
    # v3.2：重复键检查（真实 YAML 后者覆盖前者 → 前半段规则静默失效，断言不会失败）
    yaml_path = os.path.join(ROOT, "rules", "decay-params.yaml")
    for dup in _duplicate_paths(yaml_path):
        fails.append("重复键: " + dup)
        print("  FAIL %-30s %s" % ("yaml 重复键", dup))
    for yaml_key, idx, cfg_path, _unit in CHECKS:
        vals = yaml_pairs.get(yaml_key)
        if not vals or idx >= len(vals):
            skipped.append(f"{yaml_key} → {cfg_path}（yaml 中未找到）")
            continue
        want = _num(vals[idx])
        got = flat.get(cfg_path)
        if want is None:
            skipped.append(f"{yaml_key} → {cfg_path}（值不可解析：{vals[idx]}）")
            continue
        if isinstance(got, (int, float)) and abs(float(got) - want) < 1e-9:
            print("  ok   %-30s = %s" % (cfg_path, want))
            ok += 1
        else:
            fails.append("%s: yaml=%s  config=%s" % (cfg_path, want, got))
            print("  FAIL %-30s yaml=%s config=%s" % (cfg_path, want, got))

    # 跨键不变量（v3.1 / docs/19 §2）：召回名额只能有一个来源 —— memory.retrieval.topK。
    # recallBudget.maxReturned 是历史遗留的第二个出处；两者一旦分叉，就会重演 v4.0
    # "声明返回 N 条、实际按 topK 返回"的回归，所以单列一条跨键检查（yaml 与 config 两侧都比）。
    cross_label = "recallBudget.maxReturned ↔ retrieval.topK"
    mr_yaml = _first_scalar(yaml_pairs, "maxReturned")
    tk_yaml = _first_scalar(yaml_pairs, "topK")
    mr_cfg = flat.get("recallBudget.maxReturned")
    tk_cfg = flat.get("retrieval.topK")
    cross_bad = []
    if mr_yaml is None or tk_yaml is None:
        cross_bad.append("yaml 中 recallBudget.maxReturned 或 retrieval.topK 缺失/不可解析")
    elif abs(mr_yaml - tk_yaml) > 1e-9:
        cross_bad.append("yaml: maxReturned=%s != retrieval.topK=%s" % (mr_yaml, tk_yaml))
    if not (isinstance(mr_cfg, (int, float)) and isinstance(tk_cfg, (int, float))
            and abs(float(mr_cfg) - float(tk_cfg)) < 1e-9):
        cross_bad.append("config: maxReturned=%s != retrieval.topK=%s" % (mr_cfg, tk_cfg))
    if cross_bad:
        fails.append("%s: %s" % (cross_label, "；".join(cross_bad)))
        print("  FAIL %-30s %s" % (cross_label, "；".join(cross_bad)))
    else:
        print("  ok   %-30s = %s" % (cross_label, mr_yaml))
        ok += 1

    # v4.2：插件侧副本（cordis.patch.yml 的 control 段 / control.js 的 DEFAULT_PARAMS）
    # ADR-014 §6：布尔开关 immersiveEnabled 显式 skip（插件侧在 index.js DEFAULT_CONFIG，
    # 本脚本零依赖只做数值行解析；这里把"不比对"本身打印出来，避免误以为漏检）。
    for key in PLUGIN_SKIP_BOOL_KEYS:
        if ("control." + key) in flat:
            note = ("control.%s（布尔开关：插件侧在 index.js DEFAULT_CONFIG，"
                    "ADR-014 §6 授权显式 skip，不做跨语言数值比对）" % key)
            skipped.append(note)
            print("  skip %-40s %s" % ("control." + key, "bool（ADR-014 §6 授权）"))
    p_ok, p_fails, p_skipped = _check_plugin_copies(yaml_pairs)
    ok += p_ok
    fails.extend(p_fails)
    skipped.extend(p_skipped)

    print("\n%d ok, %d fail, %d skipped" % (ok, len(fails), len(skipped)))
    for s in skipped:
        print("  skip:", s)
    return 0 if not fails else 1


if __name__ == "__main__":
    raise SystemExit(main())
