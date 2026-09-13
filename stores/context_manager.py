"""
上下文管理器
为每个群组维护最近 N 条消息的滑动窗口，用于馈送给 LLM。
当前默认持久化到本地 SQLite，启动时可自动从旧 JSON 迁移。
"""
import json
import os
import asyncio
import logging
from dataclasses import asdict, dataclass, field

from sqlalchemy import delete, distinct, func, select, update

from stores.orm import ContextMessageRow, orm_session
from app_config.config import CONTEXT_MESSAGE_COUNT, DATA_DIR

logger = logging.getLogger(__name__)
CONTEXT_STORE_FILE = os.path.join(DATA_DIR, "context_history.sqlite3")
LEGACY_CONTEXT_STORE_FILE = os.path.join(DATA_DIR, "context_history.json")


@dataclass(slots=True)
class ContextMessage:
    """单条上下文消息"""

    sender_name: str
    text: str = ""
    message_type: str = "text"
    is_reply_to_bot: bool = False
    is_mention: bool = False
    caption: str = ""
    emoji: str = ""
    image_file_ids: list[str] = field(default_factory=list)
    user_id: int | None = None
    username: str = ""
    message_id: int | None = None
    reply_to_message_id: int | None = None
    file_id: str = ""
    file_name: str = ""
    char_count: int = field(default=0, init=False)

    def __post_init__(self):
        self.image_file_ids = list(self.image_file_ids or [])
        self.username = self.username or ""
        self.file_id = self.file_id or ""
        self.file_name = self.file_name or ""
        if not self.char_count:
            self.char_count = len(self.text or self.caption or self.emoji or "")

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ContextMessage":
        obj = cls(
            sender_name=data.get("sender_name", "未知"),
            text=data.get("text", ""),
            message_type=data.get("message_type", "text"),
            is_reply_to_bot=bool(data.get("is_reply_to_bot", False)),
            is_mention=bool(data.get("is_mention", False)),
            caption=data.get("caption", ""),
            emoji=data.get("emoji", ""),
            image_file_ids=data.get("image_file_ids", []),
            user_id=data.get("user_id"),
            username=data.get("username", ""),
            message_id=data.get("message_id"),
            reply_to_message_id=data.get("reply_to_message_id"),
            file_id=data.get("file_id", ""),
            file_name=data.get("file_name", ""),
        )
        obj.char_count = int(data.get("char_count", obj.char_count))
        return obj


