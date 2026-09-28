"""Explicit legacy single-game runner.

The normal multi-room Platform does not call this module. RoomManager constructs
GameServer runtimes from platform Game metadata through ``factory.py``.
"""

import logging
from dataclasses import replace
from pathlib import Path

from server.config import load_settings
from server.gameserver.database import Database
from server.gameserver.game_server import GameServer
from server.gameserver.llm.client import LLMClient
from server.gameserver.llm.narrator import Narrator
from server.gameserver.llm.player_view import PlayerViewGenerator
from server.gameserver.llm.prompt_loader import PromptLoader
from server.gameserver.llm.world_update import WorldUpdater
from server.gameserver.protocol import MAX_WEBSOCKET_MESSAGE_BYTES
from server.gameserver.roles import RoleConfig, migrate_roles_to_metadata
from server.platform.database import PlatformDatabase
from server.platform.legacy_game_web import create_web_app, serve_web

logger = logging.getLogger(__name__)


async def run_game_server(
    game_path: Path,
    scenario_name: str,
    no_room_key: bool = False,
    owner_user_id: int | None = None,
) -> None:
    settings = load_settings()
    if no_room_key:
        settings = replace(settings, room_key="")
    logger.info("Legacy Room Key: %s", settings.room_key or "disabled")
    # Explicit local instance path (not a scan): convert a roles.json-era payload
    # once so the rest of the runtime only ever reads metadata.json.
    if migrate_roles_to_metadata(game_path):
        logger.info("Migrated %s payload to metadata.json", game_path)
    role_config = RoleConfig.load(
        game_path,
        min_count=settings.min_role_count,
        max_count=settings.max_role_count,
    )
    loader = PromptLoader(str(game_path), role_config)
    database = Database(str(game_path / "game.db"), role_config.role_ids)
    await database.initialize(
        loader.json("world/initial_state.json"),
        {role_id: loader.statusbar(role_id) for role_id in role_config.role_ids},
    )
    if owner_user_id is not None and await database.get_owner_user_id() is None:
        await database.set_owner_user_id(owner_user_id)
    owner = await database.get_owner_user_id()
    if owner is None:
        raise RuntimeError(
            "this legacy game instance has no owner; use --owner <username>"
        )
    platform = PlatformDatabase(settings.platform_db)
    platform.initialize(settings.legacy_accounts_db)
    llm = LLMClient(settings.llm_api_key, settings.llm_base_url, settings.llm_model)
    game = GameServer(
        database,
        WorldUpdater(llm, loader, settings.world_update_max_tokens),
        PlayerViewGenerator(llm, loader),
        Narrator(llm, loader, settings.narration_max_tokens),
        await database.current_round(),
        scenario_name,
        role_config.names,
        room_key=settings.room_key,
        disconnect_grace_seconds=settings.disconnect_grace_seconds,
        role_config=role_config,
        openings={role_id: loader.opening(role_id) for role_id in role_config.role_ids},
        narrator_history_rounds=settings.narrator_history_rounds,
        owner_user_id=owner,
    )
    await game.recover_round()
    app = create_web_app(
        game,
        platform,
        ui_font_scale=settings.ui_font_scale,
        allow_registration=settings.allow_registration,
        auth_session_days=settings.auth_session_days,
        secure_cookie=settings.secure_cookie,
        allowed_origins=settings.allowed_origins,
    )
    await serve_web(
        app, settings.web_host, settings.web_port, MAX_WEBSOCKET_MESSAGE_BYTES
    )
