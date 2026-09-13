"""Business 模式 LLM 调用（从 llm/llm_client.py 拆分）。

与 LLMClient.business_chat 等价；作为独立 async 函数接收 LLMClient 实例。
"""
import httpx

from app_config.customization import get_text
from llm.llm_client import (
    LLMClient,
    _RUNTIME_CONFIG,
    _record_usage_if_present,
    _safe_http_error_message,
    logger,
)
from llm.provider_protocols import (
    build_request,
    normalize_provider,
    parse_response,
    provider_endpoint,
    provider_headers,
)


async def business_chat(
    client: LLMClient,
    owner_name: str,
    owner_id: str | int,
    other_name: str,
    message_text: str,
    context_messages: list[str] | None = None,
    control_hint: str = "",
) -> str:
    """Business Chatbot 专用：简单非流式对话，无工具调用，per-user 自定义 LLM 配置。

    Args:
        owner_name: 业务账号所有者的脱敏名称
        owner_id: 业务账号所有者的 Telegram ID（用于读取 per-user 设置）
        other_name: 发消息的终端用户的脱敏名称
        message_text: 终端用户发来的消息文本
    Returns:
        LLM 生成的纯文本回复
    """
    from stores.business_settings import get_user_settings
    from features.business_prompt import build_system_prompt

    uid = str(owner_id)
    settings = get_user_settings(uid)

    effective_base = settings.effective_api_base()
    effective_key = settings.effective_api_key()
    effective_model = settings.effective_model()
    effective_provider = normalize_provider(settings.effective_provider())

    http_client = httpx.AsyncClient(
        base_url=effective_base,
        timeout=httpx.Timeout(_RUNTIME_CONFIG.llm_timeout),
        headers=provider_headers(effective_provider, effective_key),
    )

    try:
        custom_persona = settings.persona if settings.has_custom_persona() else ""
        system_prompt = build_system_prompt(owner_name, custom_persona)

        messages = [{"role": "system", "content": system_prompt}]

        # ── 注入上下文（如果有）──
        if context_messages:
            context_block = get_text("llm.business_context_prefix", "下面是最近的对话历史，按时间从旧到新排列。请用它理解上下文：\n{context}").format(context="\n".join(context_messages))
            messages.append({"role": "user", "content": context_block})

        # 低侵入能力提示：只提供控制标签，不规定专用回复风格。
        extra_blocks = []
        if control_hint:
            extra_blocks.append(get_text("llm.business_control_prefix", "可选输出控制：\n{control_hint}").format(control_hint=control_hint))
        if extra_blocks:
            messages.append({"role": "system", "content": "\n\n".join(extra_blocks)})

        messages.append({"role": "user", "content": get_text("llm.business_user_template", "[来自 {other_name}] {message_text}").format(other_name=other_name, message_text=message_text)})

        # Business chatbot 永远不注入禁用思考 header
        resp = await http_client.post(
            provider_endpoint(effective_provider),
            json=build_request(
                effective_provider,
                model=effective_model,
                messages=messages,
                temperature=_RUNTIME_CONFIG.llm_temperature,
                max_tokens=_RUNTIME_CONFIG.llm_max_tokens,
                stream=False,
            ),
        )
        resp.raise_for_status()
        data = resp.json()
        normalized = parse_response(effective_provider, data)
        _record_usage_if_present(effective_model, normalized.usage_payload)
        content = normalized.text.strip()

        if len(content) > _RUNTIME_CONFIG.business_max_reply_chars:
            content = content[:_RUNTIME_CONFIG.business_max_reply_chars] + "…"
        return content or get_text("llm.business_empty_reply", "唔…咱暂时想不出怎么回复")

    except httpx.HTTPError as e:
        em = _safe_http_error_message(e) if isinstance(e, httpx.HTTPError) else get_text("llm.request_failed", "模型服务请求失败")
        logger.error(f"Business LLM 调用失败 | owner={uid}: {em}")
        return get_text("llm.business_error_with_detail", "唔…出了点问题喵（{error}）").format(error=em)
    except Exception:
        logger.exception(f"Business LLM 异常 | owner={uid}")
        return get_text("llm.business_error_reply", "唔…出了点问题喵，请稍后再试")
    finally:
        await http_client.aclose()
