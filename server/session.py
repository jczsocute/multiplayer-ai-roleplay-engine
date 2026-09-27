import asyncio
import json
from dataclasses import dataclass

from websockets.asyncio.server import ServerConnection


@dataclass
class User:
    """One currently connected room user; never persisted."""

    name: str
    websocket: ServerConnection
    role: str | None = None
    view_role: str | None = None


class Sessions:
    def __init__(self, max_users: int = 100) -> None:
        self.max_users = max_users
        self.users: dict[str, User] = {}
        self.host_connection: ServerConnection | None = None
        self.host_view = "world"
        self._lock = asyncio.Lock()

    async def join(self, name: str, websocket: ServerConnection) -> User | None:
        name = name.strip()
        if not name or len(name) > 32:
            raise ValueError("nickname must contain 1 to 32 characters")
        async with self._lock:
            if name in self.users:
                raise ValueError("this nickname is already connected")
            if len(self.users) >= self.max_users:
                return None
            user = User(name, websocket)
            self.users[name] = user
            return user

    async def remove(self, name: str, websocket: ServerConnection) -> User | None:
        async with self._lock:
            user = self.users.get(name)
            if user is None or user.websocket is not websocket:
                return None
            return self.users.pop(name)

    async def join_host(self, websocket: ServerConnection) -> bool:
        async with self._lock:
            if self.host_connection is not None:
                return False
            self.host_connection = websocket
            self.host_view = "world"
            return True

    async def remove_host(self, websocket: ServerConnection) -> None:
        async with self._lock:
            if self.host_connection is websocket:
                self.host_connection = None
                self.host_view = "world"

    async def add(self, role: str, websocket: ServerConnection) -> bool:
        """Compatibility helper for focused story-plane tests."""
        if role not in ("A", "B"):
            return False
        async with self._lock:
            if any(user.role == role for user in self.users.values()):
                return False
            self.users[role] = User(role, websocket, role=role, view_role=role)
            return True

    async def assign_roles(self, player_a: str, player_b: str) -> dict[str, str | None]:
        async with self._lock:
            if player_a == player_b:
                raise ValueError("Player A and Player B must be different users")
            if player_a not in self.users or player_b not in self.users:
                raise ValueError("assign roles only to currently connected users")

            requested = {player_a: "A", player_b: "B"}
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
        return {user.role for user in self.users.values() if user.role} == {"A", "B"}

    async def role_for(self, name: str) -> str | None:
        async with self._lock:
            user = self.users.get(name)
            return user.role if user else None

    async def user_for_role(self, role: str) -> User | None:
        async with self._lock:
            return next((user for user in self.users.values() if user.role == role), None)

    async def role_assignments(self) -> dict[str, str]:
        async with self._lock:
            return {
                user.role: user.name
                for user in self.users.values()
                if user.role is not None
            }

    async def set_view(self, name: str, role: str) -> None:
        if role not in ("A", "B"):
            raise ValueError("view role must be A or B")
        async with self._lock:
            user = self.users.get(name)
            if user is None:
                raise ValueError("user is not connected")
            if user.role is not None:
                raise ValueError("players cannot switch role views")
            user.view_role = role

    async def set_host_view(self, view: str) -> None:
        if view not in ("A", "B", "world"):
            raise ValueError("host view must be A, B, or world")
        async with self._lock:
            self.host_view = view

    async def connections_by_role(self) -> dict[str, bool]:
        assignments = await self.role_assignments()
        return {role: role in assignments for role in ("A", "B")}

    async def presence_snapshot(self) -> dict:
        async with self._lock:
            users = [
                {"name": user.name, "role": user.role}
                for user in self.users.values()
            ]
        return {"type": "presence", "users": users}

    async def broadcast(self, message: dict) -> None:
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            recipients = tuple(user.websocket for user in self.users.values())
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
        user = await self.user_for_role(role)
        if user is not None:
            await user.websocket.send(json.dumps(message, ensure_ascii=False))

    async def send_viewers(self, role: str, message: dict) -> None:
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            recipients = tuple(
                user.websocket for user in self.users.values()
                if user.view_role == role
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
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            connection = self.host_connection if self.host_view == view else None
        if connection is not None:
            try:
                await connection.send(payload)
            except Exception:
                pass
