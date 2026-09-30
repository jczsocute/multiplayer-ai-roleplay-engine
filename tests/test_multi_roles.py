import json
import tempfile
import unittest
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.gameserver.models import PlayerStatus
from server.gameserver.roles import RoleConfig
from server.gameserver.session import Sessions
from tests.support import user
from tests.test_resume import ScriptedConnection, identity_notices, join_message, resume_message


class MultipleRoleTests(unittest.IsolatedAsyncioTestCase):
    async def make_game(self, owner: int | None = None) -> tuple[GameServer, ScriptedConnection]:
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        roles = RoleConfig.from_data({"count": 3, "names": ["路人甲", "路人乙", "路人丙"]})
        database = Database(str(Path(directory.name) / "game.db"), roles.role_ids)
        await database.initialize()
        game = GameServer(database, None, None, role_config=roles, owner_user_id=owner)
        alice = ScriptedConnection()
        await game.sessions.join(user(1, "Alice"), alice)
        await game.sessions.join(user(2, "Bob"), ScriptedConnection())
        await game.sessions.assign_roles({"P1": 1, "P2": 1, "P3": 2})
        return game, alice

    async def command(self, game: GameServer, socket: ScriptedConnection, value: dict) -> None:
        await game._handle_command(1, socket, json.dumps(value))

    async def test_assignment_and_presence_keep_each_role_unique(self) -> None:
        game, _ = await self.make_game()
        self.assertEqual(await game.sessions.role_assignments(), {"P1": 1, "P2": 1, "P3": 2})
        self.assertEqual(await game.sessions.role_usernames(), {"P1": "Alice", "P2": "Alice", "P3": "Bob"})
        self.assertEqual(await game.sessions.role_connections(), {"P1": True, "P2": True, "P3": True})
        self.assertTrue(game.sessions.roles_assigned)
        self.assertEqual((await game.sessions.user_for_role("P2")).user_id, 1)
        presence = await game.sessions.presence_snapshot()
        self.assertEqual(presence["users"][0]["assigned_roles"], ["P1", "P2"])
        # Reassigning P2 transfers it; it cannot remain with two users.
        await game.sessions.assign_roles({"P1": 1, "P2": 2, "P3": 2})
        self.assertEqual(await game.sessions.role_assignments(), {"P1": 1, "P2": 2, "P3": 2})

    async def test_host_assignment_reports_each_role_for_same_member(self) -> None:
        game, alice = await self.make_game(owner=1)
        for role in game.role_ids:
            game.rounds.pause(role)
        await self.command(game, alice, {"type": "assign_roles", "assignments": {
            "P1": 1, "P2": 1, "P3": 2,
        }})
        self.assertEqual(await game.sessions.role_assignments(), {"P1": 1, "P2": 1, "P3": 2})
        notice = [m["text"] for m in alice.messages if m["type"] == "room_message"][-1]
        self.assertIn("Alice → P1", notice)
        self.assertIn("Alice → P2", notice)

    async def test_view_and_commands_use_only_current_assigned_role(self) -> None:
        game, alice = await self.make_game()
        await self.command(game, alice, {"type": "action", "text": "打开门"})
        await self.command(game, alice, {"type": "view", "role": "P2"})
        self.assertEqual(game.sessions.users[1].view_role, "P2")
        self.assertEqual(alice.messages[-1]["role"], "P2")
        self.assertEqual(alice.messages[-1]["draft"], "")
        await self.command(game, alice, {"type": "action", "text": "守住门口"})
        await self.command(game, alice, {"type": "submit"})
        self.assertEqual(game.rounds.players["P2"].status, PlayerStatus.READY)
        self.assertEqual(game.rounds.players["P1"].status, PlayerStatus.EDITING)
        await self.command(game, alice, {"type": "cancel_submit"})
        await self.command(game, alice, {"type": "pause"})
        self.assertEqual(game.rounds.players["P2"].status, PlayerStatus.PAUSED)
        await self.command(game, alice, {"type": "resume"})
        self.assertEqual(game.rounds.players["P2"].status, PlayerStatus.EDITING)
        await self.command(game, alice, {"type": "view", "role": "P1"})
        self.assertEqual(alice.messages[-1]["draft"], "打开门")
        await self.command(game, alice, {"type": "view", "role": "P3"})
        self.assertEqual(game.sessions.users[1].view_role, "P1")
        self.assertEqual(alice.messages[-1]["type"], "error")
        await self.command(game, alice, {"type": "status"})
        self.assertEqual(alice.messages[-1]["role"], "P1")

    async def test_reconnect_preserves_roles_view_drafts_and_notice(self) -> None:
        game, alice = await self.make_game()
        game.rounds.set_action("P1", "打开门")
        game.rounds.set_action("P2", "守住门口")
        await game.sessions.set_view(1, "P2")
        token = game.sessions.users[1].resume_token
        await game.sessions.mark_disconnected(1, alice)
        resumed = ScriptedConnection([resume_message(token, "test-key")])
        await game.public_handler(resumed, user(1, "Alice"))
        payload = resumed.messages[0]
        self.assertEqual(payload["assigned_roles"], ["P1", "P2"])
        self.assertEqual(payload["view_role"], "P2")
        self.assertEqual(next(m for m in resumed.messages if m["type"] == "role_view")["draft"], "守住门口")
        self.assertEqual([m["text"] for m in identity_notices(resumed)],
                         ["您目前扮演 <路人甲、路人乙>。请继续游戏。"])

    async def test_host_notice_has_priority_and_leave_releases_all_roles(self) -> None:
        game, alice = await self.make_game(owner=1)
        await game._send_identity_notice(game.sessions.users[1], alice)
        self.assertEqual(identity_notices(alice)[-1]["text"], "您目前身份为 <房主>。")
        await game.evict_user(1)
        self.assertEqual(await game.sessions.role_assignments(), {"P3": 2})
        self.assertIsNone(await game.sessions.user_for_role("P1"))
        self.assertIsNone(await game.sessions.user_for_role("P2"))

    async def test_single_role_player_still_cannot_view_another_role(self) -> None:
        sessions = Sessions()
        await sessions.join(user(1, "Alice"), ScriptedConnection())
        await sessions.join(user(2, "Bob"), ScriptedConnection())
        await sessions.assign_roles({"P1": 1, "P2": 2})
        with self.assertRaises(ValueError):
            await sessions.set_view(1, "P2")
        self.assertEqual(await sessions.role_for(1), "P1")
