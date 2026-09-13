"""
Tavily 网页搜索工具
提供给 LLM 的 function call 实现。
"""
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

import httpx

from app.runtime_config import get_shared_runtime_config
from app_config.config import (
    TAVILY_API_KEY,
    MAX_SEARCH_RESULTS,
)
from stores.focus_store import get_focus_store
from stores.memory_store import (
    add_memory,
    list_memories,
    delete_memory,
    update_memory,
    find_chat_memory,
)

logger = logging.getLogger(__name__)
_RUNTIME_CONFIG = get_shared_runtime_config()

TAVILY_API_URL = "https://api.tavily.com/search"
_CST = timezone(timedelta(hours=8), name="CST")


async def get_current_time() -> str:
    """返回当前 CST 时间，供模型按需获取，避免把时间写死进 system prompt。"""
    now = datetime.now(_CST)
    weekday_map = ["周一", "周二", "周三", "周四", "周五", "周六", "周日"]
    weekday_cn = weekday_map[now.weekday()]
    return (
        f"当前时间：{now.strftime('%Y-%m-%d %H:%M:%S CST')}\n"
        f"今天日期：{now.strftime('%Y-%m-%d')}\n"
        f"星期：{weekday_cn}\n"
        f"ISO时间：{now.isoformat()}"
    )


async def search_web(query: str, max_results: int = MAX_SEARCH_RESULTS, chat_id: int | None = None) -> str:
    """
    调用 Tavily API 搜索网页，返回格式化的搜索结果字符串。
    如果 API key 未配置，返回提示信息。
    支持 per-group Tavily key override。
    """
    # 优先使用统一配置层的群组/全局生效 key
    tavily_key = _RUNTIME_CONFIG.get_effective_tavily_api_key(chat_id)

    if not tavily_key:
        return "[搜索不可用：未配置 API Key]"

    try:
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(
                TAVILY_API_URL,
                json={
                    "api_key": tavily_key,
                    "query": query,
                    "max_results": max_results,
                    "search_depth": "basic",
                    "include_answer": True,
                },
            )
            resp.raise_for_status()
            data = resp.json()

            parts = []
            # 优先展示 answer
            answer = data.get("answer", "")
            if answer:
                parts.append(f"📌 摘要: {answer}")

            results = data.get("results", [])
            for i, r in enumerate(results[:max_results], 1):
                title = r.get("title", "无标题")
                url = r.get("url", "")
                content = r.get("content", "")
                # 截断过长的内容
                if len(content) > 300:
                    content = content[:300] + "..."
                parts.append(f"{i}. {title}\n   {content}\n   🔗 {url}")

            if not parts:
                return "未找到相关结果。"

            return "\n\n".join(parts)

    except httpx.HTTPError as e:
        logger.error(f"Tavily 搜索失败: {e}")
        return f"[搜索出错: {e}]"
    except Exception as e:
        logger.exception("Tavily 搜索异常")
        return f"[搜索异常: {e}]"


# ── URL 内容抓取 ────────────────────────────────

async def fetch_url_content(url: str) -> str:
    """抓取指定 URL 的网页内容（纯文本提取）"""
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": "Mozilla/5.0 (compatible; ChatBot/1.0)"},
            )
            resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                return f"[无法解析该链接的内容类型: {content_type}]"

            html = resp.text

            # 简单提取纯文本：去标签 + 去多余空白
            import re
            # 移除 script/style
            html = re.sub(r'<(script|style)[^>]*>.*?</\1>', '', html, flags=re.DOTALL | re.IGNORECASE)
            # 移除 HTML 标签
            text = re.sub(r'<[^>]+>', ' ', html)
            # 解码 HTML 实体
            text = text.replace('&amp;', '&').replace('&lt;', '<').replace('&gt;', '>')
            text = text.replace('&quot;', '"').replace('&#39;', "'").replace('&nbsp;', ' ')
            # 压缩空白
            text = re.sub(r'\s+', ' ', text).strip()

            if len(text) > 3000:
                text = text[:3000] + "...(内容已截断)"

            return text if text else "[页面无文本内容]"

    except httpx.HTTPError as e:
        return f"[抓取失败: {e}]"
    except Exception as e:
        logger.exception("URL 抓取异常")
        return f"[抓取异常: {e}]"


