import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { initialState } from "../state";
import { GameScreen } from "./GameScreen";

describe("GameScreen reconnect notice", () => {
  it("uses the timeout supplied by UI config", () => {
    const html = renderToStaticMarkup(<GameScreen
      state={{ ...initialState, connection: "RECONNECTING", roomDisconnectTimeoutSeconds: 300 }}
      send={() => undefined} setAction={() => undefined} setChat={() => undefined}
      sendChat={() => undefined} leave={() => undefined} setTab={() => undefined}
    />);
    expect(html).toContain("超过 300 秒");
    expect(html).not.toContain("超过 60 秒");
  });
});
