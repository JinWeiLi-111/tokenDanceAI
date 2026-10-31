# TokenDanceAI 多 Agent 改造方案

> 版本：v0.2（Claude Code 模式） · 日期：2026-10-30 · 作者：xiongkang

v0.1 走的是"Orchestrator 图节点 + Specialist 子图 + Send 并行 + AgentMemory 共享表"的常规 Supervisor 模式。
v0.2 改为对齐 Claude Code 的实际机制：**编排是主循环的工具调用，不是图节点；委派是一个工具，不是图拓扑；Agent 间靠 brief + tool result 共享，不需要共享记忆表。**

---

## 一、Claude Code 多 Agent 机制提炼

抄之前先讲清楚要抄什么。Claude Code 的多 Agent 有十个可观察的设计点：

1. **编排即主循环**。没有独立的"Orchestrator Agent"节点。主会话自己就是编排者，靠推理决定"自己干"还是"派出去"，然后调用工具委派。多 Agent 行为从工具调用里涌现，不写死在图结构里。
2. **委派是一个工具**（`spawn_agent` / Task），不是 LangGraph 的 `Send` 分发。主 Agent 在 ReAct 循环里像调普通工具一样调它，参数带 `subagent_type` / `description` / `prompt`。拓扑由模型按需决定，而不是预定义。
3. **Agent = 同一个模型 + 受限工具集 + 专用 system prompt**。Agent 之间不是不同模型，而是同一模型配上不同的工具白/黑名单和不同系统提示。`Explore` = 全工具去掉 Edit/Write 的只读搜索 Agent；`Plan` = 只读架构师；`general-purpose` = 全工具。**工具裁剪就是 Agent 的人格。**
4. **Fork vs Fresh**。Fork 继承主会话完整上下文（同模型），Fresh 只带 brief。默认 Fresh——brief 写得好就够，Fork 是特殊场景。
5. **后台执行，只回传摘要**。子 Agent 跑完，主 Agent 只拿到最终报告作为 tool result 字符串；子 Agent 期间的几十次工具调用不进主上下文。这是上下文保持干净的关键。
6. **并行 = 同一轮发多个 tool_call**。主 Agent 一轮 emit 两个 `spawn_agent` 调用，ToolNode 用 `asyncio.gather` 并发跑。不需要 `Send`，不需要预定义并行图。
7. **可续写（SendMessage）**。子 Agent 跑完不销毁，保留状态，主 Agent 可再发消息做多轮委派，不用重新 brief。
8. **Worktree 隔离**。会改文件的子 Agent 跑在独立 git worktree 里，两个并行 code Agent 不会互相覆盖。
9. **默认自己做，委派是刻意升级**。最重要的行为约束：一次委派 = 一次完整子上下文成本，几个工具调用能搞定的别委派。"何时多 Agent"是 prompting 约束，不是架构开关。
10. **Agent 定义声明式**。像 `.claude/agents/*.md` 那样，每个定义是个文件：name / description / tools allow-deny / system prompt / model override。用户可自行扩展。

---

## 二、与 v0.1 的关键差异

| 维度 | v0.1（Supervisor 模式） | v0.2（Claude Code 模式） |
| --- | --- | --- |
| 编排 | 独立 Orchestrator 图节点（plan→route→merge→synthesize） | 主 ReAgent 循环，编排从工具调用涌现 |
| 委派 | `Send` API 预定义并行分发 | `spawn_agent` 工具，模型按需调用 |
| Agent 差异 | 不同 role（orchestrator/specialist/critic）+ 不同模型 | 同模型 + 工具白/黑名单 + 专用 system prompt |
| Agent 间共享 | `AgentMemory` 工作记忆表 | brief（spawn 的 prompt 参数）+ tool result（回传摘要） |
| 并行 | Send 分发到固定子图 | 一轮多个 spawn tool_call，ToolNode 并发 |
| 续写 | 无 | `send_to_agent` 工具，保留子 Agent 状态 |
| 隔离 | 无 | 可选 git worktree |
| 默认行为 | 总是走编排图 | 默认自己做，委派是刻意升级 |

