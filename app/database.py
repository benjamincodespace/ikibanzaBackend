from datetime import datetime, timezone
from motor.motor_asyncio import AsyncIOMotorClient
from app.config import settings

client = AsyncIOMotorClient(settings.MONGO_URI)
db = client[settings.DB_NAME]

# Collections
users_col = db["users"]
listings_col = db["listings"]
offers_col = db["offers"]
transactions_col = db["transactions"]
settings_col = db["settings"]
backups_col = db["backups"]
audit_logs_col = db["audit_logs"]

DEFAULT_FEE_CONFIG = {
    "buyer_rate": settings.DEFAULT_BUYER_FEE_RATE,
    "seller_rate": settings.DEFAULT_SELLER_FEE_RATE,
    "min_fee": 0,
    "max_fee": None,
}


async def get_fee_config() -> dict:
    await settings_col.update_one(
        {"_id": "fee_config"},
        {"$setOnInsert": DEFAULT_FEE_CONFIG},
        upsert=True,
    )
    return await settings_col.find_one({"_id": "fee_config"})


async def write_audit_log(actor: dict, action: str, resource: str, details: dict | None = None):
    await audit_logs_col.insert_one({
        "actor_id": actor["id"],
        "actor_name": actor.get("name"),
        "action": action,
        "resource": resource,
        "details": details or {},
        "created_at": datetime.now(timezone.utc).isoformat(),
    })


async def ensure_indexes():
    await users_col.create_index("email", unique=True)
    await listings_col.create_index([("location", "2dsphere")])
    await listings_col.create_index("status")
    await listings_col.create_index("zoning_category")
    await offers_col.create_index("listing_id")
    await offers_col.create_index("buyer_id")
    await transactions_col.create_index("listing_id")

    # Seed default fee configuration if missing
    await get_fee_config()
