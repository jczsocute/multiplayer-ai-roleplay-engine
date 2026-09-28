# Platform Multi-room v0.1

`server/platform/` 管理平台级身份与资源 metadata。它不运行 RoundManager、LLM pipeline、角色分配或单局 Room Chat；这些职责全部留在 `server/gameserver/`。

`room_manager.py` 管理 Room metadata 对应的 Runtime 与当前进程连接 membership；
`legacy_game_web.py` 只服务 deprecated 单局兼容模式。

## 资源关系

```text
User
  ↓ owns / may use
Template
  ↓ filesystem snapshot
Game Save
  ↓ activate
Room metadata + RoomManager
  ↓ one runtime per room
GameServer Runtime
```

- Template：可复用剧本；属于真实账号，可 private/public。
- Game：长期剧情存档；即使没有 active Room 也存在。
- Room：Game 的临时在线入口。关闭 Room 不删除 Game。

每个 active Room 对应一个独立 GameServer、game.db、RoundManager、Sessions 与
command lock。RoomManager 持有 `rooms[code]` 和进程内 `user_room[user_id]`；
服务器重启时连接 membership 清空，但会从 `rooms` 表顺序恢复 Runtime。

## 平台配置变量

```dotenv
WEB_HOST=0.0.0.0
WEB_PORT=8080
PLATFORM_DB=data/platform.db
ALLOW_REGISTRATION=true
AUTH_SESSION_DAYS=30
AUTH_COOKIE_SECURE=false
UI_FONT_SCALE=0.8
MAX_ROOM_USERS=10
ROOM_DISCONNECT_TIMEOUT_SECONDS=300
```

- `PLATFORM_DB`：平台唯一 metadata SQLite。
- `AUTH_COOKIE_SECURE=true`：只在 HTTPS 环境启用。
- `ACCOUNTS_DB=data/accounts.db`：仅作为旧 Account v0.1 一次性导入来源；不是新平台数据库。

空平台正常启动不要求 LLM 配置；创建/恢复实际 Room Runtime 时需要 GameServer
README 中列出的 LLM 配置：

```bash
python -m server.main
```

## platform.db

平台数据库包含五张表：

- `users`：稳定 `user_id`、唯一 username、scrypt password hash/salt。
- `auth_sessions`：SHA-256 token hash、user、创建与过期时间。
- `templates`：稳定 `tmpl_*` ID、owner、显示名称、public 标记和时间戳。
- `games`：稳定 `game_*` ID、owner、来源 Template、显示名称和时间戳。
- `rooms`：公开 code、owner、game、可选 password hash/salt 和创建时间。

`rooms.owner_user_id` 与 `rooms.game_id` 均为 UNIQUE：一个用户最多主持一个 active Room，一个 Game 最多激活为一个 Room。

如果 `platform.db` 不存在而旧 `accounts.db` 存在，初始化会将 `users` 与 `auth_sessions` 复制进新库并保留旧文件；项目不引入 migration framework。

## Metadata 与 payload

SQLite 只保存 catalog metadata，内容继续留在文件系统：

```text
templates/tmpl_K7F92A/...
games/game_M8Q21P/...
```

稳定 ID 与显示名称严格分离，改名不会改变目录或外键。Template → Game 通过 `copytree` 创建快照，Game runtime 不读取来源 Template 的后续变化。

Template 可用条件：当前用户是 owner，或 `is_public = true`。Game 只列给它的 owner。

## Lobby、HTTP 与 WebSocket

认证保持 Account v0.1 语义：scrypt + per-user salt、HttpOnly cookie、数据库只保存 session token hash。

Web 登录后进入 Lobby：主页显示活跃房间与仅含公开剧本的“剧本广场”；创建房间、
加入房间、我的剧本、我的存档在悬浮面板中操作。“我的剧本”仅列本人资源，
创建房间的剧本选择器则包含本人全部剧本和其他用户的公开剧本。当前没有
Template Web Editor；新建剧本会复制默认 scaffold 并生成所选角色数的文件。

```text
POST /api/register
POST /api/login
POST /api/logout
GET  /api/me
GET  /api/templates
GET  /api/games
GET  /api/rooms
GET  /api/rooms/<code>
POST /api/rooms
POST /api/rooms/<code>/leave
DELETE /api/rooms/<code>
```

创建接口接受 `source=game` 或 `source=template`。Template 来源先创建归当前用户
所有的长期 Game 快照，再激活为 Room。关闭接口只允许 owner，且只删除 Room
metadata/Runtime，不删除 Game。未登录访问资源 API 返回 401。

## 资源管理 API（Lobby owner）

```text
GET    /api/templates           当前用户可用的 Template（own + public，供创建房间选择）
GET    /api/templates/public    仅公开 Template（Lobby 剧本广场；不含自己私密的）
GET    /api/templates/<id>      详情（含 introduction / tags / role_names）
GET    /api/templates/mine      仅当前用户拥有的 Template
POST   /api/templates           {name, role_count} -> Scaffold 新 Template（私有）
PATCH  /api/templates/<id>      {name} 或 {is_public}（改名 / 切换公开·私密）
POST   /api/templates/<id>/copy 复制为新的私有 Template
DELETE /api/templates/<id>      删除 metadata + payload，已有 Game 不受影响
GET    /api/games               当前用户的 Game Save
PATCH  /api/games/<id>          {name}（只改 metadata，active 也可以）
POST   /api/games/<id>/copy     复制 metadata + payload + game.db
DELETE /api/games/<id>
```

