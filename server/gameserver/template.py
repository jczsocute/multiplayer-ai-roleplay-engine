"""One filesystem Template/Game payload format, shared by loading and editing."""

import json
from pathlib import Path

from server.gameserver.roles import RoleConfig


def _object(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid JSON file: {path}") from exc
    if not isinstance(value, dict):
        raise ValueError(f"JSON root must be an object: {path}")
    return value


def same_key_structure(schema: dict, value: dict) -> bool:
    """The schema is an example with descriptions, not JSON Schema."""
    return set(schema) == set(value) and all(
        isinstance(expected, dict) == isinstance(value[key], dict)
        and (not isinstance(expected, dict) or same_key_structure(expected, value[key]))
        for key, expected in schema.items()
    )


def validate_template(
    root: str | Path, *, min_count: int | None = None, max_count: int | None = None
) -> RoleConfig:
    root = Path(root)
    roles = RoleConfig.load(root, min_count=min_count, max_count=max_count)
    for name in ("world/world.md", "prompts/ai_guidelines.md",
                 "prompts/world_update.md", "prompts/narration.md"):
        if not (root / name).is_file():
            raise ValueError(f"missing Template file: {name}")
    world_schema = _object(root / "world/world_state_schema.json")
    world_initial = _object(root / "world/world_state_initial.json")
    if not same_key_structure(world_schema, world_initial):
        raise ValueError("world initial state does not match its schema keys")
    characters = root / "characters"
    expected = {str(index) for index in range(1, len(roles.role_ids) + 1)}
    actual = {path.name for path in characters.iterdir() if path.is_dir()} if characters.is_dir() else set()
    if actual != expected:
        raise ValueError("character directories must be exactly 1..N")
    legacy_paths = (
        "schemas", "players", "statusbar", "opening",
        "characters/opening", "characters/statusbar", "prompts/player_view.md",
    )
    if (any((root / name).exists() for name in legacy_paths)
            or any(characters.glob("player_*.md"))):
        raise ValueError("legacy Template layout is not supported")
    for index in range(1, len(roles.role_ids) + 1):
        directory = characters / str(index)
        for name in ("character.md", "opening.md"):
            if not (directory / name).is_file():
                raise ValueError(f"missing Template file: characters/{index}/{name}")
        _object(directory / "character_view_schema.json")
        status_schema = directory / "character_status_schema.json"
        status_initial = directory / "character_status_initial.json"
        if status_schema.is_file():
            schema = _object(status_schema)
            initial = _object(status_initial)
            if not same_key_structure(schema, initial):
                raise ValueError(f"character {index} status initial does not match schema keys")
        elif status_initial.exists():
            raise ValueError(f"character {index} has status initial without schema")
    return roles
