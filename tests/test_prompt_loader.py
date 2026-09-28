import unittest
import tempfile
from pathlib import Path
from types import SimpleNamespace

from server.llm.client import LLMClient
from server.llm.prompt_loader import PromptLoader
from server.roles import RoleConfig


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
        roles = self.loader.json("roles.json")
        self.assertEqual(roles["count"], 2)
        self.assertEqual(len(roles["names"]), roles["count"])
        self.assertIn("生命状态", self.loader.statusbar("P1"))
        self.assertIn("通信设备", self.loader.statusbar("P2"))
        self.assertNotEqual(
            set(self.loader.statusbar("P1")), set(self.loader.statusbar("P2"))
        )
        with self.assertRaisesRegex(ValueError, "unknown role"):
            self.loader.character("P99")
        with self.assertRaisesRegex(ValueError, "unknown role"):
            self.loader.opening("P99")

    def test_missing_opening_has_a_clear_error(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roles = RoleConfig.from_data({"count": 2, "names": ["甲", "乙"]})
            loader = PromptLoader(directory, roles)
            with self.assertRaisesRegex(ValueError, "opening file is missing for role P1"):
                loader.opening("P1")

    def test_world_output_template_has_required_structure(self) -> None:
        template = self.loader.json("schemas/world_updater_output.json")
        self.assertEqual(
            set(template),
            {
                "world_state",
                "public_information",
                "player_views",
                "player_statusbar",
            },
        )
        self.assertEqual(set(template["player_views"]), {"P1", "P2"})
        self.assertEqual(set(template["player_statusbar"]), {"P1", "P2"})
        legacy_name = "world_" + "update_output.json"
        self.assertFalse((self.loader.root / "schemas" / legacy_name).exists())

    def test_initial_state_contains_complete_character_state(self) -> None:
        state = self.loader.json("world/initial_state.json")
        self.assertEqual(set(state["characters"]), {"P1", "P2"})
        for player_id in ("P1", "P2"):
            character = state["characters"][player_id]
            for field in (
                "location",
                "physical_state",
                "mental_state",
                "relationships",
                "inventory",
                "knowledge",
            ):
                self.assertIn(field, character)

    def test_narration_prompt_requests_plain_text_not_json(self) -> None:
        prompt = self.loader.text("prompts/narration.md")
        self.assertIn("只输出普通文本", prompt)
        self.assertIn("不要输出 JSON", prompt)
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
