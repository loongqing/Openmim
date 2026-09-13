"""
管理员控制面板（超级管理员专用）
将所有管理员命令浓缩为一个带按钮的交互面板。
支持白名单管理、模型切换、状态查看、群组设置概览。
"""
import logging
import json
import os
import re

from app_config.customization import get_text
import app_config.config as runtime_config
import subprocess
from typing import Optional

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes, CommandHandler, CallbackQueryHandler, MessageHandler, filters
from telegram.constants import ParseMode
from telegram.error import TelegramError

from app_config.config import (
    ADMIN_IDS,
    load_whitelist,
    save_whitelist,
    WHITELIST_FILE,
)
from stores.memory_store import (
    list_memories,
    add_memory,
    set_memory_active,
    delete_memory,
)
from plugins.manager import get_plugin_manager, reload_plugin_manager
from stores.group_settings_store import (
    get_group_settings,
    set_group_setting,
    reset_group_setting,
    SETTING_IS_SENSITIVE,
    DEFAULT_GROUP_SETTINGS,
    mask_sensitive,
)
from handlers.admin_panel_utils import (
    admin_examples as _admin_examples,
    labels as _labels,
    descriptions as _descriptions,
    mdv2 as _mdv2,
    safe_edit as _safe_edit,
)


logger = logging.getLogger(__name__)


_PENDING_STORE_KEY = "admin_pending_inputs"


def _admin_pending_store(context: ContextTypes.DEFAULT_TYPE) -> dict:
    return context.application.bot_data.setdefault(_PENDING_STORE_KEY, {})


def _get_admin_pending(context: ContextTypes.DEFAULT_TYPE, user_id: int | str | None) -> dict:
    if user_id is None:
        return {}
    return dict(_admin_pending_store(context).get(str(user_id), {}))


def _set_admin_pending(context: ContextTypes.DEFAULT_TYPE, user_id: int | str | None, data: dict) -> None:
    if user_id is None:
        return
    _admin_pending_store(context)[str(user_id)] = dict(data)


def _clear_admin_pending(context: ContextTypes.DEFAULT_TYPE, user_id: int | str | None) -> dict:
    if user_id is None:
        return {}
    return _admin_pending_store(context).pop(str(user_id), {})


# ── Callback 常量 ─────────────────────────────────

CB_PREFIX = "admin:"
CB_WHITELIST_LIST = f"{CB_PREFIX}wl_list"
CB_WHITELIST_ADD_HERE = f"{CB_PREFIX}wl_add_here"
CB_STATUS = f"{CB_PREFIX}status"
CB_SETTINGS = f"{CB_PREFIX}settings"
CB_SETTINGS_OVERVIEW = f"{CB_PREFIX}settings_overview"
CB_SETTINGS_OVERVIEW_PAGE = f"{CB_PREFIX}settings_overview_page"
CB_SETTINGS_EDIT = f"{CB_PREFIX}settings_edit"
CB_SETTINGS_RESET = f"{CB_PREFIX}settings_reset"
CB_SETTINGS_CUSTOM = f"{CB_PREFIX}settings_custom"
CB_SETTINGS_SET = f"{CB_PREFIX}settings_set"
CB_CLOSE = f"{CB_PREFIX}close"
CB_REFRESH = f"{CB_PREFIX}refresh"
CB_RESTART = f"{CB_PREFIX}restart"
CB_RESTART_CONFIRM = f"{CB_PREFIX}restart_confirm"
CB_PLUGIN_PANEL = f"{CB_PREFIX}plugin_panel"
CB_PLUGIN_TOGGLE = f"{CB_PREFIX}plugin_toggle"
CB_PLUGIN_RELOAD = f"{CB_PREFIX}plugin_reload"
CB_ACCESS_PANEL = f"{CB_PREFIX}access_panel"
CB_ACCESS_EDIT = f"{CB_PREFIX}access_edit"

CB_PERSONA = f"{CB_PREFIX}persona"
CB_PERSONA_CHAT = f"{CB_PREFIX}persona_chat"
CB_PERSONA_USER = f"{CB_PREFIX}persona_user"
CB_PERSONA_DEL = f"{CB_PREFIX}persona_del"
CB_PERSONA_DEL_CONFIRM = f"{CB_PREFIX}persona_del_confirm"
CB_PERSONA_OVERVIEW_PAGE = f"{CB_PREFIX}persona_page"
CB_PERSONA_CHAT_PAGE = f"{CB_PREFIX}persona_chat_page"
CB_MEM = f"{CB_PREFIX}mem"
CB_MEM_PAGE = f"{CB_PREFIX}mem_page"
CB_MEM_VIEW = f"{CB_PREFIX}mem_view"
CB_MEM_TOGGLE = f"{CB_PREFIX}mem_toggle"
CB_MEM_DEL = f"{CB_PREFIX}mem_del"
CB_MEM_ADD_GLOBAL = f"{CB_PREFIX}mem_add_global"
CB_MEM_ADD_CHAT = f"{CB_PREFIX}mem_add_chat"

_whitelist_ref: set[str] | None = None
_save_cb: Optional[callable] = None

ADMIN_GROUP_SETTING_KEYS = [
    "persona_prompt",
    "morning_greeting_enabled", "evening_greeting_enabled", "idle_topic_enabled",
    "free_reply_mode", "reply_preference", "attention_mode", "message_drop_probability",
    "llm_provider", "llm_model", "llm_api_key", "llm_api_base",
    "image_gen_api_key", "image_gen_api_base", "image_gen_model", "tavily_api_key",
]
MODEL_DEPENDS_ON = {"llm_model": "llm_api_key", "image_gen_model": "image_gen_api_key"}
ADMIN_BOOLEAN_SETTING_KEYS = {"morning_greeting_enabled", "evening_greeting_enabled", "idle_topic_enabled", "free_reply_mode"}


ACCESS_ALLOWLIST_KEYS: dict[str, str] = {
    "PRIVATE_ALLOWED_USER_IDS": "私聊主体对话",
    "GUEST_ALLOWED_USER_IDS": "Guest Mode",
    "BUSINESS_ALLOWED_USER_IDS": "Business Chatbot",
}


def _config_path_for_write() -> str:
    path = getattr(runtime_config, "LOCAL_CONFIG_PATH", "") or ""
    if not path:
        path = getattr(runtime_config, "LEGACY_LOCAL_CONFIG_PATH", "project_config.json")
    return path


def _load_project_config_for_write() -> dict:
    path = _config_path_for_write()
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        logger.exception("读取 project_config.json 失败")
        return {}


def _parse_user_ids_text(text: str) -> list[str]:
    raw = (text or "").strip()
    if not raw or raw in ("空", "全部", "所有人", "allow_all", "默认"):
        return []
    parts = re.split(r"[\s,，;；]+", raw)
    return sorted({p.strip() for p in parts if p.strip()})


