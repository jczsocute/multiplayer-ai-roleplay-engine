"""Room lifecycle: 10-seat capacity, disconnect timeout, kick and touch wiring."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.gameserver.roles import RoleConfig
from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager
from tests.support import user

# Deliberately short so the disconnect timeout is exercised without sleeping 300s.
SHORT_TIMEOUT = 0.1


class FakeConnection:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.closed = False

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))

    async def close(self, code: int = 1000) -> None:
        self.closed = True


class RoomLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.templates_dir = self.root / "templates"
        self.games_dir = self.root / "games"
        self.database = PlatformDatabase(self.root / "platform.db")
        self.database.initialize()
        self.owner = self.database.create_user("Alice", "password123")
        self.guest = self.database.create_user("Bob", "password123")
        self.game = self.database.create_game("game_A", self.owner.id, "Save")
        (self.games_dir / "game_A").mkdir(parents=True, exist_ok=True)
        self.touched: list[str] = []
        self.manager = RoomManager(
            self.database, self.games_dir, self.templates_dir, self.factory,
            max_users=10,
        )
        self.room = await self.manager.create_room_from_game(self.owner, self.game.id)

    async def factory(self, path: Path, owner_user_id: int) -> GameServer:
        path.mkdir(parents=True, exist_ok=True)
        database = Database(path / "game.db", ("P1",))
        await database.initialize()
        server = GameServer(
            database, None, None,
            round_number=await database.current_round(),
            scenario_name=path.name, room_key="", owner_user_id=owner_user_id,
            role_config=RoleConfig(("P1",), {"P1": "独奏"}),
            disconnect_grace_seconds=SHORT_TIMEOUT, max_users=10,
        )
        return server

    async def connect(self, account, socket: FakeConnection | None = None):
        socket = socket or FakeConnection()
        await self.room.game_server.sessions.join(account, socket)
        return socket

    async def seat(self, account) -> None:
        """Occupy a seat without a live socket (as an accepted join would)."""
        await self.manager.enter(account.id, self.room.code, object())

    # --- capacity -----------------------------------------------------------

    async def test_room_accepts_exactly_ten_seats(self) -> None:
        for index in range(1, 10):
            await self.seat(user(100 + index, f"U{index}"))
        self.assertEqual(self.manager.occupancy(self.room.code), 9)
        await self.seat(user(10, "U10"))
        self.assertEqual(self.manager.occupancy(self.room.code), 10)
        self.assertEqual(self.manager.max_users, 10)

    async def test_eleventh_new_user_is_rejected_with_room_full(self) -> None:
        for index in range(1, 11):
            await self.seat(user(100 + index, f"U{index}"))
        with self.assertRaisesRegex(ValueError, "room_full"):
            await self.seat(user(111, "U11"))
        self.assertEqual(self.manager.occupancy(self.room.code), 10)

    async def test_a_seat_inside_grace_still_counts(self) -> None:
        for index in range(1, 10):
            await self.seat(user(100 + index, f"U{index}"))
        socket = FakeConnection()
        await self.connect(user(10, "U10"), socket)
        await self.manager.enter(10, self.room.code, socket)
        await self.room.game_server.sessions.mark_disconnected(10, socket)

        # Disconnected but inside the grace period: the seat is reserved.
        self.assertEqual(self.manager.occupancy(self.room.code), 10)
        self.assertEqual(self.manager.user_current_room(10), self.room.code)
        with self.assertRaisesRegex(ValueError, "room_full"):
            await self.seat(user(111, "U11"))

    async def test_reconnecting_the_same_user_does_not_add_a_seat(self) -> None:
        for index in range(1, 11):
            await self.seat(user(100 + index, f"U{index}"))
        await self.seat(user(105, "U5"))  # same account re-enters its own Room
        self.assertEqual(self.manager.occupancy(self.room.code), 10)

    async def test_explicit_leave_releases_membership_session_and_role(self) -> None:
        socket = await self.connect(self.guest)
        await self.manager.enter(self.guest.id, self.room.code, socket)
        await self.room.game_server.sessions.assign_roles({"P1": self.guest.id})

        await self.manager.leave_member(self.guest.id, self.room.code)

        self.assertIsNone(self.manager.user_current_room(self.guest.id))
        self.assertNotIn(self.guest.id, self.room.game_server.sessions.users)
        self.assertEqual(await self.room.game_server.sessions.role_assignments(), {})
        self.assertTrue(socket.closed)
        self.assertFalse(any(message["type"] == "kicked" for message in socket.messages))

    async def test_legacy_leave_command_also_releases_platform_callback(self) -> None:
        socket = await self.connect(self.guest)
        await self.manager.enter(self.guest.id, self.room.code, socket)
        await self.room.game_server._handle_command(
            self.guest.id, socket, json.dumps({"type": "leave"})
        )
        self.assertIsNone(self.manager.user_current_room(self.guest.id))
        self.assertNotIn(self.guest.id, self.room.game_server.sessions.users)

    async def test_kick_and_timeout_release_the_seat(self) -> None:
        for index in range(1, 11):
            await self.seat(user(100 + index, f"U{index}"))
        # Kick one member: the seat must become available again.
        await self.room.game_server.sessions.join(user(101, "U1"), FakeConnection())
        await self.room.game_server.evict_user(101, reason="test")
        self.assertEqual(self.manager.occupancy(self.room.code), 9)
        await self.seat(user(111, "U11"))
        self.assertEqual(self.manager.occupancy(self.room.code), 10)

    # --- disconnect timeout -------------------------------------------------

    async def test_reconnect_inside_the_timeout_keeps_membership(self) -> None:
        socket = FakeConnection()
        await self.connect(self.guest, socket)
        await self.manager.enter(self.guest.id, self.room.code, socket)
        await self.room.game_server.sessions.mark_disconnected(self.guest.id, socket)

        await asyncio.sleep(SHORT_TIMEOUT / 4)
        rejoined = FakeConnection()
        await self.room.game_server.sessions.join(self.guest, rejoined)
        await asyncio.sleep(SHORT_TIMEOUT * 2)

        self.assertEqual(self.manager.user_current_room(self.guest.id), self.room.code)
        self.assertIn(self.guest.id, self.room.game_server.sessions.users)
        self.assertTrue(self.room.game_server.sessions.users[self.guest.id].connected)

    async def test_ordinary_user_times_out_and_really_leaves(self) -> None:
        socket = FakeConnection()
        await self.connect(self.guest, socket)
        await self.manager.enter(self.guest.id, self.room.code, socket)
        self.room.game_server.rounds.players["P1"].action = "draft"
        await self.room.game_server.sessions.assign_roles({"P1": self.guest.id})
        token = self.database.create_session(self.guest.id, 30)
        await self.room.game_server.sessions.mark_disconnected(self.guest.id, socket)

        await asyncio.sleep(SHORT_TIMEOUT * 3)

        # Membership, participant, role and seat are all released.
        self.assertIsNone(self.manager.user_current_room(self.guest.id))
        self.assertNotIn(self.guest.id, self.room.game_server.sessions.users)
        self.assertEqual(await self.room.game_server.sessions.role_for(self.guest.id), None)
        self.assertEqual(self.manager.occupancy(self.room.code), 0)
        # The Room survives and the *account* is untouched: a Room disconnect is
        # not a logout, so the auth session still resolves.
        self.assertIsNotNone(self.manager.get_runtime(self.room.code))
        self.assertIsNotNone(self.database.user_by_id(self.guest.id))
        self.assertIsNotNone(self.database.get_room(self.room.code))
        self.assertIsNotNone(self.database.resolve_session(token))

    async def test_owner_timeout_closes_the_room_and_keeps_the_save(self) -> None:
        owner_socket = FakeConnection()
        await self.connect(self.owner, owner_socket)
        await self.manager.enter(self.owner.id, self.room.code, owner_socket)
        guest_socket = FakeConnection()
        await self.connect(self.guest, guest_socket)
        await self.manager.enter(self.guest.id, self.room.code, guest_socket)

        await self.room.game_server.sessions.mark_disconnected(self.owner.id, owner_socket)
        await asyncio.sleep(SHORT_TIMEOUT * 3)

        self.assertIsNone(self.manager.get_runtime(self.room.code))
        self.assertIsNone(self.database.get_room(self.room.code))
        self.assertEqual(self.manager.user_room, {})
        self.assertTrue(guest_socket.closed)
        self.assertEqual(guest_socket.messages[-1]["type"], "room_closed")
        # The Game Save survives exactly like a manual close.
        self.assertIsNotNone(self.database.get_game(self.game.id))
        self.assertTrue((self.games_dir / self.game.id / "game.db").is_file())

    async def test_room_without_owner_timeout_stays_open(self) -> None:
        socket = FakeConnection()
        await self.connect(self.guest, socket)
        await self.manager.enter(self.guest.id, self.room.code, socket)
        await self.room.game_server.sessions.mark_disconnected(self.guest.id, socket)
        await asyncio.sleep(SHORT_TIMEOUT * 3)
        self.assertIsNotNone(self.manager.get_runtime(self.room.code))

    # --- kick ---------------------------------------------------------------

    async def kick(self, actor_id: int, target_id: int, socket=None) -> None:
        await self.room.game_server._handle_command(
            actor_id, socket or FakeConnection(), json.dumps(
                {"type": "kick_user", "user_id": target_id}
            )
        )

    async def test_owner_kicks_a_participant(self) -> None:
        owner_socket = FakeConnection()
        await self.connect(self.owner, owner_socket)
        await self.manager.enter(self.owner.id, self.room.code, owner_socket)
        guest_socket = FakeConnection()
        await self.connect(self.guest, guest_socket)
        await self.manager.enter(self.guest.id, self.room.code, guest_socket)
        await self.room.game_server.sessions.assign_roles({"P1": self.guest.id})

        await self.kick(self.owner.id, self.guest.id, owner_socket)

        self.assertEqual(guest_socket.messages[-1]["type"], "kicked")
        self.assertIn("房主", guest_socket.messages[-1]["reason"])
        self.assertTrue(guest_socket.closed)
        self.assertNotIn(self.guest.id, self.room.game_server.sessions.users)
        self.assertIsNone(self.manager.user_current_room(self.guest.id))
        self.assertIsNone(await self.room.game_server.sessions.role_for(self.guest.id))
        # Only the owner's seat is left.
        self.assertEqual(self.manager.occupancy(self.room.code), 1)
        # The player slot is editable again for the next assignment.
        self.assertEqual(self.room.game_server.rounds.players["P1"].action, "")

    async def test_kicked_user_can_join_the_same_room_again(self) -> None:
        await self.connect(self.guest, FakeConnection())
        await self.kick(self.owner.id, self.guest.id)
        # Kick is not a ban: a fresh join is accepted.
        await self.manager.enter(self.guest.id, self.room.code, object())
        self.assertEqual(self.manager.user_current_room(self.guest.id), self.room.code)

    async def test_non_owner_cannot_kick(self) -> None:
        await self.connect(self.owner, FakeConnection())
        guest_socket = FakeConnection()
        await self.connect(self.guest, guest_socket)
        await self.kick(self.guest.id, self.owner.id)
        self.assertIn(self.owner.id, self.room.game_server.sessions.users)

    async def test_owner_cannot_kick_itself(self) -> None:
        socket = FakeConnection()
        await self.connect(self.owner, socket)
        await self.kick(self.owner.id, self.owner.id)
        self.assertIn(self.owner.id, self.room.game_server.sessions.users)
        self.assertFalse(socket.closed)

    async def test_kicking_an_absent_user_is_reported(self) -> None:
        socket = FakeConnection()
        await self.connect(self.owner, socket)
        await self.kick(self.owner.id, 99999, socket)
        self.assertEqual(socket.messages[-1]["type"], "error")
        self.assertIn("user_not_in_room", socket.messages[-1]["detail"])

    # --- game metadata touch ------------------------------------------------

    async def test_round_completion_touches_the_game(self) -> None:
        self.room.game_server.on_game_changed = lambda: self.record_touch(self.game.id)
        await self.room.game_server._touch_game()
        self.assertEqual(self.touched, [self.game.id])

    async def record_touch(self, game_id: str) -> None:
        self.touched.append(game_id)


if __name__ == "__main__":
    unittest.main()


class StubWorldUpdater:
    async def update(self, current_world_state: str, actions: dict[str, str]) -> dict:
        return {
            "world_state": {"actions": actions},
            "character_views": {role: {"role": role} for role in actions},
            "character_status": {role: {"ready": True} for role in actions},
        }


class StubNarrator:
    async def narrate(self, role, view, status, history):
        return {"text": f"narration for {role}", "status": {}}


class GameTouchTests(unittest.IsolatedAsyncioTestCase):
    """`games.updated_at` must follow real content changes."""

    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.games_dir = self.root / "games"
        self.database = PlatformDatabase(self.root / "platform.db")
        self.database.initialize()
        self.owner = self.database.create_user("Alice", "password123")
        self.game = self.database.create_game("game_A", self.owner.id, "Save")
        self.manager = RoomManager(
            self.database, self.games_dir, self.root / "templates", self.factory
        )
        self.room = await self.manager.create_room_from_game(self.owner, self.game.id)
        self.socket = FakeConnection()
        await self.room.game_server.sessions.join(self.owner, self.socket)
        await self.room.game_server.sessions.assign_roles({"P1": self.owner.id})

    async def factory(self, path: Path, owner_user_id: int) -> GameServer:
        path.mkdir(parents=True, exist_ok=True)
        database = Database(path / "game.db", ("P1",))
        await database.initialize()
        return GameServer(
            database, StubWorldUpdater(), StubNarrator(),
            round_number=await database.current_round(),
            scenario_name=path.name, room_key="", owner_user_id=owner_user_id,
            role_config=RoleConfig(("P1",), {"P1": "独奏"}), max_users=10,
        )

    def command(self, message: dict) -> None:
        return self.room.game_server._handle_command(
            self.owner.id, self.socket, json.dumps(message)
        )

    async def test_room_manager_wires_both_activity_callbacks(self) -> None:
        self.assertIsNotNone(self.room.game_server.on_member_left)
        self.assertIsNotNone(self.room.game_server.on_game_changed)
        await self.manager.enter(self.owner.id, self.room.code, self.socket)
        self.assertEqual(self.manager.occupancy(self.room.code), 1)
        await self.room.game_server.on_member_left(self.owner.id)
        # A departed owner times out into a Room close, seats included.
        self.assertIsNone(self.manager.get_runtime(self.room.code))

    async def test_completed_round_touches_the_game(self) -> None:
        before = self.database.get_game(self.game.id).updated_at
        await self.command({"type": "action", "text": "开门"})
        await self.command({"type": "submit"})
        after = self.database.get_game(self.game.id).updated_at
        self.assertGreater(after, before)
        self.assertEqual(self.room.game_server.rounds.round_number, 2)

    async def test_retry_and_rollback_touch_the_game(self) -> None:
        await self.command({"type": "action", "text": "开门"})
        await self.command({"type": "submit"})
        await self.command({"type": "action", "text": "前进"})
        await self.command({"type": "submit"})

        stamps = [self.database.get_game(self.game.id).updated_at]
        await self.room.game_server.retry_round(2)
        stamps.append(self.database.get_game(self.game.id).updated_at)
        await self.room.game_server.rollback_to_round(1)
        stamps.append(self.database.get_game(self.game.id).updated_at)

        self.assertEqual(stamps, sorted(stamps))
        self.assertGreater(stamps[-1], stamps[0])

    async def test_kicked_participant_is_touched_only_through_the_room(self) -> None:
        # GameServer itself must not know about the platform database.
        self.assertFalse(hasattr(self.room.game_server, "game_id"))
        self.assertFalse(hasattr(self.room.game_server, "room_code"))
