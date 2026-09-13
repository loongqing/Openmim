from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------

def get_status(app_context: Any) -> dict:
    """High-level bot status for the dashboard."""
    whitelist = _get_whitelist(app_context)
    plugin_manager = _get_plugin_manager(app_context)
    plugins = plugin_manager.plugin_statuses() if plugin_manager else []
    try:
        import app_config.config as config
        version_info = {
            "web_panel_enabled": getattr(config, "WEB_PANEL_ENABLED", False),
            "business_enabled": getattr(config, "BUSINESS_ENABLED", False),
        }
    except Exception:
        version_info = {}
    return {
        "ok": True,
        "whitelist_count": len(whitelist),
        "plugins": plugins,
        "plugin_count": len(plugins),
        **version_info,
    }


# ---------------------------------------------------------------------------
# Whitelist helpers (reuse app_config.config ORM-backed functions)
# ---------------------------------------------------------------------------

def _get_whitelist(app_context: Any) -> set[str]:
    # app_context injects the live whitelist set used by handlers/admin_panel.
    if app_context is None:
        return set()
    wl = getattr(app_context, "whitelist", None)
    if wl is None:
        wl = getattr(app_context, "_whitelist_ref", None)
    return set(wl) if wl else set()


def list_whitelist(app_context: Any) -> list[str]:
    return sorted(_get_whitelist(app_context))


def add_whitelist_chat(app_context: Any, chat_id: str) -> list[str]:
    chat_id = str(chat_id).strip()
    if not chat_id:
        return list_whitelist(app_context)
    wl = _get_whitelist(app_context)
    wl.add(chat_id)
    _persist_whitelist(wl)
    _sync_whitelist_ref(app_context, wl)
    return sorted(wl)


def remove_whitelist_chat(app_context: Any, chat_id: str) -> list[str]:
    chat_id = str(chat_id).strip()
    wl = _get_whitelist(app_context)
    wl.discard(chat_id)
    _persist_whitelist(wl)
    _sync_whitelist_ref(app_context, wl)
    return sorted(wl)


def _persist_whitelist(wl: set[str]) -> None:
    try:
        from app_config.config import save_whitelist
        save_whitelist(wl)
    except Exception:
        logger.exception("Web Panel: save_whitelist failed")


def _sync_whitelist_ref(app_context: Any, wl: set[str]) -> None:
    """Keep the live reference inside app_context in sync (handlers read it)."""
    if app_context is None:
        return
    for attr in ("whitelist", "_whitelist_ref"):
        ref = getattr(app_context, attr, None)
        if isinstance(ref, set):
            ref.clear()
            ref.update(wl)


# ---------------------------------------------------------------------------
# Access lists (PRIVATE/GUEST/BUSINESS_ALLOWED_USER_IDS)
# ---------------------------------------------------------------------------

_ACCESS_KEYS = ("PRIVATE_ALLOWED_USER_IDS", "GUEST_ALLOWED_USER_IDS", "BUSINESS_ALLOWED_USER_IDS")


def get_access_lists() -> dict[str, list[str]]:
    import app_config.config as runtime_config
    out: dict[str, list[str]] = {}
    for key in _ACCESS_KEYS:
        out[key] = sorted({str(x) for x in getattr(runtime_config, key, set()) if str(x).strip()})
    return out


def set_access_list(key: str, ids: list[str]) -> dict[str, list[str]]:
    if key not in _ACCESS_KEYS:
        raise ValueError(f"unknown access list: {key}")
    # Reuse admin_panel writer so behaviour stays identical to /admin.
    from handlers.admin_panel import _set_allowed_user_ids
    normalized = sorted({str(x).strip() for x in ids if str(x).strip()})
    _set_allowed_user_ids(key, " ".join(normalized) if normalized else "空")
    return get_access_lists()


# ---------------------------------------------------------------------------
# Plugins
# ---------------------------------------------------------------------------

def _get_plugin_manager(app_context: Any):
    if app_context is None:
        return None
    pm = getattr(app_context, "plugin_manager", None)
    if pm is None:
        pm = getattr(app_context, "bot_data", {}).get("plugin_manager") if hasattr(app_context, "bot_data") else None
    return pm


def get_plugin_statuses(app_context: Any) -> list[dict]:
    pm = _get_plugin_manager(app_context)
    if not pm:
        return []
    return pm.plugin_statuses()


