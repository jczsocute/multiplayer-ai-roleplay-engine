"""Owner-facing Game management: rename / copy / delete and file consistency."""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.platform.catalog import (
    copy_game, create_game_snapshot, delete_game, import_template, next_copy_name,
    rename_template,
)
from server.platform.database import PlatformDatabase


class GameManagementTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.templates_dir = self.root / "templates"
        self.games_dir = self.root / "games"
        self.database = PlatformDatabase(self.root / "platform.db")
        self.database.initialize()
        self.alice = self.database.create_user("Alice", "password123")
        self.bob = self.database.create_user("Bob", "password123")
        self.template = import_template(
            self.database, Path("templates/default"), self.templates_dir,
            self.alice.id, "Default Story", is_public=True,
        )
        self.game = create_game_snapshot(
            self.database, self.template.id, self.alice.id, "love_story",
            self.templates_dir, self.games_dir,
        )

    def payload(self, game_id: str) -> Path:
        return self.games_dir / game_id

    def test_snapshot_copy_failure_removes_staging_without_metadata(self) -> None:
        def partial_copy(_source: Path, target: Path) -> None:
            target.mkdir()
            (target / "partial").write_text("partial")
            raise OSError("copy failed")

        with patch("server.platform.catalog.shutil.copytree", side_effect=partial_copy):
            with self.assertRaises(OSError):
                create_game_snapshot(self.database, self.template.id, self.alice.id,
                                     "failed", self.templates_dir, self.games_dir)
        self.assertEqual([game.name for game in self.database.list_user_games(self.alice.id)], ["love_story"])
        self.assertEqual(sorted(path.name for path in self.games_dir.iterdir()), [self.game.id])
        self.assertTrue((self.templates_dir / self.template.id / "metadata.json").is_file())

    def test_snapshot_rename_failure_rolls_back_metadata_and_staging(self) -> None:
        original_rename = Path.rename

        def fail_staging_rename(path: Path, target: Path) -> Path:
            if path.name.endswith(".creating"):
                raise OSError("rename failed")
            return original_rename(path, target)

        with patch.object(Path, "rename", fail_staging_rename):
            with self.assertRaises(OSError):
                create_game_snapshot(self.database, self.template.id, self.alice.id,
                                     "failed", self.templates_dir, self.games_dir)
        self.assertEqual([game.name for game in self.database.list_user_games(self.alice.id)], ["love_story"])
        self.assertEqual(sorted(path.name for path in self.games_dir.iterdir()), [self.game.id])

    # --- naming -------------------------------------------------------------

    def test_copy_name_uses_incrementing_suffix(self) -> None:
        self.assertEqual(next_copy_name("love_story", []), "love_story_1")
        self.assertEqual(next_copy_name("love_story", ["love_story_1"]), "love_story_2")
        self.assertEqual(
            next_copy_name("love_story", ["love_story_1", "love_story_2"]),
            "love_story_3",
        )
        # Only the owner's own names matter; unrelated names are ignored.
        self.assertEqual(next_copy_name("save", ["other"]), "save_1")

    # --- rename -------------------------------------------------------------

    def test_rename_own_game(self) -> None:
        before = self.database.get_game(self.game.id)
        renamed = rename_game_and_get(self.database, self.game.id, self.alice.id, "  新名字  ")
        self.assertEqual(renamed.name, "新名字")
        self.assertGreaterEqual(renamed.updated_at, before.updated_at)

    def test_cannot_rename_someone_elses_game(self) -> None:
        with self.assertRaises(PermissionError):
            rename_game_and_get(self.database, self.game.id, self.bob.id, "stolen")
        self.assertEqual(self.database.get_game(self.game.id).name, "love_story")

    def test_rename_rejects_empty_and_too_long_names(self) -> None:
        for bad in ("", "   ", "x" * 61):
            with self.assertRaises(ValueError):
                rename_game_and_get(self.database, self.game.id, self.alice.id, bad)

    def test_rename_allows_duplicate_names_for_the_same_owner(self) -> None:
        other = create_game_snapshot(
            self.database, self.template.id, self.alice.id, "second",
            self.templates_dir, self.games_dir,
        )
        rename_game_and_get(self.database, other.id, self.alice.id, "love_story")
        names = [game.name for game in self.database.list_user_games(self.alice.id)]
        self.assertEqual(names.count("love_story"), 2)

    # --- copy ---------------------------------------------------------------

    def test_copy_game_duplicates_metadata_and_payload(self) -> None:
        # A played save has a game.db next to its payload; both must be copied.
        (self.payload(self.game.id) / "game.db").write_bytes(b"sqlite-state")
        copied = copy_game(self.database, self.game.id, self.alice.id, self.games_dir)
        self.assertNotEqual(copied.id, self.game.id)
        self.assertEqual(copied.name, "love_story_1")
        self.assertEqual(copied.owner_user_id, self.alice.id)
        self.assertEqual(copied.source_template_id, self.template.id)
        for name in ("metadata.json", "game.db"):
            self.assertTrue((self.payload(copied.id) / name).is_file(), name)
        self.assertEqual(
            (self.payload(copied.id) / "game.db").read_bytes(), b"sqlite-state"
        )
        # The original keeps its own files untouched.
        self.assertTrue((self.payload(self.game.id) / "game.db").is_file())

    def test_copy_names_increment(self) -> None:
        first = copy_game(self.database, self.game.id, self.alice.id, self.games_dir)
        second = copy_game(self.database, self.game.id, self.alice.id, self.games_dir)
        self.assertEqual((first.name, second.name), ("love_story_1", "love_story_2"))

    def test_cannot_copy_someone_elses_game(self) -> None:
        with self.assertRaises(PermissionError):
            copy_game(self.database, self.game.id, self.bob.id, self.games_dir)
        self.assertEqual(len(self.database.list_user_games(self.bob.id)), 0)

    def test_cannot_copy_an_active_game(self) -> None:
        self.database.create_room_metadata("ROOM01", self.alice.id, self.game.id)
        with self.assertRaisesRegex(ValueError, "game_is_active"):
            copy_game(self.database, self.game.id, self.alice.id, self.games_dir)
        self.assertEqual(len(self.database.list_user_games(self.alice.id)), 1)

    # --- delete -------------------------------------------------------------

    def test_delete_inactive_game_removes_metadata_and_files(self) -> None:
        delete_game(self.database, self.game.id, self.alice.id, self.games_dir)
        self.assertIsNone(self.database.get_game(self.game.id))
        self.assertFalse(self.payload(self.game.id).exists())
        self.assertEqual(self.database.list_user_games(self.alice.id), [])

    def test_cannot_delete_someone_elses_game(self) -> None:
        with self.assertRaises(PermissionError):
            delete_game(self.database, self.game.id, self.bob.id, self.games_dir)
        self.assertIsNotNone(self.database.get_game(self.game.id))
        self.assertTrue(self.payload(self.game.id).is_dir())

    def test_cannot_delete_an_active_game(self) -> None:
        self.database.create_room_metadata("ROOM02", self.alice.id, self.game.id)
        with self.assertRaisesRegex(ValueError, "game_is_active"):
            delete_game(self.database, self.game.id, self.alice.id, self.games_dir)
        self.assertIsNotNone(self.database.get_game(self.game.id))
        self.assertTrue(self.payload(self.game.id).is_dir())

    def test_delete_survives_a_missing_payload_directory(self) -> None:
        shutil.rmtree(self.payload(self.game.id))
        delete_game(self.database, self.game.id, self.alice.id, self.games_dir)
        self.assertIsNone(self.database.get_game(self.game.id))

    def test_touch_game_updates_updated_at_only(self) -> None:
        before = self.database.get_game(self.game.id)
        self.database.touch_game(self.game.id)
        after = self.database.get_game(self.game.id)
        self.assertEqual(after.name, before.name)
        self.assertGreaterEqual(after.updated_at, before.updated_at)

    def test_game_is_active_reflects_room_rows(self) -> None:
        self.assertFalse(self.database.game_is_active(self.game.id))
        self.database.create_room_metadata("ROOM03", self.alice.id, self.game.id)
        self.assertTrue(self.database.game_is_active(self.game.id))
        self.database.delete_room("ROOM03")
        self.assertFalse(self.database.game_is_active(self.game.id))


def rename_game_and_get(database, game_id, owner_id, name):
    """Owner-checked rename helper mirroring the HTTP handler."""
    game = database.get_game(game_id)
    if game is None:
        raise ValueError("game_not_found")
    if game.owner_user_id != owner_id:
        raise PermissionError("forbidden")
    return database.rename_game(game_id, name)


if __name__ == "__main__":
    unittest.main()
