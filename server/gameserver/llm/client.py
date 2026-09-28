class LLMClient:
    """Minimal async client for an OpenAI-compatible chat completion API."""

    def __init__(self, api_key: str, base_url: str, model: str) -> None:
        # Import here so tests using a mock updater don't require the SDK at import time.
        from openai import AsyncOpenAI, DefaultAsyncHttpxClient

        self.client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=DefaultAsyncHttpxClient(trust_env=False),
        )
        self.model = model

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        options = {}
        if json_mode:
            options["response_format"] = {"type": "json_object"}
        if max_tokens is not None:
            options["max_tokens"] = max_tokens
        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            **options,
        )
        choice = response.choices[0]
        if choice.finish_reason == "length":
            raise RuntimeError("LLM output was truncated at max_tokens")
        content = choice.message.content
        if not content or not content.strip():
            raise RuntimeError("LLM returned an empty response")
        return content.strip()
