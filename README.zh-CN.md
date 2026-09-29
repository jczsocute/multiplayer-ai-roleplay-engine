# AI RP Engine

<p align="center">
  <a href="./README.md">English</a> · 简体中文
</p>

AI RP Engine 是轻量级、可自行部署的多人 AI 角色扮演平台。多名玩家以不同角色参与同一条持续发展的剧情；系统维护共享的客观世界状态，再依据各角色知道的信息分别生成叙事。

<p align="center">
  <img src="docs/images/lobby-mobile.webp" width="30%" alt="手机端大厅与创建房间" />
  <img src="docs/images/story-mobile.webp" width="30%" alt="角色剧情与可选状态栏" />
  <img src="docs/images/chat-mobile.webp" width="30%" alt="房间聊天" />
</p>

<p align="center">
  <sub>大厅 · 角色视角剧情 · 房间聊天</sub>
</p>

## 主要能力

- **Web 多人房间**：通过房间码创建或加入房间，可设置密码；房主分配角色，观众可查看剧情；多个房间可以同时运行。
- **长期存档与重连**：关闭房间仍保留 Game Save。断线宽限期内保留成员身份和座位，可返回原房间。
- **动态角色与独立叙事**：当前部署支持 2–4 个角色。WorldUpdater 统一推进世界，并生成每个角色的内部视角及可选状态；Narrator 只根据对应角色能知道的信息写出该角色的文段。
- **独立房间聊天**：聊天与在线状态不进入 AI 剧情上下文或存档的故事历史。
- **剧本创作**：基础 Web 编辑器可填写世界、角色、开场等内容；可通过 ZIP 导入、导出完整剧本，在本地编辑高级 Schema 与 Prompt。
- **重试、回滚与历史导出**：AI 回合失败后由房主重试；剧情采用单一线性时间线。存档所有者可下载含世界状态、行动、叙事等内容的 JSON ZIP。
- **自行部署**：使用兼容 OpenAI API 的 LLM 服务、SQLite 和仅供本机使用的平台 Admin 控制台。

<p align="center">
  <img src="docs/images/host-controls.webp" width="850" alt="房间成员、角色分配、重试和回滚管理界面" />
</p>

<p align="center"><sub>房主管理</sub></p>

## 工作方式

```text
用户 → 剧本 →（内容快照）存档 →（激活）房间 → GameServer
```

剧本可重复使用；创建存档时会复制一份独立 payload，之后修改原剧本不会改变已有存档。房间是存档的在线入口，关闭房间不会删除存档；每个活跃房间都有独立的 GameServer。

```text
玩家行动
    ↓
WorldUpdater
    ↓
世界状态 + 各角色视角 / 可选状态
    ↓
Narrator × N
    ↓
各角色独立剧情
```

`world_state` 是客观世界的权威状态。Narrator 只读取对应角色的人设、视角、可选状态和有限的剧情历史。玩家界面展示开场、行动、叙事和启用的状态栏；WorldUpdater 生成的角色视角只作为 Narrator 的内部输入。

## 快速开始

需要 Python 3.11+。仓库已包含生产版 `web/dist/`，正常运行平台不需要安装 Node.js。

```bash
pip install -r requirements.txt
cp .env.example .env
# 在 .env 中配置 LLM_API_KEY，供游戏房间调用兼容 OpenAI API 的服务。
python -m server.main
```

打开 `http://127.0.0.1:8080/`，注册并登录，然后在大厅使用可用剧本或已有存档创建房间，也可以输入房间码加入。房主在 Web 中分配角色。空平台启动不要求 LLM key，但创建或恢复可游玩的房间时需要有效配置；如使用其他兼容服务，可调整 `.env` 中的 `LLM_BASE_URL` 和 `LLM_MODEL`。

平台不会自动创建管理员。注册账号后，如需本机平台管理，可执行：

```bash
python -m server.main --set-admin <用户名>
python client/admin.py
```

## 剧本系统

`templates/default/` 是创建新剧本的基础 scaffold。剧本由 `metadata.json`（`count`、`names`、`title`、`introduction`、`tags`）、世界文字与状态示例、编号的 `characters/1..N/` 角色目录及 AI Prompt 组成；角色状态栏可选。`*_schema.json` 是给 LLM 的字段示例和说明，不是标准 JSON Schema。

基础 Web 编辑器负责标题、简介、标签、世界设定、角色人设、开场白和 AI 写作要求，保存时保留高级 Schema 与 Prompt。需要调整高级文件时，可下载剧本 ZIP，在本地修改后将合法 ZIP 导入为自己的新剧本，或替换已有剧本。更早的 `players/`、`statusbar/` 目录布局不会自动转换，详见 [Platform 文档](server/platform/README.md)。

## 项目结构

| 路径 | 用途 |
| --- | --- |
| `server/platform/` | 账号、剧本与存档目录、房间、Admin 和 Web API |
| `server/gameserver/` | 单个存档的回合、状态、协议、持久化与 AI 流程 |
| `web/` | React + TypeScript 客户端；`dist/` 为已提交的生产构建 |
| `client/admin.py` | 仅供本机使用的平台 Admin 控制台 |
| `templates/default/` | 仓库保留的默认剧本 scaffold |
| `tests/` | Python 测试及人工合成 fixture |

`data/`、`games/` 和非默认 `templates/` 是本机运行数据或用户内容，不提交到 Git。

## 开发

```bash
python -m unittest discover
cd web
npm ci
npx vitest run
npx tsc -b
npm run build
```

修改 Web 源码后应重建并同步 `web/dist/`。

## 详细文档

- [Platform 文档](server/platform/README.md)：账号、资源、房间、Web API、Admin 与导入。
- [GameServer 文档](server/gameserver/README.md)：剧本格式、协议、回合、AI 流程、重试与恢复。
- [开发与 Agent 约定](AGENTS.md)：代码边界和编辑规则。
- [计划与既定决策](TODO.md)。
- [English README](README.md)。

## 许可证

本项目采用 [MIT License](./LICENSE)。
