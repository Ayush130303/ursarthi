from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field

# ------------------------------------------------------------------- auth --


class RegisterRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    email: EmailStr
    password: str = Field(min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=128)


class UserResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    name: str
    email: EmailStr
    role: str
    has_preferences: bool = False


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


class UpdateProfileRequest(BaseModel):
    name: str = Field(min_length=2, max_length=120)


class ChangePasswordRequest(BaseModel):
    current_password: str = Field(min_length=1, max_length=128)
    new_password: str = Field(min_length=8, max_length=128)


class ForgotPasswordRequest(BaseModel):
    email: EmailStr


class ResetPasswordRequest(BaseModel):
    token: str
    new_password: str = Field(min_length=8, max_length=128)


# ------------------------------------------------------------ preferences --

INTERESTS = ["food", "history", "culture", "nature", "hiking", "adventure", "spirituality",
             "shopping", "nightlife", "photography", "wildlife", "beaches"]


class PreferenceIn(BaseModel):
    interests: list[str] = Field(default_factory=list, max_length=12)
    budget: Literal["low", "medium", "high"] = "medium"
    pace: Literal["relaxed", "balanced", "packed"] = "balanced"
    travel_with: Literal["solo", "couple", "family", "friends"] = "solo"
    food: Literal["anything", "vegetarian", "vegan", "jain", "halal", "non-vegetarian"] = "anything"
    notes: str | None = Field(None, max_length=500)


class PreferenceOut(PreferenceIn):
    model_config = ConfigDict(from_attributes=True)


class MemoryOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    kind: str
    value: str
    evidence: str
    created_at: datetime


# ------------------------------------------------------------------ trips --


class Stop(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    lat: float = Field(ge=-90, le=90)
    lon: float = Field(ge=-180, le=180)


class TripCreate(BaseModel):
    stops: list[Stop] = Field(min_length=2, max_length=15, description="stops[0] is the starting point")
    return_to_start: bool = False


class TripSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    title: str
    total_km: float
    created_at: datetime


class MessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: int
    role: str
    content: str
    sources: list = []
    created_at: datetime


class TripOut(TripSummary):
    stops: list[dict]
    order: list[int]
    legs_km: list[float]
    distance_mode: str
    geometry: list
    suggestions: dict | None
    messages: list[MessageOut] = []


class AskRequest(BaseModel):
    question: str = Field(min_length=2, max_length=1000)
