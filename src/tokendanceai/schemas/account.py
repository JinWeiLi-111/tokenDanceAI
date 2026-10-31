from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class AccountUpsert(BaseModel):
    provider: str = Field(..., pattern="^(openai|anthropic)$")
    base_url: str
    model: str
    api_key: str


class AccountPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    provider: str
    base_url: str
    model: str
    created_at: datetime
    updated_at: datetime
