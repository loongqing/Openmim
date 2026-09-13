"""
用户人格记忆模块。

按 user_id 保存跨对话短人格画像，并维护群内 known_users 索引。
画像只用于让 LLM 更自然地理解当前对话，不暴露真实用户名。

兼容说明：旧版按 chat_id + user_id 存储。初始化时会把旧数据合并到全局 user_id 画像；运行时仍镜像写入旧表，避免现有管理面板和清理入口失效。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime
from difflib import SequenceMatcher
from typing import Any

import httpx

from app.runtime_config import get_shared_runtime_config
from llm.provider_protocols import build_request, normalize_provider, parse_response, provider_endpoint, provider_headers
from stores.memory_store import (
    add_memory,
    list_memories,
)
from stores.group_settings_store import get_group_username_anonymization_enabled

from stores.orm import runtime_sql_connection
from stores.timestamps import cst_now_iso
from app_config.config import (
    DATA_DIR,
    WORKSPACE_DIR,
    LLM_TIMEOUT,
    PERSONA_MEMORY_DB_FILE,
    PERSONA_MEMORY_ENABLED,
    PERSONA_MEMORY_UPDATE_ENABLED,
    PERSONA_MEMORY_MAX_USERS_PER_TURN,
    PERSONA_MEMORY_PROMPT_MAX_CHARS,
    PERSONA_MEMORY_TOTAL_MAX_CHARS,
    PERSONA_MEMORY_FIELD_MAX_CHARS,
    PERSONA_MEMORY_LIST_MAX_ITEMS,
    PERSONA_MEMORY_ITEM_MAX_CHARS,
    PERSONA_MEMORY_UPDATE_MIN_CONFIDENCE,
    PERSONA_MEMORY_UPDATE_INTERVAL_SECONDS,
)

logger = logging.getLogger(__name__)
_RUNTIME_CONFIG = get_shared_runtime_config()
DB_PATH = PERSONA_MEMORY_DB_FILE if os.path.isabs(PERSONA_MEMORY_DB_FILE) else os.path.join(DATA_DIR, PERSONA_MEMORY_DB_FILE)

SENSITIVE_PATTERNS = [
    r"身份证", r"手机号", r"电话", r"住址", r"地址", r"银行卡", r"密码", r"token", r"api\s*key",
    r"secret", r"私钥", r"cookie", r"验证码",
]


@dataclass(frozen=True)
class PersonaUserRef:
    user_id: int
    display_name: str = ""
    anon_label: str = ""
    username: str = ""
    source: str = "context"


def _use_anonymous_labels(chat_id: int) -> bool:
    try:
        return get_group_username_anonymization_enabled(chat_id)
    except Exception:
        return True


def _display_label_for_prompt(ref: PersonaUserRef, chat_id: int) -> str:
    if _use_anonymous_labels(chat_id):
        return ref.anon_label or f"用户_{str(ref.user_id)[-4:]}"
    return ref.display_name or ref.username or ref.anon_label or f"用户_{str(ref.user_id)[-4:]}"


def _sender_label_for_context(cm: Any, chat_id: int) -> str:
    sender = getattr(cm, "sender_name", "未知") or "未知"
    if getattr(cm, "message_type", "text") == "bot":
        return "Bot"
    if not _use_anonymous_labels(chat_id):
        return sender
    uid = getattr(cm, "user_id", None)
    if uid:
        return f"用户_{hashlib.sha256(str(uid).encode()).hexdigest()[:4].upper()}"
    return sender




def _mask_text_for_prompt(text: str, refs: list[PersonaUserRef], chat_id: int) -> str:
    if not text or not _use_anonymous_labels(chat_id):
        return text or ""
    masked = text
    for ref in refs or []:
        label = _display_label_for_prompt(ref, chat_id)
        for raw in (ref.display_name, ref.username and ("@" + ref.username), ref.username):
            raw = (raw or "").strip()
            if raw and raw != label:
                masked = masked.replace(raw, label)
    return masked


def _connect():
    return runtime_sql_connection(DB_PATH)


def init_db() -> None:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    with _connect() as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_persona (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                display_name TEXT DEFAULT '',
                anon_label TEXT DEFAULT '',
                persona_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(chat_id, user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS known_users (
                chat_id INTEGER NOT NULL,
                user_id INTEGER NOT NULL,
                username TEXT DEFAULT '',
                display_name TEXT DEFAULT '',
                anon_label TEXT DEFAULT '',
                updated_at TEXT NOT NULL,
                PRIMARY KEY(chat_id, user_id)
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS user_persona_global (
                user_id INTEGER PRIMARY KEY,
                display_name TEXT DEFAULT '',
                username TEXT DEFAULT '',
                persona_json TEXT NOT NULL DEFAULT '{}',
                updated_at TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_known_users_chat_username ON known_users(chat_id, username)")
        conn.commit()
    _migrate_chat_persona_to_global()
    logger.info("🧬 人格记忆数据库已初始化: %s", DB_PATH)


def _ensure_db() -> None:
    if not os.path.exists(DB_PATH):
        init_db()


def upsert_known_user(chat_id: int, user_id: int | None, display_name: str = "", anon_label: str = "", username: str = "") -> None:
    if not PERSONA_MEMORY_ENABLED or not user_id:
        return
    _ensure_db()
    username = (username or "").lstrip("@").strip()
    display_name = (display_name or "").strip()
    anon_label = (anon_label or "").strip()

    # 止血：不要把已脱敏标签写回 display_name，避免污染真实显示名字段。
    if display_name.startswith("用户_") and (anon_label == display_name or not anon_label):
        display_name = ""

    now = cst_now_iso()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO known_users(chat_id, user_id, username, display_name, anon_label, updated_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                username=COALESCE(NULLIF(excluded.username, ''), known_users.username),
                display_name=COALESCE(NULLIF(excluded.display_name, ''), known_users.display_name),
                anon_label=COALESCE(NULLIF(excluded.anon_label, ''), known_users.anon_label),
                updated_at=excluded.updated_at
            """,
            (chat_id, int(user_id), username, display_name, anon_label, now),
        )
        conn.commit()