def _set_allowed_user_ids(key: str, text: str) -> list[str]:
    if key not in ACCESS_ALLOWLIST_KEYS:
        raise ValueError(f"未知允许列表: {key}")
    ids = _parse_user_ids_text(text)
    path = _config_path_for_write()
    data = _load_project_config_for_write()
    data[key] = ids
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.write("\n")
    os.replace(tmp, path)
    setattr(runtime_config, key, set(ids))
    try:
        runtime_config._local_cfg[key] = ids
    except Exception:
        pass
    return ids


def _get_allowed_user_ids(key: str) -> list[str]:
    return sorted({str(x) for x in getattr(runtime_config, key, set()) if str(x).strip()})


def _format_allowed_user_ids(key: str) -> str:
    ids = _get_allowed_user_ids(key)
    return "全部用户（未限制）" if not ids else ", ".join(ids)


def _attention_label(mode: str) -> str:
    if mode == "all_message":
        return "全消息注意力"
    if mode == "mixed":
        return "混合模式"
    return "单消息注意力"


def _is_group_setting_custom(settings: dict, key: str) -> bool:
    val = str(settings.get(key, ""))
    default = str(DEFAULT_GROUP_SETTINGS.get(key, ""))
    return val != default


def inject(whitelist: set, save_cb: callable):
    global _whitelist_ref, _save_cb
    _whitelist_ref = whitelist
    _save_cb = save_cb


# ── 构建面板 ─────────────────────────────────────

def build_admin_panel() -> str:
    wl = list(_whitelist_ref) if _whitelist_ref else []
    wl_count = len(wl)
    custom_count = 0
    persona_user_count = 0
    persona_chat_count = 0
    try:
        from stores.persona_memory import list_persona_chats
        rows = list_persona_chats(limit=200)
        persona_chat_count = len(rows)
        persona_user_count = sum(int(r.get("user_count") or 0) for r in rows)
    except Exception:
        pass
    for cid in wl:
        s = get_group_settings(cid)
        if any(_is_group_setting_custom(s, k) for k in ADMIN_GROUP_SETTING_KEYS):
            custom_count += 1
    return "\n".join([
        get_text("admin_panel.title", "🐱 **咪姆酱超级管理面板**"), "",
        f"📋 白名单: {wl_count} 个群组",
        f"⚙️ 自定义设置: {custom_count} 个群",
        f"🧬 人格记忆: {persona_user_count} 位用户 / {persona_chat_count} 个聊天",
        "", "━━━ 操作按钮 ━━━",
    ])


def build_admin_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("📋 白名单管理", callback_data=CB_WHITELIST_LIST),
         InlineKeyboardButton("⚙️ 群组设置概览", callback_data=CB_SETTINGS_OVERVIEW)],
        [InlineKeyboardButton("🤖 Bot 状态", callback_data=CB_STATUS),
         InlineKeyboardButton("🔐 访问允许列表", callback_data=CB_ACCESS_PANEL)],
        [InlineKeyboardButton("🧬 人格记忆", callback_data=CB_PERSONA),
         InlineKeyboardButton("🧠 记忆管理", callback_data=CB_MEM)],
        [InlineKeyboardButton("🧩 插件管理", callback_data=CB_PLUGIN_PANEL),
         InlineKeyboardButton("♻️ 重启 Bot", callback_data=CB_RESTART)],
        [InlineKeyboardButton("🔄 刷新", callback_data=CB_REFRESH),
         InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)],
    ])


# ── 命令入口 ─────────────────────────────────────

