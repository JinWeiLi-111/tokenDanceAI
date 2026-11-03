from sqlalchemy.orm import DeclarativeBase


class Base(DeclarativeBase):
    pass


# 导入所有 model，使其注册到 Base.metadata，供 alembic autogenerate 比对。
import tokendanceai.models.model  # noqa: F401,E402
