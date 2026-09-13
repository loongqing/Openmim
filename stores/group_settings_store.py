"""
Per-group settings persistence store.

Stores group-level overrides for:
  - scoring_criteria: focus scoring extra note
  - persona_prompt: custom persona / system prompt
  - image_gen_api_key: custom image gen API key (or "default")
  - image_gen_api_base: custom image gen API base URL (or "default")
  - image_gen_model: custom image gen model (or "default")
  - tavily_api_key: custom Tavily API key (or "default")
  - llm_model: custom LLM model (or "default")
  - llm_provider: custom LLM API protocol (or "default")
  - llm_api_base: custom LLM API base URL (or "default")

Default interfaces are NEVER exposed — group admins see "使用默认接口".
"""
from __future__ import annotations

import json
import os
from threading import Lock

from sqlalchemy import delete, func, select

from app_config.config import DATA_DIR, WORKSPACE_DIR, GROUP_CONFIG_DB_FILE
from stores.orm import GroupSetting, orm_session
from app_config.customization import get_dict
from features.crypto_utils import (
    encrypt_value,
    decrypt_value,
    is_encrypted,
    ensure_encryption_key,
)

STORE_PATH = os.path.join(DATA_DIR, "group_settings.json")  # legacy JSON import path
_lock = Lock()

DEFAULT_GROUP_SETTINGS: dict[str, str] = {
    "scoring_criteria": "",
    "persona_prompt": "",
    "morning_greeting_enabled": "true",
    "evening_greeting_enabled": "true",
    "idle_topic_enabled": "true",
    "free_reply_mode": "false",
    "reply_preference": "llm_first",
    "attention_mode": "single_message",
    "message_drop_probability": "0",
    "username_anonymization_enabled": "true",
    "repeater_enabled": "true",
    "image_gen_api_key": "default",
    "image_gen_api_base": "default",
    "image_gen_model": "default",
    "tavily_api_key": "default",
    "llm_model": "default",
    "llm_provider": "default",
    "llm_api_key": "default",
    "llm_api_base": "default",
    "enabled_skills": "[]",
    "skill_secrets": "{}",
    "disabled_tools": "[]",
}

# ── key → human-readable label ──────────────────
SETTING_LABELS: dict[str, str] = {
    "scoring_criteria": "评分标准",
    "persona_prompt": "人设",
    "morning_greeting_enabled": "早安",
    "evening_greeting_enabled": "晚安",
    "idle_topic_enabled": "6h 冷群活跃",
    "free_reply_mode": "自由回复",
    "reply_preference": "回复偏好",
    "attention_mode": "注意力模式",
    "message_drop_probability": "消息丢弃概率",
    "username_anonymization_enabled": "用户名脱敏",
    "repeater_enabled": "复读机",
    "image_gen_api_key": "生图 API Key",
    "image_gen_api_base": "生图 API Base",
    "image_gen_model": "生图模型",
    "tavily_api_key": "Tavily API Key",
    "llm_model": "对话模型",
    "llm_provider": "对话 API 协议",
    "llm_api_key": "对话 API Key",
    "llm_api_base": "对话 API Base",
    "enabled_skills": "已订阅 Skills",
    "skill_secrets": "Skill 私密信息",
    "disabled_tools": "禁用工具",
}

