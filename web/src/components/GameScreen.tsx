import type { ClientState, MobileTab } from "../state";
import type { ClientMessage, Role } from "../protocol";
import { Composer } from "./Composer";
import { PlayerStates } from "./PlayerStates";
import { RoomChat } from "./RoomChat";
import { RoundView } from "./RoundView";
import { StoryView } from "./StoryView";

type Props = {
  state: ClientState;
  send: (message: ClientMessage) => void;
  setAction: (text: string) => void;
  setChat: (text: string) => void;
  sendChat: () => void;
  leave: () => void;
  setTab: (tab: MobileTab) => void;
};

const connectionLabel: Record<string, string> = {
  CONNECTED: "已连接",
  CONNECTING: "连接中",
  RECONNECTING: "正在重新连接",
  DISCONNECTED: "未连接",
  SESSION_EXPIRED: "会话已失效",
  ERROR: "连接错误",
};

export function GameScreen({ state, send, setAction, setChat, sendChat, leave, setTab }: Props) {
  const ownStatus = state.role ? state.players[state.role]?.status : undefined;
  const disconnected = state.connection !== "CONNECTED";
  const ownCharacter = state.role ? state.players[state.role]?.character_name : null;
  const nameA = state.players.A?.character_name || "A";
  const nameB = state.players.B?.character_name || "B";
  const currentView: Role = state.viewRole ?? "A";
  const otherView: Role = currentView === "A" ? "B" : "A";
  const currentName = currentView === "A" ? nameA : nameB;
  const otherName = otherView === "A" ? nameA : nameB;

  return <main className="game-shell">
    <header className="game-header">
      <div className="header-id">
        <h1>{state.scenario || "AI RP Engine"}</h1>
        <p className="header-meta">
          <span>{state.nickname} · {state.role ? (ownCharacter ?? `玩家 ${state.role}`) : "观众"}</span>
          <span className={`conn-dot conn-${state.connection.toLowerCase()}`}>{connectionLabel[state.connection] ?? state.connection}</span>
          <span className="muted">{state.presence.length} 人在线</span>
        </p>
      </div>
      {!state.role && <button className="view-toggle" disabled={disconnected}
        onClick={() => send({ type: "view", role: otherView })}>
        <span className="view-current">当前视角：{currentName}</span>
        <span className="view-hint">点击切换为 <b>{otherName}</b> 视角</span>
      </button>}
    </header>
    {state.connection === "RECONNECTING" && <div className="disconnect-banner">
      正在重新连接… 若超过 60 秒仍未恢复，会话可能已失效。
      <button className="secondary" onClick={leave}>返回登录页</button>
    </div>}
    {state.errors.length > 0 && <div className="error-banner">{state.errors.at(-1)}</div>}
    <div className="top-status-row">
      <RoundView round={state.round} stage={state.processingStage} />
      <PlayerStates players={state.players} />
    </div>
    <nav className="mobile-tabs">
      <button className={state.mobileTab === "story" ? "active" : ""} onClick={() => setTab("story")}>剧情</button>
      <button className={state.mobileTab === "chat" ? "active" : ""} onClick={() => setTab("chat")}>
        聊天{state.chatUnread > 0 ? <span className="unread"> {state.chatUnread}</span> : null}
      </button>
    </nav>
    <div className={`main-grid ${state.mobileTab === "story" ? "tab-story" : "tab-chat"}`}>
      <section className="panel story-panel">
        <StoryView entries={state.storyEntries} viewRole={state.viewRole} characterName={state.characterName}
          spectator={!state.role} />
        {state.role && <Composer draft={state.actionDraft} status={ownStatus} disabled={disconnected}
          onDraft={setAction} onSubmit={() => { send({ type: "action", text: state.actionDraft }); send({ type: "submit" }); }}
          onCancel={() => send({ type: "cancel_submit" })}
          onPause={() => { send({ type: "action", text: state.actionDraft }); send({ type: "pause" }); }}
          onResume={() => send({ type: "resume" })} />}
      </section>
      <section className="panel chat-panel">
        <RoomChat messages={state.roomMessages} draft={state.chatDraft} disabled={disconnected}
          onDraft={setChat} onSend={sendChat} />
      </section>
    </div>
  </main>;
}
