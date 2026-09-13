"""Protocol adapters for the LLM APIs supported by Openmim.

The rest of the application uses the OpenAI Chat Completions message shape as
its canonical representation.  This module converts that representation to
OpenAI Responses or Anthropic Messages requests and normalizes their responses
back into a small common result type.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


OPENAI_COMPATIBLE = "openai_compatible"
OPENAI_RESPONSES = "openai_responses"
ANTHROPIC = "anthropic"
SUPPORTED_PROVIDERS = {OPENAI_COMPATIBLE, OPENAI_RESPONSES, ANTHROPIC}

_PROVIDER_ALIASES = {
    "openai": OPENAI_COMPATIBLE,
    "chat_completions": OPENAI_COMPATIBLE,
    "openai_chat_completions": OPENAI_COMPATIBLE,
    "responses": OPENAI_RESPONSES,
    "openai_response": OPENAI_RESPONSES,
    "claude": ANTHROPIC,
}


def normalize_provider(provider: str | None) -> str:
    value = (provider or OPENAI_COMPATIBLE).strip().lower().replace("-", "_")
    value = _PROVIDER_ALIASES.get(value, value)
    if value not in SUPPORTED_PROVIDERS:
        supported = ", ".join(sorted(SUPPORTED_PROVIDERS))
        raise ValueError(f"Unsupported LLM provider {provider!r}; expected one of: {supported}")
    return value


def provider_headers(provider: str, api_key: str) -> dict[str, str]:
    provider = normalize_provider(provider)
    headers = {"Content-Type": "application/json"}
    if provider == ANTHROPIC:
        headers.update({"x-api-key": api_key, "anthropic-version": "2023-06-01"})
    else:
        headers["Authorization"] = f"Bearer {api_key}"
    return headers


def provider_endpoint(provider: str) -> str:
    provider = normalize_provider(provider)
    if provider == OPENAI_RESPONSES:
        return "/responses"
    if provider == ANTHROPIC:
        return "/messages"
    return "/chat/completions"


def _function_definition(tool: dict) -> dict:
    function = tool.get("function", {}) if tool.get("type") == "function" else tool
    return {
        "name": function.get("name", ""),
        "description": function.get("description", ""),
        "parameters": function.get("parameters") or {"type": "object", "properties": {}},
    }


def convert_tools(tools: list[dict], provider: str) -> list[dict]:
    provider = normalize_provider(provider)
    if provider == OPENAI_COMPATIBLE:
        return tools
    converted: list[dict] = []
    for tool in tools:
        fn = _function_definition(tool)
        if not fn["name"]:
            continue
        if provider == OPENAI_RESPONSES:
            converted.append({
                "type": "function",
                "name": fn["name"],
                "description": fn["description"],
                "parameters": fn["parameters"],
            })
        else:
            converted.append({
                "name": fn["name"],
                "description": fn["description"],
                "input_schema": fn["parameters"],
            })
    return converted


def _data_uri_parts(url: str) -> tuple[str, str] | None:
    match = re.fullmatch(r"data:([^;,]+);base64,(.+)", url or "", re.DOTALL)
    return (match.group(1), match.group(2)) if match else None


def _responses_content(content: Any) -> Any:
    if not isinstance(content, list):
        return content
    result: list[dict] = []
    for block in content:
        block_type = block.get("type")
        if block_type in {"text", "input_text", "output_text"}:
            result.append({"type": "input_text", "text": block.get("text", "")})
        elif block_type in {"image_url", "input_image"}:
            image_url = block.get("image_url", "")
            if isinstance(image_url, dict):
                image_url = image_url.get("url", "")
            result.append({"type": "input_image", "image_url": image_url})
    return result


def to_responses_input(messages: list[dict]) -> list[dict]:
    result: list[dict] = []
    for message in messages:
        role = message.get("role", "user")
        if role == "tool":
            result.append({
                "type": "function_call_output",
                "call_id": message.get("tool_call_id", ""),
                "output": str(message.get("content", "")),
            })
            continue
        provider_output = message.get("_provider_output")
        if role == "assistant" and isinstance(provider_output, list):
            result.extend(provider_output)
            continue
        content = message.get("content")
        if content:
            result.append({"role": role, "content": _responses_content(content)})
        for tool_call in message.get("tool_calls") or []:
            function = tool_call.get("function", {})
            result.append({
                "type": "function_call",
                "call_id": tool_call.get("id", ""),
                "name": function.get("name", ""),
                "arguments": function.get("arguments", "{}"),
            })
    return result


def _anthropic_content(content: Any) -> Any:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return str(content or "")
    result: list[dict] = []
    for block in content:
        block_type = block.get("type")
        if block_type in {"text", "input_text", "output_text"}:
            result.append({"type": "text", "text": block.get("text", "")})
        elif block_type in {"image_url", "input_image"}:
            image_url = block.get("image_url", "")
            if isinstance(image_url, dict):
                image_url = image_url.get("url", "")
            data_uri = _data_uri_parts(image_url)
            if data_uri:
                media_type, data = data_uri
                result.append({
                    "type": "image",
                    "source": {"type": "base64", "media_type": media_type, "data": data},
                })
            elif image_url:
                result.append({"type": "image", "source": {"type": "url", "url": image_url}})
    return result


def to_anthropic_messages(messages: list[dict]) -> tuple[str, list[dict]]:
    system_parts: list[str] = []
    result: list[dict] = []
    for message in messages:
        role = message.get("role", "user")
        content = message.get("content")
        if role in {"system", "developer"}:
            if isinstance(content, str) and content:
                system_parts.append(content)
            continue
        if role == "tool":
            result.append({
                "role": "user",
                "content": [{
                    "type": "tool_result",
                    "tool_use_id": message.get("tool_call_id", ""),
                    "content": str(content or ""),
                }],
            })
            continue
        blocks = _anthropic_content(content)
        tool_calls = message.get("tool_calls") or []
        if tool_calls:
            if isinstance(blocks, str):
                blocks = [{"type": "text", "text": blocks}] if blocks else []
            for tool_call in tool_calls:
                function = tool_call.get("function", {})
                try:
                    tool_input = json.loads(function.get("arguments", "{}"))
                except (TypeError, json.JSONDecodeError):
                    tool_input = {}
                blocks.append({
                    "type": "tool_use",
                    "id": tool_call.get("id", ""),
                    "name": function.get("name", ""),
                    "input": tool_input if isinstance(tool_input, dict) else {},
                })
        result.append({"role": "assistant" if role == "assistant" else "user", "content": blocks})
    return "\n\n".join(system_parts), result


def build_request(
    provider: str,
    *,
    model: str,
    messages: list[dict],
    max_tokens: int,
    temperature: float,
    stream: bool,
    tools: list[dict] | None = None,
    extra_body: dict | None = None,
) -> dict:
    provider = normalize_provider(provider)
    tools = tools or []
    if provider == OPENAI_COMPATIBLE:
        body = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": stream,
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        if tools:
            body.update({"tools": tools, "tool_choice": "auto"})
        if extra_body:
            body.update(extra_body)
        return body
    if provider == OPENAI_RESPONSES:
        body = {
            "model": model,
            "input": to_responses_input(messages),
            "max_output_tokens": max_tokens,
            "stream": stream,
            "store": False,
        }
        # Current reasoning models reject temperature; omit it for GPT-5+ while
        # retaining the configured value for models that support sampling.
        if not model.lower().startswith(("gpt-5", "o1", "o3", "o4")):
            body["temperature"] = temperature
        if tools:
            body.update({"tools": convert_tools(tools, provider), "tool_choice": "auto"})
        return body
    system, anthropic_messages = to_anthropic_messages(messages)
    body = {
        "model": model,
        "messages": anthropic_messages,
        "max_tokens": max_tokens,
        "temperature": max(0.0, min(float(temperature), 1.0)),
        "stream": stream,
    }
    if system:
        body["system"] = system
    if tools:
        body.update({"tools": convert_tools(tools, provider), "tool_choice": {"type": "auto"}})
    return body


@dataclass
class ProviderResult:
    text: str = ""
    tool_calls: list[dict] = field(default_factory=list)
    finish_reason: str = "stop"
    usage_payload: dict | None = None
    provider_output: list[dict] = field(default_factory=list)


def parse_response(provider: str, data: dict) -> ProviderResult:
    provider = normalize_provider(provider)
    if provider == OPENAI_COMPATIBLE:
        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return ProviderResult(
            text=message.get("content") or "",
            tool_calls=message.get("tool_calls") or [],
            finish_reason=choice.get("finish_reason") or "stop",
            usage_payload=data,
        )
    if provider == OPENAI_RESPONSES:
        texts: list[str] = []
        tool_calls: list[dict] = []
        for item in data.get("output") or []:
            if item.get("type") == "message":
                for block in item.get("content") or []:
                    if block.get("type") == "output_text" and block.get("text"):
                        texts.append(block["text"])
            elif item.get("type") == "function_call":
                tool_calls.append({
                    "id": item.get("call_id") or item.get("id", ""),
                    "type": "function",
                    "function": {"name": item.get("name", ""), "arguments": item.get("arguments", "{}")},
                })
        return ProviderResult(
            text="".join(texts),
            tool_calls=tool_calls,
            finish_reason="tool_calls" if tool_calls else "stop",
            usage_payload=data,
            provider_output=list(data.get("output") or []),
        )
    texts = []
    tool_calls = []
    for block in data.get("content") or []:
        if block.get("type") == "text" and block.get("text"):
            texts.append(block["text"])
        elif block.get("type") == "tool_use":
            tool_calls.append({
                "id": block.get("id", ""),
                "type": "function",
                "function": {
                    "name": block.get("name", ""),
                    "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                },
            })
    return ProviderResult(
        text="".join(texts),
        tool_calls=tool_calls,
        finish_reason="tool_calls" if data.get("stop_reason") == "tool_use" or tool_calls else "stop",
        usage_payload=data,
    )


class ProviderStreamAccumulator:
    """Accumulate one provider's SSE JSON events into a normalized result."""

    def __init__(self, provider: str):
        self.provider = normalize_provider(provider)
        self.text = ""
        self.finish_reason = ""
        self.tool_calls: dict[int, dict] = {}
        self.usage: dict[str, int] = {}
        self.provider_output: dict[int, dict] = {}

    def _tool(self, index: int) -> dict:
        return self.tool_calls.setdefault(index, {"id": "", "name": "", "args_str": ""})

    def feed(self, event: dict) -> list[str]:
        deltas: list[str] = []
        if self.provider == OPENAI_COMPATIBLE:
            if isinstance(event.get("usage"), dict):
                self.usage.update(event["usage"])
            choice = (event.get("choices") or [None])[0]
            if not isinstance(choice, dict):
                return deltas
            delta = choice.get("delta") or {}
            self.finish_reason = choice.get("finish_reason") or self.finish_reason
            if delta.get("content"):
                deltas.append(delta["content"])
            for tc in delta.get("tool_calls") or []:
                buf = self._tool(int(tc.get("index", 0)))
                if tc.get("id"):
                    buf["id"] = tc["id"]
                function = tc.get("function") or {}
                if function.get("name"):
                    buf["name"] = function["name"]
                if function.get("arguments"):
                    buf["args_str"] += function["arguments"]
        elif self.provider == OPENAI_RESPONSES:
            event_type = event.get("type")
            if event_type == "response.output_text.delta" and event.get("delta"):
                deltas.append(event["delta"])
            elif event_type == "response.output_item.added":
                item = event.get("item") or {}
                if item.get("type") == "function_call":
                    buf = self._tool(int(event.get("output_index", 0)))
                    buf.update({
                        "id": item.get("call_id") or item.get("id", ""),
                        "name": item.get("name", ""),
                        "args_str": item.get("arguments", ""),
                    })
            elif event_type == "response.function_call_arguments.delta":
                self._tool(int(event.get("output_index", 0)))["args_str"] += event.get("delta", "")
            elif event_type == "response.output_item.done":
                item = event.get("item") or {}
                self.provider_output[int(event.get("output_index", 0))] = item
                if item.get("type") == "function_call":
                    buf = self._tool(int(event.get("output_index", 0)))
                    buf.update({
                        "id": item.get("call_id") or item.get("id", ""),
                        "name": item.get("name", ""),
                        "args_str": item.get("arguments", buf["args_str"]),
                    })
            elif event_type == "response.completed":
                response = event.get("response") or {}
                if isinstance(response.get("output"), list):
                    self.provider_output = {i: item for i, item in enumerate(response["output"])}
                if isinstance(response.get("usage"), dict):
                    self.usage.update(response["usage"])
        else:
            event_type = event.get("type")
            if event_type == "message_start":
                usage = (event.get("message") or {}).get("usage") or {}
                self.usage.update(usage)
            elif event_type == "content_block_start":
                block = event.get("content_block") or {}
                if block.get("type") == "tool_use":
                    buf = self._tool(int(event.get("index", 0)))
                    buf.update({"id": block.get("id", ""), "name": block.get("name", ""), "args_str": ""})
            elif event_type == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    deltas.append(delta["text"])
                elif delta.get("type") == "input_json_delta":
                    self._tool(int(event.get("index", 0)))["args_str"] += delta.get("partial_json", "")
            elif event_type == "message_delta":
                stop_reason = (event.get("delta") or {}).get("stop_reason")
                if stop_reason:
                    self.finish_reason = "tool_calls" if stop_reason == "tool_use" else "stop"
                usage = event.get("usage") or {}
                self.usage.update(usage)
        for delta in deltas:
            self.text += delta
        if self.tool_calls and not self.finish_reason:
            self.finish_reason = "tool_calls"
        return deltas

    def result(self) -> ProviderResult:
        calls = [
            {
                "id": buf["id"],
                "type": "function",
                "function": {"name": buf["name"], "arguments": buf["args_str"] or "{}"},
            }
            for _, buf in sorted(self.tool_calls.items())
        ]
        usage_payload = {"usage": self.usage} if self.usage else None
        return ProviderResult(
            text=self.text,
            tool_calls=calls,
            finish_reason="tool_calls" if calls else (self.finish_reason or "stop"),
            usage_payload=usage_payload,
            provider_output=[item for _, item in sorted(self.provider_output.items())],
        )
