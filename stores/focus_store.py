"""Focus mode state for per-chat trigger boost."""
from __future__ import annotations

import os
import json
from dataclasses import dataclass

from sqlalchemy import delete

from stores.orm import FocusCriteriaRow, FocusStateRow, FocusSuppressionRow, orm_session
from stores.timestamps import utc_now_iso
from app_config.config import DATA_DIR

DEFAULT_FOCUS_CRITERIA = {"extra_note": ""}
DB_PATH = os.path.join(DATA_DIR, "self_evolution.sqlite3")


@dataclass(slots=True)
class FocusState:
    chat_id: str
    active: int
    trigger_count: int
    refreshed_at: str
    updated_at: str


class FocusStore:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        with orm_session(self.db_path):
            pass

    @staticmethod
    def _state(row: FocusStateRow) -> FocusState:
        return FocusState(
            chat_id=row.chat_id,
            active=int(row.active),
            trigger_count=int(row.trigger_count),
            refreshed_at=row.refreshed_at,
            updated_at=row.updated_at,
        )

    def get(self, chat_id: str | int) -> FocusState:
        cid = str(chat_id)
        now = utc_now_iso()
        with orm_session(self.db_path) as session:
            row = session.get(FocusStateRow, cid)
            if row is None:
                row = FocusStateRow(chat_id=cid, active=0, trigger_count=0, refreshed_at=now, updated_at=now)
                session.add(row)
                session.flush()
            return self._state(row)

    def refresh(self, chat_id: str | int) -> FocusState:
        cid = str(chat_id)
        now = utc_now_iso()
        with orm_session(self.db_path) as session:
            row = session.get(FocusStateRow, cid)
            if row is None:
                row = FocusStateRow(chat_id=cid, active=1, trigger_count=0, refreshed_at=now, updated_at=now)
                session.add(row)
            else:
                row.active = 1
                row.trigger_count = 0
                row.refreshed_at = now
                row.updated_at = now
        return self.get(cid)

    def reserve_bot_trigger(self, chat_id: str | int) -> tuple[FocusState, bool]:
        cid = str(chat_id)
        now = utc_now_iso()
        reserved = False
        with orm_session(self.db_path) as session:
            # Force SQLite write lock for atomic read-modify-write.
            session.connection().exec_driver_sql("BEGIN IMMEDIATE")
            row = session.get(FocusStateRow, cid)
            if row is None:
                row = FocusStateRow(chat_id=cid, active=0, trigger_count=0, refreshed_at=now, updated_at=now)
                session.add(row)
                session.flush()
            if row.active:
                row.trigger_count = int(row.trigger_count) + 1
                reserved = True
            row.updated_at = now
        return self.get(cid), reserved

    def register_bot_trigger(self, chat_id: str | int) -> FocusState:
        state, _reserved = self.reserve_bot_trigger(chat_id)
        return state

    def clear(self, chat_id: str | int) -> None:
        cid = str(chat_id)
        now = utc_now_iso()
        with orm_session(self.db_path) as session:
            row = session.get(FocusStateRow, cid)
            if row is None:
                row = FocusStateRow(chat_id=cid, active=0, trigger_count=0, refreshed_at=now, updated_at=now)
                session.add(row)
            else:
                row.active = 0
                row.trigger_count = 0
                row.updated_at = now

    def is_suppressed(self, chat_id: str | int) -> bool:
        cid = str(chat_id)
        with orm_session(self.db_path) as session:
            row = session.get(FocusSuppressionRow, cid)
            return bool(row and int(row.suppressed))

    def set_suppressed(self, chat_id: str | int, suppressed: bool, reason: str = "") -> None:
        cid = str(chat_id)
        now = utc_now_iso()
        safe_reason = (reason or "").strip()[:500]
        with orm_session(self.db_path) as session:
            row = session.get(FocusSuppressionRow, cid)
            if row is None:
                row = FocusSuppressionRow(chat_id=cid, suppressed=1 if suppressed else 0, reason=safe_reason, updated_at=now)
                session.add(row)
            else:
                row.suppressed = 1 if suppressed else 0
                row.reason = safe_reason
                row.updated_at = now
            if suppressed:
                state = session.get(FocusStateRow, cid)
                if state is None:
                    state = FocusStateRow(chat_id=cid, active=0, trigger_count=0, refreshed_at=now, updated_at=now)
                    session.add(state)
                else:
                    state.active = 0
                    state.trigger_count = 0
                    state.updated_at = now

    def get_criteria(self, chat_id: str | int) -> dict:
        cid = str(chat_id)
        with orm_session(self.db_path) as session:
            row = session.get(FocusCriteriaRow, cid)
            if row is None:
                return dict(DEFAULT_FOCUS_CRITERIA)
            try:
                return json.loads(row.criteria_json)
            except (json.JSONDecodeError, TypeError):
                return dict(DEFAULT_FOCUS_CRITERIA)

    def set_criteria(self, chat_id: str | int, criteria: dict) -> None:
        cid = str(chat_id)
        now = utc_now_iso()
        criteria_json = json.dumps(criteria, ensure_ascii=False)
        with orm_session(self.db_path) as session:
            row = session.get(FocusCriteriaRow, cid)
            if row is None:
                session.add(FocusCriteriaRow(chat_id=cid, criteria_json=criteria_json, updated_at=now))
            else:
                row.criteria_json = criteria_json
                row.updated_at = now

    def get_criteria_note(self, chat_id: str | int) -> str:
        criteria = self.get_criteria(chat_id)
        return (criteria.get("extra_note") or "").strip()

    def reset_chat(self, chat_id: str | int) -> None:
        cid = str(chat_id)
        with orm_session(self.db_path) as session:
            session.execute(delete(FocusStateRow).where(FocusStateRow.chat_id == cid))
            session.execute(delete(FocusCriteriaRow).where(FocusCriteriaRow.chat_id == cid))
            session.execute(delete(FocusSuppressionRow).where(FocusSuppressionRow.chat_id == cid))


_store: FocusStore | None = None


def get_focus_store() -> FocusStore:
    global _store
    if _store is None:
        _store = FocusStore()
    return _store