def _empty_persona(persona: dict[str, Any] | None) -> bool:
    data = persona or {}
    return not any(data.get(k) for k in ("style", "traits", "preferences", "boundaries", "memorable"))


def _migrate_chat_persona_to_global() -> None:
    try:
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT p.user_id, p.display_name, p.persona_json, p.updated_at, p.created_at,
                       COALESCE(k.username, '') AS username
                FROM user_persona p
                LEFT JOIN known_users k ON k.chat_id=p.chat_id AND k.user_id=p.user_id
                ORDER BY p.user_id ASC, p.updated_at ASC
                """
            ).fetchall()
            if not rows:
                return
            by_user: dict[int, dict[str, Any]] = {}
            for row in rows:
                uid = int(row["user_id"])
                try:
                    patch = json.loads(row["persona_json"] or "{}")
                    patch = patch if isinstance(patch, dict) else {}
                except Exception:
                    patch = {}
                item = by_user.setdefault(uid, {"persona": {}, "display_name": "", "username": "", "created_at": row["created_at"] or row["updated_at"] or cst_now_iso(), "updated_at": row["updated_at"] or cst_now_iso()})
                item["persona"] = merge_persona(item["persona"], patch)
                if row["display_name"]:
                    item["display_name"] = row["display_name"]
                if row["username"]:
                    item["username"] = row["username"]
                if row["updated_at"]:
                    item["updated_at"] = row["updated_at"]
            for uid, item in by_user.items():
                persona = compact_persona(item["persona"])
                if _empty_persona(persona):
                    continue
                existing = conn.execute("SELECT persona_json, display_name, username, created_at FROM user_persona_global WHERE user_id=?", (uid,)).fetchone()
                display_name = item["display_name"]
                username = item["username"]
                created_at = item["created_at"]
                if existing:
                    try:
                        old = json.loads(existing["persona_json"] or "{}")
                        old = old if isinstance(old, dict) else {}
                    except Exception:
                        old = {}
                    persona = merge_persona(old, persona)
                    display_name = display_name or existing["display_name"] or ""
                    username = username or existing["username"] or ""
                    created_at = existing["created_at"] or created_at
                conn.execute(
                    """
                    INSERT INTO user_persona_global(user_id, display_name, username, persona_json, updated_at, created_at)
                    VALUES(?, ?, ?, ?, ?, ?)
                    ON CONFLICT(user_id) DO UPDATE SET
                        display_name=excluded.display_name,
                        username=excluded.username,
                        persona_json=excluded.persona_json,
                        updated_at=excluded.updated_at
                    """,
                    (uid, display_name or "", username or "", json.dumps(persona, ensure_ascii=False, separators=(",", ":")), item["updated_at"] or cst_now_iso(), created_at or cst_now_iso()),
                )
            conn.commit()
    except Exception:
        logger.exception("迁移人格记忆到全局画像失败")


def get_global_persona(user_id: int) -> dict[str, Any]:
    _ensure_db()
    with _connect() as conn:
        row = conn.execute("SELECT persona_json FROM user_persona_global WHERE user_id=?", (int(user_id),)).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row["persona_json"] or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_global_persona(ref: PersonaUserRef, persona: dict[str, Any]) -> None:
    if not PERSONA_MEMORY_ENABLED or not ref.user_id:
        return
    _ensure_db()
    persona = compact_persona(persona)
    if _empty_persona(persona):
        return
    now = cst_now_iso()
    raw = json.dumps(persona, ensure_ascii=False, separators=(",", ":"))
    username = (ref.username or "").lstrip("@").strip()
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_persona_global(user_id, display_name, username, persona_json, updated_at, created_at)
            VALUES(?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                display_name=COALESCE(NULLIF(excluded.display_name, ''), user_persona_global.display_name),
                username=COALESCE(NULLIF(excluded.username, ''), user_persona_global.username),
                persona_json=excluded.persona_json,
                updated_at=excluded.updated_at
            """,
            (int(ref.user_id), ref.display_name or "", username, raw, now, now),
        )
        conn.commit()


