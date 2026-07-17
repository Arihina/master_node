from pydantic import BaseModel


class RouteRequest(BaseModel):
    message: str


class CreateSessionRequest(BaseModel):
    title: str | None = None


class RenameSessionRequest(BaseModel):
    title: str


class FeedbackRequest(BaseModel):
    vote: int | None = None
    comment: str | None = None
