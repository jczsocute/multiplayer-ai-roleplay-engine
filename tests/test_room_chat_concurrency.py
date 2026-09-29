"""Regression tests: Room Chat must not wait for the AI pipeline's command lock."""

import asyncio
import json
import tempfile
import unittest
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from tests.support import user


WORLD_RESULT = {
    "world_state": {"gate": "open"},
    "character_views": {"P1": {"seen": 1}, "P2": {"seen": 2}},
    "character_status": {"P1": {"hp": 100}, "P2": {"hp": 100}},
}


class FakeConnection:
    def __init__(self) -> None:
        self.messages: list[dict] = []

    async def send(self, payload: str) -> None:
        self.messages.append(json.loads(payload))


class BlockingWorldUpdater:
    """Hold the AI stage open until the test releases it."""

    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.release = asyncio.Event()
        self.calls = 0

    async def update(self, current_world_state: str, actions: dict[str, str]) -> dict:
        self.calls += 1
        self.started.set()
        await self.release.wait()
        return WORLD_RESULT


class StubNarrator:
    def __init__(self) -> None:
        self.calls: list[str] = []

    async def narrate(
        self,
        player_id: str,
        character_view: object,
        character_status: object,
        chat_history: list,
    ) -> dict:
        self.calls.append(player_id)
        return {"text": f"Narration {player_id}", "status": {}}


class ProcessingRoomChatTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)

    async def make_server(self, updater: BlockingWorldUpdater) -> GameServer:
        database = Database(str(Path(self.directory.name) / "game.db"))
        await database.initialize()
        return GameServer(database, updater, StubNarrator(), 1)

    async def submit_until_processing(self, server: GameServer, sockets: dict) -> asyncio.Task:
        """Submit both players; return the task that is stuck inside WorldUpdater."""
        alice, bob = sockets["Alice"], sockets["Bob"]
        await server._handle_command(
            1, alice, json.dumps({"type": "action", "text": "open the gate"})
        )
        await server._handle_command(1, alice, json.dumps({"type": "submit"}))
        await server._handle_command(
            2, bob, json.dumps({"type": "action", "text": "stand guard"})
        )
        processing = asyncio.create_task(
            server._handle_command(2, bob, json.dumps({"type": "submit"}))
        )
        self.addCleanup(processing.cancel)
        await asyncio.wait_for(server.world_updater.started.wait(), timeout=1)
        self.assertFalse(processing.done())
        # The submit task holds the Story command lock for the whole AI pipeline, so
        # any Room Chat delivered from here on proves chat does not need that lock.
        self.assertTrue(server.command_lock.locked())
        return processing

    @staticmethod
    def chat_messages(socket: FakeConnection, text: str) -> list[dict]:
        return [
            message
            for message in socket.messages
            if message["type"] == "room_message" and message["text"] == text
        ]

    async def test_spectator_room_chat_is_delivered_before_ai_release(self) -> None:
        updater = BlockingWorldUpdater()
        server = await self.make_server(updater)
        sockets = {"Alice": FakeConnection(), "Bob": FakeConnection()}
        watcher = FakeConnection()
        await server.sessions.join(user(1, "Alice"), sockets["Alice"])
        await server.sessions.join(user(2, "Bob"), sockets["Bob"])
        await server.sessions.join(user(3, "Watcher"), watcher)
        await server.sessions.assign_roles({"P1": 1, "P2": 2})

        processing = await self.submit_until_processing(server, sockets)
        try:
            # AI is still blocked and command_lock is held by the submit task.
            await asyncio.wait_for(
                server._handle_command(
                    3, watcher, json.dumps({"type": "room_chat", "text": "还在吗"})
                ),
                timeout=1,
            )
            delivered = self.chat_messages(sockets["Alice"], "还在吗")
            self.assertEqual(len(delivered), 1)
            self.assertEqual(delivered[0]["kind"], "spectator")
            self.assertEqual(delivered[0]["sender"], "Watcher")
            self.assertFalse(processing.done())
        finally:
            updater.release.set()
        await processing

        self.assertEqual(updater.calls, 1)
        self.assertEqual(server.rounds.round_number, 2)

    async def test_player_room_chat_is_delivered_before_ai_release(self) -> None:
        updater = BlockingWorldUpdater()
        server = await self.make_server(updater)
        sockets = {"Alice": FakeConnection(), "Bob": FakeConnection()}
        await server.sessions.join(user(1, "Alice"), sockets["Alice"])
        await server.sessions.join(user(2, "Bob"), sockets["Bob"])
        await server.sessions.assign_roles({"P1": 1, "P2": 2})

        processing = await self.submit_until_processing(server, sockets)
        try:
            await asyncio.wait_for(
                server._handle_command(
                    1, sockets["Alice"], json.dumps({"type": "room_chat", "text": "别急"})
                ),
                timeout=1,
            )
            delivered = self.chat_messages(sockets["Bob"], "别急")
            self.assertEqual(len(delivered), 1)
            self.assertEqual(delivered[0]["kind"], "player")
            self.assertEqual(delivered[0]["role"], "P1")
            self.assertFalse(processing.done())
        finally:
            updater.release.set()
        await processing


if __name__ == "__main__":
    unittest.main()
