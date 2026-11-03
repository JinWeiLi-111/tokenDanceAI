from datetime import timedelta

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession
from starlette.status import HTTP_401_UNAUTHORIZED, HTTP_404_NOT_FOUND

from tokendanceai.models.model import User
from tokendanceai.services.auth import (
    create_access_token,
    get_current_admin_user,
    get_current_user,
    hash_password,
    verify_access_token,
    verify_password,
)


def test_hash_password_is_not_plaintext():
    assert hash_password("seceret123") != "seceret123"


def test_verify_password_correct_and_wrong():
    assert verify_password("seceret123", hash_password("seceret123"))


def test_create_and_verify_token_roundtrip():
    token = create_access_token(data={"sub": "42"})
    assert verify_access_token(token) == "42"


def test_expired_token_returns_none():
    token = create_access_token(data={"sub": "42"}, expires_delta=timedelta(seconds=-1))
    assert verify_access_token(token) is None


def test_tampered_token_returns_none():
    token = create_access_token(data={"sub": "42"})
    assert verify_access_token(token + "2") is None


def test_token_without_sub_returns_none():
    token = create_access_token(data={"sub1": "42"})
    assert verify_access_token(token) is None


async def test_get_current_user_invalid_token_raise_401(db: AsyncSession):
    token = "invalid_token"

    with pytest.raises(HTTPException) as e:
        await get_current_user(token, db)

    assert e.value.status_code == HTTP_401_UNAUTHORIZED


async def test_get_current_user_nonexistent_user_raises_404(db: AsyncSession):
    token = create_access_token({"sub": "42"})

    with pytest.raises(HTTPException) as e:
        await get_current_user(token, db)

    assert e.value.status_code == HTTP_404_NOT_FOUND


async def test_get_current_user_return_user(db: AsyncSession, test_user: User):
    token = create_access_token({"sub": str(test_user.id)})
    user = await get_current_user(token, db)

    assert user == test_user


async def test_get_current_admin_return_admin(
    db: AsyncSession, test_user: User, test_admin_user: User
):
    user = test_user
    admin = test_admin_user

    with pytest.raises(HTTPException) as e:
        await get_current_admin_user(user)
    assert e.value.status_code == HTTP_401_UNAUTHORIZED

    admin_user = await get_current_admin_user(admin)
    assert admin_user == admin
