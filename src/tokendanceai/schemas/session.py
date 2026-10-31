from datetime import datetime

from pydantic import BaseModel, ConfigDict


class SessionPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    user_id: int
    title: str
    create_at: datetime
    updated_at: datetime


class MessagePublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    session_id: int
    role: str
    content: str
    created_at: datetime
