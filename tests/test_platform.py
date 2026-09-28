import shutil
import sqlite3
import json
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from server.platform.catalog import create_game_snapshot, import_game, import_template
from server.platform.database import PlatformDatabase
from server.platform.web import create_platform_app


class PlatformDatabaseTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.database = PlatformDatabase(self.root / "platform.db")
        self.database.initialize()
        self.alice = self.database.create_user("Alice", "password123")
        self.bob = self.database.create_user("Bob", "password123")

    def test_template_visibility_and_ownership(self) -> None:
        own = self.database.create_template("tmpl_OWN", self.alice.id, "Own")
        public = self.database.create_template(
            "tmpl_PUBLIC", self.bob.id, "Public", is_public=True
        )
        self.database.create_template("tmpl_PRIVATE", self.bob.id, "Private")

        self.assertEqual(self.database.get_template(own.id).owner_user_id, self.alice.id)
        self.assertEqual(
            [item.id for item in self.database.list_public_templates()], [public.id]
        )
        self.assertEqual(
            {item.id for item in self.database.list_available_templates(self.alice.id)},
            {own.id, public.id},
        )

    def test_game_metadata_and_owner_listing(self) -> None:
        template = self.database.create_template("tmpl_ONE", self.alice.id, "Template")
        game = self.database.create_game(
            "game_ONE", self.alice.id, "Save", template.id
        )
        self.assertEqual(game.source_template_id, template.id)
        self.assertEqual(self.database.list_user_games(self.alice.id), [game])
        self.assertEqual(self.database.list_user_games(self.bob.id), [])

    def test_room_owner_and_game_are_unique(self) -> None:
        template = self.database.create_template("tmpl_ONE", self.alice.id, "Template")
        first = self.database.create_game("game_ONE", self.alice.id, "One", template.id)
        second = self.database.create_game("game_TWO", self.alice.id, "Two", template.id)
        self.database.create_room_metadata("ROOM01", self.alice.id, first.id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.create_room_metadata("ROOM02", self.alice.id, second.id)
        with self.assertRaises(sqlite3.IntegrityError):
            self.database.create_room_metadata("ROOM03", self.bob.id, first.id)
        self.database.delete_room("ROOM01")
        self.assertEqual(self.database.list_rooms(), [])

    def test_template_import_and_game_snapshot_are_independent(self) -> None:
        source = self.root / "legacy" / "story"
        shutil.copytree(Path("templates/default"), source)
        templates = self.root / "templates"
        games = self.root / "games"
        imported = import_template(
            self.database, source, templates, self.alice.id, "Story", is_public=True
        )
        self.assertTrue(imported.id.startswith("tmpl_"))
        self.assertTrue(imported.is_public)
        self.assertTrue((templates / imported.id / "metadata.json").is_file())

        game = create_game_snapshot(
            self.database, imported.id, self.alice.id, "Save", templates, games
        )
        original = (games / game.id / "metadata.json").read_text(encoding="utf-8")
        (templates / imported.id / "metadata.json").write_text(
            json.dumps({"count": 2, "names": ["改了", "也改了"],
                        "introduction": "new", "tags": ["new"]}, ensure_ascii=False),
            encoding="utf-8",
        )
        self.assertEqual(
            (games / game.id / "metadata.json").read_text(encoding="utf-8"), original
        )

    def test_import_rejects_missing_source_and_unknown_owner(self) -> None:
        with self.assertRaisesRegex(ValueError, "does not exist"):
            import_template(
                self.database, self.root / "missing", self.root / "templates",
                self.alice.id, "Missing",
            )
        source = self.root / "legacy" / "story"
        shutil.copytree(Path("templates/default"), source)
        with self.assertRaises(sqlite3.IntegrityError):
            import_template(
                self.database, source, self.root / "templates", 9999, "No owner"
            )

    def test_legacy_game_import_copies_payload_and_registers_owner(self) -> None:
        source = self.root / "legacy" / "old_game"
        shutil.copytree(Path("templates/default"), source)
        imported = import_game(
            self.database, source, self.root / "games", self.alice.id, "Old Game"
        )
        self.assertTrue(imported.id.startswith("game_"))
        self.assertEqual(imported.owner_user_id, self.alice.id)
        self.assertIsNone(imported.source_template_id)
        self.assertTrue((self.root / "games" / imported.id / "metadata.json").is_file())

    def test_new_platform_database_copies_legacy_accounts(self) -> None:
        legacy = PlatformDatabase(self.root / "accounts.db")
        legacy.initialize()
        user = legacy.create_user("Legacy", "password123")
        token = legacy.create_session(user.id, 30)
        target = PlatformDatabase(self.root / "migrated" / "platform.db")

        target.initialize(legacy.path)

        self.assertEqual(target.user_by_username("Legacy").id, user.id)
        self.assertEqual(target.resolve_session(token).username, "Legacy")
        self.assertTrue(legacy.path.is_file())


class EmptyPlatformWebTests(unittest.TestCase):
    def test_fresh_platform_registers_first_user_without_game_resources(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = PlatformDatabase(Path(directory) / "platform.db")
            database.initialize()
            app = create_platform_app(database, Path(directory) / "static")
            with TestClient(app) as client:
                self.assertEqual(client.get("/api/me").status_code, 401)
                registered = client.post(
                    "/api/register",
                    json={"username": "Alice", "password": "password123"},
                )
                self.assertEqual(registered.status_code, 201)
                self.assertEqual(client.get("/api/me").json()["username"], "Alice")
                self.assertEqual(client.get("/api/templates").json(), {"templates": []})
                self.assertEqual(client.get("/api/games").json(), {"games": []})
                self.assertEqual(client.get("/api/rooms").json(), {"rooms": []})


if __name__ == "__main__":
    unittest.main()
