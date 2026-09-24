from datetime import datetime, timezone
from typing import Optional
from fastapi import APIRouter, Depends, HTTPException, Query, UploadFile, File
from bson import ObjectId
from app.database import listings_col
from app.models.listing import ListingCreate, ListingUpdate, ListingOut
from app.dependencies import get_current_user, require_roles
from app.utils.cloudinary_utils import upload_image

router = APIRouter(prefix="/listings", tags=["listings"])


def _listing_out(doc: dict) -> dict:
    coords = doc.get("location", {}).get("coordinates", [0, 0])
    return {
        "id": str(doc["_id"]),
        "title": doc["title"],
        "description": doc["description"],
        "property_type": doc.get("property_type", "land"),
        "price": doc["price"],
        "currency": doc.get("currency", "RWF"),
        "size_sqm": doc["size_sqm"],
        "negotiable": doc.get("negotiable", True),
        "province": doc.get("province"),
        "district": doc.get("district"),
        "sector": doc.get("sector"),
        "cell": doc.get("cell"),
        "village": doc.get("village"),
        "longitude": coords[0],
        "latitude": coords[1],
        "upi_reference": doc.get("upi_reference"),
        "zoning_category": doc.get("zoning_category", "other"),
        "zoning_source": doc.get("zoning_source", "seller-provided"),
        "verification_status": doc.get("verification_status", "unverified"),
        "verification_note": doc.get("verification_note"),
        "verified_by": doc.get("verified_by"),
        "verified_at": doc.get("verified_at"),
        "status": doc.get("status", "published"),
        "images": doc.get("images", []),
        "owner_id": str(doc["owner_id"]),
        "created_at": doc.get("created_at"),
        "updated_at": doc.get("updated_at"),
    }


@router.post("", response_model=ListingOut)
async def create_listing(
    payload: ListingCreate,
    current_user: dict = Depends(require_roles("buyer", "seller", "agent", "admin")),
):
    now = datetime.now(timezone.utc).isoformat()
    doc = payload.model_dump(exclude={"latitude", "longitude"})
    doc["location"] = {"type": "Point", "coordinates": [payload.longitude, payload.latitude]}
    doc["owner_id"] = current_user["_id"]
    doc["verification_status"] = "unverified"
    doc["status"] = "published"
    doc["images"] = []
    doc["created_at"] = now
    doc["updated_at"] = now
    result = await listings_col.insert_one(doc)
    doc["_id"] = result.inserted_id
    return _listing_out(doc)


@router.get("", response_model=list[ListingOut])
async def search_listings(
    q: Optional[str] = None,
    min_price: Optional[float] = None,
    max_price: Optional[float] = None,
    min_size: Optional[float] = None,
    max_size: Optional[float] = None,
    zoning_category: Optional[str] = None,
    district: Optional[str] = None,
    negotiable: Optional[bool] = None,
    status_filter: Optional[str] = Query(None, alias="status"),
    limit: int = 50,
    skip: int = 0,
):
    query: dict = {}
    if status_filter:
        query["status"] = status_filter
    else:
        query["status"] = {"$in": ["published", "under_offer"]}
    if q:
        query["$or"] = [
            {"title": {"$regex": q, "$options": "i"}},
            {"description": {"$regex": q, "$options": "i"}},
        ]
    if min_price is not None or max_price is not None:
        price_q = {}
        if min_price is not None:
            price_q["$gte"] = min_price
        if max_price is not None:
            price_q["$lte"] = max_price
        query["price"] = price_q
    if min_size is not None or max_size is not None:
        size_q = {}
        if min_size is not None:
            size_q["$gte"] = min_size
        if max_size is not None:
            size_q["$lte"] = max_size
        query["size_sqm"] = size_q
    if zoning_category:
        query["zoning_category"] = zoning_category
    if district:
        query["district"] = {"$regex": district, "$options": "i"}
    if negotiable is not None:
        query["negotiable"] = negotiable

    cursor = listings_col.find(query).sort("created_at", -1).skip(skip).limit(limit)
    results = [_listing_out(doc) async for doc in cursor]
    return results


@router.get("/mine", response_model=list[ListingOut])
async def my_listings(current_user: dict = Depends(get_current_user)):
    cursor = listings_col.find({"owner_id": current_user["_id"]}).sort("created_at", -1)
    return [_listing_out(doc) async for doc in cursor]


@router.get("/{listing_id}", response_model=ListingOut)
async def get_listing(listing_id: str):
    try:
        doc = await listings_col.find_one({"_id": ObjectId(listing_id)})
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid listing id")
    if not doc:
        raise HTTPException(status_code=404, detail="Listing not found")
    return _listing_out(doc)


@router.patch("/{listing_id}", response_model=ListingOut)
async def update_listing(
    listing_id: str, payload: ListingUpdate, current_user: dict = Depends(get_current_user)
):
    doc = await listings_col.find_one({"_id": ObjectId(listing_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Listing not found")
    if str(doc["owner_id"]) != current_user["id"] and current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to edit this listing")

    update = {k: v for k, v in payload.model_dump(exclude_unset=True).items()}
    update["updated_at"] = datetime.now(timezone.utc).isoformat()
    await listings_col.update_one({"_id": doc["_id"]}, {"$set": update})
    doc = await listings_col.find_one({"_id": doc["_id"]})
    return _listing_out(doc)


@router.delete("/{listing_id}")
async def delete_listing(listing_id: str, current_user: dict = Depends(get_current_user)):
    doc = await listings_col.find_one({"_id": ObjectId(listing_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Listing not found")
    if str(doc["owner_id"]) != current_user["id"] and current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to delete this listing")
    await listings_col.update_one({"_id": doc["_id"]}, {"$set": {"status": "archived"}})
    return {"detail": "Listing archived"}


@router.post("/{listing_id}/images", response_model=ListingOut)
async def upload_listing_image(
    listing_id: str,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    doc = await listings_col.find_one({"_id": ObjectId(listing_id)})
    if not doc:
        raise HTTPException(status_code=404, detail="Listing not found")
    if str(doc["owner_id"]) != current_user["id"] and current_user["role"] != "admin":
        raise HTTPException(status_code=403, detail="Not authorized to edit this listing")

    contents = await file.read()
    uploaded = upload_image(contents)
    await listings_col.update_one(
        {"_id": doc["_id"]},
        {"$push": {"images": uploaded}, "$set": {"updated_at": datetime.now(timezone.utc).isoformat()}},
    )
    doc = await listings_col.find_one({"_id": doc["_id"]})
    return _listing_out(doc)
