"""One adapter for the Starlette transport, shared by both web shells.

`GameServer` was written against the `websockets` library, where a peer going away
surfaces as `ConnectionClosed`. Starlette instead raises `WebSocketDisconnect` (or
`WebSocketDisconnected`, a `RuntimeError` that is *not* a subclass). Without this
translation a normal browser or tab close escapes the generic
`except ConnectionClosed` handler and is logged by uvicorn as
"Exception in ASGI application".

Keeping a single adapter means the platform `/ws` and the legacy `--game` shell
cannot drift apart, and the translation is tested once.
"""

from starlette.websockets import (
    WebSocket, WebSocketDisconnect, WebSocketDisconnected, WebSocketState,
)
from websockets.exceptions import ConnectionClosed

DISCONNECTS = (WebSocketDisconnect, WebSocketDisconnected)


class WebSocketConnection:
    """Present a Starlette WebSocket as a `websockets`-style connection.

    ``first_message`` optionally replays one already-consumed message (used when
    the room handshake reads the first frame before handing the socket over).
    """

    def __init__(self, websocket: WebSocket, first_message: str | None = None) -> None:
        self.websocket = websocket
        self.first_message = first_message

    async def recv(self) -> str:
        if self.first_message is not None:
            value, self.first_message = self.first_message, None
            return value
        try:
            return await self.websocket.receive_text()
        except DISCONNECTS as exc:
            raise ConnectionClosed(None, None) from exc

    async def send(self, payload: str) -> None:
        try:
            await self.websocket.send_text(payload)
        except DISCONNECTS as exc:
            raise ConnectionClosed(None, None) from exc

    async def close(self, code: int = 1000) -> None:
        if self.websocket.application_state != WebSocketState.DISCONNECTED:
            await self.websocket.close(code=code)

    def __aiter__(self):
        return self

    async def __anext__(self) -> str:
        return await self.recv()
