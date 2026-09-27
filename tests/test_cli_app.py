import tempfile
import unittest
import json
from pathlib import Path

from client.protocol import parse_input
from server.database import Database
from server.main import GameServer
from server.models import CompletedRound, PlayerStatus
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


class HostJoinThenDisconnectConnection(JoinThenDisconnectConnection):
    remote_address = ("127.0.0.1", 12345)

    async def recv(self) -> str:
        return json.dumps({"type": "join_host"})


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
    async def test_players_are_not_hosts_and_host_does_not_take_player_slot(self) -> None:
        sessions = Sessions()
        host = FakeConnection()
        self.assertTrue(await sessions.join_host(host))
        first = await sessions.join("Chengzhe", FakeConnection())
        second = await sessions.join("Alice", FakeConnection())
        third = await sessions.join("Third", FakeConnection())

        self.assertIsNone(first.role)
        self.assertIsNone(second.role)
        self.assertIsNone(third.role)

        assignments = await sessions.assign_roles("Alice", "Chengzhe")

        self.assertEqual(
            assignments,
            {"Chengzhe": "B", "Alice": "A", "Third": None},
        )
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

    async def test_disconnect_releases_nickname_and_role(self) -> None:
        sessions = Sessions()
        original = FakeConnection()
        await sessions.join("Chengzhe", original)
        await sessions.join("Alice", FakeConnection())
        await sessions.assign_roles("Chengzhe", "Alice")

        removed = await sessions.remove("Chengzhe", original)
        rejoined = await sessions.join("Chengzhe", FakeConnection())

        self.assertEqual(removed.role, "A")
        self.assertIsNone(rejoined.role)
        self.assertIsNone(rejoined.view_role)
        self.assertFalse(sessions.roles_assigned)

    async def test_host_command_assigns_roles_and_activates_lobby(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            server = GameServer(database, None, None, None)
            await server.sessions.join("Chengzhe", FakeConnection())
            await server.sessions.join("Alice", FakeConnection())
            host = FakeConnection()
            await server.sessions.join_host(host)
            await server._handle_host_command(
                host,
                json.dumps({
                    "type": "assign_roles",
                    "player_a": "Alice",
                    "player_b": "Chengzhe",
                }),
            )

            self.assertEqual(await server.sessions.role_for("Alice"), "A")
            self.assertEqual(await server.sessions.role_for("Chengzhe"), "B")
            self.assertTrue(all(
                player.status == PlayerStatus.EDITING
                for player in server.rounds.players.values()
            ))

    async def test_host_reconnect_receives_latest_world_update(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            result = {
                "world_state": {"gate": "open"},
                "public_information": {"time": "noon"},
                "player_views": {"A": {"seen": 1}, "B": {"seen": 2}},
                "player_statusbar": {"A": {"hp": 90}, "B": {"hp": 80}},
            }
            await database.save_world_update(
                CompletedRound(1, {"A": "open", "B": "watch"}), result
            )
            server = GameServer(database, None, None, None)
            host = HostJoinThenDisconnectConnection("Host")

            await server.handler(host)

            updates = [message for message in host.messages if message["type"] == "world_update"]
            self.assertEqual(updates[0]["round"], 1)
            self.assertEqual(updates[0]["result"], result)

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
            self.assertNotIn("public_information", connection_a.messages[-1])
            self.assertNotIn("public_information", connection_b.messages[-1])

    async def test_disconnect_keeps_round_status_and_reconnect_restores_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            server = GameServer(database, None, None, None)
            connection_a = FakeConnection()
            connection_b = FakeConnection()
            await server.sessions.join("Chengzhe", connection_a)
            await server.sessions.join("Alice", connection_b)
            await server.sessions.assign_roles("Chengzhe", "Alice")
            server.rounds.set_action("A", "检查桌上的信")
            server.rounds.submit("A")

            removed = await server.sessions.remove("Chengzhe", connection_a)
            disconnected = await server._round_snapshot()
            await server.sessions.broadcast(disconnected)

            self.assertEqual(disconnected["players"]["A"]["status"], "READY")
            self.assertFalse(disconnected["players"]["A"]["connected"])
            self.assertFalse(connection_b.messages[-1]["players"]["A"]["connected"])

            rejoined = await server.sessions.join("Chengzhe", FakeConnection())
            reconnected = await server._round_snapshot()
            self.assertEqual(removed.role, "A")
            self.assertIsNone(rejoined.role)
            self.assertEqual(reconnected["players"]["A"]["status"], "READY")
            self.assertFalse(reconnected["players"]["A"]["connected"])

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
            server = GameServer(database, None, None, None)
            connection_b = FakeConnection()
            await server.sessions.join("Alice", connection_b)

            await server.handler(JoinThenDisconnectConnection("Chengzhe"))

            room_texts = [
                message.get("text")
                for message in connection_b.messages
                if message.get("type") == "room_message"
            ]
            self.assertIn("Chengzhe 已加入房间。", room_texts)
            self.assertIn("Chengzhe 已离开房间。", room_texts)


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
