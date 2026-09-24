from pydantic import BaseModel
from typing import Literal, Optional

DueDiligenceStatus = Literal["not_started", "in_progress", "completed"]
PaymentStatus = Literal["unpaid", "partially_paid", "paid"]
TransferStatus = Literal["not_started", "in_progress", "completed"]
TransactionStatus = Literal["agreed", "due_diligence", "payment", "transfer", "completed", "cancelled"]


class TransactionOut(BaseModel):
    id: str
    listing_id: str
    offer_id: str
    buyer_id: str
    seller_id: str
    agreed_price: float
    currency: str
    buyer_rate: float
    seller_rate: float
    buyer_fee: float
    seller_fee: float
    gross_platform_fee: float
    status: TransactionStatus
    due_diligence_status: DueDiligenceStatus
    payment_status: PaymentStatus
    transfer_status: TransferStatus
    created_at: str
    updated_at: str


class TransactionStatusUpdate(BaseModel):
    due_diligence_status: Optional[DueDiligenceStatus] = None
    payment_status: Optional[PaymentStatus] = None
    transfer_status: Optional[TransferStatus] = None
