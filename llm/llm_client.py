"""
LLM 客户端
通过 OpenAI 兼容 API 调用 OpenAI 兼容模型服务。
支持：流式传输、function call（搜索+抓取URL）、图片输入。
支持 per-group LLM model/base_url override。
"""
import json
import logging
from dataclasses import dataclass, field
from typing import Optional, AsyncGenerator

import httpx

from app.runtime_config import get_shared_runtime_config
from app_config.customization import get_text
from plugins.base import ToolContext
from plugins.manager import get_plugin_manager
from llm.prompt import (
    MSG_SEPARATOR,
    STICKER_PREFIX,
    STICKER_SUFFIX,
)
from llm.provider_protocols import (
    OPENAI_COMPATIBLE,
    ProviderStreamAccumulator,
    build_request,
    normalize_provider,
    parse_response,
    provider_endpoint,
    provider_headers,
)
from stores.context_manager import ContextManager
from stores.model_store import get_active_model
from stores.focus_store import get_focus_store
from stores.token_usage_store import record_usage

logger = logging.getLogger(__name__)

_RUNTIME_CONFIG = get_shared_runtime_config()

_DISABLE_REASONING = {"thinking": {"type": "disabled"}}

MAX_FILE_SIZE = 10 * 1024  # read_file 工具的文件大小上限


def _safe_http_error_message(exc: httpx.HTTPError) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        code = exc.response.status_code if exc.response is not None else 0
        if code in (401, 403):
            return get_text("llm.http_auth_failed", "模型服务鉴权失败，请检查本项目的 LLM_API_KEY。")
        if code == 429:
            return get_text("llm.http_rate_limited", "模型服务暂时限流了，稍后再试。")
        if 500 <= code <= 599:
            return get_text("llm.http_unavailable", "模型服务暂时不可用，稍后再试。")
        return get_text("llm.http_failed", "模型服务请求失败（HTTP {code}）。").format(code=code)
    if isinstance(exc, httpx.TimeoutException):
        return get_text("llm.timeout", "模型服务请求超时，稍后再试。")
    if isinstance(exc, httpx.NetworkError):
        return get_text("llm.network_failed", "连接模型服务失败，请稍后再试。")
    return get_text("llm.request_failed", "模型服务请求失败。")


# 文本工具调用协议解析已抽离到 llm/tool_protocol.py（纯函数、可独立测试）。
# 此处保留原下划线别名，调用点无需改动。
from llm.tool_protocol import (
    parse_text_tool_calls as _parse_text_tool_calls,
    clean_tool_text as _clean_tool_text,
    wrap_tool_result as _wrap_tool_result,
)


