import asyncio
import json
import shutil
import sqlite3
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

from starlette.testclient import TestClient

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.platform.bootstrap import BUNDLED_TEMPLATES, bootstrap_templates
from server.platform.catalog import set_payload_title
from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager
from server.platform.web import create_platform_app

LOOPBACK = ("127.0.0.1", 51234)


class FakeConnection:
    def __init__(self) -> None:
        self.messages: list[dict] = []
        self.closed = False

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))

    async def close(self, code: int = 1000) -> None:
        self.closed = True


class AdminFixture(unittest.TestCase):
    """Shared platform fixture: accounts, catalog rows, payloads and one Room."""

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
        self.database.set_admin("Alice", True)

        self.templates_dir.mkdir(parents=True, exist_ok=True)
        self.payload = self.templates_dir / "tmpl_PUBLIC"
        shutil.copytree(Path("templates/default"), self.payload)
        self.template = self.database.create_template(
            "tmpl_PUBLIC", self.alice.id, "Public Story", True
        )
        self.private = self.database.create_template(
            "tmpl_PRIVATE", self.alice.id, "Private Story", False
        )
        shutil.copytree(Path("templates/default"), self.templates_dir / "tmpl_PRIVATE")
        # Every supported write path keeps the payload title equal to the row
        # name; this fixture builds both directly, so it does the same.
        set_payload_title(self.payload, "Public Story")
        set_payload_title(self.templates_dir / "tmpl_PRIVATE", "Private Story")

        self.game = self.database.create_game(
            "game_A", self.bob.id, "Bob Save", self.template.id
        )
        self.manager = RoomManager(
            self.database, self.games_dir, self.templates_dir, self.factory
        )
        self.room = asyncio.run(
            self.manager.create_room_from_game(self.bob, self.game.id, "abc_123")
        )
        static = self.root / "static"
        static.mkdir()
        (static / "index.html").write_text("web", encoding="utf-8")
        self.app = create_platform_app(self.database, static, room_manager=self.manager)

    async def factory(self, path: Path, owner: int) -> GameServer:
        path.mkdir(parents=True, exist_ok=True)
        database = Database(path / "game.db")
        await database.initialize({"game": path.name})
        return GameServer(
            database, None, None, None,
            round_number=await database.current_round(),
            scenario_name=path.name, room_key="", owner_user_id=owner,
        )

    def client(self, *, loopback: bool = True) -> TestClient:
        return TestClient(self.app, client=LOOPBACK if loopback else ("10.0.0.9", 51234))

@contextmanager
def admin_session(client: TestClient, username: str = "Alice"):
    """Open the Admin socket, log in, and yield it (closed before the client)."""
    with client.websocket_connect("/admin/ws") as websocket:
        websocket.send_json({
            "type": "login", "username": username, "password": "password123",
        })
        session = websocket.receive_json()
        assert session["type"] == "session", session
        yield websocket


def command(websocket, message: dict) -> dict:
    websocket.send_json(message)
    return websocket.receive_json()


class AdminAccessTests(AdminFixture):
    def test_loopback_admin_can_log_in(self) -> None:
        with self.client() as client, client.websocket_connect("/admin/ws") as ws:
            ws.send_json({"type": "login", "username": "Alice", "password": "password123"})
            session = ws.receive_json()
            self.assertEqual(session["type"], "session")
            self.assertTrue(session["user"]["is_admin"])
            self.assertIn("users", session["commands"])

    def test_loopback_regular_user_is_forbidden(self) -> None:
        with self.client() as client, client.websocket_connect("/admin/ws") as ws:
            ws.send_json({"type": "login", "username": "Bob", "password": "password123"})
            error = ws.receive_json()
            self.assertEqual(error["type"], "error")
            self.assertEqual(error["code"], "forbidden")

    def test_wrong_password_is_unauthorized(self) -> None:
        with self.client() as client, client.websocket_connect("/admin/ws") as ws:
            ws.send_json({"type": "login", "username": "Alice", "password": "nope"})
            error = ws.receive_json()
            self.assertEqual(error["code"], "unauthorized")

    def test_non_loopback_is_rejected_even_for_admins(self) -> None:
        with self.client(loopback=False) as client:
            with client.websocket_connect("/admin/ws") as ws:
                error = ws.receive_json()
                self.assertEqual(error["code"], "forbidden")
                self.assertIn("本机", error["detail"])

    def test_client_cannot_claim_admin(self) -> None:
        with self.client() as client, client.websocket_connect("/admin/ws") as ws:
            ws.send_json({
                "type": "login", "username": "Bob", "password": "password123",
                "is_admin": True,
            })
            error = ws.receive_json()
            self.assertEqual(error["code"], "forbidden")


