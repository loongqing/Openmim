from __future__ import annotations

from plugins.base import ToolContext, ToolSpec as ToolPlugin
from integrations.scheduler_tool import SCHEDULE_TASK_TOOLS, schedule_task

SCHEDULE_TASK_TOOL_DEFINITION = SCHEDULE_TASK_TOOLS[0]


async def execute_schedule(args: dict, ctx: ToolContext) -> str:
    return await schedule_task(
        chat_id=ctx.chat_id or 0,
        message=args.get("message", ""),
        action=args.get("action", "create"),
        delay_minutes=args.get("delay_minutes"),
        trigger_at=args.get("trigger_at"),
        cron=args.get("cron"),
        task_id=args.get("task_id"),
    )


TOOLS = [ToolPlugin("schedule_task", SCHEDULE_TASK_TOOL_DEFINITION, execute_schedule, plugin="scheduler")]
