export const PROTOCOL_VERSION = 3;
export const MAX_NICKNAME_LENGTH = 32;
export const MAX_ROOM_CHAT_LENGTH = 4000;
export const MAX_ACTION_LENGTH = 20000;

export type Role = string;
export type RoleDefinition = { id: Role; name: string };
export type PlayerStatus = "LOBBY" | "EDITING" | "READY" | "PAUSED" | "PROCESSING";
export type ProcessingStage =
  | "WAITING_INPUT"
  | "WORLD_UPDATING"
  | "WORLD_DONE"
  | "VIEW_GENERATING"
  | "VIEW_DONE"
  | "NARRATION_GENERATING"
  | "FINISHED";

export type StoryEntry = {
  round?: number;
  kind: "action" | "narration" | "statusbar";
  content: unknown;
};

export type PlayerState = {
  status: PlayerStatus;
  has_action: boolean;
  connected: boolean;
  user?: string | null;
  character_name?: string | null;
};

export type JoinedMessage = {
  type: "joined";
  protocol_version: number;
  name: string;
  role: Role | null;
  view_role: Role | null;
  scenario: string;
  resume_token: string;
  roles: RoleDefinition[];
};
export type ResumedMessage = {
  type: "resumed";
  protocol_version: number;
  name: string;
  role: Role | null;
  view_role: Role | null;
  scenario: string;
  resume_token: string;
  roles: RoleDefinition[];
};
export type PresenceMessage = { type: "presence"; users: Array<{ name: string; role: Role | null; connected?: boolean }> };
export type IdentityChangedMessage = { type: "identity_changed"; role: Role | null; view_role: Role | null; reset: boolean };
export type RoleAssignedMessage = { type: "role_assigned"; assignments: Record<string, Role | null>; changed_users: string[] };
export type RoleViewMessage = {
  type: "role_view";
  role: Role;
  character_name: string;
  opening: string;
  history: StoryEntry[];
  statusbar: unknown;
  reset: boolean;
  current_view?: unknown;
  draft?: string;
};
export type RoleRoundMessage = { type: "role_round"; role: Role; round: number; entries: StoryEntry[]; statusbar: unknown };
export type RoomMessage = {
  type: "room_message";
  kind: "system" | "host" | "player" | "spectator";
  sender: string | null;
  role: Role | null;
  character_name: string | null;
  text: string;
};
export type StateMessage = { type: "state"; round: number; stage: ProcessingStage; players: Record<Role, PlayerState> };
export type ProcessingStageMessage = { type: "processing_stage"; round: number; stage: ProcessingStage };
export type StatusMessage = {
  type: "status";
  scenario: string;
  role: Role;
  round: number;
  stage: ProcessingStage;
  players: Record<Role, PlayerState>;
  draft: string;
  statusbar: unknown;
  current_view?: unknown;
};
export type RoundCompleteMessage = { type: "round_complete"; round: number };
export type ErrorMessage = { type: "error"; detail: string };
export type NoticeMessage = { type: "notice"; detail?: string; text?: string };

export type ServerMessage =
  | JoinedMessage | ResumedMessage | PresenceMessage | IdentityChangedMessage
  | RoleAssignedMessage | RoleViewMessage | RoleRoundMessage | RoomMessage
  | StateMessage | ProcessingStageMessage | StatusMessage | RoundCompleteMessage
  | ErrorMessage | NoticeMessage;

export type ClientMessage =
  | { type: "join"; name: string; room_key?: string }
  | { type: "resume"; name: string; room_key?: string; resume_token: string }
  | { type: "leave" }
  | { type: "action"; text: string }
  | { type: "submit" }
  | { type: "cancel_submit" }
  | { type: "pause" }
  | { type: "resume" }
  | { type: "status" }
  | { type: "room_chat"; text: string }
  | { type: "view"; role: Role };

export function parseServerMessage(raw: string): ServerMessage {
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object" || !("type" in value) || typeof value.type !== "string") {
    throw new Error("服务器发送了无效消息");
  }
  return value as ServerMessage;
}
