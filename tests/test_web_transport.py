import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from server.platform.auth import AUTH_COOKIE_NAME
from server.platform.database import PlatformDatabase
from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.gameserver.protocol import MAX_ACTION_LENGTH, PROTOCOL_VERSION
from server.gameserver.roles import RoleConfig
from server.platform.legacy_game_web import create_web_app
from server.platform.security import origin_allowed
from tests.support import user


class FakeUpdater:
    async def update(self, **_kwargs):
        return {
            "world_state": {"place": "hall"},
            "character_views": {"P1": {"seen": "door"}, "P2": {"seen": "window"}},
            "character_status": {"P1": {"hp": 10}, "P2": {"hp": 20}},
        }


class FakeNarrator:
    async def narrate(self, player_id, *_args):
        return {"text": f"narration {player_id}", "status": {}}


class DynamicUpdater:
    async def update(self, current_world_state, actions):
        return {
            "world_state": {"place": "hall"},
            "character_views": {role: {"seen": role} for role in actions},
            "character_status": {role: {"hp": 10} for role in actions},
        }


class SequenceConnection:
    def __init__(self, first_message: dict) -> None:
        self.incoming = [json.dumps(first_message)]
        self.messages: list[dict] = []
        self.closed = False

    async def recv(self) -> str:
        return self.incoming.pop(0)

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))

    async def close(self, **_kwargs) -> None:
        self.closed = True

    def __aiter__(self):
        return self

    async def __anext__(self):
        raise StopAsyncIteration


class WebTransportTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tempdir = tempfile.TemporaryDirectory()
        database = Database(str(Path(self.tempdir.name) / "game.db"))
        asyncio.run(database.initialize({}, {"P1": {"hp": 10}, "P2": {"hp": 20}}))
        self.game = GameServer(
            database, FakeUpdater(), FakeNarrator(), scenario_name="test"
        )
        self.accounts = PlatformDatabase(str(Path(self.tempdir.name) / "platform.db"))
        self.accounts.initialize()
        static = Path(self.tempdir.name) / "static"
        static.mkdir()
        (static / "index.html").write_text("web client", encoding="utf-8")
        self.app = create_web_app(self.game, self.accounts, static)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def cookie(self, username: str) -> dict:
        token = self.accounts.create_session(
            self.accounts.create_user(username, "password123").id, 30
        )
        return {"cookie": f"{AUTH_COOKIE_NAME}={token}"}

    def test_public_websocket_join_protocol_and_spectator_view(self) -> None:
        headers = self.cookie("Browser")
        with TestClient(self.app) as client, client.websocket_connect(
            "/ws", headers=headers
        ) as websocket:
            websocket.send_json({"type": "join", "room_key": "test-key"})
            joined = websocket.receive_json()
            self.assertEqual(joined["type"], "joined")
            self.assertEqual(joined["protocol_version"], PROTOCOL_VERSION)
            self.assertEqual(joined["user"], {"id": 1, "username": "Browser"})
            self.assertFalse(joined["is_host"])
            self.assertEqual([role["id"] for role in joined["roles"]], ["P1", "P2"])
            self.assertIn("resume_token", joined)
            self.assertEqual(websocket.receive_json()["type"], "presence")
            self.assertEqual(websocket.receive_json()["type"], "state")
            self.assertEqual(websocket.receive_json()["type"], "room_message")
            notice = websocket.receive_json()
            self.assertEqual(notice["type"], "room_message")
            self.assertEqual(notice["kind"], "system")
            self.assertEqual(
                notice["text"], "您目前身份为 <观众>。请等待房主分配角色。"
            )

            websocket.send_json({"type": "view", "role": "P1"})
            role_view = websocket.receive_json()
            self.assertEqual(role_view["type"], "role_view")
            self.assertNotIn("draft", role_view)

            websocket.send_json({"type": "room_chat", "text": "hello"})
            room_message = websocket.receive_json()
            self.assertEqual(room_message["kind"], "spectator")

    def test_public_websocket_rejects_join_host(self) -> None:
        headers = self.cookie("Browser")
        with TestClient(self.app) as client, client.websocket_connect(
            "/ws", headers=headers
        ) as websocket:
            websocket.send_json({"type": "join_host"})
            error = websocket.receive_json()
            self.assertEqual(error["type"], "error")
            self.assertIn("不接受房主连接", error["detail"])

    def test_unauthenticated_websocket_is_rejected(self) -> None:
        with TestClient(self.app) as client, client.websocket_connect("/ws") as websocket:
            message = websocket.receive_json()
            self.assertEqual(message["type"], "error")
            self.assertEqual(message["code"], "unauthorized")
        self.assertEqual(self.game.sessions.users, {})

    def test_websocket_origin_is_checked_for_browsers(self) -> None:
        app = create_web_app(
            self.game,
            self.accounts,
            Path(self.tempdir.name) / "static",
            allowed_origins=("https://play.example.com",),
        )
        headers = self.cookie("Browser")

        with TestClient(app) as client, client.websocket_connect(
            "/ws", headers={**headers, "origin": "https://evil.example.com"}
        ) as websocket:
            message = websocket.receive_json()
            self.assertEqual(message["code"], "forbidden_origin")

        with TestClient(app) as client, client.websocket_connect(
            "/ws", headers={**headers, "origin": "https://play.example.com"}
        ) as websocket:
            websocket.send_json({"type": "join", "room_key": "test-key"})
            self.assertEqual(websocket.receive_json()["type"], "joined")

    def test_client_cannot_forge_identity_or_host(self) -> None:
        headers = self.cookie("Browser")
        with TestClient(self.app) as client, client.websocket_connect(
            "/ws", headers=headers
        ) as websocket:
            # A forged user_id / is_host in join must be ignored; the cookie wins.
            websocket.send_json({
                "type": "join", "room_key": "test-key",
                "user": {"id": 999, "username": "Mallory"}, "is_host": True,
            })
            joined = websocket.receive_json()
            self.assertEqual(joined["user"], {"id": 1, "username": "Browser"})
            self.assertFalse(joined["is_host"])

    def test_owner_timeline_permission_over_websocket(self) -> None:
        database = Database(str(Path(self.tempdir.name) / "owner.db"))
        asyncio.run(database.initialize("# initial", {"P1": {}, "P2": {}}))
        game = GameServer(
            database, FakeUpdater(), FakeNarrator(),
            scenario_name="test", owner_user_id=1,
        )
        app = create_web_app(game, self.accounts, Path(self.tempdir.name) / "static")
        owner_headers = self.cookie("Owner")
        player_headers = self.cookie("Player")

        with TestClient(app) as client:
            with client.websocket_connect("/ws", headers=owner_headers) as owner, \
                    client.websocket_connect("/ws", headers=player_headers) as player:
                owner.send_json({"type": "join", "room_key": "test-key"})
                player.send_json({"type": "join", "room_key": "test-key"})
                self.assertTrue(self._receive_until(owner, "joined")["is_host"])
                self.assertFalse(self._receive_until(player, "joined")["is_host"])
                self._receive_until(owner, "room_message")
                self._receive_until(player, "room_message")

                # A non-owner cannot manage the timeline, even with a forged is_host.
                player.send_json({"type": "retry", "is_host": True})
                forbidden = self._receive_until(player, "error")
                self.assertIn("forbidden", forbidden["detail"])
                player.send_json({"type": "rollback", "round": 1})
                self.assertIn("forbidden", self._receive_until(player, "error")["detail"])

                # The owner passes the permission check and reaches the timeline logic.
                owner.send_json({"type": "retry"})
                owner_error = self._receive_until(owner, "error")
                self.assertIn("没有可重新生成的回合", owner_error["detail"])

    def test_static_client_is_served(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "web client")
        self.assertEqual(response.headers["cache-control"], "no-cache")

    def test_two_public_clients_can_be_assigned_and_complete_a_round(self) -> None:
        browser_headers = self.cookie("Browser")
        terminal_headers = self.cookie("Terminal")
        with TestClient(self.app) as client:
            with client.websocket_connect("/ws", headers=browser_headers) as browser, \
                    client.websocket_connect("/ws", headers=terminal_headers) as terminal:
                browser.send_json({"type": "join", "room_key": "test-key"})
                terminal.send_json({"type": "join", "room_key": "test-key"})
                self._receive_until(browser, "room_message")
                self._receive_until(terminal, "room_message")

                assert client.portal is not None
                client.portal.call(
                    self.game._assign_role_ids,
                    {"P1": self.accounts.user_by_username("Browser").id,
                     "P2": self.accounts.user_by_username("Terminal").id},
                )
                browser_view = self._receive_until(browser, "role_view")
                terminal_view = self._receive_until(terminal, "role_view")
                self.assertEqual(browser_view["role"], "P1")
                self.assertEqual(browser_view["draft"], "")
                self.assertEqual(terminal_view["role"], "P2")
                self._receive_until(browser, "room_message")
                self._receive_until(terminal, "room_message")

                browser.send_json({"type": "action", "text": "open the door"})
                browser.send_json({"type": "submit"})
                self._receive_until(browser, "state")
                self._receive_until(terminal, "state")
                terminal.send_json({"type": "action", "text": "watch the window"})
                terminal.send_json({"type": "submit"})

                browser_round = self._receive_until(browser, "role_round")
                terminal_round = self._receive_until(terminal, "role_round")
                self.assertEqual(browser_round["role"], "P1")
                self.assertEqual(terminal_round["role"], "P2")
                self.assertEqual(
                    [entry["kind"] for entry in browser_round["entries"]],
                    ["action", "narration", "character_status"],
                )
                self._receive_until(browser, "round_complete")
                self._receive_until(terminal, "round_complete")

    def test_three_public_clients_complete_a_round(self) -> None:
        roles = RoleConfig.from_data({
            "count": 3, "names": ["角色1", "角色2", "角色3"]
        })
        database = Database(
            str(Path(self.tempdir.name) / "three-role.db"), roles.role_ids
        )
        asyncio.run(database.initialize())
        game = GameServer(
            database,
            DynamicUpdater(),
            FakeNarrator(),
            scenario_name="three",
            role_config=roles,
        )
        app = create_web_app(game, self.accounts, Path(self.tempdir.name) / "static")
        names = ("Alice", "Bob", "Carol")
        headers = [self.cookie(name) for name in names]
        with TestClient(app) as client:
            with (
                client.websocket_connect("/ws", headers=headers[0]) as first,
                client.websocket_connect("/ws", headers=headers[1]) as second,
                client.websocket_connect("/ws", headers=headers[2]) as third,
            ):
                sockets = (first, second, third)
                for socket in sockets:
                    socket.send_json({"type": "join", "room_key": "test-key"})
                    joined = self._receive_until(socket, "joined")
                    self.assertEqual([role["id"] for role in joined["roles"]], ["P1", "P2", "P3"])
                assert client.portal is not None
                client.portal.call(
                    game._assign_role_ids,
                    {role: self.accounts.user_by_username(name).id
                     for role, name in zip(roles.role_ids, names, strict=True)},
                )
                for role, socket in zip(roles.role_ids, sockets, strict=True):
                    self.assertEqual(self._receive_until(socket, "role_view")["role"], role)
                for role, socket in zip(roles.role_ids, sockets, strict=True):
                    socket.send_json({"type": "action", "text": f"action {role}"})
                    socket.send_json({"type": "submit"})
                for role, socket in zip(roles.role_ids, sockets, strict=True):
                    self.assertEqual(self._receive_until(socket, "role_round")["role"], role)
                    self._receive_until(socket, "round_complete")

    @staticmethod
    def _receive_until(websocket, message_type: str) -> dict:
        while True:
            message = websocket.receive_json()
            if message["type"] == message_type:
                return message


