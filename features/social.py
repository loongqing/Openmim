"""社交功能 — 好感度。"""
import logging

from app_config.customization import get_list
from stores.playables_db import DB_PATH, AffinityRow, orm_session
from stores.timestamps import utc_now_iso

logger = logging.getLogger(__name__)

AFFINITY_INTERACT = 1
AFFINITY_REPLY_BOT = 2
AFFINITY_MENTION_BOT = 3

AFFINITY_LEVELS = [
    (-999, "💔 陌路人", 0.6),
    (-10, "😒 有点嫌弃", 0.7),
    (0, "😐 普通群友", 0.9),
    (15, "🙂 面熟了", 1.0),
    (40, "😊 朋友", 1.1),
    (80, "😄 好朋友", 1.2),
    (150, "🥰 亲密无间", 1.3),
    (300, "💖 挚友", 1.5),
]


def get_affinity(chat_id: str, user_id: str) -> int:
    with orm_session(DB_PATH) as session:
        row = session.get(AffinityRow, (str(chat_id), str(user_id)))
        return int(row.score) if row else 0


def _affinity_levels() -> list[tuple[int, str, float]]:
    custom = get_list("social.levels", [])
    levels = []
    for item in custom:
        if isinstance(item, dict):
            try:
                levels.append((int(item.get("threshold", 0)), str(item.get("label", "")), float(item.get("multiplier", 1.0))))
            except Exception:
                continue
    return levels or AFFINITY_LEVELS


def get_affinity_level(score: int) -> tuple[str, float]:
    levels = _affinity_levels()
    for threshold, label, multiplier in reversed(levels):
        if score >= threshold:
            return label, multiplier
    return levels[0][1], levels[0][2]


def _ensure_user_exists(chat_id: str, user_id: str):
    with orm_session(DB_PATH) as session:
        row = session.get(AffinityRow, (str(chat_id), str(user_id)))
        if row is None:
            now = utc_now_iso()
            session.add(AffinityRow(
                chat_id=str(chat_id),
                user_id=str(user_id),
                score=0,
                total_interactions=0,
                last_interaction_at=now,
                first_interaction_at=now,
            ))
            return 0
        return int(row.score)


def modify_affinity(chat_id: str, user_id: str, delta: int, reason: str = ""):
    _ensure_user_exists(chat_id, user_id)
    now = utc_now_iso()
    with orm_session(DB_PATH) as session:
        row = session.get(AffinityRow, (str(chat_id), str(user_id)))
        if row is not None:
            row.score = int(row.score) + int(delta)
            row.total_interactions = int(row.total_interactions) + 1
            row.last_interaction_at = now
            new_score = int(row.score)
        else:
            new_score = 0
    logger.info(f"💖 好感度变更: chat={chat_id} user={user_id} delta={delta} reason={reason}")
    return new_score


def track_interaction(chat_id: str, user_id: str, interaction_type: str = "message"):
    if interaction_type == "reply":
        delta = AFFINITY_REPLY_BOT
    elif interaction_type == "mention":
        delta = AFFINITY_MENTION_BOT
    else:
        delta = AFFINITY_INTERACT
    return modify_affinity(chat_id, user_id, delta, interaction_type)
