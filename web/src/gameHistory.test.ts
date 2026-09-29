import { afterEach, describe, expect, it, vi } from "vitest";
import { downloadGameHistory } from "./gameHistory";

afterEach(() => vi.unstubAllGlobals());

describe("game history download", () => {
  it("uses the game and room endpoints and explains processing rejection", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response(
      JSON.stringify({ error: "game_processing" }), { status: 409 },
    ));
    vi.stubGlobal("fetch", fetchMock);
    expect(await downloadGameHistory("game", "game_A")).toBe("请等待本轮完成……");
    expect(await downloadGameHistory("room", "ROOM1")).toBe("请等待本轮完成……");
    expect(fetchMock.mock.calls[0][0]).toBe("/api/games/game_A/history.zip");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/rooms/ROOM1/history.zip");
  });
});
