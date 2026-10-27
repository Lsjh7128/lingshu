# -*- coding: utf-8 -*-
"""Cohen's kappa（含 quadratic-weighted）——S3/S4 双人盲评一致性。

用法：
    python paper/supplement/cohen_kappa.py \
        --a paper/supplement/labels/annotator-a4.jsonl \
        --b paper/supplement/labels/annotator-a5.jsonl \
        --out paper/supplement/labels/kappa.json

输入每行：{"item_id": "...", "M1": 0|1|2|null, "M2": ..., "M3": ..., "M4": ...,
          "label": "correct|wrong|missing|should_not_recall|drift|overreach"|null,
          "quote": "...", "note": "..."}
缺失项（null / NA / 空）在对应维度上跳过该条目。
"""
import argparse
import json
import math


def load(path):
    rows = {}
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            r = json.loads(line)
            rows[r["item_id"]] = r
    return rows


def cohen_kappa(a, b, weighted=False, max_rating=2):
    """a, b: 等长评分列表（同一条目顺序一致）。weighted 用于有序 0..max_rating。"""
    assert len(a) == len(b) and a, "empty or mismatched"
    n = len(a)
    cats = sorted(set(a) | set(b))
    po = sum(1 for x, y in zip(a, b) if x == y) / n
    pe = 0.0
    for c in cats:
        pa = sum(1 for x in a if x == c) / n
        pb = sum(1 for y in b if y == c) / n
        pe += pa * pb
    if pe >= 1.0:
        return 1.0, po
    po_w = 0.0
    if weighted:
        num = 0.0
        den = 0.0
        for x, y in zip(a, b):
            w = 1.0 - ((x - y) ** 2) / (max_rating ** 2)
            num += w
            den += 1.0
        po_w = num / den
        # weighted expected
        pe_w = 0.0
        for x in cats:
            for y in cats:
                px = sum(1 for v in a if v == x) / n
                py = sum(1 for v in b if v == y) / n
                w = 1.0 - ((x - y) ** 2) / (max_rating ** 2)
                pe_w += w * px * py
        k = (po_w - pe_w) / (1.0 - pe_w) if pe_w < 1 else 1.0
        return k, po_w
    return (po - pe) / (1.0 - pe), po


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--a", required=True)
    ap.add_argument("--b", required=True)
    ap.add_argument("--out", default="")
    args = ap.parse_args()

    A, B = load(args.a), load(args.b)
    ids = sorted(set(A) & set(B))
    missing = sorted(set(A) ^ set(B))
    out = {"items_a": len(A), "items_b": len(B), "items_common": len(ids),
           "items_only_one_side": missing, "dimensions": {}}

    for dim, max_r in (("M1", 2), ("M2", 2), ("M3", 2), ("M4", 2)):
        pairs = [(A[i].get(dim), B[i].get(dim)) for i in ids]
        pairs = [(x, y) for x, y in pairs if isinstance(x, int) and isinstance(y, int)]
        if not pairs:
            out["dimensions"][dim] = {"n": 0, "note": "no integer ratings"}
            continue
        xs = [p[0] for p in pairs]
        ys = [p[1] for p in pairs]
        po = sum(1 for x, y in zip(xs, ys) if x == y) / len(pairs)
        # 零方差（天花板/地板）：全部分值落在同一类 → kappa 数学上未定义（pe=1，分母为 0）
        if len(set(xs) | set(ys)) == 1:
            out["dimensions"][dim] = {
                "n": len(pairs), "observed_agreement": round(po, 4),
                "kappa_weighted": None, "kappa_undefined": True,
                "note": "zero variance (all ratings in one category); kappa not estimable"}
            continue
        k, po = cohen_kappa(xs, ys, weighted=True, max_rating=max_r)
        out["dimensions"][dim] = {"n": len(pairs), "kappa_weighted": round(k, 4),
                                  "observed_agreement": round(po, 4)}

    pairs = [(A[i].get("label"), B[i].get("label")) for i in ids]
    pairs = [(x, y) for x, y in pairs if isinstance(x, str) and isinstance(y, str)]
    if pairs:
        agree = sum(1 for x, y in pairs if x == y) / len(pairs)
        # 分类 kappa（未加权）
        cats = sorted(set(x for x, _ in pairs) | set(y for _, y in pairs))
        pe = sum((sum(1 for x, _ in pairs if x == c) / len(pairs)) *
                 (sum(1 for _, y in pairs if y == c) / len(pairs)) for c in cats)
        k = (agree - pe) / (1 - pe) if pe < 1 else 1.0
        out["dimensions"]["label"] = {"n": len(pairs), "kappa_unweighted": round(k, 4),
                                      "observed_agreement": round(agree, 4),
                                      "categories": cats}
    else:
        out["dimensions"]["label"] = {"n": 0}

    # 判定（ANNOTATION.md §3）：κ 可估时按阈值；κ 未定义（零方差）时单独标注
    kas = [v.get("kappa_weighted", v.get("kappa_unweighted"))
           for v in out["dimensions"].values() if v.get("n")]
    actual = [x for x in kas if isinstance(x, (int, float))]
    undefined = [k for k, v in out["dimensions"].items() if v.get("kappa_undefined")]
    out["min_kappa"] = round(min(actual), 4) if actual else None
    if actual:
        out["verdict"] = ("usable" if min(actual) >= 0.80 else
                          "usable_with_caveat" if min(actual) >= 0.60 else
                          "recalibrate")
    elif undefined:
        out["verdict"] = "ceiling_not_estimable"
        out["verdict_note"] = ("all dimensions have zero variance; kappa is undefined. "
                               "Report observed agreement, not kappa.")
    else:
        out["verdict"] = "no_data"

    text = json.dumps(out, ensure_ascii=False, indent=1)
    if args.out:
        with open(args.out, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    print(text)


if __name__ == "__main__":
    main()
