import json
import tempfile
import unittest
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.gameserver.models import PlayerStatus
from server.gameserver.protocol import MAX_ACTION_LENGTH
from server.gameserver.session import Sessions
from tests.support import user


class FakeConnection:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


ALICE, BOB, TOM = user(1, "Alice"), user(2, "Bob"), user(3, "Tom")


class RoomProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_common_protocol_errors_have_codes_and_chinese_details(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            server = GameServer(database, None, None, owner_user_id=1)
            socket = FakeConnection()
            await server.sessions.join(ALICE, socket)
            for message, code in (
                ({"type": "room_chat", "text": "  "}, "empty_chat_message"),
                ([], "invalid_json_object"),
                ({"type": "rollback", "round": 0}, "invalid_rollback_round"),
            ):
                await server._handle_command(1, socket, json.dumps(message))
                self.assertEqual(socket.messages[-1]["code"], code)
                self.assertTrue(any("\u4e00" <= char <= "\u9fff" for char in socket.messages[-1]["detail"]))
            await server.sessions.join(BOB, FakeConnection())
            await server.sessions.assign_roles({"P1": 1, "P2": 2})
            await server._handle_command(1, socket, json.dumps({
                "type": "action", "text": "x" * (MAX_ACTION_LENGTH + 1),
            }))
            self.assertEqual(socket.messages[-1]["code"], "action_too_long")

    async def test_one_hundred_users_and_capacity(self) -> None:
        sessions = Sessions(max_users=100)
        for index in range(1, 101):
            result = await sessions.join(user(index, f"user-{index}"), FakeConnection())
            self.assertIsNotNone(result)
        self.assertIsNone(await sessions.join(user(101, "overflow"), FakeConnection()))

        # The same account reconnecting reclaims its slot instead of adding one.
        reclaim = await sessions.join(user(1, "user-1"), FakeConnection())
        self.assertIsNotNone(reclaim)
        self.assertFalse(reclaim.created)
        self.assertEqual(len(sessions.users), 100)

    async def test_reassign_requires_pause_and_refreshes_only_changed_users(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            server = GameServer(
                database, None, None,
                character_names={"P1": "林岚", "P2": "周砚"},
                owner_user_id=1,
            )
            alice, bob, tom = (FakeConnection() for _ in range(3))
            await server.sessions.join(ALICE, alice)
            await server.sessions.join(BOB, bob)
            await server.sessions.join(TOM, tom)
            # The owner assigns roles through the normal public command path.
            await server._handle_command(1, alice, json.dumps({
                "type": "assign_roles",
                "assignments": {"P1": 1, "P2": 2},
            }))
            server.rounds.set_action("P1", "private draft")

            await server._handle_command(1, alice, json.dumps({
                "type": "assign_roles",
                "assignments": {"P1": 3, "P2": 2},
            }))
            self.assertEqual(await server.sessions.role_for(1), "P1")
            self.assertEqual(alice.messages[-1]["type"], "error")

            server.rounds.pause("P1")
            server.rounds.pause("P2")
            for connection in (alice, bob, tom):
                connection.messages.clear()
            await server._handle_command(1, alice, json.dumps({
                "type": "assign_roles",
                "assignments": {"P1": 3, "P2": 2},
            }))

            self.assertIsNone(await server.sessions.role_for(1))
            self.assertEqual(await server.sessions.role_for(3), "P1")
            self.assertEqual(await server.sessions.role_for(2), "P2")
            self.assertEqual(server.rounds.players["P1"].status, PlayerStatus.EDITING)
            self.assertEqual(server.rounds.players["P1"].action, "")
            self.assertEqual(server.rounds.players["P2"].status, PlayerStatus.PAUSED)
            self.assertTrue(any(m["type"] == "identity_changed" for m in alice.messages))
            self.assertTrue(any(m["type"] == "role_view" for m in alice.messages))
            alice_view = next(m for m in alice.messages if m["type"] == "role_view")
            self.assertNotIn("draft", alice_view)
            tom_view = next(m for m in tom.messages if m["type"] == "role_view")
            self.assertEqual(tom_view["draft"], "")
            self.assertFalse(any(
                m["type"] in ("identity_changed", "role_view") for m in bob.messages
            ))
            presence = next(m for m in reversed(alice.messages) if m["type"] == "presence")
            roles = {item["name"]: item["role"] for item in presence["users"]}
            self.assertEqual(roles, {"Alice": None, "Bob": "P2", "Tom": "P1"})

    async def test_room_chat_is_structured_and_never_enters_ai_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            server = GameServer(
                database, None, None,
                character_names={"P1": "林岚", "P2": "周砚"},
                owner_user_id=1,
            )
            alice, bob, tom = (FakeConnection() for _ in range(3))
            await server.sessions.join(ALICE, alice)
            await server.sessions.join(BOB, bob)
            await server.sessions.join(TOM, tom)
            await server.sessions.assign_roles({"P1": 1, "P2": 2})
            server.rounds.set_action("P1", "unsubmitted secret")

            await server._handle_command(
                3, tom, json.dumps({"type": "view", "role": "P1"})
            )
            spectator_view = tom.messages[-1]
            self.assertEqual(spectator_view["type"], "role_view")
            self.assertNotIn("draft", spectator_view)
            self.assertNotIn("unsubmitted secret", str(spectator_view))

            await server._handle_command(
                1, alice, json.dumps({"type": "room_chat", "text": "先别开门"})
            )
            await server._handle_command(
                3, tom, json.dumps({"type": "room_chat", "text": "我同意"})
            )

            room_messages = [m for m in alice.messages if m["type"] == "room_message"]
            player_message, spectator_message = room_messages[-2:]
            self.assertEqual(
                (player_message["kind"], player_message["sender"],
                 player_message["role"], player_message["character_name"]),
                ("player", "Alice", "P1", "林岚"),
            )
            self.assertEqual(spectator_message["kind"], "spectator")
            self.assertEqual(await database.get_narrator_history("P1", 20), [])
            self.assertEqual(server.rounds.players["P1"].status, PlayerStatus.EDITING)

            await server._handle_command(3, tom, json.dumps({"type": "submit"}))
            await server._handle_command(2, bob, json.dumps({"type": "retry"}))
            self.assertIn("观众只能使用房间聊天", tom.messages[-1]["detail"])
            self.assertIn("forbidden", bob.messages[-1]["detail"])


if __name__ == "__main__":
    unittest.main()
