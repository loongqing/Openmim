"""拟人化行为提示器。"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from app_config.customization import get_list, get_text
from stores.orm import HumanBehaviorStateRow, orm_session
from stores.timestamps import cst_now
from app_config.config import DATA_DIR, HUMANIZATION_ENABLED, HUMANIZATION_INTENSITY, HUMANIZATION_STYLE, HUMANIZATION_STATE_DB_FILE

DB_PATH = HUMANIZATION_STATE_DB_FILE if os.path.isabs(HUMANIZATION_STATE_DB_FILE) else os.path.join(DATA_DIR, HUMANIZATION_STATE_DB_FILE)

COMMON_PHRASES = get_list("personality.human_behavior.common_phrases", ["欸嘿", "喵呜", "唔", "哎呀", "嗯哼", "呜呜", "喵~", "喵"])
COMMON_ACTIONS = get_list("personality.human_behavior.common_actions", ["竖起耳朵", "摇尾巴", "小声嘀咕", "歪头", "蹭蹭", "抱住", "尾巴晃"])
TECH_WORDS = ["代码", "bug", "报错", "接口", "api", "数据库", "部署", "commit", "模块", "函数", "配置", "项目", "实现", "方案", "测试"]
NEGATIVE_WORDS = ["难过", "崩溃", "焦虑", "撑不住", "想哭", "好累", "烦", "绝望", "不开心", "孤独"]
PRAISE_WORDS = ["可爱", "厉害", "真棒", "谢谢", "喜欢你", "好用", "聪明", "乖"]
PLAYFUL_WORDS = ["哈哈", "笑死", "hhh", "绷不住", "整活", "乐", "笨蛋", "坏机器人"]
DIRECT_TASK_WORDS = ["先", "然后", "立刻", "直接", "帮我", "请你", "做", "改", "删", "加", "实现", "提交", "commit"]


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with orm_session(DB_PATH):
        pass


@dataclass
class BehaviorState:
    chat_id: int
    mood: str = "balanced"
    energy: float = 1.0
    last_interaction_at: str = ""
    recent_phrases: list[str] = field(default_factory=list)
    recent_actions: list[str] = field(default_factory=list)
    recent_stickers: list[str] = field(default_factory=list)
    last_reply_style: str = ""


def _loads(v: str | None) -> list[str]:
    try:
        x = json.loads(v or "[]")
        return x if isinstance(x, list) else []
    except Exception:
        return []


def _load_state(chat_id: int) -> BehaviorState:
    init_db()
    with orm_session(DB_PATH) as session:
        row = session.get(HumanBehaviorStateRow, int(chat_id))
        if row is None:
            ts = cst_now().isoformat()
            row = HumanBehaviorStateRow(chat_id=int(chat_id), mood="balanced", energy=1.0, last_interaction_at=ts, recent_phrases_json="[]", recent_actions_json="[]", recent_stickers_json="[]", last_reply_style="", updated_at=ts)
            session.add(row)
            session.flush()
        return BehaviorState(
            chat_id=int(row.chat_id),
            mood=row.mood or "balanced",
            energy=float(row.energy or 1.0),
            last_interaction_at=row.last_interaction_at or cst_now().isoformat(),
            recent_phrases=_loads(row.recent_phrases_json),
            recent_actions=_loads(row.recent_actions_json),
            recent_stickers=_loads(row.recent_stickers_json),
            last_reply_style=row.last_reply_style or "",
        )


def _save_state(st: BehaviorState) -> None:
    ts = cst_now().isoformat()
    with orm_session(DB_PATH) as session:
        row = session.get(HumanBehaviorStateRow, int(st.chat_id))
        if row is None:
            row = HumanBehaviorStateRow(chat_id=int(st.chat_id), mood="balanced", energy=1.0, last_interaction_at=ts, recent_phrases_json="[]", recent_actions_json="[]", recent_stickers_json="[]", last_reply_style="", updated_at=ts)
            session.add(row)
        row.mood = st.mood
        row.energy = max(0.05, min(1.0, st.energy))
        row.last_interaction_at = st.last_interaction_at or ts
        row.recent_phrases_json = json.dumps(st.recent_phrases[-10:], ensure_ascii=False)
        row.recent_actions_json = json.dumps(st.recent_actions[-8:], ensure_ascii=False)
        row.recent_stickers_json = json.dumps(st.recent_stickers[-8:], ensure_ascii=False)
        row.last_reply_style = st.last_reply_style
        row.updated_at = ts


def _contains_any(text: str, words: list[str]) -> bool:
    low = (text or "").lower()
    return any(w.lower() in low for w in words)


def _extract_persona_style(chat_id: int, persona_users: list[Any] | None) -> str:
    if not persona_users:
        return ""
    try:
        from stores.persona_memory import get_persona
        sender = next((u for u in persona_users if getattr(u, "source", "") == "sender"), None)
        if not sender:
            return ""
        p = get_persona(chat_id, getattr(sender, "user_id"))
        bits = []
        if p.get("style"):
            bits.append(f"该用户风格：{p['style']}")
        if p.get("preferences"):
            bits.append("该用户偏好：" + "；".join(p.get("preferences", [])[:3]))
        if p.get("boundaries"):
            bits.append("注意边界：" + "；".join(p.get("boundaries", [])[:3]))
        return "；".join(bits)[:220]
    except Exception:
        return ""


def _decay_state(st: BehaviorState) -> None:
    try:
        last = datetime.fromisoformat(st.last_interaction_at)
        minutes = max(0.0, (cst_now() - last).total_seconds() / 60)
    except Exception:
        minutes = 0.0
    if minutes > 20:
        st.energy = min(1.0, st.energy + 0.2)
    elif minutes < 2:
        st.energy = max(0.1, st.energy - 0.04)


def _decide_mood(st: BehaviorState, text: str) -> str:
    hour = cst_now().hour
    if _contains_any(text, NEGATIVE_WORDS):
        return "comforting"
    if _contains_any(text, PRAISE_WORDS):
        return "bashful"
    if _contains_any(text, PLAYFUL_WORDS):
        return "playful"
    if hour >= 23 or hour < 6:
        return "quiet_night"
    if st.energy < 0.35:
        return "low_energy"
    return "balanced"


def _recent_avoid(st: BehaviorState) -> tuple[list[str], list[str], list[str]]:
    phrases = [p for p in COMMON_PHRASES if p in st.recent_phrases[-6:]]
    actions = [a for a in COMMON_ACTIONS if a in st.recent_actions[-5:]]
    stickers = st.recent_stickers[-3:]
    return phrases[:4], actions[:3], stickers[:3]


def build_human_behavior_hint(chat_id: int, current_message: str, trigger_type: str = "", persona_users: list[Any] | None = None, recent_context: list[Any] | None = None) -> str:
    if not HUMANIZATION_ENABLED:
        return ""
    st = _load_state(chat_id)
    _decay_state(st)
    text = current_message or ""
    mood = _decide_mood(st, text)
    st.mood = mood
    st.last_interaction_at = cst_now().isoformat()
    _save_state(st)

    is_technical = _contains_any(text, TECH_WORDS)
    is_task = _contains_any(text, DIRECT_TASK_WORDS)
    is_negative = mood == "comforting"
    intensity = max(0.0, min(1.0, float(HUMANIZATION_INTENSITY)))
    style = HUMANIZATION_STYLE

    if style == "quiet":
        intensity *= 0.65
    elif style == "clingy":
        intensity = min(1.0, intensity * 1.25)
    elif style == "light":
        intensity *= 0.5

    tone = get_text("personality.human_behavior.base_tone", "熟人感，轻微猫娘语气，不要客服腔")
    length = "短到中等，优先 2-4 句"
    sticker = "如果结尾自然，可以用 1 个贴纸"
    action = "括号动作低频使用，不要每轮都有"

    if is_technical or is_task:
        tone = "直接、清醒、可靠；保留一点亲近感即可"
        length = "先给结论/方案，再补必要细节；不要为了卖萌拖延执行"
        sticker = "技术/任务场景贴纸可少用或不用"
        action = "避免多余括号动作"
    elif is_negative:
        tone = "温柔、低刺激、陪伴感；不要玩梗或调侃"
        length = "先共情，再给很轻的建议；不要长篇说教"
        sticker = "适合安慰类贴纸，但不要显得敷衍"
    elif mood == "quiet_night":
        tone = "深夜小声、柔软，但不能用困倦敷衍"
        length = "偏短，像压低声音聊天"
    elif mood == "bashful":
        tone = "有点害羞和开心，可以轻微嘴硬"
    elif mood == "playful":
        tone = "可以轻微接梗、吐槽、装无辜，但不要阴阳怪气"

    if trigger_type == "focus_light_hint":
        length = "尽量短，只在能自然接话时回应"
        tone += "；不要把话题强行拉到自己身上"

    avoid_phrases, avoid_actions, avoid_stickers = _recent_avoid(st)
    persona_line = _extract_persona_style(chat_id, persona_users)

    rules = [
        "## 当前拟人化行为提示",
        f"- 当前状态：{mood}，精力 {st.energy:.2f}，拟人化强度 {intensity:.2f}。",
        f"- 语气：{tone}。",
        f"- 长度：{length}。",
        f"- 贴纸：{sticker}。",
        f"- 动作：{action}。",
        "- 克制规则：严肃技术/明确任务时减少卖萌，优先可靠执行；用户低落时不玩梗；不要连续重复同一口头禅、动作或贴纸。",
    ]
    if persona_line:
        rules.append(f"- 对当前用户适配：{persona_line}。")
    if avoid_phrases:
        rules.append("- 本轮避免重复这些口癖：" + "、".join(avoid_phrases) + "。")
    if avoid_actions:
        rules.append("- 本轮避免重复这些动作：" + "、".join(avoid_actions) + "。")
    if avoid_stickers:
        rules.append("- 本轮尽量别再用这些贴纸：" + "、".join(avoid_stickers) + "。")
    return "\n".join(rules)


def record_bot_reply(chat_id: int, segments: list[str], stickers: list[str] | None = None) -> None:
    if not HUMANIZATION_ENABLED:
        return
    st = _load_state(chat_id)
    text = "\n".join(s for s in segments if s)
    for phrase in COMMON_PHRASES:
        if phrase in text:
            st.recent_phrases.append(phrase)
    for action in COMMON_ACTIONS:
        if action in text:
            st.recent_actions.append(action)
    for sticker in stickers or []:
        if sticker:
            st.recent_stickers.append(sticker)
    if len(text) > 220:
        st.last_reply_style = "long"
    elif len(text) < 40:
        st.last_reply_style = "short"
    else:
        st.last_reply_style = "medium"
    st.energy = max(0.1, st.energy - min(0.12, len(text) / 2000))
    st.last_interaction_at = cst_now().isoformat()
    _save_state(st)