async def cmd_admin(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.effective_message:
        return
    if str(update.effective_user.id) not in ADMIN_IDS:
        await update.message.reply_text("❌ 只有管理员才能使用此命令。")
        return
    await update.message.reply_text(
        _mdv2(build_admin_panel()), reply_markup=build_admin_keyboard(), parse_mode=ParseMode.MARKDOWN_V2)


# ── 回调分发 ─────────────────────────────────────

async def admin_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()
    user_id = query.from_user.id
    if str(user_id) not in ADMIN_IDS:
        await query.answer("❌ 只有管理员才能操作", show_alert=True)
        return
    data = query.data
    try:
        if data == CB_WHITELIST_ADD_HERE:
            await _cb_whitelist_add_here(query, context)
        elif data == CB_WHITELIST_LIST:
            await _cb_whitelist_list(query)
        elif data == CB_STATUS:
            await _cb_status(query)
        elif data == CB_RESTART:
            await _cb_restart(query)
        elif data == CB_RESTART_CONFIRM:
            await _cb_restart_confirm(query)
        elif data == CB_SETTINGS_OVERVIEW:
            await _cb_settings_overview(query, 0)
        elif data.startswith(CB_SETTINGS_OVERVIEW_PAGE + ":"):
            await _cb_settings_overview(query, int(data.split(":", 2)[2]))
        elif data == CB_PERSONA:
            await _cb_persona_overview(query)
        elif data == CB_MEM:
            await _cb_memory_overview(query, 0)
        elif data == CB_PLUGIN_PANEL:
            await _cb_plugin_panel(query)
        elif data == CB_ACCESS_PANEL:
            await _cb_access_panel(query)
        elif data == CB_PLUGIN_RELOAD:
            await _cb_plugin_reload(query, context.application)
        elif data.startswith(CB_PLUGIN_TOGGLE + ":"):
            await _cb_plugin_toggle(query, data.split(":", 2)[2])
        elif data.startswith(CB_ACCESS_EDIT + ":"):
            await _cb_access_edit(query, context, data.split(":", 2)[2])
        elif data == CB_MEM_ADD_GLOBAL:
            await _cb_memory_add_start(query, context, scope="global")
        elif data == CB_MEM_ADD_CHAT:
            await _cb_memory_add_start(query, context, scope="chat")
        elif data.startswith(CB_MEM_PAGE + ":"):
            await _cb_memory_overview(query, int(data.split(":", 2)[2]))
        elif data.startswith(CB_MEM_VIEW + ":"):
            _p = data.split(":", 3)
            await _cb_memory_view(query, int(_p[2]), int(_p[3]))
        elif data.startswith(CB_MEM_TOGGLE + ":"):
            _p = data.split(":", 3)
            await _cb_memory_toggle(query, int(_p[2]), int(_p[3]))
        elif data.startswith(CB_MEM_DEL + ":"):
            _p = data.split(":", 3)
            await _cb_memory_delete(query, int(_p[2]), int(_p[3]))
        elif data.startswith(CB_PERSONA_OVERVIEW_PAGE + ":"):
            await _cb_persona_overview(query, int(data.split(":", 2)[2]))
        elif data.startswith(CB_PERSONA_CHAT_PAGE + ":"):
            _p = data.split(":", 3)
            await _cb_persona_chat(query, _p[2], int(_p[3]))
        elif data.startswith(CB_PERSONA_CHAT + ":"):
            await _cb_persona_chat(query, data.split(":", 2)[2], 0)
        elif data.startswith(CB_PERSONA_USER + ":"):
            _p = data.split(":", 3)
            await _cb_persona_user(query, _p[2], _p[3])
        elif data.startswith(CB_PERSONA_DEL_CONFIRM + ":"):
            _p = data.split(":", 3)
            await _cb_persona_delete_execute(query, _p[2], _p[3])
        elif data.startswith(CB_PERSONA_DEL + ":"):
            _p = data.split(":", 3)
            await _cb_persona_delete(query, _p[2], _p[3])
        elif data.startswith(CB_SETTINGS_EDIT + ":"):
            parts = data.split(":", 3)
            if len(parts) == 4:
                await _cb_group_setting_edit(query, parts[2], parts[3])
        elif data.startswith(CB_SETTINGS_RESET + ":"):
            parts = data.split(":", 3)
            if len(parts) == 4:
                await _cb_group_setting_reset(query, parts[2], parts[3])
        elif data.startswith(CB_SETTINGS_CUSTOM + ":"):
            parts = data.split(":", 3)
            if len(parts) == 4:
                await _cb_group_setting_custom(query, context, parts[2], parts[3])
        elif data.startswith(CB_SETTINGS_SET + ":"):
            parts = data.split(":", 4)
            if len(parts) == 5:
                await _cb_group_setting_set(query, parts[2], parts[3], parts[4])
        elif data.startswith(CB_SETTINGS + ":"):
            await _cb_group_settings(query, data.split(":", 2)[2])
        elif data == CB_REFRESH:
            await _cb_refresh(query)
        elif data == CB_CLOSE:
            await _cb_close(query)
    except Exception as e:
        logger.exception(f"管理面板回调错误: {e}")
        try:
            await query.edit_message_text(f"❌ 操作失败: {e}")
        except TelegramError:
            pass


# ── 回调实现 ─────────────────────────────────────

async def _cb_refresh(query):
    await _safe_edit(
        query,
        _mdv2(build_admin_panel()),
        parse_mode=ParseMode.MARKDOWN_V2,
        reply_markup=build_admin_keyboard(),
    )

async def _cb_whitelist_add_here(query, context):
    chat = query.message.chat if query.message else None
    if not chat:
        await query.answer("无法获取当前群组信息", show_alert=True)
        return
    chat_id = str(chat.id)
    if _whitelist_ref is not None:
        if chat_id in _whitelist_ref:
            await query.answer("当前群组已在白名单中", show_alert=False)
            return
        _whitelist_ref.add(chat_id)
        if _save_cb:
            _save_cb()
    await _safe_edit(query, f"✅ 当前群组（`{chat_id}`）已加入白名单！\n当前共 {len(_whitelist_ref)} 个群组。",
                     parse_mode=ParseMode.MARKDOWN, reply_markup=build_admin_keyboard())


async def _cb_whitelist_list(query):
    if not _whitelist_ref:
        await _safe_edit(query, "📭 白名单为空。", reply_markup=build_admin_keyboard())
        return
    wl = sorted(_whitelist_ref)
    import asyncio as _asyncio
    names: dict[str, str] = {}
    async def _fn(cid):
        try:
            c = await _asyncio.wait_for((query.message.get_bot() if query.message else None).get_chat(int(cid)), timeout=5)
            names[cid] = c.title or f"群组 {cid}"
        except Exception:
            names[cid] = f"群组 {cid}"
    await _asyncio.gather(*[_fn(cid) for cid in wl])
    lines = [f"📋 **白名单** (共 {len(wl)} 个群组):", ""]
    for i, cid in enumerate(wl, 1):
        n = names.get(cid, cid).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`').replace('[', '\\[')
        lines.append(f"  {i}. {n}  (`{cid}`)")
    lines.append(""); lines.append("点击 👇 可移除对应群组")
    kb = []
    for cid in wl:
        n = names.get(cid, cid)
        if len(n) > 25: n = n[:22] + "..."
        kb.append([InlineKeyboardButton(f"❌ 移除 {n}", callback_data=f"{CB_PREFIX}wl_remove:{cid}")])
    kb.append([InlineKeyboardButton("🔙 返回", callback_data=CB_REFRESH),
               InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def _cb_access_panel(query):
    lines = [
        "🔐 **访问允许列表**",
        "",
        "控制哪些 Telegram user_id 可以使用私聊主体对话、Guest Mode、Business Chatbot。",
        "留空 = 允许所有人；如果要禁止所有普通用户，请设置为 `1`。",
        "",
    ]
    kb = []
    for key, label in ACCESS_ALLOWLIST_KEYS.items():
        lines.append(f"• **{label}**：`{_format_allowed_user_ids(key)}`")
        kb.append([InlineKeyboardButton(f"✏️ 设置 {label}", callback_data=f"{CB_ACCESS_EDIT}:{key}")])
    kb.append([InlineKeyboardButton("🔙 返回", callback_data=CB_REFRESH), InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def _cb_access_edit(query, context, key: str):
    if key not in ACCESS_ALLOWLIST_KEYS:
        await query.answer("未知允许列表", show_alert=True)
        return
    label = ACCESS_ALLOWLIST_KEYS[key]
    await query.edit_message_text(
        "\n".join([
            f"✏️ **设置 {label} 允许 user_id**",
            "",
            f"当前：`{_format_allowed_user_ids(key)}`",
            "",
            "请回复允许的 user_id 列表，用逗号/空格/换行分隔。",
            "留空或发送 `默认`/`所有人` = 允许所有人。",
            "若要禁止任何人使用，请设置为 `1`。",
            "回复 `取消` 放弃。",
        ]),
        parse_mode=ParseMode.MARKDOWN,
    )
    _set_admin_pending(context, query.from_user.id if query.from_user else None, {
        "kind": "access_allowlist",
        "key": key,
    })


async def admin_access_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if str(update.effective_user.id) not in ADMIN_IDS:
        return
    user_id = update.effective_user.id
    pending = _get_admin_pending(context, user_id)
    if pending.get("kind") != "access_allowlist":
        return
    key = pending.get("key", "")
    text = (update.message.text or "").strip()
    _clear_admin_pending(context, user_id)
    if key not in ACCESS_ALLOWLIST_KEYS:
        await update.message.reply_text("❌ 待修改的允许列表已失效，请重新打开 /admin。")
        return
    if text == "取消":
        await update.message.reply_text("🚫 已取消修改。")
        return
    ids = _set_allowed_user_ids(key, text)
    label = ACCESS_ALLOWLIST_KEYS[key]
    display = "全部用户（未限制）" if not ids else ", ".join(ids)
    await update.message.reply_text(
        f"✅ 已更新 **{label}** 允许列表。\n当前：`{display}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _cb_plugin_panel(query):
    manager = get_plugin_manager()
    rows = manager.plugin_statuses()
    lines = ["🧩 **插件管理**", "", f"共 {len(rows)} 个插件。", ""]
    kb = []
    for row in rows:
        name = str(row.get("name", ""))
        enabled = bool(row.get("enabled", True))
        tool_count = int(row.get("tool_count", 0) or 0)
        lines.append(f"• {'✅' if enabled else '❌'} **{name}** — tools={tool_count}")
        kb.append([InlineKeyboardButton(f"{'禁用' if enabled else '启用'} {name}", callback_data=f"{CB_PLUGIN_TOGGLE}:{name}")])
    kb.append([InlineKeyboardButton("♻️ 热重载", callback_data=CB_PLUGIN_RELOAD)])
    kb.append([InlineKeyboardButton("🔙 返回", callback_data=CB_REFRESH), InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def _cb_plugin_toggle(query, plugin_name: str):
    manager = get_plugin_manager()
    try:
        enabled = manager.toggle_plugin(plugin_name)
    except (RuntimeError, ValueError) as exc:
        await query.answer(str(exc), show_alert=True)
        return
    await query.answer(f"{'✅ 已启用' if enabled else '❌ 已禁用'} {plugin_name}", show_alert=False)
    await _cb_plugin_panel(query)


async def _cb_plugin_reload(query, application):
    try:
        await reload_plugin_manager(application)
    except Exception as exc:
        logger.exception("插件配置重载失败")
        await query.answer(f"插件重载失败：{exc}", show_alert=True)
        return
    await query.answer("♻️ 插件配置与生命周期已重载", show_alert=False)
    await _cb_plugin_panel(query)


async def _cb_settings_overview(query, page: int = 0):
    wl = list(_whitelist_ref) if _whitelist_ref else []
    if not wl:
        await _safe_edit(query, "📭 白名单为空。", reply_markup=build_admin_keyboard())
        return
    wl = sorted(wl)
    page_size = 10
    total = len(wl)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    chunk = wl[page * page_size:(page + 1) * page_size]
    bot = query.message.get_bot() if query.message else None
    kb = []
    for cid in chunk:
        s = get_group_settings(cid)
        hc = any(_is_group_setting_custom(s, k) for k in ADMIN_GROUP_SETTING_KEYS)
        title = await _get_chat_title(bot, cid)
        label = f"{'🔵' if hc else '⚪'} {title}"
        if len(label) > 46:
            label = label[:43] + "..."
        kb.append([InlineKeyboardButton(label, callback_data=f"{CB_SETTINGS}:{cid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"{CB_SETTINGS_OVERVIEW_PAGE}:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"{CB_SETTINGS_OVERVIEW_PAGE}:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 返回", callback_data=CB_REFRESH),
               InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    await _safe_edit(
        query,
        f"⚙️ **群组自定义设置概览**\n\n共 {total} 个群，第 {page + 1}/{pages} 页。",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=InlineKeyboardMarkup(kb),
    )


def _md_escape(text: str) -> str:
    return (text or "").replace("_", "\\_").replace("*", "\\*").replace("`", "\\`").replace("[", "\\[")


async def _get_chat_title(bot, chat_id: str) -> str:
    if bot is None:
        return f"群组 {chat_id}"
    try:
        chat = await bot.get_chat(int(chat_id))
        return chat.title or f"群组 {chat_id}"
    except Exception:
        return f"群组 {chat_id}"


async def _cb_group_settings(query, chat_id: str):
    s = get_group_settings(chat_id)
    title = await _get_chat_title(query.message.get_bot() if query.message else None, chat_id)
    lines = [f"⚙️ **群组设置**", "", f"📋 群组：**{_md_escape(title)}**", f"🆔 ID：`{chat_id}`", ""]
    for key in ADMIN_GROUP_SETTING_KEYS:
        label = _labels().get(key, key)
        val = s.get(key, "default")
        if key in ADMIN_BOOLEAN_SETTING_KEYS:
            status = "✅ 开启" if str(val).lower() != "false" else "❌ 关闭"
        elif key == "reply_preference":
            status = "🎯 提到机器人优先" if str(val).lower() == "mention_first" else "🧠 LLM偏好优先"
        elif key == "message_drop_probability":
            try:
                status = f"🎲 {max(0.0, min(1.0, float(str(val or '0')))):.0%}"
            except ValueError:
                status = f"🎲 {val}"
        elif key == "attention_mode":
            status = "🧠 " + _attention_label(val)
        else:
            status = "🟢 默认" if val == "default" or not val else f"🔵 {mask_sensitive(val, SETTING_IS_SENSITIVE.get(key, False))}"
        lines.append(f"• **{label}**: {status}")
    lines.append("")
    lines.append("点击下方项目可修改。")
    kb = []
    for i in range(0, len(ADMIN_GROUP_SETTING_KEYS), 2):
        row = []
        for key in ADMIN_GROUP_SETTING_KEYS[i:i+2]:
            val = s.get(key, "default")
            if key in ADMIN_BOOLEAN_SETTING_KEYS:
                icon = "✅" if str(val).lower() != "false" else "❌"
            elif key == "reply_preference":
                icon = "🎯" if str(val).lower() == "mention_first" else "🧠"
            elif key == "message_drop_probability":
                icon = "🎲"
            elif key == "attention_mode":
                icon = "🧠"
            else:
                icon = "🟢" if val == "default" or not val else "🔵"
            row.append(InlineKeyboardButton(f"{icon} {_labels().get(key, key)}", callback_data=f"{CB_SETTINGS_EDIT}:{chat_id}:{key}"))
        kb.append(row)
    kb.append([InlineKeyboardButton("🔙 返回概览", callback_data=CB_SETTINGS_OVERVIEW),
               InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def _cb_group_setting_edit(query, chat_id: str, key: str):
    if key not in ADMIN_GROUP_SETTING_KEYS:
        await query.answer("未知设置项", show_alert=True)
        return
    s = get_group_settings(chat_id)
    label = _labels().get(key, key)
    desc = _descriptions().get(key, "")
    val = s.get(key, "default")
    is_default = val == "default" or not val
    is_sens = SETTING_IS_SENSITIVE.get(key, False)
    dep_key = MODEL_DEPENDS_ON.get(key)
    locked = False
    if dep_key:
        dep_val = s.get(dep_key, "default")
        locked = dep_val == "default" or not dep_val

    lines = [f"✏️ **修改群组设置**", "", f"🆔 群组：`{chat_id}`", f"项目：**{label}**"]
    if desc:
        lines.append(f"说明：{desc}")
    lines.append("")
    if key in ADMIN_BOOLEAN_SETTING_KEYS:
        lines.append("当前：" + ("✅ 开启" if str(val).lower() != "false" else "❌ 关闭"))
    elif key == "reply_preference":
        pref_label = "🎯 提到机器人优先" if str(val).lower() == "mention_first" else "🧠 LLM偏好优先"
        lines.append(f"当前：{pref_label}")
        lines.extend([
            "",
            "模式说明：",
            "• LLM偏好优先：主要按模型综合判断消息是否值得主动参与。",
            get_text("admin_panel.reply_preference_mention_desc", "• 提到机器人优先：如果上下文明显在叫猫娘/猫猫/咪姆/机器人，或希望机器人回答问题，会优先提高回应分。"),
        ])
    elif key == "message_drop_probability":
        try:
            lines.append(f"当前：🎲 {max(0.0, min(1.0, float(str(val or '0')))):.0%}")
        except ValueError:
            lines.append(f"当前：🎲 `{val}`")
    elif key == "attention_mode":
        lines.append("当前：🧠 " + _attention_label(val))
        lines.extend([
            "",
            "模式说明：",
            "• 单消息注意力：只根据当前消息做主动回应评分。",
            "• 全消息注意力：读取最近上下文，由 LLM 判断是否回应，可 REFUSE。",
            "• 混合模式：先全消息判断；若全消息拒绝，再回退单消息评分。全消息已回应则不重复触发。",
        ])
    else:
        lines.append("当前：🟢 默认" if is_default else f"当前：🔵 `{mask_sensitive(val, is_sens)}`")
    if locked:
        lines.extend(["", f"🔒 该项需要先设置「{_labels().get(dep_key, dep_key)}」为自定义值。"] )

    kb = []
    if key in ADMIN_BOOLEAN_SETTING_KEYS:
        current_on = str(val).lower() != "false"
        kb.append([InlineKeyboardButton("❌ 关闭" if current_on else "✅ 开启", callback_data=f"{CB_SETTINGS_SET}:{chat_id}:{key}:{'false' if current_on else 'true'}")])
    elif key == "reply_preference":
        current_pref = str(val).lower() if val else "llm_first"
        kb.append([InlineKeyboardButton("🧠 LLM偏好优先", callback_data=f"{CB_SETTINGS_SET}:{chat_id}:{key}:llm_first")])
        kb.append([InlineKeyboardButton("🎯 提到机器人优先", callback_data=f"{CB_SETTINGS_SET}:{chat_id}:{key}:mention_first")])
        if current_pref not in ("llm_first", "mention_first"):
            lines.append("")
            lines.append("⚠️ 当前值异常，建议重新选择一次。")
    elif key == "attention_mode":
        kb.append([InlineKeyboardButton("🧠 单消息注意力", callback_data=f"{CB_SETTINGS_SET}:{chat_id}:{key}:single_message")])
        kb.append([InlineKeyboardButton("🌊 全消息注意力", callback_data=f"{CB_SETTINGS_SET}:{chat_id}:{key}:all_message")])
        kb.append([InlineKeyboardButton("🧪 混合模式", callback_data=f"{CB_SETTINGS_SET}:{chat_id}:{key}:mixed")])
    else:
        kb.append([InlineKeyboardButton("🔄 恢复默认", callback_data=f"{CB_SETTINGS_RESET}:{chat_id}:{key}")])
        if not locked:
            kb.append([InlineKeyboardButton("✏️ 输入自定义值", callback_data=f"{CB_SETTINGS_CUSTOM}:{chat_id}:{key}")])
    kb.append([InlineKeyboardButton("🔙 返回群设置", callback_data=f"{CB_SETTINGS}:{chat_id}"),
               InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def _cb_group_setting_set(query, chat_id: str, key: str, value: str):
    if key not in ADMIN_GROUP_SETTING_KEYS:
        await query.answer("未知设置项", show_alert=True)
        return
    if key in ADMIN_BOOLEAN_SETTING_KEYS:
        if value not in ("true", "false"):
            await query.answer("未知开关值", show_alert=True)
            return
        set_group_setting(chat_id, key, value)
    elif key == "reply_preference":
        if value not in ("llm_first", "mention_first"):
            await query.answer("未知回复偏好", show_alert=True)
            return
        set_group_setting(chat_id, key, value)
    elif key == "attention_mode":
        if value not in ("single_message", "all_message", "mixed"):
            await query.answer("未知注意力模式", show_alert=True)
            return
        set_group_setting(chat_id, key, value)
    else:
        await query.answer("该设置不能快捷切换", show_alert=True)
        return
    await query.answer(f"✅ {_labels().get(key, key)} 已更新")
    await _cb_group_settings(query, chat_id)


async def _cb_group_setting_reset(query, chat_id: str, key: str):
    if key not in ADMIN_GROUP_SETTING_KEYS:
        await query.answer("未知设置项", show_alert=True)
        return
    reset_group_setting(chat_id, key)
    await query.answer(f"✅ {_labels().get(key, key)} 已恢复默认")
    await _cb_group_settings(query, chat_id)


async def _cb_group_setting_custom(query, context, chat_id: str, key: str):
    if key not in ADMIN_GROUP_SETTING_KEYS:
        await query.answer("未知设置项", show_alert=True)
        return
    label = _labels().get(key, key)
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
        "message_drop_probability": "请输入 0~1 的小数，例如：0、0.2、0.75",
    }
    lines = [f"✏️ **超级管理员修改群设置**", "", f"群组：`{chat_id}`", f"项目：**{label}**", "", "请回复新的值。"]
    if examples.get(key):
        lines.extend(["", f"💡 {_admin_examples().get(key, examples[key])}"])
    lines.extend(["", "回复 `取消` 放弃。", "回复 `默认` 恢复默认。"] )
    await query.edit_message_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)
    _set_admin_pending(context, query.from_user.id if query.from_user else None, {
        "kind": "group_setting",
        "chat_id": chat_id,
        "key": key,
    })


async def admin_group_setting_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if str(update.effective_user.id) not in ADMIN_IDS:
        return
    user_id = update.effective_user.id
    pending = _get_admin_pending(context, user_id)
    if pending.get("kind") != "group_setting":
        return
    text = (update.message.text or "").strip()
    chat_id = pending.get("chat_id", "")
    key = pending.get("key", "")
    _clear_admin_pending(context, user_id)
    if not chat_id or key not in ADMIN_GROUP_SETTING_KEYS:
        await update.message.reply_text("❌ 待修改的群设置已失效，请重新打开 /admin。")
        return
    label = _labels().get(key, key)
    if not text or text == "取消":
        await update.message.reply_text("🚫 已取消修改。")
        return
    if text == "默认":
        reset_group_setting(chat_id, key)
        await update.message.reply_text(f"✅ 群组 `{chat_id}` 的 **{label}** 已恢复默认。", parse_mode=ParseMode.MARKDOWN)
        return
    if key in ADMIN_BOOLEAN_SETTING_KEYS or key == "attention_mode":
        await update.message.reply_text("这个设置请通过面板按钮切换，不需要手动输入。")
        return
    if key == "llm_provider" and text.strip().lower() not in {"openai_compatible", "openai_responses", "anthropic"}:
        await update.message.reply_text("❌ 协议必须是 openai_compatible、openai_responses 或 anthropic。")
        return
    if key == "message_drop_probability":
        try:
            prob = float(text)
        except ValueError:
            await update.message.reply_text("❌ 请输入 0~1 之间的小数，例如 `0.2`。", parse_mode=ParseMode.MARKDOWN)
            return
        if prob < 0 or prob > 1:
            await update.message.reply_text("❌ 概率必须在 0~1 之间。")
            return
        text = f"{prob:g}"
    dep_key = MODEL_DEPENDS_ON.get(key)
    if dep_key:
        dep_val = get_group_settings(chat_id).get(dep_key, "default")
        if dep_val == "default" or not dep_val:
            await update.message.reply_text(
                f"🔒 **{label}** 无法自定义。请先设置「**{_labels().get(dep_key, dep_key)}**」。",
                parse_mode=ParseMode.MARKDOWN,
            )
            return
    set_group_setting(chat_id, key, text)
    display = mask_sensitive(text, SETTING_IS_SENSITIVE.get(key, False))
    await update.message.reply_text(
        f"✅ 已更新群组 `{chat_id}` 的 **{label}**。\n当前值：`{display}`",
        parse_mode=ParseMode.MARKDOWN,
    )


async def _cb_persona_overview(query, page: int = 0):
    try:
        from stores.persona_memory import list_persona_chats
        rows = list_persona_chats(limit=200)
    except Exception:
        rows = []

    if not rows:
        await _safe_edit(query, "🧬 人格记忆概览\n\n📭 目前还没有人格记忆。", reply_markup=build_admin_keyboard())
        return

    page_size = 20
    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    chunk = rows[page * page_size:(page + 1) * page_size]

    lines = ["🧬 **人格记忆管理**", "", f"共 {total} 个聊天存在记忆。", f"第 {page + 1}/{pages} 页", "", "选择一个聊天查看用户记忆："]
    kb = []
    bot = query.message.get_bot() if query.message else None
    for r in chunk:
        cid = str(r.get("chat_id"))
        title = await _get_chat_title(bot, cid)
        count = int(r.get("user_count") or 0)
        label = f"🧬 {title}（{count}）"
        if len(label) > 45:
            label = label[:42] + "..."
        kb.append([InlineKeyboardButton(label, callback_data=f"{CB_PERSONA_CHAT_PAGE}:{cid}:{page}")])

    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"{CB_PERSONA_OVERVIEW_PAGE}:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"{CB_PERSONA_OVERVIEW_PAGE}:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 返回", callback_data=CB_REFRESH),
               InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def _cb_persona_chat(query, chat_id: str, page: int = 0):
    try:
        from stores.persona_memory import list_persona_users
        rows = list_persona_users(int(chat_id), limit=500)
    except Exception:
        rows = []
    title = await _get_chat_title(query.message.get_bot() if query.message else None, chat_id)
    total = len(rows)
    page_size = 15
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    chunk = rows[page * page_size:(page + 1) * page_size]

    lines = ["🧬 **群组人格记忆**", "", f"📋 群组：**{_md_escape(title)}**", f"🆔 ID：`{chat_id}`", f"第 {page + 1}/{pages} 页", ""]
    if not rows:
        lines.append("📭 这个群还没有可显示的人格记忆。")
    else:
        lines.append(f"共 {total} 位用户，点击用户查看详情：")
    kb = []
    for row in chunk:
        uid = str(row.get("user_id"))
        name = str(row.get("display_name") or row.get("anon_label") or uid)
        username = row.get("username") or ""
        persona = row.get("persona") or {}
        filled = sum(1 for k in ["style", "traits", "preferences", "boundaries", "memorable"] if persona.get(k))
        label = f"👤 {name}"
        if username:
            label += f" @{username}"
        label += f" · {filled}/5"
        if len(label) > 50:
            label = label[:47] + "..."
        kb.append([InlineKeyboardButton(label, callback_data=f"{CB_PERSONA_USER}:{chat_id}:{uid}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton("⬅️ 上一页", callback_data=f"{CB_PERSONA_CHAT_PAGE}:{chat_id}:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton("下一页 ➡️", callback_data=f"{CB_PERSONA_CHAT_PAGE}:{chat_id}:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton("🔙 返回聊天列表", callback_data=f"{CB_PERSONA_OVERVIEW_PAGE}:{page if page < pages else 0}"),
               InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)])
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


def _fmt_persona_list(title: str, items) -> list[str]:
    if not items:
        return []
    if isinstance(items, str):
        items = [items]
    out = [f"**{title}**"]
    for x in list(items)[:8]:
        out.append(f"• {_md_escape(str(x))}")
    return out


async def _cb_persona_user(query, chat_id: str, user_id: str):
    try:
        from stores.persona_memory import get_persona_row
        row = get_persona_row(int(chat_id), int(user_id))
    except Exception:
        row = None
    if not row:
        await _safe_edit(query, "📭 这条人格记忆不存在，可能已被删除。", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("🔙 返回", callback_data=f"{CB_PERSONA_CHAT}:{chat_id}")]]))
        return

    name = str(row.get("display_name") or row.get("anon_label") or user_id)
    username = row.get("username") or ""
    persona = row.get("persona") or {}
    updated = row.get("updated_at") or "未知"
    lines = [
        "👤 **人格记忆详情**", "",
        f"群组：`{chat_id}`",
        f"用户：**{_md_escape(name)}** (`{user_id}`)",
    ]
    if username:
        lines.append(f"用户名：@{_md_escape(str(username))}")
    lines.extend([f"更新时间：`{_md_escape(str(updated))}`", ""])

    style = persona.get("style") or ""
    if style:
        lines.extend(["**风格**", _md_escape(str(style)), ""])
    for title, key in [("特征", "traits"), ("偏好", "preferences"), ("边界/注意", "boundaries"), ("印象", "memorable")]:
        block = _fmt_persona_list(title, persona.get(key))
        if block:
            lines.extend(block + [""])
    if len(lines) <= 8:
        lines.append("📭 该用户暂无有效人格字段。")

    kb = [
        [InlineKeyboardButton("🗑 删除这条记忆", callback_data=f"{CB_PERSONA_DEL}:{chat_id}:{user_id}")],
        [InlineKeyboardButton("🔙 返回用户列表", callback_data=f"{CB_PERSONA_CHAT}:{chat_id}"),
         InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)],
    ]
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def _cb_persona_delete(query, chat_id: str, user_id: str):
    try:
        from stores.persona_memory import get_persona_row
        row = get_persona_row(int(chat_id), int(user_id))
    except Exception:
        row = None
    name = str((row or {}).get("display_name") or (row or {}).get("anon_label") or user_id)
    lines = [
        "⚠️ **确认删除人格记忆？**", "",
        f"群组：`{chat_id}`",
        f"用户：**{_md_escape(name)}** (`{user_id}`)", "",
        "删除后不会影响 known_users，只删除人格画像。",
    ]
    kb = [
        [InlineKeyboardButton("✅ 确认删除", callback_data=f"{CB_PERSONA_DEL_CONFIRM}:{chat_id}:{user_id}")],
        [InlineKeyboardButton("🔙 取消", callback_data=f"{CB_PERSONA_USER}:{chat_id}:{user_id}")],
    ]
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def _cb_persona_delete_execute(query, chat_id: str, user_id: str):
    try:
        from stores.persona_memory import delete_persona
        ok = delete_persona(int(chat_id), int(user_id))
    except Exception:
        ok = False
    await query.answer("已删除" if ok else "删除失败", show_alert=False)
    await _cb_persona_chat(query, chat_id, 0)


async def _cb_memory_overview(query, page: int = 0):
    rows = list_memories(include_inactive=True, limit=500)
    if not rows:
        await _safe_edit(query, "🧠 记忆管理\n\n📭 目前没有任何记忆。", reply_markup=build_admin_keyboard())
        return
    page_size = 15
    total = len(rows)
    pages = max(1, (total + page_size - 1) // page_size)
    page = max(0, min(page, pages - 1))
    chunk = rows[page * page_size:(page + 1) * page_size]
    kb = []
    lines = ["🧠 **记忆管理**", "", f"共 {total} 条记忆，第 {page + 1}/{pages} 页。", ""]
    for r in chunk:
        state = "🟢" if r.get('active') else "⚪"
        scope = r.get('scope') or 'global'
        key = r.get('key') or ''
        title = (r.get('value') or '')[:26].replace('\n', ' ')
        label = f"{state} [{scope}] {key or 'untagged'} · {title}"
        if len(label) > 50:
            label = label[:47] + '...'
        kb.append([InlineKeyboardButton(label, callback_data=f"{CB_MEM_VIEW}:{r['id']}:{page}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton('⬅️ 上一页', callback_data=f"{CB_MEM_PAGE}:{page - 1}"))
    if page < pages - 1:
        nav.append(InlineKeyboardButton('下一页 ➡️', callback_data=f"{CB_MEM_PAGE}:{page + 1}"))
    if nav:
        kb.append(nav)
    kb.append([InlineKeyboardButton('➕ 新增全局记忆', callback_data=CB_MEM_ADD_GLOBAL), InlineKeyboardButton('➕ 新增指定群记忆', callback_data=CB_MEM_ADD_CHAT)])
    kb.append([InlineKeyboardButton('🔙 返回', callback_data=CB_REFRESH), InlineKeyboardButton('❌ 关闭', callback_data=CB_CLOSE)])
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def _cb_memory_view(query, memory_id: int, page: int = 0):
    row = next((x for x in list_memories(include_inactive=True, limit=500) if int(x['id']) == int(memory_id)), None)
    if not row:
        await _safe_edit(query, '📭 记忆不存在，可能已删除。', reply_markup=build_admin_keyboard())
        return
    lines = [
        '🧠 **记忆详情**','',
        f"ID：`{row['id']}`",
        f"Scope：`{row.get('scope') or ''}`",
        f"Key：`{row.get('key') or ''}`",
        f"状态：{'🟢启用' if row.get('active') else '⚪停用'}",
        f"chat_id：`{row.get('chat_id') or ''}`",
        f"user_id：`{row.get('user_id') or ''}`",
        f"更新时间：`{row.get('updated_at') or ''}`",
        '',
        row.get('value') or ''
    ]
    kb = [
        [InlineKeyboardButton('🟢/⚪ 切换启用', callback_data=f"{CB_MEM_TOGGLE}:{row['id']}:{page}"),
         InlineKeyboardButton('🗑 删除', callback_data=f"{CB_MEM_DEL}:{row['id']}:{page}")],
        [InlineKeyboardButton('🔙 返回列表', callback_data=f"{CB_MEM_PAGE}:{page}"),
         InlineKeyboardButton('❌ 关闭', callback_data=CB_CLOSE)],
    ]
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=InlineKeyboardMarkup(kb))


async def _cb_memory_toggle(query, memory_id: int, page: int):
    row = next((x for x in list_memories(include_inactive=True, limit=500) if int(x['id']) == int(memory_id)), None)
    if not row:
        await query.answer('记忆不存在', show_alert=True)
        return
    set_memory_active(memory_id, not bool(row.get('active')))
    await _cb_memory_view(query, memory_id, page)


async def _cb_memory_delete(query, memory_id: int, page: int):
    delete_memory(memory_id)
    await query.answer('已删除', show_alert=False)
    await _cb_memory_overview(query, page)

async def _cb_memory_add_start(query, context, scope: str):
    user_id = query.from_user.id if query.from_user else None
    if scope == "global":
        prompt_message = await query.edit_message_text(
            "➕ **新增全局记忆**\n\n请直接回复这条消息，发送记忆内容。\n\n回复 `取消` 放弃。",
            parse_mode=ParseMode.MARKDOWN,
        )
        _set_admin_pending(context, user_id, {
            "kind": "memory_add",
            "stage": "content",
            "scope": "global",
            "chat_id": None,
            "prompt_message_id": getattr(prompt_message, "message_id", None) or getattr(getattr(query, "message", None), "message_id", None),
        })
        return
    await query.edit_message_text(
        "➕ **新增指定群记忆**\n\n请先发送目标群 ID，或发送群 ID 尾号（例如 7088）。\n\n回复 `取消` 放弃。",
        parse_mode=ParseMode.MARKDOWN,
    )
    _set_admin_pending(context, user_id, {
        "kind": "memory_add",
        "stage": "select_chat",
        "scope": "chat",
        "chat_id": None,
    })


async def admin_memory_text_input(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not update.effective_user or not update.message:
        return
    if str(update.effective_user.id) not in ADMIN_IDS:
        return
    user_id = update.effective_user.id
    text = (update.message.text or "").strip()
    pending = _get_admin_pending(context, user_id)
    if pending.get("kind") != "memory_add":
        return

    if not text or text == "取消":
        _clear_admin_pending(context, user_id)
        await update.message.reply_text("🚫 已取消新增记忆。")
        return

    if pending.get("stage") == "select_chat":
        wl = sorted(_whitelist_ref or load_whitelist())
        matches = [cid for cid in wl if str(cid) == text or str(cid).endswith(text)]
        if len(matches) != 1:
            await update.message.reply_text(f"❌ 无法唯一定位群。匹配结果：{', '.join(matches[:8]) or '无'}")
            return
        prompt_message = await update.message.reply_text(
            f"✅ 已选择群 `{matches[0]}`。\n现在请回复这条消息，发送要新增的记忆内容。\n回复 `取消` 放弃。",
            parse_mode=ParseMode.MARKDOWN,
        )
        _set_admin_pending(context, user_id, {
            "kind": "memory_add",
            "stage": "content",
            "scope": "chat",
            "chat_id": matches[0],
            "prompt_message_id": getattr(prompt_message, "message_id", None),
        })
        return

    scope = pending.get("scope", "global")
    chat_id = pending.get("chat_id")
    _clear_admin_pending(context, user_id)
    mid = add_memory(scope=scope, value=text, chat_id=chat_id, user_id=str(user_id), source="manual")
    if scope == "global":
        await update.message.reply_text(f"✅ 已新增全局记忆 `#{mid}`。", parse_mode=ParseMode.MARKDOWN)
    else:
        await update.message.reply_text(f"✅ 已新增群 `{chat_id}` 记忆 `#{mid}`。", parse_mode=ParseMode.MARKDOWN)


async def _cb_status(query):
    from app_config.config import (
    CONTEXT_MESSAGE_COUNT,
    IDLE_TOPIC_IDLE_HOURS,
    FOCUS_LIGHT_HINT_ENABLED,
    FOCUS_LIGHT_HINT_PROBABILITY,
)
    from llm.llm_client import get_active_llm_model
    from stores.focus_store import get_focus_store
    from stores.token_usage_store import get_usage_summary
    model = get_active_llm_model()
    wl = list(_whitelist_ref) if _whitelist_ref else []
    active_focus = sum(1 for cid in wl if get_focus_store().get(cid).active)
    usage = get_usage_summary(model)
    overall = usage.get("overall", {})
    today_all = usage.get("today_all", {})
    model_all = usage.get("model_all", {})
    lines = [
        "🤖 **Bot 状态**", "",
        f"• 白名单: {len(wl)} 个群组",
        f"• 当前模型: `{model}`",
        f"• 活跃聚焦: {active_focus} 个群",
        f"• 上下文: {CONTEXT_MESSAGE_COUNT} 条",
        f"• 空闲引题: {IDLE_TOPIC_IDLE_HOURS}h 间隔",
        f"• 聚焦轻提示: {'✅' if FOCUS_LIGHT_HINT_ENABLED else '❌'} (p={FOCUS_LIGHT_HINT_PROBABILITY*100:.0f}%)",
        f"• 管理员: {', '.join(sorted(ADMIN_IDS))}",
        "",
        "📊 **Token Usage**",
        f"• 总请求数: {int(overall.get('request_count', 0))}",
        f"• 总 Prompt Tokens: {int(overall.get('prompt_tokens', 0))}",
        f"• 总未缓存 Prompt: {int(overall.get('uncached_prompt_tokens', 0))}",
        f"• 总 Completion Tokens: {int(overall.get('completion_tokens', 0))}",
        f"• 总 Total Tokens: {int(overall.get('total_tokens', 0))}",
        f"• 总缓存 Tokens: {int(overall.get('cached_prompt_tokens', 0))}",
        f"• 总缓存率: {float(overall.get('cache_rate', 0.0))*100:.1f}%",
        f"• 今日 Total Tokens: {int(today_all.get('total_tokens', 0))}",
        f"• 当前模型累计 Total: {int(model_all.get('total_tokens', 0))}",
        f"• 当前模型未缓存 Prompt: {int(model_all.get('uncached_prompt_tokens', 0))}",
        f"• 当前模型缓存率: {float(model_all.get('cache_rate', 0.0))*100:.1f}%",
    ]
    await _safe_edit(query, "\n".join(lines), parse_mode=ParseMode.MARKDOWN, reply_markup=build_admin_keyboard())


async def _cb_restart(query):
    text = "\n".join([
        "⚠️ **确认重启 mimubot？**",
        "",
        "这会执行 `systemctl restart mimubot`。",
        "如果当前 bot 进程没有对应权限，操作会失败并返回错误信息。",
    ])
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ 确认重启", callback_data=CB_RESTART_CONFIRM)],
        [InlineKeyboardButton("🔙 返回", callback_data=CB_REFRESH),
         InlineKeyboardButton("❌ 关闭", callback_data=CB_CLOSE)],
    ])
    await _safe_edit(query, text, parse_mode=ParseMode.MARKDOWN, reply_markup=kb)


async def _cb_restart_confirm(query):
    await query.answer("♻️ 正在重启 mimubot...", show_alert=False)
    try:
        result = subprocess.run(
            ["systemctl", "restart", "mimubot"],
            capture_output=True,
            text=True,
            timeout=30,
            check=False,
        )
    except subprocess.TimeoutExpired:
        await _safe_edit(
            query,
            "⚠️ 重启命令超时，请到服务器上手动检查 `mimubot` 服务状态。",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_admin_keyboard(),
        )
        return
    except Exception as e:
        logger.exception("管理员面板重启 mimubot 失败")
        err_text = str(e)[:160].replace("`", "'")
        await _safe_edit(
            query,
            f"❌ 重启失败：`{err_text}`",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_admin_keyboard(),
        )
        return

    status = subprocess.run(
        ["systemctl", "is-active", "mimubot"],
        capture_output=True,
        text=True,
        timeout=10,
        check=False,
    )
    is_active = (status.stdout or "").strip() == "active"
    if result.returncode == 0 and is_active:
        await _safe_edit(
            query,
            "✅ mimubot 已重启。",
            parse_mode=ParseMode.MARKDOWN,
            reply_markup=build_admin_keyboard(),
        )
        return

    err = (result.stderr or result.stdout or status.stderr or status.stdout or "未知错误").strip()
    err = err.replace("`", "'")[:300]
    await _safe_edit(
        query,
        f"❌ mimubot 重启失败\n```\n{err}\n```",
        parse_mode=ParseMode.MARKDOWN,
        reply_markup=build_admin_keyboard(),
    )


async def _cb_close(query):
    try:
        await query.edit_message_text("🫡 面板已关闭")
    except TelegramError:
        await query.message.delete()


# ── 白名单移除回调 ───────────────────────────────

async def admin_whitelist_remove_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if not query or not query.data:
        return
    if str(query.from_user.id) not in ADMIN_IDS:
        await query.answer("❌ 只有管理员才能操作", show_alert=True)
        return
    data = query.data
    if data.startswith(f"{CB_PREFIX}wl_remove:"):
        chat_id = data.split(":", 2)[2]
        if _whitelist_ref and chat_id in _whitelist_ref:
            _whitelist_ref.discard(chat_id)
            if _save_cb:
                _save_cb()
            await query.answer(f"✅ 已移除 {chat_id}", show_alert=False)
        await _cb_whitelist_list(query)


# ── 私聊待输入归属判定（供 private_text_router 调用）────────
# 设计：路由器按优先级依次询问各面板「这条私聊文本是不是你在等的输入」。
# 只有当本面板对应的 awaiting 状态为真时才接管并返回 True，否则返回 False 放行。

def admin_has_pending(context: ContextTypes.DEFAULT_TYPE, user_id: int | str | None = None) -> bool:
    """当前用户是否处于某个 /admin 面板的待输入状态。"""
    return bool(_get_admin_pending(context, user_id))


async def try_handle_admin_pending(update: Update, context: ContextTypes.DEFAULT_TYPE) -> bool:
    """若命中 /admin 的待输入状态则处理并返回 True，否则返回 False。"""
    user_id = update.effective_user.id if update.effective_user else None
    pending = _get_admin_pending(context, user_id)
    kind = pending.get("kind")
    if kind == "memory_add":
        before = dict(pending)
        await admin_memory_text_input(update, context)
        after = _get_admin_pending(context, user_id)
        return bool(before) or before != after
    if kind == "group_setting":
        before = dict(pending)
        await admin_group_setting_text_input(update, context)
        after = _get_admin_pending(context, user_id)
        return bool(before) or before != after
    if kind == "access_allowlist":
        before = dict(pending)
        await admin_access_text_input(update, context)
        after = _get_admin_pending(context, user_id)
        return bool(before) or before != after
    return False


# ── 处理器注册 ───────────────────────────────────

def get_handlers():
    # 注意：私聊文本输入不再在这里注册独立 MessageHandler，
    # 统一由 handlers/private_text_router.py 的单一入口分发，避免各面板互相 break。
    return [
        CommandHandler("admin", cmd_admin),
        CommandHandler("panel", cmd_admin),
        CallbackQueryHandler(admin_callback, pattern=f"^{CB_PREFIX}(?!wl_remove:)"),
        CallbackQueryHandler(admin_whitelist_remove_callback, pattern=f"^{CB_PREFIX}wl_remove:"),
    ]
