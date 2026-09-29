# TODO

## Completed

### Template Library v0.1

- Lobby 可浏览公开剧本；用户可创建、复制、重命名、公开或删除自己的剧本。
- 基础 Web Editor 可编辑标题、简介、标签、世界文字、角色人设、开场白和 AI 写作要求，并增减末尾角色。
- 用户可上传 ZIP 新建剧本，也可下载或替换自己剧本的完整 payload；上传有大小、路径和格式校验。
- 高级 schema 与 prompt 的在线编辑仍列于 Deferred。

Status: `Completed`

### Modular Template payload

- 当前唯一格式使用 `metadata.json`、World schema/initial、编号 `characters/1..N/`，以及每个角色的 view schema、可选 status schema/initial 和 opening。
- `*_schema.json` 是给 LLM 的字段示例与说明，不是标准 JSON Schema；`server/gameserver/template.py` 校验目录与初始数据。
- WorldUpdater 动态组装 `world_state`、`character_views`、可选 `character_status`；Narrator 只读取角色可见信息、可选状态与近期剧情。

Status: `Completed`

### Payload migration: roles.json → metadata.json + title / introduction / tags

- Template/Game payload 的角色配置改为 `metadata.json`：`{count, names, title, introduction, tags}`；
  `count` / `names` 的数据结构与角色 id 规则完全保持旧 `roles.json` 形态。
- 统一 loader / 校验 / 写入 / 迁移收敛在 `server/gameserver/roles.py`；运行时代码不再读取
  `roles.json`（`LEGACY_ROLES_FILENAME` 只服务一次性迁移）。
- 已登记的 Template / Game 在 Platform 启动时幂等迁移（`catalog.migrate_catalog_payloads`）；
  import 先复制源目录，只在副本中迁移；legacy `--game` 只迁移显式指定的目录；未登记目录不扫描。
- 迁移写 `metadata.json.tmp` + `os.replace` 后才删除 `roles.json`；失败保留旧文件。
- 只迁移 metadata；更早的 `players/`、`statusbar/` 等内容布局需手动改成当前格式。
- `GET /api/templates/<id>` 详情接口提供 introduction / tags / role_names（私有仅 owner 可见）；
  Template Detail overlay 显示 tags chips、剧本介绍（`pre-wrap` 纯文本）与角色列表。
- `templates/default` 已是 `metadata.json`；`love_story` / `three_player_test` 的
  introduction / tags 由 `bootstrap.BUNDLED_DESCRIPTIONS` 按实际 payload 内容提供。

Status: `Completed`

### Lobby / Resource Management / Room Lifecycle

- Lobby 重构为「发现页 + 四个悬浮面板（创建房间 / 加入房间 / 我的剧本 / 我的存档）」，
  桌面居中 modal、手机接近全屏；活跃房间与剧本广场都改成 row。
- 我的存档：读取 / 重命名 / 复制 / 删除；我的剧本：新建（2/3/4 角色，默认私有）/
  使用 / 重命名 / 复制（副本私有）/ 删除。owner API 与 Admin Console 共用同一批
  Platform helper。
- Room 座位上限 `MAX_ROOM_USERS=10`（含断线宽限座位），第 11 个新用户得到 `room_full`。
- 断线宽限 `ROOM_DISCONNECT_TIMEOUT_SECONDS=300`：普通成员超时真正离开，房主超时自动
  关房（保留 Game Save）；Room 断线不会登出账号。
- 房主可在管理面板踢人（非 ban，可重新加入）。
- `games.updated_at` 在回合完成 / retry / rollback 后更新。

Status: `Completed`

### Platform Admin & UX Cleanup

- 删除旧 Player / Host TUI（`client/terminal.py`、`client/host.py`、`client/display.py`
  及相关 CSS 与测试），同时删除只服务它们的 `HOST_WS_*` listener、`GameServer.host_handler`
  与 Host 协议粘合；Web 现在是唯一用户客户端。
- 新增本机 `client/admin.py` Platform Admin Console（`/admin/ws`，loopback + 密码 +
  `users.is_admin` 三重校验），支持 Users / Templates / Rooms 三个页签与共享命令行。
- 平台管理员通过 `python -m server.main --set-admin <username>` 授权，存于
  `platform.db.users.is_admin`；不自动创建管理员，也没有 RBAC / Host Key。
- legacy Template 通过 `--bootstrap-templates --owner <username>` 显式登记进 catalog，
  对 `(owner, name)` 幂等；目录存在不再等价于 Platform 资源。
- Web 注册增加重复密码（仅前端校验，不发送），后端错误码映射为中文提示。
- Web 界面 chrome 中文化（协议字段、DB 列、错误码保持英文）。

Status: `Completed`

### Multi-room v0.1

Platform RoomManager 已可同时恢复和运行多个彼此隔离的 GameServer Runtime，支持
Lobby、创建/加入/离开/关闭、公开 Room Code、可选密码与 Web 房主角色分配。

Status: `Completed`

## Deferred

### Large history exports

