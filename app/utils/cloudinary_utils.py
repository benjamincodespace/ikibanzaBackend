import cloudinary
import cloudinary.uploader
from app.config import settings

cloudinary.config(
    cloud_name=settings.CLOUDINARY_CLOUD_NAME,
    api_key=settings.CLOUDINARY_API_KEY,
    api_secret=settings.CLOUDINARY_API_SECRET,
    secure=True,
    timeout=30,
)


def upload_image(file_bytes: bytes, folder: str = "land-marketplace/listings") -> dict:
    """Uploads an image to Cloudinary and returns {url, public_id}."""
    result = cloudinary.uploader.upload(file_bytes, folder=folder, resource_type="image")
    return {"url": result.get("secure_url"), "public_id": result.get("public_id")}


def upload_document(file_bytes: bytes, folder: str = "land-marketplace/documents") -> dict:
    """Uploads a private supporting document (raw) to Cloudinary."""
    result = cloudinary.uploader.upload(
        file_bytes, folder=folder, resource_type="raw", type="private"
    )
    return {"url": result.get("secure_url"), "public_id": result.get("public_id")}


def delete_asset(public_id: str, resource_type: str = "image"):
    cloudinary.uploader.destroy(public_id, resource_type=resource_type)
