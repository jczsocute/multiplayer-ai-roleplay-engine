import { useEffect, useReducer, useRef } from "react";
import { AuthScreen } from "./components/AuthScreen";
import { GameScreen } from "./components/GameScreen";
import { JoinScreen } from "./components/JoinScreen";
import { humanizeError } from "./errors";
import { PROTOCOL_VERSION, type AuthUser, type ClientMessage } from "./protocol";
import { initialState, reducer, type ClientState } from "./state";
import { openGameSocket, sendMessage } from "./websocket";
import { downloadGameHistory } from "./gameHistory";

const SESSION_KEY = "rp.session";
const BACKOFF_MS = [1000, 2000, 4000, 8000, 10000];

type StoredSession = { roomKey: string; resumeToken: string };

function loadStoredSession(): StoredSession | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<StoredSession>;
    return { roomKey: value.roomKey ?? "", resumeToken: value.resumeToken ?? "" };
  } catch {
    return null;
  }
}

function saveStoredSession(session: StoredSession) {
  try {
    sessionStorage.setItem(SESSION_KEY, JSON.stringify(session));
  } catch {
    // storage can be unavailable in private mode; ignore
  }
}

function clearStoredSession() {
  try {
    sessionStorage.removeItem(SESSION_KEY);
  } catch {
    // ignore
  }
}

type PlatformRoom = { code: string; password: string };

