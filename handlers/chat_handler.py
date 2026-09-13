"""
群聊消息处理器
负责：监听消息、白名单检查、被叫到/回复/@触发、调用 LLM、发送回复。
增强：情绪状态机、真人打字节奏、对话记忆、微动作、去AI味、背压控制。
"""
import logging
import asyncio
import re
from typing import Optional

from telegram import Update
from telegram.ext import ContextTypes, MessageHandler, filters
from app_config.config import (
    MSG_SEPARATOR,
    STICKER_PREFIX,
    STICKER_SUFFIX,
    CONTEXT_MAX_TEXT_CHARS,
    BOT_CONTEXT_MAX_CHARS,
    RECENT_CONTEXT_MESSAGES,
    PERSONALITY_ENABLED,
    CONVERSATION_MEMORY_ENABLED,
    MICRO_ACTIONS_ENABLED,
    DE_AI_ENABLED,
    PERSONA_MEMORY_ENABLED,
)
from llm.llm_client import get_llm_client
from stores.context_manager import (
    ContextMessage,
    ContextManager,
)
from features.sticker_manager import StickerManager
from stores.group_settings_store import get_group_free_reply_mode
from stores.reply_tracker import (
    mark_replied,
    get_replied_map,
)
from stores.personality_state import (
    get_personality,
    restore_personality,
)
from stores.conversation_memory import (
    get_memory,
    extract_topic,
)
from features.de_ai_text import de_ai
from stores.persona_memory import update_persona_after_turn
from services.trigger_service import TriggerService
from services.reply_service import ReplyService
from services.chat_orchestrator import ChatOrchestrator
from services.media_service import MediaService
from services.chat_gatekeeper import ChatGatekeeper
from services.message_context_service import MessageContextService
from services.persona_service import PersonaService
from services.message_parser import MessageParser
from services.reaction_service import ReactionService
from services.focus_service import FocusService
from services.passive_message_service import PassiveMessageService
from services.session_lock import SessionLockManager
from plugins.base import MessageHookContext
from plugins.manager import get_plugin_manager

logger = logging.getLogger(__name__)


# 全局实例（在 main.py 中注入）
_context_mgr: Optional[ContextManager] = None
_sticker_mgr: Optional[StickerManager] = None
_whitelist: set = set()

# concurrent_updates 开启后：不同聊天可并发，同一聊天必须串行，避免上下文/回复乱序。
_session_lock = SessionLockManager()
_bot_loop_cooldown_until: dict[int, float] = {}
# message_id -> whether this bot message may be used as a bot-to-bot trigger source

_message_parser = MessageParser()
_reaction_service = ReactionService(logger=logger)

# ── 运行时节奏策略（原 features/auto_policies.py 内联） ──
EDIT_INTERVAL_SECONDS: float = 1.0
MAX_REPLY_SEGMENTS: int | None = 5


def get_edit_interval_seconds() -> float:
    return float(EDIT_INTERVAL_SECONDS)


def get_max_reply_segments(default: int = 4) -> int:
    if MAX_REPLY_SEGMENTS is not None:
        return int(MAX_REPLY_SEGMENTS)
    return int(default)
_focus_service = FocusService(
    logger=logger,
    extract_text=_message_parser.extract_text,
    get_llm_client=get_llm_client,
)


async def _get_recent_focus_context(chat_id: int) -> list[ContextMessage]:
    """返回当前消息之前最近 5 条历史上下文；失败时降级为空。"""
    if _context_mgr is None:
        return []
    return await _context_mgr.get_recent(chat_id, 5)


