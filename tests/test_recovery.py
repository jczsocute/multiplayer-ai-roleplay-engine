import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.gameserver.models import CompletedRound, PlayerStatus, RoundStage


INITIAL_WORLD = "# Initial world"
WORLD_RESULT = {
    "world_state": {"gate": "open"},
    "character_views": {"P1": {"gate": "visible"}, "P2": {"road": "visible"}},
    "character_status": {"P1": {"hp": 100}, "P2": {"hp": 100}},
}


class MockWorldUpdater:
    def __init__(self) -> None:
        self.calls = []

    async def update(self, **kwargs) -> dict:
        self.calls.append(kwargs)
        return WORLD_RESULT


class MockNarrator:
    def __init__(self) -> None:
        self.calls = []

    async def narrate(
        self,
        player_id: str,
        character_view: str,
        character_status: dict,
        chat_history: list,
    ) -> dict:
        self.calls.append(player_id)
        return {"text": f"Narration {player_id}", "status": {}}


class RecoveryTests(unittest.IsolatedAsyncioTestCase):
    async def test_world_update_failure_keeps_actions_locked_for_full_retry(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize(INITIAL_WORLD)
        completed = CompletedRound(1, {"P1": "open", "P2": "watch"})

        class FailOnce:
            def __init__(self):
                self.calls = 0

            async def update(self, **_kwargs):
                self.calls += 1
                if self.calls == 1:
                    raise RuntimeError("LLM unavailable")
                return WORLD_RESULT

        updater = FailOnce()
        game = GameServer(database, updater, MockNarrator(), 1)
        game.rounds.begin_reprocess(completed)
        await game._process_round(completed)
        failed = await database.get_recovery_data(1)
        self.assertEqual(failed["stage"], RoundStage.FAILED)
        self.assertEqual(failed["actions"], completed.actions)
        self.assertTrue(failed["locked"])
        self.assertTrue(game.rounds.is_processing())
        self.assertFalse(game.is_ai_active())

        with self.assertRaises(ValueError):
            await database.export_history()

        await game.retry_round()
        self.assertEqual(updater.calls, 2)
        self.assertEqual(game.rounds.round_number, 2)
        self.assertEqual((await database.get_recovery_data(1))["stage"], RoundStage.FINISHED)

    async def test_narrator_failure_sets_failed_stage(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize(INITIAL_WORLD)
        completed = CompletedRound(1, {"P1": "open", "P2": "watch"})

        class FailNarrator:
            async def narrate(self, *_args):
                raise RuntimeError("LLM unavailable")

        game = GameServer(database, MockWorldUpdater(), FailNarrator(), 1)
        game.rounds.begin_reprocess(completed)
        await game._process_round(completed)
        self.assertEqual((await database.get_recovery_data(1))["stage"], RoundStage.FAILED)
        self.assertEqual(game.rounds.stage, RoundStage.FAILED)
        self.assertTrue(game.rounds.is_processing())

    async def make_game(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize(INITIAL_WORLD)
        completed = CompletedRound(1, {"P1": "open", "P2": "watch"})
        await database.save_world_update(completed, WORLD_RESULT)
        updater = MockWorldUpdater()
        narrator = MockNarrator()
        game = GameServer(database, updater, narrator, 1)
        return directory, database, completed, updater, narrator, game

    async def test_save_world_update_commits_result_and_world_done_together(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize(INITIAL_WORLD)
        completed = CompletedRound(1, {"P1": "open", "P2": "watch"})

        await database.save_world_update(completed, WORLD_RESULT)

        # The result and the WORLD_DONE stage must be visible after the same commit,
        # so a crash cannot leave a persisted result behind a WORLD_UPDATING stage.
        with sqlite3.connect(database.path) as connection:
            stage, result = connection.execute(
                "SELECT stage, result_world_state FROM rounds WHERE round_number = 1"
            ).fetchone()
        self.assertEqual(stage, RoundStage.WORLD_DONE.value)
        self.assertEqual(json.loads(result), WORLD_RESULT["world_state"])

    async def test_world_done_reruns_the_whole_round(self) -> None:
        directory, database, _, updater, narrator, game = await self.make_game()
        self.addCleanup(directory.cleanup)
        await database.set_round_stage(1, RoundStage.WORLD_DONE)

        await game.recover_round()

        # No partial continuation: WorldUpdater and every Narrator run again from the
        # previous world state, even though a result was already committed.
        self.assertEqual(
            [call["current_world_state"] for call in updater.calls], [INITIAL_WORLD]
        )
        self.assertEqual(narrator.calls, ["P1", "P2"])
        self.assertEqual(game.rounds.round_number, 2)
        with sqlite3.connect(database.path) as connection:
            self.assertEqual(
                connection.execute("SELECT stage FROM rounds WHERE round_number = 1").fetchone()[0],
                RoundStage.FINISHED.value,
            )

    async def test_world_updating_reruns_world_updater(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize(INITIAL_WORLD)
        await database.save_player("P1", PlayerStatus.EDITING, "open")
        await database.save_player("P2", PlayerStatus.EDITING, "watch")
        await database.set_round_stage(1, RoundStage.WORLD_UPDATING)
        updater = MockWorldUpdater()
        game = GameServer(database, updater, MockNarrator(), 1)

        await game.recover_round()

        self.assertEqual(
            [call["current_world_state"] for call in updater.calls], [INITIAL_WORLD]
        )
        self.assertEqual(game.rounds.round_number, 2)

    async def test_view_generating_no_longer_only_fills_gaps(self) -> None:
        directory, database, _, updater, narrator, game = await self.make_game()
        self.addCleanup(directory.cleanup)
        with sqlite3.connect(database.path) as connection:
            connection.execute("DELETE FROM character_views WHERE round_id = 1 AND player_id = 'P2'")
        await database.set_round_stage(1, RoundStage.VIEW_GENERATING)

        await game.recover_round()

        # Legacy view-generation stages recover by rerunning the whole round.
        self.assertEqual(len(updater.calls), 1)
        self.assertEqual(narrator.calls, ["P1", "P2"])
        self.assertEqual(game.rounds.round_number, 2)
        with sqlite3.connect(database.path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM character_views WHERE round_id = 1"
                ).fetchone()[0],
                2,
            )

    async def test_narration_generating_regenerates_all_narrations(self) -> None:
        directory, database, completed, updater, narrator, game = await self.make_game()
        self.addCleanup(directory.cleanup)
        await database.save_narrations(
            completed.round_number,
            {"P1": {"text": "Stale narration A", "status": {}}},
        )
        await database.set_round_stage(1, RoundStage.NARRATION_GENERATING)

        await game.recover_round()

        # The successful P1 narration is not preserved: everything is regenerated.
        self.assertEqual(len(updater.calls), 1)
        self.assertEqual(narrator.calls, ["P1", "P2"])
        self.assertEqual(game.rounds.round_number, 2)
        with sqlite3.connect(database.path) as connection:
            rows = connection.execute(
                "SELECT content FROM chat_messages WHERE role = 'narrator' ORDER BY player_id"
            ).fetchall()
        self.assertEqual(len(rows), 2)
        self.assertNotIn("Stale narration A", [row[0] for row in rows])

    async def test_waiting_input_round_is_left_for_the_players(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize(INITIAL_WORLD)
        await database.save_player("P1", PlayerStatus.READY, "open")
        updater = MockWorldUpdater()
        narrator = MockNarrator()
        game = GameServer(database, updater, narrator, 1)

        await game.recover_round()

        self.assertEqual(updater.calls, [])
        self.assertEqual(narrator.calls, [])
        self.assertEqual(game.rounds.round_number, 1)
        self.assertEqual(game.rounds.players["P1"].status, PlayerStatus.READY)

    async def test_locked_but_unprocessed_round_is_rerun(self) -> None:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        database = Database(str(Path(directory.name) / "game.db"))
        await database.initialize(INITIAL_WORLD)
        # Crash window: every player already saved as PROCESSING but the round never
        # entered WORLD_UPDATING.
        await database.save_player("P1", PlayerStatus.PROCESSING, "open")
        await database.save_player("P2", PlayerStatus.PROCESSING, "watch")
        updater = MockWorldUpdater()
        narrator = MockNarrator()
        game = GameServer(database, updater, narrator, 1)

        await game.recover_round()

        self.assertEqual(len(updater.calls), 1)
        self.assertEqual(narrator.calls, ["P1", "P2"])
        self.assertEqual(game.rounds.round_number, 2)


if __name__ == "__main__":
    unittest.main()
