from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from tokendanceai.agents.runner import run_agent
from tokendanceai.db.db_session import get_db
from tokendanceai.mcp.registry import MCPRegistry, get_registry
from tokendanceai.models.model import Session
from tokendanceai.schemas.agent import AgentRunRequest
from tokendanceai.services.auth import CurrentUser

router = APIRouter()


@router.post("/run", response_class=StreamingResponse)
async def agent_run(
    req: AgentRunRequest,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    registry: MCPRegistry = Depends(get_registry),
):
    # session_id authenticate and create
    if req.session_id is None:
        session = Session(
            user_id=current_user.id, title=req.message[:50]
        )  # todo 后续使用AI总结title
        db.add(session)
        await db.commit()
        await db.refresh(session)
        session_id = session.id
    else:
        session: Session | None = await db.get(Session, req.session_id)
        if session is None or session.user_id != current_user.id:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="Session not found"
            )
        session_id = session.id

    # 调用 runner 获取 SSE generator
    return StreamingResponse(
        run_agent(
            user=current_user,
            message=req.message,
            session_id=session_id,
            db=db,
            registry=registry,
        ),
        media_type="text/event-stream",
    )