class AdminUserCommandTests(AdminFixture):
    def test_list_and_detail(self) -> None:
        with self.client() as client, admin_session(client) as ws:
            listing = command(ws, {"type": "users"})
            names = {entry["username"]: entry for entry in listing["data"]["users"]}
            self.assertEqual(set(names), {"Alice", "Bob"})
            self.assertTrue(names["Alice"]["is_admin"])
            self.assertFalse(names["Bob"]["is_admin"])
            self.assertEqual(names["Bob"]["games"], 1)
            self.assertEqual(names["Bob"]["rooms"], 1)

            detail = command(ws, {"type": "user", "username": "Bob"})
            self.assertEqual(detail["data"]["username"], "Bob")
            self.assertEqual(detail["data"]["templates"], 0)

    def test_create_duplicate_and_admin_flags(self) -> None:
        with self.client() as client, admin_session(client) as ws:
            created = command(ws, {
                "type": "create-user", "username": "Carol", "password": "password123",
            })
            self.assertEqual(created["data"]["username"], "Carol")
            self.assertFalse(created["data"]["is_admin"])

            duplicate = command(ws, {
                "type": "create-user", "username": "Carol", "password": "password123",
            })
            self.assertEqual(duplicate["type"], "error")
            self.assertEqual(duplicate["code"], "invalid_account")

            short = command(ws, {
                "type": "create-user", "username": "Dan", "password": "short",
            })
            self.assertEqual(short["type"], "error")

            promoted = command(ws, {"type": "set-admin", "username": "Carol"})
            self.assertTrue(promoted["data"]["is_admin"])
            demoted = command(ws, {"type": "unset-admin", "username": "Carol"})
            self.assertFalse(demoted["data"]["is_admin"])
            missing = command(ws, {"type": "set-admin", "username": "Nobody"})
            self.assertEqual(missing["code"], "account_not_found")

    def test_delete_rules(self) -> None:
        with self.client() as client, admin_session(client) as ws:
            command(ws, {
                "type": "create-user", "username": "Carol", "password": "password123",
            })
            self.assertIsNotNone(self.database.user_by_username("Carol"))
            deleted = command(ws, {"type": "delete-user", "username": "Carol"})
            self.assertEqual(deleted["type"], "ok")
            self.assertIsNone(self.database.user_by_username("Carol"))

            blocked = command(ws, {"type": "delete-user", "username": "Bob"})
            self.assertEqual(blocked["type"], "error")
            self.assertEqual(blocked["code"], "user_owns_resources")
            self.assertIn("Games", blocked["detail"])
            self.assertIn("Active Room", blocked["detail"])

    def test_delete_member_of_another_users_room_is_refused(self) -> None:
        carol = self.database.create_user("Carol", "password123")
        asyncio.run(self.manager.enter(carol.id, self.room.code, object()))
        with self.client() as client, admin_session(client) as ws:
            blocked = command(ws, {"type": "delete-user", "username": "Carol"})
        self.assertEqual(blocked["code"], "user_in_room")
        self.assertIn("仍在房间中", blocked["detail"])
        self.assertIsNotNone(self.database.user_by_username("Carol"))

    def test_delete_user_removes_sessions(self) -> None:
        with self.client() as client, admin_session(client) as ws:
            carol = self.database.create_user("Carol", "password123")
            self.database.create_session(carol.id, 30)
            command(ws, {"type": "delete-user", "username": "Carol"})
            with sqlite3.connect(self.database.path) as connection:
                remaining = connection.execute(
                    "SELECT COUNT(*) FROM auth_sessions WHERE user_id = ?", (carol.id,)
                ).fetchone()[0]
        self.assertEqual(remaining, 0)

    def test_unknown_command(self) -> None:
        with self.client() as client, admin_session(client) as ws:
            reply = command(ws, {"type": "drop-database"})
            self.assertEqual(reply["code"], "unknown_command")


