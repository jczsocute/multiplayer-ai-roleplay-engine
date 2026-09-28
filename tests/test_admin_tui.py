import unittest

from textual.widgets import Input, RichLog

from client.admin import AdminApp, AdminClientError


class FakeAdminClient:
    """In-memory stand-in for the loopback Admin WebSocket client."""

    def __init__(self, *, allow: bool = True, data: dict | None = None) -> None:
        self.allow = allow
        self.data = data or {}
        self.calls: list[tuple[str, dict]] = []
        self.closed = False
        self.user: dict | None = None

    async def login(self, username: str, password: str) -> dict:
        if not self.allow:
            raise AdminClientError("forbidden", "该账号不是平台管理员")
        self.user = {"id": 1, "username": username, "is_admin": True}
        return self.user

    async def command(self, command_name: str, **payload) -> dict:
        self.calls.append((command_name, payload))
        if command_name in self.data.get("__errors__", ()):
            raise AdminClientError("room_not_found", "房间不存在")
        return self.data.get(command_name) or DEFAULTS.get(command_name, {})

    async def close(self) -> None:
        self.closed = True

    def commands(self) -> list[str]:
        return [name for name, _ in self.calls]

    def payload(self, name: str) -> dict:
        return next(payload for called, payload in self.calls if called == name)


DEFAULTS = {
    "close-room": {"code": "AB12"},
    "delete-user": {"username": "Bob"},
    "create-user": {"id": 3, "username": "Carol"},
    "set-admin": {"username": "Bob", "is_admin": True},
    "unset-admin": {"username": "Bob", "is_admin": False},
    "rename-template": {
        "id": "tmpl_PUBLIC", "name": "Renamed", "owner_username": "Alice",
        "is_public": True, "roles": ["林岚", "周砚"],
    },
    "set-template-public": {
        "id": "tmpl_PUBLIC", "name": "Renamed", "owner_username": "Alice",
        "is_public": True, "roles": ["林岚", "周砚"],
    },
    "import-template": {
        "id": "tmpl_X", "name": "Love Story", "owner_username": "Alice",
        "is_public": True, "roles": ["林岚", "周砚"],
    },
}


USERS = {"users": {"users": [
    {"id": 1, "username": "Alice", "is_admin": True, "created_at": "2026-01-01T00:00:00"},
    {"id": 2, "username": "Bob", "is_admin": False, "created_at": "2026-01-02T00:00:00"},
]}}


class AdminTuiLoginTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_login_shows_console_and_lists_users(self) -> None:
        client = FakeAdminClient(data=USERS)
        app = AdminApp(client)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            login = app.screen
            login.query_one("#login-username", Input).value = "Alice"
            login.query_one("#login-password", Input).value = "password123"
            await login.submit()
            await pilot.pause()

            self.assertIsNot(login, app.screen)
            console = app.screen
            self.assertEqual(client.commands()[0], "users")
            self.assertEqual(client.user["username"], "Alice")
            rendered = "\n".join(str(line) for line in console.query_one("#admin-content", RichLog).lines)
            self.assertIn("用户", rendered)

    async def test_rejected_login_stays_on_the_login_screen(self) -> None:
        client = FakeAdminClient(allow=False)
        app = AdminApp(client)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            login = app.screen
            login.query_one("#login-username", Input).value = "Bob"
            login.query_one("#login-password", Input).value = "password123"
            await login.submit()
            await pilot.pause()

            self.assertIs(login, app.screen)
            self.assertIn("不是平台管理员", login.status_text)
            self.assertEqual(client.commands(), [])

    async def test_empty_credentials_are_not_sent(self) -> None:
        client = FakeAdminClient()
        app = AdminApp(client)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause()
            login = app.screen
            await login.submit()
            self.assertIn("请填写", login.status_text)
            self.assertEqual(client.commands(), [])


