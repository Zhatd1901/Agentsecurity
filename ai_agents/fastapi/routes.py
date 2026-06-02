"""API 路由 —— Dify HTTP Request 节点调用的端点"""
from datetime import datetime
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
import httpx

from config import WECHAT_BRIDGE_URL, DIFY_WORKFLOW_URL, DIFY_API_KEY
from database import get_db
from models import CallLog, Visitor
from schemas import (
    CallLogCreate, CallLogResponse,
    APIResponse,
    VisitorCreateRequest, VisitorDeleteRequest,
    WechatSendRequest, DbQueryRequest,
)

router = APIRouter()


# ═══════════════════════════════════════════════
# 微信推送 (Dify HTTP Request → FastAPI → 微信)
# ═══════════════════════════════════════════════

@router.post("/wechat/send", response_model=APIResponse)
async def wechat_send(body: WechatSendRequest):
    """Dify 推送消息到管理员微信"""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.post(WECHAT_BRIDGE_URL, json={"text": body.text})
            resp.raise_for_status()
            return APIResponse(message="微信消息已发送")
        except httpx.HTTPError as e:
            raise HTTPException(status_code=502, detail=f"微信服务不可达: {e}")


# ═══════════════════════════════════════════════
# SQLite 直查 (Dify LLM 节点 → 数据库查询)
# ═══════════════════════════════════════════════

from sqlalchemy import text as sa_text

@router.post("/db/query")
async def db_query(body: DbQueryRequest, db: AsyncSession = Depends(get_db)):
    """LLM 直接查询 SQLite（仅允许 SELECT）"""
    sql = body.sql.strip()

    if not sql.upper().startswith('SELECT'):
        return {"success": False, "message": "Only SELECT queries are allowed"}

    try:
        result = await db.execute(sa_text(sql))
        rows = result.fetchall()
        columns = list(result.keys())
        data = [dict(zip(columns, [str(v) for v in row])) for row in rows]
        return {"success": True, "rows": len(data), "columns": columns, "data": data}
    except Exception as e:
        return {"success": False, "message": str(e)}


# ═══════════════════════════════════════════════
# 通话记录 (Dify HTTP Request → GET/POST)
# ═══════════════════════════════════════════════

@router.post("/call-logs", response_model=APIResponse)
async def create_call_log(body: CallLogCreate, db: AsyncSession = Depends(get_db)):
    """Dify 通话开始时调用，保存通话记录"""
    call = CallLog(**body.model_dump())
    db.add(call)
    await db.commit()
    return APIResponse(message="通话记录已创建", data={"id": call.id})


@router.get("/call-logs/{call_sid}", response_model=APIResponse)
async def get_call_log(call_sid: str, db: AsyncSession = Depends(get_db)):
    """Dify 查询通话记录"""
    result = await db.execute(select(CallLog).where(CallLog.call_sid == call_sid))
    call = result.scalar_one_or_none()
    if not call:
        raise HTTPException(status_code=404, detail="通话记录不存在")
    return APIResponse(data={
        "caller_number": call.caller_number,
        "status": call.status,
        "duration": call.duration,
        "transcript": call.transcript,
        "ai_response": call.ai_response,
    })


@router.get("/call-logs", response_model=APIResponse)
async def list_call_logs(
    caller_number: Optional[str] = Query(None),
    limit: int = Query(20, le=100),
    db: AsyncSession = Depends(get_db),
):
    """按号码查询历史通话"""
    q = select(CallLog).order_by(CallLog.created_at.desc()).limit(limit)
    if caller_number:
        q = q.where(CallLog.caller_number == caller_number)
    result = await db.execute(q)
    calls = result.scalars().all()
    return APIResponse(data={
        "total": len(calls),
        "items": [{"call_sid": c.call_sid, "caller": c.caller_number, "status": c.status, "time": c.created_at.isoformat()} for c in calls]
    })


# ═══════════════════════════════════════════════
# 访客登记队列系统
# ═══════════════════════════════════════════════

import re
import asyncio


def _now_compact() -> str:
    """返回当前时间 YYYYMMDDHHMMSS 字符串"""
    return datetime.utcnow().strftime("%Y%m%d%H%M%S")


def _get_queue_head_query():
    """返回队头查询：status=0, ORDER BY entry_time ASC, id ASC"""
    return (
        select(Visitor)
        .where(Visitor.status == 0)
        .order_by(Visitor.entry_time.asc(), Visitor.id.asc())
        .limit(1)
    )


@router.post("/visitors/create")
async def visitor_create(body: VisitorCreateRequest, db: AsyncSession = Depends(get_db)):
    """创建访客记录"""
    data = body.model_dump()

    if not re.match(r'^\d{14}$', data["entry_time"]):
        return {"success": False, "message": "entry_time must be 14-digit string (YYYYMMDDHHMMSS)"}

    visitor = Visitor(**data, status=0)
    db.add(visitor)
    await db.commit()
    await db.refresh(visitor)

    # 异步通知 Dify：新访客创建
    async def _notify_dify():
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.post(DIFY_WORKFLOW_URL, json={
                    "inputs": {
                        "action": "new_visitor_created",
                        "query": "",
                        "user_id": "fastapi",
                    },
                    "response_mode": "blocking",
                    "user": "fastapi",
                }, headers={"Authorization": f"Bearer {DIFY_API_KEY}"})
                print(f"[notify_dify] status={resp.status_code}, body={resp.text[:200]}")
        except Exception as e:
            print(f"[notify_dify] FAILED: {e}")

    asyncio.create_task(_notify_dify())

    return {
        "success": True,
        "message": "visitor created",
        "visitor_id": visitor.id,
        "status": visitor.status,
    }


