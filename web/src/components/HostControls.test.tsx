import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";
import { HostControlsDialog } from "./HostControls";

describe("owner history export control", () => {
  it("shows an enabled export when idle and a wait notice during processing", () => {
    const props = {
      connection: "CONNECTED", round: 2, ownerUserId: 1,
      roles: [{ id: "P1", name: "角色一" }], users: [],
      send: () => undefined, onClose: () => undefined,
      exportHistory: async () => null,
    };
    const idle = renderToStaticMarkup(<HostControlsDialog {...props} processingStage={null} />);
    expect(idle).toContain("导出历史记录");
    expect(idle).not.toContain("请等待本轮完成");
    const processing = renderToStaticMarkup(<HostControlsDialog {...props} processingStage="WORLD_UPDATING" />);
    expect(processing).toContain("请等待本轮完成……");
    expect(processing).toMatch(/disabled=""[^>]*>导出历史记录/);
  });
});
