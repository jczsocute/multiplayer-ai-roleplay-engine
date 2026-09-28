import json

from server.gameserver.llm.client import LLMClient
from server.gameserver.llm.prompt_loader import PromptLoader


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

    async def update(
        self, current_world_state: str, actions: dict[str, str]
    ) -> dict:
        missing_actions = [role_id for role_id in self.loader.role_ids if role_id not in actions]
        if missing_actions:
            raise ValueError(f"missing actions for roles: {', '.join(missing_actions)}")
        role_sections = []
        action_sections = []
        for role_id in self.loader.role_ids:
            display_name = self.loader.character_name(role_id)
            role_sections.append(
                f"""# 角色 {role_id}（{display_name}）设定

{self.loader.character(role_id)}

# 角色 {role_id}（{display_name}）状态栏定义

{json.dumps(self.loader.statusbar(role_id), ensure_ascii=False, indent=2)}"""
            )
            action_sections.append(
                f"""## {role_id}（{display_name}）

{actions[role_id]}"""
            )
        input_text = f"""# 创作规范

{self.loader.text("prompts/ai_guidelines.md")}

# 世界设定

{self.loader.text("world/world.md")}

{chr(10).join(role_sections)}

# 当前世界

{self._format(current_world_state)}

# 本轮角色行动

{chr(10).join(action_sections)}

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
            isinstance(views.get(role_id), dict) for role_id in self.loader.role_ids
        ):
            raise ValueError("world update is missing one or more role views")
        statusbar = result.get("player_statusbar")
        if not isinstance(statusbar, dict) or not all(
            isinstance(statusbar.get(role_id), dict) for role_id in self.loader.role_ids
        ):
            raise ValueError("world update is missing one or more role statusbars")
        return result

    @staticmethod
    def _format(value) -> str:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
