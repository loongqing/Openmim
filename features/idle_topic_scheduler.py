"""Idle-group topic seeding.

This scheduler scans whitelisted chats that have been quiet for the configured idle window and
sends one short, anonymized opener based on the chat's bias (tech/life/etc.).
When possible it borrows inspiration from other chats, but only through abstract
bias labels and safe templates — no raw text, names, URLs, IDs, or quoted lines.
"""
from __future__ import annotations

import asyncio
import logging
import random
import re
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from typing import Optional

from telegram.error import TelegramError, RetryAfter

from app_config.config import (
    IDLE_TOPIC_IDLE_HOURS,
    IDLE_TOPIC_SCAN_INTERVAL_SECONDS,
    IDLE_TOPIC_MAX_PER_RUN,
)
from stores.context_manager import ContextMessage
from stores.group_activity_store import get_activity_store
from stores.focus_store import get_focus_store

logger = logging.getLogger(__name__)
_TASK: asyncio.Task | None = None

# --- Bias keywords: intentionally broad and safe ---
BIAS_KEYWORDS: dict[str, tuple[str, ...]] = {
    "tech": (
        "代码", "编程", "开发", "bug", "报错", "模型", "api", "接口", "数据库", "服务器", "部署", "性能",
        "python", "java", "go", "rust", "docker", "linux", "前端", "后端", "算法", "工具", "效率", "自动化",
        "ai", "prompt", "git", "网络", "配置", "版本",
    ),
    "life": (
        "生活", "日常", "天气", "周末", "散步", "睡觉", "起床", "心情", "旅行", "手机", "电影", "视频",
        "音乐", "健身", "养猫", "养狗", "聊天", "家", "休息", "放松", "衣服", "购物", "记录",
    ),
    "work": (
        "工作", "项目", "任务", "汇报", "会议", "文档", "需求", "排期", "进度", "方案", "协作", "加班",
        "deadline", "老板", "同事", "客户", "上线",
    ),
    "study": (
        "学习", "课程", "考试", "作业", "复习", "笔记", "论文", "老师", "学生", "题目", "知识", "训练",
    ),
    "food": (
        "吃", "美食", "做饭", "饭", "菜", "火锅", "烧烤", "咖啡", "奶茶", "早餐", "午饭", "晚饭", "甜点",
        "馋", "厨房", "口味",
    ),
    "entertainment": (
        "视频", "搞笑", "梗", "电影", "电视剧", "综艺", "动漫", "游戏", "娱乐", "刷到", "好笑", "笑死",
    ),
    "gaming": (
        "游戏", "排位", "开黑", "副本", "抽卡", "段位", "对局", "steam", "主机", "手游", "上分",
    ),
}

BIAS_HOOKS = {
    "tech": "效率小技巧 / 工具",
    "life": "日常小片段 / 视频",
    "work": "把事情拆小 / 更省心",
    "study": "知识点 / 小方法",
    "food": "做法 / 口味",
    "entertainment": "视频 / 梗",
    "gaming": "游戏体验 / 开黑",
    "general": "轻松小内容",
}

INTEREST_KEYWORDS: dict[str, tuple[str, ...]] = {
    "debugging": ("报错", "bug", "修复", "排查", "日志", "失败", "异常", "复现", "崩", "卡住"),
    "tools": ("工具", "效率", "自动化", "脚本", "插件", "配置", "部署", "工作流", "省事"),
    "ai": ("ai", "模型", "prompt", "提示词", "llm", "agent", "机器人", "生成", "推理"),
    "funny": ("搞笑", "好笑", "笑死", "视频", "梗", "离谱", "绷不住", "哈哈"),
    "food": ("吃", "饭", "菜", "火锅", "咖啡", "奶茶", "馋", "甜点"),
    "games": ("游戏", "开黑", "排位", "上分", "副本", "抽卡", "steam"),
}

INTEREST_HOOKS = {
    "debugging": "排查问题的小思路",
    "tools": "省事工具 / 自动化",
    "ai": "AI 和工具的小观察",
    "funny": "视频 / 梗",
    "food": "吃的 / 做法",
    "games": "游戏体验 / 小技巧",
}