v0.1 的 `AgentMemory` 表删掉——共享已经由"spawn 时传 brief + 回传 tool result"覆盖，`Message` 表记录 spawn 的 tool_call（即 brief）和 tool_result（即摘要）就够还原协作链路。

---

## 三、现状问题

当前 `build_agent` 直接调 `create_agent`，产出的 LangGraph 图只有 `agent` + `tools` 两个节点循环。问题不在图简单，而在**委派不是一等公民**：

| # | 问题 | v0.2 对策 |
| --- | --- | --- |
| 1 | 工具无分组，用户所有 MCP server 工具平铺进一次调用 | 子 Agent 按定义裁剪工具；主 Agent 也可只暴露子集 |
| 2 | 复杂任务只能单 Agent 一条链走到底，出错无法局部重试 | 委派成独立子上下文，失败可单独续写/重试 |
| 3 | 无并行，独立子任务串行 | 一轮多个 spawn tool_call，ToolNode 原生并发 |
| 4 | 单一 LLM 配置绑死 User | `AgentLLMConfig` 支持按 Agent 覆盖模型 |
| 5 | 无 Agent 间通信 | brief + tool result + `send_to_agent` 续写 |
| 6 | 记忆是平铺消息流 | `Message` 加 `agent_name` + `parent_tool_call_id` 还原委派树，无需新表 |
| 7 | `create_agent` 黑盒，无法插自定义节点 | 主图仍用 `create_agent`，多 Agent 行为靠工具注入而非改图 |

第 7 点是 v0.2 的核心取舍：**不重写图，而是给主 Agent 加两个工具**。图的复杂度不增长，多 Agent 能力从工具里来。

---

## 四、目标架构

```text
用户消息
  │
  ▼
┌──────────────────────────────────────────────┐
│  Main Agent (ReAct 循环)                      │
│  - 用户全部 MCP 工具                           │
│  - + spawn_agent 工具  ← 委派原语              │
│  - + send_to_agent 工具 ← 续写原语             │
│  - system prompt 内置"默认自己做"约束          │
└──────┬───────────────────────────┬────────────┘
       │ 自己直接调 MCP 工具        │ 调 spawn_agent
       │ (简单任务, 默认路径)        │ (独立并行 / 大范围搜索)
       ▼                           ▼
   MCP Tools               ┌──────────────────────┐
                           │ spawn_agent 工具执行  │
                           │  1. 查 SubagentDef   │
                           │  2. 裁剪工具集       │
                           │  3. Fork/Fresh 装消息 │
                           │  4. build_agent       │
                           │  5. ainvoke 跑完     │
                           │  6. 回传 final msg    │
                           └──────┬───────────────┘
                                  │ 并发: 同轮多个
                                  ▼
                     ┌──────────┬──────────┐
                     ▼          ▼          ▼
                Subagent A  Subagent B  Subagent C
                (各自受限工具+专用prompt)
                     │          │          │
                     └──────────┴──────────┘
                                  │
                                  ▼  只回传最终报告(摘要)
                           作为 ToolMessage 回到主 Agent
```

主图保持 `create_agent` 的 `agent ↔ tools` 双节点循环。`spawn_agent` 就是一个挂在 tools 里的普通工具，它内部跑一个**嵌套的** `create_agent` 实例。

---

## 五、核心组件

### 5.1 SubagentDefinition（声明式定义）

替换 v0.1 的 `AgentProfile`。每个定义描述一个可委派的角色，用户可 CRUD：

