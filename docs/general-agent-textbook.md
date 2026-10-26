# 通用 Agent 系统设计文档

> **文档性质**：TokenDance AI 通用 Agent 的系统设计文档（System Design Doc）。既是架构决策记录（为什么这么设计），也是落地实现参考（具体怎么写）。
>
> | 项 | 值 |
> |---|---|
> | 文档版本 | v2（接口层补全） |
> | 状态 | 实现中（agent 核心已落地，接口层设计完成待实现） |
> | 适用系统 | `tokendanceai`（FastAPI + langgraph + MCP） |
> | 目标读者 | 后端 / 平台工程师，需具备 Python async、FastAPI、LLM 应用基础 |
> | 关联代码 | `src/tokendanceai/`（本文所有代码片段均对照真实实现） |
>
> **阅读建议**：先读[序章](#序章)与[第 1 章](#ch-1)建立全景和设计目标，再按[第 2 章](#ch-2)理解分层，之后可按需跳读。开发时以[第 14 章 落地顺序](#ch-14)为施工图，遇到协议细节回[第 5–9 章](#part-3)，遇到接口契约回[第 12 章](#ch-12)。

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
## 序章：问题陈述与设计主张

### 要解决的问题

现状是一个「只会写博客」的进程内 agent：tool 直接调 blog 的 ORM，鉴权靠进程内 context，发布靠前端确认按钮。它跟 blog 焊死在一个进程里，加一种新能力就要改 agent 的代码，无法复用到别的业务。

目标是把它重构成一个**独立的通用 Agent 应用**：agent 自己不写一行业务逻辑，所有能力通过 MCP 协议从外部 server 动态获得。blog、GitHub、文件系统都只是它挂载的 MCP server，tool 在运行时发现。

### 核心设计主张（本文档的立论）

> **能力与编排分离**：agent 只负责「编排」（让哪个模型、带着哪些历史、按什么顺序调哪些 tool），「能力」全部外置到 MCP server。两者通过 MCP 协议解耦，鉴权走 MCP client 的 header，不走进程内对象。

这个主张决定了后面每一个设计决策。判断重构是否成功的试金石只有一句话（见 [1.3](#ch-1)）：**agent 代码里不再出现 `context_schema` 和 `get_agent_context()`**。

### 一个关键取舍：自己写桥，不用 adapters 包

实现 MCP 接入有两条路：用现成的 `langchain-mcp-adapters` 包，或只用 MCP 官方 SDK 自己写桥。本文档选后者。

| 维度 | 用 adapters 包 | 自己写桥（本文档选择） |
|---|---|---|
| 代码量 | 几乎为零 | ~50 行桥 + ~60 行 registry |
| 连接生命周期掌控 | 黑盒 | 完全掌控（per-user 缓存、TTL、invalidate） |
| 协议理解 | 不需要懂 | 吃透 JSON Schema、transport、session 握手 |
| 边界情况（重连、协议版本、schema 兼容） | 包已踩过坑 | 自己踩 |

选自己写，是因为这个系统的核心价值就在「per-user 动态连接管理」上（见[第 9 章](#ch-9)），这恰恰是 adapters 包的黑盒部分。什么时候该切回 adapters 包，见[附录 B](#appendix-b)——这也是一个有明确触发条件的架构决策，不是信仰。

### 文档的两条线

- **架构线**（第 1–4、10–13、16 章）：设计目标、分层、数据模型、编排、接口、扩展——回答「为什么」。
- **实现线**（第 5–9、14 章）：MCP 协议、桥、transport、session 管理、落地顺序——回答「怎么做」。

每读一个组件，都对照问自己：它对应哪一层？解决了哪个解耦问题？挡掉了哪个故障？

---

<a id="part-1"></a>
# 第 1 部分 背景与架构

<a id="ch-1"></a>
## 第 1 章 设计目标与系统边界

### 1.1 现状问题分析

当前 blog 里的 in-process agent 有三个**结构性**问题——不是功能缺失，是架构上无法演进：

| # | 问题 | 根因 | 代价 |
|---|---|---|---|
| 1 | tool 只能同进程运行 | `create_post` 靠 `get_agent_context()` 直接拿 `db` 和 `current_user` | 无法跨进程/跨机器部署，agent 和 blog 焊死 |
| 2 | 运行时 context 无法跨网络 | langgraph 的 `context_schema=AgentContext(user, db, llm_config)` 是进程内对象，不可序列化 | agent 无法独立成服务 |
| 3 | 做不到自动发布 | `create_post` 只生成 draft + 返回 `confirm_endpoint`，发布依赖前端确认按钮 | 自动化流程被 human-in-the-loop 卡住 |

三个问题的**共同根因**是：能力（业务 tool）和编排（agent）耦合在同一进程，靠进程内对象传递状态。只要这个耦合在，agent 就永远只能服务 blog 一个业务。

### 1.2 设计目标（Goals）

| 目标 | 可验证标准 |
|---|---|
| G1 能力完全外置 | agent 代码不 import 任何业务 ORM / service |
| G2 多 server 可插拔 | 新增一个 MCP server 只需写一行配置，无需改 agent 代码 |
| G3 运行时发现 tool | agent 启动不硬编码 tool 列表，运行时 `list_tools()` 动态拉取 |
| G4 多租户隔离 | 每个 user 的模型、工具集、凭据、会话完全隔离 |
| G5 凭据安全 | LLM key / MCP token 加密落库，明文不进日志/异常/响应 |
| G6 自动发布 | agent 在一个 run 内完成 draft → publish，无需人工点按钮 |

### 1.3 非目标（Non-Goals，v1 明确不做）

划清边界和定目标同样重要——避免过度设计：

- **不做**多步规划器（planner / StateGraph 自定义节点）——v1 用 langgraph 现成的 react agent，扩展点见[第 16 章](#ch-16)。
- **不做**动态 tool 检索——v1 全量注入 tool（几十个够用），tool 爆炸的应对见 [9.5](#ch-9)。
- **不做**子 agent / 多 agent 协作——留给 v2。
- **不做** stdio transport 的生产支持——v1 只打通 http（blog MCP 用这个）+ sse，stdio 留接口。
- **不自己实现** OAuth/JWT 签发机制——复用已有鉴权，本文档只定义如何消费登录态（见 [12.3](#ch-12)）。

### 1.4 系统上下文（System Context）

重构后，agent 成为独立服务，与外部系统的关系：

```
          ┌────────────┐
  用户 ───▶│  前端 (SSE) │
          └─────┬──────┘
                │ HTTP / text-event-stream
                ▼
     ┌──────────────────────┐        ┌──────────────────┐
     │  general-agent        │──MCP──▶│  blog (FastMCP)   │  create_post_draft / publish_post
     │  FastAPI + langgraph  │──MCP──▶│  GitHub MCP       │  其他能力
     │  + MCPRegistry        │──MCP──▶│  Filesystem MCP   │
     └──────────┬───────────┘        └──────────────────┘
                │ LLM API (per-user key)
                ▼
        OpenAI / Anthropic / 本地 Ollama
```

关键边界：agent **不知道** blog 的数据库长什么样，只知道 blog 暴露了哪些 tool。blog 从「被 import 的库」降级为「一个 MCP server」，和 GitHub、文件系统平级。

### 1.5 解耦的验收标准（本设计的试金石）

> **tool 的鉴权在 MCP client 的 Authorization header 里，langgraph 侧不注入任何业务 context。**

落到可检查的代码特征：

- ✅ agent 代码里**没有** `context_schema=AgentContext`
- ✅ agent 代码里**没有** `get_agent_context()`
- ✅ `build_agent` 的入参只有 `llm` / `tools` / `system_prompt`，没有 `db` / `user`
- ✅ tool 的鉴权信息只存在于 MCP client 建连时的 header（见[第 8 章](#ch-8)）

这四条同时成立，才叫「真通用 agent」。任何一条不成立，就说明能力和编排还没解耦干净。

---

<a id="ch-2"></a>
## 第 2 章 项目结构

### 2.1 目录结构（对照真实代码）

```
src/tokendanceai/
  main.py                 # FastAPI app 装配：mount static、include router、lifespan
  config.py               # Settings 单例（pydantic-settings）
  templating.py           # Jinja2 templates
  db/
    base.py               # DeclarativeBase（见第 4 章 4.1：为什么在这里）
    db_session.py         # async_engine, AsyncSessionLocal, get_db
  models/
    model.py              # User, UserLLMConfig, MCPServerConfig, Session, Message
  schemas/                # Pydantic 请求/响应契约（见第 12 章 12.4）
    user.py               # UserCreate / UserPublic / TokenResponse
    schema.py             # agent / session / server / account 的 schema
  utils/
    crypto.py             # Fernet encrypt/decrypt（LLM key / MCP token 共用）
  pages/                  # 返回 HTML 的路由
    home.py
  router/                 # JSON / SSE API 路由（见第 12 章）
    agent.py              # POST /agent/run (SSE)
    sessions.py / servers.py / account.py / auth.py
  mcp/                    # ★ MCP 基础设施（本系统的核心）
    bridge.py             # MCP tool → LangChain BaseTool 的桥（第 7 章）
    client.py             # 三种 transport：connect_http/sse/stdio（第 8 章）
    registry.py           # per-user MCP client 缓存 + TTL（第 9 章）
    config.py             # token 解密：encrypted_api_token → Bearer（待补，见 9.4）
  agents/                 # ★ 编排层（不碰业务）
    graph.py              # build_agent(llm, tools, system_prompt)（第 10 章）
    runner.py             # run_agent() → SSE 事件流（第 11 章）
    memory.py             # load_messages / save_messages（第 11 章 11.1.1）
  services/
    llm_service.py        # build_chat_model（第 10 章 10.2）
```

> 两个 `config.py` 不冲突：根目录 `config.py` 是全局 `Settings`；`mcp/config.py` 是 MCP 子域的配置转换（token 解密）。按就近原则各管各的。

### 2.2 分层与依赖方向

架构的第一性原则：**依赖单向，永不成环**。上层知道下层，下层不知道上层。

```
          ┌─────────────────────────────┐
  入口层   │  pages/(HTML)   router/(API) │
          └──────┬──────────────┬────────┘
                 ▼              ▼
  编排层   ┌──────────┐   ┌──────────┐
          │ agents/  │   │ services/│
          └────┬─────┘   └────┬─────┘
               ▼              │
  能力层   ┌──────────┐       │
          │  mcp/    │       │
          └────┬─────┘       │
               ▼             ▼
  模型层        ┌──────────────────┐
               │     models/       │
               └────────┬──────────┘
                        ▼
  基础层   ┌──────────────────────────┐
          │  db/   config.py  utils/  │  ← 横切，谁都能 import，自己不 import 上层
          └──────────────────────────┘
```

逐层职责与边界：

| 层 | 包 | 职责 | 禁止 |
|---|---|---|---|
| 入口 | `pages/` `router/` | 参数校验、依赖装配、响应包装 | 写业务逻辑、直接拼 SQL |
| 编排 | `agents/` `services/` | 对话编排、SSE 流、建模型 | import 具体业务 ORM |
| 能力 | `mcp/` | 桥、transport、连接缓存 | 知道「blog」是什么 |
| 模型 | `models/` | ORM 表定义 | import `get_db` / 上层 |
| 基础 | `db/` `config.py` `utils/` | 引擎、会话、配置、加密 | import 任何上层 |

**三条关键边界理由**：

1. **`pages/` 与 `router/` 分开**：HTML 和 JSON/SSE 的错误处理、认证、内容协商完全不同。混在一个包里，中间件和依赖会互相污染。
2. **`agents/` 不碰业务**：这是 [G1](#ch-1) 的代码级落实。编排层只认 `BaseTool` 抽象，不认 tool 背后是 blog 还是 GitHub。
3. **`mcp/` 不知道「blog」**：桥和 registry 对所有 MCP server 一视同仁。哪个 server 叫什么、暴露什么 tool，是运行时数据，不是编译时知识。

### 2.3 配置管理（`config.py`）

配置是**横切单例**，放包根目录，用 `pydantic-settings` 从环境变量/`.env` 读，模块级实例化一次：

```python
# src/tokendanceai/config.py（对照真实实现）
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="TokenDance",   # 环境变量前缀，如 TokenDancedatabase_url
    )

    database_url: str
    secret_key: str                       # JWT 签名
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    ai_encryption_key: SecretStr          # Fernet 密钥，加密 LLM key / MCP token


settings = Settings()  # type: ignore  # 字段从 .env 注入
```

**设计决策**：

- **`ai_encryption_key` 用 `SecretStr`**：防止它被 `print(settings)` 或日志误打出来——`SecretStr` 的 `repr` 是 `**********`，取值要显式 `.get_secret_value()`。这是凭据安全（[G5](#ch-1)）的第一道防线。
- **一把钥匙加密两种凭据**：LLM key 和 MCP token 共用 `ai_encryption_key`（见 [utils/crypto.py](../src/tokendanceai/utils/crypto.py)），不为每种凭据单独开钥匙。简单、够用；要做密钥轮转时再引入 key-id。
- **不给 config 开文件夹**：它是横切单例，不是领域对象。一个领域才配一个包；config 开包反而让「到底 import 哪个 Settings」变模糊。

> **待对齐**：本文档多处引用 `settings.mcp_cache_ttl`（registry TTL）和 `settings.mcp_request_timeout`（tool 调用超时，见[附录 A](#appendix-a)）。真实 `Settings` 里还没有这两个字段，落地时补上（都给默认值，分别 `300` / `30`）。

---

<a id="part-2"></a>
# 第 2 部分 数据层

<a id="ch-3"></a>
## 第 3 章 数据模型设计

### 3.1 数据模型总览

五张表，一个身份中心（`User`），两类挂载数据（配置 / 会话）：

```
                    ┌──────────┐
                    │   User    │  agent 自己的身份锚点
                    └─────┬─────┘
          ┌──────────┬────┴────┬──────────────┐
          │ 1:1      │ 1:N     │ 1:N          │
          ▼          ▼         ▼              │
  ┌──────────────┐ ┌──────────────────┐ ┌──────────┐
  │UserLLMConfig │ │ MCPServerConfig   │ │ Session   │
  │ 用哪个模型     │ │ 挂哪些能力(命脉)   │ │ 一轮对话   │
  │ encrypted_key│ │ encrypted_token   │ └────┬─────┘
  └──────────────┘ └──────────────────┘      │ 1:N
                                              ▼
                                        ┌──────────┐
                                        │ Message   │ 消息 + tool 轨迹
                                        └──────────┘
```

| 模型 | 关系 | 职责 | 核心设计点 |
|---|---|---|---|
| `User` | — | agent 内部身份 | 与 blog user 两套表，靠 MCP token 桥接（3.2） |
| `UserLLMConfig` | User 1:1 | 用户自带模型 + key | `user_id` unique；key 加密（3.3） |
| `MCPServerConfig` | User 1:N | 用户挂的能力 | `user_id` **不可** unique；token 加密（3.4） |
| `Session` | User 1:N | 多轮对话容器 | `updated_at` 决定侧边栏排序（3.5） |
| `Message` | Session 1:N | 消息 + tool 调用轨迹 | tool call/result 存 JSON（3.6） |

贯穿五张表的两条范式：**per-user 隔离**（几乎每张表都有 `user_id`，见 3.7）和**凭据加密**（两个 `encrypted_*` 字段，见 3.8）。

### 3.2 User —— agent 自己的用户体系

agent 是独立 app，不能复用 blog 的用户表。`User` 是 agent 内部身份锚点，所有 per-user 配置都挂在它的 `user_id` 上。

鉴权不走"同进程 current_user"，而是通过 MCP 的 `Authorization: Bearer <token>` 头传递。所以 `User` 是 agent 内部用的，跟 blog 的 user 是两套表，靠 MCP token 桥接。

字段：`id`、`username`、`email`、`password_hash`、`image_file`（头像）。

### 3.3 UserLLMConfig —— 每用户自己的 LLM 配置

字段：`user_id`、`provider`、`base_url`、`model`、`encrypted_api_key`。

**为什么 per-user**：通用 agent 不内置任何模型——不同用户用不同 provider（OpenAI / Anthropic / 本地 Ollama）、不同 base_url、不同 quota。agent 只负责编排，模型和密钥都是用户自带的。

**为什么加密 `api_key`**：API key 是高敏感凭据，明文存库 = 数据库被拖就全员 key 泄露。加密密钥从 `settings.ai_encryption_key` 来（Fernet），解密只在 `build_chat_model` 时做一次（见 [services/llm_service.py](../src/tokendanceai/services/llm_service.py)）。

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

`UserLLMConfig.encrypted_api_key` 和 `MCPServerConfig.encrypted_api_token` 都用 `settings.ai_encryption_key`（Fernet 对称加密，见 [utils/crypto.py](../src/tokendanceai/utils/crypto.py)）加密。解密时机：

- LLM key：`build_chat_model(llm_config)` 时解（[services/llm_service.py:9](../src/tokendanceai/services/llm_service.py#L9)）。
- MCP token：`MCPRegistry` 建 client 时解，塞进 `headers["Authorization"]`（见[第 9 章](#ch-9)）。

解密后的明文**绝不落库、绝不进日志、绝不进异常消息**（脱敏约定见 [12.10](#ch-12)）。

**(3) per-user 是核心范式**：

几乎所有表都有 `user_id`。agent 是 multi-tenant 的：每个 user 的工具集、模型、会话、凭据完全隔离。这决定了 `MCPRegistry` 必须 per-user cache、`build_agent` 必须 per-user 构建（tools 不同）、langgraph 不再注入全局 context。

### 3.8 加密字段为什么用 Text 不用 String

`encrypted_api_key` 用 `Text` 而非 `String(N)`，因为**密文长度不可预测**。

Fernet 密文 = `version(1B) + timestamp(8B) + IV(16B) + 加密块(明文按 16 字节对齐) + HMAC(32B)`，然后整体 base64 编码。不同 provider key 长度不同（OpenAI ≈ 51 字符，Anthropic ≈ 110，自建网关 200+），加密后还会膨胀 ×1.37。很难提前给 `String(N)` 选一个稳妥的 N——选小了直接 `Data too long for column` 把保存接口打挂。

`Text` = "我不赌这个字段多长"；`String(N)` = "我知道它不会超过 N"。加密输出长度不可控，赌错了就炸，所以用 `Text` 把长度问题甩给应用层。

### 3.9 完整字段定义（对照真实实现）

下面是真实 `models/model.py` 的字段定义，包含两处待修复的拼写错误（已标注 ⚠️）。

```python
# models/model.py（对照 src/tokendanceai/models/model.py）
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

    # relationship: lazy="joined" 一次 JOIN 拿 config，省第二条 SQL；uselist=False 标量关系
    llm_config: Mapped["UserLLMConfig | None"] = relationship(
        back_populates="user", uselist=False, lazy="joined", cascade="all, delete-orphan"
    )


class UserLLMConfig(Base):
    __tablename__ = "user_llm_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id), unique=True, nullable=False, index=True)
    user: Mapped["User"] = relationship(back_populates="llm_config")  # 反向引用
    provider: Mapped[str] = mapped_column(String(50), nullable=False)        # openai / anthropic
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    model: Mapped[str] = mapped_column(String(50), nullable=False)           # gpt-4o / claude-...
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)     # Fernet 密文
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    # ⚠️ 待修复: Update_at → updated_at（Python 命名规范）
    Update_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class MCPServerConfig(Base):
    __tablename__ = "mcp_server_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id), nullable=False, index=True)  # ⚠️ 不要 unique
    name: Mapped[str] = mapped_column(String(50), nullable=False)
    url: Mapped[str] = mapped_column(String(255), nullable=False)
    transport: Mapped[str] = mapped_column(String(50), nullable=False)       # http / sse / stdio
    encrypted_api_token: Mapped[str] = mapped_column(Text, nullable=False)
    enabled: Mapped[bool] = mapped_column(default=True, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class Session(Base):
    __tablename__ = "session"  # 注意：单数，不是 "sessions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id), nullable=False, index=True)
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
    # ⚠️ 待修复: Update_at → updated_at
    Update_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))


class Message(Base):
    __tablename__ = "messages"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    session_id: Mapped[int] = mapped_column(ForeignKey(Session.id), nullable=False, index=True)
    role: Mapped[str] = mapped_column(String(50), nullable=False)            # user / assistant / tool
    content: Mapped[str] = mapped_column(Text)
    tool_calls_json: Mapped[str] = mapped_column(Text)      # ⚠️ nullable 未显式设，SQLAlchemy 默认可空
    tool_results_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(UTC))
```

**待修复清单**（接口层 [12.3](#ch-12) 已列为前置条件）：

1. `UserLLMConfig.Update_at` / `Session.Update_at` → `updated_at`（两处）
2. `Message.content / tool_calls_json / tool_results_json`：显式标 `nullable=True`（语义上这三个字段可空；现在靠 SQLAlchemy 默认，不清晰）
3. 可选：`Session.id` 上加外键删除级联（`ondelete="CASCADE"`），删 session 时自动删关联 message（见 [12.7](#ch-12) 路由实现）

---

<a id="ch-4"></a>
## 第 4 章 数据库基础设施

### 4.1 Base 放 db/ 不放 models/

`Base(DeclarativeBase)` 放 `db/base.py`，不放 `models/`，原因是**依赖方向**。

`Base` 的两个消费者：

1. **models**（`User` 等）：继承 `Base` → `from tokendanceai.db.base import Base`
2. **建表逻辑**：`Base.metadata.create_all` → 需要 `Base` 和 `engine`，都在 db 包内（本项目在 `main.py` 的 `lifespan` 里调，见 [11.x / 第 13 章](#ch-13)）

如果 `Base` 放 `models/`，建表时 `db` 层要反向 import `models` —— 层次倒置，db 基础设施居然知道业务模型包，容易循环。

正确箭头：

```
models ──继承──▶ db (Base, engine, session)    单向 ✅
db ──建表──▶ models                            反向 ❌
```

> **建表时的隐藏陷阱**：`Base.metadata.create_all` 只建**已经被 import 过**的 model 对应的表。如果 `main.py` 建表前没 `import tokendanceai.models.model`，`Base.metadata` 里是空的，一张表都不会建。所以 lifespan 里要么显式 import model 模块，要么靠 router 的 import 链间接触发。

### 4.2 db/ 包结构（对照真实实现）

```python
# db/base.py
from sqlalchemy.orm import DeclarativeBase

class Base(DeclarativeBase):
    pass
```

```python
# db/db_session.py（对照 src/tokendanceai/db/db_session.py）
from collections.abc import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from tokendanceai.config import settings

async_engine = create_async_engine(url=settings.database_url)
AsyncSessionLocal = async_sessionmaker(bind=async_engine, class_=AsyncSession, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
```

**两个可选的生产加固**（真实代码暂未加，落地时建议补）：

```python
# ① 引擎参数：开发期回显 SQL + 连接健康检查，防止拿到被 DB 断掉的死连接
async_engine = create_async_engine(
    url=settings.database_url,
    echo=settings.debug,        # 需要给 Settings 补 debug 字段
    pool_pre_ping=True,         # 每次取连接前 ping 一下，自动丢弃失效连接
)

# ② get_db 里显式 rollback：异常时回滚未提交事务，避免连接带着脏事务回池
async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
```

> `async with` 退出时本就会 close 并隐式回滚未提交事务，所以不加 try/except 不会泄漏连接。显式 `rollback()` 的价值是**语义清晰**和**尽早释放**——出异常当场回滚，而不是等上下文退出。属于「正确但可以更好」，不是 bug。

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

### 5.2 为什么选 MCP（方案对比）

能力外置有多种做法，MCP 是权衡后的选择：

| 方案 | 耦合度 | 鉴权 | tool 发现 | 问题 |
|---|---|---|---|---|
| 进程内 tool（blog 现状） | 最高 | 进程内 context | 编译期硬编码 | 无法跨进程、业务膨胀 |
| 自定义 HTTP RPC | 中 | 自己设计 | 自己设计 | 每个 server 协议不一，重复造轮子 |
| OpenAPI + 代码生成 | 中 | 标准 | 靠 spec | tool 语义/描述不是为 LLM 设计的 |
| **MCP** | **最低** | **标准 header** | **运行时 `list_tools`** | 需要写一层桥（~50 行） |

MCP 的决定性优势：它是**专为 LLM tool-calling 设计**的协议——tool 自带给模型看的 `description` 和 `inputSchema`，`list_tools` 让发现发生在运行时而非编译时。这正好命中 [G2/G3](#ch-1)（可插拔 + 运行时发现）。代价是 SDK 返回的类型和 LangChain 不兼容，需要一层桥（第 6–7 章）。

改成 MCP 后，[1.1](#ch-1) 的三个结构性问题一次解决：tool 是独立 server（解耦）、agent 不写业务代码（不膨胀）、鉴权在 header（可跨网络）。

### 5.3 依赖选型

只用官方 SDK，不装 adapters 包：

```bash
uv add mcp langgraph langchain langchain-openai langchain-anthropic
```

**为什么不装 `langchain-mcp-adapters`**：这是一个架构决策，不是偷懒。

- **可控性**：自己写的桥（第 7 章）~50 行，行为完全可预测；adapters 包封装了连接生命周期，和我们要的 per-user TTL 缓存（第 9 章）打架。
- **学习价值**：吃透「MCP 类型 → LangChain 类型」的映射，是看懂整个系统的前提。
- **依赖面**：少一个第三方包，少一处版本锁和潜在的 breaking change。

代价是自己维护桥的 ~50 行；对一个以 MCP 为核心的系统，这 50 行本就该是自己的核心资产。

---

<a id="ch-6"></a>
## 第 6 章 类型鸿沟与桥的设计

### 6.1 桥要解决的根本问题

MCP SDK 返回的 tool 和 langgraph 要的 tool 接口不兼容——这不是版本问题，是**协议设计哲学不同**：

```
  MCP SDK 侧                         LangChain / langgraph 侧
  ─────────────                      ──────────────────────
  mcp.types.Tool                     langchain_core.tools.BaseTool
   ├─ name: str                       ├─ name: str
   ├─ description: str                ├─ description: str
   ├─ input_schema: dict (JSON Schema) ├─ args_schema: type[BaseModel]
  调用：await session.call_tool(name, args)  调用：await tool.ainvoke(args)
  返回：CallToolResult(content=[...])        返回：str / Any
```

| 差异 | 根因 | 破坏点 |
|---|---|---|
| schema 格式不同 | MCP 面向动态类型 JSON；LangChain 面向静态类型 Python | langgraph 用 `args_schema` 生成 function-calling 喂 LLM，拿不到就不知道要什么参数 |
| 调用接口不同 | MCP 通过 session 调，LangChain 通过对象方法调 | agent 编排层是 LangChain 的 `create_react_agent`，它只认 `BaseTool` |
| 返回结构不同 | MCP 支持多模态 content block，LangChain 通常期望字符串 | 不归一会在喂 LLM 上下文时炸 |

桥的使命：让 langgraph 以为这些 MCP tool 就是普通的 LangChain tool，无缝接入 react agent。

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

### 7.2 与真实实现的两处差异（待修复）

上面是**目标实现**；真实 [mcp/bridge.py](../src/tokendanceai/mcp/bridge.py) 有两处需要对齐：

| # | 真实代码 | 目标 | 影响 |
|---|---|---|---|
| 1 | `mcp_tool.input_schema`（第 39 行） | 取决于 SDK 版本 | 当前 MCP python SDK 的字段是 `inputSchema`（驼峰）。若运行报 `AttributeError: input_schema`，说明 SDK 用驼峰，需改回 `inputSchema`。**落地前务必对当前 SDK 版本验证一次** |
| 2 | required 分支用 `Field(default=None, ...)`（第 47 行） | `Field(..., ...)` | **功能性 bug**：required 字段也给了默认值 `None`，于是没有任何字段是真正必填的。LLM 漏传必填参数时，pydantic 不报错，错误被推迟到 MCP server 端，报错信息更难懂 |

**#2 的修复**（让 required 真正必填）：

```python
if fname in required:
    fields[fname] = (py_type, Field(..., description=desc))         # ... = 必填
else:
    fields[fname] = (py_type | None, Field(default=None, description=desc))
```

**架构理由**：schema 翻译层是 agent 和 LLM 之间的「契约边界」。契约在这里失真（必填变可选），下游（LLM → MCP server）全程带病运行，排查成本成倍放大。边界层的正确性优先级最高。

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

> 该模块已落地为 [mcp/registry.py](../src/tokendanceai/mcp/registry.py)，下面的结构与真实代码一致（真实代码里 `decrypt_token` 还是占位 `_decrypt_token`，见 9.4）。

```python
# mcp/registry.py（对照真实实现）
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

### 9.4 token 解密：`_decrypt_token` 待补

真实 [mcp/registry.py:20](../src/tokendanceai/mcp/registry.py#L20) 的 `_decrypt_token` 仍是占位实现——**有 token 就抛 `NotImplementedError`**，逼着落地时把解密补上：

```python
def _decrypt_token(encrypted: str | None) -> str | None:
    if not encrypted:
        return None
    raise NotImplementedError("token 解密未实现，见 mcp/config.py（教材 9.4）")
```

补齐方案：复用已有的 [utils/crypto.py](../src/tokendanceai/utils/crypto.py)（Fernet），不要另起炉灶：

```python
# mcp/registry.py 顶部
from tokendanceai.utils.crypto import decrypt

def _decrypt_token(encrypted: str | None) -> str | None:
    if not encrypted:
        return None
    return decrypt(encrypted)   # 用 settings.ai_encryption_key 解出明文 Bearer token
```

**架构理由**：加密/解密是一对，必须用同一把钥匙、同一套实现。LLM key 已经走 `utils/crypto.py`（见 [services/llm_service.py](../src/tokendanceai/services/llm_service.py)），MCP token 再写一套 Fernet 就有两处要维护、两处可能配错钥匙。凭据处理收敛到**单一模块**是安全系统的基本纪律。

> 真实 `_build_live` 里有 `assert token`（第 85 行）：解出来是 `None` 会直接 `AssertionError`。生产环境关 assert（`python -O`）时这行会被优化掉，建议落地时换成显式 `if token is None: raise ...`。

### 9.5 mcp/ 包的演进规划

MCP 基础设施已独立成 `mcp/` 包，按代码膨胀程度分阶段填模块：

```
mcp/
  bridge.py       # ✅ 已实现：MCP tool → LangChain BaseTool 的桥（第 7 章）
  client.py       # ✅ 已实现：三种 transport connect_http/sse/stdio（第 8 章）
  registry.py     # ✅ 已实现：per-user 缓存 + TTL（9.2）
  config.py       # ⬜ 待补：token 解密收敛处（9.4）；也可直接复用 utils/crypto
  discovery.py    # ⬜ 可选：test_connectivity（连通性测试，见 12.8 的 /test 端点）
```

**tool 爆炸的应对（规模上限）**：当前 `get_tools` 全量注入所有 enabled server 的 tool。几十个 tool 没问题；上百个时会撑爆 prompt、拖慢决策。届时的演进路径（v2）：按 server 分组做两级检索（先选 server 再选 tool）、或引入 tool 的向量检索。v1 不做（[1.3 非目标](#ch-1)），但接口留好了——`get_tools` 是唯一入口，改它不影响上层。

### 9.5 Tool 数量爆炸的风险

通用 agent 的主要风险是 tool 数量爆炸。v1 直接全量注入（几十个 tool 没问题）；后续做 dynamic tool loading——加一个 `search_tools(query)` meta-tool，按需 retrieve top-k，避免 context 膨胀。

---

<a id="part-4"></a>
# 第 4 部分 Agent 编排

<a id="ch-10"></a>
## 第 10 章 构建 Agent Graph

MCP 桥（第 7 章）、transport（第 8 章）、registry（第 9 章）把 tool 侧准备好了。本章转到 agent 侧：如何用 langgraph v2 + LangChain 的 `create_agent` 构建一个支持 tool-calling 的对话循环。

### 10.1 核心构建函数：`build_agent`

真实实现在 [agents/graph.py](../src/tokendanceai/agents/graph.py)，只有 ~30 行：

```python
# agents/graph.py
from langchain.agents import create_agent  # ⚠️ langchain.agents（不是 langgraph.prebuilt）
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool
from langgraph.graph.state import CompiledStateGraph

SYSTEM_PROMPT = """You are the assistant for a token-dance AI workspace.
Your capabilities come from tools exposed by the user's connected MCP
servers — you do not carry credentials yourself; each tool's auth is
handled in the MCP client connection, so never ask for or inline tokens.

When you have several independent steps, call their tools together in
one turn rather than one at a time. Prefer a specific tool over shell.

For any action that is hard to reverse or reaches the outside world —
publishing, deleting, sending, posting, spending — confirm the intent
with the user first, unless they have already told you to just do it.

Report results truthfully and concisely: say plainly what succeeded,
what failed (with the error), and what you skipped. Do not overstate.
"""


async def build_agent(
    llm: BaseChatModel, tools: list[BaseTool], system_prompt: str = SYSTEM_PROMPT
) -> CompiledStateGraph:
    """MCP registry provide tools"""
    return create_agent(model=llm, tools=tools, system_prompt=system_prompt)
```

**设计要点**：

1. **入口是 `langchain.agents.create_agent`，不是 langgraph 自己的**。langgraph v2 把 agent 构建函数挪回 LangChain 核心，`langgraph.prebuilt.create_react_agent` 在 v1.x 已废弃（docstring 明确写 deprecated）。迁移要点：
   - 导入从 `langgraph.prebuilt` → `langchain.agents`；
   - `prompt=` 参数改名为 `system_prompt=`（旧的 `prompt` 语义变成 state modifier）；
   - 新增 `middleware=()` 系统替代旧的 hook 参数（v1 不用 middleware，需要注入运行时 context 时再用）。
2. **不需要 `context_schema=AgentContext`**。blog 用 `AgentContext` 是因为 tool 要拿 `db` / `user`；改成 MCP 后，tool 的鉴权在 MCP client header 里，langgraph 侧不需要注入业务 context。这是解耦的关键标志（见 [1.3](#ch-1)）。
3. **system prompt 写什么**：
   - 明确 tool 来自 MCP、agent 自己不持有凭据（堵住"帮我登录这个 API" 类需求）；
   - 告诉模型「该并行就并行」，避免它一个一个串行调 tool；
   - 明确破坏性/外向操作要先确认（安全栅栏）；
   - 要求如实汇报（agent 倾向于美化失败，prompt 里逼它说实话）。

> **API 迁移参考**：官方 [Migrating from LangGraph v1](https://docs.langchain.com/oss/python/migrate/langgraph-v1) 文档详细说明了为什么废弃 `create_react_agent` 以及如何升级到新的 `create_agent`。关键词：`system_prompt` / `middleware` / state schema 简化。

### 10.2 构建 LLM 实例：`build_chat_model`

tools 由 registry 提供，llm 由 `services/llm_service.py` 构建，根据 user 配置的 provider 返回对应的 LangChain chat model。真实代码：

```python
# services/llm_service.py
from langchain_core.language_models import BaseChatModel
from pydantic import SecretStr
from tokendanceai.models.model import UserLLMConfig
from tokendanceai.utils.crypto import decrypt


def build_chat_model(llm_config: UserLLMConfig) -> BaseChatModel:
    api_key = decrypt(llm_config.encrypted_api_key)

    if llm_config.provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(
            model=llm_config.model,
            api_key=SecretStr(api_key),
            base_url=llm_config.base_url,
        )
    elif llm_config.provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(
            model_name=llm_config.model,
            api_key=SecretStr(api_key),
            timeout=None,
            stop=None,
        )
    else:
        raise ValueError(f"unknown provider: {llm_config.provider!r}")
```

**设计要点**：

1. **解密是第一行**：`encrypted_api_key` 从数据库读出来是 Fernet 密文，必须调 `utils/crypto.decrypt` 解出明文，再喂给 LangChain。解密失败（钥匙错、密文被篡改）直接抛异常，上层（runner 或 router）会捕获并回 500。
2. **`SecretStr` 包装**：LangChain 的 `api_key=` 参数类型是 `SecretStr | str`，用 `SecretStr` 包一层可以避免 model 序列化时把明文 key 打印到日志。生产环境必须用它。
3. **OpenAI 支持 `base_url`，Anthropic 不支持**：真实代码里 `ChatOpenAI` 接受 `base_url`（用于 Azure OpenAI / 兼容 API），`ChatAnthropic` 不接受（官方 SDK 只支持默认 endpoint）。如果用户配了 Anthropic 的 `base_url`，该字段直接被忽略，不会报错。
4. **未知 provider 直接报错**：不要做"默认到 openai"之类的降级，用户配错了 provider 应该报错让他知道。

> **扩展新 provider**（如 Gemini / Mistral）：加一个 `elif` 分支，导入对应的 `langchain_*` 包，喂 `model_name` 和 `api_key`。LangChain 的 chat model 接口高度统一，代价通常是一个 `uv add langchain-<provider>`。

### 10.3 把 llm + tools 装配成 agent 的完整流程

runner 里会调：

```python
llm = build_chat_model(user.llm_config)
tools = await registry.get_tools(user.id, server_configs)
agent = await build_agent(llm, tools)
```

三行之后得到的 `agent` 是一个 `CompiledStateGraph`，可以 `await agent.ainvoke(inputs)` 或 `agent.astream(inputs, stream_mode=...)`。它内部是一个状态机：

```
   user input → [agent 节点] → 有 tool_calls? ┬─ 是 → [tools 节点] → 回到 agent
                                              └─ 否 → 结束
```

- **agent 节点**：调 LLM，拿响应。如果 LLM 返回 tool_calls，就进 tools 节点；否则结束。
- **tools 节点**：并行跑所有 tool_calls，把结果塞进 `ToolMessage` 回到 agent 节点。

langgraph 管理循环、消息列表和状态；runner 只需要喂 `{"messages": [...]}` 然后 stream 出来。

---

<a id="ch-11"></a>
## 第 11 章 Runner 与 SSE 事件流

Runner 是 agent 层的「对外总线」：上接 router（第 12 章），下调 graph（第 10 章）、registry（第 9 章）、memory（11.3）。它的职责是把一次对话请求跑成一串 SSE 事件推给前端，并在结束时把本轮消息落库。真实实现在 [agents/runner.py](../src/tokendanceai/agents/runner.py)，~125 行。

### 11.0 Runner 的四个设计难点

在看代码前，先立起本章要解决的四个难点——它们决定了 runner 为什么长这样：

1. **流式双通道**：前端既要「打字机效果」（token 增量），又要「工具调用轨迹」（完整的 tool_call/tool_result）。langgraph 的 `astream` 用两种 `stream_mode` 分别给这两类数据，runner 要同时订阅、分别处理。
2. **落库时机**：消息必须在 run **结束后**整批落库，且**无论成功还是异常**都要落（否则对话断在中途、历史丢失）。用 `try/finally` 保证。
3. **只转发该转发的**：`messages` 模式会吐出所有节点的 chunk（包括 tool 节点的噪声），runner 只能转发 agent 节点的文本增量，否则前端会收到一堆乱码。
4. **session 生命周期**：runner 是 async generator，`db` session 必须由调用方（router）持有、在 generator 耗尽前不能关——这条约束影响接口层怎么注入 `db`（见 12.6）。

### 11.1 run_agent：主流程

```python
# agents/runner.py
import json
from collections.abc import AsyncGenerator, Sequence
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import (
    AIMessage, BaseMessage, HumanMessage, ToolCall, ToolMessage,
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
    # 1. 拿该 user 的全部 MCP tools（registry 内部按 (user, server) 缓存活连接）
    server_configs: Sequence[MCPServerConfig] = (
        await db.execute(
            select(MCPServerConfig).where(MCPServerConfig.user_id == user.id)
        )
    ).scalars().all()
    tools: list[BaseTool] = await registry.get_tools(
        user_id=user.id, server_configs=server_configs
    )

    # 2. 建 agent（llm 来自用户配置，没配就直接报错——不降级到默认 key）
    if user.llm_config is None:
        raise ValueError("user hadn't set LLM config")
    llm: BaseChatModel = build_chat_model(user.llm_config)
    agent: CompiledStateGraph = await build_agent(llm=llm, tools=tools)

    # 3. 拼历史 + 本轮输入
    history: list[BaseMessage] = await load_messages(db, session_id)
    inputs: list[BaseMessage] = history + [HumanMessage(message)]

    # updates 模式给「节点跑完的完整消息」，用来落库；
    # messages 模式给「token 增量」，用来推前端。
    new_messages: list[BaseMessage] = []

    try:
        # 4. 双通道流式：同时订阅 messages（token）和 updates（完整消息）
        async for mode, chunk in agent.astream(
            input=inputs, stream_mode=["messages", "updates"]
        ):
            if mode == "messages":
                msg_chunk, metadata = chunk
                # 只转发 agent 节点的、非空的 AIMessage 文本增量
                if (
                    isinstance(msg_chunk, AIMessage)
                    and metadata.get("langgraph_node") == "agent"
                    and getattr(msg_chunk, "content", "")
                ):
                    yield _sse_token(json.dumps(msg_chunk.content))

            elif mode == "updates":
                if not isinstance(chunk, dict):
                    continue
                node, payload = next(iter(chunk.items()))
                for msg in payload.get("messages", []):
                    new_messages.append(msg)                 # 攒着，最后落库
                    if node == "agent" and isinstance(msg, AIMessage):
                        for tc in msg.tool_calls or []:
                            yield _sse_tool_call(tc)          # 工具调用轨迹
                    elif node == "tools" and isinstance(msg, ToolMessage):
                        yield _sse_tool_result(msg)           # 工具返回结果

        yield _sse_done()
    finally:
        # 5. 无论成功/异常，都把本轮（user 输入 + 新产生的消息）落库
        await save_messages(db, session_id, [HumanMessage(message), *new_messages])
```

**逐段设计理由**：

1. **tools 来自 registry、不来自全局常量**：这是 MCP 化的落点——每个 user 的 tool 集不同，由他连接的 MCP server 决定。registry 内部做 per-user TTL 缓存（第 9 章），`get_tools` 是唯一入口。
2. **`user.llm_config is None` 直接报错**：没配 LLM 不降级、不猜默认，显式失败让用户知道要先去配置（对应 12.9 的 `/account`）。
3. **双 `stream_mode`**：`astream(stream_mode=["messages", "updates"])` 让每个 chunk 变成 `(mode, data)` 元组，runner 按 `mode` 分流——这是整个 runner 的设计核心。
4. **`new_messages` 只从 `updates` 收集**：`messages` 模式是碎片（token 级），不能用来落库；`updates` 模式给的是节点跑完的完整消息，才是落库的正确来源。
5. **`try/finally` 保证落库**：哪怕 astream 中途抛异常（LLM 超时、tool 报错），`finally` 也会把已产生的消息存下来，对话历史不丢。

### 11.2 SSE 事件格式：四种事件

前端需要区分四类事件，runner 用四个格式化函数把 langgraph 的数据翻译成 SSE 文本帧：

```python
def _sse_token(text: str) -> str:
    return f"event: token\ndata: {json.dumps({'content': text}, ensure_ascii=False)}\n\n"


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
```

| 事件 | 来源 | 前端渲染 |
|---|---|---|
| `token` | `messages` 模式的 AIMessage 增量 | 追加到当前 assistant 气泡（打字机效果） |
| `tool_call` | `updates` 模式 agent 节点的 `tool_calls` | 渲染一条「调用 X 工具」轨迹 |
| `tool_result` | `updates` 模式 tools 节点的 ToolMessage | 渲染工具返回（可折叠） |
| `done` | astream 正常结束 | 收尾，解除输入框 loading |

**SSE 帧格式约定**：每帧是 `event: <类型>\ndata: <JSON>\n\n`——两个换行是 SSE 协议的帧分隔符，少一个前端 `EventSource` 就粘包。`ensure_ascii=False` 让中文直接走 UTF-8，不转义成 `\uXXXX`，省带宽也便于调试。

> **为什么用 SSE 不用 WebSocket**：对话是「客户端发一次、服务端流式回一串」的单向推送，SSE 正好匹配且比 WebSocket 简单（纯 HTTP、自动重连、无需握手协议）。需要双向实时（如协作编辑）才上 WebSocket。v1 用 SSE（[1.3 非目标](#ch-1)）。

### 11.3 memory：历史消息的存与取

runner 第 3 步 `load_messages` 和第 5 步 `save_messages` 都在 [agents/memory.py](../src/tokendanceai/agents/memory.py)。它俩是一对**对称**的编解码器：把 langgraph 消息列表 ↔ `Message` 表行互转。还原必须完整——尤其 tool 调用链，缺了 agent 续聊时会以为工具没跑过、重复调用。

```python
# agents/memory.py（节选，完整见源码）
import json
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tokendanceai.models.model import Message


async def load_messages(db: AsyncSession, session_id: int) -> list[BaseMessage]:
    """读历史 Message 行，还原成 langgraph 消息列表。"""
    rows = list(
        (await db.execute(select(Message).where(Message.session_id == session_id)))
        .scalars().all()
    )
    messages: list[BaseMessage] = []
    for row in rows:
        if row.role == "user":
            messages.append(HumanMessage(content=row.content or ""))
        elif row.role == "assistant":
            tool_calls = json.loads(row.tool_calls_json) if row.tool_calls_json else []
            messages.append(AIMessage(content=row.content or "", tool_calls=tool_calls))
        elif row.role == "tool":
            payload = json.loads(row.tool_results_json) if row.tool_results_json else {}
            messages.append(ToolMessage(
                content=row.content,
                tool_call_id=payload.get("tool_call_id"),
                name=payload.get("name"),
            ))
    return messages


async def save_messages(db: AsyncSession, session_id: int, messages: list[BaseMessage]) -> None:
    """把本轮新产生的消息逐条写入 messages 表，与 load 逻辑对称。"""
    for msg in messages:
        if isinstance(msg, HumanMessage):
            db.add(Message(session_id=session_id, role="user", content=str(msg.content)))
        elif isinstance(msg, AIMessage):
            tool_calls = getattr(msg, "tool_calls", None) or []
            tool_calls_json = json.dumps(
                [{"name": tc["name"], "args": tc.get("args", {}),
                  "id": tc.get("id"), "type": "tool_call"} for tc in tool_calls],
                ensure_ascii=False,
            ) if tool_calls else None
            db.add(Message(session_id=session_id, role="assistant",
                           content=str(msg.content or ""), tool_calls_json=tool_calls_json))
        elif isinstance(msg, ToolMessage):
            tool_results_json = json.dumps(
                {"tool_call_id": msg.tool_call_id, "name": msg.name}, ensure_ascii=False)
            db.add(Message(session_id=session_id, role="tool",
                           content=str(msg.content), tool_results_json=tool_results_json))
        # 其它类型（SystemMessage 等）不入库
    await db.commit()
```

**JSON 形状约定**（load 读、save 写，两边必须对齐）：

- `tool_calls_json`（写在 `role="assistant"` 行）：`[{"name": ..., "args": {...}, "id": "call_abc", "type": "tool_call"}]`
- `tool_results_json`（写在 `role="tool"` 行）：`{"tool_call_id": "call_abc", "name": ...}`，结果正文放 `content`。

> **硬约束**：`tool_calls` 里的 `id` 和 `ToolMessage.tool_call_id` 必须一一对应——这是 langgraph tool 循环的要求，对不上会报 "Tool call id ... not found in the model's tool calls"。所以 save 时绝不能丢 `tool_call_id`。

### 11.4 与真实实现的两处笔误（待修复）

对照真实 [memory.py](../src/tokendanceai/agents/memory.py)，有两处拼写笔误需要留意：

| # | 位置 | 问题 | 影响 |
|---|---|---|---|
| 1 | `_ROLE_MAP = {..., "assitant": AIMessage, ...}`（第 10 行） | `"assitant"` 拼错（应 `"assistant"`） | **当前无害**：`load_messages` 用的是显式 if/elif（拼写正确），`_ROLE_MAP` 这个字典根本没被引用。建议直接删掉这个未使用的字典，免得日后有人误用 |
| 2 | 局部变量 `messgaes`（load_messages 内） | 拼错 | 纯命名问题，不影响功能；顺手改掉 |

**架构提醒**：`load/save` 是一对编解码器，任何一边改了 JSON 形状，另一边必须同步改。建议把这对约定写成一个小 schema 或常量，避免两处漂移——这正是「契约集中」原则（和第 7 章桥的 schema 契约同理）。

### 11.5 session 生命周期陷阱（影响接口层）

`run_agent` 是 async generator，它在 `finally` 里还要用 `db` 落库。这意味着：**`db` session 的生命周期必须覆盖整个 generator 的消费过程**。

- ✅ 正确：router 用 `Depends(get_db)` 拿 session，把它传进 `run_agent`，`StreamingResponse` 消费 generator 期间 session 一直活着。
- ❌ 错误：在 runner 内部 `async with get_db() as db:` 自己开 session——SSE 还在往外推时，如果用错 scope，session 可能已经 close，`finally` 落库直接炸。

这条约束直接决定了 12.6 的 `/agent/run` 端点怎么写（`db` 在路由层注入，不在 runner 内部开）。把它记在这里，第 12 章会呼应。

---

<a id="part-5"></a>
# 第 5 部分 接口与交互

<a id="ch-12"></a>
## 第 12 章 API 表面（接口层设计）

前面几章把「能力」做出来了：MCP 桥（第 7 章）、三种 transport（第 8 章）、per-user registry（第 9 章）、langgraph 编排（第 10 章）、runner 与 SSE 事件流（第 11 章）。本章把这些能力接到 HTTP 上——把 `router/` 下那几个 `pass` 占位的路由，补成一套可以直接对外服务的接口。

本章的定位是**接口层设计说明书**：每个 endpoint 给出请求/响应契约、鉴权要求、完整实现代码、以及「为什么这么写」的架构理由。读完应该能照着把 `router/` 全部落地，并且知道每个设计决策挡掉了哪个线上事故。

### 12.0 接口层的六条设计准则

1. **路由薄、编排厚**。router 只做三件事：解析+校验入参（Pydantic schema）、装配依赖（当前用户 / db / registry）、把结果包装成响应。真正的业务编排沉到 `agents/runner.py`、`services/`、`mcp/`，路由里不写 ORM 之外的逻辑。
2. **鉴权是横切依赖，不是每个函数各写一遍**。统一用 `Depends(get_current_user)` 注入当前用户，任何拿不到合法身份的请求在进函数体之前就被挡掉。
3. **per-user 隔离贯穿每一个 endpoint**。凡是带资源 id 的接口（session / server / message），都必须校验「这条资源属于当前用户」，否则就是水平越权。本章统一用 `await db.get(Model, id)` + `resource.user_id != current_user.id` 回 404 的模式处理；超过两处复用时抽成 `_require_owned(db, Model, id, user)` helper。
4. **出参永不泄露密文与明文凭据**。`encrypted_api_token` / `encrypted_api_key` 既不原样返回、也不解密返回；response schema 里根本没有这些字段，只返回「是否已配置」这类布尔量。
5. **写操作必须让缓存失效**。MCP server 配置是 registry 的数据源，任何增删改启停都要调 `registry.invalidate(user_id)`，否则 agent 还在用旧连接（见第 9 章 9.3）。
6. **统一错误模型**。所有异常收敛成 `HTTPException`，响应体固定 `{"detail": ...}`；越权一律回 404（不回 403，避免泄露资源是否存在，见 12.10）。

### 12.1 接口总览

按领域分五组（auth / agent / sessions / servers / account）。`Auth` 列标注是否需要登录态。

| 方法 | 路径 | Auth | 请求体 / 参数 | 响应 |
|---|---|:--:|---|---|
| POST | `/auth/register` | 否 | `UserCreate` | `201` `UserPublic` |
| POST | `/auth/login` | 否 | OAuth2 表单 `username/password` | `TokenResponse` |
| POST | `/agent/run` | 是 | `AgentRunRequest` | `200` `text/event-stream`（SSE） |
| GET | `/sessions` | 是 | `limit` / `offset` | `list[SessionPublic]` |
| GET | `/sessions/{id}/messages` | 是 | `limit` / `offset` | `list[MessagePublic]` |
| DELETE | `/sessions/{id}` | 是 | — | `204` |
| GET | `/servers` | 是 | — | `list[ServerPublic]` |
| POST | `/servers` | 是 | `ServerCreate` | `201` `ServerPublic` |
| PATCH | `/servers/{id}` | 是 | `ServerUpdate` | `ServerPublic` |
| DELETE | `/servers/{id}` | 是 | — | `204` |
| POST | `/servers/{id}/test` | 是 | — | `ServerTestResult` |
| GET | `/account` | 是 | — | `AccountPublic` |
| PUT | `/account` | 是 | `AccountUpsert` | `AccountPublic` |

> 认证端点（`/auth/*`）在原设计里没列，但它是其余所有接口的前置——没有登录态就没有 `get_current_user`。本章把它补齐，放在 12.5。

### 12.2 分层与职责边界

```
HTTP 请求
  │
  ▼
router/*.py        ← 参数校验(schema)、依赖装配、响应包装；不写业务
  │  Depends(get_current_user) / Depends(get_db) / Depends(get_registry)
  ▼
agents/runner.py   ← 对话编排（load/save history、astream、SSE 事件翻译）
services/*.py      ← build_chat_model 等无状态服务
mcp/registry.py    ← 有状态：per-user 活连接 + TTL 缓存
  │
  ▼
models/ + db/      ← ORM 与会话工厂
```

关键边界：**router 不直接返回 ORM 对象**，一律经过 `schemas/` 里的 `*Public` 模型（`model_config = ConfigDict(from_attributes=True)`）序列化。这样「数据库里存什么」和「接口暴露什么」解耦，密文字段天然被挡在外面。

### 12.3 前置条件（启动前必须完成的清单）

本章给出的代码假定以下条件已就位。启动 FastAPI app 之前，按清单逐项检查。

**(1) 数据库迁移**

`models/model.py` 里五张表（User / UserLLMConfig / MCPServerConfig / Session / Message）都必须建表完成。运行：

```bash
python -c "from tokendanceai.db.base import Base; from tokendanceai.db.db_session import async_engine; import asyncio; asyncio.run(async_engine.run_sync(Base.metadata.create_all))"
```

或者写一个 `db/init_db.py` 统一管理迁移。表结构对不上会导致 ORM 查询炸裂。

**(2) 模型字段命名错误修复**

`models/model.py` 里存在两处字段命名错误，会导致序列化失败：

- `UserLLMConfig.Update_at` → 应为 `updated_at`（Python 命名规范）
- `Session.Update_at` → 应为 `updated_at`

修复后重新建表或 ALTER TABLE。所有 `*Public` schema 用 `updated_at` 读这个字段，大小写不匹配会报 `AttributeError`。

**(3) token 解密实现**

`mcp/registry.py:_decrypt_token` 目前还抛 `NotImplementedError`。补齐逻辑：

```python
# mcp/config.py（新建文件）
from tokendanceai.utils.crypto import decrypt

def decrypt_mcp_token(encrypted: str | None) -> str | None:
    """解密 MCPServerConfig.encrypted_api_token → 明文 Bearer token。"""
    if not encrypted:
        return None
    return decrypt(encrypted)  # 复用 utils/crypto.py 里的 Fernet 解密
```

然后把 `registry.py` 的 `_decrypt_token` 改成调这个。`encrypted_api_token` 和 `encrypted_api_key` 用同一套 Fernet 密钥（`settings.ai_encryption_key`），所以解密函数可以共享。

**(4) MCPRegistry 全局单例**

`MCPRegistry` 是有状态缓存，每个 user 一个活连接。router 层需要一个全局实例，通过 FastAPI 的 `Depends()` 注入，而不是每次请求都 `MCPRegistry()`（那样缓存全失效）。

在 `main.py` 加：

```python
from contextlib import asynccontextmanager
from tokendanceai.mcp.registry import MCPRegistry

_registry: MCPRegistry | None = None

@asynccontextmanager
async def lifespan(app: FastAPI):
    global _registry
    _registry = MCPRegistry(cache_ttl=300)  # 5 分钟 TTL
    yield
    # shutdown: close all clients
    for live in _registry._clients.values():
        await live.close()
    _registry._clients.clear()

app = FastAPI(title="TokenDance AI", lifespan=lifespan)

def get_registry() -> MCPRegistry:
    if _registry is None:
        raise RuntimeError("MCPRegistry 未初始化，lifespan 启动失败")
    return _registry
```

router 里 `registry: MCPRegistry = Depends(get_registry)` 拿到同一个实例。

**(5) 鉴权依赖 `get_current_user`**

所有 Auth=是 的接口都依赖一个 `Depends(get_current_user)` 注入当前用户。假定你已经有 JWT 鉴权，照搬即可：

```python
# services/auth.py（参考结构）
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
from sqlalchemy.ext.asyncio import AsyncSession

from tokendanceai.config import settings
from tokendanceai.db.db_session import get_db
from tokendanceai.models.model import User

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: AsyncSession = Depends(get_db),
) -> User:
    cred_exc = HTTPException(status_code=status.HTTP_401_UNAUTHORIZED,
                            detail="Could not validate credentials")
    try:
        payload = jwt.decode(token, settings.secret_key, algorithms=[settings.algorithm])
        user_id = payload.get("sub")
        if user_id is None:
            raise cred_exc
    except JWTError:
        raise cred_exc
    user = await db.get(User, int(user_id))
    if user is None:
        raise cred_exc
    return user
```

token 解析失败 / user 不存在都回 401。所有需要登录的接口签名里加 `current_user: User = Depends(get_current_user)`，FastAPI 自动拦截。

**(6) schemas 完整定义**

本章用到的所有 `*Public` / `*Create` / `*Update` schema 必须定义在 `schemas/` 里。`model_config = ConfigDict(from_attributes=True)` 让 Pydantic 能从 ORM 对象转换。关键 schema 见 12.4。

### 12.4 schemas 定义（请求与响应契约）

路由层不直接返回 ORM 对象——任何 `return user` 都先转成 `UserPublic.model_validate(user)`。这是密文字段不泄露的第一道防线。

#### (1) 认证相关

```python
# schemas/user.py（现有 + 补充）
from pydantic import BaseModel, ConfigDict, EmailStr, Field

class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    email: EmailStr
    password: str = Field(..., min_length=8)

class UserPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    username: str
    email: EmailStr
    image_file: str | None = None

class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
```

#### (2) Agent 相关

```python
# schemas/agent.py（新建）
from pydantic import BaseModel, Field

class AgentRunRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    session_id: int | None = None  # 不传则新建 session
    autopilot: bool = False  # 见第 13 章

# SSE 事件无需 schema（纯文本流），事件格式约定见 12.6
```

#### (3) Session / Message 相关

```python
# schemas/session.py（新建）
from datetime import datetime
from pydantic import BaseModel, ConfigDict

class SessionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    title: str
    created_at: datetime
    updated_at: datetime  # ⚠️ ORM 里拼错成 Update_at，先修表

class MessagePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    session_id: int
    role: str  # user / assistant / tool
    content: str
    # tool_calls_json / tool_results_json 不暴露——前端不需要原始 JSON，
    # 想要结构化数据的话另开一个 MessageDetail，按需解析
    created_at: datetime
```

#### (4) MCP Server 相关

```python
# schemas/server.py（新建）
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

class ServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    url: str = Field(..., max_length=255)
    transport: str = Field(..., pattern="^(http|sse|stdio)$")
    api_token: str = Field(..., min_length=1)  # 明文传入，落库前加密

class ServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    url: str | None = Field(default=None, max_length=255)
    transport: str | None = Field(default=None, pattern="^(http|sse|stdio)$")
    api_token: str | None = Field(default=None)  # 传了就重新加密
    enabled: bool | None = None

class ServerPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    name: str
    url: str
    transport: str
    enabled: bool
    created_at: datetime
    # encrypted_api_token 不出现——敏感

class ToolSummary(BaseModel):
    name: str
    description: str

class ServerTestResult(BaseModel):
    ok: bool
    tools: list[ToolSummary] = []
    error: str | None = None
```

#### (5) Account（LLM config）相关

```python
# schemas/account.py（新建）
from datetime import datetime
from pydantic import BaseModel, ConfigDict, Field

class AccountUpsert(BaseModel):
    provider: str = Field(..., pattern="^(openai|anthropic)$")
    base_url: str
    model: str
    api_key: str  # 明文传入，落库前加密

class AccountPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    provider: str
    base_url: str
    model: str
    # encrypted_api_key 不出现
    created_at: datetime
    updated_at: datetime
```

### 12.5 认证路由（`router/auth.py`）

#### (1) 注册 `POST /auth/register`

```python
# router/auth.py
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from passlib.context import CryptContext
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokendanceai.db.db_session import get_db
from tokendanceai.models.model import User
from tokendanceai.schemas.user import TokenResponse, UserCreate, UserPublic
from tokendanceai.services.auth import create_access_token  # JWT 签发

router = APIRouter(prefix="/auth", tags=["auth"])
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


@router.post("/register", status_code=status.HTTP_201_CREATED, response_model=UserPublic)
async def register(data: UserCreate, db: AsyncSession = Depends(get_db)):
    # 校验 username/email 唯一
    existing = (
        await db.execute(
            select(User).where((User.username == data.username) | (User.email == data.email))
        )
    ).scalar_one_or_none()
    if existing:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username or email already registered",
        )
    # 密码 hash
    hashed = pwd_context.hash(data.password)
    user = User(username=data.username, email=data.email, password_hash=hashed)
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return UserPublic.model_validate(user)
```

**设计点**：用 `|` 一次查同时检查 username 和 email，避免两次 SELECT。密码用 bcrypt hash，明文不落库。`status_code=201` 语义化资源创建。返回 `UserPublic` 不泄露 `password_hash`。

#### (2) 登录 `POST /auth/login`

```python
@router.post("/login", response_model=TokenResponse)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db),
):
    user = (
        await db.execute(select(User).where(User.username == form_data.username))
    ).scalar_one_or_none()
    if not user or not pwd_context.verify(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
        )
    token = create_access_token(data={"sub": user.id})
    return TokenResponse(access_token=token, token_type="bearer")
```

**设计点**：`OAuth2PasswordRequestForm` 符合 OAuth2 规范（`username` / `password` 表单字段）。密码校验失败也回 401，不区分「用户不存在」和「密码错误」——避免用户名枚举。`create_access_token` 是你自己的 JWT 签发函数，`sub` claim 存 `user.id`。

### 12.6 Agent 对话路由（`router/agent.py`）

#### `POST /agent/run` —— SSE 流式对话

这是整个 agent 应用的核心入口。复用 `agents/runner.py:run_agent`，把 SSE 流转发给客户端。

```python
# router/agent.py
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.responses import StreamingResponse
from sqlalchemy.ext.asyncio import AsyncSession

from tokendanceai.agents.runner import run_agent
from tokendanceai.db.db_session import get_db
from tokendanceai.mcp.registry import MCPRegistry
from tokendanceai.models.model import Session, User
from tokendanceai.schemas.agent import AgentRunRequest
from tokendanceai.services.auth import get_current_user
from tokendanceai.main import get_registry

router = APIRouter(prefix="/agent", tags=["agent"])


@router.post("/run")
async def agent_run(
    req: AgentRunRequest,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    registry: MCPRegistry = Depends(get_registry),
):
    # 1. session_id 校验与创建
    if req.session_id is None:
        # 新建 session
        session = Session(user_id=current_user.id, title=req.message[:50])  # 标题截取前 50 字
        db.add(session)
        await db.commit()
        await db.refresh(session)
        session_id = session.id
    else:
        # 校验 session 属于当前用户（防水平越权）
        session = await db.get(Session, req.session_id)
        if session is None or session.user_id != current_user.id:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
        session_id = session.id

    # 2. 调 runner 拿 SSE generator
    return StreamingResponse(
        run_agent(
            user=current_user,
            message=req.message,
            session_id=session_id,
            db=db,
            registry=registry,
        ),
        media_type="text/event-stream",
    )
```

**设计点与架构理由**：

1. **session 创建时机**：`session_id=None` 时在路由层创建 session，而不是 runner 里创建——session 的 id 必须先落库才能写 message。runner 的职责是「编排对话」，不是「管 session 生命周期」。
2. **防水平越权**：`session.user_id != current_user.id` 时回 404 而不是 403（见 12.10）。
3. **SSE 流的陷阱**：`StreamingResponse` 吃一个 async generator，FastAPI 会逐块推给客户端。但 **db session 的生命周期不能被 generator 绑架**——`get_db` 的 `yield session` 在路由函数 return 时就退出了，`async with AsyncSessionLocal()` 已经 close，之后 runner 里再 `await db.execute()` 会报 `Session is closed`。

   **解法（runner 当前的实现已经对了）**：`run_agent` 的 `db: AsyncSession` 参数是路由层传进来的同一个 session 对象。FastAPI 的 `Depends(get_db)` 保证「在 generator 没耗尽之前，依赖的 db session 不会被回收」——底层是 `contextlib.AsyncExitStack` 把依赖的生命周期延长到 response 发完。所以当前代码能跑；但如果把 `db = Depends(get_db)` 挪到 runner 里去，就炸了。

4. **autopilot 参数（预留）**：`req.autopilot` 传给 runner，runner 根据它选不同 system prompt（见第 13 章）。v1 先不做，runner 签名里加参数即可。

#### SSE 事件格式约定（供前端消费）

`run_agent` yield 的四种事件（见 `agents/runner.py:104-124`）：

```
event: token
data: {"content": "..."}

event: tool_call
data: {"name": "create_post_draft", "args": {...}, "id": "call_abc"}

event: tool_result
data: {"tool_call_id": "call_abc", "name": "create_post_draft", "content": "..."}

event: done
data: {}
```

前端监听 `EventSource`，按 `event` 字段分发：

- `token` → 追加到当前 assistant message
- `tool_call` / `tool_result` → 渲染成 `<div class="tool-trace">` 展开/收起行
- `done` → 关闭 spinner、允许下一条输入

### 12.7 Session 管理路由（`router/sessions.py`）

#### (1) `GET /sessions` —— 列出当前用户的会话

```python
# router/sessions.py
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokendanceai.db.db_session import get_db
from tokendanceai.models.model import Message, Session, User
from tokendanceai.schemas.session import MessagePublic, SessionPublic
from tokendanceai.services.auth import get_current_user

router = APIRouter(prefix="/sessions", tags=["sessions"])


@router.get("", response_model=list[SessionPublic])
async def list_sessions(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(20, ge=1, le=100),
    offset: int = Query(0, ge=0),
):
    rows = (
        await db.execute(
            select(Session)
            .where(Session.user_id == current_user.id)
            .order_by(Session.updated_at.desc())  # 最近更新的在前
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return [SessionPublic.model_validate(s) for s in rows]
```

**设计点**：**per-user 隔离**——`WHERE user_id = current_user.id`，不需要手动校验。**分页必须限**——`limit` 上限 100，避免一次查几万行。**排序**——按 `updated_at DESC`，聊天 app 的常规 UX，最近活跃的在顶上。

#### (2) `GET /sessions/{id}/messages` —— 拉会话历史消息

```python
@router.get("/{session_id}/messages", response_model=list[MessagePublic])
async def list_messages(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    limit: int = Query(100, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    # 1. 校验 session 归属
    session = await db.get(Session, session_id)
    if session is None or session.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    # 2. 查该 session 的 messages，按时间正序（最早的在前）
    rows = (
        await db.execute(
            select(Message)
            .where(Message.session_id == session_id)
            .order_by(Message.created_at.asc(), Message.id.asc())  # 时间 + id 双重排序，避免同时刻乱序
            .limit(limit)
            .offset(offset)
        )
    ).scalars().all()
    return [MessagePublic.model_validate(m) for m in rows]
```

**设计点**：**先校验 session 归属，再查 message**——防水平越权的关键。如果直接 `WHERE session_id = X`，攻击者传别人的 session_id 也能拉到消息。**排序**——`created_at ASC`，聊天记录从最早到最新。`id ASC` 做次序键，避免同一毫秒内多条 message 乱序。**limit 上限 500**——单次对话很少超过 500 条消息；超了就做分页加载。

#### (3) `DELETE /sessions/{id}` —— 删除会话

```python
@router.delete("/{session_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_session(
    session_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    session = await db.get(Session, session_id)
    if session is None or session.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")
    await db.delete(session)
    # 关联的 Message 行由外键 ON DELETE CASCADE 自动删（如果建表时设了的话）；
    # 没设的话手动 DELETE FROM messages WHERE session_id = X
    await db.commit()
    return None  # 204 No Content 不返回 body
```

**设计点**：**DELETE 也要校验归属**——别让用户删别人的 session。**CASCADE 删除**——`Message.session_id` 外键上设 `ondelete="CASCADE"`，删 session 时自动删关联 message；没设的话手动删一遍（否则成孤儿行）。**204 No Content**——删除成功不返回实体，HTTP 语义。

### 12.8 MCP Server 配置路由（`router/servers.py`）

这是通用 agent 的命脉——用户在这里配 blog / GitHub / 文件系统等 MCP server，agent 的能力完全来自这些配置（见第 1 章 1.1）。

#### (1) `GET /servers` —— 列出已配的 MCP server

```python
# router/servers.py
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokendanceai.db.db_session import get_db
from tokendanceai.models.model import MCPServerConfig, User
from tokendanceai.schemas.server import ServerCreate, ServerPublic, ServerUpdate, ServerTestResult, ToolSummary
from tokendanceai.services.auth import get_current_user
from tokendanceai.utils.crypto import decrypt, encrypt
from tokendanceai.mcp.registry import MCPRegistry
from tokendanceai.main import get_registry

router = APIRouter(prefix="/servers", tags=["mcp_servers"])


@router.get("", response_model=list[ServerPublic])
async def list_servers(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    rows = (
        await db.execute(
            select(MCPServerConfig)
            .where(MCPServerConfig.user_id == current_user.id)
            .order_by(MCPServerConfig.created_at.desc())
        )
    ).scalars().all()
    return [ServerPublic.model_validate(s) for s in rows]
```

#### (2) `POST /servers` —— 添加一个 MCP server

```python
@router.post("", status_code=status.HTTP_201_CREATED, response_model=ServerPublic)
async def create_server(
    data: ServerCreate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    registry: MCPRegistry = Depends(get_registry),
):
    # 加密 token
    encrypted_token = encrypt(data.api_token)
    server = MCPServerConfig(
        user_id=current_user.id,
        name=data.name,
        url=data.url,
        transport=data.transport,
        encrypted_api_token=encrypted_token,
        enabled=True,
    )
    db.add(server)
    await db.commit()
    await db.refresh(server)
    # ⚠️ 新增后必须让缓存失效，否则 registry 还在用旧的 server 列表
    await registry.invalidate(current_user.id)
    return ServerPublic.model_validate(server)
```

**设计点**：**明文 token 入、密文落库**——`ServerCreate.api_token` 是明文（前端传过来），`encrypt()` 之后存 `encrypted_api_token`。`ServerPublic` 不含 token 字段，密文永不出库。**写操作后 `invalidate`**——新增 server 后，该 user 的 `MCPRegistry` 缓存全部失效（见第 9 章 9.3），下次 `get_tools` 重新加载。如果不调，agent 还在用旧的 tools 列表，新加的 server 不生效。

#### (3) `PATCH /servers/{id}` —— 修改 / 启用禁用

```python
@router.patch("/{server_id}", response_model=ServerPublic)
async def update_server(
    server_id: int,
    data: ServerUpdate,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    registry: MCPRegistry = Depends(get_registry),
):
    server = await db.get(MCPServerConfig, server_id)
    if server is None or server.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    # 部分更新
    if data.name is not None:
        server.name = data.name
    if data.url is not None:
        server.url = data.url
    if data.transport is not None:
        server.transport = data.transport
    if data.api_token is not None:
        server.encrypted_api_token = encrypt(data.api_token)
    if data.enabled is not None:
        server.enabled = data.enabled

    await db.commit()
    await db.refresh(server)
    await registry.invalidate(current_user.id)  # 任何改动都让缓存失效
    return ServerPublic.model_validate(server)
```

**设计点**：**部分更新语义**——`PATCH` 只更新传了的字段，`None` 的不动。这样前端「只改 enabled」时不需要重传 name/url/token。**token 重加密**——`data.api_token` 非空时，用新明文重新 `encrypt()`。旧密文被覆盖，解密后是新 token。**软启停**——`enabled=False` 时 server 配置还在，只是 `MCPRegistry.get_tools` 跳过（见 `registry.py:59`）。删配置用 DELETE，停用用 PATCH `enabled=false`。

#### (4) `DELETE /servers/{id}` —— 删除 MCP server 配置

```python
@router.delete("/{server_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_server(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    registry: MCPRegistry = Depends(get_registry),
):
    server = await db.get(MCPServerConfig, server_id)
    if server is None or server.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")
    await db.delete(server)
    await db.commit()
    await registry.invalidate(current_user.id)
```

#### (5) `POST /servers/{id}/test` —— 连通性测试

这个接口**不走 registry 缓存**——建一个临时 MCP client，调 `list_tools()`，测完立即 close。返回「能不能连上」+ 「发现了哪些 tool」。

```python
from tokendanceai.mcp.client import connect_http, connect_sse


@router.post("/{server_id}/test", response_model=ServerTestResult)
async def test_server(
    server_id: int,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    server = await db.get(MCPServerConfig, server_id)
    if server is None or server.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Server not found")

    token = decrypt(server.encrypted_api_token)
    try:
        if server.transport == "http":
            async with connect_http(server.url, token) as session:
                tools = (await session.list_tools()).tools
        elif server.transport == "sse":
            async with connect_sse(server.url, token) as session:
                tools = (await session.list_tools()).tools
        else:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"Transport {server.transport} not supported for test",
            )
        return ServerTestResult(
            ok=True,
            tools=[ToolSummary(name=t.name, description=t.description or "") for t in tools],
        )
    except Exception as e:
        # ⚠️ 异常消息可能含 token / url 片段，过滤掉敏感部分
        safe_error = str(e).replace(token, "***") if token else str(e)
        return ServerTestResult(ok=False, error=safe_error)
```

**设计点**：**临时连接**——`async with connect_*` 进去、拿 tools、退出就 close，不进 registry。**异常不泄露 token**——`str(e)` 里可能包含 `Authorization: Bearer xxx` 或者 url query，用 `.replace(token, "***")` 脱敏后再返回。更安全的做法是只返回异常类型 + 首行，不返回完整 traceback。**返回 tool 列表**——前端拿到 `tools` 数组，展示「这个 server 暴露了 X 个 tool」，用户能直观判断配置对不对。

### 12.9 Account 路由（`router/account.py`）—— LLM 配置

#### (1) `GET /account` —— 查当前用户的 LLM 配置

```python
# router/account.py
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from tokendanceai.db.db_session import get_db
from tokendanceai.models.model import User, UserLLMConfig
from tokendanceai.schemas.account import AccountPublic, AccountUpsert
from tokendanceai.services.auth import get_current_user
from tokendanceai.utils.crypto import encrypt

router = APIRouter(prefix="/account", tags=["account"])


@router.get("", response_model=AccountPublic | None)
async def get_account(
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    # User.llm_config 是 lazy="joined" 关系，current_user 上已经有了
    if current_user.llm_config is None:
        return None  # 用户还没配 LLM
    return AccountPublic.model_validate(current_user.llm_config)
```

**设计点**：**`lazy="joined"`**——`User` 定义里 `llm_config` 关系设了 `lazy="joined"`（见 `models/model.py:24-28`），查 User 时自动 JOIN 一次拉出 config。`current_user.llm_config` 不需要再 `await db.execute(select(UserLLMConfig)...)`。**返回 `None`**——用户没配过返回 `null`，前端渲染「请先配置 LLM」提示。

#### (2) `PUT /account` —— 新建或更新 LLM 配置

```python
@router.put("", response_model=AccountPublic)
async def upsert_account(
    data: AccountUpsert,
    current_user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    encrypted_key = encrypt(data.api_key)
    config = current_user.llm_config
    if config is None:
        # 新建
        config = UserLLMConfig(
            user_id=current_user.id,
            provider=data.provider,
            base_url=data.base_url,
            model=data.model,
            encrypted_api_key=encrypted_key,
        )
        db.add(config)
    else:
        # 更新
        config.provider = data.provider
        config.base_url = data.base_url
        config.model = data.model
        config.encrypted_api_key = encrypted_key

    await db.commit()
    await db.refresh(config)
    return AccountPublic.model_validate(config)
```

**设计点**：**Upsert 语义**——`PUT` 幂等，不存在就建，存在就全量覆盖。`UserLLMConfig.user_id` 上有 `unique=True`，一个 user 只有一条 config。**明文 key 入、密文落库**——`data.api_key` 明文传入（HTTPS 保护），`encrypt()` 后存 `encrypted_api_key`。`AccountPublic` 不含 key 字段，密文永不出。**无需 `invalidate`**——LLM config 不进 registry 缓存（registry 只缓存 MCP client），改完立即生效——下次 `POST /agent/run` 时 `build_chat_model` 会拿最新 config。

### 12.10 统一错误模型

本章所有接口遵守三条错误约定：

**(1) 越权统一回 404，不回 403**

```python
if resource.user_id != current_user.id:
    raise HTTPException(status_code=404, detail="Resource not found")
```

**理由**：403 Forbidden 语义是「资源存在，但你没权限」——等于告诉攻击者「这个 id 有效」。404 Not Found 语义是「资源不存在」——不透露资源是否真的存在。攻击者拿到 404 无法判断是「id 无效」还是「不属于我」，枚举成本更高。

**(2) 敏感信息不进异常消息**

任何可能泄露 token / key / 内部路径的异常，捕获后脱敏再返回：

```python
except Exception as e:
    safe_msg = str(e).replace(token, "***").replace(settings.secret_key, "***")
    return {"ok": False, "error": safe_msg}
```

**理由**：MCP 连接失败时，底层 httpx / MCP SDK 的异常可能包含完整 URL（含 query 里的 token）、`Authorization` header、甚至 internal hostname。直接 `raise HTTPException(detail=str(e))` 会把这些全部返回给前端，等于把凭据送给攻击者。

**(3) 所有异常收敛成 `HTTPException`**

路由层不抛 `ValueError` / `KeyError` / `AttributeError` 等 Python 原生异常——FastAPI 默认会把它们转成 500 Internal Server Error + traceback（debug 模式下），泄露代码结构。统一包成 `HTTPException`，自己控制 status code 和 detail。

```python
# ❌ 错误
if not server:
    raise ValueError("Server not found")  # 变成 500 + traceback

# ✅ 正确
if not server:
    raise HTTPException(status_code=404, detail="Server not found")
```

### 12.11 完整路由挂载（`main.py` 里的集成）

把上述五个 router 注册到 app：

```python
# main.py（补充）
from tokendanceai.router import account, agent, auth, servers, sessions

app.include_router(auth.router)
app.include_router(agent.router)
app.include_router(sessions.router)
app.include_router(servers.router)
app.include_router(account.router)
```

启动后，完整接口列表在 `http://localhost:8000/docs`（Swagger UI）。

### 12.12 本章检查清单

完成本章后，用以下清单验证接口层是否就绪：

- [ ] 数据库五张表已建表（User / UserLLMConfig / MCPServerConfig / Session / Message）。
- [ ] `models/model.py` 里的 `Update_at` 字段已改成 `updated_at`。
- [ ] `mcp/registry.py:_decrypt_token` 已实现（调 `utils.crypto.decrypt`）。
- [ ] `main.py` 里 `MCPRegistry` 已注册为全局单例（`lifespan` + `get_registry`）。
- [ ] `get_current_user` 依赖已实现（JWT 解析 + 查 User）。
- [ ] `schemas/` 里所有 `*Public` / `*Create` / `*Update` 已定义（见 12.4）。
- [ ] 五个 router（auth / agent / sessions / servers / account）已实现并挂载到 app。
- [ ] `POST /agent/run` 能返回 SSE 流，事件格式符合 12.6 约定。
- [ ] `POST /servers/{id}/test` 能测通一个 MCP server，返回 tools 列表。
- [ ] 所有写操作（POST / PATCH / DELETE `/servers`）都调了 `registry.invalidate(user_id)`。
- [ ] 所有资源访问接口都校验了 `resource.user_id == current_user.id`（防水平越权）。
- [ ] 所有响应 schema 都不含 `encrypted_*` 字段（密文永不出库）。

全部打勾之后，接口层就是生产可用的——能对外服务、有鉴权、密文不泄露、缓存失效机制正确。下一章（第 13 章）讲 autopilot 怎么在 system prompt 层面实现自动发布。

---

<a id="ch-13"></a>
## 第 13 章 自动发布与 Autopilot

本章是一个**架构原则的案例研究**：当需求是「让 agent 自动完成多步任务」（如先起草再发布），正确的做法是把「做什么」和「怎么编排」分到不同的层，而不是把编排逻辑硬编码进代码。

### 13.1 核心原则：编排逻辑属于 prompt，不属于代码

**反模式**——在 runner 或 tool 里写死「先 draft 再 publish」：

```python
# ❌ 不要这样
draft = await call_tool("create_post_draft", {...})
await call_tool("publish_post", {"id": draft["id"]})   # 硬编码的两步流程
```

这样写有三个结构性问题：

1. **僵化**：用户想「只起草、先别发」时，代码分支爆炸。
2. **泄漏业务到基础设施层**：runner 本该是领域无关的对话引擎，这里却塞进了 blog 特有的发布流程。
3. **不可复用**：换个业务（发邮件、建工单）又要改 runner。

**正确做法**——职责分到两层：

| 层 | 职责 | 怎么做 |
|---|---|---|
| **tool 侧（MCP server）** | 提供**原子能力** | 暴露两个独立 tool：`create_post_draft` / `publish_post`，各自只做一件事，`publish_post` 直接落库，不要 `confirm_endpoint` 那套 |
| **agent 侧（system prompt）** | 决定**编排策略** | 用 prompt 告诉模型「除非用户要求先看 draft，否则 draft 后立即 publish」 |

这样 runner、tool 全程不知道「自动发布」这个概念——它只是 LLM 根据 prompt 自主决定的一串 tool 调用。换业务只换 MCP server 和 prompt，核心代码一行不动。这正是 [1.3](#ch-1) 「通用」的含义。

### 13.2 Autopilot：用 prompt 切换自主程度

「自动发布 vs 停下来确认」是一个 per-run 的策略选择，同样落在 prompt 层，不落在代码逻辑层。加一个 `autopilot: bool` 请求参数：

```python
AUTOPILOT_PROMPT = SYSTEM_PROMPT + "\n\nWhen you create something publishable, \
publish it immediately. Do not stop to ask for confirmation."

MANUAL_PROMPT = SYSTEM_PROMPT + "\n\nAfter you draft something, STOP and show it \
to the user. Wait for explicit approval before publishing."

prompt = AUTOPILOT_PROMPT if autopilot else MANUAL_PROMPT
agent = await build_agent(llm, tools, system_prompt=prompt)
```

**设计要点**：

1. **两种模式共用一套代码**：唯一的差异是 system prompt 的尾段，`build_agent` 的签名已经支持 `system_prompt=` 覆盖（见 10.1）。
2. **与安全栅栏的关系**：注意默认 `SYSTEM_PROMPT`（10.1）已经要求「破坏性/外向操作先确认」。`AUTOPILOT_PROMPT` 是显式放宽这条——所以 autopilot 必须由用户主动开启（请求参数默认 `false`），不能是默认行为。这是「危险默认关闭」原则。
3. **autopilot 不等于无限制**：即使 autopilot 开着，删除类操作仍建议保留确认（prompt 里区分「发布/创建」和「删除/覆盖」）。自主程度是光谱，不是开关。

### 13.3 为什么不用 langgraph 的 `interrupt()` 做确认（v1）

langgraph 原生支持 `interrupt()`——在 tool 执行前暂停图、等前端 resume（第 16 章）。它比 prompt 控制更可靠（prompt 可能被模型忽略）。但 v1 选择 prompt 控制，理由是权衡：

| 方案 | 可靠性 | 实现成本 | v1 选择 |
|---|---|---|---|
| prompt 控制 | 中（依赖模型遵守） | 低（改字符串） | ✅ |
| `interrupt()` + resume | 高（代码强制） | 高（要做 checkpoint 持久化 + 前端 resume 协议） | ❌ 推迟到 v2 |

v1 的对话是「一次性 run」，没有 checkpoint 持久化，上 `interrupt()` 要先把 langgraph 的 checkpointer 接上数据库——那是第 16 章的扩展。v1 用 prompt 够用，且符合「编排在 prompt 层」的主线。

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
