import json
import tempfile
import unittest
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from tests.support import user


class FakeConnection:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


class ScriptedConnection(FakeConnection):
    def __init__(self, incoming) -> None:
        super().__init__()
        self.incoming = list(incoming)

    async def recv(self) -> str:
        return self.incoming.pop(0)

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self, **_kwargs) -> None:
        pass


ALICE, BOB = user(1, "Alice"), user(2, "Bob")
JOIN = [json.dumps({"type": "join", "room_key": "test-key"})]


class GameOwnerTests(unittest.IsolatedAsyncioTestCase):
    async def make_server(self, owner_user_id: int | None) -> GameServer:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize()
        return GameServer(
            database, None, None, None,
            room_key="test-key", owner_user_id=owner_user_id,
        )

    async def test_owner_user_id_is_stored_in_game_metadata(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize()
        self.assertIsNone(await database.get_owner_user_id())
        await database.set_owner_user_id(17)
        self.assertEqual(await database.get_owner_user_id(), 17)

    async def test_host_capability_only_for_the_owner(self) -> None:
        server = await self.make_server(owner_user_id=1)
        self.assertTrue(server.is_host(1))
        self.assertFalse(server.is_host(2))

        ownerless = await self.make_server(owner_user_id=None)
        self.assertFalse(ownerless.is_host(1))

    async def test_owner_can_play_a_role_and_still_be_host(self) -> None:
        server = await self.make_server(owner_user_id=1)
        await server.sessions.join(ALICE, FakeConnection())
        await server.sessions.join(BOB, FakeConnection())
        await server.sessions.assign_roles({"P1": 1, "P2": 2})

        alice_connection = ScriptedConnection(JOIN)
        await server.public_handler(alice_connection, ALICE)
        joined = alice_connection.messages[0]
        self.assertEqual(joined["user"], {"id": 1, "username": "Alice"})
        self.assertEqual(joined["role"], "P1")
        self.assertTrue(joined["is_host"])

        bob_connection = ScriptedConnection(JOIN)
        await server.public_handler(bob_connection, BOB)
        bob_joined = bob_connection.messages[0]
        self.assertEqual(bob_joined["role"], "P2")
        self.assertFalse(bob_joined["is_host"])


if __name__ == "__main__":
    unittest.main()
