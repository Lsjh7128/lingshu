# -*- coding: utf-8 -*-
"""控制台趋势页验收探针（只读）。

    python tools/audit_console_trends.py
    python tools/audit_console_trends.py --json
    python tools/audit_console_trends.py --home <DSH_HOME> --console console/index.html

为什么单独一个探针：
- `audit_console_data.py` 检查的是 v0 四个数据源"够不够渲染"；
- 趋势页还多一层要求：**同一条指标要能按时间聚合成序列**。数据字段齐全但只有
  当前值，仍然画不出趋势。这两件事必须分开判。

它只读，不生成页面、不改插件状态。退出码：0 = 无 FAIL；1 = 有 FAIL。
"""
from __future__ import annotations

import argparse
import datetime as _dt
import io
import json
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DEFAULT_HOME = os.path.join(os.path.expanduser("~"), ".dsh")

# 设计稿（share-audit/transcript.md §5.9）的 9 个指标。
# 第二列写"要画出一条可信趋势线，至少需要什么"，第三列写与 docs/14 G1–G7 的对应。
METRICS = [
    ("召回命中率", "每次召回的分母 + 命中数（不是每个 step 的 matched）", "G7 的同一批会话基线"),
    ("误注入率", "注入条目 + 事后判定是否被采用/是否相关", "无"),
    ("注入 token 占比", "注入 token 数 + 上下文 token 总数", "G3"),
    ("supersede 误判率", "按 (subject,relation) 的取代记录 + 人工/规则复核结论", "G1"),
    ("Cold 重建准确率", "Cold 摘要条目 + 重建正确性抽检", "无"),
    ("置信度校准误差", "每轮置信度分档 + 事后正确性标签", "无"),
    ("情感漂移", "PAD 历史采样（当前 state 只有最新值）", "无"),
    ("目标完成率", "目标生命周期事件 + 完成判定（含证据）", "无"),
    ("宪法拒绝率", "宪法审计 verdict 事件 + 拒绝码", "G5/G6 的安全侧，但不是同一指标"),
]

# 每个指标的"最小证据"键（在 audit / state / memory 的顶层字段里找）。
# 找不到就说明当前落盘结构不足以直接算该指标；部分指标只能算代理值。
METRIC_EVIDENCE = {
    "召回命中率": ("audit", ("recall_id", "query_id", "matched_count", "hit_count")),
    "误注入率": ("audit", ("injected", "adopted", "misinjected", "verdict")),
    "注入 token 占比": ("audit", ("tokens", "prompt_tokens", "context_tokens", "token_ratio")),
    "supersede 误判率": ("audit", ("supersede_review", "supersede_correct", "sampled")),
    "Cold 重建准确率": ("audit", ("cold_rebuild", "rebuild_correct", "rebuild_sample")),
    "置信度校准误差": ("audit", ("confidence", "correct", "calibration")),
    "情感漂移": ("audit", ("pad", "valence", "arousal", "dominance")),
    "目标完成率": ("audit", ("goal_completed", "completion", "goal_status")),
    "宪法拒绝率": ("audit", ("constitution", "refuse", "verdict", "reject_code")),
}

# G1–G7：趋势页必须能画出阈值线，因此阈值也必须能落到图上。
GUARDRAILS = [
    ("G1", "supersede 误判率", "≤5%"),
    ("G2", "用户纠正率", "≤15%"),
    ("G3", "注入 token 占比", "≤15%"),
    ("G4", "检索延迟 P95", "≤150ms"),
    ("G5", "无命中时编造率", "=0"),
    ("G6", "拒绝边界回归通过率", "=100%"),
    ("G7", "召回相对基线下降", "≥ −2%"),
]

results: list[dict] = []


def add(level: str, check: str, detail: str) -> None:
    results.append({"level": level, "check": check, "detail": detail})


def read_jsonl(path: str) -> list[dict]:
    out = []
    if not os.path.isfile(path):
        return out
    for line in io.open(path, encoding="utf-8"):
        line = line.strip().lstrip("\ufeff")
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    return out