```python
class SubagentDefinition(Base):
    """一个可委派的子 Agent 定义。声明式，类似 .claude/agents/*.md。"""
    __tablename__ = "subagent_definitions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id), nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 给主 Agent 看的简短说明，帮它决定何时委派给这个 Agent
    description: Mapped[str] = mapped_column(String(255), nullable=False)
    system_prompt: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 工具裁剪：MCP server name 的 allow/deny 列表(JSON)
    # allow 为空 = 继承用户全部；deny 移除指定 server
    mcp_allow_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    mcp_deny_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 模型覆盖；None = 继承主 Agent(即 user.llm_config)
    llm_config_id: Mapped[int | None] = mapped_column(
        ForeignKey("agent_llm_configs.id"), nullable=True
    )
    # 是否允许 fork(继承主会话上下文)
    fork_allowed: Mapped[bool] = mapped_column(default=False, nullable=False)
    # 是否需要 worktree 隔离(会改文件时开)
    needs_worktree: Mapped[bool] = mapped_column(default=False, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
```

**种子定义**（用户首次启用时预置，对应 Claude Code 的内置 Agent 类型）：

| name | 工具 | system prompt 要点 | fork | worktree |
| --- | --- | --- | --- | --- |
| `explore` | 全工具去 mutating（read-only） | 大范围 fan-out 搜索，只回结论不回文件堆 | ✗ | ✗ |
| `code` | 全工具 | 实现指定范围改动，跑构建验证 | ✗ | ✓ |
| `plan` | read-only | 出分步实现计划，列关键文件与权衡 | ✗ | ✗ |
| `review` | read-only | 对给定 diff/目标找 bug，按严重度排 | ✗ | ✗ |

### 5.2 AgentLLMConfig（按 Agent 覆盖模型）

沿用 v0.1 设计，主 Agent 默认用 `user.llm_config`，子 Agent 可按定义覆盖（例如 explore 用便宜模型、code 用强模型）。

```python
class AgentLLMConfig(Base):
    __tablename__ = "agent_llm_configs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey(User.id), nullable=False, index=True)
    label: Mapped[str] = mapped_column(String(100), nullable=False)
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
    model: Mapped[str] = mapped_column(String(100), nullable=False)
    encrypted_api_key: Mapped[str] = mapped_column(Text, nullable=False)
```

### 5.3 spawn_agent 工具（委派原语）

这是整个改造的核心。它是一个普通的 `@tool`，挂在主 Agent 的工具列表里：

```python
class SpawnAgentArgs(BaseModel):
    subagent_type: str        # SubagentDefinition.name, 如 "explore"
    description: str           # 3-5 词标签, 给前端展示
    prompt: str                # brief: 目标 + 已排除的方向 + 文件指引 + 范围
    fork: bool = False         # 是否继承主会话上下文
    isolation: Literal["none", "worktree"] = "none"


@tool
async def spawn_agent(args: SpawnAgentArgs) -> str:
    """委派一个子任务给指定类型的子 Agent。只在独立并行工作或大范围搜索时使用；
    几个工具调用能搞定的不要委派——每次委派都是一次完整的子上下文成本。"""
    definition = await registry.get_definition(user_id, args.subagent_type)
    # 1. 裁剪工具: 从用户全部 MCP 工具里按 allow/deny 过滤
    tools = await mcp_registry.get_tools_filtered(user_id, definition)
    # 2. 解析模型: definition.llm_config_id 或继承主 Agent
    llm = resolve_llm(definition, user)
    # 3. 装初始消息
    initial = build_initial_messages(args, definition, parent_history if args.fork else None)
    # 4. 构建并运行嵌套 Agent
    sub_agent = await build_agent(llm=llm, tools=tools, system_prompt=definition.system_prompt)
    # 5. worktree 隔离(可选)
    async with maybe_worktree(args.isolation, definition) as cwd:
        sse.emit("subagent_start", {"agent": args.subagent_type, "desc": args.description})
        result = await sub_agent.ainvoke({"messages": initial}, config={"cwd": cwd})
        sse.emit("subagent_done", {"agent": args.subagent_type})
    # 6. 只回传最终 AIMessage.content
    final: AIMessage = result["messages"][-1]
    # 7. 保留子 Agent 状态供续写
    agent_id = live_agents.register(sub_agent, initial, definition)
    return f"[subagent:{agent_id}] {final.content}"
```