def get_known_user(chat_id: int, user_id: int) -> dict[str, Any] | None:
    _ensure_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT * FROM known_users WHERE chat_id=? AND user_id=?",
            (chat_id, int(user_id)),
        ).fetchone()
    return dict(row) if row else None


def fuzzy_lookup_users(chat_id: int, query: str, limit: int = 5) -> list[dict[str, Any]]:
    """按 username/display_name/anon_label 模糊查群内已知用户。"""
    if not PERSONA_MEMORY_ENABLED:
        return []
    _ensure_db()
    q = (query or "").strip().lstrip("@").lower()
    if not q:
        return []
    with _connect() as conn:
        rows = conn.execute(
            "SELECT user_id, username, display_name, anon_label, updated_at FROM known_users WHERE chat_id=?",
            (chat_id,),
        ).fetchall()
    scored = []
    for row in rows:
        candidates = [
            str(row["username"] or "").lower(),
            str(row["display_name"] or "").lower(),
            str(row["anon_label"] or "").lower(),
        ]
        best = 0.0
        for c in candidates:
            if not c:
                continue
            if c == q:
                best = max(best, 1.0)
            elif q in c or c in q:
                best = max(best, 0.82)
            else:
                best = max(best, SequenceMatcher(None, q, c).ratio())
        if best >= 0.35:
            scored.append((best, row))
    scored.sort(key=lambda x: x[0], reverse=True)
    results = []
    for score, row in scored[: max(1, min(limit, 10))]:
        results.append({
            "user_id": int(row["user_id"]),
            "username": row["username"] or "",
            "display_name": row["display_name"] or "",
            "anon_label": row["anon_label"] or "",
            "score": round(score, 3),
            "updated_at": row["updated_at"] or "",
        })
    return results




