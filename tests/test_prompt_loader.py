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
        self.assertIn("世界管理者", self.loader.text("prompts/world_update.md"))
        self.assertIn("叙事者", self.loader.text("prompts/narration.md"))
        self.assertIn("AI 创作规范", self.loader.text("prompts/ai_guidelines.md"))
        self.assertIn("世界设定", self.loader.text("world/world.md"))
        self.assertIn("角色 P1", self.loader.character("P1"))
        self.assertEqual(self.loader.character_name("P1"), "林岚")
        self.assertEqual(self.loader.character_name("P2"), "周砚")
        self.assertIn("暴雨", self.loader.opening("P1"))
        self.assertIn("机房", self.loader.opening("P2"))
        payload = self.loader.json("metadata.json")
        self.assertEqual(payload["count"], 2)
        self.assertEqual(len(payload["names"]), payload["count"])
        self.assertTrue(payload["introduction"])
        self.assertIn("生命状态", self.loader.character_status_schema("P1"))
        self.assertIn("通信设备", self.loader.character_status_schema("P2"))
        self.assertNotEqual(
            set(self.loader.character_status_schema("P1")), set(self.loader.character_status_schema("P2"))
        )
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
        self.assertIn("风暴", state["world_information"])
        for role in self.loader.role_ids:
            self.assertEqual(
                set(self.loader.character_status_initial(role)),
                set(self.loader.character_status_schema(role)),
            )

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
