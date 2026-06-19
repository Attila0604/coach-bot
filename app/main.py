"""FastAPI entry point.

Endpoints:
- GET  /              health check
- POST /webhook/telegram   receives Telegram updates
- POST /admin/setup-webhook   one-shot: register webhook with Telegram
"""
import asyncio
import logging
from collections import deque

from fastapi import FastAPI, Request, HTTPException, Header
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from app.config import settings
from app.agents import telegram_agent, router, food_log_agent, reminders
from app import db

logging.basicConfig(level=settings.LOG_LEVEL)
log = logging.getLogger(__name__)

app = FastAPI(title="Coach-Bot")

# In-memory dedup of recently seen Telegram update_ids. Telegram re-delivers an
# update if the webhook does not ACK quickly, which would otherwise double-log
# meals. This guards the common retry-within-seconds case (single-worker deploy).
_DEDUP_MAX = 1000
_seen_update_ids: set[int] = set()
_seen_order: deque[int] = deque()

# Keep strong references to background tasks so they are not garbage-collected.
_background_tasks: set[asyncio.Task] = set()


def _already_seen(update_id: int) -> bool:
    """Return True if this update_id was already processed; otherwise mark it seen."""
    if update_id in _seen_update_ids:
        return True
    _seen_update_ids.add(update_id)
    _seen_order.append(update_id)
    while len(_seen_order) > _DEDUP_MAX:
        old = _seen_order.popleft()
        _seen_update_ids.discard(old)
    return False


async def _safe_handle(parsed: dict, default_coach_id: str) -> None:
    try:
        await router.handle_incoming(parsed, default_coach_id)
    except Exception:
        log.exception("Error handling update")
        # Bot soll nie komplett stumm bleiben -> kurze Fallback-Antwort (DE/HU).
        chat_id = parsed.get("chat_id")
        if chat_id:
            try:
                await telegram_agent.send_message(
                    chat_id,
                    "⚠️ Etwas ist schiefgelaufen — bitte gleich nochmal versuchen.\n"
                    "⚠️ Valami hiba történt — kérlek, próbáld újra.",
                )
            except Exception:
                log.exception("Fallback-Antwort fehlgeschlagen")


_scheduler: AsyncIOScheduler | None = None


@app.on_event("startup")
def _startup() -> None:
    missing = settings.validate()
    if missing:
        log.warning("Missing env vars: %s", missing)

    # Trainings-Reminder: jede Minute prüfen, ob etwas fällig ist.
    global _scheduler
    try:
        _scheduler = AsyncIOScheduler()
        _scheduler.add_job(
            reminders.run_due_reminders,
            "interval",
            minutes=1,
            id="training_reminders",
            max_instances=1,
            coalesce=True,
        )
        _scheduler.start()
        log.info("Reminder-Scheduler gestartet.")
    except Exception:
        log.exception("Reminder-Scheduler konnte nicht gestartet werden")


@app.get("/")
def health() -> dict:
    return {"status": "ok", "service": "coach-bot"}


@app.post("/push/meal-plan")
async def push_meal_plan_endpoint(
    request: Request,
    x_push_secret: str | None = Header(default=None),
) -> dict:
    """Proaktiver Push des Ernährungsplans an einen Kunden (von der Coach-App
    beim Freigeben aufgerufen). Durch ein geteiltes Secret geschützt."""
    if not settings.PUSH_SECRET:
        raise HTTPException(status_code=503, detail="Push disabled (no secret set)")
    if x_push_secret != settings.PUSH_SECRET:
        raise HTTPException(status_code=403, detail="Bad secret")

    body = await request.json()
    customer_id = body.get("customer_id")
    if not customer_id:
        raise HTTPException(status_code=400, detail="customer_id required")

    customer = db.get_customer_by_id(customer_id)
    if not customer:
        return {"ok": False, "error": "customer not found"}

    try:
        sent = await food_log_agent.push_meal_plan(customer)
    except Exception:
        log.exception("push_meal_plan failed")
        return {"ok": False, "error": "send failed"}

    return {"ok": True, "sent": sent}


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

    # Drop duplicate re-deliveries before doing any work.
    update_id = update.get("update_id")
    if update_id is not None and _already_seen(update_id):
        return {"ok": True}

    parsed = telegram_agent.parse_update(update)
    if parsed is None:
        return {"ok": True}

    # For MVP: one hardcoded coach. Later read from config / DB.
    # You set this after creating the coach row manually in Supabase.
    default_coach_id = settings.DEFAULT_COACH_ID
    if not default_coach_id:
        log.error("DEFAULT_COACH_ID not set")
        return {"ok": True}

    # Process in the background so we ACK Telegram immediately (the LLM call can
    # take several seconds). This avoids Telegram retrying and re-sending the update.
    task = asyncio.create_task(_safe_handle(parsed, default_coach_id))
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {"ok": True}


@app.post("/admin/setup-webhook")
async def setup_webhook() -> dict:
    """Call once after deploy to register webhook with Telegram."""
    if not settings.APP_BASE_URL:
        raise HTTPException(status_code=400, detail="APP_BASE_URL not set")
    url = f"{settings.APP_BASE_URL}/webhook/telegram"
    result = await telegram_agent.set_webhook(url, settings.TELEGRAM_WEBHOOK_SECRET)
    return result
