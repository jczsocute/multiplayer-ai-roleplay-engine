import logging
from pathlib import Path

import uvicorn
from starlette.applications import Starlette
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route, WebSocketRoute
from starlette.staticfiles import StaticFiles
from starlette.websockets import WebSocket, WebSocketDisconnect, WebSocketState

from server.main import GameServer

logger = logging.getLogger(__name__)


class ASGIWebSocketConnection:
    """Adapt a Starlette WebSocket to the connection shape used by GameServer."""

    def __init__(self, websocket: WebSocket) -> None:
        self.websocket = websocket

    async def recv(self) -> str:
        return await self.websocket.receive_text()

    async def send(self, payload: str) -> None:
        await self.websocket.send_text(payload)

    async def close(self, code: int = 1000) -> None:
        if self.websocket.application_state != WebSocketState.DISCONNECTED:
            await self.websocket.close(code=code)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        try:
            return await self.recv()
        except WebSocketDisconnect as exc:
            raise StopAsyncIteration from exc


def create_web_app(
    game: GameServer,
    static_dir: Path | None = None,
    ui_font_scale: float = 0.7,
) -> Starlette:
    dist = static_dir or Path(__file__).resolve().parents[1] / "web" / "dist"

    async def public_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        connection = ASGIWebSocketConnection(websocket)
        try:
            await game.public_handler(connection)
        except WebSocketDisconnect:
            pass

    async def ui_config(request) -> JSONResponse:
        return JSONResponse({
            "font_scale": ui_font_scale,
            "room_key_required": bool(game.room_key),
        })

    routes = [
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
