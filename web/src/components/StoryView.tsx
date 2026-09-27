import { useEffect, useRef, useState } from "react";
import type { Role, StoryEntry } from "../protocol";
import { StatusView } from "./StatusView";

type Props = { entries: StoryEntry[]; viewRole: Role | null; characterName: string; spectator: boolean };

export function StoryView({ entries, viewRole, characterName, spectator }: Props) {
  const scroll = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const [showJump, setShowJump] = useState(false);

  useEffect(() => {
    const element = scroll.current;
    if (element && atBottom.current) element.scrollTop = element.scrollHeight;
  }, [entries.length]);

  const onScroll = () => {
    const element = scroll.current;
    if (!element) return;
    const nearBottom = element.scrollHeight - element.scrollTop - element.clientHeight < 80;
    atBottom.current = nearBottom;
    setShowJump(!nearBottom);
  };

  const jumpToLatest = () => {
    const element = scroll.current;
    if (element) element.scrollTop = element.scrollHeight;
  };

  return <section className="story-view">
    <div className="story-header">
      <h2>Story{viewRole ? ` · ${characterName || viewRole}` : ""}</h2>
      {spectator && viewRole && <span className="spectator-hint">当前为 {characterName || viewRole} 视角</span>}
    </div>
    <div className="story-scroll" ref={scroll} onScroll={onScroll}>
      {!viewRole && <p className="muted">选择 A 或 B 查看角色历史。</p>}
      {viewRole && entries.length === 0 && <p className="muted">尚无已完成回合。</p>}
      {entries.map((entry, index) => {
        if (entry.kind === "statusbar") {
          return <details className="statusbar-card" key={`${entry.round ?? "live"}-statusbar-${index}`}>
            <summary>状态 · Round {entry.round ?? "—"}</summary>
            <StatusView value={entry.content} />
          </details>;
        }
        return <article className={`story-entry story-${entry.kind}`} key={`${entry.round ?? "live"}-${entry.kind}-${index}`}>
          {entry.kind === "action" && <div className="story-kind">行动{entry.round ? ` · Round ${entry.round}` : ""}</div>}
          <p>{String(entry.content ?? "")}</p>
        </article>;
      })}
      {showJump && <button className="jump-latest" onClick={jumpToLatest}>↓ 查看最新</button>}
    </div>
  </section>;
}
