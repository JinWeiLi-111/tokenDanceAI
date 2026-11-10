from copy import deepcopy
from unittest.mock import create_autospec

import pytest
from jsonschema.exceptions import SchemaError
from langchain_core.tools import ToolException
from langchain_core.utils.function_calling import convert_to_openai_tool
from mcp import ClientSession
from mcp.types import CallToolResult, ImageContent, TextContent
from mcp.types import Tool as MCPTool
from pydantic import ValidationError

from tokendanceai.mcp.bridge import MCPToolWrapper, wrap_mcp_tools


@pytest.fixture
def session():
    mock = create_autospec(ClientSession, instance=True, spec_set=True)
    mock.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text="ok")]
    )
    return mock


def _wrapper(session, schema: dict) -> MCPToolWrapper:
    return MCPToolWrapper(
        name="local_tool",
        description="demo",
        mcp_name="remote_tool",
        session=session,
        args_schema=schema,
    )


def test_wrap_preserves_complex_schema_and_isolates_source(session):
    schema = {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$defs": {"tag": {"type": "string", "minLength": 1}},
        "type": "object",
        "properties": {
            "mode": {"type": "string", "enum": ["fast", "safe"]},
            "value": {"type": ["integer", "string", "null"]},
            "tags": {"type": "array", "items": {"$ref": "#/$defs/tag"}},
            "config": {
                "type": "object",
                "properties": {"count": {"type": "integer", "minimum": 1}},
                "additionalProperties": False,
            },
        },
        "required": ["mode", "value"],
        "additionalProperties": False,
    }
    original = deepcopy(schema)
    remote = MCPTool(name="demo", description="description", input_schema=schema)
    wrapper = wrap_mcp_tools(session, [remote])[0]

    assert isinstance(wrapper.args_schema, dict)
    assert wrapper.args_schema == original
    assert wrapper.args_schema is not remote.input_schema
    assert wrapper.args_schema["properties"] is not remote.input_schema["properties"]
    remote.input_schema["properties"]["mode"]["enum"].append("changed")
    assert wrapper.args_schema == original


def test_wrap_sets_metadata_and_preserves_order(session):
    tools = wrap_mcp_tools(
        session,
        [
            MCPTool(name="foo", description="bar", input_schema={}),
            MCPTool(name="second", input_schema={"type": "object"}),
        ],
    )
    assert [tool.name for tool in tools] == ["foo", "second"]
    assert tools[0].description == "bar"
    assert tools[0].mcp_name == "foo"
    assert tools[0].session is session
    assert tools[0].args_schema == {}
    assert tools[1].description == ""
    assert wrap_mcp_tools(session, []) == []


def test_function_calling_conversion_preserves_constraints(session):
    schema = {
        "type": "object",
        "properties": {"value": {"type": ["string", "null"], "enum": ["ok", None]}},
        "required": ["value"],
        "additionalProperties": False,
    }
    wrapper = _wrapper(session, schema)
    parameters = convert_to_openai_tool(wrapper)["function"]["parameters"]
    assert parameters == schema


