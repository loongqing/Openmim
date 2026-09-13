"""新主入口。

当前阶段作为对旧 main.py 的轻量重组：
- 保留旧模块能力
- 把初始化与注册流程迁入 app/bootstrap.py
- 后续逐步把 post_init / scheduler / services 下沉
"""

import logging
import sys
import asyncio
from datetime import time, timezone, timedelta

from telegram import BotCommand
from app_config.customization import get_dict, get_text
from telegram.ext import ContextTypes

from app_config.config import (
    ADMIN_IDS,
    BUSINESS_ENABLED,
    TELEGRAM_CONCURRENT_UPDATES,
    validate_config,
    log_config,
    LOG_LEVEL,
)
from app.bootstrap import build_application
from features.idle_topic_scheduler import (
    start_idle_topic_loop,
    stop_idle_topic_loop,
)
from llm.llm_client import close_llm_client
from integrations.e2b_tool import (
    start_cleanup_task,
    stop_cleanup_task,
)
from integrations.scheduler_tool import (
    set_application,
    cancel_all_tasks,
)
from stores.group_settings_store import get_group_settings
from features.playables import send_greeting
from features.history_guess import stop_prepare_guess_task

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=getattr(logging, LOG_LEVEL, logging.INFO),
)
for lib in ("httpx", "httpcore", "telegram.ext", "telegram.vendor"):
    logging.getLogger(lib).setLevel(logging.WARNING)

logger = logging.getLogger("ChatBot")


async def morning_greeting_job(context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    whitelist = app.bot_data.get("whitelist", set())
    for cid in whitelist:
        try:
            if get_group_settings(cid).get("morning_greeting_enabled", "true") == "false":
                continue
            await send_greeting(app.bot, int(cid), is_morning=True)
            await asyncio.sleep(1)
        except Exception as e:
            logger.warning(f"早安发送失败 chat={cid}: {e}")


async def evening_greeting_job(context: ContextTypes.DEFAULT_TYPE):
    app = context.application
    whitelist = app.bot_data.get("whitelist", set())
    for cid in whitelist:
        try:
            if get_group_settings(cid).get("evening_greeting_enabled", "true") == "false":
                continue
            await send_greeting(app.bot, int(cid), is_morning=False)
            await asyncio.sleep(1)
        except Exception as e:
            logger.warning(f"晚安发送失败 chat={cid}: {e}")


async def post_init(application):
    bot = application.bot
    plugin_manager = application.bot_data.get("plugin_manager")
    if plugin_manager:
        await plugin_manager.startup(application.bot_data.get("app_context"), application)
    default_command_descriptions = {
        "start": "开始使用机器人",
        "admin": "打开管理面板（管理员）",
        "settings": "Business Chatbot 设置",
        "gadmin": "群组管理面板（群管理员）",
        "muteme": "让机器人忽略我的消息",
        "unmuteme": "让机器人恢复回复我",
        "quiet": "安静：不主动聚焦插话",
        "unquiet": "恢复主动聚焦插话",
        "topic": "进入话题追踪模式",
        "notopic": "退出话题追踪模式",
        "fortune": "每日运势",
        "guesshistory": "看图猜时代/地区",
        "about": "关于机器人",
    }
    command_descriptions = get_dict("bot_commands", default_command_descriptions)
    bot_commands = [BotCommand(name, str(desc)) for name, desc in command_descriptions.items()]
    try:
        await bot.set_my_commands(bot_commands)
        logger.info(get_text("messages.commands_synced_log", "✅ Commands synced"))
    except Exception as e:
        logger.warning(get_text("messages.commands_sync_failed_log", "Failed to sync commands: {error}").format(error=e))

    sticker_mgr = application.bot_data["sticker_mgr"]
    await sticker_mgr.load(bot)

    try:
        if application.job_queue is not None:
            whitelist_for_jobs = application.bot_data.get("whitelist", set())
            registered_jobs = []
            if any((get_group_settings(cid).get("morning_greeting_enabled", "true") != "false") for cid in whitelist_for_jobs):
                application.job_queue.run_daily(
                    morning_greeting_job,
                    time=time(8, 0, tzinfo=timezone(timedelta(hours=8))),
                    name="morning_greeting",
                )
                registered_jobs.append("早安(8:00)")
            if any((get_group_settings(cid).get("evening_greeting_enabled", "true") != "false") for cid in whitelist_for_jobs):
                application.job_queue.run_daily(
                    evening_greeting_job,
                    time=time(23, 0, tzinfo=timezone(timedelta(hours=8))),
                    name="evening_greeting",
                )
                registered_jobs.append("晚安(23:00)")
            if registered_jobs:
                logger.info("⏰ 已注册%s定时问候（按群开关）", "/".join(registered_jobs))
        else:
            logger.warning("JobQueue 不可用，早晚定时问候未注册")
    except Exception as e:
        logger.warning(f"注册定时问候失败: {e}")

    wl_count = len(application.bot_data.get("whitelist", set()))
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                chat_id=int(admin_id),
                text=get_text(
                    "messages.startup_admin",
                    "🤖 **Bot is online!**\n\n"
                    "• Stickers: {stickers_loaded}\n"
                    "• Whitelisted chats: {whitelist_count}\n"
                    "• Business: {business_enabled}\n"
                    "• Admin panel: `/admin`\n"
                    "• Group panel: `/gadmin`",
                ).format(
                    stickers_loaded="✅" if sticker_mgr.is_loaded else "❌",
                    whitelist_count=wl_count,
                    business_enabled="✅" if BUSINESS_ENABLED else "❌",
                ),
                parse_mode="Markdown",
            )
        except Exception:
            pass

    try:
        start_idle_topic_loop(application)
        logger.info("🪄 已启动 idle topic loop")
    except Exception as e:
        logger.warning(f"启动 idle topic loop 失败: {e}")

    try:
        start_cleanup_task()
    except Exception as e:
        logger.warning(f"启动 E2B 沙箱清理失败: {e}")

    try:
        set_application(application)
        logger.info("⏰ 已注册定时任务调度器")
    except Exception as e:
        logger.warning(f"注册定时任务调度器失败: {e}")

    logger.info("=" * 50)
    logger.info(get_text("messages.startup_log", "🤖 Bot startup complete!"))
    logger.info(f"  白名单: {wl_count} 个群组")
    logger.info(f"  Business:  {'✅' if BUSINESS_ENABLED else '❌'}")
    logger.info(f"  Concurrent updates: {TELEGRAM_CONCURRENT_UPDATES}")
    logger.info("=" * 50)


