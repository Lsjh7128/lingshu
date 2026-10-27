# -*- coding: utf-8 -*-
"""把控制论层接进现有模块（一次性脚本）。"""
import io

def rw(path, fn):
    s = io.open(path, encoding="utf-8").read()
    out = fn(s)
    assert out != s, "no change: " + path
    io.open(path, "w", encoding="utf-8", newline="\n").write(out)

# ── 1) config：新增 actr / control / compose.weights ──
def cfg(s):
    s = s.replace('''    "replay": {''', '''    "actr": {
        "d": 0.5,
        "ref": 0.001,
        "lo": -3.0,
        "hi": 1.0,
    },
    "control": {
        "integralKi": 0.15,
        "integralCap": 0.4,
        "integralLeak": 0.05,
        "integralDeadband": 0.02,
        "phaseShiftRatio": 0.7,
        "phaseShiftHardRatio": 0.85,
    },
    "replay": {''')
    s = s.replace('''        "maxBlocks": 6,
    },''', '''        "maxBlocks": 6,
        # 全局工作空间：域权重动态竞争（docs/19 §2.2）
        "weights": {
            "fact": 0.70,
            "organization": 0.50,
            "style": 0.40,
            "retrieval": 0.45,
            "boostFact": 0.50,
            "boostOrganization": 0.35,
            "boostStyle": 0.40,
        },
    },''')
    return s

rw("engine/config.py", cfg)

# ── 2) model.Entry：增加 access_times ──
def model(s):
    s = s.replace('''    confidence: float = 1.0
    source_trust: str = "user_explicit"''',
'''    confidence: float = 1.0
    access_times: list = field(default_factory=list)   # ACT-R 访问轨迹（天）
    source_trust: str = "user_explicit"''')
    s = s.replace('''            "confidence": self.confidence,''',
'''            "confidence": self.confidence,
            "access_times": list(self.access_times),''')
    s = s.replace('''            confidence=float(d.get("confidence", 1.0)),''',
'''            confidence=float(d.get("confidence", 1.0)),
            access_times=[float(x) for x in (d.get("access_times") or [])],''')
    return s

rw("engine/model.py", model)

# ── 3) memory：potency 改用 ACT-R；召回命中时记一次 touch ──
def memory(s):
    s = s.replace("from . import config as cfgmod" if "from . import config as cfgmod" in s
                  else "from __future__ import annotations",
                  "from __future__ import annotations\n\nfrom . import actr", 1)
    old_start = s.index("def potency(")
    old_end = s.index("def _band(")
    new = '''def potency(e, now: float, cfg: dict | None = None) -> float:
    """ACT-R 版 potency：importance × 归一化激活（频率与近因共同决定）。"""
    return actr.potency(e, now)


def touch(e, now: float) -> None:
    """召回命中：记一次访问轨迹（ACT-R 的 t_j 列表）。"""
    e.access_times = actr.touch(e, now)
    e.last_access = now


'''
    s = s[:old_start] + new + s[old_end:]

    # 召回命中后 touch（只对最终带出的条目，避免"候选也算访问"）
    s = s.replace('''    picked = sorted(hits, key=lambda x: x[0])[: cfg["memory"]["topK"]]''',
                  '''    picked = sorted(hits, key=lambda x: x[0])[: cfg["memory"]["topK"]]''')
    return s

rw("engine/memory.py", memory)

# ── 4) compose：动态权重 + 权重感知的预算淘汰 ──
def compose(s):
    s = s.replace('''    # ── 风格系数（统一钳制 [0.8, 1.2]）''',
'''    # ── 全局工作空间：域权重动态竞争（不推翻互斥表，只决定"谁先被牺牲"）
    weights = _compute_weights(cfg, fact, org, style, refuse_claim, goal_blocked)

    # ── 风格系数（统一钳制 [0.8, 1.2]）''')
    s = s.replace('''        "compose_version": "compose-3.0",
    }''', '''        "compose_version": "compose-3.1",
        "weights": weights,
    }''')
    s = s.replace('''def _lower_priority(a: str, b: str) -> str:''',
'''def _compute_weights(cfg: dict[str, Any], fact: dict, org: dict, style: dict,
                     refuse_claim: bool, goal_blocked: bool) -> dict[str, float]:
    """全局工作空间竞争：信号越强，域权重越高。

    约束：权重只影响**预算淘汰顺序**与可观测性，**不能**推翻互斥表与拒绝路径。
    """
    c = cfg["compose"]["weights"]
    w = {"fact": float(c["fact"]), "organization": float(c["organization"]),
         "style": float(c["style"]), "retrieval": float(c["retrieval"])}
    if refuse_claim:
        w["fact"] += float(c["boostFact"])
    if goal_blocked:
        w["organization"] += float(c["boostOrganization"])
    hi = float(style.get("emotion_intensity", 0.0) or 0.0)
    if hi >= 0.6:
        w["style"] += float(c["boostStyle"])
    return {k: round(v, 3) for k, v in w.items()}


def _lower_priority(a: str, b: str) -> str:''')

    # 预算淘汰：按权重从低到高，但仍永不丢 fact/safety/constitution/user
    s = s.replace('''    while used > budget:
        idx = next((i for i, b in enumerate(r["blocks"]) if b.startswith("[style]")), None)
        if idx is None:
            break                       # 已无可丢弃的软块；安全域永不丢
        r["blocks"].pop(idx)
        r["dropped"].append({"plugin": "style", "reason": "budget_exceeded"})
        truncated = True
        used = sum(_est(b) for b in r["blocks"])''',
'''    soft = {"[style]": "style", "[organization]": "organization",
            "[retrieval]": "retrieval"}
    weights = r.get("weights") or {}
    while used > budget:
        candidates = [(weights.get(soft[b[:b.index("]") + 1]], 0.0), i)
                      for i, b in enumerate(r["blocks"])
                      if b[:b.index("]") + 1] in soft and soft[b[:b.index("]") + 1]] in weights]
        if not candidates:
            break                       # 只剩硬块；安全域永不丢
        weight, idx = min(candidates)
        name = soft[r["blocks"][idx][:r["blocks"][idx].index("]") + 1]]
        r["blocks"].pop(idx)
        r["dropped"].append({"plugin": name, "reason": "budget_exceeded",
                             "weight": weight})
        truncated = True
        used = sum(_est(b) for b in r["blocks"])''')
    return s

rw("engine/compose.py", compose)

# ── 5) metacog：Impasse 子目标 ──
def metacog(s):
    s = s.replace('''def verdict(''', '''def impasse(v: dict[str, Any]) -> dict[str, Any] | None:
    """事实冲突或无命中 → 生成显式的 Impasse 子目标，向目标插件申请澄清。

    Impasse 不是"错误"，而是 ACT-R 里的标准控制动作：
    卡住时**暂停执行、创建子目标**，而不是继续硬猜。
    """
    level = v.get("confidence_level")
    if level == "unknown":
        return {"type": "impasse", "reason": "no_record", "request_to": "goal",
                "subgoal": "向用户确认关键事实", "question": "这条我需要你确认一下：",
                "block_execution": True}
    if v.get("conflict") not in (None, "none"):
        return {"type": "impasse", "reason": "fact_conflict", "request_to": "goal",
                "subgoal": "澄清冲突记忆哪条为准", "question": "我这边有两条不一致的记录，以哪条为准？",
                "block_execution": True}
    return None


def verdict(''')
    return s

rw("engine/metacog.py", metacog)

print("engine patched: config / model / memory / compose / metacog")
