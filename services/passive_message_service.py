"""非触发消息处理服务。

处理不会进入 LLM 主回复链路的普通消息：上下文记录、微动作、复读、关键词触发、普通互动好感度。
"""

from __future__ import annotations

import asyncio
import random
import time
from datetime import datetime, timezone, timedelta
from typing import Optional

from stores.context_manager import ContextMessage
from stores.playables_db import DB_PATH, KeywordTriggerRow, orm_session
from features.micro_actions import (
    evaluate_micro_action,
    MicroContext,
)


def check_keyword_triggers(chat_id: str, text: str) -> Optional[str]:
    """检查消息是否匹配自定义关键词触发。"""
    if not text:
        return None
    text_lower = text.strip().lower()

    with orm_session(DB_PATH) as session:
        rows = session.query(KeywordTriggerRow).filter(KeywordTriggerRow.chat_id == str(chat_id)).all()

    for row in rows:
        if row.keyword.lower() in text_lower:
            return row.response
    return None


class PassiveMessageService:
    def __init__(self, *, logger, message_context_service, get_context_mgr, micro_actions_enabled: bool):
        self._logger = logger
        self._message_context_service = message_context_service
        self._get_context_mgr = get_context_mgr
        self._micro_actions_enabled = micro_actions_enabled

    async def handle(self, *, msg, context, chat_id: int, text: str, bot_username: str, bot_id: int, is_group: bool, is_private: bool, whitelist: set) -> None:
        if not (is_private or (is_group and str(chat_id) in whitelist)):
            return

        await self._message_context_service.record_message(msg, bot_username, bot_id)

        if self._micro_actions_enabled and is_group and text:
            await self._try_micro_action(msg, context, chat_id, text, bot_username)

        if is_group and text and len(text) >= 2 and msg.from_user and not msg.from_user.is_bot:
            try:
                from stores.group_settings_store import get_group_repeater_enabled
                if get_group_repeater_enabled(chat_id):
                    from features.repetition import check_repetition
                    repeat_resp = check_repetition(str(chat_id), text, str(msg.from_user.id))
                    if repeat_resp:
                        await asyncio.sleep(random.uniform(0.5, 1.5))
                        await msg.reply_text(repeat_resp)
            except Exception:
                pass

        if is_group and text:
            try:
                trigger_resp = check_keyword_triggers(str(chat_id), text)
                if trigger_resp:
                    await asyncio.sleep(random.uniform(0.3, 1.0))
                    await msg.reply_text(trigger_resp)
            except Exception:
                pass

        if is_group and text and msg.from_user and not msg.from_user.is_bot:
            try:
                from features.social import track_interaction
                track_interaction(str(chat_id), str(msg.from_user.id), "message")
            except Exception:
                pass

    async def _try_micro_action(self, msg, context, chat_id: int, text: str, bot_username: str):
        """微动作评估与触发（原 services/micro_action_service.py 内联）。"""
        try:
            from stores.group_activity_store import get_activity_store as _gas
            store = _gas()
            state = store.get(chat_id)
            now = time.time()

            has_goodbye = any(w in (text or "").lower() for w in ["晚安", "睡了", "拜拜", "再见"])

            ctx = MicroContext(
                chat_id=chat_id,
                hour=datetime.now(timezone(timedelta(hours=8))).hour,
                silent_minutes=(now - (state.last_user_at_timestamp or now)) / 60 if state.last_user_at_timestamp else 0,
                mention_count=state.total_messages if hasattr(state, 'total_messages') else 0,
                bot_mentioned=state.bot_message_count if hasattr(state, 'bot_message_count') else 0,
                has_goodbye=has_goodbye,
                last_action_at=now,
            )

            action_text = evaluate_micro_action(ctx)
            if action_text:
                sent = await context.bot.send_message(
                    chat_id=chat_id,
                    text=action_text,
                    disable_notification=True,
                )
                context_mgr = self._get_context_mgr()
                if context_mgr is not None:
                    cm = ContextMessage(
                        sender_name=bot_username or "Bot",
                        text=action_text,
                        message_type="bot",
                    )
                    await context_mgr.append(chat_id, cm)
                self._logger.info(f"🎭 微动作触发 | chat={chat_id} | text={action_text[:40]}")
        except Exception as e:
            self._logger.debug(f"微动作评估异常: {e}")