def read_json(path: str) -> dict:
    if not os.path.isfile(path):
        return {}
    try:
        return json.loads(io.open(path, encoding="utf-8").read().lstrip("\ufeff"))
    except json.JSONDecodeError:
        return {}


def text_of(path: str) -> str:
    if not os.path.isfile(path):
        return ""
    return io.open(path, encoding="utf-8", errors="replace").read()


def check_generator(gen_paths: list[str]) -> str:
    """趋势页可以并入 report_console.py，也可以是独立的 report_trends.py。
    只要有一个生成器带趋势渲染，就算有入口。返回命中的那个（没有则空串）。"""
    existing = [p for p in gen_paths if os.path.isfile(p)]
    if not existing:
        add("FAIL", "生成器存在", "找不到生成器: %s" % ", ".join(gen_paths))
        return ""
    for p in existing:
        src = text_of(p)
        markers = {k: len(re.findall(k, src, re.I)) for k in
                   ("趋势", "trend", "spark", "polyline", "series", "window")}
        # `window` 太泛（CSS/viewport 也会命中），只有它不算趋势实现
        hit = any(markers[k] for k in ("趋势", "trend", "spark", "polyline", "series"))
        if hit:
            add("INFO", "生成器·趋势区块", "%s 命中: %s" %
                (os.path.basename(p),
                 ", ".join("%s=%d" % (k, v) for k, v in markers.items() if v)))
            return p
    add("FAIL", "生成器·趋势区块",
        "%s 里都没有趋势/序列渲染代码（关键词全 0）" %
        "、".join(os.path.basename(p) for p in existing))
    return ""


def check_page(page_paths: list[str], audit: list[dict]) -> str:
    existing = [p for p in page_paths if os.path.isfile(p)]
    if not existing:
        add("FAIL", "页面存在", "找不到页面: %s" % ", ".join(page_paths))
        return ""
    page_path = existing[0]
    page = text_of(page_path)
    markers = {k: len(re.findall(k, page, re.I)) for k in
               ("趋势", "trend", "<svg", "<canvas", "sparkline")}
    if not any(markers.values()):
        add("FAIL", "页面·趋势区块",
            "%s 没有趋势区块（关键词全 0）；页面只有卡片/表格" % os.path.basename(page_path))
    else:
        add("INFO", "页面·趋势区块", "%s 命中: %s" % (
            os.path.basename(page_path),
            ", ".join("%s=%d" % (k, v) for k, v in markers.items() if v)))

    # 离线要求：不能有外部资源 / 网络调用
    net = len(re.findall(r"https?://|fetch\(|XMLHttpRequest|src\s*=\s*['\"]https?:", page, re.I))
    if net:
        add("FAIL", "页面·离线", "发现 %d 处网络/外部资源引用；控制台要求纯静态、无网络请求" % net)
    else:
        add("INFO", "页面·离线", "未发现外部资源或网络调用")

    # 新鲜度：页面上的"生成于"不能早于最后一条审计事件，否则趋势看起来是新的、其实不是
    m = re.search(r"生成于\s*([0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2})", page)
    if not m:
        add("WARN", "页面·新鲜度", "页面没有生成时间戳，无法判断展示的是哪一轮数据")
    elif audit:
        try:
            gen = _dt.datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
            last = _dt.datetime.fromtimestamp(max(float(a.get("ts") or 0) for a in audit) / 1000.0)
            lag = (last - gen).total_seconds() / 60.0
            if lag > 60:
                add("WARN", "页面·新鲜度", "页面比最后一条审计早 %.0f 分钟：静态快照不会自动更新" % lag)
            elif lag > 0:
                add("INFO", "页面·新鲜度", "页面比最后一条审计早 %.0f 分钟（静态快照的正常滞后）" % lag)
            else:
                add("INFO", "页面·新鲜度", "页面不早于最后一条审计（早 %.0f 分钟）" % -lag)
        except ValueError:
            add("WARN", "页面·新鲜度", "生成时间无法解析: %s" % m.group(1))
    return page_path


