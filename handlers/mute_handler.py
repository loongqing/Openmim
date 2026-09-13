"""
静音指令 — /muteme 忽略我的消息，/unmuteme 取消忽略
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from app_config.customization import get_text
from stores.playables_db import DB_PATH, MutedUserRow, orm_session
from stores.timestamps import utc_now_iso

logger = logging.getLogger(__name__)


def is_muted(chat_id: str, user_id: str) -> bool:
    """检查用户是否在指定群被静音。"""
    with orm_session(DB_PATH) as session:
        return session.get(MutedUserRow, (str(chat_id), str(user_id))) is not None


async def cmd_muteme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/muteme — 让 Bot 忽略你的消息"""
    if not update.effective_user or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id)

    if is_muted(chat_id, user_id):
        await update.message.reply_text(get_text("commands.mute.already_muted", "你已经把咱按住不让搭话啦喵，想恢复就发 /unmuteme。"))
        return

    with orm_session(DB_PATH) as session:
        if session.get(MutedUserRow, (chat_id, user_id)) is None:
            session.add(MutedUserRow(chat_id=chat_id, user_id=user_id, muted_at=utc_now_iso()))

    logger.info(f"🔇 用户静音: chat={chat_id} user={user_id}")
    await update.message.reply_text(get_text("commands.mute.muted", "🔇 好哦，咱先不回你啦喵。想把咱叫回来就发 /unmuteme。"))


async def cmd_unmuteme(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """/unmuteme — 让 Bot 恢复回复你的消息"""
    if not update.effective_user or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id)

    if not is_muted(chat_id, user_id):
        await update.message.reply_text(get_text("commands.mute.not_muted", "你现在本来就没把咱静音呀喵。"))
        return

    with orm_session(DB_PATH) as session:
        row = session.get(MutedUserRow, (chat_id, user_id))
        if row is not None:
            session.delete(row)

    logger.info(f"🔊 用户取消静音: chat={chat_id} user={user_id}")
    await update.message.reply_text(get_text("commands.mute.unmuted", "🔊 收到喵，咱回来继续陪你说话啦。"))


def get_handlers():
    return [
        CommandHandler("muteme", cmd_muteme),
        CommandHandler("unmuteme", cmd_unmuteme),
    ]