async def test_required_field_rejected_before_remote_call(session):
    wrapper = _wrapper(
        session,
        {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
    )
    with pytest.raises(ToolException, match="required property"):
        await wrapper.ainvoke({})
    session.call_tool.assert_not_awaited()


async def test_optional_omission_does_not_insert_null_or_default(session):
    wrapper = _wrapper(
        session,
        {
            "type": "object",
            "properties": {"note": {"type": "string", "default": "server default"}},
        },
    )
    assert await wrapper.ainvoke({}) == "ok"
    session.call_tool.assert_awaited_once_with("remote_tool", {})


async def test_non_nullable_optional_field_rejects_explicit_null(session):
    wrapper = _wrapper(
        session,
        {"type": "object", "properties": {"note": {"type": "string"}}},
    )
    with pytest.raises(ToolException, match="not of type"):
        await wrapper.ainvoke({"note": None})
    session.call_tool.assert_not_awaited()


@pytest.mark.parametrize("value", [None, 12, "hello"])
async def test_union_and_null_are_forwarded_without_coercion(session, value):
    wrapper = _wrapper(
        session,
        {
            "type": "object",
            "properties": {"value": {"type": ["integer", "string", "null"]}},
            "required": ["value"],
        },
    )
    args = {"value": value}
    await wrapper.ainvoke(args)
    session.call_tool.assert_awaited_once_with("remote_tool", args)
    assert type(session.call_tool.await_args.args[1]["value"]) is type(value)


async def test_integer_does_not_coerce_string(session):
    wrapper = _wrapper(
        session,
        {"type": "object", "properties": {"count": {"type": "integer"}}},
    )
    with pytest.raises(ToolException, match="not of type"):
        await wrapper.ainvoke({"count": "12"})
    session.call_tool.assert_not_awaited()


async def test_required_undeclared_field_and_extra_values_are_preserved(session):
    wrapper = _wrapper(session, {"type": "object", "required": ["title"]})
    args = {"title": 123, "extra": {"values": [None, "text", 3]}}
    original = deepcopy(args)
    await wrapper.ainvoke(args)
    session.call_tool.assert_awaited_once_with("remote_tool", original)
    assert args == original
    session.call_tool.reset_mock()
    with pytest.raises(ToolException, match="required property"):
        await wrapper.ainvoke({})
    session.call_tool.assert_not_awaited()


@pytest.mark.parametrize(
    "property_schema,value",
    [
        ({"enum": ["fast", "safe"]}, "invalid"),
        ({"type": "integer", "minimum": 1, "maximum": 5}, 0),
        ({"type": "integer", "minimum": 1, "maximum": 5}, 6),
        ({"type": "string", "minLength": 2}, "a"),
        ({"type": "string", "pattern": "^[a-z]+$"}, "123"),
        ({"type": "array", "items": {"type": "integer"}}, [1, "bad"]),
        (
            {
                "type": "object",
                "properties": {"flag": {"type": "boolean"}},
                "required": ["flag"],
                "additionalProperties": False,
            },
            {"flag": "yes"},
        ),
        ({"oneOf": [{"type": "integer"}, {"type": "string"}]}, None),
        ({"anyOf": [{"const": "a"}, {"const": "b"}]}, "c"),
        ({"allOf": [{"type": "integer"}, {"minimum": 1}]}, 0),
    ],
)
async def test_schema_constraints_reject_invalid_inputs(
    session, property_schema, value
):
    wrapper = _wrapper(
        session,
        {"type": "object", "properties": {"value": property_schema}},
    )
    with pytest.raises(ToolException, match="Invalid arguments"):
        await wrapper.ainvoke({"value": value})
    session.call_tool.assert_not_awaited()


async def test_additional_properties_false_rejects_unknown_field(session):
    wrapper = _wrapper(session, {"type": "object", "additionalProperties": False})
    with pytest.raises(ToolException, match="Additional properties"):
        await wrapper.ainvoke({"extra": "value"})
    session.call_tool.assert_not_awaited()


async def test_local_ref_validates_array_items(session):
    wrapper = _wrapper(
        session,
        {
            "$defs": {"item": {"type": "integer", "minimum": 1}},
            "type": "object",
            "properties": {
                "items": {"type": "array", "items": {"$ref": "#/$defs/item"}}
            },
        },
    )
    await wrapper.ainvoke({"items": [1, 2]})
    session.call_tool.assert_awaited_once_with("remote_tool", {"items": [1, 2]})
    session.call_tool.reset_mock()
    with pytest.raises(ToolException, match="Invalid arguments"):
        await wrapper.ainvoke({"items": [0]})
    session.call_tool.assert_not_awaited()


@pytest.mark.parametrize(
    "ref", ["https://example.invalid/schema.json", "#/$defs/missing"]
)
async def test_unresolvable_ref_fails_without_remote_call(session, ref):
    wrapper = _wrapper(session, {"type": "object", "properties": {"x": {"$ref": ref}}})
    with pytest.raises(ToolException, match="only local references"):
        await wrapper.ainvoke({"x": 1})
    session.call_tool.assert_not_awaited()


async def test_declared_draft7_dialect_is_used(session):
    wrapper = _wrapper(
        session,
        {
            "$schema": "http://json-schema.org/draft-07/schema#",
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "items": [{"type": "integer"}],
                    "additionalItems": False,
                }
            },
        },
    )
    await wrapper.ainvoke({"items": [1]})
    session.call_tool.reset_mock()
    with pytest.raises(ToolException, match="Invalid arguments"):
        await wrapper.ainvoke({"items": [1, 2]})
    session.call_tool.assert_not_awaited()


