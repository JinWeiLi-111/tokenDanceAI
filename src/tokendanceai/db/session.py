from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from tokendanceai.config import settings

SQLALCHEMY_DATABASE_URL = settings.database_url

async_engine = create_async_engine(url=SQLALCHEMY_DATABASE_URL)

AsyncSessionLocal = async_sessionmaker(
    async_engine, class_=AsyncSession, expire_on_commit=False
)


async def get_db():
    async with AsyncSessionLocal() as session:
        yield session
