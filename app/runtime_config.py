"""统一运行时配置访问层。

目标：
1. 给新架构代码提供单一配置入口，减少直接散落引用 legacy config / model_store / group_settings_store。
2. 先做薄封装，避免一次性大改老模块。
3. 后续新代码优先依赖 RuntimeConfig，而不是直接 import config。
"""

from __future__ import annotations

from dataclasses import dataclass

import app_config.config as legacy_config
from stores.group_settings_store import (
    get_group_attention_mode,
    get_group_free_reply_mode,
    get_group_image_gen_api_base,
    get_group_image_gen_api_key,
    get_group_image_gen_model,
    get_group_llm_api_base,
    get_group_llm_api_key,
    get_group_llm_model,
    get_group_llm_provider,
    get_group_persona_prompt,
    get_group_scoring_criteria,
    get_group_tavily_api_key,
    get_group_username_anonymization_enabled,
)
from stores.model_store import get_active_model


@dataclass(frozen=True)
class EffectiveLLMConfig:
    provider: str
    model: str
    api_key: str
    api_base: str


@dataclass(frozen=True)
class EffectiveImageGenConfig:
    model: str
    api_key: str
    api_base: str


class RuntimeConfig:
    """统一的运行时配置访问入口。"""

    # ---- app/global ----
    @property
    def bot_token(self) -> str:
        return legacy_config.BOT_TOKEN

    @property
    def concurrent_updates(self) -> int:
        return max(1, legacy_config.TELEGRAM_CONCURRENT_UPDATES)

    @property
    def context_message_count(self) -> int:
        return legacy_config.CONTEXT_MESSAGE_COUNT

    @property
    def context_max_text_chars(self) -> int:
        return legacy_config.CONTEXT_MAX_TEXT_CHARS

    @property
    def bot_context_max_chars(self) -> int:
        return legacy_config.BOT_CONTEXT_MAX_CHARS

    @property
    def admin_ids(self) -> list[int]:
        return [int(value) for value in legacy_config.ADMIN_IDS]

    @property
    def business_enabled(self) -> bool:
        return legacy_config.BUSINESS_ENABLED

    @property
    def llm_timeout(self) -> int:
        return legacy_config.LLM_TIMEOUT

    @property
    def llm_temperature(self) -> float:
        return legacy_config.LLM_TEMPERATURE

    @property
    def llm_max_tokens(self) -> int:
        return legacy_config.LLM_MAX_TOKENS

    @property
    def stream_enabled(self) -> bool:
        return legacy_config.STREAM_ENABLED

    # ---- feature flags from legacy config (阶段性兼容) ----
    @property
    def guest_mode_max_reply_chars(self) -> int:
        return legacy_config.GUEST_MODE_MAX_REPLY_CHARS

    @property
    def business_max_reply_chars(self) -> int:
        return legacy_config.BUSINESS_MAX_REPLY_CHARS

    @property
    def personality_enabled(self) -> bool:
        return legacy_config.PERSONALITY_ENABLED

    @property
    def conversation_memory_enabled(self) -> bool:
        return legacy_config.CONVERSATION_MEMORY_ENABLED

    @property
    def persona_memory_enabled(self) -> bool:
        return legacy_config.PERSONA_MEMORY_ENABLED

    @property
    def humanization_enabled(self) -> bool:
        return legacy_config.HUMANIZATION_ENABLED

    @property
    def tool_result_max_chars(self) -> int:
        return legacy_config.TOOL_RESULT_MAX_CHARS

    @property
    def max_image_download_bytes(self) -> int:
        return legacy_config.MAX_IMAGE_DOWNLOAD_BYTES

    @property
    def recent_context_messages(self) -> int:
        return legacy_config.RECENT_CONTEXT_MESSAGES

    @property
    def recent_context_max_bot_chars(self) -> int:
        return legacy_config.RECENT_CONTEXT_MAX_BOT_CHARS

    @property
    def agent_max_rounds(self) -> int:
        return legacy_config.AGENT_MAX_ROUNDS

    @property
    def text_tool_enabled(self) -> bool:
        return legacy_config.TEXT_TOOL_ENABLED

    @property
    def guest_tool_enabled(self) -> bool:
        return legacy_config.GUEST_TOOL_ENABLED

    # ---- effective per-chat configs ----
    def get_effective_llm(self, chat_id: int | None = None) -> EffectiveLLMConfig:
        provider = legacy_config.LLM_PROVIDER
        model = get_active_model() or legacy_config.LLM_MODEL
        api_key = legacy_config.LLM_API_KEY
        api_base = legacy_config.LLM_API_BASE

        if chat_id is not None:
            custom_provider = get_group_llm_provider(chat_id)
            custom_model = get_group_llm_model(chat_id)
            custom_key = get_group_llm_api_key(chat_id)
            custom_base = get_group_llm_api_base(chat_id)
            if custom_provider:
                provider = custom_provider
            if custom_model:
                model = custom_model
            if custom_key:
                api_key = custom_key
            if custom_base:
                api_base = custom_base


        return EffectiveLLMConfig(provider=provider, model=model, api_key=api_key, api_base=api_base)

    def get_effective_image_gen(self, chat_id: int | None = None) -> EffectiveImageGenConfig:
        model = legacy_config.IMAGE_GEN_MODEL
        api_key = legacy_config.IMAGE_GEN_API_KEY
        api_base = legacy_config.IMAGE_GEN_API_BASE

        if chat_id is not None:
            custom_model = get_group_image_gen_model(chat_id)
            custom_key = get_group_image_gen_api_key(chat_id)
            custom_base = get_group_image_gen_api_base(chat_id)
            if custom_model:
                model = custom_model
            if custom_key:
                api_key = custom_key
            if custom_base:
                api_base = custom_base

        return EffectiveImageGenConfig(model=model, api_key=api_key, api_base=api_base)

    def get_effective_tavily_api_key(self, chat_id: int | None = None) -> str:
        if chat_id is not None:
            custom_key = get_group_tavily_api_key(chat_id)
            if custom_key:
                return custom_key
        return legacy_config.TAVILY_API_KEY

    # ---- per-chat behavior ----
    def get_group_persona_prompt(self, chat_id: int | None) -> str:
        return get_group_persona_prompt(chat_id)

    def get_group_scoring_criteria(self, chat_id: int | None) -> str:
        return get_group_scoring_criteria(chat_id)

    def get_group_attention_mode(self, chat_id: int | None) -> str:
        return get_group_attention_mode(chat_id) if chat_id is not None else "single_message"

    def get_group_free_reply_mode(self, chat_id: int | None) -> bool:
        return get_group_free_reply_mode(chat_id) if chat_id is not None else False

    def get_group_username_anonymization_enabled(self, chat_id: int | None) -> bool:
        return get_group_username_anonymization_enabled(chat_id) if chat_id is not None else True


_SHARED_RUNTIME_CONFIG: RuntimeConfig | None = None


def get_shared_runtime_config() -> RuntimeConfig:
    """Return the process-wide runtime configuration facade."""
    global _SHARED_RUNTIME_CONFIG
    if _SHARED_RUNTIME_CONFIG is None:
        _SHARED_RUNTIME_CONFIG = RuntimeConfig()
    return _SHARED_RUNTIME_CONFIG

