import { describe, expect, it } from "vitest";

import {
  createRoomPayload,
  mayManageRoom,
  normalizeRoomCode,
  roomNeedsPassword,
  validRoomPassword,
} from "./platform";

const room = { code: "K7Q9MX", has_password: true, owner_user_id: 17 };

describe("platform lobby helpers", () => {
  it("normalizes exact room-code searches", () => {
    expect(normalizeRoomCode(" k7q9mx ")).toBe("K7Q9MX");
  });

  it("shows password and owner controls from public metadata", () => {
    expect(roomNeedsPassword(room)).toBe(true);
    expect(mayManageRoom(room, 17)).toBe(true);
    expect(mayManageRoom(room, 23)).toBe(false);
  });

  it("validates optional room passwords", () => {
    expect(validRoomPassword("")).toBe(true);
    expect(validRoomPassword("abc_123")).toBe(true);
    expect(validRoomPassword("bad password")).toBe(false);
    expect(validRoomPassword("x".repeat(33))).toBe(false);
  });

  it("builds both room creation sources", () => {
    expect(createRoomPayload("game", "game_A", "")).toEqual({
      source: "game", game_id: "game_A", password: "",
    });
    expect(createRoomPayload("template", "tmpl_A", "abc", "新存档")).toEqual({
      source: "template", template_id: "tmpl_A", game_name: "新存档", password: "abc",
    });
  });
});
