# -*- coding: utf-8 -*-
"""剧本 A 证据分析：把 case_*.json 聚成可写进论文/报告的表格（只读）。"""
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
CASES = os.path.join(OUT, "A-model-failure")


def main() -> int:
    rows = []
    for p in sorted(glob.glob(os.path.join(OUT, "A-*", "case_*.json"))):
        d = json.load(open(p, encoding="utf-8"))
        reqs = d["mock_requests"]
        gaps = []
        for a, b in zip(reqs, reqs[1:]):
            gaps.append(round(b["t"] - a["t"], 2))
        st = d["plugin_state"]["state"] or {}
        rows.append({
            "mode": d["mode"],
            "exit_code": d["run"]["exit_code"],
            "timed_out": d["run"]["timed_out"],
            "wall_s": d["run"]["wall_s"],
            "http_attempts": len(reqs),
            "request_paths": sorted({r["path"] for r in reqs}),
            "stream": all(r["stream"] for r in reqs) if reqs else None,
            "attempt_gaps_s": gaps,
            "final_len": len(d["run"]["final"] or ""),
            "final_head": (d["run"]["final"] or "")[:80],
            "stderr_head": (d["run"]["stderr_head"] or "").strip()[:200],
            "blocked": ((st.get("control") or {}).get("blocked")),
            "turns": st.get("turns"),
            "pre_step_events": d["derived"]["injection_events"].get("pre_step", 0),
            "hook_errors": d["derived"]["hook_error_count"],
            "prompt_bytes": d["plugin_state"]["prompt_bytes"],
            "prompt_has_constitution": "〖宪法 · 只读〗" in (d["plugin_state"]["prompt_head"] or ""),
        })

    md = ["# 剧本 A 观测表", "",
          "| mode | exit | wall(s) | HTTP 尝试 | 重试间隔(s) | final 长度 | blocked | turns | pre_step | hook_error | 宪法在 prompt |",
          "|---|---:|---:|---:|---|---:|---|---:|---:|---:|---|"]
    for r in rows:
        md.append("| `%s` | %s | %.2f | %d | %s | %d | %s | %s | %d | %d | %s |" % (
            r["mode"], r["exit_code"], r["wall_s"], r["http_attempts"],
            ", ".join(str(g) for g in r["attempt_gaps_s"]) or "—",
            r["final_len"], r["blocked"], r["turns"], r["pre_step_events"],
            r["hook_errors"], r["prompt_has_constitution"]))
    md += ["", "## 逐用例 stderr（截断）", ""]
    for r in rows:
        md.append("- `%s`: `%s`" % (r["mode"], r["stderr_head"] or "(空)"))
    text = "\n".join(md) + "\n"
    out_md = os.path.join(CASES, "A-table.md")
    open(out_md, "w", encoding="utf-8", newline="\n").write(text)
    out_json = os.path.join(CASES, "A-derived.json")
    json.dump(rows, open(out_json, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    print(text)
    print("->", out_md)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
