# -*- coding: utf-8 -*-
"""tools/chaos_v2/invariants.py —— INV-01…INV-20 目录与判定器（docs/23 §4）

三条原则（写在这里，因为它们是整个 runner 的判定伦理）：

1. **不变量是唯一能让 campaign 失败的东西**（§6.5）。`rate` 类指标只给区间，
   `boundary` 只记落点，`recovery` 只给轮次——它们都不进退出码。
2. **缺失 = schema 违规 = 失败**（§4 末尾）。每个不变量在 JSON 里必须有
   `pass: true|false` + `detail`；族模块少报一个，聚合时按"未证明"记 false。
3. **偶发也是 bug**（§6.4）。R 次里只要有一次 false，就 `pass=false` + `flaky=true`，
   绝不取多数票洗成通过。

本模块分四块：

- `INVARIANTS`：20 条编号目录（编号冻结，新增只追加），id → 中文描述 + 判据 + 归属；
- 全局检查器：INV-01（确定性，按族）、INV-02（零模型调用）、INV-18（语料冻结）、
  INV-19（零仓库污染）、INV-20（判定不漂移，按族）；
- 族内校验工具：`normalize_family_payload`（行封套级别的返回值契约检查）、
  `aggregate_family_invariants`（§6.4 聚合）；
- `AuditProbe` / `snapshot_tree`：INV-02 与 INV-19 的证据采集（只读、不写盘）。
"""
from __future__ import annotations

import os
import shlex
import sys

from . import stats

# ── 不变量目录（编号冻结；新增只追加）──────────────────────
INVARIANTS = {
    "INV-01": {
        "id": "INV-01", "owner": "runner",
        "title": "确定性",
        "criterion": "engine 模式同 seed 两次运行，每族 digest 完全相同",
    },
    "INV-02": {
        "id": "INV-02", "owner": "runner",
        "title": "零模型调用",
        "criterion": "engine 模式无任何模型调用；仅 F6 允许一次 node 子进程（纯函数，不调模型）",
    },
    "INV-03": {
        "id": "INV-03", "owner": "F1/F7",
        "title": "闸门特异度",
        "criterion": "所有 severity=control 且 expect.ucas=[] 的用例，实际 ucas 必须为空",
    },
    "INV-04": {
        "id": "INV-04", "owner": "F1",
        "title": "闸门灵敏度",
        "criterion": "每个 UCA 的 expect.ucas 必须被恰好命中（不少不多）",
    },
    "INV-05": {
        "id": "INV-05", "owner": "F1/F3",
        "title": "UCA-08 只降级不熔断",
        "criterion": "saturated 命中时 downgrades 含 UCA-08，ucas 不含，verdict=allow",
    },
    "INV-06": {
        "id": "INV-06", "owner": "F1/F5",
        "title": "降维 fail-open 且不降宪法",
        "criterion": "conflict=unresolved → fallback=true、丢 style、verdict=allow、宪法块仍在",
    },
    "INV-07": {
        "id": "INV-07", "owner": "F5",
        "title": "预算压不垮宪法",
        "criterion": "任意预算下 blocks[0] 以 [constitution] 开头；dropped 记录原因",
    },
    "INV-08": {
        "id": "INV-08", "owner": "F4",
        "title": "记忆身份不污染",
        "criterion": "status=superseded 的条目永不出现在 recall.returned；泄漏按条目身份判",
    },
    "INV-09": {
        "id": "INV-09", "owner": "F4",
        "title": "预算诚实",
        "criterion": "装不下时 excluded_by_budget>0 且 truncated=true，id 不被截短；禁止静默返回空",
    },
    "INV-10": {
        "id": "INV-10", "owner": "F2/F3/F5",
        "title": "数值有界",
        "criterion": "0≤ratio≤ratioMax、|integral_k|≤cap、mult∈[0.8,1.2]、权重有限且非 NaN",
    },
    "INV-11": {
        "id": "INV-11", "owner": "F3",
        "title": "单调/方向正确",
        "criterion": "连续负偏离下 baseline 单调不减至 cap；正偏离/死区只泄放不增长",
    },
    "INV-12": {
        "id": "INV-12", "owner": "F3",
        "title": "恢复性",
        "criterion": "饱和后中性轮次 N 内 baseline 回落到 < cap/10（按 integralLeak 解析可算）",
    },
    "INV-13": {
        "id": "INV-13", "owner": "F2",
        "title": "相变映射精确",
        "criterion": "ratio<0.70→normal、[0.70,0.85)→shift、≥0.85→critical；无锚点缺失",
    },
    "INV-14": {
        "id": "INV-14", "owner": "F2",
        "title": "锚点真的在首尾",
        "criterion": "触发轮最终 prompt 首含 ANCHOR_PREFIX、尾含 ANCHOR_SUFFIX",
    },
    "INV-15": {
        "id": "INV-15", "owner": "F8",
        "title": "变形关系成立",
        "criterion": "F8 六条 MR 全成立（缩放不变、单调不降、置换等价、边界翻转、注入置换性）",
    },
    "INV-16": {
        "id": "INV-16", "owner": "F6",
        "title": "存储失败不静默",
        "criterion": "注入 store 故障后 memorywriter 要么确认写入要么显式抛错/计入 rejected；不得静默丢失",
    },
    "INV-17": {
        "id": "INV-17", "owner": "F7",
        "title": "稳态基线",
        "criterion": "冻结回放集上 recall@3=1.000、forbidden_leak=0、truncated_turns=0、blocked_turns=0",
    },
    "INV-18": {
        "id": "INV-18", "owner": "runner/validate",
        "title": "语料冻结",
        "criterion": "8 份语料的 SHA-256 与 manifest 记录一致；--check 可复现",
    },
    "INV-19": {
        "id": "INV-19", "owner": "runner",
        "title": "零仓库污染",
        "criterion": "运行只写 out-dir（默认 acceptance/chaos/v2/）下的文件，不建临时目录/子目录，不碰 rules/plugin/desktop",
    },
    "INV-20": {
        "id": "INV-20", "owner": "runner",
        "title": "判定不漂移",
        "criterion": "同一份语料在 --seed 变化时，全部 invariant 判定不变（指标可浮动）",
    },
}
INVARIANT_IDS = tuple(INVARIANTS)


