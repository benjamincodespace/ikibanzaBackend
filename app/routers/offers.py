from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends, HTTPException
from bson import ObjectId
from app.database import offers_col, listings_col, transactions_col, users_col, get_fee_config
from app.models.offer import OfferCreate, OfferRespond, OfferOut, OfferMessageCreate
from app.dependencies import get_current_user, require_roles

router = APIRouter(tags=["offers"])


async def _offer_out(doc: dict) -> dict:
    users = await users_col.find({"_id": {"$in": [doc["buyer_id"], doc["seller_id"]]}}).to_list(2)
    users_by_id = {str(user["_id"]): user for user in users}
    listing = await listings_col.find_one({"_id": doc["listing_id"]}, {"title": 1, "price": 1, "currency": 1})

    def participant(user_id):
        user = users_by_id.get(str(user_id), {})
        last_seen = user.get("last_seen")
        is_online = False
        if last_seen:
            try:
                is_online = datetime.fromisoformat(last_seen) >= datetime.now(timezone.utc) - timedelta(seconds=90)
            except ValueError:
                pass
        return {
            "id": str(user_id),
            "name": user.get("name", "User"),
            "profile_image_url": user.get("profile_image_url"),
            "last_seen": last_seen,
            "is_online": is_online,
        }

    return {
        "id": str(doc["_id"]),
        "listing_id": str(doc["listing_id"]),
        "buyer_id": str(doc["buyer_id"]),
        "seller_id": str(doc["seller_id"]),
        "status": doc["status"],
        "current_amount": doc["current_amount"],
        "currency": doc.get("currency", "RWF"),
        "turn": doc["turn"],
        "events": [{**event, "read_by": event.get("read_by", [])} for event in doc.get("events", [])],
        "created_at": doc["created_at"],
        "updated_at": doc["updated_at"],
        "listing_title": listing.get("title") if listing else None,
        "listing_price": listing.get("price") if listing else None,
        "listing_currency": listing.get("currency", "RWF") if listing else None,
        "buyer": participant(doc["buyer_id"]),
        "seller": participant(doc["seller_id"]),
    }


async def _create_transaction_from_offer(offer_doc: dict, listing_doc: dict):
    fee_config = await get_fee_config()
    buyer_rate = fee_config["buyer_rate"]
    seller_rate = fee_config["seller_rate"]
    agreed_price = offer_doc["current_amount"]

    buyer_fee = agreed_price * buyer_rate
    seller_fee = agreed_price * seller_rate
    min_fee = fee_config.get("min_fee", 0) or 0
    max_fee = fee_config.get("max_fee")
    if buyer_fee < min_fee:
        buyer_fee = min_fee
    if seller_fee < min_fee:
        seller_fee = min_fee
    if max_fee:
        buyer_fee = min(buyer_fee, max_fee)
        seller_fee = min(seller_fee, max_fee)

    now = datetime.now(timezone.utc).isoformat()
    tx_doc = {
        "listing_id": listing_doc["_id"],
        "offer_id": offer_doc["_id"],
        "buyer_id": offer_doc["buyer_id"],
        "seller_id": offer_doc["seller_id"],
        "agreed_price": agreed_price,
        "currency": offer_doc.get("currency", "RWF"),
        "buyer_rate": buyer_rate,
        "seller_rate": seller_rate,
        "buyer_fee": round(buyer_fee, 2),
        "seller_fee": round(seller_fee, 2),
        "gross_platform_fee": round(buyer_fee + seller_fee, 2),
        "status": "agreed",
        "due_diligence_status": "not_started",
        "payment_status": "unpaid",
        "transfer_status": "not_started",
        "created_at": now,
        "updated_at": now,
    }
    result = await transactions_col.insert_one(tx_doc)
    tx_doc["_id"] = result.inserted_id
    return tx_doc


