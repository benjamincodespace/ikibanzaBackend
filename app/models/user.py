from pydantic import BaseModel, EmailStr, Field
from typing import Literal, Optional

Role = Literal["buyer", "seller", "agent", "verifier", "admin"]


class UserRegister(BaseModel):
    name: str
    email: EmailStr
    password: str = Field(min_length=6)
    role: Role = "buyer"
    phone: Optional[str] = None


class UserLogin(BaseModel):
    email: EmailStr
    password: str


class UserManagementUpdate(BaseModel):
    role: Role | None = None
    is_verified: bool | None = None
    is_active: bool | None = None


class UserOut(BaseModel):
    id: str
    name: str
    email: EmailStr
    role: Role
    phone: Optional[str] = None
    profile_image_url: Optional[str] = None
    is_verified: bool = False
    last_seen: Optional[str] = None
    is_online: bool = False
    is_active: bool = True


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserOut
