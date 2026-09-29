import asyncio
import json
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path
from starlette.testclient import TestClient

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.platform.catalog import import_template
from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager, validate_room_password
from server.platform.web import create_platform_app
from tests.support import user


class FakeConnection:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.closed = False

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))

    async def close(self, code: int = 1000) -> None:
        self.closed = True


class RoomWorldUpdater:
    async def update(self, current_world_state: str, actions: dict[str, str]) -> dict:
        return {
            "world_state": {"actions": actions},
            "character_views": {role: {"role": role} for role in actions},
            "character_status": {role: {"ready": True} for role in actions},
        }


class RoomNarrator:
    async def narrate(self, role, view, status, history):
        return {"text": f"narration for {role}", "status": {}}


class MultiRoomTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addAsyncCleanup(self._cleanup)
        self.root = Path(self.temp.name)
        self.platform = PlatformDatabase(self.root / "platform.db")
        self.platform.initialize()
        self.alice = self.platform.create_user("Alice", "password123")
        self.bob = self.platform.create_user("Bob", "password123")
        self.game_a = self.platform.create_game("game_A", self.alice.id, "Game A")
        self.game_b = self.platform.create_game("game_B", self.bob.id, "Game B")
        self.manager = RoomManager(
            self.platform, self.root / "games", self.root / "templates", self.factory
        )

    async def _cleanup(self) -> None:
        self.temp.cleanup()

    async def factory(self, path: Path, owner: int) -> GameServer:
        path.mkdir(parents=True, exist_ok=True)
        database = Database(path / "game.db")
        await database.initialize({"game": path.name})
        return GameServer(
            database, RoomWorldUpdater(), RoomNarrator(),
            round_number=await database.current_round(),
            scenario_name=path.name, room_key="", owner_user_id=owner,
        )

    async def _complete_round(
        self, server: GameServer, first: int, second: int, suffix: str
    ) -> None:
        for user_id, text in ((first, f"P1-{suffix}"), (second, f"P2-{suffix}")):
            socket = server.sessions.users[user_id].websocket
            await server._handle_command(
                user_id, socket, json.dumps({"type": "action", "text": text})
            )
            await server._handle_command(
                user_id, socket, json.dumps({"type": "submit"})
            )

    async def test_two_rooms_have_isolated_state_sessions_and_locks(self) -> None:
        room_a = await self.manager.create_room_from_game(self.alice, self.game_a.id)
        room_b = await self.manager.create_room_from_game(self.bob, self.game_b.id)
        self.assertEqual(room_a.game_server.scenario_name, "Game A_A")
        self.assertEqual((await self.manager.public_room(room_a.code))["display_name"], "Game A_A")
        room_a.game_server.rounds.set_action("P1", "A action")
        await room_a.game_server.sessions.join(user(3, "AUser"), FakeConnection())

        self.assertEqual(room_b.game_server.rounds.players["P1"].action, "")
        self.assertEqual(room_b.game_server.sessions.users, {})
        self.assertIsNot(room_a.game_server.database, room_b.game_server.database)
        self.assertIsNot(room_a.game_server.command_lock, room_b.game_server.command_lock)
        await room_a.game_server.command_lock.acquire()
        try:
            await asyncio.wait_for(room_b.game_server.command_lock.acquire(), 0.1)
            room_b.game_server.command_lock.release()
        finally:
            room_a.game_server.command_lock.release()

    async def test_round_retry_and_rollback_in_room_a_do_not_touch_room_b(self) -> None:
        room_a = await self.manager.create_room_from_game(self.alice, self.game_a.id)
        room_b = await self.manager.create_room_from_game(self.bob, self.game_b.id)
        alice_peer = user(3, "AlicePeer")
        bob_peer = user(4, "BobPeer")
        for server, owner, peer in (
            (room_a.game_server, self.alice, alice_peer),
            (room_b.game_server, self.bob, bob_peer),
        ):
            await server.sessions.join(owner, FakeConnection())
            await server.sessions.join(peer, FakeConnection())
            await server.sessions.assign_roles({"P1": owner.id, "P2": peer.id})

        room_b_world = await room_b.game_server.database.get_world_state()
        await self._complete_round(
            room_a.game_server, self.alice.id, alice_peer.id, "one"
        )
        await room_a.game_server.retry_round()
        await self._complete_round(
            room_a.game_server, self.alice.id, alice_peer.id, "two"
        )
        await room_a.game_server.rollback_to_round(1)

        self.assertEqual(room_a.game_server.rounds.round_number, 2)
        self.assertEqual(room_b.game_server.rounds.round_number, 1)
        self.assertEqual(await room_b.game_server.database.get_world_state(), room_b_world)
        self.assertEqual(await room_b.game_server.database.get_role_history("P1"), [])
        self.assertEqual(
            await room_b.game_server.sessions.role_assignments(),
            {"P1": self.bob.id, "P2": bob_peer.id},
        )

    async def test_one_user_one_room_but_same_room_reconnect_allowed(self) -> None:
        first = await self.manager.create_room_from_game(self.alice, self.game_a.id)
        second = await self.manager.create_room_from_game(self.bob, self.game_b.id)
        old, new = object(), object()
        await self.manager.enter(7, first.code, old)
        await self.manager.enter(7, first.code, new)
        await self.manager.leave(7, first.code, old)
        self.assertEqual(self.manager.user_current_room(7), first.code)
        with self.assertRaisesRegex(ValueError, "already_in_room"):
            await self.manager.enter(7, second.code, object())
        await self.manager.leave(7, first.code, new)
        await self.manager.enter(7, second.code, object())
        self.assertEqual(self.manager.user_current_room(7), second.code)

    async def test_owner_and_game_unique_then_reusable_after_close(self) -> None:
        room = await self.manager.create_room_from_game(self.alice, self.game_a.id)
        other = self.platform.create_game("game_C", self.alice.id, "Game C")
        with self.assertRaisesRegex(ValueError, "owner_already_has_room"):
            await self.manager.create_room_from_game(self.alice, other.id)
        with self.assertRaisesRegex(PermissionError, "forbidden"):
            await self.manager.create_room_from_game(self.bob, self.game_a.id)
        await self.manager.close_room(room.code, self.alice.id)
        reopened = await self.manager.create_room_from_game(self.alice, self.game_a.id)
        self.assertNotEqual(reopened.code, room.code)

    async def test_template_room_failure_removes_only_new_snapshot(self) -> None:
        template = import_template(
            self.platform, Path("templates/default"), self.root / "templates",
            self.alice.id, "Source", is_public=True,
        )
        old_game = self.game_a.id
        async def failing_factory(_path: Path, _owner: int) -> GameServer:
            raise RuntimeError("runtime failed")
        self.manager.game_factory = failing_factory
        with self.assertRaisesRegex(RuntimeError, "runtime failed"):
            await self.manager.create_room_from_template(self.alice, template.id, "New save")
        self.assertEqual(
            {game.id for game in self.platform.list_user_games(self.alice.id)},
            {old_game},
        )
        self.assertTrue((self.root / "templates" / template.id).is_dir())
        self.assertIsNotNone(self.platform.get_game(old_game))
        self.assertEqual(self.platform.list_rooms(), [])

    async def test_template_room_preflight_leaves_no_snapshot(self) -> None:
        template = import_template(
            self.platform, Path("templates/default"), self.root / "templates",
            self.alice.id, "Source",
        )
        self.manager.min_role_count = 3
        with self.assertRaises(ValueError):
            await self.manager.create_room_from_template(self.alice, template.id, "Invalid")
        self.assertEqual(
            {game.id for game in self.platform.list_user_games(self.alice.id)},
            {self.game_a.id},
        )
        self.manager.min_role_count = 2
        await self.manager.create_room_from_game(self.alice, self.game_a.id)
        with self.assertRaisesRegex(ValueError, "owner_already_has_room"):
            await self.manager.create_room_from_template(self.alice, template.id, "Invalid")
        self.assertEqual(
            {game.id for game in self.platform.list_user_games(self.alice.id)},
            {self.game_a.id},
        )

    async def test_recovery_failed_room_is_admin_visible_and_closable(self) -> None:
        self.platform.create_room_metadata("GHOST1", self.alice.id, self.game_a.id, None, None)
        detail = await self.manager.admin_room("GHOST1")
        self.assertEqual(detail["runtime_status"], "RECOVERY_FAILED")
        self.assertEqual(detail["game_id"], self.game_a.id)
        self.assertEqual((await self.manager.list_admin_rooms())[0]["code"], "GHOST1")
        self.assertEqual(await self.manager.list_public_rooms(), [])
        await self.manager.close_room("GHOST1", admin=True)
        self.assertIsNone(self.platform.get_room("GHOST1"))
        self.assertIsNotNone(self.platform.get_game(self.game_a.id))

    async def test_password_validation_and_hashing(self) -> None:
        room = await self.manager.create_room_from_game(
            self.alice, self.game_a.id, "abc_123"
        )
        self.manager.verify_password(room.code, "abc_123")
        with self.assertRaisesRegex(ValueError, "required"):
            self.manager.verify_password(room.code, "")
        with self.assertRaisesRegex(ValueError, "invalid_room_password$"):
            self.manager.verify_password(room.code, "wrong")
        for invalid in ("space bad", "x" * 33, "中文"):
            with self.assertRaisesRegex(ValueError, "format"):
                validate_room_password(invalid)
        with sqlite3.connect(self.platform.path) as connection:
            stored, salt = connection.execute(
                "SELECT password_hash, password_salt FROM rooms WHERE code = ?",
                (room.code,),
            ).fetchone()
        self.assertNotEqual(stored, b"abc_123")
        self.assertNotIn(b"abc_123", bytes(stored))
        self.assertTrue(salt)

    async def test_no_password_and_close_preserves_game(self) -> None:
        room = await self.manager.create_room_from_game(self.alice, self.game_a.id)
        self.manager.verify_password(room.code, "")
        socket = FakeConnection()
        await room.game_server.sessions.join(self.alice, socket)
        await self.manager.enter(self.alice.id, room.code, socket)
        await self.manager.close_room(room.code, self.alice.id)
        self.assertIsNone(self.manager.get_runtime(room.code))
        self.assertIsNone(self.platform.get_room(room.code))
        self.assertIsNotNone(self.platform.get_game(self.game_a.id))
        self.assertTrue((self.root / "games" / self.game_a.id / "game.db").is_file())
        self.assertTrue(socket.closed)
        self.assertEqual(socket.messages[-1]["type"], "room_closed")

    async def test_restart_loads_active_room_with_platform_owner(self) -> None:
        room = await self.manager.create_room_from_game(self.alice, self.game_a.id)
        restarted = RoomManager(
            self.platform, self.root / "games", self.root / "templates", self.factory
        )
        await restarted.load_active_rooms()
        loaded = restarted.get_runtime(room.code)
        self.assertIsNotNone(loaded)
        self.assertEqual(loaded.game_id, self.game_a.id)
        self.assertEqual(loaded.game_server.owner_user_id, self.alice.id)
        self.assertEqual(restarted.user_room, {})

    async def test_public_template_creates_owned_snapshot_private_is_forbidden(self) -> None:
        templates = self.root / "templates"
        public_path = templates / "tmpl_PUBLIC"
        private_path = templates / "tmpl_PRIVATE"
        shutil.copytree(Path("templates/default"), public_path)
        shutil.copytree(Path("templates/default"), private_path)
        public = self.platform.create_template(
            "tmpl_PUBLIC", self.alice.id, "Public", True
        )
        private = self.platform.create_template(
            "tmpl_PRIVATE", self.alice.id, "Private", False
        )
        room = await self.manager.create_room_from_template(
            self.bob, public.id, "Bob Save", ""
        )
        game = self.platform.get_game(room.game_id)
        self.assertEqual(game.owner_user_id, self.bob.id)
        snapshot = self.root / "games" / game.id / "metadata.json"
        original = snapshot.read_text(encoding="utf-8")
        (public_path / "metadata.json").write_text("changed", encoding="utf-8")
        self.assertEqual(snapshot.read_text(encoding="utf-8"), original)
        with self.assertRaises(PermissionError):
            await self.manager.create_room_from_template(
                self.bob, private.id, "Forbidden", ""
            )

    async def test_template_room_defaults_to_template_name(self) -> None:
        template = import_template(
            self.platform, Path("templates/default"), self.root / "templates",
            self.alice.id, "石头剪刀布",
        )
        room = await self.manager.create_room_from_template(self.alice, template.id, "")
        game = self.platform.get_game(room.game_id)
        self.assertEqual(game.name, "石头剪刀布")
        self.assertEqual(
            room.game_server.scenario_name,
            f"石头剪刀布_{room.game_id.removeprefix('game_')}",
        )

    async def test_owner_web_command_assigns_roles_by_user_id(self) -> None:
        room = await self.manager.create_room_from_game(self.alice, self.game_a.id)
        owner_socket, bob_socket = FakeConnection(), FakeConnection()
        await room.game_server.sessions.join(self.alice, owner_socket)
        await room.game_server.sessions.join(self.bob, bob_socket)
        await room.game_server._handle_command(
            self.alice.id, owner_socket,
            json.dumps({"type": "assign_roles", "assignments": {
                "P1": self.alice.id, "P2": self.bob.id,
            }}),
        )
        self.assertEqual(
            await room.game_server.sessions.role_assignments(),
            {"P1": self.alice.id, "P2": self.bob.id},
        )


