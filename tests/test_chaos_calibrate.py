# -*- coding: utf-8 -*-
"""LINGSHU 验收 · 混沌结果 → 相变双阈值联动标定（tools/chaos_calibrate.py）

    python tests/test_chaos_calibrate.py

H0 裁决 B（ADR-011）：shift 按真实退化证据下调，hard 跟随并保留 0.15 间距宽度、上限 0.95。
本测试覆盖：

- 读取：坏 JSON / 缺失 run_id / mode 非法 / 隐藏 `.last.json` / 候选自身 / 重复 run_id；
- 证据过滤：**只认 mode==host**；engine 模式的 degraded 证据永不参与；
  keyword_hit 非布尔、退化点缺失、退化点不低于当前阈值、true 却带退化标记；
- 门槛：degraded≥3 + 对照≥3 才产出候选；不足 → 零修改 + 报告"证据不足，保持 guess"；
- 联动公式：new_shift=max(0.40, floor((min-0.05)*100)/100)、
  new_hard=min(0.95, round(new_shift+0.15,2))、hard 上限 0.95、shift 无下调空间时不 apply；
- `--apply`：**8 值同步（4 文件 × shift/hard）** + 真实 check_config_sync 0 fail + ADR + E-M1；
- 回滚：check 失败 → 8 值逐字节还原、ADR 不存在、assumptions 不变、exit 2；
- 配置漂移（shift 或 hard 任一处）→ 拒绝 apply、exit 2、零修改；
- 防降门槛：MIN_*/HARD_CAP 写死，CLI 不提供覆盖参数；
- 不碰真实仓库：所有写操作都在 tempfile 副本仓库里；真实 rules 值前后不变。
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "tools"))

import chaos_calibrate as cal  # noqa: E402

REAL_RULES = os.path.join(ROOT, "rules", "decay-params.yaml")
REAL_ENGINE = os.path.join(ROOT, "engine")
REAL_PATCH = os.path.join(ROOT, "plugin", "dsh-lingshu", "cordis.patch.yml")
REAL_JS = os.path.join(ROOT, "plugin", "dsh-lingshu", "control.js")
REAL_CHECKER = os.path.join(ROOT, "tools", "check_config_sync.py")
REAL_ASSUMPTIONS = os.path.join(ROOT, "assumptions", "已验证.md")
TARGET_RELS = [rel for rel, _kind in cal.TARGET_FILES]
DECISION_REL = os.path.join("decisions", "009-相变阈值真实标定.md")

PASS, FAIL = [], []


def check(name: str, cond: bool, detail: str = "") -> None:
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)) if detail else ""))


# ── tempfile 副本仓库 ──────────────────────────────────────

def _digest(path: str) -> str:
    return hashlib.sha256(open(path, "rb").read()).hexdigest()


def _sub(pattern: str, repl, text: str, flags=0) -> str:
    new, n = re.subn(pattern, repl, text, count=1, flags=flags)
    if n != 1:
        raise AssertionError("测试夹具替换失败：%s" % pattern)
    return new


def _set_repo_values(repo: str, shift: float, hard: float) -> None:
    """把副本仓库四处控制值改成指定值（测试夹具；仅作用于 temp 目录）。"""
    rules = os.path.join(repo, "rules", "decay-params.yaml")
    engine = os.path.join(repo, "engine", "config.py")
    patch = os.path.join(repo, "plugin", "dsh-lingshu", "cordis.patch.yml")
    js = os.path.join(repo, "plugin", "dsh-lingshu", "control.js")
    for path, shift_pat, hard_pat, flags in [
        (rules, r"(^\s*phaseShiftRatio:\s*)[\d.]+", r"(^\s*phaseShiftHardRatio:\s*)[\d.]+", re.M),
        (patch, r"(^\s*phaseShiftRatio:\s*)[\d.]+", r"(^\s*phaseShiftHardRatio:\s*)[\d.]+", re.M),
        (engine, r'("phaseShiftRatio":\s*)[\d.]+', r'("phaseShiftHardRatio":\s*)[\d.]+', 0),
        (js, r"(^\s*phaseShiftRatio:\s*)[\d.]+(?=,)",
             r"(^\s*phaseShiftHardRatio:\s*)[\d.]+(?=,)", re.M),
    ]:
        text = io.open(path, encoding="utf-8").read()
        text = _sub(shift_pat, lambda m: m.group(1) + cal._fmt(shift), text, flags)
        text = _sub(hard_pat, lambda m: m.group(1) + cal._fmt(hard), text, flags)
        io.open(path, "w", encoding="utf-8", newline="\n").write(text)


def make_repo(tmp: str, shift: float = 0.80, hard: float = 0.90) -> str:
    """造一个可跑真实 check_config_sync.py 的最小副本仓库。"""
    repo = os.path.join(tmp, "repo")
    shutil.copytree(REAL_ENGINE, os.path.join(repo, "engine"),
                    ignore=shutil.ignore_patterns("__pycache__"))
    for d in ("rules", "tools", os.path.join("plugin", "dsh-lingshu"),
              "assumptions", "decisions"):
        os.makedirs(os.path.join(repo, d), exist_ok=True)
    shutil.copy2(REAL_RULES, os.path.join(repo, "rules", "decay-params.yaml"))
    shutil.copy2(REAL_PATCH, os.path.join(repo, "plugin", "dsh-lingshu", "cordis.patch.yml"))
    shutil.copy2(REAL_JS, os.path.join(repo, "plugin", "dsh-lingshu", "control.js"))
    shutil.copy2(REAL_CHECKER, os.path.join(repo, "tools", "check_config_sync.py"))
    shutil.copy2(REAL_ASSUMPTIONS, os.path.join(repo, "assumptions", "已验证.md"))
    _set_repo_values(repo, shift, hard)
    return repo


def repo_check(repo: str) -> tuple[int, str]:
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    p = subprocess.run([sys.executable, os.path.join(repo, "tools", "check_config_sync.py")],
                       cwd=repo, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                       text=True, encoding="utf-8", errors="replace", timeout=180)
    return p.returncode, p.stdout or ""


def write_run(chaos_dir: str, run_id: str, *, mode: str = "host",
              keyword_hit=False, degraded_at_ratio=0.62, ratio=None,
              turn=40, question="总结前面 50 段内容里的密钥代号", expected="蓝鲸",
              answer="（回答缺失）", quality_labels=None) -> str:
    os.makedirs(chaos_dir, exist_ok=True)
    if ratio is None:
        ratio = degraded_at_ratio if degraded_at_ratio is not None else 0.5
    obs = [{"turn": t, "ratio": round(0.02 * t, 4), "phase": "normal", "anchor_required": False}
           for t in range(1, turn)] + \
          [{"turn": turn, "ratio": ratio, "phase": "shift", "anchor_required": True}]
    obj = {
        "run_id": run_id, "started_at": "2026-10-27T02:00:00+08:00",
        "finished_at": "2026-10-27T02:05:00+08:00", "mode": mode,
        "scenarios": {
            "long_context_flood": {
                "turns": turn, "phase_observations": obs,
                "max_ratio": max(o["ratio"] for o in obs),
                "phase_trigger_turns": [turn], "anchor_turns": [turn],
                "key_question": {"turn": turn, "question": question,
                                 "expected_keyword": expected, "answer": answer,
                                 "keyword_hit": keyword_hit},
                "degraded_at_ratio": degraded_at_ratio,
            },
            "memory_conflict": {"beta_hit": True, "alpha_leak": False, "recall_ids": []},
            "emotion_jailbreak": {"pad_baseline": {}, "saturated": {},
                                  "stpa": {"blocked": False, "ucas": [], "sources": []}},
            "variety": {"fallback_triggered": False, "drop_domains": []},
        },
        "summary": {"scenarios_run": 2, "phase_trigger_count": 1,
                    "attacks_blocked": 0, "fallback_count": 0},
    }
    if quality_labels is not None:                 # 辅助字段：只读展示，不参与证据/门槛
        obj["quality_labels"] = quality_labels
    path = os.path.join(chaos_dir, run_id + ".json")
    io.open(path, "w", encoding="utf-8", newline="\n").write(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
    return path


def write_three_plus_three(chaos_dir: str, degraded=None, controls=None) -> None:
    degraded = degraded or [0.615, 0.64, 0.66]
    controls = controls or [0.35, 0.40, 0.45]
    for i, r in enumerate(degraded):
        write_run(chaos_dir, "host-deg-%d" % i, keyword_hit=False,
                  degraded_at_ratio=r, ratio=r, turn=40 + i)
    for i, r in enumerate(controls):
        write_run(chaos_dir, "host-ctrl-%d" % i, keyword_hit=True,
                  degraded_at_ratio=None, ratio=r, turn=40 + i)


def snapshot(repo: str) -> dict:
    return {rel: _digest(os.path.join(repo, rel)) for rel in TARGET_RELS}


def read_cand(chaos_dir: str) -> dict:
    return json.load(io.open(os.path.join(chaos_dir, cal.CANDIDATES_NAME), encoding="utf-8"))


def read_report(chaos_dir: str, date: str) -> str:
    return io.open(os.path.join(chaos_dir, "calibration-%s.md" % date), encoding="utf-8").read()


def _values(repo: str) -> dict:
    """取四处 (shift, hard) 扁平视图，便于断言 8 值。"""
    four = cal.read_four_values(repo)
    out = {}
    for rel in TARGET_RELS:
        out[rel] = (four[rel]["shift"], four[rel]["hard"])
    return out


# ── 1. 读取与过滤 ────────────────────────────────────────

def test_load_runs_filters() -> None:
    print("\n[读取] 坏 JSON / 缺字段 / 隐藏文件 / 重复 run_id")
    with tempfile.TemporaryDirectory() as tmp:
        chaos = os.path.join(tmp, "chaos")
        write_run(chaos, "host-1", keyword_hit=False, degraded_at_ratio=0.6)
        write_run(chaos, "engine-1", mode="engine", keyword_hit=False, degraded_at_ratio=0.3)
        io.open(os.path.join(chaos, "broken.json"), "w", encoding="utf-8").write("{not json")
        io.open(os.path.join(chaos, "missing-id.json"), "w", encoding="utf-8").write(
            json.dumps({"mode": "host"}))
        io.open(os.path.join(chaos, "bad-mode.json"), "w", encoding="utf-8").write(
            json.dumps({"run_id": "x", "mode": "synthetic"}))
        io.open(os.path.join(chaos, ".last.json"), "w", encoding="utf-8").write(
            json.dumps({"run_id": "last", "mode": "engine"}))
        io.open(os.path.join(chaos, "provenance-candidates.json"), "w", encoding="utf-8").write("{}")
        io.open(os.path.join(chaos, "readme.md"), "w", encoding="utf-8").write("not a run")
        shutil.copy(os.path.join(chaos, "host-1.json"), os.path.join(chaos, "host-1-copy.json"))
        runs, skipped = cal.load_runs(chaos)
        ids = [r["run_id"] for r in runs]
        check("只收 host/engine 且 schema 合法的 run", ids == ["engine-1", "host-1"], ids)
        check("重复 run_id 只保留第一份", sum(1 for r in runs if r["run_id"] == "host-1") == 1)
        reasons = " ".join(s["reason"] for s in skipped)
        check("坏 JSON 被跳过并记录", "坏 JSON" in reasons)
        check("缺 run_id 被跳过", any("run_id" in s["reason"] for s in skipped))
        check("非法 mode 被跳过", any("mode" in s["reason"] for s in skipped))
        check("隐藏文件（.last.json）不被当 run", ".last.json" not in ids)
        check("候选文件自身不被当 run", "provenance-candidates.json" not in ids)
        check("非 JSON 文件忽略", any(s.get("file") == "readme.md" for s in skipped) is False)
        check("重复 run_id 写入 skipped 说明", any("重复 run_id" in s["reason"] for s in skipped))


def test_evidence_host_only() -> None:
    print("\n[证据] 只认 host；engine 模式永不参与；keyword_hit 判定")
    with tempfile.TemporaryDirectory() as tmp:
        chaos = os.path.join(tmp, "chaos")
        write_run(chaos, "h-deg", keyword_hit=False, degraded_at_ratio=0.62, ratio=0.62)
        write_run(chaos, "h-ctrl", keyword_hit=True, degraded_at_ratio=None, ratio=0.45)
        write_run(chaos, "e-deg", mode="engine", keyword_hit=False, degraded_at_ratio=0.30, ratio=0.30)
        runs, _ = cal.load_runs(chaos)
        ev = cal.extract_evidence(runs, current_shift=0.80)
        check("host 退化证据被收（1 条）", len(ev["degraded"]) == 1
              and ev["degraded"][0]["run_id"] == "h-deg", ev["degraded"])
        check("host 对照被收（1 条）", len(ev["controls"]) == 1
              and ev["controls"][0]["run_id"] == "h-ctrl")
        check("engine 模式的 degraded 不进入证据",
              all(d["run_id"] != "e-deg" for d in ev["degraded"]))
        check("engine 模式 run 记入 ignored", any(x["run_id"] == "e-deg" for x in ev["ignored"]))
        check("engine 带退化标记会显式警告", any("engine" in w and "e-deg" in w for w in ev["warnings"]),
              ev["warnings"])
        check("对照 ratio 取 key_question 所在轮", abs(ev["controls"][0]["ratio"] - 0.45) < 1e-9)

    with tempfile.TemporaryDirectory() as tmp:
        chaos = os.path.join(tmp, "chaos")
        write_run(chaos, "h-contradict", keyword_hit=True, degraded_at_ratio=0.5, ratio=0.5)
        write_run(chaos, "h-noloc", keyword_hit=False, degraded_at_ratio=None, ratio=0.5)
        path = write_run(chaos, "h-bad-hit", keyword_hit=False, degraded_at_ratio=0.5)
        obj = json.load(io.open(path, encoding="utf-8"))
        obj["scenarios"]["long_context_flood"]["key_question"]["keyword_hit"] = "false"
        io.open(path, "w", encoding="utf-8").write(json.dumps(obj, ensure_ascii=False))
        runs, _ = cal.load_runs(chaos)
        ev = cal.extract_evidence(runs, current_shift=0.80)
        check("keyword_hit=true + degraded 非空：只计对照", len(ev["controls"]) == 1
              and len(ev["degraded"]) == 0)
        check("矛盾数据有警告", any("自相矛盾" in w for w in ev["warnings"]))
        check("keyword_hit 非布尔被跳过", any(x["run_id"] == "h-bad-hit" for x in ev["ignored"]))
        check("缺 degraded_at_ratio 的 false 被跳过", any(x["run_id"] == "h-noloc" for x in ev["ignored"]))


def test_degraded_above_threshold_excluded() -> None:
    print("\n[证据] 退化点不低于当前 shift → 不构成下调依据")
    with tempfile.TemporaryDirectory() as tmp:
        chaos = os.path.join(tmp, "chaos")
        write_run(chaos, "h-high", keyword_hit=False, degraded_at_ratio=0.95, ratio=0.95)
        runs, _ = cal.load_runs(chaos)
        ev = cal.extract_evidence(runs, current_shift=0.70)
        check("ratio 0.95 ≥ 阈值 0.70 的退化不计入", len(ev["degraded"]) == 0)
        check("记入 ignored 并说明原因", any("不低于当前阈值" in x["reason"] for x in ev["ignored"]))


# ── 2. 门槛与联动公式 ────────────────────────────────────

def test_thresholds_locked() -> None:
    print("\n[门槛] 写死、不可通过 CLI 降低")
    check("MIN_DEGRADED = 3", cal.MIN_DEGRADED == 3, cal.MIN_DEGRADED)
    check("MIN_CONTROL = 3", cal.MIN_CONTROL == 3, cal.MIN_CONTROL)
    check("shift 下限 0.40 / 间距宽度 0.15 / 前移 0.05 / hard 上限 0.95",
          cal.MIN_RATIO_FLOOR == 0.40 and cal.HARD_GAP_MIN == 0.15
          and cal.DEGRADE_MARGIN == 0.05 and cal.HARD_CAP == 0.95)
    try:
        cal.main(["--repo", ROOT, "--min-degraded", "1"])
        ok = False
    except SystemExit:
        ok = True
    check("CLI 没有 --min-degraded 之类降门槛开关", ok)


def test_linkage_math() -> None:
    print("\n[联动] floor / shift 下限 0.40 / hard=shift+0.15 / hard 上限 0.95")
    r = cal.suggest_linked_thresholds(0.80, 0.90, 0.615)
    check("new_shift=0.56（floor((0.615-0.05)*100)/100）",
          abs(r["raw_shift"] - 0.56) < 1e-9 and abs(r["new_shift"] - 0.56) < 1e-9, r)
    check("new_hard=0.71（= new_shift+0.15）", abs(r["new_hard"] - 0.71) < 1e-9, r)
    check("联动不变量：hard>shift 且 hard<=0.95",
          r["invariants"]["hard_gt_shift"] and r["invariants"]["hard_le_cap"])
    r = cal.suggest_linked_thresholds(0.90, 0.95, 0.617)
    check("floor 截断：0.617 → 0.56", abs(r["new_shift"] - 0.56) < 1e-9, r)
    r = cal.suggest_linked_thresholds(0.70, 0.85, 0.30)
    check("shift 绝对下限 0.40 生效（raw=0.25）", abs(r["new_shift"] - 0.40) < 1e-9, r)
    check("shift 触底时 hard=0.55（仍保留 0.15 宽度）", abs(r["new_hard"] - 0.55) < 1e-9, r)
    r = cal.suggest_linked_thresholds(0.99, 0.99, 0.93)
    check("hard 上限 0.95 生效（new_shift=0.88 时 0.88+0.15 被截到 0.95）",
          abs(r["new_shift"] - 0.88) < 1e-9 and abs(r["new_hard"] - 0.95) < 1e-9, r)
    check("截断后 hard 仍 > shift", r["new_hard"] > r["new_shift"])


def test_candidate_requires_both_counts() -> None:
    print("\n[候选] 门槛不足 → 无候选；达标 → shift+hard 联动候选")
    with tempfile.TemporaryDirectory() as tmp:
        chaos = os.path.join(tmp, "chaos")
        write_run(chaos, "h1", keyword_hit=False, degraded_at_ratio=0.6, ratio=0.6)
        write_run(chaos, "h2", keyword_hit=False, degraded_at_ratio=0.62, ratio=0.62)
        for i in range(3):
            write_run(chaos, "c%d" % i, keyword_hit=True, degraded_at_ratio=None, ratio=0.4)
        runs, _ = cal.load_runs(chaos)
        ev = cal.extract_evidence(runs, 0.80)
        cand = cal.build_candidate(0.80, 0.90, ev)
        check("degraded 2/3 时不出候选", cand["ok"] is False and cand["insufficient"] is True)
        check("拒绝原因写明样本数", "degraded 2/3" in cand["reason"], cand["reason"])

    with tempfile.TemporaryDirectory() as tmp:
        chaos = os.path.join(tmp, "chaos")
        write_three_plus_three(chaos)
        runs, _ = cal.load_runs(chaos)
        ev = cal.extract_evidence(runs, 0.80)
        cand = cal.build_candidate(0.80, 0.90, ev)
        check("degraded 3 + 对照 3 → 出候选", cand["ok"] is True)
        check("shift：0.80 → 0.56", cand["shift"]["old"] == 0.80 and cand["shift"]["new"] == 0.56,
              cand.get("shift"))
        check("hard：0.90 → 0.71（跟随 shift+0.15）",
              cand["hard"]["old"] == 0.90 and cand["hard"]["new"] == 0.71, cand.get("hard"))
        check("min_degraded_at_ratio=0.615",
              abs(cand["evidence"]["min_degraded_at_ratio"] - 0.615) < 1e-9)
        check("公式中间量含 new_shift/new_hard/cap",
              {"new_shift", "new_hard", "hard_cap"} <= set(cand["evidence"]["formula_result"]))
        check("证据里记录联动规则 B",
              "B" in cand["evidence"]["linkage_rule"] and "0.15" in cand["evidence"]["linkage_rule"])

    # shift 无下调空间：当前已在下限 0.40，退化证据<0.40 也只能算到 0.40 → 不 apply
    with tempfile.TemporaryDirectory() as tmp:
        chaos = os.path.join(tmp, "chaos")
        write_three_plus_three(chaos, degraded=[0.30, 0.32, 0.34])
        runs, _ = cal.load_runs(chaos)
        ev = cal.extract_evidence(runs, 0.40)
        cand = cal.build_candidate(0.40, 0.55, ev)
        check("new_shift=当前 0.40 → 无下调空间、不产出候选",
              cand["ok"] is False and cand["insufficient"] is False, cand)
        check("拒绝原因写明 new_shift 不低于当前 shift",
              "不低于当前 shift" in cand["reason"], cand.get("reason"))


# ── 3. 默认行为：证据不足零修改 ───────────────────────────

def test_insufficient_zero_modification() -> None:
    print("\n[默认] 证据不足：零修改 + 报告写“证据不足，保持 guess”")
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, shift=0.80, hard=0.90)
        chaos = os.path.join(tmp, "chaos")
        write_run(chaos, "h1", keyword_hit=False, degraded_at_ratio=0.62, ratio=0.62)
        write_run(chaos, "h2", keyword_hit=False, degraded_at_ratio=0.64, ratio=0.64)
        write_run(chaos, "c1", keyword_hit=True, degraded_at_ratio=None, ratio=0.4)
        before = snapshot(repo)
        rc = cal.main(["--repo", repo, "--chaos-dir", chaos, "--date", "2026-10-27",
                       "--apply", "--quiet"])
        check("证据不足时 --apply 也是安全拒绝（exit 0）", rc == 0, rc)
        check("四个配置文件逐字节未变", snapshot(repo) == before)
        check("没有生成 decisions/009",
              not os.path.isfile(os.path.join(repo, DECISION_REL)))
        report = read_report(chaos, "2026-10-27")
        check("报告含“证据不足，保持 guess”", "证据不足，保持 guess" in report)
        check("报告含联动公式", "new_hard = min(0.95" in report)
        check("报告引用 ADR-011", "ADR-011" in report)
        cand = read_cand(chaos)
        check("候选 JSON：candidates=[]", cand["candidates"] == [])
        check("候选 JSON：refused 有原因", bool(cand["refused"]))
        check("候选 JSON：check_config_sync=not_run", cand["check_config_sync"] == "not_run")
        check("候选 JSON：files_changed=[]", cand["files_changed"] == [])
        check("候选 JSON：degraded 计数 2", cand["counts"]["degraded"] == 2, cand["counts"])
        check("候选 JSON：联动规则 B/上限 0.95 写入 linkage",
              cand["linkage"]["hard_cap"] == 0.95 and "B" in cand["linkage"]["rule"])


# ── 4. --apply：8 值同步 + ADR + assumptions ─────────────

def test_apply_eight_values_sync() -> None:
    print("\n[apply] 8 值同步（4 文件×shift/hard）+ check ok + ADR + E-M1")
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, shift=0.80, hard=0.90)
        chaos = os.path.join(tmp, "chaos")
        write_three_plus_three(chaos)      # min degraded 0.615 → shift .56 / hard .71
        rc0, out0 = repo_check(repo)
        check("副本仓库初始 check_config_sync 0 fail", rc0 == 0, out0[-300:])
        rc = cal.main(["--repo", repo, "--chaos-dir", chaos, "--date", "2026-10-27",
                       "--apply", "--quiet"])
        check("apply 成功 exit 0", rc == 0, rc)
        values = _values(repo)
        check("四处全部同步为 shift=0.56 / hard=0.71（8 个值）",
              all(v == (0.56, 0.71) for v in values.values()), values)
        for rel in TARGET_RELS:
            text = io.open(os.path.join(repo, rel), encoding="utf-8").read()
            check("%s 的 shift/hard 都写入新值" % rel,
                  "0.56" in text and "0.71" in text, rel)
        rules = io.open(os.path.join(repo, "rules", "decay-params.yaml"), encoding="utf-8").read()
        check("rules shift/hard 两行都有 provenance: measured",
              len(re.findall(r"phaseShift(Ratio|HardRatio):.*provenance: measured", rules)) == 2)
        check("rules 注释带依据 E-M1 与 ADR-011", "E-M1" in rules and "ADR-011" in rules)
        check("control 段整体 provenance 仍为 guess（其余参数未标定）",
              "provenance: guess" in rules)
        rc2, out2 = repo_check(repo)
        check("apply 后 check_config_sync 0 fail", rc2 == 0, out2[-300:])
        decision = os.path.join(repo, DECISION_REL)
        check("生成 decisions/009", os.path.isfile(decision))
        # 审计修复（A4 §6）：配置漂移时 apply 会**正确拒绝**、不写 ADR；原代码在此
        # 无条件读文件，FileNotFoundError 会掩盖后续真实 FAIL 清单（fixture 健壮性缺陷）。
        dtext = io.open(decision, encoding="utf-8").read() if os.path.isfile(decision) else ""
        check("ADR 记录 shift 旧→新", "0.8 → 0.56" in dtext, dtext[:400])
        check("ADR 记录 hard 旧→新", "0.9 → 0.71" in dtext)
        check("ADR 记录联动规则 B/ADR-011", "ADR-011" in dtext and "联动" in dtext)
        check("ADR 列出数据 run_id", "host-deg-0" in dtext)
        check("ADR 有回滚方式（8 个值）", "回滚" in dtext and "8 个值" in dtext)
        assumptions = io.open(os.path.join(repo, "assumptions", "已验证.md"),
                              encoding="utf-8").read()
        check("assumptions 追加 E-M1", "E-M1" in assumptions)
        check("E-M1 同时记录 shift 与 hard",
              "0.8→0.56" in assumptions and "0.9→0.71" in assumptions, assumptions[-600:])
        check("assumptions 原文仍在", "已验证的假设" in assumptions)
        cand = read_cand(chaos)
        check("候选 JSON：两条 param 条目（shift+hard）",
              len(cand["candidates"]) == 2
              and {c["param"] for c in cand["candidates"]} ==
              {"control.phaseShiftRatio", "control.phaseShiftHardRatio"}, cand["candidates"])
        check("候选 JSON：applied=true", all(c["applied"] for c in cand["candidates"]))
        check("候选 JSON：check_config_sync=ok", cand["check_config_sync"] == "ok")
        check("候选 JSON：files_changed 含四处配置 + ADR + assumptions",
              len(cand["files_changed"]) == 6, cand["files_changed"])
        check("候选 JSON：current_provenance=measured", cand["current_provenance"] == "measured")
        report = read_report(chaos, "2026-10-27")
        check("报告记录已 apply 且是 8 个值", "8 个值" in report and "check_config_sync.py" in report)

        # 第二次 apply：已 measured → 拒绝重复，配置不变
        before = snapshot(repo)
        rc3 = cal.main(["--repo", repo, "--chaos-dir", chaos, "--date", "2026-10-27",
                        "--apply", "--quiet"])
        check("重复 apply exit 0 且不做修改", rc3 == 0 and snapshot(repo) == before)
        cand3 = read_cand(chaos)
        check("重复 apply 被拒绝并写入 refused",
              any("已 measured" in r["reason"] for r in cand3["refused"]), cand3["refused"])


def test_apply_rollback_on_check_fail() -> None:
    print("\n[apply] check_config_sync 失败 → 8 值全量回滚 + exit 2")
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, shift=0.80, hard=0.90)
        chaos = os.path.join(tmp, "chaos")
        write_three_plus_three(chaos)
        before = snapshot(repo)
        assumptions_before = io.open(os.path.join(repo, "assumptions", "已验证.md"),
                                    encoding="utf-8").read()
        orig = cal.run_check_config_sync
        cal.run_check_config_sync = lambda repo_path: (1, "SIMULATED FAIL: phaseShiftRatio drift")
        try:
            rc = cal.main(["--repo", repo, "--chaos-dir", chaos, "--date", "2026-10-27",
                           "--apply", "--quiet"])
        finally:
            cal.run_check_config_sync = orig
        check("check 失败 → exit 2", rc == 2, rc)
        check("四个配置文件 8 个值全部回滚（逐字节一致）", snapshot(repo) == before)
        check("未生成 decisions/009",
              not os.path.isfile(os.path.join(repo, DECISION_REL)))
        check("assumptions 未被修改",
              io.open(os.path.join(repo, "assumptions", "已验证.md"),
                      encoding="utf-8").read() == assumptions_before)
        cand = read_cand(chaos)
        check("候选 JSON：check_config_sync=fail", cand["check_config_sync"] == "fail")
        check("候选 JSON：files_changed=[]（已回滚）", cand["files_changed"] == [])
        check("报告写明回滚", "回滚" in read_report(chaos, "2026-10-27"))
        rc2, out2 = repo_check(repo)
        check("回滚后 check_config_sync 仍 0 fail", rc2 == 0, out2[-300:])


def test_apply_refuses_drifted_copies() -> None:
    print("\n[apply] 四处不同源（shift 或 hard）→ 拒绝 apply（exit 2、零修改）")
    for field, value in (("shift", "0.76"), ("hard", "0.88")):
        with tempfile.TemporaryDirectory() as tmp:
            repo = make_repo(tmp, shift=0.80, hard=0.90)
            chaos = os.path.join(tmp, "chaos")
            write_three_plus_three(chaos)
            rules = os.path.join(repo, "rules", "decay-params.yaml")
            text = io.open(rules, encoding="utf-8").read()
            key = "phaseShiftRatio" if field == "shift" else "phaseShiftHardRatio"
            text = re.sub(r"(^\s*%s:\s*)[\d.]+" % key, r"\g<1>" + value, text,
                          count=1, flags=re.M)
            io.open(rules, "w", encoding="utf-8", newline="\n").write(text)
            before = snapshot(repo)
            rc = cal.main(["--repo", repo, "--chaos-dir", chaos, "--date", "2026-10-27",
                           "--apply", "--quiet"])
            check("rules 单改 %s → apply 拒绝并 exit 2" % field, rc == 2, rc)
            check("%s 漂移时四个文件逐字节未变" % field, snapshot(repo) == before)
            cand = read_cand(chaos)
            check("%s 漂移时 check_config_sync=not_run" % field,
                  cand["check_config_sync"] == "not_run")
            check("%s 漂移时报告写安全拒绝" % field,
                  "未执行任何修改" in read_report(chaos, "2026-10-27"))


# ── 4b. 辅助观察：规则自动标签（只读展示，不参与门槛）───────

def test_quality_labels_auxiliary_only() -> None:
    print("\n[辅助观察] quality_labels 只读展示，不参与门槛/候选/apply")
    check("闭集 kind 与 analyze_shadow 同源（不复制闭集）",
          tuple(cal.QUALITY_LABEL_KINDS) == ("wrong", "missing", "should_not_recall",
                                             "drift", "overreach"), cal.QUALITY_LABEL_KINDS)
    check("阈值仍写死 3/3（标签通道未改门槛）",
          cal.MIN_DEGRADED == 3 and cal.MIN_CONTROL == 3)

    # 汇总：合法/坏数据混跑，坏数据跳过不猜
    with tempfile.TemporaryDirectory() as tmp:
        chaos = os.path.join(tmp, "chaos")
        write_run(chaos, "h1", quality_labels={"total": 2,
                                              "by_kind": {"wrong": 1, "missing": 1}})
        write_run(chaos, "h2", quality_labels={"total": 1, "by_kind": {"drift": 1}})
        write_run(chaos, "h3", quality_labels="坏数据")
        write_run(chaos, "h4", quality_labels={"total": 3,
                                               "by_kind": {"wrong": "x", "bogus": 2,
                                                           "overreach": 1}})
        write_run(chaos, "h5")
        runs, _ = cal.load_runs(chaos)
        check("load_runs 保留 quality_labels 原始字段（不改既有字段）",
              any(r["run_id"] == "h1" and isinstance(r["quality_labels"], dict)
                  for r in runs))
        ql = cal.summarize_quality_labels(runs)
        check("有标签的 run 数 3 / 格式不可用 1",
              ql["runs_with_labels"] == 3 and ql["invalid_runs"] == 1, ql)
        check("total 合并 = 2+1+3 = 6", ql["total"] == 6, ql["total"])
        check("by_kind 按五类闭集合并",
              ql["by_kind"] == {"wrong": 1, "missing": 1, "should_not_recall": 0,
                                "drift": 1, "overreach": 1}, ql["by_kind"])
        check("闭集外 kind / 非数值字段计入 ignored=2（不静默合并）",
              ql["ignored_fields"] == 2, ql["ignored_fields"])
        check("by_kind 合计校验值 = 4", ql["by_kind_sum"] == 4, ql["by_kind_sum"])
        check("present=True", ql["present"] is True)

    # 标签不改变 candidate：同一批 run，有/无标签各算一次
    with tempfile.TemporaryDirectory() as tmp_a, tempfile.TemporaryDirectory() as tmp_b:
        chaos_a = os.path.join(tmp_a, "chaos")
        chaos_b = os.path.join(tmp_b, "chaos")
        write_three_plus_three(chaos_a)
        write_three_plus_three(chaos_b)
        for name in ("host-deg-0.json", "host-ctrl-2.json"):
            path = os.path.join(chaos_b, name)
            obj = json.load(io.open(path, encoding="utf-8"))
            obj["quality_labels"] = {"total": 2, "by_kind": {"wrong": 2}}
            io.open(path, "w", encoding="utf-8", newline="\n").write(
                json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
        runs_a, _ = cal.load_runs(chaos_a)
        runs_b, _ = cal.load_runs(chaos_b)
        ev_a = cal.extract_evidence(runs_a, 0.80)
        ev_b = cal.extract_evidence(runs_b, 0.80)
        cand_a = cal.build_candidate(0.80, 0.90, ev_a)
        cand_b = cal.build_candidate(0.80, 0.90, ev_b)
        check("标签不改变 degraded/对照计数",
              len(ev_b["degraded"]) == len(ev_a["degraded"]) == 3
              and len(ev_b["controls"]) == len(ev_a["controls"]) == 3)
        check("标签不进入证据集合",
              not any("quality_labels" in x for x in ev_b["degraded"] + ev_b["controls"]))
        check("标签不改变 candidate（ok/counts/新值/原因完全一致）",
              cand_b["ok"] == cand_a["ok"] is True
              and cand_b["counts"] == cand_a["counts"]
              and cand_b["new_shift"] == cand_a["new_shift"]
              and cand_b["new_hard"] == cand_a["new_hard"]
              and cand_b["reason"] == cand_a["reason"])

    # 报告：带标签 → 辅助观察一节；无标签 → 未观测（不折算成 0 当证据）
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, shift=0.80, hard=0.90)
        chaos = os.path.join(tmp, "chaos")
        write_three_plus_three(chaos)
        path = os.path.join(chaos, "host-deg-0.json")
        obj = json.load(io.open(path, encoding="utf-8"))
        obj["quality_labels"] = {"total": 2, "by_kind": {"wrong": 1, "overreach": 1}}
        io.open(path, "w", encoding="utf-8", newline="\n").write(
            json.dumps(obj, ensure_ascii=False, indent=2) + "\n")
        rc = cal.main(["--repo", repo, "--chaos-dir", chaos, "--date", "2026-10-27",
                       "--quiet"])
        check("带标签的 run 仍正常出报告（exit 0）", rc == 0, rc)
        report = read_report(chaos, "2026-10-27")
        check("报告含「辅助观察」小节并展示 quality_labels",
              "辅助观察" in report and "quality_labels" in report)
        check("报告写明门槛仍是 MIN_DEGRADED=3 / MIN_CONTROL=3 且标签不参与",
              "MIN_DEGRADED=3" in report and "MIN_CONTROL=3" in report
              and "不参与" in report)
        check("报告写明「规则自动标签 ≠ 人工结果标签」",
              "规则自动标签 ≠ 人工结果标签" in report)
        cand = read_cand(chaos)
        check("候选 JSON 阈值仍 3/3（标签不改门槛）",
              cand["thresholds"]["min_degraded"] == 3
              and cand["thresholds"]["min_control"] == 3)
        check("候选仍正常产出（标签不影响 candidate）",
              bool(cand["candidates"])
              and {c["param"] for c in cand["candidates"]} ==
              {"control.phaseShiftRatio", "control.phaseShiftHardRatio"})

    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp, shift=0.80, hard=0.90)
        chaos = os.path.join(tmp, "chaos")
        write_run(chaos, "h1", keyword_hit=False, degraded_at_ratio=0.62, ratio=0.62)
        rc = cal.main(["--repo", repo, "--chaos-dir", chaos, "--date", "2026-10-27",
                       "--quiet"])
        report = read_report(chaos, "2026-10-27")
        check("无标签 run → 报告写「未观测 / 无标签事件」（不是故障）",
              rc == 0 and "未观测 / 无标签事件" in report)


# ── 5. 默认路径 / 报告 / 真实仓库不被触碰 ─────────────────

def test_default_paths_and_report() -> None:
    print("\n[输出] 默认路径 calibration-<date>.md + provenance-candidates.json")
    with tempfile.TemporaryDirectory() as tmp:
        repo = make_repo(tmp)
        chaos = os.path.join(tmp, "chaos")
        write_three_plus_three(chaos)
        rc = cal.main(["--repo", repo, "--chaos-dir", chaos, "--date", "2026-10-27", "--quiet"])
        check("默认模式 exit 0", rc == 0, rc)
        out = os.path.join(chaos, "calibration-2026-10-27.md")
        check("报告落在 <chaos>/calibration-<date>.md", os.path.isfile(out))
        cand_path = os.path.join(chaos, "provenance-candidates.json")
        check("候选 JSON 落在 <chaos>/provenance-candidates.json", os.path.isfile(cand_path))
        cand = json.load(io.open(cand_path, encoding="utf-8"))
        for key in ("candidates", "refused", "files_changed", "check_config_sync", "linkage"):
            check("候选 JSON 含字段 %s" % key, key in cand)
        check("默认模式记录 applied=false",
              cand["candidates"] and all(c["applied"] is False for c in cand["candidates"]))
        check("候选 JSON：current_values 含 shift/hard",
              cand["current_values"] == {"shift": 0.8, "hard": 0.9}, cand["current_values"])
        report = io.open(out, encoding="utf-8").read()
        check("报告说明 engine 模式永远不参与", "engine 模式永远不参与" in report)
        check("报告含四处副本一致性（shift 与 hard）", "四处一致" in report and "hard" in report)
        check("报告引用 H0 裁决/ADR-011", "ADR-011" in report and "H0 裁决 B" in report)


def test_real_repo_untouched() -> None:
    print("\n[安全] 真实仓库的控制值没有被测试改动")
    rules = io.open(REAL_RULES, encoding="utf-8").read()
    ms = re.search(r"^\s*phaseShiftRatio:\s*([\d.]+)", rules, re.M)
    mh = re.search(r"^\s*phaseShiftHardRatio:\s*([\d.]+)", rules, re.M)
    check("真实 rules shift 仍 0.7（未被 fixture 污染）",
          ms is not None and abs(float(ms.group(1)) - 0.7) < 1e-9, ms.group(1) if ms else None)
    check("真实 rules hard 仍 0.85",
          mh is not None and abs(float(mh.group(1)) - 0.85) < 1e-9, mh.group(1) if mh else None)
    check("真实 rules 未出现 measured 注释（未做过真实 apply）",
          "provenance: measured" not in rules)


def main() -> int:
    print("LINGSHU 验收 · 混沌结果 → 相变双阈值联动标定（tools/chaos_calibrate.py）")
    test_load_runs_filters()
    test_evidence_host_only()
    test_degraded_above_threshold_excluded()
    test_thresholds_locked()
    test_linkage_math()
    test_candidate_requires_both_counts()
    test_insufficient_zero_modification()
    test_apply_eight_values_sync()
    test_apply_rollback_on_check_fail()
    test_apply_refuses_drifted_copies()
    test_quality_labels_auxiliary_only()
    test_default_paths_and_report()
    test_real_repo_untouched()
    print("\n%d passed, %d failed" % (len(PASS), len(FAIL)))
    if FAIL:
        print("failed:", ", ".join(FAIL))
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
