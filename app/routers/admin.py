from datetime import datetime, timezone
import json
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Form
from fastapi.responses import Response
from cloudinary.exceptions import Error as CloudinaryError
from bson import ObjectId
from app.database import (
    audit_logs_col,
    backups_col,
    get_fee_config as load_fee_config,
    listings_col,
    offers_col,
    settings_col,
    transactions_col,
    users_col,
    write_audit_log,
)
from app.models.settings_model import FeeConfigOut, FeeConfigUpdate
from app.models.user import UserRegister, UserOut, UserManagementUpdate
from app.security import hash_password
from app.dependencies import require_roles
from app.utils.cloudinary_utils import upload_image

router = APIRouter(prefix="/admin", tags=["admin"])


def _backup_safe(value):
    if isinstance(value, ObjectId):
        return str(value)
    if isinstance(value, dict):
        return {key: _backup_safe(item) for key, item in value.items() if key != "password_hash"}
    if isinstance(value, list):
        return [_backup_safe(item) for item in value]
    return value


async def _create_backup(current_user: dict) -> dict:
    collections = {
        "users": users_col,
        "listings": listings_col,
        "offers": offers_col,
        "transactions": transactions_col,
        "settings": settings_col,
    }
    data = {}
    counts = {}
    for name, collection in collections.items():
        documents = [_backup_safe(document) async for document in collection.find({})]
        data[name] = documents
        counts[name] = len(documents)

    now = datetime.now(timezone.utc).isoformat()
    backup = {
        "created_at": now,
        "created_by": current_user["id"],
        "created_by_name": current_user.get("name"),
        "counts": counts,
        "data": data,
    }
    result = await backups_col.insert_one(backup)
    await write_audit_log(current_user, "backup.created", str(result.inserted_id), {"counts": counts})
    return {**backup, "id": str(result.inserted_id)}


@router.get("/backups")
async def list_backups(current_user: dict = Depends(require_roles("admin"))):
    cursor = backups_col.find({}, {"data": 0}).sort("created_at", -1)
    return [
        {
            "id": str(document["_id"]),
            "created_at": document["created_at"],
            "created_by_name": document.get("created_by_name"),
            "counts": document.get("counts", {}),
        }
        async for document in cursor
    ]


@router.post("/backups")
async def create_backup(current_user: dict = Depends(require_roles("admin"))):
    backup = await _create_backup(current_user)
    return {key: value for key, value in backup.items() if key != "data"}


@router.get("/backups/{backup_id}/download")
async def download_backup(backup_id: str, current_user: dict = Depends(require_roles("admin"))):
    try:
        document = await backups_col.find_one({"_id": ObjectId(backup_id)})
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid backup id") from exc
    if not document:
        raise HTTPException(status_code=404, detail="Backup not found")
    await write_audit_log(current_user, "backup.downloaded", backup_id)
    payload = json.dumps(_backup_safe(document), indent=2)
    return Response(
        content=payload,
        media_type="application/json",
        headers={"Content-Disposition": f'attachment; filename="ikibanza-backup-{backup_id}.json"'},
    )


@router.get("/logs")
async def list_audit_logs(current_user: dict = Depends(require_roles("admin"))):
    cursor = audit_logs_col.find({}).sort("created_at", -1).limit(200)
    return [
        {
            "id": str(document["_id"]),
            "actor_name": document.get("actor_name"),
            "action": document["action"],
            "resource": document["resource"],
            "details": document.get("details", {}),
            "created_at": document["created_at"],
        }
        async for document in cursor
    ]


@router.get("/fee-config", response_model=FeeConfigOut)
async def get_fee_config(current_user: dict = Depends(require_roles("admin"))):
    doc = await load_fee_config()
    return FeeConfigOut(**{k: v for k, v in doc.items() if k != "_id"})


@router.put("/fee-config", response_model=FeeConfigOut)
async def update_fee_config(
    payload: FeeConfigUpdate, current_user: dict = Depends(require_roles("admin"))
):
    update = {k: v for k, v in payload.model_dump(exclude_unset=True).items() if v is not None}
    if update:
        await settings_col.update_one({"_id": "fee_config"}, {"$set": update})
    doc = await load_fee_config()
    await write_audit_log(current_user, "fee_config.updated", "fee_config", update)
    return FeeConfigOut(**{k: v for k, v in doc.items() if k != "_id"})


