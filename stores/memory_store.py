"""旧版单一对话/全局记忆存储管理。"""
from __future__ import annotations

import os
from typing import Any

from sqlalchemy import delete, select, update

from app_config.config import DATA_DIR
from stores.orm import MemoryRow, orm_session
from stores.timestamps import utc_now_iso

DB_PATH = os.path.join(DATA_DIR, "self_evolution.sqlite3")


def _row_dict(row: MemoryRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "scope": row.scope,
        "key": row.key,
        "value": row.value,
        "source": row.source,
        "active": row.active,
        "chat_id": row.chat_id,
        "user_id": row.user_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def init_db() -> None:
    with orm_session(DB_PATH):
        pass


def list_memories(scope: str | None = None, chat_id: str | None = None, include_inactive: bool = True, limit: int | None = 200) -> list[dict[str, Any]]:
    init_db()
    stmt = select(MemoryRow)
    if scope:
        stmt = stmt.where(MemoryRow.scope == scope)
    if chat_id is not None:
        stmt = stmt.where(MemoryRow.chat_id == str(chat_id))
    if not include_inactive:
        stmt = stmt.where(MemoryRow.active == 1)
    stmt = stmt.order_by(MemoryRow.active.desc(), MemoryRow.updated_at.desc(), MemoryRow.id.desc())
    if limit is not None:
        stmt = stmt.limit(max(1, min(int(limit), 500)))
    with orm_session(DB_PATH) as session:
        return [_row_dict(r) for r in session.scalars(stmt).all()]


def get_memory(memory_id: int) -> dict[str, Any] | None:
    init_db()
    with orm_session(DB_PATH) as session:
        row = session.get(MemoryRow, int(memory_id))
        return _row_dict(row) if row else None


def add_memory(scope: str, value: str, key: str = "", chat_id: str | None = None, user_id: str | None = None, source: str = "manual") -> int:
    init_db()
    now = utc_now_iso()
    row = MemoryRow(
        scope=scope,
        key=key or "",
        value=value,
        source=source,
        active=1,
        chat_id=str(chat_id) if chat_id is not None else None,
        user_id=str(user_id) if user_id is not None else None,
        created_at=now,
        updated_at=now,
    )
    with orm_session(DB_PATH) as session:
        session.add(row)
        session.flush()
        return int(row.id)


def set_memory_active(memory_id: int, active: bool) -> bool:
    init_db()
    with orm_session(DB_PATH) as session:
        result = session.execute(
            update(MemoryRow)
            .where(MemoryRow.id == int(memory_id))
            .values(active=1 if active else 0, updated_at=utc_now_iso())
        )
        return bool(result.rowcount)


def delete_memory(memory_id: int) -> bool:
    init_db()
    with orm_session(DB_PATH) as session:
        result = session.execute(delete(MemoryRow).where(MemoryRow.id == int(memory_id)))
        return bool(result.rowcount)


def update_memory(memory_id: int, value: str, key: str | None = None) -> bool:
    init_db()
    value = (value or "").strip()
    if not value:
        return False
    values: dict[str, Any] = {"value": value, "updated_at": utc_now_iso()}
    if key is not None:
        values["key"] = key or ""
    with orm_session(DB_PATH) as session:
        result = session.execute(update(MemoryRow).where(MemoryRow.id == int(memory_id)).values(**values))
        return bool(result.rowcount)


def find_chat_memory(chat_id: str | int, memory_id: int | None = None, key: str = "", query: str = "") -> dict[str, Any] | None:
    rows = list_memories(scope="chat", chat_id=str(chat_id), include_inactive=False, limit=200)
    if memory_id is not None:
        return next((r for r in rows if int(r.get("id", 0)) == int(memory_id)), None)
    q_key = (key or "").strip().lower()
    q_text = (query or "").strip().lower()
    if q_key:
        for r in rows:
            if str(r.get("key") or "").strip().lower() == q_key:
                return r
    if q_text:
        for r in rows:
            value = str(r.get("value") or "").strip().lower()
            if q_text in value:
                return r
    return None
