import asyncio
import json
import secrets
from dataclasses import dataclass, field
from typing import Awaitable, Callable, Protocol

class Connection(Protocol):
    async def send(self, payload: str) -> None: ...


class AccountIdentity(Protocol):
    """Minimal trusted identity supplied by the outer platform adapter."""

    id: int
    username: str


@dataclass
class User:
    """One runtime game participant, keyed by stable account id; never persisted.

    ``connected`` means the participant currently has a live websocket.  After an
    unexpected disconnect the participant stays in :class:`Sessions` for the grace
    period with ``websocket=None`` and ``connected=False`` so the same account can
    reclaim its username, role, view and draft.

    ``user_id`` is the identity; ``username`` is display/login only.
    """

    user_id: int
    username: str
    websocket: Connection | None = None
    role: str | None = None
    view_role: str | None = None
    resume_token: str = field(default_factory=lambda: secrets.token_urlsafe(24))
    connected: bool = True
    disconnect_task: asyncio.Task | None = None


@dataclass(frozen=True)
class JoinResult:
    user: User
    created: bool = False
    reclaimed: bool = False
    replaced_connection: Connection | None = None


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
        self.users: dict[int, User] = {}
        self.expiry_handler: Callable[[User], Awaitable[None]] | None = None
        self._lock = asyncio.Lock()

    async def join(self, account: AccountIdentity, websocket: Connection) -> JoinResult | None:
        """Create, reclaim or take over the participant for this account."""
        async with self._lock:
            existing = self.users.get(account.id)
            if existing is None:
                if len(self.users) >= self.max_users:
                    return None
                participant = User(
                    user_id=account.id, username=account.username, websocket=websocket
                )
                self.users[account.id] = participant
                return JoinResult(participant, created=True)
            previous = existing.websocket
            existing.username = account.username
            existing.websocket = websocket
            existing.connected = True
            self._cancel_disconnect_task(existing)
            existing.disconnect_task = None
            replaced = previous if previous is not None and previous is not websocket else None
            return JoinResult(
                existing, replaced_connection=replaced, reclaimed=replaced is None
            )

    async def remove(self, user_id: int, websocket: Connection) -> User | None:
        """Remove immediately; used for explicit leave and legacy tests."""
        async with self._lock:
            user = self.users.get(user_id)
            if user is None or user.websocket is not websocket:
                return None
            self._cancel_disconnect_task(user)
            return self.users.pop(user_id)

    async def evict(self, user_id: int) -> User | None:
        """Remove a participant regardless of which connection it holds.

        Used by the disconnect timeout and by host kick, so both paths release the
        seat, the role assignment and the identity through one code path.
        """
        async with self._lock:
            user = self.users.pop(user_id, None)
            if user is not None:
                self._cancel_disconnect_task(user)
            return user

    async def mark_disconnected(self, user_id: int, old_websocket: Connection) -> User | None:
        """Move a participant into the disconnect grace period.

        Only takes effect when ``old_websocket`` is still the participant's current
        connection, so a late ``finally`` from an old socket cannot detach an account
        that was already taken over by a newer connection.
        """
        async with self._lock:
            user = self.users.get(user_id)
            if user is None or user.websocket is not old_websocket:
                return None
            user.websocket = None
            user.connected = False
            self._cancel_disconnect_task(user)
            token = user.resume_token
            user.disconnect_task = asyncio.create_task(self._expire_after(user_id, token))
            return user

    async def resume(
        self, user_id: int, resume_token: str, websocket: Connection
    ) -> User:
        async with self._lock:
            user = self.users.get(user_id)
            if user is None or user.resume_token != resume_token:
                raise ValueError("会话凭证已失效，请重新加入房间")
            if user.websocket is not None:
                raise ValueError("该账号的会话已经连接")
            user.websocket = websocket
            user.connected = True
            self._cancel_disconnect_task(user)
            user.disconnect_task = None
            return user

    async def _expire_after(self, user_id: int, resume_token: str) -> None:
        try:
            await asyncio.sleep(self.disconnect_grace_seconds)
        except asyncio.CancelledError:
            return
        user = await self._pop_if_stale(user_id, resume_token)
        if user is not None and self.expiry_handler is not None:
            await self.expiry_handler(user)

    async def _pop_if_stale(self, user_id: int, resume_token: str) -> User | None:
        async with self._lock:
            user = self.users.get(user_id)
            if user is None or user.websocket is not None or user.resume_token != resume_token:
                return None
            return self.users.pop(user_id)

    @staticmethod
    def _cancel_disconnect_task(user: User) -> None:
        if user.disconnect_task is not None and not user.disconnect_task.done():
            user.disconnect_task.cancel()

    async def assign_roles(self, assignments: dict[str, int]) -> None:
        """Bind roles to account ids. Values are ``user_id``, never usernames."""
        async with self._lock:
            if set(assignments) != self._role_set:
                raise ValueError("每个角色都必须分配且只能分配一个成员")
            user_ids = list(assignments.values())
            if len(set(user_ids)) != len(user_ids):
                raise ValueError("每个角色必须分配给不同的成员")
            if any(
                user_id not in self.users or not self.users[user_id].connected
                for user_id in user_ids
            ):
                raise ValueError("只能把角色分配给当前在线的成员")

            requested = {user_id: role for role, user_id in assignments.items()}
            for user in self.users.values():
                old_role = user.role
                new_role = requested.get(user.user_id)
                if old_role == new_role:
                    continue
                user.role = new_role
                if new_role is not None:
                    user.view_role = new_role
                elif old_role is not None:
                    user.view_role = old_role

    @property
    def roles_assigned(self) -> bool:
        return {user.role for user in self.users.values() if user.role} == self._role_set

    async def role_for(self, user_id: int) -> str | None:
        async with self._lock:
            user = self.users.get(user_id)
            return user.role if user else None

    async def username(self, user_id: int) -> str | None:
        async with self._lock:
            user = self.users.get(user_id)
            return user.username if user else None

    async def user_for_role(self, role: str) -> User | None:
        self._validate_role(role)
        async with self._lock:
            return next((user for user in self.users.values() if user.role == role), None)

    async def role_assignments(self) -> dict[str, int]:
        """Role id -> account id for every currently occupied role."""
        async with self._lock:
            return {
                user.role: user.user_id
                for user in self.users.values()
                if user.role is not None
            }

    async def role_usernames(self) -> dict[str, str]:
        """Role id -> display username for every currently occupied role."""
        async with self._lock:
            return {
                user.role: user.username
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

    async def set_view(self, user_id: int, role: str) -> None:
        self._validate_role(role)
        async with self._lock:
            user = self.users.get(user_id)
            if user is None:
                raise ValueError("该成员当前不在线")
            if user.role is not None:
                raise ValueError("玩家不能切换查看视角")
            user.view_role = role

    def _validate_role(self, role: str) -> None:
        if role not in self._role_set:
            raise ValueError(f"未知角色：{role}")

    async def presence_snapshot(self) -> dict:
        async with self._lock:
            users = [
                {
                    "user_id": user.user_id,
                    "name": user.username,
                    "role": user.role,
                    "connected": user.connected,
                }
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

    async def send_user(self, user_id: int, message: dict) -> None:
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            user = self.users.get(user_id)
            connection = user.websocket if user else None
        if connection is not None:
            await connection.send(payload)

    async def send_viewers(self, role: str, message: dict) -> None:
        self._validate_role(role)
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            recipients = tuple(
                user.websocket
                for user in self.users.values()
                if user.view_role == role and user.websocket is not None
            )
        if recipients:
            await asyncio.gather(
                *(connection.send(payload) for connection in recipients),
                return_exceptions=True,
            )

    async def close_all(self, message: dict) -> None:
        """Notify and close every live connection for runtime shutdown."""
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            connections = [
                user.websocket for user in self.users.values()
                if user.websocket is not None
            ]
        for connection in connections:
            try:
                await connection.send(payload)
                await connection.close(code=1001)
            except Exception:
                pass
        # A closed Room keeps no participants: clearing here also cancels the
        # disconnect timers so no ghost expiry fires after the Room is gone.
        async with self._lock:
            for user in self.users.values():
                self._cancel_disconnect_task(user)
            self.users.clear()
