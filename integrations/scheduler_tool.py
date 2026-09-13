"""
LLM 计划任务工具

允许 LLM 创建定时/循环任务，到期后触发一次完整 LLM 对话。
任务不持久化——进程重启后全部丢失。

支持三种触发模式：
- delay: 延迟 N 分钟后触发
- at:    在指定 ISO 时间触发
- cron:  按 cron 表达式循环触发
"""
import asyncio
import contextlib
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

logger = logging.getLogger(__name__)

_CST = timezone(timedelta(hours=8), name="CST")
MAX_ACTIVE_TASKS = 20

_next_id = 1


@dataclass
class ScheduledTask:
    task_id: int
    chat_id: int
    message: str           # 触发时注入的提示
    trigger_type: str      # "delay" | "at" | "cron"
    cron_expr: str = ""    # cron 模式
    next_fire: datetime = field(default_factory=lambda: datetime.now(_CST))
    handle: asyncio.Task | None = None


_tasks: dict[int, ScheduledTask] = {}


def _consume_task_result(handle: asyncio.Task) -> None:
    """Retrieve failures from detached scheduler tasks."""
    if handle.cancelled():
        return
    try:
        handle.result()
    except Exception:
        logger.exception("Scheduled task loop exited unexpectedly")


async def _cancel_scheduled_task(task: ScheduledTask) -> None:
    handle = task.handle
    if handle is None or handle.done() or handle is asyncio.current_task():
        return
    handle.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await handle


def _parse_cron(expr: str) -> dict | None:
    """简单解析 5 段 cron 表达式，返回 {minute, hour, day, month, weekday} 集合。

    支持: * / N / N-M / N,M
    """
    parts = expr.strip().split()
    if len(parts) != 5:
        return None
    ranges = [
        ("minute", 0, 59),
        ("hour", 0, 23),
        ("day", 1, 31),
        ("month", 1, 12),
        ("weekday", 0, 6),
    ]
    result = {}
    for i, (name, lo, hi) in enumerate(ranges):
        val = parts[i].strip()
        if val == "*":
            result[name] = set(range(lo, hi + 1))
        elif "/" in val:
            base, step = val.split("/", 1)
            try:
                step = int(step)
                if base == "*":
                    start = lo
                else:
                    start = int(base)
                result[name] = set(range(start, hi + 1, step))
            except ValueError:
                return None
        elif "," in val:
            nums = set()
            for item in val.split(","):
                if "-" in item:
                    a, b = item.split("-", 1)
                    try:
                        nums.update(range(int(a), int(b) + 1))
                    except ValueError:
                        return None
                else:
                    try:
                        nums.add(int(item))
                    except ValueError:
                        return None
            result[name] = nums
        elif "-" in val:
            a, b = val.split("-", 1)
            try:
                result[name] = set(range(int(a), int(b) + 1))
            except ValueError:
                return None
        else:
            try:
                result[name] = {int(val)}
            except ValueError:
                return None
        # 验证范围
        for n in result[name]:
            if n < lo or n > hi:
                return None
    return result


def _next_cron_fire(cron: dict, after: datetime) -> datetime | None:
    """计算 cron 表达式的下一次触发时间（最多向前搜索 366 天）。"""
    dt = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        if (dt.minute in cron["minute"] and
            dt.hour in cron["hour"] and
            dt.day in cron["day"] and
            dt.month in cron["month"] and
            dt.weekday() in cron["weekday"]):
            return dt
        dt += timedelta(minutes=1)
    return None


