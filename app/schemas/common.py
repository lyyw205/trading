from pydantic import BaseModel


class MessageResponse(BaseModel):
    status: str
    message: str | None = None
