export const PROTOCOL_VERSION = 7;
export const MAX_USERNAME_LENGTH = 32;
export const MIN_PASSWORD_LENGTH = 8;
export const MAX_ROOM_CHAT_LENGTH = 4000;
export const MAX_ACTION_LENGTH = 20000;

export type Role = string;
export type RoleDefinition = { id: Role; name: string };
export type AuthUser = { id: number; username: string };
export type PlayerStatus = "EDITING" | "READY" | "PAUSED" | "PROCESSING";
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
  kind: "action" | "narration" | "character_status";
  content: unknown;
};

export type PlayerState = {
  status: PlayerStatus;
  has_action: boolean;
  connected: boolean;
  user?: string | null;
  user_id?: number | null;
  character_name?: string | null;
};

export type JoinedMessage = {
  type: "joined";
  protocol_version: number;
  user: AuthUser;
  role: Role | null;
  view_role: Role | null;
  is_host: boolean;
  reclaimed?: boolean;
  scenario: string;
  resume_token: string;
  roles: RoleDefinition[];
};
export type ResumedMessage = {
  type: "resumed";
  protocol_version: number;
  user: AuthUser;
  role: Role | null;
  view_role: Role | null;
  is_host: boolean;
  scenario: string;
  resume_token: string;
  roles: RoleDefinition[];
};
export type PresenceMessage = { type: "presence"; users: Array<{ user_id?: number; name: string; role: Role | null; connected?: boolean }> };
export type IdentityChangedMessage = { type: "identity_changed"; role: Role | null; view_role: Role | null; reset: boolean };
export type RoleAssignedMessage = { type: "role_assigned"; assignments: Record<string, Role | null>; changed_users: string[] };
export type RoleViewMessage = {
  type: "role_view";
  role: Role;
  character_name: string;
  opening: string;
  history: StoryEntry[];
  character_status: unknown | null;
  reset: boolean;
  draft?: string;
};
export type RoleRoundMessage = { type: "role_round"; role: Role; round: number; entries: StoryEntry[]; character_status: unknown | null };
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
  character_status: unknown | null;
};
export type RoundCompleteMessage = { type: "round_complete"; round: number };
export type SessionReplacedMessage = { type: "session_replaced"; detail: string };
export type RoomClosedMessage = { type: "room_closed"; detail: string };
export type KickedMessage = { type: "kicked"; reason: string };
export type ErrorMessage = { type: "error"; detail: string; code?: string };
export type NoticeMessage = { type: "notice"; detail?: string; text?: string };

export type ServerMessage =
  | JoinedMessage | ResumedMessage | PresenceMessage | IdentityChangedMessage
  | RoleAssignedMessage | RoleViewMessage | RoleRoundMessage | RoomMessage
  | StateMessage | ProcessingStageMessage | StatusMessage | RoundCompleteMessage
  | SessionReplacedMessage | RoomClosedMessage | KickedMessage | ErrorMessage
  | NoticeMessage;

export type ClientMessage =
  | { type: "join"; room_key?: string; password?: string }
  | { type: "resume"; room_key?: string; password?: string; resume_token: string }
  | { type: "leave" }
  | { type: "action"; text: string }
  | { type: "submit" }
  | { type: "cancel_submit" }
  | { type: "pause" }
  | { type: "resume" }
  | { type: "status" }
  | { type: "room_chat"; text: string }
  | { type: "retry" }
  | { type: "rollback"; round: number }
  | { type: "assign_roles"; assignments: Record<Role, number> }
  | { type: "kick_user"; user_id: number }
  | { type: "view"; role: Role };

export function parseServerMessage(raw: string): ServerMessage {
  const value: unknown = JSON.parse(raw);
  if (!value || typeof value !== "object" || !("type" in value) || typeof value.type !== "string") {
    throw new Error("服务器发送了无效消息");
  }
  return value as ServerMessage;
}
