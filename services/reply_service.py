"""聊天回复发送服务。

第一阶段先承接 chat_handler 中的发送/typing 逻辑，
后续再把上下文记录、流式发送、reaction 管理进一步内聚。
"""

from __future__ import annotations

import asyncio
import random
from telegram.constants import ChatAction
from telegram.error import TelegramError
from plugins.manager import get_plugin_manager


class ReplyService:
    def __init__(self, *, logger, get_sticker_mgr, extract_reaction_markers, set_message_reaction_safe):
        self._logger = logger
        self._get_sticker_mgr = get_sticker_mgr
        self._extract_reaction_markers = extract_reaction_markers
        self._set_message_reaction_safe = set_message_reaction_safe

    async def keep_typing(self, bot, chat_id: int, stop_event: asyncio.Event, timeout: float = 120.0):
        deadline = asyncio.get_event_loop().time() + timeout
        try:
            while not stop_event.is_set():
                if asyncio.get_event_loop().time() >= deadline:
                    break
                await bot.send_chat_action(chat_id=chat_id, action=ChatAction.TYPING)
                await asyncio.sleep(min(4.5, deadline - asyncio.get_event_loop().time()))
        except (TelegramError, asyncio.CancelledError):
            pass

    async def send_llm_response(self, msg, response, context, *, sender_not_in_group: bool = False):
        bot = context.bot
        chat_id = msg.chat_id


        pending_reactions: list[tuple[str, int]] = []
        for idx, original in enumerate(list(response.messages)):
            cleaned, reactions = self._extract_reaction_markers(original)
            response.messages[idx] = cleaned
            pending_reactions.extend(reactions)

        for emoji, target_mid in pending_reactions[:3]:
            await self._set_message_reaction_safe(bot, chat_id, target_mid, emoji)

        filtered_messages: list[str] = []
        filtered_stickers: list[str] = []
        for idx, text_value in enumerate(response.messages):
            sticker_value = response.stickers[idx] if idx < len(response.stickers) else ""
            if (text_value or "").strip() or sticker_value:
                filtered_messages.append(text_value)
                filtered_stickers.append(sticker_value)
        response.messages = filtered_messages
        response.stickers = filtered_stickers
        if not response.messages:
            return

        self._logger.info(f"📨 准备发送 {response.message_count} 条消息: {[m[:40] for m in response.messages]}")

        for i in range(response.message_count):
            text = response.messages[i]
            sticker_emoji = response.stickers[i] if i < len(response.stickers) else ""
            send_text, entities = await get_plugin_manager().enrich_outgoing_text(text if text else "…", chat_id=chat_id)
            try:
                if i == 0 or sender_not_in_group:
                    sent = await bot.send_message(
                        chat_id=chat_id,
                        text=send_text,
                        entities=entities,
                        reply_to_message_id=msg.message_id,
                    )
                else:
                    sent = await bot.send_message(
                        chat_id=chat_id,
                        text=send_text,
                        entities=entities,
                    )

                sticker_mgr = self._get_sticker_mgr()
                if sticker_emoji and sticker_mgr:
                    file_id = sticker_mgr.get_file_id(sticker_emoji)
                    if file_id:
                        await bot.send_sticker(
                            chat_id=chat_id,
                            sticker=file_id,
                            reply_to_message_id=sent.message_id,
                        )
                    elif sticker_mgr.available_emojis:
                        fallback_emoji = random.choice(sticker_mgr.available_emojis)
                        fallback_id = sticker_mgr.get_file_id(fallback_emoji)
                        if fallback_id:
                            self._logger.info(f"🎴 流式 fallback: {sticker_emoji} -> {fallback_emoji}")
                            await bot.send_sticker(
                                chat_id=chat_id,
                                sticker=fallback_id,
                                reply_to_message_id=sent.message_id,
                            )
                    else:
                        self._logger.warning(f"未找到贴纸 emoji={sticker_emoji} 的 file_id")

                if i < response.message_count - 1:
                    await asyncio.sleep(random.uniform(0.8, 2.0))
            except TelegramError as e:
                self._logger.error(f"发送消息失败: {e}")
                break
