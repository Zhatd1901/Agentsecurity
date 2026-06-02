"""
FastAPI 中间件 —— Dify ↔ 数据库桥梁

数据流:
  Twilio → Dify Workflow (HTTP Request 节点) → FastAPI → SQLite/MySQL
"""
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from database import engine, Base
from routes import router

app = FastAPI(
    title="Agentsecurity Middleware",
    description="Twilio → Dify → FastAPI → Database 中间件服务",
    version="1.0.0",
)


@app.get("/")
async def root():
    return RedirectResponse("/docs")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api")


@app.on_event("startup")
async def startup():
    """启动时创建数据库表"""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


@app.get("/health")
async def health():
    return {"status": "ok", "service": "agentsecurity-middleware"}
