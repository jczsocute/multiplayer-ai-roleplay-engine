"""The single editable Template payload format and its small validator."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path

from server.gameserver.llm.prompt_loader import PromptLoader
from server.gameserver.template import validate_template
from server.platform.scenario_manager import ScenarioManager


class TemplateFormatTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / "story"
        shutil.copytree("templates/default", self.root)

    def test_roles_map_to_numbered_directories(self) -> None:
        ScenarioManager.scaffold_roles(self.root, 4, title="Four Roles")
        roles = validate_template(self.root, min_count=2, max_count=4)
        loader = PromptLoader(str(self.root), roles)
        self.assertEqual(roles.role_ids, ("P1", "P2", "P3", "P4"))
        for index, role in enumerate(roles.role_ids, 1):
            self.assertIn(f"角色 P{index}", loader.character(role))
            self.assertIn(f"角色{index}", loader.opening(role))

    def test_rejects_missing_role_and_legacy_layout(self) -> None:
        shutil.rmtree(self.root / "characters/2")
        with self.assertRaisesRegex(ValueError, "exactly 1..N"):
            validate_template(self.root)
        shutil.copytree("templates/default/characters/2", self.root / "characters/2")
        (self.root / "characters/player_1.md").write_text("old")
        with self.assertRaisesRegex(ValueError, "legacy Template layout"):
            validate_template(self.root)

    def test_rejects_invalid_schema_or_initial(self) -> None:
        schema = self.root / "characters/1/character_view_schema.json"
        schema.write_text("[]", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "JSON root must be an object"):
            validate_template(self.root)
        schema.write_text('{"seen": "<what is seen>"}', encoding="utf-8")
        status = self.root / "characters/1/character_status_initial.json"
        status.write_text(json.dumps({"extra": "value"}), encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "does not match schema keys"):
            validate_template(self.root)

    def test_status_schema_is_optional_but_requires_matching_initial(self) -> None:
        schema = self.root / "characters/2/character_status_schema.json"
        initial = self.root / "characters/2/character_status_initial.json"
        initial.unlink()
        with self.assertRaisesRegex(ValueError, "invalid JSON file"):
            validate_template(self.root)
        schema.unlink()
        validate_template(self.root)
        self.assertIsNone(PromptLoader(str(self.root)).character_status_schema("P2"))
