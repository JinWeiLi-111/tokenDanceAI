import os
import secrets
from collections.abc import AsyncGenerator

from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient

os.environ["TokenDanceDatabase_url"] = "sqlite+aiosqlite:///:memory:"
# JWT HS256 签名密钥
os.environ["TokenDanceSecret_Key"] = secrets.token_urlsafe(32)
# Fernet 对称加密密钥：必须用 Fernet.generate_key()，格式有严格要求。
os.environ["TokenDanceAI_Encryption_Key"] = Fernet.generate_key().decode()

import pytest_asyncio
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.pool import StaticPool

from tokendanceai.db.base import Base
from tokendanceai.db.db_session import get_db
from tokendanceai.main import app
from tokendanceai.mcp.registry import MCPRegistry, get_registry
from tokendanceai.models.model import User
from tokendanceai.services.auth import get_current_user


@pytest_asyncio.fixture
async def async_engine():
    """内存 SQLite 引擎。Static Pool + 单连接，保证 :memory: 整个测试内可见"""
    eng = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest_asyncio.fixture
async def db(async_engine: AsyncEngine) -> AsyncGenerator[AsyncSession]:
    """一个绑定到测试引擎的 session，直接喂给需要 db 的单元测试"""
    session_maker = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    async with session_maker() as session:
        yield session


@pytest_asyncio.fixture
async def client(async_engine) -> AsyncGenerator[AsyncClient]:
    """
    返回一个 httpx AsyncClient, 直接打到 FastAPI app
    """
    session_maker = async_sessionmaker(
        async_engine, class_=AsyncSession, expire_on_commit=False
    )

    async def _override_get_db():
        async with session_maker() as session:
            yield session

    app.dependency_overrides[get_db] = _override_get_db
    # ASGITransport don't trigger lifespan, _registry always is None
    # we can initialize a MCPRegistry instance
    app.dependency_overrides[get_registry] = lambda: MCPRegistry(cache_ttl=300)

    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c

    # clear overrides, avoid infecting other tests
    app.dependency_overrides.clear()


# ------------------------------------------------
# data fixture: prebuilt a test user and auth_client
# ------------------------------------------------
@pytest_asyncio.fixture
async def test_user(db: AsyncSession) -> User:
    """往测试库插入一个用户，供需要“已有用户”的测试复用"""
    from tokendanceai.services.auth import hash_password

    user = User(
        username="Leon",
        email="xkang744@gmail.com",
        password_hash=hash_password("xk123456"),
    )

    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def test_admin_user(db: AsyncSession) -> User:
    """往测试库插入一个管理员，供需要“已有管理员”的测试复用"""
    from tokendanceai.services.auth import hash_password

    user = User(
        username="Alice",
        email="test_admin@gmail.com",
        password_hash=hash_password("xk123456"),
        is_admin=True,
    )

    db.add(user)
    await db.commit()
    await db.refresh(user)
    return user


@pytest_asyncio.fixture
async def auth_client(
    client: AsyncClient, test_user: User
) -> AsyncGenerator[AsyncClient]:
    """
    已登录的客户端：直接覆盖 get_current_user 返回 test_user，跳过 JWT 流程
    """
    app.dependency_overrides[get_current_user] = lambda: test_user

    yield client
