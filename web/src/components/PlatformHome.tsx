import { useMemo, useState } from "react";
import {
  LOBBY_TITLE, MAX_LOBBY_ROWS, mayManageRoom, pickRandom, roomGameName, roomIsFull,
  roomLabel, roomOccupancy, templateMeta, templateOwnerLabel,
} from "../lobby";
import type { GameItem, RoomItem, TemplateItem } from "../types";
import { CreateRoomForm, type CreateRoomPreset } from "./CreateRoomForm";
import { JoinRoomForm } from "./JoinRoomForm";
import { MyGamesPanel } from "./MyGamesPanel";
import { MyTemplatesPanel } from "./MyTemplatesPanel";
import { OverlayPanel } from "./OverlayPanel";
import { TemplateDetailPanel } from "./TemplateDetailPanel";

type Panel = "none" | "create" | "join" | "templates" | "games";

type Props = {
  userId: number;
  username: string;
  roleCounts: number[];
  templates: TemplateItem[];
  publicTemplates: TemplateItem[];
  myTemplates: TemplateItem[];
  games: GameItem[];
  rooms: RoomItem[];
  error: string;
  notice: string;
  busy: boolean;
  onLogout: () => void;
  onRefreshRooms: () => Promise<void>;
  onCreate: (body: object) => Promise<boolean>;
  onJoin: (room: RoomItem, password: string) => void;
  onCloseRoom: (code: string) => Promise<void>;
  onSearch: (code: string) => Promise<RoomItem | null>;
  onRenameGame: (game: GameItem, name: string) => Promise<void>;
  onCopyGame: (game: GameItem) => Promise<void>;
  onDeleteGame: (game: GameItem) => Promise<void>;
  onCreateTemplate: (name: string, roleCount: number) => Promise<void>;
  onRenameTemplate: (template: TemplateItem, name: string) => Promise<void>;
  onToggleTemplateVisibility: (template: TemplateItem, isPublic: boolean) => Promise<void>;
  onLoadTemplateDetail: (template: TemplateItem) => Promise<TemplateItem | null>;
  onCopyTemplate: (template: TemplateItem) => Promise<void>;
  onDeleteTemplate: (template: TemplateItem) => Promise<void>;
};

