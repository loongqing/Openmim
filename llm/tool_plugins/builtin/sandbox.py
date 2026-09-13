from __future__ import annotations

from plugins.base import ToolContext, ToolSpec as ToolPlugin
from integrations.e2b_tool import E2B_TOOLS, execute_run_python, execute_run_shell

RUN_PYTHON_TOOL_DEFINITION, RUN_SHELL_TOOL_DEFINITION = E2B_TOOLS


async def execute_python(args: dict, ctx: ToolContext) -> str:
    return await execute_run_python(args.get("code", ""), chat_id=ctx.chat_id)


async def execute_shell(args: dict, ctx: ToolContext) -> str:
    return await execute_run_shell(args.get("command", ""), chat_id=ctx.chat_id)


TOOLS = [
    ToolPlugin("run_python", RUN_PYTHON_TOOL_DEFINITION, execute_python, plugin="sandbox"),
    ToolPlugin("run_shell", RUN_SHELL_TOOL_DEFINITION, execute_shell, plugin="sandbox"),
]
