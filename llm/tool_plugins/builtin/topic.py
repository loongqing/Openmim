from __future__ import annotations

from plugins.base import ToolContext, ToolSpec as ToolPlugin
from integrations.web_search import START_TOPIC_TOOL_DEFINITION, STOP_TOPIC_TOOL_DEFINITION, start_topic, stop_topic


async def execute_start_topic(args: dict, ctx: ToolContext) -> str:
    return await start_topic(args.get("topic_name", ""), chat_id=ctx.chat_id)


async def execute_stop_topic(args: dict, ctx: ToolContext) -> str:
    return await stop_topic(chat_id=ctx.chat_id)


TOOLS = [
    ToolPlugin("start_topic", START_TOPIC_TOOL_DEFINITION, execute_start_topic, plugin="topic"),
    ToolPlugin("stop_topic", STOP_TOPIC_TOOL_DEFINITION, execute_stop_topic, plugin="topic"),
]
