"""Owner downloads completed world/action/narration history, never an AI partial round."""

import asyncio
import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path

from starlette.testclient import TestClient

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.gameserver.models import CompletedRound
from server.platform.catalog import create_game_snapshot, import_template
from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager
from server.platform.web import create_platform_app


class GameHistoryTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.templates = self.root / "templates"
        self.games = self.root / "games"
        self.database = PlatformDatabase(self.root / "platform.db")
        self.database.initialize()
        self.owner = self.database.create_user("Owner", "password123")
        self.other = self.database.create_user("Other", "password123")
        shutil.copytree("templates/default", self.templates / "default")
        script = import_template(self.database, Path("templates/default"),
                                 self.templates, self.owner.id, "Story")
        self.game = create_game_snapshot(self.database, script.id, self.owner.id,
                                         "Save", self.templates, self.games)
        self.game_path = self.games / self.game.id
        self.game_db = Database(str(self.game_path / "game.db"))
        asyncio.run(self.game_db.initialize({"world_information": "起点"}))
        with self.game_db._connect() as connection:
            connection.execute("UPDATE rounds SET status = 'COMPLETED', stage = 'FINISHED', result_world_state = ? WHERE round_number = 1",
                               (json.dumps({"world_information": "新世界"}),))
            connection.execute("INSERT INTO rounds (round_number, status) VALUES (2, 'OPEN')")
            connection.execute("UPDATE world_state SET content = ? WHERE id = 1",
                               (json.dumps({"world_information": "新世界"}),))
            connection.executemany("INSERT INTO round_actions VALUES (1, ?, ?)",
                                   [("P1", "开门"), ("P2", "等待")])
            connection.executemany(
                "INSERT INTO chat_messages (round_number, player_id, role, content) VALUES (1, ?, 'narrator', ?)",
                [("P1", "门开了"), ("P2", "你听见门响")],
            )
            connection.executemany(
                "INSERT INTO character_views (round_id, player_id, character_view_content) VALUES (1, ?, ?)",
                [("P1", '{"seen":"door"}'), ("P2", '{"heard":"door"}')],
            )
        self.manager = RoomManager(self.database, self.games, self.templates, self.factory)
        self.app = create_platform_app(self.database, room_manager=self.manager)

    async def factory(self, path: Path, owner_user_id: int) -> GameServer:
        database = Database(str(path / "game.db"))
        await database.initialize()
        return GameServer(database, None, None, owner_user_id=owner_user_id)

    def headers(self, user_id: int) -> dict[str, str]:
        return {"cookie": f"rp_auth={self.database.create_session(user_id, 30)}"}

    @staticmethod
    def archive(response) -> dict[str, object]:
        with zipfile.ZipFile(io.BytesIO(response.content)) as archive:
            return {name: json.loads(archive.read(name)) for name in archive.namelist()}

    def test_game_and_room_exports_include_full_completed_story(self) -> None:
        room = asyncio.run(self.manager.create_room_from_game(self.owner, self.game.id))
        with TestClient(self.app) as client:
            for path in (f"/api/games/{self.game.id}/history.zip",
                         f"/api/rooms/{room.code}/history.zip"):
                response = client.get(path, headers=self.headers(self.owner.id))
                self.assertEqual(response.status_code, 200, response.text)
                self.assertEqual(response.headers["content-type"], "application/zip")
                files = self.archive(response)
                self.assertEqual(files["manifest.json"]["completed_rounds"], 1)
                self.assertEqual(files["initial_world_state.json"], {"world_information": "起点"})
                self.assertEqual(files["current_world_state.json"], {"world_information": "新世界"})
                row = files["rounds/0001.json"]
                self.assertEqual(row["world_state"], {"world_information": "新世界"})
                self.assertEqual(row["actions"], {"P1": "开门", "P2": "等待"})
                self.assertEqual(row["narrations"], {"P1": "门开了", "P2": "你听见门响"})
                self.assertEqual(row["character_views"]["P1"], {"seen": "door"})
                self.assertNotIn("rounds/0002.json", files)
                self.assertEqual(client.get(path, headers=self.headers(self.other.id)).status_code, 403)

    def test_processing_rejects_both_owner_export_paths(self) -> None:
        room = asyncio.run(self.manager.create_room_from_game(self.owner, self.game.id))
        room.game_server.rounds.begin_reprocess(CompletedRound(2, {"P1": "a", "P2": "b"}))
        with TestClient(self.app) as client:
            for path in (f"/api/games/{self.game.id}/history.zip",
                         f"/api/rooms/{room.code}/history.zip"):
                response = client.get(path, headers=self.headers(self.owner.id))
                self.assertEqual(response.status_code, 409)
                self.assertEqual(response.json()["error"], "game_processing")
        with self.game_db._connect() as connection:
            connection.execute("UPDATE rounds SET stage = 'WORLD_UPDATING' WHERE round_number = 2")
        self.manager.rooms.clear()
        with TestClient(self.app) as client:
            response = client.get(f"/api/games/{self.game.id}/history.zip",
                                  headers=self.headers(self.owner.id))
            self.assertEqual(response.status_code, 409)

    def test_unstarted_save_exports_initial_state(self) -> None:
        fresh = create_game_snapshot(self.database, self.game.source_template_id,
                                     self.owner.id, "Unused", self.templates, self.games)
        with TestClient(self.app) as client:
            response = client.get(f"/api/games/{fresh.id}/history.zip",
                                  headers=self.headers(self.owner.id))
            self.assertEqual(response.status_code, 200)
            files = self.archive(response)
            self.assertEqual(files["manifest.json"]["completed_rounds"], 0)
            self.assertEqual(files["initial_world_state.json"], files["current_world_state.json"])
