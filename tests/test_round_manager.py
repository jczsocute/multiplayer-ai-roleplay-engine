import unittest

from server.gameserver.models import PlayerStatus
from server.gameserver.round_manager import RoundError, RoundManager


class RoundManagerTests(unittest.TestCase):
    def setUp(self) -> None:
        self.manager = RoundManager()

    def test_two_submissions_lock_and_advance_round(self) -> None:
        self.manager.set_action("P1", "open the door")
        self.manager.set_action("P2", "watch the corridor")
        self.assertIsNone(self.manager.submit("P1"))

        completed = self.manager.submit("P2")

        self.assertIsNotNone(completed)
        self.assertEqual(completed.actions["P1"], "open the door")
        self.assertTrue(
            all(p.status == PlayerStatus.PROCESSING for p in self.manager.players.values())
        )
        self.manager.start_next_round()
        self.assertEqual(self.manager.round_number, 2)
        self.assertTrue(
            all(p.status == PlayerStatus.EDITING for p in self.manager.players.values())
        )

    def test_cancel_pause_and_resume(self) -> None:
        self.manager.set_action("P1", "wait")
        self.manager.submit("P1")
        self.manager.cancel_submit("P1")
        self.manager.pause("P1")
        self.assertEqual(self.manager.players["P1"].status, PlayerStatus.PAUSED)
        self.manager.resume("P1")
        self.assertEqual(self.manager.players["P1"].status, PlayerStatus.EDITING)

    def test_submit_requires_action(self) -> None:
        self.manager.set_action("P1", " \n ")
        with self.assertRaises(RoundError):
            self.manager.submit("P1")

    def test_draft_overwrites_cancel_preserves_and_next_round_clears(self) -> None:
        self.manager.set_action("P1", "我走向房门。")
        latest = " 我悄悄走向房门，\n并侧耳听里面的声音。 "
        self.manager.set_action("P1", latest)
        self.assertEqual(
            self.manager.players["P1"].action,
            latest,
        )

        self.manager.submit("P1")
        self.manager.cancel_submit("P1")
        self.assertEqual(self.manager.players["P1"].status, PlayerStatus.EDITING)
        self.assertEqual(
            self.manager.players["P1"].action,
            latest,
        )

        self.manager.submit("P1")
        self.manager.set_action("P2", "我留在原地警戒。")
        self.manager.submit("P2")
        self.manager.start_next_round()
        self.assertEqual(self.manager.players["P1"].action, "")
        self.assertEqual(self.manager.players["P2"].action, "")
