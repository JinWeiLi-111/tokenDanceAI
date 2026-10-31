import select
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokendanceai.db.db_session import get_db
from tokendanceai.models.model import Message, Session
from tokendanceai.schemas.session import MessagePublic, SessionPublic
from tokendanceai.services.auth import CurrentUser

router = APIRouter()


@router.get("", response_model=list[SessionPublic])
async def list_sessions(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    rows = (
        (
            await db.execute(
                select(Session)
                .where(Session.user_id == current_user.id)
                .order_by(Session.update_at.desc())  # 最近更新的置顶展示
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    return [SessionPublic.model_validate(s) for s in rows]


@router.get("/{session_id}/messages", response_model=list[MessagePublic])
async def list_messages(
    session_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    # 校验 session_id 归属
    session = await db.get(Session, session_id)
    if session is None or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="status not found"
        )

    # 查 session_id 对应的 messages
    rows = (
        (
            await db.execute(
                select(Message)
                .where(Message.session_id == session_id)
                .order_by(
                    Message.created_at.asc(), Message.id.asc()
                )  # 时间 + id 双重排序，避免同时刻乱序
                .limit(limit)
                .offset(offset)
            )
        )
        .scalars()
        .all()
    )

    return [MessagePublic.model_validate(s) for s in rows]


@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
) -> None:
    # 校验 session_id 归属
    session = await db.get(Session, session_id)
    if session is None or session.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="status not found"
        )

    await db.delete(session)
    # todo cascade delete Message
    await db.commit()
    return None