OPENERS = {
    "tech": [
        "我刚看到一个和{hook}有关的小内容，思路还挺顺的。",
        "刚刷到一个{hook}的小片段，感觉挺实用。",
        "我刚看到一个把复杂东西讲明白的小内容，挺有意思。",
    ],
    "life": [
        "我刚刷到一个很日常的小视频，莫名觉得挺有趣。",
        "刚看到一个{hook}的小片段，想拿来跟你们聊两句。",
        "我看到个轻松的小内容，突然觉得这个群应该会感兴趣。",
    ],
    "work": [
        "我刚想到一个把事情拆小的方法，感觉挺省心。",
        "刚看到一个和{hook}有关的小思路，挺适合拿来开个话题。",
        "我刚看到一个工作里也能用的小技巧，挺顺手。",
    ],
    "study": [
        "我刚看到一个{hook}的小点子，感觉挺容易记住。",
        "刚刷到一个学习相关的小内容，思路挺清楚。",
        "我看到个能把知识讲明白的小片段，挺适合分享一下。",
    ],
    "food": [
        "我刚看到一个做法，直接有点馋。",
        "刚刷到一个{hook}的小片段，看着就挺香。",
        "我刚看到一个和吃的有关的小内容，突然很想聊聊。",
    ],
    "entertainment": [
        "我刚刷到个视频，真是太好笑了。",
        "刚看到一个{hook}的小片段，差点没绷住。",
        "我刚看到一个挺逗的内容，忍不住想分享一下。",
    ],
    "gaming": [
        "我刚看到一个和{hook}有关的小内容，感觉挺有戏。",
        "刚刷到一个游戏相关的小片段，莫名有点想聊。",
        "我看到个游戏小技巧，挺顺手的。",
    ],
    "general": [
        "我刚看到一个挺有意思的小内容，想听听你们怎么看。",
        "刚刷到一个轻松的小片段，感觉适合拿来聊两句。",
        "我刚看到一个小话题，突然觉得可以丢出来聊聊。",
    ],
}


def _cst_now() -> datetime:
    return datetime.now(timezone(timedelta(hours=8)))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(value: str | None) -> Optional[datetime]:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except Exception:
        return None


def _collect_text(msg: ContextMessage) -> str:
    parts = [msg.sender_name, msg.text or "", msg.caption or "", msg.emoji or ""]
    return " ".join(p for p in parts if p).strip()


def classify_bias(messages: list[ContextMessage]) -> tuple[str, int]:
    scores: Counter[str] = Counter()
    for msg in messages:
        if msg.message_type == "bot":
            continue
        text = _collect_text(msg).lower()
        if not text:
            continue
        for bias, keywords in BIAS_KEYWORDS.items():
            hit_count = sum(1 for kw in keywords if kw in text)
            if hit_count:
                scores[bias] += hit_count
    if not scores:
        return "general", 0
    best_bias, best_score = scores.most_common(1)[0]
    return best_bias, best_score


def _sanitize_public_text(text: str) -> str:
    text = (text or "").strip()
    text = re.sub(r"https?://\S+|www\.\S+|t\.me/\S+", "", text, flags=re.I)
    text = re.sub(r"@[A-Za-z0-9_]+", "", text)
    text = re.sub(r"\b\d{2,}\b", "", text)  # remove identifying numbers
    text = re.sub(r"[`\'\"“”‘’\[\]{}<>《》]", "", text)
    text = re.sub(r"\s+", " ", text)
    text = re.sub(r"[\u0000-\u001f\u007f]+", "", text)
    text = text.replace("。。", "。").replace("！！", "！").replace("??", "?")
    return text[:140].strip()


def _choose_template(bias: str, hook: str | None = None) -> str:
    bias = bias if bias in OPENERS else "general"
    template = random.choice(OPENERS[bias])
    hook = hook or BIAS_HOOKS.get(bias, BIAS_HOOKS["general"])
    return template.format(hook=hook)