def catalog() -> dict:
    """返回目录的深拷贝（只读使用者改不动模块级常量）。"""
    return {k: dict(v) for k, v in INVARIANTS.items()}


def describe(inv_id: str) -> dict:
    return dict(INVARIANTS.get(str(inv_id), {"id": str(inv_id), "title": "（未登记）",
                                             "owner": "?", "criterion": ""}))


# ── INV-01 确定性（按族）────────────────────────────────
def check_determinism(family_id: str, digest_a, digest_b, seed) -> dict:
    """同 seed 两次运行的 digest 必须完全相同（§6.2 / §6.4 r=0 复跑）。"""
    a = "" if digest_a is None else str(digest_a)
    b = "" if digest_b is None else str(digest_b)
    if not a or not b:
        return {"pass": False,
                "detail": "确定性无法证明：digest 缺失（a=%r, b=%r）" % (a[:12], b[:12])}
    if a == b:
        return {"pass": True,
                "detail": "%s seed=%s 两次运行 digest 一致：%s" % (
                    family_id, seed, a[:16])}
    return {"pass": False,
            "detail": "%s seed=%s 两次运行 digest 不一致：%s ≠ %s（engine 必须逐字节确定）"
                      % (family_id, seed, a[:16], b[:16])}


# ── INV-02 零模型调用 ───────────────────────────────────
class AuditProbe:
    """用 CPython 审计钩子（PEP 578）为 INV-02 采集证据：子进程 / 网络事件。

    为什么用审计钩子而不是"相信代码不调模型"：v2 的红线是
    **engine 零模型调用、无网络、至多一个 node 子进程（F6）**。
    审计钩子能在进程内留下不可伪造的调用痕迹（谁 spawn 了什么都记下来），
    比"我读过代码"强。钩子**只记录不阻断**（阻断会改变被测对象行为）。
    钩子一旦安装无法卸载（CPython 语义），因此 runner 在跑族之前安装、跑完停用记录。
    """

    _SUBPROCESS_EVENTS = frozenset({
        "subprocess.Popen", "os.system", "os.exec", "os.posix_spawn", "os.spawn",
    })
    _NETWORK_EVENTS = frozenset({
        "socket.connect", "socket.getaddrinfo", "socket.gethostbyname",
        "socket.socket", "urllib.Request", "http.client.connect",
    })

    def __init__(self) -> None:
        self.subprocess_dispatches = []
        self.network_events = []
        self.write_ops = []          # 写意图的系统调用（INV-19 的归因证据）
        self.fd_opens = 0            # 无名 fd 的 open 次数（subprocess 管道等，不算路径）
        self.current_family = None
        self._active = False
        self._installed = False

    def install(self) -> "AuditProbe":
        if self._installed:
            return self
        sys.addaudithook(self._hook)
        self._installed = True
        return self

    def start(self) -> "AuditProbe":
        self._active = True
        return self

    def stop(self) -> "AuditProbe":
        self._active = False
        return self

    def begin_family(self, family_id: str) -> None:
        self.current_family = family_id

    def end_family(self) -> None:
        self.current_family = None

    def _hook(self, event, args):
        if not self._active:
            return
        try:
            if event in self._SUBPROCESS_EVENTS:
                argv = self._extract_argv(args)
                self.subprocess_dispatches.append(self._classify(argv))
            elif event in self._NETWORK_EVENTS:
                detail = ""
                for arg in args[:1]:
                    detail = str(arg)[:120]
                self.network_events.append({"event": str(event), "target": detail,
                                            "family": self.current_family})
            elif event == "open":
                self._record_write_op("open", args)
            elif event in ("os.mkdir", "os.rmdir", "os.remove", "os.rename", "os.replace"):
                self._record_write_op(str(event), args)
        except Exception:  # noqa: BLE001 - 审计钩子里绝不能抛异常（会打断被测代码）
            pass

    def _record_write_op(self, op: str, args) -> None:
        """记录"写意图"的文件系统操作（只记录，不阻断）。

        - `open` 事件参数是 `(path, mode, flags)`；**注意 PEP 578 里第一个参数可能是
          文件描述符 int**（`subprocess.run(capture_output=True)` 内部 `os.fdopen(3)`），
          此时它不是路径，必须单独计 `fd_opens`，否则会被 `abspath("3")` 误判成
          `<repo>/3` 的越界写（这是 F6 的 4 次 node 子进程造成的假阳性）。
        - 只有 w/a/x/+ 模式或 O_WRONLY/O_RDWR/O_CREAT/O_APPEND/O_TRUNC 位才算写；
          否则 import 的读开也会被记成污染。
        - mkdir/remove/rename 一律记（语义本身就是写；rename 记 src+dst）。
        这是 INV-19 的**归因**证据：快照只能说明"仓库变了"，审计钩子能说明
        "是谁想写哪儿"——共享工作区里其它进程（DSH 会话、别的任务）的写入不该算到
        campaign 头上。
        """
        if op == "open":
            if len(args) < 2:
                return
            arg0, mode = args[0], args[1]
            flags = args[2] if len(args) > 2 else None
            path = self._path_arg(arg0)
            if path is None:                      # fd / 非法类型：不是路径
                self.fd_opens += 1
                return
            write_intent = False
            if isinstance(mode, str):
                write_intent = any(ch in mode for ch in ("w", "a", "x", "+"))
            elif isinstance(flags, int):
                write_bits = (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_APPEND | os.O_TRUNC)
                write_intent = bool(flags & write_bits)
            if not write_intent:
                return
            self._push_write_op(op, path)
            return

        # mkdir/rmdir/remove/rename：参数里前 1–2 个是路径
        paths = []
        for arg in list(args)[:2]:
            resolved = self._path_arg(arg)
            if resolved is not None:
                paths.append(resolved)
        if not paths:
            return
        self._push_write_op(op, paths[0], paths[1] if len(paths) > 1 else None)

    @staticmethod
    def _path_arg(arg):
        """把审计事件参数安全地转成路径字符串；不是路径（fd/int/None）返回 None。"""
        if not isinstance(arg, (str, bytes, bytearray, os.PathLike)):
            return None
        try:
            value = os.fspath(arg)
        except TypeError:
            return None
        if isinstance(value, (bytes, bytearray)):
            try:
                value = bytes(value).decode("utf-8", "replace")
            except Exception:  # noqa: BLE001
                return None
        text = str(value)
        return text or None

    def _push_write_op(self, op: str, path: str, dst=None) -> None:
        try:
            resolved = os.path.abspath(path)
        except (OSError, ValueError):
            resolved = path
        entry = {"op": op, "path": resolved, "family": self.current_family}
        if dst:
            try:
                entry["dst"] = os.path.abspath(dst)
            except (OSError, ValueError):
                entry["dst"] = dst
        self.write_ops.append(entry)

    def write_op_record(self) -> dict:
        return {"total": len(self.write_ops), "fd_opens": int(self.fd_opens),
                "ops": list(self.write_ops)[:100]}

    @staticmethod
    def _extract_argv(args) -> list:
        """从审计事件参数里取 argv。

        平台差异（实测 CPython 3.12 / Windows）：
          - POSIX：`subprocess.Popen` 的 args 是 `(executable, argv_list, cwd, env)`；
          - Windows：args 是 `(None, "cmd /c \\"echo hi\\"", None, None)` —— 真正的
            命令行是**第二个元素的字符串**，没有 argv list。
        `os.system` 则只有 `(command,)`。所以先找 list/tuple，找不到就把命令字符串
        按命令行规则切开；不能"看到第一个 str 就收工"，否则 F6 的 `node ... --no-model`
        会被误判成裸 executable 而被 INV-02 打成违规。
        """
        for arg in args:
            if isinstance(arg, (list, tuple)):
                return [str(x) for x in arg][:16]
        for arg in args:
            if isinstance(arg, bytes):
                arg = arg.decode("utf-8", "replace")
            if isinstance(arg, str) and arg.strip():
                return AuditProbe.split_command_line(arg)[:16]
        return []

    @staticmethod
    def split_command_line(command) -> list:
        """把一条命令行字符串切成 token（Windows 风格：保留引号成组后剥掉引号）。"""
        text = str(command or "").strip()
        if not text:
            return []
        try:
            parts = shlex.split(text, posix=False)
        except ValueError:
            parts = text.split()
        cleaned = []
        for part in parts:
            token = str(part).strip()
            if len(token) >= 2 and token[0] == token[-1] and token[0] in ("'", '"'):
                token = token[1:-1]
            if token:
                cleaned.append(token)
        return cleaned

    def _classify(self, argv) -> dict:
        args = [str(a) for a in (argv or [])]
        base = os.path.basename(args[0]).lower() if args else ""
        has_no_model = any(a == "--no-model" or a.startswith("--no-model=") for a in args)
        family = self.current_family or ""
        # docs/23 §7：唯一允许的子进程是 F6 的 node（纯计算，--no-model）。
        # 只按"族 + node + --no-model"判许可，不限制次数：R 次重复 + INV-01 复跑
        # 会顺序起多个 node（§7 的"至多 1 个"指并发，不是累计）。
        allowed = (family.startswith("f6")
                   and base in ("node", "node.exe")
                   and has_no_model)
        return {"argv": args[:12], "family": family, "allowed": allowed,
                "reason": "f6 node --no-model" if allowed else "engine 模式不允许该子进程"}

    def subprocess_record(self) -> dict:
        calls = list(self.subprocess_dispatches)
        violations = [c for c in calls if not c.get("allowed")]
        return {
            "node_calls": len([c for c in calls if c.get("allowed")]),
            "total": len(calls),
            "allowed": [c for c in calls if c.get("allowed")],
            "violations": violations,
        }