class PlatformRoomTransportTests(unittest.TestCase):
    def test_room_api_and_websocket_password_routing(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = PlatformDatabase(root / "platform.db")
            database.initialize()
            alice = database.create_user("Alice", "password123")
            bob = database.create_user("Bob", "password123")
            game = database.create_game("game_A", alice.id, "Game A")
            bob_game = database.create_game("game_B", bob.id, "Game B")

            async def factory(path: Path, owner: int) -> GameServer:
                path.mkdir(parents=True, exist_ok=True)
                game_db = Database(path / "game.db")
                await game_db.initialize()
                return GameServer(game_db, None, None, room_key="", owner_user_id=owner)

            manager = RoomManager(database, root / "games", root / "templates", factory)
            room = asyncio.run(manager.create_room_from_game(alice, game.id, "abc_123"))
            token = database.create_session(alice.id, 30)
            bob_token = database.create_session(bob.id, 30)
            static = root / "static"; static.mkdir()
            (static / "index.html").write_text("web", encoding="utf-8")
            app = create_platform_app(database, static, room_manager=manager)
            headers = {"cookie": f"rp_auth={token}"}
            with TestClient(app) as client:
                listing = client.get("/api/rooms", headers=headers).json()["rooms"]
                self.assertEqual(listing[0]["code"], room.code)
                self.assertTrue(listing[0]["has_password"])
                with client.websocket_connect(f"/ws?room={room.code}", headers=headers) as ws:
                    ws.send_json({"type": "join", "password": "abc_123"})
                    self.assertEqual(ws.receive_json()["type"], "joined")
                with client.websocket_connect(f"/ws?room={room.code}", headers=headers) as ws:
                    ws.send_json({"type": "join", "password": "wrong"})
                    self.assertEqual(ws.receive_json()["type"], "joined")
                # A first-time member still needs the Room Password.
                with client.websocket_connect(
                    f"/ws?room={room.code}",
                    headers={"cookie": f"rp_auth={bob_token}"},
                ) as ws:
                    ws.send_json({"type": "join", "password": "wrong"})
                    self.assertEqual(ws.receive_json()["code"], "invalid_room_password")
                created = client.post(
                    "/api/rooms",
                    headers={"cookie": f"rp_auth={bob_token}"},
                    json={"source": "game", "game_id": bob_game.id, "password": ""},
                )
                self.assertEqual(created.status_code, 201)
                created_code = created.json()["code"]
                self.assertIsNotNone(manager.get_runtime(created_code))
                closed = client.delete(
                    f"/api/rooms/{created_code}",
                    headers={"cookie": f"rp_auth={bob_token}"},
                )
                self.assertEqual(closed.status_code, 200)
                self.assertIsNotNone(database.get_game(bob_game.id))

if __name__ == "__main__":
    unittest.main()