def _recent_messages_for_chat(context_mgr, chat_id: int, limit: int = 40) -> list[ContextMessage]:
    """同步包装器，兼容 idle_topic_scheduler 的同步调用。"""
    import asyncio
    try:
        loop = asyncio.get_running_loop()
        import concurrent.futures
        # 在已有事件循环中：创建任务执行
        future = asyncio.run_coroutine_threadsafe(
            context_mgr.get_recent(chat_id, limit), loop
        )
        msgs = future.result(timeout=5)
    except RuntimeError:
        # 没有运行中的事件循环，用 asyncio.run
        msgs = asyncio.run(context_mgr.get_recent(chat_id, limit))
    except Exception:
        return []
    return [m for m in msgs if m.message_type != "bot"]


def _peer_bias(context_mgr, whitelist: set[str], exclude_chat_id: int) -> str | None:
    peer_scores: Counter[str] = Counter()
    for cid_s in whitelist:
        try:
            cid = int(cid_s)
        except Exception:
            continue
        if cid == exclude_chat_id:
            continue
        msgs = _recent_messages_for_chat(context_mgr, cid, 25)
        bias, score = classify_bias(msgs)
        if score > 0 and bias != "general":
            peer_scores[bias] += score
    if not peer_scores:
        return None
    return peer_scores.most_common(1)[0][0]


def detect_interest_hook(messages: list[ContextMessage]) -> tuple[str | None, int]:
    """Return only an abstract interest hook; never returns raw message text."""
    scores: Counter[str] = Counter()
    for msg in messages:
        if msg.message_type == "bot":
            continue
        text = _collect_text(msg).lower()
        if not text:
            continue
        for topic, keywords in INTEREST_KEYWORDS.items():
            hit_count = sum(1 for kw in keywords if kw in text)
            if hit_count:
                scores[topic] += hit_count
    if not scores:
        return None, 0
    topic, score = scores.most_common(1)[0]
    return INTEREST_HOOKS.get(topic), score


def _peer_interest_hook(context_mgr, whitelist: set[str], exclude_chat_id: int) -> tuple[str | None, int]:
    peer_scores: Counter[str] = Counter()
    for cid_s in whitelist:
        try:
            cid = int(cid_s)
        except Exception:
            continue
        if cid == exclude_chat_id:
            continue
        msgs = _recent_messages_for_chat(context_mgr, cid, 25)
        hook, score = detect_interest_hook(msgs)
        if hook and score > 0:
            peer_scores[hook] += score
    if not peer_scores:
        return None, 0
    return peer_scores.most_common(1)[0]


def build_idle_seed(context_mgr, whitelist: set[str], chat_id: int) -> tuple[str, dict]:
    messages = _recent_messages_for_chat(context_mgr, chat_id, 60)
    own_bias, own_score = classify_bias(messages)
    peer_bias = _peer_bias(context_mgr, whitelist, chat_id)

    focus_active = False
    try:
        focus_active = bool(get_focus_store().get(chat_id).active)
    except Exception:
        focus_active = False

    chosen_bias = own_bias
    source = "own"
    # 聚焦群优先保留本群语境；非聚焦群在本群信号弱时才借用其它群的抽象偏好。
    if not focus_active and (own_bias == "general" or own_score < 2):
        if peer_bias:
            chosen_bias = peer_bias
            source = "peer"

    own_hook, own_interest_score = detect_interest_hook(messages)
    peer_hook, peer_interest_score = _peer_interest_hook(context_mgr, whitelist, chat_id)
    own_threshold = 1 if focus_active else 2
    hook = own_hook if own_interest_score >= own_threshold else None
    interest_source = "own_focus" if (hook and focus_active) else ("own" if hook else None)
    if hook is None and peer_hook and peer_interest_score >= 2:
        hook = peer_hook
        interest_source = "peer"
    if hook is None:
        hook = BIAS_HOOKS.get(chosen_bias, BIAS_HOOKS["general"])
        interest_source = "bias"
    text = _choose_template(chosen_bias, hook)
    text = _sanitize_public_text(text)
    if not text:
        text = _sanitize_public_text(random.choice(OPENERS["general"]))
    meta = {
        "own_bias": own_bias,
        "own_score": own_score,
        "peer_bias": peer_bias,
        "chosen_bias": chosen_bias,
        "source": source,
        "interest_source": interest_source,
        "focus_active": focus_active,
    }
    return text, meta


