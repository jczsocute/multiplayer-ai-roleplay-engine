import asyncio
import json

from server.config import load_settings
from server.gameserver.llm.client import LLMClient


async def run() -> None:
    settings = load_settings()
    llm = LLMClient(
        settings.llm_api_key,
        settings.llm_base_url,
        settings.llm_model,
    )
    raw = await llm.generate(
        "你是 API 联通测试助手。必须只输出合法 JSON，不要输出解释或 Markdown 代码块。",
        '请只返回这个 JSON 对象：{"ok": true}',
        json_mode=True,
        max_tokens=128,
    )
    result = json.loads(raw)
    if not isinstance(result, dict) or result.get("ok") is not True:
        raise ValueError("API returned JSON, but it did not contain ok=true")
    print("DeepSeek API connection OK")
    print(f"Model: {settings.llm_model}")
    print("JSON output OK")


def main() -> None:
    try:
        asyncio.run(run())
    except Exception as exc:
        print("DeepSeek API connection FAILED")
        print(f"{type(exc).__name__}: {exc}")
        raise SystemExit(1) from None


if __name__ == "__main__":
    main()
