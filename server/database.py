import asyncio
import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from server.models import CompletedRound, PlayerStatus, RoundStage


SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY,
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
    result_world_state TEXT,
    started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    completed_at TEXT
);

CREATE TABLE IF NOT EXISTS chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    round_number INTEGER NOT NULL,
    player_id TEXT,
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
    player_id TEXT NOT NULL,
    view_content TEXT NOT NULL,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (round_id, player_id),
    FOREIGN KEY (round_id) REFERENCES rounds(round_number)
);

CREATE TABLE IF NOT EXISTS player_statusbars (
    round_id INTEGER NOT NULL,
    player_id TEXT NOT NULL,
    content TEXT NOT NULL,
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

CREATE TABLE IF NOT EXISTS round_actions (
    round_id INTEGER NOT NULL,
    player_id TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (round_id, player_id),
    FOREIGN KEY (round_id) REFERENCES rounds(round_number)
);

"""


class Database:
    """Small SQLite wrapper; blocking work is moved off the asyncio event loop."""

    def __init__(
        self, path: str, role_ids: tuple[str, ...] = ("P1", "P2")
    ) -> None:
        if not role_ids or len(set(role_ids)) != len(role_ids):
            raise ValueError("role ids must be non-empty and unique")
        self.path = Path(path)
        self.role_ids = tuple(role_ids)

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

    async def get_latest_world_update(self) -> dict | None:
        return await asyncio.to_thread(self._get_latest_world_update_sync)

    async def get_role_history(self, player_id: str) -> list[dict]:
        return await asyncio.to_thread(self._get_role_history_sync, player_id)

    async def get_latest_player_view(self, player_id: str):
        return await asyncio.to_thread(self._get_latest_player_view_sync, player_id)

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
            self._validate_schema(connection)
            connection.executemany(
                "INSERT OR IGNORE INTO players (player_id, status) VALUES (?, ?)",
                ((role_id, PlayerStatus.EDITING.value) for role_id in self.role_ids),
            )
            stored_roles = {
                row[0] for row in connection.execute("SELECT player_id FROM players")
            }
            if stored_roles != set(self.role_ids):
                raise RuntimeError(
                    "database roles do not match roles.json; recreate this game instance"
                )
            if initial_statusbars:
                connection.executemany(
                    """UPDATE players SET statusbar_content = ?
                       WHERE player_id = ? AND statusbar_content = '{}'""",
                    (
                        (self._as_text(initial_statusbars[player_id]), player_id)
                        for player_id in self.role_ids
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
    def _validate_schema(connection: sqlite3.Connection) -> None:
        connection.execute("DROP TABLE IF EXISTS participants")

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
        return {
            "statusbar": self._parse_content(statusbar[0]) if statusbar else {},
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

    def _get_latest_world_update_sync(self) -> dict | None:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT round_number, result_world_state FROM rounds
                   WHERE result_world_state IS NOT NULL
                   ORDER BY round_number DESC LIMIT 1"""
            ).fetchone()
            if row is None:
                return None
            round_id, world_state = row
            public = connection.execute(
                "SELECT content FROM public_world_info WHERE round_id = ?", (round_id,)
            ).fetchone()
            views = dict(connection.execute(
                "SELECT player_id, view_content FROM player_views WHERE round_id = ?",
                (round_id,),
            ).fetchall())
            statusbars = dict(connection.execute(
                "SELECT player_id, statusbar_content FROM players"
            ).fetchall())
        return {
            "round": round_id,
            "result": {
                "world_state": self._parse_content(world_state),
                "public_information": self._parse_content(public[0]) if public else {},
                "player_views": {
                    player_id: self._parse_content(content)
                    for player_id, content in views.items()
                },
                "player_statusbar": {
                    player_id: self._parse_content(content)
                    for player_id, content in statusbars.items()
                },
            },
        }

    def _get_role_history_sync(self, player_id: str) -> list[dict]:
        with self._connect() as connection:
            messages = connection.execute(
                """SELECT chat_messages.round_number, chat_messages.role,
                          chat_messages.content
                   FROM chat_messages
                   JOIN rounds ON rounds.round_number = chat_messages.round_number
                   WHERE chat_messages.player_id = ? AND rounds.status = 'COMPLETED'
                   ORDER BY chat_messages.round_number, chat_messages.id""",
                (player_id,),
            ).fetchall()
            statusbars = dict(connection.execute(
                """SELECT player_statusbars.round_id, player_statusbars.content
                   FROM player_statusbars
                   JOIN rounds ON rounds.round_number = player_statusbars.round_id
                   WHERE player_statusbars.player_id = ? AND rounds.status = 'COMPLETED'
                   ORDER BY player_statusbars.round_id""",
                (player_id,),
            ).fetchall())
        by_round: dict[int, dict[str, str]] = {}
        for round_id, role, content in messages:
            by_round.setdefault(round_id, {})[role] = content
        history = []
        for round_id in sorted(set(by_round) | set(statusbars)):
            round_messages = by_round.get(round_id, {})
            if "player" in round_messages:
                history.append({
                    "round": round_id,
                    "kind": "action",
                    "content": round_messages["player"],
                })
            if "narrator" in round_messages:
                history.append({
                    "round": round_id,
                    "kind": "narration",
                    "content": round_messages["narrator"],
                })
            if round_id in statusbars:
                history.append({
                    "round": round_id,
                    "kind": "statusbar",
                    "content": self._parse_content(statusbars[round_id]),
                })
        return history

    def _get_latest_player_view_sync(self, player_id: str):
        with self._connect() as connection:
            row = connection.execute(
                """SELECT view_content FROM player_views
                   WHERE player_id = ? ORDER BY round_id DESC LIMIT 1""",
                (player_id,),
            ).fetchone()
        return self._parse_content(row[0]) if row else None

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
                """SELECT stage, result_world_state
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
            saved_actions = dict(connection.execute(
                "SELECT player_id, content FROM round_actions WHERE round_id = ?",
                (round_id,),
            ).fetchall())
        return {
            "stage": RoundStage(row[0]),
            "actions": {
                role_id: saved_actions.get(
                    role_id, players.get(role_id, {}).get("action", "")
                )
                for role_id in self.role_ids
            },
            "players": players,
            "world_state": row[1],
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
                   SET result_world_state = ?
                   WHERE round_number = ?""",
                (
                    self._as_text(result["world_state"]),
                    completed.round_number,
                ),
            )
            connection.executemany(
                """INSERT INTO round_actions (round_id, player_id, content)
                   VALUES (?, ?, ?)
                   ON CONFLICT(round_id, player_id) DO UPDATE SET
                       content = excluded.content""",
                (
                    (completed.round_number, role_id, completed.actions[role_id])
                    for role_id in self.role_ids
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
                    for player_id in self.role_ids
                ),
            )
            connection.executemany(
                "UPDATE players SET statusbar_content = ? WHERE player_id = ?",
                (
                    (self._as_text(result["player_statusbar"][player_id]), player_id)
                    for player_id in self.role_ids
                ),
            )
            connection.executemany(
                """INSERT INTO player_statusbars (round_id, player_id, content)
                   VALUES (?, ?, ?)
                   ON CONFLICT(round_id, player_id) DO UPDATE SET
                       content = excluded.content,
                       timestamp = CURRENT_TIMESTAMP""",
                (
                    (
                        completed.round_number,
                        player_id,
                        self._as_text(result["player_statusbar"][player_id]),
                    )
                    for player_id in self.role_ids
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
                    (completed.round_number, role_id, completed.actions[role_id])
                    for role_id in self.role_ids
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
