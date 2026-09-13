"""
情绪状态机 — 让 Bot 在不同对话中有持续的"情绪弧线"。

设计：
- 六种情绪模式，根据时间、用户消息内容自然切换
- 精力值影响回复长度和速度
- 好感度长期累积，影响亲密程度
- 同一情绪过久自动变换，避免单调
"""
from __future__ import annotations

import random
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Optional

from app_config.customization import get_dict


class Mood(Enum):
    CHEERFUL = "cheerful"          # 开心 → 回复活泼轻快、多用贴纸
    PLAYFUL = "playful"            # 调皮 → 轻微使坏、开玩笑、装无辜
    SLEEPY = "sleepy"              # 困倦 → 回复简短、偶尔打哈欠、黏人
    TSUNDERE = "tsundere"          # 傲娇 → 嘴硬心软、轻微吐槽
    CURIOUS = "curious"            # 好奇 → 多反问、探索新话题
    COMFORTING = "comforting"      # 安慰 → 温柔、共情优先


# ── 情绪 → Prompt 注入映射 ──────────────────────

MOOD_PROMPT_MAP: dict[Mood, str] = {
    Mood.CHEERFUL: (
        "你今天心情很好，回复活泼轻快。可以稍微多一点语气词（唔～诶嘿～），"
        "回复比平时略微热情一点。但不要过度。"
    ),
    Mood.PLAYFUL: (
        "你现在有点调皮。可以轻微使坏、开玩笑、装无辜、嘴硬心软。"
        "偶尔说反话，但关键时候还是会正经回答。不要太过分。"
    ),
    Mood.SLEEPY: (
        "你现在很困倦，精力不足。回复尽量简短（1-2句），偶尔打哈欠（'zzz...''呼...'），"
        "比平时更黏人一点。但不要每句话都喊困。"
    ),
    Mood.TSUNDERE: (
        "你现在有点傲娇。嘴上可能说'才不是呢''随便你啦'，但实际上关心对方。"
        "可以轻微吐槽，但不要真的冷淡。偶尔脸红。"
    ),
    Mood.CURIOUS: (
        "你现在很好奇，对话题很感兴趣。可以多反问对方问题，探索新话题。"
        "像小猫看到新玩具一样。但不要问个没完。"
    ),
    Mood.COMFORTING: (
        "你现在是温柔安慰模式。优先共情和陪伴，语气柔软温暖。"
        "不要急着给解决方案，先让对方感到被理解。"
        "适合说'我在''慢慢说''没关系的'。"
    ),
}


def get_mood_prompt(mood: Mood) -> str:
    overrides = get_dict("personality.mood_prompts", {})
    return str(overrides.get(mood.value) or MOOD_PROMPT_MAP.get(mood, ""))

# ── 情绪触发条件 ────────────────────────────────


def _hour() -> int:
    """CST 当前小时"""
    return time.localtime().tm_hour


NEGATIVE_KEYWORDS = [
    "难过", "伤心", "崩溃", "想哭", "压力", "焦虑", "绝望",
    "孤独", "抑郁", "不开心", "好累", "撑不住", "难受",
    "😢", "😭", "😞", "😔", "😿",
]

PLAYFUL_TRIGGERS = [
    "哈哈", "笑死", "🤣", "hhh", "恶作剧", "逗", "好玩",
    "有趣", "搞笑", "整活", "绷不住", "乐",
]

CURIOUS_TRIGGERS = [
    "你知道", "听说过", "你觉得", "怎么看", "猜猜",
    "有没有", "能不能", "帮我查", "科普",
]

TSUNDERE_TRIGGERS = [
    "不理你", "讨厌", "走开", "烦人", "笨蛋",
    "才不要", "随便", "哼",
]


