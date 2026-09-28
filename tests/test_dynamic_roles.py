import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from server.config import load_role_limits
from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.gameserver.models import CompletedRound, PlayerStatus
from server.gameserver.roles import RoleConfig
from server.gameserver.round_manager import RoundManager
from server.platform.scenario_manager import ScenarioManager
from server.gameserver.session import Sessions
from tests.support import user


class FakeConnection:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


class RoleConfigTests(unittest.TestCase):
    def test_valid_config_builds_stable_ids(self) -> None:
        roles = RoleConfig.from_data(
            {"count": 3, "names": ["甲", "乙", "丙"]}, min_count=2, max_count=4
        )
        self.assertEqual(roles.role_ids, ("P1", "P2", "P3"))
        self.assertEqual(roles.names["P3"], "丙")

    def test_count_and_names_must_match(self) -> None:
        with self.assertRaisesRegex(ValueError, "names length"):
            RoleConfig.from_data({"count": 3, "names": ["甲", "乙"]})
        with self.assertRaisesRegex(ValueError, "non-empty"):
            RoleConfig.from_data({"count": 2, "names": ["甲", "  "]})

    def test_deployment_limits_are_configurable(self) -> None:
        with self.assertRaisesRegex(ValueError, "at most 4"):
            RoleConfig.from_data(
                {"count": 5, "names": [str(index) for index in range(5)]},
                min_count=2,
                max_count=4,
            )
        roles = RoleConfig.from_data(
            {"count": 6, "names": [str(index) for index in range(6)]},
            min_count=1,
            max_count=6,
        )
        self.assertEqual(roles.role_ids[-1], "P6")

    def test_invalid_environment_limits_fail(self) -> None:
        with patch.dict(
            "os.environ", {"MIN_ROLE_COUNT": "0", "MAX_ROLE_COUNT": "4"}
        ):
            with self.assertRaisesRegex(RuntimeError, "positive"):
                load_role_limits()
        with patch.dict(
            "os.environ", {"MIN_ROLE_COUNT": "4", "MAX_ROLE_COUNT": "3"}
        ):
            with self.assertRaisesRegex(ValueError, "greater than or equal"):
                load_role_limits()