@router.get("/visitors/queue/head")
async def visitor_queue_head(db: AsyncSession = Depends(get_db)):
    """查询队头 — 新访客通知推送专用"""
    count_result = await db.execute(
        select(Visitor).where(Visitor.status == 0)
    )
    pending = count_result.scalars().all()
    pending_count = len(pending)
    waiting_count = max(pending_count - 1, 0)

    result = await db.execute(_get_queue_head_query())
    head = result.scalar_one_or_none()

    if head:
        return {
            "license_plate": head.license_plate,
            "entry_time": head.entry_time,
            "purpose": head.purpose,
            "company": head.company,
            "phone": head.phone,
            "pending_count": str(pending_count),
            "waiting_count": str(waiting_count),
        }
    else:
        return {
            "license_plate": "-1",
            "entry_time": "-1",
            "purpose": "-1",
            "company": "-1",
            "phone": "-1",
            "pending_count": "0",
            "waiting_count": "0",
        }


def _visitor_to_dict(v: Visitor, prefix: str = "") -> dict:
    """将 Visitor 对象转为 dict，key 带可选前缀"""
    return {
        f"{prefix}license_plate": v.license_plate,
        f"{prefix}entry_time": v.entry_time,
        f"{prefix}purpose": v.purpose,
        f"{prefix}company": v.company,
        f"{prefix}phone": v.phone,
    }

_NEXT_DEFAULTS = {
    "next_license_plate": "-1",
    "next_entry_time": "-1",
    "next_purpose": "-1",
    "next_company": "-1",
    "next_phone": "-1",
}


@router.post("/visitors/queue/confirm")
async def visitor_queue_confirm(db: AsyncSession = Depends(get_db)):
    """确认当前队头，返回【已确认访客】+【下一条】(或 -1)"""
    result = await db.execute(_get_queue_head_query())
    head = result.scalar_one_or_none()

    if not head:
        return {
            "license_plate": "-1",
            "entry_time": "-1",
            "purpose": "-1",
            "company": "-1",
            "phone": "-1",
            "pending_count": "0",
            **_NEXT_DEFAULTS,
        }

    confirmed = _visitor_to_dict(head)
    head.status = 1
    await db.commit()

    # 统计剩余
    count_result = await db.execute(
        select(Visitor).where(Visitor.status == 0)
    )
    pending = count_result.scalars().all()
    pending_count = len(pending)

    # 下一条
    new_result = await db.execute(_get_queue_head_query())
    new_head = new_result.scalar_one_or_none()

    next_data = _visitor_to_dict(new_head, prefix="next_") if new_head else _NEXT_DEFAULTS

    return {
        **confirmed,
        "pending_count": str(pending_count),
        **next_data,
    }


@router.post("/visitors/queue/delete")
async def visitor_queue_delete(body: VisitorDeleteRequest, db: AsyncSession = Depends(get_db)):
    """删除当前队头"""
    result = await db.execute(_get_queue_head_query())
    head = result.scalar_one_or_none()

    if not head:
        return {"success": False, "message": "no pending visitor"}

    now = _now_compact()
    head.status = 2
    head.delete_reason = body.delete_reason
    head.deleted_at = now
    await db.commit()
    return {
        "success": True,
        "message": "visitor deleted",
        "visitor_id": str(head.id),
        "delete_reason": body.delete_reason,
        "deleted_at": now,
    }


@router.get("/visitors")
async def visitor_list_all(db: AsyncSession = Depends(get_db)):
    """查询全部访客（调试用）"""
    result = await db.execute(
        select(Visitor).order_by(Visitor.id.desc())
    )
    visitors = result.scalars().all()
    return {
        "success": True,
        "total": len(visitors),
        "items": [
            {
                "id": v.id,
                "license_plate": v.license_plate,
                "entry_time": v.entry_time,
                "purpose": v.purpose,
                "company": v.company,
                "phone": v.phone,
                "status": v.status,
                "delete_reason": v.delete_reason or "",
                "deleted_at": v.deleted_at or "",
            }
            for v in visitors
        ],
    }


@router.post("/visitors/test/reset")
async def visitor_test_reset(db: AsyncSession = Depends(get_db)):
    """重置测试数据"""
    from sqlalchemy import delete as sa_delete
    await db.execute(sa_delete(Visitor))

    test_data = [
        ("浙A11111", "20260530090000", "商务会谈", "绿色动力科技", "13800010001", 0),
        ("浙B22222", "20260530103000", "项目拜访", "星辰数据集团", "13900020002", 0),
        ("浙C33333", "20260530140000", "技术咨询", "蓝色蚂蚁科技", "13600030003", 0),
        ("浙D44444", "20260530160000", "合同签约", "星辰半导体", "13700040004", 1),
        ("浙E55555", "20260531083000", "业务洽谈", "星辰数据集团", "13500050005", 2),
    ]

    for plate, etime, purpose, company, phone, status in test_data:
        db.add(Visitor(
            license_plate=plate,
            entry_time=etime,
            purpose=purpose,
            company=company,
            phone=phone,
            status=status,
            delete_reason=("测试删除" if status == 2 else None),
            deleted_at=("20260531120000" if status == 2 else None),
        ))

    await db.commit()
    return {"success": True, "message": f"inserted {len(test_data)} test records"}

