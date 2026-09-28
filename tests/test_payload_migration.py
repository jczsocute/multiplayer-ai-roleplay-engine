"""Registered payload migration: Templates, Games and the snapshot invariant."""

import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.gameserver.roles import (
    LEGACY_ROLES_FILENAME, METADATA_FILENAME, RoleConfig, load_template_metadata,
)
from server.platform.catalog import (
    create_game_snapshot, describe_template, import_template, load_payload_metadata,
    migrate_catalog_payloads,
)
from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager
from tests.support import user


def write_legacy(directory: Path, count: int, names: tuple[str, ...]) -> None:
    directory.mkdir(parents=True, exist_ok=True)
    (directory / LEGACY_ROLES_FILENAME).write_text(
        json.dumps({"count": count, "names": list(names)}, ensure_ascii=False),
        encoding="utf-8",
    )


class CatalogMigrationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.templates_dir = self.root / "templates"
        self.games_dir = self.root / "games"
        self.database = PlatformDatabase(self.root / "platform.db")
        self.database.initialize()
        self.alice = self.database.create_user("Alice", "password123")

    def legacy_template_payload(self) -> str:
        """Register a Template whose payload is still roles.json-era."""
        payload = self.root / "sources" / "legacy_story"
        shutil.copytree(Path("templates/default"), payload)
        (payload / METADATA_FILENAME).unlink()
        write_legacy(payload, 2, ("林承", "周璐"))
        # Import migrates the source, so build the registration by hand instead.
        template_id = "tmpl_LEGACY"
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(payload, self.templates_dir / template_id)
        (self.templates_dir / template_id / LEGACY_ROLES_FILENAME).write_text(
            json.dumps({"count": 2, "names": ["林承", "周璐"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.database.create_template(template_id, self.alice.id, "Legacy", is_public=True)
        return template_id

    def test_registered_template_payload_is_migrated(self) -> None:
        template_id = self.legacy_template_payload()
        payload = self.templates_dir / template_id
        self.assertTrue((payload / LEGACY_ROLES_FILENAME).is_file())

        migrated = migrate_catalog_payloads(
            self.database, self.templates_dir, self.games_dir
        )
        self.assertEqual(migrated, [f"template:{template_id}"])
        self.assertFalse((payload / LEGACY_ROLES_FILENAME).exists())
        metadata = load_template_metadata(payload)
        self.assertEqual((metadata.count, metadata.names), (2, ("林承", "周璐")))
        self.assertEqual(metadata.introduction, "")
        self.assertEqual(metadata.tags, ())
        # Idempotent: a second startup migrates nothing.
        self.assertEqual(
            migrate_catalog_payloads(self.database, self.templates_dir, self.games_dir),
            [],
        )

    def test_registered_game_payload_is_migrated_and_keeps_the_snapshot(self) -> None:
        template_id = self.legacy_template_payload()
        migrate_catalog_payloads(self.database, self.templates_dir, self.games_dir)
        game = create_game_snapshot(
            self.database, template_id, self.alice.id, "旧存档",
            self.templates_dir, self.games_dir,
        )
        payload = self.games_dir / game.id
        # Simulate a save created before the migration.
        (payload / METADATA_FILENAME).unlink()
        write_legacy(payload, 2, ("林承", "周璐"))

        migrated = migrate_catalog_payloads(
            self.database, self.templates_dir, self.games_dir
        )
        self.assertEqual(migrated, [f"game:{game.id}"])
        self.assertFalse((payload / LEGACY_ROLES_FILENAME).exists())
        metadata = load_template_metadata(payload)
        self.assertEqual(metadata.names, ("林承", "周璐"))

    def test_game_migration_copies_presentation_from_its_source_template(self) -> None:
        template = import_template(
            self.database, Path("templates/default"), self.templates_dir,
            self.alice.id, "有介绍的模板", is_public=True,
        )
        game = create_game_snapshot(
            self.database, template.id, self.alice.id, "存档",
            self.templates_dir, self.games_dir,
        )
        payload = self.games_dir / game.id
        (payload / METADATA_FILENAME).unlink()
        write_legacy(payload, 2, ("林岚", "周砚"))

        migrate_catalog_payloads(self.database, self.templates_dir, self.games_dir)
        metadata = load_template_metadata(payload)
        source = load_template_metadata(self.templates_dir / template.id)
        self.assertEqual(metadata.introduction, source.introduction)
        self.assertEqual(metadata.tags, source.tags)

    def test_startup_backfills_a_missing_title(self) -> None:
        """An older metadata.json without a title gains one, and the row mirrors it."""
        template = self.database.create_template("tmpl_OLD", self.alice.id, "我的旧剧本")
        payload = self.templates_dir / template.id
        payload.mkdir(parents=True)
        from server.gameserver.roles import TemplateMetadata, write_template_metadata

        # A title-less payload: the shape written before `title` existed.
        (payload / METADATA_FILENAME).write_text(
            json.dumps({"count": 2, "names": ["甲", "乙"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.assertEqual(load_template_metadata(payload).title, "")

        labels = migrate_catalog_payloads(self.database, self.templates_dir, self.games_dir)
        self.assertIn(f"title:{template.id}", labels)
        self.assertEqual(load_template_metadata(payload).title, "我的旧剧本")
        self.assertEqual(self.database.get_template(template.id).name, "我的旧剧本")

        # Idempotent: a second run changes nothing.
        self.assertEqual(
            migrate_catalog_payloads(self.database, self.templates_dir, self.games_dir),
            [],
        )

    def test_startup_summarizes_a_slug_like_title(self) -> None:
        """A bundled slug becomes a real title in payload *and* catalog row."""
        titles = {"love_story": "都市夫妻的秘密"}
        template = self.database.create_template("tmpl_SLUG", self.alice.id, "love_story")
        payload = self.templates_dir / template.id
        payload.mkdir(parents=True)
        (payload / METADATA_FILENAME).write_text(
            json.dumps({"count": 2, "names": ["林承", "周璐"]}, ensure_ascii=False),
            encoding="utf-8",
        )

        migrate_catalog_payloads(
            self.database, self.templates_dir, self.games_dir, titles=titles
        )
        self.assertEqual(load_template_metadata(payload).title, "都市夫妻的秘密")
        renamed = self.database.get_template(template.id)
        self.assertEqual(renamed.name, "都市夫妻的秘密")
        # The payload directory keeps its stable id after the rename.
        self.assertTrue((self.templates_dir / template.id / METADATA_FILENAME).is_file())
        self.assertEqual(
            migrate_catalog_payloads(
                self.database, self.templates_dir, self.games_dir, titles=titles
            ),
            [],
        )

    def test_unregistered_directories_are_never_touched(self) -> None:
        stray = self.games_dir / "game_ORPHAN"
        write_legacy(stray, 2, ("甲", "乙"))
        self.assertEqual(
            migrate_catalog_payloads(self.database, self.templates_dir, self.games_dir),
            [],
        )
        self.assertTrue((stray / LEGACY_ROLES_FILENAME).is_file())
        self.assertFalse((stray / METADATA_FILENAME).exists())

    def test_import_migrates_a_legacy_source(self) -> None:
        source = self.root / "sources" / "old"
        write_legacy(source, 3, ("甲", "乙", "丙"))
        imported = import_template(
            self.database, source, self.templates_dir, self.alice.id, "Legacy import",
            introduction="从旧格式导入", tags=("合作",),
        )
        payload = self.templates_dir / imported.id
        self.assertTrue((payload / METADATA_FILENAME).is_file())
        self.assertFalse((payload / LEGACY_ROLES_FILENAME).exists())
        metadata = load_template_metadata(payload)
        self.assertEqual(metadata.count, 3)
        self.assertEqual(metadata.introduction, "从旧格式导入")
        self.assertEqual(metadata.tags, ("合作",))

    def test_describe_includes_tags_always_and_introduction_on_request(self) -> None:
        template = import_template(
            self.database, Path("templates/default"), self.templates_dir,
            self.alice.id, "基础", is_public=True,
        )
        listed = describe_template(self.templates_dir, template, "Alice")
        self.assertEqual(listed["tags"], ["基础剧本"])
        self.assertEqual(listed["role_names"], ["林岚", "周砚"])
        self.assertNotIn("introduction", listed)
        detailed = describe_template(
            self.templates_dir, template, "Alice", include_introduction=True
        )
        self.assertTrue(detailed["introduction"].startswith("用于创建新剧本"))

    def test_payload_metadata_helper_returns_none_for_a_broken_payload(self) -> None:
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        (self.templates_dir / "tmpl_BROKEN").mkdir()
        self.assertIsNone(load_payload_metadata(self.templates_dir / "tmpl_BROKEN"))


class SnapshotInvariantTests(unittest.TestCase):
    """Editing a Template payload must never reach an existing Game."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.templates_dir = self.root / "templates"
        self.games_dir = self.root / "games"
        self.database = PlatformDatabase(self.root / "platform.db")
        self.database.initialize()
        self.alice = self.database.create_user("Alice", "password123")
        self.template = import_template(
            self.database, Path("templates/default"), self.templates_dir,
            self.alice.id, "森林之夜", is_public=True,
        )

    def test_template_edits_do_not_change_an_existing_game_snapshot(self) -> None:
        game = create_game_snapshot(
            self.database, self.template.id, self.alice.id, "存档",
            self.templates_dir, self.games_dir,
        )
        snapshot = self.games_dir / game.id / METADATA_FILENAME
        before = json.loads(snapshot.read_text(encoding="utf-8"))

        (self.templates_dir / self.template.id / METADATA_FILENAME).write_text(
            json.dumps({
                "count": 3, "names": ["改了", "也改了", "还是改了"],
                "introduction": "新的介绍", "tags": ["新标签"],
            }, ensure_ascii=False),
            encoding="utf-8",
        )

        self.assertEqual(
            json.loads(snapshot.read_text(encoding="utf-8")), before
        )
        self.assertEqual(load_template_metadata(self.games_dir / game.id).names,
                         ("林岚", "周砚"))


class MigratedGameRuntimeTests(unittest.IsolatedAsyncioTestCase):
    """A migrated old Game must still load, recover, play, retry and roll back."""

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.templates_dir = self.root / "templates"
        self.games_dir = self.root / "games"
        self.database = PlatformDatabase(self.root / "platform.db")
        self.database.initialize()
        self.owner = self.database.create_user("Alice", "password123")

        payload = self.games_dir / "game_OLD"
        shutil.copytree(Path("templates/default"), payload)
        (payload / METADATA_FILENAME).unlink()
        write_legacy(payload, 2, ("林岚", "周砚"))
        self.database.create_game("game_OLD", self.owner.id, "旧存档")

        migrated = migrate_catalog_payloads(
            self.database, self.templates_dir, self.games_dir
        )
        self.assertIn("game:game_OLD", migrated)

        self.updates: list[int] = []
        manager = RoomManager(
            self.database, self.games_dir, self.templates_dir, self.factory
        )
        self.room = await manager.create_room_from_game(self.owner, "game_OLD")
        self.socket = RecordingConnection()

    async def factory(self, path: Path, owner_user_id: int) -> GameServer:
        roles = RoleConfig.load(path)
        database = Database(str(path / "game.db"), roles.role_ids)
        initial_state = json.loads(
            (path / "world" / "initial_state.json").read_text(encoding="utf-8")
        )
        await database.initialize(initial_state)
        server = GameServer(
            database, StubWorldUpdater(), StubViews(), StubNarrator(),
            round_number=await database.current_round(),
            scenario_name=path.name, room_key="", owner_user_id=owner_user_id,
            role_config=roles, max_users=10,
        )
        return server

    async def test_migrated_game_loads_recovers_and_plays(self) -> None:
        server = self.room.game_server
        self.assertEqual(server.role_ids, ("P1", "P2"))
        self.assertEqual(server.character_names, {"P1": "林岚", "P2": "周砚"})
        await server.recover_round()

        await server.sessions.join(self.owner, self.socket)
        await server.sessions.join(user(2, "Bob"), RecordingConnection())
        await server.sessions.assign_roles({"P1": 1, "P2": 2})

        await server._handle_command(1, self.socket, json.dumps({"type": "action", "text": "观察"}))
        await server._handle_command(1, self.socket, json.dumps({"type": "submit"}))
        await server._handle_command(2, self.socket, json.dumps({"type": "action", "text": "前进"}))
        await server._handle_command(2, self.socket, json.dumps({"type": "submit"}))
        self.assertEqual(server.rounds.round_number, 2)

        await server.retry_round(1)
        await server.rollback_to_round(1)
        self.assertEqual(server.rounds.round_number, 2)
        self.assertEqual(load_template_metadata(self.games_dir / "game_OLD").count, 2)


class RecordingConnection:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))

    async def close(self, code: int = 1000) -> None:
        return None


class StubWorldUpdater:
    async def update(self, current_world_state, actions):
        return {
            "world_state": {"actions": actions},
            "public_information": {"ok": True},
            "player_views": {role: {"role": role} for role in actions},
            "player_statusbar": {role: {"ready": True} for role in actions},
        }


class StubNarrator:
    async def narrate(self, role, public, view, statusbar, history):
        return {"text": f"narration for {role}", "status": {}}


class StubViews:
    async def generate(self, role, world):
        raise AssertionError("WorldUpdater supplies views")


if __name__ == "__main__":
    unittest.main()