async def post_shutdown(application) -> None:
    """Close every application-owned resource on the polling event loop."""
    bot_data = getattr(application, "bot_data", {}) or {}
    # Stop producers before tearing down the resources they can call into.
    try:
        await stop_idle_topic_loop()
    except Exception:
        logger.exception("idle topic loop 关闭失败")
    try:
        await cancel_all_tasks()
    except Exception:
        logger.exception("定时任务关闭失败")
    try:
        await stop_prepare_guess_task()
    except Exception:
        logger.exception("猜图预热任务关闭失败")
    plugin_manager = bot_data.get("plugin_manager")
    if plugin_manager is not None:
        try:
            await plugin_manager.shutdown(bot_data.get("app_context"), application)
        except Exception:
            logger.exception("插件关闭失败")
    try:
        await stop_cleanup_task()
    except Exception:
        logger.exception("Sandbox 清理任务关闭失败")
    try:
        await close_llm_client()
    except Exception:
        logger.exception("LLM client 关闭失败")
    finally:
        set_application(None)


def main():
    errors = validate_config()
    if errors:
        for e in errors:
            logger.error(f"❌ {e}")
        if "BOT_TOKEN" in " ".join(errors):
            sys.exit(1)

    log_config()
    application, ctx = build_application(post_init, post_shutdown)
    logger.info(get_text(
        "messages.launching_log",
        "🚀 Starting Telegram Chat Bot... (concurrent_updates={concurrent_updates}, business={business})",
    ).format(
        concurrent_updates=ctx.runtime_config.concurrent_updates,
        business="on" if ctx.runtime_config.business_enabled else "off",
    ))
    application.run_polling(
        allowed_updates=[
            "message",
            "my_chat_member",
            "callback_query",
            "chat_member",
            "business_message",
            "business_connection",
            "guest_message",
        ]
    )
    return application


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        logger.info("👋 Bot 已停止")
