import asyncio
import json
from dataclasses import dataclass

from websockets.asyncio.server import ServerConnection


@dataclass
class Participant:
    name: str
    is_host: bool
    role: str | None = None
    connected: bool = False


class Sessions:
    def __init__(self, saved_participants: list[dict] | None = None) -> None:
        self.participants: dict[str, Participant] = {}
        for saved in saved_participants or []:
            participant = Participant(
                saved["name"], bool(saved["is_host"]), saved.get("role")
            )
            self.participants[participant.name] = participant
        self.connections: dict[str, ServerConnection] = {}
        self._lock = asyncio.Lock()

    async def join(
        self, name: str, websocket: ServerConnection
    ) -> tuple[Participant, bool] | None:
        name = name.strip()
        if not name or len(name) > 32:
            raise ValueError("nickname must contain 1 to 32 characters")
        async with self._lock:
            participant = self.participants.get(name)
            if participant is not None:
                if name in self.connections:
                    raise ValueError("this nickname is already connected")
                participant.connected = True
                self.connections[name] = websocket
                return participant, False
            if len(self.participants) >= 2:
                return None
            participant = Participant(name, is_host=not self.participants, connected=True)
            self.participants[name] = participant
            self.connections[name] = websocket
            return participant, True

    async def add(self, role: str, websocket: ServerConnection) -> bool:
        """Compatibility helper used by focused round tests."""
        if role not in ("A", "B"):
            return False
        async with self._lock:
            if role in self.connections:
                return False
            participant = self.participants.get(role)
            if participant is None:
                participant = Participant(role, role == "A", role)
                self.participants[role] = participant
            participant.role = role
            participant.connected = True
            self.connections[role] = websocket
            return True

    async def assign_roles(self, host_name: str, host_role: str) -> dict[str, str]:
        if host_role not in ("A", "B"):
            raise ValueError("host_role must be A or B")
        async with self._lock:
            host = self.participants.get(host_name)
            if host is None or not host.is_host:
                raise ValueError("only the host can assign roles")
            if len(self.participants) != 2:
                raise ValueError("two participants are required before assigning roles")
            if self.roles_assigned:
                raise ValueError("roles have already been assigned")
            other = next(
                participant
                for name, participant in self.participants.items()
                if name != host_name
            )
            host.role = host_role
            other.role = "B" if host_role == "A" else "A"
            return {
                name: participant.role
                for name, participant in self.participants.items()
            }

    @property
    def roles_assigned(self) -> bool:
        return len(self.participants) == 2 and {
            participant.role for participant in self.participants.values()
        } == {"A", "B"}

    async def role_for(self, name: str) -> str | None:
        async with self._lock:
            participant = self.participants.get(name)
            return participant.role if participant else None

    async def remove(self, name: str, websocket: ServerConnection) -> None:
        async with self._lock:
            if self.connections.get(name) is websocket:
                del self.connections[name]
                self.participants[name].connected = False

    async def connections_by_role(self) -> dict[str, bool]:
        async with self._lock:
            return {
                participant.role: participant.connected
                for participant in self.participants.values()
                if participant.role is not None
            }

    async def lobby_snapshot(self) -> dict:
        async with self._lock:
            return {
                "type": "lobby",
                "assigned": self.roles_assigned,
                "participants": [
                    {
                        "name": participant.name,
                        "is_host": participant.is_host,
                        "role": participant.role,
                        "connected": participant.connected,
                    }
                    for participant in self.participants.values()
                ],
            }

    async def broadcast(self, message: dict) -> None:
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            recipients = tuple(self.connections.values())
        if recipients:
            await asyncio.gather(
                *(connection.send(payload) for connection in recipients),
                return_exceptions=True,
            )

    async def send(self, role: str, message: dict) -> None:
        payload = json.dumps(message, ensure_ascii=False)
        async with self._lock:
            name = next(
                (
                    participant.name
                    for participant in self.participants.values()
                    if participant.role == role
                ),
                None,
            )
            connection = self.connections.get(name) if name else None
        if connection is not None:
            await connection.send(payload)