def anonymize_text_by_known_users(chat_id: int, text: str) -> str:
    """Replace known real display names/usernames in text with anon labels.

    Used when old Bot replies were stored after de-anonymization, but the next
    LLM prompt should respect the group's anonymization setting.
    """
    if not text:
        return text or ""
    try:
        _ensure_db()
        with _connect() as conn:
            rows = conn.execute(
                """
                SELECT display_name, username, anon_label FROM known_users
                WHERE chat_id=? AND COALESCE(anon_label, '') != ''
                """,
                (int(chat_id),),
            ).fetchall()
    except Exception:
        return text

    out = text
    replacements: list[tuple[str, str]] = []
    for r in rows:
        anon = str(r["anon_label"] or "").strip()
        if not anon:
            continue
        display_name = str(r["display_name"] or "").strip()
        username = str(r["username"] or "").strip()
        for raw in (display_name, username, f"@{username}" if username else ""):
            raw = (raw or "").strip()
            if raw and raw != anon and not raw.startswith("用户_"):
                replacements.append((raw, anon))
    for raw, anon in sorted(set(replacements), key=lambda x: len(x[0]), reverse=True):
        out = out.replace(raw, anon)
    return out

def lookup_display_name_by_anon(chat_id: int, anon_label: str) -> str:
    """Return best known display name for an anonymized label in a chat."""
    label = (anon_label or "").strip()
    if not label:
        return ""
    _ensure_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT display_name FROM known_users
            WHERE chat_id=? AND anon_label=? AND COALESCE(display_name, '') != ''
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (int(chat_id), label),
        ).fetchone()
        if row and row["display_name"]:
            return row["display_name"]
        row = conn.execute(
            """
            SELECT display_name FROM user_persona
            WHERE chat_id=? AND anon_label=? AND COALESCE(display_name, '') != ''
            ORDER BY updated_at DESC
            LIMIT 1
            """,
            (int(chat_id), label),
        ).fetchone()
    return row["display_name"] if row and row["display_name"] else ""

def get_persona(chat_id: int, user_id: int) -> dict[str, Any]:
    global_persona = compact_persona(get_global_persona(int(user_id)))
    if not _empty_persona(global_persona):
        return global_persona
    _ensure_db()
    with _connect() as conn:
        row = conn.execute(
            "SELECT persona_json FROM user_persona WHERE chat_id=? AND user_id=?",
            (chat_id, int(user_id)),
        ).fetchone()
    if not row:
        return {}
    try:
        data = json.loads(row["persona_json"] or "{}")
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def save_persona(chat_id: int, ref: PersonaUserRef, persona: dict[str, Any]) -> None:
    if not PERSONA_MEMORY_ENABLED or not ref.user_id:
        return
    _ensure_db()
    persona = compact_persona(persona)
    if _empty_persona(persona):
        return
    save_global_persona(ref, persona)
    now = cst_now_iso()
    raw = json.dumps(persona, ensure_ascii=False, separators=(",", ":"))
    with _connect() as conn:
        conn.execute(
            """
            INSERT INTO user_persona(chat_id, user_id, display_name, anon_label, persona_json, updated_at, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(chat_id, user_id) DO UPDATE SET
                display_name=excluded.display_name,
                anon_label=excluded.anon_label,
                persona_json=excluded.persona_json,
                updated_at=excluded.updated_at
            """,
            (chat_id, int(ref.user_id), ref.display_name or "", ref.anon_label or "", raw, now, now),
        )
        conn.commit()


def _has_sensitive(text: str) -> bool:
    low = (text or "").lower()
    return any(re.search(p, low, flags=re.I) for p in SENSITIVE_PATTERNS)


def _clean_text(value: Any, max_chars: int) -> str:
    s = str(value or "").strip()
    s = re.sub(r"\s+", " ", s)
    if _has_sensitive(s):
        return ""
    return s[:max_chars]


