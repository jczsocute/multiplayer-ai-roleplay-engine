"""Regression tests for the Starlette <-> GameServer WebSocket boundary.

Two bugs were only reachable in a live server (uvicorn), not in the existing suite:

1. `POST /api/rooms` returned 500 because `load_game_server` called
   `RoleConfig.load(path, min, max)` while those parameters are keyword-only.
   Covered in ``tests/test_game_factory.py``.
2. Every normal client disconnect escaped as `WebSocketDisconnected` (a
   `RuntimeError`, *not* a `WebSocketDisconnect`) and was logged by uvicorn as
   "Exception in ASGI application". Covered here.
"""

import asyncio
import shutil
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect, WebSocketDisconnected, WebSocketState

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager
from server.platform.websocket_adapter import WebSocketConnection
from server.platform.web import create_platform_app


class RaisingWebSocket:
    """Minimal Starlette WebSocket stand-in whose recv/send always fail."""

    def __init__(self, exception: Exception) -> None:
        self.exception = exception
        self.application_state = WebSocketState.CONNECTED
        self.closed = False

    async def receive_text(self) -> str:
        raise self.exception

    async def send_text(self, payload: str) -> None:
        raise self.exception

    async def close(self, code: int = 1000) -> None:
        self.closed = True


def is_connection_closed(error: BaseException) -> bool:
    return type(error).__name__ == "ConnectionClosed"


class WebSocketAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_recv_translates_every_starlette_disconnect(self) -> None:
        for exception in (WebSocketDisconnect(1006), WebSocketDisconnected("closed")):
            connection = WebSocketConnection(RaisingWebSocket(exception))
            with self.assertRaises(Exception) as caught:
                await connection.recv()
            self.assertTrue(is_connection_closed(caught.exception))

    async def test_send_translates_a_dead_socket(self) -> None:
        connection = WebSocketConnection(
            RaisingWebSocket(WebSocketDisconnected("closed"))
        )
        with self.assertRaises(Exception) as caught:
            await connection.send("{}")
        self.assertTrue(is_connection_closed(caught.exception))

    async def test_first_message_is_replayed_once(self) -> None:
        connection = WebSocketConnection(
            RaisingWebSocket(WebSocketDisconnected("closed")), '{"type": "join"}'
        )
        self.assertEqual(await connection.recv(), '{"type": "join"}')
        with self.assertRaises(Exception) as caught:
            await connection.recv()
        self.assertTrue(is_connection_closed(caught.exception))

    async def test_iteration_surfaces_connection_closed_not_a_runtime_error(self) -> None:
        connection = WebSocketConnection(
            RaisingWebSocket(WebSocketDisconnected("closed"))
        )
        with self.assertRaises(Exception) as caught:
            async for _ in connection:
                self.fail("no message expected")
        self.assertTrue(is_connection_closed(caught.exception))


class PlatformDisconnectTests(unittest.TestCase):
    """A joined client that goes away must not raise inside the ASGI app."""

    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.templates_dir = self.root / "templates"
        self.games_dir = self.root / "games"
        self.database = PlatformDatabase(self.root / "platform.db")
        self.database.initialize()
        self.alice = self.database.create_user("Alice", "password123")
        self.bob = self.database.create_user("Bob", "password123")

        async def factory(path: Path, owner: int) -> GameServer:
            path.mkdir(parents=True, exist_ok=True)
            game_db = Database(path / "game.db")
            await game_db.initialize()
            return GameServer(
                game_db, None, None, None, room_key="", owner_user_id=owner
            )

        self.manager = RoomManager(
            self.database, self.games_dir, self.templates_dir, factory
        )

    def make_room(self):
        game = self.database.create_game("game_A", self.alice.id, "Save")
        return asyncio.run(self.manager.create_room_from_game(self.alice, game.id))

    def app(self):
        static = self.root / "static"
        static.mkdir(exist_ok=True)
        (static / "index.html").write_text("web", encoding="utf-8")
        return create_platform_app(self.database, static, room_manager=self.manager)

    def cookie(self, user_id: int) -> dict[str, str]:
        return {"cookie": f"rp_auth={self.database.create_session(user_id, 30)}"}

    def test_client_disconnect_keeps_the_seat_during_the_grace_period(self) -> None:
        room = self.make_room()
        app = self.app()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws?room={room.code}", headers=self.cookie(self.alice.id)
            ) as ws:
                ws.send_json({"type": "join", "password": ""})
                self.assertEqual(ws.receive_json()["type"], "joined")
                self.assertEqual(
                    self.manager.user_current_room(self.alice.id), room.code
                )
            # Leaving the context closes the socket: the app treats that as a normal
            # end of stream and the member stays inside the disconnect grace period.
            self.assertEqual(self.manager.user_current_room(self.alice.id), room.code)

        session = room.game_server.sessions.users.get(self.alice.id)
        self.assertIsNotNone(session)
        self.assertFalse(session.connected)

    def test_disconnect_before_the_join_frame_is_harmless(self) -> None:
        room = self.make_room()
        app = self.app()

        with TestClient(app) as client:
            with client.websocket_connect(
                f"/ws?room={room.code}", headers=self.cookie(self.bob.id)
            ):
                pass  # connect and leave immediately: no join message is sent

        self.assertIsNone(self.manager.user_current_room(self.bob.id))

    def test_same_account_reconnects_after_a_disconnect(self) -> None:
        room = self.make_room()
        headers = self.cookie(self.alice.id)
        app = self.app()

        with TestClient(app) as client:
            with client.websocket_connect(f"/ws?room={room.code}", headers=headers) as ws:
                ws.send_json({"type": "join", "password": ""})
                self.assertEqual(ws.receive_json()["type"], "joined")
            # Second connection for the same account (fresh tab) must be accepted.
            with client.websocket_connect(f"/ws?room={room.code}", headers=headers) as ws:
                ws.send_json({"type": "join", "password": ""})
                message = ws.receive_json()
                self.assertEqual(message["type"], "joined")
                self.assertEqual(message["user"]["username"], "Alice")
                self.assertTrue(message["is_host"])

    def test_unauthorized_socket_is_rejected_without_error(self) -> None:
        room = self.make_room()
        app = self.app()

        with TestClient(app) as client:
            with client.websocket_connect(f"/ws?room={room.code}") as ws:
                error = ws.receive_json()
                self.assertEqual(error["type"], "error")
                self.assertEqual(error["code"], "unauthorized")


class BootstrapPayloadTests(unittest.TestCase):
    """Bundled payloads are imported without runtime state."""

    def test_bootstrap_imports_a_payload_without_game_db(self) -> None:
        from server.platform.bootstrap import bootstrap_templates

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            templates_dir = root / "templates"
            games_dir = root / "games"
            shutil.copytree(Path("templates/default"), templates_dir / "love_story")
            database = PlatformDatabase(root / "platform.db")
            database.initialize()
            alice = database.create_user("Alice", "password123")

            results = bootstrap_templates(
                database, templates_dir, games_dir, alice.id, ("love_story",),
            )
            self.assertEqual(results[0].status, "imported")
            template = database.get_template(results[0].template_id)
            self.assertTrue(template.is_public)
            payload = templates_dir / template.id
            self.assertTrue((payload / "metadata.json").is_file())
            self.assertFalse((payload / "game.db").exists())


if __name__ == "__main__":
    unittest.main()
