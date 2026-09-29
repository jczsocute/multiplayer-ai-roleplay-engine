import { humanizeError } from "./errors";
import type { ApiResult } from "./api";
import type { TemplateItem } from "./types";

export const MAX_TEMPLATE_ZIP_BYTES = 8 * 1024 * 1024;

export async function uploadTemplateZip(file: File, templateId?: string): Promise<ApiResult<TemplateItem>> {
  if (file.size > MAX_TEMPLATE_ZIP_BYTES) {
    return { ok: false, error: "ZIP 剧本不能超过 8 MB" };
  }
  const url = templateId
    ? `/api/templates/${encodeURIComponent(templateId)}/zip`
    : "/api/templates/import-zip";
  try {
    const response = await fetch(url, {
      method: templateId ? "PUT" : "POST",
      headers: { "Content-Type": "application/zip" }, body: file,
    });
    const data = await response.json() as TemplateItem & { error?: string; detail?: string };
    if (!response.ok) return { ok: false, error: humanizeError(data.error, data.detail) };
    return { ok: true, data };
  } catch {
    return { ok: false, error: "无法连接服务器" };
  }
}

export async function downloadTemplateZip(templateId: string): Promise<string | null> {
  try {
    const response = await fetch(`/api/templates/${encodeURIComponent(templateId)}/zip`);
    if (!response.ok) {
      const data = await response.json() as { error?: string; detail?: string };
      return humanizeError(data.error, data.detail);
    }
    const url = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = url;
    link.download = `${templateId}.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    return null;
  } catch {
    return "下载剧本失败，请稍后重试";
  }
}
