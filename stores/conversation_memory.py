"""
对话记忆 — 让 Bot 能"记住"之前和用户聊过什么，自然引用。

轻量设计：
- 每个群/私聊维护一个话题列表
- 话题自动提取关键信息（用简单 NLP）
- 当用户再次提起时，Bot 可以自然回忆
"""
from __future__ import annotations

import time
import re
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class TopicNode:
    topic: str            # 话题关键词
    summary: str          # 简短摘要
    last_mentioned: float # 最后提及时间戳
    importance: int       # 重要性 1-5
    mention_count: int = 1


class ConversationMemory:
    """单聊/群聊的话题记忆"""

    def __init__(self, max_topics: int = 10):
        self._topics: list[TopicNode] = []
        self._max = max_topics

    def add_topic(self, topic: str, summary: str):
        """记录一个新话题或更新已有话题"""
        topic = (topic or "").strip().lower()
        summary = (summary or "").strip()
        if not topic or not summary:
            return

        existing = next((t for t in self._topics if t.topic == topic), None)
        if existing:
            existing.summary = summary[:200]
            existing.last_mentioned = time.time()
            existing.mention_count += 1
            existing.importance = min(5, existing.importance + 1)
        else:
            self._topics.append(TopicNode(
                topic=topic,
                summary=summary[:200],
                last_mentioned=time.time(),
                importance=1,
            ))

        # 按重要性排序，保留最重要的
        self._topics.sort(key=lambda t: (t.importance, t.mention_count), reverse=True)
        self._topics = self._topics[:self._max]

    def recall_hint(self, current_message: str) -> Optional[str]:
        """
        检查当前消息是否触及之前的话题，生成"回忆提示"。

        返回 None 表示无需回忆，否则返回可注入 system prompt 的提示。
        """
        msg = (current_message or "").lower()
        if len(msg) < 4:
            return None

        now = time.time()
        for t in self._topics:
            # 话题时间太久的不回忆（超过 4 小时）
            if now - t.last_mentioned > 14400:
                continue
            # 话题关键词出现在当前消息中
            if t.topic in msg or any(w in msg for w in t.topic.split()):
                hours_ago = int((now - t.last_mentioned) / 60)
                time_hint = f"{hours_ago} 分钟前" if hours_ago < 60 else f"{hours_ago // 60} 小时前"
                return (
                    f"你和对方{time_hint}聊过「{t.topic}」（{t.summary[:80]}），"
                    f"如果相关可以自然提到，让对方感觉你记得。但不要生硬地说'之前聊过'。"
                )
        return None

    def get_recent_hint(self) -> Optional[str]:
        """获取最近一次话题的提示（用于 idle topic）"""
        if not self._topics:
            return None
        now = time.time()
        for t in self._topics:
            if now - t.last_mentioned < 3600:
                return f"最近聊过「{t.topic}」：{t.summary[:80]}"
        return None


# ── 全局管理 ──────────────────────────────

_MAX_CHAT_MEMORIES = 1000
_memories: OrderedDict[int, ConversationMemory] = OrderedDict()


def get_memory(chat_id: int) -> ConversationMemory:
    if chat_id not in _memories:
        _memories[chat_id] = ConversationMemory()
        if len(_memories) > _MAX_CHAT_MEMORIES:
            _memories.popitem(last=False)
    else:
        _memories.move_to_end(chat_id)
    return _memories[chat_id]


# ── 简单话题提取 ──────────────────────────

COMMON_TOPICS = [
    "代码", "编程", "Python", "AI", "模型", "机器人",
    "游戏", "动漫", "音乐", "电影", "美食", "旅行",
    "工作", "学习", "考试", "面试", "健身", "养猫",
    "天气", "心情", "感情", "朋友", "家庭", "睡觉",
]

TOPIC_PATTERNS = [
    (r"(?:在学|在写|在搞|在玩|在看)(.+?)(?:呢|啊|哦|吗|的|$)", 1),
    (r"(?:喜欢|讨厌|想|要|打算)(.+?)(?:呢|啊|哦|吗|的|$)", 1),
    (r"(?:最近|今天|昨天)(?:在)?(.+?)(?:呢|啊|哦|吗|的|$)", 1),
]


def extract_topic(text: str) -> Optional[tuple[str, str]]:
    """从消息中提取话题关键词和摘要"""
    text = (text or "").strip()
    if len(text) < 6:
        return None

    # 先用正则提取
    for pattern, group in TOPIC_PATTERNS:
        m = re.search(pattern, text)
        if m:
            topic = m.group(group).strip()[:10]
            summary = text[:100]
            return topic, summary

    # 再用关键词匹配
    for kw in COMMON_TOPICS:
        if kw.lower() in text.lower():
            return kw, text[:100]

    return None
