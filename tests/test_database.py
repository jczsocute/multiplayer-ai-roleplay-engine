import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.models import CompletedRound


class DatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_initialize_and_finish_round(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "game.db"
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "CREATE TABLE participants (id INTEGER PRIMARY KEY, name TEXT)"
                )
                connection.execute("INSERT INTO participants (name) VALUES ('legacy')")
            database = Database(str(path))
            await database.initialize()
            completed = CompletedRound(1, {"P1": "left", "P2": "right"})
            await database.save_world_update(
                completed,
                {
                    "world_state": {"gate": "open"},
                    "character_views": {"P1": {"gate": "visible"}, "P2": {"fog": True}},
                    "character_status": {"P1": {"hp": 100}, "P2": {"hp": 90}},
                },
            )
            narrations = {
                    "P1": {"text": "A narration", "status": {}},
                    "P2": {"text": "B narration", "status": {}},
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
            self.assertEqual(latest["result"]["character_status"]["P2"], {"hp": 90})
            self.assertEqual(
                [item["kind"] for item in await database.get_role_history("P1")],
                ["action", "narration", "character_status"],
            )

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
                        "character_views",
                        "character_statuses",
                    }
                    <= tables
                )
                self.assertNotIn("participants", tables)
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
                    connection.execute("SELECT COUNT(*) FROM character_views").fetchone()[0], 2
                )
                self.assertNotIn("public_world_info", tables)

    async def test_narrator_history_uses_whole_recent_rounds(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            result = {
                "world_state": {"tick": 0},
                "character_views": {"P1": {}, "P2": {}},
                "character_status": {"P1": {}, "P2": {}},
            }
            for round_id in (1, 2, 3):
                completed = CompletedRound(
                    round_id, {"P1": f"a{round_id}", "P2": f"b{round_id}"}
                )
                await database.save_world_update(completed, result)
                await database.save_narrations(
                    round_id,
                    {
                        "P1": {"text": f"n{round_id}", "status": {}},
                        "P2": {"text": f"m{round_id}", "status": {}},
                    },
                )
                await database.finish_round(completed)
                if round_id < 3:
                    await database.create_round(round_id + 1)

            window = await database.get_narrator_history("P1", 2)
            self.assertEqual(
                [(item["role"], item["content"]) for item in window],
                [
                    ("player", "a2"),
                    ("narrator", "n2"),
                    ("player", "a3"),
                    ("narrator", "n3"),
                ],
            )
            single = await database.get_narrator_history("P1", 1)
            self.assertEqual(
                [(item["role"], item["content"]) for item in single],
                [("player", "a3"), ("narrator", "n3")],
            )
            self.assertEqual(len(await database.get_narrator_history("P1", 20)), 6)


if __name__ == "__main__":
    unittest.main()
