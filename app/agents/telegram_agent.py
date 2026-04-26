"""Telegram messaging: send outgoing messages, parse incoming updates."""
import httpx
from app.config import settings

TG_API = f"https://api.telegram.org/bot{settings.TELEGRAM_BOT_TOKEN}"


async def send_message(chat_id: int, text: str) -> None:
    """Send a text message to a Telegram chat."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        await http.post(
            f"{TG_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
        )


async def send_typing(chat_id: int) -> None:
    """Show 'typing...' indicator."""
    async with httpx.AsyncClient(timeout=5.0) as http:
        await http.post(
            f"{TG_API}/sendChatAction",
            json={"chat_id": chat_id, "action": "typing"},
        )


async def download_photo(file_id: str) -> tuple[bytes, str]:
    """Download a Telegram photo by file_id.

    Returns (image_bytes, media_type) where media_type is e.g. 'image/jpeg'.
    """
    async with httpx.AsyncClient(timeout=30.0) as http:
        # Step 1: getFile to resolve the file_path on Telegram's CDN
        resp = await http.get(f"{TG_API}/getFile", params={"file_id": file_id})
        resp.raise_for_status()
        data = resp.json()
        if not data.get("ok"):
            raise RuntimeError(f"Telegram getFile failed: {data}")
        file_path = data["result"]["file_path"]

        # Step 2: download the actual file bytes
        file_url = (
            f"https://api.telegram.org/file/bot{settings.TELEGRAM_BOT_TOKEN}/{file_path}"
        )
        resp = await http.get(file_url)
        resp.raise_for_status()

        # Determine media type from extension (Telegram usually serves JPEG)
        lower = file_path.lower()
        if lower.endswith(".png"):
            media_type = "image/png"
        elif lower.endswith(".webp"):
            media_type = "image/webp"
        elif lower.endswith(".gif"):
            media_type = "image/gif"
        else:
            media_type = "image/jpeg"

        return resp.content, media_type


def parse_update(update: dict) -> dict | None:
    """Extract the relevant fields from a Telegram update payload.

    Returns a normalized dict or None if the update is unhandleable (no message at all).
    """
    msg = update.get("message")
    if not msg:
        return None

    base = {
        "chat_id": msg["chat"]["id"],
        "username": msg["from"].get("username"),
        "first_name": msg["from"].get("first_name", ""),
    }

    # Photo? → take the largest variant + optional caption
    photos = msg.get("photo")
    if photos:
        largest = max(photos, key=lambda p: p.get("width", 0) * p.get("height", 0))
        return {
            **base,
            "text": msg.get("caption"),  # caption acts as text/context if present
            "is_non_text": False,
            "is_photo": True,
            "photo_file_id": largest["file_id"],
            "caption": msg.get("caption"),
        }

    # Plain text
    text = msg.get("text")
    if text:
        return {
            **base,
            "text": text,
            "is_non_text": False,
            "is_photo": False,
        }

    # Sticker, voice, video, document, etc. — not handled
    return {
        **base,
        "text": None,
        "is_non_text": True,
        "is_photo": False,
    }


async def set_webhook(webhook_url: str, secret_token: str) -> dict:
    """Register the webhook with Telegram. Run this once after deploy."""
    async with httpx.AsyncClient(timeout=10.0) as http:
        resp = await http.post(
            f"{TG_API}/setWebhook",
            json={
                "url": webhook_url,
                "secret_token": secret_token,
                "allowed_updates": ["message"],
            },
        )
        return resp.json()
