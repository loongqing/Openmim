"""Per-chat activity tracker for idle topic seeding."""
from __future__ import annotations

import os
from dataclasses import dataclass

from app_config.config import DATA_DIR
from stores.orm import ChatActivityRow, orm_session
from stores.timestamps import utc_now_iso

DB_PATH = os.path.join(DATA_DIR, "chat_activity.sqlite3")


@dataclass(slots=True)
class ActivityState:
    chat_id: str
    last_user_at: str | None
    last_bot_at: str | None
    last_seed_at: str | None
    seed_count: int
    updated_at: str


class ActivityStore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with orm_session(self.db_path):
            pass

    @staticmethod
    def _state(row: ChatActivityRow) -> ActivityState:
        return ActivityState(
            chat_id=row.chat_id,
            last_user_at=row.last_user_at,
            last_bot_at=row.last_bot_at,
            last_seed_at=row.last_seed_at,
            seed_count=int(row.seed_count),
            updated_at=row.updated_at,
        )

    def get(self, chat_id: str | int) -> ActivityState:
        cid = str(chat_id)
        now = utc_now_iso()
        with orm_session(self.db_path) as session:
            row = session.get(ChatActivityRow, cid)
            if row is None:
                row = ChatActivityRow(chat_id=cid, last_user_at=None, last_bot_at=None, last_seed_at=None, seed_count=0, updated_at=now)
                session.add(row)
                session.flush()
            return self._state(row)

    def touch_user_message(self, chat_id: str | int) -> ActivityState:
        cid = str(chat_id)
        now = utc_now_iso()
        with orm_session(self.db_path) as session:
            row = session.get(ChatActivityRow, cid)
            if row is None:
                row = ChatActivityRow(chat_id=cid, seed_count=0, updated_at=now)
                session.add(row)
            row.last_user_at = now
            row.updated_at = now
        return self.get(cid)

    def touch_bot_message(self, chat_id: str | int) -> ActivityState:
        cid = str(chat_id)
        now = utc_now_iso()
        with orm_session(self.db_path) as session:
            row = session.get(ChatActivityRow, cid)
            if row is None:
                row = ChatActivityRow(chat_id=cid, seed_count=0, updated_at=now)
                session.add(row)
            row.last_bot_at = now
            row.updated_at = now
        return self.get(cid)

    def mark_seed_sent(self, chat_id: str | int) -> ActivityState:
        cid = str(chat_id)
        now = utc_now_iso()
        state = self.get(cid)
        with orm_session(self.db_path) as session:
            row = session.get(ChatActivityRow, cid)
            if row is None:
                row = ChatActivityRow(chat_id=cid, seed_count=0, updated_at=now)
                session.add(row)
            row.last_seed_at = now
            row.seed_count = state.seed_count + 1
            row.updated_at = now
        return self.get(cid)


_store: ActivityStore | None = None


def get_activity_store() -> ActivityStore:
    global _store
    if _store is None:
        _store = ActivityStore()
    return _store
