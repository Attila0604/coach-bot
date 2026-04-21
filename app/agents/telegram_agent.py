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


def parse_update(update: dict) -> dict | None:
    """Extract the relevant fields from a Telegram update payload.

    Returns a normalized dict or None if the update is not a text message we handle.
    """
    msg = update.get("message")
    if not msg:
        return None
    text = msg.get("text")
    if not text:
        # Photo, sticker, etc. are ignored in text-only MVP
        return {
            "chat_id": msg["chat"]["id"],
            "username": msg["from"].get("username"),
            "first_name": msg["from"].get("first_name", ""),
            "text": None,
            "is_non_text": True,
        }
    return {
        "chat_id": msg["chat"]["id"],
        "username": msg["from"].get("username"),
        "first_name": msg["from"].get("first_name", ""),
        "text": text,
        "is_non_text": False,
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
