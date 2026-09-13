from types import SimpleNamespace

import pytest

import plugins.manager as manager_module
from plugins.base import BotPlugin, ToolContext, ToolSpec
from plugins.manager import PluginManager


@pytest.mark.asyncio
async def test_chat_disabled_tool_is_hidden_and_cannot_execute():
    calls = []

    async def execute(args, ctx):
        calls.append((args, ctx.chat_id))
        return "executed"

    tool = ToolSpec(
        name="dangerous_tool",
        definition={"type": "function", "function": {"name": "dangerous_tool"}},
        executor=execute,
    )
    plugin = BotPlugin()
    plugin.name = "test"
    plugin.tools = (tool,)
    manager = PluginManager(
        get_chat_disabled_tools=lambda chat_id: {"dangerous_tool"} if str(chat_id) == "42" else set()
    )
    manager.register(plugin)

    assert manager.tool_definitions(chat_id=42) == []
    assert manager.tool_definitions(chat_id=7) == [tool.definition]
    assert await manager.execute_tool("dangerous_tool", {}, ToolContext(chat_id=42)) == "[工具已禁用: dangerous_tool]"
    assert calls == []
    assert await manager.execute_tool("dangerous_tool", {"ok": True}, ToolContext(chat_id=7)) == "executed"
    assert calls == [({"ok": True}, 7)]


@pytest.mark.asyncio
async def test_reload_synchronizes_global_application_and_context(monkeypatch):
    events = []

    class LifecyclePlugin(BotPlugin):
        name = "lifecycle"

        def __init__(self, label):
            self.label = label

        async def on_startup(self, ctx):
            events.append(("start", self.label))

        async def on_shutdown(self, ctx):
            events.append(("stop", self.label))

    old = PluginManager()
    old.register(LifecyclePlugin("old"))
    new = PluginManager()
    new.register(LifecyclePlugin("new"))
    manager_module.set_plugin_manager(old)
    monkeypatch.setattr(manager_module, "load_plugins", lambda **kwargs: new)

    app_context = SimpleNamespace(plugin_manager=old)
    application = SimpleNamespace(bot_data={"plugin_manager": old, "app_context": app_context})
    result = await manager_module.reload_plugin_manager(application)

    assert result is new
    assert manager_module.get_plugin_manager() is new
    assert application.bot_data["plugin_manager"] is new
    assert app_context.plugin_manager is new
    assert events == [("stop", "old"), ("start", "new")]


@pytest.mark.asyncio
async def test_disabled_plugin_lifecycle_is_not_started_or_stopped():
    events = []

    class LifecyclePlugin(BotPlugin):
        name = "disabled"

        async def on_startup(self, ctx):
            events.append("start")

        async def on_shutdown(self, ctx):
            events.append("stop")

    manager = PluginManager(disabled_plugins={"disabled"})
    manager.register(LifecyclePlugin())
    await manager.startup(None)
    await manager.shutdown(None)

    assert events == []


@pytest.mark.asyncio
async def test_reload_requires_restart_when_enabled_handler_set_changes(monkeypatch):
    handler = object()

    class HandlerPlugin(BotPlugin):
        name = "commands"
        handlers = (handler,)

    old = PluginManager()
    old.register(HandlerPlugin())
    new = PluginManager(disabled_plugins={"commands"})
    new.register(HandlerPlugin())
    manager_module.set_plugin_manager(old)
    monkeypatch.setattr(manager_module, "load_plugins", lambda **kwargs: new)

    application = SimpleNamespace(bot_data={"plugin_manager": old, "app_context": None})
    with pytest.raises(RuntimeError, match="restart is required"):
        await manager_module.reload_plugin_manager(application)

    assert manager_module.get_plugin_manager() is old


def test_handler_plugin_cannot_be_toggled_live():
    class HandlerPlugin(BotPlugin):
        name = "commands"
        handlers = (object(),)

    manager = PluginManager()
    manager.register(HandlerPlugin())

    with pytest.raises(RuntimeError, match=r"config \+ restart"):
        manager.toggle_plugin("commands")


def test_lifecycle_plugin_cannot_be_toggled_live():
    class LifecyclePlugin(BotPlugin):
        name = "server"

        async def on_startup(self, ctx):
            pass

    manager = PluginManager()
    manager.register(LifecyclePlugin())

    with pytest.raises(RuntimeError, match=r"config \+ plugin reload"):
        manager.toggle_plugin("server")
