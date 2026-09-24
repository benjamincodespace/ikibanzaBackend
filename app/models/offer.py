from pydantic import BaseModel, Field
from typing import Optional, Literal, List

OfferStatus = Literal["pending", "countered", "accepted", "rejected", "expired", "cancelled"]
ActorRole = Literal["buyer", "seller"]
EventType = Literal["offer", "counter", "accept", "reject", "cancel", "message"]


class OfferCreate(BaseModel):
    amount: float
    currency: str = "RWF"
    message: Optional[str] = None


class OfferRespond(BaseModel):
    action: Literal["counter", "accept", "reject"]
    amount: Optional[float] = None  # required when action == counter
    message: Optional[str] = None


class OfferEventOut(BaseModel):
    actor_role: ActorRole
    actor_id: str
    type: EventType
    amount: Optional[float] = None
    message: Optional[str] = None
    at: str
    read_by: List[str] = Field(default_factory=list)


class OfferMessageCreate(BaseModel):
    message: str


class OfferOut(BaseModel):
    id: str
    listing_id: str
    buyer_id: str
    seller_id: str
    status: OfferStatus
    current_amount: float
    currency: str
    turn: ActorRole
    events: List[OfferEventOut]
    created_at: str
    updated_at: str
    listing_title: Optional[str] = None
    listing_price: Optional[float] = None
    listing_currency: Optional[str] = None
    buyer: Optional[dict] = None
    seller: Optional[dict] = None
