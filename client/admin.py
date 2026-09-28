"""Local Platform Admin Console.

This is *not* a Room Host or a Player client. It is the loopback-only
administration UI: it logs in with a normal platform account that has
``is_admin`` and drives the same Admin commands the server exposes on
``/admin/ws``. All data access happens on the server through
PlatformDatabase / catalog / RoomManager; this client never opens SQLite.
"""

import argparse
import asyncio
import json

from rich.table import Table
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Footer, Header, Input, Label, RichLog, Static
from websockets.asyncio.client import connect

DEFAULT_URI = "ws://127.0.0.1:8080/admin/ws"

COMMAND_HELP = (
    "用户   users / user <用户名> / create-user / delete-user <用户名>\n"
    "       set-admin <用户名> / unset-admin <用户名>\n"
    "剧本   templates / template <id> / rename-template <id> <新名称>\n"
    "       set-template-public <id> true|false / delete-template <id> / import-template\n"
    "房间   rooms / room <房间码> / close-room <房间码>\n"
    "其他   /help 帮助    /quit 退出"
)


class AdminClientError(Exception):
    """Raised for a rejected login or a failed Admin command."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(detail)
        self.code = code
        self.detail = detail


class AdminClient:
    """Thin request/response client for the loopback Admin WebSocket."""

    def __init__(self, uri: str = DEFAULT_URI) -> None:
        self.uri = uri
        self.connection = None
        self.user: dict | None = None

    async def connect(self) -> None:
        self.connection = await connect(self.uri, max_size=4 * 1024 * 1024)

    async def close(self) -> None:
        if self.connection is not None:
            await self.connection.close()
            self.connection = None

    async def _exchange(self, message: dict) -> dict:
        if self.connection is None:
            raise AdminClientError("disconnected", "尚未连接到 Admin endpoint")
        await self.connection.send(json.dumps(message, ensure_ascii=False))
        while True:
            raw = await self.connection.recv()
            response = json.loads(raw)
            kind = response.get("type")
            if kind == "ok":
                return response.get("data") or {}
            if kind == "error":
                raise AdminClientError(
                    str(response.get("code", "error")),
                    str(response.get("detail", "命令失败")),
                )

    async def login(self, username: str, password: str) -> dict:
        if self.connection is None:
            raise AdminClientError("disconnected", "尚未连接到 Admin endpoint")
        await self.connection.send(json.dumps(
            {"type": "login", "username": username, "password": password},
            ensure_ascii=False,
        ))
        response = json.loads(await self.connection.recv())
        if response.get("type") == "error":
            await self.close()
            raise AdminClientError(
                str(response.get("code", "error")),
                str(response.get("detail", "登录失败")),
            )
        self.user = response.get("user")
        return self.user or {}

    async def command(self, command_name: str, **payload) -> dict:
        # ``command_name`` is not ``name`` because some commands carry a ``name``
        # payload key (e.g. import-template / rename-template).
        return await self._exchange({"type": command_name, **payload})


class LoginScreen(Screen):
    """Admin login: an ordinary platform account with is_admin."""

    BINDINGS = [Binding("ctrl+c", "quit", "退出")]

    def __init__(self, client: AdminClient) -> None:
        super().__init__()
        self.client = client
        self.status_text = "使用本机平台管理员账号登录。"

    def set_status(self, text: str) -> None:
        self.status_text = text
        self.query_one("#login-status", Static).update(text)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with Vertical(id="login-box"):
            yield Static("AI RP Platform Admin", id="login-title")
            yield Label("用户名")
            yield Input(placeholder="用户名", id="login-username")
            yield Label("密码")
            yield Input(placeholder="密码", password=True, id="login-password")
            yield Button("登录", id="login-button", variant="primary")
            yield Static("使用本机平台管理员账号登录。", id="login-status")
        yield Footer()

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "login-button":
            await self.submit()

    async def on_input_submitted(self, _event: Input.Submitted) -> None:
        await self.submit()

    async def submit(self) -> None:
        username = self.query_one("#login-username", Input).value.strip()
        password = self.query_one("#login-password", Input).value
        if not username or not password:
            self.set_status("请填写用户名和密码。")
            return
        self.set_status("正在登录…")
        try:
            user = await self.client.login(username, password)
        except AdminClientError as exc:
            self.set_status(f"登录失败：{exc.detail}")
            return
        except OSError as exc:
            self.set_status(f"无法连接 Admin endpoint：{exc}")
            return
        self.app.push_screen(AdminScreen(self.client, user))


class AdminScreen(Screen):
    """Users / Templates / Rooms console with a shared command line."""

    BINDINGS = [
        Binding("f1", "tab_users", "用户"),
        Binding("f2", "tab_templates", "剧本"),
        Binding("f3", "tab_rooms", "房间"),
        Binding("ctrl+c", "quit", "退出"),
    ]

    def __init__(self, client: AdminClient, user: dict) -> None:
        super().__init__()
        self.client = client
        self.user = user
        self.wizard: list[dict] | None = None
        self.wizard_values: dict[str, str] = {}
        self.wizard_command = ""
        self.confirm: tuple[str, dict, str] | None = None
        self.status_text = ""

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        yield Static(
            f"平台管理员：{self.user.get('username', '?')}", id="admin-identity"
        )
        with Horizontal(id="admin-tabs"):
            yield Button("用户", id="tab-users")
            yield Button("剧本", id="tab-templates")
            yield Button("房间", id="tab-rooms")
        yield RichLog(id="admin-content", wrap=True, markup=False)
        yield Static("", id="admin-status")
        yield Input(placeholder="> users / templates / rooms / /help", id="admin-command")
        yield Footer()

    async def on_mount(self) -> None:
        self.write_line("欢迎使用 AI RP Platform Admin。输入 /help 查看命令。")
        await self.run_command("users")

    # --- output helpers ------------------------------------------------------

    def content(self) -> RichLog:
        return self.query_one("#admin-content", RichLog)

    def write_line(self, text: str) -> None:
        self.content().write(text)

    def write_table(self, title: str, columns: list[str], rows: list[list[str]]) -> None:
        table = Table(title=title, expand=False)
        for column in columns:
            table.add_column(column)
        if not rows:
            self.write_line(f"{title}：暂无数据。")
            return
        for row in rows:
            table.add_row(*row)
        self.content().write(table)

    def set_status(self, text: str) -> None:
        self.status_text = text
        self.query_one("#admin-status", Static).update(text)

    # --- tabs and command line ----------------------------------------------

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        mapping = {
            "tab-users": "users", "tab-templates": "templates", "tab-rooms": "rooms",
        }
        if event.button.id in mapping:
            await self.run_command(mapping[event.button.id])

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        value = event.value.strip()
        container = self.query_one("#admin-command", Input)
        if self.confirm is not None:
            container.value = ""
            await self.resolve_confirm(value)
            return
        if self.wizard is not None:
            container.value = ""
            await self.wizard_input(value)
            return
        container.value = ""
        if not value:
            return
        if value in ("/quit", "/exit"):
            self.app.exit()
            return
        if value == "/help":
            self.write_line(COMMAND_HELP)
            return
        await self.dispatch(value)

    async def run_command(self, command_name: str, **payload) -> None:
        try:
            data = await self.client.command(command_name, **payload)
        except AdminClientError as exc:
            self.set_status(f"失败：{exc.detail}")
            self.write_line(f"[错误] {exc.detail}")
            return
        except OSError as exc:
            self.set_status(f"连接失败：{exc}")
            return
        self.set_status("")
        self.render_result(command_name, data)

    async def dispatch(self, value: str) -> None:
        parts = value.split()
        command, args = parts[0], parts[1:]
        try:
            if command == "users":
                await self.run_command("users")
            elif command == "user" and len(args) == 1:
                await self.run_command("user", username=args[0])
            elif command == "create-user" and not args:
                self.start_wizard("create-user", [
                    {"field": "username", "label": "用户名"},
                    {"field": "password", "label": "密码", "password": True},
                    {"field": "repeat", "label": "重复密码", "password": True},
                ])
            elif command == "delete-user" and len(args) == 1:
                self.ask_confirm("delete-user", {"username": args[0]},
                                 f"确认删除用户 {args[0]}？[y/N]")
            elif command in ("set-admin", "unset-admin") and len(args) == 1:
                await self.run_command(command, username=args[0])
            elif command == "templates":
                await self.run_command("templates")
            elif command == "template" and len(args) == 1:
                await self.run_command("template", id=args[0])
            elif command == "rename-template" and len(args) >= 2:
                await self.run_command("rename-template", id=args[0],
                                       name=" ".join(args[1:]))
            elif command == "set-template-public" and len(args) == 2:
                flag = args[1].strip().lower() in ("1", "true", "yes", "y", "公开")
                await self.run_command("set-template-public", id=args[0], public=flag)
            elif command == "delete-template" and len(args) == 1:
                self.ask_confirm("delete-template", {"id": args[0]},
                                 f"确认删除剧本 {args[0]}？已存在的存档不会受影响。[y/N]")
            elif command == "import-template" and not args:
                self.start_wizard("import-template", [
                    {"field": "source", "label": "源目录"},
                    {"field": "owner", "label": "Owner 用户名"},
                    {"field": "name", "label": "显示名称（可留空）", "optional": True},
                    {"field": "public", "label": "公开？y/n"},
                ])
            elif command == "rooms":
                await self.run_command("rooms")
            elif command == "room" and len(args) == 1:
                await self.run_command("room", code=args[0].upper())
            elif command == "close-room" and len(args) == 1:
                code = args[0].upper()
                self.ask_confirm("close-room", {"code": code},
                                 f"确认关闭房间 {code}？[y/N]")
            else:
                self.set_status("命令无效；输入 /help 查看可用命令。")
        except IndexError:
            self.set_status("参数不足；输入 /help 查看用法。")

    # --- destructive confirmation -------------------------------------------

    def ask_confirm(self, command: str, payload: dict, question: str) -> None:
        self.confirm = (command, payload, question)
        self.set_status(question)

    async def resolve_confirm(self, answer: str) -> None:
        pending, self.confirm = self.confirm, None
        assert pending is not None
        command, payload, _ = pending
        if answer.strip().lower() not in ("y", "yes", "是"):
            self.set_status("已取消。")
            return
        await self.run_command(command, **payload)

    # --- multi-step prompts -------------------------------------------------

    def start_wizard(self, command: str, steps: list[dict]) -> None:
        self.wizard = list(steps)
        self.wizard_values = {}
        self.wizard_command = command
        self.advance_wizard()

    def advance_wizard(self) -> None:
        if self.wizard:
            step = self.wizard[0]
            optional = "（可留空）" if step.get("optional") else ""
            self.set_status(f"{step['label']}{optional}：")
            command_input = self.query_one("#admin-command", Input)
            command_input.password = bool(step.get("password"))
            command_input.placeholder = step["label"]
        else:
            self.finish_wizard()

    async def wizard_input(self, value: str) -> None:
        assert self.wizard is not None
        step = self.wizard.pop(0)
        if not value and not step.get("optional"):
            self.set_status(f"{step['label']}不能为空。")
            self.wizard.insert(0, step)
            return
        self.wizard_values[step["field"]] = value
        self.advance_wizard()

    def finish_wizard(self) -> None:
        command_input = self.query_one("#admin-command", Input)
        command_input.password = False
        command_input.placeholder = "> users / templates / rooms / /help"
        command = self.wizard_command
        values = self.wizard_values
        self.wizard, self.wizard_values, self.wizard_command = None, {}, ""
        if command == "create-user":
            if values["password"] != values["repeat"]:
                self.set_status("两次输入的密码不一致。")
                return
            asyncio.create_task(self.run_command(
                "create-user",
                username=values["username"], password=values["password"],
            ))
            return
        if command == "import-template":
            asyncio.create_task(self.run_command(
                "import-template",
                source=values["source"],
                owner=values["owner"],
                name=values["name"],
                public=values["public"].strip().lower() in ("y", "yes", "1", "true", "是"),
            ))

    # --- rendering ----------------------------------------------------------

    def render_result(self, command: str, data: dict) -> None:
        if command == "users":
            self.write_table(
                "用户",
                ["ID", "用户名", "管理员", "创建时间"],
                [
                    [str(u["id"]), u["username"], "是" if u["is_admin"] else "否",
                     str(u["created_at"])[:19]]
                    for u in data.get("users", [])
                ],
            )
        elif command == "user":
            self.write_line(
                f"用户 {data['username']}（ID {data['id']}）\n"
                f"管理员：{'是' if data['is_admin'] else '否'}\n"
                f"创建时间：{str(data['created_at'])[:19]}\n"
                f"拥有：{data['templates']} Templates / {data['games']} Games / "
                f"{data['rooms']} Active Room"
            )
        elif command == "create-user":
            self.write_line(f"已创建用户 {data['username']}（ID {data['id']}）。")
        elif command in ("set-admin", "unset-admin"):
            state = "平台管理员" if data["is_admin"] else "普通用户"
            self.write_line(f"{data['username']} 现在是{state}。")
        elif command == "delete-user":
            self.write_line(f"已删除用户 {data['username']}。")
        elif command == "templates":
            self.write_table(
                "剧本",
                ["ID", "名称", "Owner", "公开", "角色"],
                [
                    [t["id"], t["name"], t["owner_username"],
                     "是" if t["is_public"] else "否", " / ".join(t.get("roles") or [])]
                    for t in data.get("templates", [])
                ],
            )
        elif command in ("template", "rename-template", "set-template-public",
                         "import-template"):
            roles = " / ".join(data.get("roles") or []) or "（未读取）"
            self.write_line(
                f"剧本 {data['id']}\n"
                f"名称：{data['name']}\nOwner：{data['owner_username']}\n"
                f"公开：{'是' if data['is_public'] else '否'}\n角色：{roles}"
            )
        elif command == "delete-template":
            self.write_line(f"已删除剧本 {data['id']}；已有存档继续正常运行。")
        elif command == "rooms":
            self.write_table(
                "活跃房间",
                ["房间码", "存档", "房主", "在线", "密码", "回合"],
                [
                    [r["code"], r["game_name"], r["owner_username"],
                     f"{r['connected_count']}/{r['role_count']}",
                     "有" if r["has_password"] else "无", str(r["round"])]
                    for r in data.get("rooms", [])
                ],
            )
        elif command == "room":
            assignments = "，".join(
                f"{role}→{name}" for role, name in (data.get("assignments") or {}).items()
            ) or "（无）"
            self.write_line(
                f"房间 {data['code']}\n"
                f"房主：{data['owner_username']}\n"
                f"存档：{data['game_name']}（{data['game_id']}）\n"
                f"需要密码：{'是' if data['has_password'] else '否'}\n"
                f"在线：{data['connected_count']}/{data['role_count']}\n"
                f"角色分配：{assignments}\n"
                f"当前回合：{data['round']}\n"
                f"处理状态：{data['stage']}"
            )
        elif command == "close-room":
            self.write_line(f"已关闭房间 {data['code']}，用户已断开，存档保留。")
        else:
            self.write_line(json.dumps(data, ensure_ascii=False, indent=2))

    def action_tab_users(self) -> None:
        asyncio.create_task(self.run_command("users"))

    def action_tab_templates(self) -> None:
        asyncio.create_task(self.run_command("templates"))

    def action_tab_rooms(self) -> None:
        asyncio.create_task(self.run_command("rooms"))


class AdminApp(App):
    """Platform Admin Console application shell."""

    CSS = """
    Screen { layout: vertical; }
    #login-box { width: 60; height: auto; padding: 2 4; border: round $primary; }
    #login-title { text-style: bold; padding-bottom: 1; }
    #login-status { padding-top: 1; color: $text-muted; }
    #admin-identity { padding: 0 1; color: $text-muted; }
    #admin-tabs { height: 3; padding: 0 1; }
    #admin-tabs Button { margin-right: 1; min-width: 10; }
    #admin-content { height: 1fr; padding: 0 1; border: round $panel; }
    #admin-status { height: auto; padding: 0 1; color: $warning; }
    #admin-command { margin: 0 1; }
    """
    TITLE = "AI RP Platform Admin"

    def __init__(self, client: AdminClient | None = None) -> None:
        super().__init__()
        self.client = client or AdminClient()

    def on_mount(self) -> None:
        self.push_screen(LoginScreen(self.client))

    async def on_unmount(self) -> None:
        await self.client.close()


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI RP Platform Admin Console")
    parser.add_argument(
        "--uri", default=DEFAULT_URI,
        help="loopback Admin WebSocket (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    AdminApp(AdminClient(args.uri)).run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
