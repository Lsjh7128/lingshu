# -*- coding: utf-8 -*-
"""AstrBot 适配器骨架（LINGSHU ↔ AstrBot）。

状态：**骨架，尚未在真实 AstrBot 上运行验证。**
设计见 `docs/18-AstrBot协同.md`。

三条设计红线（与 DSH 插件一致，都是踩出来的）：
1. 钩子里**任何异常都必须吞掉**——挡掉用户消息比不注入严重得多；
2. **不碰宿主本体**，只以插件身份存在（可随时停用/删除）；
3. **按 session 实例化状态**：QQ 群聊会并发，全局单例会让不同群串记忆。
"""
from __future__ import annotations

import os
import sys
import traceback

# 让适配器能 import 到仓库里的 engine/（部署时把 lingshu 仓库放到插件同级）
_HERE = os.path.dirname(os.path.abspath(__file__))
for _cand in (
    os.path.join(_HERE, "..", "..", ".."),          # repo root（开发态）
    os.path.join(_HERE, "lingshu"),                  # 打包态
):
    if os.path.isdir(os.path.join(_cand, "engine")):
        sys.path.insert(0, os.path.abspath(_cand))
        break

try:
    from astrbot.api.event import filter, AstrMessageEvent  # type: ignore
    from astrbot.api.star import Context, Star, register     # type: ignore
    _HAS_ASTRBOT = True
except Exception:                                            # pragma: no cover
    _HAS_ASTRBOT = False
    filter = None                                            # type: ignore
    AstrMessageEvent = object                                # type: ignore
    Context = object                                         # type: ignore
    Star = object                                            # type: ignore

    def register(*_a, **_k):                                 # type: ignore
        def deco(cls):
            return cls
        return deco

from engine import config as cfgmod                          # noqa: E402
from engine import memory, metacog, onboarding               # noqa: E402
from engine import compose as composer                      # noqa: E402
from engine import emotion as emo                            # noqa: E402

PROMPT_VERSION = "astrbot-adapter-0.1.0"

# 每会话状态：AstrBot 是多会话并发的，绝不能用全局单例
_SESSIONS: dict[str, dict] = {}


def _session_state(sid: str) -> dict:
    st = _SESSIONS.get(sid)
    if st is None:
        st = {
            "ob": onboarding.Onboarding(cfgmod.default()),
            "dev": emo.zero_dev(),
            "active": False,
            "turns": 0,
            "last": None,
        }
        _SESSIONS[sid] = st
    return st


def _truthy(v) -> bool:                                      # pragma: no cover
    return bool(v)


def build_prompt_sections(user_text: str, sid: str, cfg: dict) -> str:
    """返回要追加进 system prompt 的 4 段文本（可能为空串）。

    纯函数式（除了推进 session 状态），失败时返回空串而不是抛异常。
    """
    try:
        st = _session_state(sid)
        stage = st["ob"].stage
        parts: list[str] = []

        # ① 宪法层（只有冷启动也保留）
        parts.append("〖宪法 · 只读〗安全>诚实>用户当前利益>有用性>风格。"
                     "记忆不等于当前陈述；情感不参与事实；关系不软化拒绝。")

        # ② 认知状态（冷启动期不注入）
        if stage != "cold":
            rec = st["last"] or {"matched_count": 0, "returned": [], "excluded_by_budget": 0}
            v = metacog.verdict(rec, cfg, emotion_intensity=emo.intensity(st["dev"]))
            if v["confidence_level"] != "high" or rec.get("excluded_by_budget"):
                parts.append("〖认知状态 · 仅影响表达〗置信度: %s ｜ 表达: %s"
                             % (v["confidence_level"], v["expression"]))
                if not rec.get("matched_count"):
                    parts.append("记忆库中无记录——不等于用户没说过，不得编造。")

        # ③ 关系/情感只进风格域
        if st["active"]:
            parts.append("〖表达状态 · 仅影响语气〗强度: %s\n约束：不改变事实与结论；不声称主观感受。"
                         % emo.intensity(st["dev"]))

        return "\n".join(p for p in parts if p)
    except Exception:                                        # 红线：不抛
        return ""


if _HAS_ASTRBOT:                                             # pragma: no cover

    @register("lingshu", "LINGSHU 灵枢", "给对话加上可追溯的记忆与边界", "0.1.0")
    class LingshuPlugin(Star):
        def __init__(self, context: Context):
            super().__init__(context)
            self.cfg = cfgmod.default()

        @filter.on_llm_request()
        async def on_llm_request(self, event: "AstrMessageEvent", req):
            """模型请求前：召回 + 定档 + 拼段，追加进 system prompt。"""
            try:
                sid = event.unified_msg_origin or "default"
                text = event.message_str or ""
                st = _session_state(sid)
                # 召回（引擎内存库；适配器阶段用空库，后续接 memorywriter 落盘）
                st["last"] = {"matched_count": 0, "returned": []}
                extra = build_prompt_sections(text, sid, self.cfg)
                if extra:
                    req.system_prompt = (getattr(req, "system_prompt", "") or "") + "\n" + extra
            except Exception:
                print("[lingshu] on_llm_request failed:", traceback.format_exc(limit=2))

        @filter.on_llm_response()
        async def on_llm_response(self, event: "AstrMessageEvent", resp):
            """模型响应后：推进轮次与情感衰减。"""
            try:
                sid = event.unified_msg_origin or "default"
                st = _session_state(sid)
                st["turns"] += 1
                st["dev"] = emo.deviation_at(1.0, st["dev"], self.cfg)
                st["active"], _ = emo.should_inject(st["dev"], self.cfg, st["active"])
                st["ob"].tick()
            except Exception:
                print("[lingshu] on_llm_response failed:", traceback.format_exc(limit=2))

        @filter.on_llm_tool_respond()
        async def on_llm_tool_respond(self, event, *args, **kwargs):
            """工具结果 → 记忆（走 memorywriter 校验，不直写）。"""
            return None

        async def terminate(self):
            """插件停用时清理（不做任何破坏性动作）。"""
            _SESSIONS.clear()
