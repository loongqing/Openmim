from __future__ import annotations

from plugins.base import ToolContext, ToolSpec as ToolPlugin
from integrations.web_search import (
    DELETE_GROUP_FACT_TOOL_DEFINITION,
    REMEMBER_GROUP_FACT_TOOL_DEFINITION,
    UPDATE_GROUP_FACT_TOOL_DEFINITION,
    delete_group_fact,
    remember_group_fact,
    update_group_fact,
)


async def execute_remember(args: dict, ctx: ToolContext) -> str:
    return await remember_group_fact(
        content=args.get("content", ""),
        key=args.get("key", ""),
        scope=args.get("scope", "chat"),
        chat_id=ctx.chat_id,
    )


async def execute_delete(args: dict, ctx: ToolContext) -> str:
    return await delete_group_fact(
        memory_id=args.get("memory_id"),
        key=args.get("key", ""),
        query=args.get("query", ""),
        chat_id=ctx.chat_id,
    )


async def execute_update(args: dict, ctx: ToolContext) -> str:
    return await update_group_fact(
        content=args.get("content", ""),
        memory_id=args.get("memory_id"),
        key=args.get("key", ""),
        query=args.get("query", ""),
        chat_id=ctx.chat_id,
    )


TOOLS = [
    ToolPlugin("remember_group_fact", REMEMBER_GROUP_FACT_TOOL_DEFINITION, execute_remember, plugin="memory"),
    ToolPlugin("delete_group_fact", DELETE_GROUP_FACT_TOOL_DEFINITION, execute_delete, plugin="memory"),
    ToolPlugin("update_group_fact", UPDATE_GROUP_FACT_TOOL_DEFINITION, execute_update, plugin="memory"),
]
