# AI RP Engine

轻量级多人 AI 角色扮演平台。用户在 Web 注册、登录并进入大厅，使用剧本创建长期存档，再开启或加入房间。多个房间各自运行独立的 GameServer；关闭房间后存档仍可继续使用。

## 主要能力

- Account：稳定用户身份、登录与会话。
- Platform：剧本、存档和房间的归属、公开目录与管理。
- 剧本：从 `templates/default/` 创建，也可在 Web 上传、下载符合当前格式的 ZIP；创建存档时复制为独立快照。
- Room：房间码、可选密码、成员与观众、断线宽限期；多个房间可同时运行。
- Web：大厅、活跃房间和剧本广场；创建或加入房间、用基础编辑器填写剧本、管理我的存档、分配角色和游玩。
- Admin：本机 `client/admin.py` 管理用户、剧本与房间。
- GameServer：动态角色、按角色视角生成叙事、回合恢复、retry 和线性 rollback；存档 owner 可下载完整历史 ZIP。

资源关系：`User → 剧本 →（快照）存档 →（激活）Room → GameServer`。用户拥有自己的剧本和存档；公开剧本可供其他用户创建自己的存档。

## 快速开始

需要 Python 3.11+。按需填写 `.env` 中的 LLM 配置；空平台启动本身不要求 LLM key，创建或恢复房间时需要可用的 LLM 配置。

```bash
pip install -r requirements.txt
cp .env.example .env
python -m server.main
```

打开 `http://127.0.0.1:8080/`，注册并登录，在大厅从已有存档或可用剧本创建房间，或用房间码加入。房主在 Web 分配角色。普通部署使用已提交的 `web/dist/`，无需安装 Node.js。
刷新大厅后，仍在断线宽限期内的成员可通过“返回房间”恢复连接；“离开房间”会立即释放角色和座位。

平台不会自动设置管理员。注册账号后，可在本机执行：

```bash
python -m server.main --set-admin <用户名>
python client/admin.py
```

如需导入本机已有的剧本目录，参见 [Platform 文档](server/platform/README.md)。`templates/default/` 是创建剧本的 scaffold，不是可直接游玩的剧本。新建剧本后会直接打开基础编辑器；也可从“我的剧本”进入编辑页。高级内容可通过剧本 ZIP 下载、修改后再导入。
玩家故事界面只显示开场、行动、叙事及剧本启用的角色状态栏；WorldUpdater 生成的角色视角只作为 Narrator 输入。房主可从管理面板或“我的存档”下载已完成回合的历史 ZIP，查看世界状态与角色信息；处理中需等待本轮完成。

## 仓库结构

| 路径 | 用途 |
| --- | --- |
| `server/platform/` | 账号、剧本与存档目录、房间路由、Admin 和 Web API |
| `server/gameserver/` | 单个存档的状态、角色、回合、协议与 AI pipeline |
| `web/` | React + TypeScript 用户客户端；`dist/` 为提交的生产构建 |
| `client/admin.py` | 本机 Platform Admin Console |
| `templates/default/` | 唯一提交的剧本 scaffold |
| `tests/` | Python 测试和合成 fixture |

`data/`、`games/` 与非默认 `templates/` 是本机运行数据，不提交到 Git。

## 开发与详细文档

```bash
python -m unittest discover
cd web && npx vitest run && npx tsc -b && npm run build
```

- [Platform Core](server/platform/README.md)：配置、认证、资源模型、Room 生命周期、API、Admin 与导入。
- [GameServer Runtime](server/gameserver/README.md)：单局 payload、协议、回合、AI pipeline、恢复与兼容入口。
- [AGENTS.md](AGENTS.md)：开发和协作约束。
- [TODO.md](TODO.md)：未来计划与已完成事项。
