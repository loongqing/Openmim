"""Guest 模式 LLM 调用（从 llm/llm_client.py 拆分）。

与 LLMClient.guest_chat 等价；作为独立 async 函数接收 LLMClient 实例。
"""
from datetime import datetime, timezone, timedelta

import httpx

from app_config.customization import get_text
from llm.llm_client import (
    LLMClient,
    _DISABLE_REASONING,
    _RUNTIME_CONFIG,
    _record_usage_if_present,
    _safe_http_error_message,
    logger,
)
from llm.provider_protocols import (
    OPENAI_COMPATIBLE,
    build_request,
    normalize_provider,
    parse_response,
    provider_endpoint,
)
from llm.tool_protocol import (
    clean_tool_text as _clean_tool_text,
    parse_text_tool_calls as _parse_text_tool_calls,
    wrap_tool_result as _wrap_tool_result,
)
from plugins.manager import get_plugin_manager


async def guest_chat(
    client: LLMClient,
    query: str,
    caller_name: str,
    image_base64: str | None = None,
    progress_callback=None,
    context_messages: list[str] | None = None,
    chat_id: int | str | None = None,
) -> str:
    now = datetime.now(timezone(timedelta(hours=8)))
    current_time = now.strftime("%Y-%m-%d %H:%M:%S CST (周%w)")

    system = get_text(
        "llm.guest_system_prompt",
        "你是一个友好的 Telegram 猫娘助手，可以在任何聊天中被 @ 召唤回答问题。\n"
        "说话风格：简洁、亲切、带一点点猫娘的口癖（如'喵~''唔'），但不要过度卖萌。\n"
        "自称'咱'或'我'。不要提到自己是 AI 或模型。回答尽量在 3 句以内，直接给答案。\n\n"
        "当前时间：{current_time}\n\n"
        "你有工具可以调用：search_web（搜索）、fetch_url（读网页）。\n"
        "调用方式：原生 function call，或文本格式 [TOOL:search_web] {{\"query\":\"...\"}} [/TOOL]\n"
        "工具结果以 [TOOL_RESULT:工具名]...[/TOOL_RESULT] 返回，你根据结果继续回答。\n"
        "需要实时信息时先搜索再回答，搜索后不要说'搜索显示'，自然融入。",
    ).format(current_time=current_time)
    user_text = get_text("llm.guest_user_template", "[来自 {caller_name}] {query}").format(caller_name=caller_name, query=query)
    messages = [{"role": "system", "content": system}]
    if context_messages:
        context_block = get_text("llm.guest_context_prefix", "下面是这条 guest 提问直接相关的上下文，按从旧到新排列。请优先结合这些内容理解当前提问：\n{context}").format(context="\n".join(context_messages))
        messages.append({"role": "user", "content": context_block})
    if image_base64:
        messages.append({
            "role": "user",
            "content": [
                {"type": "text", "text": user_text},
                {"type": "image_url", "image_url": {
                    "url": f"data:image/jpeg;base64,{image_base64}",
                    "detail": "auto",
                }},
            ],
        })
    else:
        messages.append({"role": "user", "content": user_text})

    final_text = ""
    max_rounds = _RUNTIME_CONFIG.agent_max_rounds if _RUNTIME_CONFIG.guest_tool_enabled else 1
    enable_tools = _RUNTIME_CONFIG.guest_tool_enabled

    # ── 非 DeepSeek 模型不注入禁用思考 header ──
    guest_cfg = _RUNTIME_CONFIG.get_effective_llm(None)
    guest_provider = normalize_provider(guest_cfg.provider)
    _guest_skip_reasoning = guest_provider != OPENAI_COMPATIBLE or guest_cfg.api_base != "https://api.openai.com/v1" or ("deepseek" not in guest_cfg.model.lower())

    for _round in range(max_rounds):
        try:
            if progress_callback:
                try:
                    await progress_callback(get_text("llm.guest_thinking", "🔍 咱正在思考中…"))
                except Exception:
                    pass
            guest_tools = get_plugin_manager().tool_definitions(chat_id=chat_id, limit=120) if enable_tools else []
            resp = await client._http.post(
                provider_endpoint(guest_provider),
                json=build_request(
                    guest_provider,
                    model=guest_cfg.model,
                    messages=messages,
                    temperature=0.9,
                    max_tokens=min(_RUNTIME_CONFIG.llm_max_tokens, 800 if enable_tools else 512),
                    stream=False,
                    tools=guest_tools,
                    extra_body=None if _guest_skip_reasoning else _DISABLE_REASONING,
                ),
            )
            resp.raise_for_status()
            data = resp.json()
            normalized = parse_response(guest_provider, data)
            _record_usage_if_present(guest_cfg.model, normalized.usage_payload)
            content = normalized.text.strip()
            finish_reason = normalized.finish_reason

            if content:
                clean = _clean_tool_text(content)
                if clean:
                    final_text = clean

            native_tools = normalized.tool_calls
            if finish_reason == "tool_calls" and native_tools:
                assistant_msg = {
                    "role": "assistant",
                    "content": content or None,
                    "tool_calls": native_tools,
                }
                if normalized.provider_output:
                    assistant_msg["_provider_output"] = normalized.provider_output
                messages.append(assistant_msg)
                for tc in native_tools:
                    t_name = tc.get("function", {}).get("name", "")
                    t_args = tc.get("function", {}).get("arguments", "{}")
                    logger.info(f"👻 Guest tool: {t_name}({t_args[:80]})")
                    if progress_callback:
                        try:
                            await progress_callback(get_text("llm.guest_calling_tool", "🛠️ 正在调用工具：{tool_name}…").format(tool_name=t_name))
                        except Exception:
                            pass
                    result = await client._execute_tool(t_name, t_args, chat_id=chat_id)
                    messages.append({
                        "role": "tool",
                        "tool_call_id": tc.get("id", ""),
                        "content": result,
                    })
                continue

            if _RUNTIME_CONFIG.text_tool_enabled and enable_tools:
                text_tools = _parse_text_tool_calls(content)
                if text_tools:
                    fake_tool_calls = []
                    for i, tool in enumerate(text_tools):
                        fake_tool_calls.append({
                            "id": f"guest_text_tool_{_round}_{i}",
                            "type": "function",
                            "function": {"name": tool["name"], "arguments": tool["args_str"]},
                        })
                    assistant_text_msg = {
                        "role": "assistant",
                        "content": _clean_tool_text(content) or None,
                        "tool_calls": fake_tool_calls,
                    }
                    if normalized.provider_output:
                        assistant_text_msg["_provider_output"] = normalized.provider_output
                    messages.append(assistant_text_msg)
                    for i, tool in enumerate(text_tools):
                        logger.info(f"👻 Guest text-tool: {tool['name']}")
                        if progress_callback:
                            try:
                                await progress_callback(get_text("llm.guest_calling_tool", "🛠️ 正在调用工具：{tool_name}…").format(tool_name=tool["name"]))
                            except Exception:
                                pass
                        result = await client._execute_tool(tool["name"], tool["args_str"], chat_id=chat_id)
                        messages.append({
                            "role": "user",
                            "content": _wrap_tool_result(tool["name"], result),
                        })
                    continue

            break

        except Exception as e:
            em = _safe_http_error_message(e) if isinstance(e, httpx.HTTPError) else get_text("llm.generic_retry", "请稍后再试")
            logger.error(f"Guest LLM 调用失败: {e}")
            return get_text("llm.guest_error_with_detail", "唔…出了点问题喵（{error}）").format(error=em)

    if progress_callback:
        try:
            await progress_callback(get_text("llm.guest_done", "✍️ 咱整理好回复啦…"))
        except Exception:
            pass

    if len(final_text) > _RUNTIME_CONFIG.guest_mode_max_reply_chars:
        final_text = final_text[:_RUNTIME_CONFIG.guest_mode_max_reply_chars] + "…"

    return final_text or get_text("llm.guest_empty_reply", "唔…咱暂时想不出怎么回答喵")
