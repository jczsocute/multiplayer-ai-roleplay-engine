"""Basic Template text editor; advanced payload files are copied unchanged."""

import logging
import shutil
from pathlib import Path
from uuid import uuid4

from server.gameserver.roles import (
    MAX_TITLE_LENGTH, load_template_metadata, parse_template_metadata,
    write_template_metadata,
)
from server.gameserver.template import validate_template
from server.platform.catalog import require_owned_template
from server.platform.database import PlatformDatabase

DEFAULT_VIEW_SCHEMA = '{\n  "world_information": "<该角色目前能够观察、知道或合理判断出的世界信息>"\n}\n'
logger = logging.getLogger(__name__)


def read_template_editor(
    database: PlatformDatabase, templates_dir: Path, template_id: str,
    owner_user_id: int,
) -> dict:
    require_owned_template(database, template_id, owner_user_id)
    root = Path(templates_dir) / template_id
    roles = validate_template(root)
    metadata = load_template_metadata(root)
    return {
        "id": template_id,
        "title": metadata.title,
        "introduction": metadata.introduction,
        "tags": list(metadata.tags),
        "world": (root / "world/world.md").read_text(encoding="utf-8"),
        "ai_guidelines": (root / "prompts/ai_guidelines.md").read_text(encoding="utf-8"),
        "characters": [
            {
                "index": index,
                "character": (root / f"characters/{index}/character.md").read_text(encoding="utf-8"),
                "opening": (root / f"characters/{index}/opening.md").read_text(encoding="utf-8"),
            }
            for index in range(1, len(roles.role_ids) + 1)
        ],
    }


def save_template_editor(
    database: PlatformDatabase, templates_dir: Path, template_id: str,
    owner_user_id: int, body: object, min_role_count: int, max_role_count: int,
) -> dict:
    template = require_owned_template(database, template_id, owner_user_id)
    root = Path(templates_dir) / template_id
    validate_template(root)
    old = load_template_metadata(root)
    if not isinstance(body, dict):
        raise ValueError("invalid_editor_content")
    title = body.get("title")
    introduction = body.get("introduction")
    tags = body.get("tags")
    world = body.get("world")
    guidelines = body.get("ai_guidelines")
    characters = body.get("characters")
    if not isinstance(title, str) or not title.strip() or len(title.strip()) > MAX_TITLE_LENGTH:
        raise ValueError("invalid_template_name")
    if not isinstance(introduction, str) or not isinstance(world, str) or not isinstance(guidelines, str):
        raise ValueError("invalid_editor_content")
    if not isinstance(characters, list) or not min_role_count <= len(characters) <= max_role_count:
        raise ValueError("invalid_role_count")
    for index, character in enumerate(characters, 1):
        if (not isinstance(character, dict) or character.get("index") != index
                or not isinstance(character.get("character"), str)
                or not isinstance(character.get("opening"), str)):
            raise ValueError("invalid_editor_content")
    names = old.names[:len(characters)] + tuple(
        f"角色{index}" for index in range(len(old.names) + 1, len(characters) + 1)
    )
    metadata = parse_template_metadata({
        "count": len(characters), "names": list(names), "title": title,
        "introduction": introduction, "tags": tags,
    }, min_count=min_role_count, max_count=max_role_count)

    nonce = uuid4().hex
    staging = root.parent / f".{template_id}.{nonce}.editing"
    backup = root.parent / f".{template_id}.{nonce}.previous"
    swapped = False
    try:
        shutil.copytree(root, staging)
        write_template_metadata(staging, metadata)
        (staging / "world/world.md").write_text(world, encoding="utf-8")
        (staging / "prompts/ai_guidelines.md").write_text(guidelines, encoding="utf-8")
        for index in range(len(characters) + 1, len(old.names) + 1):
            shutil.rmtree(staging / "characters" / str(index))
        for index, character in enumerate(characters, 1):
            directory = staging / "characters" / str(index)
            if index > len(old.names):
                directory.mkdir()
                (directory / "character_view_schema.json").write_text(
                    DEFAULT_VIEW_SCHEMA, encoding="utf-8"
                )
            (directory / "character.md").write_text(character["character"], encoding="utf-8")
            (directory / "opening.md").write_text(character["opening"], encoding="utf-8")
        validate_template(staging, min_count=min_role_count, max_count=max_role_count)
        root.rename(backup)
        try:
            staging.rename(root)
            swapped = True
            if metadata.title != template.name:
                database.rename_template(template_id, metadata.title)
            else:
                # Refresh updated_at after a content-only edit too.
                database.rename_template(template_id, template.name)
        except Exception:
            if swapped:
                root.rename(staging)
            backup.rename(root)
            # A database helper may commit and then fail. Restore the catalog
            # mirror after the original payload has been put back.
            try:
                current = database.get_template(template_id)
                if current is not None and current.name != template.name:
                    database.rename_template(template_id, template.name)
            except Exception:
                logger.exception("Could not restore Template catalog title for %s", template_id)
            raise
    finally:
        if staging.exists():
            shutil.rmtree(staging, ignore_errors=True)
    # A cleanup error cannot make the successful save appear to have failed.
    shutil.rmtree(backup, ignore_errors=True)
    return read_template_editor(database, templates_dir, template_id, owner_user_id)