- 所有修改操作都要求 owner；别人的资源返回 403（Template 为 `template_not_owned`）。
- Game 正在被 Room 使用时：copy/delete 返回 409 `game_is_active`，rename 允许。
- 资源不存在（或已不属于当前用户）返回 404，名称非法返回 400。
- 复制命名由 `catalog.next_copy_name` 统一处理（`名字` → `名字_1` → `名字_2`）。
- `PATCH /api/templates/<id>` 同时支持改名与可见性：`{name}` / `{is_public: true|false}`
  可单独或一起提交；`is_public` 必须是 JSON 布尔值（`0/1/"yes"` 一律 `invalid_request`）。
  可见性切换走 `catalog.set_template_public_owned` → `PlatformDatabase.set_template_public`，
  与 Admin Console 的 `set-template-public` 是同一个实现，Admin 只是跳过 owner 检查。
  公开后该 Template 会出现在所有人的 `GET /api/templates` 中。
- Template payload 的元数据统一放在 `metadata.json`（`count`/`names`/`introduction`/`tags`），
  运行时不读取旧的 `roles.json`；已登记的 Template / Game payload 会在 Platform 启动时
  被一次性、幂等地迁移（`catalog.migrate_catalog_payloads`），失败只记日志不阻塞启动。
`introduction`/`tags` 只存在于 payload，不写入 `platform.db`。
- `GET /api/templates/<id>` 返回详情：`id/name/owner_username/is_public/role_count/
  role_names/introduction/tags/updated_at`；私有 Template 仅 owner 可读（其他用户 403），
  公开 Template 任何已登录用户可读。列表接口只附带便宜的 `tags`，不含 `introduction`。
- Template 新建使用 `ScenarioManager.scaffold_roles`（基础剧本 + N 个角色），不复制
  `love_story` 之类的内容；副本一律 `is_public = false`。
- 文件操作采用补偿式一致性：先写到 `.{id}.creating|copying` 临时目录，写 metadata，
  再 rename 到最终位置；失败时清理临时目录并回滚 metadata（delete 则先把 payload
  移开、删 metadata、再删文件）。

## Room 生命周期

```dotenv
MAX_ROOM_USERS=10
ROOM_DISCONNECT_TIMEOUT_SECONDS=300
```

- `GET /api/rooms` 每项返回 `owner_username`、`occupancy`、`max_users`、
  `connected_count`、`has_password`。大厅人数用 `occupancy`：它等于该 Room 保留的
  座位数（已连接成员 + 断线宽限期内成员），而不是 websocket 数。
- 容量在 `RoomManager.enter` 中校验：同一账号重入自己的 Room 不额外占座，超过
  `MAX_ROOM_USERS` 的新用户得到 `room_full`。
- 失去 Room 连接后 `ROOM_DISCONNECT_TIMEOUT_SECONDS`（默认 300 秒）内保留
  `user_room`、角色与座位；超时后普通成员真正离开，房主超时通过同一套
  `RoomManager.close_room` 自动关房（保留 Game Save）。
- Room 断线与账号登录态无关：不会删除 `auth_sessions`、不会清 cookie，只有显式
  logout 才结束登录态。
- 房主可发送 `{"type":"kick_user","user_id":N}`（owner-only，不能踢自己）；服务端复用
  `GameServer.evict_user` 清理 session/角色/座位，目标收到 `{"type":"kicked",...}`
  后连接关闭。kick 不是 ban，被踢用户可重新加入。

公共游戏入口为 `ws(s)://HOST/ws?room=ROOM_CODE`。Room Password 不在 URL 中，
只在首个 `join`/`resume` 消息中传输；服务端保存 scrypt hash/salt，不保存明文。
一个已连接账号最多进入一个 Room，同 Room reconnect/newest-wins 仍复用 GameServer
Sessions 语义。

本机平台管理使用 loopback-only Admin endpoint：

```bash
python client/admin.py --uri ws://127.0.0.1:8080/admin/ws
```

`/admin/ws` 要求客户端地址为 loopback、账号密码正确且 `users.is_admin = 1`；
公共 `/ws` 仍拒绝 `join_host`。管理员通过 `python -m server.main --set-admin <username>`
授权。Admin Console 提供用户、剧本、房间三个页签和共用命令输入框；管理操作调用
`PlatformDatabase`、`catalog`、`RoomManager` 的同一批 helper，不在客户端直接操作 SQLite。
删除用户时如仍拥有剧本、存档或房间会被拒绝；删除剧本保留既有存档；管理员关闭房间
仍保留 Game Save。`users.is_admin` 是唯一管理员标志，不会自动授予首位注册用户。

## 导入 legacy Template

旧目录不会自动猜测 owner。先在 Web 注册账号，再显式导入：

```bash
python -m server.main \
  --import-template love_story \
  --owner Alice \
  --public
```

命令验证 owner 和源目录，为 Template 生成 `tmpl_*` ID，将 payload 复制到稳定目录，再写入 metadata。

旧 Game 必须显式声明 owner 后导入，不会在启动时自动登记：

```bash
python -m server.main --import-game old_game --owner Alice --name "Old Game"
```

## 当前限制

- RoomManager 是单进程内存管理器，不支持分布式/横向扩展。
- 没有 Template Web Editor、ZIP upload 或完整 Game library 管理。
- 没有持久化 room membership；重启后所有客户端重新连接。
- legacy Game 目录不会自动登记进平台 catalog。
