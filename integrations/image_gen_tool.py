"""
AI 图片生成工具 — 供 LLM 调用的文生图/图生图能力。

后端: OpenAI-compatible images API
模型: 由 IMAGE_GEN_MODEL 配置指定
超时: 由 IMAGE_GEN_TIMEOUT 配置指定
支持: 文生图, 图生图(接收来自 Telegram 的图片作为参考)
"""
from __future__ import annotations

import base64
import logging
import os
from io import BytesIO
from typing import Optional

import httpx

from app.runtime_config import get_shared_runtime_config
from app_config.customization import get_text

logger = logging.getLogger(__name__)
_RUNTIME_CONFIG = get_shared_runtime_config()

# ── 默认图片生成 Provider ──
from app_config.config import (
    IMAGE_GEN_API_BASE as _IMAGE_GEN_DEFAULT_BASE_URL,
    IMAGE_GEN_API_KEY as _IMAGE_GEN_DEFAULT_API_KEY,
    IMAGE_GEN_MODEL as IMAGE_GEN_DEFAULT_MODEL,
    IMAGE_GEN_TIMEOUT,
)
IMAGE_GEN_SIZE = "1024x1024"
IMAGE_GEN_N = 1


def _get_effective_image_gen_config(chat_id: int | None = None) -> dict:
    """Return effective (base_url, api_key, model) for image gen."""
    cfg = _RUNTIME_CONFIG.get_effective_image_gen(chat_id)
    return {"base_url": cfg.api_base, "api_key": cfg.api_key, "model": cfg.model}


# ── Tool Definition (LLM 可见) ────────────────

IMAGE_GEN_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "generate_image",
        "description": (
            "生成一张图片。支持文生图（根据文字描述生成）和图生图（以聊天中的图片为参考生成变体）。"
            "当用户要求画图、改图、生成图片、做表情包、或需要任何视觉内容时使用。"
            "如果用户发送/转发了图片并要求修改、替换元素、重绘、做变体，请使用 mode='image_to_image'，"
            "并把上下文 `[图片 file_ids=...]` 中的完整 Telegram file_id 原样填入 reference_file_id。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "prompt": {
                    "type": "string",
                    "description": "图片描述提示词。英文效果最好。描述画面内容、风格、色调、构图等。图生图时描述想要的变化。",
                },
                "mode": {
                    "type": "string",
                    "enum": ["text_to_image", "image_to_image"],
                    "description": "生成模式。text_to_image: 纯文字生成图片。image_to_image: 基于聊天中的参考图片生成变体。只要用户说修改/替换/参考某张图，且上下文有 file_ids，就必须选 image_to_image。",
                },
                "reference_file_id": {
                    "type": "string",
                    "description": "Telegram file_id，用于指定要用作参考的图片。必须从上下文 `[图片 file_ids=...]` 中复制完整 ID；图生图/改图时必填。",
                },
                "size": {
                    "type": "string",
                    "enum": ["1024x1024", "1792x1024", "1024x1792"],
                    "description": "图片尺寸。默认 1024x1024。",
                },
            },
            "required": ["prompt"],
        },
    },
}


# ── 工具执行入口 ──────────────────────────────

async def execute_generate_image(
    prompt: str,
    mode: str = "text_to_image",
    reference_image_base64: str | None = None,
    reference_file_id: str | None = None,
    context: any = None,
    chat_id: int | None = None,
) -> str:
    """
    执行图片生成并发送到 Telegram 聊天。
    
    Args:
        prompt: 图片描述提示词
        mode: "text_to_image" 或 "image_to_image"
        reference_image_base64: 图生图时的参考图片 base64（优先于 file_id）
        reference_file_id: Telegram file_id，当无 base64 时通过 Telegram API 重新下载
        context: Telegram ContextTypes.DEFAULT_TYPE（用于发送消息和下载图片）
        chat_id: 聊天 ID
    
    Returns:
        给 LLM 的结果字符串
    """
    prompt = (prompt or "").strip()
    if not prompt:
        return get_text("image_gen.missing_prompt", "[未提供图片描述]")

    # 通过 file_id 下载参考图片（如果提供了且无 base64）
    actual_ref_b64 = reference_image_base64
    if not actual_ref_b64 and reference_file_id and context:
        try:
            file = await context.bot.get_file(reference_file_id)
            buf = BytesIO()
            await file.download_to_memory(buf)
            data = buf.getvalue()
            actual_ref_b64 = base64.b64encode(data).decode("utf-8")
            logger.info(f"🎨 通过 file_id 重新下载参考图片 | size={len(data)} bytes | fid={reference_file_id[:24]}...")
        except Exception as e:
            logger.warning(f"通过 file_id 下载参考图片失败: {e}")

    # 图生图但没有参考图 → 降级为文生图
    actual_mode = mode
    if mode == "image_to_image" and not actual_ref_b64:
        logger.warning("图生图模式但无参考图片，降级为文生图")
        actual_mode = "text_to_image"

    logger.info(
        f"🎨 开始生图 | mode={actual_mode} | chat={chat_id} | "
        f"prompt={prompt[:80]} | has_ref={bool(actual_ref_b64)}"
    )

    try:
        # 调用 OpenAI 兼容 Images API
        image_url_or_b64 = await _call_image_api(
            prompt=prompt,
            mode=actual_mode,
            reference_b64=actual_ref_b64,
            chat_id=chat_id,
        )

        if not image_url_or_b64:
            return await _handle_api_failure(prompt, context, chat_id)

        # 下载 / 解码图片
        image_bytes = await _resolve_image(image_url_or_b64)
        if not image_bytes:
            return get_text("image_gen.download_failed", "[图片生成成功但下载失败，请重试]")

        # 发送到 Telegram
        if context and chat_id:
            try:
                caption = f"🎨 {prompt[:200]}"
                if len(caption) > 200:
                    caption = caption[:197] + "..."
                await context.bot.send_photo(
                    chat_id=chat_id,
                    photo=BytesIO(image_bytes),
                    caption=caption,
                )
                logger.info(f"🎨 图片已发送 | chat={chat_id} | size={len(image_bytes)} bytes")
                return get_text("image_gen.sent_result", "[已生成图片并发送] {prompt}").format(prompt=prompt[:150])
            except Exception as e:
                logger.error(f"发送图片到 Telegram 失败: {e}")
                return get_text("image_gen.send_failed", "[图片已生成但发送失败: {error}]").format(error=e)
        else:
            # 无 Telegram 上下文（理论上不会走到这里）
            return get_text("image_gen.no_context", "[图片已生成，但无法发送到聊天] {prompt}").format(prompt=prompt[:150])

    except Exception as e:
        logger.exception(f"图片生成异常: {e}")
        return get_text("image_gen.failed", "[图片生成失败: {error}]").format(error=e)


