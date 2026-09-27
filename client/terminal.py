import argparse
import asyncio
import json
import os
import time
from pathlib import Path

from rich.panel import Panel
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input, RichLog, Static, TextArea
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidURI

try:
    from client.display import room_message_text, to_yaml
except ModuleNotFoundError:  # Supports: python client/terminal.py
    from display import room_message_text, to_yaml


PROTOCOL_VERSION = 2

STATUS_LABELS = {
    "LOBBY": ("大厅等待", "grey50"),
    "EDITING": ("编辑中", "yellow"),
    "READY": ("已提交", "green"),
    "PAUSED": ("已暂停", "red"),
    "PROCESSING": ("处理中", "blue"),
}

PROCESSING_LABELS = {
    "WORLD_UPDATING": "世界更新中",
    "VIEW_GENERATING": "视角补全中",
    "NARRATION_GENERATING": "文段生成中",
}

PLAYER_DRAFT_HINT = "当前角色行动；也可配合 /chat 发送房间聊天"
SPECTATOR_DRAFT_HINT = "房间聊天内容（使用 /chat 发送）"
PLAYER_COMMAND_HINT = "/submit /cancel /pause /resume /status /chat /help /quit"
SPECTATOR_COMMAND_HINT = "/chat /view A /view B /help /quit"

PLAYER_HELP = """玩家命令：
/submit  提交当前 Draft 作为角色行动
/cancel  撤销提交并继续编辑
/pause   暂停当前角色
/resume  恢复当前角色
/status  显示当前角色状态栏
/chat    将当前 Draft 发送到房间聊天
/help    显示本帮助
/quit    退出客户端"""

SPECTATOR_HELP = """观众命令：
/chat    将当前 Draft 发送到房间聊天
/view A  查看 Player A 历史
/view B  查看 Player B 历史
/help    显示本帮助
/quit    退出客户端"""


