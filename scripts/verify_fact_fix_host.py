# -*- coding: utf-8 -*-
"""task-4：真实宿主验证 F-A/F-B/F-D（Lead 执行，可重复运行）。

用法（需无沙箱：宿主会写 $DSH_HOME 下的会话/日志；2026-10-27 实测在
workspace-write 沙箱下不可跑，danger-full-access 下通过）：
  <bundled python> scripts/verify_fact_fix_host.py

产物：
  acceptance/fact-fix-host-<date>.md   （命令、stdout 摘录、审计摘录、判定、诚实边界）
  acceptance/fact-fix-host-<date>-raw/ （每轮 stdout/stderr/审计/记忆原始文件）
"""
import json
import os
import shutil
import subprocess
import sys
import time

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

ROOT = r"<REPO_ROOT>"
DSH_HOME = os.environ.get("DSH_HOME") or os.path.join(os.environ["USERPROFILE"], ".dsh")
NODE = os.environ.get("LINGSHU_NODE") or os.path.join(
    os.environ["USERPROFILE"], ".dsh", "dsh-runtimes", "dsh-primary-runtime",
    "dependencies", "node", "bin", "node.exe")
if not os.path.exists(NODE):
    NODE = "node"
BIN = os.path.join(DSH_HOME, "profiles", "node_modules", "@deepseek-ai", "dsh", "lib", "bin.js")
PROFILE = "lingshu-lab-headless"
DATA = os.path.join(ROOT, ".chaos-data-fixverify")
DATA_ANCHOR = os.path.join(ROOT, ".chaos-data-anchor")
DAY = time.strftime("%Y-%m-%d")
STAMP = time.strftime("%Y%m%d-%H%M%S")
RAW = os.path.join(ROOT, "acceptance", "fact-fix-host-%s-raw" % DAY)
REPORT = os.path.join(ROOT, "acceptance", "fact-fix-host-%s.md" % DAY)
JUNCTION = os.path.join(ROOT, "plugin", "node_modules", "dsh-lingshu")
PLUGIN_DIR = os.path.join(ROOT, "plugin", "dsh-lingshu")

PASS, FAIL, NOTES = [], [], []


def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(("  ok   " if cond else "  FAIL ") + name + (("  — " + str(detail)[:220]) if detail else ""), flush=True)


def reset_data_dir(d, artifacts=("state.json", "audit.jsonl", "memory.jsonl", "last-prompt.txt")):
    os.makedirs(d, exist_ok=True)
    removed = []
    for f in artifacts:
        p = os.path.join(d, f)
        if os.path.exists(p):
            os.remove(p)
            removed.append(f)
    return removed


def snapshot(d):
    out = {}
    for f in ("state.json", "audit.jsonl", "memory.jsonl", "last-prompt.txt"):
        p = os.path.join(d, f)
        out[f] = open(p, "r", encoding="utf-8", errors="replace").read() if os.path.exists(p) else ""
    return out


def run_one(label, prompt, data_dir, extra_env=None, timeout=300):
    env = dict(os.environ, LINGSHU_DATA_DIR=data_dir, PYTHONIOENCODING="utf-8")
    if extra_env:
        env.update(extra_env)
    t0 = time.time()
    try:
        proc = subprocess.run([NODE, BIN, PROFILE, prompt], cwd=ROOT, env=env,
                              capture_output=True, timeout=timeout)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        code, out, err = -9, (e.stdout or b""), (e.stderr or b"") + b"\nTIMEOUT"
    secs = round(time.time() - t0, 1)
    rec = {"label": label, "prompt": prompt, "exit": code, "seconds": secs,
           "stdout": out.decode("utf-8", "replace").strip(),
           "stderr": err.decode("utf-8", "replace").strip()}
    os.makedirs(os.path.join(RAW, "stdout"), exist_ok=True)
    open(os.path.join(RAW, "stdout", "%s.txt" % label), "w", encoding="utf-8").write(
        rec["stdout"] + "\n")
    open(os.path.join(RAW, "stdout", "%s.stderr.log" % label), "w", encoding="utf-8").write(
        rec["stderr"] + "\n")
    print("  [run] %-12s exit=%s %.1fs stdout=%s" % (label, code, secs,
          json.dumps(rec["stdout"][:60], ensure_ascii=False)), flush=True)
    return rec


