"""
群组管理员控制面板 — Group Admin Control Panel

群管理员通过私聊命令 /gadmin 打开面板。
机器人列出该用户担任管理员的群组，选中后可调整：
  - 人设、对话模型/Key/Base、生图 Key/Base/模型、搜索 Key
所有设置仅针对该群组生效。默认接口 Key 不暴露。
模型锁定：使用默认 API Key 时禁止更改模型。
"""
import logging

from app_config.customization import get_text

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler,
    ChatMemberHandler, filters,
)
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app_config.config import (
    ADMIN_IDS,
    load_whitelist,
)
from stores.group_settings_store import (
    get_group_settings,
    set_group_setting,
    reset_group_setting,
    reset_group_settings,
    SETTING_IS_SENSITIVE,
    mask_sensitive,
    get_group_attention_mode,
    set_group_attention_mode,
    get_group_reply_preference,
    set_group_reply_preference,
    get_group_username_anonymization_enabled,
    set_group_username_anonymization_enabled,
    get_group_repeater_enabled,
    set_group_repeater_enabled,
    get_enabled_skills,
    add_enabled_skill,
    remove_enabled_skill,
    get_skill_secret,
    set_skill_secret,
    get_group_disabled_tools,
    add_group_disabled_tool,
    remove_group_disabled_tool,
    ATTENTION_MODE_SINGLE,
    ATTENTION_MODE_ALL,
    ATTENTION_MODE_MIXED,
)
from plugins.manager import get_plugin_manager
from stores.business_settings import (
    get_user_settings as get_biz_settings,
    set_user_setting as set_biz_setting,
    reset_user_setting as reset_biz_setting,
)
from handlers.admin_panel_utils import (
    admin_examples as _admin_examples,
    labels as _labels,
    descriptions as _descriptions,
    mdv2 as _mdv2,
    safe_edit as _safe_edit,
)


logger = logging.getLogger(__name__)


def _attention_label(mode: str) -> str:
    # 面板侧统一只展示单消息注意力，避免暴露已下线模式。
    return "单消息注意力"

# ── Callback 前缀 ─────────────────────────────────

CB = "gadmin:"
CB_GROUPS = f"{CB}groups"
CB_GROUPS_PAGE = f"{CB}groups_page"
CB_SETTINGS = f"{CB}settings"
CB_EDIT = f"{CB}edit"
CB_RESET = f"{CB}reset"
CB_RESET_ALL = f"{CB}reset_all"
CB_RESET_ALL_CONFIRM = f"{CB}reset_all_confirm"
CB_TOGGLE = f"{CB}toggle"
CB_ATTENTION = f"{CB}attention"
CB_ATTENTION_SET = f"{CB}attention_set"
CB_CATEGORY = f"{CB}category"
CB_CLOSE = f"{CB}close"

# ── Skill 市场回调前缀 ───────────────────────────
CB_SKILL = f"{CB}skill:"
CB_SKILL_SEARCH = f"{CB_SKILL}search"   # 搜索
CB_SKILL_TOGGLE = f"{CB_SKILL}toggle"   # 订阅/退订
CB_SKILL_PAGE = f"{CB_SKILL}page"       # 翻页
CB_SKILL_CLEAR = f"{CB_SKILL}clear"     # 清除搜索
CB_SKILL_SECRET = f"{CB_SKILL}secret"   # 私密信息
CB_TOOL_PANEL = f"{CB}tool_panel"
CB_TOOL_TOGGLE = f"{CB}tool_toggle"

# ── Business 设置回调前缀 ──────────────────────
CB_BIZ = f"{CB}biz:"
CB_BIZ_MODE = f"{CB_BIZ}mode"
CB_BIZ_EDIT = f"{CB_BIZ}edit"
CB_BIZ_RESET = f"{CB_BIZ}reset"
CB_BIZ_TOGGLE = f"{CB_BIZ}toggle"
CB_BIZ_CATEGORY = f"{CB_BIZ}category"


# ── 白名单 + 管理员缓存 ──────────────────────────

_whitelist: set[str] = set()
_admin_cache: dict[str, tuple[list[dict], float]] = {}
_ADMIN_CACHE_TTL = 300

# ── Business 面板构建 ─────────────────────────

BIZ_LABELS = {
    "llm_provider": "API 协议",
    "llm_api_key": "API Key",
    "llm_api_base": "API Base",
    "llm_model": "模型",
    "persona": "人设",
    "sticker_enabled": "贴纸工具",
    "multi_message_enabled": "多句分条",
}

BIZ_CATEGORIES = {
    "mode": {
        "title": "🎭 对话模式",
        "desc": "切换私聊回复模式。",
        "keys": ["mode"],
    },
    "llm": {
        "title": "🧠 LLM 配置",
        "desc": "配置私聊使用的模型、API Key 和 Base URL。",
        "keys": ["llm_provider", "llm_api_key", "llm_api_base", "llm_model"],
    },
    "behavior": {
        "title": "✨ 拟人化增强",
        "desc": "控制轻量记忆、多句分条和贴纸工具。",
        "keys": ["multi_message_enabled", "sticker_enabled"],
    },
    "persona": {
        "title": "📄 人设",
        "desc": "管理私聊的自定义人设。",
        "keys": ["persona"],
    },
}


def _biz_mask_key(value: str) -> str:
    if not value:
        return "使用全局默认"
    return value[:8] + "****" + value[-4:] if len(value) > 12 else value


def _biz_key_status(s, key: str) -> str:
    if key == "mode":
        return "🗣️ 已读乱回" if s.is_synonym_mode() else "💬 经典对话"
    if key == "llm_provider":
        return "🔵 自定义" if s.llm_provider else "🟢 默认"
    if key == "llm_api_key":
        return "🔵 自定义" if s.llm_api_key else "🟢 默认"
    if key == "llm_api_base":
        return "🔵 自定义" if s.llm_api_base else "🟢 默认"
    if key == "llm_model":
        return "🔵 自定义" if s.llm_model else "🟢 默认"
    if key == "persona":
        return "🔵 自定义" if s.has_custom_persona() else "🟢 默认"
    if key == "sticker_enabled":
        return "✅ 开启" if s.is_sticker_enabled() else "❌ 关闭"
    if key == "multi_message_enabled":
        return "✅ 开启" if s.is_multi_message_enabled() else "❌ 关闭"
    return ""


def _build_biz_panel_text(uid: str, user_name: str) -> str:
    s = get_biz_settings(uid)
    lines = [
        f"🐱 私聊设置 — {user_name}",
        "",
        "━━━ 设置分类 ━━━",
    ]
    for cid in ("mode", "llm", "behavior", "persona"):
        cat = BIZ_CATEGORIES[cid]
        summary = "；".join(f"{BIZ_LABELS.get(k, k)}:{_biz_key_status(s, k)}" for k in cat["keys"])
        lines.append(f"• **{cat['title']}**：{summary}")
    lines.append("")
    lines.append("点击下方分类进入独立设置页。")
    return "\n".join(lines)


def _build_biz_keyboard(uid: str) -> InlineKeyboardMarkup:
    kb = [
        [InlineKeyboardButton("🎭 对话模式", callback_data=f"{CB_BIZ_CATEGORY}:mode"),
         InlineKeyboardButton("🧠 LLM 配置", callback_data=f"{CB_BIZ_CATEGORY}:llm")],
        [InlineKeyboardButton("✨ 拟人化增强", callback_data=f"{CB_BIZ_CATEGORY}:behavior"),
         InlineKeyboardButton("📄 人设", callback_data=f"{CB_BIZ_CATEGORY}:persona")],
        [InlineKeyboardButton("🔄 刷新", callback_data=CB_BIZ),
         InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)],
    ]
    return InlineKeyboardMarkup(kb)


def _build_biz_category_text(uid: str, user_name: str, category_id: str) -> str:
    s = get_biz_settings(uid)
    cat = BIZ_CATEGORIES.get(category_id)
    if not cat:
        return "❌ 未知设置分类"
    lines = [
        f"{cat['title']}",
        "",
        f"👤 用户：**{user_name}**",
        "",
        f"📝 {cat['desc']}",
        "",
        "━━━ 当前状态 ━━━",
    ]
    if category_id == "mode":
        lines.append(f"• **当前模式**：{_biz_key_status(s, 'mode')}")
    elif category_id == "llm":
        lines.extend([
            f"• **API 协议**：`{s.llm_provider or '使用全局默认'}`",
            f"• **API Key**：`{_biz_mask_key(s.llm_api_key)}`",
            f"• **API Base**：`{s.llm_api_base or '使用全局默认'}`",
            f"• **模型**：`{s.llm_model or '使用全局默认'}`" + (" ⚠️需自定义Key" if not s.has_custom_llm() else ""),
        ])
    elif category_id == "behavior":
        for key in cat["keys"]:
            lines.append(f"• **{BIZ_LABELS[key]}**：{_biz_key_status(s, key)}")
    elif category_id == "persona":
        persona = f"📄 {s.persona_file_name or '自定义'}" if s.has_custom_persona() else get_text("admin_panel.default_persona_label", "默认咪姆酱风格")
        lines.append(f"• **当前人设**：{persona}")
    lines.append("")
    lines.append("点击下方按钮修改。")
    return "\n".join(lines)


