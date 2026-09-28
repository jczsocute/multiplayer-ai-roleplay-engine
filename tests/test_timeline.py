import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.gameserver.models import PlayerStatus, RoundStage
from server.gameserver.roles import RoleConfig
from tests.support import user


ROLES = ("P1", "P2", "P3")
NAMES = ("角色1", "角色2", "角色3")
ROLE_USERS = {"P1": 1, "P2": 2, "P3": 3}
INITIAL_WORLD = "# Initial world"

ROUND_1 = {"P1": "A1", "P2": "B1", "P3": "C1"}
ROUND_2 = {"P1": "A2", "P2": "B2", "P3": "C2"}
ROUND_3 = {"P1": "A3", "P2": "B3", "P3": "C3"}
ROUND_4 = {"P1": "A4", "P2": "B4", "P3": "C4"}
ROUND_5 = {"P1": "A5", "P2": "B5", "P3": "C5"}


class FakeWebSocket:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))

    def errors(self) -> list[str]:
        return [
            message["detail"] for message in self.messages if message["type"] == "error"
        ]


class RecordingWorldUpdater:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.serial = 0

    async def update(self, current_world_state: str, actions: dict[str, str]) -> dict:
        self.calls.append((current_world_state, dict(actions)))
        self.serial += 1
        serial = self.serial
        return {
            "world_state": {"round": f"v{serial}"},
            "public_information": {"round": serial},
            "player_views": {role: {"role": role, "round": serial} for role in ROLES},
            "player_statusbar": {role: {"hp": 10 + serial} for role in ROLES},
        }


class RecordingNarrator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def narrate(
        self,
        player_id: str,
        public_world_info: str,
        player_view: str,
        player_statusbar: dict,
        chat_history: list,
    ) -> dict:
        self.calls.append(player_id)
        return {"text": f"narration {player_id} #{len(self.calls)}", "status": {}}


class UnusedPlayerViews:
    async def generate(self, player_id: str, world_state: str) -> str:
        raise AssertionError("PlayerViewGenerator must not run in the normal pipeline")


class ExplodingDatabase(Database):
    """Fails at the last step of the retry/rollback transaction."""

    def _clear_player_inputs_sync(self, connection) -> None:  # noqa: ANN001
        raise RuntimeError("simulated database failure")


