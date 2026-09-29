import asyncio
import json
import sqlite3
from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path

from server.gameserver.models import CompletedRound, PlayerStatus, RoundStage


SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    player_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    current_action TEXT NOT NULL DEFAULT '',
    character_status_content TEXT,
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

CREATE TABLE IF NOT EXISTS character_views (
    round_id INTEGER NOT NULL,
    player_id TEXT NOT NULL,
    character_view_content TEXT NOT NULL,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (round_id, player_id),
    FOREIGN KEY (round_id) REFERENCES rounds(round_number)
);

CREATE TABLE IF NOT EXISTS character_statuses (
    round_id INTEGER NOT NULL,
    player_id TEXT NOT NULL,
    content TEXT NOT NULL,
    timestamp TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (round_id, player_id),
    FOREIGN KEY (round_id) REFERENCES rounds(round_number)
);

CREATE TABLE IF NOT EXISTS round_actions (
    round_id INTEGER NOT NULL,
    player_id TEXT NOT NULL,
    content TEXT NOT NULL,
    PRIMARY KEY (round_id, player_id),
    FOREIGN KEY (round_id) REFERENCES rounds(round_number)
);

CREATE TABLE IF NOT EXISTS game_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

"""

# Child tables bound to a round; rows are deleted before their rounds row so that
# the foreign keys stay consistent during rollback / retry.
_ROUND_TABLES = (
    ("chat_messages", "round_number"),
    ("character_views", "round_id"),
    ("character_statuses", "round_id"),
    ("round_actions", "round_id"),
)


def _round_tables(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    """Also clear historical public-info rows when rolling back an old save."""
    legacy = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'public_world_info'"
    ).fetchone()
    return _ROUND_TABLES + (("public_world_info", "round_id"),) if legacy else _ROUND_TABLES


class Database:
    """Small SQLite wrapper; blocking work is moved off the asyncio event loop."""

    def __init__(
        self, path: str, role_ids: tuple[str, ...] = ("P1", "P2"),
        status_roles: tuple[str, ...] | None = None,
    ) -> None:
        if not role_ids or len(set(role_ids)) != len(role_ids):
            raise ValueError("role ids must be non-empty and unique")
        self.path = Path(path)
        self.role_ids = tuple(role_ids)
        self.status_roles = tuple(role_ids if status_roles is None else status_roles)
        if not set(self.status_roles) <= set(self.role_ids):
            raise ValueError("status roles must be valid role ids")

    async def initialize(self, initial_world_state=None, initial_character_statuses=None) -> None:
        await asyncio.to_thread(self._initialize_sync, initial_world_state, initial_character_statuses)

    async def current_round(self) -> int:
        return await asyncio.to_thread(self._current_round_sync)

    async def set_round_stage(self, round_id: int, stage: RoundStage) -> None:
        await asyncio.to_thread(self._set_round_stage_sync, round_id, stage.value)

    async def get_recovery_data(self, round_id: int) -> dict:
        return await asyncio.to_thread(self._get_recovery_data_sync, round_id)

    async def save_player(self, player_id: str, status: PlayerStatus, action: str) -> None:
        await asyncio.to_thread(self._save_player_sync, player_id, status.value, action)

    async def get_player_display(self, player_id: str) -> dict:
        return await asyncio.to_thread(self._get_player_display_sync, player_id)

    async def get_latest_world_update(self) -> dict | None:
        return await asyncio.to_thread(self._get_latest_world_update_sync)

    async def get_role_history(self, player_id: str) -> list[dict]:
        return await asyncio.to_thread(self._get_role_history_sync, player_id)

    async def export_history(self) -> dict:
        return await asyncio.to_thread(self._export_history_sync)

    async def get_world_state(self) -> str:
        return await asyncio.to_thread(self._get_world_state_sync)

    async def save_world_update(
        self, completed: CompletedRound, result: dict
    ) -> None:
        await asyncio.to_thread(self._save_world_update_sync, completed, result)

    async def lock_round_actions(self, completed: CompletedRound) -> None:
        """Persist submitted actions before the first AI request can fail."""
        await asyncio.to_thread(self._lock_round_actions_sync, completed)

    async def get_narrator_history(
        self, player_id: str, rounds: int
    ) -> list[dict[str, str]]:
        """Recent completed rounds for the Narrator prompt, never a partial round.

        Display history (``get_role_history``) stays complete; this is the bounded
        Narrator context window. Opening and Room Chat never appear here.
        """
        return await asyncio.to_thread(self._get_narrator_history_sync, player_id, rounds)

    async def save_narrations(self, round_id: int, narrations: dict) -> None:
        await asyncio.to_thread(self._save_narrations_sync, round_id, narrations)

    async def finish_round(self, completed: CompletedRound) -> None:
        await asyncio.to_thread(self._finish_round_sync, completed)

    async def create_round(self, round_number: int) -> None:
        await asyncio.to_thread(self._create_round_sync, round_number)

    async def get_owner_user_id(self) -> int | None:
        value = await asyncio.to_thread(self._get_metadata_sync, "owner_user_id")
        return None if value is None else int(value)

    async def set_owner_user_id(self, user_id: int) -> None:
        await asyncio.to_thread(self._set_metadata_sync, "owner_user_id", str(user_id))

    async def last_completed_round(self) -> int | None:
        return await asyncio.to_thread(self._last_completed_round_sync)

    async def prepare_retry_round(self, round_number: int) -> dict[str, str]:
        """Reset round ``round_number`` for a full re-run and return its actions.

        One transaction: restore the canonical world to that round's base, drop the
        round's AI-derived output and everything after it, and clear pending input.
        """
        return await asyncio.to_thread(self._prepare_retry_round_sync, round_number)

    async def rollback_to_round(self, round_number: int) -> int:
        """Keep rounds up to ``round_number``, delete the rest; return the next round."""
        return await asyncio.to_thread(self._rollback_to_round_sync, round_number)

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def _initialize_sync(self, initial_world_state=None, initial_character_statuses=None) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            existing = {row[0] for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )}
            if "player_views" in existing and "character_views" not in existing:
                connection.execute("ALTER TABLE player_views RENAME TO character_views")
                connection.execute("ALTER TABLE character_views RENAME COLUMN view_content TO character_view_content")
            if "player_statusbars" in existing and "character_statuses" not in existing:
                connection.execute("ALTER TABLE player_statusbars RENAME TO character_statuses")
            if "players" in existing:
                columns = {row[1] for row in connection.execute("PRAGMA table_info(players)")}
                if "statusbar_content" in columns and "character_status_content" not in columns:
                    connection.execute("ALTER TABLE players RENAME COLUMN statusbar_content TO character_status_content")
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
                    "database roles do not match metadata.json; recreate this game instance"
                )
            if initial_character_statuses:
                connection.executemany(
                    """UPDATE players SET character_status_content = ?
                       WHERE player_id = ? AND (character_status_content IS NULL OR character_status_content = '{}')""",
                    (
                        (self._as_text(initial_character_statuses[player_id]), player_id)
                        for player_id in self.status_roles
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
            if initial_world_state is not None:
                # Stable base for retrying/rolling back to Round 1; never overwritten.
                connection.execute(
                    """INSERT OR IGNORE INTO game_metadata (key, value)
                       VALUES ('initial_world_state', ?)""",
                    (self._as_text(initial_world_state),),
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

    def _get_player_display_sync(self, player_id: str) -> dict:
        with self._connect() as connection:
            character_status = connection.execute(
                "SELECT character_status_content FROM players WHERE player_id = ?", (player_id,)
            ).fetchone()
        return {
            "character_status": self._parse_content(character_status[0])
            if player_id in self.status_roles and character_status and character_status[0] is not None
            else None,
        }

    @staticmethod
    def _parse_content(content: str):
        try:
            return json.loads(content)
        except (json.JSONDecodeError, TypeError):
            return content

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
            views = dict(connection.execute(
                "SELECT player_id, character_view_content FROM character_views WHERE round_id = ?",
                (round_id,),
            ).fetchall())
            character_statuses = dict(connection.execute(
                "SELECT player_id, character_status_content FROM players"
            ).fetchall())
        return {
            "round": round_id,
            "result": {
                "world_state": self._parse_content(world_state),
                "character_views": {
                    player_id: self._parse_content(content)
                    for player_id, content in views.items()
                },
                "character_status": {
                    player_id: self._parse_content(character_statuses[player_id])
                    for player_id in self.status_roles
                    if character_statuses.get(player_id) is not None
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
            character_statuses = dict(connection.execute(
                """SELECT character_statuses.round_id, character_statuses.content
                   FROM character_statuses
                   JOIN rounds ON rounds.round_number = character_statuses.round_id
                   WHERE character_statuses.player_id = ? AND rounds.status = 'COMPLETED'
                   ORDER BY character_statuses.round_id""",
                (player_id,),
            ).fetchall())
        by_round: dict[int, dict[str, str]] = {}
        for round_id, role, content in messages:
            by_round.setdefault(round_id, {})[role] = content
        history = []
        for round_id in sorted(set(by_round) | set(character_statuses)):
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
            if round_id in character_statuses:
                history.append({
                    "round": round_id,
                    "kind": "character_status",
                    "content": self._parse_content(character_statuses[round_id]),
                })
        return history

    def _export_history_sync(self) -> dict:
        """A consistent snapshot of completed story rounds for the owner export."""
        if not self.path.is_file():
            raise ValueError("game_history_unavailable")
        with self._connect() as connection:
            connection.execute("BEGIN")
            processing = connection.execute(
                "SELECT 1 FROM players WHERE status = 'PROCESSING' LIMIT 1"
            ).fetchone()
            processing_round = connection.execute(
                """SELECT 1 FROM rounds WHERE status = 'OPEN'
                   AND stage NOT IN ('WAITING_INPUT', 'FINISHED') LIMIT 1"""
            ).fetchone()
            if processing or processing_round:
                raise ValueError("game_processing")
            initial = connection.execute(
                "SELECT value FROM game_metadata WHERE key = 'initial_world_state'"
            ).fetchone()
            current = connection.execute(
                "SELECT content FROM world_state WHERE id = 1"
            ).fetchone()
            rows = connection.execute(
                """SELECT round_number, result_world_state, completed_at FROM rounds
                   WHERE status = 'COMPLETED' ORDER BY round_number"""
            ).fetchall()
            rounds = {
                number: {
                    "round": number,
                    "world_state": self._parse_content(world),
                    "actions": {}, "narrations": {},
                    "character_views": {}, "character_status": {},
                    "completed_at": completed_at,
                }
                for number, world, completed_at in rows
            }
            for number, role_id, content in connection.execute(
                "SELECT round_id, player_id, content FROM round_actions ORDER BY round_id, player_id"
            ):
                if number in rounds:
                    rounds[number]["actions"][role_id] = content
            for number, role_id, kind, content in connection.execute(
                """SELECT round_number, player_id, role, content FROM chat_messages
                   WHERE role IN ('player', 'narrator') ORDER BY round_number, id"""
            ):
                if number in rounds and role_id is not None:
                    key = "actions" if kind == "player" else "narrations"
                    if key == "actions":
                        rounds[number][key].setdefault(role_id, content)
                    else:
                        rounds[number][key][role_id] = content
            for number, role_id, content in connection.execute(
                "SELECT round_id, player_id, character_view_content FROM character_views"
            ):
                if number in rounds:
                    rounds[number]["character_views"][role_id] = self._parse_content(content)
            for number, role_id, content in connection.execute(
                "SELECT round_id, player_id, content FROM character_statuses"
            ):
                if number in rounds:
                    rounds[number]["character_status"][role_id] = self._parse_content(content)
        return {
            "initial_world_state": self._parse_content(initial[0]) if initial else None,
            "current_world_state": self._parse_content(current[0]) if current else None,
            "rounds": list(rounds.values()),
        }

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

    def _lock_round_actions_sync(self, completed: CompletedRound) -> None:
        with self._connect() as connection:
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

    def _get_recovery_data_sync(self, round_id: int) -> dict:
        with self._connect() as connection:
            row = connection.execute(
                """SELECT stage, result_world_state
                   FROM rounds WHERE round_number = ?""",
                (round_id,),
            ).fetchone()
            if row is None:
                raise RuntimeError(f"round {round_id} does not exist")
            views = dict(
                connection.execute(
                    "SELECT player_id, character_view_content FROM character_views WHERE round_id = ?",
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
                }
                for player_id, status, action in connection.execute(
                    "SELECT player_id, status, current_action FROM players"
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
            "locked": bool(saved_actions),
            "players": players,
            "world_state": row[1],
            "character_views": {
                role_id: self._parse_content(content)
                for role_id, content in views.items()
            },
            "narrations": narrations,
        }

    def _get_world_state_sync(self) -> str:
        with self._connect() as connection:
            row = connection.execute("SELECT content FROM world_state WHERE id = 1").fetchone()
            if row is None:
                raise RuntimeError("world state has not been initialized")
            return row[0]

    def _save_world_update_sync(
        self, completed: CompletedRound, result: dict
    ) -> None:
        """Persist the whole WorldUpdater result and advance to WORLD_DONE atomically.

        Everything below runs in one ``sqlite3`` transaction (``_connect`` commits on
        success, rolls back on error). ``WORLD_DONE`` is observability only: recovery
        never resumes from a partial pipeline, it re-runs the round from its base.
        """
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
            connection.executemany(
                """INSERT INTO character_views (round_id, player_id, character_view_content)
                   VALUES (?, ?, ?)
                   ON CONFLICT(round_id, player_id) DO UPDATE SET
                       character_view_content = excluded.character_view_content,
                       timestamp = CURRENT_TIMESTAMP""",
                (
                    (
                        completed.round_number,
                        player_id,
                        self._as_text(result["character_views"][player_id]),
                    )
                    for player_id in self.role_ids
                ),
            )
            connection.executemany(
                "UPDATE players SET character_status_content = ? WHERE player_id = ?",
                (
                    (self._as_text(result["character_status"][player_id]), player_id)
                    for player_id in self.status_roles
                ),
            )
            connection.executemany(
                """INSERT INTO character_statuses (round_id, player_id, content)
                   VALUES (?, ?, ?)
                   ON CONFLICT(round_id, player_id) DO UPDATE SET
                       content = excluded.content,
                       timestamp = CURRENT_TIMESTAMP""",
                (
                    (
                        completed.round_number,
                        player_id,
                        self._as_text(result["character_status"][player_id]),
                    )
                    for player_id in self.status_roles
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

    def _get_narrator_history_sync(
        self, player_id: str, rounds: int
    ) -> list[dict[str, str]]:
        """Action + narration for the most recent ``rounds`` completed rounds.

        Rounds are selected whole, so the Narrator window never starts or ends in
        the middle of a round. Room Chat and Opening are never in chat_messages.
        """
        with self._connect() as connection:
            round_ids = [
                row[0]
                for row in connection.execute(
                    """SELECT chat_messages.round_number
                       FROM chat_messages
                       JOIN rounds ON rounds.round_number = chat_messages.round_number
                       WHERE chat_messages.player_id = ?
                         AND chat_messages.role IN ('player', 'narrator')
                         AND rounds.status = 'COMPLETED'
                       GROUP BY chat_messages.round_number
                       ORDER BY chat_messages.round_number DESC
                       LIMIT ?""",
                    (player_id, rounds),
                ).fetchall()
            ]
            if not round_ids:
                return []
            placeholders = ", ".join("?" for _ in round_ids)
            rows = connection.execute(
                f"""SELECT round_number, role, content FROM chat_messages
                    WHERE player_id = ? AND role IN ('player', 'narrator')
                      AND round_number IN ({placeholders})
                    ORDER BY round_number,
                             CASE role WHEN 'player' THEN 0 ELSE 1 END,
                             id""",
                (player_id, *round_ids),
            ).fetchall()
        return [{"role": role, "content": content} for _, role, content in rows]

    def _save_narrations_sync(self, round_id: int, narrations: dict) -> None:
        """Rewrite this round's narrations; a retry always replaces all of them."""
        with self._connect() as connection:
            connection.execute(
                "DELETE FROM chat_messages WHERE round_number = ? AND role = 'narrator'",
                (round_id,),
            )
            connection.executemany(
                """INSERT INTO chat_messages (round_number, player_id, role, content)
                   VALUES (?, ?, 'narrator', ?)""",
                (
                    (round_id, player_id, narration["text"])
                    for player_id, narration in narrations.items()
                ),
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

    def _get_metadata_sync(self, key: str) -> str | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT value FROM game_metadata WHERE key = ?", (key,)
            ).fetchone()
        return None if row is None else row[0]

    def _set_metadata_sync(self, key: str, value: str) -> None:
        with self._connect() as connection:
            connection.execute(
                """INSERT INTO game_metadata (key, value) VALUES (?, ?)
                   ON CONFLICT(key) DO UPDATE SET value = excluded.value""",
                (key, value),
            )

    # --- single linear timeline: retry / rollback -----------------------------

    def _clear_player_inputs_sync(self, connection: sqlite3.Connection) -> None:
        connection.execute(
            """UPDATE players
               SET status = ?, current_action = '', updated_at = CURRENT_TIMESTAMP""",
            (PlayerStatus.EDITING.value,),
        )

    @staticmethod
    def _round_actions_sync(
        connection: sqlite3.Connection, round_id: int
    ) -> dict[str, str]:
        saved = dict(connection.execute(
            "SELECT player_id, content FROM round_actions WHERE round_id = ?",
            (round_id,),
        ).fetchall())
        if not saved:
            saved = dict(connection.execute(
                """SELECT player_id, content FROM chat_messages
                   WHERE round_number = ? AND role = 'player'""",
                (round_id,),
            ).fetchall())
        if not saved:
            saved = dict(connection.execute(
                "SELECT player_id, current_action FROM players WHERE current_action != ''"
            ).fetchall())
        return saved

    @staticmethod
    def _delete_rounds_after_sync(
        connection: sqlite3.Connection, keep_round: int
    ) -> None:
        for table, column in _round_tables(connection):
            connection.execute(
                f"DELETE FROM {table} WHERE {column} IN "
                "(SELECT round_number FROM rounds WHERE round_number > ?)",
                (keep_round,),
            )
        connection.execute("DELETE FROM rounds WHERE round_number > ?", (keep_round,))

    @staticmethod
    def _base_world_state_sync(
        connection: sqlite3.Connection, round_number: int
    ) -> str:
        if round_number <= 1:
            row = connection.execute(
                "SELECT value FROM game_metadata WHERE key = 'initial_world_state'"
            ).fetchone()
            if row is None:
                raise ValueError("initial world state is not available")
            return row[0]
        previous = connection.execute(
            "SELECT status, result_world_state FROM rounds WHERE round_number = ?",
            (round_number - 1,),
        ).fetchone()
        if previous is None or previous[0] != "COMPLETED" or previous[1] is None:
            raise ValueError(f"round {round_number - 1} is not finished")
        return previous[1]

    def _last_completed_round_sync(self) -> int | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT MAX(round_number) FROM rounds WHERE status = 'COMPLETED'"
            ).fetchone()
        return row[0]

    def _prepare_retry_round_sync(self, round_number: int) -> dict[str, str]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status FROM rounds WHERE round_number = ?", (round_number,)
            ).fetchone()
            if row is None:
                raise ValueError(f"round {round_number} does not exist")
            saved = self._round_actions_sync(connection, round_number)
            actions = {role_id: saved.get(role_id, "") for role_id in self.role_ids}
            if any(not text.strip() for text in actions.values()):
                raise ValueError(
                    f"round {round_number} has no complete actions to retry"
                )
            base_world = self._base_world_state_sync(connection, round_number)
            self._delete_rounds_after_sync(connection, round_number)
            for table, column in _round_tables(connection):
                connection.execute(
                    f"DELETE FROM {table} WHERE {column} = ?", (round_number,)
                )
            connection.execute(
                """UPDATE rounds
                   SET status = 'OPEN', stage = ?, result_world_state = NULL,
                       completed_at = NULL
                   WHERE round_number = ?""",
                (RoundStage.WAITING_INPUT.value, round_number),
            )
            connection.execute(
                """UPDATE world_state
                   SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1""",
                (base_world,),
            )
            self._clear_player_inputs_sync(connection)
        return actions

    def _rollback_to_round_sync(self, round_number: int) -> int:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, result_world_state FROM rounds WHERE round_number = ?",
                (round_number,),
            ).fetchone()
            if row is None:
                raise ValueError(f"round {round_number} does not exist")
            if row[0] != "COMPLETED" or row[1] is None:
                raise ValueError(f"round {round_number} is not a finished round")
            base_world = row[1]
            self._delete_rounds_after_sync(connection, round_number)
            connection.execute(
                "INSERT INTO rounds (round_number, status) VALUES (?, 'OPEN')",
                (round_number + 1,),
            )
            connection.execute(
                """UPDATE world_state
                   SET content = ?, updated_at = CURRENT_TIMESTAMP WHERE id = 1""",
                (base_world,),
            )
            self._clear_player_inputs_sync(connection)
        return round_number + 1
