"""配置管理"""
import os


DATABASE_URL = os.getenv(
    "FASTAPI_DATABASE_URL",
    "sqlite+aiosqlite:///./data/agentsecurity.db",
)

# Dify API 认证 Token（可选，用于鉴权）
DIFY_API_TOKEN = os.getenv("FASTAPI_DIFY_API_TOKEN", "")

# 微信桥接服务地址
WECHAT_BRIDGE_URL = os.getenv(
    "WECHAT_BRIDGE_URL",
    "http://127.0.0.1:3456/send",
)

# Dify Workflow API（用于新数据回调）
DIFY_WORKFLOW_URL = os.getenv(
    "DIFY_WORKFLOW_URL",
    "http://localhost/v1/workflows/run",
)
DIFY_API_KEY = os.getenv("DIFY_API_KEY", "app-28N0YGgRXrCzuHUgPmA2i2bu")