@router.get("/finance")
async def finance_summary(current_user: dict = Depends(require_roles("admin"))):
    transactions = [
        document
        async for document in transactions_col.find({}).sort("created_at", -1)
    ]
    revenue_by_currency = {}
    volume_by_currency = {}
    for transaction in transactions:
        currency = transaction.get("currency", "RWF")
        revenue_by_currency[currency] = round(
            revenue_by_currency.get(currency, 0) + transaction.get("gross_platform_fee", 0),
            2,
        )
        volume_by_currency[currency] = round(
            volume_by_currency.get(currency, 0) + transaction.get("agreed_price", 0),
            2,
        )

    return {
        "accepted_transactions": len(transactions),
        "revenue_by_currency": revenue_by_currency,
        "transaction_volume_by_currency": volume_by_currency,
        "recent_transactions": [
            {
                "id": str(transaction["_id"]),
                "agreed_price": transaction.get("agreed_price", 0),
                "currency": transaction.get("currency", "RWF"),
                "buyer_fee": transaction.get("buyer_fee", 0),
                "seller_fee": transaction.get("seller_fee", 0),
                "gross_platform_fee": transaction.get("gross_platform_fee", 0),
                "status": transaction.get("status"),
                "created_at": transaction.get("created_at"),
            }
        ]
    }


@router.get("/transactions")
async def accepted_transactions(current_user: dict = Depends(require_roles("admin"))):
    records = []
    async for transaction in transactions_col.find({}).sort("created_at", -1):
        buyer = await users_col.find_one({"_id": transaction.get("buyer_id")}, {"password_hash": 0})
        seller = await users_col.find_one({"_id": transaction.get("seller_id")}, {"password_hash": 0})
        listing = await listings_col.find_one({"_id": transaction.get("listing_id")})
        coordinates = (listing or {}).get("location", {}).get("coordinates", [None, None])
        records.append({
            "id": str(transaction["_id"]),
            "agreed_price": transaction.get("agreed_price", 0),
            "currency": transaction.get("currency", "RWF"),
            "buyer_fee": transaction.get("buyer_fee", 0),
            "seller_fee": transaction.get("seller_fee", 0),
            "gross_platform_fee": transaction.get("gross_platform_fee", 0),
            "status": transaction.get("status"),
            "due_diligence_status": transaction.get("due_diligence_status"),
            "payment_status": transaction.get("payment_status"),
            "transfer_status": transaction.get("transfer_status"),
            "created_at": transaction.get("created_at"),
            "updated_at": transaction.get("updated_at"),
            "buyer": {
                "id": str(buyer["_id"]), "name": buyer.get("name"),
                "email": buyer.get("email"), "phone": buyer.get("phone"),
            } if buyer else None,
            "seller": {
                "id": str(seller["_id"]), "name": seller.get("name"),
                "email": seller.get("email"), "phone": seller.get("phone"),
            } if seller else None,
            "land": {
                "id": str(listing["_id"]), "title": listing.get("title"),
                "description": listing.get("description"), "price": listing.get("price"),
                "size_sqm": listing.get("size_sqm"), "province": listing.get("province"),
                "district": listing.get("district"), "sector": listing.get("sector"),
                "cell": listing.get("cell"), "village": listing.get("village"),
                "latitude": coordinates[1], "longitude": coordinates[0],
                "images": listing.get("images", []), "documents": listing.get("documents", []),
                "verification_status": listing.get("verification_status"),
            } if listing else None,
        })
    return records


@router.get("/listings/pending")
async def pending_listings(current_user: dict = Depends(require_roles("admin", "verifier"))):
    cursor = listings_col.find({"verification_status": {"$in": ["unverified", "pending"]}})
    results = []
    async for doc in cursor:
        coordinates = doc.get("location", {}).get("coordinates", [None, None])
        results.append({
            "id": str(doc["_id"]),
            "title": doc["title"],
            "owner_id": str(doc["owner_id"]),
            "verification_status": doc.get("verification_status"),
            "zoning_source": doc.get("zoning_source"),
            "created_at": doc.get("created_at"),
            "verification_note": doc.get("verification_note"),
            "verified_by": doc.get("verified_by"),
            "verified_at": doc.get("verified_at"),
            "longitude": coordinates[0],
            "latitude": coordinates[1],
        })
    return results


