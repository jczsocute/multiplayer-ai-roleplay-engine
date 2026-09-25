import json

from server.llm.client import LLMClient
from server.llm.prompt_loader import PromptLoader


class Narrator:
    def __init__(
        self,
        llm: LLMClient,
        loader: PromptLoader | None = None,
        max_tokens: int = 4096,
    ) -> None:
        self.llm = llm
        self.loader = loader or PromptLoader()
        self.instructions = "\n\n".join(
            (
                self.loader.text("prompts/narration.md"),
                self.loader.text("prompts/ai_guidelines.md"),
            )
        )
        self.max_tokens = max_tokens

    async def narrate(
        self,
        player_id: str,
        public_world_info: str,
        player_view: str,
        chat_history: list[dict[str, str]],
    ) -> dict:
        input_text = f"""# 玩家

玩家 {player_id}

# 角色设定

{self.loader.character(player_id)}

# 公共世界信息

{self._format(public_world_info)}

# 玩家私有可见信息

{self._format(player_view)}

# 玩家历史聊天

{json.dumps(chat_history, ensure_ascii=False)}
"""
        text = await self.llm.generate(
            self.instructions, input_text, max_tokens=self.max_tokens
        )
        return {"text": text, "status": {}}

    @staticmethod
    def _format(value) -> str:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
