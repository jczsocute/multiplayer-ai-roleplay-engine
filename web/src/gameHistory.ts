import { humanizeError } from "./errors";

/** Download the owner's completed story history as JSON files in one ZIP. */
export async function downloadGameHistory(kind: "game" | "room", id: string): Promise<string | null> {
  const url = kind === "game"
    ? `/api/games/${encodeURIComponent(id)}/history.zip`
    : `/api/rooms/${encodeURIComponent(id)}/history.zip`;
  try {
    const response = await fetch(url);
    if (!response.ok) {
      const body = await response.json() as { error?: string; detail?: string };
      return humanizeError(body.error, body.detail);
    }
    const objectUrl = URL.createObjectURL(await response.blob());
    const link = document.createElement("a");
    link.href = objectUrl;
    link.download = `${id}-history.zip`;
    document.body.appendChild(link);
    link.click();
    link.remove();
    window.setTimeout(() => URL.revokeObjectURL(objectUrl), 1000);
    return null;
  } catch {
    return "历史记录下载失败，请稍后重试";
  }
}
