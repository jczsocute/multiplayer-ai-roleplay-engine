import unittest

from client.display import room_message_text, to_yaml


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

    def test_room_message_prefixes_are_structured_and_distinct(self) -> None:
        system = room_message_text({"kind": "system", "text": "Alice 已加入。"})
        host = room_message_text({"kind": "host", "text": "稍等。"})
        player = room_message_text({
            "kind": "player", "sender": "Alice",
            "character_name": "林岚", "text": "先别开门。",
        })
        spectator = room_message_text({
            "kind": "spectator", "sender": "Tom", "text": "我同意。",
        })

        self.assertEqual(system.plain, "[系统] Alice 已加入。")
        self.assertEqual(host.plain, "[管理员] 稍等。")
        self.assertEqual(player.plain, "[Alice (林岚)] 先别开门。")
        self.assertEqual(spectator.plain, "[Tom] 我同意。")
        self.assertEqual(
            len({host.spans[0].style, player.spans[0].style, spectator.spans[0].style}),
            3,
        )


if __name__ == "__main__":
    unittest.main()