# ── 聚焦评分标准调整工具 ─────────────────────────

async def update_focus_criteria(criteria_note: str, chat_id: int | None = None) -> str:
    """LLM autonomously adjusts the focus-mode scoring criteria for the current chat.
    The criteria_note is stored per-chat and appended to the scoring prompt
    to influence when the bot should jump into conversations.
    """
    note = (criteria_note or "").strip()
    if not note:
        return "[未更新：评分标准为空]"
    try:
        store = get_focus_store()
        if chat_id is not None:
            current = store.get_criteria(chat_id)
            current["extra_note"] = note
            store.set_criteria(chat_id, current)
            logger.info(f"🧲 update_focus_criteria chat={chat_id}: {note[:80]}")
            return f"[已更新本群聚焦评分标准] {note[:200]}"
        else:
            logger.info(f"🧲 update_focus_criteria (global): {note[:80]}")
            return f"[已更新聚焦评分标准] {note[:200]}"
    except Exception as e:
        logger.exception("update_focus_criteria 失败")
        return f"[更新失败: {e}]"


async def suppress_focus_mode(enabled: bool = True, reason: str = "", chat_id: int | None = None) -> str:
    """Suppress or restore automatic focus-light participation for the current chat."""
    if chat_id is None:
        return "[设置失败：缺少 chat_id]"
    try:
        store = get_focus_store()
        store.set_suppressed(chat_id, bool(enabled), reason=reason)
        if enabled:
            logger.info(f"🔕 suppress_focus_mode enabled chat={chat_id}: {reason[:80]}")
            return "[已屏蔽本对话接下来的聚焦轻提示；仍会回应明确 @/回复/叫到机器人的消息]"
        logger.info(f"🔔 suppress_focus_mode disabled chat={chat_id}: {reason[:80]}")
        return "[已恢复本对话的聚焦轻提示]"
    except Exception as e:
        logger.exception("suppress_focus_mode 失败")
        return f"[设置失败: {e}]"


SUPPRESS_FOCUS_MODE_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "suppress_focus_mode",
        "description": "屏蔽或恢复当前对话接下来的聚焦轻提示/主动插话。用户要求安静、别插话、少说话、不要主动接话时，调用 enabled=true；用户要求恢复主动参与、继续插话、解除安静时，调用 enabled=false。只影响普通群消息触发的聚焦轻提示，不影响明确 @、回复或叫到你时的回应。",
        "parameters": {
            "type": "object",
            "properties": {
                "enabled": {
                    "type": "boolean",
                    "description": "true 表示屏蔽接下来的聚焦轻提示；false 表示恢复",
                    "default": True,
                },
                "reason": {
                    "type": "string",
                    "description": "简短说明为什么调整，例如：用户要求安静",
                },
            },
            "required": ["enabled"],
        },
    },
}


UPDATE_FOCUS_CRITERIA_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "update_focus_criteria",
        "description": "调整你在当前群聊中的发言参与策略。在以下情况调用：(1)群友要求你改变活跃度或多参与/少参与某类话题，(2)群友希望你主动回复某类消息（如'以后看到有人问技术问题主动回答''有人发图片时主动夸一下''有人情绪不好时主动安慰'），(3)你需要调整插话策略。criteria_note 用简洁自然语言描述调整方向。仅在群友明确要求或你认为需要调整策略时调用，普通聊天中不要使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "criteria_note": {
                    "type": "string",
                    "description": "发言策略调整说明，自然语言描述想要的变化方向",
                },
            },
            "required": ["criteria_note"],
        },
    },
}


