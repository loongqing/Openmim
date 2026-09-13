from __future__ import annotations

import importlib
import logging
import pkgutil
from collections.abc import Iterable, Sequence
from typing import Any

from plugins.base import (
    BotPlugin,
    MessageBuildHookContext,
    MessageHookContext,
    MessageHookResult,
    ReplyHookContext,
    OutgoingTextHookContext,
    StartupContext,
    ToolContext,
    ToolSpec,
)

logger = logging.getLogger(__name__)

BUILTIN_PLUGIN_PACKAGE = "plugins.builtin"
_MANAGER: PluginManager | None = None  # type: ignore[name-defined]


def discover_plugin_modules(package_name: str = BUILTIN_PLUGIN_PACKAGE) -> tuple[str, ...]:
    package = importlib.import_module(package_name)
    modules: list[str] = []
    for info in pkgutil.iter_modules(package.__path__, prefix=f"{package_name}."):
        if not info.ispkg:
            modules.append(info.name)
    return tuple(sorted(modules))


class PluginManager:
    def __init__(
        self,
        *,
        disabled_plugins: Iterable[str] | None = None,
        get_chat_disabled_tools=None,
    ) -> None:
        self._plugins: list[BotPlugin] = []
        self._tools: dict[str, tuple[ToolSpec, BotPlugin]] = {}
        self._primary_tools: list[str] = []
        self._disabled_plugins = {str(x).strip() for x in (disabled_plugins or []) if str(x).strip()}
        self._get_chat_disabled_tools = get_chat_disabled_tools or (lambda chat_id: set())

    @property
    def plugins(self) -> tuple[BotPlugin, ...]:
        return tuple(self._plugins)

    def register(self, plugin: BotPlugin) -> None:
        self._plugins.append(plugin)
        self._plugins.sort(key=lambda p: getattr(p, "priority", 100))
        for tool in getattr(plugin, "tools", ()):
            for name in tool.names():
                if name in self._tools:
                    raise ValueError(f"Duplicate tool name: {name}")
            self._primary_tools.append(tool.name)
            for name in tool.names():
                self._tools[name] = (tool, plugin)

    async def startup(self, app_context: Any, application: Any | None = None) -> None:
        ctx = app_context if isinstance(app_context, StartupContext) else StartupContext(
            app_context=app_context,
            application=application,
            bot_data=getattr(application, "bot_data", {}) if application is not None else {},
            logger=logger,
        )
        for plugin in self._plugins:
            if not self.is_plugin_enabled(plugin.name):
                continue
            try:
                await plugin.on_startup(ctx)
            except Exception:
                logger.exception("插件启动失败: %s", getattr(plugin, "name", plugin.__class__.__name__))
                if getattr(plugin, "required", False):
                    raise

    async def shutdown(self, app_context: Any, application: Any | None = None) -> None:
        ctx = app_context if isinstance(app_context, StartupContext) else StartupContext(
            app_context=app_context,
            application=application,
            bot_data=getattr(application, "bot_data", {}) if application is not None else {},
            logger=logger,
        )
        for plugin in reversed(self._plugins):
            if not self.is_plugin_enabled(plugin.name):
                continue
            try:
                await plugin.on_shutdown(ctx)
            except Exception:
                logger.exception("插件关闭失败: %s", getattr(plugin, "name", plugin.__class__.__name__))
                if getattr(plugin, "required", False):
                    raise

    def is_plugin_enabled(self, plugin_name: str) -> bool:
        return str(plugin_name) not in self._disabled_plugins

    def disable_plugin(self, plugin_name: str) -> None:
        self._disabled_plugins.add(str(plugin_name))

    def enable_plugin(self, plugin_name: str) -> None:
        self._disabled_plugins.discard(str(plugin_name))

    def toggle_plugin(self, plugin_name: str) -> bool:
        plugin = next((item for item in self._plugins if item.name == str(plugin_name)), None)
        if plugin is None:
            raise ValueError(f"unknown plugin: {plugin_name}")
        owns_lifecycle = (
            type(plugin).on_startup is not BotPlugin.on_startup
            or type(plugin).on_shutdown is not BotPlugin.on_shutdown
        )
        if getattr(plugin, "handlers", ()):
            raise RuntimeError(
                f"plugin '{plugin_name}' owns Telegram handlers and can only be toggled via config + restart"
            )
        if owns_lifecycle:
            raise RuntimeError(
                f"plugin '{plugin_name}' owns a lifecycle and must be toggled via config + plugin reload"
            )
        if self.is_plugin_enabled(plugin_name):
            self.disable_plugin(plugin_name)
            return False
        self.enable_plugin(plugin_name)
        return True

    def plugin_statuses(self) -> list[dict[str, object]]:
        return [
            {
                "name": p.name,
                "enabled": self.is_plugin_enabled(p.name),
                "tool_count": len(getattr(p, "tools", ())),
                "priority": getattr(p, "priority", 100),
            }
            for p in self._plugins
        ]

    def _chat_disabled_tools(self, chat_id) -> set[str]:
        try:
            return {str(x).strip() for x in (self._get_chat_disabled_tools(chat_id) or []) if str(x).strip()}
        except Exception:
            logger.exception("读取群工具禁用列表失败 chat=%s", chat_id)
            return set()

    def tool_definitions(self, *, chat_id: int | str | None = None, limit: int | None = 120) -> list[dict]:
        defs: list[dict] = []
        disabled_tools = self._chat_disabled_tools(chat_id)
        for name in self._primary_tools:
            tool, plugin = self._tools[name]
            if tool.enabled_by_default and self.is_plugin_enabled(plugin.name) and tool.name not in disabled_tools:
                defs.append(tool.definition)
        return defs[:limit] if limit is not None else defs

    async def execute_tool(self, name: str, args: dict, ctx: ToolContext) -> str:
        entry = self._tools.get(name)
        if not entry:
            return f"[未知工具: {name}]"
        tool, plugin = entry
        if not self.is_plugin_enabled(plugin.name):
            return f"[插件已禁用: {plugin.name}]"
        if (not tool.enabled_by_default) or tool.name in self._chat_disabled_tools(ctx.chat_id):
            return f"[工具已禁用: {name}]"
        ctx.plugin_manager = self
        return await tool.executor(args, ctx)

    async def dispatch_message(self, ctx: MessageHookContext) -> MessageHookResult:
        for plugin in self._plugins:
            if not self.is_plugin_enabled(plugin.name):
                continue
            try:
                result = await plugin.on_message(ctx)
            except Exception:
                logger.exception("插件 on_message 失败: %s", getattr(plugin, "name", plugin.__class__.__name__))
                continue
            if result and result.action != "continue":
                return result
        return MessageHookResult(action="continue")

    async def before_build_messages(self, ctx: MessageBuildHookContext) -> None:
        for plugin in self._plugins:
            if not self.is_plugin_enabled(plugin.name):
                continue
            try:
                await plugin.before_build_messages(ctx)
            except Exception:
                logger.exception("插件 before_build_messages 失败: %s", getattr(plugin, "name", plugin.__class__.__name__))

    async def after_build_messages(self, ctx: MessageBuildHookContext) -> None:
        for plugin in self._plugins:
            if not self.is_plugin_enabled(plugin.name):
                continue
            try:
                await plugin.after_build_messages(ctx)
            except Exception:
                logger.exception("插件 after_build_messages 失败: %s", getattr(plugin, "name", plugin.__class__.__name__))

    async def before_reply(self, ctx: ReplyHookContext) -> None:
        for plugin in self._plugins:
            if not self.is_plugin_enabled(plugin.name):
                continue
            try:
                await plugin.before_reply(ctx)
            except Exception:
                logger.exception("插件 before_reply 失败: %s", getattr(plugin, "name", plugin.__class__.__name__))


    async def enrich_outgoing_text(self, text: str, *, chat_id: int | None = None, entities=None) -> tuple[str, list | None]:
        ctx = OutgoingTextHookContext(text=text, chat_id=chat_id, entities=entities)
        for plugin in self._plugins:
            if not self.is_plugin_enabled(plugin.name):
                continue
            try:
                await plugin.enrich_outgoing_text(ctx)
            except Exception:
                logger.exception("插件 enrich_outgoing_text 失败: %s", getattr(plugin, "name", plugin.__class__.__name__))
        return ctx.text, ctx.entities


