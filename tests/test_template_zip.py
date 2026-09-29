"""ZIP files are bounded payloads, never direct extraction into a live Template."""

import io
import json
import shutil
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

from starlette.testclient import TestClient

from server.platform.catalog import create_template_from_scaffold
from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager
from server.platform.template_zip import (
    MAX_FILE_BYTES, export_template_zip, import_template_zip,
)
from server.platform.web import create_platform_app


def zip_files(files: dict[str, bytes]) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return output.getvalue()


class TemplateZipTests(unittest.TestCase):
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
            self.database, self.templates, self.owner.id, "旧名称", 2, 2, 4,
        )
        self.payload = self.templates / self.record.id

    def files(self) -> dict[str, bytes]:
        return {path.relative_to(self.payload).as_posix(): path.read_bytes()
                for path in self.payload.rglob("*") if path.is_file()}

    def imported(self, data: bytes, template_id: str | None = None):
        return import_template_zip(self.database, self.templates, self.owner.id,
                                   data, 2, 4, template_id)

    def test_export_import_new_and_replace_preserve_advanced_files(self) -> None:
        (self.payload / "prompts/narration.md").write_text("高级叙事 prompt", encoding="utf-8")
        exported = export_template_zip(self.database, self.templates, self.record.id, self.owner.id)
        with zipfile.ZipFile(io.BytesIO(exported)) as archive:
            self.assertIn("metadata.json", archive.namelist())
            self.assertEqual(archive.read("prompts/narration.md"), "高级叙事 prompt".encode())
        created = self.imported(exported)
        self.assertNotEqual(created.id, self.record.id)
        self.assertFalse(created.is_public)
        self.assertEqual((self.templates / created.id / "prompts/narration.md").read_text(), "高级叙事 prompt")
        wrapped = zip_files({f"my-story/{name}": value for name, value in self.files().items()})
        wrapped_record = self.imported(wrapped)
        self.assertTrue((self.templates / wrapped_record.id / "metadata.json").is_file())

        files = self.files()
        metadata = json.loads(files["metadata.json"])
        metadata["title"] = "ZIP 新名称"
        files["metadata.json"] = json.dumps(metadata, ensure_ascii=False).encode()
        files["world/world.md"] = "导入后的世界".encode()
        updated = self.imported(zip_files(files), self.record.id)
        self.assertEqual(updated.id, self.record.id)
        self.assertEqual(updated.name, "ZIP 新名称")
        self.assertEqual((self.payload / "world/world.md").read_text(), "导入后的世界")
        self.assertEqual((self.payload / "prompts/narration.md").read_text(), "高级叙事 prompt")

    def test_rejects_unsafe_or_invalid_archives_without_touching_original(self) -> None:
        original = self.files()
        for extra in ("../escape.md", "/absolute.md", "characters/1/other.py",
                      "prompts/extra.txt", "world/../metadata.json"):
            with self.subTest(extra=extra):
                with self.assertRaisesRegex(ValueError, "invalid_template_zip"):
                    self.imported(zip_files({**original, extra: b"bad"}), self.record.id)
        broken = dict(original)
        broken.pop("world/world_state_schema.json")
        with self.assertRaises(ValueError):
            self.imported(zip_files(broken), self.record.id)
        before_count = len(self.database.list_user_templates(self.owner.id))
        with self.assertRaises(ValueError):
            self.imported(zip_files(broken))
        self.assertEqual(len(self.database.list_user_templates(self.owner.id)), before_count)
        large = zip_files({"metadata.json": b"a" * (MAX_FILE_BYTES + 1)})
        with self.assertRaises(ValueError):
            self.imported(large, self.record.id)
        duplicate = io.BytesIO()
        with zipfile.ZipFile(duplicate, "w") as archive:
            archive.writestr("metadata.json", original["metadata.json"])
            archive.writestr("metadata.json", original["metadata.json"])
        with self.assertRaisesRegex(ValueError, "invalid_template_zip"):
            self.imported(duplicate.getvalue(), self.record.id)
        linked = io.BytesIO()
        with zipfile.ZipFile(linked, "w") as archive:
            for name, content in original.items():
                info = zipfile.ZipInfo(name)
                if name == "world/world.md":
                    info.create_system = 3
                    info.external_attr = 0o120777 << 16
                archive.writestr(info, content)
        with self.assertRaisesRegex(ValueError, "invalid_template_zip"):
            self.imported(linked.getvalue(), self.record.id)
        self.assertEqual(self.files(), original)
        self.assertFalse((self.root / "escape.md").exists())

    def test_rejects_unsupported_role_count_and_rolls_back_database_failure(self) -> None:
        original = self.files()
        files = dict(original)
        metadata = json.loads(files["metadata.json"])
        metadata["count"] = 5
        metadata["names"] = [f"角色{i}" for i in range(1, 6)]
        files["metadata.json"] = json.dumps(metadata, ensure_ascii=False).encode()
        with self.assertRaises(ValueError):
            self.imported(zip_files(files), self.record.id)
        metadata["count"] = 2
        metadata["names"] = ["角色1", "角色2"]
        metadata["title"] = "数据库失败"
        files["metadata.json"] = json.dumps(metadata, ensure_ascii=False).encode()
        with mock.patch.object(self.database, "rename_template", side_effect=RuntimeError("db failure")):
            with self.assertRaisesRegex(RuntimeError, "db failure"):
                self.imported(zip_files(files), self.record.id)
        self.assertEqual(self.files(), original)
        self.assertEqual(self.database.get_template(self.record.id).name, "旧名称")

    def test_http_owner_only_and_zip_round_trip(self) -> None:
        manager = RoomManager(self.database, self.root / "games", self.templates)
        app = create_platform_app(self.database, room_manager=manager)
        owner = {"cookie": f"rp_auth={self.database.create_session(self.owner.id, 30)}"}
        other = {"cookie": f"rp_auth={self.database.create_session(self.other.id, 30)}"}
        url = f"/api/templates/{self.record.id}/zip"
        with TestClient(app) as client:
            response = client.get(url, headers=owner)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["content-type"], "application/zip")
            self.assertEqual(client.get(url, headers=other).status_code, 403)
            self.assertEqual(client.put(url, headers=other, content=response.content).status_code, 403)
            self.assertEqual(client.put(url, headers=owner, content=response.content).status_code, 200)
            created = client.post("/api/templates/import-zip", headers=owner, content=response.content)
            self.assertEqual(created.status_code, 201)
            self.assertTrue((self.templates / created.json()["id"] / "metadata.json").is_file())
            self.assertEqual(client.post("/api/templates/import-zip", headers=owner,
                                         content=b"not a zip").status_code, 400)
