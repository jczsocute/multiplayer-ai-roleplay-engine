/** Presentation-layer mapping from backend error codes to Chinese UI text.
 *
 * Deliberately tiny: no i18n framework, no locale files, no translation keys.
 * The UI is Chinese only, and protocol codes stay English on the wire.
 */
const MESSAGES: Record<string, string> = {
  // auth
  unauthorized: "登录状态已失效，请重新登录",
  unauthenticated: "登录状态已失效，请重新登录",
  forbidden: "你没有权限执行此操作",
  invalid_credentials: "用户名或密码错误",
  username_exists: "用户名已存在",
  user_not_found: "用户不存在",
  invalid_account: "账号信息无效：用户名已被使用，或密码少于 8 个字符",
  registration_disabled: "当前未开放注册，请使用已有账号登录",
  invalid_request: "请求内容无效",
  internal_error: "服务器处理失败，请稍后重试",
  disconnected: "与服务器的连接已断开",
  // rooms
  invalid_room_password: "房间密码错误",
  room_password_required: "请输入房间密码",
  invalid_room_password_format: "房间密码只能包含 1–32 位字母、数字或下划线",
  room_not_found: "房间不存在或已关闭",
  already_in_room: "请先离开当前房间",
  owner_already_has_room: "你已经主持了一个活跃房间",
  game_already_active: "该存档已经在另一个房间中运行",
  invalid_source: "请选择存档或剧本",
  room_full: "房间人数已满",
  cannot_kick_owner: "房主不能将自己踢出房间",
  user_not_in_room: "该用户已不在房间中",
  action_too_long: "行动内容过长",
  empty_chat_message: "聊天消息不能为空",
  chat_message_too_long: "聊天消息过长",
  invalid_retry_round: "重试需要有效的回合编号",
  invalid_rollback_round: "回滚需要有效的回合编号",
  role_reassign_not_paused: "重新分配前，已占用角色必须暂停",
  invalid_json_object: "消息必须是 JSON 对象",
  unknown_role: "未知角色",
  roles_not_assigned: "角色尚未分配",
  world_update_failed: "世界更新失败，房主可以重试本回合",
  player_view_failed: "角色视角生成失败，房主可以重试本回合",
  // catalog
  game_not_found: "存档不存在",
  template_not_found: "剧本不存在",
  template_not_owned: "你没有权限修改该剧本",
  game_is_active: "该存档正在房间中使用",
  invalid_game_name: "存档名称无效（1–60 个字符）",
  invalid_template_name: "剧本名称无效（1–60 个字符）",
  // admin
  account_not_found: "账号不存在",
  user_owns_resources: "该账号仍拥有剧本 / 存档 / 活跃房间，无法删除",
  unknown_command: "未知命令",
  forbidden_origin: "来源不被允许",
};

/** A machine-ish code: lowercase snake_case, no spaces or punctuation. */
const CODE_LIKE = /^[a-z][a-z0-9_]*$/;

export function humanizeError(code?: string | null, detail?: string | null): string {
  if (code && MESSAGES[code]) return MESSAGES[code];
  const text = (detail ?? "").trim();
  // The game server reports some conditions as a bare code in `detail`.
  if (text && MESSAGES[text]) return MESSAGES[text];
  // A natural server detail (already Chinese) is passed through; a bare machine
  // code never is.
  if (text && !CODE_LIKE.test(text)) return text;
  return "操作失败，请稍后重试";
}
