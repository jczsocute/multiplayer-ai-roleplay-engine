import unittest
import tempfile
import shutil
from pathlib import Path
from types import SimpleNamespace

from server.gameserver.llm.client import LLMClient
from server.gameserver.llm.prompt_loader import PromptLoader
from server.gameserver.template import validate_template


class FakeCompletions:
    def __init__(self) -> None:
        self.options = []

    async def create(self, **options):
        self.options.append(options)
        return SimpleNamespace(
            choices=[
                SimpleNamespace(
                    finish_reason="stop",
                    message=SimpleNamespace(content='{"ok": true}'),
                )
            ]
        )


class PromptLoaderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.loader = PromptLoader()

    def test_scenario_files_load(self) -> None:
        self.assertIn("世界更新", self.loader.text("prompts/world_update.md"))
        self.assertIn("角色叙事", self.loader.text("prompts/narration.md"))
        self.assertEqual(self.loader.text("prompts/ai_guidelines.md"), "")
        self.assertEqual(self.loader.text("world/world.md"), "")
        self.assertEqual(self.loader.character("P1"), "")
        self.assertEqual(self.loader.character_name("P1"), "角色1")
        self.assertEqual(self.loader.character_name("P2"), "角色2")
        self.assertEqual(self.loader.opening("P1"), "")
        self.assertEqual(self.loader.opening("P2"), "")
        payload = self.loader.json("metadata.json")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(len(payload["names"]), payload["count"])
        self.assertEqual(payload["introduction"], "")
        self.assertIsNone(self.loader.character_status_schema("P1"))
        self.assertIsNone(self.loader.character_status_schema("P2"))
        with self.assertRaisesRegex(ValueError, "unknown role"):
            self.loader.character("P99")
        with self.assertRaisesRegex(ValueError, "unknown role"):
            self.loader.opening("P99")

    def test_missing_opening_has_a_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            shutil.copytree("templates/default", Path(directory) / "story")
            (Path(directory) / "story/characters/1/opening.md").unlink()
            with self.assertRaisesRegex(ValueError, "opening.md"):
                validate_template(Path(directory) / "story")

    def test_world_output_template_has_required_structure(self) -> None:
        self.assertIn("world_information", self.loader.json("world/world_state_schema.json"))
        for role in self.loader.role_ids:
            self.assertIn("world_information", self.loader.character_view_schema(role))
        self.assertFalse((self.loader.root / "schemas").exists())

    def test_initial_state_contains_complete_character_state(self) -> None:
        state = self.loader.json("world/world_state_initial.json")
        self.assertEqual(set(state), {"world_information"})
        self.assertEqual(state["world_information"], "")
        for role in self.loader.role_ids:
            self.assertIsNone(self.loader.character_status_initial(role))

    def test_showcase_examples_load_with_optional_status(self) -> None:
        for directory, names in (
            ("templates/example1", ("路人甲", "路人乙")),
            ("templates/example1_en", ("Passerby A", "Passerby B")),
        ):
            roles = validate_template(directory, min_count=2, max_count=4)
            loader = PromptLoader(directory, roles)
            self.assertEqual(tuple(roles.names.values()), names)
            for role in roles.role_ids:
                self.assertEqual(
                    set(loader.character_status_initial(role)),
                    set(loader.character_status_schema(role)),
                )

    def test_english_scaffold_has_only_generic_content(self) -> None:
        root = Path("templates/default_en")
        roles = validate_template(root, min_count=2, max_count=4)
        loader = PromptLoader(root, roles)
        self.assertEqual(tuple(roles.names.values()), ("Character 1", "Character 2"))
        self.assertEqual(loader.text("world/world.md"), "")
        self.assertEqual(loader.text("prompts/ai_guidelines.md"), "")
        self.assertEqual(loader.json("world/world_state_initial.json"), {"world_information": ""})
        self.assertIn("World update", loader.text("prompts/world_update.md"))
        for role in roles.role_ids:
            self.assertEqual(loader.character(role), "")
            self.assertEqual(loader.opening(role), "")
            self.assertIsNone(loader.character_status_schema(role))

    def test_narration_prompt_requests_plain_text_not_json(self) -> None:
        prompt = self.loader.text("prompts/narration.md")
        self.assertIn("只输出普通文本", prompt)
        self.assertIn("不要 JSON", prompt)
        self.assertNotIn("必须严格输出合法 JSON", prompt)

    async def test_llm_client_only_enables_json_mode_when_requested(self) -> None:
        completions = FakeCompletions()
        llm = LLMClient.__new__(LLMClient)
        llm.model = "test-model"
        llm.client = SimpleNamespace(
            chat=SimpleNamespace(completions=completions)
        )

        await llm.generate("system", "user", json_mode=True, max_tokens=128)
        await llm.generate("system", "user")

        self.assertEqual(
            completions.options[0]["response_format"], {"type": "json_object"}
        )
        self.assertEqual(completions.options[0]["max_tokens"], 128)
        self.assertNotIn("response_format", completions.options[1])


if __name__ == "__main__":
    unittest.main()
