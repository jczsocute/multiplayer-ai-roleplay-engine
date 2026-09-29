import asyncio
import hmac
import logging
import re
import secrets
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Awaitable, Callable

from server.gameserver.factory import load_game_server
from server.gameserver.game_server import GameServer
from server.gameserver.template import validate_template
from server.platform.auth import hash_password
from server.platform.catalog import create_game_snapshot, delete_game
from server.platform.database import PlatformDatabase, generate_room_code
from server.platform.models import AuthenticatedUser, RoomMetadata

logger = logging.getLogger(__name__)
PASSWORD_RE = re.compile(r"^[A-Za-z0-9_]{1,32}$")
GameFactory = Callable[[Path, int], Awaitable[GameServer]]

# Seats per Room include the owner, players, spectators and members that are
# inside the disconnect grace period (a reserved seat must not be double sold).
DEFAULT_MAX_ROOM_USERS = 10


@dataclass
class RoomRuntime:
    code: str
    game_id: str
    owner_user_id: int
    game_server: GameServer


class RoomManager:
    def __init__(
        self,
        database: PlatformDatabase,
        games_dir: str | Path = "games",
        templates_dir: str | Path = "templates",
        game_factory: GameFactory = load_game_server,
        max_users: int = DEFAULT_MAX_ROOM_USERS,
        min_role_count: int = 2,
        max_role_count: int = 4,
        disconnect_timeout_seconds: int = 300,
    ) -> None:
        if max_users < 1:
            raise ValueError("max_users must be a positive integer")
        self.database = database
        self.games_dir = Path(games_dir)
        self.templates_dir = Path(templates_dir)
        self.game_factory = game_factory
        self.max_users = max_users
        self.min_role_count = min_role_count
        self.max_role_count = max_role_count
        self.disconnect_timeout_seconds = disconnect_timeout_seconds
        self.rooms: dict[str, RoomRuntime] = {}
        self.user_room: dict[int, str] = {}
        self._user_connection: dict[int, object] = {}
        self._lock = asyncio.Lock()

    async def load_active_rooms(self) -> None:
        for room in await asyncio.to_thread(self.database.list_rooms):
            try:
                runtime = await self._load_runtime(room)
                self.rooms[room.code] = runtime
            except Exception:
                logger.exception("Failed to recover active room %s", room.code)

    def get_runtime(self, room_code: str) -> RoomRuntime | None:
        return self.rooms.get(room_code.upper())

    def user_current_room(self, user_id: int) -> str | None:
        return self.user_room.get(user_id)

    def occupancy(self, room_code: str) -> int:
        """Reserved seats: connected members plus members inside the grace period."""
        code = room_code.upper()
        return sum(1 for joined in self.user_room.values() if joined == code)

    async def create_room_from_game(
        self, owner: AuthenticatedUser, game_id: str, password: str = ""
    ) -> RoomRuntime:
        game = await asyncio.to_thread(self.database.get_game, game_id)
        if game is None:
            raise ValueError("game_not_found")
        if game.owner_user_id != owner.id:
            raise PermissionError("forbidden")
        return await self._create(owner.id, game.id, password)

    async def create_room_from_template(
        self, owner: AuthenticatedUser, template_id: str, game_name: str,
        password: str = "",
    ) -> RoomRuntime:
        template = await asyncio.to_thread(self.database.get_template, template_id)
        if template is None:
            raise ValueError("template_not_found")
        if template.owner_user_id != owner.id and not template.is_public:
            raise PermissionError("forbidden")
        owned_rooms = await asyncio.to_thread(self.database.list_rooms)
        if any(room.owner_user_id == owner.id for room in owned_rooms):
            raise ValueError("owner_already_has_room")
        await asyncio.to_thread(
            validate_template,
            self.templates_dir / template_id,
            min_count=self.min_role_count, max_count=self.max_role_count,
        )
        name = game_name.strip() or template.name
        game = await asyncio.to_thread(
            create_game_snapshot,
            self.database, template_id, owner.id, name,
            self.templates_dir, self.games_dir,
        )
        try:
            return await self._create(owner.id, game.id, password)
        except Exception:
            await asyncio.to_thread(
                delete_game, self.database, game.id, owner.id, self.games_dir
            )
            raise

    async def _create(self, owner_id: int, game_id: str, password: str) -> RoomRuntime:
        password_hash, salt = await asyncio.to_thread(_password_values, password)
        async with self._lock:
            for _ in range(20):
                code = generate_room_code()
                if await asyncio.to_thread(self.database.get_room, code) is None:
                    break
            else:
                raise RuntimeError("room_code_collision")
            try:
                metadata = await asyncio.to_thread(
                    self.database.create_room_metadata,
                    code, owner_id, game_id, password_hash, salt,
                )
            except sqlite3.IntegrityError as exc:
                message = str(exc)
                if "owner_user_id" in message:
                    raise ValueError("owner_already_has_room") from exc
                if "game_id" in message:
                    raise ValueError("game_already_active") from exc
                raise
            try:
                runtime = await self._load_runtime(metadata)
            except Exception:
                await asyncio.to_thread(self.database.delete_room, code)
                raise
            self.rooms[code] = runtime
            return runtime

    async def _load_runtime(self, room: RoomMetadata) -> RoomRuntime:
        game = await asyncio.to_thread(self.database.get_game, room.game_id)
        if game is None:
            raise ValueError(f"missing game metadata: {room.game_id}")
        path = self.games_dir / room.game_id
        server = await self.game_factory(path, room.owner_user_id)
        server.scenario_name = self._room_display_name(game.name, game.id)
        # Activity callbacks live here: GameServer never learns about Platform
        # metadata, game ids or room codes.
        server.on_member_left = lambda user_id: self._on_member_left(room.code, user_id)
        server.on_game_changed = lambda: self._touch_game(room.game_id)
        return RoomRuntime(room.code, room.game_id, room.owner_user_id, server)

    @staticmethod
    def _room_display_name(game_name: str, game_id: str) -> str:
        # Keep the save identifier visible without exposing its generic game_ prefix.
        suffix = game_id.removeprefix("game_")
        return f"{game_name}_{suffix}"

    async def _touch_game(self, game_id: str) -> None:
        await asyncio.to_thread(self.database.touch_game, game_id)

    async def _on_member_left(self, room_code: str, user_id: int) -> None:
        """Called once a participant is definitively gone (timeout or kick)."""
        runtime = self.rooms.get(room_code)
        if runtime is None:
            return
        # GameServer already closed an evicted member's socket. Drop only the
        # reservation here, so an HTTP leave never attempts a second close.
        await self.leave(user_id, room_code, self._user_connection.get(user_id))
        if user_id == runtime.owner_user_id:
            logger.info("Room %s owner timed out; closing the Room", room_code)
            try:
                await self.close_room(room_code, admin=True)
            except ValueError:
                logger.info("Room %s was already gone", room_code)

    def verify_password(self, room_code: str, password: str) -> None:
        values = self.database.get_room_password(room_code.upper())
        if values is None:
            raise ValueError("room_not_found")
        expected, salt = values
        if expected is None or salt is None:
            return
        if not password:
            raise ValueError("room_password_required")
        if not hmac.compare_digest(expected, hash_password(password, salt)):
            raise ValueError("invalid_room_password")

    async def enter(self, user_id: int, room_code: str, connection: object) -> None:
        code = room_code.upper()
        async with self._lock:
            current = self.user_room.get(user_id)
            if current is not None and current != code:
                raise ValueError("already_in_room")
            held = sum(1 for joined in self.user_room.values() if joined == code)
            if current != code and held >= self.max_users:
                raise ValueError("room_full")
            self.user_room[user_id] = code
            self._user_connection[user_id] = connection

    async def leave(
        self, user_id: int, room_code: str | None = None, connection: object | None = None
    ) -> None:
        connection_to_close = None
        async with self._lock:
            current = self.user_room.get(user_id)
            active = self._user_connection.get(user_id)
            if (room_code is None or current == room_code.upper()) and (
                connection is None or active is connection
            ):
                self.user_room.pop(user_id, None)
                self._user_connection.pop(user_id, None)
                if connection is None:
                    connection_to_close = active
        if connection_to_close is not None and hasattr(connection_to_close, "close"):
            try:
                await connection_to_close.close(code=1000)
            except Exception:
                logger.debug("Member socket was already closed")

    async def leave_member(self, user_id: int, room_code: str) -> None:
        """Authoritative explicit leave: remove the game participant and seat."""
        code = room_code.upper()
        if self.user_room.get(user_id) != code:
            return
        runtime = self.get_runtime(code)
        if runtime is not None:
            await runtime.game_server.leave_user(user_id)
        # Also handles a reservation whose game session is already gone.
        await self.leave(user_id, code)

    async def release_if_gone(
        self, user_id: int, room_code: str, connection: object | None = None
    ) -> None:
        """Drop membership only when the participant really left the Room.

        A socket that closed but is inside the disconnect grace period keeps its
        seat, its role and its `user_room` membership until the timeout fires.
        """
        runtime = self.get_runtime(room_code)
        if runtime is not None and user_id in runtime.game_server.sessions.users:
            return
        await self.leave(user_id, room_code, connection)

    async def close_room(
        self, code: str, owner_user_id: int | None = None, *, admin: bool = False
    ) -> None:
        """Close a Room. ``admin=True`` lets the platform admin close any Room."""
        code = code.upper()
        room = await asyncio.to_thread(self.database.get_room, code)
        if room is None:
            raise ValueError("room_not_found")
        if not admin and room.owner_user_id != owner_user_id:
            raise PermissionError("forbidden")
        async with self._lock:
            runtime = self.rooms.pop(code, None)
            members = [user for user, joined in self.user_room.items() if joined == code]
            for user_id in members:
                self.user_room.pop(user_id, None)
                self._user_connection.pop(user_id, None)
        if runtime is not None:
            await runtime.game_server.close_connections("房主已关闭房间")
        await asyncio.to_thread(self.database.delete_room, code)

    async def public_room(self, code: str) -> dict | None:
        runtime = self.get_runtime(code)
        room = await asyncio.to_thread(self.database.get_room, code.upper())
        if runtime is None or room is None:
            return None
        game, owner, password = await asyncio.gather(
            asyncio.to_thread(self.database.get_game, room.game_id),
            asyncio.to_thread(self.database.user_by_id, room.owner_user_id),
            asyncio.to_thread(self.database.get_room_password, room.code),
        )
        presence = await runtime.game_server.sessions.presence_snapshot()
        return {
            "code": room.code,
            "game_id": room.game_id,
            "game_name": game.name if game else room.game_id,
            "display_name": self._room_display_name(game.name, game.id) if game else room.game_id,
            "owner_username": owner.username if owner else str(room.owner_user_id),
            "owner_user_id": room.owner_user_id,
            "role_count": len(runtime.game_server.role_ids),
            "connected_count": sum(
                1 for user in presence["users"] if user.get("connected")
            ),
            "occupancy": self.occupancy(room.code),
            "max_users": self.max_users,
            "has_password": bool(password and password[0]),
        }

    async def list_public_rooms(self) -> list[dict]:
        values = []
        for code in sorted(self.rooms):
            room = await self.public_room(code)
            if room is not None:
                values.append(room)
        return values

    async def admin_room(self, code: str) -> dict | None:
        """Full Room detail for the local Admin console (never sent to players)."""
        runtime = self.get_runtime(code)
        room = await asyncio.to_thread(self.database.get_room, code.upper())
        if room is None:
            return None
        game, owner, password = await asyncio.gather(
            asyncio.to_thread(self.database.get_game, room.game_id),
            asyncio.to_thread(self.database.user_by_id, room.owner_user_id),
            asyncio.to_thread(self.database.get_room_password, room.code),
        )
        detail = {
            "code": room.code,
            "game_id": room.game_id,
            "game_name": game.name if game else room.game_id,
            "owner_user_id": room.owner_user_id,
            "owner_username": owner.username if owner else str(room.owner_user_id),
            "has_password": bool(password and password[0]),
            "occupancy": self.occupancy(room.code),
            "max_users": self.max_users,
            "runtime_status": "RUNNING" if runtime is not None else "RECOVERY_FAILED",
        }
        if runtime is None:
            return detail
        presence = await runtime.game_server.sessions.presence_snapshot()
        game_server = runtime.game_server
        detail.update({
            "role_count": len(game_server.role_ids),
            "connected_count": sum(
                1 for user in presence["users"] if user.get("connected")
            ),
            "users": [
                {
                    "user_id": user["user_id"],
                    "name": user["name"],
                    "role": user["role"],
                    "connected": user["connected"],
                }
                for user in presence["users"]
            ],
            "assignments": await game_server.sessions.role_usernames(),
            "round": game_server.rounds.round_number,
            "stage": game_server.rounds.stage.value,
            "has_room_key": bool(game_server.room_key),
        })
        return detail

    async def list_admin_rooms(self) -> list[dict]:
        values = []
        rooms = await asyncio.to_thread(self.database.list_rooms)
        for code in sorted(room.code for room in rooms):
            room = await self.admin_room(code)
            if room is not None:
                values.append(room)
        return values


def validate_room_password(password: str) -> None:
    if password and not PASSWORD_RE.fullmatch(password):
        raise ValueError("invalid_room_password_format")


def _password_values(password: str) -> tuple[bytes | None, bytes | None]:
    validate_room_password(password)
    if not password:
        return None, None
    salt = secrets.token_bytes(16)
    return hash_password(password, salt), salt
