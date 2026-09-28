import type { PlayerState, Role, RoleDefinition, RoomMessage, ServerMessage, StoryEntry } from "./protocol";

export type ConnectionStatus =
  | "DISCONNECTED"
  | "CONNECTING"
  | "CONNECTED"
  | "RECONNECTING"
  | "SESSION_EXPIRED"
  | "ERROR";

export type MobileTab = "story" | "chat";

export type ClientState = {
  connection: ConnectionStatus;
  nickname: string;
  roomKey: string;
  roomKeyRequired: boolean;
  resumeToken: string;
  roles: RoleDefinition[];
  role: Role | null;
  viewRole: Role | null;
  characterName: string;
  scenario: string;
  presence: Array<{ name: string; role: Role | null; connected?: boolean }>;
  players: Partial<Record<Role, PlayerState>>;
  round: number | null;
  processingStage: string | null;
  opening: string;
  storyEntries: StoryEntry[];
  actionDraft: string;
  chatDraft: string;
  roomMessages: RoomMessage[];
  mobileTab: MobileTab;
  chatUnread: number;
  errors: string[];
  notices: string[];
};

export const initialState: ClientState = {
  connection: "DISCONNECTED", nickname: "", roomKey: "", roomKeyRequired: true, resumeToken: "",
  roles: [], role: null, viewRole: null, characterName: "", scenario: "", presence: [],
  players: {}, round: null, processingStage: null, opening: "", storyEntries: [],
  actionDraft: "", chatDraft: "", roomMessages: [], mobileTab: "story",
  chatUnread: 0, errors: [], notices: [],
};

export type ClientAction =
  | { type: "connection"; status: ConnectionStatus; detail?: string }
  | { type: "nickname"; nickname: string }
  | { type: "room_key"; roomKey: string }
  | { type: "session"; nickname: string; roomKey: string; resumeToken: string }
  | { type: "ui_config"; roomKeyRequired: boolean }
  | { type: "action_draft"; text: string }
  | { type: "chat_draft"; text: string }
  | { type: "mobile_tab"; tab: MobileTab }
  | { type: "server"; message: ServerMessage }
  | { type: "reset" };

export function reducer(state: ClientState, action: ClientAction): ClientState {
  if (action.type === "reset") return initialState;
  if (action.type === "nickname") return { ...state, nickname: action.nickname };
  if (action.type === "room_key") return { ...state, roomKey: action.roomKey };
  if (action.type === "session") {
    return { ...state, nickname: action.nickname, roomKey: action.roomKey, resumeToken: action.resumeToken };
  }
  if (action.type === "ui_config") {
    return { ...state, roomKeyRequired: action.roomKeyRequired };
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
      return { ...state, connection: "CONNECTED", nickname: message.name, role: message.role,
        viewRole: message.view_role, scenario: message.scenario, resumeToken: message.resume_token,
        roles: message.roles };
    case "resumed":
      return { ...state, connection: "CONNECTED", nickname: message.name, role: message.role,
        viewRole: message.view_role, scenario: message.scenario, resumeToken: message.resume_token,
        roles: message.roles };
    case "presence":
      return { ...state, presence: message.users };
    case "identity_changed":
      return { ...state, role: message.role, viewRole: message.view_role,
        opening: message.reset ? "" : state.opening,
        storyEntries: message.reset ? [] : state.storyEntries,
        actionDraft: message.role ? state.actionDraft : "" };
    case "role_view":
      return { ...state, viewRole: message.role, characterName: message.character_name,
        opening: message.opening, storyEntries: message.history,
        actionDraft: state.role === message.role && message.draft !== undefined
          ? message.draft : state.actionDraft };
    case "role_round":
      if (message.role !== state.viewRole) return state;
      return { ...state, storyEntries: [...state.storyEntries, ...message.entries] };
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
      return { ...state, errors: [...state.errors, message.detail] };
    case "notice":
      return { ...state, notices: [...state.notices, message.detail ?? message.text ?? ""] };
    case "role_assigned":
      return state;
  }
}

function isVisibleStage(stage: string): boolean {
  return ["WORLD_UPDATING", "VIEW_GENERATING", "NARRATION_GENERATING"].includes(stage);
}