def _clean_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, str):
        items = [value]
    elif isinstance(value, list):
        items = value
    else:
        return []
    out: list[str] = []
    seen = set()
    for item in items:
        s = _clean_text(item, PERSONA_MEMORY_ITEM_MAX_CHARS)
        key = s.lower()
        if s and key not in seen:
            seen.add(key)
            out.append(s)
        if len(out) >= PERSONA_MEMORY_LIST_MAX_ITEMS:
            break
    return out


def compact_persona(persona: dict[str, Any]) -> dict[str, Any]:
    data = {
        "style": _clean_text(persona.get("style", ""), PERSONA_MEMORY_FIELD_MAX_CHARS),
        "traits": _clean_list(persona.get("traits", [])),
        "preferences": _clean_list(persona.get("preferences", [])),
        "boundaries": _clean_list(persona.get("boundaries", [])),
        "memorable": _clean_list(persona.get("memorable", [])),
    }
    # 硬控总长：优先保留偏好/边界/风格，再保留特征和印象。
    while len(json.dumps(data, ensure_ascii=False)) > PERSONA_MEMORY_TOTAL_MAX_CHARS:
        if data["memorable"]:
            data["memorable"].pop()
        elif data["traits"]:
            data["traits"].pop()
        elif data["preferences"]:
            data["preferences"].pop()
        elif data["boundaries"]:
            data["boundaries"].pop()
        elif data["style"]:
            data["style"] = data["style"][: max(20, len(data["style"]) - 20)]
        else:
            break
    return data


def _merge_lists(old: list[str], new: list[str]) -> list[str]:
    merged: list[str] = []
    for item in list(old or []) + list(new or []):
        s = _clean_text(item, PERSONA_MEMORY_ITEM_MAX_CHARS)
        if not s:
            continue
        if any(s == x or s in x or x in s for x in merged):
            continue
        merged.append(s)
        if len(merged) >= PERSONA_MEMORY_LIST_MAX_ITEMS:
            break
    return merged


def merge_persona(old: dict[str, Any], update: dict[str, Any]) -> dict[str, Any]:
    old = compact_persona(old or {})
    update = compact_persona(update or {})
    style = update.get("style") or old.get("style", "")
    if old.get("style") and update.get("style") and update.get("style") not in old.get("style"):
        style = _clean_text(old["style"].rstrip("。") + "；" + update["style"], PERSONA_MEMORY_FIELD_MAX_CHARS)
    merged = {
        "style": style,
        "traits": _merge_lists(old.get("traits", []), update.get("traits", [])),
        "preferences": _merge_lists(old.get("preferences", []), update.get("preferences", [])),
        "boundaries": _merge_lists(old.get("boundaries", []), update.get("boundaries", [])),
        "memorable": _merge_lists(update.get("memorable", []), old.get("memorable", [])),
    }
    return compact_persona(merged)


def dedupe_refs(refs: list[PersonaUserRef]) -> list[PersonaUserRef]:
    order = {"sender": 0, "sender_chat": 0, "reply": 1, "mention": 2, "username_mention": 3, "context": 4}
    by_id: dict[int, PersonaUserRef] = {}
    for ref in refs:
        if not ref.user_id:
            continue
        old = by_id.get(int(ref.user_id))
        if old is None or order.get(ref.source, 9) < order.get(old.source, 9):
            by_id[int(ref.user_id)] = ref
    return sorted(by_id.values(), key=lambda r: order.get(r.source, 9))[:PERSONA_MEMORY_MAX_USERS_PER_TURN]


