import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from starlette.testclient import TestClient

from server.platform.auth import AUTH_COOKIE_NAME
from server.platform.database import PlatformDatabase
from server.platform.web import create_platform_app


class AccountsTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.accounts = PlatformDatabase(str(Path(self.directory.name) / "platform.db"))
        self.accounts.initialize()

    def test_register_and_login(self) -> None:
        created = self.accounts.create_user("Alice", "password123")
        self.assertEqual(created.username, "Alice")
        authed = self.accounts.authenticate("Alice", "password123")
        self.assertIsNotNone(authed)
        self.assertEqual(authed.id, created.id)

    def test_duplicate_username_rejected(self) -> None:
        self.accounts.create_user("Alice", "password123")
        with self.assertRaisesRegex(ValueError, "已被使用"):
            self.accounts.create_user("Alice", "password456")

    def test_username_and_password_validation(self) -> None:
        with self.assertRaisesRegex(ValueError, "不能为空"):
            self.accounts.create_user("   ", "password123")
        with self.assertRaisesRegex(ValueError, "最长"):
            self.accounts.create_user("x" * 33, "password123")
        with self.assertRaisesRegex(ValueError, "至少"):
            self.accounts.create_user("Alice", "short")

    def test_password_is_not_stored_in_plaintext(self) -> None:
        self.accounts.create_user("Alice", "password123")
        with sqlite3.connect(self.accounts.path) as connection:
            password_hash, salt = connection.execute(
                "SELECT password_hash, password_salt FROM users WHERE username = ?",
                ("Alice",),
            ).fetchone()
        self.assertNotEqual(password_hash, b"password123")
        self.assertNotIn(b"password123", bytes(password_hash))
        self.assertTrue(salt)
        self.assertGreater(len(password_hash), 16)

    def test_wrong_password_rejected(self) -> None:
        self.accounts.create_user("Alice", "password123")
        self.assertIsNone(self.accounts.authenticate("Alice", "wrong-password"))
        self.assertIsNone(self.accounts.authenticate("Nobody", "password123"))

    def test_session_token_is_stored_only_as_a_hash(self) -> None:
        user = self.accounts.create_user("Alice", "password123")
        token = self.accounts.create_session(user.id, 30)
        with sqlite3.connect(self.accounts.path) as connection:
            stored = [row[0] for row in connection.execute("SELECT token_hash FROM auth_sessions")]
        self.assertNotIn(token, stored)
        self.assertEqual(len(stored), 1)
        self.assertEqual(self.accounts.resolve_session(token).id, user.id)

    def test_logout_deletes_session(self) -> None:
        user = self.accounts.create_user("Alice", "password123")
        token = self.accounts.create_session(user.id, 30)
        self.accounts.delete_session(token)
        self.assertIsNone(self.accounts.resolve_session(token))
        self.assertIsNone(self.accounts.resolve_session(None))

    def test_expired_and_unknown_session_rejected(self) -> None:
        user = self.accounts.create_user("Alice", "password123")
        expired = self.accounts.create_session(user.id, 0)
        self.assertIsNone(self.accounts.resolve_session(expired))
        self.assertIsNone(self.accounts.resolve_session("not-a-real-token"))


class AuthHttpTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.accounts = PlatformDatabase(str(Path(self.directory.name) / "platform.db"))
        self.accounts.initialize()

    def make_app(self, allow_registration: bool = True):
        return create_platform_app(
            self.accounts,
            Path(self.directory.name) / "static",
            allow_registration=allow_registration,
        )

    def register(self, client: TestClient, username: str, password: str = "password123"):
        return client.post("/api/register", json={"username": username, "password": password})

    def test_me_requires_login(self) -> None:
        with TestClient(self.make_app()) as client:
            response = client.get("/api/me")
        self.assertEqual(response.status_code, 401)

    def test_register_sets_cookie_and_me_returns_user(self) -> None:
        with TestClient(self.make_app()) as client:
            response = self.register(client, "Alice")
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json(), {"id": 1, "username": "Alice"})
            self.assertIn(AUTH_COOKIE_NAME, response.cookies)
            me = client.get("/api/me")
        self.assertEqual(me.status_code, 200)
        self.assertEqual(me.json(), {"id": 1, "username": "Alice"})

    def test_duplicate_registration_rejected(self) -> None:
        with TestClient(self.make_app()) as client:
            self.register(client, "Alice")
            duplicate = self.register(client, "Alice")
        self.assertEqual(duplicate.status_code, 400)

    def test_login_and_logout_cookie_lifecycle(self) -> None:
        with TestClient(self.make_app()) as client:
            self.register(client, "Alice")
            client.cookies.clear()
            self.assertEqual(client.get("/api/me").status_code, 401)

            wrong = client.post("/api/login", json={"username": "Alice", "password": "nope"})
            self.assertEqual(wrong.status_code, 401)

            login = client.post("/api/login", json={"username": "Alice", "password": "password123"})
            self.assertEqual(login.status_code, 200)
            self.assertEqual(client.get("/api/me").json()["username"], "Alice")

            logout = client.post("/api/logout")
            self.assertEqual(logout.status_code, 200)
            self.assertEqual(client.get("/api/me").status_code, 401)

    def test_registration_can_be_disabled(self) -> None:
        with TestClient(self.make_app(allow_registration=False)) as client:
            blocked = self.register(client, "Alice")
            self.assertEqual(blocked.status_code, 403)
            self.accounts.create_user("Bob", "password123")
            login = client.post("/api/login", json={"username": "Bob", "password": "password123"})
        self.assertEqual(login.status_code, 200)

    def test_ui_config_reports_registration_flag(self) -> None:
        with TestClient(self.make_app(allow_registration=False)) as client:
            config = client.get("/ui-config.json").json()
        self.assertFalse(config["allow_registration"])

    def test_invalid_json_body_is_a_clean_error(self) -> None:
        with TestClient(self.make_app()) as client:
            response = client.post(
                "/api/login", content=b"not json", headers={"Content-Type": "application/json"}
            )
        self.assertEqual(response.status_code, 400)
        self.assertIn("error", json.loads(response.text))


if __name__ == "__main__":
    unittest.main()
