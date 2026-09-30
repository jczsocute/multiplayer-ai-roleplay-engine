import type { ClientState, MobileTab } from "../state";
import type { ClientMessage } from "../protocol";
import { shouldShowHostControls } from "../host";
import { Composer } from "./Composer";
import { HostControls } from "./HostControls";
import { PlayerStates } from "./PlayerStates";
import { RoomChat } from "./RoomChat";
import { RoundView } from "./RoundView";
import { StoryView } from "./StoryView";
import { inviteLink } from "../invite";
import { useState } from "react";
import { useLanguage } from "../i18n";

type Props = {
  state: ClientState;
  send: (message: ClientMessage) => void;
  setAction: (text: string) => void;
  setChat: (text: string) => void;
  sendChat: () => void;
  leave: () => void;
  closeRoom?: () => void;
  exportHistory?: () => Promise<string | null>;
  roomCode?: string;
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

export function GameScreen({ state, send, setAction, setChat, sendChat, leave, closeRoom, exportHistory, roomCode, setTab }: Props) {
  const [copiedInvite, setCopiedInvite] = useState(false);
  const { language } = useLanguage();
  const activeRole = state.viewRole && state.assignedRoles.includes(state.viewRole)
    ? state.viewRole : null;
  const ownStatus = activeRole ? state.players[activeRole]?.status : undefined;
  const disconnected = state.connection !== "CONNECTED";
  const ownCharacter = activeRole ? state.players[activeRole]?.character_name : null;
  const draft = activeRole ? state.actionDrafts[activeRole] ?? "" : "";
  const viewRoles = state.assignedRoles.length
    ? state.roles.filter((role) => state.assignedRoles.includes(role.id)) : state.roles;
  const host = shouldShowHostControls(state.isHost);
  // A host leaves by closing the Room, so 离开房间 is only offered when there is
  // no close-room capability (e.g. a legacy single-game instance).
  const showLeave = !host || !closeRoom;
  return <main className="game-shell">
    <header className="game-header">
      <div className="header-id">
        <h1>{state.scenario || "AI RP Engine"}</h1>
        <p className="header-meta">
          <span>
            {state.authUser?.username ?? "未知账号"}
            {state.isHost && <span className="host-badge">房主</span>}
            {" · "}{activeRole ? (ownCharacter ?? `玩家 ${activeRole}`) : "观众"}
          </span>
          <span className={`conn-dot conn-${state.connection.toLowerCase()}`}>{connectionLabel[state.connection] ?? state.connection}</span>
          <span className="muted">{state.presence.length} 人在线</span>
        </p>
      </div>
      <div className="header-actions">
        {roomCode && <button className="secondary header-button" onClick={async () => {
          await navigator.clipboard.writeText(inviteLink(window.location.origin, roomCode));
          setCopiedInvite(true);
        }}>{copiedInvite ? "已复制邀请链接" : "复制邀请链接"}</button>}
        {host && (
          <HostControls
            connection={state.connection}
            processingStage={state.processingStage}
            round={state.round}
            ownerUserId={state.authUser?.id}
            roles={state.roles}
            users={state.presence.filter((user) => user.user_id !== undefined)}
            closeRoom={closeRoom}
            exportHistory={exportHistory}
            send={send}
          />
        )}
        {showLeave && <button className="secondary header-button" onClick={leave}>离开房间</button>}
      </div>
    </header>
    {state.connection === "RECONNECTING" && <div className="disconnect-banner">
      {language === "en" ? `Reconnecting… If it takes over ${state.roomDisconnectTimeoutSeconds} seconds, the session may have expired.` : `正在重新连接… 若超过 ${state.roomDisconnectTimeoutSeconds} 秒仍未恢复，会话可能已失效。`}
      <button className="secondary" onClick={leave}>返回登录页</button>
    </div>}
    {state.errors.length > 0 && <div className="error-banner">{state.errors.at(-1)}</div>}
    <div className="top-status-row">
      <RoundView round={state.round} stage={state.processingStage} />
      <PlayerStates players={state.players} roles={state.roles} />
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
          opening={state.opening}
          spectator={state.assignedRoles.length === 0} roles={state.roles} viewRoles={viewRoles} viewDisabled={disconnected}
          onView={(role) => send({ type: "view", role })} />
        {activeRole && <Composer draft={draft} status={ownStatus} disabled={disconnected}
          onDraft={setAction} onSubmit={() => { send({ type: "action", text: draft }); send({ type: "submit" }); }}
          onCancel={() => send({ type: "cancel_submit" })}
          onPause={() => { send({ type: "action", text: draft }); send({ type: "pause" }); }}
          onResume={() => send({ type: "resume" })} />}
      </section>
      <section className="panel chat-panel">
        <RoomChat messages={state.roomMessages} draft={state.chatDraft} disabled={disconnected}
          onDraft={setChat} onSend={sendChat} />
      </section>
    </div>
  </main>;
}
