# AI RP Engine

一个轻量级双玩家 AI 角色扮演引擎。服务器负责剧本、SQLite 状态和 DeepSeek 调用；房间可容纳两名角色玩家与最多 98 名观众。普通用户可直接使用手机或桌面浏览器加入，Textual 终端客户端继续作为 Reference Client。

## 服务器安装与配置

需要 Python 3.11 或更高版本：

```bash
pip install -r requirements.txt
cp .env.example .env
```

在 `.env` 中填写服务器配置：

```dotenv
LLM_API_KEY=<用户自己的 API Key>
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
WORLD_UPDATE_MAX_TOKENS=8192
NARRATION_MAX_TOKENS=4096
# Web 页面和 Public WebSocket (/ws)
WEB_HOST=127.0.0.1
WEB_PORT=8080

# 独立的本机 Host 管理入口；必须保持 loopback
HOST_WS_HOST=127.0.0.1
HOST_WS_PORT=8766

# 房间共享入口密钥。留空则服务器启动时自动生成并打印到日志。
ROOM_KEY=

# 断线宽限期（秒），期间保留 nickname/role/view/draft 等待 resume。
DISCONNECT_GRACE_SECONDS=60

# Web UI 字号缩放（0.4-1.5），默认 0.7 为紧凑排版。
UI_FONT_SCALE=0.7
```

API Key 只应保存在被 Git 忽略的 `.env` 中。可在正式开局前人工验证 DeepSeek JSON Output：

```bash
python -m server.llm.smoke_test
```

Smoke test 不会被普通单元测试自动执行。

## Scenario Template 与 Game Instance

### Scenario Template

剧本模板位于 `templates/<scenario_name>/`，定义提示词、世界、人物和状态栏。这里是用户手工编辑剧本内容的入口。

`templates/default/` 是创建新剧本时使用的受保护基础模板，不是可直接游玩的剧本，因此不会出现在服务器剧本列表中，也不能通过普通命令删除。

仓库只跟踪 `templates/default/`。其他 `templates/<scenario_name>/` 都属于个人创作内容，已由 `.gitignore` 排除，不应提交或强制加入 Git。

### 数据层关系

`world_state` 是唯一持续的客观世界状态（Canonical State），也是下一轮 World Updater 继承的主要状态输入。所有会影响未来世界演化的事实都应保存在其中，包括时间、环境、地点、NPC、隐藏信息、幕后行动，以及 A/B 的位置、身体、精神、物品、关系和必要认知。

其余三类数据都是从本轮更新后的 `world_state` 派生出的当前视图：

```text
world_state
   │
   ├── public_information
   ├── player_views.A
   ├── player_views.B
   ├── player_statusbar.A
   └── player_statusbar.B
```

- `public_information`：可安全提供给 A/B Narrator 的共享叙事上下文，例如日期、时间和公共进度；不包含未发现地点、秘密行动和玩家私有状态，也不直接显示在 Player TUI 中。
- `player_views`：A/B 各自当前能感知、知道、发现或合理推断的信息，回答“这个玩家现在知道什么”。
- `player_statusbar`：从 `world_state` 中对应角色真实状态提炼出的 UI 摘要，也供 Narrator 理解角色当前可展示状态；它不是角色真实状态的唯一存储位置。

下一轮 World Updater 不依靠上一轮的 public/view/statusbar 延续事实。角色受伤、物品变化或记忆变化等持续信息必须先进入 `world_state`。

Narrator 只接收 `public_information`、当前玩家自己的 `player_view` 和 `player_statusbar`、对应角色设定、AI 创作规范及该玩家历史聊天。它不会接收完整 `world_state`，因此无法直接读取幕后真相或另一玩家的私有状态。

### Game Instance

实际游戏位于 `games/<game_name>/`，内容包括从 Scenario Template 复制出的配置、`game.db`、当前世界状态、聊天历史及玩家状态。

不要把 `games/` 当作剧本编辑入口。创建实例后，它拥有配置文件的独立副本；以后修改原 Template 不会影响旧实例。需要应用新版 Template 时，应创建新的 Game Instance。

`games/` 中的所有内容都是本地运行数据和个人游玩内容，整个目录由 `.gitignore` 排除，不应提交或强制加入 Git。