_focus_service._get_recent_focus_context = _get_recent_focus_context
_media_service = MediaService(logger=logger)
_gatekeeper = ChatGatekeeper(
    logger=logger,
    get_context_mgr=lambda: _context_mgr,
    bot_loop_cooldown_until=_bot_loop_cooldown_until,
    message_parser=_message_parser,
)
_message_context_service = MessageContextService(
    logger=logger,
    get_context_mgr=lambda: _context_mgr,
    extract_text=_message_parser.extract_text,
    get_sender_name=_message_parser.get_sender_name,
    is_reply_to_bot=_message_parser.is_reply_to_bot,
    is_mention_bot=_message_parser.is_mention_bot,
    get_photo_file_id=_media_service.get_photo_file_id,
)
_persona_service = PersonaService(
    extract_text=_message_parser.extract_text,
    get_sender_name=_message_parser.get_sender_name,
    anonymize_sender=_message_context_service.anonymize_sender,
)
_trigger_service = TriggerService(
    is_reply_to_bot=_message_parser.is_reply_to_bot,
    is_mention_bot=_message_parser.is_mention_bot,
    is_direct_call_bot=_message_parser.is_direct_call_bot,
    focus_can_participate=_focus_service.can_participate,
    gatekeeper=_gatekeeper,
    whitelist=_whitelist,
)
_passive_message_service = PassiveMessageService(
    logger=logger,
    message_context_service=_message_context_service,
    get_context_mgr=lambda: _context_mgr,
    micro_actions_enabled=MICRO_ACTIONS_ENABLED,
)




async def _is_user_admin(bot, chat_id: int, user_id: int) -> bool:
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return str(getattr(member, "status", "")) in ("administrator", "creator")
    except Exception:
        return False


async def _is_user_in_group(bot, chat_id: int, user_id: int) -> bool:
    """检查用户是否仍在群组中（member/admin/creator/restricted 均视为在群内）。"""
    try:
        member = await bot.get_chat_member(chat_id, user_id)
        return str(getattr(member, "status", "")) in ("administrator", "creator", "member", "restricted")
    except Exception:
        # 查询失败时保守假设用户仍在群内，避免误触全回复行为
        return True

async def locked_group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    # business_message 由 business_handler 专门处理，chat handler 不介入
    if update.business_message is not None:
        return
    chat = update.effective_chat
    if not chat:
        return await group_message_handler(update, context)
    async with _session_lock.acquire(chat.id):
        return await group_message_handler(update, context)


def init_handler(
    context_mgr: ContextManager,
    sticker_mgr: StickerManager,
    whitelist: set,
):
    """初始化处理器依赖"""
    global _context_mgr, _sticker_mgr, _whitelist
    _context_mgr = context_mgr
    _sticker_mgr = sticker_mgr
    _whitelist = whitelist
    _trigger_service.update_whitelist(whitelist)


def _log_async_task_exception(task: asyncio.Task):
    """Log async task exceptions explicitly so fire-and-forget focus tasks are visible."""
    try:
        exc = task.exception()
    except asyncio.CancelledError:
        return
    except Exception as e:
        logger.warning(f"异步任务异常读取失败: {e}")
        return
    if exc:
        logger.exception(f"异步任务执行失败: {exc}")


GAME_MARKER_RE = re.compile(r"\[(GAME_SOLVED|GAME_END)\]")


# ----------------------------------------------------------------
# 发送回复
# ----------------------------------------------------------------
_reply_service = ReplyService(
    logger=logger,
    get_sticker_mgr=lambda: _sticker_mgr,
    extract_reaction_markers=_reaction_service.extract_markers,
    set_message_reaction_safe=_reaction_service.set_message_reaction_safe,
)


_chat_orchestrator = ChatOrchestrator(
    logger=logger,
    get_llm_client=get_llm_client,
    get_group_free_reply_mode=get_group_free_reply_mode,
    get_replied_map=get_replied_map,
    keep_typing=_reply_service.keep_typing,
    sticker_prefix=STICKER_PREFIX,
    sticker_suffix=STICKER_SUFFIX,
    game_marker_re=GAME_MARKER_RE,
    text_tool_marker_re=re.compile(r'\\[TOOL:\\w+\\].*?\\[/TOOL\\]|\\[TOOL_RESULT:\\w+\\].*?\\[/TOOL_RESULT\\]', re.DOTALL),
    mark_replied=mark_replied,
    get_edit_interval_seconds=get_edit_interval_seconds,
    extract_reaction_markers=_reaction_service.extract_markers,
    set_message_reaction_safe=_reaction_service.set_message_reaction_safe,
    clear_active_game=lambda cid: __import__('features.history_guess', fromlist=['_clear_active_game'])._clear_active_game(cid),
    de_ai_enabled=DE_AI_ENABLED,
    de_ai=de_ai,
    get_max_reply_segments=get_max_reply_segments,
    msg_separator=MSG_SEPARATOR,
    get_sticker_mgr=lambda: _sticker_mgr,
    record_message=_message_context_service.record_message,
    record_bot_response=_message_context_service.record_bot_response,
    deanon_text=_message_context_service.deanon_text,
    record_bot_reply=lambda chat_id, segments, stickers: __import__('stores.human_behavior', fromlist=['record_bot_reply']).record_bot_reply(chat_id, segments, stickers),
    persona_memory_enabled=PERSONA_MEMORY_ENABLED,
    update_persona_after_turn=update_persona_after_turn,
    log_async_task_exception=_log_async_task_exception,
    get_context_mgr=lambda: _context_mgr,
    context_max_text_chars=CONTEXT_MAX_TEXT_CHARS,
    bot_context_max_chars=BOT_CONTEXT_MAX_CHARS,
    conversation_memory_enabled=CONVERSATION_MEMORY_ENABLED,
    extract_topic=extract_topic,
    get_memory=get_memory,
)