# ── OpenAI 兼容图片 API 调用 ────────────────────────────

async def _call_image_api(
    prompt: str,
    mode: str,
    reference_b64: str | None = None,
    chat_id: int | None = None,
) -> str | None:
    """
    调用 OpenAI-compatible Images API。

    文生图走 /images/generations；图生图走 /images/edits multipart。
    很多兼容接口会忽略 generations JSON 里的 image 字段，所以不能把图生图塞进 generations。
    """
    cfg = _get_effective_image_gen_config(chat_id)
    headers = {"Authorization": f"Bearer {cfg['api_key']}"}

    try:
        async with httpx.AsyncClient(timeout=httpx.Timeout(IMAGE_GEN_TIMEOUT)) as client:
            if mode == "image_to_image" and reference_b64:
                if "," in reference_b64:
                    reference_b64 = reference_b64.split(",", 1)[1]
                image_bytes = base64.b64decode(reference_b64)
                endpoint = f"{cfg['base_url']}/images/edits"
                data = {
                    "model": cfg["model"],
                    "prompt": prompt,
                    "n": str(IMAGE_GEN_N),
                    "size": IMAGE_GEN_SIZE,
                }
                files = {
                    "image": ("reference.png", image_bytes, "image/png"),
                }
                logger.info(f"🎨 图生图 edits 调用 | ref_size={len(image_bytes)} bytes")
                resp = await client.post(endpoint, data=data, files=files, headers=headers)
            else:
                endpoint = f"{cfg['base_url']}/images/generations"
                payload = {
                    "model": cfg["model"],
                    "prompt": prompt,
                    "n": IMAGE_GEN_N,
                    "size": IMAGE_GEN_SIZE,
                }
                resp = await client.post(
                    endpoint,
                    json=payload,
                    headers={**headers, "Content-Type": "application/json"},
                )

            if resp.status_code != 200:
                logger.error(
                    f"Images API 错误 | endpoint={endpoint} | status={resp.status_code} | "
                    f"body={resp.text[:300]}"
                )
                return None

            data = resp.json()
            images = data.get("data", [])
            if images:
                img = images[0]
                url = img.get("url")
                if url:
                    return url
                b64 = img.get("b64_json")
                if b64:
                    return f"data:image/png;base64,{b64}"

            logger.warning(f"Images API 返回空图片数据 | resp={str(data)[:200]}")
            return None

    except (ValueError, base64.binascii.Error) as e:
        logger.error(f"参考图 base64 无法解码: {e}")
        return None
    except httpx.TimeoutException:
        logger.error(f"Images API 超时 ({IMAGE_GEN_TIMEOUT}s)")
        return None
    except httpx.HTTPError as e:
        logger.error(f"Images API 网络错误: {e}")
        return None
    except Exception as e:
        logger.error(f"Images API 未知异常: {e}")
        return None


# ── 图片下载 / 解码 ───────────────────────────

async def _resolve_image(url_or_b64: str) -> bytes | None:
    """解析图片 URL 或 base64 data URL 为 bytes。"""
    # base64 data URL
    if url_or_b64.startswith("data:") or not url_or_b64.startswith("http"):
        try:
            if "," in url_or_b64:
                return base64.b64decode(url_or_b64.split(",", 1)[1])
            return base64.b64decode(url_or_b64)
        except Exception as e:
            logger.error(f"Base64 解码失败: {e}")
            return None

    # HTTP URL
    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.get(url_or_b64)
            resp.raise_for_status()
            return resp.content
    except Exception as e:
        logger.error(f"下载图片失败: {url_or_b64[:80]}... | {e}")
        return None


# ── API 失败时的降级消息 ──────────────────────

async def _handle_api_failure(prompt: str, context, chat_id) -> str:
    """API 不可用时发送提示消息。"""
    fallback = (
        f"🎨 咱现在连不上画图服务喵…\n"
        f"你说的「{prompt[:80]}」咱记住了，等恢复了再画！"
    )
    if context and chat_id:
        try:
            await context.bot.send_message(chat_id=chat_id, text=fallback)
        except Exception:
            pass
    return get_text("image_gen.api_unavailable", "[Image API 不可用] {prompt}").format(prompt=prompt[:150])
