"""Local Skill scanner.

The "market" is the project-local ``skill/`` directory. Each immediate child
folder containing ``SKILL.md`` is exposed as one Skill. A small frontmatter parser
is used intentionally to avoid requiring a marketplace database or YAML runtime.
"""
from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def get_skill_root() -> Path:
    """Use the same configurable root as the Web Panel uploader."""
    import app_config.config as config

    configured = Path(getattr(config, "LOCAL_SKILL_ROOT", "data/skills"))
    if not configured.is_absolute():
        configured = Path(getattr(config, "WORKSPACE_DIR", Path.cwd())) / configured
    return configured.resolve()


def _parse_frontmatter(raw: str) -> tuple[dict[str, Any], str]:
    text = raw.lstrip("﻿")
    if not text.startswith("---"):
        return {}, raw.strip()
    parts = text.split("---", 2)
    if len(parts) < 3:
        return {}, raw.strip()
    meta_text, body = parts[1], parts[2]
    meta: dict[str, Any] = {}
    for line in meta_text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip("\"'")
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            meta[key] = [x.strip().strip("\"'") for x in inner.split(",") if x.strip()]
        else:
            meta[key] = value
    return meta, body.strip()


def _tags_to_text(tags: Any) -> str:
    if isinstance(tags, list):
        return ", ".join(str(x).strip() for x in tags if str(x).strip())
    return str(tags or "").strip()


def _read_skill(path: Path) -> dict | None:
    skill_file = path / "SKILL.md"
    if not skill_file.is_file():
        return None
    try:
        raw = skill_file.read_text(encoding="utf-8")
    except Exception as e:
        logger.warning("读取 Skill 失败: %s | %s", skill_file, e)
        return None
    meta, body = _parse_frontmatter(raw)
    sid = path.name
    title_match = re.search(r"^#\s+(.+)$", body, flags=re.MULTILINE)
    name = str(meta.get("name") or (title_match.group(1).strip() if title_match else sid)).strip()
    description = str(meta.get("description") or "").strip()
    tags = _tags_to_text(meta.get("tags"))
    return {
        "id": sid,
        "name": name,
        "description": description,
        "tags": tags,
        "content": body,
        "path": str(skill_file),
    }


def _scan_skills() -> list[dict]:
    skill_root = get_skill_root()
    if not skill_root.is_dir():
        return []
    rows: list[dict] = []
    for child in sorted(skill_root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir():
            continue
        row = _read_skill(child)
        if row:
            rows.append(row)
    return rows


async def list_market_skills(page: int = 0, page_size: int = 10, keyword: str = "") -> tuple[list[dict], int]:
    """Browse/search local skills, returning (items, total)."""
    rows = _scan_skills()
    kw = (keyword or "").strip().lower()
    if kw:
        rows = [
            r for r in rows
            if kw in str(r.get("name", "")).lower()
            or kw in str(r.get("description", "")).lower()
            or kw in str(r.get("tags", "")).lower()
        ]
    total = len(rows)
    offset = max(0, int(page)) * max(1, int(page_size))
    limit = max(1, int(page_size))
    return [{k: r[k] for k in ("id", "name", "description", "tags")} for r in rows[offset:offset + limit]], total


async def get_skill_content(skill_id: str | int) -> str | None:
    info = await get_skill_info(skill_id)
    return str(info.get("content")) if info else None


async def get_skill_info(skill_id: str | int) -> dict | None:
    sid = str(skill_id).strip()
    if not sid or "/" in sid or "\\" in sid or sid in {".", ".."}:
        return None
    path = get_skill_root() / sid
    row = _read_skill(path)
    if not row:
        return None
    return {k: row[k] for k in ("id", "name", "description", "tags", "content")}


async def get_skills_summary(skill_ids: list[str | int]) -> list[dict]:
    rows: list[dict] = []
    for sid in skill_ids:
        info = await get_skill_info(sid)
        if info:
            rows.append({k: info[k] for k in ("id", "name", "description")})
    return rows