async def _send_seed(application, chat_id: int, text: str):
    bot = application.bot
    try:
        return await bot.send_message(chat_id=chat_id, text=text, disable_notification=True)
    except RetryAfter as e:
        logger.warning(f"Idle seed 触发限流，等待 {e.retry_after}s 后重试 | chat={chat_id}")
        await asyncio.sleep(e.retry_after + 1)
        return await bot.send_message(chat_id=chat_id, text=text, disable_notification=True)


async def generate_idle_topic_seeds(application):
    context_mgr = application.bot_data.get("context_mgr")
    whitelist = application.bot_data.get("whitelist") or set()
    if context_mgr is None or not whitelist:
        return []

    store = get_activity_store()
    now = _utc_now()
    due: list[tuple[datetime, int]] = []
    idle_window = timedelta(hours=IDLE_TOPIC_IDLE_HOURS)

    for cid_s in sorted(whitelist):
        try:
            chat_id = int(cid_s)
        except Exception:
            continue
        try:
            from stores.group_settings_store import get_group_settings
            if get_group_settings(cid_s).get("idle_topic_enabled", "true") == "false":
                continue
        except Exception:
            pass
        state = store.get(chat_id)
        last_user = _parse_ts(state.last_user_at)
        last_seed = _parse_ts(state.last_seed_at)
        if last_user is None:
            continue
        if now - last_user < idle_window:
            continue
        if last_seed is not None and last_seed >= last_user:
            continue
        due.append((last_user, chat_id))

    if not due:
        return []

    # 老旧程度越高越优先
    due.sort(key=lambda x: x[0])
    due = due[: max(1, IDLE_TOPIC_MAX_PER_RUN)]

    sent: list[dict] = []
    for _, chat_id in due:
        try:
            text, meta = build_idle_seed(context_mgr, whitelist, chat_id)
            sent_msg = await _send_seed(application, chat_id, text)
            try:
                from stores.group_activity_store import get_activity_store as _gas
                _gas().mark_seed_sent(chat_id)
                _gas().touch_bot_message(chat_id)
            except Exception:
                logger.exception(f"记录 idle seed 活动失败 | chat={chat_id}")
            try:
                sender_name = getattr(application.bot, "username", None) or "Bot"
                import asyncio as _asyncio
                try:
                    loop = _asyncio.get_running_loop()
                    _asyncio.run_coroutine_threadsafe(
                        context_mgr.append(chat_id, ContextMessage(sender_name=sender_name, text=text, message_type="bot")),
                        loop,
                    )
                except RuntimeError:
                    _asyncio.run(context_mgr.append(chat_id, ContextMessage(sender_name=sender_name, text=text, message_type="bot")))
            except Exception:
                logger.exception(f"追加 idle seed 到上下文失败 | chat={chat_id}")
            logger.info(
                f"🧩 Idle seed 已发送 | chat={chat_id} | bias={meta['chosen_bias']} | source={meta['source']} | interest={meta.get('interest_source')} | focus={meta.get('focus_active')} | text={text}"
            )
            sent.append({"chat_id": chat_id, "text": text, "meta": meta, "message_id": getattr(sent_msg, 'message_id', None)})
        except TelegramError as e:
            logger.warning(f"Idle seed 发送失败 | chat={chat_id} | err={e}")
        except Exception as e:
            logger.exception(f"Idle seed 处理异常 | chat={chat_id} | err={e}")

    return sent


async def _loop(application):
    logger.info("🪄 idle topic loop started")
    while True:
        try:
            await asyncio.sleep(max(60, IDLE_TOPIC_SCAN_INTERVAL_SECONDS))
            await generate_idle_topic_seeds(application)
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception(f"idle topic loop error: {e}")
            await asyncio.sleep(60)


def start_idle_topic_loop(application):
    global _TASK
    if _TASK is not None and not _TASK.done():
        return
    _TASK = asyncio.create_task(_loop(application), name="idle_topic_seed")


async def stop_idle_topic_loop():
    global _TASK
    task = _TASK
    _TASK = None
    if task is not None and not task.done():
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