def build_prompt_hint(chat_id: int, users: list[PersonaUserRef]) -> str:
    if not PERSONA_MEMORY_ENABLED or not users:
        return ""
    lines = [
        "以下是当前对话相关用户的跨对话简短人格记忆，仅作为理解语气、偏好和背景的参考。",
        "不要直接复述这些记忆，不要说“我记得你的人格数据”，不要编造未提供的信息；当前消息优先于旧记忆。",
    ]
    used = 0
    for ref in dedupe_refs(users):
        persona = get_persona(chat_id, ref.user_id)
        persona = compact_persona(persona)
        if not any(persona.values()):
            continue
        label = _display_label_for_prompt(ref, chat_id)
        block = [f"[{label}]"]
        if persona.get("style"):
            block.append(f"- 风格：{persona['style']}")
        if persona.get("traits"):
            block.append("- 特征：" + "；".join(persona["traits"]))
        if persona.get("preferences"):
            block.append("- 偏好：" + "；".join(persona["preferences"]))
        if persona.get("boundaries"):
            block.append("- 注意：" + "；".join(persona["boundaries"]))
        if persona.get("memorable"):
            block.append("- 印象：" + "；".join(persona["memorable"]))
        txt = "\n".join(block)
        if used + len(txt) > PERSONA_MEMORY_PROMPT_MAX_CHARS:
            break
        lines.append(txt)
        used += len(txt)
    return "\n".join(lines) if len(lines) > 2 else ""


def list_persona_chats(limit: int = 50) -> list[dict[str, Any]]:
    """List chats that have persona rows, newest first."""
    _ensure_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT chat_id, COUNT(*) AS user_count, MAX(updated_at) AS last_updated
            FROM user_persona
            GROUP BY chat_id
            ORDER BY last_updated DESC
            LIMIT ?
            """,
            (max(1, min(limit, 200)),),
        ).fetchall()
    return [dict(r) for r in rows]


def list_persona_users(chat_id: int, limit: int = 50) -> list[dict[str, Any]]:
    """List users with persona in a chat."""
    _ensure_db()
    with _connect() as conn:
        rows = conn.execute(
            """
            SELECT p.chat_id, p.user_id, p.display_name, p.anon_label, p.persona_json, p.updated_at,
                   k.username
            FROM user_persona p
            LEFT JOIN known_users k ON k.chat_id=p.chat_id AND k.user_id=p.user_id
            WHERE p.chat_id=?
            ORDER BY p.updated_at DESC
            LIMIT ?
            """,
            (int(chat_id), max(1, min(limit, 200))),
        ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["persona"] = json.loads(d.pop("persona_json") or "{}")
        except Exception:
            d["persona"] = {}
        out.append(d)
    return out


def get_persona_row(chat_id: int, user_id: int) -> dict[str, Any] | None:
    """Return one persona row with known user metadata."""
    _ensure_db()
    with _connect() as conn:
        row = conn.execute(
            """
            SELECT p.chat_id, p.user_id, p.display_name, p.anon_label, p.persona_json, p.updated_at,
                   k.username
            FROM user_persona p
            LEFT JOIN known_users k ON k.chat_id=p.chat_id AND k.user_id=p.user_id
            WHERE p.chat_id=? AND p.user_id=?
            """,
            (int(chat_id), int(user_id)),
        ).fetchone()
    if not row:
        return None
    d = dict(row)
    try:
        d["persona"] = json.loads(d.pop("persona_json") or "{}")
    except Exception:
        d["persona"] = {}
    return d


def delete_persona(chat_id: int, user_id: int) -> bool:
    """Delete one user's persona row. known_users is kept for mention lookup."""
    _ensure_db()
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM user_persona WHERE chat_id=? AND user_id=?",
            (int(chat_id), int(user_id)),
        )
        conn.commit()
        return cur.rowcount > 0


def _extract_json_object(text: str) -> dict[str, Any]:
    t = (text or "").strip()
    if t.startswith("```"):
        t = re.sub(r"^```(?:json)?\s*", "", t)
        t = re.sub(r"\s*```$", "", t)
    try:
        data = json.loads(t)
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{.*\}", t, flags=re.S)
    if not m:
        return {}
    try:
        data = json.loads(m.group(0))
        return data if isinstance(data, dict) else {}
    except json.JSONDecodeError:
        return {}


