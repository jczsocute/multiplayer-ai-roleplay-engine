import { describe, expect, it } from "vitest";

import { humanizeError } from "./errors";
import {
  LOBBY_TITLE, MAX_LOBBY_ROWS, copyConfirmation, deleteConfirmation, formatLocalTime,
  isOwnTemplate, newTemplatePayload, ownTemplates, pickRandom, renamePrompt,
  roomGameName, roomIsFull, roomLabel, roomOccupancy, sortedByUpdated,
  templateDetailLine, templateMeta, templateOwnerLabel,
  templateRoleCount, toggledRow, visibilityToggleLabel,
} from "./lobby";
import type { GameItem, RoomItem, TemplateItem } from "./types";

const room = (overrides: Partial<RoomItem> = {}): RoomItem => ({
  code: "K7Q9MX",
  game_id: "game_A",
  game_name: "Save",
  owner_username: "Alice",
  owner_user_id: 1,
  role_count: 2,
  connected_count: 3,
  occupancy: 3,
  max_users: 10,
  has_password: false,
  ...overrides,
});

const template = (overrides: Partial<TemplateItem> = {}): TemplateItem => ({
  id: "tmpl_A",
  name: "我的故事",
  owner_user_id: 1,
  owner_username: "Alice",
  is_public: false,
  updated_at: "2026-09-28T10:00:00+00:00",
  role_count: 2,
  role_names: ["林岚", "周砚"],
  ...overrides,
});

describe("active room rows", () => {
  it("names a room after its owner, never the game id", () => {
    expect(roomLabel(room())).toBe("Alice 的房间");
    expect(roomLabel(room({ owner_username: "" }))).toBe("房间");
  });

  it("shows occupancy against the fixed capacity", () => {
    expect(roomOccupancy(room())).toBe("3/10");
    expect(roomOccupancy(room({ max_users: undefined }))).toBe("3/10");
    expect(roomOccupancy(room({ occupancy: 10 }))).toBe("10/10");
  });

  it("falls back to connected_count when occupancy is missing", () => {
    expect(roomOccupancy(room({ occupancy: undefined, connected_count: 4 }))).toBe("4/10");
  });

  it("marks a room full at capacity so the join button can disable", () => {
    expect(roomIsFull(room({ occupancy: 9 }))).toBe(false);
    expect(roomIsFull(room({ occupancy: 10 }))).toBe(true);
    expect(roomIsFull(room({ occupancy: 10 }), "K7Q9MX")).toBe(false);
  });

  it("keeps the gamename as secondary text only when it says something", () => {
    expect(roomGameName(room())).toBe("Save");
    expect(roomGameName(room({ game_name: "K7Q9MX" }))).toBe("");
    expect(roomGameName(room({ game_name: "" }))).toBe("");
  });
});

describe("resource rows", () => {
  it("expands one row at a time", () => {
    expect(toggledRow(null, "a")).toBe("a");
    expect(toggledRow("a", "b")).toBe("b");
    expect(toggledRow("a", "a")).toBeNull();
  });

  it("formats the API timestamp in local time", () => {
    const iso = new Date(2026, 8, 28, 18, 42).toISOString();
    expect(formatLocalTime(iso)).toBe("2026-09-28 18:42");
    expect(formatLocalTime(null)).toBe("");
    expect(formatLocalTime("not-a-date")).toBe("");
  });

  it("sorts by last update, newest first", () => {
    const items: GameItem[] = [
      { id: "a", name: "old", owner_user_id: 1, updated_at: "2026-09-01T00:00:00Z" },
      { id: "b", name: "new", owner_user_id: 1, updated_at: "2026-09-28T00:00:00Z" },
    ];
    expect(sortedByUpdated(items).map((item) => item.id)).toEqual(["b", "a"]);
    // The input array is not mutated.
    expect(items.map((item) => item.id)).toEqual(["a", "b"]);
  });

  it("lists only my own templates in 我的剧本", () => {
    const mine = template();
    const other = template({ id: "tmpl_B", owner_user_id: 2, owner_username: "Bob" });
    expect(ownTemplates([mine, other], 1).map((value) => value.id)).toEqual(["tmpl_A"]);
    expect(isOwnTemplate(mine, 1)).toBe(true);
    expect(isOwnTemplate(other, 1)).toBe(false);
  });

  it("describes a template without inventing a description", () => {
    expect(templateOwnerLabel(template())).toBe("作者：Alice");
    expect(templateMeta(template())).toBe("剧本角色数 2 · 私有");
    expect(templateMeta(template({ is_public: true }))).toBe("剧本角色数 2 · 公开");
    expect(templateMeta(template({ role_count: undefined, role_names: [] })))
      .toBe("剧本角色数 0 · 私有");
    expect(templateRoleCount(template({ role_count: undefined, role_names: ["a"] })))
      .toBe(1);
  });

  it("offers the opposite visibility in the toggle label", () => {
    expect(visibilityToggleLabel(template({ is_public: false }))).toBe("切换为公开");
    expect(visibilityToggleLabel(template({ is_public: true }))).toBe("切换为私密");
  });

  it("keeps the detail line to author, role count and update time", () => {
    const line = templateDetailLine(template({ updated_at: new Date(2026, 8, 28, 18, 42).toISOString() }));
    expect(line).toBe("作者：Alice · 剧本角色数 2 · 更新时间 2026-09-28 18:42");
    expect(templateDetailLine(template({ owner_username: undefined })))
      .toContain("作者：未知");
    expect(templateDetailLine(template({ updated_at: undefined })))
      .toContain("更新时间 未知");
    expect(line).not.toContain("公开");
  });
});