## 创建自己的剧本

先从基础模板创建一个可玩的 Scenario：

```bash
python -m server.main --create-scenario my_story
```

命令会完整复制 `templates/default/` 为 `templates/my_story/`，不会自动改写其中内容。然后直接编辑以下文件：

- `prompts/world_update.md`：世界管理 AI 的基本规则，负责客观世界状态、`public_information`、`player_views` 和 `player_statusbar`；不应在这里编写具体剧情。
- `prompts/narration.md`：A/B 共用的玩家文学叙事规则。
- `prompts/ai_guidelines.md`：本剧本创作规范，例如人称、文风、节奏、信息隔离强度和特殊要求，不包含具体剧情。
- `schemas/world_updater_output.json`：World Updater 单次调用的完整 DeepSeek JSON Output 示例外壳；它既不是 `world_state` schema，也不是标准 JSON Schema。
- `world/world.md`：具体世界观、背景、规则和剧本设定。
- `world/initial_state.json`：Round 0 的完整客观 `world_state`，包括 A/B 的初始真实角色状态。
- `characters/player_a.md`：Player A 的角色设定。
- `characters/player_b.md`：Player B 的角色设定。
- `characters/statusbar/player_a.json`：Player A 希望展示的状态栏字段及组织说明。
- `characters/statusbar/player_b.json`：Player B 希望展示的状态栏字段及组织说明。
- `roles.json`：角色名清单，`count` 为角色数量，`names` 为依次排列的角色名（长度等于 `count`），供 UI 与提示词显示真实角色名，例如 `{"count": 2, "names": ["林承", "周璐"]}`。

A/B 的状态栏结构可以完全不同。这些文件不是初始状态；初始身体、精神、位置、物品等真实事实必须写入 `world/initial_state.json`。

### 推荐编辑流程

普通新剧本通常只需修改：

1. `world/world.md`
2. `world/initial_state.json`
3. `characters/player_a.md`
4. `characters/player_b.md`
5. `characters/statusbar/*.json`
6. `prompts/ai_guidelines.md`

需要深度定制 AI 行为时，再修改 `prompts/world_update.md`、`prompts/narration.md` 或 `schemas/world_updater_output.json`。普通剧本不一定需要修改核心 Prompt。

Scenario 管理命令：

```bash
python -m server.main --list-scenarios
python -m server.main --create-scenario lighthouse
python -m server.main --delete-scenario lighthouse
```

名称只允许字母、数字、下划线和连字符。`default` 不会被列出，也不能删除。

## 启动或恢复游戏

交互启动服务器：

```bash
python -m server.main
```

服务器会列出 `default` 以外的 Scenario。选择后会创建或恢复同名 Game Instance，并同时启动 Web/Public 服务和独立 Host WebSocket。如果没有可玩 Scenario，服务器会提示先创建剧本并正常退出。

也可以直接创建或恢复一个与 Scenario 同名的实例：

```bash
python -m server.main --game lighthouse
```

管理运行实例：

```bash
python -m server.main --list-games
python -m server.main --delete-game lighthouse
```

删除 Game Instance 会删除该局的数据库和进度，但不会删除 Scenario Template。

默认入口：

```text
Web:              http://127.0.0.1:8080/
Public WebSocket: ws://127.0.0.1:8080/ws
Host WebSocket:   ws://127.0.0.1:8766
```

`SERVER_HOST`/`SERVER_PORT` 仍作为 Web 配置的旧环境变量 fallback，但新部署应使用语义明确的 `WEB_*` 和 `HOST_WS_*`。

## 用户进入房间与角色分配

普通用户在浏览器打开：

```text
http://SERVER_IP:8080/
```

页面会按当前 origin 自动连接 `/ws`，无需填写服务器或 WebSocket 地址。输入昵称和 Room Key 后以 Spectator 加入；Host 分配角色后页面会自动切换为 Player UI。

旧普通 TUI 仍可连接完全相同的 Public handler（protocol v2 后需要 Room Key）：

```bash
python client/terminal.py --uri ws://SERVER_IP:8080/ws --name Alice --room-key K7M4-PQ9D
python client/terminal.py --uri ws://SERVER_IP:8080/ws --name Bob --room-key K7M4-PQ9D
```

