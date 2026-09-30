import asyncio
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager
from server.platform.starter_templates import seed_starter_templates
from server.platform.web import create_platform_app


class StarterTemplateTests(unittest.TestCase):
    def test_register_seed_is_idempotent_and_playable(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = PlatformDatabase(root / "platform.db")
            database.initialize()

            async def factory(path: Path, owner: int) -> GameServer:
                game_db = Database(path / "game.db")
                await game_db.initialize()
                return GameServer(game_db, None, None, room_key="", owner_user_id=owner)

            manager = RoomManager(database, root / "games", root / "templates", factory)
            static = root / "static"
            static.mkdir()
            (static / "index.html").write_text("web", encoding="utf-8")
            app = create_platform_app(database, static, room_manager=manager)
            with TestClient(app) as client:
                response = client.post("/api/register", json={
                    "username": "Starter", "password": "password123",
                })
                self.assertEqual(response.status_code, 201, response.text)
                user_id = response.json()["id"]
                listed = client.get("/api/templates/mine").json()["templates"]
                self.assertEqual({item["name"] for item in listed},
                                 {"石头剪刀布", "Rock, Paper, Scissors"})
                self.assertTrue(all("starter" in item["tags"] for item in listed))
                self.assertEqual(seed_starter_templates(database, manager.templates_dir, user_id), [])
                self.assertEqual(len(database.list_user_templates(user_id)), 2)

                starter = next(item for item in listed if item["name"] == "石头剪刀布")
                exported = client.get(f"/api/templates/{starter['id']}/zip")
                self.assertEqual(exported.status_code, 200, exported.text)
                replaced = client.put(f"/api/templates/{starter['id']}/zip",
                                      content=exported.content)
                self.assertEqual(replaced.status_code, 200, replaced.text)
                self.assertEqual(seed_starter_templates(database, manager.templates_dir, user_id), [])
                created = client.post("/api/rooms", json={
                    "source": "template", "template_id": starter["id"],
                    "game_name": "First game", "password": "",
                })
                self.assertEqual(created.status_code, 201, created.text)
                self.assertIsNotNone(manager.get_runtime(created.json()["code"]))
                self.assertEqual(len(database.list_user_games(user_id)), 1)
                self.assertEqual(client.delete(f"/api/rooms/{created.json()['code']}").status_code, 200)

                copied = client.post(f"/api/templates/{starter['id']}/copy")
                self.assertEqual(copied.status_code, 201, copied.text)
                self.assertEqual(client.delete(f"/api/templates/{starter['id']}").status_code, 200)
                self.assertIsNotNone(database.get_template(copied.json()["id"]))

                second = client.post("/api/register", json={
                    "username": "Second", "password": "password123",
                })
                self.assertEqual(second.status_code, 201, second.text)
                other_ids = {item.id for item in database.list_user_templates(second.json()["id"])}
                self.assertEqual(len(other_ids), 2)
                self.assertTrue(other_ids.isdisjoint({item.id for item in database.list_user_templates(user_id)}))
