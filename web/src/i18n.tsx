import { createContext, useContext, useEffect, useRef, useState, type ReactNode } from "react";

export type Language = "zh" | "en";
const STORAGE_KEY = "rp.language";
let activeLanguage: Language = "zh";

export function initialLanguage(stored: string | null, browser: string): Language {
  if (stored === "zh" || stored === "en") return stored;
  return browser.toLowerCase().startsWith("zh") ? "zh" : "en";
}

export function saveLanguage(language: Language, storage: Pick<Storage, "setItem">): void {
  storage.setItem(STORAGE_KEY, language);
}

const LanguageContext = createContext<{ language: Language; setLanguage: (value: Language) => void }>({
  language: "zh", setLanguage: () => undefined,
});
export const useLanguage = () => useContext(LanguageContext);

// Only fixed product copy is translated. Story text, user content and AI output
// are excluded by the DOM localizer and never passed to this table.
const EN: Record<string, string> = {
  "AI 角色扮演引擎": "AI RP Engine", "正在连接平台…": "Connecting to platform…",
  "正在检查登录状态…": "Checking login…", "使用账号登录": "Sign in to your account",
  "创建一个新账号": "Create an account", "用户名": "Username", "密码": "Password",
  "重复密码": "Repeat password", "登录": "Sign in", "注册": "Register",
  "处理中…": "Working…", "已有账号？登录": "Already have an account? Sign in",
  "没有账号？注册": "New here? Register", "注册当前已关闭，请使用已有账号登录。": "Registration is closed. Please sign in.",
  "请填写用户名和密码": "Enter a username and password", "用户名不能为空": "Username is required",
  "两次输入的密码不一致": "Passwords do not match", "用户名已存在": "Username is already taken",
  "用户名或密码错误": "Incorrect username or password", "账号信息无效：用户名已被使用，或密码少于 8 个字符": "Invalid account: username is taken or password is too short",
  "登录状态已失效，请重新登录": "Session expired. Please sign in again",
  "登录状态已失效，请重新登录。": "Session expired. Please sign in again.",
  "当前未开放注册，请使用已有账号登录": "Registration is closed. Please sign in.",
  "请求内容无效": "Invalid request", "服务器处理失败，请稍后重试": "Server error. Please try again.",
  "操作失败，请稍后重试": "Operation failed. Please try again.",
  "与服务器的连接已断开": "Disconnected from server",
  "公告": "Announcement", "退出登录": "Sign out", "创建房间": "Create Room",
  "加入房间": "Join Room", "我的剧本": "My Scripts", "我的存档": "My Saves",
  "活跃房间": "Active Rooms", "剧本广场": "Script Plaza", "暂无活跃房间。": "No active rooms yet.",
  "剧本广场暂时没有公开剧本。": "No public scripts yet.",
  "↻ 刷新": "↻ Refresh", "↻ 随机": "↻ Shuffle",
  "刷新并重新抽取活跃房间": "Refresh and shuffle active rooms",
  "从公开剧本中随机抽取": "Shuffle public scripts",
  "房间码": "Room code", "房主": "Host", "已满": "Full", "加入": "Join",
  "关闭": "Close", "关闭异常房间": "Close failed room", "返回房间": "Return to Room",
  "离开房间": "Leave Room", "第一次来？试试示例剧本": "First time here? Try a starter script",
  "快速体验": "Quick demo", "开始游戏": "Play", "人": "players",
  "公告加载失败，请稍后重试。": "Could not load announcement. Please try again.",
  "正在加载公告…": "Loading announcement…", "暂无公告。": "No announcement.",
  "✕ 关闭": "✕ Close", "搜索": "Search", "没有找到这个房间码。": "Room code not found.",
  "房间密码": "Room password", "房间人数已满": "Room is full",
  "取消": "Cancel",
  "选择游戏存档": "Choose a save", "选择剧本": "Choose a script",
  "从剧本开始": "Start from a script", "存档名称（默认与剧本同名）": "Save name (defaults to script name)",
  "房间密码（可选，字母/数字/_）": "Room password (optional, letters/digits/_)",
  "密码只能包含字母、数字和下划线": "Password may contain only letters, digits and underscores",
  "创建并进入": "Create and enter", "创建中…": "Creating…",
  "还没有存档。用「从剧本开始」创建第一份吧。": "No saves yet. Start one from a script.",
  "读取存档": "Open save", "重命名": "Rename", "复制": "Copy", "删除": "Delete",
  "导出世界信息": "Export history", "还没有自己的剧本。": "You have no scripts yet.",
  "新建": "New", "剧本名称": "Script name", "剧本": "Script", "存档": "Save",
  "创建剧本": "Create script", "上传zip新建": "Create from ZIP", "使用": "Use",
  "详情": "Details", "编辑": "Edit", "导出zip": "Export ZIP", "导入zip": "Import ZIP",
  "切换为私密": "Make private", "切换为公开": "Make public",
  "公开": "Public", "私有": "Private", "作者：": "Author: ", "未知": "Unknown",
  "剧本介绍": "About this script", "角色": "Characters", "使用剧本": "Use script",
  "新剧本使用项目自带的基础剧本生成角色，默认私有。": "New scripts use the bundled scaffold and start private.",
  "编辑剧本": "Edit script", "填写文字内容后保存，即可用于创建存档。": "Save your text to use this script for a game.",
  "返回我的剧本": "Back to My Scripts", "正在加载剧本…": "Loading script…",
  "返回": "Back", "基本信息": "Basics", "标题": "Title", "简介": "Introduction",
  "标签（用逗号分隔）": "Tags (comma separated)", "世界": "World", "世界设定": "World description",
  "角色数量": "Number of characters", "角色人设": "Character sheet", "开场白": "Opening",
  "新增角色会加入末尾；减少角色会删除末尾角色的内容。": "New characters are added at the end; reducing the count removes the last character.",
  "AI 写作风格 / 创作要求": "AI writing style / instructions", "高级": "Advanced",
  "高级 Schema 与 Prompt 设置不会在此页面显示。": "Advanced schemas and prompts are not shown here.",
  "暂时不支持角色状态栏在线编辑。": "Character status cannot be edited here yet.",
  "如需编辑角色状态栏或更多 prompt，请下载 zip 项目，编辑完成后导入。": "To edit status or prompts, download the ZIP, edit it, then import it.",
  "您可以在这里下载高级模板。": "Download the advanced template here.",
  "下载高级模板": "Download advanced template", "保存": "Save", "保存中…": "Saving…",
  "取消 / 返回": "Cancel / Back", "剧本已保存。": "Script saved.",
  "ZIP 已导入，剧本内容已更新。": "ZIP imported; script updated.",
  "已连接": "Connected", "连接中": "Connecting", "正在重新连接": "Reconnecting",
  "未连接": "Disconnected", "会话已失效": "Session expired", "连接错误": "Connection error",
  "未知账号": "Unknown account", "观众": "Spectator", "人在线": "online",
  "返回登录页": "Back to sign in", "剧情": "Story", "聊天": "Chat",
  "房间聊天": "Room Chat", "本次连接尚无消息。": "No messages in this session yet.",
  "系统": "System", "管理员": "Host", "发送房间聊天…": "Send a room message…",
  "发送": "Send", "↓ 查看最新": "↓ Latest", "选择角色视角": "Choose a character view",
  "选择一个角色查看历史。": "Choose a character to read the story.",
  "开场": "Opening", "尚无已完成回合。": "No completed rounds yet.",
  "输入本回合角色行动…": "Enter this character's action…",
  "提交行动": "Submit action", "暂停": "Pause", "恢复": "Resume",
  "撤销提交": "Cancel submission", "已提交，等待对方…": "Submitted, waiting for others…",
  "已暂停": "Paused", "AI 正在生成…": "AI is generating…",
  "世界更新中": "Updating world", "视角生成中": "Generating views",
  "文段生成中": "Generating narration", "生成失败，等待房主处理": "Generation failed; waiting for host",
  "复制邀请链接": "Copy invite link", "已复制邀请链接": "Invite link copied",
  "⚙ 房主管理": "⚙ Host Controls", "房主管理": "Host Controls",
  "重新生成": "Retry", "重新生成上一回合": "Retry last round",
  "确认重新生成": "Confirm retry", "房间成员": "Room Members", "踢出": "Kick",
  "角色分配": "Assign Roles", "选择用户": "Choose user", "应用角色分配": "Apply assignments",
  "回滚剧情": "Roll Back", "目标回合": "Target round", "执行回滚": "Roll back",
  "确认执行回滚": "Confirm rollback", "历史记录": "History", "导出历史记录": "Export history",
  "导出中…": "Exporting…", "请等待本轮完成……": "Wait for this round to finish…",
  "本轮生成失败，请重新生成后导出。": "Generation failed. Retry before exporting.",
  "关闭房间": "Close Room", "已提交": "Ready", "编辑中": "Editing", "处理中": "Processing",
  "无人扮演": "Unassigned", "离线": "Offline", "· 离线中": "· offline",
  "房间不存在或已关闭": "Room does not exist or is closed",
  "请先离开当前房间": "Leave your current room first",
  "你已经主持了一个活跃房间": "You already host an active room",
  "该存档已经在另一个房间中运行": "This save is active in another room",
  "请选择存档或剧本": "Choose a save or script",
  "房间密码错误": "Incorrect room password", "请输入房间密码": "Enter room password",
  "房间密码只能包含 1–32 位字母、数字或下划线": "Room password must contain 1–32 letters, digits or underscores",
  "房主不能将自己踢出房间": "The host cannot kick themselves",
  "该用户已不在房间中": "This user has left the room",
  "行动内容过长": "Action is too long", "聊天消息不能为空": "Room message cannot be empty",
  "聊天消息过长": "Room message is too long", "角色尚未分配": "Roles are not assigned",
  "未知角色": "Unknown role", "未知命令": "Unknown command",
  "消息必须是 JSON 对象": "Message must be a JSON object",
  "世界更新失败，房主可以重试本回合": "World update failed; the host can retry this round",
  "你没有权限执行此操作": "You cannot perform this action",
  "剧本不存在": "Script not found", "存档不存在": "Save not found",
  "你没有权限修改该剧本": "You cannot edit this script",
  "该存档正在房间中使用": "This save is active in a room",
  "存档名称无效（1–60 个字符）": "Save name must have 1–60 characters",
  "剧本名称无效（1–60 个字符）": "Script name must have 1–60 characters",
  "剧本内容无效，请检查标签、简介或文件结构": "Invalid script content; check tags, introduction and files",
  "角色数量不在当前服务器允许范围内": "Character count is outside server limits",
  "ZIP 剧本格式或内容无效，请检查目录和必需文件": "Invalid script ZIP; check its folders and required files",
  "ZIP 剧本超过大小限制": "Script ZIP is too large",
  "存档历史暂时无法导出": "Save history is unavailable",
  "该账号仍拥有剧本 / 存档 / 活跃房间，无法删除": "This account still owns scripts, saves or rooms",
  "账号不存在": "Account not found", "来源不被允许": "Origin is not allowed",
  "历史记录下载失败，请稍后重试": "History download failed. Please try again.",
  "剧本已复制。": "Script copied.", "存档已复制。": "Save copied.",
  "存档已删除。": "Save deleted.", "剧本已删除，已有存档不受影响。": "Script deleted; existing saves are unaffected.",
  "已离开房间。": "You left the room.",
  "有未保存的修改，确定离开吗？": "You have unsaved changes. Leave anyway?",
  "ZIP 只包含已保存的内容。继续下载吗？": "The ZIP contains only saved content. Continue?",
  "导入 ZIP 将覆盖当前剧本，未保存的修改会丢失。确定继续吗？": "Importing the ZIP replaces this script and discards unsaved changes. Continue?",
  "导入 ZIP 会覆盖当前剧本的全部内容。确定继续吗？": "Importing the ZIP replaces this entire script. Continue?",
  "关闭房间会断开所有用户，但不会删除游戏存档。确定关闭？": "Close the room and disconnect everyone? The save will remain.",
  "将使用相同的玩家行动重新生成上一回合的世界更新和全部叙事。": "Regenerate the last round's world and narration using the same actions.",
  "当前正在编辑的下一回合输入将被清空。": "Current drafts for the next round will be cleared.",
  "人 · 快速体验": "players · Quick demo", "欢迎，": "Welcome, ",
  "你当前仍在": "You are still in ", "你的房间": "Your room",
  "恢复失败，无法加入。": "could not be recovered and cannot be joined.",
  "· 房间码": "· Code", "· 房主": "· Host", "当前第": "Current round",
  "回合": "round", "第": "Round", "状态 · 第": "Status · Round",
  "剧本已设为公开，所有用户都能在「剧本广场」中看到它。": "Script is public and visible in the Script Plaza.",
  "剧本已设为私密，只有你自己可用。": "Script is private and only available to you.",
  "例如 K7M4-PQ9D": "For example K7M4-PQ9D", "创建房间失败": "Could not create room",
  "加入游戏": "Join game", "原会话已失效。": "Previous session expired.",
  "原会话已失效，请重新加入游戏。": "Previous session expired. Please join again.",
  "回滚需要有效的回合编号": "Rollback needs a valid round number",
  "重试需要有效的回合编号": "Retry needs a valid round number",
  "重新分配前，已占用角色必须暂停": "Assigned roles must be paused before reassignment",
  "存档内容会被删除。": "The save will be deleted.",
  "已用它创建的存档不受影响。": "Existing saves are unaffected.",
  "已登录：": "Signed in: ", "房间": "Room", "房间密钥": "Room key",
  "收起": "Collapse", "无法加入房间": "Could not join room",
  "无法连接服务器": "Could not connect to server", "显示": "Show", "隐藏": "Hide",
  "正在加入…": "Joining…", "用户不存在": "User not found", "行动": "Action",
  "返回大厅": "Back to Lobby", "连接中…": "Connecting…",
  "服务器发送了无效消息": "Server sent an invalid message",
  "你已被房主移出房间": "The host removed you from the room",
  "ZIP 剧本不能超过 8 MB": "Script ZIP cannot exceed 8 MB",
  "下载剧本失败，请稍后重试": "Could not download the script. Please try again.",
  "无法解析服务器消息": "Could not read the server message",
};

