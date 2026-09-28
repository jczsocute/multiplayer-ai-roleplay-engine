"""Role model plus the Template/Game payload metadata loader.

Payload metadata (``metadata.json``) lives next to the rest of a
Template/Game payload:

```json
{
  "count": 2,
  "names": ["林岚", "周砚"],
  "title": "气象站的雷雨夜",
  "introduction": "...",
  "tags": ["..."]
}
```

``count``/``names`` keep exactly the shape the old ``roles.json`` used: ``names``
is the ordered list of role display names, and runtime role ids stay the derived
``P1..PN`` sequence.

``title`` is the Script's own display title, stored in the payload so a Script is
not defined only by a platform.db row. ``platform.db.templates.name`` mirrors it
and is kept in sync by the rename/import/copy/migration paths.

``introduction``/``tags`` are Script-facing presentation fields; Games carry them
along in the snapshot but never read them at runtime.
"""

import json
import os
from dataclasses import dataclass
from pathlib import Path

METADATA_FILENAME = "metadata.json"
# Only the one-time migration helper reads this name.
LEGACY_ROLES_FILENAME = "roles.json"
MAX_TITLE_LENGTH = 60
MAX_INTRODUCTION_LENGTH = 1000
MAX_TAGS = 10
MAX_TAG_LENGTH = 20


@dataclass(frozen=True)
class RoleConfig:
    role_ids: tuple[str, ...]
    names: dict[str, str]

    def __post_init__(self) -> None:
        expected = tuple(f"P{index}" for index in range(1, len(self.role_ids) + 1))
        if not self.role_ids or self.role_ids != expected:
            raise ValueError("role ids must be the ordered sequence P1...PN")
        if set(self.names) != set(self.role_ids) or not all(
            isinstance(name, str) and name.strip() for name in self.names.values()
        ):
            raise ValueError("role names must contain one non-empty name for every role")

    @classmethod
    def from_data(
        cls,
        data: object,
        *,
        min_count: int | None = None,
        max_count: int | None = None,
    ) -> "RoleConfig":
        return parse_template_metadata(
            data, min_count=min_count, max_count=max_count
        ).to_role_config()

    @classmethod
    def load(
        cls,
        path: str | Path,
        *,
        min_count: int | None = None,
        max_count: int | None = None,
    ) -> "RoleConfig":
        """Load the role model from a payload directory's ``metadata.json``."""
        return load_template_metadata(
            path, min_count=min_count, max_count=max_count
        ).to_role_config()

    @property
    def definitions(self) -> list[dict[str, str]]:
        return [{"id": role_id, "name": self.names[role_id]} for role_id in self.role_ids]


@dataclass(frozen=True)
class TemplateMetadata:
    """Payload metadata for one Template or Game (never a platform.db row)."""

    count: int
    names: tuple[str, ...]
    title: str = ""
    introduction: str = ""
    tags: tuple[str, ...] = ()

    def display_title(self, fallback: str = "") -> str:
        """The Script title: the payload owns it, the catalog row is the mirror."""
        return self.title or fallback

    def to_role_config(
        self, *, min_count: int | None = None, max_count: int | None = None
    ) -> RoleConfig:
        """Build the runtime role model (derived ``P1..PN`` ids)."""
        validate_role_count(self.count, min_count, max_count)
        role_ids = tuple(f"P{index}" for index in range(1, self.count + 1))
        return RoleConfig(
            role_ids, dict(zip(role_ids, self.names, strict=True))
        )

    @property
    def role_ids(self) -> tuple[str, ...]:
        return tuple(f"P{index}" for index in range(1, self.count + 1))

    def as_dict(self) -> dict:
        return {
            "count": self.count,
            "names": list(self.names),
            "title": self.title,
            "introduction": self.introduction,
            "tags": list(self.tags),
        }


def normalize_tags(value: object) -> tuple[str, ...]:
    """Trim, drop empty values, de-duplicate and cap the tag list."""
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{METADATA_FILENAME} tags must be an array of strings")
    tags: list[str] = []
    for tag in value:
        if not isinstance(tag, str):
            raise ValueError("every tag must be a string")
        cleaned = tag.strip()
        if not cleaned:
            continue
        if len(cleaned) > MAX_TAG_LENGTH:
            raise ValueError(f"each tag must be at most {MAX_TAG_LENGTH} characters")
        if cleaned in tags:
            continue
        tags.append(cleaned)
    if len(tags) > MAX_TAGS:
        raise ValueError(f"at most {MAX_TAGS} tags are allowed")
    return tuple(tags)


