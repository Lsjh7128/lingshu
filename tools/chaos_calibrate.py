# -*- coding: utf-8 -*-
"""LINGSHU 混沌压测 · 相变双阈值联动标定（A2）

把 `acceptance/chaos/*.json`（A5 混沌执行器产物）里**真实 host 模式**的退化证据，
汇总成对 `control.phaseShiftRatio` + `control.phaseShiftHardRatio` 的**联动**标定建议；
默认只出报告 + 候选，`--apply` 才改配置。

    python tools/chaos_calibrate.py                      # 只出报告/候选（默认）
    python tools/chaos_calibrate.py --apply              # 证据达标才改配置（8 值同步 + 自检）
    python tools/chaos_calibrate.py --repo <副本仓库> --chaos-dir <目录> --date 2026-10-27

默认报告另含 §5.1「辅助观察」：只读 run JSON 顶层 `quality_labels`（插件 `quality_label`
规则自动标签的汇总），**不参与**任何门槛/候选判定（规则自动标签 ≠ 人工结果标签）。

────────────────────────────────────────────────────────────────────────────
标定规则（H0 裁决 B：双阈值联动；见 decisions/011-相变阈值联动规则.md）
────────────────────────────────────────────────────────────────────────────
只认 **mode == "host"** 的运行；engine 模式永远不参与（engine 没有真实模型回答，
它的 degraded_at_ratio 若非空属于 schema 违规，按协议忽略并记警告）。

一次"可判定退化"必须同时满足：
  · 该 run 的 `long_context_flood.key_question.keyword_hit === false`；
  · 该 run 的 `long_context_flood.degraded_at_ratio` 是数字（退化发生时的 ratio）；
  · 该 ratio **严格小于当前** `phaseShiftRatio`（否则阈值本来就在退化点之下，没有下调依据）。
一次"对照"：host 模式下 `keyword_hit === true`（同场景有关键词命中 = 未退化）。

跨运行累计 `degraded >= 3` 且 `对照 >= 3` 才允许产出候选；联动公式：

    new_shift = max(0.40, floor((min(degraded_at_ratio) - 0.05) * 100) / 100)
    new_hard  = min(0.95, round(new_shift + 0.15, 2))

即 **hard 随 shift 移动、保留原 0.15 间距宽度**（不是保留 hard 绝对值），hard 上限 0.95。
`floor(…*100)/100` 是"下调到退化点前 5 个百分点、按百分位向下取整"；0.40 是 shift 绝对下限。
若 new_shift 不低于当前 shift，则**没有可执行的下调**，不产出 applied 候选（报告写明原因）。

证据不足时：**零修改**，报告写"证据不足，保持 guess"，候选 JSON 里 refused 记录原因。

`--apply` 的**8 值同步**（4 文件 × shift/hard；缺一处就会让 `tools/check_config_sync.py` 报漂移）：
  1. rules/decay-params.yaml#control.phaseShiftRatio / #control.phaseShiftHardRatio
     （行内注释 provenance: measured + 依据）
  2. engine/config.py DEFAULTS["control"]["phaseShiftRatio"/"phaseShiftHardRatio"]
  3. plugin/dsh-lingshu/cordis.patch.yml（config.control.phaseShiftRatio / phaseShiftHardRatio）
  4. plugin/dsh-lingshu/control.js#DEFAULT_PARAMS.phaseShiftRatio / phaseShiftHardRatio
改完自动跑 `python tools/check_config_sync.py`；失败 → 8 值全部回滚 → exit 2。
成功 → 写 `decisions/009-相变阈值真实标定.md` + 向 `assumptions/已验证.md` 追加 E-M1（记录两个参数）。

退出码：0 = 分析完成（证据不足也是 0；apply 成功也是 0）；2 = apply 被拒绝/回滚/配置不同源。
"""

from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import math
import os
import re
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 规则自动标签的闭集：**单一来源**为 tools/analyze_shadow.py（避免两份闭集漂移）。
# 本工具只读 run JSON 顶层的 `quality_labels` 辅助汇总（插件 audit 的 quality_label 事件），
# 仅作报告展示，**不参与**任何门槛/候选判定。
_TOOLS_DIR = os.path.join(ROOT, "tools")
if _TOOLS_DIR not in sys.path:
    sys.path.insert(0, _TOOLS_DIR)
from analyze_shadow import QUALITY_LABEL_KINDS   # noqa: E402

PARAM = "control.phaseShiftRatio"
HARD_PARAM = "control.phaseShiftHardRatio"
PARAM_KEY = "phaseShiftRatio"
HARD_KEY = "phaseShiftHardRatio"

# 样本门槛与联动常数：写死，不提供 CLI 覆盖（防止"为了升级而降门槛"）；测试断言它们不被改动。
MIN_DEGRADED = 3          # 跨 run 可判定退化样本
MIN_CONTROL = 3           # 同场景 keyword_hit=true 的对照样本
MIN_RATIO_FLOOR = 0.40    # shift 绝对值下限
HARD_GAP_MIN = 0.15       # 双阈值联动后保留的间距宽度（ADR-011 / H0 裁决 B）
HARD_CAP = 0.95           # hard 上限（留出 critical 的余量）
DEGRADE_MARGIN = 0.05     # 下调到退化点前 5 个百分点

# 四处同步目标（相对 repo 根）。顺序固定，便于报告/测试断言。
TARGET_FILES = [
    ("rules/decay-params.yaml", "rules"),
    ("engine/config.py", "engine"),
    ("plugin/dsh-lingshu/cordis.patch.yml", "patch"),
    ("plugin/dsh-lingshu/control.js", "js"),
]
DECISION_REL = os.path.join("decisions", "009-相变阈值真实标定.md")
ASSUMPTIONS_REL = os.path.join("assumptions", "已验证.md")
CANDIDATES_NAME = "provenance-candidates.json"


# ── 通用 IO ──────────────────────────────────────────────

def _read_text(path: str) -> str:
    return io.open(path, encoding="utf-8").read()


