"""Payload metadata: `metadata.json` loading, validation and legacy migration."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.gameserver.roles import (
    MAX_TITLE_LENGTH,
    LEGACY_ROLES_FILENAME, MAX_INTRODUCTION_LENGTH, MAX_TAGS, MAX_TAG_LENGTH,
    METADATA_FILENAME, RoleConfig, TemplateMetadata, load_template_metadata,
    migrate_roles_to_metadata, normalize_tags, parse_template_metadata,
    write_template_metadata,
)


class TemplateMetadataParsingTests(unittest.TestCase):
    def test_reads_count_names_introduction_and_tags(self) -> None:
        metadata = parse_template_metadata({
            "count": 2,
            "names": ["林岚", "周砚"],
            "introduction": "  一段介绍  ",
            "tags": ["情感", "双人"],
        })
        self.assertEqual(metadata.count, 2)
        self.assertEqual(metadata.names, ("林岚", "周砚"))
        self.assertEqual(metadata.introduction, "一段介绍")
        self.assertEqual(metadata.tags, ("情感", "双人"))
        self.assertEqual(metadata.role_ids, ("P1", "P2"))

    def test_introduction_and_tags_default_to_empty(self) -> None:
        metadata = parse_template_metadata({"count": 2, "names": ["甲", "乙"]})
        self.assertEqual(metadata.introduction, "")
        self.assertEqual(metadata.tags, ())
        self.assertEqual(metadata.title, "")
        self.assertEqual(
            metadata.as_dict(),
            {"count": 2, "names": ["甲", "乙"], "title": "",
             "introduction": "", "tags": []},
        )

    def test_reads_and_trims_the_title(self) -> None:
        metadata = parse_template_metadata(
            {"count": 2, "names": ["甲", "乙"], "title": "  气象站的雷雨夜  "}
        )
        self.assertEqual(metadata.title, "气象站的雷雨夜")
        self.assertEqual(metadata.as_dict()["title"], "气象站的雷雨夜")
        # The payload owns the title; the catalog row is only a fallback.
        self.assertEqual(metadata.display_title("catalog-name"), "气象站的雷雨夜")
        empty = parse_template_metadata({"count": 2, "names": ["甲", "乙"]})
        self.assertEqual(empty.display_title("catalog-name"), "catalog-name")

    def test_rejects_invalid_titles(self) -> None:
        for payload in (
            {"count": 2, "names": ["甲", "乙"], "title": 5},
            {"count": 2, "names": ["甲", "乙"], "title": ["x"]},
            {"count": 2, "names": ["甲", "乙"], "title": "x" * (MAX_TITLE_LENGTH + 1)},
        ):
            with self.assertRaises(ValueError):
                parse_template_metadata(payload)
        # Exactly at the limit is fine.
        longest = parse_template_metadata(
            {"count": 2, "names": ["甲", "乙"], "title": "x" * MAX_TITLE_LENGTH}
        )
        self.assertEqual(len(longest.title), MAX_TITLE_LENGTH)

    def test_count_names_keep_the_legacy_payload_shape(self) -> None:
        """`count` is an int and `names` an ordered list, exactly as before."""
        metadata = parse_template_metadata({"count": 3, "names": ["a", "b", "c"]})
        self.assertIsInstance(metadata.count, int)
        self.assertEqual(list(metadata.names), ["a", "b", "c"])
        config = metadata.to_role_config()
        self.assertIsInstance(config, RoleConfig)
        self.assertEqual(config.names, {"P1": "a", "P2": "b", "P3": "c"})

    def test_rejects_invalid_count_and_names(self) -> None:
        for payload in (
            {"count": 0, "names": []},
            {"count": -1, "names": []},
            {"count": True, "names": ["a"]},
            {"count": "2", "names": ["a", "b"]},
            {"count": 2, "names": ["a"]},
            {"count": 2, "names": ["a", "b", "c"]},
            {"count": 2, "names": "ab"},
            {"count": 2, "names": ["a", "   "]},
            {"count": 2, "names": ["a", 5]},
            {"count": 2, "names": None},
            "not a dict",
            None,
        ):
            with self.assertRaises(ValueError, msg=payload):
                parse_template_metadata(payload)

    def test_respects_deployment_role_limits(self) -> None:
        with self.assertRaises(ValueError):
            parse_template_metadata({"count": 5, "names": list("abcde")},
                                    min_count=2, max_count=4)
        with self.assertRaises(ValueError):
            parse_template_metadata({"count": 1, "names": ["a"]}, min_count=2, max_count=4)
        parsed = parse_template_metadata(
            {"count": 3, "names": list("abc")}, min_count=2, max_count=4
        )
        self.assertEqual(parsed.count, 3)

    def test_rejects_invalid_introduction(self) -> None:
        for value in (5, ["text"], {"a": 1}, None):
            with self.assertRaises(ValueError, msg=value):
                parse_template_metadata(
                    {"count": 2, "names": ["a", "b"], "introduction": value}
                )
        with self.assertRaises(ValueError):
            parse_template_metadata({
                "count": 2, "names": ["a", "b"],
                "introduction": "x" * (MAX_INTRODUCTION_LENGTH + 1),
            })
        allowed = parse_template_metadata({
            "count": 2, "names": ["a", "b"],
            "introduction": "x" * MAX_INTRODUCTION_LENGTH,
        })
        self.assertEqual(len(allowed.introduction), MAX_INTRODUCTION_LENGTH)

    def test_normalizes_tags(self) -> None:
        self.assertEqual(
            normalize_tags(["  情感  ", "", "   ", "双人", "情感"]), ("情感", "双人")
        )
        self.assertEqual(normalize_tags([]), ())
        self.assertEqual(normalize_tags(None), ())
        # Order is preserved for the surviving tags.
        self.assertEqual(normalize_tags(["b", "a", "b"]), ("b", "a"))

    def test_rejects_invalid_tags(self) -> None:
        for value in ("情感", 5, {"情感": True}, ["ok", 5]):
            with self.assertRaises(ValueError, msg=value):
                parse_template_metadata({"count": 2, "names": ["a", "b"], "tags": value})
        with self.assertRaises(ValueError):
            parse_template_metadata({
                "count": 2, "names": ["a", "b"], "tags": ["x" * (MAX_TAG_LENGTH + 1)],
            })
        with self.assertRaises(ValueError):
            parse_template_metadata({
                "count": 2, "names": ["a", "b"],
                "tags": [f"tag{index}" for index in range(MAX_TAGS + 1)],
            })
        # Duplicates collapse before the cap, so 11 entries may still be fine.
        allowed = parse_template_metadata({
            "count": 2, "names": ["a", "b"],
            "tags": [f"tag{index}" for index in range(MAX_TAGS)] + ["tag0"],
        })
        self.assertEqual(len(allowed.tags), MAX_TAGS)

    def test_load_reports_a_clear_error_for_a_missing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "cannot read metadata.json"):
                load_template_metadata(tmp)

    def test_write_then_load_round_trips(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            written = write_template_metadata(
                tmp, TemplateMetadata(count=2, names=("甲", "乙"), title="雷雨夜",
                                      introduction="hi", tags=("x",))
            )
            self.assertEqual(written, Path(tmp) / METADATA_FILENAME)
            self.assertEqual(load_template_metadata(tmp).introduction, "hi")
            payload = json.loads(written.read_text(encoding="utf-8"))
            self.assertEqual(
                sorted(payload), ["count", "introduction", "names", "tags", "title"]
            )
            self.assertEqual(payload["title"], "雷雨夜")
            # No temp file survives a successful write.
            self.assertEqual(
                sorted(path.name for path in Path(tmp).iterdir()), [METADATA_FILENAME]
            )


class LegacyMigrationTests(unittest.TestCase):
    def legacy_dir(self, count: int = 2, names: tuple[str, ...] = ("林承", "周璐")) -> Path:
        directory = Path(tempfile.mkdtemp())
        self.addCleanup(lambda: __import__("shutil").rmtree(directory, ignore_errors=True))
        (directory / LEGACY_ROLES_FILENAME).write_text(
            json.dumps({"count": count, "names": list(names)}, ensure_ascii=False),
            encoding="utf-8",
        )
        return directory

    def test_migrates_count_and_names_and_adds_presentation(self) -> None:
        directory = self.legacy_dir()
        migrated = migrate_roles_to_metadata(
            directory, introduction="介绍", tags=("双人",)
        )
        self.assertTrue(migrated)
        self.assertFalse((directory / LEGACY_ROLES_FILENAME).exists())
        metadata = load_template_metadata(directory)
        self.assertEqual(metadata.count, 2)
        self.assertEqual(metadata.names, ("林承", "周璐"))
        self.assertEqual(metadata.introduction, "介绍")
        self.assertEqual(metadata.tags, ("双人",))

    def test_migration_records_the_title(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = self.legacy_dir()
            self.assertTrue(
                migrate_roles_to_metadata(root, title="都市夫妻的秘密")
            )
            metadata = load_template_metadata(root)
            self.assertEqual(metadata.title, "都市夫妻的秘密")
            self.assertEqual(metadata.names, ("林承", "周璐"))

    def test_migration_defaults_to_empty_introduction_and_tags(self) -> None:
        directory = self.legacy_dir(count=3, names=("甲", "乙", "丙"))
        migrate_roles_to_metadata(directory)
        metadata = load_template_metadata(directory)
        self.assertEqual(metadata.count, 3)
        self.assertEqual(metadata.names, ("甲", "乙", "丙"))
        self.assertEqual(metadata.introduction, "")
        self.assertEqual(metadata.tags, ())

    def test_migration_is_idempotent(self) -> None:
        directory = self.legacy_dir()
        self.assertTrue(migrate_roles_to_metadata(directory, introduction="first"))
        before = (directory / METADATA_FILENAME).read_text(encoding="utf-8")
        self.assertFalse(migrate_roles_to_metadata(directory, introduction="second"))
        self.assertEqual(
            (directory / METADATA_FILENAME).read_text(encoding="utf-8"), before
        )

    def test_migration_keeps_roles_json_when_the_write_fails(self) -> None:
        directory = self.legacy_dir()
        with patch("server.gameserver.roles.os.replace", side_effect=OSError("disk full")):
            with self.assertRaises(OSError):
                migrate_roles_to_metadata(directory, introduction="x")
        self.assertTrue((directory / LEGACY_ROLES_FILENAME).is_file())
        self.assertFalse((directory / METADATA_FILENAME).is_file())
        # The legacy payload is still usable for a later retry.
        self.assertTrue(migrate_roles_to_metadata(directory, introduction="x"))
        self.assertEqual(load_template_metadata(directory).introduction, "x")

    def test_migration_reports_a_payload_with_neither_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaisesRegex(ValueError, "has neither"):
                migrate_roles_to_metadata(tmp)

    def test_migration_rejects_a_corrupt_legacy_file(self) -> None:
        directory = self.legacy_dir()
        (directory / LEGACY_ROLES_FILENAME).write_text("{not json", encoding="utf-8")
        with self.assertRaises(ValueError):
            migrate_roles_to_metadata(directory)
        self.assertTrue((directory / LEGACY_ROLES_FILENAME).is_file())


class BundledPayloadTests(unittest.TestCase):
    def test_the_tracked_default_template_uses_metadata_json(self) -> None:
        root = Path("templates/default")
        self.assertTrue((root / METADATA_FILENAME).is_file())
        self.assertFalse((root / LEGACY_ROLES_FILENAME).exists())
        metadata = load_template_metadata(root)
        self.assertEqual(metadata.count, 2)
        self.assertEqual(metadata.names, ("路人甲", "路人乙"))
        self.assertTrue(metadata.introduction)
        self.assertIn("石头剪刀布", metadata.tags)
        self.assertTrue(metadata.title)

    def test_bundled_legacy_names_have_real_descriptions(self) -> None:
        from server.platform.bootstrap import BUNDLED_SCRIPTS, BUNDLED_TEMPLATES

        titles = set()
        for name in BUNDLED_TEMPLATES:
            script = BUNDLED_SCRIPTS[name]
            # The slug-like name is summarized into a real Script title.
            self.assertTrue(script.title.strip(), name)
            self.assertNotEqual(script.title, name, name)
            self.assertLessEqual(len(script.title), MAX_TITLE_LENGTH)
            titles.add(script.title)
            self.assertTrue(script.introduction.strip(), name)
            self.assertLessEqual(len(script.introduction), MAX_INTRODUCTION_LENGTH)
            self.assertTrue(2 <= len(script.tags) <= 5, name)
            # `count`/`names` must not leak into the presentation fields.
            self.assertNotIn("count", script.introduction)
            self.assertTrue(all(len(tag) <= MAX_TAG_LENGTH for tag in script.tags), name)
            self.assertEqual(len(set(script.tags)), len(script.tags), name)
        self.assertEqual(len(titles), len(BUNDLED_TEMPLATES))


if __name__ == "__main__":
    unittest.main()
