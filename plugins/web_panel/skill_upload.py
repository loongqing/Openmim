"""Skill upload & install helpers for the Web Panel.

Supports:
  1. A single ``SKILL.md`` file (markdown + YAML frontmatter).
  2. A ``.zip`` containing exactly one skill folder with ``SKILL.md``,
     or with ``SKILL.md`` at the archive root.

Validation: frontmatter ``name`` + ``description``, name regex, zip entry
path traversal, symlink rejection, max upload size, explicit overwrite.
Uploaded code is **never executed** — installing a Skill only writes
markdown/assets.
"""
from __future__ import annotations

import io
import os
import re
import shutil
import tempfile
import zipfile
from pathlib import Path

import yaml

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{1,63}$")
MAX_ZIP_ENTRIES = 256
MAX_ZIP_FILE_BYTES = 5 * 1024 * 1024
MAX_ZIP_TOTAL_BYTES = 20 * 1024 * 1024
MAX_ZIP_COMPRESSION_RATIO = 200


def parse_skill_frontmatter(text: str) -> dict:
    """Parse YAML frontmatter from a SKILL.md string."""
    if not text.startswith("---"):
        raise ValueError("SKILL.md must start with YAML frontmatter (---)")
    end = text.find("\n---", 3)
    if end == -1:
        raise ValueError("SKILL.md frontmatter not closed")
    fm_text = text[3:end].strip()
    try:
        data = yaml.safe_load(fm_text) or {}
    except yaml.YAMLError as exc:
        raise ValueError(f"frontmatter is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ValueError("frontmatter must be a YAML mapping")
    return data


def validate_skill_name(name: str) -> str:
    name = str(name or "").strip().lower()
    if not _SKILL_NAME_RE.match(name):
        raise ValueError("invalid skill name (must match ^[a-z0-9][a-z0-9_-]{1,63}$)")
    return name


def validate_zip_entries(zip_source: Path | io.BytesIO) -> None:
    """Reject unsafe paths, links and zip bombs before extracting an archive."""
    with zipfile.ZipFile(zip_source, "r") as zipf:
        entries = zipf.infolist()
        if len(entries) > MAX_ZIP_ENTRIES:
            raise ValueError(f"zip has too many entries (>{MAX_ZIP_ENTRIES})")
        total_size = 0
        for info in entries:
            name = info.filename
            normalized = name.replace("\\", "/")
            if normalized.startswith("/") or re.match(r"^[A-Za-z]:", normalized) or ".." in Path(normalized).parts:
                raise ValueError(f"unsafe zip entry: {name}")
            # Symlink attribute (Unix mode bits in external_attr high bits).
            mode = (info.external_attr >> 16) & 0o170000
            if mode == 0o120000:
                raise ValueError(f"symlinks are not allowed in skill zip: {name}")
            if info.is_dir():
                continue
            if info.file_size > MAX_ZIP_FILE_BYTES:
                raise ValueError(f"zip entry too large: {name}")
            total_size += info.file_size
            if total_size > MAX_ZIP_TOTAL_BYTES:
                raise ValueError(f"zip expands beyond {MAX_ZIP_TOTAL_BYTES} bytes")
            if info.file_size and (
                info.compress_size == 0
                or info.file_size / info.compress_size > MAX_ZIP_COMPRESSION_RATIO
            ):
                raise ValueError(f"suspicious compression ratio: {name}")


def _replace_directory(staging_dir: Path, target_dir: Path, *, overwrite: bool) -> None:
    """Promote a complete staging directory, restoring the old version on failure."""
    if target_dir.exists() and not overwrite:
        raise ValueError(f"skill '{target_dir.name}' already exists; pass overwrite=true")

    backup_dir = target_dir.parent / f".{target_dir.name}.backup"
    if backup_dir.exists():
        raise RuntimeError(f"stale skill backup exists: {backup_dir}")
    moved_old = False
    try:
        if target_dir.exists():
            os.replace(target_dir, backup_dir)
            moved_old = True
        os.replace(staging_dir, target_dir)
    except Exception:
        if moved_old and not target_dir.exists() and backup_dir.exists():
            os.replace(backup_dir, target_dir)
        raise
    else:
        if moved_old:
            shutil.rmtree(backup_dir)


def install_skill_md(content: bytes, *, skill_root: Path, overwrite: bool = False) -> dict:
    """Install a single SKILL.md file to ``<skill_root>/<name>/SKILL.md``."""
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("SKILL.md must be UTF-8") from exc
    fm = parse_skill_frontmatter(text)
    if "name" not in fm or "description" not in fm:
        raise ValueError("SKILL.md frontmatter must have name and description")
    name = validate_skill_name(fm["name"])

    target_dir = skill_root / name
    target = target_dir / "SKILL.md"
    if target.exists() and not overwrite:
        raise ValueError(f"skill '{name}' already exists; pass overwrite=true")
    target_dir.mkdir(parents=True, exist_ok=True)
    target.write_text(text, encoding="utf-8")
    return {"ok": True, "name": name, "path": str(target_dir), "kind": "md"}


def install_skill_zip(content: bytes, *, skill_root: Path, overwrite: bool = False) -> dict:
    """Install a ``.zip`` skill package. Validates entries then extracts."""
    skill_root.mkdir(parents=True, exist_ok=True)
    bio = io.BytesIO(content)
    validate_zip_entries(bio)
    bio.seek(0)
    staging_parent = Path(tempfile.mkdtemp(prefix=".skill-upload-", dir=skill_root))
    try:
        with zipfile.ZipFile(bio, "r") as zipf:
            names = zipf.namelist()
            skill_md_entries = [n for n in names if n.endswith("SKILL.md")]
            if len(skill_md_entries) != 1:
                raise ValueError("zip must contain exactly one SKILL.md")
            # Determine skill name from frontmatter.
            primary = skill_md_entries[0]
            try:
                md_text = zipf.read(primary).decode("utf-8")
            except UnicodeDecodeError as exc:
                raise ValueError("SKILL.md must be UTF-8") from exc
            fm = parse_skill_frontmatter(md_text)
            if "name" not in fm or "description" not in fm:
                raise ValueError("SKILL.md frontmatter must have name and description")
            name = validate_skill_name(fm["name"])

            target_dir = skill_root / name
            if target_dir.exists() and not overwrite:
                raise ValueError(f"skill '{name}' already exists; pass overwrite=true")
            primary_parts = Path(primary.replace("\\", "/")).parts
            common_root = primary_parts[0] + "/" if len(primary_parts) > 1 else ""
            relevant = [n for n in names if not n.endswith("/")]
            if common_root and any(not n.replace("\\", "/").startswith(common_root) for n in relevant):
                raise ValueError("zip must contain one skill folder or a root SKILL.md")

            staging_dir = staging_parent / name
            staging_dir.mkdir()
            staging_resolved = staging_dir.resolve()
            for info in zipf.infolist():
                if info.is_dir():
                    continue
                rel = info.filename.replace("\\", "/")
                if common_root:
                    rel = rel[len(common_root):]
                if not rel:
                    continue
                dest = (staging_dir / rel).resolve()
                if staging_resolved not in dest.parents:
                    raise ValueError(f"escape attempt: {info.filename}")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zipf.open(info) as src, open(dest, "wb") as out:
                    shutil.copyfileobj(src, out)

            if not (staging_dir / "SKILL.md").is_file():
                raise ValueError("SKILL.md must be at the skill package root")
            _replace_directory(staging_dir, target_dir, overwrite=overwrite)
        return {"ok": True, "name": name, "path": str(target_dir), "kind": "zip"}
    finally:
        shutil.rmtree(staging_parent, ignore_errors=True)
