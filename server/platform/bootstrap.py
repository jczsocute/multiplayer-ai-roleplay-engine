"""Developer bootstrap for the bundled legacy Templates.

A directory on disk is not a Platform Template: only rows in
``platform.db.templates`` show up in the Web catalog. This module registers the
bundled legacy content for an existing owner account, explicitly and idempotently.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from server.platform.catalog import has_payload, import_template
from server.platform.database import PlatformDatabase

logger = logging.getLogger(__name__)

# Explicitly listed; never a wildcard scan of templates/* or games/*.
BUNDLED_TEMPLATES: tuple[str, ...] = ("love_story", "three_player_test")

# A legacy Game directory may also hold runtime state; never copy it into a
# Template payload.
LEGACY_GAME_EXCLUDES: tuple[str, ...] = ("game.db",)


@dataclass(frozen=True)
class BundledScript:
    """Summarized metadata for one bundled Script (title + public description)."""

    title: str
    introduction: str
    tags: tuple[str, ...]


# Written from each payload's actual content, so a fresh bootstrap produces the
# same catalog row as an already-migrated payload: `title` becomes both the
# payload `metadata.json.title` and the `platform.db.templates.name` mirror.
# Deliberately short, public-facing text: no hidden plot, no invented setting.
BUNDLED_SCRIPTS: dict[str, BundledScript] = {
    "love_story": BundledScript(
        title="都市夫妻的秘密",
        introduction=(
            "现代都市背景的双人情感剧本。一对年轻夫妻在日常生活中逐渐面对"
            "彼此隐藏的欲望、秘密与边界，关系与心理变化是主要看点。"
            "成人向内容，建议成年玩家游玩。"
        ),
        tags=("成人向", "双人", "现代都市", "情感", "关系心理"),
    ),
    "three_player_test": BundledScript(
        title="气象站的雷雨夜",
        introduction=(
            "三人合作短剧本：偏远山顶气象站遭雷击断电，强雷暴即将引发山洪，"
            "三名角色需要在数个回合内恢复供电、取出信标密码并发出预警。"
            "强调分工、信息交换与有限时间内的取舍。"
        ),
        tags=("三人", "合作", "灾难题材", "短剧本", "现实向"),
    ),
}


def find_bundled_source(
    name: str, templates_dir: Path, games_dir: Path
) -> Path | None:
    """Resolve a bundled name to a payload directory.

    ``templates/<name>`` wins; a legacy ``games/<name>`` payload is accepted as a
    fallback so older multi-role test content can still be registered.
    """
    candidate = templates_dir / name
    if has_payload(candidate):
        return candidate
    fallback = games_dir / name
    if has_payload(fallback):
        return fallback
    return None


@dataclass(frozen=True)
class BootstrapResult:
    """Outcome of one bundled-name bootstrap attempt."""

    name: str
    status: str
    template_id: str | None = None


def bootstrap_templates(
    database: PlatformDatabase,
    templates_dir: Path,
    games_dir: Path,
    owner_user_id: int,
    names: tuple[str, ...] = BUNDLED_TEMPLATES,
) -> list[BootstrapResult]:
    """Import each bundled Template that is not in the catalog yet.

    Idempotent by ``(owner_user_id, name)``: a second run imports nothing.
    """
    results: list[BootstrapResult] = []
    for name in names:
        script = BUNDLED_SCRIPTS.get(name, BundledScript(name, "", ()))
        # Idempotent by title first (the catalog row mirrors the payload title),
        # then by the bundled slug for catalog rows imported before titles existed.
        existing = (
            database.template_by_owner_and_name(owner_user_id, script.title)
            or database.template_by_owner_and_name(owner_user_id, name)
        )
        if existing is not None:
            results.append(BootstrapResult(name, "skipped", existing.id))
            continue
        source = find_bundled_source(name, templates_dir, games_dir)
        if source is None:
            results.append(BootstrapResult(name, "missing"))
            continue
        metadata = import_template(
            database, source, templates_dir, owner_user_id, script.title,
            is_public=True, exclude=LEGACY_GAME_EXCLUDES, title=script.title,
            introduction=script.introduction, tags=script.tags,
        )
        logger.info("Bootstrapped template %s from %s", metadata.id, source)
        results.append(BootstrapResult(name, "imported", metadata.id))
    return results