@router.get("/listings")
async def all_listings_for_management(current_user: dict = Depends(require_roles("admin", "verifier"))):
    cursor = listings_col.find({}).sort("updated_at", -1)
    results = []
    async for doc in cursor:
        coordinates = doc.get("location", {}).get("coordinates", [None, None])
        owner_id = doc.get("owner_id")
        owner = await users_col.find_one({"_id": owner_id}, {"password_hash": 0}) if owner_id else None
        if not owner and owner_id:
            owner = await users_col.find_one({"id": str(owner_id)}, {"password_hash": 0})
        results.append({
            "id": str(doc["_id"]),
            "title": doc["title"],
            "description": doc.get("description"),
            "property_type": doc.get("property_type", "land"),
            "price": doc.get("price"),
            "currency": doc.get("currency", "RWF"),
            "size_sqm": doc.get("size_sqm"),
            "negotiable": doc.get("negotiable", True),
            "province": doc.get("province"),
            "district": doc.get("district"),
            "sector": doc.get("sector"),
            "cell": doc.get("cell"),
            "village": doc.get("village"),
            "owner_id": str(owner_id) if owner_id else None,
            "seller": {
                "id": str(owner["_id"]),
                "name": owner.get("name"),
                "email": owner.get("email"),
                "phone": owner.get("phone"),
                "profile_image_url": owner.get("profile_image_url"),
                "role": owner.get("role"),
                "is_verified": owner.get("is_verified", False),
            } if owner else None,
            "images": doc.get("images", []),
            "documents": doc.get("documents", []),
            "verification_status": doc.get("verification_status", "unverified"),
            "verification_note": doc.get("verification_note"),
            "verified_by": doc.get("verified_by"),
            "verified_at": doc.get("verified_at"),
            "zoning_category": doc.get("zoning_category"),
            "zoning_source": doc.get("zoning_source"),
            "status": doc.get("status"),
            "created_at": doc.get("created_at"),
            "updated_at": doc.get("updated_at"),
            "longitude": coordinates[0],
            "latitude": coordinates[1],
        })
    return results


@router.get("/listings/{listing_id}")
async def listing_for_management(
    listing_id: str,
    current_user: dict = Depends(require_roles("admin", "verifier")),
):
    try:
        listing = await listings_col.find_one({"_id": ObjectId(listing_id)})
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid listing id") from exc
    if not listing:
        raise HTTPException(status_code=404, detail="Listing not found")

    owner = await users_col.find_one({"_id": listing.get("owner_id")}, {"password_hash": 0})
    coordinates = listing.get("location", {}).get("coordinates", [None, None])
    return {
        "id": str(listing["_id"]),
        "title": listing["title"],
        "description": listing.get("description"),
        "property_type": listing.get("property_type", "land"),
        "price": listing.get("price"),
        "currency": listing.get("currency", "RWF"),
        "size_sqm": listing.get("size_sqm"),
        "negotiable": listing.get("negotiable", True),
        "province": listing.get("province"),
        "district": listing.get("district"),
        "sector": listing.get("sector"),
        "cell": listing.get("cell"),
        "village": listing.get("village"),
        "longitude": coordinates[0],
        "latitude": coordinates[1],
        "upi_reference": listing.get("upi_reference"),
        "zoning_category": listing.get("zoning_category"),
        "zoning_source": listing.get("zoning_source"),
        "verification_status": listing.get("verification_status", "unverified"),
        "verification_note": listing.get("verification_note"),
        "verified_by": listing.get("verified_by"),
        "verified_at": listing.get("verified_at"),
        "status": listing.get("status"),
        "images": listing.get("images", []),
        "documents": listing.get("documents", []),
        "owner_id": str(listing["owner_id"]),
        "seller": {
            "id": str(owner["_id"]),
            "name": owner.get("name"),
            "email": owner.get("email"),
            "phone": owner.get("phone"),
            "profile_image_url": owner.get("profile_image_url"),
            "role": owner.get("role"),
            "is_verified": owner.get("is_verified", False),
        } if owner else None,
        "created_at": listing.get("created_at"),
        "updated_at": listing.get("updated_at"),
    }


@router.patch("/listings/{listing_id}/verify")
async def verify_listing(
    listing_id: str,
    verification_status: str,
    zoning_source: str | None = None,
    verification_note: str | None = None,
    current_user: dict = Depends(require_roles("admin", "verifier")),
):
    if verification_status not in ("unverified", "pending", "verified", "rejected"):
        raise HTTPException(status_code=400, detail="Invalid verification_status")
    now = datetime.now(timezone.utc).isoformat()
    update = {
        "verification_status": verification_status,
        "verification_note": verification_note,
        "verified_by": current_user["id"],
        "verified_at": now,
        "updated_at": now,
    }
    if zoning_source:
        update["zoning_source"] = zoning_source
    result = await listings_col.update_one({"_id": ObjectId(listing_id)}, {"$set": update})
    if result.matched_count == 0:
        raise HTTPException(status_code=404, detail="Listing not found")
    await write_audit_log(current_user, "listing.verification_updated", listing_id, update)
    return {"detail": "Listing verification updated"}