def _extract_usage(payload: dict | None) -> dict[str, int] | None:
    if not isinstance(payload, dict):
        return None
    usage = payload.get("usage")
    if not isinstance(usage, dict):
        return None
    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    normalized = {
        "prompt_tokens": usage.get("prompt_tokens") or usage.get("input_tokens") or 0,
        "completion_tokens": usage.get("completion_tokens") or usage.get("output_tokens") or 0,
        "total_tokens": usage.get("total_tokens") or 0,
        "cached_prompt_tokens": (
            prompt_details.get("cached_tokens")
            or prompt_details.get("cache_read_tokens")
            or usage.get("cached_prompt_tokens")
            or usage.get("input_cached_tokens")
            or usage.get("cache_read_input_tokens")
            or 0
        ),
    }
    if not any(int(normalized.get(key) or 0) > 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens", "cached_prompt_tokens")):
        return None
    return normalized


def _record_usage_if_present(model: str, payload: dict | None) -> None:
    try:
        usage = _extract_usage(payload)
        if usage:
            record_usage(model, usage)
    except Exception:
        logger.exception("记录 token usage 失败")


@dataclass
class StreamEvent:
    type: str
    text: str = ""
    tool_name: str = ""
    tool_args: dict = field(default_factory=dict)
    tool_call_id: str = ""


class LLMResponse:
    def __init__(self, raw: str):
        self.raw = raw
        self.messages: list[str] = []
        self.stickers: list[str] = []
        self._parse()

    def _parse(self):
        logger.debug(f"LLM raw ({len(self.raw)} chars): {repr(self.raw[:200])}")
        segments = self.raw.split(MSG_SEPARATOR)
        logger.debug(f"Split into {len(segments)} segments")
        for seg in segments:
            seg = seg.strip()
            if not seg:
                continue
            sticker_emoji = ""
            si = seg.find(STICKER_PREFIX)
            ei = seg.find(STICKER_SUFFIX, si) if si != -1 else -1
            if si != -1 and ei != -1:
                sticker_emoji = seg[si + len(STICKER_PREFIX):ei].strip()
                seg = (seg[:si] + seg[ei + len(STICKER_SUFFIX):]).strip()
            if seg:
                self.messages.append(seg)
                self.stickers.append(sticker_emoji)
            elif sticker_emoji:
                self.messages.append("")
                self.stickers.append(sticker_emoji)

    @property
    def message_count(self) -> int:
        return len(self.messages)


class LLMClient:
    """多协议 LLM 客户端，支持流式输出与 function call。"""

    def __init__(self, available_emojis: list[str] | None = None):
        self._available_emojis = available_emojis or []
        default_cfg = _RUNTIME_CONFIG.get_effective_llm(None)
        default_provider = normalize_provider(default_cfg.provider)
        self._http = httpx.AsyncClient(
            base_url=default_cfg.api_base,
            timeout=httpx.Timeout(_RUNTIME_CONFIG.llm_timeout),
            limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            headers=provider_headers(default_provider, default_cfg.api_key),
        )

    async def close(self):
        await self._http.aclose()

    # ---- per-group effective config ----

    def _get_effective_llm_config(self, chat_id: int | None = None) -> dict:
        """Return the effective provider configuration for a chat."""
        cfg = _RUNTIME_CONFIG.get_effective_llm(chat_id)
        return {
            "provider": normalize_provider(cfg.provider),
            "model": cfg.model,
            "api_key": cfg.api_key,
            "base_url": cfg.api_base,
        }

    # ---- build messages ----

    async def build_messages(
        self,
        context_mgr: ContextManager,
        chat_id: int,
        current_message: str,
        current_sender: str,
        is_reply: bool = False,
        is_mention: bool = False,
        image_base64: str | None = None,
        image_file_id: str | None = None,
        persona_users: list | None = None,
        trigger_type: str = "",
        current_message_id: int | None = None,
        current_reply_to_message_id: int | None = None,
        chat_title: str | None = None,
    ) -> list[dict]:
        # Prompt 组装逻辑已抽离到 llm/message_builder.py（纯函数、可独立测试）。
        # 此处保留方法签名作薄转发，所有原调用方无需改动。
        from llm.message_builder import build_chat_messages
        return await build_chat_messages(
            runtime_config=_RUNTIME_CONFIG,
            available_emojis=self._available_emojis,
            context_mgr=context_mgr,
            chat_id=chat_id,
            current_message=current_message,
            current_sender=current_sender,
            is_reply=is_reply,
            is_mention=is_mention,
            image_base64=image_base64,
            image_file_id=image_file_id,
            persona_users=persona_users,
            trigger_type=trigger_type,
            current_message_id=current_message_id,
            current_reply_to_message_id=current_reply_to_message_id,
            chat_title=chat_title,
        )

    # ---- chat_stream ----

    @staticmethod
    def _is_custom_api(provider: str, base_url: str, api_key: str, default_cfg, default_provider: str) -> bool:
        """判断某聊天的 LLM 配置是否与全局默认不同（需要独立的 HTTP 客户端）。"""
        return (
            provider != default_provider
            or base_url != default_cfg.api_base
            or api_key != default_cfg.api_key
        )

    async def chat_stream(
        self,
        context_mgr: ContextManager,
        chat_id: int,
        current_message: str,
        current_sender: str,
        is_reply: bool = False,
        is_mention: bool = False,
        image_base64: str | None = None,
        image_file_id: str | None = None,
        telegram_context=None,
        persona_users: list | None = None,
        trigger_type: str = "",
        current_message_id: int | None = None,
        current_reply_to_message_id: int | None = None,
        chat_title: str | None = None,
    ) -> AsyncGenerator[StreamEvent, None]:
        """Run one isolated chat request and own its request-local HTTP client."""
        llm_cfg = self._get_effective_llm_config(chat_id)
        default_cfg = _RUNTIME_CONFIG.get_effective_llm(None)
        default_provider = normalize_provider(default_cfg.provider)
        is_custom_api = self._is_custom_api(
            llm_cfg["provider"], llm_cfg["base_url"], llm_cfg["api_key"], default_cfg, default_provider
        )
        http_client = self._http
        if is_custom_api:
            http_client = httpx.AsyncClient(
                base_url=llm_cfg["base_url"],
                timeout=httpx.Timeout(_RUNTIME_CONFIG.llm_timeout),
                limits=httpx.Limits(max_connections=2, max_keepalive_connections=1),
                headers=provider_headers(llm_cfg["provider"], llm_cfg["api_key"]),
            )

        stream = self._chat_stream_impl(
            context_mgr=context_mgr,
            chat_id=chat_id,
            current_message=current_message,
            current_sender=current_sender,
            is_reply=is_reply,
            is_mention=is_mention,
            image_base64=image_base64,
            image_file_id=image_file_id,
            telegram_context=telegram_context,
            persona_users=persona_users,
            trigger_type=trigger_type,
            current_message_id=current_message_id,
            current_reply_to_message_id=current_reply_to_message_id,
            chat_title=chat_title,
            _http_client=http_client,
            _llm_cfg=llm_cfg,
        )
        try:
            async for event in stream:
                yield event
        finally:
            try:
                await stream.aclose()
            finally:
                if http_client is not self._http:
                    await http_client.aclose()

    async def _chat_stream_impl(
        self,
        context_mgr: ContextManager,
        chat_id: int,
        current_message: str,
        current_sender: str,
        is_reply: bool = False,
        is_mention: bool = False,
        image_base64: str | None = None,
        image_file_id: str | None = None,
        telegram_context=None,
        persona_users: list | None = None,
        trigger_type: str = "",
        current_message_id: int | None = None,
        current_reply_to_message_id: int | None = None,
        chat_title: str | None = None,
        *,
        _http_client: httpx.AsyncClient,
        _llm_cfg: dict,
    ) -> AsyncGenerator[StreamEvent, None]:
        messages = await self.build_messages(
            context_mgr, chat_id, current_message, current_sender,
            is_reply=is_reply,
            is_mention=is_mention,
            image_base64=image_base64,
            image_file_id=image_file_id,
            persona_users=persona_users,
            trigger_type=trigger_type,
            current_message_id=current_message_id,
            current_reply_to_message_id=current_reply_to_message_id,
            chat_title=chat_title,
        )

        # ── 群组有效配置 ──
        llm_cfg = _llm_cfg
        effective_provider = llm_cfg["provider"]
        effective_model = llm_cfg["model"]
        effective_base = llm_cfg["base_url"]
        effective_key = llm_cfg["api_key"]
        default_cfg = _RUNTIME_CONFIG.get_effective_llm(None)
        default_provider = normalize_provider(default_cfg.provider)
        is_custom_api = self._is_custom_api(effective_provider, effective_base, effective_key, default_cfg, default_provider)
        # thinking 参数仅 DeepSeek 模型支持；非 deepseek 模型一律不发
        _skip_reasoning = effective_provider != OPENAI_COMPATIBLE or is_custom_api or ("deepseek" not in effective_model.lower())

        http_client = _http_client

        prev_clean_text = ""

        for _round in range(_RUNTIME_CONFIG.agent_max_rounds):
            tool_calls_buffer: dict[int, dict] = {}
            finish_reason = ""
            content_buf = ""
            stream_usage_payload: dict | None = None

            try:
                tools = get_plugin_manager().tool_definitions(chat_id=chat_id, limit=120)
                extra_body = None if _skip_reasoning else _DISABLE_REASONING
                if _RUNTIME_CONFIG.stream_enabled:
                    accumulator = ProviderStreamAccumulator(effective_provider)
                    async with http_client.stream(
                        "POST", provider_endpoint(effective_provider),
                        json=build_request(
                            effective_provider,
                            model=effective_model,
                            messages=messages,
                            temperature=_RUNTIME_CONFIG.llm_temperature,
                            max_tokens=_RUNTIME_CONFIG.llm_max_tokens,
                            stream=True,
                            tools=tools,
                            extra_body=extra_body,
                        ),
                    ) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line.startswith("data: "):
                                continue
                            data_str = line[6:]
                            if data_str == "[DONE]":
                                break
                            try:
                                chunk = json.loads(data_str)
                            except json.JSONDecodeError:
                                continue

                            for text_delta in accumulator.feed(chunk):
                                content_buf += text_delta
                                yield StreamEvent(type="text_chunk", text=text_delta)
                    normalized = accumulator.result()
                    finish_reason = normalized.finish_reason
                    stream_usage_payload = normalized.usage_payload
                    for i, tc in enumerate(normalized.tool_calls):
                        function = tc.get("function") or {}
                        tool_calls_buffer[i] = {
                            "id": tc.get("id", ""),
                            "name": function.get("name", ""),
                            "args_str": function.get("arguments", "{}"),
                        }
                    _record_usage_if_present(effective_model, stream_usage_payload)
                else:
                    resp = await http_client.post(
                        provider_endpoint(effective_provider),
                        json=build_request(
                            effective_provider,
                            model=effective_model,
                            messages=messages,
                            temperature=_RUNTIME_CONFIG.llm_temperature,
                            max_tokens=_RUNTIME_CONFIG.llm_max_tokens,
                            stream=False,
                            tools=tools,
                            extra_body=extra_body,
                        ),
                    )
                    resp.raise_for_status()
                    data = resp.json()
                    normalized = parse_response(effective_provider, data)
                    _record_usage_if_present(effective_model, normalized.usage_payload)
                    content = normalized.text
                    content_buf = content
                    finish_reason = normalized.finish_reason
                    has_text_tools = _RUNTIME_CONFIG.text_tool_enabled and _parse_text_tool_calls(content)
                    if content and not has_text_tools:
                        text_to_yield = content
                        if prev_clean_text and text_to_yield.startswith(prev_clean_text):
                            text_to_yield = text_to_yield[len(prev_clean_text):].lstrip()
                        if text_to_yield:
                            yield StreamEvent(type="text_chunk", text=text_to_yield)
                            prev_clean_text = text_to_yield
                    elif content and has_text_tools:
                        clean = _clean_tool_text(content)
                        if clean:
                            yield StreamEvent(type="text_chunk", text=clean)
                            prev_clean_text = clean
                    if normalized.tool_calls:
                        for i, tc in enumerate(normalized.tool_calls):
                            tool_calls_buffer[i] = {
                                "id": tc.get("id", ""),
                                "name": tc.get("function", {}).get("name", ""),
                                "args_str": tc.get("function", {}).get("arguments", ""),
                            }
            except httpx.HTTPError as e:
                safe_msg = _safe_http_error_message(e)
                status = e.response.status_code if isinstance(e, httpx.HTTPStatusError) and e.response is not None else "n/a"
                logger.error(f"LLM error sanitized: status={status} type={type(e).__name__}")
                yield StreamEvent(type="error", text=safe_msg)
                return
            except Exception as e:
                logger.exception("LLM exception")
                yield StreamEvent(type="error", text=get_text("llm.internal_error", "内部错误"))
                return

            if finish_reason == "tool_calls" and tool_calls_buffer:
                def _parse_tool_args(args_str: str) -> dict:
                    if not args_str:
                        return {}
                    try:
                        parsed = json.loads(args_str)
                        return parsed if isinstance(parsed, dict) else {}
                    except json.JSONDecodeError:
                        logger.warning(f"工具参数 JSON 解析失败，已按空参数处理: {args_str[:160]}")
                        return {}

                assistant_msg = {
                    "role": "assistant",
                    "content": _clean_tool_text(content_buf) or None,
                    "tool_calls": [
                        {
                            "id": buf["id"],
                            "type": "function",
                            "function": {"name": buf["name"], "arguments": buf["args_str"]},
                        }
                        for buf in tool_calls_buffer.values()
                    ],
                }
                if normalized.provider_output:
                    assistant_msg["_provider_output"] = normalized.provider_output
                messages.append(assistant_msg)

                for buf in tool_calls_buffer.values():
                    yield StreamEvent(
                        type="tool_call",
                        tool_name=buf["name"],
                        tool_args=_parse_tool_args(buf["args_str"]),
                        tool_call_id=buf["id"],
                        text=f"🔍 {buf['name']}...",
                    )
                    tool_result = await self._execute_tool(
                        buf["name"],
                        buf["args_str"],
                        chat_id=chat_id,
                        telegram_context=telegram_context,
                        reference_image_base64=image_base64,
                        reference_file_id=image_file_id,
                    )
                    messages.append({
                        "role": "tool",
                        "tool_call_id": buf["id"],
                        "content": tool_result,
                    })
                continue

            if not _RUNTIME_CONFIG.stream_enabled and _RUNTIME_CONFIG.text_tool_enabled:
                text_tools = _parse_text_tool_calls(content_buf)
                if text_tools:
                    clean = _clean_tool_text(content_buf)
                    # 不发送 tool_calls（原生 function call 已禁用），纯文本追加
                    if clean:
                        assistant_text_msg = {
                            "role": "assistant",
                            "content": clean,
                        }
                        if normalized.provider_output:
                            assistant_text_msg["_provider_output"] = normalized.provider_output
                        messages.append(assistant_text_msg)
                    for i, tool in enumerate(text_tools):
                        yield StreamEvent(
                            type="tool_call",
                            tool_name=tool["name"],
                            text=f"🔍 {tool['name']}...",
                        )
                        result = await self._execute_tool(
                            tool["name"],
                            tool["args_str"],
                            chat_id=chat_id,
                            telegram_context=telegram_context,
                            reference_image_base64=image_base64,
                            reference_file_id=image_file_id,
                        )
                        messages.append({
                            "role": "user",
                            "content": _wrap_tool_result(tool["name"], result),
                        })
                    continue

            yield StreamEvent(type="done", text=content_buf)
            return

        yield StreamEvent(type="done", text=content_buf)

    # ---- chat (non-streaming) ----

    async def chat(
        self,
        context_mgr: ContextManager,
        chat_id: int,
        current_message: str,
        current_sender: str,
        is_reply: bool = False,
        is_mention: bool = False,
        image_base64: str | None = None,
        persona_users: list | None = None,
        trigger_type: str = "",
        current_message_id: int | None = None,
        current_reply_to_message_id: int | None = None,
        chat_title: str | None = None,
    ) -> LLMResponse:
        full_text = ""
        async for ev in self.chat_stream(
            context_mgr=context_mgr,
            chat_id=chat_id,
            current_message=current_message,
            current_sender=current_sender,
            is_reply=is_reply,
            is_mention=is_mention,
            image_base64=image_base64,
            persona_users=persona_users,
            trigger_type=trigger_type,
            current_message_id=current_message_id,
            current_reply_to_message_id=current_reply_to_message_id,
            chat_title=chat_title,
        ):
            if ev.type == "text_chunk":
                full_text += ev.text
            elif ev.type == "done":
                full_text = ev.text or full_text
            elif ev.type == "error":
                return LLMResponse(f"[{ev.text}]")
        return LLMResponse(full_text)

    # ---- execute tool ----

    async def _execute_tool(
        self,
        name: str,
        args_str: str,
        chat_id: int | None = None,
        *,
        telegram_context=None,
        reference_image_base64: str | None = None,
        reference_file_id: str | None = None,
    ) -> str:
        try:
            args = json.loads(args_str) if args_str else {}
        except json.JSONDecodeError:
            return "[工具参数解析失败]"

        ctx = ToolContext(
            chat_id=chat_id,
            llm_client=self,
            telegram_context=telegram_context,
            runtime_config=_RUNTIME_CONFIG,
        )
        ctx.reference_image_base64 = reference_image_base64
        ctx.reference_file_id = reference_file_id
        ctx.plugin_manager = get_plugin_manager()
        result = await ctx.plugin_manager.execute_tool(name, args, ctx)
        max_chars = _RUNTIME_CONFIG.tool_result_max_chars
        return result[:max_chars] if len(result) > max_chars else result

    # ---- score_focus_stage1 ----

    async def score_focus_stage1(self, message: str, chat_id: int, recent_context: list | None = None) -> int | None:
        extra_note = get_focus_store().get_criteria_note(chat_id)
        reply_preference = "llm_first"
        username_anonymization_enabled = True
        try:
            from stores.group_settings_store import (
    get_group_reply_preference,
    get_group_username_anonymization_enabled,
)
            reply_preference = get_group_reply_preference(chat_id)
            username_anonymization_enabled = get_group_username_anonymization_enabled(chat_id)
        except Exception:
            pass
        # Prompt 组装已抽离到 llm/focus_scoring.py（纯函数、可独立测试）。
        from llm.focus_scoring import build_focus_stage1_messages
        messages = build_focus_stage1_messages(
            message,
            chat_id,
            recent_context,
            extra_note=extra_note,
            reply_preference=reply_preference,
            username_anonymization_enabled=username_anonymization_enabled,
        )

        llm_cfg = self._get_effective_llm_config(chat_id)
        effective_provider = llm_cfg["provider"]
        effective_model = llm_cfg["model"]
        effective_base = llm_cfg["base_url"]
        effective_key = llm_cfg["api_key"]
        default_cfg = _RUNTIME_CONFIG.get_effective_llm(None)
        default_provider = normalize_provider(default_cfg.provider)
        is_custom_api = self._is_custom_api(effective_provider, effective_base, effective_key, default_cfg, default_provider)
        _skip_reasoning = effective_provider != OPENAI_COMPATIBLE or is_custom_api or ("deepseek" not in effective_model.lower())

        http_client = self._http
        if is_custom_api:
            http_client = httpx.AsyncClient(
                base_url=effective_base,
                timeout=httpx.Timeout(_RUNTIME_CONFIG.llm_timeout),
                headers=provider_headers(effective_provider, effective_key),
            )

        try:
            resp = await http_client.post(
                provider_endpoint(effective_provider),
                json=build_request(
                    effective_provider,
                    model=effective_model,
                    messages=messages,
                    temperature=0.3,
                    max_tokens=10,
                    stream=False,
                    extra_body=None if _skip_reasoning else _DISABLE_REASONING,
                ),
            )
            resp.raise_for_status()
            data = resp.json()
            normalized = parse_response(effective_provider, data)
            _record_usage_if_present(effective_model, normalized.usage_payload)
            content = normalized.text.strip()
            from llm.focus_scoring import parse_focus_score
            return parse_focus_score(content)
        except Exception as e:
            logger.warning(f"聚焦评分 Stage1 失败: {e}")
            return None
        finally:
            if http_client is not self._http:
                await http_client.aclose()

    # ---- guest_chat ----

    async def guest_chat(
        self,
        query: str,
        caller_name: str,
        image_base64: str | None = None,
        progress_callback=None,
        context_messages: list[str] | None = None,
        chat_id: int | str | None = None,
    ) -> str:
        """Guest 模式非流式对话（实现已迁至 llm/guest_llm.py）。"""
        from llm.guest_llm import guest_chat
        return await guest_chat(
            self,
            query,
            caller_name,
            image_base64=image_base64,
            progress_callback=progress_callback,
            context_messages=context_messages,
            chat_id=chat_id,
        )


    # ---- business_chat ----
    async def business_chat(
        self, owner_name: str, owner_id: str | int,
        other_name: str, message_text: str,
        context_messages: list[str] | None = None,
        control_hint: str = "",
    ) -> str:
        """Business Chatbot 专用对话（实现已迁至 llm/business_llm.py）。"""
        from llm.business_llm import business_chat
        return await business_chat(
            self,
            owner_name,
            owner_id,
            other_name,
            message_text,
            context_messages=context_messages,
            control_hint=control_hint,
        )


    # ---- generate_text ----

    async def generate_text(
        self,
        prompt: str,
        max_tokens: int = 300,
        temperature: float = 0.8,
    ) -> str:
        llm_cfg = _RUNTIME_CONFIG.get_effective_llm(None)
        provider = normalize_provider(llm_cfg.provider)
        messages = [
            {"role": "user", "content": prompt},
        ]
        skip_reasoning = (
            provider != OPENAI_COMPATIBLE
            or llm_cfg.api_base != "https://api.openai.com/v1"
            or "deepseek" not in llm_cfg.model.lower()
        )

        try:
            resp = await self._http.post(
                provider_endpoint(provider),
                json=build_request(
                    provider,
                    model=llm_cfg.model,
                    messages=messages,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    stream=False,
                    extra_body=None if skip_reasoning else _DISABLE_REASONING,
                ),
            )
            resp.raise_for_status()
            data = resp.json()
            normalized = parse_response(provider, data)
            _record_usage_if_present(llm_cfg.model, normalized.usage_payload)
            return normalized.text.strip()
        except Exception as e:
            logger.warning(f"generate_text 调用失败: {e}")
            raise


# ── 全局单例 ──

_llm_client: Optional[LLMClient] = None


def get_llm_client(available_emojis: list[str] | None = None) -> LLMClient:
    global _llm_client
    if _llm_client is None:
        _llm_client = LLMClient(available_emojis)
    return _llm_client


async def close_llm_client():
    global _llm_client
    if _llm_client:
        await _llm_client.close()
        _llm_client = None


def get_active_llm_model() -> str:
    return get_active_model()
