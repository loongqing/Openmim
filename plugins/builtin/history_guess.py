from __future__ import annotations

from telegram.ext import CallbackQueryHandler, CommandHandler, MessageHandler, filters

from plugins.base import BotPlugin, MessageBuildHookContext
from features.history_guess import (
    COMMAND_STARTS,
    choose_history_guess_difficulty,
    get_active_game_context,
    handle_history_guess_reply,
    history_guess_difficulty_callback,
    start_history_guess_by_text,
)


class HistoryGuessPlugin(BotPlugin):
    name = "history_guess"
    priority = 80
    handlers = (
        CommandHandler(COMMAND_STARTS, choose_history_guess_difficulty),
        CallbackQueryHandler(history_guess_difficulty_callback, pattern=r"^history_guess:(low|hard)$"),
        MessageHandler(filters.Regex(r"^(猜时代|猜历史|开始猜图|历史猜图)(?:\s|$)"), start_history_guess_by_text),
        MessageHandler(filters.REPLY & (filters.TEXT | filters.Caption()), handle_history_guess_reply),
    )

    async def before_build_messages(self, ctx: MessageBuildHookContext) -> None:
        game_note = get_active_game_context(ctx.chat_id)
        if game_note:
            ctx.dynamic_blocks.append(game_note)


PLUGIN = HistoryGuessPlugin()
