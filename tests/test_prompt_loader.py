import unittest
from types import SimpleNamespace

from server.llm.client import LLMClient
from server.llm.prompt_loader import PromptLoader


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
        self.assertIn("玩家 A", self.loader.character("A"))
        self.assertEqual(self.loader.character_name("A"), "林岚")
        self.assertIn("生命状态", self.loader.statusbar("A"))
        self.assertIn("通信设备", self.loader.statusbar("B"))
        self.assertNotEqual(
            set(self.loader.statusbar("A")), set(self.loader.statusbar("B"))
        )

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
        self.assertEqual(set(template["player_views"]), {"A", "B"})
        self.assertEqual(set(template["player_statusbar"]), {"A", "B"})
        legacy_name = "world_" + "update_output.json"
        self.assertFalse((self.loader.root / "schemas" / legacy_name).exists())

    def test_initial_state_contains_complete_character_state(self) -> None:
        state = self.loader.json("world/initial_state.json")
        self.assertEqual(set(state["characters"]), {"A", "B"})
        for player_id in ("A", "B"):
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
