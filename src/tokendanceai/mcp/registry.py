from __future__ import annotations

from collections.abc import Sequence
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from langchain_core.tools import BaseTool
from mcp import ClientSession

from tokendanceai.mcp.bridge import wrap_mcp_tools
from tokendanceai.mcp.client import connect_http, connect_sse
from tokendanceai.models.model import MCPServerConfig
from tokendanceai.utils.crypto import decrypt


@dataclass
class _LiveClient:
    """一个 MCP server 的活动连接。管建连、关闭、缓存 tool 列表。"""

    session: ClientSession
    _stack: AsyncExitStack  # 持有 connect_* 的 async with 栈，close 时逆序释放
    tools_cache: list[BaseTool] = field(default_factory=list)
    last_used: datetime = field(default_factory=datetime.now)

    async def close(self) -> None:
        # 退出 AsyncExitStack → 按栈逆序释放 session → transport → http_client
        await self._stack.aclose()


class MCPRegistry:
    """per-user MCP client 缓存，带 TTL，server config 变更时失效。"""

    def __init__(self, cache_ttl: int = 300) -> None:
        self._clients: dict[tuple[int, str], _LiveClient] = {}
        self._ttl: timedelta = timedelta(seconds=cache_ttl)

    async def get_tools(
        self,
        user_id: int,
        server_configs: Sequence[MCPServerConfig],
    ) -> list[BaseTool]:
        """聚合该用户所有 enabled server 的 tools，按 cfg 顺序拼成一个列表。"""
        tools: list[BaseTool] = []
        for cfg in server_configs:
            if not cfg.enabled:
                # soft delete
                continue
            live = await self._get_or_build(user_id, cfg)
            tools.extend(live.tools_cache)
        return tools

    async def _get_or_build(self, user_id: int, cfg: MCPServerConfig) -> _LiveClient:
        key = (user_id, cfg.name)
        live = self._clients.get(key)
        # assert live client is not expired
        if live and datetime.now() - live.last_used < self._ttl:
            live.last_used = datetime.now()
            return live

        live = await self._build_live(cfg)
        mcp_tools = (await live.session.list_tools()).tools
        live.tools_cache = wrap_mcp_tools(session=live.session, mcp_tools=mcp_tools)
        self._clients[key] = live
        return live

    async def _build_live(self, cfg: MCPServerConfig) -> _LiveClient:
        """按cfg.transport建连，用 AsyncExitStack 持有，让 session 长生命周期存活"""
        stack = AsyncExitStack()
        try:
            token = decrypt(cfg.encrypted_api_token)
            assert token
            if cfg.transport == "http":
                session = await stack.enter_async_context(
                    connect_http(url=cfg.url, token=token)
                )
            elif cfg.transport == "sse":
                session = await stack.enter_async_context(
                    connect_sse(url=cfg.url, token=token)
                )
            elif cfg.transport == "stdio":
                raise NotImplementedError("stdio transport not support")
            else:
                raise NotImplementedError("unknown transport")

        except BaseException:
            await stack.aclose()
            raise
        return _LiveClient(session=session, _stack=stack)

    async def invalidate(self, user_id: int) -> None:
        """server config 修改之后调用, 关闭 user_id 关联的所有 client并删除缓存, 下次重建"""
        keys = [k for k in self._clients if k[0] == user_id]
        for k in keys:
            await self._clients[k].close()
            del self._clients[k]
