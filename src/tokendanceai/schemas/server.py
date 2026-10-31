from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ServerCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=50)
    url: str = Field(..., max_length=255)
    transport: str = Field(..., pattern="^(http|sse)$")
    api_token: str = Field(
        ..., min_length=1
    )  # # Explicitly transmit in plaintext; encrypt before entering the database.


class ServerUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=50)
    url: str = Field(..., max_length=255)
    transport: str | None = Field(..., pattern="^(http|sse)$")
    api_token: str | None = Field(default=None)
    enabled: bool | None = None


class ServerPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    user_id: int
    name: str
    url: str
    transport: str
    enabled: bool
    created_at: datetime


class ToolSummary(BaseModel):
    name: str
    description: str | None


class ServerTestResult(BaseModel):
    ok: bool
    tools: list[ToolSummary] = []
    error: str | None = None
