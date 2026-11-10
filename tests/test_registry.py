import asyncio
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, create_autospec

import pytest
from jsonschema.exceptions import SchemaError
from langchain_core.tools import ToolException
from mcp import ClientSession
from mcp.types import CallToolResult, ListToolsResult, TextContent
from mcp.types import Tool as MCPTool

from tokendanceai.mcp.registry import MCPRegistry, _LiveClient
from tokendanceai.models.model import MCPServerConfig

SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": ["integer", "string", "null"]}},
    "required": ["value"],
    "additionalProperties": False,
}


def _cfg(name: str, enabled: bool = True, transport: str = "http") -> MCPServerConfig:
    """Construct a server config without writing to the database."""
    return MCPServerConfig(
        user_id=1,
        name=name,
        url=f"http://{name}",
        transport=transport,
        encrypted_api_token="x",
        enabled=enabled,
    )


@dataclass
class RegistryHarness:
    registry: MCPRegistry
    build_calls: list[str] = field(default_factory=list)
    sessions: list = field(default_factory=list)
    closes: list[AsyncMock] = field(default_factory=list)


@pytest.fixture
async def registry_with_fake_build(monkeypatch):
    """Replace connection creation, not the cache or the real tool wrapper."""
    harness = RegistryHarness(registry=MCPRegistry(cache_ttl=300))

    async def fake_build_live(cfg: MCPServerConfig) -> _LiveClient:
        harness.build_calls.append(cfg.name)
        session = create_autospec(ClientSession, instance=True, spec_set=True)
        session.list_tools.return_value = ListToolsResult(
            tools=[
                MCPTool(
                    name=f"{cfg.name}_tool",
                    description="demo",
                    input_schema=SCHEMA,
                )
            ]
        )
        session.call_tool.return_value = CallToolResult(
            content=[TextContent(type="text", text="ok")]
        )
        close = AsyncMock()
        stack = AsyncExitStack()
        stack.push_async_callback(close)
        harness.sessions.append(session)
        harness.closes.append(close)
        return _LiveClient(session=session, _stack=stack)

    monkeypatch.setattr(harness.registry, "_build_live", fake_build_live)
    try:
        yield harness
    finally:
        for user_id in {key[0] for key in harness.registry._clients}:
            await harness.registry.invalidate(user_id)


async def test_disabled_server_is_skipped(registry_with_fake_build):
    harness = registry_with_fake_build
    assert await harness.registry.get_tools(1, [_cfg("off", enabled=False)]) == []
    assert harness.build_calls == []


async def test_empty_server_list_does_not_build(registry_with_fake_build):
    harness = registry_with_fake_build
    assert await harness.registry.get_tools(1, []) == []
    assert harness.build_calls == []


async def test_tools_are_aggregated_in_config_order(registry_with_fake_build):
    harness = registry_with_fake_build
    tools = await harness.registry.get_tools(
        1, [_cfg("second"), _cfg("off", enabled=False), _cfg("first")]
    )
    assert [tool.name for tool in tools] == ["second_tool", "first_tool"]
    assert harness.build_calls == ["second", "first"]


async def test_registry_tools_preserve_schema_and_are_callable(
    registry_with_fake_build,
):
    harness = registry_with_fake_build
    tools = await harness.registry.get_tools(1, [_cfg("demo")])
    assert tools[0].args_schema == SCHEMA
    assert await tools[0].ainvoke({"value": None}) == "ok"
    harness.sessions[0].call_tool.assert_awaited_once_with("demo_tool", {"value": None})
    harness.sessions[0].call_tool.reset_mock()
    with pytest.raises(ToolException, match="required property"):
        await tools[0].ainvoke({})
    harness.sessions[0].call_tool.assert_not_awaited()


async def test_second_call_reuses_tools_and_does_not_rediscover(
    registry_with_fake_build,
):
    harness = registry_with_fake_build
    cfg = _cfg("demo")
    first = await harness.registry.get_tools(1, [cfg])
    second = await harness.registry.get_tools(1, [cfg])
    assert harness.build_calls == ["demo"]
    assert second[0] is first[0]
    harness.sessions[0].list_tools.assert_awaited_once_with()
    harness.closes[0].assert_not_awaited()


