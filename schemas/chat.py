from pydantic import BaseModel


class RouteRequest(BaseModel):
    message: str


class RouteResponse(BaseModel):
    agent: str


class CreateSessionRequest(BaseModel):
    title: str | None = None


class ChatRequest(BaseModel):
    message: str


class SmartChatRequest(BaseModel):
    message: str
    agent_id: str | None = None
    session_id: int | None = None
