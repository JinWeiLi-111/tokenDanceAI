from __future__ import annotations

from typing import Any

from langchain_core.tools import BaseTool
from mcp import ClientSession
from mcp.types import Tool as MCPTool
from pydantic import BaseModel, ConfigDict, Field, create_model

##################################
## JSON schema -> Pydantic Model
##################################


def _json_type_to_pydantic_model(t: str | list[str] | None) -> type:
    """json schema type string -> python type"""
    if t is None:
        return str

    types = t if isinstance(t, list) else [t]
    mapping = {
        "string": str,
        "integer": int,
        "number": float,
        "boolean": bool,
        "array": list,
        "object": dict,
        "null": type(None),
    }

    py_types = [mapping.get(x, str) for x in types if x != "null"]
    if not py_types:
        return str
    return py_types[0]


def _schema_to_model(mcp_tool: MCPTool) -> type[BaseModel]:
    """MCP tool schema -> pydantic model"""
    schema = mcp_tool.input_schema or {"type": "object", "properties": {}}
    props: dict[str, Any] = schema.get("properties", {})
    required: set[Any] = set(schema.get("required", []))
    fields: dict[str, Any] = {}
    for fname, fdef in props.items():
        py_type = _json_type_to_pydantic_model(fdef.get("type", "string"))
        desc = fdef.get("description", "")
        if fname in required:
            fields[fname] = (py_type, Field(default=None, description=desc))
        else:
            fields[fname] = (py_type | None, Field(default=None, description=desc))

    return create_model(f"{mcp_tool.name}_Args", **fields)


# single mcp tool wrapper
class MCPToolWrapper(BaseTool):
    """wrap mcp tool -> Langchain BaseTool, 持有对MCP ClientSession 的引用。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    mcp_name: str
    mcp_description: str = ""
    session: ClientSession

    def _run(self, **kwargs: Any) -> Any:
        raise NotImplementedError("MCP tools are async-only. use ainvoke()")

    async def _arun(self, **kwargs: Any) -> Any:
        result = await self.session.call_tool(self.mcp_name, kwargs)
        texts = [
            block.text  # pyright: ignore[reportAttributeAccessIssue]
            for block in result.content
            if getattr(block, "type", None) == "text"
        ]
        return "\n".join(texts) if texts else str(result)


# 批量包装
def wrap_mcp_tools(session: ClientSession, mcp_tools: list[MCPTool]) -> list[BaseTool]:
    """把MCP session 列出的所有 tool 全部包装成 Langchain BaseTool."""
    tools: list[BaseTool] = []
    for tool in mcp_tools:
        args_model = _schema_to_model(tool)
        tools.append(
            MCPToolWrapper(
                name=tool.name,
                description=tool.description or "",
                mcp_name=tool.name,
                mcp_description=tool.description or "",
                session=session,
                args_schema=args_model,
            )
        )

    return tools