export function translateUi(text: string, language: Language): string {
  if (language === "zh") return text;
  const trimmed = text.trim();
  const translated = EN[trimmed];
  if (translated) return text.replace(trimmed, translated);
  let match = /^剧本角色数 (\d+) · (公开|私有)$/.exec(trimmed);
  if (match) return `${match[1]} roles · ${EN[match[2]]}`;
  match = /^作者：(.+) · 剧本角色数 (\d+) · 更新时间 (.+)$/.exec(trimmed);
  if (match) return `By ${match[1]} · ${match[2]} roles · Updated ${match[3]}`;
  match = /^作者：(.+)$/.exec(trimmed);
  if (match) return `By ${match[1]}`;
  match = /^已重命名为「(.+)」。$/.exec(trimmed);
  if (match) return `Renamed to “${match[1]}”.`;
  match = /^已于 (.+) 离开房间。$/.exec(trimmed);
  if (match) return `Left the room at ${match[1]}.`;
  match = /^协议版本不兼容：服务器 (\d+)，客户端 (\d+)$/.exec(trimmed);
  if (match) return `Protocol version mismatch: server ${match[1]}, client ${match[2]}`;
  match = /^请输入新的(存档|剧本)名称（当前：(.+)）$/.exec(trimmed);
  if (match) return `Enter a new ${match[1] === "存档" ? "save" : "script"} name (current: ${match[2]})`;
  match = /^复制(存档|剧本)「(.+)」？副本会以新名称保存。$/.exec(trimmed);
  if (match) return `Copy ${match[1] === "存档" ? "save" : "script"} “${match[2]}”? The copy gets a new name.`;
  match = /^删除(存档|剧本)「(.+)」？(.+)?此操作不可撤销。$/.exec(trimmed);
  if (match) return `Delete ${match[1] === "存档" ? "save" : "script"} “${match[2]}”? This cannot be undone.`;
  if (/^用户名最长为 \d+ 个字符$/.test(trimmed)) return text.replace(trimmed, trimmed.replace("用户名最长为", "Username must be at most").replace("个字符", "characters"));
  if (/^密码至少需要 \d+ 个字符$/.test(trimmed)) return text.replace(trimmed, trimmed.replace("密码至少需要", "Password must have at least").replace("个字符", "characters"));
  return text;
}

