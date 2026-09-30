import { useMemo, useState } from "react";
import {
  copyConfirmation, deleteConfirmation, formatLocalTime, renamePrompt, sortedByUpdated,
  toggledRow,
} from "../lobby";
import type { GameItem } from "../types";
import { uiConfirm, uiPrompt } from "../i18n";

type Props = {
  games: GameItem[];
  busy: boolean;
  error: string;
  onLoad: (game: GameItem) => void;
  onRename: (game: GameItem, name: string) => void;
  onCopy: (game: GameItem) => void;
  onExportHistory: (game: GameItem) => void;
  onDelete: (game: GameItem) => void;
};

/** 我的存档: compact rows, newest update first, expanding into an action row. */
export function MyGamesPanel({ games, busy, error, onLoad, onRename, onCopy, onDelete, onExportHistory }: Props) {
  const [openId, setOpenId] = useState<string | null>(null);
  const sorted = useMemo(() => sortedByUpdated(games), [games]);

  const rename = (game: GameItem) => {
    const name = uiPrompt(renamePrompt("存档", game.name), game.name);
    if (name !== null && name.trim() && name.trim() !== game.name) {
      onRename(game, name.trim());
    }
  };

  return <div className="resource-list">
    {busy && <p className="muted">处理中…</p>}
    {error && <p className="error-text">{error}</p>}
    {!sorted.length && <p className="muted">还没有存档。用「从剧本开始」创建第一份吧。</p>}

    {sorted.map((game) => <article className="row-card" key={game.id}>
      <button className="row-main row-toggle" aria-expanded={openId === game.id}
        onClick={() => setOpenId(toggledRow(openId, game.id))}>
        <strong>{game.name}</strong>
        <span className="muted">{formatLocalTime(game.updated_at)}</span>
      </button>
      {openId === game.id && <div className="row-actions">
        <button onClick={() => onLoad(game)}>读取存档</button>
        <button className="secondary" onClick={() => rename(game)}>重命名</button>
        <button className="secondary" onClick={() => {
          if (uiConfirm(copyConfirmation("存档", game.name))) onCopy(game);
        }}>复制</button>
        <button className="secondary" onClick={() => onExportHistory(game)}>导出世界信息</button>
        <button className="danger" onClick={() => {
          if (uiConfirm(deleteConfirmation("存档", game.name, "存档内容会被删除。"))) {
            onDelete(game);
          }
        }}>删除</button>
      </div>}
    </article>)}
  </div>;
}
