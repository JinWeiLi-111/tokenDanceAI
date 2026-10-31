from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from mcp import Tool
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokendanceai.db.db_session import get_db
from tokendanceai.mcp.client import connect_http, connect_sse
from tokendanceai.mcp.registry import MCPRegistry, get_registry
from tokendanceai.models.model import MCPServerConfig
from tokendanceai.schemas.server import (
    ServerCreate,
    ServerPublic,
    ServerTestResult,
    ServerUpdate,
    ToolSummary,
)
from tokendanceai.services.auth import CurrentUser
from tokendanceai.utils.crypto import decrypt, encrypt

router = APIRouter()


@router.get("", response_model=list[ServerPublic])
async def list_servers(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    """list all mcp servers of current_user"""
    rows = (
        (
            await db.execute(
                select(MCPServerConfig)
                .where(MCPServerConfig.user_id == current_user.id)
                .order_by(MCPServerConfig.id.asc())
            )
        )
        .scalars()
        .all()
    )

    return [ServerPublic.model_validate(s) for s in rows]


@router.post("", status_code=status.HTTP_201_CREATED, response_model=ServerPublic)
async def create_mcp_server(
    data: ServerCreate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    registry: Annotated[MCPRegistry, Depends(get_registry)],
):
    # encode token
    encrypt_token = encrypt(data.api_token)
    server = MCPServerConfig(
        user_id=current_user.id,
        name=data.name,
        url=data.url,
        transport=data.transport,
        encrypt_api_token=encrypt_token,
    )

    db.add(server)
    await db.commit()
    await db.refresh(server)
    # 新增后必须让缓存失效，否则 registry 还在用旧的 server 列表
    await registry.invalidate(current_user.id)

    return ServerPublic.model_validate(server)


@router.patch("/{server_id}", response_model=ServerPublic)
async def update_mcp_server(
    server_id: int,
    data: ServerUpdate,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    registry: Annotated[MCPRegistry, Depends(get_registry)],
):
    server = await db.get(MCPServerConfig, server_id)
    if server is None or server.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found"
        )

    # partial update
    if data.name is not None:
        server.name = data.name
    if data.url is not None:
        server.url = data.url
    if data.transport is not None:
        server.transport = data.transport
    if data.api_token is not None:
        server.encrypted_api_token = encrypt(data.api_token)
    if data.enabled is not None:
        server.enabled = data.enabled

    await db.commit()
    await db.refresh(server)

    # 更新mcp server之后，之前的缓存全部失效，下次 get_tools 时重新加载缓存
    await registry.invalidate(current_user.id)
    return ServerPublic.model_validate(server)


@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_mcp_server(
    server_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
    registry: Annotated[MCPRegistry, Depends(get_registry)],
) -> None:

    server: MCPServerConfig | None = (
        (
            await db.execute(
                select(MCPServerConfig).where(MCPServerConfig.id == server_id)
            )
        )
        .scalars()
        .first()
    )

    if server is None or server.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Invalid session_id, Server not found",
        )

    await db.delete(server)
    await db.commit()


@router.post("/{server_id}/test", response_model=ServerTestResult)
async def test_mcp_server(
    server_id: int,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, Depends(get_db)],
):
    server = (
        (
            await db.execute(
                select(MCPServerConfig).where(MCPServerConfig.id == server_id)
            )
        )
        .scalars()
        .first()
    )

    if server is None or server.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Server not found"
        )

    token = decrypt(server.encrypted_api_token)
    try:
        if server.transport == "http":
            async with connect_http(server.url, token) as session:
                tools: list[Tool] = (await session.list_tools()).tools
        elif server.transport == "sse":
            async with connect_sse(server.url, token) as session:
                tools: list[Tool] = (await session.list_tools()).tools
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Transport {server.transport} not supported for test",
            )

        return ServerTestResult(
            ok=True,
            tools=[ToolSummary(name=t.name, description=t.description) for t in tools],
        )
    except Exception as e:
        # 异常消息可能含 token / url 片段，过滤掉敏感部分
        safe_error = str(e).replace(token, "***") if token else str(e)
        return ServerTestResult(ok=False, error=safe_error)
