from pydantic import BaseModel


class UserResponse(BaseModel):
    id: str
    email: str
    role: str = "user"


class LoginUrlResponse(BaseModel):
    url: str
