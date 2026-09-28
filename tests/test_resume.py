import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from server.database import Database
from server.main import GameServer
from server.models import PlayerStatus
from server.protocol import PROTOCOL_VERSION
from server.session import Sessions

KEY = "TEST-KEY"


class ScriptedConnection:
    """Consumes a fixed list of incoming JSON strings, then disconnects."""

    def __init__(self, incoming=None) -> None:
        self.incoming = list(incoming or [])
        self.messages: list[dict] = []
        self.closed = False

    async def recv(self) -> str:
        return self.incoming.pop(0)

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))

    async def close(self, code: int = 1000) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self.incoming:
            raise StopAsyncIteration
        return self.incoming.pop(0)


def join_message(name: str, room_key: str = KEY) -> str:
    return json.dumps({"type": "join", "name": name, "room_key": room_key})


def resume_message(name: str, token: str, room_key: str = KEY) -> str:
    return json.dumps({
        "type": "resume", "name": name, "resume_token": token, "room_key": room_key,
    })


class RoomKeyTests(unittest.IsolatedAsyncioTestCase):
    async def make_game(self, grace: int = 1) -> GameServer:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize()
        return GameServer(
            database, None, None, None,
            room_key=KEY, disconnect_grace_seconds=grace,
        )

    async def test_wrong_room_key_rejected_without_occupying_nickname(self) -> None:
        game = await self.make_game()
        connection = ScriptedConnection([join_message("Alice", "WRONG")])
        await game.public_handler(connection)
        self.assertEqual(connection.messages[0]["type"], "error")
        self.assertIn("invalid room key", connection.messages[0]["detail"])
        self.assertTrue(connection.closed)
        self.assertEqual(game.sessions.users, {})

    async def test_join_returns_protocol_v3_and_resume_token(self) -> None:
        game = await self.make_game()
        connection = ScriptedConnection([join_message("Alice")])
        await game.public_handler(connection)
        joined = connection.messages[0]
        self.assertEqual(joined["type"], "joined")
        self.assertEqual(joined["protocol_version"], PROTOCOL_VERSION)
        self.assertTrue(joined["resume_token"])
        self.assertEqual(joined["resume_token"], game.sessions.users["Alice"].resume_token)

    async def test_public_endpoint_still_rejects_join_host(self) -> None:
        game = await self.make_game()
        connection = ScriptedConnection([json.dumps({"type": "join_host"})])
        await game.public_handler(connection)
        self.assertEqual(connection.messages[0]["type"], "error")
        self.assertTrue(connection.closed)
        self.assertIsNone(game.sessions.host_connection)

    async def test_no_room_key_mode_accepts_join_without_key(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize()
        game = GameServer(database, None, None, None, room_key="", disconnect_grace_seconds=1)

        connection = ScriptedConnection([json.dumps({"type": "join", "name": "Alice"})])
        await game.public_handler(connection)
        self.assertEqual(connection.messages[0]["type"], "joined")
        self.assertIn("Alice", game.sessions.users)


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    async def make_game(self, grace: int = 1) -> GameServer:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize()
        return GameServer(
            database, None, None, None,
            room_key=KEY, disconnect_grace_seconds=grace,
        )

    async def assign_two_roles(self, game: GameServer):
        alice = ScriptedConnection()
        bob = ScriptedConnection()
        await game.sessions.join("Alice", alice)
        await game.sessions.join("Bob", bob)
        await game.sessions.assign_roles({"P1": "Alice", "P2": "Bob"})
        return alice, bob

    async def test_disconnect_enters_grace_and_preserves_identity(self) -> None:
        game = await self.make_game()
        alice, _bob = await self.assign_two_roles(game)
        token = game.sessions.users["Alice"].resume_token

        disconnected = await game.sessions.mark_disconnected("Alice", alice)
        self.assertIsNotNone(disconnected)
        self.assertIn("Alice", game.sessions.users)
        self.assertFalse(game.sessions.users["Alice"].connected)
        self.assertIsNone(game.sessions.users["Alice"].websocket)
        self.assertEqual(await game.sessions.role_for("Alice"), "P1")
        self.assertEqual(game.sessions.users["Alice"].resume_token, token)

        snapshot = await game._round_snapshot()
        self.assertFalse(snapshot["players"]["P1"]["connected"])
        self.assertEqual(snapshot["players"]["P1"]["user"], "Alice")

    async def test_resume_restores_role_draft_and_ready_status(self) -> None:
        game = await self.make_game()
        alice, _bob = await self.assign_two_roles(game)
        game.rounds.set_action("P1", "检查房门")
        game.rounds.submit("P1")
        token = game.sessions.users["Alice"].resume_token
        await game.sessions.mark_disconnected("Alice", alice)

        connection = ScriptedConnection([resume_message("Alice", token)])
        await game.public_handler(connection)

        resumed = next(m for m in connection.messages if m["type"] == "resumed")
        self.assertEqual(resumed["role"], "P1")
        self.assertEqual(resumed["view_role"], "P1")
        self.assertEqual(resumed["protocol_version"], PROTOCOL_VERSION)
        state = next(m for m in connection.messages if m["type"] == "state")
        self.assertEqual(state["players"]["P1"]["status"], "READY")
        self.assertTrue(state["players"]["P1"]["connected"])
        role_view = next(m for m in connection.messages if m["type"] == "role_view")
        self.assertEqual(role_view["draft"], "检查房门")

    async def test_resume_restores_paused_status(self) -> None:
        game = await self.make_game()
        alice, _bob = await self.assign_two_roles(game)
        game.rounds.pause("P1")
        token = game.sessions.users["Alice"].resume_token
        await game.sessions.mark_disconnected("Alice", alice)

        connection = ScriptedConnection([resume_message("Alice", token)])
        await game.public_handler(connection)
        state = next(m for m in connection.messages if m["type"] == "state")
        self.assertEqual(state["players"]["P1"]["status"], PlayerStatus.PAUSED.value)

    async def test_resume_restores_spectator_view(self) -> None:
        game = await self.make_game()
        tom = ScriptedConnection()
        await game.sessions.join("Tom", tom)
        await game.sessions.set_view("Tom", "P2")
        token = game.sessions.users["Tom"].resume_token
        await game.sessions.mark_disconnected("Tom", tom)

        connection = ScriptedConnection([resume_message("Tom", token)])
        await game.public_handler(connection)
        resumed = next(m for m in connection.messages if m["type"] == "resumed")
        self.assertIsNone(resumed["role"])
        self.assertEqual(resumed["view_role"], "P2")
        role_view = next(m for m in connection.messages if m["type"] == "role_view")
        self.assertEqual(role_view["role"], "P2")

    async def test_invalid_or_mismatched_token_rejected(self) -> None:
        game = await self.make_game()
        alice, _bob = await self.assign_two_roles(game)
        token = game.sessions.users["Alice"].resume_token
        await game.sessions.mark_disconnected("Alice", alice)

        bad_token = ScriptedConnection([resume_message("Alice", "wrong-token")])
        await game.public_handler(bad_token)
        self.assertEqual(bad_token.messages[0]["type"], "error")
        self.assertTrue(bad_token.closed)
        self.assertEqual(game.sessions.users["Alice"].resume_token, token)

        wrong_name = ScriptedConnection([resume_message("Bob", token)])
        await game.public_handler(wrong_name)
        self.assertEqual(wrong_name.messages[0]["type"], "error")
        self.assertTrue(wrong_name.closed)

    async def test_grace_expiry_releases_nickname_and_role(self) -> None:
        game = await self.make_game(grace=1)
        alice, _bob = await self.assign_two_roles(game)
        token = game.sessions.users["Alice"].resume_token
        await game.sessions.mark_disconnected("Alice", alice)

        await asyncio.sleep(1.2)
        self.assertNotIn("Alice", game.sessions.users)

        expired = ScriptedConnection([resume_message("Alice", token)])
        await game.public_handler(expired)
        self.assertEqual(expired.messages[0]["type"], "error")
        self.assertTrue(expired.closed)

        replacement = await game.sessions.join("Alice", ScriptedConnection())
        self.assertIsNotNone(replacement)
        self.assertIsNone(replacement.role)

    async def test_explicit_leave_releases_immediately_and_invalidates_token(self) -> None:
        game = await self.make_game()
        alice, _bob = await self.assign_two_roles(game)
        token = game.sessions.users["Alice"].resume_token

        connection = ScriptedConnection([join_message("Alice"), json.dumps({"type": "leave"})])
        await game.sessions.remove("Alice", alice)  # simulate leave on the live socket
        self.assertNotIn("Alice", game.sessions.users)

        resume = ScriptedConnection([resume_message("Alice", token)])
        await game.public_handler(resume)
        self.assertEqual(resume.messages[0]["type"], "error")
        self.assertTrue(resume.closed)
        self.assertIsNone(await game.sessions.role_for("Alice"))

    async def test_temporary_disconnect_no_leave_and_resume_broadcasts_reconnect(self) -> None:
        game = await self.make_game()
        watcher = ScriptedConnection()
        await game.sessions.join("Watcher", watcher)
        alice = ScriptedConnection([join_message("Alice")])
        await game.public_handler(alice)

        room_texts = [
            m.get("text") for m in watcher.messages if m.get("type") == "room_message"
        ]
        self.assertIn("Alice 已加入房间。", room_texts)
        self.assertNotIn("Alice 已离开房间。", room_texts)

        token = game.sessions.users["Alice"].resume_token
        resumed_conn = ScriptedConnection([resume_message("Alice", token)])
        await game.public_handler(resumed_conn)
        room_texts = [
            m.get("text") for m in watcher.messages if m.get("type") == "room_message"
        ]
        self.assertIn("Alice 已重新连接。", room_texts)


class RaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_websocket_finally_cannot_remove_resumed_user(self) -> None:
        sessions = Sessions(disconnect_grace_seconds=60)
        old = ScriptedConnection()
        await sessions.join("Alice", old)
        token = sessions.users["Alice"].resume_token
        await sessions.mark_disconnected("Alice", old)

        new = ScriptedConnection()
        await sessions.resume("Alice", token, new)

        # A late finally from the old socket must be a no-op.
        self.assertIsNone(await sessions.mark_disconnected("Alice", old))
        self.assertIs(sessions.users["Alice"].websocket, new)
        self.assertTrue(sessions.users["Alice"].connected)

    async def test_cleanup_task_does_not_remove_after_resume(self) -> None:
        sessions = Sessions(disconnect_grace_seconds=1)
        old = ScriptedConnection()
        await sessions.join("Alice", old)
        token = sessions.users["Alice"].resume_token
        await sessions.mark_disconnected("Alice", old)
        await sessions.resume("Alice", token, ScriptedConnection())

        await asyncio.sleep(1.2)
        self.assertIn("Alice", sessions.users)
        self.assertTrue(sessions.users["Alice"].connected)


if __name__ == "__main__":
    unittest.main()
