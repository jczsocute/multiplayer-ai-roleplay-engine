"""Direct SQLite access for platform identity and metadata catalogs."""

import hmac
import logging
import secrets
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

from server.platform.auth import (
    hash_password, hash_token, now_iso, parse_time, validate_password,
    validate_username,
)
from server.platform.models import (
    AuthenticatedUser, GameMetadata, RoomMetadata, TemplateMetadata, UserRecord,
)

logger = logging.getLogger(__name__)

# Resource display names are short labels, not paths; 60 characters is plenty.
MAX_RESOURCE_NAME_LENGTH = 60

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT NOT NULL UNIQUE,
    password_hash BLOB NOT NULL,
    password_salt BLOB NOT NULL,
    created_at TEXT NOT NULL,
    is_admin INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS auth_sessions (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    FOREIGN KEY (user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS templates (
    id TEXT PRIMARY KEY,
    owner_user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    is_public INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (owner_user_id) REFERENCES users(id)
);
CREATE TABLE IF NOT EXISTS games (
    id TEXT PRIMARY KEY,
    owner_user_id INTEGER NOT NULL,
    source_template_id TEXT,
    name TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    FOREIGN KEY (owner_user_id) REFERENCES users(id),
    FOREIGN KEY (source_template_id) REFERENCES templates(id)
);
CREATE TABLE IF NOT EXISTS rooms (
    code TEXT PRIMARY KEY,
    owner_user_id INTEGER NOT NULL UNIQUE,
    game_id TEXT NOT NULL UNIQUE,
    password_hash BLOB,
    password_salt BLOB,
    created_at TEXT NOT NULL,
    FOREIGN KEY (owner_user_id) REFERENCES users(id),
    FOREIGN KEY (game_id) REFERENCES games(id)
);
"""


class PlatformDatabase:
    """One lightweight database for accounts and platform metadata."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)

    def initialize(self, legacy_accounts_path: str | Path | None = None) -> None:
        is_new = not self.path.exists()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate(connection)
        legacy = Path(legacy_accounts_path) if legacy_accounts_path else None
        if is_new and legacy and legacy != self.path and legacy.is_file():
            self._copy_legacy_accounts(legacy)

    @staticmethod
    def _migrate(connection: sqlite3.Connection) -> None:
        """Additive, no-framework migration for databases created earlier."""
        columns = {
            row["name"] for row in connection.execute("PRAGMA table_info(users)")
        }
        if "is_admin" not in columns:
            logger.info("Adding users.is_admin to an existing platform database")
            connection.execute(
                "ALTER TABLE users ADD COLUMN is_admin INTEGER NOT NULL DEFAULT 0"
            )

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _copy_legacy_accounts(self, source: Path) -> None:
        with sqlite3.connect(source) as old:
            tables = {row[0] for row in old.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
            if not {"users", "auth_sessions"}.issubset(tables):
                return
            users = old.execute(
                "SELECT id, username, password_hash, password_salt, created_at FROM users"
            ).fetchall()
            sessions = old.execute(
                "SELECT token_hash, user_id, created_at, expires_at FROM auth_sessions"
            ).fetchall()
        with self._connect() as connection:
            connection.executemany(
                """INSERT OR IGNORE INTO users
                   (id, username, password_hash, password_salt, created_at)
                   VALUES (?, ?, ?, ?, ?)""", users,
            )
            connection.executemany(
                """INSERT OR IGNORE INTO auth_sessions
                   (token_hash, user_id, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""", sessions,
            )
        logger.info("Migrated account data from %s to %s", source, self.path)

    def create_user(self, username: str, password: str) -> AuthenticatedUser:
        username = validate_username(username)
        validate_password(password)
        salt = secrets.token_bytes(16)
        try:
            with self._connect() as connection:
                cursor = connection.execute(
                    """INSERT INTO users (username, password_hash, password_salt, created_at)
                       VALUES (?, ?, ?, ?)""",
                    (username, hash_password(password, salt), salt, now_iso()),
                )
                user_id = int(cursor.lastrowid)
        except sqlite3.IntegrityError as exc:
            raise ValueError("该用户名已被使用") from exc
        return AuthenticatedUser(user_id, username)

    def authenticate(self, username: str, password: str) -> AuthenticatedUser | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id, username, password_hash, password_salt
                   FROM users WHERE username = ?""", (username.strip(),),
            ).fetchone()
        if row is None or not hmac.compare_digest(
            row["password_hash"], hash_password(password, row["password_salt"])
        ):
            return None
        return AuthenticatedUser(int(row["id"]), row["username"])

    def user_by_username(self, username: str) -> AuthenticatedUser | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, username FROM users WHERE username = ?", (username.strip(),)
            ).fetchone()
        return None if row is None else AuthenticatedUser(int(row["id"]), row["username"])

    def user_by_id(self, user_id: int) -> AuthenticatedUser | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id, username FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return None if row is None else AuthenticatedUser(int(row["id"]), row["username"])

    def create_session(self, user_id: int, days: int) -> str:
        raw_token = secrets.token_urlsafe(32)
        now = datetime.now(timezone.utc)
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO auth_sessions (token_hash, user_id, created_at, expires_at)
                   VALUES (?, ?, ?, ?)""",
                (hash_token(raw_token), user_id, now.isoformat(),
                 (now + timedelta(days=days)).isoformat()),
            )
        return raw_token

    def delete_session(self, raw_token: str | None) -> None:
        if not raw_token:
            return
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM auth_sessions WHERE token_hash = ?", (hash_token(raw_token),)
            )

    def resolve_session(self, raw_token: str | None) -> AuthenticatedUser | None:
        if not raw_token:
            return None
        token_hash = hash_token(raw_token)
        with self._connect() as connection:
            row = connection.execute(
                """SELECT users.id, users.username, auth_sessions.expires_at
                   FROM auth_sessions JOIN users ON users.id = auth_sessions.user_id
                   WHERE auth_sessions.token_hash = ?""", (token_hash,),
            ).fetchone()
            if row is None:
                return None
            if parse_time(row["expires_at"]) <= datetime.now(timezone.utc):
                connection.execute(
                    "DELETE FROM auth_sessions WHERE token_hash = ?", (token_hash,)
                )
                return None
        return AuthenticatedUser(int(row["id"]), row["username"])

    # --- platform administration ---------------------------------------------

    def is_admin(self, user_id: int) -> bool:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT is_admin FROM users WHERE id = ?", (user_id,)
            ).fetchone()
        return bool(row and row["is_admin"])

    def list_users(self) -> list[UserRecord]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT id, username, is_admin, created_at FROM users
                   ORDER BY id"""
            ).fetchall()
        return [_user(row) for row in rows]

    def user_record(self, username: str) -> UserRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT id, username, is_admin, created_at FROM users
                   WHERE username = ?""",
                (username.strip(),),
            ).fetchone()
        return _user(row) if row is not None else None

    def set_admin(self, username: str, value: bool) -> UserRecord:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT id FROM users WHERE username = ?", (username.strip(),)
            ).fetchone()
            if row is None:
                raise ValueError(f"account_not_found:{username.strip()}")
            connection.execute(
                "UPDATE users SET is_admin = ? WHERE id = ?",
                (int(value), int(row["id"])),
            )
        return self.user_record(username)

    def user_resource_counts(self, user_id: int) -> dict[str, int]:
        """Owned Templates, Games and active Rooms; used to guard deletion."""
        with self._connect() as connection:
            counts = {}
            for key, table in (
                ("templates", "templates"), ("games", "games"), ("rooms", "rooms"),
            ):
                counts[key] = int(connection.execute(
                    f"SELECT COUNT(*) FROM {table} WHERE owner_user_id = ?", (user_id,)
                ).fetchone()[0])
        return counts

    def delete_user(self, user_id: int) -> None:
        """Delete an account that owns nothing; sessions go with it.

        Raises ``ValueError`` when the account still owns resources so the admin
        resolves them first instead of silently cascading.
        """
        counts = self.user_resource_counts(user_id)
        blocking = {key: value for key, value in counts.items() if value}
        if blocking:
            raise ValueError("user_owns_resources:" + ",".join(sorted(blocking)))
        with self._connect() as connection:
            connection.execute("DELETE FROM auth_sessions WHERE user_id = ?", (user_id,))
            connection.execute("DELETE FROM users WHERE id = ?", (user_id,))

    def rename_template(self, template_id: str, name: str) -> TemplateMetadata:
        name = name.strip()
        if not name or len(name) > MAX_RESOURCE_NAME_LENGTH:
            raise ValueError("invalid_template_name")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE templates SET name = ?, updated_at = ? WHERE id = ?",
                (name, now_iso(), template_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("template_not_found")
        return self.get_template(template_id)

    def set_template_public(self, template_id: str, value: bool) -> TemplateMetadata:
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE templates SET is_public = ?, updated_at = ? WHERE id = ?",
                (int(value), now_iso(), template_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("template_not_found")
        return self.get_template(template_id)

    def delete_template(self, template_id: str) -> None:
        """Delete Template metadata; snapshot Games keep their payload and history.

        ``games.source_template_id`` is cleared first so existing Games survive
        without a dangling reference.
        """
        with self._connect() as connection:
            connection.execute(
                "UPDATE games SET source_template_id = NULL WHERE source_template_id = ?",
                (template_id,),
            )
            cursor = connection.execute(
                "DELETE FROM templates WHERE id = ?", (template_id,)
            )
            if cursor.rowcount == 0:
                raise ValueError("template_not_found")

    def template_by_owner_and_name(
        self, owner_user_id: int, name: str
    ) -> TemplateMetadata | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT * FROM templates WHERE owner_user_id = ? AND name = ?
                   ORDER BY created_at, id LIMIT 1""",
                (owner_user_id, name),
            ).fetchone()
        return _template(row) if row is not None else None

    def create_template(
        self, template_id: str, owner_user_id: int, name: str, is_public: bool = False,
    ) -> TemplateMetadata:
        now = now_iso()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO templates
                   (id, owner_user_id, name, is_public, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (template_id, owner_user_id, name, int(is_public), now, now),
            )
        return self.get_template(template_id)

    def get_template(self, template_id: str) -> TemplateMetadata | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM templates WHERE id = ?", (template_id,)
            ).fetchone()
        return _template(row)

    def list_public_templates(self) -> list[TemplateMetadata]:
        return self._list_templates("is_public = 1", ())

    def list_user_templates(self, user_id: int) -> list[TemplateMetadata]:
        return self._list_templates("owner_user_id = ?", (user_id,))

    def list_available_templates(self, user_id: int) -> list[TemplateMetadata]:
        return self._list_templates("owner_user_id = ? OR is_public = 1", (user_id,))

    def list_templates(self) -> list[TemplateMetadata]:
        """Every Template in the catalog; platform administration only."""
        return self._list_templates("1", ())

    def _list_templates(self, where: str, values: tuple) -> list[TemplateMetadata]:
        with self._connect() as connection:
            rows = connection.execute(
                f"SELECT * FROM templates WHERE {where} ORDER BY created_at, id", values
            ).fetchall()
        return [_template(row) for row in rows]

    def create_game(
        self, game_id: str, owner_user_id: int, name: str,
        source_template_id: str | None = None,
    ) -> GameMetadata:
        now = now_iso()
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO games
                   (id, owner_user_id, source_template_id, name, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (game_id, owner_user_id, source_template_id, name, now, now),
            )
        return self.get_game(game_id)

    def get_game(self, game_id: str) -> GameMetadata | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM games WHERE id = ?", (game_id,)
            ).fetchone()
        return _game(row)

    def rename_game(self, game_id: str, name: str) -> GameMetadata:
        name = name.strip()
        if not name or len(name) > MAX_RESOURCE_NAME_LENGTH:
            raise ValueError("invalid_game_name")
        with self._connect() as connection:
            cursor = connection.execute(
                "UPDATE games SET name = ?, updated_at = ? WHERE id = ?",
                (name, now_iso(), game_id),
            )
            if cursor.rowcount == 0:
                raise ValueError("game_not_found")
        return self.get_game(game_id)

    def touch_game(self, game_id: str) -> None:
        """Record that the Game's content changed (round done, retry, rollback)."""
        with self._connect() as connection:
            connection.execute(
                "UPDATE games SET updated_at = ? WHERE id = ?", (now_iso(), game_id)
            )

    def delete_game_metadata(self, game_id: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM games WHERE id = ?", (game_id,))

    def game_is_active(self, game_id: str) -> bool:
        """True while a Room references this Game (rooms.game_id is UNIQUE)."""
        with self._connect() as connection:
            row = connection.execute(
                "SELECT 1 FROM rooms WHERE game_id = ? LIMIT 1", (game_id,)
            ).fetchone()
        return row is not None

    def list_games(self) -> list[GameMetadata]:
        """Every registered Game, regardless of owner (used by migration)."""
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM games ORDER BY created_at"
            ).fetchall()
        return [_game(row) for row in rows]

    def list_user_games(self, user_id: int) -> list[GameMetadata]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM games WHERE owner_user_id = ? ORDER BY created_at, id",
                (user_id,),
            ).fetchall()
        return [_game(row) for row in rows]

    def create_room_metadata(
        self, code: str, owner_user_id: int, game_id: str,
        password_hash: bytes | None = None, password_salt: bytes | None = None,
    ) -> RoomMetadata:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO rooms
                   (code, owner_user_id, game_id, password_hash, password_salt, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (code, owner_user_id, game_id, password_hash, password_salt, now_iso()),
            )
        return self.get_room(code)

    def get_room(self, code: str) -> RoomMetadata | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM rooms WHERE code = ?", (code,)).fetchone()
        return _room(row)

    def get_room_password(self, code: str) -> tuple[bytes | None, bytes | None] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT password_hash, password_salt FROM rooms WHERE code = ?", (code,)
            ).fetchone()
        return None if row is None else (row["password_hash"], row["password_salt"])

    def list_rooms(self) -> list[RoomMetadata]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM rooms ORDER BY created_at, code"
            ).fetchall()
        return [_room(row) for row in rows]

    def delete_room(self, code: str) -> None:
        with self._connect() as connection:
            connection.execute("DELETE FROM rooms WHERE code = ?", (code,))


def generate_stable_id(prefix: str, length: int = 6) -> str:
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return f"{prefix}_{''.join(secrets.choice(alphabet) for _ in range(length))}"


def generate_room_code(length: int = 6) -> str:
    alphabet = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def _user(row: sqlite3.Row | None) -> UserRecord | None:
    if row is None:
        return None
    return UserRecord(
        int(row["id"]), row["username"], bool(row["is_admin"]), row["created_at"],
    )


def _template(row: sqlite3.Row | None) -> TemplateMetadata | None:
    if row is None:
        return None
    return TemplateMetadata(
        row["id"], int(row["owner_user_id"]), row["name"], bool(row["is_public"]),
        row["created_at"], row["updated_at"],
    )


def _game(row: sqlite3.Row | None) -> GameMetadata | None:
    if row is None:
        return None
    return GameMetadata(
        row["id"], int(row["owner_user_id"]), row["source_template_id"],
        row["name"], row["created_at"], row["updated_at"],
    )


def _room(row: sqlite3.Row | None) -> RoomMetadata | None:
    if row is None:
        return None
    return RoomMetadata(
        row["code"], int(row["owner_user_id"]), row["game_id"], row["created_at"],
    )