def check_zero_model_calls(probe: AuditProbe, mode: str = "engine") -> dict:
    """INV-02：engine 模式零模型调用；无网络；子进程只允许 F6 的 node --no-model。"""
    rec = probe.subprocess_record() if probe is not None else {"node_calls": 0, "total": 0,
                                                               "violations": []}
    network = list(getattr(probe, "network_events", [])) if probe is not None else []
    problems = []
    if mode != "engine":
        problems.append("mode=%r 不是 engine" % mode)
    if rec["violations"]:
        problems.append("出现非许可子进程：%r" % ([c["argv"][:3] for c in rec["violations"]][:3],))
    if network:
        problems.append("出现网络事件：%r" % ([n["event"] for n in network][:3],))
    ok = not problems
    detail = ("engine 零模型调用；子进程 %d 次（其中 f6 node --no-model %d 次）；网络事件 %d 次"
              % (rec["total"], rec["node_calls"], len(network)))
    if problems:
        detail = "；".join(problems) + "；" + detail
    return {"pass": ok, "detail": detail,
            "evidence": {"subprocess": rec["total"], "node_calls": rec["node_calls"],
                         "network_events": len(network)}}


# ── INV-18 语料冻结 ─────────────────────────────────────
def check_corpus_frozen(expected: dict, before: dict, after: dict) -> dict:
    """三向比对：manifest 记录的指纹 / 运行前实测 / 运行后实测。

    为什么要比 after：防止"运行过程中语料被别的进程改了"。语料被改而结果照报，
    等于用未预注册的刺激集判定（红线 6）。
    """
    expected = expected or {}
    before = before or {}
    after = after or {}
    problems = []
    for name in sorted(expected):
        want = str(expected.get(name) or "")
        got_before = str(before.get(name) or "")
        got_after = str(after.get(name) or "")
        if not got_after:
            problems.append("%s 运行后缺失" % name)
        elif got_after != want:
            problems.append("%s 指纹不符（manifest=%s… 实测=%s…）"
                            % (name, want[:12], got_after[:12]))
        elif got_before and got_before != got_after:
            problems.append("%s 运行期间被改动（%s… → %s…）"
                            % (name, got_before[:12], got_after[:12]))
    ok = not problems
    checked = len(expected)
    detail = ("%d 份语料 SHA-256 与 manifest 一致，运行前后未变" % checked) if ok \
        else "；".join(problems[:8])
    return {"pass": ok, "detail": detail}


