"""Bounded ZIP interchange for the one supported Template payload format."""

import io
import logging
import re
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from uuid import uuid4

from server.gameserver.roles import load_template_metadata
from server.gameserver.template import validate_template
from server.platform.catalog import import_template, require_owned_template
from server.platform.database import PlatformDatabase
from server.platform.models import TemplateMetadata as TemplateRecord
from server.platform.starter_templates import MARKER as STARTER_MARKER

logger = logging.getLogger(__name__)
MAX_ZIP_BYTES = 8 * 1024 * 1024
MAX_UNPACKED_BYTES = 20 * 1024 * 1024
MAX_FILE_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 64
_ROOT_FILES = {"metadata.json", "world/world.md", "world/world_state_schema.json",
               "world/world_state_initial.json", "prompts/ai_guidelines.md",
               "prompts/world_update.md", "prompts/narration.md"}
_CHARACTER_FILES = {"character.md", "opening.md", "character_view_schema.json",
                    "character_status_schema.json", "character_status_initial.json"}


def _allowed(name: str) -> bool:
    if name in _ROOT_FILES:
        return True
    parts = PurePosixPath(name).parts
    return (len(parts) == 3 and parts[0] == "characters"
            and bool(re.fullmatch(r"[1-9][0-9]*", parts[1]))
            and parts[2] in _CHARACTER_FILES)


def _safe_parts(name: str) -> tuple[str, ...]:
    if (not name or "\\" in name or name.startswith("/") or "\x00" in name
            or any(part in ("", ".", "..") for part in name.split("/"))):
        raise ValueError("invalid_template_zip")
    return PurePosixPath(name).parts


def _extract(data: bytes, target: Path) -> None:
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError("template_zip_too_large")
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except (zipfile.BadZipFile, OSError) as exc:
        raise ValueError("invalid_template_zip") from exc
    with archive:
        files = [item for item in archive.infolist() if not item.is_dir()]
        if not files or len(archive.infolist()) > MAX_ENTRIES:
            raise ValueError("invalid_template_zip")
        names = [item.filename for item in files]
        for item in archive.infolist():
            _safe_parts(item.filename.rstrip("/"))
        # Accept the rootless export and a ZIP with one enclosing folder.
        prefix = ""
        if not any(name == "metadata.json" for name in names):
            first = names[0].split("/", 1)[0]
            if not all(name.startswith(first + "/") for name in names):
                raise ValueError("invalid_template_zip")
            prefix = first + "/"
        normalized = []
        seen: set[str] = set()
        for item in files:
            name = item.filename[len(prefix):]
            if (not _allowed(name) or name in seen or item.flag_bits & 1
                    or item.compress_type not in (zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED)
                    or ((item.external_attr >> 16) & 0o170000) == 0o120000
                    or item.file_size > MAX_FILE_BYTES):
                raise ValueError("invalid_template_zip")
            seen.add(name)
            normalized.append((item, name))
        if sum(item.file_size for item, _ in normalized) > MAX_UNPACKED_BYTES:
            raise ValueError("template_zip_too_large")
        target.mkdir(parents=True)
        total = 0
        for item, name in normalized:
            try:
                with archive.open(item) as source:
                    content = source.read(MAX_FILE_BYTES + 1)
            except (RuntimeError, zipfile.BadZipFile, OSError) as exc:
                raise ValueError("invalid_template_zip") from exc
            total += len(content)
            if len(content) > MAX_FILE_BYTES or total > MAX_UNPACKED_BYTES:
                raise ValueError("template_zip_too_large")
            path = target / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)


def export_template_zip(
    database: PlatformDatabase, templates_dir: Path, template_id: str,
    owner_user_id: int,
) -> bytes:
    require_owned_template(database, template_id, owner_user_id)
    root = Path(templates_dir) / template_id
    return export_template_payload_zip(root)


def export_template_payload_zip(root: Path) -> bytes:
    """Package a validated payload, including bundled showcase files."""
    validate_template(root)
    output = io.BytesIO()
    entries = list(root.rglob("*"))
    if any(path.is_symlink() for path in entries):
        raise ValueError("invalid_template_zip")
    # The private seed marker is catalog bookkeeping, not Template content.
    files = sorted(path for path in entries if path.is_file()
                   and path.relative_to(root).as_posix() != STARTER_MARKER)
    if any(not _allowed(path.relative_to(root).as_posix())
           for path in files):
        raise ValueError("invalid_template_zip")
    if (len(files) > MAX_ENTRIES
            or any(path.stat().st_size > MAX_FILE_BYTES for path in files)
            or sum(path.stat().st_size for path in files) > MAX_UNPACKED_BYTES):
        raise ValueError("template_zip_too_large")
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in files:
            archive.write(path, path.relative_to(root).as_posix())
    data = output.getvalue()
    if len(data) > MAX_ZIP_BYTES:
        raise ValueError("template_zip_too_large")
    return data


def import_template_zip(
    database: PlatformDatabase, templates_dir: Path, owner_user_id: int,
    data: bytes, min_count: int, max_count: int, template_id: str | None = None,
) -> TemplateRecord:
    templates_dir = Path(templates_dir)
    original = (require_owned_template(database, template_id, owner_user_id)
                if template_id is not None else None)
    templates_dir.mkdir(parents=True, exist_ok=True)
    nonce = uuid4().hex
    staging = templates_dir / f".zip-{nonce}.importing"
    backup = templates_dir / f".zip-{nonce}.previous"
    swapped = False
    try:
        _extract(data, staging)
        validate_template(staging, min_count=min_count, max_count=max_count)
        metadata = load_template_metadata(staging)
        if not metadata.title:
            raise ValueError("invalid_template_zip")
        if original is None:
            return import_template(
                database, staging, templates_dir, owner_user_id,
                metadata.title, title=metadata.title,
                introduction=metadata.introduction, tags=metadata.tags,
            )
        target = templates_dir / template_id
        marker = target / STARTER_MARKER
        if marker.is_file():
            (staging / STARTER_MARKER).write_bytes(marker.read_bytes())
        target.rename(backup)
        try:
            staging.rename(target)
            swapped = True
            updated = database.rename_template(template_id, metadata.title)
        except Exception:
            if swapped:
                target.rename(staging)
            backup.rename(target)
            # A database helper may fail after its UPDATE committed; restore its
            # display title as well when that happened.
            try:
                current = database.get_template(template_id)
                if current is not None and current.name != original.name:
                    database.rename_template(template_id, original.name)
            except Exception:
                logger.exception("Could not restore Template catalog title for %s", template_id)
            raise
        shutil.rmtree(backup, ignore_errors=True)
        return updated
    finally:
        shutil.rmtree(staging, ignore_errors=True)
