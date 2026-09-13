import json

from llm.provider_protocols import (
    ANTHROPIC,
    OPENAI_COMPATIBLE,
    OPENAI_RESPONSES,
    ProviderStreamAccumulator,
    build_request,
    normalize_provider,
    parse_response,
    provider_endpoint,
    provider_headers,
)


TOOL = {
    "type": "function",
    "function": {
        "name": "get_weather",
        "description": "Get weather",
        "parameters": {
            "type": "object",
            "properties": {"city": {"type": "string"}},
            "required": ["city"],
        },
    },
}


def test_provider_aliases_endpoints_and_headers():
    assert normalize_provider("responses") == OPENAI_RESPONSES
    assert normalize_provider("claude") == ANTHROPIC
    assert provider_endpoint(OPENAI_COMPATIBLE) == "/chat/completions"
    assert provider_endpoint(OPENAI_RESPONSES) == "/responses"
    assert provider_endpoint(ANTHROPIC) == "/messages"
    assert provider_headers(OPENAI_RESPONSES, "key")["Authorization"] == "Bearer key"
    anthropic_headers = provider_headers(ANTHROPIC, "key")
    assert anthropic_headers["x-api-key"] == "key"
    assert anthropic_headers["anthropic-version"] == "2023-06-01"
    try:
        normalize_provider("unknown")
    except ValueError:
        pass
    else:
        raise AssertionError("unknown providers must be rejected")


def test_build_openai_responses_request_converts_tools_images_and_tool_results():
    messages = [
        {"role": "system", "content": "Be concise"},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "What is this?"},
                {"type": "image_url", "image_url": {"url": "data:image/png;base64,AAAA"}},
            ],
        },
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [{
                "id": "call_1",
                "type": "function",
                "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
            }],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
    ]
    body = build_request(
        OPENAI_RESPONSES,
        model="gpt-5",
        messages=messages,
        max_tokens=100,
        temperature=0.7,
        stream=False,
        tools=[TOOL],
    )
    assert body["store"] is False
    assert "temperature" not in body
    assert body["tools"][0]["name"] == "get_weather"
    assert "function" not in body["tools"][0]
    assert body["input"][1]["content"][1] == {
        "type": "input_image",
        "image_url": "data:image/png;base64,AAAA",
    }
    assert body["input"][-2]["type"] == "function_call"
    assert body["input"][-1] == {
        "type": "function_call_output",
        "call_id": "call_1",
        "output": "sunny",
    }


def test_build_anthropic_request_extracts_system_and_converts_content():
    body = build_request(
        ANTHROPIC,
        model="claude-sonnet-4-5",
        messages=[
            {"role": "system", "content": "First instruction"},
            {"role": "system", "content": "Second instruction"},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Describe"},
                    {"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,BBBB"}},
                ],
            },
            {
                "role": "assistant",
                "tool_calls": [{
                    "id": "toolu_1",
                    "type": "function",
                    "function": {"name": "get_weather", "arguments": '{"city":"Paris"}'},
                }],
            },
            {"role": "tool", "tool_call_id": "toolu_1", "content": "sunny"},
        ],
        max_tokens=200,
        temperature=1.4,
        stream=True,
        tools=[TOOL],
    )
    assert body["system"] == "First instruction\n\nSecond instruction"
    assert body["temperature"] == 1.0
    assert body["tools"][0]["input_schema"]["required"] == ["city"]
    assert body["messages"][0]["content"][1]["source"] == {
        "type": "base64",
        "media_type": "image/jpeg",
        "data": "BBBB",
    }
    assert body["messages"][1]["content"][0]["type"] == "tool_use"
    assert body["messages"][2]["content"][0]["type"] == "tool_result"


def test_parse_openai_responses_text_tools_and_usage():
    data = {
        "output": [
            {"type": "reasoning", "summary": []},
            {"type": "message", "content": [{"type": "output_text", "text": "hello"}]},
            {
                "type": "function_call",
                "id": "fc_1",
                "call_id": "call_1",
                "name": "get_weather",
                "arguments": '{"city":"Paris"}',
            },
        ],
        "usage": {"input_tokens": 10, "output_tokens": 5, "total_tokens": 15},
    }
    result = parse_response(OPENAI_RESPONSES, data)
    assert result.text == "hello"
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls[0]["id"] == "call_1"
    assert result.usage_payload is data
    assert result.provider_output[0]["type"] == "reasoning"

    follow_up = build_request(
        OPENAI_RESPONSES,
        model="gpt-5",
        messages=[
            {"role": "user", "content": "weather"},
            {"role": "assistant", "_provider_output": result.provider_output},
            {"role": "tool", "tool_call_id": "call_1", "content": "sunny"},
        ],
        max_tokens=100,
        temperature=0.7,
        stream=False,
    )
    assert follow_up["input"][1]["type"] == "reasoning"
    assert follow_up["input"][-1]["type"] == "function_call_output"


def test_parse_anthropic_text_tools_and_usage():
    data = {
        "content": [
            {"type": "text", "text": "checking"},
            {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {"city": "Paris"}},
        ],
        "stop_reason": "tool_use",
        "usage": {"input_tokens": 12, "output_tokens": 7},
    }
    result = parse_response(ANTHROPIC, data)
    assert result.text == "checking"
    assert result.finish_reason == "tool_calls"
    assert json.loads(result.tool_calls[0]["function"]["arguments"]) == {"city": "Paris"}


def test_responses_stream_accumulates_text_tool_call_and_usage():
    acc = ProviderStreamAccumulator(OPENAI_RESPONSES)
    assert acc.feed({"type": "response.output_text.delta", "delta": "hel"}) == ["hel"]
    assert acc.feed({"type": "response.output_text.delta", "delta": "lo"}) == ["lo"]
    acc.feed({
        "type": "response.output_item.added",
        "output_index": 1,
        "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "get_weather", "arguments": ""},
    })
    acc.feed({"type": "response.function_call_arguments.delta", "output_index": 1, "delta": '{"city"'})
    acc.feed({"type": "response.function_call_arguments.delta", "output_index": 1, "delta": ':"Paris"}'})
    acc.feed({
        "type": "response.completed",
        "response": {
            "output": [
                {"type": "reasoning", "id": "rs_1", "summary": []},
                {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "get_weather", "arguments": '{"city":"Paris"}'},
            ],
            "usage": {"input_tokens": 9, "output_tokens": 4},
        },
    })
    result = acc.result()
    assert result.text == "hello"
    assert result.finish_reason == "tool_calls"
    assert result.tool_calls[0]["id"] == "call_1"
    assert result.tool_calls[0]["function"]["arguments"] == '{"city":"Paris"}'
    assert result.usage_payload == {"usage": {"input_tokens": 9, "output_tokens": 4}}
    assert result.provider_output[0]["type"] == "reasoning"


def test_anthropic_stream_accumulates_text_tool_call_and_usage():
    acc = ProviderStreamAccumulator(ANTHROPIC)
    acc.feed({"type": "message_start", "message": {"usage": {"input_tokens": 11}}})
    acc.feed({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "hi"}})
    acc.feed({
        "type": "content_block_start",
        "index": 1,
        "content_block": {"type": "tool_use", "id": "toolu_1", "name": "get_weather", "input": {}},
    })
    acc.feed({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"city":"Paris"}'}})
    acc.feed({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 8}})
    result = acc.result()
    assert result.text == "hi"
    assert result.tool_calls[0]["id"] == "toolu_1"
    assert result.usage_payload == {"usage": {"input_tokens": 11, "output_tokens": 8}}