def check_audit(audit: list[dict]) -> None:
    if not audit:
        add("FAIL", "审计事件", "audit.jsonl 为空：无法聚合任何趋势")
        return

    kinds = {}
    for a in audit:
        kinds[a.get("kind", "?")] = kinds.get(a.get("kind", "?"), 0) + 1
    add("INFO", "审计事件", "%d 条 / %d 类: %s" % (
        len(audit), len(kinds),
        ", ".join("%s×%d" % (k, v) for k, v in sorted(kinds.items(), key=lambda kv: -kv[1])[:8])))

    # 时间戳：图上横轴要么 iso、要么可格式化的 ts；只有裸 ts 会让页面显示数字
    iso = sum(1 for a in audit if a.get("iso"))
    if iso == 0:
        add("WARN", "时间戳", "全部只有 ts（毫秒），没有 iso；趋势横轴需要统一格式化")
    else:
        add("INFO", "时间戳", "iso 覆盖 %d/%d" % (iso, len(audit)))

    # 跨度：14 天趋势至少要有 14 天数据（或明确标出"不足 14 天"）
    ts = [float(a["ts"]) for a in audit if a.get("ts")]
    if ts:
        days = (max(ts) - min(ts)) / 86400000.0
        add("INFO", "时间跨度", "%.1f 天（%d 条事件）" % (days, len(ts)))
        if days < 14:
            add("WARN", "14 天窗口", "只有 %.1f 天数据；页面必须显示「数据不足」而不是硬凑 14 个点" % days)
    else:
        add("FAIL", "时间跨度", "没有任何事件带 ts：无法按时间聚合")

    # 版本：指标点必须能归因到 param_version / prompt_version（ENG-1、docs/14 §7）
    for f in ("param_version", "prompt_version"):
        n = sum(1 for a in audit if a.get(f))
        if n == 0:
            add("FAIL", "版本归因", "没有任何事件带 %s：趋势页的版本筛选/对比无从谈起" % f)
        else:
            add("INFO", "版本归因", "%s 覆盖 %d/%d" % (f, n, len(audit)))

    # 指标快照 / 回滚 / 缺失：趋势页的三类关键事件
    has_snapshot = any("metric" in str(a.get("kind", "")).lower() or
                       "snapshot" in str(a.get("kind", "")).lower() or
                       "series" in a for a in audit)
    if not has_snapshot:
        add("FAIL", "指标快照", "没有 metrics/snapshot 类事件：只能从零散事件现算，无法复现历史点")
    has_rollback = any("rollback" in str(a.get("kind", "")).lower() for a in audit)
    if not has_rollback:
        add("FAIL", "回滚状态", "没有 learn/rollback 事件：9 张卡片的「跌破时的回滚状态」只能是编的")
    has_missing = any("missing" in str(a.get("kind", "")).lower() for a in audit)
    if not has_missing:
        add("FAIL", "缺失语义", "没有 guardrail_missing 事件：docs/14 §4.1 要求缺数据记事件、不填 0")

    # 聚合力：要按轮/会话聚合，必须有稳定的 id；pre_step 每 step 一条，直接数会重复计
    with_turn = sum(1 for a in audit if "turn" in a)
    add("INFO", "聚合键", "带 turn 的事件 %d/%d；pre_step 每 step 一条且无 query_id，"
                          "直接按条数算「命中率」会重复计数" % (with_turn, len(audit)))

    # G7 要求同批会话基线
    batch = sum(1 for a in audit if any(k in a for k in ("baseline", "batch", "cohort", "session_batch")))
    if batch == 0:
        add("FAIL", "同批基线", "没有任何 baseline/batch 字段：G7「同批会话对比」无法计算")


