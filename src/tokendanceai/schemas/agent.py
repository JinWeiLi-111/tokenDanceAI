from pydantic import BaseModel, Field


class AgentRunRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=10000)
    session_id: int | None = None  # if session_id is None, create a new session
    autopoilt: bool
