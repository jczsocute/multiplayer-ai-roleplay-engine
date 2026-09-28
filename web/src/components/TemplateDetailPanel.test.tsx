import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { TemplateDetailPanel } from "./TemplateDetailPanel";
import type { TemplateItem } from "../types";

const template = (overrides: Partial<TemplateItem> = {}): TemplateItem => ({
  id: "tmpl_A",
  name: "森林之夜",
  owner_user_id: 1,
  owner_username: "Alice",
  is_public: true,
  role_count: 2,
  role_names: ["林岚", "周砚"],
  introduction: "两位主角在山间旅馆重逢，需要在雨夜中面对旧日误会。",
  tags: ["情感", "双人"],
  updated_at: new Date(2026, 8, 28, 18, 42).toISOString(),
  ...overrides,
});

const render = (item: TemplateItem) =>
  renderToStaticMarkup(<TemplateDetailPanel template={item} onUse={() => undefined} />);

describe("TemplateDetailPanel", () => {
  it("shows the payload introduction", () => {
    const html = render(template());
    expect(html).toContain("剧本介绍");
    expect(html).toContain("两位主角在山间旅馆重逢");
    expect(html).toContain('class="detail-text"');
  });

  it("hides the introduction section when it is empty", () => {
    const html = render(template({ introduction: "" }));
    expect(html).not.toContain("剧本介绍");
    expect(html).not.toContain("暂无简介");
  });

  it("renders one chip per tag", () => {
    const html = render(template({ tags: ["情感", "双人", "现代都市"] }));
    expect(html.match(/class="tag-chip"/g)).toHaveLength(3);
    for (const tag of ["情感", "双人", "现代都市"]) expect(html).toContain(tag);
  });

  it("shows no tag area at all when there are none", () => {
    const html = render(template({ tags: [] }));
    expect(html).not.toContain("tag-list");
    expect(html).not.toContain("暂无标签");
    const missing = render(template({ tags: undefined }));
    expect(missing).not.toContain("tag-list");
  });

  it("lists roles as derived P1..PN names", () => {
    const html = render(template({ role_count: 2, role_names: ["林岚", "周砚"] }));
    expect(html).toContain("角色（2）");
    expect(html).toContain('class="role-id">P1</span> · 林岚');
    expect(html).toContain('class="role-id">P2</span> · 周砚');
  });

  it("keeps the author / role count / update time line", () => {
    const html = render(template());
    expect(html).toContain("作者：Alice · 剧本角色数 2 · 更新时间 2026-09-28 18:42");
  });

  it("offers the compact 使用剧本 button", () => {
    expect(render(template())).toContain('<button class="compact-button">使用剧本</button>');
  });

  it("survives a template without payload metadata", () => {
    const html = render(template({
      introduction: undefined, tags: undefined, role_names: undefined, role_count: undefined,
    }));
    expect(html).toContain("剧本角色数 0");
    expect(html).toContain("使用剧本");
    expect(html).not.toContain("tag-list");
  });
});
