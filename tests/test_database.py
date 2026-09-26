import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.database import Database
from server.models import CompletedRound


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_and_finish_round(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "game.db"
            database = Database(str(path))
            await database.initialize()
            completed = CompletedRound(1, {"A": "left", "B": "right"})
            await database.save_world_update(
                completed,
                {
                    "world_state": {"gate": "open"},
                    "public_information": {"time": "morning"},
                    "player_views": {"A": {"gate": "visible"}, "B": {"fog": True}},
                    "player_statusbar": {"A": {"hp": 100}, "B": {"hp": 90}},
                },
            )
            narrations = {
                    "A": {"text": "A narration", "status": {}},
                    "B": {"text": "B narration", "status": {}},
                }
            await database.save_narrations(1, narrations)
            await database.finish_round(completed)
            await database.create_round(2)

            self.assertEqual(await database.current_round(), 2)
            self.assertEqual(
                json.loads(await database.get_world_state()), {"gate": "open"}
            )
            latest = await database.get_latest_world_update()
            self.assertEqual(latest["round"], 1)
            self.assertEqual(latest["result"]["world_state"], {"gate": "open"})
            self.assertEqual(latest["result"]["player_statusbar"]["B"], {"hp": 90})

            with sqlite3.connect(path) as connection:
                tables = {
                    row[0]
                    for row in connection.execute(
                        "SELECT name FROM sqlite_master WHERE type = 'table'"
                    )
                }
                self.assertTrue(
                    {
                        "players",
                        "rounds",
                        "chat_messages",
                        "world_state",
                        "player_views",
                        "public_world_info",
                    }
                    <= tables
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT status FROM rounds WHERE round_number = 1"
                    ).fetchone()[0],
                    "COMPLETED",
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0], 4
                )
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM player_views").fetchone()[0], 2
                )
                self.assertEqual(
                    connection.execute("SELECT content FROM public_world_info").fetchone()[0],
                    '{"time": "morning"}',
                )


if __name__ == "__main__":
    unittest.main()