export function uiConfirm(message: string): boolean {
  return window.confirm(translateUi(message, activeLanguage));
}

export function uiPrompt(message: string, value: string): string | null {
  return window.prompt(translateUi(message, activeLanguage), value);
}

export function localizedRoomMessage(message: {
  text: string; message_key?: string; params?: Record<string, string | number>;
}, language: Language): string {
  if (language === "zh" || !message.message_key) return message.text;
  const params = message.params ?? {};
  const name = String(params.name ?? "");
  const role = String(params.role ?? "");
  const round = String(params.round ?? "");
  switch (message.message_key) {
    case "user_joined": return `${name} joined the room.`;
    case "user_reconnected": return `${name} reconnected.`;
    case "user_replaced": return `${name} took over this session from another connection.`;
    case "identity_host": return "You are the <Host>.";
    case "identity_roles": return `You play <${String(params.names ?? "").replaceAll("、", ", ")}>. Continue the game.`;
    case "identity_spectator": return "You are a <Spectator>. Wait for the host to assign a role.";
    case "user_left": return `${name} left the room.${params.roles ? ` Roles ${params.roles} are unassigned.` : ""}`;
    case "user_kicked": return `${name} was removed from the room.${params.roles ? ` Roles ${params.roles} are unassigned.` : ""}`;
    case "role_paused": return `Player ${role} paused.`;
    case "role_resumed": return `Player ${role} resumed.`;
    case "roles_assigned": return `Host assigned roles: ${params.assignments ?? ""}.`;
    case "round_retry": return `Regenerating Round ${round} with the same actions…`;
    case "round_rollback": return `Rolled back to Round ${round}; later story was deleted.`;
    default: return message.text;
  }
}

