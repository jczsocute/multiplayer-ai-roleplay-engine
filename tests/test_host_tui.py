import unittest

from client.host import HostApp
from textual.widgets import Input, RichLog, TextArea


class TestHostApp(HostApp):
    def __init__(self) -> None:
        super().__init__("ws://unused")
        self.sent = []

    def connect_to_server(self) -> None:
        pass

    async def _send(self, message: dict) -> None:
        self.sent.append(message)


class HostTuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_view_renders_complete_round_with_statusbar(self) -> None:
        app = TestHostApp()
        async with app.run_test(size=(100, 36)):
            await app._handle_message({
                "type": "role_view",
                "role": "P2",
                "opening": "Host 看到的开场",
                "history": [
                    {"round": 1, "kind": "action", "content": "观察窗外"},
                    {"round": 1, "kind": "narration", "content": "雨幕遮住远处"},
                    {"round": 1, "kind": "statusbar", "content": {"法力": 40}},
                ],
                "statusbar": {"法力": 40},
            })

            rendered = "\n".join(
                line.text for line in app.query_one("#host-history", RichLog).lines
            )
            self.assertLess(rendered.index("Host 看到的开场"), rendered.index("第 1 轮行动"))
            self.assertLess(rendered.index("第 1 轮行动"), rendered.index("第 1 轮输出"))
            self.assertLess(rendered.index("第 1 轮输出"), rendered.index("第 1 轮状态栏"))
            self.assertIn("法力", rendered)
            self.assertNotIn("当前状态栏", rendered)
            self.assertEqual(str(app._statusbar_panel({}, "状态栏").border_style), "green")

    async def test_live_round_renders_action_narration_and_statusbar(self) -> None:
        app = TestHostApp()
        async with app.run_test(size=(100, 36)):
            app.view = "P1"
            await app._handle_message({
                "type": "role_round",
                "round": 3,
                "role": "P1",
                "entries": [
                    {"kind": "action", "content": "点亮提灯"},
                    {"kind": "narration", "content": "灯光照亮走廊"},
                    {"kind": "statusbar", "content": {"灯油": 80}},
                ],
                "statusbar": {"灯油": 80},
            })

            rendered = "\n".join(
                line.text for line in app.query_one("#host-history", RichLog).lines
            )
            self.assertLess(rendered.index("本轮行动"), rendered.index("本轮输出"))
            self.assertLess(rendered.index("本轮输出"), rendered.index("本轮状态栏"))
            self.assertIn("点亮提灯", rendered)

    async def test_assign_command_maps_two_role_names(self) -> None:
        app = TestHostApp()
        async with app.run_test(size=(100, 32)) as pilot:
            app.roles = [{"id": "P1", "name": "角色1"}, {"id": "P2", "name": "角色2"}]
            command = app.query_one("#host-command", Input)
            command.value = "/assign Chengzhe Alice"
            command.focus()

            await pilot.press("enter")
            await pilot.pause()

            self.assertEqual(app.sent, [{
                "type": "assign_roles",
                "assignments": {"P1": "Chengzhe", "P2": "Alice"},
            }])
            self.assertEqual(command.value, "")

    async def test_host_chat_view_and_retry_commands(self) -> None:
        app = TestHostApp()
        async with app.run_test(size=(100, 36)) as pilot:
            app.roles = [{"id": "P1", "name": "角色1"}, {"id": "P2", "name": "角色2"}]
            draft = app.query_one("#host-draft", TextArea)
            command = app.query_one("#host-command", Input)
            draft.load_text("稍等，我重新分配。")
            command.value = "/chat"
            command.focus()
            await pilot.press("enter")
            command.value = "/view P1"
            await pilot.press("enter")
            command.value = "/retry"
            await pilot.press("enter")

            self.assertEqual(app.sent, [
                {"type": "room_chat", "text": "稍等，我重新分配。"},
                {"type": "view", "view": "P1"},
                {"type": "retry_ai"},
            ])
            self.assertEqual(draft.text, "")

    async def test_host_help_is_local(self) -> None:
        app = TestHostApp()
        async with app.run_test(size=(100, 32)) as pilot:
            command = app.query_one("#host-command", Input)
            self.assertIn("/help", command.placeholder)
            command.value = "/help"
            command.focus()
            await pilot.press("enter")

            self.assertEqual(app.sent, [])
            history = "\n".join(
                line.text for line in app.query_one("#host-history", RichLog).lines
            )
            self.assertIn("Host 命令", history)
            self.assertIn("/assign", history)


if __name__ == "__main__":
    unittest.main()
