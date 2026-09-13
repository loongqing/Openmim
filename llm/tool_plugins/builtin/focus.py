from __future__ import annotations

from plugins.base import ToolContext, ToolSpec as ToolPlugin
from integrations.web_search import (
    SUPPRESS_FOCUS_MODE_TOOL_DEFINITION,
    UPDATE_FOCUS_CRITERIA_TOOL_DEFINITION,
    suppress_focus_mode,
    update_focus_criteria,
)


async def execute_update_focus(args: dict, ctx: ToolContext) -> str:
    return await update_focus_criteria(args.get("criteria_note", ""), chat_id=ctx.chat_id)


async def execute_suppress_focus(args: dict, ctx: ToolContext) -> str:
    return await suppress_focus_mode(
        enabled=bool(args.get("enabled", True)),
        reason=args.get("reason", ""),
        chat_id=ctx.chat_id,
    )


TOOLS = [
    ToolPlugin("update_focus_criteria", UPDATE_FOCUS_CRITERIA_TOOL_DEFINITION, execute_update_focus, plugin="focus"),
    ToolPlugin("suppress_focus_mode", SUPPRESS_FOCUS_MODE_TOOL_DEFINITION, execute_suppress_focus, plugin="focus"),
]
