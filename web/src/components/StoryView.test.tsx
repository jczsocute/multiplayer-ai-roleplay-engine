import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StoryView } from "./StoryView";

describe("optional character status", () => {
  it("shows only story text when the character has no status", () => {
    const html = renderToStaticMarkup(<StoryView
      entries={[{ kind: "narration", content: "海面渐亮", round: 1 }]}
      viewRole="P2" characterName="周砚" opening=""
      spectator={false} roles={[{ id: "P2", name: "周砚" }]}
      viewDisabled={false} onView={() => undefined}
    />);
    expect(html).toContain("海面渐亮");
    expect(html).not.toContain("当前角色视角");
    expect(html).not.toContain("状态 · 第");
  });
  it("shows the round number on a live status entry", () => {
    const html = renderToStaticMarkup(<StoryView
      entries={[{ kind: "character_status", content: { hp: 10 }, round: 1 }]}
      viewRole="P1" characterName="林岚" opening=""
      spectator={false} roles={[{ id: "P1", name: "林岚" }]}
      viewDisabled={false} onView={() => undefined}
    />);
    expect(html).toContain("状态 · 第 1 回合");
    expect(html).not.toContain("状态 · 第 — 回合");
  });
});
