"""Admin commands the coach can run from their own Telegram chat with the bot.

The coach's chat_id is stored in coaches.telegram_chat_id. Commands start with '/'.

Supported:
  /help                              list commands
  /list                              list all customers
  /today <customer_id_short>         show customer's today macros
  /settarget <cid> <kcal> <p> <c> <f>   set macro targets
  /note <cid> <text>                 add note to customer profile
  /pause <cid>                       pause coaching
  /resume <cid>                      resume coaching
"""
from app import db
from app.agents import telegram_agent


async def handle_coach_command(chat_id: int, text: str) -> bool:
    """Returns True if this was a handled coach command."""
    coach = _get_coach_by_chat_id(chat_id)
    if coach is None:
        return False  # not a coach

    parts = text.strip().split(maxsplit=6)
    cmd = parts[0].lower()

    if cmd == "/help":
        await telegram_agent.send_message(chat_id, _HELP_TEXT)
        return True

    if cmd == "/list":
        await _cmd_list(chat_id, coach["id"])
        return True

    if cmd == "/today" and len(parts) >= 2:
        await _cmd_today(chat_id, parts[1])
        return True

    if cmd == "/settarget" and len(parts) >= 6:
        await _cmd_settarget(chat_id, parts[1], parts[2], parts[3], parts[4], parts[5])
        return True

    if cmd == "/pause" and len(parts) >= 2:
        try:
            db.update_customer_status(_resolve(parts[1]), "paused")
            await telegram_agent.send_message(chat_id, "✅ pausiert")
        except ValueError as e:
            await telegram_agent.send_message(chat_id, f"❌ {e}")
        return True

    if cmd == "/resume" and len(parts) >= 2:
        try:
            db.update_customer_status(_resolve(parts[1]), "active")
            await telegram_agent.send_message(chat_id, "✅ aktiv")
        except ValueError as e:
            await telegram_agent.send_message(chat_id, f"❌ {e}")
        return True

    if text.startswith("/"):
        await telegram_agent.send_message(chat_id, "Unbekannter Befehl. /help für Liste.")
        return True

    return False  # not a command — let normal flow continue


# ------------------------------------------------------------------
_HELP_TEXT = """<b>Coach-Commands</b>
/list — alle Kunden
/today &lt;cid&gt; — Tages-Makros eines Kunden
/settarget &lt;cid&gt; &lt;kcal&gt; &lt;p&gt; &lt;c&gt; &lt;f&gt; — Makro-Ziele setzen
/pause &lt;cid&gt; · /resume &lt;cid&gt;

&lt;cid&gt; = die ersten 8 Zeichen der Kunden-ID"""


def _get_coach_by_chat_id(chat_id: int) -> dict | None:
    resp = (
        db.db()
        .table("coaches")
        .select("*")
        .eq("telegram_chat_id", chat_id)
        .maybe_single()
        .execute()
    )
    return resp.data if resp else None


def _resolve(cid_short: str) -> str:
    """Resolve an 8-char prefix to full UUID.

    Postgres rejects ILIKE on UUID columns, so we fetch ids
    and match the prefix client-side.
    """
    resp = (
        db.db()
        .table("customers")
        .select("id")
        .execute()
    )
    if not resp.data:
        raise ValueError(f"Kein Kunde gefunden für '{cid_short}'")
    cid_short_lower = cid_short.lower()
    for row in resp.data:
        if row["id"].lower().startswith(cid_short_lower):
            return row["id"]
    raise ValueError(f"Kein Kunde gefunden für '{cid_short}'")


async def _cmd_list(chat_id: int, coach_id: str) -> None:
    resp = (
        db.db()
        .table("customers")
        .select("id, first_name, status")
        .eq("coach_id", coach_id)
        .order("created_at", desc=True)
        .execute()
    )
    if not resp.data:
        await telegram_agent.send_message(chat_id, "Noch keine Kunden.")
        return
    lines = [
        f"<code>{c['id'][:8]}</code> {c['first_name']} ({c['status']})"
        for c in resp.data
    ]
    await telegram_agent.send_message(chat_id, "\n".join(lines))


async def _cmd_today(chat_id: int, cid_short: str) -> None:
    from datetime import date, datetime
    from zoneinfo import ZoneInfo
    from app.config import settings

    try:
        cid = _resolve(cid_short)
    except ValueError as e:
        await telegram_agent.send_message(chat_id, f"❌ {e}")
        return

    today_start = datetime.combine(
        date.today(), datetime.min.time(), tzinfo=ZoneInfo(settings.TZ)
    )
    resp = (
        db.db()
        .table("food_logs")
        .select("total_kcal, protein_g, carbs_g, fat_g, meal_type, raw_description")
        .eq("customer_id", cid)
        .gte("logged_at", today_start.isoformat())
        .execute()
    )
    if not resp.data:
        await telegram_agent.send_message(chat_id, "Heute noch nichts geloggt.")
        return
    kcal = sum(r["total_kcal"] or 0 for r in resp.data)
    p = round(sum(float(r["protein_g"] or 0) for r in resp.data), 1)
    c = round(sum(float(r["carbs_g"] or 0) for r in resp.data), 1)
    f = round(sum(float(r["fat_g"] or 0) for r in resp.data), 1)
    lines = [f"<b>Heute:</b> {kcal} kcal · {p}g P · {c}g KH · {f}g F", ""]
    for r in resp.data:
        lines.append(f"• {r['meal_type']}: {r['raw_description']} ({r['total_kcal']} kcal)")
    await telegram_agent.send_message(chat_id, "\n".join(lines))


async def _cmd_settarget(
    chat_id: int, cid_short: str, kcal: str, p: str, c: str, f: str
) -> None:
    try:
        cid = _resolve(cid_short)
    except ValueError as e:
        await telegram_agent.send_message(chat_id, f"❌ {e}")
        return

    db.db().table("customer_profiles").update(
        {
            "daily_kcal_target": int(kcal),
            "protein_target_g": int(p),
            "carbs_target_g": int(c),
            "fat_target_g": int(f),
        }
    ).eq("customer_id", cid).execute()
    await telegram_agent.send_message(
        chat_id, f"✅ Ziele gesetzt: {kcal} kcal / {p}P / {c}KH / {f}F"
    )
