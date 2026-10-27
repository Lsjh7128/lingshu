# -*- coding: utf-8 -*-
import os

TARGETS = [
    r"paper\comparison-lab\_lead_tmp",
    r"paper\comparison-lab\lead-cells",
    r"paper\comparison-lab\raw\cells",
    r"paper\case-study\_lead_tmp",
    r"paper\_lead_tmp",
]
for d in TARGETS:
    try:
        os.makedirs(d, exist_ok=True)
        p = os.path.join(d, "probe.txt")
        with open(p, "w", encoding="utf-8") as f:
            f.write("ok")
        print("OK  ", d)
    except Exception as exc:  # noqa: BLE001
        print("FAIL", d, type(exc).__name__, exc)
