from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tokendanceai.db.base import Base
from tokendanceai.db.session import get_db


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    username: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    email: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(200), nullable=False)
    image_file: Mapped[str | None] = mapped_column(
        String(200), nullable=True, default=None
    )


class UserLLMConfig(Base):
    __tablename__ = "user_llm_configs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, index=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey(User.id), unique=True, nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(50), nullable=False)
    base_url: Mapped[str] = mapped_column(String(255), nullable=False)
