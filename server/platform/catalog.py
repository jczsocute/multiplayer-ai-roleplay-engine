"""Filesystem payload operations paired with PlatformDatabase metadata."""

import logging
import shutil
from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path

from server.gameserver.roles import (
    LEGACY_ROLES_FILENAME, METADATA_FILENAME, RoleConfig, TemplateMetadata,
    load_template_metadata, migrate_roles_to_metadata, validate_role_count,
    write_template_metadata,
)
from server.platform.database import (
    MAX_RESOURCE_NAME_LENGTH, PlatformDatabase, generate_stable_id,
)
# `TemplateRecord` is the platform.db row; `TemplateMetadata` is the payload file.
from server.platform.models import GameMetadata, TemplateMetadata as TemplateRecord
from server.platform.scenario_manager import ScenarioManager

logger = logging.getLogger(__name__)


def import_template(
    database: PlatformDatabase,
    source: Path,
    templates_dir: Path,
    owner_user_id: int,
    name: str,
    is_public: bool = False,
    exclude: tuple[str, ...] = (),
    title: str = "",
    introduction: str = "",
    tags: tuple[str, ...] = (),
) -> TemplateRecord:
    if not source.is_dir():
        raise ValueError(f"template directory does not exist: {source}")
    template_id = _available_id(database, "tmpl")
    target = templates_dir / template_id
    staging = templates_dir / f".{template_id}.importing"
    templates_dir.mkdir(parents=True, exist_ok=True)
    ignore = shutil.ignore_patterns(*exclude) if exclude else None
    try:
        shutil.copytree(source, staging, ignore=ignore)
        migrate_roles_to_metadata(
            staging, title=title or name, introduction=introduction, tags=tags
        )
        RoleConfig.load(staging, min_count=1, max_count=10_000)
        # Only the imported copy takes the requested title.
        set_payload_title(staging, title or name)
        metadata = database.create_template(
            template_id, owner_user_id, name, is_public=is_public
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        staging.rename(target)
    except Exception:
        database.delete_template(template_id)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return metadata


def has_payload(root: Path) -> bool:
    """True when a directory holds a usable payload (new format, or legacy)."""
    directory = Path(root)
    return (
        (directory / METADATA_FILENAME).is_file()
        or (directory / LEGACY_ROLES_FILENAME).is_file()
    )


def load_payload_metadata(root: Path) -> TemplateMetadata | None:
    """Payload metadata for one payload directory, or None when unusable."""
    try:
        return load_template_metadata(Path(root), min_count=1, max_count=10_000)
    except (ValueError, OSError):
        return None


def load_payload_metadata_by_id(
    templates_dir: Path, template_id: str
) -> TemplateMetadata | None:
    """Payload metadata for one Template id, or None when the payload is unusable."""
    return load_payload_metadata(Path(templates_dir) / template_id)


def set_payload_title(root: Path, title: str) -> TemplateMetadata | None:
    """Write one payload's `title`, keeping the rest of its metadata intact."""
    metadata = load_payload_metadata(root)
    if metadata is None:
        return None
    updated = replace(metadata, title=title.strip())
    write_template_metadata(Path(root), updated)
    return updated


def template_role_names(templates_dir: Path, template_id: str) -> tuple[str, ...]:
    """Display names from a Template payload, for the Admin catalog table."""
    metadata = load_payload_metadata(Path(templates_dir) / template_id)
    return metadata.names if metadata is not None else ()


def create_game_snapshot(
    database: PlatformDatabase,
    template_id: str,
    owner_user_id: int,
    name: str,
    templates_dir: Path,
    games_dir: Path,
) -> GameMetadata:
    template = database.get_template(template_id)
    if template is None:
        raise ValueError(f"unknown template: {template_id}")
    if template.owner_user_id != owner_user_id and not template.is_public:
        raise PermissionError("template is private")
    source = templates_dir / template_id
    if not source.is_dir():
        raise ValueError(f"template payload is missing: {template_id}")
    game_id = _available_id(database, "game")
    target = games_dir / game_id
    games_dir.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source, target)
    try:
        return database.create_game(game_id, owner_user_id, name, template_id)
    except Exception:
        shutil.rmtree(target)
        raise


