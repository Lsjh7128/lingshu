# -*- coding: utf-8 -*-
"""Phase A 回归锁（Phase B 专用，Lead 维护）

用途：红队变异 / 蓝队补丁在沙盒里"能不能留下"的唯一判据。
红线（H0 2026-10-28 指令 §二.3）：**任何候选补丁必须连同 Phase A 的全量断言一起跑；
新补丁导致良性对话误伤或破坏原有逻辑 → 直接废弃。**

为什么不是一个普通的 `for f in tests/*.py` 循环：
本机 DSH `workspace-write` 沙箱下，Python 3.12 的 `tempfile.mkdtemp()` 用 `os.mkdir(..., 0o700)`
建出来的目录，其 ACL 让**当前进程随后无法写入/删除**；teardown 阶段因此抛 `PermissionError`，
会污染"断言是否通过"的判断（既有审计已踩过这个坑，见 `acceptance/audit/测试严密性审计报告-2026-10-27.md` §1.2）。
本文件沿用审计期的兼容层：**只替换临时目录的创建/清理两条 IO 管道**，不改任何断言、
不改被测代码、不跳过任何测试。

两个口径（不可互相换算，别混用）：
- `--suite audit892`：Phase A 审计快照的 **14 个文件 / 892 项断言**（H0 指令里点名的 892）；
- `--suite all`：仓库当前全部 `tests/test_*.py`（当前 20 文件；数量由本工具实测，不写死）。

用法：
    python tools/run_phase_a_regression.py --suite audit892
    python tools/run_phase_a_regression.py --suite all --json experiments/phase-b/baseline.json
    python tools/run_phase_a_regression.py --suite audit892 --allow-fail   # 仅报告，不因失败退出 1

退出码：0 = 该口径 0 failed（且所有文件 exit 0）；1 = 有失败；2 = 用法错误。
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import runpy
import subprocess
import sys
import tempfile
import time

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TESTS = os.path.join(ROOT, "tests")

# Phase A 审计快照的 14 个文件（acceptance/audit/2026-10-27，共 892 项断言）。
# 仅用于复现 H0 指令里点名的"892 项"口径；新增测试文件**不许**塞进这个集合。
AUDIT_892_FILES = (
    "test_agent_state.py",
    "test_astrbot_adapter.py",
    "test_chaos_calibrate.py",
    "test_constitution.py",
    "test_control.py",
    "test_gray.py",
    "test_learning.py",
    "test_mutation_regressions.py",
    "test_onboarding_persona.py",
    "test_pipeline.py",
    "test_pipeline_integration.py",
    "test_proposal.py",
    "test_run_chaos.py",
    "test_shadow_analysis.py",
)

# 已知的环境性失败（沙箱拒绝写入 acceptance/ 下的既有产物），不是断言失败。
# 集合要**变小**必须有人证明环境已修；**变大**必须解释原因。Phase B 不许把它当挡箭牌。
KNOWN_ENV_FAIL = {
    "test_chaos_campaign.py": (
        "沙箱拒绝覆盖写 acceptance/chaos/v2/*-report.md（Errno 13）；"
        "与断言逻辑无关，Phase B 未改动该文件"
    ),
}

# ── 沙箱兼容层：只换 IO 管道 ─────────────────────────────

_counter = [0]


def _mkdtemp(suffix=None, prefix=None, dir=None):
    """替代 tempfile.mkdtemp：用 os.makedirs（默认 ACL，沙箱下可写可删）。"""
    base = dir or tempfile.gettempdir()
    pre = prefix or "tmp"
    while True:
        _counter[0] += 1
        name = os.path.join(base, "%s%d_%d" % (pre, os.getpid(), _counter[0]))
        try:
            os.makedirs(name, exist_ok=False)
            return name
        except FileExistsError:
            continue


def install_shim() -> None:
    tempfile.mkdtemp = _mkdtemp
    orig_rmtree = tempfile.TemporaryDirectory._rmtree

    def tolerant(cls, name, ignore_errors=False, repeated=False):
        try:
            return orig_rmtree.__func__(cls, name, ignore_errors=True, repeated=repeated)
        except TypeError:
            return orig_rmtree.__func__(cls, name, ignore_errors=True)
        except Exception:
            return None

    tempfile.TemporaryDirectory._rmtree = classmethod(tolerant)


# ── 跑单个文件 ───────────────────────────────────────────

# 三种既有汇总口径（仓库里并存，不许为了好解析去改测试文件）：
#   1. "174 passed, 0 failed"
#   2. "PASS: 117 ｜ FAIL: 0 ｜ 断言总数: 117"
#   3. "174 ok / 0 fail（共 174 条断言）"
_RE_PASS = re.compile(r"(\d+)\s*passed\s*[，,]\s*(\d+)\s*failed", re.I)
_RE_PASS2 = re.compile(r"PASS:\s*(\d+)\s*.*?FAIL:\s*(\d+)", re.I)
_RE_PASS3 = re.compile(r"(\d+)\s*ok\s*/\s*(\d+)\s*fail", re.I)
_RE_TAIL = re.compile(r"(\d+)\s*passed\b", re.I)


def run_one(path: str, timeout: int = 900) -> dict:
    """在子进程里跑一个测试文件（shim 由子进程自己装）。"""
    env = dict(os.environ)
    env["PYTHONIOENCODING"] = "utf-8"
    env.setdefault("TMPDIR", os.path.join(ROOT, ".tmp"))
    env.setdefault("TEMP", os.path.join(ROOT, ".tmp"))
    env.setdefault("TMP", os.path.join(ROOT, ".tmp"))
    t0 = time.time()
    try:
        proc = subprocess.run(
            [sys.executable, "-X", "utf8", os.path.abspath(__file__), "--_child", path],
            cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
            errors="replace", env=env, timeout=timeout,
        )
        out = proc.stdout + proc.stderr
        rc = proc.returncode
    except subprocess.TimeoutExpired:
        out, rc = "TIMEOUT %s" % path, 124
    passed = failed = 0
    for line in out.splitlines():
        s = line.strip()
        m = _RE_PASS.search(s) or _RE_PASS2.search(s) or _RE_PASS3.search(s)
        if m:
            passed, failed = int(m.group(1)), int(m.group(2))
    if failed == 0 and passed == 0:
        m = _RE_TAIL.search(out)
        if m:
            passed = int(m.group(1))
    if passed + failed == 0:
        # 解析不到汇总行 = 这个文件的断言数不可知，不能当"0 项通过"报出去
        return {"file": os.path.basename(path), "exit": rc, "passed": 0,
                "failed": 0, "parse_error": True,
                "seconds": round(time.time() - t0, 1),
                "tail": [ln for ln in out.strip().splitlines() if ln.strip()][-3:]}
    tail = [ln for ln in out.strip().splitlines() if ln.strip()][-3:]
    return {"file": os.path.basename(path), "exit": rc, "passed": passed,
            "failed": failed, "seconds": round(time.time() - t0, 1), "tail": tail}


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def suite_files(suite: str) -> list[str]:
    if suite == "audit892":
        return [os.path.join(TESTS, f) for f in AUDIT_892_FILES]
    return sorted(os.path.join(TESTS, f) for f in os.listdir(TESTS)
                  if f.startswith("test_") and f.endswith(".py"))


def main(argv: list[str]) -> int:
    # 子进程模式：装 shim 后按 runpy 跑目标测试文件（与直接 python tests/xxx.py 等价）
    if len(argv) >= 3 and argv[1] == "--_child":
        install_shim()
        path = os.path.abspath(argv[2])
        sys.argv = [path]
        try:
            runpy.run_path(path, run_name="__main__")
        except SystemExit as exc:
            return int(exc.code or 0)
        return 0

    ap = argparse.ArgumentParser()
    ap.add_argument("--suite", choices=("audit892", "all"), default="audit892")
    ap.add_argument("--json", default=None, help="把结果写成 JSON")
    ap.add_argument("--allow-fail", action="store_true", help="有失败也退出 0（仅报告）")
    args = ap.parse_args(argv[1:])

    files = suite_files(args.suite)
    missing = [f for f in files if not os.path.exists(f)]
    if missing:
        print("FAIL: 缺少测试文件 %s" % missing)
        return 2

    print("[PHASE-A-LOCK] suite=%s files=%d root=%s" % (args.suite, len(files), ROOT))
    results = []
    for f in files:
        r = run_one(f)
        r["sha256"] = _sha256(f)
        known = KNOWN_ENV_FAIL.get(r["file"]) if (r["exit"] != 0 or r["failed"]) else None
        r["known_env_fail"] = known
        results.append(r)
        flag = "OK  " if r["exit"] == 0 and r["failed"] == 0 else ("ENV?" if known else "FAIL")
        print("%s %-34s exit=%d %4d passed %2d failed  %5.1fs"
              % (flag, r["file"], r["exit"], r["passed"], r["failed"], r["seconds"]))

    total_p = sum(r["passed"] for r in results)
    total_f = sum(r["failed"] for r in results)
    bad = [r for r in results if (r["exit"] != 0 or r["failed"] != 0) and not r["known_env_fail"]]
    env_bad = [r for r in results if r["known_env_fail"] and (r["exit"] != 0 or r["failed"] != 0)]
    env_f = sum(r["failed"] for r in env_bad)
    unparsed = [r["file"] for r in results if r.get("parse_error")]
    summary = {
        "suite": args.suite,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "root": ROOT,
        "files": len(results),
        "total_passed": total_p,
        "total_failed": total_f,
        "failing_files": [r["file"] for r in bad],
        "known_env_failing_files": [r["file"] for r in env_bad],
        "known_env_failed_assertions": env_f,
        "unparsed_summary_files": unparsed,
        "results": results,
    }
    print("TOTAL suite=%s files=%d passed=%d failed=%d failing_files=%d known_env_failing=%d"
          % (args.suite, len(results), total_p, total_f, len(bad), len(env_bad)))
    if env_f:
        print("NOTE: 其中 %d 项失败属已知环境性（沙箱写权限），不计入红队/蓝队补丁判定：%s"
              % (env_f, ", ".join(r["file"] for r in env_bad)))
    if unparsed:
        print("NOTE: 这些文件的汇总行无法解析（断言数不可知，按不通过处理）：%s" % unparsed)
    if args.json:
        os.makedirs(os.path.dirname(os.path.abspath(args.json)) or ".", exist_ok=True)
        with io.open(args.json, "w", encoding="utf-8") as fh:
            json.dump(summary, fh, ensure_ascii=False, indent=2)
        print("saved:", os.path.abspath(args.json))

    ok = not bad and not unparsed and total_f - env_f == 0
    if args.allow_fail:
        return 0
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