SETTING_DESCRIPTIONS: dict[str, str] = {
    "scoring_criteria": "聚焦插话的评分标准调整说明（自然语言）",
    "persona_prompt": "自定义猫娘人设提示词",
    "morning_greeting_enabled": "是否允许群内早安问候",
    "evening_greeting_enabled": "是否允许群内晚安问候",
    "idle_topic_enabled": "是否允许 6h 冷群自动活跃",
    "free_reply_mode": "开启后，Bot 会把最近消息 ID 提供给 LLM，允许它选择一条或多条消息分别回复；已回复过的消息会标记提醒，避免重复追同一句。",
    "reply_preference": "控制单消息主动插话时，更偏向遵循 LLM 综合判断，还是优先响应上下文中明确提到机器人/猫娘/咪姆/猫猫或需要机器人回答的问题。",
    "attention_mode": "控制主动参与群聊时的注意力范围",
    "message_drop_probability": "机器人收到普通群消息后，随机跳过后续处理的概率。范围 0~1，例如 0=不丢弃，0.2=约 20% 丢弃，1=全部丢弃。明确 @/回复/叫到 Bot 和管理员命令不受影响。",
    "username_anonymization_enabled": "开启后，LLM 上下文中的群成员显示名会替换为 用户_XXXX；关闭后直接显示 Telegram 昵称。",
    "repeater_enabled": "开启后，群里出现复读时，机器人会偶尔跟着吐槽/接一句。关闭后完全禁用复读机。",
    "image_gen_api_key": "AI 生图的 API Key（OpenAI 兼容）",
    "image_gen_api_base": "AI 生图的 API 端点地址",
    "image_gen_model": "AI 生图使用的模型名",
    "tavily_api_key": "Tavily 联网搜索的 API Key",
    "llm_model": "群聊对话使用的 LLM 模型名",
    "llm_provider": "群聊对话使用的 API 协议（openai_compatible / openai_responses / anthropic）",
    "llm_api_key": "群聊对话使用的 LLM API Key",
    "llm_api_base": "对话 LLM 的 API 端点地址",
    "enabled_skills": "本群已订阅的 Skill 市场 Skills（JSON 数组，存 skill ID 列表）",
    "skill_secrets": "本群各 Skill 的私密信息（JSON dict，key=skill_id，value=私密文本），调用 Skill 时注入给 LLM",
    "disabled_tools": "本群禁用的 LLM 工具名列表",
}

SETTING_IS_SENSITIVE: dict[str, bool] = {
    "scoring_criteria": False,
    "persona_prompt": False,
    "morning_greeting_enabled": False,
    "evening_greeting_enabled": False,
    "idle_topic_enabled": False,
    "free_reply_mode": False,
    "reply_preference": False,
    "attention_mode": False,
    "message_drop_probability": False,
    "username_anonymization_enabled": False,
    "repeater_enabled": False,
    "image_gen_api_key": True,
    "image_gen_api_base": False,
    "image_gen_model": False,
    "tavily_api_key": True,
    "llm_model": False,
    "llm_provider": False,
    "llm_api_key": True,
    "llm_api_base": False,
    "enabled_skills": False,
    "skill_secrets": True,
    "disabled_tools": False,
}


def get_setting_labels() -> dict[str, str]:
    return get_dict("settings.labels", SETTING_LABELS)


def get_setting_descriptions() -> dict[str, str]:
    return get_dict("settings.descriptions", SETTING_DESCRIPTIONS)

# 需要加密存储的 key 列表
_ENCRYPTED_KEYS = {"image_gen_api_key", "tavily_api_key", "llm_api_key", "skill_secrets"}


