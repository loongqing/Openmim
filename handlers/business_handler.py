"""
Business Chatbot 消息处理器

处理 Telegram Business 功能：
  - business_connection：跟踪 business 连接状态
  - business_message：接收终端用户消息 → LLM 回复 → 以业务账号名义发送
  - 私聊设置面板：/settings 配置 LLM API/模型/人设，支持上传 markdown 文件
  - 双方用户名脱敏
  - "is typing" 提示 + 拟人打字延迟
"""
import asyncio
import hashlib
import logging
import random
import time
from collections import OrderedDict
from typing import Optional

from app_config.customization import get_text
import app_config.config as config

from telegram import Update, Message
from telegram.ext import (
    ContextTypes, MessageHandler, CommandHandler, filters,
)
from telegram.constants import ChatAction
from telegram.error import TelegramError

from app_config.config import (
    BUSINESS_ENABLED,
    BUSINESS_MAX_REPLY_CHARS,
    BUSINESS_TYPING_DELAY_MIN,
    BUSINESS_TYPING_DELAY_MAX,
    BUSINESS_TYPING_DELAY_PER_CHAR,
)
from llm.llm_client import get_llm_client
from stores.context_manager import ContextMessage
from stores.business_settings import (
    get_user_settings,
    set_user_setting,
    reset_user_setting,
)
from features.business_prompt import build_default_persona
from features.business_synonym import SynonymPipeline
from features.business_humanization import (
    build_business_control_hint,
    postprocess_business_reply,
)
from plugins.manager import get_plugin_manager
from features.typing_rhythm import (
    human_reaction_delay,
    segment_delay,
)
from services.message_context_service import MessageContextService
from services.session_lock import SessionLockManager

logger = logging.getLogger("BusinessBot")

# ── 活跃的 business connection 缓存 ──
# business_connection_id → owner_user_id
_active_connections: OrderedDict[str, int] = OrderedDict()
_MAX_ACTIVE_CONNECTION_CACHE = 1000


def _cache_active_connection(connection_id: str, owner_id: int) -> None:
    _active_connections[connection_id] = owner_id
    _active_connections.move_to_end(connection_id)
    if len(_active_connections) > _MAX_ACTIVE_CONNECTION_CACHE:
        _active_connections.popitem(last=False)



def _is_allowed_business_user(user_id: int | None) -> bool:
    allowed = {str(x) for x in getattr(config, "BUSINESS_ALLOWED_USER_IDS", set()) if str(x).strip()}
    if not allowed:
        return True
    return user_id is not None and str(user_id) in allowed

def _is_valid_business_human_sender(msg: Message, owner_id: int) -> bool:
    """仅允许真人终端用户进入 Business 对话/记忆链路。

    设计理由：
    - Telegram Business 场景下，单靠 from_user.is_bot 一层过滤不够稳妥；
    - 这里把 owner 自言自语、bot、频道/匿名身份(sender_chat) 一并挡住；
    - 后续在入口和上下文/记忆前都复用，避免漏过一次就污染上下文。
    """
    if msg is None:
        return False

    from_user = getattr(msg, "from_user", None)
    if from_user is None:
        logger.debug("Business msg 缺少 from_user，跳过")
        return False

    if getattr(from_user, "is_bot", False):
        logger.info(
            "Business 跳过 bot 消息 | bot=%s id=%s",
            _get_display_name(from_user),
            getattr(from_user, "id", "?"),
        )
        return False

    if getattr(msg, "sender_chat", None) is not None:
        sender_chat = msg.sender_chat
        logger.info(
            "Business 跳过 sender_chat 消息 | chat=%s id=%s",
            getattr(sender_chat, "title", None) or getattr(sender_chat, "username", None) or "未知",
            getattr(sender_chat, "id", "?"),
        )
        return False

    if not _is_allowed_business_user(getattr(from_user, "id", None)):
        logger.info("Business 跳过未授权用户 | user=%s", getattr(from_user, "id", None))
        return False

    if getattr(from_user, "id", None) == owner_id:
        logger.debug("Business 跳过 owner 自己的消息 | owner=%s", owner_id)
        return False

    return True

# concurrent_updates 开启后，同一个 Business 私聊必须串行处理，避免连续消息回复乱序。
_session_lock = SessionLockManager()


# ── 用户名脱敏 ──
# 脱敏逻辑与 MessageContextService.anonymize_sender 完全一致，直接复用。


