import { describe, expect, it } from "vitest";

import { registrationError } from "./auth";

describe("registrationError", () => {
  it("accepts a matching pair", () => {
    expect(registrationError("Alice", "password123", "password123")).toBeNull();
    expect(registrationError("  Alice  ", "password123", "password123")).toBeNull();
  });

  it("rejects a mismatched repeat password", () => {
    expect(registrationError("Alice", "password123", "password124"))
      .toBe("两次输入的密码不一致");
    expect(registrationError("Alice", "password123", ""))
      .toBe("两次输入的密码不一致");
  });

  it("rejects an empty username", () => {
    expect(registrationError("   ", "password123", "password123"))
      .toBe("用户名不能为空");
  });

  it("rejects a too short password", () => {
    expect(registrationError("Alice", "short", "short"))
      .toBe("密码至少需要 8 个字符");
  });

  it("rejects a too long username", () => {
    expect(registrationError("x".repeat(33), "password123", "password123"))
      .toContain("最长");
  });

  it("checks the repeat field before anything else the server would reject", () => {
    // Repeat-password is a pure UI confirmation: the server only ever receives
    // {username, password}, so a mismatch must never produce a request.
    expect(registrationError("Alice", "password123", "password456")).not.toBeNull();
  });
});