class GameApp(App):
    CSS_PATH = Path(__file__).with_name("tui.css")
    TITLE = "AI RP Engine"
    BINDINGS = [
        Binding("pageup", "history_page_up", show=False, priority=True),
        Binding("pagedown", "history_page_down", show=False, priority=True),
    ]

    def __init__(self, uri: str, nickname: str, room_key: str = "") -> None:
        super().__init__()
        self.uri = uri
        self.nickname = nickname
        self.room_key = room_key
        self.websocket = None
        self.role: str | None = None
        self.view_role: str | None = None
        self.scenario = ""
        self.draft = ""
        self.round_number: int | None = None
        self.round_status = "LOBBY"
        self.draft_initialized = False
        self.draft_revision = 0
        self.player_states: dict = {}
        self.presence: list[dict] = []
        self.processing_stage: str | None = None
        self.processing_started = 0.0

    def compose(self) -> ComposeResult:
        yield Static("正在连接服务器…", id="identity")
        yield RichLog(id="history", wrap=True, markup=False, auto_scroll=False)
        yield Static("A ■ 无人扮演        B ■ 无人扮演", id="collaboration")
        yield Static("Draft：", id="draft-label")
        yield TextArea(
            "",
            placeholder=SPECTATOR_DRAFT_HINT,
            id="draft-editor",
            read_only=True,
            show_cursor=False,
        )
        yield Static("Command：", id="command-label")
        yield Input(
            placeholder=SPECTATOR_COMMAND_HINT,
            id="command-input",
            disabled=True,
        )

    def on_mount(self) -> None:
        self.set_interval(1, self._refresh_processing)
        self.connect_to_server()

    @work(exclusive=True)
    async def connect_to_server(self) -> None:
        try:
            async with connect(self.uri) as websocket:
                self.websocket = websocket
                await websocket.send(json.dumps({
                    "type": "join", "name": self.nickname, "room_key": self.room_key
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
            if message.get("protocol_version", 1) != PROTOCOL_VERSION:
                raise ValueError(
                    f"不支持的协议版本：{message.get('protocol_version')}（客户端支持 {PROTOCOL_VERSION}）"
                )
            self.role = message.get("role")
            self.view_role = message.get("view_role")
            self.scenario = message["scenario"]
            self._update_identity()
            self._enable_commands()
            self._set_editor_status("LOBBY")
            return
        if message_type == "identity_changed":
            self.role = message.get("role")
            self.view_role = message.get("view_role")
            self._reset_local_view()
            self._update_identity()
            return
        if message_type == "role_assigned":
            return
        if message_type == "presence":
            self.presence = message.get("users", [])
            return
        if message_type == "state":
            self._apply_round_state(message)
            return
        if message_type == "processing_stage":
            self._set_processing_stage(str(message.get("stage", "")))
            return
        if message_type == "role_view":
            self._load_role_view(message)
            return
        if message_type == "role_round":
            if message.get("role") == self.view_role:
                self._append_live_round(message.get("entries", []))
            return
        if message_type == "room_message":
            self._append(room_message_text(message))
            return
        if message_type == "status":
            self._apply_status(message)
            self._append(self._statusbar_panel(message.get("statusbar", {}), "当前状态栏"))
            return
        if message_type == "round_complete":
            self._append(Text(f"第 {message['round']} 回合完成。", style="dim"))
            return
        if message_type == "system":  # Legacy server compatibility.
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
                await self._send({"type": "leave"})
                await self.websocket.close()
            self.exit()
            return
        if command == "/help":
            self._show_help()
            return
        if command == "/chat":
            await self._chat_draft()
            return
        if self.role is None:
            parts = command.split()
            if len(parts) == 2 and parts[0] == "/view" and parts[1].upper() in ("A", "B"):
                await self._send({"type": "view", "role": parts[1].upper()})
                return
            self._append(Text("观众命令无效；输入 /help 查看可用命令。", style="yellow"))
            return
        if command == "/submit":
            await self._submit_draft()
            return
        if command == "/cancel":
            await self._cancel_submit()
            return
        commands = {
            "/pause": {"type": "pause"},
            "/resume": {"type": "resume"},
            "/status": {"type": "status"},
        }
        message = commands.get(command)
        if message is None:
            self._append(Text(
                "玩家命令无效；输入 /help 查看可用命令。",
                style="yellow",
            ))
            return
        if command == "/pause":
            await self._sync_draft_now()
        await self._send(message)

    @work(exclusive=True, group="draft-sync")
    async def sync_draft_later(self, revision: int) -> None:
        await asyncio.sleep(0.2)
        if revision == self.draft_revision and self.round_status == "EDITING" and self.role:
            await self._send({"type": "action", "text": self._draft_editor().text})

    async def _sync_draft_now(self) -> None:
        if self.role and self.round_status == "EDITING":
            self.draft_revision += 1
            self.draft = self._draft_editor().text
            await self._send({"type": "action", "text": self.draft})

    async def _chat_draft(self) -> None:
        editor = self._draft_editor()
        if editor.read_only:
            self._append(Text("当前 Draft 为只读，暂时不能发送房间聊天。", style="yellow"))
            return
        text = editor.text.strip()
        if not text:
            self._append(Text("Draft 不能为空。", style="yellow"))
            return
        await self._send({"type": "room_chat", "text": text})
        self.draft_revision += 1
        editor.load_text("")
        self.draft = ""

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

    def action_history_page_up(self) -> None:
        self.query_one("#history", RichLog).action_page_up()

    def action_history_page_down(self) -> None:
        self.query_one("#history", RichLog).action_page_down()

    def _update_identity(self) -> None:
        identity = f"Player {self.role}" if self.role else "Spectator"
        view = f"View: {self.view_role}" if self.view_role else "未选择角色视图"
        self.query_one("#identity", Static).update(
            f"Scenario: {self.scenario}    昵称: {self.nickname}    {identity}    {view}"
        )
        self._update_input_hints()

    def _update_input_hints(self) -> None:
        if self.role:
            draft_hint = PLAYER_DRAFT_HINT
            command_hint = PLAYER_COMMAND_HINT
        else:
            draft_hint = SPECTATOR_DRAFT_HINT
            command_hint = SPECTATOR_COMMAND_HINT
        self._draft_editor().placeholder = draft_hint
        self.query_one("#command-input", Input).placeholder = command_hint

    def _show_help(self) -> None:
        content = PLAYER_HELP if self.role else SPECTATOR_HELP
        self._append(Panel(content, title="命令帮助", border_style="cyan"))

    def _enable_commands(self) -> None:
        self.query_one("#command-input", Input).disabled = False

    def _apply_round_state(self, message: dict) -> None:
        round_number = int(message["round"])
        if self.role and self.round_number is not None and round_number > self.round_number:
            self.draft_revision += 1
            self._draft_editor().load_text("")
            self.draft = ""
            self.draft_initialized = True
        self.round_number = round_number
        self.player_states = message["players"]
        stage = str(message.get("stage", ""))
        if stage in PROCESSING_LABELS:
            self._set_processing_stage(stage)
        elif not any(
            value.get("status") == "PROCESSING"
            for value in self.player_states.values()
            if isinstance(value, dict)
        ):
            self.processing_stage = None
        self._update_collaboration(self.player_states)
        if self.role:
            value = self.player_states.get(self.role, {})
            self._set_editor_status(value.get("status", "EDITING"))
        else:
            self._set_editor_status("LOBBY")

    def _apply_status(self, message: dict) -> None:
        self._apply_round_state({
            "round": message["round"],
            "stage": message.get("stage", ""),
            "players": message["players"],
        })
        draft = str(message.get("draft", ""))
        if self._draft_editor().text != draft:
            self.draft_revision += 1
            self._draft_editor().load_text(draft)
        self.draft = draft
        self.draft_initialized = True

    def _set_editor_status(self, status: str) -> None:
        self.round_status = status
        editable = self.role is None or status == "EDITING"
        editor = self._draft_editor()
        editor.read_only = not editable
        editor.show_cursor = editable

    def _draft_editor(self) -> TextArea:
        return self.query_one("#draft-editor", TextArea)

    def _update_collaboration(self, players: dict) -> None:
        line = Text()
        for index, role in enumerate(("A", "B")):
            value = players.get(role, {})
            status = value.get("status", "EDITING") if isinstance(value, dict) else value
            connected = value.get("connected", False) if isinstance(value, dict) else False
            if not connected:
                label, color = "无人扮演", "grey50"
            elif status == "PROCESSING" and self.processing_stage in PROCESSING_LABELS:
                elapsed = max(0, int(time.monotonic() - self.processing_started))
                label = f"{PROCESSING_LABELS[self.processing_stage]} · {elapsed}s"
                color = "blue"
            else:
                label, color = STATUS_LABELS.get(status, (status, "white"))
            if index:
                line.append("          ")
            line.append(f"{role} ■ {label}", style=color)
        self.query_one("#collaboration", Static).update(line)

    def _set_processing_stage(self, stage: str) -> None:
        if stage in PROCESSING_LABELS:
            self.processing_stage = stage
            self.processing_started = time.monotonic()
        elif stage in ("WAITING_INPUT", "FINISHED"):
            self.processing_stage = None
        if self.player_states:
            self._update_collaboration(self.player_states)

    def _refresh_processing(self) -> None:
        if self.processing_stage and self.player_states:
            self._update_collaboration(self.player_states)

    def _reset_local_view(self) -> None:
        self.query_one("#history", RichLog).clear()
        self.draft_revision += 1
        self._draft_editor().load_text("")
        self.draft = ""
        self.draft_initialized = False
        self._set_editor_status("LOBBY")

    def _load_role_view(self, message: dict) -> None:
        self.view_role = message.get("role")
        history = self.query_one("#history", RichLog)
        history.clear()
        entries = message.get("history", [])
        for item in entries:
            self._write_role_entry(history, int(item["round"]), item)
        if not entries:
            history.write(Text("该角色尚无历史。", style="dim"), scroll_end=False)
        history.scroll_end(animate=False)
        if "draft" in message:
            self.draft_revision += 1
            self._draft_editor().load_text(str(message["draft"]))
            self.draft = str(message["draft"])
            self.draft_initialized = True
        self._update_identity()

    def _append_live_round(self, entries: list[dict]) -> None:
        history = self.query_one("#history", RichLog)
        at_bottom = history.is_vertical_scroll_end
        titles = {
            "action": "本轮行动",
            "narration": "本轮输出",
            "statusbar": "本轮状态栏",
        }
        for item in entries:
            kind = item.get("kind")
            if kind == "statusbar":
                history.write(
                    self._statusbar_panel(item.get("content", {}), titles[kind]),
                    scroll_end=at_bottom,
                )
            elif kind in titles:
                history.write(
                    Panel(str(item.get("content", "")), title=titles[kind]),
                    scroll_end=at_bottom,
                )

    @staticmethod
    def _write_role_entry(
        history: RichLog,
        round_id: int,
        item: dict,
        *,
        scroll_end: bool = False,
    ) -> None:
        kind = item.get("kind")
        titles = {
            "action": f"第 {round_id} 轮行动",
            "narration": f"第 {round_id} 轮输出",
            "statusbar": f"第 {round_id} 轮状态栏",
        }
        if kind == "statusbar":
            history.write(
                GameApp._statusbar_panel(item.get("content", {}), titles[kind]),
                scroll_end=scroll_end,
            )
        elif kind in titles:
            history.write(
                Panel(str(item.get("content", "")), title=titles[kind]),
                scroll_end=scroll_end,
            )

    @staticmethod
    def _statusbar_panel(value, title: str) -> Panel:
        return Panel(to_yaml(value), title=title, border_style="green")

    def _append(self, renderable) -> None:
        history = self.query_one("#history", RichLog)
        at_bottom = history.is_vertical_scroll_end
        history.write(renderable, scroll_end=at_bottom)

    async def _send(self, message: dict) -> None:
        if self.websocket is None:
            self._append(Text("尚未连接服务器。", style="red"))
            return
        await self.websocket.send(json.dumps(message, ensure_ascii=False))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI RP Engine Textual client")
    parser.add_argument(
        "--uri",
        default=os.getenv("SERVER_URI", "ws://127.0.0.1:8080/ws"),
        help="WebSocket server URI",
    )
    parser.add_argument("--name", help="nickname for this connection")
    parser.add_argument(
        "--room-key",
        default=os.getenv("ROOM_KEY", ""),
        help="shared room key for the public endpoint",
    )
    return parser.parse_args()


def main() -> None:
    arguments = parse_args()
    nickname = (arguments.name or input("请输入昵称：\n> ")).strip()
    if not nickname:
        raise SystemExit("昵称不能为空")
    room_key = arguments.room_key.strip()
    GameApp(arguments.uri, nickname, room_key).run()


if __name__ == "__main__":
    main()
