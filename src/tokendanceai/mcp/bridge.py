from __future__ import annotations

from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator
from jsonschema.exceptions import SchemaError, ValidationError
from jsonschema.protocols import Validator
from jsonschema.validators import validator_for
from langchain_core.tools import BaseTool, ToolException
from mcp import ClientSession
from mcp.types import CallToolResult, TextContent
from mcp.types import Tool as MCPTool
from pydantic import ConfigDict, PrivateAttr
from referencing import Registry
from referencing.exceptions import Unresolvable


class MCPToolWrapper(BaseTool):
    """保留 MCP 的原始 JSON Schema，适配 LangChain 的调用和结果接口。"""

    model_config = ConfigDict(arbitrary_types_allowed=True)

    mcp_name: str
    mcp_description: str = ""
    session: ClientSession
    _validator: Validator = PrivateAttr()

    def model_post_init(self, context: Any) -> None:
        super().model_post_init(context)
        if not isinstance(self.args_schema, dict):
            raise TypeError("MCP tools require a JSON Schema dictionary")
        # 独立快照：不修改 SDK tool，也不随调用方之后的修改漂移。
        self.args_schema = deepcopy(self.args_schema)
        dialect = self.args_schema.get("$schema")
        if "$schema" in self.args_schema and not isinstance(dialect, str):
            raise SchemaError("JSON Schema dialect must be a string")
        validator_cls = validator_for(self.args_schema, default=Draft202012Validator)
        if dialect is not None:
            supported = validator_cls.META_SCHEMA.get(
                "$id", validator_cls.META_SCHEMA.get("id", "")
            )
            if dialect.rstrip("#") != supported.rstrip("#"):
                raise SchemaError("Unsupported JSON Schema dialect")
        validator_cls.check_schema(self.args_schema)
        # 只解析本地 $ref；不因服务器提供的 schema 发起远程下载。
        self._validator = validator_cls(self.args_schema, registry=Registry())

    def _run(self, **kwargs: Any) -> str:
        raise NotImplementedError("MCP tools are async-only. Please use ainvoke().")

    async def _arun(self, **kwargs: Any) -> str:
        # dict args_schema 不触发 LangChain 的 Pydantic 参数校验。
        # JSON Schema 校验不做类型转换、不填默认值，保留缺省和 null 的区别。
        try:
            self._validator.validate(kwargs)
        except ValidationError as exc:
            raise ToolException(
                f"Invalid arguments for {self.mcp_name}: {exc.message}"
            ) from exc
        except Unresolvable as exc:
            raise ToolException(
                f"Unresolvable JSON Schema reference for {self.mcp_name}; "
                "only local references are supported"
            ) from exc

        result = await self.session.call_tool(self.mcp_name, kwargs)
        if not isinstance(result, CallToolResult):
            raise ToolException(f"Unexpected MCP result for {self.mcp_name}")
        texts = [
            block.text for block in result.content if isinstance(block, TextContent)
        ]
        output = "\n".join(texts) if texts else str(result)
        if result.is_error:
            raise ToolException(f"MCP tool {self.mcp_name} failed: {output}")
        return output


def wrap_mcp_tools(session: ClientSession, mcp_tools: list[MCPTool]) -> list[BaseTool]:
    """把 MCP tools 包成 LangChain tools，直接使用原始 input_schema。"""
    return [
        MCPToolWrapper(
            name=tool.name,
            description=tool.description or "",
            mcp_name=tool.name,
            mcp_description=tool.description or "",
            session=session,
            args_schema=tool.input_schema,
        )
        for tool in mcp_tools
    ]