async def _resolve_owner_id(business_connection_id: str, bot):
    """从内存缓存或 Telegram API 反查 business connection 的 owner id。

    服务重启后内存缓存会丢失，必须用 get_business_connection 反查，
    不能用 update.effective_user 猜 owner。
    返回 (owner_id, business_conn)；解析失败时 owner_id 为 None。
    """
    owner_id = _active_connections.get(business_connection_id)
    business_conn = None
    if owner_id is None:
        try:
            business_conn = await bot.get_business_connection(business_connection_id)
            if business_conn and business_conn.user:
                owner_id = business_conn.user.id
                _cache_active_connection(business_connection_id, owner_id)
                logger.info(
                    f"🏢 Business 连接已从 API 恢复 | conn={business_connection_id[:16]}... | "
                    f"owner={_get_display_name(business_conn.user)} (ID:{owner_id})"
                )
        except TelegramError as e:
            logger.warning(f"Business 连接反查失败 | conn={business_connection_id[:16]}... | err={e}")
        except Exception as e:
            logger.warning(f"Business 连接反查异常 | conn={business_connection_id[:16]}... | err={e}")
    return owner_id, business_conn


def _get_display_name(user) -> str:
    """获取用户显示名。"""
    if user is None:
        return "未知"
    return user.full_name or user.first_name or str(user.id)


# ── 拟人打字延迟 ──
def _human_typing_delay(reply_text: str, incoming_text: str = "") -> float:
    """复用主体的真人节奏模型，并保留 Business 的可配置上下限。"""
    if not reply_text:
        return BUSINESS_TYPING_DELAY_MIN
    is_complex = len(incoming_text or "") > 80 or any(x in (incoming_text or "") for x in ("?", "？", "怎么", "为什么", "解释"))
    base = human_reaction_delay(reply_text, energy=0.9, is_complex=is_complex)
    # Business 回复允许稍慢一点，但不拖太久。
    per_char = len(reply_text) * BUSINESS_TYPING_DELAY_PER_CHAR * 0.35
    total = base + random.uniform(BUSINESS_TYPING_DELAY_MIN * 0.3, BUSINESS_TYPING_DELAY_MAX * 0.5) + per_char
    return max(BUSINESS_TYPING_DELAY_MIN, min(total, 7.0))


def _business_chat_key(owner_id: int | str, other_id: int | str) -> int:
    """ContextManager 只接受 int key；用稳定 hash 区分 owner-other 私聊。"""
    raw = f"business:{owner_id}:{other_id}".encode("utf-8")
    return -int(hashlib.sha256(raw).hexdigest()[:15], 16)


# ═══════════════════════════════════════════════════
#  business_connection 处理器
# ═══════════════════════════════════════════════════

async def on_business_connection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """监听 business 连接/断开事件。"""
    if not BUSINESS_ENABLED:
        return

    conn = update.business_connection
    if conn is None:
        return

    conn_id = conn.id
    user = conn.user
    can_reply = conn.is_enabled
    is_enabled = conn.is_enabled

    owner_id = user.id if user else 0
    owner_name = _get_display_name(user)

    if is_enabled and can_reply:
        _cache_active_connection(conn_id, owner_id)
        logger.info(
            f"🏢 Business 已连接 | conn={conn_id[:16]}... | "
            f"owner={owner_name} (ID:{owner_id})"
        )
        # 通知用户已连接
        try:
            await context.bot.send_message(
                chat_id=owner_id,
                text=get_text(
                    "business.connected_message",
                    "🐱 **Business Chatbot 已连接！**\n\n"
                    "从现在起，咱会帮你看着私聊、替你回话喵。\n\n"
                    "━━━ 配置命令 ━━━\n"
                    "📋 `/settings` — 查看/修改 LLM 和人设配置\n"
                    "📄 上传 `.md` 文件 — 自定义人设\n\n"
                    "💡 默认人设是咪姆酱风格，名字替换为你自己。\n"
                    "你想改说话风格的话，随时都能自己换掉喵。",
                ),
                parse_mode="Markdown",
            )
        except TelegramError:
            pass
    else:
        _active_connections.pop(conn_id, None)
        logger.info(
            f"🏢 Business 已断开 | conn={conn_id[:16]}... | "
            f"owner={owner_name} (ID:{owner_id})"
        )


# ═══════════════════════════════════════════════════
#  business_message 处理器
# ═══════════════════════════════════════════════════