@router.get("/users")
async def list_users(current_user: dict = Depends(require_roles("admin"))):
    cursor = users_col.find({})
    results = []
    async for doc in cursor:
        results.append({
            "id": str(doc["_id"]),
            "name": doc["name"],
            "email": doc["email"],
            "role": doc["role"],
            "profile_image_url": doc.get("profile_image_url"),
            "is_verified": doc.get("is_verified", False),
            "phone": doc.get("phone"),
            "is_active": doc.get("is_active", True),
            "last_seen": doc.get("last_seen"),
            "created_at": doc.get("created_at"),
        })
    return results


@router.patch("/users/{user_id}", response_model=UserOut)
async def manage_user(
    user_id: str,
    payload: UserManagementUpdate,
    current_user: dict = Depends(require_roles("admin")),
):
    try:
        target_id = ObjectId(user_id)
    except Exception as exc:
        raise HTTPException(status_code=400, detail="Invalid user id") from exc

    target = await users_col.find_one({"_id": target_id})
    if not target:
        raise HTTPException(status_code=404, detail="User not found")
    if str(target["_id"]) == current_user["id"] and payload.is_active is False:
        raise HTTPException(status_code=400, detail="You cannot disable your own admin account")
    if str(target["_id"]) == current_user["id"] and payload.role is not None and payload.role != "admin":
        raise HTTPException(status_code=400, detail="You cannot remove your own admin role")

    update = {
        key: value
        for key, value in payload.model_dump(exclude_unset=True).items()
        if value is not None
    }
    if not update:
        return UserOut(
            id=str(target["_id"]),
            name=target["name"],
            email=target["email"],
            role=target["role"],
            phone=target.get("phone"),
            profile_image_url=target.get("profile_image_url"),
            is_verified=target.get("is_verified", False),
            last_seen=target.get("last_seen"),
            is_active=target.get("is_active", True),
        )

    await users_col.update_one({"_id": target["_id"]}, {"$set": update})
    target.update(update)
    await write_audit_log(current_user, "user.updated", user_id, update)
    return UserOut(
        id=str(target["_id"]),
        name=target["name"],
        email=target["email"],
        role=target["role"],
        phone=target.get("phone"),
        profile_image_url=target.get("profile_image_url"),
        is_verified=target.get("is_verified", False),
        last_seen=target.get("last_seen"),
        is_active=target.get("is_active", True),
    )


@router.post("/agents", response_model=UserOut)
async def create_agent(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(..., min_length=6),
    phone: str | None = Form(None),
    profile_image: UploadFile = File(...),
    current_user: dict = Depends(require_roles("admin")),
):
    """Admin-only: provision an Agent/Broker account. Agents can no longer
    self-register on the public /register page."""
    payload = UserRegister(name=name, email=email, password=password, phone=phone)
    existing = await users_col.find_one({"email": payload.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    if not profile_image.content_type or not profile_image.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Profile image must be an image file")
    contents = await profile_image.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Profile image cannot be empty")
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Profile image must be 5 MB or smaller")
    try:
        profile = upload_image(contents, folder="land-marketplace/profiles")
    except CloudinaryError as exc:
        raise HTTPException(status_code=502, detail="Could not store profile image") from exc

    doc = {
        "name": payload.name,
        "email": payload.email,
        "password_hash": hash_password(payload.password),
        "role": "agent",
        "phone": payload.phone,
        "profile_image_url": profile["url"],
        "profile_image_public_id": profile["public_id"],
        "is_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await users_col.insert_one(doc)
    doc["_id"] = result.inserted_id
    await write_audit_log(current_user, "agent.created", str(result.inserted_id), {"email": doc["email"]})
    return UserOut(
        id=str(doc["_id"]),
        name=doc["name"],
        email=doc["email"],
        role=doc["role"],
        phone=doc.get("phone"),
        profile_image_url=doc["profile_image_url"],
        is_verified=doc.get("is_verified", False),
        is_active=doc.get("is_active", True),
    )