未提供 `--room-key` 时读取 `ROOM_KEY` 环境变量；服务器使用 `--no-room-key` 启动时，TUI 可直接连接、无需任何 Key。

每个新连接都是运行时 User，默认身份为 Spectator；连接顺序不会自动决定 A/B。普通 User 最多同时在线 100 人，Host 是独立连接，不占此名额。同名用户同时在线时，新连接会被拒绝。

在服务器主机上另开终端启动独立 Host TUI：

```bash
python client/host.py --uri ws://127.0.0.1:8766
```

Host 使用独立的 loopback-only listener，Public `/ws` 永远拒绝 `join_host`；权限不依赖来源 IP 判断，因此把 Web 端放到反向代理或 Tunnel 后也不会间接暴露 Host。若需从另一台管理设备操作，可使用 SSH 本地端口转发。Host TUI 会列出房间内所有当前在线昵称。选择两名 User 后输入：

```text
/assign Alice Bob
```

表示 Alice → Player A、Bob → Player B。其他在线 User 继续作为 Spectator。

`/assign` 可以重复用于换人，但当前仍有扮演者的 A、B 都必须处于 `PAUSED`；没有扮演者的角色视为空位。AI 正在 `PROCESSING` 时不允许换人。只有 role 实际变化的 User 会收到 UI reset 和完整角色视图；未变化的 Player 保留原状态。新接管角色进入 `EDITING` 且 Draft 为空，原 Player 降为 Spectator 并默认继续查看其原角色。

Player/Spectator TUI 上方的可滚动文字框显示当前角色历史、Room Chat 和角色状态栏，中间只显示 A/B 工作状态，底部是多行 Draft 与单行 Command。状态栏不再占用独立的常驻区域；无论载入历史还是实时完成新一轮，每轮都按“行动 → 输出 → 状态栏”完整显示。状态栏使用绿色框线，内容仍以 YAML 展示。`public_information` 仅作为 Narrator 的共享创作输入，不直接展示给玩家。

Player 命令：

- `Enter`：在 Draft 中换行，不提交。
- `/submit`：从 Draft TextArea 读取并提交当前完整行动；提交后内容保持可见但只读。
- `/cancel`：从 `READY` 返回 `EDITING`，保留原 Draft 并恢复编辑。
- `/pause`、`/resume`：暂停或恢复编辑。
- `/status`：只显示当前角色自己的状态栏；A/B 协作状态始终由输入框上方的实时状态区域显示。
- `/chat`：把当前 Draft 作为 Room Chat 发送并清空，不改变角色状态，也不进入 AI history。
- `/help`：在本地显示 Player 可用命令，不向服务器发送消息。
- `/quit`：退出客户端。

Spectator 命令：`/chat`、`/view A`、`/view B`、`/help`、`/quit`。Spectator 的 Draft 只用于 Room Chat，不能执行游戏控制命令；其 Draft 和 Command 灰色占位提示也只列出观众用途与命令，不会显示 `/submit`、`/pause` 等玩家命令。

Host 命令：`/assign <nicknameA> <nicknameB>`、`/chat`、`/view A`、`/view B`、`/view world`、`/status`、`/retry`、`/help`、`/quit`。`/retry` 只属于 Host。三种身份的 `/help` 都由 TUI 本地处理，只显示当前身份可用命令。

以上 Slash command 只用于 TUI。Web Client 使用明确按钮，并把 Action Draft 与 Chat Draft 分开；Player 即使处于 `READY`、`PAUSED` 或 `PROCESSING`，仍可继续使用 Room Chat。TUI 仍保留原来的单 Draft 行为。

双方提交后进入 `PROCESSING`。服务器只在阶段切换时广播 `WORLD_UPDATING`、`VIEW_GENERATING`、`NARRATION_GENERATING`，客户端在本地按秒计时并显示“世界更新中 · 3s”等状态。AI 完成后各自收到私有 Narration，服务器进入下一轮并清空旧 Draft。

Host TUI 与普通客户端使用相同的 Identity、主视图、A/B 状态、Draft 和 Command 布局，并额外显示所有在线用户。它也不保留独立 Statusbar 区域：`/view A`、`/view B` 会在主文字框中逐轮显示行动、输出和绿色框线的状态栏；`/view world` 以 YAML 显示完整的 `world_state`、`public_information`、`player_views` 和 `player_statusbar`。完整调试数据不会发送给普通 User。