@pytest.mark.parametrize(
    "schema",
    [
        {"type": "unknown_type"},
        {"type": "object", "properties": ""},
        {"$schema": "https://example.invalid/unknown-dialect"},
        {"$schema": None},
        {"$schema": 123},
    ],
)
def test_invalid_schema_rejected_during_wrapping(session, schema):
    with pytest.raises(SchemaError):
        wrap_mcp_tools(session, [MCPTool(name="bad", input_schema=schema)])
    session.call_tool.assert_not_awaited()


def test_wrapper_requires_json_schema_dictionary(session):
    with pytest.raises(TypeError, match="JSON Schema dictionary"):
        MCPToolWrapper(name="t", description="d", mcp_name="t", session=session)


def test_plain_duck_session_does_not_bypass_concrete_type_validation():
    class FakeSession:
        async def call_tool(self, name, arguments):
            return CallToolResult(content=[])

    with pytest.raises(ValidationError):
        _wrapper(FakeSession(), {})


def test_wrapper_run_is_sync_forbidden(session):
    with pytest.raises(NotImplementedError, match="ainvoke"):
        _wrapper(session, {}).invoke({})
    session.call_tool.assert_not_awaited()


async def test_wrapper_joins_only_text_blocks_and_forwards_arguments(session):
    session.call_tool.return_value = CallToolResult(
        content=[
            TextContent(type="text", text="hello"),
            ImageContent(type="image", data="AA==", mime_type="image/png"),
            TextContent(type="text", text="world"),
        ]
    )
    assert await _wrapper(session, {}).ainvoke({"x": 1}) == "hello\nworld"
    session.call_tool.assert_awaited_once_with("remote_tool", {"x": 1})


async def test_wrapper_falls_back_when_no_text_blocks(session):
    result = CallToolResult(
        content=[ImageContent(type="image", data="AA==", mime_type="image/png")]
    )
    session.call_tool.return_value = result
    assert await _wrapper(session, {}).ainvoke({}) == str(result)


async def test_mcp_error_result_is_not_treated_as_success(session):
    session.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text="failed")], is_error=True
    )
    with pytest.raises(ToolException, match="failed"):
        await _wrapper(session, {}).ainvoke({})


async def test_empty_schema_preserves_all_arguments(session):
    args = {"unknown": [1, "text", None], "nested": {"flag": True}}
    assert await _wrapper(session, {}).ainvoke(args) == "ok"
    session.call_tool.assert_awaited_once_with("remote_tool", args)


async def test_combined_schema_accepts_valid_values(session):
    wrapper = _wrapper(
        session,
        {
            "type": "object",
            "properties": {
                "value": {"oneOf": [{"type": "integer"}, {"type": "string"}]},
                "mode": {"anyOf": [{"const": "a"}, {"const": "b"}]},
                "count": {"allOf": [{"type": "integer"}, {"minimum": 1}]},
            },
        },
    )
    args = {"value": "hello", "mode": "b", "count": 2}
    await wrapper.ainvoke(args)
    session.call_tool.assert_awaited_once_with("remote_tool", args)


async def test_unexpected_mcp_result_is_rejected(session):
    session.call_tool.return_value = object()
    with pytest.raises(ToolException, match="Unexpected MCP result"):
        await _wrapper(session, {}).ainvoke({})


async def test_session_exception_propagates(session):
    session.call_tool.side_effect = TimeoutError("remote timeout")
    with pytest.raises(TimeoutError, match="remote timeout"):
        await _wrapper(session, {}).ainvoke({})