def parse_template_metadata(
    payload: object,
    *,
    min_count: int | None = None,
    max_count: int | None = None,
) -> TemplateMetadata:
    """Validate one payload dict. ``count``/``names`` keep the legacy shape."""
    if not isinstance(payload, dict):
        raise ValueError(f"{METADATA_FILENAME} must contain a JSON object")
    count = payload.get("count")
    names = payload.get("names")
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError(f"{METADATA_FILENAME} count must be a positive integer")
    if not isinstance(names, list) or len(names) != count:
        raise ValueError(f"{METADATA_FILENAME} names length must equal count")
    if not all(isinstance(name, str) and name.strip() for name in names):
        raise ValueError("every role name must be a non-empty string")
    title = payload.get("title", "")
    if not isinstance(title, str):
        raise ValueError(f"{METADATA_FILENAME} title must be a string")
    title = title.strip()
    if len(title) > MAX_TITLE_LENGTH:
        raise ValueError(
            f"{METADATA_FILENAME} title must be at most {MAX_TITLE_LENGTH} characters"
        )
    introduction = payload.get("introduction", "")
    if not isinstance(introduction, str):
        raise ValueError(f"{METADATA_FILENAME} introduction must be a string")
    introduction = introduction.strip()
    if len(introduction) > MAX_INTRODUCTION_LENGTH:
        raise ValueError(
            f"{METADATA_FILENAME} introduction must be at most "
            f"{MAX_INTRODUCTION_LENGTH} characters"
        )
    tags = normalize_tags(payload.get("tags", []))
    validate_role_count(count, min_count, max_count)
    return TemplateMetadata(
        count=count,
        names=tuple(name.strip() for name in names),
        title=title,
        introduction=introduction,
        tags=tags,
    )


def load_template_metadata(
    root: str | Path,
    *,
    min_count: int | None = None,
    max_count: int | None = None,
) -> TemplateMetadata:
    """Read ``metadata.json`` from a payload directory."""
    path = Path(root) / METADATA_FILENAME
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {METADATA_FILENAME}: {exc}") from exc
    return parse_template_metadata(payload, min_count=min_count, max_count=max_count)


def write_template_metadata(root: str | Path, metadata: TemplateMetadata) -> Path:
    """Write ``metadata.json`` through a temp file + ``os.replace``."""
    directory = Path(root)
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / METADATA_FILENAME
    staging = directory / f"{METADATA_FILENAME}.tmp"
    staging.write_text(
        json.dumps(metadata.as_dict(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.replace(staging, target)
    return target


def migrate_roles_to_metadata(
    root: str | Path,
    *,
    title: str = "",
    introduction: str = "",
    tags: tuple[str, ...] = (),
    min_count: int | None = None,
    max_count: int | None = None,
) -> bool:
    """One-time ``roles.json`` -> ``metadata.json`` migration for one payload.

    Returns True when a migration happened, False when there is nothing to do
    (``metadata.json`` already exists). ``roles.json`` is only removed after the
    new file has been written atomically, so a failure leaves the payload usable.
    """
    directory = Path(root)
    if (directory / METADATA_FILENAME).is_file():
        return False
    legacy = directory / LEGACY_ROLES_FILENAME
    if not legacy.is_file():
        raise ValueError(
            f"{directory} has neither {METADATA_FILENAME} nor {LEGACY_ROLES_FILENAME}"
        )
    try:
        data = json.loads(legacy.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"cannot read {LEGACY_ROLES_FILENAME}: {exc}") from exc
    metadata = parse_template_metadata(
        {
            "count": data.get("count") if isinstance(data, dict) else None,
            "names": data.get("names") if isinstance(data, dict) else None,
            "title": title,
            "introduction": introduction,
            "tags": list(tags),
        },
        min_count=min_count,
        max_count=max_count,
    )
    write_template_metadata(directory, metadata)
    legacy.unlink()
    return True


def validate_role_limits(min_count: int, max_count: int) -> None:
    if min_count < 1:
        raise ValueError("MIN_ROLE_COUNT must be at least 1")
    if max_count < min_count:
        raise ValueError("MAX_ROLE_COUNT must be greater than or equal to MIN_ROLE_COUNT")


def validate_role_count(
    count: int, min_count: int | None, max_count: int | None
) -> None:
    if isinstance(count, bool) or not isinstance(count, int) or count < 1:
        raise ValueError("role count must be a positive integer")
    if min_count is not None and count < min_count:
        raise ValueError(f"role count must be at least {min_count}")
    if max_count is not None and count > max_count:
        raise ValueError(f"role count must be at most {max_count}")
