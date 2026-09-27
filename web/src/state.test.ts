import { describe, expect, it } from "vitest";
import { initialState, reducer } from "./state";

describe("protocol reducer", () => {
  it("replaces story history without touching room chat", () => {
    const withChat = reducer(initialState, { type: "server", message: {
      type: "room_message", kind: "spectator", sender: "Tom", role: null,
      character_name: null, text: "hello",
    }});
    const viewed = reducer(withChat, { type: "server", message: {
      type: "role_view", role: "A", character_name: "林岚", reset: true,
      history: [{ kind: "action", content: "open" }], statusbar: { hp: 10 },
    }});
    expect(viewed.storyEntries).toHaveLength(1);
    expect(viewed.roomMessages).toHaveLength(1);
  });

  it("never adopts a spectator view as a private draft", () => {
    const spectator = reducer(initialState, { type: "server", message: {
      type: "role_view", role: "A", character_name: "林岚", reset: true,
      history: [], statusbar: {}, draft: "should be ignored",
    }});
    expect(spectator.actionDraft).toBe("");
  });

  it("clears player-only state when reassigned to spectator", () => {
    const player = { ...initialState, role: "A" as const, viewRole: "A" as const, actionDraft: "secret",
      storyEntries: [{ kind: "action" as const, content: "old" }] };
    const spectator = reducer(player, { type: "server", message: {
      type: "identity_changed", role: null, view_role: "A", reset: true,
    }});
    expect(spectator.actionDraft).toBe("");
    expect(spectator.storyEntries).toEqual([]);
  });

  it("resumed restores connection and identity", () => {
    const resumed = reducer({ ...initialState, connection: "RECONNECTING" }, { type: "server", message: {
      type: "resumed", protocol_version: 2, name: "Alice", role: "A", view_role: "A",
      scenario: "lighthouse", resume_token: "token-2",
    }});
    expect(resumed.connection).toBe("CONNECTED");
    expect(resumed.role).toBe("A");
    expect(resumed.viewRole).toBe("A");
    expect(resumed.resumeToken).toBe("token-2");
  });

  it("reconnect keeps current story and room chat", () => {
    const active = {
      ...initialState,
      storyEntries: [{ kind: "narration" as const, content: "wind" }],
      roomMessages: [{ type: "room_message" as const, kind: "system" as const, sender: null, role: null, character_name: null, text: "hi" }],
    };
    const reconnecting = reducer(active, { type: "connection", status: "RECONNECTING" });
    expect(reconnecting.connection).toBe("RECONNECTING");
    expect(reconnecting.storyEntries).toHaveLength(1);
    expect(reconnecting.roomMessages).toHaveLength(1);
  });

  it("room messages increment unread on story tab and reset on chat tab", () => {
    const withMessage = reducer(initialState, { type: "server", message: {
      type: "room_message", kind: "player", sender: "Alice", role: "A",
      character_name: "林岚", text: "hi",
    }});
    expect(withMessage.chatUnread).toBe(1);
    const onChat = reducer(withMessage, { type: "mobile_tab", tab: "chat" });
    expect(onChat.chatUnread).toBe(0);
  });

  it("ui_config can disable the room key field", () => {
    const configured = reducer(initialState, { type: "ui_config", roomKeyRequired: false });
    expect(configured.roomKeyRequired).toBe(false);
    expect(reducer(initialState, { type: "ui_config", roomKeyRequired: true }).roomKeyRequired).toBe(true);
  });
});
