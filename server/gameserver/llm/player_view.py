import json

from server.gameserver.llm.client import LLMClient
from server.gameserver.llm.prompt_loader import PromptLoader


class PlayerViewGenerator:
    def __init__(
        self,
        llm: LLMClient,
        loader: PromptLoader | None = None,
    ) -> None:
        self.llm = llm
        self.loader = loader or PromptLoader()
        self.instructions = self.loader.text("prompts/player_view.md")

    async def generate(self, player_id: str, world_state: str) -> str:
        input_text = f"""# AI 创作规范

{self.loader.text("prompts/ai_guidelines.md")}

# 世界设定

{self.loader.text("world/world.md")}

# 角色设定

{self.loader.character(player_id)}

# 当前角色

{player_id}（{self.loader.character_name(player_id)}）

# 世界状态

{world_state if isinstance(world_state, str) else json.dumps(world_state, ensure_ascii=False)}
"""
        return await self.llm.generate(self.instructions, input_text)