class ScenarioRoleScaffoldTests(unittest.TestCase):
    def test_scaffolds_two_three_and_four_roles(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            shutil.copytree(Path("templates/default"), root / "templates" / "default")
            manager = ScenarioManager(root / "templates", root / "games")
            for count in (2, 3, 4):
                scenario = manager.create_scenario(f"story_{count}", count)
                roles = json.loads((scenario / "metadata.json").read_text(encoding="utf-8"))
                self.assertEqual(roles, {
                    "count": count,
                    "names": [f"角色{index}" for index in range(1, count + 1)],
                    # The CLI scenario name becomes the payload Script title.
                    "title": f"story_{count}",
                    "introduction": "",
                    "tags": [],
                })
                # A scaffolded payload never keeps the legacy file name.
                self.assertFalse((scenario / "roles.json").exists())
                role_ids = {f"P{index}" for index in range(1, count + 1)}
                self.assertEqual(
                    {path.stem for path in (scenario / "characters").glob("player_*.md")},
                    {f"player_{index}" for index in range(1, count + 1)},
                )
                self.assertEqual(
                    {path.stem for path in (scenario / "characters/statusbar").glob("player_*.json")},
                    {f"player_{index}" for index in range(1, count + 1)},
                )
                opening_files = sorted(
                    (scenario / "characters/opening").glob("player_*.md")
                )
                self.assertEqual(
                    {path.stem for path in opening_files},
                    {f"player_{index}" for index in range(1, count + 1)},
                )
                for index, path in enumerate(opening_files, 1):
                    self.assertIn(f"角色{index}", path.read_text(encoding="utf-8"))
                initial = json.loads(
                    (scenario / "world/initial_state.json").read_text(encoding="utf-8")
                )
                schema = json.loads(
                    (scenario / "schemas/world_updater_output.json").read_text(encoding="utf-8")
                )
                self.assertEqual(set(initial["characters"]), role_ids)
                self.assertEqual(set(schema["player_views"]), role_ids)
                self.assertEqual(set(schema["player_statusbar"]), role_ids)


class DynamicRoundAndSessionTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_roles_all_must_submit(self) -> None:
        rounds = RoundManager(("P1", "P2", "P3"))
        for role in ("P1", "P2", "P3"):
            rounds.set_action(role, f"action {role}")
        self.assertIsNone(rounds.submit("P1"))
        self.assertIsNone(rounds.submit("P2"))
        completed = rounds.submit("P3")
        self.assertEqual(completed.actions, {
            "P1": "action P1", "P2": "action P2", "P3": "action P3"
        })

    async def test_four_role_runtime_has_no_fixed_upper_bound(self) -> None:
        roles = ("P1", "P2", "P3", "P4")
        rounds = RoundManager(roles)
        for role in roles:
            rounds.set_action(role, role)
        for role in roles[:-1]:
            self.assertIsNone(rounds.submit(role))
        self.assertEqual(set(rounds.submit("P4").actions), set(roles))

    async def test_three_role_assignment_and_views(self) -> None:
        sessions = Sessions(("P1", "P2", "P3"))
        names = ("Alice", "Bob", "Carol", "Tom")
        sockets = {}
        for index, name in enumerate(names, 1):
            sockets[name] = FakeConnection()
            await sessions.join(user(index, name), sockets[name])
        await sessions.assign_roles({"P1": 1, "P2": 2, "P3": 3})
        await sessions.set_view(4, "P3")
        self.assertEqual(await sessions.role_assignments(), {
            "P1": 1, "P2": 2, "P3": 3
        })
        self.assertEqual(await sessions.role_usernames(), {
            "P1": "Alice", "P2": "Bob", "P3": "Carol"
        })
        self.assertEqual(sessions.users[4].view_role, "P3")


class DynamicDatabaseTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_role_round_persists_and_recovers(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "game.db"
            roles = ("P1", "P2", "P3")
            database = Database(str(path), roles)
            await database.initialize(
                {"characters": {role: {} for role in roles}},
                {role: {"energy": 10} for role in roles},
            )
            completed = CompletedRound(1, {role: f"action {role}" for role in roles})
            result = {
                "world_state": {"tick": 1},
                "public_information": {"weather": "rain"},
                "player_views": {role: {"seen": role} for role in roles},
                "player_statusbar": {role: {"energy": 9} for role in roles},
            }
            await database.save_world_update(completed, result)
            await database.save_narrations(
                1, {role: {"text": f"story {role}"} for role in roles}
            )
            recovery = await database.get_recovery_data(1)
            self.assertEqual(recovery["actions"], completed.actions)
            self.assertEqual(set(recovery["player_views"]), set(roles))
            self.assertEqual(set(recovery["narrations"]), set(roles))
            for role in roles:
                self.assertEqual(
                    (await database.get_player_display(role))["statusbar"], {"energy": 9}
                )
            await database.finish_round(completed)
            for role in roles:
                self.assertEqual(
                    [entry["kind"] for entry in await database.get_role_history(role)],
                    ["action", "narration", "statusbar"],
                )
            with sqlite3.connect(path) as connection:
                self.assertEqual(
                    connection.execute("SELECT COUNT(*) FROM round_actions").fetchone()[0], 3
                )


class ThreeRoleWorldUpdater:
    def __init__(self) -> None:
        self.calls: list[dict[str, str]] = []

    async def update(self, current_world_state: str, actions: dict[str, str]) -> dict:
        self.calls.append(actions)
        return {
            "world_state": {"round": 1},
            "public_information": {},
            "player_views": {role: {"view": role} for role in actions},
            "player_statusbar": {role: {"ready": True} for role in actions},
        }


class ThreeRoleNarrator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def narrate(self, role, public, view, statusbar, history):
        self.calls.append(role)
        return {"text": f"narration {role}", "status": {}}


class UnusedViews:
    async def generate(self, role, world):
        raise AssertionError("normal flow must use WorldUpdater views")


class DynamicAiFlowTests(unittest.IsolatedAsyncioTestCase):
    async def test_three_role_flow_updates_once_and_sends_each_view(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roles = RoleConfig.from_data({
                "count": 3, "names": ["角色1", "角色2", "角色3"]
            })
            database = Database(str(Path(directory) / "game.db"), roles.role_ids)
            await database.initialize()
            updater = ThreeRoleWorldUpdater()
            narrator = ThreeRoleNarrator()
            server = GameServer(
                database, updater, UnusedViews(), narrator, role_config=roles
            )
            sockets = {}
            assignments = {}
            for index, role in enumerate(roles.role_ids, 1):
                sockets[role] = FakeConnection()
                await server.sessions.join(user(index, f"player{index}"), sockets[role])
                assignments[role] = index
                server.rounds.set_action(role, f"action {role}")
            await server.sessions.assign_roles(assignments)
            server.rounds.submit("P1")
            server.rounds.submit("P2")
            completed = server.rounds.submit("P3")
            await server._process_round(completed)
            self.assertEqual(updater.calls, [{
                "P1": "action P1", "P2": "action P2", "P3": "action P3"
            }])
            self.assertEqual(set(narrator.calls), set(roles.role_ids))
            for role, socket in sockets.items():
                rounds = [message for message in socket.messages if message["type"] == "role_round"]
                self.assertEqual([message["role"] for message in rounds], [role])
            self.assertEqual(server.rounds.round_number, 2)
            self.assertTrue(all(
                player.status == PlayerStatus.EDITING
                for player in server.rounds.players.values()
            ))
