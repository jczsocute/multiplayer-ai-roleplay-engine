"""Seed the two bundled playable examples as ordinary owned Templates."""

from pathlib import Path

from server.gameserver.roles import load_template_metadata
from server.platform.catalog import import_template
from server.platform.database import PlatformDatabase

STARTER_SOURCES = ("example1", "example1_en")
SOURCE_ROOT = Path(__file__).resolve().parents[2] / "templates"
MARKER = ".starter_source"


def seed_starter_templates(
    database: PlatformDatabase, templates_dir: Path, owner_user_id: int
) -> list[str]:
    """Return newly created ids; existing marked copies make repeat calls no-ops."""
    existing = {
        (templates_dir / item.id / MARKER).read_text(encoding="utf-8").strip()
        for item in database.list_user_templates(owner_user_id)
        if (templates_dir / item.id / MARKER).is_file()
    }
    created = []
    for source_name in STARTER_SOURCES:
        if source_name in existing:
            continue
        source = SOURCE_ROOT / source_name
        name = load_template_metadata(source).title
        item = import_template(database, source, templates_dir, owner_user_id, name)
        (templates_dir / item.id / MARKER).write_text(source_name, encoding="utf-8")
        created.append(item.id)
    return created