class AdminTemplateCommandTests(AdminFixture):
    def test_list_detail_rename_visibility(self) -> None:
        with self.client() as client, admin_session(client) as ws:
            listing = command(ws, {"type": "templates"})
            ids = {entry["id"] for entry in listing["data"]["templates"]}
            self.assertEqual(ids, {"tmpl_PUBLIC", "tmpl_PRIVATE"})
            public = next(
                entry for entry in listing["data"]["templates"]
                if entry["id"] == "tmpl_PUBLIC"
            )
            self.assertEqual(public["owner_username"], "Alice")
            self.assertTrue(public["roles"])

            detail = command(ws, {"type": "template", "id": "tmpl_PUBLIC"})
            self.assertEqual(detail["data"]["name"], "Public Story")
            self.assertIn("created_at", detail["data"])

            renamed = command(ws, {
                "type": "rename-template", "id": "tmpl_PUBLIC", "name": "Renamed Story",
            })
            self.assertEqual(renamed["data"]["name"], "Renamed Story")

            hidden = command(ws, {
                "type": "set-template-public", "id": "tmpl_PUBLIC", "public": False,
            })
            self.assertFalse(hidden["data"]["is_public"])
            shown = command(ws, {
                "type": "set-template-public", "id": "tmpl_PUBLIC", "public": True,
            })
            self.assertTrue(shown["data"]["is_public"])

    def test_template_detail_shows_payload_metadata(self) -> None:
        source = self.templates_dir / "described"
        shutil.copytree(Path("templates/default"), source)
        with self.client() as client, admin_session(client) as ws:
            imported = command(ws, {
                "type": "import-template", "source": "described",
                "owner": "Alice", "name": "Described", "public": True,
            })
            self.assertEqual(imported["type"], "ok", imported)
            detail = command(ws, {"type": "template", "id": imported["data"]["id"]})
            self.assertEqual(detail["type"], "ok")
            # The Admin view shows the payload title, like the Web does.
            self.assertEqual(detail["data"]["name"], "Described")
            self.assertTrue(detail["data"]["introduction"])
            self.assertEqual(detail["data"]["tags"], ["基础剧本"])
            self.assertEqual(detail["data"]["roles"], ["林岚", "周砚"])

    def test_import_and_delete_keeps_games(self) -> None:
        shutil.copytree(Path("templates/default"), self.templates_dir / "lighthouse")
        with self.client() as client, admin_session(client) as ws:
            imported = command(ws, {
                "type": "import-template", "source": "lighthouse",
                "owner": "Alice", "name": "Lighthouse", "public": True,
            })
            self.assertEqual(imported["type"], "ok")
            template_id = imported["data"]["id"]
            self.assertTrue((self.templates_dir / template_id).is_dir())
            missing_owner = command(ws, {
                "type": "import-template", "source": "lighthouse", "owner": "Nobody",
            })
            self.assertEqual(missing_owner["code"], "account_not_found")
            unknown = command(ws, {
                "type": "import-template", "source": "no-such-dir", "owner": "Alice",
            })
            self.assertEqual(unknown["code"], "template_not_found")

            deleted = command(ws, {"type": "delete-template", "id": self.template.id})
            self.assertEqual(deleted["type"], "ok")
            self.assertIsNone(self.database.get_template(self.template.id))
            self.assertFalse(self.payload.exists())

        # Snapshot Game survives, keeping its payload; the link is cleared.
        game = self.database.get_game(self.game.id)
        self.assertIsNotNone(game)
        self.assertIsNone(game.source_template_id)
        self.assertTrue((self.games_dir / self.game.id / "game.db").is_file())

    def test_private_and_public_catalog_visibility(self) -> None:
        token = self.database.create_session(self.bob.id, 30)
        headers = {"cookie": f"rp_auth={token}"}
        with self.client() as client:
            templates = client.get("/api/templates", headers=headers).json()["templates"]
        ids = {entry["id"] for entry in templates}
        self.assertIn("tmpl_PUBLIC", ids)
        self.assertNotIn("tmpl_PRIVATE", ids)


class AdminRoomCommandTests(AdminFixture):
    def test_recovery_failed_room_can_be_listed_and_closed(self) -> None:
        self.manager.rooms.pop(self.room.code)
        with self.client() as client, admin_session(client) as ws:
            listing = command(ws, {"type": "rooms"})["data"]["rooms"]
            self.assertEqual(listing[0]["runtime_status"], "RECOVERY_FAILED")
            detail = command(ws, {"type": "room", "code": self.room.code})["data"]
            self.assertEqual(detail["game_id"], self.game.id)
            self.assertEqual(command(ws, {"type": "close-room", "code": self.room.code})["type"], "ok")
        self.assertIsNone(self.database.get_room(self.room.code))
        self.assertIsNotNone(self.database.get_game(self.game.id))

    def test_list_detail_and_admin_close(self) -> None:
        socket = FakeConnection()
        asyncio.run(self.room.game_server.sessions.join(self.bob, socket))
        asyncio.run(self.manager.enter(self.bob.id, self.room.code, socket))
        self.room.game_server.rounds.set_action("P1", "draft")

        with self.client() as client, admin_session(client) as ws:
            listing = command(ws, {"type": "rooms"})
            entry = listing["data"]["rooms"][0]
            self.assertEqual(entry["code"], self.room.code)
            self.assertEqual(entry["owner_username"], "Bob")
            self.assertEqual(entry["game_name"], "Bob Save")
            self.assertTrue(entry["has_password"])
            self.assertEqual(entry["round"], 1)

            detail = command(ws, {"type": "room", "code": self.room.code})
            self.assertEqual(detail["data"]["stage"], "WAITING_INPUT")
            self.assertEqual(detail["data"]["connected_count"], 1)
            self.assertEqual(detail["data"]["assignments"], {})

            # Admin closes another user's Room through RoomManager, not raw SQL.
            closed = command(ws, {"type": "close-room", "code": self.room.code})
            self.assertEqual(closed["type"], "ok")

        self.assertIsNone(self.manager.get_runtime(self.room.code))
        self.assertIsNone(self.database.get_room(self.room.code))
        self.assertTrue(socket.closed)
        self.assertEqual(socket.messages[-1]["type"], "room_closed")
        self.assertIsNotNone(self.database.get_game(self.game.id))
        self.assertTrue((self.games_dir / self.game.id / "game.db").is_file())

        with self.client() as client, admin_session(client) as ws:
            missing = command(ws, {"type": "close-room", "code": "NOPE"})
            self.assertEqual(missing["code"], "room_not_found")


