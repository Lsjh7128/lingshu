# -*- coding: utf-8 -*-
"""完整性快照：证明故障注入只写实验目录，未改主干（plugin/engine/rules/contracts）。

输出 acceptance/chaos/fault-injection/integrity.json：
  · 每个受保护文件：sha256 + mtime
  · experiment_epoch：本次实验开始时间（2026-10-27T18:20 本地）
  · assertions：受保护文件里"mtime 晚于实验开始"的数量应为 0
"""
from __future__ import annotations

import hashlib
import json
import os
import time

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(HERE))
DEST = os.path.join(REPO, "acceptance", "chaos", "fault-injection")
DEST_FALLBACK = os.path.join(REPO, "experiments", "chaos-injection", "out")
os.makedirs(DEST, exist_ok=True)

PROTECTED_DIRS = ("plugin/dsh-lingshu", "engine", "rules", "contracts")
EPOCH = time.mktime(time.strptime("2026-10-27 18:20:00", "%Y-%m-%d %H:%M:%S"))


def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 16), b""):
            h.update(chunk)
    return h.hexdigest()


def check_latex():
    """论文源的结构自检（本机无 LaTeX 工具链，只能做能做的静态检查）：

    · `paper/sections/*.tex` 与 `paper/main.tex` 无 CJK/非 ASCII（T1 字体无法排版）；
    · 花括号与 `\\begin`/`\\end` 环境配平；
    · `$` 数量为偶数。
    返回 (ok, 明细)。
    """
    import re
    root = os.path.join(REPO, "paper")
    files = [os.path.join(root, "main.tex")]
    sec = os.path.join(root, "sections")
    if os.path.isdir(sec):
        files += [os.path.join(sec, n) for n in sorted(os.listdir(sec)) if n.endswith(".tex")]
    details = {}
    ok = True
    for path in files:
        rel = os.path.relpath(path, REPO).replace("\\", "/")
        text = open(path, encoding="utf-8").read()
        non_ascii = [i + 1 for i, ln in enumerate(text.splitlines())
                     if any(ord(ch) > 127 for ch in ln)]
        # verbatim/lstlisting 里的 $ 是字面量；\verb|...| 同理；检查前先剥离
        probe = re.sub(r"\\begin\{(verbatim|lstlisting)\}.*?\\end\{\1\}",
                       "", text, flags=re.S)
        probe = re.sub(r"\\verb\|[^|]*\|", "", probe)
        braces = probe.count("{") - probe.count("\\{") - (probe.count("}") - probe.count("\\}"))
        stack, mismatch = [], []
        for kind, name in re.findall(r"\\(begin|end)\{([^}]+)\}", probe):
            if kind == "begin":
                stack.append(name)
            elif not stack or stack[-1] != name:
                mismatch.append(name)
            else:
                stack.pop()
        # 转义的 \$ 不算数学定界符；奇数个未转义 $ 才是真问题
        dollars = (probe.count("$") - probe.count("\\$")) % 2
        good = not non_ascii and braces == 0 and not stack and not mismatch and dollars == 0
        ok = ok and good
        details[rel] = {"ok": good, "non_ascii_lines": non_ascii[:10], "brace_diff": braces,
                        "env_leftover": stack, "env_mismatch": mismatch,
                        "odd_dollars": dollars,
                        "escaped_dollars": text.count("\\$")}
    return ok, details


def main():
    files = {}
    late = []
    for rel in PROTECTED_DIRS:
        base = os.path.join(REPO, rel)
        for dirpath, dirnames, filenames in os.walk(base):
            dirnames[:] = [d for d in dirnames if d not in ("__pycache__", "node_modules")]
            for name in filenames:
                if name.endswith((".pyc", ".pyo")):
                    continue
                full = os.path.join(dirpath, name)
                relp = os.path.relpath(full, REPO).replace("\\", "/")
                mtime = os.path.getmtime(full)
                files[relp] = {"sha256": sha256(full), "mtime": int(mtime)}
                if mtime > EPOCH + 5:
                    late.append(relp)

    latex_ok, latex_detail = check_latex()
    payload = {
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "experiment_epoch": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(EPOCH)),
        "protected_dirs": list(PROTECTED_DIRS),
        "file_count": len(files),
        "files_modified_after_epoch": sorted(late),
        "assertion_no_trunk_change": len(late) == 0,
        "paper_latex_static_check": {"ok": latex_ok, "files": latex_detail},
        "files": files,
    }
    os.makedirs(DEST_FALLBACK, exist_ok=True)
    fallback = os.path.join(DEST_FALLBACK, "integrity.json")
    with open(fallback, "w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    path = fallback
    try:
        os.makedirs(DEST, exist_ok=True)
        primary = os.path.join(DEST, "integrity.json")
        with open(primary, "w", encoding="utf-8", newline="\n") as f:
            json.dump(payload, f, ensure_ascii=False, indent=1)
        path = primary
    except OSError as exc:
        print("acceptance copy skipped (sandbox):", exc)
    print("protected files:", len(files))
    print("modified after experiment epoch:", len(late), late[:10])
    print("paper latex static check:", "PASS" if latex_ok else "FAIL",
          {k: v["ok"] for k, v in latex_detail.items()})
    print("->", path)
    return 0 if (not late and latex_ok) else 2


if __name__ == "__main__":
    raise SystemExit(main())
