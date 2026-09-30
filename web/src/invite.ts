export function inviteLink(origin: string, code: string): string {
  return `${origin}/?room=${encodeURIComponent(code.toUpperCase())}`;
}

export function invitedRoom(search: string): string | null {
  const code = new URLSearchParams(search).get("room")?.trim().toUpperCase();
  return code && /^[A-Z0-9]{6,8}$/.test(code) ? code : null;
}
