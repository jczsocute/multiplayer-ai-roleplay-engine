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
        public_world_info: object,
        player_view: object,
        player_statusbar: object,
        chat_history: list[dict[str, str]],
    ) -> dict:
        input_text = f"""# 角色设定

{self.loader.character(player_id)}

# 当前角色

{player_id}（{self.loader.character_name(player_id)}）

# 公共世界信息

{self._format(public_world_info)}

# 角色可见信息

{self._format(player_view)}

# 角色当前状态

{self._format(player_statusbar)}

# 历史剧情

{json.dumps(chat_history, ensure_ascii=False)}
"""
        text = await self.llm.generate(
            self.instructions, input_text, max_tokens=self.max_tokens
        )
        return {"text": text, "status": {}}

    @staticmethod
    def _format(value) -> str:
        return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
