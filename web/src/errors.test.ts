import { describe, expect, it } from "vitest";

import { humanizeError } from "./errors";

/** Every code the backend can put on the wire, as of this round. */
const BACKEND_CODES = [
  // platform HTTP / WS
  "unauthorized", "unauthenticated", "forbidden", "invalid_credentials",
  "username_exists", "invalid_account", "registration_disabled", "invalid_request",
  "internal_error", "disconnected", "forbidden_origin",
  "invalid_room_password", "room_password_required", "invalid_room_password_format",
  "room_not_found", "already_in_room", "owner_already_has_room", "game_already_active",
  "room_full", "cannot_kick_owner", "user_not_in_room", "invalid_source",
  // catalog
  "game_not_found", "template_not_found", "template_not_owned", "game_is_active",
  "invalid_game_name", "invalid_template_name", "user_not_found",
  // admin
  "account_not_found", "user_owns_resources", "unknown_command",
  "action_too_long", "empty_chat_message", "chat_message_too_long",
  "invalid_retry_round", "invalid_rollback_round", "role_reassign_not_paused",
  "invalid_json_object", "unknown_role", "roles_not_assigned",
  "world_update_failed", "player_view_failed",
];

const CHINESE = /[\u4e00-\u9fff]/;

describe("humanizeError", () => {
  it("maps every backend code to Chinese text", () => {
    for (const code of BACKEND_CODES) {
      const text = humanizeError(code);
      expect(text, code).not.toBe(code);
      expect(text, code).toMatch(CHINESE);
    }
  });

  it("uses the 剧本 wording for catalog errors", () => {
    expect(humanizeError("template_not_found")).toBe("剧本不存在");
    expect(humanizeError("template_not_owned")).toBe("你没有权限修改该剧本");
    expect(humanizeError("invalid_template_name")).toBe("剧本名称无效（1–60 个字符）");
    expect(humanizeError("invalid_source")).toBe("请选择存档或剧本");
  });

  it("maps the documented room / auth / catalog codes", () => {
    expect(humanizeError("invalid_room_password")).toBe("房间密码错误");
    expect(humanizeError("room_password_required")).toBe("请输入房间密码");
    expect(humanizeError("room_not_found")).toBe("房间不存在或已关闭");
    expect(humanizeError("already_in_room")).toBe("请先离开当前房间");
    expect(humanizeError("room_full")).toBe("房间人数已满");
    expect(humanizeError("game_not_found")).toBe("存档不存在");
    expect(humanizeError("game_is_active")).toBe("该存档正在房间中使用");
    expect(humanizeError("cannot_kick_owner")).toBe("房主不能将自己踢出房间");
    expect(humanizeError("user_not_in_room")).toBe("该用户已不在房间中");
    expect(humanizeError("forbidden")).toBe("你没有权限执行此操作");
    expect(humanizeError("unauthorized")).toBe("登录状态已失效，请重新登录");
    expect(humanizeError("user_not_found")).toBe("用户不存在");
    expect(humanizeError("username_exists")).toBe("用户名已存在");
    expect(humanizeError("invalid_credentials")).toBe("用户名或密码错误");
  });

  it("never shows a bare machine code", () => {
    for (const code of [...BACKEND_CODES, "unknown_code", "some_new_code"]) {
      // The code may arrive as the code, as the detail, or in both fields.
      for (const text of [
        humanizeError(code),
        humanizeError(code, code),
        humanizeError(undefined, code),
      ]) {
        expect(text, code).not.toBe(code);
        expect(text, code).toMatch(CHINESE);
      }
    }
  });

  it("maps a code that arrived in the detail field", () => {
    // The game server reports some conditions as a bare code in `detail`.
    expect(humanizeError(undefined, "cannot_kick_owner")).toBe("房主不能将自己踢出房间");
    expect(humanizeError(undefined, "user_not_in_room")).toBe("该用户已不在房间中");
  });

  it("prefers the mapped message over a raw detail", () => {
    expect(humanizeError("room_password_required", "room_password_required"))
      .toBe("请输入房间密码");
  });

  it("falls back to a natural server detail when the code is unknown", () => {
    expect(humanizeError(undefined, "服务器内部错误")).toBe("服务器内部错误");
    expect(humanizeError("mystery", "剧本名称不能为空")).toBe("剧本名称不能为空");
    expect(humanizeError(undefined, "世界更新失败，可以修改行动后重新提交"))
      .toBe("世界更新失败，可以修改行动后重新提交");
  });

  it("falls back to a generic message when nothing usable is available", () => {
    expect(humanizeError()).toBe("操作失败，请稍后重试");
    expect(humanizeError("mystery")).toBe("操作失败，请稍后重试");
    expect(humanizeError("mystery", "   ")).toBe("操作失败，请稍后重试");
  });
});
