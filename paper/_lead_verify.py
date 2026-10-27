# -*- coding: utf-8 -*-
"""Lead 独立复核脚本：验证 A0/A2 关于'100 轮遗忘率结构恒 0'等关键论断。
只读，不改任何仓库文件。"""
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from engine import config as C  # noqa: E402
from engine import memory as M  # noqa: E402
from engine import replay as R  # noqa: E402
from engine import pipeline as P  # noqa: E402

cfg = C.default()
print("== config ==")
print("tau.semantic =", cfg["decay"]["tau"]["semantic"])
print("archiveThreshold =", cfg["potency"]["archiveThreshold"])
print("actrWeight =", cfg["memory"]["actrWeight"], "touchOnRecall =", cfg["memory"]["touchOnRecall"])
print("phaseShiftRatio =", cfg["control"]["phaseShiftRatio"], "hard =", cfg["control"]["phaseShiftHardRatio"])
print("TURN_STEP =", R.TURN_STEP, "BASE_TS =", R.BASE_TS)

# 1) 数据集规模
turns, store = R.load_dataset(ROOT, "v1")
print("\n== dataset ==")
print("turns =", len(turns), "memories =", len(store.entries) if hasattr(store, "entries") else "?")

# 2) 取一个高重要度 semantic 条目，算 100 轮（60s/轮）后的 potency
cands = []
for e in (store.entries if hasattr(store, "entries") else []):
    if getattr(e, "type", "") == "semantic" and float(getattr(e, "importance", 0) or 0) >= 0.9:
        cands.append(e)
print("semantic entries with importance>=0.9 :", len(cands))
if cands:
    e = cands[0]
    print("sample entry:", e.id, "type=", e.type, "importance=", e.importance,
          "access_times=", getattr(e, "access_times", None), "last_access=", getattr(e, "last_access", None))
    for t in (0, 49, 99):
        now = R.BASE_TS + t * R.TURN_STEP
        print("  turn %3d (Δt=%.4f days) potency=%.4f" % (t, t * R.TURN_STEP / 86400.0, M.potency(e, now, cfg)))
    print("  -- 若把 1 轮当作 1 天（时间放大）--")
    for days in (0, 30, 60, 75, 81, 90, 100):
        now = R.BASE_TS + days * 86400.0
        print("  day %3d potency=%.4f  archived_below_0.15=%s" % (days, M.potency(e, now, cfg), M.potency(e, now, cfg) < cfg["potency"]["archiveThreshold"]))

# 3) 冻结回放基线（真实跑一遍 pipeline，不只看文档）
rep = P.replay_pipeline(cfg, ROOT, "v1")
print("\n== replay_pipeline ==")
print(json.dumps(rep["summary"], ensure_ascii=False, sort_keys=True))

# 4) 越狱语料与执行器
jb = [json.loads(l) for l in open(os.path.join(ROOT, "chaos-set", "jailbreak_variants.jsonl"), encoding="utf-8") if l.strip()]
n_mp = sum(1 for r in jb if (r.get("expect") or {}).get("matched_patterns"))
n_blocked = sum(1 for r in jb if (r.get("expect") or {}).get("blocked") is True)
print("\n== jailbreak_variants ==")
print("lines =", len(jb), "with matched_patterns =", n_mp, "expect.blocked=true =", n_blocked)
src = open(os.path.join(ROOT, "tools", "run_chaos.py"), encoding="utf-8").read()
print("run_chaos.py mentions jailbreak_variants:", "jailbreak_variants" in src)
print("run_chaos.py mentions SCENARIOS:", "SCENARIOS" in src)

# 5) chaos engine/host JSON 摘要字段
for name in ("chaos-20261026-234349-engine.json", "chaos-20261026-234400-host.json"):
    p = os.path.join(ROOT, "acceptance", "chaos", name)
    d = json.load(open(p, encoding="utf-8"))
    print("\n== %s ==" % name)
    print("top keys:", sorted(d.keys())[:20])
    s = d.get("summary") or {}
    print("summary:", json.dumps({k: s.get(k) for k in list(s)[:12]}, ensure_ascii=False))
    for sc in (d.get("scenarios") or []):
        if isinstance(sc, dict):
            print("  scenario:", sc.get("scenario") or sc.get("id"), "keys:", list(sc.keys())[:12])
