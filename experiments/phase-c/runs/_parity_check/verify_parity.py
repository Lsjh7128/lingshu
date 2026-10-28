# -*- coding: utf-8 -*-
"""A5 验证脚本：`--enable-meta-controller` 关闭时 Phase B log 与官方 run 逐代字段对比。

用法（在仓库根目录）：
    python experiments/phase-c/runs/_parity_check/verify_parity.py

它把一个 CLI 级真实回归锁 run（run_coevolution 默认参数、只换 --out/--candidate-ledger
到本目录，元控制器关闭）与 Phase B 官方 run `run-20261028-185512` 前 5 代逐字段比较，
结果写 `parity_vs_official.json`（差异必须为 0，ts 与回归耗时除外）。
"""
import copy
import glob
import io
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", "..", ".."))
OFFICIAL = os.path.join(ROOT, "experiments", "phase-b", "runs", "run-20261028-185512",
                        "coevolution_log.jsonl")
NEW_LOG = sorted(glob.glob(os.path.join(HERE, "run-*", "coevolution_log.jsonl")))[-1]
OUT = os.path.join(HERE, "parity_vs_official.json")
COMPARE_GENERATIONS = 5  # 官方 run 前 5 代（gen0..gen4）；gen4 = 官方首个无补丁代


def _load(path):
    with io.open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh if line.strip()]


def _strip(row):
    r = copy.deepcopy(row)
    r.pop("ts", None)  # 时间戳必然不同
    if isinstance(r.get("regression"), dict):
        r["regression"].pop("seconds", None)  # 真实回归锁耗时必然不同
    return r


def main() -> int:
    official = _load(OFFICIAL)
    new = _load(NEW_LOG)
    per_gen = []
    total = 0
    for i in range(COMPARE_GENERATIONS):
        a, b = _strip(new[i]), _strip(official[i])
        diffs = [{"field": k, "new": a.get(k), "official": b.get(k)}
                 for k in sorted(set(a) | set(b)) if a.get(k) != b.get(k)]
        total += len(diffs)
        per_gen.append({"generation_id": i, "field_diffs": diffs,
                        "patch_ids": new[i].get("patch_ids_accepted"),
                        "heldout_rate_evolved_zeroday":
                            new[i].get("intercept_rate_evolved_zeroday")})
    payload = {
        "schema": "phase-c-phase-b-parity-check-v1",
        "claim": "--enable-meta-controller 关闭（默认）时 Phase B 行为与官方 run 逐字一致",
        "official_run": "experiments/phase-b/runs/run-20261028-185512",
        "new_run_log": os.path.relpath(NEW_LOG, ROOT).replace(os.sep, "/"),
        "compared_generations": COMPARE_GENERATIONS,
        "excluded_volatile_fields": ["ts", "regression.seconds"],
        "total_field_diffs": total,
        "compare_generations": True,
        "per_generation": per_gen,
    }
    with io.open(OUT, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    print("compared %d generations, total field diffs = %d" % (COMPARE_GENERATIONS, total))
    for row in per_gen:
        print("  gen %d: %d diffs  patch_ids=%s  heldout=%.4f"
              % (row["generation_id"], len(row["field_diffs"]), row["patch_ids"],
                 row["heldout_rate_evolved_zeroday"]))
    print("saved:", OUT)
    return 0 if total == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