class AdminTuiConsoleTests(unittest.IsolatedAsyncioTestCase):
    async def open_console(self, pilot, client) -> None:
        login = pilot.app.screen
        login.query_one("#login-username", Input).value = "Alice"
        login.query_one("#login-password", Input).value = "password123"
        await login.submit()
        await pilot.pause()

    async def type_command(self, pilot, text: str) -> None:
        command = pilot.app.screen.query_one("#admin-command", Input)
        command.value = text
        command.focus()
        await pilot.press("enter")
        await pilot.pause()

    async def test_tabs_and_commands_share_one_path(self) -> None:
        client = FakeAdminClient(data=USERS)
        app = AdminApp(client)
        async with app.run_test(size=(110, 40)) as pilot:
            await self.open_console(pilot, client)
            client.calls.clear()

            app.screen.query_one("#tab-templates").press()
            await pilot.pause()
            self.assertEqual(client.commands(), ["templates"])

            await self.type_command(pilot, "rooms")
            self.assertEqual(client.commands()[-1], "rooms")

    async def test_command_line_parses_arguments(self) -> None:
        client = FakeAdminClient(data={**USERS, "room": {
            "code": "AB12", "owner_username": "Bob", "game_name": "Save",
            "game_id": "game_A", "has_password": False, "connected_count": 0,
            "role_count": 2, "assignments": {}, "round": 3, "stage": "WAITING_INPUT",
        }})
        app = AdminApp(client)
        async with app.run_test(size=(110, 40)) as pilot:
            await self.open_console(pilot, client)
            client.calls.clear()

            await self.type_command(pilot, "room ab12")
            self.assertEqual(client.payload("room"), {"code": "AB12"})
            rendered = "\n".join(
                str(line) for line in app.screen.query_one("#admin-content", RichLog).lines
            )
            self.assertIn("WAITING_INPUT", rendered)

            await self.type_command(pilot, "/help")
            self.assertEqual(client.commands(), ["room"])

    async def test_recovery_failed_room_detail_is_rendered(self) -> None:
        client = FakeAdminClient(data={**USERS, "room": {
            "code": "AB12", "owner_username": "Bob", "game_name": "Save",
            "game_id": "game_A", "has_password": False,
            "runtime_status": "RECOVERY_FAILED",
        }})
        app = AdminApp(client)
        async with app.run_test(size=(110, 40)) as pilot:
            await self.open_console(pilot, client)
            await self.type_command(pilot, "room ab12")
            rendered = "\n".join(
                str(line) for line in app.screen.query_one("#admin-content", RichLog).lines
            )
            self.assertIn("恢复失败", rendered)

    async def test_close_room_requires_confirmation(self) -> None:
        client = FakeAdminClient(data=USERS)
        app = AdminApp(client)
        async with app.run_test(size=(110, 40)) as pilot:
            await self.open_console(pilot, client)
            client.calls.clear()

            await self.type_command(pilot, "close-room AB12")
            self.assertEqual(client.calls, [])
            self.assertIn("确认关闭房间 AB12", app.screen.status_text)

            await self.type_command(pilot, "n")
            self.assertEqual(client.calls, [])
            self.assertIn("已取消", app.screen.status_text)

            await self.type_command(pilot, "close-room AB12")
            await self.type_command(pilot, "y")
            self.assertEqual(client.commands(), ["close-room"])
            self.assertEqual(client.payload("close-room"), {"code": "AB12"})

    async def test_delete_user_requires_confirmation(self) -> None:
        client = FakeAdminClient(data=USERS)
        app = AdminApp(client)
        async with app.run_test(size=(110, 40)) as pilot:
            await self.open_console(pilot, client)
            client.calls.clear()

            await self.type_command(pilot, "delete-user Bob")
            self.assertEqual(client.calls, [])
            await self.type_command(pilot, "y")
            self.assertEqual(client.payload("delete-user"), {"username": "Bob"})

    async def test_create_user_wizard_checks_repeated_password(self) -> None:
        client = FakeAdminClient(data=USERS)
        app = AdminApp(client)
        async with app.run_test(size=(110, 40)) as pilot:
            await self.open_console(pilot, client)
            client.calls.clear()

            await self.type_command(pilot, "create-user")
            await self.type_command(pilot, "Carol")
            await self.type_command(pilot, "password123")
            await self.type_command(pilot, "different")
            self.assertEqual(client.calls, [])
            self.assertIn("两次输入的密码不一致", app.screen.status_text)

            await self.type_command(pilot, "create-user")
            await self.type_command(pilot, "Carol")
            await self.type_command(pilot, "password123")
            await self.type_command(pilot, "password123")
            self.assertEqual(client.payload("create-user"),
                             {"username": "Carol", "password": "password123"})

    async def test_import_template_wizard(self) -> None:
        client = FakeAdminClient(data=USERS)
        app = AdminApp(client)
        async with app.run_test(size=(110, 40)) as pilot:
            await self.open_console(pilot, client)
            client.calls.clear()

            await self.type_command(pilot, "import-template")
            await self.type_command(pilot, "love_story")
            await self.type_command(pilot, "Alice")
            await self.type_command(pilot, "Love Story")
            await self.type_command(pilot, "y")
            self.assertEqual(client.payload("import-template"), {
                "source": "love_story", "owner": "Alice", "name": "Love Story",
                "public": True,
            })

    async def test_server_error_is_shown(self) -> None:
        client = FakeAdminClient(data={**USERS, "__errors__": ["close-room"]})
        app = AdminApp(client)
        async with app.run_test(size=(110, 40)) as pilot:
            await self.open_console(pilot, client)
            await self.type_command(pilot, "close-room NOPE")
            await self.type_command(pilot, "y")
            self.assertIn("房间不存在", app.screen.status_text)

    async def test_unknown_command_is_reported(self) -> None:
        client = FakeAdminClient(data=USERS)
        app = AdminApp(client)
        async with app.run_test(size=(110, 40)) as pilot:
            await self.open_console(pilot, client)
            client.calls.clear()
            await self.type_command(pilot, "drop-tables")
            self.assertEqual(client.calls, [])
            self.assertIn("命令无效", app.screen.status_text)


if __name__ == "__main__":
    unittest.main()
