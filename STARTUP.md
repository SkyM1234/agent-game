# 项目启动说明（Windows / PowerShell）

本文适用于当前项目工作区 `D:\Python_Project\agent-game`，使用已有 Conda 环境 `llm`。

## 1. Node.js 是否需要单独启动？

需要安装 Node.js，但不需要手动启动常驻 Node 服务。

启动 Python 后端后，在模型对局第一次决策时，后端会自动启动 Node 子进程运行 Pi。每场模型对局对应一个 Node 进程，内部运行两个独立的 Agent loop；对局结束或取消后自动清理。

双方需要决策时，协调器收齐双方动作再结算；角色冷却期间，按引擎规则等待下一次可行动时机。

不要把 `node scripts/pi_battle.mjs` 当作独立服务启动，它通过标准输入、输出与 Python 通信。

## 2. 环境要求

| 组件 | 要求 |
| --- | --- |
| Python | 3.11 或以上 |
| Node.js | 22.19 或以上，当前环境的 v24.4.1 可用 |
| npm | 随 Node.js 安装 |
| Conda | 本文使用已有环境 `llm` |
| 数据库 | 默认 SQLite；配置 MySQL 时需自行启动 MySQL |

在 PowerShell 中执行：

```powershell
Set-Location D:\Python_Project\agent-game
conda activate llm

python --version
node --version
npm --version
```

## 3. 安装依赖（首次准备或依赖变更后）

在项目根目录执行：

```powershell
python -m pip install -e ".[web]"

npm --prefix pi-main install --ignore-scripts --workspace packages/agent --workspace packages/ai

npm --prefix frontend ci
```

当前工作区的 Pi 依赖与构建产物已经准备好，日常启动无需重新安装或构建 Pi。

如果迁移到全新工作区，除了安装依赖，还需要准备 Pi 的构建产物；请参照 [Pi 开发说明](pi-main/README.md) 的构建步骤。游戏桥接脚本依赖 `pi-main/packages/ai/dist/index.js`，仅复制源码不足以运行。

## 4. 配置模型和数据库

配置文件是项目根目录的 `.env`。若不存在，可执行以下命令创建；已有文件会保留：

```powershell
if (!(Test-Path .env)) {
    Copy-Item .env.example .env
}
```

用编辑器打开 `.env`，填写模型配置：

```dotenv
DEEPSEEK_API_KEY=你的实际密钥
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-v4-flash
DEEPSEEK_TIMEOUT_SECONDS=20
DEEPSEEK_MAX_TOKENS=512
DEEPSEEK_TEMPERATURE=0.5
DEEPSEEK_MAX_CONSECUTIVE_FAILURES=3
```

模型配置从根目录 `.env` 读取。真实模型对局会调用该配置对应的 API。

### 使用默认 SQLite

`.env.example` 包含一条 MySQL 示例连接。如果不使用 MySQL，请删除或注释 `.env` 中的 `ARENA_DATABASE_URL=...` 行，例如：

```dotenv
# ARENA_DATABASE_URL=mysql+pymysql://arena:password@127.0.0.1:3306/agent_arena
```

同时确保系统环境变量中没有设置 `ARENA_DATABASE_URL`，因为环境变量优先于 `.env`。默认数据库文件为 `data/index.sqlite3`，无需额外启动数据库服务。

### 使用 MySQL

先启动 MySQL，再将 `.env` 中的连接改成实际地址、用户名和密码：

```dotenv
ARENA_DATABASE_URL=mysql+pymysql://arena:实际密码@127.0.0.1:3306/agent_arena
```

账户需要具备项目初始化数据库和数据表所需的权限。

## 5. 构建前端并启动完整项目

在项目根目录、已激活 `llm` 环境的终端中执行：

```powershell
npm --prefix frontend run build

python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

保持终端运行，在浏览器打开：

<http://127.0.0.1:8000>

后端同时提供 API 和构建后的前端页面，因此日常运行只需要这个终端。

在页面中把双方策略选择为“纯 DeepSeek”，配置角色等对局参数后发起对局，即可运行两个模型 Agent 的对战。Node/Pi 会在首次模型决策时自动启动。

## 6. 日常启动最短命令

依赖安装、`.env` 配置和前端构建完成后，每次启动执行：

```powershell
Set-Location D:\Python_Project\agent-game
conda activate llm
python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

浏览器访问 <http://127.0.0.1:8000>。按 `Ctrl+C` 停止后端。

如果当前 PowerShell 无法使用 `conda activate`，也可从项目根目录执行：

```powershell
conda run --no-capture-output -n llm python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

## 7. 前端开发模式（可选）

需要边修改前端边查看效果时，使用两个终端。

终端一启动后端：

```powershell
Set-Location D:\Python_Project\agent-game
conda activate llm
python -m uvicorn backend.api.app:app --host 127.0.0.1 --port 8000
```

终端二启动 Vite：

```powershell
Set-Location D:\Python_Project\agent-game
npm --prefix frontend run dev
```

浏览器打开 Vite 终端显示的地址，默认是 <http://127.0.0.1:5173>。前端的 `/api` 请求和 WebSocket 会代理到 `http://127.0.0.1:8000`，因此后端仍需运行。

Vite 用于前端开发，Pi 的 Node 子进程仍由 Python 自动管理。

## 8. 修改后如何生效

| 修改内容 | 操作 |
| --- | --- |
| Python 后端、Pi 桥接脚本 | 结束当前对局，停止并重新启动后端 |
| 根目录 `.env` | 停止并重新启动后端 |
| 前端代码，访问 8000 | 重新执行 `npm --prefix frontend run build`，刷新页面；若此前没有构建产物，还需重启后端 |
| 前端代码，使用 Vite 开发模式 | 通常自动刷新 |
| 项目依赖 | 重新安装对应依赖，再重启服务 |

## 9. 常见问题

- **页面提示 `Frontend not built`：** 在项目根目录执行 `npm --prefix frontend run build`，然后重启后端。
- **找不到 `node` 或模型对局提示运行时不可用：** 检查 `node --version`，确认版本符合要求、依赖和 Pi 构建产物齐全；安装 Node.js 后重新打开终端再启动后端。
- **出现数据库连接失败：** 检查 `.env` 和系统环境变量中的 `ARENA_DATABASE_URL`；使用 MySQL 时确认数据库服务已启动。
- **模型策略不可用或调用失败：** 检查根目录 `.env` 中的密钥、地址、模型名称及后端日志，修改配置后重启后端。
- **8000 端口被占用：** 关闭此前启动的项目服务，或使用其他端口；开发模式的 Vite 代理默认连接 8000，改端口时需同步修改 `frontend/vite.config.ts`。
