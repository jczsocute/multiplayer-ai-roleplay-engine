import argparse
import asyncio
import json
import os
from pathlib import Path

from rich.console import Group
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Input, RichLog, Select, Static, TextArea
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidURI

try:
    from client.protocol import parse_input
except ModuleNotFoundError:  # Supports: python client/terminal.py
    from protocol import parse_input


STATUS_LABELS = {
    "LOBBY": ("大厅等待", "grey50"),
    "EDITING": ("编辑中", "yellow"),
    "READY": ("已提交", "green"),
    "PAUSED": ("已暂停", "red"),
    "PROCESSING": ("处理中", "blue"),
}


class GameApp(App):
    CSS_PATH = Path(__file__).with_name("tui.css")
    TITLE = "AI RP Engine"
    BINDINGS = [
        Binding("pageup", "history_page_up", show=False, priority=True),
        Binding("pagedown", "history_page_down", show=False, priority=True),
        Binding("ctrl+enter", "submit_draft", "提交", priority=True),
        Binding("ctrl+e", "edit_draft", "重新编辑", priority=True),
    ]

    def __init__(self, uri: str, nickname: str) -> None:
        super().__init__()
        self.uri = uri
        self.nickname = nickname
        self.websocket = None
        self.is_host = False
        self.role: str | None = None
        self.scenario = ""
        self.draft = ""
        self.round_number: int | None = None
        self.round_status = "LOBBY"
        self.draft_initialized = False
        self.draft_revision = 0
        self.narration_rounds: set[int] = set()

    def compose(self) -> ComposeResult:
        yield Static("正在连接服务器…", id="identity")
        yield RichLog(
            id="history",
            wrap=True,
            markup=False,
            auto_scroll=False,
            min_width=20,
        )
        with Vertical(id="lobby"):
            yield Static("等待另一名玩家加入…", id="lobby-info")
            with Horizontal(id="assignment-controls"):
                yield Select([], prompt="选择角色分配", id="role-select")
                yield Button("确认分配", id="assign-role", variant="primary")
        yield Static("A ■ 大厅等待        B ■ 大厅等待", id="collaboration")
        yield TextArea(
            "",
            placeholder="当前回合行动 Draft",
            id="draft-editor",
            read_only=True,
            show_cursor=False,
        )
        yield Static(
            "Enter 换行   Ctrl+Enter 提交   Ctrl+E 重新编辑",
            id="shortcut-help",
        )
        yield Input(
            placeholder="兼容命令：/submit /cancel /pause /resume /status /retry /quit",
            id="command-input",
            disabled=True,
        )

    def on_mount(self) -> None:
        self.connect_to_server()

    @work(exclusive=True)
    async def connect_to_server(self) -> None:
        try:
            async with connect(self.uri) as websocket:
                self.websocket = websocket
                await websocket.send(json.dumps({
                    "type": "join", "name": self.nickname
                }, ensure_ascii=False))
                async for raw_message in websocket:
                    await self._handle_message(json.loads(raw_message))
        except (ConnectionClosed, InvalidURI, OSError, ValueError) as exc:
            self._append(Panel(f"连接已关闭：{exc}", title="连接", border_style="red"))
        finally:
            self.websocket = None

    async def _handle_message(self, message: dict) -> None:
        message_type = message.get("type")
        if message_type == "joined":
            self.is_host = bool(message["is_host"])
            self.role = message.get("role")
            self.scenario = message["scenario"]
            self._update_identity()
            if self.role:
                self._enable_commands()
            return
        if message_type == "lobby":
            self._update_lobby(message)
            return
        if message_type == "role_assigned":
            self.role = message["assignments"].get(self.nickname)
            self._update_identity()
            self.query_one("#lobby").display = False
            self._enable_commands()
            await self._send({"type": "status"})
            return
        if message_type == "state":
            self._apply_round_state(message)
            return
        if message_type == "history":
            self._load_history(message.get("messages", []))
            return
        if message_type == "narration":
            round_id = int(message["round"])
            if round_id not in self.narration_rounds:
                self.narration_rounds.add(round_id)
                self._append_scene(message, f"第 {round_id} 回合")
            await self._send({"type": "ack", "round_id": round_id})
            return
        if message_type == "last_scene":
            round_id = int(message["round"])
            if round_id not in self.narration_rounds:
                self.narration_rounds.add(round_id)
                self._append_scene(message, "最近场景")
            return
        if message_type == "status":
            self._apply_status(message)
            self._append_status(message)
            return
        if message_type == "round_complete":
            self._append(Text(f"第 {message['round']} 回合完成。", style="dim"))
            return
        if message_type == "system":
            self._append(Text(f"[系统] {message.get('text', '')}", style="dim cyan"))
            return
        if message_type in ("error", "notice"):
            text = message.get("detail") or message.get("text", "")
            style = "red" if message_type == "error" else "cyan"
            self._append(Panel(text, border_style=style))

    def on_text_area_changed(self, event: TextArea.Changed) -> None:
        if event.text_area.id != "draft-editor":
            return
        self.draft = event.text_area.text
        self.draft_revision += 1
        if self.round_status == "EDITING" and self.role:
            self.sync_draft_later(self.draft_revision)

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        command = event.value.strip()
        if not command:
            return
        event.input.clear()
        if command == "/quit":
            await self._sync_draft_now()
            if self.websocket is not None:
                await self.websocket.close()
            self.exit()
            return
        if command == "/history":
            self._append(Text("历史记录可直接在上方区域滚动查看。", style="cyan"))
            return
        if self.role is None:
            self._append(Text("请等待 Host 完成角色分配。", style="yellow"))
            return
        if command == "/submit":
            await self._submit_draft()
            return
        if command == "/cancel":
            await self._cancel_submit()
            return
        message = parse_input(command)
        if message["type"] == "action":
            self._append(Text("命令栏只接受以 / 开头的兼容命令。", style="yellow"))
            return
        if command == "/pause":
            await self._sync_draft_now()
        await self._send(message)

    async def action_submit_draft(self) -> None:
        await self._submit_draft()

    async def action_edit_draft(self) -> None:
        await self._cancel_submit()

    @work(exclusive=True, group="draft-sync")
    async def sync_draft_later(self, revision: int) -> None:
        await asyncio.sleep(0.2)
        if revision == self.draft_revision and self.round_status == "EDITING":
            await self._send({"type": "action", "text": self._draft_editor().text})

    async def _sync_draft_now(self) -> None:
        if self.role and self.round_status == "EDITING":
            self.draft_revision += 1
            self.draft = self._draft_editor().text
            await self._send({"type": "action", "text": self.draft})

    async def _submit_draft(self) -> None:
        if self.role is None or self.round_status != "EDITING":
            self._append(Text("只有 EDITING 状态可以提交 Draft。", style="yellow"))
            return
        if not self._draft_editor().text.strip():
            self._append(Text("Draft 不能为空。", style="yellow"))
            return
        await self._sync_draft_now()
        await self._send({"type": "submit"})

    async def _cancel_submit(self) -> None:
        if self.role is None or self.round_status != "READY":
            self._append(Text("只有 READY 状态可以重新编辑。", style="yellow"))
            return
        await self._send({"type": "cancel_submit"})

    async def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id != "assign-role":
            return
        selection = self.query_one("#role-select", Select).selection
        if selection not in ("A", "B"):
            self._append(Text("请选择一种角色分配方案。", style="yellow"))
            return
        await self._send({"type": "assign_roles", "host_role": selection})

    def action_history_page_up(self) -> None:
        self.query_one("#history", RichLog).action_page_up()

    def action_history_page_down(self) -> None:
        self.query_one("#history", RichLog).action_page_down()

    def _update_lobby(self, message: dict) -> None:
        participants = message.get("participants", [])
        if message.get("assigned"):
            self.query_one("#lobby").display = False
            return
        lobby = self.query_one("#lobby")
        lobby.display = True
        descriptions = []
        for participant in participants:
            suffix = "（Host）" if participant["is_host"] else ""
            connected = "在线" if participant["connected"] else "离线"
            descriptions.append(f"{participant['name']}{suffix} · {connected}")
        self.query_one("#lobby-info", Static).update(
            "Lobby：" + "  |  ".join(descriptions)
            if descriptions else "等待玩家加入…"
        )
        controls = self.query_one("#assignment-controls")
        can_assign = self.is_host and len(participants) == 2
        controls.display = can_assign
        if can_assign:
            host = next(item for item in participants if item["is_host"])
            other = next(item for item in participants if not item["is_host"])
            select = self.query_one("#role-select", Select)
            select.set_options([
                (f"{host['name']} → Player A；{other['name']} → Player B", "A"),
                (f"{host['name']} → Player B；{other['name']} → Player A", "B"),
            ])
            select.value = "A"
        lobby_players = {
            "A": {"status": "LOBBY"},
            "B": {"status": "LOBBY"},
        }
        self._update_collaboration(lobby_players)

    def _update_identity(self) -> None:
        role = f"Player {self.role}" if self.role else "等待角色分配"
        host = " · Host" if self.is_host else ""
        self.query_one("#identity", Static).update(
            f"Scenario: {self.scenario}    昵称: {self.nickname}{host}    角色: {role}"
        )

    def _enable_commands(self) -> None:
        command_input = self.query_one("#command-input", Input)
        command_input.disabled = False

    def _apply_round_state(self, message: dict) -> None:
        round_number = int(message["round"])
        if self.round_number is not None and round_number > self.round_number:
            self.draft_revision += 1
            self._draft_editor().load_text("")
            self.draft = ""
            self.draft_initialized = True
        self.round_number = round_number
        self._update_collaboration(message["players"])
        if self.role:
            value = message["players"].get(self.role, {})
            status = value.get("status", "LOBBY") if isinstance(value, dict) else value
            self._set_editor_status(status)

    def _apply_status(self, message: dict) -> None:
        self._apply_round_state({
            "round": message["round"],
            "players": message["players"],
        })
        if not self.draft_initialized or self.round_status != "EDITING":
            draft = str(message.get("draft", ""))
            if self._draft_editor().text != draft:
                self.draft_revision += 1
                self._draft_editor().load_text(draft)
            self.draft = draft
            self.draft_initialized = True

    def _set_editor_status(self, status: str) -> None:
        self.round_status = status
        editor = self._draft_editor()
        editable = status == "EDITING"
        editor.read_only = not editable
        editor.show_cursor = editable
        if editable:
            editor.focus()

    def _draft_editor(self) -> TextArea:
        return self.query_one("#draft-editor", TextArea)

    def _update_collaboration(self, players: dict) -> None:
        line = Text()
        for index, role in enumerate(("A", "B")):
            value = players.get(role, "LOBBY")
            status = value.get("status", "LOBBY") if isinstance(value, dict) else value
            connected = value.get("connected", True) if isinstance(value, dict) else True
            if status != "LOBBY" and not connected:
                label, color = "已离开", "grey50"
            else:
                label, color = STATUS_LABELS.get(status, (status, "white"))
            if index:
                line.append("          ")
            line.append(f"{role} ■ {label}", style=color)
        self.query_one("#collaboration", Static).update(line)

    def _load_history(self, messages: list[dict]) -> None:
        history = self.query_one("#history", RichLog)
        history.clear()
        self.narration_rounds.clear()
        if not messages:
            history.write(Text("故事尚未开始。", style="dim"), scroll_end=False)
        for item in messages:
            round_id = int(item["round"])
            if item["role"] == "narrator":
                self.narration_rounds.add(round_id)
                title = f"第 {round_id} 回合 · Narration"
            else:
                title = f"第 {round_id} 回合 · 你的行动"
            history.write(Panel(item["content"], title=title), scroll_end=False)
        history.scroll_end(animate=False)

    def _append_scene(self, message: dict, title: str) -> None:
        statusbar = self._pretty(message.get("statusbar", {}))
        public = self._pretty(message.get("public_information", {}))
        content = Group(
            Text(message.get("text", "")),
            Rule("角色状态栏"),
            Text(statusbar),
            Rule("公共世界信息"),
            Text(public),
        )
        self._append(Panel(content, title=title, border_style="cyan"))

    def _append_status(self, message: dict) -> None:
        players = "\n".join(
            f"{role}: {value.get('status', value) if isinstance(value, dict) else value}"
            for role, value in message["players"].items()
        )
        content = Group(
            Text(players),
            Rule("角色状态栏"),
            Text(self._pretty(message.get("statusbar", {}))),
            Rule("公共世界信息"),
            Text(self._pretty(message.get("public_information", {}))),
        )
        self._append(Panel(content, title="当前状态", border_style="green"))

    def _append(self, renderable) -> None:
        history = self.query_one("#history", RichLog)
        at_bottom = history.is_vertical_scroll_end
        history.write(renderable, scroll_end=at_bottom)

    async def _send(self, message: dict) -> None:
        if self.websocket is None:
            self._append(Text("尚未连接服务器。", style="red"))
            return
        await self.websocket.send(json.dumps(message, ensure_ascii=False))

    @staticmethod
    def _pretty(value) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False, indent=2)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI RP Engine Textual client")
    parser.add_argument(
        "--uri",
        default=os.getenv("SERVER_URI", "ws://127.0.0.1:8765"),
        help="WebSocket server URI",
    )
    parser.add_argument("--name", help="nickname used to reconnect to this game")
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    nickname = (arguments.name or input("请输入昵称：\n> ")).strip()
    if not nickname:
        raise SystemExit("昵称不能为空")
    GameApp(arguments.uri, nickname).run()


if __name__ == "__main__":
    main()
