"""
Business Chatbot 用户设置持久化存储。

每个用户（Telegram Business 账号所有者）通过私聊配置：
  - llm_provider: 自定义 LLM API 协议
  - llm_api_key: 自定义 LLM API Key
  - llm_api_base: 自定义 LLM API Base URL
  - llm_model: 自定义 LLM 模型名
  - persona: 自定义人设文本（支持上传 markdown 文件）
  - persona_file_name: 上传的人设文件名（展示用）

默认人设使用咪姆酱风格，但名字替换为用户自己的名字。
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass
from threading import Lock

from app.runtime_config import get_shared_runtime_config
from app_config.config import DATA_DIR, WORKSPACE_DIR

SETTINGS_PATH = os.path.join(DATA_DIR, "business_user_settings.json")
_lock = Lock()
_RUNTIME_CONFIG = get_shared_runtime_config()


def _load() -> dict:
    try:
        with open(SETTINGS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _save(data: dict) -> None:
    tmp = SETTINGS_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
    os.replace(tmp, SETTINGS_PATH)


@dataclass(slots=True)
class BusinessUserSettings:
    """单个用户的 Business 设置。"""

    llm_provider: str = ""
    llm_api_key: str = ""
    llm_api_base: str = ""
    llm_model: str = ""
    persona: str = ""
    persona_file_name: str = ""
    mode: str = "chat"  # "chat" | "synonym"
    aphasia_enabled: str = "false"  # "true" | "false"
    sticker_enabled: str = "false"
    multi_message_enabled: str = "true"

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, d: dict) -> BusinessUserSettings:
        return cls(
            llm_provider=d.get("llm_provider", ""),
            llm_api_key=d.get("llm_api_key", ""),
            llm_api_base=d.get("llm_api_base", ""),
            llm_model=d.get("llm_model", ""),
            persona=d.get("persona", ""),
            persona_file_name=d.get("persona_file_name", ""),
            mode=d.get("mode", "chat"),
            aphasia_enabled=d.get("aphasia_enabled", "false"),
            sticker_enabled=d.get("sticker_enabled", "false"),
            multi_message_enabled=d.get("multi_message_enabled", "true"),
        )

    def effective_provider(self) -> str:
        return self.llm_provider if self.llm_provider else _RUNTIME_CONFIG.get_effective_llm(None).provider

    def effective_api_key(self) -> str:
        return self.llm_api_key if self.llm_api_key else _RUNTIME_CONFIG.get_effective_llm(None).api_key

    def effective_api_base(self) -> str:
        return self.llm_api_base if self.llm_api_base else _RUNTIME_CONFIG.get_effective_llm(None).api_base

    def effective_model(self) -> str:
        return self.llm_model if self.llm_model else _RUNTIME_CONFIG.get_effective_llm(None).model

    def has_custom_llm(self) -> bool:
        return bool(self.llm_provider or self.llm_api_key or self.llm_api_base or self.llm_model)

    def has_custom_persona(self) -> bool:
        return bool(self.persona.strip())

    def is_synonym_mode(self) -> bool:
        return self.mode == "synonym"

    def is_aphasia_enabled(self) -> bool:
        return self.aphasia_enabled.lower() in ("true", "1", "yes", "on")


    def is_sticker_enabled(self) -> bool:
        return self.sticker_enabled.lower() in ("true", "1", "yes", "on")

    def is_multi_message_enabled(self) -> bool:
        return self.multi_message_enabled.lower() in ("true", "1", "yes", "on")


def get_user_settings(user_id: str | int) -> BusinessUserSettings:
    uid = str(user_id)
    with _lock:
        all_data = _load()
        user_data = all_data.get(uid, {})
    return BusinessUserSettings.from_dict(user_data)


def set_user_setting(user_id: str | int, key: str, value: str) -> None:
    uid = str(user_id)
    if key == "llm_provider":
        value = str(value).strip().lower()
        if value not in {"openai_compatible", "openai_responses", "anthropic"}:
            raise ValueError("invalid llm_provider")
    with _lock:
        all_data = _load()
        if uid not in all_data:
            all_data[uid] = {}
        all_data[uid][key] = value
        _save(all_data)


def reset_user_setting(user_id: str | int, key: str) -> None:
    uid = str(user_id)
    with _lock:
        all_data = _load()
        if uid in all_data and key in all_data[uid]:
            del all_data[uid][key]
            if not all_data[uid]:
                del all_data[uid]
            _save(all_data)
