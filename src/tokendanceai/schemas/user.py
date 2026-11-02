from pydantic import BaseModel, ConfigDict, EmailStr, Field


class UserBase(BaseModel):
    username: str = Field(..., min_length=1, max_length=50)
    email: EmailStr = Field(..., max_length=120)


class UserCreate(UserBase):
    password: str = Field(..., min_length=8, max_length=50)


class UserUpdate(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=50)
    email: EmailStr | None = Field(default=None, max_length=120)


class UserPublic(UserBase):
    model_config = ConfigDict(from_attributes=True)

    id: int
    username: str = Field(..., min_length=1, max_length=50)
    image_file: str | None = None
    # User 模型当前没有 image_path 列；留默认值，避免响应序列化时缺失属性报错
    image_path: str | None = None


class UserPrivate(UserPublic):
    email: EmailStr = Field(..., max_length=120)


class Token(BaseModel):
    access_token: str
    token_type: str
