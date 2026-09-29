import { renderToStaticMarkup } from "react-dom/server";
import { afterEach, describe, expect, it, vi } from "vitest";
import { apiGet, apiPut } from "../api";
import type { TemplateEditorData } from "../types";
import { parseEditorTags, resizeCharacters, TemplateEditor } from "./TemplateEditor";

const data: TemplateEditorData = {
  id: "tmpl_A", title: "旧标题", introduction: "简介", tags: ["双人"],
  world: "旧世界", ai_guidelines: "写作要求",
  characters: [
    { index: 1, character: "角色一", opening: "开场一" },
    { index: 2, character: "角色二", opening: "开场二" },
  ],
};

afterEach(() => vi.unstubAllGlobals());

describe("Template Basic Editor", () => {
  it("keeps existing characters and adds blank numbered tail roles", () => {
    const added = resizeCharacters(data.characters, 4);
    expect(added.slice(0, 2)).toEqual(data.characters);
    expect(added.slice(2)).toEqual([
      { index: 3, character: "", opening: "" },
      { index: 4, character: "", opening: "" },
    ]);
    expect(resizeCharacters(added, 2)).toEqual(data.characters);
    expect(parseEditorTags("悬疑， 双人, 冒险\n短篇")).toEqual(["悬疑", "双人", "冒险", "短篇"]);
  });

  it("loads and submits the edited payload through the editor API", async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(new Response(JSON.stringify(data), { status: 200 }))
      .mockResolvedValueOnce(new Response(JSON.stringify({ ...data, world: "新世界" }), { status: 200 }));
    vi.stubGlobal("fetch", fetchMock);
    const loaded = await apiGet<TemplateEditorData>("/api/templates/tmpl_A/editor");
    expect(loaded.ok).toBe(true);
    if (!loaded.ok) return;
    const updated = { ...loaded.data, world: "新世界", characters: resizeCharacters(loaded.data.characters, 3) };
    const saved = await apiPut<TemplateEditorData>("/api/templates/tmpl_A/editor", updated);
    expect(saved.ok).toBe(true);
    expect(fetchMock).toHaveBeenNthCalledWith(2, "/api/templates/tmpl_A/editor", {
      method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(updated),
    });
  });

  it("has a dedicated loading page while fetching", () => {
    const html = renderToStaticMarkup(<TemplateEditor templateId="tmpl_A"
      roleCounts={[2, 3, 4]} onBack={() => undefined} onSaved={async () => undefined} />);
    expect(html).toContain("编辑剧本");
    expect(html).toContain("正在加载剧本");
  });
});
