import { humanizeError } from "./errors";
import type { AuthUser, PlayerState, Role, RoleDefinition, RoomMessage, ServerMessage, StoryEntry } from "./protocol";

export type ConnectionStatus =
  | "DISCONNECTED"
  | "CONNECTING"
  | "CONNECTED"
  | "RECONNECTING"
  | "SESSION_EXPIRED"
  | "UNAUTHENTICATED"
  | "ERROR";

export type MobileTab = "story" | "chat";

export type ClientState = {
  authChecked: boolean;
  authUser: AuthUser | null;
  isHost: boolean;
  connection: ConnectionStatus;
  roomKey: string;
  roomKeyRequired: boolean;
  allowRegistration: boolean;
  roomDisconnectTimeoutSeconds: number;
  resumeToken: string;
  roles: RoleDefinition[];
  role: Role | null;
  viewRole: Role | null;
  characterName: string;
  scenario: string;
  presence: Array<{ user_id?: number; name: string; role: Role | null; connected?: boolean }>;
  players: Partial<Record<Role, PlayerState>>;
  round: number | null;
  processingStage: string | null;
  opening: string;
  storyEntries: StoryEntry[];
  characterStatus: unknown | null;
  actionDraft: string;
  chatDraft: string;
  roomMessages: RoomMessage[];
  mobileTab: MobileTab;
  chatUnread: number;
  errors: string[];
  notices: string[];
};

export const initialState: ClientState = {
  authChecked: false, authUser: null, isHost: false,
  connection: "DISCONNECTED", roomKey: "", roomKeyRequired: true, allowRegistration: true,
  roomDisconnectTimeoutSeconds: 60,
  resumeToken: "", roles: [], role: null, viewRole: null, characterName: "", scenario: "",
  presence: [], players: {}, round: null, processingStage: null, opening: "",
  storyEntries: [], characterStatus: null,
  actionDraft: "", chatDraft: "", roomMessages: [], mobileTab: "story",
  chatUnread: 0, errors: [], notices: [],
};

export type ClientAction =
  | { type: "auth_checked" }
  | { type: "auth"; user: AuthUser }
  | { type: "auth_lost"; detail?: string }
  | { type: "connection"; status: ConnectionStatus; detail?: string }
  | { type: "room_key"; roomKey: string }
  | { type: "session"; roomKey: string; resumeToken: string }
  | { type: "ui_config"; roomKeyRequired: boolean; allowRegistration: boolean; roomDisconnectTimeoutSeconds?: number }
  | { type: "action_draft"; text: string }
  | { type: "chat_draft"; text: string }
  | { type: "mobile_tab"; tab: MobileTab }
  | { type: "server"; message: ServerMessage }
  | { type: "reset" };

export function reducer(state: ClientState, action: ClientAction): ClientState {
  if (action.type === "reset") return { ...initialState, authChecked: true };
  if (action.type === "auth_checked") return { ...state, authChecked: true };
  if (action.type === "auth") {
    return { ...state, authChecked: true, authUser: action.user, errors: [] };
  }
  if (action.type === "auth_lost") {
    return {
      ...state,
      authChecked: true,
      authUser: null,
      isHost: false,
      resumeToken: "",
      errors: action.detail ? [...state.errors, action.detail] : state.errors,
    };
  }
  if (action.type === "room_key") return { ...state, roomKey: action.roomKey };
  if (action.type === "session") {
    return { ...state, roomKey: action.roomKey, resumeToken: action.resumeToken };
  }
  if (action.type === "ui_config") {
    return {
      ...state,
      roomKeyRequired: action.roomKeyRequired,
      allowRegistration: action.allowRegistration,
      roomDisconnectTimeoutSeconds: action.roomDisconnectTimeoutSeconds ?? state.roomDisconnectTimeoutSeconds,
    };
  }
  if (action.type === "action_draft") return { ...state, actionDraft: action.text };
  if (action.type === "chat_draft") return { ...state, chatDraft: action.text };
  if (action.type === "mobile_tab") {
    return { ...state, mobileTab: action.tab, chatUnread: action.tab === "chat" ? 0 : state.chatUnread };
  }
  if (action.type === "connection") {
    return {
      ...state,
      connection: action.status,
      errors: action.detail ? [...state.errors, action.detail] : state.errors,
    };
  }

  const message = action.message;
  switch (message.type) {
    case "joined":
      return { ...state, connection: "CONNECTED", authUser: message.user, isHost: message.is_host,
        role: message.role, viewRole: message.view_role, scenario: message.scenario,
        resumeToken: message.resume_token, roles: message.roles };
    case "resumed":
      return { ...state, connection: "CONNECTED", authUser: message.user, isHost: message.is_host,
        role: message.role, viewRole: message.view_role, scenario: message.scenario,
        resumeToken: message.resume_token, roles: message.roles };
    case "session_replaced":
      return { ...state, connection: "DISCONNECTED", notices: [...state.notices, message.detail] };
    case "room_closed":
      return { ...state, connection: "DISCONNECTED", notices: [...state.notices, message.detail] };
    case "presence":
      return { ...state, presence: message.users };
    case "identity_changed":
      return { ...state, role: message.role, viewRole: message.view_role,
        opening: message.reset ? "" : state.opening,
        storyEntries: message.reset ? [] : state.storyEntries,
        characterStatus: message.reset ? null : state.characterStatus,
        actionDraft: message.role ? state.actionDraft : "" };
    case "role_view":
      return { ...state, viewRole: message.role, characterName: message.character_name,
        opening: message.opening, storyEntries: message.history,
        characterStatus: message.character_status,
        actionDraft: state.role === message.role && message.draft !== undefined
          ? message.draft : state.actionDraft };
    case "role_round":
      if (message.role !== state.viewRole) return state;
      return { ...state, storyEntries: [...state.storyEntries, ...message.entries],
        characterStatus: message.character_status };
    case "room_message":
      return { ...state, roomMessages: [...state.roomMessages, message],
        chatUnread: state.mobileTab === "chat" ? 0 : state.chatUnread + 1 };
    case "state":
      return { ...state, round: message.round, players: message.players,
        processingStage: isVisibleStage(message.stage) ? message.stage : null,
        actionDraft: state.role && message.players[state.role]?.status === "EDITING"
          && !message.players[state.role]?.has_action ? "" : state.actionDraft };
    case "processing_stage":
      return { ...state, processingStage: isVisibleStage(message.stage) ? message.stage : null };
    case "status":
      return { ...state, round: message.round, players: message.players,
        actionDraft: message.draft };
    case "round_complete":
      return { ...state, processingStage: null };
    case "error":
      return {
        ...state,
        errors: [...state.errors, humanizeError(message.code, message.detail)],
      };
    case "notice":
      return { ...state, notices: [...state.notices, message.detail ?? message.text ?? ""] };
    case "kicked":
      return {
        ...state,
        role: null,
        viewRole: null,
        isHost: false,
        connection: "DISCONNECTED",
        errors: [...state.errors, message.reason || "你已被房主移出房间"],
      };
    case "role_assigned":
      return state;
  }
}

function isVisibleStage(stage: string): boolean {
  return ["WORLD_UPDATING", "VIEW_GENERATING", "NARRATION_GENERATING"].includes(stage);
}
