import argparse
import json
import os
import shlex
import time
from pathlib import Path

from rich.panel import Panel
from rich.text import Text
from textual import work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input, RichLog, Static
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidURI

try:
    from client.display import to_yaml
except ModuleNotFoundError:  # Supports: python client/host.py
    from display import to_yaml


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


class HostApp(App):
    CSS_PATH = Path(__file__).with_name("host.css")
    TITLE = "AI RP Engine Host"
    BINDINGS = [
        Binding("pageup", "log_page_up", show=False, priority=True),
        Binding("pagedown", "log_page_down", show=False, priority=True),
    ]

    def __init__(self, uri: str) -> None:
        super().__init__()
        self.uri = uri
        self.websocket = None
        self.participants: list[dict] = []
        self.players: dict = {}
        self.processing_stage: str | None = None
        self.processing_started = 0.0

    def compose(self) -> ComposeResult:
        yield Static("正在连接服务器…", id="host-identity")
        yield RichLog(id="host-history", wrap=True, markup=False, auto_scroll=False)
        yield Static("Connected Players:\n（等待玩家）\n\nRoles:\nA: 未分配\nB: 未分配", id="host-players")
        yield Static("Processing: 空闲", id="host-processing")
        yield Input(
            placeholder="/assign <Player A 昵称> <Player B 昵称>  或  /quit",
            id="host-command",
        )

    def on_mount(self) -> None:
        self.set_interval(1, self._refresh_processing)
        self.connect_to_server()

    @work(exclusive=True)
    async def connect_to_server(self) -> None:
        try:
            async with connect(self.uri) as websocket:
                self.websocket = websocket
                await websocket.send(json.dumps({"type": "join_host"}))
                async for raw_message in websocket:
                    await self._handle_message(json.loads(raw_message))
        except (ConnectionClosed, InvalidURI, OSError, ValueError) as exc:
            self._append(Panel(f"连接已关闭：{exc}", border_style="red"))
        finally:
            self.websocket = None

    async def _handle_message(self, message: dict) -> None:
        message_type = message.get("type")
        if message_type == "host_joined":
            self.query_one("#host-identity", Static).update(
                f"Host · Scenario: {message.get('scenario', '')}"
            )
        elif message_type == "lobby":
            self.participants = message.get("participants", [])
            self._render_players()
        elif message_type == "role_assigned":
            assignments = message.get("assignments", {})
            for participant in self.participants:
                participant["role"] = assignments.get(participant["name"])
            self._render_players()
        elif message_type == "state":
            self.players = message.get("players", {})
            stage = str(message.get("stage", ""))
            if stage in PROCESSING_LABELS:
                self._set_processing_stage(stage)
            elif not any(
                value.get("status") == "PROCESSING"
                for value in self.players.values()
                if isinstance(value, dict)
            ):
                self.processing_stage = None
            self._render_players()
            self._refresh_processing()
        elif message_type == "processing_stage":
            self._set_processing_stage(str(message.get("stage", "")))
        elif message_type == "world_update":
            round_id = message.get("round", "?")
            self._append(Panel(
                Text(to_yaml(message.get("result", {}))),
                title=f"Round {round_id} World Update",
                border_style="cyan",
            ))
        elif message_type == "round_complete":
            self._append(Text(f"第 {message.get('round')} 回合完成。", style="dim"))
        elif message_type in ("error", "notice"):
            text = message.get("detail") or message.get("text", "")
            self._append(Panel(text, border_style="red" if message_type == "error" else "cyan"))

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        command = event.value.strip()
        event.input.clear()
        if not command:
            return
        if command == "/quit":
            if self.websocket is not None:
                await self.websocket.close()
            self.exit()
            return
        try:
            parts = shlex.split(command)
        except ValueError as exc:
            self._append(Text(f"命令格式错误：{exc}", style="yellow"))
            return
        if len(parts) == 3 and parts[0] == "/assign":
            await self._send({
                "type": "assign_roles",
                "player_a": parts[1],
                "player_b": parts[2],
            })
            return
        self._append(Text("可用命令：/assign <A昵称> <B昵称>、/quit", style="yellow"))

    def _render_players(self) -> None:
        connected_lines = [
            f"{item['name']} · {'在线' if item.get('connected') else '已离开'}"
            for item in self.participants
        ] or ["（等待玩家）"]
        text = Text("Connected Players:\n" + "\n".join(connected_lines) + "\n\nRoles:\n")
        for role in ("A", "B"):
            participant = next(
                (item for item in self.participants if item.get("role") == role), None
            )
            if participant is None:
                text.append(f"{role}: 未分配\n", style="grey50")
                continue
            state = self.players.get(role, {})
            status = state.get("status", "LOBBY")
            connected = participant.get("connected", False)
            if not connected:
                label, color = "已离开", "grey50"
            elif status == "PROCESSING" and self.processing_stage in PROCESSING_LABELS:
                elapsed = max(0, int(time.monotonic() - self.processing_started))
                label, color = f"{PROCESSING_LABELS[self.processing_stage]} · {elapsed}s", "blue"
            else:
                label, color = STATUS_LABELS.get(status, (status, "white"))
            text.append(f"{role} {participant['name']} ■ {label}\n", style=color)
        self.query_one("#host-players", Static).update(text)

    def _set_processing_stage(self, stage: str) -> None:
        if stage in PROCESSING_LABELS:
            self.processing_stage = stage
            self.processing_started = time.monotonic()
        elif stage in ("WAITING_INPUT", "FINISHED"):
            self.processing_stage = None
        self._render_players()
        self._refresh_processing()

    def _refresh_processing(self) -> None:
        if self.processing_stage in PROCESSING_LABELS:
            elapsed = max(0, int(time.monotonic() - self.processing_started))
            value = f"{PROCESSING_LABELS[self.processing_stage]} · {elapsed}s"
        else:
            value = "空闲"
        self.query_one("#host-processing", Static).update(f"Processing: {value}")
        if self.players:
            self._render_players()

    def action_log_page_up(self) -> None:
        self.query_one("#host-history", RichLog).action_page_up()

    def action_log_page_down(self) -> None:
        self.query_one("#host-history", RichLog).action_page_down()

    def _append(self, renderable) -> None:
        history = self.query_one("#host-history", RichLog)
        at_bottom = history.is_vertical_scroll_end
        history.write(renderable, scroll_end=at_bottom)

    async def _send(self, message: dict) -> None:
        if self.websocket is None:
            self._append(Text("尚未连接服务器。", style="red"))
            return
        await self.websocket.send(json.dumps(message, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="AI RP Engine Host TUI")
    parser.add_argument(
        "--uri",
        default=os.getenv("SERVER_URI", "ws://127.0.0.1:8765"),
        help="local WebSocket server URI",
    )
    arguments = parser.parse_args()
    HostApp(arguments.uri).run()


if __name__ == "__main__":
    main()
