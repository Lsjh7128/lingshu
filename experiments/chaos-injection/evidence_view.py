# -*- coding: utf-8 -*-
"""证据速览（人工复核用；不参与判定）。"""
import glob
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "out")


def show_a():
    for p in sorted(glob.glob(os.path.join(OUT, "A-*", "case_*.json"))):
        d = json.load(open(p, encoding="utf-8"))
        st = (d["plugin_state"]["state"] or {})
        print("==", d["mode"], "(".center(0) or "", os.path.basename(os.path.dirname(p)), ")")
        print("  requests:", [(r["path"], r["stream"], r["bytes"]) for r in d["mock_requests"]])
        print("  exit/wall:", d["run"]["exit_code"], d["run"]["wall_s"],
              "| final:", repr((d["run"]["final"] or "")[:60]))
        print("  error_lines:", [x[:150] for x in d["run"]["error_lines"][:2]])
        print("  stderr tail:", (d["run"]["stderr_head"] or "")[-200:].replace("\n", " | "))
        print("  turns:", st.get("turns"), "| lastEvent:", bool(st.get("lastEvent")),
              "| phase:", (st.get("control") or {}).get("phase"))
        print("  injection events:", d["derived"]["injection_events"],
              "| model events:", d["derived"]["model_call_events"])
        print("  prompt_bytes:", d["plugin_state"]["prompt_bytes"],
              "| prompt_head:", (d["plugin_state"]["prompt_head"] or "")[:100].replace("\n", " | "))


if __name__ == "__main__":
    show_a()
