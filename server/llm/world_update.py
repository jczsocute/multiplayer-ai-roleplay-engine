import json

from server.llm.client import LLMClient
from server.llm.prompt_loader import PromptLoader


class WorldUpdater:
    def __init__(
        self,
        llm: LLMClient,
        loader: PromptLoader | None = None,
        max_tokens: int = 8192,
    ) -> None:
        self.llm = llm
        self.loader = loader or PromptLoader()
        self.instructions = self.loader.text("prompts/world_update.md")
        self.max_tokens = max_tokens

    async def update(self, current_world_state: str, action_a: str, action_b: str) -> dict:
        input_text = f"""# 创作规范

{self.loader.text("prompts/ai_guidelines.md")}

# 世界设定

{self.loader.text("world/world.md")}

# 角色 A 设定

{self.loader.character("A")}

# 角色 A 状态

{json.dumps(self.loader.statusbar("A"), ensure_ascii=False, indent=2)}

# 角色 B 设定

{self.loader.character("B")}

# 角色 B 状态

{json.dumps(self.loader.statusbar("B"), ensure_ascii=False, indent=2)}

# 当前世界

{self._format(current_world_state)}

# 本轮角色行动

## 角色 A

{action_a}

## 角色 B

{action_b}

# JSON 输出格式示例

{json.dumps(self.loader.json("schemas/world_updater_output.json"), ensure_ascii=False, indent=2)}
"""
        raw_result = await self.llm.generate(
            self.instructions,
            input_text,
            json_mode=True,
            max_tokens=self.max_tokens,
        )
        result = json.loads(raw_result)
        if not isinstance(result, dict):
            raise ValueError("world update must be a JSON object")
        if not isinstance(result.get("world_state"), dict):
            raise ValueError("world update is missing world_state")
        if not isinstance(result.get("public_information"), dict):
            raise ValueError("world update is missing public_information")
        views = result.get("player_views")
        if not isinstance(views, dict) or not all(
            isinstance(views.get(player_id), dict) for player_id in ("A", "B")
        ):
            raise ValueError("world update is missing player views for A and B")
        statusbar = result.get("player_statusbar")
        if not isinstance(statusbar, dict) or not all(
            isinstance(statusbar.get(player_id), dict) for player_id in ("A", "B")
        ):
            raise ValueError("world update is missing player statusbar for A and B")
        return result

    @staticmethod
    def _format(value) -> str:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