def _write_text(path: str, text: str) -> None:
    parent = os.path.dirname(path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    io.open(path, "w", encoding="utf-8", newline="\n").write(text)


def _read_json(path: str):
    try:
        raw = io.open(path, encoding="utf-8", errors="replace").read().lstrip("\ufeff")
        return json.loads(raw), None
    except (OSError, json.JSONDecodeError) as exc:
        return None, "%s: %s" % (type(exc).__name__, str(exc)[:160])


def _is_num(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def _fmt(v) -> str:
    """数值转紧凑文本：0.70 → '0.7'，0.75 → '0.75'（保留两位内有效小数）。"""
    s = ("%.4f" % float(v)).rstrip("0").rstrip(".")
    return s or "0"


def _iso(ms: float | None = None) -> str:
    dt = _dt.datetime.fromtimestamp((ms or _dt.datetime.now().timestamp() * 1000) / 1000.0)
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _md_table(headers: list[str], rows: list[list]) -> str:
    out = ["| " + " | ".join(headers) + " |",
           "|" + "|".join("---" for _ in headers) + "|"]
    for r in rows:
        out.append("| " + " | ".join("" if c is None else str(c) for c in r) + " |")
    return "\n".join(out)


# ── 1. 读取混沌运行结果 ────────────────────────────────────

def load_runs(chaos_dir: str) -> tuple[list[dict], list[dict]]:
    """读取 chaos_dir 下的 run JSON；返回 (runs, skipped)。

    跳过规则：隐藏文件（`.last.json`）、候选文件自身、非 JSON、坏 JSON、缺 run_id/mode、
    重复 run_id（同一 run_id 多文件只取第一份，防 A5 的 .last.json/副本造成重复计数）。
    """
    runs: list[dict] = []
    skipped: list[dict] = []
    if not os.path.isdir(chaos_dir):
        return runs, skipped
    seen: dict[str, str] = {}
    for name in sorted(os.listdir(chaos_dir)):
        path = os.path.join(chaos_dir, name)
        if not os.path.isfile(path) or not name.lower().endswith(".json"):
            continue
        if name.startswith("."):
            skipped.append({"file": name, "reason": "隐藏/汇总文件，不是 run"})
            continue
        if name == CANDIDATES_NAME:
            skipped.append({"file": name, "reason": "候选文件自身，不是 run"})
            continue
        obj, err = _read_json(path)
        if err is not None:
            skipped.append({"file": name, "reason": "坏 JSON", "detail": err})
            continue
        if not isinstance(obj, dict):
            skipped.append({"file": name, "reason": "schema: 顶层不是对象"})
            continue
        run_id = obj.get("run_id")
        mode = obj.get("mode")
        if not isinstance(run_id, str) or not run_id.strip():
            skipped.append({"file": name, "reason": "schema: 缺 run_id"})
            continue
        if mode not in ("host", "engine"):
            skipped.append({"file": name, "reason": "schema: mode 必须是 host|engine（实际 %r）" % (mode,)})
            continue
        if run_id in seen:
            skipped.append({"file": name, "reason": "重复 run_id（已用 %s）" % seen[run_id]})
            continue
        seen[run_id] = name
        runs.append({"file": name, "run_id": run_id, "mode": mode,
                     "started_at": obj.get("started_at"), "finished_at": obj.get("finished_at"),
                     "scenarios": obj.get("scenarios") if isinstance(obj.get("scenarios"), dict) else {},
                     "summary": obj.get("summary") if isinstance(obj.get("summary"), dict) else {},
                     # 辅助字段（只追加）：规则自动标签汇总，不参与证据抽取/门槛判定。
                     "quality_labels": obj.get("quality_labels")})
    return runs, skipped


def summarize_quality_labels(runs: list[dict]) -> dict:
    """只读汇总 run JSON 顶层 `quality_labels`（{total, by_kind}）；坏数据跳过、不猜。

    **不参与** degraded/对照样本判定，也不影响 MIN_DEGRADED/MIN_CONTROL 门槛与任何 candidate：
    规则自动标签 ≠ 人工结果标签（docs/20 §1.2 / decisions/012 §2.7）。
    """
    by_kind = {k: 0 for k in QUALITY_LABEL_KINDS}
    total = 0
    by_kind_sum = 0
    runs_with_labels = 0
    invalid_runs = 0
    ignored = 0
    for run in runs:
        raw = run.get("quality_labels")
        if raw is None:
            continue
        if not isinstance(raw, dict):
            invalid_runs += 1
            continue
        runs_with_labels += 1
        run_sum = 0
        shown = raw.get("by_kind")
        if isinstance(shown, dict):
            for k, v in shown.items():
                if isinstance(k, str) and k in by_kind and _is_num(v) and int(v) >= 0:
                    n = int(v)
                    by_kind[k] += n
                    run_sum += n
                else:
                    ignored += 1          # 闭集外 kind / 非数值：单列忽略，不静默合并
        by_kind_sum += run_sum
        t = raw.get("total")
        if _is_num(t) and int(t) >= 0:
            total += int(t)
        else:
            total += run_sum              # 缺 total 时用 by_kind 合计兜底
    return {
        "runs_total": len(runs),
        "runs_with_labels": runs_with_labels,
        "invalid_runs": invalid_runs,
        "ignored_fields": ignored,
        "total": total,
        "by_kind": by_kind,
        "by_kind_sum": by_kind_sum,
        "present": runs_with_labels > 0,
    }


def _ratio_at_turn(lcf: dict, turn) -> float | None:
    """在 phase_observations 里找 key_question 那一轮的 ratio（对照的落点）。"""
    obs = lcf.get("phase_observations")
    if not isinstance(obs, list) or not isinstance(turn, (int, float)) or isinstance(turn, bool):
        return None
    for row in obs:
        if not isinstance(row, dict):
            continue
        t = row.get("turn")
        if isinstance(t, (int, float)) and not isinstance(t, bool) and int(t) == int(turn):
            r = row.get("ratio")
            if _is_num(r):
                return float(r)
    return None


def extract_evidence(runs: list[dict], current_shift: float) -> dict:
    """把 run 列表压成 degraded / controls 两个证据集合（只认 host 模式）。"""
    degraded: list[dict] = []
    controls: list[dict] = []
    ignored: list[dict] = []
    warnings: list[str] = []
    for run in runs:
        rid, mode = run["run_id"], run["mode"]
        lcf = run["scenarios"].get("long_context_flood")
        # engine 模式：不参与标定；若带退化标记，说明 A5 的 schema 用错，必须显式警告而不是静默吸收。
        if mode != "host":
            if isinstance(lcf, dict):
                kq = lcf.get("key_question") if isinstance(lcf.get("key_question"), dict) else {}
                if kq.get("keyword_hit") is False or _is_num(lcf.get("degraded_at_ratio")):
                    warnings.append("%s（mode=%s）带有关键词/退化标记：engine 模式没有真实模型回答，"
                                    "按协议忽略，不得作为退化证据" % (rid, mode))
            ignored.append({"run_id": rid, "file": run["file"], "reason": "engine 模式不参与标定"})
            continue
        if not isinstance(lcf, dict):
            ignored.append({"run_id": rid, "file": run["file"], "reason": "缺 long_context_flood 场景"})
            warnings.append("%s（host）缺 long_context_flood，无法判定退化" % rid)
            continue
        kq = lcf.get("key_question") if isinstance(lcf.get("key_question"), dict) else None
        if kq is None:
            ignored.append({"run_id": rid, "file": run["file"], "reason": "缺 key_question，无法判定退化"})
            warnings.append("%s（host）缺 key_question，无法判定 keyword_hit" % rid)
            continue
        hit = kq.get("keyword_hit")
        if not isinstance(hit, bool):
            ignored.append({"run_id": rid, "file": run["file"],
                            "reason": "keyword_hit 非布尔（%r）" % (hit,)})
            warnings.append("%s（host）keyword_hit 不是布尔值，跳过该 run" % rid)
            continue
        dar = lcf.get("degraded_at_ratio")
        base = {
            "run_id": rid, "file": run["file"],
            "turn": kq.get("turn"), "question": str(kq.get("question") or "")[:100],
            "expected_keyword": str(kq.get("expected_keyword") or "")[:80],
            "answer": str(kq.get("answer") or "")[:120],
        }
        ratio_at = _ratio_at_turn(lcf, kq.get("turn"))
        max_ratio = lcf.get("max_ratio")
        if ratio_at is None and _is_num(max_ratio):
            ratio_at = float(max_ratio)          # 兜底：没有对应轮观测时用本 run 最大 ratio
        if hit is False:
            if not _is_num(dar):
                ignored.append({"run_id": rid, "file": run["file"],
                                "ratio": None, "reason": "keyword_hit=false 但 degraded_at_ratio 缺失"})
                warnings.append("%s（host）关键字未命中但 degraded_at_ratio=null：无法定位退化点，不计入" % rid)
                continue
            item = dict(base, ratio=float(dar))
            if item["ratio"] < current_shift:
                degraded.append(item)
            else:
                ignored.append({"run_id": rid, "file": run["file"], "ratio": item["ratio"],
                                "reason": "退化点 ratio=%.4f 不低于当前阈值 %.4f，不构成下调依据"
                                          % (item["ratio"], current_shift)})
                warnings.append("%s（host）退化点 ratio=%.4f ≥ 当前阈值 %.4f：说明阈值已在退化点之下，"
                                "不需要下调" % (rid, item["ratio"], current_shift))
        else:
            if _is_num(dar):
                warnings.append("%s（host）keyword_hit=true 却 degraded_at_ratio 非空：数据自相矛盾，"
                                "忽略退化标记，只当对照" % rid)
            controls.append(dict(base, ratio=ratio_at))
    return {"degraded": degraded, "controls": controls,
            "ignored": ignored, "warnings": warnings}


# ── 2. 标定规则（纯函数，便于单测）─────────────────────────

def suggest_linked_thresholds(current_shift: float, current_hard: float,
                              min_degraded: float) -> dict:
    """H0 裁决 B（ADR-011）：shift 按真实退化证据下调，hard **跟随**并保留 0.15 间距宽度。

        new_shift = max(0.40, floor((min(degraded_at_ratio) - 0.05) * 100) / 100)
        new_hard  = min(0.95, round(new_shift + 0.15, 2))

    为什么不是"保留 hard 绝对值再校验间距"（task-13 的旧规则）：那会让 shift 永远降不下去
    （hard=0.85 → 下限 0.70 = 当前 shift）。H0 选择"保留间距宽度"：shift→critical 的预警
    结构不变，两个触发点一起前移。hard 本身没有独立退化证据，属**结构性跟随**（ADR-011 代价）。
    """
    raw_shift = math.floor((float(min_degraded) - DEGRADE_MARGIN) * 100) / 100.0
    new_shift = round(max(MIN_RATIO_FLOOR, raw_shift), 2)
    new_hard = round(min(HARD_CAP, round(new_shift + HARD_GAP_MIN, 2)), 2)
    return {
        "raw_shift": round(raw_shift, 4),
        "new_shift": new_shift,
        "new_hard": new_hard,
        "shift_floor": MIN_RATIO_FLOOR,
        "hard_gap": HARD_GAP_MIN,
        "hard_cap": HARD_CAP,
        "margin": DEGRADE_MARGIN,
        "invariants": {
            "hard_gt_shift": new_hard > new_shift,
            "hard_le_cap": new_hard <= HARD_CAP,
        },
    }


def build_candidate(current_shift: float, current_hard: float, evidence: dict) -> dict:
    """证据 → 联动候选 / 拒绝原因。不写文件、不改配置。"""
    degraded = evidence["degraded"]
    controls = evidence["controls"]
    old = {"shift": round(float(current_shift), 4), "hard": round(float(current_hard), 4)}
    base = {"param": PARAM, "linkage": True, "old": old,
            "counts": {"degraded": len(degraded), "control": len(controls)},
            "evidence": {
                "min_degraded_at_ratio": (min(d["ratio"] for d in degraded) if degraded else None),
                "degraded_samples": degraded,
                "control_samples": controls,
                "runs": sorted({d["run_id"] for d in degraded} | {c["run_id"] for c in controls}),
            }}
    if len(degraded) < MIN_DEGRADED or len(controls) < MIN_CONTROL:
        return dict(base, ok=False, refused="证据不足，保持 guess",
                    reason="样本门槛未达：degraded %d/%d，对照 %d/%d"
                           % (len(degraded), MIN_DEGRADED, len(controls), MIN_CONTROL),
                    insufficient=True)
    min_ratio = max(0.0, min(d["ratio"] for d in degraded))
    math_info = suggest_linked_thresholds(old["shift"], old["hard"], min_ratio)
    base["evidence"].update({
        "formula": "new_shift = max(0.40, floor((min(degraded_at_ratio)-0.05)*100)/100); "
                   "new_hard = min(0.95, round(new_shift+0.15, 2))",
        "formula_inputs": {"current_shift": old["shift"], "current_hard": old["hard"],
                           "min_degraded_at_ratio": round(min_ratio, 4)},
        "formula_result": math_info,
        "linkage_rule": "B（H0 裁决，ADR-011）：hard 随 shift 移动，保留 0.15 间距宽度，上限 0.95",
    })
    if math_info["new_shift"] >= old["shift"] - 1e-9:
        return dict(base, ok=False, refused="证据达标，但没有可执行的下调",
                    reason="new_shift=%s 不低于当前 shift=%s（下限 %.2f）；报告记为无下调空间"
                           % (_fmt(math_info["new_shift"]), _fmt(old["shift"]), MIN_RATIO_FLOOR),
                    insufficient=False)
    if not (math_info["invariants"]["hard_gt_shift"] and math_info["invariants"]["hard_le_cap"]):
        return dict(base, ok=False, refused="联动不变量不满足，拒绝产出候选",
                    reason="new_hard=%s 必须 > new_shift=%s 且 <= %.2f"
                           % (_fmt(math_info["new_hard"]), _fmt(math_info["new_shift"]), HARD_CAP),
                    insufficient=False)
    shift_entry = {"param": PARAM, "old": old["shift"], "new": math_info["new_shift"]}
    hard_entry = {"param": HARD_PARAM, "old": old["hard"], "new": math_info["new_hard"]}
    return dict(base, ok=True, shift=shift_entry, hard=hard_entry,
                new_shift=math_info["new_shift"], new_hard=math_info["new_hard"],
                applied=False, refused=None,
                reason="min(degraded_at_ratio)=%s → new_shift=max(0.40, floor(%.3f*100)/100)=%s；"
                       "new_hard=min(0.95, %s+0.15)=%s"
                       % (_fmt(min_ratio), min_ratio - DEGRADE_MARGIN,
                          _fmt(math_info["new_shift"]), _fmt(math_info["new_shift"]),
                          _fmt(math_info["new_hard"])))


# ── 3. 当前值 / 四处一致性 / 配置改写 ──────────────────────

def _control_block(text: str) -> tuple[int, int] | None:
    """返回 rules 中顶层 `control:` 段的 [start, end) 行号。"""
    lines = text.splitlines()
    start = None
    for i, line in enumerate(lines):
        if re.match(r"^control:\s*(?:#.*)?$", line):
            start = i
            break
    if start is None:
        return None
    end = len(lines)
    for j in range(start + 1, len(lines)):
        if re.match(r"^[A-Za-z_]", lines[j]):        # 下一个顶层键
            end = j
            break
    return start, end


def read_rule_control(repo: str) -> dict:
    """从 rules/decay-params.yaml 的 control 段解析标量键（单一来源口径）。"""
    path = os.path.join(repo, "rules", "decay-params.yaml")
    try:
        text = _read_text(path)
    except OSError:
        return {}
    bounds = _control_block(text)
    if not bounds:
        return {}
    out = {}
    for line in text.splitlines()[bounds[0] + 1:bounds[1]]:
        m = re.match(r"^\s*([A-Za-z_]\w*):\s*(-?\d+(?:\.\d+)?)\s*(?:#.*)?$", line)
        if m:
            out[m.group(1)] = float(m.group(2))
    return out


def read_four_values(repo: str) -> dict:
    """读取四处当前值（shift + hard 两组），用于报告展示与 apply 前的一致性检查。"""
    out = {}
    for rel, kind in TARGET_FILES:
        path = os.path.join(repo, rel)
        try:
            text = _read_text(path)
        except OSError:
            out[rel] = {"shift": None, "hard": None}
            continue
        vals = {"shift": None, "hard": None}
        for key, slot in (("phaseShiftRatio", "shift"), ("phaseShiftHardRatio", "hard")):
            if kind in ("rules", "patch"):
                m = re.search(r"^\s*%s:\s*(-?\d+(?:\.\d+)?)" % key, text, re.M)
            elif kind == "engine":
                m = re.search(r'"%s":\s*(-?\d+(?:\.\d+)?)' % key, text)
            else:
                m = re.search(r"^\s*%s:\s*(-?\d+(?:\.\d+)?)," % key, text, re.M)
            if m:
                vals[slot] = float(m.group(1))
        out[rel] = vals
    return out


def _calib_note(old_shift: float, new_shift: float, old_hard: float, new_hard: float,
                evidence_note: str) -> str:
    """四处同步时统一使用的行内依据注释（联动 B / ADR-011）。"""
    return ("v5.0 真实标定（联动 B / ADR-011）：shift %s→%s / hard %s→%s；"
            "provenance: measured；依据 %s"
            % (_fmt(old_shift), _fmt(new_shift), _fmt(old_hard), _fmt(new_hard), evidence_note))


def _plan_rules(text: str, old_shift: float, new_shift: float,
                old_hard: float, new_hard: float, evidence_note: str) -> tuple[str, str]:
    """改写 rules 的 shift/hard 两行 + control 段 provenance 注释。"""
    bounds = _control_block(text)
    if not bounds:
        return "", "rules 中找不到顶层 control: 段"
    lines = text.splitlines()
    start, end = bounds
    note = _calib_note(old_shift, new_shift, old_hard, new_hard, evidence_note)
    for key, old, new in (("phaseShiftRatio", old_shift, new_shift),
                          ("phaseShiftHardRatio", old_hard, new_hard)):
        hits = [i for i in range(start, end)
                if re.match(r"^\s*%s:\s*-?\d+(?:\.\d+)?\s*(?:#.*)?$" % key, lines[i])]
        if len(hits) != 1:
            return "", "rules control 段内 %s 出现 %d 次（必须恰好 1 次）" % (key, len(hits))
        i = hits[0]
        if "provenance: measured" in lines[i]:
            return "", "already_measured"
        m = re.match(r"^(\s*%s:\s*)(-?\d+(?:\.\d+)?)(\s*)(#.*)?$" % key, lines[i])
        if not m:
            return "", "rules %s 行格式无法解析: %r" % (key, lines[i])
        if abs(float(m.group(2)) - float(old)) > 1e-9:
            return "", "rules %s 当前值 %s 与规则解析出的旧值 %s 不一致" % (key, m.group(2), _fmt(old))
        old_comment = re.sub(r"^#\s*", "", (m.group(4) or "")).strip()
        parts = ([old_comment] if old_comment else []) + [note]
        lines[i] = m.group(1) + _fmt(new) + "  # " + "；".join(parts)
    # control 段整体 provenance 仍是 guess（integral*/trend/ratioMax 等未标定），加注释避免误读。
    for j in range(start + 1, end):
        pm = re.match(r"^(\s*)provenance:\s*guess\s*(?:#.*)?$", lines[j])
        if pm:
            lines[j] = pm.group(1) + "provenance: guess                 # 仅 phaseShiftRatio/" \
                                    "phaseShiftHardRatio 为 measured（联动 B，ADR-011），其余仍为 guess"
            break
    return "\n".join(lines) + "\n", ""


def _plan_engine(text: str, old_shift: float, new_shift: float,
                 old_hard: float, new_hard: float, evidence_note: str) -> tuple[str, str]:
    note = _calib_note(old_shift, new_shift, old_hard, new_hard, evidence_note)
    new_text = text
    for key, old, new in (("phaseShiftRatio", old_shift, new_shift),
                          ("phaseShiftHardRatio", old_hard, new_hard)):
        hits = re.findall(r'"%s":\s*-?\d+(?:\.\d+)?' % key, new_text)
        if len(hits) != 1:
            return "", "engine/config.py 中 %s 出现 %d 次（必须恰好 1 次）" % (key, len(hits))
        cur = re.search(r'"%s":\s*(-?\d+(?:\.\d+)?)' % key, new_text)
        if abs(float(cur.group(1)) - float(old)) > 1e-9:
            return "", "engine %s 当前值 %s 与旧值 %s 不一致" % (key, cur.group(1), _fmt(old))
        new_text = re.sub(r'("%s":\s*)-?\d+(?:\.\d+)?' % key,
                          lambda m: m.group(1) + _fmt(new), new_text)
        new_text = re.sub(r'("%s":\s*[0-9.]+),' % key,
                          lambda m: m.group(1) + ",  # " + note, new_text, count=1)
    return new_text, ""


def _plan_patch(text: str, old_shift: float, new_shift: float,
                old_hard: float, new_hard: float, evidence_note: str) -> tuple[str, str]:
    note = _calib_note(old_shift, new_shift, old_hard, new_hard, evidence_note)
    new_text = text
    for key, old, new in (("phaseShiftRatio", old_shift, new_shift),
                          ("phaseShiftHardRatio", old_hard, new_hard)):
        hits = re.findall(r"^\s*%s:\s*-?\d+(?:\.\d+)?\s*(?:#.*)?$" % key, new_text, re.M)
        if len(hits) != 1:
            return "", "cordis.patch.yml 中 %s 出现 %d 次（必须恰好 1 次）" % (key, len(hits))
        cur = re.search(r"^\s*%s:\s*(-?\d+(?:\.\d+)?)" % key, new_text, re.M)
        if abs(float(cur.group(1)) - float(old)) > 1e-9:
            return "", "cordis.patch.yml %s 当前值 %s 与旧值 %s 不一致" % (key, cur.group(1), _fmt(old))

        def repl(m, _note=note):
            comment = (m.group(3) or "").strip()
            parts = ([comment.lstrip("# ").strip()] if comment else []) + [_note]
            return m.group(1) + _fmt(new) + "  # " + "；".join(parts)

        new_text, n = re.subn(r"^(\s*%s:\s*)-?\d+(?:\.\d+)?(\s*)(#.*)?$" % key,
                              repl, new_text, count=1, flags=re.M)
        if n != 1:
            return "", "cordis.patch.yml %s 行改写失败" % key
    return new_text, ""


def _plan_js(text: str, old_shift: float, new_shift: float,
             old_hard: float, new_hard: float, evidence_note: str) -> tuple[str, str]:
    note = _calib_note(old_shift, new_shift, old_hard, new_hard, evidence_note)
    new_text = text
    for key, old, new in (("phaseShiftRatio", old_shift, new_shift),
                          ("phaseShiftHardRatio", old_hard, new_hard)):
        hits = re.findall(r"^\s*%s:\s*-?\d+(?:\.\d+)?," % key, new_text, re.M)
        if len(hits) != 1:
            return "", "control.js 中 DEFAULT_PARAMS.%s 出现 %d 次（必须恰好 1 次）" % (key, len(hits))
        cur = re.search(r"^\s*%s:\s*(-?\d+(?:\.\d+)?)," % key, new_text, re.M)
        if abs(float(cur.group(1)) - float(old)) > 1e-9:
            return "", "control.js %s 当前值 %s 与旧值 %s 不一致" % (key, cur.group(1), _fmt(old))

        def repl(m, _note=note):
            comment = (m.group(3) or "").strip()
            old_comment = comment.lstrip("/").strip()
            parts = ([old_comment] if old_comment else []) + [_note]
            return m.group(1) + _fmt(new) + ",      // " + "；".join(parts)

        new_text, n = re.subn(r"^(\s*%s:\s*)-?\d+(?:\.\d+)?,(\s*)(//.*)?$" % key,
                              repl, new_text, count=1, flags=re.M)
        if n != 1:
            return "", "control.js %s 行改写失败" % key
    return new_text, ""


_PLANNERS = {"rules": _plan_rules, "engine": _plan_engine,
             "patch": _plan_patch, "js": _plan_js}


def plan_edits(repo: str, old_shift: float, new_shift: float,
               old_hard: float, new_hard: float,
               evidence_note: str) -> tuple[dict, list[str]]:
    """为四个目标文件生成新内容（每个文件 shift+hard 两个值）；任一不可安全改写 → 空 + 错误列表。"""
    plans: dict[str, str] = {}
    errors: list[str] = []
    for rel, kind in TARGET_FILES:
        path = os.path.join(repo, rel)
        try:
            text = _read_text(path)
        except OSError as exc:
            errors.append("%s: 读取失败 %s" % (rel, exc))
            continue
        planner = _PLANNERS[kind]
        new_text, err = planner(text, old_shift, new_shift, old_hard, new_hard, evidence_note)
        if err:
            errors.append("%s: %s" % (rel, err))
            continue
        if new_text == text:
            errors.append("%s: 改写后内容未变化" % rel)
            continue
        plans[rel] = new_text
    return plans, errors


def run_check_config_sync(repo: str) -> tuple[int, str]:
    """调用仓库自带的 check_config_sync.py（在副本仓库上同样生效，因为它按脚本位置定位 ROOT）。"""
    script = os.path.join(repo, "tools", "check_config_sync.py")
    if not os.path.isfile(script):
        return 2, "找不到 %s" % script
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    try:
        proc = subprocess.run([sys.executable, script], cwd=repo, env=env,
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, encoding="utf-8", errors="replace", timeout=180)
        return proc.returncode, (proc.stdout or "")[-4000:]
    except (OSError, subprocess.SubprocessError) as exc:
        return 2, "执行 check_config_sync 失败：%s" % exc


# ── 4. ADR / assumptions（仅 apply 成功时写）───────────────

def _tail(text: str, n: int = 6) -> str:
    rows = (text or "").strip().splitlines()
    return "\n".join(rows[-n:]) if rows else ""


def build_decision_markdown(repo: str, date: str, candidate: dict, evidence: dict,
                            check_output: str, four_before: dict) -> str:
    old_s, new_s = candidate["shift"]["old"], candidate["shift"]["new"]
    old_h, new_h = candidate["hard"]["old"], candidate["hard"]["new"]
    ev = candidate["evidence"]
    lines = [
        "# 009 · 相变双阈值真实标定（shift + hard 联动 B / ADR-011）",
        "",
        "> 状态：**已应用（measured）** ｜ 日期：%s ｜ 生成：`tools/chaos_calibrate.py --apply`（自动生成）" % date,
        "> 证据来源：`acceptance/chaos/*.json`（**仅 mode==host**；engine 模式数据不参与）",
        "> 规则来源：H0 裁决 B —— 双阈值联动（`decisions/011-相变阈值联动规则.md`）",
        "> 复核要求：本 ADR 由工具生成，合入前必须人工复核数据窗口、run 清单与回滚方式。",
        "",
        "## 1. 结论",
        "",
        "- `control.phaseShiftRatio`：**%s → %s**" % (_fmt(old_s), _fmt(new_s)),
        "- `control.phaseShiftHardRatio`：**%s → %s**（跟随 shift，保留 0.15 间距宽度）"
        % (_fmt(old_h), _fmt(new_h)),
        "- 两参数 provenance：guess → measured（同一联动单元，整组升级）。",
        "",
        "依据：host 模式混沌压测中，`long_context_flood.key_question.keyword_hit=false` 的"
        "可判定退化最小发生在 `ratio=%s`，低于当时 shift 阈值 %s；同场景对照（keyword_hit=true）%d 个。"
        % (_fmt(ev["min_degraded_at_ratio"]), _fmt(old_s), candidate["counts"]["control"]),
        "",
        "## 2. 数据",
        "",
        "| run_id | turn | 退化时 ratio | 问题 | 期望关键词 | 回答摘要 |",
        "|---|---|---|---|---|---|",
    ]
    for d in ev["degraded_samples"]:
        lines.append("| %s | %s | %s | %s | %s | %s |" % (
            d["run_id"], d.get("turn"), _fmt(d["ratio"]),
            (d.get("question") or "").replace("|", "/"),
            (d.get("expected_keyword") or "").replace("|", "/"),
            (d.get("answer") or "").replace("|", "/")))
    lines += [
        "",
        "对照样本（keyword_hit=true）：",
        "",
        "| run_id | turn | ratio（该轮或本 run max） |",
        "|---|---|---|",
    ]
    for c in ev["control_samples"]:
        lines.append("| %s | %s | %s |" % (
            c["run_id"], c.get("turn"),
            _fmt(c["ratio"]) if c.get("ratio") is not None else "—"))
    lines += [
        "",
        "## 3. 计算（联动规则 B，ADR-011）",
        "",
        "```",
        "new_shift = max(0.40, floor((min(degraded_at_ratio) - 0.05) * 100) / 100)",
        "          = max(0.40, floor((%s - 0.05) * 100) / 100) = %s"
        % (_fmt(ev["formula_inputs"]["min_degraded_at_ratio"]), _fmt(new_s)),
        "new_hard  = min(0.95, round(new_shift + 0.15, 2))",
        "          = min(0.95, %s + 0.15) = %s" % (_fmt(new_s), _fmt(new_h)),
        "```",
        "",
        "> 保留了原双阈值的 **0.15 间距宽度**（不是保留 hard 绝对值）：shift→critical 的预警结构不变，"
        "两个触发点一起前移；hard 上限 0.95。",
        "",
        "## 4. 代价（为什么这是有代价的）",
        "",
        "- 相变补偿会**更早**触发：ratio 到 %s 就补 shift 级锚点，%s 就无条件锚定，"
        "prompt 变长、首尾锚点注入频率上升；" % (_fmt(new_s), _fmt(new_h)),
        "- hard 的这次变化**没有独立的 hard 级退化证据**，属于结构性跟随（ADR-011 已记录该代价）；"
        "若日后要独立标定 hard，需要另一组 critical 级证据；",
        "- 长上下文里锚点本身占 token，可能挤占事实/记忆注入预算（插件没有域预算淘汰，只按段落注入）；",
        "- 若退化点样本来自少数场景，阈值下调可能对其它场景过度保守；因此本 ADR 的证据仅覆盖所列 run 的窗口。",
        "",
        "## 5. 回滚方式",
        "",
        "把以下**四处文件 × shift/hard 两组共 8 个值**同时改回旧值（shift=%s、hard=%s），"
        "然后执行：`python tools/check_config_sync.py` 必须 0 fail：" % (_fmt(old_s), _fmt(old_h)),
        "",
    ]
    for rel, _kind in TARGET_FILES:
        cur = four_before.get(rel) or {}
        lines.append("- `%s`（当前 shift=%s / hard=%s → 回滚 shift=%s / hard=%s）"
                     % (rel,
                        _fmt(cur.get("shift") if cur.get("shift") is not None else new_s),
                        _fmt(cur.get("hard") if cur.get("hard") is not None else new_h),
                        _fmt(old_s), _fmt(old_h)))
    lines += [
        "- 把 `rules/decay-params.yaml#control.phaseShiftRatio` 与 `#control.phaseShiftHardRatio` "
        "两行的 `provenance: measured` 改回 `guess`；",
        "- 在 `assumptions/已验证.md` 的 E-M1 条目末尾标注「**已回滚（日期/原因）**」；",
        "- 重新跑 `python tools/chaos_calibrate.py` 生成新报告；本 ADR 保留为历史记录，不删除。",
        "",
        "## 6. 自检输出",
        "",
        "`python tools/check_config_sync.py` → 0 fail（尾部输出）：",
        "",
        "```",
        _tail(check_output, 6),
        "```",
        "",
    ]
    return "\n".join(lines)


def append_assumption(repo: str, date: str, candidate: dict, run_ids: list[str]) -> str:
    """向 assumptions/已验证.md 追加一条 measured 记录（记录 shift+hard 两个参数）。"""
    path = os.path.join(repo, ASSUMPTIONS_REL)
    original = _read_text(path)
    if "E-M1" in original:
        raise RuntimeError("assumptions/已验证.md 中已存在 E-M1，拒绝重复追加")
    if not original.endswith("\n"):
        original += "\n"
    old_s, new_s = candidate["shift"]["old"], candidate["shift"]["new"]
    old_h, new_h = candidate["hard"]["old"], candidate["hard"]["new"]
    block = [
        "",
        "---",
        "",
        "## 五、真实数据标定（混沌压测，%s）" % date,
        "",
        "> 证据来自 `acceptance/chaos/*.json` 的 **host 模式**运行；engine 模式不参与。",
        "> 规则：H0 裁决 B 双阈值联动（`decisions/011-相变阈值联动规则.md`）。",
        "",
        "| # | 结论 | 证据 | 强度 |",
        "|---|---|---|---|",
        "| E-M1 | `control.phaseShiftRatio` %s→%s 且 `control.phaseShiftHardRatio` %s→%s"
        "（联动 B：hard=min(0.95, shift+0.15)；host 模式 ratio=%s 已出现 keyword_hit=false 的可判定退化，"
        "对照 %d 个 keyword_hit=true） | `acceptance/chaos/`（run_id: %s）；`%s`；`decisions/011` "
        "| 中（仅该数据窗口，不可外推；hard 为结构性跟随） |"
        % (_fmt(old_s), _fmt(new_s), _fmt(old_h), _fmt(new_h),
           _fmt(candidate["evidence"]["min_degraded_at_ratio"]),
           candidate["counts"]["control"], ", ".join(run_ids),
           DECISION_REL.replace(os.sep, "/")),
        "",
        "> E-M1 的完整记录、代价与回滚方式见 `%s`。" % DECISION_REL.replace(os.sep, "/"),
        "",
    ]
    original += "\n".join(block)
    _write_text(path, original)
    return original


# ── 5. 报告 ──────────────────────────────────────────────

def _four_consistency(four: dict) -> tuple[bool, bool, list, list]:
    """返回 (shift 四处一致, hard 四处一致, shift 值列表, hard 值列表)。"""
    shifts = [(v or {}).get("shift") for v in four.values()]
    hards = [(v or {}).get("hard") for v in four.values()]

    def _ok(vals):
        return (len(vals) == len(TARGET_FILES) and all(v is not None for v in vals)
                and max(vals) - min(vals) < 1e-9)

    return _ok(shifts), _ok(hards), shifts, hards


def render_report(runs: list[dict], skipped: list[dict], evidence: dict, candidate: dict,
                  current_shift: float, current_hard: float, four: dict, apply_info: dict | None,
                  date: str, chaos_dir: str, repo: str,
                  quality_labels: dict | None = None) -> str:
    L = []
    A = L.append
    A("# 混沌结果 → 相变双阈值标定报告（%s）" % date)
    A("")
    A("> 生成：`tools/chaos_calibrate.py`（本文件为生成物，勿手改）｜ repo：`%s`" % repo)
    A("> 数据目录：`%s` ｜ 生成时间：%s" % (chaos_dir, _iso()))
    A("> 规则：H0 裁决 B —— **双阈值联动**（`decisions/011-相变阈值联动规则.md`）")
    A("> 结论：**%s**" % ("已 apply" if apply_info and apply_info.get("ok")
                        else ("证据不足，保持 guess" if candidate.get("insufficient")
                              else ("无候选" if not candidate.get("ok") else "有候选，未 apply"))))
    A("")
    A("## 1. 判定规则（写死，不提供降门槛开关）")
    A("")
    A("- 只认 `mode == \"host\"` 的运行；**engine 模式永远不参与**（没有真实模型回答，"
      "其 `degraded_at_ratio` 若非空视为 schema 违规）；")
    A("- 可判定退化 = `long_context_flood.key_question.keyword_hit === false` **且** "
      "`degraded_at_ratio` 为数字 **且** 该 ratio 严格小于当前 shift 阈值 %s（当前 hard=%s）；"
      % (_fmt(current_shift), _fmt(current_hard)))
    A("- 对照 = 同场景 `keyword_hit === true`；")
    A("- 跨 run 门槛：degraded ≥ %d 且 对照 ≥ %d（写死在脚本常量里，测试断言不可覆盖）；"
      % (MIN_DEGRADED, MIN_CONTROL))
    A("- 联动规则 B（ADR-011）：`new_shift = max(0.40, floor((min(degraded_at_ratio)-0.05)*100)/100)`；"
      "`new_hard = min(0.95, round(new_shift+0.15, 2))`——**hard 随 shift 移动**，"
      "保留 0.15 间距宽度（不是保留 hard 绝对值），hard 上限 0.95；")
    A("- shift 算出的新值不低于当前 shift → 没有可执行下调，不产出 applied 候选；")
    A("")
    A("## 2. 当前值：四处副本（shift + hard）")
    A("")
    A("| 位置 | shift | hard |")
    A("|---|---|---|")
    for rel, _kind in TARGET_FILES:
        v = four.get(rel) or {}
        A("| `%s` | %s | %s |" % (
            rel,
            _fmt(v.get("shift")) if v.get("shift") is not None else "**缺失/不可解析**",
            _fmt(v.get("hard")) if v.get("hard") is not None else "**缺失/不可解析**"))
    shift_ok, hard_ok, _sv, _hv = _four_consistency(four)
    A("")
    A("- 四处一致：shift **%s** ｜ hard **%s**（不一致时 apply 直接拒绝，先修 `check_config_sync`）"
      % ("是" if shift_ok else "否", "是" if hard_ok else "否"))
    A("")
    A("## 3. 混沌运行数据")
    A("")
    if not runs and not skipped:
        A("**没有可用运行数据（目录为空或不存在）**：证据不足，保持 guess。")
    else:
        A("| 文件 | run_id | mode | 参与？ | keyword_hit | degraded_at_ratio | 说明 |")
        A("|---|---|---|---|---|---|---|")
        for r in runs:
            lcf = r["scenarios"].get("long_context_flood")
            hit = dar = "—"
            if isinstance(lcf, dict):
                kq = lcf.get("key_question") if isinstance(lcf.get("key_question"), dict) else {}
                hit = kq.get("keyword_hit", "—")
                dar = lcf.get("degraded_at_ratio", "—")
            joined = r["mode"] == "host"
            used = any(d["run_id"] == r["run_id"] for d in evidence["degraded"]) or \
                any(c["run_id"] == r["run_id"] for c in evidence["controls"])
            A("| %s | %s | %s | %s | %s | %s | %s |" % (
                r["file"], r["run_id"], r["mode"], "是" if joined else "否（engine）",
                hit, dar, "已计入证据" if used else ("host 但无可用证据" if joined else "不参与标定")))
        for s in skipped:
            A("| %s | — | — | 否 | — | — | 跳过：%s%s |" % (
                s.get("file"), s.get("reason"), ("（%s）" % s.get("detail") if s.get("detail") else "")))
        if evidence["ignored"]:
            A("")
            A("未计入的 run/证据：")
            for x in evidence["ignored"]:
                A("- `%s`（%s）：%s" % (x.get("run_id"), x.get("file"), x.get("reason")))
    if evidence["warnings"]:
        A("")
        A("⚠️ 警告（数据/口径问题，不影响其它 run 的统计）：")
        A("")
        for w in evidence["warnings"]:
            A("- " + w)
    A("")
    A("## 4. 聚合证据")
    A("")
    A("| 指标 | 值 | 门槛 |")
    A("|---|---|---|")
    A("| 可判定退化样本（host，ratio<当前 shift 阈值） | %d | ≥%d |"
      % (len(evidence["degraded"]), MIN_DEGRADED))
    A("| 对照样本（host，keyword_hit=true） | %d | ≥%d |"
      % (len(evidence["controls"]), MIN_CONTROL))
    A("| min(degraded_at_ratio) | %s | — |"
      % (_fmt(min((d["ratio"] for d in evidence["degraded"]), default=None))
         if evidence["degraded"] else "—"))
    A("")
    if evidence["degraded"]:
        A("| run_id | turn | ratio | 期望关键词 | 回答摘要 |")
        A("|---|---|---|---|---|")
        for d in evidence["degraded"]:
            A("| %s | %s | %s | %s | %s |" % (
                d["run_id"], d.get("turn"), _fmt(d["ratio"]),
                d.get("expected_keyword") or "—", (d.get("answer") or "—").replace("|", "/")))
        A("")
    A("## 5. 结论")
    A("")
    if candidate.get("ok"):
        A("- 建议联动调整（尚未 apply）：")
        A("  - `%s`：**%s → %s**" % (PARAM, _fmt(candidate["shift"]["old"]),
                                    _fmt(candidate["shift"]["new"])))
        A("  - `%s`：**%s → %s**（= new_shift + 0.15）"
          % (HARD_PARAM, _fmt(candidate["hard"]["old"]), _fmt(candidate["hard"]["new"])))
        A("- 计算：%s" % candidate["reason"])
        A("- 证据：%d 个退化 + %d 个对照；run_id：%s"
          % (candidate["counts"]["degraded"], candidate["counts"]["control"],
             ", ".join(candidate["evidence"]["runs"])))
        A("- 落盘前请人工复核：run 是否同一插件版本、命中判定是否人工抽检过、场景是否单一。")
    elif candidate.get("insufficient"):
        A("- **证据不足，保持 guess。**")
        A("- 原因：%s" % candidate["reason"])
    else:
        A("- **没有可执行的下调**（证据已达标，但 new_shift 不低于当前 shift）。")
        A("- 原因：%s" % candidate.get("reason"))
    A("")
    A("> 联动规则 B（ADR-011）：hard 跟随 shift 移动，保留 0.15 间距宽度，上限 0.95。"
      "hard 没有独立的 critical 级退化证据，属结构性跟随；"
      "若日后要独立标定 hard，需要另一组 critical 级证据（本工具当前不做）。")
    A("")
    A("### 5.1 辅助观察：规则自动标签（`quality_labels`，**不参与门槛**）")
    A("")
    A("> **标签是辅助信号。** 本节只读 run JSON 顶层 `quality_labels`（`{total, by_kind}`），"
      "来源是插件 `audit.jsonl` 的 `kind==quality_label` 规则自动标签。它**不参与** degraded/对照"
      "样本计数，**不影响** `MIN_DEGRADED=%d` / `MIN_CONTROL=%d` 门槛与任何 candidate 判定；"
      "**规则自动标签 ≠ 人工结果标签**（docs/20 §1.2 / decisions/012 §2.7），不能作为 measured 证据。"
      % (MIN_DEGRADED, MIN_CONTROL))
    A("")
    ql = quality_labels if isinstance(quality_labels, dict) else {}
    if not ql.get("present"):
        A("**未观测 / 无标签事件**：没有 run 带顶层 `quality_labels`（旧 run JSON，或插件未产出标签）。"
          "这是**未观测**，不是「标签数为 0」的结论，也不是故障。")
    else:
        A(_md_table(["指标", "值", "说明"], [
            ["有标签的 run 数 / run 总数",
             "%d / %d" % (ql.get("runs_with_labels", 0), ql.get("runs_total", 0)),
             "顶层带 `quality_labels` 的 run"],
            ["标签总数（`total` 字段）", ql.get("total", 0), "只读，不折入门槛"],
            ["by_kind 合计", ql.get("by_kind_sum", 0),
             "与 total 不一致时以 run JSON 原值为准"],
            ["闭集 kind 计数", json.dumps(ql.get("by_kind", {}), ensure_ascii=False),
             "wrong/missing/should_not_recall/drift/overreach"],
            ["格式不可用的 run / 字段",
             "%d / %d" % (ql.get("invalid_runs", 0), ql.get("ignored_fields", 0)),
             "坏数据跳过，不猜"],
        ]))
    A("")
    A("> 标签分布只用于观察「真实宿主里用户纠正/漂移信号有多频繁」；"
      "它们既不能把 degraded 计数补到门槛，也不能替代人工判定的 keyword_hit 判定。")
    A("")
    A("## 6. apply 结果")
    A("")
    if not apply_info:
        A("本次未给 `--apply`：配置零修改。")
    elif apply_info.get("ok"):
        A("- 已同步 **8 个值（4 文件 × shift/hard）**，并在仓库上跑 `tools/check_config_sync.py`：**ok**；")
        A("- 改动文件：%s" % ", ".join("`%s`" % f for f in apply_info["files_changed"]))
        A("- ADR：`%s`；assumptions 追加：E-M1（记录 shift+hard 两个参数）。"
          % DECISION_REL.replace(os.sep, "/"))
    elif apply_info.get("check") == "fail" or apply_info.get("rolled_back"):
        A("- **apply 失败/回滚**：%s" % apply_info.get("error", ""))
        A("- `check_config_sync`：**%s**；已回滚文件：%s"
          % (apply_info.get("check", "fail"),
             ", ".join("`%s`" % f for f in apply_info.get("rolled_back", []))))
        out = (apply_info.get("check_output") or "").strip()
        if out:
            A("")
            A("```")
            A("\n".join(out.splitlines()[-12:]))
            A("```")
    else:
        # 证据不足 / 无可执行下调 / 已 measured：apply 是**安全拒绝**，不是故障。
        A("- 本次 `--apply` **未执行任何修改**（安全拒绝）：%s" % apply_info.get("error", ""))
        out = (apply_info.get("check_output") or "").strip()
        if out:
            A("")
            A("```")
            A("\n".join(out.splitlines()[-12:]))
            A("```")
    A("")
    A("## 7. 复现 / 下一步")
    A("")
    A("```bash")
    A("python tools/chaos_calibrate.py                 # 只出本报告与候选 JSON")
    A("python tools/chaos_calibrate.py --apply         # 证据达标才改配置（自动 check + 回滚）")
    A("```")
    A("")
    A("- 需要更多 **host 模式** 混沌数据（A5 夜间调度），且每个 run 必须带 `key_question.keyword_hit`；")
    A("- 判定 keyword_hit 需要有真实模型回答；engine 模式数据永远不能替代；")
    A("- 若长期无 host 数据，本报告会持续写「证据不足，保持 guess」——这是正确行为，不是故障；")
    A("- 规则变更记录：`decisions/011-相变阈值联动规则.md`（H0 裁决 B：双阈值联动）。")
    A("")
    return "\n".join(L)


def render_candidates(runs: list[dict], skipped: list[dict], evidence: dict,
                      candidate: dict, current_shift: float, current_hard: float,
                      four: dict, apply_info: dict | None, chaos_dir: str, repo: str) -> dict:
    files_changed = list(apply_info.get("files_changed", [])) if apply_info else []
    check = "not_run"
    if apply_info:
        check = apply_info.get("check") or ("ok" if apply_info.get("ok") else "fail")
    applied = bool(apply_info and apply_info.get("ok"))
    cands = []
    if candidate.get("ok"):
        # 联动单元：shift 与 hard 是**同一次决策**，两条 param 条目共享 evidence/counts/applied。
        linkage = {"rule": "new_hard = min(0.95, round(new_shift+0.15, 2))",
                   "hard_gap": HARD_GAP_MIN, "hard_cap": HARD_CAP,
                   "shift_new": candidate["shift"]["new"], "hard_new": candidate["hard"]["new"]}
        for entry in (candidate["shift"], candidate["hard"]):
            cands.append({
                "param": entry["param"],
                "old": entry["old"],
                "new": entry["new"],
                "evidence": candidate["evidence"],
                "counts": candidate["counts"],
                "applied": applied,
                "linkage": linkage,
            })
    refused = []
    if candidate.get("refused"):
        refused.append({"reason": candidate["refused"],
                        "detail": candidate.get("reason", ""),
                        "counts": candidate["counts"]})
    for x in evidence.get("ignored", []):
        refused.append({"reason": "忽略的证据", "detail": "%s（%s）：%s" % (
            x.get("run_id"), x.get("file"), x.get("reason"))})
    for s in skipped:
        refused.append({"reason": "跳过文件", "detail": "%s：%s" % (s.get("file"), s.get("reason"))})
    shift_ok, hard_ok, _sv, _hv = _four_consistency(four)
    return {
        "schema": "lingshu/chaos-calibration/v1",
        "generated_at": _iso(),
        "repo": repo,
        "chaos_dir": chaos_dir,
        "param": PARAM,
        "current_value": round(float(current_shift), 4),
        "current_values": {"shift": round(float(current_shift), 4),
                           "hard": round(float(current_hard), 4)},
        "current_provenance": "measured" if _is_measured(repo) else "guess",
        "hard_ratio": round(float(current_hard), 4),
        "linkage": {"rule": "B（H0 裁决，ADR-011）：new_shift = max(0.40, "
                            "floor((min(degraded_at_ratio)-0.05)*100)/100); "
                            "new_hard = min(0.95, round(new_shift+0.15, 2))",
                    "hard_gap": HARD_GAP_MIN, "hard_cap": HARD_CAP,
                    "four_copies_consistent": {"shift": shift_ok, "hard": hard_ok}},
        "mode_policy": "仅 mode==host 参与；engine 模式的 degraded 证据永不参与标定",
        "thresholds": {"min_degraded": MIN_DEGRADED, "min_control": MIN_CONTROL,
                       "ratio_floor": MIN_RATIO_FLOOR, "hard_gap_min": HARD_GAP_MIN,
                       "hard_cap": HARD_CAP, "degrade_margin": DEGRADE_MARGIN},
        "counts": {"runs_total": len(runs), "runs_host": sum(1 for r in runs if r["mode"] == "host"),
                   "runs_engine": sum(1 for r in runs if r["mode"] == "engine"),
                   "degraded": len(evidence["degraded"]), "control": len(evidence["controls"]),
                   "skipped_files": len(skipped)},
        "candidates": cands,
        "refused": refused,
        "files_changed": files_changed,
        "check_config_sync": check,
    }


def _is_measured(repo: str) -> bool:
    """rules control 段 shift 与 hard 两行是否都已标 provenance: measured。"""
    try:
        text = _read_text(os.path.join(repo, "rules", "decay-params.yaml"))
    except OSError:
        return False
    marked = 0
    for line in text.splitlines():
        if re.match(r"^\s*phaseShift(Ratio|HardRatio):", line) and "provenance: measured" in line:
            marked += 1
    return marked >= 2


# ── 6. apply ─────────────────────────────────────────────

def _four_consistent(four: dict) -> bool:
    """四处副本是否齐全、shift/hard 各自一致、且 hard > shift（apply 的前置条件）。"""
    shift_ok, hard_ok, shifts, hards = _four_consistency(four)
    if not (shift_ok and hard_ok):
        return False
    return hards[0] > shifts[0]


def apply_candidate(repo: str, candidate: dict, four: dict, evidence: dict,
                    date: str) -> dict:
    """8 值同步（4 文件 × shift/hard）+ 自检 + ADR/assumptions；失败全量回滚。"""
    old_s, new_s = candidate["shift"]["old"], candidate["shift"]["new"]
    old_h, new_h = candidate["hard"]["old"], candidate["hard"]["new"]
    if not _four_consistent(four):
        return {"ok": False, "check": "not_run", "files_changed": [], "rolled_back": [],
                "blocked": True,
                "error": "四处副本不一致或 hard<=shift（%s）：先修 check_config_sync 再做标定"
                         % json.dumps({k: v for k, v in four.items()}, ensure_ascii=False)}
    for rel, vals in four.items():
        if (abs(float(vals.get("shift")) - old_s) > 1e-9
                or abs(float(vals.get("hard")) - old_h) > 1e-9):
            return {"ok": False, "check": "not_run", "files_changed": [], "rolled_back": [],
                    "blocked": True,
                    "error": "%s 当前 shift/hard=%s/%s 与规则旧值 %s/%s 不一致"
                             % (rel, vals.get("shift"), vals.get("hard"),
                                _fmt(old_s), _fmt(old_h))}
    evidence_note = "E-M1 / decisions/009 / ADR-011（%s，host 退化 n=%d，对照 n=%d）" % (
        date, candidate["counts"]["degraded"], candidate["counts"]["control"])
    plans, errors = plan_edits(repo, old_s, new_s, old_h, new_h, evidence_note)
    if errors:
        return {"ok": False, "check": "not_run", "files_changed": [], "rolled_back": [],
                "blocked": True,
                "error": "配置改写前置检查失败：" + "；".join(errors)}
    originals = {}
    for rel, _kind in TARGET_FILES:
        originals[rel] = _read_text(os.path.join(repo, rel))
    assumptions_original = None
    decision_path = os.path.join(repo, DECISION_REL)
    try:
        assumptions_original = _read_text(os.path.join(repo, ASSUMPTIONS_REL))
        for rel, text in plans.items():
            _write_text(os.path.join(repo, rel), text)
        rc, out = run_check_config_sync(repo)
        if rc != 0:
            for rel, text in originals.items():
                _write_text(os.path.join(repo, rel), text)
            return {"ok": False, "check": "fail", "files_changed": [],
                    "rolled_back": [rel for rel, _k in TARGET_FILES],
                    "error": "check_config_sync 失败（exit %s），已回滚全部改动" % rc,
                    "check_output": out}
        decision_text = build_decision_markdown(repo, date, candidate, evidence, out, four)
        _write_text(decision_path, decision_text)
        append_assumption(repo, date, candidate, candidate["evidence"]["runs"])
    except Exception as exc:                     # 配置已写但 ADR/assumptions 失败：也要回滚
        for rel, text in originals.items():
            _write_text(os.path.join(repo, rel), text)
        try:
            if assumptions_original is not None:
                _write_text(os.path.join(repo, ASSUMPTIONS_REL), assumptions_original)
            if os.path.isfile(decision_path):
                os.remove(decision_path)
        except OSError:
            pass
        return {"ok": False, "check": "fail", "files_changed": [], "blocked": True,
                "rolled_back": [rel for rel, _k in TARGET_FILES],
                "error": "写 ADR/assumptions 失败，已回滚配置：%s" % exc}
    return {"ok": True, "check": "ok",
            "files_changed": [rel for rel, _k in TARGET_FILES] +
            [DECISION_REL.replace(os.sep, "/"), ASSUMPTIONS_REL.replace(os.sep, "/")],
            "rolled_back": [], "error": "", "check_output": out}


# ── 7. CLI ──────────────────────────────────────────────

def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    ap = argparse.ArgumentParser(
        description="混沌结果 → 相变双阈值（shift+hard 联动 B）标定；默认只出报告/候选，--apply 才改配置")
    ap.add_argument("--repo", default=ROOT, help="仓库根（默认本工具所在仓库；测试用副本）")
    ap.add_argument("--chaos-dir", default=None, help="混沌结果目录（默认 <repo>/acceptance/chaos）")
    ap.add_argument("--date", default=None, help="报告日期 YYYY-MM-DD（默认今天）")
    ap.add_argument("--out", default=None, help="报告路径（默认 <chaos-dir>/calibration-<date>.md）")
    ap.add_argument("--candidates-out", default=None,
                    help="候选 JSON 路径（默认 <chaos-dir>/provenance-candidates.json）")
    ap.add_argument("--apply", action="store_true",
                    help="证据达标时同步 8 个值（4 文件×shift/hard）+ check_config_sync + ADR/assumptions")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args(argv)

    repo = os.path.abspath(args.repo)
    chaos_dir = os.path.abspath(args.chaos_dir or os.path.join(repo, "acceptance", "chaos"))
    date = args.date or _dt.date.today().strftime("%Y-%m-%d")
    out = os.path.abspath(args.out) if args.out else os.path.join(chaos_dir, "calibration-%s.md" % date)
    cand_path = os.path.abspath(args.candidates_out) if args.candidates_out else \
        os.path.join(chaos_dir, CANDIDATES_NAME)

    rules_control = read_rule_control(repo)
    if PARAM_KEY not in rules_control or HARD_KEY not in rules_control:
        print("无法从 %s 解析 control.%s/%s，拒绝分析" % (
            os.path.join(repo, "rules", "decay-params.yaml"), PARAM_KEY, HARD_KEY), file=sys.stderr)
        return 2
    current_shift = float(rules_control[PARAM_KEY])
    current_hard = float(rules_control[HARD_KEY])
    four = read_four_values(repo)

    already_measured = _is_measured(repo)
    runs, skipped = load_runs(chaos_dir)
    quality_labels = summarize_quality_labels(runs)
    evidence = extract_evidence(runs, current_shift)
    candidate = build_candidate(current_shift, current_hard, evidence)

    apply_info = None
    if args.apply and already_measured:
        apply_info = {"ok": False, "check": "not_run", "files_changed": [], "rolled_back": [],
                      "error": "rules 中 shift/hard 已标 provenance: measured，拒绝重复 apply"}
        candidate = dict(candidate, ok=False, refused="已 measured，跳过",
                         reason="rules 中 shift/hard 已标 provenance: measured，拒绝重复 apply",
                         insufficient=False)
    elif args.apply:
        if not _four_consistent(four):
            # 四处不同源是**前置条件失败**：哪怕证据不足也要让运维注意到（exit 2）。
            apply_info = {"ok": False, "check": "not_run", "files_changed": [],
                          "rolled_back": [], "blocked": True,
                          "error": "四处副本不一致或 hard<=shift（%s）：先修 check_config_sync 再做标定"
                                   % json.dumps({k: v for k, v in four.items()}, ensure_ascii=False)}
        elif not candidate.get("ok"):
            apply_info = {"ok": False, "check": "not_run", "files_changed": [], "rolled_back": [],
                          "error": "证据不足或无可执行下调，零修改（见 §5）"}
        else:
            apply_info = apply_candidate(repo, candidate, four, evidence, date)

    report = render_report(runs, skipped, evidence, candidate, current_shift, current_hard,
                           four, apply_info, date, chaos_dir, repo, quality_labels)
    try:
        _write_text(out, report)
    except OSError as exc:
        print("写报告失败：%s" % exc, file=sys.stderr)
        return 2
    cand = render_candidates(runs, skipped, evidence, candidate, current_shift, current_hard,
                             four, apply_info, chaos_dir, repo)
    try:
        _write_text(cand_path, json.dumps(cand, ensure_ascii=False, indent=2) + "\n")
    except OSError as exc:
        print("写候选 JSON 失败：%s" % exc, file=sys.stderr)
        return 2

    if not args.quiet:
        print("LINGSHU 混沌标定 ｜ repo=%s ｜ chaos=%s" % (repo, chaos_dir))
        print("runs %d（host %d / engine %d）｜ degraded %d/%d ｜ 对照 %d/%d"
              % (len(runs), sum(1 for r in runs if r["mode"] == "host"),
                 sum(1 for r in runs if r["mode"] == "engine"),
                 len(evidence["degraded"]), MIN_DEGRADED,
                 len(evidence["controls"]), MIN_CONTROL))
        print("规则自动标签 %d 条（辅助观察，不参与门槛）" % quality_labels["total"])
        if candidate.get("ok"):
            print("候选（联动 B）：shift %s → %s ｜ hard %s → %s（%s）"
                  % (_fmt(current_shift), _fmt(candidate["shift"]["new"]),
                     _fmt(current_hard), _fmt(candidate["hard"]["new"]),
                     "已 apply" if apply_info and apply_info.get("ok") else "未 apply"))
        elif candidate.get("insufficient"):
            print("结论：证据不足，保持 guess（%s）" % candidate["reason"])
        else:
            print("结论：%s（%s）" % (candidate.get("refused"), candidate.get("reason")))
        if apply_info and not apply_info.get("ok"):
            print("apply：%s" % apply_info.get("error"))
        print("报告：%s" % out)
        print("候选：%s" % cand_path)

    if apply_info is not None and not apply_info.get("ok") and args.apply:
        # check 失败/回滚、配置漂移等"前置条件不满足" → exit 2（运维必须注意）；
        # 证据不足、无可执行下调、已 measured 属于安全拒绝 → exit 0。
        if apply_info.get("blocked") or apply_info.get("check") == "fail" \
                or apply_info.get("rolled_back"):
            return 2
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
