import asyncio
import json
import logging
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

from server.gameserver.game_server import GameServer
from server.platform.auth import AUTH_COOKIE_NAME
from server.platform.database import PlatformDatabase
from server.platform.models import AuthenticatedUser
from server.platform.security import origin_allowed
from server.platform.websocket_adapter import DISCONNECTS, WebSocketConnection

logger = logging.getLogger(__name__)


def create_web_app(
    game: GameServer,
    accounts: PlatformDatabase,
    static_dir: Path | None = None,
    ui_font_scale: float = 0.7,
    allow_registration: bool = True,
    auth_session_days: int = 30,
    secure_cookie: bool = False,
    allowed_origins: tuple[str, ...] = (),
) -> Starlette:
    dist = static_dir or Path(__file__).resolve().parents[2] / "web" / "dist"

    async def resolve_user(cookies) -> AuthenticatedUser | None:
        token = cookies.get(AUTH_COOKIE_NAME)
        if not token:
            return None
        return await asyncio.to_thread(accounts.resolve_session, token)

    async def read_credentials(request: Request) -> tuple[str | None, str | None]:
        try:
            data = await request.json()
        except ValueError:
            return None, None
        if not isinstance(data, dict):
            return None, None
        username, password = data.get("username"), data.get("password")
        if not isinstance(username, str) or not isinstance(password, str):
            return None, None
        return username, password

    def set_cookie(response: JSONResponse, token: str) -> None:
        response.set_cookie(
            AUTH_COOKIE_NAME,
            token,
            max_age=auth_session_days * 24 * 3600,
            path="/",
            httponly=True,
            samesite="lax",
            secure=secure_cookie,
        )

    def clear_cookie(response: JSONResponse) -> None:
        response.delete_cookie(
            AUTH_COOKIE_NAME,
            path="/",
            httponly=True,
            samesite="lax",
            secure=secure_cookie,
        )

    async def register(request: Request) -> JSONResponse:
        if not allow_registration:
            return JSONResponse(
                {"error": "registration_disabled", "detail": "当前未开放注册"},
                status_code=403,
            )
        username, password = await read_credentials(request)
        if username is None or password is None:
            return JSONResponse(
                {"error": "invalid_request", "detail": "需要 username 和 password"},
                status_code=400,
            )
        try:
            user = await asyncio.to_thread(accounts.create_user, username, password)
        except ValueError as exc:
            return JSONResponse(
                {"error": "invalid_account", "detail": str(exc)}, status_code=400
            )
        token = await asyncio.to_thread(accounts.create_session, user.id, auth_session_days)
        response = JSONResponse({"id": user.id, "username": user.username}, status_code=201)
        set_cookie(response, token)
        return response

    async def login(request: Request) -> JSONResponse:
        username, password = await read_credentials(request)
        if username is None or password is None:
            return JSONResponse(
                {"error": "invalid_request", "detail": "需要 username 和 password"},
                status_code=400,
            )
        user = await asyncio.to_thread(accounts.authenticate, username, password)
        if user is None:
            return JSONResponse(
                {"error": "invalid_credentials", "detail": "用户名或密码错误"},
                status_code=401,
            )
        token = await asyncio.to_thread(accounts.create_session, user.id, auth_session_days)
        response = JSONResponse({"id": user.id, "username": user.username})
        set_cookie(response, token)
        return response

    async def logout(request: Request) -> JSONResponse:
        token = request.cookies.get(AUTH_COOKIE_NAME)
        if token:
            await asyncio.to_thread(accounts.delete_session, token)
        response = JSONResponse({"ok": True})
        clear_cookie(response)
        return response

    async def me(request: Request) -> JSONResponse:
        user = await resolve_user(request.cookies)
        if user is None:
            return JSONResponse({"error": "unauthenticated"}, status_code=401)
        return JSONResponse({"id": user.id, "username": user.username})

    async def public_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        if not origin_allowed(
            websocket.headers.get("origin"),
            websocket.headers.get("host"),
            allowed_origins,
        ):
            await websocket.send_text(json.dumps(
                {"type": "error", "code": "forbidden_origin", "detail": "来源不被允许"},
                ensure_ascii=False,
            ))
            await websocket.close(code=1008)
            return
        user = await resolve_user(websocket.cookies)
        if user is None:
            await websocket.send_text(json.dumps(
                {"type": "error", "code": "unauthorized", "detail": "请先登录"},
                ensure_ascii=False,
            ))
            await websocket.close(code=1008)
            return
        connection = WebSocketConnection(websocket)
        try:
            await game.public_handler(connection, user)
        except (ConnectionClosed, *DISCONNECTS):
            pass

    async def ui_config(request) -> JSONResponse:
        return JSONResponse({
            "font_scale": ui_font_scale,
            "room_key_required": bool(game.room_key),
            "allow_registration": allow_registration,
        })

    routes = [
        Route("/api/register", register, methods=["POST"]),
        Route("/api/login", login, methods=["POST"]),
        Route("/api/logout", logout, methods=["POST"]),
        Route("/api/me", me, methods=["GET"]),
        WebSocketRoute("/ws", public_websocket),
        Route("/ui-config.json", ui_config),
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
            # Always revalidate the entry point so a newly built hashed bundle is used.
            response.headers["Cache-Control"] = "no-cache"
        elif request.url.path.startswith("/assets/"):
            response.headers["Cache-Control"] = "public, max-age=31536000, immutable"
        return response

    app.add_middleware(BaseHTTPMiddleware, dispatch=static_cache_policy)
    return app


async def serve_web(
    app: Starlette, host: str, port: int, max_websocket_size: int
) -> None:
    logger.info("Web client and Public WebSocket listening on http://%s:%s", host, port)
    config = uvicorn.Config(
        app,
        host=host,
        port=port,
        log_level="info",
        ws_max_size=max_websocket_size,
    )
    await uvicorn.Server(config).serve()