def toggle_plugin(app_context: Any, name: str) -> dict:
    pm = _get_plugin_manager(app_context)
    if not pm:
        raise RuntimeError("plugin manager unavailable")
    pm.toggle_plugin(name)
    # Persist disabled set to project_config.json so restart keeps the state.
    try:
        import app_config.config as config
        disabled = sorted(pm._disabled_plugins)  # noqa: SLF001 — internal but stable
        from app_config.project_config_writer import write_project_config_keys
        write_project_config_keys({"PLUGINS_DISABLED": disabled})
    except Exception:
        logger.exception("Web Panel: persist plugin toggle failed")
    return {"name": name, "enabled": pm.is_plugin_enabled(name)}


# ---------------------------------------------------------------------------
# Group settings
# ---------------------------------------------------------------------------

def get_group_setting(app_context: Any, chat_id: str) -> dict:
    from stores.group_settings_store import get_group_settings
    settings = get_group_settings(chat_id) or {}
    return {"chat_id": chat_id, "settings": settings}


def set_group_setting_value(app_context: Any, chat_id: str, key: str, value: str) -> dict:
    from stores.group_settings_store import set_group_setting
    set_group_setting(chat_id, key, value)
    return {"chat_id": chat_id, "key": key, "value": value}


def reset_group_setting(app_context: Any, chat_id: str, key: str) -> dict:
    from stores.group_settings_store import reset_group_setting
    reset_group_setting(chat_id, key)
    return {"chat_id": chat_id, "key": key, "reset": True}


# ---------------------------------------------------------------------------
# Token usage
# ---------------------------------------------------------------------------

def get_token_usage_summary() -> dict:
    try:
        from stores.token_usage_store import get_usage_summary
        return get_usage_summary()
    except Exception:
        return {"available": False}


# ---------------------------------------------------------------------------
# JSON editor (delegates to json_editor module)
# ---------------------------------------------------------------------------

def list_json_files() -> list[dict]:
    from .json_editor import EDITABLE_JSON_FILES
    return [{"name": k, "path": v} for k, v in EDITABLE_JSON_FILES.items()]


def get_json_file(name: str) -> dict:
    from .json_editor import load_json_file
    return load_json_file(name, mask=True)


def update_json_file(name: str, data: dict, *, actor: str = "web") -> dict:
    from .json_editor import update_json_file as _update
    return _update(name, data, actor=actor)


# ---------------------------------------------------------------------------
# Restart
# ---------------------------------------------------------------------------

def request_restart(app_context: Any, *, reason: str = "web_panel") -> dict:
    import os
    import app_config.config as config
    if not getattr(config, "WEB_PANEL_RESTART_ENABLED", False):
        return {"ok": False, "error": "restart disabled"}
    cmd = (getattr(config, "WEB_PANEL_RESTART_COMMAND", "") or "").strip()
    if not cmd:
        return {"ok": False, "error": "restart command not configured"}
    # Only static configured command; never user-supplied text.
    import subprocess
    subprocess.Popen(cmd, shell=True, cwd=os.getcwd())
    return {"ok": True, "scheduled": True, "reason": reason}


# ---------------------------------------------------------------------------
# Skill upload
# ---------------------------------------------------------------------------

def is_skill_upload_enabled() -> bool:
    import app_config.config as config
    return getattr(config, "WEB_PANEL_SKILL_UPLOAD_ENABLED", True)


def install_skill_upload(body: dict) -> dict:
    from .skill_upload import install_skill_md, install_skill_zip, validate_skill_name
    import app_config.config as config
    from pathlib import Path

    content: bytes = body.get("content") or b""
    filename = str(body.get("filename") or "").lower()
    overwrite = bool(body.get("overwrite"))
    # body may carry base64; if it's a raw string encode it.
    if isinstance(content, str):
        import base64
        try:
            content = base64.b64decode(content)
        except Exception:
            content = content.encode("utf-8")
    if not content:
        raise ValueError("empty upload")
    max_bytes = int(getattr(config, "WEB_PANEL_SKILL_UPLOAD_MAX_BYTES", 2_097_152))
    if len(content) > max_bytes:
        raise ValueError(f"upload too large (>{max_bytes} bytes)")
    from integrations.skill_market_client import get_skill_root
    skill_root = get_skill_root()
    if filename.endswith(".zip"):
        return install_skill_zip(content, skill_root=skill_root, overwrite=overwrite)
    if filename == "skill.md" or filename.endswith(".md"):
        return install_skill_md(content, skill_root=skill_root, overwrite=overwrite)
    raise ValueError("unsupported upload; send SKILL.md or a .zip")
