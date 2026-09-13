from __future__ import annotations

import asyncio
import logging

from plugins.base import BotPlugin, StartupContext

logger = logging.getLogger(__name__)


class WebPanelPlugin(BotPlugin):
    """Starts the Openmim Web Panel (FastAPI/Uvicorn) when enabled."""

    name = "web_panel"
    priority = 200  # low priority — start after more important plugins

    def __init__(self):
        self._server = None
        self._task = None

    async def on_startup(self, ctx: StartupContext) -> None:
        import app_config.config as config

        if not getattr(config, "WEB_PANEL_ENABLED", False):
            logger.info("Web panel disabled (WEB_PANEL_ENABLED=false)")
            return

        host = config.WEB_PANEL_HOST
        token = config.WEB_PANEL_ACCESS_TOKEN

        from plugins.web_panel.app import create_web_app

        app = create_web_app(app_context=ctx.app_context, access_token=token)
        try:
            import uvicorn
        except ImportError as exc:  # pragma: no cover — uvicorn is in requirements
            logger.error("uvicorn not installed; cannot start Web Panel: %s", exc)
            return

        uv_config = uvicorn.Config(
            app,
            host=host,
            port=config.WEB_PANEL_PORT,
            log_level="info",
            access_log=False,
        )
        self._server = uvicorn.Server(uv_config)
        self._task = asyncio.create_task(self._server.serve())
        logger.info("Web panel listening on http://%s:%s", host, config.WEB_PANEL_PORT)

    async def on_shutdown(self, ctx: StartupContext) -> None:
        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except asyncio.TimeoutError:
                self._task.cancel()
                try:
                    await self._task
                except asyncio.CancelledError:
                    pass
            except Exception:
                logger.exception("Web panel server task did not stop cleanly")
        self._server = None
        self._task = None


PLUGIN = WebPanelPlugin()
