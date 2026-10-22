# 通用 Agent 设计方案

## 1. 目标与边界

把现在 blog 里那个"只会写 post"的 in-process agent([agents/graph.py](../src/fastapi_blog/agents/graph.py)、[agents/runner.py](../src/fastapi_blog/agents/runner.py))拆出来,做成一个**独立的通用 Agent 应用** `general-agent`:

- 后端 FastAPI + langgraph 编排(复用 blog 现有的 `create_agent` 模式)。
- 能力**完全来自外部 MCP server**,agent 自己不 import 任何业务 ORM/service。blog 只是它的一个 MCP server。
- 支持挂多个 MCP server(blog、GitHub、文件系统……),tool 运行时发现。
- 写 post + 自动发布:靠 blog 暴露的 `create_post_draft` / `publish_post` MCP tool,agent 在一个 run 内完成 draft → publish。
- 每个 user 有自己的 LLM config 和 MCP server 配置(token 加密存)。

agent 和 blog 的关系变成:

```
general-agent (FastAPI + langgraph)  ──MCP──▶  blog (FastAPI + FastMCP)
                                    ──MCP──▶  其他 MCP server
```

### 现状的问题(为什么要拆)

- [tools/posts.py](../src/fastapi_blog/agents/tools/posts.py) 的 `create_post` 通过 `get_agent_context()` 直接拿 `db: AsyncSession` 和 `current_user`,tool 直接操作 blog 的 ORM/service,**只能在同进程内运行**。
- [runner.py](../src/fastapi_blog/agents/runner.py) 把 `AgentContext(user, db, llm_config)` 通过 langgraph 的 `context_schema` 注入,跨进程无法传递。
- 当前 `create_post` 只生成 draft + 返回 `confirm_endpoint=/api/create_post`,真正的发布动作依赖前端点确认按钮([tools/base.py](../src/fastapi_blog/agents/tools/base.py) 的 `ToolResult.confirm_endpoint`),是 human-in-the-loop,**做不到自动发布**。

## 2. 项目结构(独立 repo)

```
general-agent/
  pyproject.toml
  src/general_agent/
    main.py                 # FastAPI app
    config.py
    database.py
    models.py              # User, UserLLMConfig, MCPServerConfig, Session, Message
    auth.py
    schemas.py
    routers/
      agent.py             # POST /agent/run  (SSE), GET/DELETE /sessions
      servers.py           # MCP server config CRUD + 连通性测试
      account.py           # LLM config
    agents/
      graph.py             # build_agent(model, tools, system_prompt)
      runner.py            # run_agent() -> SSE, 复用 blog 的事件流逻辑
      mcp_registry.py      # per-user MCP client 缓存 + tool 发现
    services/
      llm_services.py      # build_chat_model (从 blog 搬过来)
    templates/...
    static/js/...
```

## 3. 数据模型

```python
class User:  # agent app 自己的 user
    ...


class UserLLMConfig(Base):  # 和 blog 一样
    user_id, base_url, model, encrypted_api_key


class MCPServerConfig(Base):
    id, user_id
    name  # "blog"
    url  # https://blog.example.com/mcp
    transport  # "http" | "sse" | "stdio"
    encrypted_api_token  # 调 blog 时 Authorization: Bearer xxx
    enabled, created_at


class Session(Base):
    id, user_id, title, created_at, updated_at


class Message(Base):
    id, session_id
    role  # user / assistant / tool
    content
    tool_calls_json
    tool_results_json
    created_at
```

LLM config 和 blog 重复是**有意的**:agent 是独立 app,不该依赖 blog 的 DB。用户在 agent 的 account 页配自己的 LLM。

## 4. 核心:MCP registry(per-user tool 发现)

MCP client 必须是 **per-user** 的,因为 token 是每个 user 自己的。懒加载 + 缓存:

```python
# agents/mcp_registry.py
from langchain_mcp_adapters.client import MultiServerMCPClient


class MCPRegistry:
    """每个 user 一个 client,带 TTL 缓存,server config 变更时失效。"""

    def __init__(self, db, cache_ttl: int = 300): ...

    async def get_tools(self, user: User) -> list[BaseTool]:
        client = await self._get_or_build_client(user)  # 带 Authorization header
        return await client.get_tools()

    async def invalidate(self, user_id: int) -> None: ...  # server config 改完调
```

`MultiServerMCPClient` 配置从 `MCPServerConfig` 表动态构造:

```python
{
    s.name: {
        "url": s.url,
        "transport": s.transport,
        "headers": {"Authorization": f"Bearer {decrypt(s.encrypted_api_token)}"},
    }
    for s in enabled_servers
}
```