def check_metric_sources(audit: list[dict], state: dict, mem: list[dict]) -> None:
    audit_keys = set()
    for a in audit:
        audit_keys |= set(a.keys())
    state_keys = set((state or {}).keys())
    mem_keys = set()
    for m in mem:
        mem_keys |= set(m.keys())

    ready = partial = missing = 0
    for name, _need, guard in METRICS:
        src, keys = METRIC_EVIDENCE[name]
        pool = {"audit": audit_keys, "state": state_keys, "memory": mem_keys}[src]
        hit = [k for k in keys if k in pool]
        if hit:
            ready += 1
            add("INFO", "指标源", "%s：命中 %s（%s）" % (name, ",".join(hit), guard))
        else:
            # 有代理字段的算 partial，否则 missing
            proxy = {
                "召回命中率": "pre_step.matched（每 step 重复，非每次召回）",
                "情感漂移": "state.dev（只有当前值，无历史）",
                "目标完成率": "state.goal.events（只有当前目标，无历史快照）",
            }.get(name)
            if proxy:
                partial += 1
                add("WARN", "指标源", "%s：只有代理字段（%s），不能直接画趋势" % (name, proxy))
            else:
                missing += 1
                add("FAIL", "指标源", "%s：没有可用字段（需要 %s）" % (name, ", ".join(keys)))
    add("INFO", "指标源汇总", "可直接算 %d / 只有代理 %d / 完全缺 %d（共 %d）"
        % (ready, partial, missing, len(METRICS)))


def check_guardrail_coverage() -> None:
    """9 个 UI 指标和 7 条护栏不是同一集合；趋势页必须先声明画哪一套。"""
    ui = {m[0] for m in METRICS}
    g = {x[1] for x in GUARDRAILS}
    only_g = [x for x in g if x not in ui]
    add("WARN", "指标集一致性",
        "设计稿 9 指标 vs docs/14 §4 的 7 条护栏：2 条同名（supersede 误判率、注入 token 占比）"
        "+ 1 条相关（召回命中率 ↔ G7）；仅护栏有而指标页没有: %s；"
        "两套都画且不合并是 16 条，按语义合并后 13–14 个，必须先在规格里定死" % "、".join(only_g))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--home", default=os.environ.get("DSH_HOME", DEFAULT_HOME))
    ap.add_argument("--console", default=os.path.join(ROOT, "console", "index.html"))
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    pdir = os.path.join(args.home, "plugins", "dsh-lingshu")
    gen_paths = [os.path.join(ROOT, "tools", "report_trends.py"),
                 os.path.join(ROOT, "tools", "report_console.py")]
    page_paths = [os.path.join(ROOT, "console", "trends.html"),
                  os.path.join(ROOT, "console", "trend.html"),
                  args.console]

    audit = read_jsonl(os.path.join(pdir, "audit.jsonl"))
    mem = read_jsonl(os.path.join(pdir, "memory.jsonl"))
    state = read_json(os.path.join(pdir, "state.json"))

    gen_used = check_generator(gen_paths)
    page_used = check_page(page_paths, audit)
    check_audit(audit)
    check_metric_sources(audit, state, mem)
    check_guardrail_coverage()

    counts = {"FAIL": 0, "WARN": 0, "INFO": 0}
    for r in results:
        counts[r["level"]] = counts.get(r["level"], 0) + 1
    verdict = "FAIL" if counts["FAIL"] else ("WARN" if counts["WARN"] else "PASS")

    if args.json:
        print(json.dumps({"pdir": pdir, "console": page_used or args.console,
                          "generator": gen_used, "verdict": verdict,
                          "counts": counts, "results": results,
                          "audit": len(audit), "memory": len(mem)},
                         ensure_ascii=False, indent=2))
        return 0 if counts["FAIL"] == 0 else 1

    print("LINGSHU 控制台趋势页验收探针 · %s" % pdir)
    print("生成器：%s ｜ 页面：%s" % (gen_used or "（无）", page_used or "（无）"))
    print("审计 %d 条 ｜ 记忆 %d 条 ｜ 结论 %s（FAIL %d / WARN %d）"
          % (len(audit), len(mem), verdict, counts["FAIL"], counts["WARN"]))
    print("-" * 78)
    for r in results:
        print("[%-4s] %-14s %s" % (r["level"], r["check"], r["detail"]))
    print("-" * 78)
    if counts["FAIL"]:
        print("有 FAIL：趋势页现在不成立（缺页面或缺数据契约），见上。")
    elif counts["WARN"]:
        print("能画，但有 %d 处会误导（见 WARN）。" % counts["WARN"])
    else:
        print("趋势页通过探针。")
    return 0 if counts["FAIL"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
