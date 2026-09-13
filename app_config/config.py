"""Telegram Chat Bot 配置
从本项目本地 project_config.json / 环境变量读取配置，不依赖 AstrBot 配置。
"""
import os
import json
import logging
from typing import Any

from sqlalchemy import delete, func, select
from stores.orm import WhitelistChat, orm_session

logger = logging.getLogger(__name__)

# ============================================================
# 路径常量
# ============================================================
WORKSPACE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = os.path.join(WORKSPACE_DIR, "data")
os.makedirs(DATA_DIR, exist_ok=True)

# Prefer keeping local runtime/config data under data/ so the project root stays tidy.
LOCAL_CONFIG_PATH = os.path.join(DATA_DIR, "project_config.json")
LEGACY_LOCAL_CONFIG_PATH = os.path.join(WORKSPACE_DIR, "project_config.json")
WHITELIST_FILE = os.path.join(DATA_DIR, "whitelist.json")  # legacy JSON import path
GROUP_CONFIG_DB_FILE = os.path.join(DATA_DIR, "group_config.sqlite3")

# ============================================================
# 本地配置加载
# ============================================================
_local_cfg: dict[str, Any] = {}
try:
    config_path = LOCAL_CONFIG_PATH if os.path.exists(LOCAL_CONFIG_PATH) else LEGACY_LOCAL_CONFIG_PATH
    with open(config_path, "r", encoding="utf-8") as f:
        _local_cfg = json.load(f)
except FileNotFoundError:
    logger.warning(f"未找到本地配置: {LOCAL_CONFIG_PATH}，将仅使用环境变量")
except Exception:
    logger.exception(f"读取本地配置失败: {LOCAL_CONFIG_PATH}")


def _cfg_get(name: str, default: Any = None) -> Any:
    if name in os.environ:
        return os.environ[name]
    if name in _local_cfg:
        return _local_cfg[name]
    return default


def _cfg_get_list(name: str, default: list[str] | None = None) -> list[str]:
    if name in os.environ:
        raw = os.environ[name]
        return [x.strip() for x in raw.split(",") if x.strip()]
    val = _local_cfg.get(name, default if default is not None else [])
    if isinstance(val, list):
        return [str(x).strip() for x in val if str(x).strip()]
    if isinstance(val, str):
        return [x.strip() for x in val.split(",") if x.strip()]
    return list(default or [])


# --- LLM 配置 ---
LLM_PROVIDER = str(_cfg_get("LLM_PROVIDER", "openai_compatible")).strip().lower()
LLM_API_BASE = str(_cfg_get("LLM_API_BASE", "https://api.openai.com/v1"))
LLM_API_KEY = str(_cfg_get("LLM_API_KEY", ""))
LLM_MODEL = str(_cfg_get("LLM_MODEL", "gpt-4o-mini"))
LLM_TIMEOUT = int(_cfg_get("LLM_TIMEOUT", 120))
LLM_TEMPERATURE = float(_cfg_get("LLM_TEMPERATURE", 0.9))
LLM_MAX_TOKENS = int(_cfg_get("LLM_MAX_TOKENS", 1024))

# --- Image Generation 配置 ---
IMAGE_GEN_API_BASE = str(_cfg_get("IMAGE_GEN_API_BASE", ""))
IMAGE_GEN_API_KEY = str(_cfg_get("IMAGE_GEN_API_KEY", ""))
IMAGE_GEN_MODEL = str(_cfg_get("IMAGE_GEN_MODEL", ""))
IMAGE_GEN_TIMEOUT = int(_cfg_get("IMAGE_GEN_TIMEOUT", 120))

# --- Image Caption 配置（可选；为空时图片转文字功能自动跳过）---
CAPTION_API_BASE = str(_cfg_get("CAPTION_API_BASE", ""))
CAPTION_API_KEY = str(_cfg_get("CAPTION_API_KEY", ""))
CAPTION_MODEL = str(_cfg_get("CAPTION_MODEL", ""))

# --- Tavily 搜索配置 ---
TAVILY_API_KEY = str(_cfg_get("TAVILY_API_KEY", ""))

