"""Telegram Guest Mode 处理桥。

基于 python-telegram-bot 22.8 原生 guest 接口：
- Update.guest_message
- Message.guest_query_id
- Bot.answer_guest_query()

本模块职责：
1. 识别原生 guest_message
2. 调用 PTB 原生 answer_guest_query 发占位消息
3. 复用现有 LLMClient.guest_chat 生成最终回复
4. 通过 PTB 原生 bot.edit_message_text(inline_message_id=...) 覆盖占位消息

当前实现：精简文本版 MVP
- 支持 text / caption
- 支持下载 guest 图片作为视觉输入
- 暂不做进度多次刷新，仅占位一次 + 最终编辑一次
"""
from __future__ import annotations

import asyncio
import base64
import logging
import re
from io import BytesIO

from telegram import Message, Update
from telegram.ext import ContextTypes

import app_config.config as config
from app_config.customization import get_text
from llm.llm_client import get_llm_client
from plugins.manager import get_plugin_manager

logger = logging.getLogger("GuestBridge")

MAX_IMAGE_DOWNLOAD_BYTES = 900_000



def _is_allowed_guest_user(user_id: int | None) -> bool:
    allowed = {str(x) for x in getattr(config, "GUEST_ALLOWED_USER_IDS", set()) if str(x).strip()}
    if not allowed:
        return True
    return user_id is not None and str(user_id) in allowed

def extract_guest_message(update: Update) -> Message | None:
    """从 PTB Update 中提取原生 guest_message。"""
    guest_message = getattr(update, "guest_message", None)
    return guest_message if isinstance(guest_message, Message) else None


def is_guest_update(update: Update) -> bool:
    """判断当前 update 是否为 guest_message。"""
    return extract_guest_message(update) is not None


def _parse_caller_name(message: Message) -> str:
    user = message.from_user
    if user is None:
        return get_text("guest.unknown_user", "未知用户")
    full_name = str(getattr(user, "full_name", "") or "").strip()
    if full_name:
        return full_name
    first_name = str(getattr(user, "first_name", "") or "").strip()
    last_name = str(getattr(user, "last_name", "") or "").strip()
    joined = " ".join([x for x in (first_name, last_name) if x]).strip()
    if joined:
        return joined
    uid = getattr(user, "id", None)
    return str(uid) if uid is not None else get_text("guest.unknown_user", "未知用户")


def _parse_text(message: Message) -> str:
    return str(message.text or message.caption or "").strip()


def _build_reply_context_messages(message: Message) -> list[str]:
    """提取与 guest 当前提问直接相关的 reply 上下文。"""
    context_messages: list[str] = []
    reply_to = getattr(message, "reply_to_message", None)
    if reply_to is None:
        return context_messages

    reply_user = getattr(reply_to, "from_user", None)
    reply_name = (
        str(getattr(reply_user, "full_name", "") or "").strip()
        or str(getattr(reply_user, "first_name", "") or "").strip()
        or (str(getattr(reply_user, "id", "")) if reply_user is not None else get_text("guest.unknown_user", "未知用户"))
    )
    reply_text = str(getattr(reply_to, "text", None) or getattr(reply_to, "caption", None) or "").strip()
    if reply_text:
        context_messages.append(get_text("guest.reply_context", "[被回复消息][来自 {reply_name}] {reply_text}").format(reply_name=reply_name, reply_text=reply_text))
    else:
        context_messages.append(get_text("guest.reply_context_empty", "[被回复消息][来自 {reply_name}] <无文本内容>").format(reply_name=reply_name))
    return context_messages


async def _strip_bot_mention(text: str, context: ContextTypes.DEFAULT_TYPE) -> str:
    text = (text or "").strip()
    if not text:
        return text
    try:
        me = await context.bot.get_me()
        username = str(getattr(me, "username", "") or "").strip()
        if username:
            text = re.sub(rf"@{re.escape(username)}", "", text, flags=re.IGNORECASE).strip()
    except Exception:
        logger.debug("获取 bot username 失败，跳过 guest mention 清洗", exc_info=True)
    return text


def _extract_photo_candidates(message: Message) -> list:
    if message.photo:
        return list(message.photo)
    reply_to = message.reply_to_message
    if reply_to and reply_to.photo:
        return list(reply_to.photo)
    return []


