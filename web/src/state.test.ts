import { describe, expect, it } from "vitest";
import { initialState, reducer } from "./state";

describe("protocol reducer", () => {
  it("replaces story history without touching room chat", () => {
    const withChat = reducer(initialState, { type: "server", message: {
      type: "room_message", kind: "spectator", sender: "Tom", role: null,
      character_name: null, text: "hello",
    }});
    const viewed = reducer(withChat, { type: "server", message: {
      type: "role_view", role: "P1", character_name: "林岚", reset: true,
      opening: "P1 opening", history: [{ kind: "action", content: "open" }],
      character_status: { hp: 10 },
    }});
    expect(viewed.storyEntries).toHaveLength(1);
    expect(viewed.opening).toBe("P1 opening");
    expect(viewed.characterStatus).toEqual({ hp: 10 });
    expect(viewed.roomMessages).toHaveLength(1);

    const switched = reducer(viewed, { type: "server", message: {
      type: "role_view", role: "P2", character_name: "周砚", reset: true,
      opening: "P2 opening", history: [], character_status: null,
    }});
    expect(switched.opening).toBe("P2 opening");
    expect(switched.characterStatus).toBeNull();
  });

  it("never adopts a spectator view as a private draft", () => {
    const spectator = reducer(initialState, { type: "server", message: {
      type: "role_view", role: "P1", character_name: "林岚", reset: true,
      opening: "opening", history: [], character_status: null, draft: "should be ignored",
    }});
    expect(spectator.actionDraft).toBe("");
  });

  it("clears player-only state when reassigned to spectator", () => {
    const player = { ...initialState, role: "P1", viewRole: "P1", actionDraft: "secret",
      storyEntries: [{ kind: "action" as const, content: "old" }] };
    const spectator = reducer(player, { type: "server", message: {
      type: "identity_changed", role: null, view_role: "P1", reset: true,
    }});
    expect(spectator.actionDraft).toBe("");
    expect(spectator.storyEntries).toEqual([]);
  });

  it("joined stores the authenticated user and host capability", () => {
    const joined = reducer(initialState, { type: "server", message: {
      type: "joined", protocol_version: 4, user: { id: 17, username: "Alice" },
      role: "P1", view_role: "P1", is_host: true, scenario: "lighthouse",
      resume_token: "token-1",
      roles: [{ id: "P1", name: "林岚" }, { id: "P2", name: "周砚" }],
    }});
    expect(joined.connection).toBe("CONNECTED");
    expect(joined.authUser).toEqual({ id: 17, username: "Alice" });
    expect(joined.isHost).toBe(true);
    expect(joined.role).toBe("P1");
  });

  it("resumed restores connection and identity", () => {
    const resumed = reducer({ ...initialState, connection: "RECONNECTING" }, { type: "server", message: {
      type: "resumed", protocol_version: 4, user: { id: 17, username: "Alice" },
      role: "P1", view_role: "P1", is_host: false,
      scenario: "lighthouse", resume_token: "token-2",
      roles: [{ id: "P1", name: "林岚" }, { id: "P2", name: "周砚" }, { id: "P3", name: "苏禾" }],
    }});
    expect(resumed.connection).toBe("CONNECTED");
    expect(resumed.role).toBe("P1");
    expect(resumed.viewRole).toBe("P1");
    expect(resumed.roles).toHaveLength(3);
    expect(resumed.resumeToken).toBe("token-2");
    expect(resumed.isHost).toBe(false);
  });

  it("auth_lost clears the authenticated user", () => {
    const authed = reducer(initialState, { type: "auth", user: { id: 5, username: "Bob" } });
    const lost = reducer(authed, { type: "auth_lost", detail: "登录状态已失效" });
    expect(lost.authUser).toBeNull();
    expect(lost.isHost).toBe(false);
    expect(lost.errors).toContain("登录状态已失效");
  });

  it("session_replaced drops back to the join screen", () => {
    const active = reducer(initialState, { type: "server", message: {
      type: "joined", protocol_version: 4, user: { id: 17, username: "Alice" },
      role: null, view_role: null, is_host: false, scenario: "test",
      resume_token: "t", roles: [{ id: "P1", name: "A" }],
    }});
    const replaced = reducer(active, { type: "server", message: {
      type: "session_replaced", detail: "该账号已在新的连接中接管本局。",
    }});
    expect(replaced.connection).toBe("DISCONNECTED");
    expect(replaced.authUser).toEqual({ id: 17, username: "Alice" });
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
      type: "room_message", kind: "player", sender: "Alice", role: "P1",
      character_name: "林岚", text: "hi",
    }});
    expect(withMessage.chatUnread).toBe(1);
    const onChat = reducer(withMessage, { type: "mobile_tab", tab: "chat" });
    expect(onChat.chatUnread).toBe(0);
  });

  it("ui_config can disable the room key field and registration", () => {
    const configured = reducer(initialState, { type: "ui_config", roomKeyRequired: false, allowRegistration: false,
      roomDisconnectTimeoutSeconds: 300 });
    expect(configured.roomKeyRequired).toBe(false);
    expect(configured.allowRegistration).toBe(false);
    expect(configured.roomDisconnectTimeoutSeconds).toBe(300);
    expect(reducer(initialState, { type: "ui_config", roomKeyRequired: true, allowRegistration: true }).roomKeyRequired).toBe(true);
  });
});
