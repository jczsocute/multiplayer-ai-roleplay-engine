import unittest

from client.terminal import GameApp
from textual.widgets import Input, TextArea


class TestGameApp(GameApp):
    def __init__(self) -> None:
        super().__init__("ws://unused", "Tester")
        self.sent = []

    def connect_to_server(self) -> None:
        pass

    async def _send(self, message: dict) -> None:
        self.sent.append(message)


class DraftEditorTests(unittest.IsolatedAsyncioTestCase):
    async def test_enter_submit_cancel_and_read_only_lifecycle(self) -> None:
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
            await pilot.press("ctrl+enter")
            await pilot.pause()
            self.assertEqual([item["type"] for item in app.sent], ["action", "submit"])
            self.assertEqual(app.sent[0]["text"], editor.text)

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
            await pilot.press("ctrl+e")
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

            app._apply_round_state({
                "round": 1,
                "players": {
                    "A": {"status": "READY", "connected": True},
                    "B": {"status": "EDITING", "connected": True},
                },
            })
            command = app.query_one("#command-input", Input)
            command.disabled = False
            command.value = "/cancel"
            command.focus()
            app.sent.clear()
            await pilot.press("enter")
            await pilot.pause()
            self.assertEqual(app.sent, [{"type": "cancel_submit"}])

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
