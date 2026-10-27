# -*- coding: utf-8 -*-
"""混沌工程故障注入 · 公共工具（论文 Robustness 章节数据采集）

设计约束（写在最前面，避免跑偏）：
1. **不修改灵枢主干代码**：插件通过 junction 指向 `plugin/dsh-lingshu`（只读使用）；
   所有注入都在**宿主侧/外部**完成——模型端点 URL、数据目录内容、OS 文件状态。
2. **可复现**：每个实验一个隔离 `DSH_HOME`（只有 profile 配置与状态分区，不含插件代码副本），
   数据目录用 `LINGSHU_DATA_DIR` 再隔离一层；证据（stdout/stderr/state/audit/prompt 摘要）
   一律写盘并带 sha256。
3. **零猜测**：没测到的写 `unobserved`，不填 0；每个观测都附"证据文件 + 行号/字段"。
4. 凭证只经子进程 env 传递，不落盘、不打印。

目录：
  experiments/chaos-injection/_runtime/<exp>/dshhome/   ← 每次实验的 DSH_HOME
  experiments/chaos-injection/_runtime/<exp>/data/      ← LINGSHU_DATA_DIR
  experiments/chaos-injection/out/<exp>/                ← 证据 JSON / 原始日志
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time

REPO = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXP_ROOT = os.path.join(REPO, "experiments", "chaos-injection")
RUNTIME = os.path.join(EXP_ROOT, "_runtime")
OUT = os.path.join(EXP_ROOT, "out")

PY = os.environ.get("LINGSHU_PYTHON") or os.path.join(
    os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh"),
    "dsh-runtimes", "dsh-primary-runtime", "dependencies", "python", "python.exe")
NODE = os.environ.get("LINGSHU_NODE") or os.path.join(
    os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh"),
    "dsh-runtimes", "dsh-primary-runtime", "dependencies", "node", "bin", "node.exe")
BIN = os.path.join(
    os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh"),
    "profiles", "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js")
CREDS = os.path.join(
    os.environ.get("DSH_HOME") or os.path.join(os.path.expanduser("~"), ".dsh"),
    ".credentials.yaml")
TEMPLATE_HOME = os.path.join(REPO, "paper", "host-validation", ".runtime", "dshhome")
LAB_PROFILE = "lingshu-lab-headless"
BASE_PROFILE = "headless"


# ── 凭证（只读、只进 env）──────────────────────────────
def read_api_key() -> str:
    raw = open(CREDS, "r", encoding="utf-8", errors="replace").read()
    m = re.search(r"^  DEEPSEEK_API_KEY:\s*(\S+)\s*$", raw, re.M)
    if not m:
        raise RuntimeError("无法从 credentials 读到 DEEPSEEK_API_KEY")
    return m.group(1)


# ── 隔离 DSH_HOME（profile 配置副本 + 插件 junction）────
def _link_plugin(profile_dir: str) -> None:
    """把 profile 的 node_modules/dsh-lingshu 做成指向仓库插件的 junction。

    这样实验**测的是主干当前代码**，而不是某个时间点的副本（红线上更干净）。
    """
    nm = os.path.join(profile_dir, "node_modules")
    os.makedirs(nm, exist_ok=True)
    target = os.path.join(nm, "dsh-lingshu")
    wanted = os.path.join(REPO, "plugin", "dsh-lingshu")
    if os.path.exists(target):
        if os.path.isdir(target) and not os.path.islink(target):
            shutil.rmtree(target, ignore_errors=True)
        else:
            os.remove(target)
    # Windows junction：不需要管理员权限；subprocess 传参避免引号问题
    subprocess.run(["cmd", "/c", "mklink", "/J", target, wanted],
                   check=True, capture_output=True, text=True)
    if not os.path.isdir(target):
        raise RuntimeError("junction 创建失败：%s" % target)


def prepare_home(exp: str) -> dict:
    """建立一次实验的隔离 DSH_HOME；返回路径字典。"""
    base = os.path.join(RUNTIME, exp)
    home = os.path.join(base, "dshhome")
    data = os.path.join(base, "data")
    for d in (home, data, os.path.join(OUT, exp)):
        os.makedirs(d, exist_ok=True)
    profiles = os.path.join(home, "profiles")
    for name in (LAB_PROFILE, BASE_PROFILE):
        src = os.path.join(TEMPLATE_HOME, "profiles", name)
        dst = os.path.join(profiles, name)
        if not os.path.isdir(dst):
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns("node_modules"))
        if name == LAB_PROFILE:
            _link_plugin(dst)
    # 状态分区（DSH 需要）
    for sub in ("sessions", "storages"):
        os.makedirs(os.path.join(home, sub), exist_ok=True)
    return {"home": home, "data": data, "out": os.path.join(OUT, exp)}


# ── 一次 headless 调用 ──────────────────────────────────
_FINAL_RE = re.compile(r'"type":"final","text":"((?:[^"\\]|\\.)*)"')
_USAGE_RE = re.compile(r'"usage":\{[^}]*\}')


def run_headless(prompt: str, *, home: str, data_dir: str | None, profile: str,
                 env_extra: dict | None = None, timeout: float = 180.0,
                 api_key: str | None = None, cwd: str | None = None) -> dict:
    """跑一次 headless 会话；返回结构化观测（不判断好坏，只记录）。"""
    env = dict(os.environ)
    env["DSH_HOME"] = home
    env["DEEPSEEK_API_KEY"] = api_key or read_api_key()
    env["PYTHONIOENCODING"] = "utf-8"
    if data_dir:
        env["LINGSHU_DATA_DIR"] = data_dir
    else:
        env.pop("LINGSHU_DATA_DIR", None)
    for k, v in (env_extra or {}).items():
        if v is None:
            env.pop(k, None)
        else:
            env[k] = str(v)

    t0 = time.perf_counter()
    timed_out = False
    err = None
    out = ""
    try:
        proc = subprocess.run(
            [NODE, BIN, profile, "--json", "-"],
            input=prompt.encode("utf-8"),
            cwd=cwd or REPO, env=env, capture_output=True, timeout=timeout,
        )
        out = proc.stdout.decode("utf-8", "replace")
        stderr = proc.stderr.decode("utf-8", "replace")
        code = proc.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        code = None
        out = (exc.stdout or b"").decode("utf-8", "replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
        stderr = (exc.stderr or b"").decode("utf-8", "replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
        err = "TimeoutExpired"
    wall = time.perf_counter() - t0

    finals = [m.group(1) for m in _FINAL_RE.finditer(out)]
    usages = _USAGE_RE.findall(out)
    steps = out.count('"phase":"step_start"')
    errors = [ln for ln in out.splitlines() if '"type":"error"' in ln or '"level":"error"' in ln]
    return {
        "profile": profile,
        "exit_code": code,
        "timed_out": timed_out,
        "timeout_s": timeout,
        "error_kind": err,
        "wall_s": round(wall, 3),
        "stdout_bytes": len(out.encode("utf-8")),
        "stderr_head": stderr[-1200:],
        "final_count": len(finals),
        "final": finals[-1] if finals else None,
        "usage": usages[-1] if usages else None,
        "steps": steps,
        "error_lines": errors[:5],
        "stdout_head": out[:800],
    }


# ── 证据落盘 ────────────────────────────────────────────
def save_json(path: str, obj) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)
    return path


def save_text(path: str, text: str) -> str:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write(text)
    return path


def read_jsonl(path: str) -> list:
    if not os.path.isfile(path):
        return []
    rows = []
    for line in open(path, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            rows.append({"_unparsable": line[:120]})
    return rows


def data_snapshot(data_dir: str) -> dict:
    """采集插件落盘产物（只读）：state / audit 事件计数 / memory 行数 / last-prompt。"""
    files = {}
    for name in ("state.json", "memory.jsonl", "audit.jsonl", "last-prompt.txt"):
        p = os.path.join(data_dir, name)
        files[name] = os.path.getsize(p) if os.path.isfile(p) else None
    state = None
    sp = os.path.join(data_dir, "state.json")
    if os.path.isfile(sp):
        try:
            state = json.load(open(sp, encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {"_unparsable": True}
    events = read_jsonl(os.path.join(data_dir, "audit.jsonl"))
    kinds = {}
    for e in events:
        k = e.get("type") or e.get("kind") or "unknown"
        kinds[k] = kinds.get(k, 0) + 1
    prompt = ""
    pp = os.path.join(data_dir, "last-prompt.txt")
    if os.path.isfile(pp):
        prompt = open(pp, encoding="utf-8", errors="replace").read()
    return {
        "files": files,
        "state": state,
        "audit_kinds": kinds,
        "audit_events": len(events),
        "audit_tail": events[-8:],
        "memory_rows": len(read_jsonl(os.path.join(data_dir, "memory.jsonl"))),
        "prompt_bytes": len(prompt.encode("utf-8")),
        "prompt_head": prompt[:400],
        "prompt_tail": prompt[-400:],
        "prompt_sha256_16": __import__("hashlib").sha256(prompt.encode("utf-8")).hexdigest()[:16],
    }
