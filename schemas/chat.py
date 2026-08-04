from pydantic import BaseModel


class RouteRequest(BaseModel):
    message: str
