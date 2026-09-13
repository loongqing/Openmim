"""LLM token usage persistence and aggregation."""
from __future__ import annotations

import os
from datetime import datetime, timezone, timedelta
from threading import Lock
from typing import Any

from sqlalchemy import func, select

from app_config.config import DATA_DIR
from stores.orm import TokenUsageEvent, orm_session
from stores.timestamps import utc_now_iso

DB_PATH = os.path.join(DATA_DIR, "token_usage.sqlite3")
_CST = timezone(timedelta(hours=8))
_lock = Lock()


def _today_cst() -> str:
    return datetime.now(_CST).date().isoformat()


def _to_int(value: Any) -> int:
    try:
        if value is None:
            return 0
        return max(0, int(value))
    except (TypeError, ValueError):
        return 0


def init_db() -> None:
    with orm_session(DB_PATH):
        pass


def _normalized_usage(usage: dict[str, Any] | None) -> dict[str, int]:
    usage = usage or {}
    prompt_tokens = _to_int(usage.get("prompt_tokens") or usage.get("input_tokens"))
    completion_tokens = _to_int(usage.get("completion_tokens") or usage.get("output_tokens"))
    total_tokens = _to_int(usage.get("total_tokens"))

    prompt_details = usage.get("prompt_tokens_details") or usage.get("input_tokens_details") or {}
    if not isinstance(prompt_details, dict):
        prompt_details = {}
    cached_prompt_tokens = _to_int(
        prompt_details.get("cached_tokens")
        or prompt_details.get("cache_read_tokens")
        or usage.get("cached_prompt_tokens")
        or usage.get("input_cached_tokens")
        or usage.get("cache_read_input_tokens")
    )

    if total_tokens <= 0:
        total_tokens = prompt_tokens + completion_tokens
    return {
        "request_count": 1,
        "prompt_tokens": prompt_tokens,
        "completion_tokens": completion_tokens,
        "total_tokens": total_tokens,
        "cached_prompt_tokens": min(cached_prompt_tokens, prompt_tokens) if prompt_tokens > 0 else 0,
    }


def record_usage(model: str, usage: dict[str, Any] | None) -> bool:
    model = (model or "unknown").strip() or "unknown"
    normalized = _normalized_usage(usage)
    if normalized["prompt_tokens"] <= 0 and normalized["completion_tokens"] <= 0 and normalized["total_tokens"] <= 0:
        return False
    init_db()
    with _lock:
        with orm_session(DB_PATH) as session:
            session.add(TokenUsageEvent(
                model=model,
                request_date=_today_cst(),
                request_count=normalized["request_count"],
                prompt_tokens=normalized["prompt_tokens"],
                completion_tokens=normalized["completion_tokens"],
                total_tokens=normalized["total_tokens"],
                cached_prompt_tokens=normalized["cached_prompt_tokens"],
                created_at=utc_now_iso(),
            ))
    return True


def _sum_row(*conditions) -> dict[str, int]:
    init_db()
    stmt = select(
        func.coalesce(func.sum(TokenUsageEvent.request_count), 0),
        func.coalesce(func.sum(TokenUsageEvent.prompt_tokens), 0),
        func.coalesce(func.sum(TokenUsageEvent.completion_tokens), 0),
        func.coalesce(func.sum(TokenUsageEvent.total_tokens), 0),
        func.coalesce(func.sum(TokenUsageEvent.cached_prompt_tokens), 0),
    )
    if conditions:
        stmt = stmt.where(*conditions)
    with orm_session(DB_PATH) as session:
        row = session.execute(stmt).one()
    return {
        "request_count": _to_int(row[0]),
        "prompt_tokens": _to_int(row[1]),
        "completion_tokens": _to_int(row[2]),
        "total_tokens": _to_int(row[3]),
        "cached_prompt_tokens": _to_int(row[4]),
    }


def _with_rate(data: dict[str, int]) -> dict[str, float | int]:
    prompt_tokens = _to_int(data.get("prompt_tokens"))
    cached_prompt_tokens = _to_int(data.get("cached_prompt_tokens"))
    uncached_prompt_tokens = max(prompt_tokens - cached_prompt_tokens, 0)
    cache_rate = (cached_prompt_tokens / prompt_tokens) if prompt_tokens > 0 else 0.0
    return {
        **data,
        "uncached_prompt_tokens": uncached_prompt_tokens,
        "cache_rate": cache_rate,
    }


def get_usage_summary(active_model: str | None = None) -> dict[str, Any]:
    today = _today_cst()
    overall = _with_rate(_sum_row())
    today_all = _with_rate(_sum_row(TokenUsageEvent.request_date == today))

    active_model = (active_model or "").strip()
    model_all = _with_rate(_sum_row(TokenUsageEvent.model == active_model)) if active_model else _with_rate(_sum_row(TokenUsageEvent.id < 0))
    model_today = _with_rate(_sum_row(TokenUsageEvent.request_date == today, TokenUsageEvent.model == active_model)) if active_model else _with_rate(_sum_row(TokenUsageEvent.id < 0))

    return {
        "today": today,
        "overall": overall,
        "today_all": today_all,
        "active_model": active_model,
        "model_all": model_all,
        "model_today": model_today,
    }