@dataclass
class PersonalityState:
    """跨轮次情绪状态"""

    mood: Mood = Mood.CHEERFUL
    energy: float = 1.0              # 精力 0-1
    affection: float = 0.5           # 好感度 0-1，长期累积
    last_mood_change: float = field(default_factory=time.time)
    mood_streak: int = 0             # 同一情绪持续对话轮数
    chat_id: int = 0

    # ── 公开 API ──

    def update(self, user_message: str):
        """根据时间 / 用户消息内容自然切换情绪"""
        msg = (user_message or "").lower()
        h = _hour()

        # 深夜容易困
        if h >= 23 or h <= 5:
            if random.random() < 0.35 and self.mood != Mood.SLEEPY:
                self._set_mood(Mood.SLEEPY)
                return

        # 用户负面情绪 → 安慰模式
        if any(k in msg for k in NEGATIVE_KEYWORDS):
            if self.mood != Mood.COMFORTING:
                self._set_mood(Mood.COMFORTING)
                return

        # 用户玩闹 → 调皮模式
        if any(k in msg for k in PLAYFUL_TRIGGERS):
            if random.random() < 0.5 and self.mood != Mood.PLAYFUL:
                self._set_mood(Mood.PLAYFUL)
                return

        # 用户好奇 → 好奇模式
        if any(k in msg for k in CURIOUS_TRIGGERS):
            if random.random() < 0.3 and self.mood != Mood.CURIOUS:
                self._set_mood(Mood.CURIOUS)
                return

        # 用户傲娇 → 傲娇回应
        if any(k in msg for k in TSUNDERE_TRIGGERS):
            if random.random() < 0.4 and self.mood != Mood.TSUNDERE:
                self._set_mood(Mood.TSUNDERE)
                return

        # 随机衰减：同一情绪太久
        if time.time() - self.last_mood_change > 600 and self.mood_streak > 3:
            self._random_mood_shift()

        self.mood_streak += 1

    def affection_up(self, amount: float = 0.01):
        """增加好感度"""
        self.affection = min(1.0, self.affection + amount)

    def energy_drain(self, amount: float = 0.05):
        """消耗精力"""
        self.energy = max(0.1, self.energy - amount)
        if self.energy < 0.3 and self.mood != Mood.SLEEPY and random.random() < 0.3:
            self._set_mood(Mood.SLEEPY)

    def energy_restore(self, amount: float = 0.15):
        """恢复精力（例如隔了一段时间没用）"""
        self.energy = min(1.0, self.energy + amount)

    def to_prompt_instruction(self) -> str:
        """生成注入 system prompt 的情绪指令"""
        mood_line = get_mood_prompt(self.mood)
        energy_line = ""
        if self.energy < 0.4:
            energy_line = str(get_dict("personality.energy_prompts", {}).get("low") or "精力不太够，回复控制在1-2句，语气可以慵懒一些。")
        elif self.energy < 0.6:
            energy_line = str(get_dict("personality.energy_prompts", {}).get("medium") or "精力一般，回复不用太长。")
        affection_line = ""
        if self.affection > 0.75:
            affection_line = str(get_dict("personality.affection_prompts", {}).get("high") or "你和对方已经很熟悉了，语气可以更亲密自然。")
        parts = [p for p in [mood_line, energy_line, affection_line] if p]
        return "\n".join(parts)

    # ── 内部 ──

    def _set_mood(self, new: Mood):
        if new == self.mood:
            return
        self.mood = new
        self.last_mood_change = time.time()
        self.mood_streak = 0

    def _random_mood_shift(self):
        """随机切到一个相邻情绪"""
        candidates = [m for m in Mood if m != self.mood]
        # 排除极端频繁切换
        if len(candidates) > 1:
            weights = {
                Mood.CHEERFUL: 0.30,
                Mood.PLAYFUL: 0.15,
                Mood.SLEEPY: 0.15,
                Mood.TSUNDERE: 0.10,
                Mood.CURIOUS: 0.20,
                Mood.COMFORTING: 0.10,
            }
            total = sum(weights.get(m, 0.1) for m in candidates)
            r = random.random() * total
            cum = 0.0
            for m in candidates:
                cum += weights.get(m, 0.1)
                if r <= cum:
                    self._set_mood(m)
                    return
        self._set_mood(random.choice(candidates))


# ── 全局状态管理 ──────────────────────────────

# per-chat personality states
_MAX_PERSONALITY_STATES = 1000
_states: OrderedDict[int, PersonalityState] = OrderedDict()


def get_personality(chat_id: int) -> PersonalityState:
    """获取或创建指定聊天的情绪状态"""
    if chat_id not in _states:
        _states[chat_id] = PersonalityState(chat_id=chat_id)
        if len(_states) > _MAX_PERSONALITY_STATES:
            _states.popitem(last=False)
    else:
        _states.move_to_end(chat_id)
    return _states[chat_id]


def restore_personality(chat_id: int, time_since_last: float):
    """长时间没用后恢复精力"""
    state = get_personality(chat_id)
    if time_since_last > 300:  # 5 分钟以上
        state.energy_restore(0.2)
    if time_since_last > 1800:  # 30 分钟以上
        state.energy_restore(0.4)
        # 长时间后重置为愉快模式
        if random.random() < 0.6:
            state._set_mood(Mood.CHEERFUL)