def import_game(
    database: PlatformDatabase,
    source: Path,
    games_dir: Path,
    owner_user_id: int,
    name: str,
) -> GameMetadata:
    if not source.is_dir():
        raise ValueError(f"game directory does not exist: {source}")
    game_id = _available_id(database, "game")
    target = games_dir / game_id
    staging = games_dir / f".{game_id}.importing"
    games_dir.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copytree(source, staging)
        migrate_roles_to_metadata(staging)
        RoleConfig.load(staging, min_count=1, max_count=10_000)
        metadata = database.create_game(game_id, owner_user_id, name, None)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        staging.rename(target)
    except Exception:
        database.delete_game_metadata(game_id)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return metadata


def _available_id(database: PlatformDatabase, prefix: str) -> str:
    for _ in range(20):
        value = generate_stable_id(prefix)
        existing = (
            database.get_template(value) if prefix == "tmpl" else database.get_game(value)
        )
        if existing is None:
            return value
    raise RuntimeError(f"could not allocate a unique {prefix} id")


# --- owner resource management (Lobby: 我的存档 / 我的剧本) ----------------------

def next_copy_name(base: str, existing: Iterable[str]) -> str:
    """``love_story`` -> ``love_story_1``, then ``love_story_2``, ...

    Suffixes are chosen over the current owner's own names only.
    """
    taken = {name for name in existing}
    index = 1
    while f"{base}_{index}" in taken:
        index += 1
    return f"{base}_{index}"


def describe_template(
    templates_dir: Path,
    template: TemplateRecord,
    owner_username: str,
    *,
    include_introduction: bool = False,
) -> dict:
    """Catalog row plus payload metadata, for list and detail rows.

    Lists stay lean: ``tags`` are cheap to include, ``introduction`` is only read
    for the detail view.
    """
    payload = load_payload_metadata(Path(templates_dir) / template.id)
    described = {
        "id": template.id,
        # The payload title wins; the catalog row is the mirror/fallback.
        "name": payload.display_title(template.name) if payload else template.name,
        "owner_user_id": template.owner_user_id,
        "owner_username": owner_username,
        "is_public": template.is_public,
        "created_at": template.created_at,
        "updated_at": template.updated_at,
        "role_count": payload.count if payload else 0,
        "role_names": list(payload.names) if payload else [],
        "tags": list(payload.tags) if payload else [],
    }
    if include_introduction:
        described["introduction"] = payload.introduction if payload else ""
    return described


def _template_presentation(
    database: PlatformDatabase, templates_dir: Path, game: GameMetadata
) -> tuple[str, tuple[str, ...]]:
    """Introduction/tags a Game carries over from its source Template, if any."""
    if not game.source_template_id:
        return "", ()
    payload = load_payload_metadata(Path(templates_dir) / game.source_template_id)
    if payload is None:
        return "", ()
    return payload.introduction, payload.tags


def migrate_catalog_payloads(
    database: PlatformDatabase,
    templates_dir: Path,
    games_dir: Path,
    titles: Mapping[str, str] | None = None,
) -> list[str]:
    """One-time, idempotent payload migration for *registered* resources only.

    Unregistered directories are never scanned or claimed; a payload that cannot
    be migrated is logged and skipped so startup keeps working.
    """
    templates_dir = Path(templates_dir)
    games_dir = Path(games_dir)
    migrated: list[str] = []
    for template in database.list_templates():
        payload = templates_dir / template.id
        try:
            if migrate_roles_to_metadata(payload, title=template.name):
                migrated.append(f"template:{template.id}")
        except ValueError as exc:
            logger.warning("Skipping Template payload %s: %s", template.id, exc)
            continue
        # A payload without a title (older metadata.json) gains one, and the
        # catalog row is renamed to mirror it so both stay equal.
        metadata = load_payload_metadata(payload)
        if metadata is None:
            continue
        # A slug-like catalog name becomes a summarized title when known.
        summarized = (titles or {}).get(metadata.title or template.name, template.name)
        title = metadata.title or summarized
        if not metadata.title:
            set_payload_title(payload, title)
            migrated.append(f"title:{template.id}")
        if title != template.name:
            database.rename_template(template.id, title)
            logger.info(
                "Renamed catalog row %s: %r -> %r", template.id, template.name, title
            )
    for game in database.list_games():
        introduction, tags = _template_presentation(database, templates_dir, game)
        try:
            if migrate_roles_to_metadata(
                games_dir / game.id, introduction=introduction, tags=tags
            ):
                migrated.append(f"game:{game.id}")
        except ValueError as exc:
            logger.warning("Skipping Game payload %s: %s", game.id, exc)
    return migrated


