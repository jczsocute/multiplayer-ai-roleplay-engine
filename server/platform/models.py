from dataclasses import dataclass


@dataclass(frozen=True)
class AuthenticatedUser:
    """Trusted platform identity resolved from an auth session."""

    id: int
    username: str


@dataclass(frozen=True)
class UserRecord:
    """Platform account row as seen by the Admin console."""

    id: int
    username: str
    is_admin: bool
    created_at: str


@dataclass(frozen=True)
class TemplateMetadata:
    id: str
    owner_user_id: int
    name: str
    is_public: bool
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class GameMetadata:
    id: str
    owner_user_id: int
    source_template_id: str | None
    name: str
    created_at: str
    updated_at: str


@dataclass(frozen=True)
class RoomMetadata:
    code: str
    owner_user_id: int
    game_id: str
    created_at: str
