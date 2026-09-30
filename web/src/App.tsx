import { useEffect, useRef, useState } from "react";
import GameApp from "./GameApp";
import { apiDelete, apiGet, apiPatch, apiPost } from "./api";
import { AuthScreen } from "./components/AuthScreen";
import { PlatformHome } from "./components/PlatformHome";
import { TemplateEditor } from "./components/TemplateEditor";
import { humanizeError } from "./errors";
import { formatLocalTime } from "./lobby";
import type { AuthUser } from "./protocol";
import type { GameItem, RoomItem, TemplateItem } from "./types";
import { downloadTemplateZip, uploadTemplateZip } from "./templateZip";
import { downloadGameHistory } from "./gameHistory";
import { invitedRoom } from "./invite";

type UiConfig = {
  platform_mode?: boolean;
  font_scale?: number;
  allow_registration?: boolean;
  min_role_count?: number;
  max_role_count?: number;
};

const DEFAULT_ROLE_COUNTS = [2, 3, 4];

export default function App() {
  const [config, setConfig] = useState<UiConfig | null>(null);

  useEffect(() => {
    fetch("/ui-config.json")
      .then((response) => response.json())
      .then((value: UiConfig) => {
        if (typeof value.font_scale === "number") {
          document.documentElement.style.setProperty("--font-scale", String(value.font_scale));
        }
        setConfig(value);
      })
      .catch(() => setConfig({ platform_mode: true }));
  }, []);

  if (!config) {
    return <main className="join-shell"><div className="join-card">
      <h1>AI RP Engine</h1><p className="muted">正在连接平台…</p>
    </div></main>;
  }
  if (!config.platform_mode) return <GameApp />;
  return <PlatformApp
    allowRegistration={config.allow_registration !== false}
    roleCounts={roleCounts(config.min_role_count, config.max_role_count)} />;
}

/** 2/3/4 role counts, bounded by the deployment limits. */
function roleCounts(minimum?: number, maximum?: number): number[] {
  const low = typeof minimum === "number" && minimum >= 1 ? minimum : 2;
  const high = typeof maximum === "number" && maximum >= low ? maximum : 4;
  const values: number[] = [];
  for (let count = low; count <= high; count += 1) values.push(count);
  return values.length ? values : DEFAULT_ROLE_COUNTS;
}

