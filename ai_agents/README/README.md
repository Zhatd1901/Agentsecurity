# Agentsecurity

数据流:
Twilio 接入电话 ↔ STT/TTS 语音交互 ↔ Dify 收集访客信息 → FastAPI 写入 SQLite → 电话挂断 → Workflow 推送微信确认 → 管理员确认后更新 DB

---

## 项目架构总览
┌──────────┐    SIP/WebRTC     ┌──────────────────┐    HTTP/SSE     ┌──────────────┐
│  Twilio  │ ◄──────────────► │  TEN Framework    │ ◄─────────────► │ Dify Chatflow│
│ 电话接入 │                  │ voice-assistant   │                │ 访客信息收集 │
└──────────┘                   └──────┬───────────┘                └──────┬───────┘
                                      │                                    │
                                      │ Go API Server                      │ HTTP POST
                                      │ localhost:8080                     │ /api/visitors/create
                                      ▼                                    ▼
                              ┌──────────────┐                  ┌─────────────────┐
                              │  Playground  │                  │ FastAPI 中间件   │
                              │ Next.js UI   │                  │ localhost:8000   │
                              │ port:3000    │                  │ Dify↔DB↔Workflow │
                              └──────────────┘                  └───────┬─────────┘
                                                                         │
                                                                         │ SQLite
                                                                         ▼
                                                                ┌────────────────┐
                                                                │   SQLite DB    │
                                                                │ visitors 表    │
                                                                │ status 队列    │
                                                                └───────┬────────┘
                                                                        │
                                                                        │ create 成功后触发
                                                                        │ action=new_visitor_created
                                                                        ▼
                                                                ┌────────────────┐
                                                                │ Dify Workflow  │
                                                                │ 队列调度/推送  │
                                                                └───────┬────────┘
                                                                        │
                                                                        │ HTTP POST
                                                                        │ /api/wechat/send
                                                                        ▼
┌──────────┐    ilink API      ┌──────────────────┐    HTTP/API      ┌─────────────────┐
│ 微信 App │ ◄──────────────► │  WeChat Bridge    │ ◄─────────────► │ FastAPI / Clawbot│
│ 个人微信 │                  │ Node.js 桥接      │                │ 微信发送与回调   │
└──────────┘                   └──────────────────┘                └─────────────────┘

---

## 组件清单与启动方式

### 方式一：Docker Compose 一键启动（推荐）

```bash
cd ai_agents
cp .env.example .env   # 编辑 .env 填入 API Key
docker compose up -d
```

启动后：
| 服务 | 容器名 | 端口 | 说明 |
|------|--------|------|------|
| TEN Agent Dev | `ten_agent_dev` | 3000, 8080, 9000, 49483 | TEN 运行时 + Playground + Graph Designer |
| FastAPI | `agentsecurity_fastapi` | 8000 | Dify ↔ SQLite 中间件 |

在 `ten_agent_dev` 容器内手动运行 agent:
```bash
docker exec -it ten_agent_dev bash
cd /app/agents/examples/voice-assistant && task run        # 默认语音助手
cd /app/agents/examples/voice-assistant-sip-twilio && task run  # Twilio SIP 版本
```

---

### 方式二：各组件独立启动

#### 1. TEN Framework（核心运行时）

TEN Framework 包含三个子服务：

##### 1a. Go API Server（服务器进程管理器）

负责 agent 进程的启动/停止/心跳管理。

```bash
cd ai_agents/server
go mod tidy && go mod download
go build -o bin/api main.go
./bin/api -tenapp_dir=../agents/examples/voice-assistant/tenapp
# 默认端口: 8080 (由 SERVER_PORT 环境变量控制)
```

##### 1b. TMAN Graph Designer（可视化图编辑器）

```bash
cd ai_agents/agents/examples/voice-assistant/tenapp
tman designer
# 默认端口: 49483 (由 GRAPH_DESIGNER_SERVER_PORT 环境变量控制)
```

##### 1c. Playground 前端（Next.js Web UI）

```bash
cd ai_agents/playground
bun install
bun run dev
# 默认端口: 3000
```