class TimelineTests(unittest.IsolatedAsyncioTestCase):
    async def make_server(self, owner_user_id: int = 1, database_cls=Database):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        role_config = RoleConfig.from_data({"count": 3, "names": list(NAMES)})
        database = database_cls(str(Path(directory.name) / "game.db"), role_config.role_ids)
        await database.initialize(INITIAL_WORLD)
        updater = RecordingWorldUpdater()
        narrator = RecordingNarrator()
        server = GameServer(
            database,
            updater,
            UnusedPlayerViews(),
            narrator,
            scenario_name="test",
            role_config=role_config,
            owner_user_id=owner_user_id,
        )
        self.ws = FakeWebSocket()
        self.sockets: dict[str, FakeWebSocket] = {}
        for role_id in role_config.role_ids:
            socket = FakeWebSocket()
            self.sockets[role_id] = socket
            await server.sessions.join(user(ROLE_USERS[role_id], role_id), socket)
        await server.sessions.assign_roles(dict(ROLE_USERS.items()))
        return server, database, updater, narrator

    async def submit_round(self, server: GameServer, actions: dict[str, str]) -> None:
        for role_id, text in actions.items():
            user_id = ROLE_USERS[role_id]
            await server._handle_command(
                user_id, self.ws, json.dumps({"type": "action", "text": text})
            )
            await server._handle_command(user_id, self.ws, json.dumps({"type": "submit"}))

    async def send(self, server: GameServer, user_id: int, payload: dict, ws=None) -> FakeWebSocket:
        target = ws or self.ws
        await server._handle_command(user_id, target, json.dumps(payload))
        return target

    # --- retry ---------------------------------------------------------------

    async def test_retry_first_round_reruns_everything_from_initial_world(self) -> None:
        server, database, updater, narrator = await self.make_server()
        await self.submit_round(server, ROUND_1)

        self.assertEqual([call[0] for call in updater.calls], [INITIAL_WORLD])
        self.assertEqual(narrator.calls, ["P1", "P2", "P3"])
        first_world = await database.get_world_state()

        # Round 2 input that must be cleared by the retry.
        await self.send(server, 1, {"type": "action", "text": "draft for round 2"})
        await self.send(server, 2, {"type": "action", "text": "B-next"})
        await self.send(server, 2, {"type": "submit"})
        self.assertEqual(server.rounds.players["P2"].status, PlayerStatus.READY)

        await self.send(server, 1, {"type": "retry"})

        # Same actions, same base world, full re-run, new output.
        self.assertEqual(len(updater.calls), 2)
        self.assertEqual(updater.calls[1][0], INITIAL_WORLD)
        self.assertEqual(updater.calls[1][1], ROUND_1)
        self.assertEqual(narrator.calls, ["P1", "P2", "P3", "P1", "P2", "P3"])
        self.assertNotEqual(await database.get_world_state(), first_world)
        self.assertEqual(server.rounds.round_number, 2)
        for player in server.rounds.players.values():
            self.assertEqual(player.status, PlayerStatus.EDITING)
            self.assertEqual(player.action, "")

        with sqlite3.connect(database.path) as connection:
            # Exactly one set of outputs for the round.
            for table in ("chat_messages", "player_views"):
                column = "round_number" if table == "chat_messages" else "round_id"
                self.assertEqual(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {column} = 1"
                    ).fetchone()[0],
                    6 if table == "chat_messages" else 3,
                )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM public_world_info WHERE round_id = 1"
                ).fetchone()[0],
                1,
            )
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM player_statusbars WHERE round_id = 1"
                ).fetchone()[0],
                3,
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) FROM rounds").fetchone()[0], 2
            )
            players = {
                row[0]: (row[1], row[2])
                for row in connection.execute(
                    "SELECT player_id, status, current_action FROM players"
                ).fetchall()
            }
        self.assertEqual(set(players), set(ROLES))
        for status, action in players.values():
            self.assertEqual((status, action), ("EDITING", ""))

    async def test_retry_later_round_starts_from_the_previous_result(self) -> None:
        server, database, updater, _ = await self.make_server()
        await self.submit_round(server, ROUND_1)
        first_world = await database.get_world_state()
        await self.submit_round(server, ROUND_2)
        second_world = await database.get_world_state()
        self.assertEqual(server.rounds.round_number, 3)

        await self.send(server, 1, {"type": "retry"})

        self.assertEqual(len(updater.calls), 3)
        self.assertEqual(updater.calls[2][0], first_world)
        self.assertEqual(updater.calls[2][1], ROUND_2)
        self.assertNotEqual(await database.get_world_state(), second_world)
        self.assertEqual(server.rounds.round_number, 3)
        with sqlite3.connect(database.path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT result_world_state FROM rounds WHERE round_number = 1"
                ).fetchone()[0],
                first_world,
            )
            self.assertEqual(
                [row[0] for row in connection.execute(
                    "SELECT round_number FROM rounds ORDER BY round_number"
                )],
                [1, 2, 3],
            )
            self.assertEqual(
                connection.execute(
                    "SELECT status FROM rounds WHERE round_number = 3"
                ).fetchone()[0],
                "OPEN",
            )

    async def test_retry_without_a_finished_round_is_rejected(self) -> None:
        server, _, updater, _ = await self.make_server()

        await self.send(server, 1, {"type": "retry"})

        self.assertIn("没有可重新生成的回合", self.ws.errors()[-1])
        self.assertEqual(updater.calls, [])

    # --- rollback ------------------------------------------------------------

    async def test_rollback_deletes_later_rounds_and_restores_world(self) -> None:
        server, database, _, _ = await self.make_server()
        for actions in (ROUND_1, ROUND_2, ROUND_3, ROUND_4, ROUND_5):
            await self.submit_round(server, actions)
        self.assertEqual(server.rounds.round_number, 6)
        with sqlite3.connect(database.path) as connection:
            third_world = connection.execute(
                "SELECT result_world_state FROM rounds WHERE round_number = 3"
            ).fetchone()[0]

        viewer = FakeWebSocket()
        await server.sessions.join(user(9, "Tom"), viewer)
        await server.sessions.set_view(9, "P1")
        viewer.messages.clear()

        # Pending round 6 input must not survive the rollback.
        await self.send(server, 1, {"type": "action", "text": "draft round 6"})
        await self.send(server, 2, {"type": "action", "text": "C-next"})
        await self.send(server, 2, {"type": "submit"})

        await self.send(server, 1, {"type": "rollback", "round": 3})

        self.assertEqual(server.rounds.round_number, 4)
        self.assertEqual(server.rounds.stage, RoundStage.WAITING_INPUT)
        for player in server.rounds.players.values():
            self.assertEqual(player.status, PlayerStatus.EDITING)
            self.assertEqual(player.action, "")
        self.assertEqual(await database.get_world_state(), third_world)

        with sqlite3.connect(database.path) as connection:
            rounds = [
                (row[0], row[1])
                for row in connection.execute(
                    "SELECT round_number, status FROM rounds ORDER BY round_number"
                )
            ]
            self.assertEqual(rounds, [(1, "COMPLETED"), (2, "COMPLETED"), (3, "COMPLETED"), (4, "OPEN")])
            for table, column in (
                ("chat_messages", "round_number"),
                ("player_views", "round_id"),
                ("player_statusbars", "round_id"),
                ("public_world_info", "round_id"),
                ("round_actions", "round_id"),
            ):
                self.assertEqual(
                    connection.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE {column} > 3"
                    ).fetchone()[0],
                    0,
                )

        # Role assignment, connections and Room Chat survive the timeline change.
        self.assertEqual(await server.sessions.role_for(1), "P1")
        self.assertEqual(await server.sessions.role_for(3), "P3")
        self.sockets["P1"].messages.clear()
        await self.send(server, 1, {"type": "room_chat", "text": "回滚后再聊一句"})
        self.assertIn(
            "回滚后再聊一句",
            [
                message["text"]
                for message in self.sockets["P1"].messages
                if message["type"] == "room_message"
            ],
        )

        # Spectators get a rebuilt, truncated history.
        role_views = [m for m in viewer.messages if m["type"] == "role_view"]
        self.assertTrue(role_views)
        self.assertTrue(role_views[-1]["reset"])
        rounds_in_history = {entry.get("round") for entry in role_views[-1]["history"]}
        self.assertTrue(rounds_in_history)
        self.assertTrue(rounds_in_history <= {1, 2, 3})

    async def test_room_chat_is_never_stored_in_the_story_plane(self) -> None:
        server, database, _, _ = await self.make_server()
        await self.submit_round(server, ROUND_1)
        await self.send(server, 1, {"type": "room_chat", "text": "Round 1 写崩了，我们 retry 吧"})

        with sqlite3.connect(database.path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE role NOT IN ('player', 'narrator')"
                ).fetchone()[0],
                0,
            )

        await self.send(server, 1, {"type": "retry"})

        with sqlite3.connect(database.path) as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) FROM chat_messages WHERE role NOT IN ('player', 'narrator')"
                ).fetchone()[0],
                0,
            )

    async def test_rollback_rejects_invalid_targets(self) -> None:
        server, database, _, _ = await self.make_server()
        await self.submit_round(server, ROUND_1)

        cases = [
            ({"type": "rollback", "round": 0}, "at least 1"),
            ({"type": "rollback", "round": 99}, "does not exist"),
            ({"type": "rollback", "round": 2}, "not a finished round"),
            ({"type": "rollback", "round": "x"}, "integer"),
            ({"type": "rollback"}, "integer"),
            ({"type": "rollback", "round": True}, "integer"),
        ]
        for payload, expected in cases:
            self.ws.messages.clear()
            await self.send(server, 1, payload)
            self.assertIn(expected, self.ws.errors()[-1])

        self.assertEqual(server.rounds.round_number, 2)

        fresh, _, _, _ = await self.make_server()
        await self.send(fresh, 1, {"type": "rollback", "round": 1})
        self.assertIn("not a finished round", self.ws.errors()[-1])

    async def test_rollback_transaction_is_atomic(self) -> None:
        server, database, _, _ = await self.make_server(database_cls=ExplodingDatabase)
        for actions in (ROUND_1, ROUND_2, ROUND_3):
            await self.submit_round(server, actions)
        with sqlite3.connect(database.path) as connection:
            before = connection.execute(
                "SELECT round_number, status, stage, result_world_state FROM rounds ORDER BY round_number"
            ).fetchall()
            before_world = connection.execute(
                "SELECT content FROM world_state WHERE id = 1"
            ).fetchone()[0]

        with self.assertRaises(RuntimeError):
            await database.rollback_to_round(2)

        with sqlite3.connect(database.path) as connection:
            after = connection.execute(
                "SELECT round_number, status, stage, result_world_state FROM rounds ORDER BY round_number"
            ).fetchall()
            after_world = connection.execute(
                "SELECT content FROM world_state WHERE id = 1"
            ).fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(before_world, after_world)

    async def test_retry_transaction_is_atomic(self) -> None:
        server, database, _, _ = await self.make_server(database_cls=ExplodingDatabase)
        await self.submit_round(server, ROUND_1)
        with sqlite3.connect(database.path) as connection:
            before = connection.execute(
                "SELECT round_number, status, stage, result_world_state FROM rounds ORDER BY round_number"
            ).fetchall()
            before_world = connection.execute(
                "SELECT content FROM world_state WHERE id = 1"
            ).fetchone()[0]

        with self.assertRaises(RuntimeError):
            await database.prepare_retry_round(1)

        with sqlite3.connect(database.path) as connection:
            after = connection.execute(
                "SELECT round_number, status, stage, result_world_state FROM rounds ORDER BY round_number"
            ).fetchall()
            after_world = connection.execute(
                "SELECT content FROM world_state WHERE id = 1"
            ).fetchone()[0]
        self.assertEqual(before, after)
        self.assertEqual(before_world, after_world)

    # --- permissions ---------------------------------------------------------

    async def test_only_the_owner_can_manage_the_timeline(self) -> None:
        server, _, updater, _ = await self.make_server(owner_user_id=1)
        await self.submit_round(server, ROUND_1)

        non_owner = FakeWebSocket()
        for payload in (
            {"type": "retry"},
            {"type": "rollback", "round": 1},
            {"type": "retry", "is_host": True},
            {"type": "rollback", "round": 1, "is_host": True, "user_id": 1},
        ):
            non_owner.messages.clear()
            await self.send(server, 2, payload, ws=non_owner)
            self.assertIn("forbidden", non_owner.errors()[-1])
        self.assertEqual(len(updater.calls), 1)

        spectator = FakeWebSocket()
        await server.sessions.join(user(9, "Tom"), spectator)
        await self.send(server, 9, {"type": "retry"}, ws=spectator)
        self.assertIn("forbidden", spectator.errors()[-1])

        # The owner can manage the timeline while also playing a role.
        await self.send(server, 1, {"type": "retry"})
        self.assertEqual(len(updater.calls), 2)
        self.assertEqual(server.rounds.round_number, 2)

    async def test_owner_can_rollback_through_the_public_command_path(self) -> None:
        server, database, _, _ = await self.make_server()
        for actions in (ROUND_1, ROUND_2, ROUND_3):
            await self.submit_round(server, actions)
        with sqlite3.connect(database.path) as connection:
            second_world = connection.execute(
                "SELECT result_world_state FROM rounds WHERE round_number = 2"
            ).fetchone()[0]

        await self.send(server, 1, {"type": "rollback", "round": 2})

        self.assertEqual(server.rounds.round_number, 3)
        self.assertEqual(await database.get_world_state(), second_world)


if __name__ == "__main__":
    unittest.main()
