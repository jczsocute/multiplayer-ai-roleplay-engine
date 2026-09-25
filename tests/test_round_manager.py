import unittest

from server.models import PlayerStatus
from server.round_manager import RoundError, RoundManager


class RoundManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = RoundManager()

    def test_two_submissions_lock_and_advance_round(self) -> None:
        self.manager.set_action("A", "open the door")
        self.manager.set_action("B", "watch the corridor")
        self.assertIsNone(self.manager.submit("A"))

        completed = self.manager.submit("B")

        self.assertIsNotNone(completed)
        self.assertEqual(completed.actions["A"], "open the door")
        self.assertTrue(
            all(p.status == PlayerStatus.PROCESSING for p in self.manager.players.values())
        )
        self.manager.start_next_round()
        self.assertEqual(self.manager.round_number, 2)
        self.assertTrue(
            all(p.status == PlayerStatus.EDITING for p in self.manager.players.values())
        )

    def test_cancel_pause_and_resume(self) -> None:
        self.manager.set_action("A", "wait")
        self.manager.submit("A")
        self.manager.cancel_submit("A")
        self.manager.pause("A")
        self.assertEqual(self.manager.players["A"].status, PlayerStatus.PAUSED)
        self.manager.resume("A")
        self.assertEqual(self.manager.players["A"].status, PlayerStatus.EDITING)

    def test_submit_requires_action(self) -> None:
        self.manager.set_action("A", " \n ")
        with self.assertRaises(RoundError):
            self.manager.submit("A")

    def test_draft_overwrites_cancel_preserves_and_next_round_clears(self) -> None:
        self.manager.set_action("A", "我走向房门。")
        latest = " 我悄悄走向房门，\n并侧耳听里面的声音。 "
        self.manager.set_action("A", latest)
        self.assertEqual(
            self.manager.players["A"].action,
            latest,
        )

        self.manager.submit("A")
        self.manager.cancel_submit("A")
        self.assertEqual(self.manager.players["A"].status, PlayerStatus.EDITING)
        self.assertEqual(
            self.manager.players["A"].action,
            latest,
        )

        self.manager.submit("A")
        self.manager.set_action("B", "我留在原地警戒。")
        self.manager.submit("B")
        self.manager.start_next_round()
        self.assertEqual(self.manager.players["A"].action, "")
        self.assertEqual(self.manager.players["B"].action, "")
