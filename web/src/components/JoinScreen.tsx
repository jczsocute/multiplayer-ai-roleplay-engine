import { useState, type FormEvent } from "react";
import { MAX_NICKNAME_LENGTH } from "../protocol";
import type { ConnectionStatus } from "../state";

type Props = {
  initialName: string;
  initialRoomKey: string;
  roomKeyRequired: boolean;
  connection: ConnectionStatus;
  errors: string[];
  onJoin: (name: string, roomKey: string) => void;
};

export function JoinScreen({ initialName, initialRoomKey, roomKeyRequired, connection, errors, onJoin }: Props) {
  const [name, setName] = useState(initialName);
  const [roomKey, setRoomKey] = useState(initialRoomKey);
  const [showKey, setShowKey] = useState(false);
  const busy = connection === "CONNECTING";
  const expired = connection === "SESSION_EXPIRED";
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (!name.trim()) return;
    if (roomKeyRequired && !roomKey.trim()) return;
    onJoin(name.trim(), roomKeyRequired ? roomKey.trim() : "");
  };
  return <main className="join-shell">
    <form className="join-card" onSubmit={submit}>
      <h1>AI RP Engine</h1>
      {expired && <p className="session-expired">原会话已失效。请重新加入房间。</p>}
      <label htmlFor="nickname">昵称</label>
      <input id="nickname" value={name} maxLength={MAX_NICKNAME_LENGTH}
        onChange={(event) => setName(event.target.value)} autoComplete="nickname" autoFocus />
      {roomKeyRequired && <>
        <label htmlFor="room-key">Room Key</label>
        <div className="room-key-row">
          <input id="room-key" value={roomKey} type={showKey ? "text" : "password"}
            onChange={(event) => setRoomKey(event.target.value)} autoComplete="off"
            placeholder="例如 K7M4-PQ9D" />
          <button type="button" className="secondary key-toggle" onClick={() => setShowKey((value) => !value)}
            tabIndex={-1}>{showKey ? "隐藏" : "显示"}</button>
        </div>
      </>}
      <button type="submit" disabled={busy || !name.trim() || (roomKeyRequired && !roomKey.trim())}>
        {busy ? "连接中…" : "加入房间"}
      </button>
      {errors.length > 0 && <p className="error-text">{errors.at(-1)}</p>}
    </form>
  </main>;
}
