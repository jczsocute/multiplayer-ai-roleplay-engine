"""Platform Administration commands.

One implementation shared by every entry point: the loopback Admin WebSocket
(used by ``client/admin.py``) and the maintenance CLI. Commands only call
existing Platform helpers — PlatformDatabase, catalog and RoomManager — so the
running server's in-memory state never diverges from the database.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

from server.platform.bootstrap import bootstrap_templates
from server.gameserver.roles import METADATA_FILENAME
from server.platform.catalog import (
    delete_template_payload, has_payload, import_template, load_payload_metadata,
    rename_template, template_role_names,
)
from server.platform.database import PlatformDatabase
from server.platform.room_manager import RoomManager

logger = logging.getLogger(__name__)

COMMANDS = (
    "users", "user", "create-user", "set-admin", "unset-admin", "delete-user",
    "templates", "template", "rename-template", "set-template-public",
    "delete-template", "import-template",
    "rooms", "room", "close-room",
)


class AdminError(Exception):
    """A command failure that is safe to report to the Admin console."""

    def __init__(self, code: str, detail: str | None = None) -> None:
        super().__init__(detail or code)
        self.code = code
        self.detail = detail or code


@dataclass
class AdminContext:
    database: PlatformDatabase
    room_manager: RoomManager
    templates_dir: Path
    games_dir: Path


def _text(payload: dict, key: str, *, required: bool = True) -> str:
    value = payload.get(key)
    if value is None:
        if required:
            raise AdminError("invalid_request", f"缺少参数：{key}")
        return ""
    if not isinstance(value, str):
        raise AdminError("invalid_request", f"参数类型错误：{key}")
    return value.strip()


def _flag(payload: dict, key: str, default: bool = False) -> bool:
    value = payload.get(key, default)
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "y", "on")
    raise AdminError("invalid_request", f"参数类型错误：{key}")


def _user_id(database: PlatformDatabase, username: str) -> int:
    user = database.user_by_username(username)
    if user is None:
        raise AdminError("account_not_found", f"账号不存在：{username}")
    return user.id


async def execute(context: AdminContext, command: str, payload: dict) -> object:
    """Run one Admin command and return JSON-serializable data."""
    if command not in COMMANDS:
        raise AdminError("unknown_command", f"未知命令：{command}")

    database = context.database
    if command == "users":
        return {"users": [_user_row(database, user) for user in database.list_users()]}
    if command == "user":
        username = _text(payload, "username")
        user = database.user_record(username)
        if user is None:
            raise AdminError("account_not_found", f"账号不存在：{username}")
        return _user_row(database, user)
    if command == "create-user":
        try:
            created = database.create_user(
                _text(payload, "username"), _text(payload, "password")
            )
        except ValueError as exc:
            raise AdminError("invalid_account", str(exc)) from exc
        return _user_row(database, database.user_record(created.username))
    if command in ("set-admin", "unset-admin"):
        username = _text(payload, "username")
        try:
            user = database.set_admin(username, command == "set-admin")
        except ValueError as exc:
            raise AdminError("account_not_found", f"账号不存在：{username}") from exc
        return _user_row(database, user)
    if command == "delete-user":
        username = _text(payload, "username")
        user_id = _user_id(database, username)
        counts = database.user_resource_counts(user_id)
        blocking = {key: value for key, value in counts.items() if value}
        if blocking:
            labels = {"templates": "Templates", "games": "Games", "rooms": "Active Room"}
            detail = "\n".join(
                f"- {value} {labels.get(key, key)}" for key, value in blocking.items()
            )
            raise AdminError("user_owns_resources", f"无法删除 {username}：\n{detail}")
        database.delete_user(user_id)
        return {"username": username}

    if command == "templates":
        return {"templates": [_template_row(context, value)
                              for value in database.list_templates()]}
    if command == "template":
        template_id = _text(payload, "id")
        template = database.get_template(template_id)
        if template is None:
            raise AdminError("template_not_found", f"剧本不存在：{template_id}")
        return _template_detail(context, template)
    if command == "rename-template":
        try:
            # Same helper the owner API uses, so payload title and catalog row
            # never diverge.
            template = rename_template(
                database, _text(payload, "id"), 0,
                _text(payload, "name"), context.templates_dir,
                require_owner=False,
            )
        except ValueError as exc:
            raise AdminError("invalid_request", "剧本名称不能为空") from exc
        return _template_detail(context, template)
    if command == "set-template-public":
        template_id = _text(payload, "id")
        try:
            template = database.set_template_public(
                template_id, _flag(payload, "public")
            )
        except ValueError as exc:
            raise AdminError("template_not_found", f"剧本不存在：{template_id}") from exc
        return _template_detail(context, template)
    if command == "delete-template":
        template_id = _text(payload, "id")
        try:
            database.delete_template(template_id)
        except ValueError as exc:
            raise AdminError("template_not_found", f"剧本不存在：{template_id}") from exc
        delete_template_payload(context.templates_dir, template_id)
        return {"id": template_id}
    if command == "import-template":
        owner = _text(payload, "owner")
        owner_id = _user_id(database, owner)
        source = _resolve_source(context, _text(payload, "source"))
        name = _text(payload, "name", required=False) or source.name
        metadata = import_template(
            database, source, context.templates_dir, owner_id, name,
            is_public=_flag(payload, "public"), exclude=("game.db",),
        )
        return _template_detail(context, metadata)

    rooms = context.room_manager
    if command == "rooms":
        return {"rooms": await rooms.list_admin_rooms()}
    if command == "room":
        code = _text(payload, "code")
        detail = await rooms.admin_room(code)
        if detail is None:
            raise AdminError("room_not_found", f"房间不存在：{code}")
        return detail
    if command == "close-room":
        code = _text(payload, "code")
        try:
            await rooms.close_room(code, admin=True)
        except ValueError as exc:
            raise AdminError("room_not_found", f"房间不存在：{code}") from exc
        return {"code": code.upper()}
    raise AdminError("unknown_command", f"未知命令：{command}")


def _resolve_source(context: AdminContext, source: str) -> Path:
    if not source:
        raise AdminError("invalid_request", "请填写源目录")
    candidate = Path(source)
    if candidate.is_absolute():
        if has_payload(candidate):
            return candidate
        raise AdminError(
            "template_not_found", f"目录中没有 {METADATA_FILENAME}：{source}"
        )
    for base in (context.templates_dir, context.games_dir):
        if has_payload(base / source):
            return base / source
    raise AdminError("template_not_found", f"找不到剧本目录：{source}")


def _user_row(database: PlatformDatabase, user) -> dict:
    counts = database.user_resource_counts(user.id)
    return {
        "id": user.id,
        "username": user.username,
        "is_admin": user.is_admin,
        "created_at": user.created_at,
        "templates": counts["templates"],
        "games": counts["games"],
        "rooms": counts["rooms"],
    }


def _owner_name(database: PlatformDatabase, owner_user_id: int) -> str:
    owner = database.user_by_id(owner_user_id)
    return owner.username if owner else str(owner_user_id)


def _template_row(context: AdminContext, template) -> dict:
    payload = load_payload_metadata(Path(context.templates_dir) / template.id)
    return {
        "id": template.id,
        # Same rule as the Web: the payload title is the Script's name, the
        # catalog row is the mirror.
        "name": payload.display_title(template.name) if payload else template.name,
        "owner_user_id": template.owner_user_id,
        "owner_username": _owner_name(context.database, template.owner_user_id),
        "is_public": template.is_public,
        "roles": list(template_role_names(context.templates_dir, template.id)),
    }


def _template_detail(context: AdminContext, template) -> dict:
    detail = _template_row(context, template)
    detail["created_at"] = template.created_at
    detail["updated_at"] = template.updated_at
    # Read-only payload metadata, for the Admin console's template view.
    payload = load_payload_metadata(Path(context.templates_dir) / template.id)
    detail["introduction"] = payload.introduction if payload else ""
    detail["tags"] = list(payload.tags) if payload else []
    return detail


__all__ = [
    "AdminContext", "AdminError", "COMMANDS", "bootstrap_templates", "execute",
]