# --- 沙箱配置 ---
# provider: e2b 或 shipyard。默认保持 e2b，运行时可在 data/project_config.json 中切换。
SANDBOX_PROVIDER = str(_cfg_get("SANDBOX_PROVIDER", "e2b")).strip().lower()

# E2B 代码沙箱配置
E2B_API_KEY = str(_cfg_get("E2B_API_KEY", ""))
# 沙箱在最后一次操作后空闲多少秒自动关闭。
# 建议通过 project_config.json 显式配置；当前项目使用 300 秒（5 分钟）。
E2B_TIMEOUT = int(_cfg_get("E2B_TIMEOUT", 120))

# Shipyard/Bay 沙箱配置（AstrBotDevs/shipyard）
SHIPYARD_ENDPOINT = str(_cfg_get("SHIPYARD_ENDPOINT", "http://127.0.0.1:8156")).rstrip("/")
SHIPYARD_ACCESS_TOKEN = str(_cfg_get("SHIPYARD_ACCESS_TOKEN", "secret-token"))
SHIPYARD_TTL = int(_cfg_get("SHIPYARD_TTL", 3600))
SHIPYARD_MAX_SESSIONS = int(_cfg_get("SHIPYARD_MAX_SESSIONS", 1))
SHIPYARD_CPUS = float(_cfg_get("SHIPYARD_CPUS", 1.0))
SHIPYARD_MEMORY = str(_cfg_get("SHIPYARD_MEMORY", "512m"))
SHIPYARD_EXEC_TIMEOUT = int(_cfg_get("SHIPYARD_EXEC_TIMEOUT", 180))

# Local 沙箱配置：在本机隔离工作目录中执行 Python/Shell（仅限可信部署使用）
LOCAL_SANDBOX_WORKDIR = str(_cfg_get("LOCAL_SANDBOX_WORKDIR", os.path.join(DATA_DIR, "local_sandbox")))
LOCAL_SANDBOX_TIMEOUT = int(_cfg_get("LOCAL_SANDBOX_TIMEOUT", 180))


# --- Telegram 配置 ---
BOT_TOKEN = str(_cfg_get("BOT_TOKEN", ""))

# --- 基础运行配置 ---
LOG_LEVEL = str(_cfg_get("LOG_LEVEL", "INFO")).upper()
STICKER_SETS = _cfg_get_list("STICKER_SETS", ["RinCat_SD_AC33D506", "PROs_Misc_Collection"])

# --- 管理员 ---
ADMIN_IDS = set(_cfg_get_list("ADMIN_IDS", []))


# ============================================================
# 行为参数
# ============================================================
TRIGGER_PROBABILITY = float(_cfg_get("TRIGGER_PROBABILITY", "0"))  # 随机触发已关闭
CONTEXT_MESSAGE_COUNT = int(_cfg_get("CONTEXT_MESSAGE_COUNT", "100"))  # 上下文消息数
CONTEXT_MAX_TEXT_CHARS = int(_cfg_get("CONTEXT_MAX_TEXT_CHARS", "700"))  # 单条用户上下文最大字符数
BOT_CONTEXT_MAX_CHARS = int(_cfg_get("BOT_CONTEXT_MAX_CHARS", "1400"))   # 单条 Bot 回复上下文最大字符数
CONTEXT_PERSIST_EVERY = int(_cfg_get("CONTEXT_PERSIST_EVERY", "5"))      # 非 Bot 消息每 N 条落盘
TOOL_RESULT_MAX_CHARS = int(_cfg_get("TOOL_RESULT_MAX_CHARS", "6000"))   # 工具返回给模型的最大字符数
MAX_IMAGE_DOWNLOAD_BYTES = int(_cfg_get("MAX_IMAGE_DOWNLOAD_BYTES", "900000"))  # 图片输入最大下载字节

