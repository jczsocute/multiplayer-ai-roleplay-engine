import tempfile
import unittest
import json
import shutil
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.gameserver.models import PlayerStatus
from server.gameserver.round_manager import RoundManager
from server.platform.scenario_manager import ScenarioManager
from server.gameserver.session import Sessions
from tests.support import user


class FakeConnection:
    def __init__(self) -> None:
        self.messages = []

    async def send(self, message: str) -> None:
        self.messages.append(json.loads(message))


class JoinThenDisconnectConnection(FakeConnection):
    def __init__(self, user_id: int, name: str) -> None:
        super().__init__()
        self.user_id = user_id
        self.name = name

    async def recv(self) -> str:
        return json.dumps({"type": "join", "room_key": "test-key"})

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
            shutil.copytree(Path("templates/default"), template)
            manager = ScenarioManager(root / "templates", root / "games")
            manager.create_scenario("test_story", 2)

            game = manager.create_game("test_story", "test_game")

            self.assertTrue(game.is_dir())
            self.assertTrue((game / "metadata.json").is_file())
            self.assertEqual(manager.list_scenarios(), ["test_story"])
            self.assertEqual(manager.list_games(), ["test_game"])
            manager.delete_game("test_game")
            self.assertEqual(manager.list_games(), [])

            four_roles = manager.create_scenario("blank_four", 4)
            self.assertEqual(
                json.loads((four_roles / "world/world_state_initial.json").read_text()),
                {"world_information": ""},
            )
            for index in range(1, 5):
                self.assertEqual((four_roles / f"characters/{index}/character.md").read_text(), "")
                self.assertEqual((four_roles / f"characters/{index}/opening.md").read_text(), "")

    def test_default_is_hidden_and_scenario_copy_can_be_deleted(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root / "templates" / "default"
            shutil.copytree(Path("templates/default"), template)
            (template / "marker.txt").write_text("base", encoding="utf-8")
            manager = ScenarioManager(root / "templates", root / "games")

            self.assertEqual(manager.list_scenarios(), [])
            created = manager.create_scenario("lighthouse", 2)
            self.assertEqual((created / "marker.txt").read_text(encoding="utf-8"), "base")
            self.assertEqual(manager.list_scenarios(), ["lighthouse"])

            manager.delete_scenario("lighthouse")
            self.assertEqual(manager.list_scenarios(), [])
            with self.assertRaisesRegex(ValueError, "protected"):
                manager.delete_scenario("default")
            with self.assertRaisesRegex(ValueError, "not a playable"):
                manager.create_game("default", "should_not_exist")
            with self.assertRaises(ValueError):
                manager.create_scenario("../escape", 2)

    def test_showcase_examples_are_hidden_and_not_playable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ("default", "default_en", "example1", "example1_en"):
                shutil.copytree(Path("templates") / name, root / "templates" / name)
            manager = ScenarioManager(root / "templates", root / "games")
            self.assertEqual(manager.list_scenarios(), [])
            for name in ("default_en", "example1", "example1_en"):
                with self.assertRaisesRegex(ValueError, "not a playable"):
                    manager.create_game(name, "game")
                with self.assertRaisesRegex(ValueError, "protected"):
                    manager.delete_scenario(name)


class RoleAssignmentTests(unittest.IsolatedAsyncioTestCase):
    async def test_players_are_spectators_until_the_owner_assigns_roles(self) -> None:
        sessions = Sessions()
        first = await sessions.join(user(1, "Chengzhe"), FakeConnection())
        second = await sessions.join(user(2, "Alice"), FakeConnection())
        third = await sessions.join(user(3, "Third"), FakeConnection())

        self.assertIsNone(first.user.role)
        self.assertIsNone(second.user.role)
        self.assertIsNone(third.user.role)

        await sessions.assign_roles({"P1": 2, "P2": 1})

        self.assertEqual(await sessions.role_assignments(), {"P1": 2, "P2": 1})
        self.assertEqual(await sessions.role_usernames(), {"P1": "Alice", "P2": "Chengzhe"})
        self.assertTrue(sessions.roles_assigned)

    async def test_round_state_broadcast_on_submit_cancel_pause_and_resume(self) -> None:
        sessions = Sessions()
        connection_a = FakeConnection()
        connection_b = FakeConnection()
        await sessions.join(user(1, "Alice"), connection_a)
        await sessions.join(user(2, "Bob"), connection_b)
        await sessions.assign_roles({"P1": 1, "P2": 2})
        rounds = RoundManager()

        rounds.set_action("P1", "行动")
        rounds.submit("P1")
        await sessions.broadcast(rounds.snapshot())
        rounds.cancel_submit("P1")
        rounds.pause("P1")
        await sessions.broadcast(rounds.snapshot())
        rounds.resume("P1")
        rounds.submit("P1")
        rounds.set_action("P2", "配合行动")
        rounds.submit("P2")
        await sessions.broadcast(rounds.snapshot())

        for connection in (connection_a, connection_b):
            self.assertEqual(connection.messages[0]["players"]["P1"]["status"], "READY")
            self.assertEqual(connection.messages[1]["players"]["P1"]["status"], "PAUSED")
            self.assertEqual(
                connection.messages[2]["players"]["P1"]["status"], "PROCESSING"
            )
            self.assertEqual(
                connection.messages[2]["players"]["P2"]["status"], "PROCESSING"
            )

    async def test_disconnect_releases_participant_and_role(self) -> None:
        sessions = Sessions()
        original = FakeConnection()
        await sessions.join(user(1, "Chengzhe"), original)
        await sessions.join(user(2, "Alice"), FakeConnection())
        await sessions.assign_roles({"P1": 1, "P2": 2})

        removed = await sessions.remove(1, original)
        rejoined = await sessions.join(user(1, "Chengzhe"), FakeConnection())

        self.assertEqual(removed.role, "P1")
        self.assertTrue(rejoined.created)
        self.assertIsNone(rejoined.user.role)
        self.assertIsNone(rejoined.user.view_role)
        self.assertFalse(sessions.roles_assigned)

    async def test_owner_assigns_roles_through_the_public_command(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            server = GameServer(database, None, None, owner_user_id=1)
            owner = FakeConnection()
            await server.sessions.join(user(1, "Chengzhe"), owner)
            await server.sessions.join(user(2, "Alice"), FakeConnection())
            await server._handle_command(
                1, owner,
                json.dumps({
                    "type": "assign_roles",
                    "assignments": {"P1": 2, "P2": 1},
                }),
            )

            self.assertEqual(await server.sessions.role_for(2), "P1")
            self.assertEqual(await server.sessions.role_for(1), "P2")
            self.assertTrue(all(
                player.status == PlayerStatus.EDITING
                for player in server.rounds.players.values()
            ))

    async def test_statusbar_is_only_sent_to_its_player(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize(
                {}, {"P1": {"secret_a": 1}, "P2": {"secret_b": 2}}
            )
            server = GameServer(database, None, None)
            connection_a = FakeConnection()
            connection_b = FakeConnection()
            await server.sessions.join(user(1, "Alice"), connection_a)
            await server.sessions.join(user(2, "Bob"), connection_b)
            await server.sessions.assign_roles({"P1": 1, "P2": 2})

            await server._send_status(1, connection_a)
            await server._send_status(2, connection_b)

            self.assertEqual(connection_a.messages[-1]["character_status"], {"secret_a": 1})
            self.assertEqual(connection_b.messages[-1]["character_status"], {"secret_b": 2})
            self.assertNotIn("secret_b", connection_a.messages[-1]["character_status"])
            self.assertNotIn("public_information", connection_a.messages[-1])
            self.assertNotIn("public_information", connection_b.messages[-1])

    async def test_disconnect_keeps_round_status_and_reconnect_restores_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            server = GameServer(database, None, None)
            connection_a = FakeConnection()
            connection_b = FakeConnection()
            await server.sessions.join(user(1, "Chengzhe"), connection_a)
            await server.sessions.join(user(2, "Alice"), connection_b)
            await server.sessions.assign_roles({"P1": 1, "P2": 2})
            server.rounds.set_action("P1", "检查桌上的信")
            server.rounds.submit("P1")

            removed = await server.sessions.remove(1, connection_a)
            disconnected = await server._round_snapshot()
            await server.sessions.broadcast(disconnected)

            self.assertEqual(disconnected["players"]["P1"]["status"], "READY")
            self.assertFalse(disconnected["players"]["P1"]["connected"])
            self.assertFalse(connection_b.messages[-1]["players"]["P1"]["connected"])

            rejoined = await server.sessions.join(user(1, "Chengzhe"), FakeConnection())
            reconnected = await server._round_snapshot()
            self.assertEqual(removed.role, "P1")
            self.assertTrue(rejoined.created)
            self.assertIsNone(rejoined.user.role)
            self.assertEqual(reconnected["players"]["P1"]["status"], "READY")
            self.assertFalse(reconnected["players"]["P1"]["connected"])

    async def test_latest_draft_is_persisted_and_returned_by_status(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            server = GameServer(database, None, None)
            connection = FakeConnection()
            await server.sessions.join(user(1, "Alice"), connection)
            await server.sessions.join(user(2, "Bob"), FakeConnection())
            await server.sessions.assign_roles({"P1": 1, "P2": 2})

            await server._handle_command(
                1, connection, json.dumps({"type": "action", "text": "旧 Draft"})
            )
            await server._handle_command(
                1, connection, json.dumps({"type": "action", "text": "最新完整 Draft"})
            )
            await server._send_status(1, connection)

            self.assertEqual(server.rounds.players["P1"].action, "最新完整 Draft")
            self.assertEqual(connection.messages[-1]["draft"], "最新完整 Draft")
            recovery = await database.get_recovery_data(1)
            self.assertEqual(recovery["players"]["P1"]["action"], "最新完整 Draft")

    async def test_handler_broadcasts_join_but_not_leave_during_grace(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            server = GameServer(database, None, None)
            connection_b = FakeConnection()
            await server.sessions.join(user(2, "Alice"), connection_b)

            await server.public_handler(
                JoinThenDisconnectConnection(1, "Chengzhe"), user(1, "Chengzhe")
            )

            room_texts = [
                message.get("text")
                for message in connection_b.messages
                if message.get("type") == "room_message"
            ]
            self.assertIn("Chengzhe 已加入房间。", room_texts)
            self.assertNotIn("Chengzhe 已离开房间。", room_texts)


class CliRoundFlowTests(unittest.TestCase):
    def test_commands_cover_pause_cancel_processing_and_next_round(self) -> None:
        rounds = RoundManager()

        rounds.pause("P1")
        self.assertEqual(rounds.players["P1"].status, PlayerStatus.PAUSED)
        rounds.resume("P1")
        rounds.set_action("P1", "打开舱门")
        rounds.submit("P1")
        self.assertEqual(rounds.players["P1"].status, PlayerStatus.READY)
        rounds.cancel_submit("P1")
        self.assertEqual(rounds.players["P1"].status, PlayerStatus.EDITING)
        rounds.submit("P1")
        rounds.set_action("P2", "检查仪表")
        completed = rounds.submit("P2")
        self.assertIsNotNone(completed)
        self.assertTrue(rounds.is_processing())

        rounds.start_next_round()
        self.assertEqual(rounds.round_number, 2)
        self.assertTrue(
            all(player.status == PlayerStatus.EDITING for player in rounds.players.values())
        )


if __name__ == "__main__":
    unittest.main()
