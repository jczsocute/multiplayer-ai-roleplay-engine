import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.database import Database
from server.main import GameServer
from server.models import CompletedRound, RoundStage


WORLD_RESULT = {
    "world_state": {"gate": "open"},
    "public_information": {"time": "noon"},
    "player_views": {"A": {"gate": "visible"}, "B": {"road": "visible"}},
    "player_statusbar": {"A": {"hp": 100}, "B": {"hp": 100}},
}


class MockWorldUpdater:
    def __init__(self) -> None:
        self.calls = []

    async def update(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return WORLD_RESULT


class MockPlayerViews:
    def __init__(self) -> None:
        self.calls = []

    async def generate(self, player_id: str, world_state: str) -> str:
        self.calls.append((player_id, world_state))
        return f"Recovered {player_id} view."


class MockNarrator:
    def __init__(self) -> None:
        self.calls = []

    async def narrate(
        self, player_id: str, public_world_info: str, player_view: str, chat_history: list
    ) -> dict:
        self.calls.append(player_id)
        return {"text": f"Narration {player_id}", "status": {}}


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def make_game(self):
        directory = tempfile.TemporaryDirectory()
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize()
        completed = CompletedRound(1, {"A": "open", "B": "watch"})
        await database.save_world_update(completed, WORLD_RESULT)
        updater = MockWorldUpdater()
        views = MockPlayerViews()
        narrator = MockNarrator()
        game = GameServer(database, updater, views, narrator, 1)
        return directory, database, completed, updater, views, narrator, game

    async def test_world_done_skips_world_update_and_completes_round(self) -> None:
        directory, database, _, updater, views, narrator, game = await self.make_game()
        self.addCleanup(directory.cleanup)

        await game.recover_round()

        self.assertEqual(updater.calls, [])
        self.assertEqual(views.calls, [])
        self.assertEqual(set(narrator.calls), {"A", "B"})
        self.assertEqual(game.rounds.round_number, 2)
        with sqlite3.connect(database.path) as connection:
            self.assertEqual(
                connection.execute("SELECT stage FROM rounds WHERE round_number = 1").fetchone()[0],
                RoundStage.FINISHED.value,
            )

    async def test_view_generating_only_fills_missing_b_view(self) -> None:
        directory, database, _, updater, views, _, game = await self.make_game()
        self.addCleanup(directory.cleanup)
        with sqlite3.connect(database.path) as connection:
            connection.execute("DELETE FROM player_views WHERE round_id = 1 AND player_id = 'B'")
        await database.set_round_stage(1, RoundStage.VIEW_GENERATING)

        await game.recover_round()

        self.assertEqual(updater.calls, [])
        self.assertEqual([call[0] for call in views.calls], ["B"])
        self.assertEqual(game.rounds.round_number, 2)

    async def test_narration_generating_only_fills_missing_b_narration(self) -> None:
        directory, database, completed, updater, views, narrator, game = await self.make_game()
        self.addCleanup(directory.cleanup)
        await database.save_narrations(
            completed.round_number,
            {"A": {"text": "Existing narration A", "status": {}}},
        )
        await database.set_round_stage(1, RoundStage.NARRATION_GENERATING)

        await game.recover_round()

        self.assertEqual(updater.calls, [])
        self.assertEqual(views.calls, [])
        self.assertEqual(narrator.calls, ["B"])
        self.assertEqual(game.rounds.round_number, 2)
        with sqlite3.connect(database.path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE role = 'narrator'"
                ).fetchone()[0],
                2,
            )


if __name__ == "__main__":
    unittest.main()