MSG_SEPARATOR = "[MSG]"                          # LLM 输出中的消息分隔符
STICKER_PREFIX = "[STICKER:"                     # 贴纸标记前缀
STICKER_SUFFIX = "]"                             # 贴纸标记后缀
SEARCH_TOOL_NAME = "search_web"                  # LLM function call 工具名
MAX_SEARCH_RESULTS = 5                           # 每次搜索返回结果数
IDLE_TOPIC_IDLE_HOURS = int(_cfg_get("IDLE_TOPIC_IDLE_HOURS", "6"))             # 空闲多久后尝试引出话题
IDLE_TOPIC_SCAN_INTERVAL_SECONDS = int(_cfg_get("IDLE_TOPIC_SCAN_INTERVAL_SECONDS", "3600"))  # 扫描周期
IDLE_TOPIC_MAX_PER_RUN = int(_cfg_get("IDLE_TOPIC_MAX_PER_RUN", "3"))            # 每轮最多主动发几条
STREAM_ENABLED = str(_cfg_get("STREAM_ENABLED", "false")).lower() in ("1", "true", "yes", "on")  # LLM 流式输出开关
BOT_CALL_ALIASES = _cfg_get_list("BOT_CALL_ALIASES", ["猫猫", "小猫", "咪姆", "咪姆酱"])  # 群里直接叫 Bot 的别名

