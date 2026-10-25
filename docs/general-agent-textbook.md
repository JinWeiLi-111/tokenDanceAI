# 通用 Agent 开发教程

> 一本从背景到落地的完整参考手册。融合架构设计（为什么这么做）和 MCP 桥接实现（具体怎么做），供开发时随时翻阅。
>
> 阅读建议：第一遍按章节顺序通读建立全景，开发时按「第 8 章 落地顺序」的步骤逐步实现，遇到具体卡点跳到对应章节深读。

---

## 目录

- [序章 这本书在讲什么](#序章)
- [第 1 部分 背景与架构](#part-1)
  - [第 1 章 为什么要拆一个通用 Agent](#ch-1)
  - [第 2 章 项目结构](#ch-2)
- [第 2 部分 数据层](#part-2)
  - [第 3 章 数据模型设计](#ch-3)
  - [第 4 章 数据库基础设施](#ch-4)
- [第 3 部分 MCP 协议与桥接](#part-3)
  - [第 5 章 MCP 协议基础](#ch-5)
  - [第 6 章 类型鸿沟与桥的设计](#ch-6)
  - [第 7 章 桥的参考实现](#ch-7)
  - [第 8 章 三种 Transport](#ch-8)
  - [第 9 章 Session 生命周期与 Registry](#ch-9)
- [第 4 部分 Agent 编排](#part-4)
  - [第 10 章 langgraph 接入](#ch-10)
  - [第 11 章 Runner 与 SSE 事件流](#ch-11)
- [第 5 部分 接口与交互](#part-5)
  - [第 12 章 API 表面](#ch-12)
  - [第 13 章 自动发布与 Autopilot](#ch-13)
- [第 6 部分 落地与进阶](#part-6)
  - [第 14 章 开发落地顺序](#ch-14)
  - [第 15 章 blog 侧配合改动](#ch-15)
  - [第 16 章 扩展点](#ch-16)
- [附录](#appendix)
  - [A. 已知坑点清单](#appendix-a)
  - [B. 自写桥 vs adapters 包对照表](#appendix-b)
  - [C. 术语表](#appendix-c)

---

<a id="序章"></a>
## 序章

这本书讲的是：**如何把一个"只会写博客"的进程内 agent，重构成一个能力完全来自外部 MCP server 的通用 Agent 应用**。

核心思想一句话：**agent 自己不写一行业务逻辑，它的所有能力都通过 MCP 协议从外部 server 动态获得**。blog、GitHub、文件系统……都只是它挂载的 MCP server，tool 在运行时发现。

这本书分两条线：

- **架构线**（第 1–4 章、第 10–13 章）：为什么这么设计，解耦的关键在哪。
- **实现线**（第 5–9 章、第 14 章）：MCP 协议怎么吃透、桥怎么自己写、session 怎么管。

实现线选择**不用 `langchain-mcp-adapters` 包**，而是只用 MCP 官方 SDK 自己写桥。代价是 ~50 行桥代码，收益是完全掌控连接生命周期、协议细节、per-user 缓存策略，且吃透 MCP 协议本身。

---

<a id="part-1"></a>
# 第 1 部分 背景与架构

<a id="ch-1"></a>
## 第 1 章 为什么要拆一个通用 Agent

### 1.1 目标

把现在 blog 里那个"只会写 post"的 in-process agent 拆出来，做成一个**独立的通用 Agent 应用**：

- 后端 FastAPI + langgraph 编排（复用 blog 现有的 `create_agent` 模式）。
- 能力**完全来自外部 MCP server**，agent 自己不 import 任何业务 ORM/service。blog 只是它的一个 MCP server。
- 支持挂多个 MCP server（blog、GitHub、文件系统……），tool 运行时发现。
- 写 post + 自动发布：靠 blog 暴露的 `create_post_draft` / `publish_post` MCP tool，agent 在一个 run 内完成 draft → publish。
- 每个 user 有自己的 LLM config 和 MCP server 配置（token 加密存）。

agent 和 blog 的关系变成：

```
general-agent (FastAPI + langgraph)  ──MCP──▶  blog (FastAPI + FastMCP)
                                    ──MCP──▶  其他 MCP server
```

### 1.2 现状的问题（为什么要拆）

当前 blog 里的 agent 有三个结构性问题：

1. **tool 直接操作 blog 的 ORM/service，只能同进程运行**。`create_post` 通过 `get_agent_context()` 直接拿 `db: AsyncSession` 和 `current_user`，跨进程无法传递。
2. **langgraph 的 `context_schema` 注入跨进程无法传递**。`AgentContext(user, db, llm_config)` 这种进程内对象没法序列化穿过网络。
3. **做不到自动发布**。当前 `create_post` 只生成 draft + 返回 `confirm_endpoint`，真正的发布动作依赖前端点确认按钮，是 human-in-the-loop。

拆成独立 agent + MCP 协议后，这三个问题一起解决：

- tool 的鉴权从"进程内 context"变成"MCP client 的 Authorization header"。
- 业务逻辑从"agent 直接调 ORM"变成"agent 调 MCP server 的 tool"。
- 自动发布从"前端确认"变成"system prompt 控制流程"。

### 1.3 解耦的关键标志

整个重构里最重要的一句话：**tool 的鉴权在 MCP client header 里，langgraph 侧不需要注入业务 context**。

当 agent 代码里不再出现 `context_schema=AgentContext`、不再 `get_agent_context()`，就说明解耦成功了。这是判断"是不是真通用 agent"的试金石。

---

<a id="ch-2"></a>
## 第 2 章 项目结构

### 2.1 推荐目录

```
tokendanceai/
  pyproject.toml
  src/tokendanceai/
    main.py                 # FastAPI app
    config.py               # Settings 单例
    templating.py           # Jinja2 templates
    db/
      base.py               # DeclarativeBase
      session.py            # engine, AsyncSessionLocal, get_db
      init_db.py            # 建表
    models/
      model.py              # User, UserLLMConfig, MCPServerConfig, Session, Message
    schemas/
      user.py / agent.py / server.py
    pages/                  # 返回 HTML 的路由
      home.py
    router/                 # JSON / SSE API 路由
      agent.py              # POST /agent/run (SSE), GET/DELETE /sessions
      servers.py            # MCP server config CRUD + 连通性测试
      account.py            # LLM config
    mcp/                    # MCP 基础设施（见第 9 章 9.4）
      bridge.py             # MCP tool → LangChain BaseTool 的桥
      client.py             # 三种 transport 连接（streamable_http / sse / stdio）
      registry.py           # per-user MCP client 缓存（接多 server 时再建）
    agents/
      graph.py              # build_agent(model, tools, system_prompt)
      runner.py             # run_agent() -> SSE
      system_prompt.py      # SYSTEM_PROMPT 常量
    services/
      llm_services.py       # build_chat_model
    templates/...
    static/...
```

### 2.2 分层规则

依赖方向**单向**，避免循环：

```
router / pages ──▶ agents ──▶ mcp ──▶ models ──▶ db
              ──▶ services ─────────▶ models ──▶ db
```

- `pages/` vs `router/`：返回 HTML 的放 `pages/`，返回 JSON/SSE 的放 `router/`。比把所有路由混在一个 `routers/` 下更清晰。
- `agents/` 只放编排逻辑（langgraph state、SSE 流），不碰具体业务。
- MCP 基础设施放 `mcp/` 包：`bridge.py`（桥）+ `client.py`（transport 连接）；接多 server、要 per-user 缓存时再拆出 `registry.py` / `config.py` / `discovery.py`，见 9.4。
- `db/` 是最底层基础设施，`config.py` 是横切关注点，都不 import 上层。

### 2.3 配置管理

`config.py` 放包根目录，用 `pydantic-settings` 读环境变量，模块级单例：

```python
# src/tokendanceai/config.py
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="APP_",
        extra="ignore",
    )

    secret_key: str = "change-me"
    debug: bool = False
    database_url: str = "sqlite+aiosqlite:///./tokendanceai.db"
    token_encryption_key: str = "change-me"      # 加密 MCP token / LLM key 用
    access_token_expire_minutes: int = 60 * 24
    mcp_cache_ttl: int = 300                      # MCPRegistry 的 client 缓存秒数
    mcp_request_timeout: int = 30                 # 单次 tool 调用超时


settings = Settings()
```

不要给 config 单独开文件夹——它是横切单例，不是领域。详见 [关于 config 文件夹的讨论](#)。

---

<a id="part-2"></a>
# 第 2 部分 数据层

<a id="ch-3"></a>
## 第 3 章 数据模型设计

### 3.1 五个模型总览

```python
class User:                     # agent app 自己的 user
class UserLLMConfig(Base):      # 每用户 LLM 配置（加密 key）
class MCPServerConfig(Base):    # MCP server 配置（加密 token）
class Session(Base):            # 对话会话
class Message(Base):            # 单条消息 + tool 调用记录
```

### 3.2 User —— agent 自己的用户体系

agent 是独立 app，不能复用 blog 的用户表。`User` 是 agent 内部身份锚点，所有 per-user 配置都挂在它的 `user_id` 上。

鉴权不走"同进程 current_user"，而是通过 MCP 的 `Authorization: Bearer <token>` 头传递。所以 `User` 是 agent 内部用的，跟 blog 的 user 是两套表，靠 MCP token 桥接。

字段：`id`、`username`、`email`、`password_hash`、`image_file`（头像）。

### 3.3 UserLLMConfig —— 每用户自己的 LLM 配置

字段：`user_id`、`provider`、`base_url`、`model`、`encrypted_api_key`。

**为什么 per-user**：通用 agent 不内置任何模型——不同用户用不同 provider（OpenAI / Anthropic / 本地 Ollama）、不同 base_url、不同 quota。agent 只负责编排，模型和密钥都是用户自带的。

**为什么加密 `api_key`**：API key 是高敏感凭据，明文存库 = 数据库被拖就全员 key 泄露。加密 key 从 `Settings.token_encryption_key` 来，解密只在 `build_chat_model` 时做一次。

**1:1 关系**：`user_id` 上 `unique=True`，一个用户一份 LLM 配置。

### 3.4 MCPServerConfig —— MCP server 配置（命脉）

字段：`id`、`user_id`、`name`、`url`、`transport`、`encrypted_api_token`、`enabled`、`created_at`。

这是整个"通用 agent"架构的命脉。设计文档 §1 开宗明义：**能力完全来自外部 MCP server，agent 自己不 import 任何业务 ORM/service**。agent 能写 blog、操作 GitHub、整理文件，全靠用户在这张表里配置的 server。tool 在运行时从这些 server 动态发现。

字段意义：

| 字段 | 意义 |
|---|---|
| `user_id` | per-user，不同用户接不同 server，token 各自隔离 |
| `name` | 给 server 起名（"blog"、"github"），langgraph tool 名前缀用 |
| `url` | MCP server 端点 |
| `transport` | `"http" / "sse" / "stdio"`，client 构建方式不同 |
| `encrypted_api_token` | 调 MCP 时 `Authorization: Bearer xxx` 的 token，加密存 |
| `enabled` | 软开关，禁用时不删配置，registry 只加载 `enabled=True` 的 |
| `created_at` | 排序 / 审计 |

**关系是 1:N**：一个用户能配**多个** MCP server。所以 `user_id` 上**不能**加 `unique=True`，只能 `index=True`。这是初学者最容易写错的点。

**per-user MCP client 的必要性**：MCP client 不是全局单例，是每个 user 一个 client，从这张表动态构造。token 不同 → client 不同 → 必须有这张配置表。

### 3.5 Session —— 对话会话

字段：`id`、`user_id`、`title`、`created_at`、`updated_at`。

agent 是有状态的多轮对话，不是"一问一答就忘"。`Session` 是一轮对话的容器，承载历史消息、标题（UI 侧边栏显示）、`updated_at` 决定排序。

langgraph 的 agent run 不是无状态的——每一轮要带上之前的 message history，不然模型记不住上文。`Session` 是这个 history 的归属主键。

1:N 关系：一个 user 多个 session；一个 session 多个 message。

### 3.6 Message —— 单条消息 + tool 调用记录

字段：`id`、`session_id`、`role`、`content`、`tool_calls_json`、`tool_results_json`、`created_at`。

这是 agent **可观测性**和**多轮记忆**的基石。

- `role`：`user` / `assistant` / `tool`，对应 langgraph 的 `HumanMessage` / `AIMessage` / `ToolMessage`。
- `tool_calls_json` / `tool_results_json`：模型这轮调了哪些 tool、参数是什么、结果是什么，结构化存下来。

**为什么要单独存 tool 调用记录**：

1. **断点续传**：session 中断后下次能恢复完整上下文（含 tool 结果）。
2. **可观测**：用户在 UI 能看到"agent 这轮调了 `create_post_draft`、参数 X、返回 Y"——这是 agent 区别于普通 chatbot 的核心展示能力。
3. **重放 / 调试**：出问题时能还原整个 agent run 的 tool 轨迹。
4. **成本**：避免每轮把超长 tool 结果塞回 prompt，可选择截断或摘要。

**为什么用 JSON 字段而不拆两张表**：tool call 和 tool result 强绑定（一个 call 对应一个 result），数量不大、查询模式简单（按 message_id 取），拆表反而增加 JOIN 成本。

### 3.7 跨模型的设计逻辑

**(1) 为什么 LLM config 和 MCP config 都重复存（不共享 blog 的表）**：

agent 是独立 app，不该 JOIN 到 blog 的表去查用户的 LLM。每个 app 管自己的配置，靠 MCP 协议通信，不靠共享数据库——否则又回到耦合的老路。

**(2) 加密字段的统一模式**：

`UserLLMConfig.encrypted_api_key` 和 `MCPServerConfig.encrypted_api_token` 都用 `Settings.token_encryption_key`（Fernet 对称加密）加密。解密时机：
- LLM key：`build_chat_model(user)` 时解。
- MCP token：`MCPRegistry` 建 client 时塞进 `headers["Authorization"]`。

解密后的明文**绝不落库、绝不进日志、绝不进异常消息**。

**(3) per-user 是核心范式**：

几乎所有表都有 `user_id`。agent 是 multi-tenant 的：每个 user 的工具集、模型、会话、凭据完全隔离。这决定了 `MCPRegistry` 必须 per-user cache、`build_agent` 必须 per-user 构建（tools 不同）、langgraph 不再注入全局 context。

### 3.8 加密字段为什么用 Text 不用 String

`encrypted_api_key` 用 `Text` 而非 `String(N)`，因为**密文长度不可预测**。

Fernet 密文 = `version(1B) + timestamp(8B) + IV(16B) + 加密块(明文按 16 字节对齐) + HMAC(32B)`，然后整体 base64 编码。不同 provider key 长度不同（OpenAI ≈ 51 字符，Anthropic ≈ 110，自建网关 200+），加密后还会膨胀 ×1.37。很难提前给 `String(N)` 选一个稳妥的 N——选小了直接 `Data too long for column` 把保存接口打挂。

`Text` = "我不赌这个字段多长"；`String(N)` = "我知道它不会超过 N"。加密输出长度不可控，赌错了就炸，所以用 `Text` 把长度问题甩给应用层。

### 3.9 参考字段定义

```python
# models/model.py（参考结构）
from datetime import UTC, datetime
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship
from tokendanceai.db.base import Base


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    image_file: Mapped[str | None] = mapped_column(String(200), nullable=True, default=None)


class UserLLMConfig(Base):
    __tablename__ = "user_llm_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id), unique=True, nullable=False, index=True)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)        # openai / anthropic / ollama
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)          # gpt-4o / claude-...
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)     # Fernet 密文


class MCPServerConfig(Base):
    __tablename__ = "mcp_server_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id), nullable=False, index=True)  # ⚠️ 不要 unique
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str] = mapped_column(String(255), nullable=False)
    transport: Mapped[str] = mapped_column(String(20), nullable=False)       # http / sse / stdio
    encrypted_api_token: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False, default="新会话")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey(Session.id), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False)            # user / assistant / tool
    content: Mapped[str] = mapped_column(Text, nullable=False)
    tool_calls_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    tool_results_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(UTC))
```

---

<a id="ch-4"></a>
## 第 4 章 数据库基础设施

### 4.1 Base 放 db/ 不放 models/

`Base(DeclarativeBase)` 放 `db/base.py`，不放 `models/`，原因是**依赖方向**。

`Base` 的两个消费者：
1. **models**（`User` 等）：继承 `Base` → `from db.base import Base`
2. **建表逻辑**：`Base.metadata.create_all(engine)` → 需要 `Base` 和 `engine`，都在 db 包内

如果 `Base` 放 `models/`，建表时 `db` 层要反向 import `models` —— 层次倒置，db 基础设施居然知道业务模型包，容易循环。

正确箭头：

```
models ──继承──▶ db (Base, engine, session)    单向 ✅
db ──建表──▶ models                            反向 ❌
```

### 4.2 db/ 包结构

```python
# db/base.py
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

```python
# db/session.py
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tokendanceai.config import settings

engine = create_async_engine(settings.database_url, echo=settings.debug, pool_pre_ping=True)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
```

### 4.3 expire_on_commit=False 的意义

异步 FastAPI 项目里**几乎必设**。控制事务 commit 之后对象是否失效。

- **默认 `True`**：commit 后 session 里所有对象属性被标记过期，下次访问触发一次 SELECT。同步代码自然，异步代码处处踩坑——返回出函数后访问属性会触发隐式 await，报 `MissingGreenlet`。
- **`False`**：commit 后对象保持内存值不查库。commit 完直接 `return obj`，`await db.refresh(user)` 那一步可以省掉。

对照链路：

```
True:  commit → 对象 EXPIRED → return → FastAPI 序列化访问属性 → 触发 SELECT → 💥 MissingGreenlet
False: commit → 对象 FRESH → return → 序列化直接读内存 → ✅ 无 SQL
```

### 4.4 get_db 用 yield 不用 return

`yield` 不是"不返回"，是**延迟返回 + 用完回收**：

```
请求进来 → 执行到 yield → 把 session 交给路由 → 路由跑完 → 回到 yield 之后 → async with 自动 close
```

`return` 会让函数直接退出，session 不自动关闭，连接泄漏。对 db session 这种"用完必须释放"的资源，yield 是唯一正确写法。

---

<a id="part-3"></a>
# 第 3 部分 MCP 协议与桥接

<a id="ch-5"></a>
## 第 5 章 MCP 协议基础

### 5.1 MCP 是什么

MCP（Model Context Protocol）是 Anthropic 提出的开放协议，让 LLM 客户端能以标准方式调用外部工具/资源。它定义了：

- **server**：暴露 tool 的进程（用 `fastmcp` / `mcp` SDK 写）
- **client**：调用 tool 的进程（你 agent 这边）
- **transport**：通信方式（http / sse / stdio）
- **tool schema**：每个 tool 的 name、description、inputSchema（JSON Schema）

### 5.2 为什么不直接把 tool 写在 agent 里

如果 tool 写在 agent 进程内（像 blog 现在那样），有三个问题：

1. tool 直接操作 ORM/service，只能同进程。
2. agent 要为每种业务都写一套 tool 代码，膨胀。
3. tool 的鉴权耦合在进程内 context。

改成 MCP 后：tool 是独立 server，agent 通过协议调用，鉴权在 header 里。agent 一行业务代码都不用写。

### 5.3 安装

只用官方 SDK，不装 adapters 包：

```bash
uv add mcp langgraph langchain langchain-openai  # 或 langchain-anthropic
```

`langchain-mcp-adapters` 不装——我们自己写桥。代价 ~50 行代码，收益是完全掌控 + 吃透协议。

---

<a id="ch-6"></a>
## 第 6 章 类型鸿沟与桥的设计

### 6.1 桥要解决什么

MCP SDK 返回的 tool 和 langgraph 要的 tool 接口不兼容：

```
  MCP SDK 侧                         LangChain / langgraph 侧
  ─────────────                      ──────────────────────
  mcp.types.Tool                     langchain_core.tools.BaseTool
   ├─ name: str                       ├─ name: str
   ├─ description: str                ├─ description: str
   ├─ inputSchema: dict (JSON Schema) ├─ args_schema: type[BaseModel]
  调用：await session.call_tool(name, args)  调用：await tool.ainvoke(args)
  返回：CallToolResult(content=[...])        返回：str / Any
```

### 6.2 桥做三件事

1. **参数 schema 翻译**：MCP 的 `inputSchema`（JSON Schema dict）→ pydantic `BaseModel`（LangChain 的 `args_schema`）。
2. **调用转发**：`await tool.ainvoke(**kwargs)` → `await session.call_tool(name, kwargs)`。
3. **结果归一**：MCP 返回 `CallToolResult.content` 是一组 content block（text/image/…），LangChain 期望字符串。把 text block 拼起来。

### 6.3 为什么 args_schema 必须是 pydantic model

langgraph / LangChain 用 `args_schema` 生成 function-calling 的 JSON Schema 喂给 LLM。LLM 看到 tool schema 才知道"这个 tool 叫什么、要什么参数、参数什么类型"。`args_schema` 没设对，LLM 调 tool 时会传错参数。

---

<a id="ch-7"></a>
## 第 7 章 桥的参考实现

文件放 `src/tokendanceai/mcp/bridge.py`。

```python
from __future__ import annotations
from typing import Any
from langchain_core.tools import BaseTool
from mcp import ClientSession
from mcp.types import Tool as MCPTool
from pydantic import BaseModel, ConfigDict, Field, create_model


# ─── JSON Schema → pydantic model ────────────────────────────────────

def _json_type_to_py(t: str | list[str] | None) -> type:
    """JSON Schema type string → Python type。MCP 的 type 可能是 union（list）。"""
    if t is None:
        return str
    types = t if isinstance(t, list) else [t]
    mapping = {
        "string": str, "integer": int, "number": float,
        "boolean": bool, "array": list, "object": dict, "null": type(None),
    }
    py_types = [mapping.get(x, str) for x in types if x != "null"]
    if not py_types:
        return str
    return py_types[0]  # 简化：只取第一个，复杂 schema 后续再优化


def _schema_to_model(mcp_tool: MCPTool) -> type[BaseModel]:
    """把 MCP tool 的 inputSchema 转成 pydantic model，给 LangChain 用。"""
    schema = mcp_tool.inputSchema or {"type": "object", "properties": {}}
    props: dict[str, Any] = schema.get("properties", {})
    required = set(schema.get("required", []))
    fields: dict[str, Any] = {}
    for fname, fdef in props.items():
        py_type = _json_type_to_py(fdef.get("type", "string"))
        desc = fdef.get("description", "")
        if fname in required:
            fields[fname] = (py_type, Field(..., description=desc))
        else:
            fields[fname] = (py_type | None, Field(default=None, description=desc))
    return create_model(f"{mcp_tool.name}_Args", **fields)


# ─── 单个 MCP tool 的 LangChain 包装 ────────────────────────────────

class MCPToolWrapper(BaseTool):
    """把一个 MCP tool 包成 LangChain BaseTool。持有对 MCP ClientSession 的引用。"""

    # BaseTool 是 pydantic model；ClientSession 不是 pydantic 类型，
    # 要开 arbitrary_types_allowed 才能作为字段
    model_config = ConfigDict(arbitrary_types_allowed=True)

    mcp_name: str
    mcp_description: str = ""
    session: ClientSession

    def _run(self, **kwargs: Any) -> Any:
        raise NotImplementedError("MCP tools are async-only, use ainvoke()")

    async def _arun(self, **kwargs: Any) -> str:
        result = await self.session.call_tool(self.mcp_name, kwargs)
        texts = [
            block.text for block in result.content
            if getattr(block, "type", None) == "text"
        ]
        return "\n".join(texts) if texts else str(result)


# ─── 批量包装 ────────────────────────────────────────────────────────

def wrap_mcp_tools(session: ClientSession, mcp_tools: list[MCPTool]) -> list[BaseTool]:
    """把 MCP session 列出的 tools 全部包成 LangChain BaseTool。"""
    tools: list[BaseTool] = []
    for t in mcp_tools:
        args_model = _schema_to_model(t)
        tools.append(MCPToolWrapper(
            name=t.name,
            description=t.description or "",
            mcp_name=t.name,
            mcp_description=t.description or "",
            session=session,
            args_schema=args_model,
        ))
    return tools
```

### 7.1 关键点理解

1. **`args_schema` 必须是 pydantic model**：langgraph 用它生成 function-calling JSON Schema 喂 LLM。
2. **`_run` 抛 `NotImplementedError`**：MCP 是异步协议，强行同步没意义。
3. **`arbitrary_types_allowed=True`**：`session: ClientSession` 不是 pydantic 原生类型，必须开这个开关，否则实例化报 `PydanticUserError`。
4. **结果归一只取 text block**：MCP tool 可能返回图片、资源引用，但喂给 LLM 上下文时通常只关心文字。后续要支持多模态，这里改成返回结构化 `ToolMessage` content。

---

<a id="ch-8"></a>
## 第 8 章 三种 Transport

MCP SDK 提供三种 transport，每种入口不同。

### 8.1 Streamable HTTP（最常用，blog MCP 用这个）

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client


@asynccontextmanager
async def connect_http(url: str, token: str) -> AsyncGenerator[ClientSession, None]:
    http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
    try:
        async with streamable_http_client(url=url, http_client=http_client) as (read, write):
            async with ClientSession(read_stream=read, write_stream=write) as session:
                await session.initialize()
                yield session
    finally:
        await http_client.aclose()
```

注意：当前 MCP SDK 的 `streamable_http_client` **没有 `headers` 参数**，认证头要靠传入的 `httpx2.AsyncClient` 配置；而 transport 不会关闭你传入的 client，必须在 `finally` 里自己 `aclose()`。

### 8.2 SSE（老版本 MCP server 常用）

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.sse import sse_client


@asynccontextmanager
async def connect_sse(url: str, token: str) -> AsyncGenerator[ClientSession, None]:
    async with sse_client(url=url, headers={"Authorization": f"Bearer {token}"}) as (read, write):
        async with ClientSession(read_stream=read, write_stream=write) as session:
            await session.initialize()
            yield session
```

### 8.3 stdio（本地进程式 MCP server）

```python
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client


@asynccontextmanager
async def connect_stdio(command: str, args: list[str]) -> AsyncGenerator[ClientSession, None]:
    params = StdioServerParameters(command=command, args=args)
    async with stdio_client(server=params) as (read, write):
        async with ClientSession(read_stream=read, write_stream=write) as session:
            await session.initialize()
            yield session
```

### 8.4 为什么是 @asynccontextmanager + yield，不是 __aenter__()

三个 connect 都用 `@asynccontextmanager` + `yield session`，而不是手动 `.__aenter__()` + `return session`。原因：

- **`return session` 会让 `async with` 块立刻退出**，transport 的 `__aexit__` 马上跑、关掉底层流，session 给到调用方时已经报废。
- **`yield session` 只暂停函数、不退出块**，transport 保持开着；等调用方退出它自己的 `async with`，控制流才回到 `yield` 后面，块才退出，资源才释放。
- `ClientSession` 必须用 `async with ClientSession(...) as session` 进入——它的 `__aenter__` 才会启动 dispatcher 收发循环，否则 `initialize()` 永久挂起（等不到响应）。

所以正确调用方式是：

```python
async with connect_http(url, token) as session:
    result = await session.call_tool("...", {})
# 退出块 → session → transport → http_client 依次自动清理，调用方啥都不用关
```

---

<a id="ch-9"></a>
## 第 9 章 Session 生命周期与 Registry

桥代码本身简单，难的是 **session 什么时候建、什么时候关**。MCP session 是有状态的网络连接，不能每次调 tool 都重建（开销大），也不能永远不关（连接泄漏）。

### 9.1 错误写法（session 一退出就废）

```python
async def build_agent(...):
    async with connect_http(url, token) as session:
        tools = wrap_mcp_tools(session, (await session.list_tools()).tools)
        agent = create_react_agent(llm, tools, prompt=...)
        return agent  # 💥 async with 退出 → session 断开，agent 拿到的 tools 全部失效
```

### 9.2 正确写法：封装"活的 client"

```python
# mcp/registry.py（参考结构，接多 server 时再实现）
from contextlib import AsyncExitStack
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from mcp import ClientSession
from tokendanceai.mcp.bridge import wrap_mcp_tools
from tokendanceai.mcp.client import connect_http, connect_sse  # 第 8 章的三种 transport
# token 解密见 mcp/config.py（9.4），这里假设已有 decrypt_token(cfg) 返回明文 Bearer token


@dataclass
class _LiveClient:
    """一个 MCP server 的活动连接。管建连、关闭、缓存 tool 列表。"""
    session: ClientSession
    _stack: AsyncExitStack       # 持有 connect_* 的 async with 栈，用于优雅关闭
    tools_cache: list = field(default_factory=list)
    last_used: datetime = field(default_factory=datetime.now)

    async def close(self) -> None:
        # 退出 AsyncExitStack → 按栈顺序逆序释放 session → transport → http_client
        await self._stack.aclose()


class MCPRegistry:
    """per-user MCP client 缓存，带 TTL，server config 变更时失效。"""

    def __init__(self, cache_ttl: int = 300):
        self._clients: dict[tuple[int, str], _LiveClient] = {}  # (user_id, server_name) → live
        self._ttl = timedelta(seconds=cache_ttl)

    async def get_tools(self, user_id: int, server_configs: list) -> list:
        tools = []
        for cfg in server_configs:
            if not cfg.enabled:
                continue
            live = await self._get_or_build(user_id, cfg)
            tools.extend(live.tools_cache)
        return tools

    async def _get_or_build(self, user_id: int, cfg) -> _LiveClient:
        key = (user_id, cfg.name)
        live = self._clients.get(key)
        if live and datetime.now() - live.last_used < self._ttl:
            live.last_used = datetime.now()
            return live
        # 不存在或过期 → 新建：用 AsyncExitStack.enter_async_context 持有
        # mcp.client.connect_http/sse/stdio（第 8 章），让连接长生命周期存活
        live = await self._build_live(cfg)
        mcp_tools = (await live.session.list_tools()).tools
        live.tools_cache = wrap_mcp_tools(live.session, mcp_tools)
        self._clients[key] = live
        return live

    async def _build_live(self, cfg) -> _LiveClient:
        """按 cfg.transport 建连，用 AsyncExitStack 持有，让 session 长生命周期存活。

        关键：connect_* 是 @asynccontextmanager，必须用 stack.enter_async_context
        进栈，而不是 `async with`——前者把连接的生命周期绑到 stack 上，
        stack 不 aclose() 连接就不关，正是 per-user 缓存要的"活的 client"。
        建连途中任何异常都要先 aclose() 栈，否则半开的 transport 泄漏。
        """
        stack = AsyncExitStack()
        try:
            token = decrypt_token(cfg.encrypted_api_token)  # 9.4 解密
            if cfg.transport == "http":
                session = await stack.enter_async_context(
                    connect_http(cfg.url, token)
                )
            elif cfg.transport == "sse":
                session = await stack.enter_async_context(
                    connect_sse(cfg.url, token)
                )
            elif cfg.transport == "stdio":
                # stdio 需要 command/args，MCPServerConfig 暂无对应字段；
                # 接本地 server 时再扩 cfg + connect_stdio（第 8 章）
                raise NotImplementedError("stdio transport 暂未支持")
            else:
                raise ValueError(f"未知 transport: {cfg.transport}")
        except BaseException:
            await stack.aclose()  # 建连失败：关栈防泄漏；成功则交给 _LiveClient.close()
            raise
        return _LiveClient(session=session, _stack=stack)

    async def invalidate(self, user_id: int) -> None:
        """server config 改完调，关掉该 user 所有 client，下次重建。"""
        keys = [k for k in self._clients if k[0] == user_id]
        for k in keys:
            await self._clients[k].close()
            del self._clients[k]
```

### 9.3 设计要点

1. **per-user 缓存**：key 是 `(user_id, server_name)`，不同 user 的 token 不同，client 不能共享。
2. **TTL 过期重建**：避免长期持有陈旧连接；TTL 到了下次 `get_tools` 自动重建。
3. **`invalidate(user_id)`**：用户改了 url/token/启停，必须调这个把该 user 的 client 全部 close，否则用旧连接。
4. **tools 缓存**：`list_tools()` 不是免费操作（一次网络往返），缓存下来避免每次 agent run 都重查。
5. **`enabled=False` 跳过**：软关停不删配置，registry 加载时直接跳过。

### 9.4 是否独立成 mcp/ 包

MCP 基础设施已经独立成 `mcp/` 包，按代码膨胀程度分阶段填模块：

```
mcp/
  __init__.py
  bridge.py       # MCP tool → LangChain BaseTool 的桥（已实现，见第 7 章）
  client.py       # 三种 transport 连接 connect_http/sse/stdio（已实现，见第 8 章）
  registry.py     # per-user 缓存 + TTL（接多 server 时再建，见 9.2 参考结构）
  config.py       # MCPServerConfig 行 → client 配置，token 解密
  discovery.py    # list_tools / test_connectivity
```

当前已落地 `bridge.py` + `client.py`；`registry.py` 在接多 server、需要 per-user 缓存时再补，参考 9.2 的结构。

### 9.5 Tool 数量爆炸的风险

通用 agent 的主要风险是 tool 数量爆炸。v1 直接全量注入（几十个 tool 没问题）；后续做 dynamic tool loading——加一个 `search_tools(query)` meta-tool，按需 retrieve top-k，避免 context 膨胀。

---

<a id="part-4"></a>
# 第 4 部分 Agent 编排

<a id="ch-10"></a>
## 第 10 章 langgraph 接入

桥和 registry 都好了之后，agent 编排很简单：

```python
# agents/graph.py
from langchain.agents import create_agent  # 注意：langchain.agents，不是 langgraph.prebuilt
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

SYSTEM_PROMPT = """You are a general-purpose autonomous assistant.
You have tools from connected MCP servers. To publish a blog post,
call create_post_draft then publish_post in one flow unless the user
asks to review first. Be concise."""


async def build_agent(llm: BaseChatModel, tools: list[BaseTool],
                      system_prompt: str = SYSTEM_PROMPT):
    """tools 由 MCPRegistry.get_tools(user) 提供。"""
    return create_agent(model=llm, tools=tools, system_prompt=system_prompt)
```

> **API 变更**：旧的 `langgraph.prebuilt.create_react_agent` 在 langgraph 1.x 已
> **废弃**（docstring 明确写着 deprecated in favor of `langchain.agents.create_agent`）。
> 迁移要点：
>
> - 入口从 `langgraph.prebuilt` 挪到 `langchain.agents`；
> - `prompt=` 参数改名为 `system_prompt=`（旧的 `prompt` 还能用但语义变成 state modifier，
>   传字符串虽兼容但建议直接用 `system_prompt`）；
> - 新增 `middleware=()` 中间件系统，可以在 model 前后拦截改写 state（替代旧的
>   `pre_model_hook` / `post_model_hook` / `state_modifier` 的一堆零散参数）。
>   v1 不需要 middleware，留个概念，后续要注入运行时 context（如当前 user/db）时再用。
> 迁移指南见官方 [Migrating from LangGraph v1](https://docs.langchain.com/oss/python/migrate/langgraph-v1)。

### 10.1 不再需要 context_schema

注意：**不再需要 `context_schema=AgentContext`**。现在 blog 用 `AgentContext` 是因为 tool 要拿 `db` / `user`；改成 MCP 之后，tool 的鉴权在 MCP client header 里，langgraph 侧不需要注入业务 context。

这是解耦的关键标志（见 1.3）。

### 10.2 build_chat_model

从 blog 搬过来，解密 LLM key：

```python
# services/llm_services.py（参考结构）
def build_chat_model(llm_config: UserLLMConfig) -> BaseChatModel:
    api_key = decrypt(llm_config.encrypted_api_key)   # Fernet 解密
    if llm_config.provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=llm_config.model, base_url=llm_config.base_url,
                          api_key=api_key)
    elif llm_config.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=llm_config.model, base_url=llm_config.base_url,
                             api_key=api_key)
    # ... 其他 provider
```

---

<a id="ch-11"></a>
## 第 11 章 Runner 与 SSE 事件流

### 11.1 Runner 怎么拿 tools

```python
# agents/runner.py（参考结构）
from sqlalchemy import select
from langchain_core.messages import HumanMessage
from tokendanceai.agents.graph import build_agent
from tokendanceai.mcp.registry import MCPRegistry
from tokendanceai.services.llm_services import build_chat_model


async def run_agent(user, message, session_id, db, registry: MCPRegistry):
    # 1. 拿该 user 的全部 MCP tools
    server_configs = (await db.execute(
        select(MCPServerConfig).where(MCPServerConfig.user_id == user.id)
    )).scalars().all()
    tools = await registry.get_tools(user.id, server_configs)

    # 2. 建 agent（每次 run 都建，因为 tools 可能变；可按需缓存）
    llm = build_chat_model(user.llm_config)
    agent = await build_agent(llm, tools)

    # 3. 拉 history
    history = await load_messages(db, session_id)

    # 4. 流式跑，yield SSE 事件
    async for event in agent.astream(
        {"messages": history + [HumanMessage(message)]},
        stream_mode="updates",
    ):
        yield _to_sse(event)   # 翻译成前端要的 SSE 事件

    # 5. run 结束后把本轮 messages 落库
    await save_messages(db, session_id, ...)
```

### 11.2 事件流格式

搬 blog 的事件流逻辑，四种事件：

- `on_chat_model_stream` —— LLM 流式输出 token
- `on_tool_end` —— tool 调用完成，带结果
- `on_tool_error` —— tool 调用失败
- `done` —— 整个 run 结束

改两处：
1. `tools` 从 `MCPRegistry.get_tools(user)` 拿，不再 `ALL_TOOLS`。
2. `history` 从 `Message` 表按 `session_id` 读，run 结束后把本轮 messages 落库。

### 11.3 前端配合

前端 chat 区域有三种 message row 样式：
- `msg__role` —— 显示角色（你 / Agent）
- `msg__body` —— 正文
- `tool-trace` —— tool 调用轨迹（name + args，对应 `on_tool_end` 事件）

SSE 事件流到前端时，`on_tool_end` 渲染成 `tool-trace` 行，`on_chat_model_stream` 追加到当前 assistant message body。

---

<a id="part-5"></a>
# 第 5 部分 接口与交互

<a id="ch-12"></a>
## 第 12 章 API 表面

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

### 12.1 分页与过滤

- `GET /sessions` 支持按 `updated_at` 倒序，分页。
- `GET /sessions/{id}/messages` 按 `created_at` 正序。
- `/servers` 的增删改完成后要调 `registry.invalidate(user_id)`，让缓存失效。

### 12.2 /servers/{id}/test 连通性测试

这个接口复用 MCP 基础设施：建一个临时 client，调 `list_tools()`，返回 tool 列表。不进 registry 缓存，测完就 close。

```python
# router/servers.py（参考结构）
@router.post("/servers/{server_id}/test")
async def test_server(server_id: int, db = Depends(get_db)):
    cfg = await get_server(db, server_id)
    try:
        session = await connect_http(cfg.url, decrypt(cfg.encrypted_api_token))
        tools = (await session.list_tools()).tools
        await close_session(session)
        return {"ok": True, "tools": [{"name": t.name, "description": t.description} for t in tools]}
    except Exception as e:
        return {"ok": False, "error": str(e)}  # 注意别泄露 token
```

---

<a id="ch-13"></a>
## 第 13 章 自动发布与 Autopilot

### 13.1 不要在 agent 代码里写硬逻辑

不要在 agent 代码里写"先 draft 再 publish"的硬逻辑。两件事分开：

1. **blog 侧**暴露两个独立 tool：`create_post_draft` / `publish_post`。`publish_post` 直接落库，不要 `confirm_endpoint` 那套。
2. **agent 侧**用 system prompt 控制："除非用户要求先看 draft，否则 draft 后立即 publish"。

### 13.2 autopilot 参数

加一个 per-run `autopilot: bool` 参数：

- `autopilot=true`：prompt 强化自主执行，draft 后立即 publish。
- `autopilot=false`：prompt 改成"draft 后停下来等用户确认"。

两种模式同一套代码，只是 system prompt 不同：

```python
AUTOPILOT_PROMPT = """... draft 后立即 publish，不要停下来等确认。"""
MANUAL_PROMPT = """... draft 后停下来，等用户确认再 publish。"""

prompt = AUTOPILOT_PROMPT if autopilot else MANUAL_PROMPT
agent = await build_agent(llm, tools, system_prompt=prompt)
```

---

<a id="part-6"></a>
# 第 6 部分 落地与进阶

<a id="ch-14"></a>
## 第 14 章 开发落地顺序

### 14.1 MCP 桥开发顺序（学习路径）

每一步独立验证，别一次写完才测——MCP 协议细节多，堆到最后 debug 很痛。

1. **先 `uv add mcp`**，写一个独立脚本连一个公开 MCP server（或自建 fastmcp server），手动 `list_tools()` / `call_tool()` 跑通。**先不碰 LangChain**，确认能连、能调。
2. **写 `mcp/bridge.py`**，把上一步的 tool 包成 `BaseTool`，用一个简单 LLM 调一次试试。
3. **写 `mcp/registry.py` 的 `MCPRegistry`**，先支持单一 transport（http），把 per-user + TTL 做出来。
4. **接 langgraph**，跑通"用户消息 → agent 调 MCP tool → 返回"闭环。
5. 再加 SSE / stdio transport、多 server、`invalidate`、连通性测试接口。

### 14.2 整体落地顺序

1. **blog 侧先做 MCP server + token + 两个 post tool**，用 `mcp` CLI 验证连通。
2. **起 agent 骨架**（FastAPI + DB + auth），搬 `llm_services` / `runner`。
3. **实现 `MCPRegistry` + `build_agent`**，接 blog MCP，跑通"写 post → 自动发布"。
4. **再加 session 持久化**、server config 管理页、连通性测试。
5. **之后才是** dynamic tool loading、planner、子 agent 这些扩展。

建议先做 blog 侧（第 1 步），因为没有 MCP server，agent 无从调起。

### 14.3 推荐开发节奏

- 每完成一步，用最小可跑的脚本验证，不要堆代码。
- 遇到 MCP 协议细节不确定时，回到第 5 章 / 附录 A 查坑点。
- 写完一个组件，对照附录 B 看跟 adapters 包的等价物，理解自己写的是不是合理。

---

<a id="ch-15"></a>
## 第 15 章 blog 侧需要配合的改动

agent 要能调通，blog 必须先提供 MCP。改动清单：

1. 加 `FastMCP` server，挂到现有 FastAPI app：`mcp.http_app(path="/mcp")`。
2. 暴露 tool：`create_post_draft` / `publish_post` / `list_my_posts` / `get_post`。内部复用现有 service。
3. 加 `UserApiToken` 模型（每用户一个长期 token），MCP 依赖里解析 Bearer → user，替换现在 tool 里 `get_agent_context().user` 的来源。
4. Account 页加"生成 API token"。

`publish_post` 直接落库，不要 `confirm_endpoint` 那套——自动发布的实现靠 agent 侧 system prompt 控制，不靠 blog 侧的确认机制。

---

<a id="ch-16"></a>
## 第 16 章 扩展点

v1 之后，都用 langgraph 原生能力扩展：

- **多步规划**：换成自定义 `StateGraph`，加 `planner` node 先拆任务再执行。
- **子 agent**：把某些 server 的 tool 包成 subgraph，主 agent 路由过去。
- **Human-in-the-loop**：对破坏性 tool（删 post）用 `interrupt()`，agent run 暂停，前端 resume。
- **可观测性**：接 LangSmith / langgraph 自带 tracing。
- **Dynamic tool loading**：加 `search_tools(query)` meta-tool，按需 retrieve top-k，避免 context 膨胀。

---

<a id="appendix"></a>
# 附录

<a id="appendix-a"></a>
## 附录 A. 已知坑点清单

- **`arbitrary_types_allowed=True`**：`MCPToolWrapper` 里存 `ClientSession` 字段必须开，否则 pydantic 报 `PydanticUserError`。
- **`__aenter__` / `__aexit__` 配对**：第 8 章手动 `__aenter__()` 进去的连接，close 时必须调 `__aexit__(None, None, None)`，否则底层 httpx 连接不释放。`_LiveClient.close()` 就是干这个。
- **`session.initialize()`**：建完 `ClientSession` 必须先 `await session.initialize()` 才能调别的，否则报 `RuntimeError: Server not initialized`。
- **JSON Schema nested / union 类型**：`_schema_to_model` 是简化版，不处理 `$ref` / `oneOf` / 嵌套 object。复杂 tool schema 会丢字段。遇到再增强——用 `pydantic.TypeAdapter` 解析或直接用 langchain 的 `convert_to_openai_function` 兼容路径。
- **tool name 冲突**：不同 MCP server 可能有同名 tool。`MCPToolWrapper.name` 加 server 前缀（如 `blog__create_post_draft`）可避免。
- **async-only**：MCP 工具不能同步调。langgraph 的 `create_react_agent` 默认走 async 路径，没问题；但如果某处代码走 `agent.invoke()`（同步）会触发 `_run` 抛 `NotImplementedError`。确保入口都是 `astream` / `ainvoke`。
- **连接超时**：MCP server 挂了/网络断了，`session.call_tool` 会 hang。包一层 `asyncio.wait_for(..., timeout=30)`，超时给用户友好错误。
- **token 解密时机**：`MCPServerConfig.encrypted_api_token` 解密**只在建 client 时**做一次，解密后的明文**不缓存、不进日志、不进异常消息**。
- **MCPServerConfig.user_id 不能 unique**：一个用户要能配多个 server，`unique=True` 会限制成只能一个。只能 `index=True`。
- **models 里不要 import get_db**：models 是表定义层，不该依赖 session 注入函数，会导致循环 import 风险。
- **expire_on_commit=False**：异步项目几乎必设，否则 commit 后返回对象触发隐式 SELECT 报 `MissingGreenlit`。

<a id="appendix-b"></a>
## 附录 B. 自写桥 vs adapters 包对照表

| adapters 包 | 自己写的对应物 |
|---|---|
| `MultiServerMCPClient` | `MCPRegistry`（第 9 章） |
| `client.get_tools()` | `registry.get_tools(user, configs)`（内部 `session.list_tools()` + `wrap_mcp_tools`） |
| `MultiServerMCPClient` 内部 transport 适配 | 第 8 章三个 `connect_*` 函数 |
| 自动 JSON Schema → BaseTool | `_schema_to_model`（第 7 章） |
| 自动 close | `_LiveClient.close()` |

当你发现自己的 `MCPRegistry` 越来越像 `MultiServerMCPClient`，且要处理的边界情况（重连、协议版本、schema 兼容）越来越多时，就是该考虑切回 adapters 包的信号——**不是为了偷懒，是因为那些边界情况它已经踩过坑了**。

<a id="appendix-c"></a>
## 附录 C. 术语表

| 术语 | 含义 |
|---|---|
| MCP | Model Context Protocol，Anthropic 提出的开放协议，让 LLM 客户端标准调用外部 tool |
| transport | MCP 通信方式：http / sse / stdio |
| BaseTool | LangChain 的工具抽象基类，langgraph agent 用的 tool 必须继承它 |
| args_schema | BaseTool 的参数 schema（pydantic model），langgraph 用它生成 function-calling JSON Schema 喂 LLM |
| ClientSession | MCP SDK 的客户端会话，持有连接、list_tools / call_tool 的入口 |
| _LiveClient | 自写的"MCP server 活动连接"封装，管建连/关闭/缓存 tool |
| MCPRegistry | per-user MCP client 缓存，带 TTL + invalidate |
| context_schema | langgraph 注入运行时上下文的机制；MCP 化后不再需要 |
| autopilot | per-run 参数，控制 agent 是否自主执行破坏性操作（如直接 publish） |
| Fernet | Python 对称加密库（cryptography 包），用于加密 LLM key / MCP token |

---

> **最后一句**：这本书的设计线告诉你"为什么"，实现线告诉你"怎么做"。两者对照着看，每写一个组件都问自己"它对应设计文档里的哪一层、解决了哪个解耦问题"，就能把通用 agent 的架构吃透。
