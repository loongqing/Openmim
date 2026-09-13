from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from .security import require_token

_STATIC_DIR = Path(__file__).resolve().parent / "static"
_TEMPLATES_DIR = Path(__file__).resolve().parent / "templates"


def create_web_app(*, app_context: Any, access_token: str, services: Any = None) -> FastAPI:
    """Build the Openmim Web Panel FastAPI application.

    Parameters
    ----------
    app_context:
        The bot's ApplicationContext-like object (may be None in tests).
    access_token:
        Token required for every ``/api/*`` route. Empty means the panel
        considers itself unconfigured (``/api`` returns 503).
    services:
        Optional pre-built services module. When None, import lazily so the
        app can be created without the full bot stack (used in tests).
    """
    app = FastAPI(title="Openmim Web Panel", docs_url=None, redoc_url=None, openapi_url=None)
    auth = require_token(access_token)

    if services is None:
        from . import services as services_module  # lazy import
        services = services_module

    if _STATIC_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=str(_STATIC_DIR)), name="static")
    templates = Jinja2Templates(directory=str(_TEMPLATES_DIR)) if _TEMPLATES_DIR.is_dir() else None

    @app.get("/healthz", tags=["meta"])
    async def healthz():
        return {"ok": True, "service": "openmim-web-panel"}

    @app.get("/", include_in_schema=False)
    async def index(request: Request):
        # Render the dashboard shell publicly; all data-changing/read APIs are still token-gated.
        # This lets remote users open the panel first, then paste/save the access token in the UI.
        if templates is None:
            return HTMLResponse("<h1>Openmim Web Panel</h1>(templates missing)")
        return templates.TemplateResponse(
            request,
            "index.html",
            {"public_base_url": "", "has_token": bool(access_token)},
        )

    @app.get("/api/status", tags=["api"])
    async def api_status(_auth: bool = Depends(auth)):
        return services.get_status(app_context)

    # ── Whitelist ────────────────────────────────────────────────
    @app.get("/api/whitelist", tags=["api"])
    async def wl_get(_auth: bool = Depends(auth)):
        return {"whitelist": services.list_whitelist(app_context)}

    @app.post("/api/whitelist", tags=["api"])
    async def wl_add(body: dict, _auth: bool = Depends(auth)):
        chat_id = str((body or {}).get("chat_id", "")).strip()
        if not chat_id:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="chat_id required")
        return {"whitelist": services.add_whitelist_chat(app_context, chat_id)}

    @app.delete("/api/whitelist/{chat_id}", tags=["api"])
    async def wl_del(chat_id: str, _auth: bool = Depends(auth)):
        return {"whitelist": services.remove_whitelist_chat(app_context, chat_id)}

    # ── Access lists ─────────────────────────────────────────────
    @app.get("/api/access-lists", tags=["api"])
    async def al_get(_auth: bool = Depends(auth)):
        return services.get_access_lists()

    @app.put("/api/access-lists/{key}", tags=["api"])
    async def al_set(key: str, body: dict, _auth: bool = Depends(auth)):
        ids = (body or {}).get("user_ids", [])
        if not isinstance(ids, list):
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail="user_ids must be a list")
        return services.set_access_list(key, [str(x) for x in ids])

    # ── Plugins ──────────────────────────────────────────────────
    @app.get("/api/plugins", tags=["api"])
    async def plugins_get(_auth: bool = Depends(auth)):
        return {"plugins": services.get_plugin_statuses(app_context)}

    @app.post("/api/plugins/{name}/toggle", tags=["api"])
    async def plugin_toggle(name: str, _auth: bool = Depends(auth)):
        try:
            return services.toggle_plugin(app_context, name)
        except (RuntimeError, ValueError) as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    # ── Group settings ───────────────────────────────────────────
    @app.get("/api/group-settings/{chat_id}", tags=["api"])
    async def gs_get(chat_id: str, _auth: bool = Depends(auth)):
        return services.get_group_setting(app_context, chat_id)

    @app.put("/api/group-settings/{chat_id}/{key}", tags=["api"])
    async def gs_set(chat_id: str, key: str, body: dict, _auth: bool = Depends(auth)):
        value = str((body or {}).get("value", ""))
        return services.set_group_setting_value(app_context, chat_id, key, value)

    @app.delete("/api/group-settings/{chat_id}/{key}", tags=["api"])
    async def gs_del(chat_id: str, key: str, _auth: bool = Depends(auth)):
        return services.reset_group_setting(app_context, chat_id, key)

    # ── Token usage ──────────────────────────────────────────────
    @app.get("/api/token-usage", tags=["api"])
    async def tu_get(_auth: bool = Depends(auth)):
        return services.get_token_usage_summary()

    # ── JSON editor ──────────────────────────────────────────────
    @app.get("/api/json-files", tags=["api"])
    async def jf_list(_auth: bool = Depends(auth)):
        return {"files": services.list_json_files()}

    @app.get("/api/json-files/{name}", tags=["api"])
    async def jf_get(name: str, _auth: bool = Depends(auth)):
        try:
            return services.get_json_file(name)
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=404, detail=str(exc))

    @app.put("/api/json-files/{name}", tags=["api"])
    async def jf_put(name: str, body: dict, _auth: bool = Depends(auth)):
        try:
            data = (body or {}).get("data", {})
            return services.update_json_file(name, data, actor="web")
        except ValueError as exc:
            from fastapi import HTTPException
            raise HTTPException(status_code=400, detail=str(exc))

    # ── Restart ──────────────────────────────────────────────────
    @app.post("/api/restart", tags=["api"])
    async def restart(body: dict, _auth: bool = Depends(auth)):
        reason = str((body or {}).get("reason", "web_panel"))
        return services.request_restart(app_context, reason=reason)

    # ── Skills ───────────────────────────────────────────────────
    @app.post("/api/skills/upload", tags=["api"])
    async def skill_upload(
        body: dict,
        _auth: bool = Depends(auth),
    ):
        if not services.is_skill_upload_enabled():
            from fastapi import HTTPException
            raise HTTPException(status_code=403, detail="skill upload disabled")
        return services.install_skill_upload(body)

    return app