# Guest Mode（Bot API 10.0）
GUEST_MODE_ENABLED = str(_cfg_get("GUEST_MODE_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
GUEST_MODE_MAX_REPLY_CHARS = int(_cfg_get("GUEST_MODE_MAX_REPLY_CHARS", "800"))
GUEST_ALLOWED_USER_IDS = set(_cfg_get_list("GUEST_ALLOWED_USER_IDS", []))

# 私聊主体对话开关：true 时机器人会在私聊中正常聊天（非命令消息）。
# 关闭时仅响应命令（/gadmin、/settings 等），非命令消息被忽略。
PRIVATE_CHAT_ENABLED = str(_cfg_get("PRIVATE_CHAT_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
PRIVATE_ALLOWED_USER_IDS = set(_cfg_get_list("PRIVATE_ALLOWED_USER_IDS", []))

# Agent / Tool 配置
AGENT_MAX_ROUNDS = int(_cfg_get("AGENT_MAX_ROUNDS", "10"))
TEXT_TOOL_ENABLED = str(_cfg_get("TEXT_TOOL_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
GUEST_TOOL_ENABLED = str(_cfg_get("GUEST_TOOL_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
# 插件配置：能扫描到的插件默认启用；按插件名精确禁用。
PLUGINS_DISABLED = set(_cfg_get_list("PLUGINS_DISABLED", []))

# --- Web Panel 配置 ---
WEB_PANEL_ENABLED = str(_cfg_get("WEB_PANEL_ENABLED", "false")).lower() in ("1", "true", "yes", "on")
WEB_PANEL_HOST = str(_cfg_get("WEB_PANEL_HOST", "127.0.0.1"))
WEB_PANEL_PORT = int(_cfg_get("WEB_PANEL_PORT", "7860"))
WEB_PANEL_ACCESS_TOKEN = str(_cfg_get("WEB_PANEL_ACCESS_TOKEN", ""))
WEB_PANEL_ALLOW_REMOTE_WITHOUT_TOKEN = str(_cfg_get("WEB_PANEL_ALLOW_REMOTE_WITHOUT_TOKEN", "false")).lower() in ("1", "true", "yes", "on")
WEB_PANEL_PUBLIC_BASE_URL = str(_cfg_get("WEB_PANEL_PUBLIC_BASE_URL", ""))
WEB_PANEL_RESTART_ENABLED = str(_cfg_get("WEB_PANEL_RESTART_ENABLED", "false")).lower() in ("1", "true", "yes", "on")
WEB_PANEL_RESTART_COMMAND = str(_cfg_get("WEB_PANEL_RESTART_COMMAND", ""))
WEB_PANEL_SKILL_UPLOAD_ENABLED = str(_cfg_get("WEB_PANEL_SKILL_UPLOAD_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
WEB_PANEL_SKILL_UPLOAD_MAX_BYTES = int(_cfg_get("WEB_PANEL_SKILL_UPLOAD_MAX_BYTES", "2097152"))
LOCAL_SKILL_ROOT = str(_cfg_get("LOCAL_SKILL_ROOT", os.path.join(DATA_DIR, "skills")))
FOCUS_LIGHT_HINT_ENABLED = str(_cfg_get("FOCUS_LIGHT_HINT_ENABLED", "true")).lower() in ("1", "true", "yes", "on")  # 聚焦模式低频轻提示
FOCUS_LIGHT_HINT_PROBABILITY = float(_cfg_get("FOCUS_LIGHT_HINT_PROBABILITY", "0.25"))  # 聚焦模式下轻跟聊概率
FOCUS_LIGHT_HINT_COOLDOWN_SECONDS = int(_cfg_get("FOCUS_LIGHT_HINT_COOLDOWN_SECONDS", "60"))  # 同一群轻提示最小间隔（1 分钟）
FOCUS_LIGHT_HINT_MIN_CHARS = int(_cfg_get("FOCUS_LIGHT_HINT_MIN_CHARS", "8"))  # 普通文本最短长度
FOCUS_JOIN_SCORE_THRESHOLD = int(_cfg_get("FOCUS_JOIN_SCORE_THRESHOLD", "6"))  # 聚焦加入讨论的最低评分（0-10）
FOCUS_JOIN_MAX_PER_SESSION = int(_cfg_get("FOCUS_JOIN_MAX_PER_SESSION", "5"))  # 聚焦模式下单次激活最多插话次数（不再自动暂停，仅记录）
FOCUS_STAGE1_THRESHOLD = int(_cfg_get("FOCUS_STAGE1_THRESHOLD", "4"))  # 第一阶段 LLM 评分通过阈值（低于此值不进入第二阶段）
RECENT_CONTEXT_MESSAGES = int(_cfg_get("RECENT_CONTEXT_MESSAGES", "25"))  # LLM 可见的最近群聊历史条数
RECENT_CONTEXT_MAX_BOT_CHARS = int(_cfg_get("RECENT_CONTEXT_MAX_BOT_CHARS", "180"))  # 旧 Bot 回复作为参考时的最大长度

# ── 拟人化增强配置 ─────────────────────────────
PERSONALITY_ENABLED = str(_cfg_get("PERSONALITY_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
TYPO_RHYTHM_ENABLED = str(_cfg_get("TYPO_RHYTHM_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
CONVERSATION_MEMORY_ENABLED = str(_cfg_get("CONVERSATION_MEMORY_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
MICRO_ACTIONS_ENABLED = str(_cfg_get("MICRO_ACTIONS_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
DE_AI_ENABLED = str(_cfg_get("DE_AI_ENABLED", "true")).lower() in ("1", "true", "yes", "on")

# ── 用户人格记忆配置 ────────────────────────────
PERSONA_MEMORY_ENABLED = str(_cfg_get("PERSONA_MEMORY_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
PERSONA_MEMORY_DB_FILE = str(_cfg_get("PERSONA_MEMORY_DB_FILE", "persona_memory.sqlite3"))
PERSONA_MEMORY_UPDATE_ENABLED = str(_cfg_get("PERSONA_MEMORY_UPDATE_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
PERSONA_MEMORY_MAX_USERS_PER_TURN = int(_cfg_get("PERSONA_MEMORY_MAX_USERS_PER_TURN", "8"))
PERSONA_MEMORY_PROMPT_MAX_CHARS = int(_cfg_get("PERSONA_MEMORY_PROMPT_MAX_CHARS", "1800"))
PERSONA_MEMORY_TOTAL_MAX_CHARS = int(_cfg_get("PERSONA_MEMORY_TOTAL_MAX_CHARS", "700"))
PERSONA_MEMORY_FIELD_MAX_CHARS = int(_cfg_get("PERSONA_MEMORY_FIELD_MAX_CHARS", "120"))
PERSONA_MEMORY_LIST_MAX_ITEMS = int(_cfg_get("PERSONA_MEMORY_LIST_MAX_ITEMS", "5"))
PERSONA_MEMORY_ITEM_MAX_CHARS = int(_cfg_get("PERSONA_MEMORY_ITEM_MAX_CHARS", "50"))
PERSONA_MEMORY_UPDATE_MIN_CONFIDENCE = float(_cfg_get("PERSONA_MEMORY_UPDATE_MIN_CONFIDENCE", "0.65"))
PERSONA_MEMORY_UPDATE_INTERVAL_SECONDS = int(_cfg_get("PERSONA_MEMORY_UPDATE_INTERVAL_SECONDS", "30"))

# ── 拟人化行为层配置 ────────────────────────────
HUMANIZATION_ENABLED = str(_cfg_get("HUMANIZATION_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
HUMANIZATION_INTENSITY = float(_cfg_get("HUMANIZATION_INTENSITY", "0.7"))
HUMANIZATION_STYLE = str(_cfg_get("HUMANIZATION_STYLE", "balanced"))  # light / balanced / clingy / quiet
HUMANIZATION_STATE_DB_FILE = str(_cfg_get("HUMANIZATION_STATE_DB_FILE", "human_behavior.sqlite3"))
BACKPRESSURE_GLOBAL_MAX = int(_cfg_get("BACKPRESSURE_GLOBAL_MAX", "5"))
BACKPRESSURE_PER_CHAT_MAX = int(_cfg_get("BACKPRESSURE_PER_CHAT_MAX", "4"))
TELEGRAM_CONCURRENT_UPDATES = int(_cfg_get("TELEGRAM_CONCURRENT_UPDATES", "8"))  # Telegram update 并发处理数；同一会话仍由 handler 内部锁串行

# ── Business Chatbot 配置 ────────────────────────
BUSINESS_ENABLED = str(_cfg_get("BUSINESS_ENABLED", "true")).lower() in ("1", "true", "yes", "on")
BUSINESS_ALLOWED_USER_IDS = set(_cfg_get_list("BUSINESS_ALLOWED_USER_IDS", []))
BUSINESS_MAX_REPLY_CHARS = int(_cfg_get("BUSINESS_MAX_REPLY_CHARS", "800"))
BUSINESS_TYPING_DELAY_MIN = float(_cfg_get("BUSINESS_TYPING_DELAY_MIN", "0.8"))
BUSINESS_TYPING_DELAY_MAX = float(_cfg_get("BUSINESS_TYPING_DELAY_MAX", "4.5"))
BUSINESS_TYPING_DELAY_PER_CHAR = float(_cfg_get("BUSINESS_TYPING_DELAY_PER_CHAR", "0.04"))  # 每字延迟秒数

# ============================================================
# 白名单持久化（SQLAlchemy ORM + SQLite；兼容旧 whitelist.json 自动迁移）
# ============================================================
def _legacy_whitelist_ids() -> set[str]:
    if os.path.exists(WHITELIST_FILE):
        try:
            with open(WHITELIST_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {str(x).strip() for x in data.get("group_ids", []) if str(x).strip()}
        except Exception:
            logger.exception("加载旧白名单 JSON 失败")
    return set()


def _migrate_whitelist_json_if_needed(session) -> None:
    existing = session.scalar(select(func.count()).select_from(WhitelistChat)) or 0
    if int(existing) > 0:
        return
    legacy_ids = _legacy_whitelist_ids()
    if not legacy_ids:
        return
    session.add_all([WhitelistChat(chat_id=cid) for cid in sorted(legacy_ids)])
    session.flush()
    logger.info("已从旧 whitelist.json 迁移白名单到 SQLite：%s 个群组", len(legacy_ids))


def load_whitelist() -> set:
    """从 ORM/SQLite 加载白名单，并从旧 whitelist.json 自动迁移。"""
    try:
        with orm_session(GROUP_CONFIG_DB_FILE) as session:
            _migrate_whitelist_json_if_needed(session)
            ids = session.scalars(select(WhitelistChat.chat_id)).all()
            return {str(cid).strip() for cid in ids if str(cid).strip()}
    except Exception:
        logger.exception("加载白名单 ORM 失败，回退读取旧 JSON")
        return _legacy_whitelist_ids()


def save_whitelist(group_ids: set):
    """保存白名单到 ORM/SQLite；旧 whitelist.json 仅作为迁移来源，不再写入。"""
    normalized = sorted({str(x).strip() for x in group_ids if str(x).strip()})
    try:
        with orm_session(GROUP_CONFIG_DB_FILE) as session:
            session.execute(delete(WhitelistChat))
            session.add_all([WhitelistChat(chat_id=cid) for cid in normalized])
    except Exception:
        logger.exception("保存白名单 ORM 失败")

# ============================================================
# 启动校验
# ============================================================
def _is_missing_secret(value: str) -> bool:
    value = (value or "").strip()
    return (
        not value
        or value.startswith("<")
        or value.endswith(">")
        or "placeholder" in value.lower()
    )


def validate_config():
    errors = []
    if _is_missing_secret(BOT_TOKEN):
        errors.append("BOT_TOKEN 未设置或仍为占位值（请在 project_config.json 或环境变量中提供）")
    if _is_missing_secret(LLM_API_KEY):
        errors.append("LLM_API_KEY 未设置或仍为占位值（请在 project_config.json 或环境变量中提供）")
    if LLM_PROVIDER not in {"openai_compatible", "openai_responses", "anthropic"}:
        errors.append("LLM_PROVIDER 必须是 openai_compatible、openai_responses 或 anthropic")
    if not TAVILY_API_KEY:
        logger.warning("TAVILY_API_KEY 未设置，搜索功能将不可用")
    return errors


def log_config():
    logger.info(
        "配置: provider=%s model=%s admins=%s business=%s guest=%s stickers=%s",
        LLM_PROVIDER,
        LLM_MODEL,
        sorted(ADMIN_IDS),
        "on" if BUSINESS_ENABLED else "off",
        "on" if GUEST_MODE_ENABLED else "off",
        len(STICKER_SETS),
    )
    logger.info("📋 当前配置:")
    logger.info(f"  LLM Provider: {LLM_PROVIDER}")
    logger.info(f"  LLM Base:  {LLM_API_BASE}")
    logger.info(f"  LLM Model: {LLM_MODEL}")
    logger.info(f"  LLM Key:   {LLM_API_KEY[:12]}...{LLM_API_KEY[-4:] if LLM_API_KEY else 'N/A'}")
    logger.info(f"  Tavily Key: {'✅' if TAVILY_API_KEY else '❌'}")
    logger.info(f"  Bot Token:  {BOT_TOKEN[:10]}...{BOT_TOKEN[-4:] if BOT_TOKEN else 'N/A'}")
    logger.info(f"  Admins:     {ADMIN_IDS}")
    logger.info(f"  Trigger:    {TRIGGER_PROBABILITY*100:.1f}% (random disabled)")
    logger.info(f"  Focus hint: {'✅' if FOCUS_LIGHT_HINT_ENABLED else '❌'} p={FOCUS_LIGHT_HINT_PROBABILITY*100:.1f}% cooldown={FOCUS_LIGHT_HINT_COOLDOWN_SECONDS}s")
    logger.info(f"  Context:    {CONTEXT_MESSAGE_COUNT} msgs")
    logger.info(f"  Recent ctx: {RECENT_CONTEXT_MESSAGES} msgs, bot_ref={RECENT_CONTEXT_MAX_BOT_CHARS} chars")
    logger.info(f"  Stream:     {'✅ on' if STREAM_ENABLED else '❌ off'}")
    logger.info(f"  Ctx clip:   user={CONTEXT_MAX_TEXT_CHARS} chars, bot={BOT_CONTEXT_MAX_CHARS} chars")
    logger.info(f"  Guest Mode: {'✅' if GUEST_MODE_ENABLED else '❌'}  max_reply={GUEST_MODE_MAX_REPLY_CHARS}")
    logger.info("=" * 50)