@router.post("/listings/{listing_id}/offers", response_model=OfferOut)
async def create_offer(
    listing_id: str,
    payload: OfferCreate,
    current_user: dict = Depends(require_roles("buyer", "seller")),
):
    listing = await listings_col.find_one({"_id": ObjectId(listing_id)})
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")
    if listing["status"] not in ("published", "under_offer"):
        raise HTTPException(status_code=400, detail="This listing is not accepting offers")
    if str(listing["owner_id"]) == current_user["id"]:
        raise HTTPException(status_code=400, detail="You cannot make an offer on your own listing")

    now = datetime.now(timezone.utc).isoformat()
    doc = {
        "listing_id": listing["_id"],
        "buyer_id": current_user["_id"],
        "seller_id": listing["owner_id"],
        "status": "pending",
        "current_amount": payload.amount,
        "currency": payload.currency,
        "turn": "seller",  # seller must respond next
        "events": [{
            "actor_role": "buyer",
            "actor_id": current_user["id"],
            "type": "offer",
            "amount": payload.amount,
            "message": payload.message,
            "at": now,
            "read_by": [current_user["id"]],
        }],
        "created_at": now,
        "updated_at": now,
    }
    result = await offers_col.insert_one(doc)
    doc["_id"] = result.inserted_id

    await listings_col.update_one({"_id": listing["_id"]}, {"$set": {"status": "under_offer"}})
    return await _offer_out(doc)


@router.get("/listings/{listing_id}/offers", response_model=list[OfferOut])
async def list_offers_for_listing(listing_id: str, current_user: dict = Depends(get_current_user)):
    listing = await listings_col.find_one({"_id": ObjectId(listing_id)})
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    is_owner = str(listing["owner_id"]) == current_user["id"]
    query = {"listing_id": listing["_id"]}
    if not is_owner and current_user["role"] != "admin":
        query["buyer_id"] = current_user["_id"]

    cursor = offers_col.find(query).sort("created_at", -1)
    return [await _offer_out(doc) async for doc in cursor]


@router.get("/offers/mine", response_model=list[OfferOut])
async def my_offers(current_user: dict = Depends(get_current_user)):
    query = {"$or": [{"buyer_id": current_user["_id"]}, {"seller_id": current_user["_id"]}]}
    cursor = offers_col.find(query).sort("updated_at", -1)
    return [await _offer_out(doc) async for doc in cursor]


