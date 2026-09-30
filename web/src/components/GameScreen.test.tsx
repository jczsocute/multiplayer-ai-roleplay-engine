import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { initialState } from "../state";
import { GameScreen } from "./GameScreen";

describe("GameScreen reconnect notice", () => {
  it("uses the viewed assigned role for the composer", () => {
    const html = renderToStaticMarkup(<GameScreen
      state={{ ...initialState, connection: "CONNECTED", assignedRoles: ["P1", "P2"],
        viewRole: "P2", actionDrafts: { P1: "打开门", P2: "守住门口" },
        roles: [{ id: "P1", name: "路人甲" }, { id: "P2", name: "路人乙" }, { id: "P3", name: "路人丙" }],
        players: { P1: { status: "READY", has_action: true, connected: true },
          P2: { status: "EDITING", has_action: true, connected: true } } }}
      send={() => undefined} setAction={() => undefined} setChat={() => undefined}
      sendChat={() => undefined} leave={() => undefined} setTab={() => undefined}
    />);
    expect(html).toContain("守住门口</textarea>");
    expect(html).not.toContain("打开门</textarea>");
    expect(html).toContain("提交行动");
  });
  it("renders the room display name supplied by the server", () => {
    const html = renderToStaticMarkup(<GameScreen
      state={{ ...initialState, scenario: "石头剪刀布_6LQFHV" }}
      send={() => undefined} setAction={() => undefined} setChat={() => undefined}
      sendChat={() => undefined} leave={() => undefined} setTab={() => undefined}
    />);
    expect(html).toContain("石头剪刀布_6LQFHV");
  });

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
