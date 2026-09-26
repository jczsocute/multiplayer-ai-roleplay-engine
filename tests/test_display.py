import unittest

from client.display import to_yaml


class DisplayTests(unittest.TestCase):
    def test_world_update_and_statusbar_render_as_yaml(self) -> None:
        world_update = {
            "world_state": {"gate": "open"},
            "player_views": {"A": {"seen": True}, "B": {"seen": False}},
        }
        statusbar = {"hp": 82, "san": 61, "mood": "紧张"}

        rendered_world = to_yaml(world_update)
        rendered_status = to_yaml(statusbar)

        self.assertIn("world_state:\n  gate: open", rendered_world)
        self.assertIn("player_views:\n  A:", rendered_world)
        self.assertEqual(rendered_status, "hp: 82\nsan: 61\nmood: 紧张")
        self.assertNotIn("{", rendered_status)


if __name__ == "__main__":
    unittest.main()
