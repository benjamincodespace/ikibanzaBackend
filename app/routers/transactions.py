from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from app.database import transactions_col
from app.models.transaction import TransactionOut, TransactionStatusUpdate
from app.dependencies import get_current_user, require_roles

router = APIRouter(prefix="/transactions", tags=["transactions"])


def _tx_out(doc: dict) -> dict:
    return {
        "id": str(doc["_id"]),
        "listing_id": str(doc["listing_id"]),
        "offer_id": str(doc["offer_id"]),
        "buyer_id": str(doc["buyer_id"]),
        "seller_id": str(doc["seller_id"]),
        "agreed_price": doc["agreed_price"],
        "currency": doc.get("currency", "RWF"),
        "buyer_rate": doc["buyer_rate"],
        "seller_rate": doc["seller_rate"],
        "buyer_fee": doc["buyer_fee"],
        "seller_fee": doc["seller_fee"],
        "gross_platform_fee": doc["gross_platform_fee"],
        "status": doc["status"],
        "due_diligence_status": doc["due_diligence_status"],
        "payment_status": doc["payment_status"],
        "transfer_status": doc["transfer_status"],
        "created_at": doc["created_at"],
        "updated_at": doc["updated_at"],
    }


@router.get("", response_model=list[TransactionOut])
async def list_my_transactions(current_user: dict = Depends(get_current_user)):
    if current_user["role"] == "admin":
        query = {}
    else:
        query = {"$or": [{"buyer_id": current_user["_id"]}, {"seller_id": current_user["_id"]}]}
    cursor = transactions_col.find(query).sort("created_at", -1)
    return [_tx_out(doc) async for doc in cursor]


@router.get("/{tx_id}", response_model=TransactionOut)
async def get_transaction(tx_id: str, current_user: dict = Depends(get_current_user)):
    doc = await transactions_col.find_one({"_id": ObjectId(tx_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if current_user["id"] not in (str(doc["buyer_id"]), str(doc["seller_id"])) and current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to view this transaction")
    return _tx_out(doc)


@router.patch("/{tx_id}", response_model=TransactionOut)
async def update_transaction_status(
    tx_id: str,
    payload: TransactionStatusUpdate,
    current_user: dict = Depends(require_roles("admin", "verifier", "seller", "agent")),
):
    doc = await transactions_col.find_one({"_id": ObjectId(tx_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Transaction not found")
    if current_user["role"] not in ("admin", "verifier") and current_user["id"] != str(doc["seller_id"]):
        raise HTTPException(status_code=403, detail="Not authorized to update this transaction")

    update = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if not update:
        return _tx_out(doc)

    # Derive overall transaction status from sub-statuses
    dd = update.get("due_diligence_status", doc["due_diligence_status"])
    pay = update.get("payment_status", doc["payment_status"])
    transfer = update.get("transfer_status", doc["transfer_status"])
    if transfer == "completed" and pay == "paid" and dd == "completed":
        overall = "completed"
    elif pay in ("paid", "partially_paid"):
        overall = "payment"
    elif dd in ("in_progress", "completed"):
        overall = "due_diligence"
    else:
        overall = doc["status"]
    update["status"] = overall
    update["updated_at"] = datetime.now(timezone.utc).isoformat()

    await transactions_col.update_one({"_id": doc["_id"]}, {"$set": update})
    doc = await transactions_col.find_one({"_id": doc["_id"]})
    return _tx_out(doc)