### Story Plane 与 Room Plane

Story Plane 包含 A/B Action、World Update、Player View、Narration 和 Statusbar，数据会按角色持久化并可进入 AI 上下文。

Room Plane 包含 Presence、系统通知和 Host/Player/Spectator 真人聊天，只在当前服务器进程内广播，不写入 `chat_messages`，永远不会进入 World Updater 或 Narrator history。

Room Chat 统一使用 `room_message` 协议。客户端按 `kind` 渲染：`[系统]` 为整行 dim cyan，`[管理员]`、`[昵称 (角色名)]`、`[昵称]` 使用三种不同前缀颜色，正文保持普通颜色。角色名由服务器提供，客户端不读取 Scenario 文件。

### 角色 View 与历史顺序

Player 固定查看自己的角色；Spectator 可用 `/view A|B`，Host 可额外 `/view world`。角色视图中的已完成回合按以下顺序重建到上方文字框：

1. 该角色提交的 Action
2. 该角色收到的 Narration
3. 该回合结束后的 Statusbar

每个已完成回合都是不可拆分的 Action → Narration → Statusbar 记录。TUI 重建历史时逐轮展示三者；新回合完成时，当前打开该角色视图的 Player、Spectator 或 Host 也会立即追加“本轮行动 → 本轮输出 → 本轮状态栏”，无需重新执行 `/view`。

Spectator 和 Host role view 不包含当前 Draft。只有当前真正扮演该角色的 Player 会在私有 `role_view`/`status` payload 中收到自己的 Draft。

### WebSocket 消息概览

- `joined`：连接身份、初始 spectator 状态与 `resume_token`。
- `resumed`：resume 成功后的身份与角色恢复结果。
- `presence`：当前在线 User 列表及其 A/B role、connected 状态。
- `identity_changed`：仅发给 assign/reassign 后 role 真正变化的 User，要求 reset UI。
- `role_assigned`：房间级角色绑定结果。
- `role_view`：按角色重建的历史、当前状态栏；Player 私有版本可额外含 Draft 和当前 View。
- `role_round`：新完成回合的 Action→Narration→Statusbar。
- `room_message`：统一的 system/host/player/spectator Room Chat。
- `state`、`processing_stage`：A/B 工作状态与 AI 阶段。
- `world_update`、`world_view`：仅 Host world view 使用的完整调试数据。

`joined`/`resumed`（以及 Host 的 `host_joined`）包含 `protocol_version: 2`。浏览器会拒绝不支持的未来版本并显示明确错误。

Public WebSocket 的第一条消息只允许 `join`（带 `name` + `room_key`）或 `resume`（带 `name` + `room_key` + `resume_token`）；`join_host` 永远被拒绝。

关键 Room Plane payload 示例：

```json
{"type":"presence","users":[{"name":"Alice","role":"A"},{"name":"Tom","role":null}]}
```

```json
{
  "type": "room_message",
  "kind": "player",
  "sender": "Alice",
  "role": "A",
  "character_name": "林岚",
  "text": "我觉得这里先不要开门。"
}
```

`role_view` 是服务器按接收者权限组装的消息，而不是共享缓存：Spectator/Host role view 没有 `draft`；当前 Player 的私有版本才包含该角色 Draft。

## 断线与恢复

User 意外断线后**不会立即删除**：进入 `DISCONNECT_GRACE_SECONDS`（默认 60 秒）宽限期，期间 nickname、role、view_role 与 Player Draft 都保留，Presence/State 中该 User 标记为 `connected=false`。宽限期内用 `resume`（name + room_key + resume_token）重连即可恢复原身份，无需 Host 再次 assign；恢复后广播“已重新连接”。宽限期超时才真正删除 User，释放 nickname 与角色绑定，并广播正式离开消息。

Web Client 在连接意外断开后自动用 exponential backoff（1s → 2s → 4s → 8s → 10s…）尝试 resume；resume token 只保存在 `sessionStorage`（当前 tab 生命周期），刷新页面也会先尝试恢复。服务器重启后所有 runtime token 失效，需重新加入。

