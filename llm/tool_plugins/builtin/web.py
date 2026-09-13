from __future__ import annotations

from plugins.base import ToolContext, ToolSpec as ToolPlugin
from integrations.web_search import (
    FETCH_URL_TOOL_DEFINITION,
    GET_CURRENT_TIME_TOOL_DEFINITION,
    LOOKUP_GROUP_USER_TOOL_DEFINITION,
    SEARCH_TOOL_DEFINITION,
    fetch_url_content,
    get_current_time,
    lookup_group_user,
    search_web,
)


async def execute_get_current_time(args: dict, ctx: ToolContext) -> str:
    return await get_current_time()


async def execute_search(args: dict, ctx: ToolContext) -> str:
    return await search_web(args.get("query", ""), chat_id=ctx.chat_id)


async def execute_fetch(args: dict, ctx: ToolContext) -> str:
    return await fetch_url_content(args.get("url", ""))


async def execute_lookup(args: dict, ctx: ToolContext) -> str:
    return await lookup_group_user(args.get("query", ""), chat_id=ctx.chat_id)


TOOLS = [
    ToolPlugin("get_current_time", GET_CURRENT_TIME_TOOL_DEFINITION, execute_get_current_time, plugin="web"),
    ToolPlugin("search_web", SEARCH_TOOL_DEFINITION, execute_search, plugin="web"),
    ToolPlugin("fetch_url", FETCH_URL_TOOL_DEFINITION, execute_fetch, plugin="web"),
    ToolPlugin("lookup_group_user", LOOKUP_GROUP_USER_TOOL_DEFINITION, execute_lookup, plugin="web"),
]
