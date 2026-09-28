export type RoomSummary = {
  code: string;
  has_password: boolean;
  owner_user_id: number;
};

export function normalizeRoomCode(value: string): string {
  return value.trim().toUpperCase();
}

export function roomNeedsPassword(room: RoomSummary): boolean {
  return room.has_password;
}

export function mayManageRoom(room: RoomSummary, userId: number): boolean {
  return room.owner_user_id === userId;
}

export function validRoomPassword(value: string): boolean {
  return value === "" || /^[A-Za-z0-9_]{1,32}$/.test(value);
}

export function createRoomPayload(
  source: "game" | "template",
  id: string,
  password: string,
  gameName = "",
): object {
  return source === "game"
    ? { source, game_id: id, password }
    : { source, template_id: id, game_name: gameName, password };
}
