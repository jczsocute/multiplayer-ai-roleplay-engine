import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { CreateRoomForm } from "./CreateRoomForm";
import { MyGamesPanel } from "./MyGamesPanel";
import { MyTemplatesPanel } from "./MyTemplatesPanel";
import { PlatformHome } from "./PlatformHome";
import { TemplateDetailPanel } from "./TemplateDetailPanel";
import type { RoomItem, TemplateItem } from "../types";

const template: TemplateItem = {
  id: "tmpl_A", name: "森林之夜", owner_user_id: 1, owner_username: "Alice",
  is_public: true, role_count: 2, role_names: ["林岚", "周砚"],
  introduction: "一段介绍", tags: ["情感"], updated_at: "2026-09-28T10:00:00Z",
};

const noop = () => undefined;
const asyncNoop = async () => undefined;

const home = (
  available: TemplateItem[] = [template],
  publicTemplates: TemplateItem[] = [template],
  currentRoom: RoomItem | null = null,
) => renderToStaticMarkup(
  <PlatformHome
    userId={1} username="Alice" roleCounts={[2, 3, 4]}
    templates={available} publicTemplates={publicTemplates}
    myTemplates={available} games={[]} rooms={currentRoom ? [currentRoom] : []}
    currentRoom={currentRoom}
    recoveryFailedRoom={null}
    error="" notice="" busy={false}
    onLogout={noop} onReturnCurrent={noop} onLeaveCurrent={asyncNoop}
    onRefreshRooms={asyncNoop} onCreate={async () => true}
    onJoin={noop} onCloseRoom={asyncNoop} onSearch={async () => null}
    onRenameGame={asyncNoop} onCopyGame={asyncNoop} onDeleteGame={asyncNoop}
    onExportGameHistory={asyncNoop}
    onCreateTemplate={asyncNoop} onRenameTemplate={asyncNoop}
    onToggleTemplateVisibility={asyncNoop} onLoadTemplateDetail={async () => template}
    onEditTemplate={noop}
    onImportTemplateZip={noop} onExportTemplateZip={noop}
    onCopyTemplate={asyncNoop} onDeleteTemplate={asyncNoop} />,
);

/** User-facing Chinese must say 剧本, never 模板. */
function expectScriptWording(html: string, ...expected: string[]) {
  for (const label of expected) expect(html).toContain(label);
  expect(html).not.toContain("模板");
}

describe("user-facing Template wording", () => {
  it("offers a return and leave action for a retained full-room membership", () => {
    const room: RoomItem = {
      code: "AB12", game_id: "game_A", game_name: "Save",
      owner_user_id: 2, owner_username: "Bob", role_count: 2,
      connected_count: 9, occupancy: 10, max_users: 10, has_password: true,
    };
    const html = home([template], [template], room);
    expect(html).toContain("你当前仍在Save_A");
    expect(html).toContain("返回房间");
    expect(html).toContain("离开房间");
    expect(html).not.toContain("已满</button>");
  });
  it("labels the lobby entries and the plaza as 剧本", () => {
    expectScriptWording(
      home(), "我的剧本", "剧本广场", "创建房间", "加入房间", "我的存档",
    );
  });

  it("shows only public Scripts in 剧本广场", () => {
    const privateScript: TemplateItem = {
      ...template, id: "tmpl_PRIVATE", name: "我的私密剧本", is_public: false,
    };
    const publicScript: TemplateItem = {
      ...template, id: "tmpl_PUBLIC", name: "别人的公开剧本",
      owner_username: "Bob", is_public: true,
    };
    // The Create Room selector source (own + public) still contains the private
    // Script, but the plaza must not list it.
    const html = home([privateScript, publicScript], [publicScript]);
    expect(html).toContain("剧本广场");
    expect(html).toContain("别人的公开剧本");
    expect(html).not.toContain("我的私密剧本");
    // Exactly one plaza row was rendered.
    expect((html.match(/row-link/g) ?? [])).toHaveLength(1);
  });

  it("keeps a private Script usable when creating a room", () => {
    const privateScript: TemplateItem = {
      ...template, id: "tmpl_PRIVATE", name: "我的私密剧本", is_public: false,
    };
    expect(renderToStaticMarkup(
      <CreateRoomForm games={[]} templates={[privateScript]} busy={false} error=""
        onSubmit={asyncNoop} onClose={noop} />,
    )).toContain("我的私密剧本");
  });

  it("creates rooms 从剧本开始", () => {
    expectScriptWording(
      renderToStaticMarkup(
        <CreateRoomForm games={[]} templates={[template]} busy={false} error=""
          onSubmit={asyncNoop} onClose={noop} />,
      ),
      "从剧本开始", "选择剧本",
    );
  });

  it("points 我的存档 at 从剧本开始", () => {
    expectScriptWording(
      renderToStaticMarkup(
        <MyGamesPanel games={[]} busy={false} error="" onLoad={noop}
          onRename={noop} onCopy={noop} onDelete={noop} onExportHistory={noop} />,
      ),
      "从剧本开始",
    );
  });

  it("uses 剧本 in 我的剧本", () => {
    expectScriptWording(
      renderToStaticMarkup(
        <MyTemplatesPanel templates={[template]} roleCounts={[2, 3, 4]} busy={false}
          error="" onUse={noop} onDetail={noop} onEdit={noop}
          onImportZip={noop} onExportZip={noop} onRename={noop} onCopy={noop}
          onDelete={noop} onCreate={noop} onToggleVisibility={noop} />,
      ),
      "新建", "剧本角色数",
    );
  });

  it("uses 剧本 in the detail view", () => {
    expectScriptWording(
      renderToStaticMarkup(<TemplateDetailPanel template={template} onUse={noop} />),
      "剧本介绍", "使用剧本", "剧本角色数",
    );
  });

  it("keeps internal Template identifiers untouched", () => {
    // The wire/API names are not part of the wording change.
    const html = renderToStaticMarkup(
      <TemplateDetailPanel template={template} onUse={noop} />,
    );
    expect(html).not.toContain("template_id");
    expect(template.id.startsWith("tmpl_")).toBe(true);
  });
});
