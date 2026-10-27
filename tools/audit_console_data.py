# -*- coding: utf-8 -*-
"""控制台数据自检：确认 `report_console.py` 的四个数据源"够用、不误导"。

    python tools/audit_console_data.py                 # 人读
    python tools/audit_console_data.py --json          # 机器读
    python tools/audit_console_data.py --home <DSH_HOME>

为什么单独一个脚本：
- `report_console.py` 只负责渲染，坏了会静默显示"还没有记忆条目"之类的空态；
- 空态和"数据本身缺字段"在页面上长得一样，必须有一个不渲染的检查先说清楚。

它只读，不写任何文件，不改插件状态。退出码：0 = 无 FAIL；1 = 有 FAIL。
"""
from __future__ import annotations

import argparse
import io
import json
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_HOME = os.path.join(os.path.expanduser("~"), ".dsh")

# 控制台读取的四个文件（见 tools/report_console.py:63-69）
SOURCES = ("state.json", "memory.jsonl", "audit.jsonl", "last-prompt.txt")

# memory.jsonl 的取值闭集（与 contracts/memory.schema.json 同步）
TYPE_SET = {"episodic", "semantic", "procedural"}
TRUST_SET = {"user_explicit", "user_implied", "tool_output", "system"}
STATUS_SET = {"active", "superseded", "archived", "suppressed"}

results: list[dict] = []


def add(level: str, check: str, detail: str) -> None:
    results.append({"level": level, "check": check, "detail": detail})


def read_jsonl(path: str) -> tuple[list[dict], int]:
    rows, bad = [], 0
    if not os.path.isfile(path):
        return rows, bad
    for line in io.open(path, encoding="utf-8"):
        line = line.strip().lstrip("\ufeff")
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            bad += 1
    return rows, bad