async def on_business_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Resolve the conversation identity, then serialize its complete lifecycle."""
    if not BUSINESS_ENABLED:
        return
    msg = update.business_message
    if msg is None:
        return
    business_connection_id = msg.business_connection_id
    if not business_connection_id:
        logger.warning("business_message missing business_connection_id")
        return

    owner_id, business_conn = await _resolve_owner_id(business_connection_id, context.bot)
    if owner_id is None:
        logger.warning("Business msg owner unresolved | conn=%s", business_connection_id[:16])
        return

    from_user = msg.from_user
    if not _is_valid_business_human_sender(msg, owner_id):
        return
    session_key = f"{owner_id}:{from_user.id}"
    async with _session_lock.acquire(session_key):
        await _on_business_message_unlocked(update, context, owner_id=owner_id, business_conn=business_conn)


async def _on_business_message_unlocked(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    *,
    owner_id: int,
    business_conn,
):
    """处理 business_message —— 终端用户发给业务账号的消息。"""
    if not BUSINESS_ENABLED:
        return

    msg = update.business_message
    if msg is None:
        return

    business_connection_id = msg.business_connection_id
    if not business_connection_id:
        logger.warning("business_message 缺少 business_connection_id")
        return

    # ── 获取双方信息 ──
    # 终端用户（谁给业务账号发了消息）
    from_user = msg.from_user
    if not _is_valid_business_human_sender(msg, owner_id):
        return

    other_real_name = _get_display_name(from_user)
    other_id = from_user.id

    # 脱敏
    other_name = MessageContextService.anonymize_sender(other_id, other_real_name)

    # 业务账号所有者名字（优先使用 BusinessConnection.user）
    owner_real_name = "我"
    if business_conn is not None and getattr(business_conn, "user", None):
        owner_real_name = _get_display_name(business_conn.user)
    else:
        try:
            chat_member = await context.bot.get_chat_member(owner_id, owner_id)
            owner_user = chat_member.user
            owner_real_name = _get_display_name(owner_user) if owner_user else f"用户{owner_id}"
        except Exception:
            owner_real_name = f"用户{owner_id}"
    owner_name = MessageContextService.anonymize_sender(owner_id, owner_real_name)

    # ── 提取消息文本 / 多媒体占位 ──
    text = msg.text or msg.caption or ""
    if not text:
        if msg.sticker:
            text = f"[对方发了一个贴纸 emoji={msg.sticker.emoji or ''}]"
        elif msg.photo:
            caption = (msg.caption or "").strip()
            text = f"[对方发了一张图片{'，说明：' + caption if caption else ''}]"
        elif msg.voice:
            dur = getattr(msg.voice, 'duration', None)
            text = f"[对方发了一段语音{'，时长约' + str(dur) + '秒' if dur else ''}]"
        elif msg.video:
            dur = getattr(msg.video, 'duration', None)
            caption = (msg.caption or "").strip()
            text = f"[对方发了一段视频{'，时长约' + str(dur) + '秒' if dur else ''}{'，说明：' + caption if caption else ''}]"
        elif msg.document:
            fname = getattr(msg.document, 'file_name', '') or ''
            text = f"[对方发了一个文件{'：' + fname if fname else ''}]"
        elif msg.animation:
            caption = (msg.caption or "").strip()
            text = f"[对方发了一个动图{'，说明：' + caption if caption else ''}]"
        else:
            return

    logger.info(
        f"💼 Business msg | owner={owner_real_name} | "
        f"other={other_real_name} | text={text[:60]}"
    )

    # ── 1. 发送 typing 指示器 ──
    typing_task: Optional[asyncio.Task] = None

    async def _keep_typing():
        """持续刷新 typing 状态。"""
        deadline = time.monotonic() + 30
        while time.monotonic() < deadline:
            try:
                await context.bot.send_chat_action(
                    chat_id=other_id,
                    action=ChatAction.TYPING,
                    business_connection_id=business_connection_id,
                )
            except TelegramError:
                break
            await asyncio.sleep(4.5)

    typing_task = asyncio.create_task(_keep_typing())

    # ── 2. 记录上下文 & 调用 LLM ──
    # 获取 ContextManager
    context_mgr = context.bot_data.get("context_mgr")
    biz_chat_id = _business_chat_key(owner_id, other_id)

    # 记录对方的消息
    # 这里再次做真人校验，避免入口层偶发漏判时把 bot/频道消息写进上下文与记忆提示。
    if context_mgr and _is_valid_business_human_sender(msg, owner_id):
        cm = ContextMessage(
            sender_name=other_name,
            text=text,
            message_type="text",
        )
        await context_mgr.append(biz_chat_id, cm)

    # 获取最近上下文
    context_lines = []
    if context_mgr:
        recent = await context_mgr.get_recent(biz_chat_id, 10)
        for cm in recent:
            line = f"[{cm.sender_name}]: {cm.text or '(无文字)'}"
            if len(line) > 300:
                line = line[:300] + "..."
            context_lines.append(line)

    settings = get_user_settings(owner_id)
    logger.info(
        f"💼 Business settings | owner={owner_id} | mode={settings.mode} | "
        f"multi={settings.is_multi_message_enabled()} | sticker={settings.is_sticker_enabled()}"
    )
    control_hint = build_business_control_hint(
        sticker_enabled=settings.is_sticker_enabled(),
        multi_message_enabled=settings.is_multi_message_enabled(),
        available_emojis=context.bot_data.get("sticker_mgr").available_emojis if context.bot_data.get("sticker_mgr") else [],
    )
    try:
        if settings.is_synonym_mode():
            # 已读乱回模式：走近义词流水线
            pipeline = SynonymPipeline(
                api_key=settings.effective_api_key(),
                api_base=settings.effective_api_base(),
                model=settings.effective_model(),
                provider=settings.effective_provider(),
            )
            result = await pipeline.process(text)
            reply_text = result.final_text
            logger.info(
                f"💼 Synonym reply | owner={owner_real_name} | "
                f"paragraph={result.paragraph[:40]}... | final={result.final_text[:40]}..."
            )
        else:
            # 经典对话模式：走 LLM chat
            llm = get_llm_client()
            reply_text = await llm.business_chat(
                owner_name=owner_name,
                owner_id=owner_id,
                other_name=other_name,
                message_text=text,
                context_messages=context_lines if context_lines else None,
                control_hint=control_hint,
            )
    except Exception as e:
        logger.exception(f"Business LLM 调用失败 | owner={owner_id} | mode={settings.mode}")
        reply_text = get_text("business.error_reply", "呜，刚才打了个结喵，稍后再试一下嘛。")
    finally:
        # 停止 typing 指示器
        if typing_task:
            typing_task.cancel()
            try:
                await typing_task
            except asyncio.CancelledError:
                pass

    if not reply_text:
        reply_text = get_text("business.empty_reply", "唔，咱一时有点卡壳喵。")

    processed = postprocess_business_reply(
        reply_text,
        sticker_enabled=settings.is_sticker_enabled(),
        multi_message_enabled=settings.is_multi_message_enabled(),
        max_messages=3,
    )
    reply_messages = processed.messages[:1] if not settings.is_multi_message_enabled() else processed.messages
    logger.info(
        f"💼 Business postprocess | parts={len(reply_messages)} | stickers={processed.stickers} | "
        f"multi_enabled={settings.is_multi_message_enabled()} | sticker_enabled={settings.is_sticker_enabled()}"
    )

    # ── 3. 拟人打字延迟 ──
    delay = _human_typing_delay(" ".join(reply_messages), text)
    logger.debug(f"💼 typing delay: {delay:.1f}s for {sum(len(x) for x in reply_messages)} chars")
    await asyncio.sleep(delay)

    # ── 4. 以业务账号名义发送回复（支持多句分条） ──
    try:
        for i, part in enumerate(reply_messages):
            send_text, entities = await get_plugin_manager().enrich_outgoing_text(part, chat_id=other_id)
            await context.bot.send_message(
                chat_id=other_id,
                text=send_text,
                entities=entities,
                business_connection_id=business_connection_id,
            )
            if i < len(reply_messages) - 1:
                await asyncio.sleep(segment_delay(i, len(reply_messages), part))

        # 贴纸仅在用户面板开启时发送。
        if settings.is_sticker_enabled() and processed.stickers:
            sticker_mgr = context.bot_data.get("sticker_mgr")
            if sticker_mgr:
                for emoji in processed.stickers[:1]:
                    file_id = sticker_mgr.get_file_id(emoji)
                    if file_id:
                        try:
                            await context.bot.send_sticker(
                                chat_id=other_id,
                                sticker=file_id,
                                business_connection_id=business_connection_id,
                            )
                        except TelegramError as e:
                            logger.warning(f"Business 贴纸发送失败 | emoji={emoji} | err={e}")
                        break

        logger.info(
            f"💼 Business reply | owner={owner_real_name} | "
            f"other={other_real_name} | parts={len(reply_messages)} len={sum(len(x) for x in reply_messages)}"
        )
        # 记录 Bot 回复到上下文
        if context_mgr:
            bot_cm = ContextMessage(
                sender_name=owner_name,
                text=" ".join(reply_messages),
                message_type="bot",
            )
            await context_mgr.append(biz_chat_id, bot_cm)
    except TelegramError as e:
        logger.error(f"Business 发送回复失败: {e}")


# ═══════════════════════════════════════════════════
#  返回所有 handlers

def get_handlers() -> list:
    """返回需要注册到 Application 的 handler 列表。"""
    from telegram.ext import TypeHandler
    from telegram import Update as TelegramUpdate

    return [
        # business_connection / business_message 统一由 TypeHandler 分发，
        # 避免 BusinessConnectionHandler 与 TypeHandler 对同一 update 重复处理。
        TypeHandler(TelegramUpdate, _on_business_update, strict=False, block=False),
    ]


async def _on_business_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """统一入口：根据 update 类型分发。"""
    if update.business_connection is not None:
        logger.debug("Business TypeHandler 收到 business_connection")
        await on_business_connection(update, context)
        return
    if update.business_message is not None:
        logger.info("Business TypeHandler 收到 business_message")
        await on_business_message(update, context)
        return
