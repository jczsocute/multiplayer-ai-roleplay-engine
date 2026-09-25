import json
import sqlite3
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
        self.messages.append(json.loads(message))


class ReplayTests(unittest.IsolatedAsyncioTestCase):
    async def test_reconnect_replays_until_ack_then_stops(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            completed = CompletedRound(1, {"A": "open", "B": "watch"})
            await database.save_narrations(
                1,
                {
                    "A": {"text": "Narration A", "status": {}},
                    "B": {"text": "Narration B", "status": {}},
                },
            )
            await database.finish_round(completed)
            server = GameServer(database, None, None, None)
            first_connection = FakeWebSocket()

            await server._replay_narrations("A", first_connection)

            self.assertEqual(
                first_connection.messages,
                [
                    {
                        "type": "narration",
                        "round": 1,
                        "text": "Narration A",
                        "status": {},
                        "last_scene": True,
                        "statusbar": {},
                        "public_information": {},
                    }
                ],
            )

            await server.sessions.add("A", first_connection)
            await server._handle_command(
                "A", first_connection, json.dumps({"type": "ack", "round_id": 1})
            )
            with sqlite3.connect(database.path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT last_ack_round FROM players WHERE player_id = 'A'"
                    ).fetchone()[0],
                    1,
                )

            second_connection = FakeWebSocket()
            await server._replay_narrations("A", second_connection)
            self.assertEqual(second_connection.messages, [])


if __name__ == "__main__":
    unittest.main()
