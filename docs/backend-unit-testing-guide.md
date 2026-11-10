# 后端单元测试设计文档

> **文档性质**：TokenDance AI 后端的单元测试设计文档（Testing Design Doc）。既讲方法论，也给逐层落地参考。MCP 章节（第 7–8 章）已按原始 JSON Schema 契约落地，并链接到真实 tests；其余分层给出设计示例与待办清单，照着补即可。
>
> | 项 | 值 |
> |---|---|
> | 文档版本 | v2（MCP 原始 JSON Schema 与 SDK mock 契约同步） |
> | 状态 | crypto/auth/LLM/MCP 单测已落地；其余分层保留实施指南 |
> | 适用系统 | `tokendanceai`（FastAPI + SQLAlchemy async + langgraph + MCP） |
> | 目标读者 | 本项目后端开发者，需具备 Python async、FastAPI、pytest 基础认知即可 |
> | 关联代码 | `src/tokendanceai/`、`pyproject.toml` |
> | 配套文档 | [通用 Agent 系统设计文档](general-agent-textbook.md) |
>
> **阅读建议**：没接触过测试，从[序章](#序章)和[第 1 章](#ch-1)建立认知；要动手搭环境，直接跳[第 3–4 章](#part-2)（这是最容易卡住的一步）；查某一层怎么测，看[第 5–11 章](#part-3)对照你要测的文件；想知道测试能揪出哪些现成 bug，看[附录 A](#appendix-a)。

---

## 目录

- [序章 为什么现在必须补测试](#序章)
- [第 1 部分 测试认知](#part-1)
  - [第 1 章 单元测试到底是什么](#ch-1)
  - [第 2 章 这个项目该测什么、不测什么](#ch-2)
- [第 2 部分 测试基础设施](#part-2)
  - [第 3 章 技术栈选型](#ch-3)
  - [第 4 章 conftest.py 与 fixture 体系](#ch-4)
- [第 3 部分 分层测试实战（对照真实代码）](#part-3)
  - [第 5 章 基础层：crypto 加密](#ch-5)
  - [第 6 章 服务层：auth 认证](#ch-6)
  - [第 7 章 能力层：bridge 桥（原始 JSON Schema 与 B2 回归）](#ch-7)
  - [第 8 章 能力层：registry 缓存](#ch-8)
  - [第 9 章 编排层：memory 存取对称](#ch-9)
  - [第 10 章 编排层：runner 与 SSE（mock 外部依赖）](#ch-10)
  - [第 11 章 接口层：router 端点（会抓到 schema bug）](#ch-11)
- [第 4 部分 落地](#part-4)
  - [第 12 章 怎么跑、覆盖率、CI](#ch-12)
  - [第 13 章 落地顺序](#ch-13)
- [附录](#appendix)
  - [A. 测试能暴露的真实 bug 清单](#appendix-a)
  - [B. 常用 mock 配方](#appendix-b)
  - [C. 测试命名与组织规范](#appendix-c)
  - [D. 术语表](#appendix-d)

---

<a id="序章"></a>
## 序章：为什么现在必须补测试

### 现状

后端五大模块——数据模型、MCP 桥接、agent 编排、认证、HTTP 接口——代码都写完了，能跑起来。但「能跑起来」和「正确」是两回事。最初缺少自动化测试，只能手动点 Swagger/前端；如今 `tests/` 已有 crypto/auth/LLM/MCP 回归，仍需继续覆盖其余分层与真实 SDK 集成。单靠手工验证有三个致命问题：

1. **覆盖不全**：手动只会走「正常路径」。空参数、过期 token、禁用的 server、解密失败这些分支，没人会一个个手点。
2. **不可重复**：改完一处代码，没法一键确认「别的地方没被改坏」。回归全靠记忆。
3. **发现太晚**：bug 要等到前端联调、甚至上线才暴露，那时定位成本是写测试时的几十倍。

### 立论：测试是「可执行的规格说明」

> **一个单元测试，就是一句话：「给定这样的输入，这个函数必须产出那样的输出」。把这句话写成代码，它就能自动、重复、永久地帮你守住这个约定。**

这不是额外负担，而是把你脑子里「这个函数应该这样工作」的隐性知识，固化成机器可以反复检查的显性契约。

### 这份文档不空谈——先看三个现成的 bug

三个真实 bug，测试都能在合并前抓到。其中 B2 已修复并转为正式回归，B1/B3 仍待修（对应测试已写好）：

| # | 位置 | bug | 后果 | 哪个测试抓到 |
|---|---|---|---|---|
| B1 | [schemas/session.py:11](../src/tokendanceai/schemas/session.py#L11) | `SessionPublic.create_at` 字段名拼错，模型里是 `created_at` | `GET /api/sessions` 直接 500，会话列表页全崩 | [第 11 章](#ch-11) |
| B2 | [mcp/bridge.py](../src/tokendanceai/mcp/bridge.py) | **历史已修复**：旧参数模型转换使必填失效 | 原始 JSON Schema + 显式校验拒绝缺失参数，不远调 | [第 7 章](#ch-7)，正式回归无 xfail |
| B3 | [schemas/agent.py:7](../src/tokendanceai/schemas/agent.py#L7) | `autopoilt: bool` 拼写错误且为必填，但路由根本没用它 | 前端不传这个字段，`POST /api/agent/run` 直接 422 | [第 11 章](#ch-11) |

手动测试很可能漏掉 B2（要专门构造缺失参数才触发），现在它由正式回归守护；B1/B3 的测试也已写好，等代码修复后转绿。**测试的价值，就是让这类问题在你合并代码前五秒钟就红灯报警，而不是上线后收用户投诉。** 完整清单见[附录 A](#appendix-a)。

### 文档的两条线

- **方法论线**（第 1–2 章）：单元测试的定义、边界、该测什么——回答「为什么这么测」。
- **实现线**（第 3–13 章）：环境搭建、逐层测试代码、落地顺序——回答「具体怎么写」。

---

<a id="part-1"></a>
# 第 1 部分 测试认知

<a id="ch-1"></a>
## 第 1 章 单元测试到底是什么

### 1.1 定义：隔离地验证「最小单元」

**单元测试（Unit Test）**：针对软件里**最小可测单元**（通常是一个函数或一个方法），在**与外部依赖隔离**的前提下，验证它在给定输入下的行为是否符合预期。

拆开这个定义里的两个关键词：

- **最小单元**：测 `encrypt()` 这一个函数，而不是测「用户注册的整条链路」。单元越小，出问题时定位越准——测试红了，你立刻知道是哪个函数坏了。
- **隔离**：测 `build_chat_model()` 时，不真的去连 OpenAI；测 `run_agent()` 时，不真的让 LLM 推理。外部依赖（网络、数据库、第三方 API）用**替身（mock）**顶替。这样测试才快、才稳定、才不花钱。

### 1.2 三种测试的分工（测试金字塔）

单元测试不是唯一的测试类型，它和另外两类构成一个金字塔——**越往下越多、越快、越便宜**：

```
          ╱╲
         ╱E2E╲        端到端：真浏览器点真后端真数据库。最真实，最慢最脆，最少写。
        ╱──────╲
       ╱ 集成测试 ╲     集成：多个真实组件一起测（真 DB + 真路由），验证「接缝」对不对。
      ╱──────────╲
     ╱  单元测试    ╲   单元：单个函数，依赖全 mock。最快最多最稳，本文档主角。
    ╱────────────────╲
```

| 类型 | 测什么 | 外部依赖 | 速度 | 本项目的例子 |
|---|---|---|---|---|
| 单元测试 | 单个函数的逻辑 | 全部 mock | 毫秒级 | `verify_access_token()` 对过期 token 返回 `None` |
| 集成测试 | 组件间协作 | 真实（用测试替身库，如内存 DB） | 百毫秒级 | `POST /api/user` 真的往测试库插一行 |
| 端到端 | 完整用户流程 | 全真实 | 秒级 | 浏览器注册→登录→发消息 |

> **本项目的策略**：以**单元测试**为主干，辅以**用内存数据库的接口集成测试**（第 11 章）。真正的 E2E 不在本文档范围——那需要前端配合，成本高、收益在后期。我们把 80% 的精力放在金字塔底部，用最低成本挡掉最多 bug。

### 1.3 一个好的单元测试长什么样

好的单元测试遵循 **AAA 结构**——每个测试都分三段，读起来像一个小故事：

```python
async def test_decrypt_recovers_original_plaintext():
    # Arrange（准备）：构造输入
    plaintext = "sk-secret-key-123"

    # Act（执行）：调用被测单元
    recovered = decrypt(encrypt(plaintext))

    # Assert（断言）：验证结果
    assert recovered == plaintext
```

同时追求 **FIRST 原则**：

| 字母 | 含义 | 落到这个项目 |
|---|---|---|
| **F**ast | 快 | 不连真 DB、不连真 LLM，全套测试几秒跑完 |
| **I**solated | 独立 | 测试之间不共享状态，每个用例一个干净的内存库 |
| **R**epeatable | 可重复 | 跑一万次结果一样，不依赖当前时间/网络 |
| **S**elf-validating | 自验证 | `assert` 直接给红绿，不用人眼看输出 |
| **T**imely | 及时 | 和代码一起写（本项目是补写，更要尽快） |

### 1.4 为什么这个项目「尤其」需要测试

TokenDance AI 有三个特征，让它比普通 CRUD 应用更需要测试：

1. **全链路异步**：`async/await` 到处都是。异步代码的 bug（忘了 `await`、session 生命周期错配、`expire_on_commit` 踩坑）用肉眼极难发现，但有针对性的单测或真实组件集成测试能提前暴露；mock 单测不能证明真实 SDK 的跨任务生命周期。
2. **大量外部依赖**：LLM、MCP server 都是网络外部系统。没有 mock，你根本没法稳定地验证「MCP schema 是否保真传递、非法参数是否在远调前拒绝」。
3. **安全敏感**：加密、JWT、per-user 隔离、token 脱敏——这些地方错一点就是安全事故。它们恰恰是最适合单元测试的纯逻辑（见[第 5–6 章](#ch-5)）。

---

<a id="ch-2"></a>
## 第 2 章 这个项目该测什么、不测什么

### 2.1 测试优先级：按「风险 × 复杂度」排序

不是所有代码都值得同等对待。按「出错的概率 × 出错的代价」排优先级：

| 优先级 | 特征 | 本项目对应 |
|---|---|---|
| 🔴 最高 | 有分支逻辑 + 安全/数据相关 | `verify_access_token`、bridge schema 保真/显式校验、registry 缓存/TTL/失败关闭、路由的归属校验 |
| 🟠 高 | 有分支逻辑，纯计算 | `build_chat_model` 的 provider 分支、memory 的 load/save 对称 |
| 🟡 中 | 有状态但逻辑简单 | 各 CRUD 路由的正常路径 + 错误码 |
| ⚪ 低 | 无分支、纯声明 | ORM 字段定义、Pydantic schema 字段（但**字段名错配要测**，见 B1/B3） |

### 2.2 测试地图：对照项目分层

把[系统设计文档第 2 章](general-agent-textbook.md)的分层拿过来，逐层标出「测什么」和「本文档对应章节」：

```
          ┌─────────────────────────────┐
  入口层   │  pages/(HTML)   router/(API) │   → 第 11 章：端点鉴权、归属校验、错误码
          └──────┬──────────────┬────────┘
                 ▼              ▼
  编排层   ┌──────────┐   ┌──────────┐
          │ agents/  │   │ services/│      → 第 9/10 章 + 第 6 章
          └────┬─────┘   └────┬─────┘
               ▼              │
  能力层   ┌──────────┐       │             → 第 7/8 章：桥的原 schema/显式校验、registry 缓存
          │  mcp/    │       │
          └────┬─────┘       │
               ▼             ▼
  模型层        ┌──────────────────┐        → 第 11 章随接口一起测（关系/约束）
               │     models/       │
               └────────┬──────────┘
                        ▼
  基础层   ┌──────────────────────────┐    → 第 5 章：crypto 往返与篡改检测
          │  db/   config.py  utils/  │
          └──────────────────────────┘
```

| 层 | 包 | 核心测试点 | 章节 |
|---|---|---|---|
| 基础 | `utils/crypto.py` | 加解密往返、密文不等于明文、篡改检测 | [第 5 章](#ch-5) |
| 服务 | `services/auth.py` | 密码哈希、JWT 签发/验证/过期、`get_current_user` 的 401/404 | [第 6 章](#ch-6) |
| 服务 | `services/llm_service.py` | provider 分支、解密调用、未知 provider 报错 | [第 6 章](#ch-6) |
| 能力 | `mcp/bridge.py` | 原始 JSON Schema 保真、显式校验、missing/null、结果/错误 | [第 7 章](#ch-7) |
| 能力 | `mcp/registry.py` | 缓存命中、TTL 过期、`invalidate`、`enabled` 跳过 | [第 8 章](#ch-8) |
| 编排 | `agents/memory.py` | load/save 对称、tool_call 还原 | [第 9 章](#ch-9) |
| 编排 | `agents/runner.py` | SSE 事件格式、落库时机、只转发 agent 节点 | [第 10 章](#ch-10) |
| 入口 | `router/*.py` | 认证、归属校验、CRUD、错误码 | [第 11 章](#ch-11) |

### 2.3 不值得花力气测的东西

测试也有成本，这些**别测**或**少测**：

- **纯 ORM 字段声明**：`id: Mapped[int] = mapped_column(...)` 这种没有逻辑，测它等于测 SQLAlchemy 本身。
- **第三方库的行为**：不需要测「Fernet 加密是否可逆」（那是 `cryptography` 库的职责），只需测「我们的 `encrypt/decrypt` 封装用对了」。
- **getter/setter、常量**：没有分支就没有测试价值。

> **一条实用判据**：如果一个函数里**没有 `if`、没有循环、没有 `try`、不调用别的会出错的东西**，它大概率不值得单测。把精力留给有分支的地方。

> **例外——字段名错配要测**：schema 字段名和模型对不上（B1/B3）虽然是「声明」，但它会让整个接口 500/422。这种用一个**接口冒烟测试**覆盖即可（第 11 章），不必对每个字段单独写。

---

<a id="part-2"></a>
# 第 2 部分 测试基础设施

> 这一部分是**全文最容易卡住的地方**。异步 + 数据库 + 外部依赖三件事叠在一起，环境没搭对，后面每个测试都跑不起来。建议照着抄一遍跑通，再往下看。

<a id="ch-3"></a>
## 第 3 章 技术栈选型

### 3.1 要装什么

项目 `dev` 组已包含 pytest、pytest-asyncio、ruff，且 pytest 配置已就位；以下是从空环境搭建时的参考命令，无需重复添加已安装依赖：

```bash
uv add --dev pytest pytest-asyncio
# httpx 已随 fastapi 装了（用于接口测试的 ASGITransport），无需再装
```

| 包 | 作用 | 为什么选它 |
|---|---|---|
| `pytest` | 测试框架 | Python 事实标准，`assert` 直接用、fixture 机制强大 |
| `pytest-asyncio` | 跑 `async def` 测试 | 本项目全链路 async，没它测不了任何异步函数 |
| `httpx` | 接口测试客户端 | 已安装；它的 `ASGITransport` 能**不起真服务器**直接测 FastAPI app |

**已经就位的依赖**（无需再装，但测试会用到）：

- `aiosqlite`（已装）：异步 SQLite 驱动，用它做**内存测试数据库**——比真 PostgreSQL 快几个量级，且用完即焚，测试间互不污染。
- `greenlet`（已装）：SQLAlchemy async 的底层依赖，验证了异步 ORM 能跑。

> **为什么不直接用生产的 PostgreSQL 测**：真 DB 慢、要起服务、测试间状态会串、CI 上还要额外配。内存 SQLite 让每个测试拿到一个全新的空库，几毫秒建表、几毫秒销毁。代价是 SQLite 和 PostgreSQL 有方言差异（见 [3.4](#ch-3)），但对本项目的 CRUD 足够。

### 3.2 pyproject.toml 配置

项目 `pyproject.toml` 已有下列 pytest 配置；新项目可按此添加：

```toml
[tool.pytest.ini_options]
asyncio_mode = "auto"          # 关键：让所有 async def 测试自动被当协程跑，不用每个都加 @pytest.mark.asyncio
testpaths = ["tests"]           # 测试都放 tests/ 目录
pythonpath = ["src"]            # 让测试能 import tokendanceai（项目用 src-layout）
addopts = "-v --tb=short"       # 默认详细输出 + 精简 traceback
```

**`asyncio_mode = "auto"` 是最省心的设置**：有了它，写 `async def test_xxx()` 就能直接跑，不用在每个测试上贴 `@pytest.mark.asyncio`。整个项目都是异步的，这个设置能省掉几百个装饰器。

### 3.3 目录结构

测试目录镜像 `src/` 的结构，一眼能对上「哪个测试测哪个模块」：

```
tokenDanceAI/
  src/tokendanceai/...
  tests/
    conftest.py              # ★ 全局 fixture（第 4 章），pytest 自动加载
    test_crypto.py           # → utils/crypto.py（第 5 章）
    test_auth.py             # → services/auth.py（第 6 章）
    test_llm_services.py      # → services/llm_service.py（第 6 章）
    test_bridge.py           # → mcp/bridge.py（第 7 章）
    test_registry.py         # → mcp/registry.py（第 8 章）
    test_memory.py           # → agents/memory.py（第 9 章）
    test_runner.py           # → agents/runner.py（第 10 章）
    router/
      test_user.py           # → router/user.py（第 11 章）
      test_servers.py        # → router/servers.py（第 11 章）
      test_sessions.py       # → router/sessions.py（第 11 章）
      test_account.py        # → router/account.py（第 11 章）
      test_agent.py          # → router/agent.py（第 11 章）
```

**约定**（见[附录 C](#appendix-c)）：文件名 `test_<模块>.py`，函数名 `test_<被测对象>_<场景>_<预期>`。例如 `test_verify_access_token_expired_returns_none`——光看名字就知道它测什么。

### 3.4 SQLite vs PostgreSQL：必须知道的差异

用内存 SQLite 测、生产跑 PostgreSQL，有几处方言差异要心里有数，否则会出现「测试过了线上挂」或反之：

| 维度 | SQLite（测试） | PostgreSQL（生产） | 对本项目的影响 |
|---|---|---|---|
| 大小写比较 | `LIKE` 默认不区分大小写，`=` 区分 | 都区分 | `user.py` 用 `func.lower()` 显式转小写（见 [router/user.py:32](../src/tokendanceai/router/user.py#L32)），**两边行为一致**，没踩坑 ✅ |
| 外键级联 | 默认**不强制**外键 | 强制 | 删 session 时 Message 的级联在 SQLite 测不出来（代码本就标了 `todo cascade`），需要时在测试 engine 上开 `PRAGMA foreign_keys=ON` |
| 并发/行锁 | 弱 | 强 | 单元测试不测并发，无影响 |
| 唯一约束 | 支持 | 支持 | `username`/`email` 的 unique 两边都能测 ✅ |

> **结论**：对这个项目的 CRUD 和逻辑测试，SQLite 足够且安全。唯一要注意的是**外键级联**——如果以后要测「删 session 自动删 message」，记得在测试 engine 建连时开 `PRAGMA foreign_keys=ON`（配方见[附录 B](#appendix-b)）。

---

<a id="ch-4"></a>
## 第 4 章 conftest.py 与 fixture 体系

`conftest.py` 是 pytest 的「共享装备库」——放在 `tests/` 下，里面定义的 **fixture**（测试夹具）会被同目录及子目录的所有测试自动共享，不用 import。这是整个测试体系的地基。

### 4.1 fixture 是什么

**fixture**：测试运行前帮你「准备好环境」、测试结束后「自动收拾」的函数。比如「给我一个连好的空数据库」「给我一个登录好的测试用户」。测试函数把 fixture 名字写进参数，pytest 就自动把准备好的东西喂进来：

```python
async def test_create_user(client):   # ← "client" 是个 fixture，pytest 自动注入
    resp = await client.post("/api/user", json={...})
    assert resp.status_code == 201
```

你不用管 `client` 怎么来的，conftest 已经建好了。这就是 fixture 的价值：**把「准备工作」和「测试逻辑」分开**，每个测试只写自己关心的那点。

### 4.2 第一道坎：配置是模块级单例，import 即执行

这个项目有个**必须先解决的陷阱**。看三个文件的顶层代码：

```python
# config.py:21        —— import 时就实例化，从 .env/环境变量读
settings = Settings()

# utils/crypto.py:5   —— import 时就用 settings 的 key 固化 Fernet 实例
_fernet = Fernet(settings.ai_encryption_key.get_secret_value())

# db/db_session.py:9  —— import 时就用 settings.database_url 建引擎
async_engine = create_async_engine(url=settings.database_url)
```

**含义**：只要你的测试 `import tokendanceai.任何东西`，这三行就会执行。如果那一刻环境变量里没有合法的 `database_url` 和 `ai_encryption_key`，**import 直接崩**，测试一个都收集不到。

**解决**：在 `conftest.py` 的**最顶部**（任何 `tokendanceai` import 之前）用 `os.environ` 注入测试专用配置。pydantic-settings 的优先级是「环境变量 > .env 文件」，所以这样能稳定覆盖，且让项目**没有 .env 也能跑测试**（CI 友好）。

### 4.3 conftest.py 完整实现

下面是可直接用的 `tests/conftest.py`，每段都有注释说明为什么这么写：

```python
# tests/conftest.py
"""全局测试夹具。本文件的 import 顺序是刻意安排的，不要打乱。"""
import os

# ─────────────────────────────────────────────────────────────
# ① 必须在 import 任何 tokendanceai 模块之前注入测试配置！
#    原因见文档 4.2：config/crypto/db_session 在 import 时就读 settings。
# ─────────────────────────────────────────────────────────────
from cryptography.fernet import Fernet

# env_prefix="TokenDance"，pydantic-settings 大小写不敏感，这样写即可匹配
os.environ["TokenDanceDatabase_Url"] = "sqlite+aiosqlite:///:memory:"
# JWT 密钥至少 32 字节，否则 PyJWT 会告警 InsecureKeyLength
os.environ["TokenDanceSecret_Key"] = "test-secret-key-at-least-32-bytes-long!!"
os.environ["TokenDanceAI_Encryption_Key"] = Fernet.generate_key().decode()

# ② 现在才安全地 import 应用代码
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from tokendanceai.db.base import Base
from tokendanceai.db.db_session import get_db
from tokendanceai.main import app
from tokendanceai.mcp.registry import MCPRegistry, get_registry
from tokendanceai.models import model as models
from tokendanceai.services.auth import get_current_user

# 确保所有 model 都被 import（Base.metadata 才有这些表），建表才建得全
import tokendanceai.models.model  # noqa: F401, E402


# ─────────────────────────────────────────────────────────────
# 数据库 fixture：每个测试一个全新的内存库
# ─────────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def engine():
    """内存 SQLite 引擎。StaticPool + 单连接，保证 :memory: 库在整个测试内可见。"""
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,   # 关键：不加它，每次取连接是不同的内存库，建的表就"消失"了
    )
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)   # 建表
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(engine) -> AsyncSession:
    """一个绑定到测试引擎的 session，直接喂给需要 db 的单元测试。"""
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with maker() as session:
        yield session


# ─────────────────────────────────────────────────────────────
# 应用 fixture：覆盖依赖，拿到一个可测的 HTTP 客户端
# ─────────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def client(engine):
    """
    返回一个 httpx AsyncClient，直接打到 FastAPI app（不起真服务器）。
    关键动作：用 dependency_overrides 把生产依赖换成测试替身。
    """
    maker = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async def _override_get_db():
        async with maker() as session:
            yield session

    # 把路由里 Depends(get_db) 全部换成测试库
    app.dependency_overrides[get_db] = _override_get_db
    # registry 不走 lifespan，这里给一个真实但空的实例（tool 调用在各自测试里 mock）
    app.dependency_overrides[get_registry] = lambda: MCPRegistry(cache_ttl=300)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    app.dependency_overrides.clear()   # 收尾：清掉覆盖，避免污染别的测试


# ─────────────────────────────────────────────────────────────
# 数据 fixture：预置一个测试用户 / 认证客户端
# ─────────────────────────────────────────────────────────────
@pytest_asyncio.fixture
async def test_user(db) -> models.User:
    """往测试库插一个用户，供需要"已有用户"的测试复用。"""
    from tokendanceai.services.auth import hash_password

    user = models.User(
        username="alice",
        email="alice@example.com",
        password_hash=hash_password("password123"),
    )
    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_client(client, test_user):
    """
    已登录的客户端：直接覆盖 get_current_user 返回 test_user，
    跳过 JWT 流程（JWT 本身在 test_auth.py 单独测）。
    """
    app.dependency_overrides[get_current_user] = lambda: test_user
    yield client
    # client fixture 退出时会 clear，这里不用重复清
```

### 4.4 这套 fixture 的设计要点

| fixture | 给谁用 | 设计理由 |
|---|---|---|
| `engine` | 底层 | 每个测试一个独立内存库。`StaticPool` 是 SQLite 内存库的**必配项**——不加它，连接池每次给新连接 = 新的空库，你建的表会"查无此表"。 |
| `db` | 直接测 service/repo 层 | 给需要 `AsyncSession` 参数的函数（如 `load_messages(db, ...)`）直接喂一个真 session，不经过 HTTP。 |
| `client` | 测路由（未登录场景） | 核心是 `dependency_overrides`：把 `get_db` 换成测试库、`get_registry` 换成空 registry。`ASGITransport` 让请求**不经过网络**直接进 app，快且稳。 |
| `auth_client` | 测路由（已登录场景） | 覆盖 `get_current_user` 直接返回测试用户，省去每个测试都先注册+登录。**注意**：这会跳过真实鉴权，所以「未登录返回 401」要用 `client`（不覆盖）单独测。 |

### 4.5 两个关键陷阱（踩了必卡）

1. **import 顺序不能乱**：4.3 的 `os.environ[...]` 必须在 `from tokendanceai... import` **之前**。IDE 的自动格式化（isort / ruff）会把 import 排到文件顶部，可能把 `os.environ` 挤到后面——加 `# noqa: E402` 或用 `ruff: isort: skip` 保护。这是本项目最容易踩的测试坑，因为 `crypto.py` 的 `_fernet` 在 import 时就固化了。

2. **`StaticPool` 不能省**：这是 SQLite `:memory:` 的专属坑。内存库的生命周期绑在连接上，连接一还池就没了。`StaticPool` 强制全程复用同一个连接，表才不会"消失"。换成真文件 SQLite 或 PostgreSQL 则不需要。

> **验证环境是否搭对**：写一个最小测试 `tests/test_smoke.py`：
> ```python
> async def test_env_ready(db):
>     from tokendanceai.models.model import User
>     db.add(User(username="t", email="t@t.com", password_hash="x"))
>     await db.commit()   # 不抛异常 = 库连通、表建好了
> ```
> 跑 `uv run pytest tests/test_smoke.py`，绿了就说明第 3–4 章的地基是稳的，可以开始写真测试。

---

<a id="part-3"></a>
# 第 3 部分 分层测试实战（对照真实代码）

> 从这里开始，每章对应一个真实模块：先说**这个模块该测什么**，再给**可直接用的测试代码**，关键处标注**它为什么重要**、**会不会抓到现成 bug**。顺序是从底层（无依赖、最好测）到上层（依赖多、要 mock）。

<a id="ch-5"></a>
## 第 5 章 基础层：crypto 加密

**被测对象**：[utils/crypto.py](../src/tokendanceai/utils/crypto.py) 的 `encrypt` / `decrypt`（各一行，封装 Fernet）。

### 5.1 该测什么

这两个函数虽然短，但它们是**所有凭据安全的地基**（LLM key、MCP token 都靠它）。要测的不是「Fernet 对不对」（那是库的事），而是**我们的封装用对了没**：

| 测试点 | 为什么重要 |
|---|---|
| 往返一致：`decrypt(encrypt(x)) == x` | 最基本的正确性，错了所有凭据都读不回来 |
| 密文 ≠ 明文 | 确认真的加密了，不是原样存 |
| 同一明文两次加密密文不同 | Fernet 带随机 IV/时间戳，密文每次不同（防模式分析）——验证我们没禁用它 |
| 篡改密文 → 解密抛异常 | Fernet 带 HMAC，改一个字符就该失败——这是「密文被拖库篡改」的防线 |
| 解密垃圾字符串 → 抛异常 | 不能把非法输入静默当成空串 |

### 5.2 测试代码

```python
# tests/test_crypto.py
import pytest
from cryptography.fernet import InvalidToken

from tokendanceai.utils.crypto import decrypt, encrypt


def test_encrypt_decrypt_roundtrip():
    """最核心：加密再解密，拿回原文。"""
    plaintext = "sk-proj-abc123XYZ"
    assert decrypt(encrypt(plaintext)) == plaintext


def test_encrypt_output_differs_from_plaintext():
    """确认真的加密了，不是原样返回。"""
    plaintext = "my-secret-token"
    assert encrypt(plaintext) != plaintext


def test_same_plaintext_encrypts_to_different_ciphertext():
    """Fernet 带随机 IV，同一明文两次加密结果应不同——否则泄漏'两个 key 相同'的信息。"""
    plaintext = "same-input"
    assert encrypt(plaintext) != encrypt(plaintext)
    # 但都能解回同一个原文
    assert decrypt(encrypt(plaintext)) == decrypt(encrypt(plaintext)) == plaintext


def test_decrypt_tampered_ciphertext_raises():
    """密文被改一个字符，HMAC 校验失败，必须抛异常而不是返回错数据。"""
    ciphertext = encrypt("important")
    tampered = ciphertext[:-2] + ("AA" if ciphertext[-2:] != "AA" else "BB")
    with pytest.raises(InvalidToken):
        decrypt(tampered)


def test_decrypt_garbage_raises():
    """非法输入必须报错，不能静默吞掉。"""
    with pytest.raises(InvalidToken):
        decrypt("this-is-not-a-valid-fernet-token")


def test_unicode_roundtrip():
    """中文/emoji 也要能正确往返（encode/decode 用了 utf-8）。"""
    plaintext = "密钥🔑测试"
    assert decrypt(encrypt(plaintext)) == plaintext
```

### 5.3 要点

- **不需要真 key**：conftest 已经在环境变量里注入了一个 `Fernet.generate_key()`，`_fernet` 在 import 时就用它初始化好了。测试直接用即可。
- **`test_same_plaintext_encrypts_to_different_ciphertext` 容易被忽略但很重要**：它验证了加密的「语义安全」。如果哪天有人「优化」成缓存密文，这个测试会红。
- 这是**最适合入门的一章**：无 async、无 mock、无 DB，纯函数进出。先把这个跑绿，建立信心。

---

<a id="ch-6"></a>
## 第 6 章 服务层：auth 认证 与 llm_service

### 6.1 auth —— 该测什么

[services/auth.py](../src/tokendanceai/services/auth.py) 是**安全的核心**，有明确的分支逻辑，优先级最高（🔴）。分两类：

**纯函数（好测，无依赖）**：

| 函数 | 测试点 |
|---|---|
| `hash_password` / `verify_password` | 哈希不等于原文；对的密码验过、错的验不过 |
| `create_access_token` | 能签出 token；带 `sub` 和 `exp` |
| `verify_access_token` | 合法 token 返回 sub；过期 token 返回 `None`；篡改 token 返回 `None`；缺 `sub` 返回 `None` |

**依赖函数（要 DB/mock）**：

| 函数 | 测试点 |
|---|---|
| `get_current_user` | token 无效 → 401；token 合法但用户不存在 → 404；正常 → 返回 User |
| `get_current_admin_user` | 非 admin → 403；admin → 放行 |

### 6.2 auth 测试代码

```python
# tests/test_auth.py
from datetime import timedelta

import pytest
from fastapi import HTTPException

from tokendanceai.services.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_access_token,
    verify_password,
)


# ---------- 密码哈希 ----------
def test_hash_password_is_not_plaintext():
    assert hash_password("secret123") != "secret123"


def test_verify_password_correct_and_wrong():
    h = hash_password("secret123")
    assert verify_password("secret123", h) is True
    assert verify_password("wrong", h) is False


# ---------- JWT ----------
def test_create_and_verify_token_roundtrip():
    token = create_access_token(data={"sub": "42"})
    assert verify_access_token(token) == "42"


def test_expired_token_returns_none():
    """过期 token 必须验不过——这是会话安全的底线。"""
    token = create_access_token(data={"sub": "42"}, expires_delta=timedelta(seconds=-1))
    assert verify_access_token(token) is None


def test_tampered_token_returns_none():
    token = create_access_token(data={"sub": "42"})
    assert verify_access_token(token + "x") is None


def test_token_without_sub_returns_none():
    """verify_access_token 要求 'sub' 存在（options require sub）。"""
    token = create_access_token(data={"foo": "bar"})
    assert verify_access_token(token) is None


# ---------- get_current_user（需要 DB fixture）----------
async def test_get_current_user_invalid_token_raises_401(db):
    with pytest.raises(HTTPException) as exc:
        await get_current_user(token="garbage", db=db)
    assert exc.value.status_code == 401


async def test_get_current_user_nonexistent_user_raises_404(db):
    """token 合法但指向不存在的用户 → 404。"""
    token = create_access_token(data={"sub": "9999"})
    with pytest.raises(HTTPException) as exc:
        await get_current_user(token=token, db=db)
    assert exc.value.status_code == 404


async def test_get_current_user_returns_user(db, test_user):
    token = create_access_token(data={"sub": str(test_user.id)})
    user = await get_current_user(token=token, db=db)
    assert user.id == test_user.id
    assert user.username == "alice"
```

### 6.3 要点

- **过期 token 测试用负的 `timedelta`**：`expires_delta=timedelta(seconds=-1)` 直接签一个「已经过期」的 token，不用等。这是测时间相关逻辑的标准技巧——**永远不要在测试里 `sleep`**。
- **`get_current_user` 直接调函数、传 `db` fixture**：它虽然是 FastAPI 依赖，但本质是个 async 函数，可以脱离 HTTP 直接调，省去起客户端。参数 `token` 和 `db` 直接传。
- **admin 测试**：`get_current_admin_user` 接收的是 `current_user`，直接构造一个 `is_admin=False` 的 User 传进去验 403 即可，不用走 DB。

### 6.4 llm_service —— 该测什么

[services/llm_service.py](../src/tokendanceai/services/llm_service.py) 的 `build_chat_model` 有三个分支（openai / anthropic / 未知），且第一行要解密。要测：

| 测试点 | 为什么 |
|---|---|
| openai provider → 返回 `ChatOpenAI`，且 model/base_url 正确传入 | 最常用分支 |
| anthropic provider → 返回 `ChatAnthropic` | 第二分支 |
| 未知 provider → 抛 `ValueError` | 错误处理，不能静默降级 |
| 解密被调用、key 正确传入 | 验证密文→明文这一步没漏 |

### 6.5 llm_service 测试代码

```python
# tests/test_llm_services.py
import pytest
from langchain_anthropic import ChatAnthropic
from langchain_openai import ChatOpenAI

from tokendanceai.models.model import UserLLMConfig
from tokendanceai.services.llm_service import build_chat_model
from tokendanceai.utils.crypto import encrypt


def _make_config(provider: str) -> UserLLMConfig:
    """构造一个未入库的 UserLLMConfig（纯对象，不需要 DB）。"""
    return UserLLMConfig(
        user_id=1,
        provider=provider,
        base_url="https://api.example.com/v1",
        model="test-model",
        encrypted_api_key=encrypt("sk-test-key"),
    )


def test_build_openai_model():
    model = build_chat_model(_make_config("openai"))
    assert isinstance(model, ChatOpenAI)
    assert model.model_name == "test-model"


def test_build_anthropic_model():
    model = build_chat_model(_make_config("anthropic"))
    assert isinstance(model, ChatAnthropic)


def test_unknown_provider_raises():
    """配错 provider 必须显式报错，不能默认到某个 provider。"""
    with pytest.raises(ValueError, match="unknown provider"):
        build_chat_model(_make_config("gemini"))


def test_api_key_is_decrypted_before_use(monkeypatch):
    """验证解密被调用：拦截 decrypt，确认传进去的是密文、用的是明文。"""
    import tokendanceai.services.llm_service as svc

    captured = {}

    def fake_decrypt(ciphertext: str) -> str:
        captured["ciphertext"] = ciphertext
        return "sk-decrypted"

    monkeypatch.setattr(svc, "decrypt", fake_decrypt)
    cfg = _make_config("openai")
    model = build_chat_model(cfg)

    assert captured["ciphertext"] == cfg.encrypted_api_key      # 传的是密文
    assert model.openai_api_key.get_secret_value() == "sk-decrypted"  # 用的是明文
```

### 6.6 要点

- **构造 ORM 对象不必入库**：`UserLLMConfig(...)` 直接 `new` 一个纯 Python 对象，`build_chat_model` 只读它的属性，不碰数据库。这让测试飞快。
- **`monkeypatch` 是 pytest 内置的 mock**：`test_api_key_is_decrypted_before_use` 用它把 `decrypt` 换成一个会「记录输入」的假函数，从而验证「解密这一步确实发生了、且用对了密文」。这是验证「函数内部调用了某依赖」的标准手法。
- **不真的连 OpenAI**：`ChatOpenAI(...)` 只是构造对象，不会发网络请求（真正的请求在 `.ainvoke()` 才发）。所以这里安全，不用 mock 网络。

---

<a id="ch-7"></a>
## 第 7 章 能力层：bridge 桥（原始 JSON Schema 契约与 B2 回归）

**被测对象**：[mcp/bridge.py](../src/tokendanceai/mcp/bridge.py) 的 `MCPToolWrapper` / `wrap_mcp_tools`。桥保留原始 JSON Schema、显式校验调用参数，并适配 SDK 调用与结果；不再生成 Pydantic 参数模型。设计依据见[系统设计文档第 6–7 章](general-agent-textbook.md#ch-6)，完整可执行测试见 [tests/test_bridge.py](../tests/test_bridge.py)。

### 7.1 该测什么

| 契约 | 正常/边界输入 | 必须断言 |
|---|---|---|
| schema 保真 | 完整嵌套 schema、空 `{}`、缺失 description、多 tool | `args_schema == input_schema` 且不是同一对象；嵌套对象也隔离；修改源 schema 不影响包装器；空 `{}` 不替换；metadata / session 正确 |
| 包装时校验 | dict schema；非 dict（包括布尔 schema、Pydantic 模型）；非法 schema | 非 dict 不接受（进入 `model_post_init` 后 `TypeError`）；非法 schema 精确抛 `SchemaError`，不远调 |
| 方言选择 | 无 `$schema`、受支持的显式方言、未知方言 | 无声明使用 Draft 2020-12；声明按 `validator_for` 支持的方言解释；未知方言 `SchemaError`，不回退 |
| missing vs null | 必填字符串缺失/为 null；必填 nullable 缺失/为 null；可选非 nullable 缺失/为 null | 缺失由 `required` 决定，null 由类型决定；拒绝时 `ToolException` 且 `call_tool.assert_not_awaited()` |
| union / enum | 多种允许类型、null、枚举内外的值 | 不只接受 union 第一种类型，不丢 enum；无字符串到数字的隐式转换 |
| nested / array | 内层必填、类型、`items`、数量/数值等约束 | 嵌套非法输入同样在本地拒绝，不只测顶层字段存在 |
| 本地引用 / 组合 | `$defs` / 本地 `$ref`；`oneOf` / `anyOf` / `allOf` | 本地引用与组合约束生效，`oneOf` 的多分支同时匹配也应拒绝 |
| 引用边界 | 缺失本地引用、远程 `$ref` | 无法解析的引用转 `ToolException` 且不远调；空 `referencing.Registry()` 不检索远程资源 |
| 不改参数 | 类型不符、schema 的 `default`、允许/禁止额外属性 | 不转换、不补默认值；允许的额外属性原值转发，禁止的额外属性拒绝 |
| 同步禁用 | 公开 `tool.invoke({})` | 精确 `NotImplementedError` 且 session 未 await |
| 调用转发 | 公开 `tool.ainvoke(arguments)`、本地名字不同于 `mcp_name` | 精确 `assert_awaited_once_with(mcp_name, arguments)` |
| 结果归一 | 真实 `CallToolResult` + `TextContent` / 非 text 内容、空 content | 仅文本按顺序换行拼接；无文本等于 `str(result)` fallback |
| 错误边界 | `is_error=True`、非预期结果、SDK / 网络异常 | 协议错误和非预期结果精确 `ToolException`；网络异常原样传播，且远调次数正确 |

**两个失败时机，分别断言**——这是本章测试的主轴：

- **包装期失败（`SchemaError`）**：`model_post_init` 只收 dict schema，选好 validator 后 `check_schema`。非法 schema、未知方言在建 tool 时就炸，根本走不到网络。
- **调用期失败（`ToolException`）**：`_arun` 里 `validate(kwargs)` 不过（参数非法，或 `$ref` 不可解析触发 `Unresolvable`）就抛 `ToolException`，且断言 `call_tool.assert_not_awaited()`。注意 `check_schema` 通过不代表所有引用都能解析——空 `Registry()` 只解析本地 `$ref`、不发远程下载，所以合法 schema 仍可能在这一步才暴露引用缺失。

两条易错点：显式 `$schema` 必须是字符串且与所选 `META_SCHEMA` 的 `$id`/`id`（去尾 `#`）匹配；未配 format checker，所以不要断言 `format` 行为，也不要拿 Pydantic 的参数 `ValidationError` 代替 JSON Schema 校验异常。

下表描述桥**应当**守护的完整契约。实际覆盖以 [tests/test_bridge.py](../tests/test_bridge.py) 为准——矩阵是目标清单，不是「每项都已验证」的结论。

### 7.2 代表性测试（可独立执行）

将下段保存为一个 pytest 文件即可在项目环境运行；使用 SDK autospec，不依赖额外自定义 fixture。完整用例以 [tests/test_bridge.py](../tests/test_bridge.py) 为准，不在文档复制整份测试。

```python
from copy import deepcopy
from unittest.mock import create_autospec

import pytest
from jsonschema.exceptions import SchemaError
from langchain_core.tools import ToolException
from mcp import ClientSession
from mcp.types import CallToolResult, TextContent, Tool

from tokendanceai.mcp.bridge import wrap_mcp_tools


def make_tool(schema):
    session = create_autospec(ClientSession, instance=True, spec_set=True)
    session.call_tool.return_value = CallToolResult(
        content=[TextContent(type="text", text="hello"),
                 TextContent(type="text", text="world")]
    )
    source = Tool(name="demo", description="d", input_schema=schema)
    return session, source, wrap_mcp_tools(session, [source])[0]


def test_schema_snapshot_preserves_nested_constraints():
    schema = {
        "type": "object",
        "properties": {"title": {"type": "string", "enum": ["hello"]}},
        "required": ["title"],
    }
    expected = deepcopy(schema)
    _, source, tool = make_tool(schema)
    assert tool.args_schema == expected
    assert tool.args_schema is not source.input_schema
    assert tool.args_schema["properties"] is not source.input_schema["properties"]
    source.input_schema["properties"]["title"]["enum"].append("changed")
    assert tool.args_schema == expected


@pytest.mark.asyncio
@pytest.mark.parametrize("arguments", [{}, {"title": None}, {"title": 3}])
async def test_invalid_arguments_fail_before_network(arguments):
    session, _, tool = make_tool({
        "type": "object",
        "properties": {"title": {"type": "string"}},
        "required": ["title"],
    })
    with pytest.raises(ToolException, match="Invalid arguments"):
        await tool.ainvoke(arguments)
    session.call_tool.assert_not_awaited()


@pytest.mark.asyncio
async def test_valid_arguments_preserve_extras_and_omit_defaults():
    session, _, tool = make_tool({
        "type": "object",
        "properties": {
            "title": {"type": "string"},
            "count": {"type": "integer", "default": 10},
        },
        "required": ["title"],
    })
    arguments = {"title": "hello", "extra": {"keep": True}}
    assert await tool.ainvoke(arguments) == "hello\nworld"
    session.call_tool.assert_awaited_once_with("demo", arguments)


def test_empty_schema_is_not_replaced_and_sync_is_forbidden():
    session, _, tool = make_tool({})
    assert tool.args_schema == {}
    with pytest.raises(NotImplementedError, match="async-only"):
        tool.invoke({})
    session.call_tool.assert_not_awaited()


def test_invalid_schema_fails_at_wrapping():
    session = create_autospec(ClientSession, instance=True, spec_set=True)
    source = Tool(name="bad", input_schema={"type": "not-a-json-type"})
    with pytest.raises(SchemaError):
        wrap_mcp_tools(session, [source])
    session.call_tool.assert_not_awaited()
```

### 7.3 SDK autospec：mock 网络，不伪造类型契约

生产字段仍为 `session: ClientSession`。`arbitrary_types_allowed=True` 允许 Pydantic 为 SDK 类型建立实例检查，**不允许随意鸭子类型实例通过校验**。测试必须使用 `create_autospec(ClientSession, instance=True, spec_set=True)`：异步方法成为带签名检查的 mock，未知属性赋值会失败，且能符合 SDK 实例检查。不要删除生产类型、改成局部 `Protocol` 或给随手写的 session 加 `type: ignore` 来绕过问题。

输入/输出使用真实 `mcp.types.Tool`、`CallToolResult`、`TextContent`；registry 的发现结果使用真实 `ListToolsResult`。当前 SDK 的 Python 属性为 `input_schema` / `is_error`，协议别名为 `inputSchema` / `isError`。这让字段、结果类型和 SDK 升级不兼容真正进入测试，而不是被 `SimpleNamespace` 遮住。

### 7.4 要点：断言公开行为，而不是旧内部实现

- **B2 的修法决定了测法**：旧转换器给必填字段补默认值，缺失参数就被放行。修复不是继续打补丁，而是保留原 schema、在 `_arun` 里显式校验——所以测试断言的是「缺参数在远调前被拒」这个公开行为，旧转换函数连同它的测试一起删掉，不留 `xfail`。
- **公开入口是 `ainvoke(arguments)`**：不要只调用 `_arun`，否则漏测 `BaseTool` 的参数解析路径。同步测试走 `invoke({})`。
- **异常断言要精确**：用 `pytest.raises(ToolException, match=...)` / `SchemaError` / `NotImplementedError`，不要写 `pytest.raises(Exception)`。每个调用前拒绝用例都断言 `assert_not_awaited()`；协议结果错误是调用后失败，应断言远调已发生。
- **Pydantic 仍然在**：这里只去掉 MCP schema 的有损参数模型转换；`BaseTool` 自身、API 模型和配置仍依赖 Pydantic。

---

<a id="ch-8"></a>
## 第 8 章 能力层：registry 缓存

**被测对象**：[mcp/registry.py](../src/tokendanceai/mcp/registry.py) 的 `MCPRegistry` / `_LiveClient`。完整测试见 [tests/test_registry.py](../tests/test_registry.py)，设计与限制见[系统设计文档第 9 章](general-agent-textbook.md#ch-9)。registry 负责发现、连接缓存与关闭；bridge 负责包装和参数校验，不互相接管职责。

### 8.1 该测什么

| 测试点 | 必须断言 |
|---|---|
| `enabled=False` 跳过、空配置 | 返回空列表；不建连、不发现 tools |
| 同一 `(user_id, server_name)` 缓存命中 | 不再 `_build_live` / `list_tools`；复用 tool 与 session；更新 `last_used` |
| TTL 过期及边界 | 到期后重建；**旧连接关闭回调被 await**，不只断言建连两次；失败重建不保留过期缓存 |
| 初次发现 / 包装成功 | 真实 `ListToolsResult` 的 `Tool` 被真实 bridge 包装，成功后才入缓存 |
| `list_tools` 或包装失败 | 新连接关闭回调被 await；异常传播，失败项不缓存；非法 schema 精确 `SchemaError` |
| `invalidate(user_id)` | 关闭并清除该用户所有 client；不影响其他用户；再次失效/空缓存可安全执行 |
| 多 server 聚合与 per-user 隔离 | 输出顺序与 enabled 配置顺序一致；同 server 名的不同用户不共享 session |
| `_LiveClient.close()` | 真实 `AsyncExitStack` 注册的异步关闭回调被 await |

过期时先关闭、成功后再删缓存，避免关闭失败时丢掉管理句柄。这些用例覆盖缓存命中、TTL、失效与失败清理等本地分支；真实 SDK 的跨任务生命周期不在其列（见 [8.3](#ch-8)）。

### 8.2 测试策略：在边界处打桩

缓存单测替换 `_build_live`，但不再返回虚假 session/stack。使用 SDK autospec session、真实 `ListToolsResult` / `Tool` 和一个**无真实 transport 的 `AsyncExitStack`**，给它注册 `AsyncMock` 关闭回调。这样既遵守生产类型，也能验证清理路径。`list_tools` 也是网络边界，必须配置它的返回值。

下面是可独立放入 pytest 文件的代表性失效用例（配置在内存构造，不读 DB；项目测试环境按第 4 章提供配置）。

```python
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock, create_autospec

import pytest
from mcp import ClientSession
from mcp.types import ListToolsResult, Tool

from tokendanceai.mcp.registry import MCPRegistry, _LiveClient
from tokendanceai.models.model import MCPServerConfig


@pytest.mark.asyncio
async def test_invalidate_closes_cached_client(monkeypatch):
    session = create_autospec(ClientSession, instance=True, spec_set=True)
    session.list_tools.return_value = ListToolsResult(tools=[
        Tool(name="blog_tool", description="d", input_schema={}),
    ])
    closed = AsyncMock()
    stack = AsyncExitStack()  # 没有真实 SDK / AnyIO 上下文
    stack.push_async_callback(closed)
    live = _LiveClient(session=session, _stack=stack)
    build = AsyncMock(return_value=live)
    registry = MCPRegistry(cache_ttl=300)
    monkeypatch.setattr(registry, "_build_live", build)
    cfg = MCPServerConfig(
        user_id=1, name="blog", url="http://blog/mcp", transport="http",
        encrypted_api_token="unused-in-this-test", enabled=True,
    )

    try:
        tools = await registry.get_tools(user_id=1, server_configs=[cfg])
        again = await registry.get_tools(user_id=1, server_configs=[cfg])
        assert tools[0] is again[0]
        assert tools[0].args_schema == {}
        build.assert_awaited_once_with(cfg)
        session.list_tools.assert_awaited_once_with()
        closed.assert_not_awaited()

        await registry.invalidate(user_id=1)
        closed.assert_awaited_once_with()
        assert (1, "blog") not in registry._clients
    finally:
        await stack.aclose()
```

TTL 用例直接将缓存项 `last_used` 拨到过去，或替换 registry 模块时钟控制精确边界；不要 `sleep`。用独立的旧/新关闭回调验证顺序与次数。失败用例在 SDK mock `list_tools.side_effect` 设置异常，或返回含非法 schema 的真实 `Tool`，让真实 `wrap_mcp_tools` 失败，验证新连接关闭且未入缓存。

### 8.3 要点与验证边界

- **打桩不削弱生产类型**：`_LiveClient.session` 仍是 SDK `ClientSession`，`_stack` 仍为 `AsyncExitStack`。不要用 `SimpleNamespace(aclose=...)` 冒充栈，也不要往 registry 随意挂 `_build_calls` 之类测试字段；行为记录统一用 `AsyncMock.assert_awaited_*`。
- **清理必须有断言**：缓存删除/重建的次数证明不了资源被关闭。空栈 + 关闭回调只证明 registry 走到了本地清理分支——这是单测能证明的上限。
- **到此为止**：这些用例覆盖缓存、聚合、参数包装和本地清理，但**不触及真实 SDK 连接的跨任务生命周期**——autospec 不启动 dispatcher / transport，空栈不进入真实 AnyIO 取消域。所有者任务、并发竞争、缓存键的完整配置身份等，属于[系统设计文档 9.3 的已知限制](general-agent-textbook.md#ch-9-limits)，需真实 SDK 集成测试另行验证。

> `_build_live` 已直接调 `utils.crypto.decrypt`（不再是占位函数），但里面的 `assert token` 在 `python -O` 下会被移除。解密 / transport 路径要单独测 `_build_live`、只替换它的 `decrypt` 与 transport 边界；本章这种「整体替换 `_build_live`」的缓存用例覆盖不到它。

---

<a id="ch-9"></a>
## 第 9 章 编排层：memory 存取对称

**被测对象**：[agents/memory.py](../src/tokendanceai/agents/memory.py) 的 `load_messages` / `save_messages`——langgraph 消息列表 ↔ `Message` 表行的互转。[系统设计文档 11.3](general-agent-textbook.md) 强调它俩必须**对称**。

### 9.1 该测什么

核心是**往返对称性**：存进去再读出来，消息语义不能丢——尤其是 tool 调用链。

| 测试点 | 为什么重要 |
|---|---|
| 存 HumanMessage，读回还是 Human，content 一致 | 基本往返 |
| 存带 tool_calls 的 AIMessage，读回 tool_calls 还在 | 🔴 缺了会导致 agent 重复调用工具 |
| 存 ToolMessage，读回 tool_call_id/name 还在 | langgraph 靠 tool_call_id 配对 |
| 一整轮对话（user→ai+toolcall→tool→ai）往返后顺序和内容都对 | 端到端对称 |
| 空 content / 空 tool_calls 不炸 | 边界 |

### 9.2 这一章能顺带抓到一个 bug

注意 [memory.py:10](../src/tokendanceai/agents/memory.py#L10)：

```python
_ROLE_MAP = {"user": HumanMessage, "assitant": AIMessage, "tool": ToolMessage}
#                                   ^^^^^^^^ 拼写错误 "assitant"
```

`"assitant"` 拼错了（应是 `"assistant"`）。好在 `load_messages`/`save_messages` **当前没有用这个 `_ROLE_MAP`**（它们用的是硬编码的 `if/elif` 分支，且分支里拼写是对的 `"assistant"`），所以暂时没造成运行时错误。但这是个**定时炸弹**——哪天有人重构去用 `_ROLE_MAP`，assistant 消息就会静默丢失。测试覆盖往返对称性能在那一刻抓住它。记为 **B4**（见[附录 A](#appendix-a)）。

### 9.3 测试代码

```python
# tests/test_memory.py
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from tokendanceai.agents.memory import load_messages, save_messages
from tokendanceai.models.model import Session, User
from tokendanceai.services.auth import hash_password


async def _make_session(db) -> int:
    """建一个 user + session，返回 session_id。"""
    user = User(username="bob", email="bob@e.com", password_hash=hash_password("x" * 8))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    session = Session(user_id=user.id, title="t")
    db.add(session)
    await db.commit()
    await db.refresh(session)
    return session.id


async def test_human_message_roundtrip(db):
    sid = await _make_session(db)
    await save_messages(db, sid, [HumanMessage(content="hello world")])

    loaded = await load_messages(db, sid)
    assert len(loaded) == 1
    assert isinstance(loaded[0], HumanMessage)
    assert loaded[0].content == "hello world"


async def test_ai_message_with_tool_calls_roundtrip(db):
    """★ 关键：tool_calls 必须完整往返，否则 agent 会以为工具没跑过。"""
    sid = await _make_session(db)
    ai = AIMessage(
        content="let me check",
        tool_calls=[{"name": "search", "args": {"q": "x"}, "id": "call_1", "type": "tool_call"}],
    )
    await save_messages(db, sid, [ai])

    loaded = await load_messages(db, sid)
    assert isinstance(loaded[0], AIMessage)
    assert len(loaded[0].tool_calls) == 1
    assert loaded[0].tool_calls[0]["name"] == "search"
    assert loaded[0].tool_calls[0]["id"] == "call_1"


async def test_tool_message_roundtrip(db):
    sid = await _make_session(db)
    tool_msg = ToolMessage(content="result data", tool_call_id="call_1", name="search")
    await save_messages(db, sid, [tool_msg])

    loaded = await load_messages(db, sid)
    assert isinstance(loaded[0], ToolMessage)
    assert loaded[0].tool_call_id == "call_1"
    assert loaded[0].name == "search"


async def test_full_conversation_roundtrip(db):
    """一整轮对话的顺序和内容都要对称。"""
    sid = await _make_session(db)
    original = [
        HumanMessage(content="search for cats"),
        AIMessage(content="", tool_calls=[{"name": "search", "args": {"q": "cats"}, "id": "c1", "type": "tool_call"}]),
        ToolMessage(content="found 3 cats", tool_call_id="c1", name="search"),
        AIMessage(content="I found 3 cats"),
    ]
    await save_messages(db, sid, original)

    loaded = await load_messages(db, sid)
    assert len(loaded) == 4
    assert [type(m).__name__ for m in loaded] == [
        "HumanMessage", "AIMessage", "ToolMessage", "AIMessage",
    ]
    assert loaded[0].content == "search for cats"
    assert loaded[1].tool_calls[0]["id"] == "c1"
    assert loaded[3].content == "I found 3 cats"
```

### 9.4 要点

- **这一章必须用真 DB fixture（`db`）**：memory 的职责就是读写数据库，mock 掉 DB 就失去了测试意义。这里内存 SQLite 正好派上用场——真写入、真读取，但毫秒级。
- **对称性测试的威力**：`test_full_conversation_roundtrip` 一个用例就覆盖了 save 和 load 的协同。如果哪天有人只改了一边（比如改了 save 的 JSON 格式但忘了改 load），这个测试立刻红。这正是「可执行规格」的体现——它钉死了「存取必须对称」这条契约。
- **SystemMessage 不入库是有意设计**（见 [memory.py:119](../src/tokendanceai/agents/memory.py#L119) 注释）。可以加一个测试验证「存 SystemMessage 后读回为空」，把这个设计决策也固化成测试。

---

<a id="ch-10"></a>
## 第 10 章 编排层：runner 与 SSE（mock 外部依赖）

**被测对象**：[agents/runner.py](../src/tokendanceai/agents/runner.py) 的 `run_agent`（SSE 事件流）和四个 `_sse_*` 格式化函数。这是全项目**最难测**的一块——它串起 registry、LLM、langgraph、memory 四个依赖。

### 10.1 分两层测，难度骤降

不要试图一次测整个 `run_agent`。拆成两层：

1. **纯格式化函数（`_sse_token` 等）**：无依赖，直接进出，极好测。先把这层测透。
2. **`run_agent` 主流程**：mock 掉 `build_agent`（让它返回一个假 agent，其 `astream` 吐预设事件）、`build_chat_model`、registry。重点验证「事件转发对不对」「落库有没有发生」。

### 10.2 SSE 格式化函数测试（先做这个）

```python
# tests/test_runner.py
import json

from langchain_core.messages import ToolMessage

from tokendanceai.agents.runner import (
    _sse_done, _sse_token, _sse_tool_call, _sse_tool_result,
)


def test_sse_token_format():
    out = _sse_token("hello")
    # SSE 帧必须是 event: <type>\ndata: <json>\n\n
    assert out.startswith("event: token\n")
    assert out.endswith("\n\n")
    data = json.loads(out.split("data: ")[1].strip())
    assert data["content"] == "hello"


def test_sse_tool_call_format():
    tc = {"name": "search", "args": {"q": "x"}, "id": "c1"}
    out = _sse_tool_call(tc)
    assert out.startswith("event: tool_call\n")
    data = json.loads(out.split("data: ")[1])
    assert data["name"] == "search"
    assert data["args"] == {"q": "x"}


def test_sse_tool_result_format():
    msg = ToolMessage(content="done", tool_call_id="c1", name="search")
    out = _sse_tool_result(msg)
    assert out.startswith("event: tool_result\n")
    data = json.loads(out.split("data: ")[1])
    assert data["tool_call_id"] == "c1"
    assert data["content"] == "done"


def test_sse_done_format():
    assert _sse_done() == "event: done\ndata: {}\n\n"


def test_sse_frame_ends_with_double_newline():
    """SSE 协议要求每帧以 \\n\\n 结尾，少一个前端 EventSource 会粘包。"""
    for frame in [_sse_token("a"), _sse_tool_call({"name": "n", "args": {}, "id": "i"}), _sse_done()]:
        assert frame.endswith("\n\n")
```

### 10.3 run_agent 主流程测试（mock 掉 agent）

`run_agent` 里 `agent.astream(...)` 是核心。我们造一个假 agent，让它的 `astream` 吐出预设的 `(mode, chunk)` 序列，就能验证 runner 的转发和落库逻辑：

```python
# tests/test_runner.py（接上）
from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage, ToolMessage

import tokendanceai.agents.runner as runner_mod
from tokendanceai.agents.runner import run_agent
from tokendanceai.agents.memory import load_messages
from tokendanceai.mcp.registry import MCPRegistry
from tokendanceai.models.model import Session, User, UserLLMConfig
from tokendanceai.services.auth import hash_password
from tokendanceai.utils.crypto import encrypt


class _FakeAgent:
    """假 agent：astream 吐出预设事件序列。"""
    def __init__(self, events):
        self._events = events

    async def astream(self, input, stream_mode):
        for ev in self._events:
            yield ev


async def _user_with_config_and_session(db):
    user = User(username="carol", email="c@e.com", password_hash=hash_password("x" * 8))
    db.add(user)
    await db.flush()
    user.llm_config = UserLLMConfig(
        user_id=user.id, provider="openai", base_url="http://x",
        model="gpt-4o", encrypted_api_key=encrypt("sk-x"),
    )
    session = Session(user_id=user.id, title="t")
    db.add(session)
    await db.commit()
    await db.refresh(user)
    await db.refresh(session)
    return user, session.id


async def test_run_agent_streams_token_and_persists(db, monkeypatch):
    user, sid = await _user_with_config_and_session(db)

    # 预设 astream 事件：一个 token 增量 + 一个 updates 完整消息
    ai_msg = AIMessage(content="hello")
    events = [
        ("messages", (AIMessage(content="hel"), {"langgraph_node": "agent"})),
        ("updates", {"agent": {"messages": [ai_msg]}}),
    ]

    # mock 掉建 agent / 建模型 / 拿 tools，全部返回假的
    async def fake_build_agent(llm, tools):
        return _FakeAgent(events)
    monkeypatch.setattr(runner_mod, "build_agent", fake_build_agent)
    monkeypatch.setattr(runner_mod, "build_chat_model", lambda cfg: SimpleNamespace())

    async def fake_get_tools(self, user_id, server_configs):
        return []
    monkeypatch.setattr(MCPRegistry, "get_tools", fake_get_tools)

    registry = MCPRegistry()

    # 收集 SSE 输出
    frames = []
    async for frame in run_agent(user=user, message="hi", session_id=sid, db=db, registry=registry):
        frames.append(frame)

    # 断言①：转发了 token 增量
    assert any("event: token" in f for f in frames)
    # 断言②：以 done 结尾
    assert any("event: done" in f for f in frames)
    # 断言③：落库发生了（finally 里 save_messages）——至少存了 user 输入 + 1 条 ai
    persisted = await load_messages(db, sid)
    assert any(m.content == "hi" for m in persisted)          # 用户输入落库
    assert any(getattr(m, "content", "") == "hello" for m in persisted)  # ai 消息落库


async def test_run_agent_without_llm_config_raises(db, monkeypatch):
    """没配 LLM 必须报错，不降级。"""
    user = User(username="dave", email="d@e.com", password_hash=hash_password("x" * 8))
    db.add(user)
    await db.commit()
    await db.refresh(user)
    session = Session(user_id=user.id, title="t")
    db.add(session)
    await db.commit()
    await db.refresh(session)

    async def fake_get_tools(self, user_id, server_configs):
        return []
    monkeypatch.setattr(MCPRegistry, "get_tools", fake_get_tools)

    gen = run_agent(user=user, message="hi", session_id=session.id, db=db, registry=MCPRegistry())
    with pytest.raises(ValueError, match="hadn't set LLM config"):
        async for _ in gen:
            pass
```

### 10.4 要点

- **假 agent 的 `astream` 是关键**：它把 langgraph 的复杂行为压缩成「吐一串预设元组」。这样就能精确控制输入，验证 runner 对每种 `(mode, chunk)` 的处理。真正的 langgraph 推理不在单测范围——那是集成测试或 E2E 的事。
- **验证落库用「读回来」**：`run_agent` 的 `finally` 会 `save_messages`。测试跑完后用 `load_messages` 读回，确认消息真落库了。这比 mock `save_messages` 更可信——它验证了真实的持久化效果。
- **`test_run_agent_without_llm_config_raises` 覆盖了一个重要分支**：[runner.py:47](../src/tokendanceai/agents/runner.py#L47) 的「没配 LLM 就报错、不降级」。注意异步生成器的异常要在 `async for` 里触发，所以用 `async for _ in gen` 配合 `pytest.raises`。
- **`monkeypatch.setattr(MCPRegistry, "get_tools", ...)` 打在类上**：因为 runner 内部自己调 `registry.get_tools(...)`，我们在类级别替换它的实现。注意假函数签名要带 `self`。

> **这一章的哲学**：runner 是「胶水层」，它的价值在于「把各部件正确地粘起来」。所以测试重点不是「LLM 回答得对不对」（那测不了也不该测），而是「给定部件的输出，runner 有没有正确地转发、过滤、落库」。把每个外部部件换成可控的假货，胶水本身的逻辑就清晰可测了。

---

<a id="ch-11"></a>
## 第 11 章 接口层：router 端点（会抓到 schema bug）

**被测对象**：`router/` 下五个路由文件。这一层最接近用户，**bug 在这里直接变成 HTTP 错误**。用第 4 章的 `client` / `auth_client` fixture，走真实的请求—响应链路（但 DB 是内存库）。

严格说这是**接口集成测试**（真路由 + 真 DB + 真 schema 校验），不是纯单元测试。但它性价比极高：一个请求就覆盖了「路由 + 依赖注入 + schema 序列化 + ORM」整条链，而且**专抓 schema 字段错配这类单元测试照不到的问题**。

### 11.1 该测什么

每个端点至少测三类：

| 类别 | 例子 |
|---|---|
| **正常路径** | 合法输入 → 2xx + 正确响应体 |
| **鉴权** | 不带 token → 401；带别人的资源 id → 404（归属校验） |
| **校验/错误** | 非法输入 → 422；重复用户名 → 400；资源不存在 → 404 |

### 11.2 user 路由测试（注册 + 登录，不需要 mock）

```python
# tests/router/test_user.py


async def test_create_user_success(client):
    resp = await client.post("/api/user", json={
        "username": "newuser", "email": "new@example.com", "password": "password123",
    })
    assert resp.status_code == 201
    body = resp.json()
    assert body["username"] == "newuser"
    assert "password" not in body          # 响应绝不能含密码
    assert "password_hash" not in body


async def test_create_user_duplicate_username(client):
    payload = {"username": "dup", "email": "a@e.com", "password": "password123"}
    await client.post("/api/user", json=payload)
    resp = await client.post("/api/user", json={**payload, "email": "b@e.com"})
    assert resp.status_code == 400
    assert "already exists" in resp.json()["detail"]


async def test_create_user_short_password_422(client):
    """密码 < 8 位被 schema 挡下。"""
    resp = await client.post("/api/user", json={
        "username": "u", "email": "u@e.com", "password": "short",
    })
    assert resp.status_code == 422


async def test_login_success_returns_token(client):
    await client.post("/api/user", json={
        "username": "loginuser", "email": "login@e.com", "password": "password123",
    })
    # OAuth2PasswordRequestForm 用 form 编码，username 字段填 email
    resp = await client.post("/api/user/login", data={
        "username": "login@e.com", "password": "password123",
    })
    assert resp.status_code == 200
    assert resp.json()["token_type"] == "bearer"
    assert resp.json()["access_token"]


async def test_login_wrong_password_401(client):
    await client.post("/api/user", json={
        "username": "u2", "email": "u2@e.com", "password": "password123",
    })
    resp = await client.post("/api/user/login", data={
        "username": "u2@e.com", "password": "wrongpass",
    })
    assert resp.status_code == 401
```

### 11.3 sessions 路由测试（★ 会抓到 B1）

```python
# tests/router/test_sessions.py
from tokendanceai.models.model import Session


async def test_list_sessions_requires_auth(client):
    """不带 token 必须 401。用未登录的 client（不覆盖 get_current_user）。"""
    resp = await client.get("/api/sessions")
    assert resp.status_code == 401


async def test_list_sessions_returns_user_sessions(auth_client, db, test_user):
    """
    ★ 这个测试会抓到 B1（schemas/session.py:11 的 create_at 字段名错配）。
    当前代码下 SessionPublic 要 'create_at' 但 Session 模型是 'created_at'，
    model_validate 会因缺属性在响应序列化时 500。
    修复 schema 为 created_at 后此测试转绿。
    """
    db.add(Session(user_id=test_user.id, title="my session"))
    await db.commit()

    resp = await auth_client.get("/api/sessions")
    assert resp.status_code == 200          # ← 现在会是 500，修复 B1 后才 200
    assert len(resp.json()) == 1
    assert resp.json()[0]["title"] == "my session"


async def test_delete_others_session_404(auth_client, db):
    """归属校验：删别人的 session 应 404，不能删成功。"""
    from tokendanceai.models.model import User
    from tokendanceai.services.auth import hash_password

    other = User(username="other", email="other@e.com", password_hash=hash_password("x" * 8))
    db.add(other)
    await db.commit()
    await db.refresh(other)
    other_session = Session(user_id=other.id, title="not yours")
    db.add(other_session)
    await db.commit()
    await db.refresh(other_session)

    resp = await auth_client.delete(f"/api/sessions/{other_session.id}")
    assert resp.status_code == 404
```

### 11.4 agent 路由测试（★ 会抓到 B3）

```python
# tests/router/test_agent.py
import pytest


@pytest.mark.xfail(reason="已知 bug B3: AgentRunRequest.autopoilt 必填且拼错，前端不传会 422", strict=False)
async def test_agent_run_without_autopilot_field(auth_client, monkeypatch):
    """
    ★ 前端按常理只会发 {message, session_id}，不会发 'autopoilt'。
    但 schemas/agent.py:7 把 autopoilt 定为必填 bool，导致请求 422。
    修复：把 autopoilt 改为可选并改正拼写（或直接删掉，因为路由根本没用它）。
    """
    # mock 掉 run_agent，避免真跑 LLM
    import tokendanceai.router.agent as agent_router

    async def fake_run_agent(**kwargs):
        yield "event: done\ndata: {}\n\n"
    monkeypatch.setattr(agent_router, "run_agent", fake_run_agent)

    resp = await auth_client.post("/api/agent/run", json={"message": "hello"})
    assert resp.status_code == 200          # 现在是 422（缺 autopoilt）


async def test_agent_run_requires_auth(client):
    resp = await client.post("/api/agent/run", json={"message": "hi", "autopoilt": False})
    assert resp.status_code == 401
```

### 11.5 servers 路由测试（CRUD + 失效 + ★ 顺带发现 B5）

```python
# tests/router/test_servers.py


async def test_create_server_encrypts_token(auth_client, db, test_user):
    resp = await auth_client.post("/api/servers", json={
        "name": "blog", "url": "http://blog/mcp", "transport": "http", "api_token": "secret-token",
    })
    assert resp.status_code == 201
    # 响应里绝不能出现明文 token
    assert "secret-token" not in resp.text

    # DB 里存的是密文，不是明文
    from sqlalchemy import select
    from tokendanceai.models.model import MCPServerConfig
    row = (await db.execute(select(MCPServerConfig))).scalars().first()
    assert row.encrypted_api_token != "secret-token"


async def test_create_server_invalid_transport_422(auth_client):
    """transport 只允许 http|sse（schema pattern）。"""
    resp = await auth_client.post("/api/servers", json={
        "name": "x", "url": "http://x", "transport": "grpc", "api_token": "t",
    })
    assert resp.status_code == 422


async def test_update_others_server_404(auth_client, db):
    from tokendanceai.models.model import MCPServerConfig, User
    from tokendanceai.services.auth import hash_password

    other = User(username="o2", email="o2@e.com", password_hash=hash_password("x" * 8))
    db.add(other)
    await db.commit()
    await db.refresh(other)
    srv = MCPServerConfig(
        user_id=other.id, name="s", url="http://s", transport="http", encrypted_api_token="x",
    )
    db.add(srv)
    await db.commit()
    await db.refresh(srv)

    resp = await auth_client.patch(f"/api/servers/{srv.id}", json={"enabled": False, "url": "http://s", "transport": "http"})
    assert resp.status_code == 404
```

> **★ 顺带发现 B5**：注意上面 `test_update_others_server_404` 的 PATCH body 不得不带上 `url` 和 `transport`——因为 [schemas/server.py:17-18](../src/tokendanceai/schemas/server.py#L17-L18) 的 `ServerUpdate.url` / `transport` 被定义成**必填**（`Field(..., ...)`）。但这是「部分更新」接口，理应全部可选。现在前端想只改 `enabled` 都得带上 url 和 transport，否则 422。这不是测试本身的问题，而是测试**暴露了 schema 设计缺陷**（见[附录 A](#appendix-a) 的 B5）。

### 11.6 account 路由测试（upsert 语义）

```python
# tests/router/test_account.py


async def test_get_account_not_set_404(auth_client):
    """还没配 LLM 时 GET 应 404。"""
    resp = await auth_client.get("/api/account")
    assert resp.status_code == 404


async def test_upsert_account_creates_then_updates(auth_client):
    # 第一次 POST：创建
    resp1 = await auth_client.post("/api/account", json={
        "provider": "openai", "base_url": "http://x/v1", "model": "gpt-4o", "api_key": "sk-1",
    })
    assert resp1.status_code == 200
    assert resp1.json()["provider"] == "openai"
    assert "sk-1" not in resp1.text         # 明文 key 不回显

    # 第二次 POST：更新（upsert，不是再建一个）
    resp2 = await auth_client.post("/api/account", json={
        "provider": "anthropic", "base_url": "http://y", "model": "claude-x", "api_key": "sk-2",
    })
    assert resp2.status_code == 200
    assert resp2.json()["provider"] == "anthropic"
    assert resp2.json()["id"] == resp1.json()["id"]   # 同一条记录，id 不变
```

### 11.7 要点

- **归属校验是接口层的安全核心**：每个「按 id 操作资源」的端点（delete session、update server…）都要有一个「操作别人的资源 → 404」的测试。这是防止「水平越权」的红线，手动测试极易遗漏。
- **凭据脱敏要断言**：`assert "secret-token" not in resp.text`——确保明文 token/key 不出现在任何响应里。这把[系统设计文档 G5](general-agent-textbook.md)（凭据安全）变成了可执行的检查。
- **`xfail` 标记现存 bug**：B1、B3 对应的测试用 `xfail` 标注，既记录了问题，又不会让测试套件「假红」。修好后去掉标记，它们就成了正式回归测试。
- **form vs json**：登录接口用 `OAuth2PasswordRequestForm`，必须用 `data=`（form 编码）而非 `json=`，且字段名是 `username`（填 email）。这是容易踩的小坑。

---

<a id="part-4"></a>
# 第 4 部分 落地

<a id="ch-12"></a>
## 第 12 章 怎么跑、覆盖率、CI

### 12.1 跑测试

```bash
# 跑全部
uv run pytest

# 跑 MCP 契约与缓存回归（不连真实 MCP server）
uv run pytest tests/test_bridge.py tests/test_registry.py

# 跑单个文件
uv run pytest tests/test_crypto.py

# 跑单个用例
uv run pytest tests/test_auth.py::test_expired_token_returns_none

# 看到 print 输出（调试用）
uv run pytest -s

# 失败就停，别跑完所有
uv run pytest -x

# 只重跑上次失败的
uv run pytest --lf
```

### 12.2 覆盖率

覆盖率告诉你「哪些代码行被测试执行过」。先装：

```bash
uv add --dev pytest-cov
```

跑带覆盖率的测试：

```bash
# 终端报告，列出每个文件的覆盖率和未覆盖的行号
uv run pytest --cov=tokendanceai --cov-report=term-missing

# 生成 HTML 报告，浏览器打开 htmlcov/index.html 可视化看
uv run pytest --cov=tokendanceai --cov-report=html
```

**怎么看覆盖率**——它是「反向指标」：

- **低覆盖是明确的警告**：某个有分支的文件覆盖率 30%，说明大片逻辑没测，risky。
- **高覆盖不等于没 bug**：100% 覆盖只说明「每行都跑过」，不代表「每种情况都验证过」。覆盖率是「必要不充分」。
- **别追求 100%**：把精力放在🔴🟠优先级的模块上。`main.py`、纯 schema 声明这类覆盖不覆盖无所谓。

> **本项目的合理目标**：`utils/`、`services/`、`mcp/`、`agents/` 这四个核心包做到 **80%+**；`router/` 靠接口测试覆盖主要分支即可；`config.py`/`main.py` 不设要求。

### 12.3 CI 集成（GitHub Actions 示例）

让测试在每次 push / PR 时自动跑，挡住「带病合并」。在 `.github/workflows/test.yml`：

```yaml
name: tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Install uv
        uses: astral-sh/setup-uv@v5
      - name: Set up Python
        run: uv python install 3.12
      - name: Install deps
        run: uv sync --all-extras --dev
      - name: Run tests
        run: uv run pytest --cov=tokendanceai --cov-report=term-missing
```

**关键**：测试不读 `.env`（conftest 已用环境变量注入测试配置），所以 CI 上**不需要**配任何 secret 就能跑。这是第 4.2 节那套「import 前注入环境变量」设计带来的红利。

---

<a id="ch-13"></a>
## 第 13 章 落地顺序

不要想着一次写完所有测试。按「地基 → 纯逻辑 → 带依赖 → 接口」的顺序推进，每步都能独立跑绿，有正反馈：

| 阶段 | 做什么 | 产出 | 为什么这个顺序 |
|---|---|---|---|
| **0 环境** | 装 pytest 等；写 `conftest.py`；跑通 `test_smoke.py` | 地基能用 | 环境不通，后面全白搭（第 3–4 章） |
| **1 纯函数** | `test_crypto.py`、`test_auth.py`（JWT 部分） | 最快见绿，建立信心 | 无 async DB、无 mock，最简单（第 5–6 章） |
| **2 带 DB** | `test_memory.py`、`test_auth.py`（get_current_user） | 验证 DB fixture 好使 | 引入 `db` fixture，但不用 mock（第 6/9 章） |
| **3 带 mock** | `test_bridge.py`、`test_registry.py`、`test_llm_services.py` | 原 schema/错误边界、缓存与清理回归 | SDK autospec + 真实消息 + AsyncExitStack 关闭回调（第 7/8 章） |
| **4 最难** | `test_runner.py` | 覆盖核心编排 | 依赖最多，前面铺垫够了再上（第 10 章） |
| **5 接口** | `tests/router/*.py` | 端到端挡住 schema bug | 用前面所有 fixture，收口（第 11 章） |
| **6 收尾** | 接 CI、看覆盖率、修 xfail 标的 bug | 持续防线 | 让测试长期有效（第 12 章） |

**修 bug 的时机**：B2 已修复，是「原始 schema + 显式校验」的正式回归，没有旧函数、没有 xfail。B1/B3/B5 的测试已写好，修一个、转绿一个、去掉对应的 xfail 标记即可。

> **最小可行目标**：如果时间紧，先确认**阶段 0 + 1**，守住已落地的 **MCP 阶段 3 契约回归**，再做**阶段 5**。阶段 0 搭好地基，阶段 1 覆盖安全敏感的加密和 JWT，阶段 5 用接口测试一次性挡住 schema 错配（B1/B3/B5）这类「整个接口不可用」的严重问题。这三步投入最小、挡掉的风险最大。

---

<a id="appendix"></a>
# 附录

<a id="appendix-a"></a>
## A. 测试能暴露的真实 bug 清单

下面是本文档涉及的 bug 与回归清单。B2 已修复并转为正式回归，其余为待修项（测试已写好，修复后转绿）：

| # | 位置 | 问题 | 后果 | 抓它的测试 | 修复 |
|---|---|---|---|---|---|
| **B1** | [schemas/session.py:11](../src/tokendanceai/schemas/session.py#L11) | `SessionPublic.create_at` 字段名拼错（模型是 `created_at`） | `GET /api/sessions` 返回 500，会话列表完全不可用 | `test_list_sessions_returns_user_sessions`（11.3） | 改 `create_at` → `created_at` |
| **B2（历史已修复）** | [bridge.py](../src/tokendanceai/mcp/bridge.py) | 旧参数模型转换的默认值使必填失效，并有复杂 schema 丢约束风险 | 现由本地 JSON Schema 校验拒绝缺失参数，不远调 | `test_required_field_rejected_before_remote_call`（[真实 tests](../tests/test_bridge.py)） | 删除有损转换，原始 schema 深拷贝 + 显式校验；正式回归，无 xfail |
| **B3** | [schemas/agent.py:7](../src/tokendanceai/schemas/agent.py#L7) | `autopoilt: bool` 必填且拼错，路由未使用 | 前端按常理不传 → `POST /api/agent/run` 422 | `test_agent_run_without_autopilot_field`（11.4） | 改为可选并改正拼写，或删除该字段 |
| **B4** | [memory.py:10](../src/tokendanceai/agents/memory.py#L10) | `_ROLE_MAP` 里 `"assitant"` 拼错 | 当前未使用，暂无影响；一旦有人改用它，assistant 消息静默丢失 | `test_full_conversation_roundtrip`（9.3，若重构后触发） | 改 `"assitant"` → `"assistant"`，或删掉未用的 `_ROLE_MAP` |
| **B5** | [schemas/server.py:17-18](../src/tokendanceai/schemas/server.py#L17) | `ServerUpdate.url`/`transport` 被设为必填，但这是部分更新接口 | 前端想只改 `enabled` 也必须带 url+transport，否则 422 | `test_update_others_server_404`（11.5，构造请求时暴露） | 两个字段改为 `Field(default=None, ...)` |

> **额外观察（非 bug，但值得测试固化）**：
> - [schemas/session.py:21](../src/tokendanceai/schemas/session.py#L21) 的 `MessagePublic.content: str` 是必填非空，但 `Message.content` 模型层可空（Text 未标 nullable=False）。若某条 message 的 content 为 NULL，`GET /{id}/messages` 会 500。可加一个「content 为空的 message 能否正常返回」的测试探一下。
> - [auth.py:100](../src/tokendanceai/services/auth.py#L100) 的 `get_current_admin_user` 读 `current_user.is_admin`，该字段模型里有（[model.py:29](../src/tokendanceai/models/model.py#L29)），没问题；但目前没有任何路由用到 admin 校验，属于「写了没接」。

**这张表展示测试的双重价值**：发现问题，并在修复后持续守护（B2 已是正式回归）。B1/B3/B5 则演示测试如何在修复前就把问题钉死、给出可复现的红灯。

<a id="appendix-b"></a>
## B. 常用 mock 配方

速查：遇到某类依赖，用哪种 mock 手法。

### B.1 替换模块级函数 —— `monkeypatch.setattr`

```python
def test_xxx(monkeypatch):
    import tokendanceai.services.llm_service as svc
    monkeypatch.setattr(svc, "decrypt", lambda c: "fake-plaintext")
    # 现在 svc 里对 decrypt 的调用都返回 "fake-plaintext"
```

用于：替换某模块内部调用的函数（解密、建连等）。**注意打在「使用处的模块」**，不是定义处——`llm_service.py` 里 `from ...crypto import decrypt`，就 patch `svc.decrypt`。

### B.2 MCP SDK 替身 —— autospec + 真实消息 + 真实关闭栈

```python
from contextlib import AsyncExitStack
from unittest.mock import AsyncMock, create_autospec

from mcp import ClientSession
from mcp.types import CallToolResult, ListToolsResult, TextContent, Tool

session = create_autospec(ClientSession, instance=True, spec_set=True)
session.call_tool.return_value = CallToolResult(
    content=[TextContent(type="text", text="ok")]
)
session.list_tools.return_value = ListToolsResult(tools=[
    Tool(name="demo", input_schema={}),
])
closed = AsyncMock()
stack = AsyncExitStack()  # 无真实 transport / AnyIO 上下文
stack.push_async_callback(closed)
# await stack.aclose() 后：closed.assert_awaited_once_with()
# 拒绝参数后：session.call_tool.assert_not_awaited()
```

用于：MCP 网络边界替身，生产仍是 `ClientSession`，消息仍是真实 SDK 类型。不要用鸭子类型 session、`SimpleNamespace` 消息或伪造的 `aclose` 对象。空栈的关闭回调只验证本地清理，不覆盖真实 SDK 的跨任务生命周期（见[系统设计文档 9.3](general-agent-textbook.md#ch-9-limits)）；完整例子见第 7–8 章。

### B.3 假的异步生成器 —— `async def` + `yield`

```python
class _FakeAgent:
    async def astream(self, input, stream_mode):
        for ev in [("messages", (...)), ("updates", {...})]:
            yield ev
```

用于：顶替 `agent.astream` 这类流式接口，精确控制吐出的事件序列。

### B.4 覆盖 FastAPI 依赖 —— `app.dependency_overrides`

```python
app.dependency_overrides[get_db] = _override_get_db
app.dependency_overrides[get_current_user] = lambda: test_user
# 测试结束记得 app.dependency_overrides.clear()
```

用于：接口测试里把 `get_db`/`get_current_user`/`get_registry` 换成测试替身。这是 FastAPI 官方推荐的测试注入点。

### B.5 SQLite 开外键级联（需要时）

```python
from sqlalchemy import event

@event.listens_for(engine.sync_engine, "connect")
def _fk_on(dbapi_conn, _):
    dbapi_conn.execute("PRAGMA foreign_keys=ON")
```

用于：要测「删 session 级联删 message」时，让 SQLite 强制外键（默认不强制）。

<a id="appendix-c"></a>
## C. 测试命名与组织规范

**文件名**：`test_<被测模块>.py`，镜像 `src/` 结构。

**函数名**：`test_<被测对象>_<场景>_<预期>`，光看名字就知道测什么：

```
test_verify_access_token_expired_returns_none
     └─被测对象──────┘ └─场景─┘ └──预期───┘

test_create_user_duplicate_username_400
test_get_tools_disabled_server_skipped
```

**一个测试只验一件事**：一个 `test_` 函数聚焦一个行为。别在一个函数里又测创建又测删除——失败时分不清是谁坏了。

**AAA 分段**：每个测试用空行分出 Arrange / Act / Assert 三段（见 [1.3](#ch-1)），读起来像小故事。

**断言要具体**：`assert resp.status_code == 404` 好过 `assert resp.status_code != 200`；`assert body["username"] == "alice"` 好过 `assert body`。

<a id="appendix-d"></a>
## D. 术语表

| 术语 | 含义 |
|---|---|
| **单元测试** | 隔离地验证最小代码单元（通常一个函数）行为的测试 |
| **集成测试** | 多个真实组件一起测，验证它们的「接缝」 |
| **fixture** | pytest 的测试夹具：测试前准备环境、测试后清理的可复用函数 |
| **mock / 替身** | 顶替真实依赖（网络、DB、第三方）的假对象，让测试快而稳 |
| **monkeypatch** | pytest 内置工具，运行时替换函数/属性 |
| **AAA** | Arrange-Act-Assert，单个测试的三段式结构 |
| **FIRST** | Fast/Isolated/Repeatable/Self-validating/Timely，好测试的五个特征 |
| **覆盖率（coverage）** | 被测试执行过的代码行占比，反向指标 |
| **xfail** | pytest 标记「预期失败」的测试（如已知未修的 bug）；`strict=True` 时意外通过会报错 |
| **参数化（parametrize）** | 用一个测试函数跑多组输入，避免重复 |
| **StaticPool** | SQLAlchemy 连接池策略，让内存 SQLite 全程复用单连接（内存库必配） |
| **ASGITransport** | httpx 的传输层，直接把请求打进 FastAPI app，不起真服务器 |
| **归属校验** | 验证「资源属于当前用户」的检查，防水平越权 |

---

> **结语**：测试的本质，是把「这段代码应该怎么工作」从你的脑子里、从口头约定里，搬到一个机器能自动、重复、永久执行的地方。这个后端底子不错，但「写完」和「写对且不会被改坏」之间，隔的正是这份文档描述的测试。按顺序推进——先守住加密、JWT 这些安全基础和 MCP 契约回归，再补接口测试，逐层织起回归网。记住单测的边界：它守护逻辑与契约，不替代真实 SDK 的集成验证（见第 8 章）。
