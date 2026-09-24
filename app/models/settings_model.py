from pydantic import BaseModel
from typing import Optional


class FeeConfigOut(BaseModel):
    buyer_rate: float
    seller_rate: float
    min_fee: float = 0
    max_fee: Optional[float] = None


class FeeConfigUpdate(BaseModel):
    buyer_rate: Optional[float] = None
    seller_rate: Optional[float] = None
    min_fee: Optional[float] = None
    max_fee: Optional[float] = None
