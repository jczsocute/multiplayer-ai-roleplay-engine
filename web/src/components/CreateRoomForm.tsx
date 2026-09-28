import { useState, type FormEvent } from "react";
import { createRoomPayload, validRoomPassword } from "../platform";
import type { GameItem, TemplateItem } from "../types";

export type CreateRoomPreset = {
  source: "game" | "template";
  id: string;
  name?: string;
};

type Props = {
  games: GameItem[];
  templates: TemplateItem[];
  preset?: CreateRoomPreset | null;
  busy: boolean;
  error: string;
  onSubmit: (body: object) => Promise<void>;
  onClose: () => void;
};

/** The existing Create Room form, reused from every entry point. */
export function CreateRoomForm({ games, templates, preset, busy, error, onSubmit, onClose }: Props) {
  const [source, setSource] = useState<"game" | "template">(preset?.source ?? "game");
  const [gameId, setGameId] = useState(preset?.source === "game" ? preset.id : "");
  const [templateId, setTemplateId] = useState(preset?.source === "template" ? preset.id : "");
  const [gameName, setGameName] = useState(preset?.source === "template" ? preset.name ?? "" : "");
  const [password, setPassword] = useState("");

  const chosen = source === "game" ? gameId : templateId;
  const canSubmit = Boolean(chosen) && validRoomPassword(password) && !busy;

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    if (!canSubmit) return;
    await onSubmit(createRoomPayload(
      source, source === "game" ? gameId : templateId, password, gameName,
    ));
  };

  return <form className="panel-form" onSubmit={submit}>
    <label className="radio-row">
      <input type="radio" checked={source === "game"} onChange={() => setSource("game")} /> 我的存档
    </label>
    <select disabled={source !== "game"} value={gameId}
      onChange={(event) => setGameId(event.target.value)}>
      <option value="">选择游戏存档</option>
      {games.map((game) => <option key={game.id} value={game.id}>{game.name}</option>)}
    </select>

    <label className="radio-row">
      <input type="radio" checked={source === "template"} onChange={() => setSource("template")} /> 从剧本开始
    </label>
    <select disabled={source !== "template"} value={templateId}
      onChange={(event) => setTemplateId(event.target.value)}>
      <option value="">选择剧本</option>
      {templates.map((template) => <option key={template.id} value={template.id}>{template.name}</option>)}
    </select>
    {source === "template" && <input placeholder="存档名称（可选）" value={gameName}
      onChange={(event) => setGameName(event.target.value)} />}

    <input placeholder="房间密码（可选，字母/数字/_）" maxLength={32} value={password}
      onChange={(event) => setPassword(event.target.value)} />
    {password && !validRoomPassword(password) && <p className="error-text">密码只能包含字母、数字和下划线</p>}
    {error && <p className="error-text">{error}</p>}

    <div className="panel-actions">
      <button disabled={!canSubmit}>{busy ? "创建中…" : "创建并进入"}</button>
      <button type="button" className="secondary" onClick={onClose}>取消</button>
    </div>
  </form>;
}
