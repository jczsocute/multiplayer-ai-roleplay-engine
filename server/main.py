"""Platform Core v0.1 entry point and small maintenance CLI."""

import argparse
import asyncio
import logging

from server.config import PlatformSettings, load_platform_settings, load_role_limits
from server.gameserver.runtime import run_game_server
from server.platform.bootstrap import (
    BUNDLED_SCRIPTS, BUNDLED_TEMPLATES, bootstrap_templates,
)
from server.platform.catalog import (
    import_game, import_template, migrate_catalog_payloads,
)
from server.platform.database import PlatformDatabase
from server.platform.scenario_manager import ScenarioManager
from server.platform.room_manager import RoomManager
from server.platform.web import create_platform_app, serve_platform
from server.platform.starter_templates import seed_starter_templates

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger(__name__)


def _database() -> tuple[PlatformDatabase, PlatformSettings]:
    settings = load_platform_settings()
    database = PlatformDatabase(settings.platform_db)
    database.initialize(settings.legacy_accounts_db)
    return database, settings


def _owner_id(
    parser: argparse.ArgumentParser, database: PlatformDatabase, username: str | None
) -> int:
    if not username:
        parser.error("this command requires --owner <username>")
    user = database.user_by_username(username)
    if user is None:
        parser.error(f"unknown account: {username} (register it in the Web client first)")
    return user.id


def main() -> None:
    parser = argparse.ArgumentParser(description="AI RP Engine Platform Core")
    action = parser.add_mutually_exclusive_group()
    action.add_argument("--list-games", action="store_true")
    action.add_argument("--delete-game")
    action.add_argument("--list-scenarios", action="store_true")
    action.add_argument("--create-scenario")
    action.add_argument("--delete-scenario")
    action.add_argument("--import-template")
    action.add_argument("--import-game")
    action.add_argument("--bootstrap-templates", action="store_true")
    action.add_argument("--seed-starter-templates", action="store_true")
    action.add_argument("--set-admin")
    action.add_argument("--unset-admin")
    action.add_argument(
        "--game", help="deprecated: explicitly run one legacy GameServer instance"
    )
    parser.add_argument("--role-count", type=int)
    parser.add_argument("--owner", help="existing platform username")
    parser.add_argument("--public", action="store_true", dest="is_public")
    parser.add_argument("--name", help="display name for an imported Game")
    parser.add_argument("--no-room-key", action="store_true")
    args = parser.parse_args()

    try:
        min_roles, max_roles = load_role_limits()
        manager = ScenarioManager(
            min_role_count=min_roles, max_role_count=max_roles
        )
    except (ValueError, RuntimeError) as exc:
        parser.error(str(exc))

    if args.list_scenarios:
        print("\n".join(manager.list_scenarios()) or "No playable scenarios.")
        return
    if args.create_scenario:
        try:
            role_count = args.role_count
            if role_count is None:
                role_count = int(input(f"请输入角色数量 [{min_roles}-{max_roles}]: "))
            path = manager.create_scenario(args.create_scenario, role_count)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Created scenario: {path}")
        return
    if args.delete_scenario:
        try:
            manager.delete_scenario(args.delete_scenario)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Deleted scenario: {args.delete_scenario}")
        return
    if args.list_games:
        print("\n".join(manager.list_games()) or "No legacy game instances.")
        return
    if args.delete_game:
        try:
            manager.delete_game(args.delete_game)
        except ValueError as exc:
            parser.error(str(exc))
        print(f"Deleted legacy game: {args.delete_game}")
        return

    database, settings = _database()
    if args.seed_starter_templates:
        from pathlib import Path
        for user in database.list_users():
            created = seed_starter_templates(database, Path(manager.templates_dir), user.id)
            print(f"{user.username}: seeded {len(created)} starter template(s)")
        return
    if args.set_admin or args.unset_admin:
        username = args.set_admin or args.unset_admin
        try:
            user = database.set_admin(username, bool(args.set_admin))
        except ValueError:
            parser.error(
                f"unknown account: {username} (register it in the Web client first)"
            )
        state = "platform admin" if user.is_admin else "regular user"
        print(f"{user.username} is now a {state}.")
        return
    if args.bootstrap_templates:
        owner_id = _owner_id(parser, database, args.owner)
        results = bootstrap_templates(
            database, manager.templates_dir, manager.games_dir, owner_id,
            BUNDLED_TEMPLATES,
        )
        for result in results:
            if result.status == "imported":
                print(f"Imported bundled template: {result.template_id} ({result.name})")
            elif result.status == "skipped":
                print(f"Already in catalog, skipped: {result.name} ({result.template_id})")
            else:
                print(f"Not found on disk, skipped: {result.name}")
        return
    if args.import_template:
        owner_id = _owner_id(parser, database, args.owner)
        source = manager.templates_dir / args.import_template
        try:
            metadata = import_template(
                database, source, manager.templates_dir, owner_id,
                args.import_template, is_public=args.is_public,
            )
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(f"Imported template: {metadata.id} ({metadata.name})")
        return
    if args.import_game:
        owner_id = _owner_id(parser, database, args.owner)
        source = manager.games_dir / args.import_game
        try:
            metadata = import_game(
                database, source, manager.games_dir, owner_id,
                args.name or args.import_game,
            )
        except (ValueError, OSError) as exc:
            parser.error(str(exc))
        print(f"Imported game: {metadata.id} ({metadata.name})")
        return
    if args.game:
        logger.warning("--game starts the deprecated legacy single-game runtime")
        game_path = manager.games_dir / args.game
        owner_id = None
        if not game_path.is_dir():
            if args.game not in manager.list_scenarios():
                parser.error(f"unknown legacy game or scenario: {args.game}")
            owner_id = _owner_id(parser, database, args.owner)
            game_path = manager.create_game(args.game, args.game)
        try:
            asyncio.run(run_game_server(
                game_path, args.game, no_room_key=args.no_room_key,
                owner_user_id=owner_id,
            ))
        except KeyboardInterrupt:
            logger.info("Legacy GameServer stopped")
        return

    try:
        asyncio.run(_run_platform(database, settings))
    except KeyboardInterrupt:
        logger.info("Platform stopped")


async def _run_platform(
    database: PlatformDatabase, settings: PlatformSettings
) -> None:
    min_role_count, max_role_count = load_role_limits()
    rooms = RoomManager(
        database, max_users=settings.max_room_users,
        min_role_count=min_role_count, max_role_count=max_role_count,
        disconnect_timeout_seconds=settings.room_disconnect_timeout_seconds,
    )
    # Registered payloads only: idempotent, so a normal startup is a no-op. This
    # also backfills `metadata.json.title` and mirrors it into the catalog row.
    bundled_titles = {name: script.title for name, script in BUNDLED_SCRIPTS.items()}
    for label in migrate_catalog_payloads(
        database, rooms.templates_dir, rooms.games_dir, titles=bundled_titles
    ):
        logger.info("Migrated %s payload to metadata.json", label)
    await rooms.load_active_rooms()
    app = create_platform_app(
        database,
        ui_font_scale=settings.ui_font_scale,
        min_role_count=min_role_count,
        max_role_count=max_role_count,
        allow_registration=settings.allow_registration,
        auth_session_days=settings.auth_session_days,
        secure_cookie=settings.secure_cookie,
        room_manager=rooms,
        allowed_origins=settings.allowed_origins,
    )
    await serve_platform(app, settings.web_host, settings.web_port)


if __name__ == "__main__":
    main()
