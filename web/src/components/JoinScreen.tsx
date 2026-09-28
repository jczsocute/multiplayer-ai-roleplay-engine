import { useState, type FormEvent } from "react";

type Props = {
  username: string;
  initialRoomKey: string;
  roomKeyRequired: boolean;
  connecting: boolean;
  notice: string;
  errors: string[];
  onJoin: (roomKey: string) => void;
  onLogout: () => void;
};

export function JoinScreen({
  username, initialRoomKey, roomKeyRequired, connecting, notice, errors, onJoin, onLogout,
}: Props) {
  const [roomKey, setRoomKey] = useState(initialRoomKey);
  const [showKey, setShowKey] = useState(false);
  const submit = (event: FormEvent) => {
    event.preventDefault();
    if (roomKeyRequired && !roomKey.trim()) return;
    onJoin(roomKeyRequired ? roomKey.trim() : "");
  };
  return <main className="join-shell">
    <form className="join-card" onSubmit={submit}>
      <h1>AI RP Engine</h1>
      <p className="joined-account">已登录：<strong>{username}</strong></p>
      {notice && <p className="session-expired">{notice}</p>}
      {roomKeyRequired && <>
        <label htmlFor="room-key">房间密钥</label>
        <div className="room-key-row">
          <input id="room-key" value={roomKey} type={showKey ? "text" : "password"}
            onChange={(event) => setRoomKey(event.target.value)} autoComplete="off"
            placeholder="例如 K7M4-PQ9D" autoFocus />
          <button type="button" className="secondary key-toggle" onClick={() => setShowKey((value) => !value)}
            tabIndex={-1}>{showKey ? "隐藏" : "显示"}</button>
        </div>
      </>}
      <button type="submit" disabled={connecting || (roomKeyRequired && !roomKey.trim())}>
        {connecting ? "连接中…" : "加入游戏"}
      </button>
      <button type="button" className="secondary" onClick={onLogout}>退出登录</button>
      {errors.length > 0 && <p className="error-text">{errors.at(-1)}</p>}
    </form>
  </main>;
}
