import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.llm.narrator import Narrator
from server.gameserver.llm.player_view import PlayerViewGenerator
from server.gameserver.llm.world_update import WorldUpdater
from server.gameserver.game_server import GameServer
from server.gameserver.models import PlayerStatus
from tests.support import user


class MockWorldUpdater:
    def __init__(self) -> None:
        self.calls = []

    async def update(self, current_world_state: str, actions: dict[str, str]) -> dict:
        self.calls.append((current_world_state, actions))
        return {
            "world_state": {"gate": "open"},
            "public_information": {"time": "noon"},
            "player_views": {"P1": {"gate": "visible"}, "P2": {"road": "visible"}},
            "player_statusbar": {"P1": {"hp": 100}, "P2": {"hp": 100}},
        }


class MockNarrator:
    def __init__(self) -> None:
        self.calls = []

    async def narrate(
        self,
        player_id: str,
        public_world_info: str,
        player_view: str,
        player_statusbar: dict,
        chat_history: list,
    ) -> dict:
        self.calls.append(
            (player_id, public_world_info, player_view, player_statusbar, chat_history)
        )
        return {"text": f"Narration for {player_id}.", "status": {}}


class MockPlayerViews:
    def __init__(self) -> None:
        self.calls = []

    async def generate(self, player_id: str, world_state: str) -> str:
        self.calls.append((player_id, world_state))
        return f"Recovered view for {player_id}."


class FlakyNarrator(MockNarrator):
    def __init__(self) -> None:
        super().__init__()
        self.failed_once = False

    async def narrate(
        self,
        player_id: str,
        public_world_info: str,
        player_view: str,
        player_statusbar: dict,
        chat_history: list,
    ) -> dict:
        self.calls.append(
            (player_id, public_world_info, player_view, player_statusbar, chat_history)
        )
        if player_id == "P2" and not self.failed_once:
            self.failed_once = True
            raise RuntimeError("temporary narration failure")
        return {"text": f"Narration for {player_id}.", "status": {}}


class MockLLM:
    def __init__(self, response: str = "# Updated World") -> None:
        self.system_prompt = ""
        self.user_prompt = ""
        self.response = response
        self.json_mode = False
        self.max_tokens = None

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.json_mode = json_mode
        self.max_tokens = max_tokens
        return self.response


class JsonLLM(MockLLM):
    def __init__(self, response: dict) -> None:
        super().__init__()
        self.response = response

    async def generate(
        self,
        system_prompt: str,
        user_prompt: str,
        json_mode: bool = False,
        max_tokens: int | None = None,
    ) -> str:
        self.system_prompt = system_prompt
        self.user_prompt = user_prompt
        self.json_mode = json_mode
        self.max_tokens = max_tokens
        return json.dumps(self.response)


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, message: str) -> None:
        self.messages.append(json.loads(message))


class WorldUpdateFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_narrator_uses_visible_inputs_and_returns_structure(self) -> None:
        llm = MockLLM("You see the open gate.")
        narrator = Narrator(llm)

        result = await narrator.narrate(
            "P1",
            "It is noon.",
            "The gate is visible.",
            {"左腿": "严重受伤"},
            [{"role": "player", "content": "look"}],
        )

        self.assertEqual(result["text"], "You see the open gate.")
        self.assertEqual(result["status"], {})
        self.assertIn("It is noon.", llm.user_prompt)
        self.assertIn("The gate is visible.", llm.user_prompt)
        self.assertIn("严重受伤", llm.user_prompt)
        self.assertNotIn("world_state", llm.user_prompt)

    async def test_player_view_generator_builds_player_specific_prompt(self) -> None:
        llm = MockLLM()
        generator = PlayerViewGenerator(llm)

        result = await generator.generate("P1", "objective state")

        self.assertEqual(result, "# Updated World")
        self.assertIn("感知信息管理者", llm.system_prompt)
        for expected in ("世界设定", "P1（林岚）", "objective state"):
            self.assertIn(expected, llm.user_prompt)

    async def test_world_updater_builds_prompt_without_real_api(self) -> None:
        expected = {
            "world_state": {"phase": "updated"},
            "public_information": {"time": "noon"},
            "player_views": {"P1": {"seen": "gate"}, "P2": {"seen": "road"}},
            "player_statusbar": {"P1": {"hp": 100}, "P2": {"hp": 90}},
        }
        llm = JsonLLM(expected)
        updater = WorldUpdater(llm)

        result = await updater.update(
            "old state", {"P1": "action P1", "P2": "action P2"}
        )

        self.assertEqual(result, expected)
        self.assertTrue(llm.json_mode)
        self.assertEqual(llm.max_tokens, 8192)
        self.assertIn("严格输出合法 JSON", llm.system_prompt)
        self.assertIn('"player_statusbar"', llm.user_prompt)
        self.assertIn("world_updater_output.json", llm.system_prompt)
        for expected in ("世界设定", "P1", "P2", "old state", "action P1", "action P2"):
            self.assertIn(expected, llm.user_prompt)

    async def test_world_update_is_called_and_round_advances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "game.db"
            database = Database(str(path))
            await database.initialize()
            updater = MockWorldUpdater()
            player_views = MockPlayerViews()
            narrator = MockNarrator()
            server = GameServer(database, updater, player_views, narrator, owner_user_id=1)
            websocket = FakeWebSocket()
            player_p1_socket = FakeWebSocket()
            player_p2_socket = FakeWebSocket()
            await server.sessions.join(user(1, "P1"), player_p1_socket)
            await server.sessions.join(user(2, "P2"), player_p2_socket)
            await server.sessions.assign_roles({"P1": 1, "P2": 2})

            await server._handle_command(
                1, websocket, json.dumps({"type": "action", "text": "open the gate"})
            )
            await server._handle_command(
                2, websocket, json.dumps({"type": "action", "text": "stand guard"})
            )
            await server._handle_command(1, websocket, json.dumps({"type": "submit"}))
            await server._handle_command(2, websocket, json.dumps({"type": "submit"}))

            self.assertEqual(len(updater.calls), 1)
            self.assertEqual(
                updater.calls[0][1],
                {"P1": "open the gate", "P2": "stand guard"},
            )
            self.assertEqual({call[0] for call in narrator.calls}, {"P1", "P2"})
            self.assertTrue(
                all(
                    json.loads(call[1]) == {"time": "noon"}
                    for call in narrator.calls
                )
            )
            self.assertEqual(
                {call[0]: call[3] for call in narrator.calls},
                {"P1": {"hp": 100}, "P2": {"hp": 100}},
            )
            self.assertEqual(
                [
                    entry["content"]
                    for message in player_p1_socket.messages
                    if message["type"] == "role_round"
                    for entry in message["entries"]
                    if entry["kind"] == "narration"
                ],
                ["Narration for P1."],
            )
            self.assertEqual(
                [
                    entry["content"]
                    for message in player_p2_socket.messages
                    if message["type"] == "role_round"
                    for entry in message["entries"]
                    if entry["kind"] == "narration"
                ],
                ["Narration for P2."],
            )
            self.assertFalse(any(
                message["type"] == "world_update"
                for message in player_p1_socket.messages + player_p2_socket.messages
            ))
            player_stages = [
                message["stage"] for message in player_p1_socket.messages
                if message["type"] == "processing_stage"
            ]
            for stage in ("WORLD_UPDATING", "NARRATION_GENERATING"):
                self.assertIn(stage, player_stages)
            self.assertEqual(
                await database.get_world_state(),
                '{"gate": "open"}',
            )
            self.assertEqual(server.rounds.round_number, 2)
            self.assertTrue(
                all(
                    player.status == PlayerStatus.EDITING
                    for player in server.rounds.players.values()
                )
            )

            with sqlite3.connect(path) as connection:
                result = connection.execute(
                    "SELECT status, result_world_state FROM rounds WHERE round_number = 1"
                ).fetchone()
                self.assertEqual(result, ("COMPLETED", '{"gate": "open"}'))
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM chat_messages").fetchone()[0], 4
                )
                self.assertEqual(
                    connection.execute("SELECT content FROM public_world_info").fetchone()[0],
                    '{"time": "noon"}',
                )
                views = dict(
                    connection.execute(
                        "SELECT player_id, view_content FROM player_views WHERE round_id = 1"
                    ).fetchall()
                )
                self.assertEqual(
                    views,
                    {
                        "P1": '{"gate": "visible"}',
                        "P2": '{"road": "visible"}',
                    },
                )

    async def test_failed_narration_retry_reruns_world_and_all_narrations(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize("initial world")
            updater = MockWorldUpdater()
            player_views = MockPlayerViews()
            narrator = FlakyNarrator()
            server = GameServer(database, updater, player_views, narrator, owner_user_id=1)
            websocket = FakeWebSocket()
            await server.sessions.join(user(1, "P1"), websocket)
            await server.sessions.join(user(2, "P2"), FakeWebSocket())
            await server.sessions.assign_roles({"P1": 1, "P2": 2})

            await server._handle_command(
                1, websocket, json.dumps({"type": "action", "text": "open the gate"})
            )
            await server._handle_command(
                2, websocket, json.dumps({"type": "action", "text": "stand guard"})
            )
            await server._handle_command(1, websocket, json.dumps({"type": "submit"}))
            with self.assertLogs("server.gameserver.game_server", level="ERROR"):
                await server._handle_command(2, websocket, json.dumps({"type": "submit"}))

            # The pipeline stopped mid-way: round 1 is unfinished, so its output is
            # not partial-retried; the whole round is re-run instead.
            self.assertEqual(server.rounds.round_number, 1)
            self.assertEqual(await database.get_world_state(), '{"gate": "open"}')
            self.assertEqual([call[0] for call in narrator.calls], ["P1", "P2"])

            # The game owner retries through the normal public command path.
            await server._handle_command(1, websocket, json.dumps({"type": "retry"}))

            # WorldUpdater runs again from the base world; every Narrator runs again,
            # including the P1 narration that succeeded the first time.
            self.assertEqual(len(updater.calls), 2)
            self.assertEqual(updater.calls[1][0], "initial world")
            self.assertEqual(
                updater.calls[1][1], {"P1": "open the gate", "P2": "stand guard"}
            )
            self.assertEqual([call[0] for call in narrator.calls], ["P1", "P2", "P1", "P2"])
            self.assertEqual(server.rounds.round_number, 2)
            self.assertTrue(
                all(
                    player.status == PlayerStatus.EDITING
                    for player in server.rounds.players.values()
                )
            )
            with sqlite3.connect(database.path) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM chat_messages WHERE round_number = 1"
                    ).fetchone()[0],
                    4,
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT COUNT(*) FROM player_views WHERE round_id = 1"
                    ).fetchone()[0],
                    2,
                )


if __name__ == "__main__":
    unittest.main()
