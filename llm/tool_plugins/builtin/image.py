from __future__ import annotations

from plugins.base import ToolContext, ToolSpec as ToolPlugin
from integrations.image_gen_tool import IMAGE_GEN_TOOL_DEFINITION, execute_generate_image


async def execute_image(args: dict, ctx: ToolContext) -> str:
    prompt = args.get("prompt", "")
    mode = args.get("mode", "text_to_image")
    ref_file_id = args.get("reference_file_id") or getattr(ctx, "reference_file_id", None)
    ref_image = getattr(ctx, "reference_image_base64", None)
    if mode == "text_to_image" and (ref_file_id or ref_image):
        mode = "image_to_image"
    return await execute_generate_image(
        prompt=prompt,
        mode=mode,
        reference_image_base64=ref_image,
        reference_file_id=ref_file_id,
        context=ctx.telegram_context,
        chat_id=ctx.chat_id,
    )


TOOLS = [ToolPlugin("generate_image", IMAGE_GEN_TOOL_DEFINITION, execute_image, plugin="image")]
