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
from textual.widgets import Input, RichLog, Static, TextArea
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidURI

try:
    from client.display import room_message_text, to_yaml
except ModuleNotFoundError:  # Supports: python client/host.py
    from display import room_message_text, to_yaml


STATUS_LABELS = {
    "LOBBY": ("大厅等待", "grey50"),
    "EDITING": ("编辑中", "yellow"),
    "READY": ("已提交", "green"),
    "PAUSED": ("已暂停", "red"),
    "PROCESSING": ("处理中", "blue"),
}

PROTOCOL_VERSION = 3

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
        self.users: list[dict] = []
        self.players: dict = {}
        self.roles: list[dict] = []
        self.view = "world"
        self.processing_stage: str | None = None
        self.processing_started = 0.0

    def compose(self) -> ComposeResult:
        yield Static("正在连接服务器…", id="host-identity")
        yield RichLog(id="host-history", wrap=True, markup=False, auto_scroll=False)
        yield Static("房间内所有用户\n（暂无）", id="host-presence")
        yield Static("等待角色信息…", id="host-collaboration")
        yield Static("Draft：", id="host-draft-label")
        yield TextArea("", placeholder="Host 房间聊天 Draft", id="host-draft")
        yield Static("Command：", id="host-command-label")
        yield Input(
            placeholder="/assign <昵称1> … <昵称N> /chat /view P1…PN|world /status /retry /help /quit",
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
            if message.get("protocol_version", 1) != PROTOCOL_VERSION:
                raise ValueError(
                    f"不支持的协议版本：{message.get('protocol_version')}（客户端支持 {PROTOCOL_VERSION}）"
                )
            self.roles = message.get("roles", [])
            self.query_one("#host-identity", Static).update(
                f"Host · Scenario: {message.get('scenario', '')} · View: {self.view}"
            )
        elif message_type == "presence":
            self.users = message.get("users", [])
            self._render_presence()
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
            self._render_collaboration()
        elif message_type == "processing_stage":
            self._set_processing_stage(str(message.get("stage", "")))
        elif message_type in ("world_update", "world_view"):
            if self.view == "world":
                if message_type == "world_view":
                    self.query_one("#host-history", RichLog).clear()
                self._append(Panel(
                    Text(to_yaml(message.get("result", {}))),
                    title=f"Round {message.get('round', '?')} World Update",
                    border_style="cyan",
                ))
        elif message_type == "role_view":
            self._load_role_view(message)
        elif message_type == "role_round":
            if message.get("role") == self.view:
                self._append_live_round(message.get("entries", []))
        elif message_type == "room_message":
            self._append(room_message_text(message))
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
        if command == "/help":
            roles = "\n".join(
                f"/view {item['id']}  查看 {item['name']} 角色历史" for item in self.roles
            )
            help_text = (
                "Host 命令：\n"
                f"/assign <昵称1> … <昵称{len(self.roles)}>  按角色顺序分配或重新分配\n"
                "/chat                    将 Draft 发送到房间聊天\n"
                f"{roles}\n"
                "/view world              查看完整世界调试信息\n"
                "/status                  查询当前游戏状态\n"
                "/retry                   重试失败的 AI 阶段\n"
                "/help                    显示本帮助\n"
                "/quit                    退出 Host TUI"
            )
            self._append(Panel(help_text, title="命令帮助", border_style="cyan"))
            return
        try:
            parts = shlex.split(command)
        except ValueError as exc:
            self._append(Text(f"命令格式错误：{exc}", style="yellow"))
            return
        if len(parts) == len(self.roles) + 1 and parts[0] == "/assign":
            await self._send({
                "type": "assign_roles",
                "assignments": {
                    role["id"]: nickname
                    for role, nickname in zip(self.roles, parts[1:])
                },
            })
            return
        if parts == ["/chat"]:
            text = self.query_one("#host-draft", TextArea).text.strip()
            if not text:
                self._append(Text("Draft 不能为空。", style="yellow"))
                return
            await self._send({"type": "room_chat", "text": text})
            self.query_one("#host-draft", TextArea).load_text("")
            return
        role_ids = {item["id"] for item in self.roles}
        if len(parts) == 2 and parts[0] == "/view" and (
            parts[1].upper() in role_ids or parts[1] == "world"
        ):
            self.view = parts[1] if parts[1] == "world" else parts[1].upper()
            self.query_one("#host-identity", Static).update(f"Host · View: {self.view}")
            await self._send({"type": "view", "view": self.view})
            return
        if parts == ["/status"]:
            await self._send({"type": "status"})
            return
        if parts == ["/retry"]:
            await self._send({"type": "retry_ai"})
            return
        self._append(Text(
            "Host 命令无效；输入 /help 查看可用命令。",
            style="yellow",
        ))

    def _render_presence(self) -> None:
        names = ", ".join(item["name"] for item in self.users) or "（暂无）"
        self.query_one("#host-presence", Static).update(f"房间内所有用户\n{names}")

    def _render_collaboration(self) -> None:
        line = Text()
        role_ids = [item["id"] for item in self.roles] or list(self.players)
        for index, role in enumerate(role_ids):
            state = self.players.get(role, {})
            connected = state.get("connected", False)
            status = state.get("status", "EDITING")
            nickname = state.get("user") or ""
            if not connected:
                label, color = "无人扮演", "grey50"
            elif status == "PROCESSING" and self.processing_stage in PROCESSING_LABELS:
                elapsed = max(0, int(time.monotonic() - self.processing_started))
                label, color = f"{PROCESSING_LABELS[self.processing_stage]} · {elapsed}s", "blue"
            else:
                label, color = STATUS_LABELS.get(status, (status, "white"))
            if index:
                line.append("          ")
            identity = f" {nickname}" if nickname else ""
            line.append(f"{role}{identity} ■ {label}", style=color)
        self.query_one("#host-collaboration", Static).update(line)

    def _set_processing_stage(self, stage: str) -> None:
        if stage in PROCESSING_LABELS:
            self.processing_stage = stage
            self.processing_started = time.monotonic()
        elif stage in ("WAITING_INPUT", "FINISHED"):
            self.processing_stage = None
        self._render_collaboration()

    def _refresh_processing(self) -> None:
        if self.processing_stage and self.players:
            self._render_collaboration()

    def _load_role_view(self, message: dict) -> None:
        self.view = message["role"]
        history = self.query_one("#host-history", RichLog)
        history.clear()
        opening = str(message.get("opening", "")).strip()
        if opening:
            history.write(
                Panel(opening, title="开场", border_style="cyan"), scroll_end=False
            )
        entries = message.get("history", [])
        for item in entries:
            self._write_role_entry(history, int(item["round"]), item)
        if not entries and not opening:
            history.write(Text("该角色尚无历史。", style="dim"), scroll_end=False)
        history.scroll_end(animate=False)

    def _append_live_round(self, entries: list[dict]) -> None:
        history = self.query_one("#host-history", RichLog)
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
        history: RichLog, round_id: int, item: dict, *, scroll_end: bool = False
    ) -> None:
        kind = item.get("kind")
        titles = {
            "action": f"第 {round_id} 轮行动",
            "narration": f"第 {round_id} 轮输出",
            "statusbar": f"第 {round_id} 轮状态栏",
        }
        if kind == "statusbar":
            history.write(
                HostApp._statusbar_panel(item.get("content", {}), titles[kind]),
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
        default=os.getenv("HOST_SERVER_URI", "ws://127.0.0.1:8766"),
        help="local WebSocket server URI",
    )
    arguments = parser.parse_args()
    HostApp(arguments.uri).run()


if __name__ == "__main__":
    main()
