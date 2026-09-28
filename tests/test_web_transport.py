import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from server.database import Database
from server.main import GameServer
from server.protocol import MAX_ACTION_LENGTH, PROTOCOL_VERSION
from server.roles import RoleConfig
from server.web import create_web_app


class FakeUpdater:
    async def update(self, **_kwargs):
        return {
            "world_state": {"place": "hall"},
            "public_information": {"time": "night"},
            "player_views": {"P1": {"seen": "door"}, "P2": {"seen": "window"}},
            "player_statusbar": {"P1": {"hp": 10}, "P2": {"hp": 20}},
        }


class FakeViews:
    async def generate(self, player_id, _world_state):
        return f"view {player_id}"


class FakeNarrator:
    async def narrate(self, player_id, *_args):
        return {"text": f"narration {player_id}", "status": {}}


class DynamicUpdater:
    async def update(self, current_world_state, actions):
        return {
            "world_state": {"place": "hall"},
            "public_information": {},
            "player_views": {role: {"seen": role} for role in actions},
            "player_statusbar": {role: {"hp": 10} for role in actions},
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
            database, FakeUpdater(), FakeViews(), FakeNarrator(), scenario_name="test"
        )
        static = Path(self.tempdir.name) / "static"
        static.mkdir()
        (static / "index.html").write_text("web client", encoding="utf-8")
        self.app = create_web_app(self.game, static)

    def tearDown(self) -> None:
        self.tempdir.cleanup()

    def test_public_websocket_join_protocol_and_spectator_view(self) -> None:
        with TestClient(self.app) as client, client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "join", "name": "Browser", "room_key": "test-key"})
            joined = websocket.receive_json()
            self.assertEqual(joined["type"], "joined")
            self.assertEqual(joined["protocol_version"], PROTOCOL_VERSION)
            self.assertEqual([role["id"] for role in joined["roles"]], ["P1", "P2"])
            self.assertIn("resume_token", joined)
            self.assertEqual(websocket.receive_json()["type"], "presence")
            self.assertEqual(websocket.receive_json()["type"], "state")
            self.assertEqual(websocket.receive_json()["type"], "room_message")

            websocket.send_json({"type": "view", "role": "P1"})
            role_view = websocket.receive_json()
            self.assertEqual(role_view["type"], "role_view")
            self.assertNotIn("draft", role_view)

            websocket.send_json({"type": "room_chat", "text": "hello"})
            room_message = websocket.receive_json()
            self.assertEqual(room_message["kind"], "spectator")

    def test_public_websocket_rejects_join_host(self) -> None:
        with TestClient(self.app) as client, client.websocket_connect("/ws") as websocket:
            websocket.send_json({"type": "join_host"})
            error = websocket.receive_json()
            self.assertEqual(error["type"], "error")
            self.assertIn("not allowed", error["detail"])
        self.assertIsNone(self.game.sessions.host_connection)

    def test_static_client_is_served(self) -> None:
        with TestClient(self.app) as client:
            response = client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.text, "web client")
        self.assertEqual(response.headers["cache-control"], "no-cache")

    def test_two_public_clients_can_be_assigned_and_complete_a_round(self) -> None:
        with TestClient(self.app) as client:
            with client.websocket_connect("/ws") as browser, client.websocket_connect("/ws") as terminal:
                browser.send_json({"type": "join", "name": "Browser", "room_key": "test-key"})
                terminal.send_json({"type": "join", "name": "Terminal", "room_key": "test-key"})
                self._receive_until(browser, "room_message")
                self._receive_until(terminal, "room_message")

                assert client.portal is not None
                client.portal.call(
                    self.game._assign_roles,
                    {"P1": "Browser", "P2": "Terminal"},
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
                    ["action", "narration", "statusbar"],
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
            FakeViews(),
            FakeNarrator(),
            scenario_name="three",
            role_config=roles,
        )
        app = create_web_app(game, Path(self.tempdir.name) / "static")
        with TestClient(app) as client:
            with (
                client.websocket_connect("/ws") as first,
                client.websocket_connect("/ws") as second,
                client.websocket_connect("/ws") as third,
            ):
                sockets = (first, second, third)
                names = ("Alice", "Bob", "Carol")
                for socket, name in zip(sockets, names, strict=True):
                    socket.send_json({"type": "join", "name": name, "room_key": "test-key"})
                    joined = self._receive_until(socket, "joined")
                    self.assertEqual([role["id"] for role in joined["roles"]], ["P1", "P2", "P3"])
                assert client.portal is not None
                client.portal.call(
                    game._assign_roles,
                    {"P1": "Alice", "P2": "Bob", "P3": "Carol"},
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
    async def test_host_handler_accepts_host_and_public_handler_does_not(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            game = GameServer(database, None, None, None)
            host = SequenceConnection({"type": "join_host"})
            await game.host_handler(host)
            self.assertEqual(host.messages[0]["type"], "host_joined")
            self.assertEqual(host.messages[0]["protocol_version"], PROTOCOL_VERSION)
            self.assertEqual(
                [role["id"] for role in host.messages[0]["roles"]], ["P1", "P2"]
            )

            public = SequenceConnection({"type": "join_host"})
            await game.public_handler(public)
            self.assertEqual(public.messages[0]["type"], "error")
            self.assertTrue(public.closed)

    async def test_action_length_limit_is_reported_without_storing_text(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Database(str(Path(directory) / "game.db"))
            await database.initialize()
            game = GameServer(database, None, None, None)
            connection = SequenceConnection({"type": "join", "name": "Alice"})
            await game.sessions.join("Alice", connection)
            await game.sessions.join("Bob", SequenceConnection({"type": "join", "name": "Bob"}))
            await game.sessions.assign_roles({"P1": "Alice", "P2": "Bob"})

            await game._handle_command("Alice", connection, json.dumps({
                "type": "action", "text": "x" * (MAX_ACTION_LENGTH + 1),
            }))
            self.assertEqual(connection.messages[-1]["type"], "error")
            self.assertEqual(game.rounds.players["P1"].action, "")


if __name__ == "__main__":
    unittest.main()
