"""Pydantic 请求/响应 Schema"""
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, Field


# ========== 微信推送 ==========

class WechatSendRequest(BaseModel):
    """Dify → FastAPI → 微信: 推送消息"""
    text: str = Field(..., description="要发送的消息文本")


# ========== 通话记录 ==========

class CallLogCreate(BaseModel):
    """Dify → FastAPI: 创建通话记录"""
    call_sid: str = Field(..., description="Twilio Call SID")
    caller_number: Optional[str] = None
    callee_number: Optional[str] = None
    direction: Optional[str] = "inbound"
    status: Optional[str] = None
    duration: Optional[int] = 0
    transcript: Optional[str] = None
    ai_response: Optional[str] = None


class CallLogResponse(BaseModel):
    id: int
    call_sid: str
    caller_number: Optional[str]
    status: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


# ========== 访客队列 ==========

class VisitorCreateRequest(BaseModel):
    """Dify → FastAPI: 创建访客"""
    license_plate: str = Field(..., description="车牌号")
    entry_time: str = Field(..., description="到访时间 YYYYMMDDHHMMSS")
    purpose: str = Field(..., description="到访事由")
    company: str = Field(..., description="单位/公司")
    phone: str = Field(..., description="手机号")


class VisitorDeleteRequest(BaseModel):
    """删除队头访客"""
    delete_reason: str = Field(..., min_length=1, description="删除原因")


class DbQueryRequest(BaseModel):
    """Dify → FastAPI: LLM 直接查询 SQLite"""
    sql: str = Field(..., description="SELECT 查询语句")


class VisitorResponse(BaseModel):
    id: int
    license_plate: str
    company: str
    phone: str
    purpose: str
    entry_time: datetime

    class Config:
        from_attributes = True


class VisitorQuery(BaseModel):
    """LLM 查询访客记录的条件（所有字段可选，AND 组合）"""
    license_plate: Optional[str] = None
    company: Optional[str] = None
    phone: Optional[str] = None
    purpose: Optional[str] = None
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    limit: int = Field(50, le=200)


# ========== 通用 ==========

class APIResponse(BaseModel):
    success: bool = True
    message: str = ""
    data: Optional[dict] = None
