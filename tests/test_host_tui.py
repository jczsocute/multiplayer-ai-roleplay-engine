import unittest

from client.host import HostApp
from textual.widgets import Input


class TestHostApp(HostApp):
    def __init__(self) -> None:
        super().__init__("ws://unused")
        self.sent = []

    def connect_to_server(self) -> None:
        pass

    async def _send(self, message: dict) -> None:
        self.sent.append(message)


class HostTuiTests(unittest.IsolatedAsyncioTestCase):
    async def test_assign_command_maps_two_player_names(self) -> None:
        app = TestHostApp()
        async with app.run_test(size=(100, 32)) as pilot:
            command = app.query_one("#host-command", Input)
            command.value = "/assign Chengzhe Alice"
            command.focus()

            await pilot.press("enter")
            await pilot.pause()

            self.assertEqual(app.sent, [{
                "type": "assign_roles",
                "player_a": "Chengzhe",
                "player_b": "Alice",
            }])
            self.assertEqual(command.value, "")


if __name__ == "__main__":
    unittest.main()
