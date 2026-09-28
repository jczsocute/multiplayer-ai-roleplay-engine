import { describe, expect, it } from "vitest";

import {
  defaultRollbackRound,
  hostControlsDisabled,
  retryConfirmation,
  retryMessage,
  rollbackConfirmation,
  rollbackMessage,
  rollbackRoundInput,
  parseRollbackRound,
  shouldShowHostControls,
} from "./host";
import { initialState, reducer } from "./state";

describe("host controls", () => {
  it("is only offered to the owner", () => {
    expect(shouldShowHostControls(true)).toBe(true);
    expect(shouldShowHostControls(false)).toBe(false);
  });

  it("is disabled while disconnected or processing", () => {
    expect(hostControlsDisabled({ connection: "CONNECTED", processingStage: null })).toBe(false);
    expect(hostControlsDisabled({ connection: "CONNECTED", processingStage: "WORLD_UPDATING" })).toBe(true);
    expect(hostControlsDisabled({ connection: "RECONNECTING", processingStage: null })).toBe(true);
  });

  it("builds the retry and rollback messages", () => {
    expect(retryMessage()).toEqual({ type: "retry" });
    expect(rollbackMessage(4)).toEqual({ type: "rollback", round: 4 });
  });

  it("parses a rollback round", () => {
    expect(parseRollbackRound("4")).toBe(4);
    expect(parseRollbackRound(" 12 ")).toBe(12);
    expect(parseRollbackRound("")).toBeNull();
    expect(parseRollbackRound("0")).toBeNull();
    expect(parseRollbackRound("-1")).toBeNull();
    expect(parseRollbackRound("4.5")).toBeNull();
    expect(parseRollbackRound("abc")).toBeNull();
  });

  it("defaults the target round to the previous round", () => {
    expect(defaultRollbackRound(5)).toBe(4);
    expect(defaultRollbackRound(2)).toBe(1);
    expect(rollbackRoundInput(5)).toBe("4");
    expect(rollbackRoundInput(2)).toBe("1");
  });

  it("has no default target before anything is completed", () => {
    expect(defaultRollbackRound(1)).toBeNull();
    expect(defaultRollbackRound(null)).toBeNull();
    expect(rollbackRoundInput(1)).toBe("");
    expect(rollbackRoundInput(null)).toBe("");
  });

  it("keeps a defaulted target valid for the rollback message", () => {
    expect(parseRollbackRound(rollbackRoundInput(4))).toBe(3);
  });

  it("explains the destructive actions", () => {
    expect(retryConfirmation()).toContain("重新生成上一回合");
    expect(retryConfirmation()).toContain("清空");
    expect(rollbackConfirmation(4)).toContain("第 4 回合");
    expect(rollbackConfirmation(4)).toContain("永久删除");
  });
});

describe("owner state", () => {
  it("keeps the server-computed host flag for retry/rollback", () => {
    const state = reducer(initialState, {
      type: "server",
      message: {
        type: "joined",
        protocol_version: 6,
        user: { id: 1, username: "Alice" },
        role: "P1",
        view_role: "P1",
        is_host: true,
        scenario: "test",
        resume_token: "token",
        roles: [{ id: "P1", name: "角色1" }, { id: "P2", name: "角色2" }],
      },
    });
    expect(state.isHost).toBe(true);
    expect(state.role).toBe("P1");
  });

  it("surfaces server errors from a rejected retry/rollback", () => {
    const state = reducer(initialState, {
      type: "server",
      message: { type: "error", detail: "forbidden: only the game owner can manage the timeline" },
    });
    expect(state.errors.at(-1)).toContain("forbidden");
  });
});