def load_plugins(
    *,
    modules: Sequence[str] | None = None,
    disabled_plugins: Iterable[str] | None = None,
    get_chat_disabled_tools=None,
) -> PluginManager:
    manager = PluginManager(disabled_plugins=disabled_plugins, get_chat_disabled_tools=get_chat_disabled_tools)
    module_names = discover_plugin_modules() if modules is None else tuple(modules)
    for modname in module_names:
        mod = importlib.import_module(modname)
        plugin_items: list[BotPlugin] = []
        if hasattr(mod, "PLUGIN"):
            plugin_items.append(mod.PLUGIN)
        plugin_items.extend(getattr(mod, "PLUGINS", []))
        for plugin in plugin_items:
            manager.register(plugin)
    return manager


async def reload_plugin_manager(application: Any) -> PluginManager:
    """Rebuild the manager while keeping every application reference consistent.

    Plugin modules are intentionally not re-imported here: replacing handler objects in a
    running python-telegram-bot Application is not atomic.  This operation reloads persisted
    plugin/tool configuration and reruns plugin lifecycles for the existing plugin set.
    """
    if application is None:
        raise RuntimeError("plugin reload requires the running application")

    import app_config.config as config
    try:
        from stores.group_settings_store import get_group_disabled_tools
    except Exception:
        get_group_disabled_tools = None
    old_manager = get_plugin_manager()
    manager = load_plugins(
        disabled_plugins=getattr(config, "PLUGINS_DISABLED", set()),
        get_chat_disabled_tools=get_group_disabled_tools,
    )

    def enabled_handlers(plugin_manager: PluginManager) -> tuple[Any, ...]:
        return tuple(
            handler
            for plugin in plugin_manager.plugins
            if plugin_manager.is_plugin_enabled(plugin.name)
            for handler in getattr(plugin, "handlers", ())
        )

    if enabled_handlers(old_manager) != enabled_handlers(manager):
        raise RuntimeError("enabled plugin handler set changed; restart is required")

    bot_data = getattr(application, "bot_data", None)
    if bot_data is None:
        raise RuntimeError("application bot_data is unavailable")
    app_context = bot_data.get("app_context")

    await old_manager.shutdown(app_context, application)
    try:
        await manager.startup(app_context, application)
    except Exception:
        # Preserve the previously live manager if the replacement cannot start.
        await old_manager.startup(app_context, application)
        raise

    set_plugin_manager(manager)
    bot_data["plugin_manager"] = manager
    if app_context is not None:
        app_context.plugin_manager = manager
    return manager


def set_plugin_manager(manager: PluginManager) -> None:
    global _MANAGER
    _MANAGER = manager


def get_plugin_manager() -> PluginManager:
    global _MANAGER
    if _MANAGER is None:
        import app_config.config as config
        try:
            from stores.group_settings_store import get_group_disabled_tools
        except Exception:
            get_group_disabled_tools = None
        _MANAGER = load_plugins(disabled_plugins=getattr(config, "PLUGINS_DISABLED", set()), get_chat_disabled_tools=get_group_disabled_tools)
    return _MANAGER
