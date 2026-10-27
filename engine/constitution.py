# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 宪法层审计器（docs/01 §3–§4）

为什么要有"可执行版"：
    宪法层原文是**提示词**，靠模型自觉。但 `docs/00 §4` 的强制点已经改成
    "插件内自校验"——那就必须有一段**代码**能对提议做出判定，哪怕只是规则桩。
    规则桩的价值不是准确率，而是：**它能把"明显越界"变成确定性拒绝，且可测试**。

判定口径：
- 命中任一 `A1–A10` → reject（宪法 C6：不进入审核队列）；
- 文本为空 / 无法判定 → **reject**（fail-closed，与 docs/00 一致）；
- 只有明确无命中才 pass。

另含 `FrozenGuard`：实现 `docs/00 §4` 的"启动自校验，失败即停注入"。
"""
from __future__ import annotations

import hashlib
import os
from typing import Any, Iterable

# A1–A10 检查器：编号 → (说明, 触发词表)
CHECKS: dict[str, tuple[str, tuple[str, ...]]] = {
    "A1": ("修改片段 0 的原则列表或裁决规则",
           ("修改原则", "调整原则顺序", "重排优先级", "改原则表", "去掉原则")),
    "A2": ("修改片段 1 的使用规则",
           ("修改使用规则", "去掉规则", "删除 CONST-1", "改常驻协议")),
    "A3": ("修改附录 B 的派生条目",
           ("附录 B", "派生条目", "改投影")),
    "A4": ("放宽拒绝类别 / 降低安全优先级 / 允许情绪影响事实",
           ("放宽拒绝", "降低安全优先级", "允许情绪影响", "情感参与事实",
            "情绪决定结论", "关系软化拒绝")),
    "A5": ("引入绕过审核的自动应用路径",
           ("自动应用", "绕过审核", "跳过人工确认", "取消审核门禁",
            "自动通过", "免审核")),
    "A6": ("修改审计日志 / 灰度回滚 / 物理删除接口",
           ("修改审计日志", "关闭灰度回滚", "关闭物理删除", "取消回滚")),
    "A7": ("修改合成矩阵的域分类 / 域内排序 / 互斥表",
           ("修改互斥表", "改域分类", "调整拼装顺序", "改域内排序")),
    "A8": ("修改降级矩阵的 fail-closed 环节",
           ("关闭 fail-closed", "降低宪法层审计", "审计失败也放行", "审计降级为放行")),
    "A9": ("修改宪法层自身或本审计器",
           ("修改宪法层", "修改审计器", "关闭审计器", "停用审计")),
    "A10": ("修改评估口径 / 回放集 / 护栏阈值（学习系统不得改考卷）",
            ("修改评估口径", "改回放集", "调高护栏阈值", "降低护栏阈值",
             "改判定阈值", "删掉回放样本")),
}

# 黑名单（docs/01 §4）：可被引用、不可被修改。命中即 C6 拒绝。
BLACKLIST = (
    "宪法 C1", "宪法 C2", "宪法 C3", "宪法 C4", "宪法 C5", "宪法 C6",
    "可信度分级", "8 种边", "物理删除", "人格基线锁死", "情感检索默认关闭",
    "域分类", "互斥表", "fail-closed", "评估口径", "回放集", "护栏阈值",
    "提议生成与审核分离",
)


def _haystack(proposal: Any) -> str:
    """把提议压成一段可搜索文本（dict 会展开所有字符串值）。"""
    if isinstance(proposal, str):
        return proposal
    if isinstance(proposal, dict):
        parts: list[str] = []
        for k, v in proposal.items():
            if isinstance(v, (str, int, float)):
                parts.append("%s:%s" % (k, v))
            elif isinstance(v, (list, tuple)):
                parts.extend(str(x) for x in v)
            elif isinstance(v, dict):
                parts.append(_haystack(v))
        return " ".join(parts)
    return str(proposal)


def audit(proposal: Any, *, require_fields: Iterable[str] = ("target",)) -> dict[str, Any]:
    """对一条提议做 A1–A10 审计。返回 {verdict, violations, note, checked}。

    **fail-closed**：文本为空或缺少必需字段 → 直接 reject（不确定就不放行）。
    """
    text = _haystack(proposal).strip()
    checked = sorted(CHECKS, key=lambda c: int(c[1:]))

    if not text:
        return {"verdict": "reject", "violations": [{"code": "empty", "evidence": ""}],
                "note": "提议为空，无法判定", "checked": checked}

    if isinstance(proposal, dict):
        missing = [f for f in require_fields if not str(proposal.get(f, "")).strip()]
        if missing:
            return {"verdict": "reject",
                    "violations": [{"code": "missing_fields",
                                    "evidence": ",".join(missing)}],
                    "note": "缺少必需字段，无法判定", "checked": checked}

    violations: list[dict[str, str]] = []
    for code in checked:
        _desc, words = CHECKS[code]
        for w in words:
            if w in text:
                violations.append({"code": code, "evidence": w})
                break

    # 黑名单命中：归到最贴近的检查项（A1/A2/A7/A8/A10），并标注 C6
    for item in BLACKLIST:
        if item in text:
            code = _bl_code(item)
            if not any(v["code"] == code for v in violations):
                violations.append({"code": code, "evidence": item, "via": "C6"})
            else:
                for v in violations:
                    if v["code"] == code:
                        v["via"] = "C6"

    if violations:
        return {"verdict": "reject", "violations": violations,
                "note": "命中不可修改边界（C6：不进入审核队列）", "checked": checked}
    return {"verdict": "pass", "violations": [], "note": "未命中边界", "checked": checked}


def _bl_code(item: str) -> str:
    if item.startswith("宪法 C"):
        return "A1"
    if item in ("可信度分级", "8 种边", "物理删除"):
        return "A2"
    if item in ("人格基线锁死", "情感检索默认关闭"):
        return "A4"
    if item in ("域分类", "互斥表"):
        return "A7"
    if item == "fail-closed":
        return "A8"
    return "A10"


# ── 冻结区自校验（docs/00 §4）───────────────────────────

def sha256_file(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


class FrozenGuard:
    """`_frozen/` 的启动自校验闸门。

    用法（对应 docs/09 §4.1 的硬规则）：
        guard = FrozenGuard(root, expected_hashes)
        text = guard.guarded(text_of_section)   # 校验失败 → 返回 ""（不注入）
    """

    def __init__(self, root: str, expected: dict[str, str],
                 on_violation: str = "skip_injection") -> None:
        self.root = root
        self.expected = dict(expected)
        self.on_violation = on_violation
        self.result = self.verify()

    def verify(self) -> dict[str, Any]:
        missing, mismatched, ok = [], [], []
        for rel, want in sorted(self.expected.items()):
            p = os.path.join(self.root, rel)
            if not os.path.isfile(p):
                missing.append(rel)
                continue
            got = sha256_file(p)
            if got != want:
                mismatched.append({"path": rel, "want": want, "got": got})
            else:
                ok.append(rel)
        return {"ok": not missing and not mismatched,
                "checked": len(self.expected), "ok_files": ok,
                "missing": missing, "mismatched": mismatched}

    def guarded(self, text: str) -> str:
        """校验通过才返回正文；否则返回空串（宿主按 text.length>0 丢弃该段）。"""
        return text if self.result["ok"] else ""
