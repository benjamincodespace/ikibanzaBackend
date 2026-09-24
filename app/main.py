from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.database import ensure_indexes
from app.config import settings
from app.routers import auth, listings, offers, transactions, admin

app = FastAPI(
    title="Land Marketplace API (IKIBANZA)",
    description="Backend for the Land Marketplace MVP: listings, negotiation, transactions and configurable fees.",
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=list(dict.fromkeys([
        *settings.FRONTEND_ORIGINS,
        "http://localhost:5173",
        "http://127.0.0.1:5173",
    ])),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.on_event("startup")
async def on_startup():
    await ensure_indexes()


@app.get("/")
async def root():
    return {"status": "ok", "service": "land-marketplace-api"}


@app.get("/health")
async def health():
    return {"status": "healthy"}


app.include_router(auth.router)
app.include_router(listings.router)
app.include_router(offers.router)
app.include_router(transactions.router)
app.include_router(admin.router)