# ── Tool Definitions ────────────────────────────
GET_CURRENT_TIME_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "get_current_time",
        "description": "获取当前北京时间（CST）和星期信息。当用户询问现在几点、今天几号、星期几、当前时间、日期相关判断时使用。不要在普通聊天中无故调用。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

SEARCH_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "search_web",
        "description": "搜索互联网获取最新信息。当需要查证事实、了解新闻事件、查询技术资料时使用。不要在普通闲聊中使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索关键词，用中文或英文",
                }
            },
            "required": ["query"],
        },
    },
}

FETCH_URL_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "fetch_url",
        "description": "抓取并阅读指定网页的文本内容。当你需要深入了解某个链接的具体内容时使用。通常在搜索后对感兴趣的链接使用。",
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": "要抓取的完整 URL",
                }
            },
            "required": ["url"],
        },
    },
}


from integrations.image_gen_tool import IMAGE_GEN_TOOL_DEFINITION


# ── 话题模式工具 ─────────────────────────────────

async def start_topic(topic_name: str, chat_id: int | None = None) -> str:
    """激活话题追踪模式。"""
    try:
        from handlers.topic_mode import activate_topic
        if chat_id:
            activate_topic(str(chat_id), topic_name)
            return f"✅ 已激活话题追踪，话题名：{topic_name}"
        return "❌ chat_id 缺失"
    except Exception as e:
        logger.exception(f"start_topic 失败")
        return f"[激活失败: {e}]"


async def stop_topic(chat_id: int | None = None) -> str:
    """关闭话题追踪模式。"""
    try:
        from handlers.topic_mode import deactivate_topic
        if chat_id:
            deactivate_topic(str(chat_id))
            return "✅ 已关闭话题追踪"
        return "❌ chat_id 缺失"
    except Exception as e:
        logger.exception(f"stop_topic 失败")
        return f"[关闭失败: {e}]"


START_TOPIC_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "start_topic",
        "description": "激活话题追踪模式。当群聊正在深入讨论一个有价值的主题，需要保持上下文连续性时使用。调用时需提供话题名称（10字以内）。",
        "parameters": {
            "type": "object",
            "properties": {
                "topic_name": {
                    "type": "string",
                    "description": "话题名称，简洁概括当前讨论的主题，10字以内",
                },
            },
            "required": ["topic_name"],
        },
    },
}

STOP_TOPIC_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "stop_topic",
        "description": "关闭话题追踪模式。当讨论明显偏离当前追踪话题、话题已结束、或者不再需要全量上下文时使用。",
        "parameters": {
            "type": "object",
            "properties": {},
            "required": [],
        },
    },
}

# ── 用户模糊查询工具 ──────────────────────────────

async def lookup_group_user(query: str, chat_id: int | None = None) -> str:
    """在当前群的已知用户索引里模糊查找 @username/显示名/脱敏标签。"""
    if chat_id is None:
        return "[用户查询失败：缺少 chat_id]"
    try:
        from stores.persona_memory import fuzzy_lookup_users
        rows = fuzzy_lookup_users(chat_id, query, limit=5)
        if not rows:
            return "未找到匹配的群成员。"
        parts = []
        for r in rows:
            label = r.get("anon_label") or f"用户_{str(r.get('user_id'))[-4:]}"
            username = ("@" + r.get("username")) if r.get("username") else "无 username"
            parts.append(f"- {label} | {username} | 匹配度 {r.get('score')} | 最近出现 {r.get('updated_at')}")
        return "\n".join(parts)
    except Exception as e:
        logger.exception("lookup_group_user 失败")
        return f"[用户查询异常: {e}]"


LOOKUP_GROUP_USER_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "lookup_group_user",
        "description": "当群友提到普通 @username、昵称或你不确定某个脱敏用户是谁时，在当前群的已知用户索引里模糊查找。只返回脱敏标签，不返回真实姓名。",
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "要查找的 username、昵称或用户标签，例如 @alice、alice、用户_ABCD",
                },
            },
            "required": ["query"],
        },
    },
}


