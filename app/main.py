"""FastAPI entry point.

Endpoints:
- GET  /              health check
- POST /webhook/telegram   receives Telegram updates
- POST /admin/setup-webhook   one-shot: register webhook with Telegram
"""
import logging
from fastapi import FastAPI, Request, HTTPException, Header
from app.config import settings
from app.agents import telegram_agent, router

logging.basicConfig(level=settings.LOG_LEVEL)
log = logging.getLogger(__name__)

app = FastAPI(title="Coach-Bot")


@app.on_event("startup")
def _startup() -> None:
    missing = settings.validate()
    if missing:
        log.warning("Missing env vars: %s", missing)


@app.get("/")
def health() -> dict:
    return {"status": "ok", "service": "coach-bot"}


@app.post("/webhook/telegram")
async def telegram_webhook(
    request: Request,
    x_telegram_bot_api_secret_token: str | None = Header(default=None),
) -> dict:
    # Verify secret
    if (
        settings.TELEGRAM_WEBHOOK_SECRET
        and x_telegram_bot_api_secret_token != settings.TELEGRAM_WEBHOOK_SECRET
    ):
        raise HTTPException(status_code=403, detail="Bad secret")

    update = await request.json()
    parsed = telegram_agent.parse_update(update)
    if parsed is None:
        return {"ok": True}

    # For MVP: one hardcoded coach. Later read from config / DB.
    # You set this after creating the coach row manually in Supabase.
    default_coach_id = settings.DEFAULT_COACH_ID
    if not default_coach_id:
        log.error("DEFAULT_COACH_ID not set")
        return {"ok": True}

    try:
        await router.handle_incoming(parsed, default_coach_id)
    except Exception:
        log.exception("Error handling update")
    return {"ok": True}


@app.post("/admin/setup-webhook")
async def setup_webhook() -> dict:
    """Call once after deploy to register webhook with Telegram."""
    if not settings.APP_BASE_URL:
        raise HTTPException(status_code=400, detail="APP_BASE_URL not set")
    url = f"{settings.APP_BASE_URL}/webhook/telegram"
    result = await telegram_agent.set_webhook(url, settings.TELEGRAM_WEBHOOK_SECRET)
    return result