def read_json(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        return json.loads(io.open(path, encoding="utf-8").read().lstrip("\ufeff"))
    except json.JSONDecodeError:
        return {}


def load_schema() -> tuple[list[str], set[str]]:
    """返回 (required, allowed)。schema 缺失时退回硬编码，且记一条 WARN。"""
    p = os.path.join(ROOT, "contracts", "memory.schema.json")
    if not os.path.isfile(p):
        add("WARN", "schema", "contracts/memory.schema.json 不存在，退回内置字段表")
        return ([], set())
    s = json.loads(io.open(p, encoding="utf-8").read())
    return (list(s.get("required") or []), set((s.get("properties") or {}).keys()))


def check_files(pdir: str) -> dict:
    seen = {}
    for name in SOURCES:
        p = os.path.join(pdir, name)
        ok = os.path.isfile(p)
        seen[name] = p
        if not ok:
            add("FAIL", "文件存在", "缺 %s（控制台会显示空态，看不出是缺文件）" % name)
        elif os.path.getsize(p) == 0:
            add("WARN", "文件存在", "%s 是 0 字节" % name)
    return seen


def check_memory(mem: list[dict], bad: int, required: list[str], allowed: set[str]) -> None:
    if bad:
        add("FAIL", "记忆可解析", "memory.jsonl 有 %d 行不是合法 JSON" % bad)
    if not mem:
        add("WARN", "记忆条目", "0 条：控制台会显示「还没有记忆条目」，与「链路没跑」不可区分")
        return

    ids = [m.get("id") for m in mem]
    if len(ids) != len(set(ids)):
        dup = sorted({i for i in ids if ids.count(i) > 1})
        add("FAIL", "记忆 id", "重号: %s" % ", ".join(map(str, dup)))

    # 逐条字段校验（additionalProperties:false + required）
    missing, extra = {}, {}
    for m in mem:
        for r in required:
            if r not in m or m.get(r) in (None, ""):
                missing[r] = missing.get(r, 0) + 1
        for k in m:
            if allowed and k not in allowed:
                extra[k] = extra.get(k, 0) + 1
    if missing:
        add("FAIL", "记忆字段", "%d 条里缺必填: %s" % (len(mem), ", ".join(
            "%s(%d 条)" % (k, v) for k, v in sorted(missing.items()))))
    if extra:
        add("FAIL", "记忆字段", "schema 未定义但已落盘: %s" % ", ".join(
            "%s(%d 条)" % (k, v) for k, v in sorted(extra.items())))

    # 取值闭集
    for name, key, closed in (("type", "type", TYPE_SET),
                              ("sourceTrust", "source_trust", TRUST_SET),
                              ("sourceTrust", "sourceTrust", TRUST_SET),
                              ("status", "status", STATUS_SET)):
        vals = {m.get(key) for m in mem if key in m}
        off = vals - closed
        if off:
            add("FAIL", "闭集 %s" % name, "越界值: %s" % ", ".join(sorted(map(str, off))))

    # τ 应按类型分档（docs/02 §3.1：7/30/90）
    want = {"episodic": 7, "semantic": 30, "procedural": 90}
    wrong = []
    for m in mem:
        tau = m.get("tauDays", m.get("tau_days"))
        t = m.get("type")
        if tau is not None and t in want and tau != want[t]:
            wrong.append("%s(%s=%s,期望 %s)" % (m.get("id"), t, tau, want[t]))
    if wrong:
        add("FAIL", "τ 分档", "%d 条 τ 与类型不符: %s" % (len(wrong), ", ".join(wrong[:5])))

    # 同键并存（控制台的"记忆条目"表会把三条都列出来，看起来像三条事实）
    keyed: dict[tuple, list[str]] = {}
    for m in mem:
        if m.get("status") == "active":
            keyed.setdefault((m.get("subject"), m.get("relation")), []).append(str(m.get("id")))
    multi = {k: v for k, v in keyed.items() if len(v) > 1}
    if multi:
        add("WARN", "同键并存", "%d 组 (subject,relation) 同时有多条 active（控制台会并列显示）: %s"
            % (len(multi), "; ".join("%s/%s=%s" % (k[0], k[1], ",".join(v)) for k, v in list(multi.items())[:3])))


def check_audit(audit: list[dict], bad: int) -> None:
    if bad:
        add("FAIL", "审计可解析", "audit.jsonl 有 %d 行不是合法 JSON" % bad)
    if not audit:
        add("WARN", "审计事件", "0 条：控制台时间线为空")
        return
    kinds: dict[str, int] = {}
    for a in audit:
        kinds[a.get("kind", "?")] = kinds.get(a.get("kind", "?"), 0) + 1
    top = ", ".join("%s×%d" % (k, v) for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:8])
    add("INFO", "审计事件", "%d 条 / %d 类: %s" % (len(audit), len(kinds), top))

    # 控制台按 iso 或 ts 显示时间；只有 ts（毫秒）会让表格里全是数字
    no_iso = sum(1 for a in audit if not a.get("iso") and a.get("ts"))
    if no_iso == len(audit):
        add("WARN", "审计时间", "全部只有 ts（毫秒时间戳），控制台时间列会显示成一串数字")
    elif no_iso:
        add("INFO", "审计时间", "%d 条只有 ts、无 iso" % no_iso)

    # 版本与来源：控制台要能回答"这条是哪版参数写的"
    for field in ("origin", "param_version", "prompt_version"):
        hit = sum(1 for a in audit if field in a)
        if hit == 0:
            add("WARN", "审计字段", "没有任何事件带 %s（docs/14 ENG-1 要求的投影字段）" % field)
        else:
            add("INFO", "审计字段", "%s 覆盖 %d/%d 条" % (field, hit, len(audit)))