def _should_reject_memory(text: str) -> bool:
    """Conservative filter for autonomous memory writes."""
    lowered = text.lower()
    bad_markers = (
        "http://", "https://", "t.me/", "telegram.me/", "加群", "推广", "广告", "返利", "邀请码",
        "赌博", "博彩", "现金网", "裸聊", "色情", "约炮", "仇恨", "歧视", "诈骗", "破解", "盗号",
    )
    return any(x in lowered for x in bad_markers)


async def remember_group_fact(content: str, key: str = "", scope: str = "chat", chat_id: int | None = None) -> str:
    """Save short chat/global memory learned by the LLM."""
    text = (content or "").strip()
    mem_key = (key or "").strip()
    mem_scope = (scope or "chat").strip().lower()
    if mem_scope not in ("chat", "global"):
        mem_scope = "chat"
    if mem_scope == "chat" and chat_id is None:
        return "[记忆保存失败：缺少 chat_id]"
    if not text:
        return "[记忆保存失败：内容为空]"
    if _should_reject_memory(text):
        return "[未保存：疑似广告或不良导向内容]"
    if len(mem_key) > 60:
        mem_key = mem_key[:60]
    max_len = 120 if mem_scope == "global" else 300
    if len(text) > max_len:
        text = text[:max_len]
    try:
        if mem_scope == "global":
            existing = next((r for r in list_memories(scope="global", include_inactive=False, limit=200)
                             if (mem_key and str(r.get("key") or "").strip().lower() == mem_key.lower())
                             or text.lower() in str(r.get("value") or "").lower()), None)
            if existing:
                return f"[全局记忆已存在] #{existing.get('id')} {existing.get('value')}"
            mid = add_memory(scope="global", value=text, key=mem_key, source="llm_evolution")
            logger.info(f"🧠 remember_group_fact(global-evolution) key={mem_key[:30]} content={text[:80]}")
            return f"[已记住全局表达偏好] #{mid} {text}"

        existing = find_chat_memory(chat_id=chat_id, key=mem_key, query=text)
        if existing:
            return f"[当前对话记忆已存在] #{existing.get('id')} {existing.get('value')}"
        mid = add_memory(scope="chat", value=text, key=mem_key, chat_id=str(chat_id), source="llm_tool")
        logger.info(f"🧠 remember_group_fact(chat-persistent) chat={chat_id} key={mem_key[:30]} content={text[:80]}")
        return f"[已记住当前对话事项] #{mid} {text}"
    except Exception as e:
        logger.exception("remember_group_fact 失败")
        return f"[记忆保存失败: {e}]"


REMEMBER_GROUP_FACT_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "remember_group_fact",
        "description": "主动保存值得长期复用的短记忆。scope=chat 用于当前群的约定、偏好、禁忌、项目背景；scope=global 用于你在聊天中偷偷学到的常用词、称呼习惯、偏好句式、可复用表达。用户明确要求记住时应调用；即使用户没要求，只要信息稳定、简短、后续有帮助，也可以主动调用。禁止保存广告导流、推广链接、违法违规、仇恨歧视、色情诈骗、引战或明显不良导向内容。不要保存流水账和过长内容。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {
                    "type": "string",
                    "description": "要记住的聊天事实或规则，简洁完整地改写成一句话",
                },
                "key": {
                    "type": "string",
                    "description": "可选的短键名，用于归类这条记忆，例如 language、project、禁忌、偏好、phrase、word",
                },
                "scope": {
                    "type": "string",
                    "enum": ["chat", "global"],
                    "description": "chat=当前群专属记忆；global=全局 evolution 记忆，用于偷偷学到的词语、称呼习惯、偏好句式。默认 chat。",
                }
            },
            "required": ["content"],
        },
    },
}


