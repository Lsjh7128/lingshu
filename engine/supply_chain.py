# -*- coding: utf-8 -*-
"""LINGSHU 引擎 · 第三方插件供应链加载闸门（S-CAP / ADR-017）Python 参考实现

定位：DSH 宿主**外挂**第三方认知插件加载闸门的 Python 侧同构镜像。三层检查，
任何一层失败即 fail-closed：
  1) Manifest 解析与校验：能力声明 / 入口 / 完整性哈希 / fs 作用域；
  2) 静态扫描：未声明的高危底层 API（fs.write / child_process / net / eval …）→ deny；
  3) 沙箱计划：只把申报且可授予的能力翻译成子进程权限；不可支撑 → deny（不降级）。

语义唯一来源（parity 强制）：
  · ``plugin/dsh-lingshu/supply-chain.js`` —— 逐字段镜像的 JS 实现（语义标准答案）；
  · ``contracts/supply-chain-policy.json`` —— 策略表单一来源（json 标准库读取）。

设计约束（与仓库其余模块同口径）：
  · 零依赖、纯函数、确定性：``decide`` 不读磁盘、不写日志、不抛异常；
  · 只有模块导入期按 `contracts/` 路径读一次策略表（与 JS 同构）；策略缺失/损坏
    时落到 ``FALLBACK_POLICY``（deny-all，``verdict('clean') == 'deny'``），fail-closed；
  · 静态扫描只做词法匹配：拼接字符串 / 动态 import 可以绕过，真正的物理边界在
    sandbox-host.mjs（Node permission model）；扫描的价值是**提前拒绝**。

接口（与 JS 同名，snake_case 化）：
    load_policy() / strip_comments(src) / scan_source(source, policy=None)
    parse_manifest(raw) / validate_manifest(manifest, policy=None)
    resolve_fs_scopes(manifest, plugin_dir, policy=None)
    build_node_flags(...) / decide(inputs, policy=None) / render_alert(decision)

CLI（S-CAP 跨语言 parity 用）：
    python engine/supply_chain.py --cases <cases.json>
    输入 ``[{id, input}]``，stdout 输出 JSON 数组（UTF-8），退出码 0。每项字段：
    ``{id, verdict, reason, error_code, failures:[code...],
    findings:[{id,capability,line}], capabilities:{requested,granted,denied},
    fs_scopes:{read,write,escapes}, exports, sandbox:{mode,flags_len}, fingerprint}``。

已知跨语言边界（详见 docs 与自测报告）：
  · ``parse_manifest`` 的 ``detail`` 是各自运行时的 JSON 解析错误文本（截断 120 字符），
    仅错误码跨语言一致；该字段不进入 ``decide`` 输出。
  · ``path.resolve`` 用平台原生语义；Windows 下 Python ``os.path`` 与 Node win32
    均为反斜杠、保留大小写。跨盘符相对路径两侧都按“逃逸”处理。
  · 本文件用 Python ``re`` 模拟 JS 无 ``u`` 标志正则：``\\w/\\d/\\s/\\b`` 与 ``$``/``.``
    都做了显式改写（见 ``_translate_js_pattern``），但 JS 的 ``match.slice(0, 80)``
    按 UTF-16 码元计数，本文件按码点近似（非 BMP 字符会被少算一位）。
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
from decimal import Decimal

# ══════════════════════════════════════════════════════════════════════
# 0. JS 语义小工具（String()/Number()/Boolean()/path.resolve 的等价物）
# ══════════════════════════════════════════════════════════════════════


class _UndefinedType:
    """JS ``undefined`` 的占位符：用于区分「键缺失」与 JSON 的 ``null``。"""

    __slots__ = ()

    def __repr__(self) -> str:  # pragma: no cover - 仅调试用
        return 'undefined'


UNDEFINED = _UndefinedType()

_MAX_SAFE_INTEGER = 2 ** 53


def _format_js_float(value: float) -> str:
    """模拟 ECMAScript Number::toString（最短往返十进制表示，JS 阈值）。"""
    if value != value:  # NaN
        return 'NaN'
    if value == math.inf:
        return 'Infinity'
    if value == -math.inf:
        return '-Infinity'
    if value == 0:
        return '0'  # JS String(-0) === '0'
    negative = value < 0
    magnitude = abs(value)
    # repr() 与 JS 一样给出最短往返数字串；再按 JS 的十进制/科学计数阈值重排。
    digits = repr(magnitude)
    decimal_value = Decimal(digits)
    adjusted = decimal_value.adjusted()
    if -6 <= adjusted < 21:
        text = format(decimal_value, 'f')
        if '.' in text:
            text = text.rstrip('0').rstrip('.')
    else:
        mantissa = decimal_value.scaleb(-adjusted)
        mantissa_text = format(mantissa, 'f').rstrip('0').rstrip('.')
        exponent = adjusted
        if exponent >= 0:
            text = '%se+%d' % (mantissa_text, exponent)
        else:
            text = '%se%d' % (mantissa_text, exponent)
    return ('-' + text) if negative else text


def _js_string(value) -> str:
    """模拟 JS ``String(value)``（覆盖 JSON 可表达的取值）。"""
    if value is UNDEFINED:
        return 'undefined'
    if value is None:
        return 'null'
    if isinstance(value, bool):
        return 'true' if value else 'false'
    if isinstance(value, int):
        if -_MAX_SAFE_INTEGER <= value <= _MAX_SAFE_INTEGER:
            return str(value)
        return _format_js_float(float(value))
    if isinstance(value, float):
        return _format_js_float(value)
    if isinstance(value, str):
        return value
    if isinstance(value, (list, tuple)):
        # Array.prototype.toString：null/undefined 元素输出空串，嵌套数组递归 join。
        return ','.join('' if item is None or item is UNDEFINED else _js_string(item)
                        for item in value)
    return '[object Object]'


_JS_NUMBER_TEXT_RE = re.compile(
    r'^[+-]?(?:Infinity|(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?)$')
_JS_NUMBER_INT_RE = re.compile(r'^[+-]?\d+$')
_JS_NUMBER_HEX_RE = re.compile(r'^([+-]?)0[xX]([0-9a-fA-F]+)$')
_JS_NUMBER_OCT_RE = re.compile(r'^([+-]?)0[oO]([0-7]+)$')
_JS_NUMBER_BIN_RE = re.compile(r'^([+-]?)0[bB]([01]+)$')


def _js_number(value):
    """模拟 JS ``Number(value)``（失败返回 NaN）。"""
    if value is UNDEFINED:
        return math.nan
    if value is None:
        return 0
    if isinstance(value, bool):
        return 1 if value else 0
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, list):
        return _js_number(_js_string(value))
    if isinstance(value, dict):
        return math.nan
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return 0
        if text.endswith('Infinity') and _JS_NUMBER_TEXT_RE.match(text):
            return -math.inf if text.startswith('-') else math.inf
        if _JS_NUMBER_INT_RE.match(text):
            try:
                return int(text)
            except ValueError:  # pragma: no cover - 超长数字
                return math.nan
        if _JS_NUMBER_TEXT_RE.match(text):
            try:
                return float(text)
            except ValueError:  # pragma: no cover
                return math.nan
        for pattern, base in ((_JS_NUMBER_HEX_RE, 16), (_JS_NUMBER_OCT_RE, 8),
                              (_JS_NUMBER_BIN_RE, 2)):
            match = pattern.match(text)
            if match:
                number = int(match.group(2), base)
                return -number if match.group(1) == '-' else number
        return math.nan
    return math.nan


def _js_number_str(value) -> str:
    """模拟模板字符串里的 ``${Number(value)}``。"""
    return _js_string(_js_number(value))


def _number_or_none(value):
    """JSON 输出用：JS ``JSON.stringify(NaN)`` / ``Infinity`` 都会变成 null。"""
    if isinstance(value, float) and (value != value or value in (math.inf, -math.inf)):
        return None
    return value


def _truthy(value) -> bool:
    """模拟 JS ``Boolean(value)``（注意：空数组/空对象在 JS 里为真）。"""
    if value is UNDEFINED or value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        if isinstance(value, float) and value != value:
            return False
        return value != 0
    if isinstance(value, str):
        return len(value) > 0
    if isinstance(value, (list, tuple, dict)):
        return True
    return True


def _as_string_array(value):
    """模拟 JS ``asStringArray``：非数组 / 含非字符串 → None，否则返回浅拷贝。"""
    if not isinstance(value, (list, tuple)):
        return None
    result = []
    for item in value:
        if not isinstance(item, str):
            return None
        result.append(item)
    return result


def _utf8_bytes(text: str) -> bytes:
    """UTF-8 字节（孤立代理项按 U+FFFD 处理，与 Node 编码行为对齐）。"""
    try:
        return text.encode('utf-8')
    except UnicodeEncodeError:
        try:
            return text.encode('utf-16', 'surrogatepass').decode('utf-16', 'replace').encode('utf-8')
        except (UnicodeEncodeError, UnicodeDecodeError):  # pragma: no cover - 防御
            return text.encode('utf-8', 'replace')


def _sha256_hex(text: str) -> str:
    return hashlib.sha256(_utf8_bytes(text)).hexdigest()


def _utf8_len(text: str) -> int:
    return len(_utf8_bytes(text))


def _js_slice(text: str, limit: int) -> str:
    """近似 JS ``String.prototype.slice(0, limit)``（按 UTF-16 码元宽度截断）。"""
    if limit <= 0:
        return ''
    out = []
    used = 0
    for char in text:
        width = 2 if ord(char) > 0xFFFF else 1
        if used + width > limit:
            break
        out.append(char)
        used += width
    return ''.join(out)


def _json_stringify(value) -> str:
    """近似 JS ``JSON.stringify``（用于 POLICY_DIGEST；策略表只含可 JSON 值）。"""
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'), allow_nan=False)


def _reject_json_constant(name: str):  # pragma: no cover - 仅非法 JSON 触发
    """``JSON.parse`` 拒绝 NaN/Infinity；Python json 默认接受，这里显式拒绝。"""
    raise ValueError('unexpected JSON constant: %s' % name)


# ══════════════════════════════════════════════════════════════════════
# 1. 策略表（单一来源 = contracts/supply-chain-policy.json）
# ══════════════════════════════════════════════════════════════════════
#
# 加载优先级（与 JS 一致）：
#   1) LINGSHU_SUPPLY_CHAIN_POLICY 环境变量（绝对路径，用于压测/审计副本）；
#   2) 仓库内相对路径（源码态）；
#   3) 插件目录下的打包副本（发布态）；
#   4) 全部失败 → deny-all 兜底策略（fail-closed）。
# 不在本文件内联冻结一份：内联副本会和契约文件漂移，而“策略漂移”在这里就是漏洞。

FALLBACK_POLICY = {
    'policy_version': 'sc-fallback-deny-all',
    'namespace': 'S-CAP',
    'schema': 'lingshu.supply-chain-policy/v1',
    'manifest_version': 'lingshu.plugin-manifest/v1',
    'capabilities': {'known': [], 'grantable': [], 'never_grantable': [], 'destructive': []},
    'manifest_rules': {
        'id_pattern': '^[a-z][a-z0-9._-]{1,63}$',
        'version_pattern': '^[0-9]+\\.[0-9]+\\.[0-9]+$',
        'entry_pattern': '^(?:\\./)?[A-Za-z0-9._/-]+\\.(?:mjs|js|cjs)$',
        'export_pattern': '^[A-Za-z_$][A-Za-z0-9_$]{0,63}$',
        'max_entry_bytes': 262144,
        'require_integrity': True,
        'allowed_sandbox': ['subprocess'],
        'forbid_parent_traversal': True,
        'max_exports': 32,
    },
    'sandbox': {
        'node_permission_flag': '--permission',
        'max_old_space_mb': 128,
        'ready_timeout_ms': 5000,
        'call_timeout_ms': 2000,
        'kill_grace_ms': 500,
        'rpc': 'tcp-loopback-jsonl',
        'env_allowlist': [],
    },
    'scan': {'strip_comments': True, 'max_findings_per_rule': 5, 'rules': []},
    'decision': {
        'fail_closed': True,
        'precedence': ['sandbox_unavailable'],
        'verdicts': {'blocker': 'deny', 'clean': 'deny'},
        'undeclared_usage': 'deny',
        'suspicious': 'deny',
    },
}

_ENGINE_DIR = os.path.dirname(os.path.abspath(__file__))
_REPO_ROOT = os.path.dirname(_ENGINE_DIR)

POLICY_PATH_CANDIDATES = [
    os.environ.get('LINGSHU_SUPPLY_CHAIN_POLICY') or '',
    os.path.join(_REPO_ROOT, 'contracts', 'supply-chain-policy.json'),
    os.path.join(_REPO_ROOT, 'plugin', 'dsh-lingshu', 'supply-chain-policy.json'),
]


def _policy_shape_ok(parsed) -> bool:
    """策略表自身也要过“零信任”：形状不对即拒绝使用（与 JS 同口径）。"""
    if not isinstance(parsed, dict):
        return False
    if not isinstance(parsed.get('policy_version'), str):
        return False
    capabilities = parsed.get('capabilities')
    if not isinstance(capabilities, dict) or not isinstance(capabilities.get('known'), list):
        return False
    scan = parsed.get('scan')
    if not isinstance(scan, dict) or not isinstance(scan.get('rules'), list):
        return False
    decision = parsed.get('decision')
    if not isinstance(decision, dict) or not _truthy(decision.get('verdicts')):
        return False
    return True


def load_policy() -> dict:
    """按候选路径加载策略表；全部失败 → ``FALLBACK_POLICY``（degraded=True）。

    返回 ``{policy, source, degraded}``（与 JS ``loadPolicy`` 同名字段）。
    不抛异常：任何读取/解析/形状错误都只是“试下一个候选”。
    """
    for candidate in POLICY_PATH_CANDIDATES:
        try:
            if not candidate:
                continue
            with open(candidate, 'r', encoding='utf-8') as handle:
                parsed = json.loads(handle.read(), parse_constant=_reject_json_constant)
            if not _policy_shape_ok(parsed):
                continue
            return {'policy': parsed, 'source': str(candidate), 'degraded': False}
        except Exception:  # noqa: BLE001 - 试下一个候选；全部失败走兜底
            continue
    return {'policy': FALLBACK_POLICY, 'source': 'fallback', 'degraded': True}


_LOADED_POLICY = load_policy()

POLICY = _LOADED_POLICY['policy']
POLICY_SOURCE = _LOADED_POLICY['source']
POLICY_DEGRADED = _LOADED_POLICY['degraded']
POLICY_VERSION = _js_string(POLICY.get('policy_version'))
MANIFEST_VERSION = _js_string(POLICY.get('manifest_version'))
POLICY_DIGEST = _sha256_hex(_json_stringify(POLICY))

# deny 的错误码（审计与文档共用；单边新增必须同步 docs/26 §5）
ERROR_CODES = {
    'sandbox_unavailable': 'SC-E01',
    'manifest_missing': 'SC-E02',
    'manifest_unreadable': 'SC-E03',
    'manifest_invalid': 'SC-E04',
    'entry_missing': 'SC-E05',
    'entry_too_large': 'SC-E06',
    'integrity_mismatch': 'SC-E07',
    'fs_scope_escape': 'SC-E08',
    'unknown_capability': 'SC-E09',
    'forbidden_capability': 'SC-E10',
    'undeclared_usage': 'SC-E11',
    # 运行时（沙箱启动后）失败的错误码；与静态裁决共用同一命名空间（同步 JS 侧）
    'sandbox_start_failed': 'SC-E12',
    'sandbox_ready_timeout': 'SC-E13',
    'sandbox_call_timeout': 'SC-E14',
    'sandbox_crashed': 'SC-E15',
    'sandbox_spawn_failed': 'SC-E16',
    'sandbox_handshake_failed': 'SC-E17',
    'method_not_allowed': 'SC-E18',
    'plugin_error': 'SC-E19',
}

CRITICAL = frozenset(['critical', 'high'])


# ══════════════════════════════════════════════════════════════════════
# 2. JS 正则 → Python re 的显式改写
# ══════════════════════════════════════════════════════════════════════
#
# JS（无 u 标志）的简写类都是 ASCII 口径，而 Python re 默认按 Unicode 解释，
# 例如中文“调用eval(”在 JS 里 eval 前是词边界、在 Python 里不是。为了让静态扫描
# 与 Manifest 校验的命中位置逐字一致，这里把 JS 的简写类改写成显式字符集合，
# 并把 JS 的 `$`（仅匹配输入末尾）改写成 Python 的 `\Z`（Python 的 `$` 会额外
# 匹配结尾换行之前），把 `.` 改写成不含行终止符的字符类。

_JS_WORD = 'A-Za-z0-9_'
_JS_BOUNDARY = (r'(?:(?<=[' + _JS_WORD + r'])(?![' + _JS_WORD + r'])|'
                r'(?<![' + _JS_WORD + r'])(?=[' + _JS_WORD + r']))')
_JS_NOT_BOUNDARY = (r'(?:(?<=[' + _JS_WORD + r'])(?=[' + _JS_WORD + r'])|'
                    r'(?<![' + _JS_WORD + r'])(?![' + _JS_WORD + r']))')
# ECMAScript WhiteSpace + LineTerminator（非 u 模式 \s 也吃 Unicode 空白）
_JS_SPACE = ('[\t\n\v\f\r \u00a0\u1680\u2000-\u200a'
             '\u2028\u2029\u202f\u205f\u3000\ufeff]')
_JS_NOT_SPACE = ('[^\t\n\v\f\r \u00a0\u1680\u2000-\u200a'
                 '\u2028\u2029\u202f\u205f\u3000\ufeff]')
_JS_DOT = '[^\n\r\u2028\u2029]'

_REGEX_CACHE: dict = {}


def _translate_js_pattern(pattern: str) -> str:
    """把 JS 正则源码改写成 Python re 口径（字符类上下文感知）。"""
    out = []
    index = 0
    length = len(pattern)
    in_class = False
    while index < length:
        char = pattern[index]
        if char == '\\':
            if index + 1 >= length:
                out.append('\\\\')
                index += 1
                continue
            escaped = pattern[index + 1]
            if not in_class:
                if escaped == 'w':
                    out.append('[' + _JS_WORD + ']')
                elif escaped == 'W':
                    out.append('[^' + _JS_WORD + ']')
                elif escaped == 'd':
                    out.append('[0-9]')
                elif escaped == 'D':
                    out.append('[^0-9]')
                elif escaped == 's':
                    out.append(_JS_SPACE)
                elif escaped == 'S':
                    out.append(_JS_NOT_SPACE)
                elif escaped == 'b':
                    out.append(_JS_BOUNDARY)
                elif escaped == 'B':
                    out.append(_JS_NOT_BOUNDARY)
                else:
                    out.append('\\' + escaped)
            else:
                # 字符类内部的 \w/\d/\s 可内联；其余保持原转义交给 Python。
                if escaped == 'w':
                    out.append(_JS_WORD)
                elif escaped == 'd':
                    out.append('0-9')
                elif escaped == 's':
                    out.append('\t\n\v\f\r \u00a0\u1680\u2000-\u200a'
                               '\u2028\u2029\u202f\u205f\u3000\ufeff')
                else:
                    out.append('\\' + escaped)
            index += 2
            continue
        if in_class:
            if char == ']':
                in_class = False
            out.append(char)
            index += 1
            continue
        if char == '[':
            in_class = True
            out.append(char)
            index += 1
            continue
        if char == '$':
            out.append(r'\Z')
        elif char == '.':
            out.append(_JS_DOT)
        else:
            out.append(char)
        index += 1
    return ''.join(out)


def _compile_js(pattern_text: str, ignore_case: bool = False):
    """编译改写后的正则（带缓存；缓存只影响性能，不影响纯函数语义）。"""
    key = (pattern_text, bool(ignore_case))
    cached = _REGEX_CACHE.get(key)
    if cached is not None:
        return cached
    compiled = re.compile(
        _translate_js_pattern(pattern_text),
        re.IGNORECASE if ignore_case else 0,
    )
    _REGEX_CACHE[key] = compiled
    return compiled


def _policy_regex(pattern_value, ignore_case: bool = False):
    """``new RegExp(pattern[, flags])``：pattern 为 undefined 时等价空正则。"""
    if pattern_value is UNDEFINED or pattern_value is None:
        pattern_value = ''
    return _compile_js(_js_string(pattern_value), ignore_case)


def _regex_test(pattern_value, text: str, ignore_case: bool = False) -> bool:
    """JS ``RegExp.prototype.test``（搜索语义；模式自带锚点时才近似全匹配）。"""
    if not isinstance(text, str):
        text = _js_string(text)
    try:
        return _policy_regex(pattern_value, ignore_case).search(text) is not None
    except Exception:  # noqa: BLE001 - 模式损坏时按“不命中”处理（fail-closed 由调用方加错）
        return False


_HEX64_RE = None


def _is_sha256_hex(text: str) -> bool:
    """``/^[a-f0-9]{64}$/i`` 的等价判断。"""
    global _HEX64_RE
    if _HEX64_RE is None:
        _HEX64_RE = re.compile(r'^[a-f0-9]{64}\Z', re.IGNORECASE)
    return isinstance(text, str) and _HEX64_RE.search(text) is not None


# ══════════════════════════════════════════════════════════════════════
# 3. 路径（JS path.resolve / relative / join / isAbsolute 的 Python 对应）
# ══════════════════════════════════════════════════════════════════════


def _path_resolve(value) -> str:
    """``path.resolve(String(value))``：相对当前工作目录归一为绝对路径。"""
    text = _js_string(value)
    try:
        return os.path.abspath(text)
    except Exception:  # noqa: BLE001 - 极端非法路径也不抛
        return os.path.normpath(text)


def _path_resolve_join(root: str, scope) -> str:
    """``path.resolve(root, String(scope))``：绝对 scope 覆盖 root。

    额外覆盖 Windows 的两种特例（Node win32.resolve 口径）：
      · 无盘符的根相对路径 ``/x`` → 取 root 所在盘符的根（``D:\\x``）；
      · 盘符相对路径 ``C:foo`` → 与 root 同盘时拼到 root 后，异盘落到该盘根。
    """
    text = _js_string(scope)
    try:
        if os.path.isabs(text):
            drive, _ = os.path.splitdrive(text)
            if not drive:
                root_drive, _ = os.path.splitdrive(root)
                if root_drive:
                    return os.path.normpath(root_drive + text)
            return os.path.normpath(text)
        drive, _ = os.path.splitdrive(text)
        if drive:
            root_drive, _ = os.path.splitdrive(root)
            if root_drive and drive.lower() == root_drive.lower():
                return os.path.normpath(os.path.join(root, text[len(drive):]))
            return os.path.normpath(drive + '\\' + text[len(drive):].lstrip('\\/'))
        return os.path.normpath(os.path.join(root, text))
    except Exception:  # noqa: BLE001
        return text


def _path_join(root: str, tail) -> str:
    """``path.join(root, String(tail))``（tail 始终按相对段拼接）。"""
    text = _js_string(tail)
    try:
        return os.path.normpath(root + os.sep + text)
    except Exception:  # noqa: BLE001
        return root


def _path_relative(root: str, target: str):
    """``path.relative(root, target)``；跨盘符返回 None（调用方按逃逸处理）。

    JS ``path.relative`` 跨盘符返回绝对路径；这里用 None 显式标记，避免把
    ``os.path.relpath`` 的 ValueError 误当成“未命中逃逸”。
    """
    try:
        return os.path.relpath(target, root)
    except ValueError:
        return None
    except Exception:  # noqa: BLE001
        return None


def _path_is_absolute(value) -> bool:
    """``path.isAbsolute(String(value))``。"""
    text = _js_string(value)
    try:
        return bool(os.path.isabs(text))
    except Exception:  # noqa: BLE001
        return False


# ══════════════════════════════════════════════════════════════════════
# 4. 注释剥离（保留换行，行号不漂移）
# ══════════════════════════════════════════════════════════════════════


def strip_comments(src) -> str:
    """剥离 ``//`` 与 ``/* */`` 注释，注释字符替换为空格、换行原样保留。

    字符串不剥离——宁可保守误报，也不给“藏在字符串里的 API”开后门。
    已知局限（docs/26 §6）：正则字面量里的 ``//`` 可能被误剥；模板字符串内的
    ``${}`` 不展开。本函数与 JS ``stripComments`` 逐状态镜像，不抛异常。
    """
    s = _js_string('' if src is None or src is UNDEFINED else src)
    out = list(s)
    index = 0
    length = len(s)
    state = 'code'  # code | line | block | sq | dq | tpl
    quote = ''
    while index < length:
        char = s[index]
        nxt = s[index + 1] if index + 1 < length else ''
        if state == 'code':
            if char == '/' and nxt == '/':
                out[index] = ' '
                out[index + 1] = ' '
                state = 'line'
                index += 2
                continue
            if char == '/' and nxt == '*':
                out[index] = ' '
                out[index + 1] = ' '
                state = 'block'
                index += 2
                continue
            if char == "'":
                state = 'sq'
                quote = "'"
            elif char == '"':
                state = 'dq'
                quote = '"'
            elif char == '`':
                state = 'tpl'
                quote = '`'
            index += 1
            continue
        if state == 'line':
            if char == '\n':
                state = 'code'
            else:
                out[index] = ' '
            index += 1
            continue
        if state == 'block':
            if char == '*' and nxt == '/':
                out[index] = ' '
                out[index + 1] = ' '
                state = 'code'
                index += 2
                continue
            if char != '\n' and char != '\r':
                out[index] = ' '
            index += 1
            continue
        # 字符串内：处理转义；行注释在字符串里不生效
        if char == '\\':
            index += 2
            continue
        if char == quote:
            state = 'code'
            quote = ''
        index += 1
    return ''.join(out)


def _line_of(src: str, index: int) -> int:
    """1 基行号：JS ``lineOf`` 的同义实现（只数 ``\\n``）。"""
    if index <= 0:
        return 1
    return src.count('\n', 0, min(index, len(src))) + 1


# ══════════════════════════════════════════════════════════════════════
# 5. 静态扫描
# ══════════════════════════════════════════════════════════════════════


def scan_source(source, policy=None) -> dict:
    """按 ``policy.scan.rules`` 扫描源码。

    返回 ``{findings:[{id, capability, severity, line, match}],
    by_capability:{cap:[ruleId]}, stripped_lines}``。
    同一规则最多记 ``max_findings_per_rule`` 条；规则正则损坏时按“命中一条
    unknown 规则”上报（``<invalid-pattern>``），不静默跳过。
    """
    policy = POLICY if policy is None else policy
    scan_cfg = policy.get('scan') if isinstance(policy, dict) else None
    if not isinstance(scan_cfg, dict):
        scan_cfg = {}
    raw_source = '' if source is None or source is UNDEFINED else source
    if scan_cfg.get('strip_comments') is False:
        stripped = _js_string(raw_source)
    else:
        stripped = strip_comments(raw_source)
    limit_value = scan_cfg.get('max_findings_per_rule', UNDEFINED)
    limit = 5 if not _truthy(limit_value) else _js_number(limit_value)
    findings = []
    by_capability: dict = {}
    rules = scan_cfg.get('rules')
    if not isinstance(rules, (list, tuple)):
        rules = []
    for rule in rules:
        if not isinstance(rule, dict):
            # JS 会在 rule.pattern 上抛 TypeError；本实现选择 fail-closed 上报。
            findings.append({'id': None, 'capability': None, 'severity': None,
                             'line': 0, 'match': '<invalid-pattern>'})
            continue
        rule_id = rule.get('id', UNDEFINED)
        capability = rule.get('capability', UNDEFINED)
        severity = rule.get('severity', UNDEFINED)
        capability_key = _js_string(capability)
        pattern_value = rule.get('pattern', UNDEFINED)
        pattern_text = '' if pattern_value is UNDEFINED else _js_string(pattern_value)
        try:
            regex = _compile_js(pattern_text, ignore_case=True)
        except Exception:  # noqa: BLE001 - 规则表损坏：上报而不是静默跳过
            findings.append({
                'id': rule_id,
                'capability': capability,
                'severity': severity,
                'line': 0,
                'match': '<invalid-pattern>',
            })
            by_capability.setdefault(capability_key, [])
            if rule_id not in by_capability[capability_key]:
                by_capability[capability_key].append(rule_id)
            continue
        seen = set()
        count = 0
        position = 0
        while True:
            match = regex.search(stripped, position)
            if match is None:
                break
            match_index = match.start()
            position = match_index + max(1, len(match.group(0)))
            if match_index in seen:
                break
            seen.add(match_index)
            findings.append({
                'id': rule_id,
                'capability': capability,
                'severity': severity,
                'line': _line_of(stripped, match_index),
                'match': _js_slice(match.group(0), 80),
            })
            by_capability.setdefault(capability_key, [])
            if rule_id not in by_capability[capability_key]:
                by_capability[capability_key].append(rule_id)
            count += 1
            if count >= limit:
                break
    return {
        'findings': findings,
        'by_capability': by_capability,
        'stripped_lines': len(stripped.split('\n')),
    }


# ══════════════════════════════════════════════════════════════════════
# 6. Manifest 解析与校验
# ══════════════════════════════════════════════════════════════════════


def parse_manifest(raw) -> dict:
    """只做 JSON 层解析；返回 ``{ok, error, manifest[, detail]}``，不抛异常。"""
    if raw is UNDEFINED or raw is None:
        return {'ok': False, 'error': 'manifest_missing', 'manifest': None}
    try:
        manifest = json.loads(_js_string(raw), parse_constant=_reject_json_constant)
    except Exception as err:  # noqa: BLE001 - 任何解析错误都归为 manifest_json_error
        detail = _js_slice('%s: %s' % (type(err).__name__, err), 120)
        return {'ok': False, 'error': 'manifest_json_error', 'detail': detail,
                'manifest': None}
    if not isinstance(manifest, dict):
        return {'ok': False, 'error': 'manifest_not_object', 'manifest': None}
    return {'ok': True, 'error': None, 'manifest': manifest}


_ENTRY_DRIVE_RE = re.compile(r'^[A-Za-z]:')
_ENTRY_DOT_SLASH_RE = re.compile(r'^\./')
_DEFAULT_EXPORT_PATTERN = '^[A-Za-z_$][A-Za-z0-9_$]{0,63}$'


def validate_manifest(manifest, policy=None) -> list:
    """校验 manifest 语义；返回错误数组 ``[{code, detail}]``，空数组 = 合法。"""
    policy = POLICY if policy is None else policy
    errors = []

    def push(code: str, detail: str = '') -> None:
        errors.append({'code': code, 'detail': detail})

    if not isinstance(manifest, dict):
        push('manifest_invalid', 'not_an_object')
        return errors
    manifest_rules = policy.get('manifest_rules') if isinstance(policy, dict) else None
    if not isinstance(manifest_rules, dict):
        manifest_rules = {}
    policy_manifest_version = policy.get('manifest_version') if isinstance(policy, dict) else UNDEFINED

    if manifest.get('manifest_version', UNDEFINED) != policy_manifest_version:
        push('manifest_invalid', 'manifest_version')

    manifest_id = manifest.get('id', UNDEFINED)
    if not isinstance(manifest_id, str) or not _regex_test(
            manifest_rules.get('id_pattern', UNDEFINED), manifest_id):
        push('manifest_invalid', 'id')

    version = manifest.get('version', UNDEFINED)
    if not isinstance(version, str) or not _regex_test(
            manifest_rules.get('version_pattern', UNDEFINED), version):
        push('manifest_invalid', 'version')

    entry = manifest.get('entry', UNDEFINED)
    if not isinstance(entry, str) or not _regex_test(
            manifest_rules.get('entry_pattern', UNDEFINED), entry):
        push('manifest_invalid', 'entry')
    else:
        norm = _ENTRY_DOT_SLASH_RE.sub('', entry, count=1)
        segments = norm.split('/')
        forbid_traversal = _truthy(manifest_rules.get('forbid_parent_traversal', UNDEFINED))
        if forbid_traversal and ('..' in segments or norm.startswith('/')
                                 or _ENTRY_DRIVE_RE.match(norm)):
            push('manifest_invalid', 'entry_traversal')

    exports_list = _as_string_array(manifest.get('exports', UNDEFINED))
    max_exports = _js_number(manifest_rules.get('max_exports', UNDEFINED))
    if exports_list is None or len(exports_list) < 1 or len(exports_list) > max_exports:
        push('manifest_invalid', 'exports')
    else:
        export_pattern = manifest_rules.get('export_pattern', UNDEFINED)
        if not _truthy(export_pattern):
            export_pattern = _DEFAULT_EXPORT_PATTERN
        if not all(_regex_test(export_pattern, name) for name in exports_list):
            push('manifest_invalid', 'export_name')
        if len(set(exports_list)) != len(exports_list):
            push('manifest_invalid', 'export_duplicate')

    capabilities = manifest.get('capabilities', UNDEFINED)
    if not isinstance(capabilities, dict):
        # JS: !caps || typeof caps !== 'object' || Array.isArray(caps)
        push('manifest_invalid', 'capabilities')
    else:
        required = _as_string_array(capabilities.get('required', UNDEFINED))
        optional_raw = capabilities.get('optional', UNDEFINED)
        optional = [] if optional_raw is UNDEFINED else _as_string_array(optional_raw)
        if required is None:
            push('manifest_invalid', 'capabilities_required')
        if optional is None:
            push('manifest_invalid', 'capabilities_optional')
        requested = []
        requested_set = set()
        for capability in (required or []) + (optional or []):
            if capability not in requested_set:
                requested_set.add(capability)
                requested.append(capability)
        known = []
        policy_caps = policy.get('capabilities') if isinstance(policy, dict) else None
        if isinstance(policy_caps, dict) and isinstance(policy_caps.get('known'), list):
            known = policy_caps['known']
        for capability in requested:
            if capability not in known:
                push('manifest_invalid', 'unknown_capability:%s' % capability)
        # 声明了 fs 能力就必须给出作用域，否则等于“要权限但不说要哪”——fail-closed
        fs_value = manifest.get('fs', UNDEFINED)
        fs_section = fs_value if isinstance(fs_value, dict) else {}
        read_scopes = _as_string_array(fs_section.get('read', UNDEFINED)) or []
        write_scopes = _as_string_array(fs_section.get('write', UNDEFINED)) or []
        if 'fs-read' in requested_set and not read_scopes:
            push('manifest_invalid', 'fs_scope_missing:fs-read')
        if 'fs-write' in requested_set and not write_scopes:
            push('manifest_invalid', 'fs_scope_missing:fs-write')
        for scope in read_scopes + write_scopes:
            if not scope or _path_is_absolute(scope) or _ENTRY_DRIVE_RE.match(scope):
                push('manifest_invalid', 'fs_scope_absolute')
            elif '..' in scope.split('/'):
                push('manifest_invalid', 'fs_scope_traversal')

    if _truthy(manifest_rules.get('require_integrity', UNDEFINED)):
        integrity = manifest.get('integrity', UNDEFINED)
        if not isinstance(integrity, dict) or not isinstance(
                integrity.get('entry_sha256', UNDEFINED), str):
            push('manifest_invalid', 'integrity_missing')
        elif not _is_sha256_hex(integrity['entry_sha256']):
            push('manifest_invalid', 'integrity_format')

    sandbox_value = manifest.get('sandbox', UNDEFINED)
    if sandbox_value is not UNDEFINED:
        allowed_sandbox = []
        if isinstance(manifest_rules.get('allowed_sandbox'), list):
            allowed_sandbox = manifest_rules['allowed_sandbox']
        if not isinstance(sandbox_value, str) or sandbox_value not in allowed_sandbox:
            push('manifest_invalid', 'sandbox_mode')
    return errors


# ══════════════════════════════════════════════════════════════════════
# 7. fs 作用域解析
# ══════════════════════════════════════════════════════════════════════


def resolve_fs_scopes(manifest, plugin_dir, policy=None) -> dict:
    """解析 fs 作用域为绝对路径；返回 ``{read, write, escapes}``。

    ``escapes`` 记录越出插件根目录（含跨盘符 / ``..`` 前缀）的原始 scope 字符串；
    列表顺序保持 manifest 声明顺序。不抛异常。
    """
    del policy  # 签名与 JS 对齐；当前实现不依赖策略表内容
    fs_value = manifest.get('fs') if isinstance(manifest, dict) else UNDEFINED
    fs_section = fs_value if isinstance(fs_value, dict) else {}
    result = {'read': [], 'write': [], 'escapes': []}
    root = _path_resolve(plugin_dir)
    for key in ('read', 'write'):
        scopes = _as_string_array(fs_section.get(key, UNDEFINED)) or []
        for scope in scopes:
            target = _path_resolve_join(root, scope)
            relative = _path_relative(root, target)
            if relative is None or relative.startswith('..') or _path_is_absolute(relative):
                result['escapes'].append(scope)
                continue
            result[key].append(target)
    return result


# ══════════════════════════════════════════════════════════════════════
# 8. 裁决（decide）
# ══════════════════════════════════════════════════════════════════════


def _policy_capability_list(policy, key: str) -> list:
    """读取 ``policy.capabilities[key]``（缺失/损坏 → 空列表，等价 JS 空数组）。"""
    capabilities = policy.get('capabilities') if isinstance(policy, dict) else None
    if isinstance(capabilities, dict) and isinstance(capabilities.get(key), list):
        return capabilities[key]
    return []


def _decision_sandbox(policy) -> dict:
    """``decision.sandbox`` 的四字段（node_flags 由 decide 按需追加）。"""
    sandbox = policy.get('sandbox') if isinstance(policy, dict) else None
    if not isinstance(sandbox, dict):
        sandbox = {}
    return {
        'mode': 'subprocess',
        'ready_timeout_ms': _number_or_none(_js_number(sandbox.get('ready_timeout_ms', UNDEFINED))),
        'call_timeout_ms': _number_or_none(_js_number(sandbox.get('call_timeout_ms', UNDEFINED))),
        'max_old_space_mb': _number_or_none(_js_number(sandbox.get('max_old_space_mb', UNDEFINED))),
    }


def _deny_all_decision() -> dict:
    """内部兜底裁决：任何未预期异常都转成 deny（fail-closed，不向调用方抛）。"""
    failure = {'code': 'sandbox_unavailable', 'detail': '',
               'error_code': ERROR_CODES['sandbox_unavailable']}
    return {
        'policy_version': POLICY_VERSION,
        'namespace': POLICY.get('namespace') if isinstance(POLICY, dict) else None,
        'verdict': 'deny',
        'reason': failure['code'],
        'error_code': failure['error_code'],
        'failures': [failure],
        'manifest_id': None,
        'manifest_version': None,
        'findings': [],
        'exports': [],
        'capabilities': {'requested': [], 'granted': [], 'denied': []},
        'fs_scopes': {'read': [], 'write': [], 'escapes': []},
        'sandbox': {'mode': 'subprocess', 'ready_timeout_ms': None,
                    'call_timeout_ms': None, 'max_old_space_mb': None},
        'fingerprint': _sha256_hex(''),
    }


def decide(inputs, policy=None) -> dict:
    """S-CAP 裁决纯函数；输入/输出字段与 JS ``decide`` 完全同名同值。

    输入键：``manifestPresent / manifestRaw / entryPresent / entrySource /
    entrySha256 / pluginDir / sandboxSupported``。
    输出键：``verdict / reason / error_code / failures[{code, detail, error_code}] /
    manifest_id / manifest_version / findings[{id, capability, severity, line, match}] /
    capabilities{requested, granted, denied} / fs_scopes{read, write, escapes} /
    sandbox{mode, ready_timeout_ms, call_timeout_ms, max_old_space_mb[, node_flags]} /
    fingerprint / policy_version``。
    """
    try:
        return _decide_impl(inputs, policy)
    except Exception:  # noqa: BLE001 - 契约要求：decide 永不抛异常，异常一律 deny
        return _deny_all_decision()


def _decide_impl(inputs, policy) -> dict:
    policy = POLICY if policy is None else policy
    source = inputs if isinstance(inputs, dict) else {}
    failures = []

    def add(reason: str) -> None:
        error_code = ERROR_CODES.get(reason) or 'SC-EX'
        if not any(item['code'] == reason for item in failures):
            failures.append({'code': reason, 'detail': '', 'error_code': error_code})

    manifest_present = _truthy(source.get('manifestPresent', UNDEFINED))
    manifest_raw = source.get('manifestRaw', UNDEFINED)
    entry_present = _truthy(source.get('entryPresent', UNDEFINED))
    entry_source_value = source.get('entrySource', UNDEFINED)
    entry_source = _js_string(entry_source_value) if _truthy(entry_source_value) else ''
    entry_sha256_value = source.get('entrySha256', UNDEFINED)
    entry_sha256 = (_js_string(entry_sha256_value).lower()
                    if _truthy(entry_sha256_value) else '')
    plugin_dir_value = source.get('pluginDir', UNDEFINED)
    plugin_dir = _js_string(plugin_dir_value) if _truthy(plugin_dir_value) else '.'
    sandbox_supported = _truthy(source.get('sandboxSupported', UNDEFINED))

    if not sandbox_supported:
        add('sandbox_unavailable')
    if not manifest_present:
        add('manifest_missing')

    manifest = None
    if manifest_present:
        parsed = parse_manifest(manifest_raw)
        if not parsed['ok']:
            add('manifest_unreadable')
        else:
            manifest = parsed['manifest']

    if manifest is not None:
        if validate_manifest(manifest, policy):
            add('manifest_invalid')

    manifest_rules = policy.get('manifest_rules') if isinstance(policy, dict) else None
    if not isinstance(manifest_rules, dict):
        manifest_rules = {}
    if not entry_present:
        add('entry_missing')
    elif _utf8_len(entry_source) > _js_number(manifest_rules.get('max_entry_bytes', UNDEFINED)):
        add('entry_too_large')

    # 完整性：缺失哈希已在 validate_manifest 里算 manifest_invalid；这里只比对漂移
    if manifest is not None:
        integrity = manifest.get('integrity', UNDEFINED)
        if isinstance(integrity, dict):
            declared_sha = integrity.get('entry_sha256', UNDEFINED)
            if isinstance(declared_sha, str) and _is_sha256_hex(declared_sha):
                if entry_sha256 != declared_sha.lower():
                    add('integrity_mismatch')

    if manifest is not None:
        scopes = resolve_fs_scopes(manifest, plugin_dir, policy)
    else:
        scopes = {'read': [], 'write': [], 'escapes': []}
    if scopes['escapes']:
        add('fs_scope_escape')

    requested = []
    if manifest is not None:
        capabilities = manifest.get('capabilities', UNDEFINED)
        if isinstance(capabilities, dict):
            combined = []
            for cap_list in (_as_string_array(capabilities.get('required', UNDEFINED)) or [],
                             _as_string_array(capabilities.get('optional', UNDEFINED)) or []):
                combined.extend(cap_list)
            seen = set()
            for capability in combined:
                if capability not in seen:
                    seen.add(capability)
                    requested.append(capability)
    known_capabilities = _policy_capability_list(policy, 'known')
    never_grantable = _policy_capability_list(policy, 'never_grantable')
    grantable = _policy_capability_list(policy, 'grantable')
    for capability in requested:
        if capability not in known_capabilities:
            add('unknown_capability')
        if capability in never_grantable:
            add('forbidden_capability')

    scan = scan_source(entry_source, policy)
    declared = set(requested)
    if any(finding.get('capability') not in declared for finding in scan['findings']):
        add('undeclared_usage')

    # 按策略 precedence 排序 failures；reason = 第一条（最高优先级）
    decision_cfg = policy.get('decision') if isinstance(policy, dict) else None
    if not isinstance(decision_cfg, dict):
        decision_cfg = {}
    precedence = decision_cfg.get('precedence')
    if not isinstance(precedence, (list, tuple)):
        precedence = []

    def failure_rank(item: dict) -> int:
        try:
            return precedence.index(item['code'])
        except ValueError:
            return 999

    failures.sort(key=failure_rank)
    first = failures[0] if failures else None
    verdicts = decision_cfg.get('verdicts') if isinstance(decision_cfg.get('verdicts'), dict) else {}
    blocker_verdict = verdicts.get('blocker')
    clean_verdict = verdicts.get('clean')
    granted = [capability for capability in requested if capability in grantable]
    denied = [capability for capability in requested if capability not in granted]

    decision = {
        'policy_version': POLICY_VERSION,
        'namespace': policy.get('namespace') if isinstance(policy, dict) else None,
        'verdict': (_js_string(blocker_verdict) if _truthy(blocker_verdict) else 'deny')
                   if first else
                   (_js_string(clean_verdict) if _truthy(clean_verdict) else 'allow_sandboxed'),
        'reason': first['code'] if first else 'ok',
        'error_code': first['error_code'] if first else None,
        'failures': failures,
        'manifest_id': manifest.get('id') if manifest is not None
                       and isinstance(manifest.get('id'), str) else None,
        'manifest_version': manifest.get('version') if manifest is not None
                            and isinstance(manifest.get('version'), str) else None,
        'findings': scan['findings'],
        'exports': (list(manifest['exports'])
                    if manifest is not None and isinstance(manifest.get('exports'), list)
                    else []),
        'capabilities': {'requested': requested, 'granted': granted, 'denied': denied},
        'fs_scopes': scopes,
        'sandbox': _decision_sandbox(policy),
        'fingerprint': _sha256_hex('' if manifest_raw is UNDEFINED or manifest_raw is None
                                   else _js_string(manifest_raw)),
    }
    if manifest is not None:
        entry_value = manifest.get('entry', UNDEFINED)
        entry_norm = _ENTRY_DOT_SLASH_RE.sub('', _js_string(entry_value), count=1)
        entry_path = _path_join(_path_resolve(plugin_dir), entry_norm)
        decision['sandbox']['node_flags'] = build_node_flags(
            plugin_dir=plugin_dir,
            entry_path=entry_path,
            read_scopes=scopes['read'],
            write_scopes=scopes['write'] if 'fs-write' in granted else [],
            policy=policy,
        )
    return decision


# ══════════════════════════════════════════════════════════════════════
# 9. 沙箱子进程参数
# ══════════════════════════════════════════════════════════════════════


def build_node_flags(plugin_dir, entry_path=None, read_scopes=None,
                     write_scopes=None, policy=None) -> list:
    """把已授予能力翻译成 Node permission model 的子进程参数。

    关键：插件自身目录必须可读（模块加载需要），``fs-read`` 的“额外读范围”才来自
    manifest；entry 文件本身已在 pluginDir 的 ``--allow-fs-read`` 里，``entry_path``
    仅用于断言（不上 flag，与 JS 一致）。输出顺序与 JS 相同。
    """
    del entry_path  # 与 JS 一致：仅用于断言，不生成 flag
    policy = POLICY if policy is None else policy
    sandbox_cfg = policy.get('sandbox') if isinstance(policy, dict) else None
    if not isinstance(sandbox_cfg, dict):
        sandbox_cfg = {}
    permission_flag = sandbox_cfg.get('node_permission_flag', UNDEFINED)
    permission_flag = _js_string(permission_flag) if _truthy(permission_flag) else '--permission'
    resolved_plugin_dir = _path_resolve(plugin_dir)
    flags = [
        permission_flag,
        '--no-warnings',
        '--no-addons',
        '--disable-proto=throw',
        '--disallow-code-generation-from-strings',
        '--max-old-space-size=%s' % _js_number_str(
            sandbox_cfg.get('max_old_space_mb', UNDEFINED)),
        '--allow-fs-read=%s' % resolved_plugin_dir,
    ]
    read_set = []
    seen_read = set()
    for scope in (read_scopes or []):
        resolved = _path_resolve(scope)
        if resolved not in seen_read:
            seen_read.add(resolved)
            read_set.append(resolved)
    for resolved in read_set:
        if resolved != resolved_plugin_dir:
            flags.append('--allow-fs-read=%s' % resolved)
    seen_write = set()
    for scope in (write_scopes or []):
        resolved = _path_resolve(scope)
        if resolved in seen_write:
            continue
        seen_write.add(resolved)
        flags.append('--allow-fs-write=%s' % resolved)
    return flags


# ══════════════════════════════════════════════════════════════════════
# 10. 审计/告警文本
# ══════════════════════════════════════════════════════════════════════


def render_alert(decision) -> str:
    """供审计/告警文本使用；不含用户隐私。非 deny 返回空串。"""
    if not isinstance(decision, dict) or decision.get('verdict') != 'deny':
        return ''
    manifest_id = decision.get('manifest_id')
    manifest_id_text = _js_string(manifest_id) if _truthy(manifest_id) else '<unknown>'
    error_code = decision.get('error_code')
    reason = decision.get('reason')
    return ('[供应链接管] 插件 %s 被拒绝（%s %s）：'
            '未加载、未注册任何钩子（fail-closed）。'
            % (manifest_id_text, _js_string(error_code), _js_string(reason)))


# ══════════════════════════════════════════════════════════════════════
# 11. CLI（跨语言 parity 用）
# ══════════════════════════════════════════════════════════════════════


def _cli_view(case_id, case_input) -> dict:
    """把完整 decision 压成 CLI 输出契约要求的字段（flags_len 便于跨语言比较）。"""
    decision = decide(case_input)
    sandbox = decision.get('sandbox') if isinstance(decision.get('sandbox'), dict) else {}
    node_flags = sandbox.get('node_flags')
    return {
        'id': case_id,
        'verdict': decision.get('verdict'),
        'reason': decision.get('reason'),
        'error_code': decision.get('error_code'),
        'failures': [item.get('code') for item in decision.get('failures') or []],
        'findings': [
            {'id': item.get('id'), 'capability': item.get('capability'),
             'line': item.get('line')}
            for item in decision.get('findings') or []
        ],
        'capabilities': decision.get('capabilities'),
        'fs_scopes': decision.get('fs_scopes'),
        'exports': decision.get('exports'),
        'sandbox': {
            'mode': sandbox.get('mode'),
            'flags_len': len(node_flags) if isinstance(node_flags, list) else 0,
        },
        'fingerprint': decision.get('fingerprint'),
    }


def _run_cases(cases) -> list:
    results = []
    if not isinstance(cases, (list, tuple)):
        return results
    for item in cases:
        if not isinstance(item, dict):
            continue
        results.append(_cli_view(item.get('id'), item.get('input')))
    return results


def _write_stdout(text: str) -> None:
    data = (text + '\n').encode('utf-8')
    stream = getattr(sys.stdout, 'buffer', None)
    if stream is not None:
        stream.write(data)
        stream.flush()
    else:  # pragma: no cover - 测试桩
        sys.stdout.write(text + '\n')


def main(argv=None) -> int:
    """``python engine/supply_chain.py --cases <file.json>``：读用例、写 JSON 数组。"""
    args = list(sys.argv[1:] if argv is None else argv)
    cases_path = None
    index = 0
    while index < len(args):
        arg = args[index]
        if arg == '--cases' and index + 1 < len(args):
            cases_path = args[index + 1]
            index += 2
            continue
        if arg.startswith('--cases='):
            cases_path = arg.split('=', 1)[1]
            index += 1
            continue
        if arg in ('-h', '--help'):
            _write_stdout('usage: python engine/supply_chain.py --cases <cases.json>')
            return 0
        sys.stderr.write('unknown argument: %s\n' % arg)
        return 2
    if not cases_path:
        sys.stderr.write('usage: python engine/supply_chain.py --cases <cases.json>\n')
        return 2
    try:
        with open(cases_path, 'r', encoding='utf-8') as handle:
            cases = json.loads(handle.read(), parse_constant=_reject_json_constant)
    except Exception as err:  # noqa: BLE001 - 输入不可读/不可解析：输出空数组，保持可脚本化
        sys.stderr.write('failed to read cases: %s\n' % err)
        _write_stdout('[]')
        return 1
    _write_stdout(json.dumps(_run_cases(cases), ensure_ascii=False,
                             separators=(',', ':')))
    return 0


__all__ = [
    'FALLBACK_POLICY', 'POLICY', 'POLICY_SOURCE', 'POLICY_DEGRADED', 'POLICY_VERSION',
    'POLICY_DIGEST', 'MANIFEST_VERSION', 'ERROR_CODES', 'load_policy',
    'strip_comments', 'scan_source', 'parse_manifest', 'validate_manifest',
    'resolve_fs_scopes', 'build_node_flags', 'decide', 'render_alert',
]


if __name__ == '__main__':  # pragma: no cover - CLI 入口
    sys.exit(main())