class BootstrapTests(AdminFixture):
    def _bundled_payloads(self) -> None:
        """love_story as a Template dir, three_player_test only as a legacy Game."""
        shutil.copytree(Path("templates/default"), self.templates_dir / "love_story")
        legacy = self.games_dir / "three_player_test"
        shutil.copytree(Path("templates/default"), legacy)
        (legacy / "game.db").write_bytes(b"runtime state")

    def test_bootstrap_is_idempotent_and_public(self) -> None:
        self._bundled_payloads()
        first = bootstrap_templates(
            self.database, self.templates_dir, self.games_dir, self.alice.id,
            BUNDLED_TEMPLATES,
        )
        self.assertEqual([result.status for result in first], ["imported", "imported"])
        love_story = next(result for result in first if result.name == "love_story")
        self.assertIsNotNone(self.database.get_template(love_story.template_id))
        # The legacy Game's runtime state is never copied into a Template payload.
        self.assertFalse(
            (self.templates_dir / love_story.template_id / "game.db").exists()
        )

        second = bootstrap_templates(
            self.database, self.templates_dir, self.games_dir, self.alice.id,
            BUNDLED_TEMPLATES,
        )
        self.assertEqual([result.status for result in second], ["skipped", "skipped"])
        self.assertEqual(
            [result.template_id for result in second],
            [result.template_id for result in first],
        )
        self.assertEqual(
            len([t for t in self.database.list_templates()
                 if t.name == "都市夫妻的秘密"]), 1
        )

        # The catalog row carries the summarized Script title, and the payload
        # `metadata.json.title` holds the same value (neither lives alone).
        bundled = [
            self.database.get_template(result.template_id) for result in first
        ]
        self.assertEqual(
            {template.name for template in bundled},
            {"都市夫妻的秘密", "气象站的雷雨夜"},
        )
        for template in bundled:
            payload = json.loads(
                (self.templates_dir / template.id / "metadata.json").read_text("utf-8")
            )
            # payload title and catalog name are the same value, not two homes.
            self.assertEqual(payload["title"], template.name)
            self.assertTrue(payload["introduction"])
            self.assertTrue(payload["tags"])

        token = self.database.create_session(self.bob.id, 30)
        with TestClient(self.app) as client:
            templates = client.get(
                "/api/templates", headers={"cookie": f"rp_auth={token}"}
            ).json()["templates"]
        names = {entry["name"] for entry in templates}
        self.assertIn("都市夫妻的秘密", names)
        self.assertIn("气象站的雷雨夜", names)

    def test_missing_bundled_source_is_reported(self) -> None:
        results = bootstrap_templates(
            self.database, self.templates_dir, self.games_dir, self.alice.id,
            ("does_not_exist",),
        )
        self.assertEqual(results[0].status, "missing")


class AdminSchemaTests(unittest.TestCase):
    """An existing platform.db without is_admin is upgraded additively."""

    def test_existing_database_gains_is_admin_defaulting_to_zero(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "platform.db"
            with sqlite3.connect(path) as connection:
                connection.executescript(
                    "CREATE TABLE users ("
                    " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                    " username TEXT NOT NULL UNIQUE,"
                    " password_hash BLOB NOT NULL,"
                    " password_salt BLOB NOT NULL,"
                    " created_at TEXT NOT NULL"
                    ");"
                )
                connection.execute(
                    "INSERT INTO users"
                    " (username, password_hash, password_salt, created_at)"
                    " VALUES ('Legacy', x'00', x'11', '2026-01-01T00:00:00+00:00')"
                )

            database = PlatformDatabase(path)
            database.initialize()

            user = database.user_record("Legacy")
            self.assertIsNotNone(user)
            self.assertFalse(user.is_admin)
            self.assertFalse(database.is_admin(user.id))
            database.set_admin("Legacy", True)
            self.assertTrue(database.is_admin(user.id))


if __name__ == "__main__":
    unittest.main()
