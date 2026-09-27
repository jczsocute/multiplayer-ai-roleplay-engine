import json
import tempfile
import unittest
from pathlib import Path

from server.database import Database
from server.main import GameServer
from server.models import PlayerStatus
from server.session import Sessions


class FakeConnection:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


class RoomProtocolTests(unittest.IsolatedAsyncioTestCase):
    async def test_one_hundred_users_duplicate_and_nickname_release(self) -> None:
        sessions = Sessions(max_users=100)
        self.assertTrue(await sessions.join_host(FakeConnection()))
        connections = []
        for index in range(100):
            connection = FakeConnection()
            connections.append(connection)
            self.assertIsNotNone(await sessions.join(f"user-{index}", connection))
        self.assertIsNone(await sessions.join("overflow", FakeConnection()))
        with self.assertRaisesRegex(ValueError, "already connected"):
            await sessions.join("user-0", FakeConnection())

        await sessions.remove("user-0", connections[0])
        replacement = await sessions.join("user-0", FakeConnection())
        self.assertIsNotNone(replacement)
        self.assertIsNone(replacement.role)

    async def test_reassign_requires_pause_and_refreshes_only_changed_users(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            server = GameServer(
                database, None, None, None,
                character_names={"A": "林岚", "B": "周砚"},
            )
            alice, bob, tom, host = (FakeConnection() for _ in range(4))
            await server.sessions.join("Alice", alice)
            await server.sessions.join("Bob", bob)
            await server.sessions.join("Tom", tom)
            await server.sessions.join_host(host)
            await server._handle_host_command(host, json.dumps({
                "type": "assign_roles", "player_a": "Alice", "player_b": "Bob"
            }))
            server.rounds.set_action("A", "private draft")

            await server._handle_host_command(host, json.dumps({
                "type": "assign_roles", "player_a": "Tom", "player_b": "Bob"
            }))
            self.assertEqual(await server.sessions.role_for("Alice"), "A")
            self.assertEqual(host.messages[-1]["type"], "error")

            server.rounds.pause("A")
            server.rounds.pause("B")
            for connection in (alice, bob, tom, host):
                connection.messages.clear()
            await server._handle_host_command(host, json.dumps({
                "type": "assign_roles", "player_a": "Tom", "player_b": "Bob"
            }))

            self.assertIsNone(await server.sessions.role_for("Alice"))
            self.assertEqual(await server.sessions.role_for("Tom"), "A")
            self.assertEqual(await server.sessions.role_for("Bob"), "B")
            self.assertEqual(server.rounds.players["A"].status, PlayerStatus.EDITING)
            self.assertEqual(server.rounds.players["A"].action, "")
            self.assertEqual(server.rounds.players["B"].status, PlayerStatus.PAUSED)
            self.assertTrue(any(m["type"] == "identity_changed" for m in alice.messages))
            self.assertTrue(any(m["type"] == "role_view" for m in alice.messages))
            alice_view = next(m for m in alice.messages if m["type"] == "role_view")
            self.assertNotIn("draft", alice_view)
            tom_view = next(m for m in tom.messages if m["type"] == "role_view")
            self.assertEqual(tom_view["draft"], "")
            self.assertFalse(any(
                m["type"] in ("identity_changed", "role_view") for m in bob.messages
            ))
            presence = next(m for m in reversed(host.messages) if m["type"] == "presence")
            roles = {item["name"]: item["role"] for item in presence["users"]}
            self.assertEqual(roles, {"Alice": None, "Bob": "B", "Tom": "A"})

    async def test_room_chat_is_structured_and_never_enters_ai_history(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            server = GameServer(
                database, None, None, None,
                character_names={"A": "林岚", "B": "周砚"},
            )
            alice, bob, tom, host = (FakeConnection() for _ in range(4))
            await server.sessions.join("Alice", alice)
            await server.sessions.join("Bob", bob)
            await server.sessions.join("Tom", tom)
            await server.sessions.join_host(host)
            await server.sessions.assign_roles("Alice", "Bob")
            server.rounds.set_action("A", "unsubmitted secret")

            await server._handle_command(
                "Tom", tom, json.dumps({"type": "view", "role": "A"})
            )
            spectator_view = tom.messages[-1]
            self.assertEqual(spectator_view["type"], "role_view")
            self.assertNotIn("draft", spectator_view)
            self.assertNotIn("unsubmitted secret", str(spectator_view))

            await server._handle_command(
                "Alice", alice, json.dumps({"type": "room_chat", "text": "先别开门"})
            )
            await server._handle_command(
                "Tom", tom, json.dumps({"type": "room_chat", "text": "我同意"})
            )
            await server._handle_host_command(
                host, json.dumps({"type": "room_chat", "text": "稍等"})
            )

            room_messages = [m for m in alice.messages if m["type"] == "room_message"]
            player_message, spectator_message, host_message = room_messages[-3:]
            self.assertEqual(
                (player_message["kind"], player_message["sender"],
                 player_message["role"], player_message["character_name"]),
                ("player", "Alice", "A", "林岚"),
            )
            self.assertEqual(spectator_message["kind"], "spectator")
            self.assertEqual(host_message["kind"], "host")
            self.assertEqual(await database.get_chat_history("A"), [])
            self.assertEqual(server.rounds.players["A"].status, PlayerStatus.EDITING)

            await server._handle_command("Tom", tom, json.dumps({"type": "submit"}))
            await server._handle_command(
                "Alice", alice, json.dumps({"type": "retry_narration"})
            )
            self.assertIn("spectators may only", tom.messages[-1]["detail"])
            self.assertIn("only Host", alice.messages[-1]["detail"])


if __name__ == "__main__":
    unittest.main()
