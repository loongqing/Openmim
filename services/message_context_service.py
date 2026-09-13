"""消息上下文与用户名脱敏服务。

职责：
- 给 LLM 看到的群聊用户名做脱敏
- 将用户消息/Bot 回复写入 ContextManager
- 将 LLM 输出中的脱敏标签恢复成真实显示名

该服务把原本散落在 chat_handler.py 中的上下文持久化细节收敛到一处。
"""

from __future__ import annotations

import hashlib
import re
from collections import OrderedDict
from typing import Callable

from telegram import Message

from app_config.config import (
    CONTEXT_MAX_TEXT_CHARS,
    BOT_CONTEXT_MAX_CHARS,
    MSG_SEPARATOR,
    STICKER_PREFIX,
    STICKER_SUFFIX,
)
from stores.context_manager import ContextMessage
from stores.group_activity_store import get_activity_store


class MessageContextService:
    def __init__(
        self,
        *,
        logger,
        get_context_mgr: Callable[[], object | None],
        extract_text: Callable[[Message], str],
        get_sender_name: Callable[[Message], str],
        is_reply_to_bot: Callable[[Message, str, int], bool],
        is_mention_bot: Callable[[Message, str], bool],
        get_photo_file_id: Callable[[Message], str | None],
    ):
        self._logger = logger
        self._get_context_mgr = get_context_mgr
        self._extract_text = extract_text
        self._get_sender_name = get_sender_name
        self._is_reply_to_bot = is_reply_to_bot
        self._is_mention_bot = is_mention_bot
        self._get_photo_file_id = get_photo_file_id
        self._anon_map: OrderedDict[str, dict[str, str]] = OrderedDict()
        self._max_anon_chats = 500

    @staticmethod
    def anonymize_sender(user_id: int | None, real_name: str) -> str:
        if user_id is None:
            return real_name
        h = hashlib.sha256(str(user_id).encode()).hexdigest()[:4].upper()
        return f"用户_{h}"

    def register_anon(self, chat_id: int, user_id: int | None, real_name: str) -> str:
        if user_id is None:
            return real_name
        label = self.anonymize_sender(user_id, real_name)
        cid = str(chat_id)
        if cid not in self._anon_map:
            self._anon_map[cid] = {}
        else:
            self._anon_map.move_to_end(cid)
        self._anon_map[cid][label] = real_name
        if len(self._anon_map[cid]) > 200:
            oldest = next(iter(self._anon_map[cid]))
            del self._anon_map[cid][oldest]
        if len(self._anon_map) > self._max_anon_chats:
            self._anon_map.popitem(last=False)
        return label

    def is_username_anonymization_enabled(self, chat_id: int | str | None) -> bool:
        try:
            from stores.group_settings_store import get_group_username_anonymization_enabled
            return get_group_username_anonymization_enabled(chat_id)
        except Exception:
            return True

    def get_llm_sender_name(self, chat_id: int, is_group: bool, user_id: int | None, raw_sender: str) -> str:
        if is_group and self.is_username_anonymization_enabled(chat_id):
            return self.register_anon(chat_id, user_id, raw_sender)
        return raw_sender

    def deanon_text(self, text: str, chat_id: int) -> str:
        if not text:
            return text
        mapping = self._anon_map.get(str(chat_id), {})
        if mapping:
            self._anon_map.move_to_end(str(chat_id))
        for label in sorted(mapping.keys(), key=len, reverse=True):
            if label in text:
                text = text.replace(label, mapping[label])

        labels = set(re.findall(r"用户_[0-9A-Fa-f]{4}", text))
        if labels:
            try:
                from stores.persona_memory import lookup_display_name_by_anon
                for label in sorted(labels, key=len, reverse=True):
                    real = mapping.get(label) or lookup_display_name_by_anon(chat_id, label.upper()) or lookup_display_name_by_anon(chat_id, label)
                    if real and real != label:
                        text = text.replace(label, real)
            except Exception as e:
                self._logger.debug(f"脱敏标签兜底反查失败: {e}")
        return text

    def get_anon_label_by_user(self, chat_id: int, user_id: int | None) -> str:
        if user_id is None:
            return ""
        return self.anonymize_sender(user_id, str(user_id))

    async def record_message(self, msg: Message, bot_username: str, bot_id: int = 0):
        context_mgr = self._get_context_mgr()
        if context_mgr is None:
            return

        text = self._extract_text(msg)
        if not text and not msg.sticker and not msg.photo and not getattr(msg, 'document', None):
            return

        is_reply = self._is_reply_to_bot(msg, bot_username, bot_id)
        is_mention = self._is_mention_bot(msg, bot_username)
        raw_sender = self._get_sender_name(msg)

        is_group = msg.chat.type in ("group", "supergroup")
        # 普通用户用 from_user.id；匿名管理员/频道身份用 sender_chat.id。
        # 这样开启用户名脱敏时，频道名/群名也不会直接进入普通聊天上下文。
        user_id = msg.sender_chat.id if msg.sender_chat else (msg.from_user.id if msg.from_user else None)
        username = (msg.sender_chat.username if msg.sender_chat else (msg.from_user.username if msg.from_user else "")) or ""
        sender = self.get_llm_sender_name(msg.chat_id, is_group, user_id, raw_sender)

        if len(text) > CONTEXT_MAX_TEXT_CHARS:
            text = text[: CONTEXT_MAX_TEXT_CHARS // 2] + "..." + text[-CONTEXT_MAX_TEXT_CHARS // 2:]

        if msg.sticker:
            cm = ContextMessage(
                sender_name=sender,
                message_type="sticker",
                emoji=msg.sticker.emoji or "",
                user_id=user_id,
                username=username,
                message_id=msg.message_id,
                reply_to_message_id=msg.reply_to_message.message_id if msg.reply_to_message else None,
            )
        elif msg.document:
            cm = ContextMessage(
                sender_name=sender,
                text=text,
                message_type="file",
                caption=text,
                file_id=msg.document.file_id or "",
                file_name=msg.document.file_name or "",
                user_id=user_id,
                username=username,
                message_id=msg.message_id,
                reply_to_message_id=msg.reply_to_message.message_id if msg.reply_to_message else None,
            )
        elif msg.photo:
            image_file_id = self._get_photo_file_id(msg)
            cm = ContextMessage(
                sender_name=sender,
                text=text,
                message_type="image",
                caption=text,
                image_file_ids=[image_file_id] if image_file_id else [],
                user_id=user_id,
                username=username,
                message_id=msg.message_id,
                reply_to_message_id=msg.reply_to_message.message_id if msg.reply_to_message else None,
            )
        else:
            cm = ContextMessage(
                sender_name=sender,
                text=text,
                message_type="text",
                is_reply_to_bot=is_reply,
                is_mention=is_mention,
                user_id=user_id,
                username=username,
                message_id=msg.message_id,
                reply_to_message_id=msg.reply_to_message.message_id if msg.reply_to_message else None,
            )

        await context_mgr.append(msg.chat_id, cm)
        try:
            get_activity_store().touch_user_message(msg.chat_id)
        except Exception as e:
            self._logger.warning(f"更新群组活跃时间失败: {e}")

    async def record_bot_response(self, chat_id: int, bot_username: str, segments: list[str], stickers: list[str] | None = None):
        context_mgr = self._get_context_mgr()
        if context_mgr is None:
            return
        stickers = stickers or []
        text = MSG_SEPARATOR.join([s for s in segments if s]).strip()
        if len(text) > BOT_CONTEXT_MAX_CHARS:
            text = text[: BOT_CONTEXT_MAX_CHARS // 2] + "..." + text[-BOT_CONTEXT_MAX_CHARS // 2:]
        if stickers:
            text = (text + " " if text else "") + " ".join(
                f"{STICKER_PREFIX}{e}{STICKER_SUFFIX}" for e in stickers if e
            )
        if not text:
            return
        cm = ContextMessage(
            sender_name=bot_username or "Bot",
            text=text,
            message_type="bot",
            emoji=stickers[0] if stickers else "",
        )
        await context_mgr.append(chat_id, cm)
        try:
            get_activity_store().touch_bot_message(chat_id)
        except Exception as e:
            self._logger.warning(f"更新 Bot 活跃时间失败: {e}")
        self._logger.info(f"🧠 记录 Bot 回复作为上下文分界 | chat={chat_id} | chars={len(text)}")
