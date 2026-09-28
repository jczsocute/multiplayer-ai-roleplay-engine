# GameServer Runtime

`server/gameserver/` 是单个 Game Save 的运行时边界。这里不管理平台资源目录、注册登录、Template ownership、Room catalog 或 Lobby，也不应出现 `room_id` / `room_code` 等平台路由概念。

## 负责范围

- `game_server.py`：单局命令处理、角色分配、广播、AI 回合、retry/rollback、recovery。
- `database.py`：单局 `game.db`；保存 world、round、actions、views、statusbars、narrations 和 player state。
- `round_manager.py`：动态角色的同步回合状态机。
- `session.py`：本局在线参与者、角色/view、resume 与 grace period。
- `protocol.py`：单局 WebSocket 协议与输入上限。
- `roles.py`：读取 payload 的 `metadata.json`，生成稳定角色 ID `P1..PN`；并提供 `metadata.json` 的校验、写入与一次性 `roles.json` 迁移。
- `llm/`：WorldUpdater、PlayerViewGenerator、Narrator、PromptLoader 和直接 LLM client。
- `factory.py`：Platform 用 Game payload 和 Platform owner 构造一个独立 Runtime。
- `runtime.py`：显式 legacy 单局启动器；正常平台启动不会调用它。

需要认证 cookie 的 legacy ASGI 组合 adapter 位于
`server/platform/legacy_game_web.py`，避免将注册、登录和平台数据库逻辑放进
GameServer 核心。

## 单局配置变量

LLM、角色与恢复变量同时用于 Platform 创建/恢复 Room Runtime；Room 路由变量仍只属于 Platform：

```dotenv
LLM_API_KEY=your-api-key
LLM_BASE_URL=https://api.deepseek.com
LLM_MODEL=deepseek-flash
WORLD_UPDATE_MAX_TOKENS=8192
NARRATION_MAX_TOKENS=4096
NARRATOR_HISTORY_ROUNDS=20
MIN_ROLE_COUNT=2
MAX_ROLE_COUNT=4
```

`ROOM_KEY` / `--no-room-key` 只属于 legacy single-game 模式。Platform Room 使用公开 `room_code` 与可选 `room_password`，不会把 Room Key 继续扩展为平台身份机制。
`DISCONNECT_GRACE_SECONDS` 同样只供旧单局入口使用；Platform Room 的成员宽限期
由 `ROOM_DISCONNECT_TIMEOUT_SECONDS` 控制。WebSocket `ALLOWED_ORIGINS` 属于
Platform Web 配置。

## 剧本与 Game payload

```text
metadata.json
world/initial_state.json
characters/player_N.md
characters/opening/player_N.md
characters/statusbar/player_N.json
prompts/
schemas/world_updater_output.json
```

`metadata.json` 示例：

```json
{"count": 3, "names": ["角色1", "角色2", "角色3"], "title": "气象站的雷雨夜", "introduction": "三人合作短剧本", "tags": ["三人", "合作"]}
```

显示名称不是主键；运行时始终使用 `P1..PN`。Opening 是静态展示内容，不是 Round，不进入 SQLite、world state 或 AI history。

`title` 是剧本自己的显示标题，Platform 的 catalog 名称是镜像；
`introduction` 与 `tags` 属于 payload，不写入平台数据库。
旧 `roles.json` 只供一次性迁移，新的剧本和存档都使用 `metadata.json`。

Game payload 是 Template 的完整文件快照，保存在 `games/game_*/`。单局运行时只读取 Game 自己的文件，不回读来源 Template。

## 回合与 AI pipeline

当前单局协议版本为 `6`。Platform 层只消费首个消息中的 Room Password 并选择
Runtime，之后继续使用同一套 GameServer 消息；平台资源 API 不进入游戏协议。

所有角色 READY 后进入 PROCESSING：

1. WorldUpdater 接收全部 actions，一次生成新 canonical world、public information、全部 views/statusbars。
2. Narrator 为各角色并行生成叙事，只读取 public information、自己的 view/statusbar、角色设定与有限历史。
3. 结果写入 `game.db`，广播各自的 `role_round`，进入下一轮。

`PlayerViewGenerator` 保留为接口，但正常流程直接使用 WorldUpdater 返回的 views。

玩家状态为 `EDITING → READY → PROCESSING`，`PAUSED` 是旁支状态。
持久化回合 stage 为 `WAITING_INPUT → WORLD_UPDATING → WORLD_DONE →
VIEW_GENERATING → VIEW_DONE → NARRATION_GENERATING → FINISHED`；stage 用于
观察执行状态，不是恢复检查点。`game.db` 保存 `world_state`、`rounds`、
`round_actions`、`chat_messages`、`player_views`、`public_world_info`、
`player_statusbars` 与 `players`。WorldUpdater 结果与 `WORLD_DONE` 原子提交。

Story Plane 持久化并可进入 AI context；Room Chat / presence / notice 仅在内存广播，不进入单局故事历史。角色 Opening 同样不进入 AI context。
Room Chat 不获取 Story 命令锁，所以 AI 生成期间仍可交流。显示历史完整保留；
Narrator 只取最近 `NARRATOR_HISTORY_ROUNDS` 个完整回合，默认 20。

## Retry、Rollback 与 Recovery

- `/retry` 保留原 actions，从前一轮结果重新运行完整 WorldUpdater + Narrators，并覆盖目标轮输出。
- `/rollback N` 物理删除 N 之后的回合数据，恢复 Round N 的 world，并从 N+1 的 EDITING 状态继续。
- 未完成且非 `WAITING_INPUT` 的 round 在恢复时完整重跑，不从中间 stage 部分续跑。
- WorldUpdater、PlayerViewGenerator 或 Narrator 任一步失败，都保留本回合原 actions
  和 PROCESSING 状态。房主明确 retry 时从基础 world 完整重跑；玩家不能在失败后
  修改行动重新提交。没有无限自动重试，也不保证请求级 exactly-once。
- 时间线始终是单一线性历史，不使用 revision/branch/event sourcing。

## Legacy 单局运行

平台模式由 RoomManager 创建 Runtime，owner 从 Platform metadata 显式注入；不会用
legacy `game.db` owner 决定权限。GameServer 不读取 platform.db，也不知道 Room Code。

仍保留显式开发入口：

```bash
python -m server.main --game <legacy-game-or-scenario> --owner <username>
```

这是兼容/开发命令，不是正常平台启动流程，也不再提供单独的 Host endpoint：
`GameServer.host_handler` 与 `HOST_WS_*` 已随旧 Host TUI 一起删除。房主能力
（角色分配 / retry / rollback / close-room）现在完全通过公共 `/ws` 上的
`is_host` 检查完成；Public endpoint 仍然拒绝 `join_host`。

## 测试

```bash
python -m unittest discover -v
```

测试覆盖动态 2/3/4-role、Room Chat、Web transport、身份恢复、retry、rollback、recovery 与 AI mock flow。
