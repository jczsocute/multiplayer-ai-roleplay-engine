import os
from dataclasses import dataclass

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    world_update_max_tokens: int
    narration_max_tokens: int


def load_settings() -> Settings:
    load_dotenv()
    return Settings(
        host=os.getenv("SERVER_HOST", "0.0.0.0"),
        port=int(os.getenv("SERVER_PORT", "8765")),
        llm_api_key=_required_env("LLM_API_KEY"),
        llm_base_url=_required_env("LLM_BASE_URL"),
        llm_model=_required_env("LLM_MODEL"),
        world_update_max_tokens=_positive_int_env("WORLD_UPDATE_MAX_TOKENS", 8192),
        narration_max_tokens=_positive_int_env("NARRATION_MAX_TOKENS", 4096),
    )


def _required_env(name: str) -> str:
    value = os.getenv(name, "").strip()
    if not value:
        raise RuntimeError(f"missing required environment variable: {name}")
    return value


def _positive_int_env(name: str, default: int) -> int:
    value = int(os.getenv(name, str(default)))
    if value < 1:
        raise RuntimeError(f"{name} must be a positive integer")
    return value
