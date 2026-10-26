import json
from typing import Any

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokendanceai.models.model import Message

_ROLE_MAP = {"user": HumanMessage, "assitant": AIMessage, "tool": ToolMessage}


async def load_messages(db: AsyncSession, session_id: int) -> list[BaseMessage]:
    """
    读取历史的Message行，按发生顺序还原成 langgraph 消息列表。
    """

    rows: list[Message] = list(
        (
            (await db.execute(select(Message).where(Message.session_id == session_id)))
            .scalars()
            .all()
        )
    )

    messgaes: list[BaseMessage] = []

    for row in rows:
        if row.role == "user":
            messgaes.append(HumanMessage(content=row.content or ""))
        elif row.role == "assistant":
            # tool_calls_json: [{"name": ..., "args": {...}, "id": "..."}, ...]
            tool_calls: list[dict[Any, Any]] = (
                json.loads(row.tool_calls_json) if row.tool_calls_json else []
            )
            messgaes.append(
                AIMessage(
                    content=row.content or "",
                    tool_calls=tool_calls,
                )
            )
        elif row.role == "tool":
            # tool_results_json: {"tool_call_id": "...", "name": "..."}
            # ToolMessage 必须带 tool_call_id，否则 langgraph 认不出它回应的是哪次调用
            payload: dict[Any, Any] = (
                json.loads(row.tool_results_json) if row.tool_results_json else {}
            )
            messgaes.append(
                ToolMessage(
                    content=row.content,
                    tool_call_id=payload.get("tool_call_id"),
                    name=payload.get("name"),
                )
            )

    return messgaes


async def save_messages(
    db: AsyncSession,
    session_id: int,
    messages: list[BaseMessage],
) -> None:
    """把本轮新产生的消息逐条写入 messages 表。

    顺序与 load_messages 的还原逻辑对称：user / assistant(含 tool_calls) / tool。
    """
    for msg in messages:
        if isinstance(msg, HumanMessage):
            # user prompt message
            db.add(
                Message(
                    session_id=session_id,
                    role="user",
                    content=str(msg.content),
                )
            )
        elif isinstance(msg, AIMessage):
            # tool_calls : list[dict]，含 name/args/id/type
            tool_calls = getattr(msg, "tool_calls", None) or []
            tool_calls_json = (
                json.dumps(
                    [
                        {
                            "name": tc["name"],
                            "args": tc.get("args", {}),
                            "id": tc.get("id"),
                            "type": "tool_call",
                        }
                        for tc in tool_calls
                    ],
                    ensure_ascii=False,
                )
                if tool_calls
                else None
            )
            db.add(
                Message(
                    session_id=session_id,
                    role="assistant",
                    content=str(msg.content or ""),
                    tool_calls_json=tool_calls_json,
                )
            )
        elif isinstance(msg, ToolMessage):
            # tool_results
            tool_results_json = json.dumps(
                {"tool_call_id": msg.tool_call_id, "name": msg.name},
                ensure_ascii=False,
            )
            db.add(
                Message(
                    session_id=session_id,
                    role="tool",
                    content=str(msg.content),
                    tool_results_json=tool_results_json,
                )
            )
        # 其它类型（SystemMessage 等）不入库
    await db.commit()
