# -*- coding: utf-8 -*-
"""LINGSHU · 契约工程化（借鉴 engram 的生成式契约 + digest + 漂移测试）

三条规则：
1. **契约由代码生成**，不手写 —— 手写的契约必然与代码漂移。
2. **canonical 编码写死** —— 否则换个序列化器 digest 就变，校验形同虚设。
3. **-check 模式**：生成物与仓库中的不一致 → 退出码 1（这就是"漂移测试"）。
"""
from __future__ import annotations

import hashlib
import json
import os
from typing import Any

# canonical 规则（本项目的定版，改动需 ADR）：
#   1. 对象键按 UTF-8 字节序排序（Python 的 str 排序等价于按码点，对本项目足够）
#   2. 无多余空白：separators=(',',':')
#   3. 不做 ASCII 转义：ensure_ascii=False（与 engram 的 Go 规则不同，因为实现语言不同）
#   4. 数字：整数字面量保持整数；浮点统一序列化为 Python repr（调用方须先规范到 6 位）
#   5. digest 字段自身不参与哈希
CANON_RULES = {
    "key_order": "utf8_bytes",
    "separators": [",", ":"],
    "ensure_ascii": False,
    "float_precision": 6,
    "digest_excluded_fields": ["digest"],
}


def canonical(obj: dict[str, Any]) -> bytes:
    body = {k: v for k, v in obj.items() if k not in CANON_RULES["digest_excluded_fields"]}
    return json.dumps(body, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")


def digest(obj: dict[str, Any]) -> str:
    return hashlib.sha256(canonical(obj)).hexdigest()


def build(manifest: dict[str, Any], root: str) -> dict[str, Any]:
    """由 manifest + 仓库现状生成契约文档。"""
    from .replay import _param_version
    from . import config as cfgmod

    contract: dict[str, Any] = {
        "contract_id": manifest["contract_id"],
        "revision": manifest["revision"],
        "profile": manifest["profile"],
        "status": manifest["status"],
        "generated_from": manifest["generated_from"],
        "identity": manifest["identity"],
        "capabilities": manifest["capabilities"],
        "rules": _rules_digest(root),
        "contracts": _contracts_digest(root),
        "operations": manifest["operations"],
        "defaults": {
            "param_version": _param_version(cfgmod.default()),
            "config_source": "engine/config.py（与 rules/decay-params.yaml 必须一致）",
        },
        "frozen": manifest["frozen"],
        "non_claims": manifest["non_claims"],
    }
    contract["digest"] = digest(contract)
    return contract


def _rules_digest(root: str) -> dict[str, str]:
    out = {}
    d = os.path.join(root, "rules")
    for name in sorted(os.listdir(d)):
        if name.endswith(".yaml"):
            p = os.path.join(d, name)
            out[name] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


def _contracts_digest(root: str) -> dict[str, str]:
    out = {}
    d = os.path.join(root, "contracts")
    for name in sorted(os.listdir(d)):
        if name.endswith(".json") and name != "agent-contract-v1.json":
            p = os.path.join(d, name)
            out[name] = hashlib.sha256(open(p, "rb").read()).hexdigest()
    return out


def dumps(contract: dict[str, Any]) -> str:
    """仓库内表示：缩进 2、末尾一个换行（便于 review diff）。"""
    return json.dumps(contract, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def check(root: str) -> tuple[int, str]:
    """返回 (exit_code, message)。0 = 一致；1 = 漂移。"""
    manifest = json.load(open(os.path.join(root, "contracts", "_manifest.json"), encoding="utf-8"))
    fresh = dumps(build(manifest, root))
    path = os.path.join(root, "contracts", "agent-contract-v1.json")
    if not os.path.exists(path):
        return 1, "MISSING: contracts/agent-contract-v1.json"
    current = open(path, encoding="utf-8").read()
    if current != fresh:
        return 1, ("DRIFT: 仓库契约与代码/规则不一致。"
                   "运行 `python tools/gen_contract.py --write` 重新生成并 review diff。")
    return 0, "OK: contract is fresh (digest=%s)" % json.loads(current)["digest"][:16]
