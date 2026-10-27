# -*- coding: utf-8 -*-
"""生成 fig3-inputs.json（图 3 的受控输入；只从证据 JSON 抽取，不手填数字）。"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")
EXP = os.path.dirname(HERE)


def load(*parts):
    return json.load(open(os.path.join(*parts), encoding="utf-8"))


a_sum = json.load(open(os.path.join(OUT, "A-model-failure", "A-derived.json"), encoding="utf-8"))
a_rows = {r["mode"]: r for r in a_sum}

b0 = load(OUT, "B-memory-corruption", "case_B0.json")
b1 = load(OUT, "B-memory-corruption", "case_B1.json")
b2 = load(OUT, "B-memory-corruption", "case_B2.json")
c_sum = load(OUT, "C-context-flood", "summary.json")

hook_errors = int((b1["plugin_state"].get("audit_kinds") or {}).get("hook_error", 0))
c70 = next(r for r in c_sum["cases"] if r["ratio_key"] == "0.7000")
c85 = next(r for r in c_sum["cases"] if r["ratio_key"] == "0.8500")

payload = {
    "generated_from": {
        "A": "out/A-*/case_*.json + out/A-model-failure/summary.json",
        "B": "out/B-memory-corruption/case_B*.json",
        "C": "out/C-context-flood/summary.json",
    },
    "A": {
        "http_attempts": int(a_rows["500"]["http_attempts"]),
        "wall_s": round(float(a_rows["500"]["wall_s"]), 2),
        "blocked_by_mode": {m: bool(r.get("blocked")) for m, r in a_rows.items()},
        "empty_passthrough": bool(a_rows["empty"]["final_len"] == 0),
        "fabricated_passthrough": bool(a_rows["fabricated"]["final_len"] > 0),
        "retry_gaps_s": a_rows["500"].get("attempt_gaps_s"),
    },
    "B": {
        "b0_style_injected": bool(b0["checks"].get("style_or_emotion_injected")),
        "b0_no_fallback": bool(b0["checks"].get("no_variety_fallback")),
        "b1_hook_errors": hook_errors,
        "b1_blocked": bool(b1["derived"].get("blocked")),
        "b1_answer_still_returned": bool(b1["derived"].get("got_final")),
        "b1_state_writes": int(b1["derived"].get("stateWrites") or 0),
        "b1_claimed_persisted": "记下了" in (b1["derived"].get("final_text_head") or ""),
        "b2_variety_fallback": bool(b2["derived"].get("variety_fallback")),
        "b2_style_dropped": "style" in (b2["derived"].get("dropDomains") or []),
        "b2_conflict_hint": bool(b2["checks"].get("prompt_has_conflict_hint")),
        "b2_constitution": bool(b2["checks"].get("prompt_has_constitution")),
    },
    "C": {
        "ratio70": round(float(c70["ratio_measured"]), 4),
        "phase70": c70["phase"],
        "ratio85": round(float(c85["ratio_measured"]), 4),
        "phase85": c85["phase"],
        "anchor85": bool(c85["last_prompt_anchor_head"] and c85["last_prompt_anchor_tail"]),
        "constitution_all": all(r["last_prompt_constitution"] for r in c_sum["cases"]),
        "truncation_all_zero": all(float(r["main_request_truncation_rate"]) == 0.0
                                   for r in c_sum["cases"]),
    },
}
path = os.path.join(OUT, "fig3-inputs.json")
with open(path, "w", encoding="utf-8", newline="\n") as f:
    json.dump(payload, f, ensure_ascii=False, indent=2)
print(json.dumps(payload, ensure_ascii=False, indent=2))
print("->", path)
