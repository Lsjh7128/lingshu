import json
import os

base = os.path.dirname(os.path.abspath(__file__))
s = json.load(open(os.path.join(base, "results", "summary.json"), encoding="utf-8"))
print("cells:", len(s["cells"]))
for c in s["cells"]:
    if "s2-jailbreak" in c.get("cell", ""):
        print(json.dumps({k: c.get(k) for k in ("cell", "subject", "round", "answer", "attack_success", "leak",
                                                "refused", "gate_blocked", "intercept", "hit", "hit_refined")},
                         ensure_ascii=False)[:600])
print("\naggregates:", json.dumps(s.get("aggregates"), ensure_ascii=False)[:1500])
print("\nmanual_review_s2:", json.dumps(s.get("manual_review_s2"), ensure_ascii=False)[:1500])
