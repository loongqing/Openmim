from __future__ import annotations

from io import BytesIO

from plugins.base import ToolContext, ToolSpec as ToolPlugin
from integrations.web_search import READ_FILE_TOOL_DEFINITION

MAX_FILE_SIZE = 10 * 1024


async def execute_read_file(args: dict, ctx: ToolContext) -> str:
    file_id = args.get("file_id", "")
    if not file_id:
        return "[错误：缺少 file_id]"
    try:
        tg_ctx = ctx.telegram_context
        if tg_ctx is None:
            return "[错误：无法访问 Telegram 上下文]"
        file = await tg_ctx.bot.get_file(file_id)
        buf = BytesIO()
        await file.download_to_memory(buf)
        data = buf.getvalue()
        if len(data) > MAX_FILE_SIZE:
            return f"[错误：文件过大 {len(data)} bytes，上限 {MAX_FILE_SIZE} bytes]"
        try:
            return data.decode("utf-8")
        except UnicodeDecodeError:
            return f"[错误：文件不是 UTF-8 文本（{len(data)} bytes，可能是二进制文件）]"
    except Exception as e:
        return f"[错误：读取文件失败：{e}]"


TOOLS = [ToolPlugin("read_file", READ_FILE_TOOL_DEFINITION, execute_read_file, plugin="file")]