> **Tool 数量爆炸**是通用 agent 的主要风险。v1 直接全量注入(几十个 tool 没问题);后续做 dynamic tool loading——加一个 `search_tools(query)` meta-tool,按需 retrieve top-k,避免 context 膨胀。

## 5. Agent 编排(langgraph)

复用 blog 现在的 [graph.py](../src/fastapi_blog/agents/graph.py) 写法,只是 tools 来源换了:

```python
# agents/graph.py
SYSTEM_PROMPT = """You are a general-purpose autonomous assistant.
You have tools from connected MCP servers. To publish a blog post,
call create_post_draft then publish_post in one flow unless the user
asks to review first. Be concise."""


def build_agent(llm: BaseChatModel, tools: list[BaseTool]) -> CompiledStateGraph:
    return create_agent(
        model=llm,
        tools=tools,
        system_prompt=SYSTEM_PROMPT,
    )
```

注意:**不再需要 `context_schema=AgentContext`**。现在 blog 用 `AgentContext` 是因为 tool 要拿 `db` / `user`;改成 MCP 之后,tool 的鉴权在 MCP client header 里,langgraph 侧不需要注入业务 context。这是解耦的关键标志。

### 扩展点(v1 之后,都用 langgraph 原生能力)

- **多步规划**:换成自定义 `StateGraph`,加 `planner` node 先拆任务再执行。
- **子 agent**:把某些 server 的 tool 包成 subgraph,主 agent 路由过去。
- **Human-in-the-loop**:对破坏性 tool(删 post)用 `interrupt()`,agent run 暂停,前端 resume。
- **可观测性**:接 LangSmith / langgraph 自带 tracing。

## 6. Runner(SSE,基本照搬 blog)

[runner.py](../src/fastapi_blog/agents/runner.py) 的事件流逻辑(`on_chat_model_stream` / `on_tool_end` / `on_tool_error` / `done`)直接搬过来,改两处:

1. `tools` 从 `MCPRegistry.get_tools(user)` 拿,不再 `ALL_TOOLS`。
2. `history` 从 `Message` 表按 `session_id` 读,run 结束后把本轮 messages 落库。

## 7. API 表面

```
POST   /agent/run             body: {session_id?, message, autopilot?}  -> SSE 流
GET    /sessions              列出当前 user 的会话
GET    /sessions/{id}/messages
DELETE /sessions/{id}

GET    /servers               列出已配的 MCP server
POST   /servers               加一个 server (name, url, transport, token)
PATCH  /servers/{id}          改 / 启用禁用
DELETE /servers/{id}
POST   /servers/{id}/test     连通性 + 列出发现的 tools

GET/PUT /account              LLM config
```

## 8. 自动发布的设计

不要在 agent 代码里写"先 draft 再 publish"的硬逻辑。两件事:

1. **blog 侧**暴露两个独立 tool:`create_post_draft` / `publish_post`。`publish_post` 直接落库,不要 `confirm_endpoint` 那套。
2. **agent 侧**用 system prompt 控制:"除非用户要求先看 draft,否则 draft 后立即 publish"。

加一个 per-run `autopilot: bool` 参数:`autopilot=true` 时 prompt 强化自主执行;`false` 时改成"draft 后停下来等用户确认"。这样两种模式同一套代码。

## 9. blog 侧需要配合的改动

agent 要能调通,blog 必须先提供 MCP。改动清单:

1. 加 `FastMCP` server,挂到现有 FastAPI app:`mcp.http_app(path="/mcp")`。
2. 暴露 tool:`create_post_draft` / `publish_post` / `list_my_posts` / `get_post`。内部复用现有 service。
3. 加 `UserApiToken` 模型(每用户一个长期 token),MCP 依赖里解析 Bearer → user,替换现在 tool 里 `get_agent_context().user` 的来源。
4. Account 页加"生成 API token"。

## 10. 落地顺序

1. blog 侧先做 MCP server + token + 两个 post tool,用 `mcp` CLI 验证连通。
2. 起 `general-agent` 骨架(FastAPI + DB + auth),搬 `llm_services` / `runner`。
3. 实现 `MCPRegistry` + `build_agent`,接 blog MCP,跑通"写 post → 自动发布"。
4. 再加 session 持久化、server config 管理页、连通性测试。
5. 之后才是 dynamic tool loading、planner、子 agent 这些扩展。

建议先做 blog 侧(第 1 步),因为没有 MCP server,agent 无从调起。
