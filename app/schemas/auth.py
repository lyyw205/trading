from __future__ import annotations

import re

from pydantic import BaseModel, field_validator

_EMAIL_RE = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")


def _validate_password(v: str) -> str:
    if len(v) < 8:
        raise ValueError("비밀번호는 최소 8자 이상이어야 합니다.")
    return v


def _validate_email(v: str) -> str:
    if not _EMAIL_RE.match(v):
        raise ValueError("유효한 이메일 형식이 아닙니다.")
    return v.lower().strip()


class UserResponse(BaseModel):
    id: str
    email: str
    role: str = "user"


class LoginRequest(BaseModel):
    email: str
    password: str

    _check_email = field_validator("email")(_validate_email)
    _check_password = field_validator("password")(_validate_password)


class LoginResponse(BaseModel):
    success: bool
    user: UserResponse | None = None


class CreateUserRequest(BaseModel):
    email: str
    password: str
    role: str = "user"

    _check_email = field_validator("email")(_validate_email)
    _check_password = field_validator("password")(_validate_password)

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("admin", "user"):
            raise ValueError("Role must be 'admin' or 'user'")
        return v


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str

    _check_new_password = field_validator("new_password")(_validate_password)


class ResetPasswordRequest(BaseModel):
    new_password: str

    _check_password = field_validator("new_password")(_validate_password)


class SetActiveRequest(BaseModel):
    is_active: bool


class SetRoleRequest(BaseModel):
    role: str = "user"

    @field_validator("role")
    @classmethod
    def validate_role(cls, v: str) -> str:
        if v not in ("admin", "user"):
            raise ValueError("Role must be 'admin' or 'user'")
        return v