def _format_context_lines(context_messages: list[Any], limit: int = 30, chat_id: int | None = None) -> str:
    lines: list[str] = []
    for cm in context_messages[-limit:]:
        sender = _sender_label_for_context(cm, chat_id) if chat_id is not None else getattr(cm, "sender_name", "未知")
        text = getattr(cm, "text", "") or getattr(cm, "caption", "") or (f"[贴纸 {getattr(cm, 'emoji', '')}]" if getattr(cm, "emoji", "") else "")
        text = str(text).strip()
        if text:
            lines.append(f"[{sender}] {text[:300]}")
    return "\n".join(lines)[-5000:]


def _should_skip_update_text(text: str) -> bool:
    t = (text or "").strip()
    if not t:
        return True
    if len(t) < 4 and not re.search(r"记住|以后|不要|喜欢|讨厌|偏好|项目|风格", t):
        return True
    return False


_update_locks: dict[tuple[int, int], float] = {}


def _cooldown_ok(chat_id: int, user_id: int) -> bool:
    now_ts = datetime.now().timestamp()
    if len(_update_locks) > 1000:
        cutoff = now_ts - max(1, PERSONA_MEMORY_UPDATE_INTERVAL_SECONDS)
        for stale_key, last_seen in list(_update_locks.items()):
            if last_seen < cutoff:
                _update_locks.pop(stale_key, None)
    key = (int(chat_id), int(user_id))
    last = _update_locks.get(key, 0)
    if now_ts - last < PERSONA_MEMORY_UPDATE_INTERVAL_SECONDS:
        return False
    _update_locks[key] = now_ts
    return True