class ContextManager:
    """异步安全、可持久化的群聊上下文管理器。"""

    def __init__(self, max_messages: int = CONTEXT_MESSAGE_COUNT, store_file: str = CONTEXT_STORE_FILE):
        self._max = max_messages
        self._store_file = store_file
        self._legacy_store_file = LEGACY_CONTEXT_STORE_FILE
        self._lock = asyncio.Lock()
        self._init_db()
        self._migrate_from_legacy_json_if_needed()

    def _init_db(self):
        with orm_session(self._store_file):
            pass

    def _legacy_source_file(self) -> str | None:
        if self._store_file.lower().endswith('.json'):
            return self._store_file
        store_dir = os.path.dirname(self._store_file) or '.'
        sibling_legacy = os.path.join(store_dir, 'context_history.json')
        if os.path.exists(sibling_legacy):
            return sibling_legacy
        if self._legacy_store_file and os.path.exists(self._legacy_store_file):
            return self._legacy_store_file
        return None

    @staticmethod
    def _row_to_message(row: ContextMessageRow) -> ContextMessage:
        try:
            image_file_ids = json.loads(row.image_file_ids or '[]')
            if not isinstance(image_file_ids, list):
                image_file_ids = []
        except Exception:
            image_file_ids = []
        return ContextMessage(
            sender_name=row.sender_name,
            text=row.text or '',
            message_type=row.message_type or 'text',
            is_reply_to_bot=bool(row.is_reply_to_bot),
            is_mention=bool(row.is_mention),
            caption=row.caption or '',
            emoji=row.emoji or '',
            image_file_ids=image_file_ids,
            user_id=row.user_id,
            username=row.username or '',
            message_id=row.message_id,
            reply_to_message_id=row.reply_to_message_id,
            file_id=row.file_id or '',
            file_name=row.file_name or '',
        )

    def _count_all_messages(self) -> int:
        with orm_session(self._store_file) as session:
            return int(session.scalar(select(func.count()).select_from(ContextMessageRow)) or 0)

    def _prune_chat(self, session, chat_id: int):
        ids_to_keep = select(ContextMessageRow.id).where(ContextMessageRow.chat_id == int(chat_id)).order_by(ContextMessageRow.id.desc()).limit(int(self._max))
        session.execute(
            delete(ContextMessageRow).where(
                ContextMessageRow.chat_id == int(chat_id),
                ContextMessageRow.id.not_in(ids_to_keep),
            )
        )

    def _migrate_from_legacy_json_if_needed(self):
        if self._count_all_messages() > 0:
            return
        legacy_file = self._legacy_source_file()
        if not legacy_file or not os.path.exists(legacy_file):
            return
        try:
            with open(legacy_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
            migrated_chats = 0
            migrated_messages = 0
            with orm_session(self._store_file) as session:
                for chat_id_s, items in data.items():
                    chat_id = int(chat_id_s)
                    for item in items[-self._max:]:
                        msg = ContextMessage.from_dict(item)
                        self._insert_message(session, chat_id, msg)
                    self._prune_chat(session, chat_id)
                    migrated_chats += 1
                    migrated_messages += min(len(items), self._max)
            logger.info(f"🧠 已从旧 JSON 迁移上下文: {migrated_chats} 个聊天 / {migrated_messages} 条消息")
        except Exception:
            logger.exception("旧 JSON 上下文迁移失败，将继续使用空上下文")

    @staticmethod
    def _insert_message(session, chat_id: int, msg: ContextMessage):
        session.add(ContextMessageRow(
            chat_id=int(chat_id),
            sender_name=msg.sender_name,
            text=msg.text or '',
            message_type=msg.message_type or 'text',
            is_reply_to_bot=int(bool(msg.is_reply_to_bot)),
            is_mention=int(bool(msg.is_mention)),
            caption=msg.caption or '',
            emoji=msg.emoji or '',
            char_count=int(msg.char_count),
            image_file_ids=json.dumps(msg.image_file_ids or [], ensure_ascii=False),
            user_id=msg.user_id,
            username=msg.username or '',
            message_id=msg.message_id,
            reply_to_message_id=msg.reply_to_message_id,
            file_id=msg.file_id or '',
            file_name=msg.file_name or '',
            created_at="",
        ))

    @staticmethod
    def _trim_text(text: str, limit: int) -> str:
        if not text or limit <= 0 or len(text) <= limit:
            return text
        head = max(1, limit // 2)
        tail = max(1, limit - head - 3)
        return text[:head] + "..." + text[-tail:]

    def _append_sync(self, chat_id: int, msg: ContextMessage) -> int:
        with orm_session(self._store_file) as session:
            self._insert_message(session, chat_id, msg)
            self._prune_chat(session, chat_id)
            return int(
                session.scalar(
                    select(func.count())
                    .select_from(ContextMessageRow)
                    .where(ContextMessageRow.chat_id == int(chat_id))
                )
                or 0
            )

    def _get_context_sync(self, chat_id: int) -> list[ContextMessage]:
        with orm_session(self._store_file) as session:
            rows = session.scalars(
                select(ContextMessageRow)
                .where(ContextMessageRow.chat_id == int(chat_id))
                .order_by(ContextMessageRow.id.asc())
            ).all()
            return [self._row_to_message(row) for row in rows]

    def _get_recent_sync(self, chat_id: int, n: int) -> list[ContextMessage]:
        with orm_session(self._store_file) as session:
            rows = session.scalars(
                select(ContextMessageRow)
                .where(ContextMessageRow.chat_id == int(chat_id))
                .order_by(ContextMessageRow.id.desc())
                .limit(n)
            ).all()
            return [self._row_to_message(row) for row in reversed(rows)]

    def _chat_ids_sync(self) -> list[int]:
        with orm_session(self._store_file) as session:
            rows = session.scalars(
                select(distinct(ContextMessageRow.chat_id))
                .order_by(ContextMessageRow.chat_id.asc())
            ).all()
            return [int(row) for row in rows]

    def _clear_sync(self, chat_id: int) -> None:
        with orm_session(self._store_file) as session:
            session.execute(
                delete(ContextMessageRow).where(ContextMessageRow.chat_id == int(chat_id))
            )

    def _compact_chat_sync(self, chat_id: int, max_user_chars: int, max_bot_chars: int) -> None:
        with orm_session(self._store_file) as session:
            rows = session.scalars(
                select(ContextMessageRow)
                .where(ContextMessageRow.chat_id == int(chat_id))
                .order_by(ContextMessageRow.id.asc())
            ).all()
            for row in rows:
                limit = max_bot_chars if row.message_type == 'bot' else max_user_chars
                new_text = self._trim_text(row.text or '', limit)
                new_caption = self._trim_text(row.caption or '', limit)
                new_char_count = len(new_text or new_caption or (row.emoji or ''))
                if (
                    new_text != (row.text or '')
                    or new_caption != (row.caption or '')
                    or new_char_count != int(row.char_count or 0)
                ):
                    session.execute(
                        update(ContextMessageRow)
                        .where(ContextMessageRow.id == row.id)
                        .values(
                            text=new_text,
                            caption=new_caption,
                            char_count=new_char_count,
                        )
                    )

    @staticmethod
    async def _await_thread_completion(callable_, *args):
        """Do not release serialization locks until a cancelled DB thread has exited."""
        task = asyncio.create_task(asyncio.to_thread(callable_, *args))
        try:
            return await asyncio.shield(task)
        except asyncio.CancelledError:
            try:
                await task
            except Exception:
                logger.exception("数据库线程在取消收尾阶段失败")
            raise

    async def append(self, chat_id: int, msg: ContextMessage):
        """添加一条消息到指定聊天的上下文"""
        async with self._lock:
            count = await self._await_thread_completion(self._append_sync, chat_id, msg)
            logger.debug(f"🧠 记录上下文 chat={chat_id} count={count}/{self._max}")

    async def get_context(self, chat_id: int) -> list[ContextMessage]:
        """获取指定聊天的完整上下文（快照）"""
        async with self._lock:
            return await self._await_thread_completion(self._get_context_sync, chat_id)

    async def get_recent(self, chat_id: int, n: int = 10) -> list[ContextMessage]:
        """获取最近 n 条消息"""
        n = max(0, int(n))
        if n <= 0:
            return []
        async with self._lock:
            return await self._await_thread_completion(self._get_recent_sync, chat_id, n)

    async def get_since_timestamp(self, chat_id: int, timestamp: str) -> list[ContextMessage]:
        msgs = await self.get_context(chat_id)
        return msgs

    async def get_since_last_bot(self, chat_id: int, include_last_bot: bool = True) -> list[ContextMessage]:
        _, msgs = await self.get_since_last_bot_with_total(chat_id, include_last_bot)
        return msgs

    async def get_since_last_bot_with_total(self, chat_id: int, include_last_bot: bool = True) -> tuple[int, list[ContextMessage]]:
        msgs = await self.get_context(chat_id)
        last_bot_idx = -1
        for i in range(len(msgs) - 1, -1, -1):
            if msgs[i].message_type == 'bot':
                last_bot_idx = i
                break
        if last_bot_idx < 0:
            return len(msgs), msgs
        start = last_bot_idx if include_last_bot else last_bot_idx + 1
        return len(msgs), msgs[start:]

    async def chat_ids(self) -> list[int]:
        async with self._lock:
            return await self._await_thread_completion(self._chat_ids_sync)

    async def clear(self, chat_id: int):
        """清空指定聊天的上下文"""
        async with self._lock:
            await self._await_thread_completion(self._clear_sync, chat_id)

    async def compact_chat(self, chat_id: int, max_user_chars: int, max_bot_chars: int):
        """压缩指定聊天的历史文本，减少常驻内存与磁盘体积。"""
        async with self._lock:
            await self._await_thread_completion(
                self._compact_chat_sync,
                chat_id,
                max_user_chars,
                max_bot_chars,
            )

    @property
    def active_chats(self) -> int:
        with orm_session(self._store_file) as session:
            return int(session.scalar(select(func.count(distinct(ContextMessageRow.chat_id)))) or 0)
