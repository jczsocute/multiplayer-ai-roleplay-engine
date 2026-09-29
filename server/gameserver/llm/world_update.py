import json

from server.gameserver.llm.client import LLMClient
from server.gameserver.llm.prompt_loader import PromptLoader
from server.gameserver.template import same_key_structure


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
        status_schemas = {
            role_id: schema for role_id in self.loader.role_ids
            if (schema := self.loader.character_status_schema(role_id)) is not None
        }
        output_example = {
            "world_state": self.loader.json("world/world_state_schema.json"),
            "character_views": {
                role_id: self.loader.character_view_schema(role_id)
                for role_id in self.loader.role_ids
            },
            "character_status": status_schemas,
        }
        for role_id in self.loader.role_ids:
            display_name = self.loader.character_name(role_id)
            role_sections.append(
                f"""# 角色 {role_id}（{display_name}）设定

{self.loader.character(role_id)}"""
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

{json.dumps(output_example, ensure_ascii=False, indent=2)}
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
        if not isinstance(result.get("world_state"), dict) or not same_key_structure(
            output_example["world_state"], result["world_state"]
        ):
            raise ValueError("world update is missing world_state")
        views = result.get("character_views")
        if not isinstance(views, dict) or set(views) != set(self.loader.role_ids) or not all(
            isinstance(views[role_id], dict) and same_key_structure(
                output_example["character_views"][role_id], views[role_id]
            ) for role_id in self.loader.role_ids
        ):
            raise ValueError("world update has invalid character views")
        statuses = result.get("character_status")
        if not isinstance(statuses, dict) or set(statuses) != set(status_schemas) or not all(
            isinstance(statuses[role_id], dict) and same_key_structure(
                schema, statuses[role_id]
            ) for role_id, schema in status_schemas.items()
        ):
            raise ValueError("world update has invalid character status")
        if set(result) != {"world_state", "character_views", "character_status"}:
            raise ValueError("world update has unexpected fields")
        return result

    @staticmethod
    def _format(value) -> str:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