# ── INV-19 零仓库污染 ───────────────────────────────────
_SKIP_DIR_NAMES = frozenset({
    ".git", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    "node_modules", ".venv", "venv", ".chaos-v2",
})
_SKIP_SUFFIXES = (".pyc", ".pyo")

# INV-19 的**运行时排除前缀**（仓库相对路径，`/` 分隔；大小写不敏感）。
# 为什么需要这张表：campaign 与 DSH 宿主/并行 host 验证任务共享同一个工作区，
# 宿主会话文件（`paper/host-validation/.runtime/...`、`s4-long-session/cells/...`）
# 与混沌隔离数据目录（`.chaos-data*`）会在运行窗内被**别的进程**写入。
# 把它们算成"campaign 污染"会让 INV-19 变成"环境噪声探测器"，每晚必红。
#
# **这不是放宽红线**：campaign 自身的写意图由审计钩子逐调用归因
# （`AuditProbe.write_ops`），任何越出 out-dir 的写操作照样判失败；
# 快照只负责抓"进程树之外但确实出现在受保护目录里的变化"，因此仍需运行时排除表。
RUNTIME_EXCLUDE_PREFIXES = (
    "paper/host-validation/.runtime/",
    "paper/host-validation/s4-long-session/cells/",
    "probe/",
    "probes/",
    ".chaos-data",
    ".chaos-v2",
)


