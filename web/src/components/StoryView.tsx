import { useEffect, useRef, useState } from "react";
import type { Role, RoleDefinition, StoryEntry } from "../protocol";
import { DialogueText } from "./DialogueText";
import { StatusView } from "./StatusView";
import { translateUi, useLanguage } from "../i18n";

type Props = {
  entries: StoryEntry[];
  viewRole: Role | null;
  characterName: string;
  opening: string;
  spectator: boolean;
  roles: RoleDefinition[];
  viewRoles?: RoleDefinition[];
  viewDisabled: boolean;
  onView: (role: Role) => void;
};

export function StoryView({
  entries,
  viewRole,
  characterName,
  opening,
  spectator,
  roles,
  viewRoles = roles,
  viewDisabled,
  onView,
}: Props) {
  const { language } = useLanguage();
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
      {(spectator || viewRoles.length > 1) && <div className="role-view-tabs" aria-label="选择角色视角">
        {viewRoles.map((role) => <button
          type="button"
          key={role.id}
          className={viewRole === role.id ? "active" : ""}
          aria-pressed={viewRole === role.id}
          disabled={viewDisabled}
          onClick={() => onView(role.id)}
        >{role.name}</button>)}
      </div>}
    </div>
    <div className="story-scroll" data-no-i18n ref={scroll} onScroll={onScroll}>
      {!viewRole && <p className="muted">{translateUi("选择一个角色查看历史。", language)}</p>}
      {viewRole && opening && <article className="story-entry story-opening">
        <div className="story-kind">{translateUi("开场", language)}</div>
        <p><DialogueText text={opening} /></p>
      </article>}
      {viewRole && entries.length === 0 && <p className="muted">{translateUi("尚无已完成回合。", language)}</p>}
      {entries.map((entry, index) => {
        if (entry.kind === "character_status") {
          return <details className="statusbar-card" key={`${entry.round ?? "live"}-statusbar-${index}`}>
            <summary>{language === "en" ? `Status · Round ${entry.round ?? "—"}` : `状态 · 第 ${entry.round ?? "—"} 回合`}</summary>
            <StatusView value={entry.content} />
          </details>;
        }
        return <article className={`story-entry story-${entry.kind}`} key={`${entry.round ?? "live"}-${entry.kind}-${index}`}>
          {entry.kind === "action" && <div className="story-kind">{language === "en" ? "Action" : "行动"}{entry.round ? ` · Round ${entry.round}` : ""}</div>}
          <p><DialogueText text={String(entry.content ?? "")} /></p>
        </article>;
      })}
      {showJump && <button className="jump-latest" onClick={jumpToLatest}>{translateUi("↓ 查看最新", language)}</button>}
    </div>
  </section>;
}
