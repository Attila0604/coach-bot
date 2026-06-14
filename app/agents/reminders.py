"""Proaktive Trainings-Reminder.

Wird vom Scheduler (siehe main.py) jede Minute aufgerufen. Schaut, welche
Trainingstage aktiver Pläne heute fällig sind, und schickt dem Kunden eine
Telegram-Erinnerung X Minuten vor der Trainingszeit. Eine Dedup-Tabelle
(training_reminders_sent) sorgt dafür, dass jeder Reminder nur einmal pro Tag
rausgeht — auch wenn der Scheduler jede Minute prüft oder der Bot kurz neu startet.
"""
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.config import settings
from app import db
from app.agents import telegram_agent

log = logging.getLogger(__name__)

# So weit darf ein Reminder verspätet noch raus (z.B. nach kurzer Downtime).
GRACE_MINUTES = 15

# Einfache mehrsprachige Vorlage. {time} = HH:MM, {title} = (übersetzter) Tagestitel.
_REMINDER = {
    "de": "⏰ <b>Trainings-Erinnerung</b>\nHeute um {time} Uhr: <b>{title}</b>\nViel Erfolg! 💪",
    "hu": "⏰ <b>Edzés emlékeztető</b>\nMa {time}-kor: <b>{title}</b>\nSok sikert! 💪",
    "it": "⏰ <b>Promemoria allenamento</b>\nOggi alle {time}: <b>{title}</b>\nForza! 💪",
}


def _lang_of(customer: dict) -> str:
    prof = customer.get("customer_profiles") or [{}]
    if isinstance(prof, list):
        prof = prof[0] if prof else {}
    lang = str((prof or {}).get("language") or "de").lower()
    return lang if lang in _REMINDER else "de"


def _claim(day_id: str, today_iso: str) -> bool:
    """Reserviert den heutigen Reminder für diesen Tag.

    True  = noch nicht gesendet, wir dürfen senden (Eintrag wurde angelegt).
    False = heute schon gesendet (Unique-Verletzung) oder DB-Fehler -> nicht senden.
    """
    try:
        db.db().table("training_reminders_sent").insert(
            {
                "training_day_id": day_id,
                "sent_for": today_iso,
                "recipient": "customer",
            }
        ).execute()
        return True
    except Exception:
        return False


async def run_due_reminders() -> None:
    """Vom Scheduler jede Minute aufgerufen."""
    try:
        tz = ZoneInfo(settings.TZ)
    except Exception:
        tz = ZoneInfo("Europe/Vienna")

    now = datetime.now(tz)
    today_wd = now.weekday()  # 0 = Montag ... 6 = Sonntag (entspricht der App)
    today_iso = now.date().isoformat()

    try:
        resp = (
            db.db()
            .table("training_plans")
            .select(
                "id, customer_id, name, reminder_minutes_before, notify_telegram, "
                "status, translations, "
                "training_days(id, title, weekday, time_of_day)"
            )
            .eq("status", "active")
            .eq("notify_telegram", True)
            .execute()
        )
        plans = resp.data or []
    except Exception:
        log.exception("reminder: Plan-Abfrage fehlgeschlagen")
        return

    for plan in plans:
        days = plan.get("training_days") or []
        due_days = [
            d
            for d in days
            if d.get("weekday") == today_wd and d.get("time_of_day")
        ]
        if not due_days:
            continue

        reminder_min = int(plan.get("reminder_minutes_before") or 0)

        customer = db.get_customer_by_id(plan["customer_id"])
        if not customer or not customer.get("telegram_chat_id"):
            continue

        chat_id = customer["telegram_chat_id"]
        lang = _lang_of(customer)
        tr = (plan.get("translations") or {}).get(lang) if lang != "de" else None

        for d in due_days:
            time_str = str(d["time_of_day"])[:5]  # "HH:MM"
            try:
                hh, mm = int(time_str[:2]), int(time_str[3:5])
            except Exception:
                continue

            workout_dt = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            reminder_dt = workout_dt - timedelta(minutes=reminder_min)
            delta = (now - reminder_dt).total_seconds()

            # Fällig, wenn jetzt zwischen Reminder-Zeit und Reminder-Zeit + Kulanz liegt.
            if not (0 <= delta < GRACE_MINUTES * 60):
                continue

            # Genau einmal pro Tag senden.
            if not _claim(d["id"], today_iso):
                continue

            title = d.get("title") or "Training"
            if tr:
                day_tr = (tr.get("days") or {}).get(d["id"]) or {}
                title = day_tr.get("title") or title

            text = _REMINDER[lang].format(
                time=time_str, title=telegram_agent.escape_html(title)
            )
            try:
                await telegram_agent.send_message(chat_id, text)
                db.log_message(
                    plan["customer_id"], "out", text, agent_name="training_reminder"
                )
                log.info("reminder gesendet: day=%s customer=%s", d["id"], plan["customer_id"])
            except Exception:
                log.exception("reminder: Senden fehlgeschlagen (day %s)", d["id"])
