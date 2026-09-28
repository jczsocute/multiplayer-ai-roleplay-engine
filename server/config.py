import os
import secrets
from dataclasses import dataclass

from server.roles import validate_role_limits

try:
    from dotenv import load_dotenv
except ModuleNotFoundError:
    def load_dotenv() -> bool:
        return False


# Characters that are easy to read and type; avoids 0/O and 1/I/l ambiguity.
_ROOM_KEY_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"


@dataclass(frozen=True)
class Settings:
    web_host: str
    web_port: int
    host_ws_host: str
    host_ws_port: int
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    world_update_max_tokens: int
    narration_max_tokens: int
    room_key: str
    disconnect_grace_seconds: int
    ui_font_scale: float
    min_role_count: int
    max_role_count: int


def generate_room_key() -> str:
    """Return a short, human-friendly shared room key like K7M4-PQ9D."""
    block = "".join(secrets.choice(_ROOM_KEY_ALPHABET) for _ in range(4))
    other = "".join(secrets.choice(_ROOM_KEY_ALPHABET) for _ in range(4))
    return f"{block}-{other}"


def normalize_room_key(value: str) -> str:
    """Compare room keys leniently: uppercase and ignore dashes/spaces."""
    return "".join(ch for ch in value.upper() if ch.isalnum())


def load_settings() -> Settings:
    load_dotenv()
    room_key = os.getenv("ROOM_KEY", "").strip()
    if not room_key:
        room_key = generate_room_key()
    min_role_count, max_role_count = load_role_limits()
    return Settings(
        web_host=os.getenv("WEB_HOST", os.getenv("SERVER_HOST", "127.0.0.1")),
        web_port=int(os.getenv("WEB_PORT", os.getenv("SERVER_PORT", "8080"))),
        host_ws_host=os.getenv("HOST_WS_HOST", "127.0.0.1"),
        host_ws_port=int(os.getenv("HOST_WS_PORT", "8766")),
        llm_api_key=_required_env("LLM_API_KEY"),
        llm_base_url=_required_env("LLM_BASE_URL"),
        llm_model=_required_env("LLM_MODEL"),
        world_update_max_tokens=_positive_int_env("WORLD_UPDATE_MAX_TOKENS", 8192),
        narration_max_tokens=_positive_int_env("NARRATION_MAX_TOKENS", 4096),
        room_key=room_key,
        disconnect_grace_seconds=_positive_int_env("DISCONNECT_GRACE_SECONDS", 60),
        ui_font_scale=_float_env("UI_FONT_SCALE", 0.7, 0.4, 1.5),
        min_role_count=min_role_count,
        max_role_count=max_role_count,
    )


def load_role_limits() -> tuple[int, int]:
    load_dotenv()
    minimum = _positive_int_env("MIN_ROLE_COUNT", 2)
    maximum = _positive_int_env("MAX_ROLE_COUNT", 4)
    validate_role_limits(minimum, maximum)
    return minimum, maximum


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


def _float_env(name: str, default: float, minimum: float, maximum: float) -> float:
    value = float(os.getenv(name, str(default)))
    if value < minimum or value > maximum:
        raise RuntimeError(f"{name} must be between {minimum} and {maximum}")
    return value
