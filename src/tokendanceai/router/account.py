from typing import Annotated

from fastapi import APIRouter, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tokendanceai.db.db_session import get_db
from tokendanceai.models.model import UserLLMConfig
from tokendanceai.schemas.account import AccountPublic, AccountUpsert
from tokendanceai.services.auth import CurrentUser
from tokendanceai.utils.crypto import encrypt

router = APIRouter()


@router.get("", response_model=AccountPublic)
async def get_account(
    current_user: CurrentUser,
    db: Annotated[AsyncSession, get_db],
):
    """get the llm config of current_user"""
    llm_config: UserLLMConfig | None = (
        (
            await db.execute(
                select(UserLLMConfig).where(UserLLMConfig.user_id == current_user.id)
            )
        )
        .scalars()
        .first()
    )

    if llm_config is None or llm_config.user_id != current_user.id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="server not found"
        )

    return AccountPublic.model_validate(llm_config)


@router.post("", response_model=AccountPublic)
async def upsert_account(
    data: AccountUpsert,
    current_user: CurrentUser,
    db: Annotated[AsyncSession, get_db],
):
    encrypt_api_key = encrypt(data.api_key)
    llm_config: UserLLMConfig | None = current_user.llm_config

    if llm_config is None:
        # 新建 LLM Config
        llm_config = UserLLMConfig(
            user_id=current_user.id,
            provider=data.provider,
            base_url=data.base_url,
            model=data.model,
            encrypt_api_key=encrypt_api_key,
        )
    else:
        llm_config.provider = data.provider
        llm_config.base_url = data.base_url
        llm_config.model = data.model
        llm_config.encrypted_api_key = encrypt_api_key

    db.add(llm_config)
    await db.commit()
    await db.refresh(llm_config)

    return AccountPublic.model_validate(llm_config)
