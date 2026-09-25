import asyncio
import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from server.models import CompletedRound, PlayerStatus, RoundStage


SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY CHECK (player_id IN ('A', 'B')),
    status TEXT NOT NULL,
    current_action TEXT NOT NULL DEFAULT '',
    last_ack_round INTEGER NOT NULL DEFAULT 0,
    statusbar_content TEXT NOT NULL DEFAULT '{}',
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS rounds (
    round_number INTEGER PRIMARY KEY,
    status TEXT NOT NULL,
    stage TEXT NOT NULL DEFAULT 'WAITING_INPUT',
    action_a TEXT,
    action_b TEXT,
    result_world_state TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_number INTEGER NOT NULL,
    player_id TEXT CHECK (player_id IN ('A', 'B') OR player_id IS NULL),
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (round_number) REFERENCES rounds(round_number)
);

CREATE TABLE IF NOT EXISTS world_state (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    content TEXT NOT NULL,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS player_views (
    round_id INTEGER NOT NULL,
    player_id TEXT NOT NULL CHECK (player_id IN ('A', 'B')),
    view_content TEXT NOT NULL,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (round_id, player_id),
    FOREIGN KEY (round_id) REFERENCES rounds(round_number)
);

CREATE TABLE IF NOT EXISTS public_world_info (
    round_id INTEGER PRIMARY KEY,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (round_id) REFERENCES rounds(round_number)
);

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    is_host INTEGER NOT NULL DEFAULT 0,
    role TEXT UNIQUE CHECK (role IN ('A', 'B') OR role IS NULL),
    joined_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
"""


class Database:
    """Small SQLite wrapper; blocking work is moved off the asyncio event loop."""

    def __init__(self, path: str) -> None:
        self.path = Path(path)

    async def initialize(self, initial_world_state=None, initial_statusbars=None) -> None:
        await asyncio.to_thread(self._initialize_sync, initial_world_state, initial_statusbars)

    async def current_round(self) -> int:
        return await asyncio.to_thread(self._current_round_sync)

    async def set_round_stage(self, round_id: int, stage: RoundStage) -> None:
        await asyncio.to_thread(self._set_round_stage_sync, round_id, stage.value)

    async def get_recovery_data(self, round_id: int) -> dict:
        return await asyncio.to_thread(self._get_recovery_data_sync, round_id)

    async def save_player(self, player_id: str, status: PlayerStatus, action: str) -> None:
        await asyncio.to_thread(self._save_player_sync, player_id, status.value, action)

    async def get_unacked_narrations(self, player_id: str) -> list[dict]:
        return await asyncio.to_thread(self._get_unacked_narrations_sync, player_id)

    async def acknowledge_narration(self, player_id: str, round_id: int) -> bool:
        return await asyncio.to_thread(
            self._acknowledge_narration_sync, player_id, round_id
        )

    async def get_full_history(self, player_id: str) -> list[dict]:
        return await asyncio.to_thread(self._get_full_history_sync, player_id)

    async def get_player_display(self, player_id: str) -> dict:
        return await asyncio.to_thread(self._get_player_display_sync, player_id)

    async def get_latest_narration(self, player_id: str) -> dict | None:
        return await asyncio.to_thread(self._get_latest_narration_sync, player_id)

    async def get_participants(self) -> list[dict]:
        return await asyncio.to_thread(self._get_participants_sync)

    async def register_participant(self, name: str, is_host: bool) -> None:
        await asyncio.to_thread(self._register_participant_sync, name, is_host)

    async def save_role_assignment(self, assignments: dict[str, str]) -> None:
        await asyncio.to_thread(self._save_role_assignment_sync, assignments)

    async def get_world_state(self) -> str:
        return await asyncio.to_thread(self._get_world_state_sync)

    async def save_world_state(self, content: str) -> None:
        await asyncio.to_thread(self._save_world_state_sync, content)

    async def save_world_update(
        self, completed: CompletedRound, result: dict
    ) -> None:
        await asyncio.to_thread(self._save_world_update_sync, completed, result)

    async def save_player_views(self, round_id: int, views: dict[str, str]) -> None:
        await asyncio.to_thread(self._save_player_views_sync, round_id, views)

    async def get_chat_history(self, player_id: str, limit: int = 20) -> list[dict[str, str]]:
        return await asyncio.to_thread(self._get_chat_history_sync, player_id, limit)

    async def save_narrations(self, round_id: int, narrations: dict) -> None:
        await asyncio.to_thread(self._save_narrations_sync, round_id, narrations)

    async def finish_round(self, completed: CompletedRound) -> None:
        await asyncio.to_thread(self._finish_round_sync, completed)

    async def create_round(self, round_number: int) -> None:
        await asyncio.to_thread(self._create_round_sync, round_number)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_sync(self, initial_world_state=None, initial_statusbars=None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.executescript(SCHEMA)
            self._migrate_schema(connection)
            connection.executemany(
                "INSERT OR IGNORE INTO players (player_id, status) VALUES (?, ?)",
                (("A", PlayerStatus.EDITING.value), ("B", PlayerStatus.EDITING.value)),
            )
            if initial_statusbars:
                connection.executemany(
                    """UPDATE players SET statusbar_content = ?
                       WHERE player_id = ? AND statusbar_content = '{}'""",
                    (
                        (self._as_text(initial_statusbars[player_id]), player_id)
                        for player_id in ("A", "B")
                    ),
                )
            connection.execute(
                "INSERT OR IGNORE INTO rounds (round_number, status) VALUES (1, 'OPEN')"
            )
            connection.execute(
                "INSERT OR IGNORE INTO world_state (id, content) VALUES (1, ?)",
                (
                    self._as_text(initial_world_state)
                    if initial_world_state is not None
                    else "# World State\n\nThe world has not started yet.",
                ),
            )

    @staticmethod
    def _migrate_schema(connection: sqlite3.Connection) -> None:
        world_columns = {
            row[1] for row in connection.execute("PRAGMA table_info(world_state)")
        }
        if "state_json" in world_columns and "content" not in world_columns:
            connection.execute("ALTER TABLE world_state RENAME COLUMN state_json TO content")

        round_columns = {row[1] for row in connection.execute("PRAGMA table_info(rounds)")}
        if "result_world_state" not in round_columns:
            connection.execute("ALTER TABLE rounds ADD COLUMN result_world_state TEXT")
        if "stage" not in round_columns:
            connection.execute(
                "ALTER TABLE rounds ADD COLUMN stage TEXT NOT NULL DEFAULT 'WAITING_INPUT'"
            )
        connection.execute(
            "UPDATE rounds SET stage = 'FINISHED' WHERE status = 'COMPLETED'"
        )
        connection.execute(
            """UPDATE rounds SET stage = 'WORLD_DONE'
               WHERE status = 'OPEN' AND result_world_state IS NOT NULL
                 AND stage = 'WAITING_INPUT'"""
        )
        player_columns = {row[1] for row in connection.execute("PRAGMA table_info(players)")}
        if "last_ack_round" not in player_columns:
            connection.execute(
                "ALTER TABLE players ADD COLUMN last_ack_round INTEGER NOT NULL DEFAULT 0"
            )
        if "statusbar_content" not in player_columns:
            connection.execute(
                "ALTER TABLE players ADD COLUMN statusbar_content TEXT NOT NULL DEFAULT '{}'"
            )

    def _save_player_sync(self, player_id: str, status: str, action: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE players
                   SET status = ?, current_action = ?, updated_at = CURRENT_TIMESTAMP
                   WHERE player_id = ?""",
                (status, action, player_id),
            )

    def _get_unacked_narrations_sync(self, player_id: str) -> list[dict]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT last_ack_round FROM players WHERE player_id = ?", (player_id,)
            ).fetchone()
            if row is None:
                raise ValueError(f"unknown player: {player_id}")
            messages = connection.execute(
                """SELECT round_number, content FROM chat_messages
                   WHERE player_id = ? AND role = 'narrator' AND round_number > ?
                   ORDER BY round_number""",
                (player_id, row[0]),
            ).fetchall()
        return [
            {"type": "narration", "round": round_id, "text": content, "status": {}}
            for round_id, content in messages
        ]

    def _acknowledge_narration_sync(self, player_id: str, round_id: int) -> bool:
        with self._connect() as connection:
            narration_exists = connection.execute(
                """SELECT 1 FROM chat_messages
                   WHERE player_id = ? AND round_number = ? AND role = 'narrator'""",
                (player_id, round_id),
            ).fetchone()
            if narration_exists is None:
                return False
            connection.execute(
                """UPDATE players
                   SET last_ack_round = MAX(last_ack_round, ?),
                       updated_at = CURRENT_TIMESTAMP
                   WHERE player_id = ?""",
                (round_id, player_id),
            )
            return True

    def _get_full_history_sync(self, player_id: str) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT round_number, role, content FROM chat_messages
                   WHERE player_id = ? ORDER BY id""",
                (player_id,),
            ).fetchall()
        return [{"round": row[0], "role": row[1], "content": row[2]} for row in rows]

    def _get_player_display_sync(self, player_id: str) -> dict:
        with self._connect() as connection:
            statusbar = connection.execute(
                "SELECT statusbar_content FROM players WHERE player_id = ?", (player_id,)
            ).fetchone()
            public = connection.execute(
                "SELECT content FROM public_world_info ORDER BY round_id DESC LIMIT 1"
            ).fetchone()
        return {
            "statusbar": self._parse_content(statusbar[0]) if statusbar else {},
            "public_information": self._parse_content(public[0]) if public else {},
        }

    @staticmethod
    def _parse_content(content: str):
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content

    def _get_latest_narration_sync(self, player_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT round_number, content FROM chat_messages
                   WHERE player_id = ? AND role = 'narrator' ORDER BY round_number DESC LIMIT 1""",
                (player_id,),
            ).fetchone()
        return None if row is None else {"round": row[0], "text": row[1]}

    def _get_participants_sync(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT name, is_host, role FROM participants ORDER BY id"
            ).fetchall()
        return [
            {"name": name, "is_host": bool(is_host), "role": role}
            for name, is_host, role in rows
        ]

    def _register_participant_sync(self, name: str, is_host: bool) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT OR IGNORE INTO participants (name, is_host) VALUES (?, ?)",
                (name, int(is_host)),
            )

    def _save_role_assignment_sync(self, assignments: dict[str, str]) -> None:
        with self._connect() as connection:
            connection.execute("UPDATE participants SET role = NULL")
            connection.executemany(
                "UPDATE participants SET role = ? WHERE name = ?",
                ((role, name) for name, role in assignments.items()),
            )

    def _current_round_sync(self) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT round_number FROM rounds WHERE status = 'OPEN' ORDER BY round_number DESC LIMIT 1"
            ).fetchone()
            if row is not None:
                return row[0]
            last_round = connection.execute(
                "SELECT COALESCE(MAX(round_number), 0) FROM rounds"
            ).fetchone()[0]
            round_number = last_round + 1
            connection.execute(
                "INSERT INTO rounds (round_number, status) VALUES (?, 'OPEN')",
                (round_number,),
            )
            return round_number

    def _set_round_stage_sync(self, round_id: int, stage: str) -> None:
        with self._connect() as connection:
            connection.execute(
                "UPDATE rounds SET stage = ? WHERE round_number = ?", (stage, round_id)
            )

    def _get_recovery_data_sync(self, round_id: int) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT stage, action_a, action_b, result_world_state
                   FROM rounds WHERE round_number = ?""",
                (round_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"round {round_id} does not exist")
            public_row = connection.execute(
                "SELECT content FROM public_world_info WHERE round_id = ?", (round_id,)
            ).fetchone()
            views = dict(
                connection.execute(
                    "SELECT player_id, view_content FROM player_views WHERE round_id = ?",
                    (round_id,),
                ).fetchall()
            )
            narrations = {
                player_id: {"text": content, "status": {}}
                for player_id, content in connection.execute(
                    """SELECT player_id, content FROM chat_messages
                       WHERE round_number = ? AND role = 'narrator'""",
                    (round_id,),
                ).fetchall()
            }
            players = {
                player_id: {
                    "status": status,
                    "action": action,
                    "last_ack_round": last_ack_round,
                }
                for player_id, status, action, last_ack_round in connection.execute(
                    "SELECT player_id, status, current_action, last_ack_round FROM players"
                ).fetchall()
            }
        return {
            "stage": RoundStage(row[0]),
            "actions": {
                "A": row[1] or players.get("A", {}).get("action", ""),
                "B": row[2] or players.get("B", {}).get("action", ""),
            },
            "players": players,
            "world_state": row[3],
            "public_world_info": public_row[0] if public_row else None,
            "player_views": views,
            "narrations": narrations,
        }

    def _get_world_state_sync(self) -> str:
        with self._connect() as connection:
            row = connection.execute("SELECT content FROM world_state WHERE id = 1").fetchone()
            if row is None:
                raise RuntimeError("world state has not been initialized")
            return row[0]

    def _save_world_state_sync(self, content: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE world_state
                   SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1""",
                (content,),
            )

    def _save_world_update_sync(
        self, completed: CompletedRound, result: dict
    ) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE world_state
                   SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1""",
                (self._as_text(result["world_state"]),),
            )
            connection.execute(
                """UPDATE rounds
                   SET action_a = ?, action_b = ?, result_world_state = ?
                   WHERE round_number = ?""",
                (
                    completed.actions["A"],
                    completed.actions["B"],
                    self._as_text(result["world_state"]),
                    completed.round_number,
                ),
            )
            connection.execute(
                """INSERT INTO public_world_info (round_id, content)
                   VALUES (?, ?)
                   ON CONFLICT(round_id) DO UPDATE SET
                       content = excluded.content, timestamp = CURRENT_TIMESTAMP""",
                (completed.round_number, self._as_text(result["public_information"])),
            )
            connection.executemany(
                """INSERT INTO player_views (round_id, player_id, view_content)
                   VALUES (?, ?, ?)
                   ON CONFLICT(round_id, player_id) DO UPDATE SET
                       view_content = excluded.view_content,
                       timestamp = CURRENT_TIMESTAMP""",
                (
                    (
                        completed.round_number,
                        player_id,
                        self._as_text(result["player_views"][player_id]),
                    )
                    for player_id in ("A", "B")
                ),
            )
            connection.executemany(
                "UPDATE players SET statusbar_content = ? WHERE player_id = ?",
                (
                    (self._as_text(result["player_statusbar"][player_id]), player_id)
                    for player_id in ("A", "B")
                ),
            )
            connection.execute(
                "UPDATE rounds SET stage = ? WHERE round_number = ?",
                (RoundStage.WORLD_DONE.value, completed.round_number),
            )

    @staticmethod
    def _as_text(value) -> str:
        if isinstance(value, str):
            return value
        return json.dumps(value, ensure_ascii=False)

    def _save_player_views_sync(self, round_id: int, views: dict[str, str]) -> None:
        with self._connect() as connection:
            connection.executemany(
                """INSERT INTO player_views (round_id, player_id, view_content)
                   VALUES (?, ?, ?)
                   ON CONFLICT(round_id, player_id) DO UPDATE SET
                       view_content = excluded.view_content,
                       timestamp = CURRENT_TIMESTAMP""",
                ((round_id, player_id, content) for player_id, content in views.items()),
            )

    def _get_chat_history_sync(
        self, player_id: str, limit: int
    ) -> list[dict[str, str]]:
        with self._connect() as connection:
            rows = connection.execute(
                """SELECT role, content FROM chat_messages
                   WHERE player_id = ? ORDER BY id DESC LIMIT ?""",
                (player_id, limit),
            ).fetchall()
        return [{"role": role, "content": content} for role, content in reversed(rows)]

    def _save_narrations_sync(self, round_id: int, narrations: dict) -> None:
        with self._connect() as connection:
            for player_id, narration in narrations.items():
                existing = connection.execute(
                    """SELECT 1 FROM chat_messages
                       WHERE round_number = ? AND player_id = ? AND role = 'narrator'""",
                    (round_id, player_id),
                ).fetchone()
                if existing is None:
                    connection.execute(
                        """INSERT INTO chat_messages
                           (round_number, player_id, role, content)
                           VALUES (?, ?, 'narrator', ?)""",
                        (round_id, player_id, narration["text"]),
                    )

    def _finish_round_sync(self, completed: CompletedRound) -> None:
        with self._connect() as connection:
            connection.execute(
                """UPDATE rounds
                   SET status = 'COMPLETED', stage = 'FINISHED',
                       completed_at = CURRENT_TIMESTAMP
                   WHERE round_number = ?""",
                (completed.round_number,),
            )
            connection.executemany(
                """INSERT INTO chat_messages
                   (round_number, player_id, role, content) VALUES (?, ?, 'player', ?)""",
                (
                    (completed.round_number, "A", completed.actions["A"]),
                    (completed.round_number, "B", completed.actions["B"]),
                ),
            )

    def _create_round_sync(self, round_number: int) -> None:
        with self._connect() as connection:
            connection.execute(
                "INSERT INTO rounds (round_number, status) VALUES (?, 'OPEN')",
                (round_number,),
            )
            connection.execute(
                """UPDATE players
                   SET status = ?, current_action = '', updated_at = CURRENT_TIMESTAMP""",
                (PlayerStatus.EDITING.value,),
            )
