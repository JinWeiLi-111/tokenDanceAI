"""add admin column

Revision ID: a25dbf106809
Revises:
Create Date: 2026-11-02 17:13:10.859637

"""

from typing import Sequence, Union

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "a25dbf106809"
down_revision: Union[str, Sequence[str], None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """为 users 表添加 is_admin 列。

    注意：其余表（user_llm_configs / mcp_server_configs / session / messages）
    在此迁移之前已通过 Base.metadata.create_all() 创建，本迁移仅追加缺失列。
    """
    op.add_column(
        "users",
        sa.Column(
            "is_admin",
            sa.Boolean(),
            nullable=False,
            server_default=sa.text("false"),
        ),
    )


def downgrade() -> None:
    op.drop_column("users", "is_admin")