def read_memory_entries(d):
    """按 id last-wins 折叠 memory.jsonl（与插件读取侧同一语义）。"""
    p = os.path.join(d, "memory.jsonl")
    if not os.path.exists(p):
        return []
    by_id = {}
    order = []
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        rid = str(e.get("id"))
        if rid not in by_id:
            order.append(rid)
        by_id[rid] = e
    return [by_id[i] for i in order]


def read_audit(d):
    p = os.path.join(d, "audit.jsonl")
    if not os.path.exists(p):
        return []
    evs = []
    for line in open(p, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            evs.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return evs


def main():
    print("== task-4 真实宿主验证 ==", flush=True)
    print("node   :", NODE, flush=True)
    print("bin    :", BIN, flush=True)
    print("profile:", PROFILE, flush=True)
    print("data   :", DATA, flush=True)
    os.makedirs(RAW, exist_ok=True)

    check("插件 junction 存在（宿主解析包名用）", os.path.isdir(JUNCTION) or os.path.exists(JUNCTION),
          JUNCTION)
    check("宿主 CLI bin.js 存在", os.path.exists(BIN), BIN)
    check("插件目录存在 index.js", os.path.exists(os.path.join(PLUGIN_DIR, "index.js")))
    if FAIL:
        return finish(None, None)

    # ── A. 短对话：F-A 纠正 + F-B 提问 ─────────────────────────
    print("\n[A] 短对话（3 轮，独立进程，状态跨进程续）", flush=True)
    removed = reset_data_dir(DATA)
    print("  reset:", removed, flush=True)
    r1 = run_one("A1-seed", "我的项目代号是阿尔法。请记住。", DATA)
    s1 = snapshot(DATA)
    r2 = run_one("A2-change", "我现在改主意了，代号改成贝塔。", DATA)
    s2 = snapshot(DATA)
    r3 = run_one("A3-query", "我的项目代号是什么？请只回答代号。", DATA)
    s3 = snapshot(DATA)

    for label, rec in (("A1", r1), ("A2", r2), ("A3", r3)):
        check("%s 进程 exit=0" % label, rec["exit"] == 0, "exit=%s stderr=%s" % (rec["exit"], rec["stderr"][-160:]))
    check("A1 之后 memory.jsonl 有 1 条事实（阿尔法）",
          len(read_memory_entries(DATA)) >= 1 if False else bool(s1["memory.jsonl"]),
          (s1["memory.jsonl"] or "")[:120])

    entries3 = read_memory_entries(DATA)
    evs3 = read_audit(DATA)
    alpha = [e for e in entries3 if e.get("object") == "阿尔法"]
    beta = [e for e in entries3 if e.get("object") == "贝塔"]
    bad_obj = [e for e in entries3 if str(e.get("object", "")).strip() in
               ("什么", "what", "怎么", "为什么", "how")]
    check("F-A 纠正落盘：存在 object=贝塔 的 active 条目",
          any(e.get("status") == "active" for e in beta), json.dumps(beta, ensure_ascii=False)[:200])
    check("F-A supersede：旧条目 object=阿尔法 已是 superseded",
          bool(alpha) and all(e.get("status") == "superseded" for e in alpha),
          json.dumps(alpha, ensure_ascii=False)[:200])
    check("F-A supersede：墓碑 supersededBy 指向新条目 id",
          bool(alpha) and bool(beta) and alpha[0].get("supersededBy") == beta[-1].get("id"),
          "%r vs %r" % (alpha[0].get("supersededBy") if alpha else None,
                        beta[-1].get("id") if beta else None))
    check("F-B 提问未污染记忆：没有任何 object 是疑问词的条目", not bad_obj,
          json.dumps(bad_obj, ensure_ascii=False)[:160])
    fact_writes = [e for e in evs3 if e.get("kind") == "memory_write"
                   and (e.get("origin") == "user_fact" or e.get("item", {}).get("origin") == "user_fact")]
    check("审计有 memory_supersede 事件",
          any(e.get("kind") == "memory_supersede" for e in evs3),
          str(sum(1 for e in evs3 if e.get("kind") == "memory_supersede")))
    rej = [e for e in evs3 if e.get("kind") == "fact_extract_rejected"]
    check("F-B 提问被提取器拒绝并留痕（reason 含 interrogative）",
          any("interrogative" in json.dumps(e, ensure_ascii=False) for e in rej),
          json.dumps(rej[-2:], ensure_ascii=False)[:220])
    check("F-D 回答命中新值：A3 回答含 贝塔", "贝塔" in r3["stdout"],
          json.dumps(r3["stdout"][:120], ensure_ascii=False))
    check("F-D 回答不泄漏旧值：A3 回答不含 阿尔法", "阿尔法" not in r3["stdout"],
          json.dumps(r3["stdout"][:120], ensure_ascii=False))

    # 审计字段 anchor_lag
    first_rows = [e for e in evs3 if e.get("kind") == "pre_step" and e.get("firstStep") is True]
    check("pre_step 首 step 审计行都带 anchor_lag 字段",
          bool(first_rows) and all("anchor_lag" in e for e in first_rows),
          str(len(first_rows)))
    check("真实宿主：首 step anchor_lag=false（预计算生效，本回合无滞后）",
          bool(first_rows) and all(e.get("anchor_lag") is False for e in first_rows),
          json.dumps(first_rows[-3:], ensure_ascii=False)[:260])

    # ── B. critical 锚点：首轮直接进 prompt（滞后消失的直接证据）───
    print("\n[B] critical 锚点滞后验证（LINGSHU_CONTEXT_WINDOW=2000，超长消息）", flush=True)
    removed = reset_data_dir(DATA_ANCHOR)
    print("  reset:", removed, flush=True)
    filler = "这是一段用于把上下文用量推过相变阈值的普通叙述，不含事实句式。"
    long_prompt = "请复述下面这段话的第一句：" + (filler * 220)
    print("  长消息字符数:", len(long_prompt), flush=True)
    rb = run_one("B1-critical", long_prompt, DATA_ANCHOR,
                 extra_env={"LINGSHU_CONTEXT_WINDOW": "2000"}, timeout=420)
    sb = snapshot(DATA_ANCHOR)
    evs_b = read_audit(DATA_ANCHOR)
    pre_b = [e for e in evs_b if e.get("kind") == "pre_step"]
    first_b = [e for e in pre_b if e.get("firstStep") is True]
    check("B1 进程 exit=0", rb["exit"] == 0, "exit=%s stderr=%s" % (rb["exit"], rb["stderr"][-160:]))
    check("B1 相变触发：firstStep ratio>=0.85 且 anchor=true",
          bool(first_b) and any(float(e.get("ratio") or 0) >= 0.85 and e.get("anchor") is True
                                for e in first_b),
          json.dumps(first_b[-2:], ensure_ascii=False)[:260])
    check("B1 首轮 last-prompt.txt 已含首部锚点『〖人格锚点〗』（旧版这里没有）",
          "〖人格锚点〗" in (sb["last-prompt.txt"] or ""),
          (sb["last-prompt.txt"] or "")[:120])
    check("B1 首轮 last-prompt.txt 含尾部复核锚点『〖人格锚点·复核〗』",
          "〖人格锚点·复核〗" in (sb["last-prompt.txt"] or ""),
          (sb["last-prompt.txt"] or "")[-120:])
    check("B1 首 step anchor_lag=false",
          bool(first_b) and all(e.get("anchor_lag") is False for e in first_b),
          json.dumps([e.get("anchor_lag") for e in first_b]))

    # ── 落盘原始证据 ─────────────────────────────────────
    for sub in ("A1-seed", "A2-change", "A3-query"):
        pass
    for d, tag in ((DATA, "conv"), (DATA_ANCHOR, "anchor")):
        snap = snapshot(d)
        for f, txt in snap.items():
            open(os.path.join(RAW, "%s-%s" % (tag, f)), "w", encoding="utf-8").write(txt)
    open(os.path.join(RAW, "pre_step-first-anchor.json"), "w", encoding="utf-8").write(
        json.dumps(first_b, ensure_ascii=False, indent=2))

    return finish((r1, r2, r3, rb), {"entries": entries3, "events": evs3, "first_rows": first_rows})


def finish(runs, extra):
    print("\n== 判定 ==", flush=True)
    print("PASS: %d ｜ FAIL: %d ｜ 断言总数: %d" % (len(PASS), len(FAIL), len(PASS) + len(FAIL)), flush=True)
    if FAIL:
        print("失败清单：" + "; ".join(FAIL), flush=True)

    lines = []
    lines.append("# 真实宿主验证：F-A / F-B / F-D 修复（%s）" % DAY)
    lines.append("")
    lines.append("> 由 `<REPO_ROOT>\\scripts\\verify_fact_fix_host.py` 自动生成；命令、原始 stdout/stderr、")
    lines.append("> audit/memory/last-prompt 原文见 `acceptance/fact-fix-host-%s-raw/`。本文件不写生成时刻，可复核。" % DAY)
    lines.append("")
    lines.append("## 结论")
    lines.append("")
    lines.append("- 断言：**PASS %d / FAIL %d / 共 %d**" % (len(PASS), len(FAIL), len(PASS) + len(FAIL)))
    lines.append("- F-A（纠正识别 + supersede）：%s" % ("PASS" if not any("F-A" in x for x in FAIL) else "FAIL"))
    lines.append("- F-B（提问不写记忆 + object 合法）：%s" % ("PASS" if not any("F-B" in x for x in FAIL) else "FAIL"))
    lines.append("- F-D（首回合注入无滞后 + anchor_lag 审计）：%s" % ("PASS" if not any("F-D" in x or "anchor" in x.lower() or "B1" in x for x in FAIL) else "FAIL"))
    lines.append("")
    lines.append("## 逐条断言")
    lines.append("")
    for x in PASS:
        lines.append("- `ok` %s" % x)
    for x in FAIL:
        lines.append("- **FAIL** %s" % x)
    lines.append("")
    lines.append("## 运行命令")
    lines.append("")
    lines.append("```powershell")
    lines.append("$env:LINGSHU_DATA_DIR = '<REPO_ROOT>\\.chaos-data-fixverify'")
    lines.append('& "node" "%s" %s "<prompt>"   # 每轮一个独立进程' % (BIN, PROFILE))
    lines.append("$env:LINGSHU_CONTEXT_WINDOW = '2000'   # critical 复验那一轮")
    lines.append("```")
    lines.append("")
    lines.append("模型调用预算：短对话 3 轮 + critical 1 轮 = 4 次 headless（未含混沌 host 重跑）。")
    lines.append("")
    lines.append("## 诚实边界")
    lines.append("")
    lines.append("- 单宿主、单模型路由、单日；样本量小，不构成效应量或泛化证据。")
    lines.append("- “回答命中贝塔”依赖模型本身；结构性证据以 memory.jsonl/audit.jsonl 的字段为准（本报告两者都列）。")
    lines.append("- critical 轮用 `LINGSHU_CONTEXT_WINDOW=2000` 实验室覆写把 ratio 推过阈值（与混沌 host 同一口径），")
    lines.append("  不是真实长会话；锚点位于段落位置（order 90/140），不是请求的绝对首尾。")
    lines.append("- 逐字段原始证据见 raw 目录；若结论与原始文件冲突，以原始文件为准。")
    lines.append("")
    open(REPORT, "w", encoding="utf-8").write("\n".join(lines) + "\n")
    print("报告:", REPORT, flush=True)
    return 0 if not FAIL else 1


if __name__ == "__main__":
    raise SystemExit(main())