/** Lobby = discovery only: entries open resource panels as overlays. */
export function PlatformHome(props: Props) {
  const [panel, setPanel] = useState<Panel>("none");
  const [preset, setPreset] = useState<CreateRoomPreset | null>(null);
  const [detail, setDetail] = useState<TemplateItem | null>(null);
  const [joinCode, setJoinCode] = useState("");
  // Bumping a nonce re-picks the sample; the pool itself comes from props, so a
  // refresh that changes the list also re-picks automatically.
  const [roomPick, setRoomPick] = useState(0);
  const [templatePick, setTemplatePick] = useState(0);

  const rooms = useMemo(
    () => pickRandom(props.rooms, MAX_LOBBY_ROWS),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [props.rooms, roomPick],
  );
  // The plaza only ever samples public Scripts; `props.templates` (own + public)
  // stays the Create Room selector source.
  const plazaScripts = useMemo(
    () => pickRandom(props.publicTemplates, MAX_LOBBY_ROWS),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [props.publicTemplates, templatePick],
  );

  const close = () => {
    setPanel("none");
    setPreset(null);
    setDetail(null);
    setJoinCode("");
  };
  const closeDetail = () => setDetail(null);
  const openCreate = (next: CreateRoomPreset | null = null) => {
    setPreset(next);
    setDetail(null);
    setPanel("create");
  };
  /** Show what the list already knows, then replace it with the detail payload.
   * The overlay sits on top of whichever panel is open, so 我的剧本 keeps its
   * expanded row and 剧本广场 opens the same view. */
  const openDetail = async (template: TemplateItem) => {
    setDetail(template);
    const full = await props.onLoadTemplateDetail(template);
    if (full) setDetail(full);
  };
  /** Room row click: no password joins at once, otherwise ask for it. */
  const openRoom = (room: RoomItem, password: string) => {
    if (room.has_password && !password) {
      setJoinCode(room.code);
      setPanel("join");
      return;
    }
    close();
    props.onJoin(room, password);
  };
  const refreshRooms = async () => {
    await props.onRefreshRooms();
    setRoomPick((value) => value + 1);
  };

  return <main className="lobby-shell">
    <header className="lobby-header">
      <div>
        <h1>{LOBBY_TITLE}</h1>
        <p className="muted">欢迎，{props.username}</p>
      </div>
      <button className="danger header-button" onClick={props.onLogout}>退出登录</button>
    </header>

    <nav className="lobby-quick">
      <button onClick={() => openCreate(null)}>创建房间</button>
      <button onClick={() => { setJoinCode(""); setPanel("join"); }}>加入房间</button>
      <button onClick={() => setPanel("templates")}>我的剧本</button>
      <button onClick={() => setPanel("games")}>我的存档</button>
    </nav>

    {props.error && <p className="error-banner">{props.error}</p>}
    {props.notice && <p className="notice-banner">{props.notice}</p>}

    <section className="panel">
      <div className="panel-head">
        <h2>活跃房间</h2>
        <button className="secondary compact-button" title="刷新并重新抽取活跃房间"
          onClick={() => void refreshRooms()}>↻ 刷新</button>
      </div>
      {!props.rooms.length && <p className="muted">暂无活跃房间。</p>}
      <div className="lobby-list">
        {rooms.map((room) => <article className="row-card row-inline" key={room.code}>
          <button className="row-main row-toggle" disabled={roomIsFull(room)}
            onClick={() => openRoom(room, "")}>
            <strong>{roomLabel(room)}</strong>
            <span className="muted">
              {roomOccupancy(room)} 人{room.has_password ? " · 🔒" : ""}
              {roomGameName(room) ? ` · ${roomGameName(room)}` : ""}
            </span>
          </button>
          <div className="row-actions">
            <button disabled={roomIsFull(room)} onClick={() => openRoom(room, "")}>
              {roomIsFull(room) ? "已满" : "加入"}
            </button>
            {mayManageRoom(room, props.userId) && <button className="secondary"
              onClick={() => void props.onCloseRoom(room.code)}>关闭</button>}
          </div>
        </article>)}
      </div>
    </section>

    <section className="panel">
      <div className="panel-head">
        <h2>剧本广场</h2>
        <button className="secondary compact-button" title="从公开剧本中随机抽取"
          onClick={() => setTemplatePick((value) => value + 1)}>↻ 随机</button>
      </div>
      {!plazaScripts.length && <p className="muted">剧本广场暂时没有公开剧本。</p>}
      <div className="lobby-list">
        {plazaScripts.map((template) => <button className="row-card row-inline row-link" key={template.id}
          onClick={() => void openDetail(template)}>
          <span className="row-main">
            <strong>{template.name}</strong>
            <span className="muted">{templateMeta(template)}</span>
          </span>
          <span className="muted">{templateOwnerLabel(template)}</span>
        </button>)}
      </div>
    </section>

    {panel === "create" && <OverlayPanel title="创建房间" onClose={close}>
      <CreateRoomForm games={props.games} templates={props.templates} preset={preset}
        busy={props.busy} error={props.error} onClose={close}
        onSubmit={async (body) => { if (await props.onCreate(body)) close(); }} />
    </OverlayPanel>}

    {panel === "join" && <OverlayPanel title="加入房间" onClose={close}>
      <JoinRoomForm busy={props.busy} error={props.error} initialCode={joinCode}
        onClose={close} onSearch={props.onSearch}
        onJoin={(room, password) => { close(); props.onJoin(room, password); }} />
    </OverlayPanel>}

    {panel === "games" && <OverlayPanel title="我的存档" onClose={close}>
      <MyGamesPanel games={props.games} busy={props.busy} error={props.error}
        onLoad={(game) => { close(); openCreate({ source: "game", id: game.id }); }}
        onRename={(game, name) => void props.onRenameGame(game, name)}
        onCopy={(game) => void props.onCopyGame(game)}
        onDelete={(game) => void props.onDeleteGame(game)} />
    </OverlayPanel>}

    {panel === "templates" && <OverlayPanel title="我的剧本" onClose={close}>
      <MyTemplatesPanel templates={props.myTemplates} roleCounts={props.roleCounts}
        busy={props.busy} error={props.error}
        onCreate={(name, roleCount) => void props.onCreateTemplate(name, roleCount)}
        onRename={(template, name) => void props.onRenameTemplate(template, name)}
        onToggleVisibility={(template, isPublic) =>
          void props.onToggleTemplateVisibility(template, isPublic)}
        onCopy={(template) => void props.onCopyTemplate(template)}
        onDelete={(template) => void props.onDeleteTemplate(template)}
        onDetail={(template) => void openDetail(template)}
        onUse={(template) => { close(); openCreate({
          source: "template", id: template.id, name: `${template.name} - Game`,
        }); }} />
    </OverlayPanel>}

    {detail && <OverlayPanel title={detail.name} onClose={closeDetail}>
      <TemplateDetailPanel template={detail}
        onUse={(template) => { close(); openCreate({
          source: "template", id: template.id, name: `${template.name} - Game`,
        }); }} />
    </OverlayPanel>}
  </main>;
}
