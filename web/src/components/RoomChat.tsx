import { useEffect, useRef, useState } from "react";
import type { RoomMessage } from "../protocol";
import { MAX_ROOM_CHAT_LENGTH } from "../protocol";

function prefix(message: RoomMessage): string {
  if (message.kind === "system") return "系统";
  if (message.kind === "host") return "管理员";
  if (message.kind === "player") return `${message.sender ?? ""}${message.character_name ? ` (${message.character_name})` : ""}`;
  return message.sender ?? "观众";
}
type Props = { messages: RoomMessage[]; draft: string; disabled?: boolean; onDraft: (text: string) => void; onSend: () => void };
export function RoomChat({ messages, draft, disabled, onDraft, onSend }: Props) {
  const scroll = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const [showJump, setShowJump] = useState(false);
  useEffect(() => {
    const element = scroll.current;
    if (element && atBottom.current) element.scrollTop = element.scrollHeight;
  }, [messages.length]);
  const onScroll = () => {
    const element = scroll.current;
    if (!element) return;
    const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
    atBottom.current = nearBottom;
    setShowJump(!nearBottom);
  };
  return <section className="chat-view">
    <h2>房间聊天</h2>
    <div className="chat-scroll" ref={scroll} onScroll={onScroll}>
      {messages.length === 0 && <p className="muted">本次连接尚无消息。</p>}
      {messages.map((message, index) => <p className={`chat-message message-${message.kind}`} key={index}>
        <strong>[{prefix(message)}]</strong> {message.text}
      </p>)}
      {showJump && <button className="jump-latest" onClick={() => { if (scroll.current) scroll.current.scrollTop = scroll.current.scrollHeight; }}>↓ 查看最新</button>}
    </div>
    <div className="chat-compose"><textarea value={draft} maxLength={MAX_ROOM_CHAT_LENGTH} disabled={disabled}
      onChange={(event) => onDraft(event.target.value)} placeholder="发送房间聊天…" />
      <button onClick={onSend} disabled={disabled || !draft.trim()}>发送</button></div>
  </section>;
}
