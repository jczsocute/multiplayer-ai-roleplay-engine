import tempfile
import unittest
from pathlib import Path

from server.database import Database
from server.main import GameServer
from server.models import CompletedRound


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, message: str) -> None:
        import json
        self.messages.append(json.loads(message))


class RoleViewTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_history_order_and_draft_isolation(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            completed = CompletedRound(1, {"P1": "open", "P2": "watch"})
            result = {
                "world_state": {"gate": "open"},
                "public_information": {"time": "noon"},
                "player_views": {"P1": {"gate": "visible"}, "P2": {"road": "visible"}},
                "player_statusbar": {"P1": {"hp": 90}, "P2": {"hp": 80}},
            }
            await database.save_world_update(completed, result)
            await database.save_narrations(1, {
                "P1": {"text": "Narration A", "status": {}},
                "P2": {"text": "Narration B", "status": {}},
            })
            await database.finish_round(completed)
            server = GameServer(
                database, None, None, None,
                openings={"P1": "P1 opening", "P2": "P2 opening"},
            )
            server.rounds.players["P1"].action = "private current draft"

            spectator = FakeWebSocket()
            await server._send_role_view(spectator, "P1")
            player = FakeWebSocket()
            await server._send_role_view(
                player, "P1", include_draft=True, include_current_view=True
            )

            self.assertEqual(
                [item["kind"] for item in spectator.messages[0]["history"]],
                ["action", "narration", "statusbar"],
            )
            self.assertNotIn("draft", spectator.messages[0])
            self.assertEqual(spectator.messages[0]["opening"], "P1 opening")
            self.assertNotIn("private current draft", str(spectator.messages[0]))
            self.assertEqual(player.messages[0]["draft"], "private current draft")
            self.assertEqual(player.messages[0]["current_view"], {"gate": "visible"})

    async def test_role_view_contains_opening_when_history_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            server = GameServer(
                database, None, None, None,
                openings={"P1": "The opening", "P2": "Other opening"},
            )
            spectator = FakeWebSocket()
            await server._send_role_view(spectator, "P1")
            self.assertEqual(spectator.messages[0]["opening"], "The opening")
            self.assertEqual(spectator.messages[0]["history"], [])


if __name__ == "__main__":
    unittest.main()
