# -*- coding: utf-8 -*-
"""Phase B · 演化剧本（A5，契约 §6）

一条命令把"红队变异 → 蓝队打补丁 → 回归锁 → 双防线评估"闭环跑成一条时间线：

    python tools/run_coevolution.py --out experiments/phase-b/runs \
        --max-generations 20 --patience 3 --variants-per-gen 40 \
        --seed 20261028 --token-budget 200000 [--dry-run]

每一代严格 6 步（顺序不许乱，契约 §6.2）：

    1) 红队生成   A4.generate(g, train_seeds, n=--variants-per-gen, rng)  → variants
    2) 判定       A4.save_results(variants, evolved_patch, out_path)      → red_results/bypasses
    3) 打补丁     A1.propose_patch(bypasses, evolved_patch, generation_id=g) → patch item | None
    4) 回归锁     patch 存在时 subprocess 跑 tools/run_phase_a_regression.py --suite audit892
                  **只有 exit==0 且 failed==0，补丁才被允许进入演化防线**（顺序颠倒 = 绕过红线）
    5) 双防线评估 固定 held-out 零日集分别对 ①基线防线 ②演化防线求拦截率；良性语料求误伤率
                  良性误伤率高于基线 → 丢弃本代补丁（红线 §0.3 第二道闸门）
    6) 落盘 + 判停 追加 coevolution_log.jsonl 一行；检查三条停止条件

三条停止条件（契约 §6.1）：
    converged      连续 --patience 代 bypass_success_count == 0
    token_budget   api_tokens_used > --token-budget（本阶段零 API，恒 0 → 该条件未获真实检验，
                   代码路径已实现并由单测注入模拟覆盖）
    max_generations 已跑代数 >= --max-generations

A5 的写作用域（契约 §2）：tools/run_coevolution.py、tests/test_run_coevolution.py、
experiments/phase-b/runs/**。本文件**不写** rules/candidate_patch.yaml（A1 的写作用域），
演化防线在内存里推进，每代快照落到 run 目录的 evolved_patch_snapshot.json。

诚实边界（契约 §8）：变异是确定性规则变异而非 LLM 红队；语言变异是离线词表近似；
api_tokens_used == 0 是事实；零日集是 held-out 变体而非 OOD 数据。详见落盘 evidence 的 limitations。

── Phase C 扩展（A5，契约 §5.1）：元控制器 ──────────────────────────────

Phase B 官方 run（run-20261028-185512，20 代）证明一阶词法自愈在第 1 代就把 held-out
拦截率顶到 0.775 平台，之后 13 个补丁对 held-out 零增量。Phase C 要让系统**观察到自己饱和**：

    --enable-meta-controller       # 默认关；关闭时 Phase B 行为与官方 run 逐字一致
    --handoff-to phase-c           # 触发后写交接包 handoff.json（需同时开元控制器）

每代在第 6 步落盘前计算 `marginal_coverage(state)` = 本代补丁在 held-out（固定零日集）
上带来的**增量**拦截率；无补丁代 = 0.0（定义与理由见该函数 docstring）。连续
`META_ZERO_THRESHOLD`(=3) 代 marginal_coverage == 0.0 → `stop_reason="trigger_phase_c"`，
停止补丁生成（停止 Phase B 循环），并把 `{"type":"Trigger_Phase_C",...}` 事件写到
**隔离目录** `experiments/phase-c/runs/<run_id>/audit.jsonl`——绝不允许写真实插件的
`.chaos-data*/audit.jsonl`（由 assert_isolated_audit_path 硬拦截）。

Phase C 模式（元控制器开启且未显式改 --out）默认把 run 目录放进隔离目录
`experiments/phase-c/runs/`，便于 audit.jsonl / handoff.json 与逐代 log 同处一地；
`--candidate-ledger` 可把候选账本指到隔离路径，避免 A1 的规范账本被演示 run 推进。
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib
import inspect
import io
import json
import os
import random
import subprocess
import sys
import time
import traceback
from datetime import datetime, timedelta, timezone

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOOLS = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from tools import phase_b_defense as defense  # noqa: E402

REGRESSION_TOOL = os.path.join(TOOLS, "run_phase_a_regression.py")
DEFAULT_OUT = os.path.join("experiments", "phase-b", "runs")
DEFAULT_MAX_GENERATIONS = 20
DEFAULT_PATIENCE = 3
DEFAULT_VARIANTS_PER_GEN = 40
DEFAULT_SEED = 20261028
DEFAULT_TOKEN_BUDGET = 200000
REGRESSION_SUITE = "audit892"
REGRESSION_TIMEOUT = 1800
# 规范候选补丁账本（A1 维护；Lead 裁决：官方 run 的每代 propose_patch 必须写这里，
# 同时在 run 目录留 candidate_patch_snapshot_g<g>.yaml 快照）
DEFAULT_LEDGER_REL = os.path.join("rules", "candidate_patch.yaml")
# 零日集口径（Lead 2026-10-28 通知）：held-out 集与训练流**只差种子、不差变异强度**。
# A4 的 build_zeroday_set 默认 generation_id=2（最大深度档），A5 必须显式传 0。
ZERODAY_GENERATION_ID = 0

# ── Phase C（二阶控制论观察器，契约 §5.1）───────────────
# 隔离产物根目录：audit.jsonl / handoff.json 只允许写这里的 <run_id>/ 下。
DEFAULT_PHASE_C_RUNS = os.path.join("experiments", "phase-c", "runs")
# 触发判据（契约 §5.1 / H0 §三.1）：连续 3 代 marginal_coverage == 0.0。
# 故意不做成 CLI 可调：契约钉死 3 代，开放调节等于允许"调阈值凑触发"。
META_ZERO_THRESHOLD = 3
PHASE_C_HANDOFF_TARGETS = ("phase-c",)

META_LIMITATIONS = [
    "触发判据只观察 held-out（固定零日集）的边际拦截增量；这是系统对自身饱和的**测量**，"
    "不是因果保证——held-out 增益为 0 也可能来自“本代无可打补丁的绕过样本”，"
    "两种原因在 marginal_coverage 上不可区分，判读时须结合 bypass_success_count 与 patch_rejected。",
    "“连续 3 代”阈值由 H0 契约 §5.1 固定，未做敏感性分析；3 是工程选择，不是统计检验结论。",
    "零日集是 held-out 变体而非 OOD：饱和结论只能表述为“对该 held-out 分布饱和”。",
    "元控制器只做观测与交接，不改变任何防线判定逻辑；audit 事件写在隔离目录 "
    "experiments/phase-c/runs/<run_id>/audit.jsonl，绝不写真实插件 .chaos-data*/audit.jsonl。",
]

# 契约 §6.3 的六个核心字段（字段名不许改）
CORE_FIELDS = (
    "generation_id",
    "red_team_variants_count",
    "bypass_success_count",
    "blue_team_patch_generated",
    "regression_test_pass",
    "benign_false_positive_rate",
)

LIMITATIONS = [
    "变异引擎是确定性规则变异（编码/词表/嵌套），不是 LLM 红队；不得写成“自主攻防智能体”。",
    "语言变异是离线词表近似（provenance: offline_glossary），不是真实机器翻译。",
    "api_tokens_used == 0 是事实：本阶段零模型调用，token 预算停止条件未获真实检验"
    "（代码路径已实现，仅由单元测试注入模拟值验证触发）。",
    "被测防线是 Phase A 规则型闸门（词表匹配），编码变异能绕过属可预期；"
    "本文贡献是闭环自愈机制与度量方法，不是“Phase A 很弱”。",
    "零日集与训练集来自同一批种子的不同 split，不是独立分布外数据（not OOD）；"
    "只能说 held-out 变体，不许写“零日/未见攻击”当 OOD 证据。",
    "零日集按 Lead 口径用 generation_id=0 变异分布（与训练流同变异强度、只差 held-out 种子），"
    "避免把“泛化”与“攻击变强”混在一起；口径见 evidence.zeroday_protocol。",
    "回归锁口径：audit892 = 2026-10-27 审计快照的 14 文件口径；今天同口径实测 915 项，"
    "与 892 不可换算，报告须注明口径与实测值。",
    "tests/test_chaos_campaign.py 的 3 项失败属沙箱写权限环境性失败（非断言失败），"
    "不计入补丁判定（audit892 集合不含该文件）。",
    "补丁以 run 目录内 evolved_patch_snapshot.json 快照形式保存；rules/candidate_patch.yaml 属 A1 "
    "写作用域，本脚本不写候选/生产规则文件，生产规则零改动。",
    "A1 内部良性自检结果通过 LAST_ADJUDICATION 回传；A5 另以“演化防线对良性语料实测误伤率 > 基线”"
    "作为第二道闸门，不满足则丢弃本代补丁并记录 rejected。",
    "官方 run 的候选账本按 Lead 裁决写规范入口 rules/candidate_patch.yaml（A1 persist），"
    "run 目录另存每代快照 candidate_patch_snapshot_g<g>.yaml 供单 run 回溯；"
    "生产规则零改动，合入生产 = H0 人工审批。",
    "A5 未调用 A1 的 write_proposal（proposals/phase-b 属 A1 写作用域）；如需 PROP-*.yaml "
    "人工审批件，由 Lead/A1 依据规范账本条目生成。",
    "determinism：同 seed 的变异流、绕过序列与各拦截率可复现；但补丁 id 由规范账本当前状态"
    "决定（Lead 裁决官方 run 写唯一规范账本 rules/candidate_patch.yaml），复跑时 id 会顺延——"
    "单 run 内每代 candidate_patch_snapshot_g<g>.yaml 保证可回溯。",
]


# ── 通用小工具 ──────────────────────────────────────────

def _now_iso() -> str:
    """契约示例使用 +08:00 本地时区。"""
    tz = timezone(timedelta(hours=8))
    return datetime.now(tz).isoformat(timespec="seconds")


def _read_json(path: str):
    if not os.path.exists(path):
        return None
    with io.open(path, encoding="utf-8") as fh:
        return json.load(fh)


def _read_jsonl(path: str) -> list[dict]:
    if not os.path.exists(path):
        return []
    rows = []
    with io.open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                rows.append(json.loads(line))
    return rows


def _write_jsonl(path: str, rows: list[dict]) -> None:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        for row in rows:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def _sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _rel(path: str) -> str:
    ap = os.path.abspath(path)
    try:
        rel = os.path.relpath(ap, ROOT)
    except ValueError:
        return ap
    return ap if rel.startswith("..") else rel.replace(os.sep, "/")


def _round4(x: float) -> float:
    return round(float(x), 4)


def _unique_run_dir(base: str, run_id: str) -> str:
    """同一秒内多次跑（例如确定性测试）时保证 run 目录不互相覆盖。"""
    path = os.path.join(base, run_id)
    if not os.path.exists(path):
        return path
    n = 2
    while True:
        cand = os.path.join(base, "%s-%02d" % (run_id, n))
        if not os.path.exists(cand):
            return cand
        n += 1


# ── Phase C 元控制器（契约 §5.1；独立成纯函数/小类便于单测）──────

def _norm_abs(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def is_isolated_audit_path(path: str, audit_root: str | None = None) -> bool:
    """audit 是否被允许写：必须落在隔离根目录（默认 experiments/phase-c/runs）之下，
    且路径里绝不允许出现 `.chaos-data`（真实插件/演示运行的审计账本）。

    这条闸门是硬红线（契约 §5.1）：元控制器的观察事件只能进隔离目录，
    混进生产侧 audit 账本就等于伪造了系统自身的审计记录。
    """
    ap = _norm_abs(path).replace("\\", "/")
    if ".chaos-data" in ap:
        return False
    root = _norm_abs(audit_root or os.path.join(ROOT, DEFAULT_PHASE_C_RUNS)).replace("\\", "/")
    return ap == root or ap.startswith(root + "/")


def assert_isolated_audit_path(path: str, audit_root: str | None = None) -> str:
    """不允许写就抛 RuntimeError（fail-closed，绝不静默改道）。"""
    if not is_isolated_audit_path(path, audit_root):
        raise RuntimeError(
            "拒绝写非隔离目录的 audit：%s（只允许 %s/**，且绝不允许 .chaos-data*）"
            % (path, audit_root or DEFAULT_PHASE_C_RUNS))
    return path


def marginal_coverage(state: dict) -> dict:
    """契约 §5.1：本代补丁在 held-out（固定零日集）上带来的**增量**拦截率。

    定义（写死；任何调用方不得另行发挥）：
        heldout_gain_count = 本代演化防线在零日集上多拦的条数
                           = heldout_intercepted - heldout_intercepted_prev
        heldout_gain_rate  = heldout_gain_count / zeroday_total
        marginal_coverage  = heldout_gain_rate
                             （本文件里两个字段同值，并列保留以对齐契约字段名）

    参照点是**上一代演化防线**，不是 Phase A 基线：数值回答的是"本代补丁有没有让
    held-out 拦截率上升"，而不是"累计比基线高多少"。首代（gen 0）参照点是基线防线本身。

    **无补丁代 = 0.0**（契约 §5.1 明确要求，这里给出理由）：

      1) 没有补丁被接受进入演化防线（propose_patch 返回 None，或补丁被回归锁 / 良性误伤
         闸门拒绝），防线一字未变，held-out 拦截条数的真实增量就是 0——不是"未知"，
         也不是"缺失"，而是一个真实的观测值；
      2) 语义上这一代系统没有获得任何新的泛化能力。若把"未生效的补丁"记成非零增量，
         等于用纸面 mutation 冒充防线能力，会让"饱和"永远观察不到；
      3) 这正是二阶控制论要观察的信号本身：连续多代"变异/补丁照常在产出，但 held-out
         增益为 0" → 一阶词法变异的边际收益已归零，必须切换到二阶（语义）防线。

    state 输入键（纯函数：不读全局状态；缺键按 0/None 处理）：
        generation_id、patch_id（本代真正进入演化防线的补丁 id，None = 无补丁）、
        zeroday_total、heldout_intercepted_prev、heldout_intercepted
    """
    generation_id = int(state.get("generation_id") or 0)
    patch_id = state.get("patch_id")
    total = int(state.get("zeroday_total") or 0)
    prev = int(state.get("heldout_intercepted_prev") or 0)
    now = int(state.get("heldout_intercepted") or 0)
    # 无补丁代（含被拒补丁）→ 真实增量为 0，见 docstring 理由 1/2。
    gain = (now - prev) if (patch_id and total > 0) else 0
    rate = (gain / total) if total else 0.0
    return {
        "generation_id": generation_id,
        "patch_id": str(patch_id) if patch_id else None,
        "heldout_gain_count": int(gain),
        "heldout_gain_rate": _round4(rate),
        "marginal_coverage": _round4(rate),
    }


class MetaController:
    """二阶控制论观察器：只看 held-out 的逐代增量，不看训练流指标（契约 §5.1）。

    - 关闭（默认）：`observe` 只记录观测，不累计连续零代数、不写 audit、不影响任何
      停止条件 → Phase B 行为与官方 run 逐字一致（可复现性证据）；
    - 开启：连续 `threshold`(3) 代 marginal_coverage == 0.0 → 置 triggered，
      把 Trigger_Phase_C 事件追加到隔离 audit.jsonl；调用方据 triggered 停止补丁生成。
    """

    def __init__(self, *, enabled: bool = False, audit_dir: str | None = None,
                 audit_root: str | None = None, threshold: int = META_ZERO_THRESHOLD):
        self.enabled = bool(enabled)
        self.threshold = int(threshold)
        self.audit_root = audit_root or os.path.join(ROOT, DEFAULT_PHASE_C_RUNS)
        self.audit_dir = audit_dir
        self.audit_path = os.path.join(audit_dir, "audit.jsonl") if audit_dir else None
        self.history: list[dict] = []
        self.consecutive_zero = 0
        self.triggered = False
        self.trigger_event: dict | None = None

    # -- 观测 -------------------------------------------------
    def observe(self, state: dict, **ctx) -> dict:
        """喂一个 marginal_coverage 的 state，返回本代观测与触发状态。"""
        obs = marginal_coverage(state)
        if not self.enabled:
            # 关闭时必须"零副作用"：不累计、不写盘、不改 Phase B 的任何字段。
            self.history.append(dict(obs, consecutive_zero=None, triggered=False))
            return {"observation": obs, "consecutive_zero": None, "triggered": False}
        if obs["marginal_coverage"] == 0.0:
            self.consecutive_zero += 1
        else:
            self.consecutive_zero = 0
        entry = dict(obs, consecutive_zero=self.consecutive_zero)
        self.history.append(entry)
        fired = (not self.triggered) and self.consecutive_zero >= self.threshold
        if fired:
            self.triggered = True
            self.trigger_event = self._make_event(obs, **ctx)
            self._append_audit(self.trigger_event)
        return {"observation": obs, "consecutive_zero": self.consecutive_zero,
                "triggered": fired}

    def _make_event(self, obs: dict, **ctx) -> dict:
        return {
            "type": "Trigger_Phase_C",
            "generation_id": obs["generation_id"],
            "consecutive_zero": self.consecutive_zero,
            "patches_total": int(ctx.get("patches_total") or 0),
            "heldout_rate": _round4(float(ctx.get("heldout_rate") or 0.0)),
            "marginal_coverage": obs["marginal_coverage"],
            "heldout_gain_count": obs["heldout_gain_count"],
            "zeroday_total": int(ctx.get("zeroday_total") or 0),
            "patch_id_this_generation": obs["patch_id"],
            "run_id": ctx.get("run_id"),
            "threshold": self.threshold,
            "rule": "consecutive_zero_marginal >= %d" % self.threshold,
            "source": "tools/run_coevolution.py:MetaController",
            "ts": _now_iso(),
        }

    def _append_audit(self, event: dict) -> str:
        if not self.audit_path:
            raise RuntimeError("元控制器已触发但未配置 audit_dir（隔离目录）")
        assert_isolated_audit_path(self.audit_path, self.audit_root)
        os.makedirs(os.path.dirname(os.path.abspath(self.audit_path)) or ".", exist_ok=True)
        with io.open(self.audit_path, "a", encoding="utf-8", newline="\n") as fh:
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        return self.audit_path

    # -- 汇总 -------------------------------------------------
    def summary(self) -> dict:
        return {
            "enabled": self.enabled,
            "threshold": self.threshold,
            "triggered": self.triggered,
            "consecutive_zero_final": self.consecutive_zero if self.enabled else None,
            "trigger_event": copy.deepcopy(self.trigger_event),
            "audit_path": _rel(self.audit_path) if self.audit_path else None,
            "history": copy.deepcopy(self.history),
        }


def build_handoff(*, run_id: str, run_dir: str, log_rows: list[dict],
                  trigger_event: dict, marginals: list[dict], log_path: str,
                  zeroday_path: str, zeroday_sha: str, zeroday_count: int,
                  zeroday_generation_id: int | None, patches_total: int,
                  snapshot_path: str) -> dict:
    """契约 §5.1 的交接包：Phase B 自愈饱和 → Phase C 语义升维。

    evidence 全部由本 run 的逐代 log 与元控制器观测导出，不许手填数字。
    """
    rates = [r.get("intercept_rate_evolved_zeroday", 0.0) for r in log_rows]
    final_rate = rates[-1] if rates else 0.0
    saturated_since = None
    for i, rate in enumerate(rates):
        if abs(float(rate) - float(final_rate)) <= 1e-12:
            saturated_since = i
            break
    zero_gain_with_patch = [
        int(row.get("generation_id", i))
        for i, (row, obs) in enumerate(zip(log_rows, marginals))
        if obs.get("marginal_coverage") == 0.0 and row.get("blue_team_patch_generated") == 1
    ]
    return {
        "schema": "phase-c-handoff-v1",
        "from": "phase-b",
        "to": "phase-c",
        "reason": "marginal_coverage_saturated",
        "created_at": _now_iso(),
        "phase_b_run": {
            "run_id": run_id,
            "run_dir": _rel(run_dir),
            "log_path": _rel(log_path),
            "generations_run": len(log_rows),
            "stop_reason": "trigger_phase_c",
            "evolved_patch_snapshot": _rel(snapshot_path),
            "patches_total": int(patches_total),
        },
        "trigger": copy.deepcopy(trigger_event),
        "evidence": {
            "criterion": "连续 %d 代 marginal_coverage == 0.0" % META_ZERO_THRESHOLD,
            "trigger_generation": int(trigger_event.get("generation_id")),
            "consecutive_zero": int(trigger_event.get("consecutive_zero") or 0),
            "heldout_rate_at_trigger": trigger_event.get("heldout_rate"),
            "heldout_rate_series": list(rates),
            "heldout_saturated_since_generation": saturated_since,
            "marginal_coverage_series": [o.get("marginal_coverage") for o in marginals],
            "heldout_gain_count_series": [o.get("heldout_gain_count") for o in marginals],
            "zero_gain_generations_with_patch": zero_gain_with_patch,
            "zeroday_set": {
                "path": _rel(zeroday_path),
                "count": int(zeroday_count),
                "sha256": zeroday_sha,
                "generation_id": zeroday_generation_id,
            },
            "interpretation": (
                "一阶词法变异对 held-out 的边际增益已归零：Phase B 自愈达到其可观测极限，"
                "需要切换到二阶（语义）防线。注意这不是“补丁写得更好了”的证据，"
                "而是“词法层已无可迁移增益”的证据。"),
        },
        "limitations": list(META_LIMITATIONS),
        "next": {
            "stage2": "调用 Lead 的 tools/phase_c_eval.py 出三系统（A/B/C）对比",
            "command": "python tools/run_phase_c.py",
        },
    }


def write_handoff(path: str, payload: dict, audit_root: str | None = None) -> str:
    """交接包与 audit 同写隔离目录，写前过隔离闸门（fail-closed）。"""
    assert_isolated_audit_path(path, audit_root)
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with io.open(path, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    return path


# ── 停止条件（契约 §6.1，独立成纯函数便于单测）──────────

def check_stop_conditions(*, generations_run: int, max_generations: int,
                          consecutive_zero_bypass: int, patience: int,
                          api_tokens_used: int, token_budget: int,
                          consecutive_zero_marginal: int = 0,
                          meta_zero_threshold: int = 0) -> str | None:
    """返回 stop_reason 或 None。顺序：Phase C 交接 → 收敛 → token 预算 → 代数上限。

    `meta_zero_threshold <= 0`（默认）= 元控制器关闭，行为与 Phase B 完全一致；
    两个新参数都有默认值，所以 Phase B 的既有调用/单测不受影响。

    Phase C 交接排第一：它表达的是"一阶自愈已饱和、该换二阶防线了"，
    比"代数跑满"更本质——到了上限才报 max_generations 会把饱和信号淹掉。
    """
    if meta_zero_threshold > 0 and consecutive_zero_marginal >= meta_zero_threshold:
        return "trigger_phase_c"
    if patience > 0 and consecutive_zero_bypass >= patience:
        return "converged"
    if api_tokens_used > token_budget:
        return "token_budget"
    if generations_run >= max_generations:
        return "max_generations"
    return None


# ── 回归锁 ──────────────────────────────────────────────

def build_regression_cmd(run_dir: str, generation_id: int) -> list[str]:
    json_path = os.path.join(run_dir, "regression_gen%d.json" % generation_id)
    return [sys.executable, "-X", "utf8", REGRESSION_TOOL,
            "--suite", REGRESSION_SUITE, "--json", json_path]


def default_regression_runner(run_dir: str, generation_id: int,
                              timeout: int = REGRESSION_TIMEOUT) -> dict:
    """跑 Phase A 回归锁。fail-closed：解析不到汇总行或 exit!=0 一律不通过。"""
    cmd = build_regression_cmd(run_dir, generation_id)
    json_path = cmd[cmd.index("--json") + 1]
    t0 = time.time()
    try:
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace", timeout=timeout)
        rc, out = proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        rc, out = 124, "TIMEOUT after %ss" % timeout
    seconds = round(time.time() - t0, 2)
    summary = _read_json(json_path) or {}
    passed = summary.get("total_passed")
    failed = summary.get("total_failed")
    files = summary.get("files")
    unparsed = summary.get("unparsed_summary_files") or []
    parse_ok = isinstance(passed, int) and isinstance(failed, int) and (passed + failed) > 0
    ok = bool(rc == 0 and parse_ok and failed == 0 and not unparsed)
    return {
        "ok": ok,
        "returncode": rc,
        "suite": REGRESSION_SUITE,
        "files": files,
        "passed": passed,
        "failed": failed if isinstance(failed, int) else None,
        "seconds": seconds,
        "json_path": _rel(json_path),
        "parse_error": not parse_ok,
        "stdout_tail": [ln for ln in out.strip().splitlines() if ln.strip()][-6:],
    }


# ── 模块加载（A4/A1 由队友维护，A5 只读）────────────────

def load_teammate_modules() -> tuple[object, object]:
    """导入 A4 红队 / A1 蓝队模块。缺失时给可执行的错误信息，绝不自己重实现变异器/补丁器。"""
    mods = {}
    for name, who, path in (
        ("tools.red_team_evolver", "A4 红队", "tools/red_team_evolver.py"),
        ("tools.blue_team_patcher", "A1 蓝队", "tools/blue_team_patcher.py"),
    ):
        try:
            mods[name] = importlib.import_module(name)
        except ImportError as exc:
            raise RuntimeError(
                "依赖模块未就绪：%s（%s，%s）。A5 不自行实现变异器/补丁器；"
                "等队友落盘或联系 Lead 协调。" % (name, who, path)) from exc
    return mods["tools.red_team_evolver"], mods["tools.blue_team_patcher"]


# ── 样本适配（A4 schema 用 payload，evaluate 需要 text）──

def _id_of(item) -> str:
    if isinstance(item, dict):
        for key in ("id", "variant_id", "seed_id"):
            if item.get(key):
                return str(item[key])
        return ""
    return ""


def _text_of(item) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        for key in ("text", "payload"):
            val = item.get(key)
            if isinstance(val, str) and val:
                return val
    return ""


def _to_eval_items(rows: list) -> list[dict]:
    out = []
    for i, row in enumerate(rows):
        out.append({"id": _id_of(row) or "item-%04d" % i, "text": _text_of(row),
                    "category": (row.get("category") if isinstance(row, dict) else None)})
    return out


def _mix_counts(variants: list[dict], key: str, split_combined: bool = False) -> dict[str, int]:
    out: dict[str, int] = {}
    for v in variants:
        if not isinstance(v, dict):
            continue
        val = v.get(key)
        vals = str(val).split("+") if (split_combined and val) else [str(val)]
        for one in vals:
            one = one.strip() or "unknown"
            out[one] = out.get(one, 0) + 1
    return out


# ── A1 集成钩子（只调用 A1 公开 API，不写 A1 作用域文件）──

def _call_build_zeroday(red_team, seeds_heldout: list[dict], *, seed: int, out_path: str):
    """构造固定零日集（A4 一次生成、落盘、之后只读）。

    Lead 口径（2026-10-28）：显式传 `generation_id=0`，让 held-out 集与训练流
    **只差种子、不差变异强度**；A4 默认值 2（最大深度档）会把“泛化”和“攻击变强”混在一起。
    返回 (rows, generation_id_used)；A4 若尚未支持该 kwarg（旧版/测试 fake），
    返回 generation_id_used=None，并在 evidence 里如实注明。
    """
    fn = red_team.build_zeroday_set
    kwargs = {"seed": seed, "out_path": out_path}
    used = None
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    if "generation_id" in params:
        kwargs["generation_id"] = ZERODAY_GENERATION_ID
        used = ZERODAY_GENERATION_ID
    return fn(seeds_heldout, **kwargs), used


def _call_propose_patch(blue_team, bypasses: list[dict], current_patch: dict,
                        generation_id: int, ledger_path: str):
    """调用 A1.propose_patch；若实现支持 ledger_path（A1 真实模块支持），
    把候选账本指到 **run 目录内**——A5 不写 rules/candidate_patch.yaml（A1 作用域），
    同时保证同 seed 两次跑时补丁 id 从空账本起算（determinism）。"""
    fn = blue_team.propose_patch
    kwargs = {"generation_id": generation_id}
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    if "ledger_path" in params:
        kwargs["ledger_path"] = ledger_path
    return fn(bypasses, current_patch, **kwargs)


def _call_update_status(blue_team, patch_id, status: str, ledger_path: str,
                        reason: str, evidence) -> bool:
    """A1 提供的账本状态机（candidate → accepted_sandbox / rejected）。
    失败只告警不中断演化循环（状态落盘失败不应让整轮作废）。"""
    fn = getattr(blue_team, "update_patch_status", None)
    if fn is None or not patch_id:
        return False
    kwargs = {}
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        params = {}
    if "ledger_path" in params:
        kwargs["ledger_path"] = ledger_path
    if "reason" in params:
        kwargs["reason"] = reason
    if "evidence" in params:
        kwargs["evidence"] = evidence
    try:
        fn(str(patch_id), status, **kwargs)
        return True
    except Exception as exc:  # noqa: BLE001
        print("[WARN] update_patch_status(%s, %s) 失败：%s" % (patch_id, status, exc))
        return False


def _record_rejection(rejected: list[dict], entry: dict) -> None:
    """按 (id, reason) 去重记录补丁被拒证据（同一补丁不要刷屏）。"""
    for old in rejected:
        if str(old.get("id")) == str(entry.get("id")) and old.get("reason") == entry.get("reason"):
            return
    rejected.append(entry)


def _record_adjudication(blue_team, rejected: list[dict], generation_id: int) -> None:
    """读取 A1.LAST_ADJUDICATION：propose_patch 返回 None 时，A1 可能已拒绝了一个补丁
    （例如良性自检不过）。把这类内部拒绝也如实记进 patch_rejected，而不是静默丢掉。"""
    adj = getattr(blue_team, "LAST_ADJUDICATION", None)
    if not isinstance(adj, dict) or not adj:
        return
    if adj.get("generation_id") not in (None, generation_id):
        return
    reason = str(adj.get("reason") or "")
    ignorable = ("no_bypass_input", "all_bypasses_already_intercepted", "proposed",
                 "no_patchable_surface_evidence", "")
    if reason in ignorable:
        return
    patch_id = adj.get("patch_id")
    if not patch_id:
        return
    mapped = "benign_fp_internal" if reason == "benign_new_false_positive" else reason
    fp = adj.get("benign_fp") or {}
    _record_rejection(rejected, {
        "id": str(patch_id),
        "reason": mapped,
        "generation_id": generation_id,
        "evidence": {
            "source": "A1.LAST_ADJUDICATION",
            "adjudication_reason": reason,
            "new_fp_ids": list(fp.get("new_fp_ids") or [])[:10],
            "patch_fp_ids": list(fp.get("patch_fp_ids") or [])[:10],
        },
    })


# ── 主循环 ──────────────────────────────────────────────

def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(
        description="Phase B 演化剧本（A5）：红队变异 → 蓝队补丁 → 回归锁 → 双防线评估")
    ap.add_argument("--out", default=DEFAULT_OUT, help="runs 根目录（默认 experiments/phase-b/runs）")
    ap.add_argument("--max-generations", type=int, default=DEFAULT_MAX_GENERATIONS)
    ap.add_argument("--patience", type=int, default=DEFAULT_PATIENCE)
    ap.add_argument("--variants-per-gen", type=int, default=DEFAULT_VARIANTS_PER_GEN)
    ap.add_argument("--seed", type=int, default=DEFAULT_SEED)
    ap.add_argument("--token-budget", type=int, default=DEFAULT_TOKEN_BUDGET)
    ap.add_argument("--dry-run", action="store_true",
                    help="只跑 1 代、不写 coevolution_evidence.json（快速自检）")
    # ── Phase C（契约 §5.1）──────────────────────────────
    ap.add_argument("--enable-meta-controller", action="store_true",
                    help="开启 Phase C 元控制器（默认关：关闭时 Phase B 行为与官方 run "
                         "逐字一致，这是可复现性证据）")
    ap.add_argument("--handoff-to", choices=list(PHASE_C_HANDOFF_TARGETS), default=None,
                    help="触发 Phase C 后写出交接包 handoff.json（需同时 --enable-meta-controller）")
    ap.add_argument("--phase-c-runs", default=DEFAULT_PHASE_C_RUNS,
                    help="Phase C 隔离产物根目录（audit.jsonl / handoff.json 只允许写这里）")
    ap.add_argument("--candidate-ledger", default=None,
                    help="候选补丁账本路径（默认 rules/candidate_patch.yaml；Phase C 演示可指到"
                         "隔离账本，避免推进 A1 的规范账本）")
    ns = ap.parse_args(argv)
    if ns.max_generations < 1:
        ap.error("--max-generations 必须 >= 1")
    if ns.variants_per_gen < 1:
        ap.error("--variants-per-gen 必须 >= 1")
    if ns.patience < 1:
        ap.error("--patience 必须 >= 1")
    if ns.handoff_to and not ns.enable_meta_controller:
        ap.error("--handoff-to 需要同时 --enable-meta-controller（没有元控制器就没有触发与交接）")
    return ns


def run(argv: list[str] | None = None, *,
        red_team=None, blue_team=None,
        regression_runner=None, token_meter=None,
        defense_mod=None, ledger_path: str | None = None) -> dict:
    """执行演化剧本，返回结果字典（CLI 只看 exit_code）。

    依赖注入只用于测试：默认从磁盘导入 A4/A1、跑真实回归锁、token 恒 0。
    `ledger_path` 仅在测试时注入（默认 = 规范账本 rules/candidate_patch.yaml，
    Lead 裁决：官方 run 必须写规范账本，同时 run 目录留每代快照）。

    Phase C（契约 §5.1）：
    - `--enable-meta-controller` 开启元控制器；**关闭（默认）时本函数逐字复现 Phase B 行为**，
      不新增字段、不写 audit、不改停止条件（可复现性证据由 tests/test_phase_c_meta.py 逐代对比）；
    - `--handoff-to phase-c`：触发 Phase C 交接后写 handoff.json（与 audit 同写隔离目录）；
    - `--candidate-ledger`：把候选账本指到隔离路径（Phase C 演示用，避免推进 A1 规范账本）；
    - 元控制器开启且未显式改 `--out` → run 目录默认落 experiments/phase-c/runs（隔离目录）。
    """
    args = _parse_args(argv)
    defense_mod = defense_mod or defense
    regression_runner = regression_runner or default_regression_runner
    token_meter = token_meter or (lambda: 0)
    canonical_ledger = (os.path.abspath(ledger_path) if ledger_path
                        else os.path.abspath(args.candidate_ledger) if args.candidate_ledger
                        else os.path.join(ROOT, DEFAULT_LEDGER_REL))

    if red_team is None or blue_team is None:
        red, blue = load_teammate_modules()
        red_team = red_team or red
        blue_team = blue_team or blue

    # Phase C 隔离：元控制器开启时默认把 run 目录放进隔离目录（audit/handoff 与 log 同处一地）。
    meta_enabled = bool(args.enable_meta_controller)
    out_arg = args.out
    if meta_enabled and out_arg == DEFAULT_OUT:
        out_arg = DEFAULT_PHASE_C_RUNS
        print("[A5][META] 元控制器开启且未显式指定 --out → run 目录改用隔离目录 %s"
              % DEFAULT_PHASE_C_RUNS)
    out_base = out_arg if os.path.isabs(out_arg) else os.path.join(ROOT, out_arg)
    os.makedirs(out_base, exist_ok=True)
    dry_run = bool(args.dry_run)
    max_generations = 1 if dry_run else args.max_generations
    run_id = "run-" + datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = _unique_run_dir(out_base, run_id)
    os.makedirs(run_dir, exist_ok=True)
    log_path = os.path.join(run_dir, "coevolution_log.jsonl")
    evidence_path = os.path.join(run_dir, "coevolution_evidence.json")
    snapshot_path = os.path.join(run_dir, "evolved_patch_snapshot.json")
    started_at = _now_iso()
    print("[A5] run_id=%s dir=%s max_generations=%d patience=%d variants/gen=%d seed=%d%s"
          % (os.path.basename(run_dir), _rel(run_dir), max_generations, args.patience,
             args.variants_per_gen, args.seed, " dry-run" if dry_run else ""))

    result = {
        "exit_code": 0,
        "run_id": os.path.basename(run_dir),
        "run_dir": run_dir,
        "log_path": log_path,
        "evidence_path": None if dry_run else evidence_path,
        "generations_run": 0,
        "stop_reason": None,
        "log_rows": [],
        "error": None,
    }

    # 语料（Lead 预生成）
    try:
        train_seeds = defense_mod.load_seed_attacks("train")
        heldout_seeds = defense_mod.load_seed_attacks("heldout")
        benign = defense_mod.load_benign_corpus()
    except Exception as exc:
        print("[FAIL] 语料加载失败：%s" % exc)
        result.update(exit_code=1, error="corpora: %s" % exc)
        return result
    if not train_seeds or not heldout_seeds or not benign:
        print("[FAIL] 语料为空（train=%d heldout=%d benign=%d）"
              % (len(train_seeds), len(heldout_seeds), len(benign)))
        result.update(exit_code=1, error="empty corpora")
        return result

    # 固定零日集：A4 一次生成、落盘、之后只读（契约 §4.2）
    zeroday_path = os.path.join(run_dir, "zeroday_set.jsonl")
    zeroday_note = "heldout seeds 经 A4 变异后落盘，蓝队永远看不到 payload；run 内只读"
    try:
        built, zeroday_generation_id = _call_build_zeroday(
            red_team, heldout_seeds, seed=args.seed, out_path=zeroday_path)
        if not os.path.exists(zeroday_path):
            if isinstance(built, list) and built:
                _write_jsonl(zeroday_path, built)
                zeroday_note += "（A4 未落盘，由 A5 按返回列表补写）"
            else:
                raise RuntimeError("build_zeroday_set 未落盘且无返回列表")
        zeroday = _read_jsonl(zeroday_path)
        if not zeroday:
            raise RuntimeError("零日集为空：%s" % zeroday_path)
        zeroday_items = _to_eval_items(zeroday)
    except Exception as exc:
        print("[FAIL] generation 启动前 step 0/6 build_zeroday_set 失败：%s" % exc)
        traceback.print_exc()
        result.update(exit_code=1, error="zeroday: %s" % exc)
        return result
    zeroday_sha = _sha256_file(zeroday_path)
    print("[A5] 零日集口径：generation_id=%s（0=与训练流同变异强度，只差 held-out 种子）"
          % ("0" if zeroday_generation_id == ZERODAY_GENERATION_ID else zeroday_generation_id))

    # 基线度量（整轮只算一次）
    zeroday_baseline = defense_mod.evaluate(zeroday_items, None)
    benign_baseline = defense_mod.evaluate(benign, None)
    all_seed_items = _to_eval_items(defense_mod.load_seed_attacks())
    plain_attack_baseline = defense_mod.evaluate(all_seed_items, None)
    print("[A5] 零日集 n=%d baseline拦截=%.4f；良性基线误伤=%.4f（%d/%d）；"
          "明文攻击基线拦截=%.4f"
          % (zeroday_baseline["n"], zeroday_baseline["rate"], benign_baseline["rate"],
             benign_baseline["intercepted"], benign_baseline["n"], plain_attack_baseline["rate"]))

    # ── Phase C 元控制器（契约 §5.1）──────────────────────
    meta: MetaController | None = None
    phase_c_runs_base = (args.phase_c_runs if os.path.isabs(args.phase_c_runs)
                         else os.path.join(ROOT, args.phase_c_runs))
    if meta_enabled:
        audit_dir = os.path.join(phase_c_runs_base, os.path.basename(run_dir))
        # 先过隔离闸门，再构造控制器：宁可在跑之前失败，也不许写错地方。
        assert_isolated_audit_path(os.path.join(audit_dir, "audit.jsonl"), phase_c_runs_base)
        meta = MetaController(enabled=True, audit_dir=audit_dir,
                              audit_root=phase_c_runs_base,
                              threshold=META_ZERO_THRESHOLD)
        print("[A5][META] 元控制器开启：判据=连续 %d 代 marginal_coverage==0.0；"
              "audit 只写隔离目录 %s"
              % (META_ZERO_THRESHOLD, _rel(os.path.join(audit_dir, "audit.jsonl"))))

    rng = random.Random(args.seed)
    evolved_patch: dict = {"version": 0, "patches": []}
    # Lead 裁决（2026-10-28）：官方 run 的每代 propose_patch 必须写规范账本
    # rules/candidate_patch.yaml（H0 人工审批的唯一入口），同时每代在 run 目录留一份
    # candidate_patch_snapshot_g<g>.yaml 快照（单 run 可复现、可回溯）。
    if not os.path.exists(canonical_ledger):
        print("[WARN] 候选账本不存在：%s（下一步 A1 会新建；正式长跑前请由 Lead 重置为空账本）"
              % _rel(canonical_ledger))
    print("[A5] 候选补丁账本：%s%s"
          % (_rel(canonical_ledger),
             "（隔离路径，不推进 A1 规范账本）" if args.candidate_ledger else ""))
    candidate_ledger = canonical_ledger
    ledger_snapshots: list[str] = []
    accepted_ids: list[str] = []
    rejected_patches: list[dict] = []
    regression_runs: list[dict] = []
    log_rows: list[dict] = []
    variants_total = 0
    bypass_total = 0
    consecutive_zero_bypass = 0
    api_calls = 0
    api_tokens_used = int(token_meter() or 0)
    stop_reason: str | None = None
    # 元控制器观测的参照点：上一代演化防线在零日集上的拦截条数（首代为基线）。
    prev_zeroday_intercepted = zeroday_baseline["intercepted"]
    handoff_path: str | None = None
    step = "1/6 generate"

    for generation_id in range(max_generations):
        try:
            # ── 1) 红队生成（只用 train 种子）────────────────
            step = "1/6 generate"
            variants = red_team.generate(generation_id, train_seeds,
                                         n=args.variants_per_gen, rng=rng)
            if not isinstance(variants, list):
                raise RuntimeError("generate 返回类型不是 list：%r" % type(variants))
            variants = [dict(v) for v in variants]
            variants_total += len(variants)

            # ── 2) 判定（唯一因变量：phase_b_defense.scan）────
            step = "2/6 save_results(judge)"
            red_results_path = os.path.join(run_dir, "red_results_gen%d.jsonl" % generation_id)
            judged = red_team.save_results(variants, evolved_patch, red_results_path)
            if judged is None:
                judged = _read_jsonl(red_results_path)
            records = list(judged or [])
            if len(records) != len(variants) and variants:
                raise RuntimeError("save_results 返回 %d 条，与 variants %d 不一致"
                                   % (len(records), len(variants)))
            for rec in records:
                if "bypass" not in rec:
                    rec["bypass"] = not bool(rec.get("intercepted"))
            bypasses = [r for r in records if r.get("bypass") is True]
            bypass_count = len(bypasses)
            bypass_total += bypass_count
            if records and all("intercepted" in r for r in records):
                attacks_intercepted_count = sum(1 for r in records if r.get("intercepted"))
            else:
                attacks_intercepted_count = defense_mod.evaluate(
                    _to_eval_items(variants), evolved_patch)["intercepted"]

            # ── 3) 打补丁（A1 自检良性误伤；None = 无可打补丁）──
            step = "3/6 propose_patch"
            proposal = _call_propose_patch(blue_team, bypasses, evolved_patch,
                                           generation_id, candidate_ledger)
            patch_item = None
            candidate_patch = None
            if proposal is None:
                # A1 可能已拒绝了一个补丁（如良性自检不过），把账本里的决定如实记录
                _record_adjudication(blue_team, rejected_patches, generation_id)
            if proposal is not None:
                if not isinstance(proposal, dict):
                    raise RuntimeError("propose_patch 返回类型不是 dict/None：%r" % type(proposal))
                if isinstance(proposal.get("patches"), list):
                    # 防御式兼容：A1 若返回整包 patch，取其中一条作为本代补丁
                    items = proposal["patches"]
                    patch_item = items[-1] if items else None
                    candidate_patch = copy.deepcopy(proposal) if items else None
                else:
                    patch_item = proposal
                    candidate_patch = {
                        "version": int(evolved_patch.get("version") or 0) + 1,
                        "patches": copy.deepcopy(evolved_patch.get("patches") or [])
                        + [copy.deepcopy(patch_item)],
                    }
            blue_team_patch_generated = 1 if patch_item is not None else 0
            accepted_patch_id: str | None = None
            if patch_item is not None and str(patch_item.get("status")) in ("rejected", "disabled"):
                # A1 自己把补丁标成 rejected：绝不进入演化防线
                _record_rejection(rejected_patches, {
                    "id": str(patch_item.get("id")), "reason": "blue_team_rejected",
                    "generation_id": generation_id,
                    "evidence": patch_item.get("rationale") or patch_item.get("status"),
                })
                candidate_patch = None

            # ── 4) 回归锁：只有 0 failed 才允许补丁进演化防线 ──
            regression_test_pass = None
            regression_info = None
            if candidate_patch is not None:
                step = "4/6 regression_lock"
                reg = regression_runner(run_dir, generation_id)
                regression_test_pass = bool(reg.get("ok"))
                regression_info = {
                    "suite": reg.get("suite") or REGRESSION_SUITE,
                    "files": reg.get("files"),
                    "passed": reg.get("passed"),
                    "failed": reg.get("failed"),
                    "seconds": reg.get("seconds"),
                }
                regression_runs.append(dict(regression_info, generation_id=generation_id))
                print("[gen %d] 回归锁 suite=%s files=%s passed=%s failed=%s → %s"
                      % (generation_id, regression_info["suite"], regression_info["files"],
                         regression_info["passed"], regression_info["failed"],
                         "PASS" if regression_test_pass else "FAIL（丢弃补丁）"))
                if not regression_test_pass:
                    _record_rejection(rejected_patches, {
                        "id": str(patch_item.get("id")),
                        "reason": "regression",
                        "generation_id": generation_id,
                        "evidence": {"suite": REGRESSION_SUITE,
                                     "passed": regression_info["passed"],
                                     "failed": regression_info["failed"],
                                     "json": reg.get("json_path")},
                    })
                    _call_update_status(blue_team, patch_item.get("id"), "rejected",
                                        candidate_ledger, "regression_lock_failed",
                                        {"suite": REGRESSION_SUITE,
                                         "passed": regression_info["passed"],
                                         "failed": regression_info["failed"]})
                    candidate_patch = None

            # ── 5) 双防线评估（基线 vs 演化）───────────────
            step = "5/6 evaluate"
            if candidate_patch is not None:
                try_zeroday = defense_mod.evaluate(zeroday_items, candidate_patch)
                try_benign = defense_mod.evaluate(benign, candidate_patch)
                baseline_fp = benign_baseline["intercepted"] / benign_baseline["n"]
                try_fp = try_benign["intercepted"] / try_benign["n"] if try_benign["n"] else 0.0
                if try_fp > baseline_fp + 1e-12:
                    # 红线 §0.3：良性误伤率不得高于基线 → 丢弃补丁
                    first_new_fp = None
                    base_ids = {r["id"] for r in benign_baseline["per_item"] if r["intercepted"]}
                    for item in try_benign["per_item"]:
                        if item["intercepted"] and item["id"] not in base_ids:
                            first_new_fp = {"id": item["id"], "matched": item.get("matched")}
                            break
                    _record_rejection(rejected_patches, {
                        "id": str(patch_item.get("id")),
                        "reason": "benign_fp",
                        "generation_id": generation_id,
                        "evidence": {"baseline_fp": _round4(baseline_fp),
                                     "patch_fp": _round4(try_fp),
                                     "new_false_positive": first_new_fp},
                    })
                    _call_update_status(blue_team, patch_item.get("id"), "rejected",
                                        candidate_ledger, "benign_fp",
                                        {"baseline_fp": _round4(baseline_fp),
                                         "patch_fp": _round4(try_fp),
                                         "new_false_positive": first_new_fp})
                    print("[gen %d] 良性误伤 %.4f > 基线 %.4f → 丢弃补丁 %s"
                          % (generation_id, try_fp, baseline_fp, patch_item.get("id")))
                    candidate_patch = None
                else:
                    evolved_patch = candidate_patch
                    accepted_ids.append(str(patch_item.get("id")))
                    accepted_patch_id = str(patch_item.get("id"))
                    _call_update_status(blue_team, patch_item.get("id"), "accepted_sandbox",
                                        candidate_ledger, "regression_lock_pass_benign_no_new_fp",
                                        {"regression": regression_info,
                                         "benign_fp": _round4(try_fp)})
                    print("[gen %d] 补丁 %s 通过回归锁与良性自检 → 进入演化防线"
                          % (generation_id, patch_item.get("id")))
            zeroday_evolved = defense_mod.evaluate(zeroday_items, evolved_patch)
            benign_evolved = defense_mod.evaluate(benign, evolved_patch)
            benign_total = benign_evolved["n"]
            benign_fp_count = benign_evolved["intercepted"]
            benign_fp_rate = benign_fp_count / benign_total if benign_total else 0.0

            # ── 6) 落盘 + 判停 ────────────────────────────
            step = "6/6 log_and_stop"
            consecutive_zero_bypass = consecutive_zero_bypass + 1 if bypass_count == 0 else 0
            generations_run = generation_id + 1
            api_tokens_used = int(token_meter() or 0)
            # 元控制器观测：参照上一代演化防线，看本代（真正进防线的）补丁带来多少 held-out 增量。
            meta_obs = None
            consecutive_zero_marginal = 0
            if meta is not None:
                meta_obs = meta.observe(
                    {
                        "generation_id": generation_id,
                        "patch_id": accepted_patch_id,
                        "zeroday_total": zeroday_baseline["n"],
                        "heldout_intercepted_prev": prev_zeroday_intercepted,
                        "heldout_intercepted": zeroday_evolved["intercepted"],
                    },
                    run_id=os.path.basename(run_dir),
                    patches_total=len(evolved_patch.get("patches") or []),
                    heldout_rate=zeroday_evolved["rate"],
                    zeroday_total=zeroday_baseline["n"],
                )["observation"]
                consecutive_zero_marginal = meta.consecutive_zero
            stop_reason = check_stop_conditions(
                generations_run=generations_run,
                max_generations=max_generations,
                consecutive_zero_bypass=consecutive_zero_bypass,
                patience=args.patience,
                api_tokens_used=api_tokens_used,
                token_budget=args.token_budget,
                consecutive_zero_marginal=consecutive_zero_marginal,
                meta_zero_threshold=META_ZERO_THRESHOLD if meta is not None else 0,
            )
            # 每代账本快照（Lead 裁决：run 目录留 candidate_patch_snapshot_g<g>.yaml）
            ledger_snapshot = None
            if os.path.exists(candidate_ledger):
                snap_name = "candidate_patch_snapshot_g%d.yaml" % generation_id
                snap_path = os.path.join(run_dir, snap_name)
                try:
                    with io.open(candidate_ledger, encoding="utf-8") as fh:
                        content = fh.read()
                    with io.open(snap_path, "w", encoding="utf-8", newline="\n") as fh:
                        fh.write(content)
                    ledger_snapshot = snap_name
                    ledger_snapshots.append(snap_name)
                except Exception as exc:  # noqa: BLE001
                    print("[WARN] 账本快照写入失败 gen%d：%s" % (generation_id, exc))
            row = {
                "generation_id": generation_id,
                "red_team_variants_count": len(variants),
                "bypass_success_count": bypass_count,
                "blue_team_patch_generated": blue_team_patch_generated,
                "regression_test_pass": regression_test_pass,
                "benign_false_positive_rate": _round4(benign_fp_rate),
                "attacks_intercepted_count": int(attacks_intercepted_count),
                "intercept_rate_evolved": _round4(
                    attacks_intercepted_count / len(variants) if variants else 0.0),
                "intercept_rate_baseline_zeroday": _round4(zeroday_baseline["rate"]),
                "intercept_rate_evolved_zeroday": _round4(zeroday_evolved["rate"]),
                "benign_total": benign_total,
                "benign_false_positive_count": benign_fp_count,
                "benign_false_positive_rate_baseline": _round4(
                    benign_baseline["intercepted"] / benign_baseline["n"]),
                "zeroday_total": zeroday_baseline["n"],
                "zeroday_generation_id": zeroday_generation_id,
                "consecutive_zero_bypass": consecutive_zero_bypass,
                "patches_total": len(evolved_patch.get("patches") or []),
                "patch_ids_accepted": list(accepted_ids),
                "patch_rejected": copy.deepcopy(rejected_patches),
                "candidate_ledger": os.path.basename(candidate_ledger),
                "candidate_ledger_snapshot": ledger_snapshot,
                "regression": regression_info,
                "strategy_mix": _mix_counts(variants, "strategy"),
                "technique_mix": _mix_counts(variants, "technique", split_combined=True),
                "api_calls": api_calls,
                "api_tokens_used": api_tokens_used,
                "stop_reason": stop_reason,
                "ts": _now_iso(),
            }
            if meta_obs is not None:
                # 只有元控制器开启时才新增字段；关闭时 log 行与官方 run 逐字一致（可复现性）。
                row.update({
                    "meta_controller_enabled": True,
                    "marginal_coverage": meta_obs["marginal_coverage"],
                    "heldout_gain_count": meta_obs["heldout_gain_count"],
                    "heldout_gain_rate": meta_obs["heldout_gain_rate"],
                    "consecutive_zero_marginal": consecutive_zero_marginal,
                })
            missing = [f for f in CORE_FIELDS if f not in row]
            if missing:
                raise RuntimeError("log 行缺核心字段：%s" % missing)
            with io.open(log_path, "a", encoding="utf-8", newline="\n") as fh:
                fh.write(json.dumps(row, ensure_ascii=False) + "\n")
            log_rows.append(row)
            result["generations_run"] = generations_run
            result["stop_reason"] = stop_reason
            meta_info = ""
            if meta_obs is not None:
                meta_info = " marginal=%.4f zero=%d" % (
                    meta_obs["marginal_coverage"], consecutive_zero_marginal)
            print("[gen %d] variants=%d bypass=%d patch_gen=%d regress=%s "
                  "拦截(明文)=%.4f 零日基线=%.4f 零日演化=%.4f 误伤=%.4f (%.4f)%s stop=%s"
                  % (generation_id, len(variants), bypass_count, blue_team_patch_generated,
                     "null" if regression_test_pass is None else regression_test_pass,
                     row["intercept_rate_evolved"], row["intercept_rate_baseline_zeroday"],
                     row["intercept_rate_evolved_zeroday"], row["benign_false_positive_rate"],
                     row["benign_false_positive_rate_baseline"], meta_info, stop_reason))
            prev_zeroday_intercepted = zeroday_evolved["intercepted"]
            if stop_reason:
                break
        except Exception as exc:
            print("[FAIL] generation %d step %s 异常：%s" % (generation_id, step, exc))
            traceback.print_exc()
            result.update(exit_code=1, error="generation %d step %s: %s"
                          % (generation_id, step, exc),
                          generations_run=len(log_rows),
                          stop_reason=None, log_rows=log_rows)
            return result

    # 演化防线快照（A5 写作用域内；不写 A1 的 candidate_patch.yaml）
    try:
        with io.open(snapshot_path, "w", encoding="utf-8") as fh:
            json.dump(evolved_patch, fh, ensure_ascii=False, indent=2)
    except Exception as exc:
        print("[WARN] 演化防线快照写入失败：%s" % exc)

    # ── Phase C 交接（契约 §5.1）：触发才写，且只写隔离目录 ──
    if meta is not None and meta.triggered:
        print("[A5][META] 连续 %d 代 marginal_coverage==0.0 → 触发 Phase C 交接："
              "stop_reason=trigger_phase_c（已停止补丁生成）"
              % meta.consecutive_zero)
        print("[A5][META] Trigger_Phase_C 事件 → %s" % _rel(meta.audit_path))
        print("[A5][META] 事件原文：%s"
              % json.dumps(meta.trigger_event, ensure_ascii=False))
        if args.handoff_to == "phase-c":
            handoff = build_handoff(
                run_id=os.path.basename(run_dir), run_dir=run_dir, log_rows=log_rows,
                trigger_event=meta.trigger_event,
                marginals=[dict(o) for o in meta.history if o.get("consecutive_zero") is not None],
                log_path=log_path, zeroday_path=zeroday_path, zeroday_sha=zeroday_sha,
                zeroday_count=zeroday_baseline["n"],
                zeroday_generation_id=zeroday_generation_id,
                patches_total=len(evolved_patch.get("patches") or []),
                snapshot_path=snapshot_path)
            handoff_path = os.path.join(os.path.dirname(meta.audit_path), "handoff.json")
            write_handoff(handoff_path, handoff, audit_root=meta.audit_root)
            print("[A5][META] 交接包 handoff.json → %s" % _rel(handoff_path))
            print("[A5][META] 交接：from=phase-b to=phase-c reason=%s evidence(trigger_generation=%s, "
                  "heldout_rate=%s, patches_total=%s)"
                  % (handoff["reason"], handoff["evidence"]["trigger_generation"],
                     handoff["evidence"]["heldout_rate_at_trigger"],
                     handoff["phase_b_run"]["patches_total"]))
    elif meta is not None and args.handoff_to == "phase-c":
        print("[A5][META] 未触发 Phase C（停止原因=%s）→ 不写 handoff.json" % stop_reason)

    result.update(log_rows=log_rows, generations_run=len(log_rows),
                  stop_reason=stop_reason,
                  run_dir=run_dir, log_path=log_path,
                  evidence_path=None if dry_run else evidence_path)
    result["evolved_patch"] = evolved_patch
    result["patch_ids_accepted"] = accepted_ids
    result["patch_rejected"] = rejected_patches
    result["regression_runs"] = regression_runs
    result["meta_controller"] = meta.summary() if meta is not None else {"enabled": False}
    result["audit_path"] = meta.audit_path if meta is not None else None
    result["handoff_path"] = handoff_path
    result["zeroday_path"] = zeroday_path
    result["zeroday_sha256"] = zeroday_sha

    # dry-run：只跑 1 代、不写 evidence（契约 §6.5）
    if not dry_run:
        evidence = build_evidence(
            run_id=os.path.basename(run_dir),
            run_dir=run_dir,
            started_at=started_at,
            log_rows=log_rows,
            stop_reason=stop_reason,
            seed=args.seed,
            zeroday_path=zeroday_path,
            zeroday_sha=zeroday_sha,
            zeroday_count=zeroday_baseline["n"],
            zeroday_generation_id=zeroday_generation_id,
            zeroday_baseline_rate=zeroday_baseline["rate"],
            plain_attack_baseline_rate=plain_attack_baseline["rate"],
            benign_baseline=benign_baseline,
            benign_total=benign_baseline["n"],
            variants_total=variants_total,
            bypass_total=bypass_total,
            accepted_ids=accepted_ids,
            rejected_patches=rejected_patches,
            regression_runs=regression_runs,
            candidate_ledger=candidate_ledger,
            ledger_snapshots=ledger_snapshots,
            api_calls=api_calls,
            api_tokens_used=api_tokens_used,
            snapshot_path=snapshot_path,
            dry_run=dry_run,
            meta_controller=(meta.summary() if meta is not None else None),
        )
        with io.open(evidence_path, "w", encoding="utf-8") as fh:
            json.dump(evidence, fh, ensure_ascii=False, indent=2)
        print("[A5] evidence → %s" % _rel(evidence_path))
        print("[A5] 完成：generations_run=%d stop_reason=%s patches=%s"
              % (len(log_rows), stop_reason, accepted_ids))
        print("[A5] 诚实声明：%s" % LIMITATIONS[2])
    else:
        print("[A5] dry-run 完成：generations_run=%d stop_reason=%s（未写 evidence）"
              % (len(log_rows), stop_reason))
    result["exit_code"] = 0
    return result


def build_evidence(*, run_id: str, run_dir: str, started_at: str, log_rows: list[dict],
                   stop_reason: str | None, seed: int, zeroday_path: str, zeroday_sha: str,
                   zeroday_count: int, zeroday_generation_id: int | None,
                   zeroday_baseline_rate: float,
                   plain_attack_baseline_rate: float, benign_baseline: dict,
                   benign_total: int, variants_total: int, bypass_total: int,
                   accepted_ids: list[str], rejected_patches: list[dict],
                   regression_runs: list[dict], candidate_ledger: str,
                   api_calls: int, api_tokens_used: int,
                   snapshot_path: str, dry_run: bool,
                   ledger_snapshots: list[str] | None = None,
                   meta_controller: dict | None = None) -> dict:
    """契约 §6.4 的 coevolution_evidence.json。

    `meta_controller` 仅在元控制器开启（--enable-meta-controller）时传入并落盘；
    关闭时不写该键 → evidence 也保持 Phase B 官方 run 的结构（可复现性）。
    """
    rows = log_rows
    last = rows[-1] if rows else {}
    zero_rows = [r for r in rows if r.get("intercept_rate_evolved") is not None] or rows
    max_row = max(zero_rows, key=lambda r: r.get("intercept_rate_evolved", -1.0)) if zero_rows else {}
    regression_all_pass = bool(regression_runs) and all(
        (r.get("failed") == 0 and r.get("passed") is not None) for r in regression_runs)
    if not regression_runs:
        # 整轮没有补丁 → 没有需要验证的回归，记为 vacuously true 会误导，这里记 null 并在图注说明
        regression_all_pass = None
    ledger_rel = _rel(candidate_ledger) if os.path.exists(candidate_ledger) else None
    return {
        "schema": "phase-b-coevolution-evidence-v1",
        "run_id": run_id,
        "started_at": started_at,
        "finished_at": _now_iso(),
        "generations_run": len(rows),
        "stop_reason": stop_reason,
        "converged": stop_reason == "converged",
        "dry_run": dry_run,
        "defense_under_test": {
            "baseline": "phase-a rule gate (classify_user_risk + preflight_audit UCA-01)",
            "evolved": "baseline + this run's accepted sandbox patches (snapshot: %s)"
                       % _rel(snapshot_path),
            "candidate_ledger": ledger_rel,
            "measurement": "tools/phase_b_defense.scan",
            "note": "官方 run 的候选补丁按 Lead 裁决写入规范账本 rules/candidate_patch.yaml（A1 "
                    "persist），run 目录另存 candidate_patch_snapshot_g<g>.yaml 每代快照；"
                    "生产规则零改动；合入生产 = H0 人工审批，脚本无此能力",
        },
        "candidate_ledger": ledger_rel,
        "candidate_ledger_snapshots": list(ledger_snapshots or []),
        "evolved_patch_snapshot": _rel(snapshot_path),
        "zeroday_set": {
            "path": _rel(zeroday_path),
            "count": zeroday_count,
            "sha256": zeroday_sha,
            "generation_id": zeroday_generation_id,
            "note": "held-out seeds 经 A4 确定性变异；蓝队永远拿不到 payload；run 内只读",
        },
        "zeroday_protocol": (
            "held-out seeds x generation-0 mutation distribution; blue team never sees "
            "these payloads; held-out mutations are NOT OOD data"),
        "zeroday_generation_id": zeroday_generation_id,
        "plain_attack_baseline_intercept_rate": _round4(plain_attack_baseline_rate),
        "zeroday_baseline_intercept_rate": _round4(zeroday_baseline_rate),
        "zeroday_evolved_intercept_rate_final": _round4(
            last.get("intercept_rate_evolved_zeroday", 0.0)),
        "max_intercept_rate_evolved": _round4(max_row.get("intercept_rate_evolved", 0.0)),
        "max_intercept_rate_evolved_generation": max_row.get("generation_id"),
        "benign_false_positive_rate_max": _round4(max(
            (r.get("benign_false_positive_rate", 0.0) for r in rows), default=0.0)),
        "benign_false_positive_rate_baseline": _round4(
            benign_baseline["intercepted"] / benign_baseline["n"]),
        "benign_total": benign_total,
        "red_team_variants_total": variants_total,
        "bypass_total": bypass_total,
        "patches_total": len(accepted_ids),
        "patches": list(accepted_ids),
        "patch_rejected": copy.deepcopy(rejected_patches),
        "regression_all_pass": regression_all_pass,
        "regression_runs": copy.deepcopy(regression_runs),
        "api_calls": api_calls,
        "api_tokens_used": api_tokens_used,
        "determinism": {"seed": seed, "rng": "random.Random",
                        "zeroday_seed": seed,
                        "note": "变异流/绕过序列/拦截率同 seed 可复现；补丁 id 由规范账本状态"
                                "决定（官方 run 写唯一规范账本），复跑 id 顺延；单 run 内每代"
                                "候选账本快照保证可回溯"},
        "log_path": "coevolution_log.jsonl",
        # 元控制器关闭时不写该键（保持 Phase B 官方 run 的 evidence 结构）
        **({"meta_controller": meta_controller} if meta_controller is not None else {}),
        "limitations": list(LIMITATIONS) + (
            list(META_LIMITATIONS) if meta_controller is not None else []) + [
            "本 run 的逐代原始数值见 %s（%d 行）；回归锁明细见 regression_gen*.json。"
            % (_rel(os.path.join(run_dir, "coevolution_log.jsonl")), len(rows)),
        ],
    }


def main(argv: list[str] | None = None, **kwargs) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    try:
        result = run(argv, **kwargs)
    except RuntimeError as exc:
        print("[FAIL] %s" % exc)
        return 1
    except Exception as exc:  # noqa: BLE001
        print("[FAIL] 未捕获异常：%s" % exc)
        traceback.print_exc()
        return 1
    return int(result.get("exit_code", 1))


if __name__ == "__main__":
    raise SystemExit(main())
