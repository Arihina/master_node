from pydantic import BaseModel


class RouteRequest(BaseModel):
    message: str


class CreateSessionRequest(BaseModel):
    title: str | None = None


class RenameSessionRequest(BaseModel):
    title: str


class ChatRequest(BaseModel):
    message: str


class FeedbackRequest(BaseModel):
    vote: int | None = None
    comment: str | None = None


class SmartChatRequest(BaseModel):
    message: str
    agent_id: str | None = None
    session_id: int | None = None