**关键点**：

- 子 Agent 用 `ainvoke` 跑完（非流式），内部几十次工具调用不进主上下文，只有最后一句回到主 Agent。
- 子 Agent 复用同一个 `MCPRegistry`（per-user 连接池），不重复建连。
- `sse.emit` 通过 runner.py 注入的异步队列把生命周期事件推给前端（见 §7）。
- 返回值前缀 `[subagent:{agent_id}]` 让主 Agent 知道这个子 Agent 还活着、可以续写。

### 5.4 send_to_agent 工具（续写原语）

```python
class SendToAgentArgs(BaseModel):
    agent_id: str             # spawn 返回的 agent_id
    message: str              # 追加指令 / 追问 / 让它修正


@tool
async def send_to_agent(args: SendToAgentArgs) -> str:
    """给已存在的子 Agent 发消息, 不用重新 brief。用于多轮委派。"""
    live = live_agents.get(args.agent_id)
    if live is None:
        return f"[error] subagent {args.agent_id} not found or expired"
    result = await live.agent.ainvoke(
        {"messages": live.messages + [HumanMessage(args.message)]}
    )
    live.messages = result["messages"]      # 追加保留
    final: AIMessage = result["messages"][-1]
    return final.content
```

`LiveAgent` 持有 `CompiledStateGraph` + 当前消息列表 + TTL，过期自动清理。这镜像 Claude Code 的 SendMessage。

### 5.5 Fork vs Fresh

```python
def build_initial_messages(args, definition, parent_history):
    brief = HumanMessage(args.prompt)
    if args.fork and definition.fork_allowed:
        # Fork: 继承主会话历史 + brief。贵但上下文全。
        return list(parent_history) + [brief]
    # Fresh: 只有 brief + definition.system_prompt。默认路径。
    return [brief]
```

默认 Fresh。Fork 只在子任务强依赖对话上下文时用，且定义里必须 `fork_allowed=True`。

### 5.6 Worktree 隔离

会改文件的子 Agent（`code` 类型）默认 `needs_worktree=True`。spawn 时在 `.tokendance/worktrees/<agent_id>` 建临时 worktree，子 Agent 在那里工作，互不干扰。worktree 路径写进返回值，主 Agent 知道产物落哪。子 Agent 结束后按 keep/remove 策略清理。

---

## 六、数据模型变更

```text
新增表:
  subagent_definitions   # 声明式子 Agent 定义
  agent_llm_configs       # 按 Agent 覆盖的 LLM 配置

删除(v0.1 的):
  agent_memories          # 不需要——共享靠 brief + tool result

Message 表加两列(不改表结构语义, 只增列):
  agent_name: str | None            # 产生该消息的 Agent 名(主 Agent 为 None 或 "main")
  parent_tool_call_id: str | None   # 委派树: 子 Agent 的消息回链到 spawn 的 tool_call_id
```

`parent_tool_call_id` 让前端能还原"主 Agent 调 spawn → 子 Agent 产生 N 条消息"的树状结构,无需 `AgentMemory`。

---

## 七、SSE 事件

主 Agent 的 token 流不变（现有 `token` / `tool_call` / `tool_result` / `done`）。子 Agent 内部不逐 token 推（避免和主 Agent 的流交错，对齐 Claude Code 子 Agent 折叠展示），只推生命周期事件：

```text
event: subagent_start
data: {"agent": "explore", "desc": "搜索 httpx 文档", "agent_id": "a3f"}

event: subagent_done
data: {"agent": "explore", "agent_id": "a3f", "status": "ok"}

event: subagent_message      # 可选: 推子 Agent 的阶段性结论
data: {"agent_id": "a3f", "content": "..."}
```

