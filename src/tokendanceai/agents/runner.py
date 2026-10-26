import json
from collections.abc import AsyncGenerator, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage,
    BaseMessage,
    HumanMessage,
    ToolCall,
    ToolMessage,
)
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokendanceai.agents.graph import build_agent
from tokendanceai.agents.memory import load_messages, save_messages
from tokendanceai.mcp.registry import MCPRegistry
from tokendanceai.models.model import MCPServerConfig, User
from tokendanceai.services.llm_service import build_chat_model


async def run_agent(
    user: User,
    message: str,
    session_id: int,
    db: AsyncSession,
    registry: MCPRegistry,
) -> AsyncGenerator[str, None]:
    # get all tools of this user
    server_configs: Sequence[MCPServerConfig] = (
        (
            await db.execute(
                select(MCPServerConfig).where(MCPServerConfig.user_id == user.id)
            )
        )
        .scalars()
        .all()
    )
    tools: list[BaseTool] = await registry.get_tools(
        user_id=user.id, server_configs=server_configs
    )

    # create agent
    if user.llm_config is None:
        raise ValueError("user hadn't set LLM config")
    llm: BaseChatModel = build_chat_model(user.llm_config)
    agent: CompiledStateGraph = await build_agent(llm=llm, tools=tools)

    # concat conversation history
    history: list[BaseMessage] = await load_messages(db, session_id)
    inputs: list[BaseMessage] = history + [HumanMessage(message)]

    # updates 模式给的是「节点跑完后的完整消息」，用来落库；
    # messages 模式给的是「token 增量」，用来推给前端。
    new_messages: list[BaseMessage] = []

    # ---- astream 流式输出相关变量 ----
    mode: str  # 当前 chunk 的流模式: "messages" | "updates"
    chunk: Any  # 当前 chunk, 类型随 mode 变化
    msg_chunk: BaseMessage  # messages 模式解包出的消息增量
    metadata: dict[str, Any]  # messages 模式解包出的 langgraph 元信息
    node: str  # updates 模式 chunk 的 key, 即节点名
    payload: dict[str, Any]  # updates 模式 chunk 的 value, 即节点状态增量
    msg: BaseMessage  # updates 模式遍历到的完整消息
    tc: ToolCall  # assistant 消息里的单条 tool_call

    try:
        # 4. 双通道流式：同时订阅 messages（token）和 updates（完整消息）
        async for mode, chunk in agent.astream(
            input=inputs, stream_mode=["messages", "updates"]
        ):
            if mode == "messages":
                # messages 模式: chunk = (message_chunk, metadata)
                msg_chunk, metadata = chunk
                # 只转发 LLM(agent 节点)的文本增量，过滤掉空 chunk 和非 agent 节点
                if (
                    isinstance(msg_chunk, AIMessage)
                    and metadata.get("langgraph_node") == "agent"
                    and getattr(msg_chunk, "content", "")
                ):
                    yield _sse_token(json.dumps(msg_chunk.content))

            elif mode == "updates":
                # updates 模式: chunk = {node_name: {"messages": [完整消息, ...]}}
                if not isinstance(chunk, dict):
                    continue
                node, payload = next(
                    iter(chunk.items())
                )  # 一般只返回一个完整的 {"messages" : [...]}
                for msg in payload.get("messages", []):
                    new_messages.append(msg)
                    if node == "agent" and isinstance(msg, AIMessage):
                        for tc in msg.tool_calls or []:
                            yield _sse_tool_call(tc)
                    elif node == "tools" and isinstance(msg, ToolMessage):
                        yield _sse_tool_result(msg)

        yield _sse_done()
    finally:
        await save_messages(db, session_id, [HumanMessage(message), *new_messages])


# ---------- SSE Format ----------
def _sse_token(text: str) -> str:
    return (
        f"event: token\ndata: {json.dumps({'content': text}, ensure_ascii=False)}\n\n"
    )


def _sse_tool_call(tc: ToolCall) -> str:
    data = {"name": tc["name"], "args": tc["args"], "id": tc.get("id")}
    return f"event: tool_call\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_tool_result(msg: ToolMessage) -> str:
    data = {
        "tool_call_id": msg.tool_call_id,
        "name": msg.name,
        "content": str(msg.content),
    }
    return f"event: tool_result\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _sse_done() -> str:
    return "event: done\ndata: {}\n\n"