async def delete_group_fact(memory_id: int | None = None, key: str = "", query: str = "", chat_id: int | None = None) -> str:
    """删除当前 chat 的一条持久记忆。"""
    if chat_id is None:
        return "[记忆删除失败：缺少 chat_id]"
    target = find_chat_memory(chat_id=chat_id, memory_id=memory_id, key=key, query=query)
    if not target:
        return "[记忆删除失败：未找到匹配记忆]"
    ok = delete_memory(int(target["id"]))
    return f"[已删除当前对话记忆] #{target['id']} {target.get('value')}" if ok else "[记忆删除失败]"


DELETE_GROUP_FACT_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "delete_group_fact",
        "description": "当群友明确要求你忘记、删除、移除当前聊天里之前记住的某条事项时使用。优先按 memory_id 删除；如果没有 memory_id，可以按 key 或一句可识别的 query 查找并删除。只删除当前 chat 的持久记忆。",
        "parameters": {
            "type": "object",
            "properties": {
                "memory_id": {"type": "integer", "description": "要删除的记忆 ID，若已知则优先使用"},
                "key": {"type": "string", "description": "记忆键名，例如 language、project、禁忌、偏好"},
                "query": {"type": "string", "description": "记忆内容中的关键词，用于模糊定位，例如 中文、chatbot、剧透"}
            },
            "required": [],
        },
    },
}


async def update_group_fact(content: str, memory_id: int | None = None, key: str = "", query: str = "", chat_id: int | None = None) -> str:
    """覆盖更新当前 chat 的一条持久记忆。"""
    if chat_id is None:
        return "[记忆更新失败：缺少 chat_id]"
    text = (content or "").strip()
    if not text:
        return "[记忆更新失败：内容为空]"
    target = find_chat_memory(chat_id=chat_id, memory_id=memory_id, key=key, query=query)
    if not target:
        return "[记忆更新失败：未找到匹配记忆]"
    new_key = (key or str(target.get("key") or "")).strip()
    ok = update_memory(int(target["id"]), value=text[:300], key=new_key[:60])
    return f"[已更新当前对话记忆] #{target['id']} {text[:300]}" if ok else "[记忆更新失败]"


UPDATE_GROUP_FACT_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "update_group_fact",
        "description": "当群友明确要求把当前聊天里已经记住的某条事项改掉、覆盖、更新为新版本时使用。优先按 memory_id 更新；如果没有 memory_id，可以按 key 或 query 定位后覆盖。只更新当前 chat 的持久记忆。",
        "parameters": {
            "type": "object",
            "properties": {
                "content": {"type": "string", "description": "更新后的完整记忆内容"},
                "memory_id": {"type": "integer", "description": "要更新的记忆 ID，若已知则优先使用"},
                "key": {"type": "string", "description": "记忆键名，例如 language、project、禁忌、偏好"},
                "query": {"type": "string", "description": "旧记忆内容中的关键词，用于模糊定位"}
            },
            "required": ["content"],
        },
    },
}


READ_FILE_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "read_file",
        "description": (
            "读取聊天中用户发送的文件。从上下文消息中获取 file_id，传入此工具即可下载并读取文本内容。"
            "仅支持 10KB 以内的 UTF-8 文本文件。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_id": {
                    "type": "string",
                    "description": "Telegram file_id，从上下文中的 [文件 file_id=...] 标记获取",
                },
            },
            "required": ["file_id"],
        },
    },
}

LIST_SKILLS_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "list_skills",
        "description": "列出本群已订阅的所有 Skills（名称和简介）。Skill 是特殊能力指令，调用后会返回其完整内容。",
        "parameters": {
            "type": "object",
            "properties": {},
        },
    },
}

USE_SKILL_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "use_skill",
        "description": (
            "使用本群已订阅的 Skill。Skill 是一段 Markdown 格式的指令/知识，"
            "调用后会返回其完整内容，你需要按照 Skill 中的指示来回复用户。"
            "如果不确定 Skill 名称，先调用 list_skills 查看可用列表。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "skill_name": {
                    "type": "string",
                    "description": "Skill 名称（不区分大小写）",
                },
            },
            "required": ["skill_name"],
        },
    },
}