玩家主动点击“返回登录页”或 TUI `/quit` 会先发送 `{"type":"leave"}`：立即释放 nickname/role、invalidate resume token，不进入宽限期。

角色历史、世界状态、A/B 状态栏和 AI Recovery 数据仍按角色保留；原 Player 离开后该角色显示“无人扮演”。

服务器重启时在线 User 与角色绑定均为空，Host 必须重新 assign；服务器仍会从 SQLite 保存的 AI 阶段继续处理，不重复已经成功完成的 World Update。旧数据库启动时会删除旧 `participants` 表，并新增逐回合 `player_statusbars` 表；旧回合没有历史 statusbar 快照时不会伪造数据。

## Room Key

`.env` 中：

```dotenv
ROOM_KEY=
```

- 留空：服务器启动时用 `secrets` 自动生成一个适合人工输入的随机 Key（形如 `K7M4-PQ9D`，避免 `0/O/1/I/l`），并在启动日志中打印 `Room Key: ...`。
- 填写固定值：直接使用该值。
- 启动参数 `--no-room-key`：不设置任何 Key。此时 Web 只显示昵称输入框，TUI 可直接连接；Public join/resume 不再校验 room_key。

Room Key 只存在服务器运行时或 `.env`，不写数据库、不进入 game state、不进入 Room Chat、不发送给已加入用户、不做持久化。Public `/ws` 的 `join`/`resume` 在启用 Key 时必须携带正确 `room_key`；Host endpoint 不需要 Room Key。比较时忽略大小写和 `-`/空格。

## Web 加入与手机 UI

浏览器打开 `http://SERVER_IP:8080/`，输入昵称（启用 Key 时还需 Room Key）即可加入。移动端为 Story / Chat 双 tab 布局，桌面宽屏自动变为 Story(70%) + Chat(30%) 双栏；Story 历史与追加输出都在面板内竖向滚动，Chat 同理。字号由 `.env` 的 `UI_FONT_SCALE` 控制（默认 0.7，范围 0.4–1.5），整体为低饱和紫色深色主题，状态栏默认折叠、点击展开。


## 仅作为远程客户端

纯客户端设备只需复制：

```text
client/
requirements-client.txt
```

安装依赖：

```bash
pip install -r requirements-client.txt
```

连接服务器：

```bash
python client/terminal.py --uri ws://SERVER_IP:8080/ws --name Alice
```

纯客户端不需要 `server/`、`templates/`、`games/`、SQLite 数据库、`.env` 或 DeepSeek API Key，也不会直接调用 LLM；所有 AI 和持久化工作都由服务器完成。

## Web Client 开发

仓库提交了 `web/dist/` 生产构建，因此普通玩家和运行服务器的人不需要安装 Node.js。只有修改 Web UI 时才需要 Node.js：

```bash
cd web
npm install
npm run dev
```

Vite 开发服务器会把同源 `/ws` 代理到 `ws://127.0.0.1:8080`。另一个终端正常启动 Python server 即可。

## Web Client Build

修改前端后更新生产构建：

```bash
cd web
npm run build
```

输出位于 `web/dist/`，Python server 会直接提供其中的静态文件。

## 手机局域网测试

把 `.env` 中 Web listener 改为：

```dotenv
WEB_HOST=0.0.0.0
WEB_PORT=8080
```

重启服务器后，同一局域网内的手机访问：

```text
http://SERVER_LAN_IP:8080/
```

只开放 Web/Public 端口；`HOST_WS_HOST` 必须继续保持 `127.0.0.1`（服务器也会拒绝非 loopback 配置）。

## 临时公网访问

服务器保持 `WEB_HOST=127.0.0.1` 时，可以手工启动 Cloudflare Quick Tunnel：

```bash
cloudflared tunnel --url http://127.0.0.1:8080
```

把生成的 HTTPS 地址发给玩家即可。Tunnel 只应指向 `8080` Web/Public endpoint，绝不能指向 `8766` Host endpoint。本项目不自动下载、启动或管理 cloudflared。

## 测试

普通测试全部使用 Mock，不调用真实 API：

```bash
python -m unittest discover -v
```

前端 reducer 测试与生产构建：

```bash
cd web
npm test
npm run build
```
