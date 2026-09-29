from pathlib import Path

from server.config import Settings, load_settings
from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.gameserver.llm.client import LLMClient
from server.gameserver.llm.narrator import Narrator
from server.gameserver.llm.prompt_loader import PromptLoader
from server.gameserver.llm.world_update import WorldUpdater
from server.gameserver.roles import RoleConfig


async def load_game_server(
    game_path: Path, owner_user_id: int, settings: Settings | None = None
) -> GameServer:
    settings = settings or load_settings()
    roles = RoleConfig.load(
        game_path,
        min_count=settings.min_role_count,
        max_count=settings.max_role_count,
    )
    loader = PromptLoader(str(game_path), roles)
    status_roles = tuple(role for role in roles.role_ids if loader.character_status_schema(role) is not None)
    database = Database(str(game_path / "game.db"), roles.role_ids, status_roles)
    await database.initialize(
        loader.json("world/world_state_initial.json"),
        {role: loader.character_status_initial(role) for role in status_roles},
    )
    llm = LLMClient(settings.llm_api_key, settings.llm_base_url, settings.llm_model)
    game = GameServer(
        database,
        WorldUpdater(llm, loader, settings.world_update_max_tokens),
        Narrator(llm, loader, settings.narration_max_tokens),
        await database.current_round(),
        game_path.name,
        roles.names,
        room_key="",
        disconnect_grace_seconds=settings.room_disconnect_timeout_seconds,
        role_config=roles,
        openings={role: loader.opening(role) for role in roles.role_ids},
        narrator_history_rounds=settings.narrator_history_rounds,
        owner_user_id=owner_user_id,
        max_users=settings.max_room_users,
    )
    await game.recover_round()
    return game