function localizeDom(root: HTMLElement, language: Language,
  originals: WeakMap<Text, string>, attributes: WeakMap<Element, Map<string, string>>) {
  const excluded = "script, style, .story-scroll, .chat-message, .announcement-markdown, .detail-text, [data-no-i18n]";
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT);
  let node: Node | null;
  while ((node = walker.nextNode())) {
    const current = node as Text;
    if (current.parentElement?.closest(excluded)) continue;
    const prior = originals.get(current);
    const source = prior === undefined ||
      (current.data !== translateUi(prior, "en") && current.data !== prior)
      ? current.data : prior;
    originals.set(current, source);
    const result = translateUi(source, language);
    if (current.data !== result) current.data = result;
  }
  for (const element of root.querySelectorAll("[placeholder], [title], [aria-label]")) {
    if (element.closest(excluded)) continue;
    const saved = attributes.get(element) ?? new Map<string, string>();
    for (const name of ["placeholder", "title", "aria-label"]) {
      const value = element.getAttribute(name);
      if (value === null) continue;
      const prior = saved.get(name);
      const source = prior === undefined || value !== translateUi(prior, "en") && value !== prior ? value : prior;
      saved.set(name, source);
      const result = translateUi(source, language);
      if (value !== result) element.setAttribute(name, result);
    }
    attributes.set(element, saved);
  }
}

