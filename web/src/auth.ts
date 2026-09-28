import { MIN_PASSWORD_LENGTH, MAX_USERNAME_LENGTH } from "./protocol";

/** Client-side registration validation.
 *
 * The repeat-password field is a UI confirmation only: it is never sent to the
 * server, which keeps `POST /api/register` at `{username, password}`.
 */
export function registrationError(
  username: string,
  password: string,
  repeated: string,
): string | null {
  const name = username.trim();
  if (!name) return "用户名不能为空";
  if (name.length > MAX_USERNAME_LENGTH) return `用户名最长为 ${MAX_USERNAME_LENGTH} 个字符`;
  if (password.length < MIN_PASSWORD_LENGTH) {
    return `密码至少需要 ${MIN_PASSWORD_LENGTH} 个字符`;
  }
  if (password !== repeated) return "两次输入的密码不一致";
  return null;
}
