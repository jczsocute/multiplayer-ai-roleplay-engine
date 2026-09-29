import json
import copy
import sqlite3
import shutil
import tempfile
import unittest
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.llm.narrator import Narrator
from server.gameserver.llm.world_update import WorldUpdater
from server.gameserver.llm.prompt_loader import PromptLoader
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
            "character_views": {"P1": {"gate": "visible"}, "P2": {"road": "visible"}},
            "character_status": {"P1": {"hp": 100}, "P2": {"hp": 100}},
        }


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
        self.calls.append(
            (player_id, character_view, character_status, chat_history)
        )
        return {"text": f"Narration for {player_id}.", "status": {}}


class FlakyNarrator(MockNarrator):
    def __init__(self) -> None:
        super().__init__()
        self.failed_once = False

    async def narrate(
        self,
        player_id: str,
        character_view: str,
        character_status: dict,
        chat_history: list,
    ) -> dict:
        self.calls.append(
            (player_id, character_view, character_status, chat_history)
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
    async def test_optional_character_status_skips_disabled_role(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "story"
            shutil.copytree("templates/default", root)
            (root / "characters/2/character_status_schema.json").unlink()
            (root / "characters/2/character_status_initial.json").unlink()
            loader = PromptLoader(str(root))
            p1_status = {key: "updated" for key in loader.character_status_schema("P1")}
            output = {
                "world_state": {"world_information": "updated"},
                "character_views": {
                    "P1": {"world_information": "first"},
                    "P2": {"world_information": "second"},
                },
                "character_status": {"P1": p1_status},
            }
            updater = WorldUpdater(JsonLLM(output), loader)
            self.assertEqual(await updater.update("{}", {"P1": "a", "P2": "b"}), output)

            database = Database(str(Path(directory) / "game.db"), loader.role_ids, ("P1",))
            await database.initialize(loader.json("world/world_state_initial.json"),
                                      {"P1": loader.character_status_initial("P1")})
            narrator = MockNarrator()
            server = GameServer(database, updater, narrator, owner_user_id=1)
            first, second = FakeWebSocket(), FakeWebSocket()
            await server.sessions.join(user(1, "Alice"), first)
            await server.sessions.join(user(2, "Bob"), second)
            await server.sessions.assign_roles({"P1": 1, "P2": 2})
            from server.gameserver.models import CompletedRound
            completed = CompletedRound(1, {"P1": "a", "P2": "b"})
            server.rounds.begin_reprocess(completed)
            await server._process_round(completed)
            p2_round = next(message for message in second.messages if message["type"] == "role_round")
            self.assertIsNone(p2_round["character_status"])
            self.assertFalse(any(entry["kind"] == "character_status" for entry in p2_round["entries"]))
            self.assertNotIn("character_view", p2_round)
            self.assertNotIn("world_state", p2_round)
            self.assertIsNone((await database.get_player_display("P2"))["character_status"])

    async def test_game_sqlite_connections_use_busy_timeout_and_wal(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            with database._connect() as connection:
                self.assertEqual(connection.execute("PRAGMA busy_timeout").fetchone()[0], 5000)
                self.assertEqual(connection.execute("PRAGMA journal_mode").fetchone()[0], "wal")

    async def test_world_updater_rejects_incomplete_or_unknown_role_outputs(self) -> None:
        valid = {
            "world_state": {"world_information": "updated"},
            "character_views": {"P1": {"world_information": "gate"},
                                "P2": {"world_information": "road"}},
            "character_status": {
                role: {key: "updated" for key in WorldUpdater(JsonLLM({})).loader.character_status_schema(role)}
                for role in ("P1", "P2")
            },
        }
        for field in ("character_views", "character_status"):
            for problem in ("missing", "extra", "invalid"):
                with self.subTest(field=field, problem=problem):
                    output = copy.deepcopy(valid)
                    if problem == "missing":
                        del output[field]["P2"]
                    elif problem == "extra":
                        output[field]["P3"] = {}
                    else:
                        output[field]["P1"] = []
                    with self.assertRaises(ValueError):
                        await WorldUpdater(JsonLLM(output)).update("{}", {"P1": "a", "P2": "b"})

        for field in ("world_state", "character_views", "character_status"):
            with self.subTest(field=field, problem="top-level type"):
                output = copy.deepcopy(valid)
                output[field] = []
                with self.assertRaises(ValueError):
                    await WorldUpdater(JsonLLM(output)).update("{}", {"P1": "a", "P2": "b"})

    async def test_narrator_uses_visible_inputs_and_returns_structure(self) -> None:
        llm = MockLLM("You see the open gate.")
        narrator = Narrator(llm)

        result = await narrator.narrate(
            "P1",
            "The gate is visible.",
            {"左腿": "严重受伤"},
            [{"role": "player", "content": "look"}],
        )

        self.assertEqual(result["text"], "You see the open gate.")
        self.assertEqual(result["status"], {})
        self.assertIn("The gate is visible.", llm.user_prompt)
        self.assertIn("严重受伤", llm.user_prompt)
        self.assertNotIn("world_state", llm.user_prompt)

        await narrator.narrate("P1", {"seen": "gate"}, None, [])
        self.assertNotIn("# 角色当前状态", llm.user_prompt)

    async def test_world_updater_builds_prompt_without_real_api(self) -> None:
        expected = {
            "world_state": {"world_information": "updated"},
            "character_views": {"P1": {"world_information": "gate"},
                                "P2": {"world_information": "road"}},
            "character_status": {
                role: {key: "updated" for key in WorldUpdater(JsonLLM({})).loader.character_status_schema(role)}
                for role in ("P1", "P2")
            },
        }
        llm = JsonLLM(expected)
        updater = WorldUpdater(llm)

        result = await updater.update(
            "old state", {"P1": "action P1", "P2": "action P2"}
        )

        self.assertEqual(result, expected)
        self.assertTrue(llm.json_mode)
        self.assertEqual(llm.max_tokens, 8192)
        self.assertIn("只输出合法 JSON", llm.system_prompt)
        self.assertIn('"character_status"', llm.user_prompt)
        self.assertIn('"world_information"', llm.user_prompt)
        for expected in ("世界设定", "P1", "P2", "old state", "action P1", "action P2"):
            self.assertIn(expected, llm.user_prompt)

    async def test_world_update_is_called_and_round_advances(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "game.db"
            database = Database(str(path))
            await database.initialize()
            updater = MockWorldUpdater()
            narrator = MockNarrator()
            server = GameServer(database, updater, narrator, owner_user_id=1)
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
                    call[1] == {"gate": "visible"} if call[0] == "P1"
                    else call[1] == {"road": "visible"}
                    for call in narrator.calls
                )
            )
            self.assertEqual(
                {call[0]: call[2] for call in narrator.calls},
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
            for socket in (player_p1_socket, player_p2_socket):
                live = next(message for message in socket.messages if message["type"] == "role_round")
                self.assertEqual(live["round"], 1)
                self.assertEqual({entry["round"] for entry in live["entries"]}, {1})
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
                self.assertIsNone(connection.execute(
                    "SELECT name FROM sqlite_master WHERE name = 'public_world_info'"
                ).fetchone())
                views = dict(
                    connection.execute(
                        "SELECT player_id, character_view_content FROM character_views WHERE round_id = 1"
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
            narrator = FlakyNarrator()
            server = GameServer(database, updater, narrator, owner_user_id=1)
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
                        "SELECT COUNT(*) FROM character_views WHERE round_id = 1"
                    ).fetchone()[0],
                    2,
                )


if __name__ == "__main__":
    unittest.main()
