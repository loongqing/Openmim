"""
管理员命令处理器
提供白名单管理、状态查询等命令。
"""
import logging
from typing import Set, Callable
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ChatMemberHandler, CommandHandler, ContextTypes
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app_config.config import (
    ADMIN_IDS,
    save_whitelist,
    CONTEXT_MESSAGE_COUNT,
    IDLE_TOPIC_IDLE_HOURS,
    FOCUS_LIGHT_HINT_ENABLED,
    FOCUS_LIGHT_HINT_PROBABILITY,
    FOCUS_LIGHT_HINT_COOLDOWN_SECONDS,
    FOCUS_JOIN_MAX_PER_SESSION,
    TRIGGER_PROBABILITY,
)
from stores.focus_store import get_focus_store
from stores.group_activity_store import get_activity_store
from stores.group_settings_store import set_group_reply_preference

logger = logging.getLogger(__name__)

# ── 触发概率策略（原 features/auto_policies.py 内联） ──
# 这是内联自原 features/auto_policies.py 的固定值 0.01，行为与原实现等价。
# 内联后失去了"可被外部改写"的语义，因此此值为固定常量。
# 如需调整随机触发概率，直接修改下方 TRIGGER_PROBABILITY_OVERRIDE 的值即可。
TRIGGER_PROBABILITY_OVERRIDE: float | None = 0.01


def get_trigger_probability() -> float:
    if TRIGGER_PROBABILITY_OVERRIDE is not None:
        return float(TRIGGER_PROBABILITY_OVERRIDE)
    return float(TRIGGER_PROBABILITY)

# 全局引用（由 main.py 注入）
_whitelist: Set[str] = set()
_save_callback: Callable = lambda: None
_context_mgr = None  # ContextManager 引用


def init_admin(whitelist: Set[str], save_cb: Callable = None, context_mgr=None):
    global _whitelist, _save_callback, _context_mgr
    _whitelist = whitelist
    if save_cb:
        _save_callback = save_cb
    if context_mgr:
        _context_mgr = context_mgr


def _is_admin(user_id: int) -> bool:
    """检查用户是否为管理员"""
    return str(user_id) in ADMIN_IDS


def _check_admin(update: Update) -> bool:
    """检查并返回是否为管理员"""
    user_id = update.effective_user.id if update.effective_user else 0
    return _is_admin(user_id)


def _build_whitelist_onboarding_message() -> str:
    return (
        "✅ 当前群组已加入白名单，并已默认设置为 **提到机器人优先**。\n\n"
        "在这个模式下，机器人只会回应：\n"
        "• 直接回复机器人的消息\n"
        "• @机器人的消息\n"
        "• 明确叫机器人的消息\n\n"
        "如果你希望机器人也能对它感兴趣的普通消息主动插话，请切换到 **LLM偏好优先**。\n\n"
        "**完整操作流程：**\n"
        "1. 私聊机器人\n"
        "2. 发送 `/gadmin`\n"
        "3. 选择要配置的群组\n"
        "4. 进入 `💬 回复策略`\n"
        "5. 点击 `🎯 回复偏好`\n"
        "6. 切换为 `🧠 LLM偏好优先`\n\n"
        "切换后，机器人才能根据聚焦评分对感兴趣的话题主动参与回复。"
    )


def _build_whitelist_instruction_message(chat_id: str | int, chat_title: str = "") -> str:
    title_line = f"群组：**{chat_title}**\n" if chat_title else ""
    cid = str(chat_id)
    return (
        "**机器人已加入群组**\n\n"
        f"{title_line}"
        f"ID：`{cid}`\n\n"
        "请由机器人超级管理员手动添加：\n"
        f"• 私聊机器人发送：`/whitelist {cid}`\n"
        "• 或在本群发送：`/whitelist here`"
    )


