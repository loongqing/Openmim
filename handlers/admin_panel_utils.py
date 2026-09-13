"""admin_panel 与 group_admin_panel 共享的工具函数。

这些函数此前在两个面板文件中逐字重复，现收敛到单一定义。
"""

from telegram.error import TelegramError, BadRequest
from telegram.helpers import escape_markdown

from app_config.customization import get_dict
from stores.group_settings_store import (
    get_setting_labels,
    get_setting_descriptions,
)


def admin_examples() -> dict[str, str]:
    return get_dict("admin_examples", {})


def labels() -> dict[str, str]:
    return get_setting_labels()


def descriptions() -> dict[str, str]:
    return get_setting_descriptions()


def mdv2(text: str) -> str:
    """Convert the panel's small legacy-Markdown subset (**bold**, `code`) to MarkdownV2."""
    s = str(text or "")
    out: list[str] = []
    i = 0
    while i < len(s):
        if s.startswith("**", i):
            j = s.find("**", i + 2)
            if j != -1:
                out.append("*" + escape_markdown(s[i + 2:j], version=2) + "*")
                i = j + 2
                continue
        if s[i] == "`":
            j = s.find("`", i + 1)
            if j != -1:
                out.append("`" + escape_markdown(s[i + 1:j], version=2, entity_type="code") + "`")
                i = j + 1
                continue
        out.append(escape_markdown(s[i], version=2))
        i += 1
    return "".join(out)


async def safe_edit(query, text=None, reply_markup=None, parse_mode=None):
    try:
        await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=parse_mode)
    except BadRequest as e:
        msg = str(e).lower()
        if "not modified" in msg:
            return
        # 群名/用户名/设置值可能含 Markdown 特殊字符；解析失败时降级纯文本，保证面板可用。
        if parse_mode is not None and "can't parse entities" in msg:
            try:
                await query.edit_message_text(text=text, reply_markup=reply_markup, parse_mode=None)
                return
            except TelegramError:
                pass
        raise
    except TelegramError:
        pass
