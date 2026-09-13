import asyncio
from types import SimpleNamespace

import pytest

import llm.llm_client as llm_module
import llm.tool_plugins.builtin.image as image_tool
from llm.llm_client import LLMClient, StreamEvent
from plugins.base import ToolContext


class _Response:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class _HttpClient:
    def __init__(self, payload=None, **kwargs):
        self.payload = payload or {}
        self.kwargs = kwargs
        self.calls = []
        self.closed = False

    async def post(self, path, **kwargs):
        self.calls.append((path, kwargs))
        return _Response(self.payload)

    async def aclose(self):
        self.closed = True


@pytest.mark.asyncio
async def test_execute_tool_keeps_request_context_isolated(monkeypatch):
    seen = []

    class Manager:
        async def execute_tool(self, name, args, ctx):
            await asyncio.sleep(0)
            seen.append(
                (
                    ctx.chat_id,
                    ctx.telegram_context,
                    ctx.reference_image_base64,
                    ctx.reference_file_id,
                )
            )
            return name

    manager = Manager()
    monkeypatch.setattr(llm_module, "get_plugin_manager", lambda: manager)
    client = object.__new__(LLMClient)

    await asyncio.gather(
        client._execute_tool(
            "first",
            "{}",
            chat_id=1,
            telegram_context="telegram-a",
            reference_image_base64="image-a",
            reference_file_id="file-a",
        ),
        client._execute_tool(
            "second",
            "{}",
            chat_id=2,
            telegram_context="telegram-b",
            reference_image_base64="image-b",
            reference_file_id="file-b",
        ),
    )

    assert sorted(seen) == [
        (1, "telegram-a", "image-a", "file-a"),
        (2, "telegram-b", "image-b", "file-b"),
    ]
    assert not hasattr(client, "_current_ref_image")
    assert not hasattr(client, "_current_ref_file_id")
    assert not hasattr(client, "_telegram_context")


@pytest.mark.asyncio
async def test_image_tool_reads_reference_from_invocation_context(monkeypatch):
    captured = {}

    async def fake_generate_image(**kwargs):
        captured.update(kwargs)
        return "ok"

    monkeypatch.setattr(image_tool, "execute_generate_image", fake_generate_image)
    ctx = ToolContext(chat_id=7, llm_client=object(), telegram_context="telegram")
    ctx.reference_image_base64 = "request-image"
    ctx.reference_file_id = "request-file"

    result = await image_tool.execute_image({"prompt": "draw"}, ctx)

    assert result == "ok"
    assert captured["mode"] == "image_to_image"
    assert captured["reference_image_base64"] == "request-image"
    assert captured["reference_file_id"] == "request-file"
    assert captured["context"] == "telegram"


@pytest.mark.asyncio
async def test_chat_stream_closes_custom_client_when_consumer_stops_early(monkeypatch):
    shared = _HttpClient()
    custom_clients = []
    inner_closed = asyncio.Event()

    def client_factory(**kwargs):
        client = _HttpClient(**kwargs)
        custom_clients.append(client)
        return client

    async def fake_impl(self, **kwargs):
        try:
            yield StreamEvent(type="text_chunk", text="first")
            await asyncio.Event().wait()
        finally:
            inner_closed.set()

    client = object.__new__(LLMClient)
    client._http = shared
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        client,
        "_get_effective_llm_config",
        lambda chat_id: {
            "provider": "openai_compatible",
            "model": "custom-model",
            "api_key": "custom-key",
            "base_url": "https://custom.invalid/v1",
        },
    )
    monkeypatch.setattr(LLMClient, "_chat_stream_impl", fake_impl)

    stream = client.chat_stream(None, 42, "hello", "user")
    event = await anext(stream)
    assert event.text == "first"
    await stream.aclose()

    assert inner_closed.is_set()
    assert len(custom_clients) == 1
    assert custom_clients[0].closed is True
    assert shared.closed is False


@pytest.mark.asyncio
async def test_chat_stream_closes_custom_client_when_cancelled(monkeypatch):
    shared = _HttpClient()
    custom_clients = []
    started = asyncio.Event()

    def client_factory(**kwargs):
        client = _HttpClient(**kwargs)
        custom_clients.append(client)
        return client

    async def fake_impl(self, **kwargs):
        started.set()
        await asyncio.Event().wait()
        yield StreamEvent(type="done")

    client = object.__new__(LLMClient)
    client._http = shared
    monkeypatch.setattr(llm_module.httpx, "AsyncClient", client_factory)
    monkeypatch.setattr(
        client,
        "_get_effective_llm_config",
        lambda chat_id: {
            "provider": "openai_compatible",
            "model": "custom-model",
            "api_key": "custom-key",
            "base_url": "https://custom.invalid/v1",
        },
    )
    monkeypatch.setattr(LLMClient, "_chat_stream_impl", fake_impl)

    stream = client.chat_stream(None, 42, "hello", "user")
    pending = asyncio.create_task(anext(stream))
    await started.wait()
    pending.cancel()
    with pytest.raises(asyncio.CancelledError):
        await pending

    assert len(custom_clients) == 1
    assert custom_clients[0].closed is True
    assert shared.closed is False


@pytest.mark.asyncio
async def test_generate_text_uses_main_llm_protocol_and_shared_client(monkeypatch):
    http = _HttpClient(
        {
            "content": [{"type": "text", "text": " main llm result "}],
            "stop_reason": "end_turn",
            "usage": {"input_tokens": 2, "output_tokens": 3},
        }
    )
    client = object.__new__(LLMClient)
    client._http = http
    runtime = SimpleNamespace(
        get_effective_llm=lambda chat_id: SimpleNamespace(
            provider="anthropic",
            model="claude-main",
            api_key="main-key",
            api_base="https://main.invalid/v1",
        )
    )
    monkeypatch.setattr(llm_module, "_RUNTIME_CONFIG", runtime)
    monkeypatch.setattr(llm_module, "_record_usage_if_present", lambda model, payload: None)

    result = await client.generate_text("write a topic", max_tokens=23, temperature=0.4)

    assert result == "main llm result"
    assert len(http.calls) == 1
    path, request = http.calls[0]
    assert path == "/messages"
    assert request["json"]["model"] == "claude-main"
    assert request["json"]["max_tokens"] == 23
    assert request["json"]["messages"] == [
        {"role": "user", "content": "write a topic"}
    ]
