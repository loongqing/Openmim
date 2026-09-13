from __future__ import annotations

from plugins.base import ToolContext, ToolSpec as ToolPlugin
from integrations.web_search import LIST_SKILLS_TOOL_DEFINITION, USE_SKILL_TOOL_DEFINITION


async def execute_list_skills(args: dict, ctx: ToolContext) -> str:
    from stores.group_settings_store import get_enabled_skills
    from integrations.skill_market_client import get_skills_summary

    skill_ids = get_enabled_skills(ctx.chat_id)
    if not skill_ids:
        return "本群未订阅任何 Skill。"
    summaries = await get_skills_summary(skill_ids)
    if not summaries:
        return "本群已订阅的 Skill 均已失效。"
    lines = ["本群已订阅的 Skills："]
    for s in summaries:
        desc = (s.get("description") or "")[:60]
        lines.append(f"• {s['name']} — {desc}" if desc else f"• {s['name']}")
    lines.append("")
    lines.append("使用 use_skill 工具可获取某个 Skill 的完整内容。")
    return "\n".join(lines)


async def execute_use_skill(args: dict, ctx: ToolContext) -> str:
    from stores.group_settings_store import get_enabled_skills, get_skill_secret
    from integrations.skill_market_client import get_skill_info

    runtime_config = ctx.runtime_config
    limit = getattr(runtime_config, "tool_result_max_chars", 6000)
    skill_name = args.get("skill_name", "").strip().lower()
    skill_ids = get_enabled_skills(ctx.chat_id)
    if not skill_ids:
        return "本群未订阅任何 Skill。"
    for sid in skill_ids:
        info = await get_skill_info(sid)
        if info and (info["name"].lower() == skill_name or str(info["id"]) == skill_name):
            content = info.get("content", "")[:limit]
            secret = get_skill_secret(ctx.chat_id, sid)
            if secret:
                content += f"\n\n---\n\n## 本群私密信息\n\n{secret}"
            return content
    return f"未找到已订阅的 Skill '{skill_name}'。请先在群管理面板中订阅。"


TOOLS = [
    ToolPlugin("list_skills", LIST_SKILLS_TOOL_DEFINITION, execute_list_skills, plugin="skills"),
    ToolPlugin("use_skill", USE_SKILL_TOOL_DEFINITION, execute_use_skill, plugin="skills"),
]
