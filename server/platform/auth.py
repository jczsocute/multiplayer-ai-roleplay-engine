"""Small password and session-token primitives for Platform Core."""

import hashlib
from datetime import datetime, timezone

MAX_USERNAME_LENGTH = 32
AUTH_COOKIE_NAME = "rp_auth"
PASSWORD_MIN_LENGTH = 8

_SCRYPT_N = 2 ** 14
_SCRYPT_R = 8
_SCRYPT_P = 1
_HASH_LENGTH = 32


def validate_username(username: str) -> str:
    username = username.strip()
    if not username:
        raise ValueError("用户名不能为空")
    if len(username) > MAX_USERNAME_LENGTH:
        raise ValueError(f"用户名最长为 {MAX_USERNAME_LENGTH} 个字符")
    return username


def validate_password(password: str) -> None:
    if len(password) < PASSWORD_MIN_LENGTH:
        raise ValueError(f"密码至少需要 {PASSWORD_MIN_LENGTH} 个字符")


def hash_password(password: str, salt: bytes) -> bytes:
    return hashlib.scrypt(
        password.encode("utf-8"), salt=salt, n=_SCRYPT_N, r=_SCRYPT_R,
        p=_SCRYPT_P, dklen=_HASH_LENGTH,
    )


def hash_token(raw_token: str) -> str:
    return hashlib.sha256(raw_token.encode("utf-8")).hexdigest()


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed
