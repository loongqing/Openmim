"""每日运势 — /fortune 命令。

从 features/playables.py 拆分而来。
"""
import logging
import random
from datetime import datetime, timezone

from telegram import Update
from telegram.ext import ContextTypes, CommandHandler

from stores.playables_db import DB_PATH, DailyFortuneRow, orm_session
from stores.timestamps import utc_now_iso
from llm.llm_client import get_llm_client
from features.playables import _play_text, _play_list, _play_dict

logger = logging.getLogger(__name__)

async def cmd_fortune(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_chat or not update.effective_user or not update.effective_message:
        return
    chat_id = str(update.effective_chat.id)
    user_id = str(update.effective_user.id)
    today = datetime.now(timezone.utc).date().isoformat()

    with orm_session(DB_PATH) as session:
        row = session.get(DailyFortuneRow, (chat_id, user_id, today))
        if row:
            text = row.fortune_text
            fortune_level = row.fortune_level
            lucky_number = row.lucky_number
            lucky_color = row.lucky_color
        else:
            lucky_number = random.randint(1, 99)
            lucky_color = random.choice(_play_list("fortune.lucky_colors", ["蓝色", "紫色", "金色", "绿色", "白色", "粉色", "黑色"]))

            # 程序随机运势等级（加权：大吉稀有，中吉小吉常见）
            fortune_level = random.choices(
                ["大吉", "中吉", "小吉", "吉", "末吉", "凶"],
                weights=[8, 22, 28, 25, 12, 5],
                k=1,
            )[0]

            # LLM 生成猫娘运势文案
            prompt = _play_text(
                "fortune.prompt",
                "你是一只可爱的占卜师。请根据运势等级「{fortune_level}」，写一句今日运势（1-2句话即可）。\n"
                "要求：可爱灵动，内容要和等级匹配。只输出运势文案本身，不要加前缀或解释。",
            ).format(fortune_level=fortune_level)
            try:
                text = await get_llm_client().generate_text(prompt, max_tokens=120, temperature=0.9)
                if not text or len(text) < 3:
                    raise ValueError("LLM 返回空或过短")
            except Exception:
                logger.exception("LLM 运势生成失败，使用兜底文案")
                text = _play_text("fortune.fallback_text", "今天喵运平平，但有咱陪着你，什么都不怕喵～")

            session.add(DailyFortuneRow(
                chat_id=chat_id,
                user_id=user_id,
                date=today,
                fortune_text=text,
                fortune_level=fortune_level,
                lucky_number=lucky_number,
                lucky_color=lucky_color,
                created_at=utc_now_iso(),
            ))

    level_emoji = _play_dict("fortune.level_emojis", {"大吉": "🎉", "中吉": "😸", "小吉": "🍀", "吉": "✨", "末吉": "🌤️", "凶": "💧"}).get(fortune_level, "🔮")
    await update.effective_message.reply_text(_play_text(
        "fortune.reply_template",
        "🐾 今日猫猫运势喵～\n\n{level_emoji} **{fortune_level}**\n\n🔮 {text}\n\n🍀 幸运数字：{lucky_number}\n🎨 幸运色：{lucky_color}",
    ).format(level_emoji=level_emoji, fortune_level=fortune_level, text=text, lucky_number=lucky_number, lucky_color=lucky_color))



def get_handlers():
    return [
        CommandHandler("fortune", cmd_fortune),
    ]