async def update_persona_after_turn(
    chat_id: int,
    users: list[PersonaUserRef],
    context_messages: list[Any],
    current_message: str,
    bot_reply: str,
    allow_memory_write: bool = False,
) -> None:
    """一轮对话结束后异步分析并更新相关用户的人格记忆。"""
    if not (PERSONA_MEMORY_ENABLED and PERSONA_MEMORY_UPDATE_ENABLED):
        return
    users = [u for u in dedupe_refs(users) if _cooldown_ok(chat_id, u.user_id)]
    if (not users and not allow_memory_write) or _should_skip_update_text((current_message or "") + " " + (bot_reply or "")):
        return

    use_anon = _use_anonymous_labels(chat_id)
    existing = []
    label_to_ref: dict[str, PersonaUserRef] = {}
    for ref in users:
        label = _display_label_for_prompt(ref, chat_id)
        label_to_ref[label] = ref
        # 兼容模型返回匿名标签/真实名中的另一种形式。
        if ref.anon_label:
            label_to_ref.setdefault(ref.anon_label, ref)
        if ref.display_name:
            label_to_ref.setdefault(ref.display_name, ref)
        existing.append({"label": label, "persona": compact_persona(get_persona(chat_id, ref.user_id))})

    prompt = f"""你是用户人格与长期记忆维护器。请根据这轮群聊内容，判断哪些用户的人格记忆需要更新。

当前群用户名显示策略：{"脱敏，必须使用用户_XXXX 标签，不要输出真实用户名" if use_anon else "未脱敏，可以使用上下文中的真实显示名"}。

只记录稳定、明确、有复用价值的信息，例如：对话风格、明确偏好、明确纠正、长期项目/重要背景、印象深刻且以后可能有用的事。
不要记录一次性闲聊、敏感隐私、手机号/地址/token/密码等、临时情绪、贬低判断、没有依据的猜测。
每个字段必须很短；每个字段不超过 50 字；每个列表最多 5 条。
如果本轮主回复没有调用记忆工具，你还可以在 memories 中补充应保存的短记忆：scope=chat 保存本群约定/项目/偏好；scope=global 保存偷偷学到的常用词、称呼习惯、偏好句式。广告导流、推广链接、违法违规、仇恨歧视、色情诈骗、引战内容禁止保存。
如果没有任何更新就返回 {{"updates": [], "memories": []}}。
只输出 JSON，不要解释。

可更新用户及已有记忆：
{json.dumps(existing, ensure_ascii=False)}

最近上下文：
{_format_context_lines(context_messages, chat_id=chat_id)}

本轮当前消息：
{_mask_text_for_prompt(current_message, users, chat_id)[:1200]}

Bot 本轮回复：
{_mask_text_for_prompt(bot_reply, users, chat_id)[:1200]}

输出格式：
{{"updates":[{{"user_label":"用户_ABCD","style":"","traits":[],"preferences":[],"boundaries":[],"memorable":[],"confidence":0.0}}],"memories":[{{"scope":"global","key":"phrase","content":"简短记忆","confidence":0.0}}]}}

本轮是否允许补写长期记忆：{str(bool(allow_memory_write)).lower()}
"""

    try:
        llm_cfg = _RUNTIME_CONFIG.get_effective_llm(chat_id)
        provider = normalize_provider(llm_cfg.provider)
        async with httpx.AsyncClient(
            base_url=llm_cfg.api_base,
            timeout=httpx.Timeout(min(LLM_TIMEOUT, 60)),
            headers=provider_headers(provider, llm_cfg.api_key),
        ) as client:
            resp = await client.post(
                provider_endpoint(provider),
                json=build_request(
                    provider,
                    model=llm_cfg.model,
                    messages=[{"role": "user", "content": prompt}],
                    temperature=0.1,
                    max_tokens=700,
                    stream=False,
                ),
            )
            resp.raise_for_status()
            content = parse_response(provider, resp.json()).text
    except Exception as e:
        logger.warning("人格记忆更新 LLM 调用失败: %s", e)
        return

    data = _extract_json_object(content)
    updates = data.get("updates", []) if isinstance(data, dict) else []
    memories = data.get("memories", []) if isinstance(data, dict) else []

    changed = 0
    for upd in updates:
        if not isinstance(upd, dict):
            continue
        try:
            conf = float(upd.get("confidence", 0))
        except Exception:
            conf = 0.0
        if conf < PERSONA_MEMORY_UPDATE_MIN_CONFIDENCE:
            continue
        label = str(upd.get("user_label", "")).strip()
        ref = label_to_ref.get(label)
        if not ref:
            # 兼容模型漏填 label 精确值时，根据唯一用户兜底。
            if len(users) == 1:
                ref = users[0]
            else:
                continue
        patch = compact_persona(upd)
        if not any(patch.values()):
            continue
        old = get_persona(chat_id, ref.user_id)
        merged = merge_persona(old, patch)
        save_persona(chat_id, ref, merged)
        changed += 1

    memory_added = 0
    if allow_memory_write:
        for mem in memories[:3]:
            if not isinstance(mem, dict):
                continue
            try:
                conf = float(mem.get("confidence", 0))
            except Exception:
                conf = 0.0
            if conf < 0.75:
                continue
            scope = str(mem.get("scope") or "chat").strip().lower()
            if scope not in ("chat", "global"):
                scope = "chat"
            key = str(mem.get("key") or "").strip()[:60]
            value = str(mem.get("content") or "").strip()
            if not value:
                continue
            if len(value) > (120 if scope == "global" else 300):
                value = value[:120 if scope == "global" else 300]
            lowered = value.lower()
            if any(x in lowered for x in ("http://", "https://", "t.me/", "广告", "推广", "返利", "邀请码", "赌博", "博彩", "色情", "诈骗", "仇恨", "歧视")):
                continue
            existing_rows = list_memories(scope=scope, chat_id=str(chat_id) if scope == "chat" else None, include_inactive=False, limit=200)
            if any(value.lower() in str(r.get("value") or "").lower() for r in existing_rows):
                continue
            add_memory(
                scope=scope,
                value=value,
                key=key,
                chat_id=str(chat_id) if scope == "chat" else None,
                source="llm_evolution" if scope == "global" else "llm_persona_after_turn",
            )
            memory_added += 1


    if changed:
        logger.info("🧬 跨对话人格记忆已更新 | chat=%s | users=%s", chat_id, changed)
    if memory_added:
        logger.info("🧠 人格更新阶段补写长期记忆 | chat=%s | memories=%s", chat_id, memory_added)