def check_state(state: dict) -> None:
    if not state:
        add("FAIL", "state.json", "读不到或不是合法 JSON")
        return
    for key in ("turns", "stateWrites", "frozen", "goal", "relation"):
        if key not in state:
            add("FAIL", "state.json", "缺键 %s（控制台卡片会显示默认值）" % key)

    frozen = state.get("frozen") or {}
    if frozen.get("ok") and not frozen.get("checked"):
        add("WARN", "冻结区", "ok=true 但 checked=0：控制台会显示「通过 ｜ 校验 0 个文件」，"
                              "看起来像通过，实际没校验任何文件")

    goals = ((state.get("goal") or {}).get("goals") or {})
    if not goals:
        add("WARN", "活跃目标", "0 个：控制台「活跃目标」卡片显示「无」")
    rel = state.get("relation") or {}
    if not rel:
        add("WARN", "关系", "state.relation 为空")


def check_prompt(prompt: str, state: dict) -> None:
    if not prompt.strip():
        add("WARN", "注入预览", "last-prompt.txt 为空：控制台「本轮注入预览」只有一句占位")
        return
    sections = [ln for ln in prompt.splitlines() if ln.startswith("〖")]
    add("INFO", "注入预览", "%d 个 〖〗 段、%d 字" % (len(sections), len(prompt)))
    if len(sections) < 4:
        add("WARN", "注入预览", "只有 %d 段，契约是 4 段（order 100/110/120/130）" % len(sections))
    if sections and not prompt.startswith("〖宪法"):
        add("WARN", "注入预览", "首段不是宪法段（order 100 应在最前）")

    # 预览是不是当轮的：拿 pre_step 的 chars 与文件字数对一下（粗判）
    last_chars = None
    if state.get("lastEvent") and "chars" in state["lastEvent"]:
        last_chars = state["lastEvent"]["chars"]
    if last_chars == 0 and prompt.strip():
        add("WARN", "预览新鲜度", "state.lastEvent.chars=0（当轮未注入），但 last-prompt.txt 非空 —— "
                                  "控制台会把上一轮的内容当成本轮预览")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=os.environ.get("DSH_HOME", DEFAULT_HOME))
    ap.add_argument("--json", action="store_true", help="输出机器可读结果")
    args = ap.parse_args()

    pdir = os.path.join(args.home, "plugins", "dsh-lingshu")
    files = check_files(pdir)

    required, allowed = load_schema()
    if not required:
        required = ["id", "type", "importance", "subject", "relation", "object", "content",
                    "source_quote", "source_trust", "confidence", "status", "forgetting_policy"]

    mem, mem_bad = read_jsonl(files["memory.jsonl"])
    audit, audit_bad = read_jsonl(files["audit.jsonl"])
    state = read_json(files["state.json"])
    prompt = io.open(files["last-prompt.txt"], encoding="utf-8").read() \
        if os.path.isfile(files["last-prompt.txt"]) else ""

    check_memory(mem, mem_bad, required, allowed)
    check_audit(audit, audit_bad)
    check_state(state)
    check_prompt(prompt, state)

    counts = {"FAIL": 0, "WARN": 0, "INFO": 0, "OK": 0}
    for r in results:
        counts[r["level"]] = counts.get(r["level"], 0) + 1
    verdict = "FAIL" if counts["FAIL"] else ("WARN" if counts["WARN"] else "OK")

    if args.json:
        print(json.dumps({"pdir": pdir, "verdict": verdict, "counts": counts,
                          "results": results, "memory": len(mem), "audit": len(audit)},
                         ensure_ascii=False, indent=2))
        return 0 if counts["FAIL"] == 0 else 1

    print("LINGSHU 控制台数据自检 · %s" % pdir)
    print("记忆 %d 条 ｜ 审计 %d 条 ｜ 结论 %s（FAIL %d / WARN %d）"
          % (len(mem), len(audit), verdict, counts["FAIL"], counts["WARN"]))
    print("-" * 72)
    for r in results:
        print("[%-4s] %-10s %s" % (r["level"], r["check"], r["detail"]))
    print("-" * 72)
    if counts["FAIL"]:
        print("有 FAIL：现在的数据渲染出来会出错或误导，先修数据再看页面。")
    elif counts["WARN"]:
        print("能渲染，但有 %d 处会误导（见 WARN）。" % counts["WARN"])
    else:
        print("可以渲染。")
    return 0 if counts["FAIL"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