export default function GameApp({
  platformRoom,
  onPlatformLeave,
}: {
  platformRoom?: PlatformRoom;
  onPlatformLeave?: () => void;
} = {}) {
  const [state, dispatch] = useReducer(reducer, initialState);
  const socket = useRef<WebSocket | null>(null);
  const joined = useRef(false);
  const explicitLeave = useRef(false);
  const serverRejection = useRef("");
  const authRejected = useRef(false);
  const retryTimer = useRef<number | null>(null);
  const retryAttempt = useRef(0);
  const started = useRef(false);
  const sessionRef = useRef<StoredSession>({ roomKey: "", resumeToken: "" });

  const clearRetry = () => {
    if (retryTimer.current !== null) {
      window.clearTimeout(retryTimer.current);
      retryTimer.current = null;
    }
  };

  const scheduleRetry = () => {
    clearRetry();
    dispatch({ type: "connection", status: "RECONNECTING" });
    const delay = BACKOFF_MS[Math.min(retryAttempt.current, BACKOFF_MS.length - 1)];
    retryAttempt.current += 1;
    retryTimer.current = window.setTimeout(() => {
      const session = sessionRef.current;
      openSocket({ type: "resume", room_key: session.roomKey,
        password: platformRoom?.password, resume_token: session.resumeToken }, true);
    }, delay);
  };

  const handleClose = (resumeAttempt: boolean) => {
    if (explicitLeave.current) return;
    if (authRejected.current) {
      joined.current = false;
      dispatch({ type: "auth_lost", detail: "登录状态已失效，请重新登录。" });
      return;
    }
    if (resumeAttempt) {
      if (serverRejection.current) {
        // Server explicitly rejected the resume: this game connection is gone.
        joined.current = false;
        clearStoredSession();
        sessionRef.current = { ...sessionRef.current, resumeToken: "" };
        dispatch({ type: "session", roomKey: sessionRef.current.roomKey, resumeToken: "" });
        dispatch({ type: "connection", status: "SESSION_EXPIRED", detail: "原会话已失效。" });
        return;
      }
      scheduleRetry();
      return;
    }
    if (joined.current) {
      scheduleRetry();
      return;
    }
    const detail = serverRejection.current || "无法连接服务器";
    dispatch({ type: "connection", status: "ERROR", detail });
  };

  const openSocket = (firstMessage: ClientMessage, resumeAttempt: boolean) => {
    serverRejection.current = "";
    const next = openGameSocket({
      onOpen: () => sendMessage(next, firstMessage),
      onMessage: (message) => {
        if (message.type === "error") {
          if (message.code === "unauthorized") {
            authRejected.current = true;
            dispatch({ type: "auth_lost", detail: humanizeError(message.code, message.detail) });
            return;
          }
          if (!joined.current) {
            serverRejection.current = humanizeError(message.code, message.detail);
          }
        }
        if (message.type === "session_replaced") {
          joined.current = false;
        }
        if (message.type === "kicked") {
          // Host kick is not a ban and not a logout: back to the Lobby.
          explicitLeave.current = true;
          joined.current = false;
          clearRetry();
          clearStoredSession();
          dispatch({ type: "server", message });
          window.setTimeout(() => onPlatformLeave?.(), 0);
          return;
        }
        if (message.type === "room_closed") {
          explicitLeave.current = true;
          joined.current = false;
          clearRetry();
          clearStoredSession();
          dispatch({ type: "server", message });
          window.setTimeout(() => onPlatformLeave?.(), 0);
          return;
        }
        if (message.type === "joined" || message.type === "resumed") {
          if (message.protocol_version !== PROTOCOL_VERSION) {
            dispatch({ type: "connection", status: "ERROR", detail: `协议版本不兼容：服务器 ${message.protocol_version}，客户端 ${PROTOCOL_VERSION}` });
            next.close();
            return;
          }
          joined.current = true;
          retryAttempt.current = 0;
          sessionRef.current = { roomKey: sessionRef.current.roomKey, resumeToken: message.resume_token };
          saveStoredSession(sessionRef.current);
          dispatch({ type: "session", roomKey: sessionRef.current.roomKey, resumeToken: message.resume_token });
          if (message.type === "joined" && !message.role && !message.view_role && message.roles[0]) {
            sendMessage(next, { type: "view", role: message.roles[0].id });
          }
        }
        dispatch({ type: "server", message });
      },
      onError: () => undefined,
      onClose: () => handleClose(resumeAttempt),
    }, platformRoom?.code);
    socket.current = next;
  };

  const connect = (roomKey: string) => {
    socket.current?.close();
    joined.current = false;
    explicitLeave.current = false;
    authRejected.current = false;
    serverRejection.current = "";
    clearRetry();
    retryAttempt.current = 0;
    sessionRef.current = { roomKey, resumeToken: "" };
    saveStoredSession(sessionRef.current);
    dispatch({ type: "session", roomKey, resumeToken: "" });
    dispatch({ type: "connection", status: "CONNECTING" });
    openSocket({ type: "join", room_key: roomKey, password: platformRoom?.password }, false);
  };

  const authenticate = async (
    mode: "login" | "register",
    username: string,
    password: string,
  ): Promise<string | null> => {
    try {
      const response = await fetch(`/api/${mode}`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const body = (await response.json().catch(() => ({}))) as Partial<AuthUser> & { detail?: string; error?: string };
      if (!response.ok) return humanizeError(body.error, body.detail);
      dispatch({ type: "auth", user: { id: Number(body.id), username: String(body.username) } });
      dispatch({ type: "connection", status: "DISCONNECTED" });
      return null;
    } catch {
      return "无法连接服务器";
    }
  };

  const logout = () => {
    explicitLeave.current = true;
    clearRetry();
    if (!platformRoom) sendMessage(socket.current, { type: "leave" });
    socket.current?.close();
    socket.current = null;
    joined.current = false;
    clearStoredSession();
    sessionRef.current = { roomKey: "", resumeToken: "" };
    if (platformRoom) {
      void fetch(`/api/rooms/${platformRoom.code}/leave`, { method: "POST" })
        .finally(() => onPlatformLeave?.());
    } else {
      void fetch("/api/logout", { method: "POST" }).catch(() => undefined);
    }
    dispatch({ type: "reset" });
  };

  useEffect(() => {
    fetch("/ui-config.json")
      .then((response) => response.json())
      .then((config: { font_scale?: number; room_key_required?: boolean; allow_registration?: boolean; room_disconnect_timeout_seconds?: number }) => {
        if (typeof config.font_scale === "number") {
          document.documentElement.style.setProperty("--font-scale", String(config.font_scale));
        }
        dispatch({
          type: "ui_config",
          roomKeyRequired: config.room_key_required !== false,
          allowRegistration: config.allow_registration !== false,
          roomDisconnectTimeoutSeconds: typeof config.room_disconnect_timeout_seconds === "number"
            && config.room_disconnect_timeout_seconds > 0
            ? config.room_disconnect_timeout_seconds : undefined,
        });
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    let active = true;
    fetch("/api/me")
      .then(async (response) => {
        if (!active) return;
        if (!response.ok) {
          dispatch({ type: "auth_checked" });
          return;
        }
        const user = (await response.json()) as AuthUser;
        dispatch({ type: "auth", user });
        const session = loadStoredSession();
        if (session?.resumeToken && (!platformRoom || session.roomKey === platformRoom.code)) {
          sessionRef.current = session;
          dispatch({ type: "session", roomKey: session.roomKey, resumeToken: session.resumeToken });
          openSocket({ type: "resume", room_key: session.roomKey,
            password: platformRoom?.password, resume_token: session.resumeToken }, true);
        } else if (platformRoom) {
          connect(platformRoom.code);
        }
      })
      .catch(() => {
        if (active) dispatch({ type: "auth_checked" });
      });
    return () => {
      active = false;
      clearRetry();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (!state.role || state.connection !== "CONNECTED" || state.players[state.role]?.status !== "EDITING") return;
    const timer = window.setTimeout(() => sendMessage(socket.current, { type: "action", text: state.actionDraft }), 250);
    return () => window.clearTimeout(timer);
  }, [state.actionDraft, state.connection, state.role, state.players]);

  const send = (message: ClientMessage) => {
    if (!sendMessage(socket.current, message)) {
      dispatch({ type: "connection", status: "RECONNECTING" });
    }
  };
  const sendChat = () => {
    if (state.connection !== "CONNECTED") return;
    const text = state.chatDraft.trim();
    if (!text) return;
    send({ type: "room_chat", text });
    dispatch({ type: "chat_draft", text: "" });
  };
  const closePlatformRoom = platformRoom ? async () => {
    const response = await fetch(`/api/rooms/${platformRoom.code}`, { method: "DELETE" });
    if (!response.ok) {
      const body = await response.json().catch(() => ({})) as { error?: string; detail?: string };
      dispatch({
        type: "connection",
        status: state.connection,
        detail: humanizeError(body.error, body.detail),
      });
    }
  } : undefined;

  if (!state.authChecked) {
    return <main className="join-shell">
      <div className="join-card"><h1>AI RP Engine</h1><p className="muted">正在检查登录状态…</p></div>
    </main>;
  }
  if (!state.authUser) {
    return <AuthScreen allowRegistration={state.allowRegistration} onSubmit={authenticate} />;
  }
  const inGame = state.connection === "CONNECTED" || state.connection === "RECONNECTING"
    || (joined.current && state.connection === "CONNECTING");
  if (!inGame) {
    if (platformRoom) {
      return <main className="join-shell"><div className="join-card">
        <h1>房间 {platformRoom.code}</h1>
        <p className="muted">{state.connection === "CONNECTING" ? "正在加入…" : "无法加入房间"}</p>
        {state.errors.length > 0 && <p className="error-text">{state.errors.at(-1)}</p>}
        <button className="secondary" onClick={logout}>返回大厅</button>
      </div></main>;
    }
    return <JoinScreen
      username={state.authUser.username}
      initialRoomKey={state.roomKey}
      roomKeyRequired={state.roomKeyRequired}
      connecting={state.connection === "CONNECTING"}
      notice={state.connection === "SESSION_EXPIRED" ? "原会话已失效，请重新加入游戏。" : ""}
      errors={state.errors}
      onJoin={connect}
      onLogout={logout} />;
  }
  return <GameScreen state={state} send={send} setAction={(text) => dispatch({ type: "action_draft", text })}
    setChat={(text) => dispatch({ type: "chat_draft", text })} sendChat={sendChat} leave={logout}
    closeRoom={state.isHost ? () => void closePlatformRoom?.() : undefined}
    exportHistory={platformRoom && state.isHost
      ? () => downloadGameHistory("room", platformRoom.code) : undefined}
    setTab={(tab) => dispatch({ type: "mobile_tab", tab })} />;
}
