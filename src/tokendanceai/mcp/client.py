from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx2
from mcp import ClientSession
from mcp.client.sse import sse_client
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamable_http_client


@asynccontextmanager
async def connect_http(url: str, token: str) -> AsyncGenerator[ClientSession, None]:
    http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    try:
        async with streamable_http_client(
            url=url,
            http_client=http_client,
        ) as (read, write):
            async with ClientSession(read_stream=read, write_stream=write) as session:
                await session.initialize()
                yield session
    finally:
        await http_client.aclose()


@asynccontextmanager
async def connect_sse(url: str, token: str) -> AsyncGenerator[ClientSession, None]:
    async with sse_client(url=url, headers={"Authorization": f"Bearer {token}"}) as (
        read,
        write,
    ):
        async with ClientSession(read_stream=read, write_stream=write) as session:
            await session.initialize()
            yield session


@asynccontextmanager
async def connect_stdio(command: str, args: list[str]) -> AsyncGenerator[ClientSession, None]:
    params = StdioServerParameters(command=command, args=args)
    async with stdio_client(server=params) as (read, write):
        async with ClientSession(read_stream=read, write_stream=write) as session:
            await session.initialize()
            yield session
