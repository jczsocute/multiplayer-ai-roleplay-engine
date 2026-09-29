import { useEffect, useState } from "react";
import { normalizeRoomCode, roomNeedsPassword } from "../platform";
import { roomIsFull, roomLabel, roomOccupancy } from "../lobby";
import type { RoomItem } from "../types";

type Props = {
  busy: boolean;
  error: string;
  initialCode?: string;
  currentRoomCode?: string;
  onSearch: (code: string) => Promise<RoomItem | null>;
  onJoin: (room: RoomItem, password: string) => void;
  onClose: () => void;
};

export function JoinRoomForm({
  busy, error, initialCode = "", currentRoomCode, onSearch, onJoin, onClose,
}: Props) {
  const [code, setCode] = useState(initialCode);
  const [room, setRoom] = useState<RoomItem | null>(null);
  const [password, setPassword] = useState("");
  const [searched, setSearched] = useState(false);

  const search = async () => {
    const normalized = normalizeRoomCode(code);
    setPassword("");
    const found = await onSearch(normalized);
    setRoom(found);
    setSearched(true);
  };

  const join = () => {
    if (room) onJoin(room, password);
  };

  // Opened from a Room row: look the Room up right away.
  useEffect(() => {
    if (!initialCode) return;
    void (async () => {
      const found = await onSearch(normalizeRoomCode(initialCode));
      setRoom(found);
      setSearched(true);
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [initialCode]);

  return <div className="panel-form">
    <div className="room-search">
      <input value={code} maxLength={8} placeholder="K7Q9MX"
        onChange={(event) => setCode(event.target.value.toUpperCase())} />
      <button className="secondary" disabled={!code.trim() || busy} onClick={() => void search()}>
        搜索
      </button>
    </div>
    {error && <p className="error-text">{error}</p>}

    {searched && !room && <p className="muted">没有找到这个房间码。</p>}

    {room && <article className="row-card">
      <div className="row-main">
        <strong>{roomLabel(room)}</strong>
        <span className="muted">
          {roomOccupancy(room)} 人{room.has_password ? " · 🔒" : ""}
          {" · 房间码 "}{room.code}{" · 房主 "}{room.owner_username}
        </span>
      </div>
      {roomNeedsPassword(room) && currentRoomCode !== room.code && <input type="password" placeholder="房间密码" value={password}
        onChange={(event) => setPassword(event.target.value)} />}
      {roomIsFull(room, currentRoomCode) && <p className="error-text">房间人数已满</p>}
      <div className="panel-actions">
        <button disabled={busy || roomIsFull(room, currentRoomCode)} onClick={join}>加入</button>
        <button className="secondary" onClick={onClose}>取消</button>
      </div>
    </article>}
  </div>;
}