async def test_cache_hit_refreshes_idle_ttl(registry_with_fake_build):
    harness = registry_with_fake_build
    cfg = _cfg("demo")
    await harness.registry.get_tools(1, [cfg])
    live = harness.registry._clients[(1, "demo")]
    old = datetime.now() - timedelta(seconds=100)
    live.last_used = old
    await harness.registry.get_tools(1, [cfg])
    assert live.last_used > old
    assert harness.build_calls == ["demo"]


async def test_expired_cache_closes_old_connection_and_rebuilds(
    registry_with_fake_build,
):
    harness = registry_with_fake_build
    cfg = _cfg("demo")
    first = await harness.registry.get_tools(1, [cfg])
    harness.registry._clients[(1, "demo")].last_used = datetime.now() - timedelta(
        seconds=301
    )
    second = await harness.registry.get_tools(1, [cfg])
    assert harness.build_calls == ["demo", "demo"]
    assert second[0] is not first[0]
    assert second[0].session is harness.sessions[1]
    harness.closes[0].assert_awaited_once_with()
    harness.closes[1].assert_not_awaited()
    harness.sessions[1].list_tools.assert_awaited_once_with()


async def test_invalidate_closes_all_target_user_clients(registry_with_fake_build):
    harness = registry_with_fake_build
    await harness.registry.get_tools(1, [_cfg("a"), _cfg("b")])
    await harness.registry.invalidate(1)
    assert harness.registry._clients == {}
    for close in harness.closes:
        close.assert_awaited_once_with()
    await harness.registry.invalidate(1)
    for close in harness.closes:
        close.assert_awaited_once_with()
    await harness.registry.get_tools(1, [_cfg("a")])
    assert harness.build_calls == ["a", "b", "a"]


async def test_invalidate_does_not_close_another_users_session(
    registry_with_fake_build,
):
    harness = registry_with_fake_build
    cfg = _cfg("demo")
    await harness.registry.get_tools(1, [cfg])
    other = await harness.registry.get_tools(2, [cfg])
    await harness.registry.invalidate(1)
    again = await harness.registry.get_tools(2, [cfg])
    assert again[0] is other[0]
    assert harness.build_calls == ["demo", "demo"]
    harness.closes[0].assert_awaited_once_with()
    harness.closes[1].assert_not_awaited()
    harness.sessions[1].list_tools.assert_awaited_once_with()


@pytest.mark.parametrize(
    "failure", [RuntimeError("discovery failed"), asyncio.CancelledError()]
)
async def test_discovery_failure_or_cancellation_closes_new_connection(
    monkeypatch, failure
):
    registry = MCPRegistry()
    session = create_autospec(ClientSession, instance=True, spec_set=True)
    session.list_tools.side_effect = failure
    close = AsyncMock()
    stack = AsyncExitStack()
    stack.push_async_callback(close)
    build = AsyncMock(return_value=_LiveClient(session=session, _stack=stack))
    monkeypatch.setattr(registry, "_build_live", build)
    with pytest.raises(type(failure)):
        await registry.get_tools(1, [_cfg("demo")])
    assert registry._clients == {}
    close.assert_awaited_once_with()
    session.call_tool.assert_not_awaited()


async def test_invalid_tool_schema_closes_new_connection(monkeypatch):
    registry = MCPRegistry()
    session = create_autospec(ClientSession, instance=True, spec_set=True)
    session.list_tools.return_value = ListToolsResult(
        tools=[MCPTool(name="bad", input_schema={"type": "invalid"})]
    )
    close = AsyncMock()
    stack = AsyncExitStack()
    stack.push_async_callback(close)
    monkeypatch.setattr(
        registry,
        "_build_live",
        AsyncMock(return_value=_LiveClient(session=session, _stack=stack)),
    )
    with pytest.raises(SchemaError):
        await registry.get_tools(1, [_cfg("demo")])
    assert registry._clients == {}
    close.assert_awaited_once_with()
