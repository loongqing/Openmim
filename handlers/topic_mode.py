"""
话题模式 — 追踪群聊深入话题
/topic 激活，/notopic 关闭，LLM 也可通过 start_topic/stop_topic 工具控制
"""
import logging

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from app_config.customization import get_text
from stores.playables_db import DB_PATH, TopicModeRow, orm_session
from stores.timestamps import utc_now_iso

logger = logging.getLogger(__name__)


def is_topic_active(chat_id: str) -> bool:
    with orm_session(DB_PATH) as session:
        row = session.get(TopicModeRow, str(chat_id))
        return bool(row and row.active)


def get_topic_info(chat_id: str) -> tuple[bool, str, str]:
    with orm_session(DB_PATH) as session:
        row = session.get(TopicModeRow, str(chat_id))
        if row and row.active:
            return True, row.topic_name or "", row.started_at
        return False, "", ""


def activate_topic(chat_id: str, topic_name: str = "") -> str:
    started = utc_now_iso()
    with orm_session(DB_PATH) as session:
        row = session.get(TopicModeRow, str(chat_id))
        if row is None:
            session.add(TopicModeRow(chat_id=str(chat_id), active=1, topic_name=topic_name, started_at=started))
        else:
            row.active = 1
            row.topic_name = topic_name
            row.started_at = started
    logger.info(f"💬 话题模式激活: chat={chat_id} topic={topic_name}")
    return topic_name


def deactivate_topic(chat_id: str) -> bool:
    was_active = is_topic_active(chat_id)
    with orm_session(DB_PATH) as session:
        row = session.get(TopicModeRow, str(chat_id))
        if row is not None:
            row.active = 0
    if was_active:
        logger.info(f"💬 话题模式关闭: chat={chat_id}")
    return was_active


def update_topic_name(chat_id: str, topic_name: str):
    with orm_session(DB_PATH) as session:
        row = session.get(TopicModeRow, str(chat_id))
        if row is not None:
            row.topic_name = topic_name
    logger.info(f"💬 话题名更新: chat={chat_id} topic={topic_name}")


async def cmd_topic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    if is_topic_active(chat_id):
        await update.message.reply_text("这个话题我已经在持续追踪了，想换新的就先 /notopic。")
        return
    from llm.llm_client import get_llm_client
    try:
        llm = get_llm_client()
        prompt = "群聊用户手动激活了话题追踪模式。请为当前讨论的话题取一个简短的名字（10字以内），直接回复话题名即可，不要多余内容。"
        topic_name = await llm.generate_text(prompt, max_tokens=30, temperature=0.5)
        topic_name = topic_name.strip().strip('"').strip("'").strip("「」").strip()
        if not topic_name or len(topic_name) > 20:
            topic_name = "当前话题"
    except Exception:
        topic_name = "当前话题"
    activate_topic(chat_id, topic_name)
    logger.info(f"💬 手动激活话题模式: chat={chat_id} topic={topic_name}")
    ctx = context.application.bot_data.get("context_mgr")
    ctx_total = len(await ctx.get_context(update.effective_chat.id)) if ctx else 0
    await update.message.reply_text(
        get_text("commands.topic.enabled", "💬 **话题追踪已开启**\n\n📌 话题：**{topic_name}**\n📊 上下文：{ctx_total} 条（全部输入）\n\n咱会顺着这个话题继续往下聊喵，想收尾的话就 /notopic。").format(topic_name=topic_name, ctx_total=ctx_total),
        parse_mode="Markdown")


async def cmd_notopic(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_message or not update.effective_chat:
        return
    chat_id = str(update.effective_chat.id)
    if not is_topic_active(chat_id):
        await update.message.reply_text("现在还没有正在追踪的固定话题。")
        return
    _, topic_name, _ = get_topic_info(chat_id)
    deactivate_topic(chat_id)
    await update.message.reply_text(f"🚫 **话题追踪已关闭**\n\n刚才那个「{topic_name}」就先聊到这里。", parse_mode="Markdown")


def get_handlers():
    return [CommandHandler("topic", cmd_topic), CommandHandler("notopic", cmd_notopic)]