def require_owned_game(
    database: PlatformDatabase, game_id: str, owner_user_id: int
) -> GameMetadata:
    game = database.get_game(game_id)
    if game is None:
        raise ValueError("game_not_found")
    if game.owner_user_id != owner_user_id:
        raise PermissionError("forbidden")
    return game


def require_owned_template(
    database: PlatformDatabase, template_id: str, owner_user_id: int
) -> TemplateRecord:
    template = database.get_template(template_id)
    if template is None:
        raise ValueError("template_not_found")
    if template.owner_user_id != owner_user_id:
        raise PermissionError("template_not_owned")
    return template


def require_inactive_game(database: PlatformDatabase, game_id: str) -> None:
    """Guard copy/delete: an active Room may be holding the SQLite file open."""
    if database.game_is_active(game_id):
        raise ValueError("game_is_active")


def copy_game(
    database: PlatformDatabase,
    game_id: str,
    owner_user_id: int,
    games_dir: Path,
) -> GameMetadata:
    """Copy metadata + payload + game.db into a brand new ``game_*`` id."""
    game = require_owned_game(database, game_id, owner_user_id)
    require_inactive_game(database, game_id)
    source = games_dir / game_id
    if not source.is_dir():
        raise ValueError("game_not_found")
    new_id = _available_id(database, "game")
    name = next_copy_name(
        game.name, [value.name for value in database.list_user_games(owner_user_id)]
    )
    name = name[:MAX_RESOURCE_NAME_LENGTH]
    games_dir.mkdir(parents=True, exist_ok=True)
    staging = games_dir / f".{new_id}.copying"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source, staging)
    try:
        metadata = database.create_game(
            new_id, owner_user_id, name, game.source_template_id
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        staging.rename(games_dir / new_id)
    except Exception:
        # Never leave metadata pointing at a directory that does not exist.
        database.delete_game_metadata(new_id)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return metadata


def delete_game(
    database: PlatformDatabase, game_id: str, owner_user_id: int, games_dir: Path
) -> None:
    """Delete metadata + payload. Refuses while a Room is using the Game."""
    require_owned_game(database, game_id, owner_user_id)
    require_inactive_game(database, game_id)
    payload = games_dir / game_id
    staging: Path | None = None
    if payload.is_dir():
        staging = games_dir / f".{game_id}.deleting"
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
        payload.rename(staging)
    try:
        database.delete_game_metadata(game_id)
    except Exception:
        if staging is not None:
            staging.rename(payload)
        raise
    if staging is not None:
        shutil.rmtree(staging, ignore_errors=True)


def rename_template(
    database: PlatformDatabase,
    template_id: str,
    owner_user_id: int,
    name: str,
    templates_dir: Path | None = None,
    *,
    require_owner: bool = True,
) -> TemplateRecord:
    """Rename a Script: the payload `title` and the catalog row move together.

    Admin reaches the same helper with ``require_owner=False``; only the owner
    check is skipped, never the payload/row synchronisation.
    """
    template = (
        require_owned_template(database, template_id, owner_user_id)
        if require_owner else database.get_template(template_id)
    )
    if template is None:
        raise ValueError("template_not_found")
    name = name.strip()
    if not name or len(name) > MAX_RESOURCE_NAME_LENGTH:
        raise ValueError("invalid_template_name")
    payload = Path(templates_dir) / template_id if templates_dir is not None else None
    old_title = None
    if payload is not None:
        old_metadata = load_payload_metadata(payload)
        if old_metadata is None:
            raise ValueError("template_not_found")
        old_title = old_metadata.title
        set_payload_title(payload, name)
    try:
        return database.rename_template(template_id, name)
    except Exception:
        if payload is not None and old_title is not None:
            try:
                set_payload_title(payload, old_title)
            except OSError:
                logger.exception("Could not restore Template title for %s", template_id)
        raise


def set_template_public_owned(
    database: PlatformDatabase, template_id: str, owner_user_id: int, is_public: bool
) -> TemplateRecord:
    """Owner-facing visibility toggle.

    Admin reaches ``PlatformDatabase.set_template_public`` directly; this wrapper
    only adds the owner check, so both paths share one implementation.
    """
    require_owned_template(database, template_id, owner_user_id)
    return database.set_template_public(template_id, is_public)


def create_template_from_scaffold(
    database: PlatformDatabase,
    templates_dir: Path,
    owner_user_id: int,
    name: str,
    role_count: int,
    min_role_count: int,
    max_role_count: int,
    title: str = "",
) -> TemplateRecord:
    """Scaffold a new private Template payload from ``templates/default``."""
    name = name.strip()
    if not name or len(name) > MAX_RESOURCE_NAME_LENGTH:
        raise ValueError("invalid_template_name")
    validate_role_count(role_count, min_role_count, max_role_count)
    base = templates_dir / ScenarioManager.BASE_TEMPLATE
    if not base.is_dir():
        raise ValueError("template_not_found")
    template_id = _available_id(database, "tmpl")
    templates_dir.mkdir(parents=True, exist_ok=True)
    staging = templates_dir / f".{template_id}.creating"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(base, staging)
    try:
        ScenarioManager.scaffold_roles(staging, role_count, title=title or name)
        RoleConfig.load(
            staging,
            min_count=min_role_count,
            max_count=max_role_count,
        )
        metadata = database.create_template(
            template_id, owner_user_id, name, is_public=False
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        staging.rename(templates_dir / template_id)
    except Exception:
        database.delete_template(template_id)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return metadata


def copy_template(
    database: PlatformDatabase, template_id: str, owner_user_id: int, templates_dir: Path
) -> TemplateRecord:
    """Copy one of the owner's Templates into a new private Template."""
    template = require_owned_template(database, template_id, owner_user_id)
    source = templates_dir / template_id
    if load_payload_metadata(Path(templates_dir) / template_id) is None:
        raise ValueError("template_not_found")
    new_id = _available_id(database, "tmpl")
    name = next_copy_name(
        template.name,
        [value.name for value in database.list_user_templates(owner_user_id)],
    )
    name = name[:MAX_RESOURCE_NAME_LENGTH]
    templates_dir.mkdir(parents=True, exist_ok=True)
    staging = templates_dir / f".{new_id}.copying"
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source, staging)
    set_payload_title(staging, name)
    try:
        metadata = database.create_template(
            new_id, owner_user_id, name, is_public=False
        )
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    try:
        staging.rename(templates_dir / new_id)
    except Exception:
        database.delete_template(new_id)
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return metadata


def delete_owned_template(
    database: PlatformDatabase, template_id: str, owner_user_id: int,
    templates_dir: Path, *, require_owner: bool = True,
) -> None:
    """Delete a Template with the same compensating path for owner and Admin."""
    if require_owner:
        require_owned_template(database, template_id, owner_user_id)
    elif database.get_template(template_id) is None:
        raise ValueError("template_not_found")
    payload = Path(templates_dir) / template_id
    staging = Path(templates_dir) / f".{template_id}.deleting"
    staged = payload.is_dir()
    if staged:
        payload.rename(staging)
    try:
        database.delete_template(template_id)
    except Exception:
        if staged:
            staging.rename(payload)
        raise
    if staged:
        shutil.rmtree(staging, ignore_errors=True)