async def _add_whitelist_chat(chat_id: str | int, *, notify_bot=None, notify_chat: bool = True) -> bool:
    cid = str(chat_id).strip()
    if not cid:
        return False
    if cid in _whitelist:
        return False
    _whitelist.add(cid)
    save_whitelist(_whitelist)
    set_group_reply_preference(cid, "mention_first")
    if notify_bot and notify_chat:
        try:
            await notify_bot.send_message(
                chat_id=int(cid),
                text=_build_whitelist_onboarding_message(),
                parse_mode="Markdown",
            )
        except Exception as e:
            logger.warning(f"白名单加入提示发送失败 | chat={cid} | err={e}")
    return True


# ----------------------------------------------------------------
# /whitelist <chat_id|here> / /whitelist del <chat_id|here>
# ----------------------------------------------------------------
async def whitelist(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """通过 /whitelist <chat_id|here> 添加；/whitelist del <chat_id|here> 移除并退群。"""
    if not _check_admin(update):
        await update.message.reply_text("❌ 只有管理员才能使用此命令。")
        return

    args = context.args
    if not args:
        await update.message.reply_text(
            "用法:\n"
            "• `/whitelist <群组ID>` — 手动添加指定群组\n"
            "• `/whitelist here` — 添加当前群组\n"
            "• `/whitelist del <群组ID>` — 移出白名单并尝试退群\n"
            "• `/whitelist del here` — 将当前群移出白名单并尝试退群",
            parse_mode="Markdown",
        )
        return

    action = "add"
    target = args[0].strip()
    if target.lower() == "del":
        action = "del"
        if len(args) < 2:
            await update.message.reply_text(
                "用法：`/whitelist del <群组ID>` 或 `/whitelist del here`",
                parse_mode="Markdown",
            )
            return
        target = args[1].strip()

    if target.lower() == "here":
        chat = update.effective_chat
        if not chat or chat.type not in ("group", "supergroup"):
            await update.message.reply_text(f"❌ `/whitelist {'del ' if action == 'del' else ''}here` 请在目标群组中使用。", parse_mode="Markdown")
            return
        chat_id = str(chat.id)
        is_here = True
    else:
        chat_id = target
        is_here = False

    if action == "del":
        was_present = chat_id in _whitelist
        _whitelist.discard(chat_id)
        save_whitelist(_whitelist)
        leave_ok = False
        leave_error = ""
        try:
            await context.bot.leave_chat(int(chat_id))
            leave_ok = True
        except Exception as e:
            leave_error = str(e)
            logger.warning(f"退群失败 | chat={chat_id} | err={e}")
        status = "已移出白名单" if was_present else "原本不在白名单中"
        leave_status = "已尝试退群 ✅" if leave_ok else f"退群失败/无法退群：{leave_error[:120]}"
        await update.message.reply_text(
            f"✅ 群组 `{chat_id}` {status}。\n{leave_status}\n当前白名单共 {len(_whitelist)} 个群组。",
            parse_mode="Markdown",
        )
        return

    if chat_id in _whitelist:
        await update.message.reply_text(
            "ℹ️ 当前群组已在白名单中。" if is_here else f"ℹ️ 群组 `{chat_id}` 已在白名单中。",
            parse_mode="Markdown",
        )
        return

    await _add_whitelist_chat(chat_id, notify_bot=context.bot, notify_chat=not is_here)
    logger.info(f"➕ 群组加入白名单: {chat_id} (by {update.effective_user.id})")
    await update.message.reply_text(
        (f"✅ 当前群组（`{chat_id}`）已加入白名单！\n" if is_here else f"✅ 群组 `{chat_id}` 已加入白名单！\n")
        + f"当前白名单共 {len(_whitelist)} 个群组。",
        parse_mode="Markdown",
    )
    if is_here:
        await update.message.reply_text(_build_whitelist_onboarding_message(), parse_mode="Markdown")


async def on_bot_added_to_group(update: Update, context: ContextTypes.DEFAULT_TYPE):
    my_chat_member = update.my_chat_member
    if not my_chat_member:
        return
    chat = my_chat_member.chat
    if chat.type not in ("group", "supergroup"):
        return
    old_status = my_chat_member.old_chat_member.status
    new_status = my_chat_member.new_chat_member.status
    was_not_member = old_status in ("left", "kicked", "restricted")
    is_now_member = new_status in ("member", "administrator")
    if not (was_not_member and is_now_member):
        return
    cid = str(chat.id)
    if cid in _whitelist:
        return
    try:
        await context.bot.send_message(
            chat_id=chat.id,
            text=_build_whitelist_instruction_message(cid, chat.title or f"群组 {cid}"),
            parse_mode=ParseMode.MARKDOWN,
        )
    except TelegramError as e:
        logger.warning(f"发送白名单提示失败 chat={cid}: {e}")


def _fmt_age(ts: str | None) -> str:
    if not ts:
        return "无"
    try:
        dt = datetime.fromisoformat(ts)
        delta = datetime.now(timezone.utc) - dt
        sec = max(0, int(delta.total_seconds()))
        if sec < 60:
            return f"{sec}s前"
        if sec < 3600:
            return f"{sec//60}m前"
        return f"{sec//3600}h{(sec%3600)//60}m前"
    except Exception:
        return "未知"


# ----------------------------------------------------------------
# /bot_status — 查看 Bot 状态
# ----------------------------------------------------------------
async def bot_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """查看 Bot 运行状态"""
    if not _check_admin(update):
        await update.message.reply_text("❌ 只有管理员才能使用此命令。")
        return

    active_chats = _context_mgr.active_chats if _context_mgr else 0
    chat_id = update.effective_chat.id if update.effective_chat else 0
    focus = get_focus_store().get(chat_id)
    activity = get_activity_store().get(chat_id)
    focus_text = "✅ 激活" if focus.active else "❌ 未激活"
    hint_text = f"✅ {FOCUS_LIGHT_HINT_PROBABILITY*100:.1f}% / {FOCUS_LIGHT_HINT_COOLDOWN_SECONDS}s冷却" if FOCUS_LIGHT_HINT_ENABLED else "❌ 关闭"

    lines = [
        "🤖 Bot 状态",
        "",
        f"• 白名单群组: {len(_whitelist)} 个",
        f"• 活跃上下文: {active_chats} 个聊天",
        f"• 随机触发策略值: {get_trigger_probability()*100:.2f}%（即时群聊随机触发仍关闭）",
        f"• 触发方式: 私聊 / 回复 Bot / @Bot / 直接叫名字",
        f"• {IDLE_TOPIC_IDLE_HOURS}h 空闲引题: ✅ 启用",
        f"• 聚焦轻提示: {hint_text}",
        f"• 上下文长度: {CONTEXT_MESSAGE_COUNT} 条",
        "",
        "🧲 当前聊天聚焦状态",
        f"• 状态: {focus_text}",
        f"• 计数: {focus.trigger_count}/{FOCUS_JOIN_MAX_PER_SESSION}",
        f"• 最近刷新: {_fmt_age(focus.refreshed_at)}",
        f"• 最近用户消息: {_fmt_age(activity.last_user_at)}",
        f"• 最近 Bot 消息: {_fmt_age(activity.last_bot_at)}",
        f"• 最近空闲引题: {_fmt_age(activity.last_seed_at)}",
        "",
        f"• 管理员: {', '.join(sorted(ADMIN_IDS))}",
    ]
    await update.message.reply_text("\n".join(lines), parse_mode="Markdown")


async def shutdown_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """强制关闭 Bot（管理员）。"""
    if not _check_admin(update):
        await update.message.reply_text("❌ 只有管理员才能使用此命令。")
        return

    await update.message.reply_text("🛑 正在关闭 Bot…")
    logger.warning(f"🛑 管理员 {update.effective_user.id} 执行强制关闭")
    try:
        await context.application.stop()
    except Exception as e:
        logger.exception(f"关闭 Application 失败: {e}")
        import os, signal
        os.kill(os.getpid(), signal.SIGTERM)


def get_handlers():
    """返回所有管理员命令处理器"""
    return [
        CommandHandler("whitelist", whitelist),
        CommandHandler("bot_status", bot_status),
        CommandHandler("focus_status", bot_status),
        CommandHandler("shutdown", shutdown_bot),
        ChatMemberHandler(on_bot_added_to_group, ChatMemberHandler.MY_CHAT_MEMBER),
    ]