export function LanguageProvider({ children }: { children: ReactNode }) {
  const originals = useRef(new WeakMap<Text, string>());
  const attributes = useRef(new WeakMap<Element, Map<string, string>>());
  const [language, setLanguage] = useState<Language>(() => {
    try { return initialLanguage(localStorage.getItem(STORAGE_KEY), navigator.language); }
    catch { return initialLanguage(null, navigator.language); }
  });
  activeLanguage = language;
  useEffect(() => {
    try { saveLanguage(language, localStorage); } catch { /* private mode */ }
    document.documentElement.lang = language === "zh" ? "zh-CN" : "en";
  }, [language]);
  useEffect(() => {
    // Lobby and Host dialogs use React portals directly under body.
    const root = document.body;
    let scheduled = false;
    const update = () => { scheduled = false; localizeDom(root, language, originals.current, attributes.current); };
    const observer = new MutationObserver(() => {
      if (scheduled) return;
      scheduled = true;
      queueMicrotask(update);
    });
    observer.observe(root, { subtree: true, childList: true, characterData: true, attributes: true,
      attributeFilter: ["placeholder", "title", "aria-label"] });
    update();
    return () => observer.disconnect();
  }, [language]);
  return <LanguageContext.Provider value={{ language, setLanguage }}>
    {children}
    <div className="language-switcher" aria-label="Language">
      <button className={language === "zh" ? "active" : ""} onClick={() => setLanguage("zh")}>中文</button>
      <span>|</span>
      <button className={language === "en" ? "active" : ""} onClick={() => setLanguage("en")}>EN</button>
    </div>
  </LanguageContext.Provider>;
}

export function ProjectFooter() {
  return <footer className="project-footer">AI RP Engine · <a href="https://github.com/jczsocute/multiplayer-ai-roleplay-engine">GitHub</a>
    {" · "}<a href="https://github.com/jczsocute/multiplayer-ai-roleplay-engine#readme">Documentation</a>
    {" · "}<a href="https://github.com/jczsocute/multiplayer-ai-roleplay-engine/blob/main/LICENSE">MIT License</a>
  </footer>;
}
