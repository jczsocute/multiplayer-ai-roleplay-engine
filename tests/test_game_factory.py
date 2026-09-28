"""The production GameServer factory must work through the real Room creation path.

Every other room test injects a stub ``game_factory``, which is exactly how a
signature drift inside ``load_game_server`` (``RoleConfig.load`` keyword-only
arguments) could make ``POST /api/rooms`` return 500 while the whole suite stayed
green. These tests use the default factory, so they fail loudly instead.
"""

import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

from server.config import Settings
from server.gameserver import factory as factory_module
from server.gameserver.factory import load_game_server
from server.platform.catalog import create_game_snapshot, import_template
from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager
from server.platform.web import create_platform_app


def fake_settings(**overrides) -> Settings:
    values = dict(
        web_host="127.0.0.1",
        web_port=8080,
        llm_api_key="test-key",
        llm_base_url="http://127.0.0.1:1",
        llm_model="test-model",
        world_update_max_tokens=8192,
        narration_max_tokens=4096,
        narrator_history_rounds=20,
        room_key="",
        disconnect_grace_seconds=60,
        room_disconnect_timeout_seconds=300,
        max_room_users=10,
        ui_font_scale=0.7,
        min_role_count=2,
        max_role_count=4,
        platform_db="data/platform.db",
        legacy_accounts_db="data/accounts.db",
        allow_registration=True,
        auth_session_days=30,
        secure_cookie=False,
        allowed_origins=(),
    )
    values.update(overrides)
    return Settings(**values)


class GameFactoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.templates_dir = self.root / "templates"
        self.games_dir = self.root / "games"
        self.database = PlatformDatabase(self.root / "platform.db")
        self.database.initialize()
        self.alice = self.database.create_user("Alice", "password123")

    async def test_load_game_server_applies_deployment_role_limits(self) -> None:
        game_path = self.games_dir / "game_ONE"
        game_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copytree(Path("templates/default"), game_path)

        server = await load_game_server(game_path, 7, fake_settings())
        self.assertEqual(server.role_ids, ("P1", "P2"))
        self.assertEqual(server.owner_user_id, 7)
        self.assertEqual(server.rounds.round_number, 1)

        # Proves min/max are actually forwarded into RoleConfig.load: the default
        # template has 2 roles, so a 3-role minimum must be rejected here.
        with self.assertRaisesRegex(ValueError, "at least 3"):
            await load_game_server(
                game_path, 7, fake_settings(min_role_count=3, max_role_count=4)
            )

    def test_room_creation_through_http_uses_the_default_factory(self) -> None:
        template = import_template(
            self.database, Path("templates/default"), self.templates_dir,
            self.alice.id, "Default Story", is_public=True,
        )
        game = create_game_snapshot(
            self.database, template.id, self.alice.id, "Alice Save",
            self.templates_dir, self.games_dir,
        )
        # No game_factory argument: RoomManager must use load_game_server.
        manager = RoomManager(self.database, self.games_dir, self.templates_dir)
        static = self.root / "static"
        static.mkdir()
        (static / "index.html").write_text("web", encoding="utf-8")
        app = create_platform_app(self.database, static, room_manager=manager)
        token = self.database.create_session(self.alice.id, 30)

        with mock.patch.object(
            factory_module, "load_settings", return_value=fake_settings()
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/rooms",
                    headers={"cookie": f"rp_auth={token}"},
                    json={"source": "game", "game_id": game.id, "password": ""},
                )

        self.assertEqual(response.status_code, 201, response.text)
        code = response.json()["code"]
        runtime = manager.get_runtime(code)
        self.assertIsNotNone(runtime)
        self.assertEqual(runtime.game_server.role_ids, ("P1", "P2"))
        self.assertEqual(runtime.game_server.owner_user_id, self.alice.id)
        self.assertEqual(runtime.game_server.rounds.round_number, 1)
        self.assertTrue((self.games_dir / game.id / "game.db").is_file())

    def test_room_creation_from_template_snapshot_uses_the_default_factory(self) -> None:
        template = import_template(
            self.database, Path("templates/default"), self.templates_dir,
            self.alice.id, "Default Story", is_public=True,
        )
        manager = RoomManager(self.database, self.games_dir, self.templates_dir)
        static = self.root / "static"
        static.mkdir()
        (static / "index.html").write_text("web", encoding="utf-8")
        app = create_platform_app(self.database, static, room_manager=manager)

        bob = self.database.create_user("Bob", "password123")
        token = self.database.create_session(bob.id, 30)
        with mock.patch.object(
            factory_module, "load_settings", return_value=fake_settings()
        ):
            with TestClient(app) as client:
                response = client.post(
                    "/api/rooms",
                    headers={"cookie": f"rp_auth={token}"},
                    json={
                        "source": "template", "template_id": template.id,
                        "name": "Bob Save", "password": "abc_123",
                    },
                )

        self.assertEqual(response.status_code, 201, response.text)
        runtime = manager.get_runtime(response.json()["code"])
        self.assertIsNotNone(runtime)
        # Template snapshot is owned by the player who created the Room.
        self.assertEqual(runtime.game_server.owner_user_id, bob.id)
        self.assertEqual(
            runtime.game_server.character_names, {"P1": "林岚", "P2": "周砚"}
        )

    async def test_active_room_recovery_uses_the_default_factory(self) -> None:
        template = import_template(
            self.database, Path("templates/default"), self.templates_dir,
            self.alice.id, "Default Story", is_public=True,
        )
        game = create_game_snapshot(
            self.database, template.id, self.alice.id, "Alice Save",
            self.templates_dir, self.games_dir,
        )
        with mock.patch.object(
            factory_module, "load_settings", return_value=fake_settings()
        ):
            first = RoomManager(self.database, self.games_dir, self.templates_dir)
            room = await first.create_room_from_game(self.alice, game.id)

            restarted = RoomManager(self.database, self.games_dir, self.templates_dir)
            await restarted.load_active_rooms()

        recovered = restarted.get_runtime(room.code)
        self.assertIsNotNone(recovered)
        self.assertEqual(recovered.game_server.owner_user_id, self.alice.id)
        self.assertEqual(recovered.game_server.role_ids, ("P1", "P2"))


if __name__ == "__main__":
    unittest.main()
