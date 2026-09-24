from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, HTTPException, status, Depends, Header, UploadFile, File, Form
from cloudinary.exceptions import Error as CloudinaryError
from fastapi.security import OAuth2PasswordRequestForm
from app.database import users_col
from app.models.user import UserRegister, UserLogin, TokenResponse, UserOut
from app.security import hash_password, verify_password, create_access_token
from app.dependencies import get_current_user
from app.config import settings
from app.utils.cloudinary_utils import upload_image

router = APIRouter(prefix="/auth", tags=["auth"])


def _user_out(doc: dict) -> UserOut:
    last_seen = doc.get("last_seen")
    is_online = False
    if last_seen:
        try:
            is_online = datetime.fromisoformat(last_seen) >= datetime.now(timezone.utc) - timedelta(seconds=90)
        except ValueError:
            pass
    return UserOut(
        id=str(doc["_id"]),
        name=doc["name"],
        email=doc["email"],
        role=doc["role"],
        phone=doc.get("phone"),
        profile_image_url=doc.get("profile_image_url"),
        is_verified=doc.get("is_verified", False),
        last_seen=last_seen,
        is_online=is_online,
        is_active=doc.get("is_active", True),
    )


async def _upload_profile_image(file: UploadFile) -> dict:
    if not file.content_type or not file.content_type.startswith("image/"):
        raise HTTPException(status_code=400, detail="Profile image must be an image file")
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="Profile image cannot be empty")
    if len(contents) > 5 * 1024 * 1024:
        raise HTTPException(status_code=413, detail="Profile image must be 5 MB or smaller")
    try:
        return upload_image(contents, folder="land-marketplace/profiles")
    except CloudinaryError as exc:
        raise HTTPException(status_code=502, detail="Could not store profile image") from exc


@router.post("/register", response_model=TokenResponse)
async def register(
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(..., min_length=6),
    role: str = Form("buyer"),
    phone: str | None = Form(None),
    profile_image: UploadFile = File(...),
):
    """Public signup. Only buyer/seller roles are meaningful here — agent and
    admin accounts are provisioned separately (see /admin/agents and
    /auth/register-admin) so the public form can't be used to self-grant
    elevated roles."""
    payload = UserRegister(name=name, email=email, password=password, role=role, phone=phone)
    existing = await users_col.find_one({"email": payload.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    # Public signup creates one marketplace account. Buyers and sellers use
    # the same account capabilities; elevated roles are provisioned separately.
    role = "buyer"

    profile = await _upload_profile_image(profile_image)
    doc = {
        "name": payload.name,
        "email": payload.email,
        "password_hash": hash_password(payload.password),
        "role": role,
        "phone": payload.phone,
        "profile_image_url": profile["url"],
        "profile_image_public_id": profile["public_id"],
        "is_verified": False,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await users_col.insert_one(doc)
    doc["_id"] = result.inserted_id
    token = create_access_token({"sub": str(result.inserted_id), "role": doc["role"]})
    return TokenResponse(access_token=token, user=_user_out(doc))


@router.post("/register-admin", response_model=TokenResponse)
async def register_admin(
    x_setup_key: str = Header(...),
    name: str = Form(...),
    email: str = Form(...),
    password: str = Form(..., min_length=6),
    phone: str | None = Form(None),
    profile_image: UploadFile = File(...),
):
    """Hidden, key-protected route for provisioning the platform's admin account."""
    if not settings.ADMIN_SETUP_KEY or x_setup_key.strip() != settings.ADMIN_SETUP_KEY:
        raise HTTPException(status_code=403, detail="Invalid setup key")

    payload = UserRegister(name=name, email=email, password=password, phone=phone)
    existing = await users_col.find_one({"email": payload.email})
    if existing:
        raise HTTPException(status_code=400, detail="Email already registered")

    profile = await _upload_profile_image(profile_image)
    doc = {
        "name": payload.name,
        "email": payload.email,
        "password_hash": hash_password(payload.password),
        "role": "admin",
        "phone": payload.phone,
        "profile_image_url": profile["url"],
        "profile_image_public_id": profile["public_id"],
        "is_verified": True,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    result = await users_col.insert_one(doc)
    doc["_id"] = result.inserted_id
    token = create_access_token({"sub": str(result.inserted_id), "role": "admin"})
    return TokenResponse(access_token=token, user=_user_out(doc))


@router.post("/login", response_model=TokenResponse)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    # form_data.username is used as the email field (OAuth2 password flow standard)
    user = await users_col.find_one({"email": form_data.username})
    if not user or not verify_password(form_data.password, user["password_hash"]):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect email or password",
        )
    token = create_access_token({"sub": str(user["_id"]), "role": user["role"]})
    return TokenResponse(access_token=token, user=_user_out(user))


@router.post("/login-json", response_model=TokenResponse)
async def login_json(payload: UserLogin):
    """Convenience JSON login endpoint for the frontend (avoids form-encoding)."""
    user = await users_col.find_one({"email": payload.email})
    if not user or not verify_password(payload.password, user["password_hash"]):
        raise HTTPException(status_code=401, detail="Incorrect email or password")
    token = create_access_token({"sub": str(user["_id"]), "role": user["role"]})
    return TokenResponse(access_token=token, user=_user_out(user))


@router.get("/me", response_model=UserOut)
async def me(current_user: dict = Depends(get_current_user)):
    return _user_out(current_user)


@router.post("/presence")
async def update_presence(current_user: dict = Depends(get_current_user)):
    now = datetime.now(timezone.utc).isoformat()
    await users_col.update_one({"_id": current_user["_id"]}, {"$set": {"last_seen": now}})
    return {"last_seen": now, "is_online": True}
