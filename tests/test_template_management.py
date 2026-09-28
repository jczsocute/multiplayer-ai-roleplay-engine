"""Owner-facing Template management: mine / create / rename / copy / delete."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from server.gameserver.roles import RoleConfig
from server.platform.catalog import (
    copy_template, create_game_snapshot, create_template_from_scaffold,
    delete_owned_template, describe_template, import_template, rename_template,
    set_template_public_owned,
)
from server.platform.database import PlatformDatabase


class TemplateManagementTests(unittest.TestCase):
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
        # The scaffold source, exactly as the repository ships it.
        self.templates_dir.mkdir(parents=True, exist_ok=True)
        shutil.copytree(Path("templates/default"), self.templates_dir / "default")
        self.mine = create_template_from_scaffold(
            self.database, self.templates_dir, self.alice.id, "我的故事", 2, 2, 4,
        )
        self.public = import_template(
            self.database, Path("templates/default"), self.templates_dir,
            self.bob.id, "博主的公开模板", is_public=True,
        )

    def payload(self, template_id: str) -> Path:
        return self.templates_dir / template_id

    # --- create -------------------------------------------------------------

    def test_create_scaffolds_two_three_and_four_roles(self) -> None:
        for count in (2, 3, 4):
            metadata = create_template_from_scaffold(
                self.database, self.templates_dir, self.alice.id,
                f"scaffold{count}", count, 2, 4,
            )
            self.assertFalse(metadata.is_public)
            self.assertEqual(metadata.owner_user_id, self.alice.id)
            config = RoleConfig.load(self.payload(metadata.id))
            self.assertEqual(len(config.role_ids), count)
            self.assertEqual(
                sorted(config.names), [f"P{index}" for index in range(1, count + 1)]
            )
            # A playable payload exists and is a *new* directory, not a copy of
            # love_story or an existing template.
            self.assertTrue((self.payload(metadata.id) / "world" / "initial_state.json").is_file())
            self.assertNotEqual(metadata.id, self.mine.id)

    def test_create_rejects_invalid_names_and_role_counts(self) -> None:
        for bad_name in ("", "   ", "x" * 61):
            with self.assertRaises(ValueError):
                create_template_from_scaffold(
                    self.database, self.templates_dir, self.alice.id, bad_name, 2, 2, 4,
                )
        for bad_count in (1, 5, 0, -1):
            with self.assertRaises(ValueError):
                create_template_from_scaffold(
                    self.database, self.templates_dir, self.alice.id, "n", bad_count, 2, 4,
                )
        with self.assertRaises(ValueError):
            create_template_from_scaffold(
                self.database, self.templates_dir, self.alice.id, "n", True, 2, 4,
            )

    def test_create_leaves_no_staging_directory_behind(self) -> None:
        create_template_from_scaffold(
            self.database, self.templates_dir, self.alice.id, "clean", 3, 2, 4,
        )
        leftovers = [
            path.name for path in self.templates_dir.iterdir()
            if path.name.startswith(".")
        ]
        self.assertEqual(leftovers, [])

    # --- list ---------------------------------------------------------------

    def test_mine_only_lists_owned_templates(self) -> None:
        mine = self.database.list_user_templates(self.alice.id)
        self.assertEqual([value.id for value in mine], [self.mine.id])
        available = self.database.list_available_templates(self.alice.id)
        self.assertEqual(
            {value.id for value in available}, {self.mine.id, self.public.id}
        )

    def test_describe_template_includes_author_and_roles(self) -> None:
        described = describe_template(self.templates_dir, self.mine, "Alice")
        self.assertEqual(described["owner_username"], "Alice")
        self.assertEqual(described["role_count"], 2)
        self.assertEqual(described["role_names"], ["角色1", "角色2"])
        self.assertFalse(described["is_public"])

    # --- rename -------------------------------------------------------------

    def test_payload_title_is_the_script_name(self) -> None:
        """A Script is not defined only by a platform.db row."""
        metadata = create_template_from_scaffold(
            self.database, self.templates_dir, self.alice.id, "新剧本", 2, 2, 4,
        )
        payload = json.loads(
            (self.payload(metadata.id) / "metadata.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["title"], metadata.name)
        self.assertEqual(payload["title"], "新剧本")
        # The description the API serves comes from the payload title.
        described = describe_template(
            self.templates_dir, metadata, "Alice", include_introduction=True
        )
        self.assertEqual(described["name"], "新剧本")

    def test_display_name_follows_the_payload_title(self) -> None:
        from dataclasses import replace as dataclass_replace
        from server.gameserver.roles import load_template_metadata, write_template_metadata

        payload = load_template_metadata(self.payload(self.mine.id))
        write_template_metadata(
            self.payload(self.mine.id), dataclass_replace(payload, title="payload 里的新标题")
        )
        described = describe_template(self.templates_dir, self.mine, "Alice")
        # The row still says 我的故事, but the payload wins for display.
        self.assertEqual(self.database.get_template(self.mine.id).name, "我的故事")
        self.assertEqual(described["name"], "payload 里的新标题")

    def test_rename_updates_payload_and_row_together(self) -> None:
        renamed = rename_template(
            self.database, self.mine.id, self.alice.id, "改名后的剧本",
            self.templates_dir,
        )
        self.assertEqual(renamed.name, "改名后的剧本")
        payload = json.loads(
            (self.payload(self.mine.id) / "metadata.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["title"], "改名后的剧本")
        # Admin reaches the same helper without the owner check.
        admin_renamed = rename_template(
            self.database, self.mine.id, 0, "管理员改名", self.templates_dir,
            require_owner=False,
        )
        self.assertEqual(admin_renamed.name, "管理员改名")
        self.assertEqual(
            json.loads(
                (self.payload(self.mine.id) / "metadata.json").read_text(encoding="utf-8")
            )["title"],
            "管理员改名",
        )

    def test_copy_carries_the_new_title(self) -> None:
        copy = copy_template(self.database, self.mine.id, self.alice.id, self.templates_dir)
        payload = json.loads(
            (self.payload(copy.id) / "metadata.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["title"], copy.name)
        self.assertNotEqual(payload["title"], "我的故事")

    def test_import_gives_the_copy_the_requested_title(self) -> None:
        imported = import_template(
            self.database, Path("templates/default"), self.templates_dir,
            self.alice.id, "导入的剧本",
        )
        payload = json.loads(
            (self.payload(imported.id) / "metadata.json").read_text(encoding="utf-8")
        )
        # The scaffold source's own title must not leak into the imported copy.
        self.assertEqual(payload["title"], "导入的剧本")

    def test_game_snapshot_keeps_the_title(self) -> None:
        game = create_game_snapshot(
            self.database, self.mine.id, self.alice.id, "存档", self.templates_dir,
            self.games_dir,
        )
        payload = json.loads(
            (self.games_dir / game.id / "metadata.json").read_text(encoding="utf-8")
        )
        self.assertEqual(payload["title"], self.mine.name)

    def test_rename_own_template(self) -> None:
        renamed = rename_template(self.database, self.mine.id, self.alice.id, "  改名  ")
        self.assertEqual(renamed.name, "改名")
        self.assertGreaterEqual(renamed.updated_at, self.mine.updated_at)

    def test_cannot_mutate_another_users_template(self) -> None:
        with self.assertRaises(PermissionError):
            rename_template(self.database, self.mine.id, self.bob.id, "stolen")
        with self.assertRaises(PermissionError):
            copy_template(self.database, self.mine.id, self.bob.id, self.templates_dir)
        with self.assertRaises(PermissionError):
            delete_owned_template(
                self.database, self.mine.id, self.bob.id, self.templates_dir
            )
        self.assertIsNotNone(self.database.get_template(self.mine.id))

    # --- visibility ---------------------------------------------------------

    def test_owner_toggles_visibility_both_ways(self) -> None:
        self.assertFalse(self.database.get_template(self.mine.id).is_public)
        published = set_template_public_owned(
            self.database, self.mine.id, self.alice.id, True
        )
        self.assertTrue(published.is_public)
        self.assertGreaterEqual(published.updated_at, self.mine.updated_at)
        self.assertTrue(self.database.get_template(self.mine.id).is_public)

        hidden = set_template_public_owned(
            self.database, self.mine.id, self.alice.id, False
        )
        self.assertFalse(hidden.is_public)

    def test_toggling_visibility_changes_who_can_use_it(self) -> None:
        self.assertEqual(
            [value.id for value in self.database.list_available_templates(self.bob.id)],
            [self.public.id],
        )
        set_template_public_owned(self.database, self.mine.id, self.alice.id, True)
        self.assertEqual(
            sorted(value.id for value in self.database.list_available_templates(self.bob.id)),
            sorted([self.public.id, self.mine.id]),
        )
        set_template_public_owned(self.database, self.mine.id, self.alice.id, False)
        self.assertEqual(
            [value.id for value in self.database.list_available_templates(self.bob.id)],
            [self.public.id],
        )

    def test_cannot_toggle_another_users_template(self) -> None:
        with self.assertRaises(PermissionError):
            set_template_public_owned(self.database, self.mine.id, self.bob.id, True)
        self.assertFalse(self.database.get_template(self.mine.id).is_public)
        with self.assertRaisesRegex(ValueError, "template_not_found"):
            set_template_public_owned(self.database, "tmpl_NOPE", self.alice.id, True)

    # --- copy ---------------------------------------------------------------

    def test_copy_own_template_is_private_and_suffixed(self) -> None:
        first = copy_template(self.database, self.public.id, self.bob.id, self.templates_dir)
        self.assertEqual(first.name, "博主的公开模板_1")
        self.assertFalse(first.is_public)  # public source still copies as private
        self.assertEqual(first.owner_user_id, self.bob.id)
        self.assertNotEqual(first.id, self.public.id)
        self.assertTrue((self.payload(first.id) / "metadata.json").is_file())

        second = copy_template(self.database, self.public.id, self.bob.id, self.templates_dir)
        self.assertEqual(second.name, "博主的公开模板_2")

        renamed = rename_template(self.database, first.id, self.bob.id, "副本")
        self.assertEqual(renamed.name, "副本")
        # Renaming freed `_1`, so the next copy reuses the lowest free suffix.
        third = copy_template(self.database, self.public.id, self.bob.id, self.templates_dir)
        self.assertEqual(third.name, "博主的公开模板_1")
        self.assertEqual(
            sorted(value.name for value in self.database.list_user_templates(self.bob.id)),
            ["副本", "博主的公开模板", "博主的公开模板_1", "博主的公开模板_2"],
        )

    # --- delete -------------------------------------------------------------

    def test_delete_own_template_keeps_snapshot_games_alive(self) -> None:
        game = create_game_snapshot(
            self.database, self.mine.id, self.alice.id, "存档",
            self.templates_dir, self.games_dir,
        )
        (self.games_dir / game.id / "game.db").write_bytes(b"played")

        delete_owned_template(
            self.database, self.mine.id, self.alice.id, self.templates_dir
        )
        self.assertIsNone(self.database.get_template(self.mine.id))
        self.assertFalse(self.payload(self.mine.id).exists())
        # The Game is a snapshot: it survives with its payload and history.
        surviving = self.database.get_game(game.id)
        self.assertIsNotNone(surviving)
        self.assertIsNone(surviving.source_template_id)
        self.assertTrue((self.games_dir / game.id / "game.db").is_file())

    def test_delete_unknown_template_reports_not_found(self) -> None:
        with self.assertRaisesRegex(ValueError, "template_not_found"):
            delete_owned_template(
                self.database, "tmpl_NOPE", self.alice.id, self.templates_dir
            )


if __name__ == "__main__":
    unittest.main()