> **一键启动脚本**: 进入具体 agent 目录后执行 `task run`:
> ```bash
> cd ai_agents/agents/examples/voice-assistant
> task install   # 首次运行需安装依赖
> task run       # 同时启动 tman designer + playground + Go API server
> ```

---

#### 2. FastAPI 中间件（Dify ↔ 数据库桥梁）

```bash
cd ai_agents/fastapi

# 安装依赖
pip install -r requirements.txt

# 启动服务
uvicorn main:app --host 0.0.0.0 --port 8000 --reload
# 或
python -m uvicorn main:app --host 0.0.0.0 --port 8000 --reload
```

启动后访问:
- API 文档: `http://localhost:8000/docs`
- 健康检查: `http://localhost:8000/health`

环境变量:
| 变量 | 默认值 | 说明 |
|------|--------|------|
| `FASTAPI_DATABASE_URL` | `sqlite+aiosqlite:///./data/agentsecurity.db` | 数据库连接串 |
| `FASTAPI_DIFY_API_TOKEN` | (空) | Dify API 认证 Token |

---

#### 3. WeChat Bridge（微信 ↔ Dify 桥接服务）

```bash
cd ai_agents/wechat

# 安装依赖并编译
npm install

# 扫码登录并启动
npm start
# 或
node dist/main.js

# 仅配置模式（修改 Dify API 等参数）
npm run setup
# 或
node dist/main.js setup
```

启动后进入交互式 CLI，按提示操作:
1. 扫码绑定微信（生成二维码 → 微信扫码 → 获取 bot_token）
2. 配置 Dify API Key / Base URL
3. 启动消息轮询，自动转发微信消息到 Dify

HTTP API（桥接进程提供）:
| 端点 | 方法 | 说明 |
|------|------|------|
| `/send` | POST | Dify 向微信推送消息 `{ toUserId, text }` |
| `/health` | GET | 健康检查 |

环境变量:
| 变量 | 默认值 | 说明 |
|------|--------|------|
| `WCD_DATA_DIR` | `~/.wechat-dify-bridge` | 数据存储目录（token/session） |

---

## 功能模块

### Twilio 通话接入模块
Twilio API — 通过 SIP/WebRTC 接入电话，支持 voice-assistant-sip-twilio agent。

### 语音处理模块（STT/TTS）
- STT: Deepgram
- TTS: 科大讯飞

### Dify LLM 模块
中转站: gpt-5.4

### SQL 数据存储模块
SQLite — 通过 FastAPI 中间件读写，Dify Workflow 通过 HTTP Request 节点调用。

### 微信通知模块
WeChat Bridge — Node.js 服务，接收 Dify 回调并推送消息到个人微信。

### 微信聊天机器人查询模块
WeChat Bridge — 双向桥接，用户在微信中 @机器人 即可与 Dify 对话。

### Web 后台数据可视化模块
Playground (Next.js) — TEN Agent 的可视化管理界面，支持实时对话、图编辑器。

---

## 完整开发环境搭建

```bash
# 1. 克隆仓库
git clone <repo-url> && cd ten-framework/ai_agents

# 2. 配置环境变量
cp .env.example .env
# 编辑 .env 填入:
#   - OPENAI_API_KEY / DIFY_API_KEY
#   - AGORA_APP_ID (可选，用于 RTC)
#   - NGROK_AUTHTOKEN (可选，用于公网穿透)

# 3. 安装 TEN Framework 依赖并启动
cd agents/examples/voice-assistant
task install
task run

# 4. 另起终端，启动 FastAPI
cd ai_agents/fastapi
pip install -r requirements.txt
uvicorn main:app --host 0.0.0.0 --port 8000 --reload

# 5. 另起终端，启动 WeChat Bridge
cd ai_agents/wechat
npm install && npm start
```

---

## 常用命令速查

```bash
# Docker 管理
docker compose up -d          # 后台启动
docker compose down           # 停止
docker compose logs -f         # 查看日志

# TEN Agent
task install                   # 安装依赖
task run                       # 启动全部服务
task lint                      # 代码检查

# WeChat Bridge
npm start                      # 启动桥接
npm run setup                  # 修改配置

# FastAPI
uvicorn main:app --reload      # 开发模式启动
```