function PlatformApp({ allowRegistration, roleCounts: counts }: {
  allowRegistration: boolean;
  roleCounts: number[];
}) {
  const [user, setUser] = useState<AuthUser | null>(null);
  const [inviteCode, setInviteCode] = useState(() => invitedRoom(window.location.search));
  const [checked, setChecked] = useState(false);
  // `templates` = usable Scripts (own + public) for the Create Room selector;
  // `publicTemplates` = the 剧本广场 list (public only).
  const [templates, setTemplates] = useState<TemplateItem[]>([]);
  const [publicTemplates, setPublicTemplates] = useState<TemplateItem[]>([]);
  const [myTemplates, setMyTemplates] = useState<TemplateItem[]>([]);
  const [games, setGames] = useState<GameItem[]>([]);
  const [rooms, setRooms] = useState<RoomItem[]>([]);
  const [currentRoom, setCurrentRoom] = useState<RoomItem | null>(null);
  const [recoveryFailedRoom, setRecoveryFailedRoom] = useState<string | null>(null);
  const [activeRoom, setActiveRoom] = useState<{ code: string; password: string } | null>(null);
  const [editingTemplateId, setEditingTemplateId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [busy, setBusy] = useState(false);
  const requestId = useRef(0);

  const clearMessages = () => { setError(""); setNotice(""); };

  const loadTemplates = async () => {
    const [available, plaza, mine] = await Promise.all([
      apiGet<{ templates: TemplateItem[] }>("/api/templates"),
      apiGet<{ templates: TemplateItem[] }>("/api/templates/public"),
      apiGet<{ templates: TemplateItem[] }>("/api/templates/mine"),
    ]);
    if (available.ok) setTemplates(available.data.templates);
    if (plaza.ok) setPublicTemplates(plaza.data.templates);
    if (mine.ok) setMyTemplates(mine.data.templates);
    return available.ok ? "" : available.error;
  };
  const loadGames = async () => {
    const result = await apiGet<{ games: GameItem[] }>("/api/games");
    if (result.ok) setGames(result.data.games);
    return result.ok ? "" : result.error;
  };
  const refreshRooms = async () => {
    const [result, current] = await Promise.all([
      apiGet<{ rooms: RoomItem[] }>("/api/rooms"),
      apiGet<{ room: RoomItem | null; recovery_failed_room?: string | null }>("/api/rooms/current"),
    ]);
    if (result.ok) setRooms(result.data.rooms);
    if (current.ok) {
      setCurrentRoom(current.data.room);
      setRecoveryFailedRoom(current.data.recovery_failed_room ?? null);
    }
    if (!result.ok) setError(result.error);
  };
  const refreshAll = async () => {
    const id = ++requestId.current;
    const [templatesError, gamesError, roomResult, currentResult] = await Promise.all([
      loadTemplates(), loadGames(), apiGet<{ rooms: RoomItem[] }>("/api/rooms"),
      apiGet<{ room: RoomItem | null; recovery_failed_room?: string | null }>("/api/rooms/current"),
    ]);
    if (id !== requestId.current) return;
    if (roomResult.ok) setRooms(roomResult.data.rooms);
    if (currentResult.ok) {
      setCurrentRoom(currentResult.data.room);
      setRecoveryFailedRoom(currentResult.data.recovery_failed_room ?? null);
    }
    setError(templatesError || gamesError || (roomResult.ok ? "" : roomResult.error));
  };

  useEffect(() => { fetch("/api/me").then(async (response) => {
    if (response.ok) setUser(await response.json() as AuthUser);
  }).finally(() => setChecked(true)); }, []);
  useEffect(() => {
    if (user && !activeRoom) void refreshAll();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [user, activeRoom]);

  const authenticate = async (mode: "login" | "register", username: string, password: string) => {
    try {
      const response = await fetch(`/api/${mode}`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ username, password }),
      });
      const body = await response.json() as Partial<AuthUser> & { detail?: string; error?: string };
      if (!response.ok) return humanizeError(body.error, body.detail);
      setUser({ id: Number(body.id), username: String(body.username) });
      return null;
    } catch { return "无法连接服务器"; }
  };

  /** Runs one resource action: busy flag, error capture, then a refresh. */
  const run = async (
    action: () => Promise<{ ok: true; data: unknown } | { ok: false; error: string }>,
    after?: string,
  ): Promise<boolean> => {
    clearMessages();
    setBusy(true);
    const result = await action();
    setBusy(false);
    if (!result.ok) {
      setError(result.error);
      return false;
    }
    await refreshAll();
    if (after) setNotice(after);
    return true;
  };

  const createRoom = async (body: object) => {
    clearMessages();
    setBusy(true);
    const result = await apiPost<{ code?: string }>("/api/rooms", body);
    setBusy(false);
    if (!result.ok) { setError(result.error); return false; }
    const code = result.data.code;
    if (!code) { setError("创建房间失败"); return false; }
    setActiveRoom({ code, password: String((body as { password?: string }).password ?? "") });
    return true;
  };

  const searchRoom = async (code: string) => {
    clearMessages();
    const result = await apiGet<RoomItem>(`/api/rooms/${encodeURIComponent(code)}`);
    if (!result.ok) { setError(result.error); return null; }
    return result.data;
  };

  const closeRoom = async (code: string) => {
    clearMessages();
    const result = await apiDelete<{ ok: boolean }>(`/api/rooms/${code}`);
    if (!result.ok) { setError(result.error); return; }
    await refreshAll();
  };

  const leaveCurrentRoom = async () => {
    if (!currentRoom) return;
    clearMessages();
    const result = await apiPost<{ ok: boolean }>(`/api/rooms/${currentRoom.code}/leave`);
    if (!result.ok) { setError(result.error); return; }
    setCurrentRoom(null);
    await refreshAll();
    setNotice("已离开房间。");
  };

  const logout = () => {
    void fetch("/api/logout", { method: "POST" }).finally(() => {
      setUser(null);
      setTemplates([]); setPublicTemplates([]); setMyTemplates([]);
      setGames([]); setRooms([]); setCurrentRoom(null); setRecoveryFailedRoom(null);
      setEditingTemplateId(null);
      clearMessages();
    });
  };

  const renameGame = async (game: GameItem, name: string) => {
    await run(() => apiPatch<GameItem>(`/api/games/${game.id}`, { name }), `已重命名为「${name}」。`);
  };
  const copyGame = async (game: GameItem) => {
    await run(() => apiPost<GameItem>(`/api/games/${game.id}/copy`), "存档已复制。");
  };
  const deleteGame = async (game: GameItem) => {
    await run(() => apiDelete<{ ok: boolean }>(`/api/games/${game.id}`), "存档已删除。");
  };
  const exportGameHistory = async (game: GameItem) => {
    clearMessages();
    const failure = await downloadGameHistory("game", game.id);
    if (failure) setError(failure);
  };
  const createTemplate = async (name: string, roleCount: number) => {
    clearMessages();
    setBusy(true);
    const result = await apiPost<TemplateItem>("/api/templates", { name, role_count: roleCount });
    setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    await refreshAll();
    setEditingTemplateId(result.data.id);
  };
  const renameTemplate = async (template: TemplateItem, name: string) => {
    await run(
      () => apiPatch<TemplateItem>(`/api/templates/${template.id}`, { name }),
      `已重命名为「${name}」。`,
    );
  };
  /** Detail overlay data comes from GET /api/templates/<id> (introduction/tags). */
  const loadTemplateDetail = async (template: TemplateItem): Promise<TemplateItem | null> => {
    clearMessages();
    const result = await apiGet<TemplateItem>(`/api/templates/${template.id}`);
    if (!result.ok) { setError(result.error); return null; }
    return result.data;
  };
  const toggleTemplateVisibility = async (template: TemplateItem, isPublic: boolean) => {
    await run(
      () => apiPatch<TemplateItem>(`/api/templates/${template.id}`, { is_public: isPublic }),
      isPublic
        ? "剧本已设为公开，所有用户都能在「剧本广场」中看到它。"
        : "剧本已设为私密，只有你自己可用。",
    );
  };
  const copyTemplate = async (template: TemplateItem) => {
    await run(() => apiPost<TemplateItem>(`/api/templates/${template.id}/copy`), "剧本已复制。");
  };
  const deleteTemplate = async (template: TemplateItem) => {
    await run(
      () => apiDelete<{ ok: boolean }>(`/api/templates/${template.id}`),
      "剧本已删除，已有存档不受影响。",
    );
  };
  const importTemplateZip = async (file: File) => {
    clearMessages();
    setBusy(true);
    const result = await uploadTemplateZip(file);
    setBusy(false);
    if (!result.ok) { setError(result.error); return; }
    await refreshAll();
    setEditingTemplateId(result.data.id);
  };
  const exportTemplateZip = async (template: TemplateItem) => {
    clearMessages();
    const failure = await downloadTemplateZip(template.id);
    if (failure) setError(failure);
  };

  if (!checked) {
    return <main className="join-shell"><div className="join-card">
      <h1>AI RP Engine</h1><p className="muted">正在检查登录状态…</p>
    </div></main>;
  }
  if (!user) return <AuthScreen allowRegistration={allowRegistration} onSubmit={authenticate} />;
  if (activeRoom) {
    return <GameApp platformRoom={activeRoom} onPlatformLeave={() => {
      setActiveRoom(null);
      setNotice(`已于 ${formatLocalTime(new Date().toISOString())} 离开房间。`);
    }} />;
  }
  if (editingTemplateId) return <TemplateEditor templateId={editingTemplateId}
    roleCounts={counts} onBack={() => setEditingTemplateId(null)}
    onSaved={async () => { await refreshAll(); }} />;
  return <PlatformHome
    inviteCode={inviteCode}
    onInviteConsumed={() => {
      setInviteCode(null);
      const url = new URL(window.location.href);
      url.searchParams.delete("room");
      window.history.replaceState(null, "", url.pathname + url.search + url.hash);
    }}
    userId={user.id} username={user.username} roleCounts={counts}
    templates={templates} publicTemplates={publicTemplates}
    myTemplates={myTemplates} games={games} rooms={rooms} currentRoom={currentRoom}
    recoveryFailedRoom={recoveryFailedRoom}
    error={error} notice={notice} busy={busy}
    onLogout={logout}
    onReturnCurrent={() => currentRoom && setActiveRoom({ code: currentRoom.code, password: "" })}
    onLeaveCurrent={leaveCurrentRoom}
    onRefreshRooms={refreshRooms}
    onCreate={createRoom}
    onJoin={(room, password) => setActiveRoom({ code: room.code, password })}
    onCloseRoom={closeRoom}
    onSearch={searchRoom}
    onRenameGame={renameGame} onCopyGame={copyGame} onDeleteGame={deleteGame}
    onExportGameHistory={exportGameHistory}
    onCreateTemplate={createTemplate} onRenameTemplate={renameTemplate}
    onToggleTemplateVisibility={toggleTemplateVisibility}
    onLoadTemplateDetail={loadTemplateDetail}
    onEditTemplate={(template) => setEditingTemplateId(template.id)}
    onImportTemplateZip={(file) => void importTemplateZip(file)}
    onExportTemplateZip={(template) => void exportTemplateZip(template)}
    onCopyTemplate={copyTemplate} onDeleteTemplate={deleteTemplate} />;
}
