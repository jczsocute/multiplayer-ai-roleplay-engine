import { afterEach, describe, expect, it, vi } from "vitest";
import { MAX_TEMPLATE_ZIP_BYTES, uploadTemplateZip } from "./templateZip";

afterEach(() => vi.unstubAllGlobals());

describe("Template ZIP upload", () => {
  it("uses the new and replacement endpoints", async () => {
    const fetchMock = vi.fn().mockImplementation(async () => new Response(
      JSON.stringify({ id: "tmpl_A", name: "剧本" }), { status: 201 },
    ));
    vi.stubGlobal("fetch", fetchMock);
    const file = new File(["zip bytes"], "story.zip", { type: "application/zip" });
    expect((await uploadTemplateZip(file)).ok).toBe(true);
    expect((await uploadTemplateZip(file, "tmpl_A")).ok).toBe(true);
    expect(fetchMock.mock.calls[0][0]).toBe("/api/templates/import-zip");
    expect(fetchMock.mock.calls[0][1].method).toBe("POST");
    expect(fetchMock.mock.calls[1][0]).toBe("/api/templates/tmpl_A/zip");
    expect(fetchMock.mock.calls[1][1].method).toBe("PUT");
  });

  it("rejects an oversized file before sending it", async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    const file = { size: MAX_TEMPLATE_ZIP_BYTES + 1 } as File;
    const result = await uploadTemplateZip(file);
    expect(result.ok).toBe(false);
    expect(fetchMock).not.toHaveBeenCalled();
  });
});
