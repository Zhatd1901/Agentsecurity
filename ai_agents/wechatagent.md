# WeChat Agent — 微信 AI 桥接方案

> 从ClawBot中解包出的ilink Bot 内部 API,封装成http链接dify,纯 HTTP 调用实现个人微信与 AI 后端的双向通讯。
> 原仓库https://github.com/Wechat-ggGitHub/wechat-claude-code
---

## 架构概览

```
┌──────────┐     HTTPS      ┌─────────────────┐     HTTP/SSE     ┌──────────┐
│  微信 App  │ ◄────────────► │  Node.js 桥接进程  │ ◄──────────────► │  Dify AI  │
│  (个人微信) │   ilink API    │  (wechat-bridge) │   Chat API      │  (后端)   │
└──────────┘                 └─────────────────┘                  └──────────┘
     │                              │                                  │
     │  扫码绑定 bot_type=3          │  轮询 + 发送                      │  流式对话
     │  获得 bot_token              │  sendMessage / getUpdates        │  SSE streaming
     ▼                              ▼                                  ▼
ilinkai.weixin.qq.com          src/wechat/api.ts              src/dify/provider.ts
```

**核心思路**：直接调用微信内部 ilink Bot HTTP 接口，绕过官方插件体系，用 300 行 TypeScript 实现完整双向通讯。

---

## 与 ClawBot 的关系

| | ClawBot 官方方案 | 本方案 |
|---|---|---|
| **接入层** | 微信内置插件系统 | 裸调微信 HTTP API |
| **依赖** | OpenClaw 必须运行 | 仅需 Node.js ≥18 |
| **微信版本** | 8.0.70+ | 无要求 |
| **Agent 后端** | OpenClaw → Claude | 任意兼容 API（Dify/OpenAI 等） |
| **代码来源** | 官方 npm 包 | 独立实现（协议格式参考 ClawBot） |

> API 协议格式（`types.ts`）提取自 ClawBot 插件，但实现代码完全独立，**不需要安装 ClawBot 或 OpenClaw**。

---

## 三步协议

### ① 扫码绑定

```
POST https://ilinkai.weixin.qq.com/ilink/bot/get_bot_qrcode?bot_type=3
→ 返回二维码图片
→ 用户微信扫码
→ 轮询 POST /ilink/bot/get_qrcode_status
→ 返回 bot_token + ilink_user_id
```

认证方式：`Authorization: Bearer <bot_token>` + `AuthorizationType: ilink_bot_token`

### ② 发送消息

```
POST https://ilinkai.weixin.qq.com/ilink/bot/sendmessage
Body: {
  msg: {
    from_user_id: "bot@im.bot",
    to_user_id:   "user@im.wechat",
    message_type: 2,           // BOT
    message_state: 2,          // FINISH
    context_token: "...",
    item_list: [{ type: 1, text_item: { text: "你好" } }]
  }
}
```

### ③ 接收消息（长轮询）

```
POST https://ilinkai.weixin.qq.com/ilink/bot/getupdates
Body: { get_updates_buf: "上次的sync_buf" }
→ 返回 msgs[] 数组，包含用户发送的文字/图片
```

消息类型支持：文字(TEXT=1)、图片(IMAGE=2)、语音(VOICE=3)、文件(FILE=4)、视频(VIDEO=5)

---

## 项目结构

```
ai_agents/wechat/
├── package.json
├── tsconfig.json
└── src/
    ├── main.ts              # 核心入口，消息路由 + Dify 调用
    ├── config.ts            # 配置管理（Dify API Key / Base URL）
    ├── session.ts           # 会话管理（Dify conversation_id）
    ├── constants.ts         # 常量
    ├── logger.ts            # 日志
    ├── store.ts             # JSON 文件存储
    ├── commands/
    │   ├── router.ts        # 斜杠命令路由
    │   └── handlers.ts      # /help /clear /status /prompt 等
    ├── dify/
    │   └── provider.ts      # Dify Chat API 客户端（SSE 流式解析）
    └── wechat/
        ├── api.ts           # 微信 API 封装（sendMessage / getUpdates）
        ├── login.ts         # 二维码扫码绑定
        ├── monitor.ts       # 消息轮询 + 指数退避重试
        ├── send.ts          # 消息发送（含限频保护）
        ├── media.ts         # 图片下载/解析
        ├── accounts.ts      # 账号凭证持久化
        ├── types.ts         # 协议类型定义（提取自 ClawBot）
        ├── cdn.ts           # CDN 资源下载
        ├── crypto.ts        # 加密工具
        └── sync-buf.ts      # 增量同步缓冲区
```

---

## 数据流

```
1. 微信用户发消息
       │
2. monitor.ts 轮询 getUpdates() 拉取
       │
3. main.ts handleMessage() 路由判断
       ├── /开头 → commands/handlers.ts 命令处理
       └── 普通文本 → sendToDify()
              │
4. dify/provider.ts 调 Dify Chat API
       │  POST /v1/chat-messages (response_mode: streaming)
       │  解析 SSE 事件流
       │
5. onText 回调 → 缓冲 → send.ts sendMessage()
       │  限频保护：最长36秒内只发一条
       │
6. 微信用户收到 AI 回复
```

---

## 微信端命令

| 命令 | 功能 |
|------|------|
| `/help` | 显示帮助 |
| `/clear` | 清除当前会话（新 Dify conversation） |
| `/reset` | 完全重置所有设置 |
| `/status` | 查看会话状态 |
| `/compact` | 压缩上下文（保留历史，清除 conversation_id） |
| `/history [N]` | 查看最近 N 条对话 |
| `/undo [N]` | 撤销最近 N 条对话 |
| `/prompt [内容]` | 设置系统提示词（/prompt clear 清除） |
| `/cwd [路径]` | 查看/切换工作目录 |

---

## 配置

### 环境变量

```powershell
# Dify API Key（必填）
$env:DIFY_API_KEY = "app-xxxxxxxxxxxxx"

# Dify 服务地址（选填，默认 https://api.dify.ai）
$env:DIFY_BASE_URL = "https://your-dify-server.com"

# 数据目录（选填，默认 ~/.wechat-dify-bridge）
$env:WCD_DATA_DIR = "D:\wechat-data"
```

### 配置文件（`~/.wechat-dify-bridge/config.env`）

```ini
difyApiKey=app-xxxxxxxxxxxxx
difyBaseUrl=https://api.dify.ai
systemPrompt=用中文回答
```

---

## 使用

```powershell
cd ai_agents/wechat

# 首次：扫码绑定微信
npm run setup

# 启动桥接服务
npm start
```

---

## 关键设计决策

| 决策 | 原因 |
|------|------|
| 纯 HTTP 调用，不依赖微信插件 | 避免版本限制和复杂依赖 |
| 长轮询 35 秒超时 | 微信 API 限制，超时自动重试 |
| 指数退避重试（3s → 30s） | 应对微信限频（ret: -2） |
| 流式缓冲 36 秒间隔 | 避免频繁发消息触发微信限频 |
| SSE 流式解析 | Dify streaming 模式，逐字推送体验更好 |
| conversation_id 持久化 | 跨消息保持 Dify 对话上下文 |
| session.json 存会话状态 | 进程重启后恢复对话 |

---

## 安全性

- `bot_token` 存在 `~/.wechat-dify-bridge/accounts/` 下，仅限本地文件权限
- API 请求限定 `weixin.qq.com` / `wechat.com` 域名
- 消息 ID 做去重，防止重复处理
- 账号 ID 强制白名单校验（防路径穿越）