def _norm(path: str) -> str:
    return os.path.normcase(os.path.abspath(path)).rstrip("\\/")


def _is_under(path: str, prefixes: list) -> bool:
    p = _norm(path)
    for prefix in prefixes:
        if p == prefix or p.startswith(prefix + os.sep):
            return True
    return False


def runtime_excluded_prefixes() -> tuple:
    """返回 INV-19 的运行时排除前缀（给 runner 写进 JSON / 报告用，诚实披露）。"""
    return tuple(RUNTIME_EXCLUDE_PREFIXES)


def _is_runtime_excluded(rel_path: str) -> bool:
    """仓库相对路径是否命中运行时排除前缀（大小写不敏感，`/` 与 `\\` 等价）。"""
    rel = str(rel_path).replace("\\", "/").lstrip("./").lower()
    if not rel:
        return False
    for prefix in RUNTIME_EXCLUDE_PREFIXES:
        pref = prefix.replace("\\", "/").lower()
        if rel == pref.rstrip("/") or rel.startswith(pref):
            return True
    return False


def snapshot_tree(root: str, exclude_paths=()) -> dict:
    """文件快照 {relpath: [mtime_ns, size]}，用于 INV-19 前后比对。

    `exclude_paths` 由 runner 传：out-dir（本次允许写的唯一目录）以及不可控的
    解释器缓存目录（`__pycache__` 等，见 `_SKIP_DIR_NAMES`）。只读，不跟随符号链接。
    """
    root = os.path.abspath(root)
    prefixes = [_norm(p) for p in exclude_paths]
    snap = {}
    for dirpath, dirnames, filenames in os.walk(root):
        kept = []
        for name in sorted(dirnames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            if name in _SKIP_DIR_NAMES:
                continue
            if _is_under(full, prefixes) or _is_runtime_excluded(rel):
                continue
            kept.append(name)
        dirnames[:] = kept
        for name in filenames:
            if name.endswith(_SKIP_SUFFIXES):
                continue
            full = os.path.join(dirpath, name)
            if _is_under(full, prefixes):
                continue
            rel = os.path.relpath(full, root)
            if _is_runtime_excluded(rel):
                continue
            try:
                st = os.stat(full)
            except OSError:
                continue
            snap[rel] = [int(st.st_mtime_ns), int(st.st_size)]
    return snap


def diff_snapshot(before: dict, after: dict) -> dict:
    before = before or {}
    after = after or {}
    added = sorted(set(after) - set(before))
    removed = sorted(set(before) - set(after))
    modified = sorted(k for k in set(before) & set(after) if before[k] != after[k])
    return {"added": added, "removed": removed, "modified": modified}


def check_write_scope(diff: dict, allowed_paths=(), write_opens=None,
                      excluded_prefixes=None, strict_snapshot: bool = False) -> dict:
    """INV-19：本 campaign 只允许写 out-dir。

    两条证据，**判定权不同**（这是 v2.0 在共享工作区里的重要修正）：

    1. **写意图归因**（`write_opens`，来自 AuditProbe，进程内审计钩子）——
       campaign 进程内的 open(w)/mkdir/remove/rename 路径必须全部落在 allowed_paths 下。
       这条是**精确归因**：只可能是本进程写的，不受"别的会话同时在写仓库"干扰。
       **判定以它为准**：有越界写意图 → 不通过。
    2. **仓库快照差分**（`diff`）——受保护目录里运行前后不能有新增/删除/改动。
       但共享工作区里**其它进程**（宿主、别的会话、编辑器、并行任务）也会写仓库，
       快照差分只能证明"相关性"、不能证明"是 campaign 写的"。因此默认把它作为
       **证据披露**（`evidence.snapshot_diff` / `external_changes`），不直接判失败；
       需要严格门禁时传 `strict_snapshot=True`（CLI `--strict-write-scope`），
       此时任何差分都判失败，适合"独占工作区 + CI"场景。

    快照已排除 out-dir、解释器缓存与 `RUNTIME_EXCLUDE_PREFIXES`；detail 里**原样披露**
    排除了哪些前缀与哪些差分——诚实说明"运行时噪音不计入 campaign 写盘"，不是放宽红线。
    """
    diff = diff or {}
    added = list(diff.get("added") or [])
    removed = list(diff.get("removed") or [])
    modified = list(diff.get("modified") or [])
    allowed_norm = [_norm(p) for p in (allowed_paths or ())]
    write_violations = []
    for item in (write_opens or []):
        item = item or {}
        outside = []
        for key in ("path", "dst"):
            path = str(item.get(key) or "")
            if not path:
                continue
            if not _is_under(path, allowed_norm):
                outside.append(path)
        if outside:
            entry = dict(item)
            entry["outside"] = outside
            write_violations.append(entry)
    snapshot_diff = bool(added or removed or modified)
    ok = not write_violations and not (strict_snapshot and snapshot_diff)
    parts = []
    if write_violations:
        parts.append("越界写意图 %d 次（%s）" % (
            len(write_violations),
            "；".join("%s:%s" % (v.get("op"), os.path.basename(str(v.get("path"))))
                     for v in write_violations[:5])))
    if snapshot_diff:
        parts.append("快照差分 %d 处（新增 %d / 删除 %d / 改动 %d）"
                     % (len(added) + len(removed) + len(modified),
                        len(added), len(removed), len(modified)))
    prefixes = list(excluded_prefixes if excluded_prefixes is not None
                    else RUNTIME_EXCLUDE_PREFIXES)
    allowed_text = ", ".join(_display(p) for p in (allowed_paths or ())) or "（无）"
    excl_text = ", ".join(prefixes) if prefixes else "（无）"
    head = "允许写：%s；运行时排除（不计入）：%s" % (allowed_text, excl_text)
    if ok:
        detail = "campaign 进程内零越界写意图；" + head
        if snapshot_diff:
            detail += ("；快照差分 %d 处**未归因到本进程**（共享工作区里其它进程写入，"
                       "已披露；`--strict-write-scope` 下会判失败）"
                       % (len(added) + len(removed) + len(modified)))
    else:
        detail = "越界：" + "；".join(parts) + "；" + head
    return {"pass": ok, "detail": detail,
            "excluded_runtime_prefixes": prefixes,
            "evidence": {"added": added[:50], "removed": removed[:50],
                         "modified": modified[:50],
                         "snapshot_diff_total": len(added) + len(removed) + len(modified),
                         "strict_snapshot": bool(strict_snapshot),
                         "write_ops_total": len(write_opens or []),
                         "write_op_violations": write_violations[:20]}}


def _display(path: str) -> str:
    """把允许写盘路径显示成仓库内相对样子（保留最后两段，如 acceptance/chaos/v2）。"""
    text = str(path or "").replace("\\", "/").rstrip("/")
    parts = [p for p in text.split("/") if p]
    return "/".join(parts[-2:]) if len(parts) > 2 else (text or "（无）")


# ── INV-20 判定不漂移（按族）────────────────────────────
def check_verdict_stability(family_id: str, per_repeat_invariants) -> dict:
    """把同族 R 次（seed=seed_base+r，即不同 seed）的不变量判定逐 ID 对比。

    §4 INV-20 只要求"判定不变"；§6.4 又要求把偶发失败记成 flaky。
    两者同时成立的做法：**这里只报稳定性**（哪些 ID 的布尔结果在不同 seed 下变了），
    聚合判定仍走 `aggregate_family_invariants`（flaky 也判 false）。
    """
    records = [r for r in (per_repeat_invariants or []) if isinstance(r, dict)]
    if len(records) < 2:
        return {"pass": True, "detail": "%s 只有 %d 次观测，无跨 seed 漂移可比"
                                        % (family_id, len(records))}
    ids = []
    for rec in records:
        for inv_id in rec:
            if inv_id not in ids:
                ids.append(inv_id)
    unstable = []
    for inv_id in ids:
        flags = []
        missing = False
        for rec in records:
            entry = rec.get(inv_id)
            if not isinstance(entry, dict) or not isinstance(entry.get("pass"), bool):
                missing = True
                flags.append(None)
            else:
                flags.append(bool(entry.get("pass")))
        if missing:
            unstable.append("%s（部分轮次未报）" % inv_id)
        elif len(set(flags)) > 1:
            unstable.append("%s（%s）" % (inv_id, "".join("✓" if f else "✗" for f in flags)))
    ok = not unstable
    detail = ("%s 跨 %d 个 seed 判定完全一致（%d 条不变量）"
              % (family_id, len(records), len(ids))) if ok else \
             ("%s 判定漂移：%s" % (family_id, "；".join(unstable[:8])))
    return {"pass": ok, "detail": detail}


# ── 族内不变量：返回值契约 + §6.4 聚合 ──────────────────
def _as_int(value, default=0):
    if isinstance(value, bool):
        return int(value), False
    if isinstance(value, int):
        return value, True
    if isinstance(value, float) and value.is_integer():
        return int(value), True
    return default, False


def normalize_family_payload(family_id: str, declared_ids, payload) -> tuple:
    """校验并规范化族 `run()` 的返回值，返回 `(normalized, problems)`。

    problems 非空 = 族违反了 runner 契约（§10 / task 里的族接口）。此时
    runner 仍会尝试用已提供的字段判定（缺的不变量按"未证明"计 false），
    但会把问题写进 anomalies 并让退出码为 2 —— 不许静默当没发生。
    """
    problems = []
    if not isinstance(payload, dict):
        problems.append("%s.run() 返回 %s，契约要求 dict"
                        % (family_id, type(payload).__name__))
        payload = {}

    skipped = payload.get("skipped", False)
    if not isinstance(skipped, bool):
        problems.append("%s skipped 必须是 bool（实际 %r）" % (family_id, skipped))
        skipped = bool(skipped)
    skip_reason = payload.get("skip_reason")
    if skipped and not (isinstance(skip_reason, str) and skip_reason.strip()):
        problems.append("%s skipped=true 时必须给非空 skip_reason（红线 3）" % family_id)
        skip_reason = skip_reason if isinstance(skip_reason, str) else "unspecified_skip"

    checks_raw = payload.get("checks")
    if not isinstance(checks_raw, dict):
        problems.append("%s 缺 checks 对象" % family_id)
        checks_raw = {}
    cases, ok_cases = _as_int(checks_raw.get("cases", 0))
    expectations, ok_exp = _as_int(checks_raw.get("expectations", 0))
    failed, ok_failed = _as_int(checks_raw.get("failed", 0))
    for label, ok in (("cases", ok_cases), ("expectations", ok_exp), ("failed", ok_failed)):
        if not ok:
            problems.append("%s checks.%s 必须是非负整数" % (family_id, label))
    if cases < 0 or expectations < 0 or failed < 0:
        problems.append("%s checks 计数不能为负" % family_id)
        cases, expectations, failed = max(0, cases), max(0, expectations), max(0, failed)
    failures_raw = checks_raw.get("failures")
    if failures_raw is None:
        failures_raw = []
    if not isinstance(failures_raw, list):
        problems.append("%s checks.failures 必须是数组" % family_id)
        failures_raw = []
    failures = []
    for idx, item in enumerate(failures_raw):
        if not isinstance(item, dict):
            problems.append("%s checks.failures[%d] 必须是 object" % (family_id, idx))
            continue
        entry = {
            "case": str(item.get("case", "")),
            "want": item.get("want", ""),
            "got": item.get("got", ""),
            "note": str(item.get("note", "") or ""),
        }
        failures.append(entry)
    if failed and not failures:
        problems.append("%s checks.failed=%d 但 failures 为空（不许用计数顶替清单，§6.7）"
                        % (family_id, failed))
    if len(failures) > max(failed, 0):
        failed = len(failures)

    invariant_raw = payload.get("invariants")
    if not isinstance(invariant_raw, dict):
        problems.append("%s 缺 invariants 对象（§4：缺失=schema 违规=失败）" % family_id)
        invariant_raw = {}
    declared = [str(x) for x in (declared_ids or [])]
    # skipped=true 的族允许不报不变量（由 runner 统一按"未证明"记 false）：
    # 此时缺 ID 不是 schema 违规，而是"没跑成"的诚实表达（红线 3）。
    if not skipped:
        for inv_id in declared:
            if inv_id not in invariant_raw:
                problems.append("%s 缺不变量 %s（声明了却没报）" % (family_id, inv_id))
    invariants = {}
    for inv_id, entry in invariant_raw.items():
        inv_id = str(inv_id)
        where = "%s.%s" % (family_id, inv_id)
        if inv_id not in INVARIANTS:
            problems.append("%s 是未登记的不变量 id（编号冻结，新增只能追加）" % where)
            continue
        if not isinstance(entry, dict):
            problems.append("%s 必须是 {pass, detail}（实际 %r）" % (where, entry))
            continue
        flag = entry.get("pass")
        detail = entry.get("detail")
        if not isinstance(flag, bool):
            problems.append("%s.pass 必须是 bool（实际 %r）" % (where, flag))
            flag = bool(flag)
        if not isinstance(detail, str) or not detail.strip():
            problems.append("%s.detail 必须是非空中文说明" % where)
            detail = str(detail or "")
        invariants[inv_id] = {"pass": flag, "detail": detail}

    metrics = payload.get("metrics")
    if not isinstance(metrics, dict):
        problems.append("%s 缺 metrics 对象" % family_id)
        metrics = {}
    rates_raw = payload.get("rates")
    if not isinstance(rates_raw, dict):
        problems.append("%s 缺 rates 对象" % family_id)
        rates_raw = {}
    rates = {}
    for name, entry in rates_raw.items():
        name = str(name)
        if not isinstance(entry, dict):
            problems.append("%s rates[%r] 必须是 {num, den}" % (family_id, name))
            continue
        num, ok_num = _as_int(entry.get("num", 0))
        den, ok_den = _as_int(entry.get("den", 0))
        if not (ok_num and ok_den) or num < 0 or den < 0:
            problems.append("%s rates[%r] 的 num/den 必须是非负整数" % (family_id, name))
            continue
        rates[name] = {"num": num, "den": den}
    samples = payload.get("samples")
    if samples is None:
        samples = []
    if not isinstance(samples, list):
        problems.append("%s samples 必须是数组" % family_id)
        samples = []
    notes = payload.get("notes")
    if notes is None:
        notes = []
    if not isinstance(notes, list):
        problems.append("%s notes 必须是数组" % family_id)
        notes = []
    notes = [str(n) for n in notes]

    normalized = {
        "checks": {"cases": int(cases), "expectations": int(expectations),
                   "failed": int(failed), "failures": failures},
        "metrics": metrics,
        "rates": rates,
        "samples": samples[:50],
        "notes": notes,
        "invariants": invariants,
        "skipped": skipped,
        "skip_reason": None if not skipped else str(skip_reason),
    }
    return normalized, problems


def skipped_payload(skip_reason: str, notes=None) -> dict:
    """构造"没跑成"的规范化族结果（skipped=true，绝不填 0 冒充结果）。"""
    return {
        "checks": {"cases": 0, "expectations": 0, "failed": 0, "failures": []},
        "metrics": {},
        "rates": {},
        "samples": [],
        "notes": [str(n) for n in (notes or [])],
        "invariants": {},
        "skipped": True,
        "skip_reason": str(skip_reason),
    }


def aggregate_family_invariants(declared_ids, per_repeat_invariants) -> dict:
    """§6.4：把同族 R 次的 `{id: {pass, detail}}` 聚合成 `{id: {pass, flaky, detail, observed}}`。

    缺失（某轮没报）按"未证明"记 false，不占用 flaky（flaky 专指真观测到偶发失败）。
    """
    records = [r for r in (per_repeat_invariants or []) if isinstance(r, dict)]
    ids = []
    for inv_id in list(declared_ids or []):
        if inv_id not in ids:
            ids.append(str(inv_id))
    for rec in records:
        for inv_id in rec:
            if inv_id not in ids:
                ids.append(str(inv_id))
    out = {}
    for inv_id in ids:
        flags, details = [], []
        for rec in records:
            entry = rec.get(inv_id)
            if isinstance(entry, dict):
                flags.append(entry.get("pass"))
                details.append(entry.get("detail", ""))
            else:
                flags.append(None)
                details.append("")
        merged = stats.aggregate_bools(flags, details)
        out[inv_id] = {
            "pass": merged["pass"],
            "flaky": merged["flaky"],
            "detail": merged["detail"],
            "observed": merged["observed"],
        }
    return out


# ── 模块契约检查 ────────────────────────────────────────
def check_module_contract(family: dict, module) -> list:
    """检查族模块暴露的 FAMILY_ID / INVARIANTS / STRESS_CLASS / run（返回问题列表）。"""
    fid = str(family.get("id"))
    problems = []
    module_fid = getattr(module, "FAMILY_ID", None)
    if module_fid is None:
        problems.append("%s 模块缺 FAMILY_ID" % fid)
    elif str(module_fid) != fid:
        problems.append("%s 模块 FAMILY_ID=%r 与 manifest 族 id 不一致" % (fid, module_fid))
    declared = getattr(module, "INVARIANTS", None)
    if not isinstance(declared, (tuple, list)) or not declared:
        problems.append("%s 模块缺 INVARIANTS 元组" % fid)
    else:
        unknown = [str(x) for x in declared if str(x) not in INVARIANTS]
        if unknown:
            problems.append("%s 模块声明了未登记的不变量 %r" % (fid, unknown))
        manifest_inv = family.get("invariants")
        if isinstance(manifest_inv, list) and manifest_inv:
            missing = [str(x) for x in manifest_inv if str(x) not in [str(y) for y in declared]]
            extra = [str(x) for x in declared if str(x) not in [str(y) for y in manifest_inv]]
            if missing or extra:
                problems.append("%s 模块/ manifest 的不变量清单不一致（模块缺 %r，模块多 %r）"
                                % (fid, missing, extra))
    stress = getattr(module, "STRESS_CLASS", None)
    if not isinstance(stress, str) or not stress.strip():
        problems.append("%s 模块缺 STRESS_CLASS" % fid)
    if not callable(getattr(module, "run", None)):
        problems.append("%s 模块缺可调用的 run(rows, ctx)" % fid)
    return problems


__all__ = [
    "INVARIANTS", "INVARIANT_IDS", "RUNTIME_EXCLUDE_PREFIXES",
    "catalog", "describe", "runtime_excluded_prefixes",
    "check_determinism", "AuditProbe", "check_zero_model_calls",
    "check_corpus_frozen", "snapshot_tree", "diff_snapshot", "check_write_scope",
    "check_verdict_stability", "normalize_family_payload", "skipped_payload",
    "aggregate_family_invariants", "check_module_contract",
]
