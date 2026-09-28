import asyncio
import json
import secrets
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

from server.protocol import MAX_NICKNAME_LENGTH


class Connection(Protocol):
    async def send(self, payload: str) -> None: ...


@dataclass
class User:
    """One runtime room user; never persisted.

    ``connected`` means the user currently has a live websocket.  After an
    unexpected disconnect the user stays in :class:`Sessions` for the grace
    period with ``websocket=None`` and ``connected=False`` so a later resume
    can reattach without losing nickname, role, view or draft.
    """

    name: str
    websocket: Connection | None = None
    role: str | None = None
    view_role: str | None = None
    resume_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    connected: bool = True
    disconnect_task: asyncio.Task | None = None


class Sessions:
    def __init__(
        self,
        role_ids: tuple[str, ...] = ("P1", "P2"),
        max_users: int = 100,
        disconnect_grace_seconds: int = 60,
    ) -> None:
        if not role_ids or len(set(role_ids)) != len(role_ids):
            raise ValueError("role ids must be non-empty and unique")
        self.role_ids = tuple(role_ids)
        self._role_set = frozenset(role_ids)
        self.max_users = max_users
        self.disconnect_grace_seconds = disconnect_grace_seconds
        self.users: dict[str, User] = {}
        self.host_connection: Connection | None = None
        self.host_view = "world"
        self.expiry_handler: Callable[[User], Awaitable[None]] | None = None
        self._lock = asyncio.Lock()

    async def join(self, name: str, websocket: Connection) -> User | None:
        name = name.strip()
        if not name or len(name) > MAX_NICKNAME_LENGTH:
            raise ValueError(
                f"昵称长度必须为 1 到 {MAX_NICKNAME_LENGTH} 个字符"
            )
        async with self._lock:
            if name in self.users:
                raise ValueError("该昵称已有人使用")
            if len(self.users) >= self.max_users:
                return None
            user = User(name, websocket)
            self.users[name] = user
            return user

    async def remove(self, name: str, websocket: Connection) -> User | None:
        """Remove immediately; used for explicit leave and legacy tests."""
        async with self._lock:
            user = self.users.get(name)
            if user is None or user.websocket is not websocket:
                return None
            self._cancel_disconnect_task(user)
            return self.users.pop(name)

    async def leave(self, name: str, websocket: Connection) -> User | None:
        """Explicit leave: no grace period, identity released at once."""
        return await self.remove(name, websocket)

    async def mark_disconnected(self, name: str, old_websocket: Connection) -> User | None:
        """Move a user into the disconnect grace period.

        Only takes effect when ``old_websocket`` is still the user's current
        connection, so a late ``finally`` from an old socket cannot detach a
        user who has already resumed on a new socket.
        """
        async with self._lock:
            user = self.users.get(name)
            if user is None or user.websocket is not old_websocket:
                return None
            user.websocket = None
            user.connected = False
            self._cancel_disconnect_task(user)
            token = user.resume_token
            user.disconnect_task = asyncio.create_task(self._expire_after(name, token))
            return user

    async def resume(
        self, name: str, resume_token: str, websocket: Connection
    ) -> User:
        name = name.strip()
        async with self._lock:
            user = self.users.get(name)
            if user is None or user.resume_token != resume_token:
                raise ValueError("invalid resume token")
            if user.websocket is not None:
                raise ValueError("session is already connected")
            user.websocket = websocket
            user.connected = True
            self._cancel_disconnect_task(user)
            user.disconnect_task = None
            return user

    async def _expire_after(self, name: str, resume_token: str) -> None:
        try:
            await asyncio.sleep(self.disconnect_grace_seconds)
        except asyncio.CancelledError:
            return
        user = await self._pop_if_stale(name, resume_token)
        if user is not None and self.expiry_handler is not None:
            await self.expiry_handler(user)

    async def _pop_if_stale(self, name: str, resume_token: str) -> User | None:
        async with self._lock:
            user = self.users.get(name)
            if user is None or user.websocket is not None or user.resume_token != resume_token:
                return None
            return self.users.pop(name)

    @staticmethod
    def _cancel_disconnect_task(user: User) -> None:
        if user.disconnect_task is not None and not user.disconnect_task.done():
            user.disconnect_task.cancel()

    async def join_host(self, websocket: Connection) -> bool:
        async with self._lock:
            if self.host_connection is not None:
                return False
            self.host_connection = websocket
            self.host_view = "world"
            return True

    async def remove_host(self, websocket: Connection) -> None:
        async with self._lock:
            if self.host_connection is websocket:
                self.host_connection = None
                self.host_view = "world"

    async def add(self, role: str, websocket: Connection) -> bool:
        """Compatibility helper for focused story-plane tests."""
        if role not in self._role_set:
            return False
        async with self._lock:
            if any(user.role == role for user in self.users.values()):
                return False
            self.users[role] = User(role, websocket, role=role, view_role=role)
            return True

    async def assign_roles(
        self, assignments: dict[str, str]
    ) -> dict[str, str | None]:
        async with self._lock:
            if set(assignments) != self._role_set:
                raise ValueError("assignments must contain every role exactly once")
            usernames = list(assignments.values())
            if len(set(usernames)) != len(usernames):
                raise ValueError("each role must be assigned to a different user")
            if any(
                name not in self.users or not self.users[name].connected
                for name in usernames
            ):
                raise ValueError("assign roles only to currently connected users")

            requested = {name: role for role, name in assignments.items()}
            for user in self.users.values():
                old_role = user.role
                new_role = requested.get(user.name)
                if old_role == new_role:
                    continue
                user.role = new_role
                if new_role is not None:
                    user.view_role = new_role
                elif old_role is not None:
                    user.view_role = old_role
            return {name: user.role for name, user in self.users.items()}

    @property
    def roles_assigned(self) -> bool:
        return {user.role for user in self.users.values() if user.role} == self._role_set

    async def role_for(self, name: str) -> str | None:
        async with self._lock:
            user = self.users.get(name)
            return user.role if user else None

    async def user_for_role(self, role: str) -> User | None:
        self._validate_role(role)
        async with self._lock:
            return next((user for user in self.users.values() if user.role == role), None)

    async def role_assignments(self) -> dict[str, str]:
        async with self._lock:
            return {
                user.role: user.name
                for user in self.users.values()
                if user.role is not None
            }

    async def role_connections(self) -> dict[str, bool]:
        async with self._lock:
            return {
                user.role: user.connected
                for user in self.users.values()
                if user.role is not None
            }

    async def set_view(self, name: str, role: str) -> None:
        self._validate_role(role)
        async with self._lock:
            user = self.users.get(name)
            if user is None:
                raise ValueError("user is not connected")
            if user.role is not None:
                raise ValueError("players cannot switch role views")
            user.view_role = role

    async def set_host_view(self, view: str) -> None:
        if view != "world":
            self._validate_role(view)
        async with self._lock:
            self.host_view = view

    async def connections_by_role(self) -> dict[str, bool]:
        assignments = await self.role_assignments()
        return {role: role in assignments for role in self.role_ids}

    def _validate_role(self, role: str) -> None:
        if role not in self._role_set:
            raise ValueError(f"unknown role: {role}")

    async def presence_snapshot(self) -> dict:
        async with self._lock:
            users = [
                {"name": user.name, "role": user.role, "connected": user.connected}
                for user in self.users.values()
            ]
        return {"type": "presence", "users": users}

    async def broadcast(self, message: dict) -> None:
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            recipients = tuple(
                user.websocket
                for user in self.users.values()
                if user.websocket is not None
            )
        if recipients:
            await asyncio.gather(
                *(connection.send(payload) for connection in recipients),
                return_exceptions=True,
            )

    async def send_user(self, name: str, message: dict) -> None:
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            user = self.users.get(name)
            connection = user.websocket if user else None
        if connection is not None:
            await connection.send(payload)

    async def send(self, role: str, message: dict) -> None:
        self._validate_role(role)
        user = await self.user_for_role(role)
        if user is not None and user.websocket is not None:
            await user.websocket.send(json.dumps(message, ensure_ascii=False))

    async def send_viewers(self, role: str, message: dict) -> None:
        self._validate_role(role)
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            recipients = tuple(
                user.websocket
                for user in self.users.values()
                if user.view_role == role and user.websocket is not None
            )
            host = self.host_connection if self.host_view == role else None
        sends = [connection.send(payload) for connection in recipients]
        if host is not None:
            sends.append(host.send(payload))
        if sends:
            await asyncio.gather(*sends, return_exceptions=True)

    async def send_host(self, message: dict) -> None:
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            connection = self.host_connection
        if connection is not None:
            try:
                await connection.send(payload)
            except Exception:
                pass

    async def send_host_view(self, view: str, message: dict) -> None:
        if view != "world":
            self._validate_role(view)
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            connection = self.host_connection if self.host_view == view else None
        if connection is not None:
            try:
                await connection.send(payload)
            except Exception:
                pass
