"""HTTP surface for the Lobby: resource CRUD, room occupancy and admission."""

import asyncio
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.platform.catalog import create_game_snapshot, import_template
from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager
from server.platform.web import create_platform_app


class ResourceApiTests(unittest.TestCase):
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
        shutil.copytree(Path("templates/default"), self.templates_dir / "default")
        self.template = import_template(
            self.database, Path("templates/default"), self.templates_dir,
            self.bob.id, "Bob Template", is_public=True,
        )
        self.game = create_game_snapshot(
            self.database, self.template.id, self.alice.id, "love_story",
            self.templates_dir, self.games_dir,
        )
        asyncio.run(self.seed_game_db(self.games_dir / self.game.id / "game.db"))
        self.manager = RoomManager(
            self.database, self.games_dir, self.templates_dir, self.factory, max_users=10
        )
        static = self.root / "static"
        static.mkdir()
        (static / "index.html").write_text("web", encoding="utf-8")
        self.app = create_platform_app(
            self.database, static, room_manager=self.manager, min_role_count=2,
            max_role_count=4,
        )

    @staticmethod
    async def seed_game_db(path: Path) -> None:
        """A played save holds a real game.db, not a placeholder file."""
        database = Database(str(path))
        await database.initialize()

    async def factory(self, path: Path, owner_user_id: int) -> GameServer:
        path.mkdir(parents=True, exist_ok=True)
        database = Database(path / "game.db")
        await database.initialize()
        return GameServer(
            database, None, None, None, scenario_name=path.name, room_key="",
            owner_user_id=owner_user_id, max_users=10,
        )

    def headers(self, user_id: int) -> dict[str, str]:
        return {"cookie": f"rp_auth={self.database.create_session(user_id, 30)}"}

    def client(self) -> TestClient:
        return TestClient(self.app)

    # --- games --------------------------------------------------------------

    def test_game_lifecycle_over_http(self) -> None:
        alice, bob = self.headers(self.alice.id), self.headers(self.bob.id)
        with self.client() as client:
            listing = client.get("/api/games", headers=alice).json()["games"]
            self.assertEqual([game["name"] for game in listing], ["love_story"])
            self.assertIn("updated_at", listing[0])

            renamed = client.patch(
                f"/api/games/{self.game.id}", headers=alice, json={"name": "改名"}
            )
            self.assertEqual(renamed.status_code, 200)
            self.assertEqual(renamed.json()["name"], "改名")

            forbidden = client.patch(
                f"/api/games/{self.game.id}", headers=bob, json={"name": "stolen"}
            )
            self.assertEqual(forbidden.status_code, 403)
            self.assertEqual(forbidden.json()["error"], "forbidden")

            copied = client.post(f"/api/games/{self.game.id}/copy", headers=alice)
            self.assertEqual(copied.status_code, 201)
            self.assertEqual(copied.json()["name"], "改名_1")
            self.assertTrue((self.games_dir / copied.json()["id"] / "game.db").is_file())

            deleted = client.delete(f"/api/games/{self.game.id}", headers=alice)
            self.assertEqual(deleted.status_code, 200)
            self.assertIsNone(self.database.get_game(self.game.id))
            self.assertFalse((self.games_dir / self.game.id).exists())

            for method, url, payload in (
                ("patch", f"/api/games/{self.game.id}", {"name": "x"}),
                ("delete", f"/api/games/{self.game.id}", None),
                ("post", f"/api/games/{self.game.id}/copy", None),
            ):
                response = getattr(client, method)(
                    url, headers=bob, **({"json": payload} if payload else {})
                )
                self.assertEqual(response.status_code, 404, url)

    def test_active_game_blocks_copy_and_delete(self) -> None:
        room = asyncio.run(self.manager.create_room_from_game(self.alice, self.game.id))
        with self.client() as client:
            blocked_delete = client.delete(
                f"/api/games/{self.game.id}", headers=self.headers(self.alice.id)
            )
            self.assertEqual(blocked_delete.status_code, 409)
            self.assertEqual(blocked_delete.json()["error"], "game_is_active")

            blocked_copy = client.post(
                f"/api/games/{self.game.id}/copy", headers=self.headers(self.alice.id)
            )
            self.assertEqual(blocked_copy.status_code, 409)
            # Rename only touches metadata, so it stays allowed.
            renamed = client.patch(
                f"/api/games/{self.game.id}", headers=self.headers(self.alice.id),
                json={"name": "still running"},
            )
            self.assertEqual(renamed.status_code, 200)
        self.assertIsNotNone(self.manager.get_runtime(room.code))

    def test_game_endpoints_require_authentication(self) -> None:
        with self.client() as client:
            self.assertEqual(client.get("/api/games").status_code, 401)
            game_id = self.game.id
            self.assertEqual(
                client.patch(f"/api/games/{game_id}", json={"name": "x"}).status_code, 401
            )
            self.assertEqual(client.delete(f"/api/games/{game_id}").status_code, 401)
            self.assertEqual(
                client.post(f"/api/games/{game_id}/copy").status_code, 401
            )
            self.assertEqual(client.get("/api/templates/mine").status_code, 401)
            self.assertEqual(
                client.post("/api/templates", json={"name": "x", "role_count": 2})
                .status_code, 401
            )

    # --- templates ----------------------------------------------------------

    def test_template_lifecycle_over_http(self) -> None:
        alice, bob = self.headers(self.alice.id), self.headers(self.bob.id)
        with self.client() as client:
            created = client.post(
                "/api/templates", headers=alice, json={"name": "新模板", "role_count": 3}
            )
            self.assertEqual(created.status_code, 201)
            body = created.json()
            self.assertFalse(body["is_public"])
            self.assertEqual(body["role_count"], 3)
            self.assertEqual(body["owner_username"], "Alice")
            self.assertEqual(body["role_names"], ["角色1", "角色2", "角色3"])
            self.assertTrue(
                (self.templates_dir / body["id"] / "metadata.json").is_file()
            )

            mine = client.get("/api/templates/mine", headers=alice).json()["templates"]
            self.assertEqual([value["id"] for value in mine], [body["id"]])

            available = client.get("/api/templates", headers=alice).json()["templates"]
            self.assertEqual(
                {value["id"] for value in available}, {body["id"], self.template.id}
            )
            # Bob only sees the public one.
            bob_available = client.get("/api/templates", headers=bob).json()["templates"]
            self.assertEqual([value["id"] for value in bob_available], [self.template.id])

            renamed = client.patch(
                f"/api/templates/{body['id']}", headers=alice, json={"name": "改名"}
            )
            self.assertEqual(renamed.json()["name"], "改名")

            copied = client.post(f"/api/templates/{body['id']}/copy", headers=alice)
            self.assertEqual(copied.status_code, 201)
            self.assertEqual(copied.json()["name"], "改名_1")
            self.assertFalse(copied.json()["is_public"])

            denied = client.patch(
                f"/api/templates/{body['id']}", headers=bob, json={"name": "stolen"}
            )
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(denied.json()["error"], "template_not_owned")

            deleted = client.delete(f"/api/templates/{body['id']}", headers=alice)
            self.assertEqual(deleted.status_code, 200)
            self.assertIsNone(self.database.get_template(body["id"]))
            self.assertFalse((self.templates_dir / body["id"]).exists())

    def test_owner_toggles_template_visibility_over_http(self) -> None:
        alice, bob = self.headers(self.alice.id), self.headers(self.bob.id)
        with self.client() as client:
            created = client.post(
                "/api/templates", headers=alice, json={"name": "私密作品", "role_count": 2}
            ).json()
            self.assertFalse(created["is_public"])

            published = client.patch(
                f"/api/templates/{created['id']}", headers=alice, json={"is_public": True}
            )
            self.assertEqual(published.status_code, 200)
            self.assertTrue(published.json()["is_public"])
            # It now shows up for everyone in 可用模板.
            bob_available = client.get("/api/templates", headers=bob).json()["templates"]
            self.assertIn(created["id"], [value["id"] for value in bob_available])

            hidden = client.patch(
                f"/api/templates/{created['id']}", headers=alice, json={"is_public": False}
            )
            self.assertEqual(hidden.status_code, 200)
            self.assertFalse(hidden.json()["is_public"])
            bob_available = client.get("/api/templates", headers=bob).json()["templates"]
            self.assertNotIn(created["id"], [value["id"] for value in bob_available])

            # Renaming afterwards keeps the visibility untouched.
            renamed = client.patch(
                f"/api/templates/{created['id']}", headers=alice, json={"name": "改名"}
            )
            self.assertEqual(renamed.json()["name"], "改名")
            self.assertFalse(renamed.json()["is_public"])

            denied = client.patch(
                f"/api/templates/{created['id']}", headers=bob, json={"is_public": True}
            )
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(denied.json()["error"], "template_not_owned")

    def test_template_patch_rejects_bad_payloads(self) -> None:
        # The fixture Template belongs to Bob, so the owner check passes first and
        # the payload validation is what answers.
        owner = self.headers(self.bob.id)
        with self.client() as client:
            for payload in (
                {},
                {"is_public": "yes"},
                {"is_public": 1},
                {"is_public": None},
                {"name": ""},
            ):
                response = client.patch(
                    f"/api/templates/{self.template.id}", headers=owner, json=payload
                )
                self.assertEqual(response.status_code, 400, payload)
            # Nothing changed on the way out.
            self.assertTrue(self.database.get_template(self.template.id).is_public)

    def test_template_detail_returns_payload_metadata(self) -> None:
        alice, bob = self.headers(self.alice.id), self.headers(self.bob.id)
        with self.client() as client:
            created = client.post(
                "/api/templates", headers=alice, json={"name": "私密作品", "role_count": 3}
            ).json()
            detail = client.get(f"/api/templates/{created['id']}", headers=alice)
            self.assertEqual(detail.status_code, 200)
            body = detail.json()
            self.assertEqual(body["id"], created["id"])
            self.assertEqual(body["name"], "私密作品")
            self.assertEqual(body["owner_username"], "Alice")
            self.assertFalse(body["is_public"])
            self.assertEqual(body["role_count"], 3)
            self.assertEqual(body["role_names"], ["角色1", "角色2", "角色3"])
            self.assertEqual(body["introduction"], "")
            self.assertEqual(body["tags"], [])
            self.assertIn("updated_at", body)

            # A private Template is invisible to other accounts.
            denied = client.get(f"/api/templates/{created['id']}", headers=bob)
            self.assertEqual(denied.status_code, 403)
            self.assertEqual(denied.json()["error"], "template_not_owned")

            # Public: any authenticated account may read it.
            client.patch(
                f"/api/templates/{created['id']}", headers=alice, json={"is_public": True}
            )
            allowed = client.get(f"/api/templates/{created['id']}", headers=bob)
            self.assertEqual(allowed.status_code, 200)
            self.assertEqual(allowed.json()["owner_username"], "Alice")

            missing = client.get("/api/templates/tmpl_NOPE", headers=alice)
            self.assertEqual(missing.status_code, 404)
            self.assertEqual(missing.json()["error"], "template_not_found")

            self.assertEqual(
                client.get(f"/api/templates/{created['id']}").status_code, 401
            )

    def test_template_detail_reads_introduction_and_tags_from_the_payload(self) -> None:
        payload = self.templates_dir / self.template.id / "metadata.json"
        written = json.loads(payload.read_text(encoding="utf-8"))
        written["introduction"] = "这是从 payload 读取的介绍"
        written["tags"] = ["情感", "双人"]
        payload.write_text(json.dumps(written, ensure_ascii=False), encoding="utf-8")

        with self.client() as client:
            body = client.get(
                f"/api/templates/{self.template.id}", headers=self.headers(self.bob.id)
            ).json()
        self.assertEqual(body["introduction"], "这是从 payload 读取的介绍")
        self.assertEqual(body["tags"], ["情感", "双人"])
        # Lists stay lean but still carry the cheap tags.
        with self.client() as client:
            listed = client.get(
                "/api/templates", headers=self.headers(self.bob.id)
            ).json()["templates"][0]
        self.assertEqual(listed["tags"], ["情感", "双人"])
        self.assertNotIn("introduction", listed)

    def test_script_title_lives_in_the_payload(self) -> None:
        """Create / rename / copy keep `metadata.json.title` and the row equal."""
        alice = self.headers(self.alice.id)
        with self.client() as client:
            created = client.post(
                "/api/templates", headers=alice, json={"name": "初始标题", "role_count": 2}
            ).json()
            payload = json.loads(
                (Path(self.templates_dir) / created["id"] / "metadata.json").read_text("utf-8")
            )
            self.assertEqual(payload["title"], "初始标题")

            renamed = client.patch(
                f"/api/templates/{created['id']}", headers=alice, json={"name": "改名标题"}
            ).json()
            self.assertEqual(renamed["name"], "改名标题")
            payload = json.loads(
                (Path(self.templates_dir) / created["id"] / "metadata.json").read_text("utf-8")
            )
            self.assertEqual(payload["title"], "改名标题")

            # A hand-edited payload title is what the API displays, so the Script
            # is not defined by the database row alone.
            payload["title"] = "payload 里的标题"
            (Path(self.templates_dir) / created["id"] / "metadata.json").write_text(
                json.dumps(payload, ensure_ascii=False), encoding="utf-8"
            )
            listed = client.get("/api/templates", headers=alice).json()["templates"]
            shown = next(row for row in listed if row["id"] == created["id"])
            self.assertEqual(shown["name"], "payload 里的标题")
            self.assertEqual(
                client.get(f"/api/templates/{created['id']}", headers=alice).json()["name"],
                "payload 里的标题",
            )

            copied = client.post(
                f"/api/templates/{created['id']}/copy", headers=alice
            ).json()
            copy_payload = json.loads(
                (Path(self.templates_dir) / copied["id"] / "metadata.json").read_text("utf-8")
            )
            self.assertEqual(copy_payload["title"], copied["name"])

    def test_plaza_lists_only_public_templates(self) -> None:
        """`/api/templates/public` never leaks a private Template, mine included."""
        alice, bob = self.headers(self.alice.id), self.headers(self.bob.id)
        with self.client() as client:
            mine_private = client.post(
                "/api/templates", headers=alice, json={"name": "我的私密", "role_count": 2}
            ).json()
            mine_public = client.post(
                "/api/templates", headers=alice, json={"name": "我的公开", "role_count": 2}
            ).json()
            client.patch(
                f"/api/templates/{mine_public['id']}", headers=alice, json={"is_public": True}
            )
            # The fixture template belongs to Bob and is public.
            ids = lambda response: [row["id"] for row in response.json()["templates"]]

            plaza = client.get("/api/templates/public", headers=alice)
            self.assertEqual(plaza.status_code, 200)
            self.assertEqual(
                sorted(ids(plaza)), sorted([mine_public["id"], self.template.id])
            )
            self.assertNotIn(mine_private["id"], ids(plaza))
            # Bob sees exactly the same public set: ownership does not matter.
            self.assertEqual(sorted(ids(client.get("/api/templates/public", headers=bob))),
                             sorted(ids(plaza)))
            # ...while the Create Room source still contains her own private Script.
            available = ids(client.get("/api/templates", headers=alice))
            self.assertIn(mine_private["id"], available)
            self.assertIn("introduction", client.get(
                f"/api/templates/{mine_private['id']}", headers=alice
            ).json())

            # Unpublishing removes it from the plaza again.
            client.patch(
                f"/api/templates/{mine_public['id']}", headers=alice, json={"is_public": False}
            )
            self.assertEqual(ids(client.get("/api/templates/public", headers=alice)),
                             [self.template.id])

            self.assertEqual(
                client.get("/api/templates/public").status_code, 401
            )

    def test_template_creation_validates_input(self) -> None:
        alice = self.headers(self.alice.id)
        with self.client() as client:
            for payload in (
                {"name": "", "role_count": 2},
                {"name": "x", "role_count": 1},
                {"name": "x", "role_count": 5},
                {"name": "x"},
                {"name": "x", "role_count": True},
            ):
                response = client.post("/api/templates", headers=alice, json=payload)
                self.assertEqual(response.status_code, 400, payload)

    def test_deleting_a_template_keeps_its_games(self) -> None:
        game = create_game_snapshot(
            self.database, self.template.id, self.alice.id, "from template",
            self.templates_dir, self.games_dir,
        )
        self.assertEqual(game.source_template_id, self.template.id)
        with self.client() as client:
            response = client.delete(
                f"/api/templates/{self.template.id}", headers=self.headers(self.bob.id)
            )
        self.assertEqual(response.status_code, 200)
        surviving = self.database.get_game(game.id)
        self.assertIsNotNone(surviving)
        self.assertIsNone(surviving.source_template_id)
        self.assertTrue((self.games_dir / game.id / "metadata.json").is_file())

    # --- rooms --------------------------------------------------------------

    def test_room_listing_reports_occupancy_and_capacity(self) -> None:
        room = asyncio.run(
            self.manager.create_room_from_game(self.alice, self.game.id, "abc_123")
        )
        asyncio.run(self.manager.enter(self.alice.id, room.code, object()))
        with self.client() as client:
            listing = client.get(
                "/api/rooms", headers=self.headers(self.bob.id)
            ).json()["rooms"]
        self.assertEqual(len(listing), 1)
        entry = listing[0]
        self.assertEqual(entry["owner_username"], "Alice")
        self.assertEqual(entry["occupancy"], 1)
        self.assertEqual(entry["max_users"], 10)
        self.assertTrue(entry["has_password"])
        self.assertEqual(entry["connected_count"], 0)

    def test_websocket_reports_room_full(self) -> None:
        room = asyncio.run(self.manager.create_room_from_game(self.alice, self.game.id))
        for index in range(10):
            asyncio.run(self.manager.enter(1000 + index, room.code, object()))
        guest = self.headers(self.bob.id)
        with self.client() as client:
            with client.websocket_connect(f"/ws?room={room.code}", headers=guest) as ws:
                ws.send_json({"type": "join", "password": ""})
                error = ws.receive_json()
        self.assertEqual(error["type"], "error")
        self.assertEqual(error["code"], "room_full")
        self.assertIn("已满", error["detail"])
        self.assertIsNone(self.manager.user_current_room(self.bob.id))

    def test_websocket_reports_user_visible_errors_in_chinese(self) -> None:
        """Browser-facing errors carry a code plus Chinese text, never English."""
        room = asyncio.run(self.manager.create_room_from_game(self.alice, self.game.id))
        with self.client() as client:
            # A bad first frame is rejected before the game server is reached.
            with client.websocket_connect(
                f"/ws?room={room.code}", headers=self.headers(self.bob.id)
            ) as ws:
                ws.send_json({"type": "join_host"})
                error = ws.receive_json()
            self.assertEqual(error["code"], "invalid_request")
            self.assertIn("第一条消息必须是 join 或 resume", error["detail"])
            self.assertFalse(error["detail"].isascii())

            # A room error raised by the game server is Chinese too (Bob has no
            # role here, so the spectator rule answers; the live smoke test covers
            # the assigned-player path).
            with client.websocket_connect(
                f"/ws?room={room.code}", headers=self.headers(self.bob.id)
            ) as ws:
                ws.send_json({"type": "join", "password": ""})
                self.assertEqual(ws.receive_json()["type"], "joined")
                ws.send_json({"type": "submit"})
                detail = None
                for _ in range(40):
                    message = ws.receive_json()
                    if message.get("type") == "error":
                        detail = message["detail"]
                        break
                self.assertIsNotNone(detail)
                self.assertEqual(detail, "观众只能使用房间聊天、查看视角和退出")
                self.assertFalse(detail.isascii())

    def test_websocket_rejects_a_wrong_password_before_seating(self) -> None:
        room = asyncio.run(
            self.manager.create_room_from_game(self.alice, self.game.id, "abc_123")
        )
        with self.client() as client:
            with client.websocket_connect(
                f"/ws?room={room.code}", headers=self.headers(self.bob.id)
            ) as ws:
                ws.send_json({"type": "join", "password": "wrong"})
                error = ws.receive_json()
        self.assertEqual(error["code"], "invalid_room_password")
        self.assertEqual(self.manager.occupancy(room.code), 0)

    def test_ui_config_exposes_role_limits(self) -> None:
        with self.client() as client:
            config = client.get("/ui-config.json").json()
        self.assertEqual(config["min_role_count"], 2)
        self.assertEqual(config["max_role_count"], 4)
        self.assertEqual(config["max_room_users"], 10)


if __name__ == "__main__":
    unittest.main()