实现上 runner.py 给 spawn_agent 工具注入一个 `SSESink`（asyncio.Queue），工具内 `sse.emit` 入队，runner 在跑主 astream 的同时 drain 这个队列。主 Agent 自己的 token 仍走 `astream` 的 messages 模式。

---

## 八、数据流示例

用户："帮我写个 httpx 爬虫，同时搜下最新的 httpx 文档"

```text
Main Agent (推理):
  - 这是两个独立任务, 适合并行委派
  - emit tool_calls: [
      spawn_agent(explore, "搜 httpx 文档", "搜 httpx 最新用法, 只要结论"),
      spawn_agent(code,   "写爬虫骨架", "用 httpx 写个简单爬虫, 存到 spider.py")
    ]

ToolNode 并发执行两个 spawn:
  explore 子 Agent: 调 search MCP → 摘要
  code   子 Agent: 在 worktree 调 filesystem MCP → 写文件 → 跑构建

两个 ToolMessage 回到 Main Agent:
  [subagent:e1] httpx 推荐 async client + httpx.AsyncClient...
  [subagent:c2] 已在 worktree/.td/worktrees/c2/spider.py 生成爬虫...

Main Agent (synthesize):
  综合两份摘要 → 给用户最终回答 + 代码 + 文档要点
  (如果 code 子 Agent 产物有问题, 再 send_to_agent(c2, "把超时改成 30s"))
```

注意整个过程主 Agent 上下文只多了两段摘要, 子 Agent 期间的所有搜索/写文件都没进主上下文。

---

## 九、API 变更

### 9.1 现有接口

`POST /agent/chat` 不变。主 Agent 自动决定是否委派——这是模型行为, 不是请求参数。`agent_profile_id` 字段删除（v0.1 的遗留, 不再需要）。

### 9.2 新增子 Agent 定义管理

```text
GET    /agents/definitions              # 列出用户的 SubagentDefinition
POST   /agents/definitions              # 创建
PUT    /agents/definitions/{id}
DELETE /agents/definitions/{id}
POST   /agents/definitions/seed          # 预置 explore/code/plan/review 四个种子

GET    /agents/llm-configs              # 列出 AgentLLMConfig
POST   /agents/llm-configs              # 创建(密钥加密同 UserLLMConfig)
DELETE /agents/llm-configs/{id}

GET    /agents/live                     # 列出当前存活的子 Agent(调试用)
```

---

## 十、改造路线图

### Phase 1：spawn_agent 工具（最小可用多 Agent）

- [ ] `SubagentDefinition` / `AgentLLMConfig` 模型 + Alembic migration
- [ ] `agents/spawn.py`：`spawn_agent` 工具 + `build_initial_messages`（先只支持 Fresh）
- [ ] `agents/registry.py`：按定义解析工具集（MCPRegistry 加 `get_tools_filtered`）+ 解析 LLM
- [ ] 主 Agent 的 `build_agent` 注入 `spawn_agent` 工具
- [ ] runner.py 注入 SSESink, 推 `subagent_start` / `subagent_done`
- [ ] `Message` 加 `agent_name` / `parent_tool_call_id` 列
- [ ] 种子定义预置接口 + 四个种子
- [ ] 集成测试：一个用户消息触发一次 spawn, 验证主上下文只多一段摘要

### Phase 2：并行 + 续写

- [ ] 验证一轮多 spawn tool_call 的并发（LangGraph ToolNode 原生, 主要测 MCPRegistry 并发安全）
- [ ] `send_to_agent` 工具 + `LiveAgentRegistry`（带 TTL 清理）
- [ ] 前端展示委派树（基于 `parent_tool_call_id`）

### Phase 3：Fork + Worktree 隔离

- [ ] `build_initial_messages` 支持 Fork（需把主会话历史传入工具）
- [ ] worktree 管理：建/清理/keep-remove 策略
- [ ] `code` 种子定义开 `needs_worktree`

