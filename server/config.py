import os
import secrets
from dataclasses import dataclass

from server.gameserver.roles import validate_role_limits

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
    llm_api_key: str
    llm_base_url: str
    llm_model: str
    world_update_max_tokens: int
    narration_max_tokens: int
    narrator_history_rounds: int
    room_key: str
    disconnect_grace_seconds: int
    room_disconnect_timeout_seconds: int
    max_room_users: int
    ui_font_scale: float
    min_role_count: int
    max_role_count: int
    platform_db: str
    legacy_accounts_db: str
    allow_registration: bool
    auth_session_days: int
    secure_cookie: bool
    allowed_origins: tuple[str, ...]


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
        llm_api_key=_required_env("LLM_API_KEY"),
        llm_base_url=_required_env("LLM_BASE_URL"),
        llm_model=_required_env("LLM_MODEL"),
        world_update_max_tokens=_positive_int_env("WORLD_UPDATE_MAX_TOKENS", 8192),
        narration_max_tokens=_positive_int_env("NARRATION_MAX_TOKENS", 4096),
        narrator_history_rounds=_positive_int_env("NARRATOR_HISTORY_ROUNDS", 20),
        room_key=room_key,
        disconnect_grace_seconds=_positive_int_env("DISCONNECT_GRACE_SECONDS", 60),
        room_disconnect_timeout_seconds=load_room_disconnect_timeout(),
        max_room_users=load_max_room_users(),
        ui_font_scale=_float_env("UI_FONT_SCALE", 0.7, 0.4, 1.5),
        min_role_count=min_role_count,
        max_role_count=max_role_count,
        platform_db=load_platform_path(),
        legacy_accounts_db=load_legacy_accounts_path(),
        allow_registration=_bool_env("ALLOW_REGISTRATION", True),
        auth_session_days=_positive_int_env("AUTH_SESSION_DAYS", 30),
        secure_cookie=_bool_env("AUTH_COOKIE_SECURE", False),
        allowed_origins=load_allowed_origins(),
    )


def load_allowed_origins() -> tuple[str, ...]:
    """Browser WebSocket origins to accept; empty means same-host plus loopback."""
    load_dotenv()
    raw = os.getenv("ALLOWED_ORIGINS", "")
    return tuple(item.strip() for item in raw.split(",") if item.strip())


@dataclass(frozen=True)
class PlatformSettings:
    web_host: str
    web_port: int
    max_room_users: int
    room_disconnect_timeout_seconds: int
    platform_db: str
    legacy_accounts_db: str
    ui_font_scale: float
    allow_registration: bool
    auth_session_days: int
    secure_cookie: bool
    allowed_origins: tuple[str, ...]


def load_platform_settings() -> PlatformSettings:
    """Load only settings required by the platform shell; no LLM config needed."""
    load_dotenv()
    return PlatformSettings(
        web_host=os.getenv("WEB_HOST", os.getenv("SERVER_HOST", "127.0.0.1")),
        web_port=int(os.getenv("WEB_PORT", os.getenv("SERVER_PORT", "8080"))),
        max_room_users=load_max_room_users(),
        room_disconnect_timeout_seconds=load_room_disconnect_timeout(),
        platform_db=load_platform_path(),
        legacy_accounts_db=load_legacy_accounts_path(),
        ui_font_scale=_float_env("UI_FONT_SCALE", 0.7, 0.4, 1.5),
        allow_registration=_bool_env("ALLOW_REGISTRATION", True),
        auth_session_days=_positive_int_env("AUTH_SESSION_DAYS", 30),
        secure_cookie=_bool_env("AUTH_COOKIE_SECURE", False),
        allowed_origins=load_allowed_origins(),
    )


def load_platform_path() -> str:
    load_dotenv()
    return os.getenv("PLATFORM_DB", "data/platform.db").strip() or "data/platform.db"


def load_legacy_accounts_path() -> str:
    load_dotenv()
    return os.getenv("ACCOUNTS_DB", "data/accounts.db").strip() or "data/accounts.db"


def load_max_room_users() -> int:
    """Seats per Room, including the owner, spectators and grace-period seats."""
    load_dotenv()
    return _positive_int_env("MAX_ROOM_USERS", 10)


def load_room_disconnect_timeout() -> int:
    """How long a Room membership survives after losing the Room connection."""
    load_dotenv()
    return _positive_int_env("ROOM_DISCONNECT_TIMEOUT_SECONDS", 300)


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


def _bool_env(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    normalized = value.strip().lower()
    if normalized in ("1", "true", "yes", "on"):
        return True
    if normalized in ("0", "false", "no", "off"):
        return False
    raise RuntimeError(f"{name} must be a boolean (true/false)")
