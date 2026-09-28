import type { ClientMessage, ServerMessage } from "./protocol";
import { parseServerMessage } from "./protocol";

export type SocketHandlers = {
  onOpen: () => void;
  onMessage: (message: ServerMessage) => void;
  onError: (detail: string) => void;
  onClose: () => void;
};

export function openGameSocket(handlers: SocketHandlers, roomCode?: string): WebSocket {
  const protocol = location.protocol === "https:" ? "wss:" : "ws:";
  const query = roomCode ? `?room=${encodeURIComponent(roomCode)}` : "";
  const socket = new WebSocket(`${protocol}//${location.host}/ws${query}`);
  socket.addEventListener("open", handlers.onOpen);
  socket.addEventListener("message", (event) => {
    try {
      handlers.onMessage(parseServerMessage(String(event.data)));
    } catch (error) {
      handlers.onError(error instanceof Error ? error.message : "无法解析服务器消息");
    }
  });
  socket.addEventListener("error", () => handlers.onError("无法连接服务器"));
  socket.addEventListener("close", handlers.onClose);
  return socket;
}

export function sendMessage(socket: WebSocket | null, message: ClientMessage): boolean {
  if (!socket || socket.readyState !== WebSocket.OPEN) return false;
  socket.send(JSON.stringify(message));
  return true;
}
