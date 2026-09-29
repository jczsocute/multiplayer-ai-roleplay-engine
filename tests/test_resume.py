import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.gameserver.models import PlayerStatus
from server.gameserver.protocol import PROTOCOL_VERSION
from server.gameserver.session import Sessions
from tests.support import user

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


def join_message(room_key: str = KEY) -> str:
    return json.dumps({"type": "join", "room_key": room_key})


def resume_message(token: str, room_key: str = KEY) -> str:
    return json.dumps({"type": "resume", "resume_token": token, "room_key": room_key})


def identity_notices(connection: ScriptedConnection) -> list[dict]:
    return [
        message for message in connection.messages
        if message.get("type") == "room_message"
        and message.get("text", "").startswith("您目前")
    ]


ALICE = user(1, "Alice")
BOB = user(2, "Bob")
TOM = user(3, "Tom")


class RoomKeyTests(unittest.IsolatedAsyncioTestCase):
    async def make_game(self, grace: int = 1) -> GameServer:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize()
        return GameServer(
            database, None, None,
            room_key=KEY, disconnect_grace_seconds=grace,
        )

    async def test_wrong_room_key_rejected_without_occupying_identity(self) -> None:
        game = await self.make_game()
        connection = ScriptedConnection([join_message("WRONG")])
        await game.public_handler(connection, ALICE)
        self.assertEqual(connection.messages[0]["type"], "error")
        self.assertIn("房间密钥无效", connection.messages[0]["detail"])
        self.assertTrue(connection.closed)
        self.assertEqual(game.sessions.users, {})

    async def test_join_returns_protocol_v4_user_and_resume_token(self) -> None:
        game = await self.make_game()
        connection = ScriptedConnection([join_message()])
        await game.public_handler(connection, ALICE)
        joined = connection.messages[0]
        self.assertEqual(joined["type"], "joined")
        self.assertEqual(joined["protocol_version"], PROTOCOL_VERSION)
        self.assertEqual(joined["user"], {"id": 1, "username": "Alice"})
        self.assertFalse(joined["is_host"])
        self.assertTrue(joined["resume_token"])
        self.assertEqual(joined["resume_token"], game.sessions.users[1].resume_token)

    async def test_public_endpoint_still_rejects_join_host(self) -> None:
        game = await self.make_game()
        connection = ScriptedConnection([json.dumps({"type": "join_host"})])
        await game.public_handler(connection, ALICE)
        self.assertEqual(connection.messages[0]["type"], "error")
        self.assertTrue(connection.closed)

    async def test_no_room_key_mode_accepts_join_without_key(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize()
        game = GameServer(database, None, None, room_key="", disconnect_grace_seconds=1)

        connection = ScriptedConnection([json.dumps({"type": "join"})])
        await game.public_handler(connection, ALICE)
        self.assertEqual(connection.messages[0]["type"], "joined")
        self.assertIn(1, game.sessions.users)


class ResumeTests(unittest.IsolatedAsyncioTestCase):
    async def make_game(
        self, grace: int = 1, owner_user_id: int | None = None,
        character_names: dict[str, str] | None = None,
    ) -> GameServer:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize()
        return GameServer(
            database, None, None,
            room_key=KEY, disconnect_grace_seconds=grace,
            owner_user_id=owner_user_id, character_names=character_names,
        )

    async def assign_two_roles(self, game: GameServer):
        alice = ScriptedConnection()
        bob = ScriptedConnection()
        await game.sessions.join(ALICE, alice)
        await game.sessions.join(BOB, bob)
        await game.sessions.assign_roles({"P1": 1, "P2": 2})
        return alice, bob

    async def test_new_spectator_receives_private_identity_notice(self) -> None:
        game = await self.make_game()
        watcher = ScriptedConnection()
        await game.sessions.join(BOB, watcher)

        connection = ScriptedConnection([join_message()])
        await game.public_handler(connection, ALICE)

        notices = identity_notices(connection)
        self.assertEqual(len(notices), 1)
        self.assertEqual(notices[0], {
            "type": "room_message", "kind": "system", "sender": None,
            "role": None, "character_name": None,
            "text": "您目前身份为 <观众>。请等待房主分配角色。",
        })
        self.assertEqual(identity_notices(watcher), [])
        self.assertIn("Alice 已加入房间。", [
            message.get("text") for message in watcher.messages
        ])
        self.assertEqual(await game.database.get_narrator_history("P1", 20), [])

    async def test_role_name_notice_on_rejoin_and_resume(self) -> None:
        game = await self.make_game(
            grace=60, character_names={"P1": "路人甲", "P2": "路人乙"},
        )
        _alice, bob = await self.assign_two_roles(game)
        await game.sessions.mark_disconnected(2, bob)

        rejoined = ScriptedConnection([join_message()])
        await game.public_handler(rejoined, BOB)
        text = "您目前扮演 <路人乙>。请继续游戏。"
        self.assertEqual([message["text"] for message in identity_notices(rejoined)], [text])
        self.assertLess(
            next(i for i, message in enumerate(rejoined.messages) if message["type"] == "role_view"),
            next(i for i, message in enumerate(rejoined.messages) if message.get("text") == text),
        )

        token = game.sessions.users[2].resume_token
        resumed = ScriptedConnection([resume_message(token)])
        await game.public_handler(resumed, BOB)
        self.assertEqual([message["text"] for message in identity_notices(resumed)], [text])

    async def test_host_notice_takes_priority_over_assigned_role(self) -> None:
        game = await self.make_game(
            grace=60, owner_user_id=1,
            character_names={"P1": "路人甲", "P2": "路人乙"},
        )
        alice, _bob = await self.assign_two_roles(game)
        await game.sessions.mark_disconnected(1, alice)

        connection = ScriptedConnection([join_message()])
        await game.public_handler(connection, ALICE)
        self.assertEqual(
            [message["text"] for message in identity_notices(connection)],
            ["您目前身份为 <房主>。"],
        )

    async def test_disconnect_enters_grace_and_preserves_identity(self) -> None:
        game = await self.make_game()
        alice, _bob = await self.assign_two_roles(game)
        token = game.sessions.users[1].resume_token

        disconnected = await game.sessions.mark_disconnected(1, alice)
        self.assertIsNotNone(disconnected)
        self.assertIn(1, game.sessions.users)
        self.assertFalse(game.sessions.users[1].connected)
        self.assertIsNone(game.sessions.users[1].websocket)
        self.assertEqual(await game.sessions.role_for(1), "P1")
        self.assertEqual(game.sessions.users[1].resume_token, token)

        snapshot = await game._round_snapshot()
        self.assertFalse(snapshot["players"]["P1"]["connected"])
        self.assertEqual(snapshot["players"]["P1"]["user"], "Alice")
        self.assertEqual(snapshot["players"]["P1"]["user_id"], 1)

    async def test_reconnect_without_resume_token_reclaims_participant(self) -> None:
        """Closed-tab case: the cookie proves user 1, so the role is reclaimed."""
        game = await self.make_game(grace=60)
        alice, _bob = await self.assign_two_roles(game)
        game.rounds.set_action("P1", "检查房门")
        await game.sessions.mark_disconnected(1, alice)

        connection = ScriptedConnection([join_message()])
        await game.public_handler(connection, ALICE)

        joined = connection.messages[0]
        self.assertEqual(joined["type"], "joined")
        self.assertTrue(joined["reclaimed"])
        self.assertEqual(joined["role"], "P1")
        self.assertEqual(joined["view_role"], "P1")
        self.assertEqual(game.sessions.users[1].role, "P1")
        role_view = next(m for m in connection.messages if m["type"] == "role_view")
        self.assertEqual(role_view["draft"], "检查房门")

    async def test_duplicate_connection_takes_over_and_replaces_old(self) -> None:
        game = await self.make_game(grace=60)
        alice, _bob = await self.assign_two_roles(game)

        replacement = ScriptedConnection([join_message()])
        await game.public_handler(replacement, ALICE)

        self.assertTrue(alice.closed)
        self.assertEqual(alice.messages[-1]["type"], "session_replaced")
        self.assertEqual(len(identity_notices(replacement)), 1)
        self.assertEqual(identity_notices(alice), [])
        self.assertEqual(game.sessions.users[1].role, "P1")
        presence = await game.sessions.presence_snapshot()
        self.assertEqual([item["user_id"] for item in presence["users"]].count(1), 1)

    async def test_sessions_takeover_keeps_one_participant_and_retires_old(self) -> None:
        sessions = Sessions(disconnect_grace_seconds=60)
        old = ScriptedConnection()
        first = await sessions.join(ALICE, old)
        self.assertTrue(first.created)

        new = ScriptedConnection()
        second = await sessions.join(ALICE, new)
        self.assertFalse(second.created)
        self.assertIs(second.replaced_connection, old)
        self.assertIs(sessions.users[1].websocket, new)
        self.assertEqual(len(sessions.users), 1)

    async def test_different_user_cannot_take_over_an_occupied_role(self) -> None:
        game = await self.make_game(grace=60)
        alice, _bob = await self.assign_two_roles(game)
        await game.sessions.mark_disconnected(1, alice)

        impostor = ScriptedConnection([join_message()])
        await game.public_handler(impostor, user(9, "Alice"))

        self.assertFalse(impostor.messages[0].get("reclaimed", False))
        self.assertIsNone(game.sessions.users[9].role)
        self.assertEqual(game.sessions.users[1].role, "P1")

    async def test_resume_restores_role_draft_and_ready_status(self) -> None:
        game = await self.make_game()
        alice, _bob = await self.assign_two_roles(game)
        game.rounds.set_action("P1", "检查房门")
        game.rounds.submit("P1")
        token = game.sessions.users[1].resume_token
        await game.sessions.mark_disconnected(1, alice)

        connection = ScriptedConnection([resume_message(token)])
        await game.public_handler(connection, ALICE)

        resumed = next(m for m in connection.messages if m["type"] == "resumed")
        self.assertEqual(resumed["role"], "P1")
        self.assertEqual(resumed["view_role"], "P1")
        self.assertEqual(resumed["protocol_version"], PROTOCOL_VERSION)
        self.assertEqual(resumed["user"], {"id": 1, "username": "Alice"})
        state = next(m for m in connection.messages if m["type"] == "state")
        self.assertEqual(state["players"]["P1"]["status"], "READY")
        self.assertTrue(state["players"]["P1"]["connected"])
        role_view = next(m for m in connection.messages if m["type"] == "role_view")
        self.assertEqual(role_view["draft"], "检查房门")

    async def test_resume_restores_paused_status(self) -> None:
        game = await self.make_game()
        alice, _bob = await self.assign_two_roles(game)
        game.rounds.pause("P1")
        token = game.sessions.users[1].resume_token
        await game.sessions.mark_disconnected(1, alice)

        connection = ScriptedConnection([resume_message(token)])
        await game.public_handler(connection, ALICE)
        state = next(m for m in connection.messages if m["type"] == "state")
        self.assertEqual(state["players"]["P1"]["status"], PlayerStatus.PAUSED.value)

    async def test_resume_restores_spectator_view(self) -> None:
        game = await self.make_game()
        tom = ScriptedConnection()
        await game.sessions.join(TOM, tom)
        await game.sessions.set_view(3, "P2")
        token = game.sessions.users[3].resume_token
        await game.sessions.mark_disconnected(3, tom)

        connection = ScriptedConnection([resume_message(token)])
        await game.public_handler(connection, TOM)
        resumed = next(m for m in connection.messages if m["type"] == "resumed")
        self.assertIsNone(resumed["role"])
        self.assertEqual(resumed["view_role"], "P2")
        role_view = next(m for m in connection.messages if m["type"] == "role_view")
        self.assertEqual(role_view["role"], "P2")

    async def test_invalid_token_or_other_account_rejected(self) -> None:
        game = await self.make_game()
        alice, _bob = await self.assign_two_roles(game)
        token = game.sessions.users[1].resume_token
        await game.sessions.mark_disconnected(1, alice)

        bad_token = ScriptedConnection([resume_message("wrong-token")])
        await game.public_handler(bad_token, ALICE)
        self.assertEqual(bad_token.messages[0]["type"], "error")
        self.assertTrue(bad_token.closed)
        self.assertEqual(game.sessions.users[1].resume_token, token)

        # A different account holding Alice's token cannot resume her connection.
        wrong_user = ScriptedConnection([resume_message(token)])
        await game.public_handler(wrong_user, BOB)
        self.assertEqual(wrong_user.messages[0]["type"], "error")
        self.assertTrue(wrong_user.closed)
        self.assertEqual(game.sessions.users[1].role, "P1")

    async def test_grace_expiry_releases_role(self) -> None:
        game = await self.make_game(grace=1)
        alice, _bob = await self.assign_two_roles(game)
        token = game.sessions.users[1].resume_token
        await game.sessions.mark_disconnected(1, alice)

        await asyncio.sleep(1.2)
        self.assertNotIn(1, game.sessions.users)

        expired = ScriptedConnection([resume_message(token)])
        await game.public_handler(expired, ALICE)
        self.assertEqual(expired.messages[0]["type"], "error")
        self.assertTrue(expired.closed)

        replacement = await game.sessions.join(ALICE, ScriptedConnection())
        self.assertIsNotNone(replacement)
        self.assertIsNone(replacement.user.role)

    async def test_explicit_leave_releases_immediately_and_invalidates_token(self) -> None:
        game = await self.make_game()
        alice, _bob = await self.assign_two_roles(game)
        token = game.sessions.users[1].resume_token

        await game.sessions.remove(1, alice)  # simulate leave on the live socket
        self.assertNotIn(1, game.sessions.users)

        resume = ScriptedConnection([resume_message(token)])
        await game.public_handler(resume, ALICE)
        self.assertEqual(resume.messages[0]["type"], "error")
        self.assertTrue(resume.closed)
        self.assertIsNone(await game.sessions.role_for(1))

    async def test_temporary_disconnect_no_leave_and_resume_broadcasts_reconnect(self) -> None:
        game = await self.make_game()
        watcher = ScriptedConnection()
        await game.sessions.join(user(99, "Watcher"), watcher)
        alice = ScriptedConnection([join_message()])
        await game.public_handler(alice, ALICE)

        room_texts = [
            m.get("text") for m in watcher.messages if m.get("type") == "room_message"
        ]
        self.assertIn("Alice 已加入房间。", room_texts)
        self.assertNotIn("Alice 已离开房间。", room_texts)

        token = game.sessions.users[1].resume_token
        resumed_conn = ScriptedConnection([resume_message(token)])
        await game.public_handler(resumed_conn, ALICE)
        room_texts = [
            m.get("text") for m in watcher.messages if m.get("type") == "room_message"
        ]
        self.assertIn("Alice 已重新连接。", room_texts)


class RaceTests(unittest.IsolatedAsyncioTestCase):
    async def test_old_websocket_finally_cannot_remove_resumed_user(self) -> None:
        sessions = Sessions(disconnect_grace_seconds=60)
        old = ScriptedConnection()
        await sessions.join(ALICE, old)
        token = sessions.users[1].resume_token
        await sessions.mark_disconnected(1, old)

        new = ScriptedConnection()
        await sessions.resume(1, token, new)

        # A late finally from the old socket must be a no-op.
        self.assertIsNone(await sessions.mark_disconnected(1, old))
        self.assertIs(sessions.users[1].websocket, new)
        self.assertTrue(sessions.users[1].connected)

    async def test_cleanup_task_does_not_remove_after_resume(self) -> None:
        sessions = Sessions(disconnect_grace_seconds=1)
        old = ScriptedConnection()
        await sessions.join(ALICE, old)
        token = sessions.users[1].resume_token
        await sessions.mark_disconnected(1, old)
        await sessions.resume(1, token, ScriptedConnection())

        await asyncio.sleep(1.2)
        self.assertIn(1, sessions.users)
        self.assertTrue(sessions.users[1].connected)


if __name__ == "__main__":
    unittest.main()