@router.get("/offers/{offer_id}", response_model=OfferOut)
async def get_offer(offer_id: str, current_user: dict = Depends(get_current_user)):
    doc = await offers_col.find_one({"_id": ObjectId(offer_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Offer not found")
    if current_user["id"] not in (str(doc["buyer_id"]), str(doc["seller_id"])) and current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to view this negotiation")
    return await _offer_out(doc)


@router.post("/offers/{offer_id}/respond", response_model=OfferOut)
async def respond_to_offer(
    offer_id: str, payload: OfferRespond, current_user: dict = Depends(get_current_user)
):
    doc = await offers_col.find_one({"_id": ObjectId(offer_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Offer not found")
    if doc["status"] not in ("pending", "countered"):
        raise HTTPException(status_code=400, detail=f"Offer is already {doc['status']}")

    is_buyer = current_user["id"] == str(doc["buyer_id"])
    is_seller = current_user["id"] == str(doc["seller_id"])
    if not (is_buyer or is_seller):
        raise HTTPException(status_code=403, detail="Not authorized on this negotiation")

    actor_role = "buyer" if is_buyer else "seller"
    if doc["turn"] != actor_role and current_user["role"] != "admin":
        raise HTTPException(status_code=400, detail=f"It is not your turn. Waiting on {doc['turn']}.")

    now = datetime.now(timezone.utc).isoformat()
    listing = await listings_col.find_one({"_id": doc["listing_id"]})

    if payload.action == "counter":
        if payload.amount is None:
            raise HTTPException(status_code=400, detail="amount is required for a counteroffer")
        if payload.amount <= 0:
            raise HTTPException(status_code=400, detail="Counteroffer amount must be greater than zero")
        next_turn = "seller" if actor_role == "buyer" else "buyer"
        event = {
            "actor_role": actor_role,
            "actor_id": current_user["id"],
            "type": "counter",
            "amount": payload.amount,
            "message": payload.message,
            "at": now,
            "read_by": [current_user["id"]],
        }
        await offers_col.update_one(
            {"_id": doc["_id"]},
            {
                "$set": {"status": "countered", "current_amount": payload.amount, "turn": next_turn, "updated_at": now},
                "$push": {"events": event},
            },
        )

    elif payload.action == "accept":
        event = {
            "actor_role": actor_role,
            "actor_id": current_user["id"],
            "type": "accept",
            "amount": doc["current_amount"],
            "message": payload.message,
            "at": now,
            "read_by": [current_user["id"]],
        }
        await offers_col.update_one(
            {"_id": doc["_id"]},
            {"$set": {"status": "accepted", "updated_at": now}, "$push": {"events": event}},
        )
        doc["status"] = "accepted"
        await listings_col.update_one({"_id": listing["_id"]}, {"$set": {"status": "sold", "updated_at": now}})
        await _create_transaction_from_offer(doc, listing)

    elif payload.action == "reject":
        event = {
            "actor_role": actor_role,
            "actor_id": current_user["id"],
            "type": "reject",
            "amount": doc["current_amount"],
            "message": payload.message,
            "at": now,
            "read_by": [current_user["id"]],
        }
        await offers_col.update_one(
            {"_id": doc["_id"]},
            {"$set": {"status": "rejected", "updated_at": now}, "$push": {"events": event}},
        )
        # listing becomes available again if no other active offers
        other_active = await offers_col.find_one(
            {"listing_id": listing["_id"], "status": {"$in": ["pending", "countered"]}, "_id": {"$ne": doc["_id"]}}
        )
        if not other_active:
            await listings_col.update_one({"_id": listing["_id"]}, {"$set": {"status": "published"}})

    doc = await offers_col.find_one({"_id": doc["_id"]})
    return await _offer_out(doc)


async def _get_authorized_offer(offer_id: str, current_user: dict) -> dict:
    doc = await offers_col.find_one({"_id": ObjectId(offer_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Offer not found")
    if current_user["id"] not in (str(doc["buyer_id"]), str(doc["seller_id"])) and current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not authorized on this negotiation")
    return doc


@router.post("/offers/{offer_id}/messages", response_model=OfferOut)
async def send_message(
    offer_id: str,
    payload: OfferMessageCreate,
    current_user: dict = Depends(get_current_user),
):
    doc = await _get_authorized_offer(offer_id, current_user)
    message = payload.message.strip()
    if not message:
        raise HTTPException(status_code=400, detail="Message cannot be empty")
    if len(message) > 2000:
        raise HTTPException(status_code=400, detail="Message cannot exceed 2000 characters")
    now = datetime.now(timezone.utc).isoformat()
    event = {
        "actor_role": "buyer" if current_user["id"] == str(doc["buyer_id"]) else "seller",
        "actor_id": current_user["id"],
        "type": "message",
        "message": message,
        "at": now,
        "read_by": [current_user["id"]],
    }
    await offers_col.update_one(
        {"_id": doc["_id"]},
        {"$push": {"events": event}, "$set": {"updated_at": now}},
    )
    return await _offer_out(await offers_col.find_one({"_id": doc["_id"]}))


@router.post("/offers/{offer_id}/read", response_model=OfferOut)
async def mark_offer_read(offer_id: str, current_user: dict = Depends(get_current_user)):
    doc = await _get_authorized_offer(offer_id, current_user)
    events = doc.get("events", [])
    changed = False
    for event in events:
        if current_user["id"] not in event.get("read_by", []):
            event["read_by"] = [*event.get("read_by", []), current_user["id"]]
            changed = True
    if changed:
        await offers_col.update_one({"_id": doc["_id"]}, {"$set": {"events": events}})
        doc["events"] = events
    return await _offer_out(doc)
