# AI RP Engine

一个轻量级双人 AI 角色扮演引擎。服务器负责剧本、SQLite 状态和 DeepSeek 调用；两名玩家通过 Textual 终端客户端进入同一个游戏实例。

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
SERVER_HOST=0.0.0.0
SERVER_PORT=8765
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

### Game Instance

实际游戏位于 `games/<game_name>/`，内容包括从 Scenario Template 复制出的配置、`game.db`、当前世界状态、聊天历史及玩家状态。

不要把 `games/` 当作剧本编辑入口。创建实例后，它拥有配置文件的独立副本；以后修改原 Template 不会影响旧实例。需要应用新版 Template 时，应创建新的 Game Instance。

## 创建自己的剧本

先从基础模板创建一个可玩的 Scenario：

```bash
python -m server.main --create-scenario my_story
```

命令会完整复制 `templates/default/` 为 `templates/my_story/`，不会自动改写其中内容。然后直接编辑以下文件：

- `prompts/world_update.md`：世界管理 AI 的基本规则，负责客观世界状态、`public_information`、`player_views` 和 `player_statusbar`；不应在这里编写具体剧情。
- `prompts/narration.md`：A/B 共用的玩家文学叙事规则。
- `prompts/ai_guidelines.md`：本剧本创作规范，例如人称、文风、节奏、信息隔离强度和特殊要求，不包含具体剧情。
- `schemas/world_update_output.json`：DeepSeek JSON Output 的输出示例模板，不是标准 JSON Schema。
- `world/world.md`：具体世界观、背景、规则和剧本设定。
- `world/initial_state.json`：游戏初始客观世界状态。
- `characters/player_a.md`：Player A 的角色设定。
- `characters/player_b.md`：Player B 的角色设定。
- `characters/statusbar/player_a.json`：Player A 的状态栏结构。
- `characters/statusbar/player_b.json`：Player B 的状态栏结构。

A/B 的状态栏结构可以完全不同。

### 推荐编辑流程

普通新剧本通常只需修改：

1. `world/world.md`
2. `world/initial_state.json`
3. `characters/player_a.md`
4. `characters/player_b.md`
5. `characters/statusbar/*.json`
6. `prompts/ai_guidelines.md`

需要深度定制 AI 行为时，再修改 `prompts/world_update.md`、`prompts/narration.md` 或 `schemas/world_update_output.json`。普通剧本不一定需要修改核心 Prompt。

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

服务器会列出 `default` 以外的 Scenario。选择后会创建或恢复同名 Game Instance，并监听 WebSocket。如果没有可玩 Scenario，服务器会提示先创建剧本并正常退出。

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

## 两名玩家进入游戏

客户端连接：

```bash
python client/terminal.py --uri ws://SERVER_IP:8765 --name Chengzhe
python client/terminal.py --uri ws://SERVER_IP:8765 --name Alice
```

第一位连接者是 Host，但不会自动成为 Player A。第二位玩家加入后，Host 在 Lobby 中选择 A/B 分配；分配完成后双方进入 `EDITING`。

TUI 上方显示自己的历史、Narration、角色状态栏和公共信息；中间只显示 A/B 协作状态；底部是当前回合唯一的多行行动 Draft。

主要操作：

- `Enter`：在 Draft 中换行，不提交。
- `Ctrl+Enter`：提交当前完整 Draft，进入 `READY`；提交后内容保持可见但只读。
- `Ctrl+E`：取消提交并继续编辑原 Draft。
- `/submit`、`/cancel`：分别等同于上述两个快捷键。
- `/pause`、`/resume`：暂停或恢复编辑。
- `/status`：显示双方协作状态、本角色状态栏和公共信息。
- `/retry`：AI 阶段失败后继续缺失步骤。
- `/quit`：退出客户端。

Slash command 在 Draft 下方的独立命令栏中输入，不会成为行动正文。部分终端不能区分 `Ctrl+Enter` 与普通 Enter，此时使用 `/submit`。

双方提交后进入 `PROCESSING`。AI 完成后各自收到私有 Narration，服务器进入下一轮并清空旧 Draft。

## 断线与恢复

使用相同昵称重新连接，会恢复 Host 身份、A/B 角色、当前协作状态和未完成 Draft，并重放尚未确认的 Narration。断线不会改变底层回合状态；另一端会立即看到灰色“已离开”，重连后恢复原状态。

服务器重启时会从 SQLite 中保存的 AI 阶段继续处理，不重复已经成功完成的 World Update。

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
python client/terminal.py --uri ws://SERVER_IP:8765 --name Alice
```

纯客户端不需要 `server/`、`templates/`、`games/`、SQLite 数据库、`.env` 或 DeepSeek API Key，也不会直接调用 LLM；所有 AI 和持久化工作都由服务器完成。

## 测试

普通测试全部使用 Mock，不调用真实 API：

```bash
python -m unittest discover -v
```
