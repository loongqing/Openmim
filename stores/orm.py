"""Shared SQLAlchemy ORM models and session helpers for runtime SQLite stores."""
from __future__ import annotations

import os
from contextlib import contextmanager
from datetime import datetime
from typing import Iterator, Any

from sqlalchemy import (
    DateTime,
    Float,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    inspect,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker
from sqlalchemy.pool import NullPool


class Base(DeclarativeBase):
    pass


class WhitelistChat(Base):
    __tablename__ = "whitelist_chats"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.current_timestamp(), nullable=False)


class GroupSetting(Base):
    __tablename__ = "group_settings"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    key: Mapped[str] = mapped_column(String, primary_key=True)
    value: Mapped[str] = mapped_column(String, nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime,
        server_default=func.current_timestamp(),
        onupdate=func.current_timestamp(),
        nullable=False,
    )


class ContextMessageRow(Base):
    __tablename__ = "context_messages"
    __table_args__ = (
        Index("idx_context_chat_id_id", "chat_id", "id"),
        Index("idx_context_chat_id_message_id", "chat_id", "message_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False)
    sender_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    user_id: Mapped[int] = mapped_column(Integer, nullable=True)
    username: Mapped[str] = mapped_column(Text, nullable=False, default="")
    text: Mapped[str] = mapped_column(Text, nullable=False, default="")
    is_reply_to_bot: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_mention: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    caption: Mapped[str] = mapped_column(Text, nullable=False, default="")
    image_file_ids: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    file_id: Mapped[str] = mapped_column(Text, nullable=False, default="")
    file_name: Mapped[str] = mapped_column(Text, nullable=False, default="")
    emoji: Mapped[str] = mapped_column(Text, nullable=False, default="")
    message_type: Mapped[str] = mapped_column(String, nullable=False, default="text")
    message_id: Mapped[int] = mapped_column(Integer, nullable=True)
    reply_to_message_id: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    char_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class FocusStateRow(Base):
    __tablename__ = "focus_state"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    trigger_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    refreshed_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class FocusCriteriaRow(Base):
    __tablename__ = "focus_criteria"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    criteria_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class FocusSuppressionRow(Base):
    __tablename__ = "focus_suppression"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    suppressed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    reason: Mapped[str] = mapped_column(Text, nullable=False, default="")
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class ChatActivityRow(Base):
    __tablename__ = "chat_activity"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    last_user_at: Mapped[str] = mapped_column(Text, nullable=True)
    last_bot_at: Mapped[str] = mapped_column(Text, nullable=True)
    last_seed_at: Mapped[str] = mapped_column(Text, nullable=True)
    seed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class TokenUsageEvent(Base):
    __tablename__ = "token_usage_events"
    __table_args__ = (
        Index("idx_token_usage_events_date", "request_date"),
        Index("idx_token_usage_events_model", "model"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    model: Mapped[str] = mapped_column(Text, nullable=False)
    request_date: Mapped[str] = mapped_column(Text, nullable=False)
    request_count: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cached_prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class BotReplyMap(Base):
    __tablename__ = "bot_reply_map"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_message_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    bot_message_id: Mapped[int] = mapped_column(Integer, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class MemoryRow(Base):
    __tablename__ = "memories"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(Text, nullable=False, default="global")
    key: Mapped[str] = mapped_column(Text, nullable=False, default="")
    value: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False, default="manual")
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    chat_id: Mapped[str] = mapped_column(Text, nullable=True)
    user_id: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class HumanBehaviorStateRow(Base):
    __tablename__ = "human_behavior_state"

    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    mood: Mapped[str] = mapped_column(Text, nullable=False, default="balanced")
    energy: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    last_interaction_at: Mapped[str] = mapped_column(Text, nullable=False)
    recent_phrases_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    recent_actions_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    recent_stickers_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    last_reply_style: Mapped[str] = mapped_column(Text, nullable=True, default="")
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class AffinityRow(Base):
    __tablename__ = "affinity"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    score: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    total_interactions: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_interaction_at: Mapped[str] = mapped_column(Text, nullable=False)
    first_interaction_at: Mapped[str] = mapped_column(Text, nullable=False)


class DailyFortuneRow(Base):
    __tablename__ = "daily_fortune"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[str] = mapped_column(String, primary_key=True)
    fortune_text: Mapped[str] = mapped_column(Text, nullable=False)
    fortune_level: Mapped[str] = mapped_column(Text, nullable=True)
    lucky_number: Mapped[int] = mapped_column(Integer, nullable=True)
    lucky_color: Mapped[str] = mapped_column(Text, nullable=True)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class DailyTaskRow(Base):
    __tablename__ = "daily_tasks"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    date: Mapped[str] = mapped_column(String, primary_key=True)
    tasks_json: Mapped[str] = mapped_column(Text, nullable=False)
    completed_json: Mapped[str] = mapped_column(Text, nullable=False, default="[]")
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class KeywordTriggerRow(Base):
    __tablename__ = "keyword_triggers"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    keyword: Mapped[str] = mapped_column(String, primary_key=True)
    response: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class AnonymousQuestionRow(Base):
    __tablename__ = "anonymous_questions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[str] = mapped_column(Text, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    answer: Mapped[str] = mapped_column(Text, nullable=True)
    asked_by: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    answered_at: Mapped[str] = mapped_column(Text, nullable=True)


class MutedUserRow(Base):
    __tablename__ = "muted_users"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(String, primary_key=True)
    muted_at: Mapped[str] = mapped_column(Text, nullable=False)


class TopicModeRow(Base):
    __tablename__ = "topic_mode"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    topic_name: Mapped[str] = mapped_column(Text, nullable=True)
    started_at: Mapped[str] = mapped_column(Text, nullable=False)


class HistoryGuessGameRow(Base):
    __tablename__ = "history_guess_game"

    chat_id: Mapped[str] = mapped_column(String, primary_key=True)
    active: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)
    started_by: Mapped[str] = mapped_column(Text, nullable=False)
    message_id: Mapped[int] = mapped_column(Integer, nullable=True)
    image_url: Mapped[str] = mapped_column(Text, nullable=False)
    source_name: Mapped[str] = mapped_column(Text, nullable=True)
    source_url: Mapped[str] = mapped_column(Text, nullable=True)
    title: Mapped[str] = mapped_column(Text, nullable=True)
    description: Mapped[str] = mapped_column(Text, nullable=True)
    year_text: Mapped[str] = mapped_column(Text, nullable=True)
    year_start: Mapped[int] = mapped_column(Integer, nullable=True)
    year_end: Mapped[int] = mapped_column(Integer, nullable=True)
    era_label: Mapped[str] = mapped_column(Text, nullable=True)
    country: Mapped[str] = mapped_column(Text, nullable=True)
    region: Mapped[str] = mapped_column(Text, nullable=True)
    city: Mapped[str] = mapped_column(Text, nullable=True)
    answer_text: Mapped[str] = mapped_column(Text, nullable=False)
    solved: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    solved_by: Mapped[str] = mapped_column(Text, nullable=True)
    solved_at: Mapped[str] = mapped_column(Text, nullable=True)
    reveal_requested: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    revealed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    difficulty: Mapped[str] = mapped_column(Text, nullable=False, default="hard")
    era_options_json: Mapped[str] = mapped_column(Text, nullable=True)
    place_options_json: Mapped[str] = mapped_column(Text, nullable=True)


class KnownUserRow(Base):
    __tablename__ = "known_users"
    __table_args__ = (Index("idx_known_users_chat_username", "chat_id", "username"),)

    chat_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(Text, nullable=True, default="")
    display_name: Mapped[str] = mapped_column(Text, nullable=True, default="")
    anon_label: Mapped[str] = mapped_column(Text, nullable=True, default="")
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)


class UserPersonaRow(Base):
    __tablename__ = "user_persona"
    __table_args__ = (UniqueConstraint("chat_id", "user_id", name="uq_user_persona_chat_user"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    chat_id: Mapped[int] = mapped_column(Integer, nullable=False)
    user_id: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(Text, nullable=True, default="")
    anon_label: Mapped[str] = mapped_column(Text, nullable=True, default="")
    persona_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


class UserPersonaGlobalRow(Base):
    __tablename__ = "user_persona_global"

    user_id: Mapped[int] = mapped_column(Integer, primary_key=True)
    display_name: Mapped[str] = mapped_column(Text, nullable=True, default="")
    username: Mapped[str] = mapped_column(Text, nullable=True, default="")
    persona_json: Mapped[str] = mapped_column(Text, nullable=False, default="{}")
    updated_at: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[str] = mapped_column(Text, nullable=False)


def _db_url(path: str) -> str:
    return "sqlite:///" + os.path.abspath(path)


def _ensure_legacy_columns(engine) -> None:
    optional_columns = {
        "daily_fortune": {
            "fortune_level": "ALTER TABLE daily_fortune ADD COLUMN fortune_level TEXT",
        },
        "history_guess_game": {
            "difficulty": "ALTER TABLE history_guess_game ADD COLUMN difficulty TEXT NOT NULL DEFAULT 'hard'",
            "era_options_json": "ALTER TABLE history_guess_game ADD COLUMN era_options_json TEXT",
            "place_options_json": "ALTER TABLE history_guess_game ADD COLUMN place_options_json TEXT",
        },
        "context_messages": {
            "file_id": "ALTER TABLE context_messages ADD COLUMN file_id TEXT NOT NULL DEFAULT ''",
            "file_name": "ALTER TABLE context_messages ADD COLUMN file_name TEXT NOT NULL DEFAULT ''",
        },
    }
    inspector = inspect(engine)
    existing_tables = set(inspector.get_table_names())
    with engine.begin() as conn:
        for table, columns in optional_columns.items():
            if table not in existing_tables:
                continue
            existing_columns = {col["name"] for col in inspector.get_columns(table)}
            for col_name, ddl in columns.items():
                if col_name not in existing_columns:
                    conn.exec_driver_sql(ddl)


def create_runtime_engine(db_file: str):
    os.makedirs(os.path.dirname(db_file) or ".", exist_ok=True)
    engine = create_engine(
        _db_url(db_file),
        future=True,
        poolclass=NullPool,
        connect_args={"check_same_thread": False},
    )
    with engine.begin() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.exec_driver_sql("PRAGMA synchronous=NORMAL")
    Base.metadata.create_all(engine)
    _ensure_legacy_columns(engine)
    return engine


_engine_cache: dict[str, Any] = {}


def get_engine(db_file: str):
    """按数据库路径缓存 SQLAlchemy 引擎，避免每次会话重建引擎 + create_all。"""
    key = os.path.abspath(db_file)
    engine = _engine_cache.get(key)
    if engine is None:
        engine = create_runtime_engine(db_file)
        _engine_cache[key] = engine
    return engine


@contextmanager
def orm_session(db_file: str) -> Iterator[Session]:
    engine = get_engine(db_file)
    maker = sessionmaker(bind=engine, expire_on_commit=False, future=True)
    session = maker()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


class _DriverResult:
    def __init__(self, result):
        self._result = result
        self.rowcount = getattr(result, "rowcount", 0)

    def fetchone(self):
        row = self._result.mappings().fetchone()
        return row

    def fetchall(self):
        return self._result.mappings().all()


class _RuntimeSqlConnection:
    """Small SQLAlchemy-backed adapter for legacy SQL that is not sqlite3-bound."""

    def __init__(self, db_file: str):
        self._engine = get_engine(db_file)
        self._conn = self._engine.connect()
        self._tx = self._conn.begin()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self._tx.commit()
            else:
                self._tx.rollback()
        finally:
            self._conn.close()
        return False

    @staticmethod
    def _params(params: Any):
        if params is None:
            return ()
        if isinstance(params, list):
            return tuple(params)
        return params

    def execute(self, sql: str, params: Any = None):
        return _DriverResult(self._conn.exec_driver_sql(sql, self._params(params)))

    def commit(self):
        if self._tx.is_active:
            self._tx.commit()
            self._tx = self._conn.begin()


def runtime_sql_connection(db_file: str):
    return _RuntimeSqlConnection(db_file)