def _build_biz_category_keyboard(uid: str, category_id: str) -> InlineKeyboardMarkup:
    s = get_biz_settings(uid)
    kb = []
    if category_id == "mode":
        kb.append([InlineKeyboardButton(
            "🔁 切换为已读乱回" if not s.is_synonym_mode() else "🔁 切换为经典对话",
            callback_data=CB_BIZ_MODE,
        )])
    elif category_id == "llm":
        kb.extend([
            [InlineKeyboardButton("🔌 修改协议", callback_data=f"{CB_BIZ_EDIT}:llm_provider")],
            [InlineKeyboardButton("🔑 修改 Key", callback_data=f"{CB_BIZ_EDIT}:llm_api_key")],
            [InlineKeyboardButton("🌐 修改 Base", callback_data=f"{CB_BIZ_EDIT}:llm_api_base")],
            [InlineKeyboardButton("🧠 修改模型", callback_data=f"{CB_BIZ_EDIT}:llm_model")],
        ])
    elif category_id == "behavior":
        kb.extend([
            [InlineKeyboardButton(("💬 关闭多句" if s.is_multi_message_enabled() else "💬 开启多句"), callback_data=f"{CB_BIZ_TOGGLE}:multi_message_enabled")],
            [InlineKeyboardButton(("🎴 关闭贴纸" if s.is_sticker_enabled() else "🎴 开启贴纸"), callback_data=f"{CB_BIZ_TOGGLE}:sticker_enabled")],
        ])
    elif category_id == "persona":
        kb.append([InlineKeyboardButton("📄 管理人设", callback_data=f"{CB_BIZ_EDIT}:persona")])
    kb.append([InlineKeyboardButton("🔙 返回私聊设置", callback_data=CB_BIZ),
               InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    return InlineKeyboardMarkup(kb)
# 面板按钮布局
PANEL_KEYS_ROW1 = [
    ("persona_prompt", "🎭 人设"),
    ("morning_greeting_enabled", "🌅 早安"),
    ("evening_greeting_enabled", "🌙 晚安"),
    ("idle_topic_enabled", "🪄 冷群活跃"),
    ("free_reply_mode", "🧵 自由回复"),
    ("reply_preference", "🎯 回复偏好"),
    ("llm_provider", "🔌 对话协议"),
    ("llm_model", "🤖 对话模型"),
    ("llm_api_key", "🔑 对话Key"),
    ("llm_api_base", "🔗 对话Base"),
]
PANEL_KEYS_ROW2 = [
    ("image_gen_api_key", "🎨 生图Key"),
    ("image_gen_api_base", "🌐 生图Base"),
    ("image_gen_model", "🖼️ 生图模型"),
    ("tavily_api_key", "🔍 搜索Key"),
]

MODEL_DEPENDS_ON = {"llm_model": "llm_api_key", "image_gen_model": "image_gen_api_key"}
BOOLEAN_SETTING_KEYS = {"morning_greeting_enabled", "evening_greeting_enabled", "idle_topic_enabled", "free_reply_mode", "repeater_enabled"}

SETTING_CATEGORIES = {
    "persona": {
        "title": "🎭 人设设置",
        "desc": get_text("admin_panel.persona_category_desc", "调整本群专属人设。留空/默认时使用全局咪姆酱风格。"),
        "keys": ["persona_prompt"],
    },
    "chat": {
        "title": "🤖 对话模型",
        "desc": "配置本群聊天使用的模型、API Key 和 Base URL。模型自定义需要先配置对话 Key。",
        "keys": ["llm_provider", "llm_model", "llm_api_key", "llm_api_base"],
    },
    "image": {
        "title": "🎨 生图设置",
        "desc": "配置本群图片生成使用的模型、API Key 和 Base URL。模型自定义需要先配置生图 Key。",
        "keys": ["image_gen_model", "image_gen_api_key", "image_gen_api_base"],
    },
    "search": {
        "title": "🔍 搜索设置",
        "desc": "配置本群联网搜索使用的 Tavily API Key。",
        "keys": ["tavily_api_key"],
    },
    "activity": {
        "title": "⏰ 群活跃设置",
        "desc": "控制早安、晚安和冷群活跃等自动互动能力。",
        "keys": ["morning_greeting_enabled", "evening_greeting_enabled", "idle_topic_enabled"],
    },
    "reply": {
        "title": "💬 回复策略",
        "desc": "控制 Bot 如何选择回复对象、主动插话时偏向的判断方式、复读机开关，以及普通主动消息的丢弃概率。自由回复开启后，LLM 可以选择最近上下文中的一条或多条消息分别回复。",
        "keys": ["free_reply_mode", "reply_preference", "repeater_enabled", "message_drop_probability"],
    },
    "privacy": {
        "title": "🔒 隐私显示",
        "desc": "控制群聊上下文里成员名字是否显示为脱敏标签。关闭后，LLM 会直接看到 Telegram 昵称。",
        "keys": ["username_anonymization_enabled"],
    },
}


def inject_whitelist(wl: set[str]):
    global _whitelist
    _whitelist = wl


def invalidate_admin_cache(user_id: int | None = None):
    if user_id is None:
        _admin_cache.clear()
    else:
        _admin_cache.pop(str(user_id), None)


# ── 管理员检查 ───────────────────────────────────

def _private_self_title(chat_id: str) -> str | None:
    """私聊「虚拟群」的显示标题；非私聊返回 None。

    私聊会话里 chat_id 等于 user_id（正数）；真实群组 chat_id 为负数，
    用 int(chat_id) > 0 即可可靠区分，无需依赖 user_id 传参。
    """
    try:
        if int(chat_id) > 0:
            return "💬 与我的私聊"
    except (TypeError, ValueError):
        pass
    return None


async def _is_user_group_admin(bot, user_id: int, chat_id: str) -> bool:
    # 私聊「虚拟群」：chat_id == user_id，用户管理自己的私聊设置永远合法。
    # 群组 chat_id 为负数，绝不会等于正数 user_id，因此不影响群权限判断。
    if str(chat_id) == str(user_id):
        return True
    try:
        admins = await bot.get_chat_administrators(int(chat_id))
        return any(adm.user.id == user_id for adm in admins)
    except Exception:
        return False


async def _get_admin_groups(bot, user_id: int) -> list[dict]:
    import time as _time
    uid = str(user_id)
    now = _time.time()
    if uid in _admin_cache:
        groups, ts = _admin_cache[uid]
        if now - ts < _ADMIN_CACHE_TTL:
            return _with_private_self(user_id, groups)
    groups = await _get_admin_groups_uncached(bot, user_id)
    _admin_cache[uid] = (groups, now)
    return _with_private_self(user_id, groups)


def _with_private_self(user_id: int, groups: list[dict]) -> list[dict]:
    """在群组列表最前面插入「与我的私聊」虚拟群（chat_id == user_id）。

    让用户可以像配置一个群那样，配置自己私聊会话使用的模型等设置。
    所有 per-chat 逻辑（get_effective_llm等）天然以 chat_id 索引，零额外成本复用。
    """
    private_entry = {
        "chat_id": str(user_id),
        "title": "💬 与我的私聊",
        "type": "private",
    }
    # 防御性去重：避免群列表里出现同 id（正常不会发生，群 id 为负）。
    rest = [g for g in groups if str(g.get("chat_id")) != str(user_id)]
    return [private_entry, *rest]


async def _get_admin_groups_uncached(bot, user_id: int) -> list[dict]:
    groups = []
    wl = list(_whitelist) if _whitelist else load_whitelist()
    for cid in wl:
        cid = str(cid).strip()
        if not cid:
            continue
        try:
            chat = await bot.get_chat(int(cid))
            if await _is_user_group_admin(bot, user_id, cid):
                groups.append({"chat_id": cid, "title": chat.title or f"群组 {cid}", "type": chat.type})
        except Exception:
            try:
                if await _is_user_group_admin(bot, user_id, cid):
                    groups.append({"chat_id": cid, "title": f"群组 {cid}", "type": "unknown"})
            except Exception:
                pass
    groups.sort(key=lambda g: g["title"])
    return groups


# ── UI 构建 ───────────────────────────────────────

def _build_groups_keyboard(groups: list[dict], page: int = 0) -> InlineKeyboardMarkup:
    page_size = 10
    total = len(groups)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    chunk = groups[page * page_size:(page + 1) * page_size]
    kb = []
    for g in chunk:
        label = g["title"] or f"群组 {g['chat_id']}"
        if len(label) > 36:
            label = label[:33] + "..."
        kb.append([InlineKeyboardButton(f"📋 {label}", callback_data=f"{CB_SETTINGS}:{g['chat_id']}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"{CB_GROUPS_PAGE}:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"{CB_GROUPS_PAGE}:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🔄 刷新", callback_data=CB_GROUPS),
               InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    return InlineKeyboardMarkup(kb)


def _setting_status(settings: dict, key: str) -> str:
    val = settings.get(key, "default")
    if key in BOOLEAN_SETTING_KEYS:
        if key == "free_reply_mode":
            return "✅ 开启（可选消息）" if val != "false" else "❌ 关闭（回复当前）"
        return "✅ 开启" if val != "false" else "❌ 关闭"
    if key == "reply_preference":
        return "🎯 提到机器人优先" if str(val).lower() == "mention_first" else "🧠 LLM偏好优先"
    if key == "message_drop_probability":
        try:
            p = max(0.0, min(1.0, float(str(val or "0"))))
        except ValueError:
            p = 0.0
        return f"🎲 {p:g}"
    if key == "username_anonymization_enabled":
        return "✅ 开启" if str(val).lower() != "false" else "❌ 关闭"
    if val == "default" or not val:
        return "🟢 默认"
    return "🔵 自定义"


def _category_summary(settings: dict, category_id: str) -> str:
    cat = SETTING_CATEGORIES[category_id]
    parts = []
    for key in cat["keys"]:
        label = _labels().get(key, key)
        parts.append(f"{label}:{_setting_status(settings, key)}")
    return "；".join(parts)


def _build_settings_text(chat_id: str, chat_title: str) -> str:
    settings = get_group_settings(chat_id)
    attention_mode = get_group_attention_mode(chat_id)
    attention_label = _attention_label(attention_mode)
    lines = [
        "🐱 **群组管理面板**",
        "",
        f"📋 群组：**{chat_title}**",
        f"🆔 ID：`{chat_id}`",
        "",
        "━━━ 设置分类 ━━━",
        f"• **注意力模式**：🧠 {attention_label}",
    ]
    for cid in ("persona", "chat", "image", "search", "activity", "reply", "privacy"):
        lines.append(f"• **{SETTING_CATEGORIES[cid]['title']}**：{_category_summary(settings, cid)}")
    lines.append("")
    lines.append("点击下方分类进入独立设置页；如需恢复出厂状态，可使用底部一键重置。")
    return "\n".join(lines)


def _build_settings_keyboard(chat_id: str) -> InlineKeyboardMarkup:
    attention_mode = get_group_attention_mode(chat_id)
    attention_label = _attention_label(attention_mode)
    kb = [
        [InlineKeyboardButton(f"🧠 注意力模式：{attention_label}", callback_data=f"{CB_ATTENTION}:{chat_id}")],
        [InlineKeyboardButton("🎭 人设设置", callback_data=f"{CB_CATEGORY}:{chat_id}:persona"),
         InlineKeyboardButton("🤖 对话模型", callback_data=f"{CB_CATEGORY}:{chat_id}:chat")],
        [InlineKeyboardButton("🎨 生图设置", callback_data=f"{CB_CATEGORY}:{chat_id}:image"),
         InlineKeyboardButton("🔍 搜索设置", callback_data=f"{CB_CATEGORY}:{chat_id}:search")],
        [InlineKeyboardButton("⏰ 群活跃设置", callback_data=f"{CB_CATEGORY}:{chat_id}:activity"),
         InlineKeyboardButton("💬 回复策略", callback_data=f"{CB_CATEGORY}:{chat_id}:reply")],
        [InlineKeyboardButton("🔒 隐私显示", callback_data=f"{CB_CATEGORY}:{chat_id}:privacy"),
         InlineKeyboardButton("🛠 工具开关", callback_data=f"{CB_TOOL_PANEL}:{chat_id}")],
        [InlineKeyboardButton("🧩 Skill 市场", callback_data=f"{CB_CATEGORY}:{chat_id}:skillmarket")],
        [InlineKeyboardButton("🧹 重置本群全部设置", callback_data=f"{CB_RESET_ALL}:{chat_id}")],
        [InlineKeyboardButton("🔙 返回群列表", callback_data=CB_GROUPS),
         InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)],
    ]
    return InlineKeyboardMarkup(kb)


def _build_category_text(chat_id: str, chat_title: str, category_id: str) -> str:
    cat = SETTING_CATEGORIES.get(category_id)
    if not cat:
        return "❌ 未知设置分类"
    settings = get_group_settings(chat_id)
    lines = [
        f"{cat['title']}",
        "",
        f"📋 群组：**{chat_title}**",
        f"🆔 ID：`{chat_id}`",
        "",
        f"📝 {cat['desc']}",
        "",
        "━━━ 当前状态 ━━━",
    ]
    for key in cat["keys"]:
        label = _labels().get(key, key)
        val = settings.get(key, "default")
        if key in BOOLEAN_SETTING_KEYS:
            if key == "free_reply_mode":
                display = "✅ 开启：可选择一条或多条历史消息回复" if val != "false" else "❌ 关闭：默认回复当前触发消息"
            else:
                display = "✅ 开启" if val != "false" else "❌ 关闭"
        elif key == "message_drop_probability":
            try:
                p = max(0.0, min(1.0, float(str(val or "0"))))
            except ValueError:
                p = 0.0
            display = f"🎲 `{p:g}`"
        elif key == "username_anonymization_enabled":
            display = "✅ 开启：群成员名会被转成脱敏标签" if val != "false" else "❌ 关闭：直接显示 Telegram 昵称"
        else:
            display = "🟢 使用默认" if val == "default" or not val else f"🔵 {mask_sensitive(val, SETTING_IS_SENSITIVE.get(key, False))}"
        lines.append(f"• **{label}**：{display}")
    lines.append("")
    lines.append("点击下方具体项目进行修改。")
    return "\n".join(lines)


def _build_category_keyboard(chat_id: str, category_id: str) -> InlineKeyboardMarkup:
    cat = SETTING_CATEGORIES.get(category_id)
    if not cat:
        return InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回群组设置", callback_data=f"{CB_SETTINGS}:{chat_id}")]])
    settings = get_group_settings(chat_id)
    kb = []
    for key in cat["keys"]:
        label = _labels().get(key, key)
        current = settings.get(key, "default")
        if key in BOOLEAN_SETTING_KEYS:
            icon = "✅" if current != "false" else "❌"
        elif key == "message_drop_probability":
            icon = "🎲"
        elif key == "username_anonymization_enabled":
            icon = "🔒" if current != "false" else "👁️"
        else:
            icon = "🟢" if current == "default" or not current else "🔵"
        kb.append([InlineKeyboardButton(f"{icon} {label}", callback_data=f"{CB_EDIT}:{chat_id}:{key}")])
    kb.append([InlineKeyboardButton("🔙 返回群组设置", callback_data=f"{CB_SETTINGS}:{chat_id}"),
               InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    return InlineKeyboardMarkup(kb)


def _build_attention_text(chat_id: str, chat_title: str) -> str:
    mode = get_group_attention_mode(chat_id)
    current = _attention_label(mode)
    return (
        "🧠 **注意力模式**\n\n"
        f"📋 群组：**{chat_title}**\n"
        f"当前模式：**{current}**\n\n"
        "━━━ 模式说明 ━━━\n\n"
        "**单消息注意力**\n"
        + get_text("admin_panel.attention_single_desc", "咪姆酱会只针对当前这一条消息，判断它是否值得主动回应。")
        + "适合希望她偶尔参与、不要太频繁插话的群聊。"
    )


def _build_attention_keyboard(chat_id: str) -> InlineKeyboardMarkup:
    mode = get_group_attention_mode(chat_id)
    kb = []
    kb.append([InlineKeyboardButton(f"✅ 当前：{_attention_label(mode)}", callback_data="noop")])
    if mode != ATTENTION_MODE_SINGLE:
        kb.append([InlineKeyboardButton("🧠 切换到单消息注意力", callback_data=f"{CB_ATTENTION_SET}:{chat_id}:{ATTENTION_MODE_SINGLE}")])
    kb.append([InlineKeyboardButton("🔙 返回群组设置", callback_data=f"{CB_SETTINGS}:{chat_id}"),
               InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    return InlineKeyboardMarkup(kb)


def _build_edit_keyboard(chat_id: str, key: str) -> InlineKeyboardMarkup:
    settings = get_group_settings(chat_id)
    current = settings.get(key, "default")
    is_default = current == "default" or not current
    is_sens = SETTING_IS_SENSITIVE.get(key, False)

    kb = []
    # 模型锁定检查
    dep_key = MODEL_DEPENDS_ON.get(key)
    model_locked = False
    if dep_key:
        dv = settings.get(dep_key, "default")
        if dv == "default" or not dv:
            model_locked = True

    if model_locked:
        dl = _labels().get(dep_key, dep_key)
        kb.append([InlineKeyboardButton(f"🔒 需先设置「{dl}」才能改模型", callback_data="noop")])
    elif key == "message_drop_probability":
        try:
            p = max(0.0, min(1.0, float(str(current or "0"))))
        except ValueError:
            p = 0.0
        kb.append([InlineKeyboardButton(f"🎲 当前：{p:g}", callback_data="noop")])
    elif key in BOOLEAN_SETTING_KEYS or key == "username_anonymization_enabled":
        on = (current != "false")
        kb.append([InlineKeyboardButton(f"{'✅ 当前：已开启' if on else '❌ 当前：已关闭'}", callback_data="noop")])
    elif key == "reply_preference":
        pref = get_group_reply_preference(chat_id)
        current_label = "🎯 提到机器人优先" if pref == "mention_first" else "🧠 LLM偏好优先"
        kb.append([InlineKeyboardButton(f"{current_label}", callback_data="noop")])
    elif is_default:
        kb.append([InlineKeyboardButton("🟢 当前：使用默认接口", callback_data="noop")])
    else:
        kb.append([InlineKeyboardButton(f"🔵 当前：{mask_sensitive(current, is_sens)}", callback_data="noop")])

    if key in BOOLEAN_SETTING_KEYS or key == "username_anonymization_enabled":
        current_on = (current != "false")
        if key == "free_reply_mode":
            label = "关闭自由回复（回复当前消息）" if current_on else "开启自由回复（让 LLM 选消息）"
        elif key == "username_anonymization_enabled":
            label = "关闭用户名脱敏" if current_on else "开启用户名脱敏"
        else:
            label = "🟢 关闭" if current_on else "🟢 开启"
        kb.append([InlineKeyboardButton(label, callback_data=f"{CB_TOGGLE}:{chat_id}:{key}:{'off' if current_on else 'on'}")])
    elif key == "reply_preference":
        pref = get_group_reply_preference(chat_id)
        if pref != "llm_first":
            kb.append([InlineKeyboardButton("🧠 切换为 LLM偏好优先", callback_data=f"{CB_TOGGLE}:{chat_id}:{key}:llm_first")])
        if pref != "mention_first":
            kb.append([InlineKeyboardButton("🎯 切换为 提到机器人优先", callback_data=f"{CB_TOGGLE}:{chat_id}:{key}:mention_first")])
    else:
        reset_label = "🔄 恢复默认值" if key == "message_drop_probability" else "🔄 切换为默认接口"
        kb.append([InlineKeyboardButton(reset_label, callback_data=f"{CB_RESET}:{chat_id}:{key}")])
        if not model_locked:
            custom_label = "✏️ 设置概率" if key == "message_drop_probability" else "✏️ 自定义设置"
            kb.append([InlineKeyboardButton(custom_label, callback_data=f"{CB_TOGGLE}:{chat_id}:{key}:custom")])
    kb.append([InlineKeyboardButton("🔙 返回设置", callback_data=f"{CB_SETTINGS}:{chat_id}"),
               InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    return InlineKeyboardMarkup(kb)


# ── 命令入口 ─────────────────────────────────────

async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """私聊 /settings —— 仅显示 Business Chatbot 设置面板。"""
    if not update.effective_user or not update.effective_message:
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("🔒 请在**私聊**中使用 `/settings` 打开设置面板。",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    user_id = update.effective_user.id
    user_name = update.effective_user.full_name or update.effective_user.first_name or str(user_id)
    biz_text = _build_biz_panel_text(str(user_id), user_name)
    biz_kb = _build_biz_keyboard(str(user_id))
    await update.message.reply_text(
        _mdv2(biz_text),
        reply_markup=biz_kb,
        parse_mode=ParseMode.MARKDOWN_V2,
    )

async def cmd_gadmin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return
    if update.effective_chat.type != "private":
        await update.message.reply_text("🔒 群组管理面板请在私聊中使用。\n\n直接私聊我发送 `/gadmin` 即可。",
                                        parse_mode=ParseMode.MARKDOWN)
        return
    user_id = update.effective_user.id
    loading = await update.message.reply_text("🔍 正在检查你的群组管理员权限...")

    # ── 加载群组管理 ──
    try:
        groups = await _get_admin_groups(context.bot, user_id)
    except Exception as e:
        logger.exception(f"获取管理员群组失败: {e}")
        await loading.edit_text("❌ 获取群组列表失败，请稍后重试。")
        return
    if not groups:
        await loading.edit_text(_mdv2("📭 **你当前没有管理的群组。**\n\n请确保：\n1. 你是群组的管理员\n2. 群组已在白名单中\n3. Bot 是群组成员"),
                                parse_mode=ParseMode.MARKDOWN_V2)
        return
    await loading.edit_text(_mdv2(f"✅ **设置面板已显示。**\n\n🐱 **群组管理面板**\n找到 **{len(groups)}** 个你管理的群组：\n\n选择一个群组来调整设置👇"),
                            parse_mode=ParseMode.MARKDOWN_V2, reply_markup=_build_groups_keyboard(groups, 0))


# ── 回调分发 ─────────────────────────────────────

async def gadmin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    user_id = query.from_user.id
    data = query.data
    try:
        # ── Business 设置回调 ──
        if data == CB_BIZ:
            await _cb_biz_refresh(query, context, user_id)
        elif data == CB_BIZ_MODE:
            await _cb_biz_toggle_mode(query, context, user_id)
        elif data.startswith(CB_BIZ_CATEGORY + ":"):
            await _cb_biz_show_category(query, context, user_id, data.split(":", 3)[3])
        elif data.startswith(CB_BIZ_TOGGLE + ":"):
            await _cb_biz_toggle_feature(query, context, user_id, data.split(":", 3)[3])

        elif data.startswith(CB_BIZ_EDIT + ":"):
            await _cb_biz_edit(query, context, user_id, data.split(":", 3)[3])
        elif data.startswith(CB_BIZ_RESET + ":"):
            await _cb_biz_reset(query, context, user_id, data.split(":", 3)[3])
        # ── 群组设置回调 ──
        elif data == CB_GROUPS:
            await _cb_show_groups(query, context, user_id, 0)
        elif data.startswith(CB_GROUPS_PAGE + ":"):
            await _cb_show_groups(query, context, user_id, int(data.split(":", 2)[2]))
        elif data == CB_CLOSE:
            await _cb_close(query)
        elif data.startswith(CB_SETTINGS + ":"):
            await _cb_show_settings(query, context, user_id, data.split(":", 2)[2])
        elif data.startswith(CB_RESET_ALL_CONFIRM + ":"):
            await _cb_reset_all_confirm(query, context, user_id, data.split(":", 2)[2])
        elif data.startswith(CB_RESET_ALL + ":"):
            await _cb_reset_all_prompt(query, context, user_id, data.split(":", 2)[2])
        elif data.startswith(CB_CATEGORY + ":"):
            parts = data.split(":", 3)
            if len(parts) >= 4:
                await _cb_show_category(query, context, user_id, parts[2], parts[3])
        elif data.startswith(CB_ATTENTION + ":"):
            await _cb_show_attention(query, context, user_id, data.split(":", 2)[2])
        elif data.startswith(CB_ATTENTION_SET + ":"):
            parts = data.split(":", 3)
            if len(parts) >= 4:
                await _cb_set_attention_mode(query, context, user_id, parts[2], parts[3])
        elif data.startswith(CB_SKILL_PAGE + ":"):
            parts = data.split(":")
            if len(parts) >= 5:
                await _cb_skill_market(query, context, user_id, parts[3], int(parts[4]))
        elif data.startswith(CB_SKILL_SEARCH + ":"):
            parts = data.split(":")
            if len(parts) >= 4:
                await _cb_skill_search(query, context, user_id, parts[3])
        elif data.startswith(CB_SKILL_CLEAR + ":"):
            parts = data.split(":")
            if len(parts) >= 4:
                await _cb_skill_clear_search(query, context, user_id, parts[3])
        elif data.startswith(CB_SKILL_TOGGLE + ":"):
            parts = data.split(":")
            if len(parts) >= 6:
                await _cb_skill_toggle(query, context, user_id, parts[3], parts[4], int(parts[5]))
        elif data.startswith(CB_SKILL_SECRET + "del:"):
            parts = data.split(":")
            if len(parts) >= 5:
                await _cb_skill_secret_delete(query, context, user_id, parts[3], parts[4])
        elif data.startswith(CB_SKILL_SECRET + ":"):
            parts = data.split(":")
            if len(parts) >= 5:
                await _cb_skill_secret(query, context, user_id, parts[3], parts[4])
        elif data.startswith(CB_TOOL_PANEL + ":"):
            await _cb_tool_panel(query, context, user_id, data.split(":", 2)[2])
        elif data.startswith(CB_TOOL_TOGGLE + ":"):
            parts = data.split(":", 3)
            if len(parts) >= 4:
                await _cb_tool_toggle(query, context, user_id, parts[2], parts[3])
        elif data.startswith(CB_EDIT + ":"):
            parts = data.split(":", 3)
            await _cb_show_edit(query, context, user_id, parts[2], parts[3])
        elif data.startswith(CB_RESET + ":"):
            parts = data.split(":", 3)
            await _cb_reset_setting(query, context, user_id, parts[2], parts[3])
        elif data.startswith(CB_TOGGLE + ":"):
            parts = data.split(":", 4)
            if len(parts) >= 5:
                await _cb_toggle_setting(query, context, user_id, parts[2], parts[3], parts[4])
        elif data == "noop":
            pass
    except Exception as e:
        logger.exception(f"群组管理面板回调错误: {e}")
        try:
            await query.edit_message_text(f"❌ 操作失败: {e}")
        except TelegramError:
            pass


async def _verify_admin(query, context, user_id: int, chat_id: str) -> bool:
    if str(user_id) in ADMIN_IDS:
        return True
    if not await _is_user_group_admin(context.bot, user_id, chat_id):
        await query.answer("❌ 你已不是该群管理员", show_alert=True)
        return False
    return True


async def _cb_show_groups(query, context, user_id: int, page: int = 0):
    groups = await _get_admin_groups(context.bot, user_id)
    if not groups:
        await _safe_edit(query, "📭 未找到你管理的群组。", parse_mode=ParseMode.MARKDOWN)
        return
    await _safe_edit(query, _mdv2(f"🐱 **群组管理面板**\n\n找到 **{len(groups)}** 个你管理的群组：\n\n选择一个群组来调整设置👇"),
                     parse_mode=ParseMode.MARKDOWN_V2, reply_markup=_build_groups_keyboard(groups, page))


async def _cb_show_settings(query, context, user_id: int, chat_id: str):
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    try:
        chat = await context.bot.get_chat(int(chat_id))
        title = _private_self_title(chat_id) or chat.title or f"群组 {chat_id}"
    except Exception:
        title = f"群组 {chat_id}"
    await _safe_edit(query, _mdv2(_build_settings_text(chat_id, title)),
                     parse_mode=ParseMode.MARKDOWN_V2, reply_markup=_build_settings_keyboard(chat_id))


async def _cb_reset_all_prompt(query, context, user_id: int, chat_id: str):
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    try:
        chat = await context.bot.get_chat(int(chat_id))
        title = _private_self_title(chat_id) or chat.title or f"群组 {chat_id}"
    except Exception:
        title = f"群组 {chat_id}"
    text = (
        "⚠️ **确认重置本群全部设置？**\n\n"
        f"📋 群组：**{title}**\n"
        f"🆔 ID：`{chat_id}`\n\n"
        "这会恢复本群所有机器人设置为默认值，包括：\n"
        "• 群自定义人设、对话/生图/搜索接口配置\n"
        "• 早安/晚安/冷群活跃开关\n"
        "• 注意力模式、自由回复、消息丢弃概率\n"
        "• 主动回复相关状态：聚焦状态、评分标准、安静/屏蔽主动插话设置\n\n"
        "白名单和聊天历史不会被删除。"
    )
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 确认重置", callback_data=f"{CB_RESET_ALL_CONFIRM}:{chat_id}")],
        [InlineKeyboardButton("🔙 返回群组设置", callback_data=f"{CB_SETTINGS}:{chat_id}"),
         InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)],
    ])
    await _safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def _cb_reset_all_confirm(query, context, user_id: int, chat_id: str):
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    reset_group_settings(chat_id)
    try:
        from stores.focus_store import get_focus_store
        get_focus_store().reset_chat(chat_id)
    except Exception:
        logger.exception("重置群主动回复状态失败")
    await query.answer("✅ 本群全部设置已恢复默认", show_alert=False)
    await _cb_show_settings(query, context, user_id, chat_id)


async def _cb_show_category(query, context, user_id: int, chat_id: str, category_id: str):
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    # Skill 市场分类单独处理
    if category_id == "skillmarket":
        await _cb_skill_market(query, context, user_id, chat_id, 0)
        return
    if category_id not in SETTING_CATEGORIES:
        await query.answer("未知设置分类", show_alert=True)
        return
    try:
        chat = await context.bot.get_chat(int(chat_id))
        title = _private_self_title(chat_id) or chat.title or f"群组 {chat_id}"
    except Exception:
        title = f"群组 {chat_id}"
    await _safe_edit(query, _mdv2(_build_category_text(chat_id, title, category_id)),
                     parse_mode=ParseMode.MARKDOWN_V2, reply_markup=_build_category_keyboard(chat_id, category_id))


async def _cb_show_attention(query, context, user_id: int, chat_id: str):
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    try:
        chat = await context.bot.get_chat(int(chat_id))
        title = _private_self_title(chat_id) or chat.title or f"群组 {chat_id}"
    except Exception:
        title = f"群组 {chat_id}"
    await _safe_edit(query, _mdv2(_build_attention_text(chat_id, title)),
                     parse_mode=ParseMode.MARKDOWN_V2, reply_markup=_build_attention_keyboard(chat_id))


async def _cb_set_attention_mode(query, context, user_id: int, chat_id: str, mode: str):
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    if mode != ATTENTION_MODE_SINGLE:
        await query.answer("该模式已下线，目前仅保留单消息注意力", show_alert=True)
        return
    set_group_attention_mode(chat_id, ATTENTION_MODE_SINGLE)
    label = _attention_label(ATTENTION_MODE_SINGLE)
    await query.answer(f"✅ 已切换为{label}")
    await _cb_show_attention(query, context, user_id, chat_id)


# ── Skill 市场管理 ──────────────────────────────

async def _cb_tool_panel(query, context, user_id: int, chat_id: str):
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    disabled = set(get_group_disabled_tools(chat_id))
    tools = []
    for definition in get_plugin_manager().tool_definitions(chat_id=None, limit=None):
        try:
            tools.append(definition["function"]["name"])
        except Exception:
            pass
    tools = sorted(set(tools) | disabled)
    lines = ["🛠 **工具开关**", "", f"群组：`{chat_id}`", ""]
    kb = []
    for name in tools:
        is_disabled = name in disabled
        lines.append(f"• {'❌' if is_disabled else '✅'} `{name}`")
        kb.append([InlineKeyboardButton(f"{'启用' if is_disabled else '禁用'} {name}", callback_data=f"{CB_TOOL_TOGGLE}:{chat_id}:{name}")])
    kb.append([InlineKeyboardButton("🔙 返回群组设置", callback_data=f"{CB_SETTINGS}:{chat_id}"), InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def _cb_tool_toggle(query, context, user_id: int, chat_id: str, tool_name: str):
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    disabled = set(get_group_disabled_tools(chat_id))
    if tool_name in disabled:
        remove_group_disabled_tool(chat_id, tool_name)
        await query.answer(f"✅ 已启用 {tool_name}", show_alert=False)
    else:
        add_group_disabled_tool(chat_id, tool_name)
        await query.answer(f"❌ 已禁用 {tool_name}", show_alert=False)
    await _cb_tool_panel(query, context, user_id, chat_id)


_SKILL_PAGE_SIZE = 5


async def _cb_skill_market(query, context, user_id: int, chat_id: str, page: int = 0):
    """Skill 市场主页面：已订阅 + 浏览/搜索列表"""
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    from integrations.skill_market_client import list_market_skills

    # keyword 存在 user_data 中，避免 callback_data 超长
    keyword = context.user_data.get(f"skill_search_{chat_id}", "")
    enabled_ids = get_enabled_skills(chat_id)
    skills, total = await list_market_skills(page=page, page_size=_SKILL_PAGE_SIZE, keyword=keyword)
    total_pages = max(1, (total + _SKILL_PAGE_SIZE - 1) // _SKILL_PAGE_SIZE)

    try:
        chat = await context.bot.get_chat(int(chat_id))
        title = _private_self_title(chat_id) or chat.title or f"群组 {chat_id}"
    except Exception:
        title = f"群组 {chat_id}"

    lines = [
        "🧩 **Skill 市场管理**",
        "",
        f"📋 群组：**{title}**",
        "",
    ]

    # 已订阅列表
    if enabled_ids:
        from integrations.skill_market_client import get_skills_summary
        subscribed = await get_skills_summary(enabled_ids)
        sub_names = {s["id"]: s for s in subscribed}
        lines.append("━━━ ✅ 已订阅 ━━━")
        for sid in enabled_ids:
            s = sub_names.get(sid)
            if s:
                lines.append(f"• **{s['name']}** — {(s.get('description') or '')[:40]}")
            else:
                lines.append(f"• ~~#{sid}~~（已失效）")
        lines.append("")

    # 浏览/搜索
    if keyword:
        lines.append(f"━━━ 🔍 搜索「{keyword}」（第 {page+1}/{total_pages} 页，共 {total} 个）━━━")
    else:
        lines.append(f"━━━ 🛒 浏览市场（第 {page+1}/{total_pages} 页，共 {total} 个）━━━")
    if skills:
        for s in skills:
            is_on = s["id"] in enabled_ids
            icon = "✅" if is_on else "⬜"
            desc = (s.get("description") or "")[:50]
            lines.append(f"{icon} **{s['name']}** — {desc}")
    else:
        lines.append("暂无 Skills" if not keyword else "未找到匹配的 Skills")
    lines.append("")

    # 构建键盘
    kb = []
    for s in skills:
        is_on = s["id"] in enabled_ids
        btn_text = f"{'✅ 退订' if is_on else '➕ 订阅'} {s['name']}"
        row = [InlineKeyboardButton(btn_text, callback_data=f"{CB_SKILL_TOGGLE}:{chat_id}:{s['id']}:{page}")]
        if is_on:
            has_secret = bool(get_skill_secret(chat_id, s["id"]))
            row.append(InlineKeyboardButton(
                "🔐 ✏️" if has_secret else "🔐",
                callback_data=f"{CB_SKILL_SECRET}:{chat_id}:{s['id']}",
            ))
        kb.append(row)

    # 翻页 + 搜索
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上页", callback_data=f"{CB_SKILL_PAGE}:{chat_id}:{page-1}"))
    nav.append(InlineKeyboardButton("🔍 搜索", callback_data=f"{CB_SKILL_SEARCH}:{chat_id}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️ 下页", callback_data=f"{CB_SKILL_PAGE}:{chat_id}:{page+1}"))
    kb.append(nav)

    if keyword:
        kb.append([InlineKeyboardButton("🔙 清除搜索", callback_data=f"{CB_SKILL_CLEAR}:{chat_id}")])

    kb.append([InlineKeyboardButton("🔙 返回群组设置", callback_data=f"{CB_SETTINGS}:{chat_id}"),
               InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])

    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN,
                     reply_markup=InlineKeyboardMarkup(kb))


async def _cb_skill_search(query, context, user_id: int, chat_id: str):
    """提示用户输入搜索关键词"""
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    context.user_data["gadmin_awaiting"] = True
    context.user_data["gadmin_chat_id"] = chat_id
    context.user_data["gadmin_key"] = "skill_search"
    prompt = await _safe_edit(query,
        "🔍 **搜索 Skill**\n\n请直接回复本条消息，输入搜索关键词：\n\n💡 回复 `取消` 放弃搜索",
        parse_mode=ParseMode.MARKDOWN)
    context.user_data["gadmin_prompt_message_id"] = getattr(prompt, "message_id", None) or getattr(getattr(query, "message", None), "message_id", None)


async def _cb_skill_clear_search(query, context, user_id: int, chat_id: str):
    """清除搜索关键词，回到浏览模式"""
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    context.user_data.pop(f"skill_search_{chat_id}", None)
    await _cb_skill_market(query, context, user_id, chat_id, 0)


async def _render_skill_market_to_msg(context, user_id: int, chat_id: str, page: int, message_id: int | None):
    """通过 bot.edit_message_text 渲染 Skill 市场页面（不依赖 query）"""
    from integrations.skill_market_client import (
    list_market_skills,
    get_skills_summary,
)
    user_data = context.user_data
    keyword = user_data.get(f"skill_search_{chat_id}", "")
    enabled_ids = get_enabled_skills(chat_id)
    skills, total = await list_market_skills(page=page, page_size=_SKILL_PAGE_SIZE, keyword=keyword)
    total_pages = max(1, (total + _SKILL_PAGE_SIZE - 1) // _SKILL_PAGE_SIZE)

    lines = ["🧩 **Skill 市场管理**", ""]
    if enabled_ids:
        subscribed = await get_skills_summary(enabled_ids)
        sub_names = {s["id"]: s for s in subscribed}
        lines.append("━━━ ✅ 已订阅 ━━━")
        for sid in enabled_ids:
            s = sub_names.get(sid)
            if s:
                lines.append(f"• **{s['name']}** — {(s.get('description') or '')[:40]}")
            else:
                lines.append(f"• ~~#{sid}~~（已失效）")
        lines.append("")

    if keyword:
        lines.append(f"━━━ 🔍 搜索「{keyword}」（第 {page+1}/{total_pages} 页，共 {total} 个）━━━")
    else:
        lines.append(f"━━━ 🛒 浏览市场（第 {page+1}/{total_pages} 页，共 {total} 个）━━━")
    if skills:
        for s in skills:
            is_on = s["id"] in enabled_ids
            icon = "✅" if is_on else "⬜"
            desc = (s.get("description") or "")[:50]
            lines.append(f"{icon} **{s['name']}** — {desc}")
    else:
        lines.append("暂无 Skills" if not keyword else "未找到匹配的 Skills")
    lines.append("")

    kb = []
    for s in skills:
        is_on = s["id"] in enabled_ids
        btn_text = f"{'✅ 退订' if is_on else '➕ 订阅'} {s['name']}"
        row = [InlineKeyboardButton(btn_text, callback_data=f"{CB_SKILL_TOGGLE}:{chat_id}:{s['id']}:{page}")]
        if is_on:
            has_secret = bool(get_skill_secret(chat_id, s["id"]))
            row.append(InlineKeyboardButton(
                "🔐 ✏️" if has_secret else "🔐",
                callback_data=f"{CB_SKILL_SECRET}:{chat_id}:{s['id']}",
            ))
        kb.append(row)
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上页", callback_data=f"{CB_SKILL_PAGE}:{chat_id}:{page-1}"))
    nav.append(InlineKeyboardButton("🔍 搜索", callback_data=f"{CB_SKILL_SEARCH}:{chat_id}"))
    if page < total_pages - 1:
        nav.append(InlineKeyboardButton("➡️ 下页", callback_data=f"{CB_SKILL_PAGE}:{chat_id}:{page+1}"))
    kb.append(nav)
    if keyword:
        kb.append([InlineKeyboardButton("🔙 清除搜索", callback_data=f"{CB_SKILL_CLEAR}:{chat_id}")])
    kb.append([InlineKeyboardButton("🔙 返回群组设置", callback_data=f"{CB_SETTINGS}:{chat_id}"),
               InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])

    text = "\n".join(lines)
    if message_id:
        try:
            await context.bot.edit_message_text(
                text=text, chat_id=user_id, message_id=message_id,
                parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb),
            )
        except TelegramError:
            pass


async def _cb_skill_toggle(query, context, user_id: int, chat_id: str, skill_id: str, page: int = 0):
    """订阅/退订"""
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    enabled_ids = get_enabled_skills(chat_id)
    if skill_id in enabled_ids:
        remove_enabled_skill(chat_id, skill_id)
        set_skill_secret(chat_id, skill_id, None)  # 同时清除私密信息
        await query.answer("✅ 已退订")
    else:
        add_enabled_skill(chat_id, skill_id)
        await query.answer("✅ 已订阅")
    await _cb_skill_market(query, context, user_id, chat_id, page)


async def _cb_skill_secret(query, context, user_id: int, chat_id: str, skill_id: str):
    """显示/编辑某个 Skill 的私密信息"""
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    from integrations.skill_market_client import get_skill_info
    info = await get_skill_info(skill_id)
    if not info:
        await query.answer("⚠️ Skill 不存在或已下架", show_alert=True)
        return
    skill_name = info["name"]
    current = get_skill_secret(chat_id, skill_id)
    lines = [
        f"🔐 **{skill_name}** — 私密信息",
        "",
        "这段文字只在本群调用此 Skill 时注入给 LLM，其他群和私聊看不到。",
        "",
    ]
    if current:
        lines.append(f"📋 当前内容（{len(current)} 字）：")
        lines.append(f"```\n{current[:200]}{'...' if len(current) > 200 else ''}\n```")
    else:
        lines.append("📋 当前未设置私密信息")
    lines.append("")
    lines.append("💡 回复本条消息输入新的私密信息，回复 `取消` 放弃，回复 `清空` 删除")

    kb = [
        [InlineKeyboardButton("🗑 清空", callback_data=f"{CB_SKILL_SECRET}del:{chat_id}:{skill_id}")],
        [InlineKeyboardButton("🔙 返回 Skill 市场", callback_data=f"{CB_CATEGORY}:{chat_id}:skillmarket")],
    ]
    prompt = await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN,
                              reply_markup=InlineKeyboardMarkup(kb))
    # 进入待输入状态
    context.user_data["gadmin_awaiting"] = True
    context.user_data["gadmin_chat_id"] = chat_id
    context.user_data["gadmin_key"] = f"skill_secret:{skill_id}"
    context.user_data["gadmin_prompt_message_id"] = getattr(prompt, "message_id", None) or getattr(getattr(query, "message", None), "message_id", None)


async def _cb_skill_secret_delete(query, context, user_id: int, chat_id: str, skill_id: str):
    """清空某个 Skill 的私密信息"""
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    set_skill_secret(chat_id, skill_id, None)
    await query.answer("🗑 已清空私密信息")
    await _cb_skill_secret(query, context, user_id, chat_id, skill_id)


async def _cb_show_edit(query, context, user_id: int, chat_id: str, key: str):
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    label = _labels().get(key, key)
    desc = _descriptions().get(key, "")
    current = get_group_settings(chat_id).get(key, "default")
    is_sens = SETTING_IS_SENSITIVE.get(key, False)
    is_default = current == "default" or not current
    lines = [f"⚙️ **{label}** 设置", "", f"📝 {desc}", ""]
    if key == "message_drop_probability":
        try:
            p = max(0.0, min(1.0, float(str(current or "0"))))
        except ValueError:
            p = 0.0
        lines.append(f"当前：🎲 `{p:g}`")
        lines.extend([
            "",
            "示例：",
            "• `0`：不随机丢弃普通消息",
            "• `0.2`：约 20% 普通消息会被跳过",
            "• `1`：所有普通消息都会被跳过",
            "",
            "明确 @、回复、直接叫到 Bot，以及管理员命令不受影响。",
        ])
    elif is_default:
        lines.append("当前：🟢 **使用默认接口**")
    else:
        lines.append(f"当前：🔵 `{mask_sensitive(current, is_sens)}`")
    lines.append(""); lines.append("点击下方按钮修改👇")
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN,
                     reply_markup=_build_edit_keyboard(chat_id, key))


async def _cb_reset_setting(query, context, user_id: int, chat_id: str, key: str):
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    reset_group_setting(chat_id, key)
    label = _labels().get(key, key)
    await query.answer(f"✅ {label} 已切换为默认接口", show_alert=False)
    await _cb_show_settings(query, context, user_id, chat_id)


async def _cb_toggle_setting(query, context, user_id: int, chat_id: str, key: str, val: str):
    if not await _verify_admin(query, context, user_id, chat_id):
        return
    label = _labels().get(key, key)
    if key in BOOLEAN_SETTING_KEYS or key == "username_anonymization_enabled":
        if key == "repeater_enabled":
            set_group_repeater_enabled(chat_id, val == "on")
        else:
            set_group_setting(chat_id, key, "false" if val == "off" else "true")
        await query.answer(f"✅ {label} 已{'关闭' if val == 'off' else '开启'}")
        await _cb_show_settings(query, context, user_id, chat_id)
        return
    if key == "reply_preference":
        set_group_reply_preference(chat_id, val)
        current = get_group_reply_preference(chat_id)
        human_label = "提到机器人优先" if current == "mention_first" else "LLM偏好优先"
        await query.answer(f"✅ {label} 已切换为{human_label}")
        await _cb_show_settings(query, context, user_id, chat_id)
        return
    if val == "custom":
        # 模型锁定检查
        dep_key = MODEL_DEPENDS_ON.get(key)
        if dep_key:
            dv = get_group_settings(chat_id).get(dep_key, "default")
            if dv == "default" or not dv:
                await query.answer(f"🔒 请先设置「{_labels().get(dep_key, dep_key)}」", show_alert=True)
                return
        context.user_data["gadmin_awaiting"] = True
        context.user_data["gadmin_chat_id"] = chat_id
        context.user_data["gadmin_key"] = key
        examples = {
            "persona_prompt": "例如：你是一个高冷傲娇的猫娘，不爱说话但很可靠...",
            "image_gen_api_key": "请输入 OpenAI 兼容图片服务 API Key",
            "image_gen_api_base": "例如：https://api.example.com/v1",
            "image_gen_model": "例如：gpt-image-1",
            "tavily_api_key": "请输入 Tavily API Key（tvly-...）",
            "llm_model": "例如：gpt-4o-mini / gpt-4o",
            "llm_provider": "openai_compatible / openai_responses / anthropic",
            "llm_api_key": "请输入 LLM API Key（sk-...）",
            "llm_api_base": "例如：https://api.openai.com/v1",
            "message_drop_probability": "请输入 0~1 的小数，例如：0、0.2、0.75。0=不丢弃，1=全部普通消息丢弃",
        }
        ex = _admin_examples().get(key, examples.get(key, ""))
        lines = [f"✏️ **设置 {label}**", "", f"请直接回复本条消息，输入新的 {label} 值："]
        if ex: lines.extend(["", f"💡 {ex}"])
        lines.extend(["", "💡 回复 `取消` 放弃修改", "💡 回复 `默认` 恢复使用默认接口"])
        await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def _cb_close(query):
    try:
        await query.edit_message_text("👋 面板已关闭")
    except TelegramError:
        await query.message.delete()


# ── Business 设置回调 ──────────────────────────

async def _cb_biz_refresh(query, context, user_id: int):
    uid = str(user_id)
    user = query.from_user
    user_name = user.full_name or user.first_name or str(user_id) if user else str(user_id)
    await _safe_edit(query, _mdv2(_build_biz_panel_text(uid, user_name)),
                     parse_mode=ParseMode.MARKDOWN_V2, reply_markup=_build_biz_keyboard(uid))


async def _cb_biz_show_category(query, context, user_id: int, category_id: str):
    if category_id not in BIZ_CATEGORIES:
        await query.answer("未知设置分类", show_alert=True)
        return
    uid = str(user_id)
    user = query.from_user
    user_name = user.full_name or user.first_name or str(user_id) if user else str(user_id)
    await _safe_edit(query, _mdv2(_build_biz_category_text(uid, user_name, category_id)),
                     parse_mode=ParseMode.MARKDOWN_V2, reply_markup=_build_biz_category_keyboard(uid, category_id))


async def _cb_biz_toggle_mode(query, context, user_id: int):
    uid = str(user_id)
    s = get_biz_settings(uid)
    new_mode = "synonym" if not s.is_synonym_mode() else "chat"
    set_biz_setting(uid, "mode", new_mode)
    await query.answer(f"已切换为{'已读乱回（含失语症）' if new_mode == 'synonym' else '经典对话'}模式")
    await _cb_biz_show_category(query, context, user_id, "mode")


async def _cb_biz_toggle_feature(query, context, user_id: int, key: str):
    allowed = {
        "sticker_enabled": "贴纸工具",
        "multi_message_enabled": "多句分条",
    }
    if key not in allowed:
        await query.answer("未知设置", show_alert=True)
        return
    uid = str(user_id)
    s = get_biz_settings(uid)
    current = {
        "sticker_enabled": s.is_sticker_enabled(),
        "multi_message_enabled": s.is_multi_message_enabled(),
    }[key]
    set_biz_setting(uid, key, "false" if current else "true")
    await query.answer(f"{allowed[key]}已{'关闭' if current else '开启'}")
    await _cb_biz_show_category(query, context, user_id, "behavior")

async def _cb_biz_edit(query, context, user_id: int, key: str):
    uid = str(user_id)
    s = get_biz_settings(uid)
    label_map = {"llm_provider": "API 协议", "llm_api_key": "API Key", "llm_api_base": "API Base", "llm_model": "模型", "persona": "人设"}
    label = label_map.get(key, key)
    if key == "llm_model" and not s.has_custom_llm():
        await query.answer("🔒 请先设置自定义 API Key 和 Base URL", show_alert=True)
        return
    context.user_data["gadmin_awaiting"] = True
    context.user_data["gadmin_chat_id"] = "biz"
    context.user_data["gadmin_key"] = key
    context.user_data["gadmin_user_id"] = uid
    examples = {"llm_provider": "openai_compatible / openai_responses / anthropic",
                "llm_api_key": "请输入 LLM API Key（sk-...）",
                "llm_api_base": "例如：https://api.openai.com/v1",
                "llm_model": "例如：gpt-4o-mini / gpt-4o",
                "persona": get_text("admin_panel.business_persona_example", "直接输入人设文本（最多5000字），回复 `默认` 恢复咪姆酱风格")}
    ex = _admin_examples().get(key, examples.get(key, ""))
    lines = [f"✏️ **设置 {label}**", "", f"请直接回复本条消息，输入新的 {label} 值："]
    if ex: lines.extend(["", f"💡 {ex}"])
    lines.extend(["", "💡 回复 `取消` 放弃修改", "💡 回复 `默认` 恢复使用全局默认"])
    prompt_message = await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    context.user_data["gadmin_prompt_message_id"] = getattr(prompt_message, "message_id", None) or getattr(getattr(query, "message", None), "message_id", None)

async def _cb_biz_reset(query, context, user_id: int, key: str):
    uid = str(user_id)
    reset_biz_setting(uid, key)
    label_map = {"llm_provider": "API 协议", "llm_api_key": "API Key", "llm_api_base": "API Base", "llm_model": "模型", "persona": "人设"}
    await query.answer(f"✅ {label_map.get(key, key)} 已恢复为全局默认")
    await _cb_biz_refresh(query, context, user_id)

# ── 文本输入处理 ────────────────────────────────

async def _handle_gadmin_pending_input(msg, context: ContextTypes.DEFAULT_TYPE) -> bool:
    if not getattr(msg, 'from_user', None):
        return False
    user_data = context.user_data
    if not user_data.get("gadmin_awaiting"):
        return False

    text = (getattr(msg, 'text', None) or "").strip()
    chat_id = user_data.get("gadmin_chat_id")
    key = user_data.get("gadmin_key")
    prompt_message_id = user_data.get("gadmin_prompt_message_id")
    reply_to = getattr(msg, "reply_to_message", None)
    reply_to_id = getattr(reply_to, "message_id", None)

    def _clear_pending_state() -> None:
        user_data["gadmin_awaiting"] = False
        user_data.pop("gadmin_chat_id", None)
        user_data.pop("gadmin_key", None)
        user_data.pop("gadmin_user_id", None)
        user_data.pop("gadmin_prompt_message_id", None)

    # Business / 面板待输入必须明确回复到面板提示消息，
    # 否则视为普通私聊，不消费，避免和正常聊天冲突。
    if prompt_message_id and reply_to_id != prompt_message_id:
        logger.debug(
            "忽略非面板回复的私聊文本，并清理失效待输入状态 | user=%s | expected_reply=%s | actual_reply=%s",
            getattr(msg.from_user, "id", "?"),
            prompt_message_id,
            reply_to_id,
        )
        _clear_pending_state()
        return False

    # ── Skill 搜索 ──
    if key == "skill_search":
        _clear_pending_state()
        if not text or text == "取消":
            await msg.reply_text("🚫 已取消搜索。", parse_mode=ParseMode.MARKDOWN)
            return True
        user_data[f"skill_search_{chat_id}"] = text
        logger.info(f"🔍 Skill 搜索 | chat={chat_id} | keyword={text}")
        # 构建搜索结果页面并编辑到面板消息上
        await _render_skill_market_to_msg(context, msg.from_user.id, chat_id, 0, prompt_message_id)
        return True

    # ── Skill 私密信息 ──
    if key and key.startswith("skill_secret:"):
        skill_id = key.split(":", 1)[1]
        _clear_pending_state()
        if not text or text == "取消":
            await msg.reply_text("🚫 已取消修改私密信息。", parse_mode=ParseMode.MARKDOWN)
            return True
        if text == "清空":
            set_skill_secret(chat_id, skill_id, None)
            await msg.reply_text("🗑 私密信息已清空。", parse_mode=ParseMode.MARKDOWN)
        else:
            set_skill_secret(chat_id, skill_id, text)
            logger.info(f"🔐 Skill 私密信息已更新 | chat={chat_id} | skill={skill_id} | len={len(text)}")
            await msg.reply_text(f"✅ 私密信息已保存（{len(text)} 字）。", parse_mode=ParseMode.MARKDOWN)
        return True

    # ── Business 模式：chat_id == "biz" ──
    if chat_id == "biz":
        uid = user_data.get("gadmin_user_id", str(msg.from_user.id))
        label_map = {"llm_provider": "API 协议", "llm_api_key": "API Key", "llm_api_base": "API Base", "llm_model": "模型", "persona": "人设"}
        label = label_map.get(key, key)
        logger.info(f"✍️ Business 输入到达 | user={uid} | key={key} | text_len={len(text)}")
        if not text or text == "取消":
            _clear_pending_state()
            await msg.reply_text(f"🚫 已取消修改 {label}。\n重新发送 `/gadmin` 可再次打开面板。", parse_mode=ParseMode.MARKDOWN)
            return True
        if text == "默认":
            reset_biz_setting(uid, key)
            _clear_pending_state()
            await msg.reply_text(f"✅ **{label}** 已恢复为全局默认。", parse_mode=ParseMode.MARKDOWN)
            return True
        if key == "llm_provider" and text.strip().lower() not in {"openai_compatible", "openai_responses", "anthropic"}:
            await msg.reply_text("❌ 协议必须是 openai_compatible、openai_responses 或 anthropic。")
            return True
        if key == "llm_model":
            s = get_biz_settings(uid)
            if not s.has_custom_llm():
                _clear_pending_state()
                await msg.reply_text("🔒 请先设置自定义 API Key 和 Base URL，再更改模型。", parse_mode=ParseMode.MARKDOWN)
                return True
        set_biz_setting(uid, key, text)
        _clear_pending_state()
        logger.info(f"💾 Business 设置已更新 | user={uid} | key={key}")
        await msg.reply_text(f"✅ **{label}** 已更新。\n\n重新发送 `/gadmin` 可查看面板。", parse_mode=ParseMode.MARKDOWN)
        return True

    # ── 群组模式：原有逻辑 ──
    label = _labels().get(key, key)
    if not text or text == "取消":
        _clear_pending_state()
        await msg.reply_text(f"🚫 已取消修改 {label}。\n重新发送 `/gadmin` 可再次打开面板。", parse_mode=ParseMode.MARKDOWN)
        return True
    if text == "默认":
        reset_group_setting(chat_id, key)
        _clear_pending_state()
        await msg.reply_text(f"✅ **{label}** 已恢复为默认接口。", parse_mode=ParseMode.MARKDOWN)
        return True
    if key == "llm_provider" and text.strip().lower() not in {"openai_compatible", "openai_responses", "anthropic"}:
        await msg.reply_text("❌ 协议必须是 openai_compatible、openai_responses 或 anthropic。")
        return True
    dep_key = MODEL_DEPENDS_ON.get(key)
    if dep_key:
        dv = get_group_settings(chat_id).get(dep_key, "default")
        if dv == "default" or not dv:
            _clear_pending_state()
            await msg.reply_text(f"🔒 **{label}** 无法自定义。\n请先设置「**{_labels().get(dep_key, dep_key)}**」为自定义值。",
                                 parse_mode=ParseMode.MARKDOWN)
            return True
    if key == "message_drop_probability":
        try:
            prob = float(text)
        except ValueError:
            await msg.reply_text("❌ 请输入 0~1 之间的小数，例如 `0`、`0.2`、`1`。", parse_mode=ParseMode.MARKDOWN)
            return True
        if prob < 0 or prob > 1:
            await msg.reply_text("❌ 概率必须在 0~1 之间，例如 `0.2` 表示约 20% 普通消息会被跳过。", parse_mode=ParseMode.MARKDOWN)
            return True
        text = f"{prob:g}"

    set_group_setting(chat_id, key, text)
    _clear_pending_state()
    logger.info(f"💾 群组设置已更新 | chat={chat_id} | key={key} | by={msg.from_user.id}")
    is_sens = SETTING_IS_SENSITIVE.get(key, False)
    display = text if key == "message_drop_probability" else mask_sensitive(text, is_sens)
    await msg.reply_text(f"✅ **{label}** 已更新！\n当前值：`{display}`\n\n重新发送 `/gadmin` 可查看所有设置。",
                         parse_mode=ParseMode.MARKDOWN)
    return True


async def gadmin_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    await _handle_gadmin_pending_input(update.message, context)


# ── 私聊待输入归属判定（供 private_text_router 调用）────────

def gadmin_has_pending(context: ContextTypes.DEFAULT_TYPE) -> bool:
    """当前用户是否处于 /gadmin 或 /settings 面板的待输入状态。"""
    user_data = context.user_data or {}
    return bool(user_data.get("gadmin_awaiting") and user_data.get("gadmin_prompt_message_id"))


async def try_handle_gadmin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """若命中 /gadmin、/settings 的待输入状态则处理并返回 True，否则返回 False。"""
    if not update.effective_user or not update.message:
        return False
    if not (context.user_data or {}).get("gadmin_awaiting"):
        return False
    return await _handle_gadmin_pending_input(update.message, context)


# ── 管理员权限变更监听 ─────────────────────────

async def on_chat_member_update(update: Update, context: ContextTypes.DEFAULT_TYPE):
    change = update.chat_member or update.my_chat_member
    if change and change.new_chat_member.user:
        invalidate_admin_cache(change.new_chat_member.user.id)


# ── 处理器注册 ───────────────────────────────────

def get_handlers():
    # 注意：私聊文本输入不再在这里注册独立 MessageHandler，
    # 统一由 handlers/private_text_router.py 的单一入口分发，避免各面板互相 break。
    return [
        CommandHandler("settings", cmd_settings),
        CommandHandler("gadmin", cmd_gadmin),
        CommandHandler("groupadmin", cmd_gadmin),
        CallbackQueryHandler(gadmin_callback, pattern=f"^{CB}"),
    ]


def get_chat_member_handlers():
    return [
        ChatMemberHandler(on_chat_member_update, ChatMemberHandler.CHAT_MEMBER),
        ChatMemberHandler(on_chat_member_update, ChatMemberHandler.MY_CHAT_MEMBER),
    ]