### Phase 4：行为调优（prompting）

- [ ] 主 Agent system prompt 写入"默认自己做, 委派是刻意升级"约束 + 何时该委派的判据
- [ ] 各种子定义的 system prompt 精调（explore 强调只回结论, code 强调跑构建, plan 强调列文件）
- [ ] 加可观测：记录每次 spawn 的 token 成本, 评估委派是否划算

Phase 4 是最重要的——前 3 个 phase 是机制, 这个 phase 决定机制是否被合理使用。Claude Code 的多 Agent 好用, 很大程度是因为主 Agent 被反复告知"别乱委派"。

---

## 十一、风险与对策

| 风险 | 对策 |
| --- | --- |
| 主 Agent 乱委派, 简单任务也 spawn, token 翻倍 | Phase 4 的 system prompt 约束 + 记录每次 spawn 成本做反馈 |
| 嵌套 ainvoke 阻塞主 astream, 期间主 Agent 无输出 | 设计如此——主 Agent 在等子 Agent; 靠 `subagent_start/done` SSE 让前端知道在等。如需子 Agent 进度, 加可选 `subagent_message` 事件 |
| 子 Agent 复用 MCPRegistry, 并发下连接争用 | MCPRegistry 已是 per-user 缓存; 加连接池上限 + 并发度配置 |
| Fork 上下文过大, 成本高 | `fork_allowed` 默认 False; Fork 时只传最近 N 条而非全量 |
| worktree 残留 | TTL 清理 + 会话结束统一回收 |
| 子 Agent 失败无重试 | `send_to_agent` 让主 Agent 看到错误后可重发指令重试, 无需重建上下文 |
| LangGraph 嵌套 invoke 的 checkpoint/状态隔离 | 子 Agent 用独立 in-memory state, 不挂 checkpoint store; `LiveAgent` 自己持有 messages 列表 |

---

## 十二、文件变更清单

```text
新增:
  src/tokendanceai/agents/spawn.py          # spawn_agent / send_to_agent 工具
  src/tokendanceai/agents/registry.py        # SubagentDefinition 解析 + LLM 解析
  src/tokendanceai/agents/live.py            # LiveAgentRegistry (续写 + TTL)
  src/tokendanceai/agents/worktree.py        # worktree 建管清理
  src/tokendanceai/router/agents.py         # definitions / llm-configs CRUD 路由
  src/tokendanceai/schemas/agent.py          # SubagentDefinition schemas

修改:
  src/tokendanceai/agents/graph.py          # build_agent 增参: 注入 spawn/send 工具
  src/tokendanceai/agents/runner.py         # 注入 SSESink, drain 子 Agent 事件
  src/tokendanceai/models/model.py          # +SubagentDefinition +AgentLLMConfig; Message 加两列
  src/tokendanceai/services/llm_service.py  # 支持 AgentLLMConfig + 继承主 Agent 模型
  src/tokendanceai/mcp/registry.py           # +get_tools_filtered(allow/deny)
  src/tokendanceai/main.py                  # 注册 /agents 路由

新增 Alembic migration:
  alembic/versions/xxxx_add_subagent_tables.py
```

---

## 附：为什么这套比 v0.1 更适合 TokenDanceAI

v0.1 的 Supervisor 图把"何时分解任务、分解成几个、怎么并行"写死在图结构里, 每加一种协作模式都要改图。
v0.2 的工具委派把这些决策交给主 Agent 推理, 图结构始终是 `create_agent` 的两节点循环, 新协作模式 = 加一个 SubagentDefinition, 不改图。

对 TokenDanceAI 这种"用户接自己的 MCP server、Agent 能力随用户配置变化"的场景, 工具委派的弹性是关键——你无法预定义一个通用 Supervisor 图来覆盖所有用户的 MCP 组合, 但主 Agent + spawn 工具能按用户实际工具集动态决定怎么委派。
