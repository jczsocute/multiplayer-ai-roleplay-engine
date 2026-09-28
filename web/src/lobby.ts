/** Pure helpers for the Lobby. Kept free of React and fetch so they are testable. */
import type { GameItem, RoomItem, TemplateItem } from "./types";

/** The Lobby header shows Chinese, not the product name. */
export const LOBBY_TITLE = "AI 角色扮演引擎";

/** Lobby lists show at most this many rows; 刷新/随机 re-pick from the pool. */
export const MAX_LOBBY_ROWS = 5;

/** Pick up to `count` entries at random (the whole list when it is short).
 *
 * `random` is injectable so the behaviour stays testable without stubbing globals.
 */
export function pickRandom<T>(items: T[], count: number, random: () => number = Math.random): T[] {
  if (count <= 0) return [];
  if (items.length <= count) return [...items];
  const pool = [...items];
  const picked: T[] = [];
  while (picked.length < count && pool.length > 0) {
    const index = Math.min(pool.length - 1, Math.floor(random() * pool.length));
    picked.push(pool.splice(index, 1)[0]);
  }
  return picked;
}

/** Local wall-clock rendering: the API only ever sends ISO timestamps. */
export function formatLocalTime(iso: string | null | undefined): string {
  if (!iso) return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  const pad = (value: number) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())} ` +
    `${pad(date.getHours())}:${pad(date.getMinutes())}`;
}

/** Rooms are named after their owner, never after an internal game id. */
export function roomLabel(room: RoomItem): string {
  const owner = room.owner_username?.trim();
  return owner ? `${owner} 的房间` : "房间";
}

export function roomOccupancy(room: RoomItem): string {
  const max = room.max_users || 10;
  const seats = room.occupancy ?? room.connected_count ?? 0;
  return `${seats}/${max}`;
}

export function roomGameName(room: RoomItem): string {
  return room.game_name && room.game_name !== room.code ? room.game_name : "";
}

export function roomIsFull(room: RoomItem): boolean {
  const max = room.max_users || 10;
  return (room.occupancy ?? room.connected_count ?? 0) >= max;
}

export function mayManageRoom(room: RoomItem, userId: number): boolean {
  return room.owner_user_id === userId;
}

/** Templates listed in 我的剧本: owned by the caller only. */
export function ownTemplates(templates: TemplateItem[], userId: number): TemplateItem[] {
  return templates.filter((template) => template.owner_user_id === userId);
}

export function templateOwnerLabel(template: TemplateItem): string {
  return `作者：${template.owner_username || "未知"}`;
}

export function templateMeta(template: TemplateItem): string {
  return `剧本角色数 ${templateRoleCount(template)} · ${template.is_public ? "公开" : "私有"}`;
}

/** Label for the visibility toggle: it always offers the opposite state. */
export function visibilityToggleLabel(template: TemplateItem): string {
  return template.is_public ? "切换为私密" : "切换为公开";
}

export function templateRoleCount(template: TemplateItem): number {
  return template.role_count ?? template.role_names?.length ?? 0;
}

/** Second line of the Template detail: author, role count and last update only. */
export function templateDetailLine(template: TemplateItem): string {
  return `作者：${template.owner_username || "未知"} · 剧本角色数 ` +
    `${templateRoleCount(template)} · 更新时间 ${formatLocalTime(template.updated_at) || "未知"}`;
}

export function isOwnTemplate(template: TemplateItem, userId: number): boolean {
  return template.owner_user_id === userId;
}

/** Row expansion: clicking a row opens it and closes any other open row. */
export function toggledRow(openId: string | null, clickedId: string): string | null {
  return openId === clickedId ? null : clickedId;
}

export function sortedByUpdated<T extends { updated_at?: string }>(items: T[]): T[] {
  return [...items].sort((left, right) =>
    (right.updated_at ?? "").localeCompare(left.updated_at ?? ""));
}

export function copyConfirmation(kind: "存档" | "剧本", name: string): string {
  return `复制${kind}「${name}」？副本会以新名称保存。`;
}

export function deleteConfirmation(kind: "存档" | "剧本", name: string, extra = ""): string {
  return `删除${kind}「${name}」？${extra}此操作不可撤销。`;
}

export function renamePrompt(kind: "存档" | "剧本", name: string): string {
  return `请输入新的${kind}名称（当前：${name}）`;
}

export function templateEditorMessage(): string {
  return "TODO：剧本在线编辑功能尚未完成";
}

export function newTemplatePayload(name: string, roleCount: number): {
  name: string;
  role_count: number;
} {
  return { name: name.trim(), role_count: roleCount };
}

export function gameSlug(game: GameItem): string {
  return game.name.trim() || game.id;
}
