"""应用启动编排。

这一层把初始化动作和 handler 注册从主入口中剥离出来。
第一阶段仍复用旧模块实现，重点是收敛启动时序。
"""

import asyncio
from telegram.ext import Application

from app_config.config import (
    BOT_TOKEN,
    CONTEXT_MAX_TEXT_CHARS,
    BOT_CONTEXT_MAX_CHARS,
    load_whitelist,
    save_whitelist,
    TELEGRAM_CONCURRENT_UPDATES,
    STICKER_SETS,
)
from stores.context_manager import ContextManager
from stores.group_settings_store import get_group_disabled_tools
from features.sticker_manager import StickerManager
from stores.playables_db import init_db as init_playables_db
from stores.persona_memory import init_db as init_persona_memory_db
from stores.human_behavior import init_db as init_human_behavior_db
from handlers.admin_panel import inject as inject_admin_panel
from handlers.group_admin_panel import (
    inject_whitelist as inject_whitelist_to_group_admin,
    get_chat_member_handlers,
    get_handlers as get_group_admin_handlers,
)
from handlers.admin_panel import get_handlers as get_panel_handlers
from handlers.mute_handler import get_handlers as get_mute_handlers
from handlers.topic_mode import get_handlers as get_topic_handlers
from handlers.quiet_mode import get_handlers as get_quiet_handlers
from handlers.about_handler import get_handlers as get_about_handlers
from handlers.start_handler import get_handlers as get_start_handlers
from handlers.raw_update_router import get_handlers as get_raw_update_router_handlers
from app.build_info import load_build_info

from app.container import AppContext
from app.runtime_config import get_shared_runtime_config
from plugins.manager import load_plugins, set_plugin_manager
from handlers.chat_handler import init_handler as init_chat_handler, get_handler as get_chat_handler
from handlers.admin_handler import init_admin, get_handlers as get_admin_handlers
from handlers.business_handler import get_handlers as get_business_handlers
from handlers.private_text_router import get_handlers as get_private_text_router_handlers


def build_runtime_context() -> AppContext:
    runtime_config = get_shared_runtime_config()

    init_playables_db()
    init_persona_memory_db()
    init_human_behavior_db()

    whitelist = load_whitelist()
    build_info = load_build_info()
    plugin_manager = load_plugins(
        disabled_plugins=getattr(
            __import__("app_config.config", fromlist=["PLUGINS_DISABLED"]),
            "PLUGINS_DISABLED",
            set(),
        ),
        get_chat_disabled_tools=get_group_disabled_tools,
    )
    set_plugin_manager(plugin_manager)

    context_mgr = ContextManager()

    async def _startup_compact():
        for cid in await context_mgr.chat_ids():
            await context_mgr.compact_chat(cid, CONTEXT_MAX_TEXT_CHARS, BOT_CONTEXT_MAX_CHARS)

    asyncio.run(_startup_compact())

    sticker_mgr = StickerManager(STICKER_SETS)
    init_chat_handler(context_mgr, sticker_mgr, whitelist)
    init_admin(whitelist, save_cb=lambda: save_whitelist(whitelist), context_mgr=context_mgr)
    inject_admin_panel(whitelist, save_cb=lambda: save_whitelist(whitelist))
    inject_whitelist_to_group_admin(whitelist)

    return AppContext(
        runtime_config=runtime_config,
        context_mgr=context_mgr,
        sticker_mgr=sticker_mgr,
        whitelist=whitelist,
        build_info=build_info,
        plugin_manager=plugin_manager,
    )


def build_application(post_init_callback, post_shutdown_callback=None) -> tuple[Application, AppContext]:
    ctx = build_runtime_context()

    builder = (
        Application.builder()
        .token(BOT_TOKEN)
        .post_init(post_init_callback)
        .concurrent_updates(max(1, TELEGRAM_CONCURRENT_UPDATES))
    )
    if post_shutdown_callback is not None:
        builder = builder.post_shutdown(post_shutdown_callback)
    application = builder.build()

    ctx.application = application
    application.bot_data["context_mgr"] = ctx.context_mgr
    application.bot_data["sticker_mgr"] = ctx.sticker_mgr
    application.bot_data["whitelist"] = ctx.whitelist
    application.bot_data["runtime_config"] = ctx.runtime_config
    application.bot_data["build_info"] = getattr(ctx, "build_info", None)
    application.bot_data["plugin_manager"] = getattr(ctx, "plugin_manager", None)
    application.bot_data["app_context"] = ctx

    register_handlers(application)
    return application, ctx


def register_handlers(application: Application) -> None:
    for h in get_admin_handlers():
        application.add_handler(h, group=-1)
    for h in get_panel_handlers():
        application.add_handler(h, group=-1)
    for h in get_group_admin_handlers():
        application.add_handler(h, group=-1)
    for h in get_chat_member_handlers():
        application.add_handler(h, group=-1)
    plugin_manager = application.bot_data.get("plugin_manager")
    if plugin_manager:
        for plugin in plugin_manager.plugins:
            if not plugin_manager.is_plugin_enabled(plugin.name):
                continue
            for h in getattr(plugin, "handlers", ()):
                application.add_handler(h, group=-1)
    for h in get_about_handlers():
        application.add_handler(h, group=0)
    for h in get_start_handlers():
        application.add_handler(h, group=0)
    for h in get_mute_handlers():
        application.add_handler(h, group=0)
    for h in get_quiet_handlers():
        application.add_handler(h, group=0)
    for h in get_topic_handlers():
        application.add_handler(h, group=0)
    # /fortune 仍是 core 可玩性命令；历史猜图已迁入 history_guess 插件。
    from features.fortune import get_handlers as get_fortune_handlers
    for h in get_fortune_handlers():
        application.add_handler(h, group=0)
    # business handlers 内含 TypeHandler(Update)，匹配一切 update。
    # 与 raw_update_router 同理：若放在 group=-1 会在同组内第一个命中并 break，
    # 致使同组的 private_text_router 永远轮不到执行（点"新增记忆"后输入无反应即此因）。
    # 故隔离到独立 group=-3，使其与私聊文本路由互不干扰。
    for h in get_business_handlers():
        application.add_handler(h, group=-3)
    # raw_update_router 使用 TypeHandler(Update) 匹配一切 update，
    # 必须放在独立 group=-2，否则同 group 内它会吞掉所有消息，
    # 导致后面的 private_text_router 永远轮不到执行。
    for h in get_raw_update_router_handlers():
        application.add_handler(h, group=-2)
    # 私聊文本统一路由器：group=-1 内唯一的私聊纯文本入口，
    # 按优先级分发各面板待输入，未命中则放行给 group=1 的聊天 handler。
    # 必须在 business handlers 之后注册，且使用默认 block=True，
    # 以保证其 ApplicationHandlerStop 能真正阻止消息下传。
    for h in get_private_text_router_handlers():
        application.add_handler(h, group=-1)
    application.add_handler(get_chat_handler(), group=1)
