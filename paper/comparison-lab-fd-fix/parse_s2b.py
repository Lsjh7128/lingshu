import json
import os

base = os.path.dirname(os.path.abspath(__file__))
s = json.load(open(os.path.join(base, "results", "summary.json"), encoding="utf-8"))
for c in s.get("manual_review_s2", {}).get("cells", []):
    if "baseline" in c["cell"]:
        print(c["cell"], "| canary:", c.get("canary_present"), "| hit_raw:", c.get("hit_raw"),
              "| hit_refined:", c.get("hit_refined"), "| leak:", c.get("leak"),
              "| refused:", c.get("refused"), "| verdict:", str(c.get("verdict"))[:80])
print("\nfindings:", json.dumps(s.get("findings"), ensure_ascii=False)[:1200])
