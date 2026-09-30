"""The basic editor changes only its files and swaps a validated payload."""

import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

from server.gameserver.template import validate_template
from server.platform.catalog import create_template_from_scaffold
from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager
from server.platform.template_editor import read_template_editor, save_template_editor
from server.platform.web import create_platform_app


class TemplateEditorTests(unittest.TestCase):
    def setUp(self) -> None:
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.templates = self.root / "templates"
        shutil.copytree("templates/default", self.templates / "default")
        self.database = PlatformDatabase(self.root / "platform.db")
        self.database.initialize()
        self.owner = self.database.create_user("Owner", "password123")
        self.other = self.database.create_user("Other", "password123")
        self.record = create_template_from_scaffold(
            self.database, self.templates, self.owner.id, "剧本", 2, 2, 4,
        )
        self.payload = self.templates / self.record.id

    def editor(self) -> dict:
        return read_template_editor(self.database, self.templates, self.record.id, self.owner.id)

    def save(self, body: dict) -> dict:
        return save_template_editor(
            self.database, self.templates, self.record.id, self.owner.id, body, 2, 4,
        )

    def test_new_template_editor_starts_with_blank_story_fields(self) -> None:
        data = self.editor()
        self.assertEqual(data["title"], "剧本")
        self.assertEqual(data["introduction"], "")
        self.assertEqual(data["tags"], [])
        self.assertEqual(data["world"], "")
        self.assertEqual(data["ai_guidelines"], "")
        self.assertEqual(len(data["characters"]), 2)
        self.assertTrue(all(row["character"] == row["opening"] == ""
                            for row in data["characters"]))
        self.assertEqual(json.loads((self.payload / "world/world_state_initial.json").read_text()),
                         {"world_information": ""})
        self.assertFalse(any(self.payload.glob("characters/*/character_status_schema.json")))

    def test_text_save_preserves_advanced_files_and_updates_catalog(self) -> None:
        (self.payload / "characters/1/character_status_schema.json").write_text(
            '{"mood": "<current mood>"}', encoding="utf-8"
        )
        (self.payload / "characters/1/character_status_initial.json").write_text(
            '{"mood": "ready"}', encoding="utf-8"
        )
        advanced = (
            "world/world_state_schema.json", "world/world_state_initial.json",
            "characters/1/character_view_schema.json",
            "characters/1/character_status_schema.json",
            "characters/1/character_status_initial.json",
            "prompts/world_update.md", "prompts/narration.md",
        )
        (self.payload / "prompts/world_update.md").write_text("custom world prompt", encoding="utf-8")
        (self.payload / "characters/1/character_view_schema.json").write_text(
            '{"known": "<已确认的信息>"}', encoding="utf-8"
        )
        before = {name: (self.payload / name).read_bytes() for name in advanced}
        body = self.editor()
        body.update(title="新的标题", introduction="简介", tags=["冒险", "双人"],
                    world="新的世界", ai_guidelines="简洁写作")
        body["characters"][0].update(character="角色人设", opening="开场文字")
        saved = self.save(body)
        self.assertEqual(saved["world"], "新的世界")
        self.assertEqual(saved["characters"][0]["opening"], "开场文字")
        self.assertEqual(saved["ai_guidelines"], "简洁写作")
        self.assertEqual(saved["tags"], ["冒险", "双人"])
        self.assertEqual(self.database.get_template(self.record.id).name, "新的标题")
        self.assertEqual({name: (self.payload / name).read_bytes() for name in advanced}, before)

    def test_add_and_remove_only_tail_roles(self) -> None:
        body = self.editor()
        body["characters"].append({"index": 3, "character": "", "opening": ""})
        self.save(body)
        view = json.loads((self.payload / "characters/3/character_view_schema.json").read_text())
        self.assertEqual(list(view), ["world_information"])
        self.assertFalse((self.payload / "characters/3/character_status_schema.json").exists())
        self.assertEqual(validate_template(self.payload).role_ids, ("P1", "P2", "P3"))
        body = self.editor()
        body["characters"] = body["characters"][:2]
        self.save(body)
        self.assertFalse((self.payload / "characters/3").exists())
        self.assertEqual(json.loads((self.payload / "metadata.json").read_text())["count"], 2)

    def test_invalid_input_and_database_failure_leave_original_unchanged(self) -> None:
        original = self.editor()
        snapshot = {str(path.relative_to(self.payload)): path.read_bytes()
                    for path in self.payload.rglob("*") if path.is_file()}
        for count in (1, 5):
            body = self.editor()
            body["characters"] = [{"index": index, "character": "", "opening": ""}
                                  for index in range(1, count + 1)]
            with self.assertRaisesRegex(ValueError, "invalid_role_count"):
                self.save(body)
        body = self.editor()
        body["title"] = "失败的标题"
        with mock.patch("server.platform.template_editor.write_template_metadata", side_effect=OSError("write failure")):
            with self.assertRaisesRegex(OSError, "write failure"):
                self.save(body)
        with mock.patch.object(self.database, "rename_template", side_effect=RuntimeError("db failure")):
            with self.assertRaisesRegex(RuntimeError, "db failure"):
                self.save(body)
        self.assertEqual(self.editor(), original)
        self.assertEqual(snapshot, {str(path.relative_to(self.payload)): path.read_bytes()
                                    for path in self.payload.rglob("*") if path.is_file()})
        self.assertFalse(any(path.name.endswith((".editing", ".previous"))
                             for path in self.templates.iterdir()))

    def test_http_owner_can_read_and_save_other_cannot(self) -> None:
        manager = RoomManager(self.database, self.root / "games", self.templates)
        app = create_platform_app(self.database, room_manager=manager)
        owner_cookie = {"cookie": f"rp_auth={self.database.create_session(self.owner.id, 30)}"}
        other_cookie = {"cookie": f"rp_auth={self.database.create_session(self.other.id, 30)}"}
        url = f"/api/templates/{self.record.id}/editor"
        with TestClient(app) as client:
            response = client.get(url, headers=owner_cookie)
            self.assertEqual(response.status_code, 200)
            body = response.json()
            body["world"] = "HTTP 世界"
            self.assertEqual(client.put(url, json=body, headers=owner_cookie).status_code, 200)
            self.assertEqual(client.get(url, headers=owner_cookie).json()["world"], "HTTP 世界")
            self.assertEqual(client.get(url, headers=other_cookie).status_code, 403)
            self.assertEqual(client.put(url, json=body, headers=other_cookie).status_code, 403)

    def test_committed_catalog_failure_restores_payload_and_name(self) -> None:
        before = self.editor()
        body = self.editor()
        body["title"] = "临时标题"
        original_rename = self.database.rename_template
        failed = False

        def commit_then_fail(template_id: str, name: str):
            nonlocal failed
            result = original_rename(template_id, name)
            if not failed:
                failed = True
                raise RuntimeError("failure after commit")
            return result

        with mock.patch.object(self.database, "rename_template", side_effect=commit_then_fail):
            with self.assertRaisesRegex(RuntimeError, "failure after commit"):
                self.save(body)
        self.assertEqual(self.editor(), before)
        self.assertEqual(self.database.get_template(self.record.id).name, self.record.name)
