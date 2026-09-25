import tempfile
import unittest
import json
from pathlib import Path

from client.protocol import parse_input
from server.database import Database
from server.main import GameServer
from server.models import PlayerStatus
from server.round_manager import RoundManager
from server.scenario_manager import ScenarioManager
from server.session import Sessions


class FakeConnection:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, message: str) -> None:
        self.messages.append(json.loads(message))


class JoinThenDisconnectConnection(FakeConnection):
    def __init__(self, name: str) -> None:
        super().__init__()
        self.name = name

    async def recv(self) -> str:
        return json.dumps({"type": "join", "name": self.name})

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration

    async def close(self, **_kwargs) -> None:
        pass


class ScenarioManagerTests(unittest.TestCase):
    def test_create_copy_list_and_delete_game(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "templates" / "default"
            template.mkdir(parents=True)
            (template / "world.md").write_text("test world", encoding="utf-8")
            manager = ScenarioManager(root / "templates", root / "games")
            manager.create_scenario("test_story")

            game = manager.create_game("test_story", "test_game")

            self.assertTrue(game.is_dir())
            self.assertEqual((game / "world.md").read_text(encoding="utf-8"), "test world")
            self.assertEqual(manager.list_scenarios(), ["test_story"])
            self.assertEqual(manager.list_games(), ["test_game"])
            manager.delete_game("test_game")
            self.assertEqual(manager.list_games(), [])

    def test_default_is_hidden_and_scenario_copy_can_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "templates" / "default"
            template.mkdir(parents=True)
            (template / "marker.txt").write_text("base", encoding="utf-8")
            manager = ScenarioManager(root / "templates", root / "games")

            self.assertEqual(manager.list_scenarios(), [])
            created = manager.create_scenario("lighthouse")
            self.assertEqual((created / "marker.txt").read_text(encoding="utf-8"), "base")
            self.assertEqual(manager.list_scenarios(), ["lighthouse"])

            manager.delete_scenario("lighthouse")
            self.assertEqual(manager.list_scenarios(), [])
            with self.assertRaisesRegex(ValueError, "protected"):
                manager.delete_scenario("default")
            with self.assertRaisesRegex(ValueError, "not a playable"):
                manager.create_game("default", "should_not_exist")
            with self.assertRaises(ValueError):
                manager.create_scenario("../escape")


class RoleAssignmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_lobby_host_assignment_and_third_connection_rejection(self) -> None:
        sessions = Sessions()
        first = await sessions.join("Chengzhe", FakeConnection())
        second = await sessions.join("Alice", FakeConnection())

        self.assertTrue(first[0].is_host)
        self.assertIsNone(first[0].role)
        self.assertFalse(second[0].is_host)
        self.assertIsNone(second[0].role)
        self.assertIsNone(await sessions.join("Third", FakeConnection()))

        assignments = await sessions.assign_roles("Chengzhe", "B")

        self.assertEqual(assignments, {"Chengzhe": "B", "Alice": "A"})
        self.assertTrue(sessions.roles_assigned)

    async def test_lobby_activation_and_state_broadcast(self) -> None:
        sessions = Sessions()
        connection_a = FakeConnection()
        connection_b = FakeConnection()
        await sessions.add("A", connection_a)
        await sessions.add("B", connection_b)
        rounds = RoundManager()
        rounds.enter_lobby()
        self.assertTrue(
            all(player.status == PlayerStatus.LOBBY for player in rounds.players.values())
        )

        rounds.activate_lobby()
        await sessions.broadcast(rounds.snapshot())
        rounds.set_action("A", "行动")
        rounds.submit("A")
        await sessions.broadcast(rounds.snapshot())
        rounds.cancel_submit("A")
        rounds.pause("A")
        await sessions.broadcast(rounds.snapshot())
        rounds.resume("A")
        rounds.submit("A")
        rounds.set_action("B", "配合行动")
        rounds.submit("B")
        await sessions.broadcast(rounds.snapshot())

        for connection in (connection_a, connection_b):
            self.assertEqual(connection.messages[0]["players"]["A"]["status"], "EDITING")
            self.assertEqual(connection.messages[1]["players"]["A"]["status"], "READY")
            self.assertEqual(connection.messages[2]["players"]["A"]["status"], "PAUSED")
            self.assertEqual(
                connection.messages[3]["players"]["A"]["status"], "PROCESSING"
            )
            self.assertEqual(
                connection.messages[3]["players"]["B"]["status"], "PROCESSING"
            )

    async def test_role_assignment_persists_for_reconnect(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            await database.register_participant("Chengzhe", True)
            await database.register_participant("Alice", False)
            await database.save_role_assignment({"Chengzhe": "B", "Alice": "A"})

            restored = Sessions(await database.get_participants())
            joined = await restored.join("Chengzhe", FakeConnection())

            self.assertFalse(joined[1])
            self.assertTrue(joined[0].is_host)
            self.assertEqual(joined[0].role, "B")

    async def test_statusbar_is_only_sent_to_its_player(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize(
                {}, {"A": {"secret_a": 1}, "B": {"secret_b": 2}}
            )
            server = GameServer(database, None, None, None)
            connection_a = FakeConnection()
            connection_b = FakeConnection()
            await server.sessions.add("A", connection_a)
            await server.sessions.add("B", connection_b)

            await server._send_status("A", connection_a)
            await server._send_status("B", connection_b)

            self.assertEqual(connection_a.messages[-1]["statusbar"], {"secret_a": 1})
            self.assertEqual(connection_b.messages[-1]["statusbar"], {"secret_b": 2})
            self.assertNotIn("secret_b", connection_a.messages[-1]["statusbar"])

    async def test_disconnect_keeps_round_status_and_reconnect_restores_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            participants = [
                {"name": "Chengzhe", "is_host": True, "role": "A"},
                {"name": "Alice", "is_host": False, "role": "B"},
            ]
            server = GameServer(database, None, None, None, participants=participants)
            connection_a = FakeConnection()
            connection_b = FakeConnection()
            await server.sessions.join("Chengzhe", connection_a)
            await server.sessions.join("Alice", connection_b)
            server.rounds.set_action("A", "检查桌上的信")
            server.rounds.submit("A")

            await server.sessions.remove("Chengzhe", connection_a)
            disconnected = await server._round_snapshot()
            await server.sessions.broadcast(disconnected)

            self.assertEqual(disconnected["players"]["A"]["status"], "READY")
            self.assertFalse(disconnected["players"]["A"]["connected"])
            self.assertFalse(connection_b.messages[-1]["players"]["A"]["connected"])

            rejoined = await server.sessions.join("Chengzhe", FakeConnection())
            reconnected = await server._round_snapshot()
            self.assertEqual(rejoined[0].role, "A")
            self.assertEqual(reconnected["players"]["A"]["status"], "READY")
            self.assertTrue(reconnected["players"]["A"]["connected"])

    async def test_latest_draft_is_persisted_and_returned_by_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            server = GameServer(database, None, None, None)
            connection = FakeConnection()
            await server.sessions.add("A", connection)

            await server._handle_command(
                "A", connection, json.dumps({"type": "action", "text": "旧 Draft"})
            )
            await server._handle_command(
                "A", connection, json.dumps({"type": "action", "text": "最新完整 Draft"})
            )
            await server._send_status("A", connection)

            self.assertEqual(server.rounds.players["A"].action, "最新完整 Draft")
            self.assertEqual(connection.messages[-1]["draft"], "最新完整 Draft")
            recovery = await database.get_recovery_data(1)
            self.assertEqual(recovery["players"]["A"]["action"], "最新完整 Draft")

    async def test_handler_broadcasts_reconnect_and_leave_system_messages(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            participants = [
                {"name": "Chengzhe", "is_host": True, "role": "A"},
                {"name": "Alice", "is_host": False, "role": "B"},
            ]
            server = GameServer(database, None, None, None, participants=participants)
            connection_b = FakeConnection()
            await server.sessions.join("Alice", connection_b)

            await server.handler(JoinThenDisconnectConnection("Chengzhe"))

            presence_events = [
                message.get("event")
                for message in connection_b.messages
                if message.get("type") == "system"
            ]
            self.assertEqual(presence_events, ["reconnected", "left"])
            states = [
                message
                for message in connection_b.messages
                if message.get("type") == "state"
            ]
            self.assertTrue(states[0]["players"]["A"]["connected"])
            self.assertFalse(states[-1]["players"]["A"]["connected"])
            self.assertEqual(
                states[0]["players"]["A"]["status"],
                states[-1]["players"]["A"]["status"],
            )


class CliRoundFlowTests(unittest.TestCase):
    def test_commands_cover_pause_cancel_processing_and_next_round(self) -> None:
        rounds = RoundManager()

        self.assertEqual(parse_input("/pause"), {"type": "pause"})
        rounds.pause("A")
        self.assertEqual(rounds.players["A"].status, PlayerStatus.PAUSED)
        rounds.resume("A")
        rounds.set_action("A", "打开舱门")
        rounds.submit("A")
        self.assertEqual(rounds.players["A"].status, PlayerStatus.READY)
        rounds.cancel_submit("A")
        self.assertEqual(rounds.players["A"].status, PlayerStatus.EDITING)
        rounds.submit("A")
        rounds.set_action("B", "检查仪表")
        completed = rounds.submit("B")
        self.assertIsNotNone(completed)
        self.assertTrue(rounds.is_processing())

        rounds.start_next_round()
        self.assertEqual(rounds.round_number, 2)
        self.assertTrue(
            all(player.status == PlayerStatus.EDITING for player in rounds.players.values())
        )


if __name__ == "__main__":
    unittest.main()