# 注：响应流程已内联在 group_message_handler 中。
# 旧版 _process_trigger_response 曾用于后台异步轻提示，但已移除，避免递归触发和计数错乱。

# ----------------------------------------------------------------
# 主处理器
# ----------------------------------------------------------------
async def group_message_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """群聊消息处理入口"""
    msg = update.effective_message
    if not msg or not msg.chat:
        return

    chat_type = msg.chat.type
    is_group = chat_type in ("group", "supergroup")
    is_private = chat_type == "private"

    if not is_group and not is_private:
        return

    # 私聊管理面板待输入兜底：
    # 若 admin/gadmin 在 bot_data 里有 pending 状态，直接短路，
    # 避免面板输入落入普通聊天处理。
    if is_private:
        try:
            from handlers.admin_panel import _admin_pending_store
            from handlers.group_admin_panel import gadmin_has_pending
            user_id = str(update.effective_user.id) if update.effective_user else None
            if user_id and (_admin_pending_store(context).get(user_id) or gadmin_has_pending(context)):
                logger.info("🛑 私聊面板待输入兜底拦截 | user=%s", user_id)
                return
        except Exception:
            logger.exception("检查私聊待输入状态失败，已继续走普通聊天流程")

    text = _message_parser.extract_text(msg)
    # 普通私聊不走主体 LLM。
    # /admin、/settings、/gadmin 等命令由 group=-1/0 的命令 handler 处理。
    if _gatekeeper.should_ignore_private_message(
        is_private=is_private,
        text=text,
        user_id=(msg.from_user.id if msg.from_user else None),
    ):
        return

    bot_username = context.bot.username
    bot_id = context.bot.id
    chat_id = msg.chat_id

    # bot in bot communication 已开启时，不直接整体跳过 bot 消息；
    # 但后续 TriggerService / gatekeeper 规则会阻止它们形成自动互聊死循环。

    # 匿名管理员/频道身份：优先使用 sender_chat 作为名字
    raw_sender = _message_parser.get_sender_name(msg)
    is_group_chat = msg.chat.type in ("group", "supergroup")
    # 普通用户用 from_user.id 脱敏；匿名管理员/频道身份用 sender_chat.id 脱敏，避免把频道/群名暴露给 LLM。
    user_id_for_sender = msg.sender_chat.id if msg.sender_chat else (msg.from_user.id if msg.from_user else None)
    sender = _message_context_service.get_llm_sender_name(chat_id, is_group_chat, user_id_for_sender, raw_sender)
    if msg.sender_chat:
        logger.info(
            f"👤 sender_chat 消息 | chat={chat_id} | sender_chat={raw_sender} "
            f"| from_user={(msg.from_user.username if msg.from_user else None)}"
        )

    # 命令消息交给 admin handler，不进入 LLM 上下文
    if text.startswith("/"):
        return

    # 插件消息监听 hook：可在评分/触发前处理消息或强制进入 LLM。
    plugin_force_llm = False
    plugin_result = None
    try:
        plugin_result = await get_plugin_manager().dispatch_message(MessageHookContext(
            update=update,
            telegram_context=context,
            msg=msg,
            chat_id=chat_id,
            chat_type=chat_type,
            is_group=is_group,
            is_private=is_private,
            text=text,
            raw_sender=raw_sender,
            sender=sender,
            user_id=(msg.from_user.id if msg.from_user else None),
            username=(msg.from_user.username if msg.from_user else None),
            bot_username=bot_username,
            bot_id=bot_id,
            has_photo=bool(msg.photo),
            has_sticker=bool(msg.sticker),
            image_file_id=_media_service.get_photo_file_id(msg),
            image_caption_text=(msg.caption or ""),
            whitelist=_whitelist,
        ))
    except Exception:
        logger.exception("插件消息 hook 执行失败，继续原流程")
    if plugin_result and plugin_result.action in ("handled", "drop"):
        return
    if plugin_result and plugin_result.action == "force_llm":
        plugin_force_llm = True
        if plugin_result.text is not None:
            text = plugin_result.text
        if plugin_result.sender is not None:
            sender = plugin_result.sender

    # 判断是否触发
    if plugin_force_llm:
        should, is_reply, is_mention, trigger_type = True, False, False, (plugin_result.trigger_type or "plugin")
    else:
        should, is_reply, is_mention, trigger_type = _trigger_service.should_trigger(msg, bot_username, bot_id)

    # ── 消息丢弃概率：只影响普通群消息的主动处理，不影响命令、@/回复/叫名。──
    if is_group and _gatekeeper.should_drop_focus_message(chat_id, should=should, trigger_type=trigger_type):
        return

    if not should:
        await _passive_message_service.handle(
            msg=msg,
            context=context,
            chat_id=chat_id,
            text=text,
            bot_username=bot_username,
            bot_id=bot_id,
            is_group=is_group,
            is_private=is_private,
            whitelist=_whitelist,
        )
        return

    # ── 静音检查：被 /muteme 的用户忽略所有触发 ──
    if is_group and msg.from_user and not msg.from_user.is_bot:
        try:
            from handlers.mute_handler import is_muted
            if is_muted(str(chat_id), str(msg.from_user.id)):
                logger.debug(f"🔇 跳过静音用户: chat={chat_id} user={msg.from_user.id}")
                return
        except Exception:
            pass

    # ── 好感度追踪（仅群组成员） ──
    if is_group and msg.from_user and not msg.from_user.is_bot:
        try:
            from features.social import track_interaction, get_affinity_level

            interaction_type = "reply" if is_reply else ("mention" if is_mention else "message")
            score = track_interaction(str(chat_id), str(msg.from_user.id), interaction_type)
            level, affinity_mult = get_affinity_level(score)
            if affinity_mult != 1.0:
                logger.debug(f"💖 好感度等级: {level} (x{affinity_mult}) | chat={chat_id} user={msg.from_user.id}")
        except Exception:
            pass

    # ── 情绪状态恢复与更新 ──
    if PERSONALITY_ENABLED:
        personality = get_personality(chat_id)
        restore_personality(chat_id, time_since_last=0)
        personality.update(text or "")
        logger.debug(f"😺 情绪状态 | chat={chat_id} | mood={personality.mood.value} | energy={personality.energy:.2f}")

    # ── 对话记忆提取 ──
    if CONVERSATION_MEMORY_ENABLED and text:
        topic_info = extract_topic(text)
        if topic_info:
            topic, summary = topic_info
            get_memory(chat_id).add_topic(topic, summary)

    # 允许：文本、贴纸、图片触发
    has_photo = bool(msg.photo)
    has_sticker = bool(msg.sticker)
    if not text and not has_sticker and not has_photo:
        return

    attention_mode = _focus_service.get_attention_mode(chat_id, is_group=is_group)

    # 全消息注意力会把更多普通消息交给 LLM 判断；单个普通贴纸没有足够信息。
    if _focus_service.should_skip_plain_sticker(
        is_group=is_group,
        attention_mode=attention_mode,
        trigger_type=trigger_type,
        has_sticker=has_sticker,
        has_photo=has_photo,
        text=text,
    ):
        logger.info(f"🌊 全消息注意力忽略普通单贴纸 | chat={chat_id}")
        return

    # 下载图片/贴纸 → 转译为文字（DeepSeek 不支持图片/贴纸输入）
    media_payload = await _media_service.prepare(msg, context, is_reply=is_reply)
    image_b64 = media_payload.image_b64
    image_file_id = media_payload.image_file_id
    image_caption_text = media_payload.image_caption_text

    focus_reserved = False
    # 自由回复的合法 reply target 范围应与 LLM 可见上下文尽量一致，避免“模型看得到但系统不允许回”。
    context_snapshot = await _context_mgr.get_recent(chat_id, 50) if _context_mgr is not None else []
    persona_users = _persona_service.collect_refs(msg, chat_id, bot_id, context_snapshot)
    if not _focus_service.reserve_or_refresh(chat_id, trigger_type=trigger_type, is_reply=is_reply, is_mention=is_mention):
        return
    focus_reserved = trigger_type == "focus_light_hint"
    logger.info(
        f"💬 触发对话 | chat={chat_id} | type={trigger_type} | "
        f"sender={sender} | {'📸' if has_photo else ''}{text[:50] if text else ('[sticker]' if has_sticker else '[photo]')}"
    )

    # ── 检查发送者是否仍在群内：退群/被踢用户触发的回复全部引用原消息 ──
    sender_not_in_group = False
    if is_group and msg.from_user and not msg.from_user.is_bot and not msg.sender_chat:
        sender_not_in_group = not await _is_user_in_group(context.bot, chat_id, msg.from_user.id)
        if sender_not_in_group:
            logger.info(f"👤 发送者不在群内，所有回复将引用原消息 | chat={chat_id} user={msg.from_user.id}")

    # ── 频道消息：所有回复引用原消息（不包括匿名管理员，匿名管理员 sender_chat 存在但 from_user 为空）──
    sender_is_channel = False
    if msg.sender_chat and msg.from_user:
        sender_is_channel = True
        logger.info(f"📢 频道消息，所有回复将引用原消息 | chat={chat_id} | channel={raw_sender}")

    async def _run_orchestrator_once():
        return await _chat_orchestrator.run(
            msg=msg,
            context=context,
            chat_id=chat_id,
            text=text,
            sender=sender,
            bot_username=bot_username,
            bot_id=bot_id,
            is_reply=is_reply,
            is_mention=is_mention,
            trigger_type=trigger_type,
            has_photo=has_photo,
            has_sticker=has_sticker,
            image_b64=image_b64,
            image_file_id=image_file_id,
            image_caption_text=image_caption_text,
            persona_users=persona_users,
            context_snapshot=context_snapshot,
            focus_reserved=focus_reserved,
            is_group=is_group,
            is_private=is_private,
            sender_not_in_group=sender_not_in_group,
            sender_is_channel=sender_is_channel,
        )

    # ── 聚焦轻提示：按注意力模式进入完整 chat_stream ──
    if trigger_type == "focus_light_hint":
        try:
            if attention_mode == "mixed":
                # Mixed: full-message attention first. If it actually replies, do not run single-message attention.
                first_result = await _run_orchestrator_once()
                if isinstance(first_result, dict) and first_result.get("replied"):
                    logger.info(f"🧪 混合模式：全消息注意力已回应，跳过单消息 | chat={chat_id}")
                    return
                logger.info(f"🧪 混合模式：全消息注意力未回应，回退单消息评分 | chat={chat_id}")
                if not await _focus_service.allow_focus_light_hint_stream(
                    chat_id=chat_id,
                    attention_mode="single_message",
                    text=text,
                    has_sticker=has_sticker,
                    has_photo=has_photo,
                ):
                    return
                await _run_orchestrator_once()
                return

            if not await _focus_service.allow_focus_light_hint_stream(
                chat_id=chat_id,
                attention_mode=attention_mode,
                text=text,
                has_sticker=has_sticker,
                has_photo=has_photo,
            ):
                return
        except Exception as e:
            logger.warning(f"聚焦模式前置/混合判断失败，已静默跳过 | chat={chat_id} | err={e}")
            return

    try:
        result = await _run_orchestrator_once()
    except Exception as e:
        if trigger_type == "focus_light_hint":
            logger.warning(f"聚焦模式主流程失败，已静默跳过 | chat={chat_id} | err={e}")
            return
        raise
    return

    # 主回复流程已迁移到 ChatOrchestrator；旧内联流式实现已删除。



def get_handler():
    """返回 MessageHandler 实例"""
    return MessageHandler(
        (filters.ChatType.GROUPS | filters.ChatType.PRIVATE) & (
            filters.TEXT |
            filters.PHOTO |
            filters.Sticker.ALL |
            filters.CAPTION |
            filters.Entity("mention")
        ),
        locked_group_message_handler,
    )
