import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { StoryView } from "./StoryView";

describe("optional character status", () => {
  it("shows only assigned role tabs for a multi-role player", () => {
    const html = renderToStaticMarkup(<StoryView entries={[]} viewRole="P2"
      characterName="路人乙" opening="" spectator={false}
      roles={[{ id: "P1", name: "路人甲" }, { id: "P2", name: "路人乙" }, { id: "P3", name: "路人丙" }]}
      viewRoles={[{ id: "P1", name: "路人甲" }, { id: "P2", name: "路人乙" }]}
      viewDisabled={false} onView={() => undefined} />);
    expect(html).toContain("路人甲</button>");
    expect(html).toContain("路人乙</button>");
    expect(html).not.toContain("路人丙</button>");
    expect(html).toContain('class="active" aria-pressed="true"');
  });
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
