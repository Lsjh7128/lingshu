# -*- coding: utf-8 -*-
"""tools/chaos_v2/families/ —— v2 场景族 F1–F8 包（**仅 docstring**）

本包是 manifest.families[].module 的 import 落点：每个族一个模块文件
（`f1_stpa_matrix.py` … `f8_metamorphic.py`），runner 用 `importlib` **按 manifest 动态导入**，
**不做静态枚举**——这样"族清单"的唯一来源是预注册的 manifest，
而不是某个 `__init__.py` 里的写死列表（改 list 就等于改判定，docs/23 红线 6）。

族模块契约（runner 侧 `chaos_v2.invariants.normalize_family_payload` 逐项校验）：

    FAMILY_ID: str                     # 必须等于 manifest.families[].id
    INVARIANTS: tuple[str, ...]        # 必须是 docs/23 §4 里已登记的 INV-xx
    STRESS_CLASS: str                  # docs/23 §3 的压力类型闭集
    run(rows, ctx) -> dict             # 纯函数：确定性、不写盘、不调模型、不改 cfg 原件

`run` 返回：

    {"checks": {"cases": int, "expectations": int, "failed": int,
                "failures": [{"case", "want", "got", "note"}]},
     "metrics": {...}, "rates": {name: {"num", "den"}},
     "samples": [...], "notes": [...],
     "invariants": {INV-ID: {"pass": bool, "detail": str}},
     "skipped": bool, "skip_reason": str | None}

`ctx` 由 runner 构造并是**只读约定**（族不得改它，cfg 需要改动时自己 deepcopy）：

    {"seed", "repeat", "cfg", "base_ts", "repo_root", "timeout_s", "python", "run_id"}

注意：ctx **不含 temp_dir** —— 受限沙箱下 Python 不能新建目录；族模块不得写盘，
F6 的 node 子进程自己用 `os.tmpdir()` 建删临时目录。

本文件刻意保持为空包（只有 docstring）：不 import 任何族模块，避免
"manifest 未就绪时 import 全崩"或"导入顺序影响确定性"。
"""