def _load_legacy_json() -> dict:
    try:
        with open(STORE_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _migrate_legacy_json_if_needed(session) -> None:
    existing = session.scalar(select(func.count()).select_from(GroupSetting)) or 0
    if int(existing) > 0:
        return
    data = _load_legacy_json()
    if not data:
        return
    rows: list[GroupSetting] = []
    for chat_id, settings in data.items():
        if not isinstance(settings, dict):
            continue
        for key, value in settings.items():
            if key in DEFAULT_GROUP_SETTINGS:
                rows.append(GroupSetting(chat_id=str(chat_id), key=str(key), value=str(value)))
    if not rows:
        return
    session.add_all(rows)
    session.flush()


def _load() -> dict:
    try:
        with orm_session(GROUP_CONFIG_DB_FILE) as session:
            _migrate_legacy_json_if_needed(session)
            rows = session.execute(select(GroupSetting.chat_id, GroupSetting.key, GroupSetting.value)).all()
    except Exception:
        return _load_legacy_json()
    data: dict[str, dict[str, str]] = {}
    for chat_id, key, value in rows:
        data.setdefault(str(chat_id), {})[str(key)] = str(value)
    return data


def _save(data: dict) -> None:
    rows: list[GroupSetting] = []
    for chat_id, settings in (data or {}).items():
        if not isinstance(settings, dict):
            continue
        for key, value in settings.items():
            if key in DEFAULT_GROUP_SETTINGS:
                rows.append(GroupSetting(chat_id=str(chat_id), key=str(key), value=str(value)))
    with orm_session(GROUP_CONFIG_DB_FILE) as session:
        session.execute(delete(GroupSetting))
        session.add_all(rows)

def get_group_settings(chat_id: str | int) -> dict[str, str]:
    """Return a full settings dict for a group, filling missing keys with defaults.
    自动解密敏感字段。首次调用时自动检测并加密明文值（无感知迁移）。
    """
    cid = str(chat_id)
    with _lock:
        all_data = _load()
        stored = all_data.get(cid, {})
    
    result = {**DEFAULT_GROUP_SETTINGS, **stored}
    
    # 自动迁移：明文 Key → 加密
    migrated = False
    for key in _ENCRYPTED_KEYS:
        # Only migrate values explicitly stored for this chat. Do not persist/encrypt
        # DEFAULT_GROUP_SETTINGS entries just because get_group_settings() was read.
        if key not in stored:
            continue
        val = result.get(key, "")
        if val and val != "default" and not is_encrypted(val):
            result[key] = encrypt_value(val)
            migrated = True
    
    if migrated:
        set_group_settings_bulk(cid, {k: result[k] for k in _ENCRYPTED_KEYS if result.get(k)})
    
    # 解密返回
    for key in _ENCRYPTED_KEYS:
        val = result.get(key, "")
        if val and val != "default":
            result[key] = decrypt_value(val)
    
    return result


def set_group_settings_bulk(chat_id: str | int, updates: dict[str, str]) -> None:
    """批量写入（内部方法，用于自动迁移）。"""
    cid = str(chat_id)
    with _lock:
        all_data = _load()
        if cid not in all_data:
            all_data[cid] = {}
        all_data[cid].update(updates)
        _save(all_data)


def set_group_setting(chat_id: str | int, key: str, value: str) -> None:
    """Set a single setting key for a group. 敏感 Key 自动加密存储。"""
    cid = str(chat_id)
    if key == "llm_provider" and value != "default":
        value = str(value).strip().lower()
        if value not in {"openai_compatible", "openai_responses", "anthropic"}:
            raise ValueError("invalid llm_provider")
    
    # 敏感字段加密后落盘
    store_value = encrypt_value(value) if key in _ENCRYPTED_KEYS and value and value != "default" else value
    
    with _lock:
        all_data = _load()
        if cid not in all_data:
            all_data[cid] = {}
        all_data[cid][key] = store_value
        _save(all_data)


def reset_group_setting(chat_id: str | int, key: str) -> None:
    """Reset a setting to its default value."""
    cid = str(chat_id)
    with _lock:
        all_data = _load()
        if cid in all_data and key in all_data[cid]:
            del all_data[cid][key]
            if not all_data[cid]:
                del all_data[cid]
            _save(all_data)


def reset_group_settings(chat_id: str | int) -> None:
    """Reset all persisted per-group settings to defaults.

    This removes the group's override block entirely. Callers that also need to
    reset runtime/proactive-reply state should clear focus_store separately.
    """
    cid = str(chat_id)
    with _lock:
        all_data = _load()
        if cid in all_data:
            del all_data[cid]
            _save(all_data)


# ── 便捷读取方法 ──────────────────────────────────

def _get_effective(chat_id: str | int | None, key: str) -> str | None:
    """Return effective value for key; None means use global default."""
    if chat_id is None:
        return None
    val = get_group_settings(chat_id).get(key, "default")
    if val == "default":
        return None
    return val


def get_group_image_gen_api_key(chat_id: str | int | None) -> str | None:
    return _get_effective(chat_id, "image_gen_api_key")


def get_group_image_gen_api_base(chat_id: str | int | None) -> str | None:
    return _get_effective(chat_id, "image_gen_api_base")


def get_group_image_gen_model(chat_id: str | int | None) -> str | None:
    return _get_effective(chat_id, "image_gen_model")


def get_group_tavily_api_key(chat_id: str | int | None) -> str | None:
    return _get_effective(chat_id, "tavily_api_key")


def get_group_llm_model(chat_id: str | int | None) -> str | None:
    return _get_effective(chat_id, "llm_model")


def get_group_llm_provider(chat_id: str | int | None) -> str | None:
    return _get_effective(chat_id, "llm_provider")


def get_group_llm_api_key(chat_id: str | int | None) -> str | None:
    return _get_effective(chat_id, "llm_api_key")


def get_group_llm_api_base(chat_id: str | int | None) -> str | None:
    return _get_effective(chat_id, "llm_api_base")


def get_group_persona_prompt(chat_id: str | int | None) -> str:
    if chat_id is None:
        return ""
    return (get_group_settings(chat_id).get("persona_prompt") or "").strip()


def get_group_scoring_criteria(chat_id: str | int | None) -> str:
    if chat_id is None:
        return ""
    return (get_group_settings(chat_id).get("scoring_criteria") or "").strip()



def get_group_repeater_enabled(chat_id: str | int | None) -> bool:
    if chat_id is None:
        return True
    return str(get_group_settings(chat_id).get("repeater_enabled", "true")).lower() == "true"


def set_group_repeater_enabled(chat_id: str | int, enabled: bool) -> None:
    set_group_setting(chat_id, "repeater_enabled", "true" if enabled else "false")


ATTENTION_MODE_SINGLE = "single_message"
ATTENTION_MODE_ALL = "all_message"
ATTENTION_MODE_MIXED = "mixed"
ATTENTION_MODES = {ATTENTION_MODE_SINGLE, ATTENTION_MODE_ALL, ATTENTION_MODE_MIXED}


def get_group_attention_mode(chat_id: str | int | None) -> str:
    """Return effective attention mode for a group.

    兼容历史配置：旧的 all_message / mixed 统一收敛为 single_message，
    这样无需批量迁移数据，也能保证面板与运行时行为一致。
    """
    if chat_id is None:
        return ATTENTION_MODE_SINGLE
    mode = (get_group_settings(chat_id).get("attention_mode") or ATTENTION_MODE_SINGLE).strip()
    if mode in (ATTENTION_MODE_ALL, ATTENTION_MODE_MIXED):
        return ATTENTION_MODE_SINGLE
    return mode if mode in ATTENTION_MODES else ATTENTION_MODE_SINGLE


def set_group_attention_mode(chat_id: str | int, mode: str) -> None:
    mode = (mode or "").strip()
    if mode != ATTENTION_MODE_SINGLE:
        mode = ATTENTION_MODE_SINGLE
    set_group_setting(chat_id, "attention_mode", mode)


def parse_probability(value: str | int | float | None, default: float = 0.0) -> float:
    """Parse and clamp a probability to [0, 1]. Invalid values use default."""
    try:
        p = float(str(value).strip())
    except (TypeError, ValueError):
        p = default
    if p < 0:
        return 0.0
    if p > 1:
        return 1.0
    return p


def get_group_message_drop_probability(chat_id: str | int | None) -> float:
    if chat_id is None:
        return 0.0
    return parse_probability(get_group_settings(chat_id).get("message_drop_probability"), default=0.0)


def mask_sensitive(value: str, is_sensitive: bool) -> str:
    """Mask sensitive values for display."""
    if not value or value == "default":
        return "（使用默认）"
    if not is_sensitive:
        return value[:30] + ("..." if len(value) > 30 else "")
    if len(value) <= 8:
        return "***"
    return value[:4] + "****" + value[-4:]


def get_group_free_reply_mode(chat_id: str | int) -> bool:
    """Whether free reply mode is enabled for this group."""
    return str(get_group_settings(chat_id).get("free_reply_mode", "false")).lower() in ("1", "true", "yes", "on")


def set_group_free_reply_mode(chat_id: str | int, enabled: bool) -> None:
    set_group_setting(chat_id, "free_reply_mode", "true" if enabled else "false")


def get_group_reply_preference(chat_id: str | int | None) -> str:
    if chat_id is None:
        return "llm_first"
    pref = str(get_group_settings(chat_id).get("reply_preference", "llm_first")).strip().lower()
    return pref if pref in ("llm_first", "mention_first") else "llm_first"


def set_group_reply_preference(chat_id: str | int, preference: str) -> None:
    pref = str(preference or "").strip().lower()
    if pref not in ("llm_first", "mention_first"):
        pref = "llm_first"
    set_group_setting(chat_id, "reply_preference", pref)


def get_group_username_anonymization_enabled(chat_id: str | int | None) -> bool:
    """Whether group member display names should be anonymized in LLM context.

    Defaults to true for backward-compatible privacy.
    """
    if chat_id is None:
        return True
    return str(get_group_settings(chat_id).get("username_anonymization_enabled", "true")).lower() in ("1", "true", "yes", "on")


def set_group_username_anonymization_enabled(chat_id: str | int, enabled: bool) -> None:
    set_group_setting(chat_id, "username_anonymization_enabled", "true" if enabled else "false")


# ── Skill 市场订阅 ────────────────────────────

def get_enabled_skills(chat_id: str | int | None) -> list[str]:
    """获取群已订阅的本地 skill 文件夹名列表。"""
    if chat_id is None:
        return []
    val = get_group_settings(chat_id).get("enabled_skills", "[]")
    try:
        ids = json.loads(val)
        return [str(x).strip() for x in ids if isinstance(x, (int, str)) and str(x).strip()]
    except (json.JSONDecodeError, TypeError):
        return []


def set_enabled_skills(chat_id: str | int, skill_ids: list[str | int]) -> None:
    normalized = [str(x).strip() for x in skill_ids if str(x).strip()]
    set_group_setting(chat_id, "enabled_skills", json.dumps(normalized, ensure_ascii=False))


def add_enabled_skill(chat_id: str | int, skill_id: str | int) -> list[str]:
    """订阅一个 skill，返回更新后的列表"""
    sid = str(skill_id).strip()
    ids = get_enabled_skills(chat_id)
    if sid and sid not in ids:
        ids.append(sid)
        set_enabled_skills(chat_id, ids)
    return ids


def remove_enabled_skill(chat_id: str | int, skill_id: str | int) -> list[str]:
    """退订一个 skill，返回更新后的列表"""
    sid = str(skill_id).strip()
    ids = get_enabled_skills(chat_id)
    if sid in ids:
        ids.remove(sid)
        set_enabled_skills(chat_id, ids)
    return ids



# ── 群工具禁用列表 ────────────────────────────

def get_group_disabled_tools(chat_id: str | int | None) -> list[str]:
    if chat_id is None:
        return []
    val = get_group_settings(chat_id).get("disabled_tools", "[]")
    try:
        data = json.loads(val)
        if isinstance(data, list):
            return [str(x).strip() for x in data if str(x).strip()]
    except (json.JSONDecodeError, TypeError):
        pass
    return []


def set_group_disabled_tools(chat_id: str | int, tool_names: list[str | int]) -> None:
    normalized = sorted({str(x).strip() for x in tool_names if str(x).strip()})
    set_group_setting(chat_id, "disabled_tools", json.dumps(normalized, ensure_ascii=False))


def add_group_disabled_tool(chat_id: str | int, tool_name: str | int) -> list[str]:
    name = str(tool_name).strip()
    tools = get_group_disabled_tools(chat_id)
    if name and name not in tools:
        tools.append(name)
        set_group_disabled_tools(chat_id, tools)
    return get_group_disabled_tools(chat_id)


def remove_group_disabled_tool(chat_id: str | int, tool_name: str | int) -> list[str]:
    name = str(tool_name).strip()
    tools = [x for x in get_group_disabled_tools(chat_id) if x != name]
    set_group_disabled_tools(chat_id, tools)
    return tools

# ── Skill 私密信息 ────────────────────────────

def get_skill_secrets(chat_id: str | int | None) -> dict[str, str]:
    """获取本群所有 Skill 的私密信息，返回 {skill_id_str: secret_text}"""
    if chat_id is None:
        return {}
    val = get_group_settings(chat_id).get("skill_secrets", "{}")
    try:
        data = json.loads(val)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (json.JSONDecodeError, TypeError):
        pass
    return {}


def get_skill_secret(chat_id: str | int | None, skill_id: int | str) -> str | None:
    """获取本群某个 Skill 的私密信息，无则返回 None"""
    return get_skill_secrets(chat_id).get(str(skill_id))


def set_skill_secret(chat_id: str | int, skill_id: int | str, text: str | None) -> None:
    """设置/清除本群某个 Skill 的私密信息，text 为空或 None 则删除"""
    secrets = get_skill_secrets(chat_id)
    key = str(skill_id)
    if text and text.strip():
        secrets[key] = text.strip()
    else:
        secrets.pop(key, None)
    set_group_setting(chat_id, "skill_secrets", json.dumps(secrets, ensure_ascii=False))


def remove_skill_secret(chat_id: str | int, skill_id: int | str) -> None:
    """删除本群某个 Skill 的私密信息"""
    set_skill_secret(chat_id, skill_id, None)