当前历史 ZIP 会在内存中组装完整文件，适合目前的单机规模；很长的存档可能带来较高瞬时内存占用。若后续出现实际大存档，再评估流式生成或导出大小上限。

Status: `Deferred`

### Advanced Template Editor

基础 Web Editor 已可编辑标题、简介、标签、世界、人设、开场和 AI 写作要求。
高级 schema 与 WorldUpdater/Narrator prompt 编辑仍未实现，基础保存会保留这些文件。

Status: `Deferred`

### Mobile CJK font fallback

**Problem**

部分手机浏览器中的中文正文仍可能明显回退为衬线字体。

**Current behavior**

- Desktop Web 已确认使用无衬线字体。
- Web CSS 已明确设置 `sans-serif` fallback，表单控件也使用 `font: inherit`。
- Production build 已重新生成，且未发现显式的 `font-family: serif` 规则。
- 问题可能来自移动端浏览器或系统的 CJK generic font fallback；现有 font stack 在部分设备上可能未命中合适的中文无衬线字体。

**Possible future direction**

- 使用 DevTools 或 remote debugging 检查手机实际使用的 Rendered Font。
- 分别确认 Android 和 iOS 的系统 CJK sans fallback。
- 如果仍无法稳定控制，再考虑引入项目内置中文无衬线 Web Font；当前阶段不要为此引入大型字体资源。

Status: `Deferred`

### Long-game narrative memory

**Problem**

Narrator 只读取有限窗口，很长游戏中早期文学细节会逐渐离开叙事上下文。

**Current behavior**

- canonical `world_state` 继续负责事实连续性（时间、地点、物品、契约、关系事实等）。
- Narrator 只读取最近 `NARRATOR_HISTORY_ROUNDS`（默认 20）个完整已完成回合的 Action + Narration。
- 早期人物关系、语气和情绪事件可能随时间淡出，不再直接进入 Narrator 上下文。

**Possible future direction**

- 后续可考虑 episodic summary / character memory：把较早回合压缩为简短摘要后再保留。
- 当前不要实现，也不要把全部历史永久塞进 prompt。

Status: `Deferred`

## Resolved

### Closed-tab nickname recovery

**Was**

用户关闭标签页后立即重新进入时，原昵称在 disconnect grace period 内仍可能被占用：resume token 随 `sessionStorage` 丢失，服务器又只按 nickname 识别身份。

**Resolved by Account v0.1**

- 身份改为稳定 `user_id`（auth Cookie 中的登录 session），nickname/username 不再作为主键。
- 关闭标签页后重新打开：`GET /api/me` 恢复登录态，再以 `join` 连接同一个 Game；服务器发现已有同 `user_id` 的 participant，就把它视为同一账号重新连接并接管原有角色 / Draft。
- 其他账号即使使用相同 username 也无法抢占该角色。

Status: `Resolved (Account v0.1: stable user_id + auth session)`

### legacy love_story 在 Web 不可见

**Was**

`templates/love_story/` 存在于磁盘，但 Web 的 `GET /api/templates` 看不到它。

**Resolved by Platform Admin & UX Cleanup**

- 这不是 bug：catalog 以 `platform.db.templates` 为唯一权威来源，Web 不扫描文件系统。
- 该 Template 从未 import，因此不存在 metadata / owner / public 状态。
- 现在通过 `python -m server.main --bootstrap-templates --owner <username>` 显式登记
  （幂等）或 `--import-template <name> --owner <username> --public` 单独导入。

Status: `Resolved`

## Decided

### Single linear timeline (no revisions / branches)

已确定采用单一线性时间线：`/retry` 重做最后一轮，`/rollback N` 物理删除 Round N 之后的剧情。
明确**不计划**实现 revision table、branch timeline、alternate timeline、undo/redo stack 或
soft-delete timeline；需要保留另一条剧情线时通过复制 Game 存档完成。

因此后续不要引入 `revision_id` / `branch_id` / `active_revision` / `deleted_at` 之类的字段或
revision graph 设计。

Status: `Decided (not planned)`

### Admin Console is not a game Host

`client/admin.py` 是平台级本机管理工具，**不是**房主界面，也不是玩家客户端。房主能力
（角色分配 / retry / rollback / 关闭房间 / 踢人）属于 Web 上的 `is_host` 用户。后续不要因为
“需要一个管理界面”而把 Admin Console 接回 GameServer host 协议，也不要重新引入
Player/Host TUI 或 `HOST_WS_*` listener。

Status: `Decided (not planned)`

### Kick is not a ban

被踢用户之后仍可重新加入同一个 Room（只要未满且密码正确）。明确**不计划**实现 ban list、
kick history、blocked users 或持久化 `room_members`；需要时另行设计。

Status: `Decided (not planned)`

### Lobby resource management stays on Platform helpers

Lobby 的 owner API（game/template rename/copy/delete）与 Admin Console 共用
`server/platform/catalog.py` 的同一批 helper；Admin 只是跳过 owner 检查。后续不要为 Web
单独写第二套 copy/delete 实现，也不要让 Web 直接操作文件系统。

Status: `Decided`