describe("confirmations", () => {
  it("asks before copying and deleting", () => {
    expect(copyConfirmation("存档", "love_story")).toContain("love_story");
    expect(deleteConfirmation("剧本", "我的故事", "已用它创建的存档不受影响。"))
      .toContain("已用它创建的存档不受影响。");
    expect(renamePrompt("存档", "love_story")).toContain("love_story");
  });

  it("trims the new-template payload and keeps the role count", () => {
    expect(newTemplatePayload("  我的故事  ", 3))
      .toEqual({ name: "我的故事", role_count: 3 });
  });
});

describe("lobby lists", () => {
  const items = (count: number) => Array.from({ length: count }, (_, index) => index + 1);

  it("uses a Chinese lobby title and a five-row cap", () => {
    expect(LOBBY_TITLE).toBe("AI 角色扮演引擎");
    expect(MAX_LOBBY_ROWS).toBe(5);
  });

  it("shows every entry when the pool is short", () => {
    expect(pickRandom(items(3), 5)).toEqual([1, 2, 3]);
    expect(pickRandom(items(5), 5)).toEqual([1, 2, 3, 4, 5]);
  });

  it("picks at most five distinct entries from a long pool", () => {
    const picked = pickRandom(items(20), 5);
    expect(picked).toHaveLength(5);
    expect(new Set(picked).size).toBe(5);
    for (const value of picked) expect(items(20)).toContain(value);
  });

  it("re-picks from the pool on every call", () => {
    const pool = items(20);
    const first = pickRandom(pool, 5, () => 0);
    const second = pickRandom(pool, 5, () => 0.99);
    expect(first).toEqual([1, 2, 3, 4, 5]);
    expect(second).toEqual([20, 19, 18, 17, 16]);
    expect(pool).toHaveLength(20); // the pool is never mutated
  });

  it("handles empty pools and non-positive counts", () => {
    expect(pickRandom([], 5)).toEqual([]);
    expect(pickRandom(items(4), 0)).toEqual([]);
    expect(pickRandom(items(4), -1)).toEqual([]);
  });

  it("orders 我的存档 / 我的剧本 newest first", () => {
    const games: GameItem[] = [
      { id: "old", name: "old", owner_user_id: 1, updated_at: "2026-09-01T00:00:00Z" },
      { id: "new", name: "new", owner_user_id: 1, updated_at: "2026-09-28T00:00:00Z" },
      { id: "mid", name: "mid", owner_user_id: 1, updated_at: "2026-09-14T00:00:00Z" },
    ];
    expect(sortedByUpdated(games).map((game) => game.id)).toEqual(["new", "mid", "old"]);
  });
});

describe("lobby error messages", () => {
  it("explains the new lifecycle errors in Chinese", () => {
    expect(humanizeError("room_full")).toBe("房间人数已满");
    expect(humanizeError("game_is_active")).toBe("该存档正在房间中使用");
    expect(humanizeError("game_not_found")).toBe("存档不存在");
    expect(humanizeError("template_not_owned")).toBe("你没有权限修改该剧本");
    expect(humanizeError("cannot_kick_owner")).toBe("房主不能将自己踢出房间");
    expect(humanizeError("user_not_in_room")).toBe("该用户已不在房间中");
  });
});
