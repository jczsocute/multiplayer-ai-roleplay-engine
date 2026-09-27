import unittest

from client.terminal import GameApp
from textual.widgets import Input, RichLog, TextArea


class TestGameApp(GameApp):
    def __init__(self) -> None:
        super().__init__("ws://unused", "Tester")
        self.sent = []
        self.appended = []

    def connect_to_server(self) -> None:
        pass

    async def _send(self, message: dict) -> None:
        self.sent.append(message)

    def _append(self, renderable) -> None:
        self.appended.append(renderable)


class DraftEditorTests(unittest.IsolatedAsyncioTestCase):
    async def test_role_view_renders_each_complete_round_in_order(self) -> None:
        app = TestGameApp()
        async with app.run_test(size=(100, 36)):
            await app._handle_message({
                "type": "joined",
                "name": "Tester",
                "role": None,
                "view_role": None,
                "scenario": "test",
            })
            await app._handle_message({
                "type": "role_view",
                "role": "A",
                "history": [
                    {"round": 1, "kind": "action", "content": "检查房门"},
                    {"round": 1, "kind": "narration", "content": "门后传来脚步声"},
                    {"round": 1, "kind": "statusbar", "content": {"精神状态": "警觉"}},
                    {"round": 2, "kind": "action", "content": "推开房门"},
                    {"round": 2, "kind": "narration", "content": "冷风迎面吹来"},
                    {"round": 2, "kind": "statusbar", "content": {"精神状态": "紧张"}},
                ],
                "statusbar": {"精神状态": "紧张"},
                "public_information": {"禁止展示": "幕后倒计时"},
            })

            self.assertEqual(app.view_role, "A")
            self.assertEqual(app._draft_editor().text, "")
            rendered = "\n".join(line.text for line in app.query_one("#history", RichLog).lines)
            self.assertLess(rendered.index("第 1 轮行动"), rendered.index("第 1 轮输出"))
            self.assertLess(rendered.index("第 1 轮输出"), rendered.index("第 1 轮状态栏"))
            self.assertLess(rendered.index("第 1 轮状态栏"), rendered.index("第 2 轮行动"))
            self.assertLess(rendered.index("第 2 轮行动"), rendered.index("第 2 轮输出"))
            self.assertLess(rendered.index("第 2 轮输出"), rendered.index("第 2 轮状态栏"))
            self.assertIn("精神状态", rendered)
            self.assertNotIn("当前状态栏", rendered)
            self.assertNotIn("幕后倒计时", rendered)
            self.assertEqual(str(app._statusbar_panel({}, "状态栏").border_style), "green")

    async def test_completed_round_appends_action_output_and_green_statusbar(self) -> None:
        app = TestGameApp()
        async with app.run_test(size=(100, 36)):
            await app._handle_message({
                "type": "joined",
                "name": "Tester",
                "role": None,
                "view_role": "A",
                "scenario": "test",
            })
            await app._handle_message({
                "type": "role_round",
                "round": 2,
                "role": "A",
                "entries": [
                    {"kind": "action", "content": "推开房门"},
                    {"kind": "narration", "content": "新的叙事"},
                    {"kind": "statusbar", "content": {"体力": "疲惫"}},
                ],
                "statusbar": {"体力": "疲惫"},
            })

            rendered = "\n".join(line.text for line in app.query_one("#history", RichLog).lines)
            self.assertLess(rendered.index("本轮行动"), rendered.index("本轮输出"))
            self.assertLess(rendered.index("本轮输出"), rendered.index("本轮状态栏"))
            self.assertIn("推开房门", rendered)
            self.assertIn("新的叙事", rendered)
            self.assertIn("体力", rendered)

    async def test_spectator_commands_are_limited_to_chat_and_view(self) -> None:
        app = TestGameApp()
        async with app.run_test(size=(100, 36)) as pilot:
            await app._handle_message({
                "type": "joined",
                "name": "Tester",
                "role": None,
                "view_role": None,
                "scenario": "test",
            })
            command = app.query_one("#command-input", Input)
            command.value = "/submit"
            command.focus()
            await pilot.press("enter")
            self.assertEqual(app.sent, [])

            command.value = "/view A"
            await pilot.press("enter")
            self.assertEqual(app.sent[-1], {"type": "view", "role": "A"})

            editor = app._draft_editor()
            editor.load_text("OOC hello")
            command.value = "/chat"
            await pilot.press("enter")
            self.assertEqual(app.sent[-1], {"type": "room_chat", "text": "OOC hello"})
            self.assertEqual(editor.text, "")

    async def test_hints_and_local_help_follow_current_identity(self) -> None:
        app = TestGameApp()
        async with app.run_test(size=(100, 36)) as pilot:
            await app._handle_message({
                "type": "joined",
                "name": "Tester",
                "role": None,
                "view_role": None,
                "scenario": "test",
            })
            editor = app._draft_editor()
            command = app.query_one("#command-input", Input)
            self.assertIn("房间聊天", editor.placeholder)
            self.assertNotIn("/submit", command.placeholder)

            command.value = "/help"
            command.focus()
            await pilot.press("enter")
            self.assertEqual(app.sent, [])
            self.assertIn("观众命令", str(app.appended[-1].renderable))

            await app._handle_message({
                "type": "identity_changed",
                "role": "A",
                "view_role": "A",
            })
            self.assertIn("角色行动", editor.placeholder)
            self.assertIn("/submit", command.placeholder)
            app.appended.clear()
            command.value = "/help"
            await pilot.press("enter")
            self.assertEqual(app.sent, [])
            self.assertIn("玩家命令", str(app.appended[-1].renderable))

    async def test_draft_and_command_are_independent(self) -> None:
        app = TestGameApp()
        async with app.run_test(size=(100, 32)) as pilot:
            app.role = "A"
            app.round_number = 1
            app._set_editor_status("EDITING")
            editor = app.query_one("#draft-editor", TextArea)
            editor.focus()

            await pilot.press("我", "走", "向", "门", "enter", "口")
            await pilot.pause()
            self.assertIn("\n", editor.text)
            self.assertFalse(any(item["type"] == "submit" for item in app.sent))

            app.sent.clear()
            command = app.query_one("#command-input", Input)
            command.disabled = False
            command.value = "/submit"
            command.focus()
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual([item["type"] for item in app.sent], ["action", "submit"])
            self.assertEqual(app.sent[0]["text"], editor.text)
            self.assertEqual(command.value, "")

            draft = editor.text
            app._apply_round_state({
                "round": 1,
                "players": {
                    "A": {"status": "READY", "connected": True},
                    "B": {"status": "EDITING", "connected": True},
                },
            })
            self.assertTrue(editor.read_only)
            self.assertEqual(editor.text, draft)

            app.sent.clear()
            command.value = "/cancel"
            command.focus()
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.sent, [{"type": "cancel_submit"}])
            app._apply_round_state({
                "round": 1,
                "players": {
                    "A": {"status": "EDITING", "connected": True},
                    "B": {"status": "EDITING", "connected": True},
                },
            })
            self.assertFalse(editor.read_only)
            self.assertEqual(editor.text, draft)

            self.assertEqual(editor.text, draft)
            self.assertFalse(any(
                binding.key in ("ctrl+enter", "ctrl+e")
                for binding in app.BINDINGS
            ))

    async def test_next_round_clears_draft(self) -> None:
        app = TestGameApp()
        async with app.run_test(size=(100, 32)):
            app.role = "A"
            app.round_number = 1
            editor = app.query_one("#draft-editor", TextArea)
            editor.load_text("上一轮行动")
            app._set_editor_status("PROCESSING")

            app._apply_round_state({
                "round": 2,
                "players": {
                    "A": {"status": "EDITING", "connected": True},
                    "B": {"status": "EDITING", "connected": True},
                },
            })

            self.assertEqual(editor.text, "")
            self.assertFalse(editor.read_only)


if __name__ == "__main__":
    unittest.main()
