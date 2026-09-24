from pydantic import BaseModel, Field
from typing import Optional, List, Literal

ZoningCategory = Literal[
    "R1", "R2", "R3", "commercial", "industrial", "agricultural", "mixed-use", "other"
]
ZoningSource = Literal["seller-provided", "platform-reviewed", "authoritative-source"]
VerificationStatus = Literal["unverified", "pending", "verified", "rejected"]
ListingStatus = Literal["draft", "pending_review", "published", "under_offer", "sold", "archived"]


class GeoPoint(BaseModel):
    type: Literal["Point"] = "Point"
    coordinates: List[float]  # [lng, lat]


class ListingCreate(BaseModel):
    title: str
    description: str
    property_type: str = "land"
    price: float
    currency: str = "RWF"
    size_sqm: float
    negotiable: bool = True
    province: Optional[str] = None
    district: Optional[str] = None
    sector: Optional[str] = None
    cell: Optional[str] = None
    village: Optional[str] = None
    latitude: float
    longitude: float
    upi_reference: Optional[str] = None
    zoning_category: ZoningCategory = "other"
    zoning_source: ZoningSource = "seller-provided"


class ListingUpdate(BaseModel):
    title: Optional[str] = None
    description: Optional[str] = None
    price: Optional[float] = None
    size_sqm: Optional[float] = None
    negotiable: Optional[bool] = None
    status: Optional[ListingStatus] = None
    zoning_category: Optional[ZoningCategory] = None
    zoning_source: Optional[ZoningSource] = None


class ListingOut(BaseModel):
    id: str
    title: str
    description: str
    property_type: str
    price: float
    currency: str
    size_sqm: float
    negotiable: bool
    province: Optional[str] = None
    district: Optional[str] = None
    sector: Optional[str] = None
    cell: Optional[str] = None
    village: Optional[str] = None
    latitude: float
    longitude: float
    upi_reference: Optional[str] = None
    zoning_category: str
    zoning_source: str
    verification_status: VerificationStatus
    verification_note: Optional[str] = None
    verified_by: Optional[str] = None
    verified_at: Optional[str] = None
    status: ListingStatus
    images: List[dict] = []
    owner_id: str
    created_at: str
    updated_at: str
