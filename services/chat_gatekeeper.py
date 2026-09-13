"""聊天入口守卫服务。

集中处理不会改变消息内容的短路判断：私聊禁用、bot-loop 冷却、
以及 focus 轻提示的概率丢弃。保持 handler 主流程更线性。
"""

from __future__ import annotations

import random
import time


class ChatGatekeeper:
    def __init__(self, *, logger, get_context_mgr, bot_loop_cooldown_until: dict[int, float], message_parser=None):
        self._logger = logger
        self._get_context_mgr = get_context_mgr
        self._bot_loop_cooldown_until = bot_loop_cooldown_until
        self._bot_loop_last_signature: dict[int, tuple[str, ...]] = {}
        self._message_parser = message_parser

    @staticmethod
    def is_bot_origin_message(msg) -> bool:
        return bool(msg and msg.from_user and msg.from_user.is_bot and not msg.sender_chat)

    @staticmethod
    def is_human_message(msg) -> bool:
        return bool(msg and msg.from_user and not msg.from_user.is_bot and not msg.sender_chat)

    def allow_bot_to_bot_only_if_human_trigger(self, msg, bot_username: str, bot_id: int = 0) -> tuple[bool, str]:
        """Layer 2: bot-origin messages themselves must not become automatic triggers."""
        if not self.is_bot_origin_message(msg):
            return True, "not_bot_origin"
        if self._message_parser and (
            self._message_parser.is_reply_to_bot(msg, bot_username, bot_id)
            or self._message_parser.is_mention_bot(msg, bot_username)
            or self._message_parser.is_direct_call_bot(msg, bot_username)
        ):
            return False, "bot_origin_direct_trigger_blocked"
        return False, "bot_origin_blocked"

    def is_bot_reply_chain_trigger_disallowed(self, msg, bot_id: int = 0) -> bool:
        """Layer 3: if a bot replies to a bot message, do not allow second-hop triggering."""
        if not self.is_bot_origin_message(msg):
            return False
        if not msg.reply_to_message:
            return False
        rm = msg.reply_to_message
        if self.is_bot_origin_message(rm):
            return True
        if rm.from_user and bot_id and rm.from_user.id == bot_id:
            return True
        return False

    def should_ignore_private_message(self, *, is_private: bool, text: str, user_id: int | str | None) -> bool:
        if not is_private:
            return False
        if text.startswith("/"):
            return False
        # 私聊主体对话开关：开启时正常聊天；若配置了允许列表，则仅允许列表内用户聊天。
        try:
            import app_config.config as config
            private_enabled = bool(getattr(config, "PRIVATE_CHAT_ENABLED", True))
            allowed_ids = {str(x) for x in getattr(config, "PRIVATE_ALLOWED_USER_IDS", set()) if str(x).strip()}
        except Exception:
            private_enabled = True
            allowed_ids = set()
        if not private_enabled:
            self._logger.info(f"🔕 私聊主体 LLM 已禁用，忽略非命令消息 | user={user_id if user_id else 'unknown'}")
            return True
        if allowed_ids and (user_id is None or str(user_id) not in allowed_ids):
            self._logger.info(f"🔒 私聊用户不在允许列表，忽略非命令消息 | user={user_id if user_id else 'unknown'}")
            return True
        return False

    async def detect_bot_loop(self, chat_id: int) -> bool:
        now = time.time()
        cooldown_until = self._bot_loop_cooldown_until.get(chat_id, 0)
        if cooldown_until > now:
            return True
        if cooldown_until:
            self._bot_loop_cooldown_until.pop(chat_id, None)
        if len(self._bot_loop_cooldown_until) > 1000:
            for stale_chat, expiry in list(self._bot_loop_cooldown_until.items()):
                if expiry <= now:
                    self._bot_loop_cooldown_until.pop(stale_chat, None)
                    self._bot_loop_last_signature.pop(stale_chat, None)
        context_mgr = self._get_context_mgr()
        if context_mgr is None:
            return False
        try:
            recent = await context_mgr.get_recent(chat_id, 8)
        except Exception:
            return False

        tail = []
        for cm in reversed(recent):
            uid = getattr(cm, 'user_id', None)
            if uid and int(uid) > 0:
                break
            sender = getattr(cm, 'sender_name', '') or ''
            mtype = getattr(cm, 'message_type', '') or ''
            if mtype in ('bot', 'text', 'image', 'sticker'):
                tail.append(sender)
            else:
                break

        distinct = [x for x in dict.fromkeys(tail) if x]
        if len(tail) >= 4 and len(distinct) >= 2:
            signature = tuple(tail[:8])
            last_signature = self._bot_loop_last_signature.get(chat_id)
            if last_signature == signature:
                self._logger.info(f"🔁 bot-loop 旧 tail 已处理，不重复续冷却 | chat={chat_id} | tail={tail[:6]}")
                return False
            self._bot_loop_last_signature[chat_id] = signature
            self._bot_loop_cooldown_until[chat_id] = now + 180
            self._logger.warning(f"🔁 检测到 bot-loop，进入 180s cooldown | chat={chat_id} | tail={tail[:6]}")
            return True
        self._bot_loop_last_signature.pop(chat_id, None)
        return False

    def should_drop_focus_message(self, chat_id: int, *, should: bool, trigger_type: str) -> bool:
        if not should or trigger_type != "focus_light_hint":
            return False
        try:
            from stores.group_settings_store import get_group_message_drop_probability
            drop_p = get_group_message_drop_probability(chat_id)
            if drop_p <= 0:
                return False
            r = random.random()
            if r < drop_p:
                self._logger.info(f"🎲 消息按概率丢弃 | chat={chat_id} | p={drop_p:g} | r={r:.3f} | type={trigger_type}")
                return True
            self._logger.debug(f"🎲 消息保留 | chat={chat_id} | p={drop_p:g} | r={r:.3f}")
            return False
        except Exception as e:
            self._logger.debug(f"消息丢弃概率检查失败，继续处理: {e}")
            return False