async def schedule_task(
    chat_id: int,
    message: str,
    action: str = "create",
    delay_minutes: float | None = None,
    trigger_at: str | None = None,
    cron: str | None = None,
    task_id: int | None = None,
) -> str:
    """创建/列出/取消定时任务。"""
    global _next_id

    if action == "list":
        if not _tasks:
            return "[当前没有定时任务]"
        lines = []
        for tid, t in _tasks.items():
            if t.chat_id != chat_id:
                continue
            fire_str = t.next_fire.strftime("%Y-%m-%d %H:%M CST")
            trigger_desc = f"cron={t.cron_expr}" if t.trigger_type == "cron" else t.trigger_type
            lines.append(f"  #{tid} [{trigger_desc}] 下次: {fire_str} | {t.message[:50]}")
        if not lines:
            return "[当前聊天没有定时任务]"
        return f"定时任务（共 {len(lines)} 个）：\n" + "\n".join(lines)

    if action == "cancel":
        if task_id is None:
            return "[错误：缺少 task_id]"
        t = _tasks.get(task_id)
        if t is None:
            return f"[错误：任务 #{task_id} 不存在]"
        if t.chat_id != chat_id:
            return f"[error: task #{task_id} not found]"
        _tasks.pop(task_id, None)
        await _cancel_scheduled_task(t)
        return f"[已取消任务 #{task_id}]"

    if action == "cancel_all":
        removed = 0
        to_remove = [tid for tid, t in _tasks.items() if t.chat_id == chat_id]
        for tid in to_remove:
            t = _tasks.pop(tid)
            await _cancel_scheduled_task(t)
            removed += 1
        return f"[已取消 {removed} 个任务]"

    # action == "create"
    if len(message) > 500:
        return "[错误：message 过长，上限 500 字符]"

    # 统计当前 chat 的任务数
    chat_count = sum(1 for t in _tasks.values() if t.chat_id == chat_id)
    if chat_count >= MAX_ACTIVE_TASKS:
        return f"[错误：已达上限 {MAX_ACTIVE_TASKS} 个任务/聊天]"

    now = datetime.now(_CST)

    if cron:
        parsed = _parse_cron(cron)
        if parsed is None:
            return f"[错误：cron 表达式无效：{cron}。需要 5 段：分 时 日 月 周]"
        next_fire = _next_cron_fire(parsed, now)
        if next_fire is None:
            return f"[错误：cron 表达式在可预见时间内不会触发：{cron}]"
        trigger_type = "cron"
    elif delay_minutes is not None:
        try:
            delay = float(delay_minutes)
        except (TypeError, ValueError):
            return "[错误：delay_minutes 不是有效数字]"
        if delay <= 0 or delay > 1440:
            return "[错误：delay_minutes 需在 1~1440 分钟之间]"
        next_fire = now + timedelta(minutes=delay)
        trigger_type = "delay"
    elif trigger_at:
        try:
            # 兼容带/不带时区的 ISO 字符串
            next_fire = datetime.fromisoformat(trigger_at)
            if next_fire.tzinfo is None:
                next_fire = next_fire.replace(tzinfo=_CST)
            if next_fire <= now:
                return "[错误：trigger_at 时间已过]"
            if next_fire > now + timedelta(days=365):
                return "[错误：trigger_at 超过 365 天]"
        except ValueError:
            return f"[错误：trigger_at 格式无效：{trigger_at}]"
        trigger_type = "at"
    else:
        return "[错误：需要指定 cron / delay_minutes / trigger_at 之一]"

    tid = _next_id
    _next_id += 1

    task = ScheduledTask(
        task_id=tid,
        chat_id=chat_id,
        message=message,
        trigger_type=trigger_type,
        cron_expr=cron or "",
        next_fire=next_fire,
    )

    # 如果是 cron，需要在触发后重新调度下一次
    _tasks[tid] = task
    if trigger_type == "cron":
        task.handle = asyncio.create_task(_cron_loop(tid), name=f"scheduler-cron-{tid}")
    else:
        task.handle = asyncio.create_task(_single_fire(tid), name=f"scheduler-once-{tid}")
    task.handle.add_done_callback(_consume_task_result)
    fire_str = next_fire.strftime("%Y-%m-%d %H:%M CST")
    logger.info(f"⏰ 创建定时任务 #{tid} | chat={chat_id} | type={trigger_type} | fire={fire_str} | msg={message[:60]}")
    return f"[已创建任务 #{tid}，{'下次触发' if trigger_type == 'cron' else '触发时间'}: {fire_str}]"


async def _single_fire(task_id: int):
    """一次性任务：等到触发时间，执行回调。"""
    current_handle = asyncio.current_task()
    try:
        task = _tasks.get(task_id)
        if task is None:
            return
        delay = (task.next_fire - datetime.now(_CST)).total_seconds()
        if delay > 0:
            await asyncio.sleep(delay)
        if _tasks.get(task_id) is not task:
            return
        await _fire_task(task)
    finally:
        current = _tasks.get(task_id)
        if current is not None and current.handle is current_handle:
            _tasks.pop(task_id, None)


async def _cron_loop(task_id: int):
    """cron 任务：循环触发。"""
    current_handle = asyncio.current_task()
    try:
        while True:
            task = _tasks.get(task_id)
            if task is None:
                return
            delay = (task.next_fire - datetime.now(_CST)).total_seconds()
            if delay > 0:
                await asyncio.sleep(delay)
            if _tasks.get(task_id) is not task:
                return
            await _fire_task(task)
            parsed = _parse_cron(task.cron_expr)
            if parsed is None:
                return
            next_fire = _next_cron_fire(parsed, datetime.now(_CST))
            if next_fire is None:
                return
            task.next_fire = next_fire
    finally:
        current = _tasks.get(task_id)
        if current is not None and current.handle is current_handle:
            _tasks.pop(task_id, None)


