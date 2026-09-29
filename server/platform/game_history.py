"""Owner-only ZIP download of a Game's completed, linear story history."""

import asyncio
import io
import json
import zipfile
from pathlib import Path

from server.gameserver.database import Database
from server.gameserver.roles import RoleConfig
from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager


def _json_bytes(value: object) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def _archive(game_id: str, game_name: str, roles: RoleConfig, history: dict) -> bytes:
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", _json_bytes({
            "game_id": game_id,
            "game_name": game_name,
            "roles": roles.definitions,
            "completed_rounds": len(history["rounds"]),
        }))
        archive.writestr("initial_world_state.json", _json_bytes(history["initial_world_state"]))
        archive.writestr("current_world_state.json", _json_bytes(history["current_world_state"]))
        archive.writestr("world_states.json", _json_bytes([
            {"round": row["round"], "world_state": row["world_state"]}
            for row in history["rounds"]
        ]))
        for row in history["rounds"]:
            archive.writestr(f"rounds/{row['round']:04d}.json", _json_bytes(row))
    return output.getvalue()


async def export_game_history(
    database: PlatformDatabase, room_manager: RoomManager, game_id: str,
    owner_user_id: int,
) -> bytes:
    game = await asyncio.to_thread(database.get_game, game_id)
    if game is None:
        raise ValueError("game_not_found")
    if game.owner_user_id != owner_user_id:
        raise PermissionError("forbidden")
    root = Path(room_manager.games_dir) / game_id
    if not root.is_dir():
        raise ValueError("game_history_unavailable")
    roles = await asyncio.to_thread(RoleConfig.load, root)
    runtime = next((room.game_server for room in room_manager.rooms.values()
                    if room.game_id == game_id), None)
    if runtime is not None:
        if runtime.rounds.is_processing():
            raise ValueError("game_processing")
        async with runtime.command_lock:
            if runtime.rounds.is_processing():
                raise ValueError("game_processing")
            history = await runtime.database.export_history()
    elif (root / "game.db").is_file():
        history = await Database(str(root / "game.db"), roles.role_ids).export_history()
    else:
        try:
            initial = await asyncio.to_thread(
                lambda: json.loads((root / "world/world_state_initial.json").read_text(encoding="utf-8"))
            )
        except (OSError, json.JSONDecodeError) as exc:
            raise ValueError("game_history_unavailable") from exc
        history = {"initial_world_state": initial, "current_world_state": initial, "rounds": []}
    return await asyncio.to_thread(_archive, game_id, game.name, roles, history)
