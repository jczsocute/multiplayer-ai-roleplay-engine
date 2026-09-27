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
            completed = CompletedRound(1, {"A": "open", "B": "watch"})
            result = {
                "world_state": {"gate": "open"},
                "public_information": {"time": "noon"},
                "player_views": {"A": {"gate": "visible"}, "B": {"road": "visible"}},
                "player_statusbar": {"A": {"hp": 90}, "B": {"hp": 80}},
            }
            await database.save_world_update(completed, result)
            await database.save_narrations(1, {
                "A": {"text": "Narration A", "status": {}},
                "B": {"text": "Narration B", "status": {}},
            })
            await database.finish_round(completed)
            server = GameServer(database, None, None, None)
            server.rounds.players["A"].action = "private current draft"

            spectator = FakeWebSocket()
            await server._send_role_view(spectator, "A")
            player = FakeWebSocket()
            await server._send_role_view(
                player, "A", include_draft=True, include_current_view=True
            )

            self.assertEqual(
                [item["kind"] for item in spectator.messages[0]["history"]],
                ["action", "narration", "statusbar"],
            )
            self.assertNotIn("draft", spectator.messages[0])
            self.assertNotIn("private current draft", str(spectator.messages[0]))
            self.assertEqual(player.messages[0]["draft"], "private current draft")
            self.assertEqual(player.messages[0]["current_view"], {"gate": "visible"})


if __name__ == "__main__":
    unittest.main()