async def _fire_task(task: ScheduledTask):
    """任务触发：调用 LLM 产生一条回复并发送到聊天。

    通过 application 引用获取 bot 和 ContextManager，
    注入系统提醒消息触发一次完整对话。
    """
    try:
        import telegram
        from llm.llm_client import get_llm_client

        app = _get_application()
        if app is None:
            logger.warning("⏰ 定时任务触发时 application 未就绪，跳过")
            return

        bot = app.bot
        context_mgr = app.bot_data.get("context_mgr")

        if not context_mgr:
            logger.warning("⏰ 定时任务触发时缺少 context_mgr，跳过")
            return

        trigger_message = f"[系统提醒：你之前设定了一个定时任务，现在触发了] {task.message}"
        logger.info(f"⏰ 定时任务触发 #{task.task_id} | chat={task.chat_id} | msg={task.message[:60]}")

        llm = get_llm_client()

        # 发送 typing 状态
        try:
            await bot.send_chat_action(chat_id=task.chat_id, action=telegram.constants.ChatAction.TYPING)
        except Exception:
            pass

        # 走完整 chat_stream 对话
        full_text = ""
        async for ev in llm.chat_stream(
            context_mgr=context_mgr,
            chat_id=task.chat_id,
            current_message=trigger_message,
            current_sender="定时任务",
            trigger_type="scheduled_task",
        ):
            if ev.type == "text_chunk":
                full_text += ev.text
            elif ev.type == "tool_call":
                try:
                    await bot.send_chat_action(chat_id=task.chat_id, action=telegram.constants.ChatAction.TYPING)
                except Exception:
                    pass
            elif ev.type == "done":
                full_text = ev.text or full_text
            elif ev.type == "error":
                logger.warning(f"⏰ 定时任务 LLM 调用失败: {ev.text}")
                return

        if not full_text.strip():
            logger.info(f"⏰ 定时任务 #{task.task_id} LLM 返回空内容，不发送")
            return

        # 清理控制标记
        import re
        from llm.prompt import (
    STICKER_PREFIX,
    STICKER_SUFFIX,
)
        clean = re.sub(r"\[STICKER:[^\]]+\]", "", full_text).strip()
        sticker_emojis = re.findall(
            re.escape(STICKER_PREFIX) + r"(.*?)" + re.escape(STICKER_SUFFIX),
            full_text,
        )

        if clean:
            try:
                await bot.send_message(chat_id=task.chat_id, text=clean)
            except Exception as e:
                logger.warning(f"⏰ 定时任务发送消息失败: {e}")

        # 发送贴纸
        if sticker_emojis:
            sticker_mgr = app.bot_data.get("sticker_mgr")
            if sticker_mgr:
                for emoji in sticker_emojis[:1]:
                    file_id = sticker_mgr.get_file_id(emoji)
                    if file_id:
                        try:
                            await bot.send_sticker(chat_id=task.chat_id, sticker=file_id)
                        except Exception as e:
                            logger.warning(f"⏰ 定时任务发送贴纸失败: {e}")

    except Exception as e:
        logger.exception(f"⏰ 定时任务执行异常: {e}")


# ── Application 引用管理 ──

_application = None


def set_application(app):
    """在启动时注册 application 引用。"""
    global _application
    _application = app


def _get_application():
    return _application


async def cancel_all_tasks():
    """取消所有定时任务（进程关闭时调用）。"""
    tasks = list(_tasks.values())
    _tasks.clear()
    for task in tasks:
        await _cancel_scheduled_task(task)
    logger.info("⏰ 已取消所有定时任务")


# ── Tool definitions (OpenAI function call 格式) ──

SCHEDULE_TASK_TOOL_DEFINITION = {
    "type": "function",
    "function": {
        "name": "schedule_task",
        "description": (
            "创建、查看或取消定时任务。任务到期后会自动触发你进行一次对话回复。"
            "支持三种模式：delay_minutes（N分钟后触发）、trigger_at（指定ISO时间触发）、cron（循环触发）。"
            "不持久化，重启后任务消失。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["create", "list", "cancel", "cancel_all"],
                    "description": "操作类型，默认 create",
                },
                "delay_minutes": {
                    "type": "number",
                    "description": "延迟 N 分钟后触发（1~1440）",
                },
                "trigger_at": {
                    "type": "string",
                    "description": "ISO 格式触发时间，如 2026-06-24T09:00:00+08:00",
                },
                "cron": {
                    "type": "string",
                    "description": "5段 cron 表达式：分 时 日 月 周。如 '0 9 * * *' 每天9点、'*/30 * * * *' 每30分钟",
                },
                "message": {
                    "type": "string",
                    "description": "任务触发时的提醒内容，告诉未来的你该做什么（最长500字）",
                },
                "task_id": {
                    "type": "integer",
                    "description": "cancel 操作时指定的任务 ID",
                },
            },
            "required": ["action"],
        },
    },
}

SCHEDULE_TASK_TOOLS = [SCHEDULE_TASK_TOOL_DEFINITION]
