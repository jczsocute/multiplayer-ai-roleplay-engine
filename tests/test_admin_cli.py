"""The `--set-admin` / `--unset-admin` CLI path, run as a real subprocess.

The Platform Admin Console needs `users.is_admin`, and the supported way to grant
it is this CLI, so the flag is exercised end to end against a throwaway platform
database (never the developer's own `data/platform.db`).
"""

import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager
from server.platform.web import create_platform_app

REPO = Path(__file__).resolve().parents[1]


class AdminFlagCliTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.platform_db = self.root / "platform.db"
        self.legacy_db = self.root / "accounts.db"
        self.env = dict(os.environ)
        self.env.update({
            "PLATFORM_DB": str(self.platform_db),
            "ACCOUNTS_DB": str(self.legacy_db),
            "PYTHONPATH": str(REPO),
        })
        self.database = PlatformDatabase(self.platform_db)
        self.database.initialize(self.legacy_db)
        for username in ("admin", "player"):
            self.database.create_user(username, "password123")

    # --- helpers ------------------------------------------------------------

    def run_cli(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "server.main", *args],
            cwd=REPO, env=self.env, capture_output=True, text=True, timeout=60,
        )

    def is_admin(self, username: str) -> bool:
        connection = sqlite3.connect(self.platform_db)
        try:
            row = connection.execute(
                "SELECT is_admin FROM users WHERE username = ?", (username,)
            ).fetchone()
        finally:
            connection.close()
        self.assertIsNotNone(row, username)
        return bool(row[0])

    def admin_login(self, client: TestClient, username: str) -> dict:
        with client.websocket_connect("/admin/ws") as socket:
            socket.send_json({
                "type": "login", "username": username, "password": "password123",
            })
            return socket.receive_json()

    # --- tests --------------------------------------------------------------

    def test_cli_grants_and_revokes_the_admin_flag(self) -> None:
        granted = self.run_cli("--set-admin", "admin")
        self.assertEqual(granted.returncode, 0, granted.stderr)
        self.assertIn("admin is now a platform admin", granted.stdout)
        self.assertTrue(self.is_admin("admin"))
        # Other accounts are untouched: nothing is auto-promoted.
        self.assertFalse(self.is_admin("player"))

        again = self.run_cli("--set-admin", "admin")
        self.assertEqual(again.returncode, 0, again.stderr)
        self.assertTrue(self.is_admin("admin"))

        revoked = self.run_cli("--unset-admin", "admin")
        self.assertEqual(revoked.returncode, 0, revoked.stderr)
        self.assertIn("admin is now a regular user", revoked.stdout)
        self.assertFalse(self.is_admin("admin"))

    def test_cli_reports_an_unknown_account_without_creating_it(self) -> None:
        missing = self.run_cli("--set-admin", "ghost")
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("unknown account: ghost", missing.stderr)
        connection = sqlite3.connect(self.platform_db)
        try:
            count = connection.execute(
                "SELECT COUNT(*) FROM users WHERE username = 'ghost'"
            ).fetchone()[0]
        finally:
            connection.close()
        self.assertEqual(count, 0)

    def test_granting_takes_effect_without_restarting_the_server(self) -> None:
        """`/admin/ws` reads `is_admin` per connection, so no restart is needed."""
        manager = RoomManager(
            self.database, self.root / "games", self.root / "templates",
            self.no_rooms_factory,
        )
        static = self.root / "static"
        static.mkdir()
        (static / "index.html").write_text("web", encoding="utf-8")
        app = create_platform_app(self.database, static, room_manager=manager)

        with TestClient(app, client=("127.0.0.1", 12345)) as client:
            refused = self.admin_login(client, "admin")
            self.assertEqual(refused["code"], "forbidden")

            self.assertEqual(self.run_cli("--set-admin", "admin").returncode, 0)

            session = self.admin_login(client, "admin")
            self.assertEqual(session["type"], "session")
            self.assertTrue(session["user"]["is_admin"])

            self.assertEqual(self.run_cli("--unset-admin", "admin").returncode, 0)
            self.assertEqual(self.admin_login(client, "admin")["code"], "forbidden")

    async def no_rooms_factory(self, path: Path, owner_user_id: int):
        raise AssertionError("no Room is created in this test")


if __name__ == "__main__":
    unittest.main()
