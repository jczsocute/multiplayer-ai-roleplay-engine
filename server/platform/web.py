"""HTTP and WebSocket shell for Platform accounts, resources and Rooms."""

import asyncio
import json
import logging
from dataclasses import asdict
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketState
from websockets.exceptions import ConnectionClosed

from server.gameserver.protocol import MAX_WEBSOCKET_MESSAGE_BYTES
from server.platform.admin import COMMANDS, AdminContext, AdminError
from server.platform.admin import execute as admin_execute
from server.platform.auth import AUTH_COOKIE_NAME
from server.platform.catalog import (
    copy_game, copy_template, create_template_from_scaffold, delete_game,
    delete_owned_template, describe_template, rename_template,
    set_template_public_owned,
)
from server.platform.database import PlatformDatabase
from server.platform.models import AuthenticatedUser
from server.platform.room_manager import RoomManager
from server.platform.websocket_adapter import DISCONNECTS, WebSocketConnection
from server.platform.security import is_loopback_host, origin_allowed

logger = logging.getLogger(__name__)


def create_platform_app(
    database: PlatformDatabase,
    static_dir: Path | None = None,
    ui_font_scale: float = 0.7,
    allow_registration: bool = True,
    auth_session_days: int = 30,
    secure_cookie: bool = False,
    room_manager: RoomManager | None = None,
    allowed_origins: tuple[str, ...] = (),
    min_role_count: int = 2,
    max_role_count: int = 4,
) -> Starlette:
    dist = static_dir or Path(__file__).resolve().parents[2] / "web" / "dist"
    room_manager = room_manager or RoomManager(
        database, min_role_count=min_role_count, max_role_count=max_role_count
    )

    async def resolve_user(request: Request) -> AuthenticatedUser | None:
        token = request.cookies.get(AUTH_COOKIE_NAME)
        return await asyncio.to_thread(database.resolve_session, token)

    async def credentials(request: Request) -> tuple[str | None, str | None]:
        try:
            body = await request.json()
        except ValueError:
            return None, None
        if not isinstance(body, dict):
            return None, None
        username, password = body.get("username"), body.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            return None, None
        return username, password

    def set_cookie(response: JSONResponse, token: str) -> None:
        response.set_cookie(
            AUTH_COOKIE_NAME, token, max_age=auth_session_days * 24 * 3600,
            path="/", httponly=True, samesite="lax", secure=secure_cookie,
        )

    def clear_cookie(response: JSONResponse) -> None:
        response.delete_cookie(
            AUTH_COOKIE_NAME, path="/", httponly=True, samesite="lax",
            secure=secure_cookie,
        )

    async def register(request: Request) -> JSONResponse:
        if not allow_registration:
            return JSONResponse(
                {"error": "registration_disabled", "detail": "当前未开放注册"},
                status_code=403,
            )
        username, password = await credentials(request)
        if username is None or password is None:
            return JSONResponse(
                {"error": "invalid_request", "detail": "需要 username 和 password"},
                status_code=400,
            )
        try:
            user = await asyncio.to_thread(database.create_user, username, password)
        except ValueError as exc:
            return JSONResponse(
                {"error": "invalid_account", "detail": str(exc)}, status_code=400
            )
        token = await asyncio.to_thread(
            database.create_session, user.id, auth_session_days
        )
        response = JSONResponse(asdict(user), status_code=201)
        set_cookie(response, token)
        return response

    async def login(request: Request) -> JSONResponse:
        username, password = await credentials(request)
        if username is None or password is None:
            return JSONResponse(
                {"error": "invalid_request", "detail": "需要 username 和 password"},
                status_code=400,
            )
        user = await asyncio.to_thread(database.authenticate, username, password)
        if user is None:
            return JSONResponse(
                {"error": "invalid_credentials", "detail": "用户名或密码错误"},
                status_code=401,
            )
        token = await asyncio.to_thread(
            database.create_session, user.id, auth_session_days
        )
        response = JSONResponse(asdict(user))
        set_cookie(response, token)
        return response

    async def logout(request: Request) -> JSONResponse:
        await asyncio.to_thread(
            database.delete_session, request.cookies.get(AUTH_COOKIE_NAME)
        )
        response = JSONResponse({"ok": True})
        clear_cookie(response)
        return response

    async def me(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        return JSONResponse(asdict(user))

    def template_rows(user_id: int, values) -> list[dict]:
        return [
            describe_template(
                Path(room_manager.templates_dir),
                value,
                owner.username if (owner := database.user_by_id(value.owner_user_id))
                else str(value.owner_user_id),
            )
            for value in values
        ]

    async def templates(request: Request) -> JSONResponse:
        """Scripts the caller may *use*: their own plus every public one.

        This is the Create Room selector source, so a private Script of your own
        is included here on purpose. The Lobby plaza uses ``/public`` instead.
        """
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        values = await asyncio.to_thread(database.list_available_templates, user.id)
        return JSONResponse({"templates": template_rows(user.id, values)})

    async def public_templates(request: Request) -> JSONResponse:
        """剧本广场: strictly public Scripts, regardless of who owns them."""
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        values = await asyncio.to_thread(database.list_public_templates)
        return JSONResponse({"templates": template_rows(user.id, values)})

    async def template_detail(request: Request) -> JSONResponse:
        """Detail view: owner, public Templates, or nothing (403)."""
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        template_id = request.path_params["template_id"]
        template = await asyncio.to_thread(database.get_template, template_id)
        if template is None:
            return _api_error("template_not_found", 404)
        if template.owner_user_id != user.id and not template.is_public:
            return _api_error("template_not_owned", 403)
        owner = await asyncio.to_thread(database.user_by_id, template.owner_user_id)
        row = await asyncio.to_thread(
            describe_template,
            Path(room_manager.templates_dir), template,
            owner.username if owner else str(template.owner_user_id),
            include_introduction=True,
        )
        return JSONResponse(row)

    async def my_templates(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        values = await asyncio.to_thread(database.list_user_templates, user.id)
        return JSONResponse({"templates": template_rows(user.id, values)})

    async def create_template(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("invalid_request")
            role_count = body.get("role_count")
            if isinstance(role_count, bool) or not isinstance(role_count, int):
                raise ValueError("invalid_request")
            metadata = await asyncio.to_thread(
                create_template_from_scaffold,
                database, Path(room_manager.templates_dir), user.id,
                str(body.get("name", "")), role_count,
                min_role_count, max_role_count,
            )
        except (ValueError, OSError) as exc:
            return _api_error(str(exc), 400)
        rows = template_rows(user.id, [metadata])
        return JSONResponse(rows[0], status_code=201)

    async def patch_template(request: Request) -> JSONResponse:
        """Owner edits: `{"name": ...}` renames, `{"is_public": bool}` toggles."""
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        template_id = request.path_params["template_id"]
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("invalid_request")
            if "name" not in body and "is_public" not in body:
                raise ValueError("invalid_request")
            metadata = None
            if "name" in body:
                metadata = await asyncio.to_thread(
                    rename_template, database, template_id, user.id,
                    str(body["name"]), Path(room_manager.templates_dir),
                )
            if "is_public" in body:
                value = body["is_public"]
                if not isinstance(value, bool):
                    raise ValueError("invalid_request")
                metadata = await asyncio.to_thread(
                    set_template_public_owned, database, template_id, user.id, value,
                )
        except PermissionError as exc:
            return _api_error(str(exc), 403)
        except (ValueError, OSError) as exc:
            return _api_error(str(exc), _not_found(str(exc)) or 400)
        return JSONResponse(template_rows(user.id, [metadata])[0])

    async def copy_own_template(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            metadata = await asyncio.to_thread(
                copy_template, database, request.path_params["template_id"],
                user.id, Path(room_manager.templates_dir),
            )
        except PermissionError as exc:
            return _api_error(str(exc), 403)
        except (ValueError, OSError) as exc:
            return _api_error(str(exc), _not_found(str(exc)) or 400)
        return JSONResponse(template_rows(user.id, [metadata])[0], status_code=201)

    async def delete_own_template(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            await asyncio.to_thread(
                delete_owned_template, database, request.path_params["template_id"],
                user.id, Path(room_manager.templates_dir),
            )
        except PermissionError as exc:
            return _api_error(str(exc), 403)
        except (ValueError, OSError) as exc:
            return _api_error(str(exc), _not_found(str(exc)) or 400)
        return JSONResponse({"ok": True})

    async def games(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        values = await asyncio.to_thread(database.list_user_games, user.id)
        return JSONResponse({"games": [asdict(value) for value in values]})

    async def patch_game(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        game_id = request.path_params["game_id"]
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("invalid_request")
            game = await asyncio.to_thread(database.get_game, game_id)
            if game is None:
                raise ValueError("game_not_found")
            if game.owner_user_id != user.id:
                raise PermissionError("forbidden")
            metadata = await asyncio.to_thread(
                database.rename_game, game_id, str(body.get("name", ""))
            )
        except PermissionError as exc:
            return _api_error(str(exc), 403)
        except (ValueError, OSError) as exc:
            return _api_error(str(exc), _not_found(str(exc)) or 400)
        return JSONResponse(asdict(metadata))

    async def copy_own_game(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            metadata = await asyncio.to_thread(
                copy_game, database, request.path_params["game_id"], user.id,
                Path(room_manager.games_dir),
            )
        except PermissionError as exc:
            return _api_error(str(exc), 403)
        except (ValueError, OSError) as exc:
            status = 409 if str(exc) == "game_is_active" else (_not_found(str(exc)) or 400)
            return _api_error(str(exc), status)
        return JSONResponse(asdict(metadata), status_code=201)

    async def delete_own_game(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            await asyncio.to_thread(
                delete_game, database, request.path_params["game_id"], user.id,
                Path(room_manager.games_dir),
            )
        except PermissionError as exc:
            return _api_error(str(exc), 403)
        except (ValueError, OSError) as exc:
            status = 409 if str(exc) == "game_is_active" else (_not_found(str(exc)) or 400)
            return _api_error(str(exc), status)
        return JSONResponse({"ok": True})

    async def rooms(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        return JSONResponse({"rooms": await room_manager.list_public_rooms()})

    async def room_detail(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        value = await room_manager.public_room(request.path_params["code"])
        if value is None:
            return JSONResponse({"error": "room_not_found"}, status_code=404)
        return JSONResponse(value)

    async def current_room(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        code = room_manager.user_current_room(user.id)
        failed = next(
            (room.code for room in database.list_rooms()
             if room.owner_user_id == user.id and room_manager.get_runtime(room.code) is None),
            None,
        )
        return JSONResponse({
            "room": await room_manager.public_room(code) if code else None,
            "recovery_failed_room": failed,
        })

    async def create_room(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            body = await request.json()
            if not isinstance(body, dict):
                raise ValueError("invalid_request")
            source = body.get("source")
            password = body.get("password", "")
            if not isinstance(password, str):
                raise ValueError("invalid_room_password_format")
            if source == "game":
                runtime = await room_manager.create_room_from_game(
                    user, str(body.get("game_id", "")), password
                )
            elif source == "template":
                runtime = await room_manager.create_room_from_template(
                    user, str(body.get("template_id", "")),
                    str(body.get("game_name", "")), password,
                )
            else:
                raise ValueError("invalid_source")
        except PermissionError as exc:
            return _api_error(str(exc), 403)
        except (ValueError, OSError) as exc:
            return _api_error(str(exc), 400)
        return JSONResponse({"code": runtime.code, "game_id": runtime.game_id}, status_code=201)

    async def close_room(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        try:
            await room_manager.close_room(request.path_params["code"], user.id)
        except PermissionError as exc:
            return _api_error(str(exc), 403)
        except ValueError as exc:
            return _api_error(str(exc), 404)
        return JSONResponse({"ok": True})

    async def leave_room(request: Request) -> JSONResponse:
        user = await resolve_user(request)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        await room_manager.leave_member(user.id, request.path_params["code"])
        return JSONResponse({"ok": True})

    async def game_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        if not origin_allowed(
            websocket.headers.get("origin"), websocket.headers.get("host"), allowed_origins
        ):
            await _ws_error(websocket, "forbidden", "来源不被允许")
            return
        token = websocket.cookies.get(AUTH_COOKIE_NAME)
        user = await asyncio.to_thread(database.resolve_session, token)
        if user is None:
            await _ws_error(websocket, "unauthorized", "请先登录")
            return
        code = websocket.query_params.get("room", "").upper()
        runtime = room_manager.get_runtime(code)
        if runtime is None:
            await _ws_error(websocket, "room_not_found", "房间不存在")
            return
        marker = object()
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=15)
            message = json.loads(raw)
            if not isinstance(message, dict) or message.get("type") not in ("join", "resume"):
                # A coded error with Chinese text: this reaches a browser client.
                await _ws_error(
                    websocket, "invalid_request", "第一条消息必须是 join 或 resume"
                )
                return
            password = str(message.pop("password", ""))
            if room_manager.user_current_room(user.id) != code:
                await asyncio.to_thread(room_manager.verify_password, code, password)
            connection = WebSocketConnection(websocket, json.dumps(message))
            marker = connection
            await room_manager.enter(user.id, code, marker)
            await runtime.game_server.public_handler(connection, user)
        except ValueError as exc:
            error = str(exc)
            details = {
                "room_password_required": "请输入房间密码",
                "invalid_room_password": "房间密码错误",
                "already_in_room": "请先离开当前房间",
                "room_full": "房间人数已满",
            }
            # Unknown conditions keep their code but never leak an English
            # sentence into the browser UI.
            await _ws_error(websocket, error, details.get(error, "无法加入房间，请稍后重试"))
        except (ConnectionClosed, *DISCONNECTS, asyncio.TimeoutError,
                json.JSONDecodeError):
            pass
        finally:
            # A member inside the disconnect grace period keeps its seat.
            await room_manager.release_if_gone(user.id, code, marker)

    async def ui_config(_request: Request) -> JSONResponse:
        return JSONResponse({
            "font_scale": ui_font_scale,
            "allow_registration": allow_registration,
            "platform_mode": True,
            "min_role_count": min_role_count,
            "max_role_count": max_role_count,
            "max_room_users": room_manager.max_users,
        })

    async def admin_websocket(websocket: WebSocket) -> None:
        """Loopback-only Platform Admin console endpoint.

        Three conditions must all hold: the socket peer is local, the credentials
        are valid, and the account has ``is_admin`` in platform.db. The flag is
        never taken from a client message.
        """
        await websocket.accept()
        peer = websocket.client.host if websocket.client else None
        if not is_loopback_host(peer):
            await _ws_error(websocket, "forbidden", "Admin 控制台仅允许本机访问")
            return
        try:
            raw = await asyncio.wait_for(websocket.receive_text(), timeout=15)
            message = json.loads(raw)
        except (*DISCONNECTS, asyncio.TimeoutError, json.JSONDecodeError):
            return
        if not isinstance(message, dict) or message.get("type") != "login":
            await _ws_error(websocket, "invalid_request", "第一条消息必须是 login")
            return
        username, password = message.get("username"), message.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            await _ws_error(websocket, "invalid_request", "需要 username 和 password")
            return
        user = await asyncio.to_thread(database.authenticate, username, password)
        if user is None:
            await _ws_error(websocket, "unauthorized", "用户名或密码错误")
            return
        if not await asyncio.to_thread(database.is_admin, user.id):
            await _ws_error(websocket, "forbidden", "该账号不是平台管理员")
            return
        context = AdminContext(
            database,
            room_manager,
            Path(room_manager.templates_dir),
            Path(room_manager.games_dir),
        )
        await websocket.send_text(json.dumps({
            "type": "session",
            "user": {"id": user.id, "username": user.username, "is_admin": True},
            "commands": list(COMMANDS),
        }, ensure_ascii=False))
        try:
            while True:
                raw_command = await websocket.receive_text()
                await _run_admin_command(websocket, context, raw_command)
        except DISCONNECTS:
            pass

    routes = [
        Route("/api/register", register, methods=["POST"]),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]),
        Route("/api/me", me, methods=["GET"]),
        Route("/api/templates/mine", my_templates, methods=["GET"]),
        Route("/api/templates/public", public_templates, methods=["GET"]),
        Route("/api/templates/{template_id}", template_detail, methods=["GET"]),
        Route("/api/templates", templates, methods=["GET"]),
        Route("/api/templates", create_template, methods=["POST"]),
        Route("/api/templates/{template_id}", patch_template, methods=["PATCH"]),
        Route("/api/templates/{template_id}", delete_own_template, methods=["DELETE"]),
        Route("/api/templates/{template_id}/copy", copy_own_template, methods=["POST"]),
        Route("/api/games", games, methods=["GET"]),
        Route("/api/games/{game_id}", patch_game, methods=["PATCH"]),
        Route("/api/games/{game_id}", delete_own_game, methods=["DELETE"]),
        Route("/api/games/{game_id}/copy", copy_own_game, methods=["POST"]),
        Route("/api/rooms", rooms, methods=["GET"]),
        Route("/api/rooms", create_room, methods=["POST"]),
        Route("/api/rooms/current", current_room, methods=["GET"]),
        Route("/api/rooms/{code}", room_detail, methods=["GET"]),
        Route("/api/rooms/{code}", close_room, methods=["DELETE"]),
        Route("/api/rooms/{code}/leave", leave_room, methods=["POST"]),
        WebSocketRoute("/ws", game_websocket),
        WebSocketRoute("/admin/ws", admin_websocket),
        Route("/ui-config.json", ui_config, methods=["GET"]),
    ]
    if dist.is_dir():
        routes.append(Mount("/", StaticFiles(directory=dist, html=True), name="web"))
    else:
        logger.warning(
            "Web client build not found at %s; run `cd web && npm run build`", dist
        )
        routes.append(Mount("/", StaticFiles(directory=dist, html=True, check_dir=False)))
    app = Starlette(routes=routes)

    async def static_cache_policy(request, call_next):
        response = await call_next(request)
        if request.url.path == "/" or request.url.path.endswith(".html"):
            response.headers["Cache-Control"] = "no-cache"
        elif request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=static_cache_policy)
    return app


def _not_found(code: str) -> int | None:
    """Missing resources answer 404 rather than a generic 400."""
    return 404 if code in ("game_not_found", "template_not_found") else None


def _api_error(code: str, status: int) -> JSONResponse:
    details = {
        "invalid_request": "请求内容无效",
        "invalid_source": "请选择存档或剧本",
        "invalid_room_password_format": "房间密码只能包含 1–32 位字母、数字或下划线",
        "game_not_found": "存档不存在",
        "template_not_found": "剧本不存在",
        "owner_already_has_room": "你已经主持了一个活跃房间",
        "game_already_active": "该存档已经在另一个房间中运行",
        "room_not_found": "房间不存在",
        "forbidden": "没有权限执行此操作",
        "template_not_owned": "你没有权限修改该剧本",
        "game_is_active": "该存档正在房间中使用",
        "room_full": "房间人数已满",
        "cannot_kick_owner": "房主不能将自己踢出房间",
        "user_not_in_room": "该用户已不在房间中",
        "invalid_game_name": "存档名称无效",
        "invalid_template_name": "剧本名称无效",
    }
    return JSONResponse(
        {"error": code, "detail": details.get(code, code)}, status_code=status
    )


async def _run_admin_command(
    websocket: WebSocket, context: AdminContext, raw_command: str
) -> None:
    try:
        message = json.loads(raw_command)
        if not isinstance(message, dict):
            raise AdminError("invalid_request", "命令必须是 JSON 对象")
        command = message.get("type")
        if not isinstance(command, str):
            raise AdminError("invalid_request", "命令缺少 type")
        data = await admin_execute(context, command, message)
    except AdminError as exc:
        await websocket.send_text(json.dumps(
            {"type": "error", "code": exc.code, "detail": exc.detail},
            ensure_ascii=False,
        ))
        return
    except Exception:  # noqa: BLE001 - report, never kill the admin console
        logger.exception("Admin command failed")
        await websocket.send_text(json.dumps(
            {"type": "error", "code": "internal_error", "detail": "命令执行失败"},
            ensure_ascii=False,
        ))
        return
    await websocket.send_text(json.dumps(
        {"type": "ok", "command": command, "data": data}, ensure_ascii=False,
    ))


async def _ws_error(websocket: WebSocket, code: str, detail: str) -> None:
    """Best-effort error frame: the peer may already be gone."""
    if websocket.application_state == WebSocketState.DISCONNECTED:
        return
    try:
        await websocket.send_text(json.dumps(
            {"type": "error", "code": code, "detail": detail}, ensure_ascii=False
        ))
        await websocket.close(code=1008)
    except DISCONNECTS:
        logger.debug("Peer disconnected before the error could be delivered")


async def serve_platform(app: Starlette, host: str, port: int) -> None:
    logger.info("Platform Web listening on http://%s:%s", host, port)
    await uvicorn.Server(
        uvicorn.Config(
            app, host=host, port=port, log_level="info",
            ws_max_size=MAX_WEBSOCKET_MESSAGE_BYTES,
        )
    ).serve()
