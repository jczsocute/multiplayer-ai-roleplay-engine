/** One automatic announcement per account in this browser. */
export function markAnnouncementSeen(userId: number, storage: Pick<Storage, "getItem" | "setItem">): boolean {
  const key = `ai-rp-announcement-seen:${userId}`;
  if (storage.getItem(key)) return false;
  storage.setItem(key, "1");
  return true;
}
