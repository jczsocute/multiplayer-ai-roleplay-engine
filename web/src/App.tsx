import { useEffect, useReducer, useRef } from "react";
import { GameScreen } from "./components/GameScreen";
import { JoinScreen } from "./components/JoinScreen";
import { PROTOCOL_VERSION, type ClientMessage } from "./protocol";
import { initialState, reducer, type ClientState } from "./state";
import { openGameSocket, sendMessage } from "./websocket";

const NICKNAME_KEY = "nickname";
const SESSION_KEY = "rp.session";

type StoredSession = { nickname: string; roomKey: string; resumeToken: string };

function loadStoredSession(): StoredSession | null {
  try {
    const raw = sessionStorage.getItem(SESSION_KEY);
    if (!raw) return null;
    const value = JSON.parse(raw) as Partial<StoredSession>;
    if (value.nickname && value.resumeToken) {
      return { nickname: value.nickname, roomKey: value.roomKey ?? "", resumeToken: value.resumeToken };
    }
  } catch {
    // fall through
  }
  return null;
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

function initialFromStorage(): ClientState {
  const nickname = localStorage.getItem(NICKNAME_KEY) ?? "";
  const session = loadStoredSession();
  if (session) {
    return {
      ...initialState,
      connection: "RECONNECTING",
      nickname: session.nickname,
      roomKey: session.roomKey,
      resumeToken: session.resumeToken,
    };
  }
  return { ...initialState, nickname };
}

const BACKOFF_MS = [1000, 2000, 4000, 8000, 10000];

export default function App() {
  const [state, dispatch] = useReducer(reducer, undefined, initialFromStorage);
  const socket = useRef<WebSocket | null>(null);
  const joined = useRef(false);
  const explicitLeave = useRef(false);
  const serverRejection = useRef("");
  const retryTimer = useRef<number | null>(null);
  const retryAttempt = useRef(0);
  const started = useRef(false);
  const sessionRef = useRef<StoredSession>({
    nickname: state.nickname,
    roomKey: state.roomKey,
    resumeToken: state.resumeToken,
  });

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
      openSocket(
        { type: "resume", name: session.nickname, room_key: session.roomKey, resume_token: session.resumeToken },
        true,
      );
    }, delay);
  };

  const handleClose = (resumeAttempt: boolean) => {
    if (explicitLeave.current) return;
    if (resumeAttempt) {
      if (serverRejection.current) {
        // Server explicitly rejected the resume: session is gone.
        joined.current = false;
        clearStoredSession();
        dispatch({ type: "session", nickname: sessionRef.current.nickname, roomKey: sessionRef.current.roomKey, resumeToken: "" });
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
        if (message.type === "error" && !joined.current) serverRejection.current = message.detail;
        if (message.type === "joined" || message.type === "resumed") {
          if (message.protocol_version !== PROTOCOL_VERSION) {
            dispatch({ type: "connection", status: "ERROR", detail: `协议版本不兼容：服务器 ${message.protocol_version}，客户端 ${PROTOCOL_VERSION}` });
            next.close();
            return;
          }
          joined.current = true;
          retryAttempt.current = 0;
          localStorage.setItem(NICKNAME_KEY, message.name);
          sessionRef.current = { nickname: message.name, roomKey: sessionRef.current.roomKey, resumeToken: message.resume_token };
          saveStoredSession(sessionRef.current);
          dispatch({ type: "session", ...sessionRef.current });
          if (message.type === "joined" && !message.role && !message.view_role && message.roles[0]) {
            sendMessage(next, { type: "view", role: message.roles[0].id });
          }
        }
        dispatch({ type: "server", message });
      },
      onError: () => undefined,
      onClose: () => handleClose(resumeAttempt),
    });
    socket.current = next;
  };

  const connect = (nickname: string, roomKey: string) => {
    socket.current?.close();
    joined.current = false;
    explicitLeave.current = false;
    serverRejection.current = "";
    clearRetry();
    retryAttempt.current = 0;
    clearStoredSession();
    sessionRef.current = { nickname, roomKey, resumeToken: "" };
    dispatch({ type: "session", ...sessionRef.current });
    dispatch({ type: "connection", status: "CONNECTING" });
    openSocket({ type: "join", name: nickname, room_key: roomKey }, false);
  };

  const leave = () => {
    explicitLeave.current = true;
    clearRetry();
    sendMessage(socket.current, { type: "leave" });
    socket.current?.close();
    socket.current = null;
    joined.current = false;
    clearStoredSession();
    dispatch({ type: "reset" });
  };

  useEffect(() => {
    fetch("/ui-config.json")
      .then((response) => response.json())
      .then((config: { font_scale?: number; room_key_required?: boolean }) => {
        if (typeof config.font_scale === "number") {
          document.documentElement.style.setProperty("--font-scale", String(config.font_scale));
        }
        dispatch({ type: "ui_config", roomKeyRequired: config.room_key_required !== false });
      })
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    if (started.current) return;
    started.current = true;
    const session = loadStoredSession();
    if (session) {
      sessionRef.current = session;
      dispatch({ type: "session", ...session });
      openSocket({ type: "resume", name: session.nickname, room_key: session.roomKey, resume_token: session.resumeToken }, true);
    }
    return () => {
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

  const inGame = state.connection === "CONNECTED" || state.connection === "RECONNECTING"
    || (joined.current && state.connection === "CONNECTING");
  if (!inGame) {
    return <JoinScreen initialName={state.nickname} initialRoomKey={state.roomKey}
      roomKeyRequired={state.roomKeyRequired} connection={state.connection}
      errors={state.errors} onJoin={connect} />;
  }
  return <GameScreen state={state} send={send} setAction={(text) => dispatch({ type: "action_draft", text })}
    setChat={(text) => dispatch({ type: "chat_draft", text })} sendChat={sendChat} leave={leave}
    setTab={(tab) => dispatch({ type: "mobile_tab", tab })} />;
}
