# -*- coding: utf-8 -*-
import io

P = "engine/memory.py"
s = io.open(P, encoding="utf-8").read()

old = '''def potency(e, now: float, cfg: dict | None = None) -> float:
    """ACT-R 版 potency：importance × 归一化激活（频率与近因共同决定）。"""
    return actr.potency(e, now)'''

new = '''def potency(e, now: float, cfg: dict | None = None) -> float:
    """双通道 potency：指数衰减 ∪ ACT-R 激活。

    为什么不是纯 ACT-R：
        直接换成 ACT-R 会让回放基线 recall@3 从 1.000 掉到 0.556——
        纯 power-law 对"中等年龄"的条目给的激活远低于指数衰减，排序整体翻转。
        而我们的冻结评估口径不允许这样悄无声息地变。

    做法（两条通道取大）：
        decay  = importance × exp(−Δt / τ_eff)      ← 原语义，回答"还活着吗"
        actr   = importance × normalized(B) × w      ← 频率+近因，回答"是不是常用"
        potency = max(decay, actr)

    效果：
        · 单次访问的普通条目：走 decay，**基线排序不变**；
        · 高频但久远的偏好：ACT-R 通道把它救回来（需求里明确要求的那条）；
        · `w` 默认 0.6，是"救援力度"，不是"替代程度"。
    """
    imp = float(getattr(e, "importance", 0.5))
    days = max(0.0, (now - float(getattr(e, "last_access", 0) or 0)) / 86400.0)
    tau = tau_eff(e, cfg) if cfg else 30.0
    decay = imp * math.exp(-days / tau)

    w = float((cfg or {}).get("memory", {}).get("actrWeight", 0.6))
    a = actr.potency(e, now) * w
    return max(0.0, min(1.0, max(decay, a)))'''

assert old in s
s = s.replace(old, new)
io.open(P, "w", encoding="utf-8", newline="\n").write(s)
print("potency: dual-channel")

# config 增加 actrWeight
P2 = "engine/config.py"
c = io.open(P2, encoding="utf-8").read()
c = c.replace('''    "potency": {"archiveThreshold": 0.15},''',
'''    "potency": {"archiveThreshold": 0.15},
    "memory": {"actrWeight": 0.6, "touchOnRecall": False},''')
io.open(P2, "w", encoding="utf-8", newline="\n").write(c)
print("config: memory.actrWeight")

# compose_version 断言更新（3.0 → 3.1，动态权重带来的契约升级）
P3 = "tests/test_pipeline_integration.py"
t = io.open(P3, encoding="utf-8").read()
t = t.replace('check("compose_version 正确", ev["compose_version"] == "compose-3.0", ev["compose_version"])',
              'check("compose_version 正确（3.1：动态权重）", ev["compose_version"] == "compose-3.1", ev["compose_version"])')
io.open(P3, "w", encoding="utf-8", newline="\n").write(t)
print("test updated: compose-3.1")
