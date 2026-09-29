import json

from server.gameserver.llm.client import LLMClient
from server.gameserver.llm.prompt_loader import PromptLoader


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
        role_id: str,
        character_view: object,
        character_status: object | None,
        chat_history: list[dict[str, str]],
    ) -> dict:
        status_section = (
            f"\n# 角色当前状态\n\n{self._format(character_status)}\n"
            if character_status is not None else ""
        )
        input_text = f"""# 角色设定

{self.loader.character(role_id)}

# 当前角色

{role_id}（{self.loader.character_name(role_id)}）

# 角色可见信息

{self._format(character_view)}
{status_section}

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
