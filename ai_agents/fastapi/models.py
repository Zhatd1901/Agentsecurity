"""SQLAlchemy 数据模型"""
from datetime import datetime
from sqlalchemy import Column, Integer, String, DateTime, Text
from database import Base


class CallLog(Base):
    """通话记录"""
    __tablename__ = "call_logs"

    id = Column(Integer, primary_key=True, autoincrement=True)
    call_sid = Column(String(64), unique=True, index=True, comment="Twilio Call SID")
    caller_number = Column(String(32), index=True, comment="主叫号码")
    callee_number = Column(String(32), comment="被叫号码")
    direction = Column(String(16), comment="inbound/outbound")
    status = Column(String(32), comment="通话状态")
    duration = Column(Integer, default=0, comment="通话时长(秒)")
    transcript = Column(Text, comment="语音转写文本")
    ai_response = Column(Text, comment="AI 回复文本")
    created_at = Column(DateTime, default=datetime.utcnow)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)


class Visitor(Base):
    """访客登记表 —— 队列系统"""
    __tablename__ = "visitors"

    id = Column(Integer, primary_key=True, autoincrement=True)
    license_plate = Column(String(32), nullable=False, index=True, comment="车牌号")
    entry_time = Column(String(14), nullable=False, index=True, comment="到访时间 YYYYMMDDHHMMSS")
    purpose = Column(String(64), nullable=False, comment="到访事由")
    company = Column(String(128), nullable=False, index=True, comment="单位/公司")
    phone = Column(String(32), nullable=False, index=True, comment="手机号")
    status = Column(Integer, nullable=False, default=0, index=True, comment="0=待确认 1=录入完成 2=已删除")
    delete_reason = Column(String(256), nullable=True, comment="删除原因")
    deleted_at = Column(String(14), nullable=True, comment="删除时间 YYYYMMDDHHMMSS")