class ExplicitHandlerTests(unittest.IsolatedAsyncioTestCase):
    async def test_public_handler_rejects_join_host(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            game = GameServer(database, None, None)

            public = SequenceConnection({"type": "join_host"})
            await game.public_handler(public, user(1, "Browser"))
            self.assertEqual(public.messages[0]["type"], "error")
            self.assertIn("不接受房主连接", public.messages[0]["detail"])
            self.assertTrue(public.closed)

    async def test_action_length_limit_is_reported_without_storing_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            game = GameServer(database, None, None)
            connection = SequenceConnection({"type": "join"})
            await game.sessions.join(user(1, "Alice"), connection)
            await game.sessions.join(user(2, "Bob"), SequenceConnection({"type": "join"}))
            await game.sessions.assign_roles({"P1": 1, "P2": 2})

            await game._handle_command(1, connection, json.dumps({
                "type": "action", "text": "x" * (MAX_ACTION_LENGTH + 1),
            }))
            self.assertEqual(connection.messages[-1]["type"], "error")
            self.assertEqual(game.rounds.players["P1"].action, "")


class OriginCheckTests(unittest.TestCase):
    def test_non_browser_clients_are_allowed(self) -> None:
        self.assertTrue(origin_allowed(None, "example.com", ()))
        self.assertTrue(origin_allowed(None, "example.com", ("https://example.com",)))

    def test_configured_origins_are_required_when_present(self) -> None:
        allowed = ("https://play.example.com",)
        self.assertTrue(origin_allowed("https://play.example.com", "x", allowed))
        self.assertFalse(origin_allowed("https://evil.example.com", "x", allowed))

    def test_default_allows_same_host_and_loopback(self) -> None:
        self.assertTrue(origin_allowed("http://192.168.1.9:8080", "192.168.1.9:8080", ()))
        self.assertTrue(origin_allowed("http://localhost:8080", "192.168.1.9:8080", ()))
        self.assertTrue(origin_allowed("http://127.0.0.1:8766", "127.0.0.1:8766", ()))
        self.assertFalse(origin_allowed("https://evil.example.com", "192.168.1.9:8080", ()))
        self.assertFalse(origin_allowed("file://tmp/x", "192.168.1.9:8080", ()))


if __name__ == "__main__":
    unittest.main()