async def _download_photo_from_message(
    message: Message,
    context: ContextTypes.DEFAULT_TYPE,
) -> str | None:
    candidates = _extract_photo_candidates(message)
    if not candidates:
        return None

    try:
        candidates = sorted(
            candidates,
            key=lambda p: (
                (getattr(p, "file_size", 0) or 0),
                (getattr(p, "width", 0) or 0)
                * (getattr(p, "height", 0) or 0),
            ),
        )
        chosen = candidates[0]
        for p in candidates:
            if int(getattr(p, "file_size", 0) or 0) <= MAX_IMAGE_DOWNLOAD_BYTES:
                chosen = p
            else:
                break
        file_id = str(getattr(chosen, "file_id", "") or "").strip()
        if not file_id:
            return None

        tg_file = await context.bot.get_file(file_id)
        buf = BytesIO()
        await tg_file.download_to_memory(out=buf)
        data = buf.getvalue()
        if len(data) > MAX_IMAGE_DOWNLOAD_BYTES:
            logger.warning("guest 图片过大，跳过视觉输入: %s bytes", len(data))
            return None
        b64 = base64.b64encode(data).decode("utf-8")
        logger.info("📸 Guest 图片下载完成 | size=%s | b64=%s", len(data), len(b64))
        return b64
    except Exception:
        logger.exception("下载 guest 图片失败")
        return None


async def process_guest_update(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """处理一次 guest update。命中并接管返回 True，否则 False。"""
    if not config.GUEST_MODE_ENABLED:
        logger.info("🔎 PROBE process_guest_update: GUEST_MODE_ENABLED=False，跳过")
        return False

    guest_message = extract_guest_message(update)
    if guest_message is None:
        return False
    guest_user_id = guest_message.from_user.id if guest_message.from_user else None
    if not _is_allowed_guest_user(guest_user_id):
        logger.info("Guest 跳过未授权用户 | user=%s", guest_user_id)
        return True

    logger.info("🔎 PROBE process_guest_update 命中原生 guest_message，开始处理")

    guest_query_id = str(getattr(guest_message, "guest_query_id", "") or "").strip()
    if not guest_query_id:
        logger.warning("guest_message 缺少 guest_query_id，跳过")
        return True

    caller_name = _parse_caller_name(guest_message)
    context_messages = _build_reply_context_messages(guest_message)
    try:
        text = await _strip_bot_mention(_parse_text(guest_message), context)
    except Exception:
        logger.exception("🔎 PROBE _strip_bot_mention 抛异常，降级用原始文本")
        text = _parse_text(guest_message)
    try:
        image_b64 = await _download_photo_from_message(guest_message, context)
    except Exception:
        logger.exception("🔎 PROBE _download_photo_from_message 抛异常，降级无图片")
        image_b64 = None
    if not text and image_b64:
        text = get_text("guest.image_prompt", "请描述这张图片的内容")
    elif not text:
        text = get_text("guest.default_prompt", "请帮我回答这个 guest 提问")

    logger.info(
        "👻 Guest update | caller=%s | has_image=%s | text=%s",
        caller_name,
        bool(image_b64),
        text[:80],
    )

    try:
        answer = await context.bot.answer_guest_query(
            guest_query_id=guest_query_id,
            result={
                "type": "article",
                "id": "reply",
                "title": "Reply",
                "input_message_content": {
                    "message_text": get_text("guest.placeholder", "🔍 咱正在思考中…"),
                },
            },
        )
        inline_message_id = str(getattr(answer, "inline_message_id", "") or "").strip()
        if not inline_message_id:
            raise RuntimeError("answer_guest_query succeeded but inline_message_id missing")
    except Exception as e:
        logger.exception("🔎 PROBE answer_guest_query 发送占位失败 | err=%r", e)
        return True

    async def _worker() -> None:
        async def _update_status(status: str) -> None:
            try:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=status,
                    parse_mode=None,
                )
            except Exception:
                logger.debug("Guest 进度编辑失败（已忽略）", exc_info=True)

        try:
            llm = get_llm_client()
            reply_text = await llm.guest_chat(
                text,
                caller_name,
                image_base64=image_b64,
                progress_callback=_update_status,
                context_messages=context_messages,
                chat_id=f"guest_{guest_message.from_user.id}" if guest_message.from_user else None,
            )
            if not reply_text:
                reply_text = get_text("guest.empty_reply", "唔…咱暂时想不出怎么回答喵")
        except Exception:
            logger.exception("Guest LLM 异常")
            reply_text = get_text("guest.error_reply", "唔…出了点问题喵，请稍后再试")

        try:
            send_text, entities = await get_plugin_manager().enrich_outgoing_text(reply_text, chat_id=guest_user_id)
            await context.bot.edit_message_text(
                inline_message_id=inline_message_id,
                text=send_text,
                entities=entities,
                parse_mode=None,
            )
            logger.info("✅ Guest reply 已编辑 | len=%s", len(send_text))
        except Exception:
            logger.exception("编辑 Guest reply 失败")
            try:
                await context.bot.edit_message_text(
                    inline_message_id=inline_message_id,
                    text=get_text("guest.error_reply", "唔…出了点问题喵，请稍后再试"),
                    parse_mode=None,
                )
            except Exception:
                logger.exception("Guest 失败兜底编辑也失败")

    context.application.create_task(
        _worker(),
        name=f"guest-reply-{guest_user_id}",
    )
    return True
